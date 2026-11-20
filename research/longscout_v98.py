"""V98 daily flow hypotheses, preregistered in docs/research/longscout_flow_hypotheses.md.

L98: first ready day with 2*taker-buy-quote/quote-1 <= -0.20 and
close/open-1 >= 0.01, then require R1 rising/sideways.
R98: first ready day whose average trade value is <= half the previous
20-day median, quote volume >= twice its prior-20-day median, and absolute
close-to-close return >= 0.05; fade the day's direction in R1 sideways.
Readiness requires 22 consecutive complete UTC days with positive finite
quote volume and trade count and finite taker-buy quote in [0, quote]. Both
are daily-close signals, next-day-open fills; 2 ATR stop, 2R, 7-bar cap.
Research-only, default OFF; no data can load before the report hash is locked.
"""
import hashlib
import io
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1

DATA_DIR = Path('C:/Dev/ZackBot_research_cache/longscout_codex_flow_20261120')
# The independent integration owner must set this only after a COMPLETE release.
DATA_REPORT_SHA256 = None
_FLOW = None


def set_panel(panel):
    """Load and verify the entire locked release; panel must be the full universe."""
    global _FLOW
    _FLOW = None
    if not isinstance(DATA_REPORT_SHA256, str) or not re.fullmatch(r'[0-9a-f]{64}', DATA_REPORT_SHA256):
        raise RuntimeError('flow release report hash not locked')
    report_bytes = (DATA_DIR / 'report.json').read_bytes()
    if hashlib.sha256(report_bytes).hexdigest() != DATA_REPORT_SHA256:
        raise ValueError('flow report hash mismatch')
    report = json.loads(report_bytes)
    entries = report.get('symbols')
    if report.get('status') != 'COMPLETE' or report.get('probe') is not False:
        raise ValueError('flow release is not COMPLETE')
    if not isinstance(entries, dict) or len(entries) != 166 or 'BTCUSDT' not in entries:
        raise ValueError('flow release must contain all 166 symbols')
    if set(panel) != set(entries):
        raise ValueError('panel universe differs from flow release')
    loaded = {}
    for symbol, meta in entries.items():
        if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9]+', symbol):
            raise ValueError('invalid release symbol')
        if not isinstance(meta, dict) or meta.get('status') != 'PASS':
            raise ValueError('non-passing symbol in flow release')
        blob = (DATA_DIR / 'daily' / (symbol + '.csv')).read_bytes()
        if hashlib.sha256(blob).hexdigest() != meta.get('daily_sha256'):
            raise ValueError('flow CSV hash mismatch: ' + symbol)
        flow = pd.read_csv(io.BytesIO(blob))
        if list(flow.columns) != ['t', 'trade_count', 'taker_buy_qv']:
            raise ValueError('invalid flow CSV columns: ' + symbol)
        ts = pd.to_numeric(flow.t, errors='raise')
        if ts.isna().any() or (ts % 86400000 != 0).any():
            raise ValueError('invalid daily UTC timestamps: ' + symbol)
        dates = pd.DatetimeIndex(pd.to_datetime(ts, unit='ms'))
        if not dates.is_unique or not dates.is_monotonic_increasing or len(flow) != meta.get('days'):
            raise ValueError('invalid release dates: ' + symbol)
        flow.index = dates
        loaded[symbol] = flow[['trade_count', 'taker_buy_qv']]
    _FLOW = loaded


def daily_flags(d, flow):
    """Calculate ungated daily event edges on native completed daily bars."""
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    if not dates.is_unique or not dates.is_monotonic_increasing or (dates != dates.normalize()).any():
        raise ValueError('daily timestamps must be sorted unique UTC midnights')
    f = flow.reindex(dates)
    if not dates.isin(flow.index).all():
        raise ValueError('daily price bar missing from locked flow release')
    q = pd.to_numeric(pd.Series(d.qv.to_numpy(), index=dates), errors='coerce')
    o = pd.to_numeric(pd.Series(d.o.to_numpy(), index=dates), errors='coerce')
    c = pd.to_numeric(pd.Series(d.c.to_numpy(), index=dates), errors='coerce')
    count = pd.to_numeric(f.trade_count, errors='coerce')
    buy = pd.to_numeric(f.taker_buy_qv, errors='coerce')
    valid = (np.isfinite(q) & (q > 0) & np.isfinite(count) & (count > 0)
             & (count == np.floor(count)) & np.isfinite(buy) & (buy >= 0) & (buy <= q)
             & np.isfinite(o) & (o > 0) & np.isfinite(c) & (c > 0))
    consecutive = dates.to_series().diff().eq(pd.Timedelta(days=1)).rolling(21, min_periods=21).sum().eq(21)
    ready = valid.rolling(22, min_periods=22).sum().eq(22) & consecutive
    # Algebraic comparisons preserve exact inclusive boundaries without
    # subtracting near-equal floats (40/100 is exactly the intended I=-.20).
    absorption = ready & (buy <= 0.40 * q) & (c >= 1.01 * o)
    a = q / count
    ret = c / c.shift() - 1
    event = (ready & (a <= 0.5 * a.shift().rolling(20, min_periods=20).median())
             & (q >= 2 * q.shift().rolling(20, min_periods=20).median()) & (ret.abs() >= 0.05))
    edge = event & ~event.shift(fill_value=False)
    return dict(absorption=absorption & ~absorption.shift(fill_value=False),
                long=edge & (ret < 0), short=edge & (ret > 0))


def entries(d, ctx, mode):
    if _FLOW is None:
        raise RuntimeError('verified flow release not loaded')
    if not isinstance(ctx, dict) or not isinstance(ctx.get('research_symbol'), str):
        raise ValueError('research_symbol context is required')
    symbol = ctx['research_symbol']
    if symbol not in _FLOW:
        raise ValueError('unknown research_symbol')
    flags = daily_flags(d, _FLOW[symbol])
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    regime = L1.regime_daily(pd.Series(d.c.to_numpy(), index=dates)).to_numpy()
    out = {key: pd.Series(False, index=d.index) for key in ('le', 'se', 'lx', 'sx')}
    if mode == 'absorption':
        out['le'] = S.X(pd.Series(flags['absorption'].to_numpy() & np.isin(regime, (0., 1.)), index=d.index))
    else:
        out['le'] = S.X(pd.Series(flags['long'].to_numpy() & (regime == 0), index=d.index))
        out['se'] = S.X(pd.Series(flags['short'].to_numpy() & (regime == 0), index=d.index))
    return out


def sig_absorption(d, p, ctx): return entries(d, ctx, 'absorption')
def sig_fragmentation(d, p, ctx): return entries(d, ctx, 'fragmentation')


CANDIDATES = {
    'l98_passive_absorption_1d': dict(name='[RESEARCH] L98 passive absorption', style='momentum',
        fn=sig_absorption, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2., tp_r=2., max_bars=7)),
    'r98_trade_fragmentation_1d': dict(name='[RESEARCH] R98 trade fragmentation range', style='range',
        fn=sig_fragmentation, sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2., tp_r=2., max_bars=7)),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
