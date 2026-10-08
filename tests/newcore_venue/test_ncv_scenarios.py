"""S5 cassette harness: every scripted scenario drives TestnetVenue, records a clean cassette and replays identically."""
import json

import pytest

from newcore.ports import keys as K
from newcore.venue import scenarios as S
from newcore.venue.wire import WireTimeout


@pytest.mark.parametrize('scenario', S.SCENARIOS, ids=lambda s: s.name)
def test_scenario_runs_records_and_replays(scenario):
    report = S.run_scenario(scenario)
    assert report.replayed and len(report.steps) == len(scenario.steps)
    assert all(r.requests == len(s.answers) for r, s in zip(report.steps, scenario.steps))
    text = report.cassette
    for v in (S.SCENARIO_KEY, S.SCENARIO_SECRET):
        assert v not in text
    assert json.loads(text)['format'] == 'zb-newcore-cassette/1'


def test_the_required_scenarios_exist():
    assert {s.name for s in S.SCENARIOS} == {'lifecycle_classic', 'algo_route', 'duplicate_client_id',
                                             'not_found_then_final', 'hedge_mode_check'}


def test_client_ids_are_the_runners_one_id_per_intent():
    assert S.STOP_REF.client_id == K.client_id_for(S.STOP_INTENT, 'classic')
    assert S.STOP_ALGO_REF.client_id == K.client_id_for(S.STOP_ALGO_INTENT, 'algo')
    assert S.STOP_INTENT != S.STOP_ALGO_INTENT                     # the algo fallback is a NEW protect intent
    ids = {S.ENTRY_REF.client_id, S.STOP_REF.client_id, S.STOP_ALGO_REF.client_id, S.CLOSE_REF.client_id}
    assert len(ids) == 4 and all(K.is_newcore_client_id(c) for c in ids)


def test_a_hidden_retry_is_caught():
    # A step scripted with one answer whose call sends two requests must fail the scenario.
    step = S.Step('two sends', lambda v: (v.query(S.ENTRY_REF), v.query(S.ENTRY_REF))[1],
                  (S.order(S.ENTRY_REF, status='NEW', side='BUY'), S.order(S.ENTRY_REF, status='NEW', side='BUY')))
    bad = S.Scenario('bad', 'x', [S.Step(step.label, step.call, step.answers[:1])])
    with pytest.raises(S.UnscriptedRequest):                     # propagates through the transport, never UNKNOWN
        S.run_scenario(bad)


def test_a_wrong_expectation_fails_the_scenario():
    bad = S.Scenario('bad', 'x', [S.Step('lost answer', lambda v: v.query(S.ENTRY_REF), (WireTimeout(),),
                                         dict(kind='final'))])
    with pytest.raises(S.ScenarioFailed):
        S.run_scenario(bad)


def test_unused_scripted_answers_fail_the_scenario():
    bad = S.Scenario('bad', 'x', [S.Step('query', lambda v: v.query(S.ENTRY_REF),
                                         (S.error(-2013, 'Order does not exist.'), S.dual(True)))])
    with pytest.raises(S.ScenarioFailed):
        S.run_scenario(bad)


def test_replay_of_a_tampered_cassette_is_caught():
    report = S.run_scenario(S.DUPLICATE)
    doc = json.loads(report.cassette)
    # avg price is not in the step's expectations: only the replay-equals-recording comparison can catch this.
    doc['interactions'][1]['response']['body_text'] = doc['interactions'][1]['response']['body_text'].replace(
        '"210.5"', '"999.5"')
    with pytest.raises(S.ScenarioFailed):
        S.replay_cassette(S.DUPLICATE, json.dumps(doc), [r.outcome for r in report.steps])


def test_replay_that_leaves_interactions_unplayed_is_caught():
    report = S.run_scenario(S.DUPLICATE)
    doc = json.loads(report.cassette)
    doc['interactions'].append(doc['interactions'][-1])
    with pytest.raises(Exception) as ei:
        S.replay_cassette(S.DUPLICATE, json.dumps(doc))
    assert 'not replayed' in str(ei.value)


def test_requests_are_attributed_to_their_own_step():
    # Step 1 is scripted with 2 answers but sends 1; step 2 is scripted with 0 but sends 1: totals match, steps do not.
    steps = [S.Step('one query', lambda v: v.query(S.ENTRY_REF),
                    (S.error(-2013, 'Order does not exist.'), S.error(-2013, 'Order does not exist.'))),
             S.Step('another query', lambda v: v.query(S.ENTRY_REF), ())]
    with pytest.raises(S.ScenarioFailed) as ei:
        S.run_scenario(S.Scenario('bad', 'x', steps))
    assert 'one query' in str(ei.value)


def test_run_all():
    assert [r.name for r in S.run_all()] == [s.name for s in S.SCENARIOS]
