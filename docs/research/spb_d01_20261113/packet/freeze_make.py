import hashlib,json,pathlib,datetime
P=pathlib.Path('F:/ZackBot2_data/SPB-D01-BUILD-20261113'); S=pathlib.Path('F:/ZackBot2_data/SPOTFLOW-D01-20261112'); C=pathlib.Path('C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112/clean'); H=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
m=json.loads((S/'data_manifest_frozen.json').read_text()); files=[P/'contract.md',P/'spb_signals.py',P/'spb_run.py',P/'test_spb.py',S/'data_validation.json',S/'data_manifest_frozen.json',S/'harness/data/exchange_rules_testnet.json',pathlib.Path('F:/ZackBot2_data/SPB-D01-20261113/independent_input_audit.json')]
files += list((S/'harness/research').rglob('*.py'))
files += [S/(s+'-1h-'+month+'.zip') for s in m['symbols'] for month in m['months']]
files += [C/(s+'_1h.csv') for s in m['symbols']]
files=[x for x in files if x.exists()]
receipt={'id':'SPB-D01','state':'FROZEN_PRE_OUTCOME','files':{str(p):H(p) for p in files},'notes':'one fixed rule; official spot archives; canonical futures; 5 synthetic tests passed; prior study search is bounded to available ledgers; no outcomes read'}
(P/'freeze.json').write_text(json.dumps(receipt,indent=2)+'\n')
(P/'trial_ledger.json').write_text(json.dumps({'id':'SPB-D01','trial_count':1,'status':'FROZEN_PRE_OUTCOME','freeze_sha256':H(P/'freeze.json'),'multiple_testing':'one trial; no thresholds, coins, sides or holdout tuning'},indent=2)+'\n')
print('freeze',H(P/'freeze.json'),'files',len(files)); print('contract',H(P/'contract.md'));print('signal',H(P/'spb_signals.py')); print('runner',H(P/'spb_run.py'));print('tests',H(P/'test_spb.py'))
