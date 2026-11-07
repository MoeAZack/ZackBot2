"""RT-02 second adversarial review (evidence, PAPER venue, stop watch): one regression test per confirmed finding.
All text I/O is utf-8."""
from __future__ import annotations

import dataclasses
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rt01_fixtures import account_view, acct  # noqa: E402
from test_rt02_paper_e2e import paper  # noqa: E402,F401  (fixture)

from newcore.domain import IntentState, Purpose, ResultObserved  # noqa: E402
from newcore.ports import venue as P  # noqa: E402
from newcore.runtime import journaling as J  # noqa: E402
from newcore.runtime.paper import PaperVenue  # noqa: E402

D = Decimal
A = acct(1)


def protected(paper_fixture):
    w, venue, sink, marks = paper_fixture()
    w.boot()
    w.settle(lambda s: s.scheduler.ready)
    w.settle(lambda s: account_view(s, A).last_plan_status == 'protected_confirmed', advance_ms=5_000)
    return w, venue, sink, marks


def stop_results(w):
    journal = w.runtime._accounts[A].journal
    protects = {e.intent.intent_id for e in journal.read() if hasattr(e, 'intent') and
                getattr(e.intent, 'purpose', None) is Purpose.PROTECT}
    return [e.result for e in journal.read() if isinstance(e, ResultObserved) and e.result.intent_id in protects]


# F1: a triggered algo stop answers KNOWN + child id (adapter contract); the sink follows the child's fills.
def test_f1_triggered_algo_stop_is_journaled_from_its_child_fills(paper):
    w, venue, sink, marks = protected(paper)
    marks.price = D('90')
    assert venue.tick()
    w.settle(lambda s: 'filled:stop' in sink.snapshot().log, advance_ms=1_000)
    assert 'triggered:stop' in sink.snapshot().log
    final = stop_results(w)[-1]
    (stop,) = [s for s in venue._stops.values()]
    assert final.exchange_order_id == stop.child != stop.exchange_order_id
    assert final.executed_qty == stop.qty
    # F4: the journaled price is what the child filled at (the mark), not the stop price
    assert final.avg_price == D('90')
    w.settle(lambda s: account_view(s, A).last_plan_status == 'flat_confirmed' and account_view(s, A).holds == (),
             advance_ms=1_000)


def _short_fills(venue, change):
    real = venue.fills

    def fills(symbol, eid):
        out = real(symbol, eid)
        return dataclasses.replace(out, value=tuple(change(f) for f in out.value))
    venue.fills = fills


def test_f1_a_child_that_never_fills_the_stop_becomes_unresolved(paper):
    w, venue, sink, marks = protected(paper)
    _short_fills(venue, lambda f: dataclasses.replace(f, qty=f.qty / 2))
    marks.price = D('90')
    assert venue.tick()
    snap = w.settle(lambda s: 'order_unresolved' in account_view(s, A).holds, advance_ms=1_000)
    assert 'filled:stop' not in sink.snapshot().log and 'unresolved:protect' in sink.snapshot().log
    assert J.TRIGGER_READS >= 2
    assert account_view(snap, A).holds                             # held, never assumed filled


def test_f1_child_fills_on_the_wrong_side_are_unresolved(paper):
    w, venue, sink, marks = protected(paper)
    _short_fills(venue, lambda f: dataclasses.replace(f, position_side='SHORT'))
    marks.price = D('90')
    assert venue.tick()
    w.settle(lambda s: 'order_unresolved' in account_view(s, A).holds, advance_ms=1_000)
    assert 'filled:stop' not in sink.snapshot().log


# F1 (evidence half): an algo stop's id is never sent to userTrades.
def test_f1_evidence_never_reads_fills_by_an_algo_id(paper):
    w, venue, sink, marks = protected(paper)
    ev = w.runtime._accounts[A].ports.evidence
    state = w.runtime._accounts[A].journal.state()
    assert any(v.state is IntentState.WORKING and v.intent.purpose is Purpose.PROTECT for v in state.intents)
    rows = ev.read_work(A, 999, 0, 1_000)
    assert not [r for r in rows if r.operation.value == 'fills']


# PAPER fidelity: a stop that triggers with nothing to close ends EXPIRED with nothing executed.
def test_paper_stop_with_no_position_expires_empty():
    venue = PaperVenue(clock=lambda: 2_000_000_000_000, marks=lambda s: D('90'))
    ref = P.OrderRef(symbol='BTCUSDT', client_id='zbn1p-1', route='algo')
    venue.submit_stop(P.StopOrder(ref=ref, position_side='LONG', qty=D('1'), stop_price=D('95')))
    assert venue.tick() == ('zbn1p-1',)
    out = venue.query(ref)
    assert (out.kind, out.status, out.executed_qty, out.avg_price) == (P.OutcomeKind.FINAL, 'EXPIRED', D('0'), None)


# ------------------------------------------------------------------------------------------------ mutation survivors
import threading  # noqa: E402

import pytest  # noqa: E402

from newcore.account_context import WorkResult, WorkState  # noqa: E402
from newcore.runtime.evidence import EvidenceError, _orders  # noqa: E402


def test_a_stop_the_venue_no_longer_knows_is_unresolved(paper):
    w, venue, sink, marks = protected(paper)
    real = venue.query

    def query(ref):
        out = real(ref)
        if ref.route == 'algo':
            return P.OrderOutcome(kind=P.OutcomeKind.NOT_FOUND, ref=ref, observed_at_ms=out.observed_at_ms,
                                  error_code=-2013)
        return out
    venue.query = query
    w.settle(lambda s: 'order_unresolved' in account_view(s, A).holds, advance_ms=1_000)
    assert 'unresolved:protect' in sink.snapshot().log


def test_no_stop_query_is_sent_while_the_sink_is_uncertain(paper):
    w, venue, sink, marks = protected(paper)
    w.shutdown()                                                   # drive the watch by hand from here
    w.runtime = None
    submitted = []
    sink._b = dataclasses.replace(sink._b, result=lambda work_id: None, cancel=lambda work_id, now: None,
                                  submit=lambda work: submitted.append(work.work_id))
    sink._queries.clear()
    assert sink._watched                                           # a WORKING stop is being watched
    for problem in sorted(J.UNCERTAIN):
        sink._problems = {problem}
        sink._next_query_mono = None
        sink._watch(10_000_000)
        assert submitted == [], problem
    sink._problems = set()
    sink._next_query_mono = None
    sink._watch(10_000_000)
    assert len(submitted) == 1                                     # the control: a healthy sink does ask


def test_an_unanswered_stop_query_is_cancelled_and_asked_again(paper):
    w, venue, sink, marks = protected(paper)
    w.shutdown()                                                   # drive the watch by hand from here
    w.runtime = None
    cancelled, submitted = [], []
    sink._b = dataclasses.replace(sink._b, result=lambda work_id: None,
                                  cancel=lambda work_id, now: cancelled.append(work_id),
                                  submit=lambda work: submitted.append(work.work_id))
    sink._queries.clear()
    sink._next_query_mono = None
    deadline = sink._b.order_deadline_ms
    sink._watch(10_000_000)
    assert len(submitted) == 1 and not cancelled
    sink._watch(10_000_000 + 2 * deadline - 1)                     # still waiting
    assert not cancelled
    sink._watch(10_000_000 + 2 * deadline)                         # no answer by the deadline: cancelled
    assert cancelled == submitted[:1] and not sink._queries
    sink._watch(10_000_000 + 3 * deadline)                         # and asked again on the next round
    assert len(submitted) == 2 and submitted[1] != submitted[0]


def _evidence(paper):
    w, venue, sink, marks = protected(paper)
    return w.runtime._accounts[A].ports.evidence


def _ok(rows):
    return [WorkResult(work_id=r.work_id, state=WorkState.COMPLETED, detail='ok',
                       value=P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=2_000_000_000_000, value=()))
            for r in rows]


def test_evidence_refuses_missing_extra_or_unreadable_results(paper):
    ev = _evidence(paper)
    rows = ev.read_work(A, 900, 0, 1_000)
    ev.read_work(A, 901, 0, 1_000)
    ev.read_work(A, 902, 0, 1_000)
    with pytest.raises(EvidenceError, match='results_mismatch'):
        ev.assemble(A, 900, _ok(rows)[:-1])
    bad = _ok(rows)
    bad[0] = WorkResult(work_id=bad[0].work_id, state=WorkState.COMPLETED, detail='x',
                        value=P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=2_000_000_000_000, detail='split_route_read'))
    with pytest.raises(EvidenceError, match='read_not_ok'):
        ev.assemble(A, 901, bad)
    assert ev.assemble(A, 902, _ok(ev.read_work(A, 902, 0, 1_000))).pages   # control: every planned read OK


def test_evidence_refuses_too_many_symbols(paper):
    ev = _evidence(paper)
    ev.symbols = tuple(f'S{i}USDT' for i in range(9))
    with pytest.raises(EvidenceError, match='evidence_too_wide'):
        ev.read_work(A, 950, 0, 1_000)


def test_evidence_refuses_a_close_position_order():
    row = P.VenueOrder(ref=P.OrderRef(symbol='BTCUSDT', client_id='foreign-1', route='algo'), exchange_order_id='7',
                       position_side='LONG', reduce=True, order_type='STOP_MARKET', status='NEW', qty=D('0'),
                       close_position=True, stop_price=D('90'))
    with pytest.raises(EvidenceError, match='order_record'):
        _orders(P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=2_000_000_000_000, value=(row,)))


def test_paper_classic_stop_is_refused_like_binance():
    venue = PaperVenue(clock=lambda: 2_000_000_000_000, marks=lambda s: D('100'))
    out = venue.submit_stop(P.StopOrder(ref=P.OrderRef(symbol='BTCUSDT', client_id='zbn1p-1', route='classic'),
                                        position_side='LONG', qty=D('1'), stop_price=D('95')))
    assert (out.kind, out.error_code) == (P.OutcomeKind.REJECTED, -4120)


@pytest.mark.parametrize('side,stop,marks', [('LONG', '95', ['96', '95']), ('SHORT', '105', ['104', '105'])])
def test_paper_stop_triggers_only_once_the_mark_crosses(side, stop, marks):
    price = {'v': D('100')}
    venue = PaperVenue(clock=lambda: 2_000_000_000_000, marks=lambda s: price['v'])
    venue.submit_market(P.MarketOrder(ref=P.OrderRef(symbol='BTCUSDT', client_id='zbn1e-1'), position_side=side,
                                      qty=D('1'), reduce=False))
    venue.submit_stop(P.StopOrder(ref=P.OrderRef(symbol='BTCUSDT', client_id='zbn1p-1', route='algo'),
                                  position_side=side, qty=D('1'), stop_price=D(stop)))
    price['v'] = D(marks[0])
    assert venue.tick() == ()
    price['v'] = D(marks[1])
    assert venue.tick() == ('zbn1p-1',)


def test_a_final_answer_with_an_open_status_is_ignored_not_journaled(paper):
    w, venue, sink, marks = protected(paper)
    (order,) = sink._watched.values()
    bogus = P.OrderOutcome(kind=P.OutcomeKind.FINAL, ref=P.OrderRef(symbol='BTCUSDT',
                           client_id=order.intent.client_order_id, route='algo'),
                           observed_at_ms=w.wall(), status='NEW', exchange_order_id='1', executed_qty=D('0'))
    with sink._lock:
        sink._fold_query(order, WorkResult(work_id='q', state=WorkState.COMPLETED, detail='ok', value=bogus))
    assert order.intent.intent_id in sink._watched and sink._problems == set()
