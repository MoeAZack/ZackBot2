"""AUD-07 C13c: an armed trailing entry holds a max_pos slot from the moment it is ARMED until it fills or expires, in the
backtester as in the engine. Occupancy follows arming order (slot symbol order within one candle, earlier candles first),
not the order the backtester happens to iterate symbols in. Each case runs through both legacy models and they must give
the same trades (symbol, side, entry/exit candle, reason).

Market: SOL (100, ATR 1) arms and keeps falling (never rebounds 0.5 ATR, the trailing entry expires after 3 bars);
XRP (200, ATR 2) rebounds 2 % in the candle after its arming. Trailing entry 0.5 ATR, 3 bars; XRP exit signal at 290."""
import pytest

from _aud07_twin import market, run_bt, run_engine, shape

SOL_FALL = {280: (100, 100, 99, 99), 281: (99, 99, 98.01, 98.01), 282: (98.01, 98.01, 97.0299, 97.0299),
            283: (97.0299, 97.0299, 96.0596, 96.0596), 284: (96.0596, 96.0596, 95.099, 95.099)}


def _raw(xrp_rebound_bar=280):
    return market({'SOLUSDT': (100, 0.5, SOL_FALL), 'XRPUSDT': (200, 1, {xrp_rebound_bar: (200, 204, 199, 203)})})


def _slot(order, max_pos=1):
    return dict(key='ema_st', risk=0.02, max_pos=max_pos, symbols=list(order), sides='long', mgmt={'stop_atr': 30},
                trail_entry={'dev_atr': 0.5, 'max_bars': 3})


XRP_TRADE = [('XRPUSDT', 1, 280, 290, 'signal')]
CASES = {
    # both arm on the close of 279: the first in slot order holds the only slot
    'SOL first, both armed': (('SOLUSDT', 'XRPUSDT'), 1, 280, [(279, 'SOLUSDT', 'le'), (279, 'XRPUSDT', 'le')], []),
    'XRP first, both armed': (('XRPUSDT', 'SOLUSDT'), 1, 280, [(279, 'SOLUSDT', 'le'), (279, 'XRPUSDT', 'le')], XRP_TRADE),
    # arming order across candles beats symbol order: SOL armed on 279 still holds the slot when XRP (first in the slot
    # order) signals on 280 and rebounds in 281
    'SOL armed earlier, XRP first in order': (('XRPUSDT', 'SOLUSDT'), 1, 281, [(279, 'SOLUSDT', 'le'), (280, 'XRPUSDT', 'le')], []),
    # controls: two slots, and XRP alone
    'max_pos 2': (('SOLUSDT', 'XRPUSDT'), 2, 280, [(279, 'SOLUSDT', 'le'), (279, 'XRPUSDT', 'le')], XRP_TRADE),
    'max_pos 2, XRP first': (('XRPUSDT', 'SOLUSDT'), 2, 280, [(279, 'SOLUSDT', 'le'), (279, 'XRPUSDT', 'le')], XRP_TRADE),
    'XRP signal only': (('SOLUSDT', 'XRPUSDT'), 1, 280, [(279, 'XRPUSDT', 'le')], XRP_TRADE),
}


@pytest.mark.parametrize('name', list(CASES))
def test_armed_trailing_entry_holds_its_slot_in_both_models(name):
    order, max_pos, rebound, sigs, want = CASES[name]
    want = [(s, sd, i_in + (rebound - 280), i_out, why) for s, sd, i_in, i_out, why in want]
    sigs = sigs + [(290, 'XRPUSDT', 'lx')]
    bt, attrs = run_bt(_raw(rebound), _slot(order, max_pos), sigs)
    eng, _ = run_engine(_raw(rebound), _slot(order, max_pos), sigs)
    assert shape(bt) == want, ('backtest', bt)
    assert shape(eng) == want, ('engine', eng)
    if not want and len(sigs) > 2:                      # the refused arming is counted (canonical blocked.max_pos)
        assert attrs['blocked'].get('max_pos', 0) >= 1, attrs['blocked']
