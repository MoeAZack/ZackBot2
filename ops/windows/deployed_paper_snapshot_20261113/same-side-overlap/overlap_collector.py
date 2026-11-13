import csv, itertools, json, pathlib, sys, urllib.request, urllib.parse
from datetime import datetime, timezone

ROOT = pathlib.Path(r'F:\ZackBot2_data')
OUT = ROOT / 'same-side-overlap'
ACCOUNTS = {
    '1': ('cohort_launch_20261108', 8765, '2026-11-08T04:19:24+00:00'),
    '2': ('OLD3-account2', 8766, '2026-11-12T06:01:00+00:00'),
    '3': ('ACCOUNT3-breakeven', 8767, '2026-11-12T08:20:47+00:00'),
    '4': ('EXPLORATION-account4', 8768, '2026-11-12T06:10:00+00:00'),
}

def dt(s):
    x = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if x.tzinfo is None: raise ValueError('timestamp lacks timezone')
    return x.astimezone(timezone.utc)

def get(root, port, endpoint):
    runtime = ROOT / root / 'runtime/ZackBot'
    receipt = json.loads((runtime / 'install.json').read_text(encoding='utf-8'))
    account = receipt.get('account')
    if not isinstance(account, dict): raise ValueError('unconfirmed testnet receipt')
    base = account.get('base')
    mode = account.get('mode')
    if not isinstance(base, str) or not isinstance(mode, str): raise ValueError('unconfirmed testnet receipt')
    url = urllib.parse.urlsplit(base)
    if mode.strip().upper() != 'PAPER' or url.scheme.lower() != 'https' or url.hostname not in ('testnet.binancefuture.com', 'demo-fapi.binance.com') or url.username is not None or url.password is not None:
        raise ValueError('unconfirmed testnet receipt')
    session = json.loads((runtime / 'session.json').read_text(encoding='utf-8'))
    if session.get('port') != port: raise ValueError('session port mismatch')
    req = urllib.request.Request(f'http://127.0.0.1:{port}/api/{endpoint}', headers={'X-ZB-Token': session['token']}, method='GET')
    with urllib.request.urlopen(req, timeout=20) as response: return json.load(response)

def record(row, closed):
    if row.get('manual') or str(row.get('sleeve', '')).upper() in ('MAN', 'MANUAL', ''): return None
    ident = row.get('id') if closed else row.get('key')
    sleeve, symbol, side = (str(row.get(k) or '').strip().upper() for k in ('sleeve', 'symbol', 'side'))
    if not ident or not symbol or side not in ('LONG', 'SHORT'): raise ValueError('missing trade identity, symbol or side')
    opened = dt(row['opened']); end = dt(row['closed']) if closed else None
    if end is not None and end < opened: raise ValueError('close precedes open')
    return dict(id=str(ident), sleeve=sleeve, symbol=symbol, side=side, opened=opened.isoformat(), closed=end.isoformat() if end else None)

def calculate(records, bound, now):
    grouped = {}
    for r in records:
        start, end = max(dt(r['opened']), bound), min(dt(r['closed']) if r['closed'] else now, now)
        if start >= end: continue
        grouped.setdefault((r['symbol'], r['side']), []).append((r, start, end))
    episodes, pairs = [], []
    for (symbol, side), trades in sorted(grouped.items()):
        for a, b in itertools.combinations(trades, 2):
            if a[0]['sleeve'] == b[0]['sleeve']: continue
            start, end = max(a[1], b[1]), min(a[2], b[2])
            if start < end:
                pairs.append(dict(symbol=symbol, side=side, trade_ids=sorted([a[0]['id'], b[0]['id']]), sleeves=sorted([a[0]['sleeve'], b[0]['sleeve']]), start=start.isoformat(), end=end.isoformat(), seconds=(end-start).total_seconds(), right_censored=not a[0]['closed'] and not b[0]['closed'] and end == now))
        points = sorted({x for _, start, end in trades for x in (start, end)})
        for start, end in zip(points, points[1:]):
            active = [r for r, a, b in trades if a <= start and b >= end]
            sleeves = sorted({r['sleeve'] for r in active})
            if len(sleeves) < 2: continue
            if episodes and episodes[-1]['symbol'] == symbol and episodes[-1]['side'] == side and episodes[-1]['end'] == start.isoformat():
                e = episodes[-1]; e['end'] = end.isoformat(); e['seconds'] += (end-start).total_seconds(); e['sleeves'] = sorted(set(e['sleeves']) | set(sleeves))
            else:
                episodes.append(dict(symbol=symbol, side=side, start=start.isoformat(), end=end.isoformat(), seconds=(end-start).total_seconds(), sleeves=sleeves))
        for e in episodes:
            if e['symbol'] == symbol and e['side'] == side:
                e['left_censored'] = dt(e['start']) == bound
                e['right_censored'] = dt(e['end']) == now
    return episodes, pairs

def run():
    OUT.mkdir(exist_ok=True)
    state_path = OUT / 'state.json'
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {'accounts': {}}
    results = []
    for account, (root, port, start) in ACCOUNTS.items():
        prior = state['accounts'].setdefault(account, {'closed': {}, 'last_open': {}, 'unresolved': {}})
        try:
            status = get(root, port, 'status'); now = datetime.now(timezone.utc); history = get(root, port, 'history')
            if status.get('mode') != 'PAPER' or not isinstance(status.get('lots'), list) or not isinstance(history.get('history'), list): raise ValueError('unexpected mode or response')
            opened, errors = {}, 0
            for row in status['lots']:
                try:
                    r = record(row, False)
                    if r: opened[r['id']] = r
                except (KeyError, TypeError, ValueError): errors += 1
            for row in history['history']:
                try:
                    r = record(row, True)
                    if r: prior['closed'][r['id']] = r
                except (KeyError, TypeError, ValueError): errors += 1
            for ident, r in prior['last_open'].items():
                if ident not in opened and ident not in prior['closed']: prior['unresolved'][ident] = r
            for ident in list(prior['unresolved']):
                if ident in opened or ident in prior['closed']: del prior['unresolved'][ident]
            prior['last_open'] = opened
            records = list(prior['closed'].values()) + [v for k,v in opened.items() if k not in prior['closed']]
            episodes, pairs = calculate(records, dt(start), now)
            groups = {}
            for r in opened.values():
                if dt(r['opened']) > now: continue
                groups.setdefault((r['symbol'], r['side']), []).append(r)
            current = [dict(symbol=s, side=d, sleeves=sorted({r['sleeve'] for r in rows}), trade_ids=sorted(r['id'] for r in rows)) for (s,d),rows in sorted(groups.items()) if len({r['sleeve'] for r in rows}) >= 2]
            results.append(dict(account=int(account), as_of_utc=now.isoformat(), bound_utc=start, current_groups=current, episodes=episodes, episode_count=len(episodes), overlap_seconds=sum(x['seconds'] for x in episodes), trade_pair_count=len(pairs), trade_pairs=pairs, history_rows_returned=len(history['history']), history_cap=1500, invalid_records=errors, unresolved_disappeared_open_count=len(prior['unresolved']), coverage='Observed records only; prior missing or truncated history cannot be reconstructed. Current open intervals are right-censored; intervals crossing the bound are left-censored. Two endpoint reads are not atomic.'))
            prior['last_success_utc'] = now.isoformat()
        except Exception as exc:
            results.append(dict(account=int(account), bound_utc=start, error=type(exc).__name__, coverage='Unavailable; prior state preserved.'))
    output = dict(schema_version=1, generated_utc=datetime.now(timezone.utc).isoformat(), definition='Per account and symbol/side, contiguous time with at least two distinct non-manual sleeves. Trade pairs are unique pairs of different sleeves with positive overlap. Never compare across accounts.', accounts=results)
    for name, obj in [('state.json', state), ('review_metrics.json', output)]:
        temp = OUT / (name + '.tmp'); temp.write_text(json.dumps(obj, indent=2), encoding='utf-8'); temp.replace(OUT / name)
    with (OUT/'summary.csv').open('w', newline='', encoding='utf-8') as f:
        fields=['account','as_of_utc','bound_utc','current_group_count','episode_count','overlap_seconds','trade_pair_count','history_rows_returned','invalid_records','unresolved_disappeared_open_count','error']
        w=csv.DictWriter(f, fieldnames=fields, extrasaction='ignore'); w.writeheader()
        for r in results: w.writerow({**r, 'current_group_count':len(r.get('current_groups',[])) if 'error' not in r else ''})
    print(json.dumps([{k:r.get(k) for k in ('account','as_of_utc','current_groups','episode_count','overlap_seconds','trade_pair_count','history_rows_returned','error')} for r in results], indent=2))
    return int(any('error' in r for r in results))

if __name__ == '__main__': sys.exit(run())
