"""Codex HIGH-2 (PR #43) carried into the NC-02b members: rule 1 wins over damage in snapshots and envelopes too.

A snapshot whose binding record is a complete-length bad-CRC frame but whose NC-01 snapshot record (CRC-valid) carries a
future schema is a FUTURE member (boot ABORT_RO, zero writes), never damage. Likewise a damaged envelope header followed
by a CRC-valid future header record.
"""
import os

import pytest

from nc02b_helpers import ACCT, MEM_BASE, T, account
from newcore.store import Outcome, open_store
from newcore.store.envelope import peek_version
from newcore.store.frame import (FILE_HEADER, KIND_EVIDENCE, REC, RT_BINDING, RT_HEADER, RT_SNAPSHOT, file_header,
                                 frame, resync_records)
from newcore.store.header import VersionVerdict, canonical_json
from newcore.store.records import Reader
from newcore.store.snapfile import SnapDamage, SnapFuture, decode_snapshot, peek
from newcore.store.store import Paths
from nc02b_helpers import CIPHER
from test_nc02b_store import managed_with_lot

P = Paths(MEM_BASE, ACCT)


def _bad_crc(raw, r):
    b = bytearray(raw)
    b[r.offset + REC.size - 1] ^= 0xFF
    return bytes(b)


def _current(fs):
    names = sorted(n for n in fs.listdir(P.snap) if n.endswith('.snap'))
    p = os.path.join(P.snap, names[-1])
    return p, fs.read_bytes(p)


def _future_behind_damage(raw):
    recs = {r.rtype: r for r in resync_records(raw)}
    snap = recs[RT_SNAPSHOT]
    payload = snap.payload.replace(b'"schema_version":1', b'"schema_version":2', 1)
    assert payload != snap.payload
    raw = raw[:snap.offset] + frame(RT_SNAPSHOT, payload) + raw[snap.end:]
    return _bad_crc(raw, {r.rtype: r for r in resync_records(raw)}[RT_BINDING])


def test_a_future_snapshot_record_behind_a_bad_frame_is_future_not_damage():
    fs, ex, _ = managed_with_lot()
    _, raw = _current(fs)
    bad = _future_behind_damage(raw)
    with pytest.raises(SnapFuture):
        decode_snapshot(bad, Reader(), account_id=ACCT)
    control = _bad_crc(raw, {r.rtype: r for r in resync_records(raw)}[RT_BINDING])
    with pytest.raises(SnapDamage):                               # damage alone stays damage
        decode_snapshot(control, Reader(), account_id=ACCT)


def test_boot_on_such_a_snapshot_is_abort_ro_with_zero_writes():
    fs, ex, _ = managed_with_lot()
    p, raw = _current(fs)
    fs.files.pop(os.path.normpath(p))
    fs.put(p, _future_behind_damage(raw))
    before = {k: v for k, v in fs.snapshot().items() if 'incidents' not in k}
    r = open_store(MEM_BASE, account(), exchange=ex, now_ms=T, fs=fs, cipher=CIPHER)
    assert r.outcome is Outcome.ABORT_RO, (r.outcome, r.items, r.findings)
    assert {k: v for k, v in fs.snapshot().items() if 'incidents' not in k} == before


def test_peek_sees_a_future_header_behind_a_damaged_header():
    fs, ex, _ = managed_with_lot()
    _, raw = _current(fs)
    hdr = {r.rtype: r for r in resync_records(raw)}[RT_HEADER]
    import json
    doc = json.loads(hdr.payload)
    doc['format_version'] = 99
    doc['min_reader_version'] = 99
    bad = _bad_crc(raw, hdr) + frame(RT_HEADER, canonical_json(doc))
    assert peek(bad, Reader()) == 'future'


def test_an_envelope_with_a_damaged_header_and_a_future_one_behind_is_future():
    meta = {'format': 'x', 'format_version': 99, 'min_reader_version': 99}
    good = frame(RT_HEADER, canonical_json({'format': 'x', 'format_version': 1, 'min_reader_version': 1}))
    b = bytearray(good)
    b[REC.size - 1] ^= 0xFF
    raw = file_header(KIND_EVIDENCE) + bytes(b) + frame(RT_HEADER, canonical_json(meta))
    assert raw[:FILE_HEADER.size] == file_header(KIND_EVIDENCE)
    assert peek_version(raw) is VersionVerdict.FUTURE
