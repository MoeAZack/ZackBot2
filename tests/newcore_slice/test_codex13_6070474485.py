"""Codex #13 6070474485 (P1 at 859af74): emergency-order attribution is tri-state - match / proven-not-match / UNKNOWN.

Journaled lot 5 ours; an EARLIER process's emergency order closed 2 (own 3 left); a foreign same-side add of 3 -> the
venue holds 6. The trades page is complete and contains the emergency fill, but the query of that order's deterministic
emergency client id answers UNKNOWN. Unknown attribution is not "foreign": _emergency_filled() is None, so no new stop /
close / resize is sent (never a stop of 5 over foreign quantity), resting protection is kept, the runner is loud
(UNREADABLE incident, HOLD). Covered on the 'close' id and on the classic / algo generation queries, both sides, with and
without a restart."""
from decimal import Decimal as D

import pytest

from newcore.domain import EntriesMode
from newcore.ports.venue import MarketOrder, OrderOutcome, OrderRef, OutcomeKind
from newcore.runner import InjectedSignals, ids
from slice_helpers import H4, World, flat_bars

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
SIDES = ('LONG', 'SHORT')
# (emergency id tag, the route it was sent on, the route whose query is blinded)
VARIANTS = (('close', 'classic', 'classic'), (0, 'classic', 'classic'), (0, 'algo', 'algo'))


def at(bar):
    return T0 + (bar + 1) * H4


def position(w, side):
    return sum((p.qty for p in w.venue.positions().value if p.side == side), D(0))


def stops(w, side):
    return [o for o in w.venue.open_orders().value if o.reduce and o.position_side == side
            and o.order_type == 'STOP_MARKET']


def routed_blind(port, cid, sent_route, blind_route):
    """The venue knows `cid` only on the route it was sent on (FakeVenue ignores routes); the query of `cid` on
    `blind_route` answers UNKNOWN."""
    inner = port.query

    def query(ref):
        if ref.client_id == cid and ref.route == blind_route:
            return OrderOutcome(kind=OutcomeKind.UNKNOWN, ref=ref, observed_at_ms=port.inner.now_ms, detail='scripted')
        if ref.client_id == cid and ref.route != sent_route:
            return OrderOutcome(kind=OutcomeKind.NOT_FOUND, ref=ref, observed_at_ms=port.inner.now_ms)
        return inner(ref)
    port.query = query


def earlier_emergency_exit(side, tag, sent_route, blind_route, *, restart, stop_gone=True):
    w = World(flat_bars(30), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    assert lot.qty == D('5')
    cid = ids.emergency_stop_client_id(w.runner.acct, SYM, side, D('2'), tag)
    out = w.venue.submit_market(MarketOrder(ref=OrderRef(symbol=SYM, client_id=cid),     # routed_blind places it
                                            position_side=side, qty=D('2'), reduce=True))
    assert out.kind is OutcomeKind.FINAL and position(w, side) == D('3')
    w.venue.inject_position(SYM, side, D('3'), D('100'))                  # foreign same-side add of 3
    assert position(w, side) == D('6')
    if stop_gone:                                                         # our stop is gone (Codex's case)
        w.venue.external_cancel(lot.live_stop.intent.client_order_id)
    if restart:
        w.journal.fail_writes(10 ** 9)
        w.restart(hard_hold='test: store down at boot')
    else:
        w.journal.fail_writes(10 ** 9)
        w.runner.store_unavailable('test: ENOSPC')
    routed_blind(w.port, cid, sent_route, blind_route)
    return w, lot


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('variant', VARIANTS, ids=lambda v: f'{v[0]}-{v[1]}')
@pytest.mark.parametrize('restart', (False, True))
@pytest.mark.parametrize('stop_gone', (True, False), ids=('stop-gone', 'stop-resting'))
def test_unknown_emergency_attribution_never_sizes_from_the_lot(side, variant, restart, stop_gone):
    w, lot = earlier_emergency_exit(side, *variant, restart=restart, stop_gone=stop_gone)
    before = [o.ref.client_id for o in stops(w, side)]
    n = len(w.venue.orders_submitted())
    for b in (7, 8, 9):
        w.run(b)
    sent = list(w.venue.orders_submitted()[n:])
    r = w.runner
    assert sent == [], [(o.order_type, o.qty) for o in sent]              # never a stop / close of 5 on own 3
    assert position(w, side) == D('6')
    assert [o.ref.client_id for o in stops(w, side)] == before            # resting protection kept
    assert r.mode is EntriesMode.HOLD
    assert any('UNREADABLE' in t for _, t in r.incidents)


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('variant', VARIANTS, ids=lambda v: f'{v[0]}-{v[1]}')
def test_a_readable_query_still_proves_own_three(side, variant):
    """Control: the same world with nothing blinded proves own 3 (the emergency close is matched) - no stop of 5."""
    tag, route, _ = variant
    w, lot = earlier_emergency_exit(side, tag, route, 'none', restart=False)
    n = len(w.venue.orders_submitted())
    for b in (7, 8, 9):
        w.run(b)
    sent = list(w.venue.orders_submitted()[n:])
    assert sum((o.qty for o in sent if o.order_type == 'STOP_MARKET'), D(0)) <= D('3')
    assert w.runner.mode is EntriesMode.HOLD
