"""Runner-target cassettes: every testnet interaction of a scenario is recorded (sanitized, leak-audited) with a replay
sidecar, and --replay re-runs the scenario OFFLINE through the Runner against the recorded answers: it must reach the
recorded verdict. A changed, missing or extra interaction is DIFFERENT, never silently SAME."""
import copy
import importlib.util
import io
import json
import os

import pytest

from ncv_support import DUMMY_KEY, DUMMY_SECRET
from tnet_support import ACCOUNT, DIGEST, Store, World

from newcore.tnet.driver import FAIL, PASS
from newcore.tnet.recording import REPLAY_FORMAT, run_recorded_suite
from newcore.tnet.replay import ReplayError, load_bundle, replay
from newcore.tnet.rspec import bundled

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TESTNET = [s for s in bundled() if 'testnet' in s['targets']]


def record(tmp, specs=None, **kw):
    w = World(**kw)
    res, pre, errs = run_recorded_suite(specs or bundled(), lambda rec: w.target(recorder=rec), run_nonce='rp1',
                                        cassette_dir=str(tmp), redact=(DUMMY_KEY, DUMMY_SECRET), monotonic=w.monotonic)
    return w, res, pre, errs


@pytest.fixture(scope='module')
def recorded(tmp_path_factory):
    d = tmp_path_factory.mktemp('cassettes')
    w, res, pre, errs = record(d)
    return d, res, pre, errs


def test_every_testnet_scenario_has_a_clean_cassette_and_sidecar(recorded):
    d, res, pre, errs = recorded
    assert errs == [] and pre and os.path.isfile(pre)
    by = {r.id: r for r in res.scenarios}
    for s in TESTNET:
        r = by[s['id']]
        assert r.cassette and os.path.basename(r.cassette) == f'tnet-rp1-{s["id"].lower()}.json'
        text = open(r.cassette, encoding='utf-8').read() + open(r.cassette[:-5] + '.meta.json', encoding='utf-8').read()
        assert DUMMY_KEY not in text and DUMMY_SECRET not in text and '<redacted>' in text
        meta = json.load(open(r.cassette[:-5] + '.meta.json', encoding='utf-8'))
        assert meta['format'] == REPLAY_FORMAT and meta['verdict'] == r.verdict and meta['spec'] == s
        assert meta['cycle_times'] == r.cycle_times and meta['run_nonce'] == 'rp1'
    assert not [r for r in res.scenarios if r.verdict == 'SKIPPED' and r.cassette]


@pytest.mark.parametrize('sid', [s['id'] for s in TESTNET])
def test_replay_reaches_the_recorded_verdict(recorded, sid):
    d, res, _, _ = recorded
    r = next(x for x in res.scenarios if x.id == sid)
    rr = replay(r.cassette)
    assert rr.same and rr.remaining == 0 and rr.replayed.verdict == r.verdict
    assert [x['client_id'] for x in rr.replayed.ledger] == [x['client_id'] for x in r.ledger]
    assert rr.replayed.final_truth.get('outcome') == r.final_truth.get('outcome')


def _copy(r, tmp, mutate_doc=None, mutate_meta=None):
    base = os.path.join(str(tmp), os.path.basename(r.cassette)[:-5])
    doc = json.load(open(r.cassette, encoding='utf-8'))
    meta = json.load(open(r.cassette[:-5] + '.meta.json', encoding='utf-8'))
    if mutate_doc:
        mutate_doc(doc)
    if mutate_meta:
        mutate_meta(meta)
    json.dump(doc, open(base + '.json', 'w', encoding='utf-8'))
    json.dump(meta, open(base + '.meta.json', 'w', encoding='utf-8'))
    return base + '.json'


def test_a_changed_answer_is_different(recorded, tmp_path):
    _, res, _, _ = recorded
    r = next(x for x in res.scenarios if x.id == 'T01-long')

    def wallet(doc):                     # the account answer the sizing reads: another quantity is sent on replay
        for it in doc['interactions']:
            if it['request']['url'].endswith('/fapi/v2/account'):
                it['response']['body_text'] = it['response']['body_text'].replace('5000.12345678', '2500.00000000')
    rr = replay(_copy(r, tmp_path, wallet))
    assert not rr.same and rr.replayed.verdict == FAIL and 'CassetteMismatch' in rr.replayed.error


def test_a_missing_interaction_is_different(recorded, tmp_path):
    _, res, _, _ = recorded
    r = next(x for x in res.scenarios if x.id == 'T01-long')
    rr = replay(_copy(r, tmp_path, lambda doc: doc['interactions'].pop()))
    assert not rr.same


def test_an_extra_interaction_is_different(recorded, tmp_path):
    _, res, _, _ = recorded
    r = next(x for x in res.scenarios if x.id == 'T01-long')
    rr = replay(_copy(r, tmp_path, lambda doc: doc['interactions'].append(copy.deepcopy(doc['interactions'][-1]))))
    assert not rr.same and rr.remaining == 1 and rr.replayed.verdict == PASS


def test_a_different_recorded_verdict_is_different(recorded, tmp_path):
    _, res, _, _ = recorded
    r = next(x for x in res.scenarios if x.id == 'T09')
    rr = replay(_copy(r, tmp_path, mutate_meta=lambda m: m.update(verdict='FAIL')))
    assert not rr.same and rr.replayed.verdict == PASS


def test_the_lost_answer_is_replayed_from_the_cassette_not_reinjected(recorded):
    _, res, _, _ = recorded
    r = next(x for x in res.scenarios if x.id == 'T09')
    doc = json.load(open(r.cassette, encoding='utf-8'))
    assert any(it.get('error') == 'WireTimeout' for it in doc['interactions'])
    rr = replay(r.cassette)
    assert rr.same and rr.replayed.injected == [['entry', 'lost_response', 'replayed']]


def test_a_deadline_fail_replays_as_the_same_fail(tmp_path):
    s = copy.deepcopy(next(x for x in bundled() if x['id'] == 'T01-long'))
    s['bound']['max_wall_s'] = 100
    _, res, _, _ = record(tmp_path, [s])
    r, = res.scenarios
    assert r.verdict == FAIL and r.error.startswith('DeadlineExceeded')
    rr = replay(r.cassette)
    assert rr.same and rr.replayed.verdict == FAIL


@pytest.mark.parametrize('case', ['meta_missing', 'meta_given', 'wrong_format', 'leaky', 'not_a_cassette', 'bad_spec'])
def test_bad_bundles_are_refused(recorded, tmp_path, case):
    _, res, _, _ = recorded
    r = next(x for x in res.scenarios if x.id == 'T01-long')
    if case == 'meta_missing':
        p = _copy(r, tmp_path)
        os.remove(p[:-5] + '.meta.json')
    elif case == 'meta_given':
        p = _copy(r, tmp_path)[:-5] + '.meta.json'
    elif case == 'wrong_format':
        p = _copy(r, tmp_path, mutate_meta=lambda m: m.update(format='x'))
    elif case == 'leaky':
        p = _copy(r, tmp_path, lambda d: d['interactions'][0]['request']['query'].append(['signature', 'abc']))
    elif case == 'not_a_cassette':
        p = _copy(r, tmp_path, lambda d: d.pop('interactions'))
    else:
        p = _copy(r, tmp_path, mutate_meta=lambda m: m['spec'].update(side='BOTH'))
    with pytest.raises(Exception) as ei:
        load_bundle(p)
    assert isinstance(ei.value, (ReplayError, ValueError))


# ---------------------------------------------------------------------------------------------- the CLI
def cli():
    sp = importlib.util.spec_from_file_location('cli_rp', os.path.join(REPO, 'tools', 'newcore_tnet_runner.py'))
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    return mod


def git_clean(args):
    class R:
        returncode = 0
        stdout = 'a' * 40 if args[0] == 'rev-parse' else ''
    return R()


def test_cli_records_lists_the_cassettes_in_the_report_and_replays_them(tmp_path):
    w = World()
    out = io.StringIO()
    rc = cli().main(['--target', 'testnet', '--account-id', ACCOUNT, '--key-digest', DIGEST, '--only', 'T01-long',
                     '--only', 'T09', '--run-nonce', 'cli1', '--cassette-dir', str(tmp_path / 'c'), '--report-dir',
                     str(tmp_path / 'r')], http=w.fb, sleep=w.sleep, local_clock=w.clock, store=Store(), out=out,
                    git_run=git_clean, monotonic=w.monotonic)
    assert rc == 0, out.getvalue()
    names = sorted(os.listdir(tmp_path / 'c'))
    assert names == ['tnet-cli1-preflight.json', 'tnet-cli1-t01-long.json', 'tnet-cli1-t01-long.meta.json',
                     'tnet-cli1-t09.json', 'tnet-cli1-t09.meta.json']
    rep = next(p for p in os.listdir(tmp_path / 'r') if p.endswith('.json') and 'scenarios' not in p)
    doc = json.load(open(tmp_path / 'r' / rep, encoding='utf-8'))
    assert set(doc['cassette']) == {'preflight', 'T01-long', 'T09'}
    out2 = io.StringIO()
    paths = [str(tmp_path / 'c' / n) for n in ('tnet-cli1-t01-long.json', 'tnet-cli1-t09.json')]
    assert cli().main(['--replay', paths[0], '--replay', paths[1]], out=out2) == 0
    assert out2.getvalue().count('SAME') == 2


def test_cli_replay_of_a_tampered_cassette_exits_7_and_a_bad_one_2(recorded, tmp_path):
    _, res, _, _ = recorded
    r = next(x for x in res.scenarios if x.id == 'T01-long')
    p = _copy(r, tmp_path, lambda doc: doc['interactions'].pop())
    out = io.StringIO()
    assert cli().main(['--replay', p], out=out) == 7 and 'DIFFERENT' in out.getvalue()
    assert cli().main(['--replay', str(tmp_path / 'none.json')], out=io.StringIO()) == 2


def test_cli_cassette_leak_writes_nothing_and_exits_5(tmp_path, monkeypatch):
    from newcore.venue import cassette as C

    def leak(self):
        raise C.CassetteLeak('synthetic')
    monkeypatch.setattr(C.CassetteRecorder, 'to_json', leak)
    w = World()
    out = io.StringIO()
    rc = cli().main(['--target', 'testnet', '--account-id', ACCOUNT, '--key-digest', DIGEST, '--only', 'T01-long',
                     '--run-nonce', 'cli2', '--cassette-dir', str(tmp_path / 'c'), '--report-dir',
                     str(tmp_path / 'r')], http=w.fb, sleep=w.sleep, local_clock=w.clock, store=Store(), out=out,
                    git_run=git_clean, monotonic=w.monotonic)
    assert rc == 5 and 'cassette of T01-long not written (CassetteLeak)' in out.getvalue()
    assert [n for n in os.listdir(tmp_path / 'c') if n.startswith('tnet-')] == []


def test_a_secret_in_the_replay_sidecar_writes_nothing(tmp_path):
    s = copy.deepcopy(next(x for x in bundled() if x['id'] == 'T10-floor'))
    s['description'] = 'pasted by mistake: ' + DUMMY_SECRET
    _, res, _, errs = record(tmp_path, [s])
    assert errs == [('T10-floor', 'ReportLeak')] and res.scenarios[0].cassette is None
    assert not [n for n in os.listdir(tmp_path) if 't10' in n]
