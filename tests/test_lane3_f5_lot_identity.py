"""LANE3-F5 (PR #359 red-team): two confirmed fills of one sleeve/coin/side in the same second (or after a clock step
back) are two lots - the second never replaces the first. Real lot-construction path; every side effect faked."""
import os, sys, socket, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import requests
import engine as E                                                         # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k): raise AssertionError('network forbidden in this test')
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(requests.sessions.Session, 'request', refuse)


def bare(monkeypatch, t=1_799_000_000.25):
    e = object.__new__(E.Engine)
    e.rules = {'BTCUSDT': {'tick': .01, 'step': .001}}
    e.S, e.state = {}, {'lots': {}}
    e.saves = []
    e.save_state = lambda: e.saves.append({k: dict(v) for k, v in e.state['lots'].items()}) or True
    e._ml_link = lambda *a: None
    e._replace_stop = lambda lot: True
    e._tp_wanted = lambda lot: False
    e.log_trade = lambda **kw: None
    e.notify = lambda text: None
    clock = {'t': t}
    monkeypatch.setattr(E.time, 'time', lambda: clock['t'])
    return e, clock


PLAN = dict(sym='BTCUSDT', side='LONG', sl=None, manual=True, g={}, atr=2., eq=10000., risk_usd=10., reason='synthetic',
            px=100., stop_dist=5.)


def test_red_team_repro_same_second_manual_fills_preserve_both_lots(monkeypatch):
    """Codex #359 reproducer."""
    e, _ = bare(monkeypatch)
    assert e._create_lot(dict(PLAN), 1., 100.)
    assert e._create_lot(dict(PLAN), 2., 101.)
    assert len(e.state['lots']) == 2, 'second manual fill overwrote the first lot'
    assert sum(l['qty'] for l in e.state['lots'].values()) == 3.


def test_three_fills_same_second_get_distinct_keys_and_keep_the_symbol_field(monkeypatch):
    e, _ = bare(monkeypatch)
    keys = []
    for q in (1., 2., 3.):
        assert e._create_lot(dict(PLAN), q, 100.); keys.append(e._last_lot_key)
    assert len(set(keys)) == 3 and keys[0] == 'MAN|BTCUSDT|LONG|1799000000'
    assert keys[1:] == ['MAN|BTCUSDT|LONG|1799000000-1', 'MAN|BTCUSDT|LONG|1799000000-2']
    assert all(k.split('|')[1] == 'BTCUSDT' for k in keys)                   # app / Telegram read the symbol from [1]
    assert [e.state['lots'][k]['qty'] for k in keys] == [1., 2., 3.]
    assert all(e.saves[-1][k]['qty'] == q for k, q in zip(keys, (1., 2., 3.)))   # each one saved, none replaced


def test_clock_step_back_never_reuses_a_live_lot_key(monkeypatch):
    e, clock = bare(monkeypatch)
    e._create_lot(dict(PLAN), 1., 100.); first = e._last_lot_key
    clock['t'] += 5; e._create_lot(dict(PLAN), 2., 100.)
    clock['t'] -= 5; e._create_lot(dict(PLAN), 4., 100.)                      # the clock stepped back to the first second
    assert len(e.state['lots']) == 3 and e.state['lots'][first]['qty'] == 1.
    assert sum(l['qty'] for l in e.state['lots'].values()) == 7.


def test_a_closed_trade_id_is_never_issued_again(monkeypatch):
    """Codex #363 review: removal from the live lot map is not identity release - the closed trade's history row and its
    measured-cost wait still answer to that id."""
    e, clock = bare(monkeypatch)
    e._create_lot(dict(PLAN), 1., 100.); k = e._last_lot_key
    e.history = [dict(id=k, symbol='BTCUSDT')]                               # closed: its history row ...
    e.state['measured_wait'] = {k: dict(symbol='BTCUSDT')}                   # ... and its outstanding cost evidence
    del e.state['lots'][k]
    e._create_lot(dict(PLAN), 2., 100.)                                      # same second
    assert e._last_lot_key != k
    clock['t'] -= 3600; e._create_lot(dict(PLAN), 3., 100.)                  # clock rollback to an issued second
    clock['t'] += 3600; e._create_lot(dict(PLAN), 4., 100.)
    ks = [k2 for k2 in e.state['lots']]
    assert len(set(ks + [k])) == 4 and k not in ks


def test_identity_high_water_survives_a_restart_and_a_stale_state_copy(monkeypatch):
    e, clock = bare(monkeypatch)
    e._create_lot(dict(PLAN), 1., 100.); k = e._last_lot_key
    saved = {kk: (dict(v) if isinstance(v, dict) else v) for kk, v in e.state.items()}
    saved['lot_key_seq'] = {p: dict(v) for p, v in e.state['lot_key_seq'].items()}
    del saved['lots'][k]                                                     # closed before the restart
    e2, _ = bare(monkeypatch, t=1_799_000_000.9); e2.state = saved           # same second after the restart
    e2._create_lot(dict(PLAN), 2., 100.)
    assert e2._last_lot_key != k
    e3, _ = bare(monkeypatch); e3.history = [dict(id=k)]                     # an older state copy without the mark:
    e3._create_lot(dict(PLAN), 2., 100.)                                     # the history row still blocks the id
    assert e3._last_lot_key != k


def test_other_coins_in_the_same_second_keep_plain_keys(monkeypatch):
    e, _ = bare(monkeypatch); e.rules['ETHUSDT'] = {'tick': .01, 'step': .001}
    e._create_lot(dict(PLAN), 1., 100.); a = e._last_lot_key
    e._create_lot(dict(PLAN, sym='ETHUSDT'), 1., 100.); b = e._last_lot_key
    assert a == 'MAN|BTCUSDT|LONG|1799000000' and b == 'MAN|ETHUSDT|LONG|1799000000'


def test_sleeve_lots_unchanged_when_no_collision(monkeypatch):
    e, _ = bare(monkeypatch)
    sl = dict(id='T', key='trend', tf='4h')
    e._create_lot(dict(PLAN, manual=False, sl=sl), 1., 100.)
    assert e._last_lot_key == 'T|BTCUSDT|LONG|1799000000'


def _close_and_forget(e):
    """Close every lot and drop every reference (history pruned, cost evidence settled): only the durable high-water
    mark can stop a reused id now."""
    e.state['lots'].clear(); e.state['measured_wait'] = {}; e.history = []


def test_high_water_survives_a_clock_rollback_even_after_references_are_gone(monkeypatch):
    e, clock = bare(monkeypatch)
    e._create_lot(dict(PLAN), 1., 100.); k95 = e._last_lot_key                 # second T
    clock['t'] += 5; e._create_lot(dict(PLAN), 1., 100.)                        # second T+5
    _close_and_forget(e)
    clock['t'] -= 10; e._create_lot(dict(PLAN), 1., 100.)                       # clock stepped back below T
    _close_and_forget(e)
    clock['t'] += 5; e._create_lot(dict(PLAN), 1., 100.)                        # back at second T: issued before
    assert e._last_lot_key != k95 and e._last_lot_key.startswith(k95 + '-')


def test_suffix_counter_never_goes_back_when_nothing_references_the_old_ids(monkeypatch):
    e, _ = bare(monkeypatch)
    seen = []
    for _i in range(4):
        e._create_lot(dict(PLAN), 1., 100.); seen.append(e._last_lot_key); _close_and_forget(e)
    assert len(set(seen)) == 4, seen


def test_a_referenced_suffix_is_skipped_too(monkeypatch):
    """A state restored from an older copy: fresh high-water mark, but the history still holds base and base-1."""
    e, _ = bare(monkeypatch)
    base = 'MAN|BTCUSDT|LONG|1799000000'
    e.history = [dict(id=base), dict(id=base + '-1')]
    e._create_lot(dict(PLAN), 1., 100.)
    assert e._last_lot_key == base + '-2'


def test_an_outstanding_measured_cost_wait_alone_blocks_the_id(monkeypatch):
    e, _ = bare(monkeypatch)
    base = 'MAN|BTCUSDT|LONG|1799000000'
    e.state['measured_wait'] = {base: dict(symbol='BTCUSDT')}
    e._create_lot(dict(PLAN), 1., 100.)
    assert e._last_lot_key == base + '-1'


# ---------------------------------------------------------------- Codex #363 round 2 (6925177679)
def test_r2_codex_probe_an_outstanding_candidate_cost_id_is_never_reissued(monkeypatch):
    """No lot_key_seq / history / measured_wait - only the durable pending-cost index (kept after history eviction)."""
    e, clock = bare(monkeypatch)
    base = 'MAN|BTCUSDT|LONG|1799000000'
    e.state[E.CANDIDATE_PENDING_KEY] = {base: dict(sleeve='C-F01', symbol='BTCUSDT', closed='2026-11-16T00:00:00+00:00')}
    e._create_lot(dict(PLAN), 1., 100.)
    assert e._last_lot_key != base and e.state[E.CANDIDATE_PENDING_KEY] == {base: e.state[E.CANDIDATE_PENDING_KEY][base]}


def test_r2_an_acknowledged_candidate_cost_id_is_never_reissued(monkeypatch):
    e, clock = bare(monkeypatch)
    base = 'MAN|BTCUSDT|LONG|1799000000'
    e.state[E.CEA.ACK_KEY] = {base: dict(row_id=base)}
    e._create_lot(dict(PLAN), 1., 100.)
    assert e._last_lot_key != base


def test_r2_the_audit_excursion_cache_ids_are_never_reissued(monkeypatch):
    e, clock = bare(monkeypatch)
    base = 'MAN|BTCUSDT|LONG|1799000000'
    e._tb_exc = dict(off={}, ids={base: {}}, at={}, fid=None)
    e._create_lot(dict(PLAN), 1., 100.)
    assert e._last_lot_key != base


def test_r2_legacy_state_seeds_the_mark_from_referenced_ids_so_rollback_below_them_is_suffixed(monkeypatch):
    e, clock = bare(monkeypatch)
    later = 'MAN|BTCUSDT|LONG|1799000100'                                     # an outstanding id 100 s after "now"
    e.state[E.CANDIDATE_PENDING_KEY] = {later: dict(sleeve='C-F01', symbol='BTCUSDT', closed='2026-11-16T00:00:00+00:00')}
    e._create_lot(dict(PLAN), 1., 100.)                                       # clock is behind that id
    assert e._last_lot_key.startswith('MAN|BTCUSDT|LONG|1799000000-')
    assert e.state['lot_key_seq']['MAN|BTCUSDT|LONG']['t'] == 1799000100


def test_r2_every_audited_store_is_in_the_referenced_set(monkeypatch):
    e, _ = bare(monkeypatch)
    ids = {n: f'MAN|BTCUSDT|LONG|{i}' for i, n in enumerate(('lots', 'measured_wait', E.CANDIDATE_PENDING_KEY, E.CEA.ACK_KEY), 1)}
    for n, i in ids.items(): e.state[n] = {i: {}}
    e.history = [dict(id='h|BTCUSDT|LONG|9')]; e.cohort_rows = [dict(id='c|BTCUSDT|LONG|9')]
    got = e._lot_ids_referenced()
    assert set(ids.values()) | {'h|BTCUSDT|LONG|9', 'c|BTCUSDT|LONG|9'} <= got


def test_r2_the_seeded_mark_is_the_latest_referenced_second(monkeypatch):
    e, clock = bare(monkeypatch)
    ids = {f'MAN|BTCUSDT|LONG|{1799000000 + 10 * i}': dict(sleeve='C-F01', symbol='BTCUSDT', closed='x') for i in range(1, 31)}
    e.state[E.CANDIDATE_PENDING_KEY] = ids
    e._create_lot(dict(PLAN), 1., 100.)
    assert e.state['lot_key_seq']['MAN|BTCUSDT|LONG']['t'] == 1799000300
