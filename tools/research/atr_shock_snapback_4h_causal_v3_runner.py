"""Executable, merge-gated causal runner for Candidate 13 ASS-4H v3.

This module deliberately separates authority validation from market-row reads.
The public run path will not construct an archive adapter until the frozen
contract commit is contained by the protected branch and every repository
authority has passed its byte/content binding.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from tools.research import strategy_causal_pack_01_runner as P


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "contracts" / "atr_shock_snapback_4h_causal_v3"
CONTRACT_PATH = CONTRACT_DIR / "atr_shock_snapback_4h_causal_v3_contract.v1.json"
FAMILY_PATH = CONTRACT_DIR / "atr_shock_snapback_4h_family.v1.json"
UNIVERSE_PATH = ROOT / "research_evidence" / "universe" / "pit-top40-qv30d-v4.json"
MANIFEST_PATH = ROOT / "research_evidence" / "manifests" / "binance-um-archive-v1.json.gz"
CLASSES_PATH = ROOT / "research_evidence" / "universe" / "instrument-classes-v1.json"
FILTER_PATH = ROOT / "contracts" / "strategy_causal_pack_01" / "exchange_filter_authority.v2.json"
DEFAULT_ARCHIVE_STORE = Path(r"C:\Dev\ZackBot2_data\binance_um")

CONTRACT_SHA256 = "e71e032dbbe790d5c34c82b622ee19b9bd1ff7430769d19aaed46c7c0478c1fd"
# Provisional: repin to the protected merge commit after #302 is merged.
CONTRACT_COMMIT = "c46a9216f7b8232043e06ff57f78787a5434b307"
SOURCE_FUNCTION_SHA256 = "09f2f0a2bf9f2560a9da911de3b94f5662404a90a13609971a0f32fd3ed59515"
INDICATORS_FUNCTION_SHA256 = "dfcfb920b272b2e291a2395e4706ed7cc4f0a1d847bdcf9058384c781466ec26"
FORMAT = "zackbot.atr-shock-snapback-4h-causal-v3/1"
INCOMPLETE = "INCOMPLETE_CANDIDATE"
BAR_MS = 4 * 3_600_000
HOUR_MS = 3_600_000
SIDES = {
    "atr_shock_snapback_4h_long_causal_v3": "long",
    "atr_shock_snapback_4h_short_causal_v3": "short",
}


class RunnerError(RuntimeError):
    pass


class IncompleteCandidate(RunnerError):
    pass


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _strict_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"),
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise RunnerError(f"authority unavailable or malformed: {path}: {exc}") from exc


def load_contract() -> tuple[dict, str]:
    digest = sha256(CONTRACT_PATH)
    if digest != CONTRACT_SHA256:
        raise RunnerError(f"contract digest changed: {digest}")
    contract = _strict_json(CONTRACT_PATH)
    if (contract.get("contract"), contract.get("version"), contract.get("status"),
            contract.get("signed_by")) != (
            "ATR-SHOCK-SNAPBACK-4H-CAUSAL-V3-SPARSE-AVAILABILITY", 3, "FROZEN", "codex"):
        raise RunnerError("contract is not the frozen Codex-signed v3 identity")
    cells = {row.get("candidate_id"): row.get("side") for row in contract.get("candidate_cells", ())}
    if cells != SIDES:
        raise RunnerError("candidate side identities changed")
    return contract, digest


def _content_digest(value: dict) -> str:
    return hashlib.sha256(canonical({key: item for key, item in value.items() if key != "digest"})).hexdigest()


def validate_repository_authorities(contract: dict) -> dict:
    """Validate all non-market authority before an adapter may be constructed."""
    from tools.research.strategy_causal_pack_01_archive import load_authorities, authority_digests
    authority = contract["pit_and_archive_authority"]
    expected = {
        UNIVERSE_PATH: authority["universe_byte_sha256"],
        MANIFEST_PATH: authority["manifest_gzip_byte_sha256"],
        CLASSES_PATH: authority["classes_byte_sha256"],
        FILTER_PATH: contract["exchange_filter_authority"]["byte_sha256"],
        FAMILY_PATH: contract["identity_and_multiplicity"]["family_ledger_byte_sha256"],
    }
    for path, wanted in expected.items():
        actual = sha256(path)
        if actual != wanted:
            raise RunnerError(f"authority byte digest mismatch: {path}: {actual}")
    manifest, pit = load_authorities(MANIFEST_PATH, UNIVERSE_PATH)
    digests = authority_digests(MANIFEST_PATH, UNIVERSE_PATH, manifest, pit)
    if (manifest.get("digest") != authority["manifest_content_digest"]
            or pit.get("digest") != authority["universe_content_digest"]):
        raise RunnerError("manifest/PIT canonical content digest changed")
    classes = _strict_json(CLASSES_PATH)
    if classes.get("digest") != authority["classes_content_digest"] or _content_digest(classes) != classes.get("digest"):
        raise RunnerError("instrument-classes content digest changed")
    family = _strict_json(FAMILY_PATH)
    family_expected = contract["identity_and_multiplicity"]
    if (_content_digest(family) != family.get("digest")
            or family.get("digest") != family_expected["family_ledger_content_digest"]
            or [row.get("trial_id") for row in family.get("trials", ())]
            != family_expected["family_members_ordered"]):
        raise RunnerError("append-only family ledger identity changed")
    filters = validate_filter_authorities(contract)
    return {**digests.as_dict(), "classes_digest": classes["digest"],
            "family_ledger_digest": family["digest"], "family_ledger": family,
            "manifest": manifest, "pit": pit, "_filter_authorities": filters,
            "fold_filter_snapshots": {name: {"captured_at": row.captured_at,
                                               "sha256": row.snapshot_sha256}
                                      for name, row in filters.items()}}


def git_head(repo: Path = ROOT) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def git_dirty(repo: Path = ROOT) -> bool:
    return bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True).strip())


def contract_is_merged(repo: Path = ROOT, merge_ref: str = "origin/master") -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", CONTRACT_COMMIT, merge_ref], cwd=repo,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0


@dataclass(frozen=True)
class Preflight:
    contract: dict
    contract_sha256: str
    git_head: str
    candidate_id: str
    side: str
    identity: str


def preflight(candidate_id: str, output: os.PathLike[str] | str, *, identity_dir=None,
              head_fn: Callable[[], str] = git_head,
              dirty_fn: Callable[[], bool] = git_dirty) -> Preflight:
    contract, digest = load_contract()
    if candidate_id not in SIDES:
        raise RunnerError("unknown Candidate 13 side identity")
    head = head_fn()
    if not head or dirty_fn():
        raise RunnerError("exact clean evaluator head is required")
    out = Path(output)
    if out.exists() or Path(str(out) + ".tmp").exists():
        raise RunnerError("output exists or stale staging exists; overwrite refused")
    identity = hashlib.sha256(canonical({"contract_sha256": digest, "git_head": head,
                                         "candidate_id": candidate_id})).hexdigest()
    root = Path(identity_dir) if identity_dir is not None else out.parent / ".ass_causal_v3_ids"
    if (root / f"{identity}.claim.json").exists():
        raise RunnerError("this exact contract/head/side evaluation is already consumed")
    return Preflight(contract, digest, head, candidate_id, SIDES[candidate_id], identity)


def _exclusive_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(value, fh, sort_keys=True, indent=2, allow_nan=False)
        fh.write("\n"); fh.flush(); os.fsync(fh.fileno())


def _reserve(pf: Preflight, identity_dir: Path, output: Path) -> Path:
    claim = identity_dir / f"{pf.identity}.claim.json"
    try:
        _exclusive_json(claim, {"state": "RESERVED", "identity": pf.identity,
                                "candidate_id": pf.candidate_id, "git_head": pf.git_head,
                                "contract_sha256": pf.contract_sha256, "output": str(output.resolve())})
    except FileExistsError as exc:
        raise RunnerError("this exact contract/head/side evaluation is already consumed") from exc
    return claim


def _finish_claim(claim: Path, value: dict) -> None:
    tmp = claim.with_name(f"{claim.name}.{os.getpid()}.tmp")
    _exclusive_json(tmp, value)
    os.replace(tmp, claim)


@dataclass(frozen=True)
class SignalTape:
    times_ms: tuple[int, ...]
    membership: tuple[tuple[str, ...], ...]
    entry: dict[str, tuple[bool, ...]]
    exit: dict[str, tuple[bool, ...]]
    atr: dict[str, tuple[float, ...]]
    regime: tuple[str | None, ...]
    audit: dict


def validate_source_binding(contract: dict) -> dict:
    import inspect
    import strategies
    spec = strategies.STRATEGIES.get("atr_shock_snapback_4h") or {}
    frozen = contract["frozen_source"]
    if (spec.get("fn") is not strategies.sig_atr_shock_snapback_4h
            or spec.get("tf") != "4h" or not spec.get("research_only")
            or spec.get("params") != frozen["signal_parameters"]
            or spec.get("mgmt") != frozen["management"]
            or not spec.get("reentry_lock")):
        raise RunnerError("strategies.py ASS-4H source no longer matches the frozen contract")
    if hashlib.sha256(inspect.getsource(strategies.sig_atr_shock_snapback_4h).encode()).hexdigest() != SOURCE_FUNCTION_SHA256:
        raise RunnerError("frozen ASS-4H signal function bytes changed")
    if hashlib.sha256(inspect.getsource(strategies.indicators).encode()).hexdigest() != INDICATORS_FUNCTION_SHA256:
        raise RunnerError("frozen indicator implementation bytes changed")
    return {"strategy_key": "atr_shock_snapback_4h", "function": "sig_atr_shock_snapback_4h",
            "source_head": frozen["source_head"]}


def build_signal_tape(contract: dict, candidate_id: str,
                      bars_by_symbol: dict[str, Sequence[P.Bar]],
                      core20_by_index: Sequence[Sequence[str]], *,
                      canonical_times_ms: Sequence[int]) -> SignalTape:
    """Build indicators per contiguous observed segment; later rows never veto earlier decisions."""
    import numpy as np
    import pandas as pd
    import strategies

    validate_source_binding(contract)
    side = SIDES[candidate_id]
    times = tuple(int(value) for value in canonical_times_ms)
    if not times or any(b - a != BAR_MS for a, b in zip(times, times[1:])):
        raise IncompleteCandidate(f"{INCOMPLETE}: invalid canonical 4h grid")
    if len(core20_by_index) != len(times):
        raise IncompleteCandidate(f"{INCOMPLETE}: PIT membership does not cover the canonical grid")
    membership = tuple(tuple(cell) for cell in core20_by_index)
    known = set(bars_by_symbol)
    if any(len(cell) != 20 or len(set(cell)) != 20 or not set(cell) <= known for cell in membership):
        raise IncompleteCandidate(f"{INCOMPLETE}: invalid filter-eligible PIT CORE-20 cell")
    grid = {time_ms: index for index, time_ms in enumerate(times)}
    entry = {symbol: np.zeros(len(times), dtype=bool) for symbol in known}
    exit_ = {symbol: np.zeros(len(times), dtype=bool) for symbol in known}
    atr = {symbol: np.full(len(times), np.nan) for symbol in known}
    availability = {symbol: ["LOCAL_GAP"] * len(times) for symbol in known}
    segments_by_symbol: dict[str, list[tuple[list[int], object]]] = {}
    local_gaps = {symbol: 0 for symbol in known}
    decisions_available = {symbol: 0 for symbol in known}
    for symbol, rows in bars_by_symbol.items():
        ordered = tuple(rows)
        if any(row.time_ms not in grid for row in ordered) or any(
                right.time_ms <= left.time_ms for left, right in zip(ordered, ordered[1:])):
            raise IncompleteCandidate(f"{INCOMPLETE}: {symbol} has invalid sparse times")
        for row in ordered:
            row.validate()
        chunks: list[list[P.Bar]] = []
        current: list[P.Bar] = []
        for row in ordered:
            if current and row.time_ms != current[-1].time_ms + BAR_MS:
                chunks.append(current); current = []
            current.append(row)
        if current:
            chunks.append(current)
        local_gaps[symbol] = len(times) - len(ordered)
        segments_by_symbol[symbol] = []
        for chunk in chunks:
            indices = [grid[row.time_ms] for row in chunk]
            frame = strategies.indicators(pd.DataFrame({
                "t": pd.to_datetime([row.time_ms for row in chunk], unit="ms", utc=True),
                "o": [row.open for row in chunk], "h": [row.high for row in chunk],
                "l": [row.low for row in chunk], "c": [row.close for row in chunk],
                "v": [row.volume for row in chunk],
            }))
            raw = strategies.signals("atr_shock_snapback_4h", frame, {
                "btc_down": pd.Series(False, index=frame.index)},
                contract["frozen_source"]["signal_parameters"], mask_sides=False)
            ekey, xkey = ("le", "lx") if side == "long" else ("se", "sx")
            for local, index in enumerate(indices):
                evalues, xvalues = raw[ekey], raw[xkey]
                entry[symbol][index] = bool(evalues.iloc[local] if hasattr(evalues, "iloc") else evalues[local])
                exit_[symbol][index] = bool(xvalues.iloc[local] if hasattr(xvalues, "iloc") else xvalues[local])
                atr[symbol][index] = float(frame["atr"].iloc[local])
                finite = all(math.isfinite(float(frame[name].iloc[local]))
                             for name in ("e20", "e50", "e200", "atr"))
                causal_ready = local >= 199 and finite
                availability[symbol][index] = ("AVAILABLE" if causal_ready
                                                else "INSUFFICIENT_LOCAL_HISTORY")
                decisions_available[symbol] += int(causal_ready)
            segments_by_symbol[symbol].append((indices, frame))
    regimes: list[str | None] = [None] * len(times)
    for indices, frame in segments_by_symbol.get("BTCUSDT", []):
        for local, index in enumerate(indices):
            e200, close = float(frame["e200"].iloc[local]), float(frame["c"].iloc[local])
            if math.isfinite(e200) and math.isfinite(close):
                regimes[index] = "above_or_on" if close >= e200 else "below"
    decision_records = []
    for index, (time_ms, cell) in enumerate(zip(times, membership)):
        status = {}
        signal_symbols, exit_symbols = [], []
        for symbol in cell:
            local = availability[symbol][index]
            if local == "AVAILABLE":
                local = "SIGNAL" if entry[symbol][index] else "NON_SIGNAL"
                if entry[symbol][index]:
                    signal_symbols.append(symbol)
                if exit_[symbol][index]:
                    exit_symbols.append(symbol)
            status[symbol] = local
        decision_records.append({
            "decision_index": index, "decision_time_ms": time_ms,
            "pit_members": list(cell), "pit_member_count": len(cell),
            "symbol_status": status,
            "eligible_denominator": sum(value in ("SIGNAL", "NON_SIGNAL") for value in status.values()),
            "signal_count": len(signal_symbols), "signal_symbols": signal_symbols,
            "exit_signal_symbols": exit_symbols, "entry_attempt_symbols": [],
            "admitted_symbols": [], "exposure_failures": [],
        })
    return SignalTape(times, membership,
                      {s: tuple(bool(x) for x in v) for s, v in entry.items()},
                      {s: tuple(bool(x) for x in v) for s, v in exit_.items()},
                      {s: tuple(float(x) for x in v) for s, v in atr.items()}, tuple(regimes),
                      {"identity": contract["sparse_availability"]["identity"],
                       "decision_count": len(times), "pit_member_counts": [len(x) for x in membership],
                       "flat_local_missing_bars": local_gaps,
                       "locally_available_indicator_decisions": decisions_available,
                       "decisions": decision_records})


def _make_fill(index: int, time_ms: int, action: str, qty: float, reference: float,
               rule: P.ExchangeRule, reason: str, multiplier: float) -> P.Fill:
    return P.make_fill(index, time_ms, action, qty, reference, rule, reason,
                       fee_bps=5.0 * multiplier, slippage_bps=2.0 * multiplier)


def _open_position(*, side: str, index: int, time_ms: int, reference: float, atr: float,
                   risk_cash: float, leverage_cap: float, management: dict,
                   rule: P.ExchangeRule, multiplier: float) -> P.Position | None:
    if side not in ("long", "short") or not P._positive(atr) or not P._positive(risk_cash):
        raise IncompleteCandidate(f"{INCOMPLETE}: invalid side/risk/causal ATR")
    rule.validate()
    action = "buy" if side == "long" else "sell"
    entry_exec = P.executable_price(reference, action, rule.tick, 2.0 * multiplier)
    r_price = float(management["stop_atr"]) * atr
    qty = P._floor_step(min(risk_cash / r_price, leverage_cap / entry_exec), rule.step)
    if qty < rule.min_qty or qty * entry_exec < rule.min_notional:
        return None
    if rule.max_qty is not None and qty > rule.max_qty + 1e-12:
        raise IncompleteCandidate(f"{INCOMPLETE}: entry exceeds MARKET_LOT_SIZE maxQty")
    sign = 1 if side == "long" else -1
    fill = _make_fill(index, time_ms, action, qty, reference, rule, "entry", multiplier)
    return P.Position(side, index, time_ms, reference, fill.executable_price, qty, qty, r_price,
                      fill.executable_price - sign * r_price,
                      fill.executable_price + sign * management["tp_r"] * r_price,
                      fill.executable_price + sign * management["tp1_r"] * r_price,
                      fill.executable_price + sign * management["be_r"] * r_price,
                      None, fill.executable_price, management["tp1_frac"], fills=[fill])


def _stress_fills(pos: P.Position, start: int, fills: Sequence[P.Fill], rule: P.ExchangeRule,
                  multiplier: float) -> tuple[P.Fill, ...]:
    stressed = tuple(_make_fill(row.index, row.time_ms, row.action, row.qty, row.reference_price,
                                rule, row.reason, multiplier) for row in fills)
    pos.fills[start:start + len(stressed)] = stressed
    return stressed


@dataclass(frozen=True)
class FoldResult:
    name: str
    trades: tuple[dict, ...]
    final_equity: float
    audit: dict
    marked_drawdown_cash: float
    marked_drawdown_fraction: float


def evaluate_fold(contract: dict, pf: Preflight, fold_name: str,
                  bars_by_symbol: dict[str, Sequence[P.Bar]],
                  membership: Sequence[Sequence[str]], rules: dict[str, P.ExchangeRule],
                  funding: dict[str, P.FundingLedger], marks: dict[str, dict[int, float]], *,
                  canonical_times_ms: Sequence[int], cost_multiplier: float) -> FoldResult:
    """One deterministic event/book pass. Sparse flat gaps are local; exposure gaps fail the side."""
    if cost_multiplier not in (1.0, 1.5, 2.0):
        raise RunnerError("cost multiplier is outside the frozen stress rows")
    tape = build_signal_tape(contract, pf.candidate_id, bars_by_symbol, membership,
                             canonical_times_ms=canonical_times_ms)
    n = len(tape.times_ms)
    on_grid = {symbol: tuple({row.time_ms: row for row in rows}.get(t) for t in tape.times_ms)
               for symbol, rows in bars_by_symbol.items()}
    for symbol in bars_by_symbol:
        if symbol not in rules or symbol not in funding or symbol not in marks:
            raise IncompleteCandidate(f"{INCOMPLETE}: missing filter/funding/mark authority for {symbol}")
        rules[symbol].validate()
        if not funding[symbol].checksum_verified:
            raise IncompleteCandidate(f"{INCOMPLETE}: funding checksum failed for {symbol}")
    book = P.FrozenCandidateBook(tape.membership[0])
    positions: dict[str, P.Position] = {}
    risks: dict[str, float] = {}
    funding_cash: dict[str, float] = {}
    funding_flows: dict[str, list[tuple[int, float]]] = {}
    regimes: dict[str, str] = {}
    lock_through_decision: dict[str, int] = {}
    cursors = {symbol: 0 for symbol in bars_by_symbol}
    trades: list[dict] = []
    audit = dict(tape.audit, admitted_signals=0, exposure_critical_failures=[])
    audit["decisions"] = [dict(row, symbol_status=dict(row["symbol_status"]),
                                signal_symbols=list(row["signal_symbols"]),
                                exit_signal_symbols=list(row["exit_signal_symbols"]),
                                entry_attempt_symbols=list(row["entry_attempt_symbols"]),
                                admitted_symbols=list(row["admitted_symbols"]),
                                exposure_failures=list(row["exposure_failures"]))
                          for row in tape.audit.get("decisions", ())]
    current_index = -1
    marked_peak = book.marked_equity(); max_marked_dd = max_marked_dd_fraction = 0.0

    def snapshot_drawdown() -> None:
        nonlocal marked_peak, max_marked_dd, max_marked_dd_fraction
        equity = book.marked_equity(); marked_peak = max(marked_peak, equity)
        dd = marked_peak - equity; max_marked_dd = max(max_marked_dd, dd)
        max_marked_dd_fraction = max(max_marked_dd_fraction, dd / marked_peak if marked_peak > 0 else math.inf)

    def audit_failure(symbol: str, kind: str, decision_index: int | None = None) -> None:
        row = {"symbol": symbol, "kind": kind}
        audit["exposure_critical_failures"].append(row)
        at = current_index if decision_index is None else decision_index
        if 0 <= at < len(audit["decisions"]):
            audit["decisions"][at]["exposure_failures"].append(row)

    def settle(symbol: str, event: P.FundingEvent) -> None:
        pos = positions.get(symbol)
        if pos is None or event.time_ms < pos.entry_time_ms:
            return
        if not P._positive(event.mark) or not math.isfinite(event.rate):
            raise IncompleteCandidate(f"{INCOMPLETE}: funding mark/rate gap during exposure")
        cash = -pos.sign * pos.qty * event.mark * event.rate
        if cash < 0:
            cash *= cost_multiplier
        book.book_signed_funding_cash(symbol, cash); funding_cash[symbol] += cash
        funding_flows[symbol].append((event.time_ms, cash)); snapshot_drawdown()

    def settle_until(time_ms: int, include_equal: bool) -> None:
        for symbol, ledger in funding.items():
            cursor = cursors[symbol]
            while cursor < len(ledger.events):
                event = ledger.events[cursor]
                if event.time_ms > time_ms or (event.time_ms == time_ms and not include_equal):
                    break
                settle(symbol, event); cursor += 1
            cursors[symbol] = cursor
            pos = positions.get(symbol)
            if pos is not None:
                cutoff = time_ms + int(include_equal)
                if (not ledger.coverage_complete and not ledger.coverage_gaps) or any(
                        gap.start_ms < cutoff and gap.end_ms > pos.entry_time_ms for gap in ledger.coverage_gaps):
                    audit_failure(symbol, "funding_gap")
                    raise IncompleteCandidate(f"{INCOMPLETE}: funding coverage gap intersects exposure")

    def record(symbol: str, fills: Sequence[P.Fill], *, lock_decision: int) -> None:
        if not fills:
            return
        pos = positions[symbol]
        for fill in fills:
            pnl = pos.sign * (fill.reference_price - pos.entry_reference) * fill.qty
            cost = fill.fee_cash + fill.slippage_cash
            if fill.qty < book.positions[symbol].qty - 1e-9:
                book.book_partial_exit(symbol, fill.qty, pnl, cost, rules[symbol])
            else:
                book.book_final_exit(symbol, pnl, cost)
            snapshot_drawdown()
        if pos.qty <= 0:
            accounting = P.account_position(pos, funding_cash[symbol])
            initial_risk = risks[symbol]
            cashflows = [{"time_ms": pos.entry_time_ms,
                          "cash": -(pos.fills[0].fee_cash + pos.fills[0].slippage_cash),
                          "kind": "entry_cost"}]
            cashflows.extend({"time_ms": time_ms, "cash": cash, "kind": "funding"}
                             for time_ms, cash in funding_flows[symbol])
            cashflows.extend({
                "time_ms": fill.time_ms,
                "cash": pos.sign * (fill.reference_price - pos.entry_reference) * fill.qty
                        - fill.fee_cash - fill.slippage_cash,
                "kind": fill.reason,
            } for fill in pos.fills[1:])
            trades.append({"fold": fold_name, "symbol": symbol, "side": pf.side,
                           "entry_index": pos.entry_index, "exit_index": pos.fills[-1].index,
                           "entry_time_ms": pos.entry_time_ms, "exit_time_ms": pos.fills[-1].time_ms,
                           "episode_id": f"{fold_name}:{symbol}:{pos.entry_time_ms}:{pf.side}",
                           "initial_risk_cash": initial_risk, "r": accounting["net_pnl"] / initial_risk,
                           "regime": regimes[symbol], "accounting": accounting,
                           "cashflows": cashflows,
                           "fills": [row.__dict__ for row in pos.fills]})
            lock_through_decision[symbol] = lock_decision
            del positions[symbol], risks[symbol], funding_cash[symbol], funding_flows[symbol], regimes[symbol]

    for index, time_ms in enumerate(tape.times_ms):
        current_index = index
        settle_until(time_ms, False)
        current_marks = {}
        for symbol in positions:
            mark = marks[symbol].get(time_ms)
            if not P._positive(mark):
                audit_failure(symbol, "mark_gap")
                raise IncompleteCandidate(f"{INCOMPLETE}: missing causal mark during exposure")
            current_marks[symbol] = float(mark)
        book.advance_bar(time_ms, current_marks); snapshot_drawdown()

        decision = index - 1
        for symbol in tuple(positions):
            pos = positions[symbol]
            signal_due = decision >= 0 and tape.exit[symbol][decision]
            time_due = index == pos.entry_index + 12
            if signal_due or time_due:
                bar = on_grid[symbol][index]
                if bar is None:
                    raise IncompleteCandidate(f"{INCOMPLETE}: missing due open exit")
                action = "sell" if pf.side == "long" else "buy"
                fill = _make_fill(index, time_ms, action, pos.qty, bar.open, rules[symbol],
                                  "signal" if signal_due else "time", cost_multiplier)
                pos.qty = 0.0; pos.fills.append(fill); record(symbol, (fill,), lock_decision=decision)

        if index >= 200 and index + 12 < n:
            requested = [symbol for symbol in tape.membership[decision]
                         if tape.entry[symbol][decision] and symbol not in positions
                         and decision > lock_through_decision.get(symbol, -1)]
            if 0 <= decision < len(audit["decisions"]):
                audit["decisions"][decision]["entry_attempt_symbols"].extend(requested)
            book.core20_order = tape.membership[decision]
            book._priority = {symbol: rank for rank, symbol in enumerate(book.core20_order)}

            def opener(terms: P.EntryTerms) -> P.BookAdmission | None:
                bar, atr = on_grid[terms.symbol][index], tape.atr[terms.symbol][decision]
                if bar is None:
                    audit_failure(terms.symbol, "missing_next_open", decision)
                    raise IncompleteCandidate(f"{INCOMPLETE}: admitted signal lacks next open")
                pos = _open_position(side=pf.side, index=index, time_ms=time_ms, reference=bar.open,
                                     atr=atr, risk_cash=terms.risk_cash,
                                     leverage_cap=terms.leverage_notional_cap,
                                     management=contract["frozen_source"]["management"],
                                     rule=rules[terms.symbol], multiplier=cost_multiplier)
                if pos is None:
                    return None
                regime = tape.regime[decision]
                if regime is None:
                    raise IncompleteCandidate(f"{INCOMPLETE}: admitted signal lacks causal BTC regime")
                positions[terms.symbol] = pos
                risks[terms.symbol] = pos.initial_qty * pos.r_price
                funding_cash[terms.symbol] = 0.0; funding_flows[terms.symbol] = []
                regimes[terms.symbol] = regime
                audit["admitted_signals"] += 1
                if 0 <= decision < len(audit["decisions"]):
                    audit["decisions"][decision]["admitted_symbols"].append(terms.symbol)
                first = pos.fills[0]
                return P.BookAdmission(P.BookLot(terms.symbol, pf.side, pos.qty,
                                                 pos.entry_reference, pos.entry_reference),
                                       first.fee_cash + first.slippage_cash)

            book.admit_simultaneous(time_ms, requested, opener); snapshot_drawdown()

        settle_until(time_ms, True)
        for symbol in tuple(positions):
            bar = on_grid[symbol][index]
            if bar is None:
                audit_failure(symbol, "bar_gap")
                raise IncompleteCandidate(f"{INCOMPLETE}: open position has no canonical 4h bar")
            pos = positions[symbol]; start = len(pos.fills)
            raw = P.walk_bar(pos, bar, index, contract["frozen_source"]["management"], rules[symbol])
            fills = _stress_fills(pos, start, raw, rules[symbol], cost_multiplier)
            record(symbol, fills, lock_decision=index)
    if positions:
        for symbol in positions:
            audit_failure(symbol, "terminal_open_exposure", len(tape.times_ms) - 1)
        raise IncompleteCandidate(f"{INCOMPLETE}: fold ended with admitted exposure")
    return FoldResult(fold_name, tuple(trades), book.settled_cash_equity, audit,
                      max_marked_dd, max_marked_dd_fraction)


def _fold_proxy(contract: dict) -> dict:
    a = contract["pit_and_archive_authority"]
    return {"folds": contract["folds"], "data": {
        "exchange_filter_authority_sha256": contract["exchange_filter_authority"]["byte_sha256"],
        "pit_universe_path": str(UNIVERSE_PATH.relative_to(ROOT)).replace("\\", "/"),
        "pit_universe_sha256": a["universe_byte_sha256"],
        "pit_universe_digest": a["universe_content_digest"],
    }}


def validate_filter_authorities(contract: dict) -> dict:
    """Bind every pre-fold exchange snapshot and weekly PIT cell pre-market."""
    from tools.research.strategy_causal_pack_01_archive import load_fold_filter_authorities
    filters = load_fold_filter_authorities(
        FILTER_PATH, contract["exchange_filter_authority"]["byte_sha256"], ROOT,
        _fold_proxy(contract))
    expected_bindings = contract["exchange_filter_authority"]["fold_bindings"]
    expected_cells = contract["exchange_filter_authority"]["weekly_membership_sha256"]
    if {name: row.captured_at for name, row in filters.items()} != expected_bindings:
        raise RunnerError("fold filter bindings changed")
    authority = _strict_json(FILTER_PATH)
    if authority.get("weekly_membership_sha256") != expected_cells:
        raise RunnerError("weekly PIT/filter membership digests changed")
    return filters


def load_raw_inputs(adapter, contract: dict, filter_authorities: dict, fold: dict) -> dict:
    """The only market-row reader; callers must run both guards before entering."""
    from bisect import bisect_right
    start = int(datetime.fromisoformat(fold["start"].replace("Z", "+00:00")).timestamp() * 1000)
    end = int(datetime.fromisoformat(fold["end"].replace("Z", "+00:00")).timestamp() * 1000)
    authority = filter_authorities[fold["name"]]
    symbols = adapter.core20_union(start, end, authority.eligible_symbols)
    rules = {symbol: P.ExchangeRule(**authority.rules[symbol]) for symbol in symbols}
    loads = {symbol: adapter.load_klines_sparse(symbol, "4h", start, end,
                                                eligible_symbols=authority.eligible_symbols)
             for symbol in symbols}
    bars = {symbol: tuple(P.Bar(row.open_ms, row.open, row.high, row.low, row.close, volume=row.volume)
                          for row in load.rows) for symbol, load in loads.items()}
    times = tuple(range(start, end, BAR_MS))
    membership = tuple(adapter.core20_at(t, authority.eligible_symbols) for t in times)
    ledgers, marks = {}, {}
    for symbol in symbols:
        mark_load = adapter.load_mark_closes_sparse(symbol, start, end,
                                                    eligible_symbols=authority.eligible_symbols)
        mt, mp = [row[0] for row in mark_load.rows], [row[1] for row in mark_load.rows]
        marks[symbol] = {}
        for t in times:
            at = bisect_right(mt, t) - 1
            if at >= 0 and t - mt[at] <= HOUR_MS:
                marks[symbol][t] = mp[at]
        funding_load = adapter.load_funding_sparse(symbol, start, end,
                                                   eligible_symbols=authority.eligible_symbols)
        events = []
        for event in funding_load.rows:
            at = bisect_right(mt, event.time_ms) - 1
            mark = mp[at] if at >= 0 and event.time_ms - mt[at] <= HOUR_MS else float("nan")
            events.append(P.FundingEvent(event.time_ms, event.rate, mark))
        gaps = tuple(P.AuthorityGap(row.start_ms, row.end_ms, row.reason) for row in funding_load.gaps)
        ledgers[symbol] = P.FundingLedger(tuple(events), True, not gaps, gaps)
    return {"bars": bars, "membership": membership, "rules": rules, "funding": ledgers,
            "marks": marks, "times": times}


def _months(contract: dict) -> tuple[str, ...]:
    out = []
    for fold in contract["folds"]:
        current = datetime.fromisoformat(fold["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(fold["end"].replace("Z", "+00:00"))
        current = datetime(current.year, current.month, 1, tzinfo=timezone.utc)
        while current < end:
            name = current.strftime("%Y-%m")
            if name not in out:
                out.append(name)
            current = (datetime(current.year + 1, 1, 1, tzinfo=timezone.utc)
                       if current.month == 12 else
                       datetime(current.year, current.month + 1, 1, tzinfo=timezone.utc))
    return tuple(out)


def _selection_statistics(trades: Sequence[dict]) -> tuple[float, list[float | None]]:
    """Frozen day-cluster sign flip and bootstrap, including ties."""
    import numpy as np
    by_day: dict[str, list[float]] = {}
    for trade in sorted(trades, key=lambda row: (row["exit_time_ms"], row["episode_id"])):
        day = datetime.fromtimestamp(trade["exit_time_ms"] / 1000, timezone.utc).date().isoformat()
        by_day.setdefault(day, []).append(float(trade["r"]))
    if not by_day:
        return 1.0, [None, None]
    days = np.asarray([math.fsum(by_day[day]) for day in sorted(by_day)], dtype=float)
    observed = math.fsum(float(value) for value in days)
    rng = np.random.Generator(np.random.PCG64(20261106150747))
    at_least = 0
    remaining = 100_000
    while remaining:
        size = min(2_000, remaining)
        signs = rng.integers(0, 2, size=(size, len(days)), dtype=np.int8) * 2 - 1
        at_least += sum(
            math.fsum(float(sign) * float(value) for sign, value in zip(row, days)) >= observed
            for row in signs
        )
        remaining -= size
    raw = (1 + at_least) / 100_001
    clusters = [tuple(by_day[day]) for day in sorted(by_day)]
    rng = np.random.Generator(np.random.PCG64(20261106150748))
    means = []
    for _ in range(10_000):
        chosen = rng.integers(0, len(clusters), size=len(clusters))
        sample = [value for index in chosen for value in clusters[int(index)]]
        means.append(math.fsum(sample) / len(sample))
    return raw, [float(np.quantile(means, 0.025, method="linear")),
                 float(np.quantile(means, 0.975, method="linear"))]


def _cashflow_drawdown(fold: FoldResult) -> float:
    grouped: dict[int, list[float]] = {}
    for trade in fold.trades:
        risk = float(trade["initial_risk_cash"])
        for flow in trade["cashflows"]:
            grouped.setdefault(int(flow["time_ms"]), []).append(float(flow["cash"]) / risk)
    cumulative = peak = maximum = 0.0
    for time_ms in sorted(grouped):
        cumulative += math.fsum(grouped[time_ms])
        peak = max(peak, cumulative)
        maximum = max(maximum, peak - cumulative)
    return maximum


def _holm_adjusted(inputs: dict[str, float]) -> dict[str, float]:
    contract, _ = load_contract()
    if set(inputs) != set(contract["identity_and_multiplicity"]["family_members_ordered"]):
        raise RunnerError("Holm reconciliation requires all four frozen family members")
    if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1
           for value in inputs.values()):
        raise RunnerError("Holm inputs must be four finite probabilities")
    ordered = sorted(inputs.items(), key=lambda row: (row[1], row[0]))
    adjusted, running, size = {}, 0.0, len(ordered)
    for rank, (trial_id, value) in enumerate(ordered):
        running = max(running, min(1.0, (size - rank) * float(value)))
        adjusted[trial_id] = running
    return adjusted


def _regime_gate(regime_rows: dict[str, dict]) -> bool:
    frozen = ("above_or_on", "below")
    return set(regime_rows) == set(frozen) and all(
        regime_rows[name].get("closes", 0) >= 15
        and float(regime_rows[name].get("mean_r", -math.inf)) >= -0.05
        for name in frozen)


def _pending_family_for_side(contract: dict, candidate_id: str, raw_p: float) -> dict:
    """Side packets never substitute one for an unevaluated causal sibling."""
    family = dict(_strict_json(FAMILY_PATH))
    frozen_source_digest = family.pop("digest")
    ledger = []
    for row in family["trials"]:
        item = dict(row)
        trial_id = item["trial_id"]
        if trial_id == candidate_id:
            item["raw_p_value"] = raw_p
            item["holm_input_p_value"] = raw_p
            item["status"] = "EVALUATED_CAUSAL_V3_PENDING_PAIRED_RECONCILIATION"
        elif trial_id in SIDES:
            item["raw_p_value"] = None
            item.pop("holm_input_p_value", None)
        item["holm_adjusted_p_value"] = None
        ledger.append(item)
    family["trials"] = ledger
    family["frozen_source_digest"] = frozen_source_digest
    family["reconciliation_status"] = "PENDING_BOTH_CAUSAL_SIDE_PACKETS"
    family["evaluation_digest"] = hashlib.sha256(canonical(family)).hexdigest()
    return family


def candidate_report(contract: dict, pf: Preflight, folds: Sequence[FoldResult]) -> tuple[dict, dict]:
    trades = sorted((trade for fold in folds for trade in fold.trades),
                    key=lambda row: (row["fold"], row["exit_time_ms"], row["symbol"],
                                     row["entry_time_ms"], row["episode_id"]))
    values = [float(row["r"]) for row in trades]
    count = len(values); net = math.fsum(values); mean = net / count if count else None
    wins = math.fsum(value for value in values if value > 0)
    losses = abs(math.fsum(value for value in values if value < 0))
    pf_value: float | str = wins / losses if losses else ("Infinity" if wins else 0.0)
    by_symbol: dict[str, list[float]] = {}
    by_regime: dict[str, list[float]] = {}
    months = {name: 0 for name in _months(contract)}
    for trade in trades:
        by_symbol.setdefault(trade["symbol"], []).append(float(trade["r"]))
        by_regime.setdefault(trade["regime"], []).append(float(trade["r"]))
        month = datetime.fromtimestamp(trade["exit_time_ms"] / 1000, timezone.utc).strftime("%Y-%m")
        months[month] += 1
    symbol_net = {symbol: math.fsum(rows) for symbol, rows in sorted(by_symbol.items())}
    total_abs = math.fsum(abs(value) for value in symbol_net.values())
    concentration = max((abs(value) / total_abs for value in symbol_net.values()), default=1.0)
    ordered_months = sorted(months.values())
    monthly_median = (0.0 if not ordered_months else float(ordered_months[len(ordered_months) // 2])
                      if len(ordered_months) % 2 else
                      math.fsum(ordered_months[len(ordered_months) // 2 - 1:len(ordered_months) // 2 + 1]) / 2)
    raw_p, bootstrap = _selection_statistics(trades)
    family = _pending_family_for_side(contract, pf.candidate_id, raw_p)
    gross_r = math.fsum(row["accounting"]["gross_reference_price_pnl"] /
                        row["initial_risk_cash"] for row in trades)
    fee_r = math.fsum(row["accounting"]["fee_cash"] / row["initial_risk_cash"] for row in trades)
    slip_r = math.fsum(row["accounting"]["adverse_slippage_cash"] /
                       row["initial_risk_cash"] for row in trades)
    # Positive signed funding reduces drag; debit funding increases it.
    funding_drag = -math.fsum(row["accounting"]["signed_funding_cash"] /
                              row["initial_risk_cash"] for row in trades)
    total_slots = 20 * sum((int(datetime.fromisoformat(row["end"].replace("Z", "+00:00")).timestamp() * 1000)
                            - int(datetime.fromisoformat(row["start"].replace("Z", "+00:00")).timestamp() * 1000))
                           // BAR_MS for row in contract["folds"])
    exposure = math.fsum(row["exit_index"] - row["entry_index"] + 1 for row in trades) / total_slots
    report = {
        "trade_count": count, "net_r": net, "mean_r": mean, "profit_factor": pf_value,
        "win_rate": sum(value > 0 for value in values) / count if count else 0.0,
        "max_dd_r": max((_cashflow_drawdown(fold) for fold in folds), default=0.0),
        "expectancy_r": mean, "exposure_time": exposure,
        "utc_monthly_close_counts_including_zeros": months, "monthly_trade_median": monthly_median,
        "symbol_concentration": concentration,
        "regime_breakdown": {name: {"closes": len(rows), "mean_r": math.fsum(rows) / len(rows)}
                             for name, rows in sorted(by_regime.items())},
        "coin_breakdown": {name: {"closes": len(by_symbol[name]), "net_r": value}
                           for name, value in symbol_net.items()},
        "fee_drag_r": fee_r, "slippage_drag_r": slip_r, "funding_drag_r": funding_drag,
        "gross_r": gross_r, "raw_p_value": raw_p,
        "holm_adjusted_p_value": {"status": "PENDING_PAIRED_SIDE_FAMILY_ASSEMBLY",
                                  "value": None},
        "bootstrap_ci_95_mean_r": bootstrap,
        "sparse_availability_audit": {fold.name: fold.audit for fold in folds},
        "full_candidate_ledger": family, "incomplete_rows": [],
        "survivorship_disclosure": "weekly checksum-bound PIT CORE-20; no survivor-only reduction or future veto",
        "failure_reason": ["NO_CLOSED_EPISODES"] if not trades else [],
        "marked_equity_drawdown": {fold.name: {"cash": fold.marked_drawdown_cash,
                                                "fraction": fold.marked_drawdown_fraction}
                                    for fold in folds},
    }
    missing = sorted(set(contract["required_report"]) - set(report))
    fold_net = [math.fsum(row["r"] for row in fold.trades) for fold in folds]
    regime_rows = report["regime_breakdown"]
    gates = {
        "aggregate_net_r_gt_0": net > 0,
        "aggregate_mean_r_gt_0": mean is not None and mean > 0,
        "aggregate_pf_gt_1_05": pf_value == "Infinity" or float(pf_value) > 1.05,
        "positive_pre_cost_gross_expectancy": count > 0 and gross_r / count > 0,
        "minimum_closed_trades": count >= 160, "minimum_symbols": len(by_symbol) >= 8,
        "minimum_positive_folds": sum(value > 0 for value in fold_net) >= 2,
        "minimum_monthly_trade_median": monthly_median >= 5,
        "max_dd_r": report["max_dd_r"] <= 15,
        "regime_coverage_and_mean": _regime_gate(regime_rows),
        "symbol_concentration": concentration <= 0.35,
        "causal_parity": True, "independent_review": False,
        "required_reports": not missing, "zero_incomplete_rows": not report["incomplete_rows"],
        "full_ledger_and_multiplicity_disclosure": len(family["trials"]) == 4,
    }
    return report, gates


def _verified_side_packet(path: os.PathLike[str] | str) -> tuple[dict, str]:
    packet = _strict_json(Path(path))
    claimed = packet.pop("packet_sha256", None)
    actual = hashlib.sha256(canonical(packet)).hexdigest()
    if claimed != actual:
        raise RunnerError(f"side packet digest mismatch: {Path(path)}")
    if packet.get("format") != FORMAT or packet.get("candidate_id") not in SIDES:
        raise RunnerError("family assembly requires one frozen Candidate 13 side packet")
    raw = packet.get("report", {}).get("raw_p_value")
    if type(raw) not in (int, float) or not math.isfinite(raw) or not 0 <= raw <= 1:
        raise RunnerError("side packet lacks a finite frozen raw p-value")
    pending = packet.get("report", {}).get("holm_adjusted_p_value")
    if pending != {"status": "PENDING_PAIRED_SIDE_FAMILY_ASSEMBLY", "value": None}:
        raise RunnerError("side packet did not preserve pending paired-family reconciliation")
    packet["packet_sha256"] = claimed
    return packet, claimed


def assemble_family_packets(long_packet: os.PathLike[str] | str,
                            short_packet: os.PathLike[str] | str,
                            output: os.PathLike[str] | str,
                            identity_dir: os.PathLike[str] | str) -> dict:
    """Reconcile both causal sides; only unavailable prior Stage-A p-values become one."""
    contract, contract_digest = load_contract()
    packets = [_verified_side_packet(long_packet), _verified_side_packet(short_packet)]
    by_id = {packet[0]["candidate_id"]: packet for packet in packets}
    if set(by_id) != set(SIDES):
        raise RunnerError("paired family assembly requires exactly long and short causal side packets")
    heads = {packet[0].get("integrity", {}).get("git_head") for packet in packets}
    contracts = {packet[0].get("integrity", {}).get("contract_sha256") for packet in packets}
    if len(heads) != 1 or contracts != {contract_digest} or None in heads:
        raise RunnerError("paired side packets do not share the exact contract and evaluator head")
    out = Path(output)
    if out.exists() or Path(str(out) + ".tmp").exists():
        raise RunnerError("family output exists or stale staging exists; overwrite refused")
    packet_hashes = {candidate_id: item[1] for candidate_id, item in sorted(by_id.items())}
    identity = hashlib.sha256(canonical({"contract_sha256": contract_digest,
                                         "git_head": next(iter(heads)),
                                         "side_packet_sha256": packet_hashes})).hexdigest()
    claim = Path(identity_dir) / f"family-{identity}.claim.json"
    try:
        _exclusive_json(claim, {"state": "RESERVED", "identity": identity,
                                "side_packet_sha256": packet_hashes, "output": str(out.resolve())})
    except FileExistsError as exc:
        raise RunnerError("this exact paired-family reconciliation is already consumed") from exc
    try:
        family_source = dict(_strict_json(FAMILY_PATH))
        frozen_digest = family_source.pop("digest")
        inputs = {}
        for trial_id in contract["identity_and_multiplicity"]["family_members_ordered"]:
            if trial_id in SIDES:
                inputs[trial_id] = float(by_id[trial_id][0]["report"]["raw_p_value"])
            else:
                inputs[trial_id] = 1.0
        adjusted = _holm_adjusted(inputs)
        rows = []
        for source_row in family_source["trials"]:
            row = dict(source_row); trial_id = row["trial_id"]
            row["holm_input_p_value"] = inputs[trial_id]
            row["holm_adjusted_p_value"] = adjusted[trial_id]
            if trial_id in SIDES:
                row["raw_p_value"] = inputs[trial_id]
                row["status"] = "EVALUATED_CAUSAL_V3"
            rows.append(row)
        ledger = {**family_source, "trials": rows, "frozen_source_digest": frozen_digest,
                  "reconciliation_status": "COMPLETE_BOTH_CAUSAL_SIDES"}
        ledger["evaluation_digest"] = hashlib.sha256(canonical(ledger)).hexdigest()
        result = {"format": "zackbot.atr-shock-snapback-4h-causal-v3-family/1",
                  "contract_sha256": contract_digest, "git_head": next(iter(heads)),
                  "family_identity": identity, "side_packet_sha256": packet_hashes,
                  "raw_p_values": inputs, "holm_adjusted_p_values": adjusted,
                  "full_candidate_ledger": ledger}
        result["packet_sha256"] = hashlib.sha256(canonical(result)).hexdigest()
        _exclusive_json(out, result)
    except BaseException as exc:
        _finish_claim(claim, {"state": "FAILED_CONSUMED", "identity": identity,
                              "error_type": type(exc).__name__})
        raise
    _finish_claim(claim, {"state": "COMPLETED", "identity": identity,
                          "packet_sha256": result["packet_sha256"],
                          "output": str(out.resolve())})
    return result


def evaluate_raw(adapter, pf: Preflight, authority: dict) -> dict:
    # These frozen bindings are rechecked here before load_raw_inputs is reachable.
    validate_source_binding(pf.contract)
    filters = authority.get("_filter_authorities")
    if not isinstance(filters, dict) or set(filters) != {row["name"] for row in pf.contract["folds"]}:
        raise RunnerError("pre-market filter authorities were not validated")
    stress: dict[str, list[FoldResult]] = {}
    for multiplier in (1.0, 1.5, 2.0):
        folds = []
        for fold in pf.contract["folds"]:
            raw = load_raw_inputs(adapter, pf.contract, filters, fold)
            folds.append(evaluate_fold(pf.contract, pf, fold["name"], raw["bars"], raw["membership"],
                                       raw["rules"], raw["funding"], raw["marks"],
                                       canonical_times_ms=raw["times"], cost_multiplier=multiplier))
        stress[str(multiplier)] = folds
    base = stress["1.0"]
    stress_net = {key: math.fsum(t["r"] for fold in rows for t in fold.trades)
                  for key, rows in stress.items()}
    report, gates = candidate_report(pf.contract, pf, base)
    gates["positive_after_1_5x_cost_stress"] = stress_net["1.5"] > 0
    return {"format": FORMAT, "candidate_id": pf.candidate_id, "side": pf.side,
            "source_binding": validate_source_binding(pf.contract),
            "folds": [{"name": row.name, "trades": list(row.trades), "final_equity": row.final_equity,
                       "sparse_availability_audit": row.audit,
                       "marked_equity_drawdown_cash": row.marked_drawdown_cash,
                       "marked_equity_drawdown_fraction": row.marked_drawdown_fraction} for row in base],
            "report": report, "stress_net_r": stress_net, "promotion_gates": gates,
            "promotable": False,
            "promotion_status": ("AWAITING_EXTERNAL_INDEPENDENT_REVIEW"
                                 if all(value for key, value in gates.items()
                                        if key != "independent_review") else "FAILED_GATES"),
            "integrity": {key: value for key, value in authority.items()
                          if key not in ("manifest", "pit", "family_ledger", "_filter_authorities")}}


def run(candidate_id: str, output: os.PathLike[str] | str, identity_dir: os.PathLike[str] | str,
        loader: Callable[[dict], object], *, merge_guard: Callable[[], bool] = contract_is_merged,
        head_fn: Callable[[], str] = git_head, dirty_fn: Callable[[], bool] = git_dirty) -> dict:
    """Guard, validate, reserve, then and only then allow a market adapter/read."""
    pf = preflight(candidate_id, output, identity_dir=identity_dir, head_fn=head_fn, dirty_fn=dirty_fn)
    if not merge_guard():
        raise RunnerError("provisional contract commit is not in origin/master; no market row was read")
    # Source, indicators, PIT, family and every fold-filter snapshot are bound
    # before loader() can construct an adapter or expose a market-row method.
    validate_source_binding(pf.contract)
    authority = validate_repository_authorities(pf.contract)
    claim = _reserve(pf, Path(identity_dir), Path(output))
    try:
        packet = evaluate_raw(loader(authority), pf, authority)
        packet["integrity"].update({"contract_sha256": pf.contract_sha256, "git_head": pf.git_head,
                                    "evaluation_identity": pf.identity,
                                    "contract_commit_provisional": CONTRACT_COMMIT})
        packet["packet_sha256"] = hashlib.sha256(canonical(packet)).hexdigest()
        _exclusive_json(Path(output), packet)
    except BaseException as exc:
        _finish_claim(claim, {"state": "FAILED_CONSUMED", "identity": pf.identity,
                              "error_type": type(exc).__name__})
        raise
    _finish_claim(claim, {"state": "COMPLETED", "identity": pf.identity,
                          "packet_sha256": packet["packet_sha256"], "output": str(Path(output).resolve())})
    return packet


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Candidate 13 ASS-4H causal-v3 raw runner")
    ap.add_argument("--candidate", choices=tuple(SIDES))
    ap.add_argument("--long-packet"); ap.add_argument("--short-packet")
    ap.add_argument("--out", required=True); ap.add_argument("--identity-dir", required=True)
    ap.add_argument("--archive-store", default=str(DEFAULT_ARCHIVE_STORE))
    args = ap.parse_args(argv)
    try:
        if args.long_packet or args.short_packet:
            if args.candidate or not (args.long_packet and args.short_packet):
                raise RunnerError("family mode requires both side packets and no --candidate")
            packet = assemble_family_packets(
                args.long_packet, args.short_packet, args.out, args.identity_dir)
            print(f"paired family: packet {packet['packet_sha256'][:12]} -> {args.out}")
            return 0
        if not args.candidate:
            raise RunnerError("side evaluation requires --candidate")
        def loader(authority):
            from tools.research.strategy_causal_pack_01_archive import RawArchiveAdapter
            return RawArchiveAdapter(authority["manifest"], authority["pit"], args.archive_store)
        packet = run(args.candidate, args.out, args.identity_dir, loader)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr); return 2
    print(f"{packet['candidate_id']}: packet {packet['packet_sha256'][:12]} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
