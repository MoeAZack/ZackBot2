"""Publish completed CX cases, full ledger and frozen provenance (no simulation).

Arguments: result root, fresh selected.json, real-check JSON. Existing CX1
history is retained; all completed cases are arithmetically audited first.
"""
import csv,datetime,hashlib,json,shutil,subprocess,sys
from pathlib import Path
from zoneinfo import ZoneInfo
from longscout_cx_audit import audit


def main():
    root=Path(__file__).resolve().parents[2]
    output=Path(sys.argv[1]); selected=Path(sys.argv[2])
    completed=sorted(p.name for p in output.glob('cx*') if (p/'execution_receipt.json').is_file() or
        p.name in ('cx001_entropy_collapse_long_4h','cx002_entropy_collapse_short_4h'))
    rows=audit(output,selected,completed)
    combined=root/'docs/research/longscout_cx';combined.mkdir(parents=True,exist_ok=True)
    now=datetime.datetime.now(ZoneInfo('Africa/Cairo')).isoformat()
    frozen={}
    for n in (1,2,3,4):
        cp=root/f'research/extra31_screen/longscout_cx{n}_cases.json'
        cases=json.loads(cp.read_text());rules=next(iter(cases['sources'].values()))['commit']
        cases_commit=subprocess.check_output(['git','log','--diff-filter=A','--format=%H','--',str(cp.relative_to(root))],text=True,cwd=root).splitlines()[-1]
        paths=[f'research/longscout_cx{n}.py',str(cp.relative_to(root)).replace('\\','/')]
        blobs={}
        for path,commit in zip(paths,[rules,cases_commit]):
            blob=subprocess.check_output(['git','show',commit+':'+path],cwd=root)
            assert (root/path).read_bytes().replace(b'\r\n',b'\n')==blob
            blobs[path]=hashlib.sha256(blob).hexdigest()
        frozen[str(n)]=dict(rules=rules,cases=cases_commit,git_sha256=blobs)
        current=[row for row in rows if row['id'] in cases['cases']]
        if n>1 and len(current)==2:
            d=root/f'docs/research/longscout_cx{n}'
            for row in current:
                case=row['id'];dest=d/case;dest.mkdir(exist_ok=True)
                for p in (output/case).iterdir():
                    if p.is_file():shutil.copyfile(p,dest/p.name)
            (d/'ledger.json').write_text(json.dumps(current,indent=2)+'\n')
            if not (d/'provenance.json').exists():
                (d/'provenance.json').write_text(json.dumps(dict(freeze=frozen[str(n)],published_cairo=now,
                    note='Unchanged V2 worker, rule-v1 gates and 40 seeded orders; exact-blob transport. No tuning.'),indent=2)+'\n')
            (d/'README.md').write_text(f'# CX{n}: '+str(sum(r['status']=='WIDE165_PASS' for r in current))+'/2 screen passes\n\n'+table(current)+
                '\nFull-precision gates and failed-gate names: ledger.json. Frozen provenance: provenance.json.\n'
                'All signals, both order-0 tapes and 40-order path/worst tapes are retained per case.\n'
                'Research only; consumed calendar. No tuning or profitability/activation claim.\n'
                'The shared formula, real-signal and prefix checks are documented in ../longscout_cx/README.md.\n')
    passed=sum(r['status']=='WIDE165_PASS' for r in rows)
    ledger=dict(baseline_trials=281,baseline_screen_passes=11,cx_registered=8,cx_completed=len(rows),cx_passes=passed,
        total_completed_excluding_concurrent_lanes=281+len(rows),trials=rows)
    (combined/'ledger.json').write_text(json.dumps(ledger,indent=2)+'\n')
    fields=['id','status','n','meanR15','meanR23','meanR15_early','meanR15_late','meanR15_ex_best','fresh134','extra31','order0_early','order0_late','failed']
    with (combined/'ledger.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for r in rows:w.writerow({k:'; '.join(r[k]) if k=='failed' else r[k] for k in fields})
    (combined/'freeze_provenance.json').write_text(json.dumps(frozen,indent=2)+'\n')
    checks=json.loads(Path(sys.argv[3]).read_text());(combined/'real_signal_checks.json').write_text(json.dumps(checks,indent=2)+'\n')
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (output/'_data').glob('*_1h.csv')}
    baseline=json.loads((root/'docs/research/longscout_cx1/provenance.json').read_text())
    assert hashes==baseline['loaded_csv_sha256'] and len(hashes)==166
    (combined/'input_hashes.json').write_text(json.dumps(dict(input_manifests=baseline['input_manifests'],loaded_csv_sha256=hashes),indent=2)+'\n')
    (combined/'README.md').write_text(f'''# Lane 5 independent CX search

Published {now} (Africa/Cairo). {len(rows)}/8 registered trials completed, {passed} screen passes.
Baseline: 281 reported trials / 11 screen passes. Completed cumulative count: {281+len(rows)},
excluding concurrent lanes. Independent passes increase the multiple-testing count.

'''+table(rows)+'''

R15 and subset returns are mean per-trade R at 15 bp per side, medians across 40 seeded coin orders.
Full ledger CSV/JSON includes R23, both calendar halves, ex-best coin, order-0 half counts and every failed gate.
Round directories retain full results, signals, order-0 path/worst tapes, all-order tapes and export receipts.
Original CX1 evidence and its no-follow-up statement describe its original bounded assignment;
the owner subsequently explicitly authorized CX2-CX4. CX1 remains unchanged.

## New families and no-tuning provenance

CX2: sequential, reset-on-threshold standardized CUSUM. Prior V6 momentum and V21 autocorrelation do not
implement sequential reset states. CX3: within-day squared-return energy concentration, unlike V89 volume
concentration across 30 days, V47 session scheduling or V90 return/volume shock size. CX4: previous return
versus next absolute return coupling, unlike V85 contemporaneous return/volume correlation, V21 linear
autocorrelation or V84 marginal semivariance. None repeats CX1 ordinal entropy.
Each round committed rules first, then cases. All six new trials were pushed before any of their outcomes.
freeze_provenance.json verifies immutable Git blobs; working JSON line endings may be CRLF.
No rule, parameter, gate, universe or management change followed an outcome. No failed trial was replaced.

## Validation and limits

Unchanged LONGSCOUT V2 worker, costs, funding proxy, exchange rules, eligibility, $8 risk,
$3,333.33 cap, one position, 40 seeded orders, seven rule-v1 gates and both subset gates.
Every case runs 40 path and 40 worst-path scenarios. The existing source-transport fallback reads the exact
same frozen export list via cat-file, validates every blob ID and retains byte/hash receipts.
It does not change execution. Two original manifests and 175 source CSV hashes were checked before the new batch;
166 distinct loaded hashes were checked again after runs and match original CX1 loaded data.

Original CX1 has 8 targeted native Windows/CP1252 tests; CX2-CX4 add 12 tests covering independent formulas,
gaps, flat inputs, exact thresholds, regime gates, availability and 36 full-output synthetic prefix checks.
18 independent real-data signal comparisons (six new rules on three coins) and 79 real-data prefix comparisons
pass with zero mismatches. These are sampled causality checks, not an exhaustive proof.
Every completed case's saved trades are independently reconciled against all medians and acceptance gates;
this is arithmetic verification, not an independent admission/cash replay.
The original data calendar is consumed; historical exchange-filter coverage, proxy funding, capped sizing,
survivorship and selection multiplicity remain limitations. No fresh holdout or forward profit is claimed.

Reproduce each case with LONGSCOUT_CASES=longscout_cxN_cases.json and the existing longscout_cx_transport.py,
using the frozen source data paths shown in each execution_receipt.json. Reconcile with longscout_cx_audit.py.
Final focused test receipt is validation.json when present.

Line-ending follow-up: when line_ending_audit.json is present, it records the original inherited
core.autocrlf=true, the verified switch to local false and byte-identical canonical LF checkout.
Every frozen alpha file matched its Git bytes throughout. Case files were originally authored CRLF;
all eight as-run case checksums match the preserved cxN_cases_as_run.json.gz archives. Their canonical
Git checksums differ only by line endings; no economic rerun or rule change occurred. To reproduce
the original checksum metadata exactly, decompress the relevant archive to a scratch JSON file and
set LONGSCOUT_CASES to that file's absolute path. Canonical LF cases have identical JSON content and
economics, with different raw case-checksum metadata. Original round provenance describes as-run state.

ROADMAP FIT: owner-authorized sustained lane-5 independent strategy economics under frozen LONGSCOUT gates.
DEFERRED: tuning, new data, full independent execution replay, forward/PAPER activation and unrelated work.
No accounts, keys, runtime, settings, schedulers, watchdogs, orders, other clones, merges or ready-state actions.
LANES: Build = six new rules and cases complete; Evidence = completed-case runs and audits in ledger.json;
Integration = same draft PR #356, Claude disposition only; no dependent activation.
''')
    # Windows-authored text must retain canonical LF bytes with autocrlf=false.
    # Compressed tapes and archived as-run cases are binary and stay untouched.
    for folder in [combined]+[root/f'docs/research/longscout_cx{n}' for n in (2,3,4)]:
        for p in folder.rglob('*'):
            if p.is_file() and p.suffix in ('.json','.csv','.md'):
                raw=p.read_bytes()
                if b'\r\n' in raw:p.write_bytes(raw.replace(b'\r\n',b'\n'))
    print('Published',len(rows),'CX trials;',passed,'screen passes; all gates reconciled')


def table(rows):
    text='| trial | status | median n | R15 | fresh134 | extra31 |\n|---|---|---:|---:|---:|---:|\n'
    for r in rows:
        vals=[str(r['n'])]+['n/a' if r[k] is None else f"{r[k]:+.6f}" for k in ('meanR15','fresh134','extra31')]
        text+='| '+r['id']+' | '+r['status']+' | '+' | '.join(vals)+' |\n'
    return text


if __name__=='__main__':main()
