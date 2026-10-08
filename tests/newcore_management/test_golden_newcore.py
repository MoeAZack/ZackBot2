"""NC-08 golden adapter (tests/golden/goldenlib/adapters/newcore_sim.py): the golden pack through ManagementDriver + step()
on a zb-path/1 path venue. Pins the M4 evidence for the Codex ruling; nothing here changes a case contract:

- PASS (default cap policy 'budget_plus_costs'): G-STOP, G-GAP-STOP, G-GAP-TP, G-TIME (L-01/02, S-01/02), G-DCA-SLIP and
  the one-add G-GAP-DCA-ONEADD, long and short;
- the historical two-add G-GAP-DCA-L/S-01 reproduce their recorded NC07-ONE-ADD-CAP observed lists EXACTLY
  (exit candle 286, R 0.089899 / 0.089692);
- G-TP1-ZERO-L-01 differs only by the expectation's printed precision (exact pnl 19.38593201 vs 19.385932): needs a
  correction (more digits, or a stated newcore tolerance), not a behaviour change;
- cap policy 'budget' (risk_cap = equity x risk exactly): every costed single-entry case is refused at the plan (the entry
  alone with fees and slippage is above 10 USDT) and the one-add case loses its add - the mapping needs a ruling.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'golden'))
from goldenlib import compare, schema  # noqa: E402
from goldenlib import adapters  # noqa: E402
from goldenlib.adapters.base import NotExpressible  # noqa: E402
from goldenlib.adapters.newcore_sim import CAPS_ENTRY, NewcoreSim  # noqa: E402


@pytest.fixture(autouse=True)
def _newcore_caps(monkeypatch):
    """compare() looks the adapter up in adapters.CAPS; newcore_sim is added only for these tests."""
    monkeypatch.setitem(adapters.CAPS, 'newcore_sim', CAPS_ENTRY)

CASES = {c['id']: c for c in schema.load_all()}
PASS = ('G-STOP-L-01', 'G-STOP-S-01', 'G-GAP-STOP-L-01', 'G-GAP-STOP-S-01', 'G-GAP-TP-L-01', 'G-GAP-TP-S-01',
        'G-TIME-L-01', 'G-TIME-L-02', 'G-TIME-S-01', 'G-TIME-S-02', 'G-DCA-SLIP-L-01', 'G-DCA-SLIP-S-01',
        'G-GAP-DCA-ONEADD-L-01', 'G-GAP-DCA-ONEADD-S-01')
ONE_ADD_CAP = ('G-GAP-DCA-L-01', 'G-GAP-DCA-S-01')
NOT_EXPRESSIBLE = {'G-AMBIG-ENTRY-L-01': 'faults', 'G-OUTAGE-STOP-L-01': 'faults', 'G-RESTART-TIME-L-01': 'faults',
                   'G-DAY-CAIRO-S-01': 'one symbol', 'G-DAY-CAIRO-S-SHORT-01': 'one symbol',
                   'G-DAY-CAIRO-W-01': 'one symbol', 'G-DAY-CAIRO-W-SHORT-01': 'one symbol',
                   'G-PYR-SLIP-L-01': 'pyramid', 'G-PYR-SLIP-S-01': 'pyramid', 'G-STOP-CROSSED-L-01': 'trail',
                   'G-STOP-CROSSED-S-01': 'trail', 'G-TRAILENTRY-MAXPOS-01': 'market entries',
                   'G-TRAILENTRY-MAXPOS-SHORT-01': 'market entries'}


def run(cid, policy='budget_plus_costs'):
    c = CASES[cid]
    tr = NewcoreSim(policy).run(c)
    return c, tr, compare.compare(c, tr, 'newcore_sim')


def test_every_case_is_classified():
    assert set(CASES) == set(PASS) | set(ONE_ADD_CAP) | set(NOT_EXPRESSIBLE) | {'G-TP1-ZERO-L-01'}


@pytest.mark.parametrize('cid', PASS)
def test_newcore_reproduces_the_golden_expectation(cid):
    c, tr, mism = run(cid)
    assert mism == [], compare.report(c, 'newcore_sim', mism, tr)


@pytest.mark.parametrize('cid', ONE_ADD_CAP)
def test_two_add_cases_reproduce_the_recorded_one_add_divergence_exactly(cid):
    c, tr, mism = run(cid)
    kd = next(d for d in c['known_divergences'] if d['adapter'] == 'newcore_sim')
    assert kd['divergence_id'] == 'NC07-ONE-ADD-CAP'
    assert compare.same_mismatches(c, kd['observed'], mism), compare.report(c, 'newcore_sim', mism, tr)


def test_tp1_zero_differs_only_by_the_printed_precision():
    c, tr, mism = run('G-TP1-ZERO-L-01')
    assert [m['path'] for m in mism] == ['trades[0].R', 'trades[0].pnl']
    t = tr.trades[0]
    assert (t['i_out'], t['exit']) == (282, 'TP_FULL')
    assert abs(t['pnl'] - 19.38593201) < 1e-12 and abs(t['pnl'] - 19.385932) < 2e-8


@pytest.mark.parametrize('cid', sorted(NOT_EXPRESSIBLE))
def test_out_of_scope_cases_are_refused_explicitly(cid):
    with pytest.raises(NotExpressible, match=NOT_EXPRESSIBLE[cid]):
        NewcoreSim().run(CASES[cid])


@pytest.mark.parametrize('cid', ('G-STOP-L-01', 'G-GAP-TP-S-01', 'G-TIME-L-01', 'G-TP1-ZERO-L-01'))
def test_budget_only_cap_refuses_a_legacy_sized_entry(cid):
    with pytest.raises(NotExpressible, match='entry alone risks'):
        NewcoreSim('budget').run(CASES[cid])


@pytest.mark.parametrize('cid', ('G-GAP-DCA-ONEADD-L-01', 'G-GAP-DCA-ONEADD-S-01'))
def test_budget_only_cap_drops_the_one_add(cid):
    c, tr, mism = run(cid, 'budget')
    assert tr.raw['plan_notes'] == ['add_over_cap'] and tr.trades == [] and mism


@pytest.mark.parametrize('cid', PASS + ONE_ADD_CAP)
def test_the_adapter_never_reads_the_expectation(cid):
    """Blind run (expect poisoned, divergences removed, as the pack runs adapters): the identical trace."""
    c = CASES[cid]
    assert NewcoreSim().run(adapters.blind(c)).as_dict() == NewcoreSim().run(c).as_dict()


def test_the_two_add_cases_fit_either_cap():
    for cid in ONE_ADD_CAP:
        assert run(cid, 'budget')[2] == run(cid)[2]
