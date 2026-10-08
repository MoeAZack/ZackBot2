"""Codex review of 78c0010 (#13, 6067316720) - repros first.

P1  unreadable ownership evidence let foreign quantity be touched: a live opening intent whose fills cannot be read
    made _owned_exposure None and _emergency_set sized to the WHOLE venue position; a restart in hard HOLD with the
    trades unreadable took the in-memory floor (0) for our emergency fills while the journal still said lot 5. Unknown
    evidence is now UNKNOWN - never zero, never widening ownership: only the proven quantity may get a new stop / close,
    a loud incident + HOLD, resting protection kept, zero action on quantity that may be foreign.
P2  exception text never reaches incidents / CLI output raw: type + a salted bounded correlation tag only (our own
    validation messages excepted)."""
from decimal import Decimal as D

import pytest

from newcore.domain import EntriesMode, Op, Purpose
from newcore.ports.venue import ReadKind, ReadOutcome
from newcore.runner import InjectedSignals
from slice_helpers import H4, ScriptedVenue, World, flat_bars

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
SIDES = ('LONG', 'SHORT')
SECRET = 'k3y-Zq9xW2'


def at(bar):
    return T0 + (bar + 1) * H4


def position(w, side):
    return sum((p.qty for p in w.venue.positions().value if p.side == side), D(0))


def sent_since(w, n):
    return list(w.venue.orders_submitted()[n:])


def unknown_read(w):
    return lambda *a, **k: ReadOutcome(kind=ReadKind.UNKNOWN, observed_at_ms=w.venue.now_ms, detail='timeout')


# ------------------------------------------------------------------------------------------------------- P1 (a)
@pytest.mark.parametrize('side', SIDES)
def test_p1_a_unreadable_fills_of_our_live_opening_never_size_to_the_whole_position(side):
    """Our entry rests; in hard HOLD its race fill (3) arrives with a foreign add (2) on the same side and the fills
    read of our order is UNKNOWN: nothing proves any of the 5 ours - no new stop / close at all, a loud incident."""
    w = World(flat_bars(20), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.venue.rest_next_entries(1)
    w.run(5)
    entry = next(iv for iv in w.runner.fold.intents.values() if iv.purpose is Purpose.ENTRY)
    w.journal.fail_writes(10 ** 9)
    w.runner.store_unavailable('test: ENOSPC')
    w.port.lose('cancel')                                                # the drain cancel never lands
    w.venue.fill_resting(entry.intent.client_order_id, D('3'))
    w.venue.inject_position(SYM, side, D('2'), D('100'))
    w.venue.fills = unknown_read(w)                                      # our own fills: UNKNOWN
    n = len(w.venue.orders_submitted())
    for b in range(6, 10):
        if b > 6:
            w.port.lose('cancel')
        w.run(b)
        assert [o for o in sent_since(w, n) if o.order_type != 'MARKET' or o.reduce] == []   # zero new stop / close
        assert position(w, side) == D('5')
    r = w.runner
    assert r.mode is EntriesMode.HOLD and any('UNREADABLE' in t for _, t in r.incidents)


# ------------------------------------------------------------------------------------------------------- P1 (b)
def ours_closed_foreign_left(side):
    w = World(flat_bars(30), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    w.venue.inject_position(SYM, side, D('2'), D('100'))
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)
    w.journal.fail_writes(10 ** 9)
    w.runner.store_unavailable('test: ENOSPC')
    w.port.refuse('stop', -2010)
    w.run(7)                                                             # our 5 closed by the emergency close
    assert position(w, side) == D('2')
    return w


@pytest.mark.parametrize('side', SIDES)
def test_p1_b_restart_in_hard_hold_with_trades_unreadable_never_touches_the_foreign_rest(side):
    w = ours_closed_foreign_left(side)
    w.restart(hard_hold='test: store still down at boot')
    w.venue.trades = unknown_read(w)                                     # our emergency fills cannot be proven
    n = len(w.venue.orders_submitted())
    for b in range(8, 12):
        w.run(b)
        assert sent_since(w, n) == [] and position(w, side) == D('2')    # zero action on the foreign 2
    r = w.runner
    assert r.mode is EntriesMode.HOLD and any('UNREADABLE' in t for _, t in r.incidents)


@pytest.mark.parametrize('side', SIDES)
def test_p1_b_store_back_with_trades_unreadable_never_protects_the_foreign_rest(side):
    w = ours_closed_foreign_left(side)
    w.port._refuse.pop('stop')
    w.restart()                                                          # writable again, but trades unknown
    w.venue.trades = unknown_read(w)
    n = len(w.venue.orders_submitted())
    for b in range(8, 12):
        w.run(b)
        assert sent_since(w, n) == [] and position(w, side) == D('2')
    r = w.runner
    assert r.mode is EntriesMode.HOLD and any('ownership UNKNOWN' in t for _, t in r.incidents)
    # NC-01 r3a: the store is writable again, so the ownership-unknown incident is journaled - once, across a restart
    from newcore.domain import IncidentRecorded, ReasonCode
    incs = [e for e in w.journal.read() if isinstance(e, IncidentRecorded)]
    assert [e.incident.kind for e in incs] == [ReasonCode.RECONCILE_UNRECONCILED]
    assert incs[0].incident.lot_refs and incs[0].incident.symbol == 'SOLUSDT'
    w.restart()
    w.venue.trades = unknown_read(w)
    w.run(13)
    assert len([e for e in w.journal.read() if isinstance(e, IncidentRecorded)]) == 1


# ------------------------------------------------------------------- ruling 3: the ONLY PROTECT cancels in HOLD
@pytest.mark.parametrize('side', SIDES)
def test_ruling3_any_other_protect_cancel_in_hold_is_refused(side):
    """Codex ruling (#13, 6067288768): a PROTECT cancel in HOLD only for the two named, locally re-proved cases; the
    central table is NOT widened. Anything else - another kind, or a named kind whose proof does not hold now - is
    refused by Runner.protect_cancel_in_hold."""
    from newcore.domain import ReasonCode
    w = World(flat_bars(20), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.run(6)
    r = w.runner
    lot, = r.fold.open_lots()
    stop = lot.live_stop
    r._hold([ReasonCode.RECONCILE_UNRECONCILED])
    assert r.mode is EntriesMode.HOLD and not r._permits(Purpose.PROTECT, Op.CANCEL)
    for kind in ('trail', 'replace', 'cleanup', 'manual', '', None):
        assert r.protect_cancel_in_hold(kind, stop) is False             # no other case exists
    assert r.protect_cancel_in_hold('flat_side', stop) is False          # the venue is NOT flat
    assert r.protect_cancel_in_hold('replaced', stop) is False           # no smaller replacement is working
    w.venue._apply_fill(SYM, side, lot.qty, D('100'), reduce=True, eoid='manual-close', at_ms=w.venue.now_ms,
                        fee=D('0'))
    assert r.protect_cancel_in_hold('flat_side', stop) is True           # proven flat now: the named case


# ------------------------------------------------------------------------------------------------------- P2
class Raising(ScriptedVenue):
    def _effect(self, name, call, ref, kind):
        if kind == 'stop':
            self.effects.append(name)
            raise ConnectionError(f'POST https://testnet.example/fapi?signature=abc&key={SECRET}')
        return super()._effect(name, call, ref, kind)


def test_p2_a_raising_adapter_never_leaks_its_message_into_incidents():
    w = World(flat_bars(20), InjectedSignals({(SYM, at(5)): (('enter', 'LONG'),)}, stop_atr=D('2')), strict=False)
    w.port = Raising(w.venue)
    w.runner = w.new_runner()
    w.run(8)
    texts = [t for _, t in w.runner.incidents]
    assert any('ConnectionError#' in t for t in texts)
    assert not [t for t in texts if SECRET in t or 'signature' in t or 'https://' in t]


def test_p2_a_store_exception_never_leaks_into_the_hard_hold_text():
    from newcore.ports.journal import JournalUnavailable
    w = World(flat_bars(20), InjectedSignals({(SYM, at(5)): (('enter', 'LONG'),)}, stop_atr=D('2')), strict=False)
    w.run(6)
    w.runner.store_unavailable(JournalUnavailable(f'disk at /mnt/{SECRET} full'))
    assert SECRET not in w.runner.hard_hold and not [t for _, t in w.runner.incidents if SECRET in t]


def test_p2_the_tag_is_bounded_stable_in_process_and_carries_nothing():
    from newcore.domain.errors import InvalidRecord
    from newcore.runner.redact import describe, exc_tag
    a, b = exc_tag(ConnectionError(SECRET)), exc_tag(ConnectionError(SECRET))
    assert a == b and SECRET not in a and len(a.split('#')[1]) == 10
    assert exc_tag(ConnectionError('other')) != a
    assert describe(ValueError(SECRET)) .startswith('ValueError#')        # not ours unless declared
    assert 'bad field' in describe(InvalidRecord('x', 'bad field'))       # our own validation stays readable


def test_p2_cli_output_never_carries_a_foreign_exception_message(tmp_path, monkeypatch):
    from newcore.runner import app as A
    from test_run_cli import cfg_file, run

    def boom(*a, **k):
        raise ConnectionError(f'GET https://testnet.example?key={SECRET}')
    cfg = cfg_file(tmp_path)
    monkeypatch.setattr(A, 'Session', boom)                              # e.g. a testnet factory / transport error
    code, out = run(['run', '--config', cfg, '--cycles', '1'])
    assert code == A.EXIT_CONFIG and SECRET not in out and 'https://' not in out and 'ConnectionError#' in out
    monkeypatch.setattr(A.C, 'load', lambda p: (_ for _ in ()).throw(OSError(f'socket {SECRET}')))
    code, out = run(['run', '--config', cfg, '--cycles', '1'])
    assert code == A.EXIT_CONFIG and SECRET not in out and 'OSError#' in out
