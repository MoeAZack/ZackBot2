"""Single-use protected adapter for the C257 typed evaluator."""
from __future__ import annotations
import hashlib, json, os, re, subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from tools.research import c257_successor_guard as G
from tools.research import funding_shock_reversal_xsec_c257_evaluator as E
from tools.research import funding_shock_reversal_xsec_c257_stagea as S
from tools.research import c257_archive_loader as L
from tools.research import authority_v6 as V
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[2]
PROTECTED_REF = "refs/remotes/origin/master"
SUCCESSOR_HEAD = "27f3eef77a4d10d300a288ef917fc5842170550d"
PROTECTED_PLAN_COMMIT = "64adf3a41d7a521d2af480adc7b2f23a8fa6a0c3"
BOUND_CODE = (
 "tools/research/c257_successor_guard.py",
 "tools/research/funding_shock_reversal_xsec_c257_stagea.py",
 "tools/research/funding_shock_reversal_xsec_c257_evaluator.py",
 "tools/research/funding_shock_reversal_xsec_c257_protected_adapter.py",
 "tools/research/run_funding_shock_reversal_xsec_c257_protected.py",
 "tools/research/c257_archive_loader.py", "tools/research/authority_v6.py",
 "tools/research/manifest.py", "tools/research/universe.py", "tools/research/c257_reporting.py",
 "tools/research/logical_trade_hours.py",
 "contracts/strategy_causal_pack_01/c257_259_hourly_source_correction.v2.json",
 "contracts/strategy_causal_pack_01/c257_reporting_diagnostic_amendment.v1.json")

class ProtectedAdapterError(RuntimeError): pass

@dataclass(frozen=True)
class Bindings:
    head: str
    digest: str
    blobs: Mapping[str, str]
    successor: Mapping[str, object]

def _git(*args: str) -> str:
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=False)
    if result.returncode:
        raise ProtectedAdapterError("protected Git authority is unavailable")
    return result.stdout.decode("ascii").strip()

def _ancestor(base: str, head: str) -> None:
    result = subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", base, head],
                            capture_output=True, check=False)
    if result.returncode:
        raise ProtectedAdapterError("protected head lacks frozen successor/plan ancestry")

def authenticate_before_loader() -> Bindings:
    """Hard-bind the one protected ref and all authority/code tree bytes."""
    head, protected = _git("rev-parse", "HEAD"), _git("rev-parse", "--verify", f"{PROTECTED_REF}^{{commit}}")
    if head != protected or re.fullmatch(r"[0-9a-f]{40}", head) is None or _git("status", "--porcelain"):
        raise ProtectedAdapterError("evaluation requires clean HEAD equal to hard-bound protected ref")
    # Protected integration may squash the feature history. The immutable
    # successor below still supplies the exact scaffold comparison; only the
    # protected plan base must be an ancestor of the accepted implementation.
    _ancestor(PROTECTED_PLAN_COMMIT, head)
    path = "research_evidence/prereg/funding_shock_reversal_xsec_c257_successor.v1.json"
    scaffold = G._strict_json(G._tree_bytes(ROOT, SUCCESSOR_HEAD, path), "C257 frozen scaffold")
    amended = G._strict_json(G._tree_bytes(ROOT, head, path), "C257 protected amendment")
    try:
        G.verify_pin_amendment(scaffold, amended, repo=ROOT)
        G._resolve_c256(amended, ROOT)
        pit_pin = amended["authority_v6"]["output_pins"]["pit_v9"]
        pit = G._parse_output("pit_v9", G._tree_bytes(ROOT, head, pit_pin["path"]))
        V._validate_pit_v9_prefix(pit, ROOT)
    except G.C257GuardError as exc:
        raise ProtectedAdapterError(str(exc)) from exc
    blobs: dict[str, str] = {}
    for item in BOUND_CODE:
        oid, local = _git("rev-parse", f"{head}:{item}"), _git("hash-object", "--", item)
        if oid != local or re.fullmatch(r"[0-9a-f]{40}", oid) is None:
            raise ProtectedAdapterError(f"protected tree blob differs for {item}")
        blobs[item] = oid
    from tools.research import logical_trade_hours as H
    try:H.validate_clarification(ROOT)
    except (OSError,ValueError) as exc:
        raise ProtectedAdapterError('historical source clarification changed') from exc
    digest = hashlib.sha256(json.dumps({"head": head, "blobs": blobs,
        "authority": amended["authority_v6"], "candidate": 257}, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return Bindings(head, digest, blobs, amended)

def _exclusive(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()+b"\n"
    fd = os.open(path, os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())

PRODUCTION_LOADER_BOUND = True
# Independent primary runner/report review completed before any real outcomes.
# The undefined basket diagnostic remains explicitly incomplete/non-promotable;
# this flag never accepts economic gates or enables PAPER activation.
REPORT_REQUIREMENTS_BOUND = True


def _load_protected_folds(bindings: Bindings, *, store: Path) -> L.ProtectedArchiveLoader:
    return L.ProtectedArchiveLoader(root=ROOT, store=store,
        pins=bindings.successor["authority_v6"]["output_pins"])


def _identity_directory() -> Path:
    common = Path(_git("rev-parse", "--git-common-dir"))
    return ((ROOT/common) if not common.is_absolute() else common).resolve()/"c257-evaluation"


def evaluate_once(*, store: Path | None = None, identity_dir: Path | None = None) -> Mapping[str, object]:
    bindings = authenticate_before_loader()  # must precede loader and identity
    if not PRODUCTION_LOADER_BOUND:
        raise ProtectedAdapterError(
            "C257_PRODUCTION_LOADER_UNBOUND: protected archive-to-book loader is absent")
    if not REPORT_REQUIREMENTS_BOUND:
        raise ProtectedAdapterError("C257_REPORT_UNBOUND: reviewed primary report definitions are required")
    if store is None:
        raise ProtectedAdapterError("checksum-bound archive store is required")
    durable = _identity_directory()
    if identity_dir is not None and identity_dir.resolve() != durable.resolve():
        raise ProtectedAdapterError("evaluation identity directory is fixed across all worktrees")
    identity_dir = durable
    identity = hashlib.sha256(json.dumps({"candidate_id": 257, "bindings": bindings.digest},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    claim = identity_dir / "candidate-257.claim.json"
    try:
        _exclusive(claim, {"state":"RESERVED","candidate_id":257,"identity":identity,"head":bindings.head})
    except FileExistsError as exc:
        raise ProtectedAdapterError("candidate-257 evaluation identity is already consumed") from exc
    try:
        loader = _load_protected_folds(bindings, store=store)
        capitals = S.load_frozen_contract()["position_and_portfolio"]["capital_simulations_usd"]
        developments = {str(capital): loader.load_fold("development", Decimal(capital)) for capital in capitals}
        dev_packets = {key: dict(E.evaluate(raw)) for key,raw in developments.items()}
        passed = all(packet["verdict"] == "DEVELOPMENT_SURVIVOR" for packet in dev_packets.values())
        if not passed:
            packet = {"candidate_id":257,"verdict":"REJECT_DEVELOPMENT","forward_opened":False,
                      "capital_simulations":dev_packets,"default_off":True,"paper_testnet_only":True}
        else:
            loader.permit_forward_after_development()
            capital_results = {key: dict(E.evaluate_stage_a(raw, tuple(
                loader.load_fold(fold, Decimal(key)) for fold in ("wf1","wf2","wf3"))))
                for key,raw in developments.items()}
            packet = {"candidate_id":257,"verdict":"PASS_STAGE_A" if all(
                item["verdict"] == "PASS_STAGE_A" for item in capital_results.values()) else "REJECT_FORWARD",
                "capital_simulations":capital_results,"forward_opened":True,
                "default_off":True,"paper_testnet_only":True}
        packet["promotable"] = False
        packet["economic_verdict"] = packet["verdict"]
        packet["full_report_acceptance"] = "PENDING_INDEPENDENT_REVIEW_AND_BASKET_CONTROL_SPECIFICATION"
        packet["paper_or_forward_qualification_claimed"] = False
        packet["capital_feasibility"] = {"required_capitals_usd":capitals,
            "all_required_development_cases_survived":passed,
            "selection_policy":"All fixed capital cases reported; no capital-slice rescue"}
        packet["integrity"] = {"bindings_digest": bindings.digest,
            "evaluation_identity": identity, "git_head": bindings.head}
        packet["output_identity_sha256"] = hashlib.sha256(json.dumps(packet,sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        _exclusive(identity_dir/"candidate-257.report.json", packet)
        _exclusive(claim.with_suffix(".completed.json"), {"state":"COMPLETED","identity":identity,
            "packet_sha256":hashlib.sha256(json.dumps(packet,sort_keys=True,separators=(",", ":")).encode()).hexdigest()})
        return packet
    except BaseException:
        _exclusive(claim.with_suffix(".failed.json"), {"state":"FAILED_CONSUMED","identity":identity})
        raise

def validate_locked() -> Mapping[str, object]:
    contract = S.load_frozen_contract()
    return {"candidate_id":257,"default_off":True,"evaluation_locked":True,
            "inherited_contract_sha256":G.C256_BYTE_SHA256,
            "multiplicity_alpha":contract["multiplicity"]["candidate_familywise_alpha_ceiling"]}
