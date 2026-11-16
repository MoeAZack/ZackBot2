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
import engine as E                                                                  # noqa: E402


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


# ---- r4: Codex #368 round 5 (c6929058519) ----
CID = 'zt0123456789abcdef012345'


@pytest.mark.parametrize('o', [
    dict(status='FILLED', executedQty='NaN'), dict(status='FILLED', executedQty='Infinity'),
    dict(status='FILLED', executedQty='-1'), dict(status='FILLED', executedQty=True), dict(status='CANCELED', executedQty=False),
    dict(status='FILLED', executedQty='1.5'),                                   # more than placed
    dict(executedQty='0'), dict(status='', executedQty='0'), dict(status='WEIRD', executedQty='0'),
    dict(status='CANCELED', executedQty='0', clientOrderId='zt_someone_else'),  # echoed id differs
    dict(status='FILLED'), dict(status='FILLED', executedQty=''), dict(status='FILLED', executedQty=[1]), 'FILLED'])
def test_r4_the_one_parser_never_settles_on_invalid_evidence(o):
    assert E.Engine._tp_parse(o, CID, 1.0)['state'] == 'unknown', o


def test_r4_the_one_parser_accepts_only_complete_evidence():
    assert E.Engine._tp_parse(None, CID, 1.0)['state'] == 'notfound'
    ok = E.Engine._tp_parse(dict(status='FILLED', executedQty='1', clientOrderId=CID), CID, 1.0)
    assert ok == dict(state='ok', exec=1.0, final=True, status='FILLED')
    assert E.Engine._tp_parse(dict(status='NEW', executedQty='0'), CID, 1.0)['final'] is False
    assert E.Engine._tp_parse(dict(status='PARTIALLY_FILLED', executedQty=0.5), CID, 1.0)['exec'] == 0.5


def _closed_with_held_tp(e):
    k = open_(e); lot = e.state['lots'][k]
    tag, cid = _vanish(e, lot)
    verify(e, later=3600)
    e.close_lot(k, 'exit_signal', e.trade.marks()['BTCUSDT'])
    assert cid in e.state['tp_quarantine']
    return k, tag, cid


@pytest.mark.parametrize('rec', [dict(status='FILLED', executedQty='NaN'), dict(status='FILLED', executedQty='inf'),
                                 dict(status='CANCELED', executedQty='-0.5'), dict(status='CANCELED', executedQty=True),
                                 dict(status='CANCELED', executedQty='0', clientOrderId='zt_someone_else'),
                                 dict(executedQty='0')])
def test_r4_the_closed_lot_sweep_never_releases_on_invalid_evidence(rec):
    e, _ = mk(); k, tag, cid = _closed_with_held_tp(e)
    e.trade.recs[cid] = dict(rec, orderId=9, tag=tag)
    for _ in range(2): e.manage(e.trade.marks())
    assert cid in e.state['tp_quarantine'] and e.health['incidents'][f'tp-unknown|{k}']['open']


def test_r4_a_live_lot_hold_is_not_released_by_a_record_without_status():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    tag, cid = _vanish(e, lot)
    verify(e, later=3600)
    assert lot['tp_held']
    e.trade.recs[cid] = dict(orderId=9, executedQty='0', avgPrice='0', tag=tag)            # no status
    verify(e, later=3700)
    assert lot['tp_held'] and cid in e.state['tp_quarantine']


def test_r4_relisting_books_an_unobserved_partial_before_the_hold_ends():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    tag, cid = lot['tp_id'], lot['tp_cid']
    row, rec = e.trade.tps.pop(tag), e.trade.recs.pop(cid)
    verify(e, later=3600)
    assert lot['tp_held']
    e.trade.tps[tag], e.trade.recs[cid] = row, rec                                       # listed again ...
    q = lot['qty']; e.trade.fill_tp(tag, qty=q / 2, final=False)                          # ... after half executed unseen
    verify(e, later=3700)
    assert lot['qty'] == pytest.approx(q / 2), 'the hold ended on existence alone (lot 1.0, exchange 0.5)'
    assert lot.get('tp_done') == pytest.approx(q / 2) and not lot.get('tp_held') and cid not in e.state['tp_quarantine']
    assert e.trade.pos[('BTCUSDT', 'LONG')] == pytest.approx(lot['qty'])


def test_r4_relisted_with_an_unreadable_or_missing_record_stays_held():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    tag, cid = lot['tp_id'], lot['tp_cid']
    row = e.trade.tps.pop(tag); e.trade.recs.pop(cid)
    verify(e, later=3600)
    e.trade.tps[tag] = row                                                               # listed, record still not found
    n_tp = e.trade.calls.count('tp')
    verify(e, later=3700)
    assert lot['tp_held'] and cid in e.state['tp_quarantine'] and e.trade.calls.count('tp') == n_tp


def test_r4_a_staged_grid_send_rechecks_the_fences():
    import test_grid as TG
    e, gm, _ = TG.mk()
    with e.lock: gm.start('G1', 'BTCUSDT')
    g = TG.grid_of(e)
    gm._order_ok(g, 0.001, 100.0)                                                        # no fence: allowed
    e.state['tp_quarantine'] = {CID: dict(symbol='BTCUSDT', side='LONG', tag='o:77', cid=CID, acid=None,
                                          lot='gone', t=1.0, qty=1.0)}
    with pytest.raises(ValueError, match='take-profit'): gm._order_ok(g, 0.001, 100.0)
    e.state['tp_quarantine'] = {}
    e.new_risk_fence = lambda s: 'a stop on this coin is missing'
    with pytest.raises(ValueError, match='stop on this coin is missing'): gm._order_ok(g, 0.001, 100.0)


def _algo_quarantine(e, monkeypatch, status, child):
    acid = 'zq0123456789abcdef012345'
    e.state['tp_quarantine'] = {acid: dict(symbol='BTCUSDT', side='LONG', tag='a:4242', cid=None, acid=acid, lot='gone',
                                           t=1.0, qty=1.0)}
    monkeypatch.setattr(e.trade, 'stop_status', lambda s, t: status, raising=False)
    monkeypatch.setattr(e.trade, 'stop_fill', lambda s, t: child, raising=False)
    return acid


@pytest.mark.parametrize('child', [None, dict(status='NEW', executedQty='0'), dict(status='FILLED', executedQty='NaN'),
                                   dict(status='FILLED', executedQty='2')])
@pytest.mark.parametrize('status', ['TRIGGERED', 'FINISHED'])
def test_r4_a_triggered_algo_parent_without_a_valid_final_child_keeps_the_claim(monkeypatch, status, child):
    e, _ = mk(); acid = _algo_quarantine(e, monkeypatch, status, child)
    e.manage(e.trade.marks())
    assert acid in e.state['tp_quarantine']


def test_r4_a_triggered_algo_parent_with_a_final_child_releases_and_reports(monkeypatch):
    e, _ = mk(); acid = _algo_quarantine(e, monkeypatch, 'TRIGGERED', dict(status='FILLED', executedQty='1', orderId=5))
    e.manage(e.trade.marks())
    assert acid not in e.state['tp_quarantine'] and e.health['incidents'][f'tp-ran|{acid}']['open']


def test_r4_schema_rejects_a_quarantine_key_that_is_not_its_own_id():
    good = dict(symbol='BTCUSDT', side='LONG', tag='o:77', cid=CID, acid=None, lot='x', t=1.0, qty=1.0)
    doc = dict(schema_version=E.STATE_SCHEMA, tp_quarantine={CID: good})
    E.validate_state(doc)
    with pytest.raises(E.SchemaError): E.validate_state(dict(doc, tp_quarantine={'zt9999999999999999999999': good}))
    with pytest.raises(E.SchemaError): E.validate_state(dict(doc, tp_quarantine={CID: dict(good, qty=None)}))
    algo = dict(good, tag='a:1', cid=CID, acid='zq0123456789abcdef012345')
    E.validate_state(dict(doc, tp_quarantine={algo['acid']: algo}))
    with pytest.raises(E.SchemaError): E.validate_state(dict(doc, tp_quarantine={CID: algo}))


def test_r4_a_live_lot_with_a_quarantine_claim_but_no_flag_is_treated_as_held():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]
    tag, cid = _vanish(e, lot)
    verify(e, later=3600)
    lot.pop('tp_held')                                                                   # the contradiction
    n_tp = e.trade.calls.count('tp')
    lot['qty'] = round(lot['qty'] / 2, 6); lot['stop_dirty'] = True
    assert e._replace_stop(lot)
    assert e.trade.calls.count('tp') == n_tp and lot['tp_id'] == tag                    # never replaced
    e.manage(e.trade.marks())
    assert lot.get('tp_held')                                                            # migrated back to held


# ---- r5: Codex #368 round 6 (c6929850537): the booking and the end of the hold are one durable write ----
def _restart(tmp, fake):
    E.Futures = lambda *a, **k: fake
    e = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)
    e.data = e.trade
    e.S.update(dict(UNIVERSE=['BTCUSDT', 'ETHUSDT'], SYMBOLS_ON={'BTCUSDT': True, 'ETHUSDT': True}, CAPITAL_CAP=500.0))
    e.connect(); e.marks = e.trade.marks()
    return e


class _Crash(BaseException):
    pass


@pytest.mark.parametrize('offset', [True, False])
def test_r5_an_interruption_before_booking_keeps_the_hold_and_the_restart_books_the_record(offset):
    e, tmp = mk(); k = open_(e); lot = e.state['lots'][k]
    tag, cid = lot['tp_id'], lot['tp_cid']
    row, rec = e.trade.tps.pop(tag), e.trade.recs.pop(cid)
    verify(e, later=3600)
    assert lot['tp_held'] and cid in e.state['tp_quarantine']
    e.trade.tps[tag], e.trade.recs[cid] = row, rec                       # listed again after half executed unseen
    q = lot['qty']; e.trade.fill_tp(tag, qty=q / 2, final=False)
    def crash(*a, **kw): raise _Crash('process stopped just before booking')
    e._apply_close = crash
    with pytest.raises(_Crash): verify(e, later=3700)
    if offset: e.trade.pos[('BTCUSDT', 'LONG')] += q / 2                 # an offsetting same-side change: net qty matches
    e2 = _restart(tmp, e.trade)
    l2 = e2.state['lots'][k]
    assert l2.get('tp_held') and cid in e2.state['tp_quarantine'], 'the hold was released before the booking was durable'
    e2.reconcile(e2.equity()); verify(e2, later=3800)
    assert l2.get('tp_done') == pytest.approx(q / 2) and l2['qty'] == pytest.approx(q / 2)
    assert not l2.get('tp_held') and cid not in e2.state['tp_quarantine']
    import json
    doc = json.load(open(e2.F['state'], encoding='utf-8'))
    assert doc['lots'][k]['tp_done'] == pytest.approx(q / 2) and cid not in (doc.get('tp_quarantine') or {})
