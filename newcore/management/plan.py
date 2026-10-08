"""The management plan (NC-07): fixed once, at the CONFIRMED entry fill, and never changed afterwards.

A plan carries every level and size the core may ever act on: the basket stop, at most ONE bounded add (price, scale),
TP1 (fraction, offset) and TP2 (offset) relative to the basket average, break-even after a confirmed TP1 fill, a time
exit in candles from the entry candle, an optional ratcheting trail, the cost model and the risk cap. Its invariants are
checked by the constructor (domain Record rules: strict Decimal, explicit fields, no defaults):

- side-correct levels on the tick grid; the add sits strictly inside the stop (DCA between stop and entry, or pyramid
  beyond the entry), and entry + add never exceed the venue max_qty (so the stop can always be sized to the position);
- the add quantity is `floor(scale x entry_qty)` at the step (never rounded up) and is a sendable opening order;
- planned risk to the stop, INCLUDING the reserved add (filled with adverse slippage), exit slippage at the stop and the
  taker fee on every fill, is <= `risk_cap`. The add's risk is therefore reserved at entry.

`build_plan` is the only convenience constructor: it derives the add size, drops an add that floors below the venue
minimum or would break the cap (and says so in `notes`), and refuses (PlanRefused) a plan whose entry alone breaks the cap.

`admit_entry` is the pre-trade cost-to-stop veto: an entry whose round-trip cost in R,
`(2 x fee + 2 x slip) x entry / |entry - stop|`, exceeds `max_cost_r` (default 0.15R) is refused. 1h needs it; on 4h a
2-ATR stop costs about 0.04-0.09R and passes.
"""
from __future__ import annotations

import enum
from decimal import ROUND_HALF_EVEN, Context, Decimal

from ..domain.base import CTX, ZERO, Record, check_int, positive, record, req
from ..domain.errors import DomainError
from ..domain.instrument import InstrumentRules, Rounding
from ..domain.orders import Side

DIV = Context(prec=28, rounding=ROUND_HALF_EVEN)       # the one rounding context (averages, displayed ratios)
DEFAULT_MAX_COST_R = Decimal('0.15')
ONE = Decimal(1)


class ManagementError(DomainError):
    """A management input the core cannot apply (a fill larger than its order, a candle out of order, ...)."""


class PlanRefused(ManagementError):
    """The entry alone already breaks the risk cap: no plan (the runner closes the entry under its own reason)."""


def sgn(side):
    return 1 if side is Side.LONG else -1


def better(side, a, b):
    """The tighter (more protective) of two stop levels for `side`."""
    return max(a, b) if side is Side.LONG else min(a, b)


def market_fill(px, side, slip, *, opening):
    """Price of a market (taker) fill at `px`, adverse-adjusted by the slippage: a buy pays px x (1 + slip), a sell gets
    px x (1 - slip). Opening a LONG and closing a SHORT buy."""
    buy = (side is Side.LONG) == opening
    return CTX.multiply(px, CTX.add(ONE, slip) if buy else CTX.subtract(ONE, slip))


def toward_profit(side):
    """Rounding of a target / break-even level: never closer to the entry than computed (long UP, short DOWN)."""
    return Rounding.UP if side is Side.LONG else Rounding.DOWN


def toward_loss(side):
    """Rounding of a trailed stop: never tighter than computed (long DOWN, short UP)."""
    return Rounding.DOWN if side is Side.LONG else Rounding.UP


@record
class CostModel(Record):
    """Taker fee and slippage as fractions of the notional (the golden pack's `legacy` cost model)."""
    taker_fee: Decimal
    slip: Decimal

    def _validate(self, p):
        for f in ('taker_fee', 'slip'):
            v = getattr(self, f)
            req(ZERO <= v < ONE, f'{p}.{f}', 'must be in [0, 1)')


@record
class ManagementPlan(Record):
    rules: InstrumentRules
    side: Side
    entry_price: Decimal               # confirmed average entry fill (slippage included)
    entry_qty: Decimal                 # confirmed entry quantity (on the step)
    entry_candle_open_ms: int          # open of the candle the entry filled in (candle 1 of the time exit)
    candle_seconds: int
    stop_price: Decimal                # the basket stop: fixed, never widened
    add_price: Decimal | None          # the ONE add (trigger level), or None
    add_scale: Decimal | None
    add_qty: Decimal | None            # floor(add_scale x entry_qty) at the step
    tp1_frac: Decimal | None           # TP1: fraction of the position, at avg +/- tp1_offset
    tp1_offset: Decimal | None
    tp2_offset: Decimal | None         # TP2: the remainder, at avg +/- tp2_offset
    be_after_tp1: bool                 # break-even after a CONFIRMED TP1 fill (never on a touch)
    time_exit_candles: int | None      # close at the close of the Nth candle counted from the entry candle
    trail_offset: Decimal | None       # ratcheting stop at candle close: close -/+ offset (after TP1 when TP1 is planned)
    risk_cap: Decimal                  # quote-currency budget for the WHOLE plan (entry + reserved add + costs)
    costs: CostModel
    disabled_by_default: bool
    mechanics_only: bool               # a mechanics fixture: no edge is claimed

    def _validate(self, p):
        r, s = self.rules, sgn(self.side)
        r.check_qty(self.entry_qty, p + '.entry_qty')
        positive(self.entry_price, p + '.entry_price')
        r.check_price(self.stop_price, p + '.stop_price')
        req(s * (self.entry_price - self.stop_price) > 0, p + '.stop_price', 'on the wrong side of the entry')
        req(self.candle_seconds >= 60, p + '.candle_seconds', 'at least one minute')
        add = (self.add_price, self.add_scale, self.add_qty)
        req(all(x is None for x in add) or all(x is not None for x in add), p + '.add_price',
            'add price, scale and qty come together')
        total = self.entry_qty
        if self.add_price is not None:
            r.check_price(self.add_price, p + '.add_price')
            req(s * (self.add_price - self.stop_price) > 0, p + '.add_price', 'at or beyond the stop')
            req(self.add_price != self.entry_price, p + '.add_price', 'at the entry price')
            positive(self.add_scale, p + '.add_scale')
            req(self.add_qty == r.quantize_qty(CTX.multiply(self.add_scale, self.entry_qty), Rounding.DOWN),
                p + '.add_qty', 'is not floor(scale x entry_qty) at the step')
            r.check_qty(self.add_qty, p + '.add_qty')
            req(CTX.multiply(self.add_price, self.add_qty) >= r.min_notional, p + '.add_qty', 'below min notional')
            total = CTX.add(total, self.add_qty)
        req(total <= r.max_qty, p + '.add_qty', 'entry + add above the venue max_qty: the stop could not cover it')
        req((self.tp1_frac is None) == (self.tp1_offset is None), p + '.tp1_frac', 'TP1 fraction and offset come together')
        if self.tp1_frac is not None:
            req(ZERO < self.tp1_frac < ONE, p + '.tp1_frac', 'must be in (0, 1)')
            positive(self.tp1_offset, p + '.tp1_offset')
        if self.tp2_offset is not None:
            positive(self.tp2_offset, p + '.tp2_offset')
            req(self.tp1_offset is None or self.tp2_offset > self.tp1_offset, p + '.tp2_offset', 'not beyond TP1')
        req(not self.be_after_tp1 or self.tp1_frac is not None, p + '.be_after_tp1', 'needs a TP1')
        if self.time_exit_candles is not None:
            check_int(self.time_exit_candles, p + '.time_exit_candles')
            req(self.time_exit_candles >= 1, p + '.time_exit_candles', 'at least one candle')
        if self.trail_offset is not None:
            positive(self.trail_offset, p + '.trail_offset')
        positive(self.risk_cap, p + '.risk_cap')
        risk = planned_risk(self)
        req(risk <= self.risk_cap, p + '.risk_cap', f'planned risk {risk} (add reserved) above the cap {self.risk_cap}')

    @property
    def symbol(self):
        return self.rules.symbol

    @property
    def has_add(self):
        return self.add_price is not None

    @property
    def add_is_pyramid(self):
        return self.has_add and sgn(self.side) * (self.add_price - self.entry_price) > 0

    @property
    def tf_ms(self):
        return self.candle_seconds * 1000


def planned_risk(plan):
    """Worst planned loss of the plan if every level fills and the stop is hit: entry, the RESERVED add (filled with
    adverse slippage), the stop exit with slippage, and the taker fee on every one of those fills. Exact arithmetic."""
    return _risk(plan.side, plan.costs, plan.stop_price,
                 [(plan.entry_qty, plan.entry_price)] +
                 ([(plan.add_qty, market_fill(plan.add_price, plan.side, plan.costs.slip, opening=True))]
                  if plan.add_price is not None else []))


def _risk(side, costs, stop, legs):
    s = sgn(side)
    exit_px = market_fill(stop, side, costs.slip, opening=False)
    risk, notional, qty = ZERO, ZERO, ZERO
    for q, px in legs:
        risk = CTX.add(risk, CTX.multiply(q, CTX.multiply(s, CTX.subtract(px, exit_px))))
        notional = CTX.add(notional, CTX.multiply(q, px))
        qty = CTX.add(qty, q)
    notional = CTX.add(notional, CTX.multiply(qty, exit_px))
    return CTX.add(risk, CTX.multiply(costs.taker_fee, notional))


class PlanNote(enum.StrEnum):
    ADD_BELOW_MIN = 'add_below_min'      # floor(scale x qty) is 0 / below min_qty / below min_notional: no add
    ADD_OVER_CAP = 'add_over_cap'        # the reserved add would break the risk cap: no add


@record
class PlanBuild(Record):
    plan: ManagementPlan
    notes: tuple[PlanNote, ...]


def build_plan(*, rules, side, entry_price, entry_qty, entry_candle_open_ms, candle_seconds, stop_price, risk_cap, costs,
               add_price=None, add_scale=None, tp1_frac=None, tp1_offset=None, tp2_offset=None, be_after_tp1=False,
               time_exit_candles=None, trail_offset=None, disabled_by_default=True, mechanics_only=True):
    """The plan at the confirmed entry fill. Pure; raises PlanRefused when the entry alone is above the cap."""
    base = dict(rules=rules, side=side, entry_price=entry_price, entry_qty=entry_qty,
                entry_candle_open_ms=entry_candle_open_ms, candle_seconds=candle_seconds, stop_price=stop_price,
                tp1_frac=tp1_frac, tp1_offset=tp1_offset, tp2_offset=tp2_offset, be_after_tp1=be_after_tp1,
                time_exit_candles=time_exit_candles, trail_offset=trail_offset, risk_cap=risk_cap, costs=costs,
                disabled_by_default=disabled_by_default, mechanics_only=mechanics_only)
    entry_risk = _risk(side, costs, stop_price, [(entry_qty, entry_price)])
    if entry_risk > risk_cap:
        raise PlanRefused('ManagementPlan.risk_cap', f'the entry alone risks {entry_risk} > cap {risk_cap}')
    notes = []
    if add_price is not None:
        req(add_scale is not None, 'build_plan.add_scale', 'an add needs a scale')
        q = rules.quantize_qty(CTX.multiply(add_scale, entry_qty), Rounding.DOWN)
        if q < rules.min_qty or CTX.multiply(add_price, q) < rules.min_notional:
            notes.append(PlanNote.ADD_BELOW_MIN)
        else:
            fill = market_fill(add_price, side, costs.slip, opening=True)
            if _risk(side, costs, stop_price, [(entry_qty, entry_price), (q, fill)]) > risk_cap:
                notes.append(PlanNote.ADD_OVER_CAP)
            else:
                return PlanBuild(plan=ManagementPlan(add_price=add_price, add_scale=add_scale, add_qty=q, **base),
                                 notes=())
    return PlanBuild(plan=ManagementPlan(add_price=None, add_scale=None, add_qty=None, **base), notes=tuple(notes))


# ------------------------------------------------------------------------------------------------------- admission
class AdmissionVerdict(enum.StrEnum):
    ADMITTED = 'admitted'
    COST_TO_STOP = 'cost_to_stop'         # round-trip cost above max_cost_r of R (handoff: needs a registry code)
    STOP_WRONG_SIDE = 'stop_wrong_side'


@record
class Admission(Record):
    verdict: AdmissionVerdict
    cost_r: Decimal | None                # round-trip cost in R (rounded for display; the veto compares exactly)

    @property
    def admitted(self):
        return self.verdict is AdmissionVerdict.ADMITTED


def admit_entry(*, side, entry_price, stop_price, costs, max_cost_r=DEFAULT_MAX_COST_R):
    """Cost-to-stop veto: refuse when (2 x fee + 2 x slip) x entry > max_cost_r x |entry - stop| (exact comparison)."""
    positive(entry_price, 'admit_entry.entry_price')
    positive(stop_price, 'admit_entry.stop_price')
    req(max_cost_r > 0, 'admit_entry.max_cost_r', 'must be > 0')
    dist = CTX.multiply(sgn(side), CTX.subtract(entry_price, stop_price))
    if dist <= 0:
        return Admission(verdict=AdmissionVerdict.STOP_WRONG_SIDE, cost_r=None)
    cost = CTX.multiply(CTX.multiply(2, CTX.add(costs.taker_fee, costs.slip)), entry_price)
    cost_r = DIV.divide(cost, dist)
    ok = cost <= CTX.multiply(max_cost_r, dist)
    return Admission(verdict=AdmissionVerdict.ADMITTED if ok else AdmissionVerdict.COST_TO_STOP, cost_r=cost_r)
