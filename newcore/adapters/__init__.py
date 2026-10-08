"""NEWCORE slice S1 adapters: the in-process implementations of the step-0 ports.

    fake_venue      FakeVenue      VenuePort, deterministic and driven by the replay candle path (zb-path/1)
    memory_journal  MemoryJournal  JournalPort admitting through the step-0 JournalGate
    csv_bars        CsvBarSource   BarSource over the frozen data_long CSVs (closed candles only, integer ms)
    exchange_rules  load_rules     typed InstrumentRules from data/exchange_rules_<env>.json

The durable file journal (NC-02a) and the testnet venue are separate adapters of the same ports (S3 / S5).
"""
from .csv_bars import CsvBarSource, parse_utc_ms, read_csv
from .exchange_rules import load_rules
from .fake_venue import CostModel, FakeVenue, path_points
from .memory_journal import MemoryJournal
