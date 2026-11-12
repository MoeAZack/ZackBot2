"""SPOTFLOW-D01 independent oracle (Claude, Evidence lane). RESEARCH ONLY; written from the #219 6830883380 contract text,
with NO imports or copies of Codex candidate code. Purpose: independently reproduce raw signal tuples and selected-lot cash
once Codex's data manifest exists. Frozen before any data/outcome run.

Contract points implemented (one setting, no search):
- Klines: Binance archive/REST 12-column 1h rows (open_time, o, h, l, c, v, close_time, quote_volume, trades,
  taker_buy_base, taker_buy_quote, ignore), header optional. open_time in ms or us (>= 1e14 -> us) normalised to UTC.
  Duplicate open times with different content -> error; exact duplicates collapse.
- Flow I = 2 * taker_buy_quote / quote_volume - 1, valid only if quote_volume > 0 and 0 <= taker_buy_quote <= quote_volume.
  SpotI from spot bars, PerpI from NATIVE USD-M bars, both at the SAME closed hour t (bar opening at t). No forward fill.
- Price/ATR authority: canonical futures 1h (t,o,h,l,c,v,qv). ret3 = ln(c_t / c_{t-3}) with bars t-3..t all present.
  Wilder ATR14 over the canonical bars (alpha 1/14, seeded at the first true range, as the canonical research loader does).
- Long: SpotI >= 0.2 and PerpI <= -0.2 and ret3 <= -ATR_t/c_t. Short: SpotI <= -0.2 and PerpI >= 0.2 and ret3 >= ATR_t/c_t.
  Skip if |BTC ret3| >= 0.015 (BTC bars t-3..t required, else suppressed). Level thresholds.
- PIT eligibility for BOTH venues at t: median of the prior 30 COMPLETE UTC days' quote volume (days strictly before t's day,
  24 bars each, calendar days, a missing/incomplete day breaks the 30-day window) >= 10m AND >= 720 consecutive hourly bars
  ending at t. Futures use the canonical bars, spot its own bars.
- Window: signal hour t in [2024-10-11 17:00, 2026-09-27 23:00] UTC; split 2025-10-01 on entry.
- Lot (causal 1h): entry next 1h open on canonical futures at t+1h (else refused), fill 2 bp adverse, qty = floor(min(8/dist,
  3333.33/fill)/step)*step with min_qty/min_notional, dist = 3 ATR_t; stop fill -/+ dist, target 2R, no partial/BE, exit at
  the first bar close >= entry + 12 h. Per bar: adverse open beyond stop (not the entry bar) -> exit at open; stop touched ->
  stop; target touched -> target; time. Exits 2 bp adverse; fee 5/13/21 bp on each fill notional; funding 1bp/8h x qty x
  close on every held bar incl. exit bar. Terminal: last close, excluded from closes.
"""
import numpy as np, pandas as pd

TH_FLOW, BTC_SKIP, STOP_ATR, TP_R, CAP_H = 0.2, 0.015, 3.0, 2.0, 12
RISK, CAP_NOTIONAL, SLIP, FUND_H = 8.0, 3333.33, 0.0002, 0.0001 / 8
FEE = {7: 0.0005, 15: 0.0013, 23: 0.0021}
T_FROM, T_TO, SPLIT = pd.Timestamp('2024-10-11 17:00'), pd.Timestamp('2026-09-27 23:00'), pd.Timestamp('2025-10-01')
COINS = ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'SOLUSDT', 'DOGEUSDT', 'BNBUSDT', 'ADAUSDT', 'LINKUSDT', 'AVAXUSDT']
COLS = ['open_time', 'o', 'h', 'l', 'c', 'v', 'close_time', 'qv', 'trades', 'tbb', 'tbq', 'ignore']
H = pd.Timedelta(hours=1)


def to_utc(x):
    x = np.asarray(x, dtype='int64')
    return pd.to_datetime(np.where(x >= 10 ** 14, x // 1000, x), unit='ms')


def read_klines(paths):
    frames = []
    for p in paths:
        d = pd.read_csv(p, header=None)
        if not str(d.iloc[0, 0]).strip().lstrip('-').isdigit(): d = d.iloc[1:]
        d = d.iloc[:, :12]; d.columns = COLS
        frames.append(d.astype(float))
    d = pd.concat(frames, ignore_index=True)
    d['t'] = to_utc(d.open_time.astype('int64'))
    d = d.drop(columns=['open_time', 'close_time']).drop_duplicates()
    if d.t.duplicated().any(): raise ValueError('conflicting duplicate open times')
    return d.set_index('t').sort_index()


def flow(qv, tbq):
    qv = np.asarray(qv, float); tbq = np.asarray(tbq, float)
    ok = np.isfinite(qv) & np.isfinite(tbq) & (qv > 0) & (tbq >= 0) & (tbq <= qv)
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.where(ok, 2 * tbq / qv - 1, np.nan)


def wilder_atr(df):
    c = df.c.values; h = df.h.values; l = df.l.values
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.c_[h - l, np.abs(h - pc), np.abs(l - pc)], axis=1)
    out = np.empty(len(tr)); out[0] = tr[0]
    for i in range(1, len(tr)): out[i] = out[i - 1] + (tr[i] - out[i - 1]) / 14
    return pd.Series(out, index=df.index)


def pit_elig(df):
    """df indexed by hourly open time with qv. Bool Series on df.index."""
    day = df.index.floor('D')
    g = df.qv.groupby(day); dq = g.sum().where(g.size() == 24)
    dq = dq.reindex(pd.date_range(dq.index.min(), dq.index.max(), freq='D'))
    med = dq.rolling(30, min_periods=30).median().shift(1)
    liq = med.reindex(day).values >= 1e7
    t = df.index.values
    run = np.ones(len(t), int)
    for i in range(1, len(t)):
        if t[i] - t[i - 1] == np.timedelta64(1, 'h'): run[i] = run[i - 1] + 1
    return pd.Series(liq & (run >= 720), index=df.index)


def ret3(df):
    c = df.c; idx = df.index
    prev = c.reindex(idx - 3 * H).values
    full = pd.Series(True, index=idx)
    for k in (1, 2, 3): full &= pd.Series(c.reindex(idx - k * H).notna().values, index=idx)
    return pd.Series(np.where(full, np.log(c.values / prev), np.nan), index=idx)


def signals(fut, perp_native, spot, btc_fut, fut_elig=None):
    """fut: canonical futures bars of the coin; perp_native/spot: kline frames with qv/tbq; btc_fut: canonical BTC bars.
    Returns DataFrame of (t, side, atr, close) for the window."""
    idx = fut.index
    atr = wilder_atr(fut); r3 = ret3(fut); b3 = ret3(btc_fut).reindex(idx)
    si = pd.Series(flow(spot.qv, spot.tbq), index=spot.index).reindex(idx)
    pi = pd.Series(flow(perp_native.qv, perp_native.tbq), index=perp_native.index).reindex(idx)
    fe = (fut_elig if fut_elig is not None else pit_elig(fut)).reindex(idx).fillna(False).astype(bool)
    se = pit_elig(spot).reindex(idx).fillna(False).astype(bool)
    thr = atr / fut.c
    ok = si.notna() & pi.notna() & r3.notna() & b3.notna() & (b3.abs() < BTC_SKIP) & fe & se
    ok &= (idx >= T_FROM) & (idx <= T_TO)
    lo = ok & (si >= TH_FLOW) & (pi <= -TH_FLOW) & (r3 <= -thr)
    sh = ok & (si <= -TH_FLOW) & (pi >= TH_FLOW) & (r3 >= thr)
    out = pd.DataFrame(dict(side=np.where(lo, 1, np.where(sh, -1, 0)), atr=atr, c=fut.c), index=idx)
    return out[out.side != 0]


def oracle_lot(fut, t_sig, side, atr, rule, cost_bp=7):
    """Independent causal-1h lot from raw canonical bars. Returns dict or dict(refused=...)."""
    t_in = t_sig + H
    if t_in not in fut.index: return dict(refused='missing_next_hour')
    dist = STOP_ATR * atr; fee = FEE[cost_bp]
    ref = fut.o[t_in]; e = ref * (1 + side * SLIP)
    qty = np.floor(min(RISK / dist, CAP_NOTIONAL / e) / rule['step'] + 1e-9) * rule['step']
    if qty < rule['min_qty'] or qty * e < rule['min_notional']: return dict(refused='below_min')
    stop = e - side * dist; tp = e + side * TP_R * dist
    bars = fut.loc[t_in:]
    fund = 0.0; out = None
    for k, (t, b) in enumerate(bars.iterrows()):
        fund += qty * b.c * FUND_H
        adv = b.l if side > 0 else b.h; fav = b.h if side > 0 else b.l
        if k > 0 and side * (b.o - stop) <= 0: out = (b.o, 'stop_gap', t)
        elif side * (adv - stop) <= 0: out = (stop, 'stop', t + H)
        elif side * (fav - tp) >= 0: out = (tp, 'target', t + H)
        elif t + H >= t_in + CAP_H * H: out = (b.c, 'time', t + H)
        if out: break
    if out is None: out = (bars.c.iloc[-1], 'TERMINAL_OPEN', bars.index[-1] + H)
    xr, why, t_out = out
    x = xr * (1 - side * SLIP)
    gross = side * (x - e) * qty; fees = (e + x) * qty * fee
    return dict(t_in=t_in, t_out=t_out, side=side, e=e, x=x, qty=qty, risk=qty * dist, gross=gross, fees=fees,
                fund=fund, pnl=gross - fees - fund, why=why, R=(gross - fees - fund) / RISK)
