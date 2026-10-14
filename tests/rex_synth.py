"""REX-01 synthetic tape builder and fixture registry (no market data: every bar here is generated).

Bar index 0 opens at H; negative indices are the warm-up (`pre` bars, default W = 60). Prices default to a flat 100
with high / low = max / min(open, close) +- 1, so TR-SMA14 = ATR = 2 exactly. VWAP targets are set per bar (sticky
from the index on) and realised exactly by solving each bar's quote volume (volume 1), so z is exact:
z = (close - VWAP) / 2. Funding: rows every `fund_iv` hours on the UTC grid, 1h marks at 100.

`rex(group, cases)` registers parametrized fixture cases under a spec group (F1..F16); the registry test asserts the
frozen per-group counts (208 in total)."""
from __future__ import annotations

import calendar
import os
import sys
from collections import defaultdict

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import pit as P                                                                             # noqa: E402
import range_vwap_rev as RV                                                                 # noqa: E402

HOUR, TF = RV.HOUR, RV.TF
H = calendar.timegm((2026, 11, 16, 0, 0, 0)) * 1000          # the recommended H (Monday 00:00 UTC)
RULE = {'tick': 0.01, 'step': 0.001, 'min_qty': 0.001, 'min_notional': 5.0}

REGISTRY = defaultdict(list)


def rex(group: str, cases, sides=False):
    """Register fixture cases of a spec group and parametrize the test over them (x both sides when `sides`)."""
    ids = [f'{s}-{c}' for c in cases for s in RV.SIDES] if sides else list(cases)
    REGISTRY[group].extend(f'{group}:{i}' for i in ids)
    params = [(s, c) for c in cases for s in RV.SIDES] if sides else list(cases)
    if sides:
        return pytest.mark.parametrize('side,case', params, ids=ids)
    return pytest.mark.parametrize('case', params, ids=ids)


def ms(i: int) -> int:
    return H + i * TF


def build(n: int = 40, *, pre: int = RV.W_BARS, close=None, vwap=None, ohlc=None, holes=(), vol=None,
          fund_rate=0.0001, fund_iv=8, fund_off=0, funding=None, marks=None, rule=None, side='long', tail_fund_h=48,
          hl=1.0, abs_ohlc=None):
    """One synthetic Series over bar indices -pre .. n-1. `close` {i: c}; `vwap` {i: v} sticky targets; `ohlc`
    {i: (o, h, l, c)} overrides; `holes` indices removed; `vol` {i: (volume, quote_volume)} overrides. `side='short'`
    mirrors every price / VWAP / funding sign around 100 (the exact mirror of a long fixture)."""
    close, vwap, ohlc, vol = dict(close or {}), dict(vwap or {}), dict(ohlc or {}), dict(vol or {})
    mir = (lambda p: 200.0 - p) if side == 'short' else (lambda p: p)
    idx = list(range(-pre, n))
    N = RV.N
    tv, cur = {}, 100.0
    for i in idx:
        cur = vwap.get(i, cur)
        tv[i] = mir(cur)
    qv = {}
    for j, i in enumerate(idx):
        qv[i] = tv[idx[0]] if j < N else N * (tv[i] - tv[idx[j - 1]]) + qv[idx[j - N]]
    bars, prev = {}, mir(100.0)
    for i in idx:
        c = mir(close.get(i, 100.0))
        o = prev
        if i in ohlc:
            o, h, lo, c = ohlc[i]
            o, c = mir(o), mir(c)
            h, lo = (mir(h), mir(lo)) if side == 'long' else (mir(lo), mir(h))
        else:
            h, lo = max(o, c) + hl, min(o, c) - hl
        if abs_ohlc and i in abs_ohlc:
            o, h, lo, c = abs_ohlc[i]
        v, q = vol.get(i, (1.0, qv[i]))
        if i in vol and side == 'short':
            q = 200.0 * v - q
        if i not in holes:
            bars[ms(i)] = P.Bar(ms(i), round(o, 10), round(h, 10), round(lo, 10), round(c, 10), v, round(q, 10),
                                ms(i) + TF)
        prev = c
    lo_ms, hi_ms = ms(idx[0]) - 24 * HOUR, ms(n) + tail_fund_h * HOUR
    if funding is None:
        sg = -1.0 if side == 'short' else 1.0
        funding = [P.Funding(t + fund_off, sg * fund_rate, fund_iv, t + fund_off)
                   for t in range(lo_ms - lo_ms % (fund_iv * HOUR), hi_ms, fund_iv * HOUR)]
    if marks is None:
        marks = {t: 100.0 for t in range(lo_ms - lo_ms % HOUR, hi_ms, HOUR)}
    return RV.Series(bars, dict(rule or RULE), tuple(funding), marks)


def long_entry(j: int, up=5.0, back=3.0):
    """VWAP targets giving a long X1 signal at decision close of bar j (z_(j-1) = -2.5, z_j = -1.5, RG true)."""
    return {j - 6: 100.0 + back, j - 1: 100.0 + up, j: 100.0 + back}


def run(tape, side='long', n=40, cfg=RV.PRIMARY, **kw):
    return RV.run_book(tape, cfg, side, first=H, end=ms(n), **kw)
