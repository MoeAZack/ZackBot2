import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (
    ROOT
    / "contracts"
    / "harness_compare_01"
    / "harness_compare_01_contract.v1.json"
)


def _contract():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_harness_compare_contract_has_no_trading_authority():
    contract = _contract()

    assert contract["status"] == "DRAFT"
    assert all(value is False for value in contract["authority"].values())
    assert contract["implementation_boundary"] == {
        "new_files_only": True,
        "third_party_install_in_contract_slice": False,
        "runtime_files_edited": False,
        "paper_state_touched": False,
    }


def test_harness_compare_contract_requires_causal_and_recursive_checks():
    contract = _contract()
    diagnostics = contract["diagnostics"]
    acceptance = contract["acceptance"]

    assert diagnostics["future_shift_mutation_detected"] is True
    assert diagnostics["prefix_events_byte_identical"] is True
    assert diagnostics["recursive_warmup_converges"] is True
    assert acceptance["lookahead_diagnostics_clean"] is True
    assert acceptance["recursive_diagnostics_clean"] is True
    assert acceptance["cost_stress_trade_identity_unchanged"] is True
    assert acceptance["unexplained_signal_divergences"] == 0
    assert acceptance["unexplained_fill_divergences"] == 0
    assert acceptance["unexplained_exit_divergences"] == 0
    assert acceptance["unexplained_cost_divergences"] == 0


def test_harness_compare_contract_defers_external_execution():
    contract = _contract()

    assert "freqtrade_adapter" in contract["deferred"]
    assert "pinned_isolated_environment" in contract["deferred"]
    assert contract["scope"]["engines"] == [
        "zackbot_golden",
        "freqtrade_isolated_later",
    ]


def test_harness_compare_contract_freezes_numeric_and_divergence_semantics():
    contract = _contract()
    frozen = contract["frozen_diagnostics"]
    divergence = contract["declared_divergence"]

    assert frozen["warmup_closed_candles"] == [128, 256, 512]
    assert frozen["scored_interval"] == "[512,pack_end)"
    assert frozen["binary_float_allowed"] is False
    assert frozen["canonical_json_compare"] == "byte_exact"
    assert frozen["unexplained_numeric_tolerance"] == "0"
    assert divergence["wildcards_allowed"] is False
    assert divergence["field_group_suppression_allowed"] is False
    assert set(divergence["required_fields"]) == {
        "event_key",
        "field",
        "zackbot_value",
        "foreign_value",
        "reason_code",
        "semantic_reason",
        "source_engine",
        "evidence_hash",
    }
