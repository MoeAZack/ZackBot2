"""Isolated C1H experiment: one independently simulated dataset/candidate/cost/path case."""
import argparse, gzip, hashlib, json, math, os, sys, tempfile
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "source"))
import backtest as B
import hourly_candidates as H


def digest(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""): h.update(b)
    return h.hexdigest()


def atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
        json.dump(obj, f, indent=2, allow_nan=False, default=str); f.write("\n"); tmp = f.name
    os.replace(tmp, path)


def finite(x):
    x = float(x)
    return x if math.isfinite(x) else None


def load_panel(folder):
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".csv", ".parquet"))
    if not files: raise ValueError(f"No data files in {folder}")
    panel = {}
    for p in files:
        d = pd.read_csv(p) if p.suffix.lower() == ".csv" else pd.read_parquet(p)
        d.columns = [str(c).lower() for c in d.columns]
        aliases = {"timestamp":"t", "open":"o", "high":"h", "low":"l", "close":"c", "volume":"v", "quote_volume":"qv"}
        d = d.rename(columns={k:v for k,v in aliases.items() if k in d.columns and v not in d.columns})
        if not set("tohlcv").issubset(d.columns): raise ValueError(f"Missing OHLCV in {p}")
        if pd.api.types.is_numeric_dtype(d.t):
            unit = "ms" if d.t.abs().median() > 1e11 else "s"
            d["t"] = pd.to_datetime(d.t, unit=unit, utc=True).dt.tz_localize(None)
        else: d["t"] = pd.to_datetime(d.t, utc=True).dt.tz_localize(None)
        for col in "ohlcv": d[col] = pd.to_numeric(d[col], errors="raise")
        if d.t.isna().any() or d.t.duplicated().any() or not d.t.is_monotonic_increasing: raise ValueError(f"Invalid, duplicate, or unsorted times: {p}")
        if not np.isfinite(d[list("ohlcv")].to_numpy()).all(): raise ValueError(f"Non-finite candles: {p}")
        if (d[list("ohlc")] <= 0).any().any() or (d.v < 0).any() or (d.h < d[["o","l","c"]].max(axis=1)).any() or (d.l > d[["o","h","c"]].min(axis=1)).any(): raise ValueError(f"Invalid OHLCV: {p}")
        symbol = p.stem.upper().split("_")[0].replace("-", "").replace("/", "")
        if symbol in panel: raise ValueError(f"Duplicate symbol {symbol}")
        panel[symbol] = d
    common = None
    for d in panel.values():
        idx = pd.DatetimeIndex(d.t)
        common = idx if common is None else common.intersection(idx)
    common = common.sort_values()
    if len(common) <= 220 or not (common.to_series().diff().dropna() == pd.Timedelta(hours=1)).all(): raise ValueError("Common index must be continuous 1h and exceed warmup")
    for s,d in panel.items():
        within = pd.DatetimeIndex(d.t[(d.t >= common[0]) & (d.t <= common[-1])])
        if not within.equals(common): raise ValueError(f"Unequal internal coverage: {s}")
    return panel, common, files


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--dataset", required=True, choices=["cohort9_1h","long8_1h"]); ap.add_argument("--key",required=True); ap.add_argument("--bps",required=True,type=int,choices=[0,7,15,23]); ap.add_argument("--path",required=True,choices=["path","worst"]); args=ap.parse_args()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.SetPriorityClass.restype = wintypes.BOOL
        if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00004000): raise ctypes.WinError(ctypes.get_last_error())
    if hasattr(H,"register"): H.register()
    if args.key not in H.CANDIDATES: raise ValueError("Unknown candidate key")
    panel, common, datafiles = load_panel(ROOT / "inputs" / args.dataset)
    H.set_panel(panel)
    rules_path = ROOT / "exchange_rules.json"
    preregs = sorted(p for p in ROOT.iterdir() if p.is_file() and "prereg" in p.name.lower())
    if not preregs: raise ValueError("Frozen preregistration missing")
    prereg=json.loads((ROOT / "preregistration.json").read_text(encoding="utf-8"))
    if digest(ROOT / "hourly_candidates.py") != prereg["candidate_module_sha256"] or digest(rules_path) != prereg["exchange_rules_sha256"]: raise ValueError("Frozen candidate or exchange rules changed")
    if digest(ROOT / "input_manifest.json") != prereg["data_manifest_sha256"]: raise ValueError("Frozen input manifest changed")
    for name,expected in prereg["source_sha256"].items():
        if digest(ROOT / "source" / name) != expected: raise ValueError(f"Frozen source changed: {name}")
    input_manifest=json.loads((ROOT / "input_manifest.json").read_text(encoding="utf-8"))[args.dataset]
    if set(panel)!=set(input_manifest): raise ValueError("Dataset symbols do not match frozen manifest")
    for p in datafiles:
        symbol=p.stem.upper().split("_")[0].replace("-","").replace("/","")
        if digest(p) != input_manifest[symbol]["input_sha256"]: raise ValueError(f"Frozen input changed: {p.name}")
    sources = sorted((ROOT / "source").glob("*.py")) + [ROOT / "hourly_candidates.py", Path(__file__).resolve(), ROOT / "summarize.py", rules_path, ROOT / "input_manifest.json"] + preregs + datafiles
    manifest = {str(p.relative_to(ROOT)):digest(p) for p in sources}
    spec = {"dataset":args.dataset,"key":args.key,"bps_per_side":args.bps,"path":args.path,"hashes":manifest,"schema":1}
    fingerprint = hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()
    out = ROOT / "results" / args.dataset / args.key / f"{args.bps}bps_{args.path}"
    result_path=out / "result.json"
    if result_path.exists():
        previous=json.loads(result_path.read_text(encoding="utf-8"))
        if previous.get("fingerprint") != fingerprint: raise ValueError("Existing case fingerprint mismatch; refuse reuse")
        for name, expected in previous["output_hashes"].items():
            if not (out/name).is_file() or digest(out/name) != expected: raise ValueError("Existing case output hash mismatch")
        print(f"Verified complete: {result_path}"); return
    out.mkdir(parents=True,exist_ok=True)
    B.FEE = max(0,(args.bps-2)/10000) if args.bps else 0.0
    B.SLIP = .0002 if args.bps else 0.0
    fund = .0001/8 if args.bps else 0.0
    book=B.Book(panel)
    if book.gaps or not pd.DatetimeIndex(book.t).equals(common): raise ValueError("Backtester alignment changed validated common index")
    fills=[]
    symbols=[s for s in book.syms if not (args.key.startswith("c1h_h6_btc_relative") and s.startswith("BTC"))]
    tr,cv=B.run(book,[dict(key=args.key,share=1/12,risk=.024,max_pos=1,symbols=symbols)],start=4000,max_lev=10,daily_halt=.03,warmup=220,fund_per_bar=fund,pessimistic=args.path,risk_rules={"open_risk_cap":{"mode":"enforce","pct":2.5}},exchange_rules=json.loads(rules_path.read_text(encoding="utf-8")),fill_log=fills)
    if cv.empty or not np.isfinite(cv.to_numpy()).all(): raise ValueError("Invalid equity curve")
    positions={}
    for f in fills:
        for k in ("risk","ref","qty"):
            if not math.isfinite(float(f[k])) or float(f[k]) <= 0: raise ValueError(f"Invalid fill {k}")
        ident=(f["sym"],int(f["pos_i"]),int(f["side"]))
        positions.setdefault(ident,[]).append(f)
        if f["kind"] not in ("entry","exit"): raise ValueError("Unexpected add/DCA fill")
    closed=set(); trade_cost_r=[]
    for row in tr.itertuples():
        ident=(row.sym,int(row.i_in),int(row.side))
        if ident in closed or ident not in positions: raise ValueError(f"Trade/fill mapping invalid: {ident}")
        closed.add(ident); fs=positions[ident]; entries=[f for f in fs if f["kind"]=="entry"]; exits=[f for f in fs if f["kind"]=="exit"]
        if len(entries)!=1 or not exits or not math.isclose(sum(f["qty"] for f in exits),entries[0]["qty"],rel_tol=1e-8,abs_tol=1e-10): raise ValueError(f"Closed quantity mismatch: {ident}")
        if not math.isclose(row.R,row.pnl/entries[0]["risk"],rel_tol=1e-8,abs_tol=1e-10): raise ValueError("Trade risk mismatch")
        trade_cost_r.append(sum(f["qty"]*f["ref"] for f in fs)*args.bps/10000/entries[0]["risk"])
    terminal=[]
    for ident,fs in positions.items():
        if ident in closed: continue
        entries=[f for f in fs if f["kind"]=="entry"]; exits=[f for f in fs if f["kind"]=="exit"]
        if len(entries)!=1: raise ValueError("Unmatched entry")
        remaining=entries[0]["qty"]-sum(f["qty"] for f in exits)
        if remaining<=0: raise ValueError("Unmatched closed position")
        terminal.append(dict(sym=ident[0],pos_i=ident[1],side=ident[2],remaining_qty=remaining))
    if len(tr):
        tr["entry_time_utc"]=pd.to_datetime(book.t.iloc[tr.i_in.to_numpy()]).to_numpy(); tr["exit_time_utc"]=pd.to_datetime(book.t.iloc[tr.i_out.to_numpy()]).to_numpy()
        tr["holding_bars"]=tr.i_out-tr.i_in
        tr["year"]=pd.to_datetime(tr.exit_time_utc).dt.year.astype(str)
        tr["halfyear"]=tr.year+"-H"+(((pd.to_datetime(tr.exit_time_utc).dt.month-1)//6)+1).astype(str)
        tr["period_dev"]=np.where(pd.to_datetime(tr.exit_time_utc)<pd.Timestamp("2025-01-01"),"earlier_consumed_dev","2025_and_later_consumed_dev")
    tr.to_csv(out/"trades.csv",index=False)
    cv.rename("equity_mark_to_market").to_csv(out/"curve.csv",index_label="timestamp_utc")
    with gzip.open(out/"fills.json.gz","wt",encoding="utf-8") as f: json.dump(fills,f,allow_nan=False)
    n=len(tr); rs=tr.R if n else pd.Series(dtype=float); pnl=tr.pnl if n else pd.Series(dtype=float)
    wins=float(pnl[pnl>0].sum()); losses=float(-pnl[pnl<0].sum())
    cum=rs.cumsum(); closed_dd=float((cum.cummax().clip(lower=0)-cum).max()) if n else 0.0
    baseline=pd.concat([pd.Series([4000.]),pd.Series(cv.to_numpy())],ignore_index=True); mark_dd=float((1-baseline/baseline.cummax()).max())
    group=lambda col: {str(k):finite(v) for k,v in tr.groupby(col).R.sum().items()} if n else {}
    coins=group("sym"); best=max(coins,key=coins.get) if coins else None
    duration=(common[-1]-common[220]).total_seconds()/604800
    def stats_by(col):
        if not n: return {}
        return {str(k):dict(n=int(len(g)),mean_R=finite(g.R.mean()),sum_R=finite(g.R.sum())) for k,g in tr.groupby(col)}
    underwater=baseline < baseline.cummax()
    runs=underwater.groupby((underwater != underwater.shift()).cumsum()).cumsum()
    duration_bars=int(runs.max()) if len(runs) else 0
    exbest=tr[tr.sym != best] if n else tr
    metrics=dict(economic_window_complete=(len(cv)==len(common)-220 and cv.attrs.get("liquidations",0)==0),economic_window_invalid_reason=("truncated equity curve or liquidation" if len(cv)!=len(common)-220 or cv.attrs.get("liquidations",0) else None),expected_evaluation_bars=len(common)-220,actual_evaluation_bars=len(cv),median_direct_fill_cost_R=finite(np.median(trade_cost_r)) if trade_cost_r else None,split_stats=stats_by("period_dev"),halfyear_stats=stats_by("halfyear"),ex_best_coin_n=len(exbest),ex_best_coin_mean_R=finite(exbest.R.mean()) if len(exbest) else None,mark_to_market_max_underwater_duration_bars=duration_bars,n=n,avg_R=finite(rs.mean()) if n else None,win_rate=finite((pnl>0).mean()) if n else None,profit_factor=finite(wins/losses) if losses else None,profit_factor_no_losses=bool(n and not losses),closed_trade_pnl=float(pnl.sum()),net_pnl_mark_to_market=float(cv.iloc[-1]-4000),net_return_mark_to_market=float(cv.iloc[-1]/4000-1),mark_to_market_max_drawdown=mark_dd,closed_trade_cumulative_R_drawdown=closed_dd,sum_R_by_coin=coins,sum_R_by_year=group("year"),sum_R_by_halfyear=group("halfyear"),sum_R_by_side=group("side"),sum_R_by_consumed_development_period=group("period_dev"),closes_per_calendar_week=n/duration if duration else None,median_holding_bars=finite(tr.holding_bars.median()) if n else None,ex_best_coin_sum_R=(sum(coins.values())-coins[best]) if best else None,ex_best_coin_positive=(sum(coins.values())-coins[best]>0) if best else None,best_coin=best,terminal_open_positions=terminal,liquidations=cv.attrs.get("liquidations"),feasibility=cv.attrs.get("feasibility"),blocked=cv.attrs.get("blocked"))
    coverage=dict(first_utc=str(common[0]),last_utc=str(common[-1]),common_bars=len(common),warmup_bars=220,by_symbol={s:dict(bars=len(d),first_utc=str(d.t.iloc[0]),last_utc=str(d.t.iloc[-1])) for s,d in panel.items()},internal_gaps=book.gaps)
    result=dict(complete=True,fingerprint=fingerprint,spec=spec,metrics=metrics,coverage=coverage,cost_label="GROSS_EX_FRICTION_AND_FUNDING" if not args.bps else "DIRECT_ENGINE_COST_PLUS_FUNDING_PROXY",funding_per_open_bar=fund,notes=["Risk compounds with account equity; initial nominal risk is $8, not fixed capital.","All date splits are consumed development data, not holdout.","Funding proxy is charged on both long and short positions.","Profit factor is undefined when there are no losses.","Closed R drawdown excludes open positions and uses cumulative R; marked drawdown includes open positions."] ,output_hashes={name:digest(out/name) for name in ("trades.csv","curve.csv","fills.json.gz")})
    atomic_json(result_path,result); print(f"Complete: {result_path}")

if __name__ == "__main__": main()
