"""TNET-02 target/stop scenarios: regressions from the second real testnet run (2026-11-06, head 7636dfa).

- partial_close: the BTCUSDT long filled at about 76014.00. Its target was 0.05% above the fill (about 76052.01).
  The mark peaked at 76041.06 (+0.036%), so the wait timed out.
- short_stop: the ETHUSDT short filled at about 2241.01, with the stop at 2242.14. The mark peaked at 2241.95, so the
  stop never triggered.
- restart_open_position: the case failed as "adapter" with no recorded cause.

The fixes:
- targets follow the mark they are compared with, as the DCA level already did (§14);
- the default trigger offset is 0.03%;
- trigger waits run up to the case deadline minus a margin;
- any adapter error code is recorded in the diagnostics notes.

The pass conditions are unchanged: a real trigger, a real fill, and a flat result.
"""
from __future__ import annotations

from decimal import Decimal

from newcore.testnet_harness import CaseStatus, FailureCode, FinalTruth, ScenarioKind, run_suite
from newcore.tnet02.adapter import HarnessConfig

from .support import manifest, rig

BASE = Decimal("60000")


def _case(report, kind):
    return next(c for c in report.cases if c.kind is kind)


def _move_while_held(fake, *, side: str, move: Decimal, hold_ms: int = 30_000, period_ms: int = 120_000,
                     width_ms: int = 4_000):
    """Mark sits at BASE. Once a BTCUSDT position on ``side`` has been held for ``hold_ms``, the mark moves by
    ``move`` (a signed fraction) for ``width_ms`` in each ``period_ms``."""
    seen: dict[str, int] = {}

    def path(now: int, symbol: str) -> Decimal:
        if symbol != "BTCUSDT":
            return Decimal("3000")
        if fake.positions.get(("BTCUSDT", side), Decimal("0")) <= 0:
            seen.pop("since", None)
            return BASE
        held = now - seen.setdefault("since", now)
        if held >= hold_ms and (held - hold_ms) % period_ms < width_ms:
            return (BASE * (1 + move)).quantize(Decimal("0.1"))
        return BASE
    return path


def _rig(tmp_path, *, side: str, move: str, fill_offset: str = "0", config: HarnessConfig | None = None):
    r = rig(tmp_path, price_path=lambda now, symbol: BASE if symbol == "BTCUSDT" else Decimal("3000"),
            config=config or HarnessConfig(poll_ms=1000))
    r.fake.price_path = _move_while_held(r.fake, side=side, move=Decimal(move))
    r.fake.fill_offset = Decimal(fill_offset)
    r.fake.tick()
    return r


def test_partial_close_target_follows_the_mark_not_a_fill_above_it(tmp_path):
    # real shape: the taker buy fills above mark, and the mark only rises 0.036%
    r = _rig(tmp_path, side="LONG", move="0.00036", fill_offset="12")
    report = run_suite(manifest(), r.adapter, [].append)
    for kind in (ScenarioKind.LONG_TARGET, ScenarioKind.PARTIAL_CLOSE):
        case = _case(report, kind)
        assert case.status is CaseStatus.PASSED, (kind, case.failure, r.adapter.notes)
        assert case.final_truth is FinalTruth.FLAT


def test_old_fill_based_target_with_the_old_offset_would_have_missed(tmp_path):
    # the same market with the 6 Nov 0.05% offset still fails honestly: the market must really reach the level
    r = _rig(tmp_path, side="LONG", move="0.00036", fill_offset="12",
             config=HarnessConfig(poll_ms=1000, trigger_offset=Decimal("0.0005")))
    report = run_suite(manifest(), r.adapter, [].append)
    case = _case(report, ScenarioKind.PARTIAL_CLOSE)
    assert case.status is CaseStatus.FAILED and case.failure is FailureCode.ASSERTION
    note = next(n for n in r.adapter.notes if n["kind"] == "partial_close")
    assert note["reason"] == "target.no_trigger" and Decimal(note["mark_high"]) < Decimal(note["target"])


def test_target_never_sits_at_or_below_the_fill(tmp_path):
    r = rig(tmp_path, config=HarnessConfig(trigger_offset=Decimal("0.0003")))
    from types import SimpleNamespace
    from newcore.domain.orders import Side
    case = SimpleNamespace(side=Side.LONG, marks=[Decimal("60000")], symbol="BTCUSDT")
    r.adapter._mark = lambda c: c.marks[-1]
    r.adapter._filters_for = lambda c: SimpleNamespace(tick_size=Decimal("0.1"))
    assert r.adapter._target_price(case, Decimal("60100")) == Decimal("60100.1")    # clamp above the fill
    assert r.adapter._target_price(case, Decimal("59990")) == Decimal("60018.0")    # 0.03% above the mark
    short = SimpleNamespace(side=Side.SHORT, marks=[Decimal("60000")], symbol="BTCUSDT")
    assert r.adapter._target_price(short, Decimal("59900")) == Decimal("59899.9")
    assert r.adapter._target_price(short, Decimal("60010")) == Decimal("59982.0")


def test_short_stop_triggers_on_a_0_04_percent_adverse_mark_move(tmp_path):
    # real shape: the mark rose 0.042% above the short entry, which was short of the old 0.05% stop
    r = _rig(tmp_path, side="SHORT", move="0.00042")
    report = run_suite(manifest(), r.adapter, [].append)
    case = _case(report, ScenarioKind.SHORT_STOP)
    assert case.status is CaseStatus.PASSED, (case.failure, r.adapter.notes)
    assert case.final_truth is FinalTruth.FLAT


def test_stop_that_does_not_trigger_records_why(tmp_path):
    r = _rig(tmp_path, side="SHORT", move="0.0001")
    report = run_suite(manifest(), r.adapter, [].append)
    case = _case(report, ScenarioKind.SHORT_STOP)
    assert case.status is CaseStatus.FAILED and case.failure is FailureCode.ASSERTION
    note = next(n for n in r.adapter.notes if n["kind"] == "short_stop")
    assert note["reason"] == "stop.no_trigger" and Decimal(note["mark_high"]) < Decimal(note["stop"])
    assert case.finished_ms - case.started_ms <= 9 * 60 * 1000 + 30_000
    assert note["polls"] > 500                                 # waited about 9 minutes, longer than the old 8


def test_adapter_error_code_is_recorded_for_the_diagnostics(tmp_path):
    r = rig(tmp_path)
    opener = r.adapter._journals
    real_open = opener.open

    def failing_open(case_id, boot_id, *, reopen=False):
        if reopen:
            raise OSError("disk gone C:\\secret\\path")
        return real_open(case_id, boot_id, reopen=reopen)
    opener.open = failing_open
    report = run_suite(manifest(), r.adapter, [].append)
    case = _case(report, ScenarioKind.RESTART_OPEN_POSITION)
    # untrusted local truth after a failed reopen: the case HOLDs (cleanup sends nothing), and the code is recorded
    assert case.status is CaseStatus.HOLD and case.failure is FailureCode.ADAPTER
    notes = [n for n in r.adapter.notes if n["kind"] == "restart_open_position"]
    assert [n["reason"] for n in notes] == ["adapter.restart.journal"]
    assert "secret" not in repr(r.adapter.notes)


def test_unexpected_exception_records_only_its_class(tmp_path):
    r = rig(tmp_path)

    def boom(case, flags):
        raise ValueError("venue text with a key=abc")
    r.adapter._run_minimum = boom
    report = run_suite(manifest(), r.adapter, [].append)
    assert _case(report, ScenarioKind.MINIMUM_REFUSAL).failure is FailureCode.ADAPTER
    notes = [n for n in r.adapter.notes if n["kind"] == "minimum_refusal"]
    assert [n["reason"] for n in notes] == ["error.ValueError"]
    assert "key=abc" not in repr(r.adapter.notes)


def test_one_unanswered_stop_query_before_the_truth_check_is_retried_not_fatal(tmp_path):
    from newcore.venue.binance_usdm_testnet import HttpResponse
    r = rig(tmp_path)
    real = r.fake._get_fapi_v1_algoOrder
    state = {"refreshing": False, "failed": 0}

    def flaky(params):
        if state["refreshing"] and state["failed"] == 0:
            state["failed"] += 1
            return HttpResponse(status=503, body=b"{}")
        return real(params)
    r.fake._get_fapi_v1_algoOrder = flaky
    refresh = r.adapter._refresh_stops

    def watched(case):
        state["refreshing"] = True
        try:
            return refresh(case)
        finally:
            state["refreshing"] = False
    r.adapter._refresh_stops = watched
    report = run_suite(manifest(), r.adapter, [].append)
    assert state["failed"] == 1                                # the truth check's stop read really failed once
    assert [c.status for c in report.cases] == [CaseStatus.PASSED] * 12


def test_stop_that_stays_unknown_still_holds(tmp_path):
    from newcore.venue.binance_usdm_testnet import HttpResponse
    r = rig(tmp_path)
    r.fake._get_fapi_v1_algoOrder = lambda params: HttpResponse(status=503, body=b"{}")
    report = run_suite(manifest(), r.adapter, [].append)
    # the stop can never be confirmed, so long_stop HOLDs, the suite stops there, and the cause is recorded
    case = _case(report, ScenarioKind.LONG_STOP)
    assert case.status is CaseStatus.HOLD and case.failure is FailureCode.ADAPTER
    reasons = [n["reason"] for n in r.adapter.notes if n["kind"] == "long_stop"]
    assert reasons[-1] == "adapter.refresh.stop_unknown"
    assert [c.kind for c in report.cases][-1] is ScenarioKind.LONG_STOP


# ------------------------------------------------------------------- account-facts reads (third real run, 777badeb)
# bounded_dca failed as adapter 346 s into its wait. The diagnostics note said only "adapter.read.account_facts":
# one failed read ended the case, and the port code that caused it was dropped.
from newcore.tnet02.ports import PortError  # noqa: E402


class _FlakyFacts:
    """Wraps the account port: fails with ``code`` on the calls whose numbers are in ``failing``, counted from 1."""

    def __init__(self, port, code, failing):
        self._port, self._code, self._failing, self.calls = port, code, set(failing), 0

    def facts(self, symbols):
        self.calls += 1
        if self.calls in self._failing:
            raise PortError(self._code)
        return self._port.facts(symbols)

    def __getattr__(self, name):
        return getattr(self._port, name)


def _wrap(r, code, failing):
    flaky = _FlakyFacts(r.adapter._account_port, code, failing)
    r.adapter._account_port = flaky
    return flaky


def test_one_transient_facts_failure_mid_case_is_retried(tmp_path):
    r = rig(tmp_path)
    _wrap(r, "account.mark_price", {40, 41})       # LONG/SHORT rows caught mid-update, twice in a row
    report = run_suite(manifest(), r.adapter, [].append)
    assert [c.status for c in report.cases] == [CaseStatus.PASSED] * 12
    assert not [n for n in r.adapter.notes if n["reason"].startswith("adapter.")]


def test_persistent_transient_failure_holds_after_the_bounded_retries_and_names_the_cause(tmp_path):
    r = rig(tmp_path, config=HarnessConfig(poll_ms=1000, settle_polls=4))
    flaky = _wrap(r, "reader.transport", set(range(40, 10_000)))
    report = run_suite(manifest(), r.adapter, [].append)
    failed = [c for c in report.cases if c.status is not CaseStatus.PASSED]
    assert failed and failed[0].failure is FailureCode.ADAPTER
    note = next(n for n in r.adapter.notes if n["reason"] == "adapter.read.account_facts")
    assert note["cause"] == "reader.transport"
    assert flaky.calls >= 40 + 3                    # the failing read was attempted settle_polls (4) times


def test_rate_refusal_mid_suite_holds_with_its_cause(tmp_path):
    r = rig(tmp_path)
    _wrap(r, "account.budget_refused", {40})
    report = run_suite(manifest(), r.adapter, [].append)
    note = next(n for n in r.adapter.notes if n["reason"] == "adapter.read.account_facts")
    assert note["cause"] == "account.budget_refused"
    assert any(c.failure is FailureCode.ADAPTER for c in report.cases)


def test_facts_retry_policy_directly(tmp_path):
    r = rig(tmp_path, config=HarnessConfig(poll_ms=1000, settle_polls=3))
    for code, expected_calls in (("reader.transport", 3), ("reader.http_status", 3), ("account.mark_price", 3),
                                 ("reader.malformed", 3), ("reader.response", 3),
                                 ("account.budget_refused", 1), ("reader.http_429", 1), ("reader.http_418", 1),
                                 ("account.symbol_missing", 1), ("reader.origin", 1)):
        flaky = _FlakyFacts(_AlwaysOk(), code, set(range(1, 100)))
        r.adapter._account_port = flaky
        before = r.clock.now
        try:
            r.adapter._facts(("BTCUSDT",))
        except Exception as error:   # noqa: BLE001
            assert error.code == "read.account_facts" and error.cause == code
        else:
            raise AssertionError(code)
        assert flaky.calls == expected_calls, code
        assert r.clock.now - before == (expected_calls - 1) * 1000, code     # waits poll_ms between attempts


def test_unexpected_exception_in_facts_records_only_its_class(tmp_path):
    r = rig(tmp_path)

    class Broken:
        def facts(self, symbols):
            raise RuntimeError("venue text api_key=abc")
    r.adapter._account_port = Broken()
    try:
        r.adapter._facts(("BTCUSDT",))
    except Exception as error:   # noqa: BLE001
        assert error.cause == "error.RuntimeError" and "abc" not in str(error)


class _AlwaysOk:
    def facts(self, symbols):
        raise AssertionError("never reached")


def test_adapter_failure_note_carries_the_level_and_cause(tmp_path):
    # mirrors the real DCA failure: a read fails while waiting for the add trigger
    r = rig(tmp_path, price_path=lambda now, symbol: BASE if symbol == "BTCUSDT" else Decimal("3000"),
            config=HarnessConfig(poll_ms=1000, settle_polls=2))
    seen = {"dca": False}
    real_run_dca = r.adapter._run_dca

    def run_dca(case, flags):
        seen["dca"] = True
        return real_run_dca(case, flags)
    r.adapter._run_dca = run_dca
    port = r.adapter._account_port

    class FailDuringDca:
        calls = 0

        def facts(self, symbols):
            if seen["dca"]:
                FailDuringDca.calls += 1
                if FailDuringDca.calls > 5:
                    raise PortError("reader.transport")
            return port.facts(symbols)

        def __getattr__(self, name):
            return getattr(port, name)
    r.adapter._account_port = FailDuringDca()
    report = run_suite(manifest(), r.adapter, [].append)
    assert _case(report, ScenarioKind.BOUNDED_DCA).failure is FailureCode.ADAPTER
    note = next(n for n in r.adapter.notes if n["kind"] == "bounded_dca")
    assert note["reason"] == "adapter.read.account_facts" and note["cause"] == "reader.transport"
    assert Decimal(note["level"]) < BASE
