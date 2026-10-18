"""Sealed USDT/USDC paired runner for quote_pair_usdc_shadow.v0 (USDC-PAIR-RUNNER, Codex 6245713293).

Research only, fail closed. Each strategy-side cell runs independently on each quote with quote-local BTC context; the
whole study is refused BEFORE any simulation on a missing / incomplete twin, a next-open, mark, funding, rules or
status defect, a pair mismatch, an identical file hash or bit-identical aligned OHLC. Episodes pair only on the exact
(strategy, side, base, entry timestamp); unmatched signals are kept for the agreement metrics, never zero-filled.
No packet or verdict is written unless every required cell is complete, and `USDC BETTER` is never available here
(the CI recipe is not frozen). No routing, runtime file, strategy change or install.
"""
import argparse
import collections
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

import backtest as BT  # noqa: E402
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
PRICING_READY = True                   # view A (Codex 6245962231); False refuses before any simulation
SLIP_C = 0.05                          # slip-v1 c (SLIP-C-UNFITTED), as range_vwap_rev
SCEN = C.STRESS['base']


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
    """One strategy-side cell on one quote: the app backtest (backtest.run) with the opt-in fill log. BTC<quote> is the
    book's first symbol (its BTC context). The legacy min-notional floor (backtest.MIN_NOTIONAL) lists USDT symbols
    only; for the run each <base><quote> gets its USDT twin's floor, so both quotes face the same entry floor and no
    quote-only episode comes from the table."""
    ctx = 'BTC' + quote
    symbols = [b + quote for b in BASES]
    if list(raw) != symbols:
        raise PairRunnerError(f'{quote}: symbol order is not {symbols} (BTC context first)')
    try:
        VC.assert_bounded(raw, WARM, END)
    except VC.RunnerError as e:
        raise PairRunnerError(str(e)) from None
    floor = {b + quote: BT.MIN_NOTIONAL.get(b + 'USDT', 5) for b in BASES}
    old = BT.MIN_NOTIONAL
    BT.MIN_NOTIONAL = floor
    try:
        book = BT.Book(raw)
        t = VC._ms(book.t)
        if len(t) and t.max() >= END:
            raise PairRunnerError(f'{quote}: the book holds a candle at / after END')
        if book.gaps:
            raise PairRunnerError(f'{quote}: book gaps {book.gaps} after completeness screening')
        cfg = [BC.sleeve(key, side, symbols, {}, SIM['risk'], SIM['max_pos'])]
        log = []
        tr, cv = BT.run(book, cfg, start=SIM['start'], max_lev=SIM['max_lev'], daily_halt=SIM['daily_halt'],
                        warmup=int((t < START).sum()), t0=np.datetime64(START, 'ms'), t1=np.datetime64(END, 'ms'),
                        fund_per_bar=BT.FUND_PER_BAR * BC.TF_SEC / 14400, exchange_rules=None, fill_log=log)
    finally:
        BT.MIN_NOTIONAL = old
    if book.syms[0] != ctx:
        raise PairRunnerError(f'{quote}: backtest did not use quote-local BTC context')
    col = 'le' if side == 'long' else 'se'
    m = (t >= START) & (t < END)
    sig = {s_[:-len(quote)]: [int(x) for x in t[np.asarray(book.raw_sig(key, s_)[col], bool) & m]] for s_ in symbols}
    return {'status': 'simulated', 'context_symbol': ctx, 'key': key, 'side': side, 'quote': quote, 'trades': tr,
            'curve': cv, 'book': book, 'bar_ms': t, 'fills': log, 'signals': sig}


def positions(cell):
    """Group the fill log per position and prove it complete: every closed trade has its entry first, its exit last on
    the trade's exit candle and exit quantity == entry + adds; every other logged position is still open at the end.
    Any mismatch means a fill path went unlogged -> the whole study fails."""
    q, tr = cell['quote'], cell['trades']
    groups = {}
    for f in cell['fills']:
        if f['liq'] != 'taker':
            raise PairRunnerError(f'{q}: a non-taker fill in a market-entry cell ({f["kind"]} {f["sym"]})')
        groups.setdefault((f['sym'], f['pos_i']), []).append(f)
    closed = []
    for r in (tr.itertuples() if len(tr) else ()):
        fills = groups.pop((r.sym, int(r.i_in)), None)
        qin = sum(f['qty'] for f in fills or () if f['kind'] != 'exit')
        qout = sum(f['qty'] for f in fills or () if f['kind'] == 'exit')
        if (not fills or fills[0]['kind'] != 'entry' or fills[-1]['kind'] != 'exit' or fills[-1]['i'] != int(r.i_out)
                or not abs(qin - qout) <= 1e-9 * max(qin, 1e-12)):
            raise PairRunnerError(f'{q}: fill log does not account for the {r.sym} trade entered at bar {r.i_in} '
                                  '(a fill path is unlogged)')
        closed.append((r, fills))
    still_open = []
    for (sym, i_in), fills in groups.items():
        qin = sum(f['qty'] for f in fills if f['kind'] != 'exit')
        qout = sum(f['qty'] for f in fills if f['kind'] == 'exit')
        if fills[0]['kind'] != 'entry' or not qin - qout > 1e-9 * qin:
            raise PairRunnerError(f'{q}: logged {sym} position at bar {i_in} closed without a trade record')
        still_open.append({'base': sym[:-len(q)], 't_entry': int(cell['bar_ms'][i_in])})
    return closed, still_open


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


# ------------------------------------------------------------------ pricing (view A, Codex 6245962231)
_Bar = collections.namedtuple('_Bar', 'high low close')


def _phase(f, o):
    """When a fill happens inside its candle: 'open' (entry, or a stop gapped at the open), 'close' (signal / time exit
    at the close) or 'intra' (every path event)."""
    if f['kind'] == 'entry' or (f['kind'] == 'exit' and f['gap'] and f['why'] == 'stop' and f['ref'] == o):
        return 'open'
    return 'close' if f['kind'] == 'exit' and f['why'] in ('signal', 'time') else 'intra'


def _held(deltas, j, phases):
    return sum(d for b, ph, d in deltas if b < j or (b == j and ph in phases))


def funding_paid(side, deltas, events, marks, bar_ms):
    """Exact funding on the held quantity (+ = paid). An event at a candle boundary T = open of bar j is paid on what was
    held through T: every fill of earlier bars except a close-of-bar exit at T settles after it, and an entry at the open
    of bar j fills after it. An event inside bar j with path fills in that bar charges the worse of the quantity before
    and after those fills (their instant is unknown). Mark = funding-mark proxy v1: the close of the 1h mark bar ending
    at the event's grid slot."""
    paid, rows = 0.0, []
    lo = bar_ms[min(b for b, _, _ in deltas)]
    hi = bar_ms[max(b for b, _, _ in deltas)] + BC.TF_MS
    sd = 'long' if side == 1 else 'short'
    for T, slot, rate in events:
        if not lo < slot <= hi:
            continue
        j = int(np.searchsorted(bar_ms, slot, side='right')) - 1
        if j < len(bar_ms) and bar_ms[j] == slot:
            qs = [_held([(b, ph, d) for b, ph, d in deltas if not (b == j - 1 and ph == 'close')], j, ())]
        else:
            qs = [_held(deltas, j, ('open',)), _held(deltas, j, ('open', 'intra'))]
        qs = [q for q in qs if q > 1e-12]
        if not qs:
            continue
        mark = marks.get(slot - HOUR)
        if mark is None:
            raise PairRunnerError(f'funding mark missing at {slot}')
        c = max(C.funding_cost(sd, q, [(slot, rate, mark)], SCEN) for q in qs)
        paid += c
        rows.append((slot, rate, mark, c))
    return paid, rows


def price_position(r, fills, arr, bar_ms, acc, tick, quote):
    """View A: every fill re-priced with slip-v1 (15 causally closed 4h bars, c = SLIP_C), the 5 bp taker fee and adverse
    tick rounding; exact funding on the held quantity. Never uses the backtest's own P&L."""
    side = fills[0]['side']
    gross_ref = cash = fees = slip = notional = 0.0
    deltas = []
    for f in fills:
        i, ph = f['i'], _phase(f, arr['o'][f['i']])
        e = i if ph == 'close' else i - 1                          # the last candle closed when the fill happens
        if e - C.SLIP_BARS + 1 < 0:
            raise PairRunnerError(f'{quote}: no slip history for a {f["sym"]} fill at bar {i}')
        bars = [_Bar(arr['h'][k], arr['l'][k], arr['c'][k]) for k in range(e - C.SLIP_BARS + 1, e + 1)]
        bps = C.slip_bps(bars, SLIP_C, SCEN.vol)
        px = C.fill_price(f['ref'], f['action'], bps, SCEN, gap=f['gap'], tick=tick)
        sgn = 1 if f['action'] == 'sell' else -1
        cash += sgn * px * f['qty']
        gross_ref += sgn * f['ref'] * f['qty']
        slip += abs(px - f['ref']) * f['qty']
        fees += C.TAKER * px * f['qty'] * SCEN.fee_mult
        notional += px * f['qty']
        deltas.append((i, ph, f['qty'] if f['kind'] != 'exit' else -f['qty']))
    fund, rows = funding_paid(side, deltas, acc['events'], acc['marks'], bar_ms)
    net = cash - fees - fund
    risk, dist, e0 = fills[0]['risk'], fills[0]['stop_dist'], fills[0]['ref']
    i_in, i_out = int(r.i_in), int(r.i_out)
    hi, lo = arr['h'][i_in:i_out + 1], arr['l'][i_in:i_out + 1]
    mfe = float(max(0.0, (hi.max() - e0) if side == 1 else (e0 - lo.min())) / dist)
    mae = float(max(0.0, (e0 - lo.min()) if side == 1 else (hi.max() - e0)) / dist)
    move = side * (fills[-1]['ref'] - e0) / dist
    return {'strategy': None, 'side': None, 'base': r.sym[:-len(quote)], 't_entry': int(bar_ms[i_in]),
            'i_in': i_in, 'i_out': i_out, 'hold_bars': i_out - i_in, 'n_fills': len(fills),
            'R': net / risk, 'gross_r': gross_ref / risk, 'slip_r': slip / risk, 'fee_r': fees / risk,
            'funding_r': fund / risk, 'funding_events': len(rows), 'net': net, 'equity_pct': net / SIM['start'] * 100,
            'spread_r': {str(b): (net - notional * b / 2 / 1e4) / risk for b in SPREAD_BPS},
            'mfe_r': mfe, 'mae_r': mae, 'giveback_r': mfe - move, 'why': fills[-1]['why']}


def accounting_index(acc_sym):
    f, mk = acc_sym['funding'], acc_sym['mark']
    step = acc_sym['cadence_h'] * HOUR
    t = f.fundingTime.to_numpy(np.int64)
    slots = np.rint(t / step).astype(np.int64) * step
    return {'events': list(zip(t.tolist(), slots.tolist(), f.fundingRate.to_numpy(float).tolist())),
            'marks': dict(zip(mk.open_time.to_numpy(np.int64).tolist(), mk.close.to_numpy(float).tolist()))}


def price_cell(cell, quote, accounting):
    """Every closed position of the cell priced under view A; positions still open at the window end are listed, never
    priced or zero-filled."""
    closed, still_open = positions(cell)
    book, eps = cell['book'], []
    idx = {}
    for r, fills in closed:
        sym = r.sym
        if sym not in idx:
            idx[sym] = accounting_index(accounting[sym])
        tick = float(accounting[sym]['rules']['filters']['PRICE_FILTER']['tickSize'])
        e = price_position(r, fills, book.arr[sym], cell['bar_ms'], idx[sym], tick, quote)
        e.update(strategy=cell['key'], side=cell['side'])
        eps.append(e)
    return {'status': 'complete', 'episodes': eps, 'open_at_end': still_open, 'costs': cost_summary(eps),
            'spread_rows': {str(b): {'label': SPREAD_LABEL, 'half_spread_per_fill_bps': b / 2,
                                     'net_r_sum': _r(sum(e['spread_r'][str(b)] for e in eps))} for b in SPREAD_BPS}}


def _r(x):
    return BC._num(float(x)) if x is not None else None


def cost_summary(eps):
    """Per-quote metrics of a priced cell: net / gross R, cost components, PF, max DD (R, by exit order), MFE / MAE /
    giveback / hold means, quote-equity %."""
    if not eps:
        return {'n': 0}
    net = np.array([e['R'] for e in eps])
    order = np.argsort([e['i_out'] for e in eps], kind='stable')
    cum = np.cumsum(net[order])
    dd = float((np.maximum.accumulate(np.r_[0.0, cum]) - np.r_[0.0, cum]).max())
    win, loss = net[net > 0].sum(), -net[net < 0].sum()
    mean = lambda k: _r(np.mean([e[k] for e in eps]))
    return {'n': len(eps), 'net_r_sum': _r(net.sum()), 'net_r_mean': _r(net.mean()),
            'gross_r_sum': _r(sum(e['gross_r'] for e in eps)), 'slip_r_sum': _r(sum(e['slip_r'] for e in eps)),
            'fee_r_sum': _r(sum(e['fee_r'] for e in eps)), 'funding_r_sum': _r(sum(e['funding_r'] for e in eps)),
            'pf': _r(win / loss) if loss > 0 else None, 'max_dd_r': _r(dd), 'win_rate': _r((net > 0).mean()),
            'mfe_r_mean': mean('mfe_r'), 'mae_r_mean': mean('mae_r'), 'giveback_r_mean': mean('giveback_r'),
            'hold_bars_mean': mean('hold_bars'), 'equity_pct_sum': _r(sum(e['equity_pct'] for e in eps))}


def paired_metrics(pairing):
    """USDC minus USDT on the exactly paired episodes only: net / gross R and cost deltas, by quarter and by symbol, and
    the symbol concentration of the absolute net-R delta."""
    p = pairing['paired']
    if not p:
        return {'n': 0}
    d = {k: [y[k] - x[k] for x, y in p] for k in ('R', 'gross_r', 'slip_r', 'fee_r', 'funding_r', 'equity_pct')}
    by_q, by_s = {}, {}
    for (x, y), dr in zip(p, d['R']):
        for grp, k in ((by_q, _quarter(x['t_entry'])), (by_s, x['base'])):
            g = grp.setdefault(k, {'n': 0, 'net_r_delta_sum': 0.0})
            g['n'] += 1; g['net_r_delta_sum'] += dr
    for grp in (by_q, by_s):
        for g in grp.values():
            g['net_r_delta_sum'] = _r(g['net_r_delta_sum'])
    absum = sum(abs(v) for v in d['R'])
    top = max(by_s, key=lambda k: sum(abs(dr) for (x, _), dr in zip(p, d['R']) if x['base'] == k))
    return {'n': len(p), **{f'{k}_delta_sum': _r(sum(v)) for k, v in d.items()},
            'net_r_delta_mean': _r(np.mean(d['R'])), 'by_quarter': dict(sorted(by_q.items())),
            'by_symbol': dict(sorted(by_s.items())), 'top_symbol': top,
            'top_symbol_abs_share': _r(sum(abs(dr) for (x, _), dr in zip(p, d['R']) if x['base'] == top) / absum)
            if absum > 0 else None}


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
        raise PairRunnerError('INCOMPLETE, no packet written: view-A pricing disabled (PRICING_READY) '
                              '(inputs validated; nothing simulated)')
    cells, incomplete = {}, []
    for key, side in CELLS:
        name = f'{key}.{side}'
        sim = {q: run_cell(k[q][1], q, key, side) for q in QUOTES}
        priced = {q: price_cell(sim[q], q, a[q][1]) for q in QUOTES}
        pairing = pair_episodes(*(priced[q].get('episodes', []) for q in QUOTES))
        cells[name] = {'priced': {q: {k_: v for k_, v in p.items() if k_ != 'episodes'} for q, p in priced.items()},
                       'agreement': agreement(sim['USDT']['signals'], sim['USDC']['signals']),
                       'paired': paired_metrics(pairing), 'sufficiency': sufficiency(pairing),
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
