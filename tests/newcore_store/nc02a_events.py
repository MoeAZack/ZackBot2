"""Valid NC-01 event chains for the NC-02a store tests (deterministic: every id is a hash, no clock, no randomness).

SCENARIO is the representative sequence the crash drills run: decision -> entry intent -> sent -> result -> close of
the entry -> stop decision -> stop intent -> sent -> result (known) -> working -> close decision -> close intent -> sent
-> result -> closed -> stop cancelling -> stop result -> stop closed. TAGS gives, per event, (intent, effect) so the
drills can compute the expected recovery classification with an oracle that does not use the store's fold.
"""
import dataclasses
import hashlib
from decimal import Decimal as D

from newcore.domain import (Action, Authority, Decision, DecisionKey, DecisionRecorded, EntriesMode, Evidence,
                            ExchangeStatus, HoldKind, IntentRecorded, IntentState, IntentStateChanged, Lookup,
                            ModeChanged, OrderIntent, OrderResult, OrderType, Purpose, ReasonCode, ResultObserved,
                            ResultPhase, Side)
from newcore.ports.keys import client_id_for, derive_child_intent_id, derive_decision_id, derive_intent_id

ACCOUNT_ID = 'acct_' + 'a1' * 16
AGGREGATE_ID = 'pf_' + 'b2' * 16
T0 = 1_791_400_000_000
SYMBOL = 'SOLUSDT'


def _h(tag, *parts):
    h = hashlib.sha256(('nc02a.test.' + tag).encode())
    for p in parts:
        h.update(b'\0' + str(p).encode())
    return h.hexdigest()[:32]


def event_id(seq, aggregate=AGGREGATE_ID):
    return 'evt_' + _h('event', aggregate, seq)


def result_id(intent_id, n):
    return 'res_' + _h('result', intent_id, n)


LOT_ID = 'lot_' + _h('lot', 'entry')
ENTRY_KEY = DecisionKey(strategy='trend_ema_mom@4h', strategy_version='v1', symbol=SYMBOL, side=Side.LONG,
                        candle_close_ms=T0, purpose=Purpose.ENTRY)
CLOSE_KEY = dataclasses.replace(ENTRY_KEY, candle_close_ms=T0 + 4 * 14_400_000, purpose=Purpose.CLOSE)
ENTRY_DEC = derive_decision_id(ACCOUNT_ID, ENTRY_KEY)
ENTRY_INT = derive_intent_id(ACCOUNT_ID, ENTRY_KEY, 0)
STOP_INT = derive_child_intent_id(ACCOUNT_ID, ENTRY_INT, 'protect', 0)
STOP_DEC = 'dec_' + _h('child_decision', STOP_INT)
CLOSE_DEC = derive_decision_id(ACCOUNT_ID, CLOSE_KEY)
CLOSE_INT = derive_intent_id(ACCOUNT_ID, CLOSE_KEY, 0)
QTY = D('5')


def planned(intent_id, decision_id, purpose, at, *, owner=None, stop=None, reason, slot=None):
    protect = purpose is Purpose.PROTECT
    return OrderIntent(intent_id=intent_id, account_id=ACCOUNT_ID, decision_id=decision_id,
                       client_order_id=client_id_for(intent_id, 'classic'), purpose=purpose,
                       order_type=OrderType.STOP_MARKET if protect else OrderType.MARKET, state=IntentState.PLANNED,
                       symbol=SYMBOL, side=Side.LONG, qty=QTY, reason=reason, created_at_ms=at, owner_id=owner,
                       slot_id=slot, price=None, stop_price=stop, arm=None, alt_client_order_id=None, seen_qty=None,
                       authorized_by=None)


def result(intent, n, at, **kw):
    base = dict(result_id=result_id(intent.intent_id, n), intent_id=intent.intent_id, account_id=ACCOUNT_ID,
                client_order_id=intent.client_order_id, requested_qty=intent.qty, observed_at_ms=at,
                exchange_order_id=None, exchange_status=None, lookup=None, executed_qty=None, avg_price=None,
                evidence=None, corroboration=(), resolved_by=None)
    base.update(kw)
    return OrderResult(**base)


def final_filled(intent, n, at, price='100'):
    return result(intent, n, at, phase=ResultPhase.FINAL, evidence=Evidence.EXCHANGE_FINAL,
                  exchange_order_id=str(int(_h('eoid', intent.intent_id, n)[:12], 16)), exchange_status=ExchangeStatus.FILLED,
                  executed_qty=intent.qty, avg_price=D(price))


def ev(cls, seq, at, **kw):
    return cls(event_id=event_id(seq), account_id=ACCOUNT_ID, aggregate_id=AGGREGATE_ID, sequence=seq, at_ms=at, **kw)


def decision(dec_id, action, reason, authority, key, intents, at, subject=None):
    return Decision(decision_id=dec_id, account_id=ACCOUNT_ID, at_ms=at, action=action, reason=reason,
                    authority=authority, key=key, evidence=(), symbol=SYMBOL, side=Side.LONG, subject_id=subject,
                    detail='', intents=tuple(intents), policy_version='nc02a')


def durable(it):
    return dataclasses.replace(it, state=IntentState.DURABLE)


def state(seq, at, intent, a, b):
    return ev(IntentStateChanged, seq, at, reason=intent.reason, intent_id=intent.intent_id, from_state=a, to_state=b)


def _scenario():
    S = IntentState
    t = T0
    entry = planned(ENTRY_INT, ENTRY_DEC, Purpose.ENTRY, t, reason=ReasonCode.ENTRY_SIGNAL, slot='S1')
    stop = planned(STOP_INT, STOP_DEC, Purpose.PROTECT, t + 10, owner=LOT_ID, stop=D('95'), reason=ReasonCode.PROTECT_PLACE)
    close = planned(CLOSE_INT, CLOSE_DEC, Purpose.CLOSE, t + 100, owner=LOT_ID, reason=ReasonCode.EXIT_TIME)
    e, s, c = durable(entry), durable(stop), durable(close)
    evs = [
        ev(DecisionRecorded, 1, t, reason=entry.reason,
           decision=decision(ENTRY_DEC, Action.ENTER, ReasonCode.ENTRY_SIGNAL, Authority.STRATEGY, ENTRY_KEY, [entry], t)),
        ev(IntentRecorded, 2, t, reason=e.reason, intent=e),
        state(3, t + 1, e, S.DURABLE, S.SUBMITTED),
        ev(ResultObserved, 4, t + 2, reason=e.reason, result=final_filled(e, 0, t + 2)),
        state(5, t + 2, e, S.SUBMITTED, S.FILLED),
        ev(DecisionRecorded, 6, t + 10, reason=stop.reason,
           decision=decision(STOP_DEC, Action.PROTECT, ReasonCode.PROTECT_PLACE, Authority.PROTECTION, None, [stop],
                             t + 10, subject=LOT_ID)),
        ev(IntentRecorded, 7, t + 10, reason=s.reason, intent=s),
        state(8, t + 11, s, S.DURABLE, S.SUBMITTED),
        ev(ResultObserved, 9, t + 12, reason=s.reason,
           result=result(s, 0, t + 12, phase=ResultPhase.KNOWN, exchange_order_id='9001',
                         exchange_status=ExchangeStatus.NEW)),
        state(10, t + 12, s, S.SUBMITTED, S.WORKING),
        ev(DecisionRecorded, 11, t + 100, reason=close.reason,
           decision=decision(CLOSE_DEC, Action.CLOSE, ReasonCode.EXIT_TIME, Authority.STRATEGY, CLOSE_KEY, [close],
                             t + 100, subject=LOT_ID)),
        ev(IntentRecorded, 12, t + 100, reason=c.reason, intent=c),
        state(13, t + 101, c, S.DURABLE, S.SUBMITTED),
        ev(ResultObserved, 14, t + 102, reason=c.reason, result=final_filled(c, 0, t + 102, '101')),
        state(15, t + 102, c, S.SUBMITTED, S.FILLED),
        state(16, t + 103, s, S.WORKING, S.CANCELLING),
        ev(ResultObserved, 17, t + 104, reason=s.reason,
           result=result(s, 1, t + 104, phase=ResultPhase.FINAL, evidence=Evidence.EXCHANGE_FINAL,
                         exchange_order_id='9001', exchange_status=ExchangeStatus.CANCELED, executed_qty=D(0))),
        state(18, t + 104, s, S.CANCELLING, S.CANCELLED),
    ]
    tags = [(None, 'decision'), ('entry', 'recorded'), ('entry', 'sent'), ('entry', 'final'), ('entry', 'closed'),
            (None, 'decision'), ('stop', 'recorded'), ('stop', 'sent'), ('stop', 'known'), ('stop', 'state'),
            (None, 'decision'), ('close', 'recorded'), ('close', 'sent'), ('close', 'final'), ('close', 'closed'),
            ('stop', 'state'), ('stop', 'final'), ('stop', 'closed')]
    return evs, tags, {'entry': e, 'stop': s, 'close': c}


SCENARIO, TAGS, INTENTS = _scenario()
INTENT_IDS = {k: v.intent_id for k, v in INTENTS.items()}


def expected_recovery(k):
    """Oracle: intent name -> 'durable_not_sent' | 'unknown_needs_query' | 'final_not_closed' | 'closed' for the
    first k scenario events, and the expected booked qty, computed from TAGS only."""
    seen = {}
    for name, eff in TAGS[:k]:
        if name is not None:
            seen.setdefault(name, set()).add(eff)
    out = {}
    for name, effs in seen.items():
        if 'closed' in effs:
            out[name] = 'closed'
        elif 'final' in effs:
            out[name] = 'final_not_closed'
        elif 'sent' in effs:
            out[name] = 'unknown_needs_query'
        else:
            out[name] = 'durable_not_sent'
    booked = D(0)
    for (name, eff) in TAGS[:k]:
        if eff == 'final' and name == 'entry':
            booked += QTY
        if eff == 'final' and name == 'close':
            booked -= QTY
    return out, booked


def lost_answer_events():
    """Entry decided, recorded, sent; the venue's answer is lost (crash). Then the restart's venue query: first a bare
    not-found (proves nothing), then the final record."""
    e = INTENTS['entry']
    t = T0
    head = SCENARIO[:3]
    nf = ev(ResultObserved, 4, t + 30_000, reason=e.reason,
            result=result(e, 0, t + 30_000, phase=ResultPhase.UNKNOWN, lookup=Lookup.NOT_FOUND))
    fin = ev(ResultObserved, 5, t + 60_000, reason=e.reason, result=final_filled(e, 1, t + 60_000))
    closed = state(6, t + 60_000, e, IntentState.SUBMITTED, IntentState.FILLED)
    return head, nf, fin, closed


def hold_event(seq, at, from_mode=EntriesMode.ACTIVE, from_hold=None):
    return ev(ModeChanged, seq, at, reason=ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE, from_mode=from_mode,
              to_mode=EntriesMode.HOLD, reasons=(ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE,), from_hold=from_hold,
              to_hold=HoldKind.DURABILITY_UNAVAILABLE, decision_id=None, reconciliation_id=None)
