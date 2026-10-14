"""STRAT-02-PIT-01 (Codex contract): `run_r4.py pit` routed by the committed gate candidate to an immutable short
profile in pit_run (`SHORT_RUN`). Synthetic / portable fixtures only: no real store row, no holdout, no real run.
Covers routing, exact profile / config selection, program Holm n = 35 (own-family 17 reported, never gating), no
uncapped trial, no data on a gate failure, identity / resume isolation of long vs short, CORE tier order, and
INCOMPLETE refusing evidence / report / readiness. STRAT-01 behaviour is the default and is unchanged.
"""
import dataclasses
import glob
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
sys.path.insert(0, os.path.join(ROOT, 'tests'))
import ledger as L                                                                          # noqa: E402
import pit as P                                                                             # noqa: E402
import pit_eval as PE                                                                       # noqa: E402
import pit_run as PR                                                                        # noqa: E402
import progress as PG                                                                       # noqa: E402
import r4_readiness as RD                                                                   # noqa: E402
import report as RP                                                                         # noqa: E402
import run_r4 as G                                                                          # noqa: E402
import splits as S                                                                          # noqa: E402
import static_universe as SU                                                                # noqa: E402
import test_strat01_r4b_pit as F                                                            # noqa: E402

SHORT = PR.SHORT_RUN
MULT = {'program': 35, 'by_family': {'trend_ema_mom': 18, 'trend_ema_mom_short': 17}}
AUTH = ('t', '2026-10-14')


def _prereg(gate):
    return json.loads(G._load(ROOT, json.loads(G._load(ROOT, gate))['prereg']))


# ------------------------------------------------------------------ routing (run_r4.run_pit, CLI)
@pytest.fixture
def wired(monkeypatch):
    """run_pit with the gate stubbed OPEN and the data path recorded (nothing real is read)."""
    seen = {'gate': [], 'env': [], 'tiers': []}
    monkeypatch.setattr(G, 'refuse_unless_open', lambda repo, **kw: seen['gate'].append(kw.get('gate_path', G.GATE)))
    monkeypatch.setattr(PR, 'real_env', lambda repo, **kw: seen['env'].append(kw) or {'kw': kw})
    monkeypatch.setattr(PR, 'run_tiers', lambda env, out, **kw: seen['tiers'].append(out) or [])
    return seen


def test_run_pit_routes_by_the_committed_gate_candidate(wired):
    kw = dict(store='nowhere', out_dir='x', author=AUTH[0], cairo_date=AUTH[1])
    G.run_pit(ROOT, candidate=G.SHORT_ID, **kw)
    assert wired['gate'] == [G.SHORT_GATE]
    (e,) = wired['env']
    assert e['gate_path'] == G.SHORT_GATE and e['profile'] is SHORT
    G.run_pit(ROOT, **kw)                                                # default: STRAT-01, byte-for-byte unchanged
    assert wired['gate'][-1] == G.GATE and 'profile' not in wired['env'][-1]
    G.run_pit(ROOT, candidate=G.LONG.candidate_id, **kw)
    assert wired['gate'][-1] == G.GATE and 'profile' not in wired['env'][-1]


def test_a_cross_wired_gate_or_candidate_refuses(wired):
    kw = dict(store='nowhere', out_dir='x', author=AUTH[0], cairo_date=AUTH[1])
    with pytest.raises(G.GateError, match='only from its own gate'):
        G.run_pit(ROOT, candidate=G.SHORT_ID, gate_kw={'gate_path': G.GATE}, **kw)
    assert wired['gate'] == []                                           # refused before the gate, nothing read
    with pytest.raises(G.GateError, match=f'routes {G.SHORT_ID}'):      # the short gate never runs the long profile
        G.run_pit(ROOT, gate_kw={'gate_path': G.SHORT_GATE}, **kw)
    with pytest.raises(G.GateError, match='unknown candidate'):
        G.run_pit(ROOT, candidate='nope.v1', **kw)
    assert wired['env'] == [] and wired['tiers'] == []


def test_a_closed_short_gate_reads_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(G, 'gate', lambda repo, **kw: calls.append(kw['gate_path']) or ['G1 dirty working tree'])
    for name in ('real_env', 'run_tiers', 'evaluate_tier', 'StaticDataset'):
        monkeypatch.setattr(PR, name, lambda *a, n=name, **kw: pytest.fail(f'{n} reached on a closed gate'))
    monkeypatch.setattr(P, 'Access', lambda *a, **kw: pytest.fail('a split was opened on a closed gate'))
    with pytest.raises(G.GateError, match='R4 gate closed'):
        G.run_pit(ROOT, store='nowhere', out_dir='x', author=AUTH[0], cairo_date=AUTH[1], candidate=G.SHORT_ID)
    assert G.main(['pit', '--candidate', G.SHORT_ID, '--store', 'nowhere', '--author', AUTH[0],
                   '--cairo-date', AUTH[1]]) == 3
    assert calls == [G.SHORT_GATE, G.SHORT_GATE]


def test_the_cli_routes_the_candidate_and_its_own_default_out_dir(monkeypatch):
    got = []
    monkeypatch.setattr(G, 'run_pit', lambda repo, **kw: got.append(kw) or [])
    base = ['pit', '--store', 'nowhere', '--author', AUTH[0], '--cairo-date', AUTH[1]]
    assert G.main(base + ['--candidate', G.SHORT_ID]) == 0 and G.main(base) == 0
    assert got[0]['candidate'] == G.SHORT_ID and got[0]['out_dir'].endswith(os.path.join('runs', 'r4b-short'))
    assert got[1]['candidate'] == G.LONG.candidate_id and got[1]['out_dir'].endswith(os.path.join('runs', 'r4b'))
    with pytest.raises(SystemExit):
        G.main(base + ['--candidate', 'nope.v1'])


# ------------------------------------------------------------------ the immutable profile and its configurations
def test_the_short_profile_is_immutable_and_matches_the_registration():
    with pytest.raises(dataclasses.FrozenInstanceError):
        SHORT.seed = 1
    assert (SHORT.candidate_id, SHORT.family, SHORT.primary) == (G.SHORT_ID, G.SHORT_FAMILY, G.SHORT_PRIMARY_NAME)
    assert (SHORT.side, SHORT.function, SHORT.seed, SHORT.dev_only) == ('short', 'tape_short', G.SHORT_SEED, None)
    assert SHORT.seed == 20261012 and callable(getattr(PE, SHORT.function))
    assert SHORT.summary_format != PR.SUMMARY_FORMAT and SHORT.detail_format != PR.DETAIL_FORMAT
    assert PR.RUN_PROFILES == {G.LONG.candidate_id: PR.LONG_RUN, G.SHORT_ID: SHORT}
    long = PR.LONG_RUN                                                   # STRAT-01 unchanged
    assert (long.function, long.seed, long.summary_format, long.configs) == ('tape', PR.SEED, PR.SUMMARY_FORMAT,
                                                                           PR.configs)


def test_exact_short_config_selection_and_no_uncapped_trial():
    pre = _prereg(G.SHORT_GATE)
    cfgs = SHORT.configs()
    names = [c['name'] for c in cfgs]
    assert names == [G.SHORT_PRIMARY_NAME] + pre['neighbour_grid']['configs'] + ['B1', 'B2'] and len(names) == 15
    assert not any('uncapped' in n for n in names) and all(c['role'] != 'development_only' for c in cfgs)
    assert all(c['cap'] == PR.CAP for c in cfgs if c['kind'] == 'sig')
    sha = G.hashlib.sha256(G._load(ROOT, G.SHORT_PREREG)).hexdigest()
    trials = G.trial_records(pre, sha, G.SHORT_PREREG)
    run = [r['detail'].get('config') for r in trials if r['kind'] in ('variant', 'grid_point')]
    assert run == names[:13] and len(trials) == G.SHORT_TRIALS == 17      # + B0..B3 (B0 / B3 are not configs)


def test_the_real_short_environment_derives_from_committed_files_only(tmp_path):
    env = PR.real_env(ROOT, store=str(tmp_path / 'no-store'), author=AUTH[0], cairo_date=AUTH[1],
                      gate_path=G.SHORT_GATE, profile=SHORT)
    assert not os.path.exists(tmp_path / 'no-store')                     # no data row read; metadata only
    rc = env['run_config']
    assert env['profile'] is SHORT and rc['profile'] == SHORT.binding() and rc['seed'] == G.SHORT_SEED
    assert rc['configs'] == [c['name'] for c in SHORT.configs()]
    assert env['n_trials'] == rc['n_trials'] == 35 and env['multiplicity'] == MULT == rc['multiplicity']
    assert os.path.basename(env['ledger']) == 'trend_ema_mom_short.jsonl'
    assert env['identity']['prereg'] == G.hashlib.sha256(G._load(ROOT, G.SHORT_PREREG)).hexdigest()
    PG.check_identity(env['identity'])
    long = PR.real_env(ROOT, store=str(tmp_path / 'no-store'), author=AUTH[0], cairo_date=AUTH[1], gate_path=G.GATE)
    assert long['n_trials'] == 18 and 'profile' not in long and 'profile' not in long['run_config']
    assert env['identity']['evaluator'] != long['identity']['evaluator']
    assert env['identity']['prereg'] != long['identity']['prereg']
    for tier in PR.RUN_TIERS:                                             # same frozen tiers, never-aliasing run ids
        assert env['tiers'][tier] == long['tiers'][tier]
        assert PG.run_id(env['identity'], PR.tier_config(env, tier)) != PG.run_id(long['identity'],
                                                                                  PR.tier_config(long, tier))


def test_real_env_refuses_a_cross_wired_profile(tmp_path):
    kw = dict(store=str(tmp_path / 'x'), author=AUTH[0], cairo_date=AUTH[1])
    with pytest.raises(PR.PitRunError, match='not the trend_ema_mom.v1 profile'):
        PR.real_env(ROOT, gate_path=G.SHORT_GATE, **kw)
    with pytest.raises(PR.PitRunError, match='not the trend_ema_mom_short.v1 profile'):
        PR.real_env(ROOT, gate_path=G.GATE, profile=SHORT, **kw)
    with pytest.raises(PR.PitRunError, match='unknown run profile'):
        PR.real_env(ROOT, gate_path=G.SHORT_GATE, profile=dataclasses.replace(SHORT, seed=1), **kw)


# ------------------------------------------------------------------ program Holm multiplicity (n = 35)
def test_short_holm_gates_on_the_program_family_never_the_long_or_own_count():
    env = {'n_trials': 35, 'multiplicity': MULT}
    m = PR.program_multiplicity(env, SHORT)
    assert m['n_trials'] == 35 and m['own_family_n_trials'] == 17 and m['own_family_gating'] is False
    assert m['program_families'] == MULT['by_family'] and m['gating_family'] == 'program'
    bad = [{'n_trials': 35},                                              # absent: never substituted
           {'n_trials': 18, 'multiplicity': MULT},                        # the long family count
           {'n_trials': 17, 'multiplicity': MULT},                        # the own-family count
           {'n_trials': 18, 'multiplicity': {'program': 18, 'by_family': {'trend_ema_mom': 18}}},
           {'n_trials': 34, 'multiplicity': {'program': 34, 'by_family': {'trend_ema_mom': 18,
                                                                         'trend_ema_mom_short': 16}}},
           {'n_trials': 36, 'multiplicity': {'program': 36, 'by_family': MULT['by_family']}}]
    for e in bad:
        with pytest.raises(PR.PitRunError, match='program family'):
            PR.program_multiplicity(e, SHORT)
    later = {'program': 36, 'by_family': {'trend_ema_mom': 19, 'trend_ema_mom_short': 17}}
    assert PR.program_multiplicity({'n_trials': 36, 'multiplicity': later}, SHORT)['n_trials'] == 36  # only raises
    assert PR.program_multiplicity({'n_trials': 18}, PR.LONG_RUN) == {'status': 'PASS', 'n_trials': 18}


# ------------------------------------------------------------------ holdout + the 2026-09-28 -> 2026-10-19 gap
def test_the_short_plan_opens_development_splits_only_and_never_the_gap():
    sp = _prereg(G.SHORT_GATE)['splits']
    plan = S.SplitPlan(sp['splits'], interval=sp['interval'], lookback_bars=sp['lookback_bars'],
                       horizon_bars=sp['horizon_bars'])
    keys = PR.dev_keys(plan)
    assert keys == ['train'] + [f'walk_forward[{i}]' for i in range(5)]
    gap0, h0 = S.parse_utc('2026-09-28T00:00:00Z'), S.parse_utc('2026-10-19T00:00:00Z')
    assert PR.holdout_start(plan) == h0
    assert max(PR.dev_window(plan, k)[1] for k in keys) == gap0          # no dev window reaches into the gap
    for k in ('holdout', 'calibration'):
        with pytest.raises(PR.PitRunError, match='development splits only'):
            PR.dev_window(plan, k)


# ------------------------------------------------------------------ orchestration: identity / resume / tier order
def _env(profile=None, **over):
    rc = {'configs': ['x'], 'stress_rows': ['base'], 'seed': 1, 'resamples': 10, **over}
    if profile is not None:
        rc['profile'] = profile.binding()
    ident = {'commit': '1' * 40, 'evaluator': PR.sha(rc), 'manifest': '2' * 64, 'universe': '3' * 64,
             'classification': None, 'prereg': None}
    env = {'tiers': {'CORE-10': ['A'], 'CORE-20': ['A', 'B']}, 'run_config': rc, 'identity': ident}
    return dict(env, profile=profile, folds=5) if profile is not None else env


@pytest.fixture
def stub(monkeypatch):
    seen = []

    def ev(env, tier, prog):
        seen.append((env.get('profile', PR.LONG_RUN).side, tier))
        prog.stage_start('pit.development')
        prog.stage_done('pit.development')
        if env.get('fail') == tier:
            raise P.PITError('integrity')
        if env.get('boom') == tier:
            raise KeyboardInterrupt
        prof = env.get('profile', PR.LONG_RUN)
        st = env.get('status', {}).get(tier, 'COMPLETE')
        core = {'status': st, 'missing_data_policy': PR.POLICY}
        return (dict(core, format=prof.summary_format, tier=tier, label=SU.LABEL),
                dict(core, format=prof.detail_format, tier=tier))
    monkeypatch.setattr(PR, 'evaluate_tier', ev)
    return seen


def test_the_profile_alone_changes_the_run_id():
    long, short = _env(), dict(_env(), profile=SHORT)                   # same identity + run config, other profile
    for t in PR.RUN_TIERS:
        assert PR.tier_config(short, t)['profile'] == SHORT.binding() and 'profile' not in PR.tier_config(long, t)
        assert PG.run_id(long['identity'], PR.tier_config(long, t)) != PG.run_id(short['identity'],
                                                                                 PR.tier_config(short, t))


def test_long_and_short_runs_and_resumes_never_alias(tmp_path, stub):
    out = str(tmp_path / 'r4b')
    PR.run_tiers(_env(), out, clock=None)
    for resume in (False, True):                                          # a short run never resumes a long dir
        with pytest.raises(PR.PitRunError, match='another run identity'):
            PR.run_tiers(_env(SHORT), out, resume=resume, clock=None)
    sout = str(tmp_path / 'r4b-short')
    with pytest.raises(KeyboardInterrupt):
        PR.run_tiers(dict(_env(SHORT), boom='CORE-20'), sout, clock=None)
    with pytest.raises(PR.PitRunError, match='another run identity'):     # nor a long run a short dir
        PR.run_tiers(_env(), sout, resume=True, clock=None)
    finals = PR.run_tiers(_env(SHORT), sout, resume=True, clock=None)
    assert [s for s in stub if s[0] == 'short'] == [('short', 'CORE-10'), ('short', 'CORE-20'), ('short', 'CORE-20')]
    for f in finals:
        doc = PG.read(f)
        assert doc['state'] == 'sealed' and doc['evidence']
        (s,) = glob.glob(os.path.join(os.path.dirname(f), 'summary-*.json'))
        assert json.load(open(s))['format'] == 'zb-strat02-pit-summary/1'


def test_short_core10_is_sealed_before_core20(tmp_path, stub):
    with pytest.raises(P.PITError):
        PR.run_tiers(dict(_env(SHORT), fail='CORE-10'), str(tmp_path), clock=None)
    assert stub == [('short', 'CORE-10')] and not os.path.exists(tmp_path / 'CORE-20')
    finals = PR.run_tiers(_env(SHORT), str(tmp_path / 'ok'), clock=None)
    assert stub[1:] == [('short', 'CORE-10'), ('short', 'CORE-20')]
    assert [os.path.basename(os.path.dirname(f)) for f in finals] == ['CORE-10', 'CORE-20']


def test_an_incomplete_short_tier_is_never_evidence_report_or_readiness(tmp_path, stub, monkeypatch):
    finals = PR.run_tiers(dict(_env(SHORT), status={'CORE-20': 'INCOMPLETE'}), str(tmp_path), clock=None)
    ok, bad = finals
    assert PG.read(ok)['evidence'] and not PG.read(bad)['evidence'] and G.pit_incomplete(finals) == [bad]
    RD.sealed_artifacts(ok)
    with pytest.raises(RD.ReadinessError, match='INCOMPLETE'):
        RD.sealed_artifacts(bad)
    (s,) = glob.glob(os.path.join(os.path.dirname(bad), 'summary-*.json'))
    with pytest.raises(RP.ReportError, match='INCOMPLETE'):
        RP.refuse_incomplete(json.load(open(s)))
    monkeypatch.setattr(G, 'run_pit', lambda repo, **kw: finals)
    assert G.main(['pit', '--candidate', G.SHORT_ID, '--store', 'x', '--author', AUTH[0],
                   '--cairo-date', AUTH[1]]) == 4


# ------------------------------------------------------------------ synthetic end-to-end of the short profile
def short_env(store, ledger):
    env = F.fixture_env(store, ledger, resamples=100, perms=100, b0_draws=10)
    rc = dict(env['run_config'], configs=[c['name'] for c in SHORT.configs()], seed=SHORT.seed, n_trials=35,
              profile=SHORT.binding(), multiplicity=MULT)
    ident = dict(env['identity'], evaluator=PR.sha({'code': PR.code_digest(ROOT), 'config': rc}))
    return dict(env, run_config=rc, identity=ident, n_trials=35, profile=SHORT, multiplicity=MULT, folds=1)


@pytest.fixture(scope='module')
def short_e2e(tmp_path_factory):
    base = tmp_path_factory.mktemp('r4b_short')
    store = str(base / 'store')
    F.make_store(store)
    led = str(base / 'ledger' / 'trend_ema_mom_short.jsonl')
    os.makedirs(os.path.dirname(led))
    out = str(base / 'out')
    return out, led, PR.run_tiers(short_env(store, led), out, clock=None)


def test_short_e2e_runs_the_short_profile_end_to_end(short_e2e):
    out, led, finals = short_e2e
    assert [os.path.basename(os.path.dirname(f)) for f in finals] == list(PR.RUN_TIERS)
    for f in finals:
        d = os.path.dirname(f)
        (s,), (dt,) = glob.glob(os.path.join(d, 'summary-*.json')), glob.glob(os.path.join(d, 'detail-*.json'))
        s, dt = json.load(open(s)), json.load(open(dt))
        assert (s['format'], s['candidate_id'], s['side']) == ('zb-strat02-pit-summary/1', G.SHORT_ID, 'short')
        assert dt['format'] == 'zb-strat02-pit-detail/1' and s['profile'] == SHORT.binding()
        m = s['edge00']['multiplicity']
        assert m['n_trials'] == 35 and m['own_family_n_trials'] == 17 and m['own_family_gating'] is False
        assert all(set(v) == {'primary'} for v in s['splits'].values())    # no uncapped development variant
        for sp in dt['splits'].values():
            assert sorted(sp['configs']) == sorted(c['name'] for c in SHORT.configs())
            for name, c in sp['configs'].items():
                assert all(t.get('side') == 'short' for t in c['trades']), name
        assert s['not_evaluated'] == list(PR.SHORT_NOT_EVALUATED)
        b = s['baselines']
        ps = {k: b[k]['p_one_sided'] for k in ('B0', 'B1', 'B2')}
        assert {k: b[k]['p_holm'] for k in ps} == PR.holm(ps, 35)
    # CORE-20 holds the fixture's missing bar: INCOMPLETE, sealed without evidence (missing-data policy)
    assert G.pit_incomplete(finals) == [finals[1]] and not PG.read(finals[1])['evidence']


def test_short_e2e_access_records_are_candidate_and_side_bound(short_e2e):
    out, led, _ = short_e2e
    recs = L.read(led) if hasattr(L, 'read') else [json.loads(x) for x in open(led, encoding='utf-8')]
    assert recs and all(r['candidate_id'] == G.SHORT_ID for r in recs)
    opens = [r for r in recs if r['detail'].get('split_key')]
    assert opens and all(r['detail']['side'] == 'short' and r['detail']['purpose'].startswith('STRAT-02 R4b-PIT')
                         and r['split'] != 'holdout' for r in opens)
    assert not os.path.exists(os.path.join(os.path.dirname(led), 'trend_ema_mom.jsonl'))


# ------------------------------------------------------------------ PIT-01 revision (Codex 6157724309)
# P1-1: the CORE barrier - CORE-20 opens only after a same-identity CORE-10 final that is sealed, hash-valid,
# explicitly COMPLETE and evidence-bearing; an INCOMPLETE CORE-10 is returned as the audit final, nothing else opens.
PROFS = pytest.mark.parametrize('prof', [None, SHORT], ids=['long', 'short'])
OK = {'status': 'COMPLETE', 'missing_data_policy': PR.POLICY}


def _craft(out, env, summary, evidence):
    """A sealed CORE-10 final written directly under the env's run id (named evidence chosen by the test)."""
    d = os.path.join(out, 'CORE-10')
    cfg = PR.tier_config(env, 'CORE-10')
    prog = PG.Progress(d, identity=env['identity'], config=cfg, clock=None, folds=PR.fold_bound(env))
    p = PR.write_json(os.path.join(d, f'summary-{PG.run_id(env["identity"], cfg)}.json'), summary)
    prog.sealed([p], evidence(os.path.basename(p)))
    return prog.final_path, p


@PROFS
def test_a_fresh_incomplete_core10_is_the_audit_final_and_core20_never_opens(tmp_path, stub, monkeypatch, prof):
    access = []
    monkeypatch.setattr(P, 'Access', lambda *a, **kw: access.append(1))
    env = dict(_env(prof), status={'CORE-10': 'INCOMPLETE'})
    fb = PR.fold_bound(env)
    finals = PR.run_tiers(env, str(tmp_path), clock=None)
    assert len(finals) == 1 and os.path.basename(os.path.dirname(finals[0])) == 'CORE-10'
    doc = PG.read(finals[0], fb)
    assert doc['state'] == 'sealed' and not doc['evidence']
    assert [t for _, t in stub] == ['CORE-10'] and not os.path.exists(tmp_path / 'CORE-20') and access == []
    assert G.pit_incomplete(finals, fb) == finals
    monkeypatch.setattr(G, 'run_pit', lambda repo, **kw: finals)
    monkeypatch.setattr(G, 'pit_folds', lambda repo, c=None: fb)
    cli = ['pit', '--store', 'x', '--author', AUTH[0], '--cairo-date', AUTH[1]]
    assert G.main(cli + (['--candidate', G.SHORT_ID] if prof else [])) == 4


@PROFS
def test_a_resumed_incomplete_core10_is_returned_and_core20_never_opens(tmp_path, stub, prof):
    env = dict(_env(prof), status={'CORE-10': 'INCOMPLETE'})
    first = PR.run_tiers(env, str(tmp_path), clock=None)
    again = PR.run_tiers(env, str(tmp_path), resume=True, clock=None)
    assert again == first and [t for _, t in stub] == ['CORE-10'] and not os.path.exists(tmp_path / 'CORE-20')


@PROFS
def test_a_complete_evidence_bearing_core10_opens_core20(tmp_path, stub, prof):
    env = _env(prof)
    finals = PR.run_tiers(env, str(tmp_path), clock=None)
    assert [t for _, t in stub] == ['CORE-10', 'CORE-20'] and len(finals) == 2
    assert all(PR.tier_evidence(f, env['identity'], PR.fold_bound(env)) for f in finals)


@PROFS
def test_named_evidence_must_be_a_sealed_complete_artifact(tmp_path, stub, prof):
    env = _env(prof)
    cases = [(OK, lambda p: ['ghost.json'], 'not a sealed artifact'),
             (dict(OK, status='INCOMPLETE'), lambda p: [p], 'INCOMPLETE'),
             ({'format': 'x'}, lambda p: [p], 'status')]
    for n, (summary, ev, msg) in enumerate(cases):
        out = str(tmp_path / str(n))
        _craft(out, env, summary, ev)
        with pytest.raises(PR.PitRunError, match=msg):
            PR.run_tiers(env, out, resume=True, clock=None)
        assert not os.path.exists(os.path.join(out, 'CORE-20'))
    assert stub == []


@PROFS
def test_a_tampered_or_foreign_identity_core10_refuses_before_core20(tmp_path, stub, prof):
    env = _env(prof)
    final, summary = _craft(str(tmp_path / 't'), env, OK, lambda p: [p])
    assert PR.tier_evidence(final, env['identity'], PR.fold_bound(env)) is True
    with open(summary, 'ab') as f:
        f.write(b' ')
    with pytest.raises(PR.PitRunError, match='no longer matches'):
        PR.run_tiers(env, str(tmp_path / 't'), resume=True, clock=None)
    with pytest.raises(PR.PitRunError, match='another identity'):
        PR.tier_evidence(final, dict(env['identity'], commit='9' * 40), PR.fold_bound(env))
    _craft(str(tmp_path / 'o'), _env(prof, resamples=99), OK, lambda p: [p])   # CORE-10 under another identity
    with pytest.raises(PR.PitRunError, match='another run identity'):
        PR.run_tiers(env, str(tmp_path / 'o'), resume=True, clock=None)
    assert stub == [] and not glob.glob(str(tmp_path / '*' / 'CORE-20'))


# P1-2: progress fold bounds are bound to the routed candidate's prereg / plan, never the global long prereg.
@pytest.fixture
def long3(tmp_path, monkeypatch):
    """A substituted 3-fold long prereg (the global default fold source); the committed short prereg has 5 folds."""
    p = tmp_path / 'long3.json'
    p.write_text(json.dumps({'splits': {'splits': [{'name': 'walk_forward', 'fold': k} for k in range(3)]}}))
    monkeypatch.setattr(PG, 'PREREG', str(p))
    assert PG.fold_count() == 3
    return p


def test_short_fold_bounds_come_from_the_short_prereg_never_the_long_one(tmp_path, long3):
    assert G.pit_folds(ROOT, G.SHORT_ID) == 5 and G.pit_folds(ROOT) is None
    env = PR.real_env(ROOT, store=str(tmp_path / 'no-store'), author=AUTH[0], cairo_date=AUTH[1],
                      gate_path=G.SHORT_GATE, profile=SHORT)
    assert env['folds'] == PR.fold_bound(env) == 5
    with pytest.raises(PR.PitRunError, match='routed split plan'):    # the long count never stands in for short
        PR.fold_bound(dict(env, folds=3))
    with pytest.raises(PR.PitRunError, match='no fold bound'):
        PR.fold_bound({k: v for k, v in env.items() if k != 'folds'})
    ident = _env()['identity']
    short = PG.Progress(str(tmp_path / 's'), identity=ident, config={'x': 1}, clock=None, folds=5)
    short.stage_start('pit.walk_forward', 4)                             # a valid short fold 4 is accepted
    long = PG.Progress(str(tmp_path / 'l'), identity=ident, config={'x': 1}, clock=None)
    with pytest.raises(PG.ProgressError, match=r'\[0, 3\)'):             # ... and refused under the long prereg
        long.stage_start('pit.walk_forward', 4)


def test_fold_bound_is_carried_through_write_read_seal_and_resume(tmp_path, long3, monkeypatch):
    def ev(env, tier, prog):
        prog.stage_start('pit.walk_forward', 4)
        prog.stage_done('pit.walk_forward', 4)
        return dict(OK, format=SHORT.summary_format, tier=tier), dict(OK, format=SHORT.detail_format, tier=tier)
    monkeypatch.setattr(PR, 'evaluate_tier', ev)
    env = _env(SHORT)
    finals = PR.run_tiers(env, str(tmp_path), clock=None)
    assert len(finals) == 2 and G.pit_incomplete(finals, 5) == []
    for f in finals:
        assert PG.read(f, 5)['stages'][-1]['fold'] == 4
        RD.sealed_artifacts(f, 5)
        with pytest.raises(PG.ProgressError):                            # never validates through the long prereg
            PG.read(f)
    assert G.pit_incomplete(finals) == finals                            # long bound: not evidence (fail closed)
    assert PR.run_tiers(env, str(tmp_path), resume=True, clock=None) == finals
    longish = {k: v for k, v in env.items() if k not in ('profile', 'folds')}
    with pytest.raises((PG.ProgressError, PR.PitRunError)):              # never resumes under the long bound
        PR.run_tiers(longish, str(tmp_path), resume=True, clock=None)
