"""tools/research/run_lsfvg.py - the C1 liquidity_sweep_fvg.v0 dev-run CLI.

Synthetic only: a temp 4h candle cache (app._get_candles CSV format) with planted setups + a temp git repo for the
clean-tree check (the test_vwaprev_candidate_runner fixtures); no real cache, store or data folder is read."""
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_lsfvg as R                                                                       # noqa: E402
import liquidity_sweep_fvg as LS                                                            # noqa: E402
import run_vwaprev_candidate as VC                                                          # noqa: E402
import splits as SP                                                                         # noqa: E402
import test_vwaprev_candidate_runner as VT                                                  # noqa: E402
import test_liquidity_sweep_fvg as LT                                                       # noqa: E402

H4 = R.TF_MS
WINDOW = VT.WINDOW                                       # 2025-03-03 / 2025-05-05, inside the development window
LO, HI = (SP.parse_utc(x) for x in WINDOW.split('/'))
WARM_LO = LO - R.WARMUP_BARS * H4
HEAD, TAIL = 12, 40
W = R.WARMUP_BARS                                        # slice index of the first in-window bar
NSLICE = W + (HI - LO) // H4
SYMS = ['ETHUSDT', 'SOLUSDT']
Q2 = W + (SP.parse_utc('2025-04-01T00:00:00Z') - LO) // H4

DEF = (100.0, 101.0, 99.0, 100.0)
REST = (92.0, 93.0, 91.0, 92.0)
PIV = (100.0, 110.0, 99.0, 100.0)
SWEEP = (104.0, 112.0, 103.0, 105.0)
BEAR = (104.0, 105.0, 94.0, 95.0)
FVG = (95.0, 96.0, 90.0, 92.0)
STOP_BAR = (92.0, 114.0, 91.0, 92.0)
TARGET_BAR = (92.0, 93.0, 49.0, 50.0)


def block(n, side='short', kind='time'):
    """Short setup with its FVG bar at slice index n (pivot n-12, sweep n-2); long = price mirror around 200."""
    rows = {n - 12: PIV, n - 2: SWEEP, n - 1: BEAR, n: FVG}
    for k in range(n + 1, n + 16):
        rows[k] = REST
    if kind == 'stop':
        rows[n + 3] = STOP_BAR
    elif kind == 'target':
        rows[n + 3] = TARGET_BAR
    if side == 'long':
        rows = {k: (200 - o, 200 - lw, 200 - h, 200 - c) for k, (o, h, lw, c) in rows.items()}
    return rows


PLAN = {'ETHUSDT': [(150, 'short', 'time'),                       # warm-up signal: trade closes before START
                    (240, 'short', 'time'), (280, 'long', 'target'), (320, 'short', 'stop'),
                    (Q2 - 5, 'short', 'time'),                    # entry in Q1, exit in Q2
                    (430, 'short', 'target'), (470, 'long', 'stop'), (NSLICE - 6, 'short', 'time')],   # last: open at END
        'SOLUSDT': [(260, 'long', 'time'), (360, 'short', 'target'), (500, 'short', 'stop')]}
OFF = {'ETHUSDT': 0.0, 'SOLUSDT': 50.0}


def tape(sym):
    """Slice-indexed rows: index 0 = warm-up start; HEAD bars before it, TAIL bars after END (the cache runs on)."""
    t = np.arange(WARM_LO - HEAD * H4, HI + TAIL * H4, H4, dtype=np.int64)
    rows = [DEF] * len(t)
    plan = list(PLAN.get(sym, []))
    if sym == 'ETHUSDT':
        plan.append((NSLICE + 15, 'short', 'target'))                # a setup after END: never read
    for n, side, kind in plan:
        for k, r in block(n, side, kind).items():
            if 0 <= k + HEAD < len(t):
                rows[k + HEAD] = r
    a = np.array(rows) + OFF.get(sym, 0.0)
    return pd.DataFrame(dict(t=t.astype(float), o=a[:, 0], h=a[:, 1], l=a[:, 2], c=a[:, 3], v=1000.0, qv=100000.0))


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
    d = tape(sym)
    return VC.slice_frame(d, WARM_LO, HI)


def oracle(sym):
    """LS.run_symbol on the full warm-up+window slice, in-window signals only (valid here: the warm-up trade is flat
    before START, so it never holds the slot)."""
    r = LS.run_symbol(sliced(sym))
    return [t for t in r['trades'] if t['n'] >= W], [s for s in r['skips'] if s['n'] >= W], r


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


@pytest.mark.parametrize('how', ['gap', 'missing_file'])
def test_incomplete_cache_refused(env, how):
    path = os.path.join(env['cache'], 'SOLUSDT_4h.csv')
    if how == 'gap':
        d = pd.read_csv(path)
        d.drop(index=HEAD + W + 30).to_csv(path, index=False)
    else:
        os.remove(path)
    with pytest.raises(R.RunnerError, match='INCOMPLETE'):
        go(env)
    assert not os.path.exists(env['tmp'] / 'p.json')


def test_cli(env, capsys):
    out = str(env['tmp'] / 'cli.json')
    assert R.main(['--cache', env['cache'], '--window', '2026-09-01T00:00:00Z/2026-10-01T00:00:00Z',
                   '--symbols', 'ETHUSDT', '--out', out]) == 2
    assert 'REFUSED' in capsys.readouterr().err and not os.path.exists(out)


# ------------------------------------------------------------------ warm-up
def test_warmup_signals_excluded(env):
    full = LS.run_symbol(sliced('ETHUSDT'))
    pre = [t for t in full['trades'] if t['n'] < W]
    assert len(pre) == 1 and pre[0]['exit_i'] < W                 # the planted warm-up trade exists and is flat by START
    p = go(env)
    assert p['warmup']['signals_excluded']['short'] >= 1
    assert p['warmup']['bars'] == R.WARMUP_BARS >= 200
    assert all(x['t_signal'] >= p['window']['start'] for x in p['trades'] + p['skips'] + p['open_at_end']['trades'])
    assert all(x['t_in'] >= p['window']['start'] for x in p['trades'])
    assert p['stats']['combined']['n'] == sum(len(oracle(s)[0]) - sum(t['R_net'] is None for t in oracle(s)[0])
                                              for s in SYMS)


def test_warmup_signal_holding_slot_does_not_block_window():
    """A warm-up signal is removed before the slot logic, so a warm-up trade still open at START can never turn an
    in-window signal into an overlap (the C1 overlap fixture: signals 22 and 33, 33 is an overlap of the n=22 trade)."""
    ov = {26: (92.0, 100.0, 91.0, 92.0), 31: (96.0, 102.0, 94.0, 95.0), 32: (95.0, 102.0, 85.0, 86.0),
          33: (86.0, 90.0, 84.0, 88.0)}
    ov.update({k: (88.0, 89.0, 87.0, 88.0) for k in range(34, 60)})
    df = LT.base(extra=ov, N=60)
    assert [x['n'] for x in LS.run_symbol(df)['trades']] == [22]
    ex = []
    with R.window_signals(23, ex):
        r = LS.run_symbol(df)
    assert [x['n'] for x in r['trades']] == [33] and not r['skips']
    assert [(g['side'], g['n']) for g in ex] == [('short', 22)]
    assert LS.detect.__name__ == 'detect' and LS.detect.__module__ == LS.__name__     # wrapper restored


# ------------------------------------------------------------------ packet consistency
def test_packet_fields_and_summarize_consistency(env):
    p = go(env)
    for k in ('format', 'commit', 'runner_sha256', 'window', 'warmup', 'universe', 'cache', 'costs', 'stats', 'decision',
              'trades', 'skips', 'open_at_end', 'packet_sha256'):
        assert k in p
    assert set(p['runner_sha256']) == {'run_lsfvg.py', 'liquidity_sweep_fvg.py', 'run_breadth_candidate.py',
                                       'run_vwaprev_candidate.py', 'backtest.py', 'strategies.py', 'pit_run.py',
                                       'run_r4.py', 'splits.py'}
    assert all(len(v) == 64 for v in p['runner_sha256'].values())
    assert all(len(f['sha256']) == 64 for f in p['cache']['files'].values())
    closed = [t for s in SYMS for t in oracle(s)[0] if t['R_net'] is not None]
    ref = LS.summarize(closed)
    for g, k in (('long', 'long'), ('short', 'short'), ('combined', 'total')):
        st = p['stats'][g]
        for f in ('n', 'lt_minus1', 'lt_minus1_by_label'):
            assert st[f] == ref[k][f]
        for f in ('net_R', 'gross_R', 'fee_R', 'slip_R', 'fund_R', 'pf'):
            assert (st[f] is None) == (ref[k][f] is None)
            if st[f] is not None:
                assert st[f] == pytest.approx(ref[k][f], abs=1e-8)
        need = {'wins', 'avg_R', 'max_dd_R', 'quarters', 'positive_quarters', 'hold_bars_mean', 'hold_bars_median',
                'mfe_r_mean', 'mae_r_mean', 'giveback_gross_r_mean', 'giveback_net_r_mean', 'skips', 'conflict_events', 'open_end', 'concentration',
                'cost_R_per_trade', 'gross_R_per_trade'}
        assert need <= set(st)
        assert set(st['skips']) == set(R.SKIPS)
    c = p['stats']['combined']
    assert c['n'] == p['stats']['long']['n'] + p['stats']['short']['n'] == len(p['trades'])
    assert c['n'] >= 8 and c['exits']['stop'] >= 3 and c['exits']['target'] >= 3 and c['exits']['time'] >= 2
    assert sum(c['quarters'].values()) == pytest.approx(c['net_R'], abs=1e-6)
    conc = c['concentration']
    assert sum(v['n'] for v in conc.values()) == c['n']
    assert sum(v['trade_share'] for v in conc.values()) == pytest.approx(1.0)
    assert sum(v['net_R_share'] for v in conc.values()) == pytest.approx(1.0)
    assert p['open_at_end']['count'] == c['open_end'] == 1
    assert p['open_at_end']['trades'][0]['symbol'] == 'ETHUSDT'
    for row in p['trades']:
        assert {'symbol', 'side', 't_in', 't_out', 'label', 'R_gross', 'R_net', 'mfe_r', 'mae_r', 'giveback_gross_r',
                'giveback_net_r'} <= set(row)
        assert 'giveback_r' not in row
        assert row['giveback_gross_r'] == pytest.approx(row['mfe_r'] - row['R_gross'], abs=1e-8)
        assert row['giveback_net_r'] == pytest.approx(row['mfe_r'] - row['R_net'], abs=1e-8)
        assert row['giveback_net_r'] > row['giveback_gross_r']                 # costs are never credited
        assert row['mfe_r'] >= 0 and row['mae_r'] >= 0
    assert p['decision']['tier_verdict'] in ('CANDIDATE', 'PARK') and 'verdict' not in p['decision']
    assert p['decision']['final_requires'] == 'CANDIDATE on both CORE-10 and CORE-20 (combined sides)'


def test_max_dd_in_exit_order(env):
    p = go(env)
    rows = sorted(p['trades'], key=lambda x: (x['t_out'], x['t_in'], x['symbol'], x['side']))
    cum = np.cumsum([0.0] + [x['R_net'] for x in rows])
    assert p['stats']['combined']['max_dd_R'] == pytest.approx(float((np.maximum.accumulate(cum) - cum).max()), abs=1e-6)


# ------------------------------------------------------------------ MFE / MAE / giveback on hand-built trades
def _ls_frame(ov, N=50):
    rows = [(100.0, 101.0, 99.0, 100.0)] * 23 + [REST] * (N - 23)
    for i, r in ov.items():
        rows[i] = r
    d = pd.DataFrame(np.array(rows), columns=['o', 'h', 'l', 'c'])
    d['v'], d['atr'] = 1.0, 10.0
    return d


BASE = {10: PIV, 20: SWEEP, 21: BEAR, 22: FVG}            # entry 92 at bar 23, stop 113, R 21, target 50


def test_excursions_time_exit_ignores_exit_bar_range():
    d = _ls_frame({**BASE, 27: (92.0, 95.0, 85.0, 92.0), 35: (92.0, 99.0, 70.0, 92.0)})
    t, = LS.run_symbol(d)['trades']
    assert (t['label'], t['exit_i'], t['entry'], t['R_unit']) == ('time', 35, 92.0, 21.0)
    x = R.excursions(d, t)
    assert x['mfe_r'] == pytest.approx(7 / 21) and x['mae_r'] == pytest.approx(3 / 21)   # bar 35 range not counted
    assert x['giveback_gross_r'] == pytest.approx(7 / 21 - t['R_gross'])
    assert x['giveback_net_r'] == pytest.approx(7 / 21 - t['R_net']) and 'giveback_r' not in x


def test_excursions_intrabar_stop_caps_at_stop():
    d = _ls_frame({**BASE, 25: (92.0, 93.0, 88.0, 92.0), 27: (92.0, 130.0, 40.0, 92.0)})
    t, = LS.run_symbol(d)['trades']
    assert (t['label'], t['exit_i'], t['exit']) == ('stop', 27, 113.0)                 # stop beats target same bar
    x = R.excursions(d, t)
    assert x['mae_r'] == pytest.approx(1.0) and x['mfe_r'] == pytest.approx(4 / 21)
    assert x['giveback_gross_r'] == pytest.approx(4 / 21 + 1.0)
    assert x['giveback_net_r'] == pytest.approx(4 / 21 - t['R_net']) and x['giveback_net_r'] > 4 / 21 + 1.0


def test_excursions_long_mirror():
    flip = lambda r: (200 - r[0], 200 - r[2], 200 - r[1], 200 - r[3])
    ov = {k: flip(v) for k, v in {**BASE, 27: (92.0, 95.0, 85.0, 92.0)}.items()}
    d = _ls_frame(ov)
    d.loc[23:, ['o', 'h', 'l', 'c']] = np.array(flip(REST))
    for k in (27,):
        d.loc[k, ['o', 'h', 'l', 'c']] = np.array(ov[k])
    t, = LS.run_symbol(d)['trades']
    assert t['side'] == 'long' and t['entry'] == 108.0
    x = R.excursions(d, t)
    assert x['mfe_r'] == pytest.approx(7 / 21) and x['mae_r'] == pytest.approx(3 / 21)


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


# ------------------------------------------------------------------ determinism
def test_determinism(env):
    a, b = go(env, out='a.json'), go(env, out='b.json')
    with open(env['tmp'] / 'a.json', 'rb') as f, open(env['tmp'] / 'b.json', 'rb') as g:
        assert f.read() == g.read()
    assert a['packet_sha256'] == b['packet_sha256']
    assert LS.detect.__name__ == 'detect' and LS.detect.__module__ == LS.__name__     # wrapper restored


def test_packet_json_roundtrip(env):
    go(env)
    with open(env['tmp'] / 'p.json', encoding='utf-8') as f:
        p = json.load(f)
    assert p['candidate'] == LS.CANDIDATE and p['non_promotable'] is True


# ------------------------------------------------------------------ LS.detect restoration (Codex 6228979583 B)
def test_detect_restored_after_success():
    orig = LS.detect
    ex = []
    with R.window_signals(23, ex):
        assert LS.detect is not orig
        LS.run_symbol(LT.base())
    assert LS.detect is orig and [g['n'] for g in ex] == [22]


def test_detect_restored_after_injected_failure(monkeypatch):
    """The wrapper is removed even when the detector raises inside run_symbol."""
    def boom(df, side, atr=None):
        raise ZeroDivisionError('injected detector failure')
    monkeypatch.setattr(LS, 'detect', boom)
    with pytest.raises(ZeroDivisionError, match='injected'):
        with R.window_signals(5, []):
            LS.run_symbol(LT.base())
    assert LS.detect is boom


# ------------------------------------------------------------------ review 4d535b3 follow-ups
def test_pre_window_trade_raises_runner_error(env, monkeypatch):
    import contextlib
    monkeypatch.setattr(R, 'window_signals', lambda lo_i, ex: contextlib.nullcontext())
    with pytest.raises(R.RunnerError, match='pre-window signal'):
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


def test_prereg_block_written_before_data(env, monkeypatch):
    order = []
    real_pre, real_read = R.prereg, R.B.read_cache
    monkeypatch.setattr(R, 'prereg', lambda: (order.append('prereg'), real_pre())[1])
    monkeypatch.setattr(R.B, 'read_cache', lambda *a, **k: (order.append('read'), real_read(*a, **k))[1])
    p = go(env)
    assert order[0] == 'prereg' and 'read' in order
    pr = p['prereg']
    assert pr['written_before_data'] is True and pr['source'] == 'Cowork 6228948749'
    assert pr['rule_text'] == LS.__doc__
    assert 'exit bar x on open fills' in pr['funding'] and 'both sides' in pr['funding']
    assert 'R_net' in pr['pf_and_quarters_use'] and pr['final_requires'] == R.FINAL_REQUIRES
    assert pr['frozen_v0_semantics'] == [                         # Codex 6229030001, verbatim
        'first qualifying FVG owns the setup; if that entry is skipped for guard/slot/conflict, the setup dies and '
        'does not re-arm;',
        'earliest sweep owns a shared FVG bar; later sweeps are dropped and do not retry;',
        'displacement body uses ATR14[n\u2212' '1].']
    assert pr['frozen_v0_semantics_source'] == 'Codex 6229030001 (freeze of Cowork 6228999316 (i)-(iii))'
    assert pr['reported_counts'] == ['gap_exit_by_label', 'lt_minus1_by_label', 'skips_by_symbol (conflict / overlap)',
                                     'skips_by_reason (cost_guard / gap_stop / overlap / conflict / no_next_bar)',
                                     'open_end', 'giveback_gross_r / giveback_net_r (per trade and mean)']
    assert set(p['skips_by_reason']) == set(R.SKIPS)
    assert p['skips_by_reason'] == {k: sum(1 for x in p['skips'] if x['skip'] == k) for k in R.SKIPS}
    assert p['skips_by_reason'] == p['stats']['combined']['skips']
    for g in ('long', 'short', 'combined'):
        st = p['stats'][g]
        assert set(st['gap_exit_by_label']) == set(R.LABELS)
        assert st['gap_exit_by_label'] == {k: sum(1 for x in p['trades'] if x['label'] == k and x['gap_exit']
                                                  and (g == 'combined' or x['side'] == g)) for k in R.LABELS}
        assert isinstance(st['skips_by_symbol'], dict) and 'lt_minus1_by_label' in st and 'open_end' in st


def test_skips_by_symbol_counts():
    sk = [dict(symbol='ETHUSDT', side='short', n=1, skip='overlap'), dict(symbol='ETHUSDT', side='long', n=2,
          skip='conflict'), dict(symbol='ETHUSDT', side='short', n=2, skip='conflict'),
          dict(symbol='SOLUSDT', side='short', n=3, skip='cost_guard')]
    summ = LS.summarize([])['total']
    st = R.group_stats([], sk, [], ['Q1'], summ)
    assert st['skips_by_symbol'] == {'ETHUSDT': {'conflict': 2, 'overlap': 1}, 'SOLUSDT': {'conflict': 0, 'overlap': 0}}
    assert st['conflict_events'] == 1


# ------------------------------------------------------------------ --check-coverage
def test_check_coverage_reports_late_listing_and_nothing_past_end(env, monkeypatch):
    path = os.path.join(env['cache'], 'SOLUSDT_4h.csv')
    d = pd.read_csv(path, encoding='utf-8')
    d.iloc[HEAD + 40:].to_csv(path, index=False)                   # listed 40 bars after the warm-up start
    calls = []
    monkeypatch.setattr(LS, 'run_symbol', lambda *a, **k: calls.append('run'))
    monkeypatch.setattr(R.VC, 'clean_commit', lambda *a, **k: calls.append('commit'))
    monkeypatch.setattr(LS.ST, 'indicators', lambda *a, **k: calls.append('ind'))
    cov = R.check_coverage(cache=env['cache'], window=WINDOW, symbols=SYMS)
    assert not calls                                                # nothing computed
    assert cov['required_warmup_start'] == SP.utc(WARM_LO)
    eth, sol = cov['symbols']['ETHUSDT'], cov['symbols']['SOLUSDT']
    assert eth['ok'] and eth['first'] == SP.utc(WARM_LO - HEAD * H4) and eth['last'] == SP.utc(HI - H4)
    assert not sol['covers_warmup'] and sol['first'] == SP.utc(WARM_LO + 40 * H4) and not sol['ok']
    assert cov['late_or_short'] == ['SOLUSDT']
    assert all(v['last'] is None or v['last'] < cov['window']['end'] for v in cov['symbols'].values())
    assert SP.utc(HI) not in json.dumps(cov['symbols'])            # the cache runs TAIL bars past END: none shown


def test_check_coverage_missing_file_and_holdout(env):
    os.remove(os.path.join(env['cache'], 'SOLUSDT_4h.csv'))
    cov = R.check_coverage(cache=env['cache'], window=WINDOW, symbols=SYMS)
    assert cov['symbols']['SOLUSDT']['missing_file'] and cov['late_or_short'] == ['SOLUSDT']
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


# ------------------------------------------------------------------ Cowork 6229112441
def test_out_created_during_run_is_never_replaced(env, monkeypatch):
    out = env['tmp'] / 'p.json'
    real = LS.summarize

    def racing(trades):
        with open(out, 'wb') as f:                                  # appears after the initial exists() check
            f.write(b'ORIGINAL')
        return real(trades)
    monkeypatch.setattr(LS, 'summarize', racing)
    with pytest.raises(R.RunnerError, match='created during the run'):
        go(env)
    with open(out, 'rb') as f:
        assert f.read() == b'ORIGINAL'
    assert sorted(os.listdir(env['tmp'])) == sorted(['candles', 'repo', 'p.json'])     # temp file removed


def test_packet_write_leaves_no_temp(env):
    go(env)
    assert sorted(os.listdir(env['tmp'])) == sorted(['candles', 'repo', 'p.json'])


def _poke(env, row, **vals):
    path = os.path.join(env['cache'], 'SOLUSDT_4h.csv')
    d = pd.read_csv(path, encoding='utf-8')
    for k, v in vals.items():
        d.loc[row, k] = v
    d.to_csv(path, index=False)


IN_WIN = HEAD + W + 30                                              # a csv row inside [START, END)


@pytest.mark.parametrize('vals', [dict(c=float('nan')), dict(o=0.0, h=0.0, l=0.0, c=0.0), dict(h=98.0, l=99.0),
                                  dict(h=104.5, c=105.0), dict(l=150.5), dict(l=-1.0),
                                  dict(o=float('inf'))],
                         ids=['nan_close', 'zero_bar', 'h_below_l', 'h_below_close', 'l_above_close', 'negative_low',
                              'inf_open'])
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
    p = go(env)
    assert p['stats']['combined']['n'] >= 8


def test_ohlc_problems_helper_counts():
    d = pd.DataFrame(dict(t=pd.to_datetime([0, H4, 2 * H4, 3 * H4], unit='ms'), o=[1.0, 1.0, 1.0, 1.0],
                          h=[2.0, 0.5, 2.0, 2.0], l=[0.5, 0.4, 1.5, 0.5], c=[1.5, 0.45, 1.6, 1.5]))
    p = R.ohlc_problems(d)
    assert p == {'h_below_max_oc': 1, 'l_above_min_oc': 1, 'first_bad': SP.utc(H4)}
    assert R.ohlc_problems(d.iloc[[0, 3]]) == {}
