"""NEWCORE step-0 shared interface (docs/newcore/STEP0_INTERFACE.md): Protocol ports and tiny value types only.

No adapters, no IO, no clock, no randomness, no legacy imports and (until NC-01 merges) no newcore.domain import.

    values   shared validators mirroring the NC-01 dialect
    keys     DecisionKey + canonical bytes, restart-stable decision / intent / client ids
    journal  JournalPort, EventHeader grammar (G1-G9), consumed-signal claim
    venue    VenuePort, order requests / typed outcomes, position / open-order / fill values
    bars     BarSource, Bar, closed-candle check
"""
from .bars import Bar, BarSource, check_closed_bars
from .journal import (Admission, EventHeader, EventKind, Grammar, GrammarError, JournalConflict, JournalPort,
                      JournalUnavailable, ResultOutcome, SignalClaim, claim_signal, replay)
from .keys import (DecisionKey, MAX_LEGS, client_id_for, derive_child_intent_id, derive_decision_id, derive_intent_id,
                   is_newcore_client_id)
from .values import PortValueError
from .venue import (MarketOrder, OrderOutcome, OrderRef, OutcomeKind, ReadKind, ReadOutcome, StopOrder, VenueFill,
                    VenueOrder, VenuePort, VenuePosition)
