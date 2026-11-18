"""CAPS-01 r10 (Codex #365 round 9 review, c6932155163): typed emergency-stop resolution and durable cleanup.
- P1 (i): a live generation listed under its exact deterministic id, whose direct lookup is UNKNOWN, never unlocks the
  next generation (no second stop);
- P1 (ii): a generation that fires while it is being looked up never leads to a stop sized on the stale position;
- ruling a: only an explicit absence unlocks a generation; unknown is an explicit incident, never reported safe;
- ruling b: a -4116 duplicate id is recovered, never a reason to advance to another generation;
- P2 / hand-over: a leftover generation whose cancel failed stays owned (on disk, across settlement and a restart),
  is retried, and fences new risk on the coin (manual entries included) until Binance confirms it gone.
Real Engine, real on-disk dual-owner state, persistent state.json failure; offline venue fake only."""
import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import json
import pytest
import binance_client as BC
import engine as E
import test_caps01_r7 as R7                                                 # noqa: E402
import test_caps01_r9 as R9                                                 # noqa: E402
from test_safety import SL                                                  # noqa: E402

POS = ('BTCUSDT', 'LONG')


def _setup(monkeypatch, mode='classic'):
    e, cid, exec_q = R7._dual_owner_on_disk()
    v = R9.Venue(e.trade, mode)
    down = R9.storage_down(monkeypatch)
    return e, v, down, cid, exec_q


def _ids(cid, g): return E.Engine._emg_ids(cid, g)


def _incident(e, prefix):
    return [k for k, i in (e.health.get('incidents') or {}).items() if k.startswith(prefix) and (i or {}).get('open')]


@pytest.mark.parametrize('says', ['unknown', 'absent'])
@pytest.mark.parametrize('restart', [False, True])
def test_r10_codex_i_listed_live_with_unknown_lookup_never_sends_the_next_generation(monkeypatch, restart, says):
    e, v, down, cid, exec_q = _setup(monkeypatch)
    e.reconcile(500)
    (g0,) = v.live()
    g0ids = {f'c:{_ids(cid, 0)[0]}', f'ac:{_ids(cid, 0)[1]}'}
    real_st, real_ss = v.stop_state, v.stop_status                           # g0's direct lookups say UNKNOWN ...
    v.x.stop_state = lambda s, tag: ((says, None) if says == 'absent' else ('unknown', 'UNKNOWN')) if tag in g0ids else real_st(s, tag)
    v.x.stop_status = lambda s, tag, retry=None: (None if says == 'absent' else 'UNKNOWN') if tag in g0ids else real_ss(s, tag)
    if restart: e = R9.restart(e)
    for _ in range(3): R9.one_pass(e, v, exec_q)                              # ... the detailed listing shows g0 live
    assert v.live() == [g0] and e.trade.calls.count('stop') == 1
    assert _ids(cid, 1)[0] not in v.by_id and _ids(cid, 1)[1] not in v.by_id


@pytest.mark.parametrize('listed', [True, False], ids=['fires-after-listing', 'fires-before-listing'])
def test_r10_codex_ii_a_generation_firing_during_the_scan_never_sends_on_a_stale_size(monkeypatch, listed):
    e, v, down, cid, exec_q = _setup(monkeypatch)
    e.reconcile(500)
    (g0,) = v.live()
    def fire():                                                               # g0 executes: the position goes flat
        v.st[g0[0]] = 'FILLED'; v.x.stops.pop(g0[0], None); v.x.pos[POS] = 0.0
    if listed:
        real = v.stop_state
        def lookup(s, tag):
            if v.st.get(g0[0]) == 'NEW': fire()
            return real(s, tag)
        v.x.stop_state = lookup
    else:
        real_l = v.open_stop_orders
        def listing(s, strict_algo=False, retry=None):
            if v.st.get(g0[0]) == 'NEW': fire()
            return real_l(s, strict_algo, retry)
        v.x.open_stop_orders = listing
    live = {POS: exec_q}                                                      # the pass's position snapshot: 0.4
    e._settle_unconfirmed(live)
    assert e.trade.calls.count('stop') == 1, 'a second stop was sent on the stale 0.4'
    for _ in range(3): e.reconcile(500)                                       # fresh reads: flat -> nothing to protect
    assert e.trade.calls.count('stop') == 1 and not v.live()


def test_r10_ruling_a_unknown_is_never_absence_and_is_an_explicit_incident(monkeypatch):
    e, v, down, cid, exec_q = _setup(monkeypatch)
    v.x.stop_state = lambda s, tag: ('unknown', None)                         # unsupported / malformed / missing status
    for _ in range(2): R9.one_pass(e, v, exec_q)
    assert e.trade.calls.count('stop') == 0
    assert _incident(e, 'emg-unprotected|'), 'uncovered exposure must be an open incident, never reported safe'


def test_r10_client_typed_lookup_only_counts_the_explicit_no_such_order_as_absent():
    f = BC.Futures.__new__(BC.Futures)
    def answer(x):
        def req(*a, **k):
            if isinstance(x, Exception): raise x
            return x
        return req
    cases = [(BC.BinanceError(-2013, 'Order does not exist.'), 'absent'),
             (BC.BinanceError(-5000, 'Path /fapi/v1/algoOrder, Method GET is invalid'), 'unknown'),   # unsupported
             (BC.BinanceError(-1021, 'Timestamp outside recvWindow'), 'unknown'),
             (BC.BinanceError(-503, 'busy'), 'unknown'), (TimeoutError('t'), 'unknown'),
             ({}, 'unknown'), ({'status': None}, 'unknown'), ({'status': 'WEIRD'}, 'unknown'), ([], 'unknown'),
             ({'status': 'NEW'}, 'live'), ({'status': 'CANCELED'}, 'gone'), ({'status': 'FILLED'}, 'fired')]
    for x, want in cases:
        f._req = answer(x)
        assert f.stop_state('BTCUSDT', 'c:zbabc')[0] == want, (x, want)
    f._req = answer({'algoStatus': 'NEW'}); assert f.stop_state('BTCUSDT', 'ac:zaabc')[0] == 'live'
    f._req = answer({'algoStatus': 'TRIGGERED'}); assert f.stop_state('BTCUSDT', 'ac:zaabc')[0] == 'fired'
    f._req = answer(BC.BinanceError(-1404, 'not supported')); assert f.stop_state('BTCUSDT', 'ac:zaabc')[0] == 'unknown'
    assert f.stop_state('BTCUSDT', 'x:1')[0] == 'unknown'


@pytest.mark.parametrize('mode', ['classic', 'algo'])
def test_r10_ruling_b_a_duplicate_id_is_recovered_never_a_new_generation(monkeypatch, mode):
    e, v, down, cid, exec_q = _setup(monkeypatch, mode)
    e.reconcile(500)
    (g0,) = v.live()
    hide = {'on': True}                                                        # g0 exists, but lookups/listing miss it
    real_s, real_l = v.stop_state, v.open_stop_orders
    v.x.stop_state = lambda s, tag: ('absent', None) if hide['on'] else real_s(s, tag)
    v.x.open_stop_orders = lambda s, strict_algo=False, retry=None: [] if hide['on'] else real_l(s, strict_algo, retry)
    e = R9.restart(e)
    for _ in range(3): R9.one_pass(e, v, exec_q)                               # re-send g0's own ids -> -4116 -> recovered
    assert e.trade.calls.count('stop') == 2, 'one same-id retry (-4116), then recovered - never re-sent'
    assert v.live() == [g0] and all(_ids(cid, 1)[i] not in v.by_id for i in (0, 1))
    hide['on'] = False
    R9.one_pass(e, v, exec_q)
    assert v.live() == [g0]
    (u,) = e.state['unconfirmed_entries'].values()
    assert u['prov'] in e._emg_tags(cid) and u['prov_qty'] == pytest.approx(exec_q)


@pytest.mark.parametrize('restart', [False, True])
def test_r10_codex_p2_a_leftover_from_a_failed_resize_stays_owned_and_fenced_past_settlement(monkeypatch, restart):
    e, v, down, cid, exec_q = _setup(monkeypatch)
    half = round(exec_q / 2, 3)
    v.x.pos[POS] = half; e.reconcile(500)                                      # g0 at 0.2
    (g0,) = v.live(); assert g0[1][2] == pytest.approx(half)
    v.fail_cancel.add(g0[0])                                                   # its cancel will not be answered
    v.x.pos[POS] = exec_q; e.reconcile(500)                                    # g1 at 0.4 first, then g0's cancel fails
    assert {t[1][2] for t in v.live()} == {half, exec_q}
    down[0] = False; time.sleep(1.1)
    for _ in range(3): e.manage(e.trade.marks()); e.reconcile(500)             # settled: one lot, its own stop
    lots = R7._booked(e, exec_q)
    assert len(lots) == 1 and lots[0]['qty'] == pytest.approx(exec_q) and not e.state['unconfirmed_entries']
    assert g0[0] in [t for t, _ in v.live()], 'the leftover is still live on Binance'
    disk = json.load(open(e.F['state'], encoding='utf-8'))
    assert cid in (disk.get('emg_cleanup') or {}), 'cleanup ownership must be durable'
    if restart: e = R9.restart(e)
    blk = e.entry_block(SL, 'BTCUSDT', 'SHORT', manual=True)
    assert blk and 'emergency stop' in blk                                     # same-coin new risk fenced, manual included
    assert e.stop_missing_on('BTCUSDT')
    for _ in range(2): e.reconcile(500)
    assert g0[0] in [t for t, _ in v.live()] and e.entry_block(SL, 'BTCUSDT', 'SHORT', manual=True)   # retried, still fenced
    v.fail_cancel.clear()
    for _ in range(3): e.reconcile(500)
    assert g0[0] not in [t for t, _ in v.live()]
    assert not e.state.get('emg_cleanup') and not e.stop_missing_on('BTCUSDT')
    st = [t for t, _ in v.live()]
    assert st == [lots[0]['stop_id']] or st == [e.state['lots'][next(iter(e.state['lots']))]['stop_id']]
