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
CONTRACT_SHA256 = "a3a16673340b6cfdb88257104ffc5a9f0e65c30b9733e9c8f0c62b87428d5572"
CONTRACT_COMMIT = "370871b60f58ff6a0c87048826ac6c640a3ab2a5"
FORMAT = "zackbot.strategy-causal-pack-01/2"
INCOMPLETE = "INCOMPLETE_CANDIDATE"
CANDIDATE_IDS = (
    "pullback_rsi_short_h1_causal_v3",
    "exp_mom_short_c27_causal_v3",
    "volume_pressure_inventory_short_1h_causal_v3",
)
SOURCE_BINDINGS = {
    "pullback_rsi_short_h1_causal_v3": ("pullback_rsi_short_h1", "sig_trend_pullback_rsi"),
    "exp_mom_short_c27_causal_v3": ("exp_mom_short_c27", "sig_exp_mom_short"),
    "volume_pressure_inventory_short_1h_causal_v3": (
        "volume_pressure_inventory_1h", "sig_volume_pressure_inventory_1h"),
}
ARCHIVE_MANIFEST_PATH = ROOT / "research_evidence" / "manifests" / "binance-um-archive-v1.json.gz"
DEFAULT_ARCHIVE_STORE = Path(r"C:\Dev\ZackBot2_data\binance_um")
UNIVERSE_PATH = ROOT / "research_evidence" / "universe" / "pit-top40-qv30d-v4.json"
FILTER_AUTHORITY_PATH = ROOT / "contracts" / "strategy_causal_pack_01" / "exchange_filter_authority.v2.json"
SOURCE_RUNNER_PATHS = {
    "pullback_rsi_short_h1_causal_v3": ROOT / "tools" / "research" / "run_pullback_rsi_short_h1_candidate.py",
    "exp_mom_short_c27_causal_v3": ROOT / "tools" / "research" / "run_exp_mom_short_c27_candidate.py",
    "volume_pressure_inventory_short_1h_causal_v3": (
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
        "STRATEGY-CAUSAL-PACK-01", 2, "FROZEN", "codex"
    ):
        raise RunnerError("contract is not the frozen Codex-signed v2 contract")
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


def contract_is_merged(repo: Path = ROOT, merge_ref: str = "origin/master") -> bool:
    """A caller flag cannot authorize data; the frozen commit must be in the protected ref."""
    try:
        return subprocess.run(
            ["git", "merge-base", "--is-ancestor", CONTRACT_COMMIT, merge_ref], cwd=repo,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        ).returncode == 0
    except OSError:
        return False


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
        verified_authority = packet.pop("_verified_authority", None)
        packet["integrity"] = {
            "contract_sha256": pf.contract_sha256, "git_head": pf.git_head,
            "candidate_id": pf.candidate_id, "evaluation_identity": pf.identity,
        }
        if verified_authority is not None:
            packet["integrity"].update(verified_authority)
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
    volume: float = 1.0

    def validate(self) -> None:
        vals = (self.open, self.high, self.low, self.close)
        if (not all(_positive(x) for x in vals) or not isinstance(self.volume, (int, float))
                or not math.isfinite(float(self.volume)) or self.volume < 0
                or self.low > min(self.open, self.close) or self.high < max(self.open, self.close)):
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
class AuthorityGap:
    start_ms: int
    end_ms: int
    reason: str = "unknown"


@dataclass(frozen=True)
class FundingLedger:
    events: tuple[FundingEvent, ...]
    checksum_verified: bool
    coverage_complete: bool
    coverage_gaps: tuple[AuthorityGap, ...] = ()


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
class SourceSignalTape:
    """Fold-local exact source signals plus causal ATR arrays."""

    times_ms: tuple[int, ...]
    membership: tuple[tuple[str, ...], ...]
    signals: dict[str, dict[str, tuple[bool, ...]]]
    decision_atr: dict[str, tuple[float, ...]]
    prior_closed_atr: dict[str, tuple[float | None, ...]]
    regime: tuple[str, ...]


@dataclass(frozen=True)
class FoldEvaluation:
    name: str
    trades: tuple[dict, ...]
    final_equity: float
    entries_halted: bool
    bar_count: int


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
    """Fill a prior-close signal/time decision at the next bar's open."""
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


def holm_adjusted_p_values(raw_by_candidate: dict[str, float]) -> dict[str, float]:
    """Exact Holm step-down diagnostics for the frozen three-candidate family."""
    if set(raw_by_candidate) != set(CANDIDATE_IDS):
        raise RunnerError("Holm diagnostics require all three frozen candidate raw p-values")
    ordered = sorted((float(value), candidate) for candidate, value in raw_by_candidate.items())
    if any(not 0 <= value <= 1 for value, _ in ordered):
        raise RunnerError("raw p-values must be within [0,1]")
    running, adjusted = 0.0, {}
    total = len(ordered)
    for rank, (value, candidate) in enumerate(ordered):
        running = max(running, min(1.0, (total - rank) * value))
        adjusted[candidate] = running
    return adjusted


PACK_FORMAT = "zackbot.strategy-causal-pack-01.assembled/2"
PACK_AUTHORITY_KEYS = (
    "manifest_digest", "pit_universe_digest", "manifest_file_sha256",
    "universe_file_sha256", "exchange_filter_authority_sha256",
)


def _strict_json_object(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as fh:
            value = json.load(
                fh, parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise RunnerError(f"invalid immutable JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RunnerError(f"immutable JSON artifact is not an object: {path}")
    return value


def pf_contract_filter_authority_sha256() -> str:
    """Return the filter-authority digest from the already byte-pinned contract."""
    value = load_contract()[0].get("data", {}).get("exchange_filter_authority_sha256")
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value.lower())):
        raise RunnerError("contract exchange-filter authority digest is invalid")
    return value


def _verify_candidate_packet(path: Path, identity_dir: Path) -> dict:
    packet = _strict_json_object(path)
    claimed = packet.get("packet_sha256")
    unsigned = dict(packet)
    unsigned.pop("packet_sha256", None)
    actual = hashlib.sha256(canonical(unsigned)).hexdigest()
    if claimed != actual:
        raise RunnerError(f"candidate packet checksum mismatch: {path}")
    integrity = packet.get("integrity")
    candidate_id = packet.get("candidate_id")
    if not isinstance(integrity, dict) or candidate_id not in CANDIDATE_IDS:
        raise RunnerError(f"candidate packet identity is invalid: {path}")
    head = integrity.get("git_head")
    if (integrity.get("contract_sha256") != CONTRACT_SHA256
            or integrity.get("candidate_id") != candidate_id
            or not isinstance(head, str) or len(head) not in (40, 64)
            or any(char not in "0123456789abcdef" for char in head.lower())):
        raise RunnerError(f"candidate packet frozen integrity is invalid: {path}")
    expected_identity = hashlib.sha256(canonical({
        "contract_sha256": CONTRACT_SHA256,
        "git_head": integrity["git_head"],
        "candidate_id": candidate_id,
    })).hexdigest()
    if integrity.get("evaluation_identity") != expected_identity:
        raise RunnerError(f"candidate evaluation identity mismatch: {path}")
    if any(not isinstance(integrity.get(key), str) or len(integrity[key]) != 64
           or any(char not in "0123456789abcdef" for char in integrity[key].lower())
           for key in PACK_AUTHORITY_KEYS):
        raise RunnerError(f"candidate authority digests are incomplete: {path}")
    expected_filter_authority = pf_contract_filter_authority_sha256()
    if integrity["exchange_filter_authority_sha256"] != expected_filter_authority:
        raise RunnerError(f"candidate exchange-filter authority changed: {path}")
    claim_path = identity_dir / f"{expected_identity}.claim.json"
    claim = _strict_json_object(claim_path)
    if claim != {"state": "COMPLETED", "identity": expected_identity,
                 "output": str(path.resolve()), "packet_sha256": claimed}:
        raise RunnerError(f"candidate identity tombstone does not seal packet: {path}")
    report = packet.get("report")
    gates = packet.get("promotion_gates")
    pending = report.get("holm_adjusted_p_value") if isinstance(report, dict) else None
    if (not isinstance(gates, dict) or gates.get("required_reports") is not False
            or packet.get("promotable") is not False
            or not isinstance(pending, dict)
            or pending.get("status") != "PENDING_FROZEN_PACK_ASSEMBLY"):
        raise RunnerError(f"candidate packet is not a sealed pre-assembly packet: {path}")
    return packet


def assemble_frozen_pack(packet_paths: Sequence[os.PathLike[str] | str],
                         output: os.PathLike[str] | str, *,
                         identity_dir: os.PathLike[str] | str) -> dict:
    """Seal exactly one packet per frozen candidate and derive Holm diagnostics.

    Candidate packets are never modified.  This assembler can complete required
    diagnostics, but deliberately cannot assert an independent-review verdict.
    """
    paths = tuple(Path(path) for path in packet_paths)
    if len(paths) != len(CANDIDATE_IDS):
        raise RunnerError("frozen pack requires exactly three candidate packets")
    out = Path(output)
    if out.exists() or Path(str(out) + ".tmp").exists():
        raise RunnerError("assembled output exists or stale staging exists; overwrite refused")
    identity_root = Path(identity_dir)
    packets = [_verify_candidate_packet(path, identity_root) for path in paths]
    by_id = {packet["candidate_id"]: packet for packet in packets}
    if len(by_id) != len(packets) or set(by_id) != set(CANDIDATE_IDS):
        raise RunnerError("pack must contain each frozen candidate exactly once")
    heads = {packet["integrity"]["git_head"] for packet in packets}
    authorities = {
        tuple(packet["integrity"][key] for key in PACK_AUTHORITY_KEYS) for packet in packets
    }
    if len(heads) != 1:
        raise RunnerError("candidate packets do not share one frozen runner head")
    if len(authorities) != 1:
        raise RunnerError("candidate packets do not share authority digests")
    contract, _ = load_contract()
    for packet in packets:
        report = packet["report"]
        missing = [key for key in contract["required_report"]
                   if key not in report or (report[key] is None and key != "holm_adjusted_p_value")]
        if missing:
            raise RunnerError(f"candidate required reports are incomplete: {missing}")
    raw = {candidate_id: float(by_id[candidate_id]["report"]["raw_p_value"])
           for candidate_id in CANDIDATE_IDS}
    adjusted = holm_adjusted_p_values(raw)
    source_hashes = {candidate_id: by_id[candidate_id]["packet_sha256"]
                     for candidate_id in CANDIDATE_IDS}
    pack_identity = hashlib.sha256(canonical({
        "format": PACK_FORMAT, "contract_sha256": CONTRACT_SHA256,
        "git_head": next(iter(heads)), "authority": next(iter(authorities)),
        "source_packet_sha256": source_hashes,
    })).hexdigest()
    claim = identity_root / f"pack-{pack_identity}.claim.json"
    try:
        _exclusive_identity_json(claim, {"state": "RESERVED", "identity": pack_identity,
                                         "output": str(out.resolve())})
    except FileExistsError as exc:
        raise RunnerError("this exact three-candidate pack assembly already exists") from exc
    try:
        assembled = []
        for candidate_id in CANDIDATE_IDS:
            candidate = json.loads(json.dumps(by_id[candidate_id]))
            source_hash = candidate.pop("packet_sha256")
            candidate["source_packet_sha256"] = source_hash
            candidate["report"]["holm_adjusted_p_value"] = adjusted[candidate_id]
            candidate["promotion_gates"]["required_reports"] = True
            candidate["promotion_gates"]["independent_review"] = False
            candidate["promotable"] = False
            candidate["promotion_status"] = (
                "AWAITING_EXTERNAL_INDEPENDENT_REVIEW" if all(
                    value for key, value in candidate["promotion_gates"].items()
                    if key != "independent_review") else "FAILED_GATES")
            assembled.append(candidate)
        authority = dict(zip(PACK_AUTHORITY_KEYS, next(iter(authorities))))
        result = {
            "format": PACK_FORMAT, "pack_identity": pack_identity,
            "contract_sha256": CONTRACT_SHA256, "git_head": next(iter(heads)),
            "authority": authority, "candidate_ids": list(CANDIDATE_IDS),
            "candidates": assembled, "independent_review": False, "promotable": False,
        }
        result["pack_sha256"] = hashlib.sha256(canonical(result)).hexdigest()
        _exclusive_json(out, result)
    except BaseException as exc:
        _finalize_claim(claim, {"state": "FAILED_CONSUMED", "identity": pack_identity,
                                "error_type": type(exc).__name__})
        raise
    _finalize_claim(claim, {"state": "COMPLETED", "identity": pack_identity,
                            "output": str(out.resolve()),
                            "pack_sha256": result["pack_sha256"]})
    return result


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
    holm = report.get("holm_adjusted_p_value")
    if isinstance(holm, dict) and holm.get("status") == "PENDING_FROZEN_PACK_ASSEMBLY":
        missing.append("holm_adjusted_p_value")
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
        if candidate_id != "volume_pressure_inventory_short_1h_causal_v3" and strategy.get("sides") != spec["side"]:
            raise RunnerError(f"{candidate_id}: source side changed")
        expected_management = dict(strategy["mgmt"])
        if candidate_id == "exp_mom_short_c27_causal_v3":
            expected_management["max_bars"] = 180
        if expected_management != spec["management"]:
            raise RunnerError(f"{candidate_id}: source management no longer matches the frozen contract")
        out[candidate_id] = {"strategy_key": strategy_key, "function": function_name,
                             "timeframe": strategy["tf"], "params": dict(strategy.get("params") or {})}
    return out


def build_fold_source_signal_tape(
    contract: dict,
    candidate_id: str,
    bars_by_symbol: dict[str, Sequence[Bar]],
    core20_by_index: Sequence[Sequence[str]],
    *,
    canonical_times_ms: Sequence[int] | None = None,
) -> SourceSignalTape:
    """Call the bound strategy on a causal grid without bridging data gaps.

    Flat symbols may be absent from part of a fold.  Each contiguous observed
    segment gets fresh indicators, while ranks at decision ``d`` include only
    the PIT members with an observed, causal 42-bar return at ``d``.  The
    original PIT cell is retained separately because it owns entry priority.
    """
    import numpy as np
    import pandas as pd
    import strategies as source

    bindings = validate_source_bindings(contract)
    spec = candidate_spec(contract, candidate_id)
    if not bars_by_symbol:
        raise IncompleteCandidate(f"{INCOMPLETE}: fold has no raw symbols")
    symbols = tuple(bars_by_symbol)
    step = {"1h": 3_600_000, "4h": 14_400_000}[spec["timeframe"]]
    observed_times = sorted({int(bar.time_ms) for rows in bars_by_symbol.values() for bar in rows})
    if canonical_times_ms is None:
        if not observed_times:
            raise IncompleteCandidate(f"{INCOMPLETE}: fold has no raw bars")
        if any((value - observed_times[0]) % step for value in observed_times):
            raise IncompleteCandidate(f"{INCOMPLETE}: symbol is not on the exact fold grid")
        first_times = tuple(range(observed_times[0], observed_times[-1] + step, step))
    else:
        first_times = tuple(int(value) for value in canonical_times_ms)
    if (not first_times or any(right - left != step for left, right in zip(first_times, first_times[1:]))):
        raise IncompleteCandidate(f"{INCOMPLETE}: invalid canonical fold grid")
    size = len(first_times)
    if len(core20_by_index) != size:
        raise IncompleteCandidate(f"{INCOMPLETE}: PIT membership does not cover every decision bar")
    grid_index = {time_ms: index for index, time_ms in enumerate(first_times)}
    for symbol, rows in bars_by_symbol.items():
        times = tuple(int(bar.time_ms) for bar in rows)
        if any(right <= left for left, right in zip(times, times[1:])) or any(t not in grid_index for t in times):
            raise IncompleteCandidate(f"{INCOMPLETE}: {symbol} has invalid sparse fold times")
        for row in rows:
            row.validate()
    membership = tuple(tuple(cell) for cell in core20_by_index)
    known = set(symbols)
    for cell in membership:
        if len(cell) != 20 or len(set(cell)) != 20 or not set(cell).issubset(known):
            raise IncompleteCandidate(f"{INCOMPLETE}: invalid or unavailable PIT CORE-20 cell")

    segments: dict[str, list[tuple[list[int], object]]] = {symbol: [] for symbol in symbols}
    ret42: dict[str, dict[int, float]] = {symbol: {} for symbol in symbols}
    for symbol, rows in bars_by_symbol.items():
        current: list[Bar] = []
        for row in rows:
            if current and row.time_ms - current[-1].time_ms != step:
                chunks = [current]
                current = []
                for chunk in chunks:
                    indices = [grid_index[bar.time_ms] for bar in chunk]
                    frame = source.indicators(pd.DataFrame({
                        "t": pd.to_datetime([bar.time_ms for bar in chunk], unit="ms"),
                        "o": [bar.open for bar in chunk], "h": [bar.high for bar in chunk],
                        "l": [bar.low for bar in chunk], "c": [bar.close for bar in chunk],
                        "v": [bar.volume for bar in chunk],
                    }))
                    segments[symbol].append((indices, frame))
            current.append(row)
        if current:
            indices = [grid_index[bar.time_ms] for bar in current]
            frame = source.indicators(pd.DataFrame({
                "t": pd.to_datetime([bar.time_ms for bar in current], unit="ms"),
                "o": [bar.open for bar in current], "h": [bar.high for bar in current],
                "l": [bar.low for bar in current], "c": [bar.close for bar in current],
                "v": [bar.volume for bar in current],
            }))
            segments[symbol].append((indices, frame))
        for indices, frame in segments[symbol]:
            for local, index in enumerate(indices):
                value = float(frame["ret42"].iloc[local])
                if math.isfinite(value):
                    ret42[symbol][index] = value

    rank7 = {symbol: np.full(size, 999.0) for symbol in symbols}
    rank7_n = np.zeros(size, dtype=float)
    breadth7 = np.full(size, np.nan)
    for index, cell in enumerate(membership):
        values = {symbol: ret42[symbol][index] for symbol in cell if index in ret42[symbol]}
        if not values:
            continue
        ranked = pd.Series(values, dtype=float).rank(axis=0, ascending=False)
        rank7_n[index] = len(values)
        breadth7[index] = sum(value > 0 for value in values.values()) / len(values)
        for symbol, value in ranked.items():
            rank7[symbol][index] = float(value)

    strategy_key = bindings[candidate_id]["strategy_key"]
    params = bindings[candidate_id]["params"]
    signals, decision_atr, prior_atr = {}, {}, {}
    for symbol in symbols:
        out = {key: np.zeros(size, dtype=bool) for key in ("le", "se", "lx", "sx")}
        atr_out = np.full(size, np.nan)
        prior_out: list[float | None] = [None] * size
        for indices, frame in segments[symbol]:
            local_ctx = {
                "btc_down": pd.Series(False, index=frame.index),
                "rank": pd.Series(rank7[symbol][indices], index=frame.index),
                "rank7": pd.Series(rank7[symbol][indices], index=frame.index),
                "rank7_n": pd.Series(rank7_n[indices], index=frame.index),
                "breadth7": pd.Series(breadth7[indices], index=frame.index),
                "n_symbols": 20,
            }
            raw = source.signals(strategy_key, frame, local_ctx, params, mask_sides=False)
            engine_atr = np.asarray(frame["atr"], dtype=float)
            chosen = np.asarray(raw.get("atr", engine_atr), dtype=float)
            for local, index in enumerate(indices):
                for key in out:
                    out[key][index] = bool(raw[key].iloc[local] if hasattr(raw[key], "iloc") else raw[key][local])
                atr_out[index] = chosen[local]
                if local > 0 and math.isfinite(engine_atr[local - 1]):
                    prior_out[index] = float(engine_atr[local - 1])
        signals[symbol] = {key: tuple(bool(value) for value in values) for key, values in out.items()}
        decision_atr[symbol] = tuple(float(value) for value in atr_out)
        prior_atr[symbol] = tuple(prior_out)
    btc = "BTCUSDT" if "BTCUSDT" in segments else symbols[0]
    if len(segments[btc]) != 1 or segments[btc][0][0] != list(range(size)):
        raise IncompleteCandidate(f"{INCOMPLETE}: BTC regime grid is incomplete")
    regime_frame = source.regime(segments[btc][0][1])
    regimes = tuple(source.regime_label(regime_frame.iloc[index]) for index in range(size))
    return SourceSignalTape(first_times, membership, signals, decision_atr, prior_atr, regimes)


def _evaluate_raw_fold(
    contract: dict,
    candidate_id: str,
    fold_name: str,
    bars_by_symbol: dict[str, Sequence[Bar]],
    core20_by_index: Sequence[Sequence[str]],
    rules: dict[str, ExchangeRule],
    funding: dict[str, FundingLedger],
    causal_marks: dict[str, dict[int, float]],
    *,
    canonical_times_ms: Sequence[int] | None = None,
) -> FoldEvaluation:
    """Internal synthetic/raw fold loop; public archive execution remains merge-gated."""
    spec = candidate_spec(contract, candidate_id)
    tape = build_fold_source_signal_tape(
        contract, candidate_id, bars_by_symbol, core20_by_index,
        canonical_times_ms=canonical_times_ms)
    n, lookback, holding = len(tape.times_ms), int(spec["max_lookback_bars"]), int(spec["max_holding_bars"])
    bars_on_grid = {
        symbol: tuple({int(bar.time_ms): bar for bar in rows}.get(time_ms) for time_ms in tape.times_ms)
        for symbol, rows in bars_by_symbol.items()
    }
    for symbol in bars_by_symbol:
        if symbol not in rules or symbol not in funding or symbol not in causal_marks:
            raise IncompleteCandidate(f"{INCOMPLETE}: missing filters/funding/marks for {symbol}")
        rules[symbol].validate()
        if not funding[symbol].checksum_verified:
            raise IncompleteCandidate(f"{INCOMPLETE}: funding checksum authority failed for {symbol}")

    book = FrozenCandidateBook(tape.membership[0])
    # CORE ordering may vary weekly, but the book priority for each cohort is
    # replaced only from the checksum-verified decision cell below.
    positions: dict[str, Position] = {}
    risks: dict[str, float] = {}
    fund_cash: dict[str, float] = {}
    entry_regime: dict[str, str] = {}
    trades: list[dict] = []
    event_cursor = {symbol: 0 for symbol in bars_by_symbol}

    def settle_event(symbol: str, event: FundingEvent) -> None:
        pos = positions.get(symbol)
        if pos is None or not (pos.entry_time_ms <= event.time_ms) or pos.qty <= 0:
            return
        if not _positive(event.mark) or not math.isfinite(event.rate):
            raise IncompleteCandidate(f"{INCOMPLETE}: funding mark/rate gap")
        signed = -pos.sign * pos.qty * float(event.mark) * float(event.rate)
        book.book_signed_funding_cash(symbol, signed)
        fund_cash[symbol] += signed

    def settle_before(time_ms: int, *, equal: bool) -> None:
        for symbol, ledger in funding.items():
            cursor = event_cursor[symbol]
            while cursor < len(ledger.events):
                event = ledger.events[cursor]
                if event.time_ms > time_ms or (event.time_ms == time_ms and not equal):
                    break
                settle_event(symbol, event)
                cursor += 1
            event_cursor[symbol] = cursor
            pos = positions.get(symbol)
            if pos is not None:
                cutoff = time_ms + (1 if equal else 0)
                if (not ledger.coverage_complete and not ledger.coverage_gaps) or any(
                        gap.start_ms < cutoff and gap.end_ms > pos.entry_time_ms
                        for gap in ledger.coverage_gaps):
                    raise IncompleteCandidate(
                        f"{INCOMPLETE}: funding coverage gap intersects exposure for {symbol}")

    def record_fills(symbol: str, fills: Sequence[Fill]) -> None:
        if not fills:
            return
        pos = positions[symbol]
        for fill in fills:
            pnl = pos.sign * (fill.reference_price - pos.entry_reference) * fill.qty
            cost = fill.fee_cash + fill.slippage_cash
            lot_qty = book.positions[symbol].qty
            if fill.qty < lot_qty - 1e-9:
                book.book_partial_exit(symbol, fill.qty, pnl, cost, rules[symbol])
            else:
                book.book_final_exit(symbol, pnl, cost)
        if pos.qty <= 0:
            accounting = account_position(pos, fund_cash[symbol])
            risk = risks[symbol]
            trades.append({
                "fold": fold_name, "symbol": symbol, "side": pos.side,
                "entry_index": pos.entry_index, "exit_index": pos.fills[-1].index,
                "entry_time_ms": pos.entry_time_ms, "exit_time_ms": pos.fills[-1].time_ms,
                "risk_cash": risk, "r": accounting["net_pnl"] / risk,
                "regime": entry_regime[symbol],
                "accounting": accounting, "fills": [fill.__dict__ for fill in pos.fills],
            })
            del positions[symbol], risks[symbol], fund_cash[symbol], entry_regime[symbol]

    previous_time = tape.times_ms[0] - 1
    for index, time_ms in enumerate(tape.times_ms):
        # Settlements strictly between opens affect equity before this bar.
        settle_before(time_ms, equal=False)
        marks = {}
        for symbol in positions:
            mark = causal_marks[symbol].get(time_ms)
            if not _positive(mark):
                raise IncompleteCandidate(f"{INCOMPLETE}: missing causal mark for {symbol} at {time_ms}")
            marks[symbol] = float(mark)
        book.advance_bar(time_ms, marks)

        # Prior-close signal/time decisions execute at this open before any
        # bar-t high/low/close can affect management or entry sizing.
        for symbol in tuple(positions):
            pos = positions[symbol]
            decision = index - 1
            signal = decision >= 0 and tape.signals[symbol]["sx"][decision]
            due = index == pos.entry_index + holding
            if signal or due:
                fill = discretionary_exit(pos, bars_on_grid[symbol], decision, rules[symbol],
                                          "signal" if signal else "time")
                record_fills(symbol, (fill,))

        if index > 0 and entry_is_eligible(index, lookback, holding, n):
            decision = index - 1
            requested = [symbol for symbol in tape.membership[decision]
                         if tape.signals[symbol]["se"][decision] and symbol not in positions]
            # Every simultaneous cohort uses that decision's exact PIT order.
            book.core20_order = tape.membership[decision]
            book._priority = {symbol: rank for rank, symbol in enumerate(book.core20_order)}

            def opener(terms: EntryTerms) -> BookAdmission | None:
                atr = tape.decision_atr[terms.symbol][decision]
                entry_bar = bars_on_grid[terms.symbol][index]
                if entry_bar is None:
                    raise IncompleteCandidate(
                        f"{INCOMPLETE}: missing tradable next open for {terms.symbol} at {time_ms}")
                pos = open_position(
                    side="short", entry_index=index, entry_time_ms=time_ms,
                    reference_open=entry_bar.open,
                    atr_entry=atr, risk_cash=terms.risk_cash,
                    leverage_notional_cap=terms.leverage_notional_cap,
                    management=spec["management"], rule=rules[terms.symbol])
                if pos is None:
                    return None
                positions[terms.symbol] = pos
                risks[terms.symbol] = terms.risk_cash
                fund_cash[terms.symbol] = 0.0
                entry_regime[terms.symbol] = tape.regime[decision]
                entry = pos.fills[0]
                return BookAdmission(
                    BookLot(terms.symbol, pos.side, pos.qty, pos.entry_reference, pos.entry_reference),
                    entry.fee_cash + entry.slippage_cash)

            book.admit_simultaneous(time_ms, requested, opener)

        # Exact-open funding is deliberately after the complete ordered entry
        # cohort: same-time exits exclude it, same-time entries include it,
        # and its cash effect can halt only later entries/bars.
        settle_before(time_ms, equal=True)

        # Intrabar protection now runs for every position that survived or was
        # admitted at this open, strictly after open exits/entries/funding.
        for symbol in tuple(positions):
            source_bar = bars_on_grid[symbol][index]
            if source_bar is None:
                raise IncompleteCandidate(
                    f"{INCOMPLETE}: open position has no tradable bar for {symbol} at {time_ms}")
            managed = Bar(source_bar.time_ms, source_bar.open, source_bar.high, source_bar.low,
                          source_bar.close, tape.prior_closed_atr[symbol][index], source_bar.volume)
            record_fills(symbol, walk_bar(positions[symbol], managed, index, spec["management"], rules[symbol]))
        previous_time = time_ms

    if positions:
        raise IncompleteCandidate(f"{INCOMPLETE}: fold ended with open positions")
    return FoldEvaluation(fold_name, tuple(trades), book.settled_cash_equity, book.entries_halted, n)


def _candidate_report(contract: dict, folds: Sequence[FoldEvaluation]) -> tuple[dict, dict, dict]:
    """Derive every report field exclusively from internally generated trades."""
    import numpy as np

    trades = [trade for fold in folds for trade in fold.trades]
    r = [float(trade["r"]) for trade in trades]
    positives = math.fsum(value for value in r if value > 0)
    negatives = abs(math.fsum(value for value in r if value < 0))
    cumulative = peak = max_dd = 0.0
    for value in r:
        cumulative += value
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    by_symbol: dict[str, float] = {}
    by_regime: dict[str, list[float]] = {}
    by_month = {month: 0 for month in _frozen_fold_months_utc(contract)}
    for trade in trades:
        by_symbol[trade["symbol"]] = by_symbol.get(trade["symbol"], 0.0) + float(trade["r"])
        by_regime.setdefault(trade["regime"], []).append(float(trade["r"]))
        month = datetime.fromtimestamp(trade["exit_time_ms"] / 1000, timezone.utc).strftime("%Y-%m")
        by_month[month] = by_month.get(month, 0) + 1
    monthly = sorted(by_month.values())
    monthly_median = (0.0 if not monthly else float(monthly[len(monthly) // 2]) if len(monthly) % 2
                      else (monthly[len(monthly) // 2 - 1] + monthly[len(monthly) // 2]) / 2)
    total_abs = math.fsum(abs(value) for value in by_symbol.values())
    concentration = max((abs(value) / total_abs for value in by_symbol.values()), default=1.0)
    n = len(r)
    mean = math.fsum(r) / n if n else 0.0
    if n > 1:
        sd = math.sqrt(math.fsum((value - mean) ** 2 for value in r) / (n - 1))
        se = sd / math.sqrt(n)
        raw_p = 0.5 * math.erfc(mean / se / math.sqrt(2)) if se > 0 else (0.0 if mean > 0 else 1.0)
        rng = np.random.default_rng(0)
        samples = np.asarray(r, dtype=float)
        boot = rng.choice(samples, size=(4000, n), replace=True).mean(axis=1)
        ci = [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))]
    else:
        raw_p, ci = 1.0, [mean, mean]
    gross_r = math.fsum(trade["accounting"]["gross_reference_price_pnl"] / trade["risk_cash"] for trade in trades)
    fee_r = math.fsum(trade["accounting"]["fee_cash"] / trade["risk_cash"] for trade in trades)
    slip_r = math.fsum(trade["accounting"]["adverse_slippage_cash"] / trade["risk_cash"] for trade in trades)
    funding_r = math.fsum(trade["accounting"]["signed_funding_cash"] / trade["risk_cash"] for trade in trades)
    exposure = (math.fsum(trade["exit_index"] - trade["entry_index"] + 1 for trade in trades)
                / max(1, 20 * sum(fold.bar_count for fold in folds)))
    report = required_report_shell(
        contract, trade_count=n, net_r=math.fsum(r), mean_r=mean,
        profit_factor=(positives / negatives if negatives else ("Infinity" if positives else 0.0)),
        win_rate=(sum(value > 0 for value in r) / n if n else 0.0), max_dd_r=max_dd,
        expectancy_r=mean, exposure_time=exposure, monthly_trade_median=monthly_median,
        symbol_concentration=concentration,
        regime_breakdown={name: {"trades": len(values), "mean_r": math.fsum(values) / len(values)}
                          for name, values in sorted(by_regime.items())},
        coin_breakdown={symbol: {"net_r": value,
                                 "trades": sum(t["symbol"] == symbol for t in trades)}
                        for symbol, value in sorted(by_symbol.items())},
        fee_drag_r=fee_r, slippage_drag_r=slip_r, funding_drag_r=funding_r,
        gross_r=gross_r, raw_p_value=raw_p,
        # Exact Holm ordering is derived by holm_adjusted_p_values only after
        # all three immutable candidate packets exist. This conservative bound
        # is explicit and diagnostic-only, never a promotion input.
        holm_adjusted_p_value={"status": "PENDING_FROZEN_PACK_ASSEMBLY",
                               "worst_rank_upper_bound": min(1.0, 3.0 * raw_p)},
        bootstrap_ci_95_mean_r=ci,
        incomplete_rows=0,
        survivorship_disclosure="weekly checksum-bound PIT CORE-20; no survivor-only reduction",
        failure_reason="none",
    )
    stress = {str(multiplier): math.fsum(
        stress_costs(trade["accounting"], multiplier) / trade["risk_cash"] for trade in trades)
              for multiplier in contract["costs"]["cost_stress_multipliers_to_report"]}
    return report, by_symbol, stress


def _frozen_fold_months_utc(contract: dict) -> tuple[str, ...]:
    """Every UTC calendar month touched by a frozen half-open fold."""
    months: set[str] = set()
    for fold in contract["folds"]:
        current = datetime.fromisoformat(fold["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(fold["end"].replace("Z", "+00:00"))
        current = datetime(current.year, current.month, 1, tzinfo=timezone.utc)
        while current < end:
            months.add(current.strftime("%Y-%m"))
            current = (datetime(current.year + 1, 1, 1, tzinfo=timezone.utc)
                       if current.month == 12 else
                       datetime(current.year, current.month + 1, 1, tzinfo=timezone.utc))
    return tuple(sorted(months))


def evaluate_raw_candidate(
    adapter,
    pf: Preflight,
    *,
    merge_guard: Callable[[], bool] = contract_is_merged,
) -> dict:
    """Load and evaluate the exact three-fold candidate from a verified adapter.

    The explicit merge signal is checked before the first archive-row method is
    called. The CLI does not currently supply it.
    """
    if not merge_guard():
        raise RunnerError(
            "frozen contract commit is not merged into origin/master; archive evaluation refused")
    from bisect import bisect_right
    from tools.research.strategy_causal_pack_01_archive import (
        RawArchiveAdapter,
        load_fold_filter_authorities,
    )
    if not isinstance(adapter, RawArchiveAdapter):
        raise RunnerError("evaluate_raw_candidate requires the checksum-verifying RawArchiveAdapter")
    contract = pf.contract
    if contract.get("runner_integrity", {}).get("record_manifest_digest") is not True:
        raise RunnerError("contract no longer requires manifest digest recording")
    authority = adapter.verify_repository_authorities(ARCHIVE_MANIFEST_PATH, UNIVERSE_PATH)
    filter_authorities = load_fold_filter_authorities(
        FILTER_AUTHORITY_PATH,
        contract["data"]["exchange_filter_authority_sha256"],
        ROOT,
        contract,
    )
    spec = candidate_spec(contract, pf.candidate_id)
    validate_book_contract(contract)
    bindings = validate_source_bindings(contract)
    interval = spec["timeframe"]
    folds = []
    for fold in contract["folds"]:
        start = int(datetime.fromisoformat(fold["start"].replace("Z", "+00:00")).timestamp() * 1000)
        end = int(datetime.fromisoformat(fold["end"].replace("Z", "+00:00")).timestamp() * 1000)
        fold_authority = filter_authorities[fold["name"]]
        eligible = fold_authority.eligible_symbols
        symbols = adapter.core20_union(start, end, eligible)
        rules = {symbol: ExchangeRule(**fold_authority.rules[symbol]) for symbol in symbols}
        sparse_raw = {
            symbol: adapter.load_klines_sparse(
                symbol, interval, start, end, eligible_symbols=eligible)
            for symbol in symbols
        }
        bars = {symbol: tuple(Bar(row.open_ms, row.open, row.high, row.low, row.close,
                                  volume=row.volume) for row in load.rows)
                for symbol, load in sparse_raw.items()}
        step = {"1h": 3_600_000, "4h": 14_400_000}[interval]
        times = tuple(range(start, end, step))
        membership = [adapter.core20_at(time_ms, eligible) for time_ms in times]
        ledgers, marks_at_bar = {}, {}
        for symbol in symbols:
            mark_load = adapter.load_mark_closes_sparse(
                symbol, start, end, eligible_symbols=eligible)
            mt, mp = [row[0] for row in mark_load.rows], [row[1] for row in mark_load.rows]
            marks_at_bar[symbol] = {}
            for time_ms in times:
                at = bisect_right(mt, time_ms) - 1
                if at >= 0 and time_ms - mt[at] <= 3_600_000:
                    marks_at_bar[symbol][time_ms] = mp[at]
            events = []
            funding_load = adapter.load_funding_sparse(
                symbol, start, end, eligible_symbols=eligible)
            for event in funding_load.rows:
                at = bisect_right(mt, event.time_ms) - 1
                mark = (mp[at] if at >= 0 and event.time_ms - mt[at] <= 3_600_000
                        else float("nan"))
                events.append(FundingEvent(event.time_ms, event.rate, mark))
            gaps = tuple(AuthorityGap(gap.start_ms, gap.end_ms, gap.reason)
                         for gap in funding_load.gaps)
            ledgers[symbol] = FundingLedger(tuple(events), True, not gaps, gaps)
        folds.append(_evaluate_raw_fold(contract, pf.candidate_id, fold["name"], bars,
                                        membership, rules, ledgers, marks_at_bar,
                                        canonical_times_ms=times))
    report, contributions, stress = _candidate_report(contract, folds)
    fold_net = [math.fsum(trade["r"] for trade in fold.trades) for fold in folds]
    gates = promotion_gates(report, contract, fold_net_r=fold_net,
                            symbol_contributions=contributions,
                            stress_1_5_net_r=stress["1.5"], causal_parity=True,
                            independent_review=False)
    return {
        "_verified_authority": {**authority.as_dict(),
                                "exchange_filter_authority_sha256":
                                    contract["data"]["exchange_filter_authority_sha256"],
                                "fold_filter_snapshots": {
                                    name: {
                                        "sha256": item.snapshot_sha256,
                                        "captured_at": item.captured_at,
                                    }
                                    for name, item in filter_authorities.items()
                                }},
        "candidate_id": pf.candidate_id, "source_binding": bindings[pf.candidate_id],
        "folds": [{"name": fold.name, "trades": list(fold.trades),
                   "net_r": fold_net[index], "final_equity": fold.final_equity}
                  for index, fold in enumerate(folds)],
        "report": report, "cost_stress_net_r": stress, "promotion_gates": gates,
        "promotable": False,
        "promotion_status": "AWAITING_EXTERNAL_INDEPENDENT_REVIEW" if all(
            value for key, value in gates.items() if key != "independent_review") else "FAILED_GATES",
    }


def missing_input_report(*, archive_store: os.PathLike[str] | str | None = None,
                         merge_guard: Callable[[], bool] = contract_is_merged) -> dict:
    """Read no market bytes; name the exact blockers to a real raw evaluation."""
    contract, contract_digest = load_contract()
    validate_book_contract(contract)
    bindings = validate_source_bindings(contract)
    gaps = []
    for label, path in (("archive manifest", ARCHIVE_MANIFEST_PATH),
                        ("PIT universe", UNIVERSE_PATH),
                        ("exchange-filter authority", FILTER_AUTHORITY_PATH)):
        if not path.is_file():
            gaps.append(f"missing {label}: {path}")
    for candidate_id, path in SOURCE_RUNNER_PATHS.items():
        if not path.is_file():
            gaps.append(f"{candidate_id}: missing source runner {path}")
    if not merge_guard():
        gaps.append("frozen contract commit is not contained by origin/master; market-data reads remain forbidden")
    if archive_store is None:
        gaps.append("missing external binance_um archive store path for manifest klines/fundingRate/markPriceKlines")
    elif not Path(archive_store).is_dir():
        gaps.append(f"binance_um archive store does not exist: {Path(archive_store)}")
    return {
        "format": "zackbot.strategy-causal-pack-01-readiness/2", "ready": False,
        "contract_sha256": contract_digest, "source_bindings": bindings,
        "required_sources": {"manifest": str(ARCHIVE_MANIFEST_PATH), "universe": str(UNIVERSE_PATH),
                             "exchange_filter_authority": str(FILTER_AUTHORITY_PATH),
                             "archive_store": str(Path(archive_store) if archive_store is not None else DEFAULT_ARCHIVE_STORE),
                             "source_runners": {k: str(v) for k, v in SOURCE_RUNNER_PATHS.items()}},
        "folds": contract["folds"], "cutoff": contract["data"]["forbid_read_at_or_after"],
        "required_raw_series": ["klines:1h", "klines:4h", "fundingRate", "markPriceKlines:1h"],
        "invariants": ["causal fold-start filters + filtered CORE-20 PIT membership",
                       "fold-local warmup/purge", "next-open fills",
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
        "prepared fills/trades are untrusted and forbidden; only the merge-gated checksum-bound raw adapter may evaluate"
    )


def _run_raw_candidate(candidate_id: str, output: os.PathLike[str] | str,
                       adapter_loader: Callable[[], object], *, identity_dir=None,
                       merge_guard: Callable[[], bool] = contract_is_merged,
                       head_fn=git_head, dirty_fn=git_dirty) -> dict:
    """Private immutable-output bridge; reservation precedes any adapter/archive load."""
    if not merge_guard():
        raise RunnerError(
            "frozen contract commit is not merged into origin/master; adapter load refused")
    return _run_with_loader(
        candidate_id, output, adapter_loader,
        lambda adapter, pf: evaluate_raw_candidate(adapter, pf, merge_guard=merge_guard),
        identity_dir=identity_dir, head_fn=head_fn, dirty_fn=dirty_fn)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Frozen STRATEGY-CAUSAL-PACK-01 raw runner")
    ap.add_argument("--candidate", required=True, choices=CANDIDATE_IDS)
    ap.add_argument("--out", required=True)
    ap.add_argument("--identity-dir", required=True)
    ap.add_argument("--archive-store", default=str(DEFAULT_ARCHIVE_STORE))
    args = ap.parse_args(argv)
    try:
        if not contract_is_merged():
            raise RunnerError(
                "frozen contract commit is not contained by origin/master; no archive row was read")
        from tools.research.strategy_causal_pack_01_archive import (
            RawArchiveAdapter,
            load_authorities,
        )

        def load_adapter():
            manifest, pit = load_authorities(ARCHIVE_MANIFEST_PATH, UNIVERSE_PATH)
            return RawArchiveAdapter(manifest, pit, args.archive_store)

        packet = _run_raw_candidate(
            args.candidate, args.out, load_adapter,
            identity_dir=args.identity_dir)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    report = packet["report"]
    print(
        f"{args.candidate}: {report['trade_count']} closes, "
        f"{report['net_r']:.6f}R, PF {report['profit_factor']} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
