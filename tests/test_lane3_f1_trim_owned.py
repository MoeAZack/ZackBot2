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
