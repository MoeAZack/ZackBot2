"""FEE-ASSET-01 (PAPER/testnet, observe only): the actual commission of every executed fill (per asset, maker/taker, no
conversion, no exchange ids) and the account's funding income are folded into the lot and its history row beside the
estimate. Missing or non-quote amounts stay explicit (fees_actual None + fees_actual_partial reasons). Nothing here
changes sizing, orders or the `fees` estimate."""
import json, os, sys, time
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety import SL, SG, opened                                   # noqa: E402
from test_exec_cal00_enrich import EX, OID0, QUOTE_FEE, mk, recs, fast   # noqa: E402,F401
import actual_costs as AC                                                # noqa: E402
import engine as E                                                       # noqa: E402


class MX(EX):
    """EX + Binance's per-trade `maker` flag (taker for market orders) and an income (funding) reader."""
    maker = False

    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.income_rows, self.income_calls = [], []

    def _exec(self, name, s, ps, q):
        o, ex = super()._exec(name, s, ps, q)
        for r in self.rows[o['orderId']]: r['maker'] = self.maker
        return o, ex

    def income(self, income_type='FUNDING_FEE', start_ms=None, limit=1000):
        self.income_calls.append((income_type, start_ms))
        return [dict(r) for r in self.income_rows if start_ms is None or r['time'] >= start_ms]


def settle(e):
    recs(e); return e.apply_costs()


# ------------------------------------------------------------------ pure helpers
def test_raw_from_trades_aggregates_by_asset_and_maker_without_ids():
    raw, ok = AC.raw_from_trades([(0.1, 'USDT', False), (0.2, 'USDT', False), (0.05, 'USDT', True), (0.001, 'BNB', None)])
    assert ok and raw == [dict(asset='BNB', maker=None, amount=0.001, n=1), dict(asset='USDT', maker=False, amount=0.3, n=2),
                          dict(asset='USDT', maker=True, amount=0.05, n=1)]
    raw, ok = AC.raw_from_trades([(None, 'USDT', False), (0.1, '', False), (0.2, 'USDT', 'x')])
    assert not ok and raw == [dict(asset='USDT', maker=None, amount=0.2, n=1)]


def test_finalize_reasons_and_complete_total():
    t = dict(cost_refs=['a', 'b'])
    AC.apply_fill(t, dict(cost_ref='a', commission_raw=[dict(asset='USDT', maker=False, amount=0.1, n=1)]))
    AC.finalize(t, 'USDT', 2)
    assert t['fees_actual'] is None and t['fees_actual_partial'] == ['fill_records_pending']
    AC.apply_fill(t, dict(cost_ref='b', commission_raw=[dict(asset='USDT', maker=True, amount=0.02, n=2)]))
    AC.finalize(t, 'USDT', 3)
    assert t['fees_actual'] is None and t['fees_actual_partial'] == ['fills_without_cost_record']
    AC.finalize(t, 'USDT', 2)
    assert t['fees_actual'] == pytest.approx(0.12) and t['fees_actual_partial'] is False
    assert (t['maker_fills'], t['taker_fills']) == (2, 1)
    AC.apply_fill(t, dict(cost_ref='c', commission_missing='timeout')); t['cost_refs'].append('c')
    AC.finalize(t, 'USDT', 3)
    assert t['fees_actual'] is None and t['fees_actual_partial'] == ['fill_commission_missing']
    u = dict(cost_refs=['a']); AC.apply_fill(u, dict(cost_ref='a', commission_raw=[dict(asset='BNB', maker=False, amount=0.001, n=1)]))
    AC.finalize(u, 'USDT', 1)
    assert u['fees_actual'] is None and u['fees_actual_partial'] == ['non_quote_asset'] and u['fees_actual_by_asset'] == {'BNB': 0.001}


def test_funding_attribution_pro_rata_ambiguous_unattributed():
    assert AC.attribute_funding(-0.3, [('a', 'LONG', 1.0), ('b', 'LONG', 2.0)]) == ('attributed', {'a': -0.1, 'b': -0.2})
    assert AC.attribute_funding(-0.3, [('a', 'LONG', 1.0), ('b', 'SHORT', 1.0)]) == ('ambiguous', {})
    assert AC.attribute_funding(-0.3, []) == ('unattributed', {})
    assert AC.attribute_funding('x', [('a', 'LONG', 1.0)]) == ('unreadable', {})
    assert AC.tran_key(123) == AC.tran_key('123') and '123' not in AC.tran_key(123)


def test_enrich_finish_marks_missing_commission_explicitly():
    r = dict(symbol='BTCUSDT'); E.enrich_finish(r, [])
    assert r['commission_missing'] == 'no_order_ref' and 'commission_raw' not in r
    r = dict(symbol='BTCUSDT'); E.enrich_finish(r, [dict(exec=1.0)], cut=True)
    assert r['commission_missing'] == 'timeout'


# ------------------------------------------------------------------ engine: fills -> lot -> history
def test_entry_commission_lands_on_the_lot_beside_the_estimate():
    e, _ = mk(MX); k = opened(e)
    est = e.state['lots'][k]['fees']
    assert settle(e) == 1
    lot, r = e.state['lots'][k], recs(e, 'entry_market')[0]
    assert lot['cost_refs'] == [r['cost_ref']] and r['commission_raw'][0]['maker'] is False
    want = r['commission_raw'][0]['amount']
    assert lot['fees_actual_by_asset'] == {'USDT': want} and lot['fees_actual'] == want and lot['fees_actual_partial'] is False
    assert lot['taker_fills'] == 1 and 'maker_fills' not in lot and lot['fees'] == est     # the estimate is never replaced
    assert want == pytest.approx(float(r['qty_fill']) * 100.0 * QUOTE_FEE)


def test_close_carries_costs_to_history_and_late_exit_record_updates_the_row():
    e, _ = mk(MX); e.trade.ut_script = ['empty']            # the exit's commission arrives after the close (retry)
    k = opened(e); settle(e)
    e.close_lot(k, 'signal', mark=101.0)
    h = e.history[-1]
    assert h['id'] == k and len(h['cost_refs']) == 2 and h['fees_actual'] is None
    settle(e)
    h = e.history[-1]
    assert h['fees_actual_partial'] is False and h['cost_fills_applied'] == 2 and h['taker_fills'] == 2
    rs = recs(e)
    assert h['fees_actual'] == pytest.approx(sum(x['commission_raw'][0]['amount'] for x in rs))
    saved = json.load(open(os.path.join(e.dir, 'history.json')))
    assert saved[-1]['fees_actual'] == h['fees_actual'] and saved[-1]['fees'] == h['fees']


def test_non_quote_commission_kept_per_asset_never_converted():
    e, _ = mk(MX); e.trade.asset = 'BNB'
    k = opened(e); settle(e)
    lot = e.state['lots'][k]
    assert set(lot['fees_actual_by_asset']) == {'BNB'} and lot['fees_actual'] is None
    assert lot['fees_actual_partial'] == ['non_quote_asset']


def test_unreadable_commission_is_explicitly_missing():
    e, _ = mk(MX); e.trade.ut_script = [E.BinanceError(-2011, 'unknown order')]
    k = opened(e); settle(e)
    r, lot = recs(e, 'entry_market')[0], e.state['lots'][k]
    assert r['commission_missing'] and 'commission_raw' not in r
    assert lot['fees_actual'] is None and lot['fees_actual_partial'] == ['fill_commission_missing'] and lot['cost_fills_missing'] == 1


def test_fill_written_without_reads_is_not_enriched():
    e, _ = mk(MX); e._enr.close()
    k = opened(e); settle(e)
    assert recs(e, 'entry_market')[0]['commission_missing'] == 'not_read' and e.trade.ut_calls == 0
    assert e.state['lots'][k]['fees_actual_partial'] == ['fill_commission_missing']


def test_enrichment_off_marks_not_enriched(monkeypatch):
    e, _ = mk(MX); monkeypatch.setattr(e, '_enrich_on', lambda: False)
    k = opened(e); settle(e)
    assert recs(e, 'entry_market')[0]['commission_missing'] == 'not_enriched' and e.trade.ut_calls == 0
    assert e.state['lots'][k]['fees_actual_partial'] == ['fill_commission_missing']


def test_duplicate_hand_off_is_missing_on_its_lot_never_pending_forever():
    e, _ = mk(MX); k = opened(e); settle(e)
    lot = e.state['lots'][k]
    o = dict(orderId=OID0 + 1, clientOrderId=f'zbcid{OID0 + 1}', executedQty='0.5', avgPrice='0')
    rec = e._fill_rec('pyramid_add', 'BTCUSDT', 'LONG', True, 100.0, None, 0.5, 0.5)
    e._cost_link(rec, lot); e._fill_emit(rec, [e._order_ref(o, 0.5)])      # the entry's order handed off again
    settle(e)
    assert len(recs(e, 'entry_market')) == 1 and lot['cost_fills_applied'] == 2
    assert lot['fees_actual_partial'] == ['fill_commission_missing'] and lot['fees_actual'] is None


def test_no_exchange_ids_in_records_lots_or_history():
    e, _ = mk(MX); k = opened(e); settle(e); e.close_lot(k, 'signal', mark=101.0); settle(e)
    blob = json.dumps([recs(e), e.history, e.state], default=str)
    assert str(OID0)[:9] not in blob and 'tranId' not in blob


def test_reconciled_lot_without_a_fill_record_never_claims_a_complete_total():
    t = dict(symbol='BTCUSDT', fills=[('t', 'entry', 1.0, 100.0)])
    e, _ = mk(MX); e._cost_final('lot', t)
    assert t['fees_actual'] is None and t['fees_actual_partial'] == ['fills_without_cost_record']


def test_unlinked_cost_record_waits_then_counts_as_orphan(monkeypatch):
    e, _ = mk(MX)
    e._cost_sink(dict(cost_ref='nobody', commission_raw=[]))
    assert e.apply_costs() == 0 and len(e._cost_wait) == 1 and not e.state.get('cost_orphans')
    monkeypatch.setattr(E.Engine, 'COST_WAIT_S', 0)
    e.apply_costs()
    assert e.state['cost_orphans'] == 1 and not e._cost_wait


def test_orders_and_sizing_identical_with_or_without_cost_application():
    def run(apply):
        e, _ = mk(MX); k = opened(e)
        if apply: settle(e)
        e.close_lot(k, 'signal', mark=101.0)
        if apply: settle(e)
        recs(e)
        return e.trade.order_log, {x: e.history[-1][x] for x in ('qty_max', 'fees', 'pnl', 'pnl_gross', 'r', 'risk_usd')}
    assert run(True) == run(False)


def test_maker_entry_links_its_fill_record():
    from test_v31_engine import Book
    e, _ = mk(MX, ENTRY_ORDER='maker', MAKER_REPRICE=2, MAKER_WAIT_S=30)
    bk = Book(e.trade)
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    bk.fill()
    o = next(iter(bk.orders.values())); o['orderId'] = OID0 + 77
    e.trade.rows[OID0 + 77] = [dict(symbol='BTCUSDT', id=1, orderId=OID0 + 77, price=o['price'], qty=o['executedQty'],
                                    commission=str(float(o['executedQty']) * float(o['price']) * 0.0002),
                                    commissionAsset='USDT', maker=True)]
    e.manage(e.trade.marks())
    settle(e)
    lot = next(iter(e.state['lots'].values()))
    r = recs(e, 'entry_maker')[0]
    assert lot['cost_refs'] == [r['cost_ref']] and lot['maker_fills'] == 1 and lot['fees_actual_partial'] is False


# ------------------------------------------------------------------ funding income
def frow(i, amount, sym='BTCUSDT', t=None):
    return dict(symbol=sym, incomeType='FUNDING_FEE', income=str(amount), asset='USDT', time=t or int(time.time() * 1000),
                tranId=OID0 + 500 + i)


def test_funding_attributed_deduped_and_logged_without_ids():
    e, _ = mk(MX); k = opened(e)
    e.trade.income_rows = [frow(1, -0.25)]
    assert e.funding_tick(force=True) == '1 new'
    lot = e.state['lots'][k]
    assert lot['funding_actual'] == -0.25 and lot['funding_by_asset'] == {'USDT': -0.25} and lot['funding_rows'] == 1
    assert e.funding_tick(force=True) == '0 new' and e.state['lots'][k]['funding_rows'] == 1     # same row re-read: once
    assert e.funding_tick() == 'not_due'
    lines = [json.loads(x) for x in open(os.path.join(e.dir, 'funding.jsonl'))]
    assert len(lines) == 1 and lines[0]['status'] == 'attributed' and lines[0]['shares'] == [dict(kind='lot', id=k, amount=-0.25)]
    assert str(OID0 + 501) not in json.dumps([lines, e.state])
    e.close_lot(k, 'signal', mark=101.0)
    assert e.history[-1]['funding_actual'] == -0.25                         # carried to the history row


def test_funding_during_a_closed_trade_lands_on_its_history_row():
    e, _ = mk(MX); k = opened(e); e.close_lot(k, 'signal', mark=101.0)
    h = e.history[-1]; h['opened'] = '2026-10-01T00:00:00+00:00'; h['closed'] = '2026-10-01T10:00:00+00:00'
    h['fills'] = [['2026-10-01T00:00:00+00:00', 'entry', 1.0, 100.0], ['2026-10-01T10:00:00+00:00', 'exit', 1.0, 101.0]]
    t_in = int(E.datetime.fromisoformat('2026-10-01T08:00:00+00:00').timestamp() * 1000)
    e.state['funding_cursor_ms'] = t_in - 1
    e.trade.income_rows = [frow(1, -0.4, t=t_in), frow(2, -0.9, t=t_in + 3 * 3600 * 1000)]      # second: after the close
    e.funding_tick(force=True)
    assert h['funding_actual'] == -0.4 and h['funding_rows'] == 1
    assert json.load(open(os.path.join(e.dir, 'history.json')))[-1]['funding_actual'] == -0.4
    st = [json.loads(x)['status'] for x in open(os.path.join(e.dir, 'funding.jsonl'))]
    assert st == ['attributed', 'unattributed']


def test_add_and_exit_fills_link_to_their_lot():
    e, _ = mk(MX); k = opened(e); lot = e.state['lots'][k]
    assert e._add_qty(lot, 0.1, 100.0, 'pyramid_add') is not False
    e.close_lot(k, 'signal', mark=101.0); settle(e)
    h = e.history[-1]
    kinds = {r['cost_ref']: r['kind'] for r in recs(e)}
    assert sorted(kinds[c] for c in h['cost_refs']) == ['entry_market', 'exit', 'pyramid_add']
    assert h['cost_fills_applied'] == 3 and h['fees_actual_partial'] is False


def test_worker_hands_the_enriched_record_to_the_engine():
    e, _ = mk(MX); k = opened(e); recs(e)
    assert len(e._cost_q) == 1 and e._cost_q[0]['commission_raw'][0]['asset'] == 'USDT'


def test_funding_opposite_sides_ambiguous_and_no_holder_unattributed():
    e, _ = mk(MX, HEDGE=True)
    e.trade.income_rows = [frow(1, -0.1, sym='ETHUSDT')]
    e.funding_tick(force=True)
    k1 = opened(e); e.state['lots'][k1]['opened'] = '2020-01-01T00:00:00+00:00'
    fake = dict(e.state['lots'][k1], side='SHORT'); e.state['lots']['X'] = fake
    e.trade.income_rows.append(frow(2, -0.2))
    e.funding_tick(force=True)
    st = [json.loads(x)['status'] for x in open(os.path.join(e.dir, 'funding.jsonl'))]
    assert st == ['unattributed', 'ambiguous'] and 'funding_actual' not in e.state['lots'][k1]


def test_funding_is_never_read_in_live_mode():
    e, _ = mk(MX); e.live = True
    assert e.funding_tick(force=True) == 'live' and e.trade.income_calls == []


def test_income_wrapper_is_one_audit_read(monkeypatch):
    import binance_client as BC
    calls = []
    monkeypatch.setattr(BC.Futures, '_audit_req', lambda self, p, q: calls.append((p, q)) or [])
    f = BC.Futures('k' * 16, 's' * 16, BC.TESTNET)
    assert f.income('FUNDING_FEE', 5) == [] and calls == [('/fapi/v1/income', dict(incomeType='FUNDING_FEE', limit=1000, startTime=5))]


# ------------------------------------------------------------------ review r1 regressions
def test_cost_record_lost_to_a_restart_expires_to_lost_never_pending_forever(monkeypatch):
    e, _ = mk(MX); e.trade.ut_script = ['empty', 'empty']
    k = opened(e); lot = e.state['lots'][k]
    e._cost_q.clear() if hasattr(e, '_cost_q') else None
    recs(e); e._cost_q.clear()                                # the record never reaches the engine (restart cut it)
    e._cost_final('lot', lot); assert lot['fees_actual_partial'] == ['fill_records_pending']
    monkeypatch.setattr(E.Engine, 'COST_WAIT_S', 0); e._cost_swept = 0
    time.sleep(0.01); e.apply_costs()
    assert lot['fees_actual_partial'] == ['fill_records_lost'] and lot['fees_actual'] is None


def test_funding_read_failure_backs_off_instead_of_every_pass():
    e, _ = mk(MX); opened(e)
    def boom(*a, **k): e.trade.income_calls.append(1); raise E.BinanceError(-1001, 'x')
    e.trade.income = boom
    assert e.funding_tick() == 'read_failed' and e.funding_tick() == 'not_due' and len(e.trade.income_calls) == 1
    e.state['funding_polled'] -= E.Engine.FUNDING_RETRY_S + 1
    assert e.funding_tick() == 'read_failed' and len(e.trade.income_calls) == 2


def test_funding_split_uses_quantity_held_at_payment_time():
    e, _ = mk(MX); a = opened(e)
    la = e.state['lots'][a]
    la['fills'] = [['2026-10-01T00:00:00+00:00', 'entry', 10.0, 100.0], ['2026-10-01T10:00:00+00:00', 'tp1', 5.0, 101.0]]
    la['opened'], la['qty'] = '2026-10-01T00:00:00+00:00', 5.0
    b = dict(la, fills=[['2026-10-01T00:00:00+00:00', 'entry', 10.0, 100.0], ['2026-10-01T09:00:00+00:00', 'pyramid_add', 30.0, 100.0]],
             qty=40.0); e.state['lots']['B'] = b
    t = int(E.datetime.fromisoformat('2026-10-01T08:00:00+00:00').timestamp() * 1000)
    c = dict(id='C', symbol='BTCUSDT', side='LONG', opened='2026-10-01T00:00:00+00:00', closed='2026-10-01T12:00:00+00:00',
             qty_max=40.0, fills=[['2026-10-01T00:00:00+00:00', 'entry', 10.0, 100.0],
                                  ['2026-10-01T09:00:00+00:00', 'pyramid_add', 30.0, 100.0],
                                  ['2026-10-01T12:00:00+00:00', 'exit', 40.0, 101.0]])
    e.history.append(c)
    e.state['funding_cursor_ms'] = t - 1; e.trade.income_rows = [frow(1, -3.0, t=t)]
    e.funding_tick(force=True)
    assert la['funding_actual'] == b['funding_actual'] == c['funding_actual'] == -1.0   # 10 : 10 : 10 then, not 5 : 40 : 40
    assert AC.held_at([['bad', 'entry', 1, 1]], t) is None
