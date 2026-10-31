"""CARRY-01: read-only cash-and-carry scanner and deterministic replay.

This module has deliberately no authenticated client and no order methods.  The live
command reads Binance public spot/USD-M endpoints; the replay command reads a frozen
JSONL file.  A positive result is research evidence, never activation authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import requests

SPOT_API = "https://api.binance.com"
FUTURES_API = "https://fapi.binance.com"
DAY_MS = 86_400_000
YEAR_DAYS = 365.0


class CarryDataError(ValueError):
    """The observation cannot support a causal carry calculation."""


@dataclass(frozen=True)
class CarryConfig:
    capital: float = 10_000.0
    spot_fraction: float = 0.75
    target_leverage: float = 3.0
    min_leverage: float = 2.0
    max_leverage: float = 5.0
    spot_fee_bps: float = 10.0
    perp_fee_bps: float = 5.0
    spot_slippage_bps: float = 5.0
    perp_slippage_bps: float = 5.0
    cash_benchmark_apr: float = 0.04
    safety_margin_apr: float = 0.02
    amortization_days: int = 90
    max_basis_bps: float = 50.0
    max_age_s: int = 120
    max_future_skew_s: int = 10
    maintenance_margin_rate: float = 0.005
    min_annualize_days: int = 30

    def validate(self) -> None:
        if not self.capital > 0:
            raise CarryDataError("capital must be positive")
        if not 0 < self.spot_fraction < 1:
            raise CarryDataError("spot_fraction must be between zero and one")
        if not 0 < self.min_leverage < self.target_leverage < self.max_leverage:
            raise CarryDataError("leverage limits must bracket target_leverage")
        for name in ("spot_fee_bps", "perp_fee_bps", "spot_slippage_bps", "perp_slippage_bps"):
            if getattr(self, name) < 0:
                raise CarryDataError(f"{name} cannot be negative")
        if self.amortization_days < 1 or self.max_age_s < 1 or self.max_future_skew_s < 0:
            raise CarryDataError("amortization_days and mark-age limits are invalid")
        if not 0 <= self.maintenance_margin_rate < 1 or self.min_annualize_days < 1:
            raise CarryDataError("maintenance margin and minimum annualization window are invalid")


def _finite(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise CarryDataError(f"{name} cannot be boolean")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise CarryDataError(f"{name} is not numeric") from exc
    if not math.isfinite(out) or (positive and out <= 0):
        raise CarryDataError(f"{name} must be {'positive and ' if positive else ''}finite")
    return out


def _public_get(session: requests.Session, base: str, path: str, params: dict[str, Any]) -> Any:
    response = session.get(base + path, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def _window_apr(rows: list[dict[str, Any]], now_ms: int, days: int) -> tuple[float, int]:
    cutoff = now_ms - days * DAY_MS
    rates = [_finite(row.get("fundingRate"), "fundingRate") for row in rows
             if cutoff <= int(row.get("fundingTime", -1)) <= now_ms]
    if not rates:
        raise CarryDataError(f"no settled funding events in the {days}d window")
    return math.fsum(rates) * YEAR_DAYS / days, len(rates)


def _validate_funding_history(rows: Any, now_ms: int) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise CarryDataError("funding history is not a list")
    previous = -1
    for row in rows:
        ts = row.get("fundingTime") if isinstance(row, dict) else None
        if isinstance(ts, bool) or not isinstance(ts, int):
            raise CarryDataError("fundingTime must be an integer")
        if ts <= previous:
            raise CarryDataError("funding history must be unique and strictly increasing")
        if ts > now_ms:
            raise CarryDataError("funding history contains a future event")
        _finite(row.get("fundingRate"), "fundingRate")
        previous = ts
    return rows


def _round_trip_cost_rate(config: CarryConfig) -> float:
    # Both legs are opened and closed.  Rates are expressed against total portfolio equity.
    per_open_close = (config.spot_fee_bps + config.spot_slippage_bps
                      + config.perp_fee_bps + config.perp_slippage_bps) / 10_000
    return 2 * config.spot_fraction * per_open_close


def scan_symbol(symbol: str, *, now_ms: int | None = None, config: CarryConfig | None = None,
                session: requests.Session | None = None) -> dict[str, Any]:
    """Read one public spot/perpetual pair and apply the frozen shadow admission gate."""
    config = config or CarryConfig()
    config.validate()
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    session = session or requests.Session()
    symbol = symbol.upper()

    spot = _public_get(session, SPOT_API, "/api/v3/ticker/bookTicker", {"symbol": symbol})
    perp = _public_get(session, FUTURES_API, "/fapi/v1/premiumIndex", {"symbol": symbol})
    history = _validate_funding_history(_public_get(session, FUTURES_API, "/fapi/v1/fundingRate", {
        "symbol": symbol, "startTime": now_ms - 91 * DAY_MS, "endTime": now_ms, "limit": 1000,
    }), now_ms)

    bid = _finite(spot.get("bidPrice"), "spot bid", positive=True)
    ask = _finite(spot.get("askPrice"), "spot ask", positive=True)
    mark = _finite(perp.get("markPrice"), "perpetual mark", positive=True)
    mark_time = int(perp.get("time", 0))
    if bid > ask:
        raise CarryDataError("spot bid exceeds ask")
    age_s = (now_ms - mark_time) / 1000
    if mark_time <= 0 or age_s < -config.max_future_skew_s or age_s > config.max_age_s:
        raise CarryDataError(f"perpetual mark is stale ({age_s:.1f}s)")

    spot_mid = (bid + ask) / 2
    basis_bps = (mark / spot_mid - 1) * 10_000
    apr_7d, n7 = _window_apr(history, now_ms, 7)
    apr_30d, n30 = _window_apr(history, now_ms, 30)
    apr_90d, n90 = _window_apr(history, now_ms, 90)
    round_trip_cost = _round_trip_cost_rate(config)
    cost_apr = round_trip_cost * YEAR_DAYS / config.amortization_days
    portfolio_funding_apr = apr_90d * config.spot_fraction
    net_apr = portfolio_funding_apr - cost_apr
    hurdle = config.cash_benchmark_apr + config.safety_margin_apr
    current_rate = _finite(perp.get("lastFundingRate"), "lastFundingRate")

    checks = {
        "current_funding_positive": current_rate > 0,
        "funding_7d_positive": apr_7d > 0,
        "funding_30d_positive": apr_30d > 0,
        "funding_90d_positive": apr_90d > 0,
        "basis_bounded": abs(basis_bps) <= config.max_basis_bps,
        "beats_cash_hurdle_after_cost": net_apr > hurdle,
        "history_complete_enough": n90 >= 240,  # about 90% of the normal 8h schedule
    }
    return {
        "format": "zb-carry01-scan/1",
        "sources": {"spot": SPOT_API, "perpetual": FUTURES_API},
        "symbol": symbol,
        "observed_ms": now_ms,
        "eligible_shadow": all(checks.values()),
        "checks": checks,
        "spot_mid": spot_mid,
        "perp_mark": mark,
        "basis_bps": basis_bps,
        "current_funding_rate": current_rate,
        "funding_leg_apr": {"7d": apr_7d, "30d": apr_30d, "90d": apr_90d},
        "event_count": {"7d": n7, "30d": n30, "90d": n90},
        "portfolio_funding_apr_90d": portfolio_funding_apr,
        "round_trip_cost_rate": round_trip_cost,
        "amortized_cost_apr": cost_apr,
        "net_apr_90d_proxy": net_apr,
        "cash_hurdle_apr": hurdle,
        "mark_age_s": age_s,
        "config": asdict(config),
    }


def scan_default_pairs(*, config: CarryConfig | None = None) -> dict[str, Any]:
    """Compare BTC/ETH across USDT and USDC without any account access."""
    config = config or CarryConfig()
    now_ms = int(time.time() * 1000)
    session = requests.Session()
    results, errors = [], {}
    for symbol in ("BTCUSDT", "ETHUSDT", "BTCUSDC", "ETHUSDC"):
        try:
            results.append(scan_symbol(symbol, now_ms=now_ms, config=config, session=session))
        except (CarryDataError, requests.RequestException, KeyError) as exc:
            errors[symbol] = f"{type(exc).__name__}: {exc}"
    return {"format": "zb-carry01-basket-scan/1", "observed_ms": now_ms,
            "mode": "public-read-only", "results": results,
            "errors": errors, "all_four_measured": len(results) == 4}


def _fill_cost(notional: float, fee_bps: float, slippage_bps: float) -> float:
    return abs(notional) * (fee_bps + slippage_bps) / 10_000


def replay(rows: Iterable[dict[str, Any]], config: CarryConfig | None = None) -> dict[str, Any]:
    """Replay one matched long-spot/short-perpetual book in timestamp order.

    Each row needs ``timestamp_ms``, ``spot_price`` and ``perp_price``.  A settled
    event has ``funding_rate``; rows without one are mark observations.  Funding is
    applied only on that row, never forward-filled.  Rebalances close and reopen
    both legs when estimated futures leverage leaves the frozen 2x..5x band.
    """
    config = config or CarryConfig()
    config.validate()
    data = list(rows)
    if len(data) < 2:
        raise CarryDataError("replay needs at least two observations")

    parsed = []
    previous = -1
    for row in data:
        ts = row.get("timestamp_ms")
        if isinstance(ts, bool) or not isinstance(ts, int):
            raise CarryDataError("timestamp_ms must be an integer")
        if ts <= previous:
            raise CarryDataError("timestamps must be unique and strictly increasing")
        previous = ts
        spot = _finite(row.get("spot_price"), "spot_price", positive=True)
        perp = _finite(row.get("perp_price"), "perp_price", positive=True)
        funding = None if row.get("funding_rate") in (None, "") else _finite(row["funding_rate"], "funding_rate")
        parsed.append((ts, spot, perp, funding))

    def both_leg_cost(qty: float, spot: float, perp: float) -> float:
        return (_fill_cost(qty * spot, config.spot_fee_bps, config.spot_slippage_bps)
                + _fill_cost(qty * perp, config.perp_fee_bps, config.perp_slippage_bps))

    first_ts, entry_spot, entry_perp, first_funding = parsed[0]
    if first_funding is not None:
        raise CarryDataError("the entry observation cannot also credit funding; funding must settle after entry")
    qty = config.capital * config.spot_fraction / entry_spot
    total_cost = both_leg_cost(qty, entry_spot, entry_perp)
    segment_equity = config.capital - total_cost
    segment_funding = 0.0
    funding_total = 0.0
    price_pnl_total = 0.0
    rebalances = 0
    funding_events = 0
    curve = []
    liquidated = False

    for ts, spot, perp, funding in parsed:
        if funding is not None:
            received = qty * perp * funding
            segment_funding += received
            funding_total += received
            funding_events += 1
        price_pnl = qty * (spot - entry_spot) + qty * (entry_perp - perp)
        equity = segment_equity + price_pnl + segment_funding
        futures_collateral = (segment_equity * (1 - config.spot_fraction)
                              + qty * (entry_perp - perp) + segment_funding)
        short_notional = abs(qty * perp)
        maintenance = short_notional * config.maintenance_margin_rate
        leverage = math.inf if futures_collateral <= 0 else short_notional / futures_collateral
        curve.append((ts, equity))
        if futures_collateral <= maintenance or equity <= 0:
            liquidated = True
            break
        if ts != parsed[-1][0] and not config.min_leverage <= leverage <= config.max_leverage:
            close_cost = both_leg_cost(qty, spot, perp)
            total_cost += close_cost
            price_pnl_total += price_pnl
            equity -= close_cost
            new_qty = equity * config.spot_fraction / spot
            open_cost = both_leg_cost(new_qty, spot, perp)
            total_cost += open_cost
            segment_equity = equity - open_cost
            qty, entry_spot, entry_perp, segment_funding = new_qty, spot, perp, 0.0
            rebalances += 1

    last_ts, last_spot, last_perp, _ = parsed[min(len(curve), len(parsed)) - 1]
    final_price_pnl = qty * (last_spot - entry_spot) + qty * (entry_perp - last_perp)
    price_pnl_total += final_price_pnl
    gross_end = segment_equity + final_price_pnl + segment_funding
    exit_cost = 0.0 if liquidated else both_leg_cost(qty, last_spot, last_perp)
    total_cost += exit_cost
    marked_ending_equity = max(0.0, gross_end - exit_cost)
    curve.append((last_ts, marked_ending_equity))
    peak, max_dd = 0.0, 0.0
    for _, equity in curve:
        peak = max(peak, equity)
        if peak > 0:
            max_dd = min(max_dd, equity / peak - 1)
    elapsed_days = max((last_ts - first_ts) / DAY_MS, 1 / 24)
    complete = not liquidated
    total_return = marked_ending_equity / config.capital - 1 if complete else None
    annualized = None
    if complete and elapsed_days >= config.min_annualize_days:
        annualized = ((marked_ending_equity / config.capital) ** (YEAR_DAYS / elapsed_days) - 1
                      if marked_ending_equity > 0 else -1.0)
    hurdle = config.cash_benchmark_apr + config.safety_margin_apr
    return {
        "format": "zb-carry01-replay/1",
        "status": "LIQUIDATED" if liquidated else "COMPLETE",
        "observations": len(curve) - 1,
        "funding_events": funding_events,
        "rebalances": rebalances,
        "elapsed_days": elapsed_days,
        "starting_equity": config.capital,
        "ending_equity": marked_ending_equity if complete else None,
        "last_marked_equity": marked_ending_equity,
        "total_return": total_return,
        "annualized_return": annualized,
        "max_drawdown": max_dd,
        "funding_pnl": funding_total,
        "price_leg_pnl": price_pnl_total,
        "trading_cost": total_cost,
        "cash_hurdle_apr": hurdle,
        "beats_cash_hurdle": annualized is not None and annualized > hurdle,
        "config": asdict(config),
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if not rows:
        raise CarryDataError("input JSONL is empty")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CARRY-01 public scanner / frozen-data replay")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="read BTC/ETH USDT/USDC public endpoints")
    replay_parser = sub.add_parser("replay", help="replay a frozen point-in-time JSONL")
    replay_parser.add_argument("input", type=Path)
    args = parser.parse_args(argv)
    if args.command == "scan":
        result = scan_default_pairs()
    else:
        result = replay(_read_jsonl(args.input))
        result["input_sha256"] = hashlib.sha256(args.input.read_bytes()).hexdigest()
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
