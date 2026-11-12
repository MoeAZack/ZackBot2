"""ADMISSION-CONTROLS (research only): H5 and EMA-long run ALONE in the native engine at their ACTUAL capacity from the
start (H5 max_pos 1, EMA max_pos 2), so only admitted lots occupy positions and no refused candidate masks later
opportunities. Contract: Codex GO PR #337 6835533713. Reuses the frozen BASKET-OLD3-H5 definitions, data, rules, cutoff,
common flat start and fixed-sizing helper (research/basket_old3_h5/basket.py @347c4b71, imported unchanged).

Per case (sleeve x cost x path x coin order = 24):
- native backtest.run: start 4000, share 1, fixed $8 pre-fee risk / $3,333.33 cap (basket.fixed_sizing), frozen rule
  floors, max_lev 10, native guards daily_halt 0.03 (engine rule: marked equity vs Cairo-day start) and
  risk_rules open_risk_cap 2.5% (enforce), native funding cadence, sleeve 'symbols' = the coin-priority list.
- guard influence: the same case is rerun with both guards off; any differing entry is reported (guard_changed_entries).
- target economics: basket.target_cash_events (2 bp slip, (c-2) bp fee, hourly 1bp/8h proxy) on the native lots, plus
  basket.replay on exactly those lots (single-sleeve book); any replay refusal = NONPARITY (target guards would have
  admitted differently), reported, never repaired.
"""
import os, sys, json, time
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'basket_old3_h5'))
import basket as K
import backtest as B

ORDERS = {'alpha': sorted(K.COINS),
          'cohort': ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'SOLUSDT', 'DOGEUSDT', 'BNBUSDT', 'ADAUSDT', 'LINKUSDT', 'AVAXUSDT']}
CONTROLS = [s for s in K.SLEEVES if s[0] in ('H5', 'EMA')]
GUARDS = dict(daily_halt=0.03, risk_rules={'open_risk_cap': {'mode': 'enforce', 'pct': 2.5}})


def native_capacity_run(raw, sleeve, cost_bp, path, rules, order, guards=True):
    name, key, sides, tf, cap = sleeve
    book = B.Book(raw)
    fpb = K.FUND_H * (1 if tf == '1h' else 4)
    fills = []
    B.FEE, B.SLIP = (cost_bp - 2) / 1e4, K.SLIP
    kw = dict(daily_halt=GUARDS['daily_halt'], risk_rules=GUARDS['risk_rules']) if guards else dict(daily_halt=1.0)
    with K.fixed_sizing():
        tr, cv = B.run(book, [dict(key=key, share=1.0, risk=K.RISK_USD / K.CAPITAL, max_pos=cap, sides=sides,
                                   symbols=list(order))], start=K.CAPITAL, max_lev=10.0, t0=K.START, t1=K.END,
                       warmup=K.WARMUP, fund_per_bar=fpb, pessimistic=path, exchange_rules=rules, fill_log=fills, **kw)
    T = pd.to_datetime(book.t.values)
    lots = K.build_lots(name, fills, tr, T, K.H1 if tf == '1h' else K.H4, book, fpb, cost_bp)
    return lots, cv


def case_report(name, lots, cv, cost_bp, closes1h, hours, unguarded_lots):
    """Target-economics summary of native admitted lots + parity checks."""
    if name not in K.BOOKS: K.BOOKS[name] = [name]
    rep = K.replay(lots, name, 0, closes1h, cost_bp, hours)
    recs = rep.pop('records')
    nonparity = [r['id'] for r in recs if r['status'] != 'admitted']
    ids_g = {l['id'] for l in lots}; ids_u = {l['id'] for l in unguarded_lots}
    holds = [(l['t_out'] - l['t_in']) / K.H1 for l in lots if l['closed']]
    fills = len(lots) + sum(len(l['exits']) for l in lots)
    weeks = (K.END - K.START) / pd.Timedelta(days=7)
    coin = rep['by_coin']; best_coin = max(coin, key=coin.get) if coin else None
    native_closed = sum(l['native_pnl'] for l in lots if l['closed'])
    max_entry_risk = max((l['risk_usd'] for l in lots), default=0.0)
    return dict(rep, nonparity_refusals=len(nonparity), nonparity_ids=nonparity,
                guard_changed_entries=len(ids_g ^ ids_u), guard_only_missing=sorted(ids_u - ids_g)[:20],
                native_rule_blocked=dict(cv.attrs.get('blocked', {})), exchange_skips=dict(cv.attrs['feasibility']['skipped']),
                native_final_marked_equity=float(cv.iloc[-1]) if len(cv) else None, native_closed_pnl=native_closed,
                native_funding_closed=sum(l['native_funding'] for l in lots if l['closed']),
                fills_per_week=fills / weeks, exits=sum(len(l['exits']) for l in lots),
                median_hold_h=float(np.median(holds)) if holds else None, max_hold_h=max(holds) if holds else None,
                ex_best_coin_usd=rep['net_usd'] - (coin[best_coin] if best_coin else 0.0), best_coin=best_coin,
                max_entry_risk=max_entry_risk, partial_lots=sum(len(l['exits']) > 1 for l in lots)), recs


def compare_overlay(name, recs, overlay_csv):
    """Order-0 overlay row comparison: lot ids admitted here vs admitted in the basket seed-0 record for this sleeve."""
    o = pd.read_csv(overlay_csv)
    o = o[(o.sleeve == name) & (o.status == 'admitted')]
    a = {r['id'] for r in recs if r['status'] == 'admitted'}; b = set(o.id)
    return dict(overlay_admitted=len(b), control_admitted=len(a), common=len(a & b),
                newly_admitted=sorted(a - b), missed=sorted(b - a))
