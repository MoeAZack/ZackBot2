"""Actual own269 fixed checksum-source CLI and exact-Git receipt proofs."""
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import zipfile

import pytest
from tests.test_c260_runner import make_fixture,repin_synthetic_manifest,fixture_artifacts
from tests.test_c269_source import adapt_metadata,issue,native_fixture,DEV_START,END,H
from tools.research import c269_source as S
from tools.research import c269_authority as A
from tools.research import c269_metadata as C
from tools.research import c269_runner as R

SYN_START=S.CORE.milliseconds('2025-01-01T00:00:00Z')
SYN_END=SYN_START+14*24*H


def synthetic_window(monkeypatch):
    original=C.load_proposal_metadata
    def proposal():
        result=original()
        result['splits']['development']='2025-01-01T00:00:00Z/2025-01-15T00:00:00Z'
        result['splits']['development_filter_eligible']='2025-01-01T00:00:00Z/2025-01-15T00:00:00Z'
        return result
    monkeypatch.setattr(C,'load_proposal_metadata',proposal)


def write_archive(store,symbol,series,year,month,lines,interval='1h'):
    suffix='fundingRate' if series=='fundingRate' else interval;folder='' if series=='fundingRate' else interval+'/'
    rel=f'um/monthly/{series}/{symbol}/{folder}{symbol}-{suffix}-{year:04d}-{month:02d}.zip'
    path=store/rel;path.parent.mkdir(parents=True,exist_ok=True)
    header=S.M.FUNDING_HEADER if series=='fundingRate' else S.M.KLINE_HEADER
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:z.writestr(path.name[:-4]+'.csv',header+b'\n'+b'\n'.join(lines)+b'\n')
    Path(str(path)+'.ok').write_text(hashlib.sha256(path.read_bytes()).hexdigest())
    return S.M.scan_archive(str(path),rel)


@pytest.fixture(scope='module')
def full_development_archive(tmp_path_factory):
    members=('ALT00USDT','ALT01USDT','BTCUSDT')
    root=adapt_metadata(make_fixture(tmp_path_factory.mktemp('c269-SYNTHETIC-14DAY-CLI'),hours=1,members=members,trade_interval='1m'))
    manifest=json.loads((root/'manifest.json').read_bytes());files=[]
    count=(SYN_END-SYN_START)//H
    btc=[.003*math.sin(i*1.713)+.0001*math.cos(i*.771) for i in range(count)]
    for k,symbol in enumerate(members):
        returns=btc if symbol=='BTCUSDT' else [v*(1+(k+1)/20 if v<0 else 1) for v in btc]
        price=100.;monthly={}
        for i,change in enumerate(returns):
            t=SYN_START+i*H;opening=round(price,2);price*=math.exp(change);closing=round(price,2)
            cells=list(map(str,(t,opening,round(max(opening,closing)+.5,2),round(min(opening,closing)-.5,2),closing,
                1000,t+H-1,60000000,10,500,30000000,0)))
            dt=datetime.fromtimestamp(t/1000,timezone.utc);monthly.setdefault((dt.year,dt.month),[]).append(cells)
        for (year,month),hours in monthly.items():
            marks=[];minutes=[]
            for cells in hours:
                mark=list(cells);mark[7]='0';marks.append(','.join(mark).encode())
                t=int(cells[0])
                for minute in range(60):
                    point=list(cells);point[0]=str(t+minute*60000);point[6]=str(t+(minute+1)*60000-1);point[7]='1000000'
                    minutes.append(','.join(point).encode())
            minutes.append(f'{SYN_END},{hours[-1][4]},{hours[-1][4]},{hours[-1][4]},{hours[-1][4]},1,{SYN_END+59999},1,1,1,1,0'.encode())
            files.append(write_archive(root/'store',symbol,'klines',year,month,minutes,'1m'))
            files.append(write_archive(root/'store',symbol,'markPriceKlines',year,month,marks))
            funding=[f'{t},8,.00001'.encode() for t in range(SYN_START,SYN_END,8*H)
                if (datetime.fromtimestamp(t/1000,timezone.utc).year,datetime.fromtimestamp(t/1000,timezone.utc).month)==(year,month)]
            funding.append(f'{SYN_END+1},8,.00001'.encode())
            files.append(write_archive(root/'store',symbol,'fundingRate',year,month,funding))
    manifest['files']=sorted(files,key=lambda r:r['path']);repin_synthetic_manifest(root,manifest)
    return root


def test_actual_checksum_archive_current_open_short_cli_canonical_stress(full_development_archive,tmp_path,monkeypatch):
    synthetic_window(monkeypatch)
    issue(monkeypatch,full_development_archive,tmp_path/'SYNTHETIC-own-common')
    monkeypatch.setattr(A,'_authenticate_metadata',lambda side:('a'*40,'b'*64,full_development_archive/'store',fixture_artifacts(full_development_archive),SYN_START,SYN_END))
    out=tmp_path/'long.json';assert R.main(['development','--out',str(out)])==0
    report=json.loads(out.read_bytes());base,stress=report['primary'],report['stress']
    assert base['closed_count']>0 and base['data_complete'] and stress['data_complete'],(base['failures'],stress['failures'])
    assert not base['unresolved_exposure'] and base['cash_reconciled'] and stress['cash_reconciled']
    assert base['required_report_complete'] and stress['required_report_complete']
    assert [(r['attempt_id'],r['quantity'],r['initial_risk_cash']) for r in base['closed']]==[(r['attempt_id'],r['quantity'],r['initial_risk_cash']) for r in stress['closed']]
    assert [r['funding_cash'] for r in base['closed']]==[r['funding_cash'] for r in stress['closed']]
    assert all(r['entry_ms']==r['decision_ms'] and r['entry_ms']%(6*H)==0 for r in base['closed'])
    assert len(base['hourly_equity'])==(SYN_END-SYN_START)//H+1
    assert report['all_statistically_fit_proposals_once'] and report['source_fit_coverage_once']
    assert all('score_overhead_notional_share' in r and 'forecast' not in r for r in report['all_statistically_fit_proposals_once'])
    assert report['statistics_specification']['side_seeds']==[26920261107,26920261108]
    assert report['economic_development_verdict']=='REJECT' and not report['paper_qualified']
    assert Path(report['canonical_report_path']).read_bytes()==out.read_bytes()
    with pytest.raises(S.C.C269Locked,match='ALREADY_SPENT'):R.main(['development','--out',str(tmp_path/'again.json')])


def test_export_preflight_and_failed_export_retains_canonical(native_fixture,tmp_path,monkeypatch):
    issue(monkeypatch,native_fixture,tmp_path/'SYNTHETIC-common');out=tmp_path/'existing.json';out.write_bytes(b'old')
    with pytest.raises(S.C.C269Locked,match='EXPORT_ALREADY_EXISTS'):R.main(['development','--side','short','--out',str(out)])
    assert not (tmp_path/'SYNTHETIC-common').exists()
    monkeypatch.setattr(R,'evaluate_books',lambda provider:{'candidate_id':269,'writer_test_only':True})
    export=tmp_path/'failed-export.json';original=Path.open
    def opened(path,*args,**kwargs):
        if path==export and args and args[0]=='xb':raise OSError('SYNTHETIC export failure')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',opened)
    with pytest.raises(OSError,match='SYNTHETIC export'):R.main(['development','--side','short','--out',str(export)])
    canonical=tmp_path/'SYNTHETIC-common/c269-evaluation/development--1.canonical.json'
    assert json.loads(canonical.read_bytes())['writer_test_only'] is True


@pytest.fixture
def feature_scope_repo(tmp_path,monkeypatch):
    root,store=tmp_path/'feature',tmp_path/'empty-source';store.mkdir()
    def git(*args):return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.PIPE).decode().strip()
    subprocess.run(['git','clone','--shared','--no-checkout',str(A.ROOT),str(root)],check=True,capture_output=True)
    git('config','core.autocrlf','false')
    git('sparse-checkout','set','--no-cone','/contracts/c269_overhead_notional/', '/contracts/c267_btc_residual_leadlag/lifecycle/',
        '/contracts/strategy_causal_pack_01/exchange_filter_authority.v2.json',
        '/research_evidence/prereg/binance-um-exchangeinfo-mainnet-raw-20241213T044448Z.json',
        '/research_evidence/prereg/binance-um-exchangeinfo-mainnet-raw-20260217T041328Z.json')
    git('checkout','--force','HEAD');git('config','user.name','Synthetic C269');git('config','user.email','synthetic@example.invalid')
    accepted=git('rev-parse','HEAD')
    receipt={'format':'zb-c269-offline-development-authority/1','candidate_id':269,'proposal_sha256':C.PREREG_SHA256,
        'scope':A.SCOPE,'head_policy':A.POLICY,'default_off':True,'accepted_implementation_head':accepted,
        'research_tree_oid':git('rev-parse',f'{accepted}:tools/research'),'feasibility_blob_oid':git('rev-parse',f'{accepted}:feasibility.py'),
        'documents':A.DOCUMENTS,'side_cells':[-1],'lifecycle_model_scope':S.C.LIFECYCLE_SCOPE,
        'independent_review':{'reviewer':'SYNTHETIC_ONLY','reference':'test fixture, no real source acceptance',
            'reviewed_head':accepted,'verdict':'PASS_EXACT_C269_CODE_SOURCE_COST_AND_PREREAD_RULES'},
        'authority':S.C.fixed_source_pins(),'store_root':str(store),'registration':{'candidate_id':269,'new_primary_trials':1,
            'proposal_sha256':C.PREREG_SHA256,'historical_global_variant_total':'UNKNOWN','numbered_family_screen_only':True,'global_FWER_claimed':False}}
    (root/A.RECEIPT).write_bytes(A.canonical(receipt));git('add',A.RECEIPT);git('commit','-qm','SYNTHETIC own side receipt')
    git('update-ref','-d','refs/remotes/origin/master');monkeypatch.setattr(A,'ROOT',root)
    monkeypatch.setattr(S.MetadataView,'preflight_grid',lambda view:None)
    return root,store,receipt,git


def test_actual_git_positive_own_short_issuer_no_old_authority(feature_scope_repo):
    root,store,_,git=feature_scope_repo
    with pytest.raises(S.C.C269Locked,match='EXACT_SIDE'):
        with A.development_authority(1):pytest.fail('LONG issued')
    with A.development_authority(-1) as cap:
        A.validate_issued(cap)
        assert cap.side==-1 and cap.start_ms==DEV_START and cap.end_ms==END and cap.funding_tail_exclusive_end_ms==END+15001
        assert not list(store.iterdir())
    with pytest.raises(S.C.C269Locked,match='ALREADY_SPENT'):
        with A.development_authority(-1):pytest.fail('replayed SHORT claim')


@pytest.mark.parametrize('attack',['candidate','code','side','lifecycle_kind','review','documents','source','scope'])
def test_bad_own_receipt_refuses_before_any_side_identity(feature_scope_repo,attack):
    root,_,receipt,git=feature_scope_repo
    if attack=='candidate':receipt['candidate_id']=259
    if attack=='code':receipt['research_tree_oid']='0'*40
    if attack=='side':receipt['side_cells']=[1]
    if attack=='lifecycle_kind':receipt['lifecycle_model_scope']={**receipt['lifecycle_model_scope'],'advance_asof_available':True}
    if attack=='review':receipt['independent_review']['reviewed_head']='0'*40
    if attack=='documents':receipt['documents']={}
    if attack=='source':receipt['authority']['manifest']['byte_sha256']='0'*64
    if attack=='scope':receipt['scope']='PAPER_OR_FORWARD'
    (root/A.RECEIPT).write_bytes(A.canonical(receipt));git('add',A.RECEIPT);git('commit','-qm','SYNTHETIC incorrect own scope')
    with pytest.raises(S.C.C269Locked):
        with A.development_authority(-1):pytest.fail('bad scope issued')
    assert not (root/'.git/c269-evaluation').exists()
