"""FileJournal: the NC-02a durable JournalPort of one account aggregate.

Layout (inside the account directory the caller owns; NC-02b adds snap\\, HEAD, anchors):

    <account_dir>/journal/seg-000001.seg     append-only segment: file header + record 0 (version header) + events
    <account_dir>/journal/seg-000002.seg     opened when a torn tail / failed append / interrupted create is sealed
    <account_dir>/evidence/*.ev              copies of sealed-off bytes, written BEFORE the segment that seals them

Every event record is frame(RT_EVENT, NC-01 canonical_bytes(event)): length + CRC-32 + the exact canonical bytes, so
the digest the grammar uses is the sha256 of the stored payload.

append (STEP0_INTERFACE r3 store atomicity):
    staged = gate().stage(event)            validate only (JournalConflict / ALREADY_APPLIED); the gate is unchanged
    write frame + fsync the segment         the event is durable
    staged.commit()                         only now is anything consumed (sequence, decision, intent, lineage)
A segment is created with O_EXCL, its header fsynced, then its directory flushed, before any event goes into it.

A failed write / fsync raises DurabilityUnavailable (a JournalUnavailable): nothing is committed, and the account-level
reaction (hard HOLD) is the caller's (hold.py). The failed handle is never reused (its dirty pages may be gone): the
journal closes it and ROLLS at once - the bytes after the last durable record (a partial or even complete frame of the
failed append) are copied to evidence and the next segment seals the old one at its last durable byte - so a restart
reads exactly the in-process state and a retry of the same event lands exactly once. If the roll itself fails, the
next append tries it again first. Identical re-append = ALREADY_APPLIED with no IO; another event at a used sequence or
a reused event id with other bytes raises SequenceConflict.
Single writer: one process per account (AUD-00 single instance; NC-02b's run lock). Not thread-safe.
"""
from __future__ import annotations

import hashlib
import os
import re

from newcore.domain import canonical_bytes, decode_result
from newcore.domain.codec import Outcome
from newcore.domain.events import EVENT_TYPES
from newcore.ports.journal import Admission, JournalConflict

from .errors import DurabilityUnavailable, JournalExists
from .evidence import write_evidence
from .fold import Folder
from .frame import KIND_SEGMENT, MAX_RECORD, RT_EVENT, RT_HEADER, file_header, frame
from .fs import RealFs
from .header import EMPTY_SHA, Seal, SegmentHeader, encode_header

JOURNAL_DIR = 'journal'
SEG_RE = re.compile(r'seg-([0-9]{6})\.seg')
WRITER_BUILD = 'nc-02a'
ACCT_RE = re.compile(r'acct_[0-9a-f]{32}')
PF_RE = re.compile(r'pf_[0-9a-f]{32}')


def seg_name(n):
    return f'seg-{n:06d}.seg'


def _close_quiet(fs, h):
    try:
        fs.close(h)
    except OSError:
        pass


def write_segment(fs, journal_dir, hdr):
    """Create segment hdr.segment_no durably: O_EXCL create, header, fsync, close, directory flush.
    Returns (path, length of the segment)."""
    path = os.path.join(journal_dir, seg_name(hdr.segment_no))
    data = file_header(KIND_SEGMENT) + frame(RT_HEADER, encode_header(hdr))
    h = fs.open_new(path)
    try:
        fs.write(h, data)
        fs.fsync(h)
    finally:
        _close_quiet(fs, h)
    fs.fsync_dir(journal_dir)
    return path, len(data)


class FileJournal:
    """Use create_journal (new) or recover_journal (existing); never construct directly."""

    def __init__(self, fs, account_dir, folder, segment_no, handle, seals, seg_len, writer_build=WRITER_BUILD):
        self._fs, self.account_dir, self._folder = fs, account_dir, folder
        self._jd = os.path.join(account_dir, JOURNAL_DIR)
        self.segment_no, self._h = segment_no, handle
        self._seals, self._seg_len, self._build = tuple(seals), seg_len, writer_build
        self._needs_roll = None           # the op that failed; the next append rolls before writing
        self._closed = False
        self.rolls = ()                   # (EvidenceRef | None, new segment name) of every in-process roll

    @property
    def account_id(self):
        return self._folder.account_id

    @property
    def aggregate_id(self):
        return self._folder.aggregate_id

    @property
    def needs_roll(self):
        """True after a failed write whose roll has not succeeded yet (the next append retries it first)."""
        return self._needs_roll is not None

    def _path(self, n=None):
        return os.path.join(self._jd, seg_name(self.segment_no if n is None else n))

    # ------------------------------------------------------------------------------------------------ JournalPort
    def append(self, event):
        if self._closed:
            raise DurabilityUnavailable('append refused: journal closed', self._path())
        if isinstance(event, EVENT_TYPES):                        # anything else: the gate gives the one refusal
            payload = canonical_bytes(event)
            if len(payload) > MAX_RECORD:
                raise JournalConflict(f'event of {len(payload)} bytes exceeds the {MAX_RECORD}-byte record bound')
            back = decode_result(payload)                         # A18: the writer validates like the reader
            if back.outcome is not Outcome.OK or canonical_bytes(back.record) != payload:
                raise JournalConflict(f'event does not round-trip through the NC-01 codec ({back.outcome})')
        staged = self._folder.stage(event)                        # gate().stage: validate only, nothing consumed
        if staged is Admission.ALREADY_APPLIED:
            return Admission.ALREADY_APPLIED
        if self._needs_roll is not None:
            self._roll()                                          # raises DurabilityUnavailable if it cannot
        rec = frame(RT_EVENT, payload)
        self._fs.mark('C-E1')                                     # before the write: nothing on disk
        try:
            self._fs.write(self._h, rec)
        except OSError as ex:
            self._failed('write')
            raise DurabilityUnavailable('write', self._path(), ex) from None
        self._fs.mark('C-E2')                                     # written, not yet durable
        try:
            self._fs.fsync(self._h)
        except OSError as ex:
            self._failed('fsync')
            raise DurabilityUnavailable('fsync', self._path(), ex) from None
        self._fs.mark('C-E3')                                     # durable: the caller may now send / apply
        self._seg_len += len(rec)
        self._folder.commit(staged)                               # staged.commit(): only now is it consumed
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
        """The JournalGate after the last durable event (rebuilt by replay at boot). Read it; appends stage through
        it via `append` only."""
        return self._folder.gate

    # ------------------------------------------------------------------------------------------------ extras
    def state(self):
        """The recovered / current JournalState (fold.py): ownership UNKNOWN, intents classified for the Runner."""
        return self._folder.state()

    def close(self):
        self._closed = True
        if self._h is not None:
            h, self._h = self._h, None
            _close_quiet(self._fs, h)

    # ------------------------------------------------------------------------------------------------ failure path
    def _failed(self, op):
        """Never reuse the failed handle; roll now so the files equal the in-process state. A failing roll is retried
        by the next append."""
        h, self._h = self._h, None
        if h is not None:
            _close_quiet(self._fs, h)
        self._needs_roll = op
        try:
            self._roll()
        except DurabilityUnavailable:
            pass

    def _roll(self):
        fs, cur = self._fs, self._path()
        step = 'roll read'
        try:
            data = fs.read_bytes(cur)
            if len(data) < self._seg_len:
                raise OSError(5, 'the segment is shorter than its durable length')
            extra = data[self._seg_len:]
            if not extra:                                         # nothing of the failed append reached the file
                step = 'roll reopen'
                self._h = fs.open_append(cur)
                self._needs_roll = None
                self.rolls += ((None, seg_name(self.segment_no)),)
                return
            step = 'roll evidence'
            fs.mark('C-F1')
            ref, _ = write_evidence(fs, self.account_dir, self.account_id, seg_name(self.segment_no), self._seg_len,
                                    extra, source='failed_append')
            good_sha = hashlib.sha256(data[:self._seg_len]).hexdigest()
            seals = self._seals + (Seal(self.segment_no, self._seg_len, good_sha),)
            used = [int(m.group(1)) for m in map(SEG_RE.fullmatch, fs.listdir(self._jd)) if m]
            for left in range(self.segment_no + 1, max(used + [self.segment_no]) + 1):
                # a segment an earlier failed roll created but never made durable: preserved, sealed at 0
                if left in used:
                    junk = fs.read_bytes(self._path(left))
                    if junk:
                        write_evidence(fs, self.account_dir, self.account_id, seg_name(left), 0, junk,
                                       source='failed_append')
                seals += (Seal(left, 0, EMPTY_SHA),)
            new = SegmentHeader(self.account_id, self.aggregate_id, len(seals) + 1, self._folder.last_sequence, seals,
                                self._build)
            step = 'roll segment'
            path, n = write_segment(fs, self._jd, new)
            fs.mark('C-F2')
            h = fs.open_append(path)
        except OSError as ex:
            raise DurabilityUnavailable(step, cur, ex) from None
        self.segment_no, self._h, self._seals, self._seg_len = new.segment_no, h, new.seals, n
        self._needs_roll = None
        self.rolls += ((ref, seg_name(new.segment_no)),)


class ReadOnlyJournal:
    """A read-only view of a journal whose store cannot be written (recover_journal verdict DURABILITY_UNAVAILABLE).

    It holds the events up to the last verified record (a torn tail is NOT included and stays unsealed on disk), the
    rebuilt JournalGate, the decisions and the folded JournalState, so a runner booting into hard HOLD can fold its
    state and price the A23 / A24 emergency set. It has no write handle: every append raises DurabilityUnavailable
    and nothing is ever written."""
    readonly = True

    def __init__(self, folder, why):
        self._folder, self._why = folder, why

    @property
    def account_id(self):
        return self._folder.account_id

    @property
    def aggregate_id(self):
        return self._folder.aggregate_id

    def append(self, event):
        raise DurabilityUnavailable(f'append refused: read-only journal view ({self._why})', '')

    def last_sequence(self):
        return self._folder.last_sequence

    def read(self, after_sequence=0):
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError('after_sequence must be an int >= 0')
        return tuple(self._folder.events[after_sequence:])

    def find_decision(self, decision_id):
        return self._folder.decisions.get(decision_id)

    def gate(self):
        return self._folder.gate

    def state(self):
        return self._folder.state()

    def close(self):
        pass


def _check_ids(account_id, aggregate_id):
    if not (type(account_id) is str and ACCT_RE.fullmatch(account_id)):
        raise ValueError('account_id is not an acct_ id')
    if not (type(aggregate_id) is str and PF_RE.fullmatch(aggregate_id)):
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
        _, n = write_segment(fs, jd, SegmentHeader(account_id, aggregate_id, 1, 0, (), writer_build))
        step = 'open'
        h = fs.open_append(at)
    except OSError as ex:
        raise DurabilityUnavailable(step, at, ex) from None
    return FileJournal(fs, account_dir, Folder(account_id, aggregate_id), 1, h, (), n, writer_build)
