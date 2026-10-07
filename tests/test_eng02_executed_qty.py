"""FBL-ENG02: every MARKET answer is booked by what Binance really executed (status + executedQty), never by the
requested quantity. Zero fill books nothing, a partial books only the part (avg from avgPrice / cumQuote) and the real
remainder keeps a correctly sized stop; NEW / PARTIALLY_FILLED / quantity-less / lost answers converge through the
client-id order lookup, a critical position read or the pending machinery before anything is booked.
Run:  python -m pytest -q tests/test_eng02_executed_qty.py"""
import os, sys, time
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety import mk_engine, SL, SG                                 # noqa: E402
from test_v31_engine import maker_engine, age                             # noqa: E402
from test_fills import recs, summ                                         # noqa: E402
import binance_client as BC                                               # noqa: E402
import engine as E                                                        # noqa: E402
import trade_audit as TA                                                  # noqa: E402

SYM = 'BTCUSDT'


def script(e, name, executed, status='EXPIRED', avg=None, cid=None, once=True, **over):
    """The fake exchange answers the next `name` ('open'/'close') MARKET order with `status` and moves the position by
    `executed` only ('all' = the requested quantity, a float = that much, a fraction string 'x0.5' = that share)."""
    fx = e.trade
    def f(s, ps, q):
        if once: fx.__dict__.pop(name, None)
        fx._f(name)
        q = float(q)
        x = q if executed == 'all' else (round(q * float(executed[1:]), 3) if isinstance(executed, str) else float(executed))
        fx.pos[(s, ps)] = round(fx.pos.get((s, ps), 0) + (x if name == 'open' else -x), 9)
        o = dict(status=status, executedQty=f'{x:.3f}', origQty=f'{q:.3f}', avgPrice=str(avg if avg is not None else fx.mark[s]))
        if cid: o['clientOrderId'] = cid
        o.update(over)
        return {k: v for k, v in o.items() if v is not None}
    setattr(fx, name, f)


def lot1(e):
    return next(iter(e.state['lots'].values()))


def stop_of(e, lot):
    return e.trade.stops[lot['stop_id']]


def protected(e, lot):
    """The exchange holds exactly the lot and its single stop is sized to it."""
    st = [v for v in e.trade.stops.values() if v[0] == lot['symbol'] and v[1] == lot['side']]
    assert len(st) == 1 and abs(st[0][2] - lot['qty']) < 1e-9, (st, lot['qty'])
    assert abs(e.trade.pos.get((lot['symbol'], lot['side']), 0) - lot['qty']) < 1e-9
    assert 'qty_mismatch' not in TA.cohort_ledger(lot), TA.cohort_ledger(lot)           # T05a ledger == booked quantity


def fill_recs(e, kind):
    return [x for x in recs(e) if x['kind'] == kind]


# ------------------------------------------------------------------ entries
def test_entry_zero_fill_books_nothing():
    e, _ = mk_engine()
    script(e, 'open', 0.0, 'EXPIRED')
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False
    assert not e.state['lots'] and not e.trade.stops and not e.trade.pos.get((SYM, 'LONG'))
    assert e.last_skip == 'entry order not filled'
    assert TA.reason_code(e.last_skip) == ('execution', 'entry_unfilled')
    assert any('not filled (Binance executed 0 of' in x[1] and 'nothing booked' in x[1] for x in e.health['errors'])
    r = fill_recs(e, 'entry_market')[-1]
    assert r['outcome'] == 'unfilled' and r['qty_fill'] == 0 and r['qty_req'] > 0 and r['status'] == 'EXPIRED'
    assert summ(e)['by_kind']['entry_market']['unfilled'] == 1
    assert not any(t.get('event') == 'entry' for t in _trades(e))


def _trades(e):
    import csv
    p = e.F.get('trades')
    if not p or not os.path.exists(p): return []
    with open(p, encoding='utf-8') as f: return list(csv.DictReader(f))


def test_entry_zero_fill_through_the_cycle_records_a_miss():
    e, _ = mk_engine()
    script(e, 'open', 0.0, 'EXPIRED')
    e.last_skip = ''
    if not e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()): e.miss(SL, SYM, 'LONG', SG, e.last_skip)   # what cycle() does
    m = e.missed[-1]
    assert (m['reason'], m['stage'], m['code']) == ('entry order not filled', 'execution', 'entry_unfilled')


@pytest.mark.parametrize('status', ['EXPIRED', 'FILLED', 'CANCELED'])
def test_entry_partial_books_only_the_executed_quantity(status):
    e, _ = mk_engine()
    script(e, 'open', 'x0.4', status, avg=101.0)
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()), e.last_skip
    lot = lot1(e); r = fill_recs(e, 'entry_market')[-1]
    assert lot['qty'] == lot['q0'] == r['qty_fill'] and r['qty_fill'] < r['qty_req'] and r['outcome'] == 'partial'
    assert lot['avg'] == lot['e0'] == 101.0 and lot['fills'] == [[lot['fills'][0][0], 'entry', lot['qty'], 101.0]]
    protected(e, lot)
    assert abs(stop_of(e, lot)[3] - lot['stop']) < 1e-9 and lot['stop'] < 101.0


def test_entry_full_fill_unchanged():
    e, _ = mk_engine()
    script(e, 'open', 'all', 'FILLED')
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity())
    lot = lot1(e); r = fill_recs(e, 'entry_market')[-1]
    assert r['outcome'] == 'filled' and lot['qty'] == r['qty_req'] == r['qty_fill']; protected(e, lot)


def test_entry_expired_partial_avg_from_cum_quote():
    e, _ = mk_engine()
    script(e, 'open', 0.05, 'EXPIRED', avg='0', cumQuote='5.06')        # 0.05 @ 101.2 = 5.06
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity())
    lot = lot1(e)
    assert lot['qty'] == 0.05 and abs(lot['avg'] - 101.2) < 1e-9; protected(e, lot)


def test_entry_new_answer_converges_by_client_id():
    e, _ = mk_engine(); looked = []
    def get_order(s, cid):
        looked.append((s, cid)); return dict(status='EXPIRED', executedQty='0.030', avgPrice='100.5', clientOrderId=cid)
    e.trade.get_order = get_order
    script(e, 'open', 0.03, 'NEW', cid='zbA', executedQty='0', avgPrice='0')
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity())
    lot = lot1(e)
    assert looked == [(SYM, 'zbA')] and lot['qty'] == 0.03 and lot['avg'] == 100.5; protected(e, lot)
    assert fill_recs(e, 'entry_market')[-1]['status'] == 'EXPIRED'


def test_entry_still_working_is_tracked_protected_at_once_and_booked_from_the_record(monkeypatch):
    e, _ = mk_engine(); monkeypatch.setattr(E, 'ORDER_LOOKUP_GAP_S', 0.0)
    rec = dict(status='PARTIALLY_FILLED', executedQty='0.020', clientOrderId='zbB')
    e.trade.get_order = lambda s, cid: dict(rec) if cid == 'zbB' else None
    script(e, 'open', 0.02, 'PARTIALLY_FILLED', cid='zbB', executedQty='0.020')
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False and e.last_skip == 'entry order unconfirmed'
    assert not e.state['lots'] and 'zbB' in e.state['unconfirmed_entries']
    prov = e.state['unconfirmed_entries']['zbB']['prov']
    assert prov and e.trade.stops[prov][2] == 0.02                                   # provisional stop placed right away
    assert fill_recs(e, 'entry_market')[-1]['outcome'] == 'unknown'          # telemetry: not claimed as a fill
    e.manage(e.trade.marks()); e.manage(e.trade.marks())
    assert not e.untracked and not e.state['lots']                                   # never reported untracked while waiting
    rec.update(status='EXPIRED', executedQty='0.030', avgPrice='100.4'); e.trade.pos[(SYM, 'LONG')] = 0.03
    e.manage(e.trade.marks())
    lot = lot1(e); assert lot['qty'] == 0.03 and lot['avg'] == 100.4 and not e.state['unconfirmed_entries']
    assert prov not in e.trade.stops; protected(e, lot)


def test_entry_without_quantity_or_client_id_books_the_clean_position_delta():
    e, _ = mk_engine()
    script(e, 'open', 0.03, None, executedQty=None)          # (replay / fake answers: no status, quantity or client id)
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity())
    lot = lot1(e); assert lot['qty'] == 0.03; protected(e, lot)
    assert fill_recs(e, 'entry_market')[-1]['outcome'] == 'unknown'


def test_entry_without_quantity_and_no_position_is_unconfirmed_never_the_request():
    e, _ = mk_engine()
    script(e, 'open', 0.0, None, executedQty=None)                           # no status, no quantity, nothing executed
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False
    assert not e.state['lots'] and e.last_skip == 'entry order unconfirmed'


def test_entry_without_quantity_and_unreadable_position_is_unconfirmed():
    e, _ = mk_engine()
    script(e, 'open', 'all', None, executedQty=None); e.trade.fail.add('positions')
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False
    assert not e.state['lots'] and e.last_skip == 'entry order unconfirmed'


def test_lost_entry_answer_with_a_final_partial_record_is_booked_and_protected():
    e, _ = mk_engine()
    def lost(s, ps, q):
        e.trade.pos[(s, ps)] = e.trade.pos.get((s, ps), 0) + 0.04
        raise BC.AmbiguousOrder('market order zbC status EXPIRED', 'c:zbC')
    e.trade.open = lost
    e.trade.get_order = lambda s, cid: dict(status='EXPIRED', executedQty='0.040', avgPrice='100.2', clientOrderId=cid)
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity())
    lot = lot1(e); assert lot['qty'] == 0.04 and lot['avg'] == 100.2; protected(e, lot)


def test_lost_entry_answer_without_a_record_is_tracked_then_dropped():
    e, _ = mk_engine()
    def lost(s, ps, q): raise BC.AmbiguousOrder('answer lost', 'c:zbD')
    e.trade.open = lost
    e.trade.get_order = lambda s, cid: None
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False and e.last_skip == 'entry order unconfirmed'
    assert 'zbD' in e.state['unconfirmed_entries']
    e.manage(e.trade.marks()); assert 'zbD' in e.state['unconfirmed_entries']       # < 20 s: may not be visible yet
    e.state['unconfirmed_entries']['zbD']['t'] -= E.UNCONF_DROP_S + 1; e.manage(e.trade.marks())
    assert not e.state['unconfirmed_entries'] and not e.state['lots'] and not e.trade.stops


# ------------------------------------------------------------------ adds
DCA = {'dca': {'n': 3, 'step_atr': 1.0, 'scale': 1.5, 'tp_atr': 1.0, 'stop_atr': 2.0}}
PY = {'pyramid': {'n': 2, 'step_r': 1.0, 'frac': 0.5}}


def _open(e, mgmt, key=None):
    sl = dict(SL, mgmt=mgmt, **({'key': key} if key else {})); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, SYM, 'LONG', SG, None, e.equity()), e.last_skip
    return lot1(e)


def test_dca_add_zero_fill_books_nothing_and_is_retried():
    e, _ = mk_engine(); lot = _open(e, DCA, 'dca_dip'); q0, avg0, tp0 = lot['qty'], lot['avg'], lot['tp']
    script(e, 'open', 0.0, 'EXPIRED')
    e.trade.mark[SYM] = lot['levels'][0] - 0.01; e.manage(e.trade.marks())
    assert lot['dca'] == 0 and lot['qty'] == q0 and lot['avg'] == avg0 and lot['tp'] == tp0 and len(lot['fills']) == 1
    assert any('safety_order: Binance executed 0 of' in x[1] for x in e.health['errors']); protected(e, lot)
    assert fill_recs(e, 'safety_order')[-1]['outcome'] == 'unfilled'
    e.manage(e.trade.marks())                                                  # next pass: the normal fake fills it
    assert lot['dca'] == 1 and lot['qty'] > q0; protected(e, lot)


def test_dca_add_partial_books_the_part_and_resizes_the_stop():
    e, _ = mk_engine(); lot = _open(e, DCA, 'dca_dip'); q0 = lot['qty']
    script(e, 'open', 'x0.5', 'EXPIRED', avg=97.5)
    e.trade.mark[SYM] = lot['levels'][0] - 0.01; e.manage(e.trade.marks())
    r = fill_recs(e, 'safety_order')[-1]
    assert lot['dca'] >= 1 and r['outcome'] == 'partial' and lot['fills'][1][2] == r['qty_fill'] < r['qty_req']
    assert lot['fills'][1][3] == 97.5
    assert abs(lot['avg'] - (q0 * 100.0 + r['qty_fill'] * 97.5) / (q0 + r['qty_fill'])) < 1e-9
    assert abs(lot['tp'] - (lot['avg'] + DCA['dca']['tp_atr'] * lot['atr0'])) < 1e-9
    protected(e, lot)


def test_pyramid_add_zero_and_partial():
    e, _ = mk_engine(); lot = _open(e, PY); q0 = lot['qty']
    script(e, 'open', 0.0, 'EXPIRED')
    e.trade.mark[SYM] = 106.0; e.manage(e.trade.marks())
    assert lot['adds'] == 0 and lot['qty'] == q0; protected(e, lot)
    script(e, 'open', 'x0.4', 'EXPIRED', avg=106.2)
    e.manage(e.trade.marks())
    r = fill_recs(e, 'pyramid_add')[-1]
    assert lot['adds'] == 1 and abs(lot['qty'] - (q0 + r['qty_fill'])) < 1e-9 and r['qty_fill'] < r['qty_req']
    assert lot['fills'][-1][1:] == ['pyramid_add', r['qty_fill'], 106.2]; protected(e, lot)


# ------------------------------------------------------------------ partial take-profits
def test_tp1_partial_execution_books_only_the_part():
    e, _ = mk_engine(); lot = _open(e, {'tp1_r': 1.0, 'tp1_frac': 0.5}); q0 = lot['qty']
    script(e, 'close', 'x0.4', 'EXPIRED', avg=110.0)
    e.trade.mark[SYM] = 110.0; e.manage(e.trade.marks())
    got = lot['fills'][-1][2]
    assert lot['tp1'] and lot['fills'][-1][1] == 'take_profit_1' and got == round(round(q0 * 0.5, 3) * 0.4, 3)
    assert abs(lot['qty'] - (q0 - got)) < 1e-9 and abs(lot['realized'] - got * (110.0 - 100.0)) < 1e-9
    assert any('take_profit_1: Binance executed' in x[1] for x in e.health['errors'])
    protected(e, lot)
    r = fill_recs(e, 'exit')[-1]; assert r['outcome'] == 'partial' and r['qty_fill'] == got


def test_tp1_zero_fill_books_nothing_and_fires_again():
    e, _ = mk_engine(); lot = _open(e, {'tp1_r': 1.0, 'tp1_frac': 0.5}); q0 = lot['qty']
    script(e, 'close', 0.0, 'EXPIRED')
    e.trade.mark[SYM] = 110.0; e.manage(e.trade.marks())
    assert not lot['tp1'] and lot['qty'] == q0 and len(lot['fills']) == 1 and not lot.get('realized'); protected(e, lot)
    e.manage(e.trade.marks())
    assert lot['tp1'] and abs(lot['qty'] - (q0 - round(q0 * 0.5, 3))) < 1e-9; protected(e, lot)


def test_ladder_partial_execution():
    e, _ = mk_engine(); lot = _open(e, {'tps': [[1, .5], [3, .25]]}); q0 = lot['qty']
    script(e, 'close', 'x0.5', 'EXPIRED')
    e.trade.mark[SYM] = 106.0; e.manage(e.trade.marks())
    got = lot['fills'][-1][2]
    assert lot['tps_done'] == [0] and 0 < got < round(q0 * .5, 3) and abs(lot['qty'] - (q0 - got)) < 1e-9; protected(e, lot)


# ------------------------------------------------------------------ full closes
def test_full_close_partial_keeps_the_remainder_protected_and_retries():
    e, _ = mk_engine(); lot = _open(e, {'tp_r': 1.0}); k = next(iter(e.state['lots'])); q0 = lot['qty']
    script(e, 'close', 'x0.6', 'EXPIRED', avg=106.0)
    e.trade.mark[SYM] = 106.0; e.manage(e.trade.marks())
    got = lot['fills'][-1][2]
    assert k in e.state['lots'] and abs(lot['qty'] - (q0 - got)) < 1e-9 and not e.history
    assert any('closed' in x[1] and 'kept with its stop' in x[1] for x in e.health['errors'])
    protected(e, lot)
    e.manage(e.trade.marks())                                                  # retried: the rest closes, then the trade is final
    assert k not in e.state['lots'] and not e.trade.pos.get((SYM, 'LONG')) and not e.trade.stops
    h = e.history[-1]; assert h['exit_reason'] == 'take_profit' and abs(sum(f[2] for f in h['fills'] if f[1] == 'take_profit') - q0) < 1e-9


def test_full_close_zero_fill_keeps_lot_and_stop():
    e, _ = mk_engine(); lot = _open(e, {}); k = next(iter(e.state['lots'])); stop_id = lot['stop_id']
    script(e, 'close', 0.0, 'EXPIRED')
    with pytest.raises(E.UnfilledOrder, match='executed 0 of'): e.close_lot(k, 'exit_signal', 100.0)
    assert k in e.state['lots'] and lot['stop_id'] == stop_id and len(lot['fills']) == 1 and not e.history; protected(e, lot)
    r = fill_recs(e, 'exit')[-1]
    assert r['outcome'] == 'unfilled' and r['qty_fill'] == 0 and r['qty_req'] == lot['qty'] and r['status'] == 'EXPIRED'
    assert summ(e)['by_kind']['exit']['unfilled'] == 1


def test_flatten_with_partial_and_zero_execution():
    e, _ = mk_engine()
    a = _open(e, {}); ka = next(iter(e.state['lots']))
    sl2 = dict(SL, id='U'); assert e.open_lot(sl2, 'ETHUSDT', 'LONG', dict(SG, close=50.0), None, e.equity())
    kb = next(k for k in e.state['lots'] if k != ka); b = e.state['lots'][kb]
    qa = a['qty']; calls = []
    def close(s, ps, q):
        calls.append(s); x = round(float(q) * 0.5, 3) if s == SYM else 0.0
        e.trade.pos[(s, ps)] = round(e.trade.pos[(s, ps)] - x, 9)
        return dict(status='EXPIRED', executedQty=f'{x:.3f}', avgPrice=str(e.trade.mark[s]))
    e.trade.close = close
    res = e.flatten()
    assert not res['closed'] and sorted(f[0] for f in res['failed']) == sorted([ka, kb])
    fa = dict(res['failed'])
    assert 'kept with its stop' in fa[ka] and 'executed 0 of' in fa[kb]
    assert abs(a['qty'] - (qa - round(qa * 0.5, 3))) < 1e-9; protected(e, a); protected(e, b)
    assert res['still_open'] == {f'{SYM}|LONG': a['qty'], 'ETHUSDT|LONG': b['qty']}


def test_lost_close_answer_resolved_by_a_partial_order_record():
    e, _ = mk_engine(); lot = _open(e, {}); k = next(iter(e.state['lots'])); q0 = lot['qty']
    def lost(s, ps, q):
        e.trade.pos[(s, ps)] = round(e.trade.pos[(s, ps)] - 0.05, 9)
        raise BC.AmbiguousOrder('market order zbE status EXPIRED', 'c:zbE')
    e.trade.close = lost
    e.trade.get_order = lambda s, cid: dict(status='EXPIRED', executedQty='0.050', avgPrice='99.0', clientOrderId=cid) if cid == 'zbE' else None
    with pytest.raises(BC.AmbiguousOrder): e.close_lot(k, 'exit_signal', 100.0)
    assert lot['pending']['cid'] == 'zbE'
    del e.trade.close; e.manage(e.trade.marks())
    assert k in e.state['lots'] and not lot.get('pending') and abs(lot['qty'] - (q0 - 0.05)) < 1e-9
    assert lot['fills'][-1][1:] == ['exit_signal', 0.05, 99.0] and not e.history; protected(e, lot)


def test_lost_add_answer_with_a_zero_order_record_is_dropped_at_once():
    e, _ = mk_engine(); lot = _open(e, PY); q0 = lot['qty']
    def lost(s, ps, q): raise BC.AmbiguousOrder('answer lost', 'c:zbF')
    e.trade.open = lost
    e.trade.get_order = lambda s, cid: dict(status='EXPIRED', executedQty='0', clientOrderId=cid)
    e.trade.mark[SYM] = 106.0; e.manage(e.trade.marks())
    assert lot['pending']['cid'] == 'zbF'
    del e.trade.open; e.manage(e.trade.marks())       # no 20 s wait: the record proves 0 executed -> re-sent in the same pass
    assert not lot.get('pending') and lot['adds'] == 1 and [f[1] for f in lot['fills']] == ['entry', 'pyramid_add']
    assert abs(lot['qty'] - q0 * 1.5) < 1e-9; protected(e, lot)


def test_close_without_quantity_is_confirmed_by_the_position_or_goes_pending():
    e, _ = mk_engine(); lot = _open(e, {'tp1_r': 1.0, 'tp1_frac': 0.5}); q0 = lot['qty']
    script(e, 'close', 0.0, None, executedQty=None)                          # quantity-less answer, nothing executed
    e.trade.mark[SYM] = 110.0; e.manage(e.trade.marks())
    assert lot.get('pending') and lot['pending']['kind'] == 'close' and lot['qty'] == q0 and not lot['tp1']
    lot['pending']['t'] -= 30; e.manage(e.trade.marks())                     # the position never moved -> dropped, retried
    assert not lot.get('pending'); e.manage(e.trade.marks())
    assert lot['tp1'] and abs(lot['qty'] - (q0 - round(q0 * 0.5, 3))) < 1e-9; protected(e, lot)


# ------------------------------------------------------------------ maker fallback market leg
def test_maker_fallback_market_leg_partial_and_zero():
    for mode in ('partial', 'zero'):
        e, bk = maker_engine()
        e.open_lot(SL, SYM, 'LONG', SG, None, e.equity())
        bk.fill(0.4); age(e); e.manage(e.trade.marks())
        script(e, 'open', 'x0.5' if mode == 'partial' else 0.0, 'EXPIRED')
        for _ in range(3): age(e, total=True); e.manage(e.trade.marks())
        lot = lot1(e)
        mk = [x for x in recs(e) if x['kind'] == 'entry_maker'][-1]
        fb = fill_recs(e, 'entry_fallback')[-1]
        if mode == 'partial':
            assert fb['outcome'] == 'partial' and mk.get('fallback_confirmed') is True
            assert abs(lot['qty'] - (lot['maker_qty'] + fb['qty_fill'])) < 1e-9
        else:
            assert fb['outcome'] == 'unfilled' and 'executed 0 of' in mk['fallback_failed'] and 'fallback_confirmed' not in mk
            assert lot['qty'] == lot['maker_qty']
        protected(e, lot)


def test_maker_unfilled_fallback_zero_fill_records_a_miss():
    e, bk = maker_engine(); e.S['SLEEVES'] = [dict(SL, enabled=True)]
    e.open_lot(SL, SYM, 'LONG', SG, None, e.equity())
    script(e, 'open', 0.0, 'EXPIRED')
    for _ in range(5): age(e, total=True); e.manage(e.trade.marks())
    assert not e.state['lots'] and not e.trade.pos.get((SYM, 'LONG'))
    assert e.missed[-1]['reason'] == 'entry order not filled' and e.missed[-1]['code'] == 'entry_unfilled'
    mk = [x for x in recs(e) if x['kind'] == 'entry_maker'][-1]
    assert mk['fallback_attempted'] is True and 'fallback_confirmed' not in mk


# ------------------------------------------------------------------ grid (same helpers)
def test_grid_first_order_and_adds_book_executed_quantity():
    import test_grid as TG
    e, gm, _ = TG.mk()
    with e.lock: gm.start('G1', SYM)
    g = TG.grid_of(e); top = max(c['a'] for c in g['cells'] if c['k'] == 'L')
    script(e, 'open', 0.0, 'EXPIRED')
    TG.tick(e, gm, top - 0.05)
    assert not e.state['lots'] and not g['op'] and not any(c['f'] for c in g['cells'])
    script(e, 'open', 'x0.5', 'EXPIRED')
    TG.tick(e, gm, top - 0.05)
    L = TG.lot_of(e, 'LONG'); q_cell = next(c['q'] for c in g['cells'] if c['k'] == 'L' and c['f'])
    assert L and abs(L['qty'] - round(q_cell * 0.5, 3)) < 1e-9 and abs(e.trade.pos[(SYM, 'LONG')] - L['qty']) < 1e-9
    st = [v for v in e.trade.stops.values() if v[1] == 'LONG']; assert len(st) == 1 and abs(st[0][2] - L['qty']) < 1e-9


# ------------------------------------------------------------------ FBL-ENG02 r2 (adversarial review): regressions
class _Resp:
    def __init__(self, code, data): self.status_code, self._d, self.headers, self.content = code, data, {}, b'x'
    def json(self): return self._d


def real_client(route):
    """The real binance_client.Futures with an injected transport: route(method, path, params) -> (http code, json)."""
    import types
    c = BC.Futures.__new__(BC.Futures)
    c.key, c.secret, c.base, c.rw, c.offset, c.last_ok = 'k', b's', BC.TESTNET, 6000, 0, 0
    calls = []
    def request(method, url, params=None, timeout=None):
        path = url.split('/fapi')[1]; calls.append((method, path, dict(params or {})))
        return _Resp(*route(method, path, dict(params or {})))
    c.s = types.SimpleNamespace(request=request, headers={}, get=lambda *a, **k: _Resp(200, {'serverTime': 0}))
    c.sync_time = lambda: None
    return c, calls


def _new_entry_engine(monkeypatch, record):
    """Engine on FakeX, but MARKET entries and order lookups go through the REAL client: Binance answers NEW / 0 (RESULT
    not ready), the order fills on the exchange, the client-id lookup returns `record` (mutable dict)."""
    monkeypatch.setattr(E, 'ORDER_LOOKUP_GAP_S', 0.0)
    e, _ = mk_engine(); fx = e.trade; sent = {}
    def route(method, path, p):
        if path == '/v1/order' and method == 'POST':
            sent['cid'] = p['newClientOrderId']; q = float(p['quantity'])
            fx.pos[(p['symbol'], p['positionSide'])] = fx.pos.get((p['symbol'], p['positionSide']), 0) + q
            record.setdefault('origQty', p['quantity'])
            return 200, dict(status='NEW', executedQty='0', avgPrice='0.00', cumQuote='0', origQty=p['quantity'],
                             clientOrderId=p['newClientOrderId'], orderId=1)
        if path == '/v1/order' and method == 'GET':
            return (200, dict(record, clientOrderId=p['origClientOrderId'], orderId=1)) if record.get('status') else (400, {'code': -2013, 'msg': 'Order does not exist.'})
        if method == 'DELETE': record.setdefault('cancelled', True); return 200, {'status': 'CANCELED'}
        raise AssertionError((method, path))
    c, calls = real_client(route)
    fx.open = c.open; fx.get_order = c.get_order
    fx_cancel = fx.cancel
    fx.cancel = lambda s, tag: c.cancel(s, tag) if tag.startswith('c:') else fx_cancel(s, tag)
    return e, sent, calls


def test_r1_new_answer_with_a_lagging_position_is_protected_and_booked(monkeypatch):
    """D1: NEW / 0, lookups still NEW, the first critical position read lags. Base booked lot+stop; 52f00e9 left the
    position without a stop. Now: tracked by client id, provisional stop on the first reconcile that sees it, never
    untracked, then booked from the FINAL record with its own stop."""
    record = dict(status='NEW', executedQty='0')
    e, sent, calls = _new_entry_engine(monkeypatch, record)
    fx = e.trade; real_pos = fx.positions; lag = {'n': 1}
    def positions(*a, **k):
        p = real_pos()
        if lag['n'] > 0: lag['n'] -= 1; p.pop((SYM, 'LONG'), None)
        return p
    fx.positions = positions
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False
    assert sum(1 for m, p_, _ in calls if m == 'GET') == 4 == E.ORDER_LOOKUPS           # 4 lookups, all still NEW
    u = e.state['unconfirmed_entries'][sent['cid']]; assert u['prov'] is None and not fx.stops
    e.manage(fx.marks())
    assert u['prov'] and fx.stops[u['prov']][2] == u['qty'] and not e.untracked       # protected on the next pass
    assert e.entry_block(SL, SYM, 'LONG') == 'an entry is already working on this coin'
    with pytest.raises(E.LevReject, match='unconfirmed'): e._exposure_proof(SYM, 100.0, 1.0)
    for _ in range(3): e.manage(fx.marks())
    assert not e.untracked and not e.state['lots']
    record.update(status='FILLED', executedQty=u['qty'] and f"{u['qty']:.3f}", avgPrice='100.3')
    e.manage(fx.marks())
    lot = lot1(e); assert lot['qty'] == u['qty'] and lot['avg'] == 100.3 and not e.state['unconfirmed_entries']
    protected(e, lot)


def test_r1_record_never_final_is_adopted_with_a_stop_at_give_up(monkeypatch):
    record = dict(status='NEW', executedQty='0')
    e, sent, _ = _new_entry_engine(monkeypatch, record)
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False
    u = e.state['unconfirmed_entries'][sent['cid']]; assert u['prov']                  # the position was visible at once
    u['t'] -= E.UNCONF_CANCEL_S + 1; e.manage(e.trade.marks())
    assert record.get('cancelled') and sent['cid'] in e.state['unconfirmed_entries']   # cancelled by client id, still waiting
    u['t'] -= E.UNCONF_GIVEUP_S; e.manage(e.trade.marks())
    lot = lot1(e); assert lot['qty'] == u['qty'] and not e.state['unconfirmed_entries']; protected(e, lot)


def test_r1_record_zero_drops_the_entry_and_its_provisional_stop(monkeypatch):
    record = dict(status='NEW', executedQty='0')
    e, sent, _ = _new_entry_engine(monkeypatch, record)
    e.trade.pos.clear()                                     # (this time the order did not fill: undo the fake's fill)
    real_open = e.trade.open
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False
    e.trade.pos.clear(); u = e.state['unconfirmed_entries'][sent['cid']]
    record.update(status='EXPIRED', executedQty='0')
    e.manage(e.trade.marks())
    assert not e.state['unconfirmed_entries'] and not e.state['lots'] and not e.trade.stops and not e.untracked


def test_r1_provisional_stop_is_kept_while_the_coin_is_unsettled(monkeypatch):
    record = dict(status='NEW', executedQty='0')
    e, sent, _ = _new_entry_engine(monkeypatch, record)
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False
    u = e.state['unconfirmed_entries'][sent['cid']]; assert u['prov']
    e.state['over_seen'] = {f'{SYM}|LONG': 1}               # something else on this coin/side is not settled
    u['t'] -= E.UNCONF_GIVEUP_S + 1; e.reconcile(e.equity())
    assert sent['cid'] in e.state['unconfirmed_entries'] and u['prov'] in e.trade.stops and not e.state['lots']


def test_r2_ladder_zero_after_a_fill_leaves_the_stop_marked_and_repaired():
    """D2: level 1 fills, level 2 EXPIRED 0 in the same pass -> the stop was left at the old size forever."""
    e, _ = mk_engine(); lot = _open(e, {'tps': [[1, .5], [3, .25]]}); fx = e.trade; n = {'i': 0}
    def cl(s, ps, q):
        n['i'] += 1; x = float(q) if n['i'] == 1 else 0.0
        fx.pos[(s, ps)] = round(fx.pos[(s, ps)] - x, 9)
        return dict(status='FILLED' if x else 'EXPIRED', executedQty=f'{x:.3f}', avgPrice=str(fx.mark[s]))
    fx.close = cl
    fx.mark[SYM] = 140.0; e.manage(fx.marks())
    assert lot['tps_done'] == [0] and lot['stop_dirty'] is True
    e.manage(fx.marks())                                    # level 2 still EXPIRED, but the stop is resized first
    assert lot['tps_done'] == [0] and stop_of(e, lot)[2] == lot['qty'] == fx.pos[(SYM, 'LONG')]


def test_r3_a_quantity_less_entry_never_books_a_siblings_pending_add(monkeypatch):
    """D3 (R4): sibling A has a lost pyramid add that DID fill; B's entry answer has no quantity and no client id and
    did not fill. The position delta must not be booked as B's lot."""
    monkeypatch.setattr(E, 'ORDER_LOOKUP_GAP_S', 0.0)
    e, _ = mk_engine(); fx = e.trade
    slA = dict(SL, id='A', mgmt=PY); slB = dict(SL, id='B', mgmt={}); e.S['SLEEVES'] = [slA, slB]
    assert e.open_lot(slA, SYM, 'LONG', SG, None, e.equity())
    A = lot1(e)
    def lost(s, ps, q): fx.pos[(s, ps)] += float(q); raise BC.AmbiguousOrder('answer lost', 'c:zbADD')
    fx.open = lost; fx.mark[SYM] = 106.0; e.manage(fx.marks()); assert A.get('pending')
    fx.open = lambda s, ps, q: dict(avgPrice='106.0')       # no status, no quantity, no client id, nothing executed
    assert e.open_lot(slB, SYM, 'LONG', dict(SG, close=106.0), None, e.equity()) is False
    assert e.last_skip == 'entry order unconfirmed' and [l['sleeve'] for l in e.state['lots'].values()] == ['A']
    del fx.open; A['pending']['t'] -= 30; e.manage(fx.marks())
    assert A['adds'] == 1 and abs(A['qty'] - fx.pos[(SYM, 'LONG')]) < 1e-9 and not e.untracked


@pytest.mark.parametrize('dirty', ['clean', 'untracked', 'over_seen', 'pending', 'unconfirmed', 'grid'])
def test_r3_position_confirmation_refuses_an_unsettled_coin(dirty):
    e, _ = mk_engine(); lot = _open(e, {}); q0 = lot['qty']
    e.trade.pos[(SYM, 'LONG')] = round(q0 - 0.5, 9)         # the position DID move by exactly the close
    if dirty == 'untracked': e.untracked = {f'{SYM}|LONG': 0.5}
    elif dirty == 'over_seen': e.state['over_seen'] = {f'{SYM}|LONG': 1}
    elif dirty == 'pending':
        e.state['lots']['X|BTCUSDT|LONG|1'] = dict(lot, qty=0.0, pending=dict(kind='add', qty=0.1, px=100.0, why='pyramid_add', post={}, t=1e18))
    elif dirty == 'unconfirmed': e.state['unconfirmed_entries'] = {'zbU': dict(cid='zbU', sym=SYM, side='LONG', qty=0.5)}
    elif dirty == 'grid': e.state['grids'] = {'G|BTCUSDT': dict(sym=SYM, op=dict(kind='add', side='LONG'))}
    assert e._confirm_by_position(lot, -0.5) == (0.5 if dirty == 'clean' else None)


def test_r3_a_one_step_close_is_not_confirmed_by_the_tolerance():
    e, _ = mk_engine(); lot = _open(e, {}); k = next(iter(e.state['lots'])); q0 = lot['qty']
    assert e._confirm_by_position(lot, -0.001) is None       # position unchanged: 1 step < tolerance (2 steps), refused
    e.trade.pos[(SYM, 'LONG')] = round(q0 - 0.001, 9)
    assert e._confirm_by_position(lot, -0.001) == 0.001
    e.trade.pos[(SYM, 'LONG')] = q0                          # same rule in the pending machinery
    lot['pending'] = dict(kind='close', qty=0.001, px=100.0, why='take_profit_ladder', post={}, t=time.time())
    e.reconcile(e.equity())
    assert lot.get('pending') and lot['qty'] == q0           # not booked (and not 'never filled' before 20 s)


def test_r4_partial_exit_signal_close_is_retried_on_the_next_pass():
    """D4: a PartialFill on an exit_signal / time_exit / flatten close waited for the next candle."""
    e, _ = mk_engine(); lot = _open(e, {}); k = next(iter(e.state['lots']))
    script(e, 'close', 'x0.5', 'EXPIRED')
    with pytest.raises(E.PartialFill): e.close_lot(k, 'exit_signal', 100.0)
    assert lot['close_retry'] == 'exit_signal' and lot['close_retry_n'] == 1; protected(e, lot)
    e.manage(e.trade.marks())
    assert k not in e.state['lots'] and not e.trade.pos.get((SYM, 'LONG')) and e.history[-1]['exit_reason'] == 'exit_signal'


def test_r4_close_retries_are_bounded_and_alerted():
    e, _ = mk_engine(); lot = _open(e, {}); k = next(iter(e.state['lots'])); sent = []
    e.notify = lambda t: sent.append(t)
    script(e, 'close', 0.0, 'EXPIRED', once=False)
    with pytest.raises(E.UnfilledOrder): e.close_lot(k, 'flatten', 100.0)
    for _ in range(E.CLOSE_RETRY_MAX + 3): e.manage(e.trade.marks())
    assert k in e.state['lots'] and 'close_retry' not in lot and any('closing failed' in t for t in sent)
    assert e.trade.calls.count('close') == E.CLOSE_RETRY_MAX + 1; protected(e, lot)


def test_qty_num_edge_cases():
    assert [E._qty_num(x) for x in ('nan', 'NaN', 'inf', '-inf', '-1', '-0.001', True, None, '', 'x', [])] == [None] * 11
    assert E._qty_num('1e-3') == 0.001 and E._qty_num('0') == 0.0 and E._qty_num('0.500') == 0.5 and E._qty_num(2) == 2.0


def test_partial_entry_below_the_minimum_is_still_booked_and_protected():
    """A partial below min_qty / min_notional exists on Binance, so it is booked and gets its stop like any lot (if the
    exchange refuses that stop, the existing stop-failed path closes it / keeps retrying with an alert)."""
    e, _ = mk_engine()
    script(e, 'open', 0.001, 'EXPIRED')                       # 0.001 BTC @ 100 = 0.1 USDT < 5 USDT min notional
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity())
    lot = lot1(e); assert lot['qty'] == 0.001; protected(e, lot)


# ------------------------------------------------------------------ FBL-ENG02 r3 (second adversarial review): regressions
def _u(e, sent): return e.state['unconfirmed_entries'][sent['cid']]


def _normal_open(e):
    fx = e.trade; fx.open = lambda s, ps, q: type(fx).open(fx, s, ps, q)


def _two_sleeves(e):
    slA = dict(SL, id='A', mgmt={}); slB = dict(SL, id='B', mgmt={}); e.S['SLEEVES'] = [slA, slB]
    return slA, slB


def test_x1_a_siblings_exit_never_closes_the_unconfirmed_entry(monkeypatch):
    record = dict(status='NEW', executedQty='0')
    e, sent, _ = _new_entry_engine(monkeypatch, record); fx = e.trade; slA, slB = _two_sleeves(e)
    real = fx.open; _normal_open(e)
    assert e.open_lot(slA, SYM, 'LONG', SG, None, e.equity()); kA = next(iter(e.state['lots']))
    fx.open = real
    assert e.open_lot(slB, SYM, 'LONG', SG, None, e.equity()) is False
    u = _u(e, sent); e.manage(fx.marks()); assert u['prov']
    script(e, 'close', 'all', 'FILLED')
    e.close_lot(kA, 'exit_signal', 100.0)
    assert abs(fx.pos[(SYM, 'LONG')] - u['qty']) < 1e-9                       # only A's own quantity was closed
    record.update(status='FILLED', executedQty=f"{u['qty']:.3f}", avgPrice='100.0')
    e.manage(fx.marks())
    lot = lot1(e); assert lot['sleeve'] == 'B'; protected(e, lot)


def test_x2_the_provisional_stop_is_persisted_and_a_restart_places_no_second_one(monkeypatch):
    import json
    record = dict(status='NEW', executedQty='0')
    e, sent, _ = _new_entry_engine(monkeypatch, record); fx = e.trade
    real_pos = fx.positions; lag = {'n': 1}
    def positions(*a, **k):
        p = real_pos()
        if lag['n'] > 0: lag['n'] -= 1; p.pop((SYM, 'LONG'), None)
        return p
    fx.positions = positions
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False
    e.manage(fx.marks()); u = _u(e, sent); assert u['prov']
    with open(e.F['state'], encoding='utf-8') as f: disk = json.load(f)
    assert disk['unconfirmed_entries'][sent['cid']]['prov'] == u['prov']
    u2 = dict(u, cid='zbP', prov=None, prov_qty=0.0); e.state['unconfirmed_entries']['zbP'] = u2
    e.state['unconfirmed_entries'].pop(sent['cid'])
    assert e._protect_unconfirmed(u2, u2['qty'])                               # placed -> on disk at once (a crash right
    with open(e.F['state'], encoding='utf-8') as f:                            # after it cannot lose the tag)
        assert json.load(f)['unconfirmed_entries']['zbP']['prov'] == u2['prov']
    e.trade.cancel(SYM, u2['prov']); del e.state['unconfirmed_entries']['zbP']; e.state['unconfirmed_entries'][sent['cid']] = u
    e.save_state()
    e.state['unconfirmed_entries'] = disk['unconfirmed_entries']              # restart from what is on disk
    e.manage(fx.marks()); assert len(fx.stops) == 1
    record.update(status='FILLED', executedQty=f"{u['qty']:.3f}", avgPrice='100.0'); e.manage(fx.marks())
    k = next(iter(e.state['lots'])); e.close_lot(k, 'exit_signal', 100.0)
    assert not fx.stops


def test_x3_flatten_closes_an_unconfirmed_entry(monkeypatch):
    record = dict(status='NEW', executedQty='0')
    e, sent, _ = _new_entry_engine(monkeypatch, record); fx = e.trade
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False
    u = _u(e, sent); assert u['prov']
    res = e.flatten()
    assert not fx.pos.get((SYM, 'LONG')) and not fx.stops and not e.state['unconfirmed_entries'] and not res['failed']
    assert res['closed'] == [f"UNCONF|{SYM}|LONG|{sent['cid']}"] and e.history[-1]['exit_reason'] == 'flatten'
    record.update(status='FILLED', executedQty=f"{u['qty']:.3f}", avgPrice='100.0'); e.manage(fx.marks())
    assert not e.state['lots'] and not fx.pos.get((SYM, 'LONG'))


def test_x3_flatten_of_an_invisible_unconfirmed_entry_closes_it_when_confirmed(monkeypatch):
    record = dict(status='NEW', executedQty='0')
    e, sent, _ = _new_entry_engine(monkeypatch, record); fx = e.trade
    real_pos = fx.positions; hide = {'on': True}
    fx.positions = lambda *a, **k: {k_: v for k_, v in real_pos().items() if not hide['on']}
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False
    res = e.flatten()
    assert res['failed'] and 'not confirmed' in res['failed'][0][1] and _u(e, sent)['close_on_book'] == 'flatten'
    hide['on'] = False; record.update(status='FILLED', executedQty=f"{_u(e, sent)['qty']:.3f}", avgPrice='100.0')
    e.manage(fx.marks())
    assert not e.state['lots'] and not fx.pos.get((SYM, 'LONG')) and not fx.stops and e.history[-1]['exit_reason'] == 'flatten'


@pytest.mark.parametrize('path', ['record', 'give_up'])
def test_x4_a_lot_whose_own_stop_fails_keeps_the_provisional_stop(monkeypatch, path):
    record = dict(status='NEW', executedQty='0')
    e, sent, _ = _new_entry_engine(monkeypatch, record); fx = e.trade
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False
    u = _u(e, sent); prov = u['prov']; assert prov
    if path == 'record': record.update(status='FILLED', executedQty=f"{u['qty']:.3f}", avgPrice='100.0')
    else: u['t'] -= E.UNCONF_GIVEUP_S + 1
    fx.fail.update({'stop', 'close'})
    e.manage(fx.marks())
    lot = lot1(e); assert lot['stop_id'] == prov and prov in fx.stops and lot['stop_dirty']
    fx.fail.clear(); e.manage(fx.marks())                                      # manage resizes / replaces it
    protected(e, lot)


def test_x5_a_fired_provisional_stop_is_recorded_not_booked_as_a_ghost(monkeypatch):
    record = dict(status='NEW', executedQty='0')
    e, sent, _ = _new_entry_engine(monkeypatch, record); fx = e.trade; slA, slB = _two_sleeves(e)
    real = fx.open; _normal_open(e)
    assert e.open_lot(slA, SYM, 'LONG', SG, None, e.equity()); A = lot1(e); qA = A['qty']
    fx.open = real
    assert e.open_lot(slB, SYM, 'LONG', SG, None, e.equity()) is False
    u = _u(e, sent); e.manage(fx.marks()); prov = u['prov']; assert prov
    s_ = fx.stops.pop(prov); fx.pos[(SYM, 'LONG')] -= s_[2]                    # the provisional stop fires on Binance
    e.manage(fx.marks()); assert u['prov_fired']
    record.update(status='FILLED', executedQty=f"{u['qty']:.3f}", avgPrice='100.0')
    for _ in range(3):
        e.manage(fx.marks())
        for l in e.state['lots'].values(): l['last_order_t'] = 0
    assert [l['sleeve'] for l in e.state['lots'].values()] == ['A'] and A['qty'] == qA; protected(e, A)
    h = e.history[-1]; assert h['sleeve'] == 'B' and h['exit_reason'] == 'stop' and h['exit'] == s_[3]


def test_x5_a_provisional_stop_cancelled_by_hand_is_placed_again(monkeypatch):
    record = dict(status='NEW', executedQty='0')
    e, sent, _ = _new_entry_engine(monkeypatch, record); fx = e.trade
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False
    u = _u(e, sent); prov = u['prov']; fx.stops.pop(prov)                       # someone cancels it on Binance
    e.manage(fx.marks())
    assert u['prov'] and u['prov'] != prov and fx.stops[u['prov']][2] == u['qty'] and not u.get('prov_fired')


def test_x6_once_retrying_every_close_failure_counts_and_the_stop_is_still_repaired():
    e, _ = mk_engine(); fx = e.trade
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()); k = next(iter(e.state['lots'])); lot = e.state['lots'][k]
    script(e, 'close', 'x0.5', 'EXPIRED')
    with pytest.raises(E.PartialFill): e.close_lot(k, 'exit_signal', 100.0)
    fx.fail.add('close'); n0 = fx.calls.count('close')
    lot['stop_dirty'] = True                                                   # e.g. a size change left the stop outdated
    e.manage(fx.marks())
    assert not lot['stop_dirty']                                               # repaired although the close raised after it
    for _ in range(E.CLOSE_RETRY_MAX + 5): e.manage(fx.marks())
    assert fx.calls.count('close') - n0 <= E.CLOSE_RETRY_MAX and 'close_retry' not in lot; protected(e, lot)


def test_r3_the_60s_cancel_is_retried_until_binance_accepts_it(monkeypatch):
    record = dict(status='NEW', executedQty='0')
    e, sent, _ = _new_entry_engine(monkeypatch, record); fx = e.trade
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False
    u = _u(e, sent); u['t'] -= E.UNCONF_CANCEL_S + 1
    tries = []; ok_cancel = fx.cancel
    def cancel(s, tag):
        if tag.startswith('c:'):
            tries.append(tag)
            if len(tries) < 3: raise BC.BinanceError(-1001, 'busy')
        return ok_cancel(s, tag)
    fx.cancel = cancel
    for _ in range(4): e.manage(fx.marks())
    assert len(tries) == 3 and u['cancelled'] and record.get('cancelled')
    import json
    with open(e.F['state'], encoding='utf-8') as f:                            # the settle pass persisted what it changed
        assert json.load(f)['unconfirmed_entries'][sent['cid']]['cancelled'] is True


def test_r3_an_unconfirmed_entry_without_rules_never_breaks_reconcile(monkeypatch):
    record = dict(status='NEW', executedQty='0')
    e, sent, _ = _new_entry_engine(monkeypatch, record)
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False
    e.state['unconfirmed_entries']['zbBAD'] = dict(cid='zbBAD', sym='XYZUSDT', side='LONG', qty=1.0, plan={}, t=time.time())
    e.manage(e.trade.marks())
    assert 'zbBAD' in e.state['unconfirmed_entries'] and any('no trading rules for XYZUSDT' in x[1] for x in e.health['errors'])
    real = e._order_record
    def boom(sym, cid):
        if cid == 'zbBOOM': raise RuntimeError('malformed record')
        return real(sym, cid)
    e._order_record = boom
    e.state['unconfirmed_entries'] = dict(zbBOOM=dict(cid='zbBOOM', sym='ETHUSDT', side='LONG', qty=1.0, plan={}, t=time.time()),
                                          **e.state['unconfirmed_entries'])      # the failing entry is settled FIRST
    record.update(status='FILLED', executedQty=f"{_u(e, sent)['qty']:.3f}", avgPrice='100.0')
    e.manage(e.trade.marks()); protected(e, lot1(e))                           # the good entry still settled
    assert 'zbBOOM' in e.state['unconfirmed_entries'] and any('malformed record' in x[1] for x in e.health['errors'])


def test_r3_status_health_shows_unconfirmed_entries(monkeypatch):
    import types, app as A
    record = dict(status='NEW', executedQty='0')
    e, sent, _ = _new_entry_engine(monkeypatch, record)
    assert e.open_lot(SL, SYM, 'LONG', SG, None, e.equity()) is False
    h = A.App.health(types.SimpleNamespace(loop_ok=time.time()), e, [])
    assert h['unconfirmed'] == [dict(symbol=SYM, side='LONG', qty=_u(e, sent)['qty'], protected=True, age_s=0, close_on_book=None)]
