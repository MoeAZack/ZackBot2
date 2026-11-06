"""Causal, fail-closed runner for RBR-4H short causal v3.

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
CONTRACT_PATH = (ROOT / "contracts" / "rank_breadth_reversal_4h_short_causal_v3" /
                 "rank_breadth_reversal_4h_short_causal_v3_contract.v1.json")
CONTRACT_SHA256 = "4df3132f9d12d03bc8f87e5811030fc2e39fb053ad8ed9c129dc9d07d2a5a1f6"
CONTRACT_COMMIT = "246ae1e3b305caf5abcc4e015c17c61f3247705c"
FORMAT = "zackbot.rank-breadth-reversal-4h-short-causal-v3/1"
INCOMPLETE = "INCOMPLETE_CANDIDATE"
CANDIDATE_IDS = ("rank_breadth_reversal_4h_short_causal_v3",)
SOURCE_BINDINGS = {
    "rank_breadth_reversal_4h_short_causal_v3": (
        "rank_breadth_reversal_4h", "sig_rank_breadth_reversal_4h"),
}
ARCHIVE_MANIFEST_PATH = ROOT / "research_evidence" / "manifests" / "binance-um-archive-v1.json.gz"
DEFAULT_ARCHIVE_STORE = Path(r"C:\Dev\ZackBot2_data\binance_um")
UNIVERSE_PATH = ROOT / "research_evidence" / "universe" / "pit-top40-qv30d-v4.json"
CLASSES_PATH = ROOT / "research_evidence" / "universe" / "instrument-classes-v1.json"
FAMILY_LEDGER_PATH = (ROOT / "contracts" / "rank_breadth_reversal_4h_short_causal_v3" /
                      "rank_breadth_reversal_4h_family.v1.json")
FILTER_AUTHORITY_PATH = ROOT / "contracts" / "strategy_causal_pack_01" / "exchange_filter_authority.v2.json"


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
        "RANK-BREADTH-REVERSAL-4H-SHORT-CAUSAL-V3", 2, "FROZEN", "codex"
    ):
        raise RunnerError("contract is not the frozen Codex-signed v2 contract")
    if (contract.get("candidate") or {}).get("candidate_id") != CANDIDATE_IDS[0]:
        raise RunnerError("frozen candidate identities changed")
    return contract, digest


def candidate_spec(contract: dict, candidate_id: str) -> dict:
    if candidate_id not in CANDIDATE_IDS:
        raise RunnerError(f"candidate must be one of {', '.join(CANDIDATE_IDS)}")
    return contract["candidate"]


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
        "candidate_book_isolated": True,
    }
    if any(book.get(key) != value for key, value in expected.items()):
        raise RunnerError("frozen candidate-book controls changed")
    if not str(book.get("daily_halt_timezone", "")).startswith("Africa/Cairo"):
        raise RunnerError("daily halt timezone is not frozen to Africa/Cairo")
    if not str(book.get("entry_priority", "")).startswith("the checksum-bound PIT CORE-20"):
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
    identity_root = (Path(identity_dir) if identity_dir is not None else
                     Path(output).parent / ".rank_breadth_reversal_4h_short_causal_v3_ids")
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
    cashflow_max_dd_r: float = 0.0
    marked_equity_max_dd_cash: float = 0.0
    marked_equity_max_dd_pct: float = 0.0


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

    def update_marks(self, marks: dict[str, float]) -> None:
        """Mark every open lot for disclosure without rolling the Cairo-day baseline."""
        if set(marks) != set(self.positions):
            raise IncompleteCandidate(f"{INCOMPLETE}: end-phase marks must cover open positions exactly")
        for symbol, mark in marks.items():
            if not _positive(mark):
                raise IncompleteCandidate(f"{INCOMPLETE}: invalid causal mark for {symbol}")
            self.positions[symbol].mark = float(mark)

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
                  risk_cash: float, leverage_notional_cap: float, management: dict, rule: ExchangeRule,
                  cost_multiplier: float = 1.0) -> Position | None:
    """Size from adverse executable entry and frozen MARKET_LOT_SIZE; never upsize or clip maxQty."""
    if side not in ("long", "short") or not (_positive(atr_entry) and _positive(risk_cash)):
        raise IncompleteCandidate(f"{INCOMPLETE}: invalid side, risk, or causal entry ATR")
    rule.validate()
    action = "buy" if side == "long" else "sell"
    entry_exec = executable_price(reference_open, action, rule.tick, 2.0 * cost_multiplier)
    r_price = float(management["stop_atr"]) * atr_entry
    raw = risk_cash / r_price
    capped = min(raw, leverage_notional_cap / entry_exec)
    qty = _floor_step(capped, rule.step)
    if qty < rule.min_qty or qty * entry_exec < rule.min_notional:
        return None
    if rule.max_qty is not None and qty > rule.max_qty + 1e-12:
        raise RunnerError("entry exceeds MARKET_LOT_SIZE maxQty; clipping is forbidden")
    sign = 1 if side == "long" else -1
    entry_fill = make_fill(entry_index, entry_time_ms, action, qty, reference_open, rule, "entry",
                           fee_bps=5.0 * cost_multiplier,
                           slippage_bps=2.0 * cost_multiplier)
    if entry_fill.executable_price != entry_exec:
        raise RunnerError("entry executable-price calculation diverged")
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
                reason: str, cost_multiplier: float = 1.0) -> Fill:
    action = "sell" if pos.side == "long" else "buy"
    return make_fill(index, bar.time_ms, action, qty, reference, rule, reason,
                     fee_bps=5.0 * cost_multiplier,
                     slippage_bps=2.0 * cost_multiplier)


def walk_bar(pos: Position, bar: Bar, index: int, management: dict, rule: ExchangeRule,
             cost_multiplier: float = 1.0) -> list[Fill]:
    """Apply the encoded FBL path, including partial/BE/trail timing."""
    if pos.qty <= 0:
        return []
    bar.validate()
    sign, frozen_stop = pos.sign, pos.stop
    out: list[Fill] = []
    gap = bar.open <= frozen_stop if sign == 1 else bar.open >= frozen_stop
    stop_inside = bar.low <= frozen_stop <= bar.high
    if gap:
        fill = _close_fill(pos, bar, index, rule, pos.qty, bar.open, "gap_stop", cost_multiplier)
        pos.qty = 0.0; pos.fills.append(fill); return [fill]
    if stop_inside and _favourable_inside(pos, bar):
        fill = _close_fill(pos, bar, index, rule, pos.qty, frozen_stop, "stop_wins", cost_multiplier)
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
            fill = _close_fill(pos, bar, index, rule, close_qty, level, "tp1", cost_multiplier)
            pos.qty = _floor_step(pos.qty - close_qty, rule.step)
            pos.tp1_done = True
        else:
            fill = _close_fill(pos, bar, index, rule, pos.qty, level, "target", cost_multiplier)
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
                fill = _close_fill(pos, bar, index, rule, close_qty, level, "tp1", cost_multiplier)
                pos.qty = _floor_step(pos.qty - close_qty, rule.step)
                pos.tp1_done = True
                pos.fills.append(fill); out.append(fill)
            else:
                fill = _close_fill(pos, bar, index, rule, pos.qty, level, kind, cost_multiplier)
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
                       reason: str, cost_multiplier: float = 1.0) -> Fill:
    """Fill a prior-close signal/time decision at the next bar's open."""
    index, reference, time_ms = next_open(bars, decision_index)
    if pos.qty <= 0:
        raise RunnerError("cannot discretionarily exit an already closed position")
    bar = bars[index]
    fill = _close_fill(pos, bar, index, rule, pos.qty, reference, reason, cost_multiplier)
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
    """Exact Holm step-down diagnostics; ties sort by trial identity."""
    if not raw_by_candidate:
        raise RunnerError("Holm diagnostics require a nonempty frozen family")
    ordered = sorted((float(value), candidate) for candidate, value in raw_by_candidate.items())
    if any(not 0 <= value <= 1 for value, _ in ordered):
        raise RunnerError("raw p-values must be within [0,1]")
    running, adjusted = 0.0, {}
    total = len(ordered)
    for rank, (value, candidate) in enumerate(ordered):
        running = max(running, min(1.0, (total - rank) * value))
        adjusted[candidate] = running
    return adjusted


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
    regime_count_min = min((int(x["trades"]) for x in regime.values()), default=0)
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
        "minimum_regime_closes": regime_count_min >= g["minimum_closes_per_regime"],
        "symbol_concentration": concentration <= g["max_symbol_absolute_contribution_fraction"],
        "positive_after_1_5x": stress_1_5_net_r > 0,
        "causal_parity": bool(causal_parity), "independent_review": bool(independent_review),
        "required_reports": not missing,
        "zero_incomplete_rows": int(report.get("incomplete_rows") or 0) == 0,
    }


def validate_source_bindings(contract: dict) -> dict:
    """Bind the only executable cell to the exact source function and short side."""
    import strategies as source

    out = {}
    for candidate_id, (strategy_key, function_name) in SOURCE_BINDINGS.items():
        spec = candidate_spec(contract, candidate_id)
        strategy = source.STRATEGIES.get(strategy_key)
        if not strategy or strategy.get("fn") is not getattr(source, function_name, None):
            raise RunnerError(f"{candidate_id}: missing exact source function strategies.py:{function_name}")
        if strategy.get("tf") != spec["timeframe"] or not strategy.get("research_only"):
            raise RunnerError(f"{candidate_id}: source timeframe/research-only identity changed")
        if strategy.get("sides") != "both" or strategy.get("reentry_lock") is not True:
            raise RunnerError(f"{candidate_id}: source short-cell/reentry-lock identity changed")
        if dict(strategy.get("params") or {}) != spec["signal_parameters"]:
            raise RunnerError(f"{candidate_id}: source signal parameters changed")
        if dict(strategy.get("mgmt") or {}) != spec["management"]:
            raise RunnerError(f"{candidate_id}: source management no longer matches the frozen contract")
        out[candidate_id] = {"strategy_key": strategy_key, "function": function_name,
                             "timeframe": strategy["tf"], "side": "short",
                             "reentry_lock": True, "params": dict(strategy.get("params") or {})}
    return out


def build_fold_source_signal_tape(
    contract: dict,
    candidate_id: str,
    bars_by_symbol: dict[str, Sequence[Bar]],
    core20_by_index: Sequence[Sequence[str]],
) -> SourceSignalTape:
    """Call the exact bound strategy on one complete fold with weekly PIT context.

    Indicators are computed once from fold-local bars only. Cross-sectional
    context is rebuilt through ``strategies.build_context`` for each distinct
    ordered PIT CORE-20 membership cell, so a symbol outside that week's set
    cannot affect exp-momentum ranks.
    """
    import numpy as np
    import pandas as pd
    import strategies as source

    bindings = validate_source_bindings(contract)
    spec = candidate_spec(contract, candidate_id)
    if not bars_by_symbol:
        raise IncompleteCandidate(f"{INCOMPLETE}: fold has no raw symbols")
    symbols = tuple(bars_by_symbol)
    if "BTCUSDT" not in symbols:
        raise IncompleteCandidate(f"{INCOMPLETE}: causal BTC EMA200 regime source is missing")
    lengths = {len(rows) for rows in bars_by_symbol.values()}
    if len(lengths) != 1 or not lengths or 0 in lengths:
        raise IncompleteCandidate(f"{INCOMPLETE}: fold symbol bar counts differ")
    size = lengths.pop()
    if len(core20_by_index) != size:
        raise IncompleteCandidate(f"{INCOMPLETE}: PIT membership does not cover every decision bar")
    first_times = tuple(int(bar.time_ms) for bar in bars_by_symbol[symbols[0]])
    if any(right <= left for left, right in zip(first_times, first_times[1:])):
        raise IncompleteCandidate(f"{INCOMPLETE}: fold bars are not strictly increasing")
    for symbol, rows in bars_by_symbol.items():
        if tuple(int(bar.time_ms) for bar in rows) != first_times:
            raise IncompleteCandidate(f"{INCOMPLETE}: {symbol} is not on the exact fold grid")
        for row in rows:
            row.validate()
    membership = tuple(tuple(cell) for cell in core20_by_index)
    known = set(symbols)
    for cell in membership:
        if len(cell) != 20 or len(set(cell)) != 20 or not set(cell).issubset(known):
            raise IncompleteCandidate(f"{INCOMPLETE}: invalid or unavailable PIT CORE-20 cell")

    frames = {}
    for symbol, rows in bars_by_symbol.items():
        frames[symbol] = source.indicators(pd.DataFrame({
            "t": pd.to_datetime([bar.time_ms for bar in rows], unit="ms"),
            "o": [bar.open for bar in rows], "h": [bar.high for bar in rows],
            "l": [bar.low for bar in rows], "c": [bar.close for bar in rows],
            "v": [bar.volume for bar in rows],
        }))

    # Base BTC regime/context, then exact PIT-cell rank fields are stitched in.
    btc = "BTCUSDT"
    base = source.build_context(frames, btc_key=btc)
    ctx = {symbol: {key: np.asarray(value).copy() for key, value in base[symbol].items()}
           for symbol in symbols}
    cells: dict[tuple[str, ...], list[int]] = {}
    for index, cell in enumerate(membership):
        cells.setdefault(cell, []).append(index)
    for cell, indices in cells.items():
        cell_frames = {symbol: frames[symbol] for symbol in cell}
        cell_btc = "BTCUSDT" if "BTCUSDT" in cell_frames else cell[0]
        local = source.build_context(cell_frames, btc_key=cell_btc)
        exemplar = local[cell[0]]
        # A symbol outside the point-in-time cell receives no union-derived
        # rank.  The source's 999 sentinel makes an existing short leave on
        # the source rank exit, while entry admissions remain cell-only.
        for symbol in symbols:
            ctx[symbol]["rank"][indices] = 999.0
            ctx[symbol]["rank7"][indices] = 999.0
            ctx[symbol]["rank7_n"][indices] = np.asarray(
                exemplar["rank7_n"])[indices]
            ctx[symbol]["breadth7"][indices] = np.asarray(
                exemplar["breadth7"])[indices]
            ctx[symbol]["n_symbols"] = 20
        for symbol in cell:
            for key in ("rank", "rank7", "rank7_n", "breadth7"):
                ctx[symbol][key][indices] = np.asarray(local[symbol][key])[indices]

    strategy_key = bindings[candidate_id]["strategy_key"]
    params = bindings[candidate_id]["params"]
    signals, decision_atr, prior_atr = {}, {}, {}
    for symbol in symbols:
        raw = source.signals(strategy_key, frames[symbol], ctx[symbol], params, mask_sides=False)
        signals[symbol] = {key: tuple(bool(value) for value in raw[key])
                           for key in ("le", "se", "lx", "sx")}
        engine_atr = np.asarray(frames[symbol]["atr"], dtype=float)
        chosen = np.asarray(raw.get("atr", engine_atr), dtype=float)
        decision_atr[symbol] = tuple(float(value) for value in chosen)
        prior_atr[symbol] = tuple(None if index == 0 or not math.isfinite(engine_atr[index - 1])
                                  else float(engine_atr[index - 1]) for index in range(size))
    btc_close = np.asarray(frames[btc]["c"], dtype=float)
    btc_ema200 = np.asarray(frames[btc]["e200"], dtype=float)
    regimes = tuple(
        "btc_above_or_on_ema200" if math.isfinite(btc_ema200[index])
        and btc_close[index] >= btc_ema200[index] else "btc_below_ema200"
        for index in range(size)
    )
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
    cost_multiplier: float = 1.0,
) -> FoldEvaluation:
    """Internal synthetic/raw fold loop; public archive execution remains merge-gated."""
    spec = candidate_spec(contract, candidate_id)
    tape = build_fold_source_signal_tape(contract, candidate_id, bars_by_symbol, core20_by_index)
    n, lookback, holding = len(tape.times_ms), int(spec["max_lookback_bars"]), int(spec["max_holding_bars"])
    for symbol in bars_by_symbol:
        if symbol not in rules or symbol not in funding or symbol not in causal_marks:
            raise IncompleteCandidate(f"{INCOMPLETE}: missing filters/funding/marks for {symbol}")
        rules[symbol].validate()
        if not funding[symbol].checksum_verified or not funding[symbol].coverage_complete:
            raise IncompleteCandidate(f"{INCOMPLETE}: funding authority incomplete for {symbol}")

    book = FrozenCandidateBook(tape.membership[0])
    # CORE ordering may vary weekly, but the book priority for each cohort is
    # replaced only from the checksum-verified decision cell below.
    positions: dict[str, Position] = {}
    risks: dict[str, float] = {}
    requested_risks: dict[str, float] = {}
    fund_cash: dict[str, float] = {}
    normalized_cashflows: dict[str, list[tuple[int, float]]] = {}
    entry_regime: dict[str, str] = {}
    trades: list[dict] = []
    event_cursor = {symbol: 0 for symbol in bars_by_symbol}
    reentry_locks: dict[str, int] = {}
    marked_curve: list[tuple[int, float]] = []

    def settle_event(symbol: str, event: FundingEvent) -> None:
        pos = positions.get(symbol)
        if pos is None or not (pos.entry_time_ms <= event.time_ms) or pos.qty <= 0:
            return
        if not _positive(event.mark) or not math.isfinite(event.rate):
            raise IncompleteCandidate(f"{INCOMPLETE}: funding mark/rate gap")
        signed = -pos.sign * pos.qty * float(event.mark) * float(event.rate)
        if signed < 0:
            signed *= cost_multiplier
        book.book_signed_funding_cash(symbol, signed)
        fund_cash[symbol] += signed
        normalized_cashflows[symbol].append((event.time_ms, signed / risks[symbol]))
        marked_curve.append((event.time_ms, book.marked_equity()))

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
            normalized_cashflows[symbol].append((fill.time_ms, (pnl - cost) / risks[symbol]))
            marked_curve.append((fill.time_ms, book.marked_equity()))
        if pos.qty <= 0:
            accounting = account_position(pos, fund_cash[symbol])
            risk = risks[symbol]
            last = pos.fills[-1]
            if last.reason in ("signal", "time"):
                associated_index = last.index - 1
            elif last.reason in ("gap_stop", "stop_wins", "stop", "target"):
                associated_index = last.index
            else:
                raise IncompleteCandidate(f"{INCOMPLETE}: malformed final exit reason for reentry lock")
            if not (0 <= associated_index < len(tape.times_ms)):
                raise IncompleteCandidate(f"{INCOMPLETE}: malformed associated reentry-lock candle")
            associated_ms = int(tape.times_ms[associated_index])
            reentry_locks[symbol] = associated_ms
            trades.append({
                "fold": fold_name, "symbol": symbol, "side": pos.side,
                "entry_index": pos.entry_index, "exit_index": pos.fills[-1].index,
                "entry_time_ms": pos.entry_time_ms, "exit_time_ms": pos.fills[-1].time_ms,
                "risk_cash": risk, "r": accounting["net_pnl"] / risk,
                "requested_risk_budget": requested_risks[symbol],
                "regime": entry_regime[symbol],
                "exit_reason": last.reason,
                "associated_exit_decision_open_ms": associated_ms,
                "normalized_cashflows": list(normalized_cashflows[symbol]),
                "accounting": accounting, "fills": [fill.__dict__ for fill in pos.fills],
            })
            del (positions[symbol], risks[symbol], requested_risks[symbol], fund_cash[symbol],
                 normalized_cashflows[symbol], entry_regime[symbol])

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
        marked_curve.append((time_ms, book.marked_equity()))

        # Prior-close signal/time decisions execute at this open before any
        # bar-t high/low/close can affect management or entry sizing.
        for symbol in tuple(positions):
            pos = positions[symbol]
            decision = index - 1
            signal = decision >= 0 and tape.signals[symbol]["sx"][decision]
            due = index == pos.entry_index + holding
            if signal or due:
                fill = discretionary_exit(pos, bars_by_symbol[symbol], decision, rules[symbol],
                                          "signal" if signal else "time", cost_multiplier)
                record_fills(symbol, (fill,))

        if index > 0 and entry_is_eligible(index, lookback, holding, n):
            decision = index - 1
            requested = [symbol for symbol in tape.membership[decision]
                         if tape.signals[symbol]["se"][decision] and symbol not in positions
                         and int(tape.times_ms[decision]) > reentry_locks.get(symbol, -1)]
            # Every simultaneous cohort uses that decision's exact PIT order.
            book.core20_order = tape.membership[decision]
            book._priority = {symbol: rank for rank, symbol in enumerate(book.core20_order)}

            def opener(terms: EntryTerms) -> BookAdmission | None:
                atr = tape.decision_atr[terms.symbol][decision]
                pos = open_position(
                    side="short", entry_index=index, entry_time_ms=time_ms,
                    reference_open=bars_by_symbol[terms.symbol][index].open,
                    atr_entry=atr, risk_cash=terms.risk_cash,
                    leverage_notional_cap=terms.leverage_notional_cap,
                    management=spec["management"], rule=rules[terms.symbol],
                    cost_multiplier=cost_multiplier)
                if pos is None:
                    return None
                positions[terms.symbol] = pos
                actual_initial_risk = pos.initial_qty * pos.r_price
                if not _positive(actual_initial_risk):
                    raise IncompleteCandidate(f"{INCOMPLETE}: invalid actual initial stop risk")
                risks[terms.symbol] = actual_initial_risk
                requested_risks[terms.symbol] = terms.risk_cash
                fund_cash[terms.symbol] = 0.0
                entry = pos.fills[0]
                normalized_cashflows[terms.symbol] = [(
                    time_ms, -(entry.fee_cash + entry.slippage_cash) / actual_initial_risk)]
                entry_regime[terms.symbol] = tape.regime[decision]
                return BookAdmission(
                    BookLot(terms.symbol, pos.side, pos.qty, pos.entry_reference, pos.entry_reference),
                    entry.fee_cash + entry.slippage_cash)

            book.admit_simultaneous(time_ms, requested, opener)
            marked_curve.append((time_ms, book.marked_equity()))

        # Exact-open funding is deliberately after the complete ordered entry
        # cohort: same-time exits exclude it, same-time entries include it,
        # and its cash effect can halt only later entries/bars.
        settle_before(time_ms, equal=True)

        # Intrabar protection now runs for every position that survived or was
        # admitted at this open, strictly after open exits/entries/funding.
        for symbol in tuple(positions):
            source_bar = bars_by_symbol[symbol][index]
            managed = Bar(source_bar.time_ms, source_bar.open, source_bar.high, source_bar.low,
                          source_bar.close, tape.prior_closed_atr[symbol][index], source_bar.volume)
            record_fills(symbol, walk_bar(positions[symbol], managed, index, spec["management"],
                                          rules[symbol], cost_multiplier))
        end_marks = {}
        for symbol in positions:
            end_time = time_ms + 4 * 3_600_000
            mark = causal_marks[symbol].get(end_time)
            if not _positive(mark):
                raise IncompleteCandidate(
                    f"{INCOMPLETE}: missing causal end-of-bar mark for {symbol} at {end_time}")
            end_marks[symbol] = float(mark)
        book.update_marks(end_marks)
        marked_curve.append((time_ms + 4 * 3_600_000, book.marked_equity()))
        previous_time = time_ms

    if positions:
        raise IncompleteCandidate(f"{INCOMPLETE}: fold ended with open positions")
    by_time: dict[int, list[float]] = {}
    for trade in trades:
        for event_time, value in trade["normalized_cashflows"]:
            by_time.setdefault(int(event_time), []).append(float(value))
    cumulative = peak = cashflow_dd = 0.0
    for event_time in sorted(by_time):
        cumulative += math.fsum(by_time[event_time])
        peak = max(peak, cumulative)
        cashflow_dd = max(cashflow_dd, peak - cumulative)
    marked_by_time: dict[int, float] = {}
    for event_time, equity in marked_curve:
        marked_by_time[int(event_time)] = float(equity)
    marked_peak = FrozenCandidateBook.START_EQUITY
    marked_dd_cash = marked_dd_pct = 0.0
    for event_time in sorted(marked_by_time):
        equity = marked_by_time[event_time]
        marked_peak = max(marked_peak, equity)
        dd_cash = marked_peak - equity
        marked_dd_cash = max(marked_dd_cash, dd_cash)
        if marked_peak > 0:
            marked_dd_pct = max(marked_dd_pct, dd_cash / marked_peak)
    return FoldEvaluation(fold_name, tuple(trades), book.settled_cash_equity, book.entries_halted, n,
                          cashflow_dd, marked_dd_cash, marked_dd_pct)


def _load_family_ledger() -> dict:
    verify_file_digest(FAMILY_LEDGER_PATH, "d3cdbd3ca36871004eb999ca5b8c73683fc1bb1f23407e128a63b30e299ae835")
    ledger = _strict_json_object(FAMILY_LEDGER_PATH)
    claimed = ledger.get("digest")
    core = {key: value for key, value in ledger.items() if key != "digest"}
    if hashlib.sha256(canonical(core)).hexdigest() != claimed or claimed != (
        "79a3013abb8a8b16d11b23e56e1cdcb6b9376bf6a93baed96a54bc836694a3dd"):
        raise RunnerError("frozen RBR family ledger digest changed")
    if ledger.get("holm_family_size") != 3 or len(ledger.get("trials", ())) != 3:
        raise RunnerError("frozen RBR family ledger size changed")
    return ledger


def _cluster_diagnostics(trades: Sequence[dict]) -> tuple[float, list[float | None]]:
    import numpy as np

    by_day: dict[str, list[float]] = {}
    for trade in trades:
        day = datetime.fromtimestamp(
            int(trade["exit_time_ms"]) / 1000, timezone.utc).strftime("%Y-%m-%d")
        by_day.setdefault(day, []).append(float(trade["r"]))
    if not by_day:
        return 1.0, [None, None]
    clusters = [tuple(by_day[day]) for day in sorted(by_day)]
    day_net = np.asarray([math.fsum(values) for values in clusters], dtype=float)
    observed = math.fsum(float(value) for value in day_net)
    raw_cfg = load_contract()[0]["statistics"]["raw_p_value"]
    rng = np.random.Generator(np.random.PCG64(int(raw_cfg["seed"])))
    count = 0
    remaining = int(raw_cfg["resamples"])
    while remaining:
        take = min(5000, remaining)
        signs = rng.integers(0, 2, size=(take, len(day_net)), dtype=np.int8) * 2 - 1
        count += int(np.count_nonzero(signs @ day_net >= observed))
        remaining -= take
    raw_p = (1 + count) / (int(raw_cfg["resamples"]) + 1)

    boot_cfg = load_contract()[0]["statistics"]["bootstrap_ci_95_mean_r"]
    rng = np.random.Generator(np.random.PCG64(int(boot_cfg["seed"])))
    means = np.empty(int(boot_cfg["resamples"]), dtype=float)
    for index in range(len(means)):
        sampled = rng.integers(0, len(clusters), size=len(clusters))
        values = [value for cluster_index in sampled for value in clusters[int(cluster_index)]]
        means[index] = math.fsum(values) / len(values)
    return raw_p, [
        float(np.quantile(means, 0.025, method="linear")),
        float(np.quantile(means, 0.975, method="linear")),
    ]


def _candidate_report(
    contract: dict,
    folds: Sequence[FoldEvaluation],
    stress_folds: dict[str, Sequence[FoldEvaluation]],
) -> tuple[dict, dict, dict]:
    """Derive required reports from base and independent full-book stress replays."""
    trades = [trade for fold in folds for trade in fold.trades]
    r = [float(trade["r"]) for trade in trades]
    positives = math.fsum(value for value in r if value > 0)
    negatives = abs(math.fsum(value for value in r if value < 0))
    by_symbol: dict[str, float] = {}
    by_regime: dict[str, list[float]] = {
        "btc_above_or_on_ema200": [], "btc_below_ema200": []}
    by_month = {month: 0 for month in _frozen_fold_months_utc(contract)}
    for trade in trades:
        by_symbol[trade["symbol"]] = math.fsum(
            (by_symbol.get(trade["symbol"], 0.0), float(trade["r"])))
        by_regime.setdefault(trade["regime"], []).append(float(trade["r"]))
        month = datetime.fromtimestamp(
            trade["exit_time_ms"] / 1000, timezone.utc).strftime("%Y-%m")
        if month not in by_month:
            raise RunnerError("trade close lies outside frozen fold months")
        by_month[month] += 1
    monthly = sorted(by_month.values())
    monthly_median = (
        0.0 if not monthly else float(monthly[len(monthly) // 2])
        if len(monthly) % 2 else
        (monthly[len(monthly) // 2 - 1] + monthly[len(monthly) // 2]) / 2)
    total_abs = math.fsum(abs(value) for value in by_symbol.values())
    concentration = max((abs(value) / total_abs for value in by_symbol.values()), default=1.0)
    n = len(r)
    mean = math.fsum(r) / n if n else 0.0
    raw_p, ci = _cluster_diagnostics(trades)
    ledger = _load_family_ledger()
    raw_family = {}
    for trial in ledger["trials"]:
        raw_family[trial["trial_id"]] = (
            raw_p if trial["trial_id"] == CANDIDATE_IDS[0] else 1.0)
    holm = holm_adjusted_p_values(raw_family)
    full_ledger = []
    for trial in ledger["trials"]:
        row = dict(trial)
        if row["trial_id"] == CANDIDATE_IDS[0]:
            row.update(status="EVALUATED", closed_trades=n, net_r=math.fsum(r),
                       profit_factor=(positives / negatives if negatives else
                                      ("Infinity" if positives else 0.0)),
                       raw_p_value=raw_p)
        row["holm_adjusted_p_value"] = holm[row["trial_id"]]
        full_ledger.append(row)
    gross_r = math.fsum(
        trade["accounting"]["gross_reference_price_pnl"] / trade["risk_cash"]
        for trade in trades)
    fee_r = math.fsum(
        trade["accounting"]["fee_cash"] / trade["risk_cash"] for trade in trades)
    slip_r = math.fsum(
        trade["accounting"]["adverse_slippage_cash"] / trade["risk_cash"]
        for trade in trades)
    funding_r = math.fsum(
        trade["accounting"]["signed_funding_cash"] / trade["risk_cash"]
        for trade in trades)
    exposure = (
        math.fsum(trade["exit_index"] - trade["entry_index"] + 1 for trade in trades)
        / max(1, 20 * sum(fold.bar_count for fold in folds)))
    cost_stress = {}
    for multiplier, replay_folds in stress_folds.items():
        replay_trades = [trade for fold in replay_folds for trade in fold.trades]
        replay_r = [float(trade["r"]) for trade in replay_trades]
        cost_stress[multiplier] = {
            "trade_count": len(replay_r),
            "net_r": math.fsum(replay_r),
            "mean_r": math.fsum(replay_r) / len(replay_r) if replay_r else 0.0,
            "fold_net_r": {
                fold.name: math.fsum(float(trade["r"]) for trade in fold.trades)
                for fold in replay_folds
            },
            "final_equity": {fold.name: fold.final_equity for fold in replay_folds},
        }
    report = required_report_shell(
        contract,
        trade_count=n,
        net_r=math.fsum(r),
        mean_r=mean,
        profit_factor=(positives / negatives if negatives else
                       ("Infinity" if positives else 0.0)),
        win_rate=(sum(value > 0 for value in r) / n if n else 0.0),
        max_dd_r=max((fold.cashflow_max_dd_r for fold in folds), default=0.0),
        expectancy_r=mean,
        exposure_time=exposure,
        utc_monthly_close_counts_including_zeros=by_month,
        monthly_trade_median=monthly_median,
        symbol_concentration=concentration,
        regime_breakdown={
            name: {"trades": len(values),
                   "mean_r": math.fsum(values) / len(values) if values else 0.0}
            for name, values in sorted(by_regime.items())
        },
        coin_breakdown={
            symbol: {"net_r": value,
                     "trades": sum(trade["symbol"] == symbol for trade in trades)}
            for symbol, value in sorted(by_symbol.items())
        },
        fee_drag_r=fee_r,
        slippage_drag_r=slip_r,
        funding_drag_r=funding_r,
        gross_r=gross_r,
        fold_cashflow_max_dd_r={
            fold.name: fold.cashflow_max_dd_r for fold in folds},
        fold_marked_equity_max_dd_cash={
            fold.name: fold.marked_equity_max_dd_cash for fold in folds},
        fold_marked_equity_max_dd_pct={
            fold.name: fold.marked_equity_max_dd_pct for fold in folds},
        cost_stress_full_replay=cost_stress,
        raw_p_value=raw_p,
        holm_adjusted_p_value=holm[CANDIDATE_IDS[0]],
        bootstrap_ci_95_mean_r=ci,
        incomplete_rows=0,
        survivorship_disclosure=(
            "checksum-bound weekly PIT CORE-20; manifest survivor_only=false; "
            "no current/static/union membership substitution"),
        full_candidate_ledger=full_ledger,
        failure_reason=([] if n else ["NO_CLOSED_EPISODES"]),
    )
    return report, by_symbol, cost_stress

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
    """Evaluate three independent cost books, but only after protected-ref merge."""
    if not merge_guard():
        raise RunnerError(
            "frozen contract commit is not merged into origin/master; archive evaluation refused")
    from bisect import bisect_right
    from tools.research.rank_breadth_reversal_4h_short_causal_v3_archive import RawArchiveAdapter
    from tools.research.strategy_causal_pack_01_archive import load_fold_filter_authorities
    if not isinstance(adapter, RawArchiveAdapter):
        raise RunnerError(
            "evaluate_raw_candidate requires the checksum-verifying RBR RawArchiveAdapter")
    contract = pf.contract
    if contract.get("runner_integrity", {}).get("record_manifest_digest") is not True:
        raise RunnerError("contract no longer requires manifest digest recording")
    validate_book_contract(contract)
    bindings = validate_source_bindings(contract)
    _load_family_ledger()

    # Every authority and every filter needed by any fold is bound before the
    # first candle/funding/mark archive row is opened.
    authority = adapter.verify_repository_authorities(
        ARCHIVE_MANIFEST_PATH, UNIVERSE_PATH, CLASSES_PATH)
    filter_authorities = load_fold_filter_authorities(
        FILTER_AUTHORITY_PATH,
        contract["pit_authority"]["exchange_filter_authority_sha256"],
        ROOT,
        {"data": {
            "exchange_filter_authority_sha256":
                contract["pit_authority"]["exchange_filter_authority_sha256"],
            "pit_universe_path": contract["pit_authority"]["universe_path"],
            "pit_universe_sha256": contract["pit_authority"]["universe_byte_sha256"],
            "pit_universe_digest": contract["pit_authority"]["universe_content_digest"],
        }, "folds": contract["folds"]},
    )
    fold_bounds = []
    for fold in contract["folds"]:
        start = int(datetime.fromisoformat(
            fold["start"].replace("Z", "+00:00")).timestamp() * 1000)
        end = int(datetime.fromisoformat(
            fold["end"].replace("Z", "+00:00")).timestamp() * 1000)
        fold_authority = filter_authorities[fold["name"]]
        eligible = fold_authority.eligible_symbols
        symbols = adapter.core20_union(start, end, eligible)
        rules = {
            symbol: ExchangeRule(**fold_authority.rules[symbol])
            for symbol in symbols
        }
        for rule in rules.values():
            rule.validate()
        fold_bounds.append((fold, start, end, symbols, eligible, rules))

    fold_inputs = []
    for fold, start, end, symbols, eligible, rules in fold_bounds:
        raw = {
            symbol: adapter.load_klines(
                symbol, "4h", start, end, eligible_symbols=eligible)
            for symbol in symbols
        }
        bars = {
            symbol: tuple(
                Bar(row.open_ms, row.open, row.high, row.low, row.close,
                    volume=row.volume)
                for row in rows
            )
            for symbol, rows in raw.items()
        }
        times = tuple(row.time_ms for row in bars[symbols[0]])
        membership = [
            adapter.core20_at(
                min(time_ms + 4 * 3_600_000, end - 1), eligible)
            for time_ms in times
        ]
        ledgers, marks_at_bar = {}, {}
        for symbol in symbols:
            marks = adapter.load_mark_closes(
                symbol, start, end, eligible_symbols=eligible)
            mt, mp = [row[0] for row in marks], [row[1] for row in marks]
            marks_at_bar[symbol] = {}
            for time_ms in times:
                at = bisect_right(mt, time_ms) - 1
                if at < 0 or time_ms - mt[at] > 3_600_000:
                    raise IncompleteCandidate(
                        f"{INCOMPLETE}: missing causal mark for {symbol}")
                marks_at_bar[symbol][time_ms] = mp[at]
                end_time = time_ms + 4 * 3_600_000
                at_end = bisect_right(mt, end_time) - 1
                if at_end < 0 or end_time - mt[at_end] > 3_600_000:
                    raise IncompleteCandidate(
                        f"{INCOMPLETE}: missing causal end-of-bar mark for {symbol}")
                marks_at_bar[symbol][end_time] = mp[at_end]
            events = []
            for event in adapter.load_funding(
                    symbol, start, end, eligible_symbols=eligible):
                at = bisect_right(mt, event.time_ms) - 1
                if at < 0 or event.time_ms - mt[at] > 3_600_000:
                    raise IncompleteCandidate(
                        f"{INCOMPLETE}: funding mark gap for {symbol}")
                events.append(FundingEvent(event.time_ms, event.rate, mp[at]))
            ledgers[symbol] = FundingLedger(tuple(events), True, True)
        fold_inputs.append((
            fold["name"], bars, membership,
            rules,
            ledgers, marks_at_bar,
        ))

    replay_by_multiplier: dict[str, tuple[FoldEvaluation, ...]] = {}
    for multiplier in contract["costs"]["cost_stress_multipliers_to_report"]:
        replay_by_multiplier[str(multiplier)] = tuple(
            _evaluate_raw_fold(
                contract, pf.candidate_id, name, bars, membership, rules,
                ledgers, marks, cost_multiplier=float(multiplier))
            for name, bars, membership, rules, ledgers, marks in fold_inputs
        )
    folds = replay_by_multiplier["1.0"]
    report, contributions, stress = _candidate_report(
        contract, folds, replay_by_multiplier)
    fold_net = [
        math.fsum(float(trade["r"]) for trade in fold.trades) for fold in folds]
    gates = promotion_gates(
        report, contract, fold_net_r=fold_net,
        symbol_contributions=contributions,
        stress_1_5_net_r=float(stress["1.5"]["net_r"]),
        causal_parity=True, independent_review=False)
    return {
        "_verified_authority": {
            **authority.as_dict(),
            "exchange_filter_authority_sha256":
                contract["pit_authority"]["exchange_filter_authority_sha256"],
            "fold_filter_snapshots": {
                name: {"sha256": item.snapshot_sha256, "captured_at": item.captured_at}
                for name, item in filter_authorities.items()
            }},
        "candidate_id": pf.candidate_id,
        "default_enabled": False,
        "paper_testnet_only": True,
        "source_binding": bindings[pf.candidate_id],
        "folds": [
            {
                "name": fold.name,
                "trades": list(fold.trades),
                "net_r": fold_net[index],
                "final_equity": fold.final_equity,
                "cashflow_max_dd_r": fold.cashflow_max_dd_r,
                "marked_equity_max_dd_cash": fold.marked_equity_max_dd_cash,
                "marked_equity_max_dd_pct": fold.marked_equity_max_dd_pct,
            }
            for index, fold in enumerate(folds)
        ],
        "report": report,
        "promotion_gates": gates,
        "promotable": False,
        "promotion_status": (
            "AWAITING_EXTERNAL_INDEPENDENT_REVIEW"
            if all(value for key, value in gates.items()
                   if key != "independent_review")
            else "FAILED_GATES"
        ),
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
                        ("instrument classes", CLASSES_PATH),
                        ("exchange-filter authority", FILTER_AUTHORITY_PATH)):
        if not path.is_file():
            gaps.append(f"missing {label}: {path}")
    if not gaps:
        try:
            from tools.research.rank_breadth_reversal_4h_short_causal_v3_archive import load_authorities
            from tools.research.strategy_causal_pack_01_archive import load_fold_filter_authorities
            _, pit, _ = load_authorities(
                ARCHIVE_MANIFEST_PATH, UNIVERSE_PATH, CLASSES_PATH)
            del pit
            load_fold_filter_authorities(
                FILTER_AUTHORITY_PATH,
                contract["pit_authority"]["exchange_filter_authority_sha256"],
                ROOT,
                {"data": {
                    "exchange_filter_authority_sha256":
                        contract["pit_authority"]["exchange_filter_authority_sha256"],
                    "pit_universe_path": contract["pit_authority"]["universe_path"],
                    "pit_universe_sha256": contract["pit_authority"]["universe_byte_sha256"],
                    "pit_universe_digest": contract["pit_authority"]["universe_content_digest"],
                }, "folds": contract["folds"]},
            )
        except RuntimeError as exc:
            gaps.append(f"authority/filter preflight incomplete: {exc}")
    if not merge_guard():
        gaps.append("frozen contract commit is not contained by origin/master; market-data reads remain forbidden")
    if archive_store is None:
        gaps.append("missing external binance_um archive store path for manifest klines/fundingRate/markPriceKlines")
    elif not Path(archive_store).is_dir():
        gaps.append(f"binance_um archive store does not exist: {Path(archive_store)}")
    return {
        "format": "zackbot.rank-breadth-reversal-4h-short-causal-v3-readiness/1", "ready": not gaps,
        "contract_sha256": contract_digest, "source_bindings": bindings,
        "required_sources": {"manifest": str(ARCHIVE_MANIFEST_PATH), "universe": str(UNIVERSE_PATH),
                             "classes": str(CLASSES_PATH),
                             "exchange_filter_authority": str(FILTER_AUTHORITY_PATH),
                             "archive_store": str(Path(archive_store) if archive_store is not None else DEFAULT_ARCHIVE_STORE)},
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
    ap = argparse.ArgumentParser(description="Frozen RBR causal-v2 raw runner")
    ap.add_argument("--candidate", default=CANDIDATE_IDS[0], choices=CANDIDATE_IDS)
    ap.add_argument("--out", required=True)
    ap.add_argument("--identity-dir", required=True)
    ap.add_argument("--archive-store", default=str(DEFAULT_ARCHIVE_STORE))
    args = ap.parse_args(argv)
    try:
        if not contract_is_merged():
            raise RunnerError(
                "frozen contract commit is not contained by origin/master; "
                "no archive row was read")
        from tools.research.rank_breadth_reversal_4h_short_causal_v3_archive import (
            RawArchiveAdapter, load_authorities)

        def load_adapter():
            manifest, pit, _ = load_authorities(
                ARCHIVE_MANIFEST_PATH, UNIVERSE_PATH, CLASSES_PATH)
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
