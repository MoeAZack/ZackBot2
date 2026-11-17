"""CAPS-01 r12 (Codex #365 round 11 review, c6933406576): the same-coin new-risk fence is rebuilt from SURVIVING durable
ownership - immediately after a reload and while every typed lookup is UNKNOWN - never from a successful adoption.
Codex's scenario: real dual-owner state on disk plus a small protected same-coin SHORT lot; storage down; the observed
LONG 0.4 gets emergency stop g0; the LONG shrinks to 0.2, g1 is placed and g0's cancel fails (cleanup owner in RAM only);
a new real Engine is built from that disk with storage restored but typed lookups UNKNOWN (the listing still shows both).
Then neither an add nor a manual open_lot on that coin may reach the exchange, until a confirmed scan / cleanup.
Offline fakes only."""
import copy, json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import engine as E
import test_caps01_r7 as R7                                                 # noqa: E402
import test_caps01_r9 as R9                                                 # noqa: E402
from test_safety import SL, SG                                              # noqa: E402

LONG, SHORT = ('BTCUSDT', 'LONG'), ('BTCUSDT', 'SHORT')


def _spy_open(e):
    sent, real = [], e.trade.open
    def open_(s, ps, q, *a, **k): sent.append((s, ps, q)); return real(s, ps, q)
    e.trade.open = open_
    return sent


def _scenario(monkeypatch):
    e, cid, exec_q = R7._dual_owner_on_disk()
    v = R9.Venue(e.trade)
    (u,) = e.state['unconfirmed_entries'].values()
    plan = dict(copy.deepcopy(u['plan']), side='SHORT', qty=0.2, manual=True, risk_usd=0.2)
    e.trade.pos[SHORT] = 0.2
    assert e._create_lot(plan, 0.2, 100.0)                                     # a small, protected same-coin SHORT lot
    sk = e._last_lot_key
    down = R9.storage_down(monkeypatch)
    e.reconcile(500)                                                           # emergency g0 at the observed 0.4
    (g0,) = v.live()
    v.fail_cancel.add(g0[0])
    e.trade.pos[LONG] = 0.2; e.reconcile(500)                                  # shrink: g1 at 0.2, g0's cancel fails
    assert len(v.live()) == 2
    disk = json.load(open(e.F['state'], encoding='utf-8'))
    assert not disk.get('emg_cleanup'), 'the cleanup owner must exist only in RAM for this scenario'
    down[0] = False                                                            # storage restored ...
    real_state = v.stop_state
    v.x.stop_state = lambda s, tag: ('unknown', None)                          # ... typed lookups UNKNOWN
    e2 = R9.restart(e)
    return e2, v, cid, sk, g0, real_state


def test_r12_the_fence_is_present_immediately_after_reload_before_any_reconcile(monkeypatch):
    e, v, cid, sk, g0, _ = _scenario(monkeypatch)
    assert e.stop_missing_block('BTCUSDT') == E.EMG_CLEANUP_BLOCK              # held: a resting record owns the cid
    assert e.stop_missing_block('ETHUSDT') is None                             # an unrelated coin is not fenced
    sent = _spy_open(e)
    with pytest.raises(RuntimeError, match='emergency stop'): e._add_qty(e.state['lots'][sk], 0.1, 100.0, 'pyramid_add')
    assert sent == []


@pytest.mark.parametrize('probe', ['add', 'manual_open_lot'])
def test_r12_codex_probes_after_reload_with_unknown_lookups_send_nothing(monkeypatch, probe):
    e, v, cid, sk, g0, real_state = _scenario(monkeypatch)
    for _ in range(3):
        e.reconcile(500)
        assert v.live(), 'protection must stay live'
        assert not [l for l in e.state['lots'].values() if l['side'] == 'LONG'], 'booked while the claims are unknown'
        assert e.stop_missing_block('BTCUSDT')
    (u,) = e.state['unconfirmed_entries'].values()
    assert u.get('emg_check') and not e.state['resting_entries']               # normalization durable, adoption unknown
    sent = _spy_open(e)
    if probe == 'add':
        with pytest.raises(RuntimeError, match='emergency stop'): e._add_qty(e.state['lots'][sk], 0.1, 100.0, 'pyramid_add')
    else:
        e.S['ENTRY_ORDER'] = 'market'
        with pytest.raises(ValueError, match='emergency stop'):
            e.open_lot(SL, 'BTCUSDT', 'SHORT', SG, None, e.equity(), risk=.001, manual=True)
    assert sent == [] and e.trade.pos[SHORT] == pytest.approx(0.2)
    # release only through confirmed resolution and cleanup
    v.x.stop_state = real_state; v.fail_cancel.clear()
    for _ in range(4): e.reconcile(500)
    assert g0[0] not in [t for t, _ in v.live()]
    assert not e.state['unconfirmed_entries'] and not e.state.get('emg_cleanup') and e.stop_missing_block('BTCUSDT') is None
    assert len([t for t, x in v.live() if x[1] == 'LONG']) == 1                # one LONG stop left (the lot's)
    e.close_lot(sk, 'manual', 100.0)                                            # exits were never fenced
    assert sk not in e.state['lots']


def test_r12_emg_check_alone_fences_and_a_confirmed_scan_releases_it(monkeypatch):
    e, v, cid, sk, g0, real_state = _scenario(monkeypatch)
    e.reconcile(500)
    (u,) = e.state['unconfirmed_entries'].values()
    assert u.get('emg_check')
    cl = e.state.pop('emg_cleanup', None)                                      # even without a cleanup record ...
    assert e.stop_missing_block('BTCUSDT') == E.EMG_CLEANUP_BLOCK              # ... emg_check alone fences the coin
    if cl: e.state['emg_cleanup'] = cl
    disk = json.load(open(e.F['state'], encoding='utf-8'))
    assert cid in (disk.get('emg_cleanup') or {}), 'cleanup ownership is durable as soon as a write succeeds'


def test_r12_a_held_orders_cleanup_owner_is_written_as_soon_as_a_write_succeeds(monkeypatch):
    e, cid, exec_q = R7._dual_owner_on_disk()
    v = R9.Venue(e.trade)
    v.x.stop_state = lambda s, tag: ('unknown', None)                          # nothing can be decided ...
    left = R7._fail_next_state_save(monkeypatch)                               # ... and the handover's own write fails once
    e.reconcile(500)
    assert left == [0] and cid in [r['cid'] for r in e.state['resting_entries'].values()]   # still held
    disk = json.load(open(e.F['state'], encoding='utf-8'))
    assert cid in (disk.get('emg_cleanup') or {})
    assert e.stop_missing_block('BTCUSDT') == E.EMG_CLEANUP_BLOCK
