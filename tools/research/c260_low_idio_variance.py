"""C260 fixed signal preparation only; no authorized historical execution path.

This consumes explicitly supplied synthetic/in-memory closed bars. It has no
loader, evidence seal, run identity, mutable strategy registry or venue path.
The accepted causal PACK execution functions remain the intended execution
dependency, not an authorization bypass for a new candidate.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Mapping

from tools.research import strategy_causal_pack_01_runner as PACK

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
RETURNS = 720
PREREG = Path(__file__).resolve().parents[2] / 'contracts/c260_low_idio_variance/c260_prereg_proposal.v1.json'
PREREG_SHA256 = '8b14aa5ebb554728462d759c35eae215220855b7933f5f069adbeb0d1a262d48'
REUSED_EXECUTION_SHA256 = 'a0242ce5def9aa3c64b18f2554616e25d242c96d037d81f0362ae07505d4537f'


class C260Locked(RuntimeError):
    pass


class SignalInputError(ValueError):
    pass


def assert_real_evaluation_authorized():
    """Always before any prospective loader/identity read; no caller override."""
    raise C260Locked('C260_SOURCE_AND_PROTECTED_REGISTRATION_UNBOUND: no loader or identity was accessed')


def load_proposal_metadata() -> dict:
    raw = PREREG.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PREREG_SHA256:
        raise C260Locked('C260_PROPOSAL_BYTE_DRIFT')
    value = json.loads(raw)
    if (value['candidate_id'] != 260 or value['execution_authorized'] is not False
            or value['source_authority_bound'] is not False or value['promotable'] is not False
            or value['book_and_execution']['full_target_r'] is not None):
        raise C260Locked('C260_PROPOSAL_LOCK_OR_IDENTITY_DRIFT')
    return value


@dataclass(frozen=True)
class VarianceScore:
    symbol: str
    residual_variance: float
    alpha: float
    beta: float


@dataclass(frozen=True)
class PreparedRanking:
    decision_ms: int
    entry_due_ms: int
    scheduled_exit_ms: int
    ranked: tuple[VarianceScore, ...]
    skips: tuple[tuple[str, str], ...]
    execution_authorized: bool = False


def _returns(bars: tuple[PACK.Bar, ...], decision_ms: int, fold_start_ms: int) -> tuple[float, ...] | None:
    if type(bars) is not tuple or any(type(bar) is not PACK.Bar for bar in bars):
        raise SignalInputError('C260_TYPED_IMMUTABLE_CLOSED_BARS_REQUIRED')
    if any(type(bar.time_ms) is not int or bar.time_ms >= decision_ms for bar in bars):
        raise SignalInputError('C260_ENTRY_OR_FUTURE_BAR_IN_SIGNAL')
    expected = tuple(range(decision_ms - (RETURNS + 1) * HOUR_MS, decision_ms, HOUR_MS))
    if (len(bars) != RETURNS + 1 or tuple(bar.time_ms for bar in bars) != expected
            or expected[0] < fold_start_ms):
        return None
    for bar in bars:
        bar.validate()
    result = tuple(math.log(right.close / left.close) for left, right in zip(bars, bars[1:]))
    if not all(math.isfinite(value) for value in result):
        raise SignalInputError('C260_NONFINITE_PAST_RETURN')
    return result


def rank_supplied_closed_cohort(*, decision_ms: int, fold_start_ms: int,
                                pit_filter_eligible_symbols: tuple[str, ...],
                                btc_bars: tuple[PACK.Bar, ...],
                                bars_by_symbol: Mapping[str, tuple[PACK.Bar, ...]]) -> PreparedRanking:
    """Prepare full ranks only; verified PIT/filter/source binding is not installed.

    The input universe is the complete externally verified PIT/filter output.
    This pure function neither authenticates that universe nor turns tapes into
    production evidence. Missing past histories are explicit signal skips.
    """
    if (type(decision_ms) is not int or decision_ms % DAY_MS or type(fold_start_ms) is not int
            or fold_start_ms % HOUR_MS or type(pit_filter_eligible_symbols) is not tuple
            or len(set(pit_filter_eligible_symbols)) != len(pit_filter_eligible_symbols)
            or any(type(symbol) is not str or not symbol for symbol in pit_filter_eligible_symbols)):
        raise SignalInputError('C260_FROZEN_DECISION_OR_UNIVERSE_SHAPE')
    symbols = tuple(symbol for symbol in pit_filter_eligible_symbols if symbol != 'BTCUSDT')
    entry = decision_ms + HOUR_MS
    def result(scores=(), skips=()):
        return PreparedRanking(decision_ms, entry, entry + 72 * HOUR_MS, tuple(scores), tuple(skips))
    x = _returns(btc_bars, decision_ms, fold_start_ms)
    if x is None:
        return result(skips=((symbol, 'FOLD_RESET_BTC_WARMUP_OR_MISSING_HISTORY') for symbol in symbols))
    mean_x = math.fsum(x) / RETURNS
    xx = math.fsum((value - mean_x) ** 2 for value in x)
    if not math.isfinite(xx) or xx <= 0:
        return result(skips=((symbol, 'BTC_VARIANCE_UNDEFINED') for symbol in symbols))
    scores, skips = [], []
    for symbol in symbols:
        y = _returns(bars_by_symbol.get(symbol, ()), decision_ms, fold_start_ms)
        if y is None:
            skips.append((symbol, 'FOLD_RESET_SYMBOL_WARMUP_OR_MISSING_HISTORY'))
            continue
        mean_y = math.fsum(y) / RETURNS
        beta = math.fsum((a - mean_x) * (b - mean_y) for a, b in zip(x, y)) / xx
        alpha = mean_y - beta * mean_x
        variance = math.fsum((b - alpha - beta * a) ** 2 for a, b in zip(x, y)) / (RETURNS - 2)
        if not all(math.isfinite(value) for value in (alpha, beta, variance)) or variance < 0:
            raise SignalInputError('C260_NONFINITE_FIT')
        scores.append(VarianceScore(symbol, variance, alpha, beta))
    return result(sorted(scores, key=lambda row: (row.residual_variance, row.symbol)), skips)


def main():
    assert_real_evaluation_authorized()


if __name__ == '__main__':
    main()
