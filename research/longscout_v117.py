"""Research-only long after negative funding and upside price recovery.

Enter long when the two latest usable funding events for the contract
both paid at most -2 basis points and the close breaks the prior six 4h
highs. Require the first full condition under rising or sideways coin and
BTC R1. A print is usable only after its recorded exchange timestamp.
Lose feature availability when the latest event is stale or the preceding
payment is missing. Funding is a feature. Execution keeps the frozen costs.
"""
from functools import lru_cache
import hashlib
import io
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Uses actual settled native funding as a predictor after checking its cadence. '
                  'V98 is a taker-flow study and used no signed funding signal. Other LongScout '
                  'reclaim families use OHLCV only.')
RUNNER = 'longscout_flow_run.py'
DATA_MANIFEST_SHA256 = '324c4ebf7f05798f74e74ed9772310ea44c0216efe1db8b4d6786950a619e691'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_funding_20261125/masked'
HOUR_MS = 3_600_000
set_btc = L1.set_btc


@lru_cache(maxsize=1)
def funding_manifest():
    folder = Path(os.environ.get('LONGSCOUT_FUNDING_DATA', DEFAULT_DATA))
    raw = (folder / 'manifest.json').read_bytes()
    if DATA_MANIFEST_SHA256 is None or hashlib.sha256(raw).hexdigest() != DATA_MANIFEST_SHA256:
        raise ValueError('expected pinned funding manifest')
    manifest = json.loads(raw)
    if (manifest['status'] != 'COMPLETE' or manifest['expected_symbols'] != 166
            or manifest['successes'] != 166 or manifest['gap_policy'] != 'MASK_OUTSIDE_CADENCE'):
        raise ValueError('funding manifest is incomplete')
    return folder, manifest['symbols']


def set_panel(panel):
    _, symbols = funding_manifest()
    if set(panel) != set(symbols):
        raise ValueError('funding and execution universes differ')


def load_funding(symbol):
    root, symbols = funding_manifest()
    spec = symbols[symbol]
    raw_data = (root / f'{symbol}.csv').read_bytes()
    if hashlib.sha256(raw_data).hexdigest() != spec['csv_sha256']:
        raise ValueError('funding CSV hash differs from manifest')
    result = pd.read_csv(io.BytesIO(raw_data))
    if len(result) != spec['rows']:
        raise ValueError('funding CSV row count differs from manifest')
    return result


def negative_funding_mask(candle_close_ms, funding):
    stamp = funding.calc_time.to_numpy(dtype=np.int64)
    interval = funding.funding_interval_hours.to_numpy(dtype=np.int64)
    rate = funding.last_funding_rate.to_numpy(dtype=float)
    if not (np.all(np.diff(stamp) > 0) and np.all((interval >= 1) & (interval <= 24))
            and np.all(np.isfinite(rate))):
        raise ValueError('invalid frozen funding rows')
    previous = np.searchsorted(stamp, candle_close_ms, side='left') - 1
    ready = previous >= 1
    known = previous[ready]
    pre = known - 1
    age = candle_close_ms[ready] - stamp[known]
    gap = stamp[known] - stamp[pre]
    eligible = np.zeros(len(candle_close_ms), dtype=bool)
    eligible[ready] = ((age <= interval[known] * HOUR_MS + 60_000)
                       & (gap >= HOUR_MS - 60_000)
                       & (gap <= np.maximum(interval[known], interval[pre]) * HOUR_MS + 60_000)
                       & (rate[known] <= -.0002) & (rate[pre] <= -.0002))
    return eligible


def signal(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    contiguous = dates.to_series().diff().eq(pd.Timedelta(hours=4)).rolling(6).sum().eq(6).to_numpy()
    closed_ms = (dates + pd.Timedelta(hours=4)).as_unit('ms').asi8
    funding = load_funding(L101._context_symbol(ctx))
    negative = negative_funding_mask(closed_ms, funding)
    breakout = d.c.gt(d.h.shift().rolling(6, min_periods=6).max()).to_numpy()
    condition = (negative & breakout & contiguous & np.isin(L1.coin_regime(d), (0., 1.))
                 & np.isin(L1.btc_regime(d), (0., 1.)))
    entry = pd.Series(condition, index=d.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(entry & ~entry.shift(fill_value=False)), se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l117_negative_funding_squeeze_4h': dict(
        name='[RESEARCH] L117 negative-funding squeeze long', style='momentum', fn=signal,
        sides='long', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=18),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
