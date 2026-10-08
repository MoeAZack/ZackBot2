"""RANGE-BB-MR.v1 as a management plan: the M4 mechanics fixture (disabled by default, mechanics only, 4h).

Research verdict (M4, 2026-10-08): the strategy is REJECTED as an edge (4h long IS -0.110R, CI entirely below 0); it
is kept only as a safe workload that exercises every S2 mechanic on testnet. Spec, long (the short is the exact mirror),
ATR0 = the signal candle's ATR, frozen:

    basket stop  e - 2.0 ATR0 (rounded away from the entry at the tick)
    one add      at e - 1.0 ATR0 (rounded toward the stop), scale 1.0
    TP1          50% at avg + 1.0 ATR0, then break-even on the CONFIRMED TP1 fill
    TP2          the rest at avg + 2.0 ATR0 (both re-anchored to the average after the add)
    time exit    the close of the 12th candle counted from the entry candle

The caller sizes the entry (NC-06) so the whole plan, the reserved add included, fits `risk_cap`; `build_plan` drops an
add that would not fit and says so.
"""
from __future__ import annotations

from decimal import Decimal

from ..domain.base import CTX, req
from ..domain.instrument import Rounding
from ..domain.orders import Side
from .plan import build_plan, sgn

NAME = 'RANGE-BB-MR.v1'
CANDLE_SECONDS = 4 * 3600
STOP_ATR = Decimal(2)
ADD_ATR = Decimal(1)
ADD_SCALE = Decimal(1)
TP1_ATR = Decimal(1)
TP1_FRAC = Decimal('0.5')
TP2_ATR = Decimal(2)
TIME_EXIT_CANDLES = 12


def range_bb_mr_v1(*, rules, side, entry_price, entry_qty, entry_candle_open_ms, atr0, risk_cap, costs):
    """The plan of one RANGE-BB-MR.v1 position at its confirmed entry fill. Always disabled-by-default + mechanics-only."""
    req(atr0 > 0, 'range_bb_mr_v1.atr0', 'must be > 0')
    s = sgn(side)
    away = Rounding.DOWN if side is Side.LONG else Rounding.UP
    stop = rules.quantize_price(CTX.subtract(entry_price, CTX.multiply(s, CTX.multiply(STOP_ATR, atr0))), away)
    add = rules.quantize_price(CTX.subtract(entry_price, CTX.multiply(s, CTX.multiply(ADD_ATR, atr0))), away)
    return build_plan(rules=rules, side=side, entry_price=entry_price, entry_qty=entry_qty,
                      entry_candle_open_ms=entry_candle_open_ms, candle_seconds=CANDLE_SECONDS, stop_price=stop,
                      risk_cap=risk_cap, costs=costs, add_price=add, add_scale=ADD_SCALE, tp1_frac=TP1_FRAC,
                      tp1_offset=CTX.multiply(TP1_ATR, atr0), tp2_offset=CTX.multiply(TP2_ATR, atr0), be_after_tp1=True,
                      time_exit_candles=TIME_EXIT_CANDLES, trail_offset=None, disabled_by_default=True,
                      mechanics_only=True)
