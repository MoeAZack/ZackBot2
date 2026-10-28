"""breakout_pyramid_falsify.v1: DRAFT falsification contract invariants + identity parity with the live definition (no data)."""
import hashlib
import inspect
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import engine as EN                                                                         # noqa: E402
import strategies as ST                                                                     # noqa: E402

PREREG = ROOT / 'research_evidence' / 'prereg' / 'breakout_pyramid_falsify.v1.json'   # authoritative; tests never read the docs twin
CUTOFF = '2026-09-28T00:00:00Z'
MGMT_LAYERS = ('trail_atr', 'pyramid', 'be_r', 'tp1_r', 'tp1_frac', 'ttp', 'runner', 'dca', 'tps')


def c():
    return json.loads(PREREG.read_text(encoding='utf-8'))


def src_sha(obj):
    return hashlib.sha256(inspect.getsource(obj).replace('\r\n', '\n').encode()).hexdigest()


def test_draft_unsigned_not_evaluated_no_ledger():
    p = c()
    assert p['format'] == 'zb-falsify-prereg/1' and p['candidate_id'] == 'breakout_pyramid_falsify.v1'
    assert p['family'] == 'breakout_pyramid' and p['side'] == 'long' and '6468454634' in p['ruling']
    assert p['status'].startswith('DRAFT') and p['signed'] is False
    assert not (ROOT / 'research_evidence' / 'ledger' / 'breakout_pyramid_falsify.jsonl').exists()
    assert b'breakout_pyramid_falsify' not in (ROOT / 'research_evidence' / 'ledger' / 'REGISTRY.jsonl').read_bytes()
    assert p['boundaries'] == {'paper': False, 'runtime_change': False, 'holdout_read': False,
                               'mainnet': False, 'evaluation_run': False}


def test_identity_matches_live_definition():
    i = c()['identity']
    st = ST.STRATEGIES[i['registry_key']]
    assert st['fn'] is getattr(ST, i['fn']) and st['sides'] == i['registry_sides'] == 'long'
    assert st['style'] == i['style'] and st.get('tf', '4h') == i['tf'] == '4h' and not st.get('params')
    assert src_sha(ST.sig_breakout_pyramid) == i['fn_source_sha256']
    assert src_sha(ST.indicators) == i['indicators_source_sha256'] and src_sha(ST.ema) == i['ema_source_sha256']
    assert ST.merge_mgmt('breakout_pyramid', {}) == i['live_mgmt']


def test_pinned_params_reproduce_the_live_entry_causally_on_synthetic_bars():
    p = c()['params']
    rng = np.random.default_rng(7)
    cl = 100 * np.exp(np.cumsum(rng.normal(0.002, 0.02, 600)))
    df = pd.DataFrame({'t': np.arange(600), 'o': cl, 'h': cl * 1.01, 'l': cl * 0.99, 'c': cl, 'v': 1.0})
    sig = ST.sig_breakout_pyramid(ST.indicators(df), {}, {})
    n, f, s = p['channel_bars'], p['ema_fast'], p['ema_slow']
    want = (df.c > df.h.rolling(n).max().shift()) & (ST.ema(df.c, f) > ST.ema(df.c, s))
    assert sig['le'].equals(want) and want.sum() >= 5
    wrong = (df.c > df.h.rolling(n + 5).max().shift()) & (ST.ema(df.c, f) > ST.ema(df.c, s))
    assert not sig['le'].equals(wrong)                                                  # the check can tell channels apart
    assert not sig['lx'].any() and p['searched'] is False
    k = int(np.flatnonzero(want.to_numpy())[-1])                                        # causal: bar t never sees bar t+1
    cut = ST.sig_breakout_pyramid(ST.indicators(df.iloc[:k + 1].reset_index(drop=True)), {}, {})
    assert bool(cut['le'].iloc[-1]) is True


def test_fills_are_next_open():
    fl = c()['fills']
    assert fl['causal'] is True and 'CLOSED 4h bar t' in fl['signal']
    assert 'open of bar t+1' in fl['entry_fill'] and "never at the signal bar's close" in fl['entry_fill']
    assert 'bar t+1' not in fl['signal'] and 'shifted one bar' in fl['signal']


def test_universe_is_fixed18_minus_lsk():
    u = c()['universe']
    assert 'LSKUSDT' in EN.CAND_FIXED18 and len(EN.CAND_FIXED18) == 18
    assert u['symbols'] == sorted(set(EN.CAND_FIXED18) - {'LSKUSDT'}) and len(u['symbols']) == u['size'] == 17
    assert hashlib.sha256(json.dumps(sorted(u['symbols']), separators=(',', ':')).encode()).hexdigest() == u['symbols_sha256']
    p = c()
    art = ROOT / 'research_evidence' / 'universe' / 'static-top40-minhist365-contract-20261011.json'
    assert hashlib.sha256(art.read_bytes()).hexdigest() == p['source']['universe_sha256']
    assert set(u['symbols']) <= {x['symbol'] for x in json.loads(art.read_text(encoding='utf-8'))['symbols']}
    assert 'report' in u['report_only'] and 'selection' in u['selection_bias']


def test_three_slices_before_sealed_cutoff_and_no_holdout():
    p = c()
    w = p['windows']
    sl = w['slices']
    assert [x['slice'] for x in sl] == [1, 2, 3] and w['calibration']['end'] == w['development']['start'] == sl[0]['start']
    assert all(a['end'] == b['start'] for a, b in zip(sl, sl[1:]))
    assert sl[-1]['end'] == w['development']['end'] == w['sealed_cutoff'] == CUTOFF and w['development']['end_exclusive']
    assert all(x['start'] < x['end'] <= CUTOFF for x in sl)
    assert w['holdout']['status'].startswith('SEALED') and 'start' not in w['holdout']
    assert 'does not open or schedule any holdout' in p['deferred']['holdout']
    g = p['gates']['slices']
    assert g == {'positive_slices_min': 3, 'slices': 3, 'rule': g['rule']} and 'never gated' in w['report_only_folds']
    assert 'fold_stability' not in p['gates']


def test_entry_only_gate_precedes_trail_and_isolated_pyramid_credit():
    p = c()
    s, h = p['stages'], p['hypotheses']
    assert (h['H1']['stage'], h['H2']['stage'], h['H3']['stage']) == ('S1', 'S2', 'S3')
    assert h['H2']['requires'] == 'H1 PASS' and h['H3']['requires'] == 'H1 PASS and H2 PASS'
    assert not set(s['S1']['mgmt']) & set(MGMT_LAYERS)                                  # entry-only, plain stop
    assert s['S1']['mgmt']['stop_atr'] == p['identity']['live_mgmt']['stop_atr']
    assert s['S3']['mgmt'] == p['identity']['live_mgmt'] == ST.merge_mgmt('breakout_pyramid', {})   # live default, no cap
    assert s['S2']['mgmt'] == {k: v for k, v in s['S3']['mgmt'].items() if k != 'pyramid'}         # differ only by adds
    assert 'max_bars' not in s['S2']['mgmt'] and 'max_bars' not in s['S3']['mgmt']
    assert {k for k in s if k.startswith('S') and s[k]['gated']} == {'S1', 'S2', 'S3'} and s['S1-T']['gated'] is False
    assert 'netR_S3(e) - netR_S2(e)' in p['episodes']['paired_increment']
    assert 'base' in h['H2']['test'] and 'x1.5' in h['H2']['test'] and 'all 3 slices' in h['H2']['test']
    assert 'D3 = S3 - S2' in h['H3']['test'] and 'FALSIFIED' in h['order']
    k = p['kill']
    assert all('FALSIFIED' in k[x] and k[x].startswith('S1 ') for x in ('K1', 'K2', 'K3', 'K4', 'K5'))
    assert 'before any S2 or S3 number' in k['precedence']


def test_last_entry_cutoff_and_fail_closed_boundary():
    from datetime import datetime, timedelta, timezone
    w = c()['windows']
    lc = w['last_entry_cutoff']
    sealed = datetime.strptime(w['sealed_cutoff'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    at = sealed - timedelta(hours=lc['bars'] * lc['bar_hours'])
    assert (lc['bars'], lc['bar_hours']) == (180, 4) and at.strftime('%Y-%m-%dT%H:%M:%SZ') == lc['at'] == '2026-08-29T00:00:00Z'
    assert ((sealed - at).total_seconds() / (4 * 3600)) == 180 and at.hour % 4 == 0      # on the 4h grid
    b = w['boundary_rule']
    assert 'INCOMPLETE' in b and 'excluded from every gate statistic' in b and 'counted' in b and 'no price at or after' in b


def test_unit_replay_and_book_are_separate_and_declared():
    p = c()
    e, bk = p['episodes'], p['book']
    assert 'independent paired analysis' in e['unit_replay'] and 'overlap is allowed here and only here' in e['unit_replay']
    assert 'never fed into the book' in e['never_book'] and 'never from E' in bk['source']
    assert 'one open episode per symbol' in bk['per_symbol'] and 'skipped and counted' in bk['per_symbol']
    assert 'ascending timestamp' in bk['ordering'] and 'exits first, then adds, then new entries' in bk['ordering']
    assert 'symbol name ascending' in bk['ordering']
    assert 'open lots < 4' in bk['admission'] and '3x equity' in bk['admission'] and 'never scaled down' in bk['admission']


def test_sizing_is_report_only_at_fixed_risks():
    p = c()
    assert 'sizing' not in p['gates'] and 'sizing' not in p['kill']['K8']
    sz = p['sizing']
    assert sz['status'].startswith('REPORT-ONLY') and sz['risk_per_trade'] == [0.01, 0.005, 0.0025]
    assert 'PAPER admission contract' in sz['rule'] and 'PAPER admission contract' in p['deferred']['deployment_risk_budget']


def test_finite_budget():
    p = c()
    tb = p['trial_budget']
    assert tb['gated_variants'] == len(tb['gated_ids']) == 3 and tb['diagnostic_variants'] == len(tb['diagnostic_ids']) == 1
    assert set(tb['gated_ids']) | set(tb['diagnostic_ids']) == {k for k in p['stages'] if k.startswith('S')}
    assert tb['baseline_rows'] == sum(len(b['for']) for b in p['baselines']) == 2
    assert tb['total_ledger_trial_records'] == tb['gated_variants'] + tb['diagnostic_variants'] + tb['baseline_rows'] == 6
    assert 'fixed sequence H1 -> H2 -> H3' in tb['multiplicity']
    assert p['bootstrap']['seed'] == 20261028 and p['bootstrap']['method'].endswith('10000 resamples')


def test_costs_activity_and_roadmap_thresholds():
    p = c()
    co, g = p['costs'], p['gates']
    assert co['gated_rows'] == ['base_x1', 'all_variable_costs_x1.5'] and set(co['gated_rows']) <= set(co['stress_rows'])
    assert '+ funding_actual' in co['net_formula'] and 'flat per-bar funding is never used' in co['funding']
    assert (g['stress']['base_mean_net_r_gt'], g['stress']['x1_5_mean_net_r_gt']) == (0.0, 0.0)
    a = g['activity']
    assert (a['min_episodes_total'], a['min_episodes_per_slice'], a['min_episodes_per_30d_each_slice']) == (300, 75, 2.0)
    assert (a['min_active_symbols_total'], a['min_active_symbols_per_slice']) == (15, 8)
    assert a['min_episodes_per_slice'] * 3 <= a['min_episodes_total'] and a['min_active_symbols_total'] <= 17
    dd = g['drawdown_and_tail']
    assert dd['tiers'] == {'ordinary': {'base_max_equity_dd_le': 0.35, 'closed_r_dd_lt': 40},
                           'profitability_rescue': {'base_max_equity_dd_le': 0.55,
                                                    'annualized_net_return_over_max_dd_ge': 0.75,
                                                    'total_net_r_over_closed_r_dd_ge': 2.0}}
    assert dd['longest_underwater_days_le'] == 365 and 'HIGH-RISK SLEEVE' in dd['high_risk_sleeve']
    assert (g['concentration']['max_symbol_share'], g['concentration']['max_top3_share']) == (0.25, 0.50)
    assert g['expectancy'] == {'mean_net_r_gt': 0.0, 'lcb_gt': 0.0} and 'remaining 16' in g['remove_best_symbol']


def test_sources_match_edge05_and_evaluation_deferred():
    p = c()
    e5 = json.loads((ROOT / 'research_evidence' / 'prereg' / 'funding_carry_xsec.v1.json').read_text(encoding='utf-8'))
    for k in ('universe_sha256', 'costs_sha256', 'manifest', 'manifest_digest'):
        assert p['source'][k] == e5['source'][k]
    assert hashlib.sha256((ROOT / 'tools' / 'research' / 'costs.py').read_bytes()).hexdigest() == p['source']['costs_sha256']
    assert 'Integration authorization' in p['deferred']['evaluation_run'] and 'EDGE04' in p['deferred']['evaluation_run']
