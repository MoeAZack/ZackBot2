"""EDGE05 funding_carry_xsec.v1: DRAFT prereg parity + the metadata-only coverage tool (no evaluation)."""
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'research'))
import edge05_coverage as CV                                                                # noqa: E402

PREREG = ROOT / 'research_evidence' / 'prereg' / 'funding_carry_xsec.v1.json'   # authoritative; tests never read the docs twin


def c():
    return json.loads(PREREG.read_text(encoding='utf-8'))


# ------------------------------------------------------------------ contract

def test_status_is_draft_unsigned_and_no_ledger_yet():
    p = c()
    assert p['format'] == 'zb-edge05-prereg/1' and p['candidate_id'] == 'funding_carry_xsec.v1'
    assert p['status'] == 'DRAFT - UNSIGNED - NOT FROZEN' and p['signed'] is False
    assert not (ROOT / 'research_evidence' / 'ledger' / 'funding_carry_xsec.jsonl').exists()
    assert b'funding_carry_xsec' not in (ROOT / 'research_evidence' / 'ledger' / 'REGISTRY.jsonl').read_bytes()
    assert p['boundaries'] == {'paper': False, 'runtime_change': False, 'holdout_read': False,
                               'mainnet': False, 'evaluation_run': False}


def test_universe_artifact_hash_is_bound():
    p = c()
    art = ROOT / p['universe']['artifact']
    assert hashlib.sha256(art.read_bytes()).hexdigest() == p['source']['universe_sha256']
    assert len(json.loads(art.read_text(encoding='utf-8'))['symbols']) == p['universe']['size'] == 40


def test_windows_match_edge04_split_and_holdout_is_sealed():
    w = c()['windows']
    folds = w['walk_forward']
    assert len(folds) == 5 and w['train']['end'] == folds[0]['start']
    assert all(a['end'] == b['start'] for a, b in zip(folds, folds[1:]))
    assert folds[-1]['end'] == w['sealed_cutoff'] == CV.SEALED_CUTOFF == '2026-09-28T00:00:00Z'
    assert w['holdout']['start'] > w['sealed_cutoff'] and w['holdout']['reveals'] == 1


def test_fixed_choices_and_trial_budget():
    p = c()
    s, e, g = p['selection'], p['execution'], p['gates']
    assert s['k'] == 5 and s['max_positions_per_book'] == 5 and s['books_separate'] is True
    assert e['holding_cap_hours'] == 24 and e['stop_atr_mult'] == 2.0
    assert p['signal']['availability_lag_seconds'] == 300 and p['universe']['min_eligible'] == 15
    assert 2 * s['k'] < p['universe']['min_eligible']
    tb = p['trial_budget']
    assert tb['primary_hypotheses'] == len(p['primaries']) == 2
    assert tb['diagnostics'] == len(p['diagnostics']) == 1
    assert tb['baseline_rows'] == 2 * len(p['baselines']) == 6
    assert tb['total_ledger_trial_records'] == 9 == tb['primary_hypotheses'] + tb['diagnostics'] + tb['baseline_rows']
    assert {x['book'] for x in p['primaries']} == {'long', 'short'}
    assert g['fold_stability']['positive_folds_min'] == 4 and g['fold_stability']['folds'] == 5
    assert set(p['costs']['stress_rows']) >= {'base_x1', 'all_variable_costs_x1.5', 'all_variable_costs_x2'}
    assert 'funding_actual' in p['costs']['net_formula'] and '+ funding_actual' in p['costs']['net_formula']



def test_json_numbers_behind_md_needles():
    p = c()
    g = p['gates']
    assert g['expectancy']['combined_pf_gt'] == 1.10
    dd = g['drawdown_and_tail']
    assert (dd['x1_5_max_equity_dd_le'], dd['longest_underwater_days_le'], dd['worst_cohort_net_r_ge']) == (0.65, 365, -7.5)
    assert dd['tiers'] == {'ordinary': {'base_max_equity_dd_le': 0.35, 'closed_r_dd_lt': 40},
                           'profitable_high_dd': {'base_max_equity_dd_gt': 0.35, 'base_max_equity_dd_le': 0.55,
                                                  'annualized_net_return_over_max_dd_ge': 0.75,
                                                  'total_net_r_over_closed_r_dd_ge': 2.0}}
    assert (g['concentration']['max_symbol_share'], g['concentration']['max_top3_share']) == (0.25, 0.50)
    assert g['capacity']['median_notional_over_prior_24h_quote_volume_le'] == 0.001
    assert p['promotion_allocation']['max_share_of_portfolio_risk_budget'] == 0.25
    h = p['windows']['holdout']
    assert (h['start'], h['end'], h['minimum_episodes_per_side']) == ('2026-11-02T00:00:00Z', '2027-05-03T00:00:00Z', 500)
    assert p['coverage_check']['result']['funding_rows_full_months'] == 224064


# ------------------------------------------------------------------ coverage tool (synthetic fixtures)

def ms(s):
    return CV.parse_utc(s)


def entry(series, sym, month, first, last, rows=10, interval=None):
    sub = 'fundingRate' if series == 'fundingRate' else f'{series}'
    iv = '' if series == 'fundingRate' else f'{interval}/'
    name = f'{sym}-{"fundingRate" if series == "fundingRate" else interval}-{month}.zip'
    return {'bytes': 1, 'first_open_ms': ms(first), 'interval': interval, 'last_open_ms': ms(last),
            'path': f'um/monthly/{sub}/{sym}/{iv}{name}', 'rows': rows, 'series': series, 'sha256': '0' * 64,
            'source': {'kind': 'x', 'url': 'u'}, 'symbol': sym}


def full_set(sym, month, first, last, rows=10):
    return [entry('fundingRate', sym, month, first, last, rows),
            entry('klines', sym, month, first, last, rows, '4h'),
            entry('klines', sym, month, first, last, rows, '1d'),
            entry('markPriceKlines', sym, month, first, last, rows, '1h')]


def write_manifest(tmp_path, files, gz=True, indent=1):
    doc = {'available_rule': 'x', 'data_root': 'binance_um', 'digest': 'd', 'files': files, 'id': 'syn'}
    raw = json.dumps(doc, indent=indent, sort_keys=True)
    p = tmp_path / ('m.json.gz' if gz else 'm.json')
    if gz:
        with gzip.open(p, 'wt', encoding='utf-8') as f:
            f.write(raw)
    else:
        p.write_text(raw, encoding='utf-8')
    return str(p)


def test_coverage_complete_on_synthetic(tmp_path):
    files = (full_set('AAAUSDT', '2026-07', '2026-07-01T00:00:00Z', '2026-07-31T16:00:00Z', 93)
             + full_set('AAAUSDT', '2026-08', '2026-08-01T00:00:00Z', '2026-08-31T16:00:00Z', 93)
             + full_set('BBBUSDT', '2026-08', '2026-08-01T00:00:00Z', '2026-08-31T16:00:00Z', 90))
    d = CV.coverage(write_manifest(tmp_path, files), ['AAAUSDT', 'BBBUSDT'], '2026-07-01T00:00:00Z',
                    '2026-09-01T00:00:00Z')
    assert d['status'] == 'COMPLETE'
    f = d['series']['fundingRate']
    assert f['symbols_with_data'] == 2 and f['symbols_per_month'] == {'2026-07': 1, '2026-08': 2}
    assert f['per_symbol']['AAAUSDT']['rows_full_months'] == 186


def test_coverage_parses_plain_and_other_indent(tmp_path):
    files = full_set('AAAUSDT', '2026-08', '2026-08-01T00:00:00Z', '2026-08-31T16:00:00Z')
    for gz, ind in ((False, 1), (True, 2), (False, 4)):
        sub = tmp_path / f'{gz}{ind}'
        sub.mkdir()
        d = CV.coverage(write_manifest(sub, files, gz, ind), ['AAAUSDT'], '2026-08-01T00:00:00Z',
                        '2026-09-01T00:00:00Z')
        assert d['status'] == 'COMPLETE'


def test_coverage_fails_closed_on_missing_symbol_and_month_gap(tmp_path):
    files = (full_set('AAAUSDT', '2026-06', '2026-06-01T00:00:00Z', '2026-06-30T16:00:00Z')
             + full_set('AAAUSDT', '2026-08', '2026-08-01T00:00:00Z', '2026-08-31T16:00:00Z'))
    path = write_manifest(tmp_path, files)
    d = CV.coverage(path, ['AAAUSDT', 'ZZZUSDT'], '2026-06-01T00:00:00Z', '2026-09-01T00:00:00Z')
    f = d['series']['fundingRate']
    assert d['status'] == 'INCOMPLETE'
    assert f['missing_symbols'] == ['ZZZUSDT'] and f['per_symbol']['AAAUSDT']['months_missing'] == ['2026-07']
    uni = tmp_path / 'u.json'
    uni.write_text(json.dumps({'symbols': [{'symbol': 'AAAUSDT'}, {'symbol': 'ZZZUSDT'}]}), encoding='utf-8')
    assert CV.main(['--manifest', path, '--universe', str(uni), '--start', '2026-06-01T00:00:00Z',
                    '--end', '2026-09-01T00:00:00Z', '--summary']) == 3


@pytest.mark.parametrize('end', ['2026-09-28T00:00:01Z', '2026-10-01T00:00:00Z', '2027-01-01T00:00:00Z'])
def test_coverage_refuses_end_after_cutoff_before_any_read(tmp_path, end):
    missing = str(tmp_path / 'does-not-exist.json.gz')            # a read would raise FileNotFoundError instead
    with pytest.raises(CV.CoverageError, match='sealed holdout cutoff'):
        CV.coverage(missing, ['AAAUSDT'], '2026-01-01T00:00:00Z', end)
    assert CV.main(['--manifest', missing, '--universe', missing, '--end', end]) == 2


def test_holdout_entries_never_reported(tmp_path):
    files = (full_set('AAAUSDT', '2026-08', '2026-08-01T00:00:00Z', '2026-08-31T16:00:00Z', 93)
             + full_set('AAAUSDT', '2026-09', '2026-09-01T00:00:00Z', '2026-09-30T16:00:00Z', 777)   # straddles
             + full_set('AAAUSDT', '2026-10', '2026-10-01T00:00:00Z', '2026-10-31T16:00:00Z', 999))  # sealed
    d = CV.coverage(write_manifest(tmp_path, files), ['AAAUSDT'], '2026-08-01T00:00:00Z', CV.SEALED_CUTOFF)
    out = json.dumps(d)
    assert '2026-10' not in out and '999' not in out and '777' not in out
    a = d['series']['fundingRate']['per_symbol']['AAAUSDT']
    assert a['rows_full_months'] == 93 and a['straddle_months_unread'] == ['2026-09']
    assert d['sealed_skipped_entries'] == 4
    for v in d['series'].values():
        for row in v['per_symbol'].values():
            assert not set(row) & set(CV.MS_KEYS)


def test_bad_inputs_refused(tmp_path):
    bad = entry('fundingRate', 'AAAUSDT', '2026-08', '2026-08-01T00:00:00Z', '2026-08-31T00:00:00Z')
    bad['first_open_ms'] = None
    with pytest.raises(CV.CoverageError):
        CV.coverage(write_manifest(tmp_path, [bad]), ['AAAUSDT'], '2026-08-01T00:00:00Z', '2026-09-01T00:00:00Z')
    with pytest.raises(CV.CoverageError):
        CV.coverage(str(tmp_path / 'x'), ['AAAUSDT'], '2026-09-01T00:00:00Z', '2026-08-01T00:00:00Z')
    with pytest.raises(CV.CoverageError):
        CV.parse_utc('2026-08-01T00:00:00')
    mini = tmp_path / 'mini.json'
    mini.write_text(json.dumps({'files': [bad] * 20000}), encoding='utf-8')     # one line > 1 MB
    with pytest.raises(CV.CoverageError, match='minified'):
        list(CV.iter_file_entries(str(mini)))


def test_tool_never_opens_archives_or_computes_returns():
    src = (ROOT / 'tools/research/edge05_coverage.py').read_text(encoding='utf-8')
    for banned in ('zipfile', 'import manifest', 'read_archive', 'pnl', 'pit.Access', 'backtest'):
        assert banned not in src


def _dd_gate(dd, base, x15, closed_r, ann_ret, net_r, underwater, worst):
    """Reference reading of the preregistered DD tier rule (Codex 6457763292); evaluation code must match it."""
    t = dd['tiers']
    o, h = t['ordinary'], t['profitable_high_dd']
    ordinary = base <= o['base_max_equity_dd_le'] and closed_r < o['closed_r_dd_lt']
    high = (h['base_max_equity_dd_gt'] < base <= h['base_max_equity_dd_le']
            and ann_ret / base >= h['annualized_net_return_over_max_dd_ge']
            and net_r / closed_r >= h['total_net_r_over_closed_r_dd_ge'])
    ceilings = (x15 <= dd['x1_5_max_equity_dd_le'] and underwater <= dd['longest_underwater_days_le']
                and worst >= dd['worst_cohort_net_r_ge'])
    return (ordinary or high) and ceilings


@pytest.mark.parametrize('base, x15, closed_r, ann_ret, net_r, ok', [
    (0.30, 0.40, 30, 0.10, 50, True),     # ordinary
    (0.35, 0.60, 39, 0.00, 0, True),      # ordinary at the inclusive 35% edge
    (0.45, 0.60, 60, 0.40, 130, True),    # high-DD: 0.40/0.45 = 0.89 >= 0.75, 130/60 = 2.17 >= 2.0
    (0.45, 0.60, 60, 0.30, 130, False),   # high-DD: return/DD 0.67 < 0.75
    (0.45, 0.60, 60, 0.40, 110, False),   # high-DD: net R / closed-R DD 1.83 < 2.0
    (0.56, 0.60, 60, 0.90, 200, False),   # base DD > 55% fails on any path
    (0.30, 0.66, 30, 0.10, 50, False),    # x1.5 DD > 65% fails on any path
    (0.30, 0.40, 40, 0.10, 50, False),    # ordinary needs closed-R DD < 40R
])
def test_dd_tier_rule_reference_cases(base, x15, closed_r, ann_ret, net_r, ok):
    """Synthetic numbers only, no market result: the tier rule is unambiguous before any evaluation exists."""
    dd = c()['gates']['drawdown_and_tail']
    assert _dd_gate(dd, base, x15, closed_r, ann_ret, net_r, underwater=200, worst=-5) is ok
    assert _dd_gate(dd, base, x15, closed_r, ann_ret, net_r, underwater=366, worst=-5) is False
    assert _dd_gate(dd, base, x15, closed_r, ann_ret, net_r, underwater=200, worst=-8) is False


H = 3600


@pytest.mark.parametrize('grid, n', [(8, 3), (4, 6), (1, 24)])
def test_f24_window_is_defined_on_availability_and_never_needs_the_event_at_d(grid, n):
    """Cowork 6459484792 B1: T + lag in (D - 24h, D]. On every Binance grid the window holds exactly the declared count,
    the event at T = D (not yet available) is excluded and never required, and the D-24h event is in exactly one window."""
    sig = c()['signal']
    lag = sig['availability_lag_seconds']
    assert sig['expected_events_per_grid'][str(grid)] == n
    D = 1_788_220_800                                                   # 2026-09-01T00:00Z, synthetic: no data read
    events = [D - k * grid * H for k in range(0, 48 // grid + 1)]       # D, D-g, ..., D-48h
    in_window = [T for T in events if D - 24 * H < T + lag <= D]
    assert len(in_window) == n
    assert D not in in_window and D - 24 * H in in_window
    assert all(T + lag <= D for T in in_window)                         # every summed event is already available
    prev = [T for T in events if D - 48 * H < T + lag <= D - 24 * H]
    assert len(prev) == n and not set(prev) & set(in_window)           # consecutive days never double-count
    assert '[D - 24h, D)' in sig['definition'] and 'start_ms = D - 24h, end_ms = D' in sig['sequence_check']


def test_entry_bar_is_stop_checked_and_dd_non_monotonicity_is_recorded():
    p = c()
    assert 'entry bar itself' in p['execution']['stop_path'] and 'IS stop-checked' in p['execution']['stop_path']
    dd = p['gates']['drawdown_and_tail']
    assert 'not monotone' in dd['accepted_consequence']
    assert _dd_gate(dd, 0.30, 0.40, 45, 0.90, 225, underwater=200, worst=-5) is False   # better DD fails (recorded)
    assert _dd_gate(dd, 0.40, 0.40, 45, 0.40, 90, underwater=200, worst=-5) is True     # worse DD passes
