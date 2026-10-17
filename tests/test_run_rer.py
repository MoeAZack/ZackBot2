"""tools/research/run_rer.py - the C4 range_extreme_reversion.v0 dev-run CLI.

Synthetic only: a temp 4h candle cache (app._get_candles CSV format) with planted range / probe blocks on a doji
background + a temp git repo for the clean-tree check (the test_vwaprev_candidate_runner fixtures) + synthetic manifests
in temp dirs; no real cache, store or data folder is read. A port of tests/test_run_tpc.py (accepted C3 runner @ 676cfd1)
protection by protection, plus the C4 hand-off rules of Codex 6233040303 (fixture-only e50 / atr columns dropped before
the module; invalid prices refused by the runner, never by the module's own error).

Each planted block (computed EMA50 / ATR14, no injection; u = 1% of the level P): a 30-bar box b-30..b-1 of alternating
bodies P +- 0.5u (range +- 0.6u) with one wick to P + 4u at b-20 and one to P - 4u at b-9 (W = 8u ~ 6 ATR, ER ~ 0,
flat EMA50), the probe / reclaim trigger at b, then the trade path (dojis close mid-range: never a trigger). Shorts are
the price mirror around the level."""
import contextlib
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest
from fractions import Fraction

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_rer as R                                                                         # noqa: E402
import range_extreme_reversion as RX                                                        # noqa: E402
import run_vwaprev_candidate as VC                                                          # noqa: E402
import splits as SP                                                                         # noqa: E402
import test_vwaprev_candidate_runner as VT                                                  # noqa: E402
import test_range_extreme_reversion as CT                                                   # noqa: E402

H4 = R.TF_MS
WINDOW = VT.WINDOW                                       # 2025-03-03 / 2025-05-05, inside the development window
LO, HI = (SP.parse_utc(x) for x in WINDOW.split('/'))
WARM_LO = LO - R.WARMUP_BARS * H4
HEAD, TAIL = 12, 100
W = R.WARMUP_BARS                                        # slice index of the first in-window bar
NSLICE = W + (HI - LO) // H4
SYMS = ['ETHUSDT', 'SOLUSDT']
Q2 = W + (SP.parse_utc('2025-04-01T00:00:00Z') - LO) // H4
LEVEL = {'ETHUSDT': 100.0, 'SOLUSDT': 150.0}
SKIPPED = ('gap', 'cost', 'quality', 'room')             # trade-skip kinds: the path returns to the level after 4 bars


def doji(p, r):
    return (p, p + r, p - r, p)


def block(b, P, side='long', kind='time'):
    """A planted long range / probe / reclaim at b (u = 1% of the level P), then the trade path; short = price mirror
    around P."""
    u = P / 100.0
    rows = {}
    for k in range(b - 30, b):                             # the box: alternating bodies, flat closes on average
        rows[k] = (P - 0.5 * u, P + 0.6 * u, P - 0.6 * u, P + 0.5 * u) if k % 2 == 0 else             (P + 0.5 * u, P + 0.6 * u, P - 0.6 * u, P - 0.5 * u)
    o, h, lw, c = rows[b - 20]
    rows[b - 20] = (o, P + 4.0 * u, lw, c)                 # RH = P + 4u
    o, h, lw, c = rows[b - 9]
    rows[b - 9] = (o, h, P - 4.0 * u, c)                   # RL = P - 4u, M = P
    if kind == 'overlap':                                  # lift the last 6 box bars so EMA50 stays flat to b+6
        for k in range(b - 6, b):
            rows[k] = tuple(x + u for x in rows[k])
    trig = {'quality': (P - 4.0 * u, P - 0.4 * u, P - 5.5 * u, P - 1.5 * u),     # R > 2.5 ATR[b]
            'cost': (P - 4.0 * u, P - 3.7 * u, P - 4.3 * u, P - 3.8 * u),        # R < 1% of the entry
            'room': (P - 4.0 * u, P - 2.6 * u, P - 4.4 * u, P - 2.8 * u),        # entry gaps up to P - 2u: room < R
            'overlap': (P - 4.0 * u, P - 2.2 * u, P - 4.5 * u, P - 2.5 * u)}.get(
        kind, (P - 4.0 * u, P - 3.0 * u, P - 5.5 * u, P - 3.2 * u))
    rows[b] = trig
    E, lowb = trig[3], trig[2]
    n = 4 if kind in SKIPPED else 30
    path = {k: doji(E, 0.3 * u) for k in range(b + 1, b + n)}
    if kind == 'target':                                   # high reaches M = P
        path[b + 3] = (E, P + 0.2 * u, E - 0.2 * u, P - 0.1 * u)
        path.update({k: doji(P - 0.1 * u, 0.3 * u) for k in range(b + 4, b + n)})
    elif kind == 'stop':
        path[b + 3] = (E, E + 0.2 * u, lowb - 1.0 * u, lowb - 0.8 * u)
        path.update({k: doji(lowb - 0.8 * u, 0.3 * u) for k in range(b + 4, b + n)})
    elif kind == 'inval':                                  # a close below low[b], above the stop
        path[b + 5] = (E, E + 0.2 * u, lowb - 0.1 * u, lowb - 0.05 * u)
        path.update({k: doji(lowb - 0.05 * u, 0.02 * u) for k in range(b + 6, b + n)})
    elif kind == 'gap':                                    # the entry opens below the stop
        path = {k: doji(lowb - 1.5 * u, 0.3 * u) for k in range(b + 1, b + n)}
    elif kind == 'room':
        path = {k: doji(P - 2.0 * u, 0.3 * u) for k in range(b + 1, b + n)}
    elif kind in ('follow', 'overlap'):                    # a second probe of low[b] (above the stop) at b+3 / b+7
        path[b + (3 if kind == 'follow' else 7)] = (E, E + 0.1 * u, lowb - 0.2 * u, E + 0.05 * u)
    rows.update(path)
    if side == 'short':
        rows = {k: (2 * P - o, 2 * P - lw, 2 * P - h, 2 * P - c) for k, (o, h, lw, c) in rows.items()}
    return rows


PLAN = {'ETHUSDT': [(240, 'long', 'time'), (305, 'short', 'target'),
                    (370, 'long', 'time'),                        # entry in Q1, exit in Q2
                    (440, 'short', 'stop'), (510, 'long', 'inval'),
                    (NSLICE - 6, 'short', 'time')],               # last: open at END
        'SOLUSDT': [(240, 'long', 'overlap'), (320, 'short', 'follow'), (390, 'short', 'gap'), (440, 'long', 'cost'),
                    (490, 'short', 'quality'), (540, 'short', 'room')]}
BG = HEAD + 280                                                   # a csv row on the SOLUSDT background inside the window


def tape(sym, plan=None, start=None):
    """Slice-indexed rows: index 0 = warm-up start; HEAD bars before it, TAIL bars after END (the cache runs on)."""
    P = LEVEL.get(sym, 120.0)
    t = np.arange(WARM_LO - HEAD * H4, HI + TAIL * H4, H4, dtype=np.int64)
    rows = [doji(P, 0.6 * P / 100.0)] * len(t)
    plan = list(PLAN.get(sym, []) if plan is None else plan)
    if sym == 'ETHUSDT' and plan:
        plan.append((NSLICE + 60, 'long', 'target'))               # a setup after END: never read
    for b, side, kind in plan:
        for k, r in block(b, P, side, kind).items():
            if 0 <= k + HEAD < len(t):
                rows[k + HEAD] = r
    a = np.array(rows)
    d = pd.DataFrame(dict(t=t.astype(float), o=a[:, 0], h=a[:, 1], l=a[:, 2], c=a[:, 3], v=1000.0, qv=100000.0))
    if start is not None:                                           # late listing: first row at slice index `start`
        d = d.iloc[HEAD + start:].reset_index(drop=True)
    return d


def frames():
    return {s: tape(s) for s in SYMS}


@pytest.fixture
def env(tmp_path):
    cache = VT.write_cache(str(tmp_path / 'candles'), frames())
    repo = VT.git_repo(str(tmp_path / 'repo'))
    return dict(cache=cache, repo=repo, tmp=tmp_path)


def go(env, out='p.json', **kw):
    kw = dict(dict(cache=env['cache'], window=WINDOW, symbols=SYMS, repo=env['repo']), **kw)
    return R.run(out=str(env['tmp'] / out), **kw)


def sliced(sym):
    return VC.slice_frame(tape(sym), WARM_LO, HI)


def oracle(sym):
    """RX.run_symbol on the full warm-up+window slice (its own earliest b is WARM = 220 = W: no warm-up candidate)."""
    r = RX.run_symbol(sliced(sym))
    assert all(g['b'] >= W for g in r['signals'] + r['blocked'] + r['skips'])
    return [t for t in r['trades'] if t['b'] >= W], [s for s in r['skips'] if s['b'] >= W], r


def manifest(env, syms, name='manifest.json', edit=None, **extra):
    """A synthetic klines manifest (the 23cd693 schema subset) built from the temp cache files."""
    files = {}
    for s in syms:
        path = os.path.join(env['cache'], f'{s}_4h.csv')
        with open(path, 'rb') as f:
            raw = f.read()
        d = pd.read_csv(path)
        files[s] = dict(file=f'{s}_4h.csv', sha256=hashlib.sha256(raw).hexdigest(), rows=int(len(d)),
                        first=SP.utc(int(d.t.min())), last=SP.utc(int(d.t.max())), status='complete', **extra)
    m = {'format': 'zb-klines-research/1', 'interval': '4h', 'files': files}
    if edit:
        edit(m)
    p = env['tmp'] / name
    p.write_text(json.dumps(m, indent=1), encoding='utf-8')
    return str(p)


# ------------------------------------------------------------------ the planted tape itself
def test_planted_tape_fires_as_planned():
    for s in SYMS:
        r = RX.run_symbol(sliced(s))
        assert [(g['side'], g['b']) for g in r['signals']] == \
            sorted([(sd, b) for b, sd, _ in PLAN[s]] + ([('long', 247)] if s == 'SOLUSDT' else []),
                   key=lambda x: x[1])
    _, sk, r = oracle('SOLUSDT')
    assert [x['skip'] for x in sk] == ['overlap', 'gap_stop', 'cost_guard', 'quality', 'room']
    assert [(x['b'], x['consumed_by']) for x in r['blocked']] == [(323, 320)]
    tr, _, _ = oracle('ETHUSDT')
    assert [t['label'] for t in tr] == ['time', 'target', 'time', 'stop', 'invalidation', 'open_end']
    assert [t['label'] for t in oracle('SOLUSDT')[0]] == ['time', 'time']


# ------------------------------------------------------------------ guards
@pytest.mark.parametrize('spec', ['2026-06-01T00:00:00Z/2026-09-28T04:00:00Z', '2026-09-28T00:00:00Z/2026-10-12T00:00:00Z'])
def test_holdout_refused(env, spec):
    with pytest.raises(R.RunnerError, match='sealed|holdout'):
        go(env, window=spec)
    assert not os.path.exists(env['tmp'] / 'p.json')


def test_own_holdout_guard_independent_of_vc(env, monkeypatch):
    """Even if the imported window guard let a holdout END through, this runner refuses it."""
    past = SP.parse_utc('2026-09-28T04:00:00Z')
    monkeypatch.setattr(VC, 'check_window', lambda spec, plan=None: (LO, past, WARM_LO))
    with pytest.raises(R.RunnerError, match='holdout'):
        go(env)
    assert not os.path.exists(env['tmp'] / 'p.json')


def test_own_window_required_guard_independent_of_vc(env, monkeypatch):
    monkeypatch.setattr(VC, 'check_window', lambda spec, plan=None: (LO, HI, WARM_LO))
    for w in (None, ''):
        with pytest.raises(R.RunnerError, match='^rer: an explicit --window'):
            go(env, window=w)


def test_window_required_and_warmup_guard(env):
    with pytest.raises(R.RunnerError, match='explicit --window'):
        go(env, window=None)
    with pytest.raises(R.RunnerError, match='warm-up'):
        go(env, window='2021-01-11T00:00:00Z/2021-03-01T00:00:00Z')


def test_dirty_tree_refused(env, tmp_path):
    with pytest.raises(R.RunnerError, match='dirty git tree'):
        go(env, repo=VT.git_repo(str(tmp_path / 'dirty'), dirty=True))


def test_never_overwrites(env, monkeypatch):
    go(env)
    reads = []
    monkeypatch.setattr(R.B, 'read_cache', lambda *a, **k: reads.append(a))
    with pytest.raises(R.RunnerError, match='exists; a packet is never overwritten'):
        go(env)
    assert not reads                                                # refused before a single cache byte is read


@pytest.mark.parametrize('how', ['gap', 'missing_file', 'duplicate', 'off_grid'])
def test_incomplete_cache_refused(env, how):
    path = os.path.join(env['cache'], 'SOLUSDT_4h.csv')
    d = pd.read_csv(path, encoding='utf-8')
    if how == 'gap':
        d.drop(index=HEAD + W + 30).to_csv(path, index=False)
    elif how == 'duplicate':
        pd.concat([d, d.iloc[[HEAD + W + 30]]]).to_csv(path, index=False)
    elif how == 'off_grid':
        d.loc[HEAD + W + 30, 't'] += 60_000
        d.to_csv(path, index=False)
    else:
        os.remove(path)
    # an off-grid raw open time is refused before slicing (Codex 6230537709); the rest by completeness
    with pytest.raises(R.RunnerError, match='malformed raw open times' if how == 'off_grid' else 'INCOMPLETE'):
        go(env)
    assert not os.path.exists(env['tmp'] / 'p.json')


def test_out_of_order_rows_refused(env):
    path = os.path.join(env['cache'], 'SOLUSDT_4h.csv')
    d = pd.read_csv(path, encoding='utf-8')
    i = HEAD + W + 30
    d.iloc[[i, i + 1]] = d.iloc[[i + 1, i]].to_numpy()
    d.to_csv(path, index=False)
    with pytest.raises(R.RunnerError, match='out of time order'):
        go(env)


def _cli_repo(env, monkeypatch):
    real = VC.clean_commit
    monkeypatch.setattr(VC, 'clean_commit', lambda repo: real(env['repo']))     # the CLI default repo is this checkout


def test_cli(env, capsys, monkeypatch):
    _cli_repo(env, monkeypatch)
    out = str(env['tmp'] / 'cli.json')
    assert R.main(['--cache', env['cache'], '--window', '2026-09-01T00:00:00Z/2026-10-01T00:00:00Z',
                   '--symbols', 'ETHUSDT', '--out', out]) == 2
    assert 'REFUSED' in capsys.readouterr().err and not os.path.exists(out)
    assert R.main(['--cache', env['cache'], '--window', WINDOW, '--symbols', ','.join(SYMS), '--out', out]) == 0
    assert 'run 2 excluded 0' in capsys.readouterr().out and os.path.exists(out)


# ------------------------------------------------------------------ warm-up
def test_warmup_signals_excluded(env):
    full = RX.run_symbol(sliced('ETHUSDT'))
    assert RX.WARM == W and all(g['b'] >= W for g in full['signals'])  # the module's own earliest b is START
    p = go(env)
    assert p['warmup']['signals_excluded'] == {'long': 0, 'short': 0}
    assert p['warmup']['bars'] == R.WARMUP_BARS >= 220
    assert all(x['t_signal'] >= p['window']['start']
               for x in p['trades'] + p['skips'] + p['blocked'] + p['open_at_end']['trades'])
    assert all(x['t_in'] >= p['window']['start'] for x in p['trades'])
    assert p['stats']['combined']['n'] == sum(sum(t['R_net'] is not None for t in oracle(s)[0]) for s in SYMS)


def test_warmup_candidate_neither_consumes_nor_holds_slot():
    """A pre-window candidate is removed before cooldown consumption and the slot logic: a pre-window trade still open
    at START cannot turn an in-window signal into an overlap, and its cooldown cannot block an in-window candidate."""
    d = CT.follow(CT.frame({241: (1000, 1001, 999, 1000)}), 240)
    full = RX.run_symbol(d)
    assert [g['b'] for g in full['signals']] == [230, 240] and [t['b'] for t in full['trades']] == [230]
    assert [s['skip'] for s in full['skips']] == ['overlap']
    ex = []
    with R.window_signals(231, ex):
        r = RX.run_symbol(d)
    assert [t['b'] for t in r['trades']] == [240] and not r['skips'] and not r['blocked']
    assert [(g['side'], g['b']) for g in ex] == [('long', 230)]
    d2 = CT.follow(CT.frame(), 233)
    assert [g['b'] for g in RX.run_symbol(d2)['blocked']] == [233]          # inside the 230 + 7 cooldown: blocked
    with R.window_signals(231, []):
        r2 = RX.run_symbol(d2)
    assert [g['b'] for g in r2['signals']] == [233] and not r2['blocked']
    assert RX.detect.__name__ == 'detect' and RX.detect.__module__ == RX.__name__     # wrapper restored


# ------------------------------------------------------------------ packet consistency + cost parity
def test_packet_fields_and_summarize_consistency(env):
    p = go(env)
    for k in ('format', 'commit', 'runner_sha256', 'window', 'warmup', 'universe', 'cache', 'costs', 'stats', 'decision',
              'trades', 'skips', 'blocked', 'open_at_end', 'packet_sha256', 'prereg', 'skips_by_reason', 'notes',
              'manifest'):
        assert k in p
    assert set(p['runner_sha256']) == {'run_rer.py', 'range_extreme_reversion.py', 'run_breadth_candidate.py',
                                       'run_vwaprev_candidate.py', 'backtest.py', 'strategies.py', 'pit_run.py',
                                       'run_r4.py', 'splits.py'}
    assert all(len(v) == 64 for v in p['runner_sha256'].values())
    assert all(len(f['sha256']) == 64 for f in p['cache']['files'].values())
    closed = [t for s in SYMS for t in oracle(s)[0] if t['R_net'] is not None]
    ref = RX.summarize(closed)
    for g, k in (('long', 'long'), ('short', 'short'), ('combined', 'total')):
        st = p['stats'][g]
        for f in ('n', 'lt_minus1', 'lt_minus1_by_label'):
            assert st[f] == ref[k][f]
        for f in ('net_R', 'gross_R', 'fee_R', 'slip_R', 'fund_R', 'pf'):
            assert (st[f] is None) == (ref[k][f] is None)
            if st[f] is not None:
                assert st[f] == pytest.approx(ref[k][f], abs=1e-8)
        need = {'wins', 'avg_R', 'max_dd_R', 'quarters', 'positive_quarters', 'hold_bars_mean', 'hold_bars_median',
                'mfe_r_mean', 'mae_r_mean', 'giveback_gross_r_mean', 'giveback_net_r_mean', 'skips', 'blocked',
                'open_end', 'concentration', 'cost_R_per_trade', 'gross_R_per_trade', 'exits', 'gap_exit_by_label'}
        assert need <= set(st)
        assert set(st['skips']) == set(R.SKIPS) and set(st['exits']) == set(R.LABELS)
    c = p['stats']['combined']
    assert c['n'] == p['stats']['long']['n'] + p['stats']['short']['n'] == len(p['trades']) == 7
    assert c['exits'] == {'stop': 1, 'target': 1, 'invalidation': 1, 'time': 4}
    assert p['skips_by_reason'] == {'gap_stop': 1, 'quality': 1, 'room': 1, 'cost_guard': 1, 'no_next_bar': 0,
                                    'overlap': 1}
    assert sum(c['quarters'].values()) == pytest.approx(c['net_R'], abs=1e-6)
    conc = c['concentration']
    assert set(conc) == set(SYMS) and sum(v['n'] for v in conc.values()) == c['n']
    assert sum(v['trade_share'] for v in conc.values()) == pytest.approx(1.0)
    assert sum(v['net_R_share'] for v in conc.values()) == pytest.approx(1.0)
    assert p['open_at_end']['count'] == c['open_end'] == 1
    assert p['open_at_end']['trades'][0]['symbol'] == 'ETHUSDT' and p['open_at_end']['trades'][0]['side'] == 'short'
    for row in p['trades']:
        assert {'symbol', 'side', 't_in', 't_out', 'label', 'gap_exit', 'R_gross', 'R_net', 'mfe_r', 'mae_r',
                'giveback_gross_r', 'giveback_net_r'} <= set(row)
        assert 'giveback_r' not in row
        assert row['giveback_gross_r'] == pytest.approx(row['mfe_r'] - row['R_gross'], abs=1e-8)
        assert row['giveback_net_r'] == pytest.approx(row['mfe_r'] - row['R_net'], abs=1e-8)
        assert row['giveback_net_r'] > row['giveback_gross_r']                 # costs are never credited
        assert row['mfe_r'] >= 0 and row['mae_r'] >= 0
    assert p['decision']['tier_verdict'] == 'NON_DECISION' and 'verdict' not in p['decision']     # --symbols (Q2)
    assert p['decision']['diagnostic_verdict'] in ('CANDIDATE', 'PARK')
    assert p['decision']['final_requires'] == R.FINAL_REQUIRES
    assert p['candidate'] == RX.CANDIDATE == 'range_extreme_reversion.v0' and p['non_promotable'] is True
    assert p['family'] == RX.FAMILY and p['format'] == 'zb-rer-dev/1' and p['manifest'] is None
    ru = p['rules']
    assert (ru['cost_guard'], ru['room_r'], ru['rcap_atr'], ru['cap_bars'], ru['cooldown'], ru['box']) == \
        ('1/100', '1', '5/2', 24, 7, 30)
    assert ru['skip_order'] == ['gap_stop', 'quality', 'room', 'cost_guard']
    assert p['notes']['cost'] == R.COST_NOTE and '0.265R' in R.COST_NOTE and '25 funding closes' in R.COST_NOTE


def test_cost_note_number():
    """Worst case at the guard minimum, exactly from the decimal constants: fees 0.10R + slippage 0.04R + funding over
    25 closes 0.125R = 0.265R (the draft's 'about 0.265R' is exact)."""
    from decimal import Decimal as D
    fee, slip, fund = (D(str(x)) for x in (RX.FEE, RX.SLIP, RX.FUND_PER_BAR))
    guard = D(RX.COST_GUARD.numerator) / D(RX.COST_GUARD.denominator)
    assert RX.COST_GUARD == Fraction(1, 100) and RX.CAP_BARS + 1 == 25
    assert (2 * fee) / guard == D('0.1') and (2 * slip) / guard == D('0.04') and (25 * fund) / guard == D('0.125')
    assert (2 * fee + 2 * slip + 25 * fund) / guard == D('0.265')


def test_cost_note_number_matches_the_module():
    """The C4 module on a trade exactly at the guard minimum (R 21 / entry 2100 = 1.0%), held to the time exit with
    every close and the exit at the entry price: fee + slip + funding = 0.265R."""
    t = CT.one(CT.frame(shift=1094.0))
    assert (t['entry'], t['R_unit'], t['label'], t['exit'], t['exit_i'] - t['entry_i'] + 1) == \
        (2100.0, 21.0, 'time', 2100.0, 25)
    assert t['fee_R'] == pytest.approx(0.1, abs=1e-12) and t['slip_R'] == pytest.approx(0.04, abs=1e-12)
    assert t['fund_R'] == pytest.approx(0.125, abs=1e-12)
    assert t['fee_R'] + t['slip_R'] + t['fund_R'] == pytest.approx(0.265, abs=1e-12)
    assert t['R_gross'] - t['R_net'] == pytest.approx(0.265, abs=1e-12)


def test_cost_parity_with_module(env):
    """Every trade's costs and R are the module's own floats (rounded only for serialization)."""
    p = go(env)
    ref = sorted(((s, t) for s in SYMS for t in oracle(s)[0] if t['R_net'] is not None),
                 key=lambda x: (x[1]['entry_i'], x[0], x[1]['side']))
    assert len(ref) == len(p['trades'])
    for (s, t), row in zip(ref, p['trades']):
        assert (row['symbol'], row['side'], row['label']) == (s, t['side'], t['label'])
        for f in ('R_gross', 'R_net', 'fee_R', 'slip_R', 'fund_R', 'entry', 'exit', 'stop', 'target', 'R_unit'):
            assert row[f] == R._r(t[f])
        assert row['hold_bars'] == t['hold_bars'] and row['gap_exit'] == t['gap_exit']
    assert p['costs']['fee_taker'] == RX.FEE and p['costs']['slip'] == RX.SLIP
    assert p['costs']['fund_per_bar'] == RX.FUND_PER_BAR


def test_detector_runner_parity_on_planted_tapes(env):
    """Codex 6231454794 item 3: every signal, skip, blocked candidate and trade of the module's run_symbol on the
    sliced tape appears in the packet, with the same bar times."""
    p = go(env)
    t_of = lambda s, i: SP.utc(WARM_LO + i * H4)
    sig, skp, blk, opn = set(), set(), set(), set()
    for s in SYMS:
        r = RX.run_symbol(sliced(s))
        skp |= {(s, x['side'], x['skip'], t_of(s, x['b'])) for x in r['skips']}
        blk |= {(s, x['side'], t_of(s, x['b']), t_of(s, x['consumed_by'])) for x in r['blocked']}
        sig |= {(s, t['side'], t_of(s, t['b']), t_of(s, t['entry_i']), t['label']) for t in r['trades']
                if t['R_net'] is not None}
        opn |= {(s, t['side'], t_of(s, t['b'])) for t in r['trades'] if t['R_net'] is None}
    assert {(x['symbol'], x['side'], x['skip'], x['t_signal']) for x in p['skips']} == skp and len(skp) == 5
    assert {(x['symbol'], x['side'], x['t_signal'], x['consumed_by']) for x in p['blocked']} == blk and len(blk) == 1
    assert {(x['symbol'], x['side'], x['t_signal'], x['t_in'], x['label']) for x in p['trades']} == sig
    assert {(x['symbol'], x['side'], x['t_signal']) for x in p['open_at_end']['trades']} == opn and len(opn) == 1


def test_blocked_and_skips_reported(env):
    p = go(env)
    assert [(x['symbol'], x['skip']) for x in p['skips']] == [('SOLUSDT', 'overlap'), ('SOLUSDT', 'gap_stop'),
                                                               ('SOLUSDT', 'cost_guard'), ('SOLUSDT', 'quality'),
                                                               ('SOLUSDT', 'room')]
    st = p['stats']['combined']
    assert st['skips_by_symbol'] == {'SOLUSDT': {'gap_stop': 1, 'quality': 1, 'room': 1, 'cost_guard': 1,
                                                 'no_next_bar': 0, 'overlap': 1}}
    assert st['blocked'] == len(p['blocked']) == p['stats']['long']['blocked'] + p['stats']['short']['blocked'] == 1
    for x in p['blocked']:
        assert x['consumed_by'] < x['t_signal']


def test_skips_by_symbol_counts_each_symbol_separately():
    skips = [dict(symbol='ETHUSDT', side='long', skip='overlap'), dict(symbol='SOLUSDT', side='short', skip='room'),
             dict(symbol='SOLUSDT', side='long', skip='room'), dict(symbol='SOLUSDT', side='long', skip='no_next_bar')]
    st = R.group_stats([], skips, [], [], ['2025Q1'], RX.summarize([])['total'])
    assert st['skips_by_symbol'] == {'ETHUSDT': dict.fromkeys(R.SKIPS, 0) | {'overlap': 1},
                                     'SOLUSDT': dict.fromkeys(R.SKIPS, 0) | {'room': 2, 'no_next_bar': 1}}
    assert st['skips'] == dict.fromkeys(R.SKIPS, 0) | {'overlap': 1, 'room': 2, 'no_next_bar': 1}
    assert R.SKIPS == ('gap_stop', 'quality', 'room', 'cost_guard', 'no_next_bar', 'overlap')


def test_max_dd_uses_exit_order_not_entry_order():
    mk = lambda sym, t_in, t_out, r: dict(symbol=sym, side='long', quarter='2025Q1', t_in=t_in, t_out=t_out, R_net=r,
                                          R_gross=r, fee_R=0.0, slip_R=0.0, fund_R=0.0, hold_bars=1, mfe_r=0.0,
                                          mae_r=0.0, giveback_gross_r=0.0, giveback_net_r=0.0, label='time',
                                          gap_exit=False, lt_minus1=False)
    rows = [mk('ETHUSDT', 'a1', 'b3', -1.0), mk('SOLUSDT', 'a2', 'b1', 1.0), mk('SOLUSDT', 'a3', 'b2', -1.0)]
    st = R.group_stats(rows, [], [], [], ['2025Q1'], RX.summarize(rows)['total'])
    assert st['max_dd_R'] == 2.0                                    # exit order: +1, -1, -1 (entry order would give 1.0)


def test_max_dd_in_exit_order(env):
    p = go(env)
    rows = sorted(p['trades'], key=lambda x: (x['t_out'], x['t_in'], x['symbol'], x['side']))
    cum = np.cumsum([0.0] + [x['R_net'] for x in rows])
    assert p['stats']['combined']['max_dd_R'] == pytest.approx(float((np.maximum.accumulate(cum) - cum).max()), abs=1e-6)


def test_decision_statistics_exact_recompute(env):
    """The decision fields recomputed independently from the module's own unrounded trades."""
    p = go(env)
    closed = [(s, t) for s in SYMS for t in oracle(s)[0] if t['R_net'] is not None]
    net = [t['R_net'] for _, t in closed]
    w, lo = sum(v for v in net if v > 0), -sum(v for v in net if v < 0)
    pf = w / lo
    gross = sum(t['R_gross'] for _, t in closed)
    cost = sum(t['fee_R'] + t['slip_R'] + t['fund_R'] for _, t in closed)
    q = {'2025Q1': 0.0, '2025Q2': 0.0}
    for s, t in closed:
        q[R.quarter(WARM_LO + t['entry_i'] * H4)] += t['R_net']
    pos = sum(1 for v in q.values() if v > 0)
    d = p['decision']
    assert d['pf'] == R._r(pf) and d['pf_ok'] is (pf > 1.0)
    assert d['positive_quarters'] == pos and d['quarters'] == 2 and d['quarters_ok'] is (3 * pos >= 4)
    assert d['gross_R_per_trade'] == R._r(gross / len(closed)) and d['cost_R_per_trade'] == R._r(cost / len(closed))
    assert d['cost_ok'] is (gross >= 3 * cost)
    verdict = 'CANDIDATE' if (pf > 1.0 and 3 * pos >= 4 and gross >= 3 * cost) else 'PARK'
    assert d['diagnostic_verdict'] == verdict and d['tier_verdict'] == 'NON_DECISION'
    assert p['stats']['combined']['quarters'] == {k: R._r(v) for k, v in q.items()}


# ------------------------------------------------------------------ MFE / MAE / giveback on hand-built C4 trades
# (the C4 module fixture: entry 1006 at 231, stop 985, R 21, target M 1030, after-bars (1006, 1010, 1002, 1006); the
# short is its K - p reflection with K = 2012, so the same numbers hold on both sides)
def _both(ov, **kw):
    d = CT.frame(ov, **kw)
    return [(d, CT.one(d)), (CT.flip(d), CT.one(CT.flip(d)))]


def test_excursions_time_exit_ignores_exit_bar_range():
    for d, t in _both({255: (1006.0, 1029.0, 986.0, 1006.0)}):
        assert (t['label'], t['exit_i'], t['R_unit']) == ('time', 255, 21.0)
        x = R.excursions(d, t)
        assert x['mfe_r'] == pytest.approx(4 / 21) and x['mae_r'] == pytest.approx(4 / 21)   # bar 255 range not counted
        assert x['giveback_gross_r'] == pytest.approx(4 / 21 - t['R_gross'])
        assert x['giveback_net_r'] == pytest.approx(4 / 21 - t['R_net']) and 'giveback_r' not in x


def test_excursions_intrabar_stop_caps_at_stop():
    for d, t in _both({235: (1006.0, 1031.0, 980.0, 1006.0)}):
        assert (t['label'], t['exit_i'], t['R_gross']) == ('stop', 235, -1.0)          # stop beats target same bar
        x = R.excursions(d, t)
        assert x['mae_r'] == pytest.approx(1.0) and x['mfe_r'] == pytest.approx(4 / 21)
        assert x['giveback_gross_r'] == pytest.approx(4 / 21 + 1.0)
        assert x['giveback_net_r'] == pytest.approx(4 / 21 - t['R_net']) and x['giveback_net_r'] > 4 / 21 + 1.0


def test_excursions_gap_target_counts_exit_open():
    for d, t in _both({235: (1040.0, 1041.0, 1039.0, 1040.0)}):
        assert (t['label'], t['gap_exit'], t['R_gross']) == ('target', True, 34 / 21)
        x = R.excursions(d, t)
        assert x['mfe_r'] == pytest.approx(34 / 21) and x['mae_r'] == pytest.approx(4 / 21)
        assert x['giveback_gross_r'] == pytest.approx(0.0, abs=1e-12)
        assert x['giveback_net_r'] == pytest.approx(34 / 21 - t['R_net'])


def test_excursions_invalidation_exit():
    for d, t in _both({233: (1006.0, 1008.0, 988.0, 989.0)}):              # close 989 < low[b] 990, above the stop
        assert (t['label'], t['exit_i'], t['exit']) == ('invalidation', 234, 1006.0)
        x = R.excursions(d, t)
        assert x['mfe_r'] == pytest.approx(4 / 21) and x['mae_r'] == pytest.approx(18 / 21)


def test_excursions_intrabar_stop_counts_exit_bar_open():
    """Intrabar stop whose exit bar opens beyond every earlier extreme: that open is on the path and sets the MFE."""
    for d, t in _both({235: (1020.0, 1021.0, 980.0, 1000.0)}):
        assert (t['label'], t['exit_i'], t['R_gross'], t['gap_exit']) == ('stop', 235, -1.0, False)
        x = R.excursions(d, t)
        assert x['mfe_r'] == pytest.approx(14 / 21) and x['mae_r'] == pytest.approx(1.0)
        assert x['giveback_gross_r'] == pytest.approx(14 / 21 + 1.0)


# ------------------------------------------------------------------ quarters
def test_quarter_helpers():
    assert R.quarter(SP.parse_utc('2025-03-31T20:00:00Z')) == '2025Q1'
    assert R.quarter(SP.parse_utc('2025-04-01T00:00:00Z')) == '2025Q2'
    assert R.window_quarters(LO, HI) == ['2025Q1', '2025Q2']
    assert R.window_quarters(SP.parse_utc('2024-12-30T00:00:00Z'), SP.parse_utc('2025-04-01T00:00:00Z')) == \
        ['2024Q4', '2025Q1']


def test_quarter_bucketing_by_entry_time(env):
    p = go(env)
    strad = [x for x in p['trades'] if x['t_in'] < '2025-04-01' <= x['t_out']]
    assert strad and all(x['quarter'] == '2025Q1' for x in strad)
    for x in p['trades']:
        assert x['quarter'] == R.quarter(SP.parse_utc(x['t_in']))
    c = p['stats']['combined']
    for q in ('2025Q1', '2025Q2'):
        assert c['quarters'][q] == pytest.approx(sum(x['R_net'] for x in p['trades'] if x['quarter'] == q), abs=1e-6)


def test_empty_quarters_are_zero_and_non_positive():
    st = R.group_stats([], [], [], [], ['2025Q1', '2025Q2'], RX.summarize([])['total'])
    assert st['quarters'] == {'2025Q1': 0.0, '2025Q2': 0.0} and st['positive_quarters'] == 0
    assert st['n'] == 0 and st['avg_R'] is None and st['pf'] is None
    assert R.decision(st, ['2025Q1', '2025Q2'])['tier_verdict'] == 'PARK'


# ------------------------------------------------------------------ decision rule
def _c(**kw):
    c = dict(n=10, pf=1.2, wins=5, losses=5, positive_quarters=2, gross_R=3.0, cost_R=1.0,
             gross_R_per_trade=0.3, cost_R_per_trade=0.1)
    c.update(kw)
    return c


@pytest.mark.parametrize('kw, verdict', [({}, 'CANDIDATE'), (dict(pf=1.0), 'PARK'), (dict(positive_quarters=1), 'PARK'),
                                         (dict(gross_R=2.99), 'PARK'), (dict(n=0, wins=0, losses=0), 'PARK'),
                                         (dict(pf=None, losses=0), 'CANDIDATE')])
def test_decision_rule(kw, verdict):
    d = R.decision(_c(**kw), ['Q1', 'Q2', 'Q3'])
    assert d['tier_verdict'] == verdict and d['final_requires'] == R.FINAL_REQUIRES


EPS = 1e-12


def _raw(**kw):
    c = dict(n=10, wins=5, losses=5, positive_quarters=2, pf=1.5, gross_R=3.0, cost_R=1.0)
    c.update(kw)
    return c


@pytest.mark.parametrize('pf, ok', [(1.0 - EPS, False), (1.0, False), (1.0 + EPS, True)])
def test_decision_pf_boundary_unrounded(pf, ok):
    d = R.decision(_raw(pf=pf), ['Q1', 'Q2', 'Q3'])
    assert d['pf_ok'] is ok and d['tier_verdict'] == ('CANDIDATE' if ok else 'PARK')


@pytest.mark.parametrize('gross, ok', [(3.0 - 1e-13, False), (3.0, True), (3.0 + 1e-13, True)])
def test_decision_cost_boundary_unrounded(gross, ok):
    d = R.decision(_raw(gross_R=gross, cost_R=1.0), ['Q1', 'Q2', 'Q3'])
    assert d['cost_ok'] is ok


@pytest.mark.parametrize('pos, nq, ok', [(1, 2, False), (2, 3, True), (3, 4, True), (1, 3, False)])
def test_decision_quarter_fraction_boundary(pos, nq, ok):
    d = R.decision(_raw(positive_quarters=pos), ['Q%d' % i for i in range(nq)])
    assert d['quarters_ok'] is ok


@pytest.mark.parametrize('qnet, positive', [(-EPS, False), (0.0, False), (EPS, True)])
def test_quarter_sign_on_unrounded_sum(qnet, positive):
    rows = [dict(symbol='ETHUSDT', side='short', quarter='2025Q1', t_in='a', t_out='b', R_net=qnet, R_gross=qnet,
                 fee_R=0.0, slip_R=0.0, fund_R=0.0, hold_bars=1, mfe_r=0.0, mae_r=0.0, giveback_gross_r=0.0,
                 giveback_net_r=0.0, label='time', gap_exit=False, lt_minus1=False)]
    st = R.group_stats(rows, [], [], [], ['2025Q1'], RX.summarize(rows)['total'])
    assert st['positive_quarters'] == (1 if positive else 0)
    assert st['quarters']['2025Q1'] == 0.0                          # serialized value is rounded


def test_group_stats_feeds_raw_values_to_decision():
    """group_stats -> decision end to end: PF 1 + 1e-12 rounds to 1.0 in the packet but must still pass PF > 1."""
    mk = lambda q, r: dict(symbol='ETHUSDT', side='long', quarter=q, t_in=q, t_out=q, R_net=r, R_gross=r + 0.1,
                           fee_R=0.05, slip_R=0.05, fund_R=0.0, hold_bars=1, mfe_r=0.0, mae_r=0.0,
                           giveback_gross_r=0.0, giveback_net_r=0.0, label='time', gap_exit=False, lt_minus1=False)
    rows = [mk('2025Q1', 1.0 + 1e-12), mk('2025Q2', -1.0)]
    st = R.group_stats(rows, [], [], [], ['2025Q1', '2025Q2'], RX.summarize(rows)['total'])
    d = R.decision(st, ['2025Q1', '2025Q2'])
    assert st['pf'] == 1.0 and d['pf_ok'] is True


def _xrow(q, net, gross=None, fee=0.0):
    return dict(symbol='ETHUSDT', side='long', quarter=q, t_in=q, t_out=q, R_net=net,
                R_gross=net + fee if gross is None else gross, fee_R=fee, slip_R=0.0, fund_R=0.0, hold_bars=1,
                mfe_r=0.0, mae_r=0.0, giveback_gross_r=0.0, giveback_net_r=0.0, label='time', gap_exit=False,
                lt_minus1=False)


def _xdec(rows, quarters=('2025Q1',)):
    st = R.group_stats(rows, [], [], [], list(quarters), RX.summarize(rows)['total'])
    return st, R.decision(st, list(quarters))


def test_exact_cost_boundary_gross_03_vs_cost_01():
    """Codex repro: gross 0.05 + 0.25 vs cost 0.04 + 0.06 passes exactly (Fraction sums) but fails in float sums."""
    rows = [_xrow('2025Q1', 0.05 - 0.04, gross=0.05, fee=0.04), _xrow('2025Q1', 0.25 - 0.06, gross=0.25, fee=0.06)]
    assert not (0.05 + 0.25 >= 3 * (0.04 + 0.06))                              # the float comparison fails
    st, d = _xdec(rows)
    assert d['cost_ok'] is True and d['evaluated_on'] == 'exact Fraction sums of per-trade float values'
    assert st['gross_R'] == R._r(0.05 + 0.25) and st['cost_R'] == R._r(0.04 + 0.06)   # serialized fields unchanged


def test_exact_pf_boundary():
    """Wins 0.01 + 0.02 vs loss 0.03: float PF is exactly 1.0 (fails PF > 1), the exact sums give PF > 1."""
    rows = [_xrow('2025Q1', 0.01), _xrow('2025Q2', 0.02), _xrow('2025Q2', -0.03)]
    assert (0.01 + 0.02) / 0.03 == 1.0
    st, d = _xdec(rows, ('2025Q1', '2025Q2'))
    assert d['pf_ok'] is True and d['pf'] == 1.0 and st['pf'] == 1.0


def test_exact_quarter_sign():
    """0.01 + 0.02 - 0.03 is 0.0 in floats but > 0 exactly: the quarter counts as positive."""
    rows = [_xrow('2025Q1', 0.01), _xrow('2025Q1', 0.02), _xrow('2025Q1', -0.03)]
    assert 0.01 + 0.02 - 0.03 == 0.0
    st, d = _xdec(rows)
    assert st['positive_quarters'] == 1 and st['quarters']['2025Q1'] == 0.0 and d['quarters_ok'] is True


def test_decision_uses_raw_not_rounded_group_fields():
    c = dict(n=10, wins=5, losses=5, positive_quarters=3, pf=1.0, gross_R=3.0, cost_R=1.0,
             _raw=dict(pf=1.0 + EPS, gross_R=3.0 - 1e-13, cost_R=1.0))
    d = R.decision(c, ['Q1', 'Q2', 'Q3'])
    assert d['pf_ok'] is True and d['cost_ok'] is False


# ------------------------------------------------------------------ determinism + packet self-hash
def test_determinism(env):
    a, b = go(env, out='a.json'), go(env, out='b.json')
    with open(env['tmp'] / 'a.json', 'rb') as f, open(env['tmp'] / 'b.json', 'rb') as g:
        assert f.read() == g.read()
    assert a['packet_sha256'] == b['packet_sha256']
    assert RX.detect.__name__ == 'detect' and RX.detect.__module__ == RX.__name__     # wrapper restored


def test_packet_json_roundtrip(env):
    go(env)
    with open(env['tmp'] / 'p.json', encoding='utf-8') as f:
        p = json.load(f)
    assert p['candidate'] == RX.CANDIDATE and p['non_promotable'] is True


def _self_hash(p):
    q = {k: v for k, v in p.items() if k != 'packet_sha256'}
    return hashlib.sha256(json.dumps(q, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                                     allow_nan=False).encode()).hexdigest()


def test_packet_self_hash(env):
    """Codex 6231454794 item 2: packet_sha256 recomputes from the WRITTEN packet; one changed field changes it."""
    go(env, manifest=manifest(env, SYMS))
    with open(env['tmp'] / 'p.json', encoding='utf-8') as f:
        p = json.load(f)
    assert _self_hash(p) == p['packet_sha256']
    m = json.loads(json.dumps(p))
    m['stats']['combined']['n'] += 1
    assert _self_hash(m) != p['packet_sha256']
    m = json.loads(json.dumps(p))
    m['manifest']['sha256'] = '0' * 64
    assert _self_hash(m) != p['packet_sha256']


# ------------------------------------------------------------------ RX.detect restoration
def test_detect_restored_after_success():
    orig = RX.detect
    ex = []
    with R.window_signals(231, ex):
        assert RX.detect is not orig
        RX.run_symbol(CT.frame())
    assert RX.detect is orig and [g['b'] for g in ex] == [230]


def test_detect_restored_after_injected_failure(monkeypatch):
    def boom(df, side, ind=None):
        raise ZeroDivisionError('injected detector failure')
    monkeypatch.setattr(RX, 'detect', boom)
    with pytest.raises(ZeroDivisionError, match='injected'):
        with R.window_signals(5, []):
            RX.run_symbol(CT.frame())
    assert RX.detect is boom


def test_detect_restored_after_run_failure(env, monkeypatch):
    orig = RX.detect
    monkeypatch.setattr(RX, 'summarize', lambda *a: (_ for _ in ()).throw(RuntimeError('late failure')))
    with pytest.raises(RuntimeError, match='late failure'):
        go(env)
    assert RX.detect is orig and not os.path.exists(env['tmp'] / 'p.json')


# ------------------------------------------------------------------ internal aborts
@pytest.mark.parametrize('kind', ['target', 'gap'])
def test_pre_window_trade_raises_runner_error(env, monkeypatch, kind):
    """With the wrapper disabled and the module's earliest b moved before START, a pre-window signal aborts, whether it
    becomes a trade or only a skip."""
    monkeypatch.setattr(R, 'window_signals', lambda lo_i, ex: contextlib.nullcontext())
    monkeypatch.setattr(RX, 'WARM', 150)
    plan = PLAN['ETHUSDT'] + [(160, 'long', kind)]
    tape('ETHUSDT', plan=plan).to_csv(os.path.join(env['cache'], 'ETHUSDT_4h.csv'), index=False)
    with pytest.raises(R.RunnerError, match='used a pre-window candidate' if kind == 'gap' else 'pre-window'):
        go(env)
    assert not os.path.exists(env['tmp'] / 'p.json')


def test_warmup_wrapper_drops_pre_window_signal(env, monkeypatch):
    """Same tape with the wrapper active: the pre-window signal is excluded and counted, nothing else changes."""
    monkeypatch.setattr(RX, 'WARM', 150)
    plan = PLAN['ETHUSDT'] + [(160, 'long', 'target')]
    tape('ETHUSDT', plan=plan).to_csv(os.path.join(env['cache'], 'ETHUSDT_4h.csv'), index=False)
    p = go(env)
    assert p['warmup']['signals_excluded'] == {'long': 1, 'short': 0} and p['stats']['combined']['n'] == 7


def test_warmup_bar_count_mismatch_raises_runner_error(env, monkeypatch):
    monkeypatch.setattr(R, 'WARMUP_BARS', R.WARMUP_BARS - 1)
    with pytest.raises(R.RunnerError, match='warm-up bars'):
        go(env)


def test_net_share_null_when_group_net_near_zero():
    rows = [dict(symbol='ETHUSDT', R_net=1.0), dict(symbol='SOLUSDT', R_net=-1.0 + 1e-12)]
    c = R.concentration(rows, ['ETHUSDT', 'SOLUSDT'])
    assert c['ETHUSDT']['net_R_share'] is None and c['SOLUSDT']['net_R_share'] is None
    c = R.concentration([dict(symbol='ETHUSDT', R_net=2.0), dict(symbol='SOLUSDT', R_net=-1.0)], ['ETHUSDT', 'SOLUSDT'])
    assert c['ETHUSDT']['net_R_share'] == pytest.approx(2.0)


# ------------------------------------------------------------------ prereg
def test_prereg_block_written_before_data(env, monkeypatch):
    order = []
    real_pre, real_read = R.prereg, R.B.read_cache
    monkeypatch.setattr(R, 'prereg', lambda: (order.append('prereg'), real_pre())[1])
    monkeypatch.setattr(R.B, 'read_cache', lambda *a, **k: (order.append('read'), real_read(*a, **k))[1])
    p = go(env)
    assert order[0] == 'prereg' and 'read' in order
    pr = p['prereg']
    assert pr['written_before_data'] is True and pr['rule_text'] == RX.__doc__
    assert 'entry..exit inclusive' in pr['funding'] and 'both sides' in pr['funding'] and 'never credited' in pr['funding']
    assert 'R_net' in pr['pf_and_quarters_use'] and pr['final_requires'] == R.FINAL_REQUIRES
    assert pr['cost_note'] == R.COST_NOTE
    assert len(pr['frozen_c4_parameters']) == 5 and len(pr['frozen_c4_short_mirror']) == 2
    assert len(pr['frozen_c4_lifecycle']) == 5 and len(pr['c4_draft_rules']) == 27
    assert pr['frozen_c4_lifecycle'][0] == (
        'A signal is a candidate passing every regime and trigger rule. It consumes the cross-side cooldown whether '
        'taken, no-next-bar, entry/stop gap-skipped, quality/room/cost-skipped or overlap-ignored. A candidate failing '
        'regime/trigger does not consume; a cooldown-blocked candidate does not extend the cooldown.')
    assert pr['frozen_decision'] == ('The frozen C1 rule, whole family, combined sides, with CORE-10 and CORE-20 both '
                                     'needed for a CANDIDATE; otherwise PARK.')
    assert pr['frozen_c4_decision'] == ('Later whole-family decision remains combined PF>1, at least 2/3 positive UTC '
                                        'quarters and gross R/trade >=3x cost on both authorized tiers; otherwise '
                                        'PARK. No tuning or side split.')
    assert pr['exclusion_rule'] == R.EXCLUSION_RULE and '6229469658' in pr['exclusion_rule']
    assert all(k in pr['source'] for k in ('6232261614', '6232236186', '6233040303', '676cfd1'))
    assert 'e50 / atr' in pr['module_input'] and 'OHLC-sane' in pr['module_input']
    assert set(p['skips_by_reason']) == set(R.SKIPS)
    assert p['skips_by_reason'] == {k: sum(1 for x in p['skips'] if x['skip'] == k) for k in R.SKIPS}
    assert p['skips_by_reason'] == p['stats']['combined']['skips']
    for g in ('long', 'short', 'combined'):
        st = p['stats'][g]
        assert st['gap_exit_by_label'] == {k: sum(1 for x in p['trades'] if x['label'] == k and x['gap_exit']
                                                  and (g == 'combined' or x['side'] == g)) for k in R.LABELS}


def test_prereg_frozen_strings_match_codex_source():
    """Codex 6232261614 / Build 6232236186 verbatim (list markers and ** removed): spot-check the load-bearing ones."""
    s, sm, lc, dr = R.FROZEN_C4_PARAMETERS, R.FROZEN_C4_SHORT_MIRROR, R.FROZEN_C4_LIFECYCLE, R.C4_DRAFT_RULES
    assert s[2] == ('Regime: 4.0*ATR[b-1] <= W <= 12.0*ATR[b-1]; abs(EMA50[b-1]-EMA50[b-13]) <= 0.5*ATR[b-1]; ER '
                    '<=0.30, where ER numerator is abs(C[b-1]-C[b-31]) and denominator is the sum of the 30 absolute '
                    'close changes i=b-30..b-1. Zero denominator fails.')
    assert sm[0] == 'high[b] >= RH+0.10*ATR[b-1] and <= RH+1.50*ATR[b-1]; C[b] < RH; close location <=0.40; C[b] > M.'
    assert lc[1] == ('Trade-skip order is entry/stop gap -> R cap quality -> target room -> cost. Target and stop are '
                     'frozen at signal time. A gap entry beyond the mean therefore fails room.')
    assert dr[0] == '4h closed bars; signal bar `b`.'
    assert 'Skip order: entry ≤ stop → R cap (`quality`) → target room (`room`) → cost guard.' in dr
    assert 'Stop = low[b] − 0.25·ATR[b]. Skip if entry ≤ stop.' in dr
    assert dr[-1] == ('Worst-case cost at the guard minimum (R/E = 1.0%): fees and slippage 0.14R plus funding over 25 '
                      'closes × 5e-5 / 1% = 0.125R, about 0.265R.')
    every = s + sm + lc + dr + (R.FROZEN_DECISION, R.FROZEN_C4_DECISION)
    assert all('**' not in x and not x.startswith(('- ', '1. ')) for x in every)
    dropped = ('Mirror tests use', 'Synthetic oracle first', 'Scope:', 'How it differs', 'Before any historical run',
               'Choices for Codex', 'PATH:', 'LANES:', 'ROADMAP FIT', 'DEFERRED', 'A planted range plus probe')
    assert not any(x.startswith(dropped) for x in every)            # test-method / scope / non-rule text left out


# ------------------------------------------------------------------ --check-coverage
def test_check_coverage_reports_late_listing_and_nothing_past_end(env, monkeypatch):
    path = os.path.join(env['cache'], 'SOLUSDT_4h.csv')
    d = pd.read_csv(path, encoding='utf-8')
    d.iloc[HEAD + 40:].to_csv(path, index=False)                   # listed 40 bars after the warm-up start
    calls = []
    monkeypatch.setattr(RX, 'run_symbol', lambda *a, **k: calls.append('run'))
    monkeypatch.setattr(RX, 'detect', lambda *a, **k: calls.append('detect'))
    monkeypatch.setattr(R.VC, 'clean_commit', lambda *a, **k: calls.append('commit'))
    monkeypatch.setattr(RX.ST, 'indicators', lambda *a, **k: calls.append('ind'))
    monkeypatch.setattr(RX.ST, 'ema', lambda *a, **k: calls.append('ema'))
    cov = R.check_coverage(cache=env['cache'], window=WINDOW, symbols=SYMS)
    assert not calls                                                # nothing computed
    assert cov['required_warmup_start'] == SP.utc(WARM_LO)
    eth, sol = cov['symbols']['ETHUSDT'], cov['symbols']['SOLUSDT']
    assert eth['ok'] and eth['first'] == SP.utc(WARM_LO - HEAD * H4) and eth['last'] == SP.utc(HI - H4)
    assert not eth['late_listed']
    assert not sol['covers_warmup'] and sol['first'] == SP.utc(WARM_LO + 40 * H4) and not sol['ok']
    assert sol['late_listed']
    assert cov['late_or_short'] == ['SOLUSDT'] and cov['late_listed'] == ['SOLUSDT']
    assert all(v['last'] is None or v['last'] < cov['window']['end'] for v in cov['symbols'].values())
    assert SP.utc(HI) not in json.dumps(cov['symbols'])            # the cache runs TAIL bars past END: none shown


def test_check_coverage_missing_file_and_holdout(env):
    os.remove(os.path.join(env['cache'], 'SOLUSDT_4h.csv'))
    cov = R.check_coverage(cache=env['cache'], window=WINDOW, symbols=SYMS)
    assert cov['symbols']['SOLUSDT']['missing_file'] and cov['late_or_short'] == ['SOLUSDT']
    assert cov['late_listed'] == []
    with pytest.raises(R.RunnerError, match='sealed|holdout'):
        R.check_coverage(cache=env['cache'], window='2026-06-01T00:00:00Z/2026-09-28T04:00:00Z', symbols=SYMS)


def test_cli_check_coverage(env, capsys):
    out = str(env['tmp'] / 'cov.json')
    assert R.main(['--cache', env['cache'], '--window', WINDOW, '--symbols', ','.join(SYMS), '--check-coverage']) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep['late_or_short'] == [] and not os.path.exists(out)
    assert R.main(['--cache', env['cache'], '--window', '2026-09-01T00:00:00Z/2026-10-01T00:00:00Z',
                   '--symbols', 'ETHUSDT', '--check-coverage']) == 2
    with pytest.raises(SystemExit):
        R.main(['--cache', env['cache'], '--window', WINDOW, '--symbols', 'ETHUSDT'])   # --out required for a run


# ------------------------------------------------------------------ eligibility-by-history ledger
def _add(env, sym, **kw):
    tape(sym, plan=[], **kw).to_csv(os.path.join(env['cache'], f'{sym}_4h.csv'), index=False)


def test_late_symbol_without_flag_refuses(env):
    _add(env, 'HYPEUSDT', start=40)
    with pytest.raises(R.RunnerError, match='INCOMPLETE.*HYPEUSDT'):
        go(env, symbols=SYMS + ['HYPEUSDT'])
    assert not os.path.exists(env['tmp'] / 'p.json')


def test_late_symbol_excluded_when_flagged_and_verified(env):
    _add(env, 'HYPEUSDT', start=40)
    p = go(env, symbols=SYMS + ['HYPEUSDT'], exclude_late=['hypeusdt'])
    u = p['universe']
    assert u['requested'] == SYMS + ['HYPEUSDT'] and u['run'] == SYMS
    assert u['excluded'] == [dict(symbol='HYPEUSDT', reason='late_listed', first_bar=SP.utc(WARM_LO + 40 * H4),
                                  required_warmup_start=SP.utc(WARM_LO))]
    assert u['tier'] is None and u['tier_label'] == 'SYMBOLS-2' and u['non_decision'] is True and u['exclusion_rule'] == R.EXCLUSION_RULE
    assert 'HYPEUSDT' not in p['cache']['completeness'] and 'HYPEUSDT' in p['cache']['files']
    assert set(p['stats']['combined']['concentration']) == set(SYMS)
    assert all(x['symbol'] != 'HYPEUSDT' for x in p['trades'] + p['skips'] + p['blocked'])
    base = go(env, out='base.json')                                 # same numbers as the run without HYPE
    assert p['stats'] == base['stats'] and p['trades'] == base['trades']


@pytest.mark.parametrize('how', ['gap', 'invalid_ohlc'])
def test_gapped_or_invalid_non_late_symbol_still_refuses(env, how):
    path = os.path.join(env['cache'], 'SOLUSDT_4h.csv')
    d = pd.read_csv(path, encoding='utf-8')
    if how == 'gap':
        d = d.drop(index=HEAD + W + 30)
    else:
        d.loc[HEAD + W + 30, 'c'] = float('nan')
    d.to_csv(path, index=False)
    with pytest.raises(R.RunnerError, match='not provably late-listed'):
        go(env, exclude_late=['SOLUSDT'])                          # the flag cannot drop a non-late symbol
    with pytest.raises(R.RunnerError, match='INCOMPLETE|OHLC sanity'):
        go(env)
    assert not os.path.exists(env['tmp'] / 'p.json')


@pytest.mark.parametrize('start, ok', [(0, False), (1, True)])
def test_late_boundary_first_bar_at_warmup_start_is_not_late(env, start, ok):
    _add(env, 'HYPEUSDT', start=start)
    if ok:
        assert go(env, symbols=SYMS + ['HYPEUSDT'], exclude_late=['HYPEUSDT'])['universe']['run'] == SYMS
    else:
        with pytest.raises(R.RunnerError, match='not provably late-listed'):
            go(env, symbols=SYMS + ['HYPEUSDT'], exclude_late=['HYPEUSDT'])


def test_exclude_late_edge_refusals(env):
    with pytest.raises(R.RunnerError, match='not in the requested symbols'):
        go(env, exclude_late=['HYPEUSDT'])
    with pytest.raises(R.RunnerError, match='not provably late-listed'):
        go(env, symbols=SYMS + ['HYPEUSDT'], exclude_late=['HYPEUSDT'])            # missing file is not late
    _add(env, 'HYPEUSDT', start=NSLICE + 2)                                         # no bar before END
    with pytest.raises(R.RunnerError, match='not provably late-listed'):
        go(env, symbols=SYMS + ['HYPEUSDT'], exclude_late=['HYPEUSDT'])
    d = tape('HYPEUSDT', plan=[], start=40)
    d.loc[0, 't'] += 60_000                                                         # first bar off the 4h grid
    d.to_csv(os.path.join(env['cache'], 'HYPEUSDT_4h.csv'), index=False)
    with pytest.raises(R.RunnerError, match='malformed raw open times'):          # refused before late detection
        go(env, symbols=SYMS + ['HYPEUSDT'], exclude_late=['HYPEUSDT'])
    _add(env, 'HYPEUSDT', start=40)
    for s in SYMS:
        _add(env, s, start=10)
    with pytest.raises(R.RunnerError, match='every requested symbol is excluded'):
        go(env, symbols=SYMS + ['HYPEUSDT'], exclude_late=SYMS + ['HYPEUSDT'])


@pytest.mark.parametrize('how', ['gap', 'duplicate', 'short_end', 'invalid_ohlc'])
def test_late_symbol_with_broken_listed_history_refuses(env, how):
    """Cowork 6230180232 (a): the exclusion flag never excuses a gap (or invalid OHLC) after the listing."""
    d = tape('HYPEUSDT', plan=[], start=40)
    i = W + 30                                                     # a row after the listing, before END
    if how == 'gap':
        d = d.drop(index=i)
    elif how == 'duplicate':
        d = pd.concat([d, d.iloc[[i]]])
    elif how == 'short_end':
        d = d[d.t < HI - 3 * H4]                                   # data stops before END
    else:
        d.loc[i, 'l'] = -1.0
    d.to_csv(os.path.join(env['cache'], 'HYPEUSDT_4h.csv'), index=False)
    with pytest.raises(R.RunnerError, match='does not excuse gaps|OHLC sanity'):
        go(env, symbols=SYMS + ['HYPEUSDT'], exclude_late=['HYPEUSDT'])
    assert not os.path.exists(env['tmp'] / 'p.json')


def _core10(env):
    static = VC.frozen_universe()
    for s in static['core_10']:
        if s not in SYMS:
            _add(env, s, start=40 if s == 'HYPEUSDT' else None)
    return static


def test_tier_run_with_late_exclusion_ledger(env):
    static = _core10(env)
    man = manifest(env, static['core_10'])
    with pytest.raises(R.RunnerError, match='INCOMPLETE'):
        go(env, symbols=None, tier='CORE-10', manifest=man)
    p = go(env, symbols=None, tier='CORE-10', exclude_late=['HYPEUSDT'], manifest=man)
    u = p['universe']
    assert u['requested'] == static['core_10'] and u['run'] == [s for s in static['core_10'] if s != 'HYPEUSDT']
    assert len(u['run']) == 9 and u['tier'] == 'CORE-10' and u['tier_label'] == 'CORE-10-ELIGIBLE-9'
    assert [x['symbol'] for x in u['excluded']] == ['HYPEUSDT']
    ex = [x['symbol'] for x in u['excluded']]                       # Cowork (b): run + excluded == requested, disjoint
    assert sorted(u['run'] + ex) == sorted(u['requested']) and not set(u['run']) & set(ex)
    assert set(p['cache']['completeness']) == set(u['run'])         # exactly the simulated symbols
    assert p['stats']['combined']['n'] == 7                         # only ETH / SOL carry planted setups
    assert set(p['manifest']['files']) == set(static['core_10'])    # the late-excluded symbol is bound too


def test_cli_exclude_late(env, capsys, monkeypatch):
    _cli_repo(env, monkeypatch)
    _add(env, 'HYPEUSDT', start=40)
    out = str(env['tmp'] / 'cli.json')
    assert R.main(['--cache', env['cache'], '--window', WINDOW, '--symbols', ','.join(SYMS + ['HYPEUSDT']),
                   '--exclude-late', 'HYPEUSDT', '--out', out]) == 0
    assert 'run 2 excluded 1' in capsys.readouterr().out
    with open(out, encoding='utf-8') as f:
        assert json.load(f)['universe']['excluded'][0]['symbol'] == 'HYPEUSDT'


# ------------------------------------------------------------------ manifest binding (Codex 6231454794)
def _cli_run(env, capsys, extra, syms=SYMS, tier=None):
    outdir = env['tmp'] / 'outdir'
    outdir.mkdir(exist_ok=True)
    out = str(outdir / 'cli.json')
    sel = ['--tier', tier] if tier else ['--symbols', ','.join(syms)]
    rc = R.main(['--cache', env['cache'], '--window', WINDOW, *sel, *extra, '--out', out])
    return rc, capsys.readouterr(), outdir


def test_manifest_bound_and_recorded(env, capsys, monkeypatch):
    _cli_repo(env, monkeypatch)
    _add(env, 'HYPEUSDT', start=40)
    man = manifest(env, SYMS + ['HYPEUSDT'])
    with open(man, 'rb') as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    rc, _, outdir = _cli_run(env, capsys, ['--exclude-late', 'HYPEUSDT', '--manifest', man, '--manifest-commit',
                                           'abc1234'], syms=SYMS + ['HYPEUSDT'])
    assert rc == 0
    with open(outdir / 'cli.json', encoding='utf-8') as f:
        m = json.load(f)['manifest']
    assert m['sha256'] == sha and m['commit'] == 'abc1234' and m['path'] == os.path.abspath(man)
    assert set(m['files']) == set(SYMS + ['HYPEUSDT'])
    for s, b in m['files'].items():
        assert b['result'] == 'match' and b['checked'] == ['sha256', 'rows', 'file', 'first', 'last']


def test_manifest_first_last_ms_checked(env):
    def ms(m):
        for s, e in m['files'].items():
            d = pd.read_csv(os.path.join(env['cache'], e['file']))
            e['first_ms'], e['last_ms'] = int(d.t.min()), int(d.t.max())
    p = go(env, manifest=manifest(env, SYMS, edit=ms))
    assert p['manifest']['files']['ETHUSDT']['checked'] == ['sha256', 'rows', 'file', 'first_ms', 'first', 'last_ms',
                                                            'last']

    def bad(m):
        ms(m)
        m['files']['SOLUSDT']['last_ms'] += H4
    with pytest.raises(R.RunnerError, match='last open time'):
        go(env, out='q.json', manifest=manifest(env, SYMS, name='m2.json', edit=bad))


def _set(sym, key, val):
    def f(m):
        m['files'][sym][key] = val
    return f


def _drop(sym):
    def f(m):
        del m['files'][sym]
    return f


MAN_BAD = {
    'sha256_mismatch': (_set('SOLUSDT', 'sha256', '0' * 64), 'sha256'),
    'rows_mismatch': (_set('SOLUSDT', 'rows', 1), 'rows'),
    'rows_not_int': (_set('SOLUSDT', 'rows', '1'), 'rows'),
    'missing_entry': (_drop('SOLUSDT'), 'no entry for SOLUSDT'),
    'file_mismatch': (_set('SOLUSDT', 'file', 'X_4h.csv'), 'cache file'),
    'first_mismatch': (_set('ETHUSDT', 'first', '2020-01-01T00:00:00Z'), 'first open time'),
    'last_mismatch': (_set('ETHUSDT', 'last', SP.utc(HI - H4)), 'last open time'),
}


@pytest.mark.parametrize('how', sorted(MAN_BAD))
def test_manifest_mismatch_refused_at_cli(env, capsys, monkeypatch, how):
    _cli_repo(env, monkeypatch)
    edit, msg = MAN_BAD[how]
    rc, cap, outdir = _cli_run(env, capsys, ['--manifest', manifest(env, SYMS, edit=edit)])
    assert rc == 2 and 'REFUSED' in cap.err and msg in cap.err and os.listdir(outdir) == []


def test_manifest_late_symbol_is_checked(env, capsys, monkeypatch):
    _cli_repo(env, monkeypatch)
    _add(env, 'HYPEUSDT', start=40)
    man = manifest(env, SYMS + ['HYPEUSDT'], edit=_set('HYPEUSDT', 'sha256', 'f' * 64))
    rc, cap, outdir = _cli_run(env, capsys, ['--exclude-late', 'HYPEUSDT', '--manifest', man], syms=SYMS + ['HYPEUSDT'])
    assert rc == 2 and 'HYPEUSDT cache sha256' in cap.err and os.listdir(outdir) == []


def _write_raw_manifest(env, text):
    p = env['tmp'] / 'dup_manifest.json'
    p.write_text(text, encoding='utf-8')
    return str(p)


def _entries(env):
    files = json.loads(open(manifest(env, SYMS, name='src.json'), encoding='utf-8').read())['files']
    bad = dict(files['SOLUSDT'], sha256='0' * 64)
    return files, bad


def test_manifest_duplicate_symbol_entry_refused(env, capsys, monkeypatch):
    """A repeated symbol key (bad entry first, good one second) refuses: no last-one-wins."""
    _cli_repo(env, monkeypatch)
    files, bad = _entries(env)
    text = ('{"files": {"ETHUSDT": %s, "SOLUSDT": %s, "SOLUSDT": %s}}'
            % (json.dumps(files['ETHUSDT']), json.dumps(bad), json.dumps(files['SOLUSDT'])))
    rc, cap, outdir = _cli_run(env, capsys, ['--manifest', _write_raw_manifest(env, text)])
    assert rc == 2 and 'duplicate key' in cap.err and 'SOLUSDT' in cap.err and os.listdir(outdir) == []


def test_manifest_duplicate_top_level_files_refused(env, capsys, monkeypatch):
    _cli_repo(env, monkeypatch)
    files, bad = _entries(env)
    text = '{"files": %s, "files": %s}' % (json.dumps(dict(files, SOLUSDT=bad)), json.dumps(files))
    rc, cap, outdir = _cli_run(env, capsys, ['--manifest', _write_raw_manifest(env, text)])
    assert rc == 2 and "duplicate key 'files'" in cap.err and os.listdir(outdir) == []


def test_manifest_key_case_must_match(env, capsys, monkeypatch):
    _cli_repo(env, monkeypatch)

    def lower(m):
        m['files']['solusdt'] = m['files'].pop('SOLUSDT')
    rc, cap, outdir = _cli_run(env, capsys, ['--manifest', manifest(env, SYMS, edit=lower)])
    assert rc == 2 and 'no entry for SOLUSDT' in cap.err and os.listdir(outdir) == []


@pytest.mark.parametrize('key', ['first_ms', 'last_ms'])
@pytest.mark.parametrize('val', ['same_as_float', 1.7e12])
def test_manifest_ms_must_be_int(env, key, val):
    def ms(m):
        for s, e in m['files'].items():
            d = pd.read_csv(os.path.join(env['cache'], e['file']))
            e['first_ms'], e['last_ms'] = int(d.t.min()), int(d.t.max())
        e = m['files']['SOLUSDT']
        e[key] = float(e[key]) if val == 'same_as_float' else val
    with pytest.raises(R.RunnerError, match=f'{key[:-3]} open time'):
        go(env, manifest=manifest(env, SYMS, edit=ms))


def test_manifest_missing_file_symbol_refused(env):
    man = manifest(env, SYMS)
    os.remove(os.path.join(env['cache'], 'SOLUSDT_4h.csv'))
    with pytest.raises(R.RunnerError, match='SOLUSDT cache sha256 None'):
        go(env, manifest=man)


@pytest.mark.parametrize('body', [None, b'{not json', b'\xff\xfe', b'[]', b'{"files": []}'],
                         ids=['absent', 'not_json', 'not_utf8', 'not_object', 'files_not_object'])
def test_manifest_unreadable_refused(env, capsys, monkeypatch, body):
    _cli_repo(env, monkeypatch)
    path = env['tmp'] / 'bad_manifest.json'
    if body is not None:
        path.write_bytes(body)
    rc, cap, outdir = _cli_run(env, capsys, ['--manifest', str(path)])
    assert rc == 2 and 'REFUSED' in cap.err and 'manifest' in cap.err and os.listdir(outdir) == []


def test_manifest_required_for_tier_run(env, capsys, monkeypatch):
    _cli_repo(env, monkeypatch)
    _core10(env)
    reads = []
    real = R.B.read_cache
    monkeypatch.setattr(R.B, 'read_cache', lambda *a, **k: (reads.append(a), real(*a, **k))[1])
    rc, cap, outdir = _cli_run(env, capsys, ['--exclude-late', 'HYPEUSDT'], tier='CORE-10')
    assert rc == 2 and 'requires --manifest' in cap.err and os.listdir(outdir) == [] and not reads


def test_manifest_hash_is_of_the_bytes_checked(env, monkeypatch):
    """The recorded sha256 is of the exact bytes parsed, even if the file changes right after they were read."""
    man = manifest(env, SYMS)
    with open(man, 'rb') as f:
        raw = f.read()
    real, state = R.json.loads, {'done': False}

    def loads(*a, **k):
        if not state['done'] and a and a[0] == raw.decode('utf-8'):    # only the manifest parse
            state['done'] = True
            with open(man, 'ab') as f:
                f.write(b' ')                                       # the file changes between read and hash
        return real(*a, **k)
    monkeypatch.setattr(R.json, 'loads', loads)
    p = go(env, manifest=man)
    assert state['done'] and p['manifest']['sha256'] == hashlib.sha256(raw).hexdigest()


# ------------------------------------------------------------------ write races
def test_out_created_during_run_is_never_replaced(env, monkeypatch):
    out = env['tmp'] / 'p.json'
    real = RX.summarize

    def racing(trades):
        with open(out, 'wb') as f:                                  # appears after the initial exists() check
            f.write(b'ORIGINAL')
        return real(trades)
    monkeypatch.setattr(RX, 'summarize', racing)
    with pytest.raises(R.RunnerError, match='created during the run'):
        go(env)
    with open(out, 'rb') as f:
        assert f.read() == b'ORIGINAL'
    assert sorted(os.listdir(env['tmp'])) == sorted(['candles', 'repo', 'p.json'])     # temp file removed


def test_packet_write_leaves_no_temp(env):
    go(env)
    assert sorted(os.listdir(env['tmp'])) == sorted(['candles', 'repo', 'p.json'])


def test_two_writers_same_out(env, monkeypatch):
    """Two runs race for one --out: the second finishes first and wins; the first is refused and leaves its bytes."""
    out = env['tmp'] / 'p.json'
    real = RX.summarize
    state = {'inner': None}

    def first_writer_pauses(trades):
        if state['inner'] is None:
            state['inner'] = 'running'
            monkeypatch.setattr(RX, 'summarize', real)
            state['inner'] = R.run(out=str(out), cache=env['cache'], window=WINDOW, symbols=SYMS, repo=env['repo'])
        return real(trades)
    monkeypatch.setattr(RX, 'summarize', first_writer_pauses)
    with pytest.raises(R.RunnerError, match='created during the run'):
        go(env)
    with open(out, 'rb') as f:
        body = f.read()
    assert json.loads(body)['packet_sha256'] == state['inner']['packet_sha256']
    assert sorted(os.listdir(env['tmp'])) == sorted(['candles', 'repo', 'p.json'])


# ------------------------------------------------------------------ Codex 6233040303: module input hygiene
SAME = ('stats', 'trades', 'skips', 'blocked', 'open_at_end', 'decision', 'warmup', 'universe', 'window', 'rules')


def _poison(d):
    """Fixture-style indicator columns that would change every C4 signal if the module used them: a steep EMA50 ramp
    (slope gate fails) and a tiny ATR (width gate fails)."""
    return d.assign(e50=np.arange(len(d), dtype=float) * 50.0, atr=1e-3)


def test_module_frame_drops_injection_columns():
    d = sliced('ETHUSDT')
    m = R.module_frame(_poison(d))
    assert 'e50' not in m and 'atr' not in m and list(m.columns) == list(d.columns)
    assert m.equals(d) and R.module_frame(d).equals(d) and R.INJECTION_COLS == ('e50', 'atr')
    only = R.module_frame(d.assign(atr=1.0))
    assert 'atr' not in only and only.equals(d)
    # teeth: the poisoned frame really changes the module's answer, so the drop is load-bearing
    assert RX.run_symbol(_poison(d))['signals'] == [] and RX.run_symbol(m)['signals'] == RX.run_symbol(d)['signals']


def test_injected_e50_atr_columns_in_cache_leave_packet_unchanged(env):
    """e50 / atr columns planted in the cache files change only the file digests, never a result."""
    base = go(env, out='base.json')
    for sym in SYMS:
        path = os.path.join(env['cache'], f'{sym}_4h.csv')
        _poison(pd.read_csv(path, encoding='utf-8')).to_csv(path, index=False)
    p = go(env, out='inj.json')
    for f in SAME:
        assert p[f] == base[f], f
    assert p['stats']['combined']['n'] == 7
    for sym in SYMS:
        assert p['cache']['files'][sym]['sha256'] != base['cache']['files'][sym]['sha256']


def test_injected_columns_dropped_even_if_the_reader_keeps_them(env, monkeypatch):
    """If the shared reader ever passed e50 / atr through, the runner still drops them before the module."""
    base = go(env, out='base.json')
    real_read, real_run, seen = R.B.read_cache, RX.run_symbol, []
    monkeypatch.setattr(R.B, 'read_cache', lambda *a, **k: (lambda df, rec: (None if df is None else _poison(df), rec))(
        *real_read(*a, **k)))
    monkeypatch.setattr(RX, 'run_symbol', lambda df, *a, **k: (seen.append(list(df.columns)), real_run(df, *a, **k))[1])
    p = go(env, out='inj.json')
    assert len(seen) == 2 and all('e50' not in c and 'atr' not in c for c in seen)
    for f in SAME + ('cache', 'manifest'):
        assert p[f] == base[f], f


@pytest.mark.parametrize('vals', [dict(c=float('nan')), dict(o=float('nan')), dict(l=0.0), dict(h=-1.0, l=-2.0),
                                  dict(h=149.0, l=151.0)],
                         ids=['nan_close', 'nan_open', 'zero_low', 'negative_bar', 'high_below_low'])
def test_invalid_price_refused_by_the_runner_before_the_module(env, capsys, monkeypatch, vals):
    """NaN / non-positive / h < l prices refuse in the runner with its own message; the C4 module never sees the frame,
    so its misleading 'prices must be > 0' error is never the refusal path."""
    _cli_repo(env, monkeypatch)
    _poke(env, IN_WIN, **vals)
    d = VC.slice_frame(pd.read_csv(os.path.join(env['cache'], 'SOLUSDT_4h.csv')), WARM_LO, HI)
    if 'h' not in vals or vals['h'] < 0:                            # the module alone would give the misleading error
        with pytest.raises(ValueError, match='prices must be > 0'):
            RX.run_symbol(d)
    calls = []
    for f in ('run_symbol', 'detect', 'simulate_trade', 'indicators', '_arrays'):
        monkeypatch.setattr(RX, f, lambda *a, _f=f, **k: calls.append(_f))
    with pytest.raises(R.RunnerError, match='^rer: OHLC sanity failed for .*SOLUSDT') as e:
        go(env)
    assert 'prices must be' not in str(e.value) and not calls and not os.path.exists(env['tmp'] / 'p.json')
    outdir = env['tmp'] / 'outdir'
    outdir.mkdir()
    assert R.main(['--cache', env['cache'], '--window', WINDOW, '--symbols', ','.join(SYMS),
                   '--out', str(outdir / 'cli.json')]) == 2
    err = capsys.readouterr().err
    assert 'REFUSED: rer: OHLC sanity' in err and 'prices must be' not in err and not calls and os.listdir(outdir) == []


# ------------------------------------------------------------------ OHLC sanity
def _poke(env, row, **vals):
    path = os.path.join(env['cache'], 'SOLUSDT_4h.csv')
    d = pd.read_csv(path, encoding='utf-8')
    for k, v in vals.items():
        d.loc[row, k] = v
    d.to_csv(path, index=False)


IN_WIN = BG                                                         # a csv row inside [START, END) (background doji at 150)


@pytest.mark.parametrize('vals', [dict(c=float('nan')), dict(o=0.0, h=0.0, l=0.0, c=0.0), dict(h=98.0, l=99.0),
                                  dict(h=149.5), dict(l=150.5), dict(l=-1.0), dict(o=float('inf')),
                                  dict(o=150.0, c=151.0, h=150.5, l=149.0),
                                  dict(o=151.0, c=150.0, h=152.0, l=150.5)],
                         ids=['nan_close', 'zero_bar', 'h_below_l', 'h_below_close', 'l_above_close', 'negative_low',
                              'inf_open', 'high_below_body_top', 'low_above_body_bottom'])
def test_ohlc_sanity_refuses(env, vals):
    _poke(env, IN_WIN, **vals)
    with pytest.raises(R.RunnerError, match='OHLC sanity'):
        go(env)
    assert not os.path.exists(env['tmp'] / 'p.json')


def test_ohlc_sanity_covers_warmup(env):
    _poke(env, HEAD + 3, c=float('nan'))                            # inside [warm-up start, START)
    with pytest.raises(R.RunnerError, match='OHLC sanity'):
        go(env)


def test_ohlc_sanity_ignores_bars_before_warmup_and_after_end(env):
    _poke(env, HEAD - 1, c=float('nan'))                            # before the warm-up start
    for k in range(HEAD + NSLICE, HEAD + NSLICE + 5):               # at / after END (the holdout side)
        _poke(env, k, h=-5.0, l=-9.0, c=float('nan'))
    assert go(env)['stats']['combined']['n'] == 7


def test_ohlc_problems_helper_counts():
    d = pd.DataFrame(dict(t=pd.to_datetime([0, H4, 2 * H4, 3 * H4], unit='ms'), o=[1.0, 1.0, 1.0, 1.0],
                          h=[2.0, 0.5, 2.0, 2.0], l=[0.5, 0.4, 1.5, 0.5], c=[1.5, 0.45, 1.6, 1.5]))
    assert R.ohlc_problems(d) == {'h_below_max_oc': 1, 'l_above_min_oc': 1, 'first_bad': SP.utc(H4)}
    assert R.ohlc_problems(d.iloc[[0, 3]]) == {}


def test_ohlc_equality_bars_are_valid(env):
    _poke(env, IN_WIN, o=150.0, h=150.0, l=150.0, c=150.0)         # flat bar: h == l == o == c
    _poke(env, IN_WIN + 1, o=150.0, h=151.0, l=149.0, c=151.0)     # h == close
    _poke(env, IN_WIN + 2, o=149.0, h=151.0, l=149.0, c=150.0)     # l == open
    assert go(env)['stats']['combined']['n'] == 7


def test_ohlc_problems_inverted_counted():
    d = pd.DataFrame(dict(t=pd.to_datetime([0], unit='ms'), o=[1.0], h=[0.9], l=[1.1], c=[1.0]))
    assert R.ohlc_problems(d)['h_below_l'] == 1


# ------------------------------------------------------------------ Cowork 6230180232 pre-staged attacks
def test_no_partial_file_after_mid_write_crash(env, monkeypatch):
    """(c) a crash while the packet is being written leaves neither --out nor a temp file."""
    def boom(fd):
        raise OSError('injected fsync failure')
    monkeypatch.setattr(R.os, 'fsync', boom)
    with pytest.raises(OSError, match='injected'):
        go(env)
    assert sorted(os.listdir(env['tmp'])) == sorted(['candles', 'repo'])


def test_fsync_before_link(env, monkeypatch):
    order = []
    real_fsync, real_link = R.os.fsync, R.os.link
    monkeypatch.setattr(R.os, 'fsync', lambda fd: (order.append('fsync'), real_fsync(fd))[1])
    monkeypatch.setattr(R.os, 'link', lambda a, b: (order.append('link'), real_link(a, b))[1])
    go(env)
    assert order == ['fsync', 'link']


def test_no_file_after_mid_run_crash(env, monkeypatch):
    monkeypatch.setattr(R, 'excursions', lambda *a: (_ for _ in ()).throw(RuntimeError('injected mid-run')))
    with pytest.raises(RuntimeError, match='injected mid-run'):
        go(env)
    assert sorted(os.listdir(env['tmp'])) == sorted(['candles', 'repo'])


def test_post_end_spike_does_not_leak(env):
    """(d) rows at / after END that would change EMA / ATR and the signals if read leave every result unchanged;
    only the cache file digest moves (one-byte provenance)."""
    base = go(env, out='base.json')
    for s in SYMS:
        path = os.path.join(env['cache'], f'{s}_4h.csv')
        d = pd.read_csv(path, encoding='utf-8')
        k = d.t >= HI
        assert k.sum() == TAIL
        P = LEVEL[s]
        d.loc[k, ['o', 'h', 'l', 'c']] = np.array([P, P + 500.0, P - 0.5, P + 400.0])      # huge range + probes
        d.to_csv(path, index=False)
    p = go(env, out='spike.json')
    for f in ('stats', 'trades', 'skips', 'blocked', 'open_at_end', 'decision', 'warmup', 'universe', 'window'):
        assert p[f] == base[f], f
    for s in SYMS:
        assert p['cache']['files'][s]['sha256'] != base['cache']['files'][s]['sha256']
    assert p['packet_sha256'] != base['packet_sha256']


@pytest.mark.parametrize('bars', [dict(o=1e300, h=1e301, l=1e-300, c=5e300), dict(o=0.0, h=-1.0, l=7.0, c=float('nan')),
                                  dict(o=float('inf'), h=float('-inf'), l=float('nan'), c=-3.0)],
                         ids=['absurd', 'invalid', 'non_finite'])
def test_post_end_absurd_or_invalid_bars_do_not_leak(env, bars):
    """Absurd or OHLC-invalid bars planted at / after END are never checked or read: the run is unchanged."""
    base = go(env, out='base.json')
    for s in SYMS:
        path = os.path.join(env['cache'], f'{s}_4h.csv')
        d = pd.read_csv(path, encoding='utf-8')
        k = d.t >= HI
        for c, v in bars.items():
            d.loc[k, c] = v
        d.to_csv(path, index=False)
    p = go(env, out='bad.json')
    for f in ('stats', 'trades', 'skips', 'blocked', 'open_at_end', 'decision'):
        assert p[f] == base[f], f


def test_future_rows_truncated_cache_is_identical(env):
    """Causality: a cache cut exactly at END gives the same results as one that runs on past END."""
    base = go(env, out='base.json')
    for s in SYMS:
        path = os.path.join(env['cache'], f'{s}_4h.csv')
        d = pd.read_csv(path, encoding='utf-8')
        d[d.t < HI].to_csv(path, index=False)
    p = go(env, out='cut.json')
    for f in ('stats', 'trades', 'skips', 'blocked', 'open_at_end', 'decision', 'warmup'):
        assert p[f] == base[f], f


def test_bar_stamped_end_is_cut_last_bar_kept(env):
    p = go(env)
    o, = p['open_at_end']['trades']
    assert o['t_in'] == SP.utc(LO + (NSLICE - 5 - W) * H4)            # entry on bar NSLICE-5, never exits before END
    assert p['window']['bars'] == NSLICE - W


@pytest.mark.parametrize('first, ok', [(0, True), (1, False)])
def test_exactly_220_warmup_bars_ok_219_refuses(env, first, ok):
    """(e) the cache starting exactly at the warm-up start (220 real bars) runs; one bar later (219) refuses."""
    for s in SYMS:
        tape(s, start=first).to_csv(os.path.join(env['cache'], f'{s}_4h.csv'), index=False)
    if ok:
        assert go(env)['stats']['combined']['n'] == 7
    else:
        with pytest.raises(R.RunnerError, match='INCOMPLETE'):
            go(env)
        assert not os.path.exists(env['tmp'] / 'p.json')


def test_entry_exactly_on_quarter_boundary(env):
    """(f) an entry at 2025-04-01T00:00:00Z belongs to 2025Q2; one bar earlier to 2025Q1."""
    plan = [(Q2 - 1, 'long', 'time'), (Q2 - 70, 'short', 'time')]
    tape('XRPUSDT', plan=plan).to_csv(os.path.join(env['cache'], 'XRPUSDT_4h.csv'), index=False)
    p = go(env, symbols=['XRPUSDT'])
    rows = {x['t_in']: x for x in p['trades']}
    assert rows['2025-04-01T00:00:00Z']['quarter'] == '2025Q2'
    assert rows[SP.utc(LO + (Q2 - 70 + 1 - W) * H4)]['quarter'] == '2025Q1'
    q = p['stats']['combined']['quarters']
    assert q['2025Q2'] == R._r(rows['2025-04-01T00:00:00Z']['R_net'])
    assert R.quarter(SP.parse_utc('2025-04-01T00:00:00Z') - 1) == '2025Q1'
    for m in ('01', '04', '07', '10'):
        assert R.quarter(SP.parse_utc(f'2025-{m}-01T00:00:00Z')) == f'2025Q{(int(m) - 1) // 3 + 1}'


# ------------------------------------------------------------------ review mutants (window boundary, late order)
def test_signal_on_first_in_window_bar_is_counted(env):
    """A qualifying trigger exactly at b == W (the first in-window bar) is an in-window signal: traded, not excluded."""
    d = tape('ETHUSDT', plan=[(W, 'long', 'time')])
    d.to_csv(os.path.join(env['cache'], 'ETHUSDT_4h.csv'), index=False)
    s = VC.slice_frame(d, WARM_LO, HI)
    assert [g['b'] for g in RX.run_symbol(s)['trades']] == [W]
    ex = []
    with R.window_signals(W, ex):
        r = RX.run_symbol(s)
    assert [t['b'] for t in r['trades']] == [W] and ex == []
    p = go(env, symbols=['ETHUSDT'])
    assert p['warmup']['signals_excluded'] == {'long': 0, 'short': 0}
    assert [x['t_signal'] for x in p['trades']] == [p['window']['start']]
    assert p['stats']['combined']['n'] == 1


def test_late_symbol_unsorted_rows_refused_via_cli(env, capsys, monkeypatch):
    """A late-listed symbol whose listed rows are all present and valid but out of order is refused: the flag needs the
    exact strictly increasing 4h grid, not just the right set of bars."""
    _cli_repo(env, monkeypatch)
    d = tape('HYPEUSDT', plan=[], start=40)
    i = W + 30
    d.iloc[[i, i + 1]] = d.iloc[[i + 1, i]].to_numpy()                # swap two listed rows: same set, wrong order
    d.to_csv(os.path.join(env['cache'], 'HYPEUSDT_4h.csv'), index=False)
    out = str(env['tmp'] / 'cli.json')
    assert R.main(['--cache', env['cache'], '--window', WINDOW, '--symbols', ','.join(SYMS + ['HYPEUSDT']),
                   '--exclude-late', 'HYPEUSDT', '--out', out]) == 2
    err = capsys.readouterr().err
    assert 'REFUSED' in err and 'HYPEUSDT' in err and not os.path.exists(out)


# ------------------------------------------------------------------ Codex 6230537709: raw-time integrity + Q2 labels
BAD_T = {'nan': 'nan', 'inf': 'inf', 'non_numeric': 'abc', 'fractional_ms': '+0.5', 'off_grid': '+1',
         'huge_exponent': '14400000e400', 'above_2_63': '10000000000008000000', 'negative': '-14400000'}
HUGE = ('huge_exponent', 'above_2_63', 'negative')                  # on the 4h grid, outside [0, 2^53)


def _bad_t(env, sym, row, how):
    """Rewrite one raw open time as text (no float round-trip), so the malformed value reaches the file verbatim."""
    path = os.path.join(env['cache'], f'{sym}_4h.csv')
    with open(path, encoding='utf-8') as f:
        lines = f.read().split('\n')
    head, rest = lines[row + 1].split(',', 1)
    t = int(float(head))
    v = BAD_T[how]
    new = f'{t}.5' if v == '+0.5' else str(t + 1) if v == '+1' else v
    lines[row + 1] = f'{new},{rest}'
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write('\n'.join(lines))
    return new


def _cli_refuses(env, capsys, syms=SYMS, extra=()):
    outdir = env['tmp'] / 'outdir'
    outdir.mkdir()
    out = str(outdir / 'cli.json')
    assert R.main(['--cache', env['cache'], '--window', WINDOW, '--symbols', ','.join(syms), *extra,
                   '--out', out]) == 2
    err = capsys.readouterr().err
    assert 'REFUSED' in err and 'malformed raw open times' in err
    assert os.listdir(outdir) == []
    return err


@pytest.mark.parametrize('where', ['in_window', 'after_end'])
@pytest.mark.parametrize('how', sorted(BAD_T))
def test_malformed_raw_open_time_refused_at_cli(env, capsys, monkeypatch, how, where):
    _cli_repo(env, monkeypatch)
    row = IN_WIN if where == 'in_window' else HEAD + NSLICE + TAIL - 3      # after END: never sliced, still refused
    _bad_t(env, 'SOLUSDT', row, how)
    assert 'SOLUSDT' in _cli_refuses(env, capsys)


@pytest.mark.parametrize('how', ['nan', 'fractional_ms', 'off_grid'])
def test_malformed_raw_open_time_in_late_excluded_symbol_refused(env, capsys, monkeypatch, how):
    _cli_repo(env, monkeypatch)
    _add(env, 'HYPEUSDT', start=40)
    _bad_t(env, 'HYPEUSDT', len(tape('HYPEUSDT', plan=[], start=40)) - 2, how)    # after END in the excluded symbol
    assert 'HYPEUSDT' in _cli_refuses(env, capsys, SYMS + ['HYPEUSDT'], ('--exclude-late', 'HYPEUSDT'))


def test_malformed_raw_open_time_refused_by_coverage_check(env):
    _bad_t(env, 'ETHUSDT', HEAD + NSLICE + 2, 'nan')
    with pytest.raises(R.RunnerError, match='malformed raw open times'):
        R.check_coverage(cache=env['cache'], window=WINDOW, symbols=SYMS)


def test_raw_time_problems_helper():
    ok = 'x\n'.join(['t,o', f'{WARM_LO}.0,1', f'{WARM_LO + H4},1', f'{float(WARM_LO + 2 * H4)!r},1']) + 'x'
    assert R.raw_time_problems(ok.replace('x', '').encode()) == {}
    for v, k in (('nan', 'nan_or_inf'), ('NaN', 'nan_or_inf'), ('-inf', 'nan_or_inf'), ('', 'non_numeric'),
                 ('1e', 'non_numeric'), (f'{WARM_LO}.5', 'non_integer_ms'), (f'{WARM_LO}.000001', 'non_integer_ms'),
                 (str(WARM_LO + 1), 'off_grid'), (str(WARM_LO - 1), 'off_grid')):
        p = R.raw_time_problems(f't,o\n{WARM_LO},1\n{v},1\n'.encode())
        assert p[k] == 1 and p['first_bad'].startswith('data row 2'), (v, p)


def test_q2_symbols_run_is_non_decision(env):
    p = go(env)
    assert p['universe']['tier_label'] == 'SYMBOLS-2' and p['universe']['non_decision'] is True
    assert p['non_decision'] is True and p['decision']['non_decision'] is True
    assert p['decision']['tier_verdict'] == 'NON_DECISION'
    assert p['decision']['diagnostic_verdict'] in ('CANDIDATE', 'PARK')
    one = go(env, out='one.json', symbols=['ETHUSDT'])
    assert one['universe']['tier_label'] == 'SYMBOLS-1' and one['decision']['tier_verdict'] == 'NON_DECISION'


def test_q2_symbols_run_with_late_exclusion_counts_run_symbols(env):
    _add(env, 'HYPEUSDT', start=40)
    p = go(env, symbols=SYMS + ['HYPEUSDT'], exclude_late=['HYPEUSDT'])
    assert p['universe']['tier_label'] == 'SYMBOLS-2' and p['non_decision'] is True


def test_q2_tier_run_stays_decision_bearing(env):
    static = _core10(env)
    p = go(env, symbols=None, tier='CORE-10', exclude_late=['HYPEUSDT'], manifest=manifest(env, static['core_10']))
    assert p['universe']['tier_label'] == 'CORE-10-ELIGIBLE-9' and p['universe']['non_decision'] is False
    assert p['non_decision'] is False and p['decision']['non_decision'] is False
    assert p['decision']['tier_verdict'] in ('CANDIDATE', 'PARK') and 'diagnostic_verdict' not in p['decision']


def test_raw_time_check_must_cover_the_bytes_the_reader_hashed(env, monkeypatch):
    real = R.B.read_cache

    def swapped(cache, s, tf):                                  # the file changes between the check and the read
        df, rec = real(cache, s, tf)
        return df, dict(rec, sha256='0' * 64)
    monkeypatch.setattr(R.B, 'read_cache', swapped)
    with pytest.raises(R.RunnerError, match='changed while being read'):
        go(env)
    assert not os.path.exists(env['tmp'] / 'p.json')


@pytest.mark.parametrize('where', ['in_window', 'after_end'])
@pytest.mark.parametrize('how', HUGE)
def test_out_of_range_open_time_refused_by_cli_coverage_check(env, capsys, how, where):
    row = IN_WIN if where == 'in_window' else HEAD + NSLICE + TAIL - 3
    _bad_t(env, 'SOLUSDT', row, how)
    assert R.main(['--cache', env['cache'], '--window', WINDOW, '--symbols', ','.join(SYMS), '--check-coverage']) == 2
    err = capsys.readouterr().err
    assert 'REFUSED' in err and 'malformed raw open times' in err and 'out_of_range' in err


def test_raw_time_range_bounds():
    top = (2 ** 53 - 1) // H4 * H4                                 # largest on-grid value below 2^53
    for v, ok in (('0', True), (str(top), True), (str(top + H4), False), (str(-H4), False)):
        p = R.raw_time_problems(f't,o\n{v},1\n'.encode())
        assert (p == {}) is ok and (ok or p['out_of_range'] == 1), (v, p)


def test_raw_time_row_number_counts_data_rows_not_blank_lines():
    p = R.raw_time_problems(f't,o\n\n{WARM_LO},1\n\nnan,1\n'.encode())
    assert p['first_bad'] == "data row 2: 'nan'"


def test_file_appearing_after_raw_check_refused_at_cli(env, capsys, monkeypatch):
    """TOCTOU: absent at the raw-time check, created (NaN open time after END) before the shared reader opens it."""
    _cli_repo(env, monkeypatch)
    path = os.path.join(env['cache'], 'SOLUSDT_4h.csv')
    with open(path, encoding='utf-8') as f:
        body = f.read()
    os.remove(path)
    real = R.B.read_cache

    def appear(cache, s, tf):
        if s == 'SOLUSDT' and not os.path.exists(path):
            with open(path, 'w', encoding='utf-8', newline='') as f:
                f.write(body)
            _bad_t(env, 'SOLUSDT', HEAD + NSLICE + TAIL - 3, 'nan')
        return real(cache, s, tf)
    monkeypatch.setattr(R.B, 'read_cache', appear)
    outdir = env['tmp'] / 'outdir'
    outdir.mkdir()
    assert R.main(['--cache', env['cache'], '--window', WINDOW, '--symbols', ','.join(SYMS),
                   '--out', str(outdir / 'cli.json')]) == 2
    err = capsys.readouterr().err
    assert 'REFUSED' in err and 'appeared after the raw-time check' in err and os.listdir(outdir) == []
