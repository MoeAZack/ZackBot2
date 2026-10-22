"""RISK-FILL-01 (#179): post-fill risk budget from the MEASURED entry price (pure, no I/O).

Codex ruling 6340824820. One risk basis for planned and measured (the same per-unit entry fee on both sides, so fee
noise never looks like slippage):

    risk = executed qty x (side-signed distance from the fill to the rounded stop + entry fee per unit
                           + estimated taker exit fee at the stop)

Decision order: unmeasured -> fill at/through the stop (unwind) -> impossible plan (unwind) -> measured >= CEIL x
planned (unwind) -> measured <= (1 + TOL) x planned (ok; equality allowed, favorable = record only, never a top-up) ->
trim, keeping qty = planned / measured-per-unit rounded DOWN to the step (kept risk <= planned) -> a kept remainder
below the exchange minimum unwinds -> a trim that rounds to 0 is 'below_step' (recorded, nothing sent).
The strategy stop is never re-anchored or widened.
"""
import math

TOL = 0.10          # measured may exceed planned by up to 10% (equality allowed)
CEIL = 2.0          # measured >= 2x planned -> full reduce-only unwind
FEE_EST = 0.0005    # taker exit-fee estimate at the stop (engine.FEE_EST)
ACTIONS = ('ok', 'trim', 'unwind', 'below_step', 'unmeasured')


def _sd(side):
    return 1 if side == 'LONG' else -1


def price_risk(side, qty, entry, stop):
    """Price-to-stop risk only (no fees); 0 when the entry is at/through the stop."""
    return qty * max(0.0, _sd(side) * (entry - stop))


def _floor_step(q, step):
    if not step or step <= 0: return q
    return math.floor(q / step + 1e-9) * step


def decide(side, qty, measured_px, stop, planned_px, entry_fees=None, step=0.0, min_qty=0.0, min_notional=0.0,
           fee_rate=FEE_EST):
    """One entry/add fill's post-fill check -> dict(action, why, planned, measured, ratio, unit, keep_qty, trim_qty,
    fee_basis, fee_unit). entry_fees None = the actual fee is unknown: both sides use fee_rate x planned price."""
    out = dict(action='ok', why=None, planned=None, measured=None, ratio=None, keep_qty=qty, trim_qty=0.0,
               tol=TOL, ceil=CEIL, fee_rate=fee_rate)
    if measured_px is None or not qty or qty <= 0 or stop is None or planned_px is None:
        out.update(action='unmeasured', why='risk_unmeasured', keep_qty=None); return out
    sd = _sd(side)
    if entry_fees is None or entry_fees < 0:
        fee_u, out['fee_basis'] = planned_px * fee_rate, 'estimate'
    else:
        fee_u, out['fee_basis'] = entry_fees / qty, 'actual'
    exit_u = stop * fee_rate
    out['fee_unit'] = round(fee_u + exit_u, 12)
    p_unit, m_dist = sd * (planned_px - stop), sd * (measured_px - stop)
    planned = qty * (p_unit + fee_u + exit_u)
    out['planned'] = round(planned, 8)
    if m_dist <= 0:
        out.update(action='unwind', why='through_stop', trim_qty=qty, keep_qty=0.0); return out
    unit = m_dist + fee_u + exit_u
    measured = qty * unit
    out.update(measured=round(measured, 8), unit=round(unit, 12))
    if p_unit <= 0 or planned <= 0:
        out.update(action='unwind', why='impossible_plan', trim_qty=qty, keep_qty=0.0); return out
    out['ratio'] = round(measured / planned, 6)
    if measured >= CEIL * planned:
        out.update(action='unwind', why='ceiling', trim_qty=qty, keep_qty=0.0); return out
    if measured <= planned * (1 + TOL):
        out['why'] = 'favorable' if measured < planned else 'within_tolerance'; return out
    keep = _floor_step(planned / unit, step)
    trim = round(qty - keep, 12)
    if trim <= 0 or _floor_step(trim, step) <= 0:
        out.update(action='below_step', why='trim_below_step'); return out
    if keep < (min_qty or 0) or keep * measured_px < (min_notional or 0):
        out.update(action='unwind', why='remainder_below_min', trim_qty=qty, keep_qty=0.0); return out
    out.update(action='trim', why='over_tolerance', keep_qty=round(keep, 12), trim_qty=trim)
    return out


def residual(side, qty, measured_px, stop, fee_unit):
    """Risk still carried by qty at the measured entry (after a short/failed trim): qty x (distance + fees)."""
    d = _sd(side) * (measured_px - stop)
    return round(qty * (max(0.0, d) + (fee_unit or 0.0)), 8)
