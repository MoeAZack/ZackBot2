"""Selected-DEV H5 long hypothesis; default off, PAPER canary only.

This module does not register on import or enable a trading slot. Its results are
research evidence, not a claim of verified profitability.
"""
import pandas as pd
import strategies as S

KEY = 'h5_volume_impulse_long_1h'


def signal(d, p, ctx):
    """Evaluate the frozen H5 long rule on closed candles; entry is next bar."""
    body = (d.c - d.o).abs()
    span = d.h - d.l
    prior_median = d.v.rolling(24, min_periods=24).median().shift()
    le = ((body >= d.atr) & (body <= 3 * d.atr) &
          (d.v >= 2 * prior_median) & (span > 0) & (d.c > d.o) &
          (d.c >= d.l + .8 * span) & (d.e50 > d.e200))
    false = pd.Series(False, index=d.index, dtype=bool)
    return dict(le=le.fillna(False).astype(bool), se=false.copy(),
                lx=false.copy(), sx=false.copy())


# Research-safe metadata and callable: registration creates a separate PAPER entry.
RESEARCH = dict(name='[RESEARCH] H5 volume impulse long 1h',
                desc='Selected DEV H5 long hypothesis; PAPER canary candidate.',
                style='momentum', fn=signal, sides='long', tf='1h',
                research_only=True, params={},
                mgmt=dict(stop_atr=3, tp_r=4, max_bars=48, be_r=0, trail_atr=0))
_PAPER_ENTRY = None


def register_for_paper(mode, base):
    """Register only for PAPER at an exact HTTPS Binance futures testnet origin."""
    global _PAPER_ENTRY
    if mode != 'paper' or base not in (
        'https://testnet.binancefuture.com', 'https://demo-fapi.binance.com'
    ):
        raise ValueError('H5 registration requires PAPER and an exact testnet HTTPS origin')
    existing = S.STRATEGIES.get(KEY)
    if KEY in S.STRATEGIES:
        if _PAPER_ENTRY is not None and existing is _PAPER_ENTRY:
            return existing
        raise ValueError('H5 strategy key already registered')
    entry = dict(RESEARCH, name='[PAPER CANARY] H5 volume impulse long 1h',
                 research_only=False, params=dict(RESEARCH['params']),
                 mgmt=dict(RESEARCH['mgmt']))
    S.STRATEGIES[KEY] = entry
    _PAPER_ENTRY = entry
    return entry
