"""Management inputs (r3 DRAFT item 7): the immutable inputs of one lot's management step, as journal records.

Codex requires management replay to consume ONLY journal bytes. A management step folds three kinds of input: a
closed candle (open time + OHLC) with the strategy's close request on it, an intra-candle mark price, and the venue's
executions of the lot's orders (normalized userTrades rows: trade id, order id, time, qty, price, fee, fee asset). This
module is the domain type that carries them; events.ManagementInputRecorded journals one in sequence, after the
manage.tick decision it feeds (a fills-only observation names no decision). Replay reads these records and never a
candle store, a venue or a clock.

Nothing here decides anything: the management core / driver does, from exactly these inputs.
"""
from __future__ import annotations

from decimal import Decimal

from .base import Record, check_id, check_symbol, check_text, positive, record, req
from .orders import Side
from .reasons import ReasonCode


@record
class CandleInput(Record):
    """One CLOSED candle of the lot's timeframe, exactly as the strategy read it (never re-fetched on replay)."""
    open_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def _validate(self, p):
        positive(self.low, p + '.low')
        req(self.low <= min(self.open, self.close) and self.high >= max(self.open, self.close), p + '.high',
            'open / close outside the high-low range')


@record
class FillObservation(Record):
    """One venue execution of one of the lot's orders (a normalized userTrades row; the ledger's fee and price truth)."""
    trade_id: str                 # the venue trade id (dedup key)
    exchange_order_id: str        # the venue order it executed
    at_ms: int
    qty: Decimal
    price: Decimal
    fee: Decimal                  # signed as the venue reports it (a rebate is negative)
    fee_asset: str                # the asset the fee is charged in (converted / estimated by the driver, not here)

    def _validate(self, p):
        check_text(self.trade_id, p + '.trade_id', 64)
        check_text(self.exchange_order_id, p + '.exchange_order_id', 64)
        positive(self.qty, p + '.qty')
        positive(self.price, p + '.price')
        check_text(self.fee_asset, p + '.fee_asset', 16)
        req(self.fee_asset.isascii() and self.fee_asset.isalnum() and self.fee_asset.isupper(), p + '.fee_asset',
            'an upper-case asset code')


@record
class ManagementInput(Record):
    account_id: str
    lot_id: str                              # the lot being managed
    decision_id: str | None                  # the manage.tick decision it feeds; None = a fills-only observation
    symbol: str
    side: Side
    candle: CandleInput | None               # a closed-candle tick
    close_request: ReasonCode | None         # the strategy's exit request on that candle (exit.*), else None
    mark_price: Decimal | None               # an intra-candle mark that fired a trigger
    fills: tuple[FillObservation, ...]       # executions observed since the last input, by (at_ms, trade_id)

    def _validate(self, p):
        p = f'{p}[{self.lot_id}]'
        check_id(self.account_id, p + '.account_id', 'acct')
        check_id(self.lot_id, p + '.lot_id', 'lot')
        check_symbol(self.symbol, p + '.symbol')
        req(self.candle is None or self.mark_price is None, p + '.mark_price', 'a candle tick or a mark, not both')
        if self.mark_price is not None:
            positive(self.mark_price, p + '.mark_price')
        if self.close_request is not None:
            req(self.candle is not None, p + '.close_request', 'a close request comes with its closed candle')
            req(self.close_request.namespace == 'exit', p + '.close_request', 'a close request is an exit.* reason')
        stepped = self.candle is not None or self.mark_price is not None
        req(stepped or self.fills, p, 'carries a candle, a mark or fills')
        if stepped:
            check_id(self.decision_id, p + '.decision_id', 'dec')
        else:
            req(self.decision_id is None, p + '.decision_id', 'a fills-only observation feeds no tick decision')
        keys = [(f.at_ms, f.trade_id) for f in self.fills]
        req(all(a < b for a, b in zip(keys, keys[1:])), p + '.fills', 'fills in (at_ms, trade_id) order')
        req(len({f.trade_id for f in self.fills}) == len(self.fills), p + '.fills', 'duplicate trade id')

    @property
    def latest_ms(self):
        """The newest time this input carries (the event cannot be journaled before it)."""
        times = [f.at_ms for f in self.fills] + ([self.candle.open_ms] if self.candle is not None else [])
        return max(times) if times else None
