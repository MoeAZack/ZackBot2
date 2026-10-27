import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREREG = ROOT / "docs/newcore/research/EDGE04_LEGACY_REEVAL_PREREG_v1.json"
LEDGER = ROOT / "research_evidence/ledger/edge04_legacy_reeval.jsonl"


def load_contract():
    return json.loads(PREREG.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_contract_is_frozen_and_bound_to_exact_source():
    c = load_contract()
    assert c["format"] == "zb-edge04-prereg/1"
    assert c["candidate_id"] == "edge04_legacy_reeval.v1"
    assert c["status"].startswith("FROZEN BEFORE RESULT")
    assert c["source"]["commit"] == "07cbc8172de4d05b01532f1797d217000eed0fa9"
    assert c["source"]["strategies_sha256"] == sha256(ROOT / "strategies.py")
    assert c["source"]["backtest_sha256"] == sha256(ROOT / "backtest.py")
    assert c["source"]["candidate_runner_sha256"] == sha256(
        ROOT / "tools/research/run_breadth_candidate.py"
    )
    assert c["universe"]["artifact_sha256"] == sha256(ROOT / c["universe"]["artifact"])


def test_four_runtime_hypotheses_and_management_are_exactly_frozen():
    c = load_contract()
    hs = {h["id"]: h for h in c["hypotheses"]}
    assert set(hs) == {"H1", "H2", "H3", "H4"}
    assert [(hs[k]["key"], hs[k]["side"], hs[k]["timeframe"]) for k in hs] == [
        ("pullback_rsi", "short", "4h"),
        ("rotation", "long", "4h"),
        ("exp_trend", "long", "4h"),
        ("ema_mom", "long", "4h"),
    ]
    assert hs["H1"]["when"] == "bear"
    assert hs["H1"]["management"] == {
        "stop_atr": 2.0, "tp1_r": 1.0, "tp1_frac": 0.5,
        "be_r": 1.0, "tp_r": 3.0, "max_bars": 60,
    }
    assert hs["H2"]["params"] == {"k": 5}
    assert hs["H2"]["management"] == {"stop_atr": 3.0}
    assert hs["H3"]["signal"] == hs["H4"]["signal"]
    assert hs["H3"]["management"] == {"stop_atr": 2.5, "tp_r": 3.0, "max_bars": 180}
    assert hs["H4"]["management"] == {"stop_atr": 2.5}


def test_windows_are_contiguous_and_holdout_remains_sealed():
    c = load_contract()
    w = c["windows"]
    folds = w["walk_forward"]
    assert w["train"]["end"] == folds[0]["start"]
    assert all(a["end"] == b["start"] for a, b in zip(folds, folds[1:]))
    assert folds[-1]["end"] == w["sealed_cutoff"] == "2026-09-28T00:00:00Z"
    assert w["holdout"] == {
        "start": "2026-11-02T00:00:00Z", "end": "2027-11-01T00:00:00Z",
        "minimum_episodes_per_hypothesis": 30, "reveals": 1,
    }
    assert c["boundaries"] == {
        "paper": False, "runtime_change": False, "holdout_read": False, "mainnet": False,
    }


def test_finite_trial_budget_and_prior_inventory_are_closed():
    c = load_contract()
    variants = c["variants"]
    assert len(variants) == 7
    assert sum(v["role"] == "primary" for v in variants) == 4
    assert sum(v["role"] == "diagnostic" for v in variants) == 3
    assert len({v["id"] for v in variants}) == 7
    assert c["trial_budget"] == {
        "strategy_variants": 7, "primary_hypotheses": 4, "diagnostics": 3,
        "baseline_rows": 8, "total_ledger_trial_records": 15, "primary_holm_m": 4,
        "rule": "diagnostics and stress rows may never replace a failed primary; no result-dependent new variant",
    }

    inv = c["prior_inventory"]
    packets = inv["candidate_packets"]
    assert len(packets) == len({p["packet"] for p in packets}) == 18
    assert {h: sum(p["h"] == h for p in packets) for h in ("H1", "H2", "H3", "H4")} == {
        "H1": 3, "H2": 4, "H3": 7, "H4": 4,
    }
    assert inv["legacy_csv_context"]["named_rows_from_committed_board"] == {
        "pullback_rsi": 10, "rotation": 12, "ema_mom": 10, "exp_trend": 0,
    }
    assert inv["historical_prior_looks"] == 32 + 18 + 18 == 68
    assert inv["search_aware_total_after_this_prereg"] == 68 + 7 == 75


def test_all_frozen_gates_are_present_and_diagnostics_cannot_rescue():
    c = load_contract()
    assert set(c["gates"]) == {
        "data", "activity", "walk_forward", "expectancy", "baselines", "stress",
        "concentration", "drawdown", "multiplicity", "verdict",
    }
    assert "4 of 5" in c["gates"]["walk_forward"]
    assert "0.75" in c["gates"]["activity"]
    assert "95% lower" in c["gates"]["expectancy"]
    assert "75" in c["gates"]["multiplicity"]
    assert c["gates"]["verdict"].endswith("diagnostics cannot rescue it")


def test_append_only_ledger_has_one_record_per_frozen_trial():
    from tools.research import ledger

    c = load_contract()
    records = ledger.verify(str(LEDGER))["edge04_legacy_reeval"]
    assert len(records) == c["ledger"]["records_before_result"] == 15
    assert sum(r["kind"] == "variant" for r in records) == 4
    assert sum(r["kind"] == "grid_point" for r in records) == 3
    assert sum(r["kind"] == "baseline" for r in records) == 8
    assert all(r["split"] == "development" for r in records)
    assert all(r["manifest_digest"] is None and r["run_digest"] is None for r in records)
    assert all(r["detail"]["prereg_sha256"] == sha256(PREREG) for r in records)
    assert len({r["candidate_id"] for r in records}) == 15
