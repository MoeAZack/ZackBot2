"""Fixed C257--C259 physical-source seam, never an authority or loader."""
from __future__ import annotations

import hashlib
import math
from decimal import Decimal
from pathlib import Path
from tools.research import manifest as M

MINUTE_MS = 60_000
HOUR_MS = 3_600_000
SEAM_MS = 1788220800000  # 2026-09-01T00:00:00Z
CLARIFICATION_PATH = 'contracts/strategy_causal_pack_01/c257_259_hourly_source_correction.v2.json'
CLARIFICATION_SHA256 = '648f5fdf17217297fd5737f50f2d2f2707f79d02c1211b57646478f56bb3a74c'


def validate_clarification(root: Path) -> None:
    if hashlib.sha256((root/CLARIFICATION_PATH).read_bytes()).hexdigest() != CLARIFICATION_SHA256:
        raise ValueError('historical hourly source clarification bytes changed')


def trade_window(record, *, new: bool, start_ms: int, end_ms: int):
    """Select by protected provenance/coordinate before any byte interpretation."""
    if record.get('series') != 'klines':
        return None
    if not new and record.get('interval') == '1m':
        lo, hi = start_ms, min(end_ms, SEAM_MS)
    elif new and record.get('interval') == '1h':
        lo, hi = max(start_ms, SEAM_MS), end_ms
    else:
        return None
    return (lo, hi) if lo < hi else None


def exact_hours(rows):
    """Aggregate only complete UTC groups; expose no minute path information.

    Canonical 12-cell output preserves derived sums for otherwise unused native
    fields. OHLC/quote-volume consumers retain their original hourly schema.
    """
    grouped, previous = {}, None
    def decimal(value):
        return Decimal(value.decode('ascii') if isinstance(value,bytes) else str(value))
    for row in rows:
        if len(row) != 12:
            raise ValueError('minute source must have twelve canonical cells')
        t = int(row[0])
        if t % MINUTE_MS or int(row[6]) != t+MINUTE_MS-1:
            raise ValueError('minute source timestamp/close is noncanonical')
        quote = row[7] if isinstance(row[7],bytes) else str(row[7]).encode('ascii')
        if not M.QV_RE.fullmatch(quote):
            raise ValueError('minute quote volume is not canonical native schema')
        if previous is not None and t <= previous:
            raise ValueError('duplicate/unordered minute coordinate')
        previous = t
        values = [float(row[i]) for i in (1,2,3,4,5,7,9,10)]
        o,h,l,c,v,qv,tb,tq = values
        prices=tuple(decimal(row[i]) for i in (1,2,3,4))
        count = int(row[8])
        if (not all(math.isfinite(value) for value in values) or min(o,h,l,c) <= 0
                or not l <= min(o,c) <= max(o,c) <= h or min(v,qv,tb,tq,count) < 0):
            raise ValueError('invalid native minute economic fields')
        if not prices[2] <= min(prices[0],prices[3]) <= max(prices[0],prices[3]) <= prices[1]:
            raise ValueError('invalid exact native minute price range')
        grouped.setdefault(t//HOUR_MS*HOUR_MS, []).append((t,values,count,row,prices))
    hours = []
    observed = sorted(grouped)
    if observed and observed != list(range(observed[0],observed[-1]+HOUR_MS,HOUR_MS)):
        raise ValueError('missing entire internal canonical minute hour')
    for h in observed:
        group = grouped[h]
        if [row[0] for row in group] != list(range(h,h+HOUR_MS,MINUTE_MS)):
            raise ValueError('missing/partial canonical minute hour')
        sums = {i:math.fsum(row[1][i] for row in group) for i in (4,5,6,7)}
        if not all(math.isfinite(value) for value in sums.values()):
            raise ValueError('nonfinite derived hourly volume')
        # Retain exact native price cells for Decimal consumers (C257).
        # Binary64 consumers still convert once at their unchanged typed seam.
        high=max(group,key=lambda item:item[4][1])[3][2]
        low=min(group,key=lambda item:item[4][2])[3][3]
        hours.append((h,group[0][3][1],high,low,group[-1][3][4],sums[4],
                      h+HOUR_MS-1,sums[5],sum(row[2] for row in group),sums[6],sums[7],'0'))
    return tuple(hours)
