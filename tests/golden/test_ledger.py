"""Golden pack contract version: MANIFEST.json (sha256 of every case's canonical `expect`) + CORRECTIONS.json (append-only
ledger). Design section 6: no silent re-baselining - an `expect` block changes only through a correction record.

Rules enforced here:
1. MANIFEST.json equals the hashes computed from cases/ (every case listed, nothing extra).
2. CORRECTIONS.json is a hash chain: entry k carries prev_entry_sha = sha256(canonical(entry k-1)); ids CORR-0000, -0001, ...
3. Against the merge base (origin/master), when git can read it:
   - the merge-base ledger is a prefix of this ledger (append-only: nothing edited or removed);
   - a case whose expect hash changed needs a NEW ledger entry with cases[id] = {old_expect_sha: <merge base>,
     new_expect_sha: <now>}; a removed case needs a new entry of type "retire" naming it.
4. A known divergence that names a correction id names one that exists in the ledger.
"""
import json, os, subprocess, sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from goldenlib import LEDGER_PATH, MANIFEST_PATH, REPO_ROOT, schema  # noqa: E402

BASE_REF = os.environ.get('ZB_GOLDEN_BASE', 'origin/master')


def _json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def computed_manifest():
    return {c['id']: schema.expect_sha(c) for c in schema.load_all()}


def _git_show(rel):
    try:
        r = subprocess.run(['git', 'show', f'{BASE_REF}:{rel}'], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return json.loads(r.stdout) if r.returncode == 0 else None


def test_manifest_matches_the_cases():
    man = _json(MANIFEST_PATH)
    assert man.get('schema') == 'zb-golden-manifest/1'
    got = computed_manifest()
    diff = {k: (man['cases'].get(k), got.get(k)) for k in set(man['cases']) | set(got) if man['cases'].get(k) != got.get(k)}
    assert not diff, ('golden expect blocks differ from MANIFEST.json (manifest, now). An expect block changes only as an accepted '
                      'correction: update MANIFEST.json AND append a CORRECTIONS.json entry with old/new sha. ' + json.dumps(diff, indent=1))


def test_ledger_is_a_hash_chain():
    led = _json(LEDGER_PATH)
    assert led.get('schema') == 'zb-golden-ledger/1'
    ents = led['entries']
    assert ents and ents[0]['type'] == 'genesis'
    for k, e in enumerate(ents):
        assert e['id'] == f'CORR-{k:04d}', e['id']
        for f in ('type', 'ticket', 'title', 'cases', 'rationale', 'date_cairo', 'prev_entry_sha'):
            assert f in e, (e['id'], f)
        assert e['type'] in ('genesis', 'correction', 'retire', 'add'), e['type']
        assert e['prev_entry_sha'] == (schema.sha256(ents[k - 1]) if k else None), f"{e['id']}: broken chain (an entry was edited or removed)"


def test_known_divergences_name_existing_corrections():
    ids = {e['id'] for e in _json(LEDGER_PATH)['entries']}
    for c in schema.load_all():
        for d in c['known_divergences']:
            assert d.get('correction') in (None, *ids), (c['id'], d.get('correction'))


def test_changes_against_the_merge_base_are_ledgered():
    base_man = _git_show('tests/golden/MANIFEST.json')
    base_led = _git_show('tests/golden/CORRECTIONS.json')
    if base_man is None or base_led is None:
        pytest.skip(f'{BASE_REF} has no golden manifest/ledger (first slice of the pack, or no git history): rules 1-2 still apply')
    ents = _json(LEDGER_PATH)['entries']
    old = base_led['entries']
    assert ents[:len(old)] == old, 'CORRECTIONS.json is append-only: the merge-base entries were edited or removed'
    new = ents[len(old):]
    now = computed_manifest()
    for cid, sha in base_man['cases'].items():
        if cid not in now:
            assert any(e['type'] == 'retire' and cid in e['cases'] for e in new), f'{cid} removed without a retire entry'
        elif now[cid] != sha:
            assert any(e['cases'].get(cid, {}).get('old_expect_sha') == sha and e['cases'][cid].get('new_expect_sha') == now[cid]
                       for e in new), f'{cid}: expect changed ({sha[:12]} -> {now[cid][:12]}) without a correction entry'
