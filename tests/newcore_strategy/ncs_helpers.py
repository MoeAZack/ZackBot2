"""Shared helpers for the newcore.strategy tests: frozen-CSV loading and deterministic synthetic candles."""
import csv
import math
import os
import random
from datetime import datetime, timezone

from newcore.strategy import Bars

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
H1, H4 = 3_600_000, 14_400_000
CORE8 = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT')


def to_ms(s):
    return int(datetime.strptime(s, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp()) * 1000


def load(sym, tf='4h'):
    """data_long/<tf>/<sym>_<tf>.csv -> Bars (timestamps are UTC open times)."""
    path = os.path.join(ROOT, 'data_long', tf, f'{sym}_{tf}.csv')
    t, o, h, l, c = [], [], [], [], []
    with open(path, newline='') as f:
        for r in csv.DictReader(f):
            t.append(to_ms(r['t'])); o.append(float(r['o'])); h.append(float(r['h'])); l.append(float(r['l'])); c.append(float(r['c']))
    return Bars(sym, {'4h': H4, '1h': H1}[tf], tuple(t), tuple(o), tuple(h), tuple(l), tuple(c))


def as_of(b):
    return b.close_ms(len(b) - 1)


def synthetic(n=4000, seed=7, tf_ms=H4, t0=1_640_995_200_000, base=1000.0):
    """Trending random walk (slow sine drift + noise) so both crosses and both momentum states occur. Prices are
    rounded to 1/256 so the 2M - p reflection used by the symmetry tests is exact in binary floating point."""
    rng = random.Random(seed)
    q = lambda x: round(x * 256) / 256
    t, o, h, l, c = [], [], [], [], []
    px = base
    for i in range(n):
        drift = 0.004 * math.sin(i / 300.0) + 0.0015 * math.sin(i / 47.0)
        op = px
        cl = op * (1 + drift + rng.gauss(0, 0.012))
        hi = max(op, cl) * (1 + abs(rng.gauss(0, 0.005)))
        lo = min(op, cl) * (1 - abs(rng.gauss(0, 0.005)))
        op, hi, lo, cl = q(op), q(hi), q(lo), q(cl)
        t.append(t0 + i * tf_ms); o.append(op); h.append(max(hi, op, cl)); l.append(min(lo, op, cl)); c.append(cl)
        px = cl
    return Bars('SYNUSDT', tf_ms, tuple(t), tuple(o), tuple(h), tuple(l), tuple(c))


def reflect(b, m2):
    """Price mirror p -> m2 - p (m2 = 2M): every long condition becomes the matching short condition."""
    return Bars(b.symbol, b.tf_ms, b.t_ms, tuple(m2 - x for x in b.o), tuple(m2 - x for x in b.l),
                tuple(m2 - x for x in b.h), tuple(m2 - x for x in b.c))
