"""Cross-volume-safe path helpers for the research runners (WINPATH-01, issue #345).

On Windows, os.path.commonpath / os.path.relpath raise ValueError when the two paths sit on different drives (for
example a checkout on F: and the pytest temp root on C:). These helpers give that case an explicit meaning instead of
letting the ValueError escape before the caller's authority / reserved-name checks run:

* is_within(path, root): containment. Paths on different plain drive letters are NOT contained (returns False); a
  mismatch involving a long-path ('//?/'), device ('//./') or UNC spelling (which can alias the same drive) re-raises the ValueError, and
  the reserved-directory callers turn that into a refusal. So a reserved
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


def _plain_drive(v: str) -> bool:
    """'c:' style only. Long-path, device and UNC spellings can alias a drive ('//?/F:/x' names F:/x), so they never count as
    proof that two paths sit on different volumes."""
    return v == '' or (len(v) == 2 and v[1] == ':' and v[0].isalpha())


def is_within(path: str, root: str, *, pathmod=os.path) -> bool:
    """True iff abspath(path) is root or lies under it. Different drives -> False (not containment)."""
    ap, ar = pathmod.abspath(path), pathmod.abspath(root)
    try:
        common = pathmod.commonpath([ap, ar])
    except ValueError:                      # different drives (Windows); both are absolute, so no other cause
        va, vr = _volume(ap, pathmod), _volume(ar, pathmod)
        if va != vr and _plain_drive(va) and _plain_drive(vr):
            return False
        raise                               # alias / UNC / unexplained: callers must refuse (fail closed)
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
