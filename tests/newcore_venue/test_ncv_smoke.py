"""S5 read-only smoke (tools/newcore_smoke.py + newcore.venue.smoke). Fake HTTP and DUMMY credentials only."""
import ast
import importlib.util
import io
import json
import logging
import os
import sys

import pytest

from newcore.venue import smoke as smoke_mod
from newcore.venue.credentials import CredentialStore
from newcore.venue.smoke import CORE8, NOT_FLAT, READ_ONLY_CALLS
from newcore.venue.transport import BinanceTestnetTransport
from newcore.venue.wire import WireTimeout

from ncv_support import DUMMY_KEY, DUMMY_SECRET, FakeHttp, fixture, raw

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOL = os.path.join(REPO, 'tools', 'newcore_smoke.py')
ACCOUNT = '8c1d2e3f-4a5b-4c6d-8e7f-90a1b2c3d4e5'   # CLI account ids are dashed UUIDs
NOW = 1759960000000                 # after every income_mixed row, so the 7-day window holds them all
READ_PATHS = {'/fapi/v1/time', '/fapi/v1/exchangeInfo', '/fapi/v2/account', '/fapi/v2/positionRisk',
              '/fapi/v1/positionSide/dual', '/fapi/v1/openOrders', '/fapi/v1/openAlgoOrders', '/fapi/v1/income'}
ORDER_METHODS = ('place_market', 'place_stop_market', 'cancel_order', 'cancel_algo_order', 'query_order',
                 'query_algo_order')


class XorProtector:
    """Reversible stand-in for DPAPI so the tests run anywhere (NOT protection)."""

    def protect(self, data, entropy):
        return b'X' + bytes(b ^ 0x5A for b in data)

    def unprotect(self, data, entropy):
        return bytes(b ^ 0x5A for b in data[1:])


def load_tool():
    spec = importlib.util.spec_from_file_location('newcore_smoke_tool', TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def server_time():
    return raw(200, json.dumps({'serverTime': NOW}).encode(), {'X-MBX-USED-WEIGHT-1M': '1'})


def exchange_info_core8():
    body = json.loads(fixture('exchange_info').body)
    sol = body['symbols'][0]
    body['symbols'] = [dict(sol, symbol=s, pair=s) for s in CORE8]
    return raw(200, json.dumps(body).encode(), {'X-MBX-USED-WEIGHT-1M': '41'})


def flat_script(**override):
    s = dict(t1=server_time(), t2=server_time(), t3=server_time(), info=exchange_info_core8(), account='account_v2',
             positions=raw(200, b'[]'), dual='dual_side_true', oo=raw(200, b'[]'), algo=raw(200, b'[]'),
             income='income_mixed')
    s.update(override)
    return [s[k] for k in ('t1', 't2', 't3', 'info', 'account', 'positions', 'dual', 'oo', 'algo', 'income')]


@pytest.fixture
def env(tmp_path):
    root = str(tmp_path / 'secrets')
    CredentialStore(ACCOUNT, root=root, protector=XorProtector(), harden_acl=False).save(
        'testnet', DUMMY_KEY, DUMMY_SECRET, now_ms=NOW)
    return dict(root=root, cassettes=str(tmp_path / 'cassettes'), tmp=tmp_path)


def run(env, script, extra=()):
    http = FakeHttp(*script)
    out = io.StringIO()
    rc = load_tool().main(['--account-id', ACCOUNT, '--root', env['root'], '--cassette-dir', env['cassettes'],
                           *extra], http=http, local_clock=lambda: NOW, protector=XorProtector(), out=out)
    return rc, out.getvalue(), http


def cassette_files(env):
    d = env['cassettes']
    return [os.path.join(d, f) for f in os.listdir(d)] if os.path.isdir(d) else []


# ---------- happy path ----------

def test_flat_hedge_account_exits_zero_with_summary_and_clean_cassette(env):
    rc, out, http = run(env, flat_script())
    assert rc == 0, out
    assert 'environment   : testnet' in out and 'dbef' not in out and 'DUMM…ABCD' in out
    assert 'server offset +0 ms, RTT 0 ms' in out and 'position mode : HEDGE' in out
    assert 'USDT wallet 5000.12345678 / available 4913.52345678' in out
    assert 'positions     : flat' in out and 'open orders   : 0 classic, 0 algo/conditional' in out
    assert 'funding -0.06855 USDT, commission -1.1115 USDT, realized pnl -12.5 USDT (6 rows; TRANSFER 100' in out
    assert 'rules SOLUSDT  : tick 0.01  step 1  min qty 1  min notional 5' in out
    assert 'max used weight (1m) 60' in out and 'WARNING' not in out and 'READ-ONLY smoke' in out
    assert DUMMY_KEY not in out and DUMMY_SECRET not in out
    (path,) = cassette_files(env)
    assert os.path.basename(path) == f'smoke-{NOW}.json' and f'cassette      : {path}' in out
    text = open(path, encoding='utf-8').read()
    assert DUMMY_KEY not in text and DUMMY_SECRET not in text
    sigs = [r.query.rsplit('&signature=', 1)[1] for r in http.requests if r.signed]
    assert sigs and not any(s in text for s in sigs)
    assert len(json.loads(text)['interactions']) == 10


def test_default_cassette_dir_is_zackbotnc(env, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(env['tmp']))
    http = FakeHttp(*flat_script())
    rc = load_tool().main(['--account-id', ACCOUNT, '--root', env['root']], http=http, local_clock=lambda: NOW,
                          protector=XorProtector(), out=io.StringIO())
    assert rc == 0
    assert os.listdir(env['tmp'] / 'ZackBotNC' / 'cassettes') == [f'smoke-{NOW}.json']


# ---------- READ-ONLY proof ----------

def test_call_set_is_read_only_and_order_methods_untouched(env, monkeypatch):
    called = []
    for name in ORDER_METHODS:
        def forbidden(self, *a, _n=name, **k):
            raise AssertionError(f'order method {_n} called by the read-only smoke')
        monkeypatch.setattr(BinanceTestnetTransport, name, forbidden)
    for name in sorted(READ_ONLY_CALLS | {'klines', 'user_trades'}):
        orig = getattr(BinanceTestnetTransport, name)

        def spy(self, *a, _n=name, _o=orig, **k):
            called.append(_n)
            return _o(self, *a, **k)
        monkeypatch.setattr(BinanceTestnetTransport, name, spy)
    rc, out, http = run(env, flat_script())
    assert rc == 0, out
    assert set(called) <= READ_ONLY_CALLS and set(called) == READ_ONLY_CALLS
    assert all(r.method == 'GET' for r in http.requests)
    assert {r.url.split('binancefuture.com', 1)[1] for r in http.requests} <= READ_PATHS


@pytest.mark.parametrize('path', [TOOL, smoke_mod.__file__])
def test_smoke_sources_reference_no_order_or_setting_call(path):
    tree = ast.parse(open(path, encoding='utf-8').read())
    banned = set(ORDER_METHODS) | {'set_leverage', 'set_margin_type', 'set_hedge_mode', 'cancel_all', 'klines',
                                   'user_trades'}
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & banned
    strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not any(s.startswith('/fapi/') and ('order' in s.lower() or 'leverage' in s or 'marginType' in s)
                   for s in strings)


# ---------- warnings ----------

def test_not_flat_position_warns_and_exits_one(env):
    rc, out, _ = run(env, flat_script(positions='position_risk_hedge'))
    assert rc == 1 and NOT_FLAT in out and 'SOLUSDT LONG 10 @ 222.3' in out


def test_open_orders_warn_not_flat(env):
    rc, out, _ = run(env, flat_script(oo='open_orders', algo='open_algo_orders_list'))
    assert rc == 1 and NOT_FLAT in out and 'open orders   : 1 classic, 1 algo/conditional' in out


def test_one_way_mode_warns(env):
    rc, out, _ = run(env, flat_script(dual=raw(200, b'{"dualSidePosition": false}')))
    assert rc == 1 and 'position mode : ONE-WAY   WARNING' in out and 'NEWCORE expects hedge mode' in out


def test_missing_core_symbols_warn(env):
    rc, out, _ = run(env, flat_script(info='exchange_info'))
    assert rc == 1 and 'no usable rules on testnet for: ETHUSDT, BNBUSDT' in out


# ---------- Cowork round 2: overall smoke deadline ----------

class Ticking:
    """A monotonic clock that advances by `step` seconds on every read (each transport call reads it twice)."""

    def __init__(self, step):
        self.t, self.step = 1000.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def test_deadline_aborts_with_exit_six_and_saves_the_cassette(env):
    http = FakeHttp(*flat_script())
    out = io.StringIO()
    rc = load_tool().main(['--account-id', ACCOUNT, '--root', env['root'], '--cassette-dir', env['cassettes'],
                           '--deadline-s', '1'], http=http, local_clock=lambda: NOW, protector=XorProtector(),
                          out=out, monotonic=Ticking(0.3))
    text = out.getvalue()
    assert rc == 6 and 'SMOKE DEADLINE - smoke deadline of 1 s exceeded' in text and 'nothing was retried' in text
    assert len(http.requests) < 10                                   # stopped early, did not finish the reads
    (path,) = cassette_files(env)
    saved = open(path, encoding='utf-8').read()
    assert DUMMY_KEY not in saved and DUMMY_SECRET not in saved


def test_deadline_in_real_time_with_slow_reads(env):
    import time
    slow = FakeHttp(*flat_script())

    def http(request):
        time.sleep(0.3)                      # 10 reads = 3 s+ without a deadline
        return slow(request)
    out = io.StringIO()
    t0 = time.monotonic()
    rc = load_tool().main(['--account-id', ACCOUNT, '--root', env['root'], '--cassette-dir', env['cassettes'],
                           '--deadline-s', '0.5'], http=http, local_clock=lambda: NOW, protector=XorProtector(),
                          out=out)
    assert rc == 6 and time.monotonic() - t0 < 2.5 and len(cassette_files(env)) == 1


def test_default_deadline_is_sixty_seconds_and_generous_runs_pass(env):
    assert smoke_mod.DEFAULT_DEADLINE_S == 60.0
    rc, out, _ = run(env, flat_script())
    assert rc == 0, out


@pytest.mark.parametrize('bad', ['0', '-1', '3601', 'nan', 'abc'])
def test_bad_deadline_refused(env, bad):
    rc, out, http = run(env, [], ['--deadline-s', bad])
    assert rc == 2 and http.requests == []


def test_run_smoke_deadline_covers_income_pages_and_calibration():
    calls = []

    class SlowVenue:
        def __getattr__(self, name):
            def f(*a, **k):
                calls.append(name)
                raise AssertionError('must not be reached')
            return f
    oc_calls = []

    class OC:
        def resync(self, transport, samples):
            oc_calls.append(1)
            return transport.server_time()
    with pytest.raises(smoke_mod.SmokeDeadlineExceeded) as ei:
        smoke_mod.run_smoke(SlowVenue(), OC(), deadline_s=1, monotonic=Ticking(5.0))
    assert calls == [] and 'before server_time' in ei.value.describe()


def test_a_single_call_returning_after_the_deadline_aborts():
    clock = iter([0.0, 0.1, 5.0])                    # deadline base, before server_time, after server_time

    class OneRead:
        def server_time(self):
            return 'answer'

    class OC:
        def resync(self, transport, samples):
            transport.server_time()
            raise AssertionError('the late answer must not be used')
    with pytest.raises(smoke_mod.SmokeDeadlineExceeded) as ei:
        smoke_mod.run_smoke(OneRead(), OC(), deadline_s=1, monotonic=lambda: next(clock))
    assert ei.value.step == 'server_time' and 'exceeded at server_time' in ei.value.describe()


@pytest.mark.parametrize('bad', [0, -1, 3601, True, '60'])
def test_run_smoke_validates_deadline(bad):
    with pytest.raises(ValueError):
        smoke_mod.run_smoke(object(), object(), deadline_s=bad)


# ---------- typed failures: message, non-zero exit, no retry ----------

def test_unknown_read_stops_with_exit_four_and_no_retry(env):
    rc, out, http = run(env, flat_script(account=WireTimeout())[:5])
    assert rc == 4 and 'READ FAILED - account: UNKNOWN (timeout)' in out and 'Nothing was retried' in out
    assert len(http.requests) == 5 and http.script == []          # stopped at the failed read
    assert len(cassette_files(env)) == 1                          # the evidence is still saved


def test_rejected_read_reports_code(env):
    rej = raw(400, b'{"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."}')
    rc, out, http = run(env, flat_script(positions=rej)[:6])
    assert rc == 4 and 'positions: REJECTED by Binance, code -2015 REJECTED_MBX_KEY [auth]' in out


def test_timestamp_refusal_hints_clock(env):
    rc, out, _ = run(env, flat_script(account='err_timestamp')[:5])
    assert rc == 4 and 'code -1021' in out and 'clock out of sync' in out


def test_income_failure_is_a_failed_read(env):
    rc, out, _ = run(env, flat_script(income='err_502_html'))
    assert rc == 4 and 'income (7-day window): UNKNOWN (http_5xx)' in out


def test_clock_calibration_failure(env):
    rc, out, http = run(env, [WireTimeout(), WireTimeout(), WireTimeout()])
    assert rc == 4 and 'server_time (clock calibration): no_valid_sample' in out and len(http.requests) == 3


def test_missing_credentials_tells_owner_what_to_run(tmp_path):
    http = FakeHttp()
    out = io.StringIO()
    rc = load_tool().main(['--account-id', ACCOUNT, '--root', str(tmp_path / 'none'),
                           '--cassette-dir', str(tmp_path / 'c')], http=http, local_clock=lambda: NOW,
                          protector=XorProtector(), out=out)
    assert rc == 3 and http.requests == []
    assert f'python tools/newcore_keys.py set --env testnet --account-id {ACCOUNT}' in out.getvalue()


@pytest.mark.parametrize('extra', [['--api-key', 'x'], ['--secret=y'], [DUMMY_KEY]])
def test_keys_on_command_line_refused(env, extra):
    rc, out, http = run(env, [], extra)
    assert rc == 2 and http.requests == [] and DUMMY_KEY not in out


def test_legacy_cassette_dir_refused_before_any_read(env, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(env['tmp']))
    http = FakeHttp()
    out = io.StringIO()
    rc = load_tool().main(['--account-id', ACCOUNT, '--root', env['root'], '--cassette-dir',
                           str(env['tmp'] / 'ZackBot' / 'cassettes')], http=http, local_clock=lambda: NOW,
                          protector=XorProtector(), out=out)
    assert rc == 2 and http.requests == []


def test_post_write_leak_recheck_removes_the_file(env, monkeypatch):
    tool = load_tool()

    class LeakyRecorder(tool.CassetteRecorder):                  # simulates a broken leak check upstream
        def to_json(self):
            return json.dumps({'format': 'x', 'body': DUMMY_SECRET})
    monkeypatch.setattr(tool, 'CassetteRecorder', LeakyRecorder)
    out = io.StringIO()
    rc = tool.main(['--account-id', ACCOUNT, '--root', env['root'], '--cassette-dir', env['cassettes']],
                   http=FakeHttp(*flat_script()), local_clock=lambda: NOW, protector=XorProtector(), out=out)
    assert rc == 5 and 'contained a secret value and was removed' in out.getvalue()
    assert cassette_files(env) == []
    assert DUMMY_SECRET not in out.getvalue()


def test_recorder_redact_list_holds_the_stored_key_and_secret(env, monkeypatch):
    tool = load_tool()
    seen = {}

    class Spy(tool.CassetteRecorder):
        def __init__(self, inner, *, redact=(), note=''):
            seen['redact'] = tuple(redact)
            super().__init__(inner, redact=redact, note=note)
    monkeypatch.setattr(tool, 'CassetteRecorder', Spy)
    rc = tool.main(['--account-id', ACCOUNT, '--root', env['root'], '--cassette-dir', env['cassettes']],
                   http=FakeHttp(*flat_script()), local_clock=lambda: NOW, protector=XorProtector(), out=io.StringIO())
    assert rc == 0 and DUMMY_KEY in seen['redact'] and DUMMY_SECRET in seen['redact']


def test_scrubber_is_installed_during_and_removed_after(env):
    before_factory, before_hook = logging.getLogRecordFactory(), sys.excepthook
    run(env, flat_script())
    assert logging.getLogRecordFactory() is before_factory and sys.excepthook is before_hook
