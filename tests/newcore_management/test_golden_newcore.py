"""Strict proof for the accepted 30-case / 60-binding NEWCORE golden matrix."""
from __future__ import annotations

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "golden"))

from goldenlib import adapters, compare, schema  # noqa: E402


ADAPTERS = ("newcore_live_sim", "newcore_sim")
CASES = tuple(schema.load_all())


def _run(case, adapter):
    return adapters.get(adapter).run(case)


def test_exact_frozen_matrix_is_strict_required():
    assert len(CASES) == 30
    assert {(case["id"], adapter) for case in CASES for adapter in ADAPTERS} == {
        (case["id"], adapter)
        for case in CASES
        for adapter in ADAPTERS
        if schema.status(case, adapter) == "required"
    }


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_newcore_adapter_strictly_reproduces_frozen_case(case, adapter):
    trace = _run(adapters.blind(case), adapter)
    mismatches = compare.compare(case, trace, adapter)
    assert mismatches == [], compare.report(case, adapter, mismatches, trace)
    assert trace.final == {"lots": 0}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_live_and_replay_traces_have_causal_parity(case):
    replay = _run(adapters.blind(case), "newcore_sim").as_dict()
    live = _run(adapters.blind(case), "newcore_live_sim").as_dict()
    assert live == replay


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_adapter_is_expectation_blind(case, adapter):
    assert _run(adapters.blind(case), adapter).as_dict() == _run(case, adapter).as_dict()


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_canonical_trace_is_schema_closed_and_secret_safe(case, adapter):
    wire = _run(adapters.blind(case), adapter).as_dict()
    assert set(wire) == {"trades", "final"}
    assert set(wire["final"]) == {"lots"}
    for expected, actual in zip(case["expect"]["trades"], wire["trades"]):
        assert set(actual) - {"_res"} == set(expected) - {"tol"}
        if "_res" in actual:
            assert set(actual["_res"]) <= {"R", "pnl"}
    encoded = json.dumps(wire, sort_keys=True, separators=(",", ":"), ensure_ascii=True).lower()
    assert not any(marker in encoded for marker in ("secret", "api_key", "apikey", "token", "password", "sk-live"))
