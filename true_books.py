"""TRUE-BOOK-01: five isolated $2,000 PAPER portfolios on one testnet account (PAPER/testnet only).

Pure. The engine supplies lots, closed history rows, marks and funding gaps; nothing here reads files or the exchange.
Each book's NAV is its own: start + realized gross + unrealized gross - fees + net funding, from the lots and rows of the
current cohort that carry that book. Sizing, the 4-lot cap, notional and stop-risk limits are all measured against that
NAV - never account equity / 5 and never another book's surplus. Money is Decimal end to end (quantized only for JSON).

Fees: a row/lot's actual commission (FEE-ASSET-01 `fees_actual`) when complete; otherwise its estimate, and the book is
INCOMPLETE with the reason (`nav_basis: estimate_fallback`) - never a silent zero. HALT is decided on the worse of the
actual-basis and estimate-basis NAV and is sticky for the cohort (the engine persists it).
"""
from decimal import Decimal, InvalidOperation
from datetime import datetime

import paper_books as PB

START = Decimal('2000')
MAX_LOTS = 4
STOP_RISK_MAX = Decimal('0.08')            # book open stop-risk cap as a fraction of book NAV (4 x 2 %) - Codex may rule
Q = Decimal('0.0001')
# risk / ownership unknown -> no new risk in that book (existing lots stay managed and closable). Unowned / manual
# activity contaminates the shared account: every book stops new risk until a clean cohort (Codex ruling 6240131523).
UNMEASURED = ('mark_missing', 'stop_unknown', 'working_stop_unknown', 'unowned_account_activity')


def D(x):
    """Decimal from a JSON number / string; None when unreadable or not finite."""
    if x is None or isinstance(x, bool): return None
    try: d = Decimal(str(x))
    except (InvalidOperation, ValueError): return None
    return d if d.is_finite() else None


def q(d):
    return None if d is None else float(d.quantize(Q))


def book_of(strategy):
    """The book of a strategy key, or None when unmapped (TRUE-BOOK refuses it - never parked in Discovery)."""
    for key, _n, strategies in PB.BOOKS:
        if strategy in strategies: return key
    return None


def stop_risk(lot):
    """Open loss to the CURRENT stop: max(0, (avg - stop) * side * qty). A breakeven / profit-locked stop risks 0."""
    avg, stop, qty = D(lot.get('avg')), D(lot.get('stop')), D(lot.get('qty'))
    if avg is None or stop is None or qty is None: return None
    sd = 1 if lot.get('side') == 'LONG' else -1
    return max(Decimal(0), (avg - stop) * sd * qty)


def _t(iso):
    try: return datetime.fromisoformat(str(iso))
    except ValueError: return None


def compute(cohort_id, history=(), lots=(), marks=None, working=(), halted=None, start=START, peaks=None):
    """Pure. {book: doc} for the five books. Only rows / lots stamped with cohort_id count (an older cohort's lots and their
    later exits, fees and funding stay with that cohort). halted: the persisted sticky halts {book: {...}}.
    working: [(book, notional, stop_risk)] for entries placed but not yet a lot (resting maker, unconfirmed): they hold a
    lot slot and RESERVE their notional and worst loss to stop before any fill (Codex ruling 6238997253 #1).
    peaks: persisted mark-to-market NAV peaks {book: number} (sampled by the engine), so DD sees unrealized highs.
    Anything on the shared account that no book owns (a manual / unknown-book lot or row) makes EVERY book INCOMPLETE
    (unowned_account_activity); an amount that cannot be read is INCOMPLETE too - never zero-filled."""
    marks, halted, peaks = marks or {}, halted or {}, peaks or {}
    out, unowned = {}, dict(rows=0, lots=0)
    for key, name, strategies in PB.BOOKS:
        b = dict(id=key, name=name, start=start, gross_real=Decimal(0), unreal=Decimal(0), fees_act=Decimal(0),
                 fees_est=Decimal(0), funding=Decimal(0), incomplete=[], lots=0, closed=0, notional=Decimal(0),
                 stop_risk=Decimal(0), working=0, strategies={}, series=[])
        out[key] = b
    seen = set()
    for r in history or ():
        if r.get('id') is not None:
            if r['id'] in seen: continue
            seen.add(r['id'])
        b = out.get(r.get('book'))
        if not cohort_id or r.get('cohort_id') not in (cohort_id, None): continue
        if b is None: unowned['rows'] += 1; continue                       # the engine passes only this cohort's window
        if r.get('cohort_id') != cohort_id: continue
        b['closed'] += 1
        g, fe = D(r.get('pnl_gross')), D(r.get('fees'))
        if g is None: b['incomplete'].append(dict(reason='pnl_unreadable', id=r.get('id'))); g = Decimal(0)
        if fe is None: b['incomplete'].append(dict(reason='fees_unreadable', id=r.get('id'))); fe = Decimal(0)
        _funding_gap(b, r)
        fa, fu = D(r.get('fees_actual')), D(r.get('funding_actual')) or Decimal(0)
        b['gross_real'] += g; b['fees_est'] += fe; b['funding'] += fu
        if fa is None:
            b['fees_act'] += fe
            b['incomplete'].append(dict(reason='fees_actual_missing', id=r.get('id'), why=r.get('fees_actual_partial')))
        else: b['fees_act'] += fa
        for why in r.get('funding_partial') or ():
            b['incomplete'].append(dict(reason=why, id=r.get('id'), n=r.get('funding_unattributed')))
        s = b['strategies'].setdefault(r.get('strategy_key') or '?', dict(closed=0, net=Decimal(0), wins=0, gross_win=Decimal(0),
                                                                          gross_loss=Decimal(0), fees=Decimal(0), funding=Decimal(0),
                                                                          r_sum=Decimal(0), hours=Decimal(0), exits={}))
        net = g - (fa if fa is not None else fe) + fu
        s['closed'] += 1; s['net'] += net; s['fees'] += fa if fa is not None else fe; s['funding'] += fu
        s['wins'] += net > 0
        if net > 0: s['gross_win'] += net
        else: s['gross_loss'] -= net
        s['r_sum'] += D(r.get('r')) or Decimal(0); s['hours'] += D(r.get('hours')) or Decimal(0)
        s['exits'][r.get('exit_reason') or '?'] = s['exits'].get(r.get('exit_reason') or '?', 0) + 1
        b['series'].append((str(r.get('closed') or ''), net))
    for lot in lots or ():
        b = out.get(lot.get('book'))
        if b is None: unowned['lots'] += 1; continue                       # open on the shared account, owned by no book
        sr = stop_risk(lot)
        b['lots'] += 1                                          # capacity counts every lot of the book (conservative)
        b['notional'] += (D(lot.get('qty')) or Decimal(0)) * (D(lot.get('avg')) or Decimal(0))
        if sr is None: b['incomplete'].append(dict(reason='stop_unknown', symbol=lot.get('symbol')))
        else: b['stop_risk'] += sr
        if lot.get('cohort_id') != cohort_id: continue          # value only: an older cohort's lot is not this NAV's
        g, fe = D(lot.get('realized', 0)), D(lot.get('fees', 0))
        if g is None: b['incomplete'].append(dict(reason='pnl_unreadable', id=lot.get('symbol'))); g = Decimal(0)
        if fe is None: b['incomplete'].append(dict(reason='fees_unreadable', id=lot.get('symbol'))); fe = Decimal(0)
        _funding_gap(b, lot)
        fa, fu = D(lot.get('fees_actual')), D(lot.get('funding_actual')) or Decimal(0)
        b['gross_real'] += g; b['fees_est'] += fe; b['funding'] += fu
        if fa is None:
            b['fees_act'] += fe
            b['incomplete'].append(dict(reason='fees_actual_missing', id=lot.get('symbol'), why=lot.get('fees_actual_partial')))
        else: b['fees_act'] += fa
        for why in lot.get('funding_partial') or ():
            b['incomplete'].append(dict(reason=why, id=lot.get('symbol'), n=lot.get('funding_unattributed')))
        mk, avg, qty = D(marks.get(lot.get('symbol'))), D(lot.get('avg')), D(lot.get('qty')) or Decimal(0)
        if mk is None or mk <= 0 or avg is None: b['incomplete'].append(dict(reason='mark_missing', symbol=lot.get('symbol')))
        else: b['unreal'] += (1 if lot.get('side') == 'LONG' else -1) * (mk - avg) * qty
    for k, nn, rr in working or ():
        b = out.get(k)
        if b is None: continue
        b['working'] += 1
        b['notional'] += D(nn) or Decimal(0)
        r = D(rr)
        if r is None: b['incomplete'].append(dict(reason='working_stop_unknown'))
        else: b['stop_risk'] += max(Decimal(0), r)
    if unowned['rows'] or unowned['lots']:
        for b in out.values(): b['incomplete'].append(dict(reason='unowned_account_activity', **unowned))
    for key, b in out.items():
        base = start + b['gross_real'] + b['unreal'] + b['funding']
        nav_a, nav_e = base - b['fees_act'], base - b['fees_est']
        nav = nav_a
        worse = min(nav_a, nav_e)
        # peak from the ordered close series (realized only) + now: re-derived each time, so a restated fee re-derives it
        pk = D(peaks.get(key)) if key in peaks else None
        if key in peaks and (pk is None or pk <= 0):              # a corrupted persisted peak would under-report DD
            b['incomplete'].append(dict(reason='peak_unreadable', value=str(peaks.get(key))[:40])); pk = None
        run, peak = start, max(start, nav, pk or start)
        for _t0, net in sorted(b['series'], key=lambda x: x[0]):
            run += net; peak = max(peak, run)
        h = halted.get(key)
        state = 'HALTED' if h or worse <= 0 else 'ACTIVE'
        fallback = any(x['reason'] == 'fees_actual_missing' for x in b['incomplete'])
        b.update(nav=nav, nav_estimate=nav_e, nav_worse=worse, peak=peak, dd=(1 - nav / peak) if peak > 0 else None,
                 roi=nav / start - 1, state=state, halt=h or (dict(basis='actual' if nav_a <= nav_e else 'estimate',
                                                                   nav=q(worse)) if worse <= 0 else None),
                 nav_basis='estimate_fallback' if fallback else 'actual', complete=not b['incomplete'])
    return out


def _funding_gap(b, x):
    """funding_actual None while funding was booked (several assets): the amount is unknown, never 0."""
    if x.get('funding_actual') is None and (x.get('funding_by_asset') or x.get('funding_rows')):
        b['incomplete'].append(dict(reason='funding_multi_asset', id=x.get('id') or x.get('symbol'),
                                    assets=sorted(x.get('funding_by_asset') or ())))


def admit(b, new_notional=None, new_stop_risk=None, add=False, max_leverage=None, max_lots=MAX_LOTS,
          stop_risk_max=STOP_RISK_MAX, working=None):
    """The book's own gate for one new lot (add=False) or an add to an existing lot (add=True). None or a reason."""
    k = b['id']
    if b['state'] == 'HALTED': return f'book {k} halted (NAV {q(b["nav_worse"])} <= 0) - no new risk in this book'
    nav = b['nav_worse']
    if nav <= 0: return f'book {k} has no NAV left'
    blind = sorted({x['reason'] for x in b['incomplete'] if x['reason'] in UNMEASURED})
    if blind: return f'book {k} cannot measure its risk ({", ".join(blind)}) - no new risk until it can'
    n = b['lots'] + (b['working'] if working is None else working)
    if not add and n >= max_lots: return f'book {k} full ({n}/{max_lots} lots)'
    if new_notional is not None and max_leverage is not None:
        nn = D(new_notional) or Decimal(0)
        if b['notional'] + nn > D(max_leverage) * nav:
            return f'book {k} leverage cap: {q(b["notional"] + nn)} > {max_leverage}x NAV {q(nav)}'
    if new_stop_risk is not None:
        nr = D(new_stop_risk) or Decimal(0)
        if b['stop_risk'] + nr > stop_risk_max * nav:
            return f'book {k} stop-risk cap: {q(b["stop_risk"] + nr)} > {q(stop_risk_max * 100)}% of NAV {q(nav)}'
    return None


def public(books):
    """JSON view: money quantized, per-strategy stats derived."""
    out = []
    for b in books.values():
        st = {}
        for s, x in b['strategies'].items():
            n = x['closed']
            st[s] = dict(closed=n, net=q(x['net']), win_rate=round(x['wins'] / n, 4) if n else None,
                         expectancy=q(x['net'] / n) if n else None, avg_r=q(x['r_sum'] / n) if n else None,
                         pf=q(x['gross_win'] / x['gross_loss']) if x['gross_loss'] > 0 else None,
                         fee_drag=q(x['fees']), funding=q(x['funding']), avg_hours=q(x['hours'] / n) if n else None,
                         roi=q(x['net'] / b['start']), exits=x['exits'])
        out.append(dict(id=b['id'], name=b['name'], start=q(b['start']), nav=q(b['nav']), nav_estimate=q(b['nav_estimate']),
                        peak=q(b['peak']), dd=q(b['dd']) if b['dd'] is not None else None, roi=q(b['roi']), state=b['state'],
                        halt=b['halt'], nav_basis=b['nav_basis'], complete=b['complete'], incomplete=b['incomplete'][:50],
                        incomplete_n=len(b['incomplete']), lots=b['lots'], working=b['working'], closed=b['closed'], notional=q(b['notional']),
                        stop_risk=q(b['stop_risk']), fees=q(b['fees_act']), fees_estimate=q(b['fees_est']),
                        funding=q(b['funding']), strategies=st))
    return out
