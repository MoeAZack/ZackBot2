"""Frozen C260 calendar-block inference; pure statistics, never evidence authority.

No source, identity, holdout, or venue access exists in this module. Callers must
separately authenticate and reconcile the full causal cashflow ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

import numpy as np

from tools.research import c260_low_idio_variance as C

REPLICATES = 10_000
BLOCK_DAYS = 7
NUMPY_VERSION = "2.5.3"


@dataclass(frozen=True)
class DailyPairs:
    fold: str
    # Complete chronological calendar, including every zero-trade day.
    net_r: tuple[float, ...]
    closed_count: tuple[int, ...]


def _day_ms(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def _validate(pairs: DailyPairs, windows: dict) -> None:
    if type(pairs) is not DailyPairs or pairs.fold not in windows or pairs.fold == "sealed_holdout":
        raise C.C260Locked("C260_FROZEN_STAGE_A_DAILY_PAIRS_REQUIRED")
    start, end = map(_day_ms, windows[pairs.fold])
    days = (end - start) // C.DAY_MS
    if (type(pairs.net_r) is not tuple or type(pairs.closed_count) is not tuple
            or len(pairs.net_r) != days or len(pairs.closed_count) != days
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in pairs.net_r)
            or any(type(value) is not int or value < 0 for value in pairs.closed_count)
            or any(count == 0 and value != 0 for value, count in zip(pairs.net_r, pairs.closed_count))):
        raise ValueError("C260_COMPLETE_FINITE_CALENDAR_PAIRS_REQUIRED")


def _distribution(pairs: tuple[DailyPairs, ...], *, seed: int, center: float) -> tuple[float, ...] | None:
    rng = np.random.Generator(np.random.PCG64(seed))
    # Preconsume the specified (replicate, block) C-order array for each fold,
    # in fold order; zero-count draws are never replaced or dropped.
    samples = []
    for fold in pairs:
        n = len(fold.net_r)
        starts = rng.integers(0, n, size=(REPLICATES, math.ceil(n / BLOCK_DAYS)), dtype=np.int64)
        values = tuple(value - center * count for value, count in zip(fold.net_r, fold.closed_count))
        samples.append((fold, values, starts))
    result = []
    undefined = False
    for replicate in range(REPLICATES):
        terms, count = [], 0
        for fold, values, starts in samples:
            n = len(values)
            indices = tuple((int(start) + offset) % n for start in starts[replicate]
                            for offset in range(BLOCK_DAYS))[:n]
            terms.extend(values[index] for index in indices)
            count += sum(fold.closed_count[index] for index in indices)
        if count == 0:
            undefined = True
        else:
            result.append(math.fsum(terms) / count)
    return None if undefined else tuple(result)


def inference(pairs: tuple[DailyPairs, ...], *, scope: str) -> dict:
    """Use one exact preregistered scope and fixed seeds, never caller tuning."""
    proposal = C.load_proposal_metadata()
    if np.__version__ != NUMPY_VERSION:
        raise C.C260Locked("C260_FROZEN_NUMPY_VERSION_REQUIRED")
    expected = {"development": ("development",), "aggregate_forward": ("wf1", "wf2", "wf3"),
                **{f"{name}_diagnostic": (name,) for name in ("wf1", "wf2", "wf3")}}
    if (type(pairs) is not tuple or scope not in expected
            or any(type(item) is not DailyPairs for item in pairs)
            or tuple(item.fold for item in pairs) != expected[scope]):
        raise C.C260Locked("C260_EXACT_INFERENCE_SCOPE_REQUIRED")
    for item in pairs:
        _validate(item, proposal["windows_half_open_utc"])
    count = sum(sum(item.closed_count) for item in pairs)
    mean = math.fsum(value for item in pairs for value in item.net_r) / count if count else None
    seeds = proposal["bootstrap_exact"]["seeds"]
    # Even if one distribution has an undefined replicate, consume the entire
    # separately seeded null distribution as specified. No selective rerun.
    ci = _distribution(pairs, seed=seeds[f"{scope}_ci"], center=0.) if count else None
    null = _distribution(pairs, seed=seeds[f"{scope}_null"], center=mean) if count else None
    defined = ci is not None and null is not None
    lo, hi = map(float, np.quantile(ci, [.025, .975], method="linear")) if defined else (None, None)
    p = (1 + sum(value >= mean for value in null)) / (REPLICATES + 1) if defined else None
    gated = scope in ("development", "aggregate_forward")
    return {"format": "zb-c260-calendar-block-inference/1", "scope": scope,
            "closed": count, "mean_net_R": mean, "ci_low": lo, "ci_high": hi, "p_value": p,
            "defined": defined, "inference_gate_applicable": gated,
            "inference_gate_pass": bool(defined and gated and mean > 0 and lo > 0 and p <= .05 / 260),
            "numbered_family_screen_only": True, "global_FWER_claimed": False,
            "promotable": False, "paper_qualified": False}


def drawdown_tier(net_r: float, drawdown_r: float, stress_net_r: float, stress_drawdown_r: float) -> dict:
    """Literal frozen C260 tiers. Positive economics remains a separate gate."""
    values = (net_r, drawdown_r, stress_net_r, stress_drawdown_r)
    if (any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
            or drawdown_r < 0 or stress_drawdown_r < 0):
        raise ValueError("C260_FINITE_NONNEGATIVE_DRAWDOWNS_REQUIRED")
    tier, passed, multiplier = "reject", False, 0.
    if drawdown_r <= 15:
        tier, passed, multiplier = "standard", True, 1.
    elif drawdown_r <= 30 and net_r / drawdown_r >= 1.5:
        tier, passed, multiplier = "elevated", True, min(1., 15 / drawdown_r)
    elif (drawdown_r <= 60 and net_r / drawdown_r >= 2.
          and stress_drawdown_r > 0 and stress_net_r / stress_drawdown_r >= 1.):
        tier, passed, multiplier = "high", True, min(.5, 15 / drawdown_r)
    return {"tier": tier, "pass": passed, "future_paper_multiplier_max": multiplier,
            "gate_drawdown_R": drawdown_r, "stress_gate_drawdown_R": stress_drawdown_r,
            "promotable": False}
