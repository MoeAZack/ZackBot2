from __future__ import annotations

import ast
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from newcore.domain.account import (
    Account,
    AccountBinding,
    BindingConfirmation,
    BindingState,
    Environment,
    Venue,
    confirmation_phrase,
)
from newcore.domain.orders import Side
from newcore.testnet_harness import (
    CLEANUP_FLATTEN_MS,
    CLEANUP_HOLD_MS,
    CLEANUP_PROTECT_MS,
    MAX_SCENARIO_MS,
    BindingEvidence,
    CaseStatus,
    CleanupContext,
    CleanupEvidence,
    FailureCode,
    FinalTruth,
    HarnessBinding,
    HarnessError,
    RateLimitEvidence,
    RateLimitKind,
    ScenarioCase,
    ScenarioContext,
    ScenarioEvidence,
    ScenarioKind,
    SetupEvidence,
    SuiteManifest,
    SuiteStatus,
    canonical_report_bytes,
    run_suite,
)


T0 = 2_000_000_000_000
DIGEST = "1234567890abcdef"
ACCOUNT_ID = "acct_0123456789abcdef0123456789abcdef"
BOOT_ID = "boot_0123456789abcdef0123456789abcdef"
BUILD_SHA = "a" * 40


def _account() -> Account:
    binding = AccountBinding(
        venue=Venue.BINANCE_USDM,
        environment=Environment.TESTNET,
        settlement_asset="USDT",
        key_digest=DIGEST,
        exchange_uid="tnet-owner",
    )
    confirmation = BindingConfirmation(
        account_id=ACCOUNT_ID,
        old_key_digest=None,
        new_key_digest=DIGEST,
        typed_phrase=confirmation_phrase(ACCOUNT_ID, DIGEST),
        confirmed_at_ms=T0 - 1,
    )
    return Account(
        account_id=ACCOUNT_ID,
        label="Dedicated TNET-01",
        hedge_mode=False,
        binding=binding,
        binding_state=BindingState.CONFIRMED,
        proposed_binding=None,
        confirmation=confirmation,
    )


def _manifest(*, symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")) -> SuiteManifest:
    binding = HarnessBinding(_account(), True, symbols)
    cases = tuple(
        ScenarioCase(
            f"case_{index:032x}",
            kind,
            symbols[index % len(symbols)],
            Side.SHORT if kind in (ScenarioKind.SHORT_TARGET, ScenarioKind.SHORT_STOP) else Side.LONG,
            3,
            Decimal("100"),
            12,
        )
        for index, kind in enumerate(ScenarioKind, 1)
    )
    return SuiteManifest(BUILD_SHA, T0, BOOT_ID, binding, cases)


class FakeAdapter:
    def __init__(self) -> None:
        self.contexts: list[ScenarioContext] = []
        self.cleanup_contexts: list[CleanupContext] = []
        self.override_kind: ScenarioKind | None = None
        self.evidence_changes: dict[str, object] = {}
        self.cleanup_changes: dict[str, object] = {}
        self.binding_changes: dict[str, object] = {}
        self.setup_changes: dict[str, object] = {}
        self.raise_at: str | None = None
        self.mismatch_cleanup = False

    def confirm_binding(self, expected: HarnessBinding) -> BindingEvidence:
        if self.raise_at == "binding":
            raise RuntimeError("api-key-secret https://host.invalid")
        account = expected.account
        evidence = BindingEvidence(
            account.account_id,
            account.binding.key_digest,
            account.binding.exchange_uid,
            Venue.BINANCE_USDM,
            Environment.TESTNET,
            "USDT",
            True,
            T0,
        )
        return replace(evidence, **self.binding_changes)

    def setup(self, context: ScenarioContext) -> SetupEvidence:
        self.contexts.append(context)
        if self.raise_at == "setup":
            raise RuntimeError("setup-secret")
        evidence = SetupEvidence(
            context.case.case_id,
            context.case_started_budget_ms + 1,
            context.binding_digest,
            True,
            FinalTruth.FLAT,
        )
        return replace(evidence, **self.setup_changes)

    def execute(self, context: ScenarioContext) -> ScenarioEvidence:
        if self.raise_at == "execute":
            raise RuntimeError("raw exchange payload secret")
        kind = context.case.kind
        target = kind in (ScenarioKind.LONG_TARGET, ScenarioKind.SHORT_TARGET)
        stop = kind in (ScenarioKind.LONG_STOP, ScenarioKind.SHORT_STOP)
        partial = kind is ScenarioKind.PARTIAL_CLOSE
        dca = kind is ScenarioKind.BOUNDED_DCA
        cancel = kind is ScenarioKind.CANCEL_REPLACE_RACE
        lost = kind is ScenarioKind.LOST_ANSWER_RECONCILE
        refusal = kind is ScenarioKind.MINIMUM_REFUSAL
        restart = kind is ScenarioKind.RESTART_OPEN_POSITION
        rate_429 = kind is ScenarioKind.RATE_LIMIT_429
        rate_418 = kind is ScenarioKind.IP_BAN_418
        started = context.case_started_budget_ms + 2
        rate_limit = None
        if rate_429:
            rate_limit = RateLimitEvidence(
                RateLimitKind.HTTP_429, started + 10, started + 110, True, True, False, started + 110
            )
        elif rate_418:
            rate_limit = RateLimitEvidence(
                RateLimitKind.HTTP_418, started + 10, started + 110, True, True, True, None
            )
        evidence = ScenarioEvidence(
            context.case.case_id,
            self.override_kind or kind,
            started,
            started + 1_000,
            context.synthetic_intent_id,
            context.authorization_id,
            True,
            2,
            Decimal("100"),
            Decimal("200"),
            3,
            not refusal,
            target or stop or partial or dca or restart,
            target,
            stop,
            partial,
            1 if dca else 0,
            cancel,
            refusal,
            started + 50 if lost else None,
            started + 50 if lost else None,
            True,
            restart,
            context.restart_boot_id if restart else None,
            rate_limit,
            FinalTruth.FLAT,
        )
        return replace(evidence, **self.evidence_changes)

    def cleanup(self, context: CleanupContext) -> CleanupEvidence:
        self.cleanup_contexts.append(context)
        if self.raise_at == "cleanup":
            raise RuntimeError("cleanup-secret")
        evidence = CleanupEvidence(
            context.token,
            context.started_ms,
            None,
            context.started_ms + 1_000,
            None,
            FinalTruth.FLAT,
            True,
            1,
        )
        evidence = replace(evidence, **self.cleanup_changes)
        if self.mismatch_cleanup and len(self.cleanup_contexts) % 2 == 0:
            evidence = replace(evidence, cleanup_orders_total=evidence.cleanup_orders_total + 1)
        return evidence


def _run(adapter: FakeAdapter | None = None):
    adapter = adapter or FakeAdapter()
    written: list[bytes] = []
    report = run_suite(_manifest(), adapter, written.append)
    return report, adapter, written


def test_complete_matrix_passes_and_writer_gets_only_canonical_redacted_json() -> None:
    report, adapter, written = _run()
    assert report.status is SuiteStatus.PASSED
    assert [case.kind for case in report.cases] == list(ScenarioKind)
    assert all(case.status is CaseStatus.PASSED for case in report.cases)
    assert written == [canonical_report_bytes(report)]
    decoded = json.loads(written[0])
    assert decoded["schema"] == "zackbot-tnet01/1"
    raw = written[0].decode("ascii")
    for forbidden in ("tnet-owner", "Dedicated TNET-01", "secret", "https://", "api_key"):
        assert forbidden not in raw
    assert len(adapter.contexts) == 12


def test_contexts_bind_deterministic_nc06_ids_lost_answer_and_restart_identity() -> None:
    first, adapter, _ = _run()
    second, adapter_two, _ = _run()
    assert canonical_report_bytes(first) == canonical_report_bytes(second)
    assert adapter.contexts == adapter_two.contexts
    lost = next(c for c in adapter.contexts if c.case.kind is ScenarioKind.LOST_ANSWER_RECONCILE)
    restart = next(c for c in adapter.contexts if c.case.kind is ScenarioKind.RESTART_OPEN_POSITION)
    assert lost.drop_answer_after_accepted
    assert lost.synthetic_intent_id.startswith("int_")
    assert lost.authorization_id.startswith("dec_")
    assert restart.restart_boot_id != restart.boot_id


def test_rate_limit_scenarios_carry_closed_typed_429_and_418_evidence() -> None:
    report, adapter, _ = _run()
    assert report.status is SuiteStatus.PASSED
    rate_contexts = [
        context for context in adapter.contexts
        if context.case.kind in (ScenarioKind.RATE_LIMIT_429, ScenarioKind.IP_BAN_418)
    ]
    assert [context.case.kind for context in rate_contexts] == [
        ScenarioKind.RATE_LIMIT_429,
        ScenarioKind.IP_BAN_418,
    ]


@pytest.mark.parametrize(
    ("case_kind", "wrong_evidence"),
    [
        (
            ScenarioKind.RATE_LIMIT_429,
            RateLimitEvidence(RateLimitKind.HTTP_418, T0 + 10, T0 + 20, True, True, True, None),
        ),
        (
            ScenarioKind.IP_BAN_418,
            RateLimitEvidence(RateLimitKind.HTTP_429, T0 + 10, T0 + 20, True, True, False, T0 + 20),
        ),
    ],
)
def test_rate_limit_scenarios_reject_the_other_typed_status(
    case_kind: ScenarioKind, wrong_evidence: RateLimitEvidence
) -> None:
    adapter = FakeAdapter()
    original = adapter.execute

    def execute(context: ScenarioContext) -> ScenarioEvidence:
        evidence = original(context)
        return replace(evidence, rate_limit=wrong_evidence) if context.case.kind is case_kind else evidence

    adapter.execute = execute  # type: ignore[method-assign]
    report, _, _ = _run(adapter)
    failed = next(case for case in report.cases if case.kind is case_kind)
    assert failed.failure is FailureCode.ASSERTION


def test_cleanup_is_repeated_with_one_idempotency_token_per_case() -> None:
    _, adapter, _ = _run()
    assert len(adapter.cleanup_contexts) == 24
    pairs = list(zip(adapter.cleanup_contexts[::2], adapter.cleanup_contexts[1::2], strict=True))
    assert all(left == right for left, right in pairs)
    assert len({left.token for left, _ in pairs}) == 12


@pytest.mark.parametrize(
    ("kind", "changes"),
    [
        (ScenarioKind.LONG_TARGET, {"target_confirmed": False}),
        (ScenarioKind.LONG_STOP, {"stop_confirmed": False}),
        (ScenarioKind.SHORT_TARGET, {"target_confirmed": False}),
        (ScenarioKind.SHORT_STOP, {"stop_confirmed": False}),
        (ScenarioKind.PARTIAL_CLOSE, {"partial_close_confirmed": False}),
        (ScenarioKind.BOUNDED_DCA, {"dca_adds": 2}),
        (ScenarioKind.CANCEL_REPLACE_RACE, {"cancel_replace_confirmed": False}),
        (ScenarioKind.LOST_ANSWER_RECONCILE, {"answer_lost_at_ms": T0 + 999}),
        (ScenarioKind.MINIMUM_REFUSAL, {"minimum_refused": False}),
        (ScenarioKind.RESTART_OPEN_POSITION, {"journal_reopened": False}),
        (ScenarioKind.RATE_LIMIT_429, {"rate_limit": None}),
        (ScenarioKind.IP_BAN_418, {"rate_limit": None}),
    ],
)
def test_each_frozen_scenario_has_a_closed_assertion(kind: ScenarioKind, changes: dict[str, object]) -> None:
    adapter = FakeAdapter()
    original = adapter.execute

    def execute(context: ScenarioContext) -> ScenarioEvidence:
        evidence = original(context)
        return replace(evidence, **changes) if context.case.kind is kind else evidence

    adapter.execute = execute  # type: ignore[method-assign]
    report, _, _ = _run(adapter)
    failed = next(case for case in report.cases if case.kind is kind)
    assert failed.failure is FailureCode.ASSERTION


@pytest.mark.parametrize(
    "change",
    [
        {"nc06_authorized": False},
        {"synthetic_intent_id": "int_ffffffffffffffffffffffffffffffff"},
        {"authorization_id": "dec_ffffffffffffffffffffffffffffffff"},
    ],
)
def test_execution_must_bind_exact_nc06_authorization(change: dict[str, object]) -> None:
    adapter = FakeAdapter()
    adapter.evidence_changes = change
    report, _, _ = _run(adapter)
    assert report.cases[0].failure is FailureCode.ASSERTION


@pytest.mark.parametrize(
    "change",
    [
        {"orders_placed": 13},
        {"peak_notional": Decimal("100.01")},
        {"aggregate_peak_notional": Decimal("2000.01")},
        {"max_leverage": 4},
    ],
)
def test_runtime_evidence_cannot_exceed_any_frozen_bound(change: dict[str, object]) -> None:
    adapter = FakeAdapter()
    adapter.evidence_changes = change
    report, _, _ = _run(adapter)
    assert report.cases[0].failure is FailureCode.BOUNDS


def test_case_deadline_is_ten_minutes() -> None:
    adapter = FakeAdapter()
    original = adapter.execute

    def late(context: ScenarioContext) -> ScenarioEvidence:
        return replace(original(context), finished_ms=context.case_started_budget_ms + MAX_SCENARIO_MS + 1)

    adapter.execute = late  # type: ignore[method-assign]
    report, _, _ = _run(adapter)
    assert report.cases[0].failure is FailureCode.DEADLINE


def test_suite_stops_at_sixty_minute_budget() -> None:
    adapter = FakeAdapter()
    original_execute = adapter.execute

    def slow_execute(context: ScenarioContext) -> ScenarioEvidence:
        evidence = original_execute(context)
        return replace(evidence, finished_ms=context.case_started_budget_ms + MAX_SCENARIO_MS - 1)

    adapter.execute = slow_execute  # type: ignore[method-assign]
    report, _, _ = _run(adapter)
    assert report.status is SuiteStatus.FAILED
    assert report.failure is FailureCode.SUITE_DEADLINE
    assert report.finished_ms > T0 + 60 * 60 * 1000


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"flattened_at_ms": T0 + 10_000_000}, FailureCode.CLEANUP),
        ({"cleanup_orders_total": 11}, FailureCode.BOUNDS),
    ],
)
def test_cleanup_deadline_and_total_order_ceiling_fail_closed(
    changes: dict[str, object], expected: FailureCode
) -> None:
    adapter = FakeAdapter()
    adapter.cleanup_changes = changes
    report, _, _ = _run(adapter)
    assert report.cases[0].failure is expected


def test_cleanup_protect_flatten_and_hold_deadlines_are_exact() -> None:
    protected = FakeAdapter()

    def protect(context: CleanupContext) -> CleanupEvidence:
        return CleanupEvidence(
            context.token,
            context.started_ms,
            context.started_ms + CLEANUP_PROTECT_MS,
            None,
            None,
            FinalTruth.PROTECTED_RECONCILED,
            True,
            1,
        )

    protected.cleanup = protect  # type: ignore[method-assign]
    report, _, _ = _run(protected)
    assert report.status is SuiteStatus.PASSED

    flattened = FakeAdapter()

    def flatten(context: CleanupContext) -> CleanupEvidence:
        return CleanupEvidence(
            context.token,
            context.started_ms,
            None,
            context.started_ms + CLEANUP_FLATTEN_MS,
            None,
            FinalTruth.FLAT,
            True,
            1,
        )

    flattened.cleanup = flatten  # type: ignore[method-assign]
    flat_report, _, _ = _run(flattened)
    assert flat_report.status is SuiteStatus.PASSED

    hold = FakeAdapter()

    def stop_hold(context: CleanupContext) -> CleanupEvidence:
        return CleanupEvidence(
            context.token,
            context.started_ms,
            None,
            None,
            context.started_ms + CLEANUP_HOLD_MS,
            FinalTruth.HOLD,
            False,
            0,
        )

    hold.cleanup = stop_hold  # type: ignore[method-assign]
    hold_report, _, _ = _run(hold)
    assert hold_report.status is SuiteStatus.HOLD
    assert hold_report.cases[0].failure is FailureCode.CLEANUP_HOLD


def test_non_idempotent_cleanup_fails() -> None:
    adapter = FakeAdapter()
    adapter.mismatch_cleanup = True
    report, _, _ = _run(adapter)
    assert report.cases[0].failure is FailureCode.CLEANUP_NOT_IDEMPOTENT
    assert report.cases[0].status is CaseStatus.HOLD


def _forged_cleanup(context: CleanupContext, **changes: object) -> CleanupEvidence:
    valid = CleanupEvidence(
        context.token,
        context.started_ms,
        None,
        context.started_ms + 1_000,
        None,
        FinalTruth.FLAT,
        True,
        1,
    )
    forged = object.__new__(CleanupEvidence)
    for name in valid.__dataclass_fields__:
        object.__setattr__(forged, name, changes.get(name, getattr(valid, name)))
    return forged


def test_contradictory_cleanup_terminal_evidence_fails_to_hold() -> None:
    adapter = FakeAdapter()

    def contradictory(context: CleanupContext) -> CleanupEvidence:
        return _forged_cleanup(context, protected_at_ms=context.started_ms + 500)

    adapter.cleanup = contradictory  # type: ignore[method-assign]
    report, _, _ = _run(adapter)
    assert report.status is SuiteStatus.HOLD
    assert report.cases[0].status is CaseStatus.HOLD
    assert report.cases[0].final_truth is FinalTruth.HOLD


def test_unreconciled_flat_cleanup_evidence_fails_to_hold() -> None:
    adapter = FakeAdapter()

    def unreconciled(context: CleanupContext) -> CleanupEvidence:
        return _forged_cleanup(context, reconciled=False)

    adapter.cleanup = unreconciled  # type: ignore[method-assign]
    report, _, _ = _run(adapter)
    assert report.status is SuiteStatus.HOLD
    assert report.cases[0].status is CaseStatus.HOLD
    assert report.cases[0].final_truth is FinalTruth.HOLD


def test_unreconciled_flat_scenario_evidence_fails_to_hold_even_if_cleanup_flattens() -> None:
    adapter = FakeAdapter()
    adapter.evidence_changes = {"reconciled": False}
    report, _, _ = _run(adapter)
    assert report.status is SuiteStatus.HOLD
    assert report.cases[0].failure is FailureCode.EVIDENCE_HOLD
    assert report.cases[0].final_truth is FinalTruth.HOLD


@pytest.mark.parametrize("stage", ["binding", "setup", "execute", "cleanup"])
def test_raw_adapter_exceptions_are_sanitized_and_cleanup_is_attempted(stage: str) -> None:
    adapter = FakeAdapter()
    adapter.raise_at = stage
    report, adapter, written = _run(adapter)
    raw = written[0].decode("ascii")
    assert report.status is not SuiteStatus.PASSED
    assert "secret" not in raw and "https://" not in raw
    if stage != "binding":
        assert adapter.cleanup_contexts


@pytest.mark.parametrize(
    "changes",
    [
        {"exchange_uid": "other-owner"},
        {"binding_digest": "ffffffffffffffff"},
        {"venue": object()},
        {"environment": Environment.MAINNET},
        {"settlement_asset": "USDC"},
        {"isolated": False},
    ],
)
def test_exact_binding_mismatch_stops_before_setup(changes: dict[str, object]) -> None:
    adapter = FakeAdapter()
    adapter.binding_changes = changes
    report, adapter, _ = _run(adapter)
    assert report.failure is FailureCode.BINDING
    assert not adapter.contexts and not adapter.cleanup_contexts


def test_binding_confirmation_from_before_suite_start_cannot_reach_setup() -> None:
    adapter = FakeAdapter()
    adapter.binding_changes = {"confirmed_at_ms": T0 - 1}
    report, adapter, _ = _run(adapter)
    assert report.failure is FailureCode.BINDING
    assert not adapter.contexts and not adapter.cleanup_contexts


def test_setup_requires_reconciled_flat_exact_binding() -> None:
    adapter = FakeAdapter()
    adapter.setup_changes = {"reconciled": False}
    executed = False

    def unsafe_execute(_: ScenarioContext) -> ScenarioEvidence:
        nonlocal executed
        executed = True
        raise AssertionError("execution must not start")

    adapter.execute = unsafe_execute  # type: ignore[method-assign]
    report, _, _ = _run(adapter)
    assert report.cases[0].failure is FailureCode.SETUP
    assert not executed


@pytest.mark.parametrize("field", ["leverage", "max_orders"])
def test_boolean_never_satisfies_manifest_integer_fields(field: str) -> None:
    values = {
        "case_id": "case_" + "1" * 32,
        "kind": ScenarioKind.LONG_TARGET,
        "symbol": "BTCUSDT",
        "side": Side.LONG,
        "leverage": 3,
        "max_notional": Decimal("100"),
        "max_orders": 12,
    }
    values[field] = True
    with pytest.raises(HarnessError):
        ScenarioCase(**values)


def test_writer_failure_is_stable_and_suppresses_raw_cause() -> None:
    def writer(_: bytes) -> None:
        raise RuntimeError("writer path and secret")

    with pytest.raises(HarnessError, match="^writer_failed$") as caught:
        run_suite(_manifest(), FakeAdapter(), writer)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "factory",
    [
        lambda: HarnessBinding(_account(), True, ("BTCUSDT", "ETHUSDT", "BNBUSDT")),
        lambda: ScenarioCase("case_" + "1" * 32, ScenarioKind.LONG_TARGET, "BTCUSDT", Side.LONG, 4,
                             Decimal("100"), 12),
        lambda: ScenarioCase("case_" + "1" * 32, ScenarioKind.LONG_TARGET, "BTCUSDT", Side.LONG, 3,
                             Decimal("1000.01"), 12),
        lambda: ScenarioCase("case_" + "1" * 32, ScenarioKind.LONG_TARGET, "BTCUSDT", Side.LONG, 3,
                             Decimal("100"), 13),
    ],
)
def test_manifest_bounds_are_constructor_enforced(factory) -> None:
    with pytest.raises(HarnessError):
        factory()


def test_manifest_requires_full_ordered_matrix_and_exact_types() -> None:
    manifest = _manifest()
    with pytest.raises(HarnessError, match="validation.manifest.matrix"):
        SuiteManifest(manifest.build_sha, manifest.suite_started_ms, manifest.initial_boot_id,
                      manifest.binding, tuple(reversed(manifest.cases)))
    forged = object.__new__(SuiteManifest)
    object.__setattr__(forged, "build_sha", manifest.build_sha)
    object.__setattr__(forged, "suite_started_ms", manifest.suite_started_ms)
    object.__setattr__(forged, "initial_boot_id", manifest.initial_boot_id)
    object.__setattr__(forged, "binding", manifest.binding)
    object.__setattr__(forged, "cases", list(manifest.cases))
    with pytest.raises(HarnessError):
        run_suite(forged, FakeAdapter(), lambda _: None)


def test_report_is_owned_and_caller_cannot_mutate_canonical_bytes() -> None:
    report, _, _ = _run()
    before = canonical_report_bytes(report)
    with pytest.raises((AttributeError, TypeError)):
        report.cases[0].status = CaseStatus.FAILED  # type: ignore[misc]
    assert canonical_report_bytes(report) == before


def test_module_has_no_effectful_import_or_direct_io_surface() -> None:
    source_path = Path(__file__).parents[2] / "newcore" / "testnet_harness.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    forbidden = {"pathlib", "socket", "subprocess", "requests", "urllib", "httpx", "aiohttp", "threading", "time"}
    imported = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not (imported & forbidden)
    assert not ({"open", "exec", "eval", "compile", "__import__"} & calls)
