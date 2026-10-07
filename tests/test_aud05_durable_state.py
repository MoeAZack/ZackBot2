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
    assert e2.S['MAX_LEVERAGE'] == 7 and e2.S['ENTRIES_PAUSED'] is False
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
    assert k in e2.state['lots'] and e2.S['ENTRIES_PAUSED'] is False
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
    with pytest.raises(OSError):
        e2.save_state()
    assert open(e.F['state'], 'rb').read() == b'garbage'


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
    monkeypatch.setattr(E.Engine, '_save_safe', lambda self, n, o: (saves.append(n), real(self, n, o))[1])
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
