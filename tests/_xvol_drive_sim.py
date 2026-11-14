"""WINPATH-01 (#345): simulate a Windows two-drive layout on any OS (not a test module; imported by tests).

Inside `two_drives(c_root)`, every path under `c_root` behaves as if it sat on drive C: and every other path as if it
sat on drive F: (where the checkout lives). os.path.commonpath / os.path.relpath then raise the same ValueError that
ntpath raises for mixed drives, and xvol_paths._volume reports the simulated drive. This reproduces the 22 Windows
failures of #345 on Linux against unpatched master when the original tests are run with
`-p _xvol_drive_sim` (see pytest_runtest_call below) and a C: root equal to the pytest temp root.
"""
from __future__ import annotations

import contextlib
import os
import posixpath
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools', 'research'))


def _drive(p: str, c_root: str) -> str:
    ap = posixpath.abspath(p)
    return 'c:' if ap == c_root or ap.startswith(c_root + '/') else 'f:'


@contextlib.contextmanager
def two_drives(c_root: str):
    import xvol_paths as XP
    c_root = posixpath.realpath(c_root)
    real_common, real_rel, real_vol = posixpath.commonpath, posixpath.relpath, XP._volume

    def commonpath(paths):
        paths = list(paths)
        if len({_drive(p, c_root) for p in paths}) > 1:
            raise ValueError("Paths don't have the same drive")
        return real_common(paths)

    def relpath(path, start=None):
        if _drive(path, c_root) != _drive(start or os.curdir, c_root):
            raise ValueError(f'path is on mount {_drive(path, c_root)!r}, start on mount {_drive(start or ".", c_root)!r}')
        return real_rel(path, start) if start is not None else real_rel(path)

    def volume(p, pathmod):
        return _drive(p, c_root) if pathmod is posixpath else real_vol(p, pathmod)

    posixpath.commonpath, posixpath.relpath, XP._volume = commonpath, relpath, volume
    try:
        yield c_root
    finally:
        posixpath.commonpath, posixpath.relpath, XP._volume = real_common, real_rel, real_vol


# Optional pytest plugin: `python -m pytest -p _xvol_drive_sim ...` (with tests/ on sys.path, e.g. via
# `-p tests._xvol_drive_sim` from the repo root) runs every test body with the system temp root on simulated C:.
import pytest  # noqa: E402


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    with two_drives(tempfile.gettempdir()):
        yield
