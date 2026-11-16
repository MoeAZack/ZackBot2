"""CAPS-01 r7 (Codex #365 round 7, c6930302155): one client id is booked at most once. A dual-owner state (a resting
maker record AND an unconfirmed entry for one order - older code's mid-handover shape) is persisted through the REAL
write-ahead and loaded by a REAL Engine; when the transfer's own write fails (one OSError from save_json), reconcile
must not settle the unconfirmed entry while the resting record still owns the order, and a later maker poll must not
book it either: exactly one 0.4 lot for a 0.4 position, in either order. Offline fakes only."""
import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import engine as E
import test_caps01_r6 as R6                                                 # noqa: E402


def _dual_owner_on_disk():
    """Crash after the real handover write, reload, re-create the older dual-owner shape and persist it through the real
    write-ahead; returns a third, freshly constructed engine loaded from that disk state."""
    e, bk, data_dir, cid, _, exec_q = R6._to_crash(False, 'wal')
    e2 = R6._engine_on(data_dir, e.trade)
    (u,) = e2.state['unconfirmed_entries'].values()
    rk = 'ME|T|BTCUSDT|LONG'
    e2.state['resting_entries'][rk] = dict(key=rk, sleeve='T', symbol='BTCUSDT', side='LONG', qty=u['qty'], filled=0.0, cost=0.0,
                                           n=1, t0=time.time() - 90, status='cancelling', cid=cid, price=99.9,
                                           placed_t=time.time() - 60, cancel_t=time.time() - 30, plan=u['plan'])
    e2._save_wal()                                                            # the real write-ahead
    e3 = R6._engine_on(data_dir, e.trade)
    assert cid in [r['cid'] for r in e3.state['resting_entries'].values()]
    assert cid in [x['cid'] for x in e3.state['unconfirmed_entries'].values()]
    e3.connect(); e3.marks = e.trade.marks()
    e3.trade.get_order = bk.get                                               # final: CANCELED, 40 % executed
    return e3, cid, exec_q


def _fail_next_state_save(monkeypatch):
    real, left = E.save_json, [1]
    def flaky(path, obj, backup=False, strict=False):
        if left[0] and os.path.basename(path) == 'state.json':
            left[0] -= 1; raise OSError('injected: state.json not written')
        return real(path, obj, backup=backup, strict=strict)
    monkeypatch.setattr(E, 'save_json', flaky)
    return left


def _booked(e, exec_q):
    lots = [l for l in e.state['lots'].values() if l['symbol'] == 'BTCUSDT' and l['side'] == 'LONG']
    assert sum(l['qty'] for l in lots) <= exec_q + 1e-9, f'booked twice: {[l["qty"] for l in lots]} vs position {exec_q}'
    return lots


def test_r7_codex_failed_normalization_never_settles_until_the_transfer_is_durable(monkeypatch):
    e, cid, exec_q = _dual_owner_on_disk()
    left = _fail_next_state_save(monkeypatch)
    e.reconcile(500)                                                          # normalization's strict save fails once
    assert left == [0], 'the injected failure was not hit'
    assert cid in [r['cid'] for r in e.state['resting_entries'].values()]     # the resting record still owns the order
    assert _booked(e, exec_q) == [], 'settled while a resting record still owned the order'
    time.sleep(3)                                                             # later passes, distinct lot keys (not F5)
    for _ in range(3): e.manage(e.trade.marks()); e.reconcile(500)
    lots = _booked(e, exec_q)
    assert len(lots) == 1 and lots[0]['qty'] == pytest.approx(exec_q)
    st = [v for v in e.trade.stops.values() if v[0] == 'BTCUSDT' and v[1] == 'LONG']
    assert len(st) == 1 and abs(st[0][2] - exec_q) < 1e-9
    assert not e.state['resting_entries'] and not e.state['unconfirmed_entries'] and not e.untracked


def test_r7_reverse_order_the_maker_poll_first_then_reconcile_books_once():
    e, cid, exec_q = _dual_owner_on_disk()
    e.manage(e.trade.marks())                                                 # the maker poll runs first (transfer), then reconcile
    time.sleep(3)
    for _ in range(3): e.reconcile(500); e.manage(e.trade.marks())
    lots = _booked(e, exec_q)
    assert len(lots) == 1 and lots[0]['qty'] == pytest.approx(exec_q)


def test_r7_reverse_order_with_a_failed_poll_transfer_books_once(monkeypatch):
    e, cid, exec_q = _dual_owner_on_disk()
    left = _fail_next_state_save(monkeypatch)
    rk = next(iter(e.state['resting_entries']))
    e._maker_poll(rk, e.trade.marks())                                        # the poll's transfer fails ...
    assert left == [0] and rk in e.state['resting_entries']
    e.reconcile(500)                                                          # ... reconcile (its own transfer may succeed now)
    _booked(e, exec_q)                                                        # never more than the position
    time.sleep(3)
    for _ in range(3): e.manage(e.trade.marks()); e.reconcile(500)
    lots = _booked(e, exec_q)
    assert len(lots) == 1 and lots[0]['qty'] == pytest.approx(exec_q)
