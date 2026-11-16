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


def test_r2_the_alert_closes_when_the_lot_is_closed():
    e = eng(); k, lot = opened(e)
    d = _restart_shaped(e, lot); d['sent_t'] = time.time() - 3600
    e.trade.get_order = LOOKUP['notfound']
    e._risk_fill_run(k, lot, e.marks['BTCUSDT'])
    assert e.health['incidents'][f'risk-trim-unknown|{k}']['open']
    e.close_lot(k, 'exit_signal', e.marks['BTCUSDT'])
    assert k not in e.state['lots'] and not e.health['incidents'][f'risk-trim-unknown|{k}']['open']


def test_r2_the_write_ahead_records_the_booking_mark():
    e = eng(); k, lot = opened(e)
    d = measure(e, lot, adverse(lot, 0.5))
    e._market_close = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('stop after write-ahead'))
    with pytest.raises(RuntimeError): e._risk_fill_run(k, lot, e.marks['BTCUSDT'])
    assert d['state'] == 'sent' and d['fills_n'] == len(lot.get('fills') or [])
