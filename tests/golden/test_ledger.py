"""Golden pack contract version: MANIFEST.json (sha256 of every case's CONTRACT, schema.contract_sha) + CORRECTIONS.json
(append-only ledger). Design section 6: no silent re-baselining - a contract changes only through a ledger record.

Git-independent rules (always run, also in a checkout without history):
1. MANIFEST.json equals the contract hashes computed from cases/ (every case listed, nothing extra).
2. CORRECTIONS.json is a hash chain (entry k carries prev_entry_sha = sha256(canonical(entry k-1)); ids CORR-0000, -0001, ...)
   ANCHORED by the pinned hashes below, so a fully recomputed chain fails.
3. Every case's ledger history is well formed (first record genesis/add, prev_contract_sha links each record to the previous
   one, a retired case is not live), the set of live cases equals the manifest and every case's LATEST record carries exactly
   its manifest hash: editing a case and regenerating MANIFEST.json without a ledger record fails, so does deleting or
   editing the record that carries the hash.
4. Every known divergence names (`correction`) a ledger entry that exists, records that case, and is CONTRACT-BEARING for it
   (genesis / add / correction with a contract_sha - never a retire record, never a bootstrap expect-only record); when it is
   the case's latest record, its contract_sha is the case's current contract (the contract that contains the divergence).
4b. Entries after the pinned CORR-0003 carry a rationale of at least RATIONALE_MIN characters and a ticket / PR reference
   (TICKET_REF) in `ticket` (Cowork r2 (e)); CORR-0000..0003 are pinned and untouched.
Against the base (P1-1; the base is required evidence, never optional):
5. The base ledger is a prefix of this ledger (append-only). A case new against the base needs an appended `add` record, a
   changed contract an appended `correction` record from the base hash to the current one, a removed case a `retire` record.
   Base = $ZB_GOLDEN_BASE (CI: the PR base sha / the push's previous commit, fetched explicitly), locally origin/master. An
   unavailable base FAILS; the only skip is a base tree that has no golden pack at all (the bootstrap, i.e. this first pack),
   decided by listing the base tree.
6. Divergence binding (Codex r2 P2, derived from data already in the ledger - no record is rewritten): a known divergence that
   is NEW or CHANGED against the base case file must point at a record appended after the base that carries the case's
   current contract_sha, i.e. the record whose contract first introduced it. A divergence identical to the base's (pointer
   included) was bound when it was introduced and keeps its pointer through later unrelated corrections of the case. On the
   bootstrap base every divergence is new.
7. ZB_GOLDEN_BASE may not resolve to HEAD when HEAD itself changes the pack against its parent (Cowork r2 (d)): that base
   would make every change in HEAD look already-ledgered.
"""
import json, os, re, subprocess, sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from goldenlib import LEDGER_PATH, MANIFEST_PATH, REPO_ROOT, schema  # noqa: E402

MAN_REL, LED_REL = 'tests/golden/MANIFEST.json', 'tests/golden/CORRECTIONS.json'
MANIFEST_SCHEMA = 'zb-golden-manifest/2'
# P1-2 anchor: the genesis entry, and the last entry of the bootstrap prefix (it transitively pins CORR-0000..0003 through the
# prev_entry_sha links). Entries appended after it are protected by the base-prefix rule (5). Never edit these constants.
PINNED = {'CORR-0000': '811ba5a38f2f3331b5e9733f82d4d17ea9de7de915ea5629852ac53564eceacd',
          'CORR-0003': 'ecc4599182a43d528ad4c3efe03445d7bd3997b0f32eb43a0a93a513c486db7f'}
# Bootstrap-era records (CORR-0000/0001) carry the old expect-only hash ({new_expect_sha}); every later record is
# {prev_contract_sha, contract_sha} (retire: {prev_contract_sha}).
LEGACY_RECORD_ENTRIES = ('CORR-0000', 'CORR-0001')
CONTRACT_TYPES = ('genesis', 'add', 'correction')
LAST_PINNED = 3                                      # entries with a higher index get the 4b quality rule
RATIONALE_MIN = 40
TICKET_REF = re.compile(r'\b(AUD|NC|BT|PR) ?[-#]?\d+')     # AUD-07, NC-01, BT-02, PR #32, PR-32
CASES_REL = 'tests/golden/cases'
TYPES = ('genesis', 'add', 'correction', 'retire')
SHA = re.compile(r'[0-9a-f]{64}')


def _json(path):
    with open(path, encoding='utf-8-sig') as f:
        return schema.strict_loads(f.read())         # NaN / Infinity rejected, never canonicalised


def computed_manifest():
    return {c['id']: schema.contract_sha(c) for c in schema.load_all()}


def _ledger():
    led = _json(LEDGER_PATH)
    assert led.get('schema') == 'zb-golden-ledger/1'
    return led['entries']


def case_history(ents):
    """Walk the ledger -> {case id: dict(live, sha, first_type)}; raises AssertionError on any malformed record."""
    hist = {}
    for e in ents:
        assert isinstance(e.get('cases'), dict) and e['cases'], f"{e['id']}: names no case"
        for cid, rec in e['cases'].items():
            h = hist.get(cid)
            if e['id'] in LEGACY_RECORD_ENTRIES:
                assert set(rec) == {'new_expect_sha'} and SHA.fullmatch(rec['new_expect_sha']), (e['id'], cid, rec)
                assert e['type'] in ('genesis', 'add') and h is None, (e['id'], cid)
                hist[cid] = dict(live=True, sha=None, first_type=e['type'])
                continue
            want = {'prev_contract_sha'} if e['type'] == 'retire' else {'prev_contract_sha', 'contract_sha'}
            assert set(rec) == want, f"{e['id']}/{cid}: a {e['type']} record is exactly {sorted(want)}, got {sorted(rec)}"
            assert rec['prev_contract_sha'] is None or SHA.fullmatch(rec['prev_contract_sha']), (e['id'], cid)
            if e['type'] in ('genesis', 'add'):
                assert h is None or not h['live'], f"{e['id']}: {e['type']} of {cid}, which is already live"
            else:
                assert h is not None and h['live'], f"{e['id']}: {e['type']} of {cid}, which was never added (or is retired)"
            prev = h['sha'] if h and h['live'] else None
            assert rec['prev_contract_sha'] == prev, \
                f"{e['id']}/{cid}: prev_contract_sha {rec['prev_contract_sha']} is not the case's previous contract hash {prev}"
            if e['type'] == 'retire':
                hist[cid] = dict(h, live=False, sha=None)
            else:
                assert SHA.fullmatch(rec['contract_sha'] or ''), (e['id'], cid)
                hist[cid] = dict(live=True, sha=rec['contract_sha'], first_type=(h or {}).get('first_type', e['type']))
    return hist


def test_manifest_matches_the_cases():
    man = _json(MANIFEST_PATH)
    assert man.get('schema') == MANIFEST_SCHEMA
    got = computed_manifest()
    diff = {k: (man['cases'].get(k), got.get(k)) for k in set(man['cases']) | set(got) if man['cases'].get(k) != got.get(k)}
    assert not diff, ('golden case contracts differ from MANIFEST.json (manifest, now). A contract changes only as a ledgered '
                      'correction: update MANIFEST.json AND append a CORRECTIONS.json record with the same hash. ' + json.dumps(diff, indent=1))


def test_ledger_is_an_anchored_hash_chain():
    ents = _ledger()
    assert ents and ents[0]['type'] == 'genesis'
    for k, e in enumerate(ents):
        assert e['id'] == f'CORR-{k:04d}', e['id']
        for f in ('type', 'ticket', 'title', 'cases', 'rationale', 'date_cairo', 'prev_entry_sha'):
            assert f in e, (e['id'], f)
        assert e['type'] in TYPES and (e['type'] == 'genesis') == (k == 0), (e['id'], e['type'])
        assert e['prev_entry_sha'] == (schema.sha256(ents[k - 1]) if k else None), f"{e['id']}: broken chain (an entry was edited or removed)"
        check_entry_quality(e, k)
    by_id = {e['id']: e for e in ents}
    for eid, sha in PINNED.items():
        assert eid in by_id, f'{eid} is pinned but missing: ledger entries were removed'
        assert schema.sha256(by_id[eid]) == sha, f'{eid} does not match its pinned hash: the bootstrap ledger was rewritten'


def test_every_case_is_ledgered_with_its_manifest_hash():
    """P1-2 cross-check without git: the latest ledger record of every case carries exactly its manifest hash."""
    hist = case_history(_ledger())
    man = _json(MANIFEST_PATH)['cases']
    live = {cid for cid, h in hist.items() if h['live']}
    assert not (set(man) - live), f'cases with no live ledger record (an added case needs an `add` record): {sorted(set(man) - live)}'
    assert not (live - set(man)), f'ledger cases missing from MANIFEST.json (a removed case needs a `retire` record): {sorted(live - set(man))}'
    bad = {cid: (hist[cid]['sha'], sha) for cid, sha in man.items() if hist[cid]['sha'] != sha}
    assert not bad, ('the latest ledger record of these cases does not carry their manifest hash (ledger, manifest): append a '
                     'correction record {prev_contract_sha, contract_sha} for every changed contract. ' + json.dumps(bad, indent=1))


def check_entry_quality(e, k):
    """Rule 4b (Cowork r2 (e)): only for entries after the pinned bootstrap prefix."""
    if k <= LAST_PINNED:
        return
    r = e.get('rationale')
    assert isinstance(r, str) and len(r.strip()) >= RATIONALE_MIN, \
        f"{e['id']}: rationale must state why, in at least {RATIONALE_MIN} characters"
    assert isinstance(e.get('ticket'), str) and TICKET_REF.search(e['ticket']), \
        f"{e['id']}: ticket {e.get('ticket')!r} must reference a ticket or PR (AUD-nn / NC-nn / BT-nn / PR #nn)"


def check_divergence_pointers(now_cases, ents):
    """Rule 4 (git-independent). now_cases: {case id: case}."""
    by_id = {e['id']: e for e in ents}
    latest = {}
    for e in ents:
        for cid in e['cases']:
            latest[cid] = e['id']
    for cid, c in now_cases.items():
        for d in c['known_divergences']:
            tag = f"{cid} [{d['adapter']}]: correction {d['correction']}"
            e = by_id.get(d['correction'])
            assert e is not None, f'{tag} is not in the ledger'
            assert cid in e['cases'], f'{tag}: that ledger entry does not record this case'
            assert e['type'] in CONTRACT_TYPES and e['id'] not in LEGACY_RECORD_ENTRIES, \
                f"{tag}: a {e['type']} / bootstrap expect-only record carries no contract, so it cannot have introduced a divergence"
            rec = e['cases'][cid]
            assert SHA.fullmatch(str(rec.get('contract_sha') or '')), f'{tag}: the record carries no contract_sha for this case'
            if latest.get(cid) == e['id']:
                assert rec['contract_sha'] == schema.contract_sha(c), \
                    f"{tag}: it is the case's latest record but its contract is not the case's current contract"


def test_known_divergences_name_their_ledger_entry():
    check_divergence_pointers({c['id']: c for c in schema.load_all()}, _ledger())


# ---------------------------------------------------------------- P1-1: the base is required evidence
def _git(*args):
    try:
        r = subprocess.run(['git', *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        pytest.fail(f'golden base check: `git {args[0]}` could not run ({type(e).__name__}: {e}) - the append-only rule never '
                    'skips on a git error')
    return r


def _head_changes_pack():
    """True / False: does HEAD change the golden pack against its first parent? None when that cannot be decided."""
    if _git('rev-parse', '--verify', '--quiet', 'HEAD^{commit}').returncode != 0 or \
            _git('rev-parse', '--verify', '--quiet', 'HEAD^1^{commit}').returncode != 0:
        return None                                   # root commit or shallow checkout: no parent to diff against
    r = _git('diff', '--quiet', 'HEAD^1', 'HEAD', '--', MAN_REL, LED_REL, CASES_REL)
    return {0: False, 1: True}.get(r.returncode)


def refuse_self_base(ref, base_sha, head_sha, head_changes_pack):
    """Rule 7 (pure): an explicit base equal to the commit under test is refused when that commit changes the pack (or when
    that cannot be decided)."""
    if base_sha != head_sha:
        return
    changes = head_changes_pack()
    if changes is not False:
        pytest.fail(f'ZB_GOLDEN_BASE={ref!r} resolves to HEAD ({head_sha[:12]}), and HEAD '
                    f"{'changes the golden pack' if changes else 'cannot be shown to leave the golden pack unchanged'}: the base "
                    'must be the commit BEFORE the change (PR base sha / push before), never the commit under test')


def golden_base():
    """(ref, sha) of the base. CI must name it explicitly; locally origin/master. Unavailable = FAIL (never a skip)."""
    ref = os.environ.get('ZB_GOLDEN_BASE', '').strip()
    explicit = bool(ref)
    if not ref:
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            pytest.fail('CI must pass ZB_GOLDEN_BASE (pull_request: github.event.pull_request.base.sha fetched explicitly; push: '
                        'github.event.before) - the golden ledger is never checked against an implicit or missing base')
        ref = 'origin/master'
    r = _git('rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}')
    if r.returncode != 0 or not r.stdout.strip():
        pytest.fail(f'golden base {ref!r} is not available in this checkout (shallow clone without the base fetched, no '
                    f'origin/master, or a bad sha). Fetch it (git fetch --depth=1 origin <sha>) and/or set ZB_GOLDEN_BASE. '
                    f'git: {r.stderr.strip()[:300]}')
    sha = r.stdout.strip()
    if explicit:
        h = _git('rev-parse', '--verify', '--quiet', 'HEAD^{commit}')
        refuse_self_base(ref, sha, h.stdout.strip() if h.returncode == 0 else None, _head_changes_pack)
    return ref, sha


def base_pack(sha):
    """(manifest, ledger) of the base tree, or None when the base tree has NO golden pack (the bootstrap)."""
    r = _git('ls-tree', '--name-only', sha, '--', MAN_REL, LED_REL)
    if r.returncode != 0:
        pytest.fail(f'cannot list the golden pack in base {sha[:12]}: {r.stderr.strip()[:300]}')
    present = set(r.stdout.split())
    if not present:
        return None
    if present != {MAN_REL, LED_REL}:
        pytest.fail(f'base {sha[:12]} has only {sorted(present)} of the golden pack')
    out = []
    for rel in (MAN_REL, LED_REL):
        s = _git('show', f'{sha}:{rel}')
        if s.returncode != 0:
            pytest.fail(f'cannot read {rel} at base {sha[:12]}: {s.stderr.strip()[:300]}')
        out.append(schema.strict_loads(s.stdout.lstrip('﻿')))
    return tuple(out)


def base_cases(sha):
    """{case id: parsed case} of the base tree ({} when it has no cases dir)."""
    r = _git('ls-tree', '--name-only', f'{sha}:{CASES_REL}')
    if r.returncode != 0:
        return {}
    out = {}
    for name in r.stdout.split():
        if not name.endswith('.json'):
            continue
        s = _git('show', f'{sha}:{CASES_REL}/{name}')
        if s.returncode != 0:
            pytest.fail(f'cannot read {CASES_REL}/{name} at base {sha[:12]}: {s.stderr.strip()[:300]}')
        c = schema.strict_loads(s.stdout.lstrip('﻿'))
        out[c.get('id', name[:-5])] = c
    return out


def check_divergence_binding(now_cases, ents, base_cases_, n_base_entries):
    """Rule 6 (pure). base_cases_: {id: case} at the base ({} on the bootstrap); n_base_entries: ledger length at the base."""
    appended = {e['id']: e for e in ents[n_base_entries:]}
    for cid, c in now_cases.items():
        old = {d['adapter']: d for d in (base_cases_.get(cid) or {}).get('known_divergences') or []}
        for d in c['known_divergences']:
            if old.get(d['adapter']) is not None and schema.canonical(old[d['adapter']]) == schema.canonical(d):
                continue                              # unchanged since the base: bound when it was introduced
            tag = f"{cid} [{d['adapter']}]: known divergence new / changed against the base, correction {d['correction']}"
            e = appended.get(d['correction'])
            assert e is not None, f'{tag} must point at a ledger record appended after the base (the one that introduces it)'
            rec = e['cases'].get(cid) or {}
            assert e['type'] in CONTRACT_TYPES and rec.get('contract_sha') == schema.contract_sha(c), \
                f"{tag}: the pointed record must carry the case's current contract_sha (the contract that contains the divergence)"


def check_against_base(base_man, base_led, ents, now):
    """Rule 5 (pure, unit-tested below)."""
    assert base_man.get('schema') == MANIFEST_SCHEMA, f"base manifest schema {base_man.get('schema')!r} != {MANIFEST_SCHEMA!r}"
    old = base_led['entries']
    assert ents[:len(old)] == old, 'CORRECTIONS.json is append-only: base entries were edited, reordered or removed'
    new = ents[len(old):]
    recs = lambda cid, typ: [e['cases'][cid] for e in new if e['type'] == typ and cid in e['cases']]
    for cid, sha in now.items():
        if cid not in base_man['cases']:
            assert any(r['contract_sha'] == sha for r in recs(cid, 'add')), f'{cid}: new case without an appended `add` record'
        elif base_man['cases'][cid] != sha:
            assert any(r['prev_contract_sha'] == base_man['cases'][cid] and r['contract_sha'] == sha for r in recs(cid, 'correction')), \
                f"{cid}: contract changed ({base_man['cases'][cid][:12]} -> {sha[:12]}) without an appended correction record"
    for cid, sha in base_man['cases'].items():
        if cid not in now:
            assert any(r['prev_contract_sha'] == sha for r in recs(cid, 'retire')), f'{cid} removed without an appended `retire` record'


def test_changes_against_the_base_are_ledgered():
    ref, sha = golden_base()
    pack = base_pack(sha)
    if pack is None:
        pytest.skip(f'bootstrap: base {ref} ({sha[:12]}) has no golden pack in its tree - rules 1-4 still apply')
    check_against_base(*pack, _ledger(), computed_manifest())


def test_divergences_are_bound_to_the_record_that_introduced_them():
    ref, sha = golden_base()
    pack = base_pack(sha)
    n_base = len(pack[1]['entries']) if pack else 0       # bootstrap: every entry is new, every divergence is new
    check_divergence_binding({c['id']: c for c in schema.load_all()}, _ledger(), base_cases(sha) if pack else {}, n_base)


def test_base_rule_unit():
    """Rule 5 on synthetic data: every bypass Codex listed on 4e03152 fails."""
    a, b, c = 'a' * 64, 'b' * 64, 'c' * 64
    g = dict(id='CORR-0000', type='genesis', cases={'X': {'new_expect_sha': a}})
    base_man = dict(schema=MANIFEST_SCHEMA, cases={'X': a})
    base_led = dict(entries=[g])
    check_against_base(base_man, base_led, [g], {'X': a})                                    # unchanged
    corr = dict(id='CORR-0001', type='correction', cases={'X': dict(prev_contract_sha=a, contract_sha=b)})
    check_against_base(base_man, base_led, [g, corr], {'X': b})                              # ledgered change
    for ents, now in (([g], {'X': b}),                                                       # changed, no record
                      ([g, dict(corr, cases={'X': dict(prev_contract_sha=c, contract_sha=b)})], {'X': b}),   # wrong prev
                      ([g], {'X': a, 'Y': c}),                                               # new case, no add
                      ([g, dict(corr, type='correction', cases={'Y': dict(prev_contract_sha=None, contract_sha=c)})], {'X': a, 'Y': c}),
                      ([g], {}),                                                             # removed, no retire
                      ([dict(g, title='edited')], {'X': a}),                                 # base entry edited
                      ([], {'X': a})):                                                       # base entry removed
        with pytest.raises(AssertionError):
            check_against_base(base_man, base_led, ents, now)
    with pytest.raises(AssertionError):                                                      # old-format base
        check_against_base(dict(base_man, schema='zb-golden-manifest/1'), base_led, [g], {'X': a})


def test_history_rule_unit():
    """Rule 3 on synthetic data."""
    a, b = 'a' * 64, 'b' * 64
    E = lambda i, t, cases: dict(id=f'CORR-{i:04d}', type=t, cases=cases)
    ok = [E(4, 'add', {'X': dict(prev_contract_sha=None, contract_sha=a)}),
          E(5, 'correction', {'X': dict(prev_contract_sha=a, contract_sha=b)})]
    assert case_history(ok)['X']['sha'] == b
    for bad in ([E(4, 'correction', {'X': dict(prev_contract_sha=None, contract_sha=a)})],                       # never added
                [ok[0], E(5, 'correction', {'X': dict(prev_contract_sha=b, contract_sha=a)})],                  # broken link
                [ok[0], E(5, 'add', {'X': dict(prev_contract_sha=a, contract_sha=b)})],                         # double add
                [ok[0], E(5, 'correction', {'X': dict(contract_sha=b)})],                                       # missing prev
                [ok[0], E(5, 'retire', {'X': dict(prev_contract_sha=a)}), E(6, 'correction', {'X': dict(prev_contract_sha=a, contract_sha=b)})]):
        with pytest.raises(AssertionError):
            case_history(bad)


def test_ci_workflows_pass_and_fetch_the_base():
    """P1-1: every workflow that runs the test suite names the base explicitly and makes it available in its checkout."""
    wf = lambda n: open(os.path.join(REPO_ROOT, '.github', 'workflows', n), encoding='utf-8').read()
    fetch = 'git fetch --no-tags --depth=1 origin "$ZB_GOLDEN_BASE"'
    for name, run in (('verify-fast.yml', 'python verify.py fast'), ('verify.yml', 'python verify_ci.py part')):
        s = wf(name)
        assert 'ZB_GOLDEN_BASE: ${{ github.event.pull_request.base.sha }}' in s and fetch in s, name
        assert s.index(fetch) < s.index(run), f'{name}: the base must be fetched before the tests run'
    s = wf('verify-push.yml')
    assert 'ZB_GOLDEN_BASE: ${{ github.event.before }}' in s and 'fetch-depth: 0' in s
