"""Cowork #37 batch 1 (venue CLI / spec loader / run config / names): repro tests. Fake HTTP, DUMMY keys."""
import io
import json
from decimal import Decimal as D

import pytest

from fake_binance import FakeBinance
from ncv_support import DUMMY_KEY
from test_ncv_tnet_harness import EXAMPLE, env, run, tool  # noqa: F401  (env is a fixture)

from newcore.venue.redact import is_sensitive_name
from newcore.venue.run_config import RunConfigError, load_testnet_config, parse_testnet_config
from newcore.venue.tnet import adopt_refusal
from newcore.venue.tnet_spec import SpecError, load_spec


def foreign_long(fb, qty='3'):
    fb.pos[('SOLUSDT', 'LONG')] = D(qty)
    fb.visible_pos[('SOLUSDT', 'LONG')] = D(qty)
    return fb


def spec_file(tmp_path, mutate, name='s.json'):
    s = json.load(open(EXAMPLE, encoding='utf-8'))
    mutate(s)
    p = tmp_path / name
    p.write_text(json.dumps(s), encoding='utf-8')
    return str(p)


def no_writes(fb):
    return not [q for q in fb.requests if q.method in ('POST', 'DELETE')]


def test_t2_spec_end_state_is_baseline_aware(env):  # noqa: F811
    fb = foreign_long(FakeBinance())
    rc, out = run(env, ['--scenario', EXAMPLE, '--adopt-foreign', 'SOLUSDT:LONG'], http=fb)
    assert rc == 0 and 'scenario entry_stop_close_long: PASS' in out and fb.pos[('SOLUSDT', 'LONG')] == D('3')


def test_t3_spec_min_balance_is_honoured(env, tmp_path):  # noqa: F811
    p = spec_file(tmp_path, lambda s: s['account'].update(min_balance='1000000'))
    fb = FakeBinance()
    rc, out = run(env, ['--scenario', p], http=fb)
    assert rc == 4 and 'balance_below_min' in out and no_writes(fb)


def test_t3_spec_adopt_foreign_is_honoured(env, tmp_path):  # noqa: F811
    p = spec_file(tmp_path, lambda s: s['account'].update(adopt_foreign=['SOLUSDT:LONG']))
    fb = foreign_long(FakeBinance())
    rc, out = run(env, ['--scenario', p], http=fb)
    assert rc == 0 and fb.pos[('SOLUSDT', 'LONG')] == D('3')


def test_t3_spec_max_duration_bounds_the_spec(env, tmp_path):  # noqa: F811
    def m(s):
        s['steps'].insert(1, {'id': 'nap', 'op': 'wait', 'seconds': 60})
        s['steps'].insert(2, {'id': 'again', 'op': 'market', 'symbol': 'SOLUSDT', 'side': 'LONG', 'reduce': False,
                              'qty': 'min'})
        s['faults'], s['expect']['outcomes'], s['expect']['max_duration_s'] = [], {}, 30
    fb = FakeBinance()
    rc, out = run(env, ['--scenario', spec_file(tmp_path, m)], http=fb)
    assert rc == 6 and 'a 60 s wait would end past the run deadline' in out
    assert len([q for q in fb.requests if q.method == 'POST' and 'side=BUY' in q.query]) == 1
    assert fb.flat() and 'CLEANUP CLEAN' in out


def test_t5_an_unexpected_exception_in_the_run_is_typed_and_cleaned(env, monkeypatch):  # noqa: F811
    from newcore.ports import venue as P
    from newcore.venue.tnet_probes import probe_ref
    mod = tool()

    def boom(venue, **kw):
        venue.submit_market(P.MarketOrder(ref=probe_ref('x', 'boom', 'SOLUSDT'), position_side='LONG', qty=D('1'),
                                          reduce=False))
        raise RuntimeError('boom ' + DUMMY_KEY)
    monkeypatch.setattr(mod, 'probe_p1', boom)
    fb = FakeBinance()
    rc, out = run(env, ['--probe', 'P1'], http=fb, mod=mod)
    assert rc == 7 and 'unexpected RuntimeError' in out and DUMMY_KEY not in out
    assert fb.flat() and 'CLEANUP CLEAN' in out


def test_t5_an_unexpected_exception_before_the_run_is_typed():
    def bad_git(args):
        raise RuntimeError('git exploded ' + DUMMY_KEY)
    out = io.StringIO()
    rc = tool().main(['--account-id', 'acct_' + '1' * 32, '--probe', 'P1'], out=out, git_run=bad_git)
    assert rc == 7 and 'unexpected RuntimeError' in out.getvalue() and DUMMY_KEY not in out.getvalue()


@pytest.mark.parametrize('argv,why', [
    (['--min-balance', 'NaN'], '--min-balance'), (['--min-balance', 'sNaN'], '--min-balance'),
    (['--min-balance', '-5'], '--min-balance'), (['--min-balance', 'Infinity'], '--min-balance'),
    (['--p2-samples', '0'], '--p2-samples'), (['--p2-samples', '11'], '--p2-samples'),
    (['--p2-samples', '-1'], '--p2-samples'),
    (['--cleanup-attempts', '0'], '--cleanup-attempts'), (['--cleanup-attempts', '-3'], '--cleanup-attempts'),
    (['--adopt-foreign', 'SOLUSDT:FLAT'], 'adopt'), (['--adopt-foreign', ''], 'adopt'),
    (['--adopt-foreign', 'solusdt:long'], 'adopt'), (['--adopt-foreign', 'SOLUSDT:LONG:x'], 'adopt'),
    (['--adopt-foreign', 'zbn1o-' + 'a' * 26], 'REFUSED'), (['--adopt-foreign', 'a b'], 'adopt'),
])
def test_t6_t7_t9_bad_arguments_are_refused_before_any_request(env, argv, why):  # noqa: F811
    fb = FakeBinance()
    rc, out = run(env, ['--probe', 'P1', *argv], http=fb)
    assert rc == 2 and why in out and fb.requests == []


@pytest.mark.parametrize('value,ok', [('web_x', True), ('SOLUSDT:SHORT', True), ('x:y', False), ('', False)])
def test_adopt_refusal(value, ok):
    assert (adopt_refusal(value) is None) == ok


def test_report_dir_that_is_a_file_is_refused_up_front(env, tmp_path):  # noqa: F811
    f = tmp_path / 'afile'
    f.write_text('x')
    fb = FakeBinance()
    rc, out = run(dict(env, reports=str(f)), ['--probe', 'P1'], http=fb)
    assert rc == 2 and '--report-dir is not a directory' in out and fb.requests == []


def test_config_deep_nesting_and_bad_shapes_are_typed(tmp_path):
    p = tmp_path / 'deep.toml'
    p.write_text('x = ' + '[' * 3000 + ']' * 3000 + '\n', encoding='utf-8')
    with pytest.raises(RunConfigError):
        load_testnet_config(str(p))
    deep = {'mode': 'TESTNET'}
    node = deep
    for _ in range(40):
        node['n'] = {}
        node = node['n']
    with pytest.raises(RunConfigError, match='nested deeper'):
        parse_testnet_config(deep)
    with pytest.raises(RunConfigError, match='strategy.symbols'):
        parse_testnet_config({'mode': 'TESTNET', 'venue': {'factory': 'newcore.venue.factory:build_testnet'},
                              'account': {'id': 'acct_' + 'b' * 32, 'key_digest': 'f' * 16},
                              'strategy': {'symbols': [['x']]}})


def test_config_errors_never_echo_values():
    for doc in ({'mode': DUMMY_KEY}, {'mode': 'TESTNET', 'venue': {'kind': DUMMY_KEY}}):
        with pytest.raises(RunConfigError) as ei:
            parse_testnet_config(doc)
        assert DUMMY_KEY not in str(ei.value)


@pytest.mark.parametrize('name', ['ｓｉｇｎａｔｕｒｅ', 'api​key',
                                  'Sig­nature', 'X–MBX–APIKEY', 'ＳＥＣＲＥＴ'])
def test_unicode_disguised_names_are_sensitive(name):
    assert is_sensitive_name(name)


def test_fullwidth_credential_names_in_a_config_are_refused():
    with pytest.raises(RunConfigError, match='credential-like'):
        parse_testnet_config({'mode': 'TESTNET', 'account': {'ａｐｉ＿ｓｅｃｒｅｔ': 'x'}})


@pytest.mark.parametrize('content', [b'\xff\xfe{', b'[' * 5000 + b']' * 5000, None])
def test_malformed_spec_files_are_spec_errors(tmp_path, content):
    p = tmp_path / 's.json'
    if content is None:
        s = json.load(open(EXAMPLE, encoding='utf-8'))
        s['expect']['outcomes'] = [['entry', 'final']]
        p.write_text(json.dumps(s), encoding='utf-8')
    else:
        p.write_bytes(content)
    with pytest.raises(SpecError):
        load_spec(str(p))


def test_a_fake_only_spec_is_refused_by_the_testnet_cli(env, tmp_path):  # noqa: F811
    p = spec_file(tmp_path, lambda s: s.update(targets=['fake']))
    fb = FakeBinance()
    rc, out = run(env, ['--scenario', p], http=fb)
    assert rc == 2 and 'is not a testnet scenario' in out and fb.requests == []


def test_a_key_pasted_as_a_path_is_never_echoed(env, tmp_path):  # noqa: F811
    for argv in (['--scenario', str(tmp_path / (DUMMY_KEY + '.json'))], ['--config', str(tmp_path / DUMMY_KEY)]):
        rc, out = run(env, ['--probe', 'P1', *argv], http=FakeBinance())
        assert rc == 2 and DUMMY_KEY not in out
