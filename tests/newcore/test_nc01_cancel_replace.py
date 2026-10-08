"""Codex P1 on af4e5f3: a REDUCE / CLOSE cancel-replace is representable through ONE explicit link.

The successor names its predecessor by `replaces_intent_id` (never inferred from decisions or order). The linked pair
counts as one leg for the reduce bound; cumulative fills across both stay bounded by the lot (the fill ledger); once the
covered quantity closes, the survivor is retired (CANCELLING, replaced, or portfolio-owned cancel-only work when the lot
is gone). Unrelated overlaps stay invalid."""
from decimal import Decimal as D
from itertools import permutations

import pytest

import nc01_factories as F
from nc01_factories import T0, replace
from newcore.domain import (IntentState as S, InvalidRecord, OwnerKind, Purpose, ReasonCode, canonical_bytes,
                            contract_sha256, loads)


def pair(L='1.5', a='1.5', b='1.5', *, succ_state=S.SUBMITTED, pred_state=S.CANCELLING, link=True, seed=500,
         partial_close=None, purpose=Purpose.CLOSE):
    """A lot of L with an old reducing intent (a, CANCELLING) and its replacement (b, live) carried by the lot."""
    ids = F.Ids(seed)
    acct = ids.id('acct')
    lt, extra = F.lot(ids, acct, qty=D(L), stop_state='confirmed', partial_close=D(partial_close) if partial_close else None)
    pred = F.intent(ids, acct, purpose, lt.symbol, lt.side, D(a), owner_id=lt.lot_id, state=pred_state)
    succ = F.intent(ids, acct, purpose, lt.symbol, lt.side, D(b), owner_id=lt.lot_id, state=succ_state,
                    replaces=pred.intent_id if link else None, reason=ReasonCode.EXIT_TIME)
    lt = replace(lt, in_flight=succ.intent_id)
    return dict(ids=ids, acct=acct, lot=lt, extra=tuple(extra), pred=pred, succ=succ,
                build=lambda intents=None, lot=lt: F.portfolio(acct, (F.position(ids, [lot]),),
                                                               tuple(extra) + (intents or (pred, succ))))


def test_the_linked_pair_is_one_leg_and_an_unlinked_overlap_is_not():
    for purpose in (Purpose.CLOSE, Purpose.REDUCE):
        pair(purpose=purpose)['build']()                                          # 1.5 + 1.5 linked: one leg of 1.5
        with pytest.raises(InvalidRecord, match='exceed the lot qty'):
            pair(purpose=purpose, link=False)['build']()                          # unrelated overlap: 3.0 > 1.5


def test_permutation_gives_one_record_and_one_hash():
    t = pair()
    pos = F.position(t['ids'], [t['lot']])
    orders = [t['extra'] + tuple(o) for o in permutations((t['pred'], t['succ']))]
    built = [F.portfolio(t['acct'], (pos,), o) for o in orders]
    built.append(F.portfolio(t['acct'], (pos,), tuple(reversed(orders[0]))))
    assert all(b == built[0] for b in built)
    assert len({canonical_bytes(b) for b in built}) == 1 and len({contract_sha256(b) for b in built}) == 1


def test_partial_fill_of_the_predecessor_retires_or_replaces_the_survivor():
    """The old close filled 0.5 before its cancel landed: the lot is now 1.0 (ledger 1.5 open, 0.5 closed) and the
    predecessor has left the portfolio. The 1.5 survivor must be retired or replaced."""
    t = pair(L='1', partial_close='0.5')
    succ = replace(t['succ'], replaces_intent_id=None)                     # the predecessor ended: the link is cleared
    with pytest.raises(InvalidRecord, match='retire it'):
        t['build']((succ,))                                                 # a live 1.5 survivor on a 1.0 lot
    t['build']((replace(succ, state=S.CANCELLING),))                        # retiring: valid on its own
    ids = t['ids']
    new = F.intent(ids, t['acct'], Purpose.CLOSE, succ.symbol, succ.side, D('1'), owner_id=t['lot'].lot_id,
                   replaces=succ.intent_id)
    t['build']((replace(succ, state=S.CANCELLING), new), lot=replace(t['lot'], in_flight=new.intent_id))
    with pytest.raises(InvalidRecord, match='retire it'):
        big = replace(new, qty=D('1.5'))
        t['build']((replace(succ, state=S.CANCELLING), big), lot=replace(t['lot'], in_flight=big.intent_id))


def test_old_fills_first_closes_the_lot_and_the_survivor_becomes_cancel_only_work():
    t = pair()
    acct = t['acct']
    orphan = replace(t['succ'], replaces_intent_id=None, owner_id=F.pf_id(acct), owner_kind=OwnerKind.PORTFOLIO,
                     state=S.CANCELLING)
    stop_left = tuple(replace(i, owner_id=F.pf_id(acct), owner_kind=OwnerKind.PORTFOLIO, state=S.CANCELLING)
                      for i in t['extra'])
    F.portfolio(acct, (), stop_left + (orphan,))                             # the lot is gone: only cancel work
    with pytest.raises(InvalidRecord):                                       # still owned by the vanished lot
        F.portfolio(acct, (), stop_left + (replace(t['succ'], replaces_intent_id=None),))
    with pytest.raises(InvalidRecord, match='cancel-only'):                  # portfolio-owned but still sendable
        replace(orphan, state=S.SUBMITTED)


def test_new_fills_first():
    """The replacement fills: fully (the lot is gone, the old order is cancel-only work) or partially (the lot shrinks
    and the still-cancelling predecessor, now unlinked, is retiring and bounded by the lot)."""
    t = pair()
    acct = t['acct']
    old = replace(t['pred'], owner_id=F.pf_id(acct), owner_kind=OwnerKind.PORTFOLIO)
    stop_left = tuple(replace(i, owner_id=F.pf_id(acct), owner_kind=OwnerKind.PORTFOLIO, state=S.CANCELLING)
                      for i in t['extra'])
    F.portfolio(acct, (), stop_left + (old,))
    part = pair(L='1', partial_close='0.5')
    lot = replace(part['lot'], in_flight=None)
    part['build']((part['pred'],), lot=lot)                                  # 1.5 cancelling on a 1.0 lot: retiring
    extra_leg = F.intent(part['ids'], part['acct'], Purpose.CLOSE, lot.symbol, lot.side, D('1'), owner_id=lot.lot_id)
    with pytest.raises(InvalidRecord, match='exceed the lot qty'):          # an unlinked new close next to it: 2 > 1
        part['build']((part['pred'], extra_leg), lot=replace(lot, in_flight=extra_leg.intent_id))


def test_duplicate_or_late_fill_cannot_close_more_than_the_lot():
    """Cumulative fills of both legs are bounded by the lot: the ledger refuses a second close beyond what is held."""
    t = pair()
    lt = t['lot']
    opening = lt.fills[0]
    first = F.fill(ReasonCode.EXIT_TIME, D('1'), D('101'), at=T0 + 5000, result_id=t['ids'].id('res'))
    second = F.fill(ReasonCode.EXIT_TIME, D('1.5'), D('101'), at=T0 + 6000, result_id=t['ids'].id('res'))
    with pytest.raises(InvalidRecord, match='closes more than the lot held'):
        replace(lt, fills=(opening, first, second), qty=D('0.5'))
    replace(lt, fills=(opening, first), qty=D('0.5'), stop=replace(lt.stop, qty=D('0.5'), order=None,
                                                                    confirmed_at_ms=None))


def test_restart_round_trips_the_link():
    pf = pair()['build']()
    back = loads(canonical_bytes(pf))
    assert back == pf
    succ = next(i for i in back.intents if i.replaces_intent_id is not None)
    assert succ.replaces_intent_id in {i.intent_id for i in back.intents}


def _chain(t):
    """A <- B <- C: the middle link would have to be cancelling (as a predecessor) and live (as a successor)."""
    ids, acct, lt = t['ids'], t['acct'], t['lot']
    a = F.intent(ids, acct, Purpose.CLOSE, lt.symbol, lt.side, D('1.5'), owner_id=lt.lot_id, state=S.CANCELLING)
    b = replace(t['pred'], replaces_intent_id=a.intent_id)
    return t['build']((a, b, t['succ']))


def test_invalid_links():
    t = pair()
    ids, acct, lt = t['ids'], t['acct'], t['lot']
    other_lot, other_extra = F.lot(ids, acct, lt.symbol, lt.side, D('1'), stop_state='none')
    cases = {     # each refused by its own rule (asserted by message, so no other rule can mask it)
        'predecessor missing': ('names no live predecessor',
                                lambda: t['build']((replace(t['succ'], replaces_intent_id=ids.id('int')),))),
        'predecessor not cancelling': ('must be CANCELLING',
                                       lambda: t['build']((replace(t['pred'], state=S.WORKING), t['succ']))),
        'successor cancelling': ('must be live, not cancelling',
                                 lambda: t['build']((t['pred'], replace(t['succ'], state=S.CANCELLING)))),
        'predecessor is an add': ('not a lot reduce / close',
                                  lambda: t['build']((replace(t['pred'], purpose=Purpose.ADD,
                                                              reason=ReasonCode.ENTRY_PYRAMID), t['succ']))),
        'chain of links': ('must be live, not cancelling', lambda: _chain(t)),
    }
    for name, (message, build) in cases.items():
        with pytest.raises(InvalidRecord, match=message):
            build()
            raise AssertionError(name)
    # the predecessor belongs to another lot of the same position
    pred_other = replace(t['pred'], owner_id=other_lot.lot_id)
    with pytest.raises(InvalidRecord, match='another account / instrument / side / lot'):
        F.portfolio(acct, (F.position(ids, [lt, other_lot]),), t['extra'] + (pred_other, t['succ']))
    # one link per lot: a second successor of the same predecessor
    twin = replace(t['succ'], intent_id=ids.id('int'), client_order_id='zctwin00000000000000000001')
    with pytest.raises(InvalidRecord):
        t['build']((t['pred'], t['succ'], twin))
    # the link is only for lot REDUCE / CLOSE
    stop = t['extra'][0]
    with pytest.raises(InvalidRecord, match='only a lot REDUCE / CLOSE'):
        replace(stop, replaces_intent_id=ids.id('int'))
    with pytest.raises(InvalidRecord, match='replaces itself'):
        replace(t['succ'], replaces_intent_id=t['succ'].intent_id)
    assert other_extra == []
