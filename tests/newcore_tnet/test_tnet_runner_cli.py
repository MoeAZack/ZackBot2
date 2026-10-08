"""tools/newcore_tnet_runner.py: fake target by default (no network), testnet target only with an injected fake http in
tests; usage refusals, dry run, exit codes, leak-audited report."""
import importlib.util
import io
import json
import os

import pytest

from ncv_support import DUMMY_KEY, DUMMY_SECRET
from tnet_support import ACCOUNT, DIGEST, Store, World

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_spec = importlib.util.spec_from_file_location('newcore_tnet_runner', os.path.join(REPO, 'tools',
                                                                                     'newcore_tnet_runner.py'))
CLI = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CLI)


def clean_git(args):
    class R:
        returncode = 0
        stdout = 'a' * 40 if args[0] == 'rev-parse' else ''
    return R()


def dirty_git(args):
    class R:
        returncode = 0
        stdout = 'a' * 40 if args[0] == 'rev-parse' else ' M x.py'
    return R()


def main(argv, **kw):
    out = io.StringIO()
    kw.setdefault('git_run', clean_git)
    rc = CLI.main(argv, out=out, **kw)
    return rc, out.getvalue()


def no_network(*a, **k):
    raise AssertionError('no network in tests')


def test_fake_default_runs_every_bundled_spec_and_passes():
    rc, text = main(['--run-nonce', 'cli1'])
    assert rc == 0, text
    assert text.count('PASS ') == 12 and 'summary:' in text and 'report:' not in text


def test_only_and_verbose():
    rc, text = main(['--only', 'T09', '-v'])
    assert rc == 0 and 'T09' in text and 'T01' not in text and '[x] entry phases' in text


def test_unknown_only_id_is_usage():
    assert main(['--only', 'T99'])[0] == 2


def test_bad_nonce_is_usage():
    assert main(['--run-nonce', 'Bad Nonce'])[0] == 2


def test_bad_spec_file_is_usage(tmp_path):
    p = tmp_path / 's.json'
    p.write_text('{"format": 1}')
    rc, text = main(['--spec', str(p)])
    assert rc == 2 and 'SpecError' in text


def test_a_failing_spec_exits_7(tmp_path):
    from newcore.tnet.rspec import bundled
    s = next(x for x in bundled() if x['id'] == 'T01-long')
    s['expect']['entries'] = 2
    p = tmp_path / 's.json'
    p.write_text(json.dumps(s))
    rc, text = main(['--spec', str(p)])
    assert rc == 7 and 'FAIL' in text and '[ ] entries == 2: 1' in text


def test_fake_report_only_when_asked(tmp_path):
    rc, text = main(['--only', 'T01-long', '--report-dir', str(tmp_path)])
    assert rc == 0 and 'report:' in text
    files = sorted(os.listdir(tmp_path))
    assert len(files) == 3 and any(f.endswith('.scenarios.json') for f in files)
    doc = json.loads(open(os.path.join(tmp_path, next(f for f in files if f.endswith('0.json') or
                                                         (f.endswith('.json') and 'scenarios' not in f)))).read())
    assert doc['passed'] and doc['scenarios'][0]['name'].startswith('T01-long')


@pytest.mark.parametrize('extra', [[], ['--account-id', ACCOUNT], ['--account-id', ACCOUNT, '--key-digest', 'XYZ']])
def test_testnet_needs_account_and_digest(extra):
    rc, text = main(['--target', 'testnet', *extra])
    assert rc == 2 and 'needs --config, or --account-id and --key-digest' in text


def test_testnet_dry_run_reads_no_key_and_sends_nothing():
    rc, text = main(['--target', 'testnet', '--account-id', ACCOUNT, '--key-digest', DIGEST, '--dry-run'],
                    http=no_network, store=Store())
    assert rc == 0 and 'PLAN' in text and 'T04-classic' in text and 'SKIPPED (not a testnet scenario)' in text


def test_gate_refuses_a_dirty_tree():
    assert main(['--gate'], git_run=dirty_git)[0] == 2


def test_keys_on_the_command_line_are_refused():
    rc, text = main(['--api-key', 'x'])
    assert rc == 2 and 'REFUSED' in text


def _testnet(argv, w, tmp_path, **kw):
    return main(['--target', 'testnet', '--account-id', ACCOUNT, '--key-digest', DIGEST, '--report-dir',
                 str(tmp_path), '--run-nonce', 'tcli', *argv], http=w.fb, sleep=w.sleep, local_clock=w.clock,
                store=kw.pop('store', Store()), **kw)


def test_testnet_over_fake_http_reports_and_leaks_nothing(tmp_path):
    w = World()
    rc, text = _testnet(['--only', 'T01-long', '--only', 'T09'], w, tmp_path)
    assert rc == 0, text
    assert 'report:' in text and w.fb.flat()
    blob = ''.join(open(os.path.join(tmp_path, f), encoding='utf-8').read() for f in os.listdir(tmp_path))
    assert DUMMY_KEY not in blob and DUMMY_SECRET not in blob and 'zbn1o-' in blob


def test_testnet_bracket_inconclusive_exits_1(tmp_path):
    rc, text = _testnet(['--only', 'T04-algo'], World(), tmp_path)
    assert rc == 1 and 'INCONCLUSIVE' in text


def test_testnet_preflight_refusal_exits_4(tmp_path):
    rc, text = _testnet(['--only', 'T01-long'], World(foreign_orders=('web_x',)), tmp_path)
    assert rc == 4 and 'foreign_order:SOLUSDT:web_x' in text


def test_testnet_binding_mismatch_exits_3(tmp_path):
    w = World()
    rc, text = main(['--target', 'testnet', '--account-id', ACCOUNT, '--key-digest', 'f' * 16, '--only', 'T01-long'],
                    http=w.fb, sleep=w.sleep, local_clock=w.clock, store=Store(), git_run=clean_git)
    assert rc == 3 and 'BINDING MISMATCH' in text


def test_testnet_missing_key_exits_3(tmp_path):
    from newcore.venue.credentials import CredentialsUnavailable

    class NoKey:
        def load(self, scrubber=None):
            raise CredentialsUnavailable('no key', 'missing')
    rc, text = _testnet(['--only', 'T01-long'], World(), tmp_path, store=NoKey())
    assert rc == 3 and 'NO USABLE TESTNET KEY' in text


def test_testnet_one_way_mode_is_a_factory_refusal_exit_4(tmp_path):
    rc, text = _testnet(['--only', 'T01-long'], World(dual=False), tmp_path)
    assert rc == 4 and 'FACTORY REFUSED' in text


def test_testnet_only_fake_specs_is_usage(tmp_path):
    rc, text = _testnet(['--only', 'T04-classic'], World(), tmp_path)
    assert rc == 2 and 'none of the selected specs targets testnet' in text


def test_a_report_leak_exits_5(tmp_path, monkeypatch):
    import newcore.venue.tnet as T

    def leak(*a, **k):
        raise T.ReportLeak('synthetic')
    monkeypatch.setattr(CLI, 'tnet_report', leak)
    rc, text = _testnet(['--only', 'T01-long'], World(), tmp_path)
    assert rc == 5 and 'report not written' in text


# ------------------------------------------------------------------------------------------------ --config (read only)
def write_config(tmp_path, symbols=('BTCUSDT', 'SOLUSDT'), mode='TESTNET', factory='newcore.venue.factory:build_testnet',
                 account=ACCOUNT, digest=DIGEST):
    p = tmp_path / 'testnet.toml'
    syms = ', '.join(f'"{s}"' for s in symbols)
    p.write_text(f'mode = "{mode}"\n\n[venue]\nkind = "testnet"\nfactory = "{factory}"\n\n'
                 f'[strategy]\nenabled = false\nsymbols = [{syms}]\ntf = "4h"\n\n'
                 f'[account]\nid = "{account}"\nportfolio_id = "pf_{"36" * 16}"\nkey_digest = "{digest}"\n',
                 encoding='utf-8')
    return p


def _with_config(argv, w, cfg, tmp_path, **kw):
    return main(['--target', 'testnet', '--config', str(cfg), '--report-dir', str(tmp_path / 'r'), '--run-nonce',
                 'ccli', *argv], http=w.fb, sleep=w.sleep, local_clock=w.clock, store=Store(), **kw)


def test_config_supplies_account_digest_and_symbols_and_is_never_written(tmp_path):
    cfg = write_config(tmp_path)
    before = (cfg.read_bytes(), os.stat(cfg).st_mtime_ns)
    w = World()
    rc, text = _with_config(['--only', 'T01-long'], w, cfg, tmp_path)
    assert rc == 0, text
    assert f'account {ACCOUNT}, binding {DIGEST}, symbols BTCUSDT, SOLUSDT' in text
    assert 'symbol ->' not in text                                   # SOLUSDT is a config symbol: unchanged
    assert (cfg.read_bytes(), os.stat(cfg).st_mtime_ns) == before
    assert sorted(os.listdir(tmp_path)) == ['r', 'testnet.toml']    # nothing written next to the config


def test_config_moves_specs_to_its_first_symbol(tmp_path):
    cfg = write_config(tmp_path, symbols=('BTCUSDT',))
    w = World()
    rc, text = _with_config(['--only', 'T01-long', '--only', 'T10-floor'], w, cfg, tmp_path)
    assert rc == 0, text
    assert 'T01-long: symbol -> BTCUSDT' in text and 'T10-floor: symbol -> BTCUSDT' in text
    orders = [q for q in w.fb.requests if q.method == 'POST' and q.url.endswith('/fapi/v1/order')]
    assert orders and all('symbol=BTCUSDT' in q.query for q in orders)


def test_symbol_flag_must_be_a_config_symbol(tmp_path):
    cfg = write_config(tmp_path)
    rc, text = _with_config(['--symbol', 'ETHUSDT', '--dry-run'], World(), cfg, tmp_path)
    assert rc == 2 and 'not in the config symbols' in text
    rc, text = _with_config(['--symbol', 'BTCUSDT', '--dry-run'], World(), cfg, tmp_path)
    assert rc == 0 and 'T01-long: symbol -> BTCUSDT' in text and 'config ' in text


def test_symbol_flag_retargets_the_fake_target_too():
    rc, text = main(['--only', 'T01-long', '--symbol', 'BTCUSDT'])
    assert rc == 0 and 'T01-long: symbol -> BTCUSDT' in text


@pytest.mark.parametrize('kw,why', [({'mode': 'PAPER'}, 'is not a TESTNET config'),
                                    ({'factory': 'pkg.mod:make'}, 'is not a TESTNET config')])
def test_a_non_testnet_config_is_refused(tmp_path, kw, why):
    if kw.get('mode') == 'PAPER':
        p = tmp_path / 'testnet.toml'
        p.write_text('mode = "PAPER"\n', encoding='utf-8')
    else:
        p = write_config(tmp_path, **kw)
    rc, text = _with_config(['--dry-run'], World(), p, tmp_path)
    assert rc == 2 and why in text


def test_a_broken_or_missing_config_is_usage(tmp_path):
    p = tmp_path / 'testnet.toml'
    p.write_text('mode = ', encoding='utf-8')
    assert _with_config(['--dry-run'], World(), p, tmp_path)[0] == 2
    assert _with_config(['--dry-run'], World(), tmp_path / 'none.toml', tmp_path)[0] == 2


def test_flags_that_disagree_with_the_config_are_refused(tmp_path):
    cfg = write_config(tmp_path)
    rc, text = _with_config(['--key-digest', 'f' * 16, '--dry-run'], World(), cfg, tmp_path)
    assert rc == 2 and 'disagrees with the config' in text
    rc, text = _with_config(['--account-id', ACCOUNT, '--key-digest', DIGEST, '--dry-run'], World(), cfg, tmp_path)
    assert rc == 0


def test_config_is_testnet_only():
    rc, text = main(['--config', 'x.toml'])
    assert rc == 2 and '--config is for --target testnet' in text


def test_testnet_defaults_to_the_standard_config_path(tmp_path, monkeypatch):
    base = tmp_path / 'lad'
    (base / 'ZackBotNC' / 'config').mkdir(parents=True)
    write_config(base / 'ZackBotNC' / 'config')
    monkeypatch.setenv('LOCALAPPDATA', str(base))
    rc, text = main(['--target', 'testnet', '--dry-run'], http=no_network, store=Store())
    assert rc == 0 and f'binding {DIGEST}' in text and 'testnet.toml' in text
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'empty'))
    rc, text = main(['--target', 'testnet', '--dry-run'], http=no_network, store=Store())
    assert rc == 2 and 'needs --config' in text


def test_residue_exits_8_and_lists_it(tmp_path, monkeypatch):
    from newcore.tnet.targets import FakeTarget
    from newcore.venue.tnet import CleanupResult
    dirty = CleanupResult(clean=False, attempts=3, remaining_positions=[('SOLUSDT', 'LONG', '2.5')])
    monkeypatch.setattr(FakeTarget, 'cleanup', lambda self, *a, **k: dirty)
    rc, text = main(['--only', 'T12-protected'])
    assert rc == 8 and "T12-protected: ['SOLUSDT', 'LONG', '2.5']" in text
