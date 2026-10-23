"""FUND-GAP-01 FG-1: the fundingRate continuity scan applies exactly range_vwap_rev's funding rules (no evaluation)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'research'))
import funding_gap_scan as F                                                                # noqa: E402
import pit as P                                                                             # noqa: E402

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


def test_a_symbol_listed_after_the_window_is_labelled_non_causal():
    d = F.scan_rows([], LO, LO + 10 * 8 * H, LO + 50 * 8 * H)
    assert kinds(d) == ['data_start'] and d[0]['in_window'] is False and 'non-causal' in d[0]['detail']
    d = F.scan_rows(chain(LO + 2 * 8 * H, 20), LO, LO + 10 * 8 * H, LO + 2 * 8 * H)
    assert kinds(d) == ['data_start'] and d[0]['in_window'] is True        # listed inside the window: causal


def test_clip_anchor_checks_the_rex_clip_view_not_the_store():
    """Cowork G1: rex_clip keeps time_ms >= window lo, so a store row before lo is invisible to the evaluator."""
    first, span_lo, hi = LO - 16 * H, LO - 8 * H - 60_000, LO + 10 * 8 * H
    rows = chain(LO - 8 * H, 12)
    assert F.scan_rows(rows, span_lo, hi, first, clip_lo=LO) == []
    early = [F.Row(r.time_ms - 30_000, 8) if r.time_ms == LO else r for r in rows]  # the LO event 30 s early
    assert F.scan_rows(early, span_lo, hi, first, clip_lo=LO) == []    # clipped, but LO+8h still anchors in time
    hole = [r for r in early if r.time_ms != LO + 8 * H]                # ...and the next event is missing too
    d = F.scan_rows(hole, span_lo, hi, first, clip_lo=LO)
    assert kinds(d) == ['clip_anchor', 'spacing'] and d[0]['t_ms'] == LO + 16 * H
    assert kinds(F.scan_rows(hole, span_lo, hi, first)) == ['spacing']  # the store-only view misses clip_anchor


def test_no_funding_file_at_all_is_reported():
    d = F.scan_rows([], LO, LO + H, None)
    assert kinds(d) == ['data_start'] and d[0]['in_window'] is False


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
    assert kinds <= {'time_tol', 'interval_type', 'grid', 'duplicate', 'spacing', 'data_start', 'next_event',
                     'clip_anchor'}
    assert r['in_window_defect_count'] == sum(d['in_window'] for v in r['symbols'].values() for d in v['defects'])
    hi = F.parse_utc(r['window']['hi'])
    for v in r['symbols'].values():                      # a data_start after the window end is labelled non-causal
        for d in v['defects']:
            assert d['in_window'] is not (d['kind'] == 'data_start' and d['t_ms'] > hi)
    assert r['evaluator_mismatch_symbols'] == []          # G2: the evaluator path delivers exactly the scanned rows
    for v in r['symbols'].values():
        q = v['evaluator_equivalence']
        assert q['equal'] is True and q['first_mismatch'] is None
        assert q['scanner_rows'] == q['evaluator_rows'] and q['scanner_digest'] == q['evaluator_digest']


# ------------------------------------------------------------------ G2: evaluator-path row equivalence
def fund(t, ih=8):
    return P.Funding(t, 0.0001, ih, t)


def test_equivalence_holds_on_the_same_rows_and_ignores_rows_outside_the_clip():
    rows = chain(LO - 16 * H, 12)                         # two rows before lo: never delivered by rex_clip
    hi = LO + 7 * 8 * H
    got = [fund(r.time_ms) for r in rows if LO <= r.time_ms <= hi + F.RV.FUNDING_TOL]
    q = F.equivalence(rows, got, LO, hi)
    assert q['equal'] and q['scanner_rows'] == q['evaluator_rows'] == 8 and q['first_mismatch'] is None


def test_equivalence_fails_on_a_dropped_evaluator_row():
    rows = chain(LO, 10)
    got = [fund(r.time_ms) for r in rows]
    del got[4]
    q = F.equivalence(rows, got, LO, LO + 9 * 8 * H)
    assert not q['equal'] and (q['scanner_rows'], q['evaluator_rows']) == (10, 9)
    assert q['first_mismatch'] == {'index': 4, 'scanner': [LO + 32 * H, 8, 'int'], 'evaluator': [LO + 40 * H, 8, 'int']}


def test_equivalence_fails_on_a_retyped_interval():
    rows = chain(LO, 10)
    got = [fund(r.time_ms, 8.0 if i == 6 else 8) for i, r in enumerate(rows)]
    q = F.equivalence(rows, got, LO, LO + 9 * 8 * H)
    assert not q['equal'] and q['scanner_rows'] == q['evaluator_rows'] == 10
    assert q['first_mismatch']['evaluator'] == [LO + 48 * H, 8.0, 'float'] and q['scanner_digest'] != q['evaluator_digest']


@pytest.fixture
def syn(tmp_path, monkeypatch):
    """The REX-02 loader's synthetic archive store (tests/test_range_vwap_rev_loader.py), tmp dirs only."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pit_run as PR                                                                    # noqa: E402
    import test_range_vwap_rev_loader as LD                                                 # noqa: E402
    monkeypatch.setattr(PR, '_git', LD.fake_git())
    store = str(tmp_path / 'store')
    man = LD.build_store(store)
    ds = PR.StaticDataset(man, store, LD.static(), LD.SYMS)
    return {'PR': PR, 'LD': LD, 'store': store, 'man': man, 'ds': ds, 'tmp': tmp_path}


def test_the_mirrored_read_equals_the_real_ledgered_access_path(syn):
    """evaluator_funding == rex_clip(rex_series(Access window)) for the full window and for an inner split (the
    access window does not change what rex_clip delivers)."""
    PR, LD = syn['PR'], syn['LD']
    art = LD.artifact()
    notice = str(syn['tmp'] / 'notice.json')
    with open(notice, 'w', newline='\n') as f:
        json.dump(art, f)
    ledger = str(syn['tmp'] / 'ledger' / 'range_vwap_rev.jsonl')
    os.makedirs(os.path.dirname(ledger))
    src = {'manifest': syn['man'], 'static': LD.static(), 'rules': LD.RULES, 'ledger': ledger}
    env = PR.rex_load(src, repo=LD.ROOT, store=syn['store'], tier='CORE-10', window=LD.WINDOW, notice_path=notice,
                      notice_sha256=art['digest'], author='t', cairo_date='2026-10-21', plan=PR.rex_plan(LD.ROOT))
    inner = (LD.LO + 10 * 86_400_000, LD.HI - 10 * 86_400_000)
    for s in LD.SYMS:
        for lo, hi in ((LD.LO, LD.HI), inner):
            real = PR.rex_clip(env['tape'][s], lo, hi).funding
            assert real and F.evaluator_funding(syn['ds'], s, lo, hi, acc_lo=LD.LO, acc_hi=LD.HI) == real
            assert F.evaluator_funding(syn['ds'], s, lo, hi) == real


def test_scan_reports_equivalence_and_names_a_dropped_or_retyped_evaluator_row(syn, monkeypatch):
    LD, ds = syn['LD'], syn['ds']
    rep = F.scan(syn['store'], syn['man'], list(LD.SYMS), LD.LO, LD.HI, ds=ds)
    assert rep['evaluator_mismatch_symbols'] == [] and rep['in_window_defect_count'] == 0
    assert all(v['evaluator_equivalence']['equal'] and v['evaluator_equivalence']['evaluator_rows'] > 100
               for v in rep['symbols'].values())
    real = type(ds).slice

    def drop(self, f, lo, t):                             # the evaluator path loses one AAAUSDT row
        out = real(self, f, lo, t)
        return [r for r in out if not (f['symbol'] == 'AAAUSDT' and r.time_ms == LD.LO + 8 * H + 3)]
    monkeypatch.setattr(type(ds), 'slice', drop)
    rep = F.scan(syn['store'], syn['man'], list(LD.SYMS), LD.LO, LD.HI, ds=ds)
    assert rep['evaluator_mismatch_symbols'] == ['AAAUSDT']
    q = rep['symbols']['AAAUSDT']['evaluator_equivalence']
    assert q['evaluator_rows'] == q['scanner_rows'] - 1
    assert q['first_mismatch']['scanner'] == [LD.LO + 8 * H + 3, 8, 'int']

    def retype(self, f, lo, t):                           # ... or delivers one BBBUSDT interval as a float
        out = real(self, f, lo, t)
        return [r._replace(interval_hours=float(r.interval_hours))
                if f['symbol'] == 'BBBUSDT' and r.time_ms == LD.LO + 16 * H + 3 else r for r in out]
    monkeypatch.setattr(type(ds), 'slice', retype)
    rep = F.scan(syn['store'], syn['man'], list(LD.SYMS), LD.LO, LD.HI, ds=ds)
    assert rep['evaluator_mismatch_symbols'] == ['BBBUSDT']
    assert rep['symbols']['BBBUSDT']['evaluator_equivalence']['first_mismatch']['evaluator'][2] == 'float'


def test_an_evaluator_path_file_in_the_holdout_is_refused(syn, monkeypatch):
    LD = syn['LD']
    f0 = [f for f in syn['ds'].files('fundingRate', 'AAAUSDT', None) if f['first_open_ms'] <= LD.HI][-1]
    monkeypatch.setitem(f0, 'last_open_ms', F.parse_utc(F.SEALED_END))
    with pytest.raises(F.ScanError, match='sealed holdout'):
        F.evaluator_funding(syn['ds'], 'AAAUSDT', LD.LO, LD.HI)
