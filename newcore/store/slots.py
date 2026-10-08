"""Dual-slot commit files: HEAD.a / HEAD.b (the ONLY commit point, design 3.5 / D1) and the anchor slots outside data\\
(design 3.6 / D2).

A slot file has a FIXED size (SLOT_SIZE) and is rewritten in place (open_slot + write_at 0 + fsync): no rename, no
truncate, no temp file. Layout: file header (kind) | record rtype 20: the canonical JSON document | record rtype 255:
{"sha256": sha256 of every preceding byte} | NUL padding up to SLOT_SIZE. One fsync commits.

The writer always overwrites the slot that does NOT hold the current document (the older or the invalid one), so a crash
mid-write leaves at most one invalid slot next to the previous valid one: that is the expected crash state (crash_matrix
C-S6, proposed M54), never damage. Reading a pair:
  - any slot with an unknown frame version / a future or non-integer format_version / min_reader_version: FUTURE
    (rule 1, ABORT_RO), whatever the other slot holds;
  - both valid: the higher commit_seq; equal commit_seq with different bytes is DAMAGE;
  - one valid: that one (the other may be torn or absent);
  - none valid: ABSENT when both files are missing, else DAMAGE (both invalid / one invalid and one missing).
A non-file at a slot path or an I/O error is UNREADABLE (rule 3).
"""
from __future__ import annotations

import enum
import hashlib
import os
from dataclasses import dataclass

from .frame import FILE_HEADER, RT_END, RT_SLOT, file_header, frame, record_at
from .header import HeaderError, VersionVerdict, canonical_json, check_version, strict_json

SLOT_SIZE = 64 * 1024


class SlotState(enum.StrEnum):
    VALID = 'valid'
    ABSENT = 'absent'
    INVALID = 'invalid'            # torn or damaged (indistinguishable for one slot)
    FUTURE = 'future'
    UNREADABLE = 'unreadable'


class PairState(enum.StrEnum):
    OK = 'ok'
    ABSENT = 'absent'
    FUTURE = 'future'
    DAMAGE = 'damage'
    UNREADABLE = 'unreadable'


def encode_slot(kind, doc):
    body = file_header(kind) + frame(RT_SLOT, canonical_json(doc))
    data = body + frame(RT_END, canonical_json({'sha256': hashlib.sha256(body).hexdigest()}))
    if len(data) > SLOT_SIZE:
        raise ValueError(f'slot document of {len(data)} bytes exceeds {SLOT_SIZE}')
    return data + b'\0' * (SLOT_SIZE - len(data))


def decode_slot(raw, kind):
    """(SlotState, document or None)."""
    raw = bytes(raw)
    if len(raw) >= FILE_HEADER.size and raw[:4] == b'ZBNC' and raw[4] == kind and (raw[5] != 1 or raw[6:8] != b'\0\0'):
        return SlotState.FUTURE, None
    if len(raw) != SLOT_SIZE or raw[:FILE_HEADER.size] != file_header(kind):
        return SlotState.INVALID, None
    r1 = record_at(raw, FILE_HEADER.size)
    if r1 is None or r1.rtype != RT_SLOT:
        return SlotState.INVALID, None
    try:
        doc, problems = strict_json(r1.payload)
    except HeaderError:
        return SlotState.INVALID, None
    v = check_version(doc)
    if v is not VersionVerdict.OK:
        return SlotState.FUTURE, None
    r2 = record_at(raw, r1.end)
    if r2 is None or r2.rtype != RT_END or raw.count(0, r2.end) != SLOT_SIZE - r2.end:
        return SlotState.INVALID, None
    try:
        end, p2 = strict_json(r2.payload)
    except HeaderError:
        return SlotState.INVALID, None
    if problems or p2 or end != {'sha256': hashlib.sha256(raw[:r1.end]).hexdigest()} or type(doc) is not dict:
        return SlotState.INVALID, None
    return SlotState.VALID, doc


@dataclass(frozen=True, slots=True)
class PairRead:
    state: PairState
    doc: dict | None
    current: str | None           # 'a' / 'b': the slot holding doc
    states: tuple                 # (state of a, state of b)
    detail: str = ''

    @property
    def write_next(self):
        """The slot the next commit overwrites (never the current one)."""
        return 'b' if self.current == 'a' else 'a'


def slot_path(directory, stem, which):
    return os.path.join(directory, f'{stem}.{which}')


def read_pair(fs, directory, stem, kind, validate=None):
    """Read and choose between <stem>.a and <stem>.b. `validate(doc)` raises ValueError for a structurally invalid
    document (it then counts as an invalid slot). Read-only."""
    states, docs, raws = [], [], []
    for which in ('a', 'b'):
        p = slot_path(directory, stem, which)
        try:
            k = fs.kind(p)
            if k == 'missing':
                states.append(SlotState.ABSENT)
                docs.append(None)
                raws.append(None)
                continue
            if k != 'file':
                states.append(SlotState.UNREADABLE)
                docs.append(None)
                raws.append(None)
                continue
            raw = fs.read_bytes(p)
        except OSError:
            states.append(SlotState.UNREADABLE)
            docs.append(None)
            raws.append(None)
            continue
        st, doc = decode_slot(raw, kind)
        if st is SlotState.VALID and validate is not None:
            try:
                validate(doc)
            except ValueError:
                st, doc = SlotState.INVALID, None
        states.append(st)
        docs.append(doc)
        raws.append(raw)
    states = tuple(states)
    if SlotState.FUTURE in states:
        return PairRead(PairState.FUTURE, None, None, states, 'a slot has a future / unknown format')
    if SlotState.UNREADABLE in states:
        return PairRead(PairState.UNREADABLE, None, None, states, 'a slot is unreadable or not a file')
    valid = [(docs[i]['commit_seq'], 'ab'[i], docs[i], raws[i]) for i in (0, 1) if states[i] is SlotState.VALID]
    if len(valid) == 2 and valid[0][0] == valid[1][0]:
        if valid[0][3] != valid[1][3]:
            return PairRead(PairState.DAMAGE, None, None, states, 'two different documents with one commit_seq')
        return PairRead(PairState.OK, valid[0][2], 'a', states)
    if valid:
        seq, which, doc, _ = max(valid, key=lambda v: v[0])
        return PairRead(PairState.OK, doc, which, states)
    if states == (SlotState.ABSENT, SlotState.ABSENT):
        return PairRead(PairState.ABSENT, None, None, states)
    if sorted(states) == sorted((SlotState.INVALID, SlotState.ABSENT)):
        # the very first write of the pair was cut short (C-S6 / C-S8 on a first commit): never committed, so the
        # pair is ABSENT; the caller decides (interrupted INIT, or a HEAD lost while generations exist = damage)
        return PairRead(PairState.ABSENT, None, None, states, 'torn first write')
    return PairRead(PairState.DAMAGE, None, None, states, 'no valid slot')


def write_slot(fs, directory, stem, which, kind, doc):
    """Durably write `doc` into <stem>.<which>: create (O_EXCL + fsync + dir flush) or rewrite in place + fsync.
    Raises OSError."""
    data = encode_slot(kind, doc)
    p = slot_path(directory, stem, which)
    if fs.kind(p) == 'missing':
        h = fs.open_new(p)
        try:
            fs.write(h, data)
            fs.fsync(h)
        finally:
            fs.close(h)
        fs.fsync_dir(directory)
        return
    h = fs.open_slot(p)
    try:
        fs.write_at(h, data, 0)
        fs.fsync(h)
    finally:
        fs.close(h)
