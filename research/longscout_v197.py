"""Research-only long when the Chicago Fed credit subindex eases at stable risk.

Compare the latest weekly credit and risk measures with the preceding
week as reported within the same Thursday ALFRED vintage. Require credit
to fall at least .020 index points and risk to move no more than .020.
After the full extra UTC day, buy a rising target while target and BTC
are nonbear. Historical estimates outside the pinned vintage are unused.
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

FAMILY_NOVELTY = ('Uses vintage-specific credit conditions when the financial risk subindex is '
                  'stable. V173 uses Treasury real yields, V175 repo execution costs, and V182 '
                  'Fed account balances, without modeling this credit component.')
NFCI_VINTAGES_SHA256 = '46652503c9d97a4d6aad1e4d160500190e24bc9afa831455c8ceef84e920ec6b'
MARKET_FEATURE = ('NFCI_VINTAGES_SHA256', 'vintage-matched Chicago Fed credit and risk subindexes')
MARKET_FEATURE_SOURCE = 'Chicago Fed NFCICREDIT and NFCIRISK historic ALFRED snapshots'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_nfci_vintages_20261125/manifest.json'
FIELDS = ['credit_previous', 'risk_previous', 'credit_current', 'risk_current']
_BTC = None


def load_condition_vintages():
    raw = Path(os.environ.get('LONGSCOUT_NFCI_VINTAGES', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != NFCI_VINTAGES_SHA256:
        raise ValueError('expected pinned ALFRED credit-risk vintages')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', '1w', FIELDS):
        raise ValueError('invalid Chicago Fed condition-vintage archive contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    vintage = pd.DatetimeIndex(pd.to_datetime([row[1] for row in data['rows']]))
    if (not dates.equals(pd.date_range(dates[0], dates[-1], freq='W-FRI'))
            or not vintage.equals(dates + pd.Timedelta(days=6))):
        raise ValueError('NFCI vintages must match the Thursday after the reference Friday')
    measures = pd.DataFrame([row[2:] for row in data['rows']], index=dates, columns=FIELDS, dtype='float64')
    return measures, vintage


def credit_eases(vintages, published):
    complete = np.isfinite(vintages).all(axis=1)
    credit = vintages.credit_current - vintages.credit_previous
    risk = vintages.risk_current - vintages.risk_previous
    event = complete & credit.le(-.020) & risk.abs().le(.020)
    return event.set_axis(published + pd.Timedelta(days=1))


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    vintages, available = load_condition_vintages()
    event = credit_eases(vintages, available).reindex(btc.index, fill_value=False)
    _BTC = event & L1.regime_daily(btc).isin((0., 1.))


def sig_credit_relief(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l197_chicago_fed_credit_subindex_easing_1d': dict(
        name='[RESEARCH] L197 Vintage-specific credit easing long', style='momentum',
        fn=sig_credit_relief, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
