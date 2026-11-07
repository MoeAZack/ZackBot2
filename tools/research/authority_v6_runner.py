"""One-command, fail-closed orchestration for the frozen authority-v6 build.

The economic and authority rules live in :mod:`tools.research.authority_v6`.
This module only sequences those rules, supplies bounded acquisition budgets, and
commits the four outputs after the zero-gap gate has passed.  Its backend is
injectable so the orchestration can be tested without network or market reads.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from tools.research import authority_v6 as V6


PUBLICATION_ROOT_REL = Path("research_evidence/authority_v6_publications")
CURRENT_POINTER_REL = Path("research_evidence/authority_v6_current.json")
STAGE_NAME = ".authority-v6-stage"
ARTIFACT_FILES = {
    "manifest_v6": "manifest-v6.json.gz",
    "pit_v9": "pit-v9.json",
    "filter_v4": "filter-v4.json",
    "coverage_v4": "coverage-v4.json",
}


class RunnerError(V6.AuthorityV6Error):
    """An orchestration invariant failed before authority publication."""


@dataclass(frozen=True)
class RunConfig:
    root: Path
    store: Path
    ledger: Path
    confirm_plan_sha256: str
    max_seed_bytes: int
    max_intraday_bytes: int
    max_funding_bytes: int
    accessed_cairo: str


def _strict_ledger_preflight(ledger: Path) -> None:
    if not ledger.exists():
        return
    raw = ledger.read_bytes()
    if raw:
        # This is the same complete attempt/link state-machine validation used by
        # manifest-v6, deliberately run before either network-capable seam.
        V6._validate_ledger_raw(raw)


def _output_paths(plan: Mapping, root: Path) -> dict[str, Path]:
    paths = {}
    for name in ("manifest_v6", "pit_v9", "filter_v4", "coverage_v4"):
        rel = plan["outputs"][name]["path"]
        if not isinstance(rel, str) or Path(rel).is_absolute():
            raise RunnerError(f"{name} output path is invalid")
        path = (root.resolve() / Path(*rel.split("/"))).resolve()
        if root.resolve() not in path.parents:
            raise RunnerError(f"{name} output escapes repository root")
        paths[name] = path
    return paths


def _publication_preflight(plan: Mapping, config: RunConfig) -> tuple[dict[str, Path], Path, Path, Path]:
    logical = _output_paths(plan, config.root)
    present = sorted(name for name, path in logical.items() if path.exists())
    if present:
        raise RunnerError(f"legacy direct authority-v6 output paths exist; restart refused: {present}")
    publication_root = config.root / PUBLICATION_ROOT_REL
    stage = publication_root / STAGE_NAME
    current = config.root / CURRENT_POINTER_REL
    current_part = current.with_name(current.name + ".part")
    if current.exists():
        resolve_current_outputs(plan, config.root)
        raise RunnerError("authority-v6 publication is already committed")
    if current_part.exists():
        raise RunnerError("partial authority-v6 current pointer exists; restart refused")
    if stage.exists():
        raise RunnerError("stale authority-v6 output stage exists; restart refused")
    if publication_root.exists() and any(publication_root.iterdir()):
        raise RunnerError("orphan authority-v6 publication exists without a current pointer")
    if config.store.exists() and next(config.store.rglob("*.part"), None) is not None:
        raise RunnerError("authority store contains a partial atomic-write file")
    _strict_ledger_preflight(config.ledger)
    return logical, publication_root, stage, current


def resolve_current_outputs(plan: Mapping, root: Path) -> dict[str, Path]:
    """Resolve one committed publication; the pointer is the sole visibility gate."""
    pointer_path = root / CURRENT_POINTER_REL
    try:
        pointer = V6._load_strict(pointer_path.read_bytes(), "authority-v6 current pointer")
    except OSError as exc:
        raise RunnerError("authority-v6 has no committed current publication") from exc
    if (not isinstance(pointer, dict)
            or pointer.get("format") != "zb-authority-v6-publication-pointer/1"
            or pointer.get("authority_id") != plan.get("authority_id")
            or pointer.get("plan_sha256") != V6.PLAN_SHA256
            or pointer.get("digest") != V6.content_digest(pointer)):
        raise RunnerError("authority-v6 current pointer is malformed or unbound")
    publication_id = pointer.get("publication_id")
    if not V6.HEX64.fullmatch(publication_id or ""):
        raise RunnerError("authority-v6 publication identity is malformed")
    publication_root = (root / PUBLICATION_ROOT_REL).resolve()
    bundle = (publication_root / publication_id).resolve()
    if bundle.parent != publication_root or not bundle.is_dir():
        raise RunnerError("authority-v6 committed publication directory is missing")
    entries = pointer.get("artifacts")
    _output_paths(plan, root)
    if not isinstance(entries, dict) or set(entries) != set(ARTIFACT_FILES):
        raise RunnerError("authority-v6 current pointer has an incomplete artifact set")
    try:
        publication = V6._load_strict(
            (bundle / "publication.json").read_bytes(), "authority-v6 publication")
    except OSError as exc:
        raise RunnerError("authority-v6 committed publication metadata is missing") from exc
    expected_hashes = {name: entries[name].get("byte_sha256")
                       for name in ARTIFACT_FILES if isinstance(entries[name], dict)}
    if (not isinstance(publication, dict)
            or publication.get("format") != "zb-authority-v6-publication/1"
            or publication.get("publication_id") != publication_id
            or publication.get("authority_id") != plan.get("authority_id")
            or publication.get("plan_sha256") != V6.PLAN_SHA256
            or publication.get("required_result") != "ZERO_GUARANTEED_PERIOD_GAPS"
            or publication.get("artifacts") != expected_hashes
            or publication.get("digest") != V6.content_digest(publication)):
        raise RunnerError("authority-v6 committed publication metadata is malformed or unbound")
    resolved = {}
    for name, filename in ARTIFACT_FILES.items():
        entry = entries[name]
        if (not isinstance(entry, dict)
                or entry.get("logical_path") != plan["outputs"][name]["path"]
                or entry.get("bundle_file") != filename
                or not V6.HEX64.fullmatch(entry.get("byte_sha256", ""))):
            raise RunnerError(f"authority-v6 current pointer has a malformed {name} binding")
        path = bundle / filename
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise RunnerError(f"authority-v6 committed {name} is missing") from exc
        if V6.sha256(raw) != entry["byte_sha256"]:
            raise RunnerError(f"authority-v6 committed {name} bytes changed")
        resolved[name] = path
    return resolved


def _complete_vision(result: Mapping, requests: Sequence[V6.VisionRequest], label: str) -> tuple[str, ...]:
    expected = {request.path for request in requests}
    verified = set(result.get("verified", ()))
    absent = set(result.get("official_404", ()))
    failures = set(result.get("checksum_failure", ()))
    if failures or verified & absent or verified | absent != expected:
        raise RunnerError(f"{label} acquisition did not record one terminal decision per request")
    return tuple(sorted(verified))


def _load_manifest_v5(root: Path) -> tuple[dict, bytes]:
    plan = V6.load_frozen_plan(root / V6.PLAN_PATH.relative_to(V6.ROOT))
    raw = (root / plan["base_authorities"]["manifest_v5"]["path"]).read_bytes()
    if V6.sha256(raw) != plan["base_authorities"]["manifest_v5"]["byte_sha256"]:
        raise RunnerError("manifest-v5 exact bytes changed")
    value = V6._load_strict(gzip.decompress(raw), "manifest-v5")
    if not isinstance(value, dict) or value.get("digest") != plan["base_authorities"]["manifest_v5"]["content_digest"]:
        raise RunnerError("manifest-v5 content binding changed")
    return value, raw


def _read_record_rows(store: Path, record: Mapping, *, new: bool) -> tuple[dict, list]:
    rel = record["path"]
    target = V6._safe_store_path(store, rel)
    source = record["source"]
    kind = source.get("kind")
    if kind == "binance-vision-archive":
        raw, published, csv_raw = V6.M.read_archive(str(target), rel)
        rows = V6.M.parse_archive_csv(rel, csv_raw, V6.M.archive_meta(rel))
        expected = record.get("raw_sha256") if new else record.get("sha256")
        if published != expected or V6.sha256(raw) != expected:
            raise RunnerError(f"authority source bytes changed: {rel}")
    elif kind == V6.M.FUNDING_API_KIND:
        expected = record.get("raw_sha256") if new else record.get("sha256")
        if new:
            verified = V6._scan_v6_record_unledgered(store, rel)
            if (verified.get("raw_sha256") != expected
                    or verified.get("parsed_content_digest") != record.get("parsed_content_digest")):
                raise RunnerError(f"new funding authority record changed: {rel}")
            raw = target.read_bytes()
            query = V6.parse_qs_strict(source.get("url", ""))
            try:
                start_ms = int(query["startTime"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RunnerError(f"new funding authority URL changed: {rel}") from exc
            rows = V6.parse_funding_page(raw, record["symbol"], start_ms)
        else:
            raw, rows = V6.M.read_funding_api(str(target), rel, source)
        if V6.sha256(raw) != expected:
            raise RunnerError(f"authority source bytes changed: {rel}")
    elif kind == V6.M.KLINE_API_KIND:
        raw, rows = V6.M.read_kline_api(str(target), rel, source)
        expected = record.get("raw_sha256") if new else record.get("sha256")
        if V6.sha256(raw) != expected:
            raise RunnerError(f"authority source bytes changed: {rel}")
    else:
        raise RunnerError(f"unsupported authority source kind: {kind}")
    return V6.M.archive_meta(rel), rows


def _merge_daily(target: dict[str, dict[int, Decimal]], symbol: str, rows: Sequence) -> None:
    dest = target.setdefault(symbol, {})
    for row in rows:
        time_ms = int(row[0])
        value = V6.U.qv_decimal(row[7])
        if time_ms in dest and dest[time_ms] != value:
            raise RunnerError(f"conflicting daily authority row for {symbol} at {time_ms}")
        dest[time_ms] = value


class DefaultBackend:
    """Production adapter.  All market/network effects remain behind this seam."""

    def __init__(self, config: RunConfig):
        self.config = config

    def load_plan(self) -> dict:
        return V6.load_frozen_plan(self.config.root / V6.PLAN_PATH.relative_to(V6.ROOT))

    def seed_symbols(self) -> tuple[str, ...]:
        return V6.frozen_seed_symbols(self.config.root)

    def seed_requests(self, symbols: Sequence[str]) -> tuple[V6.VisionRequest, ...]:
        return V6.daily_seed_requests(symbols)

    def acquire_vision(self, requests, *, max_total_bytes: int, phase: str, pit_v9=None):
        return V6.acquire_vision(
            requests, self.config.store, self.config.ledger,
            max_total_bytes=max_total_bytes, phase=phase, pit_v9=pit_v9,
            root=self.config.root)

    def _base_metadata(self):
        plan = self.load_plan()
        pit, _ = V6._load_bound_json(self.config.root, plan["base_authorities"]["pit_v8"], "PIT-v8")
        classes, _ = V6._load_bound_json(
            self.config.root, plan["base_authorities"]["instrument_classes_v1"], "instrument classes")
        manifest, _ = _load_manifest_v5(self.config.root)
        return pit, classes, manifest

    def _daily(self, new_paths: Sequence[str], manifest: Mapping | None = None) -> dict:
        _, _, base = self._base_metadata()
        daily = {symbol: {} for symbol in V6.frozen_seed_symbols(self.config.root)}
        base_files = base["files"] if manifest is None else manifest["active_base_files_v5"]
        for record in base_files:
            if record.get("series") == "klines" and record.get("interval") == "1d":
                _, rows = _read_record_rows(self.config.store, record, new=False)
                _merge_daily(daily, record["symbol"], rows)
        records = []
        if manifest is None:
            for path in sorted(new_paths):
                record = V6._scan_v6_record_unledgered(self.config.store, path)
                if record["series"] == "klines" and record["interval"] == "1d":
                    records.append(record)
        else:
            records = [record for record in manifest["files_v6"]
                       if record["series"] == "klines" and record["interval"] == "1d"]
        for record in records:
            _, rows = _read_record_rows(self.config.store, record, new=True)
            _merge_daily(daily, record["symbol"], rows)
        if set(daily) != set(V6.frozen_seed_symbols(self.config.root)):
            raise RunnerError("daily authority changed the exact seed symbol set")
        return daily

    def build_first_pass(self, seed_paths: Sequence[str], provisional_digest: str) -> dict:
        pit, classes, _ = self._base_metadata()
        return V6.build_pit_v9_first_pass(
            pit, self._daily(seed_paths), classes,
            provisional_manifest_digest=provisional_digest, root=self.config.root)

    def intraday_symbols(self, pit_v9: Mapping) -> tuple[str, ...]:
        return V6.frozen_pit_union_symbols(pit_v9, self.config.root)

    def intraday_requests(self, symbols: Sequence[str]) -> tuple[V6.VisionRequest, ...]:
        return V6.intraday_requests(symbols)

    def capture_funding(self, symbols: Sequence[str], pit_v9: Mapping):
        return V6.capture_funding_residual(
            symbols, self.config.store, self.config.ledger,
            max_total_bytes=self.config.max_funding_bytes, pit_v9=pit_v9,
            root=self.config.root, accessed_cairo=self.config.accessed_cairo)

    def funding_paths(self, symbols: Sequence[str]) -> tuple[str, ...]:
        allowed = set(symbols)
        paths = set()
        for line in self.config.ledger.read_bytes().splitlines():
            row = V6._load_strict(line, "acquisition ledger row")
            if (isinstance(row, dict) and row.get("kind") == "FUNDING_API"
                    and row.get("symbol") in allowed
                    and row.get("decision") in ("PRESERVED", "INCOMPLETE_EMPTY")
                    and isinstance(row.get("path"), str)):
                paths.add(row["path"])
        return tuple(sorted(paths))

    def build_manifest(self, paths: Sequence[str], pit_v9: Mapping) -> dict:
        plan = self.load_plan()
        base_path = self.config.root / plan["base_authorities"]["manifest_v5"]["path"]
        return V6.build_manifest_v6_from_store(
            base_path, paths, self.config.store, self.config.ledger, pit_v9)

    def replay_pit(self, first_pass: Mapping, manifest: Mapping) -> dict:
        pit, classes, _ = self._base_metadata()
        replay = V6.build_pit_v9_first_pass(
            pit, self._daily((), manifest), classes,
            provisional_manifest_digest=manifest["digest"], root=self.config.root)
        return V6.finalize_pit_v9(first_pass, replay, manifest_digest=manifest["digest"])

    def build_filter(self, pit_v9: Mapping) -> dict:
        plan = self.load_plan()
        base_path = self.config.root / plan["base_authorities"]["filter_v3"]["path"]
        base_raw = base_path.read_bytes()
        base = V6._load_strict(base_raw, "filter-v3")
        payloads = {row["captured_at"]: (self.config.root / row["path"]).read_bytes()
                    for row in base["snapshots"]}
        return V6.build_filter_v4(
            base_raw, pit_v9, payloads, pit_byte_sha256=V6.sha256(V6.canonical(pit_v9)))

    def build_coverage(self, manifest: Mapping, pit_v9: Mapping, filter_v4: Mapping) -> dict:
        needed = set(V6.frozen_pit_union_symbols(pit_v9, self.config.root))
        rows_by_key: dict[tuple[str, str | None, str], set[int]] = {}
        funding: dict[str, dict[int, int | None]] = {}
        records = [(row, False) for row in manifest["active_base_files_v5"]]
        records += [(row, True) for row in manifest["files_v6"]]
        for record, new in records:
            if record.get("symbol") not in needed:
                continue
            meta, rows = _read_record_rows(self.config.store, record, new=new)
            key = (meta["series"], meta["interval"], meta["symbol"])
            times = rows_by_key.setdefault(key, set())
            for row in rows:
                time_ms = row["fundingTime"] if isinstance(row, Mapping) else int(row[0])
                if time_ms in times:
                    raise RunnerError(f"duplicate resolved authority timestamp: {key} {time_ms}")
                times.add(time_ms)
                if meta["series"] == "fundingRate":
                    hours = None if isinstance(row, Mapping) else int(row[1])
                    events = funding.setdefault(meta["symbol"], {})
                    if time_ms in events:
                        raise RunnerError(f"duplicate resolved funding event: {meta['symbol']} {time_ms}")
                    events[time_ms] = hours
        row_index = {key: tuple(sorted(values)) for key, values in rows_by_key.items()}
        funding_events = {symbol: tuple(sorted(values.items())) for symbol, values in funding.items()}
        return V6.coverage_v4(
            pit_v9, filter_v4, row_index, funding_events,
            manifest_digest=manifest["digest"])


def run(config: RunConfig, *, backend=None, emit=print) -> dict:
    """Run the frozen vertical and publish only after the zero-gap gate."""
    if min(config.max_seed_bytes, config.max_intraday_bytes, config.max_funding_bytes) <= 0:
        raise RunnerError("all three acquisition byte caps must be positive")
    backend = backend or DefaultBackend(config)
    plan = backend.load_plan()
    if config.confirm_plan_sha256 != V6.PLAN_SHA256:
        raise RunnerError("operator confirmation does not match the frozen plan checksum")
    _, publication_root, stage, current = _publication_preflight(plan, config)
    symbols = tuple(backend.seed_symbols())
    seed_requests = tuple(backend.seed_requests(symbols))
    plan_notice = {
        "authority_id": plan["authority_id"], "version": plan["version"],
        "plan_sha256": V6.PLAN_SHA256, "cutoff_exclusive_ms": V6.CUTOFF_MS,
        "seed_symbols": len(symbols), "seed_requests": len(seed_requests),
        "confirmed": True,
    }
    emit(json.dumps(plan_notice, sort_keys=True))

    _strict_ledger_preflight(config.ledger)
    seed_result = backend.acquire_vision(
        seed_requests, max_total_bytes=config.max_seed_bytes, phase="seed")
    seed_paths = _complete_vision(seed_result, seed_requests, "daily seed")
    provisional = V6.sha256(V6.canonical({
        "plan_sha256": V6.PLAN_SHA256, "verified_seed_paths": seed_paths}))
    first_pass = backend.build_first_pass(seed_paths, provisional)

    intraday_symbols = tuple(backend.intraday_symbols(first_pass))
    if intraday_symbols != tuple(sorted(set(intraday_symbols))) or not set(intraday_symbols) <= set(symbols):
        raise RunnerError("derived intraday symbol set is not the exact sorted PIT-v9 subset")
    intraday_requests = tuple(backend.intraday_requests(intraday_symbols))
    if any(request.symbol not in set(intraday_symbols) for request in intraday_requests):
        raise RunnerError("intraday request escaped the exact PIT-v9 symbol set")
    _strict_ledger_preflight(config.ledger)
    intraday_result = backend.acquire_vision(
        intraday_requests, max_total_bytes=config.max_intraday_bytes,
        phase="intraday", pit_v9=first_pass)
    intraday_paths = _complete_vision(intraday_result, intraday_requests, "intraday")
    _strict_ledger_preflight(config.ledger)
    backend.capture_funding(intraday_symbols, first_pass)
    funding_paths = tuple(backend.funding_paths(intraday_symbols))

    _strict_ledger_preflight(config.ledger)
    manifest = backend.build_manifest(
        tuple(sorted(set(seed_paths + intraday_paths + funding_paths))), first_pass)
    _strict_ledger_preflight(config.ledger)
    pit_v9 = backend.replay_pit(first_pass, manifest)
    filter_v4 = backend.build_filter(pit_v9)
    coverage_v4 = backend.build_coverage(manifest, pit_v9, filter_v4)
    if (coverage_v4.get("required_result") != "ZERO_GUARANTEED_PERIOD_GAPS"
            or coverage_v4.get("guaranteed_period_gaps") != 0
            or coverage_v4.get("gaps") != []):
        raise RunnerError("coverage-v4 did not prove ZERO_GUARANTEED_PERIOD_GAPS")

    _strict_ledger_preflight(config.ledger)
    artifacts = {"manifest_v6": manifest, "pit_v9": pit_v9,
                 "filter_v4": filter_v4, "coverage_v4": coverage_v4}
    publication_root.mkdir(parents=True, exist_ok=True)
    stage.mkdir(parents=False)
    staged: dict[str, Path] = {}
    try:
        for name, value in artifacts.items():
            staged[name] = stage / ARTIFACT_FILES[name]
            V6.write_json_immutable(value, staged[name], gzip_output=(name == "manifest_v6"))
        artifact_hashes = {name: V6.sha256(path.read_bytes()) for name, path in staged.items()}
        publication_id = V6.sha256(V6.canonical({
            "authority_id": plan["authority_id"], "plan_sha256": V6.PLAN_SHA256,
            "artifacts": artifact_hashes,
        }))
        version_dir = publication_root / publication_id
        publication = {
            "format": "zb-authority-v6-publication/1", "publication_id": publication_id,
            "authority_id": plan["authority_id"], "plan_sha256": V6.PLAN_SHA256,
            "required_result": "ZERO_GUARANTEED_PERIOD_GAPS",
            "artifacts": artifact_hashes,
        }
        publication["digest"] = V6.content_digest(publication)
        V6.write_json_immutable(publication, stage / "publication.json")
        if version_dir.exists():
            raise RunnerError("immutable authority-v6 publication directory already exists")
        # One same-volume directory rename makes all four artifacts exist together,
        # but they remain invisible to readers until the single pointer commit.
        os.replace(stage, version_dir)
        pointer = {
            "format": "zb-authority-v6-publication-pointer/1",
            "authority_id": plan["authority_id"], "plan_sha256": V6.PLAN_SHA256,
            "publication_id": publication_id,
            "required_result": "ZERO_GUARANTEED_PERIOD_GAPS",
            "artifacts": {
                name: {"logical_path": plan["outputs"][name]["path"],
                       "bundle_file": ARTIFACT_FILES[name],
                       "byte_sha256": artifact_hashes[name]}
                for name in ARTIFACT_FILES
            },
        }
        pointer["digest"] = V6.content_digest(pointer)
        V6.write_json_immutable(pointer, current)
    except Exception:
        # Preserve staged/orphan evidence.  Without the pointer no reader can see a
        # partial authority, and the next run refuses the state before acquisition.
        raise
    outputs = resolve_current_outputs(plan, config.root)
    result = {
        "authority_id": plan["authority_id"], "plan_sha256": V6.PLAN_SHA256,
        "intraday_symbols": list(intraday_symbols),
        "verified_seed_paths": len(seed_paths),
        "verified_intraday_paths": len(intraday_paths),
        "funding_paths": len(funding_paths),
        "required_result": "ZERO_GUARANTEED_PERIOD_GAPS",
        "outputs": {name: str(path) for name, path in outputs.items()},
        "current_pointer": str(current), "publication_id": publication_id,
    }
    emit(json.dumps(result, sort_keys=True))
    return result


def _cairo_now() -> str:
    return datetime.now(ZoneInfo("Africa/Cairo")).isoformat(timespec="seconds")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--confirm-plan-sha256", required=True)
    parser.add_argument("--max-seed-bytes", type=int, required=True)
    parser.add_argument("--max-intraday-bytes", type=int, required=True)
    parser.add_argument("--max-funding-bytes", type=int, required=True)
    parser.add_argument("--root", type=Path, default=V6.ROOT)
    args = parser.parse_args(argv)
    config = RunConfig(
        root=args.root.resolve(), store=args.store.resolve(), ledger=args.ledger.resolve(),
        confirm_plan_sha256=args.confirm_plan_sha256,
        max_seed_bytes=args.max_seed_bytes, max_intraday_bytes=args.max_intraday_bytes,
        max_funding_bytes=args.max_funding_bytes, accessed_cairo=_cairo_now())
    try:
        run(config)
        return 0
    except (RunnerError, V6.AuthorityV6Error, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
