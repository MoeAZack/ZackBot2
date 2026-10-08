"""AUD-07 C11: "time exit after N candles" holds exactly N candles (the fill candle counts as candle 1), in the backtester
for every N and side, and in the engine whatever the entry-cycle latency. Cowork's repro: the backtest held N+1 for
N = 1, 2, 3, 4, 6 (long and short); the engine held N+1 with a 45 s entry delay and N at 15 s."""
import pytest

from _aud07_twin import market, run_bt, run_engine, shape

SIG = 279                         # signal on the close of 279 -> market entry at the open of 280


def _slot(n, side):
    return dict(key='ema_st', risk=0.02, max_pos=1, symbols=['SOLUSDT'], sides='long' if side == 1 else 'short',
                mgmt={'stop_atr': 30, 'max_bars': n})


def _raw():
    return market({'SOLUSDT': (100, 0.5, {})})


@pytest.mark.parametrize('side', [1, -1])
@pytest.mark.parametrize('n', [1, 2, 3, 4, 6])
def test_backtest_holds_exactly_n_candles(n, side):
    rows, _ = run_bt(_raw(), _slot(n, side), [(SIG, 'SOLUSDT', 'le' if side == 1 else 'se')])
    assert shape(rows) == [('SOLUSDT', side, 280, 280 + n - 1, 'time')], rows


@pytest.mark.parametrize('n,side,delay', [(1, 1, 15), (1, 1, 45), (1, -1, 45), (4, 1, 15), (4, 1, 45), (4, -1, 45)])
def test_engine_holds_exactly_n_candles_whatever_the_entry_delay(n, side, delay):
    rows, _ = run_engine(_raw(), _slot(n, side), [(SIG, 'SOLUSDT', 'le' if side == 1 else 'se')], entry_delay_s=delay)
    assert shape(rows) == [('SOLUSDT', side, 280, 280 + n - 1, 'time')], rows
