"""Golden pack tiers (Codex call on 0ef7402): TIERS.json is a checked manifest - every case in exactly one tier, core keeps
every coverage class, the extended tier is marked `slow` from the manifest, and the CI wiring runs core per commit and both
tiers at the full gate. Mutations that must fail here: a case in neither tier, a case in both, an unknown id, a core entry
without a reason, a class only the extended tier exercises, an extended param without the slow mark."""
import copy, os, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from goldenlib import REPO_ROOT, schema, tiers  # noqa: E402

import test_golden as TG  # noqa: E402


def test_every_case_is_in_exactly_one_tier():
    ids = [os.path.basename(p)[:-5] for p in schema.case_paths()]
    tier_of = tiers.load(ids)
    assert sorted(tier_of) == sorted(ids)
    assert {t for t in tier_of.values()} == set(tiers.TIERS), 'both tiers hold at least one case'


def _doc():
    return copy.deepcopy(tiers.read())


def _ids():
    return [os.path.basename(p)[:-5] for p in schema.case_paths()]


@pytest.mark.parametrize('mutation', ['neither', 'both', 'unknown', 'dup', 'no_reason', 'schema', 'extra_key', 'core_list'])
def test_tier_manifest_defects_fail(mutation):
    d = _doc()
    core_id = next(iter(d['core']))
    if mutation == 'neither':
        d['extended'].pop()
    elif mutation == 'both':
        d['extended'].append(core_id)
    elif mutation == 'unknown':
        d['extended'].append('G-DOES-NOT-EXIST-01')
    elif mutation == 'dup':
        d['extended'].append(d['extended'][0])
    elif mutation == 'no_reason':
        d['core'][core_id] = ''
    elif mutation == 'schema':
        d['schema'] = 'zb-golden-tiers/0'
    elif mutation == 'extra_key':
        d['slow'] = []
    elif mutation == 'core_list':
        d['core'] = list(d['core'])
    with pytest.raises(tiers.TierError):
        tiers.check(d, _ids())


def test_a_new_case_without_a_tier_fails():
    with pytest.raises(tiers.TierError, match='NEITHER'):
        tiers.check(_doc(), _ids() + ['G-NEW-CASE-01'])


def test_core_keeps_every_coverage_class():
    """Core keeps representative long and short paths and a required case on each legacy adapter, and one case per exit code,
    fault kind, slot feature, entry type, behaviour tag and recorded known divergence the pack exercises anywhere."""
    cases = schema.load_all()
    gaps = tiers.core_gaps(cases, tiers.load([c['id'] for c in cases]))
    assert not gaps, f'classes only the extended tier exercises (move a representative case to core): {gaps}'


def test_core_gaps_detects_a_moved_representative():
    cases = schema.load_all()
    tier_of = tiers.load([c['id'] for c in cases])
    moved = dict(tier_of, **{'G-GAP-DCA-L-01': 'extended', 'G-OUTAGE-STOP-L-01': 'extended'})
    gaps = tiers.core_gaps(cases, moved)
    assert 'exit:TP_BASKET' in gaps and 'fault:exchange_outage' in gaps, gaps


def test_behaviours_and_known_divergences_are_coverage_classes():
    """Codex pre-review of 3f4abea #2: every `behaviours` tag and every recorded known divergence (adapter, divergence_id) is
    a class derived from the case file, so core must keep a representative of each."""
    cases = {c['id']: c for c in schema.load_all()}
    for c in cases.values():
        k = tiers.classes(c)
        assert {f'behaviour:{b}' for b in c['behaviours']} <= k, c['id']
        assert {tiers.divergence_class(d) for d in c['known_divergences']} <= k, c['id']
    assert 'divergence:legacy_engine:AUD07-C11' in tiers.classes(cases['G-TIME-L-02'])


def test_core_gaps_detects_a_new_behaviour_placed_only_in_extended():
    """A future causal behaviour whose only case sits in extended fails the per-commit coverage contract."""
    cases = copy.deepcopy(schema.load_all())
    tier_of = tiers.load([c['id'] for c in cases])
    ext = next(c for c in cases if tier_of[c['id']] == 'extended')
    ext['behaviours'].append('new_causal_behaviour')
    assert tiers.core_gaps(cases, tier_of) == ['behaviour:new_causal_behaviour']


def test_core_gaps_detects_a_moved_sole_behaviour_and_divergence_guard():
    """G-DAY-CAIRO-W-01 is the only core case for cairo_day / daily_halt and for the backtester's C13d divergence; moved to
    extended it opens ONLY behaviour / divergence gaps (the exit / fault / slot / entry / adapter classes stay covered)."""
    cases = schema.load_all()
    tier_of = tiers.load([c['id'] for c in cases])
    gaps = tiers.core_gaps(cases, dict(tier_of, **{'G-DAY-CAIRO-W-01': 'extended'}))
    assert gaps == ['behaviour:cairo_day', 'behaviour:daily_halt', 'divergence:legacy_backtest:AUD07-C13D'], gaps


@pytest.mark.parametrize('moved,gaps', [
    (('G-TIME-L-02',), ['divergence:legacy_engine:AUD07-C11']),       # the engine's C11 guard (its mirror S-02 is extended)
    (('G-TIME-L-01', 'G-TIME-L-02'), ['divergence:legacy_backtest:AUD07-C11', 'divergence:legacy_engine:AUD07-C11']),
    (('G-GAP-TP-S-01',), ['divergence:legacy_backtest:AUD07-TP-GAP-OPEN']),
])
def test_core_gaps_detects_a_moved_sole_divergence_guard(moved, gaps):
    """A case that is the only core guard of a recorded defect (known divergence by adapter and divergence_id) cannot leave
    core: moving it opens exactly that divergence class."""
    cases = schema.load_all()
    tier_of = tiers.load([c['id'] for c in cases])
    assert all(tier_of[m] == 'core' for m in moved)
    assert tiers.core_gaps(cases, dict(tier_of, **{m: 'extended' for m in moved})) == gaps


def test_divergence_classes_key_on_the_id_not_the_prose():
    """Codex golden r3 residual ruling point 2: re-wording ticket / finding (descriptive evidence) leaves every coverage class
    as it was; only the divergence_id is identity. (The re-wording itself is a contract change test_ledger catches, and a
    rebinding of the ID that schema.validate rejects against DIVERGENCES.json - Codex golden r5 ruling #2.)"""
    cases = copy.deepcopy(schema.load_all())
    tier_of = tiers.load([c['id'] for c in cases])
    before = [sorted(tiers.classes(c)) for c in cases]
    for c in cases:
        for d in c['known_divergences']:
            d['ticket'], d['finding'] = 'AUD-99', 'reworded: ' + d['finding']
    assert [sorted(tiers.classes(c)) for c in cases] == before
    assert tiers.core_gaps(cases, tier_of) == []


def test_core_gaps_detects_a_divergence_id_only_in_extended():
    """A divergence_id that only an extended case carries on an adapter is a core gap (renaming the core guard's ID does it)."""
    cases = copy.deepcopy(schema.load_all())
    tier_of = tiers.load([c['id'] for c in cases])
    ext = next(c for c in cases if tier_of[c['id']] == 'extended' and c['known_divergences'])
    d = ext['known_divergences'][0]
    d['divergence_id'] = 'ZZ-RENAMED-ONLY-IN-EXTENDED'
    assert tiers.core_gaps(cases, tier_of) == [f"divergence:{d['adapter']}:ZZ-RENAMED-ONLY-IN-EXTENDED"]


def test_extended_params_are_slow_and_core_params_are_not():
    """The tier, not an ad-hoc marker, decides: exactly the extended params carry `slow` (verify fast deselects them)."""
    tier_of = tiers.load()
    params = TG._params()
    assert params and all(p.id not in ('pack-invalid', 'tiers-invalid') for p in params)
    for p in params:
        case = p.values[0]
        slow = any(m.name == 'slow' for m in p.marks)
        assert slow == (tier_of[case['id']] == 'extended'), p.id
    seen = {p.values[0]['id'] for p in params}
    assert seen == set(tier_of), 'every case is a test param in its tier'


def test_a_broken_tier_manifest_is_an_unmarked_failing_param(monkeypatch):
    def broken(*a, **k):
        raise tiers.TierError('TIERS.json: cases in NEITHER tier: [x]')
    monkeypatch.setattr(tiers, 'load', broken)
    params = TG._params()
    assert [p.id for p in params] == ['tiers-invalid'] and not params[0].marks
    with pytest.raises(pytest.fail.Exception, match='tier manifest is invalid'):
        TG.test_golden_case(*params[0].values)


def test_budget_constants_are_pinned():
    assert (tiers.CORE_TARGET_S, tiers.EXTENDED_TARGET_S, tiers.HARD_STOP_S) == (30.0, 90.0, 120.0)
    assert (TG.CORE_TARGET_S, TG.EXTENDED_TARGET_S, TG.HARD_STOP_S) == (30.0, 90.0, 120.0)


def test_ci_runs_core_per_commit_and_both_tiers_at_the_full_gate():
    """verify fast (verify-fast.yml / verify-push.yml) = pytest -m "not slow" -> the core tier; verify full (verify.yml slice
    `tests`) = pytest on every test -> core + extended. `slow` is a registered marker."""
    rd = lambda *p: open(os.path.join(REPO_ROOT, *p), encoding='utf-8').read()
    v = rd('verify.py')
    assert "pytest_step(rep, 'tests (fast set: -m \"not slow\")', ['-m', 'not slow', 'tests'], 1800)" in v
    assert "('tests (all)', lambda rep: pytest_step(rep, 'tests (all)', ['tests'], 3600))" in v
    assert "'tests': ['tests (all)']" in rd('verify_ci.py')
    assert 'python verify.py fast' in rd('.github', 'workflows', 'verify-fast.yml')
    assert 'python verify.py fast' in rd('.github', 'workflows', 'verify-push.yml')
    s = rd('.github', 'workflows', 'verify.yml')
    assert 'python verify_ci.py part ${{ matrix.part }}' in s and 'part: [tests, replay2, replay1-ui]' in s
    assert "addinivalue_line('markers', 'slow:" in rd('tests', 'conftest.py')
    assert rd('pytest.ini').count('testpaths = tests') == 1
