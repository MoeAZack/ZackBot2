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
    start = order.index('risk_gateway.cost_to_stop') + 1                  # later r3 items append after these
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


def test_item3a_only_reconcile_manual_close_books():
    from newcore.domain import Authority, InvalidRecord
    p, ids, dec, it, res = _external_close()
    with pytest.raises(InvalidRecord, match='only from a RECONCILE reconcile.manual_close'):
        F.replace(dec, action=Action.CLOSE, reason=ReasonCode.EXIT_MANUAL, authority=Authority.STRATEGY)
    with pytest.raises(InvalidRecord, match='books post-hoc intents only'):
        F.replace(dec, intents=(F.replace(it, reason=ReasonCode.EXIT_TIME),))


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
    late = F.replace(rs['filled'], result_id=ids.id('res'), observed_at_ms=F.T0 + 60_000)
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


def test_item3b_before_the_close_the_terminal_step_follows_the_fill():
    from newcore.domain import IntentState, IntentStateChanged, InvalidRecord, ResultObserved, check_event_chain
    p, ids, acct, it, rs, late, head, E = _late_fill()
    sup = E(ResultObserved, 6, late.observed_at_ms, result=late)
    filled = E(IntentStateChanged, 7, late.observed_at_ms, intent_id=it.intent_id, from_state=IntentState.UNKNOWN,
               to_state=IntentState.FILLED)
    assert check_event_chain(head + [sup, filled]) == {}
    cancelled = F.replace(filled, to_state=IntentState.CANCELLED)
    with pytest.raises(InvalidRecord, match='the final result means'):
        check_event_chain(head + [sup, cancelled])


def test_item3b_only_an_executed_exchange_record_of_the_same_order_supersedes():
    from decimal import Decimal as D
    from newcore.domain import Evidence, ExchangeStatus, ResultPhase, supersedes
    p, ids, acct, it, rs, late, head, E = _late_fill()
    cor = rs['corroborated']
    assert supersedes(cor, late)
    assert supersedes(cor, F.replace(rs['partial_cancel'], observed_at_ms=F.T0 + 60_000))     # any execution
    not_superseding = {
        'nothing executed': F.replace(late, executed_qty=D('0'), avg_price=None,
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


# ----------------------------------------------------------------------------------------------------------- item 5
def _tick(ids, acct, lot_id, **kw):
    from newcore.domain import Authority
    base = dict(decision_id=ids.id('dec'), account_id=acct, at_ms=F.T0 + 4 * 3_600_000, action=Action.WAIT,
                reason=ReasonCode.MANAGE_TICK, authority=Authority.STRATEGY, symbol='SOLUSDT', side=F.Side.LONG,
                subject_id=lot_id, detail='mg tick 1791414400000 -')
    return F.build(F.Decision, **{**base, **kw})


def test_item5_manage_tick_is_appended_and_names_its_lot():
    from newcore.domain import MEANING, canonical_bytes, loads
    from newcore.domain.reasons import GATE_NAMESPACES, NAMESPACES
    order = [r.value for r in ReasonCode]
    assert order.index('manage.tick') == order.index('reconcile.late_fill_after_not_found') + 1
    assert ReasonCode('manage.tick').namespace == 'manage' and 'manage' in NAMESPACES
    assert 'manage' not in GATE_NAMESPACES and len(MEANING[ReasonCode.MANAGE_TICK]) > 10
    ids = F.Ids(360)
    acct = ids.id('acct')
    d = _tick(ids, acct, ids.id('lot'))
    assert loads(canonical_bytes(d)) == d


def test_item5_manage_tick_refusals():
    from newcore.domain import Authority, InvalidRecord
    ids = F.Ids(361)
    acct, lot_id = ids.id('acct'), ids.id('lot')
    cases = {
        'no subject lot': ('a STRATEGY WAIT about one lot', dict(subject_id=None)),
        'an intent subject': ('subject_id', dict(subject_id=ids.id('int'))),
        'a SKIP': ('not a reason for', dict(action=Action.SKIP)),       # manage.* is no gate namespace
        'a RECONCILE': ('not a reason for', dict(action=Action.RECONCILE, authority=Authority.RECONCILIATION)),
        'another authority': ('a STRATEGY WAIT about one lot', dict(authority=Authority.PROTECTION)),
    }
    for name, (message, kw) in cases.items():
        with pytest.raises(InvalidRecord, match=message):
            _tick(ids, acct, lot_id, **kw)
            raise AssertionError(name)
