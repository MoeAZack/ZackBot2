"""FUND-GAP-01 FG-1: the fundingRate continuity scan applies exactly range_vwap_rev's funding rules (no evaluation)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'research'))
import funding_gap_scan as F                                                                # noqa: E402

H = F.HOUR
LO = F.parse_utc('2022-01-01T00:00:00Z')


def chain(start, n, iv=8, f='a.zip'):
    return [F.Row(start + i * iv * H, iv, f) for i in range(n)]


def kinds(d):
    return [x['kind'] for x in d]


def test_a_continuous_8h_series_has_no_defect():
    rows = chain(LO, 30)
    assert F.scan_rows(rows, LO, LO + 29 * 8 * H - 1, LO) == []


def test_a_missing_event_is_one_spacing_defect_naming_the_hole():
    rows = chain(LO, 30)
    del rows[10]
    d = F.scan_rows(rows, LO, LO + 29 * 8 * H - 1, LO)
    assert kinds(d) == ['spacing']
    assert d[0]['t_ms'] == LO + 9 * 8 * H and d[0]['gap_h'] == 16 and d[0]['expected_h'] == [8]
    assert d[0]['missing_at_prev_interval'] == [F.utc(LO + 10 * 8 * H)]


def test_an_interval_change_on_the_grid_is_not_a_defect():
    """8h -> 4h on the 4h grid: the chain accepts either row's interval (same as funding_events)."""
    rows = chain(LO, 5) + chain(LO + 4 * 8 * H + 4 * H, 10, iv=4)
    assert F.scan_rows(rows, LO, rows[-1].time_ms + 3 * H, LO) == []


def test_row_level_triggers_are_split_by_cause():
    late, odd, off = F.Row(LO + 61_000, 8), F.Row(LO + 8 * H, 3), F.Row(LO + 4 * H, 8)
    assert F._classify(late) == 'time_tol'
    assert F._classify(odd) == 'interval_type'
    assert F._classify(off) == 'grid'
    assert F._classify(F.Row(LO + 59_000, 8)) is None                     # inside the 60 s tolerance
    assert F._classify(F.Row(LO, '8')) == 'interval_type'                 # not an int


def test_data_start_after_the_scan_start_is_an_edge_not_a_hole():
    rows = chain(LO + 5 * 8 * H, 10)
    d = F.scan_rows(rows, LO, rows[-1].time_ms + 7 * H, rows[0].time_ms)
    assert kinds(d) == ['data_start'] and d[0]['t_ms'] == rows[0].time_ms


def test_no_funding_file_at_all_is_reported():
    assert kinds(F.scan_rows([], LO, LO + H, None)) == ['data_start']


def test_a_series_ending_inside_the_window_is_a_next_event_defect():
    rows = chain(LO, 5)
    d = F.scan_rows(rows, LO, LO + 10 * 8 * H, LO)
    assert kinds(d) == ['next_event'] and d[0]['t_ms'] == LO + 5 * 8 * H


def test_duplicate_nominal_instant():
    rows = chain(LO, 4) + [F.Row(LO + 8 * H + 30_000, 8, 'b.zip')]
    assert 'duplicate' in kinds(F.scan_rows(rows, LO, LO + 3 * 8 * H + 7 * H, LO))


def test_scan_agrees_with_funding_events_on_a_hole():
    """The strategy's own funding_events raises on the same hole the scan names."""
    rows = chain(LO, 30)
    del rows[10]

    class X:
        funding = rows
    with pytest.raises(F.RV.Incomplete):
        F.RV.funding_events(X, LO + 9 * 8 * H + H, LO, LO + 12 * 8 * H, incl=True)
    assert kinds(F.scan_rows(rows, LO, LO + 29 * 8 * H - 1, LO)) == ['spacing']


def test_a_scan_past_the_sealed_holdout_is_refused_before_any_read(tmp_path, capsys):
    rc = F.main(['--store', str(tmp_path / 'nope'), '--symbols', 'BTCUSDT',
                 '--window', '2026-09-01T00:00:00Z/2026-09-28T00:00:01Z'])
    assert rc == 2 and 'sealed holdout' in capsys.readouterr().err
    with pytest.raises(F.ScanError, match='sealed holdout'):
        F.scan(str(tmp_path), {'files': [], 'digest': 'x'}, ['BTCUSDT'], LO, F.parse_utc('2026-10-01T00:00:00Z'))


def test_bad_window_is_exit_2(tmp_path):
    assert F.main(['--store', str(tmp_path), '--symbols', 'BTCUSDT', '--window', '2022-01-01T00:00:00Z']) == 2
    assert F.main(['--store', str(tmp_path), '--symbols', 'BTCUSDT',
                   '--window', '2022-02-01T00:00:00Z/2022-01-01T00:00:00Z']) == 2


# ------------------------------------------------------------------ the frozen FG-1 report (no store needed)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _json(rel):
    import json
    with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
        return json.load(f)


def test_frozen_report_is_bound_to_the_core10_wf0_inputs():
    r = _json('research_evidence/prereg/core10_fundgap_report.v1.json')
    g = _json('research_evidence/prereg/core10_gapfill_report.v1.json')
    wf0 = next(s for s in g['segments'] if s['key'] == 'walk_forward[0]')
    assert r['format'] == F.FORMAT and r['manifest_digest'] == g['manifest_digest']
    assert (F.parse_utc(r['window']['lo']), F.parse_utc(r['window']['hi'])) == (wf0['lo'], wf0['hi'])
    assert sorted(r['symbols']) == sorted(g['symbols'])
    assert r['defect_count'] == sum(len(v['defects']) for v in r['symbols'].values())
    assert r['provenance']['clean'] is True and len(r['provenance']['commit']) == 40
    assert F.parse_utc(r["window"]["hi"]) <= F.parse_utc(F.SEALED_END)
    kinds = {d['kind'] for v in r['symbols'].values() for d in v['defects']}
    assert kinds <= {'time_tol', 'interval_type', 'grid', 'duplicate', 'spacing', 'data_start', 'next_event'}
