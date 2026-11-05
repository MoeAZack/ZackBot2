"""RT-02 end to end in PAPER_SIM: real journal, NC-04, NC-05 threads, NC-06, the journaling sink, the journal
projection, venue evidence and REC-02, against the in-memory PAPER venue.  No network, no credentials."""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rt01_fixtures import WALL0, World, account_record, account_view, acct, pf  # noqa: E402
from test_rt02_journaling import KEY, Keyed  # noqa: E402

from newcore.domain import BindingChanged, ReasonCode, make_id  # noqa: E402
from newcore.domain.account import BindingState, Environment  # noqa: E402
from newcore.runtime import (JournalingActionSink, JournalProjection, RuntimeEnvironment,  # noqa: E402
                             VenueEvidence)
from newcore.runtime.paper import PaperVenue  # noqa: E402
from newcore.store import recover_journal  # noqa: E402

D = Decimal
A = acct(1)


def bind_account(spec, environment=Environment.SIM):
    rec = account_record(spec.account_id, environment=environment)
    j = recover_journal(spec.account_dir, spec.account_id, spec.aggregate_id).journal
    j.append(BindingChanged(event_id=make_id('evt', 7), account_id=spec.account_id, aggregate_id=spec.aggregate_id,
                            sequence=j.last_sequence() + 1, at_ms=WALL0 - 1_000,
                            reason=ReasonCode.BINDING_UNCONFIRMED, from_state=BindingState.UNCONFIRMED,
                            to_state=BindingState.CONFIRMED, binding=rec.binding, confirmation=rec.confirmation,
                            reconciliation_id=None))
    j.close()


class Marks:
    def __init__(self):
        self.price = D('100')

    def __call__(self, symbol):
        return self.price


@pytest.fixture
def paper(tmp_path):
    made = []

    def make(*, entries=True, environment=Environment.SIM):
        w = World(tmp_path, 1, environment=RuntimeEnvironment.PAPER_SIM, entries_enabled=entries)
        made.append(w)
        bind_account(w.specs[0], environment)
        marks = Marks()
        venue = PaperVenue(clock=w.wall, marks=marks)
        w.venues[A] = venue
        sink = JournalingActionSink()
        w.extra_ports[A] = {'state_source': JournalProjection(label='paper'),
                            'evidence': VenueEvidence(symbols=('BTCUSDT',)), 'actions': sink,
                            'entries': Keyed()}
        return w, venue, sink, marks

    yield make
    for w in made:
        try:
            w.shutdown()
        except Exception:
            pass


def test_paper_boot_proves_a_flat_account(paper):
    w, venue, sink, marks = paper(entries=False)
    rt = w.boot()
    view = account_view(rt.snapshot(), A)
    assert view.holds == () and view.restore_proven and view.last_plan_status == 'flat_confirmed'
    w.settle(lambda s: s.scheduler.ready)


def test_paper_entry_is_protected_and_reconciles_protected(paper):
    w, venue, sink, marks = paper()
    w.boot()
    w.settle(lambda s: s.scheduler.ready)
    w.settle(lambda s: any(e.code == 'entry_routed' for e in s.events), advance_ms=5_000)
    w.settle(lambda s: sink.snapshot().log[-1:] == ('working:protect',), advance_ms=500)
    snap = w.settle(lambda s: account_view(s, A).last_plan_status == 'protected_confirmed', advance_ms=1_000)
    view = account_view(snap, A)
    assert view.holds == ()
    (position,) = [p for p in venue.positions().value if p.qty > 0]
    (stop,) = venue.open_orders('BTCUSDT').value
    assert (position.side, stop.qty, stop.stop_price) == ('LONG', position.qty, D('95'))


def test_paper_mainnet_binding_is_refused(paper):
    w, venue, sink, marks = paper(environment=Environment.MAINNET)
    rt = w.boot()
    assert account_view(rt.snapshot(), A).holds == ('environment_mismatch',)
    assert venue.positions().value == ()


def test_paper_stop_fill_is_journaled_and_the_account_reconciles_flat(paper):
    w, venue, sink, marks = paper()
    w.boot()
    w.settle(lambda s: s.scheduler.ready)
    w.settle(lambda s: account_view(s, A).last_plan_status == 'protected_confirmed', advance_ms=5_000)
    marks.price = D('94')
    assert venue.tick()
    w.settle(lambda s: 'filled:stop' in sink.snapshot().log, advance_ms=1_000)
    snap = w.settle(lambda s: account_view(s, A).last_plan_status == 'flat_confirmed'
                    and account_view(s, A).holds == (), advance_ms=1_000)
    assert sink.snapshot().lots[0][1] == '0' and sink.snapshot().problems == ()
    journal = w.runtime._accounts[A].journal
    assert journal.state().intents == ()                           # every intent closed
    assert snap.scheduler.ready
