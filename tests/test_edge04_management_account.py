import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/research"))

import edge04_account as A  # noqa: E402
import edge04_pit as EP  # noqa: E402
import pit_run as PR  # noqa: E402


TF = PR.TF
RULE = {"tick": 0.01, "step": 0.1, "min_qty": 0.1, "min_notional": 5.0}


class Tape:
    def __init__(self, rows, events=None, bps=0.0):
        self.rec = {t: {"b": list(bar)} for t, bar in rows}
        self._events = events or {}
        self._bps = bps

    def bps(self, at, vol, c):
        return self._bps

    def bar_events(self, entry_ms, bo, t):
        return list(self._events.get(bo, ()))


def row(index, o=100.0, h=101.0, lo=99.0, c=100.0):
    return ((index + 1) * TF, (o, h, lo, c))


def replay(h, rows, **kw):
    return A.replay_position(Tape(rows, kw.pop("events", None)), h, entry_ref=100.0, entry_ms=0,
                             qty=kw.pop("qty", 1.0), stop_distance=kw.pop("dist", 2.0),
                             rule=kw.pop("rule", RULE), slip_c=1.0, **kw)


def test_addendum_is_frozen_and_bound_into_adapter_identity():
    add = EP.management_addendum(str(ROOT))
    assert add["status"].startswith("FROZEN BEFORE MARKET DATA OR RESULT")
    assert add["hypotheses"]["H2"]["runtime_time_cap"] is None
    assert add["hypotheses"]["H4"]["at_observation_boundary"].startswith(A.CENSOR)
    binding = EP.binding(str(ROOT))
    assert binding["management_addendum_sha256"] == EP._sha(ROOT / EP.ADDENDUM)
    assert binding["accounting_sha256"] == EP._sha(ROOT / "tools/research/edge04_account.py")


def test_h1_stop_wins_an_unresolved_same_bar_collision():
    out = replay("H1", [row(0, h=103, lo=93)])
    assert out["status"] == PR.COMPLETE and out["reason"] == "exit.stop"
    assert out["partial_done"] is False
    assert [f["kind"] for f in out["fills"]] == ["entry", "exit.stop"]
    assert out["fills"][-1]["price"] == 102.0


def test_h1_actual_partial_moves_stop_to_breakeven_then_gap_is_accounted():
    events = {0: [(1, 0.001, 100.0)], TF: [(TF + 1, 0.001, 100.0)]}
    out = replay("H1", [row(0, h=101, lo=97.5), row(1, o=101, h=101, lo=99)], events=events)
    assert out["reason"] == "exit.breakeven" and out["partial_done"] is True
    assert [f["kind"] for f in out["fills"]] == ["entry", "exit.tp1", "exit.breakeven"]
    assert [f["qty"] for f in out["fills"]] == [1.0, 0.5, 0.5]
    assert out["stop"] == 100.0
    assert out["funding"] == pytest.approx(-0.1)  # full qty in target bar; gap exit precedes next event
    assert out["fees"] == pytest.approx(0.05 + 0.0245 + 0.02525)


def test_zero_sized_partial_does_not_claim_fill_or_breakeven():
    out = replay("H1", [row(0, h=101, lo=97.5), row(1, h=101.5, lo=99)], qty=0.1,
                 observation_end_ms=2 * TF)
    assert out["status"] == PR.INCOMPLETE and out["reason"] == "incomplete.split_end"
    assert out["partial_done"] is False and out["stop"] == 102.0
    assert [f["kind"] for f in out["fills"]] == ["entry"]


def test_h1_partial_and_remaining_target_execute_in_price_order():
    out = replay("H1", [row(0, h=101, lo=93)])
    assert out["reason"] == "exit.target"
    assert [f["kind"] for f in out["fills"]] == ["entry", "exit.tp1", "exit.target"]
    assert [f["price"] for f in out["fills"][1:]] == [98.0, 94.0]
    assert out["open_qty"] == 0


def test_every_partial_slice_gets_adverse_slippage_tick_rounding_and_its_own_fee():
    x = Tape([row(0, h=101, lo=90)], bps=10.0)
    out = A.replay_position(x, "H1", entry_ref=100, entry_ms=0, qty=1, stop_distance=2,
                            rule={**RULE, "tick": 0.1}, slip_c=1.0)
    assert [f["price"] for f in out["fills"]] == [99.9, 98.0, 94.0]
    assert [f["fee"] for f in out["fills"]] == pytest.approx([0.04995, 0.0245, 0.0235])
    assert out["fees"] == pytest.approx(sum(f["fee"] for f in out["fills"]))


def test_h1_and_h3_caps_exit_at_open_after_exactly_the_frozen_bars():
    h1 = replay("H1", [row(i) for i in range(61)])
    assert h1["reason"] == "exit.time_cap" and h1["exit_ms"] == 60 * TF
    h3 = replay("H3", [row(i, lo=98, h=105) for i in range(181)], dist=2.5)
    assert h3["reason"] == "exit.time_cap" and h3["exit_ms"] == 180 * TF


def test_h3_target_and_stop_first_collision_rules():
    collision = replay("H3", [row(0, h=108, lo=97)], dist=2.5)
    assert collision["reason"] == "exit.stop" and collision["fills"][-1]["price"] == 97.5
    target = replay("H3", [row(0, h=108, lo=98)], dist=2.5)
    assert target["reason"] == "exit.target" and target["fills"][-1]["price"] == 107.5


@pytest.mark.parametrize("hypothesis", ["H2", "H4"])
def test_uncapped_runtime_position_is_right_censored_not_force_closed(hypothesis):
    out = replay(hypothesis, [row(i) for i in range(181)], dist=3.0 if hypothesis == "H2" else 2.5)
    assert out["status"] == PR.INCOMPLETE and out["reason"] == A.CENSOR
    assert out["first_unaccountable_ms"] == 180 * TF
    assert out["pnl"] is None and [f["kind"] for f in out["fills"]] == ["entry"]


@pytest.mark.parametrize("hypothesis,dist", [("H2", 3.0), ("H4", 2.5)])
def test_uncapped_runtime_signal_exit_remains_genuine_at_censor_boundary(hypothesis, dist):
    rows = [row(i) for i in range(181)]
    out = replay(hypothesis, rows, dist=dist, signal_exit_times={180 * TF})
    assert out["status"] == PR.COMPLETE and out["reason"] == "exit.signal"
    assert out["exit_ms"] == 180 * TF


def test_missing_bar_and_missing_cost_input_fail_completeness():
    gap = replay("H3", [row(0), row(2)], dist=2.5)
    assert gap["status"] == PR.INCOMPLETE and gap["reason"] == "incomplete.gap"

    class Broken(Tape):
        def bps(self, at, vol, c):
            raise PR.Incomplete("incomplete.slip_history")

    with pytest.raises(A.Edge04AccountError, match="incomplete.slip_history"):
        A.replay_position(Broken([row(0)]), "H1", entry_ref=100, entry_ms=0, qty=1, stop_distance=2,
                          rule=RULE, slip_c=1.0)
