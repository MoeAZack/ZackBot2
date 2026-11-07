from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import math

import pytest

from tools.research import c258_executable as E
from tools.research import c258_archive_source as S

C = E.C


def book_fixture(*, rate=0.0, settlement_hour=200, atomic=False):
    lo, _ = S.frozen_window("wf1")
    end = lo+220*C.HOUR_MS
    symbols = [f"S{i:02d}USDT" for i in range(20)]
    bars, marks, funding = {}, {}, {}
    for rank, symbol in enumerate(symbols+["BTCUSDT"]):
        slope = (.003 if rank < 2 else -.003 if 18 <= rank < 20 else
                 (rank-10)*.00002 if rank < 20 else 0)
        tape = []
        for hour in range(220):
            price = round(100*math.exp(slope*hour) if symbol != "BTCUSDT" else 100+math.sin(hour/15), 4)
            tape.append(C.Bar(lo+hour*C.HOUR_MS, price, round(price+.05, 4), round(price-.05, 4), price, 1e9))
        bars[symbol] = {bar.open_ms: bar for bar in tape}
        marks[symbol] = {bar.open_ms+C.HOUR_MS-1: C.MarkClose(bar.open_ms+C.HOUR_MS-1, bar.close)
                         for bar in tape}
        funding[symbol] = (S.FundingObservation(lo+settlement_hour*C.HOUR_MS,
                                                rate if rank < 2 else 0, 1),)
    filters = {symbol: (C.Filters(Decimal("0.001"), Decimal("0.001"), Decimal("100000"),
                                  Decimal("5")), Decimal("0.0001")) for symbol in symbols}
    faults = {str(lo+200*C.HOUR_MS): symbols[0]} if atomic else {}
    source = SimpleNamespace(_manifest={"synthetic_atomic_failures": faults},
        boundary_open=lambda **kwargs: C.OpenBar(end, bars[kwargs["symbol"]][end-C.HOUR_MS].close))
    data = E.FoldData("wf1", lo, end, bars, marks, funding,
                      {lo: (tuple(symbols), filters)}, [])
    return source, data


def test_archive_book_has_concurrent_positions_exact_cash_and_isolated_diagnostics():
    source, data = book_fixture()
    primary, detail = E.replay_book(source, data, 2000, C.TrialLedger(), synthetic=True)
    assert primary.attempts
    assert any(pairs > 1 for _, pairs, _ in detail["occupancy"])
    assert max(pairs for _, pairs, _ in detail["occupancy"]) <= 3
    assert detail["final_cash"] == pytest.approx(2000+math.fsum(
        row.verdict_net_cash for row in primary.attempts))
    before = [asdict(row) for row in primary.attempts]
    diagnostic, _ = E.replay_book(source, data, 1000, C.TrialLedger(), synthetic=True)
    assert diagnostic.capital == 1000
    assert before == [asdict(row) for row in primary.attempts]
    assert all(row.fee_drag > 0 and row.slippage_drag > 0 for row in primary.attempts)
    stressed = [C.mechanical_stress_attempt(row) for row in primary.attempts]
    assert all(stress.verdict_net_cash < base.verdict_net_cash
               for base, stress in zip(primary.attempts, stressed))


def test_same_open_funding_does_not_change_second_indexed_admission_equity():
    first, data = book_fixture(rate=0)
    second, expensive = book_fixture(rate=.2)
    base, _ = E.replay_book(first, data, 2000, C.TrialLedger(), synthetic=True)
    funded, _ = E.replay_book(second, expensive, 2000, C.TrialLedger(), synthetic=True)
    entry = data.start+200*C.HOUR_MS
    def quantities(rows):
        return [(row.long_symbol, row.short_symbol, tuple(fill.quantity for fill in row.fills
                if fill.phase == "entry")) for row in rows if row.entry_ms == entry]
    assert len(quantities(base.attempts)) == 2
    assert quantities(base.attempts) == quantities(funded.attempts)
    assert sum(row.funding_cash for row in funded.attempts) < 0


def test_existing_position_same_open_funding_is_after_new_admissions(monkeypatch):
    def section(tapes, decision, order):
        hour = (decision-lo)//C.HOUR_MS
        rank = 0 if hour == 200 else 1 if hour == 201 else None
        pairs = () if rank is None else (C.IndexedPair(1,
            C.ScoredSymbol(order[rank], .1, 2), C.ScoredSymbol(order[19-rank], -.1, -2)),)
        return C.CrossSection(0, 1, 1, (), pairs)
    source, data = book_fixture(rate=0, settlement_hour=201)
    lo = data.start
    other_source, other_data = book_fixture(rate=.2, settlement_hour=201)
    monkeypatch.setattr(C, "form_cross_section", section)
    base, _ = E.replay_book(source, data, 2000, C.TrialLedger(), synthetic=True)
    funded, _ = E.replay_book(other_source, other_data, 2000, C.TrialLedger(), synthetic=True)
    assert len(base.attempts) == len(funded.attempts) == 2
    assert base.attempts[1].fills[0].quantity == funded.attempts[1].fills[0].quantity
    assert base.attempts[0].funding_cash == 0
    assert funded.attempts[0].funding_cash < 0


def test_atomic_failure_is_retained_and_whole_book_incomplete():
    source, data = book_fixture(atomic=True)
    replay, _ = E.replay_book(source, data, 2000, C.TrialLedger(), synthetic=True)
    atomic = [row for row in replay.attempts if row.atomic_one_leg_loss]
    assert atomic and replay.incomplete
    assert all(row.verdict_net_cash < 0 and row.close_ms > row.entry_ms for row in atomic)


def test_verified_absence_is_distinct_from_corrupt_pre_entry_source(monkeypatch):
    source, _ = book_fixture()
    lo, hi = S.frozen_window("development")
    source._pit = {"weeks": [{"monday_ms": lo, "books": {"crypto": {"members": []}}}]}
    source.membership_and_filters = lambda **kwargs: (("CORRUPTUSDT",), {})
    source.candles = lambda **kwargs: (_ for _ in ()).throw(C.IncompleteAuthority("changed checksum"))
    source.marks = source.funding = lambda **kwargs: ()
    with pytest.raises(C.IncompleteAuthority, match="changed checksum"):
        E._load_fold(source, "development")
    source.candles = lambda **kwargs: (_ for _ in ()).throw(S.VerifiedSourceAbsence("absent"))
    assert E._load_fold(source, "development").bars["CORRUPTUSDT"] == {}


def test_production_claim_cannot_move_or_repeat_when_code_and_pins_change(tmp_path, monkeypatch):
    common = tmp_path/"shared-git"
    monkeypatch.setattr(C, "_git", lambda *a, **k: str(common))
    pins = C.AuthorityPins("a"*40, *(C.ArtifactPin(path, "b"*64, "c"*64)
        for path in C.EXPECTED_AUTHORITY_PATHS.values()))
    _, path = E.claim_evaluation(pins, None, synthetic=False)
    assert path == common/"research-evaluation-claims"/"c258"/"c258-stage-a-single-use.json"
    with pytest.raises(C.EvaluationLocked, match="fixed across worktrees"):
        E.claim_evaluation(pins, tmp_path/"different", synthetic=False)
    with pytest.raises(C.EvaluationLocked, match="already claimed"):
        E.claim_evaluation(replace(pins, protected_head="d"*40), None, synthetic=False)


def test_calendar_union_counts_shared_weeks_once_and_keeps_exact_boundary_month():
    _, months = E._period_grids([SimpleNamespace(close_ms=S._ms("2025-10-01T00:00:00Z"),
                                                verdict_R=3)],
        S._ms("2025-04-01T00:00:00Z"), S._ms("2025-10-01T00:00:00Z"))
    assert months["2025-10"] == 3 and months["2025-09"] == 0
    shared = "2025-09-29T00:00:00+00:00"
    folds = [SimpleNamespace(weekly_period_keys=(shared,), monthly_period_keys=("2025-10",),
                             weekly_closed_counts_including_zero=(count,),
                             monthly_net_R_including_zero=(value,))
             for count, value in ((3, 2), (4, -1), (0, 0))]
    weeks, monthly = C.aggregate_forward_periods(folds)
    assert sum(weeks) == 7 and weeks.count(7) == 1
    assert len(monthly) == 18 and sum(monthly) == 1


def test_marked_drawdown_reports_full_recovery_duration():
    events = [C.DrawdownEvent(0, "MARK_END", 0, 0, 0),
              C.DrawdownEvent(1, "MARK_END", 0, 0, -3),
              C.DrawdownEvent(100, "MARK_END", 0, 0, 0)]
    assert C.chronological_drawdown(events) == (3, 100)


def test_scheduled_boundary_exit_uses_open_only_evidence():
    source, data = book_fixture()
    entry = data.end-6*C.HOUR_MS
    symbols = ("S00USDT", "S19USDT")
    legs = []
    for symbol, side in zip(symbols, (1, -1)):
        price = Decimal(str(data.bars[symbol][entry].open))
        legs.append(C.LegSpec(symbol, side, price, price-Decimal(side)*Decimal("5"),
                             data.memberships[data.start][1][symbol][0]))
    sized = C.size_pair(*legs, book_equity=2000)
    assert sized is not None
    row = E._build_attempt(source, data, identifier="boundary", entry=entry,
        regime="BTC_ABOVE_OR_ON_EMA200", accepted=C.AcceptedPair(*legs, sized))
    assert row.close_ms == data.end
    assert all(fill.phase == "exit" for fill in row.fills if fill.phase != "entry")
    assert not hasattr(source.boundary_open(symbol=symbols[0], fold="wf1"), "high")


def test_full_wilder_updates_are_not_replaced_by_a_trailing_seed():
    bars = [C.Bar(hour*C.HOUR_MS, 100, 112 if hour == 25 else 101,
                  88 if hour == 25 else 99, 100, 1e9) for hour in range(27)]
    expected = (23*((23*2+24)/24)+2)/24
    assert C.atr24_wilder(bars, 27*C.HOUR_MS) == pytest.approx(expected)
    assert C.atr24_wilder(bars, 27*C.HOUR_MS) != pytest.approx((23*2+24)/24)


def test_unknown_lifecycle_retains_accepted_entry_cost_evidence():
    source, data = book_fixture()
    data.bars["S00USDT"].pop(data.start+201*C.HOUR_MS)
    with pytest.raises(E.ReplayIncomplete) as captured:
        E.replay_book(source, data, 2000, C.TrialLedger(), synthetic=True)
    pending = captured.value.partial["unresolved_accepted_exposure"]
    assert len(pending["entry_fills"]) == 2
    assert all(fill["fee_drag"] > 0 and fill["slippage_drag"] > 0 for fill in pending["entry_fills"])


def test_diagnostic_exposure_failure_cannot_veto_valid_primary(monkeypatch):
    source, data = book_fixture()
    monkeypatch.setattr(E, "_load_fold", lambda *args, **kwargs: data)
    build = E._build_attempt
    def bounded(*args, **kwargs):
        if kwargs["accepted"].sizing.book_equity > 2500:
            raise C.IncompleteAuthority("diagnostic-only missing exposure")
        return build(*args, **kwargs)
    monkeypatch.setattr(E, "_build_attempt", bounded)
    evidence, report = E._fold_report(source, "wf1", C.TrialLedger(), synthetic=True)
    assert evidence.base.attempts and not evidence.base.incomplete
    assert report["capital_simulations"]["3000"]["incomplete"]
    assert report["capital_simulations"]["4000"]["incomplete"]
    assert report["capital_simulations"]["2000"]["closed"] > 0


def test_gap_stop_at_scheduled_open_keeps_mate_until_following_open():
    source, data = book_fixture()
    entry = data.start+200*C.HOUR_MS
    long_symbol, short_symbol = "S00USDT", "S19USDT"
    legs = []
    for symbol, side in ((long_symbol, 1), (short_symbol, -1)):
        price = Decimal(str(data.bars[symbol][entry].open))
        legs.append(C.LegSpec(symbol, side, price, price-Decimal(side)*5,
                             data.memberships[data.start][1][symbol][0]))
    due = entry+6*C.HOUR_MS
    stop = float(legs[0].stop_reference)
    data.bars[long_symbol][due] = C.Bar(due, stop-1, stop+.1, stop-2, stop-1, 1e9)
    sized = C.size_pair(*legs, book_equity=2000)
    row = E._build_attempt(source, data, identifier="gap-due", entry=entry,
        regime="BTC_ABOVE_OR_ON_EMA200", accepted=C.AcceptedPair(*legs, sized))
    finals = {fill.symbol: fill for fill in row.fills if fill.phase != "entry"}
    assert finals[long_symbol].timestamp_ms == due and finals[long_symbol].phase == "stop"
    assert finals[short_symbol].timestamp_ms == due+C.HOUR_MS and finals[short_symbol].phase == "cleanup"


def test_valid_zero_volume_does_not_remove_a_cross_section_member_but_skips_entry():
    source, data = book_fixture()
    stamp = data.start+199*C.HOUR_MS
    data.bars["S00USDT"][stamp] = replace(data.bars["S00USDT"][stamp], quote_volume=0)
    data.bars["S00USDT"][stamp].validate()
    replay, _ = E.replay_book(source, data, 2000, C.TrialLedger(), synthetic=True)
    assert any("entry impact cap" in skipped for skipped in replay.skips)


def test_production_refuses_caller_pins_before_source_or_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "C258ArchiveSource", lambda *a, **k: pytest.fail("source invoked"))
    monkeypatch.setattr(E, "claim_evaluation", lambda *a, **k: pytest.fail("identity invoked"))
    with pytest.raises(C.EvaluationLocked, match="protected mechanical amendment"):
        E.run_archive_evaluation(object(), tmp_path, tmp_path, synthetic=False)


def test_null_protected_amendment_locks_before_source_or_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "C258ArchiveSource", lambda *a, **k: pytest.fail("source invoked"))
    monkeypatch.setattr(E, "claim_evaluation", lambda *a, **k: pytest.fail("identity invoked"))
    def fake_git(root, *args, **kwargs):
        return b"null" if args[0] == "show" else "a"*40
    monkeypatch.setattr(C, "_git", fake_git)
    with pytest.raises(C.EvaluationLocked, match="invalid protected"):
        E.run_archive_evaluation(None, tmp_path, None, synthetic=False)


def test_off_grid_indexed_entry_is_skipped_without_rounding_or_promotion():
    source, data = book_fixture()
    members, rules = data.memberships[data.start]
    data.memberships[data.start] = (members, {symbol: (filters, Decimal("1"))
                                             for symbol, (filters, _) in rules.items()})
    replay, detail = E.replay_book(source, data, 2000, C.TrialLedger(), synthetic=True)
    assert not replay.attempts and not replay.incomplete
    assert any("PRICE_FILTER off-grid raw entry reference" in skip for skip in replay.skips)
    assert detail["indexed_skips"] > 0


def test_off_grid_mandatory_exit_is_retained_unrounded_and_incomplete():
    source, data = book_fixture()
    due = data.start+206*C.HOUR_MS
    original = data.bars["S00USDT"][due]
    invalid = original.open+.00005
    data.bars["S00USDT"][due] = replace(original, open=invalid)
    replay, detail = E.replay_book(source, data, 2000, C.TrialLedger(), synthetic=True)
    exits = [fill for row in replay.attempts for fill in row.fills
             if fill.symbol == "S00USDT" and fill.phase != "entry" and fill.timestamp_ms == due]
    assert exits and exits[0].reference_price == invalid
    assert Decimal(str(exits[0].reference_price)) % Decimal("0.0001") != 0
    assert replay.incomplete and "INCOMPLETE_PRICE_FILTER_TICK_EXIT" in detail["incomplete_reasons"]


def test_missing_interior_btc_hour_does_not_condition_correlation_on_surviving_rows(monkeypatch):
    source, data = book_fixture()
    missing = data.start+C.HOUR_MS
    data.bars["BTCUSDT"].pop(missing)
    monkeypatch.setattr(E, "_load_fold", lambda *a, **k: data)
    evidence, report = E._fold_report(source, "wf1", C.TrialLedger(), synthetic=True)
    alignment = report["exposure_and_correlation"]
    assert evidence.base.attempts and not evidence.base.incomplete
    assert alignment["required_canonical_BTC_intervals"] == 220
    assert alignment["matched_canonical_BTC_intervals"] == 218
    assert alignment["correlation_intervals"] == 0
    assert alignment["book_BTC_return_correlation"] is None and alignment["book_BTC_beta"] is None
    assert not evidence.complete_required_reports and not report["required_report_complete"]
    assert any(gap.get("open_ms") == missing for gap in report["authority_gaps"])


def test_complete_btc_benchmark_uses_every_in_window_interval_including_the_first():
    source, data = book_fixture()
    replay, counts = E.replay_book(source, data, 2000, C.TrialLedger(), synthetic=True)
    report = E._exposure_report(data, replay, counts)
    assert report["BTC_alignment_complete"]
    assert report["required_canonical_BTC_intervals"] == report["matched_canonical_BTC_intervals"] == 220
    assert report["correlation_intervals"] == 220 and not report["BTC_interval_gaps"]
