"""AUD-07 C13d: the daily-loss halt follows the Cairo trading day of each candle-CLOSE decision, in the backtester as in
the engine. Cowork: 4 of 14 variants diverged, all on a candle straddling Cairo midnight. Here, for a winter day (UTC+2),
a summer day (UTC+3) and both 2024 DST switch days (26 Apr starts UTC+3, 31 Oct ends it):

SOL (risk 10 %) is stopped on the 08:00 UTC candle of day D for about -1.06 R = -10.6 % > the 8 % halt. XRP then signals on
- the 20:00 UTC candle, which closes 00:00 UTC = 02:00 / 03:00 Cairo on D+1 (straddles midnight): the halt of D no longer
  applies -> XRP is taken (entry at the open of the next candle, signal exit 3 candles later);
- control: the 16:00 UTC candle, which closes 20:00 UTC = 22:00 / 23:00 Cairo, still day D -> refused by the halt.
Both models must give the same trades."""
import pandas as pd
import pytest

from _aud07_twin import market, run_bt, run_engine, shape

DAYS = {'winter': '2024-02-18', 'summer': '2024-07-10', 'dst start': '2024-04-25', 'dst end': '2024-10-31'}
BACK = 47                          # the series starts 47 days before D: D 00:00 UTC is bar 282, 08:00 is 284


def _case(day, straddle):
    start = pd.Timestamp(day) - pd.Timedelta(days=BACK)
    raw = market({'SOLUSDT': (100, 0.5, {284: (100, 100.1, 90, 92)}), 'XRPUSDT': (200, 1, {})}, start=str(start.date()))
    assert raw['SOLUSDT'].t[284] == pd.Timestamp(day) + pd.Timedelta(hours=8)
    slot = dict(key='ema_st', risk=0.10, max_pos=2, symbols=['SOLUSDT', 'XRPUSDT'], sides='long', mgmt={'stop_atr': 2})
    x = 287 if straddle else 286
    sigs = [(283, 'SOLUSDT', 'le'), (x, 'XRPUSDT', 'le'), (290, 'XRPUSDT', 'lx')]
    want = [('SOLUSDT', 1, 284, 284, 'stop')] + ([('XRPUSDT', 1, 288, 290, 'signal')] if straddle else [])
    return raw, slot, sigs, want


@pytest.mark.parametrize('straddle', [True, False], ids=['straddles-midnight', 'control-same-day'])
@pytest.mark.parametrize('day', list(DAYS))
def test_halt_clears_on_the_cairo_day_of_the_candle_close(day, straddle):
    raw, slot, sigs, want = _case(DAYS[day], straddle)
    bt, _ = run_bt(raw, slot, sigs)
    assert shape(bt) == want, ('backtest', bt)
    eng, _ = run_engine(raw, slot, sigs)
    assert shape(eng) == want, ('engine', eng)
