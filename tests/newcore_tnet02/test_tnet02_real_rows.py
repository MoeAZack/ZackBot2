"""Real Binance testnet row shapes seen on the owner's PC (5 Nov 2026 preflight).

positionRisk on the real testnet returns ~1,488 rows for every listed contract, including dated futures
(``BTCUSDT_261225``) and non-ASCII names.  One such empty row made the whole read UNKNOWN, so the preflight refused
with ``preflight_read_positions`` before sending anything.  Empty rows for symbols NEWCORE cannot trade are now
skipped; a non-empty one still fails the read closed.
"""
from __future__ import annotations

import json

import pytest

from newcore.ports.venue import ReadKind
from newcore.testnet_harness import SuiteStatus, run_suite
from newcore.venue.binance_usdm_testnet import HttpResponse

from .support import manifest, rig

ODD = ["BTCUSDT_261225", "ETHUSDT_260327", "币安人生USDT"]


def _with_rows(fake, extra):
    real = fake._get_fapi_v2_positionRisk

    def rows(params):
        body = json.loads(real(params).body)
        if "symbol" not in params:
            body += extra
        return HttpResponse(status=200, body=json.dumps(body).encode())
    fake._get_fapi_v2_positionRisk = rows


def _row(symbol, side, amt):
    return {"symbol": symbol, "positionSide": side, "positionAmt": amt, "entryPrice": "0.0", "markPrice": "1.0",
            "leverage": "20"}


def test_empty_rows_for_untradable_symbols_are_skipped(tmp_path):
    r = rig(tmp_path)
    _with_rows(r.fake, [_row(s, side, "0.000") for s in ODD for side in ("LONG", "SHORT")])
    out = r.wired.venue.positions(None)
    assert out.kind is ReadKind.OK
    assert {p.symbol for p in out.value} == set(r.fake.marks)


@pytest.mark.parametrize("amt", ["0.001", "-0.002"], ids=["long", "short"])
def test_a_non_empty_row_for_an_untradable_symbol_still_fails_closed(tmp_path, amt):
    r = rig(tmp_path)
    _with_rows(r.fake, [_row("BTCUSDT_261225", "LONG", amt)])
    assert r.wired.venue.positions(None).kind is not ReadKind.OK


def test_malformed_rows_are_not_hidden_by_the_skip(tmp_path):
    r = rig(tmp_path)
    _with_rows(r.fake, [{"symbol": "BTCUSDT_261225", "positionSide": "BOTH", "positionAmt": "0"}])
    assert r.wired.venue.positions(None).kind is not ReadKind.OK
    (tmp_path / "b").mkdir()
    r2 = rig(tmp_path / "b")
    _with_rows(r2.fake, [_row("BTCUSDT_261225", "LONG", "NaN")])
    assert r2.wired.venue.positions(None).kind is not ReadKind.OK


def test_symbol_scoped_reads_are_unchanged(tmp_path):
    r = rig(tmp_path)
    _with_rows(r.fake, [_row(s, "LONG", "0") for s in ODD])
    out = r.wired.venue.positions("BTCUSDT")
    assert out.kind is ReadKind.OK and {p.symbol for p in out.value} == {"BTCUSDT"}


def test_binding_and_full_matrix_pass_beside_real_testnet_listing(tmp_path):
    r = rig(tmp_path)
    _with_rows(r.fake, [_row(s, side, "0") for s in ODD for side in ("LONG", "SHORT")])
    spec = manifest()
    evidence = r.adapter.confirm_binding(spec.binding)
    assert evidence.isolated is True
    (tmp_path / "m").mkdir()
    full = rig(tmp_path / "m")
    _with_rows(full.fake, [_row(s, side, "0") for s in ODD for side in ("LONG", "SHORT")])
    report = run_suite(manifest(), full.adapter, [].append)
    assert report.status is SuiteStatus.PASSED
