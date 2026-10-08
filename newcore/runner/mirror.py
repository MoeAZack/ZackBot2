"""Price mirror for the short-side mechanical parity proof (M3): p -> 2*P0 - p per candle, high and low swapped.

    open' = 2P0 - open    close' = 2P0 - close    high' = 2P0 - low    low' = 2P0 - high    volume' = volume
P0 = the series' highest high (so every mirrored price is >= P0 > 0, and on the tick grid when the data is). Exact
Decimal arithmetic. Under the mirror the long rule on the original series and the mirrored short rule on the mirrored
series see the same signals (EMAs and returns are affine, ATR is a difference: unchanged), the same stop distances, the
same zb-path/1 walk (green and red swap, and so does the adverse extreme), so trades must match candle for candle.
Costs proportional to the price LEVEL (fees, slippage, funding) and the notional leverage cap are not mirror-invariant:
the parity is exact at zero costs with the cap out of the way, and the cost-on difference is measured separately.
"""
from __future__ import annotations

from newcore.ports.bars import Bar


def pivot_of(bars):
    return max(b.high for b in bars)


def mirror_bars(bars, pivot=None):
    p2 = 2 * (pivot if pivot is not None else pivot_of(bars))
    return tuple(Bar(open_ms=b.open_ms, close_ms=b.close_ms, open=p2 - b.open, high=p2 - b.low, low=p2 - b.high,
                     close=p2 - b.close, volume=b.volume) for b in bars)
