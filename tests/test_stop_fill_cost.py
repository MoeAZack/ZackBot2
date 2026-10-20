"""Protective-stop fill cost path (Codex 6243423221): a stop that fired on Binance is booked from its own execution record -
the exchange's average price, a fill record with a cost_ref and the actual userTrades commission - never from the
configured stop price with no cost record. An unreadable / ambiguous record waits STOP_FILL_WAIT_S, then the trade is
booked at the stop price marked exit_px_unconfirmed, so its row stays INCOMPLETE (no zero fee, no invented price)."""
import json, os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety import mk_engine, opened, SG                            # noqa: E402
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
    assert e.state['lots'][k]['stop_gone']
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
    for _ in range(3): e.reconcile(500)
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
    for aid in ('', None, 0, '0', -1, '-1'):
        c = client([dict(clientAlgoId='za1', algoStatus='CANCELED', actualOrderId=aid)])
        assert c.stop_fill('BTCUSDT', 'ac:za1') is None and len(c._req.calls) == 1   # never triggered: no order behind it
    c = client([dict(clientAlgoId='za1', algoStatus='NEW')])
    assert c.stop_fill('BTCUSDT', 'ac:za1') is None and c._req.calls[0][2] == dict(clientAlgoId='za1')
    assert client([BC.BinanceError(-2013, 'Order does not exist.')]).stop_fill('BTCUSDT', 'c:x') is None
    with pytest.raises(BC.BinanceError):
        client([BC.BinanceError(-1001, 'disconnected')]).stop_fill('BTCUSDT', 'o:5')   # transient: unknown, not "none"
    assert client([]).stop_fill('BTCUSDT', 'bogus') is None


def test_a_waiting_lot_is_never_managed(monkeypatch):
    e, _ = mk(SX); k = opened(e); settle(e)
    l = e.state['lots'][k]
    tag = fire(e, k, l['stop'] - 0.5); e.trade.stop_fills[tag] = None
    e.reconcile(500)
    assert e.state['lots'][k]['stop_gone'][0] == tag
    for flag in ('stop_dirty', 'force_close'):                         # each would make manage place a stop / close
        e.state['lots'][k][flag] = True; n = len(e.trade.calls)
        e.manage(e.trade.marks())
        assert k in e.state['lots'] and not [c for c in e.trade.calls[n:] if c in ('close', 'open', 'stop', 'cancel')]
        e.state['lots'][k].pop(flag)


def test_a_new_stop_is_a_new_wait_and_a_restart_never_skips_it(monkeypatch):
    e, _ = mk(SX); k = opened(e); settle(e)
    l = e.state['lots'][k]
    l['stop_gone'] = ['o:old', 1.0]                                    # an old timer for a stop that was replaced
    tag = fire(e, k, l['stop'] - 0.5); e.trade.stop_fills[tag] = None
    e.reconcile(500)
    assert k in e.state['lots'] and l['stop_gone'][0] == tag and l['stop_gone'][1] > 1.0
    l['stop_gone'][1] = 1.0; e._sf_tries.clear()                       # a restart long after the stop fired
    e.reconcile(500); e.reconcile(500)
    assert k in e.state['lots']                                        # 2 reads by this process: still waiting
    e.reconcile(500)
    assert k not in e.state['lots'] and e.history[-1]['exit_px_unconfirmed'] == 1


def test_manage_and_the_stop_verifier_send_no_order_while_the_fill_is_unreadable():
    """Codex 6243712172: through the whole unreadable interval only evidence reconciliation may act - repeated manage()
    passes and forced AUD-04 verifier passes send no order (no stop, add, close or cancel) and open no exposure."""
    e, _ = mk(SX); k = opened(e); settle(e)
    l = e.state['lots'][k]; qty = l['qty']
    tag = fire(e, k, l['stop'] - 0.5); e.trade.stop_fills[tag] = BC.BinanceError(-1001, 'disconnected')
    l['stop_placed_t'] = 0; l['last_order_t'] = 0                       # past every grace: the verifier treats it as missing
    n = len(e.trade.calls)
    for i in range(6):
        if i % 2: e.state['lots'][k]['stop_dirty'] = True                 # a pending stop move (trail / BE / DCA re-stop)
        e._stopv_due('BTCUSDT', 0); e.manage(e.trade.marks()); e.verify_stops('test')
    sent = [c for c in e.trade.calls[n:] if c in ('close', 'open', 'stop', 'cancel', 'leverage')]
    assert k in e.state['lots'] and e.state['lots'][k]['qty'] == qty and sent == []
    assert 'tags' in e.trade.calls[n:] and e.state['lots'][k].get('stop_miss', 0) >= 1   # the verifier did look at it
    assert e.trade.pos.get(('BTCUSDT', 'LONG'), 0) <= 1e-12                             # no new exposure


def two_lots(e):
    """Two lots on BTCUSDT LONG (different sleeves): lot 1's stop fires, its fill record is unreadable."""
    k1 = opened(e); settle(e)
    sl2 = E.sleeve('U', 'ema_mom', 0.5, 0.02, 2, 'all')
    e.S['SLEEVES'] = list(e.S['SLEEVES']) + [sl2]
    assert e.open_lot(sl2, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    k2 = next(k for k in e.state['lots'] if k != k1); settle(e)
    tag = fire(e, k1, e.state['lots'][k1]['stop'] - 0.5); e.trade.stop_fills[tag] = BC.BinanceError(-1001, 'disconnected')
    for k in (k1, k2): e.state['lots'][k]['last_order_t'] = 0
    e.reconcile(500)
    assert e.state['lots'][k1]['stop_gone'][0] == tag and not e.state['lots'][k2].get('stop_gone')
    return k1, k2


def test_a_waiting_lot_never_closes_a_sibling_lots_size(monkeypatch):
    """Codex 6243761037 F1: exit signal / time exit (direct close_lot) and flatten() send no close for the waiting lot;
    the sibling's exchange position and state are unchanged; reconcile then books the fired stop exactly once."""
    e, _ = mk(SX); k1, k2 = two_lots(e)
    pos0, q2, n = e.trade.pos[('BTCUSDT', 'LONG')], e.state['lots'][k2]['qty'], len(e.trade.calls)
    for why in ('exit_signal', 'time_exit'):
        with pytest.raises(RuntimeError, match='stop fired'): e.close_lot(k1, why, 100.0)
    with pytest.raises(RuntimeError, match='stop fired'): e._market_close(e.state['lots'][k1], 0.001, 'take_profit_1', 100.0)
    res = e.flatten()
    assert [f[0] for f in res['failed']] == [k1] and res['closed'] == [k2]                  # only the sibling's own close
    closes = [c for c in e.trade.calls[n:] if c == 'close']
    assert len(closes) == 1 and float(e.trade.order_log[-1][3]) == pytest.approx(q2)         # exactly the sibling's size
    assert e.trade.pos[('BTCUSDT', 'LONG')] == pytest.approx(pos0 - q2) and k1 in e.state['lots']
    monkeypatch.setattr(E.Engine, 'STOP_FILL_WAIT_S', 0)
    e.reconcile(500); e.reconcile(500)
    stops = [h for h in e.history if h['id'] == k1]
    assert k1 not in e.state['lots'] and len(stops) == 1 and stops[0]['exit_reason'] == 'stop'


def test_the_candle_exit_path_defers_the_waiting_lot_without_an_order(monkeypatch):
    """The real cycle() exit-signal caller: the waiting lot's exit is deferred (no order), the sibling is untouched."""
    e, _ = mk(SX); k1, k2 = two_lots(e)
    l1 = e.state['lots'][k1]; tf = l1['tf']
    monkeypatch.setattr(e, 'compute_signals', lambda tf_, syms, extra=None: ({f"{l1['sleeve']}|BTCUSDT": dict(SG, lx=True)}, {}))
    e.S['ENTRIES_PAUSED'] = True
    pos0, q2, n = e.trade.pos[('BTCUSDT', 'LONG')], e.state['lots'][k2]['qty'], len(e.trade.calls)
    e.cycle(tf)
    assert 'close' not in e.trade.calls[n:] and e.trade.pos[('BTCUSDT', 'LONG')] == pos0
    assert k1 in e.state['lots'] and e.state['lots'][k2]['qty'] == q2


def test_the_panel_close_and_flatten_defer_the_waiting_lot(monkeypatch):
    """Cowork 6243799034: the panel's 'close' and 'flatten' actions reach the same guard - no order for the waiting lot."""
    import app as A
    e, _ = mk(SX); k1, k2 = two_lots(e)
    app = A.App.__new__(A.App); app.engine = e; app.preview = lambda: None; app.cfg = {}; app.tg = None
    monkeypatch.setattr(A, 'APP', app, raising=False)
    pos0, n = e.trade.pos[('BTCUSDT', 'LONG')], len(e.trade.calls)
    with pytest.raises(RuntimeError, match='stop fired'): A.handle('/api/action', dict(action='close', key=k1))
    assert 'close' not in e.trade.calls[n:] and e.trade.pos[('BTCUSDT', 'LONG')] == pos0 and k1 in e.state['lots']
    with pytest.raises(ValueError, match='1 FAILED'): A.handle('/api/action', dict(action='flatten'))
    assert e.trade.calls[n:].count('close') == 1 and k1 in e.state['lots'] and k2 not in e.state['lots']


def test_every_close_order_goes_through_the_guarded_market_close():
    """Only _market_close sends a close order (panel, Telegram, grid, flatten and every exit call it), so its guard
    covers every caller."""
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for f in ('engine.py', 'app.py', 'telegram_ctl.py', 'grid.py'):
        src = open(os.path.join(root, f), encoding='utf-8').read()
        hits = [m.start() for m in re.finditer(r'\.trade\.close\(', src)]
        if f != 'engine.py': assert not hits, f
        else:
            mc = src.index('    def _market_close('); nxt = src.index('\n    def ', mc + 10)
            assert hits and all(mc < h < nxt for h in hits) and '        self._stop_fired_guard(lot)' in src[mc:nxt]
