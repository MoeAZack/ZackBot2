"""EXEC-CAL-01 (PAPER / research only): empirical execution calibration from the sanitized fill audit (fills.jsonl).

Read-only over the telemetry the engine already writes (T05 `Engine._fill_rec`); never touches an order path, settings or
the frozen research runners. Three parts:

1. `read_fills` + `build_profile`: bounded profiles by order kind x symbol liquidity tier x UTC hour bucket (all
   prespecified below). A sparse cell collapses to the first parent level with enough rows and records which level and how
   far it fell back. Per level: n, outcome counts, maker fill / fallback rates, wait p50/p90, slippage p50/p90/worst
   (bps, signed against the trade: adverse = positive, relative to the decision price recorded in the audit), fee rate,
   first/last timestamps, stale flag. Malformed rows are rejected (counted by reason), suspicious ones flagged.
2. The frozen profile: canonical JSON (sorted keys, rounded numbers), schema version, sha256 digest over everything but
   the digest, a source-window description (file basenames, counts, first/last time - no ids, no paths) and thresholds.
3. Opt-in use in research: `cost_params` / `backtest_params` (pure) map a frozen profile + mode ('median' = p50,
   'pessimistic' = p90) to slippage / fee parameters with provenance (digest, mode, level used); `calibrated_backtest`
   swaps backtest.FEE / SLIP / FEE_MAKER for one run and always restores them (backtest.py itself is not edited, so a run
   without the hook is byte-identical by construction). `promotable(profile, now)` refuses with reasons.

Output never carries record ids, order / client ids, account data or symbols' raw rows - only bucket aggregates.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

SCHEMA = 'zb-exec-cal/1'

# ---- prespecified buckets (frozen with the schema; changing any of them is a schema bump) ---------------------------------
FILL_KINDS = ('entry_market', 'entry_maker', 'entry_fallback', 'pyramid_add', 'safety_order', 'exit')   # == Engine.FILL_KINDS
MAKER_KINDS = ('entry_maker',)
ENTRY_KINDS = ('entry_market', 'entry_maker', 'entry_fallback', 'pyramid_add', 'safety_order')       # buy == LONG
KIND_CLASS = {k: ('maker' if k in MAKER_KINDS else 'taker') for k in FILL_KINDS}
# Liquidity tiers by the bot's own static universe (engine.CORE8 / engine.TOP40, mirrored and pinned by a test):
#   L1 = BTC, ETH;  L2 = the rest of CORE8;  L3 = the rest of TOP40;  L4 = anything else (outside the scope lock).
_CORE8 = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT')
_TOP40 = _CORE8 + ('SANDUSDT', 'ZECUSDT', 'QNTUSDT', 'WLDUSDT', 'NEARUSDT', 'SUIUSDT', 'UNIUSDT', 'ONEUSDT', 'MOVRUSDT',
                   'STRKUSDT', 'ZROUSDT', 'ENAUSDT', 'TAOUSDT', 'AAVEUSDT', 'LTCUSDT', '1000PEPEUSDT', 'MANAUSDT', 'ADAUSDT',
                   'ONDOUSDT', 'FILUSDT', 'ARBUSDT', 'SUPERUSDT', 'BCHUSDT', 'DOTUSDT', 'ENJUSDT', 'INJUSDT', 'GALAUSDT',
                   'ARKUSDT', 'FETUSDT', 'XLMUSDT', 'ETCUSDT', 'APTUSDT')
LIQ_TIERS = {'L1': ('BTCUSDT', 'ETHUSDT'), 'L2': _CORE8[2:], 'L3': _TOP40[8:], 'L4': ()}
HOUR_BUCKETS = (('h00_06', 0, 6), ('h06_12', 6, 12), ('h12_18', 12, 18), ('h18_24', 18, 24))   # UTC, [lo, hi)
LEVELS = ('kind_liq_hour', 'kind_liq', 'kind', 'class', 'all')                                 # collapse order, 0..4

# Assumed Binance USD-M VIP0 schedule: used only when a record carries no measured fee (fee_basis says which).
FEE_SCHEDULE = {'maker': 0.0002, 'taker': 0.0005}

THRESHOLDS = dict(
    min_cell_n=30,             # a cell with fewer rows uses its parent level (p90 of < 30 points is mostly noise)
    min_profile_n=200,         # promotion: total usable rows across the profile
    min_slip_coverage=0.80,    # promotion: share of filled rows with a valid slippage measurement
    max_age_days=14,           # a cell / profile whose newest row is older than this is stale
    max_stale_share=0.25,      # promotion: share of populated cells that may be stale
    max_rejected_share=0.05,   # promotion: share of rows rejected as malformed
    slip_outlier_bps=1000.0,   # |slippage| above this is flagged and kept out of the slippage statistics
    slip_mismatch_bps=0.5,     # recorded slip_bps vs recomputed from prices: flagged above this, recomputed value used
)
MODES = {'median': 'p50', 'pessimistic': 'p90'}
_NUM = ('expected', 'actual', 'signal_px', 'slip_bps', 'qty_req', 'qty_fill', 'wait_s', 'fee', 'commission', 'fee_rate')
_OUTCOMES = ('filled', 'partial', 'unfilled', 'unknown')


class CalibrationError(ValueError):
    pass


# ---- helpers ----------------------------------------------------------------------------------------------------------
def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def digest_of(profile: dict) -> str:
    return hashlib.sha256(canonical({k: v for k, v in profile.items() if k != 'digest'})).hexdigest()


def _r(x, nd):
    return None if x is None else round(float(x), nd) + 0.0


def _pct(vals, p):
    """Nearest-rank percentile (deterministic, no interpolation)."""
    if not vals: return None
    v = sorted(vals)
    return v[max(1, math.ceil(p * len(v))) - 1]


def _iso(dt):
    return None if dt is None else dt.astimezone(timezone.utc).isoformat(timespec='seconds')


def _parse_t(s):
    if not isinstance(s, str): return None
    try: dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError: return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else None


def liq_tier(symbol: str) -> str:
    for tier, syms in LIQ_TIERS.items():
        if symbol in syms: return tier
    return 'L4'


def hour_bucket(h: int) -> str:
    for name, lo, hi in HOUR_BUCKETS:
        if lo <= h < hi: return name
    raise ValueError(f'hour {h} outside 0..23')


def level_keys(kind, liq, hb):
    """The keys of one leaf cell at every collapse level, most specific first."""
    return (f'{kind}|{liq}|{hb}', f'{kind}|{liq}', kind, KIND_CLASS[kind], 'all')


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# ---- 1. reader ----------------------------------------------------------------------------------------------------------
def normalize(rec, th=THRESHOLDS):
    """One raw fills.jsonl record -> (row, None, flags) or (None, reject_reason, []). Only the fields the profile needs are
    kept; ids and anything else on the record are dropped here."""
    if not isinstance(rec, dict): return None, 'not_object', []
    kind = rec.get('kind')
    if not isinstance(kind, str) or not kind: return None, 'no_kind', []
    if kind not in FILL_KINDS: return None, 'unknown_kind', []
    t = _parse_t(rec.get('time'))
    if t is None: return None, 'bad_time', []
    sym, side, buy = rec.get('symbol'), rec.get('side'), rec.get('buy')
    if not isinstance(sym, str) or not sym.isascii() or not sym.isalnum() or not sym.endswith('USDT'): return None, 'bad_symbol', []
    if side not in ('LONG', 'SHORT'): return None, 'bad_side', []
    if not isinstance(buy, bool): return None, 'bad_buy', []
    if buy != ((side == 'LONG') if kind in ENTRY_KINDS else (side == 'SHORT')): return None, 'buy_side_mismatch', []
    for k in _NUM:
        v = rec.get(k)
        if v is not None and not _finite(v): return None, f'bad_{k}', []
    outcome = rec.get('outcome')
    if outcome is None: outcome = 'unknown'
    if outcome not in _OUTCOMES: return None, 'bad_outcome', []
    flags = []
    filled = outcome in ('filled', 'partial')
    ref = rec.get('signal_px') if _finite(rec.get('signal_px')) and rec['signal_px'] > 0 else rec.get('expected')
    act = rec.get('actual')
    slip = None
    if filled and _finite(ref) and ref > 0 and _finite(act) and act > 0:
        slip = (act - ref) / ref * 1e4 * (1 if buy else -1)
        rs = rec.get('slip_bps')
        if _finite(rs) and ref == rec.get('expected') and abs(rs - slip) > th['slip_mismatch_bps']: flags.append('slip_mismatch')
        if abs(slip) > th['slip_outlier_bps']: flags.append('slip_outlier'); slip = None
    elif not filled and act:
        flags.append('price_on_unfilled')
    elif filled:
        flags.append('no_fill_price')
    fee_rate = None
    if filled:
        if _finite(rec.get('fee_rate')): fee_rate = float(rec['fee_rate'])
        else:
            fee = rec.get('fee') if rec.get('fee') is not None else rec.get('commission')
            q = rec.get('qty_fill')
            if _finite(fee) and _finite(act) and act > 0 and _finite(q) and q > 0: fee_rate = abs(fee) / (act * q)
        if fee_rate is not None and not (0 <= fee_rate <= 0.01): flags.append('fee_out_of_range'); fee_rate = None
    wait = rec.get('wait_s')
    if wait is not None and wait < 0: flags.append('negative_wait'); wait = None
    row = dict(kind=kind, liq=liq_tier(sym), hb=hour_bucket(t.hour), t=t, outcome=outcome, slip=slip, wait=wait,
               fee_rate=fee_rate, fb_attempt=rec.get('fallback_attempted') is True,
               fb_confirmed=rec.get('fallback_confirmed') is True)
    return row, None, flags


def read_fills(paths, th=THRESHOLDS):
    """Read fills.jsonl-format files in the order given (pass `fills.jsonl.1` before `fills.jsonl`). Missing files are
    skipped. Returns (rows, source) where source has counts only."""
    rows, rejected, flagged, files, n_lines = [], Counter(), Counter(), [], 0
    for p in paths:
        if not os.path.exists(p): continue
        files.append(os.path.basename(p))
        with open(p, encoding='utf-8', errors='replace') as fh:
            for ln in fh:
                if not ln.strip(): continue
                n_lines += 1
                try: rec = json.loads(ln)
                except ValueError: rejected['bad_json'] += 1; continue
                row, why, flags = normalize(rec, th)
                if row is None: rejected[why] += 1; continue
                flagged.update(flags); rows.append(row)
    return rows, dict(files=files, rows_read=n_lines, rows_used=len(rows), rejected=dict(sorted(rejected.items())),
                      flagged=dict(sorted(flagged.items())))


# ---- 2. profile --------------------------------------------------------------------------------------------------------
def _stats(rows, kind_class, as_of, max_age):
    n = len(rows)
    oc = Counter(r['outcome'] for r in rows)
    filled = oc['filled'] + oc['partial']
    slips = [r['slip'] for r in rows if r['slip'] is not None]
    waits = [r['wait'] for r in rows if r['wait'] is not None]
    fees = [r['fee_rate'] for r in rows if r['fee_rate'] is not None]
    first, last = min(r['t'] for r in rows), max(r['t'] for r in rows)
    maker = kind_class == 'maker'
    if fees: fee, basis = _pct(fees, 0.5), 'measured_p50'
    elif kind_class in FEE_SCHEDULE: fee, basis = FEE_SCHEDULE[kind_class], 'assumed_vip0'
    else: fee, basis = None, 'mixed_unmeasured'
    return dict(
        n=n, filled=oc['filled'], partial=oc['partial'], unfilled=oc['unfilled'], unknown=oc['unknown'],
        fill_rate=_r(filled / n, 4), unfilled_rate=_r(oc['unfilled'] / n, 4),
        maker_fill_rate=_r(filled / n, 4) if maker else None,
        fallback_rate=_r(sum(r['fb_attempt'] for r in rows) / n, 4) if maker else None,
        fallback_confirmed_rate=_r(sum(r['fb_confirmed'] for r in rows) / n, 4) if maker else None,
        wait_n=len(waits), wait_p50_s=_r(_pct(waits, .5), 2), wait_p90_s=_r(_pct(waits, .9), 2),
        slip_n=len(slips), slip_p50_bps=_r(_pct(slips, .5), 2), slip_p90_bps=_r(_pct(slips, .9), 2),
        slip_worst_bps=_r(max(slips), 2) if slips else None,
        fee_rate=_r(fee, 6), fee_n=len(fees), fee_basis=basis,
        first=_iso(first), last=_iso(last), stale=last < as_of - max_age)


def build_profile(rows, source, as_of, thresholds=None):
    """The frozen profile dict (digest included). as_of: tz-aware datetime the staleness is judged against."""
    th = dict(THRESHOLDS, **(thresholds or {}))
    if as_of.tzinfo is None: raise CalibrationError('as_of must be timezone-aware')
    as_of = as_of.astimezone(timezone.utc).replace(microsecond=0)
    max_age = timedelta(days=th['max_age_days'])
    groups = {}
    for r in rows:
        for k in level_keys(r['kind'], r['liq'], r['hb']): groups.setdefault(k, []).append(r)
    levels = {}
    for k, g in groups.items():
        kc = k if k in ('all', 'maker', 'taker') else KIND_CLASS[k.split('|')[0]]
        levels[k] = _stats(g, kc, as_of, max_age)
    cells = {}
    for leaf in sorted({level_keys(r['kind'], r['liq'], r['hb'])[0] for r in rows}):
        kind, liq, hb = leaf.split('|')
        chain = level_keys(kind, liq, hb)
        use = next(((i, k) for i, k in enumerate(chain) if levels[k]['n'] >= th['min_cell_n']), None)
        cells[leaf] = dict(n=levels[leaf]['n'], stale=levels[leaf]['stale'],
                           use=use[1] if use else None, fallback_level=use[0] if use else None,
                           fallback_name=LEVELS[use[0]] if use else 'below_min_at_all_levels')
    filled_n = sum(1 for r in rows if r['outcome'] in ('filled', 'partial'))
    slip_n = sum(1 for r in rows if r['slip'] is not None)
    n_cells = len(cells)
    total_seen = source.get('rows_read', len(rows))
    rej = sum(source.get('rejected', {}).values())
    prof = dict(
        schema=SCHEMA, thresholds={k: th[k] for k in sorted(th)},
        buckets=dict(kinds=list(FILL_KINDS), kind_class=KIND_CLASS, liq_tiers={k: list(v) for k, v in LIQ_TIERS.items()},
                     liq_rule='L1 BTC/ETH, L2 rest of CORE8, L3 rest of TOP40, L4 other',
                     hours_utc=[[n, lo, hi] for n, lo, hi in HOUR_BUCKETS], levels=list(LEVELS)),
        source=dict(source, as_of=_iso(as_of), first=_iso(min(r['t'] for r in rows)) if rows else None,
                    last=_iso(max(r['t'] for r in rows)) if rows else None,
                    ref_basis='decision price: signal_px when recorded, else expected',
                    slip_sign='bps against the trade: adverse positive (buy above / sell below the reference)',
                    fee_basis='measured fee/commission when recorded, else assumed VIP0 schedule', fee_schedule=FEE_SCHEDULE,
                    percentile='nearest-rank'),
        coverage=dict(rows=len(rows), filled=filled_n, slip_n=slip_n,
                      slip_coverage=_r(slip_n / filled_n, 4) if filled_n else 0.0,
                      cells=n_cells, cells_collapsed=sum(1 for c in cells.values() if c['fallback_level'] != 0),
                      stale_share=_r(sum(c['stale'] for c in cells.values()) / n_cells, 4) if n_cells else 1.0,
                      rejected_share=_r(rej / total_seen, 4) if total_seen else 0.0),
        levels={k: levels[k] for k in sorted(levels)}, cells=cells)
    prof['digest'] = digest_of(prof)
    return prof


def dumps_profile(profile) -> str:
    return json.dumps(profile, sort_keys=True, indent=1, ensure_ascii=True, allow_nan=False) + '\n'


def save_profile(profile, path):
    with open(path, 'w', encoding='utf-8', newline='\n') as fh: fh.write(dumps_profile(profile))


def load_profile(path):
    with open(path, encoding='utf-8') as fh: return json.load(fh)


# ---- 4. promotion guard ------------------------------------------------------------------------------------------------
def promotable(profile, now, thresholds=None):
    """(ok, reasons). Thresholds come from the profile but can never be looser than THRESHOLDS (or the override)."""
    reasons = []
    if not isinstance(profile, dict): return False, ['malformed_profile']
    if profile.get('schema') != SCHEMA: reasons.append('schema_mismatch')
    d = profile.get('digest')
    if not isinstance(d, str): reasons.append('digest_missing')
    else:
        try:
            if digest_of(profile) != d: reasons.append('digest_mismatch')
        except (TypeError, ValueError): reasons.append('malformed_profile')
    base = dict(THRESHOLDS, **(thresholds or {}))
    pt = profile.get('thresholds') if isinstance(profile.get('thresholds'), dict) else {}
    def tight(k, bigger_is_stricter):
        v = pt.get(k, base[k])
        if not _finite(v): return base[k]
        return max(v, base[k]) if bigger_is_stricter else min(v, base[k])
    min_n, min_cov = tight('min_profile_n', True), tight('min_slip_coverage', True)
    max_age, max_stale, max_rej = tight('max_age_days', False), tight('max_stale_share', False), tight('max_rejected_share', False)
    cov = profile.get('coverage') if isinstance(profile.get('coverage'), dict) else {}
    src = profile.get('source') if isinstance(profile.get('source'), dict) else {}
    if now.tzinfo is None: raise CalibrationError('now must be timezone-aware')
    last = _parse_t(src.get('last'))
    if last is None: reasons.append('no_rows')
    else:
        if last > now + timedelta(minutes=5): reasons.append('future_dated')
        if now - last > timedelta(days=max_age): reasons.append('stale_profile')
    if not _finite(cov.get('rows')) or cov['rows'] < min_n: reasons.append('small_sample')
    if not _finite(cov.get('slip_coverage')) or cov['slip_coverage'] < min_cov: reasons.append('insufficient_slip_coverage')
    lv = profile.get('levels') if isinstance(profile.get('levels'), dict) else {}
    taker = lv.get('taker') or {}
    if not _finite(taker.get('slip_n')) or taker['slip_n'] < tight('min_cell_n', True): reasons.append('no_taker_slippage')
    if not _finite(cov.get('stale_share')) or cov['stale_share'] > max_stale: reasons.append('stale_cells')
    if not _finite(cov.get('rejected_share')) or cov['rejected_share'] > max_rej: reasons.append('rejected_share')
    return not reasons, reasons


# ---- 3. opt-in application ---------------------------------------------------------------------------------------------
def _check(profile, mode):
    if mode not in MODES: raise CalibrationError(f'mode must be one of {sorted(MODES)}')
    if not isinstance(profile, dict) or profile.get('schema') != SCHEMA: raise CalibrationError('schema mismatch')
    if profile.get('digest') != digest_of(profile): raise CalibrationError('digest mismatch: profile was modified')


def _slip_level(profile, chain):
    """First level in the chain with enough slippage samples."""
    mn = profile['thresholds']['min_cell_n']
    for i, k in enumerate(chain):
        st = profile['levels'].get(k)
        if st and st['slip_n'] >= mn: return i, k, st
    raise CalibrationError('no level has enough slippage samples')


def cost_params(profile, mode, kind, symbol, hour):
    """Per-fill parameters for one simulated fill. Slippage never credits a favourable median (floored at 0)."""
    _check(profile, mode)
    if kind not in FILL_KINDS: raise CalibrationError(f'unknown kind {kind!r}')
    h = hour.astimezone(timezone.utc).hour if isinstance(hour, datetime) else int(hour)
    chain = level_keys(kind, liq_tier(symbol), hour_bucket(h))
    i, k, st = _slip_level(profile, chain)
    raw = st['slip_p50_bps' if mode == 'median' else 'slip_p90_bps']
    bps = max(0.0, raw)
    fst = profile['levels'].get(k) or {}
    fee = fst.get('fee_rate') if fst.get('fee_rate') is not None else FEE_SCHEDULE[KIND_CLASS[kind]]
    return dict(slip_bps=bps, slip_frac=bps / 1e4, fee_rate=fee,
                provenance=dict(schema=SCHEMA, digest=profile['digest'], mode=mode, stat=MODES[mode], level=k,
                                fallback_level=i, floored=raw < 0, fee_basis=fst.get('fee_basis', 'assumed_vip0')))


def backtest_params(profile, mode):
    """Scalar parameters for backtest.run (one SLIP for every fill, taker FEE, FEE_MAKER): taker class pooled; the maker
    fee only when the profile has maker rows, else None (= backtest default kept)."""
    _check(profile, mode)
    i, k, st = _slip_level(profile, ('taker', 'all'))
    raw = st['slip_p50_bps' if mode == 'median' else 'slip_p90_bps']
    bps = max(0.0, raw)
    mk = profile['levels'].get('maker') or {}
    return dict(SLIP=bps / 1e4, FEE=st['fee_rate'], FEE_MAKER=mk.get('fee_rate'),
                provenance=dict(schema=SCHEMA, digest=profile['digest'], mode=mode, stat=MODES[mode], level=k,
                                slip_bps=bps, floored=raw < 0, fee_basis=st['fee_basis'],
                                maker_fee_basis=mk.get('fee_basis', 'backtest_default'),
                                source_window=[profile['source'].get('first'), profile['source'].get('last')]))


@contextlib.contextmanager
def calibrated_backtest(profile, mode, now=None):
    """Opt-in: backtest.FEE / SLIP / FEE_MAKER replaced for the duration, always restored. Yields the provenance (which
    also says whether the profile is promotable at `now` - a result from an unpromotable profile is labelled, not hidden)."""
    import backtest as BT
    p = backtest_params(profile, mode)
    ok, why = promotable(profile, now or datetime.now(timezone.utc))
    prov = dict(p['provenance'], promotable=ok, refusals=why)
    old = (BT.FEE, BT.SLIP, BT.FEE_MAKER)
    try:
        BT.FEE, BT.SLIP = p['FEE'], p['SLIP']
        if p['FEE_MAKER'] is not None: BT.FEE_MAKER = p['FEE_MAKER']
        yield prov
    finally:
        BT.FEE, BT.SLIP, BT.FEE_MAKER = old


def run_backtest(book, sleeves, profile=None, mode='median', now=None, **kw):
    """backtest.run with an optional calibration. profile None = exactly backtest.run(book, sleeves, **kw)."""
    import backtest as BT
    if profile is None: return BT.run(book, sleeves, **kw)
    with calibrated_backtest(profile, mode, now) as prov:
        tr, cv = BT.run(book, sleeves, **kw)
    try: cv.attrs['exec_calibration'] = prov
    except AttributeError: pass
    return tr, cv


# ---- CLI: read-only run over local fills --------------------------------------------------------------------------------
def summary(profile):
    lv = profile['levels']
    out = dict(digest=profile['digest'], rows=profile['coverage']['rows'], slip_coverage=profile['coverage']['slip_coverage'],
               stale_share=profile['coverage']['stale_share'], rejected=profile['source']['rejected'],
               flagged=profile['source']['flagged'], window=[profile['source']['first'], profile['source']['last']], by_kind={})
    for k in FILL_KINDS:
        if k in lv:
            s = lv[k]
            out['by_kind'][k] = {f: s[f] for f in ('n', 'fill_rate', 'maker_fill_rate', 'fallback_rate', 'slip_n',
                                                   'slip_p50_bps', 'slip_p90_bps', 'wait_p50_s', 'fee_rate', 'fee_basis')}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description='EXEC-CAL-01: frozen execution profile from fills.jsonl (read-only)')
    ap.add_argument('--fills', action='append', help='fills.jsonl-format file (repeat; older first). '
                                                      'Default: %%LOCALAPPDATA%%/ZackBot/fills.jsonl.1 + fills.jsonl')
    ap.add_argument('--out', required=True, help='directory for profile.json')
    a = ap.parse_args(argv)
    if not a.fills:
        d = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'ZackBot')
        a.fills = [os.path.join(d, 'fills.jsonl.1'), os.path.join(d, 'fills.jsonl')]
    rows, src = read_fills(a.fills)
    now = datetime.now(timezone.utc)
    prof = build_profile(rows, src, now)
    os.makedirs(a.out, exist_ok=True)
    save_profile(prof, os.path.join(a.out, 'profile.json'))
    ok, why = promotable(prof, now)
    print(json.dumps(dict(summary(prof), promotable=ok, refusals=why), indent=1, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
