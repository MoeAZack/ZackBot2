import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import shadow_benchmark as S  # noqa: E402


def packet(tmp_path, name='alpha', side=1, shift=0.0, start='2026-01-01T00:00:00Z', days=40):
    cache = tmp_path / 'cache'
    cache.mkdir(exist_ok=True)
    t = pd.date_range(start, periods=days * 6, freq='4h', tz='UTC')
    x = np.arange(len(t), dtype=float)
    c = 100 + shift + 0.04 * x + 1.5 * np.sin(x / 5)
    d = pd.DataFrame({'t': np.array([z.value // 1_000_000 for z in t], dtype=np.int64), 'o': c - .1, 'h': c + .4,
                      'l': c - .4, 'c': c, 'v': 1000.0, 'qv': 100000.0})
    path = cache / 'AAAUSDT_4h.csv'
    d.to_csv(path, index=False)
    raw_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    trades = []
    for i, hold, why in ((8, 2, 'tp'), (28, 5, 'stop'), (64, 3, 'time'), (100, 2, 'tp')):
        trades.append({'symbol': 'AAAUSDT', 'side': side, 't_in': t[i].isoformat().replace('+00:00', 'Z'),
                       't_out': t[i + hold].isoformat().replace('+00:00', 'Z'), 'R': .4 if i != 28 else -.2,
                       'why': why})
    end = (t[0] + pd.Timedelta(days=days)).isoformat().replace('+00:00', 'Z')
    p = {'format': S.INPUT_FORMAT, 'non_promotable': True,
         'window': {'start': t[0].isoformat().replace('+00:00', 'Z'), 'end': end,
                    'end_exclusive': True, 'tf': '4h', 'guard': 'pit_run.rex_window',
                    'sealed_cutoff': S.VC.SEALED_CUTOFF},
         'cache': {'files': {'AAAUSDT': {'file': path.name, 'sha256': raw_sha, 'dir': 'cache'}}},
         'costs': {'fee_taker': .0005, 'slip': .0002, 'fund_per_bar': .00001},
         'strategy': {'key': name, 'mgmt_mode': 'defaults'}, 'side': 'long' if side == 1 else 'short',
         'trades': trades}
    p['packet_sha256'] = hashlib.sha256(S.canonical(p)).hexdigest()
    pp = tmp_path / f'{name}.json'
    pp.write_text(json.dumps(p), encoding='utf-8')
    return p, str(pp), str(cache)


def test_random_control_deterministic_and_matches_geometry(tmp_path):
    p, _, cache = packet(tmp_path)
    a = S.random_entry_control(p, cache, iterations=200, seed=7)
    b = S.random_entry_control(p, cache, iterations=200, seed=7)
    assert a == b
    assert a['trades'] == len(p['trades'])
    assert a['matching_audit']['AAAUSDT:long']['holding_bars'] == [2, 2, 3, 5]
    assert 0 < a['empirical_p_one_sided'] <= 1
    assert a['claim'].startswith('entry-timing control')


def test_sampling_indices_do_not_depend_on_future_prices(tmp_path, monkeypatch):
    p, _, cache = packet(tmp_path)
    seen = []
    original = S._net_horizon_return

    def spy(d, i, hold, side, **kw):
        seen.append((i, hold, side))
        return original(d, i, hold, side, **kw)

    monkeypatch.setattr(S, '_net_horizon_return', spy)
    S.random_entry_control(p, cache, iterations=100, seed=11)
    first = list(seen)
    seen.clear()
    path = os.path.join(cache, 'AAAUSDT_4h.csv')
    d = pd.read_csv(path)
    d.loc[120:, ['o', 'h', 'l', 'c']] *= 1.8
    d.to_csv(path, index=False)
    p['cache']['files']['AAAUSDT']['sha256'] = hashlib.sha256(open(path, 'rb').read()).hexdigest()
    S.random_entry_control(p, cache, iterations=100, seed=11)
    assert [(i, h, s) for i, h, s in first] == seen


def test_cache_or_packet_tamper_refused(tmp_path):
    p, pp, cache = packet(tmp_path)
    q = json.loads(open(pp, encoding='utf-8').read())
    q['trades'][0]['R'] = 99
    open(pp, 'w', encoding='utf-8').write(json.dumps(q))
    with pytest.raises(S.BenchmarkError, match='packet_sha256'):
        S.load_packet(pp)
    path = os.path.join(cache, 'AAAUSDT_4h.csv')
    with open(path, 'a', encoding='utf-8') as f:
        f.write('\n')
    with pytest.raises(S.BenchmarkError, match='bytes differ'):
        S.random_entry_control(p, cache, iterations=100, seed=1)


def test_nonfinite_price_and_side_label_mismatch_refused(tmp_path):
    p, pp, cache = packet(tmp_path)
    path = os.path.join(cache, 'AAAUSDT_4h.csv')
    d = pd.read_csv(path)
    d.loc[120, 'c'] = np.inf
    d.to_csv(path, index=False)
    p['cache']['files']['AAAUSDT']['sha256'] = hashlib.sha256(open(path, 'rb').read()).hexdigest()
    with pytest.raises(S.BenchmarkError, match='finite and positive'):
        S.random_entry_control(p, cache, iterations=100, seed=1)
    q = json.loads(open(pp, encoding='utf-8').read())
    q['side'] = 'short'
    q['packet_sha256'] = S._packet_digest(q)
    open(pp, 'w', encoding='utf-8').write(json.dumps(q))
    with pytest.raises(S.BenchmarkError, match='trade 0 is malformed'):
        S.load_packet(pp)


def test_sealed_cutoff_and_malformed_trade_refused(tmp_path):
    _, pp, _ = packet(tmp_path)
    q = json.loads(open(pp, encoding='utf-8').read())
    q['window']['end'] = '2026-10-01T00:00:00Z'
    q['window']['sealed_cutoff'] = '2026-10-01T00:00:00Z'
    q['packet_sha256'] = S._packet_digest(q)
    open(pp, 'w', encoding='utf-8').write(json.dumps(q))
    with pytest.raises(S.BenchmarkError, match='sealed cutoff must equal frozen'):
        S.load_packet(pp)
    q['window']['end'] = '2026-02-10T00:00:00Z'
    q['window']['sealed_cutoff'] = S.VC.SEALED_CUTOFF
    del q['trades'][0]['R']
    q['packet_sha256'] = S._packet_digest(q)
    open(pp, 'w', encoding='utf-8').write(json.dumps(q))
    with pytest.raises(S.BenchmarkError, match='trade 0 is malformed'):
        S.load_packet(pp)


def test_short_cost_direction_and_min_iterations(tmp_path):
    p, _, cache = packet(tmp_path, side=-1)
    with pytest.raises(S.BenchmarkError, match='>= 100'):
        S.random_entry_control(p, cache, iterations=99)
    r = S.random_entry_control(p, cache, iterations=100, seed=2)
    assert r['matching_audit']['AAAUSDT:short']['trades'] == 4
    assert r['costs']['fee_taker'] == .0005


def test_portfolio_correlation_contribution_and_leave_one_out(tmp_path):
    p1, _, _ = packet(tmp_path, 'alpha')
    p2 = json.loads(json.dumps(p1))
    p2['strategy']['key'] = 'hedge'
    for i, tr in enumerate(p2['trades']):
        tr['R'] = [-.2, .3, -.1, .4][i]
    r = S.portfolio_contribution({'alpha': p1, 'hedge': p2}, min_days=30)
    # UTC window edges straddle partial Cairo calendar days (+02 in January).
    assert r['available'] is True and r['window']['days'] == 41
    assert set(r['leave_one_out']) == {'alpha', 'hedge'}
    parts = [x['component'] for x in r['volatility_contribution'].values()]
    assert sum(parts) == pytest.approx(r['annualized_vol_r'], abs=2e-8)
    assert r['calendar_zero_correlation']['alpha']['alpha'] == 1.0


def test_build_write_once_and_optional_ledger_binding(tmp_path, monkeypatch):
    p, pp, cache = packet(tmp_path)
    doc = S.build([pp], cache, iterations=100, seed=3)
    assert doc['multiple_testing']['tests_in_batch'] == 1
    assert doc['multiple_testing']['threshold'] == .05
    out = tmp_path / 'shadow.json'
    assert S.write_once(doc, str(out)) is True
    assert S.write_once(doc, str(out)) is False
    got = {}
    monkeypatch.setattr(S.LEDGER, 'append', lambda path, **kw: got.update(path=path, **kw) or kw)
    S.append_baseline(str(tmp_path / 'alpha.jsonl'), p, doc, author='codex', cairo_date='2026-10-17', lineage='root')
    assert got['kind'] == 'baseline' and got['split'] == 'development'
    assert got['detail']['packet_sha256'] == p['packet_sha256']


def test_duplicate_packet_and_insufficient_batch_trials_refused(tmp_path):
    _, pp, cache = packet(tmp_path)
    with pytest.raises(S.BenchmarkError, match='duplicate'):
        S.build([pp, pp], cache, iterations=100, names=['one', 'two'])
    base = json.loads(open(pp, encoding='utf-8').read())
    paths = []
    for i in range(6):
        q = json.loads(json.dumps(base))
        q['strategy']['key'] = f's{i}'
        q['packet_sha256'] = S._packet_digest(q)
        path = tmp_path / f's{i}.json'
        path.write_text(json.dumps(q), encoding='utf-8')
        paths.append(str(path))
    with pytest.raises(S.BenchmarkError, match='at least 119'):
        S.build(paths, cache, iterations=100, names=['a', 'b', 'c', 'd', 'e', 'f'])


def test_benchmark_identity_excludes_time_and_local_path(tmp_path):
    _, pp, cache = packet(tmp_path)
    a = S.build([pp], cache, iterations=100, seed=9)
    b = S.build([pp], cache, iterations=100, seed=9)
    b['created_at_utc'] = '2099-01-01T00:00:00Z'
    b['inputs'][0]['path'] = 'D:/another/machine/packet.json'
    assert a['benchmark_sha256'] == b['benchmark_sha256'] == S._benchmark_digest(b)


def test_empty_and_missing_common_window_refused(tmp_path):
    p, _, cache = packet(tmp_path)
    p['trades'] = []
    with pytest.raises(S.BenchmarkError, match='at least one'):
        S.random_entry_control(p, cache, iterations=100)
    p1, _, _ = packet(tmp_path, 'one')
    p2, _, _ = packet(tmp_path, 'two', start='2027-01-01T00:00:00Z')
    with pytest.raises(S.BenchmarkError, match='no common'):
        S.portfolio_contribution({'one': p1, 'two': p2}, min_days=1)
