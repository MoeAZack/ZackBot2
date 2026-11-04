"""RT-01 configuration refusals, the read-only check CLI, and the legacy/network import boundary."""
from __future__ import annotations

import ast
import glob
import io
import json
import os
import subprocess
import sys

import pytest

from newcore.runtime import AccountSpec, RuntimeConfig, RuntimeConfigError, RuntimeEnvironment, config_from_mapping
from newcore.runtime.__main__ import EXIT_OK, EXIT_REFUSED, EXIT_USAGE, main

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PACKAGE = os.path.join(ROOT, 'newcore', 'runtime')


def acct(n):
    return f'acct_{n:032x}'


def raw_config(**changes):
    row = {
        'schema': 'zackbot.newcore.runtime.config.v1',
        'environment': 'testnet',
        'accounts': [{'account_id': acct(1), 'aggregate_id': 'pf_' + '1' * 32,
                      'account_dir': 'C:/ZackBotNew/a1', 'egress_id': 'egress-1'}],
    }
    row.update(changes)
    return row


def spec(n=1, **changes):
    values = dict(account_id=acct(n), aggregate_id=f'pf_{n:032x}', account_dir=f'/tmp/a{n}', egress_id=f'e{n}')
    values.update(changes)
    return AccountSpec(**values)


# ------------------------------------------------------------------------------------------------ config
def test_minimal_config_defaults_to_testnet_with_entries_paused():
    config = config_from_mapping(raw_config())
    assert config.environment is RuntimeEnvironment.TESTNET
    assert config.entries_enabled is False
    assert len(config.accounts) == 1


@pytest.mark.parametrize('environment', ['mainnet', 'live', 'LIVE', 'testnet-live', 'production', 'real', 'paper',
                                         '', None, 1])
def test_mainnet_live_or_unknown_environment_is_refused(environment):
    with pytest.raises(RuntimeConfigError) as ex:
        config_from_mapping(raw_config(environment=environment))
    assert ex.value.code in ('environment_forbidden', 'environment_invalid')


@pytest.mark.parametrize('key', ['live_confirm', 'LIVE_CONFIRM', 'live', 'mainnet', 'api_key', 'api_secret',
                                 'secret', 'go_live', 'mainnet_ok'])
def test_live_and_credential_keys_are_refused_not_ignored(key):
    with pytest.raises(RuntimeConfigError, match='config_key_forbidden'):
        config_from_mapping(raw_config(**{key: True}))
    bad = raw_config()
    bad['accounts'][0][key] = 'x'
    with pytest.raises(RuntimeConfigError, match='account_key_forbidden'):
        config_from_mapping(bad)


def test_unknown_keys_schema_and_shape_are_refused():
    with pytest.raises(RuntimeConfigError, match='config_key_unknown'):
        config_from_mapping(raw_config(extra=1))
    with pytest.raises(RuntimeConfigError, match='schema_invalid'):
        config_from_mapping(raw_config(schema='v0'))
    with pytest.raises(RuntimeConfigError, match='config_not_object'):
        config_from_mapping([])
    bad = raw_config()
    del bad['accounts'][0]['egress_id']
    with pytest.raises(RuntimeConfigError, match='account_keys_invalid'):
        config_from_mapping(bad)


@pytest.mark.parametrize('field, value, code', [
    ('account_id', 'acct_1', 'account_id_invalid'), ('aggregate_id', 'agg', 'aggregate_id_invalid'),
    ('account_dir', '', 'account_dir_invalid'), ('account_dir', 'a\0b', 'account_dir_invalid'),
    ('egress_id', 'has space', 'egress_id_invalid')])
def test_account_spec_fields_are_validated(field, value, code):
    with pytest.raises(RuntimeConfigError, match=code):
        spec(**{field: value})


def test_accounts_must_be_one_to_four_and_distinct():
    with pytest.raises(RuntimeConfigError, match='accounts_invalid'):
        RuntimeConfig(environment=RuntimeEnvironment.TESTNET, accounts=())
    with pytest.raises(RuntimeConfigError, match='accounts_invalid'):
        RuntimeConfig(environment=RuntimeEnvironment.TESTNET, accounts=tuple(spec(i) for i in range(1, 6)))
    with pytest.raises(RuntimeConfigError, match='duplicate_account_id'):
        RuntimeConfig(environment=RuntimeEnvironment.TESTNET, accounts=(spec(1), spec(1, account_dir='/x')))
    with pytest.raises(RuntimeConfigError, match='duplicate_account_dir'):
        RuntimeConfig(environment=RuntimeEnvironment.TESTNET,
                      accounts=(spec(1, account_dir='C:\\Z\\a'), spec(2, account_dir='c:/z/A/')))
    with pytest.raises(RuntimeConfigError, match='accounts_invalid'):
        RuntimeConfig(environment=RuntimeEnvironment.TESTNET, accounts=[spec(1)])


@pytest.mark.parametrize('field, value', [
    ('max_snapshot_age_s', 0), ('max_snapshot_age_s', True), ('evidence_deadline_ms', 10),
    ('refresh_period_ms', 999), ('proof_max_age_ms', 999_999), ('stop_grace_ms', -1), ('entries_enabled', 1)])
def test_numeric_and_flag_bounds_are_exact(field, value):
    with pytest.raises(RuntimeConfigError):
        RuntimeConfig(environment=RuntimeEnvironment.TESTNET, accounts=(spec(),), **{field: value})


def test_proof_cannot_expire_before_a_refresh_can_finish():
    with pytest.raises(RuntimeConfigError, match='proof_max_age_ms_too_short'):
        RuntimeConfig(environment=RuntimeEnvironment.TESTNET, accounts=(spec(),), refresh_period_ms=5_000,
                      evidence_deadline_ms=10_000, proof_max_age_ms=14_999)


def test_environment_must_be_the_enum_not_a_string():
    with pytest.raises(RuntimeConfigError, match='environment_invalid'):
        RuntimeConfig(environment='testnet', accounts=(spec(),))


# ------------------------------------------------------------------------------------------------ CLI
def _write(tmp_path, payload):
    path = tmp_path / 'runtime.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return str(path)


def test_check_prints_a_read_only_boot_plan(tmp_path):
    out = io.StringIO()
    code = main(['check', '--config', _write(tmp_path, raw_config())], environ={}, out=out)
    assert code == EXIT_OK
    result = json.loads(out.getvalue())
    assert result['status'] == 'ok'
    plan = result['plan']
    assert plan['environment'] == 'testnet' and plan['entries_enabled'] is False
    assert plan['order_route'].startswith('none')
    assert plan['accounts'][0]['account_dir_exists'] is False


@pytest.mark.parametrize('variable', ['LIVE_CONFIRM', 'ZACKBOT_LIVE', 'ZACKBOT_MAINNET'])
def test_check_refuses_when_a_live_switch_is_in_the_environment(tmp_path, variable):
    out = io.StringIO()
    code = main(['check', '--config', _write(tmp_path, raw_config())], environ={variable: '1'}, out=out)
    assert code == EXIT_REFUSED
    assert json.loads(out.getvalue())['code'] == 'live_switch_present'


def test_check_refuses_mainnet_config_and_unreadable_files(tmp_path):
    out = io.StringIO()
    assert main(['check', '--config', _write(tmp_path, raw_config(environment='mainnet'))],
                environ={}, out=out) == EXIT_REFUSED
    assert json.loads(out.getvalue())['code'] == 'environment_forbidden'
    out = io.StringIO()
    assert main(['check', '--config', str(tmp_path / 'missing.json')], environ={}, out=out) == EXIT_REFUSED
    bad = tmp_path / 'bad.json'
    bad.write_text('{not json', encoding='utf-8')
    out = io.StringIO()
    assert main(['check', '--config', str(bad)], environ={}, out=out) == EXIT_REFUSED


def test_there_is_no_run_command_yet():
    out = io.StringIO()
    assert main(['run'], environ={}, out=out) == EXIT_USAGE
    assert main([], environ={}, out=out) == EXIT_USAGE


def test_cli_runs_as_a_module_in_a_clean_interpreter(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ('LIVE_CONFIRM', 'ZACKBOT_LIVE', 'ZACKBOT_MAINNET')}
    env['PYTHONIOENCODING'] = 'cp1252'
    out = subprocess.run([sys.executable, '-m', 'newcore.runtime', 'check', '--config',
                          _write(tmp_path, raw_config())], capture_output=True, text=True, cwd=ROOT, env=env,
                         timeout=120, encoding='utf-8')
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)['status'] == 'ok'


# ------------------------------------------------------------------------------------------------ boundary
def legacy_modules():
    return {os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(ROOT, '*.py'))}


FORBIDDEN_IMPORTS = {'socket', 'ssl', 'http', 'urllib', 'requests', 'subprocess', 'binance', 'binance_client',
                     'pickle', 'random', 'secrets'}


def test_runtime_package_imports_only_stdlib_and_newcore():
    legacy = legacy_modules()
    assert {'engine', 'backtest', 'app', 'binance_client'} <= legacy
    bad = []
    for path in sorted(glob.glob(os.path.join(PACKAGE, '*.py'))):
        tree = ast.parse(open(path, encoding='utf-8').read(), path)
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ''] if isinstance(node, ast.ImportFrom) and not node.level else [])
            for name in names:
                top = name.split('.')[0]
                if top in legacy or top in FORBIDDEN_IMPORTS:
                    bad.append(f'{os.path.basename(path)} imports {name}')
                if top == 'newcore' and name.startswith(('newcore.tnet02', 'newcore.venue.http_sender')):
                    bad.append(f'{os.path.basename(path)} imports {name}')
    assert bad == []


def test_no_legacy_module_imports_the_runtime():
    bad = []
    for name in sorted(legacy_modules()):
        text = open(os.path.join(ROOT, name + '.py'), encoding='utf-8', errors='replace').read()
        if 'newcore.runtime' in text or 'from newcore import runtime' in text:
            bad.append(name)
    assert bad == []


SMOKE = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
before = set(sys.modules)
import newcore.account_context, newcore.store, newcore.scheduler, newcore.reconciliation
import newcore.management, newcore.risk, newcore.observability
baseline = set(sys.modules)
import newcore.runtime, newcore.runtime.__main__
print(json.dumps({'all': sorted(set(sys.modules) - before), 'added': sorted(set(sys.modules) - baseline)}))
'''


def test_importing_the_runtime_loads_no_legacy_or_network_module():
    out = subprocess.run([sys.executable, '-I', '-c', SMOKE, ROOT], capture_output=True, text=True, timeout=120,
                         cwd=HERE, encoding='utf-8')
    assert out.returncode == 0, out.stderr
    rep = json.loads(out.stdout)
    tops = {m.split('.')[0] for m in rep['all']}
    assert not (tops & legacy_modules()), sorted(tops & legacy_modules())
    # NC-04 already pulls the NC-03 venue package (urllib.parse, http.client) at master; RT-01 adds no network module.
    added = {m.split('.')[0] for m in rep['added']}
    assert not (added & {'socket', 'ssl', 'http', 'urllib', 'requests'}), sorted(added)
    assert all(m.startswith('newcore.runtime') or m.split('.')[0] in {'argparse', 'gettext', 'locale'}
               for m in rep['added']), rep['added']
