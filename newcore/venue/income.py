"""Income history (/fapi/v1/income) pagination and the funding / fee / realized-pnl accounting read.

income_history() walks [start_ms, end_ms] in bounded time windows (default 7 days, the span Binance documents as the
default lookback) and pages inside a window by time: when a page is full, the next request starts at the last row's
time (inclusive) and rows already seen are dropped by (tranId, incomeType, asset). It returns ONE ReadOutcome:
- OK with every row in the range, sorted by (time, tranId), or
- the first non-OK page outcome (REJECTED / UNKNOWN) with value=None, or UNKNOWN with a pagination reason.
A partial history is never returned as if it were complete: funding and fees are money, and a gap would silently
under-count them.

summarize_income() and commission_mismatches() are pure: the ledger/outcome projection reads them (slice plan O5).
"""
from dataclasses import dataclass
from decimal import Decimal

from .outcomes import ReadKind, ReadOutcome

DAY_MS = 24 * 3600 * 1000
DEFAULT_WINDOW_MS = 7 * DAY_MS
FUNDING_FEE, COMMISSION, REALIZED_PNL = 'FUNDING_FEE', 'COMMISSION', 'REALIZED_PNL'


def _unknown(reason, rate):
    return ReadOutcome(ReadKind.UNKNOWN, 'income_history', unknown_reason=reason, rate=rate)


def income_history(transport, *, start_ms, end_ms, symbol=None, income_type=None, page_limit=1000,
                   window_ms=DEFAULT_WINDOW_MS, max_requests=500):
    for v, what in ((start_ms, 'start_ms'), (end_ms, 'end_ms')):
        if type(v) is not int or v <= 0:
            raise ValueError(f'{what} must be a positive int of milliseconds')
    if end_ms < start_ms:
        raise ValueError('end_ms before start_ms')
    if type(page_limit) is not int or not 1 <= page_limit <= 1000:
        raise ValueError('page_limit must be an int in 1..1000')
    if type(window_ms) is not int or not 1 <= window_ms <= DEFAULT_WINDOW_MS:
        raise ValueError('window_ms must be an int in 1..7 days')
    if type(max_requests) is not int or max_requests < 1:
        raise ValueError('max_requests must be a positive int')

    rows, seen, requests, rate = [], set(), 0, None
    cursor = start_ms
    while cursor <= end_ms:
        win_end = min(end_ms, cursor + window_ms - 1)
        if requests >= max_requests:
            return _unknown('too_many_pages', rate)
        out = transport.income(symbol=symbol, income_type=income_type, start_ms=cursor, end_ms=win_end,
                               limit=page_limit)
        requests += 1
        rate = out.rate
        if out.kind is not ReadKind.OK:
            return ReadOutcome(out.kind, 'income_history', error=out.error, unknown_reason=out.unknown_reason,
                               http_status=out.http_status, rate=out.rate)
        page = out.value
        if any(r.time_ms < cursor or r.time_ms > win_end for r in page):
            return _unknown('row_outside_window', rate)
        if any(b.time_ms < a.time_ms for a, b in zip(page, page[1:])):
            return _unknown('page_not_ascending', rate)
        if income_type is not None and any(r.income_type != income_type for r in page):
            return _unknown('unrequested_income_type', rate)
        if symbol is not None and any(r.symbol not in (symbol, None) for r in page):
            return _unknown('unrequested_symbol', rate)
        added = 0
        for r in page:
            if r.key not in seen:
                seen.add(r.key)
                rows.append(r)
                added += 1
        if len(page) < page_limit:
            cursor = win_end + 1                       # window exhausted
            continue
        last = page[-1].time_ms
        if added == 0 or last == cursor:
            # A full page that adds nothing, or a full page entirely inside one millisecond: restarting at `last`
            # would return the same page forever. The rest of the window cannot be reached by time paging.
            return _unknown('pagination_stuck', rate)
        cursor = last                                  # inclusive restart; duplicates are dropped by key
    rows.sort(key=lambda r: (r.time_ms, r.tran_id))
    return ReadOutcome(ReadKind.OK, 'income_history', value=tuple(rows), rate=rate)


@dataclass(frozen=True)
class IncomeSummary:
    """Signed sums, keyed by (income_type, symbol or None, asset). Negative = paid by the account."""
    totals: dict
    rows: int

    def total(self, income_type, asset='USDT', symbol=None):
        """Sum for one type and asset; symbol=None sums every symbol (and account-level rows)."""
        return sum((v for (t, s, a), v in self.totals.items()
                    if t == income_type and a == asset and (symbol is None or s == symbol)), Decimal(0))

    def funding(self, asset='USDT', symbol=None):
        return self.total(FUNDING_FEE, asset, symbol)

    def commission(self, asset='USDT', symbol=None):
        return self.total(COMMISSION, asset, symbol)

    def realized_pnl(self, asset='USDT', symbol=None):
        return self.total(REALIZED_PNL, asset, symbol)

    def other_types(self):
        return sorted({t for (t, _, _) in self.totals if t not in (FUNDING_FEE, COMMISSION, REALIZED_PNL)})


def summarize_income(rows):
    totals = {}
    for r in rows:
        k = (r.income_type, r.symbol, r.asset)
        totals[k] = totals.get(k, Decimal(0)) + r.income
    return IncomeSummary(totals, len(rows))


def commission_mismatches(income_rows, fills):
    """Cross-check the fee truth: every COMMISSION income row with a tradeId must equal -commission of the userTrades
    fill with that id (same asset), and every fill must have its COMMISSION row. Returns a list of
    (trade_id, reason, income_value, fill_value); empty = consistent. Zero-commission fills need no income row."""
    by_trade = {}
    out = []
    for r in income_rows:
        if r.income_type != COMMISSION:
            continue
        if r.trade_id is None:
            out.append((None, 'commission_without_trade_id', r.income, None))
            continue
        k = (r.trade_id, r.asset)
        by_trade[k] = by_trade.get(k, Decimal(0)) + r.income
    fills_by = {}
    for f in fills:
        k = (str(f.trade_id), f.commission_asset)
        fills_by[k] = fills_by.get(k, Decimal(0)) + f.commission
    for k, fee in sorted(fills_by.items()):
        inc = by_trade.get(k)
        if inc is None:
            if fee != 0:
                out.append((k[0], 'fill_without_commission_row', None, fee))
        elif inc != -fee:
            out.append((k[0], 'amount_mismatch', inc, fee))
    for k, inc in sorted(by_trade.items()):
        if k not in fills_by:
            out.append((k[0], 'commission_row_without_fill', inc, None))
    return out
