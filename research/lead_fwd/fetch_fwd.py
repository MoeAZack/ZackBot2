"""LEADFWD-01 forward data fetch (owner Windows PC; public Binance USDT-M REST, no key). RESEARCH ONLY.

Downloads CLOSED 1h klines from the frozen forward start (freeze.json forward_fetch.start_ms) up to the boundary
B = latest 00:00 UTC at or before now (or --boundary-ms, which must itself be a past 00:00 UTC) for BTCUSDT + the 165
frozen coins, into <out>/<B_ms>/<SYM>_1h.csv (t,o,h,l,c,v,qv; t = open ms) and writes <out>/<B_ms>/fetch_manifest.json
with each file's sha256. Standard library only. A finished boundary folder is never rewritten.
No account, key, order, scheduler or runtime action.
  python research/lead_fwd/fetch_fwd.py <out_dir> [--boundary-ms MS]
"""
import sys, os, json, time, hashlib, urllib.request, urllib.error, urllib.parse, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
FREEZE = os.path.join(HERE, 'freeze.json')
BASE = 'https://fapi.binance.com'
H = 3600000
DAY = 86400000


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


def get(path, params=None, tries=6):
    url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'research-fetch'}), timeout=30) as r:
                used = r.headers.get('X-MBX-USED-WEIGHT-1M')
                body = json.loads(r.read().decode('utf-8'))
                if used and int(used) > 1800: time.sleep(20)
                return body
        except urllib.error.HTTPError as e:
            if e.code == 400: return None            # unknown / delisted symbol: no data, recorded as such
            if k == tries - 1: raise
            time.sleep(5 * (k + 1))
        except Exception:
            if k == tries - 1: raise
            time.sleep(5 * (k + 1))


def closed_rows(klines, end):
    """Keep only bars that opened before `end` AND whose close time is before `end` (closed at the boundary)."""
    return [(int(x[0]), x[1], x[2], x[3], x[4], x[5], x[7]) for x in klines if int(x[0]) < end and int(x[6]) < end]


def fetch(sym, start, end, path):
    rows, t = [], start
    while t < end:
        k = get('/fapi/v1/klines', dict(symbol=sym, interval='1h', startTime=t, endTime=end - 1, limit=1500))
        if not k: break
        rows += closed_rows(k, end)
        nt = int(k[-1][0]) + H
        if nt <= t: break
        t = nt
        time.sleep(0.25)
    seen, clean = set(), []
    for r in sorted(rows):
        if r[0] not in seen: seen.add(r[0]); clean.append(r)
    tmp = path + '.part'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write('t,o,h,l,c,v,qv\n')
        for r in clean: f.write(','.join(str(x) for x in r) + '\n')
    os.replace(tmp, path)
    return len(clean), (clean[0][0] if clean else None), (clean[-1][0] if clean else None)


SETTLE_MS = 600000      # wait 10 minutes after B so the last 1h kline before B is final


def boundary(now_ms):
    """Latest 00:00 UTC whose last kline has settled (B + 10 min <= now)."""
    t = now_ms - SETTLE_MS
    return t - t % DAY


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out'); ap.add_argument('--boundary-ms', type=int)
    a = ap.parse_args()
    fz = json.load(open(FREEZE, encoding='utf-8'))
    now = int(time.time() * 1000)
    B = a.boundary_ms if a.boundary_ms is not None else boundary(now)
    if B % DAY or B + SETTLE_MS > now: raise SystemExit('boundary must be a 00:00 UTC at least 10 minutes in the past (last kline settled)')
    start = fz['forward_fetch']['start_ms']
    coins = ['BTCUSDT'] + sorted(fz['universe']['fresh134'] + fz['universe']['extra31'])
    d = os.path.join(a.out, str(B))
    if os.path.exists(os.path.join(d, 'fetch_manifest.json')): raise SystemExit(f'{d} already finished; boundary folders are never rewritten')
    os.makedirs(d, exist_ok=True)
    man = {}
    for i, s in enumerate(coins):
        p = os.path.join(d, f'{s}_1h.csv')
        n, f0, f1 = fetch(s, start, B, p)
        man[s] = dict(sha256=sha(p), rows=n, first_ms=f0, last_ms=f1)
        print(i, s, n, flush=True)
    json.dump(dict(id='LEADFWD-01', freeze_sha256=sha(FREEZE), boundary_ms=B, start_ms=start, fetched_at_ms=now,
                   source='fapi.binance.com /fapi/v1/klines 1h REST, closed bars only', symbols=man),
              open(os.path.join(d, 'fetch_manifest.json'), 'w', encoding='utf-8'), indent=1)
    print('DONE', B, len(man), flush=True)


if __name__ == '__main__':
    main()
