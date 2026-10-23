"""TP-SLIP-01 (#189) measurement only: every PAPER/testnet market close reads the testnet best bid / ask concurrently and
writes it into that close's fill record (touch vs decision mark, touch cover). The read never delays, changes or
blocks the order, is never made for a live or dry engine, and carries no exchange id."""
import os, sys, threading, time
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety import opened, client_with, Resp                     # noqa: E402
from test_exec_cal00_enrich import EX, mk, recs, one, OID0, fast      # noqa: E402,F401
import engine as E                                                      # noqa: E402

BOOK = dict(symbol='BTCUSDT', bidPrice='99.90', bidQty='0.004', askPrice='100.20', askQty='2.5', time=1761200000123)


class BX(EX):
    """EX + a scripted public bookTicker (a dict, an exception, or a gate that holds the read)."""
    book, hold, bt_calls, order_t, bt_t = dict(BOOK), None, 0, None, None

    def book_ticker(self, s):
        type(self).bt_calls += 1; type(self).bt_t = time.time()
        if self.hold is not None: self.hold.wait(5)
        if isinstance(self.book, Exception): raise self.book
        return dict(self.book, symbol=s) if isinstance(self.book, dict) else self.book

    def close(self, s, ps, q):
        type(self).order_t = time.time()
        return super().close(s, ps, q)


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(BX, 'book', dict(BOOK)); monkeypatch.setattr(BX, 'hold', None)
    monkeypatch.setattr(BX, 'bt_calls', 0); monkeypatch.setattr(E.Engine, 'BOOK_PROBE_WAIT_S', 0.3)


def closed(side='LONG', mark=101.0, cls=BX):
    e, _ = mk(cls); k = opened(e, side=side)
    q = e.state['lots'][k]['qty']
    e.close_lot(k, 'signal', mark=mark)
    return e, one(e, 'exit'), q


def test_long_close_records_the_bid_side_touch_and_its_distance_from_the_mark():
    e, r, q = closed('LONG', mark=101.0)
    b = r['book']
    assert (b['bid'], b['ask'], b['bid_qty'], b['ask_qty'], b['ex_ms']) == (99.9, 100.2, 0.004, 2.5, BOOK['time'])
    assert b['touch_vs_mark_bps'] == pytest.approx((101.0 - 99.9) / 101.0 * 1e4, abs=0.01)   # sold below the mark: +
    assert b['touch_cover'] == pytest.approx(0.004 / q, abs=1e-4) and b['fetch_s'] >= 0
    assert r['enrich_status'] == 'resolved' and r['actual'] == 100.0                         # the fill itself unchanged


def test_short_close_buys_at_the_ask():
    e, r, q = closed('SHORT', mark=100.0)
    b = r['book']
    assert b['touch_vs_mark_bps'] == pytest.approx((100.2 - 100.0) / 100.0 * 1e4, abs=0.01)  # bought above the mark: +
    assert b['touch_cover'] == pytest.approx(2.5 / q, abs=1e-4)


def test_better_than_mark_is_negative():
    BX.book = dict(BOOK, bidPrice='101.5')
    _, r, _ = closed('LONG', mark=101.0)
    assert r['book']['touch_vs_mark_bps'] < 0


def test_the_probe_starts_before_the_order_and_a_hung_read_never_blocks_it():
    BX.hold = threading.Event()
    t = time.time()
    e, r, _ = closed('LONG')
    BX.hold.set()
    assert BX.bt_calls == 1 and BX.order_t is not None
    assert r['book'] == {'missing': 'timeout'} and r['outcome'] == 'filled'
    assert time.time() - t < 3                                                               # bounded by the wait, not the read
    assert e.trade.order_log[-1][0] == 'close'


@pytest.mark.parametrize('err,want', [(TimeoutError('t'), 'TimeoutError'), (ValueError('x'), 'ValueError')])
def test_a_failed_read_is_explicitly_missing(err, want):
    BX.book = err
    _, r, _ = closed('LONG')
    assert r['book'] == {'missing': want} and r['outcome'] == 'filled'


def test_garbage_answer_is_missing_not_invented():
    BX.book = 'not a dict'
    _, r, _ = closed('LONG')
    assert r['book'] == {'missing': 'bad_answer'}


def test_order_calls_identical_with_and_without_the_probe():
    def run(cls):
        e, _ = mk(cls); k = opened(e); e.close_lot(k, 'signal', mark=101.0); recs(e)
        return e.trade.order_log
    BX.book = RuntimeError('down')
    assert [x[0] for x in run(BX)] == [x[0] for x in run(EX)] and run(BX)[-1][1:3] == run(EX)[-1][1:3]


def test_no_probe_without_book_ticker_live_or_dry():
    _, r, _ = closed('LONG', cls=EX)                                                          # client without the read
    assert 'book' not in r
    e, _ = mk(BX); k = opened(e); e.live = True
    assert e._book_probe('BTCUSDT') is None
    e.live, e.dry = False, True
    assert e._book_probe('BTCUSDT') is None and BX.bt_calls == 0


def test_no_ids_in_the_record():
    e, r, _ = closed('LONG')
    txt = open(e.F['fills']).read()
    assert str(OID0)[:9] not in txt and 'zbcid' not in txt
    assert set(r['book']) == {'bid', 'ask', 'bid_qty', 'ask_qty', 'ex_ms', 'fetch_s', 'touch_vs_mark_bps', 'touch_cover'}


def test_client_book_ticker_is_public_unsigned_and_on_the_audit_session():
    c, calls = client_with([Resp(200, dict(BOOK))])
    assert c.book_ticker('BTCUSDT')['bidPrice'] == '99.90'
    m, path, p = calls[0]
    assert (m, path) == ('GET', '/v1/ticker/bookTicker') and p == {'symbol': 'BTCUSDT'}       # no timestamp / signature


# ------------------------------------------------------------------ Codex #195 ruling: urgent closes never wait on evidence
def _two_lots(cls):
    e, _ = mk(cls); opened(e, 'BTCUSDT'); opened(e, 'ETHUSDT')
    return e


def test_flatten_with_a_hung_probe_adds_no_serial_wait(monkeypatch):
    monkeypatch.setattr(E.Engine, 'BOOK_PROBE_WAIT_S', 2.0)              # a normal close would wait 2 s per lot
    BX.hold = threading.Event()
    e = _two_lots(BX)
    t = time.time(); e.flatten(); dt = time.time() - t
    BX.hold.set()
    rs = recs(e, 'exit')
    assert dt < 1.0 and len(rs) == 2 and BX.bt_calls == 2                # probe started, never waited for
    assert all(r['book'] == {'missing': 'safety_deferred'} and r['outcome'] == 'filled' for r in rs)
    ref = _two_lots(EX); ref.flatten(); recs(ref)
    assert e.trade.order_log == ref.trade.order_log                      # identical close orders


def test_risk_trim_with_a_hung_probe_adds_no_wait(monkeypatch):
    monkeypatch.setattr(E.Engine, 'BOOK_PROBE_WAIT_S', 2.0)
    BX.hold = threading.Event()
    e, _ = mk(BX); k = opened(e); lot = e.state['lots'][k]
    t = time.time(); e._market_close(lot, lot['qty'] / 2, 'risk_trim', 101.0); dt = time.time() - t
    BX.hold.set()
    r = one(e, 'exit')
    assert dt < 1.0 and r['book'] == {'missing': 'safety_deferred'} and r['outcome'] == 'filled'
    ref, _ = mk(EX); k2 = opened(ref); l2 = ref.state['lots'][k2]
    ref._market_close(l2, l2['qty'] / 2, 'risk_trim', 101.0); recs(ref)
    assert e.trade.order_log == ref.trade.order_log


def test_normal_close_still_waits_for_the_book():
    _, r, _ = closed('LONG')
    assert 'missing' not in r['book'] and r['book']['bid'] == 99.9


def test_markless_close_measures_against_the_booking_price():
    e, _ = mk(BX); k = opened(e); lot = e.state['lots'][k]
    avg = lot['avg']
    e._market_close(lot, lot['qty'], 'signal', None)
    b = one(e, 'exit')['book']
    assert b['touch_vs_mark_bps'] == pytest.approx((avg - 99.9) / avg * 1e4, abs=0.01)


@pytest.mark.parametrize('why', ['risk_unwind', 'stop_crossed', 'stop_failed'])
def test_protective_closes_with_a_hung_probe_add_no_wait(monkeypatch, why):
    monkeypatch.setattr(E.Engine, 'BOOK_PROBE_WAIT_S', 2.0)
    BX.hold = threading.Event()
    e, _ = mk(BX); k = opened(e)
    t = time.time(); e.close_lot(k, why, 101.0); dt = time.time() - t
    BX.hold.set()
    r = one(e, 'exit')
    assert dt < 1.0 and BX.bt_calls == 1 and r['book'] == {'missing': 'safety_deferred'} and r['outcome'] == 'filled'
    ref, _ = mk(EX); k2 = opened(ref); ref.close_lot(k2, why, 101.0); recs(ref)
    assert e.trade.order_log == ref.trade.order_log


# ------------------------------------------------------------------ #195 P3 ruling: the urgent set's negative boundary
EVIDENCE_REASONS = ['take_profit_1', 'take_profit_ladder', 'exit_signal', 'time_exit', 'basket_tp']


def test_protective_and_evidence_reasons_are_disjoint():
    assert not set(EVIDENCE_REASONS) & set(E.Engine.BOOK_PROBE_URGENT)


@pytest.mark.parametrize('why', EVIDENCE_REASONS)
def test_evidence_closes_keep_the_bounded_wait_and_the_full_book(why):
    e, _ = mk(BX); k = opened(e)
    e.close_lot(k, why, 101.0)
    b = one(e, 'exit')['book']
    assert 'missing' not in b and (b['bid'], b['ask']) == (99.9, 100.2) and b['touch_vs_mark_bps'] > 0


@pytest.mark.parametrize('why', EVIDENCE_REASONS)
def test_evidence_closes_with_a_hung_probe_wait_then_record_timeout(monkeypatch, why):
    monkeypatch.setattr(E.Engine, 'BOOK_PROBE_WAIT_S', 0.4)
    BX.hold = threading.Event()
    e, _ = mk(BX); k = opened(e)
    t = time.time(); e.close_lot(k, why, 101.0); dt = time.time() - t
    BX.hold.set()
    assert dt >= 0.35 and one(e, 'exit')['book'] == {'missing': 'timeout'}               # waited, not safety_deferred
