"""Golden ID registries as DATA files, anchored against the immutable base tree (Codex golden r5 ruling: two claimed
identities were not historically anchored).

BEHAVIOURS.json (zb-golden-behaviours/1): the ordered behaviour-ID registry [{id, meaning}] plus `deprecated` (id ->
replacement); schema.BEHAVIOUR_MEANING is read from it, so the meanings no longer live in the test file that also pins them.

Anchor (fix 1, base-derived): test_ledger reads the BASE tree's BEHAVIOURS.json with `git show` (data, never code) and
requires the current registry to EXTEND it (extends_behaviours): the same version, every base entry at the same position with
the same ID and the same meaning, base deprecations kept, new IDs only appended. A meaning edited together with any local pin,
a reorder, a removal or a version swap fails against the base. A base tree without the file is the bootstrap (the change that
introduces the registry) and is the only case that skips; the local pin in test_vocabulary stays as defence in depth.
Why base-derived and not a MANIFEST digest + ledger record: the base tree is immutable evidence the change cannot rewrite, and
the check needs no new record type; a digest in MANIFEST.json would only move the question to "who may write the record".

DIVERGENCES.json (zb-golden-divergences/1, fix 2): the append-only registry of recorded legacy defects
[{id, adapters, ticket, finding, status}], status active | retired. One defect per ID and one ID per defect: IDs are unique
across the whole file (retired entries included) and so is the (ticket, finding) meaning. schema.validate() requires every
case known divergence to name an ACTIVE entry, with its adapter in the entry's scope and the entry's exact ticket / finding.
Base-derived rules (extends_divergences, applied by test_ledger to the base tree's copy): every base entry stays at its
position with the same id, adapters, ticket and finding; the only change it may take is active -> retired; new entries are
only appended. So a rename (the base entry's id edited), a semantic rebinding (same id, other ticket / finding / scope), a
removal, a reorder, an un-retirement and a re-append of a retired id (duplicate) all fail; a retired id is a permanent
tombstone. A case's divergence that the base case already carried on an adapter keeps its divergence_id while it exists
(test_ledger rule 10), so a consistent rename through a ledgered correction fails even with a freshly appended ID.
Registry changes need no ledger record: the registry holds no case outcome, every case-visible change (divergence_id, ticket,
finding are inside contract_sha) is already ledgered, and the registry itself is checked against the immutable base.
"""
import json, os, re

from . import GOLDEN_DIR

BEHAVIOURS_PATH = os.path.join(GOLDEN_DIR, 'BEHAVIOURS.json')
BEHAVIOURS_REL = 'tests/golden/BEHAVIOURS.json'
BEHAVIOURS_SCHEMA = 'zb-golden-behaviours/1'
BEHAVIOUR_ID = re.compile(r'[a-z][a-z0-9_]*')
MEANING_MIN = 20
DIVERGENCES_PATH = os.path.join(GOLDEN_DIR, 'DIVERGENCES.json')
DIVERGENCES_REL = 'tests/golden/DIVERGENCES.json'
DIVERGENCES_SCHEMA = 'zb-golden-divergences/1'
# Stable identity of a recorded legacy defect (Codex golden r3 residual ruling point 2), e.g. AUD07-C11, AUD07-TP-GAP-OPEN.
DIVERGENCE_ID = re.compile(r'[A-Z0-9]+(-[A-Z0-9]+)+')
DIVERGENCE_KEYS = ('id', 'adapters', 'ticket', 'finding', 'status')
DIVERGENCE_STATUS = ('active', 'retired')


class RegistryError(ValueError):
    """A registry file that is malformed, or that does not extend its base. Always a hard failure, never a skip."""


def _req(cond, what, msg):
    if not cond:
        raise RegistryError(f'{what}: {msg}')


def _no_constant(name):
    raise ValueError(f'JSON constant {name} is not allowed')


def _no_dup_keys(pairs):
    keys = [k for k, _ in pairs]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        raise ValueError(f'duplicate JSON keys {dup}')
    return dict(pairs)


def loads(text, what='registry'):
    """Strict parse: NaN / Infinity and duplicate object keys are rejected (a duplicate key would silently keep the last)."""
    try:
        return json.loads(text.lstrip('﻿'), parse_constant=_no_constant, object_pairs_hook=_no_dup_keys)
    except ValueError as e:
        raise RegistryError(f'{what}: not a readable JSON registry ({e})') from None


def read(path):
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise RegistryError(f'{os.path.basename(path)}: cannot read ({type(e).__name__}: {e})') from None
    return loads(text, os.path.basename(path))


# ------------------------------------------------------------------ behaviours
def parse_behaviours(doc, what='BEHAVIOURS.json'):
    """-> (version, {id: meaning} in registry order, {deprecated id: replacement}); RegistryError when malformed."""
    _req(isinstance(doc, dict) and set(doc) == {'schema', 'behaviours', 'deprecated'}, what,
         'exactly the keys schema, behaviours, deprecated')
    _req(doc['schema'] == BEHAVIOURS_SCHEMA, what, f"schema {doc['schema']!r} is not {BEHAVIOURS_SCHEMA!r}")
    ents = doc['behaviours']
    _req(isinstance(ents, list) and ents, what, 'behaviours: a non-empty ordered list')
    out = {}
    for k, e in enumerate(ents):
        _req(isinstance(e, dict) and set(e) == {'id', 'meaning'}, what, f'behaviours[{k}]: exactly the keys id, meaning')
        _req(isinstance(e['id'], str) and BEHAVIOUR_ID.fullmatch(e['id']), what, f"behaviours[{k}]: id {e['id']!r} must match "
             f'{BEHAVIOUR_ID.pattern}')
        _req(e['id'] not in out, what, f"behaviours[{k}]: id {e['id']!r} listed twice (one meaning per ID)")
        _req(isinstance(e['meaning'], str) and len(e['meaning'].strip()) >= MEANING_MIN, what,
             f"behaviours[{k}] ({e['id']}): the meaning is stated in at least {MEANING_MIN} characters")
        out[e['id']] = e['meaning']
    dep = doc['deprecated']
    _req(isinstance(dep, dict), what, 'deprecated: {id: replacement id}')
    for old, new in dep.items():
        _req(old in out and new in out and new not in dep and old != new, what,
             f'deprecated: {old!r} -> {new!r} must name two registered IDs (the replacement not itself deprecated)')
    return doc['schema'], out, dict(dep)


def load_behaviours(path=BEHAVIOURS_PATH):
    return parse_behaviours(read(path), os.path.basename(path))


def extends_behaviours(base_doc, now_doc):
    """Fix 1 (pure): the current behaviour registry extends the base one. RegistryError names the first violation."""
    bv, base, bdep = parse_behaviours(base_doc, 'base BEHAVIOURS.json')
    nv, now, ndep = parse_behaviours(now_doc, 'BEHAVIOURS.json')
    _req(nv == bv, 'BEHAVIOURS.json', f'registry version {nv!r} differs from the base {bv!r} (a version is never swapped '
         'inside a schema; the base registry stays the anchor)')
    b_ids, n_ids = list(base), list(now)
    _req(n_ids[:len(b_ids)] == b_ids, 'BEHAVIOURS.json',
         f'append-only against the base: the base IDs {b_ids} are not the prefix of {n_ids} (an ID was removed, renamed or '
         'reordered; deprecate it instead)')
    changed = [b for b in b_ids if now[b] != base[b]]
    _req(not changed, 'BEHAVIOURS.json', f'the meaning of base IDs {changed} changed: an ID keeps the one meaning it was '
         'published with (append a new ID instead)')
    lost = {k: v for k, v in bdep.items() if ndep.get(k) != v}
    _req(not lost, 'BEHAVIOURS.json', f'base deprecations {lost} were removed or re-pointed (a deprecation is permanent)')


# ------------------------------------------------------------------ divergences
def parse_divergences(doc, adapters, what='DIVERGENCES.json'):
    """-> tuple of entries in registry order; RegistryError when malformed. adapters: the adapter names a scope may hold."""
    _req(isinstance(doc, dict) and set(doc) == {'schema', 'divergences'}, what, 'exactly the keys schema, divergences')
    _req(doc['schema'] == DIVERGENCES_SCHEMA, what, f"schema {doc['schema']!r} is not {DIVERGENCES_SCHEMA!r}")
    ents = doc['divergences']
    _req(isinstance(ents, list), what, 'divergences: an ordered list')
    ids, meanings = {}, {}
    for k, e in enumerate(ents):
        _req(isinstance(e, dict) and set(e) == set(DIVERGENCE_KEYS), what, f'divergences[{k}]: exactly the keys {DIVERGENCE_KEYS}')
        i = e['id']
        _req(isinstance(i, str) and DIVERGENCE_ID.fullmatch(i), what, f'divergences[{k}]: id {i!r} must match {DIVERGENCE_ID.pattern}')
        _req(i not in ids, what, f'divergences[{k}]: id {i!r} is already divergences[{ids.get(i)}] - an ID is never reused (a '
             'retired ID is a permanent tombstone)')
        ids[i] = k
        sc = e['adapters']
        _req(isinstance(sc, list) and sc and all(isinstance(a, str) for a in sc) and sc == sorted(set(sc)) and set(sc) <= set(adapters),
             what, f'divergences[{k}] ({i}): adapters = a non-empty sorted list of distinct adapters from {tuple(adapters)}')
        for f in ('ticket', 'finding'):
            _req(isinstance(e[f], str) and e[f].strip() and e[f] == e[f].strip(), what,
                 f'divergences[{k}] ({i}): {f} is a non-empty string without outer whitespace')
        m = (e['ticket'], e['finding'])
        _req(m not in meanings, what, f'divergences[{k}] ({i}): ticket/finding {m} is already recorded as {meanings.get(m)} - one '
             'ID per recorded defect (a second ID for the same defect is a rename)')
        meanings[m] = i
        _req(e['status'] in DIVERGENCE_STATUS, what, f'divergences[{k}] ({i}): status must be one of {DIVERGENCE_STATUS}')
    return tuple(ents)


def load_divergences(adapters, path=DIVERGENCES_PATH):
    return parse_divergences(read(path), adapters, os.path.basename(path))


def extends_divergences(base_doc, now_doc, adapters):
    """Fix 2 (pure): the current divergence registry extends the base one. RegistryError names the first violation."""
    base = parse_divergences(base_doc, adapters, 'base DIVERGENCES.json')
    now = parse_divergences(now_doc, adapters, 'DIVERGENCES.json')
    _req(len(now) >= len(base), 'DIVERGENCES.json', f'{len(base) - len(now)} base entries were removed (retire an ID instead)')
    for k, b in enumerate(base):
        n = now[k]
        _req(n['id'] == b['id'], 'DIVERGENCES.json', f"divergences[{k}] is {n['id']!r} but the base has {b['id']!r} there: a "
             'base entry is never renamed, removed or reordered (new IDs are appended)')
        moved = [f for f in ('adapters', 'ticket', 'finding') if n[f] != b[f]]
        _req(not moved, 'DIVERGENCES.json', f"{b['id']}: {moved} differ from the base - an ID is never rebound to another "
             'defect or scope (append a new ID instead)')
        _req(n['status'] == b['status'] or (b['status'], n['status']) == ('active', 'retired'), 'DIVERGENCES.json',
             f"{b['id']}: status {b['status']} -> {n['status']}: the only allowed change is active -> retired (a retired ID is "
             'never reused)')
