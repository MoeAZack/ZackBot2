"""TNET-02 bounded_dca: the add trigger must be reachable by the feed it is compared with.

Regression for the first real testnet run (2026-11-06, head cf7fc97).  bounded_dca failed with dca_adds == 0.  The
ETHUSDT long filled at 2238.26 while mark was about 2239.17.  The trigger was set 0.05% under the FILL (2237.14), but
it is compared with polled MARK prices.  The mark only went 0.057% adverse, once and briefly, so the polls missed it and
the 8-minute wait ran out.  The rule under test now sets the trigger from the most adverse mark seen and keeps it at
least one tick adverse to the fill.  It also waits as long as the case deadline allows.  dca_adds == 1 is still
required.
"""
from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from newcore.domain.orders import Side
from newcore.testnet_harness import CaseStatus, FailureCode, FinalTruth, ScenarioKind, run_suite
from newcore.tnet02.adapter import AdapterError, HarnessConfig

from .support import manifest, rig

BASE = Decimal("60000")


def _dca(report):
    return next(c for c in report.cases if c.kind is ScenarioKind.BOUNDED_DCA)


def _dip_while_long(fake, *, depth: Decimal, hold_ms: int = 30_000, period_ms: int = 120_000, width_ms: int = 4_000):
    """Mark sits at BASE.  Once a BTCUSDT long has been held for ``hold_ms``, it dips ``depth`` below BASE for
    ``width_ms`` in each ``period_ms``.  Each dip is brief, as in the real run."""
    seen: dict[str, int] = {}

    def path(now: int, symbol: str) -> Decimal:
        if symbol != "BTCUSDT":
            return Decimal("3000")
        if fake.positions.get(("BTCUSDT", "LONG"), Decimal("0")) <= 0:
            seen.pop("since", None)
            return BASE
        since = seen.setdefault("since", now)
        held = now - since
        if held >= hold_ms and (held - hold_ms) % period_ms < width_ms:
            return (BASE * (1 - depth)).quantize(Decimal("0.1"))
        return BASE
    return path


def _rig_with(tmp_path, depth: Decimal, fill_offset: Decimal, config: HarnessConfig | None = None):
    r = rig(tmp_path, price_path=lambda now, symbol: BASE if symbol == "BTCUSDT" else Decimal("3000"),
            config=config or HarnessConfig(poll_ms=1000, trigger_wait_ms=60_000))
    r.fake.price_path = _dip_while_long(r.fake, depth=depth)
    r.fake.fill_offset = fill_offset
    r.fake.tick()
    return r


def test_fill_below_mark_and_brief_mark_dip_still_produces_the_add(tmp_path):
    # real shape: fill about 0.04% under mark, and the mark dips 0.06% for a few seconds at a time
    r = _rig_with(tmp_path, Decimal("0.0006"), Decimal("-24"))
    report = run_suite(manifest(), r.adapter, [].append)
    case = _dca(report)
    assert case.status is CaseStatus.PASSED, case.failure
    assert case.final_truth is FinalTruth.FLAT
    notes = [n for n in r.adapter.notes if n["kind"] == "bounded_dca"]
    assert [n["reason"] for n in notes] == ["dca.added"]
    trigger = Decimal(notes[0]["trigger"])
    assert trigger == Decimal("59970.0")                       # 0.05% under the mark, not under the fill
    assert trigger < BASE + Decimal("-24")                     # still adverse to the 59976 fill


def test_no_adverse_mark_move_fails_honestly_with_a_reason_and_inside_the_deadline(tmp_path):
    r = _rig_with(tmp_path, Decimal("0.0003"), Decimal("0"))   # dips are shallower than the 0.05% trigger
    report = run_suite(manifest(), r.adapter, [].append)
    case = _dca(report)
    assert case.status is CaseStatus.FAILED and case.failure is FailureCode.ASSERTION
    assert case.final_truth is FinalTruth.FLAT
    note = next(n for n in r.adapter.notes if n["kind"] == "bounded_dca")
    assert note["reason"] == "dca.no_trigger"
    assert Decimal(note["mark_low"]) > Decimal(note["trigger"])
    assert note["polls"] > 400                                 # waited about 9 minutes, longer than the old 8
    assert set(note) == {"kind", "reason", "side", "symbol", "polls", "mark_low", "mark_high", "trigger"}


def test_dca_wait_stops_short_of_the_case_deadline(tmp_path):
    r = _rig_with(tmp_path, Decimal("0"), Decimal("0"),
                  HarnessConfig(poll_ms=1000, trigger_wait_ms=60_000, dca_wait_ms=9 * 60 * 1000,
                                dca_finish_margin_ms=5 * 60 * 1000))
    report = run_suite(manifest(), r.adapter, [].append)
    case = _dca(report)
    assert case.failure is FailureCode.ASSERTION
    note = next(n for n in r.adapter.notes if n["kind"] == "bounded_dca")
    assert note["polls"] < 330                                 # capped by deadline minus the 5-minute margin


def _trigger(r, side, marks, entry):
    case = SimpleNamespace(side=side, marks=[Decimal(m) for m in marks])
    return r.adapter._dca_trigger(case, Decimal(entry), Decimal("0.1"))


@pytest.mark.parametrize("side, marks, entry, expected", [
    (Side.LONG, ["60000", "60010"], "59976", "59970.0"),        # from the lowest mark seen
    (Side.LONG, ["60000"], "60030", "59970.0"),                  # fill above mark: mark rule already adverse
    (Side.LONG, ["60100"], "60000", "59999.9"),                  # mark far above fill: clamp one tick under fill
    (Side.SHORT, ["60000", "59990"], "60024", "60030.0"),
    (Side.SHORT, ["59900"], "60000", "60000.1"),
], ids=["long_min_mark", "long_fill_above", "long_clamp", "short_max_mark", "short_clamp"])
def test_trigger_is_beyond_every_seen_mark_and_adverse_to_the_fill(tmp_path, side, marks, entry, expected):
    r = rig(tmp_path)
    got = _trigger(r, side, marks, entry)
    assert got == Decimal(expected)
    seen = [Decimal(m) for m in marks]
    if side is Side.LONG:
        assert got < min(seen) and got < Decimal(entry)
    else:
        assert got > max(seen) and got > Decimal(entry)


@pytest.mark.parametrize("field, value", [("dca_wait_ms", 0), ("dca_wait_ms", 9 * 60 * 1000 + 1),
                                          ("dca_finish_margin_ms", 0), ("dca_finish_margin_ms", 5 * 60 * 1000 + 1),
                                          ("dca_wait_ms", 1.5)])
def test_new_dca_config_is_bounded(field, value):
    with pytest.raises(AdapterError) as caught:
        HarnessConfig(**{field: value})
    assert caught.value.code == f"config.{field}"


def test_diagnostics_side_file_carries_prices_only(tmp_path, capsys):
    from tools import tnet02_run as cli
    notes = ({"kind": "bounded_dca", "reason": "dca.no_trigger", "side": "LONG", "symbol": "BTCUSDT", "polls": 3,
              "mark_low": "1", "mark_high": "2", "trigger": "0.5"},)
    cli._write_diagnostics(tmp_path, "c" * 40, 123, notes)
    written = list(tmp_path.glob("diagnostics-*.json"))
    assert [p.name for p in written] == ["diagnostics-cccccccccccc-123.json"]
    body = json.loads(written[0].read_text(encoding="utf-8"))
    assert body == {"head_sha": "c" * 40, "at_ms": 123, "notes": list(notes)}
    assert "NOTE kind=bounded_dca" in capsys.readouterr().out
    cli._write_diagnostics(tmp_path / "missing", "c" * 40, 124, notes)
    assert "DIAGNOSTICS not written" in capsys.readouterr().out
