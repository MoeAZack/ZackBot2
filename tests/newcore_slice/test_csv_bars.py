"""CsvBarSource: data_long parsing, integer ms, closed candles only, manifest check."""
import os
from decimal import Decimal as D

import pytest

from newcore.adapters.csv_bars import CsvBarSource, parse_utc_ms, read_csv
from newcore.ports import BarSource, ReadKind
from newcore.ports.values import PortValueError
from slice_helpers import H4

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_parse_utc_ms_matches_known_epochs():
    assert parse_utc_ms('1970-01-01 00:00:00') == 0
    assert parse_utc_ms('2021-12-19 08:00:00') == 1_639_900_800_000
    assert parse_utc_ms('2024-02-29 20:00:00') == 1_709_236_800_000
    with pytest.raises(ValueError):
        parse_utc_ms('2021-12-19T08:00:00Z')


def write(tmp_path, rows):
    p = tmp_path / 'X_4h.csv'
    p.write_text('t,o,h,l,c,v\n' + '\n'.join(rows) + '\n', encoding='utf-8')
    return str(p)


def test_closed_only_and_limit(tmp_path):
    rows = [f'2024-01-01 {h:02d}:00:00,100.10,101,99,100.5,7' for h in (0, 4, 8, 12)]
    bars = read_csv(write(tmp_path, rows), H4)
    assert bars[0].open == D('100.10') and bars[0].close_ms == bars[0].open_ms + H4
    src = CsvBarSource({'XUSDT': bars}, H4)
    assert isinstance(src, BarSource)
    t0 = bars[0].open_ms
    r = src.closed_bars('XUSDT', H4, as_of_ms=t0 + 2 * H4 + 5, limit=10)      # candle 2 is forming
    assert r.kind is ReadKind.OK and [b.open_ms for b in r.value] == [t0, t0 + H4]
    r = src.closed_bars('XUSDT', H4, as_of_ms=t0 + 4 * H4, limit=2)
    assert [b.open_ms for b in r.value] == [t0 + 2 * H4, t0 + 3 * H4]
    assert src.closed_bars('XUSDT', H4, as_of_ms=t0 + H4 - 1, limit=5).value == ()
    with pytest.raises(PortValueError):
        src.closed_bars('XUSDT', H4, as_of_ms=t0 + H4, limit=0)
    with pytest.raises(ValueError):
        src.closed_bars('XUSDT', 3_600_000, as_of_ms=t0 + H4, limit=1)


def test_gapped_file_is_refused(tmp_path):
    rows = ['2024-01-01 00:00:00,1,1,1,1,1', '2024-01-01 08:00:00,1,1,1,1,1']
    with pytest.raises(PortValueError):
        read_csv(write(tmp_path, rows), H4)


def test_data_long_matches_the_manifest():
    src = CsvBarSource.from_data_long(ROOT, ['BTCUSDT'], '4h')
    bars = src.all_bars('BTCUSDT')
    assert len(bars) == 10_499 and bars[0].open_ms == parse_utc_ms('2021-12-19 08:00:00')
