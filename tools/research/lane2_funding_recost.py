"""Fixed-book funding re-costing; no strategy tuning or equity-feedback re-sizing.

Consumes independently baseline-matched replay tapes and cached public events.
Primary clock: ordinary taker entries/gap fills at candle open; signal/time exits
at last millisecond; other intrabar fills at open. 'late' moves uncertain fills to
last millisecond. Exact exchange fills are not claimed by this candle replay.
"""
import argparse
from bisect import bisect_left, bisect_right
import gzip
import hashlib
import json
import math
from pathlib import Path

ORIGINAL_LUNC = {1673136000015, 1673164800000, 1673193600010, 1673222400003}


def fill_time(fill, bar_ms, timing):
    t = int(fill['time_ms'])
    kind, why = fill['kind'], fill.get('why')
    if fill.get('gap'):
        return t
    if kind == 'entry' and why != 'trail_entry' and fill.get('liq', 'taker') == 'taker':
        return t
    if kind == 'exit' and why in ('time', 'signal'):
        return t + bar_ms - 1
    return t + (bar_ms - 1 if timing == 'late' else 0)


def quantities(trade, events, bar_ms, timing='early', before_equal=False):
    fills = [(fill_time(f, bar_ms, timing), i,
              float(f['qty']) * (1 if f['kind'] in ('entry', 'add', 'dca') else -1))
             for i, f in enumerate(trade['fills'])]
    fills.sort()
    if not fills:
        raise ValueError('trade without fills')
    q, k, out = 0.0, 0, []
    tolerance = max(1e-8, max(abs(x[2]) for x in fills) * 1e-10)
    for event in events:
        stamp = event['fundingTime']
        while k < len(fills) and (fills[k][0] < stamp if before_equal else fills[k][0] <= stamp):
            q += fills[k][2]
            if q < -tolerance:
                raise ValueError('negative reconstructed position quantity')
            if abs(q) <= tolerance:
                q = 0.0
            k += 1
        out.append(q)
    return out


def recost_trade(trade, events, bar_ms, proxies, allow_all=False):
    risk = float(trade['risk'])
    if not math.isfinite(risk) or risk <= 0:
        raise ValueError('invalid trade risk')
    side = int(trade['side'])
    if side not in (-1, 1):
        raise ValueError('invalid side')
    primary = quantities(trade, events, bar_ms, 'early')
    late = quantities(trade, events, bar_ms, 'late')
    boundary = quantities(trade, events, bar_ms, 'early', True)
    cash, low, high, late_cash, boundary_cash = [], [], [], [], []
    used, missing = [], []
    for event, qty, late_qty, boundary_qty in zip(events, primary, late, boundary):
        if not any((qty, late_qty, boundary_qty)):
            continue
        key = (trade['sym'], event['fundingTime'])
        rate = float(event['fundingRate'])
        mark = event.get('markPrice')
        if mark in ('', None):
            proxy = proxies.get(key)
            authorized = allow_all or (key[0] == '1000LUNCUSDT' and key[1] in ORIGINAL_LUNC)
            if proxy is None or not authorized:
                missing.append(dict(symbol=key[0], funding_time_ms=key[1], reason='missing settlement mark without approved/cached proxy'))
                continue
            mark, lo, hi = map(float, (proxy['value'], proxy['low'], proxy['high']))
            used.append(dict(symbol=key[0], funding_time_ms=key[1], primary_qty=qty,
                             late_qty=late_qty, boundary_qty=boundary_qty,
                             primary_cash=-side * qty * mark * rate))
        else:
            mark = lo = hi = float(mark)
        coeff = -side * qty * rate
        cash.append(coeff * mark)
        bounds = (coeff * lo, coeff * hi)
        low.append(min(bounds)); high.append(max(bounds))
        late_cash.append(-side * late_qty * mark * rate)
        boundary_cash.append(-side * boundary_qty * mark * rate)
    complete = not missing
    paid = float(trade['proxy_cash_paid'])
    baseline = float(trade['baseline_pnl'])
    native = math.fsum(cash)
    return dict(trade_id=trade['trade_id'], symbol=trade['sym'], side=side, risk=risk,
                baseline_pnl=baseline, baseline_R=float(trade['baseline_R']),
                removed_proxy_cash=paid, funding_cash=native if complete else None,
                fee_slippage_only_R=(baseline + paid) / risk,
                native_funding_R=native / risk if complete else None,
                delta_R=(paid + native) / risk if complete else None,
                recosted_R=(baseline + paid + native) / risk if complete else None,
                proxy_low_delta_R=(paid + math.fsum(low)) / risk if complete else None,
                proxy_high_delta_R=(paid + math.fsum(high)) / risk if complete else None,
                late_delta_R=(paid + math.fsum(late_cash)) / risk if complete else None,
                boundary_before_fill_delta_R=(paid + math.fsum(boundary_cash)) / risk if complete else None,
                funding_event_count=sum(q > 0 for q in primary),
                labelled_proxy=bool(used), labelled_LUNC_proxy=any(x['symbol'] == '1000LUNCUSDT' for x in used),
                proxy_events=used, missing=missing, status='COMPLETE_MODELLED_CLOCK' if complete else 'INCOMPLETE')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tapes', nargs='+', required=True)
    parser.add_argument('--funding', default='research_cache/lane2/native_funding/symbols')
    parser.add_argument('--proxies', default='research_cache/lane2/mark_proxies.json')
    parser.add_argument('--allow-all-labelled-proxies', action='store_true')
    parser.add_argument('--out', required=True)
    a = parser.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    proxies = {}
    if Path(a.proxies).exists():
        proxies = {(r['symbol'], r['funding_time_ms']): r for r in json.loads(Path(a.proxies).read_text())}
    funding, stamps = {}, {}
    for path in Path(a.funding).glob('*.json'):
        payload = json.loads(path.read_text())
        funding[payload['symbol']] = payload
        stamps[payload['symbol']] = [r['fundingTime'] for r in payload['events']]
    seen, summaries = set(), []
    with gzip.open(out / 'trade_deltas.jsonl.gz', 'wt', encoding='utf-8', newline='\n') as details:
        for tape in a.tapes:
            tape = Path(tape)
            opener = gzip.open if tape.suffix == '.gz' else open
            with opener(tape, 'rt', encoding='utf-8') as stream:
                for line in stream:
                    job = json.loads(line)
                    if job['job_id'] in seen:
                        continue
                    seen.add(job['job_id'])
                    if not job['parity']['matches']:
                        raise ValueError('baseline parity did not pass: ' + job['job_id'])
                    results = []
                    for trade in job['trades']:
                        sym = trade['sym']
                        if sym not in funding:
                            raise ValueError('funding symbol absent: ' + sym)
                        start = min(f['time_ms'] for f in trade['fills'])
                        end = max(f['time_ms'] for f in trade['fills']) + job['bar_ms']
                        coverage = funding[sym]
                        if coverage['start_ms'] > start or coverage['end_ms'] < end:
                            raise ValueError('funding range does not cover tape: ' + trade['trade_id'])
                        i, j = bisect_left(stamps[sym], start), bisect_right(stamps[sym], end)
                        result = recost_trade(trade, coverage['events'][i:j], job['bar_ms'], proxies,
                                              a.allow_all_labelled_proxies)
                        details.write(json.dumps(dict(job_id=job['job_id'], **result), allow_nan=False) + '\n')
                        results.append(result)
                    complete = all(r['status'] != 'INCOMPLETE' for r in results)
                    summary = {k: v for k, v in job.items() if k not in ('trades', 'provenance')}
                    summary.update(status='COMPLETE_MODELLED_CLOCK' if complete else 'INCOMPLETE',
                        n=len(results), baseline_sumR=math.fsum(r['baseline_R'] for r in results),
                        fee_slippage_only_sumR=math.fsum(r['fee_slippage_only_R'] for r in results),
                        proxy_trade_count=sum(r['labelled_proxy'] for r in results),
                        LUNC_proxy_trade_count=sum(r['labelled_LUNC_proxy'] for r in results),
                        missing_trade_count=sum(bool(r['missing']) for r in results),
                        missing_event_keys=sorted({(x['symbol'], x['funding_time_ms']) for r in results for x in r['missing']}))
                    for key in ('delta_R', 'recosted_R', 'proxy_low_delta_R', 'proxy_high_delta_R',
                                'late_delta_R', 'boundary_before_fill_delta_R', 'native_funding_R'):
                        summary['sum_' + key] = math.fsum(r[key] for r in results) if complete else None
                    summaries.append(summary)
                    print(job['job_id'], summary['status'], summary['sum_delta_R'], flush=True)
    (out / 'job_deltas.json').write_bytes((json.dumps(summaries, indent=2, allow_nan=False) + '\n').encode())
    manifest = dict(tapes=[dict(path=str(Path(p)), sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest()) for p in a.tapes],
                    allow_all_labelled_proxies=a.allow_all_labelled_proxies,
                    proxy_sha256=hashlib.sha256(Path(a.proxies).read_bytes()).hexdigest() if Path(a.proxies).exists() else None)
    (out / 'recost_manifest.json').write_bytes((json.dumps(manifest, indent=2) + '\n').encode())


if __name__ == '__main__':
    main()
