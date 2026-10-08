"""NC-01 r3a (#44) in the NC-02b store: durable facts (result / incident ids, consumed venue trades) stay exactly-once
across compaction.

- every committed generation carries Snapshot.facts = the account's FactIndex through its last_sequence;
- boot cross-checks the committed generation's facts against the journal's own fold (a mismatch is damage: HOLD);
- compact -> restart: Folder.replay(tail, facts=snap.facts, after_sequence=snap.last_sequence, known_intents=...)
  seeds BOTH JournalGate.rebuild and check_event_chain, so a fact journaled in the compacted prefix is still refused
  under other content - and the test proves the seed is what makes that true (without it the conflict slips in).
"""
import dataclasses

import pytest

from nc02a_events import ACCOUNT_ID, AGGREGATE_ID, SCENARIO, ev
from nc02b_helpers import T
from newcore.domain import IncidentRecorded, ReasonCode, ResultObserved, check_event_chain
from newcore.domain.errors import DomainError
from newcore.domain.incident import Incident
from newcore.domain.facts import FactIndex, fold_facts
from newcore.ports.journal import Admission, JournalConflict
from newcore.store import Outcome
from newcore.store.fold import Folder
from test_nc02b_runner_api import close, lot_pf, managed, op

RESULTS = [i for i, e in enumerate(SCENARIO) if isinstance(e, ResultObserved)]


def test_the_scenario_journals_result_facts():
    assert RESULTS and fold_facts(SCENARIO).results


# ---------------------------------------------------------------------------------------------------- generations
def test_every_generation_carries_the_facts_through_its_last_sequence():
    fs, ex, b = managed()
    st = b.store
    assert st.current.snapshot.facts == FactIndex(results=(), incidents=(), trades=())
    k = RESULTS[0] + 1
    for e in SCENARIO[:k]:
        b.journal.append(e)
    sf = st.checkpoint(lot_pf(through=k)[0], T + 1)
    assert sf.snapshot.last_sequence == k and sf.snapshot.facts == fold_facts(SCENARIO[:k])
    assert sf.snapshot.facts.results                                        # the prefix result is a durable fact
    close(b)
    r = op(fs, ex)
    assert r.outcome is Outcome.MANAGE and r.store.current.snapshot.facts == fold_facts(SCENARIO[:k])
    close(r)


def test_generation_facts_that_disagree_with_the_journal_are_damage_at_boot():
    fs, ex, b = managed()
    st = b.store
    k = RESULTS[0] + 1
    for e in SCENARIO[:k]:
        b.journal.append(e)
    st.facts_through = lambda lsn: FactIndex(results=(), incidents=(), trades=())      # a generation that lies
    st.checkpoint(lot_pf(through=k)[0], T + 1)
    close(b)
    r = op(fs, ex)
    assert r.outcome is Outcome.HOLD
    assert any(i.cause == 'damage' and 'facts' in i.ref for i in r.items), r.items
    close(r)


# ---------------------------------------------------------------------------------------------------- compaction
INC = 'inc_' + '5c' * 16


def _incident_event(seq, at, detail):
    inc = Incident(incident_id=INC, account_id=ACCOUNT_ID, kind=ReasonCode.RECONCILE_MANUAL_CLOSE, at_ms=at, symbol=None,
                   side=None, intent_refs=(), lot_refs=(), position_refs=(), evidence=(), detail=detail)
    return ev(IncidentRecorded, seq, at, reason=ReasonCode.RECONCILE_MANUAL_CLOSE, incident=inc)


LOG = list(SCENARIO) + [_incident_event(len(SCENARIO) + 1, SCENARIO[-1].at_ms + 1, 'first')]
K = len(LOG)                                       # compaction point: everything so far is in the snapshot


def test_compact_then_restart_keeps_every_fact_exactly_once():
    full = Folder.replay(ACCOUNT_ID, AGGREGATE_ID, LOG)
    facts_k = fold_facts(LOG)                                             # what the snapshot at K carries
    assert any(d.fact_id == INC for d in facts_k.incidents)
    seeded = Folder.replay(ACCOUNT_ID, AGGREGATE_ID, [], facts=facts_k, after_sequence=K,
                           known_intents=check_event_chain(LOG))
    assert seeded.last_sequence == full.last_sequence == K
    bad = _incident_event(K + 1, LOG[-1].at_ms + 1, 'other content under the same incident id')
    with pytest.raises(JournalConflict):
        full.stage(bad)
    with pytest.raises(JournalConflict):                                  # the compacted prefix's fact still binds
        seeded.stage(bad)
    with pytest.raises(DomainError):                                      # ... in the NC-01 chain layer too
        check_event_chain([bad], after_sequence=K, facts=facts_k)
    unseeded = Folder.replay(ACCOUNT_ID, AGGREGATE_ID, [], after_sequence=K)
    assert unseeded.stage(bad) is not Admission.ALREADY_APPLIED          # without the seed the conflict slips in:
    check_event_chain([bad], after_sequence=K)                            # the facts ARE what the seed carries
    same = _incident_event(K + 1, LOG[-1].at_ms + 1, 'first')
    same = dataclasses.replace(same, incident=LOG[-1].incident)           # the identical fact again: still fine
    seeded.stage(same)


def test_the_step0_gate_cannot_restart_mid_stream_yet():
    """Recorded gap (reported to Codex, ports / NC-01 lane): JournalGate.rebuild(tail, facts=, after_sequence=k) seeds
    the FACTS but not the grammar's decisions / intents / lots, so a tail that touches an intent of the compacted
    prefix is refused. The store never compacts until that exists (it keeps the whole journal and replays it)."""
    k = RESULTS[0] + 1
    with pytest.raises(JournalConflict):
        Folder.replay(ACCOUNT_ID, AGGREGATE_ID, SCENARIO[k:], facts=fold_facts(SCENARIO[:k]), after_sequence=k,
                      known_intents=check_event_chain(SCENARIO[:k]))


def test_a_seeded_folder_refuses_reads_into_the_compacted_prefix():
    seeded = Folder.replay(ACCOUNT_ID, AGGREGATE_ID, [], facts=fold_facts(LOG), after_sequence=K,
                           known_intents=check_event_chain(LOG))
    assert seeded.events_after(K) == ()
    with pytest.raises(ValueError):
        seeded.events_after(K - 1)
