"""LANE3-F1-TP (Codex #360 ruling 3, comment 6925134894): a resting take-profit with a KNOWN client id that Binance does
not list and whose lookup says 'not found' (or cannot be read while the position is intact) is not proven gone - its
identity is kept and no second TP is placed (two could close the lot twice). The stop stays; the owner is alerted once
after 5 min; the order record, a listing or a position shortfall decides. Offline fakes only."""
import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import requests
from test_resting_tp import mk, open_, verify                              # noqa: E402


def _vanish(e, lot, keep_record=False):
    """The TP is neither listed nor (unless keep_record) known to the lookup."""
    tag, cid = lot['tp_id'], lot['tp_cid']
    e.trade.tps.pop(tag)
    if not keep_record: e.trade.recs.pop(cid)
    return tag, cid


def test_codex_probe_notfound_and_unlisted_keeps_identity_and_places_nothing():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    tag, cid = _vanish(e, lot)
    n_tp = e.trade.calls.count('tp')
    pushed = []; e.notify = lambda m: pushed.append(m)
    for later in (100, 301 + 60, 3600, 86400):
        verify(e, later=later)
        assert lot['tp_id'] == tag and lot['tp_cid'] == cid, later
        if later == 100: assert pushed == [], 'alerted before the 5-minute window'
        assert e.trade.calls.count('tp') == n_tp and e.trade.tps == {}, 'a second take-profit was placed'
    assert lot['stop_id'] in e.trade.stops
    assert len(pushed) == 1 and 'held as unresolved' in pushed[0]
    assert (e.health['incidents'].get(f'tp-unknown|{k}') or {}).get('open')


def test_unreadable_record_and_intact_position_never_replaces_a_known_tp(monkeypatch):
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    tag, cid = _vanish(e, lot, keep_record=True)
    monkeypatch.setattr(e.trade, 'get_order', lambda s, c: (_ for _ in ()).throw(requests.Timeout('x')))
    n_tp = e.trade.calls.count('tp')
    verify(e, later=3600)
    assert lot['tp_id'] == tag and e.trade.calls.count('tp') == n_tp


def test_a_late_record_settles_the_held_tp_and_closes_the_alert():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    tag, cid = _vanish(e, lot)
    verify(e, later=3600)
    assert (e.health['incidents'].get(f'tp-unknown|{k}') or {}).get('open')
    e.trade.recs[cid] = dict(orderId=99, status='FILLED', executedQty=str(lot['qty']), avgPrice=str(lot['tp_px']))
    e.trade.pos[('BTCUSDT', 'LONG')] -= lot['qty']
    verify(e, later=3700)
    assert k not in e.state['lots'] and e.history[-1]['exit_reason'] == 'take_profit_resting'
    assert not e.health['incidents'][f'tp-unknown|{k}']['open']


def test_a_record_that_ended_without_a_fill_still_gets_a_new_tp():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    old = lot['tp_id']; e.trade.cancel('BTCUSDT', old)                  # the record says CANCELED: definitive
    verify(e, later=100)
    assert lot['tp_id'] != old and lot['tp_id'] in e.trade.tps


def test_shortfall_while_the_stop_is_listed_is_still_the_tp_fill():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    q = lot['qty']; _vanish(e, lot)
    e.trade.pos[('BTCUSDT', 'LONG')] -= q                              # the position shows it executed
    assert e._tp_settle(k, e.trade.open_stop_tags('BTCUSDT'), short=q) == 'filled'
    assert k not in e.state['lots']


def test_no_record_source_keeps_the_position_rule(monkeypatch):
    """A client without get_order cannot be asked about a client id: an intact position still means 'gone'."""
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    old, _c = _vanish(e, lot)
    monkeypatch.delattr(type(e.trade), 'get_order')
    verify(e, later=100)
    assert lot['tp_id'] and lot['tp_id'] != old


def test_the_record_found_again_closes_the_alert_and_keeps_the_tp():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    tag, cid = _vanish(e, lot)
    verify(e, later=3600)
    assert (e.health['incidents'].get(f'tp-unknown|{k}') or {}).get('open')
    e.trade.recs[cid] = dict(orderId=7, status='NEW', executedQty='0', avgPrice='0')   # the lookup finds it after all
    verify(e, later=3700)
    assert lot['tp_id'] == tag and not e.health['incidents'][f'tp-unknown|{k}']['open']


# ---- r2: adversarial review (held TP replaced on a stop resize; stale alert; alert text) ----
def test_r2_a_held_tp_is_never_replaced_when_the_stop_is_resized():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    tag, cid = _vanish(e, lot)
    verify(e, later=3600)
    assert lot['tp_id'] == tag and lot.get('tp_held')
    n_tp = e.trade.calls.count('tp')
    lot['qty'] = round(lot['qty'] / 2, 6); lot['stop_dirty'] = True     # size change -> _replace_stop -> _tp_sync
    assert e._replace_stop(lot)
    assert e.trade.calls.count('tp') == n_tp and lot['tp_id'] == tag and lot['tp_cid'] == cid
    assert not any(t == tag for _, t in e.state.get('orphans') or []), 'the held TP was queued for cancel'


def test_r2_a_listed_tp_is_resized_again_once_the_hold_ends():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    tag, cid = lot['tp_id'], lot['tp_cid']
    row = e.trade.tps.pop(tag); rec = e.trade.recs.pop(cid)
    verify(e, later=3600)
    assert lot.get('tp_held')
    e.trade.tps[tag] = row; e.trade.recs[cid] = rec                     # listed again: the hold ends
    verify(e, later=3700)
    assert not lot.get('tp_held') and not e.health['incidents'][f'tp-unknown|{k}']['open']
    n_tp = e.trade.calls.count('tp')
    lot['qty'] = round(lot['qty'] / 2, 6); lot['stop_dirty'] = True
    assert e._replace_stop(lot)
    assert e.trade.calls.count('tp') == n_tp + 1, 'a proven TP must still follow the lot size'


def test_r2_the_alert_closes_when_the_lot_is_closed():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    _vanish(e, lot)
    verify(e, later=3600)
    assert e.health['incidents'][f'tp-unknown|{k}']['open']
    e.close_lot(k, 'exit_signal', e.trade.marks()['BTCUSDT'])
    assert k not in e.state['lots']
    e.manage(e.trade.marks())                                              # the next pass ends the alert (outside pinned _finish)
    assert not e.health['incidents'][f'tp-unknown|{k}']['open']


def test_r2_a_not_found_tp_alert_says_not_found_not_unreadable():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    _vanish(e, lot)
    for later in (3600, 7200): verify(e, later=later)
    msg = e.health['incidents'][f'tp-unknown|{k}']['msg']
    assert 'not found' in msg and 'unreadable' not in msg, msg
