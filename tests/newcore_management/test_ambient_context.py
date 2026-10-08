"""The management core never depends on the AMBIENT decimal context (Cowork, acebfb0: `_net_break_even` used `ONE - slip`
etc., so a live run and a restart replay under another context placed different stops; 302 of 3000 seeds).

Arbiter: every input is generated under the default context; then step(), the simulator, the plan builder, the risk /
admission / ledger functions run again under a hostile context (prec 4, ROUND_UP, no traps) and must give byte-identical
results (repr of the frozen records). Plus a static guard: no arithmetic operator on values in newcore/management except
the listed integer ones (timestamps, counters)."""
import ast
import decimal
import glob
import os
import random
from decimal import Decimal as D

import pytest

import test_properties as TP
from mg_factories import GOLDEN_COSTS, LONG, SHORT, ZERO_COSTS, plan, px
from newcore.management import (admit_entry, build_plan, exit_ledger, initial_state, planned_risk, realized_pnl,
                                risk_to_stop, run,
                                step)

HOSTILE = decimal.Context(prec=4, rounding=decimal.ROUND_UP, traps=[])
COWORK_SEEDS = (5, 285, 6868, 5458, 5502, 9993, 17317, 18548)


def hostile(fn, *a, **kw):
    with decimal.localcontext(HOSTILE):
        return fn(*a, **kw)


@pytest.mark.parametrize('seed', COWORK_SEEDS + tuple(range(120)))
def test_live_steps_are_byte_identical_under_a_hostile_context(seed):
    log = []
    TP.run_live(seed, record=log)
    for p, before, conf, candle, r in log:
        again = hostile(step, p, before, conf, candle)
        assert repr(again) == repr(r), seed


@pytest.mark.parametrize('seed', COWORK_SEEDS + tuple(range(120)))
def test_simulated_paths_and_ledgers_are_byte_identical_under_a_hostile_context(seed):
    rng = random.Random(10_000 + seed)
    p = None
    while p is None:
        p = TP.gen_plan(rng)
    candles = TP.gen_path(rng, p, 30, rng.random() < 0.5)
    a = run(p, candles)
    b = hostile(run, p, candles)
    assert repr(a) == repr(b), seed
    assert repr(exit_ledger(p, a.state)) == repr(hostile(exit_ledger, p, a.state)), seed
    if a.state.qty == 0:
        assert repr(realized_pnl(p, a.state)) == repr(hostile(realized_pnl, p, a.state)), seed
    if a.state.stop is not None:
        args = (p, a.state, a.state.stop.price, a.state.qty)
        assert repr(risk_to_stop(*args)) == repr(hostile(risk_to_stop, *args)), seed


@pytest.mark.parametrize('side', (LONG, SHORT))
def test_the_cowork_seed5_break_even_case(side):
    """be_after_tp1, fee 0.0005, slip 0.0002: the net break-even level is the same under any ambient context."""
    p = plan(side, tp1_frac='0.5', tp1_off='2', tp2_off='4', be=True, costs=GOLDEN_COSTS)
    st = step(p, initial_state(p, D('0.25005'))).state
    from newcore.management import ConfirmedFill, Leg
    f = (ConfirmedFill(fill_id='t1', leg=Leg.TP1, qty=D('2.5'), price=st.tp1.price, fee=D('0.1275')),)
    a = step(p, st, f)
    assert repr(a) == repr(hostile(step, p, st, f))


@pytest.mark.parametrize('seed', range(200))
def test_plan_builder_risk_and_admission_are_context_free(seed):
    rng = random.Random(30_000 + seed)
    p = TP.gen_plan(rng)
    if p is None:
        return
    assert repr(planned_risk(p)) == repr(hostile(planned_risk, p))
    kw = dict(rules=p.rules, side=p.side, entry_price=p.entry_price, entry_qty=p.entry_qty,
              entry_candle_open_ms=p.entry_candle_open_ms, candle_seconds=p.candle_seconds, stop_price=p.stop_price,
              risk_cap=p.risk_cap, costs=p.costs, add_price=p.add_price, add_scale=p.add_scale, tp1_frac=p.tp1_frac,
              tp1_offset=p.tp1_offset, tp2_offset=p.tp2_offset, be_after_tp1=p.be_after_tp1,
              time_exit_candles=p.time_exit_candles, trail_offset=p.trail_offset)
    assert repr(build_plan(**kw)) == repr(hostile(build_plan, **kw))
    a = dict(side=p.side, entry_price=p.entry_price, stop_price=p.stop_price, costs=p.costs)
    assert repr(admit_entry(**a)) == repr(hostile(admit_entry, **a))


# ------------------------------------------------------------------------------------------------- static guard
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Integer-only arithmetic (timestamps in ms, candle counts, ids) - the only operators allowed in newcore/management.
INT_ARITH = {
    ('core.py', "d = candle.open_ms - plan.entry_candle_open_ms"),
    ('core.py', "if d < 0 or d % plan.tf_ms:"),
    ('core.py', "if (candle.open_ms - plan.entry_candle_open_ms) // plan.tf_ms + 1 >= plan.time_exit_candles:"),
    ('plan.py', "return 1 if side is Side.LONG else -1"),
    ('plan.py', "return self.candle_seconds * 1000"),
    ('presets.py', "CANDLE_SECONDS = 4 * 3600"),
    ('sim.py', "n += 1"),
    ('driver.py', "lin[key] = n + 1"),                 # the intent-id ordinal (an int)
}


def _is_text_or_seq(n):
    return (isinstance(n, (ast.JoinedStr, ast.Tuple, ast.List)) or
            (isinstance(n, ast.Constant) and isinstance(n.value, str)))


def test_no_ambient_decimal_arithmetic_in_the_management_core():
    bad = []
    for path in sorted(glob.glob(os.path.join(ROOT, 'newcore', 'management', '*.py'))):
        src = open(path, encoding='utf-8').read()
        lines = src.splitlines()
        name = os.path.basename(path)
        for node in ast.walk(ast.parse(src)):
            line = lines[getattr(node, 'lineno', 1) - 1].strip()
            if isinstance(node, ast.BinOp) and not isinstance(node.op, ast.BitOr):
                if isinstance(node.op, (ast.Add, ast.Mod)) and (_is_text_or_seq(node.left) or
                                                                _is_text_or_seq(node.right)):
                    continue                                           # text / tuple / list building
                if isinstance(node.op, ast.Add) and isinstance(node.left, ast.Subscript) and \
                        isinstance(node.right, ast.Tuple):
                    continue
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
                if isinstance(node.operand, ast.Constant) and type(node.operand.value) is int:
                    continue
            elif isinstance(node, ast.AugAssign):
                pass
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('sum', 'abs'):
                pass
            else:
                continue
            if (name, line) not in INT_ARITH:
                bad.append(f'{name}:{node.lineno}: {line}')
    assert bad == [], 'route Decimal arithmetic through CTX / DIV: ' + '; '.join(bad)
