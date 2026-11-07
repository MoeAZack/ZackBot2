"""TNET-01 pure injected orchestration and redacted report kernel.

The module owns scenario ordering, frozen safety bounds, assertions, cleanup
deadlines, idempotency checks, and canonical reporting.  All effects sit behind
the injected ``HarnessAdapter`` and report writer interfaces.  This file has no
network, credential, filesystem, process, git, sleep, thread, or runtime code.
"""
from __future__ import annotations

import enum
import hashlib
import json
import re
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from typing import Any, Callable, Protocol

from newcore.domain.account import Account, AccountBinding, BindingConfirmation, BindingState, Environment, Venue
from newcore.domain.base import canonical_decimal, dec_str
from newcore.domain.orders import Side


MAX_SYMBOLS = 2
MAX_LEVERAGE = 3
MAX_SCENARIO_NOTIONAL = Decimal("1000")
MAX_AGGREGATE_NOTIONAL = Decimal("2000")
MAX_ORDERS_PER_SCENARIO = 12
MAX_SCENARIO_MS = 10 * 60 * 1000
MAX_SUITE_MS = 120 * 60 * 1000
# A market-trigger scenario that ends with no trigger, clean and flat, may be attempted again later in the same suite
# with a fresh case id, a fresh entry and fresh order ids.  Every attempt is reported, and a pass still needs the real
# trigger and fill (owner decision, 6 Nov 2026, after four real runs missed only on price direction).
MAX_ATTEMPTS = 3
CLEANUP_PROTECT_MS = 15 * 1000
CLEANUP_FLATTEN_MS = 60 * 1000
CLEANUP_HOLD_MS = 120 * 1000
REPORT_SCHEMA = "zackbot-tnet01/2"

_HEX_16 = re.compile(r"[0-9a-f]{16}")
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_DOMAIN_ID = re.compile(r"(?:acct|int|dec)_[0-9a-f]{32}")
_LOCAL_ID = re.compile(r"(?:boot|case|suite|cleanup)_[0-9a-f]{32}")
_SYMBOL = re.compile(r"[A-Z0-9]{2,30}")
ZERO = Decimal("0")


class HarnessError(ValueError):
    """Stable interface error that never contains adapter or writer text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise HarnessError(code)


def _exact(value: Any, typ: type, code: str) -> Any:
    if type(value) is not typ:
        _fail(code)
    return value


def _integer(value: Any, code: str, *, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(code)
    return value


def _decimal(value: Any, code: str, *, positive: bool = False) -> Decimal:
    try:
        out = canonical_decimal(value, code)
    except Exception:
        _fail(code)
    if out <= ZERO if positive else out < ZERO:
        _fail(code)
    return out


def _boolean(value: Any, code: str) -> bool:
    return _exact(value, bool, code)


def _symbol(value: Any, code: str) -> str:
    if type(value) is not str or _SYMBOL.fullmatch(value) is None:
        _fail(code)
    return value


def _domain_id(value: Any, prefix: str, code: str) -> str:
    if type(value) is not str or _DOMAIN_ID.fullmatch(value) is None or not value.startswith(prefix + "_"):
        _fail(code)
    return value


def _local_id(value: Any, prefix: str, code: str) -> str:
    if type(value) is not str or _LOCAL_ID.fullmatch(value) is None or not value.startswith(prefix + "_"):
        _fail(code)
    return value


class ScenarioKind(enum.StrEnum):
    LONG_TARGET = "long_target"
    LONG_STOP = "long_stop"
    SHORT_TARGET = "short_target"
    SHORT_STOP = "short_stop"
    PARTIAL_CLOSE = "partial_close"
    BOUNDED_DCA = "bounded_dca"
    CANCEL_REPLACE_RACE = "cancel_replace_race"
    LOST_ANSWER_RECONCILE = "lost_answer_reconcile"
    MINIMUM_REFUSAL = "minimum_refusal"
    RESTART_OPEN_POSITION = "restart_open_position"
    RATE_LIMIT_429 = "rate_limit_429"
    IP_BAN_418 = "ip_ban_418"


REQUIRED_SCENARIOS = tuple(ScenarioKind)
RETRYABLE_SCENARIOS = frozenset({
    ScenarioKind.LONG_TARGET, ScenarioKind.SHORT_TARGET, ScenarioKind.LONG_STOP, ScenarioKind.SHORT_STOP,
    ScenarioKind.PARTIAL_CLOSE, ScenarioKind.BOUNDED_DCA,
})


class FinalTruth(enum.StrEnum):
    FLAT = "flat"
    PROTECTED_RECONCILED = "protected_reconciled"
    HOLD = "hold"


class CaseStatus(enum.StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    HOLD = "hold"


class SuiteStatus(enum.StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    HOLD = "hold"


class FailureCode(enum.StrEnum):
    BINDING = "binding"
    SETUP = "setup"
    ADAPTER = "adapter"
    DEADLINE = "deadline"
    BOUNDS = "bounds"
    ASSERTION = "assertion"
    CLEANUP = "cleanup"
    CLEANUP_NOT_IDEMPOTENT = "cleanup_not_idempotent"
    CLEANUP_HOLD = "cleanup_hold"
    SUITE_DEADLINE = "suite_deadline"
    EVIDENCE_HOLD = "evidence_hold"


class RateLimitKind(enum.StrEnum):
    HTTP_429 = "http_429"
    HTTP_418 = "http_418"


@dataclass(frozen=True, slots=True)
class HarnessBinding:
    account: Account
    dedicated: bool
    symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        _exact(self.account, Account, "validation.binding.account")
        _boolean(self.dedicated, "validation.binding.dedicated")
        if not self.dedicated:
            _fail("validation.binding.dedicated")
        if self.account.binding_state is not BindingState.CONFIRMED:
            _fail("validation.binding.confirmed")
        if self.account.binding.environment is not Environment.TESTNET:
            _fail("validation.binding.environment")
        _exact(self.symbols, tuple, "validation.binding.symbols")
        if not 1 <= len(self.symbols) <= MAX_SYMBOLS or len(set(self.symbols)) != len(self.symbols):
            _fail("validation.binding.symbols")
        for symbol in self.symbols:
            _symbol(symbol, "validation.binding.symbol")


@dataclass(frozen=True, slots=True)
class ScenarioCase:
    case_id: str
    kind: ScenarioKind
    symbol: str
    side: Side
    leverage: int
    max_notional: Decimal
    max_orders: int

    def __post_init__(self) -> None:
        _local_id(self.case_id, "case", "validation.case.case_id")
        _exact(self.kind, ScenarioKind, "validation.case.kind")
        _symbol(self.symbol, "validation.case.symbol")
        _exact(self.side, Side, "validation.case.side")
        _integer(self.leverage, "validation.case.leverage", minimum=1, maximum=MAX_LEVERAGE)
        if _decimal(self.max_notional, "validation.case.max_notional", positive=True) > MAX_SCENARIO_NOTIONAL:
            _fail("validation.case.max_notional")
        _integer(self.max_orders, "validation.case.max_orders", minimum=1, maximum=MAX_ORDERS_PER_SCENARIO)
        expected_side = Side.SHORT if self.kind in (ScenarioKind.SHORT_TARGET, ScenarioKind.SHORT_STOP) else Side.LONG
        if self.side is not expected_side:
            _fail("validation.case.side")


@dataclass(frozen=True, slots=True)
class SuiteManifest:
    build_sha: str
    suite_started_ms: int
    initial_boot_id: str
    binding: HarnessBinding
    cases: tuple[ScenarioCase, ...]

    def __post_init__(self) -> None:
        if type(self.build_sha) is not str or _HEX_40.fullmatch(self.build_sha) is None:
            _fail("validation.manifest.build_sha")
        _integer(self.suite_started_ms, "validation.manifest.suite_started_ms", minimum=1)
        _local_id(self.initial_boot_id, "boot", "validation.manifest.initial_boot_id")
        _exact(self.binding, HarnessBinding, "validation.manifest.binding")
        _exact(self.cases, tuple, "validation.manifest.cases")
        if any(type(case) is not ScenarioCase for case in self.cases):
            _fail("validation.manifest.cases")
        if tuple(case.kind for case in self.cases) != REQUIRED_SCENARIOS:
            _fail("validation.manifest.matrix")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            _fail("validation.manifest.case_ids")
        if not {case.symbol for case in self.cases} <= set(self.binding.symbols):
            _fail("validation.manifest.symbols")


@dataclass(frozen=True, slots=True)
class ScenarioContext:
    suite_id: str
    case: ScenarioCase
    account_id: str
    binding_digest: str
    boot_id: str
    restart_boot_id: str
    synthetic_intent_id: str
    authorization_id: str
    case_started_budget_ms: int
    case_deadline_ms: int
    suite_deadline_ms: int
    drop_answer_after_accepted: bool

    def __post_init__(self) -> None:
        _local_id(self.suite_id, "suite", "validation.context.suite_id")
        _exact(self.case, ScenarioCase, "validation.context.case")
        _domain_id(self.account_id, "acct", "validation.context.account_id")
        if type(self.binding_digest) is not str or _HEX_16.fullmatch(self.binding_digest) is None:
            _fail("validation.context.binding_digest")
        _local_id(self.boot_id, "boot", "validation.context.boot_id")
        _local_id(self.restart_boot_id, "boot", "validation.context.restart_boot_id")
        if self.boot_id == self.restart_boot_id:
            _fail("validation.context.restart_boot_id")
        _domain_id(self.synthetic_intent_id, "int", "validation.context.synthetic_intent_id")
        _domain_id(self.authorization_id, "dec", "validation.context.authorization_id")
        _integer(self.case_started_budget_ms, "validation.context.case_started_budget_ms", minimum=1)
        _integer(self.case_deadline_ms, "validation.context.case_deadline_ms", minimum=1)
        _integer(self.suite_deadline_ms, "validation.context.suite_deadline_ms", minimum=1)
        if not self.case_started_budget_ms < self.case_deadline_ms <= self.suite_deadline_ms:
            _fail("validation.context.deadline")
        _boolean(self.drop_answer_after_accepted, "validation.context.drop_answer_after_accepted")
        if self.drop_answer_after_accepted != (self.case.kind is ScenarioKind.LOST_ANSWER_RECONCILE):
            _fail("validation.context.drop_answer_after_accepted")


@dataclass(frozen=True, slots=True)
class CleanupContext:
    suite_id: str
    case_id: str
    token: str
    started_ms: int
    protect_deadline_ms: int
    flatten_deadline_ms: int
    hold_deadline_ms: int

    def __post_init__(self) -> None:
        _local_id(self.suite_id, "suite", "validation.cleanup_context.suite_id")
        _local_id(self.case_id, "case", "validation.cleanup_context.case_id")
        _local_id(self.token, "cleanup", "validation.cleanup_context.token")
        _integer(self.started_ms, "validation.cleanup_context.started_ms", minimum=1)
        _integer(self.protect_deadline_ms, "validation.cleanup_context.protect_deadline_ms", minimum=1)
        _integer(self.flatten_deadline_ms, "validation.cleanup_context.flatten_deadline_ms", minimum=1)
        _integer(self.hold_deadline_ms, "validation.cleanup_context.hold_deadline_ms", minimum=1)
        if (self.protect_deadline_ms, self.flatten_deadline_ms, self.hold_deadline_ms) != (
                self.started_ms + CLEANUP_PROTECT_MS,
                self.started_ms + CLEANUP_FLATTEN_MS,
                self.started_ms + CLEANUP_HOLD_MS):
            _fail("validation.cleanup_context.deadlines")


@dataclass(frozen=True, slots=True)
class BindingEvidence:
    account_id: str
    binding_digest: str
    exchange_uid: str | None
    venue: Venue
    environment: Environment
    settlement_asset: str
    isolated: bool
    confirmed_at_ms: int

    def __post_init__(self) -> None:
        _domain_id(self.account_id, "acct", "validation.binding_evidence.account_id")
        if type(self.binding_digest) is not str or _HEX_16.fullmatch(self.binding_digest) is None:
            _fail("validation.binding_evidence.binding_digest")
        if self.exchange_uid is not None and (type(self.exchange_uid) is not str or not self.exchange_uid):
            _fail("validation.binding_evidence.exchange_uid")
        _exact(self.venue, Venue, "validation.binding_evidence.venue")
        _exact(self.environment, Environment, "validation.binding_evidence.environment")
        if type(self.settlement_asset) is not str or not self.settlement_asset.isalnum():
            _fail("validation.binding_evidence.settlement_asset")
        _boolean(self.isolated, "validation.binding_evidence.isolated")
        _integer(self.confirmed_at_ms, "validation.binding_evidence.confirmed_at_ms", minimum=1)


@dataclass(frozen=True, slots=True)
class SetupEvidence:
    case_id: str
    at_ms: int
    binding_digest: str
    reconciled: bool
    initial_truth: FinalTruth

    def __post_init__(self) -> None:
        _local_id(self.case_id, "case", "validation.setup.case_id")
        _integer(self.at_ms, "validation.setup.at_ms", minimum=1)
        if type(self.binding_digest) is not str or _HEX_16.fullmatch(self.binding_digest) is None:
            _fail("validation.setup.binding_digest")
        _boolean(self.reconciled, "validation.setup.reconciled")
        _exact(self.initial_truth, FinalTruth, "validation.setup.initial_truth")


@dataclass(frozen=True, slots=True)
class RateLimitEvidence:
    kind: RateLimitKind
    observed_at_ms: int
    retry_at_ms: int
    non_protection_blocked: bool
    protection_lane_preserved: bool
    hold_latched: bool
    resumed_at_ms: int | None

    def __post_init__(self) -> None:
        _exact(self.kind, RateLimitKind, "validation.rate_limit.kind")
        _integer(self.observed_at_ms, "validation.rate_limit.observed_at_ms", minimum=1)
        _integer(self.retry_at_ms, "validation.rate_limit.retry_at_ms", minimum=1)
        if self.retry_at_ms <= self.observed_at_ms:
            _fail("validation.rate_limit.retry_at_ms")
        _boolean(self.non_protection_blocked, "validation.rate_limit.non_protection_blocked")
        _boolean(self.protection_lane_preserved, "validation.rate_limit.protection_lane_preserved")
        _boolean(self.hold_latched, "validation.rate_limit.hold_latched")
        if self.resumed_at_ms is not None:
            _integer(self.resumed_at_ms, "validation.rate_limit.resumed_at_ms", minimum=1)
        if self.kind is RateLimitKind.HTTP_429:
            if self.hold_latched or self.resumed_at_ms is None or self.resumed_at_ms < self.retry_at_ms:
                _fail("validation.rate_limit.http_429")
        elif not self.hold_latched or self.resumed_at_ms is not None:
            _fail("validation.rate_limit.http_418")


@dataclass(frozen=True, slots=True)
class ScenarioEvidence:
    case_id: str
    kind: ScenarioKind
    started_ms: int
    finished_ms: int
    synthetic_intent_id: str
    authorization_id: str
    nc06_authorized: bool
    orders_placed: int
    peak_notional: Decimal
    aggregate_peak_notional: Decimal
    max_leverage: int
    entry_confirmed: bool
    protection_confirmed: bool
    target_confirmed: bool
    stop_confirmed: bool
    partial_close_confirmed: bool
    dca_adds: int
    cancel_replace_confirmed: bool
    minimum_refused: bool
    accepted_at_ms: int | None
    answer_lost_at_ms: int | None
    reconciled: bool
    journal_reopened: bool
    restart_boot_id: str | None
    rate_limit: RateLimitEvidence | None
    final_truth: FinalTruth
    # True only when a market-trigger scenario's wait ran to its deadline with the model answering HOLD/NO_CHANGE on
    # every poll (or its exchange stop still working), and nothing but the entry and its protective stop was sent.
    trigger_missed: bool = False

    def __post_init__(self) -> None:
        _local_id(self.case_id, "case", "validation.evidence.case_id")
        _exact(self.kind, ScenarioKind, "validation.evidence.kind")
        _integer(self.started_ms, "validation.evidence.started_ms", minimum=1)
        _integer(self.finished_ms, "validation.evidence.finished_ms", minimum=1)
        if self.finished_ms < self.started_ms:
            _fail("validation.evidence.finished_ms")
        _domain_id(self.synthetic_intent_id, "int", "validation.evidence.synthetic_intent_id")
        _domain_id(self.authorization_id, "dec", "validation.evidence.authorization_id")
        _boolean(self.nc06_authorized, "validation.evidence.nc06_authorized")
        _integer(self.orders_placed, "validation.evidence.orders_placed")
        _decimal(self.peak_notional, "validation.evidence.peak_notional")
        _decimal(self.aggregate_peak_notional, "validation.evidence.aggregate_peak_notional")
        _integer(self.max_leverage, "validation.evidence.max_leverage")
        for name in ("entry_confirmed", "protection_confirmed", "target_confirmed", "stop_confirmed",
                     "partial_close_confirmed", "cancel_replace_confirmed", "minimum_refused", "reconciled",
                     "journal_reopened"):
            _boolean(getattr(self, name), f"validation.evidence.{name}")
        _integer(self.dca_adds, "validation.evidence.dca_adds")
        _boolean(self.trigger_missed, "validation.evidence.trigger_missed")
        for name in ("accepted_at_ms", "answer_lost_at_ms"):
            value = getattr(self, name)
            if value is not None:
                _integer(value, f"validation.evidence.{name}", minimum=1)
        if self.restart_boot_id is not None:
            _local_id(self.restart_boot_id, "boot", "validation.evidence.restart_boot_id")
        if self.rate_limit is not None:
            _exact(self.rate_limit, RateLimitEvidence, "validation.evidence.rate_limit")
        _exact(self.final_truth, FinalTruth, "validation.evidence.final_truth")


@dataclass(frozen=True, slots=True)
class CleanupEvidence:
    token: str
    started_ms: int
    protected_at_ms: int | None
    flattened_at_ms: int | None
    hold_stopped_at_ms: int | None
    final_truth: FinalTruth
    reconciled: bool
    cleanup_orders_total: int

    def __post_init__(self) -> None:
        _local_id(self.token, "cleanup", "validation.cleanup.token")
        _integer(self.started_ms, "validation.cleanup.started_ms", minimum=1)
        for name in ("protected_at_ms", "flattened_at_ms", "hold_stopped_at_ms"):
            value = getattr(self, name)
            if value is not None:
                _integer(value, f"validation.cleanup.{name}", minimum=1)
                if value < self.started_ms:
                    _fail(f"validation.cleanup.{name}")
        _exact(self.final_truth, FinalTruth, "validation.cleanup.final_truth")
        _boolean(self.reconciled, "validation.cleanup.reconciled")
        _integer(self.cleanup_orders_total, "validation.cleanup.cleanup_orders_total")
        terminals = (self.protected_at_ms is not None, self.flattened_at_ms is not None,
                     self.hold_stopped_at_ms is not None)
        expected = {
            FinalTruth.PROTECTED_RECONCILED: (True, False, False),
            FinalTruth.FLAT: (False, True, False),
            FinalTruth.HOLD: (False, False, True),
        }[self.final_truth]
        if terminals != expected:
            _fail("validation.cleanup.terminal_evidence")
        if self.final_truth in (FinalTruth.FLAT, FinalTruth.PROTECTED_RECONCILED) and not self.reconciled:
            _fail("validation.cleanup.reconciled")
        if self.final_truth is FinalTruth.HOLD and self.reconciled:
            _fail("validation.cleanup.hold_reconciled")


@dataclass(frozen=True, slots=True)
class CaseReport:
    case_id: str
    kind: ScenarioKind
    status: CaseStatus
    failure: FailureCode | None
    started_ms: int
    finished_ms: int
    final_truth: FinalTruth
    orders_total: int
    peak_notional: Decimal
    aggregate_peak_notional: Decimal
    max_leverage: int
    evidence_sha256: str
    attempt: int
    attempt_of: str | None

    def __post_init__(self) -> None:
        _local_id(self.case_id, "case", "validation.case_report.case_id")
        _integer(self.attempt, "validation.case_report.attempt", minimum=1, maximum=MAX_ATTEMPTS)
        if (self.attempt == 1) != (self.attempt_of is None):
            _fail("validation.case_report.attempt_of")
        if self.attempt_of is not None:
            _local_id(self.attempt_of, "case", "validation.case_report.attempt_of")
            if self.attempt_of == self.case_id or self.kind not in RETRYABLE_SCENARIOS:
                _fail("validation.case_report.attempt_of")
        _exact(self.kind, ScenarioKind, "validation.case_report.kind")
        _exact(self.status, CaseStatus, "validation.case_report.status")
        if self.failure is not None:
            _exact(self.failure, FailureCode, "validation.case_report.failure")
        if (self.failure is None) != (self.status is CaseStatus.PASSED):
            _fail("validation.case_report.failure")
        _integer(self.started_ms, "validation.case_report.started_ms", minimum=1)
        _integer(self.finished_ms, "validation.case_report.finished_ms", minimum=1)
        _exact(self.final_truth, FinalTruth, "validation.case_report.final_truth")
        _integer(self.orders_total, "validation.case_report.orders_total")
        _decimal(self.peak_notional, "validation.case_report.peak_notional")
        _decimal(self.aggregate_peak_notional, "validation.case_report.aggregate_peak_notional")
        _integer(self.max_leverage, "validation.case_report.max_leverage")
        if type(self.evidence_sha256) is not str or _HEX_64.fullmatch(self.evidence_sha256) is None:
            _fail("validation.case_report.evidence_sha256")


@dataclass(frozen=True, slots=True)
class SuiteReport:
    schema: str
    report_sha256: str
    suite_id: str
    build_sha: str
    account_id: str
    binding_digest: str
    started_ms: int
    finished_ms: int
    status: SuiteStatus
    failure: FailureCode | None
    cases: tuple[CaseReport, ...]
    redaction_clean: bool

    def __post_init__(self) -> None:
        if self.schema != REPORT_SCHEMA:
            _fail("validation.report.schema")
        if type(self.report_sha256) is not str or _HEX_64.fullmatch(self.report_sha256) is None:
            _fail("validation.report.report_sha256")
        _local_id(self.suite_id, "suite", "validation.report.suite_id")
        if type(self.build_sha) is not str or _HEX_40.fullmatch(self.build_sha) is None:
            _fail("validation.report.build_sha")
        _domain_id(self.account_id, "acct", "validation.report.account_id")
        if type(self.binding_digest) is not str or _HEX_16.fullmatch(self.binding_digest) is None:
            _fail("validation.report.binding_digest")
        _integer(self.started_ms, "validation.report.started_ms", minimum=1)
        _integer(self.finished_ms, "validation.report.finished_ms", minimum=1)
        _exact(self.status, SuiteStatus, "validation.report.status")
        if self.failure is not None:
            _exact(self.failure, FailureCode, "validation.report.failure")
        if (self.failure is None) != (self.status is SuiteStatus.PASSED):
            _fail("validation.report.failure")
        _exact(self.cases, tuple, "validation.report.cases")
        if any(type(case) is not CaseReport for case in self.cases):
            _fail("validation.report.cases")
        _boolean(self.redaction_clean, "validation.report.redaction_clean")
        if not self.redaction_clean:
            _fail("validation.report.redaction_clean")


class HarnessAdapter(Protocol):
    def confirm_binding(self, expected: HarnessBinding) -> BindingEvidence: ...

    def setup(self, context: ScenarioContext) -> SetupEvidence: ...

    def execute(self, context: ScenarioContext) -> ScenarioEvidence: ...

    def cleanup(self, context: CleanupContext) -> CleanupEvidence: ...


ReportWriter = Callable[[bytes], None]


def _canonical(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if type(value) is Decimal:
        return dec_str(value)
    if is_dataclass(value):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if type(value) is tuple:
        return [_canonical(item) for item in value]
    if value is None or type(value) in (str, int, bool):
        return value
    _fail("validation.canonical")


def _bytes(value: Any) -> bytes:
    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _derive(prefix: str, *parts: Any) -> str:
    raw = json.dumps(_canonical(tuple(parts)), sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True).encode("ascii")
    return prefix + "_" + hashlib.sha256(raw).hexdigest()[:32]


def _seal_binding_record(value: AccountBinding) -> AccountBinding:
    _exact(value, AccountBinding, "validation.binding.record")
    return AccountBinding(venue=value.venue, environment=value.environment, settlement_asset=value.settlement_asset,
                          key_digest=value.key_digest, exchange_uid=value.exchange_uid)


def _seal_account(value: Account) -> Account:
    _exact(value, Account, "validation.binding.account")
    confirmation = value.confirmation
    if confirmation is not None:
        _exact(confirmation, BindingConfirmation, "validation.binding.confirmation")
        confirmation = BindingConfirmation(account_id=confirmation.account_id,
                                           old_key_digest=confirmation.old_key_digest,
                                           new_key_digest=confirmation.new_key_digest,
                                           typed_phrase=confirmation.typed_phrase,
                                           confirmed_at_ms=confirmation.confirmed_at_ms)
    proposed = None if value.proposed_binding is None else _seal_binding_record(value.proposed_binding)
    return Account(account_id=value.account_id, label=value.label, hedge_mode=value.hedge_mode,
                   binding=_seal_binding_record(value.binding), binding_state=value.binding_state,
                   proposed_binding=proposed, confirmation=confirmation)


def _seal_case(value: ScenarioCase) -> ScenarioCase:
    _exact(value, ScenarioCase, "validation.case")
    return ScenarioCase(value.case_id, value.kind, value.symbol, value.side, value.leverage, value.max_notional,
                        value.max_orders)


def _seal_manifest(value: SuiteManifest) -> SuiteManifest:
    _exact(value, SuiteManifest, "validation.manifest")
    binding = value.binding
    _exact(binding, HarnessBinding, "validation.manifest.binding")
    _exact(binding.symbols, tuple, "validation.manifest.binding.symbols")
    _exact(value.cases, tuple, "validation.manifest.cases")
    sealed_binding = HarnessBinding(_seal_account(binding.account), binding.dedicated, tuple(binding.symbols))
    return SuiteManifest(value.build_sha, value.suite_started_ms, value.initial_boot_id, sealed_binding,
                         tuple(_seal_case(case) for case in value.cases))


def _seal_binding_evidence(value: BindingEvidence) -> BindingEvidence:
    _exact(value, BindingEvidence, "validation.binding_evidence")
    return BindingEvidence(value.account_id, value.binding_digest, value.exchange_uid, value.venue, value.environment,
                           value.settlement_asset, value.isolated, value.confirmed_at_ms)


def _seal_setup(value: SetupEvidence) -> SetupEvidence:
    _exact(value, SetupEvidence, "validation.setup")
    return SetupEvidence(value.case_id, value.at_ms, value.binding_digest, value.reconciled, value.initial_truth)


def _seal_scenario(value: ScenarioEvidence) -> ScenarioEvidence:
    _exact(value, ScenarioEvidence, "validation.evidence")
    items = [getattr(value, field.name) for field in fields(ScenarioEvidence)]
    rate_index = next(index for index, field in enumerate(fields(ScenarioEvidence)) if field.name == "rate_limit")
    rate_limit = items[rate_index]
    if rate_limit is not None:
        _exact(rate_limit, RateLimitEvidence, "validation.evidence.rate_limit")
        items[rate_index] = RateLimitEvidence(*(getattr(rate_limit, field.name) for field in fields(RateLimitEvidence)))
    return ScenarioEvidence(*items)


def _seal_cleanup(value: CleanupEvidence) -> CleanupEvidence:
    _exact(value, CleanupEvidence, "validation.cleanup")
    return CleanupEvidence(*(getattr(value, field.name) for field in fields(CleanupEvidence)))


def _case_context(manifest: SuiteManifest, suite_id: str, case: ScenarioCase, cursor_ms: int) -> ScenarioContext:
    case_deadline = min(cursor_ms + MAX_SCENARIO_MS, manifest.suite_started_ms + MAX_SUITE_MS)
    digest = manifest.binding.account.binding.key_digest
    restart_boot = _derive("boot", suite_id, case.case_id, "restart")
    intent_id = _derive("int", suite_id, case.case_id, "synthetic_nc06")
    authorization_id = _derive("dec", suite_id, case.case_id, "nc06_authorization")
    return ScenarioContext(suite_id, case, manifest.binding.account.account_id, digest, manifest.initial_boot_id,
                           restart_boot, intent_id, authorization_id, cursor_ms, case_deadline,
                           manifest.suite_started_ms + MAX_SUITE_MS,
                           case.kind is ScenarioKind.LOST_ANSWER_RECONCILE)


def _binding_matches(binding: HarnessBinding, evidence: BindingEvidence, suite_started_ms: int) -> bool:
    expected = binding.account.binding
    return (evidence.account_id == binding.account.account_id
            and evidence.binding_digest == expected.key_digest
            and evidence.exchange_uid == expected.exchange_uid
            and evidence.venue is expected.venue
            and evidence.environment is Environment.TESTNET
            and evidence.settlement_asset == expected.settlement_asset
            and evidence.isolated
            and evidence.confirmed_at_ms >= suite_started_ms)


def _setup_failure(context: ScenarioContext, setup: SetupEvidence) -> FailureCode | None:
    case = context.case
    if (setup.case_id != case.case_id or setup.binding_digest != context.binding_digest or not setup.reconciled
            or setup.initial_truth is not FinalTruth.FLAT
            or not context.case_started_budget_ms <= setup.at_ms <= context.case_deadline_ms):
        return FailureCode.SETUP
    return None


def _scenario_failure(context: ScenarioContext, setup: SetupEvidence, evidence: ScenarioEvidence) -> FailureCode | None:
    case = context.case
    if (evidence.case_id != case.case_id or evidence.kind is not case.kind
            or evidence.synthetic_intent_id != context.synthetic_intent_id
            or evidence.authorization_id != context.authorization_id or not evidence.nc06_authorized):
        return FailureCode.ASSERTION
    if not setup.at_ms <= evidence.started_ms <= evidence.finished_ms <= context.case_deadline_ms:
        return FailureCode.DEADLINE
    if (evidence.orders_placed > case.max_orders or evidence.peak_notional > case.max_notional
            or evidence.aggregate_peak_notional > MAX_AGGREGATE_NOTIONAL or evidence.max_leverage > case.leverage):
        return FailureCode.BOUNDS
    if case.kind not in (ScenarioKind.RATE_LIMIT_429, ScenarioKind.IP_BAN_418) and evidence.rate_limit is not None:
        return FailureCode.ASSERTION
    if (evidence.final_truth in (FinalTruth.FLAT, FinalTruth.PROTECTED_RECONCILED)
            and not evidence.reconciled) or evidence.final_truth is FinalTruth.HOLD:
        return FailureCode.EVIDENCE_HOLD
    safe = evidence.final_truth in (FinalTruth.FLAT, FinalTruth.PROTECTED_RECONCILED)
    kind = case.kind
    if evidence.trigger_missed and (kind not in RETRYABLE_SCENARIOS or evidence.target_confirmed
                                    or evidence.stop_confirmed or evidence.partial_close_confirmed
                                    or evidence.dca_adds != 0 or evidence.orders_placed != 2):
        return FailureCode.ASSERTION
    if kind in (ScenarioKind.LONG_TARGET, ScenarioKind.SHORT_TARGET):
        ok = evidence.entry_confirmed and evidence.protection_confirmed and evidence.target_confirmed and safe
    elif kind in (ScenarioKind.LONG_STOP, ScenarioKind.SHORT_STOP):
        ok = evidence.entry_confirmed and evidence.protection_confirmed and evidence.stop_confirmed and safe
    elif kind is ScenarioKind.PARTIAL_CLOSE:
        ok = evidence.entry_confirmed and evidence.protection_confirmed and evidence.partial_close_confirmed and safe
    elif kind is ScenarioKind.BOUNDED_DCA:
        ok = evidence.entry_confirmed and evidence.protection_confirmed and evidence.dca_adds == 1 and safe
    elif kind is ScenarioKind.CANCEL_REPLACE_RACE:
        ok = evidence.cancel_replace_confirmed and safe
    elif kind is ScenarioKind.LOST_ANSWER_RECONCILE:
        ok = (evidence.accepted_at_ms is not None and evidence.answer_lost_at_ms == evidence.accepted_at_ms
              and evidence.reconciled and safe)
    elif kind is ScenarioKind.MINIMUM_REFUSAL:
        ok = evidence.minimum_refused and not evidence.entry_confirmed and evidence.final_truth is FinalTruth.FLAT
    elif kind is ScenarioKind.RESTART_OPEN_POSITION:
        ok = (evidence.entry_confirmed and evidence.protection_confirmed and evidence.journal_reopened
              and evidence.restart_boot_id == context.restart_boot_id
              and evidence.restart_boot_id != context.boot_id and evidence.reconciled and safe)
    elif kind is ScenarioKind.RATE_LIMIT_429:
        rate = evidence.rate_limit
        ok = (type(rate) is RateLimitEvidence and rate.kind is RateLimitKind.HTTP_429
              and evidence.started_ms <= rate.observed_at_ms < rate.retry_at_ms
              and rate.resumed_at_ms is not None and rate.resumed_at_ms <= evidence.finished_ms
              and rate.non_protection_blocked and rate.protection_lane_preserved and safe)
    else:
        rate = evidence.rate_limit
        ok = (type(rate) is RateLimitEvidence and rate.kind is RateLimitKind.HTTP_418
              and evidence.started_ms <= rate.observed_at_ms <= evidence.finished_ms
              and rate.non_protection_blocked and rate.protection_lane_preserved and rate.hold_latched and safe)
    return None if ok else FailureCode.ASSERTION


def _cleanup_failure(context: CleanupContext, evidence: CleanupEvidence, orders_before: int,
                     max_orders: int) -> FailureCode | None:
    if evidence.token != context.token or evidence.started_ms < context.started_ms:
        return FailureCode.CLEANUP_HOLD
    if orders_before + evidence.cleanup_orders_total > max_orders:
        return FailureCode.BOUNDS
    if evidence.final_truth is FinalTruth.FLAT:
        ok = (evidence.flattened_at_ms is not None and evidence.flattened_at_ms <= context.flatten_deadline_ms
              and evidence.reconciled)
    elif evidence.final_truth is FinalTruth.PROTECTED_RECONCILED:
        ok = (evidence.protected_at_ms is not None and evidence.protected_at_ms <= context.protect_deadline_ms
              and evidence.reconciled)
    else:
        ok = evidence.hold_stopped_at_ms is not None and evidence.hold_stopped_at_ms <= context.hold_deadline_ms
        if ok:
            return FailureCode.CLEANUP_HOLD
    return None if ok else FailureCode.CLEANUP


def _case_report(context: ScenarioContext, setup: SetupEvidence | None, evidence: ScenarioEvidence | None,
                 cleanup: CleanupEvidence | None, failure: FailureCode | None, attempt: int = 1,
                 attempt_of: str | None = None, cleanup_failure: FailureCode | None = None) -> CaseReport:
    started = setup.at_ms if setup is not None else context.case_started_budget_ms
    finished = (cleanup.flattened_at_ms or cleanup.protected_at_ms or cleanup.hold_stopped_at_ms or cleanup.started_ms
                if cleanup is not None else (evidence.finished_ms if evidence is not None else started))
    untrusted = (FailureCode.CLEANUP_HOLD, FailureCode.CLEANUP_NOT_IDEMPOTENT, FailureCode.EVIDENCE_HOLD)
    # an earlier scenario failure must never hide a cleanup fault behind the first cleanup's FLAT
    untrusted_terminal = failure in untrusted or cleanup_failure in untrusted
    final_truth = cleanup.final_truth if cleanup is not None and not untrusted_terminal else FinalTruth.HOLD
    orders = (evidence.orders_placed if evidence is not None else 0) + (
        cleanup.cleanup_orders_total if cleanup is not None else 0)
    peak = evidence.peak_notional if evidence is not None else ZERO
    aggregate = evidence.aggregate_peak_notional if evidence is not None else ZERO
    leverage = evidence.max_leverage if evidence is not None else 0
    status = CaseStatus.PASSED if failure is None else (
        CaseStatus.HOLD if final_truth is FinalTruth.HOLD else CaseStatus.FAILED)
    evidence_hash = hashlib.sha256(_bytes((context, setup, evidence, cleanup, failure))).hexdigest()
    return CaseReport(context.case.case_id, context.case.kind, status, failure, started, finished, final_truth, orders,
                      peak, aggregate, leverage, evidence_hash, attempt, attempt_of)


def _retryable_miss(context: ScenarioContext, evidence: ScenarioEvidence | None, cleanup: CleanupEvidence | None,
                    failure: FailureCode | None, cleanup_failure: FailureCode | None = None,
                    cleanup_repeat: CleanupEvidence | None = None) -> bool:
    """A clean market miss: the trigger scenario opened and protected a real, NC-06-authorized position, the market
    never reached its level, and the attempt ended reconciled and flat with no other fault.  Nothing else retries."""
    case = context.case
    return (case.kind in RETRYABLE_SCENARIOS and failure is FailureCode.ASSERTION
            and evidence is not None and cleanup is not None
            and cleanup_failure is None and cleanup_repeat == cleanup and evidence.trigger_missed
            and evidence.orders_placed == 2
            and evidence.case_id == case.case_id and evidence.kind is case.kind
            and evidence.synthetic_intent_id == context.synthetic_intent_id
            and evidence.authorization_id == context.authorization_id and evidence.nc06_authorized
            and evidence.entry_confirmed and evidence.protection_confirmed and evidence.rate_limit is None
            and not evidence.target_confirmed and not evidence.stop_confirmed
            and not evidence.partial_close_confirmed and evidence.dca_adds == 0
            and evidence.final_truth in (FinalTruth.FLAT, FinalTruth.PROTECTED_RECONCILED) and evidence.reconciled
            and cleanup.final_truth is FinalTruth.FLAT and cleanup.reconciled)


def attempts_complete(cases: tuple[CaseReport, ...]) -> bool:
    """The suite pass rule over case reports (shared with the gate).

    The first twelve reports are attempt 1 of each required scenario, in order.  Every later report is a further
    attempt of a retryable scenario, numbered 2..MAX_ATTEMPTS in order.  In each scenario's chain, every attempt but
    the last is a clean flat ASSERTION miss, and the last one PASSED.
    """
    n = len(REQUIRED_SCENARIOS)
    if type(cases) is not tuple or len(cases) < n or len({c.case_id for c in cases}) != len(cases):
        return False
    first = cases[:n]
    if tuple(c.kind for c in first) != REQUIRED_SCENARIOS or any(c.attempt != 1 or c.attempt_of is not None
                                                                   for c in first):
        return False
    chains: dict[str, list[CaseReport]] = {c.case_id: [c] for c in first}
    for case in cases[n:]:
        chain = chains.get(case.attempt_of) if case.attempt_of is not None else None
        if chain is None or case.kind is not chain[0].kind or case.kind not in RETRYABLE_SCENARIOS:
            return False
        if case.attempt != len(chain) + 1:
            return False
        chain.append(case)
    for chain in chains.values():
        *misses, last = chain
        if last.status is not CaseStatus.PASSED or last.failure is not None:
            return False
        if any(m.status is not CaseStatus.FAILED or m.failure is not FailureCode.ASSERTION
               or m.final_truth is not FinalTruth.FLAT for m in misses):
            return False
    return True


def _report(manifest: SuiteManifest, suite_id: str, cases: tuple[CaseReport, ...],
            failure: FailureCode | None, finished_ms: int) -> SuiteReport:
    if failure is None and attempts_complete(cases):
        status = SuiteStatus.PASSED
    elif any(case.status is CaseStatus.HOLD for case in cases):
        status, failure = SuiteStatus.HOLD, failure or FailureCode.CLEANUP_HOLD
    else:
        status, failure = SuiteStatus.FAILED, failure or FailureCode.ASSERTION
    base = (REPORT_SCHEMA, suite_id, manifest.build_sha, manifest.binding.account.account_id,
            manifest.binding.account.binding.key_digest, manifest.suite_started_ms, finished_ms, status, failure, cases,
            True)
    digest = hashlib.sha256(_bytes(base)).hexdigest()
    return SuiteReport(REPORT_SCHEMA, digest, suite_id, manifest.build_sha, manifest.binding.account.account_id,
                       manifest.binding.account.binding.key_digest, manifest.suite_started_ms, finished_ms, status,
                       failure, cases, True)


def canonical_report_bytes(report: SuiteReport) -> bytes:
    """Return the one redacted canonical JSON representation accepted by writers."""
    _exact(report, SuiteReport, "validation.report")
    return _bytes(report)


def run_suite(manifest: SuiteManifest, adapter: HarnessAdapter, writer: ReportWriter) -> SuiteReport:
    """Run the frozen matrix through injected effects and write one redacted report.

    Cleanup is attempted twice with the same token.  Equality of the two sealed
    outcomes is the idempotency proof; a mismatch fails the case.
    """
    spec = _seal_manifest(manifest)
    if not callable(writer):
        _fail("validation.writer")
    for method in ("confirm_binding", "setup", "execute", "cleanup"):
        if not callable(getattr(adapter, method, None)):
            _fail("validation.adapter")
    suite_id = _derive("suite", spec.build_sha, spec.binding.account.account_id, spec.suite_started_ms, spec.cases)
    suite_deadline = spec.suite_started_ms + MAX_SUITE_MS
    cursor = spec.suite_started_ms
    reports: list[CaseReport] = []
    suite_failure: FailureCode | None = None
    try:
        binding_evidence = _seal_binding_evidence(adapter.confirm_binding(spec.binding))
    except Exception:
        binding_evidence = None
    if binding_evidence is None or not _binding_matches(spec.binding, binding_evidence, spec.suite_started_ms):
        suite_failure = FailureCode.BINDING
    else:
        cursor = max(cursor, binding_evidence.confirmed_at_ms)

    def run_case(case: ScenarioCase, at_ms: int, attempt: int, attempt_of: str | None
                 ) -> tuple[CaseReport, FailureCode | None, bool]:
        context = _case_context(spec, suite_id, case, at_ms)
        setup = None
        evidence = None
        cleanup = None
        failure = None
        try:
            setup = _seal_setup(adapter.setup(context))
        except Exception:
            failure = FailureCode.SETUP
        if setup is not None:
            failure = _setup_failure(context, setup)
        if setup is not None and failure is None:
            try:
                evidence = _seal_scenario(adapter.execute(context))
            except Exception:
                failure = failure or FailureCode.ADAPTER
        if setup is not None and evidence is not None:
            failure = failure or _scenario_failure(context, setup, evidence)
        cleanup_started = evidence.finished_ms if evidence is not None else (setup.at_ms if setup is not None else at_ms)
        token = _derive("cleanup", suite_id, case.case_id)
        cleanup_context = CleanupContext(suite_id, case.case_id, token, cleanup_started,
                                         cleanup_started + CLEANUP_PROTECT_MS,
                                         cleanup_started + CLEANUP_FLATTEN_MS,
                                         cleanup_started + CLEANUP_HOLD_MS)
        second = None
        cleanup_failure = None
        try:
            first = _seal_cleanup(adapter.cleanup(cleanup_context))
            second = _seal_cleanup(adapter.cleanup(cleanup_context))
            cleanup = first
            if first != second:
                cleanup_failure = FailureCode.CLEANUP_NOT_IDEMPOTENT
            else:
                cleanup_failure = _cleanup_failure(cleanup_context, first,
                                                   evidence.orders_placed if evidence is not None else 0,
                                                   case.max_orders)
        except Exception:
            cleanup_failure = FailureCode.CLEANUP
        failure = failure or cleanup_failure
        report = _case_report(context, setup, evidence, cleanup, failure, attempt, attempt_of, cleanup_failure)
        retry = report.final_truth is FinalTruth.FLAT and _retryable_miss(context, evidence, cleanup, failure,
                                                                          cleanup_failure, second)
        return report, failure, retry

    def stop_after(report: CaseReport, failure: FailureCode | None) -> FailureCode | None:
        if cursor > suite_deadline:
            return FailureCode.SUITE_DEADLINE
        if report.final_truth is FinalTruth.HOLD:
            return failure or FailureCode.CLEANUP_HOLD
        return None

    misses: list[ScenarioCase] = []
    if suite_failure is None:
        for case in spec.cases:
            if cursor >= suite_deadline:
                suite_failure = FailureCode.SUITE_DEADLINE
                break
            report, failure, retry = run_case(case, cursor, 1, None)
            reports.append(report)
            cursor = max(cursor, report.finished_ms)
            suite_failure = stop_after(report, failure)
            if suite_failure is not None:
                break
            if retry:
                misses.append(case)

    # Further attempts run only after the whole first pass, so each one sees a later, independent market window.  They
    # are skipped when any other scenario already failed: the suite cannot pass, so no further orders are spent.
    missed = {case.case_id for case in misses}
    hopeless = any(r.status is not CaseStatus.PASSED and r.case_id not in missed for r in reports)
    for original in misses if suite_failure is None and not hopeless else ():
        passed = False
        for attempt in range(2, MAX_ATTEMPTS + 1):
            if cursor + MAX_SCENARIO_MS > suite_deadline:
                break                   # never start an attempt that could not get a full scenario window
            again = ScenarioCase(_derive("case", suite_id, original.case_id, "attempt", attempt), original.kind,
                                 original.symbol, original.side, original.leverage, original.max_notional,
                                 original.max_orders)
            report, failure, retry = run_case(again, cursor, attempt, original.case_id)
            reports.append(report)
            cursor = max(cursor, report.finished_ms)
            suite_failure = stop_after(report, failure)
            passed = report.status is CaseStatus.PASSED
            if suite_failure is not None or not retry:
                break
        # once any scenario's chain ends without a pass the suite cannot pass: spend no further orders
        if suite_failure is not None or not passed:
            break

    report = _report(spec, suite_id, tuple(reports), suite_failure, cursor)
    try:
        writer(canonical_report_bytes(report))
    except Exception:
        raise HarnessError("writer_failed") from None
    return report


__all__ = [
    "BindingEvidence", "CaseReport", "CaseStatus", "CleanupContext", "CleanupEvidence", "FailureCode",
    "FinalTruth", "HarnessAdapter", "HarnessBinding", "HarnessError", "MAX_AGGREGATE_NOTIONAL",
    "MAX_LEVERAGE", "MAX_ORDERS_PER_SCENARIO", "MAX_SCENARIO_MS", "MAX_SCENARIO_NOTIONAL",
    "MAX_ATTEMPTS", "MAX_SUITE_MS", "REPORT_SCHEMA", "REQUIRED_SCENARIOS", "RETRYABLE_SCENARIOS", "attempts_complete", "RateLimitEvidence", "RateLimitKind", "ScenarioCase",
    "ScenarioContext", "ScenarioEvidence", "ScenarioKind", "SetupEvidence", "SuiteManifest", "SuiteReport",
    "SuiteStatus", "canonical_report_bytes", "run_suite",
]
