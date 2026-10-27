"""Exact per-position management/accounting for the frozen EDGE-04 hypotheses.

Consumes only an already-attested ``pit_run.Sym``-compatible tape.  Portfolio admission,
sizing and exchange-rule availability remain the existing ``pit_run`` responsibilities.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C  # noqa: E402
import pit_run as PR  # noqa: E402

FORMAT = "zb-edge04-position-account/1"
CENSOR = "right_censored_runtime_no_cap"
MANAGEMENT = {
    "H1": {"side": "short", "cap": 60, "tp1_r": 1.0, "tp1_frac": 0.5, "tp_r": 3.0},
    "H2": {"side": "long", "cap": None, "observe": 180},
    "H3": {"side": "long", "cap": 180, "tp_r": 3.0},
    "H4": {"side": "long", "cap": None, "observe": 180},
}


class Edge04AccountError(ValueError):
    pass


def _floor(v, unit):
    return PR._floor(v, unit)


def _fill(ref, action, x, at, scen, slip_c, tick, *, gap=False):
    try:
        return C.fill_price(ref, action, x.bps(at, scen.vol, slip_c), scen, gap=gap, tick=tick)
    except PR.Incomplete as exc:
        raise Edge04AccountError(exc.reason)


def replay_position(x, hypothesis, *, entry_ref, entry_ms, qty, stop_distance, rule, slip_c,
                    signal_exit_times=(), row="base", scenario=None, observation_end_ms=None):
    """Replay one admitted position without inventing admission or an exit.

    ``signal_exit_times`` are closed-bar decision times; their market exits fill at the next bar open.
    The tape record keys are bar close times, exactly as in ``pit_run.Sym``.
    """
    if hypothesis not in MANAGEMENT:
        raise Edge04AccountError(f"unknown EDGE-04 hypothesis {hypothesis!r}")
    m = MANAGEMENT[hypothesis]
    side, short = m["side"], m["side"] == "short"
    try:
        qty, dist = float(qty), float(stop_distance)
        tick, step = float(rule["tick"]), float(rule["step"])
    except (KeyError, TypeError, ValueError):
        raise Edge04AccountError("quantity, stop distance, tick and step must be numeric")
    if qty <= 0 or dist <= 0 or tick <= 0 or step <= 0:
        raise Edge04AccountError("quantity, stop distance, tick and step must be positive")
    scen = scenario if scenario is not None else C.STRESS[row]
    if not isinstance(scen, C.Scenario):
        raise Edge04AccountError("scenario must be a frozen costs.Scenario")
    action_in, action_out = ("sell", "buy") if short else ("buy", "sell")
    fill = _fill(entry_ref, action_in, x, entry_ms, scen, slip_c, tick)
    if short:
        sr = PR.short_stop(fill, Decimal(repr(dist)), tick)
        if sr is None:
            raise Edge04AccountError("invalid short stop risk")
        stop, risk_u = sr
    else:
        stop = _floor(fill - dist, tick)
        risk_u = fill - stop
        if stop <= 0 or risk_u <= 0:
            raise Edge04AccountError("invalid long stop risk")
    initial_qty, remaining = qty, qty
    initial_risk = initial_qty * risk_u
    fees = C.fee(fill * qty, "taker", C.CRYPTO, scen)
    funding = gross = 0.0
    partial_done = False
    fills = [{"kind": "entry", "at_ms": entry_ms, "qty": qty, "price": fill, "fee": fees}]
    wallet_events = [{"at_ms": entry_ms, "delta": -fees, "kind": "entry_fee", "terminal": False}]
    exits = set(signal_exit_times)

    def finish(ref, at_ms, why, *, bps_at, gap=False, amount=None):
        nonlocal remaining, gross, fees
        q = remaining if amount is None else amount
        px = _fill(ref, action_out, x, bps_at, scen, slip_c, tick, gap=gap)
        fee = C.fee(px * q, "taker", C.CRYPTO, scen)
        slice_gross = ((fill - px) if short else (px - fill)) * q
        gross += slice_gross
        fees += fee
        remaining = max(0.0, remaining - q)
        fills.append({"kind": why, "at_ms": at_ms, "qty": q, "price": px, "fee": fee, "gap": gap})
        wallet_events.append({"at_ms": at_ms, "delta": slice_gross - fee, "kind": why,
                              "terminal": remaining <= 1e-12})
        return px

    def complete(reason, at_ms):
        pnl = gross - fees - funding
        return {"format": FORMAT, "hypothesis": hypothesis, "side": side, "status": PR.COMPLETE,
                "reason": reason, "entry_ms": entry_ms, "exit_ms": at_ms, "entry_price": fill,
                "initial_stop": fill + risk_u if short else fill - risk_u, "stop": stop,
                "initial_qty": initial_qty, "open_qty": remaining, "partial_done": partial_done,
                "gross": gross, "fees": fees, "funding": funding, "pnl": pnl,
                "r": pnl / initial_risk, "fills": fills, "wallet_events": wallet_events}

    def incomplete(reason, at_ms):
        return {"format": FORMAT, "hypothesis": hypothesis, "side": side, "status": PR.INCOMPLETE,
                "reason": reason, "entry_ms": entry_ms, "first_unaccountable_ms": at_ms,
                "entry_price": fill, "initial_stop": fill + risk_u if short else fill - risk_u, "stop": stop,
                "initial_qty": initial_qty, "open_qty": remaining, "partial_done": partial_done,
                "gross_accounted": gross, "fees_accounted": fees, "funding_accounted": funding,
                "pnl": None, "r": None, "fills": fills,
                "wallet_events": wallet_events + [{"at_ms": at_ms, "delta": 0.0, "kind": reason,
                                                     "terminal": True}]}

    def charge(events, amount):
        nonlocal funding
        for event in events:
            cost = C.funding_cost(side, amount, [event], scen, bars_held=1, entry_notional=amount * fill)
            funding += cost
            wallet_events.append({"at_ms": event[0], "delta": -cost, "kind": "funding", "terminal": False})

    expected_t = entry_ms + PR.TF
    for t in sorted(x.rec):
        bo = t - PR.TF
        if bo < entry_ms:
            continue
        if t != expected_t:
            return incomplete("incomplete.gap", expected_t)
        expected_t += PR.TF
        if observation_end_ms is not None and bo >= observation_end_ms:
            return incomplete("incomplete.split_end", observation_end_ms)
        rec = x.rec[t]
        o, high, low, _ = rec["b"]
        bars_held = (bo - entry_ms) // PR.TF

        # Genuine signal exits take precedence at the open, as in the shared runner.
        if bo in exits:
            finish(o, bo, "exit.signal", bps_at=bo)
            return complete("exit.signal", bo)
        if m.get("cap") is not None and bars_held == m["cap"]:
            finish(o, bo, "exit.time_cap", bps_at=bo)
            return complete("exit.time_cap", bo)
        if m.get("observe") is not None and bars_held == m["observe"]:
            return incomplete(CENSOR, bo)

        stop_hit = (o >= stop or high >= stop) if short else (o <= stop or low <= stop)
        if stop_hit:
            ref, gap = C.stop_reference(side, stop, o)
            if not gap:
                try:
                    events = x.bar_events(entry_ms, bo, t)
                except PR.Incomplete as exc:
                    return incomplete(exc.reason, bo)
                charge(events, remaining)
            finish(ref, bo if gap else t, "exit.breakeven" if partial_done else "exit.stop",
                   bps_at=bo, gap=gap)
            return complete("exit.breakeven" if partial_done else "exit.stop", bo if gap else t)

        try:
            events = x.bar_events(entry_ms, bo, t)
        except PR.Incomplete as exc:
            return incomplete(exc.reason, bo)
        charge(events, remaining)

        # Stop-first was resolved above. Targets now execute in favourable price order.
        favourable = (lambda level: low <= level) if short else (lambda level: high >= level)
        if hypothesis == "H1" and not partial_done:
            tp1 = fill - risk_u if short else fill + risk_u
            if favourable(tp1):
                part = _floor(remaining * m["tp1_frac"], step)
                if part > 0:
                    finish(tp1, t, "exit.tp1", bps_at=bo, amount=part)
                    partial_done = True
                    stop = fill  # only an executed partial earns the breakeven transition
        target_r = m.get("tp_r")
        if target_r is not None and remaining > 0:
            target = fill - target_r * risk_u if short else fill + target_r * risk_u
            if favourable(target):
                finish(target, t, "exit.target", bps_at=bo)
                return complete("exit.target", t)

    # An uncapped strategy becomes administrative right-censor only when its 180-bar boundary was actually reached.
    end = observation_end_ms if observation_end_ms is not None else (
        max(x.rec) if x.rec else entry_ms)
    if m.get("observe") is not None and end >= entry_ms + m["observe"] * PR.TF:
        return incomplete(CENSOR, entry_ms + m["observe"] * PR.TF)
    return incomplete("incomplete.split_end", end)
