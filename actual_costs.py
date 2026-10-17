"""FEE-ASSET-01: actual commission and funding next to the estimates (observe only, PAPER/testnet).

Pure helpers. The engine owns every mutation: it applies finished fill records and funding rows to its lots / history rows
on its own thread. Nothing here sizes, routes or changes an estimate; `fees` (the FEE_EST-based estimate) is never
overwritten. Amounts stay in the asset they were charged in (no conversion); `fees_actual` is filled only when every fill
of the lot is known and charged in the symbol's quote asset, otherwise it is None and `fees_actual_partial` says why.
Funding follows Binance's income sign: negative = paid by the account, positive = received.
"""
import hashlib, math
from datetime import datetime

COST_FIELDS = ('fees_actual_by_asset', 'fees_actual', 'fees_actual_partial', 'maker_fills', 'taker_fills', 'cost_refs',
               'cost_fills_applied', 'cost_fills_missing', 'cost_refs_done', 'funding_actual', 'funding_by_asset', 'funding_rows')


def _num(v):
    if isinstance(v, bool): return None
    try: x = float(v)
    except (TypeError, ValueError): return None
    return x if math.isfinite(x) else None


def raw_from_trades(trades):
    """trades: iterable of (amount, asset, maker) for ONE fill. Returns the commission_raw list aggregated by (asset, maker)
    - no trade or order ids - and whether every amount was readable."""
    agg, complete = {}, True
    for amount, asset, maker in trades:
        a = _num(amount)
        if a is None or not isinstance(asset, str) or not asset: complete = False; continue
        k = (asset, maker if isinstance(maker, bool) else None)
        x = agg.setdefault(k, dict(asset=asset, maker=k[1], amount=0.0, n=0))
        x['amount'] += a; x['n'] += 1
    out = [dict(v, amount=round(v['amount'], 10) + 0.0) for _k, v in sorted(agg.items(), key=lambda kv: (kv[0][0], str(kv[0][1])))]
    return out, complete


def apply_fill(target, rec):
    """Fold one finished fill record (with commission_raw / commission_missing) into a lot or history row (in place)."""
    by = target.setdefault('fees_actual_by_asset', {})
    raw = rec.get('commission_raw') if isinstance(rec.get('commission_raw'), list) else []
    if rec.get('commission_missing') or not raw:
        target['cost_fills_missing'] = int(target.get('cost_fills_missing') or 0) + 1
    for x in raw:
        a = _num(x.get('amount'))
        if a is None or not isinstance(x.get('asset'), str): continue
        by[x['asset']] = round(by.get(x['asset'], 0.0) + a, 10) + 0.0
        if x.get('maker') is True: target['maker_fills'] = int(target.get('maker_fills') or 0) + int(x.get('n') or 1)
        elif x.get('maker') is False: target['taker_fills'] = int(target.get('taker_fills') or 0) + int(x.get('n') or 1)
    target['cost_fills_applied'] = int(target.get('cost_fills_applied') or 0) + 1
    if rec.get('cost_ref'): target.setdefault('cost_refs_done', []).append(rec['cost_ref'])
    return target


def ref_ms(ref):
    """The creation time (ms) encoded in a cost_ref ('c<ms>-<pid>-<n>'), else None."""
    try: return int(str(ref)[1:].split('-')[0])
    except (TypeError, ValueError): return None


def finalize(target, quote, booked_fills, now_ms=None, wait_ms=None):
    """Recompute fees_actual / fees_actual_partial (in place). booked_fills: how many executions the lot itself booked
    (its `fills` entries) - a fill booked outside the order paths (reconcile, restore) has no cost record, so the total
    can never claim to be complete then. A record still not applied wait_ms after its fill is 'fill_records_lost' (an
    enrichment cut by a restart / crash): the total stays partial for good, never pending forever."""
    by = target.get('fees_actual_by_asset') or {}
    refs = len(target.get('cost_refs') or [])
    missing = int(target.get('cost_fills_missing') or 0)
    why = []
    if missing: why.append('fill_commission_missing')
    done = set(target.get('cost_refs_done') or ())
    for r in (target.get('cost_refs') or ()):
        if r in done: continue
        t = ref_ms(r)
        lost = now_ms is not None and wait_ms is not None and t is not None and now_ms - t > wait_ms
        why.append('fill_records_lost' if lost else 'fill_records_pending')
    if booked_fills is not None and refs < booked_fills: why.append('fills_without_cost_record')
    if not quote or any(a != quote for a in by): why.append('non_quote_asset')
    if why:
        target['fees_actual'], target['fees_actual_partial'] = None, sorted(set(why))
    else:
        target['fees_actual'], target['fees_actual_partial'] = round(by.get(quote, 0.0), 10) + 0.0, False
    return target


def tran_key(tran_id):
    """Dedupe key for one income row (the exchange transaction id itself is never stored)."""
    return hashlib.sha256(str(tran_id).encode()).hexdigest()[:16]


def attribute_funding(income, side_qty):
    """Split one funding payment over the lots that held the symbol at its time. side_qty: [(key, side, qty)].
    Returns (status, {key: amount}). Opposite sides on one symbol share one netted position -> 'ambiguous', nothing
    attributed (never guessed); no holder -> 'unattributed'. Basis: each lot's quantity as recorded (qty_now)."""
    amt = _num(income)
    rows = [(k, s, q) for k, s, q in side_qty if (_num(q) or 0) > 0]
    if amt is None: return 'unreadable', {}
    if not rows: return 'unattributed', {}
    if len({s for _k, s, _q in rows}) > 1: return 'ambiguous', {}
    tot = sum(float(q) for _k, _s, q in rows)
    return 'attributed', {k: round(amt * float(q) / tot, 10) + 0.0 for k, _s, q in rows}


# every kind a lot's `fills` books as an increase: the opening fill plus every `why` the engine's _add_qty books
# (a maker entry completed by the market fallback books its remainder as 'entry_fallback'); anything else reduces.
OPENING_FILLS = ('entry', 'entry_maker', 'entry_market', 'entry_fallback', 'pyramid_add', 'safety_order')


def held_at(fills, t_ms):
    """Quantity a lot held at t_ms, replayed from its booked fills [[iso, kind, qty, px], ...] (opening kinds add, every
    other kind reduces). None when a fill time is unreadable (the caller falls back)."""
    q = 0.0
    for f in fills or ():
        try:
            ts, kind, qty = datetime.fromisoformat(f[0]).timestamp() * 1000, f[1], float(f[2])
        except (TypeError, ValueError, IndexError): return None
        if ts <= t_ms: q += qty if kind in OPENING_FILLS else -qty
    return max(q, 0.0)


def apply_funding(target, amount, asset):
    by = target.setdefault('funding_by_asset', {})
    by[asset] = round(by.get(asset, 0.0) + amount, 10) + 0.0
    target['funding_rows'] = int(target.get('funding_rows') or 0) + 1
    target['funding_actual'] = round(sum(by.values()), 10) + 0.0 if len(by) == 1 else None
    return target
