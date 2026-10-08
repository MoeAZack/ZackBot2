"""AUD-07 C13g: a filled exchange stop is journalled at the exchange's ACTUAL fill price (incl. exit slippage), not at the
stop price. A stop-market gapped at the open fills at the open: the bot's history must show the -2.57 R the account lost,
not -1.05 R. Unknown fill price -> the stop price, and the journal note says so."""
import csv, os

import pytest

import binance_client as BC
from test_aud07_replay_gap import _run


def _journal(r):
    return r['history'][0]


@pytest.mark.parametrize('side', [1, -1])
def test_gapped_stop_is_journalled_at_the_fill_not_the_stop(side):
    r = _run(side, (95.0, 95.2, 94.8, 95.0))                         # stop 98.02 (long); candle opens at 95
    bt = r['bt_trades']
    assert len(bt) == 1 and len(r['history']) == 1, (bt, r['history'])
    h = _journal(r)
    eng_r = float(h['pnl']) / float(h['risk_usd'])
    assert bt.R.iloc[0] == pytest.approx(-2.57, abs=0.015)
    assert eng_r == pytest.approx(bt.R.iloc[0], abs=0.02), f'engine journal R {eng_r:.3f} vs backtest {bt.R.iloc[0]:.3f}'
    fill = (95.0 if side == 1 else 105.0) * (1 - side * 0.0002)
    assert float(h['exit']) == pytest.approx(fill, rel=1e-9), h
    assert r['metrics']['med_dr'] <= 0.02, r['metrics']


@pytest.mark.parametrize('side', [1, -1])
def test_stop_inside_the_candle_is_journalled_with_its_exit_slippage(side):
    r = _run(side, (100.0, 100.2, 97.5, 97.8))
    h = _journal(r)
    stop = 98.02 if side == 1 else 101.98
    assert float(h['exit']) == pytest.approx(stop * (1 - side * 0.0002), rel=1e-9), h
    assert float(h['pnl']) / float(h['risk_usd']) == pytest.approx(r['bt_trades'].R.iloc[0], abs=0.01)


class _Req:
    def __init__(self, answers): self.answers, self.calls = list(answers), []

    def __call__(self, method, path, params=None, signed=False, retry=None, critical=False):
        self.calls.append((path, dict(params or {}))); return self.answers.pop(0)


@pytest.mark.parametrize('tag,answers,want', [
    ('o:11', [{'status': 'FILLED', 'avgPrice': '94.981'}], 94.981),
    ('o:11', [{'status': 'CANCELED', 'avgPrice': '0'}], None),                            # nothing filled -> unknown
    ('a:7', [{'algoStatus': 'FINISHED', 'actualPrice': '94.98', 'actualOrderId': 55}], 94.98),
    ('a:7', [{'algoStatus': 'FINISHED', 'actualPrice': '0', 'actualOrderId': 55}, {'avgPrice': '94.97'}], 94.97),
    ('ac:x', [{'algoStatus': 'TRIGGERING'}], None),
    ('', [], None),
])
def test_client_reads_the_stop_fill_price(tag, answers, want):
    f = BC.Futures.__new__(BC.Futures)
    f._req = _Req(answers)
    assert f.stop_fill_price('SOLUSDT', tag) == want
    assert not f._req.answers                                                             # every planned read was made


def test_unknown_fill_price_books_the_stop_price_and_says_so():
    import engine as E

    class T:
        def stop_fill_price(self, s, tag, retry=None): raise BC.BinanceError(-1000, 'boom')
    e = E.Engine.__new__(E.Engine); e.trade = T()
    lot = dict(stop=98.02, stop_id='o:1', sleeve='G')
    assert e._stop_fill_px('SOLUSDT', lot) == (98.02, 'fill price unconfirmed: booked at the stop price')
    e.trade = object()                                                                    # a client without the read
    assert e._stop_fill_px('SOLUSDT', lot)[0] == 98.02
