"""FILL-PNL-01 (#176, PAPER/testnet): realized P&L of partial exits from the MEASURED testnet fill prices. A fill booked at
the price known when it was sent (an answer without avgPrice -> the mark / plan price) is corrected once the enriched fill
record brings the exchange price: pnl_fill_adj / avg_fill_adj beside the booked numbers (never rewritten), recomputed from
the lot's own fills so nothing is double counted; until every linked fill is measured the lot / row is INCOMPLETE."""
import json, os, sys, time
from decimal import Decimal
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety import opened                                           # noqa: E402
from test_exec_cal00_enrich import EX, mk, recs, fast                    # noqa: E402,F401
import actual_costs as AC                                                # noqa: E402
import book_ledger as BL                                                 # noqa: E402
import engine as E                                                       # noqa: E402
import trade_audit as TA                                                 # noqa: E402
import true_books as TB                                                  # noqa: E402

FOK = dict(ok=True, why=None, through_ms=None)


def settle(e):
    recs(e); return e.apply_costs()


def scenario(side, entry_px, tp_mark, tp_px):
    """Open at mark 100 (booked: the answer has no avgPrice) executing at entry_px, then a half take-profit booked at
    tp_mark executing at tp_px - the #176 shape."""
    e, _ = mk(EX); e.trade.split = [(1.0, entry_px)]
    k = opened(e, side=side); lot = e.state['lots'][k]
    assert lot['avg'] == 100.0                                           # booked at the decision price
    e.trade.split = [(1.0, tp_px)]
    q = lot['qty'] / 2
    e._market_close(lot, q, 'take_profit_1', mark=tp_mark, post={'tp1': True})
    return e, k, lot, q


@pytest.mark.parametrize('side,entry_px,tp_mark,tp_px', [
    ('LONG', 100.3, 103.0, 99.9),            # AINUSDT shape: TP1 credited at the level, filled below the entry (adverse)
    ('SHORT', 99.4, 98.0, 98.9),             # MOVRUSDT shape: a smaller real gain than credited
    ('LONG', 99.8, 101.0, 101.4),            # favorable slippage on both fills
])
def test_partial_exit_pnl_is_reconciled_to_the_measured_fills(side, entry_px, tp_mark, tp_px):
    e, k, lot, q = scenario(side, entry_px, tp_mark, tp_px)
    sd = 1 if side == 'LONG' else -1
    booked = sd * (tp_mark - 100.0) * q
    assert lot['realized'] == pytest.approx(booked)                      # what the log / state credited
    assert lot['fill_px_partial'] == ['fill_px_pending']                 # not measured yet: never shown as actual
    settle(e)
    lot = e.state['lots'][k]
    measured = sd * (tp_px - entry_px) * q
    assert lot['fill_px_partial'] is False and 'px_missing' not in lot
    assert lot['realized'] == pytest.approx(booked)                      # the booked number is never rewritten
    assert lot['realized'] + lot['pnl_fill_adj'] == pytest.approx(measured)
    assert lot['avg'] + lot['avg_fill_adj'] == pytest.approx(entry_px)
    bl = dict(lot, book='B1_FAST', cohort_id='C', fees_actual=0.0)    # the books: measured gross, measured avg
    b = TB.compute('C', lots=[bl], marks={'BTCUSDT': 101.0}, funding=FOK)['B1_FAST']
    assert not [x for x in b['incomplete'] if x['reason'] == 'fill_px_unresolved']
    assert float(b['gross_real']) == pytest.approx(measured, abs=1e-4)
    assert float(b['unreal']) == pytest.approx(sd * (101.0 - entry_px) * lot['qty'], abs=1e-4)
    led = BL.compute(dict(id='C'), lots=[bl], marks={'BTCUSDT': 101.0})['books']['B1_FAST']
    assert led['open_booked_net'] == pytest.approx(measured - lot['fees'], abs=1e-4)
    assert led['unrealized'] == pytest.approx(sd * (101.0 - entry_px) * lot['qty'], abs=1e-4)
    assert led['fill_px_unresolved'] == 0


def test_before_the_measured_price_arrives_the_lot_is_incomplete_not_actual():
    e, k, lot, q = scenario('LONG', 100.3, 103.0, 99.9)
    e._cost_final('lot', lot)
    assert lot['fill_px_partial'] == ['fill_px_pending']
    b = TB.compute('C', lots=[dict(lot, book='B1_FAST', cohort_id='C')], marks={'BTCUSDT': 100.0}, funding=FOK)['B1_FAST']
    assert [x['reason'] for x in b['incomplete'] if x['reason'] == 'fill_px_unresolved'] == ['fill_px_unresolved']


def test_the_closed_row_carries_the_measured_correction_and_books_use_it():
    e, k, lot, q = scenario('LONG', 100.3, 103.0, 99.9)
    e.trade.split = [(1.0, 100.1)]
    e.close_lot(k, 'signal', mark=102.0)                                 # the rest: booked 102, measured 100.1
    h = e.history[-1]
    assert h['fill_px_partial']                                          # the exit record is still in flight
    settle(e)
    h = e.history[-1]
    measured = (99.9 - 100.3) * q + (100.1 - 100.3) * q
    assert h['fill_px_partial'] is False and h['pnl_gross'] == pytest.approx((103.0 - 100) * q + (102.0 - 100) * q, abs=1e-4)
    assert h['pnl_gross'] + h['pnl_fill_adj'] == pytest.approx(measured, abs=1e-4)
    assert h['cost_rev'] >= 1                                            # revised through the evidence trail
    r = dict(h, book='B1_FAST', cohort_id='C', fees_actual=h['fees'])
    b = TB.compute('C', history=[r], funding=FOK)['B1_FAST']
    assert b['gross_real'] == pytest.approx(Decimal(str(measured)), abs=Decimal('0.0001'))
    assert not [x for x in b['incomplete'] if x['reason'] == 'fill_px_unresolved']
    assert 'fill_px' not in str(TB.cost_gaps([r]))
    led = BL.compute(dict(id='C'), history=[r], marks={})['books']['B1_FAST']
    assert led['realized_gross'] == pytest.approx(measured, abs=1e-4) and led['fill_px_unresolved'] == 0
    pend = dict(r, fill_px_partial=['fill_px_pending'])                  # an unmeasured close is a named cost gap
    assert 'fill_px_pending' in str(TB.cost_gaps([pend]))
    assert BL.compute(dict(id='C'), history=[pend], marks={})['books']['B1_FAST']['fill_px_unresolved'] == 1


def test_a_record_applied_twice_or_replayed_never_double_counts():
    e, k, lot, q = scenario('SHORT', 99.4, 98.0, 98.9)
    settle(e)
    adj, avg = lot['pnl_fill_adj'], lot['avg_fill_adj']
    rs = [x for x in recs(e) if x.get('cost_ref')]
    for r in rs:                                                         # the same records again (a replayed queue)
        e._cost_sink(r)
    e.apply_costs()
    e._cost_final('lot', lot)
    assert (lot['pnl_fill_adj'], lot['avg_fill_adj']) == (adj, avg)
    assert len(lot['px_actual']) == 2
    e._cost_sink(dict(cost_ref=lot['fill_refs'][-1][1], symbol='BTCUSDT', commission_missing='duplicate'))
    e.apply_costs()                                                      # a later duplicate hand-off without a price
    assert lot['fill_px_partial'] is False and 'px_missing' not in lot  # never undoes the measured price


def test_a_fill_without_an_exchange_price_stays_explicitly_incomplete():
    e, k, lot, q = scenario('LONG', 100.3, 103.0, 99.9)
    ref = lot['fill_refs'][-1][1]
    e._cost_q.clear()
    e._cost_sink(dict(cost_ref=ref, symbol='BTCUSDT', commission_missing='timeout'))   # no `actual`
    e.apply_costs()
    assert 'fill_px_missing' in lot['fill_px_partial'] and ref in lot['px_missing']
    b = TB.compute('C', lots=[dict(lot, book='B1_FAST', cohort_id='C')], marks={'BTCUSDT': 100.0}, funding=FOK)['B1_FAST']
    assert any(x['reason'] == 'fill_px_unresolved' for x in b['incomplete'])


def test_a_measured_answer_books_the_exchange_price_directly():
    """An answer that carries cumQuote (no avgPrice) is an exchange average: booked as such, no correction needed."""
    e, _ = mk(EX); k = opened(e); lot = e.state['lots'][k]
    orig = e.trade._exec

    def with_quote(name, s, ps, qq):
        o, ex = orig(name, s, ps, qq)
        o['cumQuote'] = str(round(ex * 101.7, 8)); return o, ex
    e.trade._exec = with_quote
    e.trade.split = [(1.0, 101.7)]
    e._market_close(lot, lot['qty'] / 2, 'take_profit_1', mark=103.0)
    assert lot['fills'][-1][3] == pytest.approx(101.7)


def test_legacy_lots_without_linked_fills_are_untouched():
    lot = dict(symbol='X', side='LONG', avg=10.0, qty=1.0, realized=2.0, fills=[['t', 'entry', 2.0, 10.0], ['t', 'tp', 1.0, 12.0]])
    before = dict(lot)
    AC.finalize_px(lot, 1, TA.ENTRY_KINDS)
    assert lot == before


def test_replay_matches_the_engine_booking_with_adds_and_partials():
    """The average-cost replay of the booked prices equals the engine's own realized / avg (the correction is a pure
    price difference)."""
    fills = [['t', 'entry', 2.0, 10.0], ['t', 'take_profit_1', 1.0, 12.0], ['t', 'pyramid_add', 1.0, 11.0],
             ['t', 'stop', 2.0, 9.0]]
    real, avg = AC._replay(fills, 1, TA.ENTRY_KINDS, {})
    assert real == pytest.approx(2.0 + 2 * (9.0 - 10.5)) and avg == pytest.approx(10.5)
    lot = dict(side='LONG', fills=fills, fill_refs=[[0, 'c1-1-1'], [1, 'c1-1-2'], [2, 'c1-1-3'], [3, 'c1-1-4']],
               px_actual={'c1-1-1': 10.2, 'c1-1-2': 11.9, 'c1-1-3': 11.0, 'c1-1-4': 9.0})
    AC.finalize_px(lot, 1, TA.ENTRY_KINDS)
    want = (11.9 - 10.2) * 1 + 2 * (9.0 - (10.2 + 11.0) / 2)
    assert lot['pnl_fill_adj'] == pytest.approx(want - real) and lot['fill_px_partial'] is False
    assert lot['avg_fill_adj'] == pytest.approx((10.2 + 11.0) / 2 - 10.5)


def test_no_exchange_ids_are_stored_on_the_lot():
    e, k, lot, q = scenario('LONG', 100.3, 103.0, 99.9)
    settle(e)
    s = repr({x: lot[x] for x in ('fill_refs', 'px_actual', 'px_missing') if x in lot})
    assert str(e.trade.oid) not in s and 'zbcid' not in s


def test_a_restart_that_dropped_the_queue_recovers_the_price_from_the_fill_record(monkeypatch):
    e, k, lot, q = scenario('LONG', 100.3, 103.0, 99.9)
    recs(e)                                                              # records written to fills.jsonl ...
    e._cost_q.clear()                                                    # ... but the in-memory queue died with the process
    e.save_state()
    saved = json.load(open(os.path.join(e.dir, 'state.json')))['lots'][k]
    assert saved['fill_refs'] == lot['fill_refs']                        # the links are durable
    e.apply_costs()
    assert lot['fill_px_partial'] == ['fill_px_pending']                 # too young to be called lost: no scan yet
    monkeypatch.setattr(E.Engine, 'PX_RECOVER_AGE_S', 0)
    e._cost_swept = 0; e.apply_costs()
    assert lot['fill_px_partial'] is False
    assert lot['realized'] + lot['pnl_fill_adj'] == pytest.approx((99.9 - 100.3) * q)
    e._cost_swept = 0; e.apply_costs()                                   # read once per ref: nothing changes again
    assert lot['realized'] + lot['pnl_fill_adj'] == pytest.approx((99.9 - 100.3) * q)


@pytest.mark.parametrize('px,unmeasured', [(None, True), (99.9, False)])
def test_a_lost_answer_settled_without_an_exchange_price_is_incomplete(px, unmeasured):
    e, _ = mk(EX); e.trade.split = [(1.0, 100.0)]
    k = opened(e); settle(e); lot = e.state['lots'][k]
    q = lot['qty'] / 2
    lot['pending'] = dict(kind='close', qty=q, px=103.0, why='take_profit_1', post={}, t=time.time(), cid='zbx')
    e._order_result = lambda sym, cid, qty: dict(state='final', qty=q, px=px)
    assert e._resolve_pending(k, 0.0, lot['qty'], 1e-9) is True
    assert lot['fills'][-1][3] == (103.0 if px is None else 99.9)
    assert ('fill_px_unmeasured' in (lot['fill_px_partial'] or ())) is unmeasured
    b = TB.compute('C', lots=[dict(lot, book='B1_FAST', cohort_id='C', fees_actual=0.0)], marks={'BTCUSDT': 100.0},
                   funding=FOK)['B1_FAST']
    assert any(x['reason'] == 'fill_px_unresolved' for x in b['incomplete']) is unmeasured
    assert TB.cost_pending_only(b) is False or not unmeasured           # unmeasured is a hole, never latency


def test_an_adopted_or_resynced_fill_is_flagged_unmeasured():
    lot = dict(side='LONG', fills=[['t', 'entry', 1.0, 10.0]])
    AC.px_unmeasured(lot); AC.finalize_px(lot, 1, TA.ENTRY_KINDS)
    assert lot['fill_px_partial'] == ['fill_px_unmeasured'] and lot['pnl_fill_adj'] == 0.0


def test_the_176_ainusdt_full_close_flips_from_a_booked_win_to_a_measured_loss():
    """Codex 6328575176 (02:06 Cairo): entry booked 0.04145 / measured 0.04148; TP1 47,798 booked 0.04170 / measured
    0.04147; final stop 20,486 at 0.04146 (exchange truth); complete actual fees 2.83197647. Booked gross +12.1544 /
    net +9.3179; measured gross -0.8877, net -3.7197 - a win reclassified as a loss."""
    fills = [['t0', 'entry', 68284.0, 0.04145], ['t1', 'take_profit_1', 47798.0, 0.04170], ['t2', 'stop', 20486.0, 0.04146]]
    real, _avg = AC._replay(fills, 1, TA.ENTRY_KINDS, {})
    assert real == pytest.approx(12.15436)                               # the booked gross the cohort row reports
    row = dict(id='ain', book='B1_FAST', cohort_id='C', strategy_key='sc_qbank', symbol='AINUSDT', side='LONG',
               closed='2026-10-21T23:06:00+00:00', pnl_gross=round(real, 4), fees=2.8365, pnl=9.3179, r=0.44,
               fees_actual=2.83197647, fees_actual_partial=False, exit_reason='stop', hours=1.0, fills=fills,
               fill_refs=[[0, 'c1-1-1'], [1, 'c1-1-2'], [2, 'c1-1-3']])
    for ref, px in (('c1-1-1', 0.04148), ('c1-1-2', 0.04147), ('c1-1-3', 0.04146)):
        assert AC.apply_px(row, dict(cost_ref=ref, actual=px))
    AC.finalize_px(row, 1, TA.ENTRY_KINDS)
    assert row['fill_px_partial'] is False
    assert row['pnl_gross'] + row['pnl_fill_adj'] == pytest.approx(-0.8877, abs=1e-4)      # row gross is 4-dp rounded
    assert row['pnl_gross'] + row['pnl_fill_adj'] - row['fees_actual'] == pytest.approx(-3.7197, abs=1e-4)
    b = TB.compute('C', history=[row], funding=FOK)['B1_FAST']
    s = b['strategies']['sc_qbank']
    assert float(s['net']) == pytest.approx(-3.7197, abs=1e-4) and s['wins'] == 0           # a loss, not a win
    assert float(b['gross_real']) == pytest.approx(-0.8877, abs=1e-4)
    led = BL.compute(dict(id='C'), history=[row], marks={})['books']['B1_FAST']
    assert led['realized_net'] == pytest.approx(9.3179 - 12.15436 - 0.8877, abs=1e-4)      # estimate fees, measured gross
    fam = next(x for x in TB.compare({'B1_FAST': b, **{k: v for k, v in TB.compute('C', funding=FOK).items()
                                                       if k != 'B1_FAST'}}, rows=[row])['families'] if x['id'] == 'B1_FAST')
    assert float(fam['net']) == pytest.approx(-3.7197, abs=2e-4)                           # the Strategies tab too


@pytest.mark.parametrize('name,side,fills,meas,fees_actual,gross,net', [
    # Codex 6328899763, MOVUSDT SC-MBURST time exit: entry and exit both measured 1.226 -> gross 0, net = -fees (booked
    # +0.5092 net; the booked prices / qty here are illustrative, the measured result is exact)
    ('mburst_time_exit', 'LONG', [['t0', 'entry', 1000.0, 1.2250], ['t1', 'time_exit', 1000.0, 1.2275]],
     [1.226, 1.226], 1.66577846, 0.0, -1.66577846),
    # MOVUSDT SC-QBANK: TP1 1,425.65 measured 1.226 -> 1.220 (+8.5539), remainder measured flat at 1.226; fees 2.49265595
    # -> net +6.0612 (booked +13.0602 gross: TP1 at 1.2183 vs avg 1.2270226, remainder booked 1.2265843; remainder qty
    # illustrative)
    ('qbank_full_close', 'SHORT', [['t0', 'entry', 2851.3, 1.2270226], ['t1', 'take_profit_1', 1425.65, 1.2183],
                                   ['t2', 'stop', 1425.65, 1.2265843]],
     [1.226, 1.220, 1.226], 2.49265595, 8.5539, 6.06124405),
])
def test_176_full_close_shapes_are_measured(name, side, fills, meas, fees_actual, gross, net):
    sd = 1 if side == 'LONG' else -1
    t = dict(side=side, fills=fills, fill_refs=[[i, f'c1-1-{i}'] for i in range(len(fills))])
    for i, px in enumerate(meas):
        AC.apply_px(t, dict(cost_ref=f'c1-1-{i}', actual=px))
    AC.finalize_px(t, sd, TA.ENTRY_KINDS)
    booked, _ = AC._replay(fills, sd, TA.ENTRY_KINDS, {})
    assert t['fill_px_partial'] is False
    assert booked + t['pnl_fill_adj'] == pytest.approx(gross, abs=1e-6)
    assert booked + t['pnl_fill_adj'] - fees_actual == pytest.approx(net, abs=1e-6)
    if name == 'qbank_full_close':
        assert booked == pytest.approx(13.0602, abs=1e-4)                # the pre-fix cohort gross


def test_176_open_short_basis_is_corrected_from_its_first_fill():
    """Codex 6328899763: 1000PEPEUSDT SC-MBURST short, lot basis 0.0046182, measured entry 0.0046053641."""
    lot = dict(side='SHORT', avg=0.0046182, qty=10000.0, realized=0.0, fills=[['t0', 'entry', 10000.0, 0.0046182]],
               fill_refs=[[0, 'c1-1-0']])
    AC.apply_px(lot, dict(cost_ref='c1-1-0', actual=0.0046053641)); AC.finalize_px(lot, -1, TA.ENTRY_KINDS)
    assert lot['avg'] + lot['avg_fill_adj'] == pytest.approx(0.0046053641, rel=1e-9) and lot['pnl_fill_adj'] == 0.0
    bl = dict(lot, book='B1_FAST', cohort_id='C', symbol='X', fees_actual=0.0, stop=0.0047)
    want = -(0.0046 - 0.0046053641) * 10000.0                            # unrealized on the MEASURED basis
    assert float(TB.compute('C', lots=[bl], marks={'X': 0.0046}, funding=FOK)['B1_FAST']['unreal']) == pytest.approx(want, abs=1e-4)
    assert BL.compute(dict(id='C'), lots=[bl], marks={'X': 0.0046})['books']['B1_FAST']['unrealized'] == pytest.approx(want, abs=1e-4)


def test_176_sui_multi_fill_entry_uses_the_weighted_measured_price():
    """Codex 6329208598: SUIUSDT SC-RANGE entry over 16 executions, weighted 1.0386313735 (booked 1.03408812), stop
    measured 1.0275; fees 2.68059878 -> gross -36.10460995, net -38.78520873 (pre-fix cohort -24.7119)."""
    q = 36.10460995 / (1.0386313735 - 1.0275)
    t = dict(side='LONG', fills=[['t0', 'entry', q, 1.03408812], ['t1', 'stop', q, 1.0275]],
             fill_refs=[[0, 'c1-1-0'], [1, 'c1-1-1']])
    AC.apply_px(t, dict(cost_ref='c1-1-0', actual=1.0386313735)); AC.apply_px(t, dict(cost_ref='c1-1-1', actual=1.0275))
    AC.finalize_px(t, 1, TA.ENTRY_KINDS)
    booked, _ = AC._replay(t['fills'], 1, TA.ENTRY_KINDS, {})
    assert booked + t['pnl_fill_adj'] == pytest.approx(-36.10460995, abs=1e-6)
    assert booked + t['pnl_fill_adj'] - 2.68059878 == pytest.approx(-38.78520873, abs=1e-6)


def test_an_entry_over_many_executions_is_corrected_to_their_weighted_price():
    """The engine path: one entry order filled in 16 executions at different prices (answer without avgPrice); the
    enriched record carries the qty-weighted price and the lot basis follows it."""
    e, _ = mk(EX)
    e.trade.split = [(1 / 16, 100.0 + 0.1 * i) for i in range(16)]
    k = opened(e); lot = e.state['lots'][k]
    settle(e)
    r = next(x for x in recs(e) if x['kind'] == 'entry_market')
    rows = e.trade.rows[max(e.trade.rows)]
    vwap = sum(float(x['qty']) * float(x['price']) for x in rows) / sum(float(x['qty']) for x in rows)
    assert r['actual'] == pytest.approx(vwap) and lot['avg'] == 100.0
    assert lot['fill_px_partial'] is False and lot['avg'] + lot['avg_fill_adj'] == pytest.approx(vwap)


def test_an_unaccounted_fill_is_never_assumed_exchange_priced():
    """Cowork H1: a tracked lot whose fill is neither linked, nor flagged unmeasured, nor counted as measured without a
    record says fill_ref_missing (a future call site that forgets both cannot be silent); legacy lots stay exempt."""
    t = dict(side='LONG', fills=[['t0', 'entry', 1.0, 10.0], ['t1', 'take_profit_1', 1.0, 12.0]], fill_refs=[[0, 'c1-1-0']])
    AC.apply_px(t, dict(cost_ref='c1-1-0', actual=11.0)); AC.finalize_px(t, 1, TA.ENTRY_KINDS)
    assert t['fill_px_partial'] == ['fill_ref_missing'] and t['pnl_fill_adj'] == pytest.approx(-1.0)
    AC.px_unlinked_ok(t); AC.finalize_px(t, 1, TA.ENTRY_KINDS)            # the exit was the order record's own price
    assert t['fill_px_partial'] is False
    legacy = dict(side='LONG', fills=[['t0', 'entry', 1.0, 10.0], ['t1', 'tp', 1.0, 12.0]])
    AC.finalize_px(legacy, 1, TA.ENTRY_KINDS)
    assert 'fill_px_partial' not in legacy
