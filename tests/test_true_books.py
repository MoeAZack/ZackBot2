"""TRUE-BOOK-01 (PAPER/testnet): five isolated $2,000 books. Each book sizes from its own NAV, holds at most 4 lots, keeps
notional and stop-risk inside its own NAV, halts alone at NAV <= 0, and reports per-strategy attribution. Strategy logic
and risk % are unchanged; only isolation / accounting / aggregate limits."""
import copy, inspect, json, time
from datetime import datetime, timezone
from decimal import Decimal
import pytest

import binance_client as BC
import engine as E
import paper_books as PB
import true_books as TB
from test_safety import mk_engine, SG

T_SL = E.sleeve('T', 'ema_mom', 0.5, 0.02, 4, 'all')          # B3_TREND
T2_SL = E.sleeve('U', 'donchian_ens', 0.5, 0.02, 4, 'all')    # B3_TREND, another strategy
R_SL = E.sleeve('R', 'exp_range', 0.5, 0.02, 4, 'all')        # B2_RANGE


FOK = dict(ok=True, why=None, through_ms=None)                # a healthy funding reader (pure tests)


def C(*a, **kw):
    kw.setdefault('funding', FOK)
    return TB.compute(*a, **kw)


def tb_engine(**kw):
    e, tmp = mk_engine(CAPITAL_CAP=10000.0, PAPER_BOOKS=True, PAPER_BOOK_CAP=2000.0, TRUE_BOOKS=True, **kw)
    e.S['CAP_SINCE'] = '2026-10-17T13:19:00+00:00'
    e.S['SLEEVES'] = [T_SL, T2_SL, R_SL]
    e._cohort_init()
    e.marks_t = time.time()                                    # a fresh mark feed (a stale one values nothing)
    e.trade.income = lambda *a, **k: []                        # a funding reader that has read everything (fail-closed otherwise)
    e.state['funding_read_ms'] = int(time.time() * 1000)
    return e, tmp


def cid(e):
    return e.state['cohort']['id']


def row(e, book, gross, fees=0.0, strategy='ema_mom', **kw):
    return dict(dict(id=f'h{len(e.history)}', book=book, cohort_id=cid(e), pnl_gross=gross, fees=fees, pnl=gross - fees,
                     fees_actual=fees, strategy_key=strategy, closed='2026-10-17T15:00:00+00:00', r=gross / 40,
                     hours=1.0, exit_reason='stop'), **kw)


def fake_lot(e, book, sym='ETHUSDT', side='LONG', avg=100.0, qty=1.0, stop=98.0, **kw):
    return dict(dict(symbol=sym, side=side, avg=avg, qty=qty, stop=stop, book=book, cohort_id=cid(e), sleeve='X',
                     fees=0.0, fees_actual=0.0, realized=0.0, opened='2026-10-17T14:00:00+00:00'), **kw)


def settle(e):
    """The enrichment worker delivered every fill's commission (the fake exchange never sends one)."""
    for l in e.state['lots'].values():
        l['fees_actual'] = l.get('fees', 0.0); l.pop('fees_actual_partial', None)


def size_of(e, sl, side='LONG', sym='BTCUSDT'):
    """Risk the engine would size for this slot now (open, read, close - nothing left behind)."""
    n = len(e.history)
    assert e.open_lot(sl, sym, side, SG, None, e.equity()), e.last_skip
    k, lot = next((k, l) for k, l in e.state['lots'].items() if l['sleeve'] == sl['id'] and l['symbol'] == sym)
    r = lot['risk_usd']
    e.state['lots'].pop(k); del e.history[n:]
    return r


# ------------------------------------------------------------------ pure
def test_nav_is_start_plus_gross_plus_unrealized_minus_actual_fees_plus_funding():
    b = C('C', [dict(book='B1_FAST', cohort_id='C', pnl_gross=30, fees=1.0, fees_actual=0.8, funding_actual=-0.2)],
                   [dict(book='B1_FAST', cohort_id='C', symbol='X', side='SHORT', avg=10, qty=2, stop=11, realized=4, fees=0.3,
                         fees_actual=0.25, funding_actual=0.1)], marks={'X': 9})['B1_FAST']
    assert b['nav'] == Decimal('2000') + 30 + 4 + 2 - Decimal('0.8') - Decimal('0.25') - Decimal('0.2') + Decimal('0.1')
    assert b['nav_basis'] == 'actual' and b['complete'] and b['stop_risk'] == 2 and b['notional'] == 18   # exposure at the mark


def test_missing_actual_fee_falls_back_to_estimate_and_is_incomplete():
    b = C('C', [dict(book='B2_RANGE', cohort_id='C', pnl_gross=10, fees=1.0, fees_actual=None,
                              fees_actual_partial=['fill_records_lost'])])['B2_RANGE']
    assert b['nav'] == 2009 and b['nav_basis'] == 'estimate_fallback' and not b['complete']
    assert b['incomplete'][0]['reason'] == 'fees_actual_missing'


def test_ambiguous_funding_on_a_row_makes_the_book_incomplete():
    b = C('C', [dict(book='B4_BREAKOUT', cohort_id='C', pnl_gross=1, fees=0, fees_actual=0,
                              funding_partial=['ambiguous_opposite_side_funding'], funding_unattributed=1)])['B4_BREAKOUT']
    assert not b['complete'] and b['incomplete'][0]['reason'] == 'ambiguous_opposite_side_funding'


def test_one_book_loss_never_moves_another_book():
    base = [dict(book=k, cohort_id='C', pnl_gross=5, fees=0, fees_actual=0) for k in PB.IDS]
    a = C('C', base)
    b = C('C', base + [dict(book='B1_FAST', cohort_id='C', pnl_gross=-700, fees=0, fees_actual=0)])
    assert b['B1_FAST']['nav'] == a['B1_FAST']['nav'] - 700
    assert all(TB.public({k: a[k]}) == TB.public({k: b[k]}) for k in PB.IDS if k != 'B1_FAST')


def test_halt_on_the_worse_basis_and_sticky():
    rows = [dict(book='B5_DISCOVERY', cohort_id='C', pnl_gross=-1999.5, fees=1.0, fees_actual=0.2)]
    b = C('C', rows)['B5_DISCOVERY']
    assert b['nav'] > 0 and b['nav_estimate'] < 0 and b['state'] == 'HALTED' and b['halt']['basis'] == 'estimate'
    b2 = C('C', [], halted={'B5_DISCOVERY': dict(basis='estimate')})['B5_DISCOVERY']
    assert b2['nav'] == 2000 and b2['state'] == 'HALTED'                              # sticky after a recovery
    assert TB.admit(b2) .startswith('book B5_DISCOVERY halted')


def test_peak_and_dd_rederived_from_the_ordered_close_series():
    rows = [dict(book='B3_TREND', cohort_id='C', pnl_gross=g, fees=0, fees_actual=0, closed=t)
            for g, t in ((100, '2026-10-17T10:00'), (-300, '2026-10-17T11:00'))]
    b = C('C', list(reversed(rows)))['B3_TREND']
    assert b['peak'] == 2100 and b['nav'] == 1800 and b['dd'] == 1 - Decimal(1800) / Decimal(2100)


def test_stop_risk_uses_the_current_stop_floored_at_zero():
    assert TB.stop_risk(dict(side='LONG', avg=100, stop=95, qty=2)) == 10
    assert TB.stop_risk(dict(side='LONG', avg=100, stop=101, qty=2)) == 0           # profit-locked: no risk
    assert TB.stop_risk(dict(side='SHORT', avg=100, stop=104, qty=1)) == 4
    assert TB.stop_risk(dict(side='SHORT', avg=100, qty=1)) is None


def test_other_cohort_lots_hold_capacity_but_never_value():
    b = C('NEW', [dict(book='B1_FAST', cohort_id='OLD', pnl_gross=500, fees=0, fees_actual=0)],
                   [dict(book='B1_FAST', cohort_id='OLD', symbol='X', side='LONG', avg=10, qty=1, stop=9, realized=50)],
                   marks={'X': 20})['B1_FAST']
    assert b['nav'] == 2000 and b['closed'] == 0 and b['lots'] == 1 and b['stop_risk'] == 1


def test_unknown_strategy_has_no_book():
    assert TB.book_of('brand_new_thing') is None and TB.book_of('ema_mom') == 'B3_TREND'


# ------------------------------------------------------------------ engine: sizing from the book's own NAV
def test_b3_loss_shrinks_only_b3_next_size():
    e, _ = tb_engine()
    t0, r0 = size_of(e, T_SL), size_of(e, R_SL)
    e.history.append(row(e, 'B3_TREND', -500))
    assert size_of(e, T_SL) == pytest.approx(t0 * 1500 / 2000) and size_of(e, R_SL) == pytest.approx(r0)


def test_true_books_off_keeps_paper_books_sizing_on_uses_the_2000_book():
    e, _ = tb_engine(); on = size_of(e, T_SL)
    e2, _ = tb_engine(); e2.S['TRUE_BOOKS'] = False; off = size_of(e2, T_SL)
    alloc = PB.allocation(e2.equity(), 2000.0)                                      # fake account 500: 100 per book
    assert off == pytest.approx(alloc * 0.02 * e2.kelly_mult(T_SL) * e2.governor_mult())
    assert on == pytest.approx(off * 2000 / alloc)                                  # contract: each book starts at 2,000
    e2.history.append(row(e2, 'B3_TREND', -500))
    assert size_of(e2, T_SL) == off                                                 # off: a book loss never sizes


def test_a_strategy_with_no_book_is_refused_never_parked_in_discovery():
    e, _ = tb_engine()
    odd = dict(T_SL, id='Z', key='no_such_strategy'); e.S['SLEEVES'].append(odd)       # a future, unmapped strategy
    assert not e.open_lot(odd, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert 'has no book' in e.last_skip and not e.state['lots']


# ------------------------------------------------------------------ engine: book-wide limits
def test_four_lots_in_a_book_block_its_fifth_from_any_strategy_other_books_stay_open():
    e, _ = tb_engine()
    for i in range(4): e.state['lots'][f'x{i}'] = fake_lot(e, 'B3_TREND', stop=99.9, qty=0.1)
    assert not e.open_lot(T2_SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert e.last_skip == 'book B3_TREND full (4/4 lots)'
    assert e.open_lot(R_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip


def test_a_resting_entry_holds_a_slot_and_reserves_its_risk():
    e, _ = tb_engine()
    for i in range(3): e.state['lots'][f'x{i}'] = fake_lot(e, 'B3_TREND', stop=99.9, qty=0.1)
    e.state['resting_entries']['r1'] = dict(symbol='ETHUSDT', side='LONG', qty=1.0, filled=0.0, cost=0.0, n=1, status='open', sleeve='T',
                                            cid='zbcidx', plan=dict(book='B3_TREND', qty=1.0, px=100.0, stop_dist=2.0))
    b = e.true_books()['B3_TREND']
    assert b['working'] == 1 and b['stop_risk'] == Decimal('2.03') and b['notional'] == 115     # 3 x 0.01 + 2.0 reserved; lots at mark 50
    assert not e.open_lot(T2_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()) and 'full (4/4' in e.last_skip


def test_stop_risk_cap_is_eight_percent_of_book_nav():
    e, _ = tb_engine()
    e.state['lots']['x'] = fake_lot(e, 'B3_TREND', avg=100, stop=90, qty=15.5)      # 155 of 160 at risk already
    assert not e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert e.last_skip.startswith('book B3_TREND stop-risk cap')
    assert e.open_lot(R_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip


def test_leverage_cap_is_from_the_book_nav():
    e, _ = tb_engine()
    nav = 2000.0; e.history.append(row(e, 'B3_TREND', -1000))                      # B3 NAV 1000
    e.state['lots']['x'] = fake_lot(e, 'B3_TREND', avg=50.0, stop=50.0, qty=e.S['MAX_LEVERAGE'] * 1000 / 50)   # at the mark
    assert e.true_books()['B3_TREND']['nav'] == 1000
    assert not e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert e.last_skip == 'leverage cap reached for this slot'                     # sized room from NAV 1000 is 0
    assert e.true_book_block(T_SL, dict(notional=10.0, risk=0.0)).startswith('book B3_TREND leverage cap')
    assert e.open_lot(R_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip and nav


def test_add_counts_notional_and_stop_risk_not_a_lot():
    e, _ = tb_engine()
    for i in range(3): e.state['lots'][f'x{i}'] = fake_lot(e, 'B3_TREND', stop=99.9, qty=0.1)
    assert e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    k, lot = next((k, l) for k, l in e.state['lots'].items() if l['sleeve'] == 'T')
    assert e._add_block(lot, 0.001, 100.0).startswith('book B3_TREND INCOMPLETE (fees_actual_missing)')  # fill cost unknown
    settle(e)
    b = e.true_books()['B3_TREND']
    room = TB.STOP_RISK_MAX * b['nav_worse'] - b['stop_risk']
    gap = abs(100.0 - float(lot['stop']))
    assert e._add_block(lot, float(room) / gap * 0.5, 100.0) is None                 # 4 lots: an add is still allowed
    why = e._add_block(lot, float(room) / gap * 1.5, 100.0)
    assert why and why.startswith('book B3_TREND stop-risk cap')


def test_shrinking_nav_refuses_new_risk_but_never_reduces_existing_lots():
    e, _ = tb_engine()
    e.state['lots']['x'] = fake_lot(e, 'B3_TREND', avg=100, stop=95, qty=20)         # 100 at risk = 5 % of 2000
    e.history.append(row(e, 'B3_TREND', -1000))                                    # NAV 1000: 100 > 80 cap
    snap = copy.deepcopy(e.state['lots'])
    assert not e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert e.state['lots'] == snap                                                  # refuse-only


# ------------------------------------------------------------------ HALT
def test_halted_book_refuses_new_risk_alone_and_stays_halted_across_restart():
    e, tmp = tb_engine()
    e.history.append(row(e, 'B3_TREND', -2001))
    assert e.true_books()['B3_TREND']['state'] == 'HALTED' and 'B3_TREND' in e.state['true_books']['halted']
    assert not e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()) and 'halted' in e.last_skip
    assert e.open_lot(R_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    e.history.append(row(e, 'B3_TREND', 5000))                                      # a later "recovery" never unhalts
    assert e.true_books()['B3_TREND']['state'] == 'HALTED'
    e._save_aux('history', e.history)                     # no explicit state save: the halt itself was persisted
    e2, _ = mk_engine(tmp=tmp, CAPITAL_CAP=10000.0, PAPER_BOOKS=True, PAPER_BOOK_CAP=2000.0, TRUE_BOOKS=True)
    e2.S['SLEEVES'] = [T_SL, T2_SL, R_SL]; e2.S['CAP_SINCE'] = e.S['CAP_SINCE']; e2._cohort_init()
    assert e2.true_books()['B3_TREND']['state'] == 'HALTED'


def test_a_new_cohort_starts_every_book_fresh():
    e, _ = tb_engine()
    e.history.append(row(e, 'B3_TREND', -2001)); e.true_books()
    e.state['cohort'] = dict(e.state['cohort'], id='CH-next')
    b = e.true_books()['B3_TREND']
    st = e.state['true_books']
    assert b['state'] == 'ACTIVE' and b['nav'] == 2000 and st['cohort_id'] == 'CH-next' and st['halted'] == {}
    assert {float(v) for v in st['peak'].values()} == {2000.0}


# ------------------------------------------------------------------ restart identity, attribution, API
def test_restart_reproduces_nav_limits_and_ownership():
    e, tmp = tb_engine()
    assert e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert e.open_lot(R_SL, 'ETHUSDT', 'LONG', SG, None, e.equity())
    e.history.append(row(e, 'B3_TREND', -37.5, fees=0.4)); e.history.append(row(e, 'B2_RANGE', 12.25, strategy='exp_range'))
    e.save_state(); e._save_aux('history', e.history)
    marks = {'BTCUSDT': 101.0, 'ETHUSDT': 99.0}
    before = TB.public(e.true_books(marks))
    e2, _ = mk_engine(tmp=tmp, CAPITAL_CAP=10000.0, PAPER_BOOKS=True, PAPER_BOOK_CAP=2000.0, TRUE_BOOKS=True)
    e2.S['SLEEVES'] = [T_SL, T2_SL, R_SL]; e2.S['CAP_SINCE'] = e.S['CAP_SINCE']; e2._cohort_init()
    e2.trade.income = e.trade.income                                                 # funding_read_ms came back with state
    assert TB.public(e2.true_books(marks)) == before
    assert all(l['book'] and l['cohort_id'] == cid(e2) for l in e2.state['lots'].values())


def test_per_strategy_attribution_inside_a_book():
    e, _ = tb_engine()
    e.history += [row(e, 'B3_TREND', 30, fees=1, strategy='ema_mom', id='a'), row(e, 'B3_TREND', -10, fees=1, strategy='ema_mom', id='b'),
                  row(e, 'B3_TREND', 5, fees=0.5, strategy='donchian_ens', exit_reason='take_profit', id='c')]
    st = {b['id']: b for b in e.true_books_status()['books']}['B3_TREND']['strategies']
    assert st['ema_mom']['closed'] == 2 and st['ema_mom']['net'] == 18 and st['ema_mom']['win_rate'] == 0.5
    assert st['ema_mom']['pf'] == pytest.approx(29 / 11, abs=1e-4) and st['ema_mom']['fee_drag'] == 2
    assert st['donchian_ens']['exits'] == {'take_profit': 1}


def test_status_lists_five_books_and_is_off_in_live_mode():
    e, _ = tb_engine()
    s = e.true_books_status()
    assert s['on'] and [b['id'] for b in s['books']] == list(PB.IDS) and all(b['nav'] == 2000 for b in s['books'])
    assert s['stop_risk_max'] == 0.08 and s['max_lots'] == 4
    e.live = True
    assert e._tb_on() is False and e.true_book_block(T_SL) is None


def test_exits_and_stops_are_per_lot_quantities_never_close_position():
    """Same-side lots of different books net into one hedge leg: each exit / stop must carry its own lot's qty."""
    for fn in (BC.Futures.stop, BC.Futures.close):
        src = inspect.getsource(fn)
        assert 'closePosition' not in src and 'quantity' in src


# ------------------------------------------------------------------ review r1 regressions
def test_maker_fallback_remainder_is_gated_by_the_book():
    from test_v31_engine import Book, age
    e, _ = tb_engine(ENTRY_ORDER='maker', MAKER_REPRICE=2, MAKER_WAIT_S=30, MAKER_FALLBACK=True)
    bk = Book(e.trade)
    assert e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    e.history.append(row(e, 'B3_TREND', -2001)); e.true_books()                    # the book halts while the order rests
    bk.fill(0.4); age(e); e.manage(e.trade.marks())
    for _ in range(3): age(e, total=True); e.manage(e.trade.marks())
    lot = next(iter(e.state['lots'].values()))
    assert [f[1] for f in lot['fills']] == ['entry']                                # no entry_fallback remainder
    assert e._fill_flush(5)
    mk = [json.loads(x) for x in open(e.F['fills']) if '"entry_maker"' in x][-1]
    assert 'halted' in mk['fallback_blocked'] and not mk.get('fallback_attempted')
    assert e._tb_add_block(lot, 0.1, 100.0).startswith('book B3_TREND halted')


@pytest.mark.parametrize('how', ['none', 'conflict', 'ended'])
def test_no_open_cohort_means_no_new_risk(how):
    e, _ = tb_engine()
    if how == 'none': e.state['cohort'] = None
    elif how == 'conflict': e._cohort_conflict = True
    else: e.state['cohort'] = dict(e.state['cohort'], ended_utc='2026-10-17T20:00:00+00:00')
    assert not e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()) and 'no open cohort' in e.last_skip


def test_unstamped_rows_never_value_a_book_without_a_cohort():
    b = C(None, [dict(book='B1_FAST', pnl_gross=-5000, fees=0, fees_actual=0)])['B1_FAST']
    assert b['nav'] == 2000 and b['closed'] == 0


def test_nav_survives_the_history_window_through_the_cohort_store():
    e, _ = tb_engine()
    old = row(e, 'B3_TREND', -300, id='old')
    e.cohort_rows.append(old)                                   # trimmed out of history, kept in the evidence store
    e.cohort_rows.append(row(e, 'B3_TREND', 0, id='late', fees=1.0))
    e.history.append(row(e, 'B3_TREND', 0, id='late', fees=1.0, fees_actual=0.5, cost_rev=1))   # late actual fee (revision)
    b = e.true_books()['B3_TREND']
    assert b['closed'] == 2 and b['nav'] == Decimal('1699.5')


def test_unmeasurable_risk_blocks_new_risk_in_that_book_only():
    e, _ = tb_engine()
    e.state['lots']['x'] = fake_lot(e, 'B3_TREND', sym='SOLUSDT')                  # no mark for this coin
    assert not e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert 'INCOMPLETE (mark_missing)' in e.last_skip
    assert e.open_lot(R_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip


def test_reserved_notional_sizes_down_instead_of_refusing():
    e, _ = tb_engine(MAX_LEVERAGE=1)
    free = size_qty = None
    assert e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()); free = next(iter(e.state['lots'].values()))['qty']
    e.state['lots'].clear()
    e.state['resting_entries']['r1'] = dict(symbol='ETHUSDT', side='LONG', qty=1.0, filled=0.0, cost=0.0, n=1, status='open',
                                            sleeve='U', cid='zbcidy', plan=dict(book='B3_TREND', qty=39.0, px=50.0, stop_dist=0.0))
    assert e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip   # 1950 of 2000 reserved: 50 left
    size_qty = next(iter(e.state['lots'].values()))['qty']
    assert size_qty < free and size_qty * 100 <= 50 + 1e-6


def test_status_read_never_persists_a_halt():
    e, _ = tb_engine()
    e.history.append(row(e, 'B3_TREND', -2001))
    s = e.true_books_status()
    assert {b['id']: b['state'] for b in s['books']}['B3_TREND'] == 'HALTED' and e.state['true_books']['halted'] == {}


def test_the_halt_itself_is_saved_at_once():
    e, tmp = tb_engine()
    e.history.append(row(e, 'B3_TREND', -2001)); e.true_books()                      # nothing else saves state after this
    e.history.append(row(e, 'B3_TREND', 5000)); e._save_aux('history', e.history)
    e2, _ = mk_engine(tmp=tmp, CAPITAL_CAP=10000.0, PAPER_BOOKS=True, PAPER_BOOK_CAP=2000.0, TRUE_BOOKS=True)
    e2.S['SLEEVES'] = [T_SL, T2_SL, R_SL]; e2.S['CAP_SINCE'] = e.S['CAP_SINCE']; e2._cohort_init()
    b = e2.true_books()['B3_TREND']
    assert b['nav'] > 0 and b['state'] == 'HALTED'


# ------------------------------------------------------------------ Cowork draft attack (6239405013 / 6239421192)
def test_manual_or_unknown_book_activity_makes_every_book_incomplete():
    e, _ = tb_engine()
    e.state['lots']['m'] = fake_lot(e, None, manual=True, cohort_id=None)            # a manual "Take now" lot
    books = e.true_books()
    assert all(not b['complete'] and b['incomplete'][-1] == dict(reason='unowned_account_activity', rows=0, lots=1, working=0)
               for b in books.values())
    e.state['lots'].clear()
    e.history.append(dict(row(e, None, -500), cohort_id=None, closed='2099-01-01T00:00:00+00:00'))   # manual close since start
    assert all(b['incomplete'][-1]['reason'] == 'unowned_account_activity' for b in e.true_books().values())
    b = C('C', [dict(book='B9_X', cohort_id='C', pnl_gross=-1, fees=0)])['B1_FAST']
    assert b['incomplete'] == [dict(reason='unowned_account_activity', rows=1, lots=0, working=0)]


@pytest.mark.parametrize('bad', [None, 'nan', 'abc'])
def test_unreadable_gross_or_fee_is_incomplete_never_zero(bad):
    b = C('C', [dict(book='B1_FAST', cohort_id='C', pnl_gross=bad, fees=0, fees_actual=0)])['B1_FAST']
    assert not b['complete'] and b['incomplete'][0]['reason'] == 'pnl_unreadable'
    b = C('C', [], [dict(book='B1_FAST', cohort_id='C', symbol='X', side='LONG', avg=1, qty=1, stop=1, realized=1,
                                  fees=bad, fees_actual=0)], marks={'X': 1})['B1_FAST']
    assert [x['reason'] for x in b['incomplete']] == ['fees_unreadable']


def test_multi_asset_funding_is_incomplete_and_no_funding_is_complete():
    rows = [dict(book='B1_FAST', cohort_id='C', pnl_gross=1, fees=0, fees_actual=0, funding_actual=None,
                 funding_by_asset={'USDT': -1.0, 'USDC': -0.5}, funding_rows=2),
            dict(book='B2_RANGE', cohort_id='C', pnl_gross=1, fees=0, fees_actual=0)]
    out = C('C', rows)
    assert out['B1_FAST']['incomplete'][0] == dict(reason='funding_multi_asset', id=None, assets=['USDC', 'USDT'])
    assert out['B2_RANGE']['complete']


def test_drawdown_sees_an_unrealized_high_through_the_persisted_peak():
    e, _ = tb_engine()
    e.state['lots']['x'] = fake_lot(e, 'B3_TREND', sym='ETHUSDT', avg=50.0, qty=10, stop=40.0)
    e.true_books({'ETHUSDT': 100.0})                                                # +500 open: peak 2500 sampled
    b = e.true_books({'ETHUSDT': 50.0})
    assert b['B3_TREND']['peak'] == 2500 and b['B3_TREND']['dd'] == Decimal(500) / Decimal(2500)
    assert TB.D(e.state['true_books']['peak']['B3_TREND']) == 2500


def test_an_unreadable_cohort_never_drops_a_sticky_halt():
    e, _ = tb_engine()
    e.history.append(row(e, 'B3_TREND', -2001)); e.true_books()
    coh = e.state['cohort']
    e.state['cohort'] = None; e.true_books()                                        # transient: conflict / unreadable
    e.state['cohort'] = coh
    e.history.append(row(e, 'B3_TREND', 5000))
    assert e.true_books()['B3_TREND']['state'] == 'HALTED'


@pytest.mark.parametrize('bad', ['abc', 'nan', -5, None])
def test_an_unreadable_persisted_peak_is_flagged(bad):
    b = C('C', [], peaks={'B1_FAST': bad})['B1_FAST']
    assert not b['complete'] and b['incomplete'][0]['reason'] == 'peak_unreadable' and b['peak'] == 2000


# ------------------------------------------------------------------ Cowork 6240046426 (pure-layer probes)
def test_a_stale_mark_feed_values_nothing_and_blocks_that_book():
    e, _ = tb_engine()
    e.state['lots']['x'] = fake_lot(e, 'B3_TREND', sym='ETHUSDT', avg=50.0, stop=49.0, qty=1)
    assert e.true_books()['B3_TREND']['complete']
    e.marks_t = time.time() - E.MARK_FRESH_S - 1                                   # the feed stalled
    b = e.true_books()['B3_TREND']
    assert [x['reason'] for x in b['incomplete']] == ['mark_missing'] and b['unreal'] == 0
    assert TB.admit(b).startswith('book B3_TREND INCOMPLETE (mark_missing)')


@pytest.mark.parametrize('mk', [0, -1, '0'])
def test_a_zero_or_negative_mark_is_no_price(mk):
    b = C('C', [], [dict(book='B1_FAST', cohort_id='C', symbol='X', side='LONG', avg=100, qty=1, stop=90, fees_actual=0)],
                   marks={'X': mk})['B1_FAST']
    assert b['nav'] == 2000 and b['incomplete'][0]['reason'] == 'mark_missing'


def test_an_unrealized_only_halt_must_hold_before_it_is_sticky(monkeypatch):
    e, _ = tb_engine()
    e.state['lots']['x'] = fake_lot(e, 'B3_TREND', sym='ETHUSDT', avg=50.0, stop=1.0, qty=100)
    b = e.true_books({'ETHUSDT': 29.0})['B3_TREND']                                 # -2100 open: NAV < 0 on marks only
    assert b['state'] == 'HALTED' and e.state['true_books']['halted'] == {}       # refused now, not yet sticky
    assert e.true_books({'ETHUSDT': 50.0})['B3_TREND']['state'] == 'ACTIVE'        # a one-tick print: no halt
    e.true_books({'ETHUSDT': 29.0})
    monkeypatch.setattr(E.Engine, 'TB_HALT_CONFIRM_S', 0)
    e.true_books({'ETHUSDT': 29.0})
    assert e.state['true_books']['halted']['B3_TREND']['confirmed_s'] >= 0
    assert e.true_books({'ETHUSDT': 50.0})['B3_TREND']['state'] == 'HALTED'        # sticky once confirmed


def test_compute_dedupes_rows_by_id():
    r = dict(id='a', book='B1_FAST', cohort_id='C', pnl_gross=-100, fees=0, fees_actual=0)
    assert C('C', [r, dict(r)])['B1_FAST']['nav'] == 1900


def test_unowned_activity_blocks_new_risk_in_every_book_but_keeps_lots_managed():
    e, _ = tb_engine()
    e.state['lots']['m'] = fake_lot(e, None, manual=True, cohort_id=None)
    for sl in (T_SL, R_SL):
        assert not e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity())
        assert 'unowned_account_activity' in e.last_skip
    e.state['lots'].pop('m')                                                        # closed / reconciled
    assert e.open_lot(R_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip


# ------------------------------------------------------------------ Codex consolidated correction 6240139762
FUND_T = int(datetime(2026, 10, 17, 14, 30, tzinfo=timezone.utc).timestamp() * 1000)


def reasons(b):
    return {x['reason'] for x in b['incomplete']}


def restart(e, tmp):
    e2, _ = mk_engine(tmp=tmp, CAPITAL_CAP=10000.0, PAPER_BOOKS=True, PAPER_BOOK_CAP=2000.0, TRUE_BOOKS=True)
    e2.S['SLEEVES'] = [T_SL, T2_SL, R_SL]; e2.S['CAP_SINCE'] = e.S['CAP_SINCE']; e2._cohort_init()
    e2.trade.income = e.trade.income; e2.marks_t = time.time()
    return e2


def funded_row(e, **kw):
    return row(e, 'B3_TREND', 10.0, **dict(dict(id='f1', symbol='BTCUSDT', side='LONG', opened='2026-10-17T14:00:00+00:00',
                                                closed='2026-10-17T15:00:00+00:00', qty_max=1.0,
                                                fills=[['2026-10-17T14:00:00+00:00', 'entry', 1.0, 100.0],
                                                       ['2026-10-17T15:00:00+00:00', 'exit', 1.0, 101.0]]), **kw))


def close_row(e, r):
    e.history.append(r); e._cohort_row(r); e._save_aux('history', e.history); e.save_state()


def pay(e, amount=-0.5, tran=1):
    e.trade.income = lambda *a, **k: [dict(tranId=tran, time=FUND_T, income=str(amount), asset='USDT', symbol='BTCUSDT')]
    return e.funding_tick(force=True)


def test_any_incomplete_reason_refuses_new_risk_in_that_book():
    for extra in (dict(fees_actual=None, fees_actual_partial=['fill_records_lost']), dict(pnl_gross='x'),
                  dict(funding_partial=['ambiguous_opposite_side_funding'])):
        out = C('C', [dict(dict(id='a', book='B1_FAST', cohort_id='C', pnl_gross=1, fees=0, fees_actual=0), **extra)])
        assert TB.admit(out['B1_FAST']).startswith('book B1_FAST INCOMPLETE')
        assert not out['B1_FAST']['rankable'] and TB.admit(out['B2_RANGE']) is None and out['B2_RANGE']['rankable']
    assert TB.admit(C('C', [], peaks={'B1_FAST': 'bad'})['B1_FAST']).startswith('book B1_FAST INCOMPLETE (peak_unreadable)')


def test_late_fee_and_funding_survive_the_history_trim_and_restart():
    e, tmp = tb_engine()
    ref = f'c{int(time.time() * 1000)}-1-1'
    close_row(e, funded_row(e, fees=0.9, fees_actual=None, fees_actual_partial=['fill_records_pending'], cost_refs=[ref],
                            fills_booked=1))
    assert 'fees_actual_missing' in reasons(e.true_books()['B3_TREND'])
    e._cost_sink(dict(cost_ref=ref, symbol='BTCUSDT', commission_raw=[dict(asset='USDT', maker=False, amount=0.7, n=1)]))
    assert e.apply_costs() == 1
    assert pay(e) == '1 new'
    b = e.true_books()['B3_TREND']
    assert b['complete'] and b['nav'] == Decimal('2000') + 10 - Decimal('0.7') - Decimal('0.5')
    e.history.clear(); e._save_aux('history', e.history)                             # the 3000-row window dropped it
    e2 = restart(e, tmp)
    b2 = e2.true_books()['B3_TREND']
    assert b2['complete'] and b2['closed'] == 1 and b2['nav'] == b['nav']             # never the stale first close copy


def test_a_late_fee_after_the_trim_lands_on_the_evidence_copy():
    e, tmp = tb_engine()
    ref = f'c{int(time.time() * 1000)}-1-2'
    close_row(e, funded_row(e, fees=0.9, fees_actual=None, fees_actual_partial=['fill_records_pending'], cost_refs=[ref],
                            fills_booked=1))
    e.history.clear(); e._save_aux('history', e.history)
    e._cost_sink(dict(cost_ref=ref, symbol='BTCUSDT', commission_raw=[dict(asset='USDT', maker=True, amount=0.3, n=1)]))
    assert e.apply_costs() == 1 and e.state.get('cost_orphans') is None
    assert e.true_books()['B3_TREND']['nav'] == Decimal('2009.7')
    assert restart(e, tmp).true_books()['B3_TREND']['nav'] == Decimal('2009.7')


@pytest.mark.parametrize('how', ['no_reader', 'read_failed', 'stale', 'never_read'])
def test_funding_reader_missing_failed_or_stale_makes_holders_incomplete(how):
    e, _ = tb_engine()
    e.state['lots']['x'] = fake_lot(e, 'B3_TREND', sym='ETHUSDT')
    if how == 'no_reader': del e.trade.income
    elif how == 'read_failed':
        def boom(*a, **k): raise OSError('down')
        e.trade.income = boom
        assert e.funding_tick(force=True) == 'read_failed'
    elif how == 'stale': e.state['funding_read_ms'] -= (e.FUNDING_POLL_S + e.FUNDING_RETRY_S + 601) * 1000
    else: e.state.pop('funding_read_ms')
    books = e.true_books()
    assert [x['why'] for x in books['B3_TREND']['incomplete'] if x['reason'] == 'funding_unread'] == [how]   # never exact 0
    assert books['B2_RANGE']['complete']                                             # holds nothing: unaffected
    assert not e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()) and 'funding_unread' in e.last_skip
    assert e.open_lot(R_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    if how == 'read_failed':
        e.trade.income = lambda *a, **k: []
        assert e.funding_tick(force=True) == '0 new' and 'funding_unread' not in reasons(e.true_books()['B3_TREND'])


def test_funding_coverage_flags_only_rows_closed_after_the_last_read():
    rows = [dict(id=i, book='B1_FAST', cohort_id='C', pnl_gross=1, fees=0, fees_actual=0, closed=c)
            for i, c in (('a', '2026-10-17T10:00:00+00:00'), ('b', '2026-10-17T16:00:00+00:00'))]
    b = TB.compute('C', rows, funding=dict(ok=False, why='read_failed', through_ms=FUND_T))['B1_FAST']
    assert [x['id'] for x in b['incomplete']] == ['b']
    assert len(TB.compute('C', rows)['B1_FAST']['incomplete']) == 2                  # no reader state at all: fail closed


def test_funding_crash_after_the_row_is_durable_before_the_cursor_converges_once(monkeypatch):
    e, tmp = tb_engine()
    close_row(e, funded_row(e))
    monkeypatch.setattr(e, 'save_state', lambda: True)              # the process dies before state reaches the disk
    assert pay(e) == '1 new' and e._row_by_id('f1')['funding_actual'] == -0.5
    e2 = restart(e, tmp)                                            # disk: old cursor / seen; history row has the payment
    assert not e2.state.get('funding_seen')
    assert pay(e2) == '1 new' and pay(e2) == '0 new'
    assert e2._row_by_id('f1')['funding_actual'] == -0.5 and e2._row_by_id('f1')['funding_rows'] == 1   # once, not twice
    assert e2.true_books()['B3_TREND']['funding'] == Decimal('-0.5')


def test_funding_crash_between_the_evidence_revision_and_the_history_file(monkeypatch):
    e, tmp = tb_engine()
    close_row(e, funded_row(e))
    monkeypatch.setattr(e, '_save_aux', lambda *a: False)           # history never reaches the disk ...
    monkeypatch.setattr(e, 'save_state', lambda: True)              # ... nor state: the process dies
    assert pay(e) == 'persist_failed'
    e2 = restart(e, tmp)                                            # store: revision with the payment; history: stale copy
    assert pay(e2) == '1 new'
    assert e2._row_by_id('f1')['funding_actual'] == -0.5
    b = e2.true_books()['B3_TREND']
    assert b['funding'] == Decimal('-0.5') and b['complete']
    e2.history.clear(); e2._save_aux('history', e2.history)
    assert restart(e2, tmp).true_books()['B3_TREND']['funding'] == Decimal('-0.5')


def test_a_failed_row_save_keeps_the_cursor_and_blocks_until_it_lands(monkeypatch):
    e, tmp = tb_engine()
    close_row(e, funded_row(e))
    cur = e.state.get('funding_cursor_ms')
    monkeypatch.setattr(e, '_save_aux', lambda *a: False)
    assert pay(e) == 'persist_failed' and e.state.get('funding_cursor_ms') == cur and not e.state.get('funding_seen')
    assert e._tb_funding()['why'] == 'persist_failed'                                # open holders: funding_unread
    assert e.true_books()['B3_TREND']['funding'] == Decimal('-0.5')                  # the evidence revision is durable
    monkeypatch.undo()
    assert pay(e) == '1 new' and e._row_by_id('f1')['funding_rows'] == 1           # applied once, saved now
    assert e.state['funding_seen'] and e.true_books()['B3_TREND']['complete']
    e.history.clear()
    assert restart(e, tmp).true_books()['B3_TREND']['funding'] == Decimal('-0.5')   # the history file has it


def test_a_revision_the_disk_refused_blocks_the_book_and_is_retried():
    e, tmp = tb_engine()
    close_row(e, funded_row(e))
    path = e.F['cohort_rows']; e.F['cohort_rows'] = tmp                               # a directory: every append fails
    assert pay(e) == 'persist_failed' and e.state['cohort_rev_pending'] == ['f1']
    assert 'evidence_revision_pending' in reasons(e.true_books()['B3_TREND'])
    e.F['cohort_rows'] = path
    assert 'evidence_revision_pending' not in reasons(e.true_books()['B3_TREND']) and not e.state['cohort_rev_pending']
    assert pay(e) == '1 new' and e.true_books()['B3_TREND']['complete']
    e.history.clear(); e._save_aux('history', e.history)
    assert restart(e, tmp).true_books()['B3_TREND']['funding'] == Decimal('-0.5')


def test_a_new_peak_is_durable_before_it_is_returned():
    e, tmp = tb_engine()
    close_row(e, row(e, 'B3_TREND', 300.0, id='w'))
    e.state.pop('true_books', None)
    assert e.true_books()['B3_TREND']['peak'] == 2300                                 # saved before this returned
    e.history.clear(); e.cohort_rows.clear()
    open(e.F['cohort_rows'], 'w').close(); e._save_aux('history', e.history)          # the realized series is gone too
    b = restart(e, tmp).true_books()['B3_TREND']
    assert b['peak'] == 2300 and b['dd'] == Decimal(300) / Decimal(2300)              # restart never lowers DD


def test_a_status_read_also_saves_a_new_peak():
    e, tmp = tb_engine()
    close_row(e, row(e, 'B3_TREND', 100.0, id='w'))
    e.state.pop('true_books', None)
    e.true_books_status()
    e.history.clear(); open(e.F['cohort_rows'], 'w').close(); e._save_aux('history', e.history)
    assert restart(e, tmp).true_books()['B3_TREND']['peak'] == 2100


def test_a_book_state_that_cannot_be_saved_blocks_every_book(monkeypatch):
    e, _ = tb_engine()
    e.state.pop('true_books', None)
    monkeypatch.setattr(e, 'save_state', lambda: False)
    books = e.true_books()
    assert all('true_book_state_not_durable' in reasons(b) and TB.admit(b) for b in books.values())


def test_leverage_uses_the_current_mark_not_the_entry_average():
    lot = dict(book='B1_FAST', cohort_id='C', symbol='X', side='LONG', avg=100, qty=10, stop=100, fees_actual=0)
    b = C('C', [], [lot], marks={'X': 150})['B1_FAST']
    assert b['notional'] == 1500 and b['nav'] == 2500
    assert TB.admit(b, 1000, 0, max_leverage=1) is None                               # 1500 + 1000 = 1 x NAV 2500
    assert 'leverage cap' in TB.admit(b, Decimal('1000.01'), 0, max_leverage=1)
    s = C('C', [], [dict(lot, side='SHORT', stop=100)], marks={'X': 150})['B1_FAST']  # NAV 1500, exposure 1500
    assert s['notional'] == 1500 and 'leverage cap' in TB.admit(s, Decimal('0.01'), 0, max_leverage=1)


def _halt_lot(e):
    return fake_lot(e, 'B3_TREND', sym='ETHUSDT', avg=50.0, stop=1.0, qty=1)


def test_restarts_cannot_postpone_an_unrealized_only_halt(monkeypatch):
    e, tmp = tb_engine()
    real, off = time.time, [0.0]
    monkeypatch.setattr(E.time, 'time', lambda: real() + off[0])
    close_row(e, row(e, 'B3_TREND', -1990.0, id='big'))                                # NAV 10 realized
    e.state['lots']['x'] = _halt_lot(e); e.save_state()
    marks = {'ETHUSDT': 30.0}                                                          # -20 open: NAV -10 on marks only
    assert e.true_books(marks)['B3_TREND']['state'] == 'HALTED' and e.state['true_books']['halted'] == {}
    t0 = e.state['true_books']['halt_pend']['B3_TREND']
    for step in (10, 20):
        off[0] = step; e = restart(e, tmp); e.state['lots'].setdefault('x', _halt_lot(e))
        e.true_books(marks)
        assert e.state['true_books']['halted'] == {} and e.state['true_books']['halt_pend']['B3_TREND'] == t0
    off[0] = 31; e = restart(e, tmp); e.state['lots'].setdefault('x', _halt_lot(e))
    e.true_books(marks)
    assert e.state['true_books']['halted']['B3_TREND']['confirmed_s'] >= 30


@pytest.mark.parametrize('where', ['resting_entries', 'unconfirmed_entries', 'pending_entries'])
def test_an_unknown_or_manual_working_order_blocks_every_book(where):
    e, _ = tb_engine()
    w = dict(symbol='ETHUSDT', side='LONG', qty=1.0, sleeve='ZZ', plan=dict(qty=1.0, px=100.0, stop_dist=2.0))
    e.state.setdefault(where, {})['w'] = w
    assert all('unowned_account_activity' in reasons(b) for b in e.true_books().values())
    assert TB.admit(e.true_books()['B2_RANGE']).startswith('book B2_RANGE INCOMPLETE (unowned_account_activity)')
    e.state[where]['w'] = dict(w, sleeve='T', manual=True)                             # manual even on a known sleeve
    assert all('unowned_account_activity' in reasons(b) for b in e.true_books().values())
    e.state[where]['w'] = dict(w, sleeve='T')                                          # an owned one is not
    assert 'unowned_account_activity' not in reasons(e.true_books()['B2_RANGE'])


def test_identical_replay_is_idempotent_and_a_cost_revision_is_last_wins():
    r = dict(id='a', book='B1_FAST', cohort_id='C', pnl_gross=-100, fees=1.0, fees_actual=None,
             fees_actual_partial=['fill_records_pending'])
    rev = dict(r, fees_actual=0.6, fees_actual_partial=False, cost_rev=1)
    assert C('C', [r, dict(r)])['B1_FAST']['nav'] == 1899
    b = C('C', [r, rev, dict(r)])['B1_FAST']                       # a stale copy after the revision never wins
    assert b['nav'] == Decimal('1899.4') and b['complete'] and b['closed'] == 1
    assert C('C', [rev, r])['B1_FAST']['nav'] == Decimal('1899.4')
    rev2 = dict(rev, fees_actual=0.65, cost_rev=1)                                   # same revision number: the later copy
    assert C('C', [r, rev, rev2])['B1_FAST']['nav'] == Decimal('1899.35')
    assert 'cost_rev' not in r and r['fees_actual'] is None                          # the caller's rows are never mutated


def test_a_conflicting_duplicate_is_flagged_never_silently_chosen():
    r = dict(id='a', book='B1_FAST', cohort_id='C', pnl_gross=-100, fees=0, fees_actual=0)
    out = C('C', [r, dict(r, pnl_gross=100, cost_rev=5)])
    b = out['B1_FAST']
    assert b['nav'] == 1900 and [x['fields'] for x in b['incomplete'] if x['reason'] == 'row_conflict'] == [['pnl_gross']]
    assert TB.admit(b).startswith('book B1_FAST INCOMPLETE (row_conflict)') and out['B2_RANGE']['complete']
    moved = C('C', [r, dict(r, book='B2_RANGE')])                                    # a disputed owner: every book
    assert all('row_conflict' in reasons(x) for x in moved.values())


def test_a_conflicting_history_copy_flags_the_book_across_restart():
    e, tmp = tb_engine()
    r = row(e, 'B3_TREND', -50.0, id='k')
    close_row(e, r)
    e.history[-1] = dict(r, pnl_gross=50.0); e._save_aux('history', e.history)
    assert 'row_conflict' in reasons(e.true_books()['B3_TREND'])
    assert 'row_conflict' in reasons(restart(e, tmp).true_books()['B3_TREND'])


# ------------------------------------------------------------------ review of r4 (adversarial agent) + mutation survivors
def test_a_stale_history_file_never_swallows_a_later_update(monkeypatch):
    e, tmp = tb_engine()
    ref = f'c{int(time.time() * 1000)}-1-3'
    close_row(e, funded_row(e, fees=0.9, fees_actual=None, fees_actual_partial=['fill_records_pending'], cost_refs=[ref],
                            fills_booked=1))
    e._cost_sink(dict(cost_ref=ref, symbol='BTCUSDT', commission_raw=[dict(asset='USDT', maker=False, amount=0.7, n=1)]))
    monkeypatch.setattr(e, '_save_aux', lambda *a: False)           # revision written, history file never saved: crash
    assert e.apply_costs() == 1
    e2 = restart(e, tmp)
    assert e2._row_by_id('f1')['fees_actual'] == 0.7                 # the live row caught up with the evidence copy
    assert len(e2.cohort_rows) == 1 and e2.cohort_rows[0] is e2._row_by_id('f1')
    assert pay(e2) == '1 new'                                        # a later update lands on the caught-up row
    e2.history.clear(); e2._save_aux('history', e2.history)
    b = restart(e2, tmp).true_books()['B3_TREND']
    assert b['complete'] and b['nav'] == Decimal('2000') + 10 - Decimal('0.7') - Decimal('0.5')


def test_a_revision_is_numbered_past_every_copy():
    e, _ = tb_engine()
    r = funded_row(e)
    close_row(e, r)
    stale = dict(r, cost_rev=0); e.history[-1] = stale                # a copy behind the evidence store
    e.cohort_rows[-1] = dict(r, cost_rev=4)
    e._row_changed(stale)
    assert stale['cost_rev'] == 5


def test_restored_evidence_holds_one_row_per_trade_for_the_ledger():
    e, tmp = tb_engine()
    close_row(e, funded_row(e))
    assert pay(e) == '1 new'
    e.history.clear(); e._save_aux('history', e.history)
    e2 = restart(e, tmp)
    assert [r['id'] for r in e2.cohort_rows] == ['f1'] and e2.cohort_rows[0]['funding_actual'] == -0.5


def test_a_persist_failure_retries_soon_and_lands_the_pending_revision_first():
    e, tmp = tb_engine()
    close_row(e, funded_row(e))
    path = e.F['cohort_rows']; e.F['cohort_rows'] = tmp
    assert pay(e) == 'persist_failed'
    assert e.funding_tick() == 'not_due' and float(e.state['funding_polled']) < time.time() - e.FUNDING_POLL_S + e.FUNDING_RETRY_S + 5
    e.F['cohort_rows'] = path
    assert pay(e) == '1 new' and not e.state['cohort_rev_pending'] and e.state['funding_seen']   # no true_books() between


def test_a_pending_id_of_another_cohort_never_stalls_funding():
    e, _ = tb_engine()
    e.history.append(dict(funded_row(e), id='old', cohort_id='CH-older'))
    e.state['cohort_rev_pending'] = ['old', 'gone']                  # one from a closed cohort, one lost for good
    e.state['funding_err'] = 'persist_failed'
    e.trade.income = lambda *a, **k: []
    assert e.funding_tick(force=True) == '0 new'
    assert e.state['cohort_rev_pending'] == ['gone'] and 'funding_err' not in e.state


def test_an_unvalued_lot_never_raises_the_persisted_peak():
    e, _ = tb_engine()
    e.state['lots']['x'] = fake_lot(e, 'B3_TREND', sym='SOLUSDT')   # no mark
    close_row(e, row(e, 'B3_TREND', 300.0, id='w'))
    e.state.pop('true_books', None)
    assert e.true_books()['B3_TREND']['peak'] == 2300                # shown, flagged INCOMPLETE ...
    assert 'B3_TREND' not in e.state['true_books']['peak']           # ... never kept
    assert e.state['true_books']['peak']['B2_RANGE'] == '2000'


def test_the_halt_confirmation_start_alone_is_saved(monkeypatch):
    e, tmp = tb_engine()
    close_row(e, row(e, 'B3_TREND', -1990.0, id='big'))
    e.state['lots']['x'] = _halt_lot(e); e.save_state()
    e.true_books({'ETHUSDT': 50.0})                                  # peaks saved: nothing new below
    saved = []
    monkeypatch.setattr(e, 'save_state', lambda: saved.append(1) or True)
    e.true_books({'ETHUSDT': 30.0})
    assert saved and 'B3_TREND' in e.state['true_books']['halt_pend']
