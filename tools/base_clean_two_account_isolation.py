"""Offline deterministic BASE-CLEAN two-account isolation proof.

The only venues in this module are local typed fakes.  Collection reads local
git/host identity and may write one canonical artifact; it performs no network,
credential, runtime, PAPER, testnet, or mainnet operation.
"""
from __future__ import annotations

import argparse
import enum
import hashlib
import json
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, fields
from decimal import Decimal
from pathlib import Path

from newcore.account_context import (
    ACCOUNT_ORDER_LIMIT_PATH,
    ACCOUNT_SOURCE,
    EXCHANGE_INFO_PATH,
    MARKET_DATA_SOURCE,
    SOURCE_RETRIEVED_ON,
    AccountBudget,
    AccountContext,
    LimitAuthority,
    Operation,
    RawLimitPayload,
    VenueWork,
    WorkState,
)
from newcore.base_clean import GateProof, ProofKind, canonical_bytes
from newcore.domain import Action
from newcore.ports.rate_blocks import RateBlockRecord, RateBlockState
from newcore.ports.venue import (
    MarketOrder,
    OrderOutcome,
    OrderRef,
    OutcomeKind,
    ReadKind,
    ReadOutcome,
    StopOrder,
)
from newcore.venue.binance_usdm_testnet import RateEvidence


SCHEMA = "zackbot-base-clean-two-account-isolation/1"
HARNESS_VERSION = "two-account-isolation-v1"
ROOT = Path(__file__).resolve().parents[1]
_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_HOST = re.compile(r"host_[0-9a-f]{32}")
_ACCOUNT_A = "acct_" + "a" * 32
_ACCOUNT_B = "acct_" + "b" * 32
_EGRESS_A = "proof-egress-a"
_EGRESS_B = "proof-egress-b"
_WALL = 1_800_000_000_000
_START = 1_000_000
_DEADLINE_OFFSET_MS = 100
_DISPATCH_OFFSET_MS = 50
_VENUE_OFFSET_MS = 25


class ProofError(ValueError):
    """Closed stable refusal; raw exceptions never cross this boundary."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ProofError(code)


def _exact(value: object, typ: type, code: str):
    if type(value) is not typ:
        _fail(code)
    return value


def _integer(value: object, code: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail(code)
    return value


def _boolean(value: object, code: str) -> bool:
    return _exact(value, bool, code)


def _sha(value: object, code: str) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        _fail(code)
    return value


def _sha256(value: object, code: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail(code)
    return value


def _host(value: object, code: str) -> str:
    if type(value) is not str or _HOST.fullmatch(value) is None:
        _fail(code)
    return value


class FaultKind(enum.StrEnum):
    OUTAGE_503 = "outage_503"
    VENUE_EXCEPTION = "venue_exception"
    RATE_429 = "rate_429"
    BAN_418 = "ban_418"


@dataclass(frozen=True, slots=True)
class FaultObservation:
    sequence: int
    kind: FaultKind
    account_a_budget_state: RateBlockState
    account_b_queued: int

    def __post_init__(self) -> None:
        _integer(self.sequence, "validation.fault.sequence", minimum=1)
        _exact(self.kind, FaultKind, "validation.fault.kind")
        _exact(self.account_a_budget_state, RateBlockState, "validation.fault.a_state")
        if _integer(self.account_b_queued, "validation.fault.b_queued") != 1:
            _fail("isolation.b_not_queued")


_EXPECTED_TRACE = (
    FaultObservation(1, FaultKind.OUTAGE_503, RateBlockState.OPEN, 1),
    FaultObservation(2, FaultKind.VENUE_EXCEPTION, RateBlockState.OPEN, 1),
    FaultObservation(3, FaultKind.RATE_429, RateBlockState.BLOCKED_UNTIL, 1),
    FaultObservation(4, FaultKind.BAN_418, RateBlockState.BLOCKED_UNKNOWN_DURATION, 1),
)


@dataclass(frozen=True, slots=True)
class IsolationArtifactBody:
    schema: str
    head_sha: str
    host_id: str
    harness_version: str
    bindings_sha256: str
    fault_trace: tuple[FaultObservation, ...]
    account_a_exception_state: WorkState
    account_a_latched_418_state: WorkState
    account_a_reconcile_before_restart: bool
    account_a_journal_records: int
    account_b_journal_records: int
    account_b_budget_state: RateBlockState
    account_b_protection_state: WorkState
    account_b_enqueued_offset_ms: int
    account_b_completed_offset_ms: int
    account_b_deadline_offset_ms: int
    distinct_egress: bool
    no_cross_account_mutation: bool
    journal_isolated: bool
    venue_isolated: bool

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            _fail("validation.artifact.schema")
        _sha(self.head_sha, "validation.artifact.head")
        _host(self.host_id, "validation.artifact.host")
        if self.harness_version != HARNESS_VERSION:
            _fail("validation.artifact.harness")
        _sha256(self.bindings_sha256, "validation.artifact.bindings")
        _exact(self.fault_trace, tuple, "validation.artifact.trace")
        if any(type(row) is not FaultObservation for row in self.fault_trace):
            _fail("validation.artifact.trace")
        if self.fault_trace != _EXPECTED_TRACE:
            _fail("isolation.fault_sequence")
        _exact(self.account_a_exception_state, WorkState, "validation.artifact.a_exception")
        _exact(self.account_a_latched_418_state, WorkState, "validation.artifact.a_418")
        _exact(self.account_b_budget_state, RateBlockState, "validation.artifact.b_budget")
        _exact(self.account_b_protection_state, WorkState, "validation.artifact.b_protection")
        if self.account_a_exception_state is not WorkState.UNKNOWN:
            _fail("isolation.exception_not_unknown")
        if self.account_a_latched_418_state is not WorkState.REFUSED:
            _fail("isolation.418_not_latched")
        if self.account_b_budget_state is not RateBlockState.OPEN:
            _fail("isolation.b_budget_mutated")
        if self.account_b_protection_state is not WorkState.COMPLETED:
            _fail("isolation.b_protection_not_completed")
        for name in (
            "account_a_reconcile_before_restart", "distinct_egress",
            "no_cross_account_mutation", "journal_isolated", "venue_isolated",
        ):
            if not _boolean(getattr(self, name), f"validation.artifact.{name}"):
                _fail(f"isolation.{name}")
        if _integer(self.account_a_journal_records, "validation.artifact.a_journal") != 2:
            _fail("isolation.a_journal")
        if _integer(self.account_b_journal_records, "validation.artifact.b_journal") != 0:
            _fail("isolation.b_journal")
        enqueued = _integer(self.account_b_enqueued_offset_ms, "validation.artifact.b_enqueued")
        completed = _integer(self.account_b_completed_offset_ms, "validation.artifact.b_completed")
        deadline = _integer(self.account_b_deadline_offset_ms, "validation.artifact.b_deadline", minimum=1)
        if (enqueued, completed, deadline) != (0, 75, 100) or completed >= deadline:
            _fail("isolation.b_deadline")


@dataclass(frozen=True, slots=True)
class IsolationArtifact:
    body: IsolationArtifactBody
    artifact_sha256: str

    def __post_init__(self) -> None:
        _exact(self.body, IsolationArtifactBody, "validation.envelope.body")
        _sha256(self.artifact_sha256, "validation.envelope.digest")
        if hashlib.sha256(canonical_bytes(self.body)).hexdigest() != self.artifact_sha256:
            _fail("validation.envelope.digest")


class _Clock:
    def __init__(self, value: int = _START):
        self.value = value

    def __call__(self) -> int:
        return self.value

    def advance(self, milliseconds: int) -> None:
        self.value += milliseconds


class _MemoryJournal:
    def __init__(self):
        self.records: list[RateBlockRecord] = []

    def append_rate_block(self, record: RateBlockRecord) -> None:
        self.records.append(record)

    def load_rate_blocks(self, account_id: str, egress_id: str) -> tuple[RateBlockRecord, ...]:
        return tuple(row for row in self.records
                     if (row.account_id, row.egress_id) == (account_id, egress_id))


class _FakeVenue:
    def __init__(self, clock: _Clock, *, fail_query: bool = False, stop_advance_ms: int = 0):
        self.clock = clock
        self.fail_query = fail_query
        self.stop_advance_ms = stop_advance_ms
        self.calls: list[str] = []

    def _order(self, name: str, ref: OrderRef) -> OrderOutcome:
        self.calls.append(name)
        return OrderOutcome(kind=OutcomeKind.ACKNOWLEDGED, ref=ref,
                            observed_at_ms=_WALL + (self.clock() - _START))

    def submit_market(self, order: MarketOrder) -> OrderOutcome:
        return self._order("submit_market", order.ref)

    def submit_stop(self, order: StopOrder) -> OrderOutcome:
        self.calls.append("submit_stop")
        self.clock.advance(self.stop_advance_ms)
        return OrderOutcome(kind=OutcomeKind.ACKNOWLEDGED, ref=order.ref,
                            observed_at_ms=_WALL + (self.clock() - _START))

    def cancel(self, ref: OrderRef) -> OrderOutcome:
        return self._order("cancel", ref)

    def query(self, ref: OrderRef) -> OrderOutcome:
        self.calls.append("query")
        if self.fail_query:
            raise RuntimeError("raw fake venue secret must not escape")
        return OrderOutcome(kind=OutcomeKind.ACKNOWLEDGED, ref=ref, observed_at_ms=_WALL)

    def positions(self, symbol: str | None = None) -> ReadOutcome:
        self.calls.append("positions")
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=_WALL, value=())

    def open_orders(self, symbol: str | None = None) -> ReadOutcome:
        self.calls.append("open_orders")
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=_WALL, value=())

    def fills(self, symbol: str, exchange_order_id: str) -> ReadOutcome:
        self.calls.append("fills")
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=_WALL, value=())


def _limit_evidence() -> tuple[RawLimitPayload, RawLimitPayload]:
    exchange = RawLimitPayload(
        authority=LimitAuthority.EXCHANGE_INFO, method="GET", path=EXCHANGE_INFO_PATH,
        source_url=MARKET_DATA_SOURCE, retrieved_on=SOURCE_RETRIEVED_ON,
        payload={"rateLimits": [
            {"rateLimitType": "REQUEST_WEIGHT", "interval": "MINUTE", "intervalNum": 1, "limit": 100},
        ]},
    )
    account = RawLimitPayload(
        authority=LimitAuthority.USER_RATE_LIMIT, method="GET", path=ACCOUNT_ORDER_LIMIT_PATH,
        source_url=ACCOUNT_SOURCE, retrieved_on=SOURCE_RETRIEVED_ON,
        payload=[
            {"rateLimitType": "ORDERS", "interval": "SECOND", "intervalNum": 10, "limit": 20},
            {"rateLimitType": "ORDERS", "interval": "MINUTE", "intervalNum": 1, "limit": 100},
        ],
    )
    return exchange, account


def _budget(account_id: str, egress_id: str, clock: _Clock,
            journal: _MemoryJournal) -> AccountBudget:
    exchange, account = _limit_evidence()
    return AccountBudget(
        account_id=account_id, egress_id=egress_id, exchange_info=exchange,
        account_order_limits=account, monotonic_clock=clock,
        wall_clock=lambda: _WALL + (clock() - _START), journal=journal,
    )


def _binding_digest() -> str:
    return hashlib.sha256(canonical_bytes((
        (_ACCOUNT_A, _EGRESS_A), (_ACCOUNT_B, _EGRESS_B),
    ))).hexdigest()


def _observe_fault(sequence: int, kind: FaultKind, account_a: AccountContext,
                   account_b: AccountContext) -> FaultObservation:
    b_snapshot = account_b.snapshot()
    return FaultObservation(sequence, kind, account_a.snapshot().budget.state, len(b_snapshot.queued))


def run_two_account_isolation(*, head_sha: str, host_id: str) -> IsolationArtifact:
    """Run the closed local-fake scenario and return an accepted artifact or a stable refusal."""
    _sha(head_sha, "expected_head_invalid")
    _host(host_id, "expected_host_invalid")
    clock_a, clock_b = _Clock(), _Clock()
    journal_a, journal_b = _MemoryJournal(), _MemoryJournal()
    venue_a = _FakeVenue(clock_a, fail_query=True)
    venue_b = _FakeVenue(clock_b, stop_advance_ms=_VENUE_OFFSET_MS)
    budget_a = _budget(_ACCOUNT_A, _EGRESS_A, clock_a, journal_a)
    budget_b = _budget(_ACCOUNT_B, _EGRESS_B, clock_b, journal_b)
    account_a = AccountContext(account_id=_ACCOUNT_A, egress_id=_EGRESS_A,
                               venue=venue_a, budget=budget_a, monotonic_clock=clock_a)
    account_b = AccountContext(account_id=_ACCOUNT_B, egress_id=_EGRESS_B,
                               venue=venue_b, budget=budget_b, monotonic_clock=clock_b)
    distinct = (_ACCOUNT_A != _ACCOUNT_B and _EGRESS_A != _EGRESS_B
                and budget_a is not budget_b and journal_a is not journal_b and venue_a is not venue_b)

    b_ref = OrderRef(symbol="BTCUSDT", client_id="zbn1a-proof-b-stop", route="algo")
    b_stop = StopOrder(ref=b_ref, position_side="LONG", qty=Decimal("0.001"),
                       stop_price=Decimal("50000"))
    account_b.submit(VenueWork(
        work_id="protect-b", action=Action.PROTECT, operation=Operation.SUBMIT_STOP,
        payload=b_stop, enqueued_at_ms=_START, deadline_ms=_START + _DEADLINE_OFFSET_MS,
    ))
    b_baseline = account_b.snapshot()
    trace: list[FaultObservation] = []

    budget_a.observe(RateEvidence(503, None, None, _WALL), now_ms=clock_a(), wall_now_ms=_WALL)
    trace.append(_observe_fault(1, FaultKind.OUTAGE_503, account_a, account_b))

    a_ref = OrderRef(symbol="BTCUSDT", client_id="zbn1o-proof-a-query", route="classic")
    account_a.submit(VenueWork(
        work_id="query-a", action=Action.RECONCILE, operation=Operation.QUERY,
        payload=a_ref, enqueued_at_ms=_START, deadline_ms=_START + 1_000,
    ))
    a_exception = account_a.run_next()
    trace.append(_observe_fault(2, FaultKind.VENUE_EXCEPTION, account_a, account_b))

    budget_a.observe(RateEvidence(429, 5_000, 7, _WALL), now_ms=clock_a(), wall_now_ms=_WALL)
    trace.append(_observe_fault(3, FaultKind.RATE_429, account_a, account_b))
    budget_a.observe(RateEvidence(418, None, 8, _WALL), now_ms=clock_a(), wall_now_ms=_WALL)
    trace.append(_observe_fault(4, FaultKind.BAN_418, account_a, account_b))

    a_stop_ref = OrderRef(symbol="BTCUSDT", client_id="zbn1a-proof-a-stop", route="algo")
    account_a.submit(VenueWork(
        work_id="protect-a-after-418", action=Action.PROTECT, operation=Operation.SUBMIT_STOP,
        payload=StopOrder(ref=a_stop_ref, position_side="LONG", qty=Decimal("0.001"),
                          stop_price=Decimal("50000")),
        enqueued_at_ms=_START, deadline_ms=_START + 1_000,
    ))
    a_latched = account_a.run_next()
    b_after_a = account_b.snapshot()

    clock_b.advance(_DISPATCH_OFFSET_MS)
    b_result = account_b.run_next()
    b_completed = clock_b() - _START
    a_snapshot, b_snapshot = account_a.snapshot(), account_b.snapshot()
    journal_isolated = (
        all((row.account_id, row.egress_id) == (_ACCOUNT_A, _EGRESS_A) for row in journal_a.records)
        and all((row.account_id, row.egress_id) == (_ACCOUNT_B, _EGRESS_B) for row in journal_b.records)
        and not journal_b.records
    )
    venue_isolated = tuple(venue_a.calls) == ("query",) and tuple(venue_b.calls) == ("submit_stop",)
    body = IsolationArtifactBody(
        SCHEMA, head_sha, host_id, HARNESS_VERSION, _binding_digest(), tuple(trace),
        WorkState.UNKNOWN if a_exception is None else a_exception.state,
        WorkState.UNKNOWN if a_latched is None else a_latched.state,
        a_snapshot.reconcile_before_restart, len(journal_a.records), len(journal_b.records),
        b_snapshot.budget.state,
        WorkState.UNKNOWN if b_result is None else b_result.state,
        0, b_completed, _DEADLINE_OFFSET_MS, distinct, b_after_a == b_baseline,
        journal_isolated, venue_isolated,
    )
    return IsolationArtifact(body, hashlib.sha256(canonical_bytes(body)).hexdigest())


def encode_artifact(value: IsolationArtifact) -> bytes:
    _exact(value, IsolationArtifact, "validation.envelope")
    _exact(value.body, IsolationArtifactBody, "validation.envelope.body")
    trace = tuple(FaultObservation(*(getattr(row, field.name) for field in fields(FaultObservation)))
                  for row in value.body.fault_trace)
    values = [getattr(value.body, field.name) for field in fields(IsolationArtifactBody)]
    values[5] = trace
    body = IsolationArtifactBody(*values)
    sealed = IsolationArtifact(body, value.artifact_sha256)
    envelope = {"artifact_sha256": sealed.artifact_sha256,
                "body": json.loads(canonical_bytes(sealed.body))}
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("validation.json.duplicate")
        result[key] = value
    return result


def _keys(value: object, expected: set[str], code: str) -> dict[str, object]:
    row = _exact(value, dict, code)
    if set(row) != expected or any(type(key) is not str for key in row):
        _fail(code)
    return row


def decode_artifact(data: bytes) -> IsolationArtifact:
    if type(data) is not bytes:
        _fail("validation.json.bytes")
    raw = None
    try:
        raw = json.loads(data.decode("ascii"), object_pairs_hook=_pairs,
                         parse_constant=lambda _value: _fail("validation.json.constant"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    if raw is None:
        _fail("validation.json.syntax")
    envelope = _keys(raw, {"artifact_sha256", "body"}, "validation.envelope.keys")
    names = {field.name for field in fields(IsolationArtifactBody)}
    body = _keys(envelope["body"], names, "validation.artifact.keys")
    if type(body["fault_trace"]) is not list:
        _fail("validation.artifact.trace")
    trace = []
    for value in body["fault_trace"]:
        row = _keys(value, {field.name for field in fields(FaultObservation)}, "validation.fault.keys")
        try:
            trace.append(FaultObservation(
                row["sequence"], FaultKind(row["kind"]), RateBlockState(row["account_a_budget_state"]),
                row["account_b_queued"],
            ))
        except (TypeError, ValueError):
            _fail("validation.fault.value")
    try:
        rebuilt = IsolationArtifactBody(
            body["schema"], body["head_sha"], body["host_id"], body["harness_version"],
            body["bindings_sha256"], tuple(trace), WorkState(body["account_a_exception_state"]),
            WorkState(body["account_a_latched_418_state"]),
            body["account_a_reconcile_before_restart"], body["account_a_journal_records"],
            body["account_b_journal_records"], RateBlockState(body["account_b_budget_state"]),
            WorkState(body["account_b_protection_state"]), body["account_b_enqueued_offset_ms"],
            body["account_b_completed_offset_ms"], body["account_b_deadline_offset_ms"],
            body["distinct_egress"], body["no_cross_account_mutation"],
            body["journal_isolated"], body["venue_isolated"],
        )
    except (TypeError, ValueError):
        _fail("validation.artifact.value")
    artifact = IsolationArtifact(rebuilt, envelope["artifact_sha256"])
    if encode_artifact(artifact) != data:
        _fail("validation.json.canonical")
    return artifact


def gate_proof(value: IsolationArtifact) -> GateProof:
    artifact = decode_artifact(encode_artifact(value))
    rerun = IsolationArtifact(artifact.body,
                              hashlib.sha256(canonical_bytes(artifact.body)).hexdigest())
    if rerun.artifact_sha256 != artifact.artifact_sha256:
        _fail("validation.envelope.digest")
    return GateProof(ProofKind.TWO_ACCOUNT_ISOLATION, artifact.body.head_sha,
                     artifact.body.host_id, True, artifact.artifact_sha256)


def derive_host_id() -> str:
    if sys.platform != "win32":
        _fail("host_unsupported")
    raw = "\0".join((platform.node(), platform.machine(), platform.platform())).encode("utf-8")
    return "host_" + hashlib.sha256(raw).hexdigest()[:32]


def _git_state(repo: Path) -> tuple[str, bool]:
    try:
        head = subprocess.run(("git", "rev-parse", "HEAD"), cwd=repo, capture_output=True,
                              text=True, check=True, timeout=10).stdout.strip()
        dirty = bool(subprocess.run(
            ("git", "status", "--porcelain", "--untracked-files=no"), cwd=repo,
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        _fail("git_state_unavailable")
    return _sha(head, "git_head_invalid"), dirty


def _validate_identity(expected_head: str, expected_host_id: str, actual_head: str,
                       actual_host_id: str, dirty: bool) -> None:
    _sha(expected_head, "expected_head_invalid")
    _host(expected_host_id, "expected_host_invalid")
    if actual_head != expected_head:
        _fail("head_mismatch")
    if actual_host_id != expected_host_id:
        _fail("host_mismatch")
    if type(dirty) is not bool or dirty:
        _fail("worktree_dirty")


def collect_two_account_isolation(*, expected_head: str, expected_host_id: str,
                                  repo: Path = ROOT) -> IsolationArtifact:
    result = None
    failure = None
    try:
        head, dirty = _git_state(repo)
        _validate_identity(expected_head, expected_host_id, head, derive_host_id(), dirty)
        result = run_two_account_isolation(head_sha=expected_head, host_id=expected_host_id)
        final_head, final_dirty = _git_state(repo)
        _validate_identity(expected_head, expected_host_id, final_head, derive_host_id(), final_dirty)
    except KeyboardInterrupt:
        failure = ProofError("collection_interrupted")
    except ProofError as error:
        failure = ProofError(error.code)
    except Exception:
        failure = ProofError("collection_failed")
    if failure is not None:
        raise failure from None
    if result is None:
        raise ProofError("collection_failed")
    return result


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-host-id", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        artifact = collect_two_account_isolation(
            expected_head=args.expected_head, expected_host_id=args.expected_host_id)
        output = Path(args.out) if args.out else (
            ROOT / "dev_out" / "base_clean" / f"two_account_isolation_{artifact.body.head_sha}.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as handle:
            handle.write(encode_artifact(artifact))
        print(artifact.artifact_sha256)
        return 0
    except Exception as error:
        code = error.code if type(error) is ProofError else "artifact_write_failed"
        print(code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
