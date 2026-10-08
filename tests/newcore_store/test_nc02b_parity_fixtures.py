"""Self-checks of the every-cut parity fixtures (nc02b_parity_fixtures.py) and the parity harness on today's gate.

Today's JournalGate seeds facts + last sequence only (no grammar seed), so parity holds at k = 0 (full log) and
k = n (empty tail) and fails mid-stream - that is the recorded gap; compaction stays disabled until the NC-01 lane's
typed grammar seed makes `run_parity` come back empty at EVERY k.
"""
import pytest

from nc02a_events import ACCOUNT_ID, AGGREGATE_ID
from nc02b_parity_fixtures import full_gate, parity_cases, run_parity, scenarios
from newcore.domain import (DecisionRecorded, ExternalTrade, IncidentRecorded, IntentRecorded, ResultObserved,
                            ResultPhase, check_event_chain)
from newcore.domain.facts import fold_facts
from newcore.ports.journal import JournalGate

CASES = parity_cases()


@pytest.mark.parametrize('name', sorted(scenarios()))
def test_every_scenario_is_a_valid_journal(name):
    events = scenarios()[name]
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))
    check_event_chain(events)                                             # NC-01 chain
    assert full_gate(events).grammar.last_sequence == len(events)        # step-0 gate
    fold_facts(events)


def test_the_scenarios_cover_what_a_grammar_seed_must_carry():
    all_events = [e for evs in scenarios().values() for e in evs]
    live = [k for evs in scenarios().values() if check_event_chain(evs) for k in check_event_chain(evs)]
    assert live, 'a scenario must END with live intents'
    assert any(isinstance(e, IntentRecorded) and e.intent.owner_id for e in all_events)              # owner lots
    phases = {e.result.phase for e in all_events if isinstance(e, ResultObserved)}
    assert {ResultPhase.KNOWN, ResultPhase.FINAL} <= phases
    assert any(isinstance(e, IncidentRecorded) and e.incident.intent_refs for e in all_events)       # incidents
    assert any(isinstance(e, ResultObserved) and e.result.external_trades for e in all_events)       # external
    assert fold_facts(scenarios()['external_close']).trades                                          # consumed trades
    assert any(isinstance(e, DecisionRecorded) for e in all_events)


def test_every_cut_of_every_scenario_is_a_case_with_full_log_answers():
    by = {}
    for c in CASES:
        by.setdefault(c.scenario, []).append(c.k)
        assert c.seed.last_sequence == c.k and c.tail == c.events[c.k:]
        assert c.seed.facts == fold_facts(list(c.events[:c.k]))
        assert {p[2] for p in c.probes} <= {'apply', 'already_applied', 'conflict'}
        assert any(p[0] == 'new_incident_next' and p[2] == 'apply' for p in c.probes)
        if any(p[0].startswith('prefix_') and p[0].endswith('other_content') for p in c.probes):
            assert [p[2] for p in c.probes if p[0].endswith('other_content')] == ['conflict']
    for name, evs in scenarios().items():
        assert by[name] == list(range(len(evs) + 1))


def _facts_only_rebuild(case):
    """Today's gate: facts + last sequence, no grammar seed."""
    return JournalGate.rebuild(ACCOUNT_ID, AGGREGATE_ID, list(case.tail), facts=case.seed.facts,
                               after_sequence=case.k)


def test_parity_holds_on_the_full_log_today():
    assert run_parity(_facts_only_rebuild, [c for c in CASES if c.k == 0]) == []


REAPPEND = {'prefix_event_again', 'last_event_again'}


def test_the_two_recorded_gaps_today():
    """What a grammar seed must close, measured on today's facts-only seed (reported to Codex / NC-01):
    1. mid-stream the gate refuses the tail (decisions / intents / lots of the prefix are unknown to its grammar);
    2. at every cut, an IDENTICAL re-append of a compacted event is a conflict instead of already-applied (the
       seeded gate no longer holds the events, so idempotent re-append needs event digests by sequence in the seed,
       or a ruling that re-append below the cut is refused).
    When the NC-01 seed lands, run_parity(new_rebuild, CASES) must be [] and this test is replaced by that."""
    ends = run_parity(_facts_only_rebuild, [c for c in CASES if c.k == len(c.events)])
    assert ends and {b[2] for b in ends} <= REAPPEND and all((b[3], b[4]) == ('already_applied', 'conflict')
                                                             for b in ends)
    mids = run_parity(_facts_only_rebuild, [c for c in CASES if 0 < c.k < len(c.events)])
    assert {b[2] for b in mids} <= REAPPEND | {'rebuild'}
    assert sum(b[2] == 'rebuild' for b in mids) > len(mids) // 2
