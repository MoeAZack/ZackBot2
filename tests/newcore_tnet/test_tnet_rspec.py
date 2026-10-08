"""zb-newcore-tnet-runner/1: the bundled T01-T04 / T09-T12 specs validate; malformed specs are refused, never fixed."""
import copy
import json

import pytest

from newcore.tnet import rspec as R
from newcore.tnet.rspec import SpecError, bundled, expectations, parse_rspec, validate_rspec

WANTED = {'T01-long', 'T02-short', 'T03', 'T04-algo', 'T04-classic', 'T09', 'T10-floor', 'T10-refused',
          'T10-stop-refused', 'T11', 'T12-flat', 'T12-protected'} | \
    {f'T0{n}-{side}' for n in (5, 6, 7, 8) for side in ('long', 'short')}


def base():
    return copy.deepcopy(next(s for s in bundled() if s['id'] == 'T01-long'))


def test_bundled_specs_cover_t01_to_t12():
    specs = bundled()
    assert {s['id'] for s in specs} == WANTED
    assert len({s['name'] for s in specs}) == len(specs)
    mg = [s for s in specs if s['id'][:3] in ('T05', 'T06', 'T07', 'T08')]
    assert len(mg) == 8 and all(s['management']['enabled'] and s['targets'] == ['fake'] for s in mg)
    assert not any('management' in s for s in specs if s not in mg)
    for s in specs:
        assert validate_rspec(s) is s


def test_long_and_short_cycles_are_mirrors():
    a, b = (next(s for s in bundled() if s['id'] == i) for i in ('T01-long', 'T02-short'))
    assert (a['side'], b['side']) == ('LONG', 'SHORT')
    assert a['steps'] == b['steps'] and a['expect'] == b['expect']


def test_expectations_merge_the_testnet_override_only_on_testnet():
    s = base()
    assert expectations(s, 'fake')['max_orders'] == 3
    assert expectations(s, 'testnet')['max_orders'] == 4                             # the refused classic attempt


def test_floats_are_refused():
    with pytest.raises(SpecError, match='floats'):
        parse_rspec(json.dumps(base()).replace('"0.0005"', '0.0005'))


def test_not_json_is_refused():
    with pytest.raises(SpecError, match='not JSON'):
        parse_rspec('{')


@pytest.mark.parametrize('mutate,path', [
    (lambda s: s.update(extra=1), '$'),
    (lambda s: s.update(format='zb-newcore-tnet-scenario/1'), '$.format'),
    (lambda s: s.update(id='T1'), '$.id'),
    (lambda s: s.update(id='T01-a-b-c'), '$.id'),
    (lambda s: s.update(name='X'), '$.name'),
    (lambda s: s.update(targets=[]), '$.targets'),
    (lambda s: s.update(targets=['fake', 'fake']), '$.targets'),
    (lambda s: s.update(targets=['mainnet']), '$.targets'),
    (lambda s: s.update(symbol='sol'), '$.symbol'),
    (lambda s: s.update(side='BOTH'), '$.side'),
    (lambda s: s['sizing'].update(risk_pct='0.06'), '$.sizing.risk_pct'),
    (lambda s: s['sizing'].update(risk_pct='0'), '$.sizing.risk_pct'),
    (lambda s: s['sizing'].update(max_leverage='11'), '$.sizing.max_leverage'),
    (lambda s: s.update(stop={'pct': '1', 'atr': '1'}), '$.stop'),
    (lambda s: s.update(stop={'pct': '21'}), '$.stop.pct'),
    (lambda s: s.update(fake={'classic_stops': 'maybe'}), '$.fake.classic_stops'),
    (lambda s: s.update(fake={'other': 1}), '$.fake'),
    (lambda s: s['bound'].update(max_orders=21), '$.bound.max_orders'),
    (lambda s: s['bound'].update(max_wall_s=3601), '$.bound.max_wall_s'),
    (lambda s: s['bound'].update(max_notional_usdt='100001'), '$.bound.max_notional_usdt'),
    (lambda s: s['bound'].update(max_ticks=4), '$.bound.max_ticks'),            # the steps need 5 cycles
    (lambda s: s.update(steps=[]), '$.steps'),
    (lambda s: s['steps'].append({'op': 'jump'}), '$.steps[4].op'),
    (lambda s: s['steps'].append({'op': 'tick'}), '$.steps[4]'),
    (lambda s: s['steps'].append({'op': 'tick', 'n': 0}), '$.steps[4].n'),
    (lambda s: s['steps'].append({'op': 'tick', 'n': True}), '$.steps[4].n'),
    (lambda s: s['steps'].append({'op': 'enter', 'side': 'UP'}), '$.steps[4].side'),
    (lambda s: s['steps'].append({'op': 'fault', 'on': 'entry', 'kind': 'refuse'}), '$.steps[4].code'),
    (lambda s: s['steps'].append({'op': 'fault', 'on': 'entry', 'kind': 'timeout', 'code': -1}), '$.steps[4].code'),
    (lambda s: s['steps'].append({'op': 'fault', 'on': 'query', 'kind': 'timeout'}), '$.steps[4].on'),
    (lambda s: s['steps'].append({'op': 'fault', 'on': 'entry', 'kind': 'reset'}), '$.steps[4].kind'),
    (lambda s: s['steps'].append({'op': 'check', 'what': 'empty'}), '$.steps[4].what'),
    (lambda s: s['steps'].append({'op': 'await_exit', 'max_ticks': 1, 'fake_gap_pct': '-31'}),
     '$.steps[4].fake_gap_pct'),
    (lambda s: s['expect'].update(final='gone'), '$.expect.final'),
    (lambda s: s['expect'].update(trades='SIGNAL_EXIT'), '$.expect.trades'),
    (lambda s: s['expect'].update(entry_phases=['final', 'done']), '$.expect.entry_phases'),
    (lambda s: s['expect'].update(hold_seen=1), '$.expect.hold_seen'),
    (lambda s: s['expect'].update(stop_route='ws'), '$.expect.stop_route'),
    (lambda s: s['expect'].update(unknown=1), '$.expect'),
    (lambda s: (s.update(targets=['fake'])), '$.expect_testnet'),
])
def test_malformed_specs_are_refused(mutate, path):
    s = base()
    mutate(s)
    with pytest.raises(SpecError) as ei:
        validate_rspec(s)
    assert ei.value.path == path


def test_tick_budget_counts_every_cycle_kind():
    s = base()
    s['steps'] = [{'op': 'enter'}, {'op': 'tick', 'n': 3}, {'op': 'await_exit', 'max_ticks': 5}, {'op': 'close'}]
    s['bound']['max_ticks'] = 10
    validate_rspec(s)
    s['bound']['max_ticks'] = 9
    with pytest.raises(SpecError, match='10 cycles'):
        validate_rspec(s)


def test_spec_dir_is_inside_the_package():
    assert R.SPEC_DIR.replace('\\', '/').endswith('newcore/tnet/specs')
