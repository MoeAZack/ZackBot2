"""header_of: the NC-01 DomainEvent -> step-0 EventHeader projection the JournalPort grammar runs over.

STEP0_INTERFACE.md section 7 leaves `header_of` "for when NC-01 merges". Slice S1 wrote it in
newcore/adapters/memory_journal.py (branch nc-s1-slice, 29862ca), which is not on this branch. This is the same
projection, kept byte-for-byte equivalent in behaviour; INTERFACE ITEM: move ONE shared copy into newcore.ports (bound
to newcore.domain) so MemoryJournal and FileJournal cannot drift.
"""
from __future__ import annotations

from newcore.domain import (BindingChanged, DecisionRecorded, IntentRecorded, IntentStateChanged, ModeChanged,
                            ResultObserved, contract_sha256)
from newcore.domain.orders import TERMINAL, IntentState, Lookup, ResultPhase
from newcore.ports.journal import EventHeader, EventKind, GrammarError, ResultOutcome
from newcore.ports.keys import DecisionKey as PortKey

_LIVE_TO = {IntentState.WORKING: 'working', IntentState.UNKNOWN: 'unknown', IntentState.CANCELLING: 'cancelling'}


def port_key(key):
    """NC-01 DecisionKey -> the step-0 DecisionKey (byte-identical canonical form)."""
    return PortKey(strategy=key.strategy, strategy_version=key.strategy_version, symbol=key.symbol, side=str(key.side),
                   candle_close_ms=key.candle_close_ms, purpose=str(key.purpose))


def header_of(ev, digest=None):
    """What the step-0 grammar sees of one NC-01 event. digest = contract_sha256(event) (pass it when known)."""
    base = dict(event_id=ev.event_id, account_id=ev.account_id, aggregate_id=ev.aggregate_id, sequence=ev.sequence,
                at_ms=ev.at_ms, digest=digest or contract_sha256(ev))
    if isinstance(ev, DecisionRecorded):
        d = ev.decision
        return EventHeader(kind=EventKind.DECISION_RECORDED, decision_id=d.decision_id,
                           decision_key=None if d.key is None else port_key(d.key), **base)
    if isinstance(ev, IntentRecorded):
        it = ev.intent
        return EventHeader(kind=EventKind.INTENT_RECORDED, decision_id=it.decision_id, intent_id=it.intent_id,
                           purpose=str(it.purpose), client_ids=it.client_ids, **base)
    if isinstance(ev, IntentStateChanged):
        to = ev.to_state
        if to is IntentState.SUBMITTED:
            return EventHeader(kind=EventKind.SENT, intent_id=ev.intent_id, **base)
        if to in TERMINAL:
            return EventHeader(kind=EventKind.INTENT_CLOSED, intent_id=ev.intent_id, to_state=str(to), **base)
        if to in _LIVE_TO:
            return EventHeader(kind=EventKind.STATE_CHANGED, intent_id=ev.intent_id, to_state=_LIVE_TO[to], **base)
        raise GrammarError('event.to_state', f'{to} has no journal kind (PLANNED / DURABLE are never a state change)')
    if isinstance(ev, ResultObserved):
        r = ev.result
        if r.phase is ResultPhase.FINAL:
            out, evidence = ResultOutcome.FINAL, str(r.evidence)
        elif r.phase is ResultPhase.KNOWN:
            out, evidence = ResultOutcome.KNOWN, None
        else:
            out = ResultOutcome.NOT_FOUND if r.lookup is Lookup.NOT_FOUND else ResultOutcome.UNKNOWN
            evidence = None
        return EventHeader(kind=EventKind.RESULT_RECORDED, intent_id=r.intent_id, outcome=out, evidence=evidence, **base)
    if isinstance(ev, ModeChanged):
        return EventHeader(kind=EventKind.MODE_CHANGED, **base)
    if isinstance(ev, BindingChanged):
        return EventHeader(kind=EventKind.BINDING_CHANGED, **base)
    raise GrammarError('event', f'{type(ev).__name__} is not a journal event')
