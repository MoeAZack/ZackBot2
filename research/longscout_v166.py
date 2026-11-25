"""Research-only long when BTC bid depth grows during sustained taker selling.

Use two consecutive complete days of BTC perpetual taker-sell imbalance of
at least 10%. On the second day require the matched near-book bid quantity
to grow at least 15% while ask quantity grows at most 5%. The source consists
of displayed snapshots. The rule makes no assumption about order identity
or whether the same orders replenished. Require nonbear R1 and rising target.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import longscout_v136 as L136
import strategies as S

FAMILY_NOVELTY = ('Re-registration of the unrun V163 hypothesis after an input schema failure. '
                  'Tests an increase in displayed bids during observed sell aggression, with '
                  'ask quantity as control. V124 compares near and outer depth shares, V150 '
                  'uses gross capacity, and V136 tests a reversal in executed flow alone.')
BID_RESILIENCE_MANIFEST_SHA256 = 'c1ca4058b552328308ba0915236179e299fed809f8056771fa53e1eba7e36d1e'
MARKET_FEATURE = ('BID_RESILIENCE_MANIFEST_SHA256', 'paired native BTC bid and ask depth within 1% of mid')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_bid_resilience_20261125/verified-manifest.json'
_BTC = None


def load_book():
    raw = Path(os.environ.get('LONGSCOUT_BTC_BID_RESILIENCE', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != BID_RESILIENCE_MANIFEST_SHA256:
        raise ValueError('expected pinned BTC bid/ask snapshots')
    data = json.loads(raw)
    fields = ['bid', 'ask']
    expected = ('COMPLETE_WITH_EXPLICIT_MASKS', fields, '23:55 to 24:00 UTC matched 1 percent bid and ask depth')
    if (data['status'], data['fields'], data['sample']) != expected:
        raise ValueError('invalid BTC bid/ask input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row['date'] for row in data['days']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('BTC bid/ask dates must be continuous and unique')
    return pd.DataFrame({name: [row.get(name) for row in data['days']]
                         for name in fields}, index=dates).apply(pd.to_numeric, errors='raise')


def resilient_bid(bid, ask, imbalance):
    complete = np.isfinite(bid) & bid.gt(0) & np.isfinite(ask) & ask.gt(0) & np.isfinite(imbalance)
    bid_change = bid.where(complete).pct_change(fill_method=None)
    ask_change = ask.where(complete).pct_change(fill_method=None)
    sells = imbalance.where(complete).le(-.10)
    state = sells & sells.shift(fill_value=False) & bid_change.ge(.15) & ask_change.le(.05)
    return state & ~state.shift(fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    book = load_book().reindex(btc.index)
    flow = L136.load_flow()
    q, buy = flow.quote_turnover, flow.taker_buy_quote_turnover
    valid = np.isfinite(q) & q.gt(0) & np.isfinite(buy) & buy.between(0., q)
    daily = flow.where(valid).resample('D').sum(min_count=6)
    imbalance = (2 * daily.taker_buy_quote_turnover / daily.quote_turnover - 1).reindex(btc.index)
    event = resilient_bid(book.bid, book.ask, imbalance)
    _BTC = event & L1.regime_daily(btc).isin((0., 1.))


def sig_bid_resilience(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l166_btc_bid_resilience_during_selling_1d': dict(
        name='[RESEARCH] L166 BTC bid resilience during selling long', style='momentum',
        fn=sig_bid_resilience, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
