"""Collect completed C1H cases; descriptive only, never a qualification verdict."""
import csv, hashlib, json, os, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def sha(p):
    h=hashlib.sha256()
    with open(p,"rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()

def main():
    rows=[]
    for p in sorted((ROOT/"results").glob("*/*/*/result.json")):
        d=json.loads(p.read_text(encoding="utf-8"))
        if not d.get("complete"): continue
        spec=d["spec"]
        fp=hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()
        if d.get("fingerprint") != fp: raise ValueError(f"Corrupt fingerprint: {p}")
        for rel,expected in spec["hashes"].items():
            if not (ROOT/rel).is_file() or sha(ROOT/rel)!=expected: raise ValueError(f"Input changed: {rel}")
        for name,expected in d["output_hashes"].items():
            if not (p.parent/name).is_file() or sha(p.parent/name)!=expected: raise ValueError(f"Output changed: {p.parent/name}")
        m=d["metrics"]; f=m.get("feasibility") or {}
        rows.append(dict(dataset=spec["dataset"],key=spec["key"],bps_per_side=spec["bps_per_side"],path=spec["path"],n=m["n"],avg_R=m["avg_R"],win_rate=m["win_rate"],profit_factor=m["profit_factor"],net_pnl_mark_to_market=m["net_pnl_mark_to_market"],net_return_mark_to_market=m["net_return_mark_to_market"],mark_to_market_max_drawdown=m["mark_to_market_max_drawdown"],closed_trade_cumulative_R_drawdown=m["closed_trade_cumulative_R_drawdown"],closes_per_calendar_week=m["closes_per_calendar_week"],median_holding_bars=m["median_holding_bars"],ex_best_coin_positive=m["ex_best_coin_positive"],liquidations=m["liquidations"],unknown_symbols=",".join(f.get("unknown_symbols",[])),result=str(p.relative_to(ROOT))))
    out=ROOT/"results"; out.mkdir(exist_ok=True)
    report={"completed_cases":len(rows),"qualification":"Not assessed; completion and independent gates required", "rows":rows}
    for name,write in [("summary.json",lambda f: json.dump(report,f,indent=2,allow_nan=False)),("summary.csv",lambda f: write_csv(f,rows))]:
        with tempfile.NamedTemporaryFile("w",encoding="utf-8",newline="",dir=out,delete=False) as f: write(f); tmp=f.name
        os.replace(tmp,out/name)
    print(f"Completed cases: {len(rows)}. Descriptive summary only; no qualification assessed.")

def write_csv(f,rows):
    fields=list(rows[0]) if rows else ["dataset","key","bps_per_side","path"]
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

if __name__=="__main__": main()
