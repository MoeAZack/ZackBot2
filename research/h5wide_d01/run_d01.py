"""H5-WIDE-D01 runner: Codex contract docs/research/codex_cp4_review_20261112/h5_wide_preregistration.json
(evidence head 5b768545, prereg sha256 622635c784e3fc9e37401623dad9f0c5dadeb3b2386d5a305b7cbd6416ba5174).

Claude Projects implementation, written and frozen BEFORE any D01 economics. Research only. Unchanged H5 long signal
(merged module h5_hourly_long.py, sha256 8573dd81... checked at run time); exits 3 ATR stop / 4R target / 48 bars,
no protection. A small purpose-built event simulator replaces BT.run because the contract needs EXACT fixed $8 risk
(no compounding), one position globally per arm, 40 deterministic coin-tie orders and point-in-time liquidity, which
BT.run does not provide.

Execution model (per arm, per tie order, per cost case):
- Decision on the closed bar t (signal from the H5 callable on that coin's own closed candles); entry at the NEXT bar's
  open of that coin. Only when no position is open. Several coins signalling on the same bar: the first in the tie order.
- Eligibility at decision: the 30 complete UTC days before the decision's UTC day have a median daily quote volume
  >= 10,000,000 USD (all 30 days present), and the coin has >= 720 consecutive hourly bars ending at the decision bar.
- Size: stop distance = 3 x ATR14 of the decision bar; qty = 8 / distance, rounded DOWN to the testnet step; capped so
  notional <= 333.3333 x 10; skipped (with reason) if qty < min_qty or notional < min_notional. Risk before fees is
  therefore <= $8 exactly (never rounded up).
- Exits on that coin's later bars: open beyond the stop -> fill at the open (adverse gap); else low <= stop -> stop; else
  high >= target -> target; stop first when both. Time exit at the close of the 48th bar held. A missing hour is
  skipped; the next present bar is checked with the gap rule.
- Costs: c bp per executed side (7/15/23, slippage included) on entry and exit notional; funding 1 bp per 8 h on the
  open notional for every held hour (charged as a cost). Realized loss halt: no new entry after -$120 realized within a
  Cairo day. Open-risk cap 2.5% ($100) cannot bind with one $8 position.
- Periods: early < 2025-01-01, later >= 2025-01-01 (both consumedDEV for the original 9; the extra 31 are unseen
  for H5 except the 11 symbols of the H5-WIDE pilot, which are flagged as consumed).
Usage: python run_d01.py <clean_csv_dir> <out_dir>
"""
import sys, os, json, hashlib
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'research', 'h5wide')]
SRC = os.path.join(ROOT, 'research', 'h5wide', 'h5_hourly_long_master.py')
assert hashlib.sha256(open(SRC, 'rb').read()).hexdigest() == '8573dd81b39558f55d29682223568d7f72d61e9b25eaa5f072f81dc94139f128'
import strategies as S, h5_hourly_long_master as H5

ORIG = ['ADAUSDT', 'AVAXUSDT', 'BNBUSDT', 'BTCUSDT', 'DOGEUSDT', 'ETHUSDT', 'LINKUSDT', 'SOLUSDT', 'XRPUSDT']
EXTRA = ['1000PEPEUSDT', 'AAVEUSDT', 'APTUSDT', 'ARBUSDT', 'ARKUSDT', 'BCHUSDT', 'DOTUSDT', 'ENAUSDT', 'ENJUSDT',
         'ETCUSDT', 'FETUSDT', 'FILUSDT', 'GALAUSDT', 'INJUSDT', 'LTCUSDT', 'MANAUSDT', 'MOVRUSDT', 'NEARUSDT',
         'ONDOUSDT', 'ONEUSDT', 'QNTUSDT', 'SANDUSDT', 'STRKUSDT', 'SUIUSDT', 'SUPERUSDT', 'TAOUSDT', 'UNIUSDT',
         'WLDUSDT', 'XLMUSDT', 'ZECUSDT', 'ZROUSDT']
PILOT_CONSUMED = {'1000PEPEUSDT', 'ENAUSDT', 'NEARUSDT', 'QNTUSDT', 'SUIUSDT', 'UNIUSDT', 'WLDUSDT', 'ZECUSDT'}
ARMS = dict(original9=ORIG, extra31=EXTRA, union40=ORIG + EXTRA)
T0, T1 = pd.Timestamp('2022-08-26 06:00'), pd.Timestamp('2026-10-04 04:00')
SPLIT = pd.Timestamp('2025-01-01')
RISK, CAP_NOTIONAL, HALT = 8.0, 333.3333 * 10, 120.0
STOP_ATR, TP_R, MAX_BARS, FUND_H = 3.0, 4.0, 48, 0.0001 / 8
COSTS = (7, 15, 23)
N_ORDERS = 40


def load(dirp, sym, rules):
    d = pd.read_csv(os.path.join(dirp, f'{sym}_1h.csv'))
    d['t'] = pd.to_datetime(d.t, unit='ms')
    d = d.drop_duplicates('t').sort_values('t').reset_index(drop=True)
    d = d[d.t <= T1].reset_index(drop=True)                 # full history before T0 is warm-up only
    x = S.indicators(d[['t', 'o', 'h', 'l', 'c', 'v']])
    sig = H5.signal(x, {}, {})['le'].values
    # eligibility: liquidity from the prior 30 complete UTC days, and >= 720 consecutive hourly bars
    day = d.t.dt.floor('D')
    dq = d.groupby(day).qv.sum(); dn = d.groupby(day).size()
    dq = dq.where(dn == 24)                                   # complete days only
    full = pd.date_range(dq.index.min(), dq.index.max(), freq='D')
    dq = dq.reindex(full)
    med = dq.rolling(30, min_periods=30).median().shift(1)    # days strictly before the decision day
    liq = med.reindex(day).values >= 1e7
    gap = d.t.diff().ne(pd.Timedelta(hours=1)).cumsum()
    run = d.groupby(gap).cumcount().values + 1
    elig = liq & (run >= 720)
    r = rules.get(sym)
    return dict(t=d.t.values, o=d.o.values, h=d.h.values, l=d.l.values, c=d.c.values, atr=x.atr.values,
                sig=sig & elig & (d.t.values >= np.datetime64(T0)), sig_raw=sig, elig=elig, rule=r)


def simulate(data, syms, order, cost_bp):
    """One arm / tie order / cost case. Returns trades list and skip log."""
    syms = [s for s in order if s in syms and s in data]
    rank = {s: k for k, s in enumerate(syms)}
    events = {}                                               # grid time -> coins with an eligible decision there
    for s in syms:
        a = data[s]
        for i in np.flatnonzero(a['sig'][:-1]): events.setdefault(a['t'][i], []).append((rank[s], s, i))
    cf = cost_bp / 1e4
    trades, skips = [], []
    day_loss = {}
    times = sorted(events)
    k = 0; free_after = np.datetime64('1970-01-01')
    while k < len(times):
        g = times[k]; k += 1
        if g < free_after: continue                          # a position was open at this decision time
        tt = pd.Timestamp(g); cday = (tt + pd.Timedelta(hours=2)).floor('D')
        if day_loss.get(cday, 0.0) >= HALT: continue
        for _, s, i in sorted(events[g]):
            a = data[s]; dist = STOP_ATR * a['atr'][i]; e = a['o'][i + 1]; r = a['rule']
            if r is None or not np.isfinite(dist) or dist <= 0:
                skips.append((s, tt, 'no_rule_or_atr')); continue
            qty = min(np.floor(RISK / dist / r['step'] + 1e-9), np.floor(CAP_NOTIONAL / e / r['step'] + 1e-9)) * r['step']
            if qty < r['min_qty'] or qty * e < r['min_notional']:
                skips.append((s, tt, 'below_min')); continue
            stop, tp = e - dist, e + TP_R * dist
            fund = 0.0; ex = None; held = 0
            for j in range(i + 1, len(a['t'])):
                o, h, l, c = a['o'][j], a['h'][j], a['l'][j], a['c'][j]
                held += 1; fund += qty * o * FUND_H
                if j > i + 1 and o <= stop: ex = (o, 'stop_gap')
                elif l <= stop: ex = (stop, 'stop')
                elif h >= tp: ex = (tp, 'target')
                elif held >= MAX_BARS: ex = (c, 'time')
                if ex: break
            if ex is None: break                              # data ended with the position open: not counted
            px, why = ex; tout = a['t'][j]
            gross = (px - e) * qty; fees = (e * qty + px * qty) * cf; pnl = gross - fees - fund
            od = (pd.Timestamp(tout) + pd.Timedelta(hours=2)).floor('D')
            if pnl < 0: day_loss[od] = day_loss.get(od, 0.0) - pnl
            trades.append(dict(sym=s, t_sig=tt, t_in=pd.Timestamp(a['t'][i + 1]), t_out=pd.Timestamp(tout), e=e, x=px,
                               qty=qty, risk=qty * dist, gross=gross, fees=fees, fund=fund, pnl=pnl, R=pnl / RISK, why=why,
                               held=held, notional=e * qty))
            free_after = tout                                 # the exit bar's close is the next decision time allowed
            break
    return trades, skips


def summarize(tr, weeks):
    if not tr: return dict(n=0)
    df = pd.DataFrame(tr); R = df.R
    out = dict(n=len(df), closes_per_week=round(len(df) / weeks, 3), meanR=round(float(R.mean()), 4),
               sumR=round(float(R.sum()), 2), win=round(float((R > 0).mean()), 3),
               early_n=int((df.t_in < SPLIT).sum()), later_n=int((df.t_in >= SPLIT).sum()),
               early_meanR=round(float(R[df.t_in < SPLIT].mean()), 4) if (df.t_in < SPLIT).any() else None,
               later_meanR=round(float(R[df.t_in >= SPLIT].mean()), 4) if (df.t_in >= SPLIT).any() else None)
    cum = R.cumsum(); out['closedR_maxdd'] = round(float((cum.cummax() - cum).max()), 2)
    under = (cum < cum.cummax()); out['max_underwater_trades'] = int(under.groupby((~under).cumsum()).sum().max())
    by = R.groupby(df.sym).sum().sort_values()
    out['sumR_by_coin'] = {k: round(float(v), 2) for k, v in by.items()}
    out['exbest_meanR'] = round(float(R[df.sym != by.index[-1]].mean()), 4) if len(by) > 1 else None
    out['fees_R'] = round(float(df.fees.sum() / RISK / len(df)), 4); out['fund_R'] = round(float(df.fund.sum() / RISK / len(df)), 4)
    out['gross_meanR'] = round(float(df.gross.sum() / RISK / len(df)), 4)
    out['risk_usd_max'] = round(float(df.risk.max()), 4); out['risk_usd_median'] = round(float(df.risk.median()), 4)
    out['why'] = df.why.value_counts().to_dict()
    return out


def main(dirp, out):
    os.makedirs(out, exist_ok=True)
    rules = json.load(open(os.path.join(ROOT, 'data', 'exchange_rules_testnet.json'), encoding='utf-8'))['symbols']
    data, missing = {}, []
    for s in ORIG + EXTRA:
        if os.path.exists(os.path.join(dirp, f'{s}_1h.csv')): data[s] = load(dirp, s, rules)
        else: missing.append(s)
    weeks = (T1 - T0).total_seconds() / 604800
    elig_report = {s: dict(first=str(pd.Timestamp(v['t'][0])), bars=int(len(v['t'])), raw_signals=int(v['sig_raw'].sum()),
                           eligible_signals=int(v['sig'].sum()), eligible_hours=int(v['elig'].sum()),
                           rule=bool(v['rule']), pilot_consumed=s in PILOT_CONSUMED) for s, v in data.items()}
    res = dict(contract='H5-WIDE-D01', prereg_sha256='622635c784e3fc9e37401623dad9f0c5dadeb3b2386d5a305b7cbd6416ba5174',
               runner_sha256=hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest(),
               input_sha256={s: hashlib.sha256(open(os.path.join(dirp, f'{s}_1h.csv'), 'rb').read()).hexdigest() for s in data},
               missing_symbols=missing, eligibility=elig_report, weeks=round(weeks, 2), arms={})
    for arm, syms in ARMS.items():
        base = sorted(syms)
        orders = [base] + [list(np.random.default_rng(20261112 + k).permutation(base)) for k in range(1, N_ORDERS)]
        res['arms'][arm] = {}
        for cb in COSTS:
            rows = []
            for k, od in enumerate(orders):
                tr, sk = simulate(data, syms, od, cb)
                sm = summarize(tr, weeks); sm['order'] = k; sm['skips'] = len(sk)
                rows.append(sm)
                if k == 0:
                    pd.DataFrame(tr).to_csv(os.path.join(out, f'{arm}.{cb}bp.order0.trades.csv'), index=False)
            m = pd.DataFrame([{k: v for k, v in r.items() if not isinstance(v, dict)} for r in rows])
            q = lambda col: dict(median=round(float(m[col].median()), 4), p10=round(float(m[col].quantile(.1)), 4),
                                 p90=round(float(m[col].quantile(.9)), 4), min=round(float(m[col].min()), 4),
                                 max=round(float(m[col].max()), 4)) if col in m and m[col].notna().any() else None
            res['arms'][arm][f'{cb}bp'] = dict(order0=rows[0], across_orders={c: q(c) for c in (
                'n', 'closes_per_week', 'meanR', 'early_meanR', 'later_meanR', 'exbest_meanR', 'closedR_maxdd', 'gross_meanR')},
                share_orders_meanR_pos=round(float((m.meanR > 0).mean()), 3))
            json.dump(res, open(os.path.join(out, 'd01_result.json'), 'w', encoding='utf-8'), indent=1, default=str)
    print(json.dumps({a: {c: res['arms'][a][c]['across_orders']['meanR'] for c in res['arms'][a]} for a in res['arms']}))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
