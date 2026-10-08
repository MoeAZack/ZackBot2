"""Codex P1 on PR #38: an unconfirmed replacement is never counted as protection.

Resize-up (the lot grew; the old stop is smaller) and resize-down (the lot shrank; the old stop is larger) across every
state of the replacement - DURABLE, SUBMITTED, UNKNOWN, WORKING-and-promoted - plus restart (snapshot round trip and
answers lost after a restart). Confirmed coverage is known exchange protection only; the pending target is reported
separately; promotion is atomic with retiring the old stop."""
from decimal import Decimal as D

import pytest

import nc01_factories as F
from nc01_factories import T0, replace
from newcore.domain import (InvalidRecord, IntentState, Purpose, ProtectionStatus, ReasonCode, confirmed_coverage,
                            dumps, loads, pending_coverage, promote_replacement, protection_status, target_coverage)
from newcore.domain.portfolio import entry_blocking_protections

UNPROMOTED = (IntentState.DURABLE, IntentState.SUBMITTED, IntentState.UNKNOWN, IntentState.WORKING)
OLD_LEVEL, NEW_LEVEL = D('95'), D('96')


def resize(lot_qty, old_qty, new_qty, repl_state, *, old_state=IntentState.WORKING, confirmed=True, seed=77):
    """A portfolio with one lot of `lot_qty` whose confirmed stop (old_qty) is being replaced by new_qty."""
    ids = F.Ids(seed)
    acct = ids.id('acct')
    lt, _ = F.lot(ids, acct, qty=lot_qty, stop_state='none')
    old = F.intent(ids, acct, Purpose.PROTECT, lt.symbol, lt.side, old_qty, state=old_state, owner_id=lt.lot_id,
                   stop_price=OLD_LEVEL)
    new = F.intent(ids, acct, Purpose.PROTECT, lt.symbol, lt.side, new_qty, state=repl_state, owner_id=lt.lot_id,
                   stop_price=NEW_LEVEL, reason=ReasonCode.PROTECT_RESIZE)
    stop = F.build(F.Protection, owner_id=lt.lot_id, price=OLD_LEVEL, qty=old_qty, order=old.intent_id,
                   replacement=new.intent_id, confirmed_at_ms=T0 + 2000 if confirmed else None)
    lt = replace(lt, stop=stop)
    return F.portfolio(acct, (F.position(ids, [lt]),), (old, new)), old, new


def view(pf):
    lt = pf.lots[0]
    intents = pf.intents_by_id()
    return (confirmed_coverage(lt.stop, intents), pending_coverage(lt.stop, intents), target_coverage(lt.stop, intents),
            protection_status(lt.stop, intents, lt.qty))


@pytest.mark.parametrize('state', UNPROMOTED, ids=lambda s: s.value)
def test_resize_up_stays_undersized_until_promotion(state):
    pf, old, new = resize(D('2'), D('1.5'), D('2'), state)
    confirmed, pending, target, status = view(pf)
    assert confirmed == D('1.5')                     # only the old stop is known exchange protection
    assert pending == D('2') and target == D('2')    # the intended size is visible, separately
    assert status is ProtectionStatus.UNDERSIZED     # never REPLACING / "protected" while short
    assert entry_blocking_protections(pf) == (pf.lots[0].stop,)


@pytest.mark.parametrize('state', UNPROMOTED, ids=lambda s: s.value)
def test_resize_down_overlap_is_allowed_and_visible(state):
    pf, old, new = resize(D('1'), D('1.5'), D('1'), state)
    confirmed, pending, target, status = view(pf)
    assert confirmed == D('1.5') > pf.lots[0].qty    # two reduce-only stops overlap: shown, not hidden
    assert pending == D('1') and target == D('1')    # the bound applies to what is being established
    assert status is ProtectionStatus.REPLACING and entry_blocking_protections(pf) == ()


@pytest.mark.parametrize('state', UNPROMOTED[:3], ids=lambda s: s.value)
def test_only_a_working_replacement_is_promoted(state):
    pf, old, new = resize(D('2'), D('1.5'), D('2'), state)
    with pytest.raises(InvalidRecord, match='only a WORKING one is promoted'):
        promote_replacement(pf.lots[0].stop, pf.intents_by_id(), T0 + 9000)


@pytest.mark.parametrize('sizes', [(D('2'), D('1.5'), D('2')), (D('1'), D('1.5'), D('1'))], ids=['up', 'down'])
def test_promotion_is_atomic_with_retiring_the_old_stop(sizes):
    lot_qty, old_qty, new_qty = sizes
    pf, old, new = resize(lot_qty, old_qty, new_qty, IntentState.WORKING)
    lt = pf.lots[0]
    promoted = promote_replacement(lt.stop, pf.intents_by_id(), T0 + 9000)
    assert (promoted.order, promoted.qty, promoted.price, promoted.replacement) == (new.intent_id, new_qty, NEW_LEVEL, None)
    position = lambda: replace(pf.positions[0], lots=(replace(lt, stop=promoted),))   # noqa: E731
    with pytest.raises(InvalidRecord, match='cancel-only'):          # promoting but leaving the old stop live: refused
        replace(pf, positions=(position(),))
    done = replace(pf, positions=(position(),), intents=(replace(old, state=IntentState.CANCELLING), new))
    assert view(done) == (new_qty, D(0), new_qty, ProtectionStatus.OWNED_CONFIRMED)


def test_the_old_stop_is_kept_until_the_replacement_is_promoted():
    for confirmed in (True, False):
        pf, old, new = resize(D('2'), D('1.5'), D('2'), IntentState.SUBMITTED, confirmed=confirmed)
        with pytest.raises(InvalidRecord, match='kept until the replacement is confirmed' if not confirmed else
                           'only a WORKING order is confirmed'):
            replace(pf, intents=(replace(old, state=IntentState.CANCELLING), new))


@pytest.mark.parametrize('state', UNPROMOTED, ids=lambda s: s.value)
def test_restart_from_a_snapshot_derives_the_same_coverage(state):
    for sizes in ((D('2'), D('1.5'), D('2')), (D('1'), D('1.5'), D('1'))):
        pf, _, _ = resize(*sizes, state)
        assert view(loads(dumps(pf))) == view(pf)


def test_restart_with_lost_answers_counts_no_protection():
    """After a restart both stops' answers are unreadable (UNKNOWN): nothing is KNOWN to be on the exchange."""
    for sizes in ((D('2'), D('1.5'), D('2')), (D('1'), D('1.5'), D('1'))):
        pf, old, new = resize(*sizes, IntentState.UNKNOWN, old_state=IntentState.UNKNOWN, confirmed=False)
        confirmed, pending, target, status = view(pf)
        assert confirmed == D(0) and pending == sizes[2]
        assert status is ProtectionStatus.PLACEMENT_PENDING and entry_blocking_protections(pf) == (pf.lots[0].stop,)
        with pytest.raises(InvalidRecord, match='only a WORKING one is promoted'):
            promote_replacement(pf.lots[0].stop, pf.intents_by_id(), T0 + 9000)
