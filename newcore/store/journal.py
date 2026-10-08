"""FileJournal: the NC-02a durable JournalPort of one account aggregate.

Layout (inside the account directory the caller owns; NC-02b adds snap\\, HEAD, anchors):

    <account_dir>/journal/seg-000001.seg     append-only segment: file header + record 0 (version header) + events
    <account_dir>/journal/seg-000002.seg     opened only when a torn tail / interrupted create is sealed (D3)
    <account_dir>/evidence/torn-*.ev         copies of torn bytes, written BEFORE the segment that seals them

Every event record is frame(RT_EVENT, NC-01 canonical_bytes(event)): length + CRC-32 + the exact canonical bytes, so
the digest the grammar and the ledger use is the sha256 of the stored payload.

Durability: `append` returns APPLY only after write + fsync of the segment. A segment is created with O_EXCL, its
header fsynced, then its directory flushed, before any event goes into it. A failed write / fsync raises
DurabilityUnavailable and poisons the journal for the rest of the process (see hold.py for the Runner contract).
Idempotency: an identical re-append (same event id, sequence and canonical bytes) is ALREADY_APPLIED and writes
nothing; any other event at a used sequence, or a reused event id with other bytes, raises SequenceConflict.
Single writer: one process per account (AUD-00 single instance; NC-02b's run lock). Not thread-safe.
"""
from __future__ import annotations

import os
import re

from newcore.domain import canonical_bytes, decode_result
from newcore.domain.codec import Outcome
from newcore.domain.events import EVENT_TYPES
from newcore.ports.journal import Admission, JournalConflict

from .errors import DurabilityUnavailable, JournalExists
from .fold import Folder
from .frame import KIND_SEGMENT, MAX_RECORD, RT_EVENT, RT_HEADER, file_header, frame
from .fs import RealFs
from .header import ID_RE, SegmentHeader, encode_header

JOURNAL_DIR = 'journal'
EVIDENCE_DIR = 'evidence'
SEG_RE = re.compile(r'seg-([0-9]{6})\.seg')
WRITER_BUILD = 'nc-02a'


def seg_name(n):
    return f'seg-{n:06d}.seg'


def _close_quiet(fs, h):
    try:
        fs.close(h)
    except OSError:
        pass


def write_segment(fs, journal_dir, hdr):
    """Create segment hdr.segment_no durably: O_EXCL create, header, fsync, close, directory flush."""
    path = os.path.join(journal_dir, seg_name(hdr.segment_no))
    data = file_header(KIND_SEGMENT) + frame(RT_HEADER, encode_header(hdr))
    h = fs.open_new(path)
    try:
        fs.write(h, data)
        fs.fsync(h)
    finally:
        _close_quiet(fs, h)
    fs.fsync_dir(journal_dir)
    return path


class FileJournal:
    """Use create_journal (new) or recover_journal (existing); never construct directly."""

    def __init__(self, fs, account_dir, folder, segment_no, handle):
        self._fs, self.account_dir, self._folder = fs, account_dir, folder
        self.segment_no, self._h = segment_no, handle
        self._path = os.path.join(account_dir, JOURNAL_DIR, seg_name(segment_no))
        self._poisoned = None

    @property
    def account_id(self):
        return self._folder.account_id

    @property
    def aggregate_id(self):
        return self._folder.aggregate_id

    @property
    def poisoned(self):
        return self._poisoned is not None

    # ------------------------------------------------------------------------------------------------ JournalPort
    def append(self, event):
        if self._poisoned is not None:
            raise DurabilityUnavailable(f'append refused: journal poisoned by an earlier {self._poisoned}', self._path)
        if self._h is None:
            raise DurabilityUnavailable('append refused: journal closed', self._path)
        payload = None
        if isinstance(event, EVENT_TYPES):                        # anything else: the gate gives the one refusal
            payload = canonical_bytes(event)
            if len(payload) > MAX_RECORD:
                raise JournalConflict(f'event of {len(payload)} bytes exceeds the {MAX_RECORD}-byte record bound')
            back = decode_result(payload)                         # A18: the writer validates like the reader
            if back.outcome is not Outcome.OK or canonical_bytes(back.record) != payload:
                raise JournalConflict(f'event does not round-trip through the NC-01 codec ({back.outcome})')
        p = self._folder.prepare(event)                           # JournalGate.admit (+ chain tripwire)
        if p is Admission.ALREADY_APPLIED:
            return Admission.ALREADY_APPLIED
        rec = frame(RT_EVENT, payload)
        self._fs.mark('C-E1')                                     # before the write: nothing on disk
        try:
            self._fs.write(self._h, rec)
        except OSError as ex:
            self._poison('write')
            raise DurabilityUnavailable('write', self._path, ex) from None
        self._fs.mark('C-E2')                                     # written, not yet durable
        try:
            self._fs.fsync(self._h)
        except OSError as ex:
            self._poison('fsync')
            raise DurabilityUnavailable('fsync', self._path, ex) from None
        self._fs.mark('C-E3')                                     # durable: the caller may now send / apply
        self._folder.commit(p)
        return Admission.APPLY

    def last_sequence(self):
        return self._folder.last_sequence

    def read(self, after_sequence=0):
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError('after_sequence must be an int >= 0')
        return tuple(self._folder.events[after_sequence:])

    def find_decision(self, decision_id):
        return self._folder.decisions.get(decision_id)

    def gate(self):
        """The JournalGate after the last durable event (rebuilt by replay at boot). Read it, never admit through it:
        appends go through `append` only."""
        return self._folder.gate

    # ------------------------------------------------------------------------------------------------ extras
    def state(self):
        """The recovered / current JournalState (fold.py): ownership UNKNOWN, intents classified for the Runner."""
        return self._folder.state()

    def close(self):
        if self._h is not None:
            h, self._h = self._h, None
            _close_quiet(self._fs, h)

    def _poison(self, op):
        self._poisoned = op
        h, self._h = self._h, None
        if h is not None:
            _close_quiet(self._fs, h)


def _check_ids(account_id, aggregate_id):
    if not (type(account_id) is str and ID_RE.fullmatch(account_id) and account_id.startswith('acct_')):
        raise ValueError('account_id is not an acct_ id')
    if not (type(aggregate_id) is str and ID_RE.fullmatch(aggregate_id) and aggregate_id.startswith('pf_')):
        raise ValueError('aggregate_id is not a pf_ id')


def create_journal(account_dir, account_id, aggregate_id, *, fs=None, writer_build=WRITER_BUILD):
    """Create a new, empty journal (first run of an account; NC-02b's INIT will call this). Raises JournalExists if
    segments are already there (open those with recover_journal) and DurabilityUnavailable if the store cannot write.
    An interrupted create leaves either nothing, an empty journal dir (create again) or a torn first segment
    (recover_journal seals it)."""
    _check_ids(account_id, aggregate_id)
    fs = fs or RealFs()
    jd = os.path.join(account_dir, JOURNAL_DIR)
    step, at = 'create', account_dir
    try:
        k = fs.kind(account_dir)
        if k == 'missing':
            step = 'mkdir'
            fs.mkdir(account_dir)
            fs.fsync_dir(os.path.dirname(os.path.abspath(account_dir)))
        elif k != 'dir':
            raise DurabilityUnavailable('create (the account path is not a directory)', account_dir)
        at = jd
        k = fs.kind(jd)
        if k == 'missing':
            step = 'mkdir'
            fs.mkdir(jd)
            fs.fsync_dir(account_dir)
        elif k != 'dir':
            raise DurabilityUnavailable('create (the journal path is not a directory)', jd)
        elif fs.listdir(jd):
            raise JournalExists('the journal already has segments: open it with recover_journal')
        step, at = 'segment create', os.path.join(jd, seg_name(1))
        write_segment(fs, jd, SegmentHeader(account_id, aggregate_id, 1, 0, (), writer_build))
        step = 'open'
        h = fs.open_append(at)
    except OSError as ex:
        raise DurabilityUnavailable(step, at, ex) from None
    return FileJournal(fs, account_dir, Folder(account_id, aggregate_id), 1, h)
