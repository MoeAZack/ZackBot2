import importlib.util
from types import SimpleNamespace
from pathlib import Path

import pytest

P = Path(__file__).resolve().parents[1] / 'tools' / 'research' / 'lane2_prot_replay.py'
spec = importlib.util.spec_from_file_location('lane2_prot_replay', P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_instrument_requires_exactly_one_charge_site():
    assert '_replay_hook' in m.instrument('before; ' + m.CHARGE)
    with pytest.raises(ValueError): m.instrument('no charge')
    with pytest.raises(ValueError): m.instrument(m.CHARGE + '\n' + m.CHARGE)


def test_closed_trade_keeps_partial_fill_and_proxy_separate():
    row = SimpleNamespace(sleeve='s', sym='X', i_in=0, i_out=1, side=-1, pnl=5, R=.5)
    base = dict(sleeve='s', sym='X', pos_i=0, side=-1, risk=10)
    fills = {('s', 'X', 0): [dict(base, kind='entry', qty=3, i=0),
                               dict(base, kind='exit', qty=1, i=1),
                               dict(base, kind='exit', qty=2, i=1)]}
    charges = {('s', 'X', 0): [dict(cash_paid=1), dict(cash_paid=2)]}
    t = m.serialize_trade(row, fills, charges, ['2022-01-01', '2022-01-01 04:00'], 'job')
    assert t['proxy_cash_paid'] == 3
    assert [f['signed_qty'] for f in t['fills']] == [-3, 1, 2]
    assert t['t_out_ms'] - t['t_in_ms'] == m.BAR_MS
    assert t['baseline_R'] == .5
    fills[('s', 'X', 0)][-1]['qty'] = 1
    with pytest.raises(ValueError, match='do not close'): m.serialize_trade(row, fills, charges, ['2022-01-01', '2022-01-01 04:00'], 'job')


def test_reuse_relabels_trade_ids_and_checks_published_parity():
    source = {'job_id': 'path4h|A|s|old|7', 'trades': [{'sym': 'X', 'i_in': 3, 'baseline_R': 1.25, 'trade_id': 'old'}]}
    job = 'path4h|A|s|new|7'
    result = m.reuse_record(source, job, {'n': '1', 'sumR': '1.25'})
    assert result['reused_from_job'] == source['job_id']
    assert result['trades'][0]['trade_id'] == job + '|X|3'
    assert source['trades'][0]['trade_id'] == 'old'
    with pytest.raises(ValueError, match='published'):
        m.reuse_record(source, job, {'n': '1', 'sumR': '1.5'})


def test_semantic_key_requires_exact_effective_settings_and_scope():
    pc = SimpleNamespace(COHORT=[('s', 'both', {})], variants=lambda key, base: {'one': {'x': 1}, 'same': {'x': 1}, 'other': {'x': 2}})
    strategies = SimpleNamespace(merge_mgmt=lambda key, override: override)
    a = m.semantic_key('path4h|A|s|one|7', pc, strategies)
    assert a == m.semantic_key('path4h|A|s|same|7', pc, strategies)
    assert a != m.semantic_key('path4h|B|s|same|7', pc, strategies)
    assert a != m.semantic_key('path4h|A|s|other|7', pc, strategies)


def test_salvages_only_complete_json_lines(tmp_path):
    path = tmp_path / 'interrupted.jsonl'
    path.write_text('{"job_id":"valid"}\n{"job_id":"partial"', encoding='utf-8')
    assert [record['job_id'] for _, _, record in m.read_records(tmp_path)] == ['valid']
