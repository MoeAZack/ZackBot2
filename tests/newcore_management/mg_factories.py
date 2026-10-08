"""Deterministic builders for the NC-07 management tests (stdlib only)."""
import os
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from newcore.domain import Capability, InstrumentId, InstrumentRules, Side, Venue  # noqa: E402
from newcore.management import Candle, CostModel, build_plan  # noqa: E402

T0 = 1704067200000                 # the golden pack's clock start (2024-01-01 UTC); candle 0 = the entry candle here
H4 = 4 * 3600 * 1000
K = D(200)                         # mirror axis: a long path at p is the short path at 200 - p
GOLDEN_COSTS = CostModel(taker_fee=D('0.0005'), slip=D('0.0002'))
ZERO_COSTS = CostModel(taker_fee=D(0), slip=D(0))
LONG, SHORT = Side.LONG, Side.SHORT


def rules(step='0.001', tick='0.01', min_qty=None, max_qty='1000000', min_notional='5'):
    return InstrumentRules(instrument=InstrumentId(venue=Venue.BINANCE_USDM, symbol='SOLUSDT'), tick_size=D(tick),
                           step_size=D(step), min_qty=D(min_qty or step), max_qty=D(max_qty),
                           min_notional=D(min_notional),
                           capabilities=(Capability.STOP_MARKET, Capability.REDUCE_ONLY))


def px(side, p):
    """A long-frame price in the frame of `side` (the short mirror is 200 - p)."""
    p = D(p)
    return p if side is LONG else K - p


def candle(i, o, h, l, c, side=LONG, t0=T0):
    """Candle i (0 = the entry candle) given in the LONG frame; mirrored for a short."""
    o, h, l, c = D(o), D(h), D(l), D(c)
    if side is SHORT:
        o, h, l, c = K - o, K - l, K - h, K - c
    return Candle(open_ms=t0 + i * H4, open=o, high=h, low=l, close=c)


def flat(i, p='100', wick='0.5', side=LONG):
    p, w = D(p), D(wick)
    return candle(i, p, p + w, p - w, p, side)


def path(bars, n, side=LONG, base='100', wick='0.5'):
    """n candles from the entry candle; `bars` = {i: (o, h, l, c)} in the long frame; flat at the last close between."""
    out, last = [], D(base)
    for i in range(n):
        if i in bars:
            out.append(candle(i, *bars[i], side=side))
            last = D(bars[i][3])
        else:
            out.append(flat(i, last, wick, side))
    return out


def plan(side=LONG, *, entry='100.02', qty='5', stop='98.02', cap='12', costs=GOLDEN_COSTS, r=None, add=None,
         scale='1', tp1_frac=None, tp1_off=None, tp2_off=None, be=False, time_exit=None, trail=None, mirror_entry=True,
         trail_mode='close_offset'):
    """A plan given in the LONG frame (entry / stop / add prices), mirrored for a short."""
    b = build_plan(rules=r or rules(), side=side, entry_price=px(side, entry) if mirror_entry else D(entry),
                   entry_qty=D(qty), entry_candle_open_ms=T0, candle_seconds=4 * 3600, stop_price=px(side, stop),
                   risk_cap=D(cap), costs=costs, add_price=None if add is None else px(side, add),
                   add_scale=None if add is None else D(scale), tp1_frac=None if tp1_frac is None else D(tp1_frac),
                   tp1_offset=None if tp1_off is None else D(tp1_off), tp2_offset=None if tp2_off is None else D(tp2_off),
                   be_after_tp1=be, time_exit_candles=time_exit, trail_offset=None if trail is None else D(trail),
                   trail_mode=trail_mode)
    return b.plan


def net_be(p, avg, open_fees, q):
    """Independent fee-aware net break-even (Codex ruling 8): the level where closing q (exit slippage + exit fee) covers
    every booked opening fee; rounded away from the average."""
    import decimal
    from newcore.domain.instrument import Rounding
    with decimal.localcontext() as ctx:
        ctx.prec = 60
        f, s = p.costs.taker_fee, p.costs.slip
        if p.side is LONG:
            lvl = (avg + open_fees / q) / ((1 - s) * (1 - f))
            return p.rules.quantize_price(+lvl.quantize(D('1e-20')), Rounding.UP)
        lvl = (avg - open_fees / q) / ((1 + s) * (1 + f))
        return p.rules.quantize_price(+lvl.quantize(D('1e-20')), Rounding.DOWN)


def kinds(actions):
    return [(a.kind.value, a.leg.value) for a in actions]
