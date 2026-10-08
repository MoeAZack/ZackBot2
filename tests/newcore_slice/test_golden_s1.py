"""The S1-expressible golden cases (origin/golden-short-mirrors @ 74a4505, copied verbatim into golden_cases/) through
the NEWCORE adapter: codes, bars and sides exactly, R / pnl to 1e-9 (goldenlib/compare.py EPS)."""
import pytest

from golden_adapter import NotExpressible, check_expressible, diff, load, run_case

S1_CASES = ('G-STOP-L-01', 'G-STOP-S-01', 'G-GAP-STOP-L-01', 'G-GAP-STOP-S-01')


@pytest.mark.parametrize('case_id', S1_CASES)
def test_s1_golden_case_passes_on_newcore(case_id):
    case = load(case_id)
    assert case['applies_to']['newcore_sim']['status'] == 'pending_adapter'   # flipping it is a CORRECTIONS record
    trace, runner = run_case(case)
    assert diff(case, trace) == []
    s = runner.summary()
    assert s.counters['unprotected_cycles'] == 0 and s.ownership == 'known_empty' and s.mode == 'active'


def test_lost_entry_answer_golden_resolves_by_query_into_one_lot():
    """G-AMBIG-ENTRY-L-01 (slice plan: S3) already runs on S1: the lost answer is UNKNOWN -> HOLD -> query -> FINAL,
    exactly one lot, protected in the same cycle; the outcome equals G-STOP-L-01."""
    case = load('G-AMBIG-ENTRY-L-01')
    trace, runner = run_case(case)
    assert diff(case, trace) == []
    venue = runner.venue
    assert [(o.order_type, o.reduce) for o in venue.orders_submitted()] == [('MARKET', False), ('STOP_MARKET', True)]
    s = runner.summary()
    assert s.mode == 'hold' and s.counters['unprotected_cycles'] == 0           # HOLD until an operator resume


def test_unsupported_inputs_are_not_expressible():
    case = load('G-STOP-L-01')
    for patch in ({'faults': [{'kind': 'restart', 'at_ms': 1, 'down_ms': 1}]}, {'instruments': {}},
                  {'slot': dict(case['slot'], target={'r': '2'})}):
        with pytest.raises(NotExpressible):
            check_expressible(dict(case, **patch))
