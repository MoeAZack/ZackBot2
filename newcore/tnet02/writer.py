"""TNET-02 exact-build report writer: the concrete ``ReportWriter`` for ``newcore.testnet_harness.run_suite``.

It accepts only the exact canonical report bytes for one expected build, writes them once with exclusive create and
fsync into an existing real directory, and never overwrites, follows a symlinked directory or chooses another path.
Errors carry stable codes only.
"""
from __future__ import annotations

import os
import re
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
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        dir_fd = self._open_dir()
        try:
            if dir_fd is not None:
                fd = os.open(name, flags, 0o644, dir_fd=dir_fd)
            else:
                fd = os.open(target, flags, 0o644)
        except FileExistsError:
            self._close_dir(dir_fd)
            raise WriterError("writer.exists") from None
        except OSError:
            self._close_dir(dir_fd)
            raise WriterError("writer.open_failed") from None
        ok = False
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
            ok = True
        except OSError:
            pass
        finally:
            os.close(fd)
            if not ok:
                try:
                    if dir_fd is not None:
                        os.unlink(name, dir_fd=dir_fd)
                    else:
                        os.unlink(target)
                except OSError:
                    pass
            self._close_dir(dir_fd)
        if not ok:
            raise WriterError("writer.write_failed")
        self._path = target

    def _open_dir(self) -> int | None:
        """Re-check the directory identity at write time; pin it with a no-follow dir fd where the OS allows."""
        try:
            info = os.lstat(self._out_dir)
        except OSError:
            raise WriterError("writer.out_dir") from None
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self._dir_id:
            raise WriterError("writer.out_dir")
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
