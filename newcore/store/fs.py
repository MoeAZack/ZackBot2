"""The file-system seam (nc02_design.md section 1): every byte the store reads or writes goes through one FsSeam object.

This is the ONLY module in newcore.store that touches the OS (an AST test enforces it). Tests hand the store a fault
seam instead (tests/newcore_store/nc02a_memfs.py) to inject ENOSPC / EROFS / read errors and to crash at every write or
fsync boundary.

NC-02a uses a subset of the design seam: no open_slot / truncate (no HEAD slots yet), no ACL and no unlink (evidence
envelope is NC-02b). There is no rename or replace at all: segments and evidence files are created with O_EXCL and only
ever appended to.
"""
from __future__ import annotations

import os
import stat
import sys
from typing import Protocol

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class FsSeam(Protocol):
    """Paths are absolute strings. Every method may raise OSError."""

    def kind(self, p) -> str:
        """'file' | 'dir' | 'missing' | 'other' (symlink, junction / reparse point, device...). Never follows links."""
        ...

    def listdir(self, p) -> list:
        ...

    def read_bytes(self, p) -> bytes:
        ...

    def mkdir(self, p) -> None:
        ...

    def open_new(self, p):
        """Create a new file for appending (O_CREAT | O_EXCL, binary). Never truncates an existing file."""
        ...

    def open_append(self, p):
        """Open an existing file for appending (binary)."""
        ...

    def write(self, h, data: bytes) -> None:
        """Write all of `data` at the end of the file, or raise."""
        ...

    def fsync(self, h) -> None:
        ...

    def fsync_dir(self, p) -> None:
        """Make the directory entries of p durable (Windows: FlushFileBuffers on a backup-semantics handle)."""
        ...

    def close(self, h) -> None:
        ...

    def mark(self, label: str) -> None:
        """No-op crash-point label (crash_matrix.md names); the fault seam records it."""
        ...

    def size(self, p) -> int:
        """Current size of the file at p (read-only; the writer's fence before each append)."""
        ...

    def lock_exclusive(self, p):
        """Create p if missing and take a non-blocking exclusive OS lock on it for this handle's lifetime. Returns the
        lock handle, or None when another handle (this process or another) holds it. Raises OSError on I/O failure."""
        ...

    def unlock(self, lock) -> None:
        ...


class _Handle:
    __slots__ = ('fd', 'path')

    def __init__(self, fd, path):
        self.fd, self.path = fd, path


_BINARY = getattr(os, 'O_BINARY', 0)


class RealFs:
    """The production seam over the local file system."""

    def __init__(self):
        self.dir_fsync_supported = True      # False once the platform refused a directory flush (recorded, not fatal)

    def kind(self, p):
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            return 'missing'
        if getattr(st, 'st_file_attributes', 0) & FILE_ATTRIBUTE_REPARSE_POINT:
            return 'other'
        if stat.S_ISREG(st.st_mode):
            return 'file'
        if stat.S_ISDIR(st.st_mode):
            return 'dir'
        return 'other'

    def listdir(self, p):
        return sorted(os.listdir(p))

    def read_bytes(self, p):
        fd = os.open(p, os.O_RDONLY | _BINARY)
        try:
            chunks = []
            while True:
                b = os.read(fd, 1 << 20)
                if not b:
                    return b''.join(chunks)
                chunks.append(b)
        finally:
            os.close(fd)

    def mkdir(self, p):
        os.mkdir(p)

    def open_new(self, p):
        return _Handle(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | _BINARY, 0o600), p)

    def open_append(self, p):
        return _Handle(os.open(p, os.O_WRONLY | os.O_APPEND | _BINARY), p)

    def write(self, h, data):
        view = memoryview(data)
        while view:
            n = os.write(h.fd, view)
            if n <= 0:
                raise OSError(5, 'short write')
            view = view[n:]

    def fsync(self, h):
        os.fsync(h.fd)

    def fsync_dir(self, p):
        if sys.platform == 'win32':
            if self.dir_fsync_supported and not _flush_dir_windows(p):
                self.dir_fsync_supported = False
            return
        fd = os.open(p, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def close(self, h):
        os.close(h.fd)

    def mark(self, label):
        pass

    def size(self, p):
        return os.lstat(p).st_size

    def lock_exclusive(self, p):
        fd = os.open(p, os.O_RDWR | os.O_CREAT | _BINARY, 0o600)
        try:
            if sys.platform == 'win32':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return None
        return _Handle(fd, p)

    def unlock(self, lock):
        try:
            if sys.platform == 'win32':
                import msvcrt
                os.lseek(lock.fd, 0, os.SEEK_SET)
                msvcrt.locking(lock.fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fd, fcntl.LOCK_UN)
        finally:
            os.close(lock.fd)


def _flush_dir_windows(p):
    """CreateFileW(GENERIC_WRITE, FILE_FLAG_BACKUP_SEMANTICS) + FlushFileBuffers on the directory (nc02_design 5.4).
    Measured 2026-10-08 on this NTFS volume: succeeds with GENERIC_WRITE (GENERIC_READ is refused with error 5).
    Returns False when the platform refuses the flush (ERROR_ACCESS_DENIED / ERROR_INVALID_FUNCTION): recorded as a
    capability, not a failure. Any other error raises OSError (a store fault)."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    k32.FlushFileBuffers.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    refused = (1, 5)
    h = k32.CreateFileW(p, 0x40000000, 7, None, 3, 0x02000000, None)
    if h is None or h == wintypes.HANDLE(-1).value:
        err = ctypes.get_last_error()
        if err in refused:
            return False
        raise ctypes.WinError(err)
    try:
        if not k32.FlushFileBuffers(h):
            err = ctypes.get_last_error()
            if err in refused:
                return False
            raise ctypes.WinError(err)
    finally:
        k32.CloseHandle(h)
    return True
