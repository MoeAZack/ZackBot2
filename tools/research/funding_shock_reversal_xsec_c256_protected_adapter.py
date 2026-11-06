"""Protected, fail-closed dispatch seam for the C256 real evaluator.

This is deliberately an adapter *prep*: it authenticates the frozen Stage-A
mechanics and all non-outcome authorities, but no real evaluator is registered
on this head.  Consequently ``--evaluate`` remains closed until this adapter
and a separately reviewed evaluator are both present on the clean protected
head.  Importing this module never imports an archive loader or reads a market,
outcome, identity, or holdout row.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping, Sequence

try:
    from tools.research import funding_shock_reversal_xsec_c256_stagea as C256
except ImportError:  # direct script execution from repository root
    import funding_shock_reversal_xsec_c256_stagea as C256  # type: ignore


ROOT = Path(__file__).resolve().parents[2]
PROTECTED_REF = "origin/master"
CANDIDATE_ID = 256
SOURCE_RUNNER_HEAD = "a9521c29cd42132c2043e63d623299f9551a7aff"
SOURCE_MODULE_PATH = "tools/research/funding_shock_reversal_xsec_c256_stagea.py"
SOURCE_MODULE_SHA256 = "12e2570f25cfe922be11a0cd6d65dd98baaea946317ff94034623df85792a6fe"
SOURCE_CLI_PATH = "tools/research/run_funding_shock_reversal_xsec_c256_stagea.py"
SOURCE_CLI_SHA256 = "86dd54b522bb5c643b04bd3ef389190db8c8c68563f3a86a8a8fda5bc23b4015"
CONTRACT_PATH = "research_evidence/prereg/funding_shock_reversal_xsec_c256_stagea.v1.json"
CONTRACT_SHA256 = "88294e16a44c4e2219284abeb26f4085123b9fd9de0c5120d0a19d5b84a94b54"
MANIFEST_PATH = "research_evidence/manifests/binance-um-archive-precutoff-monthly-rest-v5.json.gz"
MANIFEST_SHA256 = "3de00d9a9961d04938e71cd58fb042c28bfeea48519d91d8fbac2d12da99987a"
MANIFEST_DIGEST = "30c345c1261f3fe0d48bf4defc448c9139a073aecb2eee4155ecca1e4b64f1a8"
PIT_PATH = "research_evidence/universe/pit-top40-qv30d-precutoff-monthly-rest-v8.json"
PIT_SHA256 = "3fc2c1696a4f9ac753a140394e6ca6ba723a9364046dbb94bcb6310c48b68d41"
PIT_DIGEST = "48f99b4309ff286f5541bd51199e42d4144dcc38e9dda7aa5bc3520b4d244bcd"
FILTER_PATH = "contracts/strategy_causal_pack_01/exchange_filter_authority.v3.json"
FILTER_SHA256 = "e11b42efe63accc6a4546ac29507356fb53cdcc88c403aec031ffa48b27f1ba9"
STATUS = "NOT_AUTHORIZED_PRE_PROTECTED_EVALUATOR"

# The independently reviewed real evaluator is intentionally absent.  A later
# protected pin must replace all three nulls in one reviewed change.
EVALUATOR_MODULE: str | None = None
EVALUATOR_MODULE_SHA256: str | None = None
EVALUATOR_CALLABLE_SHA256: str | None = None


class ProtectedAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class Bindings:
    contract: Mapping[str, object]
    digest: str
    paths: tuple[str, ...]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _strict_json(raw: bytes, label: str) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtectedAdapterError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProtectedAdapterError(f"{label} must be a JSON object")
    return value


def _path(rel: str) -> Path:
    if (not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/")
            or any(part in ("", ".", "..") for part in rel.split("/"))):
        raise ProtectedAdapterError("authority path is not canonical repository-relative")
    root = ROOT.resolve()
    value = root.joinpath(*rel.split("/")).resolve()
    if root not in value.parents:
        raise ProtectedAdapterError("authority path escapes repository")
    return value


def _read_bound(rel: str, expected_sha: str, label: str) -> bytes:
    try:
        raw = _path(rel).read_bytes()
    except OSError as exc:
        raise ProtectedAdapterError(f"{label} is unavailable") from exc
    if _sha(raw) != expected_sha:
        raise ProtectedAdapterError(f"{label} bytes changed")
    return raw


def _verify_source_runner_head(paths: Sequence[str]) -> None:
    """Prove the named source head, not merely hand-copied digest constants."""
    commit = _git(["rev-parse", f"{SOURCE_RUNNER_HEAD}^{{commit}}"])
    if (commit.returncode != 0 or commit.stdout.strip() != SOURCE_RUNNER_HEAD
            or re.fullmatch(r"[0-9a-f]{40}", commit.stdout.strip()) is None):
        raise ProtectedAdapterError("exact C256 source runner head is unavailable")
    for rel in paths:
        source = _git(["rev-parse", f"{SOURCE_RUNNER_HEAD}:{rel}"])
        local = _git(["hash-object", "--", rel])
        if (source.returncode or local.returncode
                or source.stdout.strip() != local.stdout.strip()
                or re.fullmatch(r"[0-9a-f]{40}", source.stdout.strip()) is None):
            raise ProtectedAdapterError(f"{rel} differs from exact C256 source runner head")


def load_bindings() -> Bindings:
    """Verify only frozen metadata/authority bytes; never open an archive row."""
    _read_bound(SOURCE_MODULE_PATH, SOURCE_MODULE_SHA256, "C256 Stage-A module")
    _read_bound(SOURCE_CLI_PATH, SOURCE_CLI_SHA256, "C256 Stage-A CLI")
    contract = _strict_json(_read_bound(CONTRACT_PATH, CONTRACT_SHA256, "C256 contract"), "C256 contract")
    if (contract.get("candidate_id"), contract.get("status"), contract.get("default_off"),
            contract.get("evaluation_locked")) != (
            CANDIDATE_ID, "FROZEN_NOT_EVALUATED_RESEARCH_ONLY", True, True):
        raise ProtectedAdapterError("C256 contract identity/status changed")
    # This invokes the independently tested Stage-A byte and authority checks as
    # a second, differently expressed binding.  It reads metadata only.
    if C256.load_frozen_contract() != contract:
        raise ProtectedAdapterError("Stage-A and adapter contract views differ")

    authority = contract.get("data_authority") or {}
    expected_authority = {
        "archive_manifest_path": MANIFEST_PATH,
        "archive_manifest_sha256": MANIFEST_SHA256,
        "universe_path": PIT_PATH,
        "universe_sha256": PIT_SHA256,
        "universe_digest": PIT_DIGEST,
        "exchange_filter_authority_path": FILTER_PATH,
        "exchange_filter_authority_sha256": FILTER_SHA256,
    }
    if any(authority.get(key) != value for key, value in expected_authority.items()):
        raise ProtectedAdapterError("C256 contract authority binding changed")

    manifest_raw = _read_bound(MANIFEST_PATH, MANIFEST_SHA256, "manifest v5")
    try:
        manifest = _strict_json(gzip.decompress(manifest_raw), "manifest v5")
    except (OSError, EOFError) as exc:
        raise ProtectedAdapterError("manifest v5 gzip is invalid") from exc
    pit = _strict_json(_read_bound(PIT_PATH, PIT_SHA256, "PIT v8"), "PIT v8")
    filters = _strict_json(_read_bound(FILTER_PATH, FILTER_SHA256, "filter v3"), "filter v3")
    if (manifest.get("format"), manifest.get("digest")) != ("zb-data-manifest/1", MANIFEST_DIGEST):
        raise ProtectedAdapterError("manifest v5 content digest changed")
    if (pit.get("digest"), pit.get("manifest_digest")) != (PIT_DIGEST, MANIFEST_DIGEST):
        raise ProtectedAdapterError("PIT v8 is not bound to manifest v5")
    if (filters.get("authority"), filters.get("version"), filters.get("status"),
            filters.get("pit_universe_path"), filters.get("pit_universe_sha256"),
            filters.get("pit_universe_digest")) != (
            "STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY", 3, "FROZEN",
            PIT_PATH, PIT_SHA256, PIT_DIGEST):
        raise ProtectedAdapterError("filter v3 is not exactly bound to PIT v8")

    costs = contract.get("costs") or {}
    if (costs.get("taker_fee_bps_per_fill_per_leg"),
            costs.get("adverse_slippage_bps_per_fill_per_leg"),
            costs.get("stress_adverse_slippage_bps_per_fill_per_leg"),
            costs.get("base_pair_round_trip_fills"),
            C256.FEE_BPS, C256.BASE_SLIPPAGE_BPS, C256.STRESS_SLIPPAGE_BPS) != (
            5.0, 2.0, 10.0, 4, 5.0, 2.0, 10.0):
        raise ProtectedAdapterError("C256 frozen cost mechanics changed")
    bound_paths = (SOURCE_MODULE_PATH, SOURCE_CLI_PATH, CONTRACT_PATH,
                   MANIFEST_PATH, PIT_PATH, FILTER_PATH)
    _verify_source_runner_head(bound_paths)
    digest = _sha(_canonical({
        "candidate_id": CANDIDATE_ID,
        "source_runner_head": SOURCE_RUNNER_HEAD,
        "source_module_sha256": SOURCE_MODULE_SHA256,
        "source_cli_sha256": SOURCE_CLI_SHA256,
        "contract_sha256": CONTRACT_SHA256,
        "manifest_sha256": MANIFEST_SHA256,
        "manifest_digest": MANIFEST_DIGEST,
        "pit_sha256": PIT_SHA256,
        "pit_digest": PIT_DIGEST,
        "filter_sha256": FILTER_SHA256,
        "costs": [5.0, 2.0, 10.0, 4],
    }))
    return Bindings(contract=contract, digest=digest, paths=bound_paths)


def _git(args: Sequence[str], *, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text, check=False)


def _clean_exact_protected_head(protected_ref: str = PROTECTED_REF) -> str:
    head = _git(["rev-parse", "HEAD"])
    protected = _git(["rev-parse", protected_ref])
    dirty = _git(["status", "--porcelain"])
    if head.returncode or protected.returncode or dirty.returncode:
        raise ProtectedAdapterError("protected git authority is unavailable")
    head_sha, protected_sha = head.stdout.strip(), protected.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", head_sha) is None or head_sha != protected_sha or dirty.stdout:
        raise ProtectedAdapterError("evaluation requires a clean HEAD exactly equal to protected ref")
    return head_sha


def _protected_blob_matches(rel: str, protected_ref: str) -> bool:
    local = _git(["hash-object", "--", rel])
    protected = _git(["rev-parse", f"{protected_ref}:{rel}"])
    return (local.returncode == protected.returncode == 0
            and local.stdout.strip() == protected.stdout.strip()
            and re.fullmatch(r"[0-9a-f]{40}", local.stdout.strip()) is not None)


def require_protected_authority(bindings: Bindings, protected_ref: str = PROTECTED_REF) -> str:
    """Require the exact reviewed bytes on the exact clean protected checkout."""
    head = _clean_exact_protected_head(protected_ref)
    adapter_rel = Path(__file__).resolve().relative_to(ROOT.resolve()).as_posix()
    for rel in (*bindings.paths, adapter_rel):
        if not _protected_blob_matches(rel, protected_ref):
            raise ProtectedAdapterError(f"protected authority does not contain exact {rel} bytes")
    return head


def _strict_parser(argv: Sequence[str]) -> argparse.Namespace:
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ProtectedAdapterError(f"invalid argv: {message}")
    argv = tuple(argv)
    singleton = {"--out", "--identity-dir", "--archive-store"}
    for flag in singleton:
        if sum(token == flag or token.startswith(flag + "=") for token in argv) > 1:
            raise ProtectedAdapterError(f"duplicate singleton argument {flag}")
    parser = Parser(add_help=False, allow_abbrev=False)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-adapter", action="store_true")
    modes.add_argument("--evaluate", action="store_true")
    parser.add_argument("--out")
    parser.add_argument("--identity-dir")
    parser.add_argument("--archive-store")
    args = parser.parse_args(argv)
    supplied = (args.out, args.identity_dir, args.archive_store)
    if args.evaluate and any(value is None for value in supplied):
        raise ProtectedAdapterError("--evaluate requires --out, --identity-dir and --archive-store")
    if args.validate_adapter and any(value is not None for value in supplied):
        raise ProtectedAdapterError("validation mode accepts no evaluation paths")
    return args


def _exclusive(path: Path, value: Mapping[str, object]) -> None:
    raw = json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
        fh.flush()
        os.fsync(fh.fileno())


def _reserve_claim(identity_dir: Path, identity: str, head: str, output: Path) -> Path:
    claim = identity_dir / f"{identity}.claim.json"
    try:
        _exclusive(claim, {"state": "RESERVED", "identity": identity, "candidate_id": CANDIDATE_ID,
                           "git_head": head, "output": str(output.resolve())})
    except FileExistsError as exc:
        raise ProtectedAdapterError("C256 sealed evaluation identity is already consumed") from exc
    return claim


def _finish_claim(claim: Path, value: Mapping[str, object]) -> None:
    tmp = claim.with_name(f"{claim.name}.{os.getpid()}.tmp")
    _exclusive(tmp, value)
    os.replace(tmp, claim)


@contextmanager
def _stagea_capability() -> Iterator[object]:
    """Temporarily expose one unforgeable capability; always restore the lock."""
    original = C256.assert_real_evaluation_authorized
    token = object()

    def authorized(*, gate: Mapping[str, object] | None = None) -> None:
        C256.load_frozen_contract()
        if not isinstance(gate, Mapping) or gate.get("capability") is not token:
            raise C256.EvaluationLocked("C256 protected adapter capability is missing or invalid")

    C256.assert_real_evaluation_authorized = authorized
    try:
        yield token
    finally:
        C256.assert_real_evaluation_authorized = original


def _resolve_evaluator() -> Callable:
    # Intentionally no import fallback: a caller cannot name or inject an
    # evaluator, and this PREP head therefore cannot reach market data.
    if None in (EVALUATOR_MODULE, EVALUATOR_MODULE_SHA256, EVALUATOR_CALLABLE_SHA256):
        raise ProtectedAdapterError(
            "REAL_EVALUATOR_NOT_BOUND: an independently reviewed protected evaluator is required")
    raise ProtectedAdapterError("protected evaluator registry is incomplete")


def _execute_authenticated(args: argparse.Namespace, bindings: Bindings, head: str,
                           evaluator: Callable[[argparse.Namespace, Mapping[str, object]], Mapping[str, object]]) -> int:
    """Internal seam for the later byte-authenticated evaluator."""
    output = Path(args.out)
    identity = _sha(_canonical({"candidate_id": CANDIDATE_ID, "bindings": bindings.digest, "git_head": head}))
    claim = _reserve_claim(Path(args.identity_dir), identity, head, output)
    try:
        with _stagea_capability() as token:
            result = evaluator(args, {"capability": token, "bindings_digest": bindings.digest,
                                      "evaluation_identity": identity, "git_head": head})
        if not isinstance(result, Mapping):
            raise ProtectedAdapterError("protected evaluator did not return an immutable packet mapping")
        packet = dict(result)
        integrity = packet.get("integrity")
        if (packet.get("candidate_id"), packet.get("promotable"),
                isinstance(integrity, Mapping) and integrity.get("bindings_digest"),
                isinstance(integrity, Mapping) and integrity.get("evaluation_identity"),
                isinstance(integrity, Mapping) and integrity.get("git_head")) != (
                CANDIDATE_ID, False, bindings.digest, identity, head):
            raise ProtectedAdapterError("protected evaluator packet identity/bindings changed")
        _exclusive(output, packet)
    except BaseException as exc:
        _finish_claim(claim, {"state": "FAILED_CONSUMED", "identity": identity,
                              "candidate_id": CANDIDATE_ID, "error_type": type(exc).__name__})
        raise
    _finish_claim(claim, {"state": "COMPLETED", "identity": identity,
                          "candidate_id": CANDIDATE_ID, "output": str(output.resolve()),
                          "packet_sha256": _sha(_canonical(packet))})
    return 0


def guarded_dispatch(argv: Sequence[str] | None = None) -> int:
    args = _strict_parser(() if argv is None else argv)
    bindings = load_bindings()
    if args.validate_adapter:
        print(f"C256_ADAPTER_VALID; {STATUS}; BINDINGS={bindings.digest}")
        return 0
    head = require_protected_authority(bindings)
    evaluator = _resolve_evaluator()
    return _execute_authenticated(args, bindings, head, evaluator)
