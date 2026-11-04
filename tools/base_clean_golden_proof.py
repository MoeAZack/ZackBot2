"""Default-OFF BASE-CLEAN-PROOF-02 NEWCORE golden comparison collector."""
from __future__ import annotations

import argparse
import dataclasses
import enum
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden"
if str(GOLDEN) not in sys.path:
    sys.path.insert(0, str(GOLDEN))

from goldenlib import adapters, compare, deadline, schema, tiers  # noqa: E402
from goldenlib.adapters.base import Trace  # noqa: E402
from newcore.base_clean import GateProof, ProofKind  # noqa: E402


SCHEMA = "zackbot-base-clean-golden-proof/1"
NEWCORE_ADAPTERS = ("newcore_live_sim", "newcore_sim")
HOST_ID = "golden_offline"
HARD_STOP_S = tiers.HARD_STOP_S
_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class CollectorError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise CollectorError(code) from None


def _exact(value: Any, typ: type, code: str) -> Any:
    if type(value) is not typ:
        _fail(code)
    return value


def _text(value: Any, code: str) -> str:
    if type(value) is not str or not value or not value.isascii():
        _fail(code)
    return value


def _sha(value: Any, code: str) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        _fail(code)
    return value


def _sha256(value: Any, code: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail(code)
    return value


@dataclasses.dataclass(frozen=True, slots=True)
class PairSpec:
    case_id: str
    adapter: str
    contract_sha256: str
    tier: str

    def __post_init__(self) -> None:
        _text(self.case_id, "validation.pair.case")
        if self.adapter not in NEWCORE_ADAPTERS:
            _fail("validation.pair.adapter")
        _sha256(self.contract_sha256, "validation.pair.contract")
        if self.tier not in tiers.TIERS:
            _fail("validation.pair.tier")


@dataclasses.dataclass(frozen=True, slots=True)
class PairResult:
    case_id: str
    adapter: str
    contract_sha256: str
    tier: str
    trace_sha256: str

    def __post_init__(self) -> None:
        PairSpec(self.case_id, self.adapter, self.contract_sha256, self.tier)
        _sha256(self.trace_sha256, "validation.result.trace")


@dataclasses.dataclass(frozen=True, slots=True)
class GoldenArtifactBody:
    schema: str
    head_sha: str
    host_id: str
    pack_sha256: str
    pairs: tuple[PairResult, ...]

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            _fail("validation.artifact.schema")
        _sha(self.head_sha, "validation.artifact.head")
        if self.host_id != HOST_ID:
            _fail("validation.artifact.host")
        _sha256(self.pack_sha256, "validation.artifact.pack")
        _exact(self.pairs, tuple, "validation.artifact.pairs")
        for row in self.pairs:
            _exact(row, PairResult, "validation.artifact.pair")
        keys = [(row.case_id, row.adapter) for row in self.pairs]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            _fail("validation.artifact.matrix")


@dataclasses.dataclass(frozen=True, slots=True)
class GoldenArtifact:
    body: GoldenArtifactBody
    artifact_sha256: str

    def __post_init__(self) -> None:
        _exact(self.body, GoldenArtifactBody, "validation.envelope.body")
        _sha256(self.artifact_sha256, "validation.envelope.digest")
        if hashlib.sha256(canonical_bytes(self.body)).hexdigest() != self.artifact_sha256:
            _fail("validation.envelope.digest")


def _canonical(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return {field.name: _canonical(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if type(value) is tuple:
        return [_canonical(item) for item in value]
    if type(value) is list:
        return [_canonical(item) for item in value]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            _fail("validation.canonical")
        return {key: _canonical(item) for key, item in value.items()}
    if value is None or type(value) in (str, int, bool, float):
        return value
    _fail("validation.canonical")


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        _fail("validation.canonical")


def _pack_sha(plan: tuple[PairSpec, ...]) -> str:
    return hashlib.sha256(canonical_bytes(plan)).hexdigest()


def _load_plan() -> tuple[tuple[PairSpec, ...], dict[str, dict[str, Any]]]:
    try:
        cases = schema.load_all()
        tier_of = tiers.load([case["id"] for case in cases])
    except Exception:
        _fail("golden_pack_invalid")
    plan: list[PairSpec] = []
    by_id: dict[str, dict[str, Any]] = {}
    for case in cases:
        by_id[case["id"]] = case
        for adapter in NEWCORE_ADAPTERS:
            if schema.status(case, adapter) != "required":
                _fail("golden_status_not_required")
            if adapter not in adapters.CAPS:
                _fail("adapter_unavailable")
            plan.append(PairSpec(case["id"], adapter, schema.contract_sha(case), tier_of[case["id"]]))
    plan.sort(key=lambda row: (row.case_id, row.adapter))
    if len(plan) != len(cases) * len(NEWCORE_ADAPTERS):
        _fail("golden_matrix_incomplete")
    return tuple(plan), by_id


def _trace_wire(value: Any) -> dict[str, Any]:
    _exact(value, dict, "validation.trace")
    if set(value) != {"trades", "final"}:
        _fail("validation.trace.keys")
    _exact(value["trades"], list, "validation.trace.trades")
    _exact(value["final"], dict, "validation.trace.final")
    try:
        wire = schema.strict_loads(schema.canonical(value))
    except Exception:
        _fail("validation.trace.canonical")
    if adapters.POISON in schema.canonical(wire):
        _fail("adapter_expectation_leak")
    return wire


def _seal_artifact(head_sha: str, plan: tuple[PairSpec, ...],
                   traces: tuple[tuple[str, str, dict[str, Any]], ...],
                   cases: dict[str, dict[str, Any]]) -> GoldenArtifact:
    _sha(head_sha, "validation.artifact.head")
    _exact(plan, tuple, "validation.plan")
    _exact(traces, tuple, "validation.traces")
    expected = {(row.case_id, row.adapter): row for row in plan}
    supplied: dict[tuple[str, str], dict[str, Any]] = {}
    for row in traces:
        if type(row) is not tuple or len(row) != 3:
            _fail("validation.trace.record")
        case_id, adapter, trace = row
        key = (_text(case_id, "validation.trace.case"), _text(adapter, "validation.trace.adapter"))
        if key in supplied:
            _fail("golden_trace_duplicate")
        if key not in expected:
            _fail("golden_trace_unknown")
        supplied[key] = _trace_wire(trace)
    if set(supplied) != set(expected):
        _fail("golden_trace_missing")
    results: list[PairResult] = []
    for key in sorted(expected):
        spec = expected[key]
        wire = supplied[key]
        trace = Trace(trades=wire["trades"], final=wire["final"])
        try:
            mismatches = compare.compare(cases[spec.case_id], trace, spec.adapter)
        except Exception:
            _fail("golden_comparison_invalid")
        if mismatches:
            _fail("golden_comparison_mismatch")
        results.append(PairResult(
            spec.case_id, spec.adapter, spec.contract_sha256, spec.tier,
            hashlib.sha256(canonical_bytes(wire)).hexdigest(),
        ))
    body = GoldenArtifactBody(SCHEMA, head_sha, HOST_ID, _pack_sha(plan), tuple(results))
    return GoldenArtifact(body, hashlib.sha256(canonical_bytes(body)).hexdigest())


def _pairs(value: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value:
        if key in result:
            _fail("validation.json.duplicate")
        result[key] = item
    return result


def _keys(value: Any, expected: set[str], code: str) -> dict[str, Any]:
    row = _exact(value, dict, code)
    if set(row) != expected or any(type(key) is not str for key in row):
        _fail(code)
    return row


def encode_artifact(value: GoldenArtifact) -> bytes:
    _exact(value, GoldenArtifact, "validation.envelope")
    pairs = tuple(PairResult(*(getattr(row, field.name) for field in dataclasses.fields(PairResult)))
                  for row in value.body.pairs)
    body = GoldenArtifactBody(value.body.schema, value.body.head_sha, value.body.host_id,
                              value.body.pack_sha256, pairs)
    sealed = GoldenArtifact(body, value.artifact_sha256)
    return canonical_bytes({"artifact_sha256": sealed.artifact_sha256, "body": sealed.body})


def decode_artifact(data: bytes) -> GoldenArtifact:
    if type(data) is not bytes:
        _fail("validation.json.bytes")
    raw = None
    try:
        raw = json.loads(data.decode("ascii"), object_pairs_hook=_pairs,
                         parse_constant=lambda _value: _fail("validation.json.constant"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    if raw is None:
        _fail("validation.json.syntax")
    envelope = _keys(raw, {"artifact_sha256", "body"}, "validation.envelope.keys")
    body = _keys(envelope["body"], {"schema", "head_sha", "host_id", "pack_sha256", "pairs"},
                 "validation.artifact.keys")
    rows = _exact(body["pairs"], list, "validation.artifact.pairs")
    rebuilt = []
    for item in rows:
        row = _keys(item, {"case_id", "adapter", "contract_sha256", "tier", "trace_sha256"},
                    "validation.result.keys")
        rebuilt.append(PairResult(row["case_id"], row["adapter"], row["contract_sha256"],
                                  row["tier"], row["trace_sha256"]))
    artifact = GoldenArtifact(
        GoldenArtifactBody(body["schema"], body["head_sha"], body["host_id"],
                           body["pack_sha256"], tuple(rebuilt)),
        envelope["artifact_sha256"],
    )
    if encode_artifact(artifact) != data:
        _fail("validation.json.canonical")
    return artifact


def _validate_artifact_plan(artifact: GoldenArtifact, plan: tuple[PairSpec, ...]) -> None:
    if artifact.body.pack_sha256 != _pack_sha(plan):
        _fail("artifact_pack_mismatch")
    actual = tuple(PairSpec(row.case_id, row.adapter, row.contract_sha256, row.tier)
                   for row in artifact.body.pairs)
    if actual != plan:
        _fail("artifact_matrix_mismatch")


def gate_evidence(value: GoldenArtifact) -> GateProof:
    artifact = decode_artifact(encode_artifact(value))
    plan, _cases = _load_plan()
    _validate_artifact_plan(artifact, plan)
    return GateProof(ProofKind.NEWCORE_GOLDEN_ADAPTERS, artifact.body.head_sha,
                     artifact.body.host_id, True, artifact.artifact_sha256)


def _git_state(repo: Path) -> tuple[str, bool]:
    try:
        head = subprocess.run(("git", "rev-parse", "HEAD"), cwd=repo, capture_output=True,
                              text=True, check=True, timeout=10).stdout.strip()
        dirty = bool(subprocess.run(("git", "status", "--porcelain", "--untracked-files=no"),
                                    cwd=repo, capture_output=True, text=True, check=True,
                                    timeout=30).stdout.strip())
        return _sha(head, "git_head_invalid"), dirty
    except (OSError, subprocess.SubprocessError):
        _fail("git_state_unavailable")


def _identity(expected_head: str, actual_head: str, dirty: bool) -> None:
    _sha(expected_head, "expected_head_invalid")
    if actual_head != expected_head:
        _fail("head_mismatch")
    if type(dirty) is not bool or dirty:
        _fail("worktree_dirty")


def _worker(output: Path) -> int:
    try:
        plan, cases = _load_plan()
        runs = []
        for spec in plan:
            try:
                trace = adapters.get(spec.adapter).run(adapters.blind(cases[spec.case_id]))
                _exact(trace, Trace, "adapter_trace_type")
                runs.append({"case_id": spec.case_id, "adapter": spec.adapter,
                             "trace": trace.as_dict()})
            except CollectorError:
                raise
            except Exception:
                _fail("adapter_failed")
        output.write_bytes(canonical_bytes({"runs": runs}))
        return 0
    except (CollectorError, OSError) as error:
        code = error.code if type(error) is CollectorError else "worker_write_failed"
        print(code, file=sys.stderr)
        return 2


def _run_worker() -> tuple[tuple[str, str, dict[str, Any]], ...]:
    with tempfile.TemporaryDirectory(prefix="base-clean-golden-") as temporary:
        output = Path(temporary) / "runs.json"
        result = deadline.run((sys.executable, "-m", "tools.base_clean_golden_proof",
                               "--internal-worker", str(output)), HARD_STOP_S, cwd=ROOT)
        if result.timed_out:
            _fail("adapter_timeout")
        if result.returncode != 0:
            _fail("adapter_failed")
        try:
            raw = schema.strict_loads(output.read_text(encoding="ascii"))
        except Exception:
            _fail("worker_output_invalid")
    if type(raw) is not dict or set(raw) != {"runs"} or type(raw["runs"]) is not list:
        _fail("worker_output_invalid")
    traces = []
    for item in raw["runs"]:
        if type(item) is not dict or set(item) != {"case_id", "adapter", "trace"}:
            _fail("worker_output_invalid")
        traces.append((item["case_id"], item["adapter"], item["trace"]))
    return tuple(traces)


def collect(*, expected_head: str, repo: Path = ROOT) -> GoldenArtifact:
    result = None
    failure = None
    try:
        head, dirty = _git_state(repo)
        _identity(expected_head, head, dirty)
        plan, cases = _load_plan()
        traces = _run_worker()
        final_head, final_dirty = _git_state(repo)
        _identity(expected_head, final_head, final_dirty)
        result = _seal_artifact(expected_head, plan, traces, cases)
    except KeyboardInterrupt:
        failure = CollectorError("collection_interrupted")
    except CollectorError as error:
        failure = CollectorError(error.code)
    except Exception:
        failure = CollectorError("collection_failed")
    if failure is not None:
        raise failure from None
    if result is None:
        _fail("collection_failed")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-head")
    parser.add_argument("--internal-worker")
    args = parser.parse_args(argv)
    if args.internal_worker is not None:
        return _worker(Path(args.internal_worker))
    try:
        if args.expected_head is None:
            _fail("expected_head_required")
        artifact = collect(expected_head=args.expected_head)
        output = ROOT / "dev_out" / "base_clean" / f"golden_adapters_{artifact.body.head_sha}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as handle:
            handle.write(encode_artifact(artifact))
        print(output.relative_to(ROOT).as_posix())
        print(artifact.artifact_sha256)
        return 0
    except (CollectorError, OSError) as error:
        code = error.code if type(error) is CollectorError else "artifact_write_failed"
        print(code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
