"""Instrument identity and venue quantization rules (contract 2, invariant 2; ruling 2).

Hot math (indicators, sizing) may compute in float; a value becomes a record field only through these helpers, which
quantize onto the exchange's tick/step grid with an EXPLICIT rounding direction and exact integer arithmetic. There is no
default rounding, so a caller always states whether it rounds a quantity down (never more than intended) or a price
up/down. The rules are validation input supplied by the caller (NC-03 reads them from the venue), never fetched here.
"""
from __future__ import annotations

import enum
from decimal import Decimal

from .account import Venue
from .base import CTX, Record, canonical_decimal, check_symbol, positive, record, req
from .orders import OrderType


class Rounding(enum.StrEnum):
    DOWN = 'down'          # toward -infinity: never above the input
    UP = 'up'              # toward +infinity: never below the input
    NEAREST = 'nearest'    # half-even


def to_decimal(x, path='value'):
    """Bounded canonical Decimal from a Decimal, an int or a float computed by hot math (via its shortest repr).
    bool / NaN / Infinity / out-of-bounds values are rejected."""
    req(type(x) in (Decimal, int, float), path, f'not a number ({type(x).__name__})')
    if type(x) is float:
        req(x == x and x not in (float('inf'), float('-inf')), path, 'not finite')
        x = Decimal(repr(x))
    elif type(x) is int:
        x = Decimal(x)
    return canonical_decimal(x, path)


def _quantize(x, unit, rounding, path):
    req(isinstance(rounding, Rounding), path, 'rounding must be an explicit Rounding')
    d = to_decimal(x, path)
    n = CTX.divide_int(d, unit)                      # truncated toward zero, exact
    rem = CTX.subtract(d, CTX.multiply(n, unit))     # exact remainder, same sign as d
    if rounding is Rounding.DOWN and rem < 0:
        n = CTX.subtract(n, 1)
    elif rounding is Rounding.UP and rem > 0:
        n = CTX.add(n, 1)
    elif rounding is Rounding.NEAREST and rem:
        twice = CTX.multiply(CTX.abs(rem), 2)
        if twice > unit or (twice == unit and CTX.remainder(n, 2)):
            n = CTX.add(n, 1 if rem > 0 else -1)
    return canonical_decimal(CTX.multiply(n, unit), path)


def on_grid(v, unit):
    return CTX.remainder(v, unit) == 0


class Capability(enum.StrEnum):
    """What the venue supports for this instrument (validation input, not fetched here)."""
    HEDGE_MODE = 'hedge_mode'
    POST_ONLY = 'post_only'
    STOP_MARKET = 'stop_market'
    REDUCE_ONLY = 'reduce_only'


@record
class InstrumentId(Record):
    venue: Venue
    symbol: str

    def _validate(self, p):
        check_symbol(self.symbol, p + '.symbol')


@record
class InstrumentRules(Record):
    """Exchange filters of one instrument (legacy feasibility.size_check vocabulary: step, min_qty, max_qty, tick,
    min_notional) plus its capabilities."""
    instrument: InstrumentId
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal
    capabilities: tuple[Capability, ...]

    def _validate(self, p):
        req(len(set(self.capabilities)) == len(self.capabilities), p + '.capabilities', 'duplicate capability')
        for f in ('tick_size', 'step_size', 'min_qty', 'max_qty'):
            positive(getattr(self, f), f'{p}.{f}')
        req(self.min_notional >= 0, p + '.min_notional', 'must be >= 0')
        req(self.min_qty <= self.max_qty, p + '.max_qty', 'below min_qty')
        req(self.on_step(self.min_qty) and self.on_step(self.max_qty), p + '.min_qty', 'min/max qty off the step grid')

    @property
    def symbol(self):
        return self.instrument.symbol

    def supports(self, cap):
        return Capability(cap) in self.capabilities

    # -------------------------------------------------------------------------------------------- helpers
    def quantize_qty(self, x, rounding):
        return _quantize(x, self.step_size, rounding, f'{self.symbol}.qty')

    def quantize_price(self, x, rounding):
        return _quantize(x, self.tick_size, rounding, f'{self.symbol}.price')

    def on_step(self, q):
        return on_grid(q, self.step_size)

    def on_tick(self, px):
        return on_grid(px, self.tick_size)

    def check_qty(self, q, path='qty', *, reduce_only=False):
        """A quantity the exchange accepts: a whole number of steps, <= max_qty, and >= min_qty unless reduce-only."""
        canonical_decimal(q, path)
        positive(q, path)
        req(self.on_step(q), path, f'{q} is not a multiple of step {self.step_size}')
        req(q <= self.max_qty, path, f'{q} above the venue maximum {self.max_qty}')
        req(reduce_only or q >= self.min_qty, path, f'{q} below the venue minimum {self.min_qty}')

    def check_price(self, px, path='price'):
        canonical_decimal(px, path)
        positive(px, path)
        req(self.on_tick(px), path, f'{px} is not a multiple of tick {self.tick_size}')

    def check_intent(self, intent):
        """Writer-side validation of an OrderIntent against the venue grid (NC-02 A18)."""
        p = f'OrderIntent[{intent.intent_id}]'
        req(intent.symbol == self.symbol, p + '.symbol', f'rules are for {self.symbol}')
        self.check_qty(intent.qty, p + '.qty', reduce_only=intent.reduce_only)
        if intent.order_type is OrderType.LIMIT_POST_ONLY:
            req(self.supports(Capability.POST_ONLY), p + '.order_type', 'post-only is not supported')
        if intent.stop_price is not None:
            req(self.supports(Capability.STOP_MARKET), p + '.order_type', 'stop-market is not supported')
        if intent.price is not None:
            self.check_price(intent.price, p + '.price')
            req(intent.reduce_only or CTX.multiply(intent.price, intent.qty) >= self.min_notional, p + '.qty',
                'below min notional')
        if intent.stop_price is not None:
            self.check_price(intent.stop_price, p + '.stop_price')
