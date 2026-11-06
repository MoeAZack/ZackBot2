import json
from pathlib import Path

import pytest

from tools.research import strategy_causal_pack_01_runner as R


RULE = R.ExchangeRule(tick=0.01, step=0.1, min_qty=0.1, min_notional=1.0, max_qty=1000)
MGMT = {"stop_atr": 2.0, "tp1_r": 1.0, "tp1_frac": 0.5, "be_r": 1.0, "tp_r": 3.0,
        "max_bars": 60}


def bar(o=100, h=101, l=99, c=100, t=0, atr=1.0):
    return R.Bar(t, o, h, l, c, atr)


def position(side="short", management=MGMT, qty_risk=10.0):
    return R.open_position(side=side, entry_index=10, entry_time_ms=1_000, reference_open=100,
                           atr_entry=1, risk_cash=qty_risk, leverage_notional_cap=100_000,
                           management=management, rule=RULE)


def test_exact_fold_local_purge_off_by_one():
    assert list(R.eligible_entry_range(200, 60, 1000)) == list(range(200, 940))
    assert R.entry_is_eligible(200, 200, 60, 1000)
    assert R.entry_is_eligible(939, 200, 60, 1000)
    assert not R.entry_is_eligible(199, 200, 60, 1000)
    assert not R.entry_is_eligible(940, 200, 60, 1000)
    assert R.terminal_fill_is_valid(939, 999, 1000)
    assert not R.terminal_fill_is_valid(939, 1000, 1000)


def test_entry_signal_and_time_fill_only_at_next_open():
    bars = [bar(o=100, c=777, t=1), bar(o=101, c=888, t=2)]
    assert R.next_open(bars, 0) == (1, 101.0, 2)
    with pytest.raises(R.IncompleteCandidate, match="missing tradable next open"):
        R.next_open(bars, 1)
    with pytest.raises(R.IncompleteCandidate, match="invalid tradable next open"):
        R.next_open([bars[0], bar(o=float("nan"), t=2)], 0)

    p = position()
    timeline = [bar(t=i * 1_000) for i in range(80)]
    fill = R.time_exit(p, timeline, 60, RULE)
    assert fill.index == p.entry_index + 60 == 70
    assert fill.reference_price == timeline[70].open


def test_funding_and_mark_fail_closed_and_short_sign_is_credit():
    event = R.FundingEvent(2_000, 0.001, 100)
    good = R.FundingLedger((event,), checksum_verified=True, coverage_complete=True)
    assert R.funding_cash("short", 2, 1_000, 3_000, good) == pytest.approx(0.2)
    assert R.funding_cash("long", 2, 1_000, 3_000, good) == pytest.approx(-0.2)
    with pytest.raises(R.IncompleteCandidate):
        R.funding_cash("short", 2, 1_000, 3_000,
                       R.FundingLedger((event,), checksum_verified=True, coverage_complete=False))
    with pytest.raises(R.IncompleteCandidate, match="mark/rate gap"):
        R.funding_cash("short", 2, 1_000, 3_000,
                       R.FundingLedger((R.FundingEvent(2_000, 0.001, None),), True, True))


def test_slippage_is_single_count_and_stress_does_not_amplify_credits():
    p = position()
    b = bar(o=99, h=100, l=98, c=99, t=3_000)
    f = R._close_fill(p, b, 11, RULE, p.qty, 99, "signal")
    p.qty = 0; p.fills.append(f)
    acc = R.account_position(p, 1.0)
    expected = acc["gross_reference_price_pnl"] + 1.0 - acc["fee_cash"] - acc["adverse_slippage_cash"]
    assert acc["net_pnl"] == pytest.approx(expected)
    assert R.stress_costs(acc, 2.0) == pytest.approx(
        acc["gross_reference_price_pnl"] + 1.0 - 2 * acc["fee_cash"] - 2 * acc["adverse_slippage_cash"])
    debit = dict(acc, signed_funding_cash=-1.0)
    assert R.stress_costs(debit, 2.0) == pytest.approx(
        debit["gross_reference_price_pnl"] - 2.0 - 2 * debit["fee_cash"] - 2 * debit["adverse_slippage_cash"])


def test_stop_frozen_at_open_suppresses_partial_even_if_path_would_hit_it_first():
    p = position()  # executable short is 99.98; stop 101.98, tp1 97.98
    fills = R.walk_bar(p, bar(o=100, h=102.5, l=97.5, c=99, t=2_000), 11, MGMT, RULE)
    assert [(x.reason, x.qty) for x in fills] == [("stop_wins", p.initial_qty)]
    assert not p.tp1_done and p.qty == 0


def test_partial_then_breakeven_only_hits_on_later_adverse_leg():
    p = position()
    # Red short path: open -> high -> low (tp1) -> close. BE is set only after low leg;
    # the close then crosses the executable entry and stops the remainder.
    fills = R.walk_bar(p, bar(o=100, h=101, l=97.5, c=99.99, t=2_000), 11, MGMT, RULE)
    assert [x.reason for x in fills] == ["tp1", "stop"]
    assert fills[0].qty == pytest.approx(p.initial_qty * 0.5)
    assert fills[1].reference_price == pytest.approx(p.entry_exec)

    q = position()
    # Green short path: low comes first, then high. The new BE can be hit only on the later high leg.
    fills = R.walk_bar(q, bar(o=100, h=100.5, l=97.5, c=100.2, t=2_000), 11, MGMT, RULE)
    assert [x.reason for x in fills] == ["tp1", "stop"]


def test_funding_after_partial_applies_only_to_remaining_quantity():
    p = position()
    R.walk_bar(p, bar(o=100, h=101, l=97.5, c=99.99, t=2_000), 11, MGMT, RULE)
    # Both partial and BE exit happen at t=2000, so an event exactly there is
    # excluded for both closed quantities. The entry-time event counts for all.
    ledger = R.FundingLedger((R.FundingEvent(1_000, 0.001, 100),
                              R.FundingEvent(2_000, 0.001, 100)), True, True)
    assert R.funding_cash_for_position(p, ledger) == pytest.approx(p.initial_qty * 100 * 0.001)


def test_trail_ratchets_after_completed_leg_not_retroactively():
    mgmt = {"stop_atr": 5.0, "trail_atr": 1.0, "max_bars": 10}
    p = position(management=mgmt)
    # Green short path goes low first. Trail is tightened after that low; it cannot
    # claim a stop earlier within the same favourable leg, but the later high hits it.
    fills = R.walk_bar(p, bar(o=100, h=100.5, l=98, c=100.2, t=2_000, atr=1), 11, mgmt, RULE)
    assert [x.reason for x in fills] == ["stop"]
    assert fills[0].reference_price == pytest.approx(99.0)


def test_zero_sized_partial_is_retried_later():
    rule = R.ExchangeRule(tick=0.01, step=1.0, min_qty=1, min_notional=1, max_qty=10)
    mgmt = {"stop_atr": 2.0, "tp1_r": 1.0, "tp1_frac": 0.1, "max_bars": 10}
    p = R.open_position(side="short", entry_index=1, entry_time_ms=0, reference_open=100, atr_entry=1,
                        risk_cash=2, leverage_notional_cap=10000, management=mgmt, rule=rule)
    assert p.qty == 1
    assert R.walk_bar(p, bar(o=100, h=100, l=98.5, c=99, t=1), 2, mgmt, rule) == []
    assert not p.tp1_done and p.qty == 1


def test_preflight_refuses_dirty_and_overwrite_before_loader(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "CONTRACT_PATH", R.CONTRACT_PATH)
    out = tmp_path / "packet.json"
    called = []

    def loader():
        called.append(True)
        return {}

    with pytest.raises(R.RunnerError, match="dirty tree"):
        R.run_with_loader(R.CANDIDATE_IDS[0], out, loader, lambda *_: {},
                          head_fn=lambda: "abc", dirty_fn=lambda: True)
    assert called == []
    out.write_text("occupied", encoding="utf-8")
    with pytest.raises(R.RunnerError, match="overwrite refused"):
        R.run_with_loader(R.CANDIDATE_IDS[0], out, loader, lambda *_: {},
                          head_fn=lambda: "abc", dirty_fn=lambda: False)
    assert called == []


def test_one_evaluation_per_contract_head_candidate(tmp_path):
    out = tmp_path / "packet.json"
    ids = tmp_path / "ids"
    contract, _ = R.load_contract()
    report = {key: 0 for key in contract["required_report"]}
    report.update(profit_factor=1.1, max_dd_r=1, monthly_trade_median=8,
                  regime_breakdown={"all": {"mean_r": 0}}, incomplete_rows=0,
                  survivorship_disclosure="PIT CORE-20", failure_reason="economic gates not met")
    payload = {"candidate_id": R.CANDIDATE_IDS[0], "manifest_digest": "a" * 64,
               "folds": [{"name": "wf1", "net_r": 0}, {"name": "wf2", "net_r": 0},
                         {"name": "wf3", "net_r": 0}],
               "report": report, "symbol_contributions": {},
               "cost_stress_net_r": {"1.0": 0, "1.5": 0, "2.0": 0},
               "causal_parity": False, "independent_review": False}
    R.run_with_loader(R.CANDIDATE_IDS[0], out, lambda: payload, R.evaluate_payload,
                      identity_dir=ids, head_fn=lambda: "abc", dirty_fn=lambda: False)
    out.unlink()  # output deletion must not permit rerunning the same immutable identity
    with pytest.raises(R.RunnerError, match="already exists"):
        R.run_with_loader(R.CANDIDATE_IDS[0], out, lambda: payload, R.evaluate_payload,
                          identity_dir=ids, head_fn=lambda: "abc", dirty_fn=lambda: False)


def test_cli_exposes_exactly_three_frozen_candidates():
    assert R.CANDIDATE_IDS == (
        "pullback_rsi_short_h1_causal_v2",
        "exp_mom_short_c27_causal_v2",
        "volume_pressure_inventory_short_1h_causal_v2",
    )
