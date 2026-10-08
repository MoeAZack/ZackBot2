"""tools/newcore_tnet.py --config: the TESTNET run config is READ only (never written); account, binding digest and
symbols come from it; the stored key's binding digest must match. Fake HTTP, DUMMY keys, no network."""
import importlib.util
import io
import json
import os

import pytest

from fake_binance import FakeBinance
from ncv_support import DUMMY_KEY, DUMMY_SECRET
from test_ncv_credential_store import XorProtector

from newcore.venue.credentials import CredentialStore, binding_digest
from newcore.venue.run_config import (FACTORY, RunConfigError, default_testnet_config_path, load_testnet_config,
                                      parse_testnet_config)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NOW = 1759917600000
ACCOUNT = 'acct_' + 'be' * 16
DIGEST = binding_digest(DUMMY_KEY)


def doc(**over):
    d = {'mode': 'TESTNET', 'venue': {'kind': 'testnet', 'factory': FACTORY},
         'strategy': {'enabled': False, 'symbols': ['BTCUSDT', 'ETHUSDT'], 'tf': '4h'},
         'account': {'id': ACCOUNT, 'key_digest': DIGEST, 'portfolio_id': 'pf_' + '36' * 16}}
    for k, v in over.items():
        if v is None:
            d.pop(k, None)
        elif isinstance(v, dict) and isinstance(d.get(k), dict):
            d[k] = {**d[k], **v}
        else:
            d[k] = v
    return d


def toml(d):
    lines = [f'mode = "{d["mode"]}"'] if 'mode' in d else []
    for sec in ('venue', 'strategy', 'account'):
        if sec in d:
            lines.append(f'[{sec}]')
            for k, v in d[sec].items():
                lines.append(f'{k} = {json.dumps(v)}')
    return '\n'.join(lines) + '\n'


def test_parse_the_owner_shape():
    c = parse_testnet_config(doc(), 'x.toml')
    assert (c.account_id, c.key_digest, c.symbols) == (ACCOUNT, DIGEST, ('BTCUSDT', 'ETHUSDT'))
    assert c.portfolio_id == 'pf_' + '36' * 16 and c.source == 'x.toml'


@pytest.mark.parametrize('over,why', [
    ({'mode': 'PAPER'}, 'TESTNET config is required'),
    ({'mode': None}, 'TESTNET config is required'),
    ({'venue': {'kind': 'fake'}}, 'venue.kind'),
    ({'venue': {'factory': 'pkg.mod:make'}}, 'venue.factory'),
    ({'account': {'id': None}}, 'account.id'),
    ({'account': {'id': 'acct_XYZ'}}, 'account.id'),
    ({'account': {'key_digest': None}}, 'key_digest'),
    ({'account': {'key_digest': '0123456789abcdef'}}, 'placeholder'),
    ({'account': {'key_digest': 'ABCDEF0123456789'}}, 'key_digest'),
    ({'account': {'portfolio_id': 'pf_1'}}, 'portfolio_id'),
    ({'strategy': {'symbols': []}}, 'strategy.symbols'),
    ({'strategy': {'symbols': ['BTCUSDT', 'BTCUSDT']}}, 'strategy.symbols'),
    ({'strategy': {'symbols': ['btc']}}, 'strategy.symbols'),
    ({'account': {'api_secret': 'x'}}, 'credential-like'),
    ({'venue': {'apiKey': 'x'}}, 'credential-like'),
    ({'venue': 'testnet'}, 'must be tables'),
])
def test_refusals(over, why):
    d = doc()
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(d.get(k), dict):
            for kk, vv in v.items():
                if vv is None:
                    d[k].pop(kk, None)
                else:
                    d[k][kk] = vv
        elif v is None:
            d.pop(k)
        else:
            d[k] = v
    with pytest.raises(RunConfigError, match=why):
        parse_testnet_config(d)


def test_not_a_table():
    with pytest.raises(RunConfigError, match='one table'):
        parse_testnet_config([1])


def test_load_toml_json_and_bad_files(tmp_path):
    t = tmp_path / 'testnet.toml'
    t.write_text(toml(doc()), encoding='utf-8')
    j = tmp_path / 'testnet.json'
    j.write_text(json.dumps(doc()), encoding='utf-8')
    assert load_testnet_config(str(t)) == load_testnet_config(str(t))
    assert load_testnet_config(str(j)).account_id == ACCOUNT
    bad = tmp_path / 'bad.toml'
    bad.write_text('mode = ', encoding='utf-8')
    with pytest.raises(RunConfigError, match='not valid'):
        load_testnet_config(str(bad))
    with pytest.raises(RunConfigError, match='cannot read'):
        load_testnet_config(str(tmp_path / 'none.toml'))


def test_default_path():
    assert default_testnet_config_path({'LOCALAPPDATA': r'C:\L'}).replace('\\', '/') == \
        'C:/L/ZackBotNC/config/testnet.toml'


# ---------------------------------------------------------------------------------------------- the CLI
def tool():
    spec = importlib.util.spec_from_file_location('ncv_tnet_cli_cfg', os.path.join(REPO, 'tools', 'newcore_tnet.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git_clean(args):
    from types import SimpleNamespace
    return SimpleNamespace(returncode=0, stdout=('a' * 40) if args[0] == 'rev-parse' else '')


class Clock:
    t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture
def env(tmp_path):
    root = str(tmp_path / 'secrets')
    CredentialStore(ACCOUNT, root=root, protector=XorProtector(), harden_acl=False).save(
        'testnet', DUMMY_KEY, DUMMY_SECRET, now_ms=NOW)
    cfg = tmp_path / 'cfg' / 'testnet.toml'
    cfg.parent.mkdir()
    cfg.write_text(toml(doc(strategy={'symbols': ['BTCUSDT', 'SOLUSDT']})), encoding='utf-8')
    return dict(root=root, cfg=cfg, cassettes=str(tmp_path / 'cassettes'), reports=str(tmp_path / 'reports'))


def run(env, extra, http=None, cfg=True):
    out = io.StringIO()
    c = Clock()
    base = ['--root', env['root'], '--cassette-dir', env['cassettes'], '--report-dir', env['reports']]
    if cfg:
        base += ['--config', str(env['cfg'])]
    rc = tool().main([*base, *extra], http=http, local_clock=lambda: NOW, protector=XorProtector(), out=out,
                     monotonic=c.monotonic, sleep=c.sleep, git_run=git_clean)
    return rc, out.getvalue()


def test_config_supplies_account_and_probe_symbol_and_is_never_written(env):
    before = (env['cfg'].read_bytes(), os.stat(env['cfg']).st_mtime_ns)
    fb = FakeBinance()
    rc, out = run(env, ['--probe', 'P1'], http=fb)
    assert rc == 0, out
    assert f'account {ACCOUNT}, binding {DIGEST}, symbols BTCUSDT, SOLUSDT' in out
    posts = [q for q in fb.requests if q.method == 'POST']
    assert posts and all('symbol=BTCUSDT' in q.query for q in posts)                    # the first config symbol
    assert (env['cfg'].read_bytes(), os.stat(env['cfg']).st_mtime_ns) == before
    assert os.listdir(env['cfg'].parent) == ['testnet.toml']


def test_dry_run_with_config_reads_no_key(env):
    def no_http(r):
        raise AssertionError('network')
    rc, out = run(env, ['--probe', 'P1', '--dry-run'], http=no_http)
    assert rc == 0 and 'P1 client-id reuse on BTCUSDT' in out and f'account {ACCOUNT}' in out


def test_binding_mismatch_exits_3_before_any_request(env, tmp_path):
    env['cfg'].write_text(toml(doc(account={'key_digest': 'f' * 16})), encoding='utf-8')
    fb = FakeBinance()
    rc, out = run(env, ['--probe', 'P1'], http=fb)
    assert rc == 3 and 'BINDING MISMATCH' in out and fb.requests == []


def test_flags_must_agree_with_the_config(env):
    rc, out = run(env, ['--probe', 'P1', '--account-id', 'acct_' + '0' * 32, '--dry-run'])
    assert rc == 2 and 'disagrees with the config' in out
    rc, out = run(env, ['--probe', 'P1', '--symbol', 'ETHUSDT', '--dry-run'])
    assert rc == 2 and 'not in the config symbols' in out
    rc, out = run(env, ['--probe', 'P1', '--symbol', 'SOLUSDT', '--dry-run'])
    assert rc == 0 and 'P1 client-id reuse on SOLUSDT' in out


def test_a_bad_config_is_usage(env):
    env['cfg'].write_text('mode = "PAPER"\n', encoding='utf-8')
    rc, out = run(env, ['--probe', 'P1', '--dry-run'])
    assert rc == 2 and 'TESTNET config is required' in out


def test_default_config_path_is_used_without_account_id(env, monkeypatch, tmp_path):
    lad = tmp_path / 'lad'
    (lad / 'ZackBotNC' / 'config').mkdir(parents=True)
    (lad / 'ZackBotNC' / 'config' / 'testnet.toml').write_text(env['cfg'].read_text(encoding='utf-8'),
                                                                encoding='utf-8')
    monkeypatch.setenv('LOCALAPPDATA', str(lad))
    rc, out = run(env, ['--probe', 'P1', '--dry-run'], cfg=False)
    assert rc == 0 and f'account {ACCOUNT}' in out and 'testnet.toml' in out
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'empty'))
    rc, out = run(env, ['--probe', 'P1', '--dry-run'], cfg=False)
    assert rc == 2 and 'give --config' in out
