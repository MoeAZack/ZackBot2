"""Exact, fail-closed execution adapter for the causal-v4 identity overlays.

The strategy engines remain the reviewed causal-v3 bytes.  This module validates
the overlay and its immutable source contract, then installs only the four
allowed in-memory deltas for one synchronous CLI call: candidate identity,
authority artifacts, authorization status, and append-only multiplicity.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence


class AdapterError(RuntimeError):
    pass


ATR_V4_COMPLETED_PACKET_HEAD = "6754a336652496952aaab82e42fbe9bd6171430a"


def _clean_repo_head(root: Path) -> str:
    """Return the exact clean commit checked out at root, without shell parsing."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
        ).stdout.strip()
        if re.fullmatch(r"[0-9a-f]{40}", head) is None:
            raise AdapterError("repository HEAD is not an exact lowercase commit id")
        subprocess.run(
            ["git", "cat-file", "-e", f"{head}^{{commit}}"], cwd=root,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            check=True,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AdapterError("exact repository HEAD could not be resolved as a commit") from exc
    if dirty:
        raise AdapterError("dirty worktree: causal-v4 assembly refused")
    return head


def _require_ancestor_packet_head(root: Path, packet_head: str, current_head: str) -> None:
    """Require the frozen evaluator head to exist in, and precede, this clean checkout."""
    if re.fullmatch(r"[0-9a-f]{40}", packet_head) is None:
        raise AdapterError("frozen candidate packet head is not exact lowercase hex")
    try:
        subprocess.run(
            ["git", "cat-file", "-e", f"{packet_head}^{{commit}}"], cwd=root,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            check=True,
        )
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", packet_head, current_head], cwd=root,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AdapterError(
            "frozen candidate packet head is not an ancestor of the clean repository HEAD"
        ) from exc


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _strict_parser(profile: str, candidate_ids: tuple[str, ...], argv: Sequence[str]) -> argparse.Namespace:
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise AdapterError(f"{profile}: invalid argv: {message}")
    argv = tuple(argv)
    singleton = {"--candidate", "--out", "--identity-dir", "--archive-store",
                 "--long-packet", "--short-packet"}
    for flag in singleton:
        occurrences = sum(token == flag or token.startswith(flag + "=") for token in argv)
        if occurrences > 1:
            raise AdapterError(f"{profile}: duplicate singleton argument {flag}")
    parser = Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--candidate", choices=candidate_ids)
    parser.add_argument("--out", required=True)
    parser.add_argument("--identity-dir", required=True)
    parser.add_argument("--archive-store")
    if profile == "pack":
        parser.add_argument("--candidate-packet", action="append")
    if profile == "atr_shock":
        parser.add_argument("--long-packet")
        parser.add_argument("--short-packet")
    args = parser.parse_args(argv)
    pack_assembly = profile == "pack" and args.candidate_packet is not None
    atr_assembly = profile == "atr_shock" and (args.long_packet is not None or args.short_packet is not None)
    if pack_assembly:
        if len(args.candidate_packet) != 2 or args.candidate is not None or args.archive_store is not None:
            raise AdapterError("pack: assembly requires exactly two --candidate-packet values and no candidate/archive store")
        if Path(args.candidate_packet[0]).resolve() == Path(args.candidate_packet[1]).resolve():
            raise AdapterError("pack: candidate packet paths must be distinct")
        args.mode = "assembly"
    elif atr_assembly:
        if (args.long_packet is None or args.short_packet is None
                or args.candidate is not None or args.archive_store is not None):
            raise AdapterError("atr_shock: family mode requires long+short packets and no candidate/archive store")
        if Path(args.long_packet).resolve() == Path(args.short_packet).resolve():
            raise AdapterError("atr_shock: long and short packet paths must be distinct")
        args.mode = "assembly"
    else:
        if profile != "rbr_short" and args.candidate is None:
            raise AdapterError(f"{profile}: candidate evaluation requires --candidate")
        if profile == "rbr_short" and args.candidate is None:
            args.candidate = candidate_ids[0]
        args.mode = "candidate"
    if args.mode == "candidate" and args.candidate is None:
        args.candidate = candidate_ids[0]
    return args


def _identity_rows(overlay: dict) -> list[dict]:
    rows = overlay.get("identity_rebinds")
    if isinstance(rows, dict):
        rows = [rows]
    if (not isinstance(rows, list) or not rows
            or any(set(row) != {"v3_candidate_id", "v4_candidate_id"} for row in rows)):
        raise AdapterError("identity_rebinds is not the closed v3-to-v4 mapping")
    return rows


def validate_overlay(root: Path, profile: dict) -> tuple[dict, dict, str]:
    path = root.joinpath(*profile["overlay_path"].split("/"))
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != profile["overlay_byte_sha256"]:
        raise AdapterError("v4 overlay bytes changed")
    overlay = json.loads(raw.decode("utf-8"), parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    allowed = {"candidate_identity", "authority_artifacts", "authorization_status", "multiplicity_append"}
    rules = overlay.get("immutable_rules") or {}
    if (overlay.get("format") != "zb-causal-v4-authority-rebind/1"
            or overlay.get("status") != "NOT_AUTHORIZED_PRE_PROTECTED_AUTHORITY"
            or overlay.get("scope", {}).get("metadata_only") is not True
            or overlay.get("source_contract", {}).get("immutable_in_full") is not True
            or set(rules.get("overlays_exactly", ())) != allowed):
        raise AdapterError("overlay does not preserve the exact immutable-source delta")
    source = overlay["source_contract"]
    source_path = root.joinpath(*source["path"].split("/"))
    source_raw = source_path.read_bytes()
    if hashlib.sha256(source_raw).hexdigest() != source["byte_sha256"]:
        raise AdapterError("immutable source-contract bytes changed")
    contract = json.loads(source_raw.decode("utf-8"))
    rows = _identity_rows(overlay)
    if [row["v4_candidate_id"] for row in rows] != profile["candidate_ids"]:
        raise AdapterError("overlay candidate order differs from the closed runner profile")
    digest = hashlib.sha256(_canonical({
        "source_contract_sha256": source["byte_sha256"],
        "overlay_byte_sha256": profile["overlay_byte_sha256"],
    })).hexdigest()
    return overlay, contract, digest


def _replace(value, replacements: dict[str, str]):
    if isinstance(value, dict):
        return {key: _replace(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, replacements) for item in value]
    return replacements.get(value, value) if isinstance(value, str) else value


def _authority_replacements(contract: dict, overlay: dict) -> dict[str, str]:
    new = overlay["authority_artifacts"]
    old_manifest = old_universe = None
    for key in ("pit_and_archive_authority", "pit_authority", "data"):
        section = contract.get(key) or {}
        old_manifest = old_manifest or section.get("manifest_path") or section.get("manifest")
        old_universe = old_universe or section.get("universe_path") or section.get("pit_universe_path")
    replacements = {}
    if old_manifest:
        replacements[old_manifest] = new["manifest"]["path"]
    if old_universe:
        replacements[old_universe] = new["pit_universe"]["path"]
    # All three immutable v3 contracts point at the same v2 authority, but
    # express the binding in different sections.  Replace only the exact
    # frozen path and byte digest; narrative text and all economic rules stay
    # unchanged.
    replacements[
        "contracts/strategy_causal_pack_01/exchange_filter_authority.v2.json"
    ] = new["exchange_filter_authority"]["path"]
    replacements[
        "9463632b78004b367439b4fa3eae22523ffedf87b920c24cff8f5241d9fea434"
    ] = new["exchange_filter_authority"]["byte_sha256"]
    old_values = {
        "manifest": ("manifest_gzip_byte_sha256", "manifest_content_digest"),
        "pit_universe": ("universe_byte_sha256", "pit_universe_sha256", "universe_content_digest", "pit_universe_digest"),
    }
    for artifact, keys in old_values.items():
        for section in contract.values():
            if not isinstance(section, dict):
                continue
            for key in keys:
                if isinstance(section.get(key), str):
                    target = "byte_sha256" if "sha256" in key else "content_digest"
                    replacements[section[key]] = new[artifact][target]
    return replacements


def _rebound_contract(contract: dict, overlay: dict) -> tuple[dict, dict[str, str]]:
    rows = _identity_rows(overlay)
    ids = {row["v3_candidate_id"]: row["v4_candidate_id"] for row in rows}
    rebound = _replace(copy.deepcopy(contract), {**ids, **_authority_replacements(contract, overlay)})
    if "identity_and_multiplicity" in rebound:
        ordered = rebound["identity_and_multiplicity"].get("family_members_ordered", [])
        # Replacement above turns old causal identities into v4 engine cells;
        # append the immutable old trials back so history grows rather than relabels.
        old = [row["v3_candidate_id"] for row in rows]
        rebound["identity_and_multiplicity"]["family_members_ordered"] = [
            item for item in ordered if item not in ids.values()
        ] + old + [row["v4_candidate_id"] for row in rows]
    return rebound, ids


@contextmanager
def _patched(module, values: dict) -> Iterator[None]:
    saved = {name: getattr(module, name) for name in values}
    try:
        for name, value in values.items():
            setattr(module, name, value)
        yield
    finally:
        for name, value in saved.items():
            setattr(module, name, value)


def _holm(inputs: dict[str, float]) -> dict[str, float]:
    ordered = sorted(inputs.items(), key=lambda row: (row[1], row[0]))
    adjusted, running, size = {}, 0.0, len(ordered)
    for rank, (trial_id, value) in enumerate(ordered):
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise AdapterError("Holm inputs must be finite probabilities")
        running = max(running, min(1.0, (size - rank) * value))
        adjusted[trial_id] = running
    return adjusted


def _exclusive(path: Path, value: dict) -> None:
    raw = json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw); fh.flush(); os.fsync(fh.fileno())


def _finish_claim(path: Path, value: dict) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    _exclusive(tmp, value)
    os.replace(tmp, path)


def _write_assembly(output: Path, claim: Path, identity: str, packet: dict) -> dict:
    tmp = Path(str(output) + f".{os.getpid()}.tmp")
    try:
        packet["packet_sha256"] = hashlib.sha256(_canonical(packet)).hexdigest()
        _exclusive(tmp, packet)
        try:
            os.link(tmp, output)
        except FileExistsError as exc:
            raise AdapterError("assembly output already exists") from exc
        tmp.unlink()
    except BaseException as exc:
        if tmp.exists():
            tmp.unlink()
        _finish_claim(claim, {"state": "FAILED_CONSUMED", "identity": identity,
                              "error_type": type(exc).__name__})
        raise
    _finish_claim(claim, {"state": "COMPLETED", "identity": identity,
                          "packet_sha256": packet["packet_sha256"],
                          "output": str(output.resolve())})
    return packet


def _verified_v4_packet(path: Path, *, profile: dict, contract_digest: str,
                        candidate_ids: tuple[str, ...], expected_format: str,
                        identity_dir: Path, authority: dict, source_contract: dict,
                        overlay: dict, expected_head: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"),
                           parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    except (OSError, UnicodeError, ValueError) as exc:
        raise AdapterError(f"candidate packet is unavailable or malformed: {path}") from exc
    if not isinstance(value, dict):
        raise AdapterError(f"candidate packet must be a JSON object: {path}")
    claimed = value.pop("packet_sha256", None)
    if claimed != hashlib.sha256(_canonical(value)).hexdigest():
        raise AdapterError(f"candidate packet checksum mismatch: {path}")
    value["packet_sha256"] = claimed
    integrity = value.get("integrity")
    if not isinstance(integrity, dict):
        raise AdapterError(f"candidate packet integrity must be an object: {path}")
    head = integrity.get("git_head")
    if not isinstance(head, str) or re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise AdapterError(f"candidate packet git head is not exact lowercase hex: {path}")
    if head != expected_head:
        raise AdapterError(f"candidate packet head differs from exact clean repository HEAD: {path}")
    expected_identity = hashlib.sha256(_canonical({
        "contract_sha256": contract_digest, "git_head": head,
        "candidate_id": value.get("candidate_id"),
    })).hexdigest()
    if (value.get("format") != expected_format or value.get("candidate_id") not in candidate_ids
            # The reviewed v3 ATR delegate predates the redundant integrity-level
            # candidate_id field.  Its top-level candidate id is already covered
            # by the packet checksum, evaluation identity and completed claim.
            # Accept that legacy omission, but never accept a conflicting value.
            or (integrity.get("candidate_id") != value.get("candidate_id")
                and not (expected_format == "zackbot.atr-shock-snapback-4h-causal-v4/1"
                         and "candidate_id" not in integrity))
            or integrity.get("contract_sha256") != contract_digest
            or integrity.get("evaluation_identity") != expected_identity
            or integrity.get("causal_v4_overlay_sha256") != profile["overlay_byte_sha256"]
            or integrity.get("coverage_preflight_digest") != authority["coverage_preflight"]["content_digest"]
            or integrity.get("manifest_digest") != authority["manifest"]["content_digest"]
            or integrity.get("pit_universe_digest") != authority["pit_universe"]["content_digest"]
            or integrity.get("manifest_file_sha256") != authority["manifest"]["byte_sha256"]
            or integrity.get("universe_file_sha256") != authority["pit_universe"]["byte_sha256"]):
        raise AdapterError(f"candidate packet v4 identity/authority mismatch: {path}")
    identity = expected_identity
    try:
        claim = json.loads((identity_dir / f"{identity}.claim.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise AdapterError(f"candidate packet claim is unavailable or malformed: {path}") from exc
    if claim != {"state": "COMPLETED", "identity": identity,
                 "output": str(path.resolve()), "packet_sha256": claimed}:
        raise AdapterError(f"candidate packet claim mismatch: {path}")
    report = value.get("report")
    gates = value.get("promotion_gates")
    if not isinstance(report, dict) or not isinstance(gates, dict):
        raise AdapterError(f"candidate packet report/gates must be objects: {path}")
    raw = report.get("raw_p_value")
    if type(raw) not in (int, float) or not math.isfinite(raw) or not 0 <= raw <= 1:
        raise AdapterError(f"candidate packet has no finite raw p-value: {path}")
    if value.get("promotable") is not False:
        raise AdapterError("candidate packet must remain non-promotable pending family assembly")
    if value.get("multiplicity_append") != overlay.get("multiplicity_append"):
        raise AdapterError(f"candidate packet multiplicity binding changed: {path}")
    if expected_format == "zackbot.strategy-causal-pack-01/4":
        required = source_contract.get("required_report")
        if not isinstance(required, list) or any(
                key not in report or (report[key] is None and key != "holm_adjusted_p_value")
                for key in required):
            raise AdapterError(f"PACK candidate packet required report is incomplete: {path}")
        pending = report.get("holm_adjusted_p_value")
        if (not isinstance(pending, dict)
                or set(pending) != {"status", "worst_rank_upper_bound"}
                or pending.get("status") != "PENDING_FROZEN_PACK_ASSEMBLY"
                or type(pending.get("worst_rank_upper_bound")) not in (int, float)):
            raise AdapterError(f"PACK candidate packet Holm status is not exact pending assembly: {path}")
        if gates.get("required_reports") is not False:
            raise AdapterError(f"PACK candidate packet must remain report-incomplete pending assembly: {path}")
    elif expected_format == "zackbot.atr-shock-snapback-4h-causal-v4/1":
        candidate = value["candidate_id"]
        expected_side = "long" if "_long_" in candidate else "short"
        if value.get("side") != expected_side:
            raise AdapterError(f"ATR candidate side does not match its v4 identity: {path}")
        if report.get("holm_adjusted_p_value") != {
                "status": "PENDING_PAIRED_SIDE_FAMILY_ASSEMBLY", "value": None}:
            raise AdapterError(f"ATR candidate Holm status is not exact pending family: {path}")
        family = report.get("full_candidate_ledger")
        trials = family.get("trials") if isinstance(family, dict) else None
        immutable_family = json.loads(
            (Path(__file__).resolve().parents[2] / overlay["immutable_trial_history"][0]["path"]
             ).read_text(encoding="utf-8"))
        immutable_rows = {row["trial_id"]: row for row in immutable_family["trials"]}
        immutable_ids = list(immutable_rows)
        expected_ids = set(immutable_ids) | set(candidate_ids)
        if (not isinstance(trials, list) or len(trials) != 6
                or len({row.get("trial_id") for row in trials if isinstance(row, dict)}) != 6
                or {row.get("trial_id") for row in trials if isinstance(row, dict)} != expected_ids
                or family.get("reconciliation_status") != "PENDING_CAUSAL_V4_SIDE_FAMILY"):
            raise AdapterError(f"ATR candidate six-trial family disclosure is invalid: {path}")
        by_id = {row["trial_id"]: row for row in trials}
        if any(by_id[trial_id] != row for trial_id, row in immutable_rows.items()):
            raise AdapterError(f"ATR candidate immutable prior rows changed: {path}")
        own = by_id[candidate]
        other = next(item for item in candidate_ids if item != candidate)
        if (own.get("status") != "EVALUATED_CAUSAL_V4_PENDING_PAIRED_RECONCILIATION"
                or own.get("raw_p_value") != raw
                or by_id[other].get("status") != "REGISTERED_CAUSAL_V4"
                or by_id[other].get("raw_p_value") is not None):
            raise AdapterError(f"ATR candidate v4 family row states are invalid: {path}")
        if (gates.get("required_reports") is not True
                or gates.get("full_ledger_and_multiplicity_disclosure") is not True):
            raise AdapterError(f"ATR candidate packet family gates are incomplete: {path}")
    return value


def _reserve_assembly(identity_dir: Path, prefix: str, identity: str, output: Path,
                      packet_hashes: dict) -> Path:
    claim = identity_dir / f"{prefix}-{identity}.claim.json"
    try:
        _exclusive(claim, {"state": "RESERVED", "identity": identity,
                           "source_packet_sha256": packet_hashes,
                           "output": str(output.resolve())})
    except FileExistsError as exc:
        raise AdapterError("this exact assembly identity is already consumed") from exc
    return claim


def _assemble_pack(args, profile: dict, overlay: dict, contract: dict,
                   contract_digest: str, root: Path, *, clean_head_fn=None) -> int:
    exact_head = (clean_head_fn or _clean_repo_head)(root)
    paths = [Path(value) for value in args.candidate_packet]
    packets = [_verified_v4_packet(path, profile=profile, contract_digest=contract_digest,
                                   candidate_ids=tuple(profile["candidate_ids"]),
                                   expected_format="zackbot.strategy-causal-pack-01/4",
                                   identity_dir=Path(args.identity_dir),
                                   authority=overlay["authority_artifacts"],
                                   source_contract=contract, overlay=overlay,
                                   expected_head=exact_head) for path in paths]
    by_id = {packet["candidate_id"]: packet for packet in packets}
    if len(by_id) != 2 or set(by_id) != set(profile["candidate_ids"]):
        raise AdapterError("pack assembly requires one packet for each v4 identity")
    heads = {packet["integrity"]["git_head"] for packet in packets}
    if len(heads) != 1:
        raise AdapterError("pack candidate packets have different evaluator heads")
    amendment_binding = overlay["immutable_trial_history"][0]
    amendment_path = root.joinpath(*amendment_binding["path"].split("/"))
    if hashlib.sha256(amendment_path.read_bytes()).hexdigest() != amendment_binding["byte_sha256"]:
        raise AdapterError("immutable PACK trial history bytes changed")
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    prior = [dict(row) for row in amendment["failed_trials_append"]]
    vpi = next(row for row in contract["candidates"]
               if row["candidate_id"] == "volume_pressure_inventory_short_1h_causal_v3")
    prior.append({"candidate_id": vpi["candidate_id"], "status": vpi["evaluation_status"],
                  "reason": vpi["data_incomplete_reason"], "outcome_rows_read": 0,
                  "economic_verdict": None, "retained_in_multiplicity": True})
    missing = overlay["multiplicity_append"]["holm_missing_prior_p_value"]
    inputs = {row["candidate_id"]: missing for row in prior}
    inputs.update({candidate: float(by_id[candidate]["report"]["raw_p_value"])
                   for candidate in profile["candidate_ids"]})
    adjusted = _holm(inputs)
    ledger = [{**row, "holm_input_p_value": missing,
               "holm_adjusted_p_value": adjusted[row["candidate_id"]]} for row in prior]
    for candidate in profile["candidate_ids"]:
        ledger.append({"candidate_id": candidate, "status": "EVALUATED_CAUSAL_V4",
                       "raw_p_value": inputs[candidate], "holm_input_p_value": inputs[candidate],
                       "holm_adjusted_p_value": adjusted[candidate],
                       "source_packet_sha256": by_id[candidate]["packet_sha256"]})
    hashes = {key: by_id[key]["packet_sha256"] for key in sorted(by_id)}
    identity = hashlib.sha256(_canonical({"contract_sha256": contract_digest,
                                          "git_head": next(iter(heads)),
                                          "source_packet_sha256": hashes})).hexdigest()
    output = Path(args.out)
    claim = _reserve_assembly(Path(args.identity_dir), "pack-v4", identity, output, hashes)
    result = {"format": "zackbot.strategy-causal-pack-01.assembled/4",
              "contract_sha256": contract_digest, "git_head": next(iter(heads)),
              "assembly_identity": identity, "source_packet_sha256": hashes,
              "raw_p_values": inputs, "holm_adjusted_p_values": adjusted,
              "full_candidate_ledger": ledger,
              "multiplicity_append": copy.deepcopy(overlay["multiplicity_append"])}
    _write_assembly(output, claim, identity, result)
    return 0


def _assemble_atr(args, profile: dict, overlay: dict, source_contract: dict,
                  contract_digest: str, root: Path, *, clean_head_fn=None,
                  packet_head: str = ATR_V4_COMPLETED_PACKET_HEAD) -> int:
    current_head = (clean_head_fn or _clean_repo_head)(root)
    _require_ancestor_packet_head(root, packet_head, current_head)
    paths = [Path(args.long_packet), Path(args.short_packet)]
    packets = [_verified_v4_packet(path, profile=profile, contract_digest=contract_digest,
                                   candidate_ids=tuple(profile["candidate_ids"]),
                                   expected_format="zackbot.atr-shock-snapback-4h-causal-v4/1",
                                   identity_dir=Path(args.identity_dir),
                                   authority=overlay["authority_artifacts"],
                                   source_contract=source_contract, overlay=overlay,
                                   expected_head=packet_head) for path in paths]
    by_id = {packet["candidate_id"]: packet for packet in packets}
    if len(by_id) != 2 or set(by_id) != set(profile["candidate_ids"]):
        raise AdapterError("ATR family requires one long and one short v4 packet")
    heads = {packet["integrity"]["git_head"] for packet in packets}
    if len(heads) != 1:
        raise AdapterError("ATR side packets have different evaluator heads")
    binding = overlay["immutable_trial_history"][0]
    family_path = root.joinpath(*binding["path"].split("/"))
    raw = family_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding["byte_sha256"]:
        raise AdapterError("immutable ATR family bytes changed")
    source = json.loads(raw.decode("utf-8"))
    if source.get("digest") != binding["content_digest"]:
        raise AdapterError("immutable ATR family content digest changed")
    missing = overlay["multiplicity_append"]["holm_missing_prior_p_value"]
    inputs = {row["trial_id"]: missing for row in source["trials"]}
    inputs.update({candidate: float(by_id[candidate]["report"]["raw_p_value"])
                   for candidate in profile["candidate_ids"]})
    adjusted = _holm(inputs)
    ledger = []
    for row in source["trials"]:
        ledger.append({**row, "holm_input_p_value": missing,
                       "holm_adjusted_p_value": adjusted[row["trial_id"]]})
    diagnostics = {}
    for candidate in profile["candidate_ids"]:
        row = {"trial_id": candidate, "side": by_id[candidate].get("side"),
               "status": "EVALUATED_CAUSAL_V4", "raw_p_value": inputs[candidate],
               "holm_input_p_value": inputs[candidate],
               "holm_adjusted_p_value": adjusted[candidate],
               "source_packet_sha256": by_id[candidate]["packet_sha256"]}
        ledger.append(row); diagnostics[candidate] = {
            "raw_p_value": inputs[candidate], "holm_adjusted_p_value": adjusted[candidate]}
    hashes = {key: by_id[key]["packet_sha256"] for key in sorted(by_id)}
    identity = hashlib.sha256(_canonical({"contract_sha256": contract_digest,
                                          "git_head": next(iter(heads)),
                                          "source_packet_sha256": hashes})).hexdigest()
    output = Path(args.out)
    claim = _reserve_assembly(Path(args.identity_dir), "atr-family-v4", identity, output, hashes)
    result = {"format": "zackbot.atr-shock-snapback-4h-causal-v4-family/1",
              "contract_sha256": contract_digest, "git_head": next(iter(heads)),
              "family_identity": identity, "source_packet_sha256": hashes,
              "raw_p_values": inputs, "holm_adjusted_p_values": adjusted,
              "side_diagnostics": diagnostics, "full_candidate_ledger": ledger,
              "multiplicity_append": copy.deepcopy(overlay["multiplicity_append"])}
    _write_assembly(output, claim, identity, result)
    return 0


def execute(profile_name: str, profile: dict, delegate, *, candidate_id: str | None,
            argv: Sequence[str] | None, root: Path) -> int:
    """Run one reviewed engine with an exact v4 overlay; no permissive argv forwarding."""
    overlay, source_contract, contract_digest = validate_overlay(root, profile)
    args = _strict_parser(profile_name, tuple(profile["candidate_ids"]), argv or ())
    if candidate_id is not None and (args.mode != "candidate" or candidate_id != args.candidate):
        raise AdapterError("explicit candidate identity differs from argv")
    if Path(args.out).exists() or Path(str(args.out) + ".tmp").exists():
        raise AdapterError("output exists or stale staging exists; overwrite refused")
    if args.mode == "assembly":
        if profile_name == "pack":
            return _assemble_pack(args, profile, overlay, source_contract, contract_digest, root)
        if profile_name == "atr_shock":
            return _assemble_atr(args, profile, overlay, source_contract, contract_digest, root)
        raise AdapterError(f"{profile_name}: assembly mode is not supported")
    rebound, ids = _rebound_contract(source_contract, overlay)
    module = __import__(delegate.__module__, fromlist=["*"])
    authority = overlay["authority_artifacts"]
    manifest = root.joinpath(*authority["manifest"]["path"].split("/"))
    universe = root.joinpath(*authority["pit_universe"]["path"].split("/"))
    filter_authority = root.joinpath(*authority["exchange_filter_authority"]["path"].split("/"))
    if hashlib.sha256(filter_authority.read_bytes()).hexdigest() != authority["exchange_filter_authority"]["byte_sha256"]:
        raise AdapterError("exchange-filter authority bytes changed")
    old_source = getattr(module, "SOURCE_BINDINGS", {})
    old_candidate_ids = getattr(module, "CANDIDATE_IDS", ())
    source_bindings = {ids.get(key, key): value for key, value in old_source.items()}
    values = {
        "load_contract": lambda *unused, **ignored: (copy.deepcopy(rebound), contract_digest),
        "ARCHIVE_MANIFEST_PATH" if hasattr(module, "ARCHIVE_MANIFEST_PATH") else "MANIFEST_PATH": manifest,
        "UNIVERSE_PATH": universe,
        "FORMAT": {
            "pack": "zackbot.strategy-causal-pack-01/4",
            "rbr_short": "zackbot.rank-breadth-reversal-4h-short-causal-v4/4",
            "atr_shock": "zackbot.atr-shock-snapback-4h-causal-v4/1",
        }[profile_name],
    }
    secondary_module = None
    secondary_values = {}
    if hasattr(module, "CANDIDATE_IDS"):
        values["CANDIDATE_IDS"] = tuple(profile["candidate_ids"])
    if hasattr(module, "SIDES"):
        values["SIDES"] = {ids.get(key, key): value for key, value in module.SIDES.items()}
    if hasattr(module, "SOURCE_BINDINGS"):
        values["SOURCE_BINDINGS"] = source_bindings
    if hasattr(module, "FILTER_AUTHORITY_PATH"):
        values["FILTER_AUTHORITY_PATH"] = filter_authority
    if hasattr(module, "FILTER_PATH"):
        values["FILTER_PATH"] = filter_authority
    multiplicity = copy.deepcopy(overlay["multiplicity_append"])
    if profile_name == "pack":
        original_validate_source_bindings = module.validate_source_bindings
        def validate_v4_source_bindings(unused_rebound_contract):
            # The v3 validator has one management exception keyed by the v3
            # momentum identity (max_bars=180).  Rebinding the identity before
            # calling it silently bypasses that exception and rejects the
            # otherwise unchanged frozen management.  Validate the immutable
            # source contract with the immutable v3 bindings, then relabel only
            # the returned evidence keys for the authorized v4 identities.
            del unused_rebound_contract
            with _patched(module, {"SOURCE_BINDINGS": old_source,
                                   "CANDIDATE_IDS": old_candidate_ids}):
                verified = original_validate_source_bindings(source_contract)
            return {ids.get(key, key): value for key, value in verified.items()}
        values["validate_source_bindings"] = validate_v4_source_bindings
        original_evaluate = module.evaluate_raw_candidate
        def evaluate_v4(adapter, pf, **kwargs):
            packet = original_evaluate(adapter, pf, **kwargs)
            report = packet.get("report") or {}
            pending = report.get("holm_adjusted_p_value")
            if isinstance(pending, dict) and "worst_rank_upper_bound" in pending:
                pending["worst_rank_upper_bound"] = min(
                    1.0, profile["multiplicity"]["resulting"] * float(report["raw_p_value"]))
            packet["multiplicity_append"] = copy.deepcopy(multiplicity)
            verified = packet.setdefault("_verified_authority", {})
            verified.update(causal_v4_overlay_sha256=profile["overlay_byte_sha256"],
                            coverage_preflight_digest=authority["coverage_preflight"]["content_digest"])
            return packet
        values["evaluate_raw_candidate"] = evaluate_v4
    elif profile_name == "rbr_short":
        from tools.research import rank_breadth_reversal_4h_short_causal_v3_archive as rbr_archive
        secondary_module = rbr_archive
        secondary_values = {
            "EXPECTED_MANIFEST_SHA256": authority["manifest"]["byte_sha256"],
            "EXPECTED_MANIFEST_DIGEST": authority["manifest"]["content_digest"],
            "EXPECTED_UNIVERSE_SHA256": authority["pit_universe"]["byte_sha256"],
            "EXPECTED_UNIVERSE_DIGEST": authority["pit_universe"]["content_digest"],
        }
        original_ledger = module._load_family_ledger
        def v4_ledger():
            ledger = copy.deepcopy(original_ledger())
            old = _identity_rows(overlay)[0]
            source = next(row for row in ledger["trials"] if row["trial_id"] == old["v3_candidate_id"])
            appended = copy.deepcopy(source)
            appended.update(trial_id=old["v4_candidate_id"], status="REGISTERED_CAUSAL_V4")
            for key in ("raw_p_value", "holm_adjusted_p_value", "closed_trades", "net_r", "profit_factor"):
                appended.pop(key, None)
            ledger["trials"].append(appended)
            ledger["holm_family_size"] = profile["multiplicity"]["resulting"]
            return ledger
        values["_load_family_ledger"] = v4_ledger
        original_evaluate = module.evaluate_raw_candidate
        def evaluate_v4(adapter, pf, **kwargs):
            packet = original_evaluate(adapter, pf, **kwargs)
            packet["multiplicity_append"] = copy.deepcopy(multiplicity)
            verified = packet.setdefault("_verified_authority", {})
            verified.update(causal_v4_overlay_sha256=profile["overlay_byte_sha256"],
                            coverage_preflight_digest=authority["coverage_preflight"]["content_digest"])
            return packet
        values["evaluate_raw_candidate"] = evaluate_v4
    elif profile_name == "atr_shock":
        original_run = module.run
        def run_v4(*run_args, **run_kwargs):
            # The protected v4 overlay supersedes only the provisional v3
            # commit-membership gate.  All source, authority, reservation and
            # clean-head guards inside the reviewed runner still execute.
            run_kwargs["merge_guard"] = lambda: True
            return original_run(*run_args, **run_kwargs)
        values["run"] = run_v4
        original_validate_authorities = module.validate_repository_authorities
        authority_contract = _replace(copy.deepcopy(source_contract),
                                      _authority_replacements(source_contract, overlay))
        def validate_v4_authorities(unused_contract):
            # The immutable four-row source ledger is validated byte-for-byte
            # here; the two v4 rows exist only in the evaluation/assembly view.
            return original_validate_authorities(authority_contract)
        values["validate_repository_authorities"] = validate_v4_authorities
        def pending_v4(contract, selected, raw_p):
            del contract
            frozen = copy.deepcopy(module._strict_json(module.FAMILY_PATH))
            frozen_digest = frozen.pop("digest")
            family = frozen
            rows = family["trials"]
            for mapping in _identity_rows(overlay):
                source = next(row for row in rows if row["trial_id"] == mapping["v3_candidate_id"])
                appended = copy.deepcopy(source)
                appended["trial_id"] = mapping["v4_candidate_id"]
                appended["status"] = ("EVALUATED_CAUSAL_V4_PENDING_PAIRED_RECONCILIATION"
                                      if appended["trial_id"] == selected else "REGISTERED_CAUSAL_V4")
                appended["raw_p_value"] = raw_p if appended["trial_id"] == selected else None
                appended["holm_input_p_value"] = raw_p if appended["trial_id"] == selected else None
                appended["holm_adjusted_p_value"] = None
                rows.append(appended)
            family["frozen_source_digest"] = frozen_digest
            family["reconciliation_status"] = "PENDING_CAUSAL_V4_SIDE_FAMILY"
            family["multiplicity_append"] = copy.deepcopy(multiplicity)
            family["evaluation_digest"] = hashlib.sha256(_canonical(family)).hexdigest()
            return family
        values["_pending_family_for_side"] = pending_v4
        original_evaluate = module.evaluate_raw
        def evaluate_v4(adapter, pf, verified_authority):
            packet = original_evaluate(adapter, pf, verified_authority)
            packet["multiplicity_append"] = copy.deepcopy(multiplicity)
            packet.setdefault("promotion_gates", {})[
                "full_ledger_and_multiplicity_disclosure"] = (
                    len(packet.get("report", {}).get("full_candidate_ledger", {}).get("trials", ()))
                    == profile["multiplicity"]["resulting"])
            packet.setdefault("integrity", {}).update(
                causal_v4_overlay_sha256=profile["overlay_byte_sha256"],
                coverage_preflight_digest=authority["coverage_preflight"]["content_digest"])
            return packet
        values["evaluate_raw"] = evaluate_v4
    forwarded = ["--candidate", args.candidate, "--out", args.out,
                 "--identity-dir", args.identity_dir]
    if args.archive_store is not None:
        forwarded += ["--archive-store", args.archive_store]
    with _patched(module, values):
        if secondary_module is not None:
            with _patched(secondary_module, secondary_values):
                return delegate(forwarded)
        return delegate(forwarded)
