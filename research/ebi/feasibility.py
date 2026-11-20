"""EBI step A: aggTrades data feasibility (Claude, research only). Written from Codex staged GO (#337 6831737001 / #219
6831735253) section A, frozen before any run. No prices, notionals, returns or PnL are used: only ids and timestamps.

Rules implemented:
- Events in file order: agg_trade_id, first_trade_id, last_trade_id, transact_time (ms).
- Defect intervals between consecutive events (i-1, i), each inclusive [lo, hi] in ms:
    raw_trade_gap   first_trade_id_i > last_trade_id_{i-1} + 1           -> [t_{i-1}, t_i]
    agg_id_gap      agg_id_i != agg_id_{i-1} + 1 (gap, repeat or reorder) -> [t_{i-1}, t_i]
    ts_decrease     t_i < t_{i-1}                                         -> [t_i, t_{i-1}]
    silence_gt_5s   t_i - t_{i-1} > 5000                                  -> [t_{i-1}, t_i]
  Each cause is retained separately; an event pair can carry several causes. Unknown causes are not attributed.
- Scheduled bins: every 30 s bin [a, b) of the UTC day. A bin's dependency span is [t_anchor, b], where t_anchor is the
  timestamp of the latest event strictly before a (the start price anchor); the endpoint is the latest event before b,
  which lies inside the span. A bin is usable iff it has an anchor, no defect interval touches its span
  (lo <= b and hi >= t_anchor), and the post-file tail is not inside it (b <= t_last + 5000, else 'tail_unobserved').
  Empty bins are not zero-filled; a 30 s empty bin always contains a >5 s silence and is unusable.
- Report per coin: usable fraction, bins lost and clock time lost per cause (cause counts overlap), union lost.
- Gate: usable fraction >= 0.80 on EACH coin, else DATA_INADEQUATE.
"""
import csv, io, json, sys, zipfile, hashlib
import numpy as np

BIN_MS, SILENCE_MS, GATE = 30_000, 5_000, 0.80
CAUSES = ('raw_trade_gap', 'agg_id_gap', 'ts_decrease', 'silence_gt_5s')


def load(path):
    """Return int64 arrays (agg, first, last, t) from a Binance aggTrades zip/csv, header optional."""
    if path.endswith('.zip'):
        with zipfile.ZipFile(path) as z:
            raw = z.read(z.namelist()[0]).decode('utf-8')
    else:
        raw = open(path, encoding='utf-8').read()
    rows = list(csv.reader(io.StringIO(raw)))
    if rows and not rows[0][0].strip().isdigit(): rows = rows[1:]
    a = np.array([[int(r[0]), int(r[3]), int(r[4]), int(r[5])] for r in rows if r], dtype=np.int64)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def defect_intervals(agg, first, last, t):
    """dict cause -> (lo, hi) int64 arrays."""
    t0, t1 = t[:-1], t[1:]
    masks = dict(raw_trade_gap=first[1:] > last[:-1] + 1, agg_id_gap=agg[1:] != agg[:-1] + 1,
                 ts_decrease=t1 < t0, silence_gt_5s=(t1 - t0) > SILENCE_MS)
    lo, hi = np.minimum(t0, t1), np.maximum(t0, t1)
    return {c: (lo[m], hi[m]) for c, m in masks.items()}


def bin_usability(t, day_start_ms, n_bins, intervals):
    """Returns (usable bool[n], cause -> bool[n] lost flags incl. no_anchor and tail_unobserved)."""
    a = day_start_ms + BIN_MS * np.arange(n_bins, dtype=np.int64); b = a + BIN_MS
    tmax = np.maximum.accumulate(t)                                   # monotone for search; decreases flagged separately
    k = np.searchsorted(tmax, a, side='left') - 1                     # latest event strictly before a
    has_anchor = k >= 0
    span_lo = np.where(has_anchor, tmax[np.maximum(k, 0)], a)
    lost = dict(no_anchor=~has_anchor, tail_unobserved=b > t.max() + SILENCE_MS)
    for c, (lo, hi) in intervals.items():
        diff = np.zeros(n_bins + 1, np.int64)
        # touches span [span_lo_k, b_k] iff lo <= b_k and hi >= span_lo_k; b and span_lo are nondecreasing in k
        k_from = np.searchsorted(b, lo, side='left')                  # first k with b_k >= lo
        k_to = np.searchsorted(span_lo, hi, side='right')             # first k with span_lo_k > hi
        ok = k_from < k_to
        np.add.at(diff, k_from[ok], 1); np.add.at(diff, k_to[ok], -1)
        lost[c] = np.cumsum(diff[:-1]) > 0
    union = np.zeros(n_bins, bool)
    for v in lost.values(): union |= v
    return ~union, lost


def report(path, day_start_ms, n_bins=2880):
    agg, first, last, t = load(path)
    iv = defect_intervals(agg, first, last, t)
    usable, lost = bin_usability(t, day_start_ms, n_bins, iv)
    frac = float(usable.mean())
    return dict(file=path.split('/')[-1], sha256=hashlib.sha256(open(path, 'rb').read()).hexdigest(), events=int(len(t)),
                first_ts=int(t.min()), last_ts=int(t.max()),
                defect_pairs={c: int(len(iv[c][0])) for c in CAUSES},
                defect_clock_ms={c: int((iv[c][1] - iv[c][0]).sum()) for c in CAUSES},
                bins=n_bins, usable_bins=int(usable.sum()), usable_fraction=frac,
                bins_lost_by_cause={c: int(v.sum()) for c, v in lost.items()},
                clock_s_lost_by_cause={c: int(v.sum()) * BIN_MS // 1000 for c, v in lost.items()},
                bins_lost_union=int((~usable).sum()), passes_80pct=frac >= GATE)


if __name__ == '__main__':
    day = int(sys.argv[1]); out = dict(contract='EBI-A #337 6831737001', gate=GATE,
                                      coins=[report(p, day) for p in sys.argv[2:]])
    out['verdict'] = 'A_PASS' if all(c['passes_80pct'] for c in out['coins']) else 'DATA_INADEQUATE'
    print(json.dumps(out, indent=1))
