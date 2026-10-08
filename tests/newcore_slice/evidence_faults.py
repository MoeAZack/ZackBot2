"""Evidence faults for venue fills / trades reads (Cowork acceptance suite v16 h3..h7): each fault class turns the TRUE
read into what a broken adapter / page / cache could answer."""
import dataclasses
from decimal import Decimal as D

from newcore.ports.venue import ReadKind, ReadOutcome

FAULTS = ('raise', 'unknown', 'empty', 'trunc', 'stale', 'dup', 'duplast', 'mismatch')
UNPROVEN = ('raise', 'unknown', 'empty', 'trunc', 'stale', 'mismatch')      # must read as UNKNOWN
RECOVERABLE = ('dup', 'duplast')                                            # de-duplicated: the truth


def faulty(read, fault, *, stale_ms=4 * 3600 * 1000):
    """Wrap a venue read (fills / trades) so every OK answer is bent by `fault`."""
    def call(*args, **kwargs):
        if fault == 'raise':
            raise ConnectionError('evidence read raised')
        r = read(*args, **kwargs)
        if fault == 'ok' or r.kind is not ReadKind.OK:
            return r
        rows = tuple(r.value)
        if fault == 'unknown':
            return ReadOutcome(kind=ReadKind.UNKNOWN, observed_at_ms=r.observed_at_ms, detail='timeout')
        if fault == 'empty':
            rows = ()
        elif fault == 'trunc':
            rows = (dataclasses.replace(rows[0], qty=rows[0].qty / D(2)),) if rows else ()
        elif fault == 'stale':
            return ReadOutcome(kind=ReadKind.OK, observed_at_ms=r.observed_at_ms - stale_ms, value=rows)
        elif fault == 'dup':
            rows = rows + rows
        elif fault == 'duplast':
            rows = rows + rows[-1:]
        elif fault == 'mismatch':
            rows = tuple(dataclasses.replace(f, exchange_order_id='999999999') for f in rows)
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=r.observed_at_ms, value=rows)
    return call
