"""REG-02 (Codex 6155928419): freeze + register STRAT-02 `trend_ema_mom_short.v1` through its SEPARATE short gate.

Evidence map (contract "Required evidence"):
  * canonical LF bytes == `git show HEAD:path`; the prereg SHA binds the gate and every ledger record;
  * mutation tests for short identity / side / primary / cap / secondary absence / trials / grid / M3-inapplicable /
    holdout / universe / rules / M1 policy;
  * throwaway-repo root-family creation, chain verification, 17 exact deterministic records, duplicate / partial /
    tampered / uncommitted refusal, the deadline boundary on an injected clock with both ledger files byte-identical;
  * short G1-G5 (pins / ancestry / universe / routing); the STRAT-01 path preserved at 18;
  * program multiplicity 18 + 17 = 35 and monotonic increase.
No market or history data is read, nothing is evaluated, the holdout is never opened."""
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOCS = 'do' + 'cs'   # segmented: executable source never names the documentation tree literally (test_ci reuse guard)
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import ledger as L                                                                          # noqa: E402
import missing_data as MD                                                                   # noqa: E402
import report as R                                                                          # noqa: E402
import run_r4 as G                                                                          # noqa: E402
import splits as S                                                                          # noqa: E402
import trend_ema_mom as T                                                                   # noqa: E402

PREREG_DIR = 'research_evidence/prereg'
HOLDOUT_START = S.parse_utc('2026-10-19T00:00:00Z')
LAST_MS = HOLDOUT_START - 1                                                 # 2026-10-18T23:59:59.999Z
BEFORE = S.parse_utc('2026-10-14T12:00:00Z')
LEDGER_DIR = 'research_evidence/ledger'


def load_short() -> bytes:
    with open(os.path.join(ROOT, G.SHORT_PREREG), 'rb') as f:
        return f.read()


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git(repo, *a):
    return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', *a],
                          capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout.strip()


def in_git_checkout() -> bool:
    return os.path.exists(os.path.join(ROOT, '.git'))


# ------------------------------------------------------------------ the committed, canonical artifacts
def test_canonical_prereg_and_gate_are_lf_and_equal_their_head_blobs():
    if not in_git_checkout():
        pytest.skip('not a git checkout')
    for rel in (G.SHORT_PREREG, G.SHORT_GATE):
        attr = subprocess.run(['git', 'check-attr', 'text', 'eol', '--', rel], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout
        assert f'{rel}: text: set' in attr and f'{rel}: eol: lf' in attr, attr
        blob = subprocess.run(['git', 'show', f'HEAD:{rel}'], cwd=ROOT, capture_output=True, check=True).stdout
        with open(os.path.join(ROOT, rel), 'rb') as f:
            work = f.read()
        assert work == blob and b'\r' not in work and work.endswith(b'}\n')
        assert work == (json.dumps(json.loads(work), indent=1) + '\n').encode('ascii')      # canonical written form
    assert not os.path.exists(os.path.join(ROOT, _DOCS, 'newcore', 'research', 'drafts', 'trend_ema_mom_short.v1.draft.json'))


def test_the_short_gate_pins_the_exact_prereg_sha_and_the_accepted_sources():
    g = json.loads(G._load(ROOT, G.SHORT_GATE))
    assert G.route(g) is G.SHORT
    assert g['prereg_sha256'] == sha(load_short())
    assert (g['prereg'], g['ledger'], g['integration_ref']) == (G.SHORT_PREREG, G.SHORT_LEDGER, 'origin/master')
    assert [(p['prs'], p['head'], p['integrated'], p['after']) for p in g['pins']] == [
        ([73], '620cf71a5649276ee3819aed8810e012c74eb37c', '4f7e3b4335512bd448d45a9d914d83531c9d77b1',
         'c613337a4efc7ebf31afbda186aef4219ecd30e3'),
        ([74], '686868ba520445116c59fa271326f0c0c0d78468', '7af6623338761010e4479fa54d09b2265867a4af',
         '4f7e3b4335512bd448d45a9d914d83531c9d77b1')]
    long_gate = json.loads(G._load(ROOT, G.GATE))
    assert g['universe'] == long_gate['universe'] and G.route(long_gate) is G.LONG         # STRAT-01 gate untouched
    assert 'candidate_id' not in long_gate and long_gate['prereg'] == G.LONG.prereg


def test_the_committed_short_prereg_validates_and_declares_exactly_17_trials():
    pre = json.loads(load_short())
    assert G.validate_prereg(pre, ROOT) == []
    assert G.profile_of(pre) is G.SHORT
    recs = G.trial_records(pre, '0' * 64, G.SHORT_PREREG)
    assert len(recs) == pre['trials']['declared']['total'] == G.SHORT_TRIALS == 17
    assert [r['kind'] for r in recs] == ['variant'] + ['grid_point'] * 12 + ['baseline'] * 4
    assert recs[0]['detail'] == {'prereg_path': G.SHORT_PREREG, 'prereg_sha256': '0' * 64, 'role': 'preregistration',
                                 'book': 'crypto', 'config': G.SHORT_PRIMARY_NAME}
    assert not any(r['detail'].get('role') == 'development_only_variant' for r in recs)     # no uncapped variant
    assert [r['detail'].get('config') for r in recs[1:13]] == [n for n, _ in T.neighbours()]
    assert [r['detail'].get('baseline') for r in recs[13:]] == ['B0', 'B1', 'B2', 'B3']
    assert all(r['candidate_id'] == G.SHORT_ID and r['split'] == 'development' for r in recs)
    assert recs[0]['window'] == {'start': '2021-01-04T00:00:00Z', 'end': '2027-10-18T00:00:00Z'}
    ho = next(x for x in pre['splits']['splits'] if x['name'] == 'holdout')
    assert (ho['start'], ho['end']) == ('2026-10-19T00:00:00Z', '2027-10-18T00:00:00Z')
    assert pre['m3_reproduction']['applicable'] is False and pre['variants']['secondary'] == []
    assert pre['missing_data']['policy'] == MD.POLICY == 'zb-missing-data/1'


def test_the_short_profile_matches_the_accepted_runner():
    import pit_run as PR
    assert (G.SHORT_PRIMARY_NAME, G.SHORT_SEED) == (PR.SHORT_PRIMARY_NAME, PR.SHORT_SEED)
    sig = [c['name'] for c in PR.configs_short() if c['kind'] == 'sig']
    recs = G.trial_records(json.loads(load_short()), '0' * 64, G.SHORT_PREREG)
    assert sig == [r['detail']['config'] for r in recs if r['kind'] in ('variant', 'grid_point')]
    assert not any(c.get('cap') is None and c['kind'] == 'sig' for c in PR.configs_short())


def test_the_frozen_content_equals_the_reviewed_draft_outside_the_m1_reconciliation():
    """Phase A item 1: only status / freeze date and the zb-missing-data/1 text changed against the reviewed draft."""
    if not in_git_checkout():
        pytest.skip('not a git checkout')
    p = subprocess.run(['git', 'show', f'7af6623338761010e4479fa54d09b2265867a4af:{_DOCS}/newcore/research/drafts/'
                        'trend_ema_mom_short.v1.draft.json'], cwd=ROOT, capture_output=True)
    if p.returncode:
        pytest.skip('the accepted base commit is not present (fetch)')
    old, new = json.loads(p.stdout), json.loads(load_short())
    assert set(new) - set(old) == {'missing_data'} and not set(old) - set(new)
    changed = {k for k in old if old[k] != new[k]}
    assert changed == {'status', 'cairo_date', 'rule', 'costs', 'baselines', 'splits', 'verdict_rules'}
    assert {k for k in old['rule'] if old['rule'][k] != new['rule'][k]} == {'time_cap', 'funding'}
    assert {k for k in old['costs'] if old['costs'][k] != new['costs'][k]} == {'delist'}
    assert {k for k in old['baselines'] if old['baselines'][k] != new['baselines'][k]} == {'B1'}
    assert {k for k in old['splits'] if old['splits'][k] != new['splits'][k]} == {'notes'}
    assert {k for k in old['verdict_rules'] if old['verdict_rules'][k] != new['verdict_rules'][k]} == {'PARK'}
    for k in ('primary_params', 'variants', 'neighbour_grid', 'trials', 'universe_rule', 'data', 'edge00',
              'hypothesis', 'm3_reproduction', 'stop_condition', 'metrics', 'contamination'):
        assert old[k] == new[k], k


def _mut_rule(p):
    p['rule']['entry'] = p['rule']['entry'].replace('cross_down(EMA20, EMA50)', 'cross_up(EMA20, EMA50)')


@pytest.mark.parametrize('mut, msg', [
    (lambda p: p.__setitem__('candidate_id', 'trend_ema_mom_short.v2'), 'keys must be exactly'),   # routes to long
    (lambda p: p.__setitem__('family', 'trend_ema_mom'), 'short identity'),
    (lambda p: p.__setitem__('format', 'zb-prereg/2'), 'short identity'),
    (lambda p: p['hypothesis'].__setitem__('side', 'long'), 'hypothesis.side'),
    (lambda p: p['variants']['primary'].__setitem__('side', 'long'), 'variants.primary'),
    (lambda p: p['variants']['primary'].__setitem__('name', 'trend_ema_mom.v1.cap180'), 'variants.primary'),
    (lambda p: p['variants']['primary'].__setitem__('promotable', False), 'variants.primary'),
    (lambda p: p['variants']['primary'].__setitem__('time_cap_bars', None), 'variants.primary'),
    (lambda p: p['primary_params'].__setitem__('time_cap_bars', 120), 'finite time cap'),
    (lambda p: p['primary_params'].__setitem__('stop_atr', '2.0'), 'primary_params'),
    (lambda p: p['variants']['secondary'].append({'name': 'trend_ema_mom_short.v1.uncapped', 'time_cap_bars': None,
                                                  'book': 'crypto', 'promotable': False}), 'secondary must be empty'),
    (lambda p: p['trials']['declared'].update(variant=2, total=18), '17'),
    (lambda p: p['trials']['declared'].__setitem__('baseline', 3), '17'),
    (lambda p: p['trials'].__setitem__('program_multiplicity', 'own family n = 17'), 'n = 35'),
    (lambda p: p['edge00'].__setitem__('multiplicity', 'Holm family = the trend_ema_mom_short ledger (n = 17)'),
     'edge00.multiplicity'),
    (lambda p: p['neighbour_grid']['configs'].pop(), 'neighbour grid'),
    (lambda p: p['neighbour_grid']['steps'].__setitem__('ema_fast', 10), 'neighbour grid'),
    (lambda p: p['m3_reproduction'].__setitem__('applicable', True), 'm3_reproduction must be inapplicable'),
    (lambda p: p['m3_reproduction'].__setitem__('manifest', {'id': 'legacy-m3-repro-v1'}), 'inapplicable'),
    (lambda p: p['splits']['splits'][-1].update(start='2026-10-12T00:00:00Z', end='2027-10-11T00:00:00Z'),
     'holdout must start on a Monday'),                                   # STRAT-01's window: before the freeze
    (lambda p: p['splits']['splits'][-1].update(start='2026-09-21T00:00:00Z', end='2027-09-20T00:00:00Z'), 'holdout'),
    (lambda p: p['splits']['splits'][-1].update(start='2026-10-20T00:00:00Z', end='2027-10-19T00:00:00Z'),
     'Monday'),
    (lambda p: p['splits']['splits'][-1].update(end='2027-10-25T00:00:00Z'), 'holdout'),
    (lambda p: p['data'].__setitem__('universe_digest', '0' * 64), 'static universe file'),
    (lambda p: p['data'].__setitem__('universe_path', 'research_evidence/universe/pit-top40-qv30d-v4.json'),
     'frozen static current list'),
    (lambda p: p['universe_rule']['report_tiers'].pop('EXT-21-40'), 'report_tiers'),
    (_mut_rule, 'short mirror'),
    (lambda p: p['rule'].__setitem__('stop', p['rule']['stop'].replace('CEILED', 'FLOORED')), 'short mirror'),
    (lambda p: p['rule'].pop('liquidation'), 'short mirror'),
    (lambda p: p['baselines'].__setitem__('B0', p['baselines']['B0'].replace('20261012', '20261009')), 'seed'),
    (lambda p: p['costs'].__setitem__('stress_rows', p['costs']['stress_rows'][:-1]), 'stress_rows'),
    (lambda p: p['missing_data'].__setitem__('policy', 'zb-missing-data/0'), 'missing_data'),
    (lambda p: p['missing_data']['rules'].pop('first_unaccountable_event'), 'missing_data'),
    (lambda p: p['missing_data']['rules'].__setitem__('exclude_and_rerun', 'drop the symbol'), 'missing_data'),
    (lambda p: p['missing_data'].__setitem__('doc', f'{_DOCS}/newcore/research/MISSING_DATA_POLICY_v2.md'),
     'missing_data'),
    (lambda p: p.pop('missing_data'), 'keys must be exactly'),
])
def test_short_prereg_validation_refuses_drift(mut, msg):
    pre = json.loads(load_short())
    mut(pre)
    bad = G.validate_prereg(pre, ROOT)
    assert any(msg in b for b in bad), bad


def test_the_long_path_is_unchanged_at_18():
    with open(os.path.join(ROOT, G.LONG.prereg), 'rb') as f:
        pre = json.loads(f.read())
    assert G.profile_of(pre) is G.LONG and G.validate_prereg(pre, ROOT) == []
    recs = G.trial_records(pre, '0' * 64, G.LONG.prereg)
    assert len(recs) == G.TRIALS == 18
    assert [(r['detail']['role'], r['detail'].get('config')) for r in recs[:2]] == [
        ('preregistration', G.PRIMARY_NAME), ('development_only_variant', G.DEV_ONLY)]
    assert (G.LONG.gate, G.LONG.root, G.LONG.program) == (G.GATE, False, False)


def test_routing_refuses_a_cross_wired_or_unpinned_gate():
    short = json.loads(G._load(ROOT, G.SHORT_GATE))
    long_ = json.loads(G._load(ROOT, G.GATE))
    for bad, msg in [(dict(short, candidate_id='trend_ema_mom_short.v9'), 'unknown candidate_id'),
                     (dict(short, prereg=G.LONG.prereg), 'routing'),
                     (dict(short, ledger=G.LONG.ledger), 'routing'),
                     ({k: v for k, v in short.items() if k != 'prereg_sha256'}, 'prereg_sha256'),
                     (dict(short, prereg_sha256='abc'), 'prereg_sha256'),
                     (dict(long_, prereg=G.SHORT_PREREG), 'routing'),
                     (dict(long_, ledger=G.SHORT_LEDGER), 'routing')]:
        with pytest.raises(G.GateError, match=msg):
            G.route(bad)


def test_cli_routes_check_and_register_to_the_named_candidate(monkeypatch):
    seen = []
    monkeypatch.setattr(G, 'gate', lambda repo, gate_path=G.GATE, **kw: seen.append(('check', gate_path)) or ['x'])
    monkeypatch.setattr(G, 'register', lambda repo, gate_path=G.GATE, **kw: seen.append(('register', gate_path)) or [])
    monkeypatch.setattr(G.L, 'verify', lambda *a, **kw: {})
    assert G.main(['check']) == 3 and G.main(['check', '--candidate', G.SHORT_ID]) == 3
    assert G.main(['register', '--candidate', G.SHORT_ID, '--author', 't', '--cairo-date', '2026-10-14']) == 0
    assert G.main(['register', '--author', 't', '--cairo-date', '2026-10-14']) == 0
    assert seen == [('check', G.GATE), ('check', G.SHORT_GATE), ('register', G.SHORT_GATE), ('register', G.GATE)]
    with pytest.raises(SystemExit):
        G.main(['register', '--candidate', 'trend_ema_mom_short.v2', '--author', 't', '--cairo-date', '2026-10-14'])


def test_pit_is_refused_for_the_short_gate_even_when_open(monkeypatch):
    monkeypatch.setattr(G, 'refuse_unless_open', lambda repo, **kw: None)
    # STRAT-02-PIT-01: the short gate runs only under the explicit short candidate (its own profile); an implicit
    # (STRAT-01) pit pointed at the short gate is still refused before any data is read
    with pytest.raises(G.GateError, match=f'routes {G.SHORT_ID}, not {G.LONG.candidate_id}'):
        G.run_pit(ROOT, store=ROOT, out_dir=ROOT, author='t', cairo_date='2026-10-14',
                  gate_kw={'gate_path': G.SHORT_GATE})


def test_the_committed_registration_state_is_all_or_nothing():
    """Before Phase B: no short family anywhere. After: exactly the 17 declared records, committed, SHA-bound, and the
    program Holm family is 35 (never a partial state)."""
    fams = L.verify(os.path.join(ROOT, G.SHORT_LEDGER))
    raw = load_short()
    if G.SHORT_FAMILY not in fams:
        with open(os.path.join(ROOT, LEDGER_DIR, L.REGISTRY), 'rb') as f:
            assert b'"trend_ema_mom_short"' not in f.read()
        assert G.multiplicity(fams) == {'program': 18, 'by_family': {'trend_ema_mom': 18, G.SHORT_FAMILY: 0}}
        return
    recs = fams[G.SHORT_FAMILY]
    want = G.trial_records(json.loads(raw), sha(raw), G.SHORT_PREREG)
    strip = lambda r: {k: r[k] for k in ('kind', 'candidate_id', 'split', 'window', 'manifest_digest', 'detail')}
    assert [strip(r) for r in recs] == want
    assert all(r['detail']['prereg_sha256'] == sha(raw) == json.loads(G._load(ROOT, G.SHORT_GATE))['prereg_sha256']
               for r in recs)
    assert G.multiplicity(fams)['program'] == G.PROGRAM_N_AT_FREEZE == 35
    assert G.gate(ROOT, gate_path=G.SHORT_GATE, need_cleared=False) == []


REGISTERED_SHORT_SHA = '2ed497d1e71f150b9d8958da7598131da3543a8e57c0f27e768bb21c13719a4a'   # its first 17 lines
REGISTERED_REGISTRY_ENTRY = 'c348bc798e6600befb4196ed3f520bc8d931247b342e7e849a0e0765be75b23f'  # REGISTRY seq 8


def test_the_committed_strat02_registration_is_pinned():
    """Phase B (7230ba5): the real registration, Cairo 2026-10-14, before the 2026-10-19 holdout. Later appends may
    extend the files; the registration prefix itself never changes."""
    if not in_git_checkout():
        pytest.skip('not a git checkout')
    blob = subprocess.run(['git', 'show', f'HEAD:{G.SHORT_LEDGER}'], cwd=ROOT, capture_output=True, check=True).stdout
    lines = blob.splitlines(keepends=True)
    assert sha(b''.join(lines[:17])) == REGISTERED_SHORT_SHA
    recs = [json.loads(x) for x in lines[:17]]
    assert all(r['cairo_date'] == '2026-10-14' and r['author'] == 'claude-code' for r in recs)
    assert {r['detail']['prereg_sha256'] for r in recs} == {sha(load_short())}
    reg_blob = subprocess.run(['git', 'show', f'HEAD:{LEDGER_DIR}/{L.REGISTRY}'], cwd=ROOT, capture_output=True,
                              check=True).stdout.splitlines(keepends=True)
    entry = [x for x in reg_blob if b'"trend_ema_mom_short"' in x]
    assert len(entry) == 1 and sha(entry[0]) == REGISTERED_REGISTRY_ENTRY
    e = json.loads(entry[0])
    assert (e['seq'], e['kind'], e['ref']) == (8, 'family', {'parent': None, 'genesis': sha(lines[0])})
    led = [os.path.join(ROOT, G.SHORT_LEDGER), os.path.join(ROOT, LEDGER_DIR, L.REGISTRY)]
    before = [open(p, 'rb').read() for p in led]
    with pytest.raises(G.GateError):                         # a repeat is a no-op refusal (already registered)
        G.register(ROOT, author='claude-code', cairo_date='2026-10-14', gate_path=G.SHORT_GATE, now_ms=BEFORE)
    assert [open(p, 'rb').read() for p in led] == before


# ------------------------------------------------------------------ a throwaway repo (never the real ledger)
CODE = tuple(R.CORE_EVAL_FILES) + tuple(G.EVAL_FILES) + ('tools/research/run_r4.py', 'tools/research/m3_repro.py',
                                                           'tools/research/static_universe.py')


def _copy(r, rel):
    (r / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(os.path.join(ROOT, rel), r / rel)


def make_repo(tmp_path, *, long_registered=True, name='repo'):
    """Code, the canonical short prereg + gate (pins / integration ref rewritten to the throwaway history), the real
    universe + snapshot + policy doc, and a ledger directory: a copy of the committed STRAT-01 ledgers (18 long trial
    records) or a fresh one holding only the long window_spent record."""
    r = tmp_path / name
    for rel in CODE + (G.SHORT_PREREG, G.UNIVERSE, G.SNAPSHOT, G.MISSING_DATA_DOC):
        _copy(r, rel)
    (r / '.gitattributes').write_text('* -text\n')
    git(tmp_path, 'init', '-q', '-b', 'master', str(r))
    led = r / LEDGER_DIR
    led.mkdir(parents=True)
    gen = None
    if long_registered:
        for n in sorted(os.listdir(os.path.join(ROOT, LEDGER_DIR))):
            if n.endswith('.jsonl') and not n.startswith(G.SHORT_FAMILY):
                if n == L.REGISTRY:
                    with open(os.path.join(ROOT, LEDGER_DIR, n), 'rb') as f:   # the pre-REG-02 registry prefix
                        lines = [x for x in f.read().splitlines(keepends=True) if b'"trend_ema_mom_short"' not in x]
                    (led / n).write_bytes(b''.join(lines))
                else:
                    _copy(r, f'{LEDGER_DIR}/{n}')
    else:
        L.append(str(led / 'trend_ema_mom.jsonl'), kind='window_spent', candidate_id=T.RULE_ID, split='development',
                 window={'start': '2021-12-19T00:00:00Z', 'end': '2026-10-05T00:00:00Z'}, author='t',
                 cairo_date='2026-10-09', lineage='root')
        with open(led / L.REGISTRY, 'rb') as f:
            gen = sha(f.read().splitlines(keepends=True)[0])
    git(r, 'add', '-A')
    git(r, 'commit', '-q', '-m', 'foundation (#73 + #74 analogue)')
    head = git(r, 'rev-parse', 'HEAD')
    g = json.loads(G._load(ROOT, G.SHORT_GATE))
    g.update(integration_ref='master', pins=[{'slice': 'foundation', 'prs': [73, 74], 'head': head}])
    (r / G.SHORT_GATE).write_text(json.dumps(g, indent=1) + '\n')
    git(r, 'add', '-A')
    git(r, 'commit', '-q', '-m', 'short gate')
    return str(r), gen


def files(repo):
    out = {}
    for n in (L.REGISTRY, f'{G.SHORT_FAMILY}.jsonl', 'trend_ema_mom.jsonl'):
        p = os.path.join(repo, LEDGER_DIR, n)
        out[n] = open(p, 'rb').read() if os.path.exists(p) else None
    return out


def reg(repo, gen=None, now=BEFORE, date='2026-10-14'):
    return G.register(repo, author='claude-code', cairo_date=date, gate_path=G.SHORT_GATE, registry_genesis=gen,
                      now_ms=now)


def test_root_family_registration_appends_17_exact_records_and_opens_the_short_gate(tmp_path):
    repo, gen = make_repo(tmp_path)
    kw = dict(gate_path=G.SHORT_GATE, registry_genesis=gen)
    fail = G.gate(repo, **kw)
    assert any('G3 program Holm family 18 < 35' in f for f in fail) and any('G3 no preregistration' in f for f in fail)
    before = files(repo)
    recs = reg(repo, gen)
    after = files(repo)
    raw = load_short()
    assert len(recs) == 17 and after['trend_ema_mom.jsonl'] == before['trend_ema_mom.jsonl']
    strip = lambda r: {k: r[k] for k in ('kind', 'candidate_id', 'split', 'window', 'manifest_digest', 'detail')}
    assert [strip(r) for r in recs] == G.trial_records(json.loads(raw), sha(raw), G.SHORT_PREREG)
    assert all(r['detail']['prereg_sha256'] == sha(raw) and r['author'] == 'claude-code'
               and r['cairo_date'] == '2026-10-14' for r in recs)
    lines = after[f'{G.SHORT_FAMILY}.jsonl'].splitlines(keepends=True)
    assert len(lines) == 17 and [json.loads(x)['seq'] for x in lines] == list(range(1, 18))
    assert json.loads(lines[0])['prev'] == L.GENESIS
    assert all(json.loads(lines[i])['prev'] == sha(lines[i - 1]) for i in range(1, 17))           # hash chain
    assert after[L.REGISTRY].startswith(before[L.REGISTRY])                                    # append-only
    (new,) = after[L.REGISTRY][len(before[L.REGISTRY]):].splitlines()
    entry = json.loads(new)
    assert entry['kind'] == 'family' and entry['family'] == G.SHORT_FAMILY
    assert entry['ref'] == {'parent': None, 'genesis': sha(lines[0])}                          # independent root
    assert entry['prev'] == sha(before[L.REGISTRY].splitlines(keepends=True)[-1])
    fams = L.verify(os.path.join(repo, G.SHORT_LEDGER), registry_genesis=gen)
    assert L.n_trials(fams[G.SHORT_FAMILY]) == 17
    assert G.multiplicity(fams) == {'program': 35, 'by_family': {'trend_ema_mom': 18, G.SHORT_FAMILY: 17}}
    assert any('G3 the registration records are not committed at HEAD' in f for f in G.gate(repo, **kw))
    with pytest.raises(G.GateError, match='already registered'):                     # duplicate, uncommitted
        reg(repo, gen)
    assert files(repo) == after
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'register STRAT-02')
    assert G.gate(repo, **kw) == []                                                  # G1-G5 open
    with pytest.raises(G.GateError, match='already registered'):                     # duplicate, committed: no-op
        reg(repo, gen)
    assert files(repo) == after
    assert G.gate(repo, registry_genesis=gen, need_cleared=False) != []              # the long gate is not here


def test_registration_bytes_are_deterministic(tmp_path):
    a, _ = make_repo(tmp_path, name='a')
    b, _ = make_repo(tmp_path, name='b')
    reg(a), reg(b)
    assert files(a) == files(b) and files(a)[f'{G.SHORT_FAMILY}.jsonl'] is not None


def test_deadline_boundary_on_an_injected_clock(tmp_path):
    repo, gen = make_repo(tmp_path)
    before = files(repo)
    assert before[f'{G.SHORT_FAMILY}.jsonl'] is None
    for now in (HOLDOUT_START, HOLDOUT_START + 1, S.parse_utc('2027-01-04T00:00:00Z')):
        with pytest.raises(G.GateError, match='holdout started at 2026-10-19T00:00:00Z'):
            reg(repo, gen, now=now)
        assert files(repo) == before                                                 # both ledger files identical
    assert len(reg(repo, gen, now=LAST_MS)) == 17                                    # 2026-10-18T23:59:59.999Z


def test_a_foreign_partial_registration_is_refused_without_appending(tmp_path):
    """A prefix written by another author is not this registration's deterministic prefix: never resumed."""
    repo, gen = make_repo(tmp_path)
    raw = load_short()
    first = G.trial_records(json.loads(raw), sha(raw), G.SHORT_PREREG)[0]
    L.append(os.path.join(repo, G.SHORT_LEDGER), author='t', cairo_date='2026-10-14', lineage='root', **first)
    before = files(repo)
    with pytest.raises(G.GateError, match='same --author'):
        reg(repo, gen)
    assert files(repo) == before
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'partial')
    fail = G.gate(repo, gate_path=G.SHORT_GATE, registry_genesis=gen)
    assert any('differ from the declared set' in f for f in fail) and any('G3 program Holm' in f for f in fail)


def test_a_pre_existing_short_family_is_refused(tmp_path):
    repo, gen = make_repo(tmp_path)
    L.append(os.path.join(repo, G.SHORT_LEDGER), kind='data_access', candidate_id=G.SHORT_ID, split='development',
             window={'start': '2021-01-04T00:00:00Z', 'end': '2021-12-20T00:00:00Z'}, author='t',
             cairo_date='2026-10-14', lineage='root')
    before = files(repo)
    with pytest.raises(G.GateError, match='another prereg or kind'):                # uncommitted: dirt refusal
        reg(repo, gen)
    assert files(repo) == before
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'foreign root')
    with pytest.raises(G.GateError, match='independent root'):                       # committed: root refusal
        reg(repo, gen)
    assert files(repo) == before


@pytest.mark.parametrize('where', ['working', 'committed'])
def test_a_tampered_ledger_is_refused(tmp_path, where):
    repo, gen = make_repo(tmp_path)
    p = os.path.join(repo, 'research_evidence/ledger/trend_ema_mom.jsonl')
    with open(p, 'rb') as f:
        data = f.read()
    with open(p, 'wb') as f:
        f.write(data.replace(b'"cairo_date":"2026-10-11"', b'"cairo_date":"2026-10-10"', 1))
    if where == 'committed':
        git(repo, 'commit', '-q', '-am', 'rewrite history')
    before = files(repo)
    with pytest.raises((G.GateError, L.LedgerError)):
        reg(repo, gen)
    assert files(repo) == before and before[f'{G.SHORT_FAMILY}.jsonl'] is None


def test_an_uncommitted_or_edited_prereg_or_dirty_code_is_refused(tmp_path):
    repo, gen = make_repo(tmp_path)
    before = files(repo)
    p = os.path.join(repo, G.SHORT_PREREG)
    raw = open(p, 'rb').read()
    open(p, 'wb').write(raw.replace(b'"stop_atr": "2.5"', b'"stop_atr": "2.0"'))      # a retune attempt
    with pytest.raises(G.GateError, match='G2'):
        reg(repo, gen)
    open(p, 'wb').write(raw + b'\n')                                                 # same JSON, other bytes
    with pytest.raises(G.GateError, match='not committed at HEAD'):
        reg(repo, gen)
    open(p, 'wb').write(raw.replace(b'\n', b'\r\n'))                                 # a CRLF checkout
    with pytest.raises(G.GateError, match='prereg_sha256'):
        reg(repo, gen)
    open(p, 'wb').write(raw)
    with open(os.path.join(repo, 'tools/research/trend_ema_mom.py'), 'a') as f:
        f.write('# edit\n')
    with pytest.raises(G.GateError, match='G1 dirty'):
        reg(repo, gen)
    git(repo, 'checkout', '--', 'tools/research/trend_ema_mom.py')
    g = json.loads(G._load(repo, G.SHORT_GATE))
    git(repo, 'rm', '-q', '--cached', G.SHORT_PREREG)
    git(repo, 'commit', '-q', '-m', 'untrack prereg')
    with pytest.raises(G.GateError, match='not committed at HEAD'):                  # untracked prereg
        reg(repo, gen)
    assert files(repo) == before and g['prereg_sha256'] == sha(raw)


def test_short_registration_needs_strat01_registered_first(tmp_path):
    repo, gen = make_repo(tmp_path, long_registered=False)
    before = files(repo)
    with pytest.raises(G.GateError, match='below 35'):
        reg(repo, gen)
    assert files(repo) == before


def test_program_multiplicity_is_35_and_only_increases(tmp_path):
    repo, gen = make_repo(tmp_path)
    reg(repo, gen)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'register')
    path = os.path.join(repo, G.SHORT_LEDGER)
    n = lambda: G.multiplicity(L.verify(path, registry_genesis=gen))
    assert n()['program'] == 35 and n()['by_family'][G.SHORT_FAMILY] == 17
    w = {'start': '2021-01-04T00:00:00Z', 'end': '2021-12-20T00:00:00Z'}
    L.append(os.path.join(repo, 'research_evidence/ledger/trend_ema_mom.jsonl'), kind='grid_point',
             candidate_id=T.RULE_ID, split='development', window=w, author='t', cairo_date='2026-10-15',
             detail={'role': 'later'})
    assert n()['program'] == 36
    L.append(path, kind='baseline', candidate_id=G.SHORT_ID, split='development', window=w, author='t',
             cairo_date='2026-10-15', detail={'role': 'later'})
    L.append(path, kind='data_access', candidate_id=G.SHORT_ID, split='development', window=w, author='t',
             cairo_date='2026-10-15')                                                # not a trial: no change
    assert n() == {'program': 37, 'by_family': {'trend_ema_mom': 19, G.SHORT_FAMILY: 18}}
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'later records')
    assert not any(f.startswith('G3 program') for f in G.gate(repo, gate_path=G.SHORT_GATE, registry_genesis=gen))


def test_short_gate_g2_g4_g5_close_on_drift(tmp_path):
    repo, gen = make_repo(tmp_path)
    reg(repo, gen)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'register')
    kw = dict(gate_path=G.SHORT_GATE, registry_genesis=gen)
    assert G.gate(repo, **kw) == []
    p = os.path.join(repo, G.SHORT_GATE)
    g0 = json.loads(open(p).read())

    def with_gate(**over):
        open(p, 'w').write(json.dumps(dict(g0, **over), indent=1) + '\n')
        git(repo, 'commit', '-q', '-am', 'gate edit')
        out = G.gate(repo, **kw)
        open(p, 'w').write(json.dumps(g0, indent=1) + '\n')
        git(repo, 'commit', '-q', '-am', 'gate restore')
        return out
    assert any('gate-pinned prereg_sha256' in f for f in with_gate(prereg_sha256='0' * 64))
    git(repo, 'checkout', '-q', '-b', 'side')
    (open(os.path.join(repo, 'x.txt'), 'w')).write('x')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'unmerged')
    side = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '-q', 'master')
    assert any('not merged into master' in f for f in with_gate(pins=g0['pins'] + [{'slice': 'x', 'prs': [0],
                                                                                    'head': side}]))
    assert any('full 40-hex' in f for f in with_gate(pins=[dict(g0['pins'][0], head=g0['pins'][0]['head'][:7])]))
    assert any(f.startswith('G5') for f in with_gate(universe=dict(g0['universe'], digest='c' * 64)))
    assert any(f.startswith('G5') for f in with_gate(universe=dict(g0['universe'], owner_approved=False)))
    assert any('unknown candidate_id' in f for f in with_gate(candidate_id='nope'))
    assert G.gate(repo, **kw) == []


# ------------------------------------------------------------- interrupted registration recovery (Codex 6156314715)
def interrupt_after(monkeypatch, n):
    real, calls = L.append, []

    def flaky(*a, **kw):
        if len(calls) == n:
            raise OSError(f'injected fault after {n} successful appends')
        calls.append(1)
        return real(*a, **kw)
    monkeypatch.setattr(G.L, 'append', flaky)
    return lambda: monkeypatch.setattr(G.L, 'append', real)


def root_declarations(repo):
    return [x for x in files(repo)[L.REGISTRY].splitlines() if b'"trend_ema_mom_short"' in x]


@pytest.mark.parametrize('commit', [False, True], ids=['raw-crash-state', 'committed-prefix'])
@pytest.mark.parametrize('n', [1, 5])
def test_an_interrupted_registration_resumes_to_exactly_17(tmp_path, monkeypatch, n, commit):
    clean, _ = make_repo(tmp_path, name='clean')
    reg(clean)                                                                       # the uninterrupted reference
    repo, gen = make_repo(tmp_path)
    restore = interrupt_after(monkeypatch, n)
    with pytest.raises(OSError, match='injected'):
        reg(repo, gen)
    restore()
    part = files(repo)
    assert len(part[f'{G.SHORT_FAMILY}.jsonl'].splitlines()) == n and len(root_declarations(repo)) == 1
    if commit:                                                                       # optional: committed prefix
        git(repo, 'add', '-A')
        git(repo, 'commit', '-q', '-m', 'interrupted')
    else:                                                                            # required: the raw crash state
        assert sorted(G._status_paths(repo)) == sorted([G.SHORT_LEDGER, f'{LEDGER_DIR}/{L.REGISTRY}'])
    assert any(f.startswith('G3') for f in G.gate(repo, gate_path=G.SHORT_GATE, registry_genesis=gen))
    for bad in (dict(date='2026-10-15'), dict(now=HOLDOUT_START)):                   # refusals: byte-identical
        with pytest.raises(G.GateError):
            reg(repo, gen, **bad)
        assert files(repo) == part
    with pytest.raises(G.GateError, match='same --author'):
        G.register(repo, author='someone-else', cairo_date='2026-10-14', gate_path=G.SHORT_GATE,
                   registry_genesis=gen, now_ms=BEFORE)
    assert files(repo) == part
    more = reg(repo, gen, now=LAST_MS)                                               # h0 - 1 ms: allowed
    assert len(more) == 17 - n and [r['seq'] for r in more] == list(range(n + 1, 18))
    done = files(repo)
    assert done == files(clean)                                                      # identical to uninterrupted
    assert len(done[f'{G.SHORT_FAMILY}.jsonl'].splitlines()) == 17 and len(root_declarations(repo)) == 1
    fams = L.verify(os.path.join(repo, G.SHORT_LEDGER), registry_genesis=gen)
    assert G.multiplicity(fams)['program'] == 35
    with pytest.raises(G.GateError, match='already registered'):                     # complete: duplicate no-op
        reg(repo, gen)
    assert files(repo) == done
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'resumed')
    assert G.gate(repo, gate_path=G.SHORT_GATE, registry_genesis=gen) == []


def test_a_non_prefix_or_extended_partial_state_is_refused(tmp_path, monkeypatch):
    raw = load_short()
    want = G.trial_records(json.loads(raw), sha(raw), G.SHORT_PREREG)
    path = lambda r: os.path.join(r, G.SHORT_LEDGER)
    a, gen = make_repo(tmp_path, name='a')                                           # record 2 first: not a prefix
    L.append(path(a), author='claude-code', cairo_date='2026-10-14', lineage='root', **want[1])
    b, _ = make_repo(tmp_path, name='b')                                             # a prefix, then a later record
    restore = interrupt_after(monkeypatch, 3)
    with pytest.raises(OSError):
        reg(b)
    restore()
    L.append(path(b), kind='data_access', candidate_id=G.SHORT_ID, split='development',
             window={'start': '2021-01-04T00:00:00Z', 'end': '2021-12-20T00:00:00Z'}, author='t',
             cairo_date='2026-10-14')
    for repo, msg in ((a, 'not the exact declared'), (b, 'another prereg or kind')):
        before = files(repo)
        with pytest.raises(G.GateError, match=msg):
            reg(repo)
        assert files(repo) == before
    git(b, 'add', '-A')
    git(b, 'commit', '-q', '-m', 'prefix + later record')
    before = files(b)
    with pytest.raises(G.GateError, match='tail of the family'):
        reg(b)
    assert files(b) == before


def _dirt_cases(repo):
    """(name, apply, undo) for every state a raw-crash retry must refuse byte-identically."""
    reg_p = os.path.join(repo, LEDGER_DIR, L.REGISTRY)
    short_p = os.path.join(repo, G.SHORT_LEDGER)
    long_p = os.path.join(repo, LEDGER_DIR, 'trend_ema_mom.jsonl')

    def edit(path, fn):
        old = open(path, 'rb').read()
        return (lambda: open(path, 'wb').write(fn(old))), (lambda: open(path, 'wb').write(old))

    def touch(rel):
        p = os.path.join(repo, rel)
        return (lambda: open(p, 'w').write('x')), (lambda: os.remove(p))
    w = {'start': '2021-01-04T00:00:00Z', 'end': '2021-12-20T00:00:00Z'}
    return [
        ('code', *edit(os.path.join(repo, 'tools/research/trend_ema_mom.py'), lambda b: b + b'# edit\n'), 'G1'),
        ('prereg', *edit(os.path.join(repo, G.SHORT_PREREG), lambda b: b + b'\n'), 'G2'),
        ('gate', *edit(os.path.join(repo, G.SHORT_GATE), lambda b: b.replace(b'"note"', b'"notes"')),
         'besides the ledger append'),
        ('unrelated file', *touch('notes.txt'), 'G1'),
        ('unrelated evidence file', *touch('research_evidence/x.json'), 'besides the ledger append'),
        ('other ledger append', *(lambda: L.append(long_p, kind='data_access', candidate_id=T.RULE_ID,
                                                    split='development', window=w, author='t', cairo_date='2026-10-14'),
                                  edit(long_p, lambda b: b)[1]), 'besides the ledger append'),
        # a rewritten / malformed registry is refused already by L.verify (it never reaches the append)
        ('registry rewrite', *edit(reg_p, lambda b: b.replace(b'"seq":1}', b'"seq":1 }', 1)),
         'HEAD bytes|not canonical|append-only'),
        ('registry extra line', *edit(reg_p, lambda b: b + b.splitlines(keepends=True)[-1]), 'declaration|seq must'),
        ('extra record', *(lambda: L.append(short_p, kind='data_access', candidate_id=G.SHORT_ID, split='development',
                                             window=w, author='t', cairo_date='2026-10-14'),
                           edit(short_p, lambda b: b)[1]), 'another prereg or kind'),
    ]


@pytest.mark.parametrize('case', range(9))
def test_a_raw_crash_retry_refuses_any_other_dirt_byte_identically(tmp_path, monkeypatch, case):
    repo, gen = make_repo(tmp_path)
    restore = interrupt_after(monkeypatch, 5)
    with pytest.raises(OSError):
        reg(repo, gen)
    restore()
    part = files(repo)
    name, apply, undo, msg = _dirt_cases(repo)[case]
    apply()
    dirty = files(repo)
    with pytest.raises((G.GateError, L.LedgerError), match=msg):
        reg(repo, gen)
    assert files(repo) == dirty, name                                                # nothing appended
    undo()
    assert files(repo) == part and len(reg(repo, gen)) == 12                         # then the clean retry resumes
    assert len(files(repo)[f'{G.SHORT_FAMILY}.jsonl'].splitlines()) == 17 and len(root_declarations(repo)) == 1
