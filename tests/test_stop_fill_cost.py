"""Protective-stop fill cost path (Codex 6243423221): a stop that fired on Binance is booked from its own execution record -
the exchange's average price, a fill record with a cost_ref and the actual userTrades commission - never from the
configured stop price with no cost record. An unreadable / ambiguous record waits STOP_FILL_WAIT_S, then the trade is
booked at the stop price marked exit_px_unconfirmed, so its row stays INCOMPLETE (no zero fee, no invented price)."""
import json, os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety import mk_engine, opened                                # noqa: E402
from test_exec_cal00_enrich import OID0, QUOTE_FEE, mk, recs, fast       # noqa: E402,F401
from test_fee_asset01 import MX, settle                                  # noqa: E402
import binance_client as BC                                              # noqa: E402
import engine as E                                                       # noqa: E402
import true_books as TB                                                  # noqa: E402


class SX(MX):
    """MX + the stop's own execution record (stop_fill), scripted per stop tag."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.stop_fills, self.sf_calls = {}, 0

    def stop_fill(self, s, tag):
        self.sf_calls += 1
        r = self.stop_fills.get(tag)
        if isinstance(r, Exception): raise r
        return dict(r) if r else None


def fire(e, k, px, qty=None, status='FILLED'):
    """Binance triggers lot k's stop at px: the stop leaves the open list, the position shrinks, and the triggered order
    (with its userTrades rows) is what stop_fill reports."""
    t, l = e.trade, e.state['lots'][k]
    tag, q = l['stop_id'], l['qty'] if qty is None else qty
    t.stops.pop(tag, None)
    t.pos[(l['symbol'], l['side'])] = t.pos.get((l['symbol'], l['side']), 0) - l['qty']
    t.oid += 1
    t.rows[t.oid] = [dict(symbol=l['symbol'], id=t.oid * 10, orderId=t.oid, price=str(px), qty=str(q),
                          commission=str(q * px * QUOTE_FEE), commissionAsset='USDT', maker=False)]
    t.stop_fills[tag] = dict(orderId=t.oid, clientOrderId=f'zbstop{t.oid}', status=status, executedQty=str(q), avgPrice=str(px))
    return tag


def test_a_fired_stop_is_booked_at_its_exchange_fill_with_its_actual_commission():
    e, _ = mk(SX); k = opened(e); settle(e)
    l = e.state['lots'][k]; avg, qty, stop = l['avg'], l['qty'], l['stop']
    px = round(stop - 0.37, 2)                                           # slipped past the configured stop
    fire(e, k, px)
    e.reconcile(500)
    h = e.history[-1]
    assert k not in e.state['lots'] and h['id'] == k and h['exit_reason'] == 'stop'
    assert h['exit'] == px and h['pnl_gross'] == pytest.approx((px - avg) * qty, abs=1e-4)
    assert h['fills_booked'] == 2 and len(h['cost_refs']) == 2
    settle(e)
    h = e.history[-1]
    assert h['fees_actual_partial'] is False and 'exit_px_unconfirmed' not in h
    rs = recs(e)
    assert h['fees_actual'] == pytest.approx(sum(x['commission_raw'][0]['amount'] for x in rs))
    s = [x for x in rs if x['kind'] == 'stop']
    assert len(s) == 1 and s[0]['actual'] == px and s[0]['expected'] == stop and s[0]['cost_ref'] in h['cost_refs']
    assert h['fees_actual'] is not None             # TRUE-BOOK counts the row as complete
    blob = json.dumps([rs, e.history, e.state], default=str)
    assert str(OID0)[:9] not in blob and 'zbstop' not in blob                # exchange ids stay in memory only


def test_an_unreadable_stop_record_waits_then_books_unconfirmed_and_incomplete(monkeypatch):
    e, _ = mk(SX); k = opened(e); settle(e)
    l = e.state['lots'][k]; qty, stop = l['qty'], l['stop']
    tag = fire(e, k, stop - 0.5)
    e.trade.stop_fills[tag] = BC.BinanceError(-1001, 'disconnected')
    e.state['lots'][k]['last_order_t'] = 0                               # no recent order: only the wait stops a resize
    for _ in range(3):
        e.reconcile(500)
        assert k in e.state['lots'] and e.state['lots'][k]['qty'] == qty    # waiting: never booked, never resized
    assert e.state['lots'][k]['stop_gone_t']
    monkeypatch.setattr(E.Engine, 'STOP_FILL_WAIT_S', 0)
    e.reconcile(500)
    h = e.history[-1]
    assert k not in e.state['lots'] and h['exit'] == stop and h['exit_px_unconfirmed'] == 1
    settle(e)
    h = e.history[-1]
    assert h['fees_actual'] is None and 'exit_px_unconfirmed' in h['fees_actual_partial']
    assert 'fills_without_cost_record' in h['fees_actual_partial'] and h['fees_actual'] is None
    assert not [x for x in recs(e) if x['kind'] == 'stop']               # no fill record claims an exchange price


@pytest.mark.parametrize('kind', ['qty', 'status', 'none'])
def test_an_ambiguous_record_is_never_taken_as_the_fill(monkeypatch, kind):
    e, _ = mk(SX); k = opened(e); settle(e)
    l = e.state['lots'][k]; qty, stop = l['qty'], l['stop']
    tag = fire(e, k, stop - 0.5, **dict(qty=dict(qty=qty / 2), status=dict(status='EXPIRED'), none={})[kind])
    if kind == 'none': e.trade.stop_fills[tag] = None
    e.reconcile(500)
    assert k in e.state['lots']
    monkeypatch.setattr(E.Engine, 'STOP_FILL_WAIT_S', 0)
    e.reconcile(500)
    h = e.history[-1]
    assert k not in e.state['lots'] and h['exit'] == stop and h['exit_px_unconfirmed'] == 1
    settle(e)
    h = e.history[-1]
    assert 'exit_px_unconfirmed' in h['fees_actual_partial'] and h['fees_actual'] is None
    assert not [x for x in recs(e) if x['kind'] == 'stop']


def test_a_client_without_a_stop_record_books_at_once_but_never_complete():
    e, _ = mk_engine(); k = opened(e)
    e.trade.pos[('BTCUSDT', 'LONG')] = 0; e.trade.stops.clear()
    e.reconcile(500)
    h = e.history[-1]
    assert k not in e.state['lots'] and h['exit_reason'] == 'stop' and h['exit_px_unconfirmed'] == 1
    assert 'exit_px_unconfirmed' in h['fees_actual_partial']


def test_finalize_keeps_an_unconfirmed_exit_incomplete_even_with_every_commission():
    import actual_costs as AC
    t = dict(cost_refs=['a'], exit_px_unconfirmed=1)
    AC.apply_fill(t, dict(cost_ref='a', commission_raw=[dict(asset='USDT', maker=False, amount=0.1, n=1)]))
    AC.finalize(t, 'USDT', 1)
    assert t['fees_actual'] is None and t['fees_actual_partial'] == ['exit_px_unconfirmed']
    assert 'exit_px_unconfirmed' in AC.COST_FIELDS and 'exit_px_unconfirmed' in TB.REVISABLE


# ------------------------------------------------------------------ client: classic and algo stop records (read-only)
class R:
    def __init__(self, answers): self.answers, self.calls = list(answers), []

    def __call__(self, method, path, params, **kw):
        self.calls.append((method, path, dict(params), kw.get('signed'), kw.get('retry')))
        a = self.answers.pop(0)
        if isinstance(a, Exception): raise a
        return a


def client(answers):
    c = BC.Futures.__new__(BC.Futures); c._req = R(answers); return c


def test_client_stop_fill_classic_algo_and_missing():
    c = client([dict(orderId=5, status='FILLED', executedQty='1', avgPrice='99')])
    assert c.stop_fill('BTCUSDT', 'o:5')['avgPrice'] == '99'
    assert c._req.calls == [('GET', '/fapi/v1/order', dict(symbol='BTCUSDT', orderId='5'), True, False)]
    c = client([dict(algoId=7, algoStatus='FINISHED', actualOrderId=55), dict(orderId=55, status='FILLED')])
    assert c.stop_fill('BTCUSDT', 'a:7')['orderId'] == 55
    assert [x[1:3] for x in c._req.calls] == [('/fapi/v1/algoOrder', dict(algoId='7')), ('/fapi/v1/order', dict(symbol='BTCUSDT', orderId=55))]
    c = client([dict(clientAlgoId='za1', algoStatus='CANCELED', actualOrderId='')])
    assert c.stop_fill('BTCUSDT', 'ac:za1') is None and len(c._req.calls) == 1   # never triggered: no order behind it
    assert client([BC.BinanceError(-2013, 'Order does not exist.')]).stop_fill('BTCUSDT', 'c:x') is None
    with pytest.raises(BC.BinanceError):
        client([BC.BinanceError(-1001, 'disconnected')]).stop_fill('BTCUSDT', 'o:5')   # transient: unknown, not "none"
    assert client([]).stop_fill('BTCUSDT', 'bogus') is None
