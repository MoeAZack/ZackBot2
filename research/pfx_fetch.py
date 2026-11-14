"""Fetch official USD-M monthly premiumIndexKlines 1h, original9, 2024-10..2026-09 (216 ZIPs + CHECKSUMs). Research only.
Bounded: <=4 workers, <=3 retries on transient errors, stop on 403/418/429 or checksum mismatch, hard 16 MiB cap."""
import hashlib, os, sys, time, json, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
B = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/data/futures/um/monthly/premiumIndexKlines'
COINS = ['ADAUSDT','AVAXUSDT','BNBUSDT','BTCUSDT','DOGEUSDT','ETHUSDT','LINKUSDT','SOLUSDT','XRPUSDT']
MONTHS = [f'{y}-{m:02d}' for y in (2024, 2025, 2026) for m in range(1, 13) if '2024-10' <= f'{y}-{m:02d}' <= '2026-09']
CAP = 16 * 2**20
def get(url):
    for k in range(4):
        try: return urllib.request.urlopen(url, timeout=60).read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 418, 429): raise SystemExit(f'STOP {e.code} {url}')
            if k == 3: raise
        except Exception:
            if k == 3: raise
        time.sleep(2 * (k + 1))
def one(job):
    s, m = job; n = f'{s}-1h-{m}.zip'; u = f'{B}/{s}/1h/{n}'
    z = get(u); c = get(u + '.CHECKSUM').decode().split()[0]
    if hashlib.sha256(z).hexdigest() != c: raise SystemExit(f'STOP checksum {n}')
    return s, n, z, c
out = sys.argv[1]; tot = 0; man = {}
with ThreadPoolExecutor(4) as ex:
    for s, n, z, c in ex.map(one, [(s, m) for s in COINS for m in MONTHS]):
        tot += len(z)
        if tot > CAP: raise SystemExit('STOP cap')
        os.makedirs(f'{out}/{s}', exist_ok=True); open(f'{out}/{s}/{n}', 'wb').write(z); open(f'{out}/{s}/{n}.CHECKSUM', 'w', encoding='utf-8').write(f'{c}  {n}\n')
        man[f'{s}/{n}'] = dict(sha256=c, bytes=len(z))
print(len(man), tot)
json.dump(dict(source=B, files=man, total_bytes=tot), open(f'{out}/fetch_manifest.json', 'w', encoding='utf-8'), indent=1, sort_keys=True)
