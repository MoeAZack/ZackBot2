"""LANE3-F2 (PR #359 red-team): a recorded stop id that is LISTED on Binance is protection only when its detailed row
actually protects the lot - a stop order that closes the lot's side, at the lot's trigger, with enough quantity (shared
rows counted once). Otherwise the lot is never shown protected and its stop is re-placed stop-first. Offline fakes only."""
import os, sys, socket, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import requests
import engine as E                                                         # noqa: E402
from test_aud04_stop_verify import mk, lot_open, tick                     # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k): raise AssertionError('network forbidden in this test')
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(requests.sessions.Session, 'request', refuse)


@pytest.fixture(autouse=True)
def restore_fake_factory(monkeypatch):
    monkeypatch.setattr(E, 'Futures', E.Futures)


def setup(tmp_path):
    e, clk, _ = mk(tmp=str(tmp_path))
    k = lot_open(e); lot = e.state['lots'][k]
    assert lot['stop_id'] in e.trade.stops and not lot.get('stop_dirty')
    return e, clk, k, lot


def edit(e, tag, qty=None, px=None):
    s, ps, q, p = e.trade.stops[tag]
    e.trade.stops[tag] = (s, ps, q if qty is None else qty, p if px is None else px)


def test_red_team_repro_half_quantity_is_not_confirmed(tmp_path):
    """Codex #359 reproducer: the exchange lists the recorded stop, but it covers half of the lot."""
    e, clk, k, lot = setup(tmp_path)
    edit(e, lot['stop_id'], qty=lot['qty'] / 2)
    lot['stop_confirmed_t'] = None
    e._verify_symbol('BTCUSDT', 'red-team')
    v = e.stop_view(lot)
    assert not v['protected'] and v['stop_state'] == 'placing'
    assert lot['stop_dirty'] and lot['stop_confirmed_t'] is None
    assert any('covers' in x[1] for x in e.health['errors'])


def test_listed_short_stop_never_refreshes_an_earlier_confirmation(tmp_path):
    """Already confirmed a moment ago: a listing that is short must not keep (or renew) 'protected'."""
    e, clk, k, lot = setup(tmp_path)
    e._verify_symbol('BTCUSDT', 'x'); assert e.stop_view(lot)['protected']
    edit(e, lot['stop_id'], qty=lot['qty'] / 2); clk[0] += 1
    e._verify_symbol('BTCUSDT', 'x')
    assert not e.stop_view(lot)['protected']


def test_short_stop_is_replaced_stop_first_then_protected(tmp_path):
    e, clk, k, lot = setup(tmp_path)
    old = lot['stop_id']; edit(e, old, qty=lot['qty'] / 2)
    e._verify_symbol('BTCUSDT', 'x')
    seq = []
    real_stop, real_cancel = e.trade.stop, e.trade.cancel
    e.trade.stop = lambda *a: (seq.append(('stop', len(e.trade.stops))), real_stop(*a))[1]
    e.trade.cancel = lambda s, t: (seq.append(('cancel', t)), real_cancel(s, t))[1]
    tick(e, clk, n=3)
    assert lot['stop_id'] != old and old not in e.trade.stops               # replaced, the short one cancelled
    assert e.trade.stops[lot['stop_id']][2] == pytest.approx(lot['qty'])     # full size now
    assert seq[0][0] == 'stop' and ('cancel', old) in seq and seq.index(('cancel', old)) > 0   # new FIRST, never 0 stops
    e._verify_symbol('BTCUSDT', 'x')
    assert e.stop_view(lot)['protected'] and not lot.get('stop_dirty')
    assert not (e.health.get('incidents', {}).get(f'stop-short|{k}') or {}).get('open')


def test_wrong_trigger_is_not_protection(tmp_path):
    e, clk, k, lot = setup(tmp_path)
    tickv = e.rules['BTCUSDT']['tick']
    edit(e, lot['stop_id'], px=lot['stop'] - 50 * tickv)
    e._verify_symbol('BTCUSDT', 'x')
    assert not e.stop_view(lot)['protected'] and lot['stop_dirty']


def test_trigger_within_half_a_tick_is_still_protection(tmp_path):
    e, clk, k, lot = setup(tmp_path)
    edit(e, lot['stop_id'], px=lot['stop'] + e.rules['BTCUSDT']['tick'] * 0.4)
    e._verify_symbol('BTCUSDT', 'x')
    assert e.stop_view(lot)['protected'] and not lot.get('stop_dirty')


def test_wrong_direction_is_not_protection(tmp_path):
    """A same-id row that BUYS a LONG adds to it - never protection."""
    e, clk, k, lot = setup(tmp_path)
    e.trade.sides[lot['stop_id']] = 'BUY'
    e._verify_symbol('BTCUSDT', 'x')
    assert not e.stop_view(lot)['protected'] and lot['stop_dirty']


def test_non_stop_type_is_not_protection(tmp_path, monkeypatch):
    e, clk, k, lot = setup(tmp_path)
    real = e.trade.open_stop_orders
    monkeypatch.setattr(e.trade, 'open_stop_orders', lambda *a, **kw: [dict(r, type='LIMIT') for r in real(*a, **kw)])
    e._verify_symbol('BTCUSDT', 'x')
    assert not e.stop_view(lot)['protected'] and lot['stop_dirty']


def test_larger_or_close_position_row_is_protection(tmp_path):
    e, clk, k, lot = setup(tmp_path)
    edit(e, lot['stop_id'], qty=lot['qty'] * 3)
    e._verify_symbol('BTCUSDT', 'x'); assert e.stop_view(lot)['protected']
    edit(e, lot['stop_id'], qty=0.0); e.trade.cp[lot['stop_id']] = True; clk[0] += 1
    e._verify_symbol('BTCUSDT', 'x'); assert e.stop_view(lot)['protected'] and not lot.get('stop_dirty')


def test_detail_less_row_still_confirms_its_id(tmp_path, monkeypatch):
    """Unchanged: a listing without details (None) cannot judge coverage - the id confirms as before."""
    e, clk, k, lot = setup(tmp_path)
    real = e.trade.open_stop_orders
    monkeypatch.setattr(e.trade, 'open_stop_orders', lambda *a, **kw: [dict(r, qty=None, stop_price=None, side=None, type=None)
                                                                        for r in real(*a, **kw)])
    e._verify_symbol('BTCUSDT', 'x')
    assert e.stop_view(lot)['protected'] and not lot.get('stop_dirty')


def test_one_row_shared_by_two_lots_is_counted_once(tmp_path):
    """Two lots recording the same stop row: its quantity covers one of them, never both."""
    e, clk, k, lot = setup(tmp_path)
    k2 = k + '|twin'; twin = dict(lot); e.state['lots'][k2] = twin
    e._verify_symbol('BTCUSDT', 'x')
    states = sorted([e.stop_view(lot)['protected'], e.stop_view(twin)['protected']])
    assert states == [False, True]
    edit(e, lot['stop_id'], qty=lot['qty'] * 2); clk[0] += 1
    for l in (lot, twin): l['stop_dirty'] = False
    e._verify_symbol('BTCUSDT', 'x')
    assert e.stop_view(lot)['protected'] and e.stop_view(twin)['protected']


def test_short_stop_blocks_new_entries_on_the_coin(tmp_path):
    e, clk, k, lot = setup(tmp_path)
    edit(e, lot['stop_id'], qty=lot['qty'] / 2)
    e._verify_symbol('BTCUSDT', 'x')
    assert any(l['symbol'] == 'BTCUSDT' and l.get('stop_dirty') for l in e.state['lots'].values())
    assert 'waiting for its stop' in (e.entry_block(None, 'BTCUSDT', 'LONG', manual=True) or '')


def test_tag_only_read_never_clears_a_short_verdict(tmp_path):
    """Adversarial review: a tag-only open-order read (T05b recovery / reconcile) cannot judge coverage - it must not
    re-confirm the lot or close the stop-short alert as 'covers the lot'."""
    e, clk, k, lot = setup(tmp_path)
    edit(e, lot['stop_id'], qty=lot['qty'] / 2)
    e._verify_symbol('BTCUSDT', 'x')
    inc = lambda: (e.health.get('incidents', {}).get(f'stop-short|{k}') or {}).get('open')
    assert lot['stop_dirty'] and lot['stop_confirmed_t'] is None and inc() and lot['stop_short']
    clk[0] += 1
    e._stops_reconfirmed()
    e._stops_seen('BTCUSDT', set(e.trade.stops))                         # the reconcile call shape
    assert not e.stop_view(lot)['protected'] and lot['stop_confirmed_t'] is None and inc()


def test_short_verdict_is_cleared_by_the_new_stop_and_saved_state_validates(tmp_path):
    e, clk, k, lot = setup(tmp_path)
    edit(e, lot['stop_id'], qty=lot['qty'] / 2)
    e._verify_symbol('BTCUSDT', 'x')
    E._validate_lot(k, lot)                                              # stop_short is a known, valid lot field
    tick(e, clk, n=3)
    assert 'stop_short' not in lot and not lot.get('stop_dirty')
    clk[0] += 1; e._stops_reconfirmed()
    assert e.stop_view(lot)['protected']
