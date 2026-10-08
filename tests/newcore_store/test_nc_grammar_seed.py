"""GRAMMAR_SEED.md (Codex ruling 6070599699): every-cut parity of the grammar seed over the NC-02b fixtures, the snapshot
envelope binding (snapshot A + seed B, missing / uncommitted / digest-mismatched / stale / future seed) and one refusal
test per code S1..S6. Compaction stays DISABLED in the store: nothing here enables it.

The fixtures are nc02b_parity_fixtures.py taken verbatim from nc-02b-store @ 30b1055 (git checkout 30b1055 -- <file>).
"""
import dataclasses
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
for d in (HERE, os.path.join(os.path.dirname(HERE), 'newcore')):
    if d not in sys.path:
        sys.path.insert(0, d)

from nc01_factories import unknown_portfolio                                    # noqa: E402
from nc02a_memfs import MemFs                                                   # noqa: E402
from nc02b_parity_fixtures import (ACCOUNT_ID, AGGREGATE_ID, T0, parity_cases, run_parity,  # noqa: E402
                                   scenarios)
from newcore.domain import IntentState, Snapshot, check_event_chain           # noqa: E402
from newcore.domain.errors import InvalidRecord                                # noqa: E402
from newcore.domain.ledger import EventDigest                                  # noqa: E402
from newcore.ports.journal import JournalGate                                  # noqa: E402
from newcore.ports.seed import (GrammarSeedRefused, envelope_sha256, known_intents, open_snapshot,  # noqa: E402
                                restore_gate, seal_snapshot, seed_bytes, seed_from_bytes)

CASES = parity_cases()
PF = unknown_portfolio(ACCOUNT_ID)
OTHER_ACCT = 'acct_' + 'c3' * 16


def full(events):
    return JournalGate.rebuild(ACCOUNT_ID, AGGREGATE_ID, list(events))


def snapshot_of(seed, **kw):
    return Snapshot(account_id=ACCOUNT_ID, generation=PF.generation, last_sequence=kw.get('seq', seed.last_sequence),
                    written_at_ms=T0, writer_build='seed-test', portfolio=PF, facts=kw.get('facts', seed.facts))


def committed(seed):
    data = seal_snapshot(snapshot_of(seed), seed)
    return data, envelope_sha256(data)


def seed_rebuild(case):
    """The restart path NC-02 will run: prefix gate -> seed -> committed envelope bytes -> open -> rebuild(tail)."""
    seed = full(case.events[:case.k]).grammar_seed()
    data, sha = committed(seed)
    return restore_gate(ACCOUNT_ID, AGGREGATE_ID, data, list(case.tail), committed_sha256=sha)


# ---------------------------------------------------------------------------------------------------- parity
def test_every_cut_parity_over_the_nc02b_fixtures():
    assert len(CASES) == 77
    assert run_parity(seed_rebuild, CASES) == []


@pytest.mark.parametrize('case', CASES, ids=lambda c: f'{c.scenario}-k{c.k}')
def test_seeded_gate_state_equals_the_full_log_gate(case):
    seed = full(case.events[:case.k]).grammar_seed()
    assert seed_from_bytes(seed_bytes(seed)) == seed                          # byte round trip
    restored = JournalGate.rebuild(ACCOUNT_ID, AGGREGATE_ID, list(case.tail), grammar_seed=seed)
    assert restored.grammar_seed() == full(case.events).grammar_seed()
    # binding point 2: the chain's known_intents is a projection of the seed, equal to the chain's own fold
    assert known_intents(seed) == case.seed.known_intents
    assert seed.facts == case.seed.facts and seed.last_sequence == case.seed.last_sequence
    assert {d.decision_id for d in seed.decisions} == set(case.seed.decisions)
    assert {si.intent.intent_id for si in seed.intents} == set(case.seed.intents)


def test_the_g3_digests_are_kept_whole_in_v1():
    for name, events in scenarios().items():
        seed = full(events).grammar_seed()
        assert [d.sequence for d in seed.events] == list(range(1, len(events) + 1)), name


def test_the_chain_tripwire_restores_from_the_seed_projection():
    """check_event_chain(tail, known_intents=known_intents(seed), facts=seed.facts) accepts the tail at every cut but the
    two the NC-01 chain has no seed form for (OPEN, domain-side; the gate above has full parity at every cut):
    a cut between a FINAL result and its terminal step (the chain seeds no finals), and an external-close result whose
    RECONCILE decision is in the compacted prefix (the chain seeds no decisions). Pinned so a change is visible."""
    from newcore.domain.errors import DomainError
    gaps = {}
    for case in CASES:
        seed = full(case.events[:case.k]).grammar_seed()
        try:
            check_event_chain(list(case.tail), after_sequence=case.k, known_intents=known_intents(seed),
                              facts=seed.facts)
        except DomainError as ex:
            gaps[(case.scenario, case.k)] = ex.msg
    final_gap = 'a terminal step needs a durable FINAL result first'
    decision_gap = 'the authorising RECONCILE decision is not in the log'
    assert set(gaps.values()) == {final_gap, decision_gap}
    assert [k for k, m in gaps.items() if m == decision_gap] == [('external_close', 11)]
    assert len(gaps) == 11


# ---------------------------------------------------------------------------------------------------- binding
def _mid():
    events = scenarios()['lifecycle']
    k = len(events) // 2
    return events, k, full(events[:k]).grammar_seed()


def _seed_b(seed):
    """Seed B: same last_sequence and facts as A, other content (one G3 digest differs)."""
    ev = list(seed.events)
    ev[0] = EventDigest(event_id=ev[0].event_id, sequence=1, sha256='f' * 64)
    return dataclasses.replace(seed, events=tuple(ev))


def _refused(code, fn, match=None):
    with pytest.raises(GrammarSeedRefused, match=match) as ex:
        fn()
    assert ex.value.code == code
    return ex.value


def test_snapshot_a_with_seed_b_at_the_same_sequence_and_facts_is_refused():
    events, k, a = _mid()
    b = _seed_b(a)
    assert (b.last_sequence, b.facts) == (a.last_sequence, a.facts) and b != a
    data_a, sha_a = committed(a)
    swapped = seal_snapshot(snapshot_of(a), b)               # a well-formed envelope: snapshot A + seed B
    _refused('S3', lambda: restore_gate(ACCOUNT_ID, AGGREGATE_ID, swapped, events[k:], committed_sha256=sha_a),
             'not the committed envelope')
    # and the committed A restores (the control)
    assert restore_gate(ACCOUNT_ID, AGGREGATE_ID, data_a, events[k:], committed_sha256=sha_a).grammar_seed() == \
        full(events).grammar_seed()


def test_seed_digest_mismatch_inside_the_envelope_is_refused():
    events, k, a = _mid()
    data_a, _ = committed(a)
    forged = data_a.replace(a.events[0].sha256.encode(), b'f' * 64, 1)     # seed bytes changed, digest field kept
    assert forged != data_a
    _refused('S3', lambda: open_snapshot(forged, committed_sha256=envelope_sha256(forged)), 'digest mismatch')


def test_a_missing_seed_is_refused():
    events, k, a = _mid()
    _refused('S3', lambda: open_snapshot(b'', committed_sha256='0' * 64), 'missing')
    _refused('S3', lambda: open_snapshot(None, committed_sha256='0' * 64), 'missing')
    # a bare NC-01 snapshot document (no seed in it) committed as the snapshot
    from newcore.domain import canonical_bytes
    bare = canonical_bytes(snapshot_of(a))
    _refused('S3', lambda: open_snapshot(bare, committed_sha256=envelope_sha256(bare)), 'without its grammar seed')
    # a loose seed document is never a restore input either
    loose = seed_bytes(a)
    _refused('S3', lambda: open_snapshot(loose, committed_sha256=envelope_sha256(loose)), 'not a seeded_snapshot')
    _refused('S3', lambda: seal_snapshot(snapshot_of(a), None), 'carries no grammar seed')


@pytest.mark.parametrize('pending', ['drop', 'all', 'half', 'one', 'minus1', 'zero'])
@pytest.mark.parametrize('model', ['ntfs', 'posix'])
def test_a_seed_written_but_never_durably_committed_is_refused(model, pending):
    """Envelope A is committed (fsync + dir flush + its digest in the commit). Envelope B (a later cut) is written but
    the process dies before its fsync / commit: whatever bytes survive, the restart opens A or refuses - never B."""
    events = scenarios()['lifecycle']
    a = full(events[:6]).grammar_seed()
    b = full(events[:9]).grammar_seed()
    data_a, sha_a = committed(a)
    data_b, _ = committed(b)
    fs = MemFs('/s')
    h = fs.open_new('/s/g1.env')
    fs.write(h, data_a)
    fs.fsync(h)
    fs.close(h)
    fs.fsync_dir('/s')
    head = sha_a                                                   # what the store committed
    h = fs.open_new('/s/g2.env')
    fs.write(h, data_b)                                            # no fsync, no commit of its digest
    after = fs.crash(model, pending)
    assert open_snapshot(after.read_bytes('/s/g1.env'), committed_sha256=head).seed == a
    if after.kind('/s/g2.env') == 'file':
        present = after.read_bytes('/s/g2.env')
        _refused('S3', lambda: open_snapshot(present, committed_sha256=head))


def test_stale_and_future_seeds_are_refused():
    events = scenarios()['lifecycle']
    a = full(events[:6]).grammar_seed()
    stale = full(events[:5]).grammar_seed()
    future = full(events[:7]).grammar_seed()
    snap = snapshot_of(a)
    _refused('S3', lambda: seal_snapshot(snap, stale), 'stale')
    _refused('S3', lambda: seal_snapshot(snap, future), 'future')
    # even when the facts happen to match: a seed of another sequence with the snapshot's facts
    _refused('S3', lambda: seal_snapshot(dataclasses.replace(snap, facts=stale.facts), stale), 'stale')


def test_facts_mismatch_against_the_snapshot_is_refused():
    events = scenarios()['incidents']
    a = full(events).grammar_seed()
    prev = full(events[:-1]).grammar_seed()
    assert prev.facts != a.facts
    _refused('S3', lambda: seal_snapshot(snapshot_of(a, facts=prev.facts), a), "snapshot's facts")


# ---------------------------------------------------------------------------------------------------- S1..S6
def _rebuild(seed, tail=(), **kw):
    return JournalGate.rebuild(ACCOUNT_ID, AGGREGATE_ID, list(tail), grammar_seed=seed, **kw)


def test_s1_unsupported_seed_version():
    _, _, a = _mid()
    _refused('S1', lambda: _rebuild(dataclasses.replace(a, seed_version=2)))
    body_only = seed_bytes(a).replace(b'"seed_version":1', b'"seed_version":2', 1)      # body first (sorted keys)
    _refused('S1', lambda: seed_from_bytes(body_only))
    both = seed_bytes(a).replace(b'"seed_version":1', b'"seed_version":2')
    _refused('S1', lambda: seed_from_bytes(both))


def test_s2_another_account_or_aggregate():
    _, _, a = _mid()
    _refused('S2', lambda: JournalGate.rebuild(ACCOUNT_ID, 'pf_' + 'd4' * 16, [], grammar_seed=a))
    _refused('S2', lambda: _rebuild(dataclasses.replace(a, account_id=OTHER_ACCT)))


def test_s3_prefix_coverage_gap():
    _, _, a = _mid()
    _refused('S3', lambda: _rebuild(dataclasses.replace(a, events=a.events[:-1])))
    _refused('S3', lambda: _rebuild(dataclasses.replace(a, last_sequence=a.last_sequence + 1)))


def test_s4_owner_completeness():
    events = scenarios()['lifecycle']
    a = full(events).grammar_seed()
    assert a.lots
    _refused('S4', lambda: _rebuild(dataclasses.replace(a, lots=())), 'owner is no seeded lot')
    _refused('S4', lambda: _rebuild(dataclasses.replace(a, decisions=a.decisions[1:])))
    _refused('S4', lambda: _rebuild(dataclasses.replace(a, lineage=())), 'ordinals')


def test_s5_impossible_state():
    events = scenarios()['lifecycle']
    a = full(events).grammar_seed()
    i = next(n for n, si in enumerate(a.intents) if si.state is IntentState.FILLED)
    bad = list(a.intents)
    bad[i] = dataclasses.replace(bad[i], state=IntentState.CANCELLED)          # not terminal_for(final)
    _refused('S5', lambda: _rebuild(dataclasses.replace(a, intents=tuple(bad))), 'terminal_for')
    bad[i] = dataclasses.replace(a.intents[i], sent_at_ms=None)                 # final executed, never sent
    _refused('S5', lambda: _rebuild(dataclasses.replace(a, intents=tuple(bad))))
    bad[i] = dataclasses.replace(a.intents[i], late_applied=True)               # applied without a late result
    _refused('S5', lambda: _rebuild(dataclasses.replace(a, intents=tuple(bad))), 'late_applied')


def test_s6_seed_with_facts_or_after_sequence():
    _, _, a = _mid()
    _refused('S6', lambda: _rebuild(a, facts=a.facts))
    _refused('S6', lambda: _rebuild(a, after_sequence=a.last_sequence))
    _refused('S6', lambda: _rebuild(a, after_sequence=0))


def test_a_codec_damaged_seed_raises_the_codec_error():
    _, _, a = _mid()
    raw = seed_bytes(a).replace(b'"last_sequence":', b'"last_sequence_x":', 1)
    with pytest.raises(InvalidRecord):
        seed_from_bytes(raw)


def test_a_facts_only_gate_exports_no_seed():
    events, k, a = _mid()
    legacy = JournalGate.rebuild(ACCOUNT_ID, AGGREGATE_ID, [], facts=a.facts, after_sequence=k)
    _refused('S3', legacy.grammar_seed, 'facts-only')
