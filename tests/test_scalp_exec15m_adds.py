"""#187 (Codex 6372669079): an add / second entry on an open engine lot is one trade with several entry legs - priced
leg by leg, funded on the quantity actually held, reported in provenance, and never dropped, split or double-counted.
Ambiguous lot identity, impossible quantities and an add lot without its authoritative risk fail closed."""
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'tools', 'research'), ROOT]

import costs as C                                         # noqa: E402
import scalp_exec_15m as X                                # noqa: E402

SYM, Q = 'DOGEUSDT', 900_000
BASE = C.STRESS['base']


def f(k, act, qty, ref, ps='LONG', kind='market', sym=SYM, px=None, fee=None):
    d = dict(t_ms=k * Q, sym=sym, ps=ps, act=act, kind=kind, qty=qty, ref=ref)
    if px is not None: d.update(px=px, fee=fee)
    return d


def row(fills, ps='LONG', risk=10.0, sym=SYM):
    """An engine history row: its own booked fills [t, kind, qty, px] (entry kinds per trade_audit)."""
    kinds = {'open': 'entry', 'add': 'pyramid_add', 'close': 'take_profit_1'}
    out, first = [], True
    for x in fills:
        if x['act'] == 'stop_set': continue
        k = 'close' if x['act'] == 'close' else ('open' if first else 'add')
        if x['act'] == 'open': first = False
        out.append([x['t_ms'], kinds[k], x['qty'], x['ref']])
    return dict(symbol=sym, side=ps, risk_usd=risk, exit_reason='tp', pnl_gross=None, fills=out)


def bars(n=40):
    return [X.Bar((k * Q, 100.0, 100.5, 99.5, 100.0)) for k in range(n)]


def priced(tr, scen=BASE, funding_rows=()):
    return X.price_trade(tr, scen, row=C.CRYPTO, slip_c=1e-12, bars15=bars(), funding_rows=list(funding_rows))


def cash(fills, ps='LONG'):
    sd = 1 if ps == 'LONG' else -1
    return sd * math.fsum((1 if x['act'] == 'close' else -1) * x['ref'] * x['qty'] for x in fills if x['act'] != 'stop_set')


# ------------------------------------------------------------------ reconstruction
@pytest.mark.parametrize('ps', ['LONG', 'SHORT'])
def test_one_add_full_close_is_one_trade(ps):
    fl = [f(20, 'open', 2.0, 100.0, ps), f(20, 'stop_set', 0, 95.0, ps), f(22, 'open', 1.0, 102.0, ps),
          f(25, 'close', 3.0, 104.0, ps)]
    (t,) = X.trades_of(fl, [row(fl, ps)])
    assert [x['qty'] for x in t['entries']] == [2.0, 1.0] and t['entry'] is t['entries'][0] and t['stop0'] == 95.0
    assert (t['adds'], t['add_qty'], t['qty_in'], t['risk_usd']) == (1, 1.0, 3.0, 10.0)
    p = priced(t)
    assert abs(p['gross'] - p['gross_ref']) < 0.5                       # each leg priced at its own reference (cost ~0.12)
    assert p['gross_ref'] == pytest.approx(cash(fl, ps)) and p['gross_ref'] == pytest.approx(
        (1 if ps == 'LONG' else -1) * (3 * 104.0 - 2 * 100.0 - 102.0))


@pytest.mark.parametrize('ps', ['LONG', 'SHORT'])
def test_multiple_adds_and_partial_closes(ps):
    fl = [f(20, 'open', 1.0, 100.0, ps), f(21, 'open', 1.0, 101.0, ps), f(22, 'close', 0.5, 103.0, ps),
          f(23, 'open', 2.0, 99.0, ps), f(24, 'close', 1.5, 104.0, ps), f(26, 'close', 2.0, 106.0, ps)]
    (t,) = X.trades_of(fl, [row(fl, ps)])
    assert t['adds'] == 2 and t['add_qty'] == 3.0 and t['qty_in'] == 4.0 and len(t['closes']) == 3
    assert sum(x['qty'] for x in t['entries']) == pytest.approx(sum(c['qty'] for c in t['closes']))   # conservation
    assert priced(t)['gross_ref'] == pytest.approx(cash(fl, ps))


def test_add_after_a_partial_exit_stays_in_the_same_trade():
    fl = [f(20, 'open', 2.0, 100.0), f(21, 'close', 1.0, 105.0), f(22, 'open', 1.0, 103.0), f(24, 'close', 2.0, 110.0)]
    (t,) = X.trades_of(fl, [row(fl)])
    assert t['adds'] == 1 and priced(t)['gross_ref'] == pytest.approx(105 + 220 - 200 - 103)


def test_single_entry_lots_are_unchanged():
    fl = [f(20, 'open', 2.0, 100.0), f(24, 'close', 2.0, 95.0)]
    (t,) = X.trades_of(fl, [dict(symbol=SYM, side='LONG', risk_usd=None, exit_reason='stop', pnl_gross=-10)])
    assert t['adds'] == 0 and t['risk_usd'] is None                    # no fills / risk needed without an add (as v1)
    assert priced(t)['gross_ref'] == pytest.approx(-10.0)


def test_sequential_lots_restart_equivalent_row_order():
    a = [f(20, 'open', 1.0, 100.0), f(21, 'open', 1.0, 101.0), f(22, 'close', 2.0, 103.0)]
    b = [f(23, 'open', 3.0, 100.0), f(25, 'close', 3.0, 98.0)]
    ts = X.trades_of(a + b, [row(a, risk=5.0), row(b, risk=7.0)])          # history in close order: as replayed
    assert [t['risk_usd'] for t in ts] == [5.0, 7.0] and [t['adds'] for t in ts] == [1, 0]
    with pytest.raises(X.ScalpExecError, match='ambiguous'):              # rows out of order: never misattributed
        X.trades_of(a + b, [row(b, risk=7.0), row(a, risk=5.0)])


def test_sub_step_dust_then_reopen_is_two_legal_lots():
    a = [f(20, 'open', 1000.0, 100.0), f(21, 'close', 999.9999995, 101.0)]
    b = [f(22, 'open', 0.1, 100.0), f(23, 'close', 0.1000005, 101.0)]
    ts = X.trades_of(a + b, [row(a), row(b)], {SYM: {'step': 1e-5}})
    assert len(ts) == 2
    assert [sum(x['qty'] for x in t['entries']) for t in ts] == pytest.approx([1000.0, 0.1])
    assert [sum(x['qty'] for x in t['closes']) for t in ts] == pytest.approx([1000.0, 0.1])


def test_carried_dust_does_not_hide_a_larger_over_close():
    a = [f(20, 'open', 1000.0, 100.0), f(21, 'close', 999.9999995, 101.0)]
    b = [f(22, 'open', 0.1, 100.0), f(23, 'close', 0.1000015, 101.0)]
    with pytest.raises(X.ScalpExecError, match='exceed'):
        X.trades_of(a + b, [row(a), row(b)], {SYM: {'step': 1e-5}})


@pytest.mark.parametrize('other_sym,other_side', [(SYM, 'SHORT'), ('XRPUSDT', 'LONG')])
def test_carried_dust_never_crosses_symbol_or_side(other_sym, other_side):
    a = [f(20, 'open', 1000.0, 100.0), f(21, 'close', 999.9999995, 101.0)]
    b = [f(22, 'open', 0.1, 100.0, other_side, sym=other_sym),
         f(23, 'close', 0.1000005, 101.0, other_side, sym=other_sym)]
    rules = {SYM: {'step': 1e-5}, other_sym: {'step': 1e-5}}
    with pytest.raises(X.ScalpExecError, match='exceed'):
        X.trades_of(a + b, [row(a), row(b, ps=other_side, sym=other_sym)], rules)


def test_consumed_dust_cannot_be_reused_by_a_third_lot():
    a = [f(20, 'open', 1000.0, 100.0), f(21, 'close', 999.9999995, 101.0)]
    b = [f(22, 'open', 0.1, 100.0), f(23, 'close', 0.1000005, 101.0)]
    c = [f(24, 'open', 0.1, 100.0), f(25, 'close', 0.1000005, 101.0)]
    with pytest.raises(X.ScalpExecError, match='exceed'):
        X.trades_of(a + b + c, [row(a), row(b), row(c)], {SYM: {'step': 1e-5}})


@pytest.mark.parametrize('close_qty', [0.2, 0.100002])
def test_genuine_over_close_still_refuses_at_and_beyond_the_boundary(close_qty):
    fl = [f(20, 'open', 0.1, 100.0), f(21, 'close', close_qty, 101.0)]
    with pytest.raises(X.ScalpExecError, match='exceed'):
        X.trades_of(fl, [row(fl)], {SYM: {'step': 1e-5}})


def test_one_whole_step_over_close_on_a_big_lot_refuses():
    fl = [f(20, 'open', 1000.0, 100.0), f(21, 'close', 1000.1, 101.0)]
    with pytest.raises(X.ScalpExecError, match='exceed'):
        X.trades_of(fl, [row(fl)], {SYM: {'step': 0.1}})


def test_long_and_short_lots_of_one_symbol_stay_apart():
    lg = [f(20, 'open', 1.0, 100.0), f(21, 'open', 1.0, 100.5), f(24, 'close', 2.0, 101.0)]
    sh = [f(20, 'open', 2.0, 100.0, 'SHORT'), f(22, 'open', 1.0, 100.2, 'SHORT'), f(23, 'close', 3.0, 99.0, 'SHORT')]
    ts = X.trades_of(sorted(lg + sh, key=lambda x: x['t_ms']), [row(sh, 'SHORT'), row(lg)])
    assert sorted((t['ps'], t['adds'], t['qty_in']) for t in ts) == [('LONG', 1, 2.0), ('SHORT', 1, 3.0)]


# ------------------------------------------------------------------ costs aggregate over every leg
def test_fees_and_slippage_are_charged_on_every_entry_leg():
    one = [f(20, 'open', 3.0, 100.0), f(25, 'close', 3.0, 104.0)]
    two = [f(20, 'open', 2.0, 100.0), f(22, 'open', 1.0, 100.0), f(25, 'close', 3.0, 104.0)]
    (t1,), (t2,) = X.trades_of(one, [row(one)]), X.trades_of(two, [row(two)])
    p1, p2 = priced(t1), priced(t2)
    assert p2['fees'] == pytest.approx(p1['fees']) and p2['gross'] == pytest.approx(p1['gross'])   # same notional
    hot = X.SCENARIOS['edge01_x1_5']
    assert priced(t2, hot)['gross'] < priced(t2)['gross']                 # each leg pays its own slippage


def test_funding_follows_the_quantity_actually_held():
    fl = [f(20, 'open', 1.0, 100.0), f(22, 'open', 2.0, 100.0), f(24, 'close', 1.0, 100.0), f(27, 'close', 2.0, 100.0)]
    (t,) = X.trades_of(fl, [row(fl)])
    ev = [(21 * Q, 0.001, 100.0), (22 * Q, 0.001, 100.0), (25 * Q, 0.001, 100.0), (27 * Q, 0.001, 100.0)]
    # held: 1 at T21, 3 at T22 (the add at T counts), 2 at T25, closed at T27 (not charged)
    assert priced(t, funding_rows=ev)['funding'] == pytest.approx(0.001 * 100.0 * (1 + 3 + 2))


def test_account_path_sums_every_leg_price_and_fee():
    fl = [f(20, 'open', 2.0, 100.0, px=100.1, fee=0.1), f(22, 'open', 1.0, 102.0, px=102.2, fee=0.05),
          f(25, 'close', 3.0, 104.0, px=103.9, fee=0.15)]
    (a,) = X.account_trades(fl, [row(fl)], paid=[(23 * Q, SYM, 'LONG', 0.02)])
    assert a['fees'] == pytest.approx(0.3) and a['funding'] == pytest.approx(0.02)
    assert a['net'] == pytest.approx(3 * 103.9 - 2 * 100.1 - 102.2 - 0.3 - 0.02)
    assert (a['qty'], a['adds'], a['add_qty']) == (3.0, 1, 1.0) and a['net_r'] == pytest.approx(a['net'] / 10.0)


def test_add_provenance_counts_trades_legs_and_quantity():
    st = X.add_stats([dict(adds=2, add_qty=3.0), dict(adds=0, add_qty=0.0), dict(adds=1, add_qty=0.5)])
    assert st == dict(scope='closed_trades', trades=3, trades_with_adds=2, add_legs=3, add_qty=3.5, open_lots=0,
                      open_lots_with_adds=0)


# ------------------------------------------------------------------ fail closed
def test_an_over_close_refuses():
    fl = [f(20, 'open', 1.0, 100.0), f(21, 'open', 1.0, 100.0), f(22, 'close', 3.0, 101.0)]
    with pytest.raises(X.ScalpExecError, match='exceed'):
        X.trades_of(fl, [row(fl)])


def test_a_non_positive_quantity_refuses():
    fl = [f(20, 'open', 1.0, 100.0), f(21, 'open', 0.0, 100.0), f(22, 'close', 1.0, 101.0)]
    with pytest.raises(X.ScalpExecError, match='non-positive'):
        X.trades_of(fl, [row(fl)])


def test_a_close_without_an_open_lot_refuses():
    with pytest.raises(X.ScalpExecError, match='without an open lot'):
        X.trades_of([f(21, 'close', 1.0, 101.0)], [])


def test_legs_that_are_not_the_rows_booked_fills_refuse():
    fl = [f(20, 'open', 1.0, 100.0), f(21, 'open', 1.0, 100.0), f(22, 'close', 2.0, 101.0)]
    one_entry = [f(20, 'open', 2.0, 100.0), f(22, 'close', 2.0, 101.0)]   # e.g. two engine lots behind one position
    split_close = [fl[0], fl[1], f(22, 'close', 1.0, 101.0), f(22, 'close', 1.0, 101.0)]   # same entries, other closes
    bigger_close = [fl[0], fl[1], f(22, 'close', 2.5, 101.0)]
    extra_close = fl + [f(23, 'close', 0.5, 101.0)]                       # same legs plus one more close
    other_split = [f(20, 'open', 1.5, 100.0), f(21, 'open', 0.5, 100.0), fl[2]]   # same counts, other entry sizes
    for h in (row(one_entry), row(split_close), row(bigger_close), row(extra_close), row(other_split), dict(row(fl), fills=[]), dict(row(fl), fills=None)):
        with pytest.raises(X.ScalpExecError, match='ambiguous'):
            X.trades_of(fl, [h])


def test_an_add_lot_without_its_risk_refuses():
    fl = [f(20, 'open', 1.0, 100.0), f(21, 'open', 1.0, 100.0), f(22, 'close', 2.0, 101.0)]
    for r in (None, 0, 0.0):
        with pytest.raises(X.ScalpExecError, match='risk_usd'):
            X.trades_of(fl, [row(fl, risk=r)])


def test_history_count_mismatch_still_refuses():
    fl = [f(20, 'open', 1.0, 100.0), f(21, 'open', 1.0, 100.0), f(22, 'close', 2.0, 101.0)]
    with pytest.raises(X.ScalpExecError, match='disagree'):
        X.trades_of(fl, [row(fl), row(fl)])


# ------------------------------------------------------------------ no silent drop or double count
def test_every_fill_is_in_exactly_one_trade_and_the_cash_adds_up():
    lots = [[f(20, 'open', 1.0, 100.0), f(21, 'open', 0.5, 99.0), f(23, 'close', 1.5, 102.0)],
            [f(24, 'open', 2.0, 101.0), f(25, 'close', 1.0, 100.0), f(26, 'open', 1.0, 98.0), f(28, 'close', 2.0, 103.0)],
            [f(29, 'open', 1.0, 104.0), f(30, 'close', 1.0, 103.0)]]
    fl = [x for l in lots for x in l]
    ts = X.trades_of(fl, [row(l) for l in lots])
    legs = [id(x) for t in ts for x in t['entries'] + t['closes']]
    assert len(legs) == len(set(legs)) == len(fl)                         # every fill once, none twice, none dropped
    assert math.fsum(priced(t)['gross_ref'] for t in ts) == pytest.approx(cash(fl))
    assert X.add_stats(ts) == dict(scope='closed_trades', trades=3, trades_with_adds=2, add_legs=2, add_qty=1.5, open_lots=0,
                                   open_lots_with_adds=0)


# ------------------------------------------------------------------ Codex 6373065821: the causal leg order is identity
def test_an_add_and_a_partial_close_in_the_other_order_refuse():
    fl = [f(20, 'open', 2.0, 100.0), f(21, 'close', 1.0, 105.0), f(22, 'open', 1.0, 103.0), f(24, 'close', 2.0, 110.0)]
    swapped = [fl[0], fl[2], fl[1], fl[3]]                              # same per-class quantities, other history
    assert X.trades_of(fl, [row(fl)])[0]['adds'] == 1
    with pytest.raises(X.ScalpExecError, match='ambiguous'):
        X.trades_of(fl, [row(swapped)])


def test_a_completed_lot_missing_from_history_refuses_cleanly():
    fl = [f(20, 'open', 1.0, 100.0), f(21, 'open', 1.0, 100.0), f(22, 'close', 2.0, 101.0)]
    other = [f(20, 'open', 1.0, 100.0, sym='XRPUSDT'), f(22, 'close', 1.0, 101.0, sym='XRPUSDT')]
    for hist in ([], [row(other, sym='XRPUSDT')], [row(fl, 'SHORT')]):
        with pytest.raises(X.ScalpExecError, match='disagree'):
            X.trades_of(fl + (other if hist and hist[0]['symbol'] == 'XRPUSDT' else []), hist)


# ------------------------------------------------------------------ Codex 6373243304: per-entry RISK-FILL-01 truth
def test_single_entry_trim_is_kept_and_counted():
    fl = [f(20, 'open', 2.0, 100.0), f(24, 'close', 2.0, 95.0)]
    h = dict(row(fl), risk_fill={'0': {'action': 'trim', 'planned': 1}})
    (t,) = X.trades_of(fl, [h])
    assert t['risk_fill'] == ['trim'] and t['risk_fill_status'] is None
    assert X.rf_counts([t])['trim'] == 1 and X.rf_basis([t])['entry_legs'] == 1


def test_an_add_lot_keeps_every_leg_action_in_causal_order():
    fl = [f(20, 'open', 2.0, 100.0), f(21, 'close', 1.0, 105.0), f(22, 'open', 1.0, 103.0), f(24, 'close', 2.0, 110.0)]
    h = dict(row(fl), risk_fill={'0': {'action': 'ok'}, '2': {'action': 'unwind'}})     # fill index 2 = the add
    (t,) = X.trades_of(fl, [h])
    assert t['risk_fill'] == ['ok', 'unwind']
    c, b = X.rf_counts([t]), X.rf_basis([t])
    assert (c['ok'], c['unwind'], b['entry_legs'], b['decided'], b['no_decision']) == (1, 1, 2, 2, 0)


def test_an_undecided_leg_and_string_statuses_are_explicit():
    fl = [f(20, 'open', 1.0, 100.0), f(21, 'open', 1.0, 100.0), f(22, 'close', 2.0, 101.0)]
    (t,) = X.trades_of(fl, [dict(row(fl), risk_fill={'0': {'action': 'trim'}})])
    assert t['risk_fill'] == ['trim', None] and X.rf_basis([t])['no_decision'] == 1
    (u,) = X.trades_of(fl, [dict(row(fl), risk_fill='unavailable')])
    assert u['risk_fill'] == [None, None] and X.rf_basis([u])['lot_status'] == {'unavailable': 1}


def test_a_decision_on_a_close_or_unknown_fill_refuses():
    fl = [f(20, 'open', 1.0, 100.0), f(21, 'open', 1.0, 100.0), f(22, 'close', 2.0, 101.0)]
    for rf in ({'0': {'action': 'ok'}, '2': {'action': 'trim'}}, {'7': {'action': 'ok'}}):
        with pytest.raises(X.ScalpExecError, match='risk_fill'):
            X.trades_of(fl, [dict(row(fl), risk_fill=rf)])


def test_account_trades_carry_the_leg_actions():
    fl = [f(20, 'open', 2.0, 100.0, px=100.0, fee=0.1), f(22, 'open', 1.0, 102.0, px=102.0, fee=0.05),
          f(25, 'close', 3.0, 104.0, px=104.0, fee=0.15)]
    (a,) = X.account_trades(fl, [dict(row(fl), risk_fill={'0': {'action': 'trim'}, '1': {'action': 'ok'}})], paid=[])
    assert a['risk_fill'] == ['trim', 'ok'] and X.rf_counts([a])['trim'] == 1 and X.rf_counts([a])['ok'] == 1


def test_repriced_would_is_explicitly_not_applicable_for_an_add_lot():
    fl = [f(20, 'open', 2.0, 100.0), f(20, 'stop_set', 0, 95.0), f(22, 'open', 1.0, 102.0), f(25, 'close', 3.0, 104.0)]
    (t,) = X.trades_of(fl, [row(fl)])
    assert priced(t)['risk_fill_would'] == X.NA_ADD
    one = [f(20, 'open', 2.0, 100.0), f(20, 'stop_set', 0, 95.0), f(25, 'close', 2.0, 104.0)]
    (u,) = X.trades_of(one, [row(one)])
    assert priced(u)['risk_fill_would'] in X.RF.ACTIONS


def test_open_lots_with_adds_are_counted_in_provenance():
    lots = {'a': dict(fills=[[0, 'entry', 1, 1], [1, 'pyramid_add', 1, 1]]), 'b': dict(fills=[[0, 'entry', 1, 1]])}
    st = X.add_stats([], lots)
    assert (st['open_lots'], st['open_lots_with_adds'], st['scope']) == (2, 1, 'closed_trades')
