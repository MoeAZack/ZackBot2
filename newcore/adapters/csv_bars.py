"""CsvBarSource: the BarSource over frozen `data_long` CSVs (STEP0_INTERFACE.md section 5).

File format (data_long/<tf>/<SYMBOL>_<tf>.csv): header `t,o,h,l,c,v`; t = candle OPEN time as 'YYYY-MM-DD HH:MM:SS' UTC.
Prices are kept as the exact CSV text (Decimal), times as integer UTC ms (converted with integer calendar arithmetic, no
datetime / timezone library). Every file is parsed once and validated as Bars (OHLC order, alignment, contiguity);
`from_data_long` also checks each file's sha256 against DATA_MANIFEST.json and refuses a mismatch.

closed_bars(symbol, tf_ms, as_of_ms, limit) answers the last `limit` candles whose close_ms <= as_of_ms, oldest first,
through check_closed_bars: the forming candle is never returned. A symbol or timeframe this source does not hold raises
ValueError (a wiring error, not a venue answer).
"""
from __future__ import annotations

import bisect
import hashlib
import json
import os
from decimal import Decimal

from newcore.ports.bars import Bar, check_closed_bars, check_request
from newcore.ports.venue import ReadKind, ReadOutcome

TF_MS = {'1h': 3_600_000, '4h': 14_400_000}
DAY_MS = 86_400_000


def _days_from_civil(y, m, d):
    """Days since 1970-01-01 of a proleptic Gregorian date (H. Hinnant's algorithm, integer only)."""
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def parse_utc_ms(text):
    """'YYYY-MM-DD HH:MM:SS' (UTC) -> integer ms. Strict: exactly that shape."""
    if len(text) != 19 or text[4] != '-' or text[7] != '-' or text[10] != ' ' or text[13] != ':' or text[16] != ':':
        raise ValueError(f'bad timestamp {text!r}')
    y, mo, d, h, mi, s = int(text[0:4]), int(text[5:7]), int(text[8:10]), int(text[11:13]), int(text[14:16]), \
        int(text[17:19])
    if not (1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and s < 60):
        raise ValueError(f'bad timestamp {text!r}')
    return ((_days_from_civil(y, mo, d) * 24 + h) * 60 + mi) * 60_000 + s * 1000


def read_csv(path, tf_ms):
    """Parse one data_long CSV into validated, contiguous Bars."""
    with open(path, encoding='utf-8', newline='') as fh:
        lines = fh.read().splitlines()
    if not lines or lines[0].strip() != 't,o,h,l,c,v':
        raise ValueError(f'{path}: header must be t,o,h,l,c,v')
    out = []
    for n, line in enumerate(lines[1:], 2):
        parts = line.split(',')
        if len(parts) != 6:
            raise ValueError(f'{path}:{n}: expected 6 fields')
        t = parse_utc_ms(parts[0])
        o, h, l, c, v = (Decimal(x) for x in parts[1:])
        out.append(Bar(open_ms=t, close_ms=t + tf_ms, open=o, high=h, low=l, close=c, volume=v))
    bars = tuple(out)
    if bars:
        check_closed_bars(bars, tf_ms, bars[-1].close_ms)            # aligned, contiguous, oldest first
    return bars


def sha256_file(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class CsvBarSource:
    def __init__(self, bars_by_symbol, tf_ms):
        """bars_by_symbol: {symbol: tuple[Bar, ...]} already validated (see read_csv / from_data_long)."""
        self.tf_ms = tf_ms
        self._bars = {s: tuple(b) for s, b in bars_by_symbol.items()}
        self._close = {s: [b.close_ms for b in bs] for s, bs in self._bars.items()}
        for s, bs in self._bars.items():
            if bs:
                check_closed_bars(bs, tf_ms, bs[-1].close_ms)

    @classmethod
    def from_data_long(cls, root, symbols, tf='4h', *, verify_manifest=True):
        tf_ms = TF_MS[tf]
        manifest = {}
        if verify_manifest:
            with open(os.path.join(root, 'DATA_MANIFEST.json'), encoding='utf-8') as fh:
                doc = json.load(fh)
            manifest = doc.get('files', doc)
        out = {}
        for s in symbols:
            rel = f'data_long/{tf}/{s}_{tf}.csv'
            path = os.path.join(root, *rel.split('/'))
            if verify_manifest:
                want = (manifest.get(rel) or {}).get('sha256')
                got = sha256_file(path)
                if want != got:
                    raise ValueError(f'{rel}: sha256 {got} does not match DATA_MANIFEST ({want})')
            out[s] = read_csv(path, tf_ms)
        return cls(out, tf_ms)

    def all_bars(self, symbol):
        """The whole frozen series (to build a FakeVenue over the same candles)."""
        return self._bars[symbol]

    def closed_bars(self, symbol, tf_ms, *, as_of_ms, limit):
        check_request(symbol, tf_ms, as_of_ms, limit)
        if tf_ms != self.tf_ms or symbol not in self._bars:
            raise ValueError(f'this source holds {sorted(self._bars)} at {self.tf_ms} ms, not {symbol} at {tf_ms}')
        end = bisect.bisect_right(self._close[symbol], as_of_ms)
        bars = self._bars[symbol][max(0, end - limit):end]
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=as_of_ms, value=check_closed_bars(bars, tf_ms, as_of_ms))
