"""Five equal PAPER books on one account.

The exchange margin is shared; these are attribution and sizing books, not independent Binance accounts. Unknown/new
strategies land in Discovery so horizontal scouting remains visible without contaminating the established family books.
"""
import actual_costs as AC

BOOKS = (
    ('B1_FAST', 'Fast / scalp', frozenset({'exp_scalp', 'range_scalp', 'failed_breakout_scalp',
                                            'momentum_burst_scalp', 'quick_bank_runner'})),
    ('B2_RANGE', 'Range / VWAP', frozenset({'exp_range', 'exp_vwaprev'})),
    ('B3_TREND', 'Trend / momentum', frozenset({'ema_st', 'ema_mom', 'donchian_ens', 'exp_trend',
                                                'pullback_rsi', 'rotation', 'hot_coin', 'exp_mom_short'})),
    ('B4_BREAKOUT', 'Breakout / structure', frozenset({'breakout_pyramid', 'squeeze_tp', 'bear_breakdown',
                                                       'exp_breakout', 'exp_structure'})),
    ('B5_DISCOVERY', 'Discovery / new research', frozenset({'dca_dip', 'exp_rip_sell_dca'})),
)
IDS = tuple(x[0] for x in BOOKS)


def book_for(strategy):
    for key, _name, strategies in BOOKS:
        if strategy in strategies: return key
    return 'B5_DISCOVERY'


def allocation(equity, cap=2000.0):
    """Equal capital available to each book; scales down honestly until the account reaches 5 x cap."""
    try: return round(max(0.0, min(float(cap), float(equity) / len(BOOKS))), 8)
    except (TypeError, ValueError): return 0.0


def summary(equity, cap, lots=(), history=()):
    per = allocation(equity, cap)
    out = {k: dict(id=k, name=n, strategies=sorted(strategies), allocation=per, open=0, open_risk=0.0,
                   closed=0, net_pnl=0.0, fees=0.0, pending=0)
           for k, n, strategies in BOOKS}
    for lot in lots or ():
        k = lot.get('book')
        if k not in out: continue
        b = out[k]; b['open'] += 1; b['open_risk'] += float(lot.get('risk_current', lot.get('risk_usd')) or 0.0)
    for row in history or ():
        k = row.get('book')
        if k not in out: continue
        b = out[k]; b['closed'] += 1
        v = AC.row_view(row)                     # FILL-PNL-02: the measured net; a pending row is counted, never valued
        if v['status'] == 'pending': b['pending'] += 1; continue
        b['net_pnl'] += float(v['pnl'] or 0.0); b['fees'] += float(AC.measured(row)['fees'] if v['status'] == 'measured'
                                                                  else row.get('fees') or 0.0)
    for b in out.values():
        for x in ('open_risk', 'net_pnl', 'fees'): b[x] = round(b[x], 2)
    return list(out.values())
