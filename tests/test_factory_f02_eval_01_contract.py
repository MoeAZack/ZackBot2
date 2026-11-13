"""FACTORY-F02-EVAL-01: result-blind binding of EDGE04 H3 to a later isolated C-F02 PAPER decision."""
import hashlib
import json
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, 'contracts', 'factory_f02_eval_01', 'factory_f02_eval_01_contract.v1.json')
EXPECTED_H3_MANAGEMENT = {'stop_atr': 2.5, 'tp_r': 3.0, 'max_bars': 180}
EXPECTED_H3_SIGNAL = 'EMA20 crosses above EMA50, close>EMA200, ret42>0 and ret180>0'
EXPECTED_H3_EXIT = 'EMA20<EMA50 or either momentum return is non-positive'
EXPECTED_GATES = {
    'data': 'every headline symbol/split complete; no account bust; no unresolved accounting row',
    'activity': 'at least 60 closed episodes and at least 0.75 closed episodes per week across development+walk-forward',
    'walk_forward': 'at least 4 of 5 folds have positive net R; combined walk-forward mean net R>0 and PF>1.10',
    'expectancy': 'episode-clustered 95% lower confidence bound of combined walk-forward mean net R>0; pre-cost gross mean R>0',
    'baselines': 'base net return exceeds B1 and the episode-clustered 95% lower bound of candidate-minus-B0 mean R is >0',
    'stress': 'all-variable-costs x1.5 mean net R>0; no sign flip at 60-second latency; fees/slippage x2 reported',
    'concentration': 'no symbol exceeds 35% of absolute net-R contribution and top three do not exceed 60%',
    'drawdown': 'base max equity drawdown<25%, closed-trade max drawdown<25R, and all-variable-costs x1.5 max equity drawdown<35%',
    'multiplicity': 'Holm alpha 0.05 across the four primaries; report deflated/search-aware statistics with 75 prior+new strategy looks',
    'verdict': 'PASS only if every gate passes for the frozen primary; diagnostics cannot rescue it',
    'additional_no_rescue': 'Development packets and PAPER cannot rescue a failed H3 primary.',
}


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
    assert x['management'] == EXPECTED_H3_MANAGEMENT
    assert x['signal'] == EXPECTED_H3_SIGNAL and x['exit'] == EXPECTED_H3_EXIT


def test_edge04_is_the_only_execution_grade_input_and_no_result_was_read():
    c = load(); b = c['boundaries']; e = c['edge04_binding']
    assert e['hypothesis'] == 'H3' and e['variant'] == 'H3.primary' and e['strategy'] == 'exp_trend'
    assert e['repository_pr'] == 217 and len(e['observed_head_at_freeze']) == 40
    assert e['prereg_sha256'] == 'ac914c1647652e5d4e9f267b45414bae32cc59e308837812a3ffdfbd98194bd2'
    assert e['h3_signal'] == EXPECTED_H3_SIGNAL + '; exit ' + EXPECTED_H3_EXIT
    assert 'must remain identical' in e['runtime_parity'] and 'INCONCLUSIVE' in e['runtime_parity']
    assert 'final signed' in e['rule'] and 'must not launch another' in e['no_duplicate']
    assert b == dict(market_data_opened=False, edge04_result_read=False, holdout_read=False,
                     paper_changed=False, runtime_changed=False, mainnet=False)


def test_every_execution_grade_gate_is_frozen_and_no_rescue_exists():
    c = load(); g = c['frozen_gates']; d = c['decision']
    assert g == EXPECTED_GATES
    assert 'no tuning' in d['park'] and 'INCONCLUSIVE' not in d['eligible_for_paper']
    assert 'never waives' in d['high_drawdown']
    assert 'new signed amendment' in d['inconclusive_rerun'] and 'never silently restart' in d['inconclusive_rerun']


def test_later_paper_is_isolated_default_off_and_separately_authorized():
    c = load(); p = c['paper_pack']; deferred = ' '.join(c['deferred'])
    assert p['prepared_only_after_eligibility'] is True
    assert 'C-F02 runs alone' in p['isolation'] and 'C-F01 must be OFF' in p['isolation']
    assert p['default'].startswith('OFF') and 'separate Codex PAPER authorization' in p['default']
    assert 'fail-closed' in p['entry_and_cost_parity'] and 'confirmed Binance testnet protection' in p['protection']
    assert 'never fall back silently' in p['rollback']
    assert all(x in deferred for x in ('active EDGE04 H3 result', 'PAPER/testnet mutation', 'mainnet/live'))
