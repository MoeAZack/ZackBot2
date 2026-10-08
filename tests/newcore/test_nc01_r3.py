"""NC-01 r3 DRAFT amendments (pre-staged for Codex; each item is a separate commit and can be accepted on its own).

Item 2: risk halts and the REC-02 reconcile vocabulary in the reason registry."""
import pytest

import nc01_factories as F
from newcore.domain import Action, ReasonCode

R3_CODES = {
    'risk_gateway.drawdown_kill': (Action.HALT, Action.SKIP),
    'risk_gateway.daily_halt': (Action.HALT, Action.SKIP),
    'reconcile.foreign_quarantine': (Action.RECONCILE, Action.PAUSE, Action.SKIP),
    'reconcile.manual_close': (Action.RECONCILE,),
    'reconcile.manual_add': (Action.RECONCILE, Action.PAUSE),
    'reconcile.stale_read': (Action.RECONCILE, Action.WAIT),
    'reconcile.late_fill_after_not_found': (Action.RECONCILE,),
}


@pytest.mark.parametrize('value', sorted(R3_CODES))
def test_item2_r3_reason_codes_exist_and_are_usable(value):
    from newcore.domain import MEANING
    code = ReasonCode(value)
    assert len(MEANING[code]) > 10 and not code.deprecated
    ids = F.Ids(300)
    for action in R3_CODES[value]:
        d = F.decision_with_intents(ids, ids.id('acct'), action, code)
        assert d.reason is code


def test_item2_r3_codes_are_appended_after_v1():
    order = [r.value for r in ReasonCode]
    assert order.index('risk_gateway.cost_to_stop') < min(order.index(v) for v in R3_CODES)
    assert order[-len(R3_CODES):] == ['risk_gateway.drawdown_kill', 'risk_gateway.daily_halt',
                                      'reconcile.foreign_quarantine', 'reconcile.manual_close', 'reconcile.manual_add',
                                      'reconcile.stale_read', 'reconcile.late_fill_after_not_found']


# ----------------------------------------------------------------------------------------------------------- item 1
def _incident_setup(seed=310):
    p, ids = F.single_lot_portfolio(seed, stop_state='confirmed', in_flight='close')
    return p, ids, F.incident(ids, p.account_id, p)


def test_item1_incident_event_round_trips_and_is_journaled_in_sequence():
    from newcore.domain import (Admission, EventCursor, IncidentRecorded, admit, canonical_bytes, check_event_chain,
                                loads)
    p, ids, inc = _incident_setup()
    ev = F.event(IncidentRecorded, ids, p.account_id, 1, at=F.T0 + 600, incident=inc, reason=inc.kind)
    assert loads(canonical_bytes(ev)) == ev
    assert check_event_chain([ev]) == {}                                  # no effect on intents
    cur = EventCursor(account_id=p.account_id, aggregate_id=F.pf_id(p.account_id), last_sequence=0, applied=())
    cur, how = admit(cur, ev)
    assert how is Admission.APPLY and admit(cur, ev)[1] is Admission.ALREADY_APPLIED


def test_item1_incident_kinds_and_references_are_typed():
    from newcore.domain import IncidentRecorded, InvalidRecord, Side
    p, ids, inc = _incident_setup()
    lt = p.lots[0]
    for kind in (ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE, ReasonCode.OWNERSHIP_UNTRACKED_POSITION,
                 ReasonCode.RECONCILE_FOREIGN_QUARANTINE, ReasonCode.PROTECT_RESTORING, ReasonCode.RISK_DAILY_HALT):
        F.replace(inc, kind=kind)
    F.replace(inc, symbol=None, side=None, intent_refs=(), lot_refs=(), position_refs=(), evidence=(), detail='')
    bad = {
        'entry reason as kind': dict(kind=ReasonCode.ENTRY_SIGNAL),
        'exit reason as kind': dict(kind=ReasonCode.EXIT_STOP),
        'lot id as intent ref': dict(intent_refs=(lt.lot_id,)),
        'intent id as lot ref': dict(lot_refs=(lt.in_flight,)),
        'lot id as position ref': dict(position_refs=(lt.lot_id,)),
        'duplicate ref': dict(lot_refs=(lt.lot_id, lt.lot_id)),
        'evidence not an id': dict(evidence=('see the log',)),
        'side without symbol': dict(symbol=None, side=Side.LONG),
        'bad symbol': dict(symbol='sol usdt'),
        'detail too long': dict(detail='x' * 161),
        'kind as text': dict(kind='reconcile.manual_close'),
        'time in seconds': dict(at_ms=1_791_400_000),
    }
    for name, kw in bad.items():
        with pytest.raises(InvalidRecord):
            F.replace(inc, **kw)
            raise AssertionError(name)
    acct = p.account_id
    for kw in (dict(reason=ReasonCode.RECOVERY_STATE_MISSING),             # reason must be the incident's kind
               dict(at=F.T0 + 400),                                        # journaled before it was observed
               dict(incident=F.replace(inc, account_id=ids.id('acct')))):  # another account's incident
        with pytest.raises(InvalidRecord):
            F.event(IncidentRecorded, ids, acct, 1, **{'at': F.T0 + 600, 'incident': inc, 'reason': inc.kind, **kw})


def test_item1_step0_journal_maps_incidents():
    from newcore.domain import IncidentRecorded
    from newcore.ports.journal import EventKind, header_of
    p, ids, inc = _incident_setup()
    ev = F.event(IncidentRecorded, ids, p.account_id, 1, at=F.T0 + 600, incident=inc, reason=inc.kind)
    assert header_of(ev).kind is EventKind.INCIDENT_RECORDED
