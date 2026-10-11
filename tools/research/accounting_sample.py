"""STRAT-00 R4-0 item 4: export a fixed long + short accounting sample for an independent (Cowork) calculator.

No strategy is evaluated: the trades below are fixed by hand in this file (symbol, side, entry bar, stop distance,
maximum hold), chosen before any data is read, on a spent development window that never touches the holdout. The
script reads the manifest-verified archive through a ledgered development `Access` (res01_infra family) and writes two
files:

  <out>/accounting_inputs.json  everything an independent calculator needs: the trade specs, the conventions, the
                                cost parameters, the raw 4h bars (15 closed pre-fill bars for slip-v1 + every bar
                                from entry to exit) and the funding events (time, signed rate, funding-mark proxy).
  <out>/accounting_build.json   Build's side, computed with the R3 primitives of costs.py (slip_bps, fill_price,
                                stop_reference, fee, funding_cost): fills, fees, funding, gross, net and R per trade.

Cowork recomputes `accounting_build.json` from `accounting_inputs.json` alone and compares: every value |d| <= 1e-9
(relative to the notional for USDT values). Both files are referenced only by SHA-256 in status posts.

  python tools/research/accounting_sample.py --store C:/Dev/ZackBot2_data/binance_um --out-dir <dir>
         --author claude-code --cairo-date YYYY-MM-DD
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C                                                                           # noqa: E402
import manifest as M                                                                        # noqa: E402
import pit as P                                                                             # noqa: E402
import splits as S                                                                          # noqa: E402

FORMAT = 'zb-accounting-sample/1'
MANIFEST = 'research_evidence/manifests/binance-um-archive-v1.json.gz'
UNIVERSE = 'research_evidence/universe/pit-top40-qv30d-v4.json'
LEDGER = 'research_evidence/ledger/res01_infra.jsonl'
WINDOW = ('2025-02-24T00:00:00Z', '2025-05-05T00:00:00Z')        # spent development window (walk_forward fold 3)
TF, TF_MS = '4h', 4 * P.HOUR
NOTIONAL = 1000.0                                                 # USDT at the entry fill; qty = NOTIONAL / fill
SCEN = C.STRESS['base']
# Fixed before any data is read. stop_pct = stop distance from the entry fill; max_bars = bars held before a time exit.
TRADES = (
    {'id': 'T1', 'symbol': 'BTCUSDT', 'side': 'long', 'entry': '2025-03-03T08:00:00Z', 'stop_pct': 0.03, 'max_bars': 18},
    {'id': 'T2', 'symbol': 'BTCUSDT', 'side': 'short', 'entry': '2025-03-10T00:00:00Z', 'stop_pct': 0.03, 'max_bars': 18},
    {'id': 'T3', 'symbol': 'ETHUSDT', 'side': 'long', 'entry': '2025-03-17T04:00:00Z', 'stop_pct': 0.04, 'max_bars': 24},
    {'id': 'T4', 'symbol': 'ETHUSDT', 'side': 'short', 'entry': '2025-03-24T12:00:00Z', 'stop_pct': 0.04, 'max_bars': 24},
    {'id': 'T5', 'symbol': 'SOLUSDT', 'side': 'long', 'entry': '2025-04-01T00:00:00Z', 'stop_pct': 0.02, 'max_bars': 12},
    {'id': 'T6', 'symbol': 'XRPUSDT', 'side': 'short', 'entry': '2025-04-07T08:00:00Z', 'stop_pct': 0.02, 'max_bars': 12},
)
CONVENTIONS = {
    'entry': 'market at the open of the entry bar; slip-v1 bps from the 15 closed 4h bars available at that open; '
             'buy for long, sell for short; no tick / step rounding (tick = None, qty unrounded)',
    'qty': f'qty = {NOTIONAL} / entry_fill',
    'stop': 'long: entry_fill x (1 - stop_pct); short: entry_fill x (1 + stop_pct). Checked on every bar from the '
            'entry bar on: long hit when low <= stop, short when high >= stop. costs.stop_reference: a bar that opens '
            'through the stop fills at its open (gap = true, gap slip multiplier), else at the stop; slip-v1 bps from '
            'the 15 closed bars available at that bar open; sell for long, buy for short',
    'time_exit': 'no stop within max_bars bars (entry bar included): market at the open of bar entry + max_bars, '
                 'slip-v1 bps from the 15 closed bars available at that open',
    'exit_time': 'time exit and gap stop: the exit bar open; non-gap stop: the stop bar close (open + 4h)',
    'fees': 'taker 0.05% x |fill x qty| on entry and on exit',
    'funding': 'costs.funding_cost base row: sum over events entry <= T < exit_time of sign x rate x qty x mark '
               '(sign +1 long, -1 short; + = paid); mark = funding-mark proxy v1 (close of the 1h mark bar ending at '
               'or before T)',
    'gross': 'long: (exit - entry) x qty; short: (entry - exit) x qty',
    'net': 'gross - fees - funding',
    'R': 'net / (|entry_fill - stop| x qty)',
    'slip_bps': 'max(2, c x 1e4 x TR-SMA14 / last close), c = slip-cal-v1 crypto 0.05; TR_i = max(h-l, |h-c_prev|, '
                '|l-c_prev|) over the last 14 of the 15 bars',
}


class SampleError(ValueError):
    pass


def _bar(b) -> dict:
    return {'open_ms': b.open_ms, 'open': b.open, 'high': b.high, 'low': b.low, 'close': b.close}


def _closed(bars, t):
    out = [b for b in bars if b.available_ms <= t]
    if len(out) < C.SLIP_BARS:
        raise SampleError(f'fewer than {C.SLIP_BARS} closed bars before {S.utc(t)}')
    return out[-C.SLIP_BARS:]


def one(w, spec: dict, c: float, row: C.CostRow) -> tuple[dict, dict]:
    sym, side = spec['symbol'], spec['side']
    e_ms = S.parse_utc(spec['entry'])
    last_open = e_ms + spec['max_bars'] * TF_MS
    pos = w.view(e_ms).enter(sym, f"strat00-acct-{spec['id']}")
    v = w.view(last_open + TF_MS)
    bars = [b for b in v.bars(sym, TF, 10 ** 6, position=pos) if b.open_ms >= e_ms - C.SLIP_BARS * TF_MS]
    by = {b.open_ms: b for b in bars}
    if any(e_ms + i * TF_MS not in by for i in range(spec['max_bars'] + 1)):
        raise SampleError(f"{spec['id']}: a 4h bar is missing between entry and the time exit")
    buy, sell = ('buy', 'sell') if side == 'long' else ('sell', 'buy')
    e_bps = C.slip_bps(_closed(bars, e_ms), c)
    entry = C.fill_price(by[e_ms].open, buy, e_bps, SCEN)
    stop = entry * (1 - spec['stop_pct']) if side == 'long' else entry * (1 + spec['stop_pct'])
    qty = NOTIONAL / entry
    exit_px = reason = x_bps = gap = x_ms = None
    for i in range(spec['max_bars']):
        b = by[e_ms + i * TF_MS]
        if (side == 'long' and b.low <= stop) or (side == 'short' and b.high >= stop):
            ref, gap = C.stop_reference(side, stop, b.open)
            x_bps = C.slip_bps(_closed(bars, b.open_ms), c)
            exit_px, reason = C.fill_price(ref, sell, x_bps, SCEN, gap=gap), 'stop'
            x_ms = b.open_ms if gap else b.open_ms + TF_MS
            break
    if reason is None:
        x_ms, gap, reason = last_open, False, 'time'
        x_bps = C.slip_bps(_closed(bars, x_ms), c)
        exit_px = C.fill_price(by[x_ms].open, sell, x_bps, SCEN)
    events = v.funding_events(pos, x_ms)
    fees = C.fee(entry * qty, 'taker', row, SCEN) + C.fee(exit_px * qty, 'taker', row, SCEN)
    funding = C.funding_cost(side, qty, events, SCEN)
    gross = (exit_px - entry) * qty if side == 'long' else (entry - exit_px) * qty
    net = gross - fees - funding
    used = [b for b in bars if b.open_ms <= max(x_ms, last_open)]
    inputs = dict(spec, bars_4h=[_bar(b) for b in used],
                  funding_events=[{'time_ms': t, 'rate': r, 'mark': m} for t, r, m in events])
    build = {'id': spec['id'], 'entry_bps': e_bps, 'entry_fill': entry, 'qty': qty, 'stop': stop,
             'exit_reason': reason, 'exit_gap': gap, 'exit_bps': x_bps, 'exit_fill': exit_px, 'exit_time_ms': x_ms,
             'funding_events': len(events), 'fees': fees, 'funding': funding, 'gross': gross, 'net': net,
             'R': net / (abs(entry - stop) * qty)}
    return inputs, build


def run(repo: str, store: str, out_dir: str, *, author: str, cairo_date: str) -> dict:
    man = M.load(os.path.join(repo, MANIFEST))
    with open(os.path.join(repo, UNIVERSE), encoding='utf-8') as f:
        uni = json.load(f)
    ds = P.Dataset(man, store, uni, books=['crypto'])
    acc = P.Access(ds, None, os.path.join(repo, LEDGER), candidate_id='res01.strat00.accounting', author=author,
                   cairo_date=cairo_date, repo=repo)
    w = acc.open_development(*WINDOW, detail={'purpose': 'STRAT-00 R4-0 item 4: fixed hand-specified long + short '
                                                         'accounting sample; no strategy, no signal',
                                              'symbols': sorted({t['symbol'] for t in TRADES})})
    row = C.DEFAULT_ROWS['crypto']
    c = 0.05
    ins, outs = [], []
    for spec in TRADES:
        i, o = one(w, spec, c, row)
        ins.append(i)
        outs.append(o)
    common = {'format': FORMAT, 'manifest_digest': ds.digest, 'universe_digest': ds.universe_digest,
              'window': list(WINDOW), 'scenario': 'base'}
    inputs = dict(common, conventions=CONVENTIONS,
                  costs={'taker': row.taker, 'slip_c': c, 'slip_floor_bps': C.SLIP_FLOOR_BPS,
                         'slip_bars': C.SLIP_BARS, 'gap_slip_mult': SCEN.gap_slip_mult, 'notional': NOTIONAL},
                  trades=ins)
    build = dict(common, trades=outs)
    os.makedirs(out_dir, exist_ok=True)
    res = {}
    for name, doc in (('accounting_inputs.json', inputs), ('accounting_build.json', build)):
        raw = json.dumps(doc, indent=1, sort_keys=True, allow_nan=False).encode('utf-8') + b'\n'
        p = os.path.join(out_dir, name)
        with open(p + '.tmp', 'wb') as f:
            f.write(raw)
        os.replace(p + '.tmp', p)
        res[name] = hashlib.sha256(raw).hexdigest()
    res['trades'] = len(outs)
    res['sides'] = {s: sum(t['side'] == s for t in TRADES) for s in ('long', 'short')}
    res['exits'] = {r: sum(o['exit_reason'] == r for o in outs) for r in ('stop', 'time')}
    res['funding_events'] = sum(o['funding_events'] for o in outs)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='STRAT-00 accounting sample export')
    ap.add_argument('--store', required=True)
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--author', required=True)
    ap.add_argument('--cairo-date', required=True)
    ap.add_argument('--repo', default=M.REPO)
    a = ap.parse_args(argv)
    try:
        print(json.dumps(run(a.repo, a.store, a.out_dir, author=a.author, cairo_date=a.cairo_date), sort_keys=True))
    except (SampleError, P.PITError, C.CostError, M.ManifestError, OSError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
