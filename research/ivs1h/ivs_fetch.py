"""Fetch official BTCBVOLUSDT daily BVOLIndex ZIPs pinned in expected_manifest.json (ruling 82f70688). Research only, public data.
Bounded: <=4 workers, <=3 transient retries, stop on 403/418/429, checksum mismatch, listed-size mismatch, or 512 MiB total.
Writes raw ZIP + CHECKSUM to <cache>; nothing raw goes into Git. Usage: python -I ivs_fetch.py <manifest> <cache> <fetch_manifest_out>"""
import hashlib, json, os, sys, time, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
CAP = 512 * 2**20
LOCK = threading.Lock(); TOT = [0]
def get(url):
    for k in range(4):
        try: return urllib.request.urlopen(url, timeout=120).read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 418, 429): raise SystemExit(f'STOP {e.code} {url}')
            if k == 3: raise
        except Exception:
            if k == 3: raise
        time.sleep(2 * (k + 1))
def one(item):
    d, f = item; name = f['zip_url'].rsplit('/', 1)[1]; p = os.path.join(CACHE, name)
    with LOCK:
        TOT[0] += f['zip_listed_bytes']
        if TOT[0] > CAP: raise SystemExit('STOP cap')
    if os.path.exists(p) and os.path.exists(p + '.CHECKSUM'):
        z = open(p, 'rb').read(); c = open(p + '.CHECKSUM', encoding='utf-8').read().split()[0]
    else:
        z = get(f['zip_url']); c = get(f['checksum_url']).decode().split()[0]
    h = hashlib.sha256(z).hexdigest()
    if h != c: raise SystemExit(f'STOP checksum {name}')
    if len(z) != f['zip_listed_bytes']: raise SystemExit(f'STOP size {name}')
    open(p + '.part', 'wb').write(z); os.replace(p + '.part', p)
    open(p + '.CHECKSUM', 'w', encoding='utf-8').write(f'{c}  {name}\n')
    return d, dict(file=name, sha256=h, bytes=len(z))
if __name__ == '__main__':
    M = json.load(open(sys.argv[1], encoding='utf-8')); CACHE = sys.argv[2]; os.makedirs(CACHE, exist_ok=True)
    out = {}
    with ThreadPoolExecutor(4) as ex:
        for d, r in ex.map(one, sorted(M['files'].items())): out[d] = r
    assert sorted(out) == sorted(M['files'])
    json.dump(dict(manifest_dates=len(out), total_bytes=sum(r['bytes'] for r in out.values()), missing_dates=M['missing_dates'], files=out),
              open(sys.argv[3], 'w', encoding='utf-8'), indent=1, sort_keys=True)
    print(len(out), sum(r['bytes'] for r in out.values()))
