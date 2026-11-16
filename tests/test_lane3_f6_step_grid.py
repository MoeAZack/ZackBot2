"""LANE3-F6 (PR #359 red-team): flooring to a step that is not a power of ten stays on the grid and never rounds up."""
import os, sys, math, random
from decimal import Decimal
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import feasibility as F


def legacy(x, step):
    """The pre-fix round_step, kept here only to prove power-of-ten steps are unchanged."""
    dec = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(math.floor(x / step + 1e-9) * step, dec)


def on_grid(q, step):
    k = Decimal(repr(q)) / Decimal(repr(step))
    return k == k.to_integral_value()


def test_red_team_repro_non_power_of_ten_step_is_floored_without_leaving_grid():
    """Codex #359 reproducer."""
    result = F.size_check(0.76, 0.76, 100, dict(step=0.25, min_qty=0.25, min_notional=1))
    assert result['ok'] is True and result['qty'] == 0.75


@pytest.mark.parametrize('step,x,want', [(0.25, 0.76, 0.75), (0.25, 0.99, 0.75), (0.025, 0.074, 0.05), (0.5, 1.49, 1.0),
                                         (2.5, 7.4, 5.0), (0.0025, 0.0074, 0.005), (0.05, 0.149, 0.1)])
def test_floor_is_exact_for_non_power_of_ten_steps(step, x, want):
    assert F.round_step(x, step) == want


@pytest.mark.parametrize('step', [0.25, 0.5, 0.2, 0.05, 0.025, 0.0025, 2.5, 5, 0.3, 0.75])
def test_postconditions_never_above_input_always_on_grid(step):
    rnd = random.Random(7)
    for _ in range(3000):
        x = rnd.uniform(0, 1000)
        q = F.round_step(x, step)
        assert q <= x + 1e-12 and on_grid(q, step), (step, x, q)


def test_power_of_ten_steps_bit_identical_to_before():
    """Every current Binance step / tick is a power of ten: results there are exactly the old ones (no replay drift)."""
    rnd = random.Random(11)
    steps = [10.0 ** -k for k in range(13)] + [float(f'1e-{k}') for k in range(1, 13)] + [1, 10, 100, 1000]
    for step in steps:
        for _ in range(2000):
            x = rnd.uniform(0, 1e6) * rnd.choice([1e-9, 1e-6, 1e-3, 1, 1e3])
            assert F.round_step(x, step) == legacy(x, step), (step, x)


def test_engine_rd_follows():
    import engine as E
    assert E.Engine._rd(0.76, 0.25) == 0.75
