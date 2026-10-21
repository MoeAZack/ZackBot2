"""BOOK-LEDGER-01: observe-only virtual NAV per PAPER book.

Each of the five books starts a cohort at its own $2,000 (PAPER_BOOK_CAP) and is valued only from the lots and closed
trades stamped with that book: closed net PnL + the open lots' booked partials + mark-to-market of what is still open.
The books share one Binance margin account, so a book's NAV is an attribution, never a liquidation boundary.

Observe-only: nothing here sizes, enters, exits or freezes anything. WOULD_FREEZE is a flag for the evidence record.
Costs: `pnl` in history is net of the ESTIMATED fees (FEE_EST / maker rate). Actual commission and funding are reported
separately with their missingness and never replace the estimate (FEE-ASSET-01 fills them in).
"""
import hashlib, json

import paper_books as PB

LEDGER_VERSION = 1
BASIS = 'pnl net of estimated fees; actual fees / funding reported separately, not in nav'


def book_version():
    """Short hash of the strategy -> book map: a book means the same thing only while this is unchanged."""
    doc = [[k, n, sorted(s)] for k, n, s in PB.BOOKS]
    return hashlib.sha256(json.dumps(doc, separators=(',', ':')).encode()).hexdigest()[:16]


def membership(row, cohort, t_key):
    """'in' / 'legacy' (unstamped but inside the cohort window) / 'foreign' (another cohort, or outside this one).
    Only the first cohort (seq 0, opened on a build that predates stamping) can own unstamped rows; a cohort opened at a
    boundary stamps every book lot from its first second, so an unstamped row is never its own - and a row closed in the
    boundary second belongs to the cohort that ended (end is inclusive), never to both."""
    cid = row.get('cohort_id')
    if cid: return 'in' if cid == cohort['id'] else 'foreign'
    if int(cohort.get('seq') or 0) > 0: return 'foreign'
    t = str(row.get(t_key) or '')
    end = cohort.get('ended_utc')
    return 'legacy' if t >= cohort['started_utc'] and (not end or t <= end) else 'foreign'


def _f(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def _blank(k, name, nav_start):
    return dict(id=k, name=name, nav_start=nav_start, closed=0, open=0, realized_net=0.0, realized_gross=0.0, fees_est=0.0,
                open_booked_net=0.0, unrealized=0.0, marks_missing=0, fees_actual=0.0, fees_actual_missing=0,
                funding_actual=0.0, funding_missing=0, fill_px_unresolved=0, symbols=[])


def compute(cohort, history=(), lots=(), marks=None, nav_start=2000.0, prev=None, floor=0.0, t=None):
    """Pure. Returns the ledger document; `prev` (the last persisted document) only carries peaks and freeze flags."""
    marks = marks or {}
    nav_start = float(nav_start)
    books = {k: _blank(k, n, nav_start) for k, n, _s in PB.BOOKS}
    excl = dict(foreign=0, unbooked=0, unknown_book=0, legacy_unstamped=0)

    def target(row, t_key):
        m = membership(row, cohort, t_key)
        if m == 'foreign': excl['foreign'] += 1; return None
        k = row.get('book')
        if k is None: excl['unbooked'] += 1; return None
        if k not in books: excl['unknown_book'] += 1; return None
        if m == 'legacy': excl['legacy_unstamped'] += 1        # counted in its book, reported so it is never silent
        return books[k]

    for row in history or ():
        b = target(row, 'closed')
        if b is None: continue
        b['closed'] += 1
        adj = _f(row.get('pnl_fill_adj')) or 0.0                 # FILL-PNL-01: measured fill prices
        if row.get('fill_px_partial'): b['fill_px_unresolved'] += 1
        b['realized_net'] += (_f(row.get('pnl')) or 0.0) + adj
        b['realized_gross'] += (_f(row.get('pnl_gross')) or 0.0) + adj
        b['fees_est'] += _f(row.get('fees')) or 0.0
        fa, fu = _f(row.get('fees_actual')), _f(row.get('funding_actual'))
        if fa is None: b['fees_actual_missing'] += 1
        else: b['fees_actual'] += fa
        if fu is None: b['funding_missing'] += 1
        else: b['funding_actual'] += fu
    for lot in lots or ():
        b = target(lot, 'opened')
        if b is None: continue
        b['open'] += 1
        sym = lot.get('symbol')
        if sym not in b['symbols']: b['symbols'].append(sym)
        booked, fees = (_f(lot.get('realized')) or 0.0) + (_f(lot.get('pnl_fill_adj')) or 0.0), _f(lot.get('fees')) or 0.0
        if lot.get('fill_px_partial'): b['fill_px_unresolved'] += 1
        b['open_booked_net'] += booked - fees
        b['fees_est'] += fees
        mk, avg, qty = _f(marks.get(sym)), _f(lot.get('avg')), _f(lot.get('qty')) or 0.0
        if mk is None or avg is None: b['marks_missing'] += 1
        else: b['unrealized'] += (1 if lot.get('side') == 'LONG' else -1) * (mk - avg - (_f(lot.get('avg_fill_adj')) or 0.0)) * qty
    pb = (prev or {}).get('books') or {}
    if (prev or {}).get('cohort_id') != cohort['id']: pb = {}     # peaks / freezes never leak across cohorts
    for k, b in books.items():
        nav = nav_start + b['realized_net'] + b['open_booked_net'] + b['unrealized']
        old = pb.get(k) or {}
        peak = max(nav_start, nav, _f(old.get('peak')) or nav_start)
        b.update(nav=round(nav, 4), peak=round(peak, 4), roi=round(nav / nav_start - 1, 6) if nav_start else None,
                 dd=round(1 - nav / peak, 6) if peak > 0 else None, stale=b['marks_missing'] > 0)
        frozen = old.get('state') == 'WOULD_FREEZE'                # sticky inside a cohort: a recovery does not unfreeze
        if not frozen and nav <= nav_start * floor:
            b['state'], b['would_freeze_at'] = 'WOULD_FREEZE', t
        else:
            b['state'] = 'WOULD_FREEZE' if frozen else 'ACTIVE'
            if frozen: b['would_freeze_at'] = old.get('would_freeze_at')
        for x in ('realized_net', 'realized_gross', 'fees_est', 'open_booked_net', 'unrealized', 'fees_actual', 'funding_actual'):
            b[x] = round(b[x], 4)
        if b['fees_actual_missing'] and not b['fees_actual']: b['fees_actual'] = None
        if b['funding_missing'] and not b['funding_actual']: b['funding_actual'] = None
        b['symbols'].sort()
    return dict(version=LEDGER_VERSION, book_version=book_version(), cohort_id=cohort['id'], t=t, basis=BASIS,
                floor=floor, shared_account=True,
                note='books share Binance margin; NAV is attribution only, liquidation is account-wide',
                books=books, excluded=excl, overlap=overlap(books))


def overlap(books):
    """Symbols held by more than one book at once: each book keeps its own lots, the exchange nets one position."""
    seen = {}
    for k, b in books.items():
        for s in b['symbols']: seen.setdefault(s, []).append(k)
    return {s: ks for s, ks in sorted(seen.items()) if len(ks) > 1}


def parity(doc, cohort, history=(), lots=()):
    """Closed-trade parity with paper_books.summary over the same cohort rows: {} = consistent."""
    rows = [r for r in history or () if membership(r, cohort, 'closed') != 'foreign']
    ols = [l for l in lots or () if membership(l, cohort, 'opened') != 'foreign']
    ref = {x['id']: x for x in PB.summary(0, 0, ols, rows)}
    out = {}
    for k, b in doc['books'].items():
        r = ref[k]
        if b['closed'] != r['closed'] or b['open'] != r['open'] or abs(b['realized_net'] - r['net_pnl']) > 0.01:
            out[k] = dict(ledger=[b['closed'], b['open'], b['realized_net']], summary=[r['closed'], r['open'], r['net_pnl']])
    return out
