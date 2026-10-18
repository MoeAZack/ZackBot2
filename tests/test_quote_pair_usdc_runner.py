"""USDC-PAIR-RUNNER (Codex 6245713293): the paired USDT/USDC study refuses the whole run before any simulation on a
defective or copied input, pairs episodes only on exact keys and writes no packet unless every cell is complete."""
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = os.path.join(ROOT, 'tools', 'research')
if R not in sys.path:
    sys.path.insert(0, R)
import run_quote_pair_usdc as Q  # noqa: E402

H = Q.HOUR
STALE = r'C:\Dev\ZackBot2_usdc_pair_data'


def _write(root, name, df):
    df.to_csv(root / name, index=False)
    data = (root / name).read_bytes()
    return {'file': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


def pack(tmp, quote, *, copy_ohlc=False, mutate=None):
    """A complete synthetic klines + accounting pack for one quote. `mutate(kind, sym, df)` may edit a frame."""
    root = tmp / quote
    root.mkdir(parents=True)
    mut = mutate or (lambda kind, sym, df: df)
    t = np.arange(Q.WARM, Q.END, Q.BC.TF_MS, dtype=np.int64)
    rng = np.random.default_rng(7)
    walk = 100 * np.exp(np.cumsum(rng.normal(0, .01, len(t))))
    kfiles, afiles, rules = {}, {}, {}
    for j, base in enumerate(Q.BASES):
        sym = base + quote
        c = walk * (1 + j) * (1 if quote == 'USDT' or copy_ohlc else 1.0003)
        o = np.r_[c[0], c[:-1]]
        d = pd.DataFrame({'t': t, 'o': o, 'h': np.maximum(o, c) * 1.002, 'l': np.minimum(o, c) * .998, 'c': c,
                          'v': 10.0 if quote == 'USDT' else 11.0, 'qv': 1000.0})   # volumes differ: hashes differ
        kfiles[sym] = dict(_write(root, f'{sym}_4h.csv', mut('klines', sym, d)), status='complete',
                           validation={'status': 'complete'}, rows=len(t))
        ft = np.arange(Q.WARM, Q.END, 8 * H, dtype=np.int64)
        f = pd.DataFrame({'fundingTime': ft, 'fundingRate': 0.0001 if quote == 'USDT' else 0.00005,
                          'markPrice': 100.0 + j})
        mt = np.arange(Q.WARM, Q.END, H, dtype=np.int64)
        mp = 100.0 + j + (0 if quote == 'USDT' else .01)
        mk = pd.DataFrame({'open_time': mt, 'open': mp, 'high': mp + 1, 'low': mp - 1, 'close': mp,
                           'close_time': mt + H - 1})
        for series, df in (('fundingRate', f), ('markPriceKlines_1h', mk)):
            afiles[f'{sym}/{series}'] = dict(_write(root, f'{sym}_{series}.csv', mut(series, sym, df)), symbol=sym,
                                             series=series, status='complete')
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
    (root / 'klines.json').write_text(json.dumps(km), encoding='utf-8')
    (root / 'accounting.json').write_text(json.dumps(am), encoding='utf-8')
    return root / 'klines.json', root / 'accounting.json'


def edit(path, fn):
    m = json.loads(path.read_text())
    fn(m)
    path.write_text(json.dumps(m))


def both(tmp, **kw):
    (ut, ua), (ct, ca) = pack(tmp, 'USDT'), pack(tmp, 'USDC', **kw)
    return dict(usdt_klines=str(ut), usdc_klines=str(ct), usdt_accounting=str(ua), usdc_accounting=str(ca))


def test_a_complete_pair_loads_with_quote_local_btc_first(tmp_path):
    k, a = Q.load_all(**both(tmp_path))
    assert list(k['USDC'][1])[0] == 'BTCUSDC' and list(k['USDT'][1])[0] == 'BTCUSDT'
    assert a['USDC'][1]['BTCUSDC']['cadence_h'] == 8 and a['USDT'][1]['ETHUSDT']['rules']['status'] == 'TRADING'


@pytest.mark.parametrize('fn,match', [
    (lambda m: m['window'].update(end='2026-10-05T00:00:00Z'), 'frozen complete contract'),
    (lambda m: m.update(incomplete=['BTCUSDC']), 'frozen complete contract'),
    (lambda m: m.update(stopped='Transient'), 'frozen complete contract'),
    (lambda m: m.update(quote='USDT'), 'frozen complete contract'),
    (lambda m: m['fetch'].update(start='2024-10-14T00:00:00Z'), 'frozen complete contract'),
    (lambda m: m['files']['SOLUSDC'].update(status='INCOMPLETE'), 'SOLUSDC is INCOMPLETE'),
    (lambda m: m['files']['SOLUSDC']['validation'].update(status='INCOMPLETE'), 'SOLUSDC is INCOMPLETE'),
    (lambda m: m['files']['XRPUSDC'].update(sha256='0' * 64), 'does not match its manifest sha256'),
    (lambda m: m['files']['XRPUSDC'].update(file='../XRPUSDC_4h.csv'), 'unsafe'),
    (lambda m: m['pairs']['ETHUSDC'].update(usdt_twin='ETHFDUSD'), 'pairs do not match'),
    (lambda m: m['pairs'].pop('DOGEUSDC'), 'pairs do not match'),
    (lambda m: m['pairs']['ETHUSDC'].update(base='ETC'), 'pairs do not match'),
    (lambda m: m.pop('pairs'), 'pairs do not match'),
    (lambda m: m['pairs']['ETHUSDC'].pop('usdt_twin'), 'pairs do not match'),
    (lambda m: m['pairs']['ETHUSDC'].update(usdc_twin='ETHUSDT'), 'pairs do not match'),
])
def test_each_klines_manifest_defect_refuses(tmp_path, fn, match):
    kw = both(tmp_path)
    edit(tmp_path / 'USDC' / 'klines.json', fn)
    with pytest.raises(Q.PairRunnerError, match=match):
        Q.load_all(**kw)


def _drop_row(i):
    return lambda df: df.drop(index=i).reset_index(drop=True)


@pytest.mark.parametrize('kind,sym,fn,match', [
    ('klines', 'BNBUSDC', _drop_row(2000), 'INCOMPLETE for the frozen window'),
    ('klines', 'BNBUSDC', _drop_row(0), 'INCOMPLETE for the frozen window'),
    ('klines', 'BNBUSDC', lambda d: d.assign(o=np.where(d.index == 77, 0.0, d.o)), 'next-open'),
    ('klines', 'BNBUSDC', lambda d: d.assign(h=np.where(d.index == 77, d.l * .5, d.h)), 'next-open'),
    ('klines', 'BNBUSDC', lambda d: d.assign(c=np.where(d.index == 5, np.nan, d.c)), 'non-finite'),
    ('klines', 'BNBUSDC', lambda d: d.drop(columns='qv'), 'columns'),
    ('klines', 'BNBUSDC', lambda d: pd.concat([d, d.iloc[[-1]].assign(t=Q.END)], ignore_index=True), 'INCOMPLETE'),
    ('klines', 'BNBUSDC', lambda d: d.assign(t=d.t + np.where(d.index == 50, 0.5, 0)), 'INCOMPLETE'),
    ('fundingRate', 'ETHUSDC', _drop_row(300), 'fixed 1/4/8h UTC grid'),
    ('fundingRate', 'ETHUSDC', _drop_row(0), 'phase anchor'),
    ('fundingRate', 'ETHUSDC', lambda d: d.iloc[:-1], 'window end'),
    ('fundingRate', 'ETHUSDC', lambda d: d.assign(fundingTime=d.fundingTime + np.where(d.index == 9, 2 * H, 0)),
     'fixed 1/4/8h UTC grid'),
    ('fundingRate', 'ETHUSDC', lambda d: d.assign(markPrice=np.where(d.index == 4, 0.0, d.markPrice)), 'mark price'),
    ('markPriceKlines_1h', 'DOGEUSDC', _drop_row(999), 'mark grid'),
    ('markPriceKlines_1h', 'DOGEUSDC', lambda d: d.assign(close_time=d.close_time + 1), 'off grid'),
    ('markPriceKlines_1h', 'DOGEUSDC', lambda d: d.assign(low=np.where(d.index == 3, -1.0, d.low)), 'mark OHLC'),
])
def test_each_series_defect_refuses(tmp_path, kind, sym, fn, match):
    mut = lambda k, s, df: fn(df) if (k, s) == (kind, sym) else df
    (ut, ua), (ct, ca) = pack(tmp_path, 'USDT'), pack(tmp_path, 'USDC', mutate=mut)
    with pytest.raises(Q.PairRunnerError, match=match):
        Q.load_all(str(ut), str(ct), str(ua), str(ca))


def _funding(tmp, fn):
    mut = lambda k, s, df: fn(df) if (k, s) == ('fundingRate', 'ETHUSDC') else df
    (ut, ua), (ct, ca) = pack(tmp, 'USDT'), pack(tmp, 'USDC', mutate=mut)
    return lambda: Q.load_all(str(ut), str(ct), str(ua), str(ca))


def test_a_jittered_funding_time_inside_tolerance_is_accepted(tmp_path):
    jit = (np.arange(Q.WARM, Q.END, 8 * H) // (8 * H) % 3 - 1) * 50_000     # -50 / 0 / +50 s, never accumulating
    _funding(tmp_path, lambda d: d.assign(fundingTime=d.fundingTime + jit))()


def test_jitter_cannot_accumulate_into_a_skipped_event(tmp_path):
    # one event dropped, the rest re-spaced 8h + 12.8 s apart: every STEP is within tolerance, the grid slots are not
    n = len(np.arange(Q.WARM, Q.END, 8 * H)) - 1
    drift = lambda d: pd.DataFrame({'fundingTime': Q.WARM + np.arange(n) * (8 * H + 12_800),
                                    'fundingRate': 0.0001, 'markPrice': 101.0})
    with pytest.raises(Q.PairRunnerError, match='fixed 1/4/8h UTC grid'):
        _funding(tmp_path, drift)()


def test_a_shifted_funding_phase_refuses(tmp_path):
    shift = lambda d: d.assign(fundingTime=d.fundingTime - 4 * H)          # 04/12/20 UTC instead of 00/08/16
    with pytest.raises(Q.PairRunnerError, match='fixed 1/4/8h UTC grid'):
        _funding(tmp_path, shift)()


def test_an_absurd_funding_rate_refuses(tmp_path):
    with pytest.raises(Q.PairRunnerError, match='sanity bound'):
        _funding(tmp_path, lambda d: d.assign(fundingRate=np.where(d.index == 10, 5.0, d.fundingRate)))()


@pytest.mark.parametrize('fn,match', [
    (lambda m: m['exchange_rules']['symbols']['BTCUSDC'].update(status='SETTLING'), 'rules/status defect'),
    (lambda m: m['exchange_rules']['symbols']['BTCUSDC'].update(quoteAsset='USDT'), 'rules/status defect'),
    (lambda m: m['exchange_rules']['symbols']['BTCUSDC'].update(contractType='CURRENT_QUARTER'), 'rules/status'),
    (lambda m: m['exchange_rules']['symbols']['BTCUSDC']['filters'].pop('MIN_NOTIONAL'), 'rules/status defect'),
    (lambda m: m['exchange_rules']['symbols'].pop('SOLUSDC'), 'rules/status defect'),
    (lambda m: m['exchange_rules']['symbols']['ETHUSDC']['filters'].update(PRICE_FILTER={}), 'rules/status defect'),
    (lambda m: m['exchange_rules']['symbols']['ETHUSDC']['filters'].update(LOT_SIZE=None), 'rules/status defect'),
    (lambda m: m['exchange_rules']['symbols']['ETHUSDC']['filters']['MIN_NOTIONAL'].update(notional='0'), 'rules/'),
    (lambda m: m['exchange_rules']['symbols']['ETHUSDC']['filters']['MARKET_LOT_SIZE'].update(stepSize='x'), 'rules/'),
    (lambda m: m['exchange_rules']['symbols'].update(ETHUSDC='TRADING'), 'rules/status defect'),
    (lambda m: m['exchange_rules'].update(label='CAUSAL'), 'frozen complete contract'),
    (lambda m: m['files']['BTCUSDC/fundingRate'].update(status='INCOMPLETE'), 'BTCUSDC fundingRate is INCOMPLETE'),
    (lambda m: m['files']['BTCUSDC/fundingRate'].update(symbol='BTCUSDT'), 'INCOMPLETE'),
    (lambda m: m['files'].pop('XRPUSDC/markPriceKlines_1h'), 'INCOMPLETE'),
    (lambda m: m.update(symbols=['BTCUSDC']), 'frozen complete contract'),
    (lambda m: m['window'].update(end_exclusive='2026-10-05T00:00:00Z'), 'frozen complete contract'),
])
def test_each_accounting_manifest_defect_refuses(tmp_path, fn, match):
    kw = both(tmp_path)
    edit(tmp_path / 'USDC' / 'accounting.json', fn)
    with pytest.raises(Q.PairRunnerError, match=match):
        Q.load_all(**kw)


def test_a_copied_or_identical_twin_refuses(tmp_path):
    with pytest.raises(Q.PairRunnerError, match='bit-identical'):
        Q.load_all(**both(tmp_path / 'a', copy_ohlc=True))
    kw = both(tmp_path / 'b')                                     # the USDC pack ships the USDT file under its name
    for b in Q.BASES:
        src, dst = tmp_path / 'b' / 'USDT' / f'{b}USDT_4h.csv', tmp_path / 'b' / 'USDC' / f'{b}USDC_4h.csv'
        dst.write_bytes(src.read_bytes())
    sha = {b: hashlib.sha256((tmp_path / 'b' / 'USDC' / f'{b}USDC_4h.csv').read_bytes()).hexdigest() for b in Q.BASES}
    edit(tmp_path / 'b' / 'USDC' / 'klines.json', lambda m: [m['files'][b + 'USDC'].update(sha256=sha[b]) for b in Q.BASES])
    with pytest.raises(Q.PairRunnerError, match='identical sha256'):
        Q.load_all(**kw)


def test_a_shared_funding_file_refuses(tmp_path):
    kw = both(tmp_path)
    src, dst = tmp_path / 'USDT' / 'SOLUSDT_fundingRate.csv', tmp_path / 'USDC' / 'SOLUSDC_fundingRate.csv'
    dst.write_bytes(src.read_bytes())
    edit(tmp_path / 'USDC' / 'accounting.json',
         lambda m: m['files']['SOLUSDC/fundingRate'].update(sha256=hashlib.sha256(dst.read_bytes()).hexdigest()))
    with pytest.raises(Q.PairRunnerError, match='fundingRate files have an identical sha256'):
        Q.load_all(**kw)


def test_a_symbol_copied_inside_one_quote_refuses(tmp_path):
    kw = both(tmp_path)
    d = tmp_path / 'USDC'
    (d / 'ETHUSDC_4h.csv').write_bytes((d / 'BTCUSDC_4h.csv').read_bytes())
    edit(d / 'klines.json', lambda m: m['files']['ETHUSDC'].update(sha256=m['files']['BTCUSDC']['sha256']))
    with pytest.raises(Q.PairRunnerError, match='ETHUSDC and BTCUSDC share one kline file'):
        Q.load_all(**kw)
    kw = both(tmp_path / 'f')
    d = tmp_path / 'f' / 'USDC'
    (d / 'SOLUSDC_fundingRate.csv').write_bytes((d / 'BTCUSDC_fundingRate.csv').read_bytes())
    edit(d / 'accounting.json', lambda m: m['files']['SOLUSDC/fundingRate'].update(
        sha256=m['files']['BTCUSDC/fundingRate']['sha256']))
    with pytest.raises(Q.PairRunnerError, match='share one fundingRate file'):
        Q.load_all(**kw)


def test_a_reformatted_copy_of_a_twin_series_refuses(tmp_path):
    kw = both(tmp_path)
    src = pd.read_csv(tmp_path / 'USDT' / 'SOLUSDT_markPriceKlines_1h.csv')
    dst = tmp_path / 'USDC' / 'SOLUSDC_markPriceKlines_1h.csv'
    src.to_csv(dst, index=False, float_format='%.2f')                      # same values, different bytes
    edit(tmp_path / 'USDC' / 'accounting.json', lambda m: m['files']['SOLUSDC/markPriceKlines_1h'].update(
        sha256=hashlib.sha256(dst.read_bytes()).hexdigest()))
    with pytest.raises(Q.PairRunnerError, match='SOL: twin mark values are identical'):
        Q.load_all(**kw)
    kw = both(tmp_path / 'f')
    src = pd.read_csv(tmp_path / 'f' / 'USDT' / 'XRPUSDT_fundingRate.csv').assign(markPrice=55.5)
    dst = tmp_path / 'f' / 'USDC' / 'XRPUSDC_fundingRate.csv'
    src.to_csv(dst, index=False)
    edit(tmp_path / 'f' / 'USDC' / 'accounting.json', lambda m: m['files']['XRPUSDC/fundingRate'].update(
        sha256=hashlib.sha256(dst.read_bytes()).hexdigest()))
    with pytest.raises(Q.PairRunnerError, match='XRP: twin funding values are identical'):
        Q.load_all(**kw)


def test_a_malformed_manifest_or_csv_refuses_cleanly(tmp_path):
    kw = both(tmp_path)
    (tmp_path / 'USDC' / 'klines.json').write_text('[1, 2]')
    with pytest.raises(Q.PairRunnerError, match='not a JSON object'):
        Q.load_all(**kw)
    kw = both(tmp_path / 'c')
    d = tmp_path / 'c' / 'USDC'
    (d / 'BNBUSDC_4h.csv').write_bytes(b'')
    edit(d / 'klines.json', lambda m: m['files']['BNBUSDC'].update(sha256=hashlib.sha256(b'').hexdigest()))
    with pytest.raises(Q.PairRunnerError, match='unreadable CSV'):
        Q.load_all(**kw)


def test_misaligned_twins_refuse(tmp_path):
    k, _ = Q.load_all(**both(tmp_path))
    kt, ft, kc, fc = k['USDT'][1], k['USDT'][2], dict(k['USDC'][1]), k['USDC'][2]
    kc['XRPUSDC'] = kc['XRPUSDC'].assign(t=kc['XRPUSDC'].t + pd.Timedelta(milliseconds=1))
    with pytest.raises(Q.PairRunnerError, match='do not align'):
        Q.validate_twins(kt, kc, ft, fc)


def test_any_defect_refuses_the_whole_study_before_any_simulation(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(Q, 'run_cell', lambda *a, **k: calls.append(a) or pytest.fail('simulated'))
    monkeypatch.setattr(Q.VC, 'clean_commit', lambda repo: 'c0ffee')
    kw = both(tmp_path)
    edit(tmp_path / 'USDC' / 'accounting.json', lambda m: m['exchange_rules']['symbols']['DOGEUSDC'].update(status='BREAK'))
    with pytest.raises(Q.PairRunnerError, match='DOGEUSDC'):
        Q.run(out=str(tmp_path / 'pkt.json'), **kw)
    assert calls == [] and not (tmp_path / 'pkt.json').exists()


def test_the_stale_unfrozen_pack_is_refused_clearly():
    if not os.path.isdir(STALE):
        pytest.skip('stale local pack not present')
    m = os.path.join(STALE, 'manifest.json')
    with pytest.raises(Q.PairRunnerError, match='USDC klines manifest does not prove the frozen complete contract'):
        Q.load_klines(m, 'USDC')
    with pytest.raises(Q.PairRunnerError, match='accounting manifest'):
        Q.load_accounting(m, 'USDC')


def ep(base, t, R=1.0, strategy='exp_trend', side='long'):
    return {'strategy': strategy, 'side': side, 'base': base, 't_entry': t, 'R': R}


def test_episodes_pair_only_on_the_exact_key_and_unmatched_are_kept():
    T = Q.START
    u = [ep('BTC', T), ep('ETH', T), ep('SOL', T + Q.BC.TF_MS), ep('XRP', T, side='short')]
    c = [ep('BTC', T, R=-1.0), ep('ETH', T + Q.BC.TF_MS), ep('SOL', T + Q.BC.TF_MS), ep('XRP', T)]
    p = Q.pair_episodes(u, c)
    assert [(a['base'], b['R']) for a, b in p['paired']] == [('BTC', -1.0), ('SOL', 1.0)]
    assert sorted(e['base'] for e in p['only_usdt']) == ['ETH', 'XRP']               # a side mismatch never pairs
    assert sorted(e['base'] for e in p['only_usdc']) == ['ETH', 'XRP']
    assert [e['R'] for e in p['only_usdt'] + p['only_usdc']] == [1.0] * 4             # kept as is, never zero-filled
    assert len(p['paired']) + len(p['only_usdt']) == len(u) and len(p['paired']) + len(p['only_usdc']) == len(c)
    with pytest.raises(Q.PairRunnerError, match='duplicate episode key'):
        Q.pair_episodes([ep('BTC', T), ep('BTC', T, R=2.0)], c)


def test_signal_agreement_and_lead_lag():
    B = Q.BC.TF_MS
    T = Q.START
    a = Q.agreement({'BTC': [T, T + 10 * B, T + 40 * B], 'ETH': [T + 100 * B]},
                    {'BTC': [T, T + 12 * B], 'ETH': [T + 99 * B, T + 101 * B], 'SOL': [T]})
    assert (a['both'], a['only_usdt'], a['only_usdc']) == (1, 3, 4)
    assert a['lead_lag_bars'] == {'1': 1, '2': 1} and a['unpaired_usdt'] == 1         # tie -> the later USDC signal
    assert a['unpaired_usdc'] == 2                                                      # ETH +99 bars and SOL
    one = Q.agreement({'BTC': [T, T + 2 * B]}, {'BTC': [T + B]})                       # one USDC signal, two claimants
    assert one['lead_lag_bars'] == {'1': 1} and one['unpaired_usdt'] == 1 and one['unpaired_usdc'] == 0
    rev = Q.agreement({'BTC': [T + 3 * B]}, {'BTC': [T]})
    assert rev['lead_lag_bars'] == {'-3': 1}                                            # USDC earlier: negative
    assert a['jaccard'] == pytest.approx(1 / 8) and a['by_base']['SOL'] == {'both': 0, 'only_usdt': 0, 'only_usdc': 1}


def test_sufficiency_needs_every_part_of_the_frozen_bar():
    q = [pd.Timestamp(f'{y}-{m:02d}-15', tz='UTC').value // 1_000_000 for y, m in
         ((2024, 11), (2025, 2), (2025, 5), (2025, 8), (2025, 11), (2026, 2))]
    paired = [(ep(b, q[i % 6] + i * Q.BC.TF_MS), ep(b, q[i % 6] + i * Q.BC.TF_MS))
              for i, b in zip(range(60), ['BTC', 'ETH', 'SOL', 'XRP'] * 15)]
    ok = Q.sufficiency({'paired': paired, 'only_usdt': [], 'only_usdc': []})
    assert ok['exploratory_eligible'] and len(ok['quarters']) == 6 and ok['symbols'] == ['BTC', 'ETH', 'SOL', 'XRP']
    assert not Q.sufficiency({'paired': paired[:59], 'only_usdt': [], 'only_usdc': []})['exploratory_eligible']
    three = [(dict(a, base='BTC' if a['base'] == 'XRP' else a['base']), b) for a, b in paired]
    assert not Q.sufficiency({'paired': three, 'only_usdt': [], 'only_usdc': []})['exploratory_eligible']
    five_q = [(dict(a, t_entry=q[0]), b) if _q(a) == _q(paired[-1][0]) else (a, b) for a, b in paired]
    assert not Q.sufficiency({'paired': five_q, 'only_usdt': [], 'only_usdc': []})['exploratory_eligible']


def _q(e):
    return Q._quarter(e['t_entry'])


def test_a_cell_runs_on_its_own_quote_with_btc_context(tmp_path):
    k, _ = Q.load_all(**both(tmp_path))
    cell = Q.run_cell(k['USDC'][1], 'USDC', 'exp_trend', 'long')
    assert cell['context_symbol'] == 'BTCUSDC' and set(cell['signals']) == set(Q.BASES)
    assert all(e['side'] == 'long' and Q.START <= e['t_entry'] < Q.END for e in cell['episodes'])
    shuffled = dict(reversed(list(k['USDC'][1].items())))
    with pytest.raises(Q.PairRunnerError, match='BTC context first'):
        Q.run_cell(shuffled, 'USDC', 'exp_trend', 'long')


def test_both_quotes_face_the_same_min_notional_floor(tmp_path, monkeypatch):
    seen = {}

    def fake(raw, key, side, symbols, *a, **k):
        seen[symbols[0]] = {s: Q.BC.BT.legacy_rule(s)['min_notional'] for s in symbols}
        raise Q.BC.RunnerError('stop here')
    monkeypatch.setattr(Q.BC, 'simulate', fake)
    k, _ = Q.load_all(**both(tmp_path))
    before = dict(Q.BC.BT.MIN_NOTIONAL)
    for q in Q.QUOTES:
        with pytest.raises(Q.PairRunnerError, match='stop here'):
            Q.run_cell(k[q][1], q, 'exp_trend', 'long')
    assert list(seen['BTCUSDC'].values()) == list(seen['BTCUSDT'].values()) and seen['BTCUSDC']['BTCUSDC'] == 50
    assert Q.BC.BT.MIN_NOTIONAL == before                                    # restored after the run


def test_no_packet_while_any_cell_is_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(Q.VC, 'clean_commit', lambda repo: 'c0ffee')
    sims = []
    monkeypatch.setattr(Q, 'run_cell', lambda raw, q, key, side: sims.append(q) or {'episodes': [], 'signals': {}})
    out = tmp_path / 'pkt.json'
    with pytest.raises(Q.PairRunnerError, match='nothing simulated'):          # pricing not ready: refused up front
        Q.run(out=str(out), **both(tmp_path))
    assert not out.exists() and sims == []
    monkeypatch.setattr(Q, 'PRICING_READY', True)
    with pytest.raises(Q.PairRunnerError, match='INCOMPLETE, no packet written'):
        Q.run(out=str(out), **both(tmp_path / 'stub'))
    monkeypatch.setattr(Q, 'price_cell', lambda cell, q, acc: {'status': 'complete'})    # a bare status is not enough
    with pytest.raises(Q.PairRunnerError, match='priced cell lacks'):
        Q.run(out=str(out), **both(tmp_path / 'bare'))
    assert not out.exists()
    monkeypatch.setattr(Q, 'price_cell', lambda cell, q, acc: dict.fromkeys(Q.PRICED_KEYS, []) | {'status': 'complete'})
    p = Q.run(out=str(out), **both(tmp_path / 'ok'))
    assert p['cells']['exp_trend.long']['pairing'] == {'paired': [], 'only_usdt': [], 'only_usdc': []}
    assert out.exists() and p['usdc_better'].startswith('UNAVAILABLE') and p['verdict'] is None
    with pytest.raises(Q.PairRunnerError, match='never overwritten'):
        Q.run(out=str(out), **both(tmp_path / 'again'))


def test_the_integration_layout_and_a_wider_pack_load(tmp_path):
    kw = both(tmp_path)
    for q in ('USDT', 'USDC'):                        # files moved into <quote>/ below the manifest, plus extra bases
        d = tmp_path / q
        (d / q.lower()).mkdir()
        for f in d.glob('*.csv'):
            f.rename(d / q.lower() / f.name)
        edit(d / 'klines.json', lambda m: m['pairs'].update({'SUI' + q: {'base': 'SUI', 'usdt_twin': 'SUIUSDT'}}))
    k, _ = Q.load_all(**kw)
    assert list(k['USDC'][1]) == [b + 'USDC' for b in Q.BASES]
    src = tmp_path / 'USDC' / 'usdc' / 'BTCUSDC_4h.csv'
    (tmp_path / 'USDC' / 'BTCUSDC_4h.csv').write_bytes(src.read_bytes())
    with pytest.raises(Q.PairRunnerError, match='ambiguous'):
        Q.load_all(**kw)
