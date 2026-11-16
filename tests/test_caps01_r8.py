"""CAPS-01 r8 (Codex #365 round 8, c6930700484): a held order (its dual-owner handover not durable) is still PROTECTED.
An older dual-owner state (resting maker record + unconfirmed entry for one order, no provisional stop) is persisted
through the REAL write-ahead and loaded by a REAL Engine; then EVERY state.json write fails (persistent OSError from
save_json). Over several manage + reconcile passes the observed position carries exactly ONE stop at its size and
nothing is booked; once storage recovers the order is settled exactly once: one lot, one stop at the lot's size.
Offline fakes only."""
import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import engine as E
import test_caps01_r7 as R7                                                 # noqa: E402


def _storage_down(monkeypatch):
    real, down, hits = E.save_json, [True], [0]
    def flaky(path, obj, backup=False, strict=False):
        if down[0] and os.path.basename(path) == 'state.json':
            hits[0] += 1; raise OSError('injected: state.json not written (persistent)')
        return real(path, obj, backup=backup, strict=strict)
    monkeypatch.setattr(E, 'save_json', flaky)
    return down, hits


def _stops(e):
    return [v for v in e.trade.stops.values() if v[0] == 'BTCUSDT' and v[1] == 'LONG']


def _held(e, cid):
    return cid in [r['cid'] for r in e.state['resting_entries'].values()]


def test_r8_codex_protection_survives_a_persistent_failed_transfer_then_settles_once(monkeypatch):
    e, cid, exec_q = R7._dual_owner_on_disk()
    (u,) = e.state['unconfirmed_entries'].values()
    assert not u.get('prov') and not _stops(e)                                # Codex's shape: no provisional stop yet
    down, hits = _storage_down(monkeypatch)
    for _ in range(3):
        e.manage(e.trade.marks()); e.reconcile(500)
        assert _held(e, cid), 'the handover cannot be durable while storage is down'
        assert R7._booked(e, exec_q) == [], 'booked before the handover was durable'
        st = _stops(e)
        assert len(st) == 1 and abs(st[0][2] - exec_q) < 1e-9, f'exposure {exec_q} must carry exactly one stop: {st}'
    assert hits[0] > 0 and e.persist_block()                                  # storage really failed (entries blocked)
    rk = next(iter(e.state['resting_entries']))
    e._maker_poll(rk, e.trade.marks())                                        # the poll path adds no second stop
    assert len(_stops(e)) == 1
    down[0] = False                                                           # storage recovers
    time.sleep(1.1)                                                           # distinct lot key (#363 F5, not this PR)
    for _ in range(3): e.manage(e.trade.marks()); e.reconcile(500)
    lots = R7._booked(e, exec_q)
    assert len(lots) == 1 and lots[0]['qty'] == pytest.approx(exec_q)
    st = _stops(e)
    assert len(st) == 1 and abs(st[0][2] - exec_q) < 1e-9 and lots[0]['stop_id']
    assert not e.state['resting_entries'] and not e.state['unconfirmed_entries'] and not e.untracked
    assert not e.persist_block()


def test_r8_held_order_with_no_exposure_places_no_stop(monkeypatch):
    e, cid, exec_q = R7._dual_owner_on_disk()
    e.trade.pos.pop(('BTCUSDT', 'LONG'), None)                               # nothing executed on Binance (yet)
    _storage_down(monkeypatch)
    e.reconcile(500)
    assert _held(e, cid) and not _stops(e) and R7._booked(e, exec_q) == []


def test_r8_held_order_stop_covers_all_the_order_produced_not_only_the_entry_size(monkeypatch):
    e, cid, exec_q = R7._dual_owner_on_disk()
    (u,) = e.state['unconfirmed_entries'].values()
    u['qty'] = round(exec_q / 4, 6)                     # older shape: the entry was written for a remainder only, but the
    down, _ = _storage_down(monkeypatch)                # resting record's order (same client id) produced the whole position
    e.reconcile(500)
    st = _stops(e)
    assert _held(e, cid) and len(st) == 1 and abs(st[0][2] - exec_q) < 1e-9, f'under-protected: {st} vs {exec_q}'
