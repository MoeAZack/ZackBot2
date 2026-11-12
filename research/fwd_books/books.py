"""FWD-BOOKS (research only): two separate hypothetical prospective $4,000 books, H5 long 1h max_pos 1 and EMA-long 4h
max_pos 2, using the UNCHANGED admission-controls definitions @29d76073 (research/admission_controls/controls.py, which
imports research/basket_old3_h5/basket.py @347c4b71 unchanged). Contract: Codex FORWARD EVIDENCE GO, PR #337 6836143114.

- Inputs: Binance USD-M 1h kline arrays already collected by an existing research runner (no new fetcher here) plus,
  optionally, frozen historical 1h CSVs for warmup. Overlapping bars from two sources must agree exactly or the row is
  INPUT_CONFLICT. Gaps, partial or open candles => INCOMPLETE with the precise missing coverage, never zero-filled.
- Cutoff T: the first strictly future unopened UTC 1h candle after the manifest freeze. Both ledgers start flat at T.
  H5 scores 1h bars with open time >= T; EMA scores complete epoch-aligned UTC 4h bars with open time >= T (no hourly
  substitution). Bars before T are warmup input only, never scored.
- Each evaluation re-runs the native engine from the frozen warmup start W0 to the latest complete boundary, so a row is
  a pure function of (frozen definitions, inputs < boundary). Primary priority = alphabetical; cohort order is a
  diagnostic only and is never selected.
- Guards: native 3% Cairo-day halt on MARKED equity vs the Cairo-day start, and the 2.5% open-risk cap (enforce). The
  target ledger replays exactly the native lots; any target refusal is NONPARITY (reported, never relabelled).
- Hypothetical bar replay at 7/15/23 bp per executed side (2 bp slip in fill prices + (c-2) bp fee) and a 1 bp / 8 h
  funding proxy. Not exchange fills, not stop evidence, not live parity.
"""
import os, sys, json, hashlib
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'admission_controls'))
import controls as C
K = C.K
B = C.B
import strategies as S

H1 = pd.Timedelta(hours=1); H4 = pd.Timedelta(hours=4); H1MS = 3_600_000
PRIMARY_ORDER = 'alpha'


def tf_of(name):
    return {sl[0]: sl[3] for sl in C.CONTROLS}[name]


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


def cutoff_after(freeze_ms):
    """First strictly future unopened 1h candle: a candle opening exactly at the freeze instant has already opened."""
    if not isinstance(freeze_ms, (int, np.integer)): raise TypeError('freeze_ms must be an integer UTC ms')
    return (int(freeze_ms) // H1MS + 1) * H1MS


def first_scored_bar(name, T):
    """First bar open time scored for a book: T for 1h, the first UTC 4h boundary >= T for 4h."""
    T = pd.Timestamp(T)
    return T if tf_of(name) == '1h' else T.ceil('4h')


def klines_frame(rows, step):
    """Binance kline arrays -> t,o,h,l,c,v,qv. Every row must span exactly one `step` (Timedelta)."""
    ms = int(step / pd.Timedelta(milliseconds=1))
    if not rows: return pd.DataFrame(columns=['t', 'o', 'h', 'l', 'c', 'v', 'qv'])
    if any(int(x[6]) - int(x[0]) != ms - 1 for x in rows): raise ValueError('kline_span_mismatch')
    return pd.DataFrame(dict(t=pd.to_datetime([int(x[0]) for x in rows], unit='ms'), o=[float(x[1]) for x in rows],
                             h=[float(x[2]) for x in rows], l=[float(x[3]) for x in rows], c=[float(x[4]) for x in rows],
                             v=[float(x[5]) for x in rows], qv=[float(x[7]) for x in rows]))


VOL_RTOL = 1e-6      # volumes may differ by float summation (1h->4h) only; prices must match exactly


def merge_sources(frames):
    """Union of frames in precedence order (earlier wins on duplicate bars). Duplicate bars must match exactly on o,h,l,c
    and within VOL_RTOL on v,qv; otherwise ValueError('input_conflict ...'). Never repairs a disagreement."""
    fs = [f[['t', 'o', 'h', 'l', 'c', 'v', 'qv']].assign(_src=i) for i, f in enumerate(frames) if len(f)]
    d = pd.concat(fs, ignore_index=True).sort_values(['t', '_src'], kind='mergesort')
    first = d.drop_duplicates('t', keep='first').set_index('t')
    rest = d[d.t.duplicated(keep='first')]
    for r in rest.itertuples():
        f = first.loc[r.t]
        if any(getattr(r, k) != f[k] for k in 'ohlc') or any(
                abs(getattr(r, k) - f[k]) > VOL_RTOL * max(abs(f[k]), 1e-12) for k in ('v', 'qv')):
            raise ValueError(f'input_conflict at {r.t}')
    return first.drop(columns='_src').reset_index()


def coverage(raw, W0, T, boundary, step, first_scored):
    """COMPLETE only if every coin has every bar of `step` with open in [W0, boundary - step] (closed by boundary) and
    >= WARMUP bars before the first scored bar. Returns (status, detail) with the exact gaps; never fills anything."""
    W0, T, boundary, first_scored = map(pd.Timestamp, (W0, T, boundary, first_scored))
    need = pd.date_range(W0, boundary - step, freq=step) if boundary - step >= W0 else []
    detail = {}
    for s in K.COINS:
        d = raw.get(s)
        have = set() if d is None else set(d.t[(d.t >= W0) & (d.t + step <= boundary)])
        miss = [h for h in need if h not in have]
        warm = sum(1 for h in have if h < first_scored)
        if miss or warm < K.WARMUP:
            detail[s] = dict(missing_bars=len(miss), first_missing=[str(m) for m in miss[:10]], warmup_bars=warm,
                             warmup_needed=K.WARMUP)
    return ('COMPLETE' if not detail else 'INCOMPLETE'), detail


def signals_after(raw, name, key, start, boundary, side_col='le'):
    """Raw entry signals on closed bars with open time in [start, boundary - bar) per coin (diagnostic)."""
    bar = H1 if tf_of(name) == '1h' else H4
    book = B.Book(raw); out = {}
    for s in raw:
        t = pd.to_datetime(book.t).values; sig = np.asarray(book.raw_sig(key, s)[side_col], dtype=bool)
        m = (t >= np.datetime64(start)) & (t + np.timedelta64(bar) <= np.datetime64(boundary))
        out[s] = int(sig[m].sum())
    return out


def lot_costs(lots, cost_bp):
    fee = (cost_bp - 2) / 1e4; f = sl = 0.0
    for l in lots:
        f += l['qty'] * l['e_fill'] * fee; sl += l['qty'] * l['ref_in'] * K.SLIP
        for x in l['exits']:
            xf = x['ref'] * (1 - l['side'] * K.SLIP); f += x['qty'] * xf * fee; sl += x['qty'] * x['ref'] * K.SLIP
    return f, sl


def book_case(raw, sleeve, cost_bp, path, rules, order, closes1h, hours):
    name, key = sleeve[0], sleeve[1]
    lots, cv = C.native_capacity_run(raw, sleeve, cost_bp, path, rules, order, guards=True)
    ulots, _ = C.native_capacity_run(raw, sleeve, cost_bp, path, rules, order, guards=False)
    rep, recs = C.case_report(name, lots, cv, cost_bp, closes1h, hours, ulots)
    adm = [r for r in recs if r['status'] == 'admitted']
    closed = [r for r in adm if r['closed']]; opn = [r for r in adm if not r['closed']]
    fees, slip = lot_costs(lots, cost_bp)
    rep.update(closed_trade_pnl_usd=sum(r['net'] for r in closed), cash_growth_usd=rep['closed_cash'] - K.CAPITAL,
               open_lots=len(opn), closed_lots=len(closed), fees_usd=fees, slip_usd_approx=slip,
               target_funding_closed_usd=-sum(r['funding'] for r in closed),
               target_funding_open_usd=-sum(r['funding'] for r in opn),
               native_funding_closed_usd=sum(l['native_funding'] for l in lots if l['closed']),
               native_funding_open_usd=sum(l['native_funding'] for l in lots if not l['closed']),
               admitted_ids=[r['id'] for r in adm], guard_blocked_ids=rep.pop('guard_only_missing'))
    return rep, recs


def evaluate(raw1, raw4, rules, W0_1h, W0_4h, T, boundary):
    """One row at a boundary. Pure function of the frozen definitions and closed input bars < boundary.
    raw1: 1h frames (H5 and the target 1h mark/funding ledger); raw4: native UTC 4h frames (EMA)."""
    W0_1h, W0_4h, T, boundary = map(pd.Timestamp, (W0_1h, W0_4h, T, boundary))
    if boundary < T or (boundary - T) % H1: raise ValueError('boundary must be T + k hours')
    fs = {sl[0]: first_scored_bar(sl[0], T) for sl in C.CONTROLS}
    st1, d1 = coverage(raw1, W0_1h, T, boundary, H1, T)
    end4 = boundary.floor('4h')                                  # EMA scores complete 4h bars only
    st4, d4 = coverage(raw4, W0_4h, T, end4, H4, T.ceil('4h'))
    row = dict(boundary=str(boundary), T=str(T), W0_1h=str(W0_1h), W0_4h=str(W0_4h),
               status='COMPLETE' if st1 == st4 == 'COMPLETE' else 'INCOMPLETE', coverage={'1h': d1, '4h': d4}, books={})
    if row['status'] != 'COMPLETE': return row, {}
    r1 = {s: d[(d.t >= W0_1h) & (d.t < boundary)].reset_index(drop=True) for s, d in raw1.items()}
    r4 = {s: d[(d.t >= W0_4h) & (d.t + H4 <= end4)].reset_index(drop=True) for s, d in raw4.items()}
    row['input_sha256'] = {f'{tf}|{s}': hashlib.sha256(pd.util.hash_pandas_object(d, index=False).values.tobytes()).hexdigest()
                           for tf, r in (('1h', r1), ('4h', r4)) for s, d in sorted(r.items())}
    closes1h = {s: dict(zip(d.t, d.c)) for s, d in r1.items()}
    hours = list(pd.date_range(T, boundary - H1, freq='h'))
    K.register_h5()
    old = (K.START, K.END); K.START, K.END = T, boundary
    receipts = {}; nonparity = 0
    try:
        for sl in C.CONTROLS:
            name = sl[0]; raw = r1 if sl[3] == '1h' else r4; bar = H1 if sl[3] == '1h' else H4
            b = dict(tf=sl[3], max_pos=sl[4], first_scored_bar=str(fs[name]), primary_order=PRIMARY_ORDER,
                     scored_bars=int(sum(1 for t in raw['BTCUSDT'].t if t >= fs[name])),
                     signals=signals_after(raw, name, sl[1], fs[name], boundary), cases=[])
            for c in K.COSTS:
                for p in K.PATHS:
                    for oname, order in C.ORDERS.items():
                        if b['scored_bars'] == 0:
                            rep = dict(admitted=0, closes=0, net_usd=0.0, baseline_flat=True); recs = []
                        else:
                            rep, recs = book_case(raw, sl, c, p, rules, order, closes1h, hours)
                            nonparity += rep['nonparity_refusals']
                        b['cases'].append(dict(cost=c, path=p, order=oname, diagnostic=(oname != PRIMARY_ORDER), **rep))
                        receipts[f'{name}_{c}_{p}_{oname}'] = recs
            row['books'][name] = b
    finally:
        K.START, K.END = old
    if nonparity: row['status'] = 'NONPARITY'
    return row, receipts
