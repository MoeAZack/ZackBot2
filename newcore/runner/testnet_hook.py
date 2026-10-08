"""The testnet seam of `python -m newcore.run`: a factory hook plus the account-reads shim. No network code lives here.

The TestnetVenue adapter (nc-venue-testnet: newcore/venue/testnet_venue.py) is the transport lane's and is NOT merged
into this branch: the run config names a factory, "module:callable", and the hook calls it with the RunConfig:

    factory(config) -> {'venue': <VenuePort>, 'bars': <BarSource>,
                        'account_reader': <TestnetAccountReader>  (or 'account_reads': already in the Runner's shape)}

The Runner's VenuePort contract with that adapter (handled in runner.py, tested with a fake in
tests/newcore_slice/test_testnet_semantics.py):
  - a duplicate client id is UNKNOWN detail 'duplicate_client_id' -> query by client id (the order exists);
  - a classic stop refused because conditional orders need the algo service is REJECTED detail 'algo_route' -> the
    classic -> algo fallback (also keyed on the transport's codes);
  - a triggered / finished algo stop is KNOWN detail 'algo_triggered' with the CHILD order id; fill truth = fills(child).
Equity and funding are not on the port: TestnetAccountReader (nc-venue-testnet c872c6b, newcore/venue/testnet_venue.py)
gives equity() -> (Equity(asset, wallet_balance, margin_balance, available_balance, unrealized_pnl),) and
funding(start_ms=, end_ms=, symbol=) -> FundingPayment(symbol (None for an account-level row), asset, amount (signed:
negative = paid), at_ms, tran_id), rows in [start_ms, end_ms]. The field names the shim reads are pinned in
EQUITY_FIELDS / FUNDING_FIELDS and checked against the real dataclasses by tests/newcore_slice/test_testnet_semantics.py
(a drift fails there, not on the first testnet cycle). AccountReadsShim maps them onto the Runner's reads.
The mark: TestnetAccountReader.mark_price(symbol) -> (MarkQuote(symbol, price, at_ms = Binance server time),), already
UNKNOWN 'stale_mark' / 'future_mark' outside its freshness window; the Runner (F3 / guard fallback level, poll_marks)
reads `mark_price(symbol).value[0]` as the Decimal mark. The shim re-checks the quote it is handed (one row, this
symbol, a positive price, server time within MARK_MAX_AGE_MS / MARK_MAX_AHEAD_MS of the read) and never passes a
price it cannot show is current: anything else is UNKNOWN, so the fallback has no level rather than a stale one.
"""
from __future__ import annotations

import importlib
import time
from decimal import Decimal

from newcore.adapters.fake_venue import FundingRow
from newcore.ports.venue import ReadKind, ReadOutcome

# the real field layout (nc-venue-testnet c872c6b); the shim reads only the starred ones
EQUITY_FIELDS = ('asset', 'wallet_balance', 'margin_balance', 'available_balance', 'unrealized_pnl')   # *wallet_balance
FUNDING_FIELDS = ('symbol', 'asset', 'amount', 'at_ms', 'tran_id')                                   # *symbol *amount *at_ms
MARK_FIELDS = ('symbol', 'price', 'at_ms')                                                           # all three
MARK_MAX_AGE_MS = 30_000          # = testnet_venue.MARK_MAX_AGE_MS: a mark older than this (server clock) is stale
MARK_MAX_AHEAD_MS = 5_000         # = testnet_venue.MARK_MAX_AHEAD_MS: further ahead of the read is not believed


class AccountReadsShim:
    """Runner AccountReads over a TestnetAccountReader-shaped reader.

    equity()                         -> (wallet_balance,): the closed equity the sizing risks a fraction of
    funding(symbol, side, from, to)  -> FundingRow(symbol, side, at_ms, amount): amount = -payment.amount (paid > 0),
                                        rows of that symbol with from < at_ms <= to (account-level rows, symbol None, are
                                        not a position's funding). Binance income rows carry no position side: in hedge mode
                                        with both sides of a symbol open the attribution is ambiguous, and the rows are
                                        attributed to the side asked (reported limitation; one side per symbol in S1/S4).
    mark_price(symbol)               -> (price,) from the reader's (MarkQuote,), fresh by its server time, else UNKNOWN
                                        (a reader with no mark read: UNKNOWN 'no_mark_read').
    A read that is not OK is passed through unchanged (unknown is never zero)."""

    def __init__(self, reader):
        self.reader = reader

    def equity(self):
        r = self.reader.equity()
        if r.kind is not ReadKind.OK:
            return r
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=r.observed_at_ms, value=(r.value[0].wallet_balance,))

    def funding(self, symbol, side, from_ms, to_ms):
        r = self.reader.funding(start_ms=from_ms + 1, end_ms=to_ms, symbol=symbol)
        if r.kind is not ReadKind.OK:
            return r
        rows = tuple(FundingRow(symbol, side, p.at_ms, -p.amount) for p in r.value
                     if p.symbol == symbol and from_ms < p.at_ms <= to_ms)
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=r.observed_at_ms, value=rows)


    def mark_price(self, symbol):
        read = getattr(self.reader, 'mark_price', None)
        if read is None:
            return ReadOutcome(kind=ReadKind.UNKNOWN, observed_at_ms=int(time.time() * 1000), detail='no_mark_read')
        r = read(symbol)
        if r.kind is not ReadKind.OK:
            return r
        now = r.observed_at_ms

        def unknown(detail):
            return ReadOutcome(kind=ReadKind.UNKNOWN, observed_at_ms=now, detail=detail)
        if not isinstance(r.value, tuple) or len(r.value) != 1:
            return unknown('unrepresentable')
        q = r.value[0]
        try:
            sym, price, at = q.symbol, q.price, q.at_ms
        except AttributeError:
            return unknown('unrepresentable')
        if sym != symbol or not isinstance(price, Decimal) or not price.is_finite() or price <= 0 \
                or type(at) is not int:
            return unknown('unrepresentable')
        if at < now - MARK_MAX_AGE_MS:
            return unknown('stale_mark')
        if at > now + MARK_MAX_AHEAD_MS:
            return unknown('future_mark')
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=now, value=(price,))


def load_factory(spec):
    module, _, name = spec.partition(':')
    return getattr(importlib.import_module(module), name)


def build(config):
    """(venue, bars, account_reads, instrument_rules or None) from the configured factory (newcore.venue.factory:
    build_testnet on nc-venue-testnet returns venue, bars, account_reader and the venue's own instrument_rules)."""
    parts = load_factory(config.factory)(config)
    missing = {'venue', 'bars'} - set(parts)
    if missing or not ({'account_reader', 'account_reads'} & set(parts)):
        raise ValueError(f'the testnet factory must return venue, bars and account_reader / account_reads ({parts!r})')
    reads = parts.get('account_reads') or AccountReadsShim(parts['account_reader'])
    return parts['venue'], parts['bars'], reads, parts.get('instrument_rules')
