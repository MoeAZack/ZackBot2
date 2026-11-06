"""Adversarial executable-runner tests for Candidate 13 ASS-4H causal-v3."""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from tools.research import atr_shock_snapback_4h_causal_v3_runner as R
from tools.research import strategy_causal_pack_01_runner as P


SYMS = tuple(["BTCUSDT"] + [f"S{i:02d}USDT" for i in range(1, 20)])
T0 = 1_743_465_600_000


def bars(n=214, *, missing=None):
    missing = set(missing or ())
    return {symbol: tuple(P.Bar(T0 + i * R.BAR_MS, 100, 101, 99, 100, volume=10)
                          for i in range(n) if (symbol, i) not in missing)
            for symbol in SYMS}


def membership(n=214, first=None):
    order = tuple(first or SYMS)
    return tuple(order for _ in range(n))


def rules():
    return {symbol: P.ExchangeRule(0.01, 0.001, 0.001, 0.01, 1000) for symbol in SYMS}


def funding(*, events=None, gaps=None):
    events, gaps = events or {}, gaps or {}
    return {symbol: P.FundingLedger(tuple(events.get(symbol, ())), True,
                                    not bool(gaps.get(symbol)), tuple(gaps.get(symbol, ())))
            for symbol in SYMS}


def marks(n=214):
    return {symbol: {T0 + i * R.BAR_MS: 100.0 for i in range(n)} for symbol in SYMS}


def pf(side="short"):
    contract, digest = R.load_contract()
    candidate = f"atr_shock_snapback_4h_{side}_causal_v3"
    return R.Preflight(contract, digest, "a" * 40, candidate, side, "b" * 64)


def tape(n=214, *, side="short", entry_at=None, exit_at=None, order=None):
    entry_at, exit_at = entry_at or {}, exit_at or {}
    cells = membership(n, order)
    decisions = []
    for i, cell in enumerate(cells):
        signals = [s for s in cell if i in set(entry_at.get(s, ()))]
        decisions.append({"decision_index": i, "decision_time_ms": T0 + i * R.BAR_MS,
                          "pit_members": list(cell), "pit_member_count": 20,
                          "symbol_status": {s: ("SIGNAL" if s in signals else "NON_SIGNAL") for s in cell},
                          "eligible_denominator": 20, "signal_count": len(signals),
                          "signal_symbols": signals, "exit_signal_symbols": [],
                          "entry_attempt_symbols": [], "admitted_symbols": [],
                          "exposure_failures": []})
    return R.SignalTape(
        tuple(T0 + i * R.BAR_MS for i in range(n)), cells,
        {s: tuple(i in set(entry_at.get(s, ())) for i in range(n)) for s in SYMS},
        {s: tuple(i in set(exit_at.get(s, ())) for i in range(n)) for s in SYMS},
        {s: tuple(1.0 for _ in range(n)) for s in SYMS},
        tuple("above_or_on" for _ in range(n)),
        {"identity": "ASS4H-SPARSE-CAUSAL-AVAILABILITY/v1", "decision_count": n,
         "pit_member_counts": [20] * n, "flat_local_missing_bars": {},
         "locally_available_indicator_decisions": {}, "decisions": decisions})


def test_contract_pin_is_explicitly_provisional_until_protected_merge():
    contract, digest = R.load_contract()
    assert digest == R.CONTRACT_SHA256
    assert R.CONTRACT_COMMIT == "c46a9216f7b8232043e06ff57f78787a5434b307"
    assert contract["sparse_availability"]["identity"] == "ASS4H-SPARSE-CAUSAL-AVAILABILITY/v1"


def test_unmerged_contract_refuses_before_loader_or_market_read(tmp_path):
    called = []
    with pytest.raises(R.RunnerError, match="no market row was read"):
        R.run("atr_shock_snapback_4h_short_causal_v3", tmp_path / "out.json", tmp_path / "ids",
              lambda authority: called.append(authority), merge_guard=lambda: False,
              head_fn=lambda: "a" * 40, dirty_fn=lambda: False)
    assert called == []
    assert not (tmp_path / "ids").exists()


def test_identity_is_consumed_after_a_failed_reserved_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "validate_repository_authorities", lambda contract: {"ok": True})
    monkeypatch.setattr(R, "evaluate_raw", lambda *args: (_ for _ in ()).throw(R.RunnerError("hostile")))
    args = ("atr_shock_snapback_4h_short_causal_v3", tmp_path / "out.json", tmp_path / "ids",
            lambda authority: object())
    kwargs = {"merge_guard": lambda: True, "head_fn": lambda: "a" * 40, "dirty_fn": lambda: False}
    with pytest.raises(R.RunnerError, match="hostile"):
        R.run(*args, **kwargs)
    claim = next((tmp_path / "ids").glob("*.claim.json"))
    assert '"FAILED_CONSUMED"' in claim.read_text()
    with pytest.raises(R.RunnerError, match="already consumed"):
        R.run(*args, **kwargs)


def test_sparse_flat_gap_resets_indicators_and_does_not_fail_cell(monkeypatch):
    contract, _ = R.load_contract()
    raw = bars(214, missing={("S01USDT", 205)})
    import strategies
    original = strategies.indicators
    lengths = []

    def observed(frame):
        lengths.append(len(frame)); return original(frame)

    R.validate_source_binding(contract)
    monkeypatch.setattr(R, "validate_source_binding", lambda unused: {})
    monkeypatch.setattr(strategies, "indicators", observed)
    got = R.build_signal_tape(contract, "atr_shock_snapback_4h_short_causal_v3", raw,
                              membership(214), canonical_times_ms=[T0 + i * R.BAR_MS for i in range(214)])
    assert got.audit["flat_local_missing_bars"]["S01USDT"] == 1
    assert 205 in lengths and 8 in lengths
    assert got.entry["S01USDT"][205] is False
    assert len(got.audit["decisions"]) == 214
    assert got.audit["decisions"][0]["symbol_status"]["BTCUSDT"] == "INSUFFICIENT_LOCAL_HISTORY"
    assert got.audit["decisions"][205]["symbol_status"]["S01USDT"] == "LOCAL_GAP"
    assert got.audit["decisions"][205]["pit_member_count"] == 20
    assert got.audit["decisions"][205]["eligible_denominator"] == 19


def test_appending_future_segment_does_not_rewrite_prior_signals():
    contract, _ = R.load_contract()
    short = R.build_signal_tape(contract, "atr_shock_snapback_4h_long_causal_v3", bars(213),
                                membership(213), canonical_times_ms=[T0 + i * R.BAR_MS for i in range(213)])
    long = R.build_signal_tape(contract, "atr_shock_snapback_4h_long_causal_v3", bars(214),
                               membership(214), canonical_times_ms=[T0 + i * R.BAR_MS for i in range(214)])
    for symbol in SYMS:
        assert long.entry[symbol][:-1] == short.entry[symbol]
        assert long.exit[symbol][:-1] == short.exit[symbol]
        assert long.atr[symbol][:-1] == short.atr[symbol]


def test_flat_symbol_funding_gap_is_harmless_without_exposure(monkeypatch):
    monkeypatch.setattr(R, "build_signal_tape", lambda *args, **kwargs: tape())
    gaps = {"S01USDT": (P.AuthorityGap(T0, T0 + R.BAR_MS, "missing"),)}
    p = pf()
    got = R.evaluate_fold(p.contract, p, "wf1", bars(), membership(), rules(), funding(gaps=gaps), marks(),
                          canonical_times_ms=[T0 + i * R.BAR_MS for i in range(214)], cost_multiplier=1.0)
    assert got.trades == () and got.audit["admitted_signals"] == 0


def test_admitted_signal_missing_next_open_fails_whole_side(monkeypatch):
    monkeypatch.setattr(R, "build_signal_tape", lambda *args, **kwargs: tape(entry_at={"S01USDT": (199,)}))
    raw = bars(missing={("S01USDT", 200)})
    with pytest.raises(R.IncompleteCandidate, match="lacks next open"):
        p = pf()
        R.evaluate_fold(p.contract, p, "wf1", raw, membership(), rules(), funding(), marks(),
                        canonical_times_ms=[T0 + i * R.BAR_MS for i in range(214)], cost_multiplier=1.0)


def test_admitted_exposure_mark_gap_fails_instead_of_dropping_episode(monkeypatch):
    monkeypatch.setattr(R, "build_signal_tape", lambda *args, **kwargs: tape(entry_at={"S01USDT": (199,)}))
    bad_marks = marks(); del bad_marks["S01USDT"][T0 + 201 * R.BAR_MS]
    with pytest.raises(R.IncompleteCandidate, match="missing causal mark"):
        p = pf()
        R.evaluate_fold(p.contract, p, "wf1", bars(), membership(), rules(), funding(), bad_marks,
                        canonical_times_ms=[T0 + i * R.BAR_MS for i in range(214)], cost_multiplier=1.0)


def test_simultaneous_entries_follow_original_pit_order(monkeypatch):
    order = ("S02USDT", "S01USDT") + tuple(s for s in SYMS if s not in ("S01USDT", "S02USDT"))
    monkeypatch.setattr(R, "build_signal_tape", lambda *args, **kwargs:
                        tape(entry_at={"S01USDT": (199,), "S02USDT": (199,)}, order=order))
    seen = []

    def refuse(**kwargs):
        seen.append(kwargs["reference"]); return None

    # Distinct opens make the frozen priority observable without admissions.
    raw = bars()
    raw["S01USDT"] = tuple(replace(row, open=101, high=102, close=101) for row in raw["S01USDT"])
    raw["S02USDT"] = tuple(replace(row, open=102, high=103, close=102) for row in raw["S02USDT"])
    monkeypatch.setattr(R, "_open_position", refuse)
    p = pf()
    R.evaluate_fold(p.contract, p, "wf1", raw, membership(214, order), rules(), funding(), marks(),
                    canonical_times_ms=[T0 + i * R.BAR_MS for i in range(214)], cost_multiplier=1.0)
    assert seen[:2] == [102, 101]


def test_exact_open_funding_counts_for_entry_and_time_exit_excludes_its_timestamp(monkeypatch):
    monkeypatch.setattr(R, "build_signal_tape", lambda *args, **kwargs: tape(entry_at={"S01USDT": (199,)}))
    entry_t, exit_t = T0 + 200 * R.BAR_MS, T0 + 212 * R.BAR_MS
    events = {"S01USDT": (P.FundingEvent(entry_t, 0.001, 100.0),
                           P.FundingEvent(exit_t, 0.5, 100.0))}
    p = pf("short")
    got = R.evaluate_fold(p.contract, p, "wf1", bars(), membership(), rules(), funding(events=events), marks(),
                          canonical_times_ms=[T0 + i * R.BAR_MS for i in range(214)], cost_multiplier=1.0)
    assert len(got.trades) == 1
    trade = got.trades[0]
    assert trade["fills"][-1]["reason"] == "time"
    assert trade["accounting"]["signed_funding_cash"] > 0
    assert trade["accounting"]["signed_funding_cash"] < 1.0  # the 50% exit-time event was excluded
    assert trade["initial_risk_cash"] == pytest.approx(trade["fills"][0]["qty"] * 1.5)
    assert got.audit["decisions"][199]["entry_attempt_symbols"] == ["S01USDT"]
    assert got.audit["decisions"][199]["admitted_symbols"] == ["S01USDT"]


def test_stress_rows_are_three_independent_full_book_replays(monkeypatch):
    calls = []
    monkeypatch.setattr(R, "load_raw_inputs", lambda *args, **kwargs: {
        "bars": {}, "membership": (), "rules": {}, "funding": {}, "marks": {}, "times": ()})

    def fake_fold(contract, pf_, name, *args, cost_multiplier, **kwargs):
        calls.append((name, cost_multiplier))
        return R.FoldResult(name, ({"r": cost_multiplier},), 500.0, {}, 0.0, 0.0)

    monkeypatch.setattr(R, "evaluate_fold", fake_fold)
    monkeypatch.setattr(R, "candidate_report", lambda *args: ({}, {"independent_review": False}))
    authorities = {row["name"]: object() for row in pf().contract["folds"]}
    got = R.evaluate_raw(object(), pf(), {"_filter_authorities": authorities})
    assert calls == [(name, m) for m in (1.0, 1.5, 2.0) for name in ("wf1", "wf2", "wf3")]
    assert got["stress_net_r"] == {"1.0": 3.0, "1.5": 4.5, "2.0": 6.0}


def test_cashflow_drawdown_groups_same_timestamp_atomically():
    trade = {"initial_risk_cash": 1.0, "cashflows": [{"time_ms": 1, "cash": 1.0}]}
    twin = {"initial_risk_cash": 1.0, "cashflows": [{"time_ms": 1, "cash": -1.0}]}
    fold = R.FoldResult("wf1", (trade, twin), 500.0, {}, 0.0, 0.0)
    assert R._cashflow_drawdown(fold) == 0.0


def test_frozen_selection_statistics_use_day_clusters_seeds_and_inclusive_ties():
    one_day = [{"exit_time_ms": T0, "episode_id": "one", "r": 1.0}]
    raw, ci = R._selection_statistics(one_day)
    assert raw == 0.5004749952500475
    assert ci == [1.0, 1.0]
    assert R._selection_statistics([]) == (1.0, [None, None])


def test_empty_report_is_complete_and_fails_closed_without_fake_economics():
    p = pf()
    folds = tuple(R.FoldResult(name, (), 500.0, {"decision_count": 0}, 0.0, 0.0)
                  for name in ("wf1", "wf2", "wf3"))
    report, gates = R.candidate_report(p.contract, p, folds)
    assert set(p.contract["required_report"]) <= set(report)
    assert report["trade_count"] == 0 and report["raw_p_value"] == 1.0
    assert report["bootstrap_ci_95_mean_r"] == [None, None]
    assert report["holm_adjusted_p_value"] == {
        "status": "PENDING_PAIRED_SIDE_FAMILY_ASSEMBLY", "value": None}
    assert report["failure_reason"] == ["NO_CLOSED_EPISODES"]
    assert gates["required_reports"] and gates["zero_incomplete_rows"]
    assert not gates["aggregate_net_r_gt_0"] and not gates["independent_review"]


def test_regime_gate_requires_both_frozen_regimes_not_merely_every_present_row():
    good = {"above_or_on": {"closes": 15, "mean_r": -0.05},
            "below": {"closes": 16, "mean_r": 0.01}}
    assert R._regime_gate(good)
    assert not R._regime_gate({"above_or_on": good["above_or_on"]})
    assert not R._regime_gate({**good, "below": {"closes": 14, "mean_r": 1.0}})
    assert not R._regime_gate({**good, "below": {"closes": 20, "mean_r": -0.051}})
    assert not R._regime_gate({**good, "unknown": {"closes": 99, "mean_r": 1.0}})


def test_side_family_ledger_never_substitutes_one_for_unevaluated_causal_sibling():
    contract, _ = R.load_contract()
    current = "atr_shock_snapback_4h_long_causal_v3"
    ledger = R._pending_family_for_side(contract, current, 0.02)
    rows = {row["trial_id"]: row for row in ledger["trials"]}
    assert rows[current]["holm_input_p_value"] == 0.02
    sibling = rows["atr_shock_snapback_4h_short_causal_v3"]
    assert sibling["raw_p_value"] is None and "holm_input_p_value" not in sibling
    assert [rows[name]["holm_input_p_value"] for name in (
        "atr_shock_snapback_4h_long_stagea_v1", "atr_shock_snapback_4h_short_stagea_v1")] == [1.0, 1.0]
    assert ledger["reconciliation_status"] == "PENDING_BOTH_CAUSAL_SIDE_PACKETS"


def _write_side_packet(path, candidate_id, raw_p, head="c" * 40):
    _, contract_sha = R.load_contract()
    packet = {"format": R.FORMAT, "candidate_id": candidate_id,
              "report": {"raw_p_value": raw_p,
                         "holm_adjusted_p_value": {
                             "status": "PENDING_PAIRED_SIDE_FAMILY_ASSEMBLY", "value": None}},
              "integrity": {"contract_sha256": contract_sha, "git_head": head}}
    packet["packet_sha256"] = __import__("hashlib").sha256(R.canonical(packet)).hexdigest()
    path.write_text(json.dumps(packet), encoding="utf-8")
    return packet


def test_paired_family_assembly_uses_both_actual_causal_p_values_and_is_one_shot(tmp_path):
    long_id = "atr_shock_snapback_4h_long_causal_v3"
    short_id = "atr_shock_snapback_4h_short_causal_v3"
    long_path, short_path = tmp_path / "long.json", tmp_path / "short.json"
    _write_side_packet(long_path, long_id, 0.01)
    _write_side_packet(short_path, short_id, 0.02)
    got = R.assemble_family_packets(long_path, short_path, tmp_path / "family.json", tmp_path / "ids")
    assert got["raw_p_values"][long_id] == 0.01
    assert got["raw_p_values"][short_id] == 0.02
    assert got["holm_adjusted_p_values"][long_id] == 0.04
    assert got["holm_adjusted_p_values"][short_id] == 0.06
    rows = {row["trial_id"]: row for row in got["full_candidate_ledger"]["trials"]}
    assert rows[long_id]["raw_p_value"] == 0.01 and rows[short_id]["raw_p_value"] == 0.02
    assert got["full_candidate_ledger"]["reconciliation_status"] == "COMPLETE_BOTH_CAUSAL_SIDES"
    with pytest.raises(R.RunnerError, match="already consumed"):
        R.assemble_family_packets(long_path, short_path, tmp_path / "another.json", tmp_path / "ids")


def test_paired_family_assembly_rejects_duplicate_side_or_mismatched_head(tmp_path):
    long_id = "atr_shock_snapback_4h_long_causal_v3"
    short_id = "atr_shock_snapback_4h_short_causal_v3"
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    _write_side_packet(a, long_id, 0.1)
    _write_side_packet(b, long_id, 0.2)
    with pytest.raises(R.RunnerError, match="exactly long and short"):
        R.assemble_family_packets(a, b, tmp_path / "family.json", tmp_path / "ids")
    _write_side_packet(b, short_id, 0.2, head="d" * 40)
    with pytest.raises(R.RunnerError, match="exact contract and evaluator head"):
        R.assemble_family_packets(a, b, tmp_path / "family2.json", tmp_path / "ids2")


def test_source_and_all_authorities_are_bound_before_loader_is_reachable(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(R, "validate_source_binding", lambda contract: order.append("source") or {})
    monkeypatch.setattr(R, "validate_repository_authorities",
                        lambda contract: order.append("authority") or {"_filter_authorities": {}})
    monkeypatch.setattr(R, "evaluate_raw",
                        lambda adapter, pf_, authority: order.append("evaluate") or {"integrity": {}})
    R.run("atr_shock_snapback_4h_short_causal_v3", tmp_path / "out.json", tmp_path / "ids",
          lambda authority: order.append("loader") or object(), merge_guard=lambda: True,
          head_fn=lambda: "a" * 40, dirty_fn=lambda: False)
    assert order == ["source", "authority", "loader", "evaluate"]


def test_source_drift_refuses_before_authority_or_market_loader(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(R, "validate_source_binding",
                        lambda contract: (_ for _ in ()).throw(R.RunnerError("source drift")))
    monkeypatch.setattr(R, "validate_repository_authorities", lambda contract: called.append("authority"))
    with pytest.raises(R.RunnerError, match="source drift"):
        R.run("atr_shock_snapback_4h_short_causal_v3", tmp_path / "out.json", tmp_path / "ids",
              lambda authority: called.append("loader"), merge_guard=lambda: True,
              head_fn=lambda: "a" * 40, dirty_fn=lambda: False)
    assert called == []


def test_authority_drift_refuses_before_market_loader(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(R, "validate_source_binding", lambda contract: {})
    monkeypatch.setattr(R, "validate_repository_authorities",
                        lambda contract: (_ for _ in ()).throw(R.RunnerError("authority drift")))
    with pytest.raises(R.RunnerError, match="authority drift"):
        R.run("atr_shock_snapback_4h_short_causal_v3", tmp_path / "out.json", tmp_path / "ids",
              lambda authority: called.append("loader"), merge_guard=lambda: True,
              head_fn=lambda: "a" * 40, dirty_fn=lambda: False)
    assert called == []
