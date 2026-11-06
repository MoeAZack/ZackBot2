import json
from datetime import datetime, timezone
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


def ms(iso_utc):
    return int(datetime.fromisoformat(iso_utc).replace(tzinfo=timezone.utc).timestamp() * 1000)


def core20():
    return tuple(f"S{i:02d}" for i in range(20))


def admission(symbol, *, side="long", qty=1.0, price=100.0, entry_cost=0.0):
    return R.BookAdmission(
        R.BookLot(symbol, side, qty, price, price), entry_cost)


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


def test_favourable_gap_short_tp1_fills_at_trigger_without_improvement():
    p = position()
    fills = R.walk_bar(p, bar(o=97.0, h=97.5, l=96.5, c=97.2, t=2_000), 11, MGMT, RULE)
    assert fills[0].reason == "tp1"
    assert fills[0].reference_price == pytest.approx(p.tp1)
    assert fills[0].reference_price > 97.0  # no favourable improvement to the gap open


def test_favourable_gap_long_target_fills_at_trigger_without_improvement():
    mgmt = {"stop_atr": 2.0, "tp_r": 1.0, "max_bars": 10}
    p = position(side="long", management=mgmt)
    fills = R.walk_bar(p, bar(o=103.0, h=103.5, l=102.5, c=103.2, t=2_000), 11, mgmt, RULE)
    assert [x.reason for x in fills] == ["target"]
    assert fills[0].reference_price == pytest.approx(p.target)
    assert fills[0].reference_price < 103.0  # no favourable improvement to the gap open


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
        R._run_with_loader(R.CANDIDATE_IDS[0], out, loader, lambda *_: {},
                          head_fn=lambda: "abc", dirty_fn=lambda: True)
    assert called == []
    out.write_text("occupied", encoding="utf-8")
    with pytest.raises(R.RunnerError, match="overwrite refused"):
        R._run_with_loader(R.CANDIDATE_IDS[0], out, loader, lambda *_: {},
                          head_fn=lambda: "abc", dirty_fn=lambda: False)
    assert called == []


def test_preflight_pins_contract_but_records_actual_clean_evaluator_head(tmp_path):
    first = R.preflight(R.CANDIDATE_IDS[0], tmp_path / "a.json",
                        head_fn=lambda: "head-one", dirty_fn=lambda: False)
    second = R.preflight(R.CANDIDATE_IDS[0], tmp_path / "b.json",
                         head_fn=lambda: "head-two", dirty_fn=lambda: False)
    assert first.git_head == "head-one" and second.git_head == "head-two"
    assert first.contract_sha256 == second.contract_sha256 == R.CONTRACT_SHA256
    assert first.identity != second.identity
    with pytest.raises(R.RunnerError, match="could not be recorded"):
        R.preflight(R.CANDIDATE_IDS[0], tmp_path / "c.json",
                    head_fn=lambda: "", dirty_fn=lambda: False)


def test_one_evaluation_per_contract_head_candidate(tmp_path):
    out = tmp_path / "packet.json"
    ids = tmp_path / "ids"
    payload = {"internal_raw_fixture": True}
    internal_evaluator = lambda data, _pf: {"internal": data}
    R._run_with_loader(R.CANDIDATE_IDS[0], out, lambda: payload, internal_evaluator,
                      identity_dir=ids, head_fn=lambda: "abc", dirty_fn=lambda: False)
    out.unlink()  # output deletion must not permit rerunning the same immutable identity
    with pytest.raises(R.RunnerError, match="already exists"):
        R._run_with_loader(R.CANDIDATE_IDS[0], out, lambda: payload, internal_evaluator,
                          identity_dir=ids, head_fn=lambda: "abc", dirty_fn=lambda: False)


def test_fake_high_aggregate_metrics_and_self_verdicts_cannot_promote(tmp_path):
    pf = R.preflight(R.CANDIDATE_IDS[0], tmp_path / "out.json",
                     head_fn=lambda: "abc", dirty_fn=lambda: False)
    fake = {"candidate_id": R.CANDIDATE_IDS[0], "manifest_digest": "a" * 64,
            "folds": ["wf1", "wf2", "wf3"], "trades": [],
            "report": {"trade_count": 9999, "net_r": 1e9},
            "causal_parity": True, "independent_review": True, "promoted": True}
    with pytest.raises(R.RunnerError, match="prepared fills/trades are untrusted and forbidden"):
        R.evaluate_payload(fake, pf)
    honest_empty = {k: fake[k] for k in ("candidate_id", "manifest_digest", "folds", "trades")}
    with pytest.raises(R.RunnerError, match="raw-bar source-signal evaluator is not implemented"):
        R.evaluate_payload(honest_empty, pf)


def test_caller_fill_injection_is_forbidden_and_raw_episode_uses_causal_primitives(tmp_path):
    pf = R.preflight(R.CANDIDATE_IDS[0], tmp_path / "out.json",
                     head_fn=lambda: "abc", dirty_fn=lambda: False)
    trade = {"trade_id": "wf1-BTC-1", "fold": "wf1", "symbol": "BTCUSDT", "regime": "trend",
             "side": "short", "risk_cash": 10.0,
             "exchange_rule": {"tick": 0.01, "step": 0.1, "min_qty": 0.1,
                               "min_notional": 1.0, "max_qty": 1000},
             "entry": {"index": 1, "time_ms": 1_000, "reference_price": 100.0, "qty": 1.0},
             "exits": [{"index": 2, "time_ms": 3_000, "reference_price": 99.0,
                         "qty": 1.0, "reason": "signal"}],
             "funding": {"events": [{"time_ms": 2_000, "rate": 0.001, "mark": 100.0}],
                         "checksum_verified": True, "coverage_complete": True}}
    payload = {"candidate_id": R.CANDIDATE_IDS[0], "manifest_digest": "b" * 64,
               "folds": ["wf1", "wf2", "wf3"], "trades": [trade]}
    with pytest.raises(R.RunnerError, match="prepared fills/trades are untrusted and forbidden"):
        R.evaluate_payload(payload, pf)

    # A frozen synthetic raw-bar episode still exercises the internal causal
    # primitives: completed decision ATR -> next-open entry -> FBL path fills.
    contract, _ = R.load_contract()
    spec = R.candidate_spec(contract, "pullback_rsi_short_h1_causal_v2")
    raw_bars = [bar(t=i * 1_000) for i in range(202)]
    raw_bars[200] = bar(o=100, h=100.5, l=99.5, c=100, t=200_000)
    raw_bars[201] = bar(o=100, h=101, l=97.5, c=99.99, t=201_000)
    entry_index, entry_open, entry_time = R.next_open(raw_bars, 199)
    assert R.entry_is_eligible(entry_index, spec["max_lookback_bars"],
                               spec["max_holding_bars"], 300)
    p = R.open_position(side=spec["side"], entry_index=entry_index, entry_time_ms=entry_time,
                        reference_open=entry_open, atr_entry=1.0, risk_cash=10.0,
                        leverage_notional_cap=100_000, management=spec["management"], rule=RULE)
    fills = R.walk_bar(p, raw_bars[201], 201, spec["management"], RULE)
    assert [x.reason for x in fills] == ["tp1", "stop"]
    assert fills[0].reference_price == pytest.approx(p.tp1)
    assert fills[1].reference_price == pytest.approx(p.entry_exec)


def test_digest_is_bound_to_file_bytes_not_arbitrary_hex(tmp_path):
    evidence = tmp_path / "manifest.json"
    evidence.write_bytes(b'{"universe":"CORE-20"}\n')
    with pytest.raises(R.RunnerError, match="evidence digest mismatch"):
        R.verify_file_digest(evidence, "a" * 64)
    actual = R.hashlib.sha256(evidence.read_bytes()).hexdigest()
    assert R.verify_file_digest(evidence, actual) == actual


@pytest.mark.parametrize("mutation", [
    {"symbol": "NOTCORE20"}, {"fold": "wf9"}, {"entry": {"index": -1}},
    {"exits": [{"reason": "invented"}]}, {"atr": -1}, {"price": 0},
])
def test_wrong_membership_fold_index_reason_atr_or_price_payload_is_never_accepted(tmp_path, mutation):
    pf = R.preflight(R.CANDIDATE_IDS[0], tmp_path / "out.json",
                     head_fn=lambda: "abc", dirty_fn=lambda: False)
    with pytest.raises(R.RunnerError, match="prepared fills/trades are untrusted and forbidden"):
        R.evaluate_payload({"candidate_id": R.CANDIDATE_IDS[0], **mutation}, pf)


def test_identity_reserved_before_loader_blocks_reentrant_second_packet(tmp_path):
    ids, out1, out2 = tmp_path / "ids", tmp_path / "one.json", tmp_path / "two.json"
    payload = {"candidate_id": R.CANDIDATE_IDS[0], "manifest_digest": "a" * 64,
               "folds": ["wf1", "wf2", "wf3"], "trades": []}
    nested_loader_called = []

    def loader():
        with pytest.raises(R.RunnerError, match="already exists"):
            R._run_with_loader(R.CANDIDATE_IDS[0], out2,
                              lambda: nested_loader_called.append(True), lambda data, _pf: {"data": data},
                              identity_dir=ids, head_fn=lambda: "abc", dirty_fn=lambda: False)
        return payload

    R._run_with_loader(R.CANDIDATE_IDS[0], out1, loader, lambda data, _pf: {"data": data},
                      identity_dir=ids, head_fn=lambda: "abc", dirty_fn=lambda: False)
    assert nested_loader_called == [] and out1.exists() and not out2.exists()
    claim = next(ids.glob("*.claim.json"))
    assert json.loads(claim.read_text(encoding="utf-8"))["state"] == "COMPLETED"


def test_failed_evaluation_consumes_identity_before_data_can_be_reloaded(tmp_path):
    ids, out = tmp_path / "ids", tmp_path / "failed.json"
    calls = []
    with pytest.raises(ValueError, match="boom"):
        R._run_with_loader(R.CANDIDATE_IDS[0], out, lambda: calls.append(1),
                          lambda *_: (_ for _ in ()).throw(ValueError("boom")),
                          identity_dir=ids, head_fn=lambda: "abc", dirty_fn=lambda: False)
    claim = next(ids.glob("*.claim.json"))
    assert json.loads(claim.read_text(encoding="utf-8"))["state"] == "FAILED_CONSUMED"
    with pytest.raises(R.RunnerError, match="already exists"):
        R._run_with_loader(R.CANDIDATE_IDS[0], out, lambda: calls.append(2), lambda *_: {},
                          identity_dir=ids, head_fn=lambda: "abc", dirty_fn=lambda: False)
    assert calls == [1]


@pytest.mark.parametrize("fault", ["json_dump", "write_flush", "fsync"])
def test_post_create_claim_fault_never_reopens_identity_or_emits_packet(tmp_path, monkeypatch, fault):
    ids, out = tmp_path / "ids", tmp_path / f"{fault}.json"
    loader_calls = []
    payload = {"candidate_id": R.CANDIDATE_IDS[0], "manifest_digest": "a" * 64,
               "folds": ["wf1", "wf2", "wf3"], "trades": []}

    def fail(*_args, **_kwargs):
        raise OSError(f"injected {fault} failure")

    with monkeypatch.context() as injected:
        if fault == "json_dump":
            injected.setattr(R.json, "dump", fail)
        elif fault == "write_flush":
            injected.setattr(R, "_flush_json", fail)
        else:
            injected.setattr(R, "_fsync_json", fail)
        with pytest.raises(OSError, match=f"injected {fault} failure"):
            R._run_with_loader(R.CANDIDATE_IDS[0], out,
                              lambda: loader_calls.append(True) or payload, lambda data, _pf: {"data": data},
                              identity_dir=ids, head_fn=lambda: "abc", dirty_fn=lambda: False)

    claims = list(ids.glob("*.claim.json"))
    assert len(claims) == 1 and claims[0].exists()
    assert not out.exists() and loader_calls == []
    with pytest.raises(R.RunnerError, match="already exists"):
        R._run_with_loader(R.CANDIDATE_IDS[0], out,
                          lambda: loader_calls.append(True) or payload, lambda data, _pf: {"data": data},
                          identity_dir=ids, head_fn=lambda: "abc", dirty_fn=lambda: False)
    assert loader_calls == [] and not out.exists()


def test_cli_exposes_exactly_three_frozen_candidates():
    assert R.CANDIDATE_IDS == (
        "pullback_rsi_short_h1_causal_v2",
        "exp_mom_short_c27_causal_v2",
        "volume_pressure_inventory_short_1h_causal_v2",
    )
    assert not hasattr(R, "run_with_loader")
    assert not hasattr(R, "trade_from_evidence")


def test_cli_has_no_prepared_input_or_evaluation_route(tmp_path, capsys):
    out, identities = tmp_path / "packet.json", tmp_path / "ids"
    rc = R.main(["--candidate", R.CANDIDATE_IDS[0], "--out", str(out),
                 "--identity-dir", str(identities)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "contract PR #298" in err and "raw archive adapter" in err
    assert not out.exists() and not identities.exists()


def test_readiness_wires_exact_repository_sources_without_market_read():
    report = R.missing_input_report(contract_merged=False)
    assert report["ready"] is False
    assert set(report["source_bindings"]) == set(R.CANDIDATE_IDS)
    assert report["source_bindings"]["pullback_rsi_short_h1_causal_v2"]["function"] == "sig_trend_pullback_rsi"
    assert report["source_bindings"]["exp_mom_short_c27_causal_v2"]["function"] == "sig_exp_mom_short"
    assert report["source_bindings"]["volume_pressure_inventory_short_1h_causal_v2"]["function"] == "sig_volume_pressure_inventory_1h"
    assert report["folds"] == [
        {"name": "wf1", "start": "2025-04-01T00:00:00Z", "end": "2025-09-30T00:00:00Z"},
        {"name": "wf2", "start": "2025-10-01T00:00:00Z", "end": "2026-03-31T00:00:00Z"},
        {"name": "wf3", "start": "2026-04-01T00:00:00Z", "end": "2026-09-28T00:00:00Z"},
    ]
    joined = "\n".join(report["gaps"])
    assert "contract PR #298" in joined
    assert "risk_cash and leverage_notional_cap" not in joined
    assert "raw archive adapter is not yet enabled" in joined


def test_frozen_book_contract_and_default_archive_store_are_wired(tmp_path):
    contract, _ = R.load_contract()
    frozen = R.validate_book_contract(contract)
    assert frozen["reference_start_equity_usdt"] == R.FrozenCandidateBook.START_EQUITY == 500.0
    assert frozen["risk_fraction_of_current_candidate_book_equity"] == R.FrozenCandidateBook.RISK_FRACTION == 0.01
    assert frozen["maximum_open_positions"] == R.FrozenCandidateBook.MAX_OPEN_POSITIONS == 4
    assert frozen["gross_leverage_multiple"] == R.FrozenCandidateBook.GROSS_LEVERAGE_MULTIPLE == 10.0
    assert frozen["daily_halt_fraction"] == R.FrozenCandidateBook.DAILY_HALT_FRACTION == 0.08
    report = R.missing_input_report(archive_store=tmp_path, contract_merged=False)
    assert report["required_sources"]["archive_store"] == str(tmp_path)
    assert not any("archive store does not exist" in gap for gap in report["gaps"])
    assert str(R.DEFAULT_ARCHIVE_STORE) == r"C:\Dev\ZackBot2_data\binance_um"


def test_simultaneous_entries_use_core20_priority_dynamic_equity_and_caps():
    book = R.FrozenCandidateBook(core20())
    book.advance_bar(ms("2026-07-01T00:00:00"), {})
    seen = []

    def opener(terms):
        seen.append(terms)
        return admission(terms.symbol, qty=10.0, entry_cost=1.0)

    admitted = book.admit_simultaneous(
        ms("2026-07-01T00:00:00"), ["S06", "S03", "S02", "S01", "S00"], opener)
    assert admitted == ["S00", "S01", "S02", "S03"]
    assert [terms.symbol for terms in seen] == admitted
    assert [terms.risk_cash for terms in seen] == pytest.approx([5.0, 4.99, 4.98, 4.97])
    assert [terms.leverage_notional_cap for terms in seen] == pytest.approx([5000, 3990, 2980, 1970])
    assert len(book.positions) == 4


def test_candidate_books_are_isolated_and_compound_realized_equity():
    first, second = R.FrozenCandidateBook(core20()), R.FrozenCandidateBook(core20())
    now = ms("2026-07-01T00:00:00")
    first.advance_bar(now, {})
    second.advance_bar(now, {})
    first.admit_simultaneous(
        now, ["S00"], lambda terms: admission(terms.symbol))
    first.book_final_exit("S00", realized_reference_pnl_cash=50.0, exit_fee_and_slippage_cash=0.0)
    assert first.settled_cash_equity == 550.0
    assert first.risk_cash() == pytest.approx(5.5)
    assert second.settled_cash_equity == 500.0
    assert second.risk_cash() == pytest.approx(5.0)


def test_cairo_daily_halt_latches_entries_but_never_blocks_exit_management():
    book = R.FrozenCandidateBook(core20())
    day_one = ms("2026-07-01T00:00:00")  # 03:00 Cairo (DST)
    book.advance_bar(day_one, {})
    book.admit_simultaneous(
        day_one, ["S00"], lambda terms: admission(terms.symbol, qty=5))
    book.advance_bar(ms("2026-07-01T01:00:00"), {"S00": 90})
    assert book.marked_equity() == 450
    assert book.entries_halted
    called = []
    assert book.admit_simultaneous(
        ms("2026-07-01T02:00:00"), ["S01"], lambda terms: called.append(terms) or None) == []
    assert called == []
    book.advance_bar(ms("2026-07-01T03:00:00"), {"S00": 100})
    assert book.entries_halted  # recovery never unlatches within the Cairo day
    book.book_final_exit("S00", realized_reference_pnl_cash=-50.0, exit_fee_and_slippage_cash=0.0)
    assert book.settled_cash_equity == 450  # exits/management remain enabled
    book.advance_bar(ms("2026-07-01T21:00:00"), {})  # 00:00 next Cairo day
    assert not book.entries_halted and book.day_start_equity == 450


def test_book_controls_have_no_caller_override_and_reject_non_core_entries():
    with pytest.raises(TypeError):
        R.FrozenCandidateBook(core20(), start_equity=999)  # type: ignore[call-arg]
    book = R.FrozenCandidateBook(core20())
    with pytest.raises(R.RunnerError, match="outside frozen CORE-20"):
        book.admit_simultaneous(ms("2026-07-01T00:00:00"), ["NOTCORE20"], lambda _terms: None)


def test_advance_bar_refuses_subset_marks_for_two_open_positions():
    book = R.FrozenCandidateBook(core20())
    now = ms("2026-07-01T00:00:00")
    book.advance_bar(now, {})
    book.admit_simultaneous(
        now, ["S00", "S01"], lambda terms: admission(terms.symbol))
    with pytest.raises(R.IncompleteCandidate, match="cover every open position.*S01"):
        book.advance_bar(ms("2026-07-01T01:00:00"), {"S00": 101})


def test_entry_partial_and_final_cashflows_single_count_costs_and_keep_remainder():
    book = R.FrozenCandidateBook(core20())
    now = ms("2026-07-01T00:00:00")
    book.advance_bar(now, {})
    book.admit_simultaneous(
        now, ["S00"], lambda terms: admission(terms.symbol, qty=10, entry_cost=1.0))
    assert book.settled_cash_equity == 499
    book.advance_bar(ms("2026-07-01T01:00:00"), {"S00": 110})
    assert book.marked_equity() == 599
    book.book_partial_exit(
        "S00", executed_qty=4.0, realized_reference_pnl_cash=40.0,
        exit_fee_and_slippage_cash=1.0, rule=RULE)
    assert book.positions["S00"].qty == 6.0
    assert book.gross_open_notional() == 660.0
    assert book.settled_cash_equity == 538.0
    assert book.marked_equity() == 598.0
    book.book_final_exit(
        "S00", realized_reference_pnl_cash=60.0, exit_fee_and_slippage_cash=1.0)
    assert not book.positions
    assert book.settled_cash_equity == 597.0  # 500 - entry 1 + gross 100 - exits 2


def test_signed_funding_changes_next_entry_risk_equity_and_cap_before_exit():
    book = R.FrozenCandidateBook(core20())
    now = ms("2026-07-01T00:00:00")
    book.advance_bar(now, {})
    book.admit_simultaneous(
        now, ["S00"], lambda terms: admission(terms.symbol, side="short"))
    book.book_signed_funding_cash("S00", 5.0)  # positive rate receipt for short
    seen = []
    book.admit_simultaneous(
        now, ["S01"], lambda terms: seen.append(terms) or admission(terms.symbol, side="short"))
    assert seen[0].marked_equity == 505.0
    assert seen[0].risk_cash == pytest.approx(5.05)
    assert seen[0].leverage_notional_cap == pytest.approx(4950.0)
    book.book_signed_funding_cash("S00", -10.0)
    later = []
    book.admit_simultaneous(
        now, ["S02"], lambda terms: later.append(terms) or admission(terms.symbol, side="short"))
    assert later[0].marked_equity == 495.0
    assert later[0].risk_cash == pytest.approx(4.95)
    assert later[0].leverage_notional_cap == pytest.approx(4750.0)


def test_funding_cashflow_latches_daily_halt_without_blocking_final_exit():
    book = R.FrozenCandidateBook(core20())
    now = ms("2026-07-01T00:00:00")
    book.advance_bar(now, {})
    book.admit_simultaneous(now, ["S00"], lambda terms: admission(terms.symbol))
    book.book_signed_funding_cash("S00", -40.0)
    assert book.entries_halted and book.marked_equity() == 460.0
    book.book_final_exit("S00", realized_reference_pnl_cash=0.0, exit_fee_and_slippage_cash=0.0)
    assert not book.positions
