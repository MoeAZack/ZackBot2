"""Store-level documents of NC-02b (design 3.2-3.6) and their strict validators. Pure.

- Reader: what this build reads and writes (format versions, writer_seq, migrators). D8: builds are ordered by the
  integer writer_seq; writer_build is display text.
- Settings (D13): a record inside every snapshot, with its own format version (a future settings record is rule 1).
- Provenance: how a generation came to be and how far it may be trusted (MANAGED / HOLD / HOLD_INIT), the
  reconciliation record that proves it, the incidents and evidence it carries, its HOLD items.
- HEAD (D1) and anchor (D2) slot documents.
Ids derived here are pure functions of journaled / committed data (no clock, no randomness), and NOTHING parses an id.
"""
from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass, field

FORMAT_HEAD = 'zackbot.newcore.head'
FORMAT_ANCHOR = 'zackbot.newcore.anchor'
FORMAT_SNAPSHOT = 'zackbot.newcore.snapshot'
FORMAT_SETTINGS = 'zackbot.newcore.settings'
STORE_FORMAT = 1
WRITER_SEQ = 2                       # NC-02a = 1 (journal only), NC-02b = 2
WRITER_BUILD = 'nc-02b'
SETTING_KEY_RE = re.compile(r'[a-z0-9_.]{1,64}')
SHA_RE = re.compile(r'[0-9a-f]{64}')
SNAP_NAME_RE = re.compile(r'g([0-9]{20})\.snap')
INT64 = 2 ** 63 - 1


class Trust(enum.StrEnum):
    MANAGED = 'managed'
    HOLD = 'hold'
    HOLD_INIT = 'hold_init'


class ProvenanceKind(enum.StrEnum):
    INIT_FLAT = 'init_flat'           # first run, flat exchange, confirmed binding: known-empty
    INIT_HOLD = 'init_hold'           # first run against a non-flat exchange (HOLD-INIT)
    PROMOTION = 'promotion'           # reconciliation match (or owner resolution), re-checked atomically
    HOLD = 'hold'                     # a HOLD entry (damage, missing member, identity, rollback)
    MIGRATION = 'migration'           # F<R translated into a new generation (always HOLD)
    CHECKPOINT = 'checkpoint'         # the runner's own snapshot of a MANAGED portfolio


@dataclass(frozen=True)
class Reader:
    """What this build reads. migrators: {older snapshot format: fn(old snapshot body dict) -> current body dict}."""
    snapshot_format: int = 1
    store_format: int = STORE_FORMAT
    writer_seq: int = WRITER_SEQ
    writer_build: str = WRITER_BUILD
    migrators: dict = field(default_factory=dict)


class RecordError(ValueError):
    """A store document that is structurally invalid (damage). Never names content."""


def req(cond, msg):
    if not cond:
        raise RecordError(msg)


def is_count(v):
    return type(v) is int and 0 <= v <= INT64


def sha256_hex(b):
    return hashlib.sha256(bytes(b)).hexdigest()


def snap_name(generation):
    return f'g{generation:020d}.snap'


def derive_hex(tag, *parts):
    h = hashlib.sha256(b'zackbot.newcore.store.' + tag.encode('ascii') + b'.v1')
    for p in parts:
        h.update(b'\0' + str(p).encode('ascii'))
    return h.hexdigest()


def reconciliation_id(account_id, taken_ms, generation):
    """rec_ id of the reconciliation that proves `generation` (NC-01 id shape; never parsed)."""
    return 'rec_' + derive_hex('reconciliation_id', account_id, taken_ms, generation)[:32]


def incident_id(account_id, member, sha256):
    return 'inc-' + derive_hex('incident_id', account_id, member, sha256)[:24]


# ---------------------------------------------------------------------------------------------------- settings (D13)
def settings_doc(values):
    return {'format': FORMAT_SETTINGS, 'format_version': 1, 'min_reader_version': 1, 'values': dict(values)}


def validate_settings(doc):
    req(type(doc) is dict and set(doc) == {'format', 'format_version', 'min_reader_version', 'values'}, 'settings keys')
    req(doc['format'] == FORMAT_SETTINGS, 'settings format')
    vals = doc['values']
    req(type(vals) is dict and len(vals) <= 256, 'settings values')
    for k, v in vals.items():
        req(SETTING_KEY_RE.fullmatch(k) is not None, 'settings key')
        req(type(v) is str and len(v) <= 256 and v.isprintable(), 'settings value')
    return dict(vals)


# ---------------------------------------------------------------------------------------------------- provenance
RECON_KEYS = frozenset({'recon_id', 'taken_ms', 'key_digest', 'verdict', 'snapshot_sha256'})
ITEM_KEYS = frozenset({'scope', 'cause', 'ref'})
EVIDENCE_KEYS = frozenset({'name', 'sha256', 'size', 'source', 'rel_path', 'incident_id'})
PROV_KEYS = frozenset({'kind', 'trust', 'from_generation', 'recon', 'incident_ids', 'evidence', 'items', 'migration',
                       'decision_id'})


def provenance(kind, trust, *, from_generation=None, recon=None, incident_ids=(), evidence=(), items=(), migration=None,
               decision_id=None):
    return {'kind': str(ProvenanceKind(kind)), 'trust': str(Trust(trust)), 'from_generation': from_generation,
            'recon': recon, 'incident_ids': sorted(set(incident_ids)),
            'evidence': [dict(e) for e in evidence], 'items': [dict(i) for i in items], 'migration': migration,
            'decision_id': decision_id}


def validate_provenance(doc):
    req(type(doc) is dict and set(doc) == PROV_KEYS, 'provenance keys')
    try:
        kind, trust = ProvenanceKind(doc['kind']), Trust(doc['trust'])
    except ValueError:
        raise RecordError('provenance kind / trust') from None
    req(doc['from_generation'] is None or is_count(doc['from_generation']), 'provenance from_generation')
    r = doc['recon']
    if r is not None:
        req(type(r) is dict and set(r) == RECON_KEYS and is_count(r['taken_ms']) and type(r['recon_id']) is str
            and type(r['key_digest']) is str and r['verdict'] in ('flat', 'match', 'owner_resolved')
            and type(r['snapshot_sha256']) is str and SHA_RE.fullmatch(r['snapshot_sha256']) is not None,
            'provenance recon')
    req(type(doc['incident_ids']) is list and all(type(i) is str for i in doc['incident_ids']), 'provenance incidents')
    req(type(doc['evidence']) is list and all(type(e) is dict and set(e) == EVIDENCE_KEYS for e in doc['evidence']),
        'provenance evidence')
    req(type(doc['items']) is list and all(type(i) is dict and set(i) == ITEM_KEYS for i in doc['items']),
        'provenance items')
    m = doc['migration']
    req(m is None or (type(m) is dict and set(m) == {'from_format', 'from_sha256'}), 'provenance migration')
    req(doc['decision_id'] is None or type(doc['decision_id']) is str, 'provenance decision')
    req((trust is Trust.MANAGED) <= (kind in (ProvenanceKind.INIT_FLAT, ProvenanceKind.PROMOTION,
                                              ProvenanceKind.CHECKPOINT)), 'only init_flat / promotion / checkpoint '
                                                                             'generations are MANAGED')
    req((kind in (ProvenanceKind.INIT_FLAT, ProvenanceKind.PROMOTION)) <= (r is not None), 'a proven generation names '
                                                                                            'its reconciliation')
    req((kind is ProvenanceKind.MIGRATION) == (m is not None) and (kind is not ProvenanceKind.MIGRATION
                                                                  or trust is Trust.HOLD), 'a migration is HOLD')
    return doc


# ---------------------------------------------------------------------------------------------------- HEAD / anchor
SNAPREF_KEYS = frozenset({'generation', 'name', 'sha256', 'len'})
HEAD_KEYS = frozenset({'account_id', 'binding_digest', 'commit_seq', 'format', 'format_version', 'min_reader_version',
                       'generation', 'snapshot', 'retained', 'quarantined', 'retired', 'high_water', 'writer_history',
                       'open_incidents', 'written_ms'})
ANCHOR_KEYS = frozenset({'account_id', 'binding_digest', 'commit_seq', 'format', 'format_version', 'min_reader_version',
                         'generation', 'writer_seq', 'hard_hold', 'written_ms'})


def _snapref(d):
    req(type(d) is dict and set(d) == SNAPREF_KEYS and is_count(d['generation']) and d['generation'] >= 1
        and d['name'] == snap_name(d['generation']) and type(d['sha256']) is str and SHA_RE.fullmatch(d['sha256'])
        and is_count(d['len']), 'snapshot reference')


def validate_head(doc):
    req(set(doc) == HEAD_KEYS and doc['format'] == FORMAT_HEAD, 'HEAD keys')
    req(is_count(doc['commit_seq']) and doc['commit_seq'] >= 1 and is_count(doc['generation']) and is_count(
        doc['written_ms']), 'HEAD counters')
    _snapref(doc['snapshot'])
    req(doc['snapshot']['generation'] == doc['generation'], 'HEAD generation')
    req(type(doc['retained']) is list and type(doc['quarantined']) is list and type(doc['retired']) is list,
        'HEAD lists')
    for r in doc['retained']:
        _snapref(r)
        req(r['generation'] < doc['generation'], 'HEAD retained generation')
    for q in doc['quarantined']:
        req(type(q) is dict and set(q) == {'generation', 'name', 'incident_id'} and is_count(q['generation']),
            'HEAD quarantined')
    req(all(type(n) is str and SNAP_NAME_RE.fullmatch(n) for n in doc['retired']), 'HEAD retired')
    hw = doc['high_water']
    req(type(hw) is dict and set(hw) == {'generation', 'writer_seq'} and is_count(hw['generation'])
        and is_count(hw['writer_seq']) and hw['generation'] >= doc['generation'], 'HEAD high_water')
    wh = doc['writer_history']
    req(type(wh) is list and wh and all(type(w) is dict and set(w) == {'writer_seq', 'from_commit'}
                                        and is_count(w['writer_seq']) and is_count(w['from_commit']) for w in wh),
        'HEAD writer_history')
    req(type(doc['open_incidents']) is list and all(type(i) is str for i in doc['open_incidents']), 'HEAD incidents')
    req(type(doc['binding_digest']) is str and type(doc['account_id']) is str, 'HEAD identity')


def validate_anchor(doc):
    req(set(doc) == ANCHOR_KEYS and doc['format'] == FORMAT_ANCHOR, 'anchor keys')
    req(is_count(doc['commit_seq']) and doc['commit_seq'] >= 1 and is_count(doc['generation'])
        and is_count(doc['writer_seq']) and is_count(doc['written_ms']) and type(doc['hard_hold']) is bool,
        'anchor fields')
    req(type(doc['binding_digest']) is str and type(doc['account_id']) is str, 'anchor identity')


def writer_regressed(history):
    """True when an older writer ran after a newer one (A03 / rule 2)."""
    seqs = [w['writer_seq'] for w in sorted(history, key=lambda w: w['from_commit'])]
    return any(b < a for a, b in zip(seqs, seqs[1:]))
