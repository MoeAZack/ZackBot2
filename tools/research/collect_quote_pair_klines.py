"""Collect a quote-specific, bounded research candle pack without changing the frozen collector.

Only symbols whose same-base USDT contract is in ZackBot's frozen universe are accepted. The
underlying collector still owns the sealed development-window, no-overwrite and gap rules.
"""
import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import collect_klines_research as CK  # noqa: E402
import run_vwaprev_candidate as VC  # noqa: E402

FORMAT = 'zb-quote-pair-klines/1'
CONTRACT = 'quote_pair_usdc_shadow.v0'
WINDOW = '2024-10-14T00:00:00Z/2026-09-28T00:00:00Z'
INTERVAL = '4h'
WARMUP_BARS = 220


class PairCollectError(RuntimeError):
    pass


def resolve_symbols(raw_symbols, quote, static=None):
    quote = str(quote).upper()
    if quote not in ('USDT', 'USDC'):
        raise PairCollectError('quote must be USDT or USDC')
    static = static or VC.frozen_universe()
    known = {x['symbol'] for x in static['symbols']}
    out = []
    for raw in raw_symbols:
        if not isinstance(raw, str) or not raw.isascii():
            raise PairCollectError(f'invalid symbol: {raw!r}')
        symbol = raw.strip().upper()
        if not re.fullmatch(r'[A-Z0-9]{2,20}' + quote, symbol):
            raise PairCollectError(f'symbol must be an explicit {quote} contract: {symbol!r}')
        base = symbol[:-len(quote)]
        twin = base + 'USDT'
        if twin not in known:
            raise PairCollectError(f'{symbol} has no same-base contract in the frozen universe')
        if symbol not in out:
            out.append(symbol)
    if not out:
        raise PairCollectError('at least one symbol is required')
    return out


def validate_files(man, out):
    """Turn malformed or unproved cache bytes into INCOMPLETE before they can be sealed."""
    incomplete = set(man.get('incomplete', ()))
    for symbol, rec in man['files'].items():
        path = os.path.join(out, rec['file'])
        problems = []
        try:
            df = CK.pd.read_csv(path)
            if list(df.columns) != CK.COLS:
                problems.append('columns')
            else:
                n = df[CK.COLS].apply(CK.pd.to_numeric, errors='coerce')
                finite = CK.np.isfinite(n.to_numpy(float)).all(axis=1)
                ordered = n.t.to_numpy().tolist() == sorted(n.t.to_numpy().tolist())
                o, h, l, c, v, qv = (n[x].to_numpy(float) for x in ('o', 'h', 'l', 'c', 'v', 'qv'))
                valid = (finite & (o > 0) & (h > 0) & (l > 0) & (c > 0) & (v >= 0) & (qv >= 0)
                         & (h >= l) & (o >= l) & (o <= h) & (c >= l) & (c <= h))
                if not valid.all():
                    problems.append(f'invalid_ohlcv_rows={int((~valid).sum())}')
                if not ordered:
                    problems.append('timestamps_not_sorted')
        except (OSError, ValueError, KeyError) as e:
            problems.append(f'unreadable={type(e).__name__}')
        if rec.get('fetched', '').startswith('existing file'):
            problems.append('preexisting_provenance_unverified')
        rec['validation'] = {'status': 'INCOMPLETE' if problems else 'complete', 'problems': problems,
                             'source': rec.get('fetched')}
        if problems:
            rec['status'] = 'INCOMPLETE'
            rec['error'] = '; '.join(problems)
            incomplete.add(symbol)
    man['incomplete'] = sorted(incomplete)


def collect_pair(*, symbols, quote, interval, window, out, manifest, warmup_bars=220, **kwargs):
    if os.path.exists(manifest):
        raise PairCollectError(f'{manifest} exists; a manifest is never overwritten')
    if interval != INTERVAL or window != WINDOW or int(warmup_bars) != WARMUP_BARS:
        raise PairCollectError(f'{CONTRACT} requires {INTERVAL}, window {WINDOW}, warmup {WARMUP_BARS}')
    quote = str(quote).upper()
    resolved = resolve_symbols(symbols, quote)
    man = CK.collect(symbols=resolved, interval=interval, window=window, out=out, manifest=None,
                     warmup_bars=warmup_bars, **kwargs)
    validate_files(man, out)
    pairs = {s: {'base': s[:-len(quote)], 'usdt_twin': s[:-len(quote)] + 'USDT'} for s in resolved}
    with open(os.path.abspath(__file__), 'rb') as f:
        tool_sha = hashlib.sha256(f.read()).hexdigest()
    man.update(format=FORMAT, contract=CONTRACT, quote=quote, pairs=pairs)
    man.setdefault('tool', {})['collect_quote_pair_klines.py'] = tool_sha
    os.makedirs(os.path.dirname(os.path.abspath(manifest)), exist_ok=True)
    with open(manifest, 'x', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(man, sort_keys=True, indent=1) + '\n')
    return man


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--quote', required=True, choices=('USDT', 'USDC'))
    ap.add_argument('--symbols', required=True)
    ap.add_argument('--interval', required=True, choices=sorted(CK.INTERVALS))
    ap.add_argument('--window', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--warmup-bars', type=int, default=220)
    a = ap.parse_args(argv)
    try:
        man = collect_pair(symbols=a.symbols.split(','), quote=a.quote, interval=a.interval, window=a.window,
                           out=a.out, manifest=a.manifest, warmup_bars=a.warmup_bars)
    except (PairCollectError, CK.CollectError, VC.RunnerError) as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    for symbol, row in man['files'].items():
        print(f"{symbol}: {row.get('status')} rows {row.get('rows')} missing {row.get('missing')} {row.get('error', '')}")
    return 1 if man['incomplete'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
