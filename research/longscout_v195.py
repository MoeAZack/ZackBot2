"""Research-only long when distinctly negative funding recedes across peers.

Use the latest already published Binance settlement, normalized to eight
hours. Compare the same at-least-60 peers across adjacent daily decisions,
leaving the target out. Buy after the fraction paying -1 bp or worse falls
from at least 35% to at most 20%. Require target funding within 1.5 bp of
zero, an up-day target and nonbear target and BTC R1. Entry metadata only.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import longscout_v117 as L117
import longscout_v143 as L143
import strategies as S

FAMILY_NOVELTY = ('Measures absolute settled negative-funding participation in a paired '
                  'leave-one-out universe. V117 tests each target on its own funding, '
                  'V143 ranks relative carry, and V170 uses traded basis across five peers.')
RUNNER = 'longscout_flow_run.py'
DATA_MANIFEST_SHA256 = L117.DATA_MANIFEST_SHA256
_ENTRY = None


def funding_pressure_eases(rates):
    data = rates.where(np.isfinite(rates))
    before = data.shift()
    paired = data.notna() & before.notna()
    negative_then = before.le(-.0001) & paired
    negative_now = data.le(-.0001) & paired
    total = paired.sum(axis=1)
    peers = paired.rsub(total, axis=0)
    then = negative_then.rsub(negative_then.sum(axis=1), axis=0)
    now = negative_now.rsub(negative_now.sum(axis=1), axis=0)
    eligible = peers.where(peers.ge(60))
    return then.div(eligible).ge(.35) & now.div(eligible).le(.20)


def set_panel(panel):
    global _ENTRY
    L117.set_panel(panel)
    btc, _ = L101._native_daily(panel['BTCUSDT'])
    grid = pd.date_range(btc.index[0], btc.index[-1], freq='D')
    cutoff = (grid + pd.Timedelta(days=1)).as_unit('ms').asi8
    reported = {}
    for symbol, frame in panel.items():
        if symbol == 'BTCUSDT':
            continue
        close, _ = L101._native_daily(frame)
        current = pd.Series(L143.observed_funding(cutoff, L117.load_funding(symbol)), index=grid)
        reported[symbol] = current.where(close.reindex(grid).notna())
    snapshot = pd.DataFrame(reported, index=grid)
    eligible_market = L1.regime_daily(btc.reindex(grid)).isin((0., 1.))
    _ENTRY = (funding_pressure_eases(snapshot) & snapshot.abs().le(.00015)).where(eligible_market, False, axis=0)


def sig_peer_funding_relief(d, params, ctx):
    symbol = L101._context_symbol(ctx)
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    buys = _ENTRY[symbol].reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(buys.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l195_absolute_funding_pressure_breadth_relief_1d': dict(
        name='[RESEARCH] L195 Broad settled funding pressure eases', style='momentum',
        fn=sig_peer_funding_relief, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
