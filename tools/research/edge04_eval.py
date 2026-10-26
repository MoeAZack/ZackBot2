"""EDGE-04 causal signal tape.

This module is intentionally stdlib-only so it can run inside ``pit.evaluate``.  It emits
signals and the next-open/funding capabilities needed by the existing PIT accounting
path, but never computes an outcome.  One sandbox process is created per split, so the
cache and the rotation transition state cannot cross a split boundary.
"""
from __future__ import annotations

import math

import trend_ema_mom as T

FORMAT = "zb-edge04-pit-tape/1"
TF = "4h"
TF_MS = 4 * 60 * 60 * 1000
# Runtime fetch cap (engine.py); the SplitPlan separately enforces the preregistered
# 240-bar purge/warm-up after every boundary.
WINDOW = 1500
HOUR = 60 * 60 * 1000
MARK_BARS = 8
HYPOTHESES = ("H1", "H2", "H3", "H4")


def _ema(values, n):
    if not values:
        return []
    alpha = 2.0 / (n + 1.0)
    out = [float(values[0])]
    for value in values[1:]:
        out.append(alpha * float(value) + (1.0 - alpha) * out[-1])
    return out


def _wilder(values, n):
    """pandas ``ewm(alpha=1/n, adjust=False)`` over an already-valid series."""
    if not values:
        return []
    alpha = 1.0 / n
    out = [float(values[0])]
    for value in values[1:]:
        out.append(alpha * float(value) + (1.0 - alpha) * out[-1])
    return out


def _rsi(closes, n=14):
    if len(closes) < 2:
        return [None] * len(closes)
    diffs = [float(closes[i]) - float(closes[i - 1]) for i in range(1, len(closes))]
    up = _wilder([max(x, 0.0) for x in diffs], n)
    down = _wilder([max(-x, 0.0) for x in diffs], n)
    out = [None]
    for u, d in zip(up, down):
        # strategies.rsi replaces a zero denominator by NaN before division.
        out.append(None if d == 0 else 100.0 - 100.0 / (1.0 + u / d))
    return out


def _atr(bars, n=14):
    if not bars:
        return []
    tr = []
    for i, bar in enumerate(bars):
        prev = bar.close if i == 0 else bars[i - 1].close
        tr.append(max(bar.high - bar.low, abs(bar.high - prev), abs(bar.low - prev)))
    return _wilder(tr, n)


def _contiguous(bars):
    return len(bars) >= 2 and all(b.open_ms - a.open_ms == TF_MS for a, b in zip(bars, bars[1:]))


def _tail(bars):
    """Contiguous closed-bar suffix; a hole restarts warm-up instead of bridging it."""
    if not bars:
        return ()
    i = len(bars) - 1
    while i and bars[i].open_ms - bars[i - 1].open_ms == TF_MS:
        i -= 1
    return tuple(bars[i:])


def _score(bars):
    """Runtime ``ret180 / (rolling-180 close-return sample SD * sqrt(180))``."""
    if len(bars) < 181 or not _contiguous(bars[-181:]):
        return None
    c = [float(x.close) for x in bars[-181:]]
    returns = [c[i] / c[i - 1] - 1.0 for i in range(1, len(c))]
    mean = math.fsum(returns) / 180.0
    var = math.fsum((x - mean) ** 2 for x in returns) / 179.0
    sd = math.sqrt(var)
    ret180 = c[-1] / c[0] - 1.0
    return None if not sd or not math.isfinite(sd) else ret180 / (sd * math.sqrt(180.0))


def _rank(scores):
    """pandas rank(ascending=False) semantics, including average ranks for ties."""
    valid = sorted(((v, s) for s, v in scores.items() if v is not None), reverse=True)
    out, i = {}, 0
    while i < len(valid):
        j = i + 1
        while j < len(valid) and valid[j][0] == valid[i][0]:
            j += 1
        rank = ((i + 1) + j) / 2.0
        for _, symbol in valid[i:j]:
            out[symbol] = rank
        i = j
    return out


def _daily_bear(bars):
    """Exact ``strategies.regime`` daily close/EMA200 rule on this frozen 240-bar tail."""
    if not bars:
        return False
    daily = []
    day = None
    for bar in bars:
        # A 4h bar becomes available at open+4h.  Its completed UTC day is keyed by
        # the instant just before availability, matching resample('1D').last().
        key = (bar.available_ms - 1) // (24 * HOUR)
        if key != day:
            daily.append(float(bar.close))
            day = key
        else:
            daily[-1] = float(bar.close)
    if len(daily) < 30:
        return False
    return daily[-1] < _ema(daily, 200)[-1]


def _signals(bars, *, rank, prev_rank, bear):
    if not bars or bars[-1].available_ms <= 0:
        return {h: {"e": False, "x": False, "atr": None} for h in HYPOTHESES}
    c = [float(x.close) for x in bars]
    atr = _atr(bars, 14)[-1]
    e20, e50, e200 = _ema(c, 20), _ema(c, 50), _ema(c, 200)
    rsi = _rsi(c, 14)
    ready = len(c) >= 181 and _contiguous(bars[-181:])
    mom = ready and c[-1] / c[-43] - 1.0 > 0 and c[-1] / c[-181] - 1.0 > 0
    cross = len(c) >= 2 and e20[-1] > e50[-1] and e20[-2] <= e50[-2]
    trend_entry = bool(ready and cross and c[-1] > e200[-1] and mom)
    trend_exit = bool(ready and (e20[-1] < e50[-1] or not mom))

    rsi_cross = (len(rsi) >= 2 and rsi[-1] is not None and rsi[-2] is not None
                 and rsi[-1] < 60.0 and rsi[-2] >= 60.0)
    h1_entry = bool(bear and c[-1] < e200[-1] and e50[-1] < e200[-1] and rsi_cross)

    in_top = rank is not None and rank <= 5 and c[-1] > e50[-1]
    prev_top = prev_rank is not None and prev_rank <= 5 and len(c) >= 2 and c[-2] > e50[-2]
    h2_entry = bool(in_top and not prev_top)
    h2_exit = bool(rank is None or rank > 8 or c[-1] < e50[-1])
    return {
        "H1": {"e": h1_entry, "raw_e": bool(c[-1] < e200[-1] and e50[-1] < e200[-1] and rsi_cross),
               "x": False, "atr": repr(2.0 * atr)},
        "H2": {"e": h2_entry, "x": h2_exit, "atr": repr(3.0 * atr), "rank": rank,
               "prev_rank": prev_rank, "above_ema50": c[-1] > e50[-1],
               "prev_above_ema50": len(c) >= 2 and c[-2] > e50[-2]},
        "H3": {"e": trend_entry, "x": trend_exit, "atr": repr(2.5 * atr)},
        "H4": {"e": trend_entry, "x": trend_exit, "atr": repr(2.5 * atr)},
    }


class Edge04Tape:
    def __init__(self):
        self._last_t = None
        self._previous = self._current = None

    def decide(self, view):
        t = view.t
        if self._last_t is None or t > self._last_t:
            base = self._current or {}
            out, state = self._step(base, view)
            self._previous, self._current, self._last_t = base, state, t
            return out
        if t == self._last_t:
            return self._step(self._previous, view)[0]
        raise ValueError("decision times must be non-decreasing")

    @staticmethod
    def _bars(view, symbol, cache):
        have = cache.get(symbol, ())
        if have:
            new = tuple(view.bars(symbol, TF, 2))
            add = tuple(b for b in new if b.open_ms > have[-1].open_ms)
            if len(add) < len(new):
                have = (have + add)[-WINDOW:]
                cache[symbol] = have
                return have
        have = tuple(view.bars(symbol, TF, WINDOW))
        cache[symbol] = have
        return have

    @staticmethod
    def _funding(view, symbol, t):
        rows = view.funding(symbol, t - TF_MS + 1)
        if not rows:
            return []
        marks, out = view.mark_bars(symbol, MARK_BARS), []
        for row in rows:
            known = [b for b in marks if b.available_ms <= row.time_ms]
            mark = known[-1].close if known and row.time_ms - known[-1].available_ms < HOUR else None
            out.append([row.time_ms, row.rate, row.interval_hours, mark])
        return out

    def _step(self, base, view):
        cache, members = dict(base), sorted(view.members())
        tails = {s: _tail(self._bars(view, s, cache)) for s in members}
        current = {s: _score(b[-181:]) if b and b[-1].available_ms == view.t else None for s, b in tails.items()}
        previous = {s: _score(b[-182:-1]) if len(b) >= 182 else None for s, b in tails.items()}
        ranks, prev_ranks = _rank(current), _rank(previous)
        btc = tails.get("BTCUSDT", ())
        bear = bool(btc and btc[-1].available_ms == view.t and _daily_bear(btc))
        records = []
        for symbol in members:
            bars = tails[symbol]
            fund = self._funding(view, symbol, view.t)
            if not bars or bars[-1].available_ms != view.t:
                if fund:
                    records.append({"s": symbol, "f": fund})
                continue
            last = bars[-1]
            records.append({
                "s": symbol, "b": [last.open, last.high, last.low, last.close], "f": fund,
                "q": _signals(bars, rank=ranks.get(symbol), prev_rank=prev_ranks.get(symbol), bear=bear),
                "n": view.next_open(symbol, TF),
            })
        return {"format": FORMAT, "t": view.t, "bear": bear, "r": records}, cache


_TAPE = Edge04Tape()


def tape(view):
    return _TAPE.decide(view)


def summarize(outputs):
    """Outcome-free deterministic shape summary used by the immutable run envelope."""
    counts = {h: 0 for h in HYPOTHESES}
    records = 0
    for cycle in outputs:
        for rec in cycle["r"]:
            if "q" not in rec:
                continue
            records += 1
            for h in HYPOTHESES:
                counts[h] += int(bool(rec["q"][h]["e"]))
    return {"format": "zb-edge04-tape-summary/1", "cycles": len(outputs), "records": records,
            "entry_signals": counts}
