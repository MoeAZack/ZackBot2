import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "gpu_research_01" / "gpu_research_01_contract.v1.json"


def _contract():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_gpu_contract_has_no_trading_or_runtime_authority():
    contract = _contract()

    assert contract["status"] == "DRAFT"
    assert all(value is False for value in contract["authority"].values())
    assert contract["implementation_boundary"] == {
        "new_files_only": True,
        "runtime_dependency_added": False,
        "runtime_files_edited": False,
        "paper_state_touched": False,
        "holdout_opened": False,
    }


def test_gpu_contract_freezes_resource_guard_and_no_fallback():
    guard = _contract()["resource_guard"]

    assert guard["required_device"] == "cuda:0"
    assert guard["silent_cpu_fallback_allowed"] is False
    assert guard["max_gpu_memory_gib"] == 8
    assert guard["max_host_memory_gib"] == 4
    assert guard["max_cpu_threads"] == 2
    assert guard["min_free_gpu_memory_gib"] == 8
    assert guard["min_free_host_memory_gib"] == 4
    assert guard["paper_health_must_be_known"] is True
    assert guard["single_gpu_research_owner"] is True
    assert guard["oom_retry_with_larger_request"] is False


def test_gpu_contract_preserves_ml_data_and_validation_gates():
    contract = _contract()
    gate = contract["ml_data_gate"]
    pair = contract["pair_rules"]

    assert gate == {
        "min_paired_labels": 1000,
        "min_family_side_cell": 100,
        "min_future_folds": 4,
        "min_fold_labels": 50,
        "min_cairo_days": 30,
        "min_regimes": 2,
        "synthetic_rows_count_toward_gate": False,
    }
    assert pair["same_ordered_rows"] is True
    assert pair["same_fold_indices"] is True
    assert pair["same_features_labels_costs"] is True
    assert pair["same_seed_and_parameters"] is True
    assert pair["binary_model_equality_expected"] is False
    assert pair["independent_ml_train_gates_required"] is True
    assert pair["agreement_may_waive_failed_gate"] is False


def test_gpu_contract_requires_determinism_diagnostics_and_fail_closed_errors():
    contract = _contract()

    assert contract["determinism"] == {
        "same_device_gpu_repeats": 2,
        "canonical_prediction_and_metric_packet_compare": "byte_exact",
        "nondeterminism_blocks_candidate": True,
    }
    assert set(contract["required_diagnostics"]) == {
        "training_seconds",
        "peak_host_memory_bytes",
        "peak_gpu_memory_bytes",
        "fold_metrics",
        "prediction_pearson",
        "prediction_spearman",
        "prediction_mean_abs_delta",
        "prediction_max_abs_delta",
        "threshold_decision_disagreement",
        "artifact_hashes",
    }
    assert set(contract["failure_policy"].values()) == {"fail_closed"}
    assert contract["scope"]["edge04_migration"] is False
    assert contract["scope"]["synthetic_probe_is_strategy_evidence"] is False
