"""NC-03 S5: server-time offset measurement, the offset clock, and -1021 as a resync signal (never an auto-retry)."""
import json
from decimal import Decimal as D

import pytest

from newcore.venue.clock import OffsetClock, OffsetMeasurement, is_timestamp_refusal, measure_offset, system_clock_ms
from newcore.venue.credentials import StaticCredentials
from newcore.venue.outcomes import OrderOutcomeKind as K, ReadKind, ReadOutcome
from newcore.venue.transport import BinanceTestnetTransport, PositionMode

from ncv_support import DUMMY_KEY, DUMMY_SECRET, FakeHttp, query_pairs, raw


class Ticker:
    """A local clock that advances by scripted steps on each read (to fake request round trips)."""

    def __init__(self, start, steps):
        self.now, self.steps = start, list(steps)

    def __call__(self):
        v = self.now
        if self.steps:
            self.now += self.steps.pop(0)
        return v


class TimeVenue:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    def server_time(self):
        self.calls += 1
        a = self.answers.pop(0)
        return a if isinstance(a, ReadOutcome) else ReadOutcome(ReadKind.OK, 'server_time', value=a)


def test_offset_from_lowest_rtt_sample():
    # local reads: (1000, 1400) rtt 400 ; (2000, 2100) rtt 100 ; (3000, 3300) rtt 300
    clock = Ticker(1000, [400, 600, 100, 900, 300])
    v = TimeVenue(1000 + 200 + 5000, 2050 + 5003, 3150 + 4990)
    m = measure_offset(v, clock, samples=3)
    assert m.ok and v.calls == 3
    assert (m.offset_ms, m.rtt_ms) == (5003, 100) and len(m.samples) == 3
    assert m.applied_offset_ms == 5003 - 50                      # biased by ceil(rtt/2): never ahead of the server


def test_failed_and_slow_samples_are_dropped():
    clock = Ticker(1000, [10, 0, 5000, 0, 20])
    unknown = ReadOutcome(ReadKind.UNKNOWN, 'server_time', unknown_reason='timeout')
    m = measure_offset(TimeVenue(unknown, 999999, 1000 + 5000 + 10 + 5000 + 10), clock, samples=3, max_rtt_ms=2000)
    assert m.ok and m.rtt_ms == 20 and len(m.samples) == 1


def test_no_valid_sample_and_implausible_offset():
    clock = Ticker(1000, [10] * 10)
    unknown = ReadOutcome(ReadKind.UNKNOWN, 'server_time', unknown_reason='timeout')
    m = measure_offset(TimeVenue(unknown, unknown), clock, samples=2)
    assert not m.ok and m.reason == 'no_valid_sample' and m.applied_offset_ms is None
    m = measure_offset(TimeVenue(10_000_000), Ticker(1000, [10, 10]), samples=1, max_abs_offset_ms=60_000)
    assert not m.ok and m.reason == 'implausible_offset'


def test_backwards_local_clock_sample_is_dropped():
    clock = Ticker(5000, [-100, 0])
    m = measure_offset(TimeVenue(5000), clock, samples=1)
    assert not m.ok and m.reason == 'no_valid_sample'


@pytest.mark.parametrize('kw', [dict(samples=0), dict(samples=6), dict(max_rtt_ms=0), dict(max_abs_offset_ms=-1)])
def test_sample_bounds(kw):
    with pytest.raises(ValueError):
        measure_offset(TimeVenue(), Ticker(1, []), **kw)


def test_bad_local_clock_refused():
    with pytest.raises(ValueError):
        measure_offset(TimeVenue(1), lambda: 1.5, samples=1)


def test_offset_clock_apply_and_failed_measurement_keeps_old():
    oc = OffsetClock(lambda: 1_000_000)
    assert oc() == 1_000_000 and oc.needs_resync
    assert oc.apply(OffsetMeasurement(True, 250, 20, ((250, 20),), 1))
    assert oc() == 1_000_240 and not oc.needs_resync
    assert not oc.apply(OffsetMeasurement(False, None, None, (), 3, 'no_valid_sample'))
    assert oc.offset_ms == 240


def test_minus_1021_marks_resync_without_any_resend():
    local = Ticker(1759917600000, [0] * 50)
    oc = OffsetClock(local)
    http = FakeHttp('err_timestamp')
    t = BinanceTestnetTransport(environment='testnet', http=http, clock=oc, position_mode=PositionMode.HEDGE,
                                credentials=StaticCredentials(DUMMY_KEY, DUMMY_SECRET))
    oc.apply(OffsetMeasurement(True, 0, 0, ((0, 0),), 1))
    out = t.place_market('SOLUSDT', 'BUY', 'LONG', D('1'), 'zb-a', reduce_only=False)
    assert out.kind is K.REJECTED and is_timestamp_refusal(out)
    assert oc.observe(out) is True and oc.needs_resync
    assert len(http.requests) == 1                       # nothing resent by the transport or the clock


def test_resync_then_signed_timestamp_uses_offset():
    local = Ticker(1759917600000, [40, 0, 0, 0])               # one sample: rtt 40 ms
    oc = OffsetClock(local)
    server = 1759917600000 + 20 + 3000                         # server is 3 s ahead of the local midpoint
    http = FakeHttp(raw(200, json.dumps({'serverTime': server}).encode()), 'account_v2')
    t = BinanceTestnetTransport(environment='testnet', http=http, clock=oc, position_mode=PositionMode.HEDGE,
                                credentials=StaticCredentials(DUMMY_KEY, DUMMY_SECRET))
    m = oc.resync(t, samples=1)
    assert m.ok and m.offset_ms == 3000 and oc.offset_ms == 3000 - 20
    assert not http.requests[0].signed                        # the time read is unsigned and uses no offset
    t.account()
    ts = int(dict(query_pairs(http.last))['timestamp'])
    assert ts == local.now + 3000 - 20                         # local + biased offset
    assert ts <= server + (local.now - 1759917600000)          # never ahead of server time


def test_not_a_timestamp_refusal():
    assert not is_timestamp_refusal(ReadOutcome(ReadKind.OK, 'x'))
    oc = OffsetClock(lambda: 1)
    assert oc.observe(ReadOutcome(ReadKind.OK, 'x')) is False


def test_system_clock_is_int_ms():
    v = system_clock_ms()
    assert type(v) is int and v > 1_700_000_000_000
