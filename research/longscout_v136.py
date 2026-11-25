"""Research-only long on BTC aggressor-flow reversal.

Use completed BTC perpetual 4h quote turnover and taker-buy quote turnover.
Require sell imbalance in each of the preceding three 4h bars and mean
imbalance no greater than -0.10. Buy after the current imbalance reaches
+0.10 while BTC and the target coin close higher and both have rising
or sideways R1. The signal uses a switch in trading direction. Execute
with the unchanged frozen simulator.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('Uses a chronological reversal in actual market-wide BTC taker direction. V98 '
                  'is single-day per-coin absorption, where price opposes takers, or trade-size '
                  'reversal. V122 splits unsigned activity across instruments. This neither tests '
                  'absorption nor estimates trade size.')
BTC_SIGNED_FLOW_MANIFEST_SHA256 = 'c85bb28a5123b939ffee8ebc836066a169c2dc65b0c77dc0eb67a635ac8056e7'
MARKET_FEATURE = ('BTC_SIGNED_FLOW_MANIFEST_SHA256', 'BTC 4h taker-buy and total quote turnover')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_signed_flow_20261125/manifest.json'
_BTC = None


def load_flow():
    path = Path(os.environ.get('LONGSCOUT_BTC_SIGNED_FLOW_MANIFEST', DEFAULT_DATA))
    raw = path.read_bytes()
    if BTC_SIGNED_FLOW_MANIFEST_SHA256 is None or hashlib.sha256(raw).hexdigest() != BTC_SIGNED_FLOW_MANIFEST_SHA256:
        raise ValueError('expected pinned BTC signed-flow manifest')
    data = json.loads(raw)
    fields = ['quote_turnover', 'taker_buy_quote_turnover']
    if (data['status'], data['timeframe'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', '4h', fields):
        raise ValueError('invalid BTC signed-flow source contract')
    ts, rows = zip(*data['rows'])
    dates = pd.DatetimeIndex(pd.to_datetime(ts, unit='ms'))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='4h')):
        raise ValueError('BTC flow dates must be sorted and consecutive')
    return pd.DataFrame([row or [None, None] for row in rows], index=dates, columns=fields).apply(pd.to_numeric)


def flow_reversal(total, taker):
    valid = np.isfinite(total) & np.isfinite(taker) & total.gt(0) & taker.ge(0) & taker.le(total)
    imbalance = (2 * taker / total - 1).where(valid)
    previous = imbalance.shift().rolling(3, min_periods=3)
    return previous.max().lt(0) & previous.mean().le(-.10) & imbalance.ge(.10)


def set_btc(frame):
    global _BTC
    dates = pd.DatetimeIndex(pd.to_datetime(frame.t))
    prices = pd.Series(frame.c.to_numpy(), index=dates)
    grid = pd.date_range(dates[0], dates[-1], freq='4h')
    flow = load_flow().reindex(grid)
    rising = prices.reindex(grid).diff().gt(0)
    nonbear = pd.Series(np.isin(L1.coin_regime(frame), (0., 1.)), index=dates)
    _BTC = (flow_reversal(flow.quote_turnover, flow.taker_buy_quote_turnover)
            & rising & nonbear.reindex(grid, fill_value=False))


def sig_aggressor_reversal(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    consecutive = dates.to_series().diff().eq(pd.Timedelta(hours=4)).to_numpy()
    entry = (_BTC.reindex(dates, fill_value=False).to_numpy() & consecutive
             & d.c.diff().gt(0).to_numpy() & np.isin(L1.coin_regime(d), (0., 1.)))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(entry, index=d.index)), se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l136_btc_taker_flow_reversal_4h': dict(
        name='[RESEARCH] L136 BTC taker-flow reversal long', style='momentum', fn=sig_aggressor_reversal,
        sides='long', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=18),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
