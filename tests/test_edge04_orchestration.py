import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/research"))

import edge04_orchestrate as O  # noqa: E402
import edge04_pit as EP  # noqa: E402
import costs as C  # noqa: E402
import pit as P  # noqa: E402
import pit_run as PR  # noqa: E402


RULE = {"tick": 0.01, "step": 0.1, "min_qty": 0.1, "min_notional": 5.0}
BASE = 1_767_225_600_000


def candidate(symbol, at=0, end=100, pnl=0.0):
    at, end = BASE + at, BASE + end
    return {"symbol": symbol, "entry_ms": at, "entry_price": 100.0, "signal_close": 100.0,
            "risk_u": 10.0, "rule": dict(RULE), "end": end, "pnl": pnl, "side": "long",
            "marks": {BASE + x: 100.0 for x in (0, 5, 10, 20, 30)}}


def replay(c, qty):
    fee = qty * c["entry_price"] * 0.0005
    return {"status": PR.COMPLETE, "symbol": c["symbol"], "exit_ms": c["end"],
            "wallet_events": [{"at_ms": c["entry_ms"], "delta": -fee, "kind": "entry_fee"},
                              {"at_ms": c["end"], "delta": c["pnl"], "kind": "exit", "terminal": True}]}


def test_orchestration_addendum_stresses_and_binding_are_frozen():
    doc = O.addendum(str(ROOT))
    assert doc["B0"]["books_per_primary"] == 10_000
    s = O.STRESS["all_variable_costs_x1.5"]
    assert (s.fee_mult, s.slip_mult, s.funding_mult) == (1.5, 1.5, 1.5)
    assert O.STRESS["fees_slippage_x2"].fee_mult == O.STRESS["fees_slippage_x2"].slip_mult == 2
    assert O.STRESS["funding_x2"].funding_mult == 2
    assert O.STRESS["gap_slippage_x5"].gap_slip_mult == 5
    bind = EP.binding(str(ROOT))
    assert bind["orchestration_addendum_sha256"] == EP._sha(ROOT / EP.ORCHESTRATION_ADDENDUM)
    assert bind["orchestration_sha256"] == EP._sha(ROOT / "tools/research/edge04_orchestrate.py")


def test_all_variable_15x_scales_slip_fee_and_signed_funding_without_flipping_sign():
    s = O.STRESS["all_variable_costs_x1.5"]
    assert C.fill_price(100, "buy", 10, s) == pytest.approx(100.15)
    assert C.fee(100, "taker", C.CRYPTO, s) == pytest.approx(1.5 * C.TAKER * 100)
    long = C.funding_cost("long", 2, [(1, 0.001, 100)], s)
    short = C.funding_cost("short", 2, [(1, 0.001, 100)], s)
    assert long == pytest.approx(0.3) and short == pytest.approx(-0.3)


def test_shared_book_uses_one_cycle_snapshot_capacity_and_frozen_order():
    rows = [candidate(s, end=1000) for s in ("E", "D", "C", "B", "A")]
    out = O.route_shared(rows, replay)
    assert [x["symbol"] for x in out["accepted"]] == ["A", "B", "C", "D"]
    assert {x["snapshot"] for x in out["accepted"]} == {10_000.0}
    assert out["refused"] == [{"symbol": "E", "entry_ms": BASE, "reason": "capacity.max_positions"}]
    assert all(x["admitted_qty"] == 10.0 for x in out["accepted"])


def test_shared_book_exits_before_entries_and_one_symbol_is_enforced():
    rows = [candidate("BTC", 0, 10, pnl=100), candidate("BTC", 5, 20), candidate("BTC", 10, 30)]
    out = O.route_shared(rows, replay, max_positions=1)
    assert [(x["symbol"], x["snapshot"]) for x in out["accepted"]] == [("BTC", 10_000), ("BTC", 10_099.5)]
    assert out["refused"][0]["reason"] == "capacity.one_per_symbol"


def test_shared_book_requires_causal_marks_and_applies_cairo_daily_halt():
    first = candidate("BTC", 0, 20)
    first["marks"][BASE + 5] = 60.0
    second = candidate("ETH", 5, 20)
    out = O.route_shared([first, second], replay)
    assert out["refused"] == [{"symbol": "ETH", "entry_ms": BASE + 5, "reason": "risk.daily_loss"}]

    missing = candidate("BTC", 0, 20)
    missing["marks"].pop(BASE + 5)
    with pytest.raises(O.OrchestrationError, match="causal mark missing"):
        O.route_shared([missing, candidate("ETH", 5, 20)], replay)


def test_b0_is_exactly_10000_deterministic_matched_books_without_replacement():
    episodes = [{"symbol": "BTC", "side": "long", "holding_bars": 3},
                {"symbol": "BTC", "side": "long", "holding_bars": 3}]
    eligible = [{"symbol": "BTC", "side": "long", "holding_bars": 3, "entry_ms": i} for i in range(6)]
    a = O.b0_books("edge04_legacy_reeval.v1", "H2", "train", episodes, eligible)
    b = O.b0_books("edge04_legacy_reeval.v1", "H2", "train", episodes, eligible)
    assert len(a) == 10_000 and a == b
    assert len({x["entry_ms"] for x in a[0]["entries"]}) == 2
    assert len({x["seed"] for x in a}) == 10_000


def test_b0_unmatched_episode_fails_instead_of_changing_the_match():
    with pytest.raises(O.OrchestrationError, match="unmatched"):
        O.b0_books("edge04_legacy_reeval.v1", "H1", "train",
                    [{"symbol": "BTC", "side": "short", "holding_bars": 7}], [])


def test_b1_requires_exact_executable_opens_and_is_side_matched():
    opens = {"BTC": {10: 100, 20: 110}, "ETH": {10: 50, 20: 45}}
    plan = O.b1_plan(["BTC", "ETH"], "short", opens, start_ms=10, end_ms=20)
    assert [x["weight"] for x in plan] == [0.5, 0.5]
    assert {x["side"] for x in plan} == {"short"}
    del opens["ETH"][20]
    with pytest.raises(O.OrchestrationError, match="INCOMPLETE"):
        O.b1_plan(["BTC", "ETH"], "short", opens, start_ms=10, end_ms=20)


def test_latency_60s_accepts_only_exact_causal_one_minute_coverage():
    bar = P.Bar(1_000, 100, 102, 99, 101, 1, 1, 61_000)
    assert O.latency_60s([bar], 1_000) == 101
    with pytest.raises(O.OrchestrationError, match="1m coverage missing"):
        O.latency_60s([], 1_000)
    fake_15m = P.Bar(1_000, 100, 102, 99, 101, 1, 1, 901_000)
    with pytest.raises(O.OrchestrationError, match="1m coverage missing"):
        O.latency_60s([fake_15m], 1_000)


def attested_outputs():
    out = []
    for i in range(16):
        t = BASE + (i + 1) * PR.TF
        q = {h: {"e": False, "x": False, "atr": "2.5"} for h in ("H1", "H2", "H3", "H4")}
        if i == 14:
            q["H3"]["e"] = True
        out.append({"format": "zb-edge04-pit-tape/1", "t": t, "bear": False,
                    "r": [{"s": "BTC", "b": [100, 101, 99, 100], "f": [], "q": q, "n": 100}]})
    return out


def test_attested_tape_is_the_only_candidate_source_and_latency_uses_1m():
    outputs = attested_outputs()
    rules = {"BTC": dict(RULE)}
    rows = O.candidates_from_tape(outputs, "H3", rules=rules, slip_c=1.0)
    assert len(rows) == 1 and rows[0]["entry_ref"] == 100 and rows[0]["side"] == "long"
    at = rows[0]["entry_ms"]
    one = P.Bar(at, 100, 103, 99, 102, 1, 1, at + 60_000)
    delayed = O.candidates_from_tape(outputs, "H3", rules=rules, slip_c=1.0,
                                     scenario=O.STRESS["entry_latency_60s"], minute_bars={"BTC": [one]})
    assert delayed[0]["entry_ref"] == 102
    with pytest.raises(O.OrchestrationError, match="1m coverage missing"):
        O.candidates_from_tape(outputs, "H3", rules=rules, slip_c=1.0,
                               scenario=O.STRESS["entry_latency_60s"], minute_bars={})


def test_reports_are_write_once(tmp_path):
    path = tmp_path / "report.json"
    doc = {"format": "zb-edge04-report/1", "status": "COMPLETE"}
    O.write_report(str(path), doc)
    O.write_report(str(path), dict(doc))
    with pytest.raises(EP.Edge04Error, match="different content"):
        O.write_report(str(path), {**doc, "status": "INCOMPLETE"})


def passing_metrics():
    return {"status": PR.COMPLETE, "episodes": 60, "episodes_per_week": 1.0,
            "walk_forward_net_r": [1, 1, 1, 1, -0.1], "mean_net_r": 0.2, "profit_factor": 1.2,
            "mean_net_r_lcb95": 0.01, "gross_mean_r": 0.3, "net_return": 0.2,
            "b1_net_return": 0.1, "candidate_minus_b0_lcb95": 0.01,
            "stress": {"all_variable_costs_x1.5_mean_r": 0.01, "entry_latency_60s_sign": 1,
                       "all_variable_costs_x1.5_max_equity_dd": 0.2},
            "max_symbol_contribution": 0.3, "top3_symbol_contribution": 0.6,
            "max_equity_dd": 0.2, "max_closed_r_dd": 20, "holm_pass": True}


def test_gates_are_all_required_and_report_is_content_addressed():
    metrics = passing_metrics()
    assert O.gates(metrics) == {"pass": True, "failures": [],
                                "rule": "every base-primary gate must pass; diagnostics cannot rescue"}
    bad = dict(metrics, status=PR.INCOMPLETE, episodes=59)
    assert O.gates(bad)["failures"] == ["data_complete", "activity"]
    run = {"run_digest": "a" * 64}
    report = O.report_envelope(run, metrics, "b" * 64)
    assert len(report["report_digest"]) == 64 and report["gates"]["pass"] is True


def test_run_envelope_uses_all_10000_hashed_b0_seeds(monkeypatch, tmp_path):
    seen = {}

    def freeze_run(**kw):
        seen.update(kw)
        return {"format": "fake", "run": {}, "run_digest": "c" * 64}

    monkeypatch.setattr(O.R, "freeze_run", freeze_run)
    monkeypatch.setattr(O.R, "write_envelope", lambda path, env: seen.update(store=path))
    ident = {"candidate_id": "edge04_legacy_reeval.v1", "split": "train"}
    env = O.freeze_envelope(str(ROOT), str(tmp_path), eval_files=[], config={"hypothesis": "H3"},
                            schedule=[1], ident=ident)
    assert env["run_digest"] == "c" * 64 and seen["store"] == str(tmp_path)
    assert len(seen["seeds"]) == len(set(seen["seeds"])) == 10_000
    assert seen["seeds"][0] == O.seed(ident["candidate_id"], "H3", "train", 0)
