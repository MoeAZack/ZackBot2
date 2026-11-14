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


def test_a1_same_source_reversed_or_duplicate_input_is_incomplete():
    raw1, raw4 = data()
    rev = {s: d.copy() for s, d in raw1.items()}; rev['ETHUSDT'] = rev['ETHUSDT'].iloc[::-1].reset_index(drop=True)
    dup = {s: d.copy() for s, d in raw1.items()}
    dup['SOLUSDT'] = pd.concat([dup['SOLUSDT'].iloc[:50], dup['SOLUSDT'].iloc[49:]], ignore_index=True)
    r4 = {s: d.copy() for s, d in raw4.items()}; r4['XRPUSDT'] = r4['XRPUSDT'].iloc[[1, 0] + list(range(2, len(r4['XRPUSDT'])))]
    with Synth([], []):
        a, ra = ev(rev, raw4, T + 8 * H1); b, _ = ev(dup, raw4, T + 8 * H1); c, _ = ev(raw1, r4, T + 8 * H1)
    assert a['status'] == 'INCOMPLETE' and ra == {} and a['coverage']['1h']['ETHUSDT']['order_violation']['kind'] == 'out_of_order'
    assert b['status'] == 'INCOMPLETE' and b['coverage']['1h']['SOLUSDT']['order_violation'] == dict(
        kind='duplicate', at=str(W0 + 49 * H1), previous=str(W0 + 49 * H1), row=50)
    assert c['status'] == 'INCOMPLETE' and c['coverage']['4h']['XRPUSDT']['order_violation']['kind'] == 'out_of_order'
    for bad in (raw1['BTCUSDT'].iloc[::-1], pd.concat([raw1['BTCUSDT'].iloc[:3], raw1['BTCUSDT'].iloc[2:]])):
        try: FB.merge_sources([raw1['BTCUSDT'], bad]); assert False
        except ValueError as e: assert 'order_violation source 1' in str(e)
    assert len(FB.merge_sources([raw1['BTCUSDT'].iloc[:30], raw1['BTCUSDT'].iloc[20:]])) == len(raw1['BTCUSDT'])


def test_a1_public_loader_reports_hostile_files_incomplete(tmp=None):
    import json, tempfile, hashlib
    import run_books as RB
    tmp = tmp or tempfile.mkdtemp()
    can = os.path.join(tmp, 'canon'); os.makedirs(os.path.join(can, 'inputs', 'cohort9_1h'))
    t1 = pd.date_range('2026-08-01', K.END - H1, freq='h'); man = {}
    for s in K.COINS:
        p = os.path.join(can, 'inputs', 'cohort9_1h', f'{s}.csv')
        pd.DataFrame(dict(t=[x.value // 10**6 for x in t1], o=100.0, h=101.0, l=99.0, c=100.0, v=1.0, qv=100.0)).to_csv(p, index=False)
        man[s] = dict(input_sha256=hashlib.sha256(open(p, 'rb').read()).hexdigest())
    json.dump({'cohort9_1h': man}, open(os.path.join(can, 'input_manifest.json'), 'w', encoding='utf-8'))
    json.dump(RULES, open(os.path.join(can, 'exchange_rules.json'), 'w', encoding='utf-8'))
    def kl(ts, step):
        ms = int(step / pd.Timedelta(milliseconds=1))
        return [[int(t.value // 10**6), '100', '101', '99', '100', str(1.0 * ms / 3600000), int(t.value // 10**6) + ms - 1,
                 str(100.0 * ms / 3600000), 0, 0, 0, 0] for t in ts]
    fwd = os.path.join(tmp, 'fwd'); os.makedirs(fwd)
    h1 = pd.date_range('2026-10-30 16:00', T - H1, freq='h'); h4 = pd.date_range('2026-09-23 04:00', T.floor('4h') - H4, freq='4h')
    for s in K.COINS:
        a1, a4 = kl(h1, H1), kl(h4, H4)
        if s == 'DOGEUSDT': a1 = a1[:10] + a1[9:]                        # duplicate 1h row, same source
        if s == 'LINKUSDT': a4 = a4[::-1]                                 # reversed 4h source
        json.dump(a1, open(os.path.join(fwd, f'{s}-1h.json'), 'w', encoding='utf-8'))
        json.dump(a4, open(os.path.join(fwd, f'{s}-4h.json'), 'w', encoding='utf-8'))
    fz = os.path.join(tmp, 'freeze.json')
    json.dump(dict(T_utc=str(T), W0_1h_utc='2026-10-30 16:00:00', W0_4h_utc='2026-08-01 00:00:00', source_sha256={},
                   canonical=dict(manifest_sha256=FB.sha(os.path.join(can, 'input_manifest.json')),
                                  rules_sha256=FB.sha(os.path.join(can, 'exchange_rules.json')))), open(fz, 'w', encoding='utf-8'))
    open(os.path.join(tmp, 'freeze.sha256'), 'w', encoding='utf-8').write(FB.sha(fz))
    res = RB.main(fz, can, fwd, os.path.join(tmp, 'out'), coverage_only=True)
    assert res['status'] == 'INCOMPLETE' and list(res['order_violations']) == ['4h|LINKUSDT']
    assert res['order_violations']['4h|LINKUSDT']['kind'] == 'out_of_order'
    json.dump(kl(h4, H4), open(os.path.join(fwd, 'LINKUSDT-4h.json'), 'w', encoding='utf-8'))
    res = RB.main(fz, can, fwd, os.path.join(tmp, 'out'), coverage_only=True)
    assert res['status_1h'] == 'INCOMPLETE' and res['gaps_1h']['DOGEUSDT']['order_violation']['kind'] == 'duplicate'
    assert res['status_4h'] == 'COMPLETE' and list(res['gaps_1h']) == ['DOGEUSDT'], (res['gaps_4h'], list(res['gaps_1h']))
    json.dump(kl(h1, H1), open(os.path.join(fwd, 'DOGEUSDT-1h.json'), 'w', encoding='utf-8'))
    res = RB.main(fz, can, fwd, os.path.join(tmp, 'out'), coverage_only=True)
    assert res['status_1h'] == res['status_4h'] == 'COMPLETE'                # valid cross-source overlap preserved


if __name__ == '__main__':
    for k, f in sorted(globals().items()):
        if k.startswith('test_'): f(); print('PASS', k)
