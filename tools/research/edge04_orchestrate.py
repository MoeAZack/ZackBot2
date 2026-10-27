"""Frozen EDGE-04 orchestration utilities (no implicit data access)."""
from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import random

import costs as C
import edge04_account as A
import edge04_pit as EP
import pit_run as PR
import report as R

FORMAT = "zb-edge04-orchestration/1"
ADDENDUM = "docs/newcore/research/EDGE04_ORCHESTRATION_ADDENDUM_v1.json"
B0_BOOKS = 10_000
STRESS = {
    "base": C.Scenario("base"),
    "all_variable_costs_x1.5": C.Scenario("all_variable_costs_x1.5", fee_mult=1.5, slip_mult=1.5,
                                                   funding_mult=1.5),
    "fees_slippage_x2": C.Scenario("fees_slippage_x2", fee_mult=2.0, slip_mult=2.0),
    "funding_x2": C.Scenario("funding_x2", funding_mult=2.0),
    "gap_slippage_x5": C.Scenario("gap_slippage_x5", gap_slip_mult=5.0),
    "entry_latency_60s": C.Scenario("entry_latency_60s"),
}


class OrchestrationError(ValueError):
    pass


def addendum(repo):
    path = os.path.join(repo, ADDENDUM)
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("format") != "zb-edge04-orchestration-addendum/1" or doc.get("B0", {}).get(
            "books_per_primary") != B0_BOOKS or doc.get("boundaries", {}).get("market_data_opened") is not False:
        raise OrchestrationError("EDGE-04 orchestration addendum is absent or not frozen before data")
    return doc


def seed(candidate_id, hypothesis, split, index):
    if hypothesis not in ("H1", "H2", "H3", "H4") or type(index) is not int or not 0 <= index < B0_BOOKS:
        raise OrchestrationError("B0 seed needs a primary and book index 0..9999")
    raw = f"{candidate_id}|{hypothesis}|{split}|{index}".encode("ascii")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def b0_books(candidate_id, hypothesis, split, episodes, eligible):
    """10k deterministic matched plans. Entries are metadata only; execution uses the same book/cost path later."""
    groups = {}
    for row in eligible:
        groups.setdefault((row["symbol"], row["side"], row["holding_bars"]), []).append(row)
    out = []
    for i in range(B0_BOOKS):
        rng, used, picks = random.Random(seed(candidate_id, hypothesis, split, i)), set(), []
        for episode in episodes:
            key = (episode["symbol"], episode["side"], episode["holding_bars"])
            pool = [x for x in groups.get(key, ()) if (key, x["entry_ms"]) not in used]
            if not pool:
                raise OrchestrationError(f"B0 unmatched episode {key}; baseline is INCOMPLETE")
            pick = pool[rng.randrange(len(pool))]
            used.add((key, pick["entry_ms"]))
            picks.append(pick)
        out.append({"index": i, "seed": seed(candidate_id, hypothesis, split, i), "entries": picks})
    return out


def b1_plan(symbols, side, exact_opens, *, start_ms, end_ms):
    """Executable-open B1 plan; no interpolation or close-price proxy."""
    if side not in ("long", "short"):
        raise OrchestrationError("B1 side must be long or short")
    symbols = list(symbols)
    rows = []
    for symbol in symbols:
        opens = exact_opens.get(symbol, {})
        if start_ms not in opens or end_ms not in opens:
            raise OrchestrationError(f"B1 {symbol}: exact executable open missing; baseline is INCOMPLETE")
        rows.append({"symbol": symbol, "side": side, "weight": 1.0 / len(symbols),
                     "entry_ms": start_ms, "entry_ref": opens[start_ms],
                     "exit_ms": end_ms, "exit_ref": opens[end_ms]})
    return rows


def latency_60s(minute_bars, entry_ms):
    """Reference available exactly at +60s; 4h/15m rows cannot satisfy this shape."""
    rows = [b for b in minute_bars if getattr(b, "open_ms", None) == entry_ms]
    if len(rows) != 1 or rows[0].available_ms != entry_ms + 60_000:
        raise OrchestrationError("entry_latency_60s exact causal 1m coverage missing; row is INCOMPLETE")
    return float(rows[0].close)


def latency_from_dataset(dataset, symbol, entry_ms):
    bar = dataset.exact_minute_bar(symbol, entry_ms)
    return latency_60s(() if bar is None else (bar,), entry_ms)


def candidates_from_tape(outputs, hypothesis, *, rules, slip_c, scenario=None, minute_bars=None):
    """Translate only attested EDGE-04 tape bits into executable candidate inputs for the shared router."""
    if hypothesis not in A.MANAGEMENT:
        raise OrchestrationError(f"unknown primary {hypothesis!r}")
    scenario = scenario or STRESS["base"]
    tape = PR.read_tape(outputs)
    times, syms = tape
    cycles = {o["t"]: o for o in outputs}
    side = A.MANAGEMENT[hypothesis]["side"]
    exits = {s: {o["t"] for o in outputs for r in o["r"] if r.get("s") == s and r.get("q", {}).get(
        hypothesis, {}).get("x")} for s in syms}
    rows = []
    for t in times:
        for rec in cycles[t]["r"]:
            q = rec.get("q", {}).get(hypothesis)
            if not q or not q.get("e"):
                continue
            symbol, x = rec["s"], syms[rec["s"]]
            if symbol not in rules:
                raise OrchestrationError(f"{symbol}: pinned exchange rule missing; row is INCOMPLETE")
            nxt = x.rec.get(t + PR.TF)
            ref = nxt["b"][0] if nxt is not None else rec.get("n")
            if ref is None:
                raise OrchestrationError(f"{symbol} at {t}: next executable open missing; row is INCOMPLETE")
            if scenario.name == "entry_latency_60s":
                ref = latency_from_dataset(minute_bars, symbol, t) if hasattr(minute_bars, "exact_minute_bar") else \
                    latency_60s((minute_bars or {}).get(symbol, ()), t)
            dist = float(q["atr"])
            action = "sell" if side == "short" else "buy"
            fill = C.fill_price(ref, action, x.bps(t, scenario.vol, slip_c), scenario,
                                tick=rules[symbol]["tick"])
            if side == "short":
                sr = PR.short_stop(fill, dist, rules[symbol]["tick"])
                if sr is None:
                    raise OrchestrationError(f"{symbol}: invalid stop risk")
                risk_u = sr[1]
            else:
                stop = PR._floor(fill - dist, rules[symbol]["tick"])
                risk_u = fill - stop
            rows.append({"symbol": symbol, "side": side, "hypothesis": hypothesis, "entry_ms": t,
                         "entry_ref": ref, "entry_price": fill, "signal_close": rec["b"][3],
                         "risk_u": risk_u, "stop_distance": dist, "rule": rules[symbol],
                         "signal_exit_times": exits[symbol], "x": x, "scenario": scenario})
    admission_times = sorted({r["entry_ms"] for r in rows})
    for row in rows:
        row["marks"] = {t: row["x"].rec[t]["b"][3] for t in admission_times if t in row["x"].rec}
    return rows


def orchestrate(outputs, hypothesis, *, rules, slip_c, scenario=None, minute_bars=None, observation_end_ms=None):
    """End-to-end dry orchestration over an already-attested tape; it never opens a Dataset or Window."""
    rows = candidates_from_tape(outputs, hypothesis, rules=rules, slip_c=slip_c, scenario=scenario,
                                minute_bars=minute_bars)

    def managed(c, qty):
        return A.replay_position(c["x"], hypothesis, entry_ref=c["entry_ref"], entry_ms=c["entry_ms"], qty=qty,
                                 stop_distance=c["stop_distance"], rule=c["rule"], slip_c=slip_c,
                                 signal_exit_times=c["signal_exit_times"], scenario=c["scenario"],
                                 observation_end_ms=observation_end_ms)

    return route_shared(rows, managed)


def route_shared(candidates, replay, *, equity0=10_000.0, risk_fraction=0.01, max_positions=4,
                 max_leverage=3.0, gap_buffer=0.10, daily_loss=0.03):
    """Route chronologically through one shared wallet/capacity book.

    A candidate supplies symbol, entry_ms, entry_price, risk_u, signal_close and exchange rule. ``replay(candidate,
    qty)`` returns an exact managed episode with time-stamped ``wallet_events`` and a terminal/censor time.
    """
    wallet, active, events, accepted, refused, seq = float(equity0), {}, [], [], [], 0
    day = day_start = halted_day = None
    rows = sorted(candidates, key=lambda x: (x["entry_ms"], x["symbol"]))

    def drain(at):
        nonlocal wallet
        while events and events[0][0] <= at:
            _, _, delta, symbol, terminal = heapq.heappop(events)
            wallet += delta
            if terminal:
                active.pop(symbol, None)

    for at in sorted({r["entry_ms"] for r in rows}):
        drain(at)
        batch = [r for r in rows if r["entry_ms"] == at]
        marked = {}
        for symbol, pos in active.items():
            mark = pos["marks"].get(at)
            if mark is None:
                raise OrchestrationError(f"{symbol}: causal mark missing at admission cycle; book is INCOMPLETE")
            marked[symbol] = float(mark)
        equity = wallet + math.fsum((marked[s] - p["fill"]) * p["qty"] * (1 if p["side"] == "long" else -1)
                                    for s, p in active.items())
        now_day = PR.E.cairo_day(at)
        if now_day != day:
            day, day_start, halted_day = now_day, equity, None
        if halted_day != day and day_start > 0 and equity / day_start - 1.0 <= -daily_loss:
            halted_day = day
        snapshot = wallet
        open_notional = math.fsum(v["qty"] * marked[s] for s, v in active.items())
        for c in batch:
            symbol, rule = c["symbol"], c["rule"]
            if halted_day == day:
                refused.append({"symbol": symbol, "entry_ms": at, "reason": "risk.daily_loss"}); continue
            if symbol in active:
                refused.append({"symbol": symbol, "entry_ms": at, "reason": "capacity.one_per_symbol"}); continue
            if len(active) >= max_positions:
                refused.append({"symbol": symbol, "entry_ms": at, "reason": "capacity.max_positions"}); continue
            risk_u, fill, ref = float(c["risk_u"]), float(c["entry_price"]), float(c["signal_close"])
            room = max_leverage * snapshot - open_notional
            raw = snapshot * risk_fraction / risk_u
            qty = min(raw, max(room, 0.0) / (ref * (1.0 + gap_buffer)))
            qty = PR._floor(qty, rule["step"]) if qty > 0 else 0.0
            if qty <= 0 or qty < rule["min_qty"] or qty * fill < rule["min_notional"]:
                refused.append({"symbol": symbol, "entry_ms": at, "reason": "execution.size_min"}); continue
            episode = replay(c, qty)
            if episode.get("symbol", symbol) != symbol:
                raise OrchestrationError("managed episode symbol differs from its admitted candidate")
            terminal_at = episode.get("exit_ms", episode.get("first_unaccountable_ms"))
            if terminal_at is None or terminal_at < at:
                raise OrchestrationError("managed episode has no valid terminal/censor time")
            active[symbol] = {"qty": qty, "fill": fill, "side": c["side"], "marks": c["marks"],
                              "terminal_ms": terminal_at}
            open_notional += qty * fill
            for event in episode.get("wallet_events", ()):
                seq += 1
                heapq.heappush(events, (event["at_ms"], seq, event["delta"], symbol,
                                        event.get("terminal", False)))
            # The shared router owns admission identity. Management replays historically did
            # not repeat the symbol, so seal it here instead of making downstream consumers
            # infer it from event ordering.
            accepted.append(dict(episode, symbol=symbol, admitted_qty=qty, snapshot=snapshot))
    drain(2**63 - 1)
    return {"format": FORMAT, "wallet_end": wallet, "accepted": accepted, "refused": refused,
            "status": PR.COMPLETE if all(x.get("status") == PR.COMPLETE for x in accepted) else PR.INCOMPLETE}


def freeze_envelope(repo, runs_dir, *, eval_files, config, schedule, ident, exec_obs=None):
    """Use the repository's immutable /2 or /3 envelope and write-once store."""
    addendum(repo)
    hypothesis = config.get("hypothesis")
    seeds = [seed(ident["candidate_id"], hypothesis, ident["split"], i) for i in range(B0_BOOKS)]
    env = R.freeze_run(repo=repo, eval_files=eval_files, config=config, seeds=seeds,
                       entrypoint={"path": "tools/research/edge04_eval.py", "function": "tape",
                                   "summary": "summarize"}, schedule=schedule, exec_obs=exec_obs, **ident)
    R.write_envelope(runs_dir, env)
    return env


def write_report(path, report):
    """Strict write-once report artifact; report construction remains evidence/attestation gated by report.py."""
    return EP.write_once(path, report)


def gates(metrics):
    """Mechanical frozen primary gate. Diagnostics/stresses cannot substitute for a failed base primary."""
    failures = []
    need = lambda ok, name: failures.append(name) if not ok else None
    need(metrics.get("status") == PR.COMPLETE, "data_complete")
    need(metrics.get("episodes", 0) >= 60 and metrics.get("episodes_per_week", 0) >= 0.75, "activity")
    folds = metrics.get("walk_forward_net_r", [])
    need(len(folds) == 5 and sum(x > 0 for x in folds) >= 4 and metrics.get("mean_net_r", 0) > 0
         and metrics.get("profit_factor", 0) > 1.10, "walk_forward")
    need(metrics.get("mean_net_r_lcb95", 0) > 0 and metrics.get("gross_mean_r", 0) > 0, "expectancy")
    need(metrics.get("net_return", 0) > metrics.get("b1_net_return", 0)
         and metrics.get("candidate_minus_b0_lcb95", 0) > 0, "baselines")
    stress = metrics.get("stress", {})
    need(stress.get("all_variable_costs_x1.5_mean_r", 0) > 0
         and stress.get("entry_latency_60s_sign", 0) == 1, "stress")
    need(metrics.get("max_symbol_contribution", 1) <= 0.35
         and metrics.get("top3_symbol_contribution", 1) <= 0.60, "concentration")
    need(metrics.get("max_equity_dd", 1) < 0.25 and metrics.get("max_closed_r_dd", 99) < 25
         and stress.get("all_variable_costs_x1.5_max_equity_dd", 1) < 0.35, "drawdown")
    need(metrics.get("holm_pass") is True, "multiplicity")
    return {"pass": not failures, "failures": failures,
            "rule": "every base-primary gate must pass; diagnostics cannot rescue"}


def report_envelope(run_env, results, attestation_digest):
    """Content-addressed EDGE-04 report envelope; caller writes it through ``write_report``."""
    if not isinstance(attestation_digest, str) or len(attestation_digest) != 64:
        raise OrchestrationError("report needs the 64-hex PIT attestation digest")
    doc = {"format": "zb-edge04-report/1", "run_digest": run_env["run_digest"],
           "attestation_digest": attestation_digest, "results": results,
           "results_digest": hashlib.sha256(PR.canon(results)).hexdigest(), "gates": gates(results)}
    doc["report_digest"] = hashlib.sha256(PR.canon(doc)).hexdigest()
    return doc
