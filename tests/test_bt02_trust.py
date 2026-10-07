"""BT02 trust gate (Codex review of PR #18): untrusted exchange rules must never change reported backtest numbers.

- app.run_backtest_job applies exchange rules ONLY when the snapshot state is 'ok' for the selected environment
  (feasibility.trusted_rules); every other state (missing, unverified, file import, stale, invalid, wrong environment,
  'off') runs the legacy floor - results identical to exchange_rules=None - and reports rules_applied=False + reason.
- No default rule data is shipped (the placeholder file was materially wrong vs live testnet).
- exchange_rules.build_file (file import) is never trusted: verified=False, provenance 'file', capture time from the
  file's serverTime / --captured-at, never the current clock. Only `fetch` (direct exchangeInfo GET) is trusted.
"""
import copy, io, json, os, sys, time
import pytest
import backtest as B
import feasibility as F
import exchange_rules as XR
import test_bt02_exchange_filters as T

ROOT = T.ROOT
NOW = T.NOW


# ---------------------------------------------------------------- feasibility.trusted_rules / snapshot_state provenance
def test_trusted_rules_only_for_ok_and_matching_environment():
    s = T.snap()
    assert F.trusted_rules(s, 'ok', 'testnet') == (s, True, '')
    for st in ('unavailable', 'unverified', 'stale', 'invalid', 'wrong_environment', 'off', None):
        r, applied, why = F.trusted_rules(s, st, 'testnet')
        assert r is None and applied is False and 'NOT applied' in why and 'not execution-realistic' in why, st
    r, applied, why = F.trusted_rules(s, 'ok', 'mainnet')           # defence in depth: 'ok' computed for another env
    assert r is None and applied is False and 'testnet' in why
    assert F.trusted_rules(None, 'ok', 'testnet')[0] is None
    assert F.trusted_rules(s, 'ok', 'paper')[0] is None


def test_only_direct_fetch_or_engine_provenance_can_be_ok():
    for prov, want in (('fetch', 'ok'), ('engine', 'ok'), ('file', 'unverified'), ('file-override', 'unverified'),
                       (None, 'unverified'), ('', 'unverified')):
        s = T.snap(); s['provenance'] = prov
        if prov is None: del s['provenance']
        assert F.snapshot_state(s, NOW, 'testnet')[0] == want, prov
        assert F.trusted_rules(s, F.snapshot_state(s, NOW, 'testnet')[0], 'testnet')[1] is (want == 'ok')


# ---------------------------------------------------------------- file import is never trusted
def _info_file(tmp_path, server_ms=None, name='exchangeInfo.json'):
    inf = T.info(T.TESTNET)
    if server_ms is not None: inf['serverTime'] = server_ms
    p = tmp_path / name; p.write_text(json.dumps(inf), encoding='utf-8')
    return str(p)


def test_file_import_defaults_unverified_and_takes_capture_time_from_the_file(tmp_path):
    out = str(tmp_path / 'r.json')
    s, _ = XR.build_file(_info_file(tmp_path), 'testnet', out=out)              # no serverTime, no --captured-at
    assert s['verified'] is False and s['provenance'] == 'file' and s['fetched_at'] is None
    assert F.snapshot_state(XR.load('testnet', path=out), NOW, 'testnet')[0] == 'unverified'
    s, _ = XR.build_file(_info_file(tmp_path, server_ms=int(NOW * 1000)), 'testnet', out=out)     # fresh serverTime
    assert s['fetched_at'] == XR._iso(NOW) and s['verified'] is False
    assert F.snapshot_state(s, NOW, 'testnet')[0] == 'unverified'               # fresh but imported: still not ok
    s, _ = XR.build_file(_info_file(tmp_path), 'testnet', out=out, captured_at='2026-09-20T00:00:00Z')
    assert s['fetched_at'] == '2026-09-20T00:00:00Z'
    with pytest.raises(ValueError): XR.build_file(_info_file(tmp_path), 'testnet', out=out, captured_at='yesterday')


def test_old_file_import_cannot_become_green(tmp_path):
    out = str(tmp_path / 'r.json')
    s, _ = XR.build_file(_info_file(tmp_path, server_ms=int((NOW - 200 * 86400) * 1000)), 'testnet', out=out)
    assert s['fetched_at'] == XR._iso(NOW - 200 * 86400)                       # the file's own time, not "now"
    assert F.snapshot_state(s, NOW, 'testnet')[0] != 'ok'
    s['verified'] = True                                                        # hand-edited trust flag
    assert F.snapshot_state(s, NOW, 'testnet')[0] != 'ok'
    s['fetched_at'] = XR._iso(NOW)
    assert F.snapshot_state(s, NOW, 'testnet')[0] == 'unverified'              # provenance 'file' is never ok


def test_wrong_environment_import_cannot_become_green(tmp_path):
    out = str(tmp_path / 'exchange_rules_testnet.json')                         # a mainnet import saved as the testnet file
    s, _ = XR.build_file(_info_file(tmp_path, server_ms=int(NOW * 1000)), 'mainnet', out=out)
    assert F.snapshot_state(XR.load('testnet', path=out), NOW, 'testnet')[0] == 'wrong_environment'
    assert F.snapshot_state(XR.load('testnet', path=out), NOW, 'mainnet')[0] == 'unverified'


def test_cli_build_warns_on_stderr_and_rejects_the_old_flag(tmp_path, monkeypatch):
    err = io.StringIO(); monkeypatch.setattr(sys, 'stderr', err); monkeypatch.setattr(sys, 'stdout', io.StringIO())
    out = str(tmp_path / 'r.json')
    assert XR.main(['build', _info_file(tmp_path), '--env', 'testnet', '--out', out]) == 0
    assert 'NOT trusted' in err.getvalue() and 'exchange_rules.py fetch --env testnet' in err.getvalue()
    assert XR.load('testnet', path=out)['verified'] is False
    assert XR.main(['build', _info_file(tmp_path), '--env', 'testnet', '--out', out, '--fetched-at', T.ISO_NOW]) == 2


def test_direct_fetch_is_the_trusted_path(tmp_path):
    calls = []

    class R:
        def raise_for_status(self): pass
        def json(self):
            inf = T.info(T.TESTNET); inf['serverTime'] = int(NOW * 1000); return inf

    out = str(tmp_path / 'exchange_rules_testnet.json')
    s, old = XR.fetch('testnet', out=out, get=lambda url, timeout: calls.append(url) or R())
    assert calls == ['https://testnet.binancefuture.com/fapi/v1/exchangeInfo'] and old is None
    assert s['verified'] is True and s['provenance'] == 'fetch' and s['fetched_at'] == XR._iso(NOW)
    assert F.snapshot_state(XR.load('testnet', path=out), NOW, 'testnet')[0] == 'ok'
    XR.fetch('mainnet', out=str(tmp_path / 'm.json'), get=lambda url, timeout: calls.append(url) or R())
    assert calls[-1] == 'https://fapi.binance.com/fapi/v1/exchangeInfo'


# ---------------------------------------------------------------- no default rule data is shipped
def test_no_rule_snapshot_is_shipped_or_bundled(tmp_path):
    ps = open(os.path.join(ROOT, 'installer.ps1'), encoding='utf-8-sig').read()
    assert 'exchange_rules_testnet' not in ps and 'exchange_rules_mainnet' not in ps
    import app as A, inspect
    assert 'exchange_rules_' not in inspect.getsource(A.selftest)
    p = os.path.join(ROOT, 'data', 'exchange_rules_testnet.json')
    if os.path.exists(p):                                       # only a real `fetch` may have created it locally
        assert XR.load('testnet', path=p).get('provenance') == 'fetch' and 'PLACEHOLDER' not in XR.load('testnet', path=p)['source']
    assert XR.load('testnet', root=str(tmp_path)) is None
    assert F.snapshot_state(XR.load('testnet', root=str(tmp_path)), NOW, 'testnet')[0] == 'unavailable'


def test_app_missing_snapshot_is_unavailable_with_capture_instructions(monkeypatch, tmp_path):
    import app as A
    # the installed exe has a read-only temp bundle: a fetched snapshot must be found in the user data folder first
    assert A.RULES_ROOTS[0] == A.DATA and A.RULES_ROOTS[1] == os.path.join(A.BUNDLE, 'data')
    monkeypatch.setattr(A, 'APP', None); monkeypatch.setattr(A, 'RULES_ROOTS', [str(tmp_path)])
    snap, st, detail = A.exchange_rules_now()
    assert snap is None and st == 'unavailable' and 'python exchange_rules.py fetch --env testnet' in detail


def test_panel_says_rules_not_applied():
    src = open(os.path.join(ROOT, 'panel.html'), encoding='utf-8').read()
    assert 'f.rules_applied===false' in src and 'Exchange rules NOT applied' in src and 'exchange_rules.py fetch' in src


# ---------------------------------------------------------------- app backtests: untrusted rules never change the numbers


def _biting_rules():
    """Rules that certainly change a calm $500 run if applied: every coin needs 1,000,000 USDT notional."""
    import engine as E
    return {s: dict(step=0.001, min_qty=0.001, min_notional=1e6, tick=0.01) for s in E.CORE8}


def _write(root, snap, env='testnet'):
    os.makedirs(str(root), exist_ok=True)
    XR.save(snap, XR.path_for(env, root=str(root)))


def _fresh(env='testnet', **kw):
    s = F.build_snapshot(F.exchange_info_from_rules(_biting_rules()), env, 'test fetch',
                         fetched_at=XR._iso(time.time() - 3600), verified=True, provenance='fetch')
    s.update(kw)
    return s


def _job(monkeypatch, root, seen, req_extra=None):
    import app as A, engine as E
    monkeypatch.setattr(A, 'APP', None)
    monkeypatch.setattr(A, 'RULES_ROOTS', [str(root)])
    monkeypatch.setattr(A, 'get_candles', T._csv_candles)
    out = os.path.join(str(root), '_appdata'); os.makedirs(os.path.join(out, 'backtests'), exist_ok=True)
    monkeypatch.setattr(A, 'DATA', out)                          # results go to a temp folder, not the user's data folder
    real = A.BT.run
    monkeypatch.setattr(A.BT, 'run', lambda *a, **k: seen.append(k.get('exchange_rules')) or real(*a, **k))
    jid = f'20260101-000000-trust-{os.getpid()}-{len(seen)}-{id(root)}'
    A.JOBS[jid] = dict(status='queued')
    A.run_backtest_job(jid, dict(name='trust', sleeves=copy.deepcopy(E.PRESETS['calm']['sleeves']), days=120, tf='4h', start=500,
                                 universe=list(E.CORE8), **(req_extra or {})))
    j = A.JOBS.pop(jid)
    assert j['status'] == 'done', j
    return j['result']


def _headline(res):
    return json.dumps(dict(stats=res['stats'], curve=res['curve'], by_symbol=res['by_symbol'], by_sleeve=res['by_sleeve'],
                           years=res['years'], oos=res['oos'], dd=res['dd_curve']), sort_keys=True, default=str)


def _untrusted_cases(tmp_path):
    imp = tmp_path / 'imp'; os.makedirs(str(imp))
    inf = F.exchange_info_from_rules(_biting_rules()); inf['serverTime'] = int(time.time() * 1000)
    (imp / 'info.json').write_text(json.dumps(inf), encoding='utf-8')
    imported, _ = XR.build_file(str(imp / 'info.json'), 'testnet', out=str(tmp_path / 'scratch.json'))
    bad = _fresh(); bad['symbols']['BTCUSDT']['step'] = 0                       # invalid, other coins still biting
    return {
        'missing': None,
        'unverified': _fresh(verified=False),
        'file_import': imported,
        'file_import_forced_verified': dict(imported, verified=True),
        'stale': _fresh(fetched_at=XR._iso(time.time() - 60 * 86400)),
        'no_time': _fresh(fetched_at=None),
        'invalid': bad,
        'wrong_environment': _fresh(env='mainnet'),
    }


def test_untrusted_snapshots_never_change_app_backtest_results(monkeypatch, tmp_path):
    seen = []
    base = _job(monkeypatch, tmp_path / 'none', seen)                            # no snapshot at all = exchange_rules=None
    assert seen and all(x is None for x in seen)
    assert base['feasibility']['rules_applied'] is False and base['feasibility']['groups']['4h']['mode'] == 'legacy'
    # positive control: the same biting rules, trusted -> the numbers DO change (so the comparison below can detect a leak)
    seen.clear(); _write(tmp_path / 'ok', _fresh())
    ok = _job(monkeypatch, tmp_path / 'ok', seen)
    assert seen and all(x is not None for x in seen) and ok['feasibility']['rules_applied'] is True
    assert _headline(ok) != _headline(base)
    for name, s in _untrusted_cases(tmp_path).items():
        root = tmp_path / ('c_' + name)
        os.makedirs(str(root))
        if s is not None: _write(root, s)
        seen.clear()
        res = _job(monkeypatch, root, seen)
        f = res['feasibility']
        assert seen and all(x is None for x in seen), name
        assert f['rules_applied'] is False and f['execution_realistic'] is False and 'NOT applied' in f['rules_reason'], name
        assert f['rules_state'] != 'ok' and f['groups']['4h']['mode'] == 'legacy', name
        assert _headline(res) == _headline(base), name


def test_rules_off_request_is_reported_not_applied(monkeypatch, tmp_path):
    _write(tmp_path, _fresh())
    seen = []
    res = _job(monkeypatch, tmp_path, seen, dict(exchange_rules='off'))
    assert all(x is None for x in seen)
    assert res['feasibility']['rules_state'] == 'off' and res['feasibility']['rules_applied'] is False


def test_engine_rules_for_another_environment_are_not_applied(monkeypatch, tmp_path):
    """A connected LIVE engine selects mainnet; a testnet file must not be used for it (and vice versa)."""
    import app as A
    _write(tmp_path, _fresh())                                                   # testnet snapshot only
    monkeypatch.setattr(A, 'RULES_ROOTS', [str(tmp_path)])

    class Eng:
        live = True; rules = {}; rules_meta = None
    monkeypatch.setattr(A, 'APP', type('X', (), dict(engine=Eng()))())
    snap, st, _ = A.exchange_rules_now()
    assert st == 'unavailable' and A.rules_env() == 'mainnet'
    assert F.trusted_rules(_fresh(), 'ok', A.rules_env())[1] is False


def test_other_backtest_callers_never_load_rule_files():
    """Lab / research / study runs go through backtest.run without exchange rules (legacy floor); replay takes them only as
    an explicit, documented exploratory argument. Only app.run_backtest_job loads a snapshot, through trusted_rules."""
    for f in ('lab.py', 'research_combos.py', 'research_grid.py', 'research_long.py', 'research_long2.py', 'research_refresh.py',
              'trade_audit.py', 'verify.py'):
        p = os.path.join(ROOT, f)
        if os.path.exists(p):
            src = open(p, encoding='utf-8').read()
            assert 'exchange_rules' not in src and 'XRULES' not in src, f
    rp = open(os.path.join(ROOT, 'replay.py'), encoding='utf-8').read()
    assert 'exchange_rules=None' in rp and 'exploratory' in rp and 'XRULES' not in rp and 'import exchange_rules' not in rp
    app_src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
    assert 'exchange_rules=xrun' in app_src and 'exchange_rules=xsnap' not in app_src
    assert app_src.count('F.trusted_rules(') == 1
