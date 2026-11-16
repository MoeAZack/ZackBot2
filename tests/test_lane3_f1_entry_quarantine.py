"""LANE3-F1-entry (Codex #360 ruling 1, comment 6925134894): a lost ENTRY answer whose known client id Binance does not
show (not found / a record that is not final / unreadable) and with no position yet stays owned and quarantined: it is
never forgotten on a timer, it keeps blocking conflicting risk on that coin/side, its provisional protection follows any
late fill, and the owner is alerted once after 5 min. The 'nosource' simulation path is unchanged. Offline fakes only."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import test_safety as TS
from test_safety import mk_engine, ambiguous_once, SL, SG, opened
E = TS.E


def _lost_entry(e, fill=False):
    ambiguous_once(e, 'open', fill=fill)
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity()) and e.last_skip == 'entry order unconfirmed'
    (uk, u), = e.state['unconfirmed_entries'].items()
    return uk, u


def _age(e, uk, s):
    e.state['unconfirmed_entries'][uk]['t'] = E.time.time() - s


def _unreadable(s, cid): raise TimeoutError('order lookup timed out')


LOOKUPS = {'notfound': lambda s, cid: None,
           'pending': lambda s, cid: {'status': 'NEW', 'executedQty': '0', 'avgPrice': '0', 'clientOrderId': cid},
           'unreadable': _unreadable}


@pytest.mark.parametrize('state', sorted(LOOKUPS))
def test_codex_probe_flat_entry_is_held_at_every_age_and_alerted_once(state):
    e, _ = mk_engine(); uk, u = _lost_entry(e)
    e.trade.get_order = LOOKUPS[state]
    pushed = []; e.notify = lambda m: pushed.append(m)
    for age in (21, 299, 301, 3600, 86400):
        _age(e, uk, age); e.manage(e.trade.marks())
        assert uk in e.state['unconfirmed_entries'] and e.state['lots'] == {}, (state, age)
    assert len(pushed) == 1 and 'held as unresolved' in pushed[0], pushed
    assert (e.health['incidents'].get(f'entry-unknown|{uk}') or {}).get('open')


def test_held_entry_blocks_entries_adds_and_grid_on_that_coin_side_only():
    e, _ = mk_engine()
    k = opened(e); lot = e.state['lots'][k]                                  # a sibling lot on BTC LONG
    e.S['SLEEVES'] = [dict(SL, id='T2')]
    ambiguous_once(e, 'open', fill=False)
    assert not e.open_lot(dict(SL, id='T2'), 'BTCUSDT', 'LONG', SG, None, e.equity())
    (uk, u), = e.state['unconfirmed_entries'].items()
    e.trade.get_order = LOOKUPS['notfound']; _age(e, uk, 3600); e.manage(e.trade.marks())
    assert uk in e.state['unconfirmed_entries']
    assert 'not confirmed' in (e.entry_block(SL, 'BTCUSDT', 'LONG', manual=True) or '')
    assert 'unconfirmed entry' in (e._add_block(lot, lot['qty'] / 2, 100.0) or '')
    assert e.entry_block(SL, 'BTCUSDT', 'SHORT', manual=True) is None or 'not confirmed' not in e.entry_block(SL, 'BTCUSDT', 'SHORT', manual=True)
    g = dict(sym='BTCUSDT')
    assert e.grids._can_add(g, 'LONG') is False


def test_a_late_fill_is_protected_and_adopted_and_the_alert_closes():
    e, _ = mk_engine(); uk, u = _lost_entry(e)
    e.trade.get_order = LOOKUPS['notfound']
    _age(e, uk, 3600); e.manage(e.trade.marks())
    assert (e.health['incidents'].get(f'entry-unknown|{uk}') or {}).get('open')
    q = u['qty']; e.trade.pos[('BTCUSDT', 'LONG')] = q                      # the delayed request executed after all
    e.manage(e.trade.marks())                                                 # past 5 min with a position: adopted
    (k, lot), = e.state['lots'].items()
    assert lot['qty'] == pytest.approx(q) and e.state['unconfirmed_entries'] == {}
    assert lot['stop_id'] in e.trade.stops and len(e.trade.stops) == 1        # protected, exactly one stop
    assert not e.health['incidents'][f'entry-unknown|{uk}']['open']


def test_nosource_simulation_path_is_unchanged():
    e, _ = mk_engine(); uk, u = _lost_entry(e)                              # FakeX has no get_order: 'nosource'
    _age(e, uk, E.UNCONF_DROP_S + 1); e.manage(e.trade.marks())
    assert e.state['unconfirmed_entries'] == {} and e.state['lots'] == {}


def test_an_entry_without_a_client_id_keeps_the_old_rule():
    e, _ = mk_engine(); uk, u = _lost_entry(e)
    u['cid'] = None
    e.trade.get_order = LOOKUPS['notfound']
    _age(e, uk, E.UNCONF_DROP_S + 1); e.manage(e.trade.marks())
    assert e.state['unconfirmed_entries'] == {}
