"""TNET-01 scenario-spec format zb-newcore-tnet-scenario/1: strict validation, never fixed up."""
import copy
import json
import os

import pytest

from newcore.venue import tnet_spec as S

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLE = os.path.join(HERE, 'fixtures', 'tnet_entry_stop_close_long.json')


def example():
    with open(EXAMPLE, encoding='utf-8') as fh:
        return json.load(fh)


def test_the_example_spec_is_valid_and_has_a_stable_digest():
    doc = S.load_spec(EXAMPLE)
    assert doc['name'] == 'entry_stop_close_long' and len(S.spec_digest(doc)) == 64
    assert S.spec_digest(doc) == S.spec_digest(json.loads(json.dumps(doc, sort_keys=False)))


def mutate(fn):
    doc = copy.deepcopy(example())
    fn(doc)
    return doc


@pytest.mark.parametrize('fn,path', [
    (lambda d: d.update(format='zb-newcore-tnet-scenario/2'), '$.format'),
    (lambda d: d.update(name='Bad Name'), '$.name'),
    (lambda d: d.update(extra=1), '$'),
    (lambda d: d.pop('expect'), '$'),
    (lambda d: d.update(symbols=[]), '$.symbols'),
    (lambda d: d.update(symbols=['SOLUSDT', 'SOLUSDT']), '$.symbols'),
    (lambda d: d['steps'][0].update(op='limit'), '$.steps[0].op'),
    (lambda d: d['steps'][0].update(symbol='BTCUSDT'), '$.steps[0].symbol'),
    (lambda d: d['steps'][0].update(side='BOTH'), '$.steps[0].side'),
    (lambda d: d['steps'][0].update(reduce='false'), '$.steps[0].reduce'),
    (lambda d: d['steps'][0].update(qty='0'), '$.steps[0].qty'),
    (lambda d: d['steps'][0].update(qty='1e3'), '$.steps[0].qty'),
    (lambda d: d['steps'][0].update(price='1'), '$.steps[0]'),
    (lambda d: d['steps'][1].update(id='entry'), '$.steps[1].id'),
    (lambda d: d['steps'][1].update(ref='protect'), '$.steps[1].ref'),               # forward reference
    (lambda d: d['steps'][2].update(qty='filled:verify'), '$.steps[2].qty'),
    (lambda d: d['steps'][2].update(trigger_offset_pct='-90'), '$.steps[2].trigger_offset_pct'),
    (lambda d: d['steps'][2].update(route='ALGO'), '$.steps[2].route'),
    (lambda d: d['steps'][6].update(seconds=0), '$.steps[6].seconds'),
    (lambda d: d['steps'][6].update(seconds=True), '$.steps[6].seconds'),
    (lambda d: d['faults'][0].update(kind='meteor'), '$.faults[0].kind'),
    (lambda d: d['faults'][0].update(at='settle'), '$.faults[0].at'),
    (lambda d: d['faults'][0].update(times=9), '$.faults[0].times'),
    (lambda d: d['expect']['outcomes'].update(close=['filled']), '$.expect.outcomes.close'),
    (lambda d: d['expect']['outcomes'].update(nope=['final']), '$.expect.outcomes.nope'),
    (lambda d: d['expect']['end_state'].update(flat='yes'), '$.expect.end_state.flat'),
    (lambda d: d['expect']['end_state'].pop('open_newcore_orders'), '$.expect.end_state'),
    (lambda d: d['expect'].update(max_duration_s=99999), '$.expect.max_duration_s'),
    (lambda d: d['account'].update(min_balance='-1'), '$.account.min_balance'),
    (lambda d: d['account'].update(api_key='x'), '$.account'),
])
def test_invalid_specs_are_refused_with_a_path(fn, path):
    with pytest.raises(S.SpecError) as ei:
        S.validate_spec(mutate(fn))
    assert ei.value.path == path


def test_json_floats_are_refused(tmp_path):
    p = tmp_path / 'f.json'
    doc = example()
    text = json.dumps(doc).replace('"min_balance": "100"', '"min_balance": 100.0')
    p.write_text(text, encoding='utf-8')
    with pytest.raises(S.SpecError):
        S.load_spec(str(p))


def test_not_json_is_refused(tmp_path):
    p = tmp_path / 'x.json'
    p.write_text('{nope', encoding='utf-8')
    with pytest.raises(S.SpecError):
        S.load_spec(str(p))
