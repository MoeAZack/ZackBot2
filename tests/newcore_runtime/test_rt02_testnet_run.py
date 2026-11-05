"""NEWCORE runtime testnet launcher (tools/newcore_testnet_run.py) against a scripted testnet sender.

No network and no real credentials: the sender answers by path, and the keys are throwaway test strings.  The
launcher needs TNET-02 (PR #294, stacked on #291); on a base without it, only the refusals that happen before
TNET-02 is needed run, and the rest are skipped.  All text I/O is utf-8."""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import newcore_testnet_run as T  # noqa: E402

from newcore.domain import BindingChanged  # noqa: E402
from newcore.domain.account import BindingState, Environment, confirmation_phrase  # noqa: E402
from newcore.runtime.config import config_from_mapping  # noqa: E402

KEY, SECRET = 'TestKeyNotReal0000000001', 'TestSecretNotReal000000001'
ENV = {'ZACKBOT_TNET02_API_KEY': KEY, 'ZACKBOT_TNET02_API_SECRET': SECRET}
ACCOUNT = 'acct_' + '0' * 31 + '1'
WALL0 = 2_000_000_000_000


def acct_dir(tmp_path):
    return str(tmp_path / 'acct1')


def write_json(path, value):
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(value, handle)
    return str(path)


def config(tmp_path, **extra):
    row = {'schema': 'zackbot.newcore.runtime.config.v1', 'environment': 'testnet',
           'accounts': [{'account_id': ACCOUNT, 'aggregate_id': 'pf_' + '0' * 31 + '1',
                         'account_dir': acct_dir(tmp_path), 'egress_id': 'testnet-1'}]}
    row.update(extra)
    return write_json(tmp_path / 'config.json', row)


def load(path):
    with open(path, encoding='utf-8') as handle:
        return config_from_mapping(json.load(handle))


def needs_tnet02():
    return pytest.importorskip('newcore.tnet02', reason='needs TNET-02 (PR #294)')


def account_file(tmp_path, *, key=KEY, account_id=ACCOUNT, **extra):
    from newcore.tnet02.wiring import key_digest
    digest = key_digest(key)
    row = {'account_id': account_id, 'label': 'rt-testnet', 'key_digest': digest, 'hedge_mode': True,
           'settlement_asset': 'USDT', 'confirmed_at_ms': WALL0 - 60_000,
           'typed_phrase': confirmation_phrase(account_id, digest)}
    row.update(extra)
    return write_json(tmp_path / 'account.json', row)


@pytest.fixture
def pins(tmp_path, monkeypatch):
    needs_tnet02()
    import tnet02_run
    monkeypatch.setattr(tnet02_run, 'MACHINE_PIN_STORE', tmp_path / 'machine_pins.json')


class ScriptedTestnet:
    """Answers the launcher's reads; any order-placing request fails the test."""

    def __init__(self, origin):
        self.origin = origin
        self.requests = []
        self.lock = threading.Lock()

    def __call__(self, request):
        from newcore.venue.binance_usdm_testnet import HttpResponse
        path = request.url[len(self.origin):]
        with self.lock:
            self.requests.append((request.method, path))
        assert request.method == 'GET', request
        body = {
            '/fapi/v1/exchangeInfo': {'rateLimits': [{'rateLimitType': 'REQUEST_WEIGHT', 'interval': 'MINUTE',
                                                      'intervalNum': 1, 'limit': 2400}]},
            '/fapi/v1/rateLimit/order': [{'rateLimitType': 'ORDERS', 'interval': 'SECOND', 'intervalNum': 10,
                                          'limit': 300},
                                         {'rateLimitType': 'ORDERS', 'interval': 'MINUTE', 'intervalNum': 1,
                                          'limit': 1200}],
            '/fapi/v2/positionRisk': [],
            '/fapi/v1/openOrders': [],
            '/fapi/v1/openAlgoOrders': [],
        }[path]
        return HttpResponse(200, json.dumps(body).encode('utf-8'), None, 10)


class FakeTime:
    def __init__(self):
        self.mono, self.wall = 1_000_000, WALL0
        self.lock = threading.Lock()

    def mono_ms(self):
        with self.lock:
            return self.mono

    def wall_ms(self):
        with self.lock:
            return self.wall

    def sleep(self, seconds):
        with self.lock:
            self.mono += int(seconds * 1000)
            self.wall += int(seconds * 1000)
        time.sleep(0.003)


def hooks(made=None):
    clock = FakeTime()

    def factory(origin):
        sender = ScriptedTestnet(origin)
        if made is not None:
            made.append(sender)
        return sender
    return {'sleep': clock.sleep, 'monotonic_clock': clock.mono_ms, 'wall_clock': clock.wall_ms,
            'sender_factory': factory}


# ------------------------------------------------------------------------------------------------ refusals (no TNET-02)
@pytest.mark.parametrize('variable', ['LIVE_CONFIRM', 'zackbot_mainnet'])
def test_live_switch_is_refused_before_anything(tmp_path, variable):
    out = io.StringIO()
    argv = ['run', '--config', config(tmp_path), '--account-file', 'x', '--symbols', 'BTCUSDT']
    assert T.main(argv, environ={variable: ''}, out=out) == T.EXIT_REFUSED
    assert json.loads(out.getvalue())['code'] == 'live_switch_present'


def test_a_paper_config_is_refused(tmp_path):
    out = io.StringIO()
    cfg = config(tmp_path, environment='paper_sim')
    assert T.main(['init', '--config', cfg, '--account-file', 'x'], environ={}, out=out) == T.EXIT_REFUSED
    assert json.loads(out.getvalue())['code'] == 'testnet_only'
    assert not os.path.exists(acct_dir(tmp_path))


def test_a_mainnet_config_is_refused(tmp_path):
    out = io.StringIO()
    cfg = config(tmp_path, environment='mainnet')
    assert T.main(['init', '--config', cfg, '--account-file', 'x'], environ={}, out=out) == T.EXIT_REFUSED
    assert json.loads(out.getvalue())['code'] == 'environment_forbidden'


def test_entries_without_a_factory_are_refused(tmp_path):
    with pytest.raises(T.TestnetRunError, match='entries_source_missing'):
        T.run_testnet(load(config(tmp_path, entries_enabled=True)), 'x', symbols=('BTCUSDT',), emit=print)


# ------------------------------------------------------------------------------------------------ init
def test_init_journals_the_confirmed_testnet_binding_once(tmp_path, pins):
    cfg, acc = config(tmp_path), account_file(tmp_path)
    assert T.init_testnet(load(cfg), acc) == 'bound'
    assert T.init_testnet(load(cfg), acc) == 'already_bound'
    from newcore.store import recover_journal
    journal = recover_journal(acct_dir(tmp_path), ACCOUNT, 'pf_' + '0' * 31 + '1').journal
    try:
        (ev,) = [e for e in journal.read() if isinstance(e, BindingChanged)]
    finally:
        journal.close()
    assert ev.to_state is BindingState.CONFIRMED and ev.binding.environment is Environment.TESTNET


def test_init_refuses_a_different_binding_and_a_foreign_account(tmp_path, pins):
    cfg = config(tmp_path)
    assert T.init_testnet(load(cfg), account_file(tmp_path)) == 'bound'
    with pytest.raises(T.TestnetRunError, match='already_bound_differently'):
        T.init_testnet(load(cfg), account_file(tmp_path, key='AnotherTestKey00000000002'))
    with pytest.raises(T.TestnetRunError, match='account_file_account_mismatch'):
        T.init_testnet(load(cfg), account_file(tmp_path, account_id='acct_' + '0' * 31 + '2'))


def test_init_refuses_a_wrong_typed_phrase(tmp_path, pins):
    with pytest.raises(T.TestnetRunError):
        T.init_testnet(load(config(tmp_path)), account_file(tmp_path, typed_phrase='BIND yes'))
    assert not os.path.exists(acct_dir(tmp_path))


# ------------------------------------------------------------------------------------------------ run
def test_run_proves_a_flat_testnet_account_with_reads_only(tmp_path, pins):
    cfg, acc = config(tmp_path), account_file(tmp_path)
    T.init_testnet(load(cfg), acc)
    made, rows = [], []
    final = T.run_testnet(load(cfg), acc, symbols=('BTCUSDT',), emit=rows.append, max_steps=60, environ=ENV,
                          **hooks(made))
    (account,) = final['accounts']
    assert account['holds'] == [] and account['plan'] == 'flat_confirmed' and account['restore_proven'], final
    assert final['environment'] == 'testnet' and final['entries_enabled'] is False
    paths = {p for _m, p in made[0].requests}
    assert {'/fapi/v1/exchangeInfo', '/fapi/v1/rateLimit/order', '/fapi/v2/positionRisk', '/fapi/v1/openOrders',
            '/fapi/v1/openAlgoOrders'} <= paths
    assert all(m == 'GET' for m, _p in made[0].requests)            # nothing but reads
    pinned = json.loads((tmp_path / 'machine_pins.json').read_text(encoding='utf-8'))
    assert list(pinned.values()) == [made[0].origin]


def test_run_refuses_keys_that_are_not_the_bound_account_before_any_request(tmp_path, pins):
    cfg, acc = config(tmp_path), account_file(tmp_path)
    T.init_testnet(load(cfg), acc)
    made = []
    other = {'ZACKBOT_TNET02_API_KEY': 'SomeoneElseTestKey000003', 'ZACKBOT_TNET02_API_SECRET': SECRET}
    with pytest.raises(T.TestnetRunError, match='key_digest_mismatch'):
        T.run_testnet(load(cfg), acc, symbols=('BTCUSDT',), emit=print, max_steps=1, environ=other, **hooks(made))
    assert made[0].requests == []
    assert not (tmp_path / 'machine_pins.json').exists()


def test_run_refuses_a_key_already_pinned_to_the_other_origin(tmp_path, pins):
    from newcore.tnet02.wiring import key_digest
    cfg, acc = config(tmp_path), account_file(tmp_path)
    T.init_testnet(load(cfg), acc)
    write_json(tmp_path / 'machine_pins.json', {key_digest(KEY): 'https://demo-fapi.binance.com'})
    made = []
    with pytest.raises(T.TestnetRunError, match='credentials_origin_reuse'):
        T.run_testnet(load(cfg), acc, symbols=('BTCUSDT',), emit=print, max_steps=1, environ=ENV, **hooks(made))
    assert made[0].requests == []


def test_run_without_init_is_refused(tmp_path, pins):
    with pytest.raises(T.TestnetRunError, match='account_not_initialised'):
        T.run_testnet(load(config(tmp_path)), account_file(tmp_path), symbols=('BTCUSDT',), emit=print,
                      environ=ENV, **hooks())


def test_run_refuses_bad_symbols(tmp_path, pins):
    cfg, acc = config(tmp_path), account_file(tmp_path)
    T.init_testnet(load(cfg), acc)
    for symbols in ((), ('btc usdt',), tuple(f'S{i}USDT' for i in range(9))):
        with pytest.raises(T.TestnetRunError, match='symbols_invalid'):
            T.run_testnet(load(cfg), acc, symbols=symbols, emit=print, environ=ENV, **hooks())


def test_cli_run_end_to_end(tmp_path, pins):
    cfg, acc = config(tmp_path), account_file(tmp_path)
    out = io.StringIO()
    assert T.main(['init', '--config', cfg, '--account-file', acc], environ={}, out=out) == T.EXIT_OK
    out = io.StringIO()
    code = T.main(['run', '--config', cfg, '--account-file', acc, '--symbols', 'BTCUSDT', '--max-steps', '40',
                   '--status-every', '10'], environ=ENV, out=out, run_hooks=hooks())
    rows = [json.loads(x) for x in out.getvalue().splitlines()]
    assert code == T.EXIT_OK and rows[0]['status'] == 'booted' and rows[-1]['status'] == 'stopped'
