"""ORDER-SYM-01 (PAPER/testnet, test-first): B1_FAST candidates on one 15m candle. While the first fill's commission is in
flight the book refuses new risk (fail-closed stays). Once the commission lands inside the SAME candle the deferred
candidates must be reconsidered, so the opened set never depends on sleeve/symbol iteration order.
Contract: reconsideration re-runs entry_block/open_lot (fee-edge gate, AI filter, admit all see it again) and never replays
a stored order (fee edge, AI review, book caps / stop-risk, opposite-side funding, cohort state, entry pause); tie-break =
the original deterministic iteration order (the first still-eligible candidate wins; reversing the order reverses the
winner); deferred records are memory-only, keyed to their signal candle, never persisted, and dropped at candle roll, on a
non-cost_pending recheck, on terminal fill_records_lost, at a cohort boundary and on restart. Codex ruling 6290806436.
'Same candle' tick = one pass of app.py's main loop between candles: manage(marks), apply_costs(), true_books().
Clock frozen (E.now_utc + time.time) at FIXED; T0 is the decision candle closed at FIXED."""
import json, os, sys, threading, time
from datetime import datetime, timezone

import numpy as np, pandas as pd, pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_exec_cal00_enrich import mk, fast                             # noqa: E402,F401  (fast: autouse backoff)
from test_fee_asset01 import MX                                         # noqa: E402
import engine as E                                                      # noqa: E402
import true_books as TB                                                 # noqa: E402

XFAIL = pytest.mark.xfail(strict=True, reason='ORDER-SYM-01: contract conflict reported to Codex (fixture, not the deferral)')
A, B, C, X1, X2 = 'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT'
PX = {A: 100.0, B: 50.0, C: 20.0, X1: 2.0, X2: 1.0}
P_SL = E.sleeve('P', 'exp_scalp', 0.5, 0.02, 4, [A], tf='15m')          # B1_FAST
Q_SL = E.sleeve('Q', 'exp_scalp', 0.5, 0.02, 4, [B], tf='15m')          # B1_FAST
R_SL = E.sleeve('R', 'exp_scalp', 0.5, 0.02, 4, [C], tf='15m')          # B1_FAST
F_SL = E.sleeve('F', 'exp_scalp', 0.5, 0.01, 4, [X1, X2], tf='4h')      # B1_FAST filler lots, 1% risk: 1+1+2+2 < 8% stop-risk room
T_SL = E.sleeve('T', 'ema_mom', 0.5, 0.02, 4, [B], tf='4h')             # B3_TREND
ORDERS = {'AB': ([P_SL, Q_SL], A, B), 'BA': ([Q_SL, P_SL], B, A)}       # sleeve order = candidate iteration order
TF = 900
FIXED = datetime(2026, 10, 20, 12, 20, tzinfo=timezone.utc).timestamp()
T0 = '2026-10-20 12:00:00'                                              # decision candle closed at 12:15
T1 = '2026-10-20 12:15:00'
PENDING = ('cost_pending', 'INCOMPLETE (fees_actual_missing)')         # PR #163 text / master text


class GX(MX):
    """MX (+3 coins) whose userTrades read waits on a gate: the commission stays in flight until the test releases it."""
    live = []

    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.mark.update(PX); self.gate = threading.Event(); self.gate.set(); GX.live.append(self)

    def user_trades(self, s, oid):
        self.gate.wait(30); return super().user_trades(s, oid)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Frozen clock (clock[0] = epoch s) for E.now_utc and time.time; teardown ALWAYS opens every gate (no blocked worker)."""
    c = [FIXED]
    monkeypatch.setattr(E.time, 'time', lambda: c[0])
    monkeypatch.setattr(E, 'now_utc', lambda: datetime.fromtimestamp(c[0], timezone.utc))
    yield c
    for x in GX.live: x.gate.set()
    GX.live.clear()


@pytest.fixture
def eng(clock):
    """Engine factory (eng.clock = the frozen clock); teardown drains each engine's enrichment worker."""
    made = []

    def make(sleeves, held=True):
        e = tb(sleeves); made.append(e)
        if held: e.trade.gate.clear()
        return e
    make.clock = clock
    yield make
    for e in made:
        e.trade.gate.set(); e._fill_flush(10)


def frame(px, t_last, n=120):
    t = pd.date_range(end=pd.Timestamp(t_last, tz='UTC'), periods=n, freq='15min')
    c = np.full(n, px)
    return pd.DataFrame(dict(t=t, o=c, h=c * 1.01, l=c * 0.99, c=c, v=np.full(n, 1000.0), atr=np.full(n, px * 0.02),
                             e50=c * 0.98, st=np.ones(n)))


def sig(sym, t, le=True):
    return dict(close=PX[sym], atr=PX[sym] * 0.02, time=t, le=le, se=False, lx=False, sx=False)


def tb(sleeves):
    e, _ = mk(GX, CAPITAL_CAP=10000.0, PAPER_BOOKS=True, PAPER_BOOK_CAP=2000.0, TRUE_BOOKS=True,
              UNIVERSE=list(PX), SYMBOLS_ON={s: True for s in PX})
    e.S['CAP_SINCE'] = '2026-10-17T13:19:00+00:00'
    e.S['SLEEVES'] = list(sleeves)
    e._cohort_init()
    e.marks_t = time.time()
    e.state['funding_read_ms'] = int(time.time() * 1000)
    return e


def one_cycle(e, entries, t=T0, tf='15m'):
    """One candle close with entry signals for `entries` (the ml_shadow01 pattern: compute_signals overridden)."""
    frames = {s: frame(p, t) for s, p in PX.items()}
    sigs = {f"{sl['id']}|{s}": sig(s, t) for sl in e.S['SLEEVES'] if sl['tf'] == tf for s in sl['symbols'] if s in entries}
    e.compute_signals = lambda tf, syms, extra=(): ({k: dict(v) for k, v in sigs.items()}, frames)
    e.cycle(tf)


def app_pass(e):
    """One app.py main-loop pass between candles (same order as app.py): manage, actual costs, true books."""
    e.manage(e.data.marks()); e.apply_costs(); e._tb_on() and e.true_books()


def deliver(e):
    """The enrichment worker resolves the held commission and hands it to Engine._cost_sink."""
    e.trade.gate.set(); assert e._fill_flush(10)


def opens(e):
    return [x[1] for x in e.trade.order_log if x[0] == 'open']


def held(e, book=None):
    return sorted(l['symbol'] for l in e.state['lots'].values() if book is None or l.get('book') == book)


def miss_of(e, sym):
    m = [x for x in e.missed if x['symbol'] == sym]
    assert m, f'no miss logged for {sym}'
    return m[-1]['reason']


def b1(e):
    return e.true_books()['B1_FAST']


def fill_book(e, n=2):
    """n measured B1_FAST lots already open (commission delivered and folded): leaves 4 - n lot slots."""
    for s in (X1, X2)[:n]:
        assert e.open_lot(F_SL, s, 'LONG', sig(s, T0), None, e.equity()), e.last_skip
        deliver(e); e.apply_costs()                                    # measured before the next one is admitted
    assert b1(e)['complete'] and b1(e)['lots'] == n


# ------------------------------------------------------------------ fail-closed (must pass on master)
@pytest.mark.parametrize('order', ORDERS)
def test_unresolved_commission_fails_closed_in_both_orders(order, eng):
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves)
    one_cycle(e, {A, B})
    assert opens(e) == [first] and held(e) == [first]
    assert any(w in miss_of(e, second) for w in PENDING), miss_of(e, second)
    app_pass(e); app_pass(e)                                           # ticks while the commission is still in flight
    assert opens(e) == [first] and held(e) == [first]
    assert {x['reason'] for x in b1(e)['incomplete']} == {'fees_actual_missing'}


# ------------------------------------------------------------------ deferred reconsideration (missing on master)
@pytest.mark.parametrize('order', ORDERS)
def test_resolved_commission_lets_the_deferred_candidate_open_in_the_same_candle(order, eng):
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves)
    one_cycle(e, {A, B})
    assert opens(e) == [first]
    deliver(e)
    app_pass(e); app_pass(e)                                           # folded on pass 1; a manage-hosted retry acts on pass 2
    assert b1(e)['complete'] or TB.cost_pending_only(b1(e))
    assert held(e) == sorted([A, B]), f'deferred {second} not reconsidered: {held(e)}'


@pytest.mark.parametrize('order', ORDERS)
def test_reconsideration_never_duplicates_an_order(order, eng):
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves)
    one_cycle(e, {A, B})
    deliver(e)
    for _ in range(2):                                                 # the reconsideration path triggered twice
        app_pass(e); app_pass(e); deliver(e)
    assert sorted(opens(e)) == sorted([A, B]), opens(e)                # exchange 'open' calls == 2 in total
    assert held(e) == sorted([A, B])                                   # one lot per symbol


ROOM = {'ABC': ([P_SL, Q_SL, R_SL], A, B, C), 'ACB': ([P_SL, R_SL, Q_SL], A, C, B)}   # first, earliest-deferred, later:
# the deferred pair reversed -> the winner reversed


@pytest.mark.parametrize('order', ROOM)
def test_limited_room_never_exceeds_the_book_cap(order, eng):
    """Guard: 2 measured lots + the first fill leave room for ONE; whatever reconsiders, the book never passes 4 lots."""
    sleeves, first, d1, d2 = ROOM[order]
    e = eng(sleeves + [F_SL], held=False)
    fill_book(e); e.trade.gate.clear()
    one_cycle(e, {A, B, C})
    assert held(e, 'B1_FAST') == sorted([X1, X2, first])
    assert all(any(w in miss_of(e, s) for w in PENDING) for s in (d1, d2))
    deliver(e)
    for _ in range(3): app_pass(e)
    assert b1(e)['lots'] <= 4 and len(opens(e)) <= 4                   # never 2 extra opens


@pytest.mark.parametrize('order', ROOM)
def test_limited_room_reconsiders_the_earliest_deferred_first(order, eng):
    sleeves, first, d1, d2 = ROOM[order]
    e = eng(sleeves + [F_SL], held=False)
    fill_book(e); e.trade.gate.clear()
    one_cycle(e, {A, B, C})
    deliver(e)
    for _ in range(3): app_pass(e)
    assert held(e, 'B1_FAST') == sorted([X1, X2, first, d1]), held(e, 'B1_FAST')   # tie-break = iteration order
    assert opens(e).count(d2) == 0 and len(opens(e)) == 4               # full admit(): the cap holds d2 out


# ------------------------------------------------------------------ guards (pass on master)
def test_another_book_is_never_held_by_a_b1_commission_in_flight(eng):
    """Guard: a B1_FAST commission in flight never refuses or defers a 4h B3_TREND candidate."""
    e = eng([P_SL, T_SL])
    one_cycle(e, {A})
    assert opens(e) == [A] and any(x['reason'] == 'fees_actual_missing' for x in b1(e)['incomplete'])
    one_cycle(e, {B}, tf='4h')
    assert opens(e) == [A, B] and not [m for m in e.missed if m['symbol'] == B]
    lot = next(l for l in e.state['lots'].values() if l['symbol'] == B)
    assert lot['book'] == 'B3_TREND'
    app_pass(e); app_pass(e)
    assert opens(e) == [A, B]                                           # opened exactly once


@pytest.mark.parametrize('order', ORDERS)
def test_a_commission_that_never_arrives_drops_the_deferred_candidate(order, eng):
    """Guard: the gate never opens and the clock passes COST_WAIT_S; the deferred candidate is never admitted and the book
    refuses as INCOMPLETE, never as cost_pending."""
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves)
    one_cycle(e, {A, B})
    eng.clock[0] += E.Engine.COST_WAIT_S + 60
    e.state['funding_read_ms'] = int(eng.clock[0] * 1000)              # funding stays fresh: only the commission is missing
    for _ in range(3): app_pass(e)
    assert opens(e) == [first] and held(e) == [first]
    why = TB.admit(b1(e))
    assert why and 'INCOMPLETE' in why and 'cost_pending' not in why, why
    assert not TB.cost_pending_only(b1(e))


def test_a_commission_that_never_arrives_is_marked_lost(eng):
    """Was a strict-xfail finding on d5e3491 (entry lot never finalized before its first cost record); fixed by #163."""
    e = eng([P_SL])
    one_cycle(e, {A})
    eng.clock[0] += E.Engine.COST_WAIT_S + 60
    app_pass(e)
    lot = next(iter(e.state['lots'].values()))
    assert lot.get('fees_actual_partial') == ['fill_records_lost'], lot.get('fees_actual_partial')


@pytest.mark.parametrize('order', ORDERS)
def test_a_deferred_candidate_expires_with_its_candle(order, eng):
    """Guard (trivially passes on master): a commission that lands only after the next candle never revives the stale
    signal - the next cycle has no entry signal for the deferred coin, so it must not be opened from the old one."""
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves)
    one_cycle(e, {A, B})
    assert opens(e) == [first]
    eng.clock[0] += TF
    one_cycle(e, set(), t=T1)                                          # the next candle: no signal for either coin
    deliver(e)
    app_pass(e); app_pass(e)
    assert opens(e) == [first] and held(e) == [first]


@pytest.mark.parametrize('order', ORDERS)
def test_same_coin_next_candle_opens_once_from_the_fresh_signal(order, eng):
    """Guard: deferred at T0; the commission lands after the roll; a fresh T1 signal for the same slot/coin opens it
    exactly once, keyed to T1 - the T0 entry is discarded."""
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves)
    taken = []
    real = e._audit_cand
    e._audit_cand = lambda sl, sym, side, sg, decision, reason=None: (
        taken.append((sym, sg.get('time'))) if decision == 'taken' else None, real(sl, sym, side, sg, decision, reason))[1]
    one_cycle(e, {A, B})
    eng.clock[0] += TF
    deliver(e)
    app_pass(e); app_pass(e)                                           # T0 candle rolled: nothing reopens from T0
    assert opens(e) == [first]
    one_cycle(e, {second}, t=T1)
    assert opens(e).count(second) == 1 and held(e) == sorted([A, B])
    assert [t for s, t in taken if s == second] == [T1]


@pytest.mark.parametrize('order', ORDERS)
def test_a_fee_edge_change_while_deferred_is_seen_on_reconsideration(order, eng):
    """Guard: FEE_EDGE_GATE turned on (mult 20) while the candidate is deferred: it never opens once the commission lands,
    and a fresh open_lot proves the gate itself refuses it (re-checked, not replayed)."""
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves)
    one_cycle(e, {A, B})
    e.S.update(FEE_EDGE_GATE=True, FEE_EDGE_MULT=20.0)
    deliver(e)
    app_pass(e); app_pass(e)
    assert opens(e) == [first] and held(e) == [first]
    sl2 = next(sl for sl in sleeves if second in sl['symbols'])
    assert not e.open_lot(sl2, second, 'LONG', sig(second, T0), None, e.equity())
    assert e.last_skip.startswith('fee_edge'), e.last_skip


@pytest.mark.parametrize('order', ORDERS)
def test_entries_paused_while_deferred_prevents_the_reopen(order, eng):
    """Guard (stands in for a cohort boundary, which refuses while a lot is open): ENTRIES_PAUSED set while deferred."""
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves)
    one_cycle(e, {A, B})
    e.S['ENTRIES_PAUSED'] = True
    deliver(e)
    app_pass(e); app_pass(e)
    assert opens(e) == [first] and held(e) == [first]
    assert e.entry_block(next(sl for sl in sleeves if second in sl['symbols']), second, 'LONG') == 'entries paused'


@pytest.mark.parametrize('order', ORDERS)
def test_only_cost_pending_refusals_are_deferred(order, eng):
    """Guard: the second candidate is refused for another reason (already in a trade on this coin); it is never
    reconsidered when the first fill's commission resolves."""
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves, held=False)
    sl2 = next(sl for sl in sleeves if second in sl['symbols'])
    assert e.open_lot(sl2, second, 'LONG', sig(second, T0), None, e.equity()), e.last_skip
    deliver(e); e.apply_costs()
    assert b1(e)['complete']                                            # the earlier lot's commission is measured
    e.trade.gate.clear()
    one_cycle(e, {A, B})
    assert opens(e) == [second, first]
    assert miss_of(e, second) == 'already in a trade on this coin'
    deliver(e)
    app_pass(e); app_pass(e)
    assert opens(e) == [second, first] and held(e) == sorted([A, B])


def test_cohort_boundary_clears_the_deferred_set(eng):
    """A cohort boundary drops every deferred record (one terminal miss each) and nothing opens from it afterwards."""
    e = eng([P_SL, Q_SL]); rows = spy(e)
    one_cycle(e, {A, B})
    assert opens(e) == [A] and [r['sym'] for r in e._deferred] == [B]
    c = e.state['cohort']; e._start_cohort(c['started_utc'], c['seq'] + 1)
    assert e._deferred == [] and misses(rows, B) == ['cohort boundary']
    deliver(e)
    for _ in range(3): app_pass(e)
    assert opens(e) == [A] and held(e) == [A]


@pytest.mark.parametrize('order', ORDERS)
def test_restart_mid_deferral_never_opens_the_deferred_candidate(order, eng, monkeypatch):
    """Guard: deferral is memory-only. A fresh Engine on the same folder / account gets the first fill's commission and is
    then fully measured - the deferred candidate of the dead process never opens, and no deferred key is persisted."""
    sleeves, first, second = ORDERS[order]
    ctl = eng(sleeves); one_cycle(ctl, {first})                        # control: same fill, nothing deferred
    e = eng(sleeves); one_cycle(e, {A, B})
    assert opens(e) == [first] and e.save_state()
    keys = lambda x: set(json.load(open(x.F['state'], encoding='utf-8')))
    assert ctl.save_state() and keys(e) == keys(ctl)                   # nothing new appears in the saved state
    assert 'defer' not in open(e.F['state'], encoding='utf-8').read().lower()
    monkeypatch.setattr(E, 'Futures', lambda *a, **k: e.trade)        # restart: same data folder, same (fake) account
    e2 = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), e.dir)
    e2.data, e2._enr = e2.trade, E.FillEnricher()
    e2.S.update({k: e.S[k] for k in ('CAPITAL_CAP', 'PAPER_BOOKS', 'PAPER_BOOK_CAP', 'TRUE_BOOKS', 'CAP_SINCE', 'UNIVERSE',
                                     'SYMBOLS_ON', 'SLEEVES')})
    e2.connect(); e2.marks = e2.trade.marks(); e2._cohort_init(); e2.marks_t = time.time()
    lot = next(iter(e2.state['lots'].values()))
    assert lot['symbol'] == first and any(x['reason'] == 'fees_actual_missing' for x in b1(e2)['incomplete'])
    ref = lot['cost_refs'][0]                                          # the commission reaches the new process
    e2._cost_sink(dict(cost_ref=ref, symbol=first, commission_raw=[dict(asset='USDT', maker=False, amount=0.05, n=1)]))
    e2.apply_costs()
    assert b1(e2)['complete'], b1(e2)['incomplete']
    app_pass(e2); app_pass(e2)
    assert opens(e) == [first] and sorted(l['symbol'] for l in e2.state['lots'].values()) == [first]


# ------------------------------------------------------------------ decision-ledger truth (Codex 6291113027, Cowork 6291124835)
def spy(e):
    """Record every decision hook call (miss, ML row, shadow row, T05a audit) - the calls ARE the ledger rows."""
    rows = []
    for n in ('miss', '_ml_decision', '_shadow_signal', '_audit_cand'):
        real = getattr(e, n)
        def w(*a, _r=real, _n=n, **k):
            rows.append((_n, a, k)); return _r(*a, **k)
        setattr(e, n, w)
    return rows


def misses(rows, sym):
    return [a[4] for n, a, k in rows if n == 'miss' and a[1] == sym and (k.get('kind') if 'kind' in k else (a[5] if len(a) > 5 else None)) is None]


def status(rows, hook, sym):
    i = {'_ml_decision': 5, '_shadow_signal': 4}[hook]
    return [a[i] for n, a, k in rows if n == hook and a[1] == sym]


def taken(rows, sym):
    return [a[3].get('time') for n, a, k in rows if n == '_audit_cand' and a[1] == sym and a[4] == 'taken']


def pending(why):
    return any(w in why for w in PENDING)


@pytest.mark.parametrize('order', ORDERS)
def test_ledger_defer_resolve_open_is_one_pending_then_one_taken(order, eng):
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves); rows = spy(e)
    one_cycle(e, {A, B}); deliver(e)
    for _ in range(3): app_pass(e)
    assert misses(rows, second) == [] and not [m for m in e.missed if m['symbol'] == second], misses(rows, second)
    assert status(rows, '_ml_decision', second) == ['pending', 'taken']
    assert status(rows, '_shadow_signal', second) == ['pending', 'taken']
    assert taken(rows, second) == [T0] and held(e) == sorted([A, B])


@pytest.mark.parametrize('order', ORDERS)
@pytest.mark.parametrize('end', ['roll', 'lost'])
def test_ledger_a_dropped_deferral_is_recorded_once(order, end, eng):
    """Guard: a deferral dropped at the candle roll / after COST_WAIT_S leaves ONE final miss + ONE skipped row, never one
    per manage tick (master: the original refusal is that one record)."""
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves); rows = spy(e)
    one_cycle(e, {A, B})
    eng.clock[0] += TF if end == 'roll' else E.Engine.COST_WAIT_S + 60
    e.state['funding_read_ms'] = int(eng.clock[0] * 1000)
    if end == 'roll': one_cycle(e, set(), t=T1); deliver(e)
    for _ in range(5): app_pass(e)
    assert len(misses(rows, second)) == 1 and len([m for m in e.missed if m['symbol'] == second]) == 1
    assert status(rows, '_ml_decision', second).count('skipped') <= 1 and status(rows, '_shadow_signal', second).count('skipped') <= 1
    assert opens(e) == [first]


@pytest.mark.parametrize('order', ORDERS)
@pytest.mark.parametrize('end', ['roll', 'lost'])
def test_ledger_a_dropped_deferral_records_its_terminal_reason_once(order, end, eng):
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves); rows = spy(e)
    one_cycle(e, {A, B})
    eng.clock[0] += TF if end == 'roll' else E.Engine.COST_WAIT_S + 60
    e.state['funding_read_ms'] = int(eng.clock[0] * 1000)
    if end == 'roll': one_cycle(e, set(), t=T1); deliver(e)
    for _ in range(5): app_pass(e)
    ms = misses(rows, second)
    assert len(ms) == 1 and not pending(ms[0]), ms                     # the terminal reason, not the cost_pending one
    assert status(rows, '_ml_decision', second) == ['pending', 'skipped']


@pytest.mark.parametrize('order', ORDERS)
def test_reconsideration_calls_open_lot_exactly_once(order, eng):
    """open_lot is the normal path (AI review, fee edge, admit run inside it): one more call for the deferred coin."""
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves)
    one_cycle(e, {A, B})
    calls, real = [], e.open_lot
    e.open_lot = lambda sl, sym, *a, **k: (calls.append(sym), real(sl, sym, *a, **k))[1]
    deliver(e)
    for _ in range(3): app_pass(e); deliver(e)
    assert calls == [second], calls


@pytest.mark.parametrize('order', ORDERS)
@pytest.mark.parametrize('change', ['fee_edge', 'paused'])
def test_a_state_change_while_deferred_is_the_recorded_recheck_reason(order, change, eng):
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves); rows = spy(e)
    one_cycle(e, {A, B})
    assert status(rows, '_ml_decision', second) == ['pending'] and misses(rows, second) == []   # deferred: pending, no miss
    e.S.update(dict(FEE_EDGE_GATE=True, FEE_EDGE_MULT=20.0) if change == 'fee_edge' else dict(ENTRIES_PAUSED=True))
    deliver(e)
    for _ in range(3): app_pass(e)
    assert opens(e) == [first]
    want = 'fee_edge' if change == 'fee_edge' else 'entries paused'
    ms = misses(rows, second)
    assert len(ms) == 1 and ms[0].startswith(want), ms                   # ONE terminal miss: the re-check reason


@pytest.mark.parametrize('order', ORDERS)
def test_race_at_the_roll_opens_once_from_the_fresh_signal(order, eng, monkeypatch):
    """T0 deferred; the T1 cycle runs with the commission STILL in flight (T1 refused pending, T0 dropped); the commission
    lands within T1 -> exactly one open, keyed to T1."""
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves); rows = spy(e)
    one_cycle(e, {A, B})
    monkeypatch.setattr(E.Engine, 'COST_WAIT_S', 3 * TF)               # still IN FLIGHT at T1 (not yet fill_records_lost)
    eng.clock[0] += TF
    e.marks_t = eng.clock[0]; e.state['funding_read_ms'] = int(eng.clock[0] * 1000)          # fresh feeds at T1
    one_cycle(e, {second}, t=T1)
    assert opens(e) == [first] and status(rows, '_ml_decision', second)[-1] == 'pending'   # T1 deferred, not a miss
    deliver(e)
    for _ in range(3): app_pass(e)
    assert opens(e).count(second) == 1 and taken(rows, second) == [T1], (opens(e), taken(rows, second))
    ms = misses(rows, second)
    assert len(ms) == 1 and not pending(ms[0]), ms                     # only T0's terminal (candle-roll) miss


@pytest.mark.parametrize('order', ROOM)
def test_stop_risk_room_holds_the_deferred_candidates_out(order, eng):
    """Guard: lot slots left (2 of 4) but the book's stop-risk room (STOP_RISK_MAX x NAV) cannot take another lot."""
    sleeves, first, d1, d2 = ROOM[order]
    e = eng(sleeves + [F_SL], held=False)
    fill_book(e, 1); e.trade.gate.clear()
    one_cycle(e, {A, B, C})
    b = b1(e); r = next(l for l in e.state['lots'].values() if l['symbol'] == first)['risk_usd']
    cap = TB.STOP_RISK_MAX * b['nav_worse']
    f = next(l for l in e.state['lots'].values() if l['symbol'] == X1)
    f['stop'] = f['avg'] - (float(cap - b['stop_risk']) - r / 2 + float(TB.stop_risk(f))) / f['qty']   # room left = r/2
    assert f['stop'] > 0 and b1(e)['lots'] == 2
    deliver(e)
    for _ in range(3): app_pass(e)
    b = b1(e)
    assert 'stop-risk cap' in (TB.admit(b, 0, r) or ''), TB.admit(b, 0, r)
    assert held(e, 'B1_FAST') == sorted([X1, first]) and b['stop_risk'] <= cap


def test_ledger_hook_signatures_are_the_ones_the_spies_index():
    """Cowork nit: misses()/status()/taken() read positional args; an arity change must fail here, by name."""
    import inspect
    sig = lambda f: list(inspect.signature(f).parameters)
    assert sig(E.Engine.miss)[:7] == ['self', 'sl', 'sym', 'side', 'sg', 'reason', 'kind']
    assert sig(E.Engine._ml_decision)[:7] == ['self', 'sl', 'sym', 'side', 'sg', 'frames', 'status']
    assert sig(E.Engine._shadow_signal)[:6] == ['self', 'sl', 'sym', 'side', 'sg', 'status']
    assert sig(E.Engine._audit_cand)[:6] == ['self', 'sl', 'sym', 'side', 'sg', 'decision']


@pytest.mark.parametrize('order', ORDERS)
def test_no_retry_while_the_commission_is_still_in_flight(order, eng):
    """A still-pending book is checked cheaply: open_lot (AI review, fee edge, admit) is not re-run on every tick."""
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves)
    one_cycle(e, {A, B})
    calls, real = [], e.open_lot
    e.open_lot = lambda sl, sym, *a, **k: (calls.append(sym), real(sl, sym, *a, **k))[1]
    for _ in range(3): app_pass(e)                                       # commission still held by the gate
    assert calls == [] and len(e._deferred) == 1


@pytest.mark.parametrize('order', ORDERS)
def test_reconsideration_never_re_enters_itself(order, eng):
    """A nested apply_costs() from inside the retried open_lot never starts a second reconsideration pass."""
    sleeves, first, second = ORDERS[order]
    e = eng(sleeves)
    one_cycle(e, {A, B})
    calls, real = [], e.open_lot
    def nested(sl, sym, *a, **k):
        calls.append(sym); e.apply_costs()                               # a future nested caller
        return real(sl, sym, *a, **k)
    e.open_lot = nested
    deliver(e); app_pass(e)
    assert calls == [second] and opens(e).count(second) == 1
