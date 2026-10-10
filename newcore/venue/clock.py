"""Server-time offset for SIGNED timestamps.

Binance refuses a signed request when its timestamp is more than 1000 ms ahead of server time, or older than
recvWindow (-1021). OffsetClock is the `clock` the transport is given: local ms + a measured offset.

- measure_offset() takes a BOUNDED sample (1..5 unsigned GET /fapi/v1/time calls), keeps the lowest-RTT sample, and
  refuses implausible answers (RTT above max_rtt_ms, |offset| above max_abs_offset_ms, a non-monotonic local clock).
- The applied offset is biased by -ceil(RTT/2): the true offset is only known to within +-RTT/2, so the timestamp may
  lag server time by up to one RTT (well inside recvWindow) but never runs ahead of it.
- A -1021 refusal does NOT retry anything: observe(outcome) only marks needs_resync. The caller (Runner) decides when
  to call resync() and whether to send again. The transport itself never resends.
"""
from dataclasses import dataclass

TIMESTAMP_REFUSAL_CODE = -1021


def system_clock_ms():
    """The wall clock in integer ms (for the S5 harness / Runner; tests inject their own)."""
    import time
    return time.time_ns() // 1_000_000


def is_timestamp_refusal(outcome):
    err = getattr(outcome, 'error', None)
    return err is not None and getattr(err, 'code', None) == TIMESTAMP_REFUSAL_CODE


@dataclass(frozen=True)
class OffsetMeasurement:
    ok: bool
    offset_ms: object          # int | None: server - local midpoint of the chosen sample
    rtt_ms: object             # int | None
    samples: tuple             # ((offset_ms, rtt_ms), ...) of the valid samples
    attempts: int
    reason: object = None      # None | 'no_valid_sample' | 'implausible_offset'

    @property
    def applied_offset_ms(self):
        """The offset OffsetClock uses: biased so a signed timestamp never runs ahead of server time."""
        return None if not self.ok else self.offset_ms - (self.rtt_ms + 1) // 2


def _ms(v):
    if type(v) is not int or v <= 0:
        raise ValueError('local clock must return a positive int of ms')
    return v


def measure_offset(transport, local_clock, *, samples=3, max_rtt_ms=2000, max_abs_offset_ms=3_600_000):
    if type(samples) is not int or not 1 <= samples <= 5:
        raise ValueError('samples must be an int in 1..5')
    if type(max_rtt_ms) is not int or not 1 <= max_rtt_ms <= 10_000:
        raise ValueError('max_rtt_ms must be an int in 1..10000')
    if type(max_abs_offset_ms) is not int or max_abs_offset_ms < 0:
        raise ValueError('max_abs_offset_ms must be a non-negative int')
    good = []
    for _ in range(samples):
        t0 = _ms(local_clock())
        out = transport.server_time()
        t1 = _ms(local_clock())
        if not out.ok or t1 < t0 or t1 - t0 > max_rtt_ms:
            continue
        good.append((out.value - (t0 + t1) // 2, t1 - t0))
    if not good:
        return OffsetMeasurement(False, None, None, (), samples, 'no_valid_sample')
    offset, rtt = min(good, key=lambda s: s[1])
    if abs(offset) > max_abs_offset_ms:
        return OffsetMeasurement(False, offset, rtt, tuple(good), samples, 'implausible_offset')
    return OffsetMeasurement(True, offset, rtt, tuple(good), samples)


class OffsetClock:
    """callable() -> local ms + applied offset. Starts unsynced (offset 0, needs_resync True)."""

    def __init__(self, local_clock):
        if not callable(local_clock):
            raise ValueError('local_clock must be callable')
        self._local = local_clock
        self._offset = 0
        self._needs_resync = True
        self.last_measurement = None

    def __call__(self):
        return _ms(self._local()) + self._offset

    @property
    def offset_ms(self):
        return self._offset

    @property
    def needs_resync(self):
        return self._needs_resync

    def apply(self, measurement):
        """Use an OK measurement; a failed one changes nothing (the old offset and the resync flag stay)."""
        self.last_measurement = measurement
        if not isinstance(measurement, OffsetMeasurement) or not measurement.ok:
            return False
        self._offset = measurement.applied_offset_ms
        self._needs_resync = False
        return True

    def observe(self, outcome):
        """Feed every signed outcome here. A -1021 refusal marks needs_resync and returns True. Nothing is resent."""
        if is_timestamp_refusal(outcome):
            self._needs_resync = True
            return True
        return False

    def resync(self, transport, **kw):
        """Measure with the RAW local clock (never with this clock's own offset) and apply. Returns the measurement."""
        m = measure_offset(transport, self._local, **kw)
        self.apply(m)
        return m

    def __repr__(self):
        return f'OffsetClock(offset_ms={self._offset}, needs_resync={self._needs_resync})'
