"""tools/newcore_replay_cassette.py: replay a recorded smoke cassette offline, PASS / FAIL per interaction."""
import importlib.util
import io
import json
import os

import pytest

from newcore.venue import scenarios as S
from newcore.venue.cassette_replay import leak_audit, replay_report, scenario_from_cassette

from test_ncv_smoke import REPO, cassette_files, env, flat_script  # noqa: F401
from test_ncv_smoke_trade import run, trade_script


def tool():
    spec = importlib.util.spec_from_file_location('ncv_replay_cli', os.path.join(REPO, 'tools',
                                                                                 'newcore_replay_cassette.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cli(path):
    out = io.StringIO()
    rc = tool().main([str(path)], out=out)
    return rc, out.getvalue()


@pytest.fixture
def smoke_cassette(env):
    rc, out, _ = run(env, flat_script() + trade_script(), ['--trade'])
    assert rc == 0, out
    (path,) = cassette_files(env)
    return path


def test_real_smoke_cassette_replays_every_interaction(smoke_cassette):
    rc, out = cli(smoke_cassette)
    doc = json.load(open(smoke_cassette, encoding='utf-8'))
    n = len(doc['interactions'])
    assert rc == 0, out
    lines = out.splitlines()
    assert sum(ln.startswith('PASS ') for ln in lines) == n and not any(ln.startswith('FAIL') for ln in lines)
    assert f'{n} PASS / {n} total; leak audit: clean' in out
    assert 'POST /fapi/v1/order (market)' in out and 'POST /fapi/v1/algoOrder (algo stop)' in out
    assert 'DELETE /fapi/v1/algoOrder' in out and 'GET /fapi/v1/klines' in out and 'GET /fapi/v1/income' in out


def test_read_only_cassette_with_a_timeout_interaction(env):
    from newcore.venue.wire import WireTimeout
    rc, _, _ = run(env, flat_script(account=WireTimeout())[:5])
    (path,) = cassette_files(env)
    rc, out = cli(path)
    assert rc == 0 and 'unknown (timeout)' in out


@pytest.mark.parametrize('scenario', S.SCENARIOS, ids=lambda s: s.name)
def test_scenario_cassettes_replay_through_the_cli_logic(scenario):
    doc = json.loads(S.run_scenario(scenario).cassette)
    results = replay_report(doc)
    assert [r.status for r in results] == ['PASS'] * len(doc['interactions'])


def test_scenario_from_cassette_works_with_replay_cassette():
    text = S.run_scenario(S.LIFECYCLE).cassette
    outcomes = S.replay_cassette(scenario_from_cassette(json.loads(text)), text)
    assert len(outcomes) == len(json.loads(text)['interactions'])


def test_request_the_transport_cannot_have_sent_fails_there_and_skips_the_rest(smoke_cassette, tmp_path):
    doc = json.load(open(smoke_cassette, encoding='utf-8'))
    i = next(k for k, it in enumerate(doc['interactions']) if it['request']['url'].endswith('/fapi/v1/order'))
    doc['interactions'][i]['request']['query'].append(['foo', '1'])      # a parameter our transport never sends
    p = tmp_path / 'tampered.json'
    p.write_text(json.dumps(doc), encoding='utf-8')
    rc, out = cli(p)
    lines = out.splitlines()
    assert rc == 1 and all(ln.startswith('PASS') for ln in lines[:i])
    assert lines[i].startswith('FAIL') and 'CassetteMismatch' in lines[i]
    assert all(ln.startswith('SKIPPED') for ln in lines[i + 1:-1])


def test_consistently_edited_request_still_replays_as_typed_outcomes(smoke_cassette, tmp_path):
    # PASS means "the recorded request is exactly what the transport sends for that call, and the recorded answer
    # parses into a typed outcome" - an edited answer shows up in the outcome, e.g. as UNKNOWN (echo_mismatch).
    doc = json.load(open(smoke_cassette, encoding='utf-8'))
    i = next(k for k, it in enumerate(doc['interactions']) if it['request']['url'].endswith('/fapi/v1/order'))
    doc['interactions'][i]['request']['query'] = [[k, 'BTCUSDT' if k == 'symbol' else v]
                                                  for k, v in doc['interactions'][i]['request']['query']]
    results = replay_report(doc)
    assert results[i].status == 'PASS' and 'echo_mismatch' in results[i].detail


def test_unsupported_endpoint_fails(tmp_path):
    doc = json.loads(S.run_scenario(S.DUPLICATE).cassette)
    doc['interactions'][1]['request']['url'] = 'https://testnet.binancefuture.com/fapi/v1/listenKey'
    results = replay_report(doc)
    assert results[0].status == 'PASS' and results[1].status == 'FAIL'
    assert results[1].label.endswith('GET /fapi/v1/listenKey')                       # the label names the endpoint
    assert results[1].detail.startswith('UnsupportedInteraction (detail withheld, ref ')   # never raw text (Codex)


def test_leak_audit_finding_fails_the_cli(smoke_cassette, tmp_path):
    doc = json.load(open(smoke_cassette, encoding='utf-8'))
    doc['interactions'][0]['response']['headers']['Set-Cookie'] = 'session=abc123'
    p = tmp_path / 'leaky.json'
    p.write_text(json.dumps(doc), encoding='utf-8')
    rc, out = cli(p)
    assert rc == 1 and 'leak audit: FAIL' in out
    assert leak_audit(json.load(open(smoke_cassette, encoding='utf-8'))) is None


@pytest.mark.parametrize('content,args', [(None, ['missing.json']), ('{"format": "x"}', None), ('not json', None),
                                          (None, []), (None, ['--help'])])
def test_bad_input_is_exit_two(tmp_path, content, args):
    if content is not None:
        p = tmp_path / 'bad.json'
        p.write_text(content, encoding='utf-8')
        args = [str(p)]
    elif args and args[0] == 'missing.json':
        args = [str(tmp_path / 'missing.json')]
    out = io.StringIO()
    assert tool().main(args, out=out) == 2
