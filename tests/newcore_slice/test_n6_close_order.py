"""Codex ruling 13 / TNET N6 (P1): an exit never opens a naked window. The reduce-only close goes out with the stop
still live; the stop is cancelled only after the close's FINAL proves the lot flat. Pinned: a crash at EVERY journal
write and EVERY venue effect of the exit cycle, long and short - at the moment of the crash the venue is flat or still
holds the owned stop, and after the restart the run ends flat with nothing resting, never naked."""
from decimal import Decimal as D

import pytest

from newcore.runner import InjectedSignals
from slice_helpers import H4, Crash, World, flat_bars

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
ENTRY, EXIT = 5, 10
SIDES = ('LONG', 'SHORT')


def world(side, close_ok=True):
    s = {(SYM, T0 + (ENTRY + 1) * H4): (('enter', side),), (SYM, T0 + (EXIT + 1) * H4): (('close', side),)}
    return World(flat_bars(20), InjectedSignals(s, stop_atr=D('2')))


def naked(w, side):
    pos = next(p.qty for p in w.venue.positions().value if p.side == side)
    cover = sum((o.qty for o in w.venue.open_orders().value if o.reduce and o.position_side == side
                 and o.order_type == 'STOP_MARKET'), D(0))
    return pos > 0 and cover < pos


def _exit_cycle_counts(side):
    w = world(side)
    w.run(EXIT - 1)
    n, e = len(w.journal.read()), len(w.port.effects)
    w.run(EXIT)
    return len(w.journal.read()) - n, len(w.port.effects) - e, n, e


COUNTS = {s: _exit_cycle_counts(s) for s in SIDES}


def finish(w, side):
    w.port.disarm()
    w.restart()
    for i in range(EXIT, 13):
        t = w.close_ms(i)
        w.venue.advance_to(t)
        w.runner.cycle(t)
        assert not naked(w, side)
    assert all(p.qty == 0 for p in w.venue.positions().value) and not w.venue.open_orders().value
    r = w.runner
    assert r.counters.unprotected_cycles == 0 and not r.fold.open_lots()
    t, = r.trades()
    assert t.exit_reason.value == 'exit.exit_signal'


@pytest.mark.parametrize('side,k', [(s, k) for s in SIDES for k in range(COUNTS[s][0])])
def test_n6_crash_at_every_journal_write_of_the_exit_never_naked(side, k):
    w = world(side)
    w.run(EXIT - 1)
    w.journal.fail_writes(1, after=k, error=Crash)
    t = w.close_ms(EXIT)
    w.venue.advance_to(t)
    with pytest.raises(Crash):
        w.runner.cycle(t)
    assert not naked(w, side)                                      # the instant the process dies
    finish(w, side)


@pytest.mark.parametrize('side,n,when', [(s, n, wh) for s in SIDES for n in range(1, COUNTS[s][1] + 1)
                                          for wh in ('before', 'after')])
def test_n6_crash_at_every_venue_effect_of_the_exit_never_naked(side, n, when):
    w = world(side)
    w.run(EXIT - 1)
    w.port.crash(len(w.port.effects) + n, when)
    t = w.close_ms(EXIT)
    w.venue.advance_to(t)
    with pytest.raises(Crash):
        w.runner.cycle(t)
    assert not naked(w, side)
    finish(w, side)


@pytest.mark.parametrize('side', SIDES)
def test_n6_the_close_is_sent_before_the_stop_cancel(side):
    w = world(side)
    w.run(EXIT - 1)
    e = len(w.port.effects)
    w.run(EXIT)
    assert w.port.effects[e:] == ['market_reduce', 'cancel']
