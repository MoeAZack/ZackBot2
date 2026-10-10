"""Entry sizing: fixed-fractional risk of equity over the stop distance, capped by leverage, floored to the step.

    risk_usd = equity x risk_pct
    qty_raw  = risk_usd / stop_distance
    qty      = min(qty_raw, (max_leverage x equity - open_notional) / (ref_price x (1 + cap_gap_buffer))), quantized
               DOWN to the step. ref_price is the signal close: the fill (next open) is unknown at decision time, so the
               buffer keeps the cap at the fill for a gap up to the buffer (0.10 = 10%; 0 = research parity)
A quantity below the venue minimum (min_qty, min_notional at ref_price) is no trade (execution.size_min); it is never
rounded up (BT02 / C25). Above max_qty it is cut to max_qty. Decimal throughout, explicit contexts (no ambient one).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Context, Decimal, DivisionByZero, InvalidOperation, Overflow

from newcore.domain import ReasonCode, Rounding

ZERO = Decimal(0)
SCTX = Context(prec=34, rounding=ROUND_DOWN, traps=[InvalidOperation, Overflow, DivisionByZero])


@dataclass(frozen=True, slots=True)
class SizingPolicy:
    risk_pct: Decimal = Decimal('0.01')
    max_leverage: Decimal = Decimal('3')
    cap_gap_buffer: Decimal = Decimal('0')    # cap sized on ref x (1 + buffer): room for a gap at the next open


@dataclass(frozen=True, slots=True)
class Sizing:
    qty: Decimal | None               # None: no trade (reason says why)
    risk_usd: Decimal
    qty_raw: Decimal
    capped: bool
    reason: ReasonCode | None


def size_entry(*, equity, stop_distance, ref_price, rules, policy, open_notional=ZERO) -> Sizing:
    risk_usd = SCTX.multiply(equity, policy.risk_pct)
    if equity <= 0 or stop_distance <= 0 or ref_price <= 0:
        return Sizing(None, risk_usd, ZERO, False, ReasonCode.EXEC_SIZE_MIN)
    raw = SCTX.divide(risk_usd, stop_distance)
    room = SCTX.subtract(SCTX.multiply(policy.max_leverage, equity), open_notional)
    cap = SCTX.divide(max(room, ZERO), SCTX.multiply(ref_price, SCTX.add(1, policy.cap_gap_buffer)))
    q = min(raw, cap)
    q = min(rules.quantize_qty(q, Rounding.DOWN), rules.max_qty) if q > 0 else ZERO
    if q <= 0 or q < rules.min_qty or SCTX.multiply(q, ref_price) < rules.min_notional:
        return Sizing(None, risk_usd, raw, cap < raw, ReasonCode.EXEC_SIZE_MIN)
    return Sizing(q, risk_usd, raw, cap < raw, None)
