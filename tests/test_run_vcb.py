"""tools/research/run_vcb.py - the C2 vol_compression_breakout.v0 dev-run CLI.

Synthetic only: a temp 4h candle cache (app._get_candles CSV format) with planted compression boxes on a doji
background + a temp git repo for the clean-tree check (the test_vwaprev_candidate_runner fixtures); no real cache, store
or data folder is read. Mirrors tests/test_run_lsfvg.py (accepted C1 runner) protection by protection, plus the
eligibility-by-history exclusion ledger."""
import contextlib
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_vcb as R                                                                         # noqa: E402
import vol_compression_breakout as VCB                                                      # noqa: E402
import run_vwaprev_candidate as VC                                                          # noqa: E402
import splits as SP                                                                         # noqa: E402
import test_vwaprev_candidate_runner as VT                                                  # noqa: E402
import test_vol_compression_breakout as CT                                                  # noqa: E402

H4 = R.TF_MS
WINDOW = VT.WINDOW                                       # 2025-03-03 / 2025-05-05, inside the development window
LO, HI = (SP.parse_utc(x) for x in WINDOW.split('/'))
WARM_LO = LO - R.WARMUP_BARS * H4
HEAD, TAIL = 12, 40
W = R.WARMUP_BARS                                        # slice index of the first in-window bar
NSLICE = W + (HI - LO) // H4
SYMS = ['ETHUSDT', 'SOLUSDT']
Q2 = W + (SP.parse_utc('2025-04-01T00:00:00Z') - LO) // H4
LEVEL = {'ETHUSDT': 100.0, 'SOLUSDT': 150.0}


def doji(p, r=1.0):
    return (p, p + r, p - r, p)


def block(b, P, side='long', kind='time'):
    """A planted long compression box (bars b-12..b-1 flat), breakout at b, then the trade path; short = price mirror
    around P. Background around it is dojis (body 0: never a breakout)."""
    rows = {k: (P, P + 0.05, P - 0.05, P) for k in range(b - 12, b)}
    E = P + 3.0
    rows[b] = (P, P + 3.05, P - 0.05, E)
    for k in range(b + 1, b + 20):
        rows[k] = doji(E, 0.5)
    if kind == 'target':
        rows[b + 3] = (E, E + 12.0, E - 0.5, E + 10.0)
        rows.update({k: doji(E + 10.0, 0.5) for k in range(b + 4, b + 20)})
    elif kind == 'stop':
        rows[b + 3] = (E, E + 0.5, P - 1.0, P - 0.5)
        rows.update({k: doji(P - 0.5, 0.5) for k in range(b + 4, b + 20)})
    elif kind == 'inval':
        rows[b + 3] = (E, E + 0.5, P - 0.02, P)                  # close below Hb, low above the stop
        rows.update({k: doji(P, 0.5) for k in range(b + 4, b + 20)})
    elif kind == 'overlap':
        rows[b + 13] = (E, E + 3.0, E - 0.5, E + 2.9)            # second box b+1..b+12 breaks while the trade is open
        rows.update({k: doji(E + 2.9, 0.5) for k in range(b + 14, b + 20)})
    elif kind == 'gap':
        rows.update({k: doji(P - 1.0, 0.5) for k in range(b + 1, b + 20)})       # opens below the stop
    elif kind == 'cost':
        rows[b] = (P, P + 0.65, P - 0.05, P + 0.6)               # R ~ 0.69 < 0.84% of the entry
        rows.update({k: doji(P + 0.6, 0.3) for k in range(b + 1, b + 20)})
    if side == 'short':
        rows = {k: (2 * P - o, 2 * P - lw, 2 * P - h, 2 * P - c) for k, (o, h, lw, c) in rows.items()}
    return rows


PLAN = {'ETHUSDT': [(130, 'short', 'time'),                       # warm-up signal: flat (and unconsumed) by START
                    (240, 'long', 'time'), (300, 'short', 'target'),
                    (Q2 - 5, 'long', 'time'),                     # entry in Q1, exit in Q2
                    (460, 'short', 'stop'), (530, 'long', 'inval'),
                    (NSLICE - 6, 'short', 'time')],               # last: open at END
        'SOLUSDT': [(250, 'short', 'time'), (320, 'long', 'overlap'), (400, 'short', 'gap'), (470, 'long', 'cost'),
                    (540, 'short', 'inval')]}


def tape(sym, plan=None, start=None):
    """Slice-indexed rows: index 0 = warm-up start; HEAD bars before it, TAIL bars after END (the cache runs on)."""
    P = LEVEL.get(sym, 120.0)
    t = np.arange(WARM_LO - HEAD * H4, HI + TAIL * H4, H4, dtype=np.int64)
    rows = [doji(P)] * len(t)
    plan = list(PLAN.get(sym, []) if plan is None else plan)
    if sym == 'ETHUSDT' and plan:
        plan.append((NSLICE + 15, 'long', 'target'))               # a setup after END: never read
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
    """VCB.run_symbol on the full warm-up+window slice, in-window signals only (valid here: the warm-up trade is flat
    and its consumption interval over before START)."""
    r = VCB.run_symbol(sliced(sym))
    assert all(g['b'] + VCB.CONSUME <= W and (t['exit_i'] or 0) < W
               for g in r['signals'] if g['b'] < W for t in r['trades'] if t['b'] == g['b'])
    return [t for t in r['trades'] if t['b'] >= W], [s for s in r['skips'] if s['b'] >= W], r


# ------------------------------------------------------------------ the planted tape itself
def test_planted_tape_fires_as_planned():
    for s in SYMS:
        r = VCB.run_symbol(sliced(s))
        assert [(g['side'], g['b']) for g in r['signals']] == \
            sorted([(sd, b) for b, sd, _ in PLAN[s]] + ([('long', 333)] if s == 'SOLUSDT' else []),
                   key=lambda x: x[1])
    _, sk, r = oracle('SOLUSDT')
    assert sorted(x['skip'] for x in sk) == ['cost_guard', 'gap_stop', 'overlap']
    tr, _, _ = oracle('ETHUSDT')
    assert [t['label'] for t in tr] == ['time', 'target', 'time', 'stop', 'invalidation', 'open_end']


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


def test_window_required_and_warmup_guard(env):
    with pytest.raises(R.RunnerError, match='explicit --window'):
        go(env, window=None)
    with pytest.raises(R.RunnerError, match='warm-up'):
        go(env, window='2021-01-11T00:00:00Z/2021-03-01T00:00:00Z')


def test_dirty_tree_refused(env, tmp_path):
    with pytest.raises(R.RunnerError, match='dirty git tree'):
        go(env, repo=VT.git_repo(str(tmp_path / 'dirty'), dirty=True))


def test_never_overwrites(env):
    go(env)
    with pytest.raises(R.RunnerError, match='never overwritten'):
        go(env)


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
    with pytest.raises(R.RunnerError, match='INCOMPLETE'):
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
    full = VCB.run_symbol(sliced('ETHUSDT'))
    pre = [t for t in full['trades'] if t['b'] < W]
    assert len(pre) == 1 and pre[0]['exit_i'] < W                 # the planted warm-up trade exists and is flat by START
    p = go(env)
    assert p['warmup']['signals_excluded'] == {'long': 0, 'short': 1}
    assert p['warmup']['bars'] == R.WARMUP_BARS >= 220
    assert all(x['t_signal'] >= p['window']['start']
               for x in p['trades'] + p['skips'] + p['blocked'] + p['open_at_end']['trades'])
    assert all(x['t_in'] >= p['window']['start'] for x in p['trades'])
    assert p['stats']['combined']['n'] == sum(sum(t['R_net'] is not None for t in oracle(s)[0]) for s in SYMS)


def test_warmup_candidate_neither_consumes_nor_holds_slot():
    """A warm-up candidate is removed before box consumption and the slot logic: a warm-up trade still open at START
    cannot turn an in-window signal into an overlap, and its box cannot block an in-window candidate."""
    d = CT._slot_frame(145)
    full = VCB.run_symbol(d)
    assert [g['b'] for g in full['signals']] == [130, 145] and [t['b'] for t in full['trades']] == [130]
    assert [s['skip'] for s in full['skips']] == ['overlap']
    ex = []
    with R.window_signals(131, ex):
        r = VCB.run_symbol(d)
    assert [t['b'] for t in r['trades']] == [145] and not r['skips'] and not r['blocked']
    assert [(g['side'], g['b']) for g in ex] == [('long', 130)]
    d2 = CT.frame({131: (97.5, 98.0, 97.0, 97.5), 142: CT.CAND}, aft=CT.DEF, atr_ov=CT.cand_at({}, 142))
    assert [g['b'] for g in VCB.run_symbol(d2)['blocked']] == [142]           # b+12: blocked when 130 is a signal
    with R.window_signals(131, []):
        r2 = VCB.run_symbol(d2)
    assert [g['b'] for g in r2['signals']] == [142] and not r2['blocked']
    assert VCB.detect.__name__ == 'detect' and VCB.detect.__module__ == VCB.__name__     # wrapper restored


# ------------------------------------------------------------------ packet consistency + cost parity
def test_packet_fields_and_summarize_consistency(env):
    p = go(env)
    for k in ('format', 'commit', 'runner_sha256', 'window', 'warmup', 'universe', 'cache', 'costs', 'stats', 'decision',
              'trades', 'skips', 'blocked', 'open_at_end', 'packet_sha256', 'prereg', 'skips_by_reason'):
        assert k in p
    assert set(p['runner_sha256']) == {'run_vcb.py', 'vol_compression_breakout.py', 'run_breadth_candidate.py',
                                       'run_vwaprev_candidate.py', 'backtest.py', 'strategies.py', 'pit_run.py',
                                       'run_r4.py', 'splits.py'}
    assert all(len(v) == 64 for v in p['runner_sha256'].values())
    assert all(len(f['sha256']) == 64 for f in p['cache']['files'].values())
    closed = [t for s in SYMS for t in oracle(s)[0] if t['R_net'] is not None]
    ref = VCB.summarize(closed)
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
    assert p['skips_by_reason'] == {'cost_guard': 1, 'gap_stop': 1, 'overlap': 1, 'no_next_bar': 0}
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
    assert p['decision']['tier_verdict'] in ('CANDIDATE', 'PARK') and 'verdict' not in p['decision']
    assert p['decision']['final_requires'] == R.FINAL_REQUIRES
    assert p['candidate'] == VCB.CANDIDATE and p['non_promotable'] is True


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
    assert p['costs']['fee_taker'] == VCB.FEE and p['costs']['slip'] == VCB.SLIP
    assert p['costs']['fund_per_bar'] == VCB.FUND_PER_BAR


def test_blocked_and_skips_reported(env):
    p = go(env)
    assert [(x['symbol'], x['skip']) for x in p['skips']] == [('SOLUSDT', 'overlap'), ('SOLUSDT', 'gap_stop'),
                                                               ('SOLUSDT', 'cost_guard')]
    st = p['stats']['combined']
    assert st['skips_by_symbol'] == {'SOLUSDT': {'cost_guard': 1, 'gap_stop': 1, 'overlap': 1, 'no_next_bar': 0}}
    assert st['blocked'] == len(p['blocked']) == p['stats']['long']['blocked'] + p['stats']['short']['blocked']
    for x in p['blocked']:
        assert x['consumed_by'] < x['t_signal']


def test_max_dd_in_exit_order(env):
    p = go(env)
    rows = sorted(p['trades'], key=lambda x: (x['t_out'], x['t_in'], x['symbol'], x['side']))
    cum = np.cumsum([0.0] + [x['R_net'] for x in rows])
    assert p['stats']['combined']['max_dd_R'] == pytest.approx(float((np.maximum.accumulate(cum) - cum).max()), abs=1e-6)


# ------------------------------------------------------------------ MFE / MAE / giveback on hand-built C2 trades
def _both(ov):
    d = CT.frame(ov)
    return [(d, CT.one(d)), (CT.flip(d), CT.one(CT.flip(d)))]


def test_excursions_time_exit_ignores_exit_bar_range():
    for d, t in _both({135: (105.0, 110.0, 100.0, 105.0), 149: (105.0, 120.0, 90.0, 105.0)}):
        assert (t['label'], t['exit_i'], t['R_unit']) == ('time', 149, 8.0)
        x = R.excursions(d, t)
        assert x['mfe_r'] == pytest.approx(5 / 8) and x['mae_r'] == pytest.approx(5 / 8)   # bar 149 range not counted
        assert x['giveback_gross_r'] == pytest.approx(5 / 8 - t['R_gross'])
        assert x['giveback_net_r'] == pytest.approx(5 / 8 - t['R_net']) and 'giveback_r' not in x


def test_excursions_intrabar_stop_caps_at_stop():
    for d, t in _both({135: (105.0, 130.0, 96.0, 105.0)}):
        assert (t['label'], t['exit_i'], t['R_gross']) == ('stop', 135, -1.0)          # stop beats target same bar
        x = R.excursions(d, t)
        assert x['mae_r'] == pytest.approx(1.0) and x['mfe_r'] == pytest.approx(1 / 8)
        assert x['giveback_gross_r'] == pytest.approx(1 / 8 + 1.0)
        assert x['giveback_net_r'] == pytest.approx(1 / 8 - t['R_net']) and x['giveback_net_r'] > 1 / 8 + 1.0


def test_excursions_gap_target_counts_exit_open():
    for d, t in _both({135: (130.0, 131.0, 129.0, 130.0)}):
        assert (t['label'], t['gap_exit'], t['R_gross']) == ('target', True, 25 / 8)
        x = R.excursions(d, t)
        assert x['mfe_r'] == pytest.approx(25 / 8) and x['mae_r'] == pytest.approx(1 / 8)
        assert x['giveback_gross_r'] == pytest.approx(0.0, abs=1e-12)
        assert x['giveback_net_r'] == pytest.approx(25 / 8 - t['R_net'])


def test_excursions_invalidation_exit():
    for d, t in _both({133: (105.0, 107.0, 100.0, 100.5), 134: (99.0, 100.0, 98.0, 99.0)}):
        assert (t['label'], t['exit_i'], t['exit']) == ('invalidation', 134, 99.0 if t['side'] == 'long' else 101.0)
        x = R.excursions(d, t)
        assert x['mfe_r'] == pytest.approx(2 / 8) and x['mae_r'] == pytest.approx(6 / 8)


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
    st = R.group_stats([], [], [], [], ['2025Q1', '2025Q2'], VCB.summarize([])['total'])
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
    st = R.group_stats(rows, [], [], [], ['2025Q1'], VCB.summarize(rows)['total'])
    assert st['positive_quarters'] == (1 if positive else 0)
    assert st['quarters']['2025Q1'] == 0.0                          # serialized value is rounded


def test_decision_uses_raw_not_rounded_group_fields():
    c = dict(n=10, wins=5, losses=5, positive_quarters=3, pf=1.0, gross_R=3.0, cost_R=1.0,
             _raw=dict(pf=1.0 + EPS, gross_R=3.0 - 1e-13, cost_R=1.0))
    d = R.decision(c, ['Q1', 'Q2', 'Q3'])
    assert d['pf_ok'] is True and d['cost_ok'] is False


# ------------------------------------------------------------------ determinism
def test_determinism(env):
    a, b = go(env, out='a.json'), go(env, out='b.json')
    with open(env['tmp'] / 'a.json', 'rb') as f, open(env['tmp'] / 'b.json', 'rb') as g:
        assert f.read() == g.read()
    assert a['packet_sha256'] == b['packet_sha256']
    assert VCB.detect.__name__ == 'detect' and VCB.detect.__module__ == VCB.__name__     # wrapper restored


def test_packet_json_roundtrip(env):
    go(env)
    with open(env['tmp'] / 'p.json', encoding='utf-8') as f:
        p = json.load(f)
    assert p['candidate'] == VCB.CANDIDATE and p['non_promotable'] is True


# ------------------------------------------------------------------ VCB.detect restoration
def test_detect_restored_after_success():
    orig = VCB.detect
    ex = []
    with R.window_signals(131, ex):
        assert VCB.detect is not orig
        VCB.run_symbol(CT.frame())
    assert VCB.detect is orig and [g['b'] for g in ex] == [130]


def test_detect_restored_after_injected_failure(monkeypatch):
    def boom(df, side, atr=None):
        raise ZeroDivisionError('injected detector failure')
    monkeypatch.setattr(VCB, 'detect', boom)
    with pytest.raises(ZeroDivisionError, match='injected'):
        with R.window_signals(5, []):
            VCB.run_symbol(CT.frame())
    assert VCB.detect is boom


def test_detect_restored_after_run_failure(env, monkeypatch):
    orig = VCB.detect
    monkeypatch.setattr(VCB, 'summarize', lambda *a: (_ for _ in ()).throw(RuntimeError('late failure')))
    with pytest.raises(RuntimeError, match='late failure'):
        go(env)
    assert VCB.detect is orig and not os.path.exists(env['tmp'] / 'p.json')


# ------------------------------------------------------------------ internal aborts
def test_pre_window_trade_raises_runner_error(env, monkeypatch):
    monkeypatch.setattr(R, 'window_signals', lambda lo_i, ex: contextlib.nullcontext())
    with pytest.raises(R.RunnerError, match='pre-window'):
        go(env)
    assert not os.path.exists(env['tmp'] / 'p.json')


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
    assert pr['written_before_data'] is True and pr['rule_text'] == VCB.__doc__
    assert 'entry..exit inclusive' in pr['funding'] and 'both sides' in pr['funding'] and 'never credited' in pr['funding']
    assert 'R_net' in pr['pf_and_quarters_use'] and pr['final_requires'] == R.FINAL_REQUIRES
    assert len(pr['frozen_c2_semantics']) == 13 and len(pr['c2_rulings']) == 4
    assert pr['c2_rulings'] == [
        'Accepted: consumption is per symbol across both sides.',
        'Accepted: a candidate inside the active b..b+12 consumption interval is blocked and does not extend it. Only '
        'an otherwise qualifying, unblocked signal starts a new 13-bar interval.',
        'Accepted: ATR[b] must be finite. With valid positive OHLC and the breakout-body rule, computed ATR[b] is '
        'positive; the later runner will independently refuse invalid OHLC before detection.',
        'Accepted: funding is the C1/`backtest.run` parity convention, charged as cost on closes e..x inclusive and '
        'disclosed in the packet.']
    assert pr['frozen_c2_semantics'][6] == (
        'A box is consumed only after all compression + breakout tests pass, whether the resulting entry is taken, '
        'gap-skipped, cost-skipped, or overlap-ignored. Failed filters do not consume. Next eligible box candidate is '
        'b′≥b+13.')
    assert pr['frozen_c2_semantics'][3].startswith('Filters use ATR[b−1]. The stop buffer intentionally uses ATR[b]')
    assert pr['frozen_decision'] == ('The development decision rule remains the three frozen C1 tests, whole '
                                     'family/combined sides, on both eligible-history tiers; otherwise PARK.')
    assert pr['exclusion_rule'] == R.EXCLUSION_RULE and '6229469658' in pr['exclusion_rule']
    assert set(p['skips_by_reason']) == set(R.SKIPS)
    assert p['skips_by_reason'] == {k: sum(1 for x in p['skips'] if x['skip'] == k) for k in R.SKIPS}
    assert p['skips_by_reason'] == p['stats']['combined']['skips']
    for g in ('long', 'short', 'combined'):
        st = p['stats'][g]
        assert st['gap_exit_by_label'] == {k: sum(1 for x in p['trades'] if x['label'] == k and x['gap_exit']
                                                  and (g == 'combined' or x['side'] == g)) for k in R.LABELS}


def test_prereg_frozen_strings_match_codex_source():
    """The 13 frozen bullets are Codex 6229577171 verbatim (markdown removed): spot-check the load-bearing ones."""
    s = R.FROZEN_C2_SEMANTICS
    assert s[0].startswith('4h, not 1h. Keep the 3R target, 18-bar decision cap / open b+19 exit')
    assert s[5].endswith('No same-open exit/re-entry.')
    assert s[10] == ('Costs are C1’s frozen model: taker fee 5 bps per fill, slippage 2 bps per fill, funding '
                     '5e−5 per held 4h bar, always cost.')
    assert s[12] == 'Long invalidation is C[k] < Hb; short is C[k] > Lb. Equality does not invalidate. First eligible k=b+1.'
    assert all('**' not in x for x in s + R.C2_RULINGS)


# ------------------------------------------------------------------ --check-coverage
def test_check_coverage_reports_late_listing_and_nothing_past_end(env, monkeypatch):
    path = os.path.join(env['cache'], 'SOLUSDT_4h.csv')
    d = pd.read_csv(path, encoding='utf-8')
    d.iloc[HEAD + 40:].to_csv(path, index=False)                   # listed 40 bars after the warm-up start
    calls = []
    monkeypatch.setattr(VCB, 'run_symbol', lambda *a, **k: calls.append('run'))
    monkeypatch.setattr(VCB, 'detect', lambda *a, **k: calls.append('detect'))
    monkeypatch.setattr(R.VC, 'clean_commit', lambda *a, **k: calls.append('commit'))
    monkeypatch.setattr(VCB.ST, 'indicators', lambda *a, **k: calls.append('ind'))
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
    assert u['tier'] is None and u['tier_label'] is None and u['exclusion_rule'] == R.EXCLUSION_RULE
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
    with pytest.raises(R.RunnerError, match='not provably late-listed'):
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


def test_tier_run_with_late_exclusion_ledger(env):
    static = VC.frozen_universe()
    for s in static['core_10']:
        if s not in SYMS:
            _add(env, s, start=40 if s == 'HYPEUSDT' else None)
    with pytest.raises(R.RunnerError, match='INCOMPLETE'):
        go(env, symbols=None, tier='CORE-10')
    p = go(env, symbols=None, tier='CORE-10', exclude_late=['HYPEUSDT'])
    u = p['universe']
    assert u['requested'] == static['core_10'] and u['run'] == [s for s in static['core_10'] if s != 'HYPEUSDT']
    assert len(u['run']) == 9 and u['tier'] == 'CORE-10' and u['tier_label'] == 'CORE-10-ELIGIBLE-9'
    assert [x['symbol'] for x in u['excluded']] == ['HYPEUSDT']
    ex = [x['symbol'] for x in u['excluded']]                       # Cowork (b): run + excluded == requested, disjoint
    assert sorted(u['run'] + ex) == sorted(u['requested']) and not set(u['run']) & set(ex)
    assert set(p['cache']['completeness']) == set(u['run'])         # exactly the simulated symbols
    assert p['stats']['combined']['n'] == 8                         # only ETH / SOL carry planted boxes


def test_cli_exclude_late(env, capsys, monkeypatch):
    _cli_repo(env, monkeypatch)
    _add(env, 'HYPEUSDT', start=40)
    out = str(env['tmp'] / 'cli.json')
    assert R.main(['--cache', env['cache'], '--window', WINDOW, '--symbols', ','.join(SYMS + ['HYPEUSDT']),
                   '--exclude-late', 'HYPEUSDT', '--out', out]) == 0
    assert 'run 2 excluded 1' in capsys.readouterr().out
    with open(out, encoding='utf-8') as f:
        assert json.load(f)['universe']['excluded'][0]['symbol'] == 'HYPEUSDT'


# ------------------------------------------------------------------ write races
def test_out_created_during_run_is_never_replaced(env, monkeypatch):
    out = env['tmp'] / 'p.json'
    real = VCB.summarize

    def racing(trades):
        with open(out, 'wb') as f:                                  # appears after the initial exists() check
            f.write(b'ORIGINAL')
        return real(trades)
    monkeypatch.setattr(VCB, 'summarize', racing)
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
    real = VCB.summarize
    state = {'inner': None}

    def first_writer_pauses(trades):
        if state['inner'] is None:
            state['inner'] = 'running'
            monkeypatch.setattr(VCB, 'summarize', real)
            state['inner'] = R.run(out=str(out), cache=env['cache'], window=WINDOW, symbols=SYMS, repo=env['repo'])
        return real(trades)
    monkeypatch.setattr(VCB, 'summarize', first_writer_pauses)
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


IN_WIN = HEAD + W + 60                                              # a csv row inside [START, END) (background doji at 150)


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


def test_no_file_after_mid_run_crash(env, monkeypatch):
    monkeypatch.setattr(R, 'excursions', lambda *a: (_ for _ in ()).throw(RuntimeError('injected mid-run')))
    with pytest.raises(RuntimeError, match='injected mid-run'):
        go(env)
    assert sorted(os.listdir(env['tmp'])) == sorted(['candles', 'repo'])


def test_post_end_spike_does_not_leak(env):
    """(d) rows at / after END that would change ATR, the median and the signals if read leave every result unchanged;
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
