"""NC-01 domain model and reason codes (docs/newcore/NC01_CONTRACT.md).

Pure stdlib: no file, network, clock, environment, logging or legacy access (enforced by
tests/newcore/test_nc01_import_boundary.py). One short module per record family:

    errors      DomainError / InvalidRecord / ForeignDocument / UnsupportedVersion (Future/Unknown/Older) / OwnershipUnknown
    base        Record base class, the strict Decimal / ms-timestamp / id dialect
    reasons     ReasonCode registry (append-only, semantic entries), golden exit / signal projection
    instrument  InstrumentId, InstrumentRules + explicit-rounding quantize helpers
    account     Account (stable AccountId), AccountBinding, typed binding confirmation / rotation states
    orders      OrderIntent (the one order-ownership family, lifecycle), OrderResult (phase + evidence)
    modes       EntriesMode, HoldKind, the pure permitted-action table
    protection  Protection (own facts only) and its derived lifecycle status
    portfolio   Portfolio (explicit trust), Position, Lot, Fill, ownership proof
    decision    Decision (reason, authority, evidence refs), Action priority
    events      DomainEvent records (event_id, aggregate, sequence) + pure chain check
    ledger      sequence admission + idempotent re-apply
    snapshot    Snapshot / HighWater + generation rollback verdict
    codec       strict canonical versioned JSON, canonical_bytes / contract_sha256
"""
from .account import (Account, AccountBinding, BindingConfirmation, BindingState, Environment, Venue,
                      confirmation_phrase, observe_binding, require_environment)
from .base import make_id
from .codec import (DecodeResult, Outcome, canonical_bytes, contract_sha256, decode_document, decode_result, dumps,
                    encode_document, loads)
from .decision import Action, Authority, Decision, DecisionKey
from .errors import (DomainError, EventOrderError, ForeignDocument, FutureSchema, InvalidRecord, OlderSchema,
                     OwnershipUnknown, UnknownSchema, UnsupportedVersion)
from .events import (BindingChanged, DecisionRecorded, DomainEvent, IncidentRecorded, IntentRecorded,
                     IntentStateChanged, ManagementInputRecorded, ModeChanged, ResultObserved, check_event_chain)
from .incident import Incident
from .manage_input import CandleInput, FillObservation, ManagementInput
from .instrument import Capability, InstrumentId, InstrumentRules, Rounding
from .ledger import Admission, EventCursor, EventDigest, admit
from .modes import EntriesMode, HoldKind, Op, Permission, permitted
from .orders import (Arming, Evidence, ExchangeStatus, ExternalTrade, IntentState, Lookup, OrderIntent, OrderResult,
                     OrderType,
                     OwnerFamily, OwnerKind, PositionRead, Purpose, ResultPhase, ResultStage, Side, check_result_for_intent,
                     is_post_hoc, may_apply, may_send, supersedes, terminal_for,
                     LIMIT_TYPES, TARGET_REASONS)
from .portfolio import (Fill, Lot, LotSource, Ownership, OwnershipProof, Portfolio, Position, ProofKind,
                        check_account_portfolio, check_flat_snapshot_fresh, entry_blocking_protections,
                        owned_client_ids, ownership_families)
from .protection import (MissPhase, Protection, ProtectionStatus, StopMiss, confirmed_coverage, pending_coverage,
                         promote_replacement, protection_status, target_coverage)
from .reasons import GOLDEN_EXIT, GOLDEN_EXIT_CODES, GOLDEN_SIGNAL, MEANING, ReasonCode
from .snapshot import GenerationVerdict, HighWater, Snapshot, check_generation
