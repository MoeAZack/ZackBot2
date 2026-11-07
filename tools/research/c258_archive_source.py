"""Checksum-bound v6 input for C258; no outcome evaluation at import time."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from tools.research import cross_sectional_relative_strength_continuation_c258_stagea as C
from tools.research.authority_v6_runner import _read_record_rows


@dataclass(frozen=True)
class SourceRows:
    symbol: str
    stream: str
    fold: str
    start_ms: int
    end_ms: int
    rows: tuple[tuple, ...]
    records: tuple[tuple[str, str], ...]


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def frozen_window(fold: str) -> tuple[int, int]:
    splits = C.load_frozen_contract()["splits"]
    windows = {"development": splits["development"]}
    windows.update({f"wf{i+1}": value for i, value in enumerate(splits["forward_folds"])})
    if fold not in windows:
        raise C.IncompleteAuthority("unknown or sealed C258 fold")
    return tuple(_ms(part) for part in windows[fold].split("/"))


class C258ArchiveSource:
    """Opens protected authority first, then only explicitly bound archive records.

    This source is a data dependency of the pending replay, not an executable
    evaluator. It cannot accept caller market rows or replace the frozen universe.
    """

    def __init__(self, pins: C.AuthorityPins, store: Path, *, root: Path = C.ROOT):
        docs = C.validate_authority_pins(pins, root=root)
        self._store = Path(store)
        self._manifest = docs["manifest_v6"]
        self._pit = docs["pit_v9"]
        self._filters = docs["filter_v4"]
        self._records = tuple((record, False) for record in self._manifest["active_base_files_v5"])
        self._records += tuple((record, True) for record in self._manifest["files_v6"])
        paths = [record["path"] for record, _ in self._records]
        if len(paths) != len(set(paths)):
            raise C.IncompleteAuthority("duplicate active archive path")

    def load(self, *, symbol: str, stream: str, fold: str,
             start_ms: int, end_ms: int) -> SourceRows:
        lo, hi = frozen_window(fold)
        if (type(start_ms) is not int or type(end_ms) is not int
                or not lo <= start_ms < end_ms <= hi):
            raise C.IncompleteAuthority("market request escapes frozen fold")
        if stream not in ("klines", "markPriceKlines", "fundingRate"):
            raise C.IncompleteAuthority("unsupported C258 source stream")
        selected = []
        rows: dict[int, tuple] = {}
        for record, new in self._records:
            if (record.get("symbol") != symbol or record.get("series") != stream
                    or (stream != "fundingRate" and record.get("interval") != "1h")):
                continue
            # Reject any record that intersects the requested range ambiguously.
            first = record.get("observed_first_ms") if new else record.get("first_open_ms")
            last = record.get("observed_last_ms") if new else record.get("last_open_ms")
            if first is not None and last is not None and (last < start_ms or first >= end_ms):
                continue
            _, parsed = _read_record_rows(self._store, record, new=new)
            digest = record["raw_sha256" if new else "sha256"]
            selected.append((record["path"], digest))
            for item in parsed:
                if isinstance(item, Mapping):
                    stamp = item["fundingTime"]
                    row = (stamp, str(item["fundingRate"]))
                else:
                    row = tuple(item)
                    stamp = int(row[0])
                if start_ms <= stamp < end_ms:
                    if stamp in rows:
                        raise C.IncompleteAuthority("overlapping active authority timestamps")
                    rows[stamp] = row
        if stream != "fundingRate" and tuple(sorted(rows)) != tuple(range(start_ms, end_ms, C.HOUR_MS)):
            raise C.IncompleteAuthority("missing canonical C258 source candle")
        if not selected:
            raise C.IncompleteAuthority("no checksum-bound source records")
        return SourceRows(symbol, stream, fold, start_ms, end_ms,
                          tuple(rows[t] for t in sorted(rows)), tuple(sorted(selected)))
