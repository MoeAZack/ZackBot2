"""Intrabar stop/target resolution from 1m bars (RES-01 R3; plan section 2a and Cowork review item 12).

When one signal-timeframe bar (4h, 15m, ...) touches both the stop and the target, the order of events is read from
the bar's 1m bars, minute by minute:
  * the first minute that touches either level decides; a minute that opens through the stop fills the stop;
  * a minute that touches both cannot be ordered without trades (none in v1): stop first, labelled, UNRESOLVED;
  * 1m coverage missing or incomplete (e.g. the 11 universe members outside the 1m union): stop first, labelled,
    UNRESOLVED. Unresolved rows count toward the 5% PARK rule.
Every resolution also carries both bounds (stop-first and target-first outcomes), so a report can show both results and
the ambiguity rate. Pure functions over `Bar` tuples (pit.Bar or anything with open_ms/open/high/low); no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass

MINUTE = 60_000
PARK_UNRESOLVED_SHARE = 0.05

ONE_LEVEL = 'BAR'                                   # only one level touched: no ambiguity
RES_1M = 'RES-1M'                                   # resolved from 1m order
SAME_MINUTE = 'RES-1M-SAME-MINUTE-STOP-FIRST'       # both inside one minute, no ordered trades: stop first
NO_1M = 'RES-NO-1M-STOP-FIRST'                      # 1m missing / incomplete: stop first
LABELS = (ONE_LEVEL, RES_1M, SAME_MINUTE, NO_1M)


class IntrabarError(ValueError):
    pass


@dataclass(frozen=True)
class Resolution:
    outcome: str            # 'stop' | 'target' | 'none'
    label: str | None       # None when nothing was touched
    ambiguous: bool         # both levels touched inside the signal bar
    unresolved: bool        # decided by the stop-first fallback, not by data
    gap: bool               # the deciding (minute) bar opened through the stop
    stop_first: str         # the conservative bound's outcome
    target_first: str       # the optimistic bound's outcome


def _touches(side: str, bar, stop: float, target: float) -> tuple[bool, bool]:
    if side == 'long':
        return bar.low <= stop, bar.high >= target
    if side == 'short':
        return bar.high >= stop, bar.low <= target
    raise IntrabarError("side must be 'long' or 'short'")


def _opens_through_stop(side: str, bar, stop: float) -> bool:
    return bar.open <= stop if side == 'long' else bar.open >= stop


def resolve(side: str, bar, interval_ms: int, stop: float, target: float, minutes) -> Resolution:
    """`bar`: the signal bar; `minutes`: its 1m bars (any order; empty or None = no 1m data)."""
    if (side == 'long' and not stop < target) or (side == 'short' and not stop > target):
        raise IntrabarError('stop and target must lie on opposite sides for the given side')
    hit_s, hit_t = _touches(side, bar, stop, target)
    gap = hit_s and _opens_through_stop(side, bar, stop)
    if not (hit_s and hit_t) or gap:
        out = 'stop' if hit_s else 'target' if hit_t else 'none'
        return Resolution(out, ONE_LEVEL if out != 'none' or gap else None, hit_s and hit_t, False, gap, out, out)
    want = list(range(bar.open_ms, bar.open_ms + interval_ms, MINUTE))
    mins = sorted(minutes or (), key=lambda m: m.open_ms)
    if [m.open_ms for m in mins] != want:
        return Resolution('stop', NO_1M, True, True, False, 'stop', 'target')
    for m in mins:
        s, t = _touches(side, m, stop, target)
        if s and _opens_through_stop(side, m, stop):
            return Resolution('stop', RES_1M, True, False, True, 'stop', 'target')
        if s and t:
            return Resolution('stop', SAME_MINUTE, True, True, False, 'stop', 'target')
        if s or t:
            return Resolution('stop' if s else 'target', RES_1M, True, False, False, 'stop', 'target')
    raise IntrabarError('1m bars do not reproduce the signal bar touches (inconsistent data)')


def summarize(resolutions, n_trades: int) -> dict:
    """Ambiguity accounting for the report (section 2a); `park` when unresolved ambiguity exceeds 5% of trades."""
    rs = [r for r in resolutions if r.label is not None]
    amb = sum(r.ambiguous for r in rs)
    unres = sum(r.unresolved for r in rs)
    share = unres / n_trades if n_trades else 0.0
    return {'trades': n_trades, 'ambiguous': amb, 'unresolved': unres,
            'ambiguity_rate': amb / n_trades if n_trades else 0.0, 'unresolved_rate': share,
            'by_label': {k: sum(r.label == k for r in rs) for k in LABELS},
            'park': share > PARK_UNRESOLVED_SHARE}
