"""STRAT-01 R4b-PIT evaluator: the point-in-time signal tape of `trend_ema_mom.v1` and its 12 registered neighbours,
run ONLY inside the `pit.evaluate` sandbox (`zb-eval-sandbox/1`), never in the harness process.

Entry point for `pit.evaluate(window, <this file>, 'tape', times)`:
  tape(view)   one cycle at `view.t`: for every universe member (ascending symbol order) whose bar closes at `t`,
               the closed bar, the signal of every registered configuration over the last `WINDOW` closed bars
               available at `t` (contiguous tail; a gap restarts warm-up, never forward-filled), the primary stop
               distance once the primary is warm, and the funding events that became available in (t - 4h, t] with
               their funding-mark proxy v1 (`pit.FUNDING_MARK_PROXY`: the close of the 1h mark bar ending at or before
               the funding time; a gap > 1 bar = no mark).
  - per symbol `n`: the open of the bar opening at t (`View.next_open`: its open only, observable at t), used only
    for a fill "at the open of the next bar" at the split end, where that bar lies past the window;
  - per cycle `g`: the registered PIT regime label (prereg hypothesis.regime): BTCUSDT 4h close vs its trailing
    EMA200 at the decision time, `btc_up` (close > EMA200) / `btc_down`, over the same last-`WINDOW` contiguous
    closed bars as the rule; None until that tail reaches the rule's warm-up (220 bars) or without a BTCUSDT bar
    closing at t. Frozen in the attested tape before any outcome is computed.
The tape holds no outcome: no fill, no cost, no R. Accounting (fills, costs, funding, the 180-bar cap, baselines,
metrics) is a pure function of this attested tape in `pit_run.py`, so every number it produces derives from rows that
were available at the time they are used (each decision here is re-run under future perturbation by `pit.evaluate`).

Signals are bit-identical to `trend_ema_mom.signal_at_last` (same float recursions over the same bars; EMAs are shared
across configurations by period only, which changes no arithmetic). State: the incremental bar cache only (the m3_eval
pattern): a repeated time recomputes from the state BEFORE that time and discards its new state, so the perturbation
re-run is a pure function of (prior state, view at t); a time going backwards is refused. Stdlib only.
"""
from __future__ import annotations

import trend_ema_mom as T

FORMAT = 'zb-pit-tape/2'
HOUR = 3_600_000
REGIME_SYMBOL = 'BTCUSDT'
REGIME_EMA = 200
MARK_BARS = 8                                       # 1h mark bars read per funding lookup (covers (t - 4h, t])
SIGNAL_CONFIGS = (('primary', T.PRIMARY),) + tuple(T.neighbours())   # index 0 = the primary (cap180 + uncapped)


def signals(bars, configs=SIGNAL_CONFIGS):
    """[(le, lx, atr) | None per configuration] at the last bar of `bars` (closed, contiguous). EMAs / ATR / returns
    are computed once per distinct period and are bit-identical to `trend_ema_mom.signal_at_last`."""
    i = len(bars) - 1
    c = [b.close for b in bars]
    h = [b.high for b in bars]
    lo = [b.low for b in bars]
    emas, atrs, out = {}, {}, []

    def ema(n):
        if n not in emas:
            emas[n] = T.ema(c, n)
        return emas[n]

    for _, p in configs:
        if i < p.start():
            out.append(None)
            continue
        ef, es, et = ema(p.ema_fast), ema(p.ema_slow), ema(p.ema_trend)
        if p.atr_len not in atrs:
            atrs[p.atr_len] = T.wilder_atr(h, lo, c, p.atr_len)
        rs, rl = T.pct_return(c, p.bars_of(p.mom_short_days), i), T.pct_return(c, p.bars_of(p.mom_long_days), i)
        mom = rs is not None and rl is not None and rs > 0 and rl > 0
        out.append((T.cross_up(ef, es, i) and c[i] > et[i] and mom, ef[i] < es[i] or not mom, atrs[p.atr_len][i]))
    return out


class Tape:
    def __init__(self, configs=SIGNAL_CONFIGS, window: int = T.WINDOW):
        self.configs, self.window = tuple(configs), window
        self._last_t = None
        self._prev = self._cur = None

    def decide(self, view):
        t = view.t
        if self._last_t is None or t > self._last_t:
            base = self._cur if self._cur is not None else {}
            out, st = self._step(base, view)
            self._prev, self._cur, self._last_t = base, st, t
            return out
        if t == self._last_t:                       # the perturbation re-run: same prior state, new state discarded
            return self._step(self._prev, view)[0]
        raise ValueError('decision times must be non-decreasing within one sandbox process')

    def _bars(self, view, s, cache):
        have = cache.get(s, ())
        if have:
            new = view.bars(s, T.TF, 2)
            add = tuple(b for b in new if b.open_ms > have[-1].open_ms)
            if len(add) < len(new):                 # overlaps the cache: nothing was skipped
                have = (have + add)[-self.window:]
                cache[s] = have
                return have
        have = tuple(view.bars(s, T.TF, self.window))
        cache[s] = have
        return have

    def _funding(self, view, s, t):
        rows = view.funding(s, t - T.TF_MS + 1)
        if not rows:
            return []
        marks = view.mark_bars(s, MARK_BARS)
        out = []
        for f in rows:
            m = [b for b in marks if b.available_ms <= f.time_ms]
            mark = m[-1].close if m and f.time_ms - m[-1].available_ms < HOUR else None
            out.append([f.time_ms, f.rate, f.interval_hours, mark])
        return out

    def _step(self, base: dict, view):
        cache = dict(base)
        t, recs, regime = view.t, [], None
        for s in sorted(view.members()):
            b = self._bars(view, s, cache)
            fund = self._funding(view, s, t)
            if not b or b[-1].available_ms != t:
                if fund:
                    recs.append({'s': s, 'f': fund})
                continue                                    # no bar closing at t: no decision (never stale)
            last = b[-1]
            tail = T.contiguous_tail(b)
            sig = signals(tail, self.configs)
            if s == REGIME_SYMBOL and len(tail) > T.PRIMARY.warmup_bars:
                closes = [x.close for x in tail]
                regime = 'btc_up' if closes[-1] > T.ema(closes, REGIME_EMA)[-1] else 'btc_down'
            rec = {'s': s, 'b': [last.open, last.high, last.low, last.close], 'f': fund,
                   'w': ''.join('-' if x is None else '1' for x in sig),
                   'x': ''.join('1' if x is not None and x[1] else '0' for x in sig),
                   'e': {str(k): str(T.stop_distance(x[2], self.configs[k][1]))
                         for k, x in enumerate(sig) if x is not None and x[0]},
                   'd': None if sig[0] is None else str(T.stop_distance(sig[0][2], self.configs[0][1])),
                   'n': view.next_open(s, T.TF)}
            recs.append(rec)
        return {'t': t, 'r': recs, 'g': regime}, cache


_TAPE = Tape()


def tape(view):
    return _TAPE.decide(view)
