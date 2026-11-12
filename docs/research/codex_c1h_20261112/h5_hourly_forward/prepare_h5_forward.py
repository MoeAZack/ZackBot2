"""Prepare immutable prospective H5 study only. Never creates or starts a task."""
import argparse, hashlib, json, time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
R=Path(__file__).resolve().parent
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def check_t0(t0,now):
 if not isinstance(t0,int) or t0%3600000 or t0 != (now//3600000+1)*3600000: raise ValueError('t0 must be the next unopened hourly candle')
def main():
 a=argparse.ArgumentParser();a.add_argument('--t0-ms',required=True,type=int);args=a.parse_args()
 if any((R/n).exists() for n in ('freeze.json','freeze.sha256','cursor.json')): raise RuntimeError('study already frozen; refuse overwrite')
 source=json.loads((Path('F:/ZackBot2_data/C1H-20261112/preregistration.json')).read_text())
 if source['source_head']!='7b5beecb3a964d4ce36aa7187a734093ffe6818a': raise RuntimeError('source head mismatch')
 for n,h in source['source_sha256'].items():
  if sha(R/'source'/n)!=h: raise RuntimeError('accepted source mismatch: '+n)
 if sha(R/'source/h5_hourly_long.py')!='d6b28a448d47412f892e60b41f40f8c19f4020c08a63a7c9a8d6cc51ff2a9948': raise RuntimeError('H5 candidate mismatch')
 now=int(requests.get('https://fapi.binance.com/fapi/v1/time',timeout=20).json()['serverTime']);check_t0(args.t0_ms,now)
 files=['forward.py','prepare_h5_forward.py','testnet-rules.json']+['source/'+n for n in list(source['source_sha256'])+['h5_hourly_long.py']]
 syms=[s+'USDT' for s in ['BTC','ETH','XRP','SOL','DOGE','BNB','ADA','LINK','AVAX']]
 sleeve=dict(key='h5_volume_impulse_long_1h',share=1/12,risk=.024,max_pos=1,sides='long',symbols=syms,mgmt=dict(stop_atr=3,tp_r=4,max_bars=48,be_r=0,trail_atr=0))
 m=dict(id='H5-HOURLY-FWD-20261112',head='5c95508fbc1765705da8698aa1824f5cdf0c3a29',base_head=source['source_head'],frozen_server_ms=now,t0_ms=args.t0_ms,t0_cairo=datetime.fromtimestamp(args.t0_ms/1000,ZoneInfo('Africa/Cairo')).isoformat(),symbols=syms,capital_per_arm=4000,initial_risk_per_sleeve=8,cost_bps_per_executed_side=[7,15,23],funding='1bp/8h stress proxy both sides, not native funding',fill_model='hypothetical solo research book; no claim of live parity or independent P&L',arms=[dict(id='H5_LONG_SOLO',tf='1h',sleeves=[sleeve])],source_sha256={n:sha(R/'source'/n) for n in list(source['source_sha256'])+['h5_hourly_long.py']},immutable_sha256={n:sha(R/n) for n in files})
 now2=int(requests.get('https://fapi.binance.com/fapi/v1/time',timeout=20).json()['serverTime']);check_t0(args.t0_ms,now2)
 with (R/'freeze.json').open('x',encoding='utf-8') as f: json.dump(m,f,indent=2)
 with (R/'freeze.sha256').open('x') as f:f.write(sha(R/'freeze.json'))
 with (R/'cursor.json').open('x') as f:json.dump({'next_ms':args.t0_ms},f)
 print(json.dumps({'frozen':m['id'],'t0_cairo':m['t0_cairo'],'freeze_sha256':sha(R/'freeze.json'),'next_ms':args.t0_ms,'baseline':'pending first boundary; zero closed trades must be verified','task_created':False,'runner_started':False}))
if __name__=='__main__':main()
