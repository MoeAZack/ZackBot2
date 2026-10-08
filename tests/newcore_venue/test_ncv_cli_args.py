"""Cowork round 2, item 4: the owner tools' argv guard. --account-id is a dashed UUID (exempt from the key heuristic);
anything else there is refused with an account-id message; keys are still refused everywhere."""
import importlib.util
import io
import os

import pytest

from newcore.venue.cli_args import ACCOUNT_REFUSAL, KEY_REFUSAL, argv_refusal, is_account_uuid

from ncv_support import DUMMY_KEY

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UUID = '8c1d2e3f-4a5b-4c6d-8e7f-90a1b2c3d4e5'


def tool(name):
    spec = importlib.util.spec_from_file_location(f'ncv_{name}_cli', os.path.join(REPO, 'tools', f'{name}.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize('value', [UUID, UUID.upper(), '00000000-0000-0000-0000-000000000000'])
def test_dashed_uuid_account_ids_pass(value):
    assert is_account_uuid(value)
    assert argv_refusal(['status', '--account-id', value]) is None
    assert argv_refusal(['status', f'--account-id={value}']) is None


@pytest.mark.parametrize('value', ['abcdefghijklmnopqrstuvwxyz0123', 'A' * 24, 'nc-acct-1', DUMMY_KEY,
                                   UUID.replace('-', ''), UUID + 'x', ' ' + UUID, UUID + '\n', ''])
def test_non_uuid_account_ids_get_the_account_message(value):
    assert argv_refusal(['status', '--account-id', value]) == ACCOUNT_REFUSAL
    assert argv_refusal(['status', f'--account-id={value}']) == ACCOUNT_REFUSAL


@pytest.mark.parametrize('argv', [['status', '--account-id', UUID, '--api-key', 'x'],
                                  ['status', '--account-id', UUID, DUMMY_KEY],
                                  ['status', '--account-id', UUID, '--Secret=abc'],
                                  ['status', '--account-id', UUID, '--root', 'C:\\x', 'abcdefghijklmnopqrstuvwxyz0123']])
def test_keys_still_refused_next_to_a_valid_account_id(argv):
    assert argv_refusal(argv) == KEY_REFUSAL


def test_paths_are_not_mistaken_for_keys():
    assert argv_refusal(['status', '--account-id', UUID, '--root',
                         r'C:\Users\x\AppData\Local\Temp\pytest-of-x\abcdefghijklmnopqrstuvwxyz0123']) is None


@pytest.mark.parametrize('name', ['newcore_smoke', 'newcore_keys'])
def test_tools_refuse_a_long_alnum_account_id_with_the_account_message(name, tmp_path):
    argv = (['--account-id', 'abcdefghijklmnopqrstuvwxyz0123'] if name == 'newcore_smoke'
            else ['status', '--account-id', 'abcdefghijklmnopqrstuvwxyz0123'])
    out = io.StringIO()
    rc = tool(name).main(argv + ['--root', str(tmp_path / 's')], out=out)
    assert rc == 2 and 'dashed UUID' in out.getvalue() and 'abcdefghijklmnopqrstuvwxyz0123' not in out.getvalue()


@pytest.mark.parametrize('name', ['newcore_smoke', 'newcore_keys'])
def test_tools_accept_a_uuid_account_id(name, tmp_path):
    argv = ['--account-id', UUID] if name == 'newcore_smoke' else ['status', '--account-id', UUID]
    out = io.StringIO()
    rc = tool(name).main(argv + ['--root', str(tmp_path / 's')], out=out)
    assert rc in (3, 4) and 'REFUSED' not in out.getvalue()     # gets past the guard; no stored key -> typed exit
