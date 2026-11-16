"""LANE3-F3/F4 (PR #359 red-team): a final order record or a classic open-order row without executedQty is UNKNOWN,
never 'zero executed'. Synthetic responses through a fake transport; no network, no credentials."""
import json
import socket

import pytest
import requests

from newcore.ports.keys import client_id_for
from newcore.ports.venue import OrderRef, OutcomeKind, ReadKind
from newcore.venue.binance_usdm_testnet import BinanceUsdMTestnetVenue, HttpResponse, TESTNET_BASE_URL

NOW = 1_799_000_000_000
CID = client_id_for('int_' + '12' * 16)
REF = OrderRef(symbol='BTCUSDT', client_id=CID)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k): raise AssertionError('network forbidden in this test')
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(requests.sessions.Session, 'request', refuse)


class FakeCredentials:
    def api_key(self): return 'fake'
    def sign(self, payload): return 'fake'


def make_venue(*docs):
    answers = iter(docs)
    def sender(_request): return HttpResponse(200, json.dumps(next(answers)).encode())
    return BinanceUsdMTestnetVenue(environment='testnet', base_url=TESTNET_BASE_URL, sender=sender,
                                   credentials=FakeCredentials(), clock=lambda: NOW)


FINAL = dict(symbol='BTCUSDT', clientOrderId=CID, orderId=123)
ROW = dict(symbol='BTCUSDT', clientOrderId=CID, orderId=123, positionSide='LONG', type='LIMIT', origQty='1',
           stopPrice='0', reduceOnly=False, closePosition=False)


@pytest.mark.parametrize('status', ['CANCELED', 'EXPIRED', 'FILLED'])
def test_red_team_repro_final_order_missing_execution_quantity_is_unknown(status):
    """Codex #359 reproducer (CANCELED), extended to every final status."""
    outcome = make_venue(dict(FINAL, status=status)).query(REF)
    assert outcome.kind is OutcomeKind.UNKNOWN and outcome.executed_qty is None
    assert outcome.detail == 'final_without_executed_qty'


def test_final_with_explicit_zero_is_still_a_zero_fill():
    outcome = make_venue(dict(FINAL, status='CANCELED', executedQty='0')).query(REF)
    assert outcome.kind is OutcomeKind.FINAL and outcome.executed_qty == 0


def test_final_with_partial_fill_is_unchanged():
    outcome = make_venue(dict(FINAL, status='CANCELED', executedQty='0.4', avgPrice='100')).query(REF)
    assert outcome.kind is OutcomeKind.FINAL and str(outcome.executed_qty) == '0.4'


@pytest.mark.parametrize('status', ['PARTIALLY_FILLED', 'NEW'])
def test_red_team_repro_open_classic_row_missing_execution_quantity_is_unknown(status):
    """Codex #359 reproducer (PARTIALLY_FILLED); a NEW row cannot prove zero either - Binance always sends the field."""
    outcome = make_venue([dict(ROW, status=status)], []).open_orders('BTCUSDT')
    assert outcome.kind is ReadKind.UNKNOWN and outcome.value is None
    assert outcome.detail == 'open_without_executed_qty'


def test_open_classic_row_with_execution_quantity_gives_remaining():
    outcome = make_venue([dict(ROW, status='PARTIALLY_FILLED', executedQty='0.25')], []).open_orders('BTCUSDT')
    assert outcome.kind is ReadKind.OK and str(outcome.value[0].qty) == '0.75'


def test_open_algo_row_without_execution_quantity_is_still_read():
    """An untriggered algo (conditional) order carries no executedQty: unchanged."""
    algo = dict(symbol='BTCUSDT', clientAlgoId=CID, algoId=77, positionSide='LONG', orderType='STOP_MARKET',
                algoStatus='NEW', quantity='1', triggerPrice='90', reduceOnly=False, closePosition=False)
    outcome = make_venue([], [algo]).open_orders('BTCUSDT')
    assert outcome.kind is ReadKind.OK and str(outcome.value[0].qty) == '1'
