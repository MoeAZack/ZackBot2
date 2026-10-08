"""AUD-07 C13d: the backtest's trading day is the Africa/Cairo date of each candle CLOSE (the decision time), DST-aware
through the IANA zone, and a missing tz database fails loudly instead of guessing a fixed UTC offset.
The behaviour (halt on day D, post-midnight signal taken) is pinned by the golden cases G-DAY-CAIRO-W-01 / -S-01."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

import backtest as B

H4 = 4 * 3600


def _days(*stamps):
    return list(B.cairo_close_days(np.array(pd.to_datetime(list(stamps)), dtype='datetime64[ns]'), H4))


def test_winter_and_summer_candle_close_dates():
    # winter (UTC+2): the 20:00 UTC candle closes 00:00 UTC = 02:00 Cairo on the NEXT date; 16:00 closes 22:00 Cairo
    assert _days('2024-02-18 16:00', '2024-02-18 20:00') == [dt.date(2024, 2, 18), dt.date(2024, 2, 19)]
    # summer (UTC+3): 16:00 closes 23:00 Cairo (same date), 20:00 closes 03:00 Cairo (next date)
    assert _days('2024-07-10 16:00', '2024-07-10 20:00') == [dt.date(2024, 7, 10), dt.date(2024, 7, 11)]


def test_dst_transition_day():
    # Egypt DST 2024 starts at local midnight on Fri 26 Apr: the 20:00 UTC candle of 25 Apr closes 03:00 Cairo (UTC+3)
    # on 26 Apr; the 16:00 candle closes 22:00 Cairo (still UTC+2) on 25 Apr
    assert _days('2024-04-25 16:00', '2024-04-25 20:00') == [dt.date(2024, 4, 25), dt.date(2024, 4, 26)]


def test_no_tz_database_fails_loudly(monkeypatch):
    monkeypatch.setattr(B, 'CAIRO_TZ', 'Not/A_Zone')
    with pytest.raises(RuntimeError, match='Cairo trading day'):
        _days('2024-02-18 20:00')
