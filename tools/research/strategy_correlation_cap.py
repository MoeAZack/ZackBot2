"""Offline shadow replay of preregistered strategy correlation/risk caps.

Reads only a manifest-pinned Phase-C snapshot.  It never imports the engine,
contacts an exchange, or changes PAPER/runtime state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import actual_costs as AC
import true_books as TB
from tools.research import phase_c_report as PC


SCHEMA = "zackbot-shadow-correlation-cap-result/1"
PREREG = "research_evidence/prereg/strategy_correlation_cap_shadow.v1.json"
ADAPTER_VERSION = "phase-c-closed-position-adapter/1"
CLUSTER_GAP_SECONDS = 60
RISK_CAP_R = Decimal("2")
ENTRY_KINDS = frozenset({"entry", "dca", "pyramid", "add"})
VARIANTS = (
    ("baseline", None, None),
    ("cap_1_r2", 1, RISK_CAP_R),
    ("cap_2_r2", 2, RISK_CAP_R),
    ("cap_3_r2", 3, RISK_CAP_R),
)
ZERO = Decimal("0")


class StudyError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def fail(code: str) -> None:
    raise StudyError(code) from None


def D(value: Any, code: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        fail(code)
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        fail(code)
    if not result.is_finite() or positive and result <= ZERO:
        fail(code)
    return result


def instant(value: Any, code: str) -> datetime:
    if type(value) is not str:
        fail(code)
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail(code)
    if result.tzinfo is None:
        fail(code)
    return result.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def cairo(value: datetime | None) -> str | None:
    if value is None:
        return None
    from zoneinfo import ZoneInfo
    return value.astimezone(ZoneInfo("Africa/Cairo")).isoformat()


def ref(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def quote(symbol: str) -> str:
    for asset in ("USDT", "USDC"):
        if symbol.endswith(asset) and len(symbol) > len(asset):
            return asset
    return "OTHER"


@dataclass(frozen=True, slots=True)
class Position:
    ref: str
    source_id: str
    strategy_key: str
    symbol: str
    direction: str
    opened: datetime
    closed: datetime | None
    initial_risk_r: Decimal
    net_r: Decimal | None
    entry_turnover: Decimal
    exit_turnover: Decimal
    quote_asset: str
    complete: bool
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Event:
    at: datetime
    sequence: int
    kind: str
    position: Position


def _fills(row: dict[str, Any], code: str) -> tuple[tuple[datetime, str, Decimal, Decimal], ...]:
    raw = row.get("fills")
    if type(raw) is not list or not raw:
        fail(code)
    refs = {item[0]: item[1] for item in (row.get("fill_refs") or ())
            if type(item) in (list, tuple) and len(item) == 2 and type(item[0]) is int}
    actual = row.get("px_actual") or {}
    result = []
    for index, item in enumerate(raw):
        if type(item) not in (list, tuple) or len(item) < 4 or type(item[1]) is not str:
            fail(code)
        price = actual.get(refs.get(index), item[3])
        result.append((instant(item[0], code), item[1], D(item[2], code, positive=True),
                       D(price, code, positive=True)))
    if any(result[index][0] < result[index - 1][0] for index in range(1, len(result))):
        fail("source.fill_order")
    return tuple(result)


def _base_fields(row: dict[str, Any]) -> tuple[str, str, str, str, Decimal, tuple[str, ...]]:
    source_id = row.get("id")
    strategy = row.get("strategy_key") or row.get("key_strategy")
    symbol, direction = row.get("symbol"), row.get("side")
    if not all(type(value) is str and value for value in (source_id, strategy, symbol)):
        fail("source.identity")
    if direction not in ("LONG", "SHORT"):
        fail("source.direction")
    risk = D(row.get("risk_usd"), "source.risk", positive=True)
    issues = []
    if row.get("manual") is not False:
        issues.append("manual_or_unowned")
    if int(row.get("adds") or 0) or int(row.get("dca") or 0):
        issues.append("risk_changes_after_entry")
    return source_id, strategy, symbol, direction, risk, tuple(issues)


def closed_position(row: dict[str, Any]) -> Position:
    source_id, strategy, symbol, direction, risk, issues = _base_fields(row)
    fills = _fills(row, "source.closed_fills")
    entry = [item for item in fills if item[1] in ENTRY_KINDS]
    exits = [item for item in fills if item[1] not in ENTRY_KINDS]
    if not entry or not exits:
        fail("source.closed_legs")
    opened = instant(row.get("opened"), "source.opened")
    recorded_closed = instant(row.get("closed"), "source.closed")
    closed = exits[-1][0]
    if entry[0][0] != opened or recorded_closed < closed or closed <= opened:
        fail("source.lifecycle_times")
    measured = AC.measured(row)
    if not measured["complete"]:
        issues = tuple(sorted(set(issues) | {f"cost:{why}" for why in measured["why"]}))
        net_r = None
    else:
        net_r = D(measured["net"], "source.net") / risk
    return Position(
        ref(source_id), source_id, strategy, symbol, direction, opened, closed, Decimal("1"), net_r,
        sum((qty * price for _at, kind, qty, price in fills if kind in ENTRY_KINDS), ZERO),
        sum((qty * price for _at, kind, qty, price in fills if kind not in ENTRY_KINDS), ZERO),
        quote(symbol), not issues and net_r is not None, tuple(sorted(issues)),
    )


def open_position(source_id: str, row: dict[str, Any]) -> Position:
    copied = dict(row, id=source_id)
    source_id, strategy, symbol, direction, _risk, issues = _base_fields(copied)
    fills = _fills(copied, "source.open_fills")
    entry = [item for item in fills if item[1] in ENTRY_KINDS]
    if not entry or any(item[1] not in ENTRY_KINDS for item in fills):
        fail("source.open_legs")
    opened = instant(row.get("opened"), "source.opened")
    if entry[0][0] != opened:
        fail("source.lifecycle_times")
    return Position(
        ref(source_id), source_id, strategy, symbol, direction, opened, None, Decimal("1"), None,
        sum((qty * price for _at, _kind, qty, price in entry), ZERO), ZERO,
        quote(symbol), False, tuple(sorted(set(issues) | {"right_censored_open"})),
    )


def events_of(positions: Iterable[Position]) -> tuple[Event, ...]:
    raw: list[tuple[datetime, str, Position]] = []
    for position in positions:
        raw.append((position.opened, "entry", position))
        if position.closed is not None:
            raw.append((position.closed, "exit", position))
    raw.sort(key=lambda item: item[0])
    ties = {at for at, _kind, _position in raw if sum(row[0] == at for row in raw) > 1}
    if ties:
        fail("source.ambiguous_event_order")
    return tuple(Event(at, sequence, kind, position)
                 for sequence, (at, kind, position) in enumerate(raw, 1))


def load_positions(snapshot: Path, manifest: Path) -> tuple[tuple[Position, ...], dict[str, Any]]:
    try:
        man, pins = PC.preflight(str(snapshot), str(manifest))
        source = PC.load_snapshot(str(snapshot), man)
    except Exception:
        fail("snapshot.invalid")
    if source["torn"]:
        fail("snapshot.torn")
    cohort = source.get("cohort")
    if type(cohort) is not dict or type(cohort.get("id")) is not str:
        fail("snapshot.cohort")
    cohort_id = cohort["id"]
    rows = TB.merge_rows(source["cohort_rows"] + source["history"])
    closed = [closed_position(row) for row in rows if row.get("cohort_id") == cohort_id]
    raw_lots = (source.get("state") or {}).get("lots") or {}
    if type(raw_lots) is not dict:
        fail("snapshot.open_lots")
    opened = [open_position(source_id, row) for source_id, row in raw_lots.items()
              if type(row) is dict and row.get("cohort_id") == cohort_id]
    positions = tuple(sorted(closed + opened, key=lambda row: (row.opened, row.ref)))
    if len({row.source_id for row in positions}) != len(positions):
        fail("source.duplicate_position")
    events_of(positions)
    metadata = {
        "snapshot_utc": man["snapshot_utc"],
        "manifest_sha256": pins["manifest_sha256"],
        "cohort_id": cohort_id,
        "cohort_started_utc": cohort.get("started_utc"),
        "closed_positions": len(closed),
        "open_positions": len(opened),
        "source_files": {name: record["sha256"] for name, record in sorted(man["files"].items())},
    }
    return positions, metadata


def latest_cluster(positions: Iterable[Position], strategy_key: str = "exp_vwaprev") -> tuple[Position, ...]:
    entries = sorted((row for row in positions if row.strategy_key == strategy_key), key=lambda row: row.opened)
    groups: list[list[Position]] = []
    for row in entries:
        if not groups or (row.opened - groups[-1][-1].opened).total_seconds() > CLUSTER_GAP_SECONDS:
            groups.append([row])
        else:
            groups[-1].append(row)
    eligible = [group for group in groups if len(group) >= 2]
    if not eligible:
        fail("cluster.not_found")
    return tuple(eligible[-1])


def _max_drawdown(values: Iterable[Decimal]) -> Decimal:
    equity = peak = drawdown = ZERO
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _pf(values: Iterable[Decimal]) -> Decimal | None:
    rows = tuple(values)
    wins = sum((value for value in rows if value > ZERO), ZERO)
    losses = -sum((value for value in rows if value < ZERO), ZERO)
    return None if losses == ZERO else wins / losses


def _fmt(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.quantize(Decimal("0.000001"))
    return format(normalized, "f")


def replay(positions: tuple[Position, ...], variant: tuple[str, int | None, Decimal | None],
           *, candidate_refs: set[str] | None = None, seed_refs: set[str] | None = None,
           through: datetime | None = None) -> dict[str, Any]:
    variant_id, max_positions, max_risk = variant
    candidate_refs = {row.ref for row in positions} if candidate_refs is None else set(candidate_refs)
    seed_refs = set() if seed_refs is None else set(seed_refs)
    relevant = tuple(row for row in positions if row.ref in candidate_refs | seed_refs
                     and row.opened <= (through or datetime.max.replace(tzinfo=timezone.utc)))
    events = events_of(relevant)
    open_rows: dict[str, Position] = {}
    accepted: set[str] = set()
    blocked: dict[str, str] = {}
    both = 0
    closed_rows: list[Position] = []
    peaks_positions: dict[str, int] = {}
    peaks_risk: dict[str, Decimal] = {}
    for event in events:
        if through is not None and event.at > through:
            break
        row = event.position
        if event.kind == "exit":
            if row.ref in accepted:
                open_rows.pop(row.ref, None)
                if row.ref in candidate_refs:
                    closed_rows.append(row)
            continue
        same = sum(item.strategy_key == row.strategy_key and item.direction == row.direction
                   for item in open_rows.values())
        open_risk = sum((item.initial_risk_r for item in open_rows.values()
                         if item.strategy_key == row.strategy_key), ZERO)
        if row.ref in seed_refs:
            accepted.add(row.ref)
            open_rows[row.ref] = row
            pkey = f"{row.strategy_key}|{row.direction}"
            peaks_positions[pkey] = max(peaks_positions.get(pkey, 0), same + 1)
            peaks_risk[row.strategy_key] = max(peaks_risk.get(row.strategy_key, ZERO),
                                               open_risk + row.initial_risk_r)
            continue
        if row.ref not in candidate_refs:
            continue
        position_hit = max_positions is not None and same >= max_positions
        risk_hit = max_risk is not None and open_risk + row.initial_risk_r > max_risk
        if position_hit or risk_hit:
            if position_hit and risk_hit:
                both += 1
            blocked[row.ref] = ("same_strategy_direction_position_cap" if position_hit
                                else "strategy_open_risk_cap")
            continue
        accepted.add(row.ref)
        open_rows[row.ref] = row
        pkey = f"{row.strategy_key}|{row.direction}"
        peaks_positions[pkey] = max(peaks_positions.get(pkey, 0), same + 1)
        peaks_risk[row.strategy_key] = max(peaks_risk.get(row.strategy_key, ZERO),
                                           open_risk + row.initial_risk_r)
    accepted_candidates = [row for row in relevant if row.ref in candidate_refs and row.ref in accepted]
    incomplete = sorted({issue for row in accepted_candidates for issue in row.issues})
    if through is not None and any(row.closed is None or row.closed > through for row in accepted_candidates):
        incomplete.append("right_censored_at_window")
    realized = [row.net_r for row in closed_rows if row.net_r is not None]
    if len(realized) != len(closed_rows):
        incomplete.append("accepted_close_without_net_r")
    turnover: dict[str, Decimal] = {}
    for row in accepted_candidates:
        turnover[row.quote_asset] = turnover.get(row.quote_asset, ZERO) + row.entry_turnover
        if row.closed is not None and (through is None or row.closed <= through):
            turnover[row.quote_asset] += row.exit_turnover
    candidate_rows = [row for row in relevant if row.ref in candidate_refs]
    times = [row.opened for row in candidate_rows]
    times += [row.closed for row in candidate_rows if row.closed is not None]
    return {
        "variant": variant_id,
        "status": "complete" if not incomplete else "insufficient",
        "insufficiency_reasons": sorted(set(incomplete)),
        "window": {
            "first_utc": iso(min(times) if times else None),
            "last_utc": iso(max(times) if times else None),
            "first_cairo": cairo(min(times) if times else None),
            "last_cairo": cairo(max(times) if times else None),
        },
        "sample_counts": {
            "candidate_entries": len(candidate_rows),
            "accepted_entries": len(accepted_candidates),
            "blocked_entries": len(blocked),
            "terminal_closed_positions": len(closed_rows),
            "accepted_open_at_end": sum(row.ref in open_rows and row.ref in candidate_refs for row in relevant),
        },
        "net_r": _fmt(sum(realized, ZERO)) if not incomplete else None,
        "profit_factor": _fmt(_pf(realized)) if not incomplete else None,
        "profit_factor_note": "no_losses" if not incomplete and realized and _pf(realized) is None else None,
        "max_drawdown_r": _fmt(_max_drawdown(realized)) if not incomplete else None,
        "closed_only_diagnostic": {
            "terminal_positions_with_net": len(realized),
            "net_r": _fmt(sum(realized, ZERO)),
            "profit_factor": _fmt(_pf(realized)),
            "profit_factor_note": "no_losses" if realized and _pf(realized) is None else None,
            "max_drawdown_r": _fmt(_max_drawdown(realized)),
            "not_causal_when_status_insufficient": bool(incomplete),
        },
        "turnover_by_quote": {asset: _fmt(value) for asset, value in sorted(turnover.items())},
        "blocked_by_reason": {
            "same_strategy_direction_position_cap": sum(reason == "same_strategy_direction_position_cap"
                                                          for reason in blocked.values()),
            "strategy_open_risk_cap": sum(reason == "strategy_open_risk_cap" for reason in blocked.values()),
            "both_conditions_true": both,
        },
        "peak_positions": dict(sorted(peaks_positions.items())),
        "peak_open_r": {key: _fmt(value) for key, value in sorted(peaks_risk.items())},
        "blocked_refs": sorted(blocked),
    }


def last_flat_cutoff(positions: tuple[Position, ...], snapshot_at: datetime) -> datetime | None:
    active: set[str] = set()
    last = None
    for event in events_of(positions):
        if event.at > snapshot_at:
            break
        if event.kind == "entry":
            active.add(event.position.ref)
        else:
            active.discard(event.position.ref)
            if not active:
                last = event.at
    return last


def section(positions: tuple[Position, ...], *, candidate_refs: set[str] | None = None,
            seed_refs: set[str] | None = None, through: datetime | None = None) -> list[dict[str, Any]]:
    return [replay(positions, variant, candidate_refs=candidate_refs, seed_refs=seed_refs, through=through)
            for variant in VARIANTS]


def build(snapshot: Path, manifest: Path, prereg: Path) -> dict[str, Any]:
    prereg_bytes = prereg.read_bytes()
    try:
        registration = json.loads(prereg_bytes)
    except (OSError, ValueError):
        fail("prereg.invalid")
    if registration.get("schema") != "zackbot-shadow-correlation-cap-prereg/1":
        fail("prereg.schema")
    positions, metadata = load_positions(snapshot, manifest)
    snapshot_at = instant(metadata["snapshot_utc"], "snapshot.time")
    cluster = latest_cluster(positions)
    cluster_start = min(row.opened for row in cluster)
    cluster_refs = {row.ref for row in cluster}
    cluster_seeds = {row.ref for row in positions if row.strategy_key == "exp_vwaprev"
                     and row.opened < cluster_start and (row.closed is None or row.closed > cluster_start)}
    exp_refs = {row.ref for row in positions if row.strategy_key == "exp_vwaprev"}
    cutoff = last_flat_cutoff(positions, snapshot_at)
    prefix_refs = ({row.ref for row in positions if row.opened <= cutoff} if cutoff is not None else set())
    return {
        "schema": SCHEMA,
        "study_id": registration["study_id"],
        "adapter_version": ADAPTER_VERSION,
        "prereg_sha256": hashlib.sha256(prereg_bytes).hexdigest(),
        "source": metadata,
        "data_quality": {
            "event_order": "strictly unique UTC lifecycle timestamps; chronological rank is event_sequence",
            "positions": len(positions),
            "complete_positions": sum(row.complete for row in positions),
            "incomplete_positions": sum(not row.complete for row in positions),
            "issues": {issue: sum(issue in row.issues for row in positions)
                       for issue in sorted({issue for row in positions for issue in row.issues})},
            "historical_trade_records_sufficient_for_cluster": all(row.complete for row in cluster),
            "historical_trade_records_sufficient_for_whole_snapshot": all(row.complete for row in positions),
        },
        "recent_exp_vwaprev_cluster": {
            "definition": f"latest exp_vwaprev entry burst with adjacent entry gaps <= {CLUSTER_GAP_SECONDS}s",
            "candidate_refs": sorted(cluster_refs),
            "seed_open_refs": sorted(cluster_seeds),
            "results": section(positions, candidate_refs=cluster_refs, seed_refs=cluster_seeds,
                               through=snapshot_at),
        },
        "exp_vwaprev_full_cohort": {
            "results": section(positions, candidate_refs=exp_refs, through=snapshot_at),
        },
        "whole_cohort_to_snapshot": {
            "warning": "INSUFFICIENT when accepted positions remain open or accepted rows have unresolved cost/risk evidence",
            "results": section(positions, through=snapshot_at),
        },
        "whole_cohort_latest_flat_prefix": {
            "cutoff_utc": iso(cutoff),
            "cutoff_cairo": cairo(cutoff),
            "results": section(positions, candidate_refs=prefix_refs, through=cutoff) if cutoff is not None else [],
        },
        "interpretation_boundary": {
            "shadow_only": True,
            "selection": "all preregistered variants reported; no winner selected",
            "statistical_inference": "none; the complete exp_vwaprev sample has 19 positions and the recent burst has 3",
            "blocked_entries": "zero result and zero turnover; later original entries retain source order",
            "counterfactual_limit": "removed trades do not change prices; no replacement signals or account-state-dependent signals are synthesized",
            "paper_runtime_changed": False,
        },
    }


def canonical(value: Any) -> bytes:
    try:
        return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                           allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        fail("result.noncanonical")


def seal(value: dict[str, Any]) -> dict[str, Any]:
    if "artifact_sha256" in value:
        fail("result.already_sealed")
    result = dict(value)
    result["artifact_sha256"] = hashlib.sha256(canonical(value)).hexdigest()
    return result


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Shadow strategy correlation/risk cap result",
        "",
        f"Snapshot: {result['source']['snapshot_utc']} (Cairo: {cairo(instant(result['source']['snapshot_utc'], 'snapshot.time'))})",
        f"Cohort: `{result['source']['cohort_id']}`; {result['source']['closed_positions']} closed and {result['source']['open_positions']} open positions.",
        f"Artifact body SHA-256: `{result['artifact_sha256']}`.",
        "",
        "All values are descriptive shadow evidence. No variant is selected and no PAPER/runtime setting changed.",
        f"The recent burst has {len(result['recent_exp_vwaprev_cluster']['candidate_refs'])} candidate entries and "
        f"{len(result['recent_exp_vwaprev_cluster']['seed_open_refs'])} pre-existing open exp_vwaprev position(s) at admission.",
        "The complete exp_vwaprev sample has only 19 positions, below a 30-trade inference floor; no p-value, promotion, or best-cell claim is made.",
    ]
    sections = (
        ("Recent exp_vwaprev cluster", result["recent_exp_vwaprev_cluster"]["results"]),
        ("Full exp_vwaprev cohort", result["exp_vwaprev_full_cohort"]["results"]),
        ("Whole cohort to snapshot", result["whole_cohort_to_snapshot"]["results"]),
        ("Whole cohort latest flat prefix", result["whole_cohort_latest_flat_prefix"]["results"]),
    )
    for title, rows in sections:
        lines += ["", f"## {title}"]
        if rows:
            window = rows[0]["window"]
            lines += ["", f"Window: {window['first_cairo']} to {window['last_cairo']} Cairo "
                             f"({window['first_utc']} to {window['last_utc']} UTC)."]
        lines += ["", "| Variant | Status | Candidates | Accepted | Blocked | Net R | PF | DD R | Closed-only diagnostic net / PF / DD | Turnover | Position / risk blocks |",
                  "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |"]
        for row in rows:
            counts, reasons = row["sample_counts"], row["blocked_by_reason"]
            diagnostic = row["closed_only_diagnostic"]
            turnover = ", ".join(f"{key} {value}" for key, value in row["turnover_by_quote"].items()) or "0"
            lines.append(
                f"| {row['variant']} | {row['status']} | {counts['candidate_entries']} | {counts['accepted_entries']} | "
                f"{counts['blocked_entries']} | {row['net_r']} | {row['profit_factor'] or row['profit_factor_note']} | "
                f"{row['max_drawdown_r']} | {diagnostic['net_r']} / {diagnostic['profit_factor'] or diagnostic['profit_factor_note']} / "
                f"{diagnostic['max_drawdown_r']} | {turnover} | {reasons['same_strategy_direction_position_cap']} / "
                f"{reasons['strategy_open_risk_cap']} |"
            )
        reasons = sorted({reason for row in rows for reason in row["insufficiency_reasons"]})
        if reasons:
            lines += ["", "Insufficiency: " + ", ".join(f"`{reason}`" for reason in reasons) + "."]
    lines += [
        "",
        "## Boundary",
        "",
        "Blocked entries earn zero and never cause a future trade to be substituted. Recorded costs, funding, fills and exits are unchanged for retained positions. The whole current cohort is not a completed performance sample while open positions remain, so its scalar performance fields are intentionally null.",
        "",
    ]
    return "\n".join(lines)


def write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError:
        fail("output.exists")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--prereg", type=Path, default=Path(PREREG))
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = seal(build(args.snapshot, args.manifest, args.prereg))
        write_once(args.out_dir / "result.json", canonical(result))
        write_once(args.out_dir / "result.md", render_markdown(result).encode("utf-8"))
        print(result["artifact_sha256"])
        return 0
    except (StudyError, OSError) as error:
        print(error.code if type(error) is StudyError else "io.failure", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
