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
"""
from __future__ import annotations

import importlib

from newcore.adapters.fake_venue import FundingRow
from newcore.ports.venue import ReadKind, ReadOutcome

# the real field layout (nc-venue-testnet c872c6b); the shim reads only the starred ones
EQUITY_FIELDS = ('asset', 'wallet_balance', 'margin_balance', 'available_balance', 'unrealized_pnl')   # *wallet_balance
FUNDING_FIELDS = ('symbol', 'asset', 'amount', 'at_ms', 'tran_id')                                   # *symbol *amount *at_ms


class AccountReadsShim:
    """Runner AccountReads over a TestnetAccountReader-shaped reader.

    equity()                         -> (wallet_balance,): the closed equity the sizing risks a fraction of
    funding(symbol, side, from, to)  -> FundingRow(symbol, side, at_ms, amount): amount = -payment.amount (paid > 0),
                                        rows of that symbol with from < at_ms <= to (account-level rows, symbol None, are
                                        not a position's funding). Binance income rows carry no position side: in hedge mode
                                        with both sides of a symbol open the attribution is ambiguous, and the rows are
                                        attributed to the side asked (reported limitation; one side per symbol in S1/S4).
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
