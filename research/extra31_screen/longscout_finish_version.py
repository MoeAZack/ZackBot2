"""Package and publish one COMPLETED research version; never activates anything.

Usage: python longscout_finish_version.py <version> <own_output_dir> <test_log>
All cases must finish40orders. Preserves source/case freezes and every trial.
Writes only own clone/output; pushes own branch and posts result to PR347.
"""
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from longscout_register_version import RESTART_BRANCH

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REPO = 'MoeAZack/ZackBot2'
FRESH = 'C:/Dev/ZackBot_research_cache/longscout_fresh_20261114'
EXTRA = 'C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112'


def command(args,**kwargs):
    return subprocess.check_output(args,cwd=ROOT,text=True,encoding='utf-8',**kwargs).strip()


def number(x): return 'none' if x is None else f'{x:+.6f}'


def main():
    version,out,testlog = int(sys.argv[1]),Path(sys.argv[2]).resolve(),Path(sys.argv[3]).resolve()
    restart = version >= 105
    deferred='--defer-network' in sys.argv[4:]
    assert out.parent == Path('C:/Dev/ZackBot_research_cache').resolve() and out.name.startswith('longscout_codex_')
    assert command(['git','branch','--show-current']) == (RESTART_BRANCH if restart else 'codex/longscout-v91-batch-20261120')
    cases_path = HERE/f'longscout_v{version}_cases.json'
    cases = json.loads(cases_path.read_text())
    for key,c in cases['cases'].items():
        r = json.loads((out/key/f'{key}_result.json').read_text())
        assert r['orders']==40 and len(r['per_order']['path'])==40 and len(r['per_order']['worst'])==40
        assert r['wide165']['cases_sha256']==hashlib.sha256(cases_path.read_bytes()).hexdigest()
        command(['git','diff','--exit-code',c['commit'],'--',f'research/longscout_v{version}.py'])
    env=dict(os.environ,LONGSCOUT_CASES=cases_path.name)
    print(command([sys.executable,str(HERE/'longscout_v91_input_audit.py'),FRESH,EXTRA,str(out)]),flush=True)
    with (out/'report.log').open('w',encoding='utf-8') as stream:
        subprocess.run([sys.executable,str(HERE/'longscout_version_report.py'),str(out),FRESH],cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT,check=True)
    diagnostics=json.loads((out/'diagnostics.json').read_text())
    target=ROOT/f'docs/research/longscout_v{version}'
    target.mkdir(parents=True,exist_ok=False)
    for key in cases['cases']:
        dest=target/key; dest.mkdir()
        for p in (out/key).iterdir():
            if p.is_file() and (p.name.endswith('.json') or p.name.endswith('.csv') or p.name.endswith('.csv.gz')): shutil.copy2(p,dest/p.name)
    for name in ('diagnostics.json','input_audit.json'): shutil.copy2(out/name,target/name)
    if restart:
        shutil.copytree(out/'mutations', target/'mutations')
    if testlog.exists():
        raw=testlog.read_bytes()
        text=raw.decode('utf-16') if raw.startswith(b'\xff\xfe') else raw.decode('utf-8')
        assert 'passed' in text and 'failed' not in text.lower(), 'No clean tests evidence'
        (target/'tests.log').write_text(text,encoding='utf-8')
    else: raise RuntimeError('Test log missing')
    ledger_path=HERE/'longscout_resume_ledger.json'
    ledger=json.loads(ledger_path.read_text())
    item=ledger['versions'][str(version)]
    assert item['status']=='PREREGISTERED', 'Never silently overwrite a completed version'
    passes=[]
    rows=[]
    for candidate in item['candidates']:
        key=candidate['key']; d=diagnostics['cases'][key]
        candidate.update(verdict=d['status'],meanR15_fee_plus_slip=d['medians']['meanR15'])
        candidate['failed_gates']=[k for k,v in d['gates'].items() if not v]
        if restart:
            candidate['forward_worthy'] = d['forward_bar']['qualified']
        for subset in ('fresh134','extra31'):
            if not (d['subsets'][subset]['median_meanR15'] is not None and d['subsets'][subset]['median_meanR15']>0): candidate['failed_gates'].append(subset)
        if d['status']=='WIDE165_PASS': passes.append(key)
        rows.append([key,d['status'],d['medians']['n'],d['medians']['meanR15'],d['subsets']['fresh134']['median_meanR15'],d['subsets']['extra31']['median_meanR15'],d['dd']['median_R'],d['dd']['median_label']])
    item.update(status='SCREEN_PASS_PENDING_INDEPENDENT_REVIEW' if passes else 'COMPLETE_REJECTED',batch_passes=len(passes))
    ledger['completed_trials']+=len(item['candidates'])
    ledger['historical_screen_passes']+=len(passes)
    ledger_path.write_text(json.dumps(ledger,indent=1)+'\n',encoding='utf-8',newline='\n')
    with (target/'summary.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f);w.writerow(['candidate','status','median_closes','meanR15_fee_plus_slip','fresh134','extra31','median_closedDD_R','DD_label']);w.writerows(rows)
    stamp=datetime.now(ZoneInfo('Africa/Cairo')).strftime('%d %b %Y %H:%M Cairo')
    table='| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |\n|---|---:|---:|---:|---:|---|---|\n'
    for key,status,n,r,f,e,dd,label in rows: table+=f'|{key}|{n:g}|{number(r)}|{number(f)}|{number(e)}|{dd:.3f}R {label}|{status}|\n'
    source=next(iter(cases['sources'].values()))['commit']
    detail=[]
    for key,d in diagnostics['cases'].items():
        detail.append(f"{key}: failures {', '.join(next(c['failed_gates'] for c in item['candidates'] if c['key']==key)) or 'none'}; BTC rising/sideways/falling R15 {number(d['btc_regimes']['rising']['median_meanR15'])}/{number(d['btc_regimes']['sideways']['median_meanR15'])}/{number(d['btc_regimes']['falling']['median_meanR15'])}; long/short {number(d['sides']['long']['median_meanR15'])}/{number(d['sides']['short']['median_meanR15'])}; fallback-rule path trades (40 repeated orders) {d['missing_rule_trades']['path_trades_all_orders']}, order0 {d['missing_rule_trades']['order0']}.")
        if restart:
            forward = d['forward_bar']
            regimes = '/'.join(number(forward['fresh_regime_R15'][regime]) for regime in ('rising','sideways','falling'))
            failures = ', '.join(k for k, v in forward['gates'].items() if not v)
            top3 = forward['fresh_top3_share']
            share = f'{top3:.2%}' if top3 is not None else 'undefined'
            detail.append(f"{key}: pooled R23 {number(d['medians']['meanR23'])}; fresh134 rising/sideways/falling R15 "
                          f"{regimes}; fresh134 R23 {number(forward['fresh_R23'])}; fresh134 top-three/net "
                          f"{share}; forward bar {'PASS' if forward['qualified'] else 'FAIL ('+failures+')'}.")
    if restart:
        detail.insert(0, cases['family_novelty'])
        detail.append(cases['forward_bar'])
    synopsis=(f'# LONGSCOUT V{version}: {len(passes)}/{len(rows)} frozen screen passes\n\n{stamp}. Source freeze `{source}`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: {ledger["completed_trials"]} completed trials / {ledger["historical_screen_passes"]} historical screen passes; {ledger["registered_trials"]} registered.\n\n'+table+'\n'+'\n\n'.join(detail)+
        '\n\nResearch-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.\n\nDD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.\n\n'+
        ('A screen pass is a lead for independent reproduction. No book is activated.\n\n' if passes else 'The frozen economic rules are rejected. Search proceeds to the next preregistered version.\n\n')+
        'ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.\n')
    (target/'README.md').write_text(synopsis,encoding='utf-8',newline='\n')
    command(['git','add',str(target.relative_to(ROOT)),str(ledger_path.relative_to(ROOT))])
    command(['git','-c','user.name=Codex','-c','user.email=codex@users.noreply.github.com','commit','-m',f'research: publish V{version} economics and complete trial ledger'])
    head=command(['git','rev-parse','HEAD'])
    report_url=f'https://github.com/{REPO}/blob/{head}/docs/research/longscout_v{version}/README.md'
    body=(f'<!-- agent: codex -->\nCODEX RESULT: LONGSCOUT V{version}, {stamp}: **{len(passes)}/{len(rows)} screen passes**. Ledger **{ledger["registered_trials"]} registered / {ledger["completed_trials"]} completed / {ledger["historical_screen_passes"]} historical screen passes**.\n\n'+table+
          f'\n[Full evidence]({report_url}), exact head `{head}`; [frozen cases](https://github.com/{REPO}/blob/{head}/research/extra31_screen/longscout_v{version}_cases.json) and source `{source}`.\n\n'+'\n\n'.join(detail)+
          '\n\n40orders/165coins, unchanged rule-v1+fresh134/extra31 gates;15bp FEES+2bp slip/side, native fixed funding, frozen exchange-rule feasibility. Saved-tape gates/subsets match; tests and both tapes linked. Consumed calendar, not untouched confirmation. Missing ICP/TLM snapshot coverage and fixed-not-historical funding remain disclosed; no tuning or subgroup rescue.\n\n'+
          ('Screen-pass lead awaits independent reproduction.\n\n' if passes else 'Rejected. Proceeding to the next preregistered version under owner follow-up.\n\n')+
          'ROADMAP FIT: long/range economic search. DEFERRED: strategy activation, protected branch changes and mainnet.\nLANES: Build prepares the next hypothesis. Evidence can reproduce these frozen results. Integration publishes the ledger and advances the next registered version.\n')
    request=out/'github_result_request.json';request.write_text(json.dumps(dict(body=body)),encoding='utf-8')
    if deferred:
        receipt=dict(id=None,html_url=None,publication_pending=True,head=head,screen_passes=passes,
                     note='Workstation outbound connection outage; immutable local evidence and queued PR347 request. Source/case provenance unchanged.')
        (out/'github_receipt.json').write_text(json.dumps(receipt,indent=1)+'\n',encoding='utf-8')
        state_path=ROOT/'research/longscout_control_state.json'
        if state_path.exists():
            state=json.loads(state_path.read_text())
            state['completed_versions'][str(version)]=dict(passes=len(passes),trials_after=ledger['completed_trials'],comment=None,publication_pending=True,head=head)
            if state.get('running',{}).get('version')==version:state['running']={}
            state_path.write_text(json.dumps(state,indent=1)+'\n',encoding='utf-8',newline='\n')
        print(json.dumps(receipt),flush=True)
        return
    if restart:
        command(['git', 'push', 'origin', f'HEAD:refs/heads/{RESTART_BRANCH}'])
    else:
        command(['git','push'])
    receipt=json.loads(command(['gh','api','--method','POST',f'repos/{REPO}/issues/347/comments','--input',str(request)]))
    (out/'github_receipt.json').write_text(json.dumps(dict(id=receipt['id'],html_url=receipt['html_url'],head=head,screen_passes=passes),indent=1)+'\n',encoding='utf-8')
    state_path=ROOT/'research/longscout_control_state.json'
    if state_path.exists():
        state=json.loads(state_path.read_text())
        state['completed_versions'][str(version)]=dict(passes=len(passes),trials_after=ledger['completed_trials'],comment=receipt['html_url'],head=head)
        if state.get('running',{}).get('version')==version:state['running']={}
        state_path.write_text(json.dumps(state,indent=1)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(dict(head=head,comment=receipt['html_url'],screen_passes=passes,completed_trials=ledger['completed_trials'])),flush=True)


if __name__=='__main__': main()
