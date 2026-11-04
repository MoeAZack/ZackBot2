"""TNET-02 adapter guards called directly, so a check is proven even where the kernel would also catch it.

Each test here kills a mutant of one adapter guard that the end-to-end suite alone left alive (the kernel's own
binding and cleanup checks masked them).  Driving the adapter directly shows each guard refuses on its own.
"""
from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from newcore.domain.account import Environment
from newcore.domain.orders import IntentState, Purpose, Side
from newcore.ports.rate_blocks import RateBlockState
from newcore.testnet_harness import (CLEANUP_FLATTEN_MS, CLEANUP_HOLD_MS, CLEANUP_PROTECT_MS, CleanupContext,
                                     FinalTruth, HarnessBinding, _case_context, _derive)
from newcore.tnet02.adapter import AdapterError, _Order
from newcore.tnet02.faults import FaultKind
from newcore.venue.binance_usdm_testnet import HttpResponse

from .fake_binance import Algo
from .support import T0, account, manifest, rig


def _code(fn, *args) -> str:
    with pytest.raises(AdapterError) as caught:
        fn(*args)
    return caught.value.code


def _posts(r):
    return [x for x in r.fake.requests if x[0] in ("POST", "DELETE")]


def _bound(tmp_path):
    r = rig(tmp_path)
    spec = manifest()
    r.adapter.confirm_binding(spec.binding)
    suite_id = _derive("suite", "guards")
    context = _case_context(spec, suite_id, spec.cases[0], T0)
    r.adapter.setup(context)
    return r, spec, context


def _cleanup_context(context, started=T0 + 1):
    token = _derive("cleanup", context.suite_id, context.case.case_id)
    return CleanupContext(context.suite_id, context.case.case_id, token, started, started + CLEANUP_PROTECT_MS,
                          started + CLEANUP_FLATTEN_MS, started + CLEANUP_HOLD_MS)


# ------------------------------------------------------------------------------------------------- binding
def test_binding_key_digest_mismatch_refused_by_adapter(tmp_path):
    r = rig(tmp_path)
    binding = manifest(acct=account(digest="0" * 16)).binding
    assert _code(r.adapter.confirm_binding, binding) == "binding.key_digest"
    assert r.fake.requests == []


def test_binding_non_testnet_environment_refused_by_adapter(tmp_path):
    # HarnessBinding itself refuses MAINNET; the adapter must still refuse if that validation is ever bypassed
    r = rig(tmp_path)
    acct = account()
    mainnet = dataclasses.replace(acct, binding=dataclasses.replace(acct.binding, environment=Environment.MAINNET))
    forged = object.__new__(HarnessBinding)
    for field in dataclasses.fields(HarnessBinding):
        object.__setattr__(forged, field.name, getattr(manifest().binding, field.name))
    object.__setattr__(forged, "account", mainnet)
    assert _code(r.adapter.confirm_binding, forged) == "binding.environment"
    assert r.fake.requests == []


@pytest.mark.parametrize("hostile", ["one_way", "settlement"])
def test_binding_account_mode_mismatch_refused_by_adapter(tmp_path, hostile):
    r = rig(tmp_path)
    binding = manifest().binding
    if hostile == "one_way":
        # the position rows still look hedge-mode; only the account facts report one-way mode
        port = r.adapter._account_port
        real_facts = port.facts
        port.facts = lambda symbols: dataclasses.replace(real_facts(symbols), hedge_mode=False)
    else:
        acct = account()
        binding = HarnessBinding(dataclasses.replace(acct, binding=dataclasses.replace(acct.binding,
                                                                                       settlement_asset="USDC")),
                                 True, binding.symbols)
    assert _code(r.adapter.confirm_binding, binding) == "binding.account_mode"
    assert _posts(r) == []


# ------------------------------------------------------------------------------------------------- setup
def test_setup_refuses_while_a_fault_is_armed(tmp_path):
    r = rig(tmp_path)
    spec = manifest()
    r.adapter.confirm_binding(spec.binding)
    r.wired.faults.arm(FaultKind.HTTP_429, lambda request: False, retry_after_ms=1000)
    context = _case_context(spec, _derive("suite", "guards"), spec.cases[0], T0)
    assert _code(r.adapter.setup, context) == "setup.fault_armed"
    assert _posts(r) == []


# ------------------------------------------------------------------------------------------------- cleanup
def test_cleanup_never_releases_a_real_exchange_429(tmp_path):
    r, _spec, context = _bound(tmp_path)
    original = r.fake._get_fapi_v2_positionRisk
    r.fake._get_fapi_v2_positionRisk = lambda params: HttpResponse(
        status=429, body=b'{"code":-1003,"msg":"too many"}', retry_after_ms=60_000)
    with pytest.raises(AdapterError):
        r.adapter._read_positions("BTCUSDT")
    r.fake._get_fapi_v2_positionRisk = original
    assert r.wired.budget.snapshot().state is not RateBlockState.OPEN
    evidence = r.adapter.cleanup(_cleanup_context(context))
    assert evidence.final_truth is FinalTruth.HOLD
    assert r.wired.budget.snapshot().state is not RateBlockState.OPEN
    assert _posts(r) == []


def test_cleanup_holds_on_venue_position_it_does_not_own_and_sends_nothing(tmp_path):
    r, _spec, context = _bound(tmp_path)
    r.adapter._case.position = Decimal("0.005")
    r.fake.positions[("BTCUSDT", "LONG")] = Decimal("0.010")
    evidence = r.adapter.cleanup(_cleanup_context(context))
    assert evidence.final_truth is FinalTruth.HOLD
    assert r.fake.positions[("BTCUSDT", "LONG")] == Decimal("0.010")
    assert _posts(r) == []


def test_cleanup_holds_when_reconciliation_is_not_flat_and_second_call_sends_nothing(tmp_path):
    r, _spec, context = _bound(tmp_path)
    r.fake.algos["foreign"] = Algo(99, "foreign", "BTCUSDT", "LONG", Decimal("0.001"), Decimal("50000"))
    cleanup_context = _cleanup_context(context)
    first = r.adapter.cleanup(cleanup_context)
    assert first.final_truth is FinalTruth.HOLD
    assert "foreign" in r.fake.algos and _posts(r) == []
    before = len(r.fake.requests)
    second = r.adapter.cleanup(cleanup_context)
    assert second == first
    assert r.fake.requests[before:] == []


@pytest.mark.parametrize("purpose, state", [(Purpose.PROTECT, IntentState.UNKNOWN),
                                            (Purpose.PROTECT, IntentState.CANCELLING),
                                            (Purpose.ENTRY, IntentState.WORKING),
                                            (Purpose.CLOSE, IntentState.UNKNOWN)])
def test_cleanup_holds_on_an_unresolved_order_without_sending(tmp_path, purpose, state):
    r, _spec, context = _bound(tmp_path)
    r.adapter._case.orders.append(_Order("int_" + "1" * 32, "zbn1o-unknown", "classic", purpose, "BTCUSDT",
                                         Side.LONG, Decimal("0.005"), None, state))
    r.adapter._case.position = Decimal("0.005")
    r.fake.positions[("BTCUSDT", "LONG")] = Decimal("0.005")
    evidence = r.adapter.cleanup(_cleanup_context(context))
    assert evidence.final_truth is FinalTruth.HOLD
    assert r.fake.positions[("BTCUSDT", "LONG")] == Decimal("0.005")
    assert _posts(r) == []


def test_cleanup_holds_without_sending_while_local_truth_is_untrusted(tmp_path):
    r, _spec, context = _bound(tmp_path)
    r.adapter._case.trusted = False
    r.adapter._case.position = Decimal("0.005")
    r.fake.positions[("BTCUSDT", "LONG")] = Decimal("0.005")
    evidence = r.adapter.cleanup(_cleanup_context(context))
    assert evidence.final_truth is FinalTruth.HOLD
    assert r.fake.positions[("BTCUSDT", "LONG")] == Decimal("0.005")
    assert _posts(r) == []
