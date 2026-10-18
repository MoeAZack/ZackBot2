"""Sealed USDT/USDC paired runner for quote_pair_usdc_shadow.v0 (USDC-PAIR-RUNNER, Codex 6245713293).

Research only, fail closed. Each strategy-side cell runs independently on each quote with quote-local BTC context; the
whole study is refused BEFORE any simulation on a missing / incomplete twin, a next-open, mark, funding, rules or
status defect, a pair mismatch, an identical file hash or bit-identical aligned OHLC. Episodes pair only on the exact
(strategy, side, base, entry timestamp); unmatched signals are kept for the agreement metrics, never zero-filled.
No packet or verdict is written unless every required cell is complete, and `USDC BETTER` is never available here
(the CI recipe is not frozen). No routing, runtime file, strategy change or install.
"""
import argparse
import hashlib
import io
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import costs as C  # noqa: E402
import run_breadth_candidate as BC  # noqa: E402
import run_vwaprev_candidate as VC  # noqa: E402

FORMAT = 'zb-quote-pair-usdc/1'
CONTRACT = 'quote_pair_usdc_shadow.v0'
KLINES_FORMAT = 'zb-quote-pair-klines/1'
ACCOUNTING_FORMAT = 'quote-pair-accounting/1'
START_S, END_S, WARM_S = '2024-10-14T00:00:00Z', '2026-09-28T00:00:00Z', '2024-09-07T08:00:00Z'
START, END, WARM = (pd.Timestamp(x).value // 1_000_000 for x in (START_S, END_S, WARM_S))
HOUR = 3_600_000
BASES = ('BTC', 'ETH', 'SOL', 'XRP', 'BNB', 'DOGE')
QUOTES = ('USDT', 'USDC')
CELLS = (('exp_trend', 'long'), ('exp_trend', 'short'), ('breakout_pyramid', 'long'), ('bear_breakdown', 'short'))
REQUIRED_FILTERS = ('PRICE_FILTER', 'LOT_SIZE', 'MARKET_LOT_SIZE', 'MIN_NOTIONAL')
FUNDING_CADENCES_H = (1, 4, 8)
MARK_COLS = ['open_time', 'open', 'high', 'low', 'close', 'close_time']
FUNDING_COLS = ['fundingTime', 'fundingRate', 'markPrice']
SPREAD_BPS = (0, 1, 2, 5)
SPREAD_LABEL = 'SPREAD-UNCALIBRATED'
LEAD_LAG_BARS = 6
MIN_PAIRED, MIN_PER_QUOTE, MIN_QUARTERS, MIN_SYMBOLS = 60, 30, 6, 4
SIM = dict(start=10_000.0, max_lev=50.0, daily_halt=1.0, risk=0.01, max_pos=6)
FUNDING_RATE_ABS_MAX = 0.05            # sanity bound per event (Binance caps are far inside it); beyond = a data defect
FILTER_FIELDS = {'PRICE_FILTER': 'tickSize', 'LOT_SIZE': 'stepSize', 'MARKET_LOT_SIZE': 'stepSize',
                 'MIN_NOTIONAL': 'notional'}
PRICED_KEYS = ('status', 'episodes', 'costs', 'spread_rows')    # what a complete priced cell must carry
PRICING_READY = False                  # price_cell is a stub until Codex rules on 6245780479: refuse before simulating


class PairRunnerError(RuntimeError):
    pass


def _read_json(path, what):
    try:
        with open(path, encoding='utf-8') as f:
            m = json.load(f)
    except (OSError, ValueError) as e:
        raise PairRunnerError(f'{what} unreadable ({type(e).__name__})') from None
    if not isinstance(m, dict):
        raise PairRunnerError(f'{what} is not a JSON object')
    return m


def _csv(data, cols, what):
    try:
        df = pd.read_csv(io.BytesIO(data))
    except (ValueError, pd.errors.ParserError) as e:
        raise PairRunnerError(f'{what}: unreadable CSV ({type(e).__name__})') from None
    return _numeric(df, cols, what)


def _file(root, rec, what, sub=None):
    """A manifest file record -> raw bytes, only when the name is a plain file name and the sha256 matches. The file
    sits beside the manifest or in its `sub` folder (the Integration pack layout); both present is ambiguous."""
    name = rec.get('file')
    if not isinstance(name, str) or not name or os.path.basename(name) != name:
        raise PairRunnerError(f'{what}: unsafe or missing file name')
    found = [p for p in [os.path.join(root, name)] + ([os.path.join(root, sub, name)] if sub else []) if os.path.isfile(p)]
    if len(found) > 1:
        raise PairRunnerError(f'{what}: file {name} is present both beside the manifest and in {sub}/ (ambiguous)')
    path = found[0] if found else os.path.join(root, name)
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except OSError:
        raise PairRunnerError(f'{what}: file {name} is missing') from None
    digest = hashlib.sha256(raw).hexdigest()
    if not isinstance(rec.get('sha256'), str) or rec['sha256'].lower() != digest:
        raise PairRunnerError(f'{what}: file {name} does not match its manifest sha256')
    return raw, {'file': name, 'sha256': digest, 'bytes': len(raw)}


def _numeric(df, cols, what):
    if list(df.columns) != cols:
        raise PairRunnerError(f'{what}: columns {list(df.columns)} are not {cols}')
    out = df.apply(pd.to_numeric, errors='coerce')
    if not np.isfinite(out.to_numpy(float)).all():
        raise PairRunnerError(f'{what}: non-finite or non-numeric values')
    return out


def _ohlc_sane(o, h, l, c):
    return bool((l > 0).all() and (h >= np.maximum(o, c)).all() and (l <= np.minimum(o, c)).all())


# ------------------------------------------------------------------ candles
def load_klines(manifest_path, quote):
    """The quote's 4h trade-kline pack (collect_quote_pair_klines format) -> (manifest, {sym: frame sliced}, records).
    Every candle on [WARM, END) present exactly once, positive sane OHLC (so every next open inside the window exists)."""
    m = _read_json(manifest_path, f'{quote} klines manifest')
    w = m.get('window') or {}
    if (m.get('format') != KLINES_FORMAT or m.get('contract') != CONTRACT or m.get('quote') != quote
            or m.get('interval') != '4h' or w.get('start') != START_S or w.get('end') != END_S
            or w.get('end_exclusive') is not True or (m.get('fetch') or {}).get('start') != WARM_S
            or m.get('incomplete') != [] or m.get('stopped') is not None):
        raise PairRunnerError(f'{quote} klines manifest does not prove the frozen complete contract')
    pairs = m.get('pairs')                               # required; a wider pack is fine, every frozen base pairs exactly
    want = {b + quote: b for b in BASES}
    if (not isinstance(pairs, dict) or not set(want) <= set(pairs)
            or any(not isinstance(pairs[s], dict) or pairs[s].get('base') != b
                   or pairs[s].get('usdt_twin') != b + 'USDT'
                   or pairs[s].get('usdc_twin', b + 'USDC') != b + 'USDC' for s, b in want.items())):
        raise PairRunnerError(f'{quote} klines manifest pairs do not match the frozen bases')
    root = os.path.dirname(os.path.abspath(manifest_path))
    raw, files = {}, {}
    for base in BASES:                                   # BTC first: quote-local context for backtest.Book
        sym = base + quote
        rec = (m.get('files') or {}).get(sym) or {}
        if rec.get('status') != 'complete' or (rec.get('validation') or {}).get('status') != 'complete':
            raise PairRunnerError(f'{sym} is INCOMPLETE')
        data, frec = _file(root, rec, sym, quote.lower())
        d = _csv(data, VC.COLS, sym)
        t = d.t.to_numpy(np.int64)
        sliced = VC.slice_frame(d, WARM, END)                    # t -> datetime, as the app book expects
        comp = BC.completeness(sliced, WARM, END)
        if (not comp['complete'] or len(d) != comp['expected']
                or not np.all(np.diff(t) > 0) or not np.array_equal(t, d.t.to_numpy(float))):
            raise PairRunnerError(f'{sym} is INCOMPLETE for the frozen window (missing / off-grid / unordered candles)')
        if int(t[0]) != WARM or int(t[-1]) != END - BC.TF_MS:
            raise PairRunnerError(f'{sym} does not cover exactly [{WARM_S}, {END_S})')
        if not _ohlc_sane(*(d[k].to_numpy(float) for k in 'ohlc')):
            raise PairRunnerError(f'{sym}: next-open / OHLC defect (non-positive or inconsistent prices)')
        raw[sym] = sliced
        files[sym] = dict(frec, rows=int(len(d)))
    _distinct(files, quote, 'kline')
    return m, raw, files


def _distinct(files, quote, what, series=None):
    """Two bases of one quote never share a file (a copied symbol)."""
    seen = {}
    for sym, r in files.items():
        h = (r[series] if series else r)['sha256']
        if h in seen:
            raise PairRunnerError(f'{quote}: {sym} and {seen[h]} share one {what} file (copied symbol)')
        seen[h] = sym


# ------------------------------------------------------------------ accounting (funding, mark, rules)
def check_funding(sym, f):
    """Every funding event of [WARM, END) on ONE fixed UTC grid (1/4/8h, phase 00:00 UTC), each within the timing
    tolerance of its own grid slot - never of the previous event, so jitter cannot accumulate into a skipped event -
    one event per slot with no slot skipped, the slot at / before WARM present and the last slot of the window present.
    A cadence change is refused, not guessed: without a declared interval a skipped event and a switch look alike."""
    t = f.fundingTime.to_numpy(np.int64)
    tol = C.FUNDING_TIME_TOLERANCE_MS
    if len(t) < 2 or not np.all(np.diff(t) > 0):
        raise PairRunnerError(f'{sym}: funding events missing or unordered')
    cad = None
    for h in FUNDING_CADENCES_H:
        step = h * HOUR
        slot = np.rint(t / step).astype(np.int64)
        if np.all(np.abs(t - slot * step) <= tol) and np.all(np.diff(slot) == 1):
            cad = h
            break
    if cad is None:
        raise PairRunnerError(f'{sym}: funding events are not one fixed 1/4/8h UTC grid (missing, extra, shifted '
                              'or switched)')
    first, last = int(slot[0]) * step, int(slot[-1]) * step
    if not WARM - step < first <= WARM:
        raise PairRunnerError(f'{sym}: no funding phase anchor at the warm-up start')
    if not END - step <= last < END:
        raise PairRunnerError(f'{sym}: funding events missing at the window end')
    if (np.abs(f.fundingRate.to_numpy(float)) > FUNDING_RATE_ABS_MAX).any():
        raise PairRunnerError(f'{sym}: funding rate beyond the {FUNDING_RATE_ABS_MAX:g} sanity bound')
    if (f.markPrice.to_numpy(float) <= 0).any():
        raise PairRunnerError(f'{sym}: non-positive funding mark price')
    return cad


def check_mark(sym, mk):
    t = mk.open_time.to_numpy(np.int64)
    grid = np.arange(WARM, END, HOUR, dtype=np.int64)
    if len(t) != len(grid) or not np.array_equal(t, grid):
        raise PairRunnerError(f'{sym}: 1h mark grid is not complete on [{WARM_S}, {END_S})')
    if not np.array_equal(mk.close_time.to_numpy(np.int64), grid + HOUR - 1):
        raise PairRunnerError(f'{sym}: 1h mark close times are off grid')
    if not _ohlc_sane(*(mk[k].to_numpy(float) for k in ('open', 'high', 'low', 'close'))):
        raise PairRunnerError(f'{sym}: mark OHLC defect')


def _pos(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return bool(np.isfinite(v) and v > 0)


def check_rules(sym, base, quote, r):
    r = r if isinstance(r, dict) else {}
    flt = r.get('filters') if isinstance(r.get('filters'), dict) else {}
    if (r.get('status') != 'TRADING' or r.get('baseAsset') != base or r.get('quoteAsset') != quote
            or r.get('contractType') != 'PERPETUAL'
            or any(not isinstance(flt.get(k), dict) or not _pos(flt[k].get(v)) for k, v in FILTER_FIELDS.items())):
        raise PairRunnerError(f'{sym}: exchange rules/status defect (needs TRADING PERPETUAL {base}/{quote} + filters '
                              'with positive tickSize / stepSize / notional)')


def load_accounting(manifest_path, quote):
    """The quote's funding / 1h mark / rules pack (collect_quote_pair_accounting format) -> (manifest, per-symbol
    {funding frame, mark frame, rules, cadence_h}, records). Rules are a CURRENT_SNAPSHOT_ONLY, NON_CAUSAL label."""
    m = _read_json(manifest_path, f'{quote} accounting manifest')
    w = m.get('window') or {}
    want = [b + quote for b in BASES]
    rules = m.get('exchange_rules') or {}
    if (m.get('format') != ACCOUNTING_FORMAT or m.get('quote') != quote or sorted(m.get('symbols') or []) != sorted(want)
            or w.get('warmup_start') != WARM_S or w.get('development_start') != START_S
            or w.get('end_exclusive') != END_S or m.get('incomplete') != [] or m.get('stopped') is not None
            or rules.get('label') != 'CURRENT_SNAPSHOT_ONLY'):
        raise PairRunnerError(f'{quote} accounting manifest does not prove the frozen complete contract')
    root = os.path.dirname(os.path.abspath(manifest_path))
    out, files = {}, {}
    for base in BASES:
        sym = base + quote
        recs = {}
        for series, cols in (('fundingRate', FUNDING_COLS), ('markPriceKlines_1h', MARK_COLS)):
            rec = (m.get('files') or {}).get(f'{sym}/{series}') or {}
            if rec.get('status') != 'complete' or rec.get('symbol') != sym or rec.get('series') != series:
                raise PairRunnerError(f'{sym} {series} is INCOMPLETE')
            data, frec = _file(root, rec, f'{sym} {series}', quote.lower())
            recs[series] = (_csv(data, cols, f'{sym} {series}'), frec)
        f, mk = recs['fundingRate'][0], recs['markPriceKlines_1h'][0]
        cad = check_funding(sym, f)
        check_mark(sym, mk)
        check_rules(sym, base, quote, (rules.get('symbols') or {}).get(sym))
        out[sym] = {'funding': f, 'mark': mk, 'rules': rules['symbols'][sym], 'cadence_h': cad}
        files[sym] = {k: v[1] for k, v in recs.items()}
    for series in ('fundingRate', 'markPriceKlines_1h'):
        _distinct(files, quote, series, series)
    return m, out, files


# ------------------------------------------------------------------ twins
def validate_twins(kt, kc, ft, fc, at=None, ac=None, va=None, vc=None):
    """Refuse an identical file hash or bit-identical aligned OHLC (a copied / relabelled pack), misaligned candles,
    and a funding or mark series shared across the quotes - by file hash and by value (a reformatted copy)."""
    for base in BASES:
        a, b = base + 'USDT', base + 'USDC'
        if ft[a]['sha256'] == fc[b]['sha256']:
            raise PairRunnerError(f'{base}: twin kline files have an identical sha256')
        x, y = kt[a], kc[b]
        if not np.array_equal(x.t.to_numpy(np.int64), y.t.to_numpy(np.int64)):
            raise PairRunnerError(f'{base}: twin timestamps do not align exactly')
        if np.array_equal(x[['o', 'h', 'l', 'c']].to_numpy(float), y[['o', 'h', 'l', 'c']].to_numpy(float)):
            raise PairRunnerError(f'{base}: twin OHLC is bit-identical')
        if at is not None and ac is not None:
            for series in ('fundingRate', 'markPriceKlines_1h'):
                if at[a][series]['sha256'] == ac[b][series]['sha256']:
                    raise PairRunnerError(f'{base}: twin {series} files have an identical sha256')
        if va is not None and vc is not None:
            for series, cols in (('funding', ['fundingTime', 'fundingRate']), ('mark', ['open', 'high', 'low', 'close'])):
                x, y = va[a][series][cols].to_numpy(float), vc[b][series][cols].to_numpy(float)
                if x.shape == y.shape and np.array_equal(x, y):
                    raise PairRunnerError(f'{base}: twin {series} values are identical (copied series)')


def load_all(usdt_klines, usdc_klines, usdt_accounting, usdc_accounting):
    """Every check, for both quotes, before any simulation."""
    k = {q: load_klines(p, q) for q, p in (('USDT', usdt_klines), ('USDC', usdc_klines))}
    a = {q: load_accounting(p, q) for q, p in (('USDT', usdt_accounting), ('USDC', usdc_accounting))}
    validate_twins(k['USDT'][1], k['USDC'][1], k['USDT'][2], k['USDC'][2], a['USDT'][2], a['USDC'][2],
                   a['USDT'][1], a['USDC'][1])
    return k, a


# ------------------------------------------------------------------ simulation
def run_cell(raw, quote, key, side):
    """One strategy-side cell on one quote. BTC<quote> is the book's first symbol (its BTC context). The legacy
    min-notional floor (backtest.MIN_NOTIONAL) lists USDT symbols only; for the run each <base><quote> gets its USDT
    twin's floor, so both quotes face the same entry floor and no quote-only episode comes from the table."""
    ctx = 'BTC' + quote
    symbols = [b + quote for b in BASES]
    if list(raw) != symbols:
        raise PairRunnerError(f'{quote}: symbol order is not {symbols} (BTC context first)')
    try:
        VC.assert_bounded(raw, WARM, END)
    except VC.RunnerError as e:
        raise PairRunnerError(str(e)) from None
    floor = {b + quote: BC.BT.MIN_NOTIONAL.get(b + 'USDT', 5) for b in BASES}
    old = BC.BT.MIN_NOTIONAL
    BC.BT.MIN_NOTIONAL = floor
    try:
        tr, cv, book, pos = BC.simulate(raw, key, side, symbols, {}, START, END, tf_sec=BC.TF_SEC, **SIM)
    except BC.RunnerError as e:
        raise PairRunnerError(str(e)) from None
    finally:
        BC.BT.MIN_NOTIONAL = old
    if book.syms[0] != ctx:
        raise PairRunnerError(f'{quote}: backtest did not use quote-local BTC context')
    t = VC._ms(book.t)
    eps = [{'strategy': key, 'side': side, 'base': r.sym[:-len(quote)], 't_entry': int(t[int(r.i_in)]),
            'i_in': int(r.i_in), 'i_out': int(r.i_out), 'R': float(r.R), 'pnl': float(r.pnl), 'why': str(r.why)}
           for r in tr.itertuples() if r.side == side]
    col = 'le' if side == 'long' else 'se'
    m = (t >= START) & (t < END)
    sig = {s[:-len(quote)]: [int(x) for x in t[np.asarray(book.raw_sig(key, s)[col], bool) & m]] for s in symbols}
    return {'status': 'simulated', 'context_symbol': ctx, 'episodes': eps, 'signals': sig, 'trades': tr, 'curve': cv,
            'bar_ms': t}


# ------------------------------------------------------------------ pairing
def _key(e):
    return (e['strategy'], e['side'], e['base'], e['t_entry'])


def pair_episodes(usdt, usdc):
    """Exact (strategy, side, base, t_entry) pairs. Unmatched episodes are kept on their own side; a duplicate key on
    one side is a defect (it cannot be paired unambiguously)."""
    idx = {}
    for q, eps in (('USDT', usdt), ('USDC', usdc)):
        seen = {}
        for e in eps:
            if _key(e) in seen:
                raise PairRunnerError(f'{q}: duplicate episode key {_key(e)}')
            seen[_key(e)] = e
        idx[q] = seen
    both = sorted(set(idx['USDT']) & set(idx['USDC']))
    return {'paired': [(idx['USDT'][k], idx['USDC'][k]) for k in both],
            'only_usdt': [idx['USDT'][k] for k in sorted(set(idx['USDT']) - set(idx['USDC']))],
            'only_usdc': [idx['USDC'][k] for k in sorted(set(idx['USDC']) - set(idx['USDT']))]}


def agreement(sig_usdt, sig_usdc, tf_ms=BC.TF_MS, window=LEAD_LAG_BARS):
    """Raw entry-signal agreement per base on exact timestamps, plus lead-lag between the unmatched signals: one-to-one
    nearest matching within +/- window bars (positive = USDC later; closest first, a tie to the later USDC signal,
    then the earlier USDT signal). Nothing is zero-filled: a signal left without a partner counts as unpaired."""
    out = {'both': 0, 'only_usdt': 0, 'only_usdc': 0, 'lead_lag_bars': {}, 'unpaired_usdt': 0, 'unpaired_usdc': 0,
           'by_base': {}}
    for base in BASES:
        a, b = set(sig_usdt.get(base, ())), set(sig_usdc.get(base, ()))
        oa, ob = sorted(a - b), sorted(b - a)
        out['by_base'][base] = {'both': len(a & b), 'only_usdt': len(oa), 'only_usdc': len(ob)}
        out['both'] += len(a & b); out['only_usdt'] += len(oa); out['only_usdc'] += len(ob)
        cand = sorted((abs(d), -d, x, y, d) for x in oa for y in ob
                      for d in [(y - x) // tf_ms] if abs(d) <= window)
        used_a, used_b = set(), set()
        for _, _, x, y, d in cand:
            if x in used_a or y in used_b:
                continue
            used_a.add(x); used_b.add(y)
            out['lead_lag_bars'][str(d)] = out['lead_lag_bars'].get(str(d), 0) + 1
        out['unpaired_usdt'] += len(oa) - len(used_a)
        out['unpaired_usdc'] += len(ob) - len(used_b)
    n = out['both'] + out['only_usdt'] + out['only_usdc']
    out['jaccard'] = BC._num(out['both'] / n) if n else None
    out['lead_lag_bars'] = dict(sorted(out['lead_lag_bars'].items(), key=lambda kv: int(kv[0])))
    return out


def _quarter(ms):
    ts = pd.Timestamp(int(ms), unit='ms')
    return f'{ts.year}Q{(ts.month - 1) // 3 + 1}'


def sufficiency(pairing):
    """The frozen exploratory bar: >= 60 paired episodes, >= 30 per quote, >= 6 quarters, >= 4 symbols."""
    p = pairing['paired']
    n_q = {'USDT': len(p) + len(pairing['only_usdt']), 'USDC': len(p) + len(pairing['only_usdc'])}
    quarters = sorted({_quarter(a['t_entry']) for a, _ in p})
    symbols = sorted({a['base'] for a, _ in p})
    ok = len(p) >= MIN_PAIRED and min(n_q.values()) >= MIN_PER_QUOTE and len(quarters) >= MIN_QUARTERS \
        and len(symbols) >= MIN_SYMBOLS
    return {'paired': len(p), 'per_quote': n_q, 'quarters': quarters, 'symbols': symbols,
            'exploratory_eligible': ok,
            'bar': f'>= {MIN_PAIRED} paired, >= {MIN_PER_QUOTE} per quote, >= {MIN_QUARTERS} quarters, '
                   f'>= {MIN_SYMBOLS} symbols'}


# ------------------------------------------------------------------ pricing (pending Codex A/B ruling 6245780479)
def price_cell(cell, quote, accounting):
    """Re-prices every fill at the 5 bp conservative taker fee, slip-v1, exact funding with the causal mark proxy and
    the 0/1/2/5 bp SPREAD-UNCALIBRATED rows. Not implemented until Codex rules on the fill-log design, so a cell is
    never complete and no packet can be written (PRICING_READY stays False)."""
    return {'status': 'INCOMPLETE', 'reason': 'cost re-pricing pending Codex ruling 6245780479 (fill-log A / B)'}


# ------------------------------------------------------------------ run
def run(*, usdt_klines, usdc_klines, usdt_accounting, usdc_accounting, out, repo=ROOT):
    if os.path.exists(out):
        raise PairRunnerError(f'{out} exists; a packet is never overwritten')
    try:
        commit = VC.clean_commit(repo)
    except VC.RunnerError as e:
        raise PairRunnerError(str(e)) from None
    k, a = load_all(usdt_klines, usdc_klines, usdt_accounting, usdc_accounting)      # refuses before any simulation
    if not PRICING_READY:
        raise PairRunnerError('INCOMPLETE, no packet written: cost re-pricing pending Codex ruling 6245780479 '
                              '(inputs validated; nothing simulated)')
    cells, incomplete = {}, []
    for key, side in CELLS:
        name = f'{key}.{side}'
        sim = {q: run_cell(k[q][1], q, key, side) for q in QUOTES}
        priced = {q: price_cell(sim[q], q, a[q][1]) for q in QUOTES}
        pairing = pair_episodes(sim['USDT']['episodes'], sim['USDC']['episodes'])
        cells[name] = {'priced': priced, 'agreement': agreement(sim['USDT']['signals'], sim['USDC']['signals']),
                       'sufficiency': sufficiency(pairing),
                       'pairing': {'paired': [[x['t_entry'], x['base'], x['R'], y['R']] for x, y in pairing['paired']],
                                   'only_usdt': [[e['t_entry'], e['base'], e['R']] for e in pairing['only_usdt']],
                                   'only_usdc': [[e['t_entry'], e['base'], e['R']] for e in pairing['only_usdc']]}}
        for q, p in priced.items():
            if p.get('status') != 'complete' or any(x not in p for x in PRICED_KEYS):
                incomplete.append(f'{name}/{q}: {p.get("reason") or "priced cell lacks " + str(PRICED_KEYS)}')
    if incomplete:
        raise PairRunnerError('INCOMPLETE, no packet written: ' + '; '.join(incomplete))
    pkt = {'format': FORMAT, 'contract': CONTRACT, 'commit': commit, 'tree': 'clean', 'cells': cells,
           'window': {'start': START_S, 'end': END_S, 'end_exclusive': True, 'warmup_start': WARM_S,
                      'holdout_read': False},
           'inputs': {q: {'klines_files': k[q][2], 'accounting_files': a[q][2]} for q in QUOTES},
           'verdict': None, 'usdc_better': 'UNAVAILABLE: CI recipe not frozen', 'non_promotable': True}
    pkt['packet_sha256'] = hashlib.sha256(VC._canon(pkt)).hexdigest()
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'x', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(pkt, sort_keys=True, indent=1, allow_nan=False) + '\n')
    return pkt


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    for q in ('usdt', 'usdc'):
        ap.add_argument(f'--{q}-klines', required=True, help=f'{q.upper()} {KLINES_FORMAT} manifest')
        ap.add_argument(f'--{q}-accounting', required=True, help=f'{q.upper()} {ACCOUNTING_FORMAT} manifest')
    ap.add_argument('--out', required=True)
    x = ap.parse_args(argv)
    try:
        p = run(usdt_klines=x.usdt_klines, usdc_klines=x.usdc_klines, usdt_accounting=x.usdt_accounting,
                usdc_accounting=x.usdc_accounting, out=x.out)
    except PairRunnerError as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    print(f"paired study complete; packet {p['packet_sha256'][:12]} -> {x.out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
