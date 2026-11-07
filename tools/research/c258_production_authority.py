"""Protected mechanical execution amendment; never a caller pin override."""
from __future__ import annotations
from dataclasses import asdict
import json
from pathlib import Path

from tools.research import cross_sectional_relative_strength_continuation_c258_stagea as C

AMENDMENT_PATH = "contracts/strategy_causal_pack_01/c258_stagea_execution_authority.v1.json"
IMPLEMENTATION_PATHS = (
    "tools/research/c258_executable.py", "tools/research/c258_archive_source.py",
    "tools/research/c258_production_authority.py",
    "tools/research/cross_sectional_relative_strength_continuation_c258_stagea.py",
    "tools/research/run_cross_sectional_relative_strength_continuation_c258_stagea.py",
    "tools/research/authority_v6.py", "tools/research/authority_v6_runner.py",
    "tools/research/manifest.py", "tools/research/universe.py",
    "tools/research/logical_trade_hours.py",
    "contracts/strategy_causal_pack_01/c257_259_hourly_source_correction.v2.json",
)


def protected_amendment(root: Path, *, verify_local_code: bool = True):
    """Resolve only the current protected ref and its byte-bound amendment."""
    C.load_frozen_contract()
    try:
        head = C._git(root, "rev-parse", "--verify", f"{C.AUTHORITY_PROTECTED_REF}^{{commit}}")
        raw = C._git(root, "show", f"{head}:{AMENDMENT_PATH}", binary=True)
    except C.IncompleteAuthority as exc:
        raise C.EvaluationLocked("C258_REAL_EVALUATION_LOCKED: protected remote ref or mechanical amendment absent; no loader was invoked") from exc
    try:
        doc = json.loads(raw)
        if set(doc) != {"format", "candidate_id", "contract_sha256", "default_off",
                       "paper_testnet_only", "authority_pins", "accepted_implementation_head",
                       "implementation_blobs", "independent_acceptance"}:
            raise ValueError("execution amendment is not mechanical")
        if (doc.get("format"), doc.get("candidate_id"), doc.get("contract_sha256"),
            doc.get("default_off"), doc.get("paper_testnet_only")) != (
                "zb-c258-stagea-execution-authority/1", 258, C.CONTRACT_SHA256, True, True):
            raise ValueError("execution amendment identity mismatch")
        pin_doc = doc["authority_pins"]
        if set(pin_doc) != {"protected_head", *C.EXPECTED_AUTHORITY_PATHS}:
            raise ValueError("authority pin closure is not exact")
        pins = C.AuthorityPins(pin_doc["protected_head"], **{
            name: C.ArtifactPin(**pin_doc[name]) for name in C.EXPECTED_AUTHORITY_PATHS})
        C._git(root, "merge-base", "--is-ancestor", pins.protected_head, head)
        implementation_head = doc["accepted_implementation_head"]
        if len(implementation_head) != 40 or any(ch not in "0123456789abcdef" for ch in implementation_head):
            raise ValueError("implementation head is not exact")
        C._git(root, "merge-base", "--is-ancestor", implementation_head, head)
        for frozen_path, expected in ((C.CONTRACT_PATH.relative_to(C.ROOT).as_posix(), C.CONTRACT_SHA256),
                                       (C.PLAN_PATH.relative_to(C.ROOT).as_posix(), C.PLAN_SHA256)):
            for tree in (head, implementation_head):
                frozen_raw = C._git(root, "show", f"{tree}:{frozen_path}", binary=True)
                if C._sha256_bytes(frozen_raw) != expected:
                    raise ValueError("protected frozen contract/plan changed")
        blobs = doc["implementation_blobs"]
        if set(blobs) != set(IMPLEMENTATION_PATHS):
            raise ValueError("implementation closure is incomplete")
        for path in IMPLEMENTATION_PATHS:
            accepted = C._git(root, "show", f"{implementation_head}:{path}", binary=True)
            protected = C._git(root, "show", f"{head}:{path}", binary=True)
            if C._sha256_bytes(accepted) != blobs[path] or accepted != protected:
                raise ValueError("protected implementation blob drift")
            if verify_local_code and C._sha256(C.ROOT/path) != blobs[path]:
                raise ValueError("local executable differs from independently accepted protected blobs")
        if verify_local_code and (C._git(root, "rev-parse", "HEAD") != head
                                  or C._git(root, "status", "--porcelain")):
            raise ValueError("real evaluation requires clean exact protected HEAD")
        evidence = doc["independent_acceptance"]
        from tools.research import logical_trade_hours as H
        H.validate_clarification(C.ROOT)
        evidence_path = evidence["path"]
        if Path(evidence_path).is_absolute() or ".." in Path(evidence_path).parts:
            raise ValueError("invalid acceptance evidence path")
        evidence_raw = C._git(root, "show", f"{head}:{evidence_path}", binary=True)
        accepted = json.loads(evidence_raw)
        if (C._sha256_bytes(evidence_raw) != evidence["sha256"]
                or accepted.get("accepted_implementation_head") != implementation_head
                or accepted.get("verdict") != "PASS"
                or accepted.get("independent") is not True):
            raise ValueError("independent exact-head acceptance evidence mismatch")
    except (KeyError, TypeError, ValueError, OSError, C.IncompleteAuthority) as exc:
        raise C.EvaluationLocked("C258_REAL_EVALUATION_LOCKED: invalid protected execution amendment; no loader was invoked") from exc
    return pins, doc, head


def authorize_production(root: Path = C.ROOT) -> C.AuthorityPins:
    pins, _, _ = protected_amendment(root)
    docs = C.validate_authority_pins(pins, root=root)
    if docs["manifest_v6"].get("synthetic_fixture_only") is True:
        raise C.EvaluationLocked("synthetic source cannot authorize a real C258 evaluation")
    return pins


def authority_head_is_bound_ancestor(root: Path, pins: C.AuthorityPins) -> bool:
    try:
        amended, _, _ = protected_amendment(root, verify_local_code=False)
    except C.EvaluationLocked:
        return False
    return asdict(amended) == asdict(pins)
