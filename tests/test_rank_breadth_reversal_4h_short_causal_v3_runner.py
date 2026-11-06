import json
from datetime import datetime, timezone

import pytest

from tools.research import rank_breadth_reversal_4h_short_causal_v3_runner as R
from tools.research import rank_breadth_reversal_4h_short_causal_v3_archive as A


RULE = R.ExchangeRule(tick=0.01, step=0.1, min_qty=0.1, min_notional=1.0, max_qty=1000)
MGMT = {"stop_atr": 1.5, "tp1_r": 1.0, "tp1_frac": 0.5,
        "be_r": 1.0, "tp_r": 2.0, "max_bars": 18}


def ms(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1000)


def core20():
    return ("BTCUSDT",) + tuple(f"S{i:02d}" for i in range(1, 20))


def bars(symbols, n=225, *, start=None):
    start = start or ms("2025-04-01T00:00:00")
    return {
        symbol: tuple(R.Bar(start + i * 4 * 3_600_000, 100, 101, 99, 100, volume=10)
                      for i in range(n))
        for symbol in symbols
    }


def tape(symbols, n, entries=(), exits=()):
    start = ms("2025-04-01T00:00:00")
    signals = {}
    for symbol in symbols:
        se = [False] * n
        sx = [False] * n
        if symbol == symbols[1]:
            for index in entries:
                se[index] = True
            for index in exits:
                sx[index] = True
        signals[symbol] = {"le": tuple([False] * n), "se": tuple(se),
                           "lx": tuple([False] * n), "sx": tuple(sx)}
    return R.SourceSignalTape(
        tuple(start + i * 4 * 3_600_000 for i in range(n)),
        tuple([tuple(symbols)] * n), signals,
        {symbol: tuple([1.0] * n) for symbol in symbols},
        {symbol: tuple([None] + [1.0] * (n - 1)) for symbol in symbols},
        tuple(["btc_above_or_on_ema200"] * n),
    )


def fold_inputs(symbols, n=225):
    raw = bars(symbols, n)
    rules = {symbol: RULE for symbol in symbols}
    funding = {symbol: R.FundingLedger((), True, True) for symbol in symbols}
    marks = {symbol: {row.time_ms: 100.0 for row in raw[symbol]} for symbol in symbols}
    return raw, [tuple(symbols)] * n, rules, funding, marks


def test_source_binding_is_exact_short_cell_and_reentry_lock(monkeypatch):
    contract, _ = R.load_contract()
    binding = R.validate_source_bindings(contract)[R.CANDIDATE_IDS[0]]
    assert binding == {
        "strategy_key": "rank_breadth_reversal_4h",
        "function": "sig_rank_breadth_reversal_4h",
        "timeframe": "4h", "side": "short", "reentry_lock": True,
        "params": contract["candidate"]["signal_parameters"],
    }
    import strategies
    monkeypatch.setattr(strategies, "sig_rank_breadth_reversal_4h", lambda *_: {})
    with pytest.raises(R.RunnerError, match="exact source function"):
        R.validate_source_bindings(contract)


def test_signal_tape_matches_exact_source_short_cell_bar_for_bar():
    import pandas as pd
    import strategies

    contract, _ = R.load_contract()
    symbols = core20()
    raw = bars(symbols, 225)
    tape_result = R.build_fold_source_signal_tape(
        contract, R.CANDIDATE_IDS[0], raw, [symbols] * 225)
    frames = {
        symbol: strategies.indicators(pd.DataFrame({
            "t": pd.to_datetime([row.time_ms for row in rows], unit="ms"),
            "o": [row.open for row in rows], "h": [row.high for row in rows],
            "l": [row.low for row in rows], "c": [row.close for row in rows],
            "v": [row.volume for row in rows],
        }))
        for symbol, rows in raw.items()
    }
    context = strategies.build_context(frames, btc_key="BTCUSDT")
    direct = strategies.signals(
        "rank_breadth_reversal_4h", frames["S01"], context["S01"],
        contract["candidate"]["signal_parameters"], mask_sides=False)
    assert tape_result.signals["S01"]["se"] == tuple(bool(value) for value in direct["se"])
    assert tape_result.signals["S01"]["sx"] == tuple(bool(value) for value in direct["sx"])


def test_weekly_pit_cell_change_is_preserved_and_not_union_substituted():
    contract, _ = R.load_contract()
    symbols = core20() + ("S20",)
    raw = bars(symbols, 225)
    first = core20()
    second = ("BTCUSDT",) + tuple(f"S{i:02d}" for i in range(2, 21))
    membership = [first if i < 210 else second for i in range(225)]
    result = R.build_fold_source_signal_tape(contract, R.CANDIDATE_IDS[0], raw, membership)
    assert result.membership[209] == first
    assert result.membership[210] == second
    assert "S01" not in result.membership[210] and "S20" in result.membership[210]


def test_bad_pit_cell_or_time_grid_makes_whole_candidate_incomplete():
    contract, _ = R.load_contract()
    symbols = core20()
    raw = bars(symbols, 225)
    membership = [symbols] * 225
    membership[200] = symbols[:-1]
    with pytest.raises(R.IncompleteCandidate, match="PIT CORE-20"):
        R.build_fold_source_signal_tape(contract, R.CANDIDATE_IDS[0], raw, membership)
    shifted = dict(raw)
    shifted["S19"] = tuple(R.Bar(row.time_ms + 1, row.open, row.high, row.low,
                                 row.close, volume=row.volume) for row in raw["S19"])
    with pytest.raises(R.IncompleteCandidate, match="exact fold grid"):
        R.build_fold_source_signal_tape(contract, R.CANDIDATE_IDS[0], shifted, [symbols] * 225)


def test_source_reentry_lock_blocks_exit_candle_but_next_candle_is_eligible(monkeypatch):
    contract, _ = R.load_contract()
    symbols = core20()
    raw, membership, rules, funding, marks = fold_inputs(symbols)
    # Entry at 200 stops on its fill bar. Decision 200 is locked; decision 201 may re-enter at 202.
    mutable = {key: list(value) for key, value in raw.items()}
    for index in (200, 202):
        row = mutable["S01"][index]
        mutable["S01"][index] = R.Bar(row.time_ms, 100, 103, 99, 100, volume=10)
    raw = {key: tuple(value) for key, value in mutable.items()}
    monkeypatch.setattr(R, "build_fold_source_signal_tape",
                        lambda *_: tape(symbols, 225, entries=(199, 200, 201)))
    result = R._evaluate_raw_fold(contract, R.CANDIDATE_IDS[0], "wf", raw,
                                  membership, rules, funding, marks)
    s01 = [trade for trade in result.trades if trade["symbol"] == "S01"]
    assert [trade["entry_index"] for trade in s01] == [200, 202]
    assert all(trade["exit_reason"] in ("stop", "stop_wins") for trade in s01)


def test_due_signal_exit_precedes_fill_bar_gap_path(monkeypatch):
    contract, _ = R.load_contract()
    symbols = core20()
    raw, membership, rules, funding, marks = fold_inputs(symbols)
    mutable = {key: list(value) for key, value in raw.items()}
    safe = mutable["S01"][200]
    mutable["S01"][200] = R.Bar(safe.time_ms, 100, 101, 99, 100, volume=10)
    gap = mutable["S01"][201]
    mutable["S01"][201] = R.Bar(gap.time_ms, 105, 106, 104, 105, volume=10)
    raw = {key: tuple(value) for key, value in mutable.items()}
    monkeypatch.setattr(R, "build_fold_source_signal_tape",
                        lambda *_: tape(symbols, 225, entries=(199,), exits=(200,)))
    result = R._evaluate_raw_fold(contract, R.CANDIDATE_IDS[0], "wf", raw,
                                  membership, rules, funding, marks)
    trade = next(trade for trade in result.trades if trade["symbol"] == "S01")
    assert trade["exit_index"] == 201
    assert trade["exit_reason"] == "signal"
    assert trade["fills"][-1]["reference_price"] == 105


def test_missing_mark_and_funding_refuse_before_reduced_sample(monkeypatch):
    contract, _ = R.load_contract()
    symbols = core20()
    raw, membership, rules, funding, marks = fold_inputs(symbols)
    monkeypatch.setattr(R, "build_fold_source_signal_tape",
                        lambda *_: tape(symbols, 225, entries=(199,)))
    bad_funding = dict(funding)
    bad_funding["S01"] = R.FundingLedger((), True, False)
    with pytest.raises(R.IncompleteCandidate, match="funding authority incomplete"):
        R._evaluate_raw_fold(contract, R.CANDIDATE_IDS[0], "wf", raw,
                             membership, rules, bad_funding, marks)
    bad_marks = {symbol: dict(values) for symbol, values in marks.items()}
    del bad_marks["S01"][raw["S01"][201].time_ms]
    with pytest.raises(R.IncompleteCandidate, match="missing causal .*mark"):
        R._evaluate_raw_fold(contract, R.CANDIDATE_IDS[0], "wf", raw,
                             membership, rules, funding, bad_marks)


def test_entry_partial_funding_and_final_cash_are_single_count():
    pos = R.open_position(side="short", entry_index=200, entry_time_ms=1000,
                          reference_open=100, atr_entry=1, risk_cash=5,
                          leverage_notional_cap=5000, management=MGMT, rule=RULE)
    assert pos is not None
    fills = R.walk_bar(pos, R.Bar(2000, 100, 101, 97, 99.99, volume=10),
                       201, MGMT, RULE)
    assert [fill.reason for fill in fills] == ["tp1", "stop"]
    account = R.account_position(pos, 0.25)
    expected = (account["gross_reference_price_pnl"] + 0.25 - account["fee_cash"]
                - account["adverse_slippage_cash"])
    assert account["net_pnl"] == pytest.approx(expected)
    assert sum(fill.qty for fill in pos.fills[1:]) == pytest.approx(pos.initial_qty)


def test_cost_multiplier_changes_actual_fills_not_posthoc_repricing():
    base = R.open_position(side="short", entry_index=1, entry_time_ms=0,
                           reference_open=100, atr_entry=1, risk_cash=5,
                           leverage_notional_cap=5000, management=MGMT, rule=RULE,
                           cost_multiplier=1.0)
    stress = R.open_position(side="short", entry_index=1, entry_time_ms=0,
                             reference_open=100, atr_entry=1, risk_cash=5,
                             leverage_notional_cap=5000, management=MGMT, rule=RULE,
                             cost_multiplier=2.0)
    assert stress.entry_exec < base.entry_exec
    assert stress.fills[0].fee_cash > base.fills[0].fee_cash
    assert stress.fills[0].slippage_cash > base.fills[0].slippage_cash


def test_zero_trade_months_and_contaminated_trials_are_disclosed_not_pooled():
    contract, _ = R.load_contract()
    folds = tuple(R.FoldEvaluation(row["name"], (), 500.0, False, 300)
                  for row in contract["folds"])
    stress = {str(multiplier): folds
              for multiplier in contract["costs"]["cost_stress_multipliers_to_report"]}
    report, contributions, cost_rows = R._candidate_report(contract, folds, stress)
    assert report["trade_count"] == 0
    assert all(value == 0 for value in report["utc_monthly_close_counts_including_zeros"].values())
    assert report["monthly_trade_median"] == 0
    assert report["bootstrap_ci_95_mean_r"] == [None, None]
    assert report["failure_reason"] == ["NO_CLOSED_EPISODES"]
    assert [row["status"] for row in report["full_candidate_ledger"][:2]] == [
        "REJECTED_CONTAMINATED_CONTEXT", "REJECTED_CONTAMINATED_CONTEXT"]
    assert report["full_candidate_ledger"][0]["net_r"] == -1.70
    assert report["full_candidate_ledger"][1]["net_r"] == 7.33
    assert not contributions and set(cost_rows) == {"1.0", "1.5", "2.0"}


def test_premerge_refusal_happens_before_adapter_or_archive_row_use():
    class HostileAdapter:
        touched = False

        def load_klines(self, *args):
            self.touched = True
            raise AssertionError("market row opened")

    contract, digest = R.load_contract()
    pf = R.Preflight(contract, digest, "a" * 40, R.CANDIDATE_IDS[0], "b" * 64)
    adapter = HostileAdapter()
    with pytest.raises(R.RunnerError, match="not merged"):
        R.evaluate_raw_candidate(adapter, pf, merge_guard=lambda: False)
    assert adapter.touched is False

    loaded = []
    with pytest.raises(R.RunnerError, match="adapter load refused"):
        R._run_raw_candidate(
            R.CANDIDATE_IDS[0], "unused.json", lambda: loaded.append(True),
            merge_guard=lambda: False, head_fn=lambda: "a" * 40,
            dirty_fn=lambda: False)
    assert loaded == []


def test_filter_authority_failure_wins_before_first_market_row(monkeypatch):
    from tools.research import strategy_causal_pack_01_archive as filter_archive

    contract, digest = R.load_contract()
    pf = R.Preflight(contract, digest, "a" * 40, R.CANDIDATE_IDS[0], "b" * 64)
    adapter = object.__new__(A.RawArchiveAdapter)
    adapter.verify_repository_authorities = lambda *_: A.AuthorityDigests(
        "1" * 64, "2" * 64, "3" * 64, "4" * 64, "5" * 64, "6" * 64)
    market_reads = []
    adapter.core20_union = lambda *_args, **_kwargs: market_reads.append(True)

    def refuse(*_args, **_kwargs):
        raise filter_archive.ArchiveIncomplete("filter authority checksum mismatch: hostile")

    monkeypatch.setattr(filter_archive, "load_fold_filter_authorities", refuse)
    with pytest.raises(filter_archive.ArchiveIncomplete, match="checksum mismatch"):
        R.evaluate_raw_candidate(adapter, pf, merge_guard=lambda: True)
    assert market_reads == []


def test_preflight_dirty_overwrite_and_identity_tombstone_precede_loader(tmp_path):
    out = tmp_path / "result.json"
    called = []
    loader = lambda: called.append(True)
    with pytest.raises(R.RunnerError, match="dirty tree"):
        R._run_with_loader(R.CANDIDATE_IDS[0], out, loader, lambda *_: {},
                           head_fn=lambda: "a" * 40, dirty_fn=lambda: True)
    assert called == []
    out.write_text("occupied", encoding="utf-8")
    with pytest.raises(R.RunnerError, match="overwrite refused"):
        R._run_with_loader(R.CANDIDATE_IDS[0], out, loader, lambda *_: {},
                           head_fn=lambda: "a" * 40, dirty_fn=lambda: False)
    assert called == []


def test_prepared_fill_or_trade_payload_is_never_an_evaluation_route():
    with pytest.raises(R.RunnerError, match="prepared fills/trades"):
        R.evaluate_payload({"trades": [{"r": 999}]}, None)


def test_pinned_authority_byte_mismatch_refuses(tmp_path):
    manifest = tmp_path / "manifest.json.gz"
    universe = tmp_path / "universe.json"
    classes = tmp_path / "classes.json"
    manifest.write_bytes(b"not the pinned manifest")
    universe.write_text("{}", encoding="utf-8")
    classes.write_text("{}", encoding="utf-8")
    with pytest.raises(A.ArchiveIncomplete):
        A.load_authorities(manifest, universe, classes)


def test_default_off_paper_only_and_contract_controls_are_not_overridable():
    contract, _ = R.load_contract()
    assert contract["activation"]["default_enabled"] is False
    assert contract["activation"]["paper_testnet_only"] is True
    assert R.FrozenCandidateBook.START_EQUITY == 500.0
    assert R.FrozenCandidateBook.RISK_FRACTION == 0.01
    assert R.FrozenCandidateBook.MAX_OPEN_POSITIONS == 4
    assert R.FrozenCandidateBook.GROSS_LEVERAGE_MULTIPLE == 10.0
    with pytest.raises(TypeError):
        R.FrozenCandidateBook(core20(), risk_fraction=0.02)
