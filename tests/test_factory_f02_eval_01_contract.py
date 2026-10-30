"""FACTORY-F02-EVAL-01: result-blind binding of EDGE04 H3 to a later isolated C-F02 PAPER decision."""
import hashlib
import json
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, 'contracts', 'factory_f02_eval_01', 'factory_f02_eval_01_contract.v1.json')


def load(path=PATH):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def sha(path):
    with open(os.path.join(ROOT, path), 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_contract_binds_the_signed_family_and_exact_f02_cell():
    c = load()
    assert c['format'] == 'zb-factory-f02-eval/1' and c['status'].startswith('DRAFT')
    for p in c['parents'].values():
        assert sha(p['path']) == p['sha256']
    family = json.load(open(os.path.join(ROOT, c['parents']['family']['path']), encoding='utf-8'))
    assert family['family']['members']['C-F02'] == ['exp_trend', 'long']
    x = c['candidate']
    assert (x['slot'], x['strategy'], x['side'], x['timeframe'], x['family']) == \
           ('C-F02', 'exp_trend', 'long', '4h', 'trend_long')
    assert x['management'] == {'stop_atr': 2.5, 'tp_r': 3.0, 'max_bars': 180}


def test_edge04_is_the_only_execution_grade_input_and_no_result_was_read():
    c = load(); b = c['boundaries']; e = c['edge04_binding']
    assert e['hypothesis'] == 'H3' and e['variant'] == 'H3.primary' and e['strategy'] == 'exp_trend'
    assert e['repository_pr'] == 217 and len(e['observed_head_at_freeze']) == 40
    assert 'final signed' in e['rule'] and 'must not launch another' in e['no_duplicate']
    assert b == dict(market_data_opened=False, edge04_result_read=False, holdout_read=False,
                     paper_changed=False, runtime_changed=False, mainnet=False)


def test_every_execution_grade_gate_is_frozen_and_no_rescue_exists():
    c = load(); g = c['frozen_gates']; d = c['decision']
    assert set(g) == {'data', 'activity', 'walk_forward', 'expectancy', 'baselines', 'stress',
                      'concentration', 'drawdown', 'multiplicity', 'verdict'}
    assert '4 of 5' in g['walk_forward'] and '0.75' in g['activity'] and '95%' in g['expectancy']
    assert 'x1.5' in g['stress'] and '25%' in g['drawdown'] and 'Holm' in g['multiplicity']
    assert 'every gate' in g['verdict'] and 'cannot rescue' in g['verdict']
    assert 'no tuning' in d['park'] and 'INCONCLUSIVE' not in d['eligible_for_paper']
    assert 'never waives' in d['high_drawdown']


def test_later_paper_is_isolated_default_off_and_separately_authorized():
    c = load(); p = c['paper_pack']; deferred = ' '.join(c['deferred'])
    assert p['prepared_only_after_eligibility'] is True
    assert 'C-F02 runs alone' in p['isolation'] and 'C-F01 must be OFF' in p['isolation']
    assert p['default'].startswith('OFF') and 'separate Codex PAPER authorization' in p['default']
    assert 'fail-closed' in p['entry_and_cost_parity'] and 'confirmed Binance testnet protection' in p['protection']
    assert 'never fall back silently' in p['rollback']
    assert all(x in deferred for x in ('active EDGE04 H3 result', 'PAPER/testnet mutation', 'mainnet/live'))
