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
               'cost_fills_applied', 'cost_fills_missing', 'cost_refs_done', 'funding_unattributed', 'funding_partial', 'funding_actual', 'funding_by_asset', 'funding_rows',
               'funding_keys', 'exit_px_unconfirmed', 'cost_refs_recovered', 'cost_order_keys')


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
    if rec.get('cost_ref') and rec['cost_ref'] in (target.get('cost_refs_done') or ()): return target   # replay-safe
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


def match_fill(trades, symbol, buy, qty, quote, lo_ms, hi_ms, limit=None):
    """FEE-RECOVER-01 (pure): the commission of ONE booked fill whose own reads timed out, proven from a time-window
    userTrades read. trades: the account's executions of `symbol` over a span padded around [lo_ms, hi_ms] (so an order
    reaching outside the window is seen whole). A candidate is one exchange order on the booked side whose executions
    (deduplicated by trade id, split executions summed) total exactly the booked quantity and all lie inside the window.
    Returns ('ok', commission_raw, order_key) only for exactly one candidate whose every amount is readable and charged
    in `quote`; otherwise (status, None, None) - never a guess: unreadable / too_many_rows / bad_evidence / no_match /
    ambiguous / amount_unreadable / non_quote_asset. order_key (order_key()) identifies the matched exchange order without
    its id, so one order can never be attributed to two fills (the caller refuses a key another fill consumed)."""
    q0 = _num(qty)
    if not q0 or q0 <= 0 or buy not in (True, False) or not quote or not symbol: return 'bad_evidence', None, None
    if not isinstance(trades, list): return 'unreadable', None, None
    if limit and len(trades) >= limit: return 'too_many_rows', None, None          # the span may hold more than was returned
    groups = {}
    for t in trades:
        if not isinstance(t, dict): return 'unreadable', None, None
        if t.get('symbol', symbol) != symbol or t.get('buyer') is not buy: continue
        if t.get('orderId') is None or t.get('id') is None: return 'unreadable', None, None
        groups.setdefault(str(t['orderId']), {})[str(t['id'])] = t
    cands = []
    for rows in groups.values():
        rows = list(rows.values())
        qs, tms = [_num(t.get('qty')) for t in rows], [_num(t.get('time')) for t in rows]
        if any(x is None or x <= 0 for x in qs) or any(x is None for x in tms): return 'unreadable', None, None
        if abs(sum(qs) - q0) > 1e-9 * max(1.0, q0): continue
        inside = [lo_ms <= x <= hi_ms for x in tms]
        if any(inside) and not all(inside): return 'ambiguous', None, None   # straddles the window: never skip it (H1)
        if not all(inside): continue
        cands.append(rows)
    if not cands: return 'no_match', None, None
    if len(cands) > 1: return 'ambiguous', None, None
    raw, complete = raw_from_trades((t.get('commission'), t.get('commissionAsset'), t.get('maker')) for t in cands[0])
    if not complete or not raw: return 'amount_unreadable', None, None
    if any(x['asset'] != quote for x in raw): return 'non_quote_asset', None, None
    return 'ok', raw, order_key(symbol, cands[0][0]['orderId'])


def order_key(symbol, order_id):
    """FEE-RECOVER-01: a stable, privacy-safe key for one exchange order (the order id itself is never stored)."""
    return hashlib.sha256(f'{symbol}|{order_id}'.encode()).hexdigest()[:16]


def order_owner(target, key):
    """The ref of `target` that consumed exchange order `key` in a recovery, else None."""
    return next((r for r, k in (target.get('cost_order_keys') or ()) if k == key), None)


def recover_fill(target, ref, raw, key=None):
    """FEE-RECOVER-01: fold a recovered commission into the lot / row that applied `ref` as missing (in place). True when
    applied; False (nothing changed) when ref is not this target's, was never applied, is already recovered, or no
    missing fill remains. `fees` (the estimate) and every trade fact stay as they are."""
    if ref in (target.get('cost_refs_recovered') or ()): return False
    if ref not in (target.get('cost_refs') or ()) or ref not in (target.get('cost_refs_done') or ()): return False
    if int(target.get('cost_fills_missing') or 0) < 1: return False
    by = target.setdefault('fees_actual_by_asset', {})
    for x in raw:
        a = _num(x.get('amount'))
        if a is None or not isinstance(x.get('asset'), str): continue
        by[x['asset']] = round(by.get(x['asset'], 0.0) + a, 10) + 0.0
        if x.get('maker') is True: target['maker_fills'] = int(target.get('maker_fills') or 0) + int(x.get('n') or 1)
        elif x.get('maker') is False: target['taker_fills'] = int(target.get('taker_fills') or 0) + int(x.get('n') or 1)
    target['cost_fills_missing'] = int(target['cost_fills_missing']) - 1
    target.setdefault('cost_refs_recovered', []).append(ref)
    if key: target.setdefault('cost_order_keys', []).append([ref, key])    # consumed with the fee, in one revision
    return True


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
    if not refs and booked_fills is None: why.append('no_cost_records')
    if not quote or any(a != quote for a in by): why.append('non_quote_asset')
    if target.get('exit_px_unconfirmed'): why.append('exit_px_unconfirmed')   # an exit price that is not exchange truth
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
OPENING_FILLS = ('entry', 'entry_maker', 'entry_market', 'entry_fallback', 'pyramid_add', 'safety_order', 'grid_buy', 'grid_sell')


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


def mark_funding_gap(target, reason):
    """A funding payment that could not be attributed to this lot / row: its funding_actual is no longer complete."""
    target['funding_unattributed'] = int(target.get('funding_unattributed') or 0) + 1
    target['funding_partial'] = sorted(set(target.get('funding_partial') or []) | {reason})
    return target


def apply_funding(target, amount, asset):
    by = target.setdefault('funding_by_asset', {})
    by[asset] = round(by.get(asset, 0.0) + amount, 10) + 0.0
    target['funding_rows'] = int(target.get('funding_rows') or 0) + 1
    target['funding_actual'] = round(sum(by.values()), 10) + 0.0 if len(by) == 1 else None
    return target
