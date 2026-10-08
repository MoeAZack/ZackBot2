"""Typed InstrumentRules from the repo's exchange-filter snapshot (data/exchange_rules_<env>.json, schema
zackbot.exchange_rules/1: per symbol step, min_qty, tick, min_notional). Numbers are read as exact Decimals from the JSON
text (never through float)."""
from __future__ import annotations

import json
from decimal import Decimal

from newcore.domain import Capability, InstrumentId, InstrumentRules, Venue

MAX_QTY = Decimal('1000000000')          # the snapshot carries no max_qty: an upper bound that never binds in S1


def load_rules(path, symbols):
    with open(path, encoding='utf-8') as fh:
        doc = json.load(fh, parse_float=Decimal, parse_int=Decimal)
    if doc.get('schema') != 'zackbot.exchange_rules/1':
        raise ValueError(f'{path}: not a zackbot.exchange_rules/1 snapshot')
    out = {}
    for s in symbols:
        r = doc['symbols'][s]
        out[s] = InstrumentRules(instrument=InstrumentId(venue=Venue.BINANCE_USDM, symbol=s), tick_size=r['tick'],
                                 step_size=r['step'], min_qty=r['min_qty'], max_qty=MAX_QTY,
                                 min_notional=r['min_notional'],
                                 capabilities=(Capability.HEDGE_MODE, Capability.STOP_MARKET, Capability.REDUCE_ONLY))
    return out
