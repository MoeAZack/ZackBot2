"""The production account-reads shim exposes the Runner's `mark_price(symbol)` (Codex, facd4b6 review): the testnet
TestnetAccountReader answers (MarkQuote(symbol, price, at_ms),) - not a bare price - and the old shim exposed no mark,
so on real testnet the F3 / guard fallback always had no mark and went straight to the emergency close (the TNET
harness adapted it only in nc-tnet01 b312b82). The shim maps the quote to (price,) only when it can show the price is
current (its server time inside the freshness window of the read), else a typed UNKNOWN - never an assumed fresh
price. MarkQuote below is a VERBATIM mirror of nc-venue-testnet newcore/venue/testnet_venue.py (pinned by field
names, and against the real class once the venue package is on the branch)."""
import dataclasses
from decimal import Decimal as D

import pytest

from newcore.ports.venue import ReadKind, ReadOutcome
from newcore.runner.testnet_hook import MARK_FIELDS, MARK_MAX_AGE_MS, MARK_MAX_AHEAD_MS, AccountReadsShim

NOW = 1_700_000_000_000


@dataclasses.dataclass(frozen=True)
class MarkQuote:
    symbol: str
    price: D
    at_ms: int                   # Binance server time of the mark


def test_mark_field_contract():
    assert tuple(f.name for f in dataclasses.fields(MarkQuote)) == MARK_FIELDS
    try:
        from newcore.venue import testnet_venue as real
    except ImportError:
        pytest.skip('newcore.venue is not merged on this branch: the mirror above is pinned from nc-venue-testnet')
    assert tuple(f.name for f in dataclasses.fields(real.MarkQuote)) == MARK_FIELDS
    assert (real.MARK_MAX_AGE_MS, real.MARK_MAX_AHEAD_MS) == (MARK_MAX_AGE_MS, MARK_MAX_AHEAD_MS)


class Reader:
    """TestnetAccountReader.mark_price's shape: OK (MarkQuote,) / REJECTED / UNKNOWN, observed_at_ms = the read."""

    def __init__(self, answer):
        self.answer = answer

    def mark_price(self, symbol):
        return self.answer(symbol)


def ok(*quotes):
    return lambda symbol: ReadOutcome(kind=ReadKind.OK, observed_at_ms=NOW, value=tuple(quotes))


def test_a_fresh_quote_is_the_bare_decimal_mark():
    r = AccountReadsShim(Reader(ok(MarkQuote('SOLUSDT', D('101.25'), NOW - 1_000)))).mark_price('SOLUSDT')
    assert r.kind is ReadKind.OK and r.value == (D('101.25'),) and r.observed_at_ms == NOW


@pytest.mark.parametrize('quote, detail', [
    (MarkQuote('SOLUSDT', D('101'), NOW - MARK_MAX_AGE_MS - 1), 'stale_mark'),
    (MarkQuote('SOLUSDT', D('101'), NOW + MARK_MAX_AHEAD_MS + 1), 'future_mark'),
    (MarkQuote('BTCUSDT', D('101'), NOW), 'unrepresentable'),               # another symbol
    (MarkQuote('SOLUSDT', D('0'), NOW), 'unrepresentable'),
    (MarkQuote('SOLUSDT', D('NaN'), NOW), 'unrepresentable'),
    (MarkQuote('SOLUSDT', 101.0, NOW), 'unrepresentable'),                  # a float is not a venue price
    (MarkQuote('SOLUSDT', D('101'), None), 'unrepresentable'),              # no server time: freshness unprovable
])
def test_a_quote_that_cannot_be_shown_current_is_unknown(quote, detail):
    r = AccountReadsShim(Reader(ok(quote))).mark_price('SOLUSDT')
    assert r.kind is ReadKind.UNKNOWN and r.detail == detail and r.value is None


def test_shapes_and_failures():
    two = ok(MarkQuote('SOLUSDT', D('1'), NOW), MarkQuote('SOLUSDT', D('2'), NOW))
    assert AccountReadsShim(Reader(two)).mark_price('SOLUSDT').kind is ReadKind.UNKNOWN
    assert AccountReadsShim(Reader(ok())).mark_price('SOLUSDT').kind is ReadKind.UNKNOWN
    assert AccountReadsShim(Reader(ok(D('101')))).mark_price('SOLUSDT').kind is ReadKind.UNKNOWN   # a bare price row
    for down in (ReadOutcome(kind=ReadKind.UNKNOWN, observed_at_ms=NOW, detail='stale_mark'),
                 ReadOutcome(kind=ReadKind.REJECTED, observed_at_ms=NOW, error_code=-1121, detail='bad symbol')):
        r = AccountReadsShim(Reader(lambda s, d=down: d)).mark_price('SOLUSDT')
        assert r is down                                                     # passed through, never a price

    class NoMark:
        pass
    assert AccountReadsShim(NoMark()).mark_price('SOLUSDT').detail == 'no_mark_read'


def test_the_runner_reads_the_mark_through_the_shim():
    """Runner._current_mark (F3 / guard fallback level) gets the Decimal from a fresh quote and None from a stale one."""
    from newcore.runner.runner import Runner

    class Probe:
        venue = object()                                                     # no mark on the venue port

        def __init__(self, reads):
            self.reads = reads
    fresh = AccountReadsShim(Reader(ok(MarkQuote('SOLUSDT', D('99.5'), NOW))))
    stale = AccountReadsShim(Reader(ok(MarkQuote('SOLUSDT', D('99.5'), NOW - MARK_MAX_AGE_MS - 1))))
    assert Runner._current_mark(Probe(fresh), 'SOLUSDT') == D('99.5')
    assert Runner._current_mark(Probe(stale), 'SOLUSDT') is None
