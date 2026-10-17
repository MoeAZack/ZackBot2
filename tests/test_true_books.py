"""TRUE-BOOK-01 (PAPER/testnet): five isolated $2,000 books. Each book sizes from its own NAV, holds at most 4 lots, keeps
notional and stop-risk inside its own NAV, halts alone at NAV <= 0, and reports per-strategy attribution. Strategy logic
and risk % are unchanged; only isolation / accounting / aggregate limits."""
import copy, inspect, json
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


def tb_engine(**kw):
    e, tmp = mk_engine(CAPITAL_CAP=10000.0, PAPER_BOOKS=True, PAPER_BOOK_CAP=2000.0, TRUE_BOOKS=True, **kw)
    e.S['CAP_SINCE'] = '2026-10-17T13:19:00+00:00'
    e.S['SLEEVES'] = [T_SL, T2_SL, R_SL]
    e._cohort_init()
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
    b = TB.compute('C', [dict(book='B1_FAST', cohort_id='C', pnl_gross=30, fees=1.0, fees_actual=0.8, funding_actual=-0.2)],
                   [dict(book='B1_FAST', cohort_id='C', symbol='X', side='SHORT', avg=10, qty=2, stop=11, realized=4, fees=0.3,
                         fees_actual=0.25, funding_actual=0.1)], marks={'X': 9})['B1_FAST']
    assert b['nav'] == Decimal('2000') + 30 + 4 + 2 - Decimal('0.8') - Decimal('0.25') - Decimal('0.2') + Decimal('0.1')
    assert b['nav_basis'] == 'actual' and b['complete'] and b['stop_risk'] == 2 and b['notional'] == 20


def test_missing_actual_fee_falls_back_to_estimate_and_is_incomplete():
    b = TB.compute('C', [dict(book='B2_RANGE', cohort_id='C', pnl_gross=10, fees=1.0, fees_actual=None,
                              fees_actual_partial=['fill_records_lost'])])['B2_RANGE']
    assert b['nav'] == 2009 and b['nav_basis'] == 'estimate_fallback' and not b['complete']
    assert b['incomplete'][0]['reason'] == 'fees_actual_missing'


def test_ambiguous_funding_on_a_row_makes_the_book_incomplete():
    b = TB.compute('C', [dict(book='B4_BREAKOUT', cohort_id='C', pnl_gross=1, fees=0, fees_actual=0,
                              funding_partial=['ambiguous_opposite_side_funding'], funding_unattributed=1)])['B4_BREAKOUT']
    assert not b['complete'] and b['incomplete'][0]['reason'] == 'ambiguous_opposite_side_funding'


def test_one_book_loss_never_moves_another_book():
    base = [dict(book=k, cohort_id='C', pnl_gross=5, fees=0, fees_actual=0) for k in PB.IDS]
    a = TB.compute('C', base)
    b = TB.compute('C', base + [dict(book='B1_FAST', cohort_id='C', pnl_gross=-700, fees=0, fees_actual=0)])
    assert b['B1_FAST']['nav'] == a['B1_FAST']['nav'] - 700
    assert all(TB.public({k: a[k]}) == TB.public({k: b[k]}) for k in PB.IDS if k != 'B1_FAST')


def test_halt_on_the_worse_basis_and_sticky():
    rows = [dict(book='B5_DISCOVERY', cohort_id='C', pnl_gross=-1999.5, fees=1.0, fees_actual=0.2)]
    b = TB.compute('C', rows)['B5_DISCOVERY']
    assert b['nav'] > 0 and b['nav_estimate'] < 0 and b['state'] == 'HALTED' and b['halt']['basis'] == 'estimate'
    b2 = TB.compute('C', [], halted={'B5_DISCOVERY': dict(basis='estimate')})['B5_DISCOVERY']
    assert b2['nav'] == 2000 and b2['state'] == 'HALTED'                              # sticky after a recovery
    assert TB.admit(b2) .startswith('book B5_DISCOVERY halted')


def test_peak_and_dd_rederived_from_the_ordered_close_series():
    rows = [dict(book='B3_TREND', cohort_id='C', pnl_gross=g, fees=0, fees_actual=0, closed=t)
            for g, t in ((100, '2026-10-17T10:00'), (-300, '2026-10-17T11:00'))]
    b = TB.compute('C', list(reversed(rows)))['B3_TREND']
    assert b['peak'] == 2100 and b['nav'] == 1800 and b['dd'] == 1 - Decimal(1800) / Decimal(2100)


def test_stop_risk_uses_the_current_stop_floored_at_zero():
    assert TB.stop_risk(dict(side='LONG', avg=100, stop=95, qty=2)) == 10
    assert TB.stop_risk(dict(side='LONG', avg=100, stop=101, qty=2)) == 0           # profit-locked: no risk
    assert TB.stop_risk(dict(side='SHORT', avg=100, stop=104, qty=1)) == 4
    assert TB.stop_risk(dict(side='SHORT', avg=100, qty=1)) is None


def test_other_cohort_lots_hold_capacity_but_never_value():
    b = TB.compute('NEW', [dict(book='B1_FAST', cohort_id='OLD', pnl_gross=500, fees=0, fees_actual=0)],
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
    assert b['working'] == 1 and b['stop_risk'] == Decimal('2.03') and b['notional'] == 130     # 3 x 0.01 + 2.0 reserved
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
    assert b['state'] == 'ACTIVE' and b['nav'] == 2000 and e.state['true_books'] == dict(cohort_id='CH-next', halted={})


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
    b = TB.compute(None, [dict(book='B1_FAST', pnl_gross=-5000, fees=0, fees_actual=0)])['B1_FAST']
    assert b['nav'] == 2000 and b['closed'] == 0


def test_nav_survives_the_history_window_through_the_cohort_store():
    e, _ = tb_engine()
    old = row(e, 'B3_TREND', -300, id='old')
    e.cohort_rows.append(old)                                   # trimmed out of history, kept in the evidence store
    e.cohort_rows.append(row(e, 'B3_TREND', 0, id='late', fees=1.0))
    e.history.append(row(e, 'B3_TREND', 0, id='late', fees=0.5))                   # history carries the late actual fee
    b = e.true_books()['B3_TREND']
    assert b['closed'] == 2 and b['nav'] == Decimal('1699.5')


def test_unmeasurable_risk_blocks_new_risk_in_that_book_only():
    e, _ = tb_engine()
    e.state['lots']['x'] = fake_lot(e, 'B3_TREND', sym='SOLUSDT')                  # no mark for this coin
    assert not e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert 'cannot measure its risk (mark_missing)' in e.last_skip
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
