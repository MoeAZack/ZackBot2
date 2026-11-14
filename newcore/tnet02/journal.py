"""TNET-02 durable per-case harness journal.

A hash-chained, fsynced, append-only line file per case.  Reopening verifies every line, the chain, the sequence
and the case identity; any defect raises (the adapter turns that into HOLD).  This is the harness's own write-ahead
record for the restart scenario; wiring the NC-01/NC-02 event grammar is the replacement-entrypoint slice.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from decimal import Decimal
from pathlib import Path

from newcore.domain.base import dec_str
from newcore.domain.orders import IntentState, Purpose, Side

from .ports import JournalRecord, PortError, RecordKind


_CASE = re.compile(r"case_[0-9a-f]{32}")
_GENESIS = "0" * 64
_FIELDS = ("kind", "sequence", "case_id", "boot_id", "symbol", "side", "intent_id", "client_id", "route", "purpose",
           "state", "requested_qty", "executed_qty", "exchange_order_id", "stop_price")


class JournalError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _wire(record: JournalRecord) -> dict:
    out = {}
    for name in _FIELDS:
        value = getattr(record, name)
        if type(value) is Decimal:
            value = dec_str(value)
        elif hasattr(value, "value"):
            value = value.value
        out[name] = value
    return out


def _record(row: dict) -> JournalRecord:
    if type(row) is not dict or set(row) != set(_FIELDS):
        raise JournalError("journal.line_keys")

    def dec(value):
        if type(value) is not str:
            raise JournalError("journal.line_decimal")
        return Decimal(value)

    try:
        return JournalRecord(
            RecordKind(row["kind"]), row["sequence"], row["case_id"], row["boot_id"], row["symbol"], Side(row["side"]),
            row["intent_id"], row["client_id"], row["route"],
            None if row["purpose"] is None else Purpose(row["purpose"]),
            None if row["state"] is None else IntentState(row["state"]),
            dec(row["requested_qty"]), dec(row["executed_qty"]), row["exchange_order_id"],
            None if row["stop_price"] is None else dec(row["stop_price"]))
    except (PortError, ValueError, TypeError, KeyError):
        raise JournalError("journal.line_invalid") from None


class FileCaseJournal:
    __slots__ = ("_path", "_case_id", "_records", "_head", "_closed", "_on_close", "_check_root")

    def __init__(self, path: Path, case_id: str, records: tuple[JournalRecord, ...], head: str,
                 on_close=None, check_root=None):
        self._path, self._case_id = path, case_id
        self._records, self._head, self._closed = list(records), head, False
        self._on_close, self._check_root = on_close, check_root

    def records(self) -> tuple[JournalRecord, ...]:
        return tuple(self._records)

    def append(self, record: JournalRecord) -> None:
        if self._closed:
            raise JournalError("journal.closed")
        if type(record) is not JournalRecord or record.case_id != self._case_id:
            raise JournalError("journal.record")
        if record.sequence != len(self._records) + 1:
            raise JournalError("journal.sequence")
        body = json.dumps(_wire(record), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = hashlib.sha256((self._head + body).encode("ascii")).hexdigest()
        line = (json.dumps({"prev": self._head, "sha256": digest, "body": json.loads(body)}, sort_keys=True,
                           separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
        if self._check_root is not None:
            self._check_root()
        try:
            fd = os.open(self._path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0)
                         | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            raise JournalError("journal.open") from None
        try:
            view = memoryview(line)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        except OSError:
            raise JournalError("journal.write") from None
        finally:
            os.close(fd)
        self._records.append(record)
        self._head = digest

    def close(self) -> None:
        if not self._closed and self._on_close is not None:
            self._on_close(self._case_id, self._head, len(self._records))
        self._closed = True


class FileJournalOpener:
    """Opens per-case journals under one existing real directory."""

    def __init__(self, root: os.PathLike[str] | str):
        try:
            info = os.lstat(root)
        except (OSError, TypeError, ValueError):
            raise JournalError("journal.root") from None
        if not stat.S_ISDIR(info.st_mode):
            raise JournalError("journal.root")
        self._root = Path(root)
        self._root_id = (info.st_dev, info.st_ino)
        # In-process tail anchors: head digest and record count of each journal at close.  A reopen must match the
        # anchor exactly, so a truncated or re-chained file is refused even though its own chain is consistent.
        self._anchors: dict[str, tuple[str, int]] = {}

    def _check_root(self) -> None:
        try:
            info = os.lstat(self._root)
        except OSError:
            raise JournalError("journal.root") from None
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self._root_id:
            raise JournalError("journal.root")

    def _anchor(self, case_id: str, head: str, count: int) -> None:
        self._anchors[case_id] = (head, count)

    def open(self, case_id: str, boot_id: str, *, reopen: bool) -> FileCaseJournal:
        if type(case_id) is not str or _CASE.fullmatch(case_id) is None or type(reopen) is not bool:
            raise JournalError("journal.case_id")
        path = self._root / f"{case_id}.journal"
        self._check_root()
        if not reopen:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            except FileExistsError:
                raise JournalError("journal.exists") from None
            except OSError:
                raise JournalError("journal.create") from None
            os.fsync(fd)
            os.close(fd)
            return FileCaseJournal(path, case_id, (), _GENESIS, self._anchor, self._check_root)
        try:
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode):
                raise JournalError("journal.missing")
            data = path.read_bytes()
        except OSError:
            raise JournalError("journal.missing") from None
        head, records = _GENESIS, []
        if data and not data.endswith(b"\n"):
            raise JournalError("journal.torn")
        for raw in data.splitlines():
            try:
                line = json.loads(raw.decode("ascii"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise JournalError("journal.line_syntax") from None
            if type(line) is not dict or set(line) != {"prev", "sha256", "body"} or line["prev"] != head:
                raise JournalError("journal.chain")
            body = json.dumps(line["body"], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            if hashlib.sha256((head + body).encode("ascii")).hexdigest() != line["sha256"]:
                raise JournalError("journal.chain")
            record = _record(line["body"])
            if record.case_id != case_id or record.sequence != len(records) + 1:
                raise JournalError("journal.identity")
            records.append(record)
            head = line["sha256"]
        if self._anchors.get(case_id) != (head, len(records)):
            raise JournalError("journal.anchor")
        return FileCaseJournal(path, case_id, tuple(records), head, self._anchor, self._check_root)


__all__ = ["FileCaseJournal", "FileJournalOpener", "JournalError"]
