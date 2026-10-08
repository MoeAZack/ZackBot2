"""S4: one book across symbols (shared equity), max_positions, 3x leverage cap, Cairo-day daily-loss halt (DST), drawdown
kill (HOLD, never auto-resumes), deterministic concurrent-signal ordering, crash / restart with several lots open."""
from decimal import Decimal as D

import pytest

from newcore.adapters import CostModel, CsvBarSource, FakeVenue, MemoryJournal
from newcore.domain import Action, EntriesMode, ReasonCode
from newcore.ports import header_of
from newcore.risk import BookPolicy, BookRisk, cairo_day, cairo_offset_hours
from newcore.runner import InjectedSignals, RunnerConfig
from newcore.runner.book import BookRunner
from slice_helpers import Crash, ACCOUNT_ID, PORTFOLIO_ID, fine_rules, flat_bars, sim_account

H1 = 3_600_000
H4 = 14_400_000
SUMMER = 1_719_792_000_000          # 2024-07-01 00:00 UTC (Cairo UTC+3: midnight = 21:00 UTC)
WINTER = 1_704_067_200_000          # 2024-01-01 00:00 UTC (Cairo UTC+2: midnight = 22:00 UTC)
SYMS = ('AAAUSDT', 'BBBUSDT', 'CCCUSDT', 'DDDUSDT', 'EEEUSDT')


def utc(s):
    from newcore.adapters import parse_utc_ms
    return parse_utc_ms(s)


# ------------------------------------------------------------------------------------------------ Cairo day
@pytest.mark.parametrize('when,day,offset', [
    ('2024-01-01 21:00:00', '2024-01-01', 2), ('2024-01-01 22:00:00', '2024-01-02', 2),     # winter midnight 22:00 UTC
    ('2024-07-01 20:00:00', '2024-07-01', 3), ('2024-07-01 21:00:00', '2024-07-02', 3),     # summer midnight 21:00 UTC
    ('2024-04-25 21:00:00', '2024-04-25', 2),                                                # last winter hour
    ('2024-04-25 22:00:00', '2024-04-26', 3),                                                # DST starts: 00:00 -> 01:00
    ('2024-10-30 21:00:00', '2024-10-31', 3),                                                # last summer midnight
    ('2024-10-31 21:00:00', '2024-10-31', 2),                                                # DST ends: 24:00 -> 23:00
    ('2024-10-31 22:00:00', '2024-11-01', 2)])
def test_cairo_day_is_dst_correct(when, day, offset):
    ms = utc(when)
    assert cairo_day(ms) == day and cairo_offset_hours(ms) == offset


def test_on_the_4h_grid_the_day_split_does_not_depend_on_dst():
    """4h closes are 00/04/08/12/16/20 UTC; Cairo midnight (21:00 / 22:00 UTC) falls inside the 20:00 -> 00:00 candle,
    so the 00:00 UTC close is always the new Cairo day and the 20:00 UTC close the old one."""
    for base in (WINTER, SUMMER):                                    # base = a 00:00 UTC close
        utc_date = lambda ms: f'{ms // 86_400_000}'
        assert cairo_day(base) != cairo_day(base - 4 * H1)            # the 00:00 close opens the new Cairo day
        assert cairo_day(base - 4 * H1) == cairo_day(base - 8 * H1)   # the 20:00 close is still the old one
        assert utc_date(base) != utc_date(base - 4 * H1)              # = the UTC date split, in winter and summer


# ------------------------------------------------------------------------------------------------ pure risk
def test_risk_halt_rolls_with_the_cairo_day_and_kill_needs_resume():
    r = BookRisk(BookPolicy(daily_loss_pct=D('0.03'), kill_drawdown_pct=D('0.10')))
    t = utc('2024-07-01 12:00:00')
    assert [e.kind for e in r.observe(t, D('1000'))] == ['roll']
    assert r.observe(t + H1, D('975')) == [] and not r.halted(t + H1)                    # -2.5%
    assert [e.kind for e in r.observe(t + 2 * H1, D('970'))] == ['halt']                 # -3.0% exactly
    assert r.halted(t + 5 * H1) and r.observe(t + 3 * H1, D('960')) == []                 # once per day
    nxt = utc('2024-07-01 21:00:00')                                                      # Cairo 00:00, July 2
    assert [e.kind for e in r.observe(nxt, D('960'))] == ['roll'] and not r.halted(nxt)
    assert [e.kind for e in r.observe(nxt + H1, D('899'))] == ['halt', 'kill']           # -10.1% from the 1000 peak
    assert [e.kind for e in r.observe(nxt + 30 * H1, D('950'))] == ['roll'] and r.killed  # never by itself
    r.resumed(D('950'))
    assert not r.killed and r.peak == D('950')


def test_capacity():
    assert BookRisk(BookPolicy(max_positions=4)).at_capacity(4)
    assert not BookRisk(BookPolicy(max_positions=4)).at_capacity(3)
    assert not BookRisk(BookPolicy(max_positions=None)).at_capacity(99)


# ------------------------------------------------------------------------------------------------ the book world
class Book:
    def __init__(self, signals, *, t0=WINTER, tf=H4, n=40, overrides=None, policy=BookPolicy(), equity='500',
                 stop_atr='2', symbols=SYMS[:4]):
        self.tf, self.syms = tf, symbols
        self.candles = {s: flat_bars(n, overrides=(overrides or {}).get(s), tf=tf, t0=t0) for s in symbols}
        self.venue = FakeVenue(self.candles, tf, costs=CostModel(), equity=D(equity))
        self.journal = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
        self.bars = CsvBarSource(self.candles, tf)
        label = {H1: '1h', H4: '4h'}[tf]
        self.signals = InjectedSignals({(s, t0 + (b + 1) * tf): tuple(v) for (s, b), v in signals.items()},
                                       stop_atr=D(stop_atr), tf_label=label)
        self.cfg = RunnerConfig(account=sim_account(t0 - 1), portfolio_id=PORTFOLIO_ID, symbols=symbols, tf_ms=tf,
                                timeframe=label, rules={s: fine_rules(s) for s in symbols}, sides=('LONG', 'SHORT'))
        self.policy = policy
        self.t0 = t0
        self.runner = self.make()

    def make(self):
        return BookRunner(self.cfg, policy=self.policy, journal=self.journal, venue=self.venue, bars=self.bars,
                          signals=self.signals)

    def restart(self):
        self.journal = self.journal.reopen()
        self.runner = self.make()

    def run(self, upto_bar):
        start = max(0, (self.venue.now_ms - self.t0) // self.tf)
        for i in range(start, upto_bar + 1):
            t = self.t0 + (i + 1) * self.tf
            self.venue.advance_to(t)
            self.runner.cycle(t)
        return self.runner

    def decisions(self, symbol=None, action=None):
        """Entry decisions (ENTER / SKIP) by default; `action` selects any one action."""
        acts = (Action.ENTER, Action.SKIP) if action is None else (action,)
        return [d for d in self.runner.fold.decisions.values() if (symbol is None or d.symbol == symbol)
                and d.action in acts]

    def entries(self):
        return [(o.ref.symbol, o.position_side) for o in self.venue.orders_submitted() if o.order_type == 'MARKET'
                and not o.reduce]


E = (('enter', 'LONG'),)
LOOSE = BookPolicy(risk_pct=D('0.02'), max_positions=None, max_leverage=D('10'), daily_loss_pct=None,
                   kill_drawdown_pct=None)


def test_max_positions_refuses_the_fifth_in_configured_order():
    sig = {(s, 5): E for s in reversed(SYMS)}                          # inserted in reverse: order must not matter
    b = Book(sig, policy=dataclass_replace(LOOSE, max_positions=4), symbols=SYMS)
    b.run(6)
    assert [s for s, _ in b.entries()] == list(SYMS[:4])
    skip, = b.decisions('EEEUSDT', Action.SKIP)
    assert skip.reason is ReasonCode.CAPACITY_MAX_POSITIONS
    assert len(b.runner.fold.open_lots()) == 4 and b.runner.counters.unprotected_cycles == 0


def test_a_closed_lot_frees_its_slot_for_a_later_signal():
    sig = {(s, 5): E for s in SYMS[:4]}
    sig[('AAAUSDT', 8)] = (('close', 'LONG'),)
    sig[('EEEUSDT', 6)] = E
    sig[('EEEUSDT', 9)] = E
    b = Book(sig, policy=dataclass_replace(LOOSE, max_positions=4), symbols=SYMS)
    b.run(10)
    assert [d.reason for d in b.decisions('EEEUSDT')] == [ReasonCode.CAPACITY_MAX_POSITIONS, ReasonCode.ENTRY_SIGNAL]


def test_leverage_cap_trims_then_refuses_on_shared_equity():
    sig = {(s, 5): E for s in SYMS[:4]}
    b = Book(sig, policy=dataclass_replace(LOOSE, max_leverage=D('3'), cap_gap_buffer=D(0)))  # each 5 x 100 = 1x
    b.run(6)
    lots = {x.symbol: x.qty for x in b.runner.fold.open_lots()}
    # 3 x 500 = 1500: A, B at 5 each (500.1 at their fills), C trimmed to the room left at the fill prices
    assert lots == {'AAAUSDT': D('5'), 'BBBUSDT': D('5'), 'CCCUSDT': D('4.998')}
    skip, = b.decisions('DDDUSDT', Action.SKIP)
    assert skip.reason is ReasonCode.EXEC_SIZE_MIN


def test_one_equity_snapshot_per_cycle_and_closes_before_entries():
    """Two entries in one cycle are sized on the same equity (the research's eq0), after that cycle's closes."""
    sig = {('AAAUSDT', 5): E, ('AAAUSDT', 10): (('close', 'LONG'),), ('BBBUSDT', 10): E, ('CCCUSDT', 10): E}
    b = Book(sig, policy=LOOSE)
    b.run(11)
    v = b.venue
    entries = [o for o in v.orders_submitted() if o.order_type == 'MARKET' and not o.reduce]
    close, = [o for o in v.orders_submitted() if o.order_type == 'MARKET' and o.reduce]
    assert close.seq < entries[1].seq < entries[2].seq                # A's close is sent before B's and C's entries
    fees = sum(f.fee for o in entries[1:] for f in v.fills(o.ref.symbol, o.exchange_order_id).value)
    snapshot = v.equity().value[0] + fees                             # the wallet right after A's close
    qty = {d.symbol: d.intents[0].qty for d in b.decisions(action=Action.ENTER) if d.symbol != 'AAAUSDT'}
    assert qty['BBBUSDT'] == qty['CCCUSDT'] == snapshot * D('0.02') / D('2')   # one snapshot, not cut by B's fee


def dataclass_replace(p, **kw):
    import dataclasses
    return dataclasses.replace(p, **kw)


# ------------------------------------------------------------------------------------------------ halt / kill / DST
HALT = BookPolicy(risk_pct=D('0.10'), max_positions=4, max_leverage=D('10'), daily_loss_pct=D('0.03'),
                  kill_drawdown_pct=None)
CRASH = {'AAAUSDT': {12: ('100', '100.1', '90', '92')}}              # A's stop fills inside candle 12


def halt_world(t0, policy=HALT):
    """1h candles. A enters at 10:00 UTC, is stopped at 12:00-13:00 UTC (-10.6% of equity); B signals at the closes of
    20:00, 21:00 and 22:00 UTC."""
    sig = {('AAAUSDT', 9): E, ('BBBUSDT', 19): E, ('BBBUSDT', 20): E, ('BBBUSDT', 21): E}
    return Book(sig, t0=t0, tf=H1, n=30, overrides=CRASH, policy=policy, symbols=SYMS[:2])


@pytest.mark.parametrize('t0,expect', [
    (SUMMER, [ReasonCode.FILTER_HALT, ReasonCode.ENTRY_SIGNAL, ReasonCode.CAPACITY_IN_TRADE]),   # 21:00 UTC = next day
    (WINTER, [ReasonCode.FILTER_HALT, ReasonCode.FILTER_HALT, ReasonCode.ENTRY_SIGNAL])])        # 22:00 UTC = next day
def test_daily_halt_holds_for_the_cairo_day_of_the_candle_close(t0, expect):
    b = halt_world(t0)
    b.run(23)
    halt, = b.decisions(action=Action.WAIT)
    assert halt.reason is ReasonCode.FILTER_HALT and halt.at_ms == t0 + 13 * H1
    assert [d.reason for d in b.decisions('BBBUSDT')] == expect
    assert b.runner.fold.mode is EntriesMode.ACTIVE                 # a halt is not HOLD: closes / stops keep working


def test_daily_halt_survives_a_restart_on_the_same_cairo_day():
    b = halt_world(WINTER)
    b.run(15)
    b.restart()
    b.run(23)
    assert [d.reason for d in b.decisions('BBBUSDT')][:2] == [ReasonCode.FILTER_HALT, ReasonCode.FILTER_HALT]


def test_drawdown_kill_moves_to_hold_and_never_resumes_by_itself():
    kill = dataclass_replace(HALT, daily_loss_pct=None, kill_drawdown_pct=D('0.10'))
    b = halt_world(SUMMER, kill)
    b.run(28)
    f = b.runner.fold
    assert f.mode is EntriesMode.HOLD and ReasonCode.FILTER_HALT in f.mode_reasons
    hold, = [d for d in f.decisions.values() if d.action is Action.HALT]
    assert [d.reason for d in b.decisions('BBBUSDT')] == [ReasonCode.FILTER_HALT] * 3        # every entry refused
    b.restart()
    assert b.runner.risk.killed and b.runner.fold.mode is EntriesMode.HOLD
    assert b.runner.resume(b.t0 + 29 * H1) and b.runner.fold.mode is EntriesMode.ACTIVE
    assert not b.runner.risk.killed


# ------------------------------------------------------------------------------------------------ crash / restart
def several_open():
    sig = {('AAAUSDT', 5): E, ('BBBUSDT', 5): E, ('CCCUSDT', 6): E, ('AAAUSDT', 14): (('close', 'LONG'),),
           ('BBBUSDT', 15): (('close', 'LONG'),), ('CCCUSDT', 16): (('close', 'LONG'),)}
    return Book(sig, policy=LOOSE, overrides={'DDDUSDT': {}})


@pytest.mark.parametrize('after', range(1, 12))
def test_crash_inside_a_multi_entry_cycle_then_restart(after):
    """Two entries in the same cycle (A and B at candle 5): crash after each journal boundary, restart, re-deliver."""
    ref = several_open()
    ref.run(20)
    b = several_open()
    b.run(4)
    b.journal.fail_writes(1, after=after, error=Crash)
    with pytest.raises(Crash):                                   # the process dies here
        b.run(5)
    b.restart()
    b.runner.cycle(b.t0 + 6 * H4)                                      # the crashed candle is re-delivered
    b.run(20)
    orders = [(o.ref.symbol, o.order_type, o.reduce, o.status) for o in b.venue.orders_submitted()]
    assert sorted(orders) == sorted((o.ref.symbol, o.order_type, o.reduce, o.status)
                                    for o in ref.venue.orders_submitted())
    assert [(t.symbol, t.pnl) for t in b.runner.trades()] == [(t.symbol, t.pnl) for t in ref.runner.trades()]
    assert b.runner.summary().ownership == 'known_empty'


def test_restart_with_three_lots_open_rebuilds_the_book():
    ref = several_open()
    ref.run(20)
    b = several_open()
    b.run(10)
    assert len(b.runner.fold.open_lots()) == 3
    n = len(b.journal.read())
    b.restart()
    b.runner.cycle(b.t0 + 11 * H4)                                     # the last candle again: nothing new
    assert len(b.journal.read()) == n
    b.run(20)
    assert [header_of(e).digest for e in b.journal.read()] == [header_of(e).digest for e in ref.journal.read()]
