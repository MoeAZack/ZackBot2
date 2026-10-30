"""CAND-EVICT-ACK-01 (signed, Codex 6517958103; pin amendment 6518282215): the Integration acknowledgement of an evicted
candidate pending-cost index entry - T1-T20 + T11a of contracts/cand_evict_ack_01. Synthetic data only
(tests/test_safety.FakeX engine, PAPER); never reads market data, settings or state of a real bot."""
import copy
import hashlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_factory_cand01 import F01, F02, on, rot, slot                       # noqa: E402,F401  (fixtures + helpers)
from test_factory_cand02_family_safety import _close_f01, _other_close, _reenable   # noqa: E402
from test_safety import mk_engine                                             # noqa: E402
import cand_evict_ack as CEA                                                  # noqa: E402
import engine as E                                                            # noqa: E402
import f01_canary as FC                                                       # noqa: E402

KEY, ACK = E.CANDIDATE_PENDING_KEY, CEA.ACK_KEY
CID = '6518282215'
OLD = '2026-10-01T00:00:00+00:00'


def body(rid, reason='evicted before settlement; exchange records reviewed', cid=CID):
    return dict(row_id=rid, reason=reason, comment_id=cid)


def evicted(e, rid='lot-evicted-1', sleeve=F01, symbol='BTCUSDT', closed=OLD, save=True):
    """An index entry whose row is not in history (the evicted case), persisted."""
    e.state.setdefault(KEY, {})[rid] = dict(sleeve=sleeve, symbol=symbol, closed=closed)
    if save: assert e.save_state()
    return rid


def disk_state(e):
    with open(e.F['state'], encoding='utf-8') as fh:
        return json.load(fh)


def strict(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                                     allow_nan=False).encode('ascii')).hexdigest()


# ------------------------------------------------------------------ T1 / T2: the real eviction, cleared by the ack only
def test_t1_t2_a_really_evicted_entry_is_cleared_by_the_ack_and_no_slot_is_re_enabled(on):
    e, tmp = on
    k, row = _close_f01(e)
    e.history[:] = [row] + [dict(id=f'x{i}', sleeve='ema_mom', symbol='ETHUSDT', closed=row['closed']) for i in range(3000)]
    _other_close(e)                                                   # the trim evicts the candidate row
    assert all(h.get('id') != k for h in e.history) and k in e.state[KEY]
    t = E.pd.Timestamp(row['closed']).timestamp() + E.CANDIDATE_COST_PENDING_S
    assert e.candidate_guard(now=t) == [F01, F02]                     # fail-closed (OQ6)
    disabled = copy.deepcopy(e.state['candidate_disabled'])
    ok, msg, rec = e.ack_evicted_pending(body(k))
    assert ok, msg
    assert k not in e.state[KEY] and e.state[ACK][k] == rec and rec['comment_id'] == CID and rec['by'] == 'integration'
    assert rec['entry'] == dict(sleeve=F01, symbol='BTCUSDT', closed=CEA.canonical_closed(row['closed']))
    assert rec['entry']['closed'] is not None
    assert e._candidate_pending_why(F01, t + 10 ** 6) is None         # the index no longer yields a reason
    # T2: nothing switched back on, the disable reasons are untouched
    assert not slot(e, F01)['enabled'] and not slot(e, F02)['enabled']
    assert e.state['candidate_disabled'] == disabled
    _reenable(e)                                                      # a separate Integration action
    assert e.candidate_guard(now=t + 10 ** 6) == []


# ------------------------------------------------------------------ T3-T6, T8: refusals change nothing
def test_t3_an_unknown_row_id_is_refused_and_state_is_byte_identical(on):
    e, _ = on
    evicted(e)
    before = json.dumps(e.state, sort_keys=True, default=str)
    ok, msg, rec = e.ack_evicted_pending(body('no-such-row'))
    assert not ok and 'unknown' in msg and rec is None
    assert json.dumps(e.state, sort_keys=True, default=str) == before and ACK not in e.state


def test_t4_a_row_still_in_history_is_refused_as_not_evicted(on):
    e, _ = on
    k, row = _close_f01(e)
    ok, msg, _ = e.ack_evicted_pending(body(k))
    assert not ok and 'not evicted' in msg and k in e.state[KEY] and ACK not in e.state


def test_t5_a_second_call_is_idempotent_with_no_save_and_the_first_record_stands(on, monkeypatch):
    e, _ = on
    rid = evicted(e)
    ok, _, first = e.ack_evicted_pending(body(rid))
    assert ok
    first = copy.deepcopy(first)
    saves = []
    monkeypatch.setattr(e, 'save_state', lambda: saves.append(1) or True)
    ok, msg, rec = e.ack_evicted_pending(body(rid))
    assert ok and 'already acknowledged' in msg and rec == first and saves == []
    ok, msg, rec = e.ack_evicted_pending(body(rid, reason='other', cid='6599999999'))
    assert ok and 'already acknowledged (comment 6518282215)' in msg and '6599999999 not recorded' in msg
    assert e.state[ACK][rid] == first and saves == []


@pytest.mark.parametrize('b', [
    dict(row_id='r', reason='x', comment_id='abc123'), dict(row_id='r', reason='x', comment_id=''),
    dict(row_id='r', reason='x', comment_id='12345'), dict(row_id='r', reason='x', comment_id='1234567890123'),
    dict(row_id='r', reason='x', comment_id=6518282215), dict(row_id='r', reason='   ', comment_id=CID),
    dict(row_id='r', reason='x' * 201, comment_id=CID), dict(reason='x', comment_id=CID),
    dict(row_id='r', reason='x', comment_id=CID, net=1.0), dict(row_id=['r', 's'], reason='x', comment_id=CID),
    dict(row_id='', reason='x', comment_id=CID), dict(row_id='r', comment_id=CID), ['r'],
])
def test_t6_malformed_requests_are_refused(on, b):
    e, _ = on
    evicted(e, rid='r')
    before = copy.deepcopy(e.state)
    ok, msg, rec = e.ack_evicted_pending(b)
    assert not ok and rec is None and e.state == before


def test_t8_a_live_engine_refuses(on):
    e, _ = on
    rid = evicted(e)
    e.live = True
    ok, msg, _ = e.ack_evicted_pending(body(rid))
    assert not ok and 'PAPER-only' in msg and rid in e.state[KEY]


# ------------------------------------------------------------------ T7: a failed save
def test_t7a_save_returns_false_rolls_back_the_two_subtrees_exactly(on, monkeypatch):
    e, _ = on
    evicted(e, rid='a'); rid = evicted(e, rid='b'); evicted(e, rid='c')
    idx0 = list(e.state[KEY].items())
    monkeypatch.setattr(e, 'save_state', lambda: False)
    ok, msg, rec = e.ack_evicted_pending(body(rid))
    assert not ok and 'rolled back' in msg and 'AUD-05' in msg and rec is None
    assert list(e.state[KEY].items()) == idx0                         # same entries, same order
    assert ACK not in e.state                                         # the newly created empty dict is gone
    assert rid in disk_state(e)[KEY]


def test_t7b_a_write_exception_latches_aud05_and_the_latch_remains(on, monkeypatch):
    e, _ = on
    rid = evicted(e)
    e.state[ACK] = {}                                                 # pre-existing (empty) record dict: kept
    assert e.save_state()
    monkeypatch.setattr(E, 'save_json', lambda *a, **k: (_ for _ in ()).throw(OSError('disk full')))
    ok, msg, _ = e.ack_evicted_pending(body(rid))
    assert not ok
    assert e.state[KEY] == {rid: dict(sleeve=F01, symbol='BTCUSDT', closed=OLD)} and e.state[ACK] == {}
    assert 'state' in getattr(e, 'state_untrusted', {}) and e.S.get('ENTRIES_PAUSED') is True   # expected, and stays
    monkeypatch.undo()
    assert rid in disk_state(e)[KEY] and not disk_state(e).get(ACK)


# ------------------------------------------------------------------ T9 / T11 / T11a: content-strict record
@pytest.mark.parametrize('entry,want', [
    (dict(sleeve=F01, symbol='BTCUSDT', closed='2026-10-01 12:00:00'),
     dict(sleeve=F01, symbol='BTCUSDT', closed='2026-10-01T12:00:00+00:00')),
    (dict(sleeve='c-f01', symbol='8389765432123', closed='not a time', orderId=8389765432123, nested=dict(tradeId=7)),
     dict(sleeve=None, symbol=None, closed=None)),
    (dict(sleeve='C-F01 ', symbol='x-ab12cd', closed=12345), dict(sleeve=None, symbol=None, closed=None)),
    (dict(sleeve=F02, symbol='btcusdt', closed='2026-10-01T15:00:00+03:00'),
     dict(sleeve=F02, symbol=None, closed='2026-10-01T12:00:00+00:00')),
    (dict(sleeve=F01, symbol='BTCUSDT/1', closed='NaT'), dict(sleeve=F01, symbol=None, closed=None)),
    (dict(sleeve=F01, symbol='BTCUSDT\n', closed=1759320000.0), dict(sleeve=F01, symbol=None, closed=None)),
    (['C-F01', 'BTCUSDT'], dict(sleeve=None, symbol=None, closed=None)),
    ('clientOrderId=x-123', dict(sleeve=None, symbol=None, closed=None)),
])
def test_t9_t11_a_malformed_entry_is_acked_with_content_strict_fields_only(on, entry, want):
    e, _ = on
    e.state.setdefault(KEY, {})['m'] = copy.deepcopy(entry)
    ok, msg, rec = e.ack_evicted_pending(body('m'))
    assert ok, msg
    assert rec['entry'] == want and set(rec) == {'row_id', 'entry', 'entry_sha256', 'reason', 'comment_id', 't', 'by'}
    assert rec['entry_sha256'] == strict(entry)
    assert FC.id_fields(rec) == [] and FC.id_fields(e.state[ACK]) == []        # T9: no id key survives
    blob = json.dumps(e.state[ACK])
    assert '8389765432123' not in blob and 'x-123' not in blob and 'x-ab12cd' not in blob
    assert 'm' not in e.state[KEY]


@pytest.mark.parametrize('entry', [dict(sleeve=F01, symbol={'BTCUSDT'}), dict(sleeve=F01, closed=float('nan')),
                                   dict(sleeve=F01, x=object()), dict(sleeve=F01, closed=float('inf')), {1: 'a', 'b': 2}])
def test_t11a_a_non_serializable_original_is_refused_and_kept(on, entry, monkeypatch):
    e, _ = on
    e.state.setdefault(KEY, {})['n'] = entry
    saves = []
    monkeypatch.setattr(e, 'save_state', lambda: saves.append(1) or True)
    ok, msg, rec = e.ack_evicted_pending(body('n'))
    assert not ok and 'not strictly JSON-serializable' in msg and rec is None
    assert e.state[KEY]['n'] is entry and ACK not in e.state and saves == []
    with pytest.raises((TypeError, ValueError)):
        CEA.strict_sha256(entry)


# ------------------------------------------------------------------ T10: no P&L, history, settings or evidence change
def test_t10_figures_history_and_settings_are_unchanged(on):
    e, _ = on
    k, row = _close_f01(e)
    rid = evicted(e)
    hist, S = copy.deepcopy(e.history), copy.deepcopy(e.S)
    rest = {k_: copy.deepcopy(v) for k_, v in e.state.items() if k_ not in (KEY, ACK)}
    assert e.ack_evicted_pending(body(rid))[0]
    assert e.history == hist and e.S == S
    assert {k_: v for k_, v in e.state.items() if k_ not in (KEY, ACK)} == rest
    assert 'px_ack' not in row                                          # no FILL-PNL-02 settlement written


# ------------------------------------------------------------------ T12 / T13
def test_t12_an_unreadable_index_is_refused_and_still_fails_closed(on):
    e, _ = on
    e.state[KEY] = ['not', 'a', 'dict']
    ok, msg, _ = e.ack_evicted_pending(body('x'))
    assert not ok and 'unreadable' in msg and ACK not in e.state
    assert sorted(e.candidate_guard()) == [F01, F02]


def test_t13_the_ack_survives_a_restart(on):
    e, tmp = on
    rid = evicted(e)
    ok, _, rec = e.ack_evicted_pending(body(rid))
    assert ok
    e2, _ = mk_engine(tmp)                                            # restart WITHOUT an explicit save
    assert rid not in (e2.state.get(KEY) or {}) and e2.state[ACK][rid] == rec


# ------------------------------------------------------------------ T14: the F01 canary (A7 / OQ1)
START = datetime(2026, 10, 10, tzinfo=timezone.utc)


def _cfg():
    return dict(on=True, start_utc=START.isoformat(timespec='seconds'))


def _acked(closed, sleeve=F01):
    return {'r1': dict(row_id='r1', entry=dict(sleeve=sleeve, symbol='BTCUSDT', closed=closed), entry_sha256='0' * 64,
                       reason='x', comment_id=CID, t=OLD, by='integration')}


def test_t14_an_acked_evicted_c_f01_close_after_the_start_counts_and_blocks_unmeasured(monkeypatch):
    monkeypatch.setattr(FC, 'cfg_problems', lambda cfg, repo_root=None: [])
    now = START + timedelta(days=2)
    after = (START + timedelta(hours=5)).isoformat()
    st = FC.bound_status(_cfg(), [], {}, now, acked=_acked(after))
    assert st['closed'] == 1 and st['net_r'] == 0.0 and st['drawdown_r'] == 0.0 and st['stop'] is None
    assert st['block'] == ('F01 canary: 1 evicted C-F01 close(s) acknowledged without a measured net - drawdown not '
                           'measurable, no new C-F01 entry (fail closed)')
    assert FC.bound_status(_cfg(), [], {}, now, acked=_acked(None))['block'] == st['block']       # unreadable close
    before = (START - timedelta(hours=5)).isoformat()                                            # a later frozen start excludes it
    assert FC.bound_status(_cfg(), [], {}, now, acked=_acked(before)) == FC.bound_status(_cfg(), [], {}, now)
    assert FC.bound_status(_cfg(), [], {}, now, acked=_acked(after, F02))['block'] is None        # C-F01 only
    many = {f'r{i}': _acked(after)['r1'] for i in range(FC.MAX_CLOSED_TRADES)}
    st8 = FC.bound_status(_cfg(), [], {}, now, acked=many)
    assert st8['closed'] == FC.MAX_CLOSED_TRADES and st8['stop']['id'] == 'S6'                    # the 8-close bound


def test_t14_the_engine_passes_the_ack_record_to_the_canary(on, monkeypatch):
    e, _ = on
    seen = {}
    monkeypatch.setattr(FC, 'config', lambda S: _cfg())
    monkeypatch.setattr(FC, 'env_refusal', lambda *a: None)
    real = FC.bound_status
    monkeypatch.setattr(FC, 'bound_status', lambda *a, **k: seen.update(k) or real(*a, **k))
    rid = evicted(e)
    assert e.ack_evicted_pending(body(rid))[0]
    e.f01_canary_block(slot(e, F01))
    assert seen['acked'] is e.state[ACK]


# ------------------------------------------------------------------ T15 / T16
def test_t15_a_settlement_clear_writes_no_ack_record(on, monkeypatch):
    e, _ = on
    real = E.AC.row_view
    monkeypatch.setattr(E.AC, 'row_view', lambda r: dict(status='measured', pnl=0.0, r=None, why=[]) if r.get('_settled') else real(r))
    k, row = _close_f01(e)
    row['_settled'] = True
    e._cost_final('row', row)
    assert k not in e.state[KEY] and ACK not in e.state


def test_t16_time_never_clears_an_entry(on):
    e, _ = on
    rid = evicted(e)
    far = time.time() + 10 * 365 * 86400
    e.candidate_guard(now=far)
    assert rid in e.state[KEY] and ACK not in e.state


# ------------------------------------------------------------------ T17: guard / ack interleaving under self.lock
def test_t17_a_direct_guard_call_blocks_while_another_thread_holds_the_lock(on):
    e, _ = on
    held, release, done = threading.Event(), threading.Event(), threading.Event()

    def holder():
        with e.lock:
            held.set(); release.wait(5)

    def guard():
        e.candidate_guard(); done.set()

    h = threading.Thread(target=holder); h.start(); assert held.wait(5)
    g = threading.Thread(target=guard); g.start()                    # a DIRECT call: no caller holds the lock, not via cycle()
    assert not done.wait(0.3)                                         # blocked on self.lock
    release.set(); h.join(5); g.join(5)
    assert done.is_set()


@pytest.mark.parametrize('save_ok', [True, False])
def test_t17_the_guard_never_sees_a_half_state(on, monkeypatch, save_ok):
    e, _ = on
    now_iso = datetime.now(timezone.utc).isoformat(timespec='seconds')     # recent: the guard keeps reading, never disables
    rids = [evicted(e, rid=f'i{i}', closed=now_iso) for i in range(6)]
    obs, real_why = [], e._candidate_pending_why

    def why(sid, now):
        idx, acks = e.state.get(KEY) or {}, e.state.get(ACK) or {}
        obs.extend((r in idx, r in acks) for r in rids)
        return real_why(sid, now)

    monkeypatch.setattr(e, '_candidate_pending_why', why)
    in_save = threading.Event()

    def slow_save():
        in_save.set(); time.sleep(0.05)                               # the ack is mid-step (entry popped, record written)
        return save_ok

    monkeypatch.setattr(e, 'save_state', slow_save)
    stop = threading.Event()

    def guard_loop():
        while not stop.is_set(): e.candidate_guard()                  # direct calls from the competing thread

    g = threading.Thread(target=guard_loop); g.start()
    try:
        for r in rids:
            in_save.clear()
            assert e.ack_evicted_pending(body(r))[0] is save_ok
            assert in_save.is_set()
    finally:
        stop.set(); g.join(5)
    assert obs and all(o in ((True, False), (False, True)) for o in obs)      # before or after, never popped-without-record
    assert all(sl['enabled'] for sl in e.S['SLEEVES'] if sl['id'] in E.CANDIDATE_SLOTS)


# ------------------------------------------------------------------ T18 / T19 / T20
def test_t18_no_cohort_evidence_or_network_write(on, monkeypatch):
    e, tmp = on
    rid = evicted(e)

    def snap():
        out = {}
        for d, _, fs in os.walk(tmp):
            for f in fs:
                p = os.path.join(d, f)
                with open(p, 'rb') as fh: out[os.path.relpath(p, tmp)] = hashlib.sha256(fh.read()).hexdigest()
        return out

    import socket
    import subprocess
    import urllib.request
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network / process call from the ack path'))
    monkeypatch.setattr(socket, 'create_connection', boom)
    monkeypatch.setattr(urllib.request, 'urlopen', boom)
    monkeypatch.setattr(subprocess, 'run', boom)
    a = snap()
    assert e.ack_evicted_pending(body(rid))[0]
    b = snap()
    changed = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
    assert changed and all(os.path.basename(k).startswith('state.json') for k in changed), changed
    src = open(CEA.__file__, encoding='utf-8').read()
    for mod in ('import socket', 'import urllib', 'import requests', 'import subprocess', 'gh api', 'cohort'):
        assert mod not in src.split('"""', 2)[2], mod


def test_t19_the_same_path_acks_a_c_f02_entry(on):
    e, _ = on
    rid = evicted(e, rid='f02-row', sleeve=F02, symbol='ETHUSDT')
    ok, _, rec = e.ack_evicted_pending(body(rid))
    assert ok and rec['entry']['sleeve'] == F02 and rid not in e.state[KEY]


def test_t20_the_ack_record_is_unbounded(on, monkeypatch):
    e, _ = on
    monkeypatch.setattr(e, 'save_state', lambda: True)
    for i in range(1000):
        evicted(e, rid=f'u{i}', save=False)
        assert e.ack_evicted_pending(body(f'u{i}'))[0]
    assert len(e.state[ACK]) == 1000 and e.state[KEY] == {}


# ------------------------------------------------------------------ the HMAC panel API (app.handle behind do_POST's token)
def test_the_paper_api_route(on, monkeypatch):
    import app as A
    import types
    e, _ = on
    monkeypatch.setattr(A, 'APP', types.SimpleNamespace(engine=e))
    rid = evicted(e)
    with pytest.raises(ValueError):
        A.handle(CEA.ROUTE, dict(row_id=rid, reason='x', comment_id='nope'))
    out = A.handle(CEA.ROUTE, body(rid))
    assert out['row_id'] == rid and out['record']['comment_id'] == CID and rid not in e.state[KEY]
    with pytest.raises(ValueError):
        A.handle(CEA.ROUTE, body('unknown-row'))


def test_the_pin_amendment_is_the_only_engine_pin_change():
    am = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'contracts',
                                     'cand_evict_ack_01', 'pin_amendment.v1.json'), encoding='utf-8'))
    assert set(am['amended']) == {'engine.py:Engine.candidate_guard'}
    got = FC.function_pins(['engine.py:Engine.candidate_guard'])
    assert got['engine.py:Engine.candidate_guard'] == am['amended']['engine.py:Engine.candidate_guard']['to']


# ------------------------------------------------------------------ Codex 6518560081: the cohort manifest correction
def test_the_cohort_manifest_changes_only_the_candidate_guard_pin_and_the_preflight_accepts_it():
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rel = 'contracts/factory_f01_impl_01/cohort_manifest.v1.json'
    cur = json.load(open(os.path.join(root, rel), encoding='utf-8'))['pins_expected']
    try:
        old = json.loads(subprocess.run(['git', '-C', root, 'show', f'e7e6550:{rel}'], capture_output=True, check=True,
                                        timeout=30).stdout.decode('utf-8'))['pins_expected']
    except (OSError, subprocess.SubprocessError, ValueError):
        pytest.skip('git history of the manifest (master e7e6550) not available')
    am = json.load(open(os.path.join(root, 'contracts', 'cand_evict_ack_01', 'pin_amendment.v1.json'), encoding='utf-8'))
    (gk, ch), = am['amended'].items()
    real = lambda d: {k: v for k, v in d.items() if not k.startswith('_')}
    assert gk == 'engine.py:Engine.candidate_guard' and old[gk] == ch['from'] and cur[gk] == ch['to']
    assert set(real(cur)) == set(real(old)) and len(real(cur)) == 34
    assert {k for k in real(cur) if cur[k] != old[k]} == {gk}                  # (a) every other pin byte-identical
    assert '6518282215' in cur['_amended_cand_evict_ack_01'] and cur['_source'] == old['_source']
    got = FC.function_pins(cur)                                                  # (b) the bundled preflight's pin rule
    assert {k: got[k] for k in real(cur)} == real(cur)
    pins_bad = sorted(k for k, v in cur.items() if not k.startswith('_') and got.get(k) != v)   # C9's pin part
    assert pins_bad == []
