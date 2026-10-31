import math
from pathlib import Path

import pytest

from tools.research import carry01 as C


def test_module_has_no_authenticated_or_order_endpoint():
    source = Path(C.__file__).read_text(encoding="utf-8")
    assert "X-MBX-APIKEY" not in source
    assert "/fapi/v1/order" not in source
    assert "/api/v3/order" not in source


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, now_ms, rate=0.0001):
        self.now_ms = now_ms
        self.rate = rate

    def get(self, url, params, timeout):
        if "bookTicker" in url:
            return FakeResponse({"bidPrice": "99.9", "askPrice": "100.1"})
        if "premiumIndex" in url:
            return FakeResponse({"markPrice": "100.2", "time": self.now_ms,
                                 "lastFundingRate": str(self.rate)})
        rows = [{"fundingTime": self.now_ms - i * 8 * 3_600_000, "fundingRate": str(self.rate)}
                for i in range(1, 271)]
        return FakeResponse(rows)


def test_scan_is_public_cost_aware_and_eligible_when_edge_is_large():
    now = 2_000_000_000_000
    result = C.scan_symbol("BTCUSDT", now_ms=now, session=FakeSession(now, rate=0.0003))
    assert result["eligible_shadow"] is True
    assert result["event_count"]["90d"] == 270
    assert result["basis_bps"] == pytest.approx(20.0)
    assert result["net_apr_90d_proxy"] > result["cash_hurdle_apr"]


def test_scan_rejects_negative_funding_even_when_data_is_complete():
    now = 2_000_000_000_000
    result = C.scan_symbol("ETHUSDC", now_ms=now, session=FakeSession(now, rate=-0.0001))
    assert result["eligible_shadow"] is False
    assert result["checks"]["current_funding_non_negative"] is False
    assert result["checks"]["funding_90d_positive"] is False


def test_scan_fails_closed_on_stale_mark():
    now = 2_000_000_000_000
    session = FakeSession(now - 10_000_000)
    with pytest.raises(C.CarryDataError, match="stale"):
        C.scan_symbol("BTCUSDT", now_ms=now, session=session)


def test_replay_flat_prices_collects_funding_after_four_fill_costs():
    start = 2_000_000_000_000
    rows = [{"timestamp_ms": start + i * 8 * 3_600_000, "spot_price": 100, "perp_price": 100,
             "funding_rate": None if i == 0 else 0.001} for i in range(10)]
    result = C.replay(rows)
    assert result["status"] == "COMPLETE"
    assert result["funding_events"] == 9
    assert result["funding_pnl"] > result["trading_cost"]
    assert result["ending_equity"] > result["starting_equity"]
    assert result["price_leg_pnl"] == pytest.approx(0)


def test_replay_delta_hedge_offsets_equal_asset_move():
    start = 2_000_000_000_000
    rows = [
        {"timestamp_ms": start, "spot_price": 100, "perp_price": 100},
        {"timestamp_ms": start + C.DAY_MS, "spot_price": 120, "perp_price": 120},
    ]
    result = C.replay(rows)
    assert result["price_leg_pnl"] == pytest.approx(0)
    assert result["ending_equity"] < result["starting_equity"]
    assert result["trading_cost"] > 0


def test_replay_refuses_lookahead_like_timestamp_order_and_bad_values():
    with pytest.raises(C.CarryDataError, match="strictly increasing"):
        C.replay([
            {"timestamp_ms": 2, "spot_price": 1, "perp_price": 1},
            {"timestamp_ms": 1, "spot_price": 1, "perp_price": 1},
        ])
    with pytest.raises(C.CarryDataError, match="positive"):
        C.replay([
            {"timestamp_ms": 1, "spot_price": 1, "perp_price": 1},
            {"timestamp_ms": 2, "spot_price": 0, "perp_price": 1},
        ])
    with pytest.raises(C.CarryDataError, match="after entry"):
        C.replay([
            {"timestamp_ms": 1, "spot_price": 1, "perp_price": 1, "funding_rate": 0.001},
            {"timestamp_ms": 2, "spot_price": 1, "perp_price": 1},
        ])


def test_replay_rebalances_outside_frozen_band():
    start = 2_000_000_000_000
    result = C.replay([
        {"timestamp_ms": start, "spot_price": 100, "perp_price": 100},
        {"timestamp_ms": start + 3_600_000, "spot_price": 100, "perp_price": 130},
        {"timestamp_ms": start + 2 * 3_600_000, "spot_price": 100, "perp_price": 130},
    ])
    assert result["rebalances"] == 1
    assert math.isfinite(result["ending_equity"])
