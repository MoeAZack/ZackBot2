"""AUD-05 (audit C08, P1): durable, fail-closed settings/state and atomic /api/settings.
Before the fix a corrupt settings.json silently reset to defaults (entries ON) and was then overwritten; a corrupt
state.json started with no lots (the bot lost ownership of live positions); writes had no fsync and no backup; and
/api/settings applied keys one by one, so memory and disk diverged when a later key was invalid."""
import glob, json, os
import pytest
import test_safety as TS
from test_safety import mk_engine, FakeX

E = TS.E

CORRUPT = {'garbage': b'\x00\xff not json at all', 'truncated': b'{"MAX_LEVERAGE": 7, "UNIV',
           'wrong_type': b'[1, 2, 3]', 'empty': b''}


def _write(path, raw):
    with open(path, 'wb') as f: f.write(raw)


def _evidence(path):
    return sorted(glob.glob(path + '.corrupt-*'))


def _restart(tmp, fake):
    """A fresh engine on the same data folder and the same (fake) Binance account."""
    E.Futures = lambda *a, **k: fake
    e = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)
    e.data = e.trade
    e.connect(); e.marks = e.trade.marks()
    return e


def _incident(e, name):
    inc = (e.health.get('incidents') or {}).get(f'integrity|{name}')
    assert inc and inc['open'], e.health.get('incidents')
    return inc['msg']


@pytest.fixture
def notes(monkeypatch):
    sent = []
    monkeypatch.setattr(E.Engine, 'notify', lambda self, t: sent.append(t))
    return sent


# ------------------------------------------------------------------ durable writes
def test_save_fsyncs_and_keeps_the_previous_good_file_as_bak(tmp_path, monkeypatch):
    n = []
    real = os.fsync
    monkeypatch.setattr(E.os, 'fsync', lambda fd: (n.append(fd), real(fd))[1])
    p = str(tmp_path / 'state.json')
    E.save_json(p, dict(v=1), backup=True)
    assert len(n) >= 1 and not os.path.exists(p + '.bak'), 'first save: nothing to back up'
    n.clear()
    E.save_json(p, dict(v=2), backup=True)
    assert len(n) >= 2, 'both the .bak and the new file are fsynced'
    assert json.load(open(p)) == dict(v=2) and json.load(open(p + '.bak')) == dict(v=1)
    assert not glob.glob(str(tmp_path / '*.tmp'))


def test_engine_saves_settings_and_state_with_a_bak():
    e, tmp = mk_engine()
    e.save_state(); e.state['day'] = 'X'; e.save_state()
    e.save_settings(); e.S['MAX_LEVERAGE'] = 7; e.save_settings()
    assert json.load(open(e.F['state'] + '.bak'))['day'] != 'X' and json.load(open(e.F['state']))['day'] == 'X'
    assert json.load(open(e.F['settings'] + '.bak'))['MAX_LEVERAGE'] != 7


def test_a_damaged_file_never_replaces_a_good_bak(tmp_path):
    p = str(tmp_path / 'settings.json')
    E.save_json(p, dict(v=1), backup=True); E.save_json(p, dict(v=2), backup=True)
    _write(p, b'{"v": 3, "tru')                                      # damaged on disk between two saves
    E.save_json(p, dict(v=4), backup=True)
    assert json.load(open(p + '.bak')) == dict(v=1) and json.load(open(p)) == dict(v=4)


def test_a_crash_between_temp_write_and_replace_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    p = str(tmp_path / 'state.json')
    E.save_json(p, dict(lots={'a': 1}), backup=True)
    def boom(src, dst):
        if dst == p: raise OSError('power cut')
        return os.rename(src, dst) if not os.path.exists(dst) else (os.remove(dst), os.rename(src, dst))
    monkeypatch.setattr(E.os, 'replace', boom)
    with pytest.raises(OSError):
        E.save_json(p, dict(lots={}), backup=True)
    assert json.load(open(p)) == dict(lots={'a': 1}), 'the previous good file is untouched'
    assert not glob.glob(str(tmp_path / '*.tmp')), 'no temp file left behind'


def test_windows_replace_contention_is_retried_then_raised(tmp_path, monkeypatch):
    p = str(tmp_path / 's.json'); real = os.replace; calls = []
    monkeypatch.setattr(E, '_sleep', lambda s: None)
    def flaky(src, dst):
        calls.append(dst)
        if len(calls) < 3: raise PermissionError(13, 'in use')
        return real(src, dst)
    monkeypatch.setattr(E.os, 'replace', flaky)
    E.save_json(p, dict(a=1))
    assert json.load(open(p)) == dict(a=1) and len(calls) == 3
    monkeypatch.setattr(E.os, 'replace', lambda s, d: (_ for _ in ()).throw(PermissionError(13, 'in use')))
    with pytest.raises(PermissionError):
        E.save_json(p, dict(a=2))
    assert json.load(open(p)) == dict(a=1) and not glob.glob(str(tmp_path / '*.tmp'))


# ------------------------------------------------------------------ corrupt settings
@pytest.mark.parametrize('kind', sorted(CORRUPT))
def test_corrupt_settings_with_a_good_bak_restores_the_backup(kind, notes):
    e, tmp = mk_engine()
    e.S['MAX_LEVERAGE'] = 7; e.save_settings(); e.save_settings()          # .bak holds MAX_LEVERAGE 7
    _write(e.F['settings'], CORRUPT[kind])
    e2 = _restart(tmp, e.trade)
    assert e2.S['MAX_LEVERAGE'] == 7 and e2.S['ENTRIES_PAUSED'] is True, 'an older copy: paused for owner review'
    assert json.load(open(e2.F['settings']))['ENTRIES_PAUSED'] is True
    assert 'restored from settings.json.bak' in _incident(e2, 'settings') and any('settings.json' in t for t in notes)
    ev = _evidence(e.F['settings'])
    assert len(ev) == 1 and open(ev[0], 'rb').read() == CORRUPT[kind], 'the damaged file is kept byte for byte'


@pytest.mark.parametrize('kind', sorted(CORRUPT))
def test_corrupt_settings_without_a_bak_pauses_entries_and_keeps_the_evidence(kind, notes):
    e, tmp = mk_engine()
    e.save_settings()
    os.remove(e.F['settings'] + '.bak') if os.path.exists(e.F['settings'] + '.bak') else None
    _write(e.F['settings'], CORRUPT[kind])
    e2 = _restart(tmp, e.trade)
    assert e2.S['ENTRIES_PAUSED'] is True, 'never trade on silently reset defaults'
    assert 'ENTRIES PAUSED' in _incident(e2, 'settings') and notes
    assert json.load(open(e2.F['settings']))['ENTRIES_PAUSED'] is True, 'the pause is durable'
    ev = _evidence(e.F['settings'])
    assert len(ev) == 1 and open(ev[0], 'rb').read() == CORRUPT[kind], 'later saves did not destroy the evidence'
    assert e2.entry_block(TS.SL, 'BTCUSDT', 'LONG') == 'entries paused'


def test_valid_settings_file_with_a_bad_value_is_not_treated_as_corrupt():
    e, tmp = mk_engine()
    with open(e.F['settings'], 'w') as f: json.dump(dict(MARKET_COLLECTOR='off'), f)
    e2 = _restart(tmp, e.trade)
    assert e2.S['MARKET_COLLECTOR'] is True and e2.S['ENTRIES_PAUSED'] is False and not _evidence(e.F['settings'])


# ------------------------------------------------------------------ corrupt state
def test_corrupt_state_with_a_good_bak_restores_the_lots(notes):
    e, tmp = mk_engine()
    k = TS.opened(e); e.save_state()                                       # .bak = a save that already holds the lot
    _write(e.F['state'], b'{"lots": {"T|BTC')
    e2 = _restart(tmp, e.trade)
    assert k in e2.state['lots'] and e2.S['ENTRIES_PAUSED'] is True and e2.state['lots'][k]['restored_from_bak']
    assert 'restored from state.json.bak' in _incident(e2, 'state') and len(_evidence(e.F['state'])) == 1
    e2.reconcile(e2.equity()); e2.reconcile(e2.equity())
    assert not e2.untracked, 'the restored lot owns its Binance position again'


@pytest.mark.parametrize('kind', sorted(CORRUPT))
def test_corrupt_state_without_bak_pauses_entries_and_reports_the_live_position_untracked(kind, notes):
    e, tmp = mk_engine()
    TS.opened(e)
    for p in (e.F['state'] + '.bak',):
        if os.path.exists(p): os.remove(p)
    _write(e.F['state'], CORRUPT[kind])
    e2 = _restart(tmp, e.trade)
    assert e2.state['lots'] == {} and e2.S['ENTRIES_PAUSED'] is True
    assert json.load(open(e2.F['settings']))['ENTRIES_PAUSED'] is True
    assert 'ENTRIES PAUSED' in _incident(e2, 'state')
    e2.reconcile(e2.equity()); e2.reconcile(e2.equity())                  # (alerted when seen on two passes)
    assert 'BTCUSDT|LONG' in e2.untracked, 'the live position is reported, never silently forgotten'
    assert any('UNTRACKED' in m[1] for m in e2.health['errors'])
    assert e2.trade.stops, 'its Binance stop is left alone'
    e2.save_state()
    ev = _evidence(e.F['state'])
    assert len(ev) == 1 and open(ev[0], 'rb').read() == CORRUPT[kind]


def test_restart_after_recovery_keeps_the_evidence_and_stays_paused(notes):
    e, tmp = mk_engine()
    TS.opened(e)
    if os.path.exists(e.F['state'] + '.bak'): os.remove(e.F['state'] + '.bak')
    _write(e.F['state'], b'garbage')
    e2 = _restart(tmp, e.trade); e2.save_state(); e2.save_settings()
    ev = _evidence(e.F['state'])
    e3 = _restart(tmp, e.trade); e3.save_state()
    assert _evidence(e.F['state']) == ev and open(ev[0], 'rb').read() == b'garbage'
    assert e3.S['ENTRIES_PAUSED'] is True, 'stays paused until the owner resumes'


def test_evidence_that_cannot_be_moved_aside_is_never_saved_over(monkeypatch, notes):
    e, tmp = mk_engine()
    if os.path.exists(e.F['state'] + '.bak'): os.remove(e.F['state'] + '.bak')
    _write(e.F['state'], b'garbage')
    monkeypatch.setattr(E, 'quarantine', lambda p: None)
    e2 = _restart(tmp, e.trade)
    assert e2.S['ENTRIES_PAUSED'] is True
    assert e2.save_state() is False and e2.save_state() is False, 'refused, reported - never an exception storm'
    assert open(e.F['state'], 'rb').read() == b'garbage'
    assert e2.health['incidents']['save-held|state']['count'] == 2


def test_corrupt_aux_file_is_kept_aside_and_never_raises(notes):
    e, tmp = mk_engine()
    _write(e.F['history'], b'[{"a": 1}, {"b"')
    e2 = _restart(tmp, e.trade)
    assert e2.history == [] and len(_evidence(e.F['history'])) == 1 and _incident(e2, 'history')
    assert e2.S['ENTRIES_PAUSED'] is False, 'an aux file does not pause trading'


# ------------------------------------------------------------------ atomic /api/settings
@pytest.fixture
def api(monkeypatch):
    import app as A
    e, tmp = mk_engine()
    e.S['UNIVERSE'] = ['BTCUSDT']; e.S['SYMBOLS_ON'] = {'BTCUSDT': True}     # ETH is tradable but not in the list yet
    e.save_settings()
    app = A.App.__new__(A.App); app.engine = e; app.preview = lambda: None; app.cfg = {}; app.tg = None
    monkeypatch.setattr(A, 'APP', app, raising=False)
    cfg_writes = []
    monkeypatch.setattr(A, 'write_cfg', lambda u: cfg_writes.append(u))
    return A, e, cfg_writes


def _snap(e):
    return json.dumps(e.S, sort_keys=True, default=str), open(e.F['settings'], 'rb').read(), json.dumps(e.state, sort_keys=True, default=str)


@pytest.mark.parametrize('body', [
    dict(MAX_LEVERAGE=5, ENTRY_ORDER='limit'),                                           # LIMITS then a bad choice
    dict(ADD_SYMBOL='ETH', MARKET_COLLECTOR='no'),                                       # coin added, then bad bool
    dict(CAPITAL_CAP=900, GOVERNOR=dict(mode='auto', rules=[{'if': 'dd_gte', 'value': 10, 'then': {'risk_mult': 9}}])),
    dict(SYMBOLS_ON={'BTCUSDT': False}, RISK_RULES='nope'),
    dict(REMOVE_SYMBOL='BTCUSDT', TELEGRAM_CHAT='not a chat'),
    dict(TELEGRAM_TOKEN='123456:ABCdefGHI_jkl-MNOpqrstuvw', DAILY_LOSS_HALT=5),   # token file write, then bad limit
    dict(ENTRIES_PAUSED=True, AI_FILTER=True, bogus_key=1),
])
def test_one_invalid_key_changes_neither_memory_nor_disk(api, body):
    A, e, cfg_writes = api
    before = _snap(e)
    with pytest.raises(ValueError):
        A.handle('/api/settings', body)
    assert _snap(e) == before, 'nothing applied, nothing saved'
    assert not cfg_writes, 'no side effect ran'


def test_a_valid_update_is_applied_and_saved_once(api, monkeypatch):
    A, e, _ = api
    saves = []
    real = E.Engine._save_safe
    monkeypatch.setattr(E.Engine, '_save_safe', lambda self, n, o, **kw: (saves.append(n), real(self, n, o, **kw))[1])
    e._lev = {'BTCUSDT': 5}
    assert A.handle('/api/settings', dict(MAX_LEVERAGE=5, ENTRY_ORDER='maker', SYMBOLS_ON={'BTCUSDT': False})) == 'saved'
    assert saves == ['settings'] and e._lev == {}
    disk = json.load(open(e.F['settings']))
    assert e.S['MAX_LEVERAGE'] == disk['MAX_LEVERAGE'] == 5 and disk['ENTRY_ORDER'] == 'maker' and disk['SYMBOLS_ON']['BTCUSDT'] is False


def test_a_failed_save_leaves_memory_unchanged(api, monkeypatch):
    A, e, _ = api
    before = _snap(e)
    def fail(*a, **k): raise PermissionError(13, 'disk locked')
    monkeypatch.setattr(E, 'save_json', fail)
    with pytest.raises(PermissionError):
        A.handle('/api/settings', dict(MAX_LEVERAGE=3))
    assert _snap(e) == before


def test_a_failed_side_effect_is_reported_and_the_settings_stay_committed(api, monkeypatch):
    A, e, _ = api
    def boom(v): raise RuntimeError('state disk full')
    monkeypatch.setattr(e, 'set_capital_base', boom)
    with pytest.raises(ValueError, match='settings saved, but start amount failed'):
        A.handle('/api/settings', dict(MAX_LEVERAGE=4, CAPITAL_CAP=800))
    assert e.S['MAX_LEVERAGE'] == json.load(open(e.F['settings']))['MAX_LEVERAGE'] == 4
    assert e.S['CAPITAL_CAP'] == json.load(open(e.F['settings']))['CAPITAL_CAP'] == 500.0, 'memory and disk agree'


# ------------------------------------------------------------------ review round 1 (F1-F6)
def test_r1_bak_restore_keeps_the_owners_pause(notes):
    """F1/R1: the owner's pause was the last save; the .bak (one save behind) was unpaused - a restore must not unpause."""
    e, tmp = mk_engine(); e.save_settings()
    e.S['ENTRIES_PAUSED'] = True; e.save_settings()
    _write(e.F['settings'], b'{"ENTRIES_PAUSED": true, "MAX_')
    e2 = _restart(tmp, e.trade)
    assert e2.S['ENTRIES_PAUSED'] is True and 'OLDER copy' in _incident(e2, 'settings')


def test_r1_stale_bak_lot_books_no_phantom_exit_until_the_owner_resumes(notes):
    """F1/R2: a lot closed in the last save is resurrected from the .bak: reconcile reports, books nothing, sends nothing."""
    e, tmp = mk_engine(); k = TS.opened(e); e.save_state()
    e.close_lot(k, 'manual', e.marks['BTCUSDT'])                     # last save: no lot; .bak still holds it
    _write(e.F['state'], b'{"lots": {')
    e2 = _restart(tmp, e.trade)
    rows0 = open(e2.F['trades']).read().splitlines()
    calls0 = len(e2.trade.calls)
    for _ in range(3): e2.manage(e2.trade.marks())
    assert open(e2.F['trades']).read().splitlines() == rows0, 'no phantom stop / P&L booked'
    assert k in e2.state['lots'] and e2.state['lots'][k]['restored_mismatch']
    assert not [c for c in e2.trade.calls[calls0:] if c in ('open', 'close', 'stop', 'cancel')], 'no orders for a stale lot'
    inc = e2.health['incidents']['restored|BTCUSDT|LONG']
    assert inc['open'] and 'nothing booked' in inc['msg']
    e2.S['ENTRIES_PAUSED'] = False                                     # the owner checked Binance and resumed
    e2.reconcile(e2.equity())
    assert k not in e2.state['lots'], 'after the owner resumes, the difference is booked normally'


def test_r1_restored_lot_without_stop_id_places_no_second_stop_before_the_verifier_read(notes):
    """F1: the .bak was saved before the stop id was recorded, but Binance holds the stop: while the coin's open orders
    cannot be read nothing is placed; once read, the verifier adopts the matching bot stop - never a second stop."""
    e, tmp = mk_engine(); k = TS.opened(e)
    lot = e.state['lots'][k]; tag = lot['stop_id']
    lot['stop_id'] = None; e.save_state(); e.save_state()
    _write(e.F['state'], b'garbage')
    e2 = _restart(tmp, e.trade)
    assert tag in e2.trade.stops and e2.state['lots'][k]['stop_id'] is None
    n0 = e2.trade.calls.count('stop')
    def unreadable(sym): raise TimeoutError('open orders timed out')
    e2._stop_rows = unreadable
    for _ in range(2): e2.manage(e2.trade.marks())
    assert e2.trade.calls.count('stop') == n0, 'Binance may hold its stop: nothing placed before a verifier read'
    l2 = e2.state['lots'][k]
    row = dict(tag=tag, client_id='zb' + '0' * 22, type='STOP_MARKET', side='SELL', position_side='LONG',
               qty=l2['qty'], stop_price=l2['stop'])
    e2._stop_rows = lambda sym: ([row] if sym == 'BTCUSDT' else [], True)
    e2._stopv.clear()
    e2.manage(e2.trade.marks())
    assert e2.state['lots'][k]['stop_id'] == tag and e2.trade.calls.count('stop') == n0, 'adopted, not duplicated'


def test_r1_crash_before_the_pause_is_saved_never_comes_back_unpaused(monkeypatch, notes):
    """F2/R3: the pause is persisted BEFORE the damaged state is moved aside; if it cannot be, the file stays in place."""
    e, tmp = mk_engine(); TS.opened(e)
    if os.path.exists(e.F['state'] + '.bak'): os.remove(e.F['state'] + '.bak')
    _write(e.F['state'], b'garbage')
    real = E.Engine.save_settings
    monkeypatch.setattr(E.Engine, 'save_settings', lambda self: (_ for _ in ()).throw(OSError('power cut')))
    e2 = _restart(tmp, e.trade)                                       # boot 1: the pause cannot be saved
    assert e2.S['ENTRIES_PAUSED'] is True and open(e.F['state'], 'rb').read() == b'garbage' and not _evidence(e.F['state'])
    monkeypatch.setattr(E.Engine, 'save_settings', real)
    e3 = _restart(tmp, e.trade)                                       # boot 2: same damaged file -> fail closed again
    assert e3.S['ENTRIES_PAUSED'] is True and e3.entry_block(TS.SL, 'ETHUSDT', 'LONG') == 'entries paused'


@pytest.mark.parametrize('name', ['state', 'settings'])
def test_r1_main_missing_with_earlier_evidence_fails_closed(name, notes):
    """F2: the damaged file was moved aside but the run died before anything else was saved: never a 'first run'."""
    e, tmp = mk_engine(); e.save_state(); e.save_settings()
    for p in (e.F[name], e.F[name] + '.bak'):
        if os.path.exists(p): os.remove(p)
    _write(e.F[name] + '.corrupt-20261007T000000Z', b'garbage')
    e2 = _restart(tmp, e.trade)
    assert e2.S['ENTRIES_PAUSED'] is True and 'earlier recovery did not finish' in _incident(e2, name)


def test_r1_transient_read_error_on_a_good_file_is_retried_not_quarantined(monkeypatch, notes):
    """F3/R4: a sharing violation (antivirus) on a GOOD state.json is retried - the good file is loaded, nothing moved."""
    e, tmp = mk_engine(); k = TS.opened(e); e.save_state(); e.state['day'] = 'NEWEST'; e.save_state()
    monkeypatch.setattr(E, '_sleep', lambda s: None)
    ro = E.read_json; n = [0]
    def flaky(p, kind=None):
        if p.endswith('state.json') and n[0] < 2: n[0] += 1; raise PermissionError(13, 'sharing violation')
        return ro(p, kind)
    monkeypatch.setattr(E, 'read_json', flaky)
    e2 = _restart(tmp, e.trade)
    assert e2.state['day'] == 'NEWEST' and k in e2.state['lots'] and not _evidence(e.F['state']) and not e2.integrity
    assert e2.S['ENTRIES_PAUSED'] is False


def test_r1_persistent_read_error_fails_closed_without_touching_the_file(monkeypatch, notes):
    e, tmp = mk_engine(); TS.opened(e); e.save_state()
    good = open(e.F['state'], 'rb').read()
    monkeypatch.setattr(E, '_sleep', lambda s: None)
    ro = E.read_json
    def locked(p, kind=None):
        if p.endswith('state.json'): raise PermissionError(13, 'locked')
        return ro(p, kind)
    monkeypatch.setattr(E, 'read_json', locked)
    e2 = _restart(tmp, e.trade)
    assert e2.S['ENTRIES_PAUSED'] is True and e2.state['lots'] == {} and 'cannot be read' in _incident(e2, 'state')
    assert e2.save_state() is False
    assert open(e.F['state'], 'rb').read() == good and not _evidence(e.F['state']), 'never moved, never overwritten'


def test_r1_unmovable_corrupt_settings_starts_paused(monkeypatch, notes):
    """F4: a corrupt settings.json that cannot be moved aside: the bot still starts (paused), saves are refused."""
    import app as A
    e, tmp = mk_engine(); e.save_settings()
    if os.path.exists(e.F['settings'] + '.bak'): os.remove(e.F['settings'] + '.bak')
    _write(e.F['settings'], b'{"MAX_')
    monkeypatch.setattr(E, 'quarantine', lambda p: None)
    e2 = _restart(tmp, e.trade)                                       # used to raise inside __init__
    assert e2.S['ENTRIES_PAUSED'] is True and open(e.F['settings'], 'rb').read() == b'{"MAX_'
    assert e2.save_settings() is False
    assert e2.health['incidents']['save-held|settings']['open']
    app = A.App.__new__(A.App); app.engine = e2; app.preview = lambda: None; app.cfg = {}; app.tg = None
    monkeypatch.setattr(A, 'APP', app, raising=False)
    lev = e2.S['MAX_LEVERAGE']
    with pytest.raises(OSError):
        A.handle('/api/settings', dict(MAX_LEVERAGE=3))
    assert e2.S['MAX_LEVERAGE'] == lev, 'a refused save changes nothing in memory'


def test_r1_file_damaged_while_running_is_kept_as_evidence_at_save_time(notes):
    """F5: state.json damaged on disk while the bot runs: the next save keeps the damaged file aside and reports it."""
    e, tmp = mk_engine(); e.save_state(); e.save_state()
    _write(e.F['state'], b'{"lots": {"x')
    assert e.save_state() is True
    ev = _evidence(e.F['state'])
    assert len(ev) == 1 and open(ev[0], 'rb').read() == b'{"lots": {"x'
    assert e.health['incidents']['integrity|state|save']['open']
    assert json.load(open(e.F['state']))['lots'] == e.state['lots']


def test_r1_record_equity_never_raises(monkeypatch):
    """F6: the equity file is an aux file (AUD-01): a failed write is reported, never raised into the cycle."""
    e, tmp = mk_engine()
    def fail(*a, **k): raise PermissionError(13, 'locked')
    monkeypatch.setattr(E, 'save_json', fail)
    e.record_equity(510.0)
    assert e.equity_hist[-1][1] == 510.0 and e.health['incidents']['save|equity']['open']
