"""TNET-02 exact-build report writer: the concrete ``ReportWriter`` for ``newcore.testnet_harness.run_suite``.

It accepts only the exact canonical report bytes for one expected build, fsyncs a private same-directory file, then
publishes it atomically through an exclusive hard link and fsyncs the directory.  It never overwrites, follows a
symlinked directory or exposes a partial final report.  Errors carry stable codes only.
"""
from __future__ import annotations

import os
import re
import secrets
import stat
from pathlib import Path

from .gate import GateError, decode_report


_SHA = re.compile(r"[0-9a-f]{40}")


class WriterError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def report_filename(build_sha: str, report_sha256: str) -> str:
    return f"tnet01-{build_sha}-{report_sha256[:16]}.json"


class ExactBuildReportWriter:
    """Callable[[bytes], None]; one instance writes at most one report."""

    __slots__ = ("_out_dir", "_dir_id", "_build_sha", "_path")

    def __init__(self, out_dir: os.PathLike[str] | str, build_sha: str):
        if type(build_sha) is not str or _SHA.fullmatch(build_sha) is None:
            raise WriterError("writer.build_sha")
        try:
            info = os.lstat(out_dir)
        except (OSError, TypeError, ValueError):
            raise WriterError("writer.out_dir") from None
        if not stat.S_ISDIR(info.st_mode):
            raise WriterError("writer.out_dir")
        self._out_dir = Path(out_dir)
        self._dir_id = (info.st_dev, info.st_ino)
        self._build_sha = build_sha
        self._path: Path | None = None

    @property
    def path(self) -> Path | None:
        return self._path

    def __call__(self, data: bytes) -> None:
        if self._path is not None:
            raise WriterError("writer.already_written")
        try:
            report = decode_report(data)
        except GateError:
            raise WriterError("writer.report_invalid") from None
        if report.build_sha != self._build_sha:
            raise WriterError("writer.build_mismatch")
        name = report_filename(report.build_sha, report.report_sha256)
        target = self._out_dir / name
        temp_name = f".{name}.{secrets.token_hex(8)}.tmp"
        temp = self._out_dir / temp_name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        dir_fd = self._open_dir()
        try:
            if dir_fd is not None:
                fd = os.open(temp_name, flags, 0o644, dir_fd=dir_fd)
            else:
                fd = os.open(temp, flags, 0o644)
        except OSError:
            self._close_dir(dir_fd)
            raise WriterError("writer.open_failed") from None
        written_ok = False
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short write")
                view = view[written:]
            os.fsync(fd)
            written_ok = True
        except OSError:
            pass
        finally:
            os.close(fd)
        if not written_ok:
            self._unlink(temp_name, temp, dir_fd)
            self._close_dir(dir_fd)
            raise WriterError("writer.write_failed")

        # A same-directory hard link is an atomic, no-overwrite publication.  The final name cannot expose partial
        # bytes: it appears only after the complete temporary inode has been fsynced.  A crash before this point
        # leaves at worst an unreferenced temporary name, so a fresh writer can retry.
        try:
            if dir_fd is not None:
                os.link(temp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
            else:
                # Re-check immediately before the path-based Windows operation, closing the directory-swap window
                # as far as that platform permits without dir-fd APIs.
                self._verify_dir_identity()
                os.link(temp, target, follow_symlinks=False)
        except FileExistsError:
            self._unlink(temp_name, temp, dir_fd)
            self._close_dir(dir_fd)
            raise WriterError("writer.exists") from None
        except OSError:
            self._unlink(temp_name, temp, dir_fd)
            self._close_dir(dir_fd)
            raise WriterError("writer.publish_failed") from None
        self._unlink(temp_name, temp, dir_fd)
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            except OSError:
                self._close_dir(dir_fd)
                raise WriterError("writer.sync_failed") from None
        self._close_dir(dir_fd)
        self._path = target

    @staticmethod
    def _unlink(name: str, path: Path, dir_fd: int | None) -> None:
        try:
            if dir_fd is not None:
                os.unlink(name, dir_fd=dir_fd)
            else:
                os.unlink(path)
        except OSError:
            pass

    def _verify_dir_identity(self) -> None:
        try:
            info = os.lstat(self._out_dir)
        except OSError:
            raise WriterError("writer.out_dir") from None
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self._dir_id:
            raise WriterError("writer.out_dir")

    def _open_dir(self) -> int | None:
        """Re-check the directory identity at write time; pin it with a no-follow dir fd where the OS allows."""
        self._verify_dir_identity()
        if os.open not in os.supports_dir_fd or not hasattr(os, "O_DIRECTORY"):
            return None  # Windows: identity re-check above is the guard
        try:
            fd = os.open(self._out_dir, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            raise WriterError("writer.out_dir") from None
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != self._dir_id:
            os.close(fd)
            raise WriterError("writer.out_dir")
        return fd

    @staticmethod
    def _close_dir(fd: int | None) -> None:
        if fd is not None:
            os.close(fd)


__all__ = ["ExactBuildReportWriter", "WriterError", "report_filename"]
