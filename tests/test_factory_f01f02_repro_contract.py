"""FACTORY-F01F02-REPRO independent-reproduction contract (Codex 6488285235): the JSON twin is authoritative, stays
DRAFT/unsigned, pins F01 `ema_mom` long and F02 `exp_trend` long fixed-18 development packets (Codex 6468259657,
6442094571, 6443422894) and their 131-fill / Jaccard 0.655 overlap, and forbids importing Build's code, holdout reads,
PAPER/runtime wiring, dual full-risk allocation and order/trade ids.

Contract only - reads the JSON (and, for sha pins, the committed files on master). Never reads market data or packets.
"""
import hashlib
import json
import math
import os
import re
from fractions import Fraction

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_DIR = os.path.join(ROOT, 'contracts', 'factory_f01f02_repro')
JS = os.path.join(CONTRACT_DIR, 'factory_f01f02_repro_contract.v1.json')
MD = os.path.join(CONTRACT_DIR, 'FACTORY_F01F02_REPRO_CONTRACT.md')
HEX40, HEX64 = re.compile(r'[0-9a-f]{40}'), re.compile(r'[0-9a-f]{64}')
CUTOFF = '2026-09-28T00:00:00Z'
FIXED18 = ['BTCUSDT', 'ETHUSDT', 'ZECUSDT', 'SOLUSDT', 'XRPUSDT', 'NEARUSDT', 'DOGEUSDT', 'UNIUSDT', 'SUIUSDT', 'QNTUSDT',
           'BNBUSDT', 'LSKUSDT', 'ENAUSDT', '1000PEPEUSDT', 'WLDUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT']
# The values Codex reported (6468259657 / 6442094571) at packet precision; the coordinator's frozen headline.
HEADLINE = {'F01': dict(key='ema_mom', trades=160, net_r=101.091624, pf=2.773052, dd=7.55),
            'F02': dict(key='exp_trend', trades=171, net_r=56.692730, pf=1.996836, dd=7.62)}


@pytest.fixture(scope='module')
def c():
    with open(JS, encoding='utf-8') as fh:
        return json.load(fh)


@pytest.fixture(scope='module')
def md():
    with open(MD, encoding='utf-8') as fh:
        return fh.read()


def _sha_lf(rel):
    with open(os.path.join(ROOT, rel), 'rb') as fh:
        return hashlib.sha256(fh.read().replace(b'\r\n', b'\n')).hexdigest()


def _sha_raw(rel):
    with open(os.path.join(ROOT, rel), 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# ------------------------------------------------------------------ status / shape
def test_json_parses_and_is_draft_unsigned(c, md):
    assert c['contract'] == 'FACTORY-F01F02-REPRO' and c['version'] == 1
    assert c['status'] == 'DRAFT' and c['signed_by'] is None and c['frozen_at'] is None
    assert c['doc'] == os.path.basename(MD) and c['assignment'].startswith('Codex 6488285235')
    assert 'JSON wins any conflict' in c['authority']
    assert '**Status: DRAFT — unsigned.**' in md and 'the JSON wins any conflict' in md


def test_required_sections(c):
    for k in ('evidence_sources', 'code', 'window', 'slices', 'universe', 'data', 'costs', 'cells', 'overlap',
              'semantics', 'tolerances', 'verdict_schema', 'forbidden', 'open_questions', 'binding_checks', 'deferred'):
        assert k in c, k
    assert c['evidence_sources']['headline'].startswith('Codex #219 6468259657')
    assert 'Build does NOT rerun' in c['build_role']


def test_code_shas_and_runner_pins(c):
    code = c['code']
    assert HEX40.fullmatch(code['packet_commit']) and code['packet_commit'] == 'b5957d58f82e0247717e2111ab5b2b41dac9a24e'
    assert HEX40.fullmatch(code['master_reference']) and code['master_reference'].startswith('184ec2e')
    r = code['runner']
    assert r['path'] == 'tools/research/run_breadth_candidate.py' and r['mgmt_mode'] == 'defaults'
    for k in ('sha256', 'helper_sha256'):
        assert HEX64.fullmatch(r[k]), k


def test_runner_pins_match_committed_files(c):
    r = c['code']['runner']
    for path, want in ((r['path'], r['sha256']), (r['helper_path'], r['helper_sha256'])):
        assert want in (_sha_raw(path), _sha_lf(path)), path


# ------------------------------------------------------------------ window / slices / universe / costs
def test_window_and_holdout_boundary(c):
    w = c['window']
    assert (w['start'], w['end'], w['end_exclusive'], w['tf']) == ('2024-10-14T00:00:00Z', CUTOFF, True, '4h')
    assert w['sealed_cutoff'] == CUTOFF and w['warmup_bars'] == 220 and w['warmup_start'] == '2024-09-07T08:00:00Z'
    assert w['bars'] == 4284                                                     # 714 days x 6 bars


def test_three_contiguous_chronological_slices(c):
    s = c['slices']['list']
    assert [x['id'] for x in s] == ['S1', 'S2', 'S3'] and all(x['end_exclusive'] for x in s)
    assert s[0]['start'] == c['window']['start'] and s[-1]['end'] == c['window']['end']
    assert s[0]['end'] == s[1]['start'] == '2025-06-16T00:00:00Z' and s[1]['end'] == s[2]['start'] == '2026-02-16T00:00:00Z'
    assert c['slices']['account_reset_per_slice'] is True and 'code-defined' in c['slices']['source']


def test_fixed18_universe(c):
    u = c['universe']
    assert u['symbols'] == FIXED18 and len(set(u['symbols'])) == 18
    assert {x['symbol'] for x in u['expected_exclusions']} == {'HYPEUSDT', 'PUMPUSDT'}
    assert all(x['status'] == 'INCOMPLETE' and x['missing_4h_bars'] > 0 for x in u['expected_exclusions'])
    assert HEX64.fullmatch(u['static_universe_digest'])


def test_fixed18_is_core20_minus_incomplete_on_master(c):
    u = c['universe']
    assert _sha_lf(u['static_universe_file']) == u['static_universe_file_sha256_lf']
    with open(os.path.join(ROOT, u['static_universe_file']), encoding='utf-8') as fh:
        static = json.load(fh)
    assert static['universe_id'] == u['static_universe_id']
    assert [s for s in static['core_20'] if s not in ('HYPEUSDT', 'PUMPUSDT')] == u['symbols']


def test_data_pins(c):
    d = c['data']
    assert set(d['files']) == set(FIXED18) | {'HYPEUSDT', 'PUMPUSDT'}
    for s, f in d['files'].items():
        assert HEX64.fullmatch(f['sha256']) and f['dir'] in d['dirs'], s
        assert f['rows'] == (4504 if s in FIXED18 else f['rows']) and (s in FIXED18) == ('excluded' not in f)
    assert d['committed_manifest_on_master'] is None                             # an open question, not invented
    assert 'fapi.binance.com' in d['redownload_rule'] and 'INCONCLUSIVE' in d['redownload_rule']


def test_costs_legacy_assumptions(c):
    k = c['costs']
    assert (k['fee_taker_per_fill'], k['slippage_per_fill'], k['funding_per_bar']) == (0.0005, 0.0002, 0.00005)
    assert 'legacy' in k['exchange_floor'] and 'legacy' in k['funding_note']
    assert k['account'] == {'start_usd': 500.0, 'risk_per_trade': 0.01, 'max_positions': 4, 'max_leverage': 10.0,
                            'daily_halt': 0.08, 'share': 1.0, 'kelly': None}


# ------------------------------------------------------------------ cells / headline numbers
@pytest.mark.parametrize('cid', ['F01', 'F02'])
def test_cell_headline_numbers(c, cid):
    cell, h = c['cells'][cid], HEADLINE[cid]
    e = cell['expected']
    assert cell['strategy_key'] == h['key'] and cell['side'] == 'long'
    assert e['closed_trades'] == h['trades'] and e['symbols'] == 18
    assert math.isclose(e['net_r'], h['net_r'], abs_tol=1e-9) and math.isclose(e['pf'], h['pf'], abs_tol=1e-9)
    assert round(e['max_dd_r'], 2) == h['dd']
    assert math.isclose(e['mean_r'], e['net_r'] / e['closed_trades'], abs_tol=1e-6)
    assert e['raw_entry_signals'] == 228 and e['busted'] is False and e['liquidations'] == 0


@pytest.mark.parametrize('cid', ['F01', 'F02'])
def test_cell_packets_and_slices(c, cid):
    cell = c['cells'][cid]
    for p in (cell['full_packet'], cell['full_packet_equivalent']):
        assert HEX64.fullmatch(p['file_sha256']) and HEX64.fullmatch(p['packet_sha256'])
    assert cell['full_packet']['packet_sha256'] != cell['full_packet_equivalent']['packet_sha256']
    sl = cell['slices']
    assert list(sl) == ['S1', 'S2', 'S3']
    for x in sl.values():
        assert HEX64.fullmatch(x['file_sha256']) and HEX64.fullmatch(x['packet_sha256'])
        assert x['sign'] == 'positive' and x['net_r'] > 0 and x['pf'] > 1           # all 3 chronological slices positive
    assert sum(x['closed_trades'] for x in sl.values()) == cell['expected']['closed_trades']
    assert sum(x['raw_entry_signals'] for x in sl.values()) == cell['expected']['raw_entry_signals']
    assert math.isclose(sum(x['net_r'] for x in sl.values()), cell['expected']['net_r'], abs_tol=1e-5)


def test_cell_management_differs_only_by_target_and_time_cap(c):
    f1, f2 = c['cells']['F01'], c['cells']['F02']
    assert f1['mgmt'] == {'stop_atr': 2.5} and f2['mgmt'] == {'stop_atr': 2.5, 'tp_r': 3.0, 'max_bars': 180}
    assert f1['role'] == 'family primary'


# ------------------------------------------------------------------ overlap
def test_overlap_131_and_jaccard(c):
    o = c['overlap']
    n1, n2 = c['cells']['F01']['expected']['closed_trades'], c['cells']['F02']['expected']['closed_trades']
    assert o['pair'] == ['F01', 'F02'] and o['shared_exact_fills'] == 131 and o['jaccard'] == 0.655
    j = Fraction(o['shared_exact_fills'], n1 + n2 - o['shared_exact_fills'])
    assert j == Fraction(131, 200) and float(j) == o['jaccard']
    assert o['exact_fill_key'] == ['symbol', 't_in', 'side'] and 'PROPOSED' in o['exact_fill_definition_status']
    assert 'never two full independent risk budgets' in o['family_consequence']


# ------------------------------------------------------------------ semantics / tolerances / verdict
def test_semantics_cite_code_and_flag_code_defined(c):
    s = {k: v for k, v in c['semantics'].items() if not k.startswith('_')}
    for k in ('frame_and_warmup', 'indicators', 'entry_signal', 'signal_exit', 'pending_and_fill', 'sizing',
              'per_bar_management_order', 'intrabar_path', 'time_exit', 'exit_accounting', 'end_of_run', 'stats',
              'incomplete_rule'):
        assert k in s, k
    for k, v in s.items():
        assert v['code_defined'] is True and re.search(r'\.py:\w', v['source']), k
    assert 'stop-first' in s['intrabar_path']['rule'].lower() or 'Stop-first' in s['intrabar_path']['rule']
    assert '(1 + 0.0002)' in s['pending_and_fill']['fill_price']
    assert 'USD' in s['stats']['pf']


def test_tolerances(c):
    t = c['tolerances']
    assert 'exactly' in t['trade_set'] and '(symbol, t_in, side)' in t['trade_set']
    assert t['closed_trades'] == 'exact' and t['raw_entry_signals'] == 'exact'
    assert (t['per_trade_r_abs'], t['mean_r_abs']) == (1e-6, 1e-6)
    assert (t['pf_abs'], t['max_dd_r_abs'], t['max_dd_equity_abs']) == (1e-4, 1e-4, 1e-4)
    assert 'exact' in t['overlap'] and 'DIVERGED' in t['comparison_rule']


def test_verdict_schema(c):
    v = c['verdict_schema']
    assert v['values'] == ['REPRODUCED', 'DIVERGED', 'INCONCLUSIVE']
    assert 'every check is REPRODUCED' in v['overall'] and 'first divergent trade' in v['first_divergence']
    assert 'first_divergence' in v['output_fields']
    assert {'orderId', 'tradeId'} <= set(v['forbidden_output_fields'])


def test_forbidden_list_has_key_items(c):
    f = ' | '.join(c['forbidden'])
    for needle in ('importing strategies, backtest', 'runner modules', CUTOFF, 'public Binance klines',
                   'PAPER registration', 'runtime wiring', 'full-risk dual allocation', 'order ids or trade ids',
                   'mainnet', 'Build rerunning'):
        assert needle in f, needle


def test_open_questions_present(c, md):
    q = c['open_questions']
    assert [x['id'] for x in q] == [f'Q{i}' for i in range(1, len(q) + 1)] and len(q) >= 5
    assert all(x['topic'] and '?' in x['question'] for x in q)
    for x in q:
        assert x['id'] in md


def test_md_summarises_headline(c, md):
    for cid in ('F01', 'F02'):
        e = c['cells'][cid]['expected']
        assert f"| {e['closed_trades']} |" in md and c['cells'][cid]['strategy_key'] in md
    assert '131' in md and '0.655' in md and '184ec2e' in md
