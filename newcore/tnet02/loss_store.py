"""Atomic, canonical persistence for the account-bound TNET-02 loss tracker.

The immutable identity file distinguishes a first-ever account from a deleted/torn state.  The independently atomic
anchor binds the current state digest and timestamp; any missing file, mismatch, rollback, or interrupted two-file
update fails closed instead of silently starting a neutral tracker.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from newcore import loss_state as LS
from newcore.domain.base import dec_str
from newcore.risk import ClusterEntry, PositionSide


_SCHEMA = "zackbot.tnet02.loss-state/2"
_HIGH_WATER_SCHEMA = "zackbot.tnet02.loss-high-water/1"
_ACCOUNT = re.compile(r"acct_[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{16}")
_SYMBOL = re.compile(r"[A-Z0-9]{2,32}")
_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_IDENTITY = ".tnet02-loss-identity.json"
_STATE = ".tnet02-loss-state.json"
_ANCHOR = ".tnet02-loss-anchor.json"


class LossStoreError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise LossStoreError(code) from None


def _identity(account_id: str, binding_digest: str) -> dict:
    if (type(account_id) is not str or _ACCOUNT.fullmatch(account_id) is None
            or type(binding_digest) is not str or _DIGEST.fullmatch(binding_digest) is None):
        _fail("loss_store.identity")
    return {"account_id": account_id, "binding_digest": binding_digest, "schema": _SCHEMA}


def _canonical(body: dict) -> bytes:
    return (json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def _decimal(value: object, code: str, *, positive: bool = False) -> Decimal:
    if type(value) is not str:
        _fail(code)
    try:
        out = Decimal(value)
    except Exception:
        _fail(code)
    if not out.is_finite() or dec_str(out) != value or (positive and out <= 0):
        _fail(code)
    return out


def _integer(value: object, code: str) -> int:
    if type(value) is not int or not 0 < value <= 253402300799999:
        _fail(code)
    return value


def _tracker_wire(tracker: LS.LossTracker) -> dict:
    if type(tracker) is not LS.LossTracker:
        _fail("loss_store.tracker")
    return {
        "account_id": tracker.account_id,
        "day_start_equity": dec_str(tracker.day_start_equity),
        "day_start_unrealized": dec_str(tracker.day_start_unrealized),
        "drawdown_kill_latched": tracker.drawdown_kill_latched,
        "entries": [{"at_ms": row.at_ms, "side": row.entry.side.value,
                     "strategy_id": row.entry.strategy_id, "symbol": row.entry.symbol}
                    for row in tracker.entries],
        "halt_latched": tracker.halt_latched,
        "last_ms": tracker.last_ms,
        "peak_equity": dec_str(tracker.peak_equity),
        "realized_pnl": dec_str(tracker.realized_pnl),
        "stop_outs": [{"at_ms": row.at_ms, "strategy_id": row.strategy_id} for row in tracker.stop_outs],
        "trading_day": tracker.trading_day,
    }


_TRACKER_KEYS = {"account_id", "trading_day", "day_start_equity", "day_start_unrealized", "realized_pnl",
                 "halt_latched", "peak_equity", "drawdown_kill_latched", "last_ms", "stop_outs", "entries"}


def _id(value: object, code: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        _fail(code)
    return value


def _tracker_read(row: object, account_id: str) -> LS.LossTracker:
    if type(row) is not dict or set(row) != _TRACKER_KEYS or row.get("account_id") != account_id:
        _fail("loss_store.tracker")
    last = _integer(row["last_ms"], "loss_store.last_ms")
    day = row["trading_day"]
    if type(day) is not str or day != LS.cairo_trading_day(last):
        _fail("loss_store.trading_day")
    if type(row["halt_latched"]) is not bool or type(row["drawdown_kill_latched"]) is not bool:
        _fail("loss_store.latches")
    stop_rows, entry_rows = row["stop_outs"], row["entries"]
    if type(stop_rows) is not list or type(entry_rows) is not list:
        _fail("loss_store.history")
    stops = []
    previous = 0
    for item in stop_rows:
        if type(item) is not dict or set(item) != {"strategy_id", "at_ms"}:
            _fail("loss_store.stop_out")
        at = _integer(item["at_ms"], "loss_store.stop_out")
        if at < previous or at > last:
            _fail("loss_store.stop_out")
        previous = at
        stops.append(LS.StopOut(strategy_id=_id(item["strategy_id"], "loss_store.stop_out"), at_ms=at))
    entries = []
    previous = 0
    for item in entry_rows:
        if type(item) is not dict or set(item) != {"strategy_id", "symbol", "side", "at_ms"}:
            _fail("loss_store.entry")
        at = _integer(item["at_ms"], "loss_store.entry")
        if (at < previous or at > last or type(item["symbol"]) is not str
                or _SYMBOL.fullmatch(item["symbol"]) is None):
            _fail("loss_store.entry")
        previous = at
        try:
            side = PositionSide(item["side"])
        except (TypeError, ValueError):
            _fail("loss_store.entry")
        entries.append(LS.LoggedEntry(entry=ClusterEntry(strategy_id=_id(item["strategy_id"], "loss_store.entry"),
                                                         symbol=item["symbol"], side=side), at_ms=at))
    return LS.LossTracker(
        account_id=account_id, trading_day=day,
        day_start_equity=_decimal(row["day_start_equity"], "loss_store.decimal", positive=True),
        day_start_unrealized=_decimal(row["day_start_unrealized"], "loss_store.decimal"),
        realized_pnl=_decimal(row["realized_pnl"], "loss_store.decimal"),
        halt_latched=row["halt_latched"],
        peak_equity=_decimal(row["peak_equity"], "loss_store.decimal", positive=True),
        drawdown_kill_latched=row["drawdown_kill_latched"], last_ms=last,
        stop_outs=tuple(stops), entries=tuple(entries))


@dataclass(frozen=True, slots=True)
class LossHighWater:
    generation: int
    state_sha256: str

    def __post_init__(self) -> None:
        if (type(self.generation) is not int or self.generation < 1 or type(self.state_sha256) is not str
                or re.fullmatch(r"[0-9a-f]{64}", self.state_sha256) is None):
            _fail("loss_high_water.record")


class LossHighWaterPort(Protocol):
    def load(self, account_id: str, binding_digest: str) -> LossHighWater | None: ...

    def advance(self, account_id: str, binding_digest: str, expected_generation: int | None,
                record: LossHighWater) -> None: ...


def _water_body(account_id: str, binding_digest: str, record: LossHighWater) -> dict:
    identity = _identity(account_id, binding_digest)
    return {"account_id": identity["account_id"], "binding_digest": identity["binding_digest"],
            "generation": record.generation, "schema": _HIGH_WATER_SCHEMA,
            "state_sha256": record.state_sha256}


def _water_read(body: object, account_id: str, binding_digest: str) -> LossHighWater:
    expected = _water_body(account_id, binding_digest, LossHighWater(1, "0" * 64))
    if type(body) is not dict or set(body) != set(expected):
        _fail("loss_high_water.record")
    if (body.get("schema") != _HIGH_WATER_SCHEMA or body.get("account_id") != account_id
            or body.get("binding_digest") != binding_digest):
        _fail("loss_high_water.identity")
    return LossHighWater(body.get("generation"), body.get("state_sha256"))


def _sync_directory(path: Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    try:
        fd = os.open(path, os.O_RDONLY | directory_flag)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        _fail("loss_store.durability")


class FileLossHighWater:
    """Test/injected independent high-water file, located outside the replaceable state directory."""

    def __init__(self, path: os.PathLike[str] | str):
        self._path = Path(path)
        try:
            info = os.lstat(self._path.parent)
        except OSError:
            _fail("loss_high_water.root")
        if not stat.S_ISDIR(info.st_mode):
            _fail("loss_high_water.root")
        self._parent_id = (info.st_dev, info.st_ino)

    def _check_parent(self) -> None:
        try:
            info = os.lstat(self._path.parent)
        except OSError:
            _fail("loss_high_water.root")
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self._parent_id:
            _fail("loss_high_water.root")

    def load(self, account_id: str, binding_digest: str) -> LossHighWater | None:
        _identity(account_id, binding_digest)
        self._check_parent()
        try:
            info = os.lstat(self._path)
        except FileNotFoundError:
            return None
        except OSError:
            _fail("loss_high_water.read")
        if not stat.S_ISREG(info.st_mode):
            _fail("loss_high_water.read")
        try:
            raw = self._path.read_bytes()
            body = json.loads(raw.decode("ascii"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            _fail("loss_high_water.read")
        if _canonical(body) != raw:
            _fail("loss_high_water.read")
        return _water_read(body, account_id, binding_digest)

    def advance(self, account_id: str, binding_digest: str, expected_generation: int | None,
                record: LossHighWater) -> None:
        current = self.load(account_id, binding_digest)
        if ((current is None) != (expected_generation is None)
                or (current is not None and current.generation != expected_generation)
                or record.generation != (1 if expected_generation is None else expected_generation + 1)):
            _fail("loss_high_water.rollback")
        raw = _canonical(_water_body(account_id, binding_digest, record))
        self._check_parent()
        if current is None:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            try:
                fd = os.open(self._path, flags, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError:
                _fail("loss_high_water.write")
        else:
            try:
                fd, temporary = tempfile.mkstemp(prefix=".tnet02-water-", suffix=".tmp", dir=self._path.parent)
                os.chmod(temporary, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self._path)
            except OSError:
                try:
                    os.unlink(temporary)
                except (OSError, UnboundLocalError):
                    pass
                _fail("loss_high_water.write")
        _sync_directory(self._path.parent)


class WindowsRegistryLossHighWater:
    """Production high-water under locked HKCU, independent of filesystem state directories."""

    _KEY = r"Software\ZackBot\TNET02\LossState"

    def __init__(self) -> None:
        if os.name != "nt":
            _fail("loss_high_water.platform")
        try:
            import winreg  # noqa: F401
        except ImportError:
            _fail("loss_high_water.platform")

    @staticmethod
    def _value_name(account_id: str, binding_digest: str) -> str:
        _identity(account_id, binding_digest)
        return hashlib.sha256((account_id + "\0" + binding_digest).encode("ascii")).hexdigest()

    @staticmethod
    def _mutex(account_id: str, binding_digest: str):
        try:
            import ctypes
            kernel = ctypes.windll.kernel32
            kernel.CreateMutexW.restype = ctypes.c_void_p
            kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
            kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            kernel.ReleaseMutex.argtypes = (ctypes.c_void_p,)
            kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            name = "Local\\ZackBot-TNET02-Loss-" + WindowsRegistryLossHighWater._value_name(
                account_id, binding_digest)
            handle = kernel.CreateMutexW(None, False, name)
            if not handle or kernel.WaitForSingleObject(handle, 30_000) not in (0, 0x80):
                _fail("loss_high_water.lock")
            return kernel, handle
        except LossStoreError:
            raise
        except Exception:
            _fail("loss_high_water.lock")

    @staticmethod
    def _release(lock) -> None:
        kernel, handle = lock
        kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)

    @classmethod
    def _load_unlocked(cls, account_id: str, binding_digest: str) -> LossHighWater | None:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls._KEY, 0, winreg.KEY_READ) as key:
                raw, kind = winreg.QueryValueEx(key, cls._value_name(account_id, binding_digest))
        except FileNotFoundError:
            return None
        except OSError:
            _fail("loss_high_water.read")
        if kind != winreg.REG_SZ or type(raw) is not str:
            _fail("loss_high_water.read")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            _fail("loss_high_water.read")
        if _canonical(body).decode("ascii") != raw:
            _fail("loss_high_water.read")
        return _water_read(body, account_id, binding_digest)

    def load(self, account_id: str, binding_digest: str) -> LossHighWater | None:
        lock = self._mutex(account_id, binding_digest)
        try:
            return self._load_unlocked(account_id, binding_digest)
        finally:
            self._release(lock)

    def advance(self, account_id: str, binding_digest: str, expected_generation: int | None,
                record: LossHighWater) -> None:
        lock = self._mutex(account_id, binding_digest)
        try:
            current = self._load_unlocked(account_id, binding_digest)
            if ((current is None) != (expected_generation is None)
                    or (current is not None and current.generation != expected_generation)
                    or record.generation != (1 if expected_generation is None else expected_generation + 1)):
                _fail("loss_high_water.rollback")
            try:
                import winreg
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self._KEY, 0,
                                        winreg.KEY_READ | winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, self._value_name(account_id, binding_digest), 0, winreg.REG_SZ,
                                      _canonical(_water_body(account_id, binding_digest, record)).decode("ascii"))
                    winreg.FlushKey(key)
            except OSError:
                _fail("loss_high_water.write")
        finally:
            self._release(lock)


class FileLossStore:
    """One account/binding loss state under an existing, pinned directory."""

    def __init__(self, root: os.PathLike[str] | str, high_water: LossHighWaterPort | None = None):
        try:
            info = os.lstat(root)
        except (OSError, TypeError, ValueError):
            _fail("loss_store.root")
        if not stat.S_ISDIR(info.st_mode):
            _fail("loss_store.root")
        self._root = Path(root)
        self._root_id = (info.st_dev, info.st_ino)
        token = hashlib.sha256(str(self._root.absolute()).encode("utf-8")).hexdigest()[:16]
        self._high_water = high_water or FileLossHighWater(
            self._root.parent / f".tnet02-loss-high-water-{token}.json")

    def _check_root(self) -> None:
        try:
            info = os.lstat(self._root)
        except OSError:
            _fail("loss_store.root")
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self._root_id:
            _fail("loss_store.root")

    def _sync_root(self) -> None:
        """Persist directory-entry changes when this platform exposes directory fsync."""
        directory_flag = getattr(os, "O_DIRECTORY", None)
        if directory_flag is None:  # Windows has no Python directory-fsync primitive.
            return
        try:
            fd = os.open(self._root, os.O_RDONLY | directory_flag)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            _fail("loss_store.durability")

    def _read(self, name: str, code: str) -> tuple[dict, bytes] | None:
        self._check_root()
        path = self._root / name
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return None
        except OSError:
            _fail(code)
        if not stat.S_ISREG(info.st_mode):
            _fail(code)
        try:
            raw = path.read_bytes()
            body = json.loads(raw.decode("ascii"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            _fail(code)
        if type(body) is not dict or _canonical(body) != raw:
            _fail(code)
        return body, raw

    def _create(self, name: str, raw: bytes) -> None:
        self._check_root()
        path = self._root / name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except OSError:
            _fail("loss_store.create")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            _fail("loss_store.write")
        self._sync_root()

    def _replace(self, name: str, raw: bytes) -> None:
        self._check_root()
        path = self._root / name
        try:
            fd, temporary = tempfile.mkstemp(prefix=".tnet02-loss-", suffix=".tmp", dir=self._root)
            os.chmod(temporary, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._sync_root()
        except OSError:
            try:
                os.unlink(temporary)
            except (OSError, UnboundLocalError):
                pass
            _fail("loss_store.write")

    @staticmethod
    def _state(identity: dict, tracker: LS.LossTracker, wallet_balance: Decimal,
               generation: int) -> tuple[dict, bytes]:
        if tracker.account_id != identity["account_id"] or type(wallet_balance) is not Decimal \
                or not wallet_balance.is_finite() or type(generation) is not int or generation < 1:
            _fail("loss_store.state")
        tracker_wire = _tracker_wire(tracker)
        if _tracker_read(tracker_wire, identity["account_id"]) != tracker:
            _fail("loss_store.tracker")
        body = {**identity, "generation": generation, "tracker": tracker_wire,
                "wallet_balance": dec_str(wallet_balance)}
        raw = _canonical(body)
        return body, raw

    @staticmethod
    def _anchor(identity: dict, tracker: LS.LossTracker, raw: bytes, generation: int) -> bytes:
        return _canonical({**identity, "generation": generation, "last_ms": tracker.last_ms,
                           "state_sha256": hashlib.sha256(raw).hexdigest()})

    def _load_full(self, account_id: str, binding_digest: str) -> tuple[LS.LossTracker, Decimal, int] | None:
        expected = _identity(account_id, binding_digest)
        high_water = self._high_water.load(account_id, binding_digest)
        identity = self._read(_IDENTITY, "loss_store.identity")
        state = self._read(_STATE, "loss_store.state")
        anchor = self._read(_ANCHOR, "loss_store.anchor")
        if identity is None and state is None and anchor is None:
            if high_water is not None:
                _fail("loss_store.deleted")
            return None
        if identity is None or state is None or anchor is None or identity[0] != expected or high_water is None:
            _fail("loss_store.identity")
        body, raw = state
        if set(body) != {"schema", "account_id", "binding_digest", "generation", "tracker", "wallet_balance"}:
            _fail("loss_store.state")
        if {key: body[key] for key in expected} != expected:
            _fail("loss_store.identity")
        generation = body["generation"]
        if type(generation) is not int or generation < 1:
            _fail("loss_store.generation")
        tracker = _tracker_read(body["tracker"], account_id)
        wallet = _decimal(body["wallet_balance"], "loss_store.wallet")
        state_sha = hashlib.sha256(raw).hexdigest()
        expected_anchor = json.loads(self._anchor(expected, tracker, raw, generation).decode("ascii"))
        if (anchor[0] != expected_anchor or high_water.generation != generation
                or high_water.state_sha256 != state_sha):
            _fail("loss_store.rollback")
        return tracker, wallet, generation

    def load(self, account_id: str, binding_digest: str) -> tuple[LS.LossTracker, Decimal] | None:
        record = self._load_full(account_id, binding_digest)
        return None if record is None else record[:2]

    def initialize(self, account_id: str, binding_digest: str, tracker: LS.LossTracker,
                   wallet_balance: Decimal) -> None:
        identity = _identity(account_id, binding_digest)
        if (self._high_water.load(account_id, binding_digest) is not None
                or any(self._read(name, "loss_store.exists") is not None for name in (_IDENTITY, _STATE, _ANCHOR))):
            _fail("loss_store.exists")
        generation = 1
        _body, raw = self._state(identity, tracker, wallet_balance, generation)
        # Identity first is intentional: any interruption after this point leaves a durable fail-closed tombstone.
        self._create(_IDENTITY, _canonical(identity))
        self._create(_STATE, raw)
        self._create(_ANCHOR, self._anchor(identity, tracker, raw, generation))
        self._high_water.advance(account_id, binding_digest, None,
                                 LossHighWater(generation, hashlib.sha256(raw).hexdigest()))

    def save(self, account_id: str, binding_digest: str, tracker: LS.LossTracker,
             wallet_balance: Decimal) -> None:
        current = self._load_full(account_id, binding_digest)
        if current is None or tracker.last_ms < current[0].last_ms:
            _fail("loss_store.rollback")
        generation = current[2] + 1
        identity = _identity(account_id, binding_digest)
        _body, raw = self._state(identity, tracker, wallet_balance, generation)
        # State/anchor first, independent high-water last: every interruption leaves a mismatch and therefore HOLDs.
        self._replace(_STATE, raw)
        self._replace(_ANCHOR, self._anchor(identity, tracker, raw, generation))
        self._high_water.advance(account_id, binding_digest, current[2],
                                 LossHighWater(generation, hashlib.sha256(raw).hexdigest()))


__all__ = ["FileLossHighWater", "FileLossStore", "LossHighWater", "LossHighWaterPort", "LossStoreError",
           "WindowsRegistryLossHighWater"]
