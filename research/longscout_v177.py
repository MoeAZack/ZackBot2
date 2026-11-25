"""Research-only long after risk-sensitive currencies recover against havens.

Use ECB daily AUD, CAD, CHF and JPY per EUR. Measure the risk pair's
average log value against the haven pair, which cancels the EUR quote.
Require a decline of at least .75% in the five preceding sessions and a
.40% recovery on the current session. AUD and CAD must each strengthen
against the haven basket. Require seven observed sessions, gaps at most
four days, and one extra UTC day before a nonbear up-day entry.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Tests rotation between two cyclically sensitive and two haven currencies '
                  'using a common quote that cancels. V132 tests crypto spot triangular pricing, '
                  'V121 stablecoin depegging, and V173 US real interest rates.')
ECB_RISK_FX_SHA256 = '515ed02a116ff56a76883b6920bde56960da89bd509ac606097ec010e086b208'
MARKET_FEATURE = ('ECB_RISK_FX_SHA256', 'ECB AUD, CAD, CHF and JPY reference exchange rates')
MARKET_FEATURE_SOURCE = 'ECB Data Portal euro foreign exchange reference rates'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_ecb_risk_fx_20261125/manifest.json'
MARKETS = ['AUD', 'CAD', 'CHF', 'JPY']
_BTC = None


def load_fx():
    raw = Path(os.environ.get('LONGSCOUT_ECB_RISK_FX', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ECB_RISK_FX_SHA256:
        raise ValueError('expected pinned ECB exchange rates')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields'], data['units']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1d', MARKETS, 'foreign currency units per EUR',
    ):
        raise ValueError('invalid ECB FX source contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not (dates.is_unique and dates.is_monotonic_increasing and dates.equals(dates.normalize())):
        raise ValueError('ECB session dates must be sorted unique midnights')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=MARKETS, dtype='float64')


def cyclicals_recover(quotes):
    log_quote = np.log(quotes.where(np.isfinite(quotes) & quotes.gt(0)))
    haven = (log_quote.CHF + log_quote.JPY) / 2.
    aud, cad = haven - log_quote.AUD, haven - log_quote.CAD
    risk_pair = (aud + cad) / 2.
    ready = risk_pair.notna().rolling(7, min_periods=7).sum().eq(7)
    daily_gaps = risk_pair.index.to_series().diff().dt.days
    consecutive = daily_gaps.rolling(6, min_periods=6).max().le(4)
    weak = (risk_pair.shift() - risk_pair.shift(6)).le(np.log(.9925))
    recovery = risk_pair.diff().ge(np.log(1.004)) & aud.diff().gt(0) & cad.diff().gt(0)
    events = ready & consecutive & weak & recovery
    return events.set_axis(events.index + pd.Timedelta(days=1))


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    pulse = cyclicals_recover(load_fx()).reindex(btc.index, fill_value=False)
    _BTC = pulse & L1.regime_daily(btc).isin((0., 1.))


def sig_fx_cyclicals(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l177_ecb_cyclicals_vs_havens_recovery_1d': dict(
        name='[RESEARCH] L177 FX cyclicals recover against haven currencies', style='momentum',
        fn=sig_fx_cyclicals, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
