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
Hard stop (P2 on b01d439): the adapters never run in the pytest process. pack() runs pack_worker.py ONCE per session under
goldenlib.deadline (HARD_STOP_S wall clock, the whole process tree killed on expiry) and every test reads that run, so a hung
adapter fails the session after HARD_STOP_S instead of holding verify-fast to the job timeout. The adapters only see
adapters.blind(case) (Cowork r2 (a)): a trace that equals the golden was produced without the expectation.
"""
import json, os, sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from goldenlib import REPO_ROOT, adapters, compare, deadline, schema  # noqa: E402

LEGACY = ('legacy_backtest', 'legacy_engine')
TARGET_S = 60.0                      # design budget for the whole pack per commit (AUD-08 section 7)
HARD_STOP_S = 120.0                  # the pack may never cost more than this, whatever else is decided
WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pack_worker.py')
_PACK = {}                           # the session's single pack run (pack())


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


def run_pack(hard_stop_s, worker=WORKER):
    """Run the whole pack in a deadline-bounded child -> dict(timed_out, elapsed, runs={(id, adapter): rec}, error)."""
    import tempfile
    fd, out = tempfile.mkstemp(prefix='golden_pack_', suffix='.json')
    os.close(fd)
    try:
        r = deadline.run([sys.executable, worker, out], hard_stop_s, cwd=REPO_ROOT)
        res = dict(timed_out=r.timed_out, elapsed=r.elapsed, runs={}, error=None)
        if r.timed_out:
            res['error'] = (f'golden pack hit the {hard_stop_s:g} s HARD STOP and was killed after {r.elapsed:.2f} s '
                            f'(process tree). stderr tail: {r.stderr[-1500:]}')
        elif r.returncode != 0:
            res['error'] = f'golden pack worker exited {r.returncode}: {r.stderr[-3000:]}'
        else:
            with open(out, encoding='utf-8') as f:
                data = json.load(f)                   # transport: NaN tokens allowed here, compare.py rejects them
            res['runs'] = {(x['id'], x['adapter']): x for x in data['runs']}
        return res
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


def pack():
    if not _PACK:
        _PACK.update(run_pack(HARD_STOP_S))
        slow = sorted(_PACK['runs'].values(), key=lambda x: -x['seconds'])[:3]
        print(f"\ngolden pack: {len(_PACK['runs'])} runs, {_PACK['elapsed']:.1f} s wall incl. worker start (target "
              f"{TARGET_S:.0f} s, hard stop {HARD_STOP_S:.0f} s); slowest "
              f"{[(x['id'], x['adapter'], round(x['seconds'], 1)) for x in slow]}")
    return _PACK


def pack_trace(case_id, adapter):
    p = pack()
    if p['error']:
        pytest.fail(p['error'])
    rec = p['runs'].get((case_id, adapter))
    if rec is None:
        pytest.fail(f'golden pack worker did not run {case_id} [{adapter}]')
    if 'error' in rec:
        pytest.fail(f"{case_id} [{adapter}] raised in the adapter: {rec['error']}")
    return rec


@pytest.mark.parametrize('case,adapter', _params())
def test_golden_case(case, adapter):
    if isinstance(case, schema.CaseError):
        pytest.fail(f'golden pack does not load: {case}')
    rec = pack_trace(case['id'], adapter)
    trace = adapters.Trace(trades=rec['trace']['trades'], final=rec['trace']['final'])
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


def test_pack_runtime_hard_stop():
    """The whole pack (every runnable pair, whatever the selection) ran inside the HARD_STOP_S deadline."""
    p = pack()
    assert not p['timed_out'], p['error']
    assert p['error'] is None, p['error']
    want = {(c['id'], ad) for c in schema.load_all() for ad in LEGACY if schema.status(c, ad) in ('required', 'known_divergence')}
    assert set(p['runs']) == want, f"the pack run is incomplete: missing {sorted(want - set(p['runs']))}"
    assert p['elapsed'] <= HARD_STOP_S, f"golden pack took {p['elapsed']:.1f} s > {HARD_STOP_S:.0f} s hard stop"


def test_pack_runtime_target():
    p = pack()
    assert p['error'] is None, p['error']
    assert p['elapsed'] <= TARGET_S, (f"golden pack took {p['elapsed']:.1f} s > the {TARGET_S:.0f} s per-commit target: speed "
                                      'the slow cases up or move them out of verify fast with a recorded decision')


def test_adapters_never_read_the_expectation():
    """Cowork r2 (a): legacy_backtest on the full case and on blind(case) (expect poisoned, known divergences removed) gives
    identical traces for every case; legacy_engine only ever runs blind, so its golden comparison is the same proof."""
    n = 0
    for c in schema.load_all():
        if schema.status(c, 'legacy_backtest') in ('required', 'known_divergence'):
            rec = pack_trace(c['id'], 'legacy_backtest')
            assert rec['trace'] == rec['trace_full'], f"{c['id']}: legacy_backtest trace depends on expect / known_divergences"
            n += 1
    assert n


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
