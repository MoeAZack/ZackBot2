"""Synthetic tests for the prospective books (no market data, no outcomes)."""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books as FB
C, K, S = FB.C, FB.K, FB.S

H1, H4 = FB.H1, FB.H4
W0 = pd.Timestamp('2026-10-01 00:00'); T = pd.Timestamp('2026-11-12 22:00')
RULES = {s: dict(step=0.001, min_qty=0.001, min_notional=5.0) for s in K.COINS}


def data(end=T + 60 * H1, seed=3):
    rng = np.random.default_rng(seed); t = pd.date_range(W0, end - H1, freq='h'); n = len(t); out = {}
    for s in K.COINS:
        c = 100 * np.exp(np.cumsum(rng.normal(0, .003, n))); o = np.r_[c[0], c[:-1]]
        out[s] = pd.DataFrame(dict(t=t, o=o, h=np.maximum(o, c) * 1.002, l=np.minimum(o, c) * .998, c=c, v=1.0, qv=100.0))
    return out, {s: K.to_4h(d) for s, d in out.items()}


def strat(times):
    def fn(d, p, ctx):
        le = pd.Series(d.t.isin(times).values, index=d.index); f = pd.Series(False, index=d.index)
        return dict(le=le, se=f, lx=f.copy(), sx=f.copy())
    return dict(name='t', desc='t', style='t', fn=fn, sides='long', tf='1h', params={}, mgmt=dict(stop_atr=50.0, max_bars=5))


class Synth:
    """Swap the two control sleeves for synthetic strategies with fixed signal times (restored on exit)."""
    def __init__(self, t1h, t4h): self.t1h, self.t4h = t1h, t4h
    def __enter__(self):
        S.STRATEGIES['zz1'] = strat(self.t1h); S.STRATEGIES['zz4'] = strat(self.t4h)
        self.old = C.CONTROLS; C.CONTROLS = [('H5', 'zz1', 'long', '1h', 1), ('EMA', 'zz4', 'long', '4h', 2)]
    def __exit__(self, *a):
        C.CONTROLS = self.old; S.STRATEGIES.pop('zz1'); S.STRATEGIES.pop('zz4')


def ev(raw1, raw4, boundary, W04=W0):
    return FB.evaluate(raw1, raw4, RULES, W0, W04, T, boundary)


def test_cutoff_is_first_strictly_future_unopened_candle():
    h = pd.Timestamp('2026-11-12 21:00').value // 10**6
    assert FB.cutoff_after(h + 1) == h + FB.H1MS
    assert FB.cutoff_after(h) == h + FB.H1MS                      # candle opening at the freeze instant has opened
    assert FB.cutoff_after(h - 1) == h
    assert FB.first_scored_bar('EMA', pd.Timestamp('2026-11-12 22:00')) == pd.Timestamp('2026-11-13 00:00')
    assert FB.first_scored_bar('EMA', pd.Timestamp('2026-11-13 00:00')) == pd.Timestamp('2026-11-13 00:00')
    assert FB.first_scored_bar('H5', T) == T


def test_klines_and_merge_conflicts_are_never_repaired():
    k = [[0, '1', '2', '0.5', '1.5', '10', 3599999, '15', 1, 0, 0, 0], [3600000, '1.5', '2', '1', '1.2', '5', 7199999, '6', 1, 0, 0, 0]]
    d = FB.klines_frame(k, H1); assert list(d.c) == [1.5, 1.2]
    try: FB.klines_frame(k, H4); assert False
    except ValueError as e: assert 'span' in str(e)
    a = d.copy(); b = d.copy(); b.loc[1, 'v'] = 5 * (1 + 1e-9)
    assert len(FB.merge_sources([a, b])) == 2                      # float-sum volume noise allowed
    b.loc[1, 'c'] = 1.2000001
    try: FB.merge_sources([a, b]); assert False
    except ValueError as e: assert 'input_conflict' in str(e)
    b = d.copy(); b.loc[1, 'v'] = 5.01
    try: FB.merge_sources([a, b]); assert False
    except ValueError as e: assert 'input_conflict' in str(e)


def test_missing_bar_or_short_warmup_is_incomplete_not_zero():
    raw1, raw4 = data()
    gap = {s: d.copy() for s, d in raw1.items()}; gap['LINKUSDT'] = gap['LINKUSDT'][gap['LINKUSDT'].t != T + 3 * H1]
    with Synth([], []):
        row, rec = ev(gap, raw4, T + 8 * H1)
    assert row['status'] == 'INCOMPLETE' and row['books'] == {} and rec == {}
    assert row['coverage']['1h']['LINKUSDT']['first_missing'] == [str(T + 3 * H1)]
    with Synth([], []):
        row, _ = ev(raw1, raw4, T + 8 * H1, W04=T - 100 * H4)       # only 100 4h warmup bars
    assert row['status'] == 'INCOMPLETE' and row['coverage']['4h']['BTCUSDT']['warmup_bars'] < K.WARMUP
    trunc = {s: d[d.t < T + 5 * H1] for s, d in raw1.items()}       # inputs end before the boundary
    with Synth([], []):
        row, _ = ev(trunc, raw4, T + 8 * H1)
    assert row['status'] == 'INCOMPLETE' and row['coverage']['1h']['BTCUSDT']['missing_bars'] == 3


def test_baseline_is_flat_and_nothing_before_T_is_scored():
    raw1, raw4 = data()
    pre = [T - 30 * H1, T - 2 * H1]                               # pre-cutoff signals must never become positions
    with Synth(pre, [T - 8 * H1]):
        row, _ = ev(raw1, raw4, T)
        assert row['status'] == 'COMPLETE' and all(c['admitted'] == 0 for b in row['books'].values() for c in b['cases'])
        row, rec = ev(raw1, raw4, T + 12 * H1)
    assert row['status'] == 'COMPLETE'
    for k, rs in rec.items(): assert all(pd.Timestamp(r['t_in']) >= T for r in rs), k
    assert all(c['admitted'] == 0 for c in row['books']['H5']['cases'])


def test_signals_after_T_are_booked_flat_start_and_ema_on_4h_calendar():
    raw1, raw4 = data()
    with Synth([T + 2 * H1], [T + 1 * H1, T + 6 * H1]):           # T+1h (23:00) is not a 4h open: EMA ignores it
        row, rec = ev(raw1, raw4, T + 20 * H1)
    h5 = rec['H5_15_path_alpha']; em = rec['EMA_15_path_alpha']
    assert row['status'] == 'COMPLETE'
    assert [pd.Timestamp(r['t_in']) for r in h5] == [T + 3 * H1]                    # next 1h open
    assert {pd.Timestamp(r['t_in']) for r in em} == {T + 10 * H1}                    # 4h bar 04:00 -> next 08:00 open
    assert len(em) == 2 and {r['sym'] for r in em} == {'ADAUSDT', 'AVAXUSDT'}        # max_pos 2, alphabetical priority
    co = rec['EMA_15_path_cohort']; assert {r['sym'] for r in co} == {'BTCUSDT', 'ETHUSDT'}
    case = [c for c in row['books']['EMA']['cases'] if c['cost'] == 15 and c['path'] == 'path']
    assert [c['diagnostic'] for c in case] == [False, True]
    assert row['books']['EMA']['signals']['BTCUSDT'] == 1 and row['books']['H5']['signals']['BTCUSDT'] == 1


def test_row_is_prefix_stable_and_reconciles():
    raw1, raw4 = data()
    with Synth([T + 2 * H1, T + 14 * H1], [T + 6 * H1]):
        a, ra = ev(raw1, raw4, T + 12 * H1)
        b, rb = ev(raw1, raw4, T + 40 * H1)
    for k in ra:
        ca = {r['id']: r for r in ra[k] if r['closed']}; cb = {r['id']: r for r in rb[k]}
        for i, r in ca.items(): assert abs(r['net'] - cb[i]['net']) < 1e-9, (k, i)      # later data never rewrites the past
    for b_ in b['books'].values():
        for c in b_['cases']:
            assert c['nonparity_refusals'] == 0 and c['guard_changed_entries'] == 0
            assert abs(c['cash_growth_usd'] + c['open_mark'] - c['net_usd']) < 1e-6
            assert c['fees_usd'] > 0 and c['open_lots'] + c['closed_lots'] == c['admitted']
            assert abs(c['target_funding_closed_usd'] + c['target_funding_open_usd'] + c['funding_usd']) < 1e-9   # funding_usd is signed cash
    assert a['input_sha256'] != b['input_sha256'] and set(a['input_sha256']) == set(b['input_sha256'])


if __name__ == '__main__':
    for k, f in sorted(globals().items()):
        if k.startswith('test_'): f(); print('PASS', k)
