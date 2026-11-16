"""BASE-CLEAN pure promotion evaluator.

Callers discover tracked paths and collect evidence.  This module only seals
those immutable values, classifies the supplied universe, and returns one
deterministic ELIGIBLE or typed HOLD result.  It performs no filesystem, git,
process, network, clock, runtime, PAPER, deletion, or packaging operation.
"""
from __future__ import annotations

import enum
import hashlib
import json
import re
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


SCHEMA = "zackbot-base-clean/1"
REGISTER_SCHEMA = "zackbot-base-clean-register/1"
FULL_COMMAND = "python verify.py full"
NC01_MUTATION_COMMAND = "python tests/newcore/nc01_mutations.py"
NC02_MUTATION_COMMAND = "python tests/newcore_store/nc02a_mutations.py"
REQUIRED_COMMANDS = (FULL_COMMAND, NC01_MUTATION_COMMAND, NC02_MUTATION_COMMAND)

VALIDATE_MAX_US = Decimal("1000")
ENCODE_MAX_US = Decimal("6750")
DECODE_MAX_US = Decimal("23400")
SUBMIT_MAX_MS = Decimal("10")
STEP_MAX_MS = Decimal("50")
SNAPSHOT_MEAN_MAX_MS = Decimal("1")
PUBLISH_MAX_MS = Decimal("250")
MEMORY_PRIVATE_MAX_MIB = Decimal("512")
MEMORY_GROWTH_MAX_MIB = Decimal("32")
MEMORY_MIN_DURATION_MS = 60 * 60 * 1000
CURRENT_HOST_PYTHON_VERSION = "3.12.10"
NC04_ENDPOINT_TABLE_VERSION = "binance-usdm-v1-2026-11-03"
NC04_RESERVE_FORMULA = "max(minimum,ceil(verified_limit*0.10));capped_at_verified_limit"
NC04_COST_TABLE_SHA256 = "66e65242d60d23a8d07ecb99c17f4b449b28ce6b8ed64b5c05b248475937fa90"
NC04_WINDOW_POLICY = "request_weight_1m=60000;orders_10s=10000;orders_1m=60000"
NC04_POLICY_SHA256 = "ee19285ed8c051d52bc02ca3854a05359fafe80d9f050f062bc5c7bd6779e81f"

_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_PATH_PART = re.compile(r"[^/\\\x00-\x1f]+")
ZERO = Decimal("0")


class GateInputError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise GateInputError(code)


def _exact(value: Any, typ: type, code: str) -> Any:
    if type(value) is not typ:
        _fail(code)
    return value


def _bool(value: Any, code: str) -> bool:
    return _exact(value, bool, code)


def _int(value: Any, code: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail(code)
    return value


def _decimal(value: Any, code: str) -> Decimal:
    if type(value) is not Decimal:
        _fail(code)
    try:
        if (not value.is_finite() or value < ZERO or value.as_tuple().exponent > 0
                or value.is_zero() and (value.is_signed() or value.as_tuple().exponent != 0)
                or not value.is_zero() and value.as_tuple().exponent < 0 and value.as_tuple().digits[-1] == 0):
            _fail(code)
    except (InvalidOperation, ValueError):
        _fail(code)
    return value


def _sha(value: Any, code: str) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        _fail(code)
    return value


def _sha256(value: Any, code: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail(code)
    return value


def _identifier(value: Any, code: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        _fail(code)
    return value


def _path(value: Any, code: str, *, prefix: bool = False) -> str:
    if type(value) is not str or not value or value.startswith("/") or "\\" in value:
        _fail(code)
    if prefix and not value.endswith("/"):
        _fail(code)
    candidate = value[:-1] if prefix else value
    parts = candidate.split("/")
    if any(part in ("", ".", "..") or _PATH_PART.fullmatch(part) is None for part in parts):
        _fail(code)
    return value


class PathClass(enum.StrEnum):
    DELETE = "DELETE"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    TEMPORARY_ADAPTER = "TEMPORARY_ADAPTER"
    STILL_REQUIRED = "STILL_REQUIRED"


class PathRole(enum.StrEnum):
    BUILD_VERIFY = "build_verify"
    CONTRACT_EVIDENCE = "contract_evidence"
    NEWCORE_REPLACEMENT = "newcore_replacement"
    RESEARCH_EVIDENCE = "research_evidence"
    RUNTIME_ASSET = "runtime_asset"
    RUNTIME_LEGACY = "runtime_legacy"
    COMPARISON_ADAPTER = "comparison_adapter"


class TargetDisposition(enum.StrEnum):
    KEEP = "keep"
    TARGET_DELETE_NO_PORT = "target_delete_no_port"
    RETIRE_AFTER_COMPARISON = "retire_after_comparison"


class ProofKind(enum.StrEnum):
    NEWCORE_GOLDEN_ADAPTERS = "newcore_golden_adapters"
    REAL_TNET_EXACT_HEAD = "real_tnet_exact_head"
    TWO_ACCOUNT_ISOLATION = "two_account_isolation"
    CURRENT_HOST_CODEC_BASELINE = "current_host_codec_baseline"


class GateStatus(enum.StrEnum):
    ELIGIBLE = "eligible"
    HOLD = "hold"


class HoldCode(enum.StrEnum):
    REGISTER_INVALID = "register_invalid"
    UNIVERSE_UNVERIFIED = "universe_unverified"
    UNIVERSE_UNCLASSIFIED = "universe_unclassified"
    UNIVERSE_AMBIGUOUS = "universe_ambiguous"
    COMMAND_MISSING = "command_missing"
    COMMAND_FAILED = "command_failed"
    COMMAND_HEAD_MISMATCH = "command_head_mismatch"
    GOLDEN_ADAPTERS_MISSING = "golden_adapters_missing"
    REAL_TNET_MISSING = "real_tnet_missing"
    TWO_ACCOUNT_ISOLATION_MISSING = "two_account_isolation_missing"
    HOST_CODEC_REBASELINE_MISSING = "host_codec_rebaseline_missing"
    PROOF_FAILED = "proof_failed"
    PROOF_HEAD_MISMATCH = "proof_head_mismatch"
    PROOF_HOST_MISMATCH = "proof_host_mismatch"
    LATENCY_MISSING = "latency_missing"
    LATENCY_EXCEEDED = "latency_exceeded"
    MEMORY_MISSING = "memory_missing"
    MEMORY_EXCEEDED = "memory_exceeded"
    REQUEST_BUDGET_UNVERIFIED = "request_budget_unverified"


@dataclass(frozen=True, slots=True)
class PathRule:
    rule_id: str
    classification: PathClass
    role: PathRole
    prefixes: tuple[str, ...]
    paths: tuple[str, ...]
    excluded_paths: tuple[str, ...]
    target: TargetDisposition
    consumers: tuple[str, ...]
    expiry_gate: str | None

    def __post_init__(self) -> None:
        _identifier(self.rule_id, "validation.rule.id")
        _exact(self.classification, PathClass, "validation.rule.classification")
        _exact(self.role, PathRole, "validation.rule.role")
        for name in ("prefixes", "paths", "excluded_paths", "consumers"):
            _exact(getattr(self, name), tuple, f"validation.rule.{name}")
        if not self.prefixes and not self.paths:
            _fail("validation.rule.selectors")
        for value in self.prefixes:
            _path(value, "validation.rule.prefix", prefix=True)
        for name in ("paths", "excluded_paths"):
            for value in getattr(self, name):
                _path(value, f"validation.rule.{name}")
        for consumer in self.consumers:
            _identifier(consumer, "validation.rule.consumer")
        for name in ("prefixes", "paths", "excluded_paths", "consumers"):
            values = getattr(self, name)
            if len(set(values)) != len(values):
                _fail(f"validation.rule.{name}")
        _exact(self.target, TargetDisposition, "validation.rule.target")
        if self.expiry_gate is not None:
            _identifier(self.expiry_gate, "validation.rule.expiry_gate")
        if self.classification is PathClass.TEMPORARY_ADAPTER:
            if self.role is not PathRole.COMPARISON_ADAPTER or not self.consumers or self.expiry_gate is None:
                _fail("validation.rule.temporary_adapter")
        elif self.target is TargetDisposition.RETIRE_AFTER_COMPARISON:
            _fail("validation.rule.target")
        if self.classification is PathClass.DELETE and self.consumers:
            _fail("validation.rule.delete_consumers")


@dataclass(frozen=True, slots=True)
class GatePolicy:
    required_commands: tuple[str, ...]
    validate_max_us: Decimal
    encode_max_us: Decimal
    decode_max_us: Decimal
    submit_max_ms: Decimal
    step_max_ms: Decimal
    snapshot_mean_max_ms: Decimal
    publish_max_ms: Decimal
    memory_private_max_mib: Decimal
    memory_growth_max_mib: Decimal
    memory_min_duration_ms: int
    memory_provisional: bool
    current_host_python_version: str
    nc04_endpoint_table_version: str
    nc04_reserve_formula: str
    nc04_cost_table_sha256: str
    nc04_window_policy: str
    nc04_policy_sha256: str

    def __post_init__(self) -> None:
        _exact(self.required_commands, tuple, "validation.policy.commands")
        if self.required_commands != REQUIRED_COMMANDS:
            _fail("validation.policy.commands")
        expected = (
            ("validate_max_us", VALIDATE_MAX_US),
            ("encode_max_us", ENCODE_MAX_US),
            ("decode_max_us", DECODE_MAX_US),
            ("submit_max_ms", SUBMIT_MAX_MS),
            ("step_max_ms", STEP_MAX_MS),
            ("snapshot_mean_max_ms", SNAPSHOT_MEAN_MAX_MS),
            ("publish_max_ms", PUBLISH_MAX_MS),
            ("memory_private_max_mib", MEMORY_PRIVATE_MAX_MIB),
            ("memory_growth_max_mib", MEMORY_GROWTH_MAX_MIB),
        )
        for name, frozen in expected:
            if _decimal(getattr(self, name), f"validation.policy.{name}") != frozen:
                _fail(f"validation.policy.{name}")
        if _int(self.memory_min_duration_ms, "validation.policy.memory_duration", minimum=1) != MEMORY_MIN_DURATION_MS:
            _fail("validation.policy.memory_duration")
        if not _bool(self.memory_provisional, "validation.policy.memory_provisional"):
            _fail("validation.policy.memory_provisional")
        if self.current_host_python_version != CURRENT_HOST_PYTHON_VERSION:
            _fail("validation.policy.host_python")
        if self.nc04_endpoint_table_version != NC04_ENDPOINT_TABLE_VERSION:
            _fail("validation.policy.nc04_table")
        if self.nc04_reserve_formula != NC04_RESERVE_FORMULA:
            _fail("validation.policy.nc04_formula")
        if self.nc04_cost_table_sha256 != NC04_COST_TABLE_SHA256:
            _fail("validation.policy.nc04_costs")
        if self.nc04_window_policy != NC04_WINDOW_POLICY:
            _fail("validation.policy.nc04_windows")
        if self.nc04_policy_sha256 != NC04_POLICY_SHA256:
            _fail("validation.policy.nc04_policy")


@dataclass(frozen=True, slots=True)
class BaseCleanRegister:
    schema: str
    version: int
    rules: tuple[PathRule, ...]
    policy: GatePolicy

    def __post_init__(self) -> None:
        if self.schema != REGISTER_SCHEMA:
            _fail("validation.register.schema")
        if _int(self.version, "validation.register.version", minimum=1) != 1:
            _fail("validation.register.version")
        _exact(self.rules, tuple, "validation.register.rules")
        if not self.rules or any(type(rule) is not PathRule for rule in self.rules):
            _fail("validation.register.rules")
        if len({rule.rule_id for rule in self.rules}) != len(self.rules):
            _fail("validation.register.rule_ids")
        _exact(self.policy, GatePolicy, "validation.register.policy")


@dataclass(frozen=True, slots=True)
class CommandEvidence:
    command: str
    head_sha: str
    passed: bool
    complete: bool
    evidence_sha256: str

    def __post_init__(self) -> None:
        if type(self.command) is not str or not self.command or not self.command.isascii():
            _fail("validation.command.command")
        _sha(self.head_sha, "validation.command.head")
        _bool(self.passed, "validation.command.passed")
        _bool(self.complete, "validation.command.complete")
        _sha256(self.evidence_sha256, "validation.command.evidence")


@dataclass(frozen=True, slots=True)
class GateProof:
    kind: ProofKind
    head_sha: str
    host_id: str
    passed: bool
    evidence_sha256: str

    def __post_init__(self) -> None:
        _exact(self.kind, ProofKind, "validation.proof.kind")
        _sha(self.head_sha, "validation.proof.head")
        _identifier(self.host_id, "validation.proof.host")
        _bool(self.passed, "validation.proof.passed")
        _sha256(self.evidence_sha256, "validation.proof.evidence")


@dataclass(frozen=True, slots=True)
class LatencyEvidence:
    head_sha: str
    host_id: str
    python_version: str
    validate_us: Decimal
    encode_us: Decimal
    decode_us: Decimal
    submit_ms: Decimal
    step_ms: Decimal
    snapshot_mean_ms: Decimal
    publish_ms: Decimal
    evidence_sha256: str

    def __post_init__(self) -> None:
        _sha(self.head_sha, "validation.latency.head")
        _identifier(self.host_id, "validation.latency.host")
        if type(self.python_version) is not str or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", self.python_version) is None:
            _fail("validation.latency.python")
        for name in ("validate_us", "encode_us", "decode_us", "submit_ms", "step_ms",
                     "snapshot_mean_ms", "publish_ms"):
            _decimal(getattr(self, name), f"validation.latency.{name}")
        _sha256(self.evidence_sha256, "validation.latency.evidence")


@dataclass(frozen=True, slots=True)
class MemoryEvidence:
    head_sha: str
    host_id: str
    duration_ms: int
    private_working_set_mib: Decimal
    growth_mib: Decimal
    provisional: bool
    evidence_sha256: str

    def __post_init__(self) -> None:
        _sha(self.head_sha, "validation.memory.head")
        _identifier(self.host_id, "validation.memory.host")
        _int(self.duration_ms, "validation.memory.duration", minimum=1)
        _decimal(self.private_working_set_mib, "validation.memory.private")
        _decimal(self.growth_mib, "validation.memory.growth")
        _bool(self.provisional, "validation.memory.provisional")
        _sha256(self.evidence_sha256, "validation.memory.evidence")


@dataclass(frozen=True, slots=True)
class RequestBudgetEvidence:
    head_sha: str
    exchange_info_limits: bool
    user_rate_limit_limits: bool
    hardcoded_ceiling_present: bool
    endpoint_table_version: str
    reserve_formula: str
    cost_table_verified: bool
    windows_verified: bool
    cost_table_sha256: str
    window_policy: str
    policy_sha256: str

    def __post_init__(self) -> None:
        _sha(self.head_sha, "validation.request.head")
        for name in ("exchange_info_limits", "user_rate_limit_limits", "hardcoded_ceiling_present",
                     "cost_table_verified", "windows_verified"):
            _bool(getattr(self, name), f"validation.request.{name}")
        if type(self.endpoint_table_version) is not str or not self.endpoint_table_version:
            _fail("validation.request.table")
        if type(self.reserve_formula) is not str or not self.reserve_formula:
            _fail("validation.request.formula")
        _sha256(self.cost_table_sha256, "validation.request.cost_table")
        if type(self.window_policy) is not str or not self.window_policy:
            _fail("validation.request.window_policy")
        _sha256(self.policy_sha256, "validation.request.policy")


@dataclass(frozen=True, slots=True)
class GateEvidence:
    head_sha: str
    host_id: str
    tracked_paths: tuple[str, ...]
    universe_source: str
    universe_complete: bool
    universe_sha256: str
    commands: tuple[CommandEvidence, ...]
    proofs: tuple[GateProof, ...]
    latency: LatencyEvidence | None
    memory: MemoryEvidence | None
    request_budget: RequestBudgetEvidence | None

    def __post_init__(self) -> None:
        _sha(self.head_sha, "validation.evidence.head")
        _identifier(self.host_id, "validation.evidence.host")
        if self.universe_source != "git_ls_files":
            _fail("validation.evidence.universe_source")
        _bool(self.universe_complete, "validation.evidence.universe_complete")
        _sha256(self.universe_sha256, "validation.evidence.universe_sha256")
        for name, typ in (("tracked_paths", str), ("commands", CommandEvidence), ("proofs", GateProof)):
            values = _exact(getattr(self, name), tuple, f"validation.evidence.{name}")
            if any(type(value) is not typ for value in values):
                _fail(f"validation.evidence.{name}")
        for path in self.tracked_paths:
            _path(path, "validation.evidence.path")
        if (not self.tracked_paths or tuple(sorted(self.tracked_paths)) != self.tracked_paths
                or len(set(self.tracked_paths)) != len(self.tracked_paths)):
            _fail("validation.evidence.paths_order")
        if len({row.command for row in self.commands}) != len(self.commands):
            _fail("validation.evidence.commands")
        if len({row.kind for row in self.proofs}) != len(self.proofs):
            _fail("validation.evidence.proofs")
        if self.latency is not None:
            _exact(self.latency, LatencyEvidence, "validation.evidence.latency")
        if self.memory is not None:
            _exact(self.memory, MemoryEvidence, "validation.evidence.memory")
        if self.request_budget is not None:
            _exact(self.request_budget, RequestBudgetEvidence, "validation.evidence.request")


@dataclass(frozen=True, slots=True)
class GateResult:
    schema: str
    decision_id: str
    head_sha: str
    status: GateStatus
    holds: tuple[HoldCode, ...]
    tracked_count: int
    class_counts: tuple[tuple[PathClass, int], ...]
    register_sha256: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            _fail("validation.result.schema")
        if type(self.decision_id) is not str or re.fullmatch(r"base_[0-9a-f]{32}", self.decision_id) is None:
            _fail("validation.result.id")
        _sha(self.head_sha, "validation.result.head")
        _exact(self.status, GateStatus, "validation.result.status")
        _exact(self.holds, tuple, "validation.result.holds")
        if any(type(hold) is not HoldCode for hold in self.holds) or tuple(sorted(set(self.holds), key=str)) != self.holds:
            _fail("validation.result.holds")
        if (self.status is GateStatus.ELIGIBLE) != (not self.holds):
            _fail("validation.result.status")
        _int(self.tracked_count, "validation.result.tracked_count")
        _exact(self.class_counts, tuple, "validation.result.class_counts")
        _sha256(self.register_sha256, "validation.result.register_hash")
        _sha256(self.evidence_sha256, "validation.result.evidence_hash")


def _canonical(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if type(value) is Decimal:
        return format(value, "f")
    if is_dataclass(value):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if type(value) is tuple:
        return [_canonical(item) for item in value]
    if value is None or type(value) in (str, int, bool):
        return value
    _fail("validation.canonical")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _seal_dataclass(value: Any, typ: type, code: str) -> Any:
    _exact(value, typ, code)
    return typ(*(getattr(value, field.name) for field in fields(typ)))


def _seal_register(value: BaseCleanRegister) -> BaseCleanRegister:
    _exact(value, BaseCleanRegister, "validation.register")
    _exact(value.rules, tuple, "validation.register.rules")
    rules = tuple(sorted(
        (_seal_dataclass(row, PathRule, "validation.register.rule") for row in value.rules),
        key=lambda row: row.rule_id,
    ))
    policy = _seal_dataclass(value.policy, GatePolicy, "validation.register.policy")
    return BaseCleanRegister(value.schema, value.version, rules, policy)


def _seal_evidence(value: GateEvidence) -> GateEvidence:
    _exact(value, GateEvidence, "validation.evidence")
    _exact(value.tracked_paths, tuple, "validation.evidence.tracked_paths")
    _exact(value.commands, tuple, "validation.evidence.commands")
    _exact(value.proofs, tuple, "validation.evidence.proofs")
    commands = tuple(sorted(
        (_seal_dataclass(row, CommandEvidence, "validation.command") for row in value.commands),
        key=lambda row: row.command,
    ))
    proofs = tuple(sorted(
        (_seal_dataclass(row, GateProof, "validation.proof") for row in value.proofs),
        key=lambda row: row.kind.value,
    ))
    latency = None if value.latency is None else _seal_dataclass(value.latency, LatencyEvidence, "validation.latency")
    memory = None if value.memory is None else _seal_dataclass(value.memory, MemoryEvidence, "validation.memory")
    request = (None if value.request_budget is None else
               _seal_dataclass(value.request_budget, RequestBudgetEvidence, "validation.request"))
    return GateEvidence(value.head_sha, value.host_id, tuple(value.tracked_paths), value.universe_source,
                        value.universe_complete, value.universe_sha256, commands, proofs,
                        latency, memory, request)


def _matches(rule: PathRule, path: str) -> bool:
    if path in rule.excluded_paths:
        return False
    return path in rule.paths or any(path.startswith(prefix) for prefix in rule.prefixes)


def _required_metadata_ok(register: BaseCleanRegister) -> bool:
    expected = {"engine.py", "grid.py", "ai_filter.py"}
    matched: set[str] = set()
    for rule in register.rules:
        selected = expected.intersection(rule.paths)
        if selected:
            if (rule.classification is not PathClass.STILL_REQUIRED
                    or rule.role is not PathRole.RUNTIME_LEGACY
                    or rule.target is not TargetDisposition.TARGET_DELETE_NO_PORT
                    or not rule.consumers
                    or rule.expiry_gate != "replacement_entrypoint_zero_consumers"):
                return False
            matched.update(selected)
    return matched == expected


def _command_holds(evidence: GateEvidence, required: tuple[str, ...]) -> set[HoldCode]:
    holds: set[HoldCode] = set()
    by = {row.command: row for row in evidence.commands}
    for command in required:
        row = by.get(command)
        if row is None:
            holds.add(HoldCode.COMMAND_MISSING)
        elif row.head_sha != evidence.head_sha:
            holds.add(HoldCode.COMMAND_HEAD_MISMATCH)
        elif not row.passed or not row.complete:
            holds.add(HoldCode.COMMAND_FAILED)
    return holds


_PROOF_HOLD = {
    ProofKind.NEWCORE_GOLDEN_ADAPTERS: HoldCode.GOLDEN_ADAPTERS_MISSING,
    ProofKind.REAL_TNET_EXACT_HEAD: HoldCode.REAL_TNET_MISSING,
    ProofKind.TWO_ACCOUNT_ISOLATION: HoldCode.TWO_ACCOUNT_ISOLATION_MISSING,
    ProofKind.CURRENT_HOST_CODEC_BASELINE: HoldCode.HOST_CODEC_REBASELINE_MISSING,
}


def _proof_holds(evidence: GateEvidence) -> set[HoldCode]:
    holds: set[HoldCode] = set()
    by = {row.kind: row for row in evidence.proofs}
    for kind, missing in _PROOF_HOLD.items():
        row = by.get(kind)
        if row is None:
            holds.add(missing)
            continue
        if row.head_sha != evidence.head_sha:
            holds.add(HoldCode.PROOF_HEAD_MISMATCH)
        if not row.passed:
            holds.add(HoldCode.PROOF_FAILED)
        if kind is ProofKind.CURRENT_HOST_CODEC_BASELINE:
            if row.host_id != evidence.host_id:
                holds.add(HoldCode.PROOF_HOST_MISMATCH)
            latency = evidence.latency
            memory = evidence.memory
            if (latency is None or memory is None
                    or row.evidence_sha256 != latency.evidence_sha256
                    or row.evidence_sha256 != memory.evidence_sha256):
                holds.add(HoldCode.HOST_CODEC_REBASELINE_MISSING)
    return holds


def _latency_holds(evidence: GateEvidence, policy: GatePolicy) -> set[HoldCode]:
    row = evidence.latency
    if row is None or row.head_sha != evidence.head_sha or row.host_id != evidence.host_id:
        return {HoldCode.LATENCY_MISSING}
    if row.python_version != policy.current_host_python_version:
        return {HoldCode.HOST_CODEC_REBASELINE_MISSING}
    limits = (
        (row.validate_us, policy.validate_max_us),
        (row.encode_us, policy.encode_max_us),
        (row.decode_us, policy.decode_max_us),
        (row.submit_ms, policy.submit_max_ms),
        (row.step_ms, policy.step_max_ms),
        (row.snapshot_mean_ms, policy.snapshot_mean_max_ms),
        (row.publish_ms, policy.publish_max_ms),
    )
    return {HoldCode.LATENCY_EXCEEDED} if any(value > limit for value, limit in limits) else set()


def _memory_holds(evidence: GateEvidence, policy: GatePolicy) -> set[HoldCode]:
    row = evidence.memory
    if row is None or row.head_sha != evidence.head_sha or row.host_id != evidence.host_id:
        return {HoldCode.MEMORY_MISSING}
    if (row.duration_ms < policy.memory_min_duration_ms or row.provisional is not policy.memory_provisional
            or row.private_working_set_mib > policy.memory_private_max_mib
            or row.growth_mib > policy.memory_growth_max_mib):
        return {HoldCode.MEMORY_EXCEEDED}
    return set()


def _request_holds(evidence: GateEvidence, policy: GatePolicy) -> set[HoldCode]:
    row = evidence.request_budget
    if (row is None or row.head_sha != evidence.head_sha or not row.exchange_info_limits
            or not row.user_rate_limit_limits or row.hardcoded_ceiling_present or not row.cost_table_verified
            or not row.windows_verified or row.endpoint_table_version != policy.nc04_endpoint_table_version
            or row.reserve_formula != policy.nc04_reserve_formula
            or row.cost_table_sha256 != policy.nc04_cost_table_sha256
            or row.window_policy != policy.nc04_window_policy
            or row.policy_sha256 != policy.nc04_policy_sha256):
        return {HoldCode.REQUEST_BUDGET_UNVERIFIED}
    return set()


def evaluate_base_clean(register: BaseCleanRegister, evidence: GateEvidence) -> GateResult:
    """Seal and evaluate the supplied register and exact-head evidence."""
    spec = _seal_register(register)
    facts = _seal_evidence(evidence)
    holds: set[HoldCode] = set()
    if not _required_metadata_ok(spec):
        holds.add(HoldCode.REGISTER_INVALID)

    expected_universe_hash = hashlib.sha256(canonical_bytes(facts.tracked_paths)).hexdigest()
    if not facts.universe_complete or facts.universe_sha256 != expected_universe_hash:
        holds.add(HoldCode.UNIVERSE_UNVERIFIED)

    counts = {kind: 0 for kind in PathClass}
    for path in facts.tracked_paths:
        matches = [rule for rule in spec.rules if _matches(rule, path)]
        if not matches:
            holds.add(HoldCode.UNIVERSE_UNCLASSIFIED)
        elif len(matches) > 1:
            holds.add(HoldCode.UNIVERSE_AMBIGUOUS)
        else:
            counts[matches[0].classification] += 1

    holds.update(_command_holds(facts, spec.policy.required_commands))
    holds.update(_proof_holds(facts))
    holds.update(_latency_holds(facts, spec.policy))
    holds.update(_memory_holds(facts, spec.policy))
    holds.update(_request_holds(facts, spec.policy))

    ordered_holds = tuple(sorted(holds, key=str))
    register_hash = hashlib.sha256(canonical_bytes(spec)).hexdigest()
    evidence_hash = hashlib.sha256(canonical_bytes(facts)).hexdigest()
    status = GateStatus.ELIGIBLE if not ordered_holds else GateStatus.HOLD
    base = (SCHEMA, facts.head_sha, status, ordered_holds, len(facts.tracked_paths),
            tuple((kind, counts[kind]) for kind in PathClass), register_hash, evidence_hash)
    decision_id = "base_" + hashlib.sha256(canonical_bytes(base)).hexdigest()[:32]
    return GateResult(SCHEMA, decision_id, facts.head_sha, status, ordered_holds, len(facts.tracked_paths),
                      tuple((kind, counts[kind]) for kind in PathClass), register_hash, evidence_hash)


__all__ = [
    "BaseCleanRegister", "CommandEvidence", "FULL_COMMAND", "GateEvidence", "GateInputError", "GatePolicy",
    "GateProof", "GateResult", "GateStatus", "HoldCode", "LatencyEvidence", "MemoryEvidence",
    "NC01_MUTATION_COMMAND", "NC02_MUTATION_COMMAND", "PathClass", "PathRole", "PathRule", "ProofKind",
    "RequestBudgetEvidence", "TargetDisposition", "canonical_bytes", "evaluate_base_clean",
]
