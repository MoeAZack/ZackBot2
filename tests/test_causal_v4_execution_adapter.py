"""Hostile metadata/fake-engine tests; no archive, market, or outcome row is opened."""
import argparse
import copy
import json
import subprocess
from pathlib import Path

import pytest

from tools.research import causal_v4_execution_adapter as A
from tools.research import causal_v4_runner_rebind as G
from tools.research import strategy_causal_pack_01_runner as PACK_V3


ROOT = Path(__file__).resolve().parents[1]
TEST_HEAD = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _profile(name="pack"):
    return G.load_metadata()["profiles"][name]


def test_overlay_binds_exact_source_bytes_and_only_allowed_delta(tmp_path):
    overlay, source, digest = A.validate_overlay(ROOT, _profile())
    assert overlay["source_contract"]["immutable_in_full"] is True
    assert source["contract"] == "STRATEGY-CAUSAL-PACK-01"
    assert len(digest) == 64
    hostile = copy.deepcopy(overlay)
    hostile["immutable_rules"]["overlays_exactly"].append("folds")
    raw = json.dumps(hostile).encode()
    path = tmp_path / "hostile.json"; path.write_bytes(raw)
    profile = copy.deepcopy(_profile())
    profile["overlay_path"] = str(path.relative_to(tmp_path)).replace("\\", "/")
    profile["overlay_byte_sha256"] = __import__("hashlib").sha256(raw).hexdigest()
    with pytest.raises(A.AdapterError, match="immutable-source delta"):
        A.validate_overlay(tmp_path, profile)


def test_rebound_contract_preserves_old_trials_and_appends_v4_atr_family():
    profile = _profile("atr_shock")
    overlay, source, _ = A.validate_overlay(ROOT, profile)
    rebound, ids = A._rebound_contract(source, overlay)
    ordered = rebound["identity_and_multiplicity"]["family_members_ordered"]
    assert ordered[-4:] == [*ids, *ids.values()]
    assert len(ordered) == profile["multiplicity"]["resulting"]


@pytest.mark.parametrize("profile_name", ["pack", "rbr_short", "atr_shock"])
def test_rebound_contract_uses_exact_v3_filter_authority(profile_name):
    profile = _profile(profile_name)
    overlay, source, _ = A.validate_overlay(ROOT, profile)
    rebound, _ = A._rebound_contract(source, overlay)
    authority = overlay["authority_artifacts"]["exchange_filter_authority"]
    if profile_name == "pack":
        actual = {
            "path": rebound["data"]["exchange_filter_authority"],
            "byte_sha256": rebound["data"]["exchange_filter_authority_sha256"],
        }
    elif profile_name == "rbr_short":
        actual = {
            "path": rebound["pit_authority"]["exchange_filter_authority_path"],
            "byte_sha256": rebound["pit_authority"]["exchange_filter_authority_sha256"],
        }
    else:
        actual = rebound["exchange_filter_authority"]
    assert actual["path"] == authority["path"]
    assert actual["byte_sha256"] == authority["byte_sha256"]


def test_unknown_argv_and_candidate_mismatch_refuse_before_delegate(monkeypatch, tmp_path):
    calls = []
    def delegate(argv):
        calls.append(argv); return 0
    delegate.__module__ = PACK_V3.__name__
    base = ["--candidate", _profile()["candidate_ids"][0], "--out", str(tmp_path / "p.json"),
            "--identity-dir", str(tmp_path / "ids")]
    with pytest.raises(A.AdapterError, match="unrecognized arguments"):
        A.execute("pack", _profile(), delegate, candidate_id=None,
                  argv=base + ["--surprise"], root=ROOT)
    with pytest.raises(A.AdapterError, match="differs from argv"):
        A.execute("pack", _profile(), delegate, candidate_id=_profile()["candidate_ids"][1],
                  argv=base, root=ROOT)
    assert calls == []


def test_fake_engine_gets_v4_contract_authority_and_reserves_before_loader(tmp_path, monkeypatch):
    candidate = _profile()["candidate_ids"][0]
    out = tmp_path / "packet.json"; identities = tmp_path / "ids"
    seen = []
    monkeypatch.setattr(PACK_V3, "evaluate_raw_candidate", lambda unused, pf, **kwargs: {
        "candidate_id": pf.candidate_id, "report": {
            "raw_p_value": .02,
            "holm_adjusted_p_value": {"status": "PENDING_FROZEN_PACK_ASSEMBLY",
                                        "worst_rank_upper_bound": .06}}})
    def delegate(argv):
        parser = argparse.ArgumentParser()
        parser.add_argument("--candidate"); parser.add_argument("--out")
        parser.add_argument("--identity-dir"); parser.add_argument("--archive-store")
        args = parser.parse_args(argv)
        def loader():
            claims = list(Path(args.identity_dir).glob("*.claim.json"))
            assert len(claims) == 1
            seen.append((PACK_V3.ARCHIVE_MANIFEST_PATH.name, PACK_V3.UNIVERSE_PATH.name,
                         PACK_V3.FILTER_AUTHORITY_PATH.name,
                         json.loads(claims[0].read_text())["candidate_id"]))
            return object()
        PACK_V3._run_with_loader(
            args.candidate, args.out, loader,
            lambda adapter, pf: PACK_V3.evaluate_raw_candidate(adapter, pf),
            identity_dir=args.identity_dir, head_fn=lambda: "f" * 40, dirty_fn=lambda: False)
        return 0
    delegate.__module__ = PACK_V3.__name__
    argv = ["--candidate", candidate, "--out", str(out), "--identity-dir", str(identities)]
    assert A.execute("pack", _profile(), delegate, candidate_id=candidate, argv=argv, root=ROOT) == 0
    packet = json.loads(out.read_text())
    assert seen == [("binance-um-archive-precutoff-monthly-rest-v5.json.gz",
                     "pit-top40-qv30d-precutoff-monthly-rest-v8.json",
                     "exchange_filter_authority.v3.json", candidate)]
    assert packet["candidate_id"] == packet["integrity"]["candidate_id"] == candidate
    assert packet["format"] == "zackbot.strategy-causal-pack-01/4"
    assert packet["integrity"]["contract_sha256"] != PACK_V3.CONTRACT_SHA256
    assert packet["integrity"]["causal_v4_overlay_sha256"] == _profile()["overlay_byte_sha256"]
    assert packet["multiplicity_append"]["resulting_registered_packet_count"] == 5
    assert packet["report"]["holm_adjusted_p_value"]["worst_rank_upper_bound"] == .1
    with pytest.raises(A.AdapterError, match="output exists"):
        A.execute("pack", _profile(), delegate, candidate_id=candidate, argv=argv, root=ROOT)


def test_legacy_v3_globals_are_restored_after_adapter_failure(tmp_path):
    original_ids = PACK_V3.CANDIDATE_IDS
    original_manifest = PACK_V3.ARCHIVE_MANIFEST_PATH
    original_filters = PACK_V3.FILTER_AUTHORITY_PATH
    def explode(argv):
        assert PACK_V3.CANDIDATE_IDS == tuple(_profile()["candidate_ids"])
        raise RuntimeError("fake stop")
    explode.__module__ = PACK_V3.__name__
    argv = ["--candidate", _profile()["candidate_ids"][0], "--out", str(tmp_path / "p.json"),
            "--identity-dir", str(tmp_path / "ids")]
    with pytest.raises(RuntimeError, match="fake stop"):
        A.execute("pack", _profile(), explode, candidate_id=None, argv=argv, root=ROOT)
    assert PACK_V3.CANDIDATE_IDS == original_ids
    assert PACK_V3.ARCHIVE_MANIFEST_PATH == original_manifest
    assert PACK_V3.FILTER_AUTHORITY_PATH == original_filters


def test_filter_authority_byte_mismatch_refuses_before_delegate(monkeypatch, tmp_path):
    calls = []
    def delegate(argv):
        calls.append(argv)
        return 0
    delegate.__module__ = PACK_V3.__name__
    authority_path = ROOT / "contracts/strategy_causal_pack_01/exchange_filter_authority.v3.json"
    original = Path.read_bytes
    def hostile_read(path):
        raw = original(path)
        return raw + b" " if path.resolve() == authority_path.resolve() else raw
    monkeypatch.setattr(Path, "read_bytes", hostile_read)
    argv = ["--candidate", _profile()["candidate_ids"][0], "--out", str(tmp_path / "p.json"),
            "--identity-dir", str(tmp_path / "ids")]
    with pytest.raises(A.AdapterError, match="exchange-filter authority bytes changed"):
        A.execute("pack", _profile(), delegate, candidate_id=None, argv=argv, root=ROOT)
    assert calls == []


def _fake_packet(tmp_path, profile_name, candidate, head=None, raw_p=.04):
    head = head or TEST_HEAD
    profile = _profile(profile_name)
    overlay, source, contract_digest = A.validate_overlay(ROOT, profile)
    formats = {"pack": "zackbot.strategy-causal-pack-01/4",
               "atr_shock": "zackbot.atr-shock-snapback-4h-causal-v4/1"}
    identity = __import__("hashlib").sha256(A._canonical({
        "contract_sha256": contract_digest, "git_head": head, "candidate_id": candidate})).hexdigest()
    if profile_name == "pack":
        report = {key: 0 for key in source["required_report"]}
        report.update(raw_p_value=raw_p, holm_adjusted_p_value={
            "status": "PENDING_FROZEN_PACK_ASSEMBLY",
            "worst_rank_upper_bound": min(1.0, 5 * raw_p)})
        gates = {"required_reports": False}
    else:
        family = json.loads((ROOT / overlay["immutable_trial_history"][0]["path"]).read_text())
        trials = [dict(row) for row in family["trials"]]
        for v4_id in profile["candidate_ids"]:
            trials.append({"trial_id": v4_id,
                           "status": ("EVALUATED_CAUSAL_V4_PENDING_PAIRED_RECONCILIATION"
                                      if v4_id == candidate else "REGISTERED_CAUSAL_V4"),
                           "raw_p_value": raw_p if v4_id == candidate else None})
        report = {"raw_p_value": raw_p,
                  "holm_adjusted_p_value": {
                      "status": "PENDING_PAIRED_SIDE_FAMILY_ASSEMBLY", "value": None},
                  "full_candidate_ledger": {"trials": trials,
                      "reconciliation_status": "PENDING_CAUSAL_V4_SIDE_FAMILY"}}
        gates = {"required_reports": True, "full_ledger_and_multiplicity_disclosure": True}
    value = {"format": formats[profile_name], "candidate_id": candidate,
             "side": "long" if "long" in candidate else "short",
             "report": report, "promotion_gates": gates, "promotable": False,
             "multiplicity_append": overlay["multiplicity_append"],
             "integrity": {"candidate_id": candidate, "contract_sha256": contract_digest,
                           "git_head": head, "evaluation_identity": identity,
                           "causal_v4_overlay_sha256": profile["overlay_byte_sha256"],
                           "coverage_preflight_digest": overlay["authority_artifacts"]["coverage_preflight"]["content_digest"],
                           "manifest_digest": overlay["authority_artifacts"]["manifest"]["content_digest"],
                           "pit_universe_digest": overlay["authority_artifacts"]["pit_universe"]["content_digest"],
                           "manifest_file_sha256": overlay["authority_artifacts"]["manifest"]["byte_sha256"],
                           "universe_file_sha256": overlay["authority_artifacts"]["pit_universe"]["byte_sha256"]}}
    value["packet_sha256"] = __import__("hashlib").sha256(A._canonical(value)).hexdigest()
    path = tmp_path / f"{candidate}.json"; path.write_text(json.dumps(value))
    ids = tmp_path / "ids"; ids.mkdir(exist_ok=True)
    (ids / f"{identity}.claim.json").write_text(json.dumps({
        "state": "COMPLETED", "identity": identity, "output": str(path.resolve()),
        "packet_sha256": value["packet_sha256"]}))
    return path


def _resign_packet(path, identity_dir):
    value = json.loads(path.read_text())
    value.pop("packet_sha256", None)
    value["packet_sha256"] = __import__("hashlib").sha256(A._canonical(value)).hexdigest()
    path.write_text(json.dumps(value))
    identity = value.get("integrity", {}).get("evaluation_identity") if isinstance(value.get("integrity"), dict) else None
    if identity:
        (identity_dir / f"{identity}.claim.json").write_text(json.dumps({
            "state": "COMPLETED", "identity": identity, "output": str(path.resolve()),
            "packet_sha256": value["packet_sha256"]}))


@pytest.mark.parametrize("profile,extra", [
    ("pack", ["--candidate-packet", "a", "--candidate-packet", "b"]),
    ("atr_shock", ["--long-packet", "a", "--short-packet", "b"]),
])
def test_duplicate_singletons_and_mixed_modes_are_rejected(profile, extra, tmp_path):
    candidate = _profile(profile)["candidate_ids"][0]
    base = ["--out", str(tmp_path / "o"), "--identity-dir", str(tmp_path / "ids")]
    with pytest.raises(A.AdapterError, match="duplicate singleton"):
        A._strict_parser(profile, tuple(_profile(profile)["candidate_ids"]),
                         base + ["--out", "again", "--candidate", candidate])
    with pytest.raises(A.AdapterError, match="no candidate"):
        A._strict_parser(profile, tuple(_profile(profile)["candidate_ids"]),
                         base + extra + ["--candidate", candidate])
    with pytest.raises(A.AdapterError, match="duplicate singleton"):
        A._strict_parser(profile, tuple(_profile(profile)["candidate_ids"]),
                         [f"--out={tmp_path / 'one'}", "--out", str(tmp_path / "two"),
                          f"--identity-dir={tmp_path / 'ids'}", "--candidate", candidate])


def test_duplicate_assembly_packet_paths_are_rejected(tmp_path):
    base = ["--out", str(tmp_path / "o"), "--identity-dir", str(tmp_path / "ids")]
    with pytest.raises(A.AdapterError, match="distinct"):
        A._strict_parser("pack", tuple(_profile()["candidate_ids"]),
                         base + ["--candidate-packet", "same", "--candidate-packet", "same"])
    with pytest.raises(A.AdapterError, match="distinct"):
        A._strict_parser("atr_shock", tuple(_profile("atr_shock")["candidate_ids"]),
                         base + ["--long-packet=same", "--short-packet", "same"])


@pytest.mark.parametrize("mutation,match", [
    (lambda value: value.update(integrity=[]), "integrity must be an object"),
    (lambda value: value["integrity"].update(git_head="ABC"), "git head"),
    (lambda value: value["integrity"].update(evaluation_identity="0" * 64), "identity/authority"),
    (lambda value: value.update(report={"raw_p_value": .1}), "required report is incomplete"),
])
def test_pack_forged_packet_shapes_are_adapter_errors(tmp_path, mutation, match):
    candidate = _profile()["candidate_ids"][0]
    path = _fake_packet(tmp_path, "pack", candidate)
    value = json.loads(path.read_text()); mutation(value); path.write_text(json.dumps(value))
    _resign_packet(path, tmp_path / "ids")
    overlay, source, digest = A.validate_overlay(ROOT, _profile())
    with pytest.raises(A.AdapterError, match=match):
        A._verified_v4_packet(path, profile=_profile(), contract_digest=digest,
            candidate_ids=tuple(_profile()["candidate_ids"]),
            expected_format="zackbot.strategy-causal-pack-01/4",
            identity_dir=tmp_path / "ids", authority=overlay["authority_artifacts"],
            source_contract=source, overlay=overlay, expected_head=TEST_HEAD)


def test_atr_forged_side_and_family_schema_are_rejected(tmp_path):
    profile = _profile("atr_shock"); candidate = profile["candidate_ids"][0]
    path = _fake_packet(tmp_path, "atr_shock", candidate)
    value = json.loads(path.read_text())
    value["side"] = "short"
    value["report"]["full_candidate_ledger"]["trials"].pop()
    path.write_text(json.dumps(value)); _resign_packet(path, tmp_path / "ids")
    overlay, source, digest = A.validate_overlay(ROOT, profile)
    with pytest.raises(A.AdapterError, match="side does not match"):
        A._verified_v4_packet(path, profile=profile, contract_digest=digest,
            candidate_ids=tuple(profile["candidate_ids"]),
            expected_format="zackbot.atr-shock-snapback-4h-causal-v4/1",
            identity_dir=tmp_path / "ids", authority=overlay["authority_artifacts"],
            source_contract=source, overlay=overlay, expected_head=TEST_HEAD)


def test_pack_assembly_builds_five_way_append_only_holm(tmp_path):
    ids = _profile()["candidate_ids"]
    paths = [_fake_packet(tmp_path, "pack", candidate, raw_p=.01 + i * .02)
             for i, candidate in enumerate(ids)]
    out = tmp_path / "pack.json"
    args = ["--candidate-packet", str(paths[0]), "--candidate-packet", str(paths[1]),
            "--out", str(out), "--identity-dir", str(tmp_path / "ids")]
    overlay, source, digest = A.validate_overlay(ROOT, _profile())
    ns = A._strict_parser("pack", tuple(ids), args)
    assert A._assemble_pack(ns, _profile(), overlay, source, digest, ROOT,
                            clean_head_fn=lambda root: TEST_HEAD) == 0
    result = json.loads(out.read_text())
    assert len(result["full_candidate_ledger"]) == 5
    assert set(result["holm_adjusted_p_values"]) == {
        "pullback_rsi_short_h1_causal_v3", "exp_mom_short_c27_causal_v3",
        "volume_pressure_inventory_short_1h_causal_v3", *ids}
    assert all(result["raw_p_values"][old] == 1.0 for old in (
        "pullback_rsi_short_h1_causal_v3", "exp_mom_short_c27_causal_v3",
        "volume_pressure_inventory_short_1h_causal_v3"))


def test_atr_family_assembly_builds_six_rows_and_rejects_wrong_head(tmp_path):
    ids = _profile("atr_shock")["candidate_ids"]
    paths = [_fake_packet(tmp_path, "atr_shock", ids[0], raw_p=.02),
             _fake_packet(tmp_path, "atr_shock", ids[1], raw_p=.03)]
    base = ["--long-packet", str(paths[0]), "--short-packet", str(paths[1]),
            "--out", str(tmp_path / "family.json"), "--identity-dir", str(tmp_path / "ids")]
    profile = _profile("atr_shock"); overlay, _, digest = A.validate_overlay(ROOT, profile)
    _, source, _ = A.validate_overlay(ROOT, profile)
    assert A._assemble_atr(A._strict_parser("atr_shock", tuple(ids), base), profile,
                           overlay, source, digest, ROOT,
                           clean_head_fn=lambda root: TEST_HEAD) == 0
    result = json.loads((tmp_path / "family.json").read_text())
    assert len(result["full_candidate_ledger"]) == 6
    assert len(result["holm_adjusted_p_values"]) == 6
    assert set(result["side_diagnostics"]) == set(ids)
    bad = _fake_packet(tmp_path, "atr_shock", ids[1], head="b" * 40, raw_p=.03)
    hostile = ["--long-packet", str(paths[0]), "--short-packet", str(bad),
               "--out", str(tmp_path / "bad.json"), "--identity-dir", str(tmp_path / "ids")]
    with pytest.raises(A.AdapterError, match="exact clean repository HEAD"):
        A._assemble_atr(A._strict_parser("atr_shock", tuple(ids), hostile), profile,
                        overlay, source, digest, ROOT,
                        clean_head_fn=lambda root: TEST_HEAD)


def test_failed_pack_serialization_consumes_claim_without_partial_output(tmp_path, monkeypatch):
    ids = _profile()["candidate_ids"]
    paths = [_fake_packet(tmp_path, "pack", candidate) for candidate in ids]
    out = tmp_path / "broken.json"; identity_dir = tmp_path / "ids"
    argv = ["--candidate-packet", str(paths[0]), "--candidate-packet", str(paths[1]),
            "--out", str(out), "--identity-dir", str(identity_dir)]
    profile = _profile(); overlay, source, digest = A.validate_overlay(ROOT, profile)
    original = A._exclusive
    def hostile(path, value):
        if str(path).startswith(str(out)):
            raise TypeError("serialization failed")
        return original(path, value)
    monkeypatch.setattr(A, "_exclusive", hostile)
    with pytest.raises(TypeError, match="serialization failed"):
        A._assemble_pack(A._strict_parser("pack", tuple(ids), argv), profile,
                         overlay, source, digest, ROOT,
                         clean_head_fn=lambda root: TEST_HEAD)
    assert not out.exists()
    claims = list(identity_dir.glob("pack-v4-*.claim.json"))
    assert len(claims) == 1 and json.loads(claims[0].read_text())["state"] == "FAILED_CONSUMED"


@pytest.mark.parametrize("profile_name", ["pack", "atr_shock"])
@pytest.mark.parametrize("wrong_head", ["0" * 40, "existing-other"])
def test_assembly_rejects_nonexistent_and_existing_other_heads_before_claim(
        tmp_path, profile_name, wrong_head):
    if wrong_head == "existing-other":
        wrong_head = subprocess.check_output(
            ["git", "rev-parse", "HEAD^"], cwd=ROOT, text=True).strip()
        assert wrong_head != TEST_HEAD
    profile = _profile(profile_name); ids = profile["candidate_ids"]
    paths = [_fake_packet(tmp_path, profile_name, ids[0], head=wrong_head),
             _fake_packet(tmp_path, profile_name, ids[1], head=TEST_HEAD)]
    output = tmp_path / "must-not-exist.json"; identity_dir = tmp_path / "ids"
    overlay, source, digest = A.validate_overlay(ROOT, profile)
    if profile_name == "pack":
        argv = ["--candidate-packet", str(paths[0]), "--candidate-packet", str(paths[1]),
                "--out", str(output), "--identity-dir", str(identity_dir)]
        invoke = lambda: A._assemble_pack(
            A._strict_parser("pack", tuple(ids), argv), profile, overlay, source, digest, ROOT,
            clean_head_fn=lambda root: TEST_HEAD)
        claim_glob = "pack-v4-*.claim.json"
    else:
        argv = ["--long-packet", str(paths[0]), "--short-packet", str(paths[1]),
                "--out", str(output), "--identity-dir", str(identity_dir)]
        invoke = lambda: A._assemble_atr(
            A._strict_parser("atr_shock", tuple(ids), argv), profile, overlay, source, digest, ROOT,
            clean_head_fn=lambda root: TEST_HEAD)
        claim_glob = "atr-family-v4-*.claim.json"
    with pytest.raises(A.AdapterError, match="exact clean repository HEAD"):
        invoke()
    assert not output.exists()
    assert list(identity_dir.glob(claim_glob)) == []


@pytest.mark.parametrize("profile_name", ["pack", "atr_shock"])
def test_dirty_repo_head_refuses_before_packet_or_claim(profile_name, tmp_path):
    profile = _profile(profile_name); ids = profile["candidate_ids"]
    output = tmp_path / "must-not-exist.json"; identity_dir = tmp_path / "ids"
    overlay, source, digest = A.validate_overlay(ROOT, profile)
    def dirty(_root):
        raise A.AdapterError("dirty worktree: causal-v4 assembly refused")
    if profile_name == "pack":
        argv = ["--candidate-packet", str(tmp_path / "missing-a"),
                "--candidate-packet", str(tmp_path / "missing-b"),
                "--out", str(output), "--identity-dir", str(identity_dir)]
        invoke = lambda: A._assemble_pack(
            A._strict_parser("pack", tuple(ids), argv), profile, overlay, source, digest, ROOT,
            clean_head_fn=dirty)
    else:
        argv = ["--long-packet", str(tmp_path / "missing-a"),
                "--short-packet", str(tmp_path / "missing-b"),
                "--out", str(output), "--identity-dir", str(identity_dir)]
        invoke = lambda: A._assemble_atr(
            A._strict_parser("atr_shock", tuple(ids), argv), profile, overlay, source, digest, ROOT,
            clean_head_fn=dirty)
    with pytest.raises(A.AdapterError, match="dirty worktree"):
        invoke()
    assert not output.exists() and not identity_dir.exists()
