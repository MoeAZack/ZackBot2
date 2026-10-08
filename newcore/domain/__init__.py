"""NC-01 domain model and reason codes.

Pure stdlib (no IO, clock, randomness, network or legacy import; enforced by tests/newcore/test_nc01_import_boundary.py).
One short module per record family:

    errors      DomainError / InvalidRecord / ForeignDocument / FutureSchema / UnknownSchema / OwnershipUnknown
    base        Record base class, Decimal / ms-timestamp / id rules, deterministic client ids
    reasons     ReasonCode registry (append-only), golden exit projection
    instrument  InstrumentRules + explicit-rounding quantize helpers
    account     Account, AccountBinding, typed binding confirmation / rotation states
    orders      OrderIntent (one record for every purpose, with lifecycle), OrderResult (phase + evidence)
    modes       EntriesMode, HoldKind, the pure permitted-action table
    protection  Protection (own facts only) and its derived lifecycle status
    portfolio   Portfolio, Position, Lot, Fill, OrphanCancel, ownership proof
    decision    Decision, Action priority, reason-stage rules
    events      append-only event records + pure chain check
    snapshot    Snapshot / HighWater + generation rollback verdict
    codec       strict canonical JSON
"""
from .account import (Account, AccountBinding, BindingConfirmation, BindingState, Environment, confirmation_phrase,
                      observe_binding)
from .base import deterministic_cid, make_cid, make_id
from .codec import decode_document, dumps, encode_document, loads
from .decision import Action, Decision
from .errors import DomainError, ForeignDocument, FutureSchema, InvalidRecord, OwnershipUnknown, UnknownSchema
from .events import (BindingChanged, DecisionRecorded, IntentRecorded, IntentStateChanged, ModeChanged, ResultObserved,
                     check_event_chain)
from .instrument import InstrumentRules, Rounding
from .modes import EntriesMode, HoldKind, Op, Permission, permitted
from .orders import (Arming, Evidence, ExchangeStatus, IntentState, Lookup, OrderIntent, OrderResult, OrderType,
                     OwnerFamily, PositionRead, Purpose, ResultPhase, Side, check_result_for_intent, protect_cid)
from .portfolio import (Fill, Lot, LotSource, OrphanCancel, Ownership, OwnershipProof, Portfolio, Position, ProofKind,
                        check_account_portfolio, owned_client_ids, ownership_families)
from .protection import MissPhase, Protection, ProtectionStatus, StopMiss, protection_status
from .reasons import GOLDEN_EXIT, GOLDEN_EXIT_CODES, ReasonCode
from .snapshot import GenerationVerdict, HighWater, Snapshot, check_generation
