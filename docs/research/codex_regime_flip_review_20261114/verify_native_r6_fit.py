from pathlib import Path
import subprocess,hashlib,json,io
import numpy as np
import pandas as pd
R=Path(r'F:\ZackBot2_data\C1H-20261112\review-branch'); H='60c0a1c5933b371d36ad0a011f3130d228b8fe51'; P='docs/research/r6_fit163_native_confirm_20261114/'
def blob(p): return subprocess.check_output(['git','show',H+':'+p],cwd=R)
verified=[]
for line in blob(P+'SHA256SUMS').decode().splitlines():
    sha,name=line.split(maxsplit=1); name=name.strip(); p='research/extra31_screen/r6_fit163_confirm.py' if name.startswith('../') else P+name
    b=blob(p); variants=[b] if name.endswith('.gz') else [b,b.replace(b'\r\n',b'\n'),b.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')]
    assert sha in [hashlib.sha256(x).hexdigest() for x in variants],name; verified.append(name)
fz='docs/research/regime_flip_testnet_fit_20261114/'
for name in ('freeze.json','rules.json'):
    assert json.loads(blob('research/extra31_screen/r6_fit163/'+name))==json.loads((R/fz/name).read_text()),name
native=json.loads(blob(P+'r6_regime_flip_4h_result.json'))
ownroot=Path(r'F:\ZackBot2_data\REGIME-FLIP-REVIEW-20261114\fit\own_cash'); own=json.loads((ownroot/'per_order_stats.json').read_text())
maxdiff=0.
for a,b in zip(own,native['per_order']['path']):
    for key in ('n','meanR15','meanR23','meanR15_early','meanR15_late','meanR15_ex_best'):
        maxdiff=max(maxdiff,abs(a[key]-b[key])); assert np.isclose(a[key],b[key],atol=1e-10,rtol=1e-10),(key,a[key],b[key])
nt=pd.read_csv(io.BytesIO(blob(P+'r6_regime_flip_4h_order0_path_trades.csv'))); ot=pd.read_csv(ownroot/'order0_own_trades.csv')
assert list(zip(nt.sym,nt.side,nt.i_in,nt.i_out,nt.why))==list(zip(ot.sym,ot.side,ot.i_in,ot.i_out,ot.why))
cashdiff={k:float(max(abs(nt[k]-ot[k]))) for k in ('pnl','R','pnl15','pnl23','turnover')}; assert max(cashdiff.values())<1e-8
assert native['fit163']['status']=='FIT_PASS'
result=dict(head=H,hashes_verified=len(verified),freeze_rules_match=True,orderings_compared=len(own),per_order_fields=6,max_stat_difference=maxdiff,trades_compared=len(nt),max_cash_differences=cashdiff,verdict='NATIVE_CONFIRMATION_ACCEPTED for deployment-fit economic gate; no runtime/activation approval',scope='Reviewed actual native wrapper; compares separate independent simulator to original-alpha/native-engine outputs at exact head. Current-filter fit is dependent, consumed-calendar, tail-concentrated. No rerun or account action.')
O=Path(r'F:\ZackBot2_data\REGIME-FLIP-REVIEW-20261114'); (O/'native_fit_acceptance.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8',newline='\n'); print(json.dumps(result,indent=2))
