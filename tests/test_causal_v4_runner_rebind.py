"""Hostile tests for the pre-protection causal-v4 runner barrier; no market data is opened."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.research import atr_shock_snapback_4h_causal_v4_runner as ATR
from tools.research import causal_v4_runner_rebind as G
from tools.research import rank_breadth_reversal_4h_short_causal_v4_runner as RBR
from tools.research import strategy_causal_pack_01_causal_v4_runner as PACK


ROOT = Path(__file__).resolve().parents[1]
PROFILE_IDS = {
    "pack": "pullback_rsi_short_h1_causal_v4",
    "rbr_short": "rank_breadth_reversal_4h_short_causal_v4",
    "atr_shock": "atr_shock_snapback_4h_long_causal_v4",
}
LEGACY_BYTES = {
    "tools/research/strategy_causal_pack_01_runner.py":
        "a0242ce5def9aa3c64b18f2554616e25d242c96d037d81f0362ae07505d4537f",
    "tools/research/strategy_causal_pack_01_archive.py":
        "691a6aa2ec621f4b4d36f9ad60b3583b920162ae0c56956d0928dcdeaeb70e70",
    "tools/research/rank_breadth_reversal_4h_short_causal_v3_runner.py":
        "ca9acbf0cc6a1283b83ccdda41e19dcc256a48a39c3c6f5e1e074c94b7ff3b91",
    "tools/research/rank_breadth_reversal_4h_short_causal_v3_archive.py":
        "da162f97aace876c39ec97e36704f2baf463816ba667d3d99d61b5fd4fd64b13",
    "tools/research/run_atr_shock_snapback_candidate.py":
        "2dba8fa8f1a5a5952ede18bf95cf900abe513ef23cc304244442338934a0e9b3",
    "strategies.py": "0d416c8ca93788827aabe8374dae9f397f23c68213727d8725dc574071b8b011",
}


def test_saved_metadata_is_provisional_only_and_all_profiles_validate():
    metadata = G.load_metadata()
    gate = metadata["protected_execution_gate"]
    assert gate["v4_contract_provisional_stack_pin"] == G.PROVISIONAL_STACK_PIN
    assert gate["authority_protected_merge_commit"] == "577b0cdaab10ba0c81d0d31950632ee0dd52d2c6"
    assert gate["v4_contract_protected_commit"] == "11db5a45036a9cf831f7d46773b6e621d7c9dc92"
    assert gate["filter_authority_protected_commit"] is None
    assert metadata["scope"] == {
        "runner_metadata_and_validators_only": True,
        "strategy_logic_changed": False,
        "parameters_folds_gates_costs_exchange_rules_changed": False,
        "identity_claim_authorized": False,
        "market_or_outcome_read_authorized": False,
    }
    for profile, candidate_id in PROFILE_IDS.items():
        assert candidate_id in G.validate_profile(profile, metadata)["candidate_ids"]


@pytest.mark.parametrize("profile", ["pack", "rbr_short", "atr_shock"])
def test_provisional_barrier_refuses_before_delegate_import_or_protected_ref_access(monkeypatch, profile):
    touched = {"import": 0, "ancestor": 0}
    monkeypatch.setattr(G.importlib, "import_module", lambda *_: touched.__setitem__("import", 1))
    with pytest.raises(G.V4ExecutionBarrier, match="filter-authority pins are incomplete"):
        G.guarded_dispatch(
            profile,
            PROFILE_IDS[profile],
            is_ancestor=lambda *_: touched.__setitem__("ancestor", 1) or True,
        )
    assert touched == {"import": 0, "ancestor": 0}


def test_d07_provisional_pin_can_never_be_relabelled_as_protected(monkeypatch):
    metadata = copy.deepcopy(G.load_metadata())
    gate = metadata["protected_execution_gate"]
    gate["authority_protected_merge_commit"] = "a" * 40
    gate["v4_contract_protected_commit"] = G.PROVISIONAL_STACK_PIN
    gate["filter_authority_protected_commit"] = "c" * 40
    calls = []
    monkeypatch.setattr(G, "load_metadata", lambda: metadata)
    with pytest.raises(G.V4ExecutionBarrier, match="PROVISIONAL_STACK_PIN only"):
        G.require_execution_authority("pack", is_ancestor=lambda *args: calls.append(args) or True)
    assert calls == []


def _post_pin_metadata():
    metadata = copy.deepcopy(G.load_metadata())
    gate = metadata["protected_execution_gate"]
    gate["authority_protected_merge_commit"] = "a" * 40
    gate["v4_contract_protected_commit"] = "b" * 40
    gate["filter_authority_protected_commit"] = "c" * 40
    return metadata


@pytest.mark.parametrize("profile", ["pack", "rbr_short", "atr_shock"])
def test_post_pin_spoofed_correct_label_callable_is_rejected(monkeypatch, profile):
    metadata = _post_pin_metadata()
    monkeypatch.setattr(G, "load_metadata", lambda: metadata)
    spec = G.CLOSED_DELEGATE_REGISTRY[profile]

    def spoofed(_argv=None):
        raise AssertionError("spoofed delegate must never run")

    spoofed.__module__ = spec["module"]
    spoofed.__qualname__ = spec["qualname"]
    fake_module = SimpleNamespace(
        __file__=str(ROOT.joinpath(*spec["module"].split(".")).with_suffix(".py")),
        main=spoofed,
    )
    monkeypatch.setattr(G.importlib, "import_module", lambda _: fake_module)
    with pytest.raises(G.V4ExecutionBarrier, match="callable source digest changed"):
        G.guarded_dispatch(
            profile,
            PROFILE_IDS[profile],
            is_ancestor=lambda *_: True,
            is_exact_clean_protected_head=lambda *_: True,
        )


@pytest.mark.parametrize("profile", ["pack", "rbr_short", "atr_shock"])
def test_exact_closed_registry_delegate_resolves_by_identity_and_digest(profile):
    metadata = G.load_metadata()
    delegate = G.resolve_bound_delegate(profile, metadata["profiles"][profile])
    spec = G.CLOSED_DELEGATE_REGISTRY[profile]
    assert delegate.__module__ == spec["module"]
    assert delegate.__qualname__ == spec["qualname"]


@pytest.mark.parametrize("entry", [PACK, RBR, ATR])
def test_public_v4_entrypoints_are_pre_protection_blocked(entry, capsys):
    assert entry.main([]) == 3
    assert "NOT_AUTHORIZED_PRE_PROTECTED_AUTHORITY" in capsys.readouterr().err


def test_atr_delegate_resolution_is_reachable_only_after_hypothetical_final_pins(monkeypatch):
    metadata = _post_pin_metadata()
    calls = []
    monkeypatch.setattr(G, "load_metadata", lambda: metadata)
    monkeypatch.setattr(
        G,
        "resolve_bound_delegate",
        lambda profile, saved: calls.append((profile, saved)) or (lambda argv: 7),
    )
    assert G.guarded_dispatch(
        "atr_shock",
        PROFILE_IDS["atr_shock"],
        argv=(),
        is_ancestor=lambda *_: True,
        is_exact_clean_protected_head=lambda *_: True,
    ) == 7
    assert calls == [("atr_shock", metadata["profiles"]["atr_shock"])]


def test_post_pin_execution_requires_clean_exact_protected_head(monkeypatch):
    metadata = _post_pin_metadata()
    monkeypatch.setattr(G, "load_metadata", lambda: metadata)
    calls = []
    with pytest.raises(G.V4ExecutionBarrier, match="clean HEAD exactly equal"):
        G.require_execution_authority(
            "pack",
            is_ancestor=lambda *args: calls.append(args) or True,
            is_exact_clean_protected_head=lambda *_: False,
        )
    assert len(calls) == 3


def test_tampered_filter_authority_refuses_before_delegate_import(monkeypatch, tmp_path):
    metadata = _post_pin_metadata()
    original_contained = G._contained
    filter_rel = metadata["authority_artifacts"]["exchange_filter_authority"]["path"]
    tampered = tmp_path / "exchange_filter_authority.v3.json"
    tampered.write_bytes((ROOT / filter_rel).read_bytes() + b"\n")
    touched = {"import": 0, "ancestor": 0, "head": 0}

    def contained(rel):
        return tampered if rel == filter_rel else original_contained(rel)

    monkeypatch.setattr(G, "load_metadata", lambda: metadata)
    monkeypatch.setattr(G, "_contained", contained)
    monkeypatch.setattr(G.importlib, "import_module", lambda *_: touched.__setitem__("import", 1))
    with pytest.raises(G.V4ExecutionBarrier, match="exchange-filter authority byte hash changed"):
        G.guarded_dispatch(
            "pack",
            PROFILE_IDS["pack"],
            is_ancestor=lambda *_: touched.__setitem__("ancestor", 1) or True,
            is_exact_clean_protected_head=lambda *_: touched.__setitem__("head", 1) or True,
        )
    assert touched == {"import": 0, "ancestor": 0, "head": 0}


def test_strategy_logic_and_v3_runner_bytes_unchanged_except_reviewed_filter_loader():
    for rel, expected in LEGACY_BYTES.items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == expected


def test_runner_metadata_exactly_matches_v4_overlay_ids_authority_and_multiplicity():
    metadata = json.loads(G.METADATA_PATH.read_text(encoding="utf-8"))
    for name, profile in metadata["profiles"].items():
        overlay = json.loads((ROOT / profile["overlay_path"]).read_text(encoding="utf-8"))
        assert [row["v4_candidate_id"] for row in overlay["identity_rebinds"]] == profile["candidate_ids"]
        assert overlay["authority_artifacts"] == metadata["authority_artifacts"]
        append = overlay["multiplicity_append"]
        assert append.get("prior_registered_packet_count", append.get("prior_family_size")) == \
            profile["multiplicity"]["prior"]
        assert append["appended_v4_trial_count"] == profile["multiplicity"]["append"]
        assert append.get("resulting_registered_packet_count", append.get("resulting_family_size")) == \
            profile["multiplicity"]["resulting"]
        expected = ("EXACT_REVIEWED_CANDIDATE_13_CLOSED_REGISTRY_PIN_NOT_EXECUTABLE_PRE_PROTECTION"
                    if name == "atr_shock" else
                    "EXACT_CLOSED_REGISTRY_PIN_NOT_EXECUTABLE_PRE_PROTECTION")
        assert profile["delegate_pin_status"] == expected


def test_atr_delegate_and_review_tests_match_independently_accepted_f137_bytes():
    assert hashlib.sha256(
        (ROOT / "tools/research/atr_shock_snapback_4h_causal_v3_runner.py").read_bytes()
    ).hexdigest() == "9843582b04d42ccafe1adc79ef1cbd702cf6bd11b9916ec38cc129bded8bca59"
    assert hashlib.sha256(
        (ROOT / "tests/test_atr_shock_snapback_4h_causal_v3_runner.py").read_bytes()
    ).hexdigest() == "d9b954bbdd20877958f57866ceee498530fbd4e54c3c8d15cdc491d3a2b1a6de"
