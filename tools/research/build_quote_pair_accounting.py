"""Trim Integration's raw quote-pair accounting pack into the runner's `quote-pair-accounting/1` manifests (one per quote).

Research only. Reads `<raw>/funding_rate/<SYM>.csv`, `<raw>/mark_klines/<SYM>_1h.csv` and the exchangeInfo snapshot
named by `<raw>/manifest.json`; writes, write-once, `<out>/<QUOTE>-accounting.json` and `<out>/<quote>/<SYM>_*.csv`
holding only [warm-up start, sealed cutoff). Each raw file must be time ordered; reading stops at the FIRST row at /
after the cutoff, so no value at or after 2026-09-28T00:00Z is ever parsed or written. Nothing is validated as
complete here - the runner (run_quote_pair_usdc.load_accounting) re-validates every grid, hash and rule before any
simulation. Rules are labelled CURRENT_SNAPSHOT_ONLY / NON_CAUSAL.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import costs as C  # noqa: E402
import run_quote_pair_usdc as Q  # noqa: E402

SERIES = (('fundingRate', 'funding_rate', '{sym}.csv', 'fundingTime', Q.FUNDING_COLS),
          ('markPriceKlines_1h', 'mark_klines', '{sym}_1h.csv', 'open_time', Q.MARK_COLS))


class BuildError(RuntimeError):
    pass


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def trim(path, tcol, cols, lo):
    """Rows of a time-ordered raw CSV with lo <= t < END, only `cols`. Stops at the first row at / after END."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator='\n')
    w.writerow(cols)
    n, prev = 0, None
    try:
        f = open(path, newline='', encoding='utf-8')
    except OSError:
        raise BuildError(f'{path}: missing') from None
    first = None
    with f:
        head = next(csv.reader([f.readline()]), None)
        if not head or head[0] != tcol or any(c not in head for c in cols):
            raise BuildError(f'{path}: columns {head} must start with {tcol} and hold {cols}')
        ix = [head.index(c) for c in cols]
        for line in f:
            try:
                t = int(line.split(',', 1)[0])          # only the time field is read before the cutoff check
            except ValueError:
                raise BuildError(f'{path}: unreadable time after {prev}') from None
            if prev is not None and t <= prev:
                raise BuildError(f'{path}: not strictly time ordered at {t}')
            prev = t
            first = t if first is None else first
            if t >= Q.END:
                break                                   # sealed: nothing at / after the cutoff is parsed or read further
            if t >= lo:
                row = next(csv.reader([line]))
                w.writerow([row[k] for k in ix])
                n += 1
    return out.getvalue().encode('utf-8'), n, first


def provenance(rm, folder, sym, name):
    """The raw manifest's record of one series file. It must exist and name this file; the raw pack carries no
    per-file sha256, so the first row time is the strongest check available without reading past the cutoff."""
    series = rm.get('series') or {}
    rec = series.get(f'{folder}/{sym}') or series.get(f'{folder}/{sym}/1h')
    if not isinstance(rec, dict) or rec.get('file') != f'{folder}/{name}':
        raise BuildError(f'{folder}/{name}: not recorded in the raw manifest')
    if rec.get('symbol', sym) != sym:
        raise BuildError(f'{folder}/{name}: raw manifest names symbol {rec.get("symbol")}')
    return rec


def rules_for(info, sym):
    r = next((x for x in info.get('symbols') or () if x.get('symbol') == sym), None)
    if r is None:
        raise BuildError(f'{sym}: not in the exchangeInfo snapshot')
    return {'status': r.get('status'), 'contractType': r.get('contractType'), 'baseAsset': r.get('baseAsset'),
            'quoteAsset': r.get('quoteAsset'),
            'filters': {f['filterType']: {k: v for k, v in f.items() if k != 'filterType'}
                        for f in r.get('filters') or () if f.get('filterType') in Q.REQUIRED_FILTERS}}


def build(raw, out, pin_info_sha=None):
    if os.path.exists(out):
        raise BuildError(f'{out} exists; a pack is never overwritten')
    try:
        with open(os.path.join(raw, 'manifest.json'), encoding='utf-8') as f:
            rm = json.load(f)
        ex = rm['series']['exchange_info/mainnet']
        with open(os.path.join(raw, ex['file']), 'rb') as f:
            info_raw = f.read()
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise BuildError(f'raw manifest / exchangeInfo unreadable ({type(e).__name__})') from None
    got = _sha(info_raw)
    if ex.get('sha256') != got and got != pin_info_sha:
        raise BuildError(f'exchangeInfo snapshot sha256 {got} does not match its raw manifest ({ex.get("sha256")}); '
                         'pin the file hash explicitly with --pin-exchange-info-sha to accept it')
    pinned = ex.get('sha256') != got
    info = json.loads(info_raw)
    staged = {}
    for quote in Q.QUOTES:
        files, rules = {}, {}
        for base in Q.BASES:
            sym = base + quote
            for series, folder, pat, tcol, cols in SERIES:
                lo = Q.WARM - C.FUNDING_TIME_TOLERANCE_MS if series == 'fundingRate' else Q.WARM
                data, n, first = trim(os.path.join(raw, folder, pat.format(sym=sym)), tcol, cols, lo)
                rec = provenance(rm, folder, sym, pat.format(sym=sym))
                if rec is not None and rec.get('first_ts') is not None and int(rec['first_ts']) != first:
                    raise BuildError(f'{folder}/{pat.format(sym=sym)}: first row {first} is not the raw manifest '
                                     f'first_ts {rec["first_ts"]} (swapped or edited raw file)')
                name = f'{sym}_{series}.csv'
                staged[os.path.join(quote.lower(), name)] = data
                files[f'{sym}/{series}'] = {'symbol': sym, 'series': series, 'status': 'complete', 'file': name,
                                            'rows': n, 'bytes': len(data), 'sha256': _sha(data),
                                            'source': f'{folder}/{pat.format(sym=sym)} (trimmed)'}
            rules[sym] = rules_for(info, sym)
        m = {'format': Q.ACCOUNTING_FORMAT, 'contract': Q.CONTRACT, 'quote': quote,
             'authority': 'RESEARCH_ONLY; NO_TRADING_OR_PROMOTION_AUTHORITY',
             'symbols': [b + quote for b in Q.BASES],
             'window': {'warmup_start': Q.WARM_S, 'development_start': Q.START_S, 'end_exclusive': Q.END_S},
             'exchange_rules': {'label': 'CURRENT_SNAPSHOT_ONLY', 'causality': 'NON_CAUSAL',
                                'promotion_authority': 'NONE', 'source': ex['file'], 'sha256': got,
                                'raw_manifest_sha256': ex.get('sha256'), 'sha_pinned_by_operator': pinned,
                                'fetched_utc': ex.get('fetched_utc'), 'symbols': rules},
             'source': {'raw_manifest_sha256': _sha(json.dumps(rm, sort_keys=True).encode()),
                        'raw_dir': os.path.abspath(raw)},
             'stopped': None, 'files': files, 'incomplete': []}
        staged[f'{quote}-accounting.json'] = (json.dumps(m, sort_keys=True, indent=1) + '\n').encode('utf-8')
    for rel, data in staged.items():                     # every file staged before the first write
        p = os.path.join(out, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'xb') as f:
            f.write(data)
    return {q: os.path.join(out, f'{q}-accounting.json') for q in Q.QUOTES}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--raw', required=True, help='Integration raw pack (accounting_raw)')
    ap.add_argument('--out', required=True, help='new directory for the trimmed per-quote packs')
    ap.add_argument('--pin-exchange-info-sha', help='accept this exact exchangeInfo file sha256 when the raw manifest '
                    'hash does not match it (recorded as sha_pinned_by_operator)')
    a = ap.parse_args(argv)
    try:
        paths = build(a.raw, a.out, a.pin_exchange_info_sha)
    except BuildError as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    for q, p in paths.items():
        print(f'{q}: {p}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
