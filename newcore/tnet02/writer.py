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

    __slots__ = ("_out_dir", "_dir_id", "_build_sha", "_path", "_guard_fd")

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
        self._guard_fd: int | None = None

    def __del__(self):
        guard = getattr(self, "_guard_fd", None)
        if guard is not None:
            try:
                os.close(guard)
            except OSError:
                pass

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
        temp_info = None
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short write")
                view = view[written:]
            os.fsync(fd)
            temp_info = os.fstat(fd)
            written_ok = True
        except OSError:
            pass
        if not written_ok:
            os.close(fd)
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
            os.close(fd)
            self._unlink(temp_name, temp, dir_fd)
            self._close_dir(dir_fd)
            raise WriterError("writer.exists") from None
        except OSError:
            self._unlink_owned(name, target, dir_fd, temp_info)
            os.close(fd)
            self._unlink(temp_name, temp, dir_fd)
            self._close_dir(dir_fd)
            raise WriterError("writer.publish_failed") from None
        guard_fd = None
        try:
            guard_fd = self._verify_published(name, target, dir_fd, temp_info, data)
        except (OSError, WriterError):
            os.close(fd)
            self._unlink_owned(name, target, dir_fd, temp_info)
            self._unlink(temp_name, temp, dir_fd)
            self._close_dir(dir_fd)
            raise WriterError("writer.publish_verify") from None
        os.close(fd)
        self._unlink(temp_name, temp, dir_fd)
        self._publication_hook("post_temp_unlink")
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            except OSError:
                os.close(guard_fd)
                self._unlink_owned(name, target, dir_fd, temp_info)
                self._close_dir(dir_fd)
                raise WriterError("writer.sync_failed") from None
        try:
            guard_fd = self._verify_published(name, target, dir_fd, temp_info, data, guard_fd)
            self._publication_hook("post_final_verify")
            guard_fd = self._verify_published(name, target, dir_fd, temp_info, data, guard_fd)
            self._publication_hook("pre_return")
            guard_fd = self._verify_published(name, target, dir_fd, temp_info, data, guard_fd)
        except (OSError, WriterError):
            os.close(guard_fd)
            self._unlink_owned(name, target, dir_fd, temp_info)
            self._close_dir(dir_fd)
            raise WriterError("writer.publish_verify") from None
        self._close_dir(dir_fd)
        self._guard_fd = guard_fd
        self._path = target

    def _verify_published(self, name: str, target: Path, dir_fd: int | None, temp_info, data: bytes,
                          guard_fd: int | None = None) -> int:
        """Prove the final name is the fsynced temporary inode and contains exactly the accepted bytes.

        The handle-less Windows hard-link API can race a directory replacement.  Identity checks on both sides of
        an exact handle read, plus inode equality with the still-open temporary file, turn that race into a refusal.
        """
        opened_here = guard_fd is None
        try:
            if dir_fd is None:
                self._verify_dir_identity()
                path_info = os.lstat(target)
                if opened_here:
                    guard_fd = os.open(target, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                                       | getattr(os, "O_NOFOLLOW", 0))
            else:
                path_info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                if opened_here:
                    guard_fd = os.open(name, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                                       | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
            final_info = os.fstat(guard_fd)
            identity = (temp_info.st_dev, temp_info.st_ino)
            if (not stat.S_ISREG(path_info.st_mode) or not stat.S_ISREG(final_info.st_mode)
                    or (path_info.st_dev, path_info.st_ino) != identity
                    or (final_info.st_dev, final_info.st_ino) != identity
                    or path_info.st_size != len(data) or final_info.st_size != len(data)):
                raise WriterError("writer.publish_verify")
            chunks = []
            os.lseek(guard_fd, 0, os.SEEK_SET)
            remaining = len(data) + 1
            while remaining:
                chunk = os.read(guard_fd, min(remaining, 1024 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            if b"".join(chunks) != data:
                raise WriterError("writer.publish_verify")
            if dir_fd is None:
                self._verify_dir_identity()
                after = os.lstat(target)
            else:
                after = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            if (not stat.S_ISREG(after.st_mode)
                    or (after.st_dev, after.st_ino) != (temp_info.st_dev, temp_info.st_ino)
                    or after.st_size != len(data)):
                raise WriterError("writer.publish_verify")
            return guard_fd
        except Exception:
            if opened_here and guard_fd is not None:
                os.close(guard_fd)
            raise

    def _unlink_owned(self, name: str, path: Path, dir_fd: int | None, owned_info) -> None:
        """Remove a claimed final name only while it still identifies the inode created by this writer."""
        try:
            info = (os.stat(name, dir_fd=dir_fd, follow_symlinks=False) if dir_fd is not None
                    else os.lstat(path))
            if (info.st_dev, info.st_ino) != (owned_info.st_dev, owned_info.st_ino):
                return
            self._unlink(name, path, dir_fd)
        except OSError:
            pass

    @staticmethod
    def _publication_hook(stage: str) -> None:
        """Test seam for deterministic namespace races; production intentionally does nothing."""

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
