"""Research-only long when broad BTC account positioning catches up.

Require three completed 23:55 UTC observations with top-account long/short
count ratio at least 1.10 and the all-account count ratio below 1.0. Buy after
the all-account ratio turns at least 1.0 while the top-account ratio remains
at least 1.10. Require positive target return and nonbear BTC and target
regimes, then execute at the next modeled daily open.
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

FAMILY_NOVELTY = ('Tests chronological agreement between top and all-account count ratios. '
                  'V119 compares account counts with position sizes inside the top cohort '
                  'and fades price disagreement. The two account series here measure count '
                  'direction without inferring order sizes or a separate retail population.')
BTC_COHORT_ACCOUNTS_SHA256 = '64686d7268a6bdc85106aacf3b4d1a2ac6b44db8fdaaa0c559b7df314921952b'
MARKET_FEATURE = ('BTC_COHORT_ACCOUNTS_SHA256', 'BTC top and all-account count ratios at 23:55 UTC')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_cohort_accounts_20261125/verified-manifest.json'
_BTC = None


def load_cohorts():
    raw = Path(os.environ.get('LONGSCOUT_BTC_COHORT_ACCOUNTS', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != BTC_COHORT_ACCOUNTS_SHA256:
        raise ValueError('expected pinned BTC account ratio input')
    data = json.loads(raw)
    fields = ['count_toptrader_long_short_ratio', 'count_long_short_ratio']
    expected = ('COMPLETE_WITH_EXPLICIT_MASKS', '23:55 UTC exact observation', fields)
    if (data['status'], data['sample'], data['fields']) != expected:
        raise ValueError('invalid BTC account ratio input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row['date'] for row in data['days']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('BTC account ratio dates must be continuous and unique')
    return pd.DataFrame({field: [row.get(field) for row in data['days']]
                         for field in fields}, index=dates).apply(pd.to_numeric, errors='raise')


def breadth_catches_up(top_accounts, all_accounts):
    valid = np.isfinite(top_accounts) & np.isfinite(all_accounts) & top_accounts.gt(0) & all_accounts.gt(0)
    disagreement = (top_accounts.ge(1.1) & all_accounts.lt(1.)).where(valid, np.nan)
    sustained = disagreement.shift().rolling(3, min_periods=3).sum().eq(3)
    return sustained & valid & top_accounts.ge(1.1) & all_accounts.ge(1.)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    source = load_cohorts()
    agreement = breadth_catches_up(source.count_toptrader_long_short_ratio, source.count_long_short_ratio)
    _BTC = agreement.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))


def sig_account_agreement(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l142_btc_account_breadth_catches_up_1d': dict(
        name='[RESEARCH] L142 BTC account breadth catches up long', style='momentum',
        fn=sig_account_agreement, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
