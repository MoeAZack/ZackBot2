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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "contracts" / "strategy_causal_pack_01" / "strategy_causal_pack_01_contract.v1.json"
CONTRACT_SHA256 = "ea6c6844374c3c9dff80743ff6a69855aa5c77777427182cfb63eb4c1c5a3105"
FORMAT = "zackbot.strategy-causal-pack-01/1"
INCOMPLETE = "INCOMPLETE_CANDIDATE"
CANDIDATE_IDS = (
    "pullback_rsi_short_h1_causal_v2",
    "exp_mom_short_c27_causal_v2",
    "volume_pressure_inventory_short_1h_causal_v2",
)
SOURCE_BINDINGS = {
    "pullback_rsi_short_h1_causal_v2": ("pullback_rsi_short_h1", "sig_trend_pullback_rsi"),
    "exp_mom_short_c27_causal_v2": ("exp_mom_short_c27", "sig_exp_mom_short"),
    "volume_pressure_inventory_short_1h_causal_v2": (
        "volume_pressure_inventory_1h", "sig_volume_pressure_inventory_1h"),
}
ARCHIVE_MANIFEST_PATH = ROOT / "research_evidence" / "manifests" / "binance-um-archive-v1.json.gz"
DEFAULT_ARCHIVE_STORE = Path(r"C:\Dev\ZackBot2_data\binance_um")
UNIVERSE_PATH = ROOT / "research_evidence" / "universe" / "pit-top40-qv30d-v4.json"
EXCHANGE_INFO_PATH = (ROOT / "research_evidence" / "prereg" /
                      "binance-um-exchangeinfo-mainnet-raw-20261011T151658Z.json")
SOURCE_RUNNER_PATHS = {
    "pullback_rsi_short_h1_causal_v2": ROOT / "tools" / "research" / "run_pullback_rsi_short_h1_candidate.py",
    "exp_mom_short_c27_causal_v2": ROOT / "tools" / "research" / "run_exp_mom_short_c27_candidate.py",
    "volume_pressure_inventory_short_1h_causal_v2": (
        ROOT / "tools" / "research" / "run_volume_pressure_inventory_candidate.py"),
}


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


def verify_file_digest(path: os.PathLike[str] | str, claimed_sha256: str) -> str:
    """Bind evidence to file bytes; a plausible-looking digest string is not evidence."""
    if (not isinstance(claimed_sha256, str) or len(claimed_sha256) != 64
            or any(c not in "0123456789abcdef" for c in claimed_sha256.lower())):
        raise RunnerError("claimed digest is not a SHA-256 hex value")
    actual = _sha256(Path(path))
    if actual != claimed_sha256.lower():
        raise RunnerError(f"evidence digest mismatch: actual {actual}")
    return actual


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


def validate_book_contract(contract: dict) -> dict:
    """Refuse execution unless every executable book control is still frozen."""
    book = contract.get("book_and_sizing") or {}
    expected = {
        "reference_start_equity_usdt": 500.0,
        "risk_fraction_of_current_candidate_book_equity": 0.01,
        "maximum_open_positions": 4,
        "gross_leverage_multiple": 10.0,
        "daily_halt_fraction": 0.08,
        "equity_compounding": True,
        "candidate_books_isolated": True,
        "cross_candidate_capital_sharing": False,
    }
    if any(book.get(key) != value for key, value in expected.items()):
        raise RunnerError("frozen candidate-book controls changed")
    if not str(book.get("daily_halt_timezone", "")).startswith("Africa/Cairo"):
        raise RunnerError("daily halt timezone is not frozen to Africa/Cairo")
    if not str(book.get("entry_priority", "")).startswith("For simultaneous next-open entries"):
        raise RunnerError("simultaneous-entry priority rule changed")
    if not str(book.get("caller_override", "")).startswith("forbidden"):
        raise RunnerError("candidate-book controls became caller-overridable")
    return book


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
    # The contract bytes are pinned above. The evaluator head is intentionally
    # the actual clean head and becomes part of the immutable evaluation ID.
    if not head:
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


def _run_with_loader(
    candidate_id: str,
    output: os.PathLike[str] | str,
    loader: Callable[[], object],
    evaluator: Callable[[object, Preflight], dict],
    *,
    identity_dir: os.PathLike[str] | str | None = None,
    head_fn: Callable[[], str] = git_head,
    dirty_fn: Callable[[], bool] = git_dirty,
) -> dict:
    """Internal raw-adapter harness; not a public prepared-evidence route."""
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
        _exclusive_identity_json(claim, {"state": "RESERVED", "identity": pf.identity,
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
    """Create a disposable JSON artifact; remove it if its payload is incomplete."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o644)
    try:
        _write_json_fd(fd, value)
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _exclusive_identity_json(path: Path, value: dict) -> None:
    """Create a consumed identity and never unlink it after O_EXCL succeeds.

    Even an empty or partial file is a tombstone: the filename binds the exact
    identity, and all later attempts refuse on existence before loading data.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    _write_json_fd(fd, value)  # any exception propagates; the claim remains consumed


def _write_json_fd(fd: int, value: dict) -> None:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(value, fh, sort_keys=True, indent=2, allow_nan=False)
        fh.write("\n")
        _flush_json(fh)
        _fsync_json(fh)


def _flush_json(fh) -> None:
    fh.flush()


def _fsync_json(fh) -> None:
    os.fsync(fh.fileno())


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


@dataclass
class BookLot:
    """Minimal marked position state used by the raw evaluator's portfolio book."""

    symbol: str
    side: str
    qty: float
    entry_reference: float
    mark: float

    def validate(self) -> None:
        if self.side not in ("long", "short"):
            raise RunnerError("book position side must be long or short")
        if not all(_positive(value) for value in (self.qty, self.entry_reference, self.mark)):
            raise RunnerError("book position needs positive qty, entry reference, and mark")

    @property
    def sign(self) -> int:
        return 1 if self.side == "long" else -1


@dataclass(frozen=True)
class EntryTerms:
    symbol: str
    risk_cash: float
    leverage_notional_cap: float
    marked_equity: float
    gross_open_notional: float


@dataclass(frozen=True)
class BookAdmission:
    """One new lot plus entry-only costs, booked exactly once on admission."""

    lot: BookLot
    entry_fee_and_slippage_cash: float


class FrozenCandidateBook:
    """Contract-frozen, isolated candidate book for causal raw evaluation.

    The constructor intentionally accepts no sizing or risk controls. A contract
    change is required to alter any of these values.
    """

    START_EQUITY = 500.0
    RISK_FRACTION = 0.01
    MAX_OPEN_POSITIONS = 4
    GROSS_LEVERAGE_MULTIPLE = 10.0
    DAILY_HALT_FRACTION = 0.08
    CAIRO = ZoneInfo("Africa/Cairo")

    def __init__(self, core20_order: Sequence[str]):
        order = tuple(core20_order)
        if len(order) != 20 or len(set(order)) != 20 or any(not symbol for symbol in order):
            raise RunnerError("checksum-verified frozen CORE-20 order must contain 20 unique symbols")
        self.core20_order = order
        self._priority = {symbol: index for index, symbol in enumerate(order)}
        # Settled cash starts at contract equity. Entry costs, signed funding,
        # and incremental exit cashflows touch this ledger exactly once;
        # open reference-price PnL remains unrealized in marked_equity().
        self.settled_cash_equity = self.START_EQUITY
        self.positions: dict[str, BookLot] = {}
        self.day: str | None = None
        self.day_start_equity: float | None = None
        self.entries_halted = False

    def marked_equity(self) -> float:
        unrealized = sum(
            lot.sign * (lot.mark - lot.entry_reference) * lot.qty
            for lot in self.positions.values()
        )
        return self.settled_cash_equity + unrealized

    def gross_open_notional(self) -> float:
        return sum(abs(lot.qty * lot.mark) for lot in self.positions.values())

    def risk_cash(self) -> float:
        return max(0.0, self.marked_equity() * self.RISK_FRACTION)

    def leverage_notional_cap(self) -> float:
        return max(
            0.0,
            self.GROSS_LEVERAGE_MULTIPLE * self.marked_equity() - self.gross_open_notional(),
        )

    @classmethod
    def _cairo_day(cls, time_ms: int) -> str:
        if type(time_ms) is not int:
            raise RunnerError("bar timestamp must be integer milliseconds")
        return datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).astimezone(cls.CAIRO).date().isoformat()

    def advance_bar(self, time_ms: int, marks: dict[str, float]) -> None:
        """Apply causal marks, freeze the Cairo-day baseline, and latch the halt."""
        if set(marks) != set(self.positions):
            missing = sorted(set(self.positions) - set(marks))
            extra = sorted(set(marks) - set(self.positions))
            raise IncompleteCandidate(
                f"{INCOMPLETE}: causal marks must cover every open position exactly; "
                f"missing={missing}, extra={extra}"
            )
        for symbol, mark in marks.items():
            if not _positive(mark):
                raise IncompleteCandidate(f"{INCOMPLETE}: invalid causal mark for {symbol}")
            self.positions[symbol].mark = float(mark)
        cairo_day = self._cairo_day(time_ms)
        equity = self.marked_equity()
        if cairo_day != self.day:
            self.day = cairo_day
            self.day_start_equity = equity
            self.entries_halted = False
        elif equity <= float(self.day_start_equity) * (1.0 - self.DAILY_HALT_FRACTION):
            self.entries_halted = True

    def admit_simultaneous(
        self,
        time_ms: int,
        symbols: Sequence[str],
        opener: Callable[[EntryTerms], BookAdmission | None],
    ) -> list[str]:
        """Process one next-open cohort in frozen CORE-20 priority order.

        ``opener`` is an internal source-signal adapter: it receives only terms
        computed from current book state and returns the resulting lot plus its
        entry-only fee/slippage cash. That cost is settled exactly once here;
        close APIs accept only later incremental cashflows. Later symbols
        therefore see earlier fills and costs.
        """
        requested = tuple(symbols)
        if len(set(requested)) != len(requested):
            raise RunnerError("duplicate simultaneous entry request")
        unknown = set(requested) - set(self._priority)
        if unknown:
            raise RunnerError(f"entry outside frozen CORE-20: {sorted(unknown)}")
        if self.day != self._cairo_day(time_ms):
            self.advance_bar(time_ms, {})
        admitted: list[str] = []
        for symbol in sorted(requested, key=self._priority.__getitem__):
            self._latch_halt()
            if (self.entries_halted or symbol in self.positions
                    or len(self.positions) >= self.MAX_OPEN_POSITIONS):
                continue
            cap = self.leverage_notional_cap()
            risk = self.risk_cash()
            if cap <= 0 or risk <= 0:
                continue
            terms = EntryTerms(symbol, risk, cap, self.marked_equity(), self.gross_open_notional())
            opened = opener(terms)
            if opened is None:
                continue
            lot = opened.lot
            entry_cost_cash = opened.entry_fee_and_slippage_cash
            lot.validate()
            if lot.symbol != symbol or not math.isfinite(entry_cost_cash) or entry_cost_cash < 0:
                raise RunnerError("internal opener returned mismatched lot or invalid entry cost")
            self.positions[symbol] = lot
            self.settled_cash_equity -= entry_cost_cash
            self._latch_halt()
            admitted.append(symbol)
        return admitted

    def _latch_halt(self) -> None:
        if (self.day_start_equity is not None
                and self.marked_equity() <= self.day_start_equity * (1.0 - self.DAILY_HALT_FRACTION)):
            self.entries_halted = True

    def book_signed_funding_cash(self, symbol: str, signed_funding_cash: float) -> None:
        """Settle one funding event before any later entry or exit decision."""
        if symbol not in self.positions or not math.isfinite(signed_funding_cash):
            raise RunnerError("invalid signed funding settlement")
        self.settled_cash_equity += signed_funding_cash
        self._latch_halt()

    def book_partial_exit(
        self,
        symbol: str,
        executed_qty: float,
        realized_reference_pnl_cash: float,
        exit_fee_and_slippage_cash: float,
        rule: "ExchangeRule",
    ) -> None:
        """Settle one exchange-floored partial fill and retain the marked remainder.

        The realized PnL and exit cost must describe only this fill. Entry costs
        were already settled at admission and are deliberately absent here.
        """
        if symbol not in self.positions:
            raise RunnerError("partial exit has no open position")
        rule.validate()
        lot = self.positions[symbol]
        floored_qty = _floor_step(executed_qty, rule.step)
        if (not _positive(executed_qty) or abs(floored_qty - executed_qty) > 1e-9
                or floored_qty >= lot.qty - 1e-12):
            raise RunnerError("partial exit quantity is not a valid exchange-floored subset")
        if (not math.isfinite(realized_reference_pnl_cash)
                or not math.isfinite(exit_fee_and_slippage_cash)
                or exit_fee_and_slippage_cash < 0):
            raise RunnerError("invalid incremental partial-exit cashflow")
        remaining = _floor_step(lot.qty - floored_qty, rule.step)
        if remaining <= 0:
            raise RunnerError("partial exit must retain positive exchange-floored quantity")
        lot.qty = remaining
        self.settled_cash_equity += realized_reference_pnl_cash - exit_fee_and_slippage_cash
        self._latch_halt()

    def book_final_exit(
        self,
        symbol: str,
        realized_reference_pnl_cash: float,
        exit_fee_and_slippage_cash: float,
    ) -> None:
        """Settle only the final fill's incremental PnL/cost and remove the lot."""
        if symbol not in self.positions:
            raise RunnerError("final exit has no open position")
        if (not math.isfinite(realized_reference_pnl_cash)
                or not math.isfinite(exit_fee_and_slippage_cash)
                or exit_fee_and_slippage_cash < 0):
            raise RunnerError("invalid incremental final-exit cashflow")
        del self.positions[symbol]
        self.settled_cash_equity += realized_reference_pnl_cash - exit_fee_and_slippage_cash
        self._latch_halt()


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


def validate_source_bindings(contract: dict) -> dict:
    """Prove all three identities point at the repository's exact frozen signal functions."""
    import strategies as source

    out = {}
    for candidate_id, (strategy_key, function_name) in SOURCE_BINDINGS.items():
        spec = candidate_spec(contract, candidate_id)
        strategy = source.STRATEGIES.get(strategy_key)
        if not strategy or strategy.get("fn") is not getattr(source, function_name, None):
            raise RunnerError(f"{candidate_id}: missing exact source function strategies.py:{function_name}")
        if strategy.get("tf") != spec["timeframe"] or not strategy.get("research_only"):
            raise RunnerError(f"{candidate_id}: source timeframe/research-only identity changed")
        if candidate_id != "volume_pressure_inventory_short_1h_causal_v2" and strategy.get("sides") != spec["side"]:
            raise RunnerError(f"{candidate_id}: source side changed")
        expected_management = dict(strategy["mgmt"])
        if candidate_id == "exp_mom_short_c27_causal_v2":
            expected_management["max_bars"] = 180
        if expected_management != spec["management"]:
            raise RunnerError(f"{candidate_id}: source management no longer matches the frozen contract")
        out[candidate_id] = {"strategy_key": strategy_key, "function": function_name,
                             "timeframe": strategy["tf"], "params": dict(strategy.get("params") or {})}
    return out


def missing_input_report(*, archive_store: os.PathLike[str] | str | None = None,
                         contract_merged: bool = False) -> dict:
    """Read no market bytes; name the exact blockers to a real raw evaluation."""
    contract, contract_digest = load_contract()
    validate_book_contract(contract)
    bindings = validate_source_bindings(contract)
    gaps = []
    for label, path in (("archive manifest", ARCHIVE_MANIFEST_PATH), ("PIT universe", UNIVERSE_PATH),
                        ("frozen exchangeInfo", EXCHANGE_INFO_PATH)):
        if not path.is_file():
            gaps.append(f"missing {label}: {path}")
    for candidate_id, path in SOURCE_RUNNER_PATHS.items():
        if not path.is_file():
            gaps.append(f"{candidate_id}: missing source runner {path}")
    if not contract_merged:
        gaps.append("contract PR #298 is not recorded merged; market-data reads remain forbidden")
    if archive_store is None:
        gaps.append("missing external binance_um archive store path for manifest klines/fundingRate/markPriceKlines")
    elif not Path(archive_store).is_dir():
        gaps.append(f"binance_um archive store does not exist: {Path(archive_store)}")
    gaps.append("raw archive adapter is not yet enabled; no evaluation/promotion route exists")
    return {
        "format": "zackbot.strategy-causal-pack-01-readiness/1", "ready": False,
        "contract_sha256": contract_digest, "source_bindings": bindings,
        "required_sources": {"manifest": str(ARCHIVE_MANIFEST_PATH), "universe": str(UNIVERSE_PATH),
                             "exchange_info": str(EXCHANGE_INFO_PATH),
                             "archive_store": str(Path(archive_store) if archive_store is not None else DEFAULT_ARCHIVE_STORE),
                             "source_runners": {k: str(v) for k, v in SOURCE_RUNNER_PATHS.items()}},
        "folds": contract["folds"], "cutoff": contract["data"]["forbid_read_at_or_after"],
        "required_raw_series": ["klines:1h", "klines:4h", "fundingRate", "markPriceKlines:1h"],
        "invariants": ["CORE-20 PIT membership", "fold-local warmup/purge", "next-open fills",
                       "prior-closed ATR", "actual exchange filters", "actual funding/causal marks",
                       "isolated 500 USDT books", "dynamic 1% marked-equity risk",
                       "four-position/10x gross caps", "CORE-20 simultaneous-entry priority",
                       "8% Africa/Cairo day entry halt with exits always enabled", "compounding",
                       "required report and promotion gates", "immutable identity tombstones"],
        "gaps": gaps,
    }


def evaluate_payload(payload: object, pf: Preflight) -> dict:
    """Compatibility refusal: prepared trades/fills are never evaluation evidence."""
    del payload, pf
    raise RunnerError(
        "prepared fills/trades are untrusted and forbidden; the raw-bar source-signal evaluator is not implemented"
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Frozen STRATEGY-CAUSAL-PACK-01 runner (raw evaluator unavailable)")
    ap.add_argument("--candidate", required=True, choices=CANDIDATE_IDS)
    ap.add_argument("--out", required=True)
    ap.add_argument("--identity-dir", required=True)
    ap.add_argument("--archive-store", default=str(DEFAULT_ARCHIVE_STORE))
    args = ap.parse_args(argv)
    try:
        report = missing_input_report(archive_store=args.archive_store, contract_merged=False)
        candidate_spec(load_contract()[0], args.candidate)
        raise RunnerError("; ".join(report["gaps"]))
    except RunnerError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
