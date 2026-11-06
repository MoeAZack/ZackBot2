"""Causal, fail-closed runner core for ``STRATEGY-CAUSAL-PACK-01``.

The module is deliberately data-source agnostic.  Production callers must provide
checksum-verified bars, exchange rules, funding events and causal marks.  This
keeps integrity validation ahead of the first market-data read and makes the
execution rules testable with hostile in-memory fixtures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "contracts" / "strategy_causal_pack_01" / "strategy_causal_pack_01_contract.v1.json"
CONTRACT_SHA256 = "00e839b523e35fee63d7fdd254752c50e56817ad38aa548d99f7e8d899a2be85"
EXPECTED_HEAD = "ee7554bd5754b20544ab00fca47b6a1e3423d58d"
FORMAT = "zackbot.strategy-causal-pack-01/1"
INCOMPLETE = "INCOMPLETE_CANDIDATE"
CANDIDATE_IDS = (
    "pullback_rsi_short_h1_causal_v2",
    "exp_mom_short_c27_causal_v2",
    "volume_pressure_inventory_short_1h_causal_v2",
)


class RunnerError(RuntimeError):
    """The frozen evaluation cannot proceed."""


class IncompleteCandidate(RunnerError):
    """Required causal data is absent; reduced-sample evaluation is forbidden."""


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_contract(path: Path = CONTRACT_PATH) -> tuple[dict, str]:
    """Validate the authoritative bytes before a caller is allowed to load data."""
    digest = _sha256(path)
    if digest != CONTRACT_SHA256:
        raise RunnerError(f"contract digest changed: {digest}; expected {CONTRACT_SHA256}")
    with path.open(encoding="utf-8") as fh:
        contract = json.load(fh)
    if (contract.get("contract"), contract.get("version"), contract.get("status"), contract.get("signed_by")) != (
        "STRATEGY-CAUSAL-PACK-01", 1, "FROZEN", "codex"
    ):
        raise RunnerError("contract is not the frozen Codex-signed v1 contract")
    ids = tuple(row.get("candidate_id") for row in contract.get("candidates", ()))
    if ids != CANDIDATE_IDS:
        raise RunnerError("frozen candidate identities changed")
    return contract, digest


def candidate_spec(contract: dict, candidate_id: str) -> dict:
    if candidate_id not in CANDIDATE_IDS:
        raise RunnerError(f"candidate must be one of {', '.join(CANDIDATE_IDS)}")
    return next(row for row in contract["candidates"] if row["candidate_id"] == candidate_id)


def git_head(repo: Path = ROOT) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def git_dirty(repo: Path = ROOT) -> bool:
    return bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True).strip())


@dataclass(frozen=True)
class Preflight:
    contract: dict
    contract_sha256: str
    git_head: str
    candidate_id: str
    identity: str


def preflight(
    candidate_id: str,
    output: os.PathLike[str] | str,
    *,
    identity_dir: os.PathLike[str] | str | None = None,
    head_fn: Callable[[], str] = git_head,
    dirty_fn: Callable[[], bool] = git_dirty,
) -> Preflight:
    """Run every integrity refusal before any market loader may be invoked."""
    contract, digest = load_contract()
    candidate_spec(contract, candidate_id)
    head = head_fn()
    if head != EXPECTED_HEAD and not head:
        raise RunnerError("exact git head could not be recorded")
    if dirty_fn():
        raise RunnerError("dirty tree: frozen evaluation refused")
    out = Path(output)
    if out.exists() or Path(str(out) + ".tmp").exists():
        raise RunnerError("output exists or stale staging exists; overwrite refused")
    identity = hashlib.sha256(canonical({
        "contract_sha256": digest, "git_head": head, "candidate_id": candidate_id
    })).hexdigest()
    if identity_dir is not None and (Path(identity_dir) / f"{identity}.claim.json").exists():
        raise RunnerError("this exact contract/head/candidate evaluation already exists")
    return Preflight(contract, digest, head, candidate_id, identity)


def run_with_loader(
    candidate_id: str,
    output: os.PathLike[str] | str,
    loader: Callable[[], object],
    evaluator: Callable[[object, Preflight], dict],
    *,
    identity_dir: os.PathLike[str] | str | None = None,
    head_fn: Callable[[], str] = git_head,
    dirty_fn: Callable[[], bool] = git_dirty,
) -> dict:
    """Reserve identity before ``loader``; every attempt is consumed fail-closed."""
    pf = preflight(candidate_id, output, identity_dir=identity_dir, head_fn=head_fn, dirty_fn=dirty_fn)
    identity_root = Path(identity_dir) if identity_dir is not None else Path(output).parent / ".strategy_causal_pack_01_ids"
    claim = _reserve_identity(pf, identity_root, output)
    try:
        packet = evaluator(loader(), pf)
        packet.setdefault("format", FORMAT)
        packet["integrity"] = {
            "contract_sha256": pf.contract_sha256, "git_head": pf.git_head,
            "candidate_id": pf.candidate_id, "evaluation_identity": pf.identity,
        }
        packet["packet_sha256"] = hashlib.sha256(canonical(packet)).hexdigest()
        _exclusive_json(Path(output), packet)
    except BaseException as exc:
        _finalize_claim(claim, {"state": "FAILED_CONSUMED", "identity": pf.identity,
                                "error_type": type(exc).__name__})
        raise
    _finalize_claim(claim, {"state": "COMPLETED", "identity": pf.identity,
                            "output": str(Path(output).resolve()), "packet_sha256": packet["packet_sha256"]})
    return packet


def _reserve_identity(pf: Preflight, identity_dir: Path, output: os.PathLike[str] | str) -> Path:
    """O_EXCL is the concurrency boundary; even a failed attempt remains consumed."""
    claim = identity_dir / f"{pf.identity}.claim.json"
    try:
        _exclusive_json(claim, {"state": "RESERVED", "identity": pf.identity,
                                "contract_sha256": pf.contract_sha256, "git_head": pf.git_head,
                                "candidate_id": pf.candidate_id, "output": str(Path(output).resolve())})
    except FileExistsError as exc:
        raise RunnerError("this exact contract/head/candidate evaluation already exists") from exc
    return claim


def _finalize_claim(claim: Path, value: dict) -> None:
    """Atomically move a reserved identity to completed or failed-consumed state."""
    tmp = claim.with_name(f"{claim.name}.{os.getpid()}.tmp")
    _exclusive_json(tmp, value)
    os.replace(tmp, claim)


def _exclusive_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, sort_keys=True, indent=2, allow_nan=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def entry_is_eligible(entry_index: int, lookback: int, holding: int, fold_size: int) -> bool:
    """The exact fold-local purge inequality: e >= L and e + H < N."""
    return entry_index >= lookback and entry_index + holding < fold_size


def eligible_entry_range(lookback: int, holding: int, fold_size: int) -> range:
    if min(lookback, holding, fold_size) < 0:
        raise RunnerError("fold indices must be nonnegative")
    return range(lookback, max(lookback, fold_size - holding))


def terminal_fill_is_valid(entry_index: int, exit_index: int, fold_size: int) -> bool:
    return entry_index <= exit_index < fold_size


def next_open(bars: Sequence["Bar"], decision_index: int) -> tuple[int, float, int]:
    """Signal/entry/time decisions always fill at d+1, never at d close or a last mark."""
    index = decision_index + 1
    if index >= len(bars):
        raise IncompleteCandidate(f"{INCOMPLETE}: missing tradable next open after index {decision_index}")
    bar = bars[index]
    if bar is None or not _positive(bar.open):
        raise IncompleteCandidate(f"{INCOMPLETE}: invalid tradable next open at index {index}")
    return index, float(bar.open), int(bar.time_ms)


def time_exit(pos: "Position", bars: Sequence["Bar"], holding_bars: int, rule: "ExchangeRule") -> "Fill":
    """The time decision on e+H-1 fills exactly at the next open x=e+H."""
    if type(holding_bars) is not int or holding_bars <= 0:
        raise RunnerError("holding_bars must be a positive integer")
    fill = discretionary_exit(pos, bars, pos.entry_index + holding_bars - 1, rule, "time")
    if fill.index != pos.entry_index + holding_bars:
        raise RunnerError("time exit violated x=e+H")
    return fill


@dataclass(frozen=True)
class Bar:
    time_ms: int
    open: float
    high: float
    low: float
    close: float
    atr_prior: float | None = None

    def validate(self) -> None:
        vals = (self.open, self.high, self.low, self.close)
        if not all(_positive(x) for x in vals) or self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise IncompleteCandidate(f"{INCOMPLETE}: unresolved or malformed OHLC bar")


@dataclass(frozen=True)
class ExchangeRule:
    tick: float
    step: float
    min_qty: float = 0.0
    min_notional: float = 0.0
    max_qty: float | None = None

    def validate(self) -> None:
        if not (_positive(self.tick) and _positive(self.step)):
            raise IncompleteCandidate(f"{INCOMPLETE}: missing tick or MARKET_LOT_SIZE step")


@dataclass(frozen=True)
class FundingEvent:
    time_ms: int
    rate: float
    mark: float | None


@dataclass(frozen=True)
class FundingLedger:
    events: tuple[FundingEvent, ...]
    checksum_verified: bool
    coverage_complete: bool


@dataclass(frozen=True)
class Fill:
    index: int
    time_ms: int
    action: str
    qty: float
    reference_price: float
    executable_price: float
    reason: str
    fee_cash: float
    slippage_cash: float


@dataclass
class Position:
    side: str
    entry_index: int
    entry_time_ms: int
    entry_reference: float
    entry_exec: float
    qty: float
    initial_qty: float
    r_price: float
    stop: float
    target: float | None
    tp1: float | None
    be_trigger: float | None
    trail_atr: float | None
    best_price: float
    tp1_frac: float = 0.0
    tp1_done: bool = False
    fills: list[Fill] = field(default_factory=list)

    @property
    def sign(self) -> int:
        return 1 if self.side == "long" else -1


def _positive(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) > 0


def _floor_step(qty: float, step: float) -> float:
    return math.floor(qty / step + 1e-12) * step


def _round_adverse(price: float, tick: float, action: str) -> float:
    raw = price / tick
    ticks = math.ceil(raw - 1e-12) if action == "buy" else math.floor(raw + 1e-12)
    return ticks * tick


def executable_price(reference: float, action: str, tick: float, slippage_bps: float = 2.0) -> float:
    if action not in ("buy", "sell") or not _positive(reference) or not _positive(tick):
        raise RunnerError("fill needs a positive price/tick and buy or sell action")
    shifted = reference * (1 + slippage_bps / 10_000) if action == "buy" else reference * (1 - slippage_bps / 10_000)
    return _round_adverse(shifted, tick, action)


def make_fill(index: int, time_ms: int, action: str, qty: float, reference: float, rule: ExchangeRule,
              reason: str, *, fee_bps: float = 5.0, slippage_bps: float = 2.0) -> Fill:
    px = executable_price(reference, action, rule.tick, slippage_bps)
    slip = qty * ((px - reference) if action == "buy" else (reference - px))
    return Fill(index, time_ms, action, qty, reference, px, reason,
                abs(qty * px) * fee_bps / 10_000, max(0.0, slip))


def open_position(*, side: str, entry_index: int, entry_time_ms: int, reference_open: float, atr_entry: float,
                  risk_cash: float, leverage_notional_cap: float, management: dict, rule: ExchangeRule) -> Position | None:
    """Size from adverse executable entry and frozen MARKET_LOT_SIZE; never upsize or clip maxQty."""
    if side not in ("long", "short") or not (_positive(atr_entry) and _positive(risk_cash)):
        raise IncompleteCandidate(f"{INCOMPLETE}: invalid side, risk, or causal entry ATR")
    rule.validate()
    action = "buy" if side == "long" else "sell"
    entry_exec = executable_price(reference_open, action, rule.tick)
    r_price = float(management["stop_atr"]) * atr_entry
    raw = risk_cash / r_price
    capped = min(raw, leverage_notional_cap / entry_exec)
    qty = _floor_step(capped, rule.step)
    if qty < rule.min_qty or qty * entry_exec < rule.min_notional:
        return None
    if rule.max_qty is not None and qty > rule.max_qty + 1e-12:
        raise RunnerError("entry exceeds MARKET_LOT_SIZE maxQty; clipping is forbidden")
    sign = 1 if side == "long" else -1
    entry_fill = make_fill(entry_index, entry_time_ms, action, qty, reference_open, rule, "entry")
    target = entry_exec + sign * float(management["tp_r"]) * r_price if "tp_r" in management else None
    tp1 = entry_exec + sign * float(management["tp1_r"]) * r_price if "tp1_r" in management else None
    be = entry_exec + sign * float(management["be_r"]) * r_price if "be_r" in management else None
    return Position(side, entry_index, entry_time_ms, reference_open, entry_exec, qty, qty, r_price,
                    entry_exec - sign * r_price, target, tp1, be, management.get("trail_atr"), entry_exec,
                    float(management.get("tp1_frac", 0)), fills=[entry_fill])


def path_points(bar: Bar, side: str) -> tuple[float, float, float, float]:
    bar.validate()
    if bar.close > bar.open:
        return bar.open, bar.low, bar.high, bar.close
    if bar.close < bar.open:
        return bar.open, bar.high, bar.low, bar.close
    return (bar.open, bar.high, bar.low, bar.close) if side == "long" else (bar.open, bar.low, bar.high, bar.close)


def _in_range(level: float, a: float, b: float) -> bool:
    return min(a, b) - 1e-12 <= level <= max(a, b) + 1e-12


def _favourable_inside(pos: Position, bar: Bar) -> bool:
    levels = [x for x in (pos.target, None if pos.tp1_done else pos.tp1) if x is not None]
    return any(bar.low <= x <= bar.high for x in levels)


def _close_fill(pos: Position, bar: Bar, index: int, rule: ExchangeRule, qty: float, reference: float,
                reason: str) -> Fill:
    action = "sell" if pos.side == "long" else "buy"
    return make_fill(index, bar.time_ms, action, qty, reference, rule, reason)


def walk_bar(pos: Position, bar: Bar, index: int, management: dict, rule: ExchangeRule) -> list[Fill]:
    """Apply the encoded FBL path, including partial/BE/trail timing."""
    if pos.qty <= 0:
        return []
    bar.validate()
    sign, frozen_stop = pos.sign, pos.stop
    out: list[Fill] = []
    gap = bar.open <= frozen_stop if sign == 1 else bar.open >= frozen_stop
    stop_inside = bar.low <= frozen_stop <= bar.high
    if gap:
        fill = _close_fill(pos, bar, index, rule, pos.qty, bar.open, "gap_stop")
        pos.qty = 0.0; pos.fills.append(fill); return [fill]
    if stop_inside and _favourable_inside(pos, bar):
        fill = _close_fill(pos, bar, index, rule, pos.qty, frozen_stop, "stop_wins")
        pos.qty = 0.0; pos.fills.append(fill); return [fill]

    # A favourable gap is an open-to-open path event.  Stops already had
    # priority above.  Trigger fills never receive favourable gap improvement.
    open_events: list[tuple[float, str]] = []
    if pos.target is not None and ((sign == 1 and bar.open >= pos.target) or (sign == -1 and bar.open <= pos.target)):
        open_events.append((pos.target, "target"))
    if (not pos.tp1_done and pos.tp1 is not None
            and ((sign == 1 and bar.open >= pos.tp1) or (sign == -1 and bar.open <= pos.tp1))):
        open_events.append((pos.tp1, "tp1"))
    open_events.sort(key=lambda x: x[0], reverse=sign == -1)
    for level, kind in open_events:
        if pos.qty <= 0:
            break
        if kind == "tp1":
            close_qty = _floor_step(pos.qty * pos.tp1_frac, rule.step)
            if close_qty <= 0:
                continue
            fill = _close_fill(pos, bar, index, rule, close_qty, level, "tp1")
            pos.qty = _floor_step(pos.qty - close_qty, rule.step)
            pos.tp1_done = True
        else:
            fill = _close_fill(pos, bar, index, rule, pos.qty, level, "target")
            pos.qty = 0.0
        pos.fills.append(fill); out.append(fill)
    if open_events and pos.qty > 0:
        pos.best_price = max(pos.best_price, bar.open) if sign == 1 else min(pos.best_price, bar.open)
        if pos.be_trigger is not None:
            reached = pos.best_price >= pos.be_trigger if sign == 1 else pos.best_price <= pos.be_trigger
            if reached:
                pos.stop = max(pos.stop, pos.entry_exec) if sign == 1 else min(pos.stop, pos.entry_exec)
        if pos.trail_atr is not None:
            if not _positive(bar.atr_prior):
                raise IncompleteCandidate(f"{INCOMPLETE}: missing prior-closed-bar ATR for trail")
            proposed = pos.best_price - sign * float(pos.trail_atr) * float(bar.atr_prior)
            pos.stop = max(pos.stop, proposed) if sign == 1 else min(pos.stop, proposed)

    pts = path_points(bar, pos.side)
    for a, b in zip(pts, pts[1:]):
        if pos.qty <= 0:
            break
        events: list[tuple[float, str]] = []
        if _in_range(pos.stop, a, b): events.append((pos.stop, "stop"))
        if pos.target is not None and _in_range(pos.target, a, b): events.append((pos.target, "target"))
        if not pos.tp1_done and pos.tp1 is not None and _in_range(pos.tp1, a, b): events.append((pos.tp1, "tp1"))
        events.sort(key=lambda x: x[0], reverse=b < a)
        for level, kind in events:
            if pos.qty <= 0:
                break
            if kind == "tp1":
                requested = pos.qty * pos.tp1_frac
                close_qty = _floor_step(requested, rule.step)
                if close_qty <= 0:
                    continue
                fill = _close_fill(pos, bar, index, rule, close_qty, level, "tp1")
                pos.qty = _floor_step(pos.qty - close_qty, rule.step)
                pos.tp1_done = True
                pos.fills.append(fill); out.append(fill)
            else:
                fill = _close_fill(pos, bar, index, rule, pos.qty, level, kind)
                pos.qty = 0.0
                pos.fills.append(fill); out.append(fill)
        # Management sees the completed leg only, and cannot stop retroactively inside it.
        pos.best_price = max(pos.best_price, b) if sign == 1 else min(pos.best_price, b)
        if pos.qty > 0 and pos.be_trigger is not None:
            reached = pos.best_price >= pos.be_trigger if sign == 1 else pos.best_price <= pos.be_trigger
            if reached:
                pos.stop = max(pos.stop, pos.entry_exec) if sign == 1 else min(pos.stop, pos.entry_exec)
        if pos.qty > 0 and pos.trail_atr is not None:
            if not _positive(bar.atr_prior):
                raise IncompleteCandidate(f"{INCOMPLETE}: missing prior-closed-bar ATR for trail")
            proposed = pos.best_price - sign * float(pos.trail_atr) * float(bar.atr_prior)
            pos.stop = max(pos.stop, proposed) if sign == 1 else min(pos.stop, proposed)
    return out


def discretionary_exit(pos: Position, bars: Sequence[Bar], decision_index: int, rule: ExchangeRule,
                       reason: str) -> Fill:
    """Call only after the decision candle's full intrabar path was processed."""
    index, reference, time_ms = next_open(bars, decision_index)
    if pos.qty <= 0:
        raise RunnerError("cannot discretionarily exit an already closed position")
    bar = bars[index]
    fill = _close_fill(pos, bar, index, rule, pos.qty, reference, reason)
    pos.qty = 0.0; pos.fills.append(fill)
    return fill


def funding_cash(side: str, qty: float, entry_time_ms: int, exit_time_ms: int, ledger: FundingLedger) -> float:
    """Signed cash: positive rate credits a short and debits a long."""
    if not ledger.checksum_verified or not ledger.coverage_complete:
        raise IncompleteCandidate(f"{INCOMPLETE}: funding coverage/checksum is incomplete")
    sign = 1 if side == "long" else -1
    cash = 0.0
    for event in ledger.events:
        if entry_time_ms <= event.time_ms < exit_time_ms:
            if not _positive(event.mark) or not math.isfinite(event.rate):
                raise IncompleteCandidate(f"{INCOMPLETE}: funding mark/rate gap")
            cash += -sign * qty * float(event.mark) * event.rate
    return cash


def funding_cash_for_position(pos: Position, ledger: FundingLedger) -> float:
    """Fund each closed quantity over its own exact ``[entry, exit)`` lifetime.

    This is important after TP1: funding after that partial applies only to the
    remaining position.  An event exactly at entry counts; one exactly at an
    exit fill does not count for the quantity closed by that fill.
    """
    if pos.qty > 0 or not pos.fills[1:]:
        raise IncompleteCandidate(f"{INCOMPLETE}: funding needs a fully closed position")
    return math.fsum(funding_cash(pos.side, fill.qty, pos.entry_time_ms, fill.time_ms, ledger)
                     for fill in pos.fills[1:])


def account_position(pos: Position, signed_funding_cash: float) -> dict:
    exits = pos.fills[1:]
    exited = math.fsum(x.qty for x in exits)
    if not math.isclose(exited, pos.initial_qty, rel_tol=0, abs_tol=1e-9):
        raise IncompleteCandidate(f"{INCOMPLETE}: position is not fully closed")
    gross = math.fsum(pos.sign * (x.reference_price - pos.entry_reference) * x.qty for x in exits)
    fees = math.fsum(x.fee_cash for x in pos.fills)
    slip = math.fsum(x.slippage_cash for x in pos.fills)
    net = gross + signed_funding_cash - fees - slip
    return {"gross_reference_price_pnl": gross, "signed_funding_cash": signed_funding_cash,
            "fee_cash": fees, "adverse_slippage_cash": slip, "net_pnl": net}


def stress_costs(accounting: dict, multiplier: float) -> float:
    """Credits stay 1x; fees, slip, and negative funding debits are stressed."""
    funding = float(accounting["signed_funding_cash"])
    stressed_funding = funding if funding >= 0 else funding * multiplier
    return (float(accounting["gross_reference_price_pnl"]) + stressed_funding
            - multiplier * float(accounting["fee_cash"])
            - multiplier * float(accounting["adverse_slippage_cash"]))


def required_report_shell(contract: dict, **values: object) -> dict:
    """No report can silently omit a required key; unknown values fail the gate."""
    report = {key: None for key in contract["required_report"]}
    unknown = set(values) - set(report)
    if unknown:
        raise RunnerError(f"unknown report fields: {sorted(unknown)}")
    report.update(values)
    return report


def promotion_gates(report: dict, contract: dict, *, fold_net_r: Sequence[float], symbol_contributions: dict,
                    stress_1_5_net_r: float, causal_parity: bool, independent_review: bool) -> dict:
    missing = [key for key in contract["required_report"] if report.get(key) is None]
    trades = int(report.get("trade_count") or 0)
    symbols = len(symbol_contributions)
    total_abs = math.fsum(abs(float(x)) for x in symbol_contributions.values())
    concentration = max((abs(float(x)) / total_abs for x in symbol_contributions.values()), default=1.0)
    regime = report.get("regime_breakdown") or {}
    regime_min = min((float(x["mean_r"]) for x in regime.values()), default=-math.inf)
    g = contract["promotion_gates"]
    max_dd = report.get("max_dd_r")
    return {
        "aggregate_net_r_gt_0": float(report.get("net_r") or 0) > 0,
        "aggregate_mean_r_gt_0": float(report.get("mean_r") or 0) > 0,
        "aggregate_pf_gt_1_05": float(report.get("profit_factor") or 0) > g["aggregate_pf_gt"],
        "minimum_closed_trades": trades >= g["minimum_closed_trades"],
        "minimum_symbols": symbols >= g["minimum_symbols"],
        "minimum_positive_folds": sum(float(x) > 0 for x in fold_net_r) >= g["minimum_positive_folds"],
        "minimum_monthly_trade_median": float(report.get("monthly_trade_median") or 0) >= g["minimum_monthly_trade_median"],
        "max_dd_r": max_dd is not None and float(max_dd) <= g["max_dd_r_lte"],
        "minimum_regime_mean_r": regime_min >= g["minimum_regime_mean_r"],
        "symbol_concentration": concentration <= g["max_symbol_absolute_contribution_fraction"],
        "positive_after_1_5x": stress_1_5_net_r > 0,
        "causal_parity": bool(causal_parity), "independent_review": bool(independent_review),
        "required_reports": not missing,
        "zero_incomplete_rows": int(report.get("incomplete_rows") or 0) == 0,
    }


def trade_from_evidence(row: dict) -> dict:
    """Recompute one trade from reference fills and verified funding rows.

    Executable prices, fees, slippage, funding cash, net cash and R are never
    accepted from the caller.  They are reconstructed with the frozen rules.
    """
    try:
        side, risk = row["side"], float(row["risk_cash"])
        rule = ExchangeRule(**row["exchange_rule"])
        entry, exits = row["entry"], row["exits"]
    except (KeyError, TypeError, ValueError) as exc:
        raise IncompleteCandidate(f"{INCOMPLETE}: malformed per-trade evidence") from exc
    if side not in ("long", "short") or not _positive(risk) or not isinstance(exits, list) or not exits:
        raise IncompleteCandidate(f"{INCOMPLETE}: invalid side, risk, or exit evidence")
    rule.validate()
    entry_qty = float(entry.get("qty", 0))
    if (not _positive(entry_qty)
            or not math.isclose(entry_qty, _floor_step(entry_qty, rule.step), rel_tol=0, abs_tol=1e-9)):
        raise IncompleteCandidate(f"{INCOMPLETE}: invalid entry quantity")
    entry_fill = make_fill(int(entry["index"]), int(entry["time_ms"]),
                           "buy" if side == "long" else "sell", entry_qty,
                           float(entry["reference_price"]), rule, "entry")
    if (entry_qty < rule.min_qty or entry_qty * entry_fill.executable_price < rule.min_notional
            or (rule.max_qty is not None and entry_qty > rule.max_qty + 1e-12)):
        raise IncompleteCandidate(f"{INCOMPLETE}: entry violates frozen exchange filters")
    pos = Position(side, entry_fill.index, entry_fill.time_ms, entry_fill.reference_price,
                   entry_fill.executable_price, entry_qty, entry_qty, 1.0, 0.0,
                   None, None, None, None, entry_fill.executable_price, fills=[entry_fill])
    allowed = {"target", "tp1", "stop", "stop_wins", "gap_stop", "signal", "time"}
    for raw in exits:
        reason = raw.get("reason")
        qty = float(raw.get("qty", 0))
        if (reason not in allowed or not _positive(qty)
                or not math.isclose(qty, _floor_step(qty, rule.step), rel_tol=0, abs_tol=1e-9)):
            raise IncompleteCandidate(f"{INCOMPLETE}: invalid exit fill")
        fill = make_fill(int(raw["index"]), int(raw["time_ms"]),
                         "sell" if side == "long" else "buy", qty,
                         float(raw["reference_price"]), rule, reason)
        if fill.index < entry_fill.index or fill.time_ms < entry_fill.time_ms:
            raise IncompleteCandidate(f"{INCOMPLETE}: exit precedes entry")
        pos.fills.append(fill)
    remaining = entry_qty - math.fsum(x.qty for x in pos.fills[1:])
    pos.qty = 0.0 if abs(remaining) <= 1e-9 else max(0.0, remaining)
    try:
        raw_ledger = row["funding"]
        events = tuple(sorted((FundingEvent(int(x["time_ms"]), float(x["rate"]),
                                            None if x.get("mark") is None else float(x["mark"]))
                               for x in raw_ledger["events"]), key=lambda x: x.time_ms))
        if len({x.time_ms for x in events}) != len(events):
            raise IncompleteCandidate(f"{INCOMPLETE}: duplicate funding settlement")
        ledger = FundingLedger(events, raw_ledger["checksum_verified"] is True,
                               raw_ledger["coverage_complete"] is True)
    except (KeyError, TypeError, ValueError) as exc:
        raise IncompleteCandidate(f"{INCOMPLETE}: malformed funding evidence") from exc
    funding = funding_cash_for_position(pos, ledger)
    accounting = account_position(pos, funding)
    r_value = accounting["net_pnl"] / risk
    return {"trade_id": str(row.get("trade_id", "")), "fold": str(row.get("fold", "")),
            "symbol": str(row.get("symbol", "")), "regime": str(row.get("regime", "")),
            "entry_time_ms": entry_fill.time_ms, "exit_time_ms": max(x.time_ms for x in pos.fills[1:]),
            "risk_cash": risk, "r": r_value, "accounting": accounting,
            "stress_r": {str(k): stress_costs(accounting, k) / risk for k in (1.0, 1.5, 2.0)}}


def _breakdown(trades: Sequence[dict], key: str) -> dict:
    values: dict[str, list[float]] = {}
    for trade in trades:
        values.setdefault(trade[key], []).append(trade["r"])
    return {name: {"trade_count": len(rows), "net_r": math.fsum(rows),
                   "mean_r": math.fsum(rows) / len(rows)} for name, rows in sorted(values.items())}


def _bootstrap_ci(values: Sequence[float]) -> list[float]:
    if not values:
        return [0.0, 0.0]
    rng = random.Random(int(hashlib.sha256(canonical(list(values))).hexdigest()[:16], 16))
    n = len(values)
    means = sorted(math.fsum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(1000))
    return [means[24], means[974]]


def derive_report(trades: Sequence[dict], contract: dict) -> tuple[dict, dict, list[float], dict]:
    """Derive every economic metric from reconstructed trades, never aggregates."""
    ordered = sorted(trades, key=lambda x: (x["exit_time_ms"], x["trade_id"]))
    rs = [x["r"] for x in ordered]
    gains = math.fsum(x for x in rs if x > 0)
    losses = -math.fsum(x for x in rs if x < 0)
    net = math.fsum(rs)
    equity = peak = max_dd = 0.0
    for value in rs:
        equity += value; peak = max(peak, equity); max_dd = max(max_dd, peak - equity)
    symbol_contrib = {k: v["net_r"] for k, v in _breakdown(ordered, "symbol").items()}
    total_abs = math.fsum(abs(x) for x in symbol_contrib.values())
    concentration = max((abs(x) / total_abs for x in symbol_contrib.values()), default=1.0)
    months: dict[str, int] = {}
    for fold in contract["folds"]:
        current = datetime.fromisoformat(fold["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(fold["end"].replace("Z", "+00:00"))
        while current < end:
            months.setdefault(current.strftime("%Y-%m"), 0)
            current = datetime(current.year + (current.month == 12), current.month % 12 + 1, 1, tzinfo=timezone.utc)
    for trade in ordered:
        key = datetime.fromtimestamp(trade["exit_time_ms"] / 1000, timezone.utc).strftime("%Y-%m")
        months[key] = months.get(key, 0) + 1
    mean = net / len(rs) if rs else 0.0
    if len(rs) >= 2 and statistics.stdev(rs) > 0:
        z = abs(mean) / (statistics.stdev(rs) / math.sqrt(len(rs)))
        raw_p = math.erfc(z / math.sqrt(2))
    else:
        raw_p = 1.0
    fold_net = [math.fsum(x["r"] for x in ordered if x["fold"] == fold["name"]) for fold in contract["folds"]]
    stress = {str(k): math.fsum(x["stress_r"][str(k)] for x in ordered) for k in (1.0, 1.5, 2.0)}
    report = required_report_shell(
        contract, trade_count=len(rs), net_r=net, mean_r=mean,
        profit_factor=(gains / losses if losses else (1e12 if gains else 0.0)),
        win_rate=(sum(x > 0 for x in rs) / len(rs) if rs else 0.0), max_dd_r=max_dd,
        expectancy_r=mean,
        exposure_time=math.fsum(x["exit_time_ms"] - x["entry_time_ms"] for x in ordered),
        monthly_trade_median=(statistics.median(months.values()) if months else 0.0),
        symbol_concentration=concentration, regime_breakdown=_breakdown(ordered, "regime"),
        coin_breakdown=_breakdown(ordered, "symbol"),
        fee_drag_r=math.fsum(x["accounting"]["fee_cash"] / x["risk_cash"] for x in ordered),
        slippage_drag_r=math.fsum(x["accounting"]["adverse_slippage_cash"] / x["risk_cash"] for x in ordered),
        funding_drag_r=math.fsum(-x["accounting"]["signed_funding_cash"] / x["risk_cash"] for x in ordered),
        gross_r=math.fsum(x["accounting"]["gross_reference_price_pnl"] / x["risk_cash"] for x in ordered),
        raw_p_value=raw_p, holm_adjusted_p_value=min(1.0, raw_p * len(CANDIDATE_IDS)),
        bootstrap_ci_95_mean_r=_bootstrap_ci(rs), incomplete_rows=0,
        survivorship_disclosure="Frozen CORE-20 point-in-time universe; delist/survivorship limits remain disclosed.",
        failure_reason="external causal-parity and independent-review verdicts are not assertable by this runner",
    )
    return report, stress, fold_net, symbol_contrib


def evaluate_payload(payload: object, pf: Preflight) -> dict:
    """Reconstruct a packet from per-trade fill/funding evidence only."""
    if not isinstance(payload, dict) or payload.get("candidate_id") != pf.candidate_id:
        raise RunnerError("prepared input candidate identity does not match the CLI candidate")
    forbidden = {"report", "promotion_gates", "promoted", "causal_parity", "independent_review"} & set(payload)
    if forbidden:
        raise RunnerError(f"caller-supplied aggregate/verdict fields are forbidden: {sorted(forbidden)}")
    digest = payload.get("manifest_digest")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
        raise RunnerError("prepared input must record a SHA-256 manifest digest")
    expected_folds = [x["name"] for x in pf.contract["folds"]]
    folds_in = payload.get("folds")
    if folds_in != expected_folds:
        raise RunnerError("prepared input must identify wf1, wf2, and wf3 in frozen order")
    raw_trades = payload.get("trades")
    if not isinstance(raw_trades, list):
        raise RunnerError("prepared input must contain per-trade fill/funding evidence")
    trades = [trade_from_evidence(row) for row in raw_trades]
    identities = [x["trade_id"] for x in trades]
    if any(not x for x in identities) or len(identities) != len(set(identities)):
        raise RunnerError("trade identities must be non-empty and unique")
    if any(x["fold"] not in expected_folds or not x["symbol"] or not x["regime"] for x in trades):
        raise RunnerError("each trade must identify a frozen fold, symbol, and regime")
    report, stress, fold_net, contributions = derive_report(trades, pf.contract)
    folds = [{"name": name, "trade_count": sum(x["fold"] == name for x in trades), "net_r": net}
             for name, net in zip(expected_folds, fold_net)]
    gates = promotion_gates(
        report, pf.contract, fold_net_r=fold_net, symbol_contributions=contributions,
        stress_1_5_net_r=float(stress["1.5"]), causal_parity=False, independent_review=False,
    )
    return {"candidate_id": pf.candidate_id, "manifest_digest": digest,
            "folds": folds, "report": report, "cost_stress_net_r": stress,
            "promotion_gates": gates, "promoted": False,
            "external_verdict_required": ["causal_parity", "independent_review"],
            "scope": {"research_only": True, "paper_testnet_only": True,
                      "default_enabled": False, "mainnet_authority": False}}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Frozen STRATEGY-CAUSAL-PACK-01 causal runner")
    ap.add_argument("--candidate", required=True, choices=CANDIDATE_IDS)
    ap.add_argument("--input", required=True, help="prepared, checksum-verified causal input JSON")
    ap.add_argument("--out", required=True)
    ap.add_argument("--identity-dir", required=True)
    args = ap.parse_args(argv)

    # The input path is intentionally not opened until preflight succeeds.
    def loader() -> object:
        with open(args.input, encoding="utf-8") as fh:
            return json.load(fh)

    try:
        run_with_loader(args.candidate, args.out, loader, evaluate_payload, identity_dir=args.identity_dir)
    except (OSError, ValueError, RunnerError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(f"{args.candidate}: immutable packet -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
