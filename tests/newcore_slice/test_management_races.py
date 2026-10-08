"""M4 races through the runner (driver a0acf99: a flat lot cancels every stop still working, also an in-flight one once
it confirms - never twice, never while open).

Break-even replace race: TP1 fills, the break-even stop is sent and RESTS on the venue but its answer is lost (and the
venue cannot be read for it), so the old stop stays the carrier; then the OLD stop fills for the whole position at the
venue. At the next cycle the old stop's fill makes the lot flat and the replacement's confirmation arrives: it is
cancelled in that same cycle - flat, no open order left, never naked."""
import pytest

from newcore.domain import EntriesMode, IntentState, Purpose
from newcore.ports.keys import client_id_for, derive_child_intent_id
from newcore.ports.venue import OrderOutcome, OutcomeKind
from newcore.runner.managed import SyntheticPlans
from mg_helpers import ZERO_COSTS, MgWorld, intents_of, path, signals

pytestmark = pytest.mark.usefixtures('journal_kind')
FLOW = {6: (100, 100.2, 98.9, 99), 7: (99, 99.2, 98.9, 99), 8: (99, 102.2, 98.9, 102)}
PLANS = SyntheticPlans(costs=ZERO_COSTS, time_exit_candles=8)


@pytest.mark.parametrize('side', ('LONG', 'SHORT'))
def test_break_even_race_old_stop_fills_then_flat_with_no_open_orders(side):
    w = MgWorld(path(24, FLOW, side), signals(side), plans=PLANS)
    w.run(7)
    r = w.runner
    acct = w.config.account.account_id
    lot, = r.fold.open_lots()
    old = lot.carrier                                              # the stop resized after the add
    be_cid = client_id_for(derive_child_intent_id(acct, lot.lot_id, Purpose.PROTECT, len(lot.protects)), 'classic')
    inner, sent = w.port.submit_stop, []

    def rests_answer_lost(order):                                  # the break-even stop lands; its answer is lost
        out = inner(order)
        if order.ref.client_id == be_cid:
            sent.append(out)
            return OrderOutcome(kind=OutcomeKind.UNKNOWN, ref=order.ref, observed_at_ms=w.venue.now_ms,
                                detail='timeout')
        return out
    w.port.submit_stop = rests_answer_lost
    w.port.blind(be_cid)
    w.run(8)                                                       # TP1 fills, break-even sent, unconfirmed
    r = w.runner
    lot, = r.fold.open_lots()
    assert sent and sent[0].kind is OutcomeKind.KNOWN               # it IS resting on the venue
    assert lot.carrier.intent_id == old.intent_id and lot.replacement.intent.client_order_id == be_cid
    assert r.counters.unprotected_cycles == 0
    o = w.venue._orders[old.intent.client_order_id]                # the OLD stop fills the whole position
    w.venue._execute(o, lot.qty, o.stop_price, at_ms=w.venue.now_ms)
    assert all(p.qty == 0 for p in w.venue.positions().value)
    assert [x.ref.client_id for x in w.venue.open_orders().value] == [be_cid]
    w.port._blind.discard(be_cid)
    w.run(9)
    r = w.runner
    assert not r.fold.open_lots() and not w.venue.open_orders().value          # flat, nothing left working
    be = r.fold.by_client_id[be_cid]
    assert be.state is IntentState.CANCELLED and not be.live
    assert r.counters.unprotected_cycles == 0 and r.fold.mode is EntriesMode.ACTIVE
    assert len([iv for iv in intents_of(r, Purpose.PROTECT) if iv.state is IntentState.CANCELLING]) == 0
