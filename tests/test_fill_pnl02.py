"""FILL-PNL-02 (#178, pre-staged): measured P&L propagated to every consumer that claims actual performance. Exact
runtime fixtures from #176; the consumer assertions are added with the implementation (after #177 merges)."""
import os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import actual_costs as AC                                                # noqa: E402
import trade_audit as TA                                                 # noqa: E402


def test_176_pepe_short_time_exit_measured_net():
    """Codex 6329983887: 1000PEPEUSDT SC-MBURST short, qty 420,579; measured entry 0.0046053641 (booked basis
    0.0046182), measured exit 0.0046228; fees 1.55246880 -> gross -7.33317339, net -8.88564219 (pre-fix row -2.8681)."""
    q = 420579.0
    t = dict(side='SHORT', fills=[['t0', 'entry', q, 0.0046182], ['t1', 'time_exit', q, 0.0046228]],
             fill_refs=[[0, 'c1-1-0'], [1, 'c1-1-1']])
    AC.apply_px(t, dict(cost_ref='c1-1-0', actual=0.0046053641)); AC.apply_px(t, dict(cost_ref='c1-1-1', actual=0.0046228))
    AC.finalize_px(t, -1, TA.ENTRY_KINDS)
    booked, _ = AC._replay(t['fills'], -1, TA.ENTRY_KINDS, {})
    assert t['fill_px_partial'] is False
    assert booked + t['pnl_fill_adj'] == pytest.approx(-7.33317339, abs=1e-6)
    assert booked + t['pnl_fill_adj'] - 1.55246880 == pytest.approx(-8.88564219, abs=1e-6)


# ---------------------------------------------------------------- #178 consumer contract (pre-staged; Codex ruling 6329976227)
# Skipped until the implementation (branched from merged #177) adds AC.measured / AC.capital_hold.
needs_impl = pytest.mark.skipif(not (hasattr(AC, 'measured') and hasattr(AC, 'capital_hold')), reason='#178 not implemented yet')


def _row(i, gross, fees_act=0.0, adj=None, partial=False, funding=None, closed='2026-10-22T00:00:00'):
    r = dict(id=f'r{i}', closed=closed, pnl_gross=gross, fees=abs(fees_act), fees_actual=fees_act, pnl=gross - fees_act)
    if adj is not None: r.update(fill_refs=[[0, f'c{i}']], pnl_fill_adj=adj, fill_px_partial=partial)
    if funding is not None: r['funding_actual'] = funding
    return r


@needs_impl
def test_measured_matches_true_books_net():
    """net = booked gross + pnl_fill_adj - fees_actual + funding_actual (true_books per-row formula)."""
    m = AC.measured(_row(1, -2.8681, fees_act=1.5524688, adj=-4.46507339, funding=0.25))
    assert m['complete'] is True and m['why'] == []
    assert m['net'] == pytest.approx(-2.8681 - 4.46507339 - 1.5524688 + 0.25, abs=1e-8)


@needs_impl
@pytest.mark.parametrize('bad,why', [(dict(fill_px_partial=['fill_px_pending']), 'fill_px_pending'),
                                     (dict(fees_actual=None), 'fees_actual_missing'),
                                     (dict(funding_partial=['funding_unattributed']), 'funding_unattributed')])
def test_measured_incomplete_is_never_actual(bad, why):
    r = _row(2, 5.0, fees_act=0.5, adj=0.0); r.update(bad)
    m = AC.measured(r)
    assert m['complete'] is False and why in m['why']


@needs_impl
def test_hold_at_checkpoint_until_every_new_row_is_measured():
    """Ruling 1: an incomplete row is never excluded while compounding continues - capital holds at the last fully
    reconciled checkpoint (base if none), exposes realized_unmeasured_n + the hold reason, then revises exactly once."""
    a, b = _row(1, 10.0, 1.0, adj=0.0), _row(2, -4.0, 0.5, adj=-1.0, partial=['fill_px_pending'])
    h = AC.capital_hold([a, b], None)
    assert h['realized'] == 0.0 and h['realized_unmeasured_n'] == 1 and h['hold']          # no checkpoint yet -> base
    c1 = AC.capital_hold([a], None)
    assert c1['realized'] == pytest.approx(9.0) and not c1['hold'] and c1['realized_unmeasured_n'] == 0
    h2 = AC.capital_hold([a, b], c1['checkpoint'])
    assert h2['realized'] == pytest.approx(9.0) and h2['hold'] and h2['checkpoint'] == c1['checkpoint']
    b.update(fill_px_partial=False)                                                     # reconciliation completes
    r1 = AC.capital_hold([a, b], h2['checkpoint'])
    assert r1['realized'] == pytest.approx(9.0 - 5.5) and not r1['hold']
    r2 = AC.capital_hold([a, b], r1['checkpoint'])                                      # idempotent: revised once
    assert r2['realized'] == r1['realized'] and r2['checkpoint'] == r1['checkpoint']


@needs_impl
def test_booked_pnl_never_reaches_capital_when_measured_differs():
    """AIN-shape: booked net looks small, measured net is worse - capital uses the measured value only."""
    r = _row(3, -0.30, 2.832, adj=-0.5877)                      # booked gross -0.30, measured gross -0.8877
    h = AC.capital_hold([r], None)
    assert h['realized'] == pytest.approx(-0.8877 - 2.832, abs=1e-6)


def test_179_xrp_short_partial_tp1_measured_net():
    """Codex 6330860693: XRPUSDT SC-QBANK short, entry 3,507.8 booked at the planned 1.35508 (measured 1.3524780803),
    TP1 closed 2,455.4 measured at 1.3512 -> gross +3.13819837; fees 1.32834984 + 1.32709459 -> net +0.48275394
    (pre-fix runtime booked +12.2278). The 1,052.4 remainder is still open."""
    t = dict(side='SHORT', fills=[['t0', 'entry', 3507.8, 1.35508], ['t1', 'take_profit_1', 2455.4, 1.3501]],
             fill_refs=[[0, 'c1-1-0'], [1, 'c1-1-1']])
    AC.apply_px(t, dict(cost_ref='c1-1-0', actual=1.3524780803)); AC.apply_px(t, dict(cost_ref='c1-1-1', actual=1.3512))
    AC.finalize_px(t, -1, TA.ENTRY_KINDS)
    booked, avg = AC._replay(t['fills'], -1, TA.ENTRY_KINDS, {})
    assert t['fill_px_partial'] is False
    assert booked + t['pnl_fill_adj'] == pytest.approx(3.13819837, abs=1e-6)
    assert booked + t['pnl_fill_adj'] - 1.32834984 - 1.32709459 == pytest.approx(0.48275394, abs=1e-6)
    assert avg + t['avg_fill_adj'] == pytest.approx(1.3524780803, abs=1e-10)       # remainder basis = measured entry


def test_179_xrp_short_full_close_measured_net():
    """Codex 6331145434: the 1,052.4 runner closed measured at 1.3454 -> gross +7.44897171, fees 0.56933916 +
    0.56635958 -> net +6.31327297. Whole trade: net +6.79602691 vs pre-fix booked +25.1441 (3.70x overstated)."""
    t = dict(side='SHORT', fills=[['t0', 'entry', 3507.8, 1.35508], ['t1', 'take_profit_1', 2455.4, 1.3501],
                                  ['t2', 'exit', 1052.4, 1.3440]],
             fill_refs=[[0, 'c1-1-0'], [1, 'c1-1-1'], [2, 'c1-1-2']])
    for r, px in (('c1-1-0', 1.3524780803), ('c1-1-1', 1.3512), ('c1-1-2', 1.3454)): AC.apply_px(t, dict(cost_ref=r, actual=px))
    AC.finalize_px(t, -1, TA.ENTRY_KINDS)
    booked, _ = AC._replay(t['fills'], -1, TA.ENTRY_KINDS, {})
    fees = 1.32834984 + 1.32709459 + 0.56933916 + 0.56635958
    assert t['fill_px_partial'] is False
    assert booked + t['pnl_fill_adj'] == pytest.approx(3.13819837 + 7.44897171, abs=1e-6)
    assert booked + t['pnl_fill_adj'] - fees == pytest.approx(6.79602691, abs=1e-6)


# ---------------------------------------------------------------- #178 engine + consumer wiring
from test_book_ledger import books_engine                                # noqa: E402
from test_safety import SL, SG                                           # noqa: E402
import paper_books as PB                                                 # noqa: E402


def _close_one(e, notes):
    e.notify = notes.append
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    k = next(iter(e.state['lots']))
    e.close_lot(k, 'manual')
    return e.history[-1]


def _measure(e, r, adj=-1.25, fees=0.75):
    r.update(fees_actual=fees, fees_actual_partial=False, fill_px_partial=False, pnl_fill_adj=adj)
    e._row_changed(r)


def test_engine_close_waits_for_measured_net_then_finishes_once():
    """Ruling 1 end to end: an unmeasured close holds capital at the checkpoint, records no R and announces 'pending';
    the measurement revision finishes R / notice exactly once and capital resumes on the measured net."""
    e, _ = books_engine(COMPOUND=True)
    before = e.capital_info()['realized']
    notes = []
    r = _close_one(e, notes)
    assert AC.row_view(r)['status'] == 'pending'
    info = e.capital_info()
    assert info['hold'] == 'realized_unmeasured' and info['realized_unmeasured_n'] == 1 and info['realized'] == before
    assert not (e.state.get('hist') or {}).get(SL['id']) and r['id'] in e.state['measured_wait']
    assert any('pending exchange fill prices' in n for n in notes) and not any('PnL +' in n or 'PnL -' in n for n in notes)
    _measure(e, r)
    m = AC.measured(r)
    assert m['complete'] and r['id'] not in e.state['measured_wait']
    assert e.state['hist'][SL['id']] == [round(m['net'] / r['risk_usd'], 3)]
    assert sum('MEASURED' in n for n in notes) == 1 and f"{m['net']:+.2f}" in notes[-1]
    info = e.capital_info()
    assert info['hold'] is None and info['realized'] == pytest.approx(before + m['net'], abs=0.01)
    _measure(e, r)                                                       # a later restatement never re-records
    assert len(e.state['hist'][SL['id']]) == 1 and sum('MEASURED' in n for n in notes) == 1


def test_legacy_close_keeps_its_booked_value_immediately():
    """A close with no fill links (booked before FILL-PNL-01) has nothing to measure: R and notice at once, no hold."""
    e, _ = books_engine(COMPOUND=True)
    notes = []; e.notify = notes.append
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    k, lot = next(iter(e.state['lots'].items()))
    for f in ('fill_refs', 'px_unmeasured', 'px_unlinked_ok'): lot.pop(f, None)
    e.close_lot(k, 'manual')
    r = e.history[-1]
    assert AC.row_view(r)['status'] == 'booked' and not e.state.get('measured_wait')
    assert len(e.state['hist'][SL['id']]) == 1 and any('(booked)' in n for n in notes)
    assert e.capital_info()['hold'] is None


def test_open_pnl_uses_the_measured_entry_basis():
    e, _ = books_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    l = next(iter(e.state['lots'].values()))
    e.marks = {'BTCUSDT': l['avg'] + 10.0}
    base = e.bot_unrealized()
    l['avg_fill_adj'] = 2.0                                              # measured entry 2.0 worse than booked
    assert e.bot_unrealized() == pytest.approx(base - 2.0 * l['qty'])


def test_paper_books_value_measured_rows_and_count_pending():
    meas = _row(1, 10.0, 1.0, adj=-2.0); meas.update(book='B1_FAST', fees=0.4)
    pend = _row(2, 5.0, 0.5, adj=0.0, partial=['fill_px_pending']); pend['book'] = 'B1_FAST'
    legacy = dict(book='B1_FAST', pnl=3.0, fees=0.2, closed='2026-10-22T00:00:00')
    b = next(x for x in PB.summary(10000.0, 10000.0, history=[meas, pend, legacy]) if x['id'] == 'B1_FAST')
    assert b['closed'] == 3 and b['pending'] == 1
    assert b['net_pnl'] == pytest.approx(10.0 - 2.0 - 1.0 + 3.0) and b['fees'] == pytest.approx(1.0 + 0.2)


def test_telegram_profit_never_shows_a_pending_row_as_actual():
    import telegram_ctl as TG
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    meas = _row(1, 10.0, 1.0, adj=-2.0, closed=now); meas.update(symbol='BTCUSDT', side='LONG')
    pend = _row(2, 50.0, 0.5, adj=0.0, partial=['fill_px_pending'], closed=now); pend.update(symbol='ETHUSDT', side='SHORT')

    class _E:
        import threading
        lock = threading.Lock(); history = [meas, pend]
    out = TG.TelegramControl.c_profit(object.__new__(TG.TelegramControl), _E(), ['7'], None)
    assert '(1 pending measurement)' in out and 'Net P&L: +7.00' in out and 'ETH' not in out


def test_a_tracked_row_without_its_correction_is_never_measured():
    """Cowork N1: a tracked row whose pnl_fill_adj never got written stays pending, never planned prices as actual."""
    r = _row(9, 4.0, 0.5, adj=0.0); r.pop('pnl_fill_adj')
    assert AC.row_view(r)['status'] == 'pending' and 'pnl_fill_adj_missing' in AC.measured(r)['why']


def test_permanent_hold_releases_only_on_operator_ack_with_its_own_net():
    """Codex 6332168655: data that can never be measured holds until an explicit operator acknowledgement; the release
    uses the operator's stated net (AAVE raw truth -2.3612176), never the booked figure (+6.7578)."""
    e, _ = books_engine(COMPOUND=True)
    before = e.capital_info()['realized']
    notes = []
    r = _close_one(e, notes)
    r.update(fees_actual=1.6852176, fees_actual_partial=False, fill_px_partial=['fill_ref_missing'], pnl=6.7578)
    e._row_changed(r)
    assert e.capital_info()['hold'] == 'realized_unmeasured' and r['id'] in e.state['measured_wait']
    assert e.ack_unmeasured(r['id'], 'x')[0] is False and e.ack_unmeasured('nope', 1)[0] is False
    assert e.ack_unmeasured(r['id'], float('nan'))[0] is False
    ok, _ = e.ack_unmeasured(r['id'], -2.3612176, 'exchange fills')
    assert ok and e.ack_unmeasured(r['id'], -2.3612176)[0] is False                 # once
    info = e.capital_info()
    assert info['hold'] is None and info['realized'] == pytest.approx(before - 2.3612176, abs=0.01)
    assert e.state['hist'][SL['id']] == [round(-2.3612176 / r['risk_usd'], 3)] and r['id'] not in e.state['measured_wait']
    assert any('operator-acknowledged' in n and '-2.36' in n for n in notes)
    assert AC.row_view(r)['status'] == 'pending'                                   # displays without the ack stay pending
    m = dict(_row(5, 1.0, 0.1, adj=0.0)); assert e.ack_unmeasured(m['id'], 1.0)[0] is False   # not in history
