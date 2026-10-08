"""The step-0 grammar seed (docs/newcore/ports/GRAMMAR_SEED.md, Codex ruling 6070599699) and its snapshot envelope.

`GrammarSeed` is the COMPLETE JournalGate state after a prefix of one journal: G3 event digests (all of them, v1),
decisions, intents (live and terminal, with their NC-01 content state), lots, lineage ordinals, the G6 protect
fallback and the durable FactIndex. Its only producer is `JournalGate.grammar_seed()`, its only consumer
`JournalGate.rebuild(..., grammar_seed=)`. `check_grammar_seed` is the fail-closed gate (S1..S5); a refusal raises
`GrammarSeedRefused` with the exact code and nothing is rebuilt.

`known_intents(seed)` is the ONE projection the NC-01 chain (`check_event_chain(known_intents=)`) reads: derived from
`SeedIntent`, never stored a second time.

Snapshot binding (Codex ruling, binding clarification): the durable unit is ONE canonical envelope
`SeededSnapshot(snapshot, seed, seed_version, seed_sha256)`. `seal_snapshot` produces its bytes; the store commits them
as one record and keeps `envelope_sha256` in what it commits (HEAD / anchor). `open_snapshot(data, committed_sha256=)`
refuses (S3) a missing envelope / seed, bytes that are not the committed ones (a swapped or uncommitted seed), an inner
seed digest mismatch and a seed whose last_sequence / facts differ from the snapshot's (stale / future seed); S1 an
unsupported seed version. A loose seed document is never a restore input. Ports-only: no I/O here.
"""
from __future__ import annotations

import hashlib
import json

from newcore.domain import (DecisionKey, Evidence, FactIndex, IntentState, OrderIntent, OrderResult, OwnerKind,
                            Purpose, ResultPhase)
from newcore.domain.base import Record, record
from newcore.domain.codec import from_json, to_json
from newcore.domain.errors import DomainError
from newcore.domain.ledger import EventDigest
from newcore.domain.orders import TERMINAL, booking_step_ok, check_result_for_intent, terminal_for
from newcore.domain.snapshot import Snapshot

from .keys import derive_child_intent_id, derive_decision_id, derive_intent_id, derive_lot_id, route_of
from .journal import EXECUTING, SHA_RE, GrammarError

GRAMMAR_SEED_VERSION = 1
SEED_FORMAT = 'zackbot.newcore.ports'
_DOC_KEYS = frozenset({'format', 'record_type', 'seed_version', 'body'})
_PRE_SENT = frozenset({IntentState.PLANNED, IntentState.DURABLE})
_SENT_ONLY = frozenset({IntentState.SUBMITTED, IntentState.WORKING, IntentState.UNKNOWN})


class GrammarSeedRefused(GrammarError):
    """rebuild(grammar_seed=) / open_snapshot refused the seed: no gate is built, NC-02 stops the restart."""

    def __init__(self, code, path, msg):
        self.code = code
        super().__init__(path, f'{code}: {msg}')


def refuse(cond, code, path, msg):
    if not cond:
        raise GrammarSeedRefused(code, path, msg)


# ---------------------------------------------------------------------------------------------------- records
@record
class SeedDecision(Record):
    decision_id: str
    key: DecisionKey | None
    authorized: tuple[str, ...]          # the exact authorized intent-id set, sorted


@record
class SeedIntent(Record):
    intent: OrderIntent                  # the durable record as journaled
    state: IntentState                   # current lifecycle state
    sent_at_ms: int | None               # the SUBMITTED step (G7)
    final: OrderResult | None            # the FINAL result (the superseding one after a late fill)
    superseded: bool                     # r3 3b
    late_result_id: str | None
    late_applied: bool


@record
class SeedLineage(Record):
    owner_id: str
    purpose: Purpose
    next_ordinal: int


@record
class SeedProtect(Record):
    owner_id: str
    intent_id: str


@record
class GrammarSeed(Record):
    seed_version: int
    account_id: str
    aggregate_id: str
    last_sequence: int
    events: tuple[EventDigest, ...]      # by sequence: exactly 1..last_sequence
    decisions: tuple[SeedDecision, ...]  # by decision_id
    intents: tuple[SeedIntent, ...]      # by intent_id
    lots: tuple[str, ...]                # sorted
    lineage: tuple[SeedLineage, ...]     # by (owner_id, purpose)
    last_protect: tuple[SeedProtect, ...]  # by owner_id
    facts: FactIndex


@record
class SeededSnapshot(Record):
    """The one recoverable envelope: the snapshot and the canonical seed of the SAME prefix, committed together."""
    snapshot: Snapshot
    seed: GrammarSeed
    seed_version: int
    seed_sha256: str


# ---------------------------------------------------------------------------------------------------- projection
def known_intents(seed):
    """check_event_chain's `known_intents` (intent_id -> (OrderIntent, state, sent_at_ms)): the seed's live intents."""
    return {si.intent.intent_id: (si.intent, si.state, si.sent_at_ms) for si in seed.intents if si.state not in TERMINAL}


# ---------------------------------------------------------------------------------------------------- the checks
def _sorted_unique(keys, code, path, what):
    refuse(all(a < b for a, b in zip(keys, keys[1:])), code, path, f'{what} must be sorted and unique')


def check_grammar_seed(seed, account_id, aggregate_id):
    """S1..S5 (GRAMMAR_SEED.md section 4). Raises GrammarSeedRefused; returns None when the seed is restorable."""
    refuse(isinstance(seed, GrammarSeed), 'S1', 'grammar_seed', f'not a GrammarSeed ({type(seed).__name__})')
    refuse(seed.seed_version == GRAMMAR_SEED_VERSION, 'S1', 'grammar_seed.seed_version',
           f'{seed.seed_version} is not the supported version {GRAMMAR_SEED_VERSION} (no migration)')
    refuse((seed.account_id, seed.aggregate_id) == (account_id, aggregate_id), 'S2', 'grammar_seed.account_id',
           'seed of another account / aggregate')
    for i, si in enumerate(seed.intents):
        refuse(si.intent.account_id == account_id, 'S2', f'grammar_seed.intents[{i}]', 'intent of another account')
    # S3 prefix coverage
    n = seed.last_sequence
    refuse(n >= 0, 'S3', 'grammar_seed.last_sequence', '>= 0')
    refuse(tuple(d.sequence for d in seed.events) == tuple(range(1, n + 1)), 'S3', 'grammar_seed.events',
           f'must hold exactly sequences 1..{n}')
    refuse(len({d.event_id for d in seed.events}) == len(seed.events), 'S3', 'grammar_seed.events',
           'duplicate event id')
    # S4 owner / lifecycle completeness
    p = 'grammar_seed'
    _sorted_unique([d.decision_id for d in seed.decisions], 'S4', p + '.decisions', 'decision ids')
    _sorted_unique([si.intent.intent_id for si in seed.intents], 'S4', p + '.intents', 'intent ids')
    _sorted_unique(list(seed.lots), 'S4', p + '.lots', 'lot ids')
    _sorted_unique([(x.owner_id, x.purpose.value) for x in seed.lineage], 'S4', p + '.lineage', 'lineage groups')
    _sorted_unique([x.owner_id for x in seed.last_protect], 'S4', p + '.last_protect', 'protect owners')
    decisions = {d.decision_id: d for d in seed.decisions}
    for d in seed.decisions:
        q = f'{p}.decisions[{d.decision_id}]'
        refuse(tuple(sorted(set(d.authorized))) == d.authorized, 'S4', q + '.authorized', 'sorted and unique')
        if d.key is not None:
            refuse(d.decision_id == derive_decision_id(account_id, d.key), 'S4', q, 'keyed id is not derived')
            refuse(d.authorized in ((), (derive_intent_id(account_id, d.key),)), 'S4', q + '.authorized',
                   'a keyed decision authorizes exactly derive_intent_id(account, key) or nothing')
    intents = {si.intent.intent_id: si for si in seed.intents}
    cids = [si.intent.client_order_id for si in seed.intents]
    refuse(len(set(cids)) == len(cids), 'S4', p + '.intents', 'a client id is used twice')
    groups = {}
    for iid, si in intents.items():
        it, q = si.intent, f'{p}.intents[{iid}]'
        dec = decisions.get(it.decision_id)
        refuse(dec is not None and iid in dec.authorized, 'S4', q + '.decision_id',
               'its decision is not seeded or does not authorize it')
        refuse(route_of(iid, it.client_order_id) is not None, 'S4', q + '.client_order_id',
               'not client_id_for(intent, route)')
        if dec.key is not None:
            refuse(it.purpose is dec.key.purpose, 'S4', q + '.purpose', 'differs from the decision key')
        if it.owner_id is None:
            refuse(dec.key is not None, 'S4', q + '.owner_id', 'an unkeyed intent needs its lineage owner')
            continue
        if it.owner_kind is OwnerKind.ENTRY_INTENT:
            o = intents.get(it.owner_id)
            refuse(o is not None and o.intent.purpose is Purpose.ENTRY, 'S4', q + '.owner_id', 'owner is no seeded entry')
        else:
            refuse(it.owner_id in seed.lots, 'S4', q + '.owner_id', 'owner is no seeded lot')
        groups[(it.owner_id, it.purpose)] = groups.get((it.owner_id, it.purpose), 0) + 1
    lineage = {(x.owner_id, x.purpose): x.next_ordinal for x in seed.lineage}
    refuse(lineage == groups, 'S4', p + '.lineage', 'ordinals do not match the seeded (owner, purpose) groups')
    for iid, si in intents.items():
        it = si.intent
        if it.owner_id is not None and decisions[it.decision_id].key is None:
            refuse(any(iid == derive_child_intent_id(account_id, it.owner_id, it.purpose, k)
                       for k in range(lineage[(it.owner_id, it.purpose)])), 'S4', f'{p}.intents[{iid}]',
                   'lineage id outside its (owner, purpose) ordinals')
    protect_owners = {o for (o, u) in groups if u is Purpose.PROTECT}
    refuse({x.owner_id for x in seed.last_protect} == protect_owners, 'S4', p + '.last_protect',
           'one entry per owner with a protect intent')
    for x in seed.last_protect:
        last = derive_child_intent_id(account_id, x.owner_id, Purpose.PROTECT, groups[(x.owner_id, Purpose.PROTECT)] - 1)
        si = intents.get(x.intent_id)
        refuse(si is not None and si.intent.purpose is Purpose.PROTECT and si.intent.owner_id == x.owner_id
               and (x.intent_id == last or last not in intents), 'S4', f'{p}.last_protect[{x.owner_id}]',
               "is not the owner's last protect attempt")
    # S5 impossible states
    results = {d.fact_id for d in seed.facts.results}
    for iid, si in intents.items():
        _check_intent_state(si, results, f'{p}.intents[{iid}]')
    lots = {derive_lot_id(account_id, iid) for iid, si in intents.items()
            if si.intent.purpose is Purpose.ENTRY and si.final is not None and si.final.evidence in EXECUTING
            and not si.superseded}
    refuse(set(seed.lots) == lots, 'S4', p + '.lots', 'not exactly the lots opened by seeded entry fills')


def _check_intent_state(si, results, q):
    it, st, sent, final = si.intent, si.state, si.sent_at_ms, si.final
    refuse(booking_step_ok(it, st), 'S5', q + '.state', f'a post-hoc booking never reaches {st}')
    refuse(st not in _PRE_SENT or sent is None, 'S5', q + '.sent_at_ms', f'set on a never-sent {st} intent')
    refuse(st not in _SENT_ONLY or sent is not None, 'S5', q + '.sent_at_ms', f'missing on the sent state {st}')
    refuse(st is not IntentState.PLANNED, 'S5', q + '.state', 'a recorded intent is never PLANNED')
    if final is not None:
        refuse(final.phase is ResultPhase.FINAL, 'S5', q + '.final', 'not a FINAL result')
        try:
            check_result_for_intent(it, final, sent)
        except DomainError as ex:
            raise GrammarSeedRefused('S5', q + '.final', f'does not belong to the intent ({ex})') from None
        if sent is None:
            refuse(final.evidence in (Evidence.NOT_SENT, Evidence.EXCHANGE_EXTERNAL), 'S5', q + '.final',
                   'a never-sent intent only ends not_sent / exchange_external')
        else:
            refuse(final.evidence is not Evidence.NOT_SENT, 'S5', q + '.final', 'the intent was sent')
        refuse(final.result_id in results, 'S5', q + '.final', 'the final result is not in the seeded facts')
    if st in TERMINAL:
        refuse(final is not None, 'S5', q + '.state', 'terminal without a final result')
        refuse(si.superseded or terminal_for(final) is st, 'S5', q + '.state', 'is not terminal_for(final)')
    else:
        refuse(not si.superseded, 'S5', q + '.superseded', 'a live intent was never superseded')
    if si.superseded:
        refuse(st in TERMINAL and final.evidence is Evidence.EXCHANGE_FINAL and si.late_result_id == final.result_id,
               'S5', q + '.superseded', 'without its superseding final')
    else:
        refuse(si.late_result_id is None, 'S5', q + '.late_result_id', 'set without a superseding final')
    refuse(not si.late_applied or si.late_result_id is not None, 'S5', q + '.late_applied',
           'applied without a late result')


def check_seed_for_snapshot(seed, snapshot):
    """S3: the seed and the snapshot it is restored with cover the same prefix and carry the same facts."""
    refuse(isinstance(snapshot, Snapshot), 'S3', 'snapshot', 'not a Snapshot')
    refuse(isinstance(seed, GrammarSeed), 'S3', 'grammar_seed', 'the snapshot carries no grammar seed')
    refuse(seed.account_id == snapshot.account_id, 'S2', 'grammar_seed.account_id', "not the snapshot's account")
    refuse(seed.last_sequence == snapshot.last_sequence, 'S3', 'grammar_seed.last_sequence',
           f'{seed.last_sequence} is a {"stale" if seed.last_sequence < snapshot.last_sequence else "future"} seed for '
           f'snapshot sequence {snapshot.last_sequence}')
    refuse(seed.facts == snapshot.facts, 'S3', 'grammar_seed.facts', "differ from the snapshot's facts")


# ---------------------------------------------------------------------------------------------------- bytes
def _doc_bytes(rtype, obj):
    doc = {'format': SEED_FORMAT, 'record_type': rtype, 'seed_version': GRAMMAR_SEED_VERSION, 'body': to_json(obj)}
    return json.dumps(doc, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('utf-8')


def _strict_doc(data, rtype, cls):
    refuse(isinstance(data, (bytes, bytearray)) and len(data) > 0, 'S3', rtype, f'no {rtype} document')

    def pairs(kv):
        out = {}
        for k, v in kv:
            refuse(k not in out, 'S3', rtype, f'duplicate key {k!r}')
            out[k] = v
        return out

    def no_float(s):
        raise GrammarSeedRefused('S3', rtype, f'non-integer number {s[:20]!r}')
    try:
        doc = json.loads(bytes(data).decode('utf-8'), object_pairs_hook=pairs, parse_float=no_float,
                         parse_constant=no_float)
    except (ValueError, RecursionError) as ex:
        if isinstance(ex, GrammarSeedRefused):
            raise
        raise GrammarSeedRefused('S3', rtype, f'not one complete JSON document ({type(ex).__name__})') from None
    refuse(type(doc) is dict and doc.get('format') == SEED_FORMAT, 'S3', rtype,
           f'not a {SEED_FORMAT} document (a snapshot without its grammar seed?)')
    refuse(doc.get('seed_version') == GRAMMAR_SEED_VERSION and type(doc.get('seed_version')) is int, 'S1',
           rtype + '.seed_version', f'{doc.get("seed_version")!r} is not version {GRAMMAR_SEED_VERSION}')
    refuse(set(doc) == _DOC_KEYS and doc['record_type'] == rtype, 'S3', rtype, f'not a {rtype} envelope')
    obj = from_json(cls, doc['body'], rtype)
    refuse(_doc_bytes(rtype, obj) == bytes(data), 'S3', rtype, 'not the canonical bytes')
    return obj


def seed_bytes(seed):
    """Canonical bytes of a seed (the identity the envelope digest covers)."""
    return _doc_bytes('grammar_seed', seed)


def seed_sha256(seed):
    return hashlib.sha256(seed_bytes(seed)).hexdigest()


def seed_from_bytes(data):
    """Strict decode of seed_bytes(). Field / invariant damage raises the codec's InvalidRecord."""
    seed = _strict_doc(data, 'grammar_seed', GrammarSeed)
    refuse(seed.seed_version == GRAMMAR_SEED_VERSION, 'S1', 'grammar_seed.seed_version',
           f'{seed.seed_version} is not the supported version {GRAMMAR_SEED_VERSION} (no migration)')
    return seed


def seal_snapshot(snapshot, seed):
    """The envelope bytes NC-02 commits as ONE record. Refuses a seed that does not belong to the snapshot."""
    check_seed_for_snapshot(seed, snapshot)
    check_grammar_seed(seed, seed.account_id, seed.aggregate_id)
    env = SeededSnapshot(snapshot=snapshot, seed=seed, seed_version=GRAMMAR_SEED_VERSION, seed_sha256=seed_sha256(seed))
    data = _doc_bytes('seeded_snapshot', env)
    refuse(open_snapshot(data, committed_sha256=envelope_sha256(data)) == env, 'S3', 'seeded_snapshot',
           'does not re-decode to itself')
    return data


def envelope_sha256(data):
    """The digest the store keeps with its commit (HEAD / anchor): binds the restart to exactly these bytes."""
    return hashlib.sha256(bytes(data)).hexdigest()


def open_snapshot(data, *, committed_sha256):
    """Decode a committed envelope. Refuses (S3) missing / torn / uncommitted / swapped bytes, a seed digest mismatch and
    a stale / future seed; (S1) an unsupported seed version. Returns the SeededSnapshot."""
    refuse(isinstance(committed_sha256, str) and SHA_RE.fullmatch(committed_sha256) is not None, 'S3',
           'seeded_snapshot', 'no committed envelope digest')
    refuse(isinstance(data, (bytes, bytearray)) and len(data) > 0, 'S3', 'seeded_snapshot',
           'missing: no committed snapshot envelope (and so no grammar seed)')
    refuse(envelope_sha256(data) == committed_sha256, 'S3', 'seeded_snapshot',
           'these bytes are not the committed envelope (swapped, torn or never durably committed)')
    env = _strict_doc(data, 'seeded_snapshot', SeededSnapshot)
    refuse(env.seed_version == GRAMMAR_SEED_VERSION == env.seed.seed_version, 'S1', 'seeded_snapshot.seed_version',
           f'{env.seed_version} / {env.seed.seed_version} is not version {GRAMMAR_SEED_VERSION}')
    refuse(env.seed_sha256 == seed_sha256(env.seed), 'S3', 'seeded_snapshot.seed_sha256',
           'the seed is not the one the envelope committed (digest mismatch)')
    check_seed_for_snapshot(env.seed, env.snapshot)
    return env


def restore_gate(account_id, aggregate_id, data, tail, *, committed_sha256):
    """The one restart path after compaction: committed envelope -> seeded gate -> the journal tail."""
    from .journal import JournalGate
    env = open_snapshot(data, committed_sha256=committed_sha256)
    return JournalGate.rebuild(account_id, aggregate_id, tail, grammar_seed=env.seed)
