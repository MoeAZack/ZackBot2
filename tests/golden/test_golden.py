"""zb-golden/1 pack: every case x every legacy adapter, compared to the GOLDEN expectation (never one model to the other).

applies_to per adapter:
- required          -> must equal the golden (strict codes / bars; R / pnl to float precision, widened only by a per-trade
                       `tol` that names the adapter and states why, plus the adapter's stated output resolution);
- known_divergence  -> pytest.mark.xfail(strict=True, raises=KnownDivergence). The run must reproduce the recorded
                       `observed` mismatch list EXACTLY: drift inside a known divergence, or a harness error, is a real
                       failure (wrong exception type), and a fixed divergence XPASSes, which fails the run - the fixing PR
                       then removes the divergence entry and sets the adapter `required`; both are part of the contract
                       hash, so that PR appends a `correction` record to CORRECTIONS.json (test_ledger.py);
- not_applicable / pending_adapter -> the reason is mandatory (schema) and the pair is reported as skipped.
No `slow` marker: the pack runs per commit in `verify fast`. Budget: test_pack_runtime_target (60 s) and
test_pack_runtime_hard_stop (120 s), measured on the whole pack whatever the selection or order.
"""
import os, sys, time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from goldenlib import adapters, compare, schema  # noqa: E402

LEGACY = ('legacy_backtest', 'legacy_engine')
TARGET_S = 60.0                      # design budget for the whole pack per commit (AUD-08 section 7)
HARD_STOP_S = 120.0                  # the pack may never cost more than this, whatever else is decided
_TIMES = {}                          # (case id, adapter) -> wall seconds of this session's run


class KnownDivergence(AssertionError):
    """The adapter reproduced exactly the recorded known divergence."""


def _params():
    try:
        cases = schema.load_all()
    except schema.CaseError as e:                         # P3: one failing test naming the file, never a collection crash
        return [pytest.param(e, None, id='pack-invalid')]
    out = []
    for c in cases:
        for ad in LEGACY:
            st = schema.status(c, ad)
            marks = []
            if st == 'known_divergence':
                kd = next(d for d in c['known_divergences'] if d['adapter'] == ad)
                marks.append(pytest.mark.xfail(strict=True, raises=KnownDivergence,
                                               reason=f"{kd['ticket']} {kd['finding']}: {kd['reason']}"))
            elif st != 'required':
                v = c['applies_to'][ad]
                marks.append(pytest.mark.skip(reason=f"{st}: {v['reason']}"))
            out.append(pytest.param(c, ad, id=f"{c['id']}-{ad}", marks=marks))
    return out


@pytest.mark.parametrize('case,adapter', _params())
def test_golden_case(case, adapter):
    if isinstance(case, schema.CaseError):
        pytest.fail(f'golden pack does not load: {case}')
    t0 = time.perf_counter()
    try:
        trace = adapters.get(adapter).run(case)
    finally:
        _TIMES[(case['id'], adapter)] = time.perf_counter() - t0
    mism = compare.compare(case, trace, adapter)
    if schema.status(case, adapter) == 'known_divergence':
        kd = next(d for d in case['known_divergences'] if d['adapter'] == adapter)
        if not mism:
            return                                        # fixed -> strict XPASS -> fails the run until the entry is removed
        assert compare.same_mismatches(case, kd['observed'], mism), \
            'DRIFT inside a known divergence (not the recorded mismatch):\n' + compare.report(case, adapter, mism, trace)
        raise KnownDivergence(f"{kd['ticket']} {kd['finding']} reproduced: " + compare.report(case, adapter, mism, trace))
    assert not mism, compare.report(case, adapter, mism, trace)


def test_every_case_names_every_adapter_and_validates():
    cases = schema.load_all()
    assert cases, 'no golden cases found'
    for c in cases:
        assert set(c['applies_to']) == set(schema.ADAPTERS)


def test_the_pack_has_a_passing_case_on_each_legacy_adapter():
    """The runner is proven on cases that already pass, not only on known divergences."""
    for ad in LEGACY:
        assert any(schema.status(c, ad) == 'required' for c in schema.load_all()), ad


def _pack_times():
    """Wall time of every runnable case x legacy adapter. Reuses what this session already measured and runs every pair that
    has not run (selected alone, -k, reordered collection), so the budget is always the whole pack - it never skips."""
    for c in schema.load_all():
        for ad in LEGACY:
            if schema.status(c, ad) in ('required', 'known_divergence') and (c['id'], ad) not in _TIMES:
                t0 = time.perf_counter()
                try:
                    adapters.get(ad).run(c)
                except Exception:                         # a broken run fails its own case test; its time still counts
                    pass
                _TIMES[(c['id'], ad)] = time.perf_counter() - t0
    total = sum(_TIMES.values())
    slow = sorted(_TIMES.items(), key=lambda kv: -kv[1])[:3]
    print(f'\ngolden pack: {len(_TIMES)} runs in {total:.1f} s (target {TARGET_S:.0f} s, hard stop {HARD_STOP_S:.0f} s); '
          f'slowest {[(k, round(v, 1)) for k, v in slow]}')
    return total


def test_pack_runtime_hard_stop():
    total = _pack_times()
    assert total <= HARD_STOP_S, f'golden pack took {total:.1f} s > {HARD_STOP_S:.0f} s hard stop'


def test_pack_runtime_target():
    total = _pack_times()
    assert total <= TARGET_S, (f'golden pack took {total:.1f} s > the {TARGET_S:.0f} s per-commit target: speed the slow cases up '
                               'or move them out of verify fast with a recorded decision')


def test_legacy_mgmt_mapping_is_explicit():
    """Design section 3: the neutral -> legacy mapping is code and unit-tested; what it cannot express is an error."""
    from goldenlib.adapters.base import NotExpressible, check_costs, legacy_slot
    c = {'slot': {'sides': 'long', 'risk': '0.02', 'max_pos': 1, 'stop': {'atr': '2'}, 'time_exit': {'bars': 4},
                  'entry': {'type': 'trail', 'dev_atr': '0.5', 'max_bars': 3}}}
    assert legacy_slot(c) == ('ema_st', {'stop_atr': 2, 'max_bars': 4}, {'trail_entry': {'dev_atr': 0.5, 'max_bars': 3}})
    d = {'slot': {'sides': 'long', 'dca': {'n': 3, 'step_atr': '1', 'scale': '1.5', 'tp_atr': '1', 'stop_atr': '2'}}}
    key, mg, _ = legacy_slot(d)
    assert key == 'dca_dip' and mg['dca'] == dict(n=3, step_atr=1, scale=1.5, tp_atr=1, stop_atr=2) and mg['max_bars'] == 10 ** 6
    for bad in ({'ladder': [[1, 0.5]]}, {'tp1': {'r': '1', 'frac': '0.5'}}, {'runner': {'dca_frac': '0.5'}},
                {'entry': {'type': 'maker'}}, {'stop': {'atr': '2', 'buffer': 1}}, {'dca': {'n': 3}}, {}):
        with pytest.raises(NotExpressible):
            legacy_slot({'slot': {'sides': 'long', **({'stop': {'atr': '2'}} if 'stop' not in bad and 'dca' not in bad and bad else {}), **bad}})
    for funding in (True, False):
        with pytest.raises(NotExpressible):
            check_costs({'costs': {'taker_fee': '0.0004'}}, funding)
        with pytest.raises(NotExpressible):
            check_costs({'costs': {'fundng_per_bar': '0.01'}}, funding)
    check_costs({'costs': {'funding_per_bar': '0.01'}}, funding=True)
    with pytest.raises(NotExpressible):                       # P2-3a: the engine replay charges no funding
        check_costs({'costs': {'funding_per_bar': '0.01'}}, funding=False)
    check_costs({'costs': {'funding_per_bar': '0'}}, funding=False)


def test_capabilities_and_final_state_are_strict():
    """P2-1: a declared final key missing from a trace is a mismatch; an unknown expected key is a schema error."""
    from goldenlib.adapters import CAPS, Trace
    case = {'id': 'X', 'expect': {'trades': [], 'final': {'lots': 0}}}
    assert compare.compare(case, Trace(trades=[], final={'lots': 0}), 'legacy_engine') == []
    assert compare.compare(case, Trace(trades=[], final={}), 'legacy_engine') == [dict(path='final.lots', expected=0, actual=None)]
    assert compare.compare(case, Trace(trades=[], final={}), 'legacy_backtest') == []      # declares no final keys
    with pytest.raises(AssertionError):
        compare.compare(case, Trace(trades=[], final={'lots': 0}), 'legacy_backtest')     # reports a key it does not declare
    assert set(CAPS) == set(LEGACY) and all(set(v['final']) <= set(schema.FINAL_KEYS) for v in CAPS.values())
    import copy
    c = copy.deepcopy(schema.load(schema.case_paths()[0])); c['expect']['final'] = {'lotz': 0}
    with pytest.raises(schema.CaseError, match='lotz'):
        schema.validate(c)


def test_tolerance_is_per_trade_per_adapter():
    """P2-2: a per-trade tol applies only to the adapters it names; there is no case-wide tolerance."""
    from goldenlib.adapters import Trace
    t = dict(sym='S', side='LONG', i_in=1, i_out=2, exit='STOP_HIT', R='-1.0',
             tol=dict(R='0.0001', adapters=['legacy_engine'], why='path step'))
    case = {'id': 'X', 'expect': {'trades': [t]}}
    act = dict(sym='S', side='LONG', i_in=1, i_out=2, exit='STOP_HIT', R=-1.00005, pnl=0.0)
    assert compare.compare(case, Trace(trades=[act], final={'lots': 0}), 'legacy_engine') == []
    assert [m['path'] for m in compare.compare(case, Trace(trades=[act], final={}), 'legacy_backtest')] == ['trades[0].R']
    import copy
    c = copy.deepcopy(schema.load(schema.case_paths()[0])); c['expect']['tolerance'] = {'R': '0.011', 'why': 'x'}
    with pytest.raises(schema.CaseError, match='case-wide tolerance'):
        schema.validate(c)
