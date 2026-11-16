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
from test_resting_tp import mk, open_, verify, sl_, ON                              # noqa: E402


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


def test_shortfall_while_the_stop_is_listed_is_never_credited_to_a_known_tp():
    """r3 (Codex #368 case 1; stricter than r1, which credited it): an unrelated reduction can match."""
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    q = lot['qty']; tag, cid = _vanish(e, lot)
    e.trade.pos[('BTCUSDT', 'LONG')] -= q / 2                          # an unrelated 50% reduction
    n_tp = e.trade.calls.count('tp')
    assert e._tp_settle(k, e.trade.open_stop_tags('BTCUSDT'), short=q / 2) == 'held'
    assert k in e.state['lots'] and lot['tp_id'] == tag and lot['tp_cid'] == cid and e.trade.calls.count('tp') == n_tp
    assert not e.history and lot['tp_held'] and cid in e.state['tp_quarantine']


def test_shortfall_is_still_the_tp_fill_when_the_tp_cannot_be_asked_about(monkeypatch):
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    q = lot['qty']; _vanish(e, lot)
    monkeypatch.delattr(type(e.trade), 'get_order')
    e.trade.pos[('BTCUSDT', 'LONG')] -= q
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


def test_r3_a_held_tp_stays_owned_after_its_lot_closes_until_its_record_settles_it():
    """Codex #368 case 2: a cancel answer / a negative lookup is not proof - a new position must not meet an old reducer."""
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    tag, cid = _vanish(e, lot)
    verify(e, later=3600)
    assert e.health['incidents'][f'tp-unknown|{k}']['open'] and cid in e.state['tp_quarantine']
    e.close_lot(k, 'exit_signal', e.trade.marks()['BTCUSDT'])            # the fake cancel answers True (normalized)
    assert k not in e.state['lots']
    for _ in range(2): e.manage(e.trade.marks())
    assert cid in e.state['tp_quarantine'] and e.health['incidents'][f'tp-unknown|{k}']['open']
    blk = e.entry_block(sl_(**ON), 'BTCUSDT', 'LONG', manual=True)
    assert blk and 'take-profit' in blk                                    # no new risk on that coin
    assert e.grids._can_add(dict(sym='BTCUSDT'), 'LONG') is False
    assert e.entry_block(sl_(**ON), 'ETHUSDT', 'LONG', manual=True) is None or 'take-profit' not in e.entry_block(sl_(**ON), 'ETHUSDT', 'LONG', manual=True)
    e.trade.recs[cid] = dict(orderId=9, status='CANCELED', executedQty='0', avgPrice='0', tag=tag)   # its record: ended
    e.manage(e.trade.marks())
    assert cid not in e.state['tp_quarantine'] and not e.health['incidents'][f'tp-unknown|{k}']['open']
    assert e.entry_block(sl_(**ON), 'BTCUSDT', 'LONG', manual=True) is None or 'take-profit' not in (e.entry_block(sl_(**ON), 'BTCUSDT', 'LONG', manual=True) or '')


def test_r3_a_live_held_tp_of_a_closed_lot_is_cancelled_and_released_only_on_its_record():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    tag, cid = lot['tp_id'], lot['tp_cid']
    row = e.trade.tps.pop(tag)                                             # unlisted, record NEW (hidden but live)
    rec = e.trade.recs.pop(cid)
    verify(e, later=3600)
    e.trade.recs[cid] = rec; e.trade.tps[tag] = row
    e.trade.cancel = lambda s, t: True                                    # cancel normalized to True, order still live
    e.close_lot(k, 'exit_signal', e.trade.marks()['BTCUSDT'])
    e.manage(e.trade.marks())
    assert cid in e.state['tp_quarantine'], 'released on a cancel answer while the record still says NEW'
    rec['status'] = 'CANCELED'
    e.manage(e.trade.marks())
    assert cid not in e.state['tp_quarantine']


def test_r3_an_executed_held_tp_of_a_closed_lot_is_reported():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    tag, cid = _vanish(e, lot)
    verify(e, later=3600)
    e.close_lot(k, 'exit_signal', e.trade.marks()['BTCUSDT'])
    e.trade.recs[cid] = dict(orderId=9, status='FILLED', executedQty='1', avgPrice='120', tag=tag)
    pushed = []; e.notify = lambda m: pushed.append(m)
    e.manage(e.trade.marks())
    assert cid not in e.state['tp_quarantine'] and e.health['incidents'][f'tp-ran|{cid}']['open']
    assert any('executed' in m for m in pushed)


def test_r3_a_final_record_without_executed_qty_is_unknown_never_replaced():
    """Codex #368 case 3: missing execution evidence is not 'nothing executed'."""
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    tag, cid = lot['tp_id'], lot['tp_cid']
    e.trade.tps.pop(tag)
    e.trade.recs[cid] = dict(orderId=9, status='EXPIRED', avgPrice='0', tag=tag)          # no executedQty
    n_tp = e.trade.calls.count('tp')
    verify(e, later=3600)
    assert lot['tp_id'] == tag and lot['tp_cid'] == cid and e.trade.calls.count('tp') == n_tp
    assert e._tp_record(lot)['state'] == 'unknown'


def test_r3_an_unlisted_algo_tp_with_an_intact_position_is_held_not_replaced():
    """Codex #368 case 4: the hold covers the algo route (a:/ac: + clientAlgoId) too."""
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    old = lot['tp_id']; e.trade.tps.pop(old)
    lot['tp_id'] = 'a:4242'                                               # placed through the algo fallback
    n_tp = e.trade.calls.count('tp')
    verify(e, later=3600)
    assert lot['tp_id'] == 'a:4242' and lot['tp_acid'] and e.trade.calls.count('tp') == n_tp
    assert lot.get('tp_held') and lot['tp_acid'] in e.state['tp_quarantine']


def test_r3_the_verifier_reports_a_new_hold_as_a_change_and_it_is_durable():
    """Codex #368 case 5: the ownership transition is saved now, not by some later unrelated save."""
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    _vanish(e, lot)
    e.clock = lambda: time.time() + 3600
    tags = e.trade.open_stop_tags('BTCUSDT'); rows = e.trade.open_stop_orders('BTCUSDT')
    assert e._verify_tps('BTCUSDT', rows, tags) is True
    import json
    doc = json.load(open(e.F['state'], encoding='utf-8'))
    assert doc['lots'][k].get('tp_held') and lot['tp_cid'] in doc.get('tp_quarantine', {})


def test_r2_a_not_found_tp_alert_says_not_found_not_unreadable():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    _vanish(e, lot)
    for later in (3600, 7200): verify(e, later=later)
    msg = e.health['incidents'][f'tp-unknown|{k}']['msg']
    assert 'not found' in msg and 'unreadable' not in msg, msg
