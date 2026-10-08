"""recover_journal: open an existing journal, decide what it is, fold it, and repair only a torn tail.

Read-only phase (no byte written until the verdict is CLEAN or REPAIRED):
  P1 list journal/: a missing or empty journal dir is MISSING; a non-directory is UNREADABLE.
  P2 read every segment; a non-file or an I/O error is UNREADABLE (decided after rule 1).
  P3 RULE 1: peek every readable segment's file header and record 0, and the envelope version of every CRC-valid event
     record in every segment. An unknown frame / format version, a future format_version / min_reader_version, or an
     event whose NC-01 schema_version this build does not read => ABORT_RO with zero writes. Rule 1 wins over damage.
  P4 structure: segments numbered 1..n; the newest segment m with a valid header seals 1..m-1 (length + sha256 of the
     sealed bytes); every sealed prefix scans clean; segments after m may only be interrupted creates (VOID: no valid
     record, NUL or a prefix of a header, never longer than a header frame); the header names this account / aggregate
     and the right lsn_base. Anything else is DAMAGED.
  P5 decode every event strictly (NC-01 codec, canonical bytes only) and rebuild the JournalGate by replaying them
     (fold.Folder.replay: the step-0 gate, then one NC-01 chain check).
     Any refusal, or a record twice, is DAMAGED (never skipped, never truncated).
  P6 the active segment m: bytes after its last valid record with no valid record after them are a TORN tail.
Write phase (only for a torn tail / void segments): copy each torn byte range into evidence/ (O_EXCL, fsync, dir flush;
an earlier identical copy is reused), then create segment n+1 whose header seals m at its last good byte and every void
segment at 0. Nothing is truncated or overwritten (design D3). An OSError here is DURABILITY_UNAVAILABLE (hard HOLD).
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from newcore.domain import canonical_bytes, decode_result
from newcore.domain.codec import Outcome
from newcore.domain.events import EVENT_TYPES
from newcore.ports.journal import JournalConflict

from .errors import DurabilityUnavailable
from .envelope import EvidenceRef, write_evidence
from .fold import Folder
from .frame import KIND_SEGMENT, MAX_RECORD, RT_EVENT, RT_HEADER, HeaderState, scan
from .fs import RealFs
from .header import (EMPTY_SHA, HeaderError, Seal, SegmentHeader, VersionVerdict, check_version, decode_header,
                     header_frame_max, strict_json)
from .hold import Verdict, directive_for
from .journal import (JOURNAL_DIR, LOCK_NAME, SEG_RE, WRITER_BUILD, FileJournal, ReadOnlyJournal, _check_ids,
                      acquire_lock, seg_name, write_segment)


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str                 # future_format | unknown_format | future_event | unreadable | non_file | unexpected_file
    #                           | missing | damage | torn_tail | void_segment | foreign
    name: str | None          # file name (never a full path)
    offset: int | None
    detail: str

@dataclass(frozen=True, slots=True)
class Recovery:
    verdict: Verdict
    journal: FileJournal | None       # CLEAN / REPAIRED only
    state: object | None              # JournalState: CLEAN / REPAIRED / DURABILITY_UNAVAILABLE (fold is intact)
    findings: tuple
    evidence: tuple
    created: tuple                    # files this recovery created (relative names)
    view: ReadOnlyJournal | None = None   # DURABILITY_UNAVAILABLE: read-only events / gate / state, appends refused

    @property
    def directive(self):
        return directive_for(self.verdict)

def _sha(b):
    return hashlib.sha256(b).hexdigest()

class _Out(Exception):
    def __init__(self, verdict, findings):
        self.verdict, self.findings = verdict, tuple(findings)

def recover_journal(account_dir, account_id, aggregate_id, *, fs=None, writer_build=WRITER_BUILD, cipher=None):
    """Inspect (read-only) and, for CLEAN / a torn tail, open the journal. `cipher` seals torn bytes (None = DPAPI).
    Raises JournalLocked when another writer holds the journal (the read-only verdicts never take the lock, so an
    ABORT_RO / DAMAGED / UNREADABLE / MISSING journal is never written to, not even by the lock file)."""
    insp = inspect_journal(account_dir, account_id, aggregate_id, fs=fs)
    return open_journal(insp, writer_build=writer_build, cipher=cipher)


@dataclass(frozen=True)
class Inspection:
    """The read-only verdict of a journal (NC-02b boot runs rule 1 over every member before any write)."""
    fs: object
    account_dir: str
    account_id: str
    aggregate_id: str
    verdict: Verdict                   # CLEAN (incl. a torn tail to repair) or the zero-write outcome
    findings: tuple
    plan: object | None

    @property
    def needs_repair(self):
        p = self.plan
        return p is not None and (bool(p.voids) or (p.active_scan is not None and p.active_scan.tail_offset is not None))

    @property
    def state(self):
        return None if self.plan is None else self.plan.folder.state()

    @property
    def last_sequence(self):
        return 0 if self.plan is None else self.plan.folder.last_sequence

    @property
    def events(self):
        return () if self.plan is None else tuple(self.plan.folder.events)


def inspect_journal(account_dir, account_id, aggregate_id, *, fs=None):
    """Read-only phase only: never writes. verdict is CLEAN when the journal can be opened (maybe after a repair)."""
    _check_ids(account_id, aggregate_id)
    fs = fs or RealFs()
    try:
        plan = _read_only_safe(fs, account_dir, account_id, aggregate_id)
    except _Out as out:
        return Inspection(fs, account_dir, account_id, aggregate_id, out.verdict, out.findings, None)
    return Inspection(fs, account_dir, account_id, aggregate_id, Verdict.CLEAN, tuple(plan.findings), plan)


def open_journal(insp, *, writer_build=WRITER_BUILD, cipher=None):
    """Write phase of an inspection: take the writer lock, re-read under it, repair a torn tail (evidence envelope,
    seal, roll) and open for appending. Raises JournalLocked."""
    if insp.plan is None:
        return Recovery(insp.verdict, None, None, insp.findings, (), ())
    fs = insp.fs
    jd = os.path.join(insp.account_dir, JOURNAL_DIR)
    try:
        lock = acquire_lock(fs, jd)
    except OSError:
        return Recovery(Verdict.DURABILITY_UNAVAILABLE, None, insp.plan.folder.state(), tuple(insp.plan.findings), (),
                        (), ReadOnlyJournal(insp.plan.folder, 'the writer lock cannot be taken'))
    try:
        plan = _read_only_safe(fs, insp.account_dir, insp.account_id, insp.aggregate_id)     # re-read under the lock
    except _Out as out:
        fs.unlock(lock)
        return Recovery(out.verdict, None, None, out.findings, (), ())
    rec = _finish(fs, insp.account_dir, insp.account_id, plan, writer_build, cipher, lock)
    if rec.journal is None:
        fs.unlock(lock)
    return rec


def _read_only_safe(fs, account_dir, account_id, aggregate_id):
    """_read_only, with any unexpected exception on hostile bytes turned into DAMAGED (never raised: finding 2)."""
    try:
        return _read_only(fs, account_dir, account_id, aggregate_id)
    except _Out:
        raise
    except (IndexError, KeyError, TypeError, ValueError, AttributeError, OverflowError) as ex:
        raise _Out(Verdict.DAMAGED, [Finding('damage', None, None, f'internal: {type(ex).__name__}')]) from None

# ---------------------------------------------------------------------------------------------------- read-only phase
@dataclass
class _Plan:
    folder: Folder
    names: dict               # segment no -> file name
    blobs: dict               # segment no -> bytes
    m: int                    # newest segment with a valid header (0 = none)
    header: SegmentHeader | None
    active_scan: object | None
    voids: list
    findings: list

def _read_only(fs, account_dir, account_id, aggregate_id):
    jd = os.path.join(account_dir, JOURNAL_DIR)
    try:
        k = fs.kind(jd)
        if k == 'missing':
            raise _Out(Verdict.MISSING, [Finding('missing', JOURNAL_DIR, None, 'no journal directory')])
        if k != 'dir':
            raise _Out(Verdict.UNREADABLE, [Finding('non_file', JOURNAL_DIR, None, 'the journal path is not a directory')])
        listing = fs.listdir(jd)
    except OSError as ex:
        raise _Out(Verdict.UNREADABLE, [Finding('unreadable', JOURNAL_DIR, None, type(ex).__name__)]) from None

    names, findings, unreadable, damage = {}, [], [], []
    for n in listing:
        mt = SEG_RE.fullmatch(n)
        if mt and int(mt.group(1)) >= 1:
            names[int(mt.group(1))] = n
        elif n != LOCK_NAME:                                  # Cowork finding 7: reported, ignored, never damage
            findings.append(Finding('stray_file', n, None, 'not a journal segment: ignored'))
    blobs = {}
    for no, n in sorted(names.items()):
        p = os.path.join(jd, n)
        try:
            kk = fs.kind(p)
            if kk != 'file':
                unreadable.append(Finding('non_file', n, None, f'a {kk} at a segment path'))
                continue
            blobs[no] = fs.read_bytes(p)
        except OSError as ex:
            unreadable.append(Finding('unreadable', n, None, type(ex).__name__))

    # P3 rule 1 -------------------------------------------------------------------------------------------------
    scans, headers, future = {}, {}, []
    decoded = {}                                              # (no, offset) -> DecodeResult
    for no, data in blobs.items():
        n = names[no]
        sc = scans[no] = scan(data, KIND_SEGMENT)
        if sc.header is HeaderState.UNKNOWN_FORMAT:
            future.append(Finding('unknown_format', n, 0, f'frame version {sc.frame_version} / reserved bits'))
            continue
        if sc.header is not HeaderState.OK:
            continue
        recs = sc.records
        if recs and recs[0].offset == 8 and recs[0].rtype == RT_HEADER:
            try:
                doc, problems = strict_json(recs[0].payload)
                v = check_version(doc)
                if v is not VersionVerdict.OK:
                    future.append(Finding('future_format' if v is VersionVerdict.FUTURE else 'unknown_format', n, 8,
                                          'segment header version'))
                else:
                    headers[no] = decode_header(doc, problems, recs[0].payload)
            except HeaderError as ex:
                headers[no] = ex
        for r in recs:
            if r.rtype == RT_EVENT:
                res = decoded[(no, r.offset)] = decode_result(r.payload)
                if res.outcome is Outcome.UNSUPPORTED_VERSION:
                    future.append(Finding('future_event', n, r.offset, f'event schema {res.error.direction}'))
    if future:
        raise _Out(Verdict.ABORT_RO, future)
    if unreadable:
        raise _Out(Verdict.UNREADABLE, unreadable)
    if damage:
        raise _Out(Verdict.DAMAGED, damage)
    if not names:
        raise _Out(Verdict.MISSING, [Finding('missing', JOURNAL_DIR, None, 'an empty journal directory')])

    # P4 structure ------------------------------------------------------------------------------------------------
    nos = sorted(names)
    if nos != list(range(1, len(nos) + 1)):
        raise _Out(Verdict.DAMAGED, [Finding('damage', None, None, f'segments are not numbered 1..{len(nos)}')])
    for no, h in headers.items():
        if isinstance(h, HeaderError):
            damage.append(Finding('damage', names[no], 8, f'segment header: {h}'))
    valid = [no for no, h in headers.items() if isinstance(h, SegmentHeader)]
    m = max(valid, default=0)
    hdr = headers.get(m) if m else None
    voids = []
    for no in nos[m:]:
        sc, data = scans[no], blobs[no]
        if no in headers:                                     # a CRC-valid header that does not decode
            continue
        void = (not sc.records and sc.damage is None and len(data) <= header_frame_max(no)
                and sc.header in (HeaderState.TORN, HeaderState.ZERO, HeaderState.OK))
        if void:
            voids.append(no)
        else:
            why = sc.damage[1] if sc.damage else f'{sc.header} file of {len(data)} bytes after the last header'
            damage.append(Finding('damage', names[no], sc.damage[0] if sc.damage else 0, why))
    seq_segments = []                                         # (no, records after the header)
    if hdr is not None:
        if (hdr.account_id, hdr.aggregate_id) != (account_id, aggregate_id) or hdr.segment_no != m \
                or len(hdr.seals) != m - 1:                    # Cowork finding 2: stop here, never index past it
            raise _Out(Verdict.DAMAGED, damage + [Finding('foreign', names[m], 8,
                                                          'the journal names another account / aggregate / segment')])
        for j in range(1, m):
            seal, data = hdr.seals[j - 1], blobs[j]
            if seal.sealed_len > len(data) or _sha(data[:seal.sealed_len]) != seal.sha256:
                damage.append(Finding('damage', names[j], seal.sealed_len, 'sealed bytes differ from the seal'))
                continue
            if seal.sealed_len == 0:
                continue
            sj = scan(data[:seal.sealed_len], KIND_SEGMENT)
            hj = headers.get(j)
            if not sj.clean or not sj.records or sj.records[0].rtype != RT_HEADER or not isinstance(hj, SegmentHeader):
                damage.append(Finding('damage', names[j], sj.good_end, 'a sealed segment does not scan clean'))
                continue
            if (hj.account_id, hj.aggregate_id, hj.segment_no) != (account_id, aggregate_id, j) or \
                    hj.seals != hdr.seals[:j - 1]:
                damage.append(Finding('damage', names[j], 8, 'header disagrees with the newer seals'))
                continue
            seq_segments.append((j, hj, sj.records[1:]))
        sm = scans[m]
        if sm.damage is not None:
            damage.append(Finding('damage', names[m], sm.damage[0], sm.damage[1]))
        seq_segments.append((m, hdr, sm.records[1:]))
    if damage:
        raise _Out(Verdict.DAMAGED, damage)

    # P5 decode and fold ------------------------------------------------------------------------------------------
    events = []
    for j, hj, recs in seq_segments:
        n = names[j]
        if hj.lsn_base != len(events):
            raise _Out(Verdict.DAMAGED, [Finding('damage', n, 8, f'lsn_base {hj.lsn_base} != {len(events)}')])
        for r in recs:
            if r.rtype != RT_EVENT or len(r.payload) > MAX_RECORD:
                raise _Out(Verdict.DAMAGED, [Finding('damage', n, r.offset, f'record type {r.rtype} in a segment')])
            res = decoded.get((j, r.offset)) or decode_result(r.payload)
            if res.outcome is not Outcome.OK:
                raise _Out(Verdict.DAMAGED, [Finding('damage', n, r.offset, f'event record {res.outcome}: '
                                                                             f'{res.error.path}')])
            ev = res.record
            if not isinstance(ev, EVENT_TYPES) or canonical_bytes(ev) != r.payload:
                raise _Out(Verdict.DAMAGED, [Finding('damage', n, r.offset, 'not a canonical NC-01 event record')])
            events.append(ev)
    try:                                                      # boot: rebuild the gate by replaying the durable events
        folder = Folder.replay(account_id, aggregate_id, events)
    except JournalConflict as ex:
        raise _Out(Verdict.DAMAGED, [Finding('damage', None, None, f'gate replay: {ex}')]) from None

    # P6 torn tail ------------------------------------------------------------------------------------------------
    active = scans[m] if m else None
    if active is not None and active.tail_offset is not None:
        findings.append(Finding('torn_tail', names[m], active.tail_offset, f'{len(blobs[m]) - active.tail_offset} bytes'))
    for v in voids:
        findings.append(Finding('void_segment', names[v], 0, f'interrupted segment create ({len(blobs[v])} bytes)'))
    return _Plan(folder, names, blobs, m, hdr, active, voids, findings)

# ---------------------------------------------------------------------------------------------------- write phase
def _finish(fs, account_dir, account_id, plan, writer_build, cipher, lock=None):
    jd = os.path.join(account_dir, JOURNAL_DIR)
    folder, m = plan.folder, plan.m
    torn = plan.active_scan is not None and plan.active_scan.tail_offset is not None
    if not torn and not plan.voids:
        path = os.path.join(jd, plan.names[m])
        try:
            h = fs.open_append(path)
        except OSError:
            return Recovery(Verdict.DURABILITY_UNAVAILABLE, None, folder.state(), tuple(plan.findings), (), (),
                            ReadOnlyJournal(folder, 'the segment cannot be opened for writing'))
        j = FileJournal(fs, account_dir, folder, m, h, plan.header.seals, len(plan.blobs[m]), writer_build, cipher,
                        lock)
        return Recovery(Verdict.CLEAN, j, folder.state(),
                        tuple(plan.findings), (), ())

    pieces = []                                               # (segment no, offset, bytes) to preserve
    seals = list(plan.header.seals) if plan.header is not None else []
    if m:
        data, sc = plan.blobs[m], plan.active_scan
        end = sc.tail_offset if torn else len(data)
        if torn:
            pieces.append((m, end, data[end:]))
        seals.append(Seal(m, end, _sha(data[:end])))
    for v in plan.voids:
        if plan.blobs[v]:
            pieces.append((v, 0, plan.blobs[v]))
        seals.append(Seal(v, 0, EMPTY_SHA))
    new_no = len(plan.names) + 1
    evidence, created = [], []
    try:
        for no, off, data in pieces:
            fs.mark('C-T0')
            src = 'torn_tail' if no == m else 'void_segment'
            ref, new = write_evidence(fs, account_dir, account_id, plan.names[no], off, data, src, cipher)
            evidence.append(ref)
            if new:
                created.append(ref.name)
        fs.mark('C-T1')                                       # tail preserved, segment not yet sealed
        hdr = SegmentHeader(account_id, folder.aggregate_id, new_no, folder.last_sequence, tuple(seals), writer_build)
        _, seg_len = write_segment(fs, jd, hdr)
        created.append(f'{JOURNAL_DIR}/{seg_name(new_no)}')
        fs.mark('C-T3')                                       # sealed; nothing appended yet
        h = fs.open_append(os.path.join(jd, seg_name(new_no)))
    except OSError:
        return Recovery(Verdict.DURABILITY_UNAVAILABLE, None, folder.state(), tuple(plan.findings), tuple(evidence),
                        tuple(created), ReadOnlyJournal(folder, 'the torn tail cannot be sealed'))
    j = FileJournal(fs, account_dir, folder, new_no, h, tuple(seals), seg_len, writer_build, cipher, lock)
    return Recovery(Verdict.REPAIRED, j, folder.state(), tuple(plan.findings), tuple(evidence), tuple(created))


__all__ = ['Recovery', 'Finding', 'EvidenceRef', 'Verdict', 'Inspection', 'inspect_journal', 'open_journal',
           'recover_journal', 'DurabilityUnavailable']
