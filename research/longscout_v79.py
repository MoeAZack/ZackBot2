"""LONGSCOUT-V79: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: WEEKLY swing-structure turn (a run of lower weekly highs broken by a close above last
week's high). Earlier weekly rules used engulfing (V59), inside weeks (V60), 12-week closes (V25) and weekly EMA/RSI.

L79A l79_weekly_turn_up_4h    long  weeks are Mon-Sun UTC; a complete week has all 7 days. Weeks w-5..w-1 are complete and
                                    consecutive, and each of w-4..w-1 has a lower high than the week before it (four lower
                                    weekly highs). During week w, the first completed day that closes above week w-1's high;
                                    coin rising or sideways (R1). First 4h bar of the next day (modeled fill 04:00 UTC). Stop
                                    2.5 ATR, 2R, 30 bars.
S79A s79_weekly_turn_down_4h  short mirror: four higher weekly lows (w-4..w-1), first close in week w below week w-1's low; coin
                                    falling or sideways (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _turn_v(D, p, up):
    k = p['k']
    mon = pd.Series(D.index - pd.to_timedelta(D.index.dayofweek, unit='D'), index=D.index)
    g = D.groupby(mon.values)
    W = pd.DataFrame({'h': g.h.max(), 'l': g.l.min(), 'n': g.size()})
    W = W[W.n == 7]
    wk = list(W.index); H, Lw = W.h.values, W.l.values
    lvl = {}
    for i in range(k, len(wk)):
        if wk[i] - wk[i - k] != pd.Timedelta(days=7 * k):
            continue
        if up and all(H[t] < H[t - 1] for t in range(i - k + 1, i + 1)):
            lvl[wk[i] + pd.Timedelta(days=7)] = H[i]
        if not up and all(Lw[t] > Lw[t - 1] for t in range(i - k + 1, i + 1)):
            lvl[wk[i] + pd.Timedelta(days=7)] = Lw[i]
    out = np.zeros(len(D), bool)
    done = set()
    c = D.c.values
    for j, m in enumerate(mon.values):
        m = pd.Timestamp(m)
        if m not in lvl or m in done:
            continue
        if (c[j] > lvl[m]) if up else (c[j] < lvl[m]):
            out[j] = True; done.add(m)
    return pd.Series(out, index=D.index)


def _turn(d, p, up):
    D = L27._days_v(d)
    return L27._next_day(_turn_v(D, p, up), D, d)


def sig_up(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_turn(d, p, True) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


def sig_dn(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_turn(d, p, False) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d),
                sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l79_weekly_turn_up_4h': dict(
        name='[RESEARCH] L79A Weekly turn up after four lower weekly highs long 4h', style='reversal', fn=sig_up,
        sides='long', tf='4h', research_only=True, params=dict(k=4), mgmt=dict(MG)),
    's79_weekly_turn_down_4h': dict(
        name='[RESEARCH] S79A Weekly turn down after four higher weekly lows short 4h', style='reversal', fn=sig_dn,
        sides='short', tf='4h', research_only=True, params=dict(k=4), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
