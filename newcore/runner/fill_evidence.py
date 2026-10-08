"""Venue fill / trade evidence: what a fills or userTrades read PROVES, or UNKNOWN (Cowork acceptance suite h3 / h4 / h6,
Codex #13 P1). Every runner decision that sizes, attributes or books from fill rows goes through `rows_of`:

  - not OK (raised / unknown / rejected)                      -> UNKNOWN
  - STALE: observed before this cycle (observed_at < now)      -> UNKNOWN
  - MISMATCH: a row of another order / symbol / position side  -> UNKNOWN
  - a row before the window that was asked for                 -> UNKNOWN
  - DUP / DUPLAST: the same trade id twice                     -> de-duplicated (identical rows); conflicting -> UNKNOWN
  - EMPTY / TRUNC: rows of an order that do not add up to the quantity the venue's own order record executed
                                                               -> UNKNOWN (never a partial sum, never zero)
  - a FULL page (as many rows as one venue page holds) with no proven end -> UNKNOWN, unless the read carries the
    adapter's completeness evidence (proven_complete: TestnetVenue pages to a proven end itself; Cowork 6068372233 #2)
Unknown evidence is never zero and never widens ownership; callers record an incident and HOLD / stay pending.
"""
from __future__ import annotations

import re
from decimal import Decimal

from newcore.ports.venue import ReadKind

ZERO = Decimal(0)
PAGE_LIMIT = 1000                     # one venue page of fills / userTrades (Binance max limit): full = end not proven


COMPLETE = re.compile(r'complete pages=\d+ dups=\d+')


class EvidencePending(LookupError):
    """A projection that needs evidence the venue did not prove (a fee, a fill): pending, never a zero."""


def proven_complete(read):
    """The adapter's own completeness evidence on an OK read (TestnetVenue fills / trades, nc-venue-testnet 5f0c959:
    ReadOutcome.detail 'complete pages=P dups=N' - every page read to a short page by its raw row count). Present:
    the page count heuristic is not applied (a long, complete multi-page answer is valid). Absent (FakeVenue, an
    adapter without the marker): a page that reaches PAGE_LIMIT rows is not proven complete -> UNKNOWN."""
    return isinstance(getattr(read, 'detail', None), str) and COMPLETE.fullmatch(read.detail) is not None


def rows_of(read, *, symbol, now=None, eoid=None, side=None, since=None, expect=None):
    """-> (rows, None) with de-duplicated rows (oldest first), or (None, why).
    expect: {exchange_order_id: executed qty} the rows of each named order must add up to exactly (completeness)."""
    if read is None or read.kind is not ReadKind.OK:
        return None, f'read {getattr(read, "kind", "absent")}'
    if now is not None and read.observed_at_ms < now:
        return None, f'stale read (observed {read.observed_at_ms} < {now})'
    if not proven_complete(read) and len(read.value) >= PAGE_LIMIT:
        return None, f'a full page of {len(read.value)} rows: its end is not proven'
    seen = {}
    for f in read.value:
        if f.symbol != symbol or (eoid is not None and f.exchange_order_id != eoid) or \
                (side is not None and f.position_side != side):
            return None, f'mismatched row {f.trade_id} (order {f.exchange_order_id})'
        if since is not None and f.at_ms < since:
            return None, f'row {f.trade_id} before the window'
        prev = seen.get(f.trade_id)
        if prev is not None and prev != f:
            return None, f'conflicting duplicate trade id {f.trade_id}'
        seen[f.trade_id] = f
    rows = tuple(sorted(seen.values(), key=lambda f: (f.at_ms, f.trade_id)))
    for oid, qty in (expect or {}).items():
        got = sum((f.qty for f in rows if f.exchange_order_id == oid), ZERO)
        if got != qty:
            return None, f'incomplete rows of order {oid} ({got} of {qty})'
    return rows, None
