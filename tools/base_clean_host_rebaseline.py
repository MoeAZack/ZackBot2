"""Offline BASE-CLEAN current-host evidence collector.

Run only from a clean exact-head checkout with Python 3.12.10.  The collector
uses pure NEWCORE codec and scheduler workloads, performs a one-hour memory
soak, and writes one canonical artifact below ``dev_out/base_clean``.  It has no
network, credential, exchange, runtime, PAPER, UI, or mainnet integration.
"""
from __future__ import annotations

import argparse
import ctypes
import dataclasses
import hashlib
import json
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from newcore.account_context import Operation, VenueWork, WorkResult, WorkState
from newcore.base_clean import (
    CURRENT_HOST_PYTHON_VERSION,
    DECODE_MAX_US,
    ENCODE_MAX_US,
    MEMORY_GROWTH_MAX_MIB,
    MEMORY_MIN_DURATION_MS,
    MEMORY_PRIVATE_MAX_MIB,
    PUBLISH_MAX_MS,
    SNAPSHOT_MEAN_MAX_MS,
    STEP_MAX_MS,
    SUBMIT_MAX_MS,
    VALIDATE_MAX_US,
    GateProof,
    LatencyEvidence,
    MemoryEvidence,
    ProofKind,
    canonical_bytes,
)
from newcore.domain import Action, dumps, loads
from newcore.scheduler import AccountBinding, HookOutcome, Scheduler, SchedulerState


SCHEMA = "zackbot-base-clean-host-rebaseline/1"
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path("tests/newcore/fixtures/nc01_samples_v1.jsonl")
_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_HOST = re.compile(r"host_[0-9a-f]{32}")
_ZERO = Decimal("0")
_MIB = Decimal(1024 * 1024)


class CollectorError(ValueError):
    """Closed, stable collector failure with no raw exception text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise CollectorError(code)


def _exact(value: object, typ: type, code: str):
    if type(value) is not typ:
        _fail(code)
    return value


def _sha(value: object, code: str) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        _fail(code)
    return value


def _sha256(value: object, code: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail(code)
    return value


def _host(value: object, code: str) -> str:
    if type(value) is not str or _HOST.fullmatch(value) is None:
        _fail(code)
    return value


def _integer(value: object, code: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail(code)
    return value


def _decimal(value: object, code: str) -> Decimal:
    if type(value) is not Decimal:
        _fail(code)
    invalid = False
    try:
        if not value.is_finite() or value < _ZERO:
            invalid = True
    except (InvalidOperation, ValueError):
        invalid = True
    if invalid:
        _fail(code)
    return value


def _canonical_decimal(value: Decimal) -> Decimal:
    value = _decimal(value, "validation.decimal")
    if value.is_zero():
        return _ZERO
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return Decimal(text)


@dataclass(frozen=True, slots=True)
class HostMeasurements:
    validate_us: Decimal
    encode_us: Decimal
    decode_us: Decimal
    submit_ms: Decimal
    step_ms: Decimal
    snapshot_mean_ms: Decimal
    publish_ms: Decimal

    def __post_init__(self) -> None:
        for field in fields(self):
            value = _canonical_decimal(getattr(self, field.name))
            if value.as_tuple() != getattr(self, field.name).as_tuple():
                _fail(f"validation.measurements.{field.name}")


@dataclass(frozen=True, slots=True)
class HostArtifactBody:
    schema: str
    head_sha: str
    host_id: str
    python_version: str
    workload_sha256: str
    duration_ms: int
    measurements: HostMeasurements
    private_working_set_mib: Decimal
    growth_mib: Decimal
    provisional_memory: bool

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            _fail("validation.artifact.schema")
        _sha(self.head_sha, "validation.artifact.head")
        _host(self.host_id, "validation.artifact.host")
        if self.python_version != CURRENT_HOST_PYTHON_VERSION:
            _fail("validation.artifact.python")
        _sha256(self.workload_sha256, "validation.artifact.workload")
        _integer(self.duration_ms, "validation.artifact.duration", minimum=1)
        _exact(self.measurements, HostMeasurements, "validation.artifact.measurements")
        for name in ("private_working_set_mib", "growth_mib"):
            value = _canonical_decimal(getattr(self, name))
            if value.as_tuple() != getattr(self, name).as_tuple():
                _fail(f"validation.artifact.{name}")
        if type(self.provisional_memory) is not bool or not self.provisional_memory:
            _fail("validation.artifact.provisional")


@dataclass(frozen=True, slots=True)
class HostArtifact:
    body: HostArtifactBody
    artifact_sha256: str

    def __post_init__(self) -> None:
        _exact(self.body, HostArtifactBody, "validation.envelope.body")
        _sha256(self.artifact_sha256, "validation.envelope.digest")
        if hashlib.sha256(canonical_bytes(self.body)).hexdigest() != self.artifact_sha256:
            _fail("validation.envelope.digest")


@dataclass(frozen=True, slots=True)
class HostEvidenceBundle:
    proof: GateProof
    latency: LatencyEvidence
    memory: MemoryEvidence

    def __post_init__(self) -> None:
        _exact(self.proof, GateProof, "validation.bundle.proof")
        _exact(self.latency, LatencyEvidence, "validation.bundle.latency")
        _exact(self.memory, MemoryEvidence, "validation.bundle.memory")


_LATENCY_LIMITS = (
    ("validate_us", VALIDATE_MAX_US),
    ("encode_us", ENCODE_MAX_US),
    ("decode_us", DECODE_MAX_US),
    ("submit_ms", SUBMIT_MAX_MS),
    ("step_ms", STEP_MAX_MS),
    ("snapshot_mean_ms", SNAPSHOT_MEAN_MAX_MS),
    ("publish_ms", PUBLISH_MAX_MS),
)


def build_artifact(*, head_sha: str, host_id: str, python_version: str,
                   workload_sha256: str, duration_ms: int, measurements: HostMeasurements,
                   private_working_set_mib: Decimal, growth_mib: Decimal) -> HostArtifact:
    """Validate a completed measurement set and seal its accepted artifact."""
    if duration_ms < MEMORY_MIN_DURATION_MS:
        _fail("collection_partial_duration")
    if any(getattr(measurements, name) > limit for name, limit in _LATENCY_LIMITS):
        _fail("collection_latency_ceiling")
    if private_working_set_mib > MEMORY_PRIVATE_MAX_MIB or growth_mib > MEMORY_GROWTH_MAX_MIB:
        _fail("collection_memory_ceiling")
    body = HostArtifactBody(
        SCHEMA, head_sha, host_id, python_version, workload_sha256, duration_ms,
        measurements, _canonical_decimal(private_working_set_mib),
        _canonical_decimal(growth_mib), True,
    )
    return HostArtifact(body, hashlib.sha256(canonical_bytes(body)).hexdigest())


def encode_artifact(value: HostArtifact) -> bytes:
    """Reconstruct and encode one artifact as canonical ASCII JSON."""
    _exact(value, HostArtifact, "validation.envelope")
    body = HostArtifactBody(*(getattr(value.body, field.name) for field in fields(HostArtifactBody)))
    sealed = HostArtifact(body, value.artifact_sha256)
    envelope = {
        "artifact_sha256": sealed.artifact_sha256,
        "body": json.loads(canonical_bytes(sealed.body)),
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("validation.json.duplicate")
        result[key] = value
    return result


def _keys(value: object, expected: set[str], code: str) -> dict[str, object]:
    row = _exact(value, dict, code)
    if set(row) != expected or any(type(key) is not str for key in row):
        _fail(code)
    return row


def _wire_decimal(value: object, code: str) -> Decimal:
    if type(value) is not str or not value or value.startswith("+"):
        _fail(code)
    parsed = None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        pass
    if parsed is None:
        _fail(code)
    parsed = _canonical_decimal(parsed)
    if format(parsed, "f") != value:
        _fail(code)
    return parsed


def decode_artifact(data: bytes) -> HostArtifact:
    """Strictly reconstruct canonical artifact bytes and verify their digest."""
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
    body = _keys(envelope["body"], {
        "schema", "head_sha", "host_id", "python_version", "workload_sha256", "duration_ms",
        "measurements", "private_working_set_mib", "growth_mib", "provisional_memory",
    }, "validation.artifact.keys")
    metrics = _keys(body["measurements"], {name for name, _limit in _LATENCY_LIMITS},
                    "validation.measurements.keys")
    measurements = HostMeasurements(**{
        name: _wire_decimal(metrics[name], f"validation.measurements.{name}")
        for name, _limit in _LATENCY_LIMITS
    })
    rebuilt = HostArtifactBody(
        body["schema"], body["head_sha"], body["host_id"], body["python_version"],
        body["workload_sha256"], body["duration_ms"], measurements,
        _wire_decimal(body["private_working_set_mib"], "validation.artifact.private"),
        _wire_decimal(body["growth_mib"], "validation.artifact.growth"),
        body["provisional_memory"],
    )
    artifact = HostArtifact(rebuilt, envelope["artifact_sha256"])
    if encode_artifact(artifact) != data:
        _fail("validation.json.canonical")
    return artifact


def gate_evidence(value: HostArtifact) -> HostEvidenceBundle:
    """Convert only a fully reconstructed accepted artifact into BASE-CLEAN evidence."""
    artifact = decode_artifact(encode_artifact(value))
    body, digest = artifact.body, artifact.artifact_sha256
    accepted = build_artifact(
        head_sha=body.head_sha, host_id=body.host_id, python_version=body.python_version,
        workload_sha256=body.workload_sha256, duration_ms=body.duration_ms,
        measurements=body.measurements,
        private_working_set_mib=body.private_working_set_mib, growth_mib=body.growth_mib,
    )
    if accepted.artifact_sha256 != digest:
        _fail("validation.envelope.digest")
    row = body.measurements
    return HostEvidenceBundle(
        GateProof(ProofKind.CURRENT_HOST_CODEC_BASELINE, body.head_sha, body.host_id, True, digest),
        LatencyEvidence(
            body.head_sha, body.host_id, body.python_version, row.validate_us, row.encode_us,
            row.decode_us, row.submit_ms, row.step_ms, row.snapshot_mean_ms, row.publish_ms, digest,
        ),
        MemoryEvidence(
            body.head_sha, body.host_id, body.duration_ms, body.private_working_set_mib,
            body.growth_mib, True, digest,
        ),
    )


def derive_host_id() -> str:
    """Return a privacy-safe identity for the current Windows host."""
    if sys.platform != "win32":
        _fail("host_unsupported")
    raw = "\0".join((platform.node(), platform.machine(), platform.platform())).encode("utf-8")
    return "host_" + hashlib.sha256(raw).hexdigest()[:32]


def _git_state(repo: Path) -> tuple[str, bool]:
    head = None
    dirty = None
    try:
        head = subprocess.run(
            ("git", "rev-parse", "HEAD"), cwd=repo, capture_output=True, text=True,
            check=True, timeout=10,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ("git", "status", "--porcelain", "--untracked-files=no"), cwd=repo,
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    if head is None or dirty is None:
        _fail("git_state_unavailable")
    return _sha(head, "git_head_invalid"), dirty


def _validate_identity(expected_head: str, expected_host_id: str, actual_head: str,
                       actual_host_id: str, python_version: str, dirty: bool) -> None:
    _sha(expected_head, "expected_head_invalid")
    _host(expected_host_id, "expected_host_invalid")
    if actual_head != expected_head:
        _fail("head_mismatch")
    if actual_host_id != expected_host_id:
        _fail("host_mismatch")
    if python_version != CURRENT_HOST_PYTHON_VERSION:
        _fail("python_mismatch")
    if type(dirty) is not bool or dirty:
        _fail("worktree_dirty")


class _ProbeClock:
    def __init__(self) -> None:
        self.value = 1_000_000

    def __call__(self) -> int:
        return self.value


class _ProbeWorker:
    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        self.work: list[VenueWork] = []
        self.results: list[WorkResult] = []
        self.lock = threading.Lock()

    def submit(self, work: VenueWork) -> None:
        with self.lock:
            self.work.append(work)

    def run_next(self) -> WorkResult | None:
        with self.lock:
            if not self.work:
                return None
            work = self.work.pop(0)
            result = WorkResult(work_id=work.work_id, state=WorkState.COMPLETED, detail="probe")
            self.results.append(result)
            return result

    def snapshot(self):
        with self.lock:
            return SimpleNamespace(
                account_id=self.account_id, queued=tuple(row.work_id for row in self.work),
                in_flight=None, results=tuple(self.results), reconcile_before_restart=False,
            )

    def stop(self, _deadline_ms: int):
        return self.snapshot()


class _ProbeHooks:
    def guards(self, _account_id: str, _now_ms: int) -> HookOutcome:
        return HookOutcome.success()

    manage = guards
    cycle = guards


def _timed(fn: Callable[[], object], iterations: int, repeats: int, scale: Decimal) -> Decimal:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter_ns()
        for _ in range(iterations):
            fn()
        samples.append(Decimal(time.perf_counter_ns() - started) / Decimal(iterations) / scale)
    return _canonical_decimal(statistics.median(samples))


class _RealProbes:
    def __init__(self, repo: Path) -> None:
        fixture = (repo / FIXTURE).read_bytes()
        lines = [line for line in fixture.splitlines() if line]
        if not lines:
            _fail("workload_missing")
        self.workload_sha256 = hashlib.sha256(fixture).hexdigest()
        self.record = loads(max(lines, key=len))
        self.encoded = dumps(self.record)
        self.clock = _ProbeClock()
        account_id = "acct_" + "a" * 32
        self.worker = _ProbeWorker(account_id)
        binding = AccountBinding(
            account_id=account_id, worker=self.worker, hooks=_ProbeHooks(), restore_proven=True)
        self.scheduler = Scheduler(
            boot_id="boot-base-clean-probe", accounts=(binding,), monotonic_clock=self.clock)
        self.work_sequence = 0
        self.scheduler.step()

    def _validate(self) -> None:
        dataclasses.replace(self.record)

    def _encode(self) -> None:
        dumps(self.record)

    def _decode(self) -> None:
        loads(self.encoded)

    def _submit(self) -> None:
        self.work_sequence += 1
        now = self.clock()
        self.scheduler.submit(
            self.worker.account_id,
            VenueWork(
                work_id=f"probe-{self.work_sequence}", action=Action.PROTECT,
                operation=Operation.QUERY, payload=None, enqueued_at_ms=now,
                deadline_ms=now + 60_000,
            ),
        )

    def measure(self) -> HostMeasurements:
        for _ in range(3):
            self._validate(); self._encode(); self._decode(); self.scheduler.step()
        validate = _timed(self._validate, 200, 5, Decimal(1000))
        encode = _timed(self._encode, 40, 5, Decimal(1000))
        decode = _timed(self._decode, 20, 5, Decimal(1000))
        submit = _timed(self._submit, 10, 5, Decimal(1_000_000))
        snapshot = _timed(self.scheduler.snapshot, 10_000, 5, Decimal(1_000_000))
        steps = []
        for _ in range(25):
            started = time.perf_counter_ns()
            self.scheduler.step()
            steps.append(Decimal(time.perf_counter_ns() - started) / Decimal(1_000_000))
        return HostMeasurements(
            validate, encode, decode, submit, _canonical_decimal(statistics.median(steps)),
            snapshot, _canonical_decimal(max(steps)),
        )

    def exercise(self) -> None:
        self._validate(); self._encode(); self._decode()
        self.scheduler.snapshot(); self.scheduler.step()

    def close(self) -> None:
        try:
            self.scheduler.stop(self.clock())
            for _ in range(100):
                if self.scheduler.step().state is SchedulerState.STOPPED:
                    break
                time.sleep(0.001)
        except Exception:
            pass


class _SYSTEM_INFO(ctypes.Structure):
    _fields_ = [
        ("wProcessorArchitecture", ctypes.c_ushort), ("wReserved", ctypes.c_ushort),
        ("dwPageSize", ctypes.c_ulong), ("lpMinimumApplicationAddress", ctypes.c_void_p),
        ("lpMaximumApplicationAddress", ctypes.c_void_p),
        ("dwActiveProcessorMask", ctypes.c_size_t), ("dwNumberOfProcessors", ctypes.c_ulong),
        ("dwProcessorType", ctypes.c_ulong), ("dwAllocationGranularity", ctypes.c_ulong),
        ("wProcessorLevel", ctypes.c_ushort), ("wProcessorRevision", ctypes.c_ushort),
    ]


def _private_working_set_mib() -> Decimal:
    if sys.platform != "win32":
        _fail("host_unsupported")
    blocks = None
    system = None
    try:
        get_process = ctypes.windll.kernel32.GetCurrentProcess
        get_process.restype = ctypes.c_void_p
        query = ctypes.windll.psapi.QueryWorkingSet
        query.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)
        query.restype = ctypes.c_bool
        process = get_process()
        system = _SYSTEM_INFO()
        ctypes.windll.kernel32.GetSystemInfo(ctypes.byref(system))
        size = 1024 * 1024
        for _ in range(8):
            buffer = ctypes.create_string_buffer(size)
            if query(process, buffer, size):
                count = ctypes.c_size_t.from_buffer_copy(buffer).value
                required = ctypes.sizeof(ctypes.c_size_t) * (count + 1)
                if required <= size:
                    blocks = (ctypes.c_size_t * count).from_buffer_copy(
                        buffer, ctypes.sizeof(ctypes.c_size_t))
                    break
            size *= 2
    except (AttributeError, OSError):
        pass
    if blocks is None or system is None or system.dwPageSize <= 0:
        _fail("memory_probe_unavailable")
    # PSAPI_WORKING_SET_BLOCK: Protection(5), ShareCount(3), Shared(1).
    private_pages = sum(1 for block in blocks if not block & (1 << 8))
    return _canonical_decimal(Decimal(private_pages * system.dwPageSize) / _MIB)


def _run_soak(probes: _RealProbes, *, clock_ns: Callable[[], int] = time.perf_counter_ns,
              sleeper: Callable[[float], None] = time.sleep,
              memory_reader: Callable[[], Decimal] = _private_working_set_mib,
              duration_ms: int = MEMORY_MIN_DURATION_MS) -> tuple[int, Decimal, Decimal]:
    if duration_ms != MEMORY_MIN_DURATION_MS:
        _fail("duration_not_frozen")
    started = clock_ns()
    baseline = memory_reader()
    peak = baseline
    while (clock_ns() - started) // 1_000_000 < duration_ms:
        probes.exercise()
        peak = max(peak, memory_reader())
        sleeper(0.05)
    elapsed = (clock_ns() - started) // 1_000_000
    final = memory_reader()
    peak = max(peak, final)
    return elapsed, peak, _canonical_decimal(max(_ZERO, peak - baseline))


def collect_current_host(*, expected_head: str, expected_host_id: str,
                         repo: Path = ROOT) -> HostArtifact:
    """Collect one exact-head artifact; any uncertainty raises a stable refusal."""
    probes = None
    result = None
    failure = None
    try:
        actual_head, dirty = _git_state(repo)
        actual_host = derive_host_id()
        _validate_identity(
            expected_head, expected_host_id, actual_head, actual_host,
            platform.python_version(), dirty,
        )
        probes = _RealProbes(repo)
        measurements = probes.measure()
        elapsed, private_mib, growth_mib = _run_soak(probes)
        final_head, final_dirty = _git_state(repo)
        _validate_identity(
            expected_head, expected_host_id, final_head, derive_host_id(),
            platform.python_version(), final_dirty,
        )
        result = build_artifact(
            head_sha=expected_head, host_id=expected_host_id,
            python_version=CURRENT_HOST_PYTHON_VERSION,
            workload_sha256=probes.workload_sha256, duration_ms=elapsed,
            measurements=measurements, private_working_set_mib=private_mib,
            growth_mib=growth_mib,
        )
    except KeyboardInterrupt:
        failure = CollectorError("collection_interrupted")
    except CollectorError as error:
        failure = CollectorError(error.code)
    except Exception:
        failure = CollectorError("collection_failed")
    finally:
        if probes is not None:
            probes.close()
    if failure is not None:
        raise failure
    if result is None:
        raise CollectorError("collection_failed")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-head")
    parser.add_argument("--expected-host-id")
    parser.add_argument("--print-host-id", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.print_host_id:
            print(derive_host_id())
            return 0
        if args.expected_head is None or args.expected_host_id is None:
            _fail("expected_identity_required")
        artifact = collect_current_host(
            expected_head=args.expected_head, expected_host_id=args.expected_host_id)
        output = ROOT / "dev_out" / "base_clean" / f"host_rebaseline_{artifact.body.head_sha}.json"
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
