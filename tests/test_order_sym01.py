"""ORDER-SYM-01 (PAPER/testnet, test-first): two B1_FAST candidates on one 15m candle. While the first fill's commission is
in flight the book is cost_pending and refuses new risk (fail-closed stays). Once the commission lands inside the SAME
candle the deferred candidate must be reconsidered, so the opened set never depends on sleeve/symbol iteration order.
'Same candle' tick = one pass of app.py's main loop between candles: manage(marks), apply_costs(), true_books()."""
import os, sys, threading, time
from datetime import timedelta

import numpy as np, pandas as pd, pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_exec_cal00_enrich import mk, fast                             # noqa: E402,F401  (fast: autouse backoff)
from test_fee_asset01 import MX                                         # noqa: E402
import engine as E                                                      # noqa: E402
import true_books as TB                                                 # noqa: E402

XFAIL = pytest.mark.xfail(strict=True, reason='ORDER-SYM-01: deferred same-candle reconsideration not implemented')
A, B = 'BTCUSDT', 'ETHUSDT'
P_SL = E.sleeve('P', 'exp_scalp', 0.5, 0.02, 4, [A], tf='15m')          # B1_FAST
Q_SL = E.sleeve('Q', 'exp_scalp', 0.5, 0.02, 4, [B], tf='15m')          # B1_FAST
ORDERS = {'AB': ([P_SL, Q_SL], A, B), 'BA': ([Q_SL, P_SL], B, A)}       # sleeve order = candidate iteration order
TF = 900
PENDING = ('cost_pending', 'INCOMPLETE (fees_actual_missing)')   # PR #163 text / master text


class GX(MX):
    """MX whose userTrades read waits on a gate: the commission stays in flight until the test releases it."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.gate = threading.Event(); self.gate.set()

    def user_trades(self, s, oid):
        self.gate.wait(10); return super().user_trades(s, oid)


def candle(shift=0):
    """Open of the decision candle (the last closed 15m candle), as str(d.t) without tz."""
    t = (int(time.time()) // TF - 1 + shift) * TF
    return pd.Timestamp(t, unit='s').strftime('%Y-%m-%d %H:%M:%S')


def frame(px, t_last, n=120):
    t = pd.date_range(end=pd.Timestamp(t_last, tz='UTC'), periods=n, freq='15min')
    c = np.full(n, px)
    return pd.DataFrame(dict(t=t, o=c, h=c * 1.01, l=c * 0.99, c=c, v=np.full(n, 1000.0), atr=np.full(n, px * 0.02),
                             e50=c * 0.98, st=np.ones(n)))


def sig(sym, t, le=True):
    px = 100.0 if sym == A else 50.0
    return dict(close=px, atr=px * 0.02, time=t, le=le, se=False, lx=False, sx=False)


def tb(sleeves):
    e, _ = mk(GX, CAPITAL_CAP=10000.0, PAPER_BOOKS=True, PAPER_BOOK_CAP=2000.0, TRUE_BOOKS=True)
    e.S['CAP_SINCE'] = '2026-10-17T13:19:00+00:00'
    e.S['SLEEVES'] = list(sleeves)
    e._cohort_init()
    e.marks_t = time.time()
    e.state['funding_read_ms'] = int(time.time() * 1000)
    assert {TB.book_of(sl['key']) for sl in sleeves} == {'B1_FAST'}
    return e


def one_cycle(e, entries, t=None):
    """One 15m candle close with entry signals for `entries` (the ml_shadow01 pattern: compute_signals overridden)."""
    t = t or candle()
    frames = {A: frame(100.0, t), B: frame(50.0, t)}
    sigs = {f"{sl['id']}|{s}": sig(s, t) for sl in e.S['SLEEVES'] for s in sl['symbols'] if s in entries}
    e.compute_signals = lambda tf, syms, extra=(): ({k: dict(v) for k, v in sigs.items()}, frames)
    e.cycle('15m')


def app_pass(e):
    """One app.py main-loop pass between candles (same order as app.py): manage, actual costs, true books."""
    e.manage(e.data.marks()); e.apply_costs(); e._tb_on() and e.true_books()


def deliver(e):
    """The enrichment worker resolves the held commission and hands it to Engine._cost_sink."""
    e.trade.gate.set(); assert e._fill_flush(10)


def opens(e):
    return [x[1] for x in e.trade.order_log if x[0] == 'open']


def held(e):
    return sorted(l['symbol'] for l in e.state['lots'].values())


# ------------------------------------------------------------------ fail-closed (must pass on master)
@pytest.mark.parametrize('order', ORDERS)
def test_unresolved_commission_fails_closed_in_both_orders(order):
    sleeves, first, second = ORDERS[order]
    e = tb(sleeves); e.trade.gate.clear()
    try:
        one_cycle(e, {A, B})
        assert opens(e) == [first] and held(e) == [first]
        miss = [m for m in e.missed if m['symbol'] == second]
        assert miss and any(w in miss[-1]['reason'] for w in PENDING), miss[-1]['reason']
        app_pass(e); app_pass(e)                                       # ticks while the commission is still in flight
        assert opens(e) == [first] and held(e) == [first]
        assert {x['reason'] for x in e.true_books()['B1_FAST']['incomplete']} == {'fees_actual_missing'}
    finally:
        e.trade.gate.set()


# ------------------------------------------------------------------ deferred reconsideration (missing on master)
@XFAIL
@pytest.mark.parametrize('order', ORDERS)
def test_resolved_commission_lets_the_deferred_candidate_open_in_the_same_candle(order):
    sleeves, first, second = ORDERS[order]
    e = tb(sleeves); e.trade.gate.clear()
    one_cycle(e, {A, B})
    assert opens(e) == [first]
    deliver(e)
    app_pass(e); app_pass(e)                                           # folded on pass 1; a manage-hosted retry acts on pass 2
    assert e.true_books()['B1_FAST']['complete'] or TB.cost_pending_only(e.true_books()['B1_FAST'])
    assert held(e) == sorted([A, B]), f'deferred {second} not reconsidered: {held(e)}'


@XFAIL
@pytest.mark.parametrize('order', ORDERS)
def test_reconsideration_never_duplicates_an_order(order):
    sleeves, first, second = ORDERS[order]
    e = tb(sleeves); e.trade.gate.clear()
    one_cycle(e, {A, B})
    deliver(e)
    for _ in range(2):                                                 # the reconsideration path triggered twice
        app_pass(e); app_pass(e); deliver(e)
    assert sorted(opens(e)) == sorted([A, B]), opens(e)                # exchange 'open' calls == 2 in total
    assert held(e) == sorted([A, B])                                   # one lot per symbol


# ------------------------------------------------------------------ guards (pass on master)
@pytest.mark.parametrize('order', ORDERS)
def test_a_deferred_candidate_expires_with_its_candle(order, monkeypatch):
    """Guard (trivially passes on master): a commission that lands only after the next candle never revives the stale
    signal - the next cycle has no entry signal for the deferred coin, so it must not be opened from the old one."""
    sleeves, first, second = ORDERS[order]
    e = tb(sleeves); e.trade.gate.clear()
    try:
        one_cycle(e, {A, B})
        assert opens(e) == [first]
        real = E.now_utc
        monkeypatch.setattr(E, 'now_utc', lambda: real() + timedelta(seconds=TF))
        one_cycle(e, set(), t=candle(1))                               # the next candle: no signal for either coin
    finally:
        deliver(e)
    app_pass(e); app_pass(e)
    assert opens(e) == [first] and held(e) == [first]


@pytest.mark.parametrize('order', ORDERS)
def test_only_cost_pending_refusals_are_deferred(order):
    """Guard: the second candidate is refused for another reason (already in a trade on this coin); it is never
    reconsidered when the first fill's commission resolves."""
    sleeves, first, second = ORDERS[order]
    e = tb(sleeves)
    sl2 = next(sl for sl in sleeves if second in sl['symbols'])
    assert e.open_lot(sl2, second, 'LONG', sig(second, candle()), None, e.equity()), e.last_skip
    deliver(e); e.apply_costs()
    assert e.true_books()['B1_FAST']['complete']                       # the earlier lot's commission is measured
    e.trade.gate.clear()
    one_cycle(e, {A, B})
    assert opens(e) == [second, first]
    miss = [m for m in e.missed if m['symbol'] == second]
    assert miss and miss[-1]['reason'] == 'already in a trade on this coin'
    deliver(e)
    app_pass(e); app_pass(e)
    assert opens(e) == [second, first] and held(e) == sorted([A, B])
