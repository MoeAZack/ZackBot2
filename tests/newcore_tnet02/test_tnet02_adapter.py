"""TNET-02 hostile-fake tests: the real adapter + real #291 venue + real NC-04/06/07/REC-02 against a fake exchange."""
from __future__ import annotations

import ast
import dataclasses
from decimal import Decimal
from pathlib import Path

import pytest

from newcore.testnet_harness import (CaseStatus, FailureCode, FinalTruth, ScenarioKind, SuiteStatus,
                                     canonical_report_bytes, run_suite)
from newcore.tnet02.adapter import HarnessConfig
from newcore.tnet02.gate import gate_proof
from newcore.venue.binance_usdm_testnet import HttpResponse

from .fake_binance import SECRET_KEY, SECRET_SIG, Algo, err
from .support import BUILD_SHA, account, manifest, rig, wave


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "newcore" / "tnet02"


def _run(r, spec=None):
    out: list[bytes] = []
    report = run_suite(spec or manifest(), r.adapter, out.append)
    assert len(out) == 1 and out[0] == canonical_report_bytes(report)
    return report, out[0]


def _posts(r, path=None):
    return [x for x in r.fake.requests if x[0] == "POST" and (path is None or x[1] == path)]


def _case(report, kind):
    return next(c for c in report.cases if c.kind is kind)


# ------------------------------------------------------------------------------------------------- happy path
def test_full_matrix_passes_against_fake_and_converts_to_passed_proof(tmp_path):
    r = rig(tmp_path)
    report, data = _run(r)
    assert report.status is SuiteStatus.PASSED, [(c.kind, c.failure) for c in report.cases]
    assert [c.status for c in report.cases] == [CaseStatus.PASSED] * 12
    assert all(c.final_truth is FinalTruth.FLAT for c in report.cases)
    assert all(c.orders_total <= 12 and c.peak_notional <= 1000 and c.max_leverage <= 3 for c in report.cases)
    proof = gate_proof(data, expected_head=BUILD_SHA, host_id="tnet_test")
    assert proof.passed is True
    assert not r.fake.positions or all(v == 0 for v in r.fake.positions.values())
    assert not [a for a in r.fake.algos.values() if a.status in ("NEW", "WORKING")]


def test_equivalent_runs_produce_identical_report_bytes(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert _run(rig(tmp_path / "a"))[1] == _run(rig(tmp_path / "b"))[1]


def test_minimum_refusal_sends_nothing(tmp_path):
    r = rig(tmp_path)
    report, _ = _run(r)
    case = _case(report, ScenarioKind.MINIMUM_REFUSAL)
    assert case.status is CaseStatus.PASSED and case.orders_total == 0


def test_lost_answer_entry_is_submitted_exactly_once(tmp_path):
    r = rig(tmp_path)
    report, _ = _run(r)
    assert _case(report, ScenarioKind.LOST_ANSWER_RECONCILE).status is CaseStatus.PASSED
    entries = {}
    for _m, path, params in _posts(r, "/fapi/v1/order"):
        entries[params["newClientOrderId"]] = entries.get(params["newClientOrderId"], 0) + 1
    assert entries and max(entries.values()) == 1


def test_synthetic_rate_limits_never_reach_the_exchange(tmp_path):
    r = rig(tmp_path)
    _run(r)
    fired = r.wired.faults.fired
    assert [f.kind.value for f in fired] == ["drop_after_accept", "http_429", "http_418"]
    assert [f.forwarded for f in fired] == [True, False, False]


# ------------------------------------------------------------------------------------------------- binding
@pytest.mark.parametrize("hostile", ["digest", "foreign_position", "foreign_algo", "one_way", "algo_read_fails",
                                     "settlement"])
def test_binding_hostility_refuses_before_any_order(tmp_path, hostile):
    r = rig(tmp_path)
    spec = manifest()
    if hostile == "digest":
        spec = manifest(acct=account(digest="0" * 16))
    elif hostile == "foreign_position":
        r.fake.positions[("ETHUSDT", "SHORT")] = Decimal("1")
    elif hostile == "foreign_algo":
        r.fake.algos["foreign"] = Algo(1, "foreign", "ETHUSDT", "LONG", Decimal("1"), Decimal("1"))
    elif hostile == "one_way":
        r.fake.hedge_mode = False
    elif hostile == "algo_read_fails":
        r.fake.fail_algo_open_read = True
    elif hostile == "settlement":
        acct = account()
        spec = manifest(acct=dataclasses.replace(acct, binding=dataclasses.replace(acct.binding,
                                                                                   settlement_asset="USDC")))
    report, data = _run(r, spec)
    assert report.status is SuiteStatus.FAILED and report.failure is FailureCode.BINDING
    assert report.cases == ()
    assert _posts(r) == []
    assert gate_proof(data, expected_head=BUILD_SHA, host_id="tnet_test").passed is False


# ------------------------------------------------------------------------------------------------- setup
def test_account_leverage_above_case_ceiling_holds_without_orders(tmp_path):
    r = rig(tmp_path)
    r.fake.leverage["BTCUSDT"] = 5
    report, _ = _run(r)
    assert report.status is SuiteStatus.HOLD
    assert report.cases[0].failure is FailureCode.SETUP
    assert _posts(r) == []


def test_preexisting_owned_looking_position_is_not_touched(tmp_path):
    r = rig(tmp_path)
    original_tick = r.fake.tick
    state = {"armed": False}

    def tick():
        original_tick()
        # a foreign position appears after binding confirmation, before the first case setup
        if not state["armed"] and any(req[1] == "/fapi/v1/openAlgoOrders" for req in r.fake.requests):
            state["armed"] = True
            r.fake.positions[("BTCUSDT", "LONG")] = Decimal("0.01")
    r.fake.tick = tick
    report, _ = _run(r)
    assert report.status is SuiteStatus.HOLD
    assert report.cases[0].failure is FailureCode.SETUP
    assert r.fake.positions[("BTCUSDT", "LONG")] == Decimal("0.01")
    assert _posts(r) == []


# ------------------------------------------------------------------------------------------------- bounds
def test_entry_that_would_breach_notional_is_never_sent(tmp_path):
    r = rig(tmp_path, config=HarnessConfig(entry_notional_fraction=Decimal("0.49")))
    adapter = r.adapter
    real_market = adapter._market
    real_mark = adapter._mark
    state = {"in_market": False}

    def market(case, purpose, qty, action):
        state["in_market"] = True
        try:
            return real_market(case, purpose, qty, action)
        finally:
            state["in_market"] = False

    def mark(case):
        value = real_mark(case)
        # the pre-send guard reads a mark 3x the price NC-06 sized with
        return value * 3 if state["in_market"] else value
    adapter._market, adapter._mark = market, mark
    report, _ = _run(r)
    first = report.cases[0]
    assert first.failure is FailureCode.ADAPTER
    assert first.orders_total == 0
    assert _posts(r) == []


def test_order_ceiling_is_enforced_locally_and_holds_instead_of_overspending(tmp_path):
    r = rig(tmp_path)
    spec = manifest()
    cases = list(spec.cases)
    cases[0] = dataclasses.replace(cases[0], max_orders=2)
    spec = dataclasses.replace(spec, cases=tuple(cases))
    report, _ = _run(r, spec)
    first = report.cases[0]
    assert first.orders_total <= 2
    assert report.status is SuiteStatus.HOLD
    assert len(_posts(r)) == 2


# ------------------------------------------------------------------------------------------------- order truth
def test_lost_answer_that_cannot_be_corroborated_holds_and_never_resubmits(tmp_path):
    r = rig(tmp_path)
    spec = manifest()
    entry_cid = None
    original = r.fake._post_fapi_v1_order

    def post(params):
        nonlocal entry_cid
        resp = original(params)
        if params["type"] == "MARKET" and entry_cid is None and state["drop_armed"]:
            entry_cid = params["newClientOrderId"]
            r.fake.hidden_answers.add(entry_cid)
        return resp
    state = {"drop_armed": False}
    real_arm = r.wired.faults.arm

    def arm(kind, match, **kwargs):
        state["drop_armed"] = kind.value == "drop_after_accept"
        return real_arm(kind, match, **kwargs)
    r.wired.faults.arm = arm
    r.fake._post_fapi_v1_order = post
    report, _ = _run(r, spec)
    lost = _case(report, ScenarioKind.LOST_ANSWER_RECONCILE)
    assert lost.status is CaseStatus.HOLD
    assert report.status is SuiteStatus.HOLD
    sent = [p for p in _posts(r, "/fapi/v1/order") if p[2]["newClientOrderId"] == entry_cid]
    assert len(sent) == 1


def test_rejected_protection_fails_case_and_cleanup_flattens(tmp_path):
    r = rig(tmp_path)
    r.fake.reject_algo_stop = True
    report, _ = _run(r)
    first = report.cases[0]
    assert first.status is CaseStatus.FAILED and first.failure is FailureCode.ADAPTER
    assert first.final_truth is FinalTruth.FLAT
    assert all(v == 0 for v in r.fake.positions.values())


def test_stop_that_never_triggers_fails_honestly(tmp_path):
    r = rig(tmp_path, price_path=lambda now, symbol: Decimal("60000") if symbol == "BTCUSDT" else Decimal("3000"),
            config=HarnessConfig(poll_ms=5000, trigger_wait_ms=60_000))
    report, _ = _run(r)
    first = report.cases[0]
    assert first.kind is ScenarioKind.LONG_TARGET and first.status is CaseStatus.FAILED
    assert first.failure is FailureCode.ASSERTION and first.final_truth is FinalTruth.FLAT
    assert report.status is SuiteStatus.FAILED


def test_real_418_from_exchange_is_never_released_by_cleanup(tmp_path):
    r = rig(tmp_path)
    original = r.fake._get_fapi_v2_positionRisk
    seen = {"n": 0}

    def ban(params):
        seen["n"] += 1
        if seen["n"] == 6:
            return HttpResponse(status=418, body=b'{"code":-1003,"msg":"banned"}', retry_after_ms=None)
        return original(params)
    r.fake._get_fapi_v2_positionRisk = ban
    report, _ = _run(r)
    assert report.status is SuiteStatus.HOLD
    assert r.wired.budget.snapshot().cause == "ban_418"


def test_duplicate_client_id_answer_is_reconciled_not_resent(tmp_path):
    r = rig(tmp_path)
    original = r.fake._post_fapi_v1_order
    state = {"done": False}

    def post(params):
        resp = original(params)
        if params["type"] == "MARKET" and not state["done"]:
            state["done"] = True
            return err(-4116)
        return resp
    r.fake._post_fapi_v1_order = post
    report, _ = _run(r)
    assert report.cases[0].status is CaseStatus.PASSED
    cids = [p[2]["newClientOrderId"] for p in _posts(r, "/fapi/v1/order")]
    assert len(cids) == len(set(cids))


def test_clock_regression_never_passes(tmp_path):
    r = rig(tmp_path)
    original = r.fake.__class__.__call__
    count = {"n": 0}

    def hostile(self, request):
        count["n"] += 1
        if count["n"] == 30:
            self.clock.now -= 600_000
        return original(self, request)
    r.fake.__class__.__call__ = hostile
    try:
        report, data = _run(r)
    finally:
        r.fake.__class__.__call__ = original
    assert report.status is not SuiteStatus.PASSED
    assert gate_proof(data, expected_head=BUILD_SHA, host_id="tnet_test").passed is False


# ------------------------------------------------------------------------------------------------- cleanup
def test_second_cleanup_sends_no_order_or_cancel(tmp_path):
    r = rig(tmp_path)
    adapter = r.adapter
    real_cleanup = adapter.cleanup
    seen: dict[str, int] = {}

    def cleanup(context):
        if context.token in seen:
            before = len(r.fake.requests)
            out = real_cleanup(context)
            assert all(m == "GET" for m, _p, _q in r.fake.requests[before:])
            return out
        seen[context.token] = 1
        return real_cleanup(context)
    adapter.cleanup = cleanup
    report, _ = _run(r)
    assert report.status is SuiteStatus.PASSED


def test_state_change_between_cleanup_calls_is_not_idempotent(tmp_path):
    r = rig(tmp_path)
    adapter = r.adapter
    real_cleanup = adapter.cleanup
    seen: set[str] = set()

    def cleanup(context):
        if context.token not in seen:
            seen.add(context.token)
            out = real_cleanup(context)
            r.fake.positions[("BTCUSDT", "LONG")] = Decimal("0.002")
            return out
        return real_cleanup(context)
    adapter.cleanup = cleanup
    report, _ = _run(r)
    assert report.status is SuiteStatus.HOLD
    assert report.cases[0].failure is FailureCode.CLEANUP_NOT_IDEMPOTENT


# ------------------------------------------------------------------------------------------------- restart
def test_corrupt_journal_on_restart_holds(tmp_path):
    r = rig(tmp_path)
    opener = r.adapter._journals
    real_open = opener.open

    def open_(case_id, boot_id, *, reopen):
        if reopen:
            path = r.journal_dir / f"{case_id}.journal"
            data = path.read_bytes()
            path.write_bytes(data.replace(b'"executed_qty":"0"', b'"executed_qty":"9"', 1))
        return real_open(case_id, boot_id, reopen=reopen)
    opener.open = open_
    report, _ = _run(r)
    restart = _case(report, ScenarioKind.RESTART_OPEN_POSITION)
    assert restart.status is CaseStatus.HOLD
    assert report.status is SuiteStatus.HOLD


# ------------------------------------------------------------------------------------------------- privacy
def test_secrets_urls_and_exception_text_never_reach_report_or_journal(tmp_path, capsys):
    r = rig(tmp_path)
    original = r.fake.__class__.__call__
    count = {"n": 0}

    def hostile(self, request):
        count["n"] += 1
        if count["n"] == 40:
            raise RuntimeError(f"{SECRET_KEY} {SECRET_SIG} {request.url} X-MBX-APIKEY")
        return original(self, request)
    r.fake.__class__.__call__ = hostile
    try:
        report, data = _run(r)
    finally:
        r.fake.__class__.__call__ = original
    captured = capsys.readouterr()
    blobs = [data, captured.out.encode(), captured.err.encode()]
    blobs += [p.read_bytes() for p in r.journal_dir.iterdir()]
    for blob in blobs:
        for marker in (SECRET_KEY, SECRET_SIG, "testnet.binancefuture.com", "X-MBX-APIKEY", "RuntimeError"):
            assert marker.encode() not in blob


# ------------------------------------------------------------------------------------------------- static
def test_package_imports_no_network_process_or_legacy_module():
    banned = {"socket", "ssl", "http", "urllib", "subprocess", "requests", "engine", "binance_client", "app",
              "newcore.venue.http_sender"}
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in banned and name not in banned, (path.name, name)
