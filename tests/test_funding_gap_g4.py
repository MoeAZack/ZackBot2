"""FUND-GAP-01 G4: cadence-transition provenance / enumeration and the two terminal-coverage rules (Codex 6362587273),
plus the G3 omission contract as an explicit output-schema allowlist."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'research'))
import funding_gap_g3 as G3                                                                 # noqa: E402
import funding_gap_g4 as G                                                                  # noqa: E402
import funding_gap_scan as F                                                                # noqa: E402

H = G.HOUR
D = F.parse_utc('2022-01-03T00:00:00Z')
ENTRY, LO, HI = D + 4 * H, D + 8 * H, D + 12 * H          # the G3 SOL geometry: entry 04:00, bar 08:00-12:00


def sol_tape():
    return [(D + h * H, 2) for h in range(-8, 9, 2)] + [(D + h * H, 8) for h in (16, 24, 32)]


@pytest.mark.parametrize('a, b, gap, want', [(2, 8, 8, 'new'), (2, 8, 2, 'old'), (8, 2, 2, 'new'),
                                              (8, 2, 6, 'neither'), (4, 4, 4, 'both')])
def test_classify_gap(a, b, gap, want):
    assert G.classify_gap(a, b, gap * H) == want


@pytest.mark.parametrize('conv, cls', [('old_label', 'new'), ('new_label', 'old')])
def test_a_clean_switch_shows_its_label_convention_in_the_gap_class(conv, cls):
    ev = G.schedule(D, 2, 8, D + 8 * H, conv)
    tr = G.transitions_of([F.Row(t, h) for t, h in ev])
    assert len(tr) == 1 and tr[0]['class'] == cls and tr[0]['old_h'] == 2 and tr[0]['new_h'] == 8
    assert tr[0]['hidden_slots'] == (3 if conv == 'old_label' else 0)


def test_off_grid_rows_are_not_transitions():
    rows = [F.Row(D, 8), F.Row(D + 3 * H, 2), F.Row(D + 8 * H, 8)]          # 03:00 is off the 2h grid
    assert G.transitions_of(rows) == []


def test_the_sol_tape_fails_last_row_and_passes_lookahead1():
    x = G._series(sol_tape())
    assert G3.classify(x, ENTRY, LO, HI, True) == ('uncovered_terminal', D + 10 * H)
    assert G.covered('last_row', x, ENTRY, LO, HI, True) is False
    assert G.covered('lookahead1', x, ENTRY, LO, HI, True) is True


def test_lookahead1_only_relaxes_the_terminal():
    for ident, ev, kept, entry, lo, hi, incl in G.cases():
        x = G._series(kept)
        base = G.covered('last_row', x, entry, lo, hi, incl)
        got = G.covered('lookahead1', x, entry, lo, hi, incl)
        if base:
            assert got, ident                                   # a passing call never becomes a refusal
        elif G3.classify(x, entry, lo, hi, incl)[0] != 'uncovered_terminal':
            assert not got, ident                               # other predicates are untouched


def test_lookahead1_needs_a_valid_pair_with_the_next_row():
    tape = [(t, h) for t, h in sol_tape() if t != D + 16 * H]   # next row is now 24:00 (gap 16h: no interval)
    assert G.covered('lookahead1', G._series(tape), ENTRY, LO, HI, True) is False
    tape = [(t, h) for t, h in sol_tape() if t <= D + 8 * H]    # no row within one max interval after hi
    assert G.covered('lookahead1', G._series(tape), ENTRY, LO, HI, True) is False


def test_simulation_decides_the_sol_shape_only_given_a_label_convention():
    sim = G.simulate()
    assert sim['sol_shape'] == {'old_label': [True], 'new_label': [False]}
    for r in G.RULES:
        s = sim['rules'][r]
        assert s['cases'] > 20_000 and s['false_accept'] + s['false_reject'] > 0 and len(s['first_mismatch']) == 1
    assert sim['rules']['lookahead1']['false_reject'] < sim['rules']['last_row']['false_reject']
    assert sim['counterexamples']['same_convention']['count'] > 0
    G3.check_schema({'simulation': sim}, {'simulation': G.SCHEMA['simulation']}, G.G4Error)


def test_truth_requires_the_anchor_and_every_event_to_hi():
    ev = G.schedule(D, 2, 2, D, 'old_label')
    kept = [e for e in ev if e[0] != D + 10 * H]
    assert G.truth(ev, ENTRY, HI, True, ev) and not G.truth(ev, ENTRY, HI, True, kept)
    kept = [e for e in ev if e[0] != D + 12 * H]
    assert G.truth(ev, ENTRY, HI, False, kept) and not G.truth(ev, ENTRY, HI, True, kept)


# ------------------------------------------------------------------ omission contracts (allowlists)
def test_g4_report_schema_refuses_rates_floats_and_unlisted_keys():
    ok = {'transitions': {'symbols': {'SOLUSDT': {'rows': 3, 'last_row_utc': None, 'transitions': []}}}}
    G.assert_blind(ok)
    for bad in ({'rate': 1}, {'transitions': {'total': 1.0}},
                {'transitions': {'symbols': {'SOLUSDT': {'rows': 3, 'rate': 0.0001}}}},
                {'transitions': {'symbols': {'sol usdt': {'rows': 3}}}},
                {'provenance_rows': [{'interval_hours': True}]}):
        with pytest.raises(G.G4Error, match='omission contract'):
            G.assert_blind(bad)


def test_g3_allowlist_refuses_unlisted_keys_floats_and_bools_as_ints():
    for bad in ({'cells': [{'pnl': 1}]}, {'cells': [{'failing_ms': 1.0}]}, {'cells': [{'failing_ms': True}]},
                {'symbols': 'SOLUSDT'}, {'split': []}, {'cells': [{'censor': {'cycle_ms': 0, 'mark': 1}}]}):
        with pytest.raises(G3.G3Error, match='omission contract'):
            G3.assert_blind(bad)
    G3.assert_blind({'cells': [{'predicate': None, 'note': 'x', 'censor': {'reason': None, 'cycle_ms': None}}]})


def test_the_frozen_g3_report_still_passes_the_allowlist_unchanged():
    path = os.path.join(F.REPO, 'research_evidence', 'prereg', 'core10_fundgap_g3_report.v1.json')
    with open(path, 'rb') as f:
        raw = f.read()
    import hashlib
    assert hashlib.sha256(raw).hexdigest() == '0737c1449c65ebbc82715c44057985ca3bef170213a4767eb963a43edf193c20'
    G3.assert_blind(json.loads(raw))


# ------------------------------------------------------------------ provenance / enumeration on a synthetic store
@pytest.fixture
def store(tmp_path):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import test_range_vwap_rev_loader as LD                                                 # noqa: E402
    st = str(tmp_path / 'store')
    return LD, st, LD.build_store(st)


def test_provenance_names_the_file_line_and_raw_interval(store):
    LD, st, man = store
    f = next(f for f in man['files'] if f.get('series') == 'fundingRate' and f['symbol'] == 'AAAUSDT')
    rows = G._raw_rows(st, f)
    ln, a, b = rows[1]
    got = G.provenance(st, man, 'AAAUSDT', (F.utc(int(a)),))
    assert len(got) == 1 and got[0]['path'] == f['path'] and got[0]['sha256'] == f['sha256']
    assert got[0]['csv_line'] == ln and got[0]['raw_funding_interval_hours'] == b.decode()
    assert got[0]['interval_hours'] == int(b) and got[0]['interval_type'] == 'int'
    with pytest.raises(G.G4Error, match='stored rows'):
        G.provenance(st, man, 'AAAUSDT', (F.utc(int(a) + 30 * 60_000),))


def test_a_file_reaching_the_holdout_is_refused_unread_and_skipped_by_the_enumeration(store, monkeypatch):
    LD, st, man = store
    fs = [f for f in man['files'] if f.get('series') == 'fundingRate' and f['symbol'] == 'AAAUSDT']
    monkeypatch.setitem(fs[-1], 'last_open_ms', F.parse_utc(F.SEALED_END))
    with pytest.raises(G.G4Error, match='sealed holdout'):
        G._raw_rows(st, fs[-1])
    reads = []
    real = G._raw_rows
    monkeypatch.setattr(G, '_raw_rows', lambda s, f: reads.append(f['path']) or real(s, f))
    out = G.transitions(st, man, ['AAAUSDT'], LD.LO)
    assert out['skipped_sealed_files'] == [fs[-1]['path']] and fs[-1]['path'] not in reads
    assert out['symbols']['AAAUSDT']['rows'] > 0 and out['total'] == len(out['symbols']['AAAUSDT']['transitions'])
