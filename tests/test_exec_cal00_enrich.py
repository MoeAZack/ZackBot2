"""EXEC-CAL-00 (PAPER/testnet, observe only): a fill whose order answer has no average price (or, always, no commission)
is enriched from the order's own userTrades by a bounded, non-blocking worker. Enrichment never changes, retries,
cancels or resizes an order, never touches a lot, and what it cannot resolve stays explicitly missing."""
import copy, json, os, sys, tempfile, threading, time
import pytest
import requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety import FakeX, SL, SG, opened, client_with, Resp      # noqa: E402
from test_v31_engine import Book                                        # noqa: E402
import binance_client as BC                                             # noqa: E402
import engine as E                                                      # noqa: E402

OID0 = 987654321000                                   # 12-digit exchange ids: must never appear in any written record
QUOTE_FEE = 0.0005


class EX(FakeX):
    """FakeX + exchange order ids, configurable avgPrice / executed fraction, get_order and userTrades (scripted)."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.oid, self.avg, self.frac, self.split, self.asset = OID0, '0', 1.0, [(1.0, None)], 'USDT'
        self.rows, self.ords, self.ut_script, self.ut_calls, self.go_calls, self.order_log = {}, {}, [], 0, 0, []

    def _exec(self, name, s, ps, q):
        self.oid += 1
        ex = round(float(q) * self.frac, 3)
        self.order_log.append((name, s, ps, str(q)))
        o = dict(orderId=self.oid, clientOrderId=f'zbcid{self.oid}', status='FILLED' if self.frac >= 1 else 'EXPIRED',
                 executedQty=str(ex), avgPrice=self.avg, cumQuote='0')
        rows, left = [], ex
        for i, (f, px) in enumerate(self.split):
            qq = round(ex * f, 6) if i < len(self.split) - 1 else round(left, 6)
            left -= qq; p = px or self.mark[s]
            rows.append(dict(symbol=s, id=self.oid * 10 + i, orderId=self.oid, price=str(p), qty=str(qq),
                             commission=str(qq * p * QUOTE_FEE), commissionAsset=self.asset))
        self.rows[self.oid], self.ords[o['clientOrderId']] = rows, dict(o, symbol=s)
        return o, ex

    def open(self, s, ps, q):
        self._f('open'); o, ex = self._exec('open', s, ps, q)
        self.pos[(s, ps)] = self.pos.get((s, ps), 0) + ex; return o

    def close(self, s, ps, q):
        self._f('close'); o, ex = self._exec('close', s, ps, q)
        self.pos[(s, ps)] = self.pos.get((s, ps), 0) - ex; return o

    def get_order(self, s, cid):
        self.go_calls += 1
        return dict(self.ords[cid]) if cid in self.ords else None

    def user_trades(self, s, oid):
        self.ut_calls += 1
        if self.ut_script:
            r = self.ut_script.pop(0)
            if isinstance(r, Exception): raise r
            if r == 'empty': return []
        return [dict(x) for x in self.rows.get(oid, [])]


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(E, 'ENRICH_BACKOFF_S', (0.01, 0.02))
    monkeypatch.setattr(E, 'ENRICH_CAP_S', 5.0)


def mk(cls=EX, tmp=None, enr=None, **S):
    tmp = tmp or tempfile.mkdtemp()
    E.Futures = cls
    e = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)
    e.data = e.trade
    e.S.update(dict(UNIVERSE=['BTCUSDT', 'ETHUSDT'], SYMBOLS_ON={'BTCUSDT': True, 'ETHUSDT': True}, CAPITAL_CAP=500.0), **S)
    e._enr = enr or E.FillEnricher()                 # a private worker per test (no cross-test state)
    e.connect()
    e.marks = e.trade.marks()
    return e, tmp


def recs(e, kind=None):
    assert e._fill_flush(10), 'enrichment / writer did not catch up'
    p = e.F['fills']
    out = [json.loads(l) for l in open(p)] if os.path.exists(p) else []
    return [r for r in out if kind is None or r['kind'] == kind]


def one(e, kind='entry_market'):
    rs = recs(e, kind); assert len(rs) == 1, rs; return rs[0]


# ------------------------------------------------------------------ resolution
def test_empty_avgprice_resolved_from_user_trades_and_booking_unchanged():
    e, _ = mk(); e.trade.split = [(1.0, 100.5)]           # decided at 100.0, executed at 100.5, answer avgPrice '0'
    k = opened(e)
    r = one(e)
    assert r['enrich_status'] == 'resolved' and r['actual'] == 100.5 and r['slip_bps'] == 50.0
    assert r['decision_slippage_bps'] == 50.0 and r['commission_rate'] == r['fee_rate'] == QUOTE_FEE
    assert r['commission_asset'] == 'USDT' and r['enrich_attempts'] == 1 and 'limit_improvement_bps' not in r
    assert e.state['lots'][k]['avg'] == 100.0             # lot booking keeps its existing fallback (the decision price)


def test_partial_fill_weighted_over_executed_qty_only():
    e, _ = mk(); e.trade.frac, e.trade.split = 0.5, [(0.5, 100.0), (0.5, 101.0)]
    opened(e)
    r = one(e)
    assert r['outcome'] == 'partial' and r['qty_fill'] < r['qty_req']
    assert r['enrich_status'] == 'resolved' and r['actual'] == pytest.approx(100.5)


def test_multiple_user_trades_qty_weighted():
    e, _ = mk(); e.trade.split = [(0.25, 100.0), (0.75, 102.0)]
    opened(e)
    r = one(e)
    assert r['actual'] == pytest.approx(101.5) and r['decision_slippage_bps'] == pytest.approx(150.0)
    assert r['commission_rate'] == pytest.approx(QUOTE_FEE)


def test_delayed_visibility_first_read_empty_then_resolved():
    e, _ = mk(); e.trade.ut_script = ['empty']
    opened(e)
    r = one(e)
    assert e.trade.ut_calls == 2 and r['enrich_status'] == 'resolved' and r['enrich_attempts'] == 2


def test_trades_only_partly_visible_never_price_the_fill():
    """Rows covering only part of the executed qty are not a price for the whole fill: stays missing after the bound."""
    e, _ = mk(); e.trade.split = [(0.5, 100.0), (0.5, 104.0)]
    real = e.trade.user_trades
    e.trade.user_trades = lambda s, oid: real(s, oid)[:1]
    opened(e)
    r = one(e)
    assert r['enrich_status'] == 'missing' and r['actual'] is None and 'decision_slippage_bps' not in r
    assert 'commission_rate' not in r and r['enrich_attempts'] == E.ENRICH_ATTEMPTS


def test_avgprice_present_still_reads_commission():
    e, _ = mk(); e.trade.avg = '100.0'
    opened(e)
    r = one(e)
    assert r['enrich_status'] == 'resolved' and r['actual'] == 100.0 and r['commission_rate'] == QUOTE_FEE


def test_exit_and_add_paths_enriched():
    e, _ = mk(); k = opened(e)
    e.close_lot(k, 'signal', mark=101.0)                  # sold at mark 100.0 after deciding at 101.0: adverse +99 bps
    r = one(e, 'exit')
    assert r['enrich_status'] == 'resolved' and r['buy'] is False and r['actual'] == 100.0
    assert r['decision_slippage_bps'] == pytest.approx(99.01, abs=0.01)


# ------------------------------------------------------------------ failures: bounded, missing, no order side effects
@pytest.mark.parametrize('errs', [
    [requests.Timeout('t'), requests.Timeout('t'), requests.Timeout('t')],
    [BC.BinanceError(-429, 'rate'), BC.BinanceError(-429, 'rate'), BC.BinanceError(-429, 'rate')],
    [BC.BinanceError(-503, 'busy'), BC.BinanceError(-1001, 'x'), BC.BinanceError(-502, 'busy')],
])
def test_transient_failures_bounded_then_missing_no_order_side_effects(errs):
    e, _ = mk(); e.trade.ut_script = list(errs)
    k = opened(e)
    lot = copy.deepcopy(e.state['lots'][k]); calls = list(e.trade.calls); log = list(e.trade.order_log)
    r = one(e)
    assert e.trade.ut_calls == E.ENRICH_ATTEMPTS == 3 and r['enrich_status'] == 'missing' and r['actual'] is None
    assert not any(x in r for x in ('decision_slippage_bps', 'commission_rate', 'fee_rate', 'commission_asset'))
    assert r['slip_bps'] is None                          # missing is never 0
    assert e.trade.calls == calls and e.trade.order_log == log and e.state['lots'][k] == lot


def test_definitive_refusal_is_not_retried():
    e, _ = mk(); e.trade.ut_script = [BC.BinanceError(-1102, 'bad param')]
    opened(e)
    r = one(e)
    assert e.trade.ut_calls == 1 and r['enrich_status'] == 'missing'


def test_wall_clock_cap_gives_timeout(monkeypatch):
    monkeypatch.setattr(E, 'ENRICH_CAP_S', 0.005)
    monkeypatch.setattr(E, 'ENRICH_BACKOFF_S', (0.05, 0.05))
    e, _ = mk(); e.trade.ut_script = ['empty', 'empty', 'empty']
    opened(e)
    r = one(e)
    assert r['enrich_status'] == 'timeout' and r['actual'] is None and e.trade.ut_calls <= 1


def test_order_path_never_waits_on_enrichment():
    """The hand-off is non-blocking: a read that hangs does not delay the entry (the record waits, the order does not)."""
    import threading, time
    gate = threading.Event()
    e, _ = mk()
    real = e.trade.user_trades
    e.trade.user_trades = lambda s, oid: (gate.wait(5), real(s, oid))[1]
    t = time.monotonic(); opened(e); dt = time.monotonic() - t
    assert dt < 1.0 and e.state['lots']
    gate.set()
    assert one(e)['enrich_status'] == 'resolved'


# ------------------------------------------------------------------ duplicates / restart: never recorded twice
def test_duplicate_hand_off_recorded_once():
    e, _ = mk(); e.trade.ut_script = ['empty']
    opened(e)
    o = dict(orderId=OID0 + 1, clientOrderId=f'zbcid{OID0 + 1}', executedQty='0.5', avgPrice='0')
    rec = e._fill_rec('entry_market', 'BTCUSDT', 'LONG', True, 100.0, None, 0.5, 0.5)
    e._fill_emit(rec, [e._order_ref(o, 0.5)])            # the same order handed off again (duplicate callback)
    e._fill_emit(rec, [e._order_ref(o, 0.5)])
    assert len(recs(e, 'entry_market')) == 1 and e._enr.summary()['duplicates'] == 2


def test_engine_replaced_mid_enrichment_records_once():
    enr = E.FillEnricher()
    e, tmp = mk(enr=enr); e.trade.ut_script = ['empty', 'empty']
    opened(e)
    e2, _ = mk(tmp=tmp, enr=enr)                          # settings restart: a new engine, the same process-wide worker
    assert len(recs(e2, 'entry_market')) == 1 and recs(e2, 'entry_market')[0]['enrich_status'] == 'resolved'


def test_shutdown_mid_enrichment_writes_once_then_restart_does_not_rewrite(monkeypatch):
    monkeypatch.setattr(E, 'ENRICH_BACKOFF_S', (5.0, 5.0)); monkeypatch.setattr(E, 'ENRICH_CAP_S', 30.0)
    enr = E.FillEnricher()
    e, tmp = mk(enr=enr); e.trade.ut_script = ['empty']
    opened(e)
    import time
    for _ in range(200):
        if e.trade.ut_calls: break
        time.sleep(0.01)
    assert enr.close(2.0)                                  # process stopping while the job waits for its 2nd read
    rs = recs(e, 'entry_market')
    assert len(rs) == 1 and rs[0]['enrich_status'] == 'timeout' and rs[0]['actual'] is None
    e2, _ = mk(tmp=tmp)                                    # restart: ids were memory-only, nothing is re-read or re-written
    assert len(recs(e2, 'entry_market')) == 1 and e2.trade.ut_calls == 0


def test_closed_worker_writes_now_without_reads():
    enr = E.FillEnricher(); enr.close(1.0)
    e, _ = mk(enr=enr); e.trade.avg = '100.0'
    opened(e)
    r = one(e)
    assert r['enrich_status'] == 'partial' and r['enrich_attempts'] == 0 and e.trade.ut_calls == 0 and r['actual'] == 100.0


# ------------------------------------------------------------------ commission normalisation
def test_commission_asset_mismatch_leaves_rate_missing():
    e, _ = mk(); e.trade.asset = 'BNB'
    opened(e)
    r = one(e)
    assert r['enrich_status'] == 'partial' and r['actual'] == 100.0
    assert not any(x in r for x in ('commission_rate', 'fee_rate', 'commission_asset'))


def test_mixed_commission_assets_leave_rate_missing():
    e, _ = mk(); e.trade.split = [(0.5, None), (0.5, None)]
    real = e.trade.user_trades
    e.trade.user_trades = lambda s, oid: [dict(x, commissionAsset='BNB') if i else x for i, x in enumerate(real(s, oid))]
    opened(e)
    assert 'commission_rate' not in one(e)


# ------------------------------------------------------------------ PAPER / testnet identity
def _run(fail):
    e, _ = mk()
    if fail:
        e.trade.user_trades = lambda s, oid: (_ for _ in ()).throw(BC.BinanceError(-503, 'busy'))
    k = opened(e); e.close_lot(k, 'signal', mark=101.0)
    recs(e)
    return e


def test_order_calls_identical_whether_enrichment_succeeds_or_fails():
    ok, bad = _run(False), _run(True)
    assert ok.live is False and bad.live is False and ok.cfg['MODE'] == bad.cfg['MODE'] == 'paper'
    assert ok.trade.order_log == bad.trade.order_log and len(ok.trade.order_log) == 2
    assert ok.trade.calls == bad.trade.calls
    assert json.dumps(ok.trade.stops, sort_keys=True) == json.dumps(bad.trade.stops, sort_keys=True)
    assert {r['enrich_status'] for r in recs(ok)} == {'resolved'} and {r['enrich_status'] for r in recs(bad)} == {'missing'}


def test_live_client_is_never_read():
    e, _ = mk(); e.live = True                             # observation only: enrichment is PAPER/testnet-scoped here
    assert not e._enrich_on()


def test_no_ids_in_any_record():
    e, _ = mk(); k = opened(e); e.close_lot(k, 'signal', mark=101.0)
    rs = recs(e)
    txt = open(e.F['fills']).read()
    assert len(rs) == 2 and str(OID0)[:9] not in txt and 'zbcid' not in txt
    for r in rs:
        assert not set(r) & {'orderId', 'clientOrderId', 'oid', 'cid', 'id', 'tradeId', 'refs'}


# ------------------------------------------------------------------ maker: limit improvement (diagnostic) sign
@pytest.mark.parametrize('buy,act,lim,want', [(True, 99.9, 100.0, 10.0), (True, 100.1, 100.0, -10.0),
                                              (False, 100.1, 100.0, 10.0), (False, 99.9, 100.0, -10.0)])
def test_limit_improvement_sign(buy, act, lim, want):
    r = E.fill_px_fields(dict(kind='entry_maker', buy=buy, expected=lim, signal_px=100.0, actual=act), lim)
    assert r['limit_improvement_bps'] == pytest.approx(want)
    assert r['decision_slippage_bps'] == pytest.approx(-want)   # same prices: improvement and cost have opposite signs


def test_limit_improvement_never_on_taker_and_never_zero_when_missing():
    assert 'limit_improvement_bps' not in E.fill_px_fields(dict(kind='entry_market', buy=True, expected=100.0, actual=99.0), 100.0)
    r = E.fill_px_fields(dict(kind='entry_maker', buy=True, expected=100.0, actual=None), 100.0)
    assert 'limit_improvement_bps' not in r and 'decision_slippage_bps' not in r and r['slip_bps'] is None


def test_maker_fill_enriched_with_commission_and_limit_diagnostic():
    e, _ = mk(ENTRY_ORDER='maker', MAKER_REPRICE=2, MAKER_WAIT_S=30)
    bk = Book(e.trade)
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    bk.fill()
    o = next(iter(bk.orders.values())); o['orderId'] = OID0 + 77
    e.trade.rows[OID0 + 77] = [dict(symbol='BTCUSDT', id=1, orderId=OID0 + 77, price=o['price'], qty=o['executedQty'],
                                    commission=str(float(o['executedQty']) * float(o['price']) * 0.0002), commissionAsset='USDT')]
    e.manage(e.trade.marks())
    r = one(e, 'entry_maker')
    assert r['enrich_status'] == 'resolved' and r['actual'] == 99.9 and r['limit_improvement_bps'] == 0.0
    assert r['decision_slippage_bps'] == pytest.approx(-10.0) and r['commission_rate'] == pytest.approx(0.0002)
    assert not e._mk_refs


def test_maker_short_limit_improvement_without_reads():
    """A client without user_trades (no enrichment) still gets the limit diagnostic from the memory-only refs."""
    e, _ = mk(cls=FakeX, ENTRY_ORDER='maker', MAKER_REPRICE=2, MAKER_WAIT_S=30)
    bk = Book(e.trade)
    assert e.open_lot(SL, 'BTCUSDT', 'SHORT', dict(SG, le=False, se=True), None, e.equity())
    bk.fill(); e.manage(e.trade.marks())
    r = one(e, 'entry_maker')
    assert r['limit_improvement_bps'] == 0.0 and r['decision_slippage_bps'] == pytest.approx(-10.0)   # sold at 100.1 vs 100
    assert 'enrich_status' not in r


# ------------------------------------------------------------------ the exchange wrapper
def test_user_trades_wrapper_single_attempt_and_outside_the_outage_circuit(monkeypatch):
    monkeypatch.setattr(BC.time, 'sleep', lambda x: None)
    c, calls = client_with([Resp(429, {'code': -1003, 'msg': 'rate'})])
    with pytest.raises(BC.BinanceError) as ei:
        c.user_trades('BTCUSDT', 123)
    assert BC.is_transient(ei.value) and len(calls) == 1
    assert calls[0][0] == 'GET' and calls[0][1] == '/v1/userTrades' and calls[0][2]['orderId'] == 123
    assert c.health.state == 'ok'                          # an audit read never drives the T05b circuit
    c, calls = client_with([Resp(200, [{'orderId': 1, 'qty': '1', 'price': '2'}])])
    assert c.user_trades('BTCUSDT', 1)[0]['price'] == '2'


def test_duplicate_detected_by_either_id():
    enr = E.FillEnricher()
    rec = dict(kind='entry_market', symbol='BTCUSDT', buy=True, outcome='filled', expected=100.0, actual=None)
    w = type('W', (), {'emit': lambda self, r: True})()
    assert enr.submit(rec, [dict(oid=OID0 + 5, cid='zbcidA', exec=1.0)], None, w) == 'queued'
    assert enr.submit(rec, [dict(oid=None, cid='zbcidA', exec=1.0)], None, w) == 'duplicate'
    assert enr.submit(rec, [dict(oid=OID0 + 5, cid=None, exec=1.0)], None, w) == 'duplicate'
    enr.close(1.0)


def test_maker_refs_lost_by_restart_mean_no_reads_and_no_limit_diagnostic():
    e, _ = mk(ENTRY_ORDER='maker', MAKER_REPRICE=2, MAKER_WAIT_S=30)
    bk = Book(e.trade)
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    e._mk_refs = type('Forget', (dict,), {'setdefault': lambda self, k, d: d})()   # memory lost (restart) before finalize
    bk.fill()
    e.manage(e.trade.marks())
    r = one(e, 'entry_maker')
    assert e.trade.ut_calls == 0 and 'enrich_status' not in r and 'limit_improvement_bps' not in r
    assert r['actual'] == 99.9 and r['decision_slippage_bps'] == pytest.approx(-10.0)


# ------------------------------------------------------------------ self-review: races / partial correlation
def test_enrichment_racing_a_close_never_touches_lots_or_state():
    e, _ = mk(); e.trade.ut_script = ['empty', 'empty']      # entry enrichment still pending when the lot closes
    k = opened(e); e.close_lot(k, 'signal', mark=101.0)
    snap = json.dumps(e.state, sort_keys=True, default=str); hist = json.dumps(e.history, default=str)
    rs = recs(e)
    assert json.dumps(e.state, sort_keys=True, default=str) == snap and json.dumps(e.history, default=str) == hist
    assert sorted(r['kind'] for r in rs) == ['entry_market', 'exit'] and k not in e.state['lots']
    assert {r['enrich_status'] for r in rs} == {'resolved'}


def test_maker_fill_partly_before_restart_is_not_enriched_from_the_rest():
    e, _ = mk(ENTRY_ORDER='maker', MAKER_REPRICE=2, MAKER_WAIT_S=30)
    bk = Book(e.trade)
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    rec = next(iter(e.state['resting_entries'].values()))
    rec['filled'] += 0.001; rec['cost'] += 0.001 * 99.9      # executed before a restart: its order id is not in memory
    bk.fill(); e.manage(e.trade.marks())
    r = one(e, 'entry_maker')
    assert e.trade.ut_calls == 0 and 'enrich_status' not in r and 'limit_improvement_bps' not in r


# ------------------------------------------------------------------ review fixes (ported from the e0_rev adversarial repros)
class _ListWriter:
    def __init__(self): self.out, self.closed = [], False
    def emit(self, r):
        if not self.closed: self.out.append(r)


def _jrec(**k):
    return dict(dict(kind='entry_market', symbol='BTCUSDT', side='LONG', buy=True, outcome='filled', expected=100.0, actual=None), **k)


def _jref(oid, cid=None, exec=1.0, opx=None):
    return dict(oid=oid, cid=cid, exec=exec, opx=opx, lpx=None)


def _maker_no_avg(cls, lose_refs=False):
    e, _ = mk(cls=cls, ENTRY_ORDER='maker', MAKER_REPRICE=2, MAKER_WAIT_S=30)
    bk = Book(e.trade)
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    bk.fill()
    o = next(iter(bk.orders.values())); o['orderId'] = OID0 + 77; o['avgPrice'] = '0'    # answered without a price
    if lose_refs:
        real = e._maker_done_order
        def done(rec, o):
            real(rec, o); e._mk_refs.clear()                                             # as after a restart
        e._maker_done_order = done
    e.manage(e.trade.marks())
    return e, one(e, 'entry_maker')


@pytest.mark.parametrize('cls,lose', [(EX, False), (FakeX, False), (EX, True)])
def test_maker_fill_without_price_never_records_the_limit_as_executed(cls, lose):
    """MEDIUM: a maker fill answered with no avgPrice is booked at its limit (unchanged), but the audit record never
    claims the limit as `actual`: no actual, no bps; with reads that never resolve it, status missing / timeout."""
    e, r = _maker_no_avg(cls, lose)
    assert r.get('actual') is None and r.get('slip_bps') is None, r
    assert 'limit_improvement_bps' not in r and 'decision_slippage_bps' not in r, r
    if cls is EX and not lose: assert r['enrich_status'] in ('missing', 'timeout'), r
    lot = next(iter(e.state['lots'].values()))
    assert lot['avg'] == pytest.approx(r['expected'])                       # booking unchanged: the lot still sits at the limit


def test_audit_read_never_touches_the_outage_circuit(monkeypatch):
    """LOW-MED: an audit read neither closes nor feeds the T05b circuit, never touches its telemetry, uses its own
    session with a short timeout, and is not sent while an outage is open."""
    monkeypatch.setattr(BC.time, 'sleep', lambda x: None)
    c, calls = client_with([Resp(200, [])])
    c.health.fail('GET /fapi/v2/positionRisk: HTTP 503', read=True)
    before = c.health.snapshot()
    assert c.user_trades('BTCUSDT', 1) == [] and c.health.snapshot() == before and c.last_ok == 0   # no ok(): still degraded
    c, calls = client_with([Resp(429, {'code': -1003, 'msg': 'rate'})])
    before = c.health.snapshot()
    with pytest.raises(BC.AuditRateLimited): c.user_trades('BTCUSDT', 1)
    assert c.health.snapshot() == before and c.audit_blocked() == 'cooldown'            # no fail(); shared audit pause
    c, calls = client_with([Resp(200, [])])
    for _ in range(3): c.health.fail('GET /fapi/v2/positionRisk: HTTP 503', read=True)
    before = c.health.snapshot()
    with pytest.raises(BC.AuditSkipped): c.user_trades('BTCUSDT', 1)
    assert calls == [] and c.health.snapshot() == before                                # not sent; no probe / fail-fast count
    c, calls = client_with([Resp(503, {}, {'Retry-After': '30'})])
    seen, real = {}, c.s.request
    def request(m, u, params=None, timeout=None):
        seen['t'] = timeout; return real(m, u, params, timeout)
    c.s.request = request
    with pytest.raises(BC.BinanceError): c.user_trades('BTCUSDT', 1)
    assert seen['t'] == BC.AUDIT_TIMEOUT_S <= 3.0 < E.ENRICH_CAP_S                     # bounded well below the 10 s cap
    assert c._audit['until'] - BC.time.monotonic() > 25                                # Retry-After honoured
    f = BC.Futures.__new__(BC.Futures); f.key, f.s = 'k' * 16, requests.Session()
    s = f._audit_session()
    assert isinstance(s, requests.Session) and s is not f.s and s.headers['X-MBX-APIKEY'] == f.key   # same auth path


def test_rate_limit_stops_the_job_and_pauses_every_other_audit_read(monkeypatch):
    monkeypatch.setattr(BC.time, 'sleep', lambda x: None)
    c, calls = client_with([Resp(429, {'code': -1003, 'msg': 'rate'}, {'Retry-After': '5'})])
    enr, w = E.FillEnricher(), _ListWriter()
    assert enr.submit(_jrec(), [_jref(1, 'a')], c, w) == 'queued' and enr.flush(5)
    assert enr.submit(_jrec(symbol='ETHUSDT'), [_jref(2, 'b')], c, w) == 'queued' and enr.flush(5)
    assert len(calls) == 1 and [r.get('enrich_reason') for r in w.out] == ['rate_limit', 'cooldown']
    assert [r['enrich_status'] for r in w.out] == ['missing', 'missing'] and w.out[0]['enrich_attempts'] == 1
    assert c.health.snapshot()['other_fails'] == 0 and c.health.state == 'ok'
    enr.close(1.0)


def test_shutdown_with_a_hung_read_writes_every_pending_record_once():
    """LOW: close() writes the in-flight job and the queued ones BEFORE the fill writers close; the hung read's late
    result is dropped, never written a second time."""
    class Slow:
        def user_trades(self, s, oid): time.sleep(1.0); return []
    enr, w = E.FillEnricher(), _ListWriter()
    assert enr.submit(_jrec(), [_jref(1, 'c1')], Slow(), w) == 'queued'
    time.sleep(0.1)
    assert enr.submit(_jrec(symbol='ETHUSDT'), [_jref(2, 'c2')], Slow(), w) == 'queued'
    enr.close(0.2)
    assert len(w.out) == 2 and {r['enrich_status'] for r in w.out} == {'timeout'}
    time.sleep(1.3)                                                     # the hung read returns after the shutdown
    assert len(w.out) == 2 and enr.summary()['dropped_late'] >= 1


def test_worker_start_failure_writes_the_record_once(monkeypatch):
    """LOW: when the worker thread cannot start, nothing is queued or marked seen; the caller writes it now, once."""
    enr, w = E.FillEnricher(), _ListWriter()
    e, _ = mk(enr=enr); e._fillw = w
    real, n = threading.Thread.start, [0]
    def start(self):
        if self.name == 'fill-enrich' and n[0] == 0:
            n[0] += 1; raise RuntimeError('cannot start new thread')
        return real(self)
    monkeypatch.setattr(threading.Thread, 'start', start)
    opened(e)
    assert n[0] == 1 and not enr.seen and not enr.jobs
    enr.submit(_jrec(symbol='ETHUSDT'), [_jref(2, 'c2')], e.trade, w)    # a later hand-off starts the worker
    assert enr.flush(5)
    assert len([r for r in w.out if r['kind'] == 'entry_market' and r['symbol'] == 'BTCUSDT']) == 1
    enr.close(1.0)


def test_trade_rows_without_an_id_are_rejected():
    rec, refs = _jrec(), [_jref(5)]
    row = dict(symbol='BTCUSDT', orderId=5, price='101', qty='1', commission='0.05', commissionAsset='USDT')
    class C:
        def user_trades(self, s, oid): return [dict(row), dict(row)]        # no id: never counted, never coverage
    E.enrich_attempt(rec, refs, C()); E.enrich_finish(rec, refs)
    assert rec.get('actual') is None and rec['enrich_status'] == 'missing' and 'fee_rate' not in rec


def test_negative_commission_is_a_rebate_flag_never_a_negative_fee_rate():
    rec, refs = _jrec(), [_jref(5)]
    class C:
        def user_trades(self, s, oid):
            return [dict(symbol='BTCUSDT', id=1, orderId=5, price='100', qty='1', commission='-0.01', commissionAsset='USDT')]
    E.enrich_attempt(rec, refs, C()); E.enrich_finish(rec, refs)
    assert rec['actual'] == 100.0 and rec.get('commission_rebate') is True and rec.get('commission_asset') == 'USDT'
    assert 'fee_rate' not in rec and 'commission_rate' not in rec and rec['enrich_status'] == 'partial'
