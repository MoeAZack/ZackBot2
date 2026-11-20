"""Evaluate one NEWSTRAT-CP4 hourly candidate (account-4 replacements) with the unchanged BT.run.
Usage: python research/newstrat_cp4_run.py <key> <out_dir> [path|worst]

Copied from research/newstrat_cp_run.py (PR #334) with account 4 sizing and the CP4 gate; frozen before any run.
Periods are consumedDEV early (<2025) and consumedDEV later (>=2025): neither is an untouched holdout.

Frozen evaluation (set before any run):
- universe: the 8 cohort coins with multi-year data (BTC ETH XRP SOL DOGE BNB LINK AVAX; ADA has no long file)
- 1h: data_long/1h (from 2022-08-26); 4h->1d resample of data_long/4h for the daily candidate
- $4,000 start, risk 0.002 (= $8 per entry, account 4), max_pos 1, max_lev 10, daily_halt 0.08, exchange_rules = testnet snapshot
- costs: base fee 5 bp + slip 2 bp per side (7 bp); stresses add 8 / 16 bp on executed notional (15 / 23 bp per side)
- funding: 0.01%/8h equivalent charged per bar on open notional, scaled to the bar length
- periods: DEV < 2025-01-01, HOLDOUT >= 2025-01-01 (single run, reported split; no parameter was tuned on either)
- CP4 gate (path fills): >=30 closes in each period; expectancy > 0 R at 15 bp in BOTH periods; >= 0 R at 23 bp
  overall; median round-trip cost at 15 bp <= 15% of R; > 0 R at 15 bp overall without the best coin; worst-fill
  scenario > 0 R at 15 bp overall. Max drawdown is REPORTED (closed-R and equity), not gated (owner allows high DD).
  The turnover fallback is counted and reported; a candidate that needed it is not cost-qualified.
"""
import json, sys, glob, os, hashlib
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as BT, strategies as S, newstrat_cp4 as N4

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMS = ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'SOLUSDT', 'DOGEUSDT', 'BNBUSDT', 'LINKUSDT', 'AVAXUSDT']
START, RISK, SPLIT = 4000.0, 0.002, pd.Timestamp('2025-01-01')
BASE_R = START * RISK


def load(tf):
    out = {}
    for s in SYMS:
        sub = '1h' if tf == '1h' else '4h'
        d = pd.read_csv(os.path.join(ROOT, 'data_long', sub, f'{s}_{sub}.csv'), parse_dates=['t'])
        if tf == '1d':
            d = (d.set_index('t').resample('1D', label='left', closed='left')
                 .agg(dict(o='first', h='max', l='min', c='last', v='sum')).dropna().reset_index())
            d = d.iloc[:-1]                         # drop a possibly incomplete last day
        out[s] = d
    return out


def period_stats(tr, mask, turn_by_trade):
    t = tr[mask]
    n = len(t)
    if not n: return dict(n=0)
    risk = (t.pnl / t.R).abs()
    ext = turn_by_trade[mask]
    res = dict(n=n, win=round(float((t.pnl > 0).mean() * 100), 1), pnl=round(float(t.pnl.sum()), 2),
               long_n=int((t.side == 1).sum()), short_n=int((t.side == -1).sum()),
               long_pnl=round(float(t.pnl[t.side == 1].sum()), 2), short_pnl=round(float(t.pnl[t.side == -1].sum()), 2))
    for bp, extra in ((7, 0.0), (15, 0.0008), (23, 0.0016)):
        res[f'expR_{bp}bp'] = round(float(((t.pnl - ext * extra) / risk).mean()), 4)
        res[f'pnl_{bp}bp'] = round(float((t.pnl - ext * extra).sum()), 2)
    return res


def main(key, out, scen='path'):
    N4.register()
    st = S.STRATEGIES[key]
    tf = st['tf']
    bar_h = {'1h': 1, '4h': 4, '1d': 24}[tf]
    raw = load(tf)
    book = BT.Book(raw)
    rules = json.load(open(os.path.join(ROOT, 'data', 'exchange_rules_testnet.json'), encoding='utf-8'))
    log = []
    tr, cv = BT.run(book, [dict(key=key, share=1.0, risk=RISK, max_pos=1, symbols=[x for x in SYMS if x not in st.get('exclude', [])])], start=START, max_lev=10, daily_halt=0.08,
                    warmup=60 if tf == '1d' else 220, fund_per_bar=0.00005 * bar_h / 4, pessimistic=scen,
                    exchange_rules=rules, fill_log=log)
    T = pd.to_datetime(book.t.values)
    if len(tr):
        tr['t_in'] = T[tr.i_in.values]; tr['t_out'] = T[tr.i_out.values]
    # executed notional per trade (entry + every exit fill), matched on (symbol, entry bar)
    turn = {}
    for f in log:
        k = (f.get('sym') or f.get('s'), f.get('i_in', f.get('pos_i')))
        turn[k] = turn.get(k, 0.0) + abs(f['ref'] * f['qty'])
    tt = np.array([turn.get((r.sym, r.i_in), np.nan) for r in tr.itertuples()]) if len(tr) else np.array([])
    res_fallback = int(np.isnan(tt).sum()) if len(tr) else 0
    if len(tr) and np.isnan(tt).any():           # fallback: pro-rate the total turnover by risk
        tot = sum(abs(f['ref'] * f['qty']) for f in log)
        w = (tr.pnl / tr.R).abs(); tt = (w / w.sum() * tot).values
    if len(tr):
        tr['turnover'] = tt; tr['risk_usd'] = (tr.pnl / tr.R).abs()
    dd_usd = float((cv.cummax() - cv).max()) if len(cv) else 0.0
    res = dict(key=key, tf=tf, scenario=scen, symbols=SYMS, bars=len(T), first=str(T[0]), last=str(T[-1]),
               end_equity=round(float(cv.iloc[-1]), 2), maxdd_usd=round(dd_usd, 2), maxdd_R=round(dd_usd / BASE_R, 1),
               feasibility={k: v for k, v in cv.attrs.get('feasibility', {}).items() if k in ('attempts', 'executed', 'rule_blocked')},
               all=period_stats(tr, np.ones(len(tr), bool), tt) if len(tr) else dict(n=0),
               early_consumed=period_stats(tr, (tr.t_in < SPLIT).values, tt) if len(tr) else dict(n=0),
               later_consumed=period_stats(tr, (tr.t_in >= SPLIT).values, tt) if len(tr) else dict(n=0),
               why=tr.why.value_counts().to_dict() if len(tr) else {},
               code_sha256={n: hashlib.sha256(open(os.path.join(ROOT, p), 'rb').read()).hexdigest()
                            for n, p in (('newstrat_cp4.py', 'research/newstrat_cp4.py'), ('newstrat_cp4_run.py', 'research/newstrat_cp4_run.py'), ('backtest.py', 'backtest.py'),
                                         ('strategies.py', 'strategies.py'))})
    a, dv, ho = res['all'], res['early_consumed'], res['later_consumed']
    res['dd_label'] = ('Standard' if res['maxdd_R'] <= 15 else 'Elevated' if res['maxdd_R'] <= 30 else
                       'High' if res['maxdd_R'] <= 60 else 'Extreme')
    if len(tr):
        risk = tr.risk_usd
        cost15 = tr.turnover * 0.0015 / risk                       # round-trip cost at 15 bp per side, in R
        res['cost15_R_median'] = round(float(cost15.median()), 4)
        r15 = (tr.pnl - tr.turnover * 0.0008) / risk
        by = r15.groupby(tr.sym).sum().sort_values()
        res['sumR15_by_coin'] = {k: round(float(v), 2) for k, v in by.items()}
        res['expR15_wo_best_coin'] = round(float(r15[tr.sym != by.index[-1]].mean()), 4) if len(by) > 1 else None
        res['sumR15_by_year'] = {str(k): round(float(v), 2) for k, v in r15.groupby(tr.t_in.dt.year).sum().items()}
        top5 = r15.sort_values().iloc[-5:].sum(); res['top5_share'] = round(float(top5 / r15.sum()), 3) if r15.sum() > 0 else None
        res['closedR15_maxdd'] = round(float((r15.cumsum().cummax() - r15.cumsum()).max()), 2)
        weeks = (T[-1] - T[0]).total_seconds() / 604800
        res['closes_per_week'] = round(len(tr) / weeks, 2)
    res['turnover_fallback_trades'] = res_fallback
    res['paper_eligible'] = bool(len(tr) and dv.get('n', 0) >= 30 and ho.get('n', 0) >= 30 and dv['expR_15bp'] > 0 and
                                 ho['expR_15bp'] > 0 and a['expR_23bp'] >= 0 and res['cost15_R_median'] <= 0.15 and
                                 (res['expR15_wo_best_coin'] or 0) > 0 and res_fallback == 0)
    os.makedirs(out, exist_ok=True)
    tr.to_csv(os.path.join(out, f'{key}.{scen}.trades.csv'), index=False)
    json.dump(res, open(os.path.join(out, f'{key}.{scen}.result.json'), 'w', encoding='utf-8'), indent=1, default=str)
    print(json.dumps(res, default=str))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else 'path')
