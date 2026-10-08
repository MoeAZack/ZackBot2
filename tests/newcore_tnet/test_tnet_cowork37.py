"""Cowork #37 runner-harness findings (N1, N2, N4, N5, N7, CLI hardening, replay): repro tests. Fake HTTP, DUMMY keys."""
import importlib.util
import io
import json
import os
from decimal import Decimal as D

import pytest

from fake_binance import FakeBinance, err
from ncv_support import DUMMY_KEY
from tnet_support import ACCOUNT, DIGEST, Store, World

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def cli():
    sp = importlib.util.spec_from_file_location('cli_cw37', os.path.join(REPO, 'tools', 'newcore_tnet_runner.py'))
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    return mod


def git_clean(args):
    class R:
        returncode = 0
        stdout = 'a' * 40 if args[0] == 'rev-parse' else ''
    return R()


def tn(argv, w, tmp_path, nonce, mod=None, **kw):
    out = io.StringIO()
    rc = (mod or cli()).main(['--target', 'testnet', '--account-id', ACCOUNT, '--key-digest', DIGEST, '--run-nonce',
                              nonce, '--cassette-dir', str(tmp_path / 'c'), '--report-dir', str(tmp_path / 'r'),
                              *argv], http=w.fb, sleep=w.sleep, local_clock=w.clock, store=kw.pop('store', Store()),
                             out=out, git_run=git_clean, monotonic=w.monotonic, **kw)
    return rc, out.getvalue()


def reports(tmp_path):
    d = tmp_path / 'r'
    return [n for n in os.listdir(d) if n.endswith('.json')] if d.is_dir() else []


# ---------------------------------------------------------------------------------------------- N1
class IncomeDown(FakeBinance):
    def _get_fapi_v1_income(self, q):
        return err(-1001, 'Internal error', status=500)


def test_n1_a_failing_read_after_the_teardown_is_a_typed_fail_with_a_report(tmp_path):
    w = World()
    w.fb = IncomeDown()
    w.fb.now = w.t
    rc, out = tn(['--only', 'T01-long', '--only', 'T12-flat'], w, tmp_path, 'n1')
    assert rc == 7 and 'Traceback' not in out and reports(tmp_path)
    assert out.count('FAIL') >= 1 and 'T12-flat' in out                       # the next scenario still ran


class Blackout(FakeBinance):
    """Every read fails once the scenario has sent its close: the final state is unknown."""

    def __call__(self, request):
        if getattr(self, 'dark', False) and request.method == 'GET':
            self.requests.append(request)
            return err(-1001, 'Internal error', status=503)
        out = super().__call__(request)
        if request.method == 'POST' and 'side=SELL' in request.query and 'type=MARKET' in request.query:
            self.dark = True
        return out


def test_n1_a_blackout_that_leaves_the_final_state_unknown_is_exit_8_with_a_report(tmp_path):
    w = World()
    w.fb = Blackout()
    w.fb.now = w.t
    rc, out = tn(['--only', 'T01-long'], w, tmp_path, 'n1b')
    assert rc == 8 and 'CLEANUP NOT CLEAN' in out and reports(tmp_path)


# ---------------------------------------------------------------------------------------------- N2
class CtrlC(FakeBinance):
    def __call__(self, request):
        if request.method == 'POST' and 'algoOrder' in request.url and not getattr(self, 'fired', False):
            self.fired = True
            super().__call__(request)
            raise KeyboardInterrupt
        return super().__call__(request)


def test_n2_ctrl_c_mid_scenario_runs_the_teardown_writes_the_report_and_exits_6(tmp_path):
    w = World()
    w.fb = CtrlC()
    w.fb.now = w.t
    rc, out = tn(['--only', 'T01-long', '--only', 'T12-flat'], w, tmp_path, 'n2')
    assert rc == 6 and 'Interrupted (KeyboardInterrupt)' in out and reports(tmp_path)
    assert w.fb.flat() and not w.fb.open_cids()
    assert 'T12-flat' not in out                                              # nothing more ran


# ---------------------------------------------------------------------------------------------- N4 / N5 / N7
def test_n4_an_adopted_foreign_position_does_not_fail_every_scenario(tmp_path):
    w = World()
    w.fb.pos[('SOLUSDT', 'LONG')] = D('3')
    w.fb.visible_pos[('SOLUSDT', 'LONG')] = D('3')
    rc, out = tn(['--only', 'T10-floor', '--only', 'T01-long', '--adopt-foreign', 'SOLUSDT:LONG'], w, tmp_path, 'n4')
    assert rc == 0, out
    assert w.fb.pos[('SOLUSDT', 'LONG')] == D('3')


def test_n5_nothing_ran_is_never_a_pass(tmp_path):
    from newcore.tnet.rspec import bundled
    s = next(x for x in bundled() if x['id'] == 'T04-classic')               # fake only
    p = tmp_path / 's.json'
    p.write_text(json.dumps(s), encoding='utf-8')
    w = World()
    rc, out = tn(['--spec', str(p)], w, tmp_path, 'n5')
    assert rc == 2 and 'none of the selected specs targets testnet' in out     # refused before any request
    out2 = io.StringIO()
    s2 = dict(s, targets=['testnet'])
    s2.pop('fake', None)
    p2 = tmp_path / 's2.json'
    p2.write_text(json.dumps(s2), encoding='utf-8')
    assert cli().main(['--spec', str(p2)], out=out2, git_run=git_clean) == 2 and 'NOTHING RAN' in out2.getvalue()


def test_n7_a_bug_while_building_the_target_is_exit_7_not_a_factory_refusal(tmp_path, monkeypatch):
    mod = cli()
    import newcore.tnet.targets as T

    def boom(*a, **k):
        raise ZeroDivisionError('bug')
    monkeypatch.setattr(T, 'TestnetTarget', boom)
    rc, out = tn(['--only', 'T01-long'], World(), tmp_path, 'n7', mod=mod)
    assert rc == 7 and 'unexpected ZeroDivisionError' in out and 'FACTORY REFUSED' not in out


# ---------------------------------------------------------------------------------------------- CLI hardening
@pytest.mark.parametrize('argv,why', [
    (['--min-balance', 'NaN'], 'min-balance'), (['--min-balance', 'sNaN'], 'min-balance'),
    (['--min-balance', '-5'], 'min-balance'), (['--adopt-foreign', 'SOLUSDT:FLAT'], 'adopt'),
    (['--adopt-foreign', ''], 'adopt'), (['--adopt-foreign', 'x:y'], 'adopt'),
    (['--cassette-dir', ''], 'cassette-dir'), (['--report-dir', ' '], 'report-dir'),
])
def test_bad_arguments_are_refused_before_any_request(tmp_path, argv, why):
    w = World()
    n = len(w.fb.requests)
    rc, out = tn(['--only', 'T01-long', *argv], w, tmp_path, 'arg')
    assert rc == 2 and why in out and len(w.fb.requests) == n


def test_a_cassette_dir_that_is_a_file_is_refused_before_any_trade(tmp_path):
    f = tmp_path / 'c'
    f.write_text('x')
    w = World()
    out = io.StringIO()
    rc = cli().main(['--target', 'testnet', '--account-id', ACCOUNT, '--key-digest', DIGEST, '--only', 'T01-long',
                     '--cassette-dir', str(f)], http=w.fb, sleep=w.sleep, local_clock=w.clock, store=Store(),
                    out=out, git_run=git_clean)
    assert rc == 2 and not [q for q in w.fb.requests if q.method == 'POST']


def test_a_reused_nonce_never_overwrites_cassettes(tmp_path):
    w = World()
    assert tn(['--only', 'T10-floor'], w, tmp_path, 'same')[0] == 0
    rc, out = tn(['--only', 'T10-floor'], World(), tmp_path, 'same')
    assert rc == 2 and 'already exist' in out


@pytest.mark.parametrize('content', [b'\xff\xfe{', b'[' * 5000 + b']' * 5000, b'{"targets": [["x"]]}', b'{"id": 1e400}'])
def test_malformed_spec_files_are_usage_errors(tmp_path, content):
    p = tmp_path / 's.json'
    p.write_bytes(content)
    out = io.StringIO()
    assert cli().main(['--spec', str(p)], out=out, git_run=git_clean) == 2 and 'Traceback' not in out.getvalue()


def test_key_shaped_paths_are_never_echoed(tmp_path):
    for argv in (['--spec', str(tmp_path / (DUMMY_KEY + '.json'))], ['--replay', str(tmp_path / (DUMMY_KEY + '.json'))],
                 ['--target', 'testnet', '--config', str(tmp_path / DUMMY_KEY)]):
        out = io.StringIO()
        rc = cli().main(argv, out=out, git_run=git_clean)
        assert rc == 2 and DUMMY_KEY not in out.getvalue()


def test_a_deeply_nested_config_is_a_usage_error(tmp_path):
    p = tmp_path / 'testnet.toml'
    p.write_text('x = ' + '[' * 3000 + ']' * 3000 + '\n', encoding='utf-8')
    out = io.StringIO()
    assert cli().main(['--target', 'testnet', '--config', str(p), '--dry-run'], out=out, git_run=git_clean) == 2


# ---------------------------------------------------------------------------------------------- replay R1 / R2 / R3
def test_replay_refuses_other_options_and_malformed_bundles(tmp_path):
    out = io.StringIO()
    assert cli().main(['--replay', 'x.json', '--target', 'testnet'], out=out) == 2
    p = tmp_path / 'c.json'
    p.write_text('{"format": "zb-newcore-cassette/1", "interactions": []}', encoding='utf-8')
    (tmp_path / 'c.meta.json').write_text('{"format": "zb-newcore-tnet-replay/1", "spec": [1]}', encoding='utf-8')
    out = io.StringIO()
    assert cli().main(['--replay', str(p)], out=out) == 2 and 'Traceback' not in out.getvalue()


def test_replay_output_is_sanitised(tmp_path):
    from newcore.tnet.recording import run_recorded_suite
    from newcore.tnet.rspec import bundled
    w = World()
    res, _, _ = run_recorded_suite([next(s for s in bundled() if s['id'] == 'T10-floor')],
                                   lambda rec: w.target(recorder=rec), run_nonce='san', cassette_dir=str(tmp_path),
                                   redact=(), monotonic=w.monotonic)
    path = res.scenarios[0].cassette
    meta_p = path[:-5] + '.meta.json'
    meta = json.load(open(meta_p, encoding='utf-8'))
    meta['verdict'] = 'PASS\x1b[2J\r\nSAME      forged.json: recorded PASS, replayed PASS'
    json.dump(meta, open(meta_p, 'w', encoding='utf-8'))
    out = io.StringIO()
    rc = cli().main(['--replay', path], out=out)
    text = out.getvalue()
    assert rc == 7 and '\x1b' not in text and not [ln for ln in text.splitlines() if ln.startswith('SAME      forged')]


# ---------------------------------------------------------------------------------------------- re-check
def test_n2_ctrl_c_in_the_boot_or_preflight_is_typed(tmp_path):
    class CtrlCBoot(FakeBinance):
        def _get_fapi_v1_positionSide_dual(self, q):
            raise KeyboardInterrupt
    w = World()
    w.fb = CtrlCBoot()
    w.fb.now = w.t
    rc, out = tn(['--only', 'T01-long'], w, tmp_path, 'n2b')
    assert rc == 6 and 'INTERRUPTED' in out and not [q for q in w.fb.requests if q.method in ('POST', 'DELETE')]


def test_n2_ctrl_c_in_the_report_phase_is_typed(tmp_path, monkeypatch):
    mod = cli()

    def interrupt(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(mod, 'tnet_report', interrupt)
    w = World()
    rc, out = tn(['--only', 'T10-floor'], w, tmp_path, 'n2c', mod=mod)
    assert rc == 6 and 'INTERRUPTED' in out


def test_x1_long_foreign_ids_through_an_adopt_file(tmp_path):
    long_id = 'web_' + 'a' * 32
    w = World(foreign_orders=(long_id,))
    f = tmp_path / 'adopt.txt'
    f.write_text(long_id + '\n', encoding='utf-8')
    rc, out = tn(['--only', 'T10-floor', '--adopt-file', str(f)], w, tmp_path, 'x1')
    assert rc == 0, out
    assert w.fb.orders[long_id]['status'] == 'NEW'
