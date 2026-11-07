"""C268 pure signal/ranker, not an executable book or source capability.

Native prices require an explicit source-owned binary64 close projection.
No loader, receipt, claim, cash, fill, risk or execution interface exists here.
"""
from dataclasses import dataclass
import math
from typing import Mapping, Sequence

HOUR_MS = 3_600_000
RETURNS = 168
MIN_DOWNSIDE = 48
MIN_FITS = 16
INDEXED_PROPOSALS = 3
EXECUTION_AUTHORIZED = False


class SignalIncomplete(ValueError):
    """Malformed/noncausal history is not a benign eligibility skip."""


class UndefinedFit(ValueError):
    """Valid history whose frozen statistical moments are undefined."""


@dataclass(frozen=True)
class HourlyClose:
    open_ms: int
    close_ms: int
    close: float


@dataclass(frozen=True)
class PairedReturn:
    open_ms: int
    btc: float
    alt: float


@dataclass(frozen=True)
class DownsideFit:
    symbol: str
    entry_ms: int
    downside_count: int
    beta_all: float
    beta_down: float
    score: float

    @property
    def positive_eligible(self):
        return self.beta_down > 0.0 and self.score > 0.0


@dataclass(frozen=True)
class IndexedSignal:
    index: int
    fit: DownsideFit


@dataclass(frozen=True)
class RankedSignals:
    entry_ms: int
    statistically_fitted_count: int
    positive_eligible_count: int
    indexed: tuple[IndexedSignal, ...]
    undefined_symbols: tuple[str, ...]
    status: str
    execution_authorized: bool = False


def _window(entry_ms, window_start_ms, window_end_ms):
    if (any(type(t) is not int or t % HOUR_MS for t in
            (entry_ms, window_start_ms, window_end_ms))
            or not window_start_ms <= entry_ms - (RETURNS + 1) * HOUR_MS
            or not entry_ms < window_end_ms):
        raise SignalIncomplete('C268_EXACT_RESET_WINDOW_AND_CAUSAL_WARMUP_REQUIRED')


def decision_clock(entry_ms):
    """True only at canonical UTC00/06/12/18 opens, regardless of local DST."""
    return type(entry_ms) is int and entry_ms % (6 * HOUR_MS) == 0


def paired_returns(btc_closes: Sequence[HourlyClose], alt_closes: Sequence[HourlyClose], *,
                   entry_ms, window_start_ms, window_end_ms):
    _window(entry_ms, window_start_ms, window_end_ms)
    expected = tuple(range(entry_ms - (RETURNS + 1) * HOUR_MS, entry_ms, HOUR_MS))
    tapes = tuple(btc_closes), tuple(alt_closes)
    for tape in tapes:
        if len(tape) != RETURNS + 1:
            raise SignalIncomplete('C268_EXACT_169_CLOSES_REQUIRED')
        for point, timestamp in zip(tape, expected):
            if (type(point) is not HourlyClose or type(point.open_ms) is not int
                    or point.open_ms != timestamp or type(point.close_ms) is not int
                    or point.close_ms != timestamp + HOUR_MS - 1
                    or type(point.close) is not float or not math.isfinite(point.close)
                    or point.close <= 0.0):
                raise SignalIncomplete('C268_EXACT_CONTIGUOUS_NATIVE_CLOSE_COORDINATES_REQUIRED')
    result = []
    for index in range(1, RETURNS + 1):
        values = []
        for tape in tapes:
            ratio = tape[index].close / tape[index - 1].close
            if not math.isfinite(ratio) or ratio <= 0:
                raise SignalIncomplete('C268_NONFINITE_LOG_RETURN')
            value = math.log(ratio)
            if not math.isfinite(value):
                raise SignalIncomplete('C268_NONFINITE_LOG_RETURN')
            values.append(value)
        result.append(PairedReturn(expected[index], *values))
    return tuple(result)


def fit_paired_returns(symbol, rows: Sequence[PairedReturn], *, entry_ms,
                       window_start_ms, window_end_ms):
    """Typed-return math seam only; this does not attest to source closes."""
    _window(entry_ms, window_start_ms, window_end_ms)
    if type(symbol) is not str or not symbol or symbol == 'BTCUSDT':
        raise SignalIncomplete('C268_ALT_SYMBOL_REQUIRED')
    rows = tuple(rows)
    expected = tuple(range(entry_ms - RETURNS * HOUR_MS, entry_ms, HOUR_MS))
    if (len(rows) != RETURNS or any(type(r) is not PairedReturn or type(r.open_ms) is not int
            or r.open_ms != t or any(type(v) is not float or not math.isfinite(v)
                for v in (r.btc, r.alt)) for r, t in zip(rows, expected))):
        raise SignalIncomplete('C268_EXACT_168_FINITE_PAIRED_RETURNS_REQUIRED')
    x, y = tuple(r.btc for r in rows), tuple(r.alt for r in rows)
    try:
        mx, my = math.fsum(x) / RETURNS, math.fsum(y) / RETURNS
        all_xx = math.fsum((v - mx) ** 2 for v in x)
        all_xy = math.fsum((a - mx) * (b - my) for a, b in zip(x, y))
        if not all(math.isfinite(v) for v in (mx, my, all_xx, all_xy)):
            raise SignalIncomplete('C268_NONFINITE_FULL_MOMENTS')
        if all_xx <= 0:
            raise UndefinedFit('C268_NONPOSITIVE_FULL_VARIANCE')
        beta_all = all_xy / all_xx
        downside = tuple((a, b) for a, b in zip(x, y) if a < mx)
        n = len(downside)
        if n < MIN_DOWNSIDE:
            raise UndefinedFit('C268_FEWER_THAN_48_DOWNSIDE_OBSERVATIONS')
        mdx = math.fsum(a for a, _ in downside) / n
        mdy = math.fsum(b for _, b in downside) / n
        down_xx = math.fsum((a - mdx) ** 2 for a, _ in downside)
        down_xy = math.fsum((a - mdx) * (b - mdy) for a, b in downside)
        if not all(math.isfinite(v) for v in (mdx, mdy, down_xx, down_xy)):
            raise SignalIncomplete('C268_NONFINITE_DOWNSIDE_MOMENTS')
        if down_xx <= 0:
            raise UndefinedFit('C268_NONPOSITIVE_DOWNSIDE_VARIANCE')
        beta_down = down_xy / down_xx
        score = beta_down - beta_all
    except (OverflowError, ZeroDivisionError) as exc:
        raise SignalIncomplete('C268_NONFINITE_ORDERED_MOMENT_ARITHMETIC') from exc
    if not all(math.isfinite(v) for v in (beta_all, beta_down, score)):
        raise SignalIncomplete('C268_NONFINITE_FIT')
    return DownsideFit(symbol, entry_ms, n, beta_all, beta_down, score)


def rank_signals(*, entry_ms, window_start_ms, window_end_ms, btc_closes,
                 pit_filter_members: Sequence[str], alt_closes: Mapping[str, Sequence[HourlyClose]]):
    """Freeze original indexed proposals; occupancy/feasibility never backfills.

    A future owning book may skip these indices, but this API supplies neither
    a replacement list nor an entry authorization. Integrity failures propagate.
    """
    _window(entry_ms, window_start_ms, window_end_ms)
    if not decision_clock(entry_ms):
        raise SignalIncomplete('C268_UTC_SIX_HOUR_OPEN_REQUIRED')
    members = tuple(pit_filter_members)
    if (any(type(s) is not str or not s for s in members) or len(set(members)) != len(members)):
        raise SignalIncomplete('C268_EXACT_UNIQUE_PIT_FILTER_MEMBERS_REQUIRED')
    fitted, undefined = [], []
    for symbol in members:
        if symbol == 'BTCUSDT':
            continue
        if symbol not in alt_closes:
            raise SignalIncomplete('C268_MISSING_MEMBER_SOURCE_IS_NOT_AN_ELIGIBILITY_SKIP')
        returns = paired_returns(btc_closes, alt_closes[symbol], entry_ms=entry_ms,
            window_start_ms=window_start_ms, window_end_ms=window_end_ms)
        try:
            fitted.append(fit_paired_returns(symbol, returns, entry_ms=entry_ms,
                window_start_ms=window_start_ms, window_end_ms=window_end_ms))
        except UndefinedFit:
            undefined.append(symbol)
    eligible = sorted((f for f in fitted if f.positive_eligible), key=lambda f: (-f.score, f.symbol))
    covered = len(fitted) >= MIN_FITS
    indexed = tuple(IndexedSignal(i + 1, fit) for i, fit in enumerate(eligible[:INDEXED_PROPOSALS])) if covered else ()
    return RankedSignals(entry_ms, len(fitted), len(eligible), indexed, tuple(undefined),
        'INDEXED_SIGNALS_ONLY' if covered else 'INSUFFICIENT_STATISTICALLY_DEFINED_FITS')
