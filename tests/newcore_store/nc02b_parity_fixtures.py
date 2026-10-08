"""Every-cut compaction PARITY fixtures (Codex #13, 6069341639): store-side journals for the NC-01 lane's typed grammar
seed - `JournalGate.rebuild(tail, grammar_seed=..., facts=..., after_sequence=k)` must behave exactly like the full-log
replay at EVERY cut k. Compaction stays disabled in the store until that holds; nothing here touches production code.

    for case in parity_cases():          # one per (scenario, k)
        case.scenario, case.k            # name, cut: the snapshot holds events[:k], the journal keeps events[k:]
        case.events                      # the whole journal (the full-log reference)
        case.tail                        # events[k:]
        case.seed                        # CutSeed: last_sequence, facts (FactIndex), known_intents (check_event_chain
                                         #   shape: intent_id -> (OrderIntent, IntentState, submitted_at_ms | None)),
                                         #   decisions / intents / lots recorded in the prefix (for a grammar seed)
        case.probes                      # (name, event, expected): what the FULL-log gate answers when the event is
                                         #   staged next - 'apply' | 'already_applied' | 'conflict'

Scenarios cover live and terminal intents, owner lots (PROTECT / CLOSE owned by the entry's lot), result phases
(KNOWN, FINAL, the lost-answer path), incidents with intent / lot references, and an external close booked by
reconcile from venue trades (consumed-trade facts). `run_parity(rebuild)` is the harness: give it a callable
(scenario events, case) -> gate and it checks every case against the full log.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from decimal import Decimal as D

from nc02a_events import (ACCOUNT_ID, AGGREGATE_ID, CLOSE_INT, ENTRY_INT, LOT_ID, QTY, SCENARIO, STOP_INT, SYMBOL,
                          T0, durable, ev, event_id, lost_answer_events, planned, state)
from newcore.domain import (Action, Authority, DecisionRecorded, Evidence, ExternalTrade, IncidentRecorded,
                            IntentRecorded, IntentState, IntentStateChanged, OrderResult, Purpose, ReasonCode,
                            ResultObserved, ResultPhase, Venue, check_event_chain)
from newcore.domain.facts import fold_facts
from newcore.domain.incident import Incident
from newcore.ports.journal import Admission, JournalConflict, JournalGate
from newcore.ports.keys import derive_child_intent_id


# ---------------------------------------------------------------------------------------------------- builders
def resequence(events):
    """The same events renumbered 1..n with matching event ids (insertion keeps every journal gap-free)."""
    return [dataclasses.replace(e, sequence=n, event_id=event_id(n)) for n, e in enumerate(events, 1)]


def incident(n, at, *, intents=(), lots=(), detail=None, kind=ReasonCode.RECONCILE_MANUAL_CLOSE):
    inc = Incident(incident_id='inc_' + f'{n:02x}' * 16, account_id=ACCOUNT_ID, kind=kind, at_ms=at, symbol=None,
                   side=None, intent_refs=tuple(intents), lot_refs=tuple(lots), position_refs=(), evidence=(),
                   detail=detail)
    return ev(IncidentRecorded, 1, at, reason=kind, incident=inc)          # sequence set by resequence()


def _external_close_events(at):
    """A RECONCILE decision books an external close of the entry's lot from venue trades (consumed-trade facts)."""
    from nc02a_events import decision
    dec_id = 'dec_' + 'e1' * 16
    it = planned(derive_child_intent_id(ACCOUNT_ID, LOT_ID, Purpose.CLOSE, 0), dec_id, Purpose.CLOSE, at, owner=LOT_ID,
                 reason=ReasonCode.RECONCILE_EXTERNAL_CLOSE)
    dec = decision(dec_id, Action.RECONCILE, ReasonCode.RECONCILE_EXTERNAL_CLOSE, Authority.RECONCILIATION, None, [it],
                   at, subject=LOT_ID)
    sv = dict(venue=Venue.BINANCE_USDM, symbol=SYMBOL)
    trades = (ExternalTrade(trade_id='9101', at_ms=at - 5_000, qty=D('3'), price=D('101'), **sv),
              ExternalTrade(trade_id='9102', at_ms=at - 4_000, qty=QTY - 3, price=D('102'), **sv))
    res = OrderResult(result_id='res_' + 'e3' * 16, intent_id=it.intent_id, account_id=ACCOUNT_ID,
                      client_order_id=it.client_order_id, phase=ResultPhase.FINAL, requested_qty=it.qty,
                      observed_at_ms=at + 5, exchange_order_id=None, exchange_status=None, lookup=None,
                      executed_qty=it.qty, avg_price=(D('3') * 101 + (QTY - 3) * 102) / QTY,
                      evidence=Evidence.EXCHANGE_EXTERNAL, corroboration=(), resolved_by=dec_id,
                      external_trades=trades, supersedes_result_id=None)
    d = durable(it)
    return [ev(DecisionRecorded, 1, at, reason=dec.reason, decision=dec),           # sequences: resequence()
            ev(IntentRecorded, 1, at, reason=d.reason, intent=d),
            ev(ResultObserved, 1, at + 5, reason=ReasonCode.RECONCILE_EXTERNAL_CLOSE, result=res),
            state(1, at + 5, d, IntentState.DURABLE, IntentState.FILLED)]


def scenarios():
    """name -> a valid journal (every one is checked against the full-log gate + NC-01 chain by the self-test)."""
    s = list(SCENARIO)
    out = {
        'lifecycle': s,                                                   # entry / stop / close, all terminal
        'live_at_end': s[:13],                                            # stop WORKING, close SUBMITTED: live
        'lost_answer': _lost_answer(),                                    # a sent intent whose answer is lost
        'incidents': resequence(s[:5] + [incident(1, s[4].at_ms + 1, intents=(ENTRY_INT,), lots=(LOT_ID,))]
                                + s[5:] + [incident(2, s[-1].at_ms + 1, detail='after the close')]),
        'external_close': resequence(s[:10] + _external_close_events(s[9].at_ms + 50)
                                     + [incident(3, s[9].at_ms + 60, intents=(STOP_INT,), lots=(LOT_ID,))]),
    }
    return out


# ---------------------------------------------------------------------------------------------------- cuts
@dataclass(frozen=True)
class CutSeed:
    last_sequence: int
    facts: object                 # FactIndex through last_sequence (what Snapshot.facts carries)
    known_intents: dict           # check_event_chain(prefix): the live intents at the cut
    decisions: tuple              # decision ids recorded in the prefix
    intents: tuple                # intent ids recorded in the prefix (live or terminal)
    lots: tuple                   # lot ids opened in the prefix (owners of PROTECT / CLOSE intents)


@dataclass(frozen=True)
class ParityCase:
    scenario: str
    k: int
    events: tuple
    tail: tuple
    seed: CutSeed
    probes: tuple                 # (name, event, expected)


def _seed(prefix):
    decisions = tuple(e.decision.decision_id for e in prefix if isinstance(e, DecisionRecorded))
    intents = tuple(e.intent.intent_id for e in prefix if isinstance(e, IntentRecorded))
    lots = tuple(sorted({e.intent.owner_id for e in prefix if isinstance(e, IntentRecorded) and e.intent.owner_id}
                        | ({LOT_ID} if any(isinstance(e, ResultObserved) and e.result.intent_id == ENTRY_INT
                                           and e.result.phase is ResultPhase.FINAL and e.result.executed_qty
                                           for e in prefix) else set())))
    return CutSeed(len(prefix), fold_facts(prefix), check_event_chain(prefix), decisions, intents, lots)


def full_gate(events):
    return JournalGate.rebuild(ACCOUNT_ID, AGGREGATE_ID, list(events))


def answer(gate, event):
    """'apply' | 'already_applied' | 'conflict' - what this gate says when `event` is staged next (nothing commits)."""
    try:
        st = gate.stage(event)
    except JournalConflict:
        return 'conflict'
    return 'already_applied' if st is None or st is Admission.ALREADY_APPLIED else 'apply'


def _probes(events, k):
    n = len(events)
    at = events[-1].at_ms + 1
    probes = []
    if k:                                                                 # an event of the compacted prefix again
        probes.append(('prefix_event_again', events[k - 1]))
    probes.append(('last_event_again', events[-1]))
    facts = [e for e in events[:k] if isinstance(e, (ResultObserved, IncidentRecorded))]
    if facts:                                                             # a prefix FACT id under other content
        f = facts[0]
        if isinstance(f, ResultObserved):
            other = dataclasses.replace(f.result, observed_at_ms=f.result.observed_at_ms + 7)
            probes.append(('prefix_result_id_other_content',
                           dataclasses.replace(f, sequence=n + 1, event_id=event_id(n + 1), at_ms=at, result=other)))
        else:
            other = dataclasses.replace(f.incident, detail='other content')
            probes.append(('prefix_incident_id_other_content',
                           dataclasses.replace(f, sequence=n + 1, event_id=event_id(n + 1), at_ms=at, incident=other)))
    inc = dataclasses.replace(incident(0x7f, at, intents=(ENTRY_INT,) if any(
        isinstance(e, IntentRecorded) and e.intent.intent_id == ENTRY_INT for e in events) else ()),
        sequence=n + 1, event_id=event_id(n + 1))
    probes.append(('new_incident_next', inc))                            # a fresh, valid next event
    gate = full_gate(events)
    return tuple((name, e, answer(gate, e)) for name, e in probes)


def parity_cases():
    out = []
    for name, events in scenarios().items():
        events = tuple(events)
        for k in range(len(events) + 1):
            out.append(ParityCase(name, k, events, events[k:], _seed(list(events[:k])), _probes(list(events), k)))
    return out


def run_parity(rebuild, cases=None):
    """rebuild(case) -> a JournalGate restarted from case.seed + case.tail. Returns the mismatches against the full log
    as (scenario, k, probe, full_answer, seeded_answer) - empty when the seed gives full-log parity at every cut."""
    bad = []
    for case in cases or parity_cases():
        try:
            gate = rebuild(case)
        except JournalConflict as ex:
            bad.append((case.scenario, case.k, 'rebuild', 'ok', f'refused: {type(ex).__name__}'))
            continue
        if gate.grammar.last_sequence != len(case.events):
            bad.append((case.scenario, case.k, 'last_sequence', len(case.events), gate.grammar.last_sequence))
        for pname, event, want in case.probes:
            got = answer(gate, event)
            if got != want:
                bad.append((case.scenario, case.k, pname, want, got))
    return bad


def _lost_answer():
    head, not_found, final, closed = lost_answer_events()
    return list(head) + [not_found, final, closed]
