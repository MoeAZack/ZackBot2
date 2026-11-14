"""CP-Y2 breadth thrust, daily; default off, PAPER/testnet canary only.

Frozen rule from research/newstrat_cp3.py (PR #337, commit 646ab3fa, sig_breadth_thrust), unchanged:
  breadth = share of the panel's coins whose daily close is above their own 20-day EMA (ewm span 20, adjust=False,
            min_periods=20; a coin counts from its first candle, and below-EMA during its first 19 days).
  long  : breadth reaches >= hi after it was <= lo at some point in the previous `win` days (first day only), and the
          coin closes above its own EMA21;
  short : the mirror (breadth <= lo after >= hi in the previous `win` days), coin below its EMA21.
  No signal exits; management is stop 2.5 ATR, half off at +1R, stop to breakeven at +1R, target 3R, 20-day time exit.

Panel: exactly PANEL_SYMBOLS (BTCUSDT + the 31 coins of the frozen screen), as in the research run. The engine's context
(strategies.build_context) carries the closes of every coin of the timeframe's cycle in ctx['panel_c']; this rule keeps
only PANEL_SYMBOLS, so other coins never move the breadth. It FAILS CLOSED (no signal at all) when the context has no
panel, or when any panel coin is absent or has no close on the latest panel candle, or the coin's own last candle is not
that candle (a missing / stale coin would silently shrink the breadth denominator, which the research never saw).
A slot of this strategy may only trade panel coins (app.validate_sleeve enforces it through 'panel_symbols').
Evidence: PR #340 comment 6849008512 (disclosed post-hoc max 3 / 5 variant pass; thin: few distinct entry dates).
Importing this module registers nothing; only register_for_paper adds a PAPER entry. Not a profitability claim.
"""
import numpy as np
import pandas as pd
import strategies as S

KEY = 'cp_y2_breadth_thrust_1d'
PARAMS = dict(lo=0.25, hi=0.75, win=5)
MGMT = dict(stop_atr=2.5, tp_r=3.0, max_bars=20, tp1_r=1.0, tp1_frac=0.5, be_r=1.0)
EXTRA31 = ('1000PEPEUSDT', 'AAVEUSDT', 'APTUSDT', 'ARBUSDT', 'ARKUSDT', 'BCHUSDT', 'DOTUSDT', 'ENAUSDT', 'ENJUSDT',
           'ETCUSDT', 'FETUSDT', 'FILUSDT', 'GALAUSDT', 'INJUSDT', 'LTCUSDT', 'MANAUSDT', 'MOVRUSDT', 'NEARUSDT',
           'ONDOUSDT', 'ONEUSDT', 'QNTUSDT', 'SANDUSDT', 'STRKUSDT', 'SUIUSDT', 'SUPERUSDT', 'TAOUSDT', 'UNIUSDT',
           'WLDUSDT', 'XLMUSDT', 'ZECUSDT', 'ZROUSDT')
PANEL_SYMBOLS = ('BTCUSDT',) + EXTRA31          # PR #340 sieve_v1_panel: "BTC + the case's 31 coins"


def breadth(c):
    """Share of coins above their own EMA20 at each time. c: closes, time x symbol (NaN before a coin's first candle)."""
    above = (c > c.ewm(span=20, adjust=False, min_periods=20).mean()).where(c.notna())
    return above.sum(axis=1) / c.notna().sum(axis=1).replace(0, np.nan)


def thrust(br, p):
    """(up, dn) boolean Series on the panel's time index: the first day of a breadth thrust up / down."""
    up = (br >= p['hi']) & (br.rolling(p['win']).min().shift() <= p['lo'])
    dn = (br <= p['lo']) & (br.rolling(p['win']).max().shift() >= p['hi'])
    up = up & ~up.shift().fillna(False).astype(bool)
    dn = dn & ~dn.shift().fillna(False).astype(bool)
    return up, dn


def panel_of(d, ctx):
    """The frozen panel's closes, or None (fail closed) when it is missing, incomplete or stale on the latest candle."""
    c = ctx.get('panel_c') if isinstance(ctx, dict) else None
    if not isinstance(c, pd.DataFrame) or c.empty or not len(d) or any(s not in c.columns for s in PANEL_SYMBOLS):
        return None
    c = c[list(PANEL_SYMBOLS)].sort_index().dropna(how='all')
    if c.empty or c.iloc[-1].isna().any() or pd.Timestamp(d.t.iloc[-1]) != c.index[-1]:
        return None
    return c


def signal(d, p, ctx):
    """Evaluate the frozen rule on closed daily candles of one coin; entry is the next bar."""
    z = pd.Series(False, index=d.index, dtype=bool)
    c = panel_of(d, ctx)
    if c is None:
        return dict(le=z.copy(), se=z.copy(), lx=z.copy(), sx=z.copy())
    up, dn = thrust(breadth(c), p)
    t = pd.to_datetime(d.t)
    u = pd.Series(up.reindex(t).fillna(False).values, index=d.index).astype(bool)
    w = pd.Series(dn.reindex(t).fillna(False).values, index=d.index).astype(bool)
    le = u & (d.c > d.e21)
    se = w & (d.c < d.e21)
    return dict(le=S.X(le), se=S.X(se), lx=z.copy(), sx=z.copy())


# Research-safe metadata and callable: registration creates a separate PAPER entry.
RESEARCH = dict(name='[RESEARCH] CP-Y2 Breadth thrust daily',
                desc='Breadth thrust (share of coins above EMA20 swings <=25% -> >=75% within 5 days, and the mirror) '
                     'over BTC + 31 screened coins; disclosed post-hoc variant pass. PAPER canary candidate.',
                style='breadth', fn=signal, sides='both', tf='1d', panel_symbols=PANEL_SYMBOLS, reentry_lock=True,
                research_only=True, params=dict(PARAMS), mgmt=dict(MGMT))
_PAPER_ENTRY = None


def register_for_paper(mode, base):
    """Register only for PAPER at an exact HTTPS Binance futures testnet origin."""
    global _PAPER_ENTRY
    if mode != 'paper' or base not in (
        'https://testnet.binancefuture.com', 'https://demo-fapi.binance.com'
    ):
        raise ValueError('breadth thrust registration requires PAPER and an exact testnet HTTPS origin')
    existing = S.STRATEGIES.get(KEY)
    if KEY in S.STRATEGIES:
        if _PAPER_ENTRY is not None and existing is _PAPER_ENTRY:
            return existing
        raise ValueError('breadth thrust strategy key already registered')
    entry = dict(RESEARCH, name='[PAPER CANARY] CP-Y2 Breadth thrust daily',
                 research_only=False, params=dict(RESEARCH['params']),
                 mgmt=dict(RESEARCH['mgmt']))
    S.STRATEGIES[KEY] = entry
    _PAPER_ENTRY = entry
    return entry
