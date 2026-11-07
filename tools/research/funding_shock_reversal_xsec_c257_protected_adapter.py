"""Single-use protected adapter for the C257 typed evaluator."""
from __future__ import annotations
import hashlib, json, os, re, subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from tools.research import c257_successor_guard as G
from tools.research import funding_shock_reversal_xsec_c257_evaluator as E
from tools.research import funding_shock_reversal_xsec_c257_stagea as S

ROOT = Path(__file__).resolve().parents[2]
PROTECTED_REF = "refs/remotes/origin/master"
SUCCESSOR_HEAD = "27f3eef77a4d10d300a288ef917fc5842170550d"
PROTECTED_PLAN_COMMIT = "64adf3a41d7a521d2af480adc7b2f23a8fa6a0c3"
BOUND_CODE = (
 "tools/research/c257_successor_guard.py",
 "tools/research/funding_shock_reversal_xsec_c257_stagea.py",
 "tools/research/funding_shock_reversal_xsec_c257_evaluator.py",
 "tools/research/funding_shock_reversal_xsec_c257_protected_adapter.py",
 "tools/research/run_funding_shock_reversal_xsec_c257_protected.py")

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
    _ancestor(SUCCESSOR_HEAD, head); _ancestor(PROTECTED_PLAN_COMMIT, head)
    path = "research_evidence/prereg/funding_shock_reversal_xsec_c257_successor.v1.json"
    scaffold = G._strict_json(G._tree_bytes(ROOT, SUCCESSOR_HEAD, path), "C257 frozen scaffold")
    amended = G._strict_json(G._tree_bytes(ROOT, head, path), "C257 protected amendment")
    try:
        G.verify_pin_amendment(scaffold, amended, repo=ROOT)
        G._resolve_c256(amended, ROOT)
    except G.C257GuardError as exc:
        raise ProtectedAdapterError(str(exc)) from exc
    blobs: dict[str, str] = {}
    for item in BOUND_CODE:
        oid, local = _git("rev-parse", f"{head}:{item}"), _git("hash-object", "--", item)
        if oid != local or re.fullmatch(r"[0-9a-f]{40}", oid) is None:
            raise ProtectedAdapterError(f"protected tree blob differs for {item}")
        blobs[item] = oid
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

def evaluate_once(loader: E.SealedTypedLoader, *, identity_dir: Path) -> Mapping[str, object]:
    bindings = authenticate_before_loader()  # must precede loader and identity
    identity = hashlib.sha256(json.dumps({"candidate_id": 257, "bindings": bindings.digest},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    claim = identity_dir / f"candidate-257-{identity}.claim.json"
    try:
        _exclusive(claim, {"state":"RESERVED","candidate_id":257,"identity":identity,"head":bindings.head})
    except FileExistsError as exc:
        raise ProtectedAdapterError("candidate-257 evaluation identity is already consumed") from exc
    try:
        typed = loader.load(capability=object())
        if not isinstance(typed, E.EvaluationInput):
            raise ProtectedAdapterError("sealed loader returned an untyped payload")
        packet = dict(E.evaluate(typed)); packet["promotable"] = False
        packet["integrity"] = {"bindings_digest": bindings.digest,
            "evaluation_identity": identity, "git_head": bindings.head}
        packet["output_identity_sha256"] = hashlib.sha256(json.dumps(packet,sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
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
