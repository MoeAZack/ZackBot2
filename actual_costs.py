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
               'funding_keys', 'exit_px_unconfirmed', 'cost_refs_recovered', 'cost_order_keys',
               'fill_refs', 'px_actual', 'px_missing', 'pnl_fill_adj', 'avg_fill_adj', 'fill_px_partial',   # FILL-PNL-01
               'px_unmeasured', 'px_unlinked_ok', 'px_ack')


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


# ---- FILL-PNL-01 (#176): realized P&L from the MEASURED fill prices --------------------------------------------------
# A lot books each fill at the price known when it was sent (the order answer's average, else the mark / plan price). The
# exchange price arrives later in the fill's cost record (enriched `actual`). The booked numbers (`realized`, `avg`,
# `pnl_gross`) are never rewritten; `pnl_fill_adj` / `avg_fill_adj` are the corrections from replaying the lot's own fills
# with every measured price (average-cost, the same arithmetic as the booking), recomputed from scratch each time - so a
# record applied twice, or after the close, never double counts. Until every linked fill has its exchange price the
# target says why (`fill_px_partial`), and that is INCOMPLETE, never planned-price P&L presented as actual.
def link_px(target, ref):
    """The fill just appended to target['fills'] is the execution behind cost record `ref` (no exchange id)."""
    if ref and target.get('fills'):
        target.setdefault('fill_refs', []).append([len(target['fills']) - 1, ref])
    return target


def _idx(v):
    """The fill indexes a per-fill marker list holds; anything else (a scalar count, junk) covers no fill."""
    return {i for i in v if isinstance(i, int) and not isinstance(i, bool)} if isinstance(v, (list, tuple)) else set()


def _mark(target, key):
    """Mark the fill just appended to target['fills'] by its index - once: a repeated mark of the same fill is a no-op,
    so a duplicate call can never cover a different, unaccounted fill (Codex 6330154803)."""
    fills = target.get('fills') or []
    if fills: target[key] = sorted(_idx(target.get(key)) | {len(fills) - 1})
    return target


def px_unmeasured(target):
    """A fill booked WITHOUT an exchange price and without a record that could bring one (a lost answer settled from the
    position, an adoption at the mark, a resync, a stop whose fill was not read): its P&L stays INCOMPLETE for good."""
    return _mark(target, 'px_unmeasured')


def px_unlinked_ok(target):
    """A fill booked at an exchange-reported price that has no cost record (settled from its order record): measured,
    but outside fill_refs - marked so the coverage check accounts for it."""
    return _mark(target, 'px_unlinked_ok')


def apply_px(target, rec):
    """Record the exchange price of one finished fill record, once per ref. A record without one is `px_missing`.
    Returns True when the target changed."""
    ref = rec.get('cost_ref')
    if not ref or ref not in {r for _, r in (target.get('fill_refs') or ())}: return False
    pa = target.setdefault('px_actual', {})
    if ref in pa or ref in (target.get('px_missing') or ()): return False
    px = _num(rec.get('actual'))
    if px is not None and px > 0: pa[ref] = px
    else: target.setdefault('px_missing', []).append(ref)
    return True


def _replay(fills, sd, entry_kinds, over):
    """(realized gross, average entry) of the fills booked in order, average-cost; `over` = {fill index: price}.
    (None, None) when a fill is unreadable."""
    avg, q, real = None, 0.0, 0.0
    for i, f in enumerate(fills or ()):
        if not isinstance(f, (list, tuple)) or len(f) < 4: return None, None
        n, px = _num(f[2]), _num(over.get(i, f[3]))
        if n is None or px is None: return None, None
        if f[1] in entry_kinds:
            avg = px if avg is None or q <= 0 else (avg * q + px * n) / (q + n)
            q += n
        elif avg is not None:
            real += sd * (px - avg) * n
            q -= n
    return real, avg


def finalize_px(target, sd, entry_kinds, now_ms=None, wait_ms=None):
    """Recompute pnl_fill_adj / avg_fill_adj / fill_px_partial (in place) from the fills and the measured prices so far.
    A target with no linked fill (booked before FILL-PNL-01, or never sent) is left as it is."""
    if target.get('px_ack'): return target       # FILL-PNL-02: an operator-acknowledged close is final for its fill prices
    refs = [x for x in (target.get('fill_refs') or ()) if isinstance(x, (list, tuple)) and len(x) == 2]
    if not refs and not target.get('px_unmeasured') and not target.get('px_unlinked_ok'): return target
    pa, miss = target.get('px_actual') or {}, set(target.get('px_missing') or ())
    why = {'fill_px_unmeasured'} if target.get('px_unmeasured') else set()
    fills = target.get('fills') or []
    # Cowork H1: every fill INDEX of a target tracked since FILL-PNL-01 is linked, flagged unmeasured or marked measured
    # without a record - a booked fill none of them covers is never silently assumed exchange-priced (a set: duplicates
    # never cover another fill, and a scalar count from any older state covers nothing)
    covered = {i for i, _ in refs if isinstance(i, int)} | _idx(target.get('px_unmeasured')) | _idx(target.get('px_unlinked_ok'))
    if any(i not in covered for i in range(len(fills))): why.add('fill_ref_missing')
    for i, r in refs:
        if r in pa: continue
        if r in miss: why.add('fill_px_missing'); continue
        t = ref_ms(r)
        lost = now_ms is not None and wait_ms is not None and t is not None and now_ms - t > wait_ms
        why.add('fill_px_lost' if lost else 'fill_px_pending')
    if any(not isinstance(i, int) or not 0 <= i < len(fills) for i, _ in refs): why.add('fill_ref_unmatched')
    booked = _replay(fills, sd, entry_kinds, {})
    meas = _replay(fills, sd, entry_kinds, {i: pa[r] for i, r in refs if r in pa and isinstance(i, int)})
    if booked[0] is None or meas[0] is None:
        why.add('fills_unreadable'); target['pnl_fill_adj'] = target['avg_fill_adj'] = None
    else:
        target['pnl_fill_adj'] = round(meas[0] - booked[0], 10) + 0.0
        target['avg_fill_adj'] = (round(meas[1] - booked[1], 12) + 0.0) if None not in (meas[1], booked[1]) else 0.0
    target['fill_px_partial'] = sorted(why) or False
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


# ---------------------------------------------------------------- FILL-PNL-02 (#178): measured P&L for every consumer
# Codex ruling 6329976227: sizing, guards, R history, ML labels, notifications and books read the MEASURED result; a row
# tracked since FILL-PNL-01 whose result is not measured yet is never presented or compounded as actual. Rows booked
# before FILL-PNL-01 (no fill links, no coverage marks) keep their booked value - nothing exists to measure them with.

def tracked(t):
    """True for a target whose fill prices are measured since FILL-PNL-01 (a legacy row has none of these keys)."""
    return any(k in t for k in ('fill_refs', 'px_unmeasured', 'px_unlinked_ok'))


def measured(row):
    """One closed row's actual result -> {gross, fees, funding, net, complete, why}: the true_books per-row formula
    net = pnl_gross + pnl_fill_adj - fees_actual + funding_actual, complete only when true_books would count it."""
    why = set()
    p = row.get('fill_px_partial')
    if isinstance(p, (list, tuple)): why.update(str(x) for x in p)
    elif p: why.add('fill_px_partial')
    g, adj = _num(row.get('pnl_gross')), _num(row.get('pnl_fill_adj')) if 'pnl_fill_adj' in row else 0.0
    if tracked(row) and 'pnl_fill_adj' not in row: why.add('pnl_fill_adj_missing')    # Cowork N1: never planned prices
    if g is None: why.add('pnl_unreadable'); g = 0.0
    if adj is None: why.add('pnl_fill_adj_unreadable'); adj = 0.0
    fa = _num(row.get('fees_actual'))
    if fa is None:
        why.add('fees_actual_missing'); fa = _num(row.get('fees')) or 0.0
    why.update(str(x) for x in row.get('funding_partial') or ())
    fu = _num(row.get('funding_actual'))
    if fu is None:
        if row.get('funding_by_asset') or row.get('funding_rows'): why.add('funding_unpriced')
        fu = 0.0
    if row.get('_conflict'): why.add('row_conflict')
    gross = g + adj
    return dict(gross=gross, fees=fa, funding=fu, net=gross - fa + fu, complete=not why, why=sorted(why))


def row_view(row):
    """What a consumer may show / count for one closed row -> {pnl, r, status, why}. status 'measured' = actual;
    'pending' = tracked but not measured yet (pnl None - never the booked figure); 'booked' = a legacy row's own value."""
    if not tracked(row):
        return dict(pnl=_num(row.get('pnl')), r=_num(row.get('r')), status='booked', why=[])
    m = measured(row)
    risk = _num(row.get('risk_usd'))
    if not m['complete']: return dict(pnl=None, r=None, status='pending', why=m['why'])
    st = 'acknowledged' if row.get('px_ack') else 'measured'      # the acknowledged net is written into the row itself
    return dict(pnl=round(m['net'], 4), r=round(m['net'] / risk, 2) if risk else None, status=st, why=[])


def measured_avg(lot):
    """An open lot's entry basis at the measured fill prices. None = unknown (Codex 6332163574 P1-2): a tracked lot whose
    fill prices are not all measured, or whose correction is missing / unreadable - never the booked avg as actual.
    A legacy lot (no fill links) keeps its booked avg."""
    a = _num(lot.get('avg'))
    if a is None or not tracked(lot): return a
    adj = _num(lot.get('avg_fill_adj'))
    if lot.get('fill_px_partial') or adj is None: return None
    return a + adj


def capital_hold(rows, checkpoint):
    """Ruling 1: realized P&L for COMPOUND / guard capital. While any tracked row is not measured, capital holds at the
    last fully reconciled checkpoint (None = base: 0.0) and says why; once every row is measured the checkpoint is revised
    once (idempotent - the same rows give the same checkpoint). Legacy rows count at their booked value.
    -> {realized, checkpoint, hold, realized_unmeasured_n, why}."""
    total, why, n = 0.0, set(), 0
    for r in rows:
        v = row_view(r)
        if v['status'] == 'pending': n += 1; why.update(v['why']); continue
        total += v['pnl'] or 0.0
    if n:
        held = _num((checkpoint or {}).get('realized'))
        return dict(realized=held if held is not None else 0.0, checkpoint=checkpoint, hold='realized_unmeasured',
                    realized_unmeasured_n=n, why=sorted(why))
    ck = dict(realized=round(total, 8), n=len(rows))
    return dict(realized=ck['realized'], checkpoint=ck, hold=None, realized_unmeasured_n=0, why=[])


# Fill-price gaps that can never close by themselves - the only ones an operator acknowledgement may settle. A row still
# pending its price (fill_px_pending) or missing fees / funding is refused: those resolve from records, not by hand.
ACK_REASONS = frozenset({'fill_px_lost', 'fill_px_missing', 'fill_px_unmeasured', 'fill_ref_missing', 'fill_ref_unmatched',
                         'fills_unreadable', 'pnl_fill_adj_unreadable', 'pnl_fill_adj_missing', 'fill_px_partial'})


def acknowledge(row, net, note, t):
    """Codex 6332263574 P1-1: settle a permanently unmeasurable close at the operator's stated actual net, IN the row (one
    truth for every consumer): pnl_fill_adj is set so that the row's net equals `net` exactly, fill_px_partial clears,
    and px_ack keeps the evidence (net, note, time, the unmeasured reasons, the correction it replaced). The booked
    figure is never used. -> (ok, message); the row is untouched when refused."""
    if row.get('px_ack'): return False, 'already acknowledged'
    if not tracked(row): return False, 'a legacy close has no measurement to settle'
    try: net = float(net)
    except (TypeError, ValueError): return False, 'net must be a number'
    if net != net or net in (float('inf'), float('-inf')): return False, 'net must be a finite number'
    note = str(note or '').strip()
    if not note: return False, 'an evidence note is required'
    m = measured(row)
    if m['complete']: return False, 'this close is measured - nothing to acknowledge'
    other = set(m['why']) - ACK_REASONS
    if other: return False, f"not an unmeasurable fill price ({', '.join(sorted(other))}) - it resolves from its records"
    g = _num(row.get('pnl_gross')); fa = _num(row.get('fees_actual')); fu = _num(row.get('funding_actual')) or 0.0
    row['px_ack'] = dict(net=round(net, 8), note=note[:200], t=t, why=m['why'], replaced_adj=row.get('pnl_fill_adj'))
    row['pnl_fill_adj'] = round(net - (g - fa + fu), 10) + 0.0
    row['fill_px_partial'] = False
    return True, f'acknowledged at net {net:+.4f}'
