"""Handoff checks for N6 / N8 (Codex review of #334): turnover-fallback use, fill-to-trade reconciliation,
adverse-path drawdown at 7/15/23 bp, funding sensitivity. Rules unchanged; reuses newstrat_cp_run's setup.
Usage: python research/newstrat_cp_handoff.py <out_json>"""
import json, sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import newstrat_cp_run as R
import backtest as BT, strategies as S, newstrat_cp2 as N2

EXTRA = {7: 0.0, 15: 0.0008, 23: 0.0016}


def run(key, scen, fund_mult=1.0):
    st = S.STRATEGIES[key]
    raw = R.load('4h'); N2.set_btc(raw['BTCUSDT']); book = BT.Book(raw)
    rules = json.load(open(os.path.join(R.ROOT, 'data', 'exchange_rules_testnet.json'), encoding='utf-8'))
    log = []
    tr, cv = BT.run(book, [dict(key=key, share=1.0, risk=R.RISK, max_pos=2,
                                symbols=[x for x in R.SYMS if x not in st.get('exclude', [])])],
                    start=R.START, max_lev=10, daily_halt=0.08, warmup=220, fund_per_bar=0.00005 * fund_mult,
                    pessimistic=scen, exchange_rules=rules, fill_log=log)
    return book, tr, cv, log


def reconcile(tr, log):
    fills = pd.DataFrame(log)
    fills['notional'] = (fills.ref * fills.qty).abs()
    keys = set(zip(tr.sym, tr.i_in))
    fk = list(zip(fills.sym, fills.pos_i))
    orphan = [k for k in set(fk) if k not in keys]                 # positions still open at the end of data
    g = fills.groupby(['sym', 'pos_i'])
    per = g.agg(n=('kind', 'size'), entries=('kind', lambda k: int((k == 'entry').sum())),
                exit_qty=('qty', lambda q: 0.0), notional=('notional', 'sum'))
    ent_q = fills[fills.kind == 'entry'].groupby(['sym', 'pos_i']).qty.sum()
    ex_q = fills[fills.kind == 'exit'].groupby(['sym', 'pos_i']).qty.sum()
    closed = [k for k in keys]
    qty_mismatch = [k for k in closed if abs(ent_q.get(k, 0) - ex_q.get(k, 0)) > 1e-9 * max(1.0, ent_q.get(k, 0))]
    tt = np.array([per.notional.get(k, np.nan) for k in zip(tr.sym, tr.i_in)])
    return dict(trades=int(len(tr)), fills=int(len(fills)), fills_on_closed_trades=int(sum(k in keys for k in fk)),
                open_at_end_positions=[list(map(str, k)) for k in orphan],
                trades_without_fills=int(np.isnan(tt).sum()), entries_per_trade_max=int(per.entries.max()),
                entry_exit_qty_mismatches=len(qty_mismatch), turnover_fallback_needed=bool(np.isnan(tt).any()),
                duplicate_trade_keys=int(len(tr) - len(keys))), tt


def dd_closed(tr, tt, extra, base_r):
    t = tr.assign(tt=tt).sort_values('i_out')
    eq = (t.pnl - t.tt * extra).cumsum()
    eq = pd.concat([pd.Series([0.0]), eq.reset_index(drop=True)])
    return round(float((eq.cummax() - eq).max() / base_r), 1), round(float(eq.iloc[-1]), 2)


def main(out):
    R.N.register(); N2.register()
    res = {}
    for key in ('cp_n6_btc_lead_catchup_4h', 'cp_n8_squeeze_breakdown_short_4h'):
        for scen in ('path', 'worst'):
            book, tr, cv, log = run(key, scen)
            rec, tt = reconcile(tr, log)
            mtm = float((cv.cummax() - cv).max()) / R.BASE_R
            dd = {f'{bp}bp': dict(zip(('closed_trade_maxdd_R', 'net_pnl_usd'), dd_closed(tr, tt, x, R.BASE_R))) for bp, x in EXTRA.items()}
            fund = {}
            for fm in (0.0, 1.0, 2.0, 3.0):
                tr2, cv2 = (tr, cv) if fm == 1.0 else run(key, scen, fm)[1:3]
                risk = (tr2.pnl / tr2.R).abs()
                _, tt2 = reconcile(tr2, run(key, scen, fm)[3]) if fm != 1.0 else (None, tt)
                fund[f'x{fm:g}'] = dict(n=int(len(tr2)), end_equity=round(float(cv2.iloc[-1]), 2),
                                        expR_15bp=round(float(((tr2.pnl - tt2 * 0.0008) / risk).mean()), 4))
            res[f'{key}.{scen}'] = dict(reconciliation=rec, mark_to_market_maxdd_R_7bp=round(mtm, 1), drawdown=dd, funding=fund)
            print(key, scen, json.dumps(res[f'{key}.{scen}']), flush=True)
    json.dump(res, open(out, 'w', encoding='utf-8'), indent=1)


if __name__ == '__main__':
    main(sys.argv[1])
