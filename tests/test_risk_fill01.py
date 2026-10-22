"""RISK-FILL-01 (#179): post-fill risk budget from the measured entry. Codex ruling 6340824820; exact PAPER fixtures
(XRP 6330701070 severe, AAVE 6331284896 moderate); Cowork A-O matrix 6330791420 / oracle 6341541170.
Basis (both sides): qty x (fill-to-stop distance + actual entry fee per unit + FEE_EST x stop)."""
import os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import risk_fill as RF                                                     # noqa: E402

XRP = dict(side='SHORT', qty=3507.8, stop=1.3608, planned_px=1.35507997, measured_px=1.3524780803, entry_fees=1.897689)


def test_xrp_full_basis_numbers_and_trim():
    """Price-to-stop 20.07 -> 29.19 (+45%); on the ruling's full basis 24.35 -> 33.48 (+37.5%): trim, kept risk <= planned."""
    assert RF.price_risk('SHORT', XRP['qty'], XRP['planned_px'], XRP['stop']) == pytest.approx(20.07, abs=0.01)
    assert RF.price_risk('SHORT', XRP['qty'], XRP['measured_px'], XRP['stop']) == pytest.approx(29.19, abs=0.01)
    d = RF.decide(**XRP, step=0.1, min_qty=0.1, min_notional=5.0)
    assert d['planned'] == pytest.approx(24.35, abs=0.01) and d['measured'] == pytest.approx(33.48, abs=0.01)
    assert d['action'] == 'trim' and d['fee_basis'] == 'actual'
    assert d['keep_qty'] * d['unit'] <= d['planned'] + 1e-9 and d['keep_qty'] + d['trim_qty'] == pytest.approx(XRP['qty'])
    assert d['keep_qty'] == pytest.approx(round(d['keep_qty'] / 0.1) * 0.1)          # on the step


def test_aave_moderate_overshoot_trims():
    """AAVE short: 20.68 -> 23.64 price-to-stop (+14.3%); above 10% on the full basis too -> trim."""
    d = RF.decide('SHORT', 14.0, 150.4417142857, 152.13, 150.6526992, entry_fees=0.8424736, step=0.1, min_qty=0.1, min_notional=5)
    assert RF.price_risk('SHORT', 14.0, 150.6526992, 152.13) == pytest.approx(20.68, abs=0.01)
    assert d['ratio'] > 1.10 and d['action'] == 'trim' and d['keep_qty'] * d['unit'] <= d['planned'] + 1e-9


def test_tolerance_boundary_equal_is_allowed():
    """measured == 1.10 x planned is allowed; just above trims; just below is ok (Codex ruling 1)."""
    base = dict(side='LONG', qty=100.0, stop=90.0, planned_px=100.0, entry_fees=0.0, fee_rate=0.0, step=1.0)
    assert RF.decide(measured_px=101.0, **base)['action'] == 'ok'                  # exactly 11/10 = 1.10
    assert RF.decide(measured_px=100.99, **base)['action'] == 'ok'
    assert RF.decide(measured_px=101.2, **base)['action'] == 'trim'


@pytest.mark.parametrize('px', [100.0, 99.5])
def test_favorable_or_flat_is_record_only(px):
    d = RF.decide('LONG', 10.0, px, 98.0, 100.0, entry_fees=0.5)
    assert d['action'] == 'ok' and d['trim_qty'] == 0 and d['keep_qty'] == 10.0


@pytest.mark.parametrize('side,px,stop', [('LONG', 97.5, 98.0), ('LONG', 98.0, 98.0), ('SHORT', 102.0, 101.0)])
def test_fill_at_or_through_the_stop_unwinds(side, px, stop):
    d = RF.decide(side, 10.0, px, stop, 100.0, entry_fees=0.0)
    assert d['action'] == 'unwind' and d['why'] == 'through_stop' and d['trim_qty'] == 10.0


def test_ceiling_unwinds_at_exactly_two_x():
    base = dict(side='LONG', qty=10.0, stop=90.0, planned_px=100.0, entry_fees=0.0, fee_rate=0.0)
    assert RF.decide(measured_px=110.0, **base)['action'] == 'unwind'             # 20 vs 10 = 2.0x
    assert RF.decide(measured_px=109.9, **base)['action'] == 'trim'


def test_remainder_below_exchange_minimum_unwinds_and_tiny_trim_is_below_step():
    d = RF.decide('LONG', 10.0, 101.5, 90.0, 100.0, entry_fees=0.0, fee_rate=0.0, step=1.0, min_notional=1000.0)
    assert d['action'] == 'unwind' and d['why'] == 'remainder_below_min'
    d = RF.decide('LONG', 2.5, 101.2, 90.0, 100.0, entry_fees=0.0, fee_rate=0.0, step=1.0)   # trim 0.5 < step 1
    assert d['action'] == 'below_step' and d['trim_qty'] == 0


def test_unmeasured_and_impossible_plan():
    assert RF.decide('LONG', 10.0, None, 98.0, 100.0)['action'] == 'unmeasured'
    d = RF.decide('LONG', 10.0, 99.0, 98.0, 97.0, entry_fees=0.0)                    # plan already through its stop
    assert d['action'] == 'unwind' and d['why'] == 'impossible_plan'


def test_unknown_entry_fee_uses_the_same_estimate_on_both_sides():
    d = RF.decide('LONG', 10.0, 100.0, 98.0, 100.0, entry_fees=None)
    assert d['fee_basis'] == 'estimate' and d['measured'] == pytest.approx(d['planned']) and d['action'] == 'ok'


# ---------------------------------------------------------------- engine: decide on measurement, act in manage()
from test_safety import mk_engine, SL, SG, FakeX                         # noqa: E402
import actual_costs as AC                                                 # noqa: E402


class CidX(FakeX):
    """FakeX with client ids and order records (the real client's close(cid=) / get_order)."""
    def close(self, s, ps, q, cid=None):
        self.cids.append(cid)
        if self.crash: raise self.crash
        o = super().close(s, ps, q); o.update(executedQty=q, status='FILLED')
        if cid: self.orders[cid] = dict(o, clientOrderId=cid)
        return o

    def get_order(self, s, cid): return self.orders.get(cid)


def eng():
    e, _ = mk_engine()
    e.trade.__class__ = CidX
    e.trade.orders, e.trade.cids, e.trade.crash = {}, [], None
    return e


def opened(e):
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    return next(iter(e.state['lots'].items()))


def measure(e, lot, px, fees=0.05, i=0, expected=None):
    ref = next(r for j, r in lot['fill_refs'] if j == i)
    rec = dict(cost_ref=ref, actual=px, expected=expected, commission_raw=[dict(asset='USDT', amount=fees, maker=False)])
    AC.apply_px(lot, rec); e._cost_final('lot', lot); e._risk_fill_mark(lot, rec)
    return lot['risk_fill'][str(i)]


def adverse(lot, frac):
    return lot['fills'][0][3] + frac * abs(lot['fills'][0][3] - lot['stop'])     # LONG: a higher fill is worse


def test_adverse_fill_trims_once_with_write_ahead_id_and_resizes_after():
    e = eng(); k, lot = opened(e)
    q0 = lot['qty']
    d = measure(e, lot, adverse(lot, 0.5))
    assert d['action'] == 'trim' and d['state'] == 'due' and 0 < d['trim_qty'] < q0
    assert e._add_block(lot, 1.0, 100.0) == 'risk_trim in progress'
    e.manage(e.marks)
    assert d['state'] == 'done' and lot['qty'] == pytest.approx(q0 - d['trim_qty']) and d['trimmed'] == pytest.approx(d['trim_qty'])
    assert e.trade.cids[-1].startswith('zbrf') and d['cid'] == e.trade.cids[-1] and d['residual'] <= d['planned'] + 1e-6
    assert lot['fills'][-1][1] == 'risk_trim'
    n = len(e.trade.cids); e.manage(e.marks); assert len(e.trade.cids) == n          # exactly once


def test_crash_after_write_ahead_settles_from_the_order_record_exactly_once():
    e = eng(); k, lot = opened(e)
    d = measure(e, lot, adverse(lot, 0.5)); q0 = lot['qty']
    e.trade.crash = KeyboardInterrupt()                                  # the process dies inside the send
    with pytest.raises(KeyboardInterrupt): e._risk_fill_run(k, lot, e.marks['BTCUSDT'])
    assert d['state'] == 'sent' and d['qty_before'] == q0                # written ahead
    e.trade.crash = None
    e.trade.orders[d['cid']] = dict(status='FILLED', executedQty=str(d['send_qty']), avgPrice=str(e.marks['BTCUSDT']))
    e.trade.pos[('BTCUSDT', 'LONG')] -= d['send_qty']                    # it did execute on the exchange
    n = len(e.trade.cids)
    e._risk_fill_run(k, lot, e.marks['BTCUSDT'])
    assert d['state'] == 'done' and len(e.trade.cids) == n and lot['qty'] == pytest.approx(q0 - d['send_qty'])


def test_sent_but_already_booked_is_never_resent():
    e = eng(); k, lot = opened(e)
    d = measure(e, lot, adverse(lot, 0.5))
    d.update(state='sent', cid='zbrfdead', send_qty=d['trim_qty'], qty_before=lot['qty'], attempts=1)
    e._apply_close(lot, d['trim_qty'], e.marks['BTCUSDT'], 'resync')      # a resync / pending resolve booked it
    n = len(e.trade.cids)
    e._risk_fill_run(k, lot, e.marks['BTCUSDT'])
    assert d['state'] == 'done' and len(e.trade.cids) == n and d['trimmed'] == pytest.approx(d['trim_qty'])


def test_never_reached_exchange_resends_once_then_residual_alert_no_loop():
    import engine as E
    e = eng(); k, lot = opened(e)
    d = measure(e, lot, adverse(lot, 0.5))
    d.update(state='sent', cid='zbrfgone', send_qty=d['trim_qty'], qty_before=lot['qty'], attempts=1)

    def unfilled(*a, **kw): raise E.UnfilledOrder('nothing executed')
    e._market_close = unfilled
    errs = []; e.err = lambda msg, **kw: errs.append(msg)
    e._risk_fill_run(k, lot, e.marks['BTCUSDT'])                         # notfound -> one more send (attempt 2), unfilled
    assert d['attempts'] == 2 and d['state'] == 'due'
    e._risk_fill_run(k, lot, e.marks['BTCUSDT'])                         # bounded: residual + alert, nothing more sent
    assert d['state'] == 'residual' and d['residual'] > d['planned'] and any('residual' in x for x in errs)
    assert e._add_block(lot, 1.0, 100.0).startswith('risk_residual')
    assert not any(v.get('state') in ('due', 'sent') for v in lot['risk_fill'].values())


def test_unmeasured_entry_blocks_adds_and_keeps_the_lot_managed():
    e = eng(); k, lot = opened(e)
    assert e._risk_fill_block(lot) is None                              # still arriving: not missing (ruling 5)
    lot['fill_px_partial'] = ['fill_px_lost']
    assert e._risk_fill_block(lot).startswith('risk_unmeasured')         # lost: missing -> blocked
    lot['fill_px_partial'] = ['fill_px_pending']
    d = measure(e, lot, None)
    assert d['action'] == 'unmeasured' and e._add_block(lot, 1.0, 100.0).startswith('risk_unmeasured')
    e.manage(e.marks)
    assert k in e.state['lots'] and not e.trade.cids


def test_favorable_fill_is_record_only():
    e = eng(); k, lot = opened(e)
    d = measure(e, lot, lot['fills'][0][3] - 0.1)
    assert d['action'] == 'ok' and d['state'] == 'done' and d['why'] == 'favorable'
    e.manage(e.marks)
    assert not e.trade.cids and e._risk_fill_block(lot) is None


def test_ceiling_unwinds_the_whole_lot_into_history_with_the_record():
    e = eng(); k, lot = opened(e)
    d = measure(e, lot, adverse(lot, 1.2), fees=0.0)
    assert d['action'] == 'unwind' and d['why'] == 'ceiling'
    e.manage(e.marks)
    assert k not in e.state['lots']
    h = e.history[-1]
    assert h['exit_reason'] == 'risk_unwind' and h['risk_fill']['0']['action'] == 'unwind' and h['risk_fill']['0']['state'] == 'done'


def test_each_add_is_checked_on_its_own():
    e = eng(); k, lot = opened(e)
    measure(e, lot, lot['fills'][0][3])                                  # entry exactly as booked: ok
    lot['fills'].append(['t1', 'pyramid_add', 1.0, 100.0]); AC.link_px(lot, 'c-add-1')
    d = measure(e, lot, 100.0 + 0.6 * abs(100.0 - lot['stop']), i=1, expected=100.0)
    assert lot['risk_fill']['0']['state'] == 'done' and d['fill'] == 1 and d['action'] in ('trim', 'unwind', 'below_step')


def test_legacy_lot_shows_unavailable_and_is_not_blocked():
    e = eng(); k, lot = opened(e)
    for f in ('fill_refs', 'px_unmeasured', 'px_unlinked_ok', 'risk_fill'): lot.pop(f, None)
    assert e.risk_fill_view(lot) == 'unavailable' and e._risk_fill_block(lot) is None


def test_entry_reported_by_the_exchange_is_judged_on_its_anchored_stop():
    """The stop is anchored at the first fill's BOOKED price: when Binance reported the real price, risk is exactly the
    plan even though the decision (expected) price differed - never a false trim (plan basis = booked fill 0)."""
    e = eng(); k, lot = opened(e)
    b = lot['fills'][0][3]
    d = measure(e, lot, b, expected=b - 0.9 * abs(b - lot['stop']))     # decision price far better than the fill
    assert d['action'] == 'ok' and d['planned_px'] == b


def test_trim_leaves_the_stop_marked_for_resize():
    e = eng(); k, lot = opened(e)
    d = measure(e, lot, adverse(lot, 0.5))
    e._risk_fill_run(k, lot, e.marks['BTCUSDT'])
    assert d['state'] == 'done' and lot['stop_dirty'] is True


def test_non_finite_inputs_fail_closed_as_unmeasured():
    nan, inf = float('nan'), float('inf')
    for args in ((nan, 95.0, 100.0), (101.0, nan, 100.0), (101.0, 95.0, nan), (inf, 95.0, 100.0), (101.0, -inf, 100.0)):
        assert RF.decide('LONG', 1.0, *args)['action'] == 'unmeasured'
    d = RF.decide('LONG', 1.0, 101.0, 95.0, 100.0, entry_fees=nan)
    assert d['fee_basis'] == 'estimate' and d['action'] in ('ok', 'trim', 'unwind', 'below_step')


def test_check_error_records_unmeasured_and_blocks_adds():
    e = eng(); k, lot = opened(e)
    orig = RF.decide
    def boom(*a, **kw): raise ValueError('boom')
    RF.decide = boom
    try: d = measure(e, lot, adverse(lot, 0.5))
    finally: RF.decide = orig
    assert d['state'] == 'unmeasured' and d['why'] == 'risk_check_error'
    assert e._add_block(lot, 1.0, 100.0).startswith('risk_unmeasured')


def test_a_trim_that_keeps_nothing_is_an_unwind_even_without_exchange_minimums():
    """Cowork 6351190962: keep floors to 0 with min_qty = min_notional = 0 -> an unwind, never a 'trim' of everything."""
    d = RF.decide('LONG', 1.0, 100.0, 90.0, 97.0, step=1.0)                # ratio ~1.43, keep = floor(~0.7) = 0
    assert d['action'] == 'unwind' and d['why'] == 'remainder_below_min' and d['keep_qty'] == 0.0 and d['trim_qty'] == 1.0
