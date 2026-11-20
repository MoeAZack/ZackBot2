"""Synthetic tests for EBI step-A feasibility (no market data)."""
import os, sys, tempfile
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import feasibility as F

D = 1_788_220_800_000          # 2026-09-01 00:00 UTC


def ev(ts, agg0=100, raw_breaks=(), agg_breaks=()):
    n = len(ts); agg = agg0 + np.arange(n); first = 1000 + 2 * np.arange(n); last = first + 1
    for i in raw_breaks: first[i:] += 5; last[i:] += 5
    for i in agg_breaks: agg[i:] += 3
    return agg.astype(np.int64), first.astype(np.int64), last.astype(np.int64), np.asarray(ts, np.int64)


def run(e, n=6):
    return F.bin_usability(e[3], D, n, F.defect_intervals(*e))


def dense(end_s, step_ms=1000, start=-2000):
    return list(range(D + start, D + end_s * 1000 + 1, step_ms))


def test_clean_stream_all_usable_except_tail():
    u, lost = run(ev(dense(180)))
    assert u.all() and not any(v.any() for v in lost.values())
    u, lost = run(ev(dense(170)))                                   # last event 170s: bin [150,180) ends > 175 -> tail
    assert list(u) == [True] * 5 + [False] and lost['tail_unobserved'][5]


def test_no_anchor_first_bin():
    u, lost = run(ev(dense(180, start=0)))                          # first event exactly at a=0: no event strictly before
    assert not u[0] and lost['no_anchor'][0] and u[1:].all()


def test_raw_trade_gap_contaminates_spans_inclusive():
    ts = dense(180); i = ts.index(D + 45_000)
    u, lost = run(ev(ts, raw_breaks=[i]))                           # interval [44s, 45s] inside bin [30,60)
    assert list(u) == [True, False, True, True, True, True] and lost['raw_trade_gap'][1]
    assert not lost['agg_id_gap'].any()                             # published agg ids stay continuous
    i = ts.index(D + 60_000)
    u, _ = run(ev(ts, raw_breaks=[i]))                              # [59s,60s]: touches bin1 endpoint b=60 and bin2 span
    assert list(u) == [True, False, False, True, True, True]


def test_anchor_interval_contaminates_next_bin():
    ts = [t for t in dense(180) if not (D + 25_000 <= t < D + 31_000)] # events 24s then 31s: anchor of bin1 at 24s
    i = ts.index(D + 31_000)
    u, lost = run(ev(ts, raw_breaks=[i]))                           # gap interval [24s,31s] (7s also silence)
    assert not u[0] and not u[1] and lost['raw_trade_gap'][1] and lost['silence_gt_5s'][1] and u[2:].all()


def test_agg_gap_and_ts_decrease_and_silence_each_retained():
    ts = dense(180); i = ts.index(D + 100_000)
    u, lost = run(ev(ts, agg_breaks=[i]))
    assert lost['agg_id_gap'][3] and not lost['raw_trade_gap'].any() and not u[3]
    ts2 = list(ts); j = ts2.index(D + 130_000); ts2[j] = D + 128_500   # 129s then 128.5s
    u, lost = run(ev(ts2))
    assert lost['ts_decrease'][4] and not u[4] and u[:4].all()
    ts3 = [t for t in ts if not (D + 70_000 < t < D + 76_000)]         # 70s -> 76s silence 6s
    u, lost = run(ev(ts3))
    assert lost['silence_gt_5s'][2] and not u[2]
    ts4 = [t for t in ts if not (D + 70_000 < t < D + 75_000)]         # exactly 5s: allowed
    u, _ = run(ev(ts4))
    assert u.all()


def test_empty_bin_not_usable_and_gate():
    ts = [t for t in dense(180) if not (D + 30_000 <= t < D + 60_000)]
    u, lost = run(ev(ts))
    assert not u[1] and lost['silence_gt_5s'][1]


def test_load_header_optional_and_report_gate():
    d = tempfile.mkdtemp(); p = os.path.join(d, 'x.csv')
    rows = ''.join(f'{100 + k},1.0,1.0,{1000 + 2 * k},{1001 + 2 * k},{t},false\n' for k, t in enumerate(dense(86_400, 1000)))
    open(p, 'w', encoding='utf-8').write('agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,'
                                         'is_buyer_maker\n' + rows)
    r = F.report(p, D)
    assert r['bins'] == 2880 and r['usable_bins'] == 2880 and r['passes_80pct']
    agg, first, last, t = F.load(p)
    assert agg[0] == 100 and t[0] == D - 2000


def test_single_missing_raw_id_and_anchor_touch_inclusive():
    ts = dense(180); i = ts.index(D + 59_000)
    e = ev(ts); e[1][i:] += 1; e[2][i:] += 1                       # exactly one raw id missing between 58s and 59s
    u, lost = run(e)
    assert lost['raw_trade_gap'][1] and lost['raw_trade_gap'][2]   # [58s,59s] touches bin2 anchor (59s) inclusively
    assert list(u) == [True, False, False, True, True, True]


def test_interval_starting_at_bin_end_touches_that_bin():
    ts = dense(180); i = ts.index(D + 61_000)
    u, lost = run(ev(ts, raw_breaks=[i]))                           # [60s,61s]: lo == b of bin1 -> inclusive touch
    assert lost['raw_trade_gap'][1] and lost['raw_trade_gap'][2] and list(u) == [True, False, False, True, True, True]


if __name__ == '__main__':
    for k, f in sorted(globals().items()):
        if k.startswith('test_'): f(); print('PASS', k)
