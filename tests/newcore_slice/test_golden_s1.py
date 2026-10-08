"""The S1 / S3 / S4-expressible golden cases (origin/golden-short-mirrors @ 74a4505, copied verbatim into golden_cases/) through
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


S4_CASES = ('G-DAY-CAIRO-W-01', 'G-DAY-CAIRO-S-01', 'G-DAY-CAIRO-W-SHORT-01', 'G-DAY-CAIRO-S-SHORT-01')


@pytest.mark.parametrize('case_id', S4_CASES)
def test_s4_cairo_day_golden_case_passes_on_the_book(case_id):
    """Two symbols, max_pos 2, the 8% Cairo-day halt keyed by the candle CLOSE (winter UTC+2 / summer UTC+3, long and
    short): the halt of day D does not block a signal whose candle closes after Cairo midnight (AUD-07 C13d)."""
    case = load(case_id)
    trace, runner = run_case(case)
    assert diff(case, trace) == []
    assert [e.kind for e in runner.events if e.kind != 'roll'] == ['halt']
    assert runner.summary().counters['unprotected_cycles'] == 0


def test_s3_exchange_outage_golden_case_passes():
    """G-OUTAGE-STOP-L-01: no read / send during the outage (reconciliation unreadable -> HOLD, no decision); the stop
    rests on the exchange and fills there; after recovery the fill is booked from the order record at the EXCHANGE
    time (i_out 282, STOP_HIT), never a bot close."""
    case = load('G-OUTAGE-STOP-L-01')
    trace, runner = run_case(case)
    assert diff(case, trace) == []
    assert not [o for o in runner.venue.inner.orders_submitted() if o.order_type == 'MARKET' and o.reduce]


def test_a_restart_fault_reproduces_the_fault_free_trace():
    """The adapter's restart fault (no cycle while down, then a NEW Runner over the same journal) on G-STOP-L-01's
    market while the trade is open: identical trace (G-RESTART-TIME-L-01 itself also needs a time exit: S2)."""
    case = load('G-STOP-L-01')
    t0 = int(case['clock']['start'])
    faulted = dict(case, faults=[{'kind': 'restart', 'at_ms': t0 + 281 * 14_400_000 + 60_000,
                                  'down_ms': 14_400_000}])
    assert diff(case, run_case(faulted)[0]) == []


def test_unsupported_inputs_are_refused_with_a_named_owner():
    case = load('G-STOP-L-01')
    for patch, owner in (({'faults': [{'kind': 'lost_response', 'order': 'stop', 'nth': 1, 'truth': 'filled'}]},
                          'S3 runner'),
                         ({'instruments': {}}, 'S2 management'),
                         ({'slot': dict(case['slot'], target={'r': '2'})}, 'S2 management'),
                         ({'slot': dict(case['slot'], dca={'n': 1})}, 'range slice')):
        with pytest.raises(NotExpressible, match=f'owner: {owner}'):
            check_expressible(dict(case, **patch))
