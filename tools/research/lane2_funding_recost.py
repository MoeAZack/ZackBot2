"""Fixed-book funding re-costing; no strategy tuning or equity-feedback re-sizing.

Consumes independently baseline-matched replay tapes and cached public events.
Primary clock: ordinary taker entries/gap fills at candle open; signal/time exits
at last millisecond; other intrabar fills at open. 'late' moves uncertain fills to
last millisecond. Exact exchange fills are not claimed by this candle replay.
"""
import argparse
from bisect import bisect_left, bisect_right
from collections import OrderedDict
import gzip
import hashlib
import json
import math
from pathlib import Path

ORIGINAL_LUNC = {1673136000015, 1673164800000, 1673193600010, 1673222400003}


class History:
    """Bound memory while reading long public histories across the 174-symbol book."""
    def __init__(self, folder, limit=8):
        self.paths = {p.stem: p for p in Path(folder).glob('*.json')}
        self.cache, self.stamps, self.limit = OrderedDict(), {}, limit

    def __contains__(self, symbol):
        return symbol in self.paths

    def __getitem__(self, symbol):
        if symbol not in self.cache:
            data = json.loads(self.paths[symbol].read_text())
            if data['status'] != 'PAGINATED_REST_RANGE_COMPLETE':
                raise ValueError('funding pagination incomplete: ' + symbol)
            stamps = [r['fundingTime'] for r in data['events']]
            if stamps != sorted(set(stamps)):
                raise ValueError('duplicate/unordered funding history: ' + symbol)
            data['cadence_gaps'] = [(a, b) for a, b in zip(stamps, stamps[1:])
                                    if b - a > 8 * 3600000 + 120000]
            self.cache[symbol], self.stamps[symbol] = data, stamps
            if len(self.cache) > self.limit:
                old, _ = self.cache.popitem(last=False)
                del self.stamps[old]
        self.cache.move_to_end(symbol)
        return self.cache[symbol]


def rate_coverage_gaps(coverage, start, end):
    """Flag suspicious missing event spans; never fill a missing rate with zero."""
    rows = coverage['events']
    if not rows:
        return [dict(start_ms=start, end_ms=end, reason='NO_PUBLIC_FUNDING_EVENTS_IN_QUERIED_RANGE')]
    gaps = [dict(start_ms=a, end_ms=b, reason='RETURNED_EVENT_CADENCE_EXCEEDS_8H_PLUS_2MIN')
            for a, b in coverage.get('cadence_gaps', []) if a < end and b > start]
    allowance = 8 * 3600000 + 120000
    if rows[0]['fundingTime'] - start > allowance:
        gaps.append(dict(start_ms=start, end_ms=rows[0]['fundingTime'], reason='LEFT_FUNDING_HISTORY_EDGE'))
    if end - rows[-1]['fundingTime'] > allowance:
        gaps.append(dict(start_ms=rows[-1]['fundingTime'], end_ms=end, reason='RIGHT_FUNDING_HISTORY_EDGE'))
    return gaps


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def expand_job(job):
    """Lead books price one fixed fill tape at three fee levels; PROT replays each."""
    if 'matches' not in job['parity']:
        job['parity']['matches'] = job['parity'].get('pass', False)
    if not job['trades'] or 'pnl_by_cost' not in job['trades'][0]:
        yield job
        return
    for cost in (7, 15, 23):
        trades = []
        job_id = job['job_id'] + '|cost' + str(cost)
        for trade in job['trades']:
            pnl = float(trade['pnl_by_cost'][str(cost)])
            trades.append(dict(trade, trade_id=job_id + '|' + trade['trade_id'],
                               baseline_pnl=pnl, baseline_R=pnl / float(trade['risk'])))
        yield dict(job, job_id=job_id, cost_bp=cost, trades=trades)


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
        if not all(math.isfinite(x) for x in (rate, mark, lo, hi)) or not 0 < lo <= mark <= hi:
            raise ValueError('invalid native/proxy mark or funding rate')
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
    parser.add_argument('--proxies', default='research_cache/lane2/mark_archives/mark_sources.json')
    parser.add_argument('--allow-all-labelled-proxies', action='store_true')
    parser.add_argument('--out', required=True)
    a = parser.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    proxies = {}
    if Path(a.proxies).exists():
        proxy_payload = json.loads(Path(a.proxies).read_text())
        records = proxy_payload['records'] if isinstance(proxy_payload, dict) else proxy_payload
        proxies = {(r['symbol'], r['funding_time_ms']): r for r in records}
    funding = History(a.funding)
    stamps = funding.stamps
    seen, summaries = set(), []
    with gzip.open(out / 'trade_deltas.jsonl.gz', 'wt', encoding='utf-8', newline='\n') as details:
        for tape in a.tapes:
            tape = Path(tape)
            opener = gzip.open if tape.suffix == '.gz' else open
            with opener(tape, 'rt', encoding='utf-8') as stream:
                for line in stream:
                    for job in expand_job(json.loads(line)):
                        process_job(job, seen, funding, stamps, proxies, a, details, summaries)
    (out / 'job_deltas.json').write_bytes((json.dumps(summaries, indent=2, allow_nan=False) + '\n').encode())
    manifest = dict(tapes=[dict(path=str(Path(p)), sha256=sha_file(p)) for p in a.tapes],
                    allow_all_labelled_proxies=a.allow_all_labelled_proxies,
                    proxy_sha256=hashlib.sha256(Path(a.proxies).read_bytes()).hexdigest() if Path(a.proxies).exists() else None)
    (out / 'recost_manifest.json').write_bytes((json.dumps(manifest, indent=2) + '\n').encode())


def process_job(job, seen, funding, stamps, proxies, a, details, summaries):
    if job['job_id'] in seen:
        return
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
        result['rate_coverage_gaps'] = rate_coverage_gaps(coverage, start, end)
        if result['rate_coverage_gaps']:
            result['status'] = 'INCOMPLETE'
            for key in ('funding_cash', 'native_funding_R', 'delta_R', 'recosted_R',
                        'proxy_low_delta_R', 'proxy_high_delta_R', 'late_delta_R',
                        'boundary_before_fill_delta_R'):
                result[key] = None
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
        rate_gap_trade_count=sum(bool(r['rate_coverage_gaps']) for r in results),
        missing_event_keys=sorted({(x['symbol'], x['funding_time_ms']) for r in results for x in r['missing']}))
    for key in ('delta_R', 'recosted_R', 'proxy_low_delta_R', 'proxy_high_delta_R',
                'late_delta_R', 'boundary_before_fill_delta_R', 'native_funding_R'):
        summary['sum_' + key] = math.fsum(r[key] for r in results) if complete else None
    summaries.append(summary)
    print(job['job_id'], summary['status'], summary['sum_delta_R'], flush=True)

if __name__ == '__main__':
    main()
