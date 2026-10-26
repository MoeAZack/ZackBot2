"""Pure, research-only implementation of ``xsec_rs_4h.v1``.

This module deliberately has no CLI, cache discovery, network access, strategy
registry hook, or PAPER/runtime adapter.  It implements the candle-only EDGE-03
candidate frozen at contract commit 164ad652a17e55e0c29c594ceadb6b576448642c
so a later, separately authorised Stage-A runner can consume it.

Input frames use Binance kline *open* timestamps in ``t`` and columns
``t,o,h,l,c``.  A decision is made only for a completed bar whose close is
exactly 00:00 UTC; its order can fill only at the next bar's open.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Protocol

import numpy as np
import pandas as pd


RULE_ID = 'xsec_rs_4h.v1'
CONTRACT_COMMIT = '164ad652a17e55e0c29c594ceadb6b576448642c'
TF_MS = 4 * 60 * 60 * 1000
DAY_MS = 24 * 60 * 60 * 1000
LOOKBACK = 180
EMA_N = 50
ATR_N = 14
STOP_ATR = 2.0
CAP_BARS = 6
MIN_ELIGIBLE = 8
PICKS = 2
BASE_FEE = 0.0005
BASE_SLIP = 0.0002
CORE_10_ELIGIBLE_9 = (
    'BTCUSDT', 'ETHUSDT', 'ZECUSDT', 'SOLUSDT', 'XRPUSDT',
    'NEARUSDT', 'DOGEUSDT', 'UNIUSDT', 'SUIUSDT',
)


class XsecError(ValueError):
    pass


@dataclass(frozen=True)
class CostModel:
    multiplier: float = 1.0

    def __post_init__(self):
        if self.multiplier not in (1.0, 1.5):
            raise XsecError('cost multiplier must be the frozen base (1.0) or stress (1.5)')

    @property
    def fee(self) -> float:
        return BASE_FEE * self.multiplier

    @property
    def slip(self) -> float:
        return BASE_SLIP * self.multiplier


@dataclass(frozen=True)
class RankRow:
    symbol: str
    rank: int
    score: float
    close: float
    ema50: float
    atr14: float


@dataclass(frozen=True)
class Decision:
    close_ms: int
    eligible: tuple[str, ...]
    ranking: tuple[RankRow, ...]
    long: tuple[str, ...]
    short: tuple[str, ...]
    skipped: str | None = None


@dataclass(frozen=True)
class Trade:
    symbol: str
    side: str
    decision_ms: int
    entry_ms: int
    exit_ms: int
    entry_ref: float
    entry: float
    stop: float
    exit_ref: float
    exit: float
    exit_reason: str
    atr14: float
    gross_r: float
    fee_r: float
    funding_r: float
    net_r: float
    cost_multiplier: float


@dataclass(frozen=True)
class IncompleteEpisode:
    symbol: str
    side: str
    decision_ms: int
    entry_ms: int
    reason: str
    exit_ms: int | None = None
    exit_reason: str | None = None


@dataclass(frozen=True)
class RunResult:
    rule_id: str
    side: str
    cost_multiplier: float
    decisions: tuple[Decision, ...]
    trades: tuple[Trade, ...]
    incomplete: tuple[IncompleteEpisode, ...]
    blocked_by_lock: tuple[tuple[int, str], ...]
    cooldowns: tuple[tuple[int, str, int, str], ...]


@dataclass(frozen=True)
class FundingRecord:
    """One point-in-time Binance funding settlement.

    ``available_ms`` prevents a record retrieved or revised later from being
    backfilled into an earlier simulated decision.  ``mark_price`` is the
    actual settlement notional price; no candle proxy is accepted.
    """
    symbol: str
    settlement_ms: int
    available_ms: int
    rate: float
    mark_price: float

    def __post_init__(self):
        if self.symbol not in CORE_10_ELIGIBLE_9:
            raise XsecError(f'funding record has non-headline symbol {self.symbol!r}')
        if type(self.settlement_ms) is not int or type(self.available_ms) is not int:
            raise XsecError('funding timestamps must be integer milliseconds')
        if not all(math.isfinite(float(v)) for v in (self.rate, self.mark_price)) or self.mark_price <= 0:
            raise XsecError('funding rate/mark_price must be finite and mark_price positive')


class FundingInput(Protocol):
    """Explicit funding capability supplied by a later evidence runner."""

    def crossed(self, symbol: str, entry_ms: int, exit_ms: int) -> tuple[int, ...]: ...

    def record_at(self, symbol: str, settlement_ms: int) -> FundingRecord | None: ...


class FundingTape:
    """Small immutable adapter over an authoritative settlement calendar and records.

    Settlement instants are explicit rather than inferred from candles.  A
    listed settlement without a matching causal record makes the episode
    accounting-incomplete.  Entry and exit fills own half-open position time,
    so only settlements strictly inside ``(entry_ms, exit_ms)`` are crossed.
    """

    def __init__(self, settlements, records=()):
        times = tuple(sorted(int(t) for t in settlements))
        if len(times) != len(set(times)):
            raise XsecError('funding settlement calendar contains duplicates')
        self._times = times
        self._records = {}
        for record in records:
            if not isinstance(record, FundingRecord):
                raise XsecError('funding records must be FundingRecord values')
            key = (record.symbol, int(record.settlement_ms))
            if key[1] not in times:
                raise XsecError(f'funding record is outside the settlement calendar {key}')
            if key in self._records:
                raise XsecError(f'duplicate funding record {key}')
            self._records[key] = record

    def crossed(self, symbol: str, entry_ms: int, exit_ms: int) -> tuple[int, ...]:
        del symbol
        return tuple(t for t in self._times if entry_ms < t < exit_ms)

    def record_at(self, symbol: str, settlement_ms: int) -> FundingRecord | None:
        return self._records.get((symbol, int(settlement_ms)))


def _ms(values) -> np.ndarray:
    a = np.asarray(values)
    if np.issubdtype(a.dtype, np.datetime64):
        return a.astype('datetime64[ms]').astype(np.int64)
    x = np.asarray(a, dtype=float)
    if not np.isfinite(x).all() or not np.equal(x, np.floor(x)).all():
        raise XsecError('timestamps must be finite integer milliseconds')
    return x.astype(np.int64)


def _frame(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    missing = [k for k in ('t', 'o', 'h', 'l', 'c') if k not in df]
    if missing:
        raise XsecError(f'{symbol}: missing columns {missing}')
    out = df.loc[:, ['t', 'o', 'h', 'l', 'c']].copy()
    out['t'] = _ms(out.t)
    for k in ('o', 'h', 'l', 'c'):
        out[k] = np.asarray(out[k], dtype=float)
    if out.t.duplicated().any() or not out.t.is_monotonic_increasing:
        raise XsecError(f'{symbol}: timestamps must be strictly increasing and unique')
    return out.reset_index(drop=True)


def _ema(values: np.ndarray, n: int) -> float:
    alpha, value = 2.0 / (n + 1.0), float(values[0])
    for x in values[1:]:
        value = (1.0 - alpha) * value + alpha * float(x)
    return value


def _atr(h: np.ndarray, lo: np.ndarray, c: np.ndarray, n: int) -> float:
    """Wilder EMA (adjust=False), seeded by the first true range."""
    alpha, value = 1.0 / n, None
    for i in range(len(c)):
        tr = h[i] - lo[i] if i == 0 else max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
        value = float(tr) if value is None else (1.0 - alpha) * value + alpha * float(tr)
    return float(value)


def _snapshot(df: pd.DataFrame, decision_close_ms: int):
    """Return score/EMA/ATR from the contiguous causal tail ending at the decision bar."""
    open_ms = decision_close_ms - TF_MS
    t = df.t.to_numpy(np.int64)
    j = int(np.searchsorted(t, open_ms))
    if j == len(t) or t[j] != open_ms:
        return None
    start = j
    while start and t[start] - t[start - 1] == TF_MS:
        start -= 1
    if j - start < LOOKBACK:
        return None
    tail = df.iloc[start:j + 1]
    x = tail.loc[:, ['o', 'h', 'l', 'c']].to_numpy(float)
    if not np.isfinite(x).all() or (x <= 0).any():
        return None
    c = x[:, 3]
    returns = np.diff(np.log(c[-(LOOKBACK + 1):]))
    if len(returns) != LOOKBACK:
        return None
    deviation = float(np.std(returns, ddof=1))
    score = float(math.log(c[-1] / c[-(LOOKBACK + 1)]) / deviation) if deviation > 0 else math.nan
    ema50 = _ema(c, EMA_N)
    atr14 = _atr(x[:, 1], x[:, 2], c, ATR_N)
    if not all(math.isfinite(v) for v in (score, ema50, atr14)) or atr14 <= 0:
        return None
    return score, float(c[-1]), ema50, atr14


def prepare(frames: Mapping[str, pd.DataFrame], symbols=CORE_10_ELIGIBLE_9) -> dict[str, pd.DataFrame]:
    symbols = tuple(symbols)
    if symbols != CORE_10_ELIGIBLE_9:
        raise XsecError('xsec_rs_4h.v1 requires the frozen CORE-10-ELIGIBLE-9 universe and order')
    return {s: _frame(frames[s], s) for s in symbols if s in frames}


def rank_at(frames: Mapping[str, pd.DataFrame], decision_close_ms: int) -> Decision:
    """One literal 00:00 UTC decision. Exact score ties use symbol ascending."""
    decision_close_ms = int(decision_close_ms)
    if decision_close_ms % DAY_MS:
        raise XsecError('decision close must be exactly 00:00 UTC')
    rows = []
    for symbol in CORE_10_ELIGIBLE_9:
        if symbol in frames:
            snap = _snapshot(frames[symbol], decision_close_ms)
            if snap is not None:
                rows.append((symbol, *snap))
    eligible = tuple(sorted(r[0] for r in rows))
    if len(rows) < MIN_ELIGIBLE:
        return Decision(decision_close_ms, eligible, (), (), (), f'eligible {len(rows)}/{MIN_ELIGIBLE}')
    rows.sort(key=lambda r: (-r[1], r[0]))
    ranking = tuple(RankRow(r[0], i + 1, r[1], r[2], r[3], r[4]) for i, r in enumerate(rows))
    long = tuple(r.symbol for r in ranking if r.score > 0 and r.close > r.ema50)[:PICKS]
    short = tuple(r.symbol for r in reversed(ranking) if r.score < 0 and r.close < r.ema50)[:PICKS]
    return Decision(decision_close_ms, eligible, ranking, long, short)


def decisions(frames: Mapping[str, pd.DataFrame], start_ms: int | None = None,
              end_ms: int | None = None) -> tuple[Decision, ...]:
    """All causal daily decisions in ``[start_ms, end_ms)``; non-midnight closes never decide."""
    prepared = prepare(frames)
    closes = sorted({int(t) + TF_MS for d in prepared.values() for t in d.t.to_numpy(np.int64)
                     if (int(t) + TF_MS) % DAY_MS == 0})
    return tuple(rank_at(prepared, t) for t in closes
                 if (start_ms is None or t >= int(start_ms)) and (end_ms is None or t < int(end_ms)))


def _bar_at(df: pd.DataFrame, open_ms: int):
    t = df.t.to_numpy(np.int64)
    i = int(np.searchsorted(t, open_ms))
    return None if i == len(t) or t[i] != open_ms else df.iloc[i]


def _trade(df: pd.DataFrame, row: RankRow, decision_ms: int, side: str, cost: CostModel,
           funding: FundingInput):
    sd = 1 if side == 'long' else -1
    entry_ms = decision_ms
    first = _bar_at(df, entry_ms)
    if first is None:
        return IncompleteEpisode(row.symbol, side, decision_ms, entry_ms, 'missing next-open bar')
    entry_ref = float(first.o)
    entry = entry_ref * (1.0 + sd * cost.slip)
    risk = STOP_ATR * row.atr14
    stop = entry - sd * risk
    exit_ms = entry_ms + CAP_BARS * TF_MS
    exit_ref = None
    reason = None
    for k in range(CAP_BARS):
        bar = _bar_at(df, entry_ms + k * TF_MS)
        if bar is None:
            return IncompleteEpisode(row.symbol, side, decision_ms, entry_ms, f'missing holding bar {k + 1}')
        o, h, lo = float(bar.o), float(bar.h), float(bar.l)
        if sd == 1 and o <= stop or sd == -1 and o >= stop:
            exit_ms, exit_ref, reason = int(bar.t), o, 'stop_gap'
            break
        if sd == 1 and lo <= stop or sd == -1 and h >= stop:
            exit_ms, exit_ref, reason = int(bar.t), stop, 'stop'
            break
    if exit_ref is None:
        cap = _bar_at(df, exit_ms)
        if cap is None:
            return IncompleteEpisode(row.symbol, side, decision_ms, entry_ms, 'missing time-cap open')
        exit_ref, reason = float(cap.o), 'time'
    exit_fill = exit_ref * (1.0 - sd * cost.slip)
    gross_r = sd * (exit_fill - entry) / risk
    fee_r = cost.fee * (entry + exit_fill) / risk
    funding_r = 0.0
    for settlement_ms in funding.crossed(row.symbol, entry_ms, exit_ms):
        record = funding.record_at(row.symbol, settlement_ms)
        if record is None:
            return IncompleteEpisode(row.symbol, side, decision_ms, entry_ms,
                                     f'missing funding settlement {settlement_ms}', exit_ms, reason)
        if (record.symbol != row.symbol or int(record.settlement_ms) != settlement_ms
                or int(record.available_ms) > settlement_ms):
            return IncompleteEpisode(row.symbol, side, decision_ms, entry_ms,
                                     f'non-causal funding settlement {settlement_ms}', exit_ms, reason)
        # Positive funding is paid by longs and received by shorts.  R is the
        # per-unit entry-to-initial-stop distance, so quantity cancels.
        funding_r += -sd * float(record.rate) * float(record.mark_price) / risk
    return Trade(row.symbol, side, decision_ms, entry_ms, exit_ms, entry_ref, entry, stop, exit_ref, exit_fill,
                 reason, row.atr14, gross_r, fee_r, funding_r, gross_r - fee_r + funding_r, cost.multiplier)


def run_side(frames: Mapping[str, pd.DataFrame], side: str, *, cost_multiplier: float = 1.0,
             funding: FundingInput | None = None, start_ms: int | None = None,
             end_ms: int | None = None) -> RunResult:
    """Evaluate one side in isolation.  The other side has no shared position or lock state."""
    if side not in ('long', 'short'):
        raise XsecError("side must be 'long' or 'short'")
    if funding is None:
        raise XsecError('an explicit point-in-time funding input is required')
    prepared = prepare(frames)
    ds = decisions(prepared, start_ms, end_ms)
    cost = CostModel(float(cost_multiplier))
    locks: dict[str, int] = {}
    trades, incomplete, blocked, cooldowns = [], [], [], []
    for d in ds:
        if d.skipped:
            continue
        by_symbol = {r.symbol: r for r in d.ranking}
        for symbol in getattr(d, side):
            entry_ms = d.close_ms
            if entry_ms < locks.get(symbol, -1):
                blocked.append((entry_ms, symbol))
                continue
            episode = _trade(prepared[symbol], by_symbol[symbol], d.close_ms, side, cost, funding)
            if isinstance(episode, IncompleteEpisode):
                incomplete.append(episode)
                if episode.exit_ms is not None:
                    until = episode.exit_ms + TF_MS
                    locks[symbol] = until
                    cooldowns.append((episode.exit_ms, symbol, until, episode.exit_reason or 'unknown'))
                continue
            trades.append(episode)
            # Every close, including stop and gap-stop closes, starts the
            # same-side one-complete-bar re-entry lock.
            until = episode.exit_ms + TF_MS
            locks[symbol] = until
            cooldowns.append((episode.exit_ms, symbol, until, episode.exit_reason))
    return RunResult(RULE_ID, side, cost.multiplier, ds, tuple(trades), tuple(incomplete), tuple(blocked),
                     tuple(cooldowns))


def run_cost_pair(frames: Mapping[str, pd.DataFrame], side: str, *, start_ms: int | None = None,
                  end_ms: int | None = None, funding: FundingInput | None = None) -> dict[str, RunResult]:
    """Mechanically paired frozen base and 1.5x fee/slippage stress runs."""
    return {
        'base': run_side(frames, side, cost_multiplier=1.0, funding=funding, start_ms=start_ms, end_ms=end_ms),
        'stress_1_5x': run_side(frames, side, cost_multiplier=1.5, funding=funding,
                               start_ms=start_ms, end_ms=end_ms),
    }
