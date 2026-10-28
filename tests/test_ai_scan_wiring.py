"""AI-SCAN-01 wiring slice: the append-only scan ledger (atomic, restart-idempotent, fail-closed on corrupt / torn lines)
and the read-only GET /api/ai_scan (default OFF -> exactly {on:false, rows:[]}, PAPER only, GET only, no order / risk /
settings authority). Fakes only - no network, no real keys, no real data folder."""
import copy, http.client, json, os, sys, tempfile, threading, types
from http.server import ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['LOCALAPPDATA'] = tempfile.mkdtemp()            # never the real %LOCALAPPDATA%\ZackBot
import ai_scan as A                                        # noqa: E402
import ai_scan_ledger as L                                 # noqa: E402
import engine as E                                         # noqa: E402
from test_ai_scan import sig, shadow, outcome, outcome1, sim   # noqa: E402

NOW = '2026-10-20T12:20:00Z'


def recs():
    s = sig(); s2 = sig(symbol='ethusdt', side='SHORT')
    return [s, shadow(s), outcome(s), s2, sim(s2)]


def write_all(path, rows):
    led = L.ScanLedger(path)
    return led, [led.append(r) for r in rows]


# ------------------------------------------------------------------ ledger
def test_append_is_one_whole_line_per_record_and_restart_idempotent(tmp_path):
    p = L.ledger_path(tmp_path)
    rows = recs()
    led, out = write_all(p, rows)
    assert out == rows and len(open(p, 'rb').read().splitlines()) == 5
    raw = open(p, 'rb').read()
    led2, again = write_all(p, rows + [copy.deepcopy(r) for r in rows])        # a restart replays everything twice
    assert again == [None] * 10 and open(p, 'rb').read() == raw                # no duplicate rows, no rewrite
    assert led2.records() == rows and led2.skipped == 0
    assert A.record_key(rows[0]) in led2


def test_a_second_revision_appends_and_a_conflicting_record_is_refused_unwritten(tmp_path):
    p = L.ledger_path(tmp_path)
    s = sig()
    led, _ = write_all(p, [s, outcome(s)])
    o1 = outcome1(s)                                                           # cost_revision_id derived from COSTS1
    assert led.append(o1) == o1                                                # same decision, new cost revision id
    raw = open(p, 'rb').read()
    for bad in (outcome(s, exit_reason='sl'), sig(rules_reason='edited')):    # same key, other content
        with pytest.raises(A.AIScanError, match='conflicting replay'):
            led.append(bad)
        with pytest.raises(A.AIScanError, match='conflicting replay'):
            L.ScanLedger(p).append(bad)                                        # also after a restart
    assert open(p, 'rb').read() == raw


@pytest.mark.parametrize('bad', [dict(sig(), stop=1.0), {'kind': 'signal'}, 'x', None])
def test_unsealed_or_forged_rows_are_never_written(tmp_path, bad):
    p = L.ledger_path(tmp_path)
    led = L.ScanLedger(p)
    with pytest.raises((A.AIScanError, KeyError)):
        led.append(bad)
    assert not os.path.exists(p) or open(p, 'rb').read() == b''


def test_a_corrupt_or_torn_trailing_line_is_skipped_counted_and_closed_off(tmp_path, caplog):
    p = L.ledger_path(tmp_path)
    rows = recs()
    write_all(p, rows[:3])
    with open(p, 'ab') as f: f.write(b'{"kind":"signal","signal_id":"tor')               # crash mid-append
    led = L.ScanLedger(p)                                                                   # restart: never raises
    assert led.records() == rows[:3] and led.skipped == 1
    assert 'skipping invalid ledger line 4' in caplog.text
    assert led.append(rows[3]) == rows[3]                                                   # lands on its own line
    lines = open(p, 'rb').read().splitlines()
    assert json.loads(lines[-1]) == rows[3] and lines[-2].startswith(b'{"kind":"signal","signal_id":"tor')
    assert L.ScanLedger(p).records() == rows[:4]


@pytest.mark.parametrize('junk', [b'not json\n', b'[1,2]\n', b'\xff\xfe\n'])
def test_any_invalid_line_is_skipped_never_raised(tmp_path, junk):
    p = L.ledger_path(tmp_path)
    rows = recs()
    write_all(p, rows[:2])
    with open(p, 'ab') as f: f.write(junk)
    edited = dict(rows[2], net_r=99.0)                                         # an edited record: hash mismatch
    with open(p, 'ab') as f: f.write((json.dumps(edited) + '\n').encode())
    got, skipped = L.read_records(p)
    assert got == rows[:2] and skipped == 2


def test_concurrent_appends_never_interleave(tmp_path):
    p = L.ledger_path(tmp_path)
    led = L.ScanLedger(p)
    many = [sig(symbol=f'c{i}usdt') for i in range(40)]
    ts = [threading.Thread(target=led.append, args=(r,)) for r in many]
    for t in ts: t.start()
    for t in ts: t.join(10)
    got, skipped = L.read_records(p)
    assert skipped == 0 and sorted(r['signal_id'] for r in got) == sorted(r['signal_id'] for r in many)


# ------------------------------------------------------------------ the view
def test_flag_off_or_missing_is_exactly_on_false_rows_empty_without_reading_the_ledger(tmp_path, monkeypatch):
    write_all(L.ledger_path(tmp_path), recs())
    monkeypatch.setattr(L, 'read_records', lambda p: (_ for _ in ()).throw(AssertionError('ledger read while OFF')))
    for S in ({}, {'AI_SCAN': False}, {'AI_SCAN': 'true'}, {'AI_SCAN': 1}, None):
        assert L.scan_view(S, tmp_path, now=NOW) == {'on': False, 'rows': []}


def test_flag_on_returns_the_ledger_rows_newest_first(tmp_path):
    rows = recs()
    write_all(L.ledger_path(tmp_path), rows)
    v = L.scan_view({'AI_SCAN': True}, tmp_path, now=NOW)
    assert v['on'] is True and v['error'] is None and v['skipped'] == 0
    views = A.fold(rows)
    assert {r['signal_id'] for r in v['rows']} == set(views)
    assert v['rows'][0] == A.display(views[v['rows'][0]['signal_id']], NOW)
    btc = next(r for r in v['rows'] if r['signal_id'] == rows[0]['signal_id'])
    assert btc['ai'][0]['label'] == A.LABEL_AI and btc['outcomes']['measured']['net_r'] == 1.2
    assert L.scan_view({'AI_SCAN': True}, tmp_path, now=NOW, limit=1)['rows'] == v['rows'][:1]


def test_flag_on_without_a_ledger_is_empty_and_creates_nothing(tmp_path):
    assert L.scan_view({'AI_SCAN': True}, tmp_path, now=NOW) == dict(on=True, rows=[], skipped=0, error=None)
    assert not os.path.exists(os.path.join(tmp_path, L.LEDGER_DIR))


def test_a_replay_that_fails_closed_shows_no_rows_and_the_reason(tmp_path):
    s = sig()
    p = L.ledger_path(tmp_path)
    write_all(p, [shadow(s)])                                                  # joins no recorded signal
    v = L.scan_view({'AI_SCAN': True}, tmp_path, now=NOW)
    assert v['rows'] == [] and 'joins no recorded signal' in v['error']


# ------------------------------------------------------------------ settings + app endpoint
def test_ai_scan_defaults_off_and_is_a_validated_bool():
    assert E.GLOBAL_DEFAULTS['AI_SCAN'] is False
    E.validate_settings(dict(schema_version=E.SETTINGS_SCHEMA, AI_SCAN=True))
    with pytest.raises(E.SchemaError):
        E.validate_settings(dict(schema_version=E.SETTINGS_SCHEMA, AI_SCAN='on'))


class StrictEngine:
    """Only S / live / dir may be read; any order, lock, risk or settings path fails the test."""
    ALLOWED = ('S', 'live', 'dir')

    def __init__(self, S, live, d):
        object.__setattr__(self, '_d', dict(S=S, live=live, dir=d))
        object.__setattr__(self, 'touched', [])

    def __getattr__(self, k):
        self.touched.append(k)
        if k in self.ALLOWED: return self._d[k]
        raise AssertionError(f'/api/ai_scan touched engine.{k}')

    def __setattr__(self, k, v):
        raise AssertionError(f'/api/ai_scan wrote engine.{k}')


@pytest.fixture
def appmod(monkeypatch):
    import app as AP
    return AP


def test_endpoint_has_no_authority_and_never_mutates_settings(appmod, tmp_path):
    write_all(L.ledger_path(tmp_path), recs())
    S = {'AI_SCAN': True, 'ENTRIES_PAUSED': False, 'MAX_LEVERAGE': 3}
    before = copy.deepcopy(S)
    e = StrictEngine(S, False, str(tmp_path))
    v = appmod.ai_scan_view(types.SimpleNamespace(engine=e), now=NOW)
    assert v['on'] is True and len(v['rows']) == 2
    assert S == before and set(e.touched) <= set(StrictEngine.ALLOWED)
    S['AI_SCAN'] = False
    assert appmod.ai_scan_view(types.SimpleNamespace(engine=e), now=NOW) == {'on': False, 'rows': []}


def test_endpoint_is_off_in_live_mode_and_the_setting_is_paper_only(appmod, tmp_path):
    write_all(L.ledger_path(tmp_path), recs())
    e = StrictEngine({'AI_SCAN': True}, True, str(tmp_path))
    assert appmod.ai_scan_view(types.SimpleNamespace(engine=e), now=NOW) == {'on': False, 'rows': []}
    live = types.SimpleNamespace(S=copy.deepcopy(E.GLOBAL_DEFAULTS), live=True)
    with pytest.raises(ValueError, match='PAPER only'):
        appmod.settings_update(live, {'AI_SCAN': True})
    paper = types.SimpleNamespace(S=copy.deepcopy(E.GLOBAL_DEFAULTS), live=False)
    ns, after = appmod.settings_update(paper, {'AI_SCAN': True})
    assert ns['AI_SCAN'] is True and after == [] and paper.S['AI_SCAN'] is False   # validated on a copy
    with pytest.raises(ValueError):
        appmod.settings_update(paper, {'AI_SCAN': 'yes'})


def test_endpoint_is_get_only(appmod, tmp_path, monkeypatch):
    write_all(L.ledger_path(tmp_path), recs())
    monkeypatch.setattr(appmod, 'APP', types.SimpleNamespace(engine=StrictEngine({'AI_SCAN': True}, False, str(tmp_path))),
                        raising=False)
    with pytest.raises(ValueError, match='unknown request'):
        appmod.handle('/api/ai_scan', {})                                    # the POST dispatcher has no such action
    srv = ThreadingHTTPServer(('127.0.0.1', 0), appmod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        hdr = {'Host': appmod.HOSTS[0], 'X-ZB-Token': appmod.TOKEN, 'Content-Type': 'application/json',
               'Origin': appmod.ORIGINS[0]}

        def call(method, body=None):
            c = http.client.HTTPConnection('127.0.0.1', srv.server_address[1], timeout=10)
            c.request(method, '/api/ai_scan', body=body, headers=hdr)
            r = c.getresponse(); out = (r.status, r.read()); c.close()
            return out
        st, b = call('GET')
        assert st == 200 and json.loads(b)['on'] is True and len(json.loads(b)['rows']) == 2
        st, b = call('POST', b'{}')
        assert st == 400 and json.loads(b)['ok'] is False
        for m in ('PUT', 'DELETE', 'PATCH'):
            assert call(m)[0] == 501
    finally:
        srv.shutdown(); srv.server_close()


def _resealed(rec, **kw):
    """Anyone with write access can reseal a line (the record hash is unkeyed): the reader must still fail closed."""
    return A._seal({**copy.deepcopy(rec), **kw})


@pytest.mark.parametrize('make', [
    lambda s, o: _resealed(s, signal_id=['x']), lambda s, o: _resealed(s, signal_id={'a': 1}),
    lambda s, o: _resealed(s, signal_id=None), lambda s, o: _resealed(shadow(s), epoch=['e']),
    lambda s, o: _resealed(o, cost_revision_id=['r']), lambda s, o: _resealed(o, source={'m': 1}),
    lambda s, o: _resealed(s, signal_id=''), lambda s, o: _resealed(o, cost_revision_id=None),
    lambda s, o: _resealed(o, cost_model_hash='b' * 64),                       # measured with the inactive id set too
    lambda s, o: _resealed(o, source='other'),
])
def test_a_resealed_line_with_a_malformed_key_is_skipped_never_raised(tmp_path, make):
    """Cowork 6460365559 R1: an unhashable or None key part used to escape as TypeError from fold / the ledger index."""
    p = L.ledger_path(tmp_path)
    s = sig(); o = outcome(s)
    write_all(p, [s, o])
    with pytest.raises(A.AIScanError, match='malformed key'):
        A.record_key(make(s, o))
    with open(p, 'ab') as f:
        f.write(json.dumps(make(s, o)).encode() + b'\n')
    led = L.ScanLedger(p)                                                      # constructs; the line is skipped
    assert led.records() == [s, o] and led.skipped == 1
    v = L.scan_view({'AI_SCAN': True}, tmp_path, now=NOW)
    assert v['on'] is True and v['skipped'] == 1 and v['error'] is None and len(v['rows']) == 1


def test_a_directory_at_the_ledger_path_fails_closed_with_an_error_not_an_exception(tmp_path):
    p = L.ledger_path(tmp_path)
    os.makedirs(p)
    v = L.scan_view({'AI_SCAN': True}, tmp_path, now=NOW)
    assert v['on'] is True and v['rows'] == [] and 'ledger unreadable' in v['error']
    led = L.ScanLedger(p)                                                      # construction never raises
    assert 'ledger unreadable' in led.error and led.records is not None
    with pytest.raises(A.AIScanError, match='append refused'):
        led.append(sig())
    assert os.path.isdir(p)
