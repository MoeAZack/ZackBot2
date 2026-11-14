"""Cross-volume-safe path helpers for the research runners (WINPATH-01, issue #345).

On Windows, os.path.commonpath / os.path.relpath raise ValueError when the two paths sit on different drives (for
example a checkout on F: and the pytest temp root on C:). These helpers give that case an explicit meaning instead of
letting the ValueError escape before the caller's authority / reserved-name checks run:

* is_within(path, root): containment. Paths on different drives are NOT contained (returns False), so a reserved
  directory on one drive never authorizes or denies anything on another; every other check the caller makes still
  runs. The comparison is case-normalized (os.path.normcase), so a lower-case drive letter or folder spelling on
  Windows cannot step around a reserved-directory denial. Only ValueError from commonpath is handled; no other
  exception is turned into an answer.
* path_identity(path, root): the identity string for an input file. Same volume: exactly the previous bytes,
  relpath(abspath(path), root) with '/' separators (unchanged, including '..' for files outside root). Different
  volume: the absolute path with '/' separators (drive-qualified, e.g. 'C:/Users/x/rules.json'), the same convention
  tools/research/pit.py _code_path already uses for files outside the run checkout. A relpath never carries a drive,
  so the two forms cannot collide. The file hash in the identity is unaffected.

`pathmod` defaults to os.path; tests pass ntpath to exercise the Windows drive semantics on any OS.
"""
from __future__ import annotations

import os


def _volume(p: str, pathmod) -> str:
    """The drive / UNC share of an absolute path, case-folded ('' on POSIX). Tests may substitute this."""
    return pathmod.splitdrive(p)[0].lower()


def is_within(path: str, root: str, *, pathmod=os.path) -> bool:
    """True iff abspath(path) is root or lies under it. Different drives -> False (not containment)."""
    ap, ar = pathmod.abspath(path), pathmod.abspath(root)
    try:
        common = pathmod.commonpath([ap, ar])
    except ValueError:                      # different drives (Windows); both are absolute, so no other cause
        if _volume(ap, pathmod) != _volume(ar, pathmod):
            return False
        raise
    return pathmod.normcase(common) == pathmod.normcase(ar)


def path_identity(path: str, root: str, *, pathmod=os.path) -> str:
    """Same volume: relpath(abspath(path), root) with '/' (unchanged bytes). Different drive: absolute '/' path."""
    ap = pathmod.abspath(path)
    try:
        rel = pathmod.relpath(ap, root)
    except ValueError:
        if _volume(ap, pathmod) != _volume(pathmod.abspath(root), pathmod):
            return ap.replace(pathmod.sep, '/')
        raise
    return rel.replace(pathmod.sep, '/')
