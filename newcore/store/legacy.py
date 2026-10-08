"""Rule 0: REJECT legacy files (A14, design P1, decision D15).

Legacy state (state.json, settings.json, install.json and their .bak / .corrupt-* / *.tmp companions) is never read as
state, settings or binding, never written, renamed, moved or copied into the evidence envelope. It is only reported once,
by hash and metadata, through the out-of-band boot incident log. Reading the bytes to hash them is the only access.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass

LEGACY_RE = re.compile(r'(state|settings|install)\.json(\.bak|\.corrupt-[^/\\]*|\.[^/\\]*\.tmp)?')


@dataclass(frozen=True)
class LegacyRef:
    name: str
    status: str                 # 'file' | 'non-file' | 'unreadable'
    sha256: str | None
    size: int | None
    mtime_ms: int | None

    def doc(self):
        return {'name': self.name, 'status': self.status, 'sha256': self.sha256, 'size': self.size,
                'mtime_ms': self.mtime_ms}


def scan_legacy(fs, data_dir):
    """Read-only. Returns the legacy members found directly in data_dir, sorted by name."""
    try:
        if fs.kind(data_dir) != 'dir':
            return ()
        names = fs.listdir(data_dir)
    except OSError:
        return ()
    out = []
    for n in names:
        if LEGACY_RE.fullmatch(n) is None:
            continue
        p = os.path.join(data_dir, n)
        try:
            k = fs.kind(p)
            if k != 'file':
                out.append(LegacyRef(n, 'non-file', None, None, None))
                continue
            size, mtime_ns = fs.stat(p)
            data = fs.read_bytes(p)
            out.append(LegacyRef(n, 'file', hashlib.sha256(data).hexdigest(), size, mtime_ns // 1_000_000))
        except OSError:
            out.append(LegacyRef(n, 'unreadable', None, None, None))
    return tuple(out)
