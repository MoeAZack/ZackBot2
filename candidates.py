"""FACTORY-CAND-01: per-candidate PAPER attribution for the factory candidate slots (engine.CANDIDATE_SLOTS).

Each candidate slot is its own row: open lots and open risk, closed trades, wins / losses, profit factor, measured net P&L
and fees, trades whose costs are still pending (counted, never valued), enabled flag and the auto-disable reason. Reads only;
nothing here sizes, orders or switches a slot.
"""
import actual_costs as AC


def summary(sleeves, lots=(), history=(), disabled=None):
    out = {}
    for sl in sleeves or ():
        sid = sl.get('id')
        if not str(sid or '').startswith('C-'): continue
        out[sid] = dict(id=sid, key=sl.get('key'), sides=sl.get('sides'), enabled=bool(sl.get('enabled')),
                        disabled=(disabled or {}).get(sid), open=0, open_risk=0.0, closed=0, wins=0, losses=0,
                        gross_win=0.0, gross_loss=0.0, net_pnl=0.0, fees=0.0, pending=0, pf=None)
    for lot in lots or ():
        c = out.get(lot.get('sleeve'))
        if c is None: continue
        c['open'] += 1; c['open_risk'] += float(lot.get('risk_current', lot.get('risk_usd')) or 0.0)
    for row in history or ():
        c = out.get(row.get('sleeve'))
        if c is None: continue
        c['closed'] += 1
        v = AC.row_view(row)                     # FILL-PNL-02: the measured net; a pending row is counted, never valued
        if v['status'] == 'pending': c['pending'] += 1; continue
        pnl = float(v['pnl'] or 0.0)
        c['net_pnl'] += pnl
        c['fees'] += float(AC.measured(row)['fees'] if v['status'] in ('measured', 'acknowledged') else row.get('fees') or 0.0)
        if pnl > 0: c['wins'] += 1; c['gross_win'] += pnl
        else: c['losses'] += 1; c['gross_loss'] -= pnl
    for c in out.values():
        c['pf'] = round(c['gross_win'] / c['gross_loss'], 4) if c['gross_loss'] > 0 else None
        for x in ('open_risk', 'net_pnl', 'fees', 'gross_win', 'gross_loss'): c[x] = round(c[x], 2)
    return list(out.values())
