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
"""
import json, os, re

from . import GOLDEN_DIR

BEHAVIOURS_PATH = os.path.join(GOLDEN_DIR, 'BEHAVIOURS.json')
BEHAVIOURS_REL = 'tests/golden/BEHAVIOURS.json'
BEHAVIOURS_SCHEMA = 'zb-golden-behaviours/1'
BEHAVIOUR_ID = re.compile(r'[a-z][a-z0-9_]*')
MEANING_MIN = 20


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
