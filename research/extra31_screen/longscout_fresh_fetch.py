"""LONGSCOUT-FRESH-V1 data fetch (Windows; public Binance USDT-M REST, no key). RESEARCH ONLY.

Applies the selection rule frozen in longscout_fresh_v1.json to the live exchangeInfo, writes the selected list FIRST,
then downloads closed 1h klines for BTC + every selected coin into <out>/clean/<SYM>_1h.csv (t,o,h,l,c,v,qv; t = open
ms) and writes <out>/input_manifest.json with each file's sha256. Standard library only. Resumable: a finished file is
kept (its sha recorded). No account, key, order or runtime action.
  python longscout_fresh_fetch.py <freeze_json> <out_dir>
"""
import sys, os, json, time, hashlib, urllib.request, urllib.parse

BASE = 'https://fapi.binance.com'


def get(path, params=None, tries=6):
    url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'research-fetch'}), timeout=30) as r:
                used = r.headers.get('X-MBX-USED-WEIGHT-1M')
                body = json.loads(r.read().decode('utf-8'))
                if used and int(used) > 1800: time.sleep(20)
                return body
        except Exception as e:
            if k == tries - 1: raise
            time.sleep(5 * (k + 1))


def select(info, rule):
    used = set(rule['exclude_symbols'])
    used_bases = {s[:-4].replace('1000', '') for s in used}
    stable = set(rule['exclude_bases'])
    cut = rule['onboard_before_ms']
    out = []
    for s in info['symbols']:
        b = s['baseAsset']
        if (s.get('contractType') == 'PERPETUAL' and s.get('quoteAsset') == 'USDT' and s.get('status') == 'TRADING'
                and s.get('underlyingType', 'COIN') == 'COIN' and int(s.get('onboardDate', 0)) < cut
                and s['symbol'] not in used and b.replace('1000', '').replace('1M', '') not in used_bases and b not in stable):
            out.append(s['symbol'])
    return sorted(out)


def fetch(sym, start, end, path):
    rows, t = [], start
    while t < end:
        k = get('/fapi/v1/klines', dict(symbol=sym, interval='1h', startTime=t, endTime=end - 1, limit=1500))
        if not k: break
        for x in k:
            if int(x[0]) < end and int(x[6]) < end + 3600000: rows.append((int(x[0]), x[1], x[2], x[3], x[4], x[5], x[7]))
        nt = int(k[-1][0]) + 3600000
        if nt <= t: break
        t = nt
        time.sleep(0.3)
    seen, clean = set(), []
    for r in sorted(rows):
        if r[0] not in seen: seen.add(r[0]); clean.append(r)
    tmp = path + '.part'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write('t,o,h,l,c,v,qv\n')
        for r in clean: f.write(','.join(str(x) for x in r) + '\n')
    os.replace(tmp, path)
    return len(clean)


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


def main():
    fz, out = sys.argv[1], sys.argv[2]
    rule = json.load(open(fz, encoding='utf-8'))['selection']
    os.makedirs(os.path.join(out, 'clean'), exist_ok=True)
    info = get('/fapi/v1/exchangeInfo')
    json.dump(info, open(os.path.join(out, 'exchangeInfo.json'), 'w', encoding='utf-8'))
    syms = select(info, rule)
    json.dump(dict(selected=syms, count=len(syms), server_time=info.get('serverTime'), freeze_sha256=sha(fz)),
              open(os.path.join(out, 'selected.json'), 'w', encoding='utf-8'), indent=1)
    print('selected', len(syms), flush=True)
    man = {}
    for i, s in enumerate(['BTCUSDT'] + syms):
        p = os.path.join(out, 'clean', f'{s}_1h.csv')
        n = None
        if not os.path.exists(p):
            n = fetch(s, rule['start_ms'], rule['end_ms'], p)
        man[s] = dict(sha256=sha(p), rows=n if n is not None else sum(1 for _ in open(p, encoding='utf-8')) - 1)
        print(i, s, man[s]['rows'], flush=True)
    json.dump(dict(id='LONGSCOUT-FRESH-V1', freeze_sha256=sha(fz), source='fapi.binance.com /fapi/v1/klines 1h REST', symbols=man),
              open(os.path.join(out, 'input_manifest.json'), 'w', encoding='utf-8'), indent=1)
    print('DONE', len(man), flush=True)


if __name__ == '__main__':
    main()
