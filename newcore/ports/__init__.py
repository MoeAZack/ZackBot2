"""NEWCORE step-0 shared interface (docs/newcore/STEP0_INTERFACE.md): Protocol ports and tiny value types only.

No adapters, no IO, no clock, no randomness, no legacy imports and (until NC-01 merges) no newcore.domain import.

    values   shared validators mirroring the NC-01 dialect
    keys     DecisionKey + canonical bytes, restart-stable decision / intent / client ids
"""
from .keys import (DecisionKey, MAX_LEGS, client_id_for, derive_child_intent_id, derive_decision_id, derive_intent_id,
                   is_newcore_client_id)
from .values import PortValueError
