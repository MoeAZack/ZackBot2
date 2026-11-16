"""LONGSCOUT-V33: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families (exhaustion volume, BTC correlation, BTC beta).

S33A s33_churn_exhaustion_4h    short coin rising (R1); the last completed day had volume >= 4x the median of the prior 30 days
                                      yet closed in the bottom 40% of its range after a 30-day gain of >= 30% (heavy volume, no
                                      progress: a blow-off). First 4h bar of the next day. Stop 3 ATR, 2R, 30 bars.
L33A l33_btc_decoupling_4h      long  coin rising (R1) and the 30-day correlation of its daily returns with BTC's falls below 0.3
                                      (first such day): idiosyncratic strength. First 4h bar of the next day.
                                      Stop 2.5 ATR, 2.5R, 36 bars.
L33B l33_high_beta_btc_up_4h    long  BTC rising (R1 on BTC) turns on, i.e. first completed day of a BTC rising regime, and the
                                      coin's 60-day beta to BTC daily returns is >= 1.3; coin not falling (R1). First 4h bar of
                                      the next day. Stop 3 ATR, 3R, 60 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _churn(d, p):
    D = L27._days_v(d)
    med = D.v.rolling(p['base'], min_periods=p['base']).median().shift()
    pos = (D.c - D.l) / (D.h - D.l).replace(0, np.nan)
    gain = D.c / D.c.shift(p['base']) - 1
    ok = (D.v >= p['vol'] * med) & (pos <= p['pos']) & (gain >= p['gain']) & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_churn(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_churn(d, p) & (cr == 1), index=d.index)), lx=_z(d), sx=_z(d))


def _joint(d):
    D = L27._days_v(d)
    f = L1._BTC['f']
    b = L1.daily_closes(f.t, f.c)
    J = pd.DataFrame({'c': D.c, 'b': b.reindex(D.index).values}, index=D.index)
    step = J.index.to_series().diff() == pd.Timedelta(days=1)
    J['rc'] = J.c.pct_change().where(step)
    J['rb'] = J.b.pct_change().where(step)
    return D, J


def _decouple(d, p):
    D, J = _joint(d)
    n = p['n']
    corr = J.rc.rolling(n, min_periods=n).corr(J.rb)
    hit = corr < p['lvl']
    ok = hit & ~hit.shift(fill_value=False) & corr.notna()
    return L27._next_day(ok, D, d)


def sig_decouple(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_decouple(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def _beta_turn(d, p):
    D, J = _joint(d)
    n = p['n']
    beta = J.rc.rolling(n, min_periods=n).cov(J.rb) / J.rb.rolling(n, min_periods=n).var()
    f = L1._BTC['f']
    br = L1.regime_daily(L1.daily_closes(f.t, f.c)).reindex(D.index)
    turn = (br == 1) & (br.shift() != 1) & br.shift().notna()
    ok = turn & (beta >= p['beta']) & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_beta_turn(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_beta_turn(d, p) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


CANDIDATES = {
    's33_churn_exhaustion_4h': dict(
        name='[RESEARCH] S33A High-volume churn after a big run short 4h', style='reversal', fn=sig_churn,
        sides='short', tf='4h', research_only=True, params=dict(base=30, vol=4.0, pos=0.4, gain=0.30),
        mgmt=dict(stop_atr=3.0, tp_r=2.0, max_bars=30)),
    'l33_btc_decoupling_4h': dict(
        name='[RESEARCH] L33A BTC decoupling in rising regime long 4h', style='momentum', fn=sig_decouple,
        sides='long', tf='4h', research_only=True, params=dict(n=30, lvl=0.3), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
    'l33_high_beta_btc_up_4h': dict(
        name='[RESEARCH] L33B High-beta coin at BTC rising-regime start long 4h', style='trend', fn=sig_beta_turn,
        sides='long', tf='4h', research_only=True, params=dict(n=60, beta=1.3), mgmt=dict(stop_atr=3.0, tp_r=3.0, max_bars=60)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
