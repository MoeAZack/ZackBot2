"""Metadata-only causal-v4 authority rebinds; this module opens no market/outcome row."""
import gzip
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REBIND_PATHS = (
    ROOT / "contracts/strategy_causal_pack_01_causal_v4/strategy_causal_pack_01_causal_v4_rebind.v1.json",
    ROOT / "contracts/rank_breadth_reversal_4h_short_causal_v4/rank_breadth_reversal_4h_short_causal_v4_rebind.v1.json",
    ROOT / "contracts/atr_shock_snapback_4h_causal_v4/atr_shock_snapback_4h_causal_v4_rebind.v1.json",
)
EXPECTED_IDENTITIES = (
    {
        "pullback_rsi_short_h1_causal_v3": "pullback_rsi_short_h1_causal_v4",
        "exp_mom_short_c27_causal_v3": "exp_mom_short_c27_causal_v4",
    },
    {"rank_breadth_reversal_4h_short_causal_v3": "rank_breadth_reversal_4h_short_causal_v4"},
    {
        "atr_shock_snapback_4h_long_causal_v3": "atr_shock_snapback_4h_long_causal_v4",
        "atr_shock_snapback_4h_short_causal_v3": "atr_shock_snapback_4h_short_causal_v4",
    },
)
AUTHORITY = {
    "manifest": {
        "path": "research_evidence/manifests/binance-um-archive-precutoff-monthly-rest-v5.json.gz",
        "byte_sha256": "3de00d9a9961d04938e71cd58fb042c28bfeea48519d91d8fbac2d12da99987a",
        "content_digest": "30c345c1261f3fe0d48bf4defc448c9139a073aecb2eee4155ecca1e4b64f1a8",
    },
    "pit_universe": {
        "path": "research_evidence/universe/pit-top40-qv30d-precutoff-monthly-rest-v8.json",
        "byte_sha256": "3fc2c1696a4f9ac753a140394e6ca6ba723a9364046dbb94bcb6310c48b68d41",
        "content_digest": "48f99b4309ff286f5541bd51199e42d4144dcc38e9dda7aa5bc3520b4d244bcd",
    },
    "coverage_preflight": {
        "path": "contracts/strategy_causal_pack_01/precutoff_authority_coverage_preflight.v3.json",
        "byte_sha256": "eeb985475e553602321151bfa973e338f943ef3c8181836c220c46862e1a4c65",
        "content_digest": "43734e67a4bafeb7c7b29bb857cb7b8e6d04d2f47c7aedc87ac7bed275157ada",
        "required_guaranteed_period_gaps": 0,
    },
}
TOP_KEYS = {
    "format", "rebind_id", "version", "status", "frozen_at_cairo", "scope", "source_contract",
    "identity_rebinds", "authority_artifacts", "immutable_rules", "immutable_trial_history",
    "multiplicity_append", "protected_authority_prerequisite",
}


def _raw_json(rel):
    raw = (ROOT / rel).read_bytes()
    return raw, json.loads(raw)


def _artifact_json(binding):
    raw = (ROOT / binding["path"]).read_bytes()
    decoded = gzip.decompress(raw) if binding["path"].endswith(".gz") else raw
    return raw, json.loads(decoded)


def test_rebinds_overlay_only_identity_authority_status_and_multiplicity_metadata():
    for path, expected_ids in zip(REBIND_PATHS, EXPECTED_IDENTITIES):
        rebind = json.loads(path.read_text(encoding="utf-8"))
        assert set(rebind) == TOP_KEYS
        assert rebind["format"] == "zb-causal-v4-authority-rebind/1"
        assert rebind["status"] == "NOT_AUTHORIZED_PRE_PROTECTED_AUTHORITY"
        assert rebind["source_contract"]["immutable_in_full"] is True
        assert rebind["immutable_rules"]["overlays_exactly"] == [
            "candidate_identity", "authority_artifacts", "authorization_status", "multiplicity_append"]
        assert rebind["immutable_rules"]["runner_pins_unchanged"] is True
        assert {row["v3_candidate_id"]: row["v4_candidate_id"]
                for row in rebind["identity_rebinds"]} == expected_ids
        assert all(old.endswith("_causal_v3") and new.endswith("_causal_v4")
                   for old, new in expected_ids.items())
        # Rule-bearing structures are absent from the overlay. They remain byte-pinned in source_contract.
        assert not ({"folds", "costs", "execution", "promotion_gates", "signal_parameters",
                     "management", "exchange_filter_authority"} & set(rebind))


def test_source_contracts_and_trial_histories_are_byte_for_byte_immutable_references():
    for path in REBIND_PATHS:
        rebind = json.loads(path.read_text(encoding="utf-8"))
        source_raw, source = _raw_json(rebind["source_contract"]["path"])
        assert hashlib.sha256(source_raw).hexdigest() == rebind["source_contract"]["byte_sha256"]
        source_ids = json.dumps(source, sort_keys=True)
        for row in rebind["identity_rebinds"]:
            assert row["v3_candidate_id"] in source_ids
            assert row["v4_candidate_id"] not in source_ids
        for history in rebind["immutable_trial_history"]:
            history_raw, body = _raw_json(history["path"])
            assert hashlib.sha256(history_raw).hexdigest() == history["byte_sha256"]
            if "content_digest" in history:
                assert body["digest"] == history["content_digest"]


def test_all_rebinds_pin_the_same_exact_complete_authority_without_opening_market_rows():
    for path in REBIND_PATHS:
        rebind = json.loads(path.read_text(encoding="utf-8"))
        assert rebind["authority_artifacts"] == AUTHORITY
        assert rebind["scope"] == {
            "metadata_only": True,
            "market_rows_read": False,
            "candidate_signals_evaluated": False,
            "candidate_outcomes_read": False,
            "economics_run": False,
            "runner_pins_changed": False,
        }
    for name, binding in AUTHORITY.items():
        raw, artifact = _artifact_json(binding)
        assert hashlib.sha256(raw).hexdigest() == binding["byte_sha256"]
        assert artifact["digest"] == binding["content_digest"]
        if name == "coverage_preflight":
            assert artifact["guaranteed_period_gaps"] == 0
            assert artifact["wf3_september"]["daily_grid_missing"] == []
            assert artifact["wf3_september"]["funding_incomplete_symbols"] == []


def test_protected_authority_merge_is_a_hard_execution_barrier():
    for path in REBIND_PATHS:
        rebind = json.loads(path.read_text(encoding="utf-8"))
        gate = rebind["protected_authority_prerequisite"]
        assert gate == {
            "pull_request": 304,
            "authority_source_head": "59babc0c13aa08fafdffe400029c7c1377b96319",
            "authority_protected_merge_commit": "577b0cdaab10ba0c81d0d31950632ee0dd52d2c6",
            "required_event": ("PR #304 containing the exact authority artifact hashes in this rebind is merged "
                               "into the protected branch"),
            "execution_authorized_now": False,
            "post_merge_runner_pin_change_requires_separate_review": True,
        }


def test_multiplicity_is_append_only_and_does_not_reuse_v3_trials():
    pack, rbr, atr = (json.loads(path.read_text(encoding="utf-8")) for path in REBIND_PATHS)
    assert pack["multiplicity_append"] == {
        "rule": ("Append both causal_v4 identities as new trials; never replace, relabel, erase or credit any "
                 "prior v3 or earlier trial."),
        "prior_registered_packet_count": 3, "appended_v4_trial_count": 2,
        "resulting_registered_packet_count": 5, "holm_missing_prior_p_value": 1.0,
        "inference_role": "diagnostic_only",
        "promotion_uses_inference": False,
    }
    assert (rbr["multiplicity_append"]["prior_family_size"],
            rbr["multiplicity_append"]["resulting_family_size"]) == (3, 4)
    assert (atr["multiplicity_append"]["prior_family_size"],
            atr["multiplicity_append"]["resulting_family_size"]) == (4, 6)
    assert atr["multiplicity_append"]["cells_judged_separately"] is True
    assert atr["multiplicity_append"]["cross_side_pooling"] is False
