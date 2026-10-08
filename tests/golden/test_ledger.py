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
   changed contract an appended `correction` record from the base hash, a removed case a `retire` record; the appended records
   of EVERY case (still present, removed, or added and retired inside the change) form one linked chain (check_case_chain):
   it starts at the base (an `add` from nothing, or a correction / retire from the base hash), each later record is a linked
   correction or retire, a retire is the single last record (nothing after it) and is present exactly when the case is gone,
   otherwise the chain ends at the current contract; an unchanged case has no stray record.
   Base = $ZB_GOLDEN_BASE (CI: the PR base sha / the push's previous commit, fetched explicitly), locally origin/master. An
   unavailable base FAILS; the only skip is a base tree that has no golden pack at all (the bootstrap, i.e. this first pack),
   decided by listing the base tree.
6. Divergence binding (Codex r2 P2, derived from data already in the ledger - no record is rewritten): a known divergence that
   is NEW or CHANGED against the base case file must point at a record appended after the base that carries the case's
   current contract_sha, i.e. the record whose contract first introduced it. A divergence identical to the base's (pointer
   included) was bound when it was introduced and keeps its pointer through later unrelated corrections of the case. On the
   bootstrap base every divergence is new.
7. ZB_GOLDEN_BASE may not resolve to HEAD when HEAD itself changes the pack (or a registry) against its parent (Cowork r2
   (d)): that base would make every change in HEAD look already-ledgered.
8. Registry anchor (Codex golden r5 ruling #1): BEHAVIOURS.json extends the base tree's copy (goldenlib.registry
   .extends_behaviours: same version, base IDs and meanings unchanged at their positions, appends only). The only skip is a
   base tree without the file (the bootstrap: the change that introduces it), decided by listing the base tree.
9. Divergence registry anchor (Codex golden r5 ruling #2): DIVERGENCES.json extends the base tree's copy
   (registry.extends_divergences: base entries keep position, id, adapters, ticket and finding; only active -> retired;
   appends only; IDs and (ticket, finding) unique across the file, so a retired ID is never reused). Same bootstrap skip.
   No ledger record for a registry change: it holds no case outcome, and every case-visible change moves contract_sha.
10. Divergence ID stability: a known divergence the BASE case already carried on an adapter (with a divergence_id) keeps that
   divergence_id while the case still records a divergence on that adapter - a consistent rename through a ledgered
   correction fails even when the new ID is registered. A different defect there means retiring the case and adding a new one.
"""
import json, os, re, subprocess, sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from goldenlib import LEDGER_PATH, MANIFEST_PATH, REPO_ROOT, registry, schema  # noqa: E402

MAN_REL, LED_REL = 'tests/golden/MANIFEST.json', 'tests/golden/CORRECTIONS.json'
REGISTRY_RELS = (registry.BEHAVIOURS_REL, registry.DIVERGENCES_REL)
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
    r = _git('diff', '--quiet', 'HEAD^1', 'HEAD', '--', MAN_REL, LED_REL, CASES_REL, *REGISTRY_RELS)
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


def base_registry(sha, rel):
    """The parsed registry `rel` of the base tree (data via `git show`, never imported code), or None when the base tree has
    no such file (the bootstrap)."""
    r = _git('ls-tree', '--name-only', sha, '--', rel)
    if r.returncode != 0:
        pytest.fail(f'cannot list {rel} in base {sha[:12]}: {r.stderr.strip()[:300]}')
    if not r.stdout.strip():
        return None
    s = _git('show', f'{sha}:{rel}')
    if s.returncode != 0:
        pytest.fail(f'cannot read {rel} at base {sha[:12]}: {s.stderr.strip()[:300]}')
    return registry.loads(s.stdout, f'base {rel}')


def test_behaviour_registry_extends_the_base():
    """Rule 8 (Codex golden r5 ruling #1): the behaviour IDs and meanings are anchored in the immutable base tree."""
    ref, sha = golden_base()
    base = base_registry(sha, registry.BEHAVIOURS_REL)
    if base is None:
        pytest.skip(f'bootstrap: base {ref} ({sha[:12]}) has no {registry.BEHAVIOURS_REL} - this change introduces the registry '
                    '(the local pin in test_vocabulary still applies)')
    registry.extends_behaviours(base, registry.read(registry.BEHAVIOURS_PATH))


def test_divergence_registry_extends_the_base():
    """Rule 9 (Codex golden r5 ruling #2): divergence IDs are an append-only registry anchored in the immutable base tree."""
    ref, sha = golden_base()
    base = base_registry(sha, registry.DIVERGENCES_REL)
    if base is None:
        pytest.skip(f'bootstrap: base {ref} ({sha[:12]}) has no {registry.DIVERGENCES_REL} - this change introduces the registry')
    registry.extends_divergences(base, registry.read(registry.DIVERGENCES_PATH), schema.ADAPTERS)


def check_divergence_id_stability(now_cases, base_cases_):
    """Rule 10 (pure). base_cases_: {id: case} at the base ({} on the bootstrap)."""
    for cid, c in now_cases.items():
        old = {d.get('adapter'): d.get('divergence_id') for d in (base_cases_.get(cid) or {}).get('known_divergences') or []}
        for d in c['known_divergences']:
            was = old.get(d['adapter'])
            assert was is None or d['divergence_id'] == was, \
                (f"{cid} [{d['adapter']}]: divergence_id {was} at the base is now {d['divergence_id']} - a recorded divergence keeps "
                 'its ID (no rename, even ledgered and registered; a different defect here means retiring the case and adding '
                 'a new one)')


def test_divergence_ids_are_stable_against_the_base():
    ref, sha = golden_base()
    check_divergence_id_stability({c['id']: c for c in schema.load_all()}, base_cases(sha) if base_pack(sha) else {})


def test_divergence_id_stability_unit():
    """Rule 10 on synthetic data: a consistent rename (every case, both adapters) fails; keeping the ID, dropping the
    divergence, a new divergence on another adapter and a base case without IDs (the pre-ID bootstrap) pass."""
    kd = lambda a, i: dict(adapter=a, divergence_id=i)
    base = {'X': dict(known_divergences=[kd('legacy_engine', 'AUD07-C11'), kd('legacy_backtest', 'AUD07-C11')]),
            'Y': dict(known_divergences=[dict(adapter='legacy_backtest')])}
    now = lambda x, y=(): {'X': dict(known_divergences=list(x)), 'Y': dict(known_divergences=list(y))}
    check_divergence_id_stability(now([kd('legacy_engine', 'AUD07-C11'), kd('legacy_backtest', 'AUD07-C11')]), base)
    check_divergence_id_stability(now([kd('legacy_engine', 'AUD07-C11')]), base)                      # backtest fixed
    check_divergence_id_stability(now([], [kd('legacy_backtest', 'AUD07-C13A'), kd('legacy_engine', 'AUD07-C13G')]), base)
    check_divergence_id_stability(now([kd('legacy_engine', 'AUD07-C11B')]), {})                         # bootstrap
    for x in ([kd('legacy_engine', 'AUD07-C11B'), kd('legacy_backtest', 'AUD07-C11B')],                 # consistent rename
              [kd('legacy_engine', 'AUD07-C11'), kd('legacy_backtest', 'AUD07-C13A')]):                 # rebound on one adapter
        with pytest.raises(AssertionError, match='keeps its ID'):
            check_divergence_id_stability(now(x), base)


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
    chain = lambda cid: [(e['id'], e['type'], e['cases'][cid]) for e in new if cid in e['cases']]
    touched = {cid for e in new for cid in (e.get('cases') or {})}
    for cid in sorted(set(base_man['cases']) | set(now) | touched):
        check_case_chain(cid, base_man['cases'].get(cid), now.get(cid), chain(cid))


def check_case_chain(cid, start, end, ch):
    """Rule 5 for one case (Codex pre-review of 3f4abea #1: the same chain rule for a case that still exists and for a removed
    one). start: the base contract hash (None: not in the base); end: the current contract hash (None: removed / never here);
    ch: the case's appended records [(entry id, type, record)] in ledger order. The appended records form ONE linked chain:
    - it starts at the base: an `add` from nothing when the case is not in the base, else a `correction` or `retire` whose
      prev_contract_sha is the base hash;
    - each later record is a `correction` or `retire` whose prev_contract_sha is the previous record's contract_sha;
    - a `retire` is the LAST record (exactly one, nothing after it) and is required exactly when the case is gone now;
    - otherwise the chain ends at the case's current contract.
    No appended record: the case must be unchanged against the base."""
    if not ch:
        assert start is not None or end is None, f'{cid}: new case without an appended `add` record'
        assert end is not None, f'{cid} removed without an appended `retire` record'
        assert start == end, f'{cid}: contract changed ({start[:12]} -> {end[:12]}) without an appended correction record'
        return
    eid0, t0, r0 = ch[0]
    if start is None:
        assert t0 == 'add' and r0.get('prev_contract_sha') is None, \
            f'{cid}: new case without an appended `add` record (first appended record is {eid0} {t0})'
    else:
        assert t0 in ('correction', 'retire') and r0.get('prev_contract_sha') == start, \
            (f'{cid}: the first appended record {eid0} ({t0}, prev {str(r0.get("prev_contract_sha"))[:12]}) is not a correction '
             f'or retire from the base contract {start[:12]}')
    for (e0, _, p), (e1, t1, r1) in zip(ch, ch[1:]):
        assert t1 in ('correction', 'retire'), f'{cid}: {e1} is a {t1}; only correction / retire records may follow the first'
        assert r1.get('prev_contract_sha') == p.get('contract_sha'), \
            (f"{cid}: appended records do not chain: {e1} prev {str(r1.get('prev_contract_sha'))[:12]} is not {e0}'s contract "
             f"{str(p.get('contract_sha'))[:12]}")
    retires = [e for e, t, _ in ch if t == 'retire']
    assert len(retires) <= 1, f'{cid}: retired more than once ({retires})'
    assert not retires or ch[-1][1] == 'retire', f'{cid}: records follow its retirement {retires[0]}: {[e for e, *_ in ch]}'
    if end is None:
        assert retires, f'{cid} removed without an appended `retire` record (its chain ends at {ch[-1][0]} {ch[-1][1]})'
    else:
        assert not retires, f'{cid} is retired by {retires[0]} but is still in the pack'
        assert ch[-1][2].get('contract_sha') == end, \
            f'{cid}: its last appended record {ch[-1][0]} is not its current contract {end[:12]}'


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
    # AUD-08 golden clock-ms: add-then-correct and correct-twice inside one change are a linked chain to the current contract
    add_y = dict(id='CORR-0001', type='add', cases={'Y': dict(prev_contract_sha=None, contract_sha=b)})
    fix_y = dict(id='CORR-0002', type='correction', cases={'Y': dict(prev_contract_sha=b, contract_sha=c)})
    check_against_base(base_man, base_led, [g, add_y, fix_y], {'X': a, 'Y': c})
    fix_x2 = dict(id='CORR-0002', type='correction', cases={'X': dict(prev_contract_sha=b, contract_sha=c)})
    check_against_base(base_man, base_led, [g, corr, fix_x2], {'X': c})
    for ents, now in (([g, add_y], {'X': a, 'Y': c}),                                        # add, then changed unrecorded
                      ([g, add_y, dict(fix_y, cases={'Y': dict(prev_contract_sha=a, contract_sha=c)})], {'X': a, 'Y': c}),  # broken link
                      ([g, add_y, dict(fix_y, type='add')], {'X': a, 'Y': c}),               # a second add
                      ([g, corr, dict(fix_x2, cases={'X': dict(prev_contract_sha=a, contract_sha=c)})], {'X': c}),  # broken link
                      ([g, corr], {'X': c})):                                                # last record != current
        with pytest.raises(AssertionError):
            check_against_base(base_man, base_led, ents, now)


def _retire_fixture():
    a, b, c, d = 'a' * 64, 'b' * 64, 'c' * 64, 'd' * 64
    g = dict(id='CORR-0000', type='genesis', cases={'X': {'new_expect_sha': a}})
    E = lambda i, t, cid, prev, new=None: dict(id=f'CORR-{i:04d}', type=t, cases={cid: (
        dict(prev_contract_sha=prev) if t == 'retire' else dict(prev_contract_sha=prev, contract_sha=new))})
    return a, b, c, d, g, E, dict(schema=MANIFEST_SCHEMA, cases={'X': a}), dict(entries=[g])


def test_base_rule_retire_positive_controls():
    """Codex pre-review of 3f4abea #1: valid retirement chains pass."""
    a, b, c, d, g, E, base_man, base_led = _retire_fixture()
    check_against_base(base_man, base_led, [g, E(1, 'retire', 'X', a)], {})                                   # plain retire
    check_against_base(base_man, base_led, [g, E(1, 'correction', 'X', a, b), E(2, 'retire', 'X', b)], {})     # correction, retire
    check_against_base(base_man, base_led, [g, E(1, 'correction', 'X', a, b), E(2, 'correction', 'X', b, c),
                                            E(3, 'retire', 'X', c)], {})                                       # two corrections
    check_against_base(base_man, base_led, [g, E(1, 'add', 'Y', None, b), E(2, 'retire', 'Y', b)], {'X': a})   # add, retire
    check_against_base(base_man, base_led, [g, E(1, 'add', 'Y', None, b), E(2, 'correction', 'Y', b, c),
                                            E(3, 'retire', 'Y', c)], {'X': a})                                 # add, fix, retire
    check_against_base(base_man, base_led, [g, E(1, 'correction', 'X', a, b), E(2, 'correction', 'X', b, a)],
                       {'X': a})                                                                               # changed and back


@pytest.mark.parametrize('mutation', ['broken_link_retire', 'unlinked_correction_then_retire', 'retire_from_wrong_hash',
                                      'duplicate_retire', 'correction_after_retire', 'add_after_retire', 'retire_still_present',
                                      'add_retire_broken_link', 'add_then_retire_then_correct', 'stray_record_unchanged',
                                      'retire_first_on_new_case', 'correction_after_retire_of_corrected'])
def test_base_rule_retire_chain_mutations(mutation):
    """Codex pre-review of 3f4abea #1: a removed case's appended records are ONE linked chain from the base (or its add) that
    ends in exactly one retire whose prev is the last contract, with nothing after it. At 3f4abea rule 5 accepted 10 of these
    12 (only retire_from_wrong_hash and correction_after_retire_of_corrected failed there) and REJECTED the valid
    correction-then-retire control above."""
    a, b, c, d, g, E, base_man, base_led = _retire_fixture()
    ents, now = {
        'broken_link_retire': ([g, E(1, 'correction', 'X', a, b), E(2, 'retire', 'X', a)], {}),               # retire skips b
        'unlinked_correction_then_retire': ([g, E(1, 'correction', 'X', c, d), E(2, 'retire', 'X', a)], {}),
        'retire_from_wrong_hash': ([g, E(1, 'retire', 'X', c)], {}),
        'duplicate_retire': ([g, E(1, 'retire', 'X', a), E(2, 'retire', 'X', a)], {}),
        'correction_after_retire': ([g, E(1, 'retire', 'X', a), E(2, 'correction', 'X', a, b)], {}),
        'add_after_retire': ([g, E(1, 'retire', 'X', a), E(2, 'add', 'X', None, b)], {}),
        'retire_still_present': ([g, E(1, 'retire', 'X', a)], {'X': a}),
        'add_retire_broken_link': ([g, E(1, 'add', 'Y', None, b), E(2, 'retire', 'Y', c)], {'X': a}),
        'add_then_retire_then_correct': ([g, E(1, 'add', 'Y', None, b), E(2, 'retire', 'Y', b),
                                          E(3, 'correction', 'Y', b, c)], {'X': a}),
        'stray_record_unchanged': ([g, E(1, 'correction', 'X', c, d)], {'X': a}),
        'retire_first_on_new_case': ([g, E(1, 'retire', 'Y', None)], {'X': a}),
        'correction_after_retire_of_corrected': ([g, E(1, 'correction', 'X', a, b), E(2, 'retire', 'X', b),
                                                  E(3, 'correction', 'X', b, c)], {}),
    }[mutation]
    with pytest.raises(AssertionError):
        check_against_base(base_man, base_led, ents, now)


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
