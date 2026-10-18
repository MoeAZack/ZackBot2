"""Wave-2 USDT/USDC fee-rescue batch runner: pinned fail-closed preflight, INCOMPLETE-only refusals, write-once /
hash-bound resume, side override, controls, fee scenarios and summary invariants - on tiny synthetic packs with an
injected expected-sha table (no real data)."""
import copy
import hashlib
import json
import os
import shutil
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = os.path.join(ROOT, 'tools', 'research')
if R not in sys.path:
    sys.path.insert(0, R)
import run_quote_pair_wave2 as W  # noqa: E402

Q = W.Q
REAL_EXPECTED = copy.deepcopy(W.EXPECTED)          # before any fixture injects a synthetic table
H = Q.HOUR
M15 = W.TFS['15m']['tf_ms']
M4H = W.TFS['4h']['tf_ms']
WIN = ('2026-09-01T00:00:00Z', '2026-09-10T00:00:00Z', '2026-09-28T00:00:00Z')   # tiny test window (warm, start, end)
FEES = [
    {'id': 'equal_5bp', 'maker_bps': {'USDC': 5.0, 'USDT': 5.0}, 'status': 'COMPLETE',
     'taker_bps': {'USDC': 5.0, 'USDT': 5.0}},
    {'effective_from': '2025-12-10T08:00:00Z', 'id': 'advertised_regular_user', 'maker_bps': {'USDC': 0.0, 'USDT': 2.0},
     'source_url': 'https://www.binance.com/en/support/announcement/detail/03180947641b414ebcb9a6c407fe80e2',
     'status': 'PROVISIONAL_CURRENT_SCHEDULE', 'taker_bps': {'USDC': 4.0, 'USDT': 5.0}, 'valid_until': 'until further notice'},
    {'id': 'promotion_expiry_stress', 'maker_bps': {'USDC': None, 'USDT': 2.0},
     'missing': 'official standard non-promotional USDC schedule', 'status': 'INCOMPLETE',
     'taker_bps': {'USDC': None, 'USDT': 5.0}},
]
CANDS = [{'strategy': 'exp_scalp', 'side': 'long', 'tf': '15m', 'tier': 'A_TEST', 'evidence': {'trades': 1}},
         {'strategy': 'pullback_rsi', 'side': 'short', 'tf': '4h', 'tier': 'B_TEST', 'evidence': {'trades': 1},
          'registry_side_override_required': True}]
CTRLS = [{'strategy': 'exp_trend', 'side': 'short', 'tf': '4h'}, {'strategy': 'dca_dip', 'side': 'long', 'tf': '1h'}]


def _set_window(mp):
    w, s, e = WIN
    for name, v in (('WARM_S', w), ('START_S', s), ('END_S', e)):
        mp.setattr(Q, name, v)
    for name, v in (('WARM', w), ('START', s), ('END', e)):
        mp.setattr(Q, name, pd.Timestamp(v).value // 1_000_000)


def sha(path):
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()


def _csv(path, df):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return {'file': path.name, 'sha256': sha(path), 'bytes': path.stat().st_size}


def _ohlc(t, j, quote, step_vol, seed):
    rng = np.random.default_rng(seed + j)
    c = 100 * (1 + j) * np.exp(np.cumsum(rng.normal(0, step_vol, len(t)))) * (1 if quote == 'USDT' else 1.0003)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({'t': t, 'o': o, 'h': np.maximum(o, c) * 1.002, 'l': np.minimum(o, c) * .998, 'c': c,
                         'v': 10.0 if quote == 'USDT' else 11.0, 'qv': 1000.0})


def build(root, *, mutate=None, cand_edit=None, combined_edit=None):
    """A complete synthetic wave-2 input set under root. `mutate(kind, quote, sym, df)` edits a frame before it is
    written (manifests stay self-consistent); the returned EXPECTED table pins the result."""
    mut = mutate or (lambda kind, quote, sym, df: df)
    t4 = np.arange(Q.WARM, Q.END, M4H, dtype=np.int64)
    t15 = np.arange(Q.WARM, Q.END, M15, dtype=np.int64)
    combined = {'authority': 'RESEARCH_ONLY', 'contract': W.W15_CONTRACT, 'format': W.W15_FORMAT, 'pairs': {},
                'problems': [], 'quotes': {}, 'status': 'complete',
                'window': {'development_start': Q.START_S, 'end_exclusive': Q.END_S, 'expected_rows_per_symbol': len(t15),
                           'fetch_start': Q.WARM_S, 'interval': '15m', 'interval_ms': M15}}
    paths = {}
    for quote in Q.QUOTES:
        kfiles, afiles, rules, f15, c15 = {}, {}, {}, {}, {}
        d4, da, d15 = root / '4h' / quote, root / 'acc' / quote, root / 'w15' / quote
        for j, base in enumerate(Q.BASES):
            sym = base + quote
            kfiles[sym] = dict(_csv(d4 / f'{sym}_4h.csv', mut('klines4h', quote, sym, _ohlc(t4, j, quote, .01, 7))),
                               status='complete', validation={'status': 'complete'}, rows=len(t4))
            r15 = _csv(d15 / f'{sym}_15m.csv', mut('klines15', quote, sym, _ohlc(t15, j, quote, .003, 11)))
            f15[sym] = dict(r15, status='complete', rows=len(t15), expected=len(t15))
            c15[sym] = {'path': f'{quote}/{sym}_15m.csv', 'rows': len(t15), 'sha256': r15['sha256']}
            ft = np.arange(Q.WARM, Q.END, 8 * H, dtype=np.int64)
            f = pd.DataFrame({'fundingTime': ft, 'fundingRate': 0.0001 if quote == 'USDT' else 0.00005,
                              'markPrice': 100.0 + j})
            mt = np.arange(Q.WARM, Q.END, H, dtype=np.int64)
            mpx = 100.0 + j + (0 if quote == 'USDT' else .01)
            mk = pd.DataFrame({'open_time': mt, 'open': mpx, 'high': mpx + 1, 'low': mpx - 1, 'close': mpx,
                               'close_time': mt + H - 1})
            for series, df in (('fundingRate', f), ('markPriceKlines_1h', mk)):
                afiles[f'{sym}/{series}'] = dict(_csv(da / f'{sym}_{series}.csv', mut(series, quote, sym, df)),
                                                 symbol=sym, series=series, status='complete')
            rules[sym] = {'status': 'TRADING', 'contractType': 'PERPETUAL', 'baseAsset': base, 'quoteAsset': quote,
                          'filters': {'PRICE_FILTER': {'tickSize': '0.01'}, 'LOT_SIZE': {'stepSize': '0.001'},
                                      'MARKET_LOT_SIZE': {'stepSize': '0.001'}, 'MIN_NOTIONAL': {'notional': '5'}}}
        km = {'format': Q.KLINES_FORMAT, 'contract': Q.CONTRACT, 'quote': quote, 'interval': '4h', 'incomplete': [],
              'stopped': None, 'fetch': {'start': Q.WARM_S},
              'window': {'start': Q.START_S, 'end': Q.END_S, 'end_exclusive': True}, 'files': kfiles,
              'pairs': {b + quote: {'base': b, 'usdt_twin': b + 'USDT'} for b in Q.BASES}}
        am = {'format': Q.ACCOUNTING_FORMAT, 'quote': quote, 'symbols': [b + quote for b in Q.BASES], 'incomplete': [],
              'stopped': None, 'files': afiles,
              'window': {'warmup_start': Q.WARM_S, 'development_start': Q.START_S, 'end_exclusive': Q.END_S},
              'exchange_rules': {'label': 'CURRENT_SNAPSHOT_ONLY', 'causality': 'NON_CAUSAL', 'symbols': rules}}
        m15 = {'format': W.KLINES15_FORMAT, 'interval': '15m', 'incomplete': [], 'stopped': None, 'files': f15,
               'fetch': {'start': Q.WARM_S, 'end_exclusive': Q.END_S},
               'window': {'start': Q.START_S, 'end': Q.END_S, 'end_exclusive': True}}
        (d4 / 'klines.json').write_text(json.dumps(km), encoding='utf-8')
        (da / 'accounting.json').write_text(json.dumps(am), encoding='utf-8')
        (d15 / 'MANIFEST.15m.json').write_text(json.dumps(m15), encoding='utf-8')
        combined['quotes'][quote] = {'collector_format': W.KLINES15_FORMAT, 'collector_incomplete': [], 'files': c15,
                                     'manifest': f'{quote}/MANIFEST.15m.json',
                                     'manifest_sha256': sha(d15 / 'MANIFEST.15m.json')}
        paths[quote] = (d4 / 'klines.json', da / 'accounting.json')
    for b in Q.BASES:
        combined['pairs'][b] = {'exact_timestamp_match': True, 'timestamp_only_usdc': 0, 'timestamp_only_usdt': 0,
                                'usdc_rows': len(t15), 'usdt_rows': len(t15)}
    if combined_edit:
        combined_edit(combined)
    cpath = root / 'w15' / 'MANIFEST.wave2-quote-pair-15m.json'
    cpath.write_text(json.dumps(combined), encoding='utf-8')
    reg = sha(os.path.join(ROOT, 'strategies.py'))
    cm = {'authority': 'RESEARCH_ONLY; NO_EXECUTION_OR_PROMOTION_AUTHORITY', 'bases': list(Q.BASES),
          'candidates': copy.deepcopy(CANDS), 'controls_negative_gross_only': copy.deepcopy(CTRLS),
          'data': {'15m': {'combined_manifest': str(cpath), 'combined_manifest_sha256': sha(cpath), 'status': 'COMPLETE'},
                   '4h': {'USDT_manifest': str(paths['USDT'][0]), 'USDT_manifest_sha256': sha(paths['USDT'][0]),
                          'USDC_manifest': str(paths['USDC'][0]), 'USDC_manifest_sha256': sha(paths['USDC'][0]),
                          'status': 'COMPLETE'}},
          'fee_scenarios': [{'definition': d, 'definition_sha256': W.fee_definition_sha(d)} for d in copy.deepcopy(FEES)],
          'format': W.CANDIDATES_FORMAT, 'freeze': {}, 'selection': {},
          'strategy_registry': {'path': 'strategies.py', 'sha256': reg},
          'window': {'development_start': Q.START_S, 'end_exclusive': Q.END_S, 'fetch_start': Q.WARM_S}}
    if cand_edit:
        cand_edit(cm)
    (root / 'cand.json').write_text(json.dumps(cm), encoding='utf-8')
    args = dict(candidates=str(root / 'cand.json'), wave2_15m=str(cpath), usdt_4h=str(paths['USDT'][0]),
                usdc_4h=str(paths['USDC'][0]), usdt_accounting=str(paths['USDT'][1]),
                usdc_accounting=str(paths['USDC'][1]))
    expected = {'candidates': sha(root / 'cand.json'), 'wave2_15m': sha(cpath), 'usdt_4h': sha(paths['USDT'][0]),
                'usdc_4h': sha(paths['USDC'][0]), 'usdt_accounting': sha(paths['USDT'][1]),
                'usdc_accounting': sha(paths['USDC'][1]), 'strategy_registry': reg,
                'fee': {d['id']: W.fee_definition_sha(d) for d in FEES}}
    return args, expected


GIT = {'head': 'c0ffee' * 6 + 'c0ff', 'tree': 'beef' * 10, 'clean': True}


def _patch(mp, expected):
    _set_window(mp)
    mp.setattr(W, 'EXPECTED', expected)
    mp.setattr(W, 'git_state', lambda repo: dict(GIT))


@pytest.fixture(scope='module')
def done(tmp_path_factory):
    """One complete batch (shared by the invariant tests), with every BT.run call recorded."""
    mp = pytest.MonkeyPatch()
    _set_window(mp)
    root = tmp_path_factory.mktemp('w2')
    args, expected = build(root / 'in')
    _patch(mp, expected)
    calls, orig = [], Q.BT.run

    def spy(book, sleeves, **kw):
        calls.append({'syms': list(book.syms), 'sleeves': copy.deepcopy(sleeves),
                      **{k: kw[k] for k in ('t0', 't1', 'warmup', 'start', 'max_lev', 'daily_halt', 'fund_per_bar')}})
        return orig(book, sleeves, **kw)
    mp.setattr(Q.BT, 'run', spy)
    code, path = W.run(**args, out_dir=str(root / 'out1'))
    mp.setattr(Q.BT, 'run', orig)
    yield {'mp': mp, 'root': root, 'args': args, 'expected': expected, 'code': code, 'path': path, 'calls': calls,
           'summary': json.loads(open(path, encoding='utf-8').read()) if code == 0 else None}
    mp.undo()


def _files(d):
    return sorted(p for p in os.listdir(d))


def _incomplete_only(out_dir):
    names = _files(out_dir)
    assert names and all(n.startswith('INCOMPLETE.') and n.endswith('.json') for n in names), names
    rec = json.loads(open(os.path.join(out_dir, names[-1]), encoding='utf-8').read())
    assert rec['status'] == 'INCOMPLETE' and rec['verdict'] is None and rec['usdc_better'] == W.USDC_BETTER
    assert rec['non_promotable'] is True
    return rec


# ------------------------------------------------------------------ the complete run
def test_a_complete_run_writes_the_versioned_summary_with_its_invariants(done):
    assert done['code'] == 0, open(done['path'], encoding='utf-8').read()
    s = done['summary']
    assert s['format'] == 'zb-quote-pair-wave2-report/1' and s['verdict'] is None
    assert s['usdc_better'] == 'USDC BETTER: UNAVAILABLE' and s['non_promotable'] is True
    assert s['routing_or_config_changes'] is None and s['window']['holdout_read'] is False
    assert set(s['cells']) == {'candidate.exp_scalp.long.15m', 'candidate.pullback_rsi.short.4h',
                               'control.exp_trend.short.4h'}
    assert s['pins']['git'] == GIT and set(s['pins']['code']) == set(W.CODE_FILES)
    assert s['pins']['candidates'] == done['expected']['candidates'] and s['pins']['fee_scenarios'] == done['expected']['fee']
    for c in s['cells'].values():
        for sid, row in c['scenarios'].items():
            for q, x in row['quotes'].items():
                if x['status'] != 'complete':
                    continue
                m = x['metrics']
                for k in ('gross_pnl', 'fees', 'adverse_slippage', 'funding', 'net_pnl', 'net_r_sum', 'gross_r_sum',
                          'closed_trades', 'open_trades', 'turnover', 'expectancy_r', 'hold_hours_mean'):
                    assert k in m, (sid, q, k)
                assert {r['label'] for r in x['spread_rows'].values()} == {'SPREAD-UNCALIBRATED'}
                assert set(x['spread_rows']) == {'0', '1', '2', '5'}
        assert set(c['exchange_filter_refusals']) == set(Q.QUOTES) and 'agreement' in c
    exp = s['cells']['candidate.exp_scalp.long.15m']['scenarios']['equal_5bp']
    assert exp['quotes']['USDT']['metrics']['closed_trades'] > 0, 'fixture must trade on 15m'
    assert exp['paired']['n_paired'] > 0 and 'sufficiency' in exp['paired'] and 'by_quarter' in exp['paired']['metrics']


def test_slip_lookback_is_15_closed_bars_of_the_cells_own_tf_in_bars_and_seconds(done):
    s = done['summary']
    assert s['slip']['15m'] == dict(s['slip']['15m'], id='slip-v1@15m', lookback_bars=15, lookback_seconds=13500)
    assert s['slip']['4h'] == dict(s['slip']['4h'], id='slip-v1@4h', lookback_bars=15, lookback_seconds=216000)
    for name, c in s['cells'].items():
        tf = c['tf']
        want = {'15m': ('slip-v1@15m', 13500), '4h': ('slip-v1@4h', 216000)}[tf]
        for row in [c] + list(c['scenarios'].values()):
            assert (row['slip']['id'], row['slip']['lookback_bars'], row['slip']['lookback_seconds']) == (want[0], 15, want[1])


def test_slip_bars_are_one_named_constant_per_tf(monkeypatch):
    monkeypatch.setattr(W, 'TFS', dict(W.TFS, **{'15m': dict(W.TFS['15m'], slip_bars=20)}))
    assert W.slip_label('15m')['lookback_bars'] == 20 and W.slip_label('15m')['lookback_seconds'] == 18000


def test_fee_scenarios_are_exactly_the_manifests_and_promotion_expiry_is_incomplete(done):
    s = done['summary']
    assert {k: v['status'] for k, v in s['fee_scenarios'].items()} == W.FEE_STATUS
    for c in s['cells'].values():
        sc = c['scenarios']
        assert sc['promotion_expiry_stress']['quotes']['USDC'] == {'status': 'INCOMPLETE',
                                                                   'reason': 'no USDC taker rate in promotion_expiry_stress'}
        assert sc['promotion_expiry_stress']['paired'] is None and 'never a comparison' in sc['promotion_expiry_stress']['use']
        assert 'PROVISIONAL' in sc['advertised_regular_user']['use']
        assert sc['advertised_regular_user']['quotes']['USDC']['taker_bps'] == 4.0
        assert sc['equal_5bp']['quotes']['USDC']['taker_bps'] == 5.0


def test_fees_follow_each_scenarios_taker_rate_per_quote(done):
    c = done['summary']['cells']['candidate.exp_scalp.long.15m']['scenarios']
    eq, adv = c['equal_5bp']['quotes'], c['advertised_regular_user']['quotes']
    assert adv['USDT']['metrics'] == eq['USDT']['metrics']                       # USDT 5 bp in both
    f5, f4 = eq['USDC']['metrics']['fees'], adv['USDC']['metrics']['fees']
    assert f4 == pytest.approx(f5 * 4 / 5, rel=1e-6)                              # same fills, rate only
    assert adv['USDC']['metrics']['gross_pnl'] == eq['USDC']['metrics']['gross_pnl']


def test_usdt_and_usdc_use_identical_config_and_window(done):
    calls = done['calls']
    assert len(calls) == 2 * 3
    for a, b in zip(calls[0::2], calls[1::2]):                                  # (USDT, USDC) per cell
        assert a['syms'][0] == 'BTCUSDT' and b['syms'][0] == 'BTCUSDC'
        strip = lambda c: [dict(x, symbols=[s[:-4] for s in x['symbols']]) for x in c['sleeves']]
        assert strip(a) == strip(b)
        assert {k: a[k] for k in a if k not in ('syms', 'sleeves')} == {k: b[k] for k in b if k not in ('syms', 'sleeves')}
    fpb = {c['sleeves'][0]['key']: c['fund_per_bar'] for c in calls}
    assert fpb['exp_scalp'] == pytest.approx(Q.BT.FUND_PER_BAR * 900 / 14400)
    assert fpb['exp_trend'] == pytest.approx(Q.BT.FUND_PER_BAR)


def test_the_manifest_side_override_is_honoured(done):
    pr = [c for c in done['calls'] if c['sleeves'][0]['key'] == 'pullback_rsi']
    assert pr and all(c['sleeves'][0]['sides'] == 'short' for c in pr)
    assert W.S.STRATEGIES['pullback_rsi']['sides'] == 'long'                     # registry untouched
    cell = done['summary']['cells']['candidate.pullback_rsi.short.4h']
    assert cell['side_override'] is True and cell['registry_sides'] == 'long'
    det = json.loads(open(os.path.join(os.path.dirname(done['path']), 'cells', 'candidate.pullback_rsi.short.4h.json'),
                          encoding='utf-8').read())['content']
    for row in det['scenarios'].values():
        for x in row['quotes'].values():
            assert all(e['side'] == 'short' and e['episode_key'][1] == 'short' for e in x.get('episodes', []))


def test_a_short_override_trades_only_short(tmp_path, monkeypatch):
    _set_window(monkeypatch)
    args, expected = build(tmp_path)
    _patch(monkeypatch, expected)
    _, _, _, datas, _ = W.preflight(**args)
    cell = Q.run_cell(datas['4h']['USDT'], 'USDT', 'pullback_rsi', 'short')
    assert all(f['side'] == -1 for f in cell['fills'])
    assert (cell['trades'].side == -1).all() if len(cell['trades']) else True
    assert sum(len(v) for v in cell['signals'].values()) > 0, 'fixture must emit short signals'


def test_controls_are_labelled_control_never_rescued(done):
    s = done['summary']
    ctrl = s['cells']['control.exp_trend.short.4h']
    assert ctrl['role'] == 'control' and 'tier' not in ctrl
    assert 'rescued' not in json.dumps(ctrl).lower()
    assert all(c['role'] == 'candidate' for n, c in s['cells'].items() if n.startswith('candidate.'))
    assert s['controls_not_run'] == [{'name': 'control.dca_dip.long.1h', 'role': 'control', 'strategy': 'dca_dip',
                                      'side': 'long', 'tf': '1h', 'status': W.UNROUTED_CONTROL_STATUS}]
    assert s['controls_incomplete'] is True                    # the hole is flagged at the top level, never silent
    assert s['unrouted_control_count'] == 1 and s['packet_status'] == 'INCOMPLETE'
    assert s['global_verdict_available'] is False and s['verdict'] is None and s['non_promotable'] is True


def test_filter_basis_and_resume_binding_are_stated(done):
    s = done['summary']
    assert s['exchange_filter_basis'] == W.FILTER_BASIS and 'not a deliberate' in s['resume_binding']


def test_every_imported_simulation_module_is_pinned():
    for f in ('feasibility.py', 'tools/research/costs.py', 'tools/research/run_breadth_candidate.py',
              'tools/research/run_vwaprev_candidate.py', 'backtest.py'):
        assert f in W.CODE_FILES


def test_paired_rows_use_the_exact_episode_key(done):
    d = os.path.join(os.path.dirname(done['path']), 'cells', 'candidate.exp_scalp.long.15m.json')
    p = json.loads(open(d, encoding='utf-8').read())['content']['scenarios']['equal_5bp']['paired']
    assert p['episodes'] and all(len(e['episode_key']) == 4 and e['episode_key'][:2] == ['exp_scalp', 'long']
                                 for e in p['episodes'])
    assert all(e['delta_R'] == pytest.approx(e['usdc_R'] - e['usdt_R']) for e in p['episodes'])


# ------------------------------------------------------------------ determinism / overwrite / resume
def test_a_rerun_is_byte_identical_except_the_run_record(done):
    out2 = done['root'] / 'out2'
    code, path = W.run(**done['args'], out_dir=str(out2))
    assert code == 0
    d1, d2 = os.path.dirname(done['path']), os.path.dirname(path)
    assert os.path.basename(d1) == os.path.basename(d2)                          # same pins -> same run id
    for rel in ['preflight.json'] + [os.path.join('cells', n) for n in _files(os.path.join(d1, 'cells'))]:
        assert open(os.path.join(d1, rel), 'rb').read() == open(os.path.join(d2, rel), 'rb').read(), rel
    s1, s2 = done['summary'], json.loads(open(path, encoding='utf-8').read())
    assert s1['variable_fields'] == ['run_record']
    assert {k: v for k, v in s1.items() if k != 'run_record'} == {k: v for k, v in s2.items() if k != 'run_record'}


def test_an_existing_completed_run_is_never_overwritten(done):
    before = open(done['path'], 'rb').read()
    code, path = W.run(**done['args'], out_dir=str(done['root'] / 'out1'))
    assert code == 2 and os.path.basename(path).startswith('INCOMPLETE.')
    rec = json.loads(open(path, encoding='utf-8').read())
    assert rec['stage'] == 'output' and 'never overwritten' in rec['reason']
    assert open(done['path'], 'rb').read() == before


def test_resume_reuses_only_hash_bound_completed_cells(done, monkeypatch):
    src = os.path.dirname(os.path.dirname(done['path']))
    out = done['root'] / 'out_resume'
    shutil.copytree(src, out)
    rid = os.path.basename(os.path.dirname(done['path']))
    os.remove(out / rid / 'summary.json')
    os.remove(out / rid / 'cells' / 'control.exp_trend.short.4h.json')         # interrupted before this cell
    computed = []
    orig = W.compute_cell
    monkeypatch.setattr(W, 'compute_cell', lambda spec, *a: computed.append(spec['name']) or orig(spec, *a))
    code, path = W.run(**done['args'], out_dir=str(out))
    assert code == 0 and computed == ['control.exp_trend.short.4h']
    s = json.loads(open(path, encoding='utf-8').read())
    assert s['run_record']['resumed_cells'] == 2
    assert {k: v for k, v in s.items() if k != 'run_record'} == {k: v for k, v in done['summary'].items()
                                                                 if k != 'run_record'}


def test_a_tampered_completed_cell_is_refused_never_trusted(done, monkeypatch):
    src = os.path.dirname(os.path.dirname(done['path']))
    out = done['root'] / 'out_tamper'
    shutil.copytree(src, out)
    rid = os.path.basename(os.path.dirname(done['path']))
    os.remove(out / rid / 'summary.json')
    cp = out / rid / 'cells' / 'candidate.exp_scalp.long.15m.json'
    env = json.loads(cp.read_text(encoding='utf-8'))
    env['content']['scenarios']['equal_5bp']['quotes']['USDC']['metrics']['net_r_sum'] += 5.0
    cp.write_text(json.dumps(env), encoding='utf-8')
    monkeypatch.setattr(W, 'compute_cell', lambda *a: pytest.fail('a tampered cell must not be silently recomputed'))
    code, path = W.run(**done['args'], out_dir=str(out))
    assert code == 2 and not (out / rid / 'summary.json').exists()
    rec = json.loads(open(path, encoding='utf-8').read())
    assert 'hash binding' in rec['reason'] and rec['stage'] == 'simulation'
    # a foreign cell (other preflight) is refused the same way
    env2 = W.envelope(dict(env['content'], preflight_sha256='0' * 64))
    cp.write_text(json.dumps(env2), encoding='utf-8')
    assert W.run(**done['args'], out_dir=str(out))[0] == 2


# ------------------------------------------------------------------ fail-closed preflight
def _drop(kind_want, quote_want='USDC', sym_want=None, i=50):
    def mut(kind, quote, sym, df):
        if kind == kind_want and quote == quote_want and (sym_want is None or sym == sym_want):
            return df.drop(index=i).reset_index(drop=True)
        return df
    return mut


def _bad_fee(m):
    m['fee_scenarios'][0]['definition']['taker_bps']['USDC'] = 4.0
    m['fee_scenarios'][0]['definition_sha256'] = W.fee_definition_sha(m['fee_scenarios'][0]['definition'])


def _fee_status(m):
    m['fee_scenarios'][2]['definition']['status'] = 'COMPLETE'
    m['fee_scenarios'][2]['definition_sha256'] = W.fee_definition_sha(m['fee_scenarios'][2]['definition'])


CASES = {
    'missing 15m bar': (dict(mutate=_drop('klines15', sym_want='ETHUSDC')), None, 'INCOMPLETE'),
    'missing 4h bar': (dict(mutate=_drop('klines4h', 'USDT', 'SOLUSDT')), None, 'INCOMPLETE'),
    'missing funding row': (dict(mutate=_drop('fundingRate', 'USDT', 'BTCUSDT', 5)), None, 'funding'),
    'missing mark row': (dict(mutate=_drop('markPriceKlines_1h', 'USDC', 'XRPUSDC', 7)), None, 'mark grid'),
    'fee definition mutated': (dict(cand_edit=_bad_fee), None, 'pinned'),
    'fee sha mismatch': (dict(cand_edit=lambda m: m['fee_scenarios'][1].update(definition_sha256='0' * 64)), None,
                         'does not match its definition'),
    'fee status changed': (dict(cand_edit=_fee_status), 'refee', 'status'),
    'unsupported strategy': (dict(cand_edit=lambda m: m['candidates'][0].update(strategy='nope')), None,
                             'unsupported strategy'),
    'side override not declared': (dict(cand_edit=lambda m: m['candidates'][1].pop('registry_side_override_required')),
                                   None, 'side override is required'),
    'unsupported side': (dict(cand_edit=lambda m: m['controls_negative_gross_only'][0].update(side='both')), None,
                         'unsupported side'),
    'candidate on unrouted tf': (dict(cand_edit=lambda m: m['candidates'][0].update(tf='1h')), None, 'no routed'),
    'registry sha mismatch': (dict(cand_edit=lambda m: m['strategy_registry'].update(sha256='0' * 64)), None,
                              'strategy registry'),
    '4h route mismatch': (dict(cand_edit=lambda m: m['data']['4h'].update(USDC_manifest_sha256='0' * 64)), None,
                          'data.4h route'),
    'timestamp mismatch': (dict(combined_edit=lambda m: m['pairs']['DOGE'].update(exact_timestamp_match=False)), None,
                           'timestamp mismatch'),
    'cell listed twice': (dict(cand_edit=lambda m: m['controls_negative_gross_only'].append(
        {k: m['candidates'][0][k] for k in ('strategy', 'side', 'tf')})), None, 'listed twice'),
    'per-quote 15m manifest sha': (dict(combined_edit=lambda m: m['quotes']['USDC'].update(manifest_sha256='0' * 64)),
                                   None, 'combined manifest record'),
    'per-file 15m sha disagrees': (dict(combined_edit=lambda m: m['quotes']['USDC']['files']['ETHUSDC'].update(
        sha256='0' * 64)), None, 'disagree'),
    'row at the sealed end': (dict(mutate=lambda kind, quote, sym, df: (
        df.assign(t=df.t.where(df.index != len(df) - 1, Q.END)) if kind == 'klines15' and sym == 'ETHUSDC' else df)),
        None, 'sealed end'),
    'off-grid 15m candle': (dict(mutate=lambda kind, quote, sym, df: (
        df.assign(t=df.t.where(df.index != 100, df.t + 1)) if kind == 'klines15' and sym == 'ETHUSDC' else df)),
        None, 'frozen window'),
    'window changed': (dict(cand_edit=lambda m: m['window'].update(fetch_start='2026-08-01T00:00:00Z')), None, 'window'),
}


@pytest.mark.parametrize('case', sorted(CASES))
def test_every_input_defect_refuses_with_only_an_incomplete_record(case, tmp_path, monkeypatch):
    _set_window(monkeypatch)
    kw, mode, match = CASES[case]
    args, expected = build(tmp_path / 'in', **kw)
    if mode == 'refee':
        expected['fee'] = {d['id']: W.fee_definition_sha(d) for d in
                           [x['definition'] for x in json.loads(open(args['candidates']).read())['fee_scenarios']]}
    _patch(monkeypatch, expected)
    monkeypatch.setattr(Q, 'run_cell', lambda *a, **k: pytest.fail('simulated before the preflight finished'))
    code, path = W.run(**args, out_dir=str(tmp_path / 'out'))
    assert code == 2
    rec = _incomplete_only(tmp_path / 'out')
    assert rec['stage'] == 'preflight' and match in rec['reason'], rec['reason']


@pytest.mark.parametrize('what', ['csv byte', 'pinned sha', 'missing file', 'dirty tree', 'accounting csv'])
def test_mutated_files_hashes_and_a_dirty_tree_refuse(what, tmp_path, monkeypatch):
    _set_window(monkeypatch)
    args, expected = build(tmp_path / 'in')
    _patch(monkeypatch, expected)
    if what == 'csv byte':
        p = tmp_path / 'in' / 'w15' / 'USDT' / 'BNBUSDT_15m.csv'
        p.write_bytes(p.read_bytes().replace(b'\n', b'\r\n', 1))
    elif what == 'pinned sha':
        monkeypatch.setattr(W, 'EXPECTED', dict(expected, candidates='0' * 64))
    elif what == 'missing file':
        os.remove(tmp_path / 'in' / 'w15' / 'USDC' / 'DOGEUSDC_15m.csv')
    elif what == 'accounting csv':
        p = tmp_path / 'in' / 'acc' / 'USDC' / 'SOLUSDC_fundingRate.csv'
        p.write_bytes(p.read_bytes() + b'\n')
    else:
        def dirty(repo):
            raise W.Wave2Error('git tree not clean / unprovable: dirty')
        monkeypatch.setattr(W, 'git_state', dirty)
    monkeypatch.setattr(Q, 'run_cell', lambda *a, **k: pytest.fail('simulated'))
    code, _ = W.run(**args, out_dir=str(tmp_path / 'out'))
    assert code == 2
    _incomplete_only(tmp_path / 'out')


def test_the_real_pins_and_fee_canonical_form():
    assert REAL_EXPECTED['candidates'] == '1812f1f5b12cda24d1ade5be44e5b9f5e7d8f7f6a5fb071f4ef76f48094d21be'
    assert REAL_EXPECTED['wave2_15m'] == 'edf1a497601b822d8681c2c47749dfdf1c4f98b155eb59d301d80bf1b0f42271'
    assert {d['id']: W.fee_definition_sha(d) for d in FEES} == REAL_EXPECTED['fee']     # compact sorted JSON
    assert W.SLIP_BARS_15M == 15 and W.SLIP_BARS_4H == 15


def test_out_dir_inside_the_repo_refuses(tmp_path, monkeypatch):
    _set_window(monkeypatch)
    args, expected = build(tmp_path / 'in')
    _patch(monkeypatch, expected)
    code, path = W.run(**args, out_dir=str(tmp_path / 'out'), repo=str(tmp_path))
    assert code == 2 and 'inside the repository' in json.loads(open(path, encoding='utf-8').read())['reason']


def test_main_returns_non_zero_on_refusal(tmp_path, monkeypatch, capsys):
    _set_window(monkeypatch)
    args, expected = build(tmp_path / 'in')
    _patch(monkeypatch, dict(expected, wave2_15m='0' * 64))
    argv = sum([[f'--{k.replace("_", "-")}', v] for k, v in args.items()], []) + ['--out-dir', str(tmp_path / 'out')]
    assert W.main(argv) == 2 and 'INCOMPLETE' in capsys.readouterr().err


def test_out_dir_on_another_drive_is_not_refused_as_inside_the_repo(tmp_path, monkeypatch):
    _set_window(monkeypatch)
    args, expected = build(tmp_path / 'in')
    _patch(monkeypatch, expected)
    real = os.path.commonpath
    def other_drive(paths):
        if str(tmp_path / 'out') in [str(x) for x in paths]: raise ValueError("Paths don't have the same drive")
        return real(paths)
    monkeypatch.setattr(W.os.path, 'commonpath', other_drive)
    code, path = W.run(**args, out_dir=str(tmp_path / 'out'), repo=str(tmp_path / 'in'))
    reason = json.loads(open(path, encoding='utf-8').read()).get('reason', '') if code else ''
    assert 'inside the repository' not in reason and 'same drive' not in reason



def test_write_once_never_overwrites(tmp_path):
    p = str(tmp_path / 'x.json')
    W.write_once(p, 'a')
    with pytest.raises(W.Wave2Error, match='never overwritten'):
        W.write_once(p, 'b')
    assert open(p, encoding='utf-8').read() == 'a'


# ------------------------------------------------------------------ Codex 6248825944 #2: the advertised schedule is causal
def _eps(*rows):
    return [dict(t_entry=t, hold_bars=h, spread_r={str(b): 0.0 for b in Q.SPREAD_BPS}, R=0.0, gross_r=0.0, slip_r=0.0,
                 fee_r=0.0, funding_r=0.0, net=0.0, start_equity_pct=0.0, mfe_r=0.0, mae_r=0.0, giveback_r=0.0,
                 base='BTC', risk=1.0, turnover=0.0, i_in=0, i_out=h) for t, h in rows]


def test_advertised_schedule_is_never_applied_retroactively():
    eff, tf = 1_000_000, 100
    p = dict(episodes=_eps((eff - 500, 2), (eff - 150, 2), (eff, 3), (eff + 400, 1)),
             open_at_end=[dict(base='BTC', t_entry=eff - 1), dict(base='ETH', t_entry=eff + 1)])
    out, w = W.causal_window(p, eff, tf)
    assert [e['t_entry'] for e in out['episodes']] == [eff, eff + 400]          # nothing entered before it is priced
    assert w['excluded_entered_before'] == 1 and w['excluded_straddling'] == 1   # the straddler is excluded, not mixed
    assert w['excluded_open_entered_before'] == 1 and [o['t_entry'] for o in out['open_at_end']] == [eff + 1]
    assert out['costs']['n'] == 2


def test_advertised_rows_cover_only_the_causal_window_on_both_twins(done):
    fees = json.loads(open(done['args']['candidates'], encoding='utf-8').read())['fee_scenarios']
    eff = W._ms(next(f['definition']['effective_from'] for f in fees if f['definition']['id'] == 'advertised_regular_user'))
    for name in done['summary']['cells']:
        d = os.path.join(os.path.dirname(done['path']), 'cells', f'{name}.json')
        sc = json.loads(open(d, encoding='utf-8').read())['content']['scenarios']
        adv, eq = sc['advertised_regular_user'], sc['equal_5bp']
        wins = [adv['quotes'][q]['fee_window'] for q in Q.QUOTES]
        assert wins[0]['from_utc'] == wins[1]['from_utc'] and wins[0]['rule'] == wins[1]['rule']   # symmetric
        for q in Q.QUOTES:
            assert all(e['t_entry'] >= eff for e in adv['quotes'][q]['episodes'])
            assert eq['quotes'][q]['fee_window'] is None                            # equal_5bp stays full-window
        if adv['paired']:
            assert all(e['episode_key'][3] >= eff for e in adv['paired']['episodes'])


def test_asymmetric_twin_fee_windows_refuse(tmp_path, monkeypatch):
    _set_window(monkeypatch)
    args, expected = build(tmp_path / 'in')
    _patch(monkeypatch, expected)
    real, calls = W.causal_window, []
    def skew(p, eff, tf):                                          # the USDC twin gets a different window
        calls.append(1)
        out, w = real(p, eff, tf)
        return out, (dict(w, from_utc='1999-01-01T00:00:00Z') if len(calls) % 2 == 0 else w)
    monkeypatch.setattr(W, 'causal_window', skew)
    code, path = W.run(**args, out_dir=str(tmp_path / 'out'))
    assert code == 2 and 'twin fee windows differ' in json.loads(open(path, encoding='utf-8').read())['reason']


def test_a_mid_window_effective_from_really_excludes_earlier_episodes(tmp_path, monkeypatch):
    _set_window(monkeypatch)
    args, expected = build(tmp_path / 'in')
    _patch(monkeypatch, expected)
    mid = W._ms('2026-09-18T00:00:00Z')                          # inside the synthetic window (all of it is post-2025-12-10)
    real = W.check_fee_scenarios
    monkeypatch.setattr(W, 'check_fee_scenarios', lambda fs: {
        k: (dict(v, effective_from_ms=mid) if v['effective_from_ms'] is not None else v) for k, v in real(fs).items()})
    code, path = W.run(**args, out_dir=str(tmp_path / 'out'))
    assert code == 0, open(path, encoding='utf-8').read()[:400]
    before = kept = 0
    for name in json.loads(open(path, encoding='utf-8').read())['cells']:
        sc = json.loads(open(os.path.join(os.path.dirname(path), 'cells', f'{name}.json'), encoding='utf-8').read())
        sc = sc['content']['scenarios']
        for q in Q.QUOTES:
            adv, eq = sc['advertised_regular_user']['quotes'][q], sc['equal_5bp']['quotes'][q]
            assert all(e['t_entry'] >= mid for e in adv['episodes'])
            before += sum(e['t_entry'] < mid for e in eq['episodes'])
            kept += len(adv['episodes'])
            assert adv['fee_window']['excluded_entered_before'] + adv['fee_window']['excluded_straddling'] + len(
                adv['episodes']) == len(eq['episodes'])
    assert before > 0 and kept > 0, (before, kept)              # the boundary really splits the synthetic trades
