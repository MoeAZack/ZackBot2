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
        if code is ReasonCode.RECONCILE_LATE_FILL:          # item 3b: a late-fill reconcile names its intent
            d = F.build(F.Decision, decision_id=ids.id('dec'), account_id=ids.id('acct'), at_ms=F.T0, action=action,
                        reason=code, authority=F.authority_for(action, code), subject_id=ids.id('int'))
        else:
            d = F.decision_with_intents(ids, ids.id('acct'), action, code)
        assert d.reason is code


def test_item2_r3_codes_are_appended_after_v1():
    order = [r.value for r in ReasonCode]
    assert order.index('risk_gateway.cost_to_stop') < min(order.index(v) for v in R3_CODES)
    start = order.index('risk_gateway.cost_to_stop') + 1                  # later r3 codes append after these
    assert order[start:start + len(R3_CODES)] == ['risk_gateway.drawdown_kill', 'risk_gateway.daily_halt',
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
    F.replace(inc, symbol=None, side=None, intent_refs=(), lot_refs=(), position_refs=(), evidence=(), detail=None)
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
    """A lot of 1.5 closed OUTSIDE the bot: a RECONCILE (reconcile.external_close) decision books a post-hoc CLOSE
    (exit.manual), recorded DURABLE, ended by a FINAL exchange_external result built from the venue trades."""
    from decimal import Decimal as D
    from newcore.domain import Authority, Evidence, ExternalTrade, IntentState, OrderResult, Purpose, ResultPhase
    p, ids = F.single_lot_portfolio(seed, stop_state='confirmed')
    acct, lt = p.account_id, p.lots[0]
    dec_id = ids.id('dec')
    it = F.intent(ids, acct, Purpose.CLOSE, lt.symbol, lt.side, D(qty), owner_id=lt.lot_id, state=IntentState.PLANNED,
                  decision_id=dec_id, reason=ReasonCode.RECONCILE_EXTERNAL_CLOSE, created=F.T0 + 90_000)
    dec = F.build(F.Decision, decision_id=dec_id, account_id=acct, at_ms=F.T0 + 90_000, action=Action.RECONCILE,
                  reason=ReasonCode.RECONCILE_EXTERNAL_CLOSE, authority=Authority.RECONCILIATION, symbol=lt.symbol,
                  side=lt.side, subject_id=lt.lot_id, intents=(it,))
    sv = dict(venue=F.Venue.BINANCE_USDM, symbol=lt.symbol)
    trades = (ExternalTrade(trade_id='9001', at_ms=F.T0 + 80_000, qty=D('1'), price=D('101'), **sv),
              ExternalTrade(trade_id='9002', at_ms=F.T0 + 81_000, qty=D(qty) - 1, price=D('102'), **sv))
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
                    reason=ReasonCode.RECONCILE_EXTERNAL_CLOSE),
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
    with pytest.raises(InvalidRecord, match='exactly the sum of its venue trades'):      # price in range: only the sum
        F.replace(res, external_trades=res.external_trades[:1], avg_price=res.external_trades[0].price)
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


def test_item3a_only_reconcile_external_close_books():
    from newcore.domain import Authority, InvalidRecord
    p, ids, dec, it, res = _external_close()
    with pytest.raises(InvalidRecord, match='only from a RECONCILE reconcile.external_close'):
        F.replace(dec, action=Action.CLOSE, reason=ReasonCode.EXIT_MANUAL, authority=Authority.OPERATOR)
    with pytest.raises(InvalidRecord, match='books post-hoc intents only'):
        F.replace(dec, intents=(F.replace(it, reason=ReasonCode.EXIT_TIME),))
    with pytest.raises(InvalidRecord, match='books post-hoc intents only'):
        F.replace(dec, intents=())                                               # a booking books something
    with pytest.raises(InvalidRecord, match='books post-hoc intents only'):
        F.replace(dec, action=Action.PAUSE, intents=(), authority=Authority.RECONCILIATION)


# ----------------------------------------------------------------------------------------------------------- item 3b
def _late_fill(seed=340):
    """A lot CLOSE sent at T0, lost (UNKNOWN), decided not_found_corroborated (nothing executed) - then the venue's
    own FINAL record of that client id shows it filled. Returns the pieces and the chain up to the corroboration."""
    from newcore.domain import (DecisionRecorded, IntentRecorded, IntentState, IntentStateChanged, ResultObserved)
    p, ids = F.single_lot_portfolio(seed, stop_state='confirmed')
    acct, lt = p.account_id, p.lots[0]
    dec_id = ids.id('dec')
    it = F.intent(ids, acct, F.Purpose.CLOSE, lt.symbol, lt.side, lt.qty, owner_id=lt.lot_id,
                  state=IntentState.PLANNED, decision_id=dec_id, reason=ReasonCode.EXIT_TIME, created=F.T0 - 10_000)
    dec = F.decision(ids, acct, Action.CLOSE, ReasonCode.EXIT_TIME, (it,), dec_id=dec_id, at=F.T0 - 10_000)
    rs = F.results(ids, acct, it)
    late = F.replace(rs['filled'], result_id=ids.id('res'), observed_at_ms=F.T0 + 60_000,
                     supersedes_result_id=rs['corroborated'].result_id)          # PR #44 P1-b: names the prior fact
    E = lambda cls, n, at, **kw: F.event(cls, ids, acct, n, at=at, **kw)          # noqa: E731
    head = [E(DecisionRecorded, 1, dec.at_ms, decision=dec, reason=dec.reason),
            E(IntentRecorded, 2, dec.at_ms, intent=F.replace(it, state=IntentState.DURABLE), reason=it.reason),
            E(IntentStateChanged, 3, F.T0, intent_id=it.intent_id, from_state=IntentState.DURABLE,
              to_state=IntentState.SUBMITTED),
            E(IntentStateChanged, 4, F.T0 + 1000, intent_id=it.intent_id, from_state=IntentState.SUBMITTED,
              to_state=IntentState.UNKNOWN),
            E(ResultObserved, 5, F.T0 + 30_000, result=rs['corroborated'])]
    return p, ids, acct, it, rs, late, head, E


def _late_fill_decision(ids, acct, it, late, at=F.T0 + 61_000):
    from newcore.domain import Authority
    return F.build(F.Decision, decision_id=ids.id('dec'), account_id=acct, at_ms=at, action=Action.RECONCILE,
                   reason=ReasonCode.RECONCILE_LATE_FILL, authority=Authority.RECONCILIATION, symbol=it.symbol,
                   side=it.side, subject_id=it.intent_id, evidence=(late.result_id,))


def test_item3b_a_final_exchange_record_supersedes_not_found_corroborated_after_close():
    from newcore.domain import (Admission, DecisionRecorded, EventCursor, IntentState, IntentStateChanged,
                                ResultObserved, admit, canonical_bytes, check_event_chain, loads, supersedes)
    p, ids, acct, it, rs, late, head, E = _late_fill()
    assert supersedes(rs['corroborated'], late)
    closed = E(IntentStateChanged, 6, F.T0 + 30_000, intent_id=it.intent_id, from_state=IntentState.UNKNOWN,
               to_state=IntentState.CANCELLED)                      # closed on the corroboration ("nothing executed")
    fix = _late_fill_decision(ids, acct, it, late)
    chain = head + [closed, E(ResultObserved, 7, late.observed_at_ms, result=late,
                              reason=ReasonCode.RECONCILE_LATE_FILL),
                    E(DecisionRecorded, 8, fix.at_ms, decision=fix, reason=fix.reason)]
    assert check_event_chain(chain) == {}
    cur = EventCursor(account_id=acct, aggregate_id=F.pf_id(acct), last_sequence=0, applied=())
    for ev in chain:
        cur, how = admit(cur, ev)
        assert how is Admission.APPLY
        assert loads(canonical_bytes(ev)) == ev


def test_item3b_the_original_intent_stays_terminal():
    """Ruling 4: the late record is a reconciliation fact about a TERMINAL intent. Before the intent closes on its
    corroborated final it is refused (the terminal step comes first); after, the intent stays CANCELLED and the late
    fill is never turned into a lifecycle step."""
    from newcore.domain import IntentState, IntentStateChanged, InvalidRecord, ResultObserved, check_event_chain
    p, ids, acct, it, rs, late, head, E = _late_fill()
    early = E(ResultObserved, 6, late.observed_at_ms, result=late)
    with pytest.raises(InvalidRecord, match='event after the final result'):
        check_event_chain(head + [early])
    closed = E(IntentStateChanged, 6, F.T0 + 30_000, intent_id=it.intent_id, from_state=IntentState.UNKNOWN,
               to_state=IntentState.CANCELLED)
    sup = E(ResultObserved, 7, late.observed_at_ms, result=late)
    for to in (IntentState.FILLED, IntentState.CANCELLED):
        with pytest.raises(InvalidRecord, match='is not a lifecycle step'):     # terminal never moves
            check_event_chain(head + [closed, sup, E(IntentStateChanged, 8, late.observed_at_ms,
                                                     intent_id=it.intent_id, from_state=IntentState.CANCELLED,
                                                     to_state=to)])


def test_item3b_the_same_journal_bytes_fold_identically_and_apply_once():
    """Ruling 4: replaying the journal (decoded from its bytes, any number of times) folds to the same result, and a
    re-delivered event is ALREADY_APPLIED - the late fill is never applied twice."""
    from newcore.domain import (Admission, DecisionRecorded, EventCursor, IntentState, IntentStateChanged,
                                ResultObserved, admit, canonical_bytes, check_event_chain, loads)
    p, ids, acct, it, rs, late, head, E = _late_fill()
    fix = _late_fill_decision(ids, acct, it, late)
    chain = head + [E(IntentStateChanged, 6, F.T0 + 30_000, intent_id=it.intent_id, from_state=IntentState.UNKNOWN,
                      to_state=IntentState.CANCELLED),
                    E(ResultObserved, 7, late.observed_at_ms, result=late, reason=ReasonCode.RECONCILE_LATE_FILL),
                    E(DecisionRecorded, 8, fix.at_ms, decision=fix, reason=fix.reason)]
    blob = [canonical_bytes(ev) for ev in chain]
    folds = []
    for _ in range(2):                                                          # two restarts from the same bytes
        events = [loads(b) for b in blob]
        assert check_event_chain(events) == check_event_chain(chain) == {}
        cur = EventCursor(account_id=acct, aggregate_id=F.pf_id(acct), last_sequence=0, applied=())
        for ev in events:
            cur, how = admit(cur, ev)
            assert how is Admission.APPLY
        for ev in events:                                                       # a repeated reconcile pass
            assert admit(cur, ev) == (cur, Admission.ALREADY_APPLIED)
        folds.append(cur)
    assert folds[0] == folds[1]


def test_item3b_only_an_executed_exchange_record_of_the_same_order_supersedes():
    from decimal import Decimal as D
    from newcore.domain import Evidence, ExchangeStatus, ResultPhase, supersedes
    p, ids, acct, it, rs, late, head, E = _late_fill()
    cor = rs['corroborated']
    assert supersedes(cor, late)
    assert supersedes(cor, F.replace(rs['partial_cancel'], observed_at_ms=F.T0 + 60_000,
                                     supersedes_result_id=cor.result_id))                 # any execution
    not_superseding = {
        'nothing executed': F.replace(late, executed_qty=D('0'), avg_price=None, supersedes_result_id=None,
                                      exchange_status=ExchangeStatus.CANCELED),
        'a decided adoption, not exchange evidence': F.replace(rs['adopted'], observed_at_ms=F.T0 + 60_000),
        'a refusal': F.replace(rs['refused'], observed_at_ms=F.T0 + 60_000),
        'not final': F.replace(rs['known'], observed_at_ms=F.T0 + 60_000),
        'older than the corroboration': F.replace(late, observed_at_ms=cor.observed_at_ms - 1),
        'another client id': F.replace(late, client_order_id=ids.cid('c')),
        'another intent': F.replace(late, intent_id=ids.id('int')),
    }
    for name, new in not_superseding.items():
        assert not supersedes(cor, new), name
    for prior in (rs['filled'], rs['refused'], rs['adopted'], rs['known']):
        assert not supersedes(prior, late)                          # only a corroborated not-found is superseded
        assert not supersedes(prior, F.replace(late, supersedes_result_id=prior.result_id))   # even when named
    assert cor.phase is ResultPhase.FINAL and cor.evidence is Evidence.NOT_FOUND_CORROBORATED


def test_item3b_refusals():
    from newcore.domain import (DecisionRecorded, IntentState, IntentStateChanged, InvalidRecord, ResultObserved,
                                check_event_chain)
    p, ids, acct, it, rs, late, head, E = _late_fill()
    closed = E(IntentStateChanged, 6, F.T0 + 30_000, intent_id=it.intent_id, from_state=IntentState.UNKNOWN,
               to_state=IntentState.CANCELLED)
    sup = E(ResultObserved, 7, late.observed_at_ms, result=late)
    fix = _late_fill_decision(ids, acct, it, late)
    fix_ev = lambda n, d=fix: E(DecisionRecorded, n, d.at_ms, decision=d, reason=d.reason)   # noqa: E731
    again = F.replace(late, result_id=ids.id('res'), observed_at_ms=F.T0 + 62_000)
    cases = {
        'superseded twice': ('event after the final result',
                             head + [closed, sup, E(ResultObserved, 8, again.observed_at_ms, result=again)]),
        'a zero fill after the close': ('event after the final result',
                                        head + [closed, E(ResultObserved, 7, late.observed_at_ms,
                                                          result=F.replace(rs['refused'], result_id=ids.id('res'),
                                                                           observed_at_ms=F.T0 + 60_000))]),
        'late-fill decision without the record': ('names a journaled superseding', head + [closed, fix_ev(7)]),
        'late-fill decision names another result': (
            'names a journaled superseding',
            head + [closed, sup, fix_ev(8, F.replace(fix, evidence=(rs['corroborated'].result_id,)))]),
        'late fill reconciled twice': ('a late fill is reconciled once',
                                       head + [closed, sup, fix_ev(8),
                                               fix_ev(9, F.replace(fix, decision_id=ids.id('dec')))]),
    }
    for name, (message, chain) in cases.items():
        with pytest.raises(InvalidRecord, match=message):
            check_event_chain(chain)
            raise AssertionError(name)


def test_item3b_a_late_fill_decision_is_a_reconcile_about_one_intent():
    from newcore.domain import Authority, InvalidRecord
    p, ids, acct, it, rs, late, head, E = _late_fill()
    fix = _late_fill_decision(ids, acct, it, late)
    with pytest.raises(InvalidRecord, match='about one intent'):
        F.replace(fix, subject_id=None)
    with pytest.raises(InvalidRecord):
        F.replace(fix, subject_id=p.lots[0].lot_id)                 # a lot is not the intent it corrects
    with pytest.raises(InvalidRecord):
        F.replace(fix, action=Action.CLOSE, authority=Authority.STRATEGY)


# --------------------------------------------------------------------------------- r3a rulings 1 + 2 (Codex on #13)
def test_ruling2_reconcile_external_close_never_authorizes_a_send():
    """A historical exchange fact can never be read as permission to send: the booking reason is accepted ONLY by a
    RECONCILE reconcile.external_close decision (exhaustively over every action x reason), may_send is False, and the
    event chain refuses the send step itself (the step-0 journal gate does the same: test_ports_journal)."""
    from newcore.domain import (Authority, IntentState, IntentStateChanged, InvalidRecord, check_event_chain,
                                may_send)
    p, ids, dec, it, res = _external_close()
    accepted = []
    for action in Action:
        for reason in ReasonCode:
            try:
                F.build(F.Decision, decision_id=dec.decision_id, account_id=dec.account_id, at_ms=dec.at_ms,
                        action=action, reason=reason, authority=F.authority_for(action, reason), symbol=dec.symbol,
                        side=dec.side, subject_id=dec.subject_id, intents=(it,))
                accepted.append((action, reason))
            except InvalidRecord:
                pass
    assert accepted == [(Action.RECONCILE, ReasonCode.RECONCILE_EXTERNAL_CLOSE)]
    dur = F.replace(it, state=IntentState.DURABLE)
    assert not may_send(dur)
    chain = _booking_chain(p, ids, dec, it, res)
    send = F.replace(chain[3], to_state=IntentState.SUBMITTED, sequence=3)
    with pytest.raises(InvalidRecord, match='a post-hoc booking is never sent'):
        check_event_chain(chain[:2] + [send])                                   # refused at the send step itself
    drop = F.replace(chain[3], to_state=IntentState.CANCELLING, sequence=3)
    with pytest.raises(InvalidRecord, match='a post-hoc booking is never sent'):
        check_event_chain(chain[:2] + [drop])
    for purpose, reason in ((F.Purpose.ADD, ReasonCode.RECONCILE_EXTERNAL_CLOSE),
                            (F.Purpose.ENTRY, ReasonCode.RECONCILE_EXTERNAL_CLOSE)):
        with pytest.raises(InvalidRecord, match='intent needs a entry.* reason'):
            F.intent(ids, p.account_id, purpose, owner_id=p.lots[0].lot_id if purpose is F.Purpose.ADD else None,
                     reason=reason)
    assert Authority.RECONCILIATION is dec.authority


def test_ruling2_exit_manual_is_an_operator_requested_bot_close():
    from newcore.domain import (Authority, Evidence, IntentState, InvalidRecord, MEANING, check_result_for_intent,
                                may_send)
    assert 'operator-requested bot close' in MEANING[ReasonCode.EXIT_MANUAL]
    p, ids, dec, it, res = _external_close()
    lt = p.lots[0]
    dec_id = ids.id('dec')
    close = F.intent(ids, p.account_id, F.Purpose.CLOSE, lt.symbol, lt.side, lt.qty, owner_id=lt.lot_id,
                     state=IntentState.PLANNED, decision_id=dec_id, reason=ReasonCode.EXIT_MANUAL)
    d = F.decision(ids, p.account_id, Action.CLOSE, ReasonCode.EXIT_MANUAL, (close,), dec_id=dec_id)
    assert d.authority is Authority.OPERATOR                                    # the owner's command
    assert may_send(F.replace(close, state=IntentState.DURABLE))               # the bot sends it
    with pytest.raises(InvalidRecord, match='operator reasons'):
        F.replace(d, authority=Authority.STRATEGY)
    booked = F.replace(res, intent_id=close.intent_id, client_order_id=close.client_order_id)
    assert booked.evidence is Evidence.EXCHANGE_EXTERNAL
    with pytest.raises(InvalidRecord, match='only books a post-hoc'):         # never a booking
        check_result_for_intent(close, booked, None)


def test_ruling1_an_external_increase_is_quarantined_protect_only():
    """3a books an external REDUCE / CLOSE only. A venue position LARGER than owned (manual add / foreign) stays in
    quarantine: its RECONCILE decision may only protect, never book or close."""
    from decimal import Decimal as D
    from newcore.domain import Authority, IntentState, InvalidRecord
    p, ids, dec, it, res = _external_close()
    lt = p.lots[0]
    for reason in (ReasonCode.RECONCILE_MANUAL_ADD, ReasonCode.RECONCILE_FOREIGN_QUARANTINE):
        dec_id = ids.id('dec')
        stop = F.intent(ids, p.account_id, F.Purpose.PROTECT, lt.symbol, lt.side, lt.qty, owner_id=lt.lot_id,
                        state=IntentState.PLANNED, decision_id=dec_id, stop_price=D('95'), created=F.T0)
        ok = F.build(F.Decision, decision_id=dec_id, account_id=p.account_id, at_ms=F.T0, action=Action.RECONCILE,
                     reason=reason, authority=Authority.RECONCILIATION, intents=(stop,))
        assert ok.intents == (stop,)
        for purpose, r in ((F.Purpose.CLOSE, ReasonCode.EXIT_TIME), (F.Purpose.REDUCE, ReasonCode.EXIT_TP1),
                           (F.Purpose.CLOSE, ReasonCode.RECONCILE_EXTERNAL_CLOSE)):
            other = F.intent(ids, p.account_id, purpose, lt.symbol, lt.side, D('0.5'), owner_id=lt.lot_id,
                             state=IntentState.PLANNED, decision_id=dec_id, reason=r, created=F.T0)
            with pytest.raises(InvalidRecord):
                F.replace(ok, intents=(stop, other))
                raise AssertionError((reason, purpose, r))
        with pytest.raises(InvalidRecord, match='protect-only, never booked'):
            F.replace(ok, intents=(stop, F.intent(ids, p.account_id, F.Purpose.CLOSE, lt.symbol, lt.side, D('0.5'),
                                                  owner_id=lt.lot_id, state=IntentState.PLANNED, decision_id=dec_id,
                                                  reason=ReasonCode.EXIT_TIME, created=F.T0)))
# ----------------------------------------------------------------------------------------------------------- item 4
def _resting_target(seed=350, qty=None, reason=ReasonCode.EXIT_TP1, purpose=None):
    """A lot of 1.5 with a resting reduce-only take-profit target (WORKING) in flight."""
    from decimal import Decimal as D
    from newcore.domain import IntentState, OrderType, Purpose
    p, ids = F.single_lot_portfolio(seed, stop_state='confirmed', in_flight='reduce')
    lt = p.lots[0]
    old = next(i for i in p.intents if i.intent_id == lt.in_flight)
    purpose = purpose or Purpose.REDUCE
    tgt = F.replace(old, purpose=purpose, order_type=OrderType.LIMIT_REDUCE_ONLY, price=D('112.5'), reason=reason,
                    state=IntentState.WORKING, qty=qty or old.qty)
    pf = F.replace(p, intents=tuple(tgt if i.intent_id == old.intent_id else i for i in p.intents))
    return pf, ids, lt, tgt


def test_item4_a_resting_reduce_only_target_is_a_lot_reduce_or_close():
    from decimal import Decimal as D
    from newcore.domain import Purpose, canonical_bytes, loads
    pf, ids, lt, tgt = _resting_target()
    assert tgt.reduce_only and not tgt.opening and not tgt.pullable
    assert loads(canonical_bytes(tgt)) == tgt and loads(canonical_bytes(pf)) == pf     # codec round trips
    F.rules().check_intent(tgt)                                                       # on the venue grid
    whole = _resting_target(purpose=Purpose.CLOSE, qty=D('1.5'), reason=ReasonCode.EXIT_TAKE_PROFIT)[3]
    assert loads(canonical_bytes(whole)) == whole
    for reason in (ReasonCode.EXIT_LADDER, ReasonCode.EXIT_BASKET_TP, ReasonCode.EXIT_BASKET_TP_PART):
        F.replace(tgt, reason=reason)


def test_item4_resting_target_refusals():
    from decimal import Decimal as D
    from newcore.domain import Capability, InvalidRecord, OrderType, Purpose
    pf, ids, lt, tgt = _resting_target()
    acct = pf.account_id
    cases = {
        'an opening add': ('a lot REDUCE / CLOSE',
                           lambda: F.intent(ids, acct, Purpose.ADD, owner_id=lt.lot_id,
                                            order_type=OrderType.LIMIT_REDUCE_ONLY, price=D('99'))),
        'an entry': ('a lot REDUCE / CLOSE', lambda: F.intent(ids, acct, Purpose.ENTRY, order_type=OrderType.LIMIT_REDUCE_ONLY,
                                                              price=D('99'))),
        'no price': ('a limit order has a price', lambda: F.replace(tgt, price=None)),
        'a stop price': ('only a stop has a stop price', lambda: F.replace(tgt, stop_price=D('90'))),
        'not a take-profit': ('take-profit reason', lambda: F.replace(tgt, reason=ReasonCode.EXIT_TIME)),
        'an external close': ('take-profit reason', lambda: F.replace(tgt, reason=ReasonCode.EXIT_MANUAL,
                                                                     state=F.IntentState.DURABLE)),
    }
    for name, (message, build) in cases.items():
        with pytest.raises(InvalidRecord, match=message):
            build()
            raise AssertionError(name)
    with pytest.raises(InvalidRecord, match='not a multiple of tick'):
        F.rules().check_intent(F.replace(tgt, price=D('112.505')))
    no_reduce_only = tuple(c for c in F.rules().capabilities if c is not Capability.REDUCE_ONLY)
    with pytest.raises(InvalidRecord, match='resting reduce-only limit is not supported'):
        F.rules(caps=no_reduce_only).check_intent(tgt)


# ----------------------------------------------------------------------------------------- r3b ruling 5 + 6 (item 4)
def _target_chain(seed=390, side=None):
    """A lot of 1.5 and a resting reduce-only target of 1.0 at 112.5: decided, recorded, sent, WORKING."""
    from decimal import Decimal as D
    from newcore.domain import (DecisionRecorded, IntentRecorded, IntentState, IntentStateChanged, OrderType,
                                Purpose)
    p, ids = F.single_lot_portfolio(seed, stop_state='confirmed')
    acct, lt = p.account_id, p.lots[0]
    dec_id = ids.id('dec')
    tgt = F.intent(ids, acct, Purpose.REDUCE, lt.symbol, lt.side, D('1'), owner_id=lt.lot_id,
                   state=IntentState.PLANNED, decision_id=dec_id, reason=ReasonCode.EXIT_TP1, created=F.T0 - 10_000,
                   order_type=OrderType.LIMIT_REDUCE_ONLY, price=D('112.5'))
    dec = F.decision(ids, acct, Action.REDUCE, ReasonCode.EXIT_TP1, (tgt,), dec_id=dec_id, at=F.T0 - 10_000)
    E = lambda cls, n, at, **kw: F.event(cls, ids, acct, n, at=at, **kw)                    # noqa: E731
    chain = [E(DecisionRecorded, 1, dec.at_ms, decision=dec, reason=dec.reason),
             E(IntentRecorded, 2, dec.at_ms, intent=F.replace(tgt, state=IntentState.DURABLE), reason=tgt.reason),
             E(IntentStateChanged, 3, F.T0, intent_id=tgt.intent_id, from_state=IntentState.DURABLE,
               to_state=IntentState.SUBMITTED),
             E(IntentStateChanged, 4, F.T0 + 500, intent_id=tgt.intent_id, from_state=IntentState.SUBMITTED,
               to_state=IntentState.WORKING)]
    return p, ids, acct, lt, tgt, chain, E


def _target_result(ids, tgt, phase, executed, avg, status, at=F.T0 + 60_000):
    from decimal import Decimal as D
    from newcore.domain import Evidence, OrderResult
    return F.build(OrderResult, result_id=ids.id('res'), intent_id=tgt.intent_id, account_id=tgt.account_id,
                   client_order_id=tgt.client_order_id, phase=phase, requested_qty=tgt.qty, observed_at_ms=at,
                   exchange_order_id='4401', exchange_status=status,
                   executed_qty=None if executed is None else D(executed), avg_price=None if avg is None else D(avg),
                   evidence=Evidence.EXCHANGE_FINAL if phase.value == 'final' else None)


def test_ruling5_target_is_a_reduce_only_gtc_limit_not_post_only():
    from newcore.domain import Capability, OrderType, canonical_bytes, loads
    pf, ids, lt, tgt = _resting_target()
    assert tgt.order_type is OrderType.LIMIT_REDUCE_ONLY and tgt.reduce_only and not tgt.pullable
    no_post_only = tuple(c for c in F.rules().capabilities if c is not Capability.POST_ONLY)
    F.rules(caps=no_post_only).check_intent(tgt)                    # needs reduce-only, never post-only
    assert loads(canonical_bytes(tgt)) == tgt


def test_ruling5_partial_fill_then_cancel_then_fallback_to_market():
    """The target fills 0.4 and the rest is cancelled (time exit): KNOWN partial -> CANCELLING -> FINAL partial ->
    CANCELLED; the remaining 1.1 of the lot then closes at market."""
    from decimal import Decimal as D
    from newcore.domain import (DecisionRecorded, ExchangeStatus, IntentRecorded, IntentState, IntentStateChanged,
                                Purpose, ResultObserved, ResultPhase, check_event_chain, terminal_for)
    p, ids, acct, lt, tgt, chain, E = _target_chain()
    known = _target_result(ids, tgt, ResultPhase.KNOWN, None, None, ExchangeStatus.PARTIALLY_FILLED,
                           at=F.T0 + 30_000)
    final = _target_result(ids, tgt, ResultPhase.FINAL, '0.4', '112.5', ExchangeStatus.CANCELED)
    assert terminal_for(final) is IntentState.CANCELLED and final.booked_qty == D('0.4')
    dec_id = ids.id('dec')
    mkt = F.intent(ids, acct, Purpose.CLOSE, lt.symbol, lt.side, D('1.1'), owner_id=lt.lot_id,
                   state=IntentState.PLANNED, decision_id=dec_id, reason=ReasonCode.EXIT_TIME, created=F.T0 + 61_000)
    mdec = F.decision(ids, acct, Action.CLOSE, ReasonCode.EXIT_TIME, (mkt,), dec_id=dec_id, at=F.T0 + 61_000)
    from newcore.domain import Evidence
    mfill = F.build(F.OrderResult, result_id=ids.id('res'), intent_id=mkt.intent_id, account_id=acct,
                    client_order_id=mkt.client_order_id, phase=ResultPhase.FINAL, requested_qty=mkt.qty,
                    observed_at_ms=F.T0 + 63_000, exchange_order_id='4402', exchange_status=ExchangeStatus.FILLED,
                    executed_qty=mkt.qty, avg_price=D('108'), evidence=Evidence.EXCHANGE_FINAL)
    full = chain + [
        E(ResultObserved, 5, known.observed_at_ms, result=known),
        E(IntentStateChanged, 6, F.T0 + 59_000, intent_id=tgt.intent_id, from_state=IntentState.WORKING,
          to_state=IntentState.CANCELLING),
        E(ResultObserved, 7, final.observed_at_ms, result=final),
        E(IntentStateChanged, 8, final.observed_at_ms, intent_id=tgt.intent_id, from_state=IntentState.CANCELLING,
          to_state=IntentState.CANCELLED),
        E(DecisionRecorded, 9, mdec.at_ms, decision=mdec, reason=mdec.reason),
        E(IntentRecorded, 10, mdec.at_ms, intent=F.replace(mkt, state=IntentState.DURABLE), reason=mkt.reason),
        E(IntentStateChanged, 11, F.T0 + 62_000, intent_id=mkt.intent_id, from_state=IntentState.DURABLE,
          to_state=IntentState.SUBMITTED),
        E(ResultObserved, 12, mfill.observed_at_ms, result=mfill),
        E(IntentStateChanged, 13, mfill.observed_at_ms, intent_id=mkt.intent_id, from_state=IntentState.SUBMITTED,
          to_state=IntentState.FILLED)]
    assert check_event_chain(full) == {}
    assert final.booked_qty + mfill.booked_qty == lt.qty                          # the lot closes exactly


def test_ruling5_cancel_replace_of_a_resting_target():
    """A resting target is replaced through the explicit link: by a re-priced target, or by a market close
    (fallback) - one leg for the reduce bound either way."""
    from decimal import Decimal as D
    from newcore.domain import IntentState, InvalidRecord, OrderType, Purpose
    pf, ids, lt, tgt = _resting_target()
    old = F.replace(tgt, state=IntentState.CANCELLING)
    others = tuple(i for i in pf.intents if i.intent_id != tgt.intent_id)
    for order_type, price, reason in ((OrderType.LIMIT_REDUCE_ONLY, D('111'), ReasonCode.EXIT_TP1),
                                      (OrderType.MARKET, None, ReasonCode.EXIT_TIME)):
        new = F.intent(ids, pf.account_id, Purpose.REDUCE, lt.symbol, lt.side, tgt.qty, owner_id=lt.lot_id,
                       state=IntentState.SUBMITTED, reason=reason, order_type=order_type, price=price,
                       replaces=tgt.intent_id)
        lot = F.replace(lt, in_flight=new.intent_id)
        F.portfolio(pf.account_id, (F.position(ids, [lot]),), others + (old, new))
        with pytest.raises(InvalidRecord, match='must be CANCELLING'):         # the old target must be cancelling
            F.portfolio(pf.account_id, (F.position(ids, [lot]),), others + (tgt, new))


def test_ruling5_gap_through_fills_at_the_limit_or_better():
    """Price gaps through the target: a limit fills at its price or better, never worse (both sides)."""
    from newcore.domain import ExchangeStatus, InvalidRecord, ResultPhase, Side, check_result_for_intent
    p, ids, acct, lt, tgt, chain, E = _target_chain()
    for avg in ('112.5', '115.25'):                                             # long target: sells at 112.5+
        check_result_for_intent(tgt, _target_result(ids, tgt, ResultPhase.FINAL, '1', avg, ExchangeStatus.FILLED),
                                F.T0)
    with pytest.raises(InvalidRecord, match='never fills worse than its limit'):
        check_result_for_intent(tgt, _target_result(ids, tgt, ResultPhase.FINAL, '1', '112.49',
                                                    ExchangeStatus.FILLED), F.T0)
    from decimal import Decimal as D
    short = F.replace(tgt, side=Side.SHORT, price=D('90'))
    check_result_for_intent(short, _target_result(ids, short, ResultPhase.FINAL, '1', '87', ExchangeStatus.FILLED),
                            F.T0)                                               # short target: buys at 90-
    with pytest.raises(InvalidRecord, match='never fills worse than its limit'):
        check_result_for_intent(short, _target_result(ids, short, ResultPhase.FINAL, '1', '90.01',
                                                      ExchangeStatus.FILLED), F.T0)


def test_ruling5_restart_reads_the_resting_target_back_from_bytes():
    from newcore.domain import Admission, EventCursor, admit, canonical_bytes, check_event_chain, loads
    p, ids, acct, lt, tgt, chain, E = _target_chain()
    events = [loads(canonical_bytes(ev)) for ev in chain]
    assert events == chain
    live = check_event_chain(events)
    it, state, sent = live[tgt.intent_id]
    assert it.price == tgt.price and it.order_type is tgt.order_type and state.value == 'working' and sent == F.T0
    cur = EventCursor(account_id=acct, aggregate_id=F.pf_id(acct), last_sequence=0, applied=())
    for ev in events:
        cur, how = admit(cur, ev)
        assert how is Admission.APPLY
    pf, _, _, carried = _resting_target()
    assert loads(canonical_bytes(pf)) == pf and carried in loads(canonical_bytes(pf)).intents
    from newcore.domain import Snapshot, fold_facts                          # r3a shape: the snapshot carries facts
    snap = Snapshot(account_id=pf.account_id, generation=pf.generation, last_sequence=len(chain),
                    written_at_ms=F.T0 + 90_000, writer_build='nc01-test', portfolio=pf, facts=fold_facts(chain))
    back = loads(canonical_bytes(snap))
    assert back == snap and carried in back.portfolio.intents


def test_ruling6_take_profit_reasons_are_route_neutral():
    from newcore.domain import MEANING
    for code in (ReasonCode.EXIT_TAKE_PROFIT, ReasonCode.EXIT_BASKET_TP):
        assert 'market' not in MEANING[code]
    assert 'basket' in MEANING[ReasonCode.EXIT_BASKET_TP]


# ------------------------------------------------------------------- Codex P1 on 02493b6: the resting target's orphan
def _orphaned_target(how, seed=360):
    """The lot of a WORKING resting target disappears while the target is being cancelled: by a stop fill, an external
    close, or a side flip. The target is re-owned by the portfolio aggregate as cancel-only work (the generic orphan
    form: owner_kind PORTFOLIO, owner_id the portfolio id, CANCELLING). Returns (portfolio, ids, orphan target)."""
    from decimal import Decimal as D
    from newcore.domain import IntentState, OwnerKind, Side
    pf, ids, lt, tgt = _resting_target(seed)
    acct = pf.account_id
    orphan = lambda i: F.replace(i, owner_id=F.pf_id(acct), owner_kind=OwnerKind.PORTFOLIO,       # noqa: E731
                                 state=IntentState.CANCELLING)
    target = orphan(tgt)
    rest = tuple(i for i in pf.intents if i.intent_id != tgt.intent_id)
    positions = ()
    if how == 'stop_fill':                       # the stop filled: the lot and its stop are gone, the target remains
        rest = ()
    elif how == 'external_close':                # closed outside the bot: every owned order is cancel-only work
        rest = tuple(orphan(i) for i in rest)
    elif how == 'side_flip':                     # closed and re-opened the other way: a new lot, the old work orphaned
        rest = tuple(orphan(i) for i in rest)
        flip = Side.SHORT if lt.side is Side.LONG else Side.LONG
        new_lot, carried = F.lot(ids, acct, lt.symbol, flip, D('2'))
        positions = (F.position(ids, [new_lot]),)
        rest += tuple(carried)
    else:
        raise AssertionError(how)
    return F.portfolio(acct, positions, rest + (target,)), ids, target


HOW = ('stop_fill', 'external_close', 'side_flip')


@pytest.mark.parametrize('how', HOW)
def test_p1_a_resting_target_whose_lot_is_gone_is_retained_as_a_cancel_only_orphan(how):
    from newcore.domain import OrderType, OwnerFamily, OwnerKind
    pf, ids, target = _orphaned_target(how)
    assert target in pf.intents
    assert target.order_type is OrderType.LIMIT_REDUCE_ONLY and target.reduce_only and target.price is not None
    assert target.orphan and target.owner_kind is OwnerKind.PORTFOLIO and target.owner_id == pf.portfolio_id
    assert target.family is OwnerFamily.ORPHAN and target.state.value == 'cancelling'
    from newcore.domain.portfolio import owned_client_ids
    assert target.client_order_id in owned_client_ids(pf)                    # still owned: never dropped


@pytest.mark.parametrize('how', HOW)
def test_p1_the_orphan_target_survives_snapshot_journal_bytes_and_a_restart(how):
    from newcore.domain import (Admission, EventCursor, IntentState, IntentStateChanged, OwnerKind, Snapshot, admit,
                                canonical_bytes, fold_facts, loads)
    p, ids, acct, lt, tgt, chain, E = _target_chain(seed=391)
    chain = chain + [E(IntentStateChanged, 5, F.T0 + 30_000, intent_id=tgt.intent_id,
                       from_state=IntentState.WORKING, to_state=IntentState.CANCELLING)]   # cancel requested
    events = [loads(canonical_bytes(ev)) for ev in chain]
    assert events == chain
    cur = EventCursor(account_id=acct, aggregate_id=F.pf_id(acct), last_sequence=0, applied=())
    for ev in events:
        cur, how_ = admit(cur, ev)
        assert how_ is Admission.APPLY
    pf, _, target = _orphaned_target(how)
    snap = Snapshot(account_id=pf.account_id, generation=pf.generation, last_sequence=len(chain),
                    written_at_ms=F.T0 + 90_000, writer_build='nc01-test', portfolio=pf, facts=fold_facts([]))
    back = loads(canonical_bytes(snap))                                       # restart: the snapshot read from bytes
    assert back == snap and target in back.portfolio.intents
    kept = next(i for i in back.portfolio.intents if i.intent_id == target.intent_id)
    assert kept.owner_kind is OwnerKind.PORTFOLIO and kept.owner_id == back.portfolio.portfolio_id
    assert kept.state is IntentState.CANCELLING and kept.price == target.price
    assert loads(canonical_bytes(target)) == target                           # the intent record alone round trips


def test_p1_a_portfolio_owned_target_must_be_cancelling_and_owned_by_this_portfolio():
    from newcore.domain import IntentState, InvalidRecord, OwnerKind
    from newcore.domain.orders import CANCEL_ONLY
    pf, ids, target = _orphaned_target('external_close')
    others = tuple(i for i in pf.intents if i.intent_id != target.intent_id)
    for state in IntentState:
        if state in CANCEL_ONLY:
            continue
        with pytest.raises(InvalidRecord, match='cancel-only'):              # still sendable: refused on the record
            F.replace(target, state=state)
            raise AssertionError(state)
    for state in CANCEL_ONLY - {IntentState.CANCELLING}:                     # terminal: never owned by a portfolio
        with pytest.raises(InvalidRecord, match='not a live state'):
            F.replace(pf, intents=others + (F.replace(target, state=state),))
            raise AssertionError(state)
    stranger = F.Ids(999).id('acct')
    wrong = F.replace(target, owner_id=F.pf_id(stranger))
    with pytest.raises(InvalidRecord, match='owned by this portfolio aggregate'):
        F.replace(pf, intents=others + (wrong,))
    with pytest.raises(InvalidRecord):                                       # a lot id under the portfolio kind
        F.replace(target, owner_id=F.Ids(6).id('lot'))
    with pytest.raises(InvalidRecord, match='not owned by entry_intent'):    # an entry-owned target stays refused
        F.replace(target, owner_kind=OwnerKind.ENTRY_INTENT, owner_id=F.Ids(5).id('int'), state=IntentState.WORKING)


# ------------------------------------------------------- Codex P1 on 4b4c3a6: replay never reactivates an orphan target
def _orphan_replay(how='stop_fill', after=40):
    """The orphaned target as a snapshot carries it after restart: known to replay as CANCELLING, sent at T0."""
    from newcore.domain import IntentState
    pf, ids, target = _orphaned_target(how)
    acct = pf.account_id
    E = lambda cls, n, at, **kw: F.event(cls, ids, acct, after + n, at=at, **kw)               # noqa: E731
    return ids, target, {target.intent_id: (target, IntentState.CANCELLING, F.T0)}, E


@pytest.mark.parametrize('how', HOW)
@pytest.mark.parametrize('to', ('working', 'unknown'))
def test_p1_replay_refuses_to_reactivate_an_orphan_target(how, to):
    from newcore.domain import (IntentState, IntentStateChanged, InvalidRecord, canonical_bytes, check_event_chain,
                                loads)
    ids, target, known, E = _orphan_replay(how)
    ev = E(IntentStateChanged, 1, F.T0 + 70_000, intent_id=target.intent_id, from_state=IntentState.CANCELLING,
           to_state=IntentState(to))
    back = loads(canonical_bytes(ev))                                         # byte round trip, as read from the journal
    assert back == ev
    with pytest.raises(InvalidRecord, match='cancel-only work'):
        check_event_chain([back], after_sequence=40, known_intents=known)


@pytest.mark.parametrize('how', HOW)
def test_p1_a_late_final_still_ends_the_orphan_target_after_restart(how):
    from newcore.domain import (ExchangeStatus, IntentState, IntentStateChanged, ResultObserved, ResultPhase,
                                canonical_bytes, check_event_chain, loads)
    ids, target, known, E = _orphan_replay(how)
    final = _target_result(ids, target, ResultPhase.FINAL, '0.4', '112.5', ExchangeStatus.CANCELED)
    chain = [E(ResultObserved, 1, final.observed_at_ms, result=final),
             E(IntentStateChanged, 2, final.observed_at_ms + 1, intent_id=target.intent_id,
               from_state=IntentState.CANCELLING, to_state=IntentState.CANCELLED)]
    back = [loads(canonical_bytes(ev)) for ev in chain]
    assert back == chain
    assert check_event_chain(back, after_sequence=40, known_intents=known) == {}


def test_p1_a_lot_owned_cancelling_target_keeps_the_generic_transition_table():
    from newcore.domain import IntentState, IntentStateChanged, check_event_chain
    p, ids, acct, lt, tgt, chain, E = _target_chain(seed=392)
    cancel = E(IntentStateChanged, 5, F.T0 + 30_000, intent_id=tgt.intent_id, from_state=IntentState.WORKING,
               to_state=IntentState.CANCELLING)
    for to in (IntentState.WORKING, IntentState.UNKNOWN):                     # cancel rejected / timed out: unchanged
        back = E(IntentStateChanged, 6, F.T0 + 31_000, intent_id=tgt.intent_id, from_state=IntentState.CANCELLING,
                 to_state=to)
        live = check_event_chain(chain + [cancel, back])
        assert live[tgt.intent_id][1] is to
