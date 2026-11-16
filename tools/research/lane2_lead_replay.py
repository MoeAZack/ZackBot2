"""Replay frozen LONGSCOUT leads offline. Research only; no exchange access."""
import argparse, gzip, hashlib, io, json, os, subprocess, sys, tarfile
from pathlib import Path

PIN = '4189908d08c559421bdfd270c02de3f8dd6d97e9'
ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / '.git' / 'lead_replay_source'
CACHE = ROOT / 'research_cache' / 'lane2' / 'replay'
SHARED = Path('C:/Dev/ZackBot_research_cache')
LEADS = {'r6_regime_flip_4h':6,'s54_double_top_4h':54,'l24_breakaway_day_4h':24,'s26_weekly_close_breakdown_4h':26,'l40_dual_momentum_4h':40,'s51_momentum_accel_4h':51,'s76_uptrend_line_break_4h':76,'r74_prior_month_break_4h':74}

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def archive(commit, paths, dest):
    b = subprocess.check_output(['git','-C',str(ROOT),'archive',commit,*paths])
    with tarfile.open(fileobj=io.BytesIO(b)) as t: t.extractall(dest, filter='data')

def stage():
    STAGE.mkdir(parents=True, exist_ok=True)
    archive(PIN, ['research/extra31_screen'], STAGE)

def instrument(path):
    text = path.read_text(encoding='utf-8')
    needle = "                eq -= p['qty'] * c * FPB; p['realized'] -= p['qty'] * c * FPB"
    if text.count(needle) != 1: raise RuntimeError('funding instrumentation point changed')
    text = text.replace(needle, "                if fill_log is not None: fill_log.append(dict(kind='__proxy__', sym=s, pos_i=int(p['i']), i=int(i), qty=float(p['qty']), ref=float(c), cash=float(p['qty'] * c * FPB)))\n" + needle)
    path.write_text(text, encoding='utf-8', newline='\n')

def worker(case, output):
    import numpy as np
    import pandas as pd
    stage()
    here = STAGE / 'research' / 'extra31_screen'
    sys.path.insert(0, str(here))
    import run_screen as RS, sieve_v1 as SV, sieve_v1_panel as SP
    version = LEADS[case]
    casespath = here / f'longscout_v{version}_cases.json'
    cases = json.loads(casespath.read_text(encoding='utf-8'))
    SP.CASES = cases
    RS.CONTRACT['sources'].update(cases['sources'])
    out = Path(output); out.mkdir(parents=True, exist_ok=True)
    pubdir = f'docs/research/longscout_v{version}/{case}'
    archive(PIN, [f'{pubdir}/{case}_result.json',f'{pubdir}/{case}_all_orders_trades.csv.gz'], out)
    pub = out / pubdir
    result = json.loads((pub / f'{case}_result.json').read_text(encoding='utf-8'))
    coins = result['coins']
    fresh = SHARED / 'longscout_fresh_20261114'
    extra = SHARED / 'h5_wide_d01_20261112'
    expected_fresh = 'fb0c0572b6c2bc5f569c64d89bd0ee2ca1dd9e3cf96b1ed8bd1110c341ed2f3e'
    if sha(fresh/'input_manifest.json') != expected_fresh: raise RuntimeError('fresh manifest mismatch')
    RS.check_data(str(extra/'clean'), True, RS.EXTRA31)
    manifest = json.loads((fresh/'input_manifest.json').read_text(encoding='utf-8'))
    extra_manifest = json.loads((extra/'input_manifest.json').read_text(encoding='utf-8'))
    data = out / '_data'; data.mkdir(exist_ok=True)
    hashes = {}
    for s in ['BTCUSDT'] + coins:
        base = extra if s in RS.EXTRA31 else fresh
        src = base/'clean'/f'{s}_1h.csv'; dest = data/src.name
        h = sha(src); hashes[s] = h
        if base == fresh and h != manifest['symbols'][s]['sha256']: raise RuntimeError(f'{s} data mismatch')
        if base == extra and h != extra_manifest['symbols'][s]['csv_sha256']: raise RuntimeError(f'{s} data mismatch')
        if dest.exists() and sha(dest) != h: raise RuntimeError(f'{s} stale linked data')
        if not dest.exists(): os.link(src,dest)
    RS.ROOT = str(ROOT)
    src = Path(RS.export(cases['cases'][case]['source'],str(out)))
    for name, expected_hash in result['code_sha256'].items():
        file = src/name if name in ('backtest.py','strategies.py') else src/'research'/name
        if sha(file) != expected_hash: raise RuntimeError(f'published source hash mismatch: {name}')
    backtest_hash = sha(src/'backtest.py'); instrument(src/'backtest.py')
    sys.path[:0] = [str(src),str(src/'research')]
    import backtest as BT
    published = pd.read_csv(pub/f'{case}_all_orders_trades.csv.gz')
    original = BT.run; completed = []
    temporary = out/f'{case}_jobs.jsonl.gz.tmp'
    stream = gzip.open(temporary, 'wt', encoding='utf-8')
    def hooked(book, *args, **kwargs):
        tr, curve = original(book,*args,**kwargs)
        log = kwargs['fill_log']; bykey = {}
        for f in log: bykey.setdefault((f['sym'],f['pos_i']),[]).append(f)
        timeline = pd.to_datetime(book.t.values)
        trades = []
        for _, row in tr.iterrows():
            k = (row.sym,int(row.i_in)); items = bykey.get(k,[])
            fills = [f for f in items if f['kind'] != '__proxy__']
            if not fills: raise RuntimeError(f'missing fills {k}')
            cash = sum(f['cash'] for f in items if f['kind'] == '__proxy__')
            turn = sum(abs(f['ref']*f['qty']) for f in fills)
            for f in fills: f['time_ms'] = int(timeline[f['i']].value // 1000000)
            risk = float(fills[0]['risk']); pnl = float(row.pnl)
            trades.append(dict(trade_id=f'{row.sym}:{int(row.i_in)}', sym=row.sym, side=int(row.side), i_in=int(row.i_in), i_out=int(row.i_out), t_in_ms=int(timeline[int(row.i_in)].value//1000000), t_out_ms=int(timeline[int(row.i_out)].value//1000000), risk=risk, baseline_pnl=pnl, baseline_R=float(row.R), pnl_by_cost={'7':pnl,'15':pnl-turn*.0008,'23':pnl-turn*.0016}, proxy_cash_paid=cash, fills=fills))
        order, scen = len(completed)//2, ('path','worst')[len(completed)%2]
        expected = published[(published.order == order) & (published.scen == scen)]
        actual = {(t['sym'], t['t_in_ms'], t['side']): t for t in trades}
        if len(actual) != len(trades) or len(expected) != len(trades): raise RuntimeError(f'trade count/key mismatch {order} {scen}')
        for _, e in expected.iterrows():
            key = (e.sym, int(pd.Timestamp(e.t_in).value//1000000), int(e.side))
            t = actual.get(key)
            if t is None or t['t_out_ms'] != int(pd.Timestamp(e.t_out).value//1000000): raise RuntimeError(f'trade identity mismatch {order} {scen} {key}')
            for cost in ('7','15','23'):
                if not np.isclose(t['pnl_by_cost'][cost], e['pnl'+cost], rtol=1e-9, atol=1e-7): raise RuntimeError(f'trade pnl mismatch {order} {scen} {key} cost {cost}')
        parity = {'published_n':len(expected),'replayed_n':len(trades),'published_net15':float(expected.pnl15.sum()),'replayed_net15':sum(t['pnl_by_cost']['15'] for t in trades)}
        parity['pass'] = bool(parity['published_n']==parity['replayed_n'] and np.isclose(parity['published_net15'],parity['replayed_net15'],rtol=1e-9,atol=1e-6))
        if not parity['pass']: raise RuntimeError(f'order parity failed: {order} {scen} {parity}')
        job = dict(job_id=f'{case}:{order}:{scen}',strategy=case,scope='wide165',order=order,scenario=scen,variant='fixed_book',cost_bp=15,bar_ms=14400000,parity=parity,trades=trades)
        stream.write(json.dumps(job,allow_nan=False)+'\n')
        completed.append(parity)
        log[:] = [f for f in log if f['kind'] != '__proxy__']
        return tr, curve
    BT.run = hooked
    from types import SimpleNamespace
    a = SimpleNamespace(worker=str(src),case=case,max_pos=1,universe=','.join(coins),data=str(data),out=str(out),orders=40)
    SP.worker(a)
    stream.close()
    if len(completed) != 80: raise RuntimeError(f'expected 80 jobs; got {len(completed)}')
    metadata=dict(pin=PIN,case=case,cases_sha256=sha(casespath),source_commit=cases['sources'][cases['cases'][case]['source']]['commit'],backtest_sha256=backtest_hash,input_sha256=hashes,published_result_sha256=sha(pub/f'{case}_result.json'),published_trades_sha256=sha(pub/f'{case}_all_orders_trades.csv.gz'),timing='fills at candle open; actual intrabar timing unknown',proxy='cash charged at management loop before intrabar events',jobs=len(completed))
    temporary.replace(out/f'{case}_jobs.jsonl.gz')
    (out/f'{case}_parity.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    print('PARITY PASS',case,len(completed),flush=True)

def main():
    p=argparse.ArgumentParser(); p.add_argument('--case',choices=LEADS,required=True); p.add_argument('--out',type=Path)
    a=p.parse_args(); worker(a.case,a.out or CACHE/a.case)
if __name__ == '__main__': main()
