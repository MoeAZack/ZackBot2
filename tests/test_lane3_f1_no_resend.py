"""LANE3-F1 (PR #359 red-team): an ambiguous order POST is never sent a second time on a "not found" lookup.
Offline: fake transport only, no network, no credentials, no account."""
import os, sys, socket, tempfile
import pytest
import requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import binance_client as BC                                                # noqa: E402
from test_outage import Resp, Clock, client, _nosleep                     # noqa: E402

NOT_FOUND = lambda: Resp(400, {'code': -2013, 'msg': 'Order does not exist.'})


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k): raise AssertionError('network forbidden in this test')
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(requests.sessions.Session, 'request', refuse)


def scripted(post_answers, get_answers):
    """POSTs and GETs answered from their own scripts (the last entry repeats)."""
    def answer(method, path, params):
        seq = post_answers if method == 'POST' else get_answers
        r = seq.pop(0) if len(seq) > 1 else seq[0]
        return r() if callable(r) else r
    return answer


def posts(calls): return [c for c in calls if c[0] == 'POST']
def gets(calls): return [c for c in calls if c[0] == 'GET']


def test_red_team_repro_one_absent_lookup_is_not_a_resend(monkeypatch):
    """Codex #359 reproducer (verbatim logic): the first POST was matched, its answer and the lookup are delayed."""
    c = object.__new__(BC.Futures)
    sent = []
    def request(method, path, params, **kwargs):
        assert method == 'POST'
        sent.append(dict(params))
        if len(sent) == 1: raise requests.Timeout('synthetic delayed acknowledgement')
        return dict(status='FILLED', executedQty='1', avgPrice='100')
    monkeypatch.setattr(c, '_req', request)
    monkeypatch.setattr(c, 'get_order', lambda *a: None)
    monkeypatch.setattr(BC.time, 'sleep', lambda s: None)
    with pytest.raises(BC.AmbiguousOrder) as ei:
        c._order(dict(symbol='BTCUSDT', type='MARKET', quantity='1', newClientOrderId='lane3-synthetic-replay'), adds_risk=True)
    assert len(sent) == 1, 'one absent lookup cannot prove an ambiguous POST never executed'
    assert ei.value.tag == 'c:lane3-synthetic-replay' and 'not re-sent' in str(ei.value)


@pytest.mark.parametrize('fn', ['open', 'close'])
def test_market_never_found_raises_ambiguous_with_one_post_and_every_lookup(monkeypatch, fn):
    clk = Clock(); _nosleep(monkeypatch, clk)
    c, calls = client(scripted([requests.Timeout('lost')], [NOT_FOUND]), clk)
    with pytest.raises(BC.AmbiguousOrder) as ei:
        getattr(c, fn)('BTCUSDT', 'LONG', '0.01')
    assert len(posts(calls)) == 1                                  # never a second POST
    assert len(gets(calls)) == 4                                   # every bounded lookup still went out
    cid = posts(calls)[0][2]['newClientOrderId']
    assert all(g[2]['origClientOrderId'] == cid for g in gets(calls))
    assert ei.value.tag == f'c:{cid}' and '4 lookups' in str(ei.value)


def test_late_visible_order_is_returned_after_not_found_lookups(monkeypatch):
    """Not found, not found, then the record appears: its FINAL record is the answer - no second POST."""
    clk = Clock(); _nosleep(monkeypatch, clk)
    rec = Resp(200, {'orderId': 9, 'status': 'FILLED', 'executedQty': '0.01', 'avgPrice': '100'})
    c, calls = client(scripted([requests.Timeout('lost')], [NOT_FOUND, NOT_FOUND, rec]), clk)
    o = c.open('BTCUSDT', 'LONG', '0.01')
    assert o['orderId'] == 9 and len(posts(calls)) == 1 and len(gets(calls)) == 3


def test_stop_post_lost_and_not_found_stays_owned_not_resent(monkeypatch):
    """A resting stop: a resend would either duplicate it or be refused as a duplicate id (and then be wrongly treated
    as 'refused, nothing placed'). Unconfirmed -> AmbiguousOrder with its client id, one POST."""
    clk = Clock(); _nosleep(monkeypatch, clk)
    c, calls = client(scripted([requests.Timeout('lost')], [NOT_FOUND]), clk)
    with pytest.raises(BC.AmbiguousOrder) as ei:
        c.stop('BTCUSDT', 'LONG', '0.01', '90', cid='zbstoplost')
    assert len(posts(calls)) == 1 and ei.value.tag == 'c:zbstoplost'


def test_lookup_errors_then_not_found_still_one_post(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk)
    c, calls = client(scripted([requests.Timeout('lost')], [requests.ConnectionError('x'), NOT_FOUND]), clk)
    with pytest.raises(BC.AmbiguousOrder):
        c.open('BTCUSDT', 'LONG', '0.01')
    assert len(posts(calls)) == 1


def test_accepted_first_answer_unchanged(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk)
    c, calls = client(scripted([Resp(200, {'orderId': 3, 'status': 'FILLED', 'executedQty': '0.01', 'avgPrice': '1'})],
                               [NOT_FOUND]), clk)
    assert c.open('BTCUSDT', 'LONG', '0.01')['orderId'] == 3 and len(calls) == 1
