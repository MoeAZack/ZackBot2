"""Outcome projection: one TradeOutcome per closed lot, and a run summary (slice plan O5: derived, no NC-01 record).

Truth sources: the journal fold (which intents opened / closed the lot, why) and the venue's fill and funding reads
(price, fee and funding as the venue booked them). Nothing is estimated.

    gross   sum of the closing fills' realized pnl (venue)
    fees    entry + exit fill fees (venue, positive = paid)
    funding funding the venue charged (symbol, side) while the lot was open (positive = paid)
    pnl     gross - fees - funding
    R       pnl / risk_usd, risk_usd = initial qty x |entry price - stop price| (the money the stop put at risk; equal
            to the legacy equity x risk when the size is not floored or capped, e.g. every golden case)
Exit reason: exit.stop for a protective stop fill, otherwise the closing intent's reason (exit.exit_signal,
exit.stop_failed); `exit_code` is its zb-golden/1 projection (STOP_HIT / SIGNAL_EXIT / STOP_FAILED).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, localcontext

from newcore.domain import ReasonCode
from newcore.domain.reasons import golden_exit
from newcore.ports.venue import ReadKind

ZERO = Decimal(0)
RCTX = Context(prec=28)                     # R and the exit VWAP (reporting precision)
ECTX = Context(prec=60)                     # sums of venue amounts: exact at this precision


@dataclass(frozen=True, slots=True)
class TradeOutcome:
    symbol: str
    side: str
    lot_id: str
    entry_intent_id: str
    exit_intent_ids: tuple
    signal_close_ms: int              # the entry signal candle's close (DecisionKey.candle_close_ms)
    entry_ms: int                     # entry fill time (venue)
    exit_ms: int                      # last exit fill time (venue)
    exit_signal_close_ms: int | None  # the exit signal candle's close (signal exits)
    qty: Decimal
    entry_price: Decimal
    exit_price: Decimal               # qty-weighted over the closing fills
    stop_price: Decimal
    risk_distance: Decimal
    risk_usd: Decimal
    gross: Decimal
    fees: Decimal
    funding: Decimal
    pnl: Decimal
    r: Decimal
    exit_reason: ReasonCode
    exit_code: str | None
    reason_codes: tuple               # (entry reason, exit reason) values


def _read(r, what):
    if r.kind is not ReadKind.OK:
        raise LookupError(f'{what}: venue read {r.kind}')
    return r.value


def trade_outcome(fold, lot, venue, account_reads, tf_ms, stop_price) -> TradeOutcome:
    with localcontext(ECTX):
        return _trade_outcome(fold, lot, venue, account_reads, tf_ms, stop_price)


def _trade_outcome(fold, lot, venue, account_reads, tf_ms, stop_price):
    sym, side = lot.symbol, lot.side
    entry_fills = _read(venue.fills(sym, lot.entry.final.exchange_order_id), 'entry fills')
    for a in lot.add_fills:                                         # ADD fills: opening fees too (none in S1)
        entry_fills += _read(venue.fills(sym, a.exchange_order_id), 'add fills')
    exit_fills = []
    for c in lot.closings:
        exit_fills += _read(venue.fills(sym, c.exchange_order_id), 'exit fills')
    fees = sum((f.fee for f in entry_fills + tuple(exit_fills)), ZERO)
    gross = sum((f.realized_pnl for f in exit_fills), ZERO)
    qty_out = sum((f.qty for f in exit_fills), ZERO)
    exit_px = RCTX.divide(sum((f.qty * f.price for f in exit_fills), ZERO), qty_out)
    entry_ms = min(f.at_ms for f in entry_fills)
    exit_ms = max(f.at_ms for f in exit_fills)
    last = lot.closings[-1]
    reason = last.reason
    # a stop fills DURING its candle (fill time = that candle's open), so that candle's funding is the lot's too; a
    # market close fills at a candle open before the candle is played
    end = exit_ms + tf_ms if reason is ReasonCode.EXIT_STOP else exit_ms
    funding = sum((row.amount for row in _read(account_reads.funding(sym, side, entry_ms, end), 'funding')), ZERO)
    dist = abs(lot.entry_price - stop_price)              # R anchors on the entry fill (== avg without adds)
    risk_usd = lot.initial_qty * dist
    pnl = gross - fees - funding
    close_iv = fold.intents[last.intent_id]
    key = fold.decisions[close_iv.intent.decision_id].key
    return TradeOutcome(
        symbol=sym, side=side, lot_id=lot.lot_id, entry_intent_id=lot.entry.intent_id,
        exit_intent_ids=tuple(c.intent_id for c in lot.closings), signal_close_ms=fold.entry_key(lot.entry).candle_close_ms,
        entry_ms=entry_ms, exit_ms=exit_ms, exit_signal_close_ms=None if key is None else key.candle_close_ms,
        qty=lot.initial_qty, entry_price=lot.entry_price, exit_price=exit_px, stop_price=stop_price, risk_distance=dist,
        risk_usd=risk_usd, gross=gross, fees=fees, funding=funding, pnl=pnl,
        r=RCTX.divide(pnl, risk_usd) if risk_usd > 0 else ZERO, exit_reason=reason, exit_code=golden_exit(reason),
        reason_codes=(str(lot.entry.intent.reason), str(reason)))


@dataclass(frozen=True, slots=True)
class RunSummary:
    trades: int
    wins: int
    sum_r: Decimal
    mean_r: Decimal
    pnl: Decimal
    fees: Decimal
    funding: Decimal
    equity_end: Decimal | None
    open_lots: int
    ownership: str | None
    mode: str
    counters: dict
    stop_routes: str = '-'                   # H4: 'SYMBOL:SIDE:classic|algo' of each open lot's carrying stop

    def as_dict(self):
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in
                ((f, getattr(self, f)) for f in self.__dataclass_fields__)}


def summarize(trades, *, equity_end, open_lots, ownership, mode, counters, stop_routes='-') -> RunSummary:
    with localcontext(ECTX):
        return _summarize(trades, equity_end, open_lots, ownership, mode, counters, stop_routes)


def _summarize(trades, equity_end, open_lots, ownership, mode, counters, stop_routes='-'):
    n = len(trades)
    sum_r = sum((t.r for t in trades), ZERO)
    return RunSummary(trades=n, wins=sum(1 for t in trades if t.pnl > 0), sum_r=sum_r,
                      mean_r=RCTX.divide(sum_r, n) if n else ZERO, pnl=sum((t.pnl for t in trades), ZERO),
                      fees=sum((t.fees for t in trades), ZERO), funding=sum((t.funding for t in trades), ZERO),
                      equity_end=equity_end, open_lots=open_lots, ownership=ownership, mode=mode, counters=dict(counters),
                      stop_routes=stop_routes)
