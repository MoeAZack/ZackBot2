"""zb-golden/1 pack: every case x every legacy adapter, compared to the GOLDEN expectation (never one model to the other).

applies_to per adapter:
- required          -> must equal the golden (strict codes / bars; R / pnl within the case's declared tolerance);
- known_divergence  -> pytest.mark.xfail(strict=True, raises=KnownDivergence). The run must reproduce the recorded
                       `observed` mismatch list EXACTLY: drift inside a known divergence, or a harness error, is a real
                       failure (wrong exception type), and a fixed divergence XPASSes, which fails the run - the fixing PR
                       then removes the divergence entry (the `expect` block, and so the manifest hash, does not change);
- not_applicable / pending_adapter -> the reason is mandatory (schema) and the pair is reported as skipped.
No `slow` marker: the pack runs per commit in `verify fast`. Budget: test_pack_runtime_budget.
"""
import os, sys, time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from goldenlib import adapters, compare, schema  # noqa: E402

LEGACY = ('legacy_backtest', 'legacy_engine')
BUDGET_S = 60.0                      # AUD-07 slice target for the whole pack per commit (design budget: 60 s, hard stop 120 s)
HARD_STOP_S = 120.0
_TIMES = {}


class KnownDivergence(AssertionError):
    """The adapter reproduced exactly the recorded known divergence."""


def _params():
    out = []
    for c in schema.load_all():
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
    t0 = time.perf_counter()
    try:
        trace = adapters.get(adapter).run(case)
    finally:
        _TIMES[(case['id'], adapter)] = time.perf_counter() - t0
    mism = compare.compare(case, trace)
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


def test_pack_runtime_budget():
    """Runs last in this module (pytest keeps file order): the measured per-case times of this session's run."""
    if not _TIMES:
        pytest.skip('no golden case ran in this session (selected alone)')
    total = sum(_TIMES.values())
    slow = sorted(_TIMES.items(), key=lambda kv: -kv[1])[:3]
    print(f'\ngolden pack: {len(_TIMES)} runs in {total:.1f} s (budget {BUDGET_S:.0f} s); slowest {slow}')
    assert total <= HARD_STOP_S, f'golden pack took {total:.1f} s > {HARD_STOP_S:.0f} s hard stop'


def test_legacy_mgmt_mapping_is_explicit():
    """Design section 3: the neutral -> legacy mapping is code and unit-tested; what it cannot express is an error."""
    from goldenlib.adapters.base import NotExpressible, check_costs, legacy_slot
    c = {'slot': {'sides': 'long', 'risk': '0.02', 'max_pos': 1, 'stop': {'atr': '2'}, 'time_exit': {'bars': 4},
                  'entry': {'type': 'trail', 'dev_atr': '0.5', 'max_bars': 3}}}
    assert legacy_slot(c) == ('ema_st', {'stop_atr': 2, 'max_bars': 4}, {'trail_entry': {'dev_atr': 0.5, 'max_bars': 3}})
    d = {'slot': {'sides': 'long', 'dca': {'n': 3, 'step_atr': '1', 'scale': '1.5', 'tp_atr': '1', 'stop_atr': '2'}}}
    key, mg, _ = legacy_slot(d)
    assert key == 'dca_dip' and mg['dca'] == dict(n=3, step_atr=1, scale=1.5, tp_atr=1, stop_atr=2) and mg['max_bars'] == 10 ** 6
    for bad in ({'ladder': [[1, 0.5]]}, {'entry': {'type': 'maker'}}, {'stop': {'atr': '2', 'buffer': 1}},
                {'dca': {'n': 3}}, {}):
        with pytest.raises(NotExpressible):
            legacy_slot({'slot': {'sides': 'long', **({'stop': {'atr': '2'}} if 'stop' not in bad and 'dca' not in bad and bad else {}), **bad}})
    with pytest.raises(NotExpressible):
        check_costs({'costs': {'taker_fee': '0.0004'}})
