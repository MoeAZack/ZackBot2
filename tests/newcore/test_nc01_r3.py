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


# ----------------------------------------------------------------------------------------------------------- item 3a
def _external_close(seed=330, qty='1.5'):
    """A lot of 1.5 closed OUTSIDE the bot: a RECONCILE (reconcile.manual_close) decision books a post-hoc CLOSE
    (exit.manual), recorded DURABLE, ended by a FINAL exchange_external result built from the venue trades."""
    from decimal import Decimal as D
    from newcore.domain import Authority, Evidence, ExternalTrade, IntentState, OrderResult, Purpose, ResultPhase
    p, ids = F.single_lot_portfolio(seed, stop_state='confirmed')
    acct, lt = p.account_id, p.lots[0]
    dec_id = ids.id('dec')
    it = F.intent(ids, acct, Purpose.CLOSE, lt.symbol, lt.side, D(qty), owner_id=lt.lot_id, state=IntentState.PLANNED,
                  decision_id=dec_id, reason=ReasonCode.EXIT_MANUAL, created=F.T0 + 90_000)
    dec = F.build(F.Decision, decision_id=dec_id, account_id=acct, at_ms=F.T0 + 90_000, action=Action.RECONCILE,
                  reason=ReasonCode.RECONCILE_MANUAL_CLOSE, authority=Authority.RECONCILIATION, symbol=lt.symbol,
                  side=lt.side, subject_id=lt.lot_id, intents=(it,))
    trades = (ExternalTrade(trade_id='9001', at_ms=F.T0 + 80_000, qty=D('1'), price=D('101')),
              ExternalTrade(trade_id='9002', at_ms=F.T0 + 81_000, qty=D(qty) - 1, price=D('102')))
    res = F.build(OrderResult, result_id=ids.id('res'), intent_id=it.intent_id, account_id=acct,
                  client_order_id=it.client_order_id, phase=ResultPhase.FINAL, requested_qty=it.qty,
                  observed_at_ms=F.T0 + 95_000, executed_qty=it.qty, avg_price=D('101.25'),
                  evidence=Evidence.EXCHANGE_EXTERNAL, resolved_by=dec_id, external_trades=trades)
    return p, ids, dec, it, res


def _booking_chain(p, ids, dec, it, res):
    from newcore.domain import DecisionRecorded, IntentRecorded, IntentState, IntentStateChanged, ResultObserved
    acct = p.account_id
    dur = F.replace(it, state=IntentState.DURABLE)
    return [F.event(DecisionRecorded, ids, acct, 1, at=dec.at_ms, decision=dec, reason=dec.reason),
            F.event(IntentRecorded, ids, acct, 2, at=dec.at_ms, intent=dur, reason=dur.reason),
            F.event(ResultObserved, ids, acct, 3, at=res.observed_at_ms, result=res,
                    reason=ReasonCode.RECONCILE_MANUAL_CLOSE),
            F.event(IntentStateChanged, ids, acct, 4, at=res.observed_at_ms, intent_id=it.intent_id,
                    from_state=IntentState.DURABLE, to_state=IntentState.FILLED)]


def test_item3a_an_external_close_is_booked_by_reconcile_and_never_sent():
    from newcore.domain import (Admission, EventCursor, IntentState, admit, canonical_bytes, check_event_chain, loads,
                                may_send, terminal_for)
    p, ids, dec, it, res = _external_close()
    chain = _booking_chain(p, ids, dec, it, res)
    assert check_event_chain(chain) == {}                                    # booked and closed
    assert terminal_for(res) is IntentState.FILLED and res.booked_qty == it.qty
    assert not may_send(F.replace(it, state=IntentState.DURABLE))            # never sent
    cur = EventCursor(account_id=p.account_id, aggregate_id=F.pf_id(p.account_id), last_sequence=0, applied=())
    for ev in chain:
        cur, how = admit(cur, ev)
        assert how is Admission.APPLY
        assert loads(canonical_bytes(ev)) == ev
    lt = p.lots[0]
    carried = F.replace(lt, in_flight=it.intent_id)                          # while booking: carried by its lot
    F.replace(p, positions=(F.replace(p.positions[0], lots=(carried,)),),
              intents=p.intents + (F.replace(it, state=IntentState.DURABLE),))


def test_item3a_external_evidence_rules():
    from decimal import Decimal as D
    from newcore.domain import IntentState, InvalidRecord, check_event_chain, check_result_for_intent
    p, ids, dec, it, res = _external_close()
    bad_results = {
        'no deciding decision': dict(resolved_by=None),
        'no venue trades': dict(external_trades=()),
        'trades do not sum to the booking': dict(external_trades=res.external_trades[:1]),
        'price outside the trades': dict(avg_price=D('150')),
        'duplicate trade id': dict(external_trades=(res.external_trades[0],
                                                    F.replace(res.external_trades[1], trade_id='9001'))),
        'trade after the observation': dict(external_trades=(res.external_trades[0],
                                                             F.replace(res.external_trades[1], at_ms=F.T0 + 99_000))),
        'an exchange order id': dict(exchange_order_id='77'),
    }
    for name, kw in bad_results.items():
        with pytest.raises(InvalidRecord):
            F.replace(res, **kw)
            raise AssertionError(name)
    with pytest.raises(InvalidRecord, match='only an external close carries venue trades'):
        F.replace(F.results(ids, p.account_id, it)['filled'], external_trades=res.external_trades)
    normal = F.replace(it, reason=ReasonCode.EXIT_TIME)
    with pytest.raises(InvalidRecord, match='only books a post-hoc'):        # exchange_external on a normal close
        check_result_for_intent(normal, F.replace(res, client_order_id=normal.client_order_id), F.T0)
    with pytest.raises(InvalidRecord, match='never sent'):                    # a post-hoc booking that was sent
        check_result_for_intent(it, res, F.T0 + 91_000)
    for state in (IntentState.SUBMITTED, IntentState.WORKING, IntentState.UNKNOWN, IntentState.CANCELLING):
        with pytest.raises(InvalidRecord, match='post-hoc booking is never'):
            F.replace(it, state=state)
    with pytest.raises(InvalidRecord, match='external reduce / close of a lot'):    # not lot-owned
        F.replace(it, owner_id=F.pf_id(p.account_id), owner_kind=F.OwnerKind.PORTFOLIO, state=IntentState.NOT_SENT)
    chain = _booking_chain(p, ids, dec, it, res)
    sent = F.replace(chain[3], from_state=IntentState.DURABLE, to_state=IntentState.SUBMITTED, sequence=3)
    with pytest.raises(InvalidRecord):                                         # a send, then the external result
        check_event_chain(chain[:2] + [sent, F.replace(chain[2], sequence=4)])


def test_item3a_only_reconcile_manual_close_books():
    from newcore.domain import Authority, InvalidRecord
    p, ids, dec, it, res = _external_close()
    with pytest.raises(InvalidRecord, match='only from a RECONCILE reconcile.manual_close'):
        F.replace(dec, action=Action.CLOSE, reason=ReasonCode.EXIT_MANUAL, authority=Authority.STRATEGY)
    with pytest.raises(InvalidRecord, match='books post-hoc intents only'):
        F.replace(dec, intents=(F.replace(it, reason=ReasonCode.EXIT_TIME),))
