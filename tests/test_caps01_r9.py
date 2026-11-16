"""CAPS-01 r9 (Codex #365 round 9, c6931239346): the emergency stop that protects a HELD order's observed exposure while
storage is down has a DETERMINISTIC identity (derived from the held order's durable client id), so a process loss during
the outage - with the send's answer accepted or lost, on the classic or the algo (clientAlgoId) path - is followed by a
lookup of that same id and never by a second stop. A real Engine reloads the real on-disk dual-owner state; the venue
fake models client ids, duplicate-id refusal, the algo fallback, lost answers, status lookups and the open-order listing.
Checked after every pass: never two live stops, never 0 once protected, nothing booked while held; after storage recovers:
one lot, settled once, one correctly sized stop. Offline fakes only."""
import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import binance_client as BC
import engine as E
import test_caps01_r6 as R6                                                 # noqa: E402
import test_caps01_r7 as R7                                                 # noqa: E402


class Venue:
    """Stop orders by client id on top of the FakeX/Book fake. mode 'classic' or 'algo' (classic refused -4120, then the
    algo endpoint with clientAlgoId); lose='placed' -> placed but the answer is lost, 'unplaced' -> lost before Binance."""
    def __init__(self, x, mode='classic'):
        self.x, self.mode, self.lose, self.n, self.by_id, self.st, self.max_live = x, mode, None, 0, {}, {}, 0
        self.fail_cancel = set()                                        # resolved tags whose cancel raises (network)
        x.stop, x.stop_status, x.open_stop_orders, x.stop_state = self.stop, self.stop_status, self.open_stop_orders, self.stop_state
        bk = x.cancel.__self__ if hasattr(x.cancel, '__self__') and hasattr(x.cancel.__self__, 'orders') else None
        if bk is not None: bk._cancel = self.cancel
        else: x.cancel = self.cancel
    def live(self, sym='BTCUSDT', side='LONG'):
        return [(t, v) for t, v in self.x.stops.items() if v[0] == sym and v[1] == side and self.st.get(t) == 'NEW']
    def stop(self, s, ps, q, p, cid=None, acid=None):
        self.x._f('stop')
        lose, self.lose = self.lose, None
        if self.mode == 'classic' and cid:
            if cid in self.by_id: raise BC.BinanceError(-4116, 'ClientOrderId is duplicated.')
            if lose == 'unplaced': raise BC.AmbiguousOrder('answer lost (never reached Binance)', f'c:{cid}')
            tag = self._put('o', s, ps, q, p, cid)
            if lose == 'placed': raise BC.AmbiguousOrder('answer lost', f'c:{cid}')
            return tag
        if self.mode == 'algo' and acid:
            if acid in self.by_id: raise BC.BinanceError(-4116, 'clientAlgoId is duplicated.')
            if lose == 'unplaced': raise BC.AmbiguousOrder('algo stop unconfirmed', f'ac:{acid}')
            tag = self._put('a', s, ps, q, p, acid)
            if lose == 'placed': raise BC.AmbiguousOrder('algo stop unconfirmed', f'ac:{acid}')
            return tag
        return self._put('o', s, ps, q, p, None)                       # a lot's own stop (no client id from the engine)
    def _put(self, k, s, ps, q, p, cid):
        self.n += 1; tag = f'{k}:{self.n}'
        self.x.stops[tag] = (s, ps, float(q), float(p)); self.st[tag] = 'NEW'
        if cid: self.by_id[cid] = tag
        self.max_live = max(self.max_live, len(self.live(s, ps)))
        return tag
    def _tag(self, tag):
        kind, _, oid = tag.partition(':')
        return self.by_id.get(oid) if kind in ('c', 'ac') else tag
    def cancel(self, s, tag):
        self.x._f('cancel'); t = self._tag(tag)
        if t in self.fail_cancel: raise BC.BinanceError(-1001, 'injected: cancel not answered')
        if t in self.st and self.st[t] == 'NEW': self.st[t] = 'CANCELED'; self.x.stops.pop(t, None)
        else: self.x.stops.pop(tag, None)
        return True
    def stop_status(self, s, tag, retry=None):
        t = self._tag(tag)
        if t is None: return None
        if t in self.st: return self.st[t]
        return 'NEW' if t in self.x.stops else None
    def stop_state(self, s, tag):
        st = self.stop_status(s, tag)
        if st is None: return 'absent', None                           # the venue's explicit no-such-order
        return {'NEW': 'live', 'CANCELED': 'gone', 'FILLED': 'fired'}.get(st, 'unknown'), st
    def open_stop_orders(self, s, strict_algo=False, retry=None):
        cid = {v: k for k, v in self.by_id.items()}
        return [dict(tag=t, type='STOP_MARKET', position_side=v[1], side='SELL' if v[1] == 'LONG' else 'BUY', qty=v[2],
                     stop_price=v[3], client_id=cid.get(t), status='NEW', close_position=False, reduce_only=False)
                for t, v in self.x.stops.items() if v[0] == s and self.st.get(t, 'NEW') == 'NEW']


def storage_down(monkeypatch):
    real, down = E.save_json, [True]
    def flaky(path, obj, backup=False, strict=False):
        if down[0] and os.path.basename(path) == 'state.json': raise OSError('injected: state.json not written (persistent)')
        return real(path, obj, backup=backup, strict=strict)
    monkeypatch.setattr(E, 'save_json', flaky)
    return down


def held(e, cid):
    return cid in [r['cid'] for r in e.state['resting_entries'].values()]


def restart(e):
    """Process loss: a new real Engine on the same data directory and the same venue (nothing in memory survives)."""
    fake = e.trade
    e2 = R6._engine_on(os.path.dirname(e.F['state']), fake)
    e2.connect(); e2.marks = fake.marks()
    return e2


def one_pass(e, v, exec_q, booked_ok=False):
    e.manage(e.trade.marks()); e.reconcile(500)
    live = v.live()
    assert len(live) <= 1, f'two live stops after a pass: {live}'
    if not booked_ok: assert v.max_live <= 1, 'two live stops at one moment during the outage'
    if not booked_ok: assert R7._booked(e, exec_q) == [], 'booked before the handover was durable'
    return live


@pytest.mark.parametrize('mode', ['classic', 'algo'])
@pytest.mark.parametrize('answer', ['accepted', 'lost', 'lost-unplaced'])
@pytest.mark.parametrize('recover', ['same-process', 'new-process'])
def test_r9_process_loss_during_the_outage_never_doubles_the_stop_then_settles_once(monkeypatch, mode, answer, recover):
    e, cid, exec_q = R7._dual_owner_on_disk()
    v = Venue(e.trade, mode)
    down = storage_down(monkeypatch)
    v.lose = {'accepted': None, 'lost': 'placed', 'lost-unplaced': 'unplaced'}[answer]
    one_pass(e, v, exec_q)                                                    # the first emergency send
    assert held(e, cid)
    assert len(v.live()) == (0 if answer == 'lost-unplaced' else 1)
    e = restart(e)                                                            # process loss DURING the outage
    assert held(e, cid) and not any(u.get('prov') for u in e.state['unconfirmed_entries'].values())   # nothing on disk names it
    for _ in range(3):
        live = one_pass(e, v, exec_q)
        assert held(e, cid) and len(live) == 1 and abs(live[0][1][2] - exec_q) < 1e-9   # continuously protected, once
    assert e.persist_block()
    if recover == 'new-process': e = restart(e)
    down[0] = False                                                           # storage recovers
    time.sleep(1.1)                                                           # distinct lot key (#363 F5, not this PR)
    for _ in range(3): one_pass(e, v, exec_q, booked_ok=True)
    lots = R7._booked(e, exec_q)
    assert len(lots) == 1 and lots[0]['qty'] == pytest.approx(exec_q)         # settled exactly once
    live = v.live()
    assert len(live) == 1 and abs(live[0][1][2] - exec_q) < 1e-9 and v.x.stops.get(lots[0]['stop_id']) == live[0][1]
    assert not e.state['resting_entries'] and not e.state['unconfirmed_entries'] and not e.untracked
    assert not e.persist_block()
    assert v.max_live <= 2                    # the lot's stop replaces the emergency one replace-first (new, then old cancelled)


def test_r9_emergency_ids_are_deterministic_bot_ids_within_binance_limits():
    a, b = E.Engine._emg_ids('zmabc', 0), E.Engine._emg_ids('zmabc', 0)
    assert a == b and E.Engine._emg_ids('zmabc', 1) != a and E.Engine._emg_ids('zmabd', 0) != a
    for g in range(E.EMG_GENS):
        for i in E.Engine._emg_ids('zm' + 'f' * 22, g):
            assert E.BOT_STOP_CID_RE.fullmatch(i) and len(i) <= 36


def test_r9_unknown_status_sends_nothing(monkeypatch):
    e, cid, exec_q = R7._dual_owner_on_disk()
    v = Venue(e.trade)
    storage_down(monkeypatch)
    e.trade.stop_state = lambda s, tag: ('unknown', None)                     # e.g. a failed read / unsupported endpoint
    for _ in range(2): one_pass(e, v, exec_q)
    assert not v.live() and 'stop' not in e.trade.calls                      # unknown -> never a stop on a guess


def test_r9_a_foreign_stop_with_another_id_is_never_adopted(monkeypatch):
    e, cid, exec_q = R7._dual_owner_on_disk()
    v = Venue(e.trade)
    tag = v._put('o', 'BTCUSDT', 'LONG', exec_q, 90.0, 'web_manual_1')        # placed outside the bot
    storage_down(monkeypatch)
    e.manage(e.trade.marks()); e.reconcile(500)
    (u,) = e.state['unconfirmed_entries'].values()
    assert u.get('prov') not in (tag, 'c:web_manual_1') and tag in e.trade.stops   # not adopted, not cancelled


def test_r9_normal_provisional_callers_keep_the_strict_write_ahead(monkeypatch):
    e, cid, exec_q = R7._dual_owner_on_disk()
    Venue(e.trade)
    storage_down(monkeypatch)
    (uk, u), = e.state['unconfirmed_entries'].items()
    n = len(e.trade.stops)
    e._prov_set(uk, u, exec_q)                                                # not held: write-ahead fails -> nothing sent
    assert len(e.trade.stops) == n and not u.get('prov')


def test_r9_a_lost_answer_not_visible_yet_is_never_sent_again_in_the_same_process(monkeypatch):
    e, cid, exec_q = R7._dual_owner_on_disk()
    v = Venue(e.trade)
    storage_down(monkeypatch)
    v.lose = 'placed'
    real, real_l = v.stop_state, v.open_stop_orders
    v.x.stop_state = lambda s, tag: ('absent', None)                          # placed, but not visible to lookups yet
    v.x.open_stop_orders = lambda s, strict_algo=False, retry=None: []
    e.reconcile(500); e.reconcile(500); e.reconcile(500)
    assert e.trade.calls.count('stop') == 1                                   # its outcome is unknown: nothing re-sent
    v.x.stop_state, v.x.open_stop_orders = real, real_l
    e.reconcile(500)
    assert len(v.live()) == 1 and e.trade.calls.count('stop') == 1            # found by its id and adopted


@pytest.mark.parametrize('refused', [False, True], ids=['replaced', 'replacement-refused'])
def test_r9_a_resize_never_drops_the_only_confirmed_stop_first(monkeypatch, refused):
    e, cid, exec_q = R7._dual_owner_on_disk()
    v = Venue(e.trade)
    storage_down(monkeypatch)
    e.reconcile(500)
    (old,) = v.live()
    e.trade.pos[('BTCUSDT', 'LONG')] = round(exec_q / 2, 3)                   # the exposure shrank (closed outside the bot)
    if refused: e.trade.fail.add('stop')
    e.reconcile(500)
    live = v.live()
    assert len(live) == 1 and v.max_live <= 2
    if refused: assert live == [old]                                          # the oversized stop stays: never 0 stops
    else: assert live[0] != old and abs(live[0][1][2] - exec_q / 2) < 1e-9 and v.st[old[0]] == 'CANCELED'


def test_r9_a_live_stop_whose_size_is_not_listed_yet_blocks_a_second_one_after_a_restart(monkeypatch):
    e, cid, exec_q = R7._dual_owner_on_disk()
    v = Venue(e.trade)
    storage_down(monkeypatch)
    e.reconcile(500)
    assert len(v.live()) == 1
    v.x.open_stop_orders = lambda s, strict_algo=False, retry=None: []       # live by status, not in the listing yet
    e = restart(e)
    for _ in range(2): e.reconcile(500)
    assert e.trade.calls.count('stop') == 1 and len(v.live()) == 1           # size unknown -> never a second stop


def test_r9_a_held_orders_emergency_ids_are_owned_by_the_bot_fence(monkeypatch):
    e, cid, exec_q = R7._dual_owner_on_disk()
    owned = e._owned_stop_tags('BTCUSDT')
    for g in range(E.EMG_GENS):
        c, a = E.Engine._emg_ids(cid, g)
        assert f'c:{c}' in owned and f'ac:{a}' in owned                       # never an 'extra' to cancel, never adopted by a lot
