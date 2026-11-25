"""Research-only crypto long when investors replace dealers at US bill auctions.

For regular Treasury 4-week bill auctions, require the fraction of accepted
competitive awards from direct and indirect bidders to be 8 percentage
points above its prior 12-auction median. Keep competitive amount within
15% of its prior median, with bid-to-cover at or above its prior median.
Require each auction gap to be 5-9 days and wait two extra UTC days after
auction. Buy a rising nonbear target in nonbear BTC.
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

FAMILY_NOVELTY = ('Uses changes in accepted awards by bidder type in US bill primary auctions. '
                  'V173 uses secondary sovereign real yields, V175 overnight repo dispersion, '
                  'and V182 weekly Fed balance-sheet liquidity.')
BILL_AUCTION_AWARDS_SHA256 = '411714e35c7ef015623fced937be06374e8223b287c191e8d9fbbcf558fb459e'
MARKET_FEATURE = ('BILL_AUCTION_AWARDS_SHA256', 'US Treasury 4-week bills competitive award composition')
MARKET_FEATURE_SOURCE = 'United States Treasury Fiscal Data, Treasury Securities Auctions Data'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_bill_auctions_20261125/manifest.json'
FIELDS = ['primary_dealer_accepted', 'direct_bidder_accepted', 'indirect_bidder_accepted',
          'comp_accepted', 'bid_to_cover_ratio', 'pdf_filenm_comp_results']
_BTC = None


def load_bill_auctions():
    raw = Path(os.environ.get('LONGSCOUT_BILL_AUCTIONS', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != BILL_AUCTION_AWARDS_SHA256:
        raise ValueError('expected pinned US Treasury bill auction records')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', 'auction', FIELDS):
        raise ValueError('invalid auction archive contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.is_unique or not dates.is_monotonic_increasing or not dates.equals(dates.normalize()):
        raise ValueError('auction dates must be unique sorted UTC day labels')
    return pd.DataFrame([row[1:-1] for row in data['rows']], index=dates, columns=FIELDS[:-1], dtype=float)


def investor_share_pulse(auctions):
    valid = np.isfinite(auctions).all(axis=1) & auctions.ge(0).all(axis=1)
    nondealer_awards = auctions.direct_bidder_accepted + auctions.indirect_bidder_accepted
    valid &= (nondealer_awards + auctions.primary_dealer_accepted).eq(auctions.comp_accepted)
    total = auctions.comp_accepted.where(valid & auctions.comp_accepted.gt(0))
    cover = auctions.bid_to_cover_ratio.where(valid & auctions.bid_to_cover_ratio.gt(0))
    share = nondealer_awards / total
    complete = auctions.index.to_series().diff().dt.days.between(5, 9).rolling(12, min_periods=12).sum().eq(12)
    past_share = share.shift().rolling(12, min_periods=12).median()
    past_total = total.shift().rolling(12, min_periods=12).median()
    past_cover = cover.shift().rolling(12, min_periods=12).median()
    has_bidder_switch = share.sub(past_share).ge(.08)
    similar_size = (total / past_total).between(.85, 1.15)
    pulse = complete & has_bidder_switch & similar_size & cover.ge(past_cover)
    return pulse.set_axis(pulse.index + pd.Timedelta(days=2))


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    pulse = investor_share_pulse(load_bill_auctions()).reindex(btc.index, fill_value=False)
    _BTC = pulse & L1.regime_daily(btc).isin((0., 1.))


def sig_auction_nondealer_demand(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    eligible = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (eligible & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l205_treasury_bill_primary_auction_demand_1d': dict(
        name='[RESEARCH] L205 Stronger nondealer demand at regular US 4-week bill auctions',
        style='momentum', fn=sig_auction_nondealer_demand, sides='long', tf='1d', research_only=True,
        params={}, mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
