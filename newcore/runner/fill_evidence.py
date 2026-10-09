"""Venue fill / trade evidence: what a fills or userTrades read PROVES, or UNKNOWN (Cowork acceptance suite h3 / h4 / h6,
Codex #13 P1). Every runner decision that sizes, attributes or books from fill rows goes through `rows_of`:

  - not OK (raised / unknown / rejected)                      -> UNKNOWN
  - STALE: observed before this cycle (observed_at < now)      -> UNKNOWN
  - MISMATCH: a row of another order / symbol / position side  -> UNKNOWN
  - a row before the window that was asked for                 -> UNKNOWN
  - DUP / DUPLAST: the same trade id twice                     -> de-duplicated (identical rows); conflicting -> UNKNOWN
  - EMPTY / TRUNC: rows of an order that do not add up to the quantity the venue's own order record executed
                                                               -> UNKNOWN (never a partial sum, never zero)
  - NOT PROVEN COMPLETE: an OK read without the adapter's completeness evidence ('complete pages=P dups=N' in its
    detail: TestnetVenue pages to a proven end, FakeVenue answers whole) -> UNKNOWN (Cowork 6068372233 #2 /
    6069221337 #4: no row-count heuristic)
Unknown evidence is never zero and never widens ownership; callers record an incident and HOLD / stay pending.
"""
from __future__ import annotations

import re
from decimal import Decimal

from newcore.ports.venue import ReadKind

ZERO = Decimal(0)


COMPLETE = re.compile(r'complete pages=([1-9][0-9]*) dups=(0|[1-9][0-9]*)', re.ASCII)
MAX_PAGES = 10_000                     # Cowork 6070885320: a sane bound on the adapter's page count


class EvidencePending(LookupError):
    """A projection that needs evidence the venue did not prove (a fee, a fill): pending, never a zero."""


def proven_complete(read):
    """The adapter's own completeness evidence on an OK read (TestnetVenue fills / trades, nc-venue-testnet 5f0c959:
    ReadOutcome.detail 'complete pages=P dups=N' - every page read to a short page by its raw row count; FakeVenue
    emits 'complete pages=1 dups=0'). Absent -> the read is not proven complete -> UNKNOWN (fail-closed).
    Cowork 6070885320: canonical ASCII digits only (no leading zeros, no Unicode digits), 1 <= pages <= MAX_PAGES and
    dups <= the rows returned; anything else is not evidence."""
    detail = getattr(read, 'detail', None)
    m = COMPLETE.fullmatch(detail) if isinstance(detail, str) else None
    if m is None:
        return False
    pages, dups = int(m.group(1)), int(m.group(2))
    try:
        rows = len(read.value)
    except TypeError:
        return False
    return 1 <= pages <= MAX_PAGES and dups <= rows


def rows_of(read, *, symbol, now=None, eoid=None, side=None, since=None, expect=None):
    """-> (rows, None) with de-duplicated rows (oldest first), or (None, why).
    expect: {exchange_order_id: executed qty} the rows of each named order must add up to exactly (completeness)."""
    if read is None or read.kind is not ReadKind.OK:
        return None, f'read {getattr(read, "kind", "absent")}'
    if now is not None and read.observed_at_ms < now:
        return None, f'stale read (observed {read.observed_at_ms} < {now})'
    if not proven_complete(read):                                     # Cowork 6069221337 #4: never a row-count
        return None, 'no completeness evidence on the read (end not proven)'   # heuristic - the adapter proves it
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
