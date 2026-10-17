"""tools/research/run_tpc.py - the C3 trend_pullback_continuation.v0 dev-run CLI.

Synthetic only: a temp 4h candle cache (app._get_candles CSV format) with planted trend / pullback / resumption blocks
on a doji background + a temp git repo for the clean-tree check (the test_vwaprev_candidate_runner fixtures) + synthetic
manifests in temp dirs; no real cache, store or data folder is read. A port of tests/test_run_vcb.py (accepted C2 runner
@ 0fc33f2) protection by protection, plus the manifest binding (Codex 6231454794) and a packet self-hash check.

Each planted block (computed EMA20 / EMA50 / ATR14, no injection): a 25-bar ramp (+1% of the level per bar, peak at
b-6), a 5-bar pullback (-1.6% per bar), the trigger at b, then the trade path (dojis have no body: never a trigger).
Shorts are the price mirror around the level."""
import contextlib
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_tpc as R                                                                         # noqa: E402
import trend_pullback_continuation as TPC                                                   # noqa: E402
import run_vwaprev_candidate as VC                                                          # noqa: E402
import splits as SP                                                                         # noqa: E402
import test_vwaprev_candidate_runner as VT                                                  # noqa: E402
import test_trend_pullback_continuation as CT                                               # noqa: E402

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
RAMP = 24


def doji(p, r):
    return (p, p + r, p - r, p)


def block(b, P, side='long', kind='time'):
    """A planted long trend / pullback / resumption at b (u = 1% of the level P), then the trade path; short = price
    mirror around P."""
    u = P / 100.0
    rows, x = {}, P
    for k in range(b - 6 - RAMP, b - 5):                   # ramp, peak bar b-6 (the swing high)
        rows[k] = (x, x + 1.2 * u, x - 0.2 * u, x + 1.0 * u)
        x += 1.0 * u
    for k in range(b - 5, b):                              # pullback b-5..b-1 (touches EMA20, stays above EMA50)
        tiny = kind == 'cost' and k == b - 1
        c = x - (0.1 if tiny else 2.0 if kind == 'cost' else 1.6) * u
        rows[k] = (x, x + (0.05 if tiny else 0.2) * u, c - (0.05 if tiny else 0.2) * u, c)
        x = c
    c = x + (0.6 if kind == 'cost' else 2.4) * u           # trigger at b
    low = x - 6.0 * u if kind == 'quality' else x - (0.0 if kind == 'cost' else 0.1 * u)
    rows[b] = (x, c + 0.2 * u, low, c)
    E, n = c, (12 if kind in ('gap', 'cost', 'quality') else 32)
    path = {k: doji(E, 0.3 * u) for k in range(b + 1, b + n)}
    if kind == 'target':
        path[b + 3] = (E, E + 9.0 * u, E - 0.3 * u, E + 8.0 * u)
        path.update({k: doji(E + 8.0 * u, 0.3 * u) for k in range(b + 4, b + n)})
    elif kind == 'stop':
        path[b + 3] = (E, E + 0.3 * u, E - 4.0 * u, E - 3.5 * u)
        path.update({k: doji(E - 3.5 * u, 0.3 * u) for k in range(b + 4, b + n)})
    elif kind == 'gap':                                    # entry opens below the stop
        path = {k: doji(E - 5.0 * u, 0.3 * u) for k in range(b + 1, b + n)}
    elif kind == 'follow':                                 # a second trigger at b+1 on the same swing: blocked
        path[b + 1] = (E, E + 1.1 * u, E - 0.1 * u, E + 1.0 * u)
        path.update({k: doji(E + 1.0 * u, 0.3 * u) for k in range(b + 2, b + n)})
    elif kind == 'overlap':                                # a new-swing signal at b+9 while the trade is open
        for i, lv in enumerate((1, 2, 3, 4, 5, 3.5, 2, 0.5)):
            path[b + 1 + i] = doji(E + lv * u, 0.3 * u)
        path[b + 9] = (E + 0.5 * u, E + 3.6 * u, E + 0.4 * u, E + 3.5 * u)
        path.update({k: doji(E + 3.5 * u, 0.3 * u) for k in range(b + 10, b + n)})
    elif kind == 'inval':                                  # EMA50 catches up, then a close below it above the stop
        for i, k in enumerate(range(b + 1, b + n)):
            path[k] = doji(E + min(i, 5) * u, 0.3 * u)
        path[b + 26] = (E + 5.0 * u, E + 5.1 * u, E - 2.0 * u, E - 1.8 * u)
        path.update({k: doji(E - 1.8 * u, 0.3 * u) for k in range(b + 27, b + n)})
    rows.update(path)
    if side == 'short':
        rows = {k: (2 * P - o, 2 * P - lw, 2 * P - h, 2 * P - c) for k, (o, h, lw, c) in rows.items()}
    return rows


PLAN = {'ETHUSDT': [(240, 'long', 'time'), (305, 'short', 'target'),
                    (370, 'long', 'time'),                        # entry in Q1, exit in Q2
                    (440, 'short', 'stop'), (510, 'long', 'inval'),
                    (NSLICE - 6, 'short', 'time')],               # last: open at END
        'SOLUSDT': [(240, 'short', 'follow'), (305, 'long', 'overlap'), (370, 'short', 'gap'), (420, 'long', 'cost'),
                    (470, 'short', 'quality'), (530, 'short', 'inval')]}
BG = HEAD + 385                                                   # a csv row on the SOLUSDT background inside the window


def tape(sym, plan=None, start=None):
    """Slice-indexed rows: index 0 = warm-up start; HEAD bars before it, TAIL bars after END (the cache runs on)."""
    P = LEVEL.get(sym, 120.0)
    t = np.arange(WARM_LO - HEAD * H4, HI + TAIL * H4, H4, dtype=np.int64)
    rows = [doji(P, 0.25 * P / 100.0)] * len(t)
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
    """TPC.run_symbol on the full warm-up+window slice (its own earliest b is WARM = 220 = W: no warm-up candidate)."""
    r = TPC.run_symbol(sliced(sym))
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
        r = TPC.run_symbol(sliced(s))
        assert [(g['side'], g['b']) for g in r['signals']] == \
            sorted([(sd, b) for b, sd, _ in PLAN[s]] + ([('long', 314)] if s == 'SOLUSDT' else []),
                   key=lambda x: x[1])
    _, sk, r = oracle('SOLUSDT')
    assert sorted(x['skip'] for x in sk) == ['cost_guard', 'gap_stop', 'overlap', 'quality']
    assert [(x['b'], x['consumed_by']) for x in r['blocked']] == [(241, 240)]
    tr, _, _ = oracle('ETHUSDT')
    assert [t['label'] for t in tr] == ['time', 'target', 'time', 'stop', 'invalidation', 'open_end']
    assert [t['label'] for t in oracle('SOLUSDT')[0]] == ['time', 'time', 'invalidation']


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
        with pytest.raises(R.RunnerError, match='^tpc: an explicit --window'):
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
    full = TPC.run_symbol(sliced('ETHUSDT'))
    assert TPC.WARM == W and all(g['b'] >= W for g in full['signals'])  # the module's own earliest b is START
    p = go(env)
    assert p['warmup']['signals_excluded'] == {'long': 0, 'short': 0}
    assert p['warmup']['bars'] == R.WARMUP_BARS >= 220
    assert all(x['t_signal'] >= p['window']['start']
               for x in p['trades'] + p['skips'] + p['blocked'] + p['open_at_end']['trades'])
    assert all(x['t_in'] >= p['window']['start'] for x in p['trades'])
    assert p['stats']['combined']['n'] == sum(sum(t['R_net'] is not None for t in oracle(s)[0]) for s in SYMS)


def test_warmup_candidate_neither_consumes_nor_holds_slot():
    """A pre-window candidate is removed before setup consumption and the slot logic: a pre-window trade still open at
    START cannot turn an in-window signal into an overlap, and its setup cannot block an in-window candidate."""
    d = CT.frame(pats=(250,))
    full = TPC.run_symbol(d)
    assert [g['b'] for g in full['signals']] == [230, 250] and [t['b'] for t in full['trades']] == [230]
    assert [s['skip'] for s in full['skips']] == ['overlap']
    ex = []
    with R.window_signals(231, ex):
        r = TPC.run_symbol(d)
    assert [t['b'] for t in r['trades']] == [250] and not r['skips'] and not r['blocked']
    assert [(g['side'], g['b']) for g in ex] == [('long', 230)]
    d2 = CT.frame(follow=(231,), a_b=10.0)
    assert [g['b'] for g in TPC.run_symbol(d2)['blocked']] == [231]          # same swing: blocked when 230 is a signal
    with R.window_signals(231, []):
        r2 = TPC.run_symbol(d2)
    assert [g['b'] for g in r2['signals']] == [231] and not r2['blocked']
    assert TPC.detect.__name__ == 'detect' and TPC.detect.__module__ == TPC.__name__     # wrapper restored


# ------------------------------------------------------------------ packet consistency + cost parity
def test_packet_fields_and_summarize_consistency(env):
    p = go(env)
    for k in ('format', 'commit', 'runner_sha256', 'window', 'warmup', 'universe', 'cache', 'costs', 'stats', 'decision',
              'trades', 'skips', 'blocked', 'open_at_end', 'packet_sha256', 'prereg', 'skips_by_reason', 'notes',
              'manifest'):
        assert k in p
    assert set(p['runner_sha256']) == {'run_tpc.py', 'trend_pullback_continuation.py', 'run_breadth_candidate.py',
                                       'run_vwaprev_candidate.py', 'backtest.py', 'strategies.py', 'pit_run.py',
                                       'run_r4.py', 'splits.py'}
    assert all(len(v) == 64 for v in p['runner_sha256'].values())
    assert all(len(f['sha256']) == 64 for f in p['cache']['files'].values())
    closed = [t for s in SYMS for t in oracle(s)[0] if t['R_net'] is not None]
    ref = TPC.summarize(closed)
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
    assert c['n'] == p['stats']['long']['n'] + p['stats']['short']['n'] == len(p['trades']) == 8
    assert c['exits'] == {'stop': 1, 'target': 1, 'invalidation': 2, 'time': 4}
    assert p['skips_by_reason'] == {'cost_guard': 1, 'quality': 1, 'gap_stop': 1, 'overlap': 1, 'no_next_bar': 0}
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
    assert p['candidate'] == TPC.CANDIDATE == 'trend_pullback_continuation.v0' and p['non_promotable'] is True
    assert p['family'] == TPC.FAMILY and p['format'] == 'zb-tpc-dev/1' and p['manifest'] is None
    assert p['rules']['target_r'] == '5/2' and p['rules']['cost_guard'] == '1/100' and p['rules']['cap_bars'] == 30
    assert p['notes']['cost'] == R.COST_NOTE and '0.29R' in R.COST_NOTE and '31' in R.COST_NOTE


def test_cost_note_number():
    """Worst case at the guard minimum: fees + slippage 0.14R, funding 31 closes ~0.155R, ~0.29R in total."""
    fees_slip = 2 * TPC.FEE + 2 * TPC.SLIP
    fund = 31 * TPC.FUND_PER_BAR
    total = (fees_slip + fund) / float(TPC.COST_GUARD)
    assert fees_slip / float(TPC.COST_GUARD) == pytest.approx(0.14) and total == pytest.approx(0.295)


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
    assert p['costs']['fee_taker'] == TPC.FEE and p['costs']['slip'] == TPC.SLIP
    assert p['costs']['fund_per_bar'] == TPC.FUND_PER_BAR


def test_detector_runner_parity_on_planted_tapes(env):
    """Codex 6231454794 item 3: every signal, skip, blocked candidate and trade of the module's run_symbol on the
    sliced tape appears in the packet, with the same bar times."""
    p = go(env)
    t_of = lambda s, i: SP.utc(WARM_LO + i * H4)
    sig, skp, blk, opn = set(), set(), set(), set()
    for s in SYMS:
        r = TPC.run_symbol(sliced(s))
        skp |= {(s, x['side'], x['skip'], t_of(s, x['b'])) for x in r['skips']}
        blk |= {(s, x['side'], t_of(s, x['b']), t_of(s, x['consumed_by'])) for x in r['blocked']}
        sig |= {(s, t['side'], t_of(s, t['b']), t_of(s, t['entry_i']), t['label']) for t in r['trades']
                if t['R_net'] is not None}
        opn |= {(s, t['side'], t_of(s, t['b'])) for t in r['trades'] if t['R_net'] is None}
    assert {(x['symbol'], x['side'], x['skip'], x['t_signal']) for x in p['skips']} == skp and len(skp) == 4
    assert {(x['symbol'], x['side'], x['t_signal'], x['consumed_by']) for x in p['blocked']} == blk and len(blk) == 1
    assert {(x['symbol'], x['side'], x['t_signal'], x['t_in'], x['label']) for x in p['trades']} == sig
    assert {(x['symbol'], x['side'], x['t_signal']) for x in p['open_at_end']['trades']} == opn and len(opn) == 1


def test_blocked_and_skips_reported(env):
    p = go(env)
    assert [(x['symbol'], x['skip']) for x in p['skips']] == [('SOLUSDT', 'overlap'), ('SOLUSDT', 'gap_stop'),
                                                               ('SOLUSDT', 'cost_guard'), ('SOLUSDT', 'quality')]
    st = p['stats']['combined']
    assert st['skips_by_symbol'] == {'SOLUSDT': {'cost_guard': 1, 'quality': 1, 'gap_stop': 1, 'overlap': 1,
                                                 'no_next_bar': 0}}
    assert st['blocked'] == len(p['blocked']) == p['stats']['long']['blocked'] + p['stats']['short']['blocked'] == 1
    for x in p['blocked']:
        assert x['consumed_by'] < x['t_signal']


def test_skips_by_symbol_counts_each_symbol_separately():
    skips = [dict(symbol='ETHUSDT', side='long', skip='overlap'), dict(symbol='SOLUSDT', side='short', skip='quality'),
             dict(symbol='SOLUSDT', side='long', skip='quality')]
    st = R.group_stats([], skips, [], [], ['2025Q1'], TPC.summarize([])['total'])
    assert st['skips_by_symbol'] == {'ETHUSDT': dict.fromkeys(R.SKIPS, 0) | {'overlap': 1},
                                     'SOLUSDT': dict.fromkeys(R.SKIPS, 0) | {'quality': 2}}
    assert st['skips'] == dict.fromkeys(R.SKIPS, 0) | {'overlap': 1, 'quality': 2}


def test_max_dd_uses_exit_order_not_entry_order():
    mk = lambda sym, t_in, t_out, r: dict(symbol=sym, side='long', quarter='2025Q1', t_in=t_in, t_out=t_out, R_net=r,
                                          R_gross=r, fee_R=0.0, slip_R=0.0, fund_R=0.0, hold_bars=1, mfe_r=0.0,
                                          mae_r=0.0, giveback_gross_r=0.0, giveback_net_r=0.0, label='time',
                                          gap_exit=False, lt_minus1=False)
    rows = [mk('ETHUSDT', 'a1', 'b3', -1.0), mk('SOLUSDT', 'a2', 'b1', 1.0), mk('SOLUSDT', 'a3', 'b2', -1.0)]
    st = R.group_stats(rows, [], [], [], ['2025Q1'], TPC.summarize(rows)['total'])
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


# ------------------------------------------------------------------ MFE / MAE / giveback on hand-built C3 trades
def _both(ov, **kw):
    d = CT.frame(ov, **kw)
    return [(d, CT.one(d)), (CT.flip(d), CT.one(CT.flip(d)))]


def test_excursions_time_exit_ignores_exit_bar_range():
    for d, t in _both({261: (1164.0, 1200.0, 1150.0, 1164.0)}):
        assert (t['label'], t['exit_i'], t['R_unit']) == ('time', 261, 22.0)
        x = R.excursions(d, t)
        assert x['mfe_r'] == pytest.approx(2 / 22) and x['mae_r'] == pytest.approx(2 / 22)   # bar 261 range not counted
        assert x['giveback_gross_r'] == pytest.approx(2 / 22 - t['R_gross'])
        assert x['giveback_net_r'] == pytest.approx(2 / 22 - t['R_net']) and 'giveback_r' not in x


def test_excursions_intrabar_stop_caps_at_stop():
    for d, t in _both({235: (1164.0, 1230.0, 1140.0, 1164.0)}):
        assert (t['label'], t['exit_i'], t['R_gross']) == ('stop', 235, -1.0)          # stop beats target same bar
        x = R.excursions(d, t)
        assert x['mae_r'] == pytest.approx(1.0) and x['mfe_r'] == pytest.approx(2 / 22)
        assert x['giveback_gross_r'] == pytest.approx(2 / 22 + 1.0)
        assert x['giveback_net_r'] == pytest.approx(2 / 22 - t['R_net']) and x['giveback_net_r'] > 2 / 22 + 1.0


def test_excursions_gap_target_counts_exit_open():
    for d, t in _both({235: (1225.0, 1226.0, 1224.0, 1225.0)}):
        assert (t['label'], t['gap_exit'], t['R_gross']) == ('target', True, 61 / 22)
        x = R.excursions(d, t)
        assert x['mfe_r'] == pytest.approx(61 / 22) and x['mae_r'] == pytest.approx(2 / 22)
        assert x['giveback_gross_r'] == pytest.approx(0.0, abs=1e-12)
        assert x['giveback_net_r'] == pytest.approx(61 / 22 - t['R_net'])


def test_excursions_invalidation_exit():
    for d, t in _both({233: (1164.0, 1166.0, 1146.0, 1148.0), 234: (1147.0, 1148.0, 1146.0, 1147.0)},
                      e50_ov={233: 1150.0}):
        assert (t['label'], t['exit_i'], t['exit']) == ('invalidation', 234, 1147.0 if t['side'] == 'long' else 1181.0)
        x = R.excursions(d, t)
        assert x['mfe_r'] == pytest.approx(2 / 22) and x['mae_r'] == pytest.approx(18 / 22)


def test_excursions_intrabar_stop_counts_exit_bar_open():
    """Intrabar stop whose exit bar opens beyond every earlier extreme: that open is on the path and sets the MFE."""
    for d, t in _both({235: (1175.0, 1176.0, 1140.0, 1150.0)}):
        assert (t['label'], t['exit_i'], t['R_gross'], t['gap_exit']) == ('stop', 235, -1.0, False)
        x = R.excursions(d, t)
        assert x['mfe_r'] == pytest.approx(11 / 22) and x['mae_r'] == pytest.approx(1.0)
        assert x['giveback_gross_r'] == pytest.approx(11 / 22 + 1.0)


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
    st = R.group_stats([], [], [], [], ['2025Q1', '2025Q2'], TPC.summarize([])['total'])
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
    st = R.group_stats(rows, [], [], [], ['2025Q1'], TPC.summarize(rows)['total'])
    assert st['positive_quarters'] == (1 if positive else 0)
    assert st['quarters']['2025Q1'] == 0.0                          # serialized value is rounded


def test_group_stats_feeds_raw_values_to_decision():
    """group_stats -> decision end to end: PF 1 + 1e-12 rounds to 1.0 in the packet but must still pass PF > 1."""
    mk = lambda q, r: dict(symbol='ETHUSDT', side='long', quarter=q, t_in=q, t_out=q, R_net=r, R_gross=r + 0.1,
                           fee_R=0.05, slip_R=0.05, fund_R=0.0, hold_bars=1, mfe_r=0.0, mae_r=0.0,
                           giveback_gross_r=0.0, giveback_net_r=0.0, label='time', gap_exit=False, lt_minus1=False)
    rows = [mk('2025Q1', 1.0 + 1e-12), mk('2025Q2', -1.0)]
    st = R.group_stats(rows, [], [], [], ['2025Q1', '2025Q2'], TPC.summarize(rows)['total'])
    d = R.decision(st, ['2025Q1', '2025Q2'])
    assert st['pf'] == 1.0 and d['pf_ok'] is True


def _xrow(q, net, gross=None, fee=0.0):
    return dict(symbol='ETHUSDT', side='long', quarter=q, t_in=q, t_out=q, R_net=net,
                R_gross=net + fee if gross is None else gross, fee_R=fee, slip_R=0.0, fund_R=0.0, hold_bars=1,
                mfe_r=0.0, mae_r=0.0, giveback_gross_r=0.0, giveback_net_r=0.0, label='time', gap_exit=False,
                lt_minus1=False)


def _xdec(rows, quarters=('2025Q1',)):
    st = R.group_stats(rows, [], [], [], list(quarters), TPC.summarize(rows)['total'])
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
    assert TPC.detect.__name__ == 'detect' and TPC.detect.__module__ == TPC.__name__     # wrapper restored


def test_packet_json_roundtrip(env):
    go(env)
    with open(env['tmp'] / 'p.json', encoding='utf-8') as f:
        p = json.load(f)
    assert p['candidate'] == TPC.CANDIDATE and p['non_promotable'] is True


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


# ------------------------------------------------------------------ TPC.detect restoration
def test_detect_restored_after_success():
    orig = TPC.detect
    ex = []
    with R.window_signals(231, ex):
        assert TPC.detect is not orig
        TPC.run_symbol(CT.frame())
    assert TPC.detect is orig and [g['b'] for g in ex] == [230]


def test_detect_restored_after_injected_failure(monkeypatch):
    def boom(df, side, ind=None):
        raise ZeroDivisionError('injected detector failure')
    monkeypatch.setattr(TPC, 'detect', boom)
    with pytest.raises(ZeroDivisionError, match='injected'):
        with R.window_signals(5, []):
            TPC.run_symbol(CT.frame())
    assert TPC.detect is boom


def test_detect_restored_after_run_failure(env, monkeypatch):
    orig = TPC.detect
    monkeypatch.setattr(TPC, 'summarize', lambda *a: (_ for _ in ()).throw(RuntimeError('late failure')))
    with pytest.raises(RuntimeError, match='late failure'):
        go(env)
    assert TPC.detect is orig and not os.path.exists(env['tmp'] / 'p.json')


# ------------------------------------------------------------------ internal aborts
@pytest.mark.parametrize('kind', ['target', 'gap'])
def test_pre_window_trade_raises_runner_error(env, monkeypatch, kind):
    """With the wrapper disabled and the module's earliest b moved before START, a pre-window signal aborts, whether it
    becomes a trade or only a skip."""
    monkeypatch.setattr(R, 'window_signals', lambda lo_i, ex: contextlib.nullcontext())
    monkeypatch.setattr(TPC, 'WARM', 150)
    plan = PLAN['ETHUSDT'] + [(160, 'long', kind)]
    tape('ETHUSDT', plan=plan).to_csv(os.path.join(env['cache'], 'ETHUSDT_4h.csv'), index=False)
    with pytest.raises(R.RunnerError, match='used a pre-window candidate' if kind == 'gap' else 'pre-window'):
        go(env)
    assert not os.path.exists(env['tmp'] / 'p.json')


def test_warmup_wrapper_drops_pre_window_signal(env, monkeypatch):
    """Same tape with the wrapper active: the pre-window signal is excluded and counted, nothing else changes."""
    monkeypatch.setattr(TPC, 'WARM', 150)
    plan = PLAN['ETHUSDT'] + [(160, 'long', 'target')]
    tape('ETHUSDT', plan=plan).to_csv(os.path.join(env['cache'], 'ETHUSDT_4h.csv'), index=False)
    p = go(env)
    assert p['warmup']['signals_excluded'] == {'long': 1, 'short': 0} and p['stats']['combined']['n'] == 8


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
    assert pr['written_before_data'] is True and pr['rule_text'] == TPC.__doc__
    assert 'entry..exit inclusive' in pr['funding'] and 'both sides' in pr['funding'] and 'never credited' in pr['funding']
    assert 'R_net' in pr['pf_and_quarters_use'] and pr['final_requires'] == R.FINAL_REQUIRES
    assert pr['cost_note'] == R.COST_NOTE
    assert len(pr['frozen_c3_parameters']) == 2 and len(pr['frozen_c3_lifecycle']) == 8
    assert len(pr['c3_draft_rules']) == 33
    assert pr['frozen_c3_lifecycle'][1] == (
        'A qualifying signal consumes its setup whether taken, no-next-bar, gap-skipped, cost/quality-skipped, or '
        'overlap-ignored. A candidate failing a signal gate does not consume. A candidate blocked by prior consumption '
        'does not extend it.')
    assert pr['frozen_decision'] == ('The frozen C1 rule, whole family, combined sides. A CANDIDATE needs both '
                                     'eligible-history CORE-10 and CORE-20; otherwise PARK.')
    assert pr['exclusion_rule'] == R.EXCLUSION_RULE and '6229469658' in pr['exclusion_rule']
    assert '6230952961' in pr['source'] and '6230936151' in pr['source']
    assert set(p['skips_by_reason']) == set(R.SKIPS)
    assert p['skips_by_reason'] == {k: sum(1 for x in p['skips'] if x['skip'] == k) for k in R.SKIPS}
    assert p['skips_by_reason'] == p['stats']['combined']['skips']
    for g in ('long', 'short', 'combined'):
        st = p['stats'][g]
        assert st['gap_exit_by_label'] == {k: sum(1 for x in p['trades'] if x['label'] == k and x['gap_exit']
                                                  and (g == 'combined' or x['side'] == g)) for k in R.LABELS}


def test_prereg_frozen_strings_match_codex_source():
    """Codex 6230952961 / Build 6230936151 verbatim (list markers and ** removed): spot-check the load-bearing ones."""
    s, lc, dr = R.FROZEN_C3_PARAMETERS, R.FROZEN_C3_LIFECYCLE, R.C3_DRAFT_RULES
    assert s[0] == ('4h; EMA20/EMA50; slope ≥0.5·ATR over b−7→b−1; trend persistence at least 10/12; pullback depth '
                    '≥1.0·ATR; EMA20 touch zone +0.25·ATR; stop buffer 0.2·ATR[b]; R cap ≤3.0·ATR[b]; cost guard '
                    'R/actual entry ≥1.0%; target 2.5R; time decision b+30 / exit open b+31.')
    assert lc[3] == ('One slot per symbol. A signal at close x−1 is ignored/consumed when the existing trade exits at '
                     'open x. An open_end trade owns the slot forever within that dataset.')
    assert lc[7] == 'Costs/funding use the accepted C2 parity convention.'
    assert dr[0] == '4h closed bars only. Signal bar `b`.'
    assert 'Cost guard: skip unless R / entry ≥ 1.0%. Fees of 2×5 bps plus slippage of 2×2 bps cost at most 0.14 R ' \
           'before funding.' in dr
    assert 'C[b] < H (the trigger must come before the breakout, which keeps this distinct from C2).' in dr
    assert all('**' not in x for x in s + lc + dr + (R.FROZEN_DECISION,))


# ------------------------------------------------------------------ --check-coverage
def test_check_coverage_reports_late_listing_and_nothing_past_end(env, monkeypatch):
    path = os.path.join(env['cache'], 'SOLUSDT_4h.csv')
    d = pd.read_csv(path, encoding='utf-8')
    d.iloc[HEAD + 40:].to_csv(path, index=False)                   # listed 40 bars after the warm-up start
    calls = []
    monkeypatch.setattr(TPC, 'run_symbol', lambda *a, **k: calls.append('run'))
    monkeypatch.setattr(TPC, 'detect', lambda *a, **k: calls.append('detect'))
    monkeypatch.setattr(R.VC, 'clean_commit', lambda *a, **k: calls.append('commit'))
    monkeypatch.setattr(TPC.ST, 'indicators', lambda *a, **k: calls.append('ind'))
    monkeypatch.setattr(TPC.ST, 'ema', lambda *a, **k: calls.append('ema'))
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
    assert p['stats']['combined']['n'] == 8                         # only ETH / SOL carry planted setups
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
    real = TPC.summarize

    def racing(trades):
        with open(out, 'wb') as f:                                  # appears after the initial exists() check
            f.write(b'ORIGINAL')
        return real(trades)
    monkeypatch.setattr(TPC, 'summarize', racing)
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
    real = TPC.summarize
    state = {'inner': None}

    def first_writer_pauses(trades):
        if state['inner'] is None:
            state['inner'] = 'running'
            monkeypatch.setattr(TPC, 'summarize', real)
            state['inner'] = R.run(out=str(out), cache=env['cache'], window=WINDOW, symbols=SYMS, repo=env['repo'])
        return real(trades)
    monkeypatch.setattr(TPC, 'summarize', first_writer_pauses)
    with pytest.raises(R.RunnerError, match='created during the run'):
        go(env)
    with open(out, 'rb') as f:
        body = f.read()
    assert json.loads(body)['packet_sha256'] == state['inner']['packet_sha256']
    assert sorted(os.listdir(env['tmp'])) == sorted(['candles', 'repo', 'p.json'])


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
    assert go(env)['stats']['combined']['n'] == 8


def test_ohlc_problems_helper_counts():
    d = pd.DataFrame(dict(t=pd.to_datetime([0, H4, 2 * H4, 3 * H4], unit='ms'), o=[1.0, 1.0, 1.0, 1.0],
                          h=[2.0, 0.5, 2.0, 2.0], l=[0.5, 0.4, 1.5, 0.5], c=[1.5, 0.45, 1.6, 1.5]))
    assert R.ohlc_problems(d) == {'h_below_max_oc': 1, 'l_above_min_oc': 1, 'first_bad': SP.utc(H4)}
    assert R.ohlc_problems(d.iloc[[0, 3]]) == {}


def test_ohlc_equality_bars_are_valid(env):
    _poke(env, IN_WIN, o=150.0, h=150.0, l=150.0, c=150.0)         # flat bar: h == l == o == c
    _poke(env, IN_WIN + 1, o=150.0, h=151.0, l=149.0, c=151.0)     # h == close
    _poke(env, IN_WIN + 2, o=149.0, h=151.0, l=149.0, c=150.0)     # l == open
    assert go(env)['stats']['combined']['n'] == 8


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
        d.loc[k, ['o', 'h', 'l', 'c']] = np.array([P, P + 500.0, P - 0.5, P + 400.0])      # huge range + breakouts
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
        assert go(env)['stats']['combined']['n'] == 8
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
def test_breakout_on_first_in_window_bar_is_counted(env):
    """A qualifying trigger exactly at b == W (the first in-window bar) is an in-window signal: traded, not excluded."""
    d = tape('ETHUSDT', plan=[(W, 'long', 'time')])
    d.to_csv(os.path.join(env['cache'], 'ETHUSDT_4h.csv'), index=False)
    s = VC.slice_frame(d, WARM_LO, HI)
    assert [g['b'] for g in TPC.run_symbol(s)['trades']] == [W]
    ex = []
    with R.window_signals(W, ex):
        r = TPC.run_symbol(s)
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
