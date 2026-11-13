"""Local, read-only cross-account overview. No trading/configuration forwarding."""
import concurrent.futures,hashlib,hmac,http.cookies,json,os,secrets,subprocess,sys,threading,urllib.request,urllib.parse,webbrowser
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
ROOT=Path(__file__).resolve().parent; BASE=ROOT.parent;PORT=8769;ORIGIN=f'http://127.0.0.1:{PORT}'
ACCOUNTS={1:('Current cohort','cohort_launch_20261108',8765),2:('OLD3','OLD3-account2',8766),3:('Profit protection','ACCOUNT3-breakeven',8767),4:('Exploration · low confidence','EXPLORATION-account4',8768)}
SESSION=ROOT/'hub-session.json'
def load_token():
 if SESSION.exists():return json.loads(SESSION.read_text())['token']
 token=secrets.token_urlsafe(32);SESSION.write_text(json.dumps({'token':token}));return token
TOKEN=load_token()
def browser():
 for env in ['PROGRAMFILES(X86)','PROGRAMFILES','LOCALAPPDATA']:
  for tail in ['Microsoft/Edge/Application/msedge.exe','Google/Chrome/Application/chrome.exe']:
   p=Path(os.environ.get(env,''))/tail
   if p.is_file():return str(p)
 raise RuntimeError('browser_unavailable')
def launch(url,profile):
 subprocess.Popen([browser(),f'--app={url}',f'--user-data-dir={profile}','--window-size=1480,960','--no-first-run','--disable-extensions','--disable-sync','--no-default-browser-check'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def session(n):
 name,folder,port=ACCOUNTS[n];data=BASE/folder/'runtime/ZackBot'
 s=json.loads((data/'session.json').read_text());assert int(s['port'])==port
 return s,data,port
def request_status(n):
 s,data,port=session(n)
 r=urllib.request.Request(f'http://127.0.0.1:{port}/api/status',headers={'X-ZB-Token':s['token'],'Origin':f'http://127.0.0.1:{port}'})
 with urllib.request.urlopen(r,timeout=6) as response:o=json.load(response)
 if o.get('mode')!='PAPER':raise ValueError('unconfirmed_testnet')
 receipt=json.loads((data/'install.json').read_text());account=receipt.get('account',{})
 if account.get('mode')!='paper' or urllib.parse.urlsplit(account.get('base','')).hostname not in ['testnet.binancefuture.com','demo-fapi.binance.com']:raise ValueError('unconfirmed_testnet')
 return o
SAFE_LOT=['symbol','side','sleeve','qty','avg','stop','opened','manual','tf','risk_usd','protected','stop_state','mark','pnl','r','pnl_status']
def account_snapshot(n):
 name,folder,port=ACCOUNTS[n];row={'id':n,'name':name,'port':port}
 try:
  o=request_status(n);c=o['capital'];health=o.get('health',{})
  row.update(connected=True,observed=datetime.now(timezone.utc).isoformat(),paused=bool(o['settings']['ENTRIES_PAUSED']),engine=health.get('engine'),exchange=health.get('exchange'),unprotected=len(health.get('unprotected',[])),sizing=c.get('sizing'),realized=c.get('realized'),unrealized=c.get('unrealized'),since=c.get('since'),unmeasured_closed=c.get('realized_unmeasured_n',0),unmeasured_open=c.get('unrealized_unmeasured_n',0),lots=[{k:l.get(k) for k in SAFE_LOT} for l in o.get('lots',[])],symbols=len(o['settings'].get('UNIVERSE',[])),last_cycle=health.get('last_cycle_ok',{}))
 except Exception as ex:row.update(connected=False,error='Testnet identity could not be confirmed' if isinstance(ex,ValueError) else 'Account unavailable; figures are not current')
 return row
def overview():
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(account_snapshot,ACCOUNTS))
 return {'accounts':rows,'observed':datetime.now(timezone.utc).isoformat()}
HEADERS={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"}
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,code,obj,html=False,extra=None):
  b=(obj if html else json.dumps(obj)).encode();self.send_response(code)
  for k,v in {**HEADERS,**(extra or {}),'Content-Type':'text/html; charset=utf-8' if html else 'application/json','Content-Length':str(len(b))}.items():self.send_header(k,v)
  self.end_headers();self.wfile.write(b)
 def auth(self):
  try:cookie=http.cookies.SimpleCookie();cookie.load(self.headers.get('Cookie',''));v=cookie.get('zb_hub8769');return bool(v) and hmac.compare_digest(v.value,TOKEN)
  except Exception:return False
 def host(self):return self.headers.get('Host')==f'127.0.0.1:{PORT}'
 def do_GET(self):
  if not self.host():return self.send(421,{})
  u=urllib.parse.urlsplit(self.path);t=urllib.parse.parse_qs(u.query).get('t',[''])[0]
  if u.path=='/' and t and hmac.compare_digest(t,TOKEN):return self.send(303,{},extra={'Location':'/','Set-Cookie':f'zb_hub8769={TOKEN}; HttpOnly; SameSite=Strict; Path=/'})
  if u.path=='/api/ping':
   nonce=urllib.parse.parse_qs(u.query).get('nonce',[''])[0][:64];return self.send(200,{'app':'zackbot-hub','proof':hmac.new(TOKEN.encode(),nonce.encode(),hashlib.sha256).hexdigest()})
  if not self.auth():return self.send(401,{'error':'Open ZackBot All Accounts from its desktop shortcut.'})
  if u.path=='/':return self.send(200,(ROOT/'index.html').read_text(encoding='utf-8'),True)
  if u.path=='/api/overview':return self.send(200,overview())
  return self.send(404,{})
 def do_POST(self):
  if not self.host():return self.send(421,{})
  if not self.auth() or self.headers.get('Origin')!=ORIGIN:return self.send(403,{})
  if self.path!='/api/open-account':return self.send(404,{})
  try:
   length=int(self.headers.get('Content-Length',0));assert 0<length<100
   data=json.loads(self.rfile.read(length));assert set(data)=={'account'};n=data['account'];assert type(n)==int and n in ACCOUNTS
   request_status(n) # refuse an unconfirmed environment
   s,folder,port=session(n);launch(f'http://127.0.0.1:{port}/?t='+s['token'],folder/'app-window')
   return self.send(200,{'ok':True})
  except Exception:return self.send(400,{'error':'Could not open this account. Check its connection first.'})
def main():
 try:server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
 except OSError:
  nonce=secrets.token_hex(16)
  try:
   with urllib.request.urlopen(ORIGIN+'/api/ping?nonce='+nonce,timeout=3) as response:proof=json.load(response)
   if proof.get('app')!='zackbot-hub' or not hmac.compare_digest(proof.get('proof',''),hmac.new(TOKEN.encode(),nonce.encode(),hashlib.sha256).hexdigest()):raise RuntimeError('port_identity_mismatch')
  except Exception:raise SystemExit(1)
  if '--no-window' not in sys.argv:launch(ORIGIN+'/?t='+TOKEN,ROOT/'browser-profile')
  return
 if '--no-window' not in sys.argv:threading.Timer(.5,lambda:launch(ORIGIN+'/?t='+TOKEN,ROOT/'browser-profile')).start()
 server.serve_forever()
if __name__=='__main__':main()
