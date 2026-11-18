"""LANE3-F1-trim (Codex #360 ruling 2, comment 6925134894): a post-fill risk trim written ahead as 'sent' whose known
client id Binance does not show (not found / not final / unreadable) is never re-sent under a new id - a crash can fall
between the durable 'sent' write and lot['pending']. It stays owned, adds stay blocked, the stop and every exit keep
running, the owner is alerted once after 5 min, and only the final record or the lot's own booking settles it."""
import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
from test_risk_fill01 import eng, opened, measure, adverse                # noqa: E402
from test_safety import SL, SG                                            # noqa: E402

LOOKUP = {'notfound': lambda s, cid: None,
          'pending': lambda s, cid: dict(status='NEW', executedQty='0', avgPrice='0'),
          'unreadable': lambda s, cid: (_ for _ in ()).throw(TimeoutError('lookup timed out'))}


def _restart_shaped(e, lot):
    """Codex's probe: state=sent, qty_before=lot qty, send_qty=trim, attempts=1, NO lot['pending']."""
    d = measure(e, lot, adverse(lot, 0.5))
    d.update(state='sent', cid='zbrfcrash', send_qty=d['trim_qty'], qty_before=lot['qty'], attempts=1)
    assert 'pending' not in lot
    return d


@pytest.mark.parametrize('state', sorted(LOOKUP))
def test_codex_probe_sent_trim_is_never_resent_under_a_new_id(state):
    e = eng(); k, lot = opened(e); q0 = lot['qty']
    d = _restart_shaped(e, lot)
    e.trade.get_order = LOOKUP[state]
    sends = []; e._market_close = lambda *a, **kw: sends.append((a, kw))
    pushed = []; e.notify = lambda m: pushed.append(m)
    for age in (0, 21, 301, 3600, 86400):
        d['sent_t'] = time.time() - age
        assert e._risk_fill_run(k, lot, e.marks['BTCUSDT']) is False, (state, age)
        assert d['state'] == 'sent' and d['attempts'] == 1 and d['cid'] == 'zbrfcrash'
    assert sends == [] and lot['qty'] == q0
    assert len(pushed) == 1 and 'held as unresolved' in pushed[0]
    assert e._add_block(lot, 1.0, 100.0) == 'risk_trim in progress'


def test_held_trim_keeps_the_stop_and_the_lot_managed_and_exits_still_close():
    e = eng(); k, lot = opened(e)
    d = _restart_shaped(e, lot); d['sent_t'] = time.time() - 3600
    e.trade.get_order = LOOKUP['notfound']
    stop = lot['stop_id']
    e.manage(e.marks)
    assert k in e.state['lots'] and lot['stop_id'] == stop and stop in e.trade.stops
    e.close_lot(k, 'exit_signal', e.marks['BTCUSDT'])
    assert k not in e.state['lots']


def test_the_final_record_settles_a_held_trim_and_closes_the_alert():
    e = eng(); k, lot = opened(e); q0 = lot['qty']
    d = _restart_shaped(e, lot); d['sent_t'] = time.time() - 3600
    e.trade.get_order = LOOKUP['notfound']
    e._risk_fill_run(k, lot, e.marks['BTCUSDT'])
    assert (e.health['incidents'].get(f'risk-trim-unknown|{k}') or {}).get('open')
    e.trade.get_order = lambda s, cid: dict(status='FILLED', executedQty=str(d['send_qty']), avgPrice=str(e.marks['BTCUSDT']))
    e.trade.pos[('BTCUSDT', 'LONG')] -= d['send_qty']
    assert e._risk_fill_run(k, lot, e.marks['BTCUSDT']) is True
    assert d['state'] == 'done' and lot['qty'] == pytest.approx(q0 - d['send_qty'])
    assert not e.health['incidents'][f'risk-trim-unknown|{k}']['open']


def test_a_record_written_before_sent_t_existed_starts_its_clock_on_first_sight():
    e = eng(); k, lot = opened(e)
    d = _restart_shaped(e, lot); d.pop('sent_t', None)
    e.trade.get_order = LOOKUP['notfound']
    pushed = []; e.notify = lambda m: pushed.append(m)
    e._risk_fill_run(k, lot, e.marks['BTCUSDT'])
    assert 'sent_t' in d and pushed == []                                    # not alerted at once


def test_the_write_ahead_records_its_send_time():
    e = eng(); k, lot = opened(e)
    d = measure(e, lot, adverse(lot, 0.5))
    e.manage(e.marks)
    assert d['state'] == 'done' and isinstance(d.get('sent_t'), float)


# ---- r2: adversarial review (an unrelated exit settled a held trim; stale alert on lot close) ----
def _held_then_unrelated_exit(e, k, lot):
    d = _restart_shaped(e, lot); d.update(sent_t=time.time() - 3600, fills_n=len(lot.get('fills') or []))
    e.trade.get_order = LOOKUP['notfound']
    e._risk_fill_run(k, lot, e.marks['BTCUSDT'])
    e._apply_close(lot, e.rules['BTCUSDT']['step'], e.marks['BTCUSDT'], 'take_profit_ladder')   # not the trim
    return d


@pytest.mark.parametrize('state', sorted(LOOKUP))
def test_r2_an_unrelated_exit_never_settles_a_held_trim(state):
    e = eng(); k, lot = opened(e)
    d = _held_then_unrelated_exit(e, k, lot)
    e.trade.get_order = LOOKUP[state]
    sends = []; e._market_close = lambda *a, **kw: sends.append((a, kw))
    assert e._risk_fill_run(k, lot, e.marks['BTCUSDT']) is False
    assert d['state'] == 'sent' and d['cid'] == 'zbrfcrash' and sends == []
    assert e._risk_fill_block(lot)
    assert e.health['incidents'][f'risk-trim-unknown|{k}']['open']


def test_r2_a_booking_tagged_as_the_trim_still_settles_it():
    e = eng(); k, lot = opened(e)
    d = _restart_shaped(e, lot); d.update(sent_t=time.time() - 3600, fills_n=len(lot.get('fills') or []))
    e.trade.get_order = LOOKUP['notfound']
    e._risk_fill_run(k, lot, e.marks['BTCUSDT'])
    e._apply_close(lot, d['send_qty'], e.marks['BTCUSDT'], 'risk_trim')    # e.g. a pending close resolved by reconcile
    assert e._risk_fill_run(k, lot, e.marks['BTCUSDT']) is True
    assert d['state'] == 'done' and not e.health['incidents'][f'risk-trim-unknown|{k}']['open']


def test_r2_after_an_unrelated_exit_the_final_record_decides():
    e = eng(); k, lot = opened(e)
    d = _held_then_unrelated_exit(e, k, lot); q = lot['qty']
    e.trade.get_order = lambda s, cid: dict(status='EXPIRED', executedQty='0', avgPrice='0')
    assert e._risk_fill_run(k, lot, e.marks['BTCUSDT']) is True
    assert d['state'] == 'due' and lot['qty'] == q                           # nothing executed: sent again next, never booked
    assert not e.health['incidents'][f'risk-trim-unknown|{k}']['open']
    e2 = eng(); k2, lot2 = opened(e2)
    d2 = _held_then_unrelated_exit(e2, k2, lot2); q2 = lot2['qty']
    e2.trade.get_order = lambda s, cid: dict(status='FILLED', executedQty=str(d2['send_qty']), avgPrice=str(e2.marks['BTCUSDT']))
    assert e2._risk_fill_run(k2, lot2, e2.marks['BTCUSDT']) is True
    assert d2['state'] == 'done' and lot2['qty'] == q2                        # settled; never booked a second time here


# ---- r3: ownership outlives the lot (Codex #367 c6927872389) ----
def _closed_with_unknown_trim(e, state='notfound'):
    k, lot = opened(e)
    d = _restart_shaped(e, lot); d['sent_t'] = time.time() - 3600
    e.trade.get_order = LOOKUP[state]
    e._risk_fill_run(k, lot, e.marks['BTCUSDT'])
    assert e.health['incidents'][f'risk-trim-unknown|{k}']['open']
    e.close_lot(k, 'exit_signal', e.marks['BTCUSDT'])                       # an independent exit closes the trade
    assert k not in e.state['lots']
    return k, d


@pytest.mark.parametrize('state', sorted(LOOKUP))
def test_codex_probe_a_closed_lot_does_not_release_its_still_unknown_trim(state):
    e = eng(); k, d = _closed_with_unknown_trim(e, state)
    seen = []; real = e._order_result
    e._order_result = lambda s, c, q: (seen.append(c), real(s, c, q))[1]
    for _ in range(3): e.manage(e.marks)
    assert 'zbrfcrash' in seen, 'the original id is no longer reconciled'
    assert e.health['incidents'][f'risk-trim-unknown|{k}']['open'], 'alert released because the parent trade closed'
    assert 'zbrfcrash' in e.state['trim_owned']
    blk = e.entry_block(SL, 'BTCUSDT', 'LONG', manual=True)
    assert blk and 'zbrfcrash' in blk, blk                                   # manual admission refused
    assert e.grids._can_add(dict(sym='BTCUSDT'), 'LONG') is False            # grid too
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity()) and not e.state['lots']


def test_r3_the_final_zero_record_ends_the_closed_trades_trim():
    e = eng(); k, d = _closed_with_unknown_trim(e)
    e.trade.get_order = lambda s, c: dict(status='EXPIRED', executedQty='0', avgPrice='0')
    e.manage(e.marks)
    assert 'zbrfcrash' not in (e.state.get('trim_owned') or {})
    assert not e.health['incidents'][f'risk-trim-unknown|{k}']['open']
    assert e.entry_block(SL, 'BTCUSDT', 'LONG', manual=True) is None
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip


def test_r3_a_late_execution_of_a_closed_trades_trim_is_alerted_never_booked():
    e = eng(); k, d = _closed_with_unknown_trim(e)
    n_hist = len(e.history); pushed = []; e.notify = lambda m: pushed.append(m)
    e.trade.get_order = lambda s, c: dict(status='FILLED', executedQty=str(d['send_qty']), avgPrice=str(e.marks['BTCUSDT']))
    e.manage(e.marks)
    assert 'zbrfcrash' not in (e.state.get('trim_owned') or {}) and len(e.history) == n_hist and not e.state['lots']
    assert (e.health['incidents'].get('risk-trim-late|zbrfcrash') or {}).get('open')
    assert any('CLOSED trade executed' in m for m in pushed)


def test_r3_a_trim_settled_on_its_live_lot_is_dropped_from_the_account_record():
    e = eng(); k, lot = opened(e)
    d = _restart_shaped(e, lot); d['sent_t'] = time.time() - 3600
    e.trade.get_order = LOOKUP['notfound']
    e.manage(e.marks)
    assert 'zbrfcrash' in e.state['trim_owned']
    assert e.entry_block(SL, 'ETHUSDT', 'LONG', manual=True) is None        # another coin is never blocked
    e.trade.get_order = lambda s, cid: dict(status='EXPIRED', executedQty='0', avgPrice='0')
    e.manage(e.marks); e.manage(e.marks)
    assert 'zbrfcrash' not in (e.state.get('trim_owned') or {})


def test_r3_the_write_ahead_owns_the_id_before_the_send():
    e = eng(); k, lot = opened(e)
    d = measure(e, lot, adverse(lot, 0.5))
    e._market_close = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('crash after the write-ahead'))
    with pytest.raises(RuntimeError): e._risk_fill_run(k, lot, e.marks['BTCUSDT'])
    o = e.state['trim_owned'][d['cid']]
    assert o['symbol'] == 'BTCUSDT' and o['side'] == 'LONG' and o['lot'] == k
    E_ = sys.modules['engine']; E_.validate_state(dict(e.state, schema_version=E_.STATE_SCHEMA))


@pytest.mark.parametrize('rec', [dict(symbol=5, side='LONG'), dict(symbol='BTCUSDT', side='BOTH'), dict(symbol='BTCUSDT'), 'x'])
def test_r3_a_malformed_ownership_record_fails_the_state_check(rec):
    E_ = sys.modules['engine']
    e = eng()
    with pytest.raises(E_.SchemaError):
        E_.validate_state(dict(e.state, schema_version=E_.STATE_SCHEMA, trim_owned={'zbrfx': rec}))


def test_r2_the_write_ahead_records_the_booking_mark():
    e = eng(); k, lot = opened(e)
    d = measure(e, lot, adverse(lot, 0.5))
    e._market_close = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('stop after write-ahead'))
    with pytest.raises(RuntimeError): e._risk_fill_run(k, lot, e.marks['BTCUSDT'])
    assert d['state'] == 'sent' and d['fills_n'] == len(lot.get('fills') or [])


# ---- r4: the fence is re-checked at the final risk-adding send (Codex #368 c6929058519 point 3, also for trim_owned) ----
def _orphan_trim(e, sym='BTCUSDT'):
    e.state['trim_owned'] = {'zbrfgone': dict(cid='zbrfgone', symbol=sym, side='LONG', lot='gone|lot', sleeve='T', send_qty=0.01,
                                              sent_t=time.time() - 3600)}
    assert e.trim_orphan_block(sym)


def test_r4_a_staged_grid_open_is_not_sent_once_a_trim_quarantine_exists():
    import test_grid as TG
    e, gm, _ = TG.mk()
    with e.lock: gm.start('G1', 'BTCUSDT')
    g = TG.grid_of(e)
    cells = [j for j, c in enumerate(g['cells']) if c['k'] == 'L'][:1]
    m = e.trade.marks()['BTCUSDT']
    op = gm._op(g, 'LONG', 'open', cells, sum(g['cells'][j]['q'] for j in cells), m)    # staged (write-ahead done)
    _orphan_trim(e)                                                                      # the fence appears after staging
    n_open = e.trade.calls.count('open')
    gm._open(g, op)
    assert e.trade.calls.count('open') == n_open and TG.lot_of(e, 'LONG') is None and not g.get('op')


def test_r4_grid_add_send_gate_refuses_under_the_fence():
    import test_grid as TG
    e, gm, _ = TG.mk()
    with e.lock: gm.start('G1', 'BTCUSDT')
    g = TG.grid_of(e); _orphan_trim(e)
    with pytest.raises(ValueError, match='zbrfgone'): gm._order_ok(g, 0.001, e.trade.marks()['BTCUSDT'])


def test_r4_an_add_send_refuses_under_the_fence_and_exits_still_close():
    e = eng(); k, lot = opened(e); q = lot['qty']
    _orphan_trim(e)
    n_open = e.trade.calls.count('open')
    with pytest.raises(RuntimeError, match='zbrfgone'): e._add_qty(lot, q / 2, e.marks['BTCUSDT'], 'pyramid_add')
    assert e.trade.calls.count('open') == n_open and lot['qty'] == q and 'pending' not in lot
    e.close_lot(k, 'exit_signal', e.marks['BTCUSDT'])                       # reducing orders / exits stay available
    assert k not in e.state['lots']


def test_r4_a_maker_resting_order_is_not_placed_under_the_fence():
    import test_v31_engine as TV
    e, bk = TV.maker_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    rec = next(iter(e.state['resting_entries'].values()))
    _orphan_trim(e)
    with pytest.raises(RuntimeError, match='zbrfgone'): e._maker_place(rec)
    assert len(bk.orders) == 1


# ---- r5 (Codex #367 c6929101714 points 1 and 2) ----
def test_r5_no_trim_is_sent_without_a_durable_write_ahead():
    e = eng(); k, lot = opened(e)
    d = measure(e, lot, adverse(lot, 0.5)); before = dict(d)
    sends = []; e._market_close = lambda *a, **kw: sends.append(a)
    real = e._save_safe
    def disk_full(name, obj, strict=False):
        if strict: raise OSError(28, 'No space left on device')
        return False
    e._save_safe = disk_full
    assert e._risk_fill_run(k, lot, e.marks['BTCUSDT']) is False
    assert sends == [], 'a trim was sent without a durable write-ahead'
    assert d == before and d['state'] == 'due'                             # rolled back: no cid, attempts unchanged
    assert not (e.state.get('trim_owned') or {})
    assert (e.health['incidents'].get(f'risk-trim-wal|{k}') or {}).get('open')
    e.close_lot(k, 'exit_signal', e.marks['BTCUSDT'])                       # exits stay available
    assert k not in e.state['lots']


def test_r5_after_the_disk_recovers_the_trim_is_sent_once():
    e = eng(); k, lot = opened(e)
    d = measure(e, lot, adverse(lot, 0.5))
    real = e._save_safe; fail = [True]
    e._save_safe = lambda name, obj, strict=False: ((_ for _ in ()).throw(OSError(28, 'full')) if strict and fail[0]
                                                     else real(name, obj, strict=strict))
    assert e._risk_fill_run(k, lot, e.marks['BTCUSDT']) is False
    fail[0] = False
    e.manage(e.marks)
    assert d['state'] == 'done' and d['attempts'] == 1 and not e.health['incidents'][f'risk-trim-wal|{k}']['open']


@pytest.mark.parametrize('rec', [
    dict(symbol='BTCUSDT', side='LONG'),                                                     # Codex's probe: no qty, no t
    dict(cid='zbrfx', symbol='BTCUSDT', side='LONG', send_qty=None, sent_t=1.0),
    dict(cid='zbrfx', symbol='BTCUSDT', side='LONG', send_qty=0, sent_t=1.0),
    dict(cid='zbrfx', symbol='BTCUSDT', side='LONG', send_qty=float('nan'), sent_t=1.0),
    dict(cid='zbrfx', symbol='BTCUSDT', side='LONG', send_qty=0.01),
    dict(cid='other', symbol='BTCUSDT', side='LONG', send_qty=0.01, sent_t=1.0)])
def test_r5_incomplete_owner_records_fail_the_state_check(rec):
    E_ = sys.modules['engine']
    e = eng()
    with pytest.raises(E_.SchemaError):
        E_.validate_state(dict(e.state, schema_version=E_.STATE_SCHEMA, trim_owned={'zbrfx': rec}))


def test_r5_a_complete_owner_record_passes_the_state_check():
    E_ = sys.modules['engine']
    e = eng()
    E_.validate_state(dict(e.state, schema_version=E_.STATE_SCHEMA, trim_owned={'zbrfx': dict(
        cid='zbrfx', symbol='BTCUSDT', side='LONG', lot='x', send_qty=0.01, sent_t=1.0)}))


@pytest.mark.parametrize('rec, other_blocked', [(dict(symbol='BTCUSDT', side='LONG'), False), (dict(side='LONG'), True), ('junk', True)])
def test_r5_an_incomplete_owner_in_memory_never_crashes_management_and_stays_a_fence(rec, other_blocked):
    e = eng()
    e.state['trim_owned'] = {'zbrfx': rec}
    e.trade.get_order = lambda s, c: dict(status='FILLED', executedQty='0.01', avgPrice='100')   # a final fill
    pushed = []; e.notify = lambda m: pushed.append(m)
    for _ in range(3): e.manage(e.marks)                                    # no TypeError
    assert 'zbrfx' in e.state['trim_owned'], 'an incomplete owner was settled'
    assert e.entry_block(SL, 'BTCUSDT', 'LONG', manual=True)
    assert bool(e.trim_orphan_block('ETHUSDT')) is other_blocked
    assert len([m for m in pushed if 'incomplete' in m]) == 1
