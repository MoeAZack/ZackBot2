"""Where a runner-driven scenario runs. Both targets give the driver the same surface:

    kind                     'fake' | 'testnet'
    prepare(spec, account)   fresh per scenario: port (VenuePort), reads (AccountReads), bars (BarSource),
                             rules {symbol: InstrumentRules}, journal (fresh MemoryJournal)
    next_close() -> ms       move to the next 1m candle close and return it (the cycle time)
    arm_fault(on, kind, code)
    reopen_journal()         a restart: the journal reopened from its durable events
    raw                      the unwrapped venue (final truth + cleanup read and act on it, never through the bound)
    preflight(symbols)       PreflightResult-like (ok, refusals, baseline)
    environment              Environment of the run's Account

FakeTarget: FakeVenue + CsvBarSource over a synthetic 1m market built from the spec (flat at `fake.price`, a narrow
wick; an await_exit step with fake_gap_pct gaps the market on the candle AFTER the first await cycle, so a resting stop
fills at that open). CI, no network, no keys.
TestnetTarget: newcore.venue.factory:build_testnet(config, http=HttpFaults(http), ...) - the runner's own testnet
factory, with its instrument_rules and server-aligned clock; the cycle waits for the real candle close (+ settle).
`http` is injected: the CLI passes TestnetHttpSender, the tests a fake Binance (no network).
"""
from decimal import Decimal

from newcore.adapters import CsvBarSource, FakeVenue, MemoryJournal
from newcore.domain import Capability, Environment, InstrumentId, InstrumentRules, Venue
from newcore.ports.bars import Bar
from newcore.runner.testnet_hook import AccountReadsShim
from newcore.venue.tnet import PreflightResult, tnet_cleanup, tnet_preflight

from .seams import BoundExceeded, DeadlineExceeded, HttpFaults, NoFaults, PortFaults

TF_MS = 60_000
FAKE_T0 = 1_759_917_600_000            # 2025-10-08 10:00 UTC, a minute boundary
WARMUP = 20                            # candles before the first cycle (ATR14 needs 15)
SLACK = 6                              # candles after the last planned cycle (cleanup closes need a next open)
from .rspec import MAX_SETTLE_MS  # noqa: E402  (the bounded testnet wait after a candle close)
CLEANUP_SETTLE_S = 2.0                 # testnet teardown: a clean read is re-read this much later before CLEAN
D = Decimal


def fake_rules(symbol):
    """SOL-like filters (0.01 tick / step, 5 USDT minimum notional): the sizing floor binds like on the venue."""
    return InstrumentRules(instrument=InstrumentId(venue=Venue.BINANCE_USDM, symbol=symbol), tick_size=D('0.01'),
                           step_size=D('0.01'), min_qty=D('0.01'), max_qty=D('1000000'), min_notional=D('5'),
                           capabilities=(Capability.HEDGE_MODE, Capability.STOP_MARKET, Capability.REDUCE_ONLY))


def plan_ticks(spec):
    """(total cycles, {candle index: gap pct}) of a spec on the fake market (cycle k closes candle WARMUP - 1 + k)."""
    k, gaps = 0, {}
    for st in spec['steps']:
        op = st['op']
        if op in ('enter', 'close'):
            k += 1
        elif op == 'tick':
            k += st['n']
        elif op == 'await_exit':
            if 'fake_gap_pct' in st:
                gaps[WARMUP + k] = D(st['fake_gap_pct'])
            k += st['max_ticks']
    return k, gaps


def fake_candles(spec, price):
    total, gaps = plan_ticks(spec)
    n = WARMUP + total + SLACK
    px, wick = D(price), D(price) / 1000
    out = []
    for i in range(n):
        if i in gaps:
            px = (px * (1 + gaps[i] / 100)).quantize(D('0.01'))
            wick = px / 1000
        out.append(Bar(open_ms=FAKE_T0 + i * TF_MS, close_ms=FAKE_T0 + (i + 1) * TF_MS, open=px, high=px + wick,
                       low=px - wick, close=px, volume=D('1000')))
    return tuple(out)


class FakeTarget:
    kind = 'fake'
    environment = Environment.SIM

    def prepare(self, spec, account, portfolio_id):
        sym = spec['symbol']
        fake = spec.get('fake', {})
        self.candles = fake_candles(spec, fake.get('price', '100'))
        self.venue = FakeVenue({sym: self.candles}, TF_MS, equity=D(fake.get('equity', '5000')))
        if fake.get('classic_stops') == 'refuse':
            self.venue.refuse_classic_stops()
        self.faults = PortFaults(self.venue)
        self.port, self.reads, self.raw = self.faults, self.venue, self.venue
        self.bars = CsvBarSource({sym: self.candles}, TF_MS)
        self.rules = {sym: fake_rules(sym)}
        self.journal = MemoryJournal(account.account_id, portfolio_id)
        self._i = WARMUP - 1

    def preflight(self, symbols, **kw):
        return PreflightResult(True, (), None, (), (), {})

    def next_close(self, deadline_s=None):
        """No wall time passes on the fake market; the driver still re-checks its deadline after this returns."""
        if self._i >= len(self.candles) - 1:
            raise BoundExceeded('the fake market is exhausted')
        t = self.candles[self._i].close_ms
        self._i += 1
        self.venue.advance_to(t)
        return t

    def arm_fault(self, on, kind, code=None):
        self.faults.arm(on, kind, code)

    @property
    def injected(self):
        return list(self.faults.injected)

    def reopen_journal(self):
        self.journal = self.journal.reopen()
        return self.journal

    def cleanup(self, symbols, run_id, baseline=None):
        return tnet_cleanup(self.raw, symbols, run_id=run_id, baseline=baseline)


def check_settle_ms(v):
    if type(v) is not int or not 0 <= v <= MAX_SETTLE_MS:
        raise ValueError(f'settle_ms must be an int in 0..{MAX_SETTLE_MS}')
    return v


class TestnetTarget:
    __test__ = False
    kind = 'testnet'
    environment = Environment.TESTNET

    def __init__(self, config, *, http, sleep, local_clock=None, store=None, scrubber=None, settle_ms=1500,
                 factory=None, recorder=None, faults=True):
        """recorder: callable(http) -> CassetteRecorder, wrapped OUTSIDE the fault seam (the cassette holds what the
        transport saw, injected faults included, so a replay needs no seam). faults=False: a replay (no injection)."""
        if factory is None:
            from newcore.venue.factory import build_testnet as factory
        self.seam = HttpFaults(http) if faults else NoFaults(http)
        self.recorder = recorder(self.seam) if recorder is not None else None
        wire = self.recorder if self.recorder is not None else self.seam
        check_settle_ms(settle_ms)
        kw = {} if scrubber is None else {'scrubber': scrubber}
        parts = factory(config, http=wire, local_clock=local_clock, store=store, **kw)
        self.config = config
        self.venue, self.reader = parts['venue'], parts['account_reader']
        self.bars, self.clock = parts['bars'], parts['clock']
        self.instrument_rules = parts['instrument_rules']
        self.binding_digest = parts.get('binding_digest')
        self.reads = AccountReadsShim(self.reader)
        self.raw = self.venue
        self.sleep, self.settle_ms, self.default_settle_ms = sleep, settle_ms, settle_ms

    def prepare(self, spec, account, portfolio_id):
        sym = spec['symbol']
        if sym not in self.instrument_rules:
            raise BoundExceeded(f'{sym} is not in the factory rules (config.symbols)')
        self.port = self.venue
        self.settle_ms = spec['bound'].get('settle_ms', self.default_settle_ms)
        self.rules = {sym: self.instrument_rules[sym]}
        self.journal = MemoryJournal(account.account_id, portfolio_id)

    def preflight(self, symbols, **kw):
        return tnet_preflight(self.venue, self.reader, symbols, **kw)

    def next_close(self, deadline_s=None):
        """Wait for the next candle close + settle. The wait is bounded by the caller's remaining deadline: if it would
        end past it, DeadlineExceeded is raised BEFORE sleeping (never a sleep past the deadline)."""
        now = self.clock()
        boundary = (now // TF_MS + 1) * TF_MS
        wait_s = (boundary + self.settle_ms - now) / 1000
        if deadline_s is not None and wait_s > deadline_s:
            raise DeadlineExceeded(f'the next candle close + settle is {wait_s:.1f} s away, only {max(deadline_s, 0):.1f}'
                                   f' s of the scenario deadline are left; nothing was sent')
        self.sleep(wait_s)
        return boundary

    def arm_fault(self, on, kind, code=None):
        self.seam.arm(on, kind, code)

    @property
    def injected(self):
        return list(self.seam.injected)

    def reopen_journal(self):
        self.journal = self.journal.reopen()
        return self.journal

    def cleanup(self, symbols, run_id, baseline=None):
        """A clean read must be confirmed CLEANUP_SETTLE_S later (venue read lag); not bounded by the deadline."""
        return tnet_cleanup(self.raw, symbols, run_id=run_id, baseline=baseline, confirm_reads=2,
                            settle_s=CLEANUP_SETTLE_S, sleep=self.sleep)
