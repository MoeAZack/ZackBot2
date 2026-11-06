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
