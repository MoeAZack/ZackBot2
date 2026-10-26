"""Frozen EDGE-04 Stage-A development/walk-forward runner (holdout is unreachable)."""
from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C  # noqa: E402
import edge04_1m_acquire as E1  # noqa: E402
import edge04_orchestrate as O  # noqa: E402
import edge04_pit as EP  # noqa: E402
import ledger as L  # noqa: E402
import manifest as M  # noqa: E402
import pit as P  # noqa: E402
import pit_run as PR  # noqa: E402
import splits as S  # noqa: E402

FORMAT = "zb-edge04-stage-a/1"
VARIANTS = (
    ("H1.primary", "H1", None, "primary"), ("H1.any_ablation", "H1", "any", "diagnostic"),
    ("H2.primary_k5", "H2", 5, "primary"), ("H2.k3", "H2", 3, "diagnostic"),
    ("H2.k7", "H2", 7, "diagnostic"), ("H3.primary", "H3", None, "primary"),
    ("H4.primary", "H4", None, "primary"),
)
PRIMARY = ("H1.primary", "H2.primary_k5", "H3.primary", "H4.primary")


class StageAError(ValueError):
    pass


def sha_bytes(raw): return hashlib.sha256(raw).hexdigest()
def sha_doc(doc): return sha_bytes(M.canonical(doc))


def read_json(path):
    with open(path, encoding="utf-8") as f: return json.load(f)


def write_once(path, doc):
    EP.write_once(path, doc)
    return sha_bytes(open(path, "rb").read())


def exact_head(repo, expected):
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True)
    if head != expected or dirty:
        raise StageAError(f"exact clean head required: expected {expected}, got {head}, dirty={bool(dirty)}")
    return head


def variant_outputs(outputs, variant, first, last):
    name, hypothesis, override, _ = variant
    out = copy.deepcopy(list(outputs))
    for cycle in out:
        t = cycle["t"]
        for rec in cycle["r"]:
            for h, q in rec.get("q", {}).items():
                if h != hypothesis or not first <= t <= last:
                    q["e"] = False
            q = rec.get("q", {}).get(hypothesis)
            if not q:
                continue
            if name == "H1.any_ablation":
                q["e"] = bool(q.get("raw_e")) and first <= t <= last
            elif hypothesis == "H2":
                k = int(override)
                now = q.get("rank") is not None and q["rank"] <= k and q.get("above_ema50")
                prev = q.get("prev_rank") is not None and q["prev_rank"] <= k and q.get("prev_above_ema50")
                q["e"] = bool(now and not prev and first <= t <= last)
                q["x"] = bool(q.get("rank") is None or q["rank"] > k + 3 or not q.get("above_ema50"))
    return out


def account_summary(book):
    accepted = book.get("accepted", [])
    done = [x for x in accepted if x.get("status") == PR.COMPLETE]
    pnl = [float(x["pnl"]) for x in done]
    rs = [float(x["r"]) for x in done]
    neg = -math.fsum(x for x in pnl if x < 0)
    return {"status": book.get("status", PR.INCOMPLETE), "episodes": len(accepted),
            "complete_episodes": len(done), "wallet_end": book.get("wallet_end"),
            "net_pnl": math.fsum(pnl), "mean_net_r": math.fsum(rs) / len(rs) if rs else None,
            "profit_factor": math.fsum(x for x in pnl if x > 0) / neg if neg else None,
            "incomplete": dict(collections.Counter(x.get("reason") for x in accepted
                                                     if x.get("status") != PR.COMPLETE)),
            "refusals": dict(collections.Counter(x["reason"] for x in book.get("refused", [])))}


def incomplete_row(reason):
    """Stable expected-failure row: preserve evidence and never convert a missing input to a process crash."""
    return {"summary":{"status":PR.INCOMPLETE,"episodes":0,"complete_episodes":0,"wallet_end":None,
                       "net_pnl":None,"mean_net_r":None,"profit_factor":None,
                       "incomplete":{"orchestration":1},"refusals":{},"reason":str(reason)},
            "book":{"format":O.FORMAT,"status":PR.INCOMPLETE,"wallet_end":None,"accepted":[],"refused":[],
                    "failure":{"class":type(reason).__name__,"reason":str(reason)}}}


def orchestrate_row(outputs, hypothesis, *, rules, slip_c, scenario, ds, hi):
    try:
        book=O.orchestrate(outputs,hypothesis,rules=rules,slip_c=slip_c,scenario=scenario,
                           minute_bars=ds,observation_end_ms=hi)
        return {"summary":account_summary(book),"book":book}
    except (O.OrchestrationError,PR.PitRunError,C.CostError) as e:
        return incomplete_row(e)


def b1(outputs, hypothesis, symbols, first, hi, rules, slip_c, scenario, ds):
    """Equal-weight side-matched passive, with exact opens and the same costs."""
    _, syms = PR.read_tape(outputs)
    side = "short" if hypothesis == "H1" else "long"
    rows, equity = [], 10_000.0
    for symbol in symbols:
        x = syms.get(symbol)
        try:
            if x is None or first not in x.rec or hi not in x.rec or x.rec[first].get("n") is None or x.rec[hi].get("n") is None:
                raise PR.Incomplete("incomplete.executable_open")
            ent_ref = x.rec[first]["n"]
            if scenario.name == "entry_latency_60s":
                ent_ref = O.latency_from_dataset(ds, symbol, first)
            action_in, action_out = (("sell", "buy") if side == "short" else ("buy", "sell"))
            tick = rules[symbol]["tick"]
            ent = C.fill_price(ent_ref, action_in, x.bps(first, scenario.vol, slip_c), scenario, tick=tick)
            ext = C.fill_price(x.rec[hi]["n"], action_out, x.bps(hi, scenario.vol, slip_c), scenario, tick=tick)
            qty = (10_000.0 / len(symbols)) / ent
            fees = C.fee(ent * qty, "taker", C.CRYPTO, scenario) + C.fee(ext * qty, "taker", C.CRYPTO, scenario)
            funding = C.funding_cost(side, qty, x.events(first, hi), scenario, entry_notional=qty * ent)
            gross = (ent - ext if side == "short" else ext - ent) * qty
            rows.append({"symbol":symbol, "status":PR.COMPLETE, "pnl":gross-fees-funding})
            equity += gross - fees - funding
        except (PR.Incomplete, O.OrchestrationError) as e:
            rows.append({"symbol":symbol, "status":PR.INCOMPLETE, "reason":getattr(e,"reason",str(e))})
    return {"status":PR.COMPLETE if all(x["status"] == PR.COMPLETE for x in rows) else PR.INCOMPLETE,
            "wallet_end":equity, "rows":rows}


def b0(outputs, hypothesis, primary_book, key, first, last, hi, rules, slip_c, ds):
    """10,000 deterministic matched random-entry shared books.

    Each completed primary episode contributes one same-symbol/side/holding-duration/risk-shape template.
    Random entries are without replacement. The normal shared wallet enforces the identical capacity gates.
    """
    if primary_book.get("status") != PR.COMPLETE:
        return {"status":PR.INCOMPLETE,"reason":"primary book incomplete; matched B0 is undefined",
                "books":O.B0_BOOKS,"valid_books":0}
    templates=[]
    for x in primary_book.get("accepted",[]):
        if x.get("status") != PR.COMPLETE:
            return {"status":PR.INCOMPLETE,"reason":"primary episode incomplete","books":O.B0_BOOKS,"valid_books":0}
        hold=(x["exit_ms"]-x["entry_ms"])//PR.TF
        if hold < 1:
            return {"status":PR.INCOMPLETE,"reason":"non-positive holding duration","books":O.B0_BOOKS,"valid_books":0}
        templates.append({"symbol":x["symbol"],"side":x["side"],"hold":hold,
                          "risk_frac":abs(x["entry_price"]-x["initial_stop"])/x["entry_price"]})
    _,syms=PR.read_tape(outputs)
    pools={}
    for tp in templates:
        pk=(tp["symbol"],tp["hold"])
        if pk not in pools:
            sx=syms[tp["symbol"]]
            pools[pk]=[t for t in range(first,last+1,PR.TF) if t in sx.rec and sx.rec[t].get("n") is not None
                       and t+tp["hold"]*PR.TF <= hi and t+tp["hold"]*PR.TF in sx.rec
                       and sx.rec[t+tp["hold"]*PR.TF].get("n") is not None]
    per_stress={}
    for stress,scenario in O.STRESS.items():
        vals=[];invalid=0
        for index in range(O.B0_BOOKS):
            rng=random.Random(O.seed("edge04_legacy_reeval.v1",hypothesis,key,index)); used=set(); cand=[]
            try:
                for tp in templates:
                    pool=[t for t in pools[(tp["symbol"],tp["hold"])] if (tp["symbol"],t) not in used]
                    if not pool: raise StageAError("B0 unmatched template")
                    e=pool[rng.randrange(len(pool))];used.add((tp["symbol"],e));sx=syms[tp["symbol"]]
                    ref=sx.rec[e]["n"]
                    if scenario.name=="entry_latency_60s": ref=O.latency_from_dataset(ds,tp["symbol"],e)
                    action="sell" if tp["side"]=="short" else "buy";rule=rules[tp["symbol"]]
                    fill=C.fill_price(ref,action,sx.bps(e,scenario.vol,slip_c),scenario,tick=rule["tick"])
                    cand.append({"symbol":tp["symbol"],"side":tp["side"],"entry_ms":e,"entry_price":fill,
                                 "signal_close":sx.rec[e]["b"][3],"risk_u":fill*tp["risk_frac"],"rule":rule,
                                 "x":sx,"exit_ms":e+tp["hold"]*PR.TF})
                times={c["entry_ms"] for c in cand}
                for c in cand: c["marks"]={t:c["x"].rec[t]["b"][3] for t in times if t in c["x"].rec}
                def replay(c,qty):
                    sx=c["x"];e=c["entry_ms"];z=c["exit_ms"];short=c["side"]=="short"
                    action="buy" if short else "sell"
                    px=C.fill_price(sx.rec[z]["n"],action,sx.bps(z,scenario.vol,slip_c),scenario,
                                    tick=c["rule"]["tick"])
                    entry_fee=C.fee(c["entry_price"]*qty,"taker",C.CRYPTO,scenario)
                    exit_fee=C.fee(px*qty,"taker",C.CRYPTO,scenario)
                    events=[{"at_ms":e,"delta":-entry_fee,"kind":"entry_fee","terminal":False}]
                    funding=0.0
                    for event in sx.events(e,z):
                        cost=C.funding_cost(c["side"],qty,[event],scenario,entry_notional=qty*c["entry_price"])
                        funding+=cost;events.append({"at_ms":event[0],"delta":-cost,"kind":"funding","terminal":False})
                    gross=((c["entry_price"]-px) if short else (px-c["entry_price"]))*qty
                    pnl=gross-entry_fee-exit_fee-funding
                    events.append({"at_ms":z,"delta":gross-exit_fee,"kind":"exit","terminal":True})
                    return {"status":PR.COMPLETE,"symbol":c["symbol"],"entry_ms":e,"exit_ms":z,"pnl":pnl,
                            "r":pnl/(qty*c["risk_u"]),"wallet_events":events}
                book=O.route_shared(cand,replay)
                if len(book["accepted"]) != len(templates) or book["status"] != PR.COMPLETE:
                    invalid+=1
                else: vals.append(book["wallet_end"]-10_000.0)
            except (PR.Incomplete,O.OrchestrationError,StageAError,C.CostError): invalid+=1
        per_stress[stress]={"valid_books":len(vals),"invalid_books":invalid,
                            "mean_net_pnl":math.fsum(vals)/len(vals) if vals else None,
                            "outcomes_digest":sha_doc(vals)}
    return {"status":PR.COMPLETE if all(x["valid_books"]==O.B0_BOOKS for x in per_stress.values()) else PR.INCOMPLETE,
            "books":O.B0_BOOKS,"stresses":per_stress}


def run_split(repo, outdir, key, ds, plan, ledger, rules, symbols, slip_c):
    lo, hi = EP.development_window(plan, key)
    first, last = plan.decision_range(key)
    packet_path = os.path.join(outdir, "tapes", key.replace("[", "-").replace("]", "") + ".json")
    if os.path.exists(packet_path):
        packet = read_json(packet_path); outputs = packet["outputs"]
    else:
        acc = P.Access(ds, plan, ledger, candidate_id="edge04_legacy_reeval.v1", author="codex",
                       cairo_date=datetime.now(ZoneInfo("Africa/Cairo")).strftime("%Y-%m-%d"), repo=repo)
        window = acc.open(key, lineage="root", detail={"purpose":"EDGE-04 Stage-A causal tape", "holdout":False})
        ev = P.evaluate(window, EP.EVALUATOR, EP.FUNCTION, range(lo + PR.TF, hi + 1, PR.TF),
                        summary="summarize", perturb=True)
        packet = {"format":"zb-edge04-stage-a-tape/1", "split":key,
                  "attestation":ev.attestation, "attestation_digest":ev.attestation_digest,
                  "summary":ev.results, "outputs":list(ev)}
        os.makedirs(os.path.dirname(packet_path), exist_ok=True); write_once(packet_path, packet); outputs = list(ev)
    variants = {}
    for variant in VARIANTS:
        name, hypothesis, _, role = variant
        transformed = variant_outputs(outputs, variant, first, last)
        stresses = {}
        for stress, scenario in O.STRESS.items():
            stresses[stress] = orchestrate_row(transformed,hypothesis,rules=rules,slip_c=slip_c,
                                                scenario=scenario,ds=ds,hi=hi)
        variants[name] = {"hypothesis":hypothesis, "role":role, "stresses":stresses}
    baselines = {}
    for name in PRIMARY:
        variant = next(v for v in VARIANTS if v[0] == name); hypothesis = variant[1]
        transformed = variant_outputs(outputs, variant, first, last)
        primary_book=variants[name]["stresses"]["base"]["book"]
        b1_rows={}
        for stress,scenario in O.STRESS.items():
            try: b1_rows[stress]=b1(transformed,hypothesis,symbols,first,hi,rules,slip_c,scenario,ds)
            except (O.OrchestrationError,PR.PitRunError,C.CostError,PR.Incomplete) as e:
                b1_rows[stress]={"status":PR.INCOMPLETE,"wallet_end":None,"rows":[],"reason":str(e)}
        baselines[hypothesis] = {"B1": b1_rows,
                                 "B0": b0(transformed,hypothesis,primary_book,key,first,last,hi,rules,slip_c,ds)}
    result = {"format":"zb-edge04-stage-a-split/1", "split":key, "window":plan.window(key),
              "decision_range":{"first":first,"last":last}, "tape_attestation":packet["attestation_digest"],
              "variants":variants, "baselines":baselines}
    path = os.path.join(outdir,"splits",key.replace("[","-").replace("]","")+".json")
    os.makedirs(os.path.dirname(path),exist_ok=True); write_once(path,result)
    return result


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo",required=True); ap.add_argument("--store",required=True)
    ap.add_argument("--overlay-store",required=True); ap.add_argument("--overlay-manifest",required=True)
    ap.add_argument("--out",required=True); ap.add_argument("--expected-head",required=True)
    a=ap.parse_args(argv); repo=os.path.abspath(a.repo); out=os.path.abspath(a.out)
    head=exact_head(repo,a.expected_head)
    os.makedirs(out,exist_ok=True)
    lock=os.path.join(out,"RUNNING.lock")
    try: fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY); os.write(fd,str(os.getpid()).encode()); os.close(fd)
    except FileExistsError: raise StageAError("output already has an active/uncleared run lock")
    try:
        manifest=M.load(os.path.join(repo,"research_evidence/manifests/binance-um-archive-v1.json.gz"))
        overlay=E1.load_overlay_manifest(a.overlay_manifest)
        static=read_json(os.path.join(repo,"research_evidence/universe/static-top40-minhist365-contract-20261011.json"))
        plan=EP.plan(repo); symbols=EP.contract(repo)["universe"]["headline"]
        ds=EP.dataset(manifest,a.store,static,repo=repo,minute_overlay=overlay,minute_overlay_store=a.overlay_store)
        with open(os.path.join(repo,PR.RULES_PATH),"rb") as f: raw_rules=f.read()
        rules=PR.load_rules(raw_rules,symbols)
        source_ledger_dir=os.path.join(repo,"research_evidence/ledger")
        src_ledger=os.path.join(source_ledger_dir,"edge04_legacy_reeval.jsonl")
        ledger_dir=os.path.join(out,"ledger")
        if not os.path.exists(ledger_dir):
            shutil.copytree(source_ledger_dir,ledger_dir)
        ledger=os.path.join(ledger_dir,"edge04_legacy_reeval.jsonl")
        identity={"format":FORMAT,"status":"RUNNING","head":head,"binding":EP.binding(repo,overlay),
                  "manifest_digest":manifest["digest"],"overlay_digest":overlay["digest"],
                  "split_plan_digest":plan.digest,"ledger_initial_sha256":sha_bytes(open(src_ledger,"rb").read()),
                  "variants":[v[0] for v in VARIANTS],"stresses":list(O.STRESS),"B0_books":O.B0_BOOKS,
                  "holdout_opened":False}
        identity["identity_digest"]=sha_doc(identity)
        write_once(os.path.join(out,"run-identity.json"),identity)
        results=[]
        for key in PR.dev_keys(plan):
            results.append(run_split(repo,out,key,ds,plan,ledger,rules,symbols,0.05))
        incomplete = any(b["status"] != PR.COMPLETE for r in results for v in r["variants"].values()
                         for b in [v["stresses"]["base"]["summary"]])
        incomplete = incomplete or any(base["B0"]["status"] != PR.COMPLETE or
                                       any(x["status"] != PR.COMPLETE for x in base["B1"].values())
                                       for r in results for base in r["baselines"].values())
        final={**identity,"status":"INCOMPLETE" if incomplete else "COMPLETE",
               "splits":[r["split"] for r in results],"holdout_opened":False}
        final["packet_digest"]=sha_doc(final); write_once(os.path.join(out,"stage-a-packet.json"),final)
        return 3 if final["status"] == "INCOMPLETE" else 0
    finally:
        try: os.remove(lock)
        except FileNotFoundError: pass

if __name__=="__main__": raise SystemExit(main())
