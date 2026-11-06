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
import subprocess
import sys
from dataclasses import dataclass, field
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
    if identity_dir is not None and (Path(identity_dir) / f"{identity}.json").exists():
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
    """Integrity checks precede ``loader``; output and identity records are O_EXCL."""
    pf = preflight(candidate_id, output, identity_dir=identity_dir, head_fn=head_fn, dirty_fn=dirty_fn)
    packet = evaluator(loader(), pf)
    packet.setdefault("format", FORMAT)
    packet["integrity"] = {
        "contract_sha256": pf.contract_sha256, "git_head": pf.git_head,
        "candidate_id": pf.candidate_id, "evaluation_identity": pf.identity,
    }
    packet["packet_sha256"] = hashlib.sha256(canonical(packet)).hexdigest()
    _exclusive_json(Path(output), packet)
    if identity_dir is not None:
        record = Path(identity_dir) / f"{pf.identity}.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        _exclusive_json(record, {"identity": pf.identity, "output": str(Path(output).resolve()),
                                 "packet_sha256": packet["packet_sha256"]})
    return packet


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


def evaluate_payload(payload: object, pf: Preflight) -> dict:
    """CLI boundary for a prepared packet; market adapters should replace this evaluator.

    The prepared packet must already contain fold-level causal results.  The runner
    refuses incomplete reports rather than pretending a partial packet is evidence.
    """
    if not isinstance(payload, dict) or payload.get("candidate_id") != pf.candidate_id:
        raise RunnerError("prepared input candidate identity does not match the CLI candidate")
    digest = payload.get("manifest_digest")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
        raise RunnerError("prepared input must record a SHA-256 manifest digest")
    folds = payload.get("folds")
    expected_folds = [x["name"] for x in pf.contract["folds"]]
    if not isinstance(folds, list) or [x.get("name") for x in folds if isinstance(x, dict)] != expected_folds:
        raise RunnerError("prepared input must report wf1, wf2, and wf3 separately in frozen order")
    report = payload.get("report")
    if not isinstance(report, dict):
        raise RunnerError("prepared input has no required report")
    missing = [key for key in pf.contract["required_report"] if key not in report]
    if missing:
        raise RunnerError(f"prepared input omits required report fields: {missing}")
    if report.get("incomplete_rows") != 0:
        raise IncompleteCandidate(f"{INCOMPLETE}: prepared input contains incomplete rows")
    contributions = payload.get("symbol_contributions")
    if not isinstance(contributions, dict):
        raise RunnerError("prepared input must include symbol contributions")
    stress = payload.get("cost_stress_net_r")
    if not isinstance(stress, dict) or not all(str(x) in stress for x in (1.0, 1.5, 2.0)):
        raise RunnerError("prepared input must report 1.0x, 1.5x, and 2.0x cost stress")
    fold_net = [x.get("net_r") for x in folds]
    if any(not isinstance(x, (int, float)) or not math.isfinite(float(x)) for x in fold_net):
        raise RunnerError("each frozen fold needs finite net_r")
    gates = promotion_gates(
        report, pf.contract, fold_net_r=fold_net, symbol_contributions=contributions,
        stress_1_5_net_r=float(stress["1.5"]), causal_parity=payload.get("causal_parity") is True,
        independent_review=payload.get("independent_review") is True,
    )
    return {"candidate_id": pf.candidate_id, "manifest_digest": digest,
            "folds": folds, "report": report, "cost_stress_net_r": stress,
            "promotion_gates": gates, "promoted": all(gates.values()),
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
