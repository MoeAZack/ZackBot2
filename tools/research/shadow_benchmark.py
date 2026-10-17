"""Research-only random-entry and portfolio controls for ZackBot candidate packets.

This module deliberately does not alter the trading engine or candidate runner.  It consumes
one or more immutable ``zb-breadth-candidate/1`` packets and their exact candle cache:

* the entry-timing control preserves each observed trade's symbol, side and holding bars, then
  samples a random eligible entry from the same symbol and sealed window;
* the portfolio report aligns daily realised R inside the common tested window, reports both
  zero-filled calendar and active-overlap correlations, volatility contribution and leave-one-
  out return/drawdown context;
* the output is write-once, deterministic for an explicit seed and always non-promotable;
* optionally, one baseline record is appended through the existing ``zb-ledger/1`` writer.

Boundary: the legacy backtester has no injectable signal-provider interface.  Consequently this
is a matched-horizon entry-timing null, not a claim that random entries replayed the strategy's
exact stop/TP/signal-exit state machine.  That stronger null needs a later backtester interface.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ledger as LEDGER  # noqa: E402
import run_vwaprev_candidate as VC  # noqa: E402

FORMAT = 'zb-shadow-benchmark/1'
INPUT_FORMAT = 'zb-breadth-candidate/1'
CAIRO = ZoneInfo('Africa/Cairo')
MIN_ITERATIONS = 100
TF_MS = {'15m': 15 * 60_000, '4h': 4 * 3_600_000}


class BenchmarkError(ValueError):
    pass


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _num(x, nd=8):
    if x is None:
        return None
    x = float(x)
    return round(x, nd) if math.isfinite(x) else None


def _ms(s: str) -> int:
    t = pd.Timestamp(s)
    if t.tzinfo is None:
        raise BenchmarkError(f'timestamp is not UTC-aware: {s!r}')
    return int(t.tz_convert('UTC').value // 1_000_000)


def _packet_digest(packet: dict) -> str:
    body = {k: v for k, v in packet.items() if k != 'packet_sha256'}
    return hashlib.sha256(canonical(body)).hexdigest()


def _benchmark_digest(doc: dict) -> str:
    """Stable result identity: timestamps and machine-local absolute paths are provenance, not evidence."""
    body = {k: v for k, v in doc.items() if k not in ('benchmark_sha256', 'created_at_utc')}
    body['inputs'] = [{k: v for k, v in x.items() if k != 'path'} for x in body['inputs']]
    return hashlib.sha256(canonical(body)).hexdigest()


def load_packet(path: str) -> tuple[dict, str]:
    with open(path, encoding='utf-8') as f:
        packet = json.load(f, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    if not isinstance(packet, dict) or packet.get('format') != INPUT_FORMAT:
        raise BenchmarkError(f'{path}: expected {INPUT_FORMAT}')
    digest = _packet_digest(packet)
    if packet.get('packet_sha256') != digest:
        raise BenchmarkError(f'{path}: packet_sha256 does not match packet content')
    if packet.get('non_promotable') is not True:
        raise BenchmarkError(f'{path}: candidate packet must be explicitly non-promotable')
    w = packet.get('window') or {}
    if not (w.get('start') and w.get('end') and w.get('end_exclusive') is True and w.get('tf')
            and w.get('guard') == 'pit_run.rex_window' and w.get('sealed_cutoff')):
        raise BenchmarkError(f'{path}: incomplete sealed-window identity')
    if w['sealed_cutoff'] != VC.SEALED_CUTOFF:
        raise BenchmarkError(f'{path}: sealed cutoff must equal frozen {VC.SEALED_CUTOFF}')
    try:
        start_ms, end_ms, cutoff_ms = _ms(w['start']), _ms(w['end']), _ms(w['sealed_cutoff'])
    except (TypeError, ValueError):
        raise BenchmarkError(f'{path}: invalid sealed-window timestamps') from None
    if start_ms >= end_ms:
        raise BenchmarkError(f'{path}: window start must be before end')
    if end_ms > cutoff_ms:
        raise BenchmarkError(f'{path}: window reaches beyond its sealed development cutoff')
    if not isinstance(packet.get('trades'), list):
        raise BenchmarkError(f'{path}: trades must be a list')
    st = packet.get('strategy')
    if not isinstance(st, dict) or not all(isinstance(st.get(k), str) and st[k] for k in ('key', 'mgmt_mode')):
        raise BenchmarkError(f'{path}: strategy key and mgmt_mode are required')
    if packet.get('side') not in ('long', 'short'):
        raise BenchmarkError(f'{path}: side must be long or short')
    expected_side = 1 if packet['side'] == 'long' else -1
    for i, tr in enumerate(packet['trades']):
        try:
            valid = (isinstance(tr, dict) and isinstance(tr['symbol'], str) and tr['symbol']
                     and type(tr['side']) is int and tr['side'] == expected_side and math.isfinite(float(tr['R']))
                     and _ms(tr['t_in']) <= _ms(tr['t_out']))
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise BenchmarkError(f'{path}: trade {i} is malformed')
    return packet, digest


def _frame(cache: str, cache_extra: str | None, packet: dict, symbol: str) -> pd.DataFrame:
    rec = ((packet.get('cache') or {}).get('files') or {}).get(symbol)
    if not isinstance(rec, dict) or not rec.get('file') or not rec.get('sha256'):
        raise BenchmarkError(f'{symbol}: packet has no hashed candle file')
    root = cache_extra if rec.get('dir') == 'cache_extra' else cache
    if not root:
        raise BenchmarkError(f'{symbol}: packet used cache_extra but --cache-extra was not supplied')
    path = os.path.join(root, rec['file'])
    if not os.path.isfile(path):
        raise BenchmarkError(f'{symbol}: candle file is missing: {path}')
    if sha256_file(path) != rec['sha256']:
        raise BenchmarkError(f'{symbol}: candle bytes differ from the candidate packet')
    d = pd.read_csv(path)
    need = {'t', 'o', 'h', 'l', 'c'}
    if not need.issubset(d.columns):
        raise BenchmarkError(f'{symbol}: candle file lacks {sorted(need - set(d.columns))}')
    px = d[['o', 'h', 'l', 'c']].to_numpy(float)
    if not np.isfinite(px).all() or (px <= 0).any():
        raise BenchmarkError(f'{symbol}: candle prices must be finite and positive')
    d = d.sort_values('t').reset_index(drop=True)
    t = d.t.to_numpy(np.int64)
    if len(t) != len(np.unique(t)) or (len(t) > 1 and np.any(np.diff(t) <= 0)):
        raise BenchmarkError(f'{symbol}: duplicate or unsorted candle timestamps')
    return d


def _net_horizon_return(d: pd.DataFrame, i: int, hold: int, side: int, *, fee: float, slip: float,
                        funding: float) -> float:
    """Matched-horizon proxy using the declared next-open/close convention and explicit costs."""
    entry = float(d.o.iloc[i]) * (1.0 + side * slip)
    exit_ = float(d.c.iloc[i + hold]) * (1.0 - side * slip)
    if not (entry > 0 and exit_ > 0):
        raise BenchmarkError('non-positive candle price in benchmark path')
    gross = side * (exit_ - entry) / entry
    fees = fee * (1.0 + exit_ / entry)
    return gross - fees - funding * (hold + 1)


def random_entry_control(packet: dict, cache: str, *, cache_extra: str | None = None,
                         iterations: int = 500, seed: int | None = None) -> dict:
    """Matched symbol/side/count/holding-bars random-entry control.

    Sampling depends only on timestamp coverage, observed trade geometry and ``seed``.  It never
    inspects candidate prices before choosing indices.  Sampling with replacement is intentional:
    this is a selection benchmark, not a capacity-constrained portfolio replay.
    """
    if type(iterations) is not int or iterations < MIN_ITERATIONS:
        raise BenchmarkError(f'iterations must be an int >= {MIN_ITERATIONS}')
    trades = packet['trades']
    if not trades:
        raise BenchmarkError('random-entry control needs at least one observed trade')
    costs = packet.get('costs') or {}
    try:
        fee, slip, funding = map(float, (costs['fee_taker'], costs['slip'], costs['fund_per_bar']))
    except (KeyError, TypeError, ValueError):
        raise BenchmarkError('packet needs finite fee_taker, slip and fund_per_bar costs') from None
    if not all(math.isfinite(x) and x >= 0 for x in (fee, slip, funding)):
        raise BenchmarkError('benchmark costs must be finite and non-negative')
    lo, hi = _ms(packet['window']['start']), _ms(packet['window']['end'])
    tf = packet['window']['tf']
    if tf not in TF_MS:
        raise BenchmarkError(f'unsupported packet timeframe {tf!r}')
    symbols = sorted({t.get('symbol') for t in trades if t.get('symbol')})
    if len(symbols) != len({t.get('symbol') for t in trades}):
        raise BenchmarkError('every trade needs a symbol')
    frames = {s: _frame(cache, cache_extra, packet, s) for s in symbols}
    for sym, d in frames.items():
        t = d.t.to_numpy(np.int64)
        inside = t[(t >= lo) & (t < hi)]
        if len(inside) < 2 or np.any(np.diff(inside) != TF_MS[tf]):
            raise BenchmarkError(f'{sym}: packet window is not a complete {tf} candle grid')
    specs, audit = [], {}
    for n, tr in enumerate(trades):
        try:
            sym, side = tr['symbol'], int(tr['side'])
            tin, tout = _ms(tr['t_in']), _ms(tr['t_out'])
        except (KeyError, TypeError, ValueError):
            raise BenchmarkError(f'trade {n}: malformed symbol/side/timestamps') from None
        if side not in (-1, 1) or not (lo <= tin <= tout < hi):
            raise BenchmarkError(f'trade {n}: side/window geometry is invalid')
        d = frames[sym]
        times = d.t.to_numpy(np.int64)
        a, b = np.searchsorted(times, [tin, tout])
        if a >= len(times) or b >= len(times) or times[a] != tin or times[b] != tout:
            raise BenchmarkError(f'trade {n}: entry/exit timestamp is absent from {sym} candles')
        hold = int(b - a)
        eligible = np.flatnonzero((times >= lo) & (times < hi) &
                                  (np.arange(len(times)) + hold < len(times)))
        eligible = eligible[times[eligible + hold] < hi]
        if not len(eligible):
            raise BenchmarkError(f'trade {n}: no eligible {sym} window for {hold} holding bars')
        specs.append((sym, side, hold, a, eligible))
        key = f'{sym}:{"long" if side == 1 else "short"}'
        q = audit.setdefault(key, {'trades': 0, 'holding_bars': []})
        q['trades'] += 1
        q['holding_bars'].append(hold)
    for q in audit.values():
        q['holding_bars'].sort()

    if seed is None:
        seed = int(packet['packet_sha256'][:16], 16) % (2 ** 63 - 1)
    if type(seed) is not int or seed < 0:
        raise BenchmarkError('seed must be a non-negative int')
    rng = np.random.default_rng(seed)
    observed = sum(_net_horizon_return(frames[s], a, h, sd, fee=fee, slip=slip, funding=funding)
                   for s, sd, h, a, _ in specs)
    null = np.empty(iterations, float)
    for k in range(iterations):
        total = 0.0
        for sym, side, hold, _, eligible in specs:
            i = int(eligible[int(rng.integers(0, len(eligible)))])
            total += _net_horizon_return(frames[sym], i, hold, side, fee=fee, slip=slip, funding=funding)
        null[k] = total
    ge = int((null >= observed).sum())
    p = (1 + ge) / (iterations + 1)
    pct = (float((null < observed).sum()) + 0.5 * float((null == observed).sum())) / iterations
    return {
        'kind': 'matched_horizon_random_entry', 'research_only': True,
        'claim': 'entry-timing control; NOT identical stop/TP/signal-exit replay',
        'sampling': 'with replacement after matching exact symbol, side, trade count and holding-bar multiset',
        'iterations': iterations, 'seed': seed, 'trades': len(specs), 'matching_audit': audit,
        'costs': {'fee_taker': fee, 'slip': slip, 'fund_per_bar': funding},
        'observed_proxy_net_return_sum': _num(observed),
        'null': {'mean': _num(null.mean()), 'std': _num(null.std(ddof=1)) if iterations > 1 else None,
                 'p05': _num(np.quantile(null, .05)), 'median': _num(np.median(null)),
                 'p95': _num(np.quantile(null, .95)), 'max': _num(null.max())},
        'empirical_p_one_sided': _num(p), 'observed_percentile': _num(pct),
        'beats_random_proxy_at_5pct': bool(observed > 0 and p <= .05),
    }


def _daily_r(packet: dict) -> pd.Series:
    lo = pd.Timestamp(packet['window']['start']).tz_convert(CAIRO).date()
    hi = (pd.Timestamp(packet['window']['end']).tz_convert(CAIRO) - pd.Timedelta(microseconds=1)).date()
    idx = pd.date_range(lo, hi, freq='D')
    out = pd.Series(0.0, index=idx)
    for tr in packet['trades']:
        day = pd.Timestamp(tr['t_out']).tz_convert(CAIRO).date()
        ts = pd.Timestamp(day)
        if ts not in out.index:
            raise BenchmarkError('trade exit lies outside the packet window')
        r = float(tr['R'])
        if not math.isfinite(r):
            raise BenchmarkError('trade R must be finite')
        out.loc[ts] += r
    return out


def _max_dd_r(x: np.ndarray) -> float:
    c = np.cumsum(np.asarray(x, float))
    if not len(c):
        return 0.0
    p = np.maximum.accumulate(np.r_[0.0, c])
    return float((p - np.r_[0.0, c]).max())


def portfolio_contribution(named_packets: dict[str, dict], *, weights: dict[str, float] | None = None,
                           min_days: int = 30) -> dict:
    if len(named_packets) < 2:
        return {'available': False, 'reason': 'portfolio contribution needs at least two packets'}
    if len(set(named_packets)) != len(named_packets):
        raise BenchmarkError('portfolio component names must be unique')
    curves = {k: _daily_r(v) for k, v in named_packets.items()}
    start = max(x.index.min() for x in curves.values())
    end = min(x.index.max() for x in curves.values())
    if end < start:
        raise BenchmarkError('packets have no common calendar window')
    frame = pd.DataFrame({k: v.loc[start:end] for k, v in curves.items()})
    if frame.isna().any().any():
        raise BenchmarkError('daily R alignment contains an unaccounted gap')
    if len(frame) < min_days:
        raise BenchmarkError(f'portfolio report needs at least {min_days} common calendar days')
    names = list(frame)
    if weights is None:
        w = np.repeat(1.0 / len(names), len(names))
    else:
        if set(weights) != set(names):
            raise BenchmarkError('weights must name every portfolio component exactly once')
        w = np.asarray([float(weights[n]) for n in names])
        if not np.isfinite(w).all() or (w < 0).any() or w.sum() <= 0:
            raise BenchmarkError('weights must be finite, non-negative and have a positive sum')
        w /= w.sum()
    cov = frame.cov().to_numpy(float) * 365.0
    pv2 = float(w @ cov @ w)
    pv = math.sqrt(max(0.0, pv2))
    marginal = cov @ w / pv if pv > 0 else np.full(len(names), np.nan)
    component = w * marginal
    pct = component / pv if pv > 0 else np.full(len(names), np.nan)
    portfolio = frame.to_numpy(float) @ w
    full_sum, full_dd = float(portfolio.sum()), _max_dd_r(portfolio)
    active_corr = frame.replace(0.0, np.nan).corr(min_periods=max(3, min_days // 5))
    loo = {}
    for i, name in enumerate(names):
        keep = np.arange(len(names)) != i
        wk = w[keep]
        if wk.sum() <= 0:
            loo[name] = {'available': False}
            continue
        wk = wk / wk.sum()
        q = frame.iloc[:, keep].to_numpy(float) @ wk
        qs, qdd = float(q.sum()), _max_dd_r(q)
        loo[name] = {'available': True, 'without_r_sum': _num(qs), 'without_max_dd_r': _num(qdd),
                     'weight_reallocation_delta_r_sum': _num(full_sum - qs),
                     'weight_reallocation_delta_max_dd_r': _num(full_dd - qdd)}
    drops = {}
    for name, packet in named_packets.items():
        outside = [t for t in packet['trades']
                   if not (start <= pd.Timestamp(t['t_out']).tz_convert(CAIRO).tz_localize(None).normalize() <= end)]
        drops[name] = {'trades': len(outside), 'r_sum': _num(sum(float(t['R']) for t in outside))}
    return {
        'available': True, 'basis': 'daily realised trade R, Cairo exit day, common calendar window',
        'joint_engine_replay': False,
        'warning': 'separately-run sleeves; research attribution only, not shared-capital execution proof',
        'window': {'start': str(start.date()), 'end': str(end.date()), 'days': len(frame)},
        'alignment_drops': drops,
        'weights': {n: _num(w[i]) for i, n in enumerate(names)},
        'calendar_zero_correlation': {a: {b: _num(frame.corr().loc[a, b]) for b in names} for a in names},
        'active_overlap_correlation': {a: {b: _num(active_corr.loc[a, b]) for b in names} for a in names},
        'annualized_vol_r': _num(pv),
        'volatility_contribution': {
            n: {'marginal': _num(marginal[i]), 'component': _num(component[i]),
                'percent': _num(pct[i])} for i, n in enumerate(names)},
        'portfolio_r_sum': _num(full_sum), 'portfolio_max_dd_r': _num(full_dd),
        'leave_one_out': loo,
    }


def build(packet_paths: list[str], cache: str, *, cache_extra: str | None = None,
          iterations: int = 500, seed: int | None = None, names: list[str] | None = None) -> dict:
    if not packet_paths:
        raise BenchmarkError('at least one --packet is required')
    loaded = [load_packet(p) for p in packet_paths]
    packets = [x[0] for x in loaded]
    digests = [x[1] for x in loaded]
    if len(set(digests)) != len(digests):
        raise BenchmarkError('duplicate candidate packets are not independent tests')
    min_iterations = max(MIN_ITERATIONS, 20 * len(packets) - 1)
    if iterations < min_iterations:
        raise BenchmarkError(f'{len(packets)} packets need at least {min_iterations} iterations so the minimum empirical '
                             'p-value can reach the Bonferroni threshold')
    if names is None:
        names = [f"{p['strategy']['key']}:{p['side']}:{p['strategy']['mgmt_mode']}" for p in packets]
    if len(names) != len(packets) or len(set(names)) != len(names):
        raise BenchmarkError('names must be unique and match the number of packets')
    controls = {}
    for i, (name, packet) in enumerate(zip(names, packets)):
        derived = seed + i if seed is not None else None
        controls[name] = random_entry_control(packet, cache, cache_extra=cache_extra,
                                              iterations=iterations, seed=derived)
    threshold = .05 / len(controls)
    doc = {
        'format': FORMAT, 'non_promotable': True,
        'purpose': 'shadow statistical controls; no live/PAPER entry authority',
        'created_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'inputs': [{'name': n, 'path': os.path.abspath(p), 'packet_sha256': d}
                   for n, p, d in zip(names, packet_paths, digests)],
        'random_entry_controls': controls,
        'multiple_testing': {
            'method': 'Bonferroni within this benchmark batch', 'alpha': .05,
            'tests_in_batch': len(controls), 'threshold': _num(threshold),
            'minimum_iterations_for_threshold': min_iterations,
            'passes_proxy_bonferroni': {
                n: bool(c['observed_proxy_net_return_sum'] > 0 and c['empirical_p_one_sided'] <= threshold)
                for n, c in controls.items()},
            'scope_warning': 'historical ledger trials are additional; this batch correction is a lower bound',
        },
        'portfolio': portfolio_contribution(dict(zip(names, packets))),
        'limitations': [
            'matched-horizon proxy does not replay stop/TP/signal-exit management',
            'portfolio attribution uses separately-run sleeves and does not prove shared-capital execution',
            'multiple-testing correction remains required across the complete experiment ledger',
        ],
    }
    doc['benchmark_sha256'] = _benchmark_digest(doc)
    return doc


def write_once(doc: dict, path: str) -> bool:
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = None
        if isinstance(old, dict):
            try:
                valid_old = _benchmark_digest(old) == old.get('benchmark_sha256')
            except (AttributeError, KeyError, TypeError, ValueError):
                valid_old = False
            if valid_old and old['benchmark_sha256'] == doc.get('benchmark_sha256'):
                return False                     # same evidence: recoverable retry after a later ledger failure
        raise BenchmarkError(f'{path} exists with different evidence; benchmark artifacts are never overwritten')
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'x', encoding='utf-8', newline='\n') as f:
        json.dump(doc, f, sort_keys=True, indent=1, allow_nan=False)
    return True


def append_baseline(ledger_path: str, packet: dict, benchmark: dict, *, author: str,
                    cairo_date: str, lineage: str | None = None) -> dict:
    if len(benchmark['inputs']) != 1:
        raise BenchmarkError('one ledger baseline record must bind exactly one candidate packet')
    p = benchmark['inputs'][0]
    c = next(iter(benchmark['random_entry_controls'].values()))
    candidate = f"{packet['strategy']['key']}:{packet['side']}:{packet['strategy']['mgmt_mode']}"
    return LEDGER.append(
        ledger_path, kind='baseline', candidate_id=candidate, split='development',
        window={'start': packet['window']['start'], 'end': packet['window']['end']},
        author=author, cairo_date=cairo_date, manifest_digest=None, run_digest=None, lineage=lineage,
        detail={'benchmark_format': FORMAT, 'benchmark_sha256': benchmark['benchmark_sha256'],
                'packet_sha256': p['packet_sha256'], 'control': c['kind'],
                'iterations': c['iterations'], 'seed': c['seed'],
                'empirical_p_one_sided': c['empirical_p_one_sided']})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--packet', action='append', required=True)
    ap.add_argument('--name', action='append')
    ap.add_argument('--cache', required=True)
    ap.add_argument('--cache-extra')
    ap.add_argument('--iterations', type=int, default=500)
    ap.add_argument('--seed', type=int)
    ap.add_argument('--out', required=True)
    ap.add_argument('--ledger', help='optional zb-ledger/1 family file; single --packet only')
    ap.add_argument('--lineage', help='required by zb-ledger/1 when --ledger starts a new family')
    ap.add_argument('--author', default='codex')
    a = ap.parse_args(argv)
    try:
        doc = build(a.packet, a.cache, cache_extra=a.cache_extra, iterations=a.iterations,
                    seed=a.seed, names=a.name)
        write_once(doc, a.out)
        if a.ledger:
            if len(a.packet) != 1:
                raise BenchmarkError('--ledger requires exactly one --packet')
            packet, _ = load_packet(a.packet[0])
            today = datetime.now(CAIRO).strftime('%Y-%m-%d')
            append_baseline(a.ledger, packet, doc, author=a.author, cairo_date=today,
                            lineage=a.lineage)
    except (BenchmarkError, LEDGER.LedgerError, OSError, ValueError) as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    print(f"shadow benchmark {doc['benchmark_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
