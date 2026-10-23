"""FUND-GAP-01 G3: the first-failure proof names the exact funding_events predicate, binds to the real evaluator path,
reads like the real Access path, refuses the holdout and never emits a rate or a result (Codex 6358557359)."""
import collections
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'research'))
import funding_gap_g3 as G                                                                  # noqa: E402
import funding_gap_scan as F                                                                # noqa: E402
import range_vwap_rev as RV                                                                 # noqa: E402

H = G.HOUR
T0 = F.parse_utc('2022-01-03T00:00:00Z')
Row = collections.namedtuple('Row', 'time_ms rate interval_hours available_ms')


def ser(times, iv=8):
    return RV.Series({}, {}, tuple(Row(t, 0.000123, iv, t) for t in times), {})


def chain(n, start=T0, iv=8):
    return [start + k * iv * H for k in range(n)]


def real_raise(x, entry, lo, hi, incl):
    with pytest.raises(RV.Incomplete) as e:
        RV.funding_events(x, entry, lo, hi, incl=incl)
    return e.value.first_ms


ENTRY, BO = T0 + 24 * H, T0 + 32 * H                 # entry at a funding open; one 4h bar [BO, BO + 4h)


@pytest.mark.parametrize('name, times, want', [
    ('off_grid', chain(6) + [T0 + 28 * H + 30 * 60_000], T0 + 28 * H + 30 * 60_000),
    ('missing_anchor', [t for t in chain(8) if t > ENTRY], ENTRY),
    ('nonconsecutive_pair', [t for t in chain(8) if t != T0 + 24 * H], T0 + 16 * H),
    ('uncovered_terminal', chain(4), T0 + 32 * H),
])
def test_each_predicate_is_named_at_the_same_time_as_the_real_raise(name, times, want):
    x = ser(times)
    hi = BO + 4 * H
    got = G.classify(x, ENTRY, BO, hi, True)
    assert got == (name, want)
    assert real_raise(x, ENTRY, BO, hi, True) == want


def test_a_passing_call_has_no_predicate():
    x = ser(chain(12))
    assert G.classify(x, ENTRY, BO, BO + 4 * H, True) is None
    RV.funding_events(x, ENTRY, BO, BO + 4 * H, incl=True)


def test_exclusive_terminal_bound_differs_from_inclusive():
    x = ser(chain(6))                                 # last event T0+40h; next due T0+48h
    hi = T0 + 48 * H
    assert G.classify(x, ENTRY, T0 + 44 * H, hi, True) == ('uncovered_terminal', hi)
    assert G.classify(x, ENTRY, T0 + 44 * H, hi, False) is None


def _fake_book(x, entry, lo, hi):
    """A stand-in for run_book that reaches funding_events through the module global, like bar_funding does."""
    def book(tape, cfg, side, *a, row='base', mode=None, **kw):
        try:
            RV.funding_events(x, entry, lo, hi, incl=True)
            return {'trades': [{'net': 1.5, 'r': 0.3}], 'status': RV.COMPLETE}
        except RV.Incomplete as e:
            return {'trades': [{'net': -2.0, 'r': -0.4, 'qty': 3.0}], 'status': RV.INCOMPLETE, 'wallet_end': 99.0,
                    'censor': {'reason': e.reason, 'class': 'funding_rate_gap', 'symbol': 'AAAUSDT', 'phase': e.phase,
                               'cycle_ms': hi, 'first_unaccountable_ms': e.first_ms, 'last_valid_ms': lo}}
    return book


def test_capture_binds_the_target_cell_and_stubs_the_rest(monkeypatch):
    x = ser([t for t in chain(8) if t != T0 + 24 * H])
    monkeypatch.setattr(RV, 'run_book', _fake_book(x, ENTRY, BO, BO + 4 * H))
    cfg = object()
    target = (G.SPLIT, 'long', 'CFG', 'base')
    with G.capture([{'cfg': cfg, 'mode': 'x1', 'name': 'CFG'}], cells=(target,)) as got:
        hit = RV.run_book({}, cfg, 'long', row='base', mode='x1')
        stub = RV.run_book({}, cfg, 'short', row='base', mode='x1')
    assert hit['status'] == RV.INCOMPLETE and stub == {'trades': [], 'status': RV.COMPLETE}
    assert list(got) == [target] and got[target]['call']['first_ms'] == T0 + 16 * H
    rec = G.cell_record(target, got[target])
    assert rec['predicate'] == 'nonconsecutive_pair' and rec['causal'] is True
    assert rec['failing_utc'] == F.utc(T0 + 16 * H) and rec['inclusive'] is True
    assert RV.run_book is not None and G.RV.funding_events is RV.funding_events     # hooks removed on exit


def test_the_report_omits_rates_and_results(monkeypatch):
    x = ser([t for t in chain(8) if t != T0 + 24 * H])
    monkeypatch.setattr(RV, 'run_book', _fake_book(x, ENTRY, BO, BO + 4 * H))
    cfg, target = object(), (G.SPLIT, 'long', 'CFG', 'base')
    with G.capture([{'cfg': cfg, 'mode': 'x1', 'name': 'CFG'}], cells=(target,)) as got:
        RV.run_book({}, cfg, 'long', row='base', mode='x1')
    doc = {'cells': [G.cell_record(target, got[target])]}
    G.assert_blind(doc)
    text = json.dumps(doc)
    assert '0.000123' not in text and '1.23e-04' not in text           # no funding rate value
    for k in ('"rate"', '"net"', '"r"', '"qty"', '"trades"', '"wallet', '"mark'):
        assert k not in text
    assert doc['cells'][0]['nearby_rows'] and set(doc['cells'][0]['nearby_rows'][0]) == {
        'time_ms', 'utc', 't_nom_ms', 't_nom_utc', 'interval_hours', 'interval_type'}
    with pytest.raises(G.G3Error, match='omission contract'):
        G.assert_blind({'cells': [{'nearby_rows': [{'rate': 1}]}]})


def test_a_classifier_disagreement_refuses():
    x = ser(chain(12))                                # passes: but the capture claims a raise
    rec = {'call': dict(x=x, entry_open=ENTRY, lo=BO, hi=BO + 4 * H, incl=True, first_ms=ENTRY), 'censor': {}}
    with pytest.raises(G.G3Error, match='disagrees'):
        G.cell_record((G.SPLIT, 'long', 'CFG', 'base'), rec)


# ------------------------------------------------------------------ mirror == the real ledgered Access path
@pytest.fixture
def syn(tmp_path, monkeypatch):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pit_run as PR                                                                    # noqa: E402
    import test_range_vwap_rev_loader as LD                                                 # noqa: E402
    monkeypatch.setattr(PR, '_git', LD.fake_git())
    store = str(tmp_path / 'store')
    man = LD.build_store(store)
    ds = PR.StaticDataset(man, store, LD.static(), LD.SYMS)
    return {'PR': PR, 'LD': LD, 'store': store, 'man': man, 'ds': ds, 'tmp': tmp_path}


def test_the_mirrored_tape_equals_the_real_access_tape(syn):
    PR, LD = syn['PR'], syn['LD']
    art = LD.artifact()
    notice = str(syn['tmp'] / 'notice.json')
    with open(notice, 'w', newline='\n') as f:
        json.dump(art, f)
    ledger = str(syn['tmp'] / 'ledger' / 'range_vwap_rev.jsonl')
    os.makedirs(os.path.dirname(ledger))
    src = {'manifest': syn['man'], 'static': LD.static(), 'rules': LD.RULES, 'ledger': ledger}
    env = PR.rex_load(src, repo=LD.ROOT, store=syn['store'], tier='CORE-10', window=LD.WINDOW, notice_path=notice,
                      notice_sha256=art['digest'], author='t', cairo_date='2026-10-23', plan=PR.rex_plan(LD.ROOT))
    lo, hi = LD.LO + 10 * 86_400_000, LD.HI - 10 * 86_400_000          # an inner segment, its own access window
    for s in LD.SYMS:
        real = PR.rex_clip(env['tape'][s], lo, hi)
        mine = PR.rex_clip(G.series_of(syn['ds'], s, LD.RULES[s], lo, hi + PR.REX_FUNDING_TAIL_MS), lo, hi)
        assert real.bars and real.marks and real.funding
        assert mine.bars == real.bars and mine.marks == real.marks and mine.funding == real.funding
        assert mine.rule == real.rule


def test_a_holdout_file_on_the_mirror_path_refuses(syn, monkeypatch):
    LD = syn['LD']
    f0 = [f for f in syn['ds'].files('klines', 'AAAUSDT', '4h') if f['first_open_ms'] <= LD.HI][-1]
    monkeypatch.setitem(f0, 'last_open_ms', F.parse_utc(F.SEALED_END))
    with pytest.raises(G.G3Error, match='sealed holdout'):
        G.series_of(syn['ds'], 'AAAUSDT', LD.RULES['AAAUSDT'], LD.LO, LD.HI)


def test_cells_are_the_four_known_wf0_incomplete_funding_cells():
    assert G.CELLS == (('walk_forward[0]', 'long', 'range_vwap_rev.v1.rg+0.5', 'base'),
                       ('walk_forward[0]', 'long', 'range_vwap_rev.v1.rg+0.5', 'fees_slip_x2'),
                       ('walk_forward[0]', 'long', 'B2', 'base'),
                       ('walk_forward[0]', 'long', 'B2', 'fees_slip_x2'))
    import pit_run as PR                                                                    # noqa: E402
    assert {c['name'] for c in PR.rex_configs()} >= {'range_vwap_rev.v1.rg+0.5', 'B2'}


def test_the_frozen_report_is_blind_and_binds_the_four_cells():
    path = os.path.join(F.REPO, 'research_evidence', 'prereg', 'core10_fundgap_g3_report.v1.json')
    with open(path, encoding='utf-8') as f:
        doc = json.load(f)
    G.assert_blind(doc)
    assert doc['format'] == G.FORMAT and doc['split']['key'] == G.SPLIT and doc['variant'] == G.VARIANT
    assert [(c['split'], c['side'], c['config'], c['row']) for c in doc['cells']] == [list(k) for k in G.CELLS] or \
        [tuple((c['split'], c['side'], c['config'], c['row'])) for c in doc['cells']] == list(G.CELLS)
    assert all(c['predicate'] in G.PREDICATES and c['causal'] for c in doc['cells'])
    assert doc['provenance']['clean'] and len(doc['provenance']['commit']) == 40
    assert all(f['path'] and len(f['sha256']) == 64 for f in doc['files_read'])
    assert doc['access_window']['hi'] < F.SEALED_END
