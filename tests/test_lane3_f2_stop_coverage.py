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


@pytest.mark.parametrize('drop', [dict(qty=None), dict(stop_price=None), dict(side=None), dict(type=None),
                                  dict(qty=None, stop_price=None, side=None, type=None)])
def test_incomplete_detailed_row_is_unknown_never_protection(tmp_path, monkeypatch, drop):
    """Codex #361 r1: a detailed listing that lacks a detail the proof needs (the REST parser maps a malformed number to
    None) stays UNKNOWN - never confirmed, not even by a later tag-only read; alerted; nothing re-placed on it alone."""
    e, clk, k, lot = setup(tmp_path)
    real = e.trade.open_stop_orders
    monkeypatch.setattr(e.trade, 'open_stop_orders', lambda *a, **kw: [dict(r, **drop) for r in real(*a, **kw)])
    e._verify_symbol('BTCUSDT', 'x')
    assert not e.stop_view(lot)['protected'] and not lot.get('stop_dirty') and 'lacks' in lot['stop_short']
    assert (e.health.get('incidents', {}).get(f'stop-short|{k}') or {}).get('open')
    clk[0] += 1; e._stops_reconfirmed()
    assert not e.stop_view(lot)['protected']
    monkeypatch.setattr(e.trade, 'open_stop_orders', real)                    # a complete listing proves it again
    clk[0] += 1; e._verify_symbol('BTCUSDT', 'x')
    assert e.stop_view(lot)['protected'] and 'stop_short' not in lot


def test_position_side_is_judged_even_without_order_side(tmp_path, monkeypatch):
    """Codex #361 r1: side=None plus positionSide SHORT for a LONG lot is never protection."""
    e, clk, k, lot = setup(tmp_path)
    real = e.trade.open_stop_orders
    monkeypatch.setattr(e.trade, 'open_stop_orders', lambda *a, **kw: [dict(r, side=None, position_side='SHORT') for r in real(*a, **kw)])
    e._verify_symbol('BTCUSDT', 'x')
    assert not e.stop_view(lot)['protected'] and lot['stop_dirty']


def test_tag_only_listing_of_an_older_client_still_confirms_ids(tmp_path, monkeypatch):
    """Unchanged: a client without detailed rows (open_stop_tags only, strict=False) confirms by id as before."""
    e, clk, k, lot = setup(tmp_path)
    monkeypatch.delattr(type(e.trade), 'open_stop_orders')
    e._verify_symbol('BTCUSDT', 'x')
    assert e.stop_view(lot)['protected']


def test_repairing_one_lot_never_cancels_a_stop_a_sibling_records(tmp_path):
    """Codex #361 r1: a shared carrier stays while any sibling lot still records it."""
    e, clk, k, lot = setup(tmp_path)
    old = lot['stop_id']
    k2 = k + '|twin'; twin = dict(lot); e.state['lots'][k2] = twin
    e._verify_symbol('BTCUSDT', 'x')
    dirty = next(l for l in (lot, twin) if l.get('stop_dirty')); clean = lot if dirty is twin else twin
    tick(e, clk, n=3)
    assert dirty['stop_id'] != old and dirty['stop_id'] in e.trade.stops       # its own full stop now
    assert clean['stop_id'] == old and old in e.trade.stops, "the sibling's only stop was cancelled"
    e._finish(k2 if dirty is twin else k, 'test_close')                       # closing the repaired lot keeps the carrier
    assert old in e.trade.stops


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


def test_closing_one_lot_never_cancels_a_stop_a_sibling_records(tmp_path):
    e, clk, k, lot = setup(tmp_path)
    old = lot['stop_id']; k2 = k + '|twin'; e.state['lots'][k2] = dict(lot)
    e._finish(k, 'test_close')
    assert old in e.trade.stops and e.state['lots'][k2]['stop_id'] == old
    e._finish(k2, 'test_close')                                               # the last owner: cancelled as before
    assert old not in e.trade.stops


# ---------------------------------------------------------------- Codex #361 round 2 (comment 6925138007)
def _unknown(e, monkeypatch, lot):
    real = e.trade.open_stop_orders
    monkeypatch.setattr(e.trade, 'open_stop_orders', lambda *a, **kw: [dict(r, qty=None) for r in real(*a, **kw)])
    e._verify_symbol('BTCUSDT', 'x')
    assert lot['stop_short'] and not lot.get('stop_dirty') and not e.stop_view(lot)['protected']
    return real


def test_r2_tag_only_fallback_never_clears_an_unknown_latch(tmp_path, monkeypatch):
    """HIGH 1: after a detailed read leaves coverage unknown, a verifier pass on a tag-only client keeps the latch."""
    e, clk, k, lot = setup(tmp_path)
    _unknown(e, monkeypatch, lot)
    monkeypatch.delattr(e.trade, 'open_stop_orders')                          # the client now only lists tags
    monkeypatch.delattr(type(e.trade), 'open_stop_orders')
    assert e._stop_rows('BTCUSDT')[1] is False                                # really the tag-only path
    clk[0] += 1; e._verify_symbol('BTCUSDT', 'x')
    assert lot.get('stop_short') and not e.stop_view(lot)['protected']
    assert (e.health.get('incidents', {}).get(f'stop-short|{k}') or {}).get('open')


def test_r2_unknown_coverage_blocks_entries_adds_and_grid_orders_but_not_exits(tmp_path, monkeypatch):
    """HIGH 2: unconfirmed coverage blocks new risk on the coin (manual too, both sides) without a blind replacement."""
    e, clk, k, lot = setup(tmp_path)
    n_stops = e.trade.n
    _unknown(e, monkeypatch, lot)
    for side in ('LONG', 'SHORT'):
        assert 'not confirmed' in (e.entry_block(None, 'BTCUSDT', side, manual=True) or ''), side
    assert 'not confirmed' in (e._add_block(lot, lot['qty'] / 2, 100.0) or '')
    assert e.stop_missing_on('BTCUSDT')                                       # the grid's new-order gate reads this
    assert e.entry_block(None, 'ETHUSDT', 'LONG', manual=True) is None or 'not confirmed' not in e.entry_block(None, 'ETHUSDT', 'LONG', manual=True)
    tick(e, clk, n=2)
    assert e.trade.n == n_stops                                               # nothing re-placed on unknown alone
    e._finish(k, 'test_close')                                                # an exit never consults the gate
    assert k not in e.state['lots'] and e.stop_missing_block('BTCUSDT') is None


def test_r2_proven_coverage_lifts_the_block(tmp_path, monkeypatch):
    e, clk, k, lot = setup(tmp_path)
    real = _unknown(e, monkeypatch, lot)
    monkeypatch.setattr(e.trade, 'open_stop_orders', real)
    clk[0] += 1; e._verify_symbol('BTCUSDT', 'x')
    assert 'stop_short' not in lot and e.stop_unconfirmed_block('BTCUSDT') is None


def test_r2_carrier_guard_is_per_coin(tmp_path):
    """A BTC stop tag equal to an ETH lot's (symbol-scoped) order id is still cancelled on BTC."""
    e, clk, k, lot = setup(tmp_path)
    tag = lot['stop_id']
    other = dict(lot, symbol='ETHUSDT'); e.state['lots'][k + '|eth'] = other           # same tag text, other coin
    del e.state['lots'][k]                                                    # no BTC lot records it any more
    assert e._cancel_or_park('BTCUSDT', tag) is True and tag not in e.trade.stops
    assert not [x for x in e.state.get('orphans') or [] if x[1] == tag]


def test_r2_a_true_shared_carrier_on_the_same_coin_is_still_kept(tmp_path):
    e, clk, k, lot = setup(tmp_path)
    tag = lot['stop_id']
    e.state['lots'][k + '|twin'] = dict(lot)
    assert e._cancel_or_park('BTCUSDT', tag) is False and tag in e.trade.stops
