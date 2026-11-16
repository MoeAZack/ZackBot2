"""Offline mainnet-readiness repros; no exchange, credentials or runtime access."""
import socket
import pytest
import requests

import binance_client as BC
import engine as E


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('network forbidden in lane3')
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(requests.sessions.Session, 'request', refuse)


def test_ambiguous_market_is_not_resent_after_one_not_found_lookup(monkeypatch):
    """A filled order's CID is only unique among open orders, not a replay key."""
    client = object.__new__(BC.Futures)
    sent = []

    def request(method, path, params, **kwargs):
        assert method == 'POST'
        sent.append(dict(params))
        if len(sent) == 1:
            # Matching accepted the first request; its answer and lookup are delayed.
            raise requests.Timeout('synthetic delayed acknowledgement')
        return dict(status='FILLED', executedQty='1', avgPrice='100')

    monkeypatch.setattr(client, '_req', request)
    monkeypatch.setattr(client, 'get_order', lambda *args: None)
    monkeypatch.setattr(BC.time, 'sleep', lambda seconds: None)
    try:
        client._order(dict(symbol='BTCUSDT', type='MARKET', quantity='1',
                           newClientOrderId='lane3-synthetic-replay'), adds_risk=True)
    except BC.AmbiguousOrder:
        pass
    assert len(sent) == 1, 'one absent lookup cannot prove an ambiguous POST never executed'


def test_same_second_manual_fills_preserve_both_lots(monkeypatch):
    """Manual entries can repeat a symbol/side; identities must remain distinct."""
    engine = object.__new__(E.Engine)
    engine.rules = {'BTCUSDT': {'tick': .01}}
    engine.S = {}
    engine.state = {'lots': {}}
    # Keep the real lot construction and identity path; replace every side effect.
    engine.save_state = lambda: True
    engine._ml_link = lambda *args: None
    engine._replace_stop = lambda lot: True
    engine._tp_wanted = lambda lot: False
    engine.log_trade = lambda **kwargs: None
    engine.notify = lambda text: None
    monkeypatch.setattr(E.time, 'time', lambda: 1_799_000_000.25)
    plan = dict(sym='BTCUSDT', side='LONG', sl=None,
                manual=True, g={}, atr=2., eq=10000., risk_usd=10., reason='synthetic',
                px=100., stop_dist=5.)
    assert engine._create_lot(plan, 1., 100.)
    assert engine._create_lot(plan, 2., 101.)
    assert len(engine.state['lots']) == 2, 'second manual fill overwrote the first lot'
    assert sum(lot['qty'] for lot in engine.state['lots'].values()) == 3.
