"""FBL-ENG01: routine exchange verification of protective stops.

reconcile() only reads open orders when a position has SHRUNK, so a stop cancelled outside the bot (or EXPIRED instead of
filling) could leave the panel saying 'protected' indefinitely. These tests drive the budgeted verifier (engine.verify_stops)
through a fake exchange that keeps per-order status history, plus the REAL client for the parsing / outage paths."""
import os, sys, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import binance_client as BC                                                # noqa: E402
import engine as E                                                         # noqa: E402
from test_safety import FakeX, SL, SG                                      # noqa: E402
from test_outage import Resp, Clock, client, BUSY, _nosleep               # noqa: E402

T0 = 1_000_000.0


def cid(tag, prefix=None):
    """A bot client id of the exact binance_client.new_cid shape ('zb'/'za' + 22 hex) for a fake order tag."""
    return (prefix or ('za' if tag.startswith('a:') else 'zb')) + f'{int(tag[2:]):022x}'


class VX(FakeX):
    """FakeX + what the verifier reads: detailed open-order rows (with a delayed-visibility switch) and the status of
    every order ever placed (NEW / CANCELED / EXPIRED / FILLED ...). algo=True makes stop() return 'a:<id>' tags."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.hidden, self.status, self.cids, self.algo = set(), {}, {}, False
    def stop(self, s, ps, q, p):
        tag = super().stop(s, ps, q, p)
        if self.algo:
            self.stops['a:' + tag[2:]] = self.stops.pop(tag); tag = 'a:' + tag[2:]
        self.status[tag] = 'NEW'; return tag
    def cancel(self, s, tag):
        super().cancel(s, tag)
        if self.status.get(tag) == 'NEW': self.status[tag] = 'CANCELED'
        return True
    def open_stop_orders(self, s, strict_algo=False):
        self._f('tags'); self.calls.append(('strict', strict_algo))
        return [dict(tag=k, type='STOP_MARKET', position_side=v[1], side='SELL' if v[1] == 'LONG' else 'BUY', qty=v[2],
                     stop_price=v[3], client_id=self.cids.get(k, cid(k)), status='NEW')
                for k, v in self.stops.items() if v[0] == s and k not in self.hidden]
    def stop_status(self, s, tag):
        self._f('status'); self.calls.append(('status', tag)); return self.status.get(tag)
    # ---- what happens on the exchange outside the bot
    def ext_cancel(self, tag, status='CANCELED'):
        self.stops.pop(tag); self.status[tag] = status
    def trigger(self, tag, status='FILLED'):
        s, ps, q, p = self.stops.pop(tag); self.pos[(s, ps)] = self.pos.get((s, ps), 0) - q; self.status[tag] = status
    def add_stop(self, s, ps, q, p, cid):
        self.n += 1; tag = f'o:{self.n}'; self.stops[tag] = (s, ps, float(q), float(p)); self.cids[tag] = cid; self.status[tag] = 'NEW'
        return tag


def mk(cls=VX, tmp=None, dry=False, fake=None, **S):
    tmp = tmp or tempfile.mkdtemp()
    saved = E.Futures; E.Futures = cls
    try: e = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp, dry=dry)
    finally: E.Futures = saved
    if fake is not None: e.trade = fake
    e.data = e.trade
    e.S.update(dict(UNIVERSE=['BTCUSDT', 'ETHUSDT'], SYMBOLS_ON={'BTCUSDT': True, 'ETHUSDT': True}, CAPITAL_CAP=500.0), **S)
    e.connect(); e.marks = e.trade.marks()
    clk = [T0]; e.clock = lambda: clk[0]
    return e, clk, tmp


def lot_open(e, sym='BTCUSDT', side='LONG'):
    assert e.open_lot(SL if sym == 'BTCUSDT' else dict(SL, id='B'), sym, side, dict(SG, close=e.trade.mark[sym]), None, e.equity()), e.last_skip
    return next(k for k, l in e.state['lots'].items() if l['symbol'] == sym and l['side'] == side)


def reads(e, sym=None): return [c for c in e.trade.calls if c == 'tags'] if sym is None else None


def n_tags(e): return sum(1 for c in e.trade.calls if c == 'tags')


def n_stops(e): return sum(1 for c in e.trade.calls if c == 'stop')


def tick(e, clk, dt=8.0, n=1):
    for _ in range(n): clk[0] += dt; e.manage(e.trade.marks())


# ------------------------------------------------------------------ start-up / restart / panel freshness
def test_restart_drops_old_confirmation_and_the_startup_pass_verifies_every_symbol():
    e, clk, tmp = mk(); kb = lot_open(e); ke = lot_open(e, 'ETHUSDT')
    assert e.state['lots'][kb]['stop_confirmed_t'] == T0                   # placement accepted by Binance = confirmed now
    e.save_state()
    e2, clk2, _ = mk(tmp=tmp, fake=e.trade)                                 # restart on the same exchange
    for k in (kb, ke):
        l = e2.state['lots'][k]
        assert l['stop_confirmed_t'] is None and l['stop_miss'] == 0      # migrated default: unconfirmed
        v = e2.stop_view(l); assert not v['protected'] and v['stop_state'] == 'unverified'
    import app as A
    assert A._stop_view(e2, e2.state['lots'][kb])['protected'] is False    # the panel never shows an old proof as fresh
    c0 = n_tags(e2); e2.manage(e2.trade.marks())
    assert n_tags(e2) - c0 == 2                                             # ONE read per held symbol, both in the first pass
    for k in (kb, ke):
        assert e2.state['lots'][k]['stop_confirmed_t'] == T0 and e2.stop_view(e2.state['lots'][k])['protected']
    assert A._stop_view(e2, e2.state['lots'][kb]) == dict(protected=True, stop_state='confirmed', stop_age_s=0.0, stop_note=None)


def test_protected_needs_a_fresh_confirmation_stale_is_not_protected():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]
    e.trade.fail.add('tags')                                                # reads keep failing (non-transient)
    clk[0] += E.STOP_FRESH_S - 1; assert e.stop_view(l)['protected']
    tick(e, clk, 2.0)
    v = e.stop_view(l); assert not v['protected'] and v['stop_state'] == 'stale' and 'min ago' in v['stop_note']
    assert l['stop_miss'] == 0                                             # unreadable is NOT missing: no restore, no block
    assert n_stops(e) == 1 and e.entry_block(SL, 'BTCUSDT', 'SHORT') != E.STOP_MISSING_BLOCK
    import telegram_ctl as TG
    assert TG.TelegramControl._protected(e, l) is False                     # Telegram uses the same rule
    e.trade.fail.discard('tags'); tick(e, clk, E.STOP_RECHECK_S + 1)
    assert e.stop_view(l)['protected']


# ------------------------------------------------------------------ request-weight budget
def test_reads_are_budgeted_per_symbol_about_once_a_minute():
    e, clk, _ = mk(); lot_open(e); lot_open(e, 'ETHUSDT')
    c0 = n_tags(e); tick(e, clk, 8.0, n=75)                                 # 10 minutes of 8 s manage passes
    per_sym = (n_tags(e) - c0) / 2
    assert 8 <= per_sym <= 13, per_sym                                       # ~1 read (2 weight) per symbol per minute
    assert e.stopv_stats['restored'] == 0 and e.stopv_stats['misses'] == 0
    for s in ('BTCUSDT', 'ETHUSDT'):                                         # fixed per-coin jitter inside +-20 %
        assert 0.8 * E.STOP_VERIFY_S <= E.Engine._stopv_period(s) <= 1.2 * E.STOP_VERIFY_S
    assert E.Engine._stopv_period('BTCUSDT') != E.Engine._stopv_period('ETHUSDT')


def test_candle_close_re_verifies_unless_read_in_the_last_ten_seconds():
    e, clk, _ = mk(); lot_open(e)
    e.compute_signals = lambda tf, syms, extra=(): ({}, {})
    tick(e, clk, 4.0)                                                       # the routine pass read it (and set next due)
    c0 = n_tags(e); clk[0] += 5; e.cycle('4h'); assert n_tags(e) == c0     # read 5 s ago: skipped
    clk[0] += 6; e.cycle('4h'); assert n_tags(e) == c0 + 1                 # 11 s: the candle close re-verifies


def test_a_stop_order_action_is_re_verified_on_the_next_pass():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]
    tick(e, clk, 4.0); c0 = n_tags(e)
    tick(e, clk, 4.0); assert n_tags(e) == c0                               # not due yet
    assert e._replace_stop(l, 96.0)                                          # stop moved (order action)
    tick(e, clk, E.STOP_ACTION_MIN_S + 1); assert n_tags(e) == c0 + 1   # re-verified right after (floored)
    assert l['stop_id'] in e.trade.stops and len(e.trade.stops) == 1


# ------------------------------------------------------------------ external cancel -> fail closed, restore stop-first
def test_external_cancel_is_detected_alerted_and_restored_once():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]; old = l['stop_id']
    sent = []; e.notify = lambda t: sent.append(t)
    tick(e, clk, 4.0)
    e.trade.ext_cancel(old)                                                 # someone cancels it on Binance
    tick(e, clk, E.STOP_VERIFY_S * 1.25)
    assert ('status', old) in e.trade.calls                                 # direct lookup by id: CANCELED -> gone
    assert l['stop_id'] != old and l['stop_id'] in e.trade.stops and len(e.trade.stops) == 1
    assert not l['stop_dirty'] and l['stop_miss'] == 0 and e.stopv_stats['restored'] == 1
    inc = e.health['incidents'][f'stop-missing|{k}']; assert inc['open'] and 'restoring it' in inc['msg']
    assert len(sent) == 1 and 'disappeared' in sent[0]
    tick(e, clk, E.STOP_ACTION_MIN_S + 1)                                       # the new stop is seen listed -> incident closed
    assert not e.health['incidents'][f'stop-missing|{k}']['open'] and e.stop_view(l)['protected']
    tick(e, clk, 8.0, n=20); assert n_stops(e) == 2 and len(sent) == 1     # nothing more placed or alerted


def test_unknown_status_needs_two_consecutive_misses_and_blocks_entries_and_adds_meanwhile_but_never_exits():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]; old = l['stop_id']
    tick(e, clk, 4.0)
    e.trade.ext_cancel(old, status=None)                                    # gone, but the lookup cannot say so
    tick(e, clk, E.STOP_VERIFY_S * 1.25)
    assert l['stop_miss'] == 1 and l['stop_id'] == old and n_stops(e) == 1  # one miss: nothing placed yet
    assert e.stop_view(l)['stop_state'] == 'missing' and not e.stop_view(l)['protected']
    assert e.entry_block(SL, 'BTCUSDT', 'SHORT') == E.STOP_MISSING_BLOCK     # new entries on the coin blocked (both sides)
    assert e.entry_block(SL, 'BTCUSDT', 'LONG', manual=True) == E.STOP_MISSING_BLOCK
    assert e._add_block(l, 0.1, 100.0) == E.STOP_MISSING_BLOCK               # adds blocked
    assert e.entry_block(dict(SL, id='B'), 'ETHUSDT', 'LONG') != E.STOP_MISSING_BLOCK   # other coins unaffected
    tick(e, clk, E.STOP_RECHECK_S + 1)                                      # second consecutive miss -> restore
    assert l['stop_id'] != old and l['stop_id'] in e.trade.stops and n_stops(e) == 2 and l['stop_miss'] == 0
    assert e.entry_block(SL, 'BTCUSDT', 'SHORT') != E.STOP_MISSING_BLOCK


def test_exits_are_never_blocked_while_a_stop_is_missing():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]
    tick(e, clk, 4.0); e.trade.ext_cancel(l['stop_id'], status=None); tick(e, clk, E.STOP_VERIFY_S * 1.25)
    assert l['stop_miss'] == 1
    e.close_lot(k, 'manual_close')
    assert k not in e.state['lots'] and e.trade.pos[('BTCUSDT', 'LONG')] == 0 and 'close' in e.trade.calls


def test_failed_restore_stays_dirty_retries_and_keeps_entries_blocked():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]
    tick(e, clk, 4.0); e.trade.ext_cancel(l['stop_id'])
    e.trade.fail.add('stop'); tick(e, clk, E.STOP_VERIFY_S * 1.25)
    assert l['stop_dirty'] and l['stop_miss'] == 1
    assert e.entry_block(SL, 'BTCUSDT', 'SHORT') == E.STOP_MISSING_BLOCK
    e.trade.fail.discard('stop'); tick(e, clk, 4.0)                         # manage's existing every-pass retry
    assert not l['stop_dirty'] and l['stop_id'] in e.trade.stops and len(e.trade.stops) == 1


# ------------------------------------------------------------------ EXPIRED / triggered: never restore over a closed position
def test_stop_triggered_and_position_closed_is_booked_as_stopped_not_restored():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]; tag = l['stop_id']
    tick(e, clk, 4.0)
    e.trade.trigger(tag, 'FILLED'); clk[0] += E.STOP_VERIFY_S * 1.25
    assert e.verify_stops() is True and n_stops(e) == 1                    # FILLED: no restore (reconcile books it)
    e.manage(e.trade.marks())
    assert k not in e.state['lots'] and e.history[-1]['exit_reason'] == 'stop' and n_stops(e) == 1


def test_expired_on_trigger_with_the_position_already_reduced_is_left_to_reconcile():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]; tag = l['stop_id']
    tick(e, clk, 4.0)
    e.trade.trigger(tag, 'EXPIRED'); clk[0] += E.STOP_VERIFY_S * 1.25       # race: the critical re-read sees it closed
    e.verify_stops()
    assert e.stopv_stats['deferred'] == 1 and n_stops(e) == 1 and l['stop_id'] == tag
    assert f'stop-missing|{k}' not in e.health['incidents']                 # no false alarm
    e.manage(e.trade.marks()); assert k not in e.state['lots'] and n_stops(e) == 1


def test_expired_with_the_position_intact_is_restored():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]; tag = l['stop_id']
    tick(e, clk, 4.0)
    e.trade.ext_cancel(tag, status='EXPIRED'); tick(e, clk, E.STOP_VERIFY_S * 1.25)
    assert l['stop_id'] != tag and l['stop_id'] in e.trade.stops and e.stopv_stats['restored'] == 1
    assert 'EXPIRED' in e.health['incidents'][f'stop-missing|{k}']['msg']


def test_filled_status_with_the_position_intact_restores_only_after_three_misses():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]; tag = l['stop_id']
    tick(e, clk, 4.0)
    e.trade.ext_cancel(tag, status='FILLED')                                # says fired, but the position never moved
    tick(e, clk, E.STOP_VERIFY_S * 1.25); assert l['stop_miss'] == 1 and n_stops(e) == 1
    tick(e, clk, E.STOP_RECHECK_S + 1); assert l['stop_miss'] == 2 and n_stops(e) == 1
    tick(e, clk, E.STOP_RECHECK_S + 1); assert l['stop_id'] != tag and n_stops(e) == 2


def test_position_read_failure_right_before_restore_places_nothing():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]; tag = l['stop_id']
    tick(e, clk, 4.0); e.trade.ext_cancel(tag)
    clk[0] += E.STOP_VERIFY_S * 1.25; e.trade.fail.add('positions'); e.verify_stops()
    assert n_stops(e) == 1 and l['stop_id'] == tag and l['stop_miss'] == 1    # unknown position -> no order, still blocked
    assert e.entry_block(SL, 'BTCUSDT', 'SHORT') == E.STOP_MISSING_BLOCK


# ------------------------------------------------------------------ delayed visibility
def test_a_just_placed_stop_not_listed_yet_is_not_a_miss_and_a_live_status_confirms_it():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]
    e.trade.hidden.add(l['stop_id'])                                        # placed, but not in openOrders yet
    tick(e, clk, 4.0)
    assert l['stop_miss'] == 0 and ('status', l['stop_id']) not in e.trade.calls   # inside the listing grace: no miss
    tick(e, clk, E.STOP_LIST_GRACE_S + 1)                                   # still not listed -> one lookup: NEW
    assert ('status', l['stop_id']) in e.trade.calls
    assert l['stop_miss'] == 0 and l['stop_confirmed_t'] == clk[0] and n_stops(e) == 1
    tick(e, clk, 8.0, n=12); assert n_stops(e) == 1 and len(e.trade.stops) == 1


def test_without_a_status_lookup_two_misses_are_required_and_a_late_listing_cancels_nothing_twice():
    class TagsOnly(VX):
        stop_status = None
    e, clk, _ = mk(TagsOnly); k = lot_open(e); l = e.state['lots'][k]; tag = l['stop_id']
    tick(e, clk, 4.0); e.trade.hidden.add(tag)                              # listing lags
    tick(e, clk, E.STOP_VERIFY_S * 1.25); assert l['stop_miss'] == 1 and n_stops(e) == 1
    e.trade.hidden.discard(tag); tick(e, clk, E.STOP_RECHECK_S + 1)         # listed again before the 2nd miss
    assert l['stop_miss'] == 0 and l['stop_id'] == tag and n_stops(e) == 1
    e.trade.hidden.add(tag); tick(e, clk, E.STOP_VERIFY_S * 1.25); tick(e, clk, E.STOP_RECHECK_S + 1)
    assert l['stop_id'] != tag and n_stops(e) == 2                          # 2 consecutive misses -> restored
    assert tag not in e.trade.stops and len(e.trade.stops) == 1             # the old one was cancelled: never two live


def test_tag_only_fallback_client_works_for_the_base_fake():
    e, clk, _ = mk(FakeX); k = lot_open(e); l = e.state['lots'][k]
    e.trade.stops.pop(l['stop_id'])                                         # FakeX has no stop_status: unknown
    tick(e, clk, E.STOP_VERIFY_S * 1.25); assert l['stop_miss'] == 1
    tick(e, clk, E.STOP_RECHECK_S + 1); assert l['stop_id'] in e.trade.stops and len(e.trade.stops) == 1


# ------------------------------------------------------------------ duplicates
def test_an_extra_bot_stop_is_cancelled_and_foreign_orders_are_never_touched():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]
    extra = e.trade.add_stop('BTCUSDT', 'LONG', l['qty'], 90.0, BC.new_cid('zb'))
    mine = e.trade.add_stop('BTCUSDT', 'LONG', l['qty'], 80.0, 'web_user_stop')
    tick(e, clk, 4.0)
    assert extra not in e.trade.stops and mine in e.trade.stops and l['stop_id'] in e.trade.stops
    assert e.stopv_stats['extras_cancelled'] == 1
    assert any('extra bot stop' in x[1] for x in e.health['errors'])


def test_an_extra_bot_stop_is_kept_while_binance_holds_more_than_the_lots():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]
    e.trade.pos[('BTCUSDT', 'LONG')] += 2.0                                  # e.g. a lot lost from state: untracked size
    extra = e.trade.add_stop('BTCUSDT', 'LONG', 2.0, 90.0, BC.new_cid('zb'))
    tick(e, clk, 4.0); tick(e, clk, 8.0, n=10)
    assert 'BTCUSDT|LONG' in e.untracked and extra in e.trade.stops          # it may be that position's only stop
    assert e.stopv_stats['extras_cancelled'] == 0


def test_extras_are_kept_while_a_lot_has_no_listed_stop_of_its_own():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]
    tick(e, clk, 4.0); e.trade.ext_cancel(l['stop_id'], status=None)
    extra = e.trade.add_stop('BTCUSDT', 'LONG', l['qty'], 90.0, BC.new_cid('zb'))
    tick(e, clk, E.STOP_VERIFY_S * 1.25)
    assert l['stop_miss'] == 1 and extra in e.trade.stops                   # never cancel the only stop left
    tick(e, clk, E.STOP_RECHECK_S + 1)                                      # restored ...
    assert l['stop_id'] in e.trade.stops and extra in e.trade.stops
    tick(e, clk, E.STOP_ACTION_MIN_S + 1)                                       # ... then the extra goes: exactly one
    assert extra not in e.trade.stops and len(e.trade.stops) == 1


def test_a_matching_unowned_bot_stop_is_adopted_instead_of_placing_a_second():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]; tag = l['stop_id']
    l['stop_id'], l['stop_dirty'] = None, True                              # e.g. the placement answer was lost
    s0 = n_stops(e); tick(e, clk, 4.0)
    assert l['stop_id'] == tag and not l['stop_dirty'] and n_stops(e) == s0 and len(e.trade.stops) == 1
    assert e.stopv_stats['adopted'] == 1 and e.stop_view(l)['protected']


def test_a_missing_stop_is_replaced_by_adopting_a_matching_listed_one():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]; tag = l['stop_id']
    tick(e, clk, 4.0); e.trade.ext_cancel(tag)
    twin = e.trade.add_stop('BTCUSDT', 'LONG', l['qty'], l['stop'], BC.new_cid('zb'))
    tick(e, clk, E.STOP_VERIFY_S * 1.25)
    assert l['stop_id'] == twin and n_stops(e) == 1 and len(e.trade.stops) == 1


# ------------------------------------------------------------------ algo vs classic tags
def test_algo_stops_are_verified_and_restored_by_their_a_tags():
    e, clk, _ = mk(); e.trade.algo = True
    k = lot_open(e); l = e.state['lots'][k]; tag = l['stop_id']; assert tag.startswith('a:')
    tick(e, clk, 4.0); assert e.stop_view(l)['protected']
    e.trade.ext_cancel(tag, 'EXPIRED'); tick(e, clk, E.STOP_VERIFY_S * 1.25)
    assert ('status', tag) in e.trade.calls and l['stop_id'].startswith('a:') and l['stop_id'] != tag
    assert len(e.trade.stops) == 1


def test_real_client_rows_and_status_lookups_for_classic_and_algo(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk)
    def answer(m, p, q):
        if p == '/v1/openOrders': return Resp(200, [{'orderId': 11, 'type': 'STOP_MARKET', 'positionSide': 'LONG', 'side': 'SELL',
                                                     'origQty': '0.5', 'stopPrice': '95', 'clientOrderId': 'zbA', 'status': 'NEW'},
                                                    {'orderId': 12}])                     # detail-less rows still count
        if p == '/v1/openAlgoOrders': return Resp(200, {'orders': [{'algoId': 7, 'orderType': 'STOP_MARKET', 'positionSide': 'SHORT',
                                                                    'quantity': '1', 'triggerPrice': '60', 'clientAlgoId': 'zaB'}]})
        if p == '/v1/order':
            if q.get('orderId') == '11': return Resp(200, {'orderId': 11, 'status': 'canceled'})
            return Resp(400, {'code': -2013, 'msg': 'Order does not exist.'})
        if p == '/v1/algoOrder':
            if q.get('algoId') == '7': return Resp(200, {'algoId': 7, 'algoStatus': 'TRIGGERED'})
            if q.get('algoId') == '8': return Resp(404, {'code': -5000, 'msg': 'Path /fapi/v1/algoOrder, Method GET is invalid'})
            return BUSY()
        return Resp(500, {'code': -1, 'msg': 'unexpected'})
    c, calls = client(answer, clk)
    rows = c.open_stop_orders('BTCUSDT')
    assert [r['tag'] for r in rows] == ['o:11', 'o:12', 'a:7'] and c.open_stop_tags('BTCUSDT') == {'o:11', 'o:12', 'a:7'}
    assert rows[0] == dict(tag='o:11', type='STOP_MARKET', position_side='LONG', side='SELL', qty=0.5, stop_price=95.0,
                           client_id='zbA', status='NEW')
    assert rows[1]['client_id'] is None and rows[1]['qty'] is None
    assert rows[2]['client_id'] == 'zaB' and rows[2]['stop_price'] == 60.0 and rows[2]['position_side'] == 'SHORT'
    assert c.stop_status('BTCUSDT', 'o:11') == 'CANCELED'
    assert c.stop_status('BTCUSDT', 'o:99') is None                           # does not exist = unknown, never 'gone'
    assert c.stop_status('BTCUSDT', 'a:7') == 'TRIGGERED'
    assert c.stop_status('BTCUSDT', 'a:8') is None                            # algo endpoint unsupported = unknown
    assert c.stop_status('BTCUSDT', 'junk') is None
    with pytest.raises(BC.BinanceError): c.stop_status('BTCUSDT', 'a:9')     # busy = raise (caller: unknown)
    paths = [(m, p, q.get('orderId') or q.get('algoId')) for m, p, q in calls if p in ('/v1/order', '/v1/algoOrder')]
    assert paths[0] == ('GET', '/v1/order', '11') and ('GET', '/v1/algoOrder', '7') in paths
    assert all(q.get('symbol') == 'BTCUSDT' for m, p, q in calls if p in ('/v1/openOrders', '/v1/openAlgoOrders'))   # weight-1 form


def test_real_client_busy_algo_read_is_unknown_not_an_empty_list(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk)
    def answer(m, p, q):
        if p == '/v1/openOrders': return Resp(200, [])
        return BUSY()
    c, calls = client(answer, clk)
    with pytest.raises(BC.BinanceError): c.open_stop_orders('BTCUSDT')


# ------------------------------------------------------------------ outage interplay (T05b circuit)
def test_circuit_in_outage_skips_verification_entirely():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]
    h = BC.ExchangeHealth(clock=Clock()); e.trade.__dict__['_health'] = h
    for _ in range(3): h.fail('x')
    assert h.state == 'outage'
    e.trade.ext_cancel(l['stop_id']); c0 = n_tags(e); clk[0] += 600
    assert e.verify_stops() is False and n_tags(e) == c0 and l['stop_miss'] == 0
    assert e.stop_view(l)['stop_state'] == 'stale'                           # unknown -> not confirmed, NOT 'missing'


def test_transient_read_failures_through_the_real_client_never_conclude_missing(monkeypatch):
    from test_outage import _real_client_engine, _marks, _orders
    e, k, c, calls, clk, mode = _real_client_engine(monkeypatch)
    e.clock = clk; e._stopv.clear(); lot = e.state['lots'][k]; lot['stop_confirmed_t'] = None   # one clock for both
    e.manage(_marks()); assert lot['stop_confirmed_t'] == clk.t and e.stop_view(lot)['protected']
    n0 = len(e.health['errors']); c0 = len(calls)
    mode['down'] = True
    for _ in range(60): e.manage(_marks()); clk.t += 8                       # 8 minutes of outage
    assert lot['stop_miss'] == 0 and _orders(calls, c0) == []               # nothing concluded, nothing sent
    v = e.stop_view(lot); assert not v['protected'] and v['stop_state'] == 'stale'
    assert e.stopv_stats['restored'] == 0 and e.stopv_stats['misses'] == 0
    new = [x[1] for x in list(e.health['errors'])[n0:]]
    assert sum('no order was sent' in x for x in new) == 1 and not any('stop' in x and 'restor' in x for x in new), new
    mode['down'] = False; clk.t += 120
    e.manage(_marks()); e.manage(_marks())
    assert c.health.state == 'ok' and e.stop_view(lot)['protected'] and _orders(calls, c0) == []


def test_stop_cancelled_during_an_outage_is_restored_after_recovery():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]; tag = l['stop_id']
    h = BC.ExchangeHealth(clock=lambda: clk[0]); e.trade.__dict__['_health'] = h
    real = e.trade.positions; down = {'v': False}
    def positions():
        if down['v']: h.fail('GET /fapi/v2/positionRisk: HTTP 503'); raise BC.BinanceError(-1007, 'Timeout')
        h.ok(); return real()
    e.trade.positions = positions
    tick(e, clk, 4.0)
    down['v'] = True; tick(e, clk, 8.0, n=10)
    e.trade.ext_cancel(tag)                                                 # cancelled while the bot was blind
    assert n_stops(e) == 1
    down['v'] = False; tick(e, clk, 8.0)
    assert not e.health['incidents']['exchange-down']['open']               # T05b recovery still closes the incident
    assert l['stop_id'] != tag and l['stop_id'] in e.trade.stops and len(e.trade.stops) == 1   # same pass: restored
    assert e.health['incidents']['stop-unseen|BTCUSDT|LONG']['open']             # T05b reported it (observe only) ...
    tick(e, clk, E.STOP_ACTION_MIN_S + 1)                                       # ... and it closes once the new stop is listed
    assert not e.health['incidents']['stop-unseen|BTCUSDT|LONG']['open'] and not e.health['incidents'][f'stop-missing|{k}']['open']


# ------------------------------------------------------------------ dry mode untouched
def test_dry_mode_never_reads_or_places_anything():
    live, _, _ = mk(); k = lot_open(live)
    e, clk, _ = mk(dry=True)
    assert e.open_lot(SL, 'ETHUSDT', 'LONG', dict(SG, close=50.0), None, e.equity()) and not e.state['lots']   # dry: no lot
    e.state['lots'][k] = dict(live.state['lots'][k], stop_confirmed_t=None); l = e.state['lots'][k]
    e.trade.stops = dict(live.trade.stops); e.trade.pos = dict(live.trade.pos); e.trade.ext_cancel(l['stop_id'])
    c0 = list(e.trade.calls); tick(e, clk, 8.0, n=20)
    assert e.verify_stops() is False and [c for c in e.trade.calls[len(c0):] if c in ('tags', 'stop', 'cancel', 'status')] == []
    assert l['stop_miss'] == 0 and not l['stop_dirty'] and e.stop_view(l)['protected'] is False      # unverified, untouched


# ------------------------------------------------------------------ T05a observe-only hooks unaffected
def test_audit_failures_do_not_affect_verification(monkeypatch):
    import trade_audit as TA
    monkeypatch.setattr(TA, 'observe', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('audit broken')))
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]
    tick(e, clk, 4.0); e.trade.ext_cancel(l['stop_id'])
    tick(e, clk, E.STOP_VERIFY_S * 1.25)
    assert e.stopv_stats['restored'] == 1 and len(e.trade.stops) == 1


# ------------------------------------------------------------------ adversarial review of 333ca56 (D1-D4 + gaps)
class AX(VX):
    """Entry stop answer lost (the stop IS live, client id known, parked as c:<cid>), cancels can fail on demand."""
    amb_once = False; cancel_fail = 0
    def stop(self, s, ps, q, p):
        tag = super().stop(s, ps, q, p)
        if self.amb_once:
            self.amb_once = False; c = cid(tag); self.cids[tag] = c; self.bycid = dict(getattr(self, 'bycid', {}), **{'c:' + c: tag})
            raise BC.AmbiguousOrder('timeout', 'c:' + c)
        return tag
    def cancel(self, s, tag):
        if self.cancel_fail: self.cancel_fail -= 1; self.calls.append('cancel'); raise BC.BinanceError(-1001, 'disconnected')
        return super().cancel(s, getattr(self, 'bycid', {}).get(tag, tag))


def test_d1_a_stop_queued_for_cancellation_is_never_adopted():
    e, clk, _ = mk(AX)
    e.trade.amb_once = True; e.trade.fail.add('close')                      # entry stop unconfirmed + safety close fails
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', dict(SG, close=100.0), None, e.equity())
    k = next(iter(e.state['lots'])); l = e.state['lots'][k]; e.trade.fail.discard('close')
    amb = next(iter(e.trade.stops)); assert l['stop_id'] is None and e.state['orphans'] == [['BTCUSDT', 'c:' + cid(amb)]]
    e.trade.cancel_fail = 1; tick(e, clk, 8.0)                              # the orphan sweep fails once
    assert l['stop_id'] != amb and e.stopv_stats['adopted'] == 0            # NOT adopted: a fresh stop was placed
    unprotected = 0
    for _ in range(12):
        tick(e, clk, 8.0); unprotected += 0 if l['stop_id'] in e.trade.stops else 8
    assert unprotected == 0 and list(e.trade.stops) == [l['stop_id']] and e.state['orphans'] == []


def test_d1_restore_never_adopts_a_queued_orphan_either():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]; tag = l['stop_id']
    tick(e, clk, 4.0); e.trade.ext_cancel(tag)
    twin = e.trade.add_stop('BTCUSDT', 'LONG', l['qty'], l['stop'], BC.new_cid('zb'))
    e.state['orphans'].append(['BTCUSDT', 'c:' + e.trade.cids[twin]]); e.trade.fail.add('cancel')   # queued, cancel failing
    tick(e, clk, E.STOP_VERIFY_S * 1.25)
    assert l['stop_id'] not in (tag, twin) and l['stop_id'] in e.trade.stops and e.stopv_stats['adopted'] == 0


def test_d2_algo_endpoint_unsupported_while_holding_an_algo_stop_is_unknown_never_a_second_stop(monkeypatch):
    from test_safety import mk_engine, opened
    e, _ = mk_engine(); k = opened(e); lot = e.state['lots'][k]; lot['stop_id'] = 'a:77'
    clk = Clock(); _nosleep(monkeypatch, clk)
    live = {'a:77'}; down = {'on': False}; n = {'o': 100}
    def answer(m, p, q):
        if p == '/v2/positionRisk': return Resp(200, [{'symbol': 'BTCUSDT', 'positionSide': 'LONG', 'positionAmt': str(lot['qty'])}])
        if 'lgo' in p and down['on']: return Resp(404, {'code': -1404, 'msg': 'HTTP 404 (not JSON)'})
        if p == '/v1/openOrders': return Resp(200, [])
        if p == '/v1/openAlgoOrders':
            return Resp(200, {'orders': [{'algoId': 77, 'orderType': 'STOP_MARKET', 'positionSide': 'LONG', 'quantity': str(lot['qty']),
                                          'triggerPrice': str(lot['stop']), 'clientAlgoId': 'za' + '7' * 22}] if 'a:77' in live else []})
        if p == '/v1/order' and m == 'POST': n['o'] += 1; live.add(f"o:{n['o']}"); return Resp(200, {'orderId': n['o'], 'status': 'NEW'})
        if m == 'DELETE': live.discard(('a:' if 'lgo' in p else 'o:') + str(q.get('algoId') or q.get('orderId'))); return Resp(200, {})
        return Resp(400, {'code': -1102, 'msg': f'unexpected {m} {p}'})
    c, calls = client(answer, clk); e.trade = c
    t = [T0]; e.clock = lambda: t[0]; lot['stop_placed_t'] = T0 - 100; e._stopv.clear()
    sent = []; e.notify = lambda m: sent.append(m)
    e.manage({'BTCUSDT': 100.0, 'ETHUSDT': 50.0}); assert e.stop_view(lot)['protected']
    down['on'] = True
    for _ in range(15): t[0] += 8; e.manage({'BTCUSDT': 100.0, 'ETHUSDT': 50.0})
    assert sorted(live) == ['a:77'] and lot['stop_id'] == 'a:77' and e.state['orphans'] == [] and sent == []
    assert lot['stop_miss'] == 0 and e.stopv_stats['restored'] == 0       # unknown, not missing
    assert not [x for x in calls if x[0] == 'POST']
    down['on'] = False
    for _ in range(3): t[0] += 8; e.manage({'BTCUSDT': 100.0, 'ETHUSDT': 50.0})
    assert sorted(live) == ['a:77'] and e.stop_view(lot)['protected']


def test_d2_real_client_strict_algo_raises_on_unsupported(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk)
    def answer(m, p, q):
        if p == '/v1/openOrders': return Resp(200, [])
        return Resp(404, {'code': -1404, 'msg': 'HTTP 404 (not JSON)'})
    c, calls = client(answer, clk)
    assert c.open_stop_orders('BTCUSDT') == []                               # no algo stop held: unsupported = none (as before)
    with pytest.raises(BC.BinanceError): c.open_stop_orders('BTCUSDT', strict_algo=True)


def test_d2_an_algo_stop_with_an_unreadable_status_is_alerted_never_auto_replaced():
    e, clk, _ = mk(); e.trade.algo = True
    k = lot_open(e); l = e.state['lots'][k]; tag = l['stop_id']
    sent = []; e.notify = lambda t: sent.append(t)
    tick(e, clk, 4.0); e.trade.ext_cancel(tag, status=None)                 # not listed, status unknown
    tick(e, clk, E.STOP_VERIFY_S * 1.25); tick(e, clk, E.STOP_RECHECK_S + 1); tick(e, clk, E.STOP_RECHECK_S + 1)
    assert l['stop_id'] == tag and n_stops(e) == 1 and l['stop_miss'] >= 2   # never a second stop on a guess
    assert e.health['incidents'][f'stop-missing|{k}']['open'] and 'cannot be read' in e.health['incidents'][f'stop-missing|{k}']['msg']
    assert len(sent) == 1 and e.entry_block(SL, 'BTCUSDT', 'SHORT') == E.STOP_MISSING_BLOCK
    e.trade.status[tag] = 'CANCELED'; tick(e, clk, E.STOP_RECHECK_S + 1)    # a readable gone status restores it
    assert l['stop_id'] != tag and len(e.trade.stops) == 1


def _open_incidents(e): return sorted(k for k, i in e.health['incidents'].items() if i['open'])


def test_d3_extra_cancel_and_closed_lot_incidents_are_closed():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]
    extra = e.trade.add_stop('BTCUSDT', 'LONG', l['qty'], l['stop'] - 5, BC.new_cid('zb'))
    tick(e, clk, 4.0)
    assert extra not in e.trade.stops and not [x for x in _open_incidents(e) if x.startswith('stop-extra')]
    assert any('extra bot stop' in x[1] for x in e.health['errors'])        # still reported once
    e.trade.ext_cancel(l['stop_id']); e.trade.fail.add('stop')
    tick(e, clk, E.STOP_VERIFY_S * 1.25); assert f'stop-missing|{k}' in _open_incidents(e)
    e.close_lot(k, 'manual', 100.0)
    assert f'stop-missing|{k}' not in _open_incidents(e)


def test_d3_an_extra_whose_cancel_fails_stays_open_until_it_is_gone():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]
    extra = e.trade.add_stop('BTCUSDT', 'LONG', l['qty'], l['stop'] - 5, BC.new_cid('zb'))
    e.trade.fail.add('cancel'); tick(e, clk, 4.0)
    assert f'stop-extra|BTCUSDT|{extra}' in _open_incidents(e) and ['BTCUSDT', extra] in e.state['orphans']


def test_d4_a_stop_trailing_every_pass_is_not_re_read_every_pass():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]
    c0 = n_tags(e)
    for _ in range(75):                                                     # 10 min of 8 s passes, the stop moves each pass
        clk[0] += 8.0; assert e._replace_stop(l, l['stop'] + 0.01); e.manage(e.trade.marks())
    assert n_tags(e) - c0 <= 20, n_tags(e) - c0                              # ~1 read / 32 s, not 1 per pass (was 38)
    assert l['stop_id'] in e.trade.stops and len(e.trade.stops) == 1


# ------------------------------------------------------------------ test gaps from the review
def test_restore_whose_new_stop_answer_is_lost_leaves_no_lasting_duplicate():
    e, clk, _ = mk(AX); k = lot_open(e); l = e.state['lots'][k]; tag = l['stop_id']
    tick(e, clk, 4.0); e.trade.ext_cancel(tag)
    e.trade.amb_once = True                                                 # the restore's stop is placed but unanswered
    clk[0] += E.STOP_VERIFY_S * 1.25; e.verify_stops()
    assert l['stop_dirty'] and len(e.state['orphans']) == 1 and len(e.trade.stops) == 1   # live, but unknown to the bot
    tick(e, clk, 8.0, n=12)
    assert not l['stop_dirty'] and l['stop_id'] in e.trade.stops and len(e.trade.stops) == 1 and e.state['orphans'] == []


def test_two_lots_same_coin_side_qty_and_price_keep_their_own_stops():
    e, clk, _ = mk(); ka = lot_open(e)
    assert e.open_lot(dict(SL, id='C'), 'BTCUSDT', 'LONG', dict(SG, close=100.0), None, e.equity()), e.last_skip
    kb = next(x for x in e.state['lots'] if x != ka); la, lb = e.state['lots'][ka], e.state['lots'][kb]
    assert (la['qty'], la['stop']) == (lb['qty'], lb['stop']) and la['stop_id'] != lb['stop_id']
    tick(e, clk, 4.0); a0, b0 = la['stop_id'], lb['stop_id']
    e.trade.ext_cancel(a0); tick(e, clk, E.STOP_VERIFY_S * 1.25)
    assert lb['stop_id'] == b0 and la['stop_id'] not in (a0, b0)            # B's twin stop is never adopted / cancelled
    assert set(e.trade.stops) == {la['stop_id'], b0}
    tick(e, clk, 8.0, n=10); assert set(e.trade.stops) == {la['stop_id'], b0}


def test_only_exact_bot_client_ids_count_as_extras():
    e, clk, _ = mk(); k = lot_open(e); l = e.state['lots'][k]
    foreign = [e.trade.add_stop('BTCUSDT', 'LONG', l['qty'], 80.0 + i, c) for i, c in enumerate(
        ['zbOLDDUP', 'zb' + 'A' * 22, 'zc' + 'a' * 22, 'zb' + 'a' * 23, 'zb' + 'a' * 21, 'xzb' + 'a' * 22, 'web_' + 'a' * 22, ''])]
    bot = e.trade.add_stop('BTCUSDT', 'LONG', l['qty'], 70.0, BC.new_cid('za'))
    tick(e, clk, 4.0)
    assert all(f in e.trade.stops for f in foreign) and bot not in e.trade.stops
    assert E.BOT_STOP_CID_RE.fullmatch(BC.new_cid('zb')) and E.BOT_STOP_CID_RE.fullmatch(BC.new_cid('za'))


def test_real_client_status_minus_2013_is_unknown_for_classic_and_algo(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk)
    c, calls = client(lambda m, p, q: Resp(400, {'code': -2013, 'msg': 'Order does not exist.'}), clk)
    assert c.stop_status('BTCUSDT', 'o:5') is None and c.stop_status('BTCUSDT', 'a:5') is None
    assert c.stop_status('BTCUSDT', 'c:zbX') is None and c.stop_status('BTCUSDT', 'ac:zaX') is None
    assert [p for m, p, q in calls] == ['/v1/order', '/v1/algoOrder', '/v1/order', '/v1/algoOrder']
