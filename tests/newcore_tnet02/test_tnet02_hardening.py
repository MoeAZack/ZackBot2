"""TNET-02 hardening for a real exchange: partial stop fills, cancel polling, uncoded cleanup errors, 429 timing."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from newcore.domain.orders import IntentState, Purpose, Side
from newcore.ports.venue import OutcomeKind, ReadKind
from newcore.testnet_harness import FinalTruth, ScenarioKind, SuiteStatus, run_suite
from newcore.tnet02.adapter import AdapterError, _Order
from newcore.tnet02.faults import FaultKind
from newcore.account_context import WorkState

from .support import manifest, rig
from .test_tnet02_adapter_guards import _bound, _cleanup_context, _posts


def _stop(qty: str) -> _Order:
    return _Order("int_" + "2" * 32, "zbn1a-" + "b" * 26, "algo", Purpose.PROTECT, "BTCUSDT", Side.LONG,
                  Decimal(qty), Decimal("50000"), IntentState.WORKING)


def _fills(*qtys: str):
    rows = [SimpleNamespace(position_side="LONG", qty=Decimal(q), price=Decimal("60000")) for q in qtys]
    return SimpleNamespace(state=WorkState.COMPLETED, value=SimpleNamespace(kind=ReadKind.OK, value=rows))


def test_partially_filled_triggered_stop_stays_working_until_the_child_finishes(tmp_path):
    r, _spec, _context = _bound(tmp_path)
    adapter, case = r.adapter, r.adapter._case
    case.position = Decimal("0.010")
    order = _stop("0.010")
    case.orders.append(order)
    trigger = SimpleNamespace(exchange_order_id="77")
    adapter._run = lambda *_a, **_k: _fills("0.004")
    adapter._absorb_trigger(case, order, trigger)
    assert order.state is IntentState.WORKING and case.position == Decimal("0.006")
    adapter._run = lambda *_a, **_k: _fills("0.004", "0.006")
    adapter._absorb_trigger(case, order, trigger)
    assert order.state is IntentState.FILLED and case.position == 0


def test_triggered_stop_larger_than_the_position_ends_when_the_position_is_gone(tmp_path):
    r, _spec, _context = _bound(tmp_path)
    adapter, case = r.adapter, r.adapter._case
    case.position = Decimal("0.004")
    order = _stop("0.010")
    case.orders.append(order)
    adapter._run = lambda *_a, **_k: _fills("0.004")
    adapter._absorb_trigger(case, order, SimpleNamespace(exchange_order_id="77"))
    assert order.state is IntentState.CANCELLED and case.position == 0


def test_cancel_waits_between_polls_while_a_triggered_child_is_still_settling(tmp_path):
    r, _spec, _context = _bound(tmp_path)
    adapter, case = r.adapter, r.adapter._case
    order = _stop("0.010")
    case.orders.append(order)
    sleeps: list[int] = []
    adapter._sleep = sleeps.append
    adapter._run = lambda *_a, **_k: SimpleNamespace(state=WorkState.UNKNOWN, value=None)
    adapter._query = lambda *_a, **_k: SimpleNamespace(kind=OutcomeKind.KNOWN, detail="algo_triggered")
    adapter._absorb_trigger = lambda *_a, **_k: None
    with pytest.raises(AdapterError) as caught:
        adapter._cancel(case, order)
    assert caught.value.code == "cancel.unconfirmed"
    assert len(sleeps) == adapter._cfg.settle_polls


def test_uncoded_failure_inside_cleanup_still_ends_in_a_recorded_hold(tmp_path):
    r, _spec, context = _bound(tmp_path)

    def boom(_case):
        raise RuntimeError("unexpected")
    r.adapter._refresh_stops = boom
    cleanup_context = _cleanup_context(context)
    first = r.adapter.cleanup(cleanup_context)
    assert first.final_truth is FinalTruth.HOLD and r.adapter._halted
    before = len(r.fake.requests)
    assert r.adapter.cleanup(cleanup_context) == first
    assert r.fake.requests[before:] == [] and _posts(r) == []


def test_no_general_read_reaches_the_exchange_before_retry_after(tmp_path):
    r = rig(tmp_path)
    seen: list[int] = []
    real = r.fake._get_fapi_v2_positionRisk

    def logged(params):
        seen.append(r.clock.now)
        return real(params)
    r.fake._get_fapi_v2_positionRisk = logged
    out: list[bytes] = []
    report = run_suite(manifest(), r.adapter, out.append)
    assert report.status is SuiteStatus.PASSED
    fired = [f for f in r.wired.faults.fired if f.kind is FaultKind.HTTP_429]
    assert len(fired) == 1 and fired[0].forwarded is False
    retry_at = fired[0].fired_at_ms + r.adapter._cfg.rate_retry_after_ms
    assert not [t for t in seen if fired[0].fired_at_ms < t < retry_at]
    case = next(c for c in report.cases if c.kind is ScenarioKind.RATE_LIMIT_429)
    assert case.final_truth in (FinalTruth.FLAT, FinalTruth.PROTECTED_RECONCILED)
