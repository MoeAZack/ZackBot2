"""Repros for Codex's review of PR #43 at 8c12adb (two HIGH defects), written before the fixes.

HIGH-1 reused evidence must be made durable before the sealing segment. The first evidence attempt can write every byte
       and then fail its fsync (or a directory flush); the retry finds byte-identical evidence and used to return it
       as-is, then durably created the sealing segment - a power loss then left the evidence zero-byte (NTFS model) or
       absent (POSIX model) while recovery reported CLEAN: the torn bytes were gone (the sealed segment keeps them, but
       a sealed tail is never read again). Invariant pinned here for both models and every pending-byte outcome:
       after failed-first-durability -> retry -> crash, recovery is CLEAN / REPAIRED only with the intact evidence.
HIGH-2 a future format after corruption must still force ABORT_RO: the frame scan stopped at the first invalid record,
       so rule 1 never saw a CRC-valid future-schema event (or future header) behind a complete bad-CRC frame and the
       verdict was DAMAGED. Rule 1 now sweeps every independently CRC-valid frame of the file (resync on the frame
       sync bytes, bounded by the file size) before any DAMAGED verdict.
"""
import os

import pytest

from nc02a_events import ACCOUNT_ID, AGGREGATE_ID, SCENARIO
from nc02a_memfs import PENDING, FaultFs, oserror
from nc02a_util import ACCT_DIR, mem_journal, records, recover, seg
from newcore.domain import canonical_bytes
from newcore.store import Verdict
from newcore.store.frame import REC, RT_EVENT, RT_HEADER, frame
from newcore.store.header import canonical_json
from newcore.store.recovery import evidence_bytes

MODELS = ('ntfs', 'posix')
START = 6
TAIL = frame(RT_EVENT, canonical_bytes(SCENARIO[START]))[:37]
EV_DIR = os.path.join(ACCT_DIR, 'evidence')


def torn():
    fs = mem_journal(SCENARIO[:START])
    good = fs.read_bytes(seg(1))
    fs.put(seg(1), good + TAIL)
    return fs, len(good)


def _in(p, d):
    return os.path.normcase(os.path.normpath(os.path.dirname(p))) == os.path.normcase(os.path.normpath(d))


FIRST_FAILURES = {
    'evidence file fsync': lambda op, p: op == 'fsync' and _in(p, EV_DIR),
    'evidence dir flush': lambda op, p: op == 'fsync_dir' and os.path.normpath(p) == os.path.normpath(EV_DIR),
    'account dir flush after mkdir': lambda op, p: op == 'fsync_dir' and os.path.normpath(p) == os.path.normpath(ACCT_DIR),
}


def _evidence_files(fs):
    if fs.kind(EV_DIR) != 'dir':
        return {}
    return {n: fs.read_bytes(os.path.join(EV_DIR, n)) for n in fs.listdir(EV_DIR)}


@pytest.mark.parametrize('first', sorted(FIRST_FAILURES))
def test_after_the_crash_recovery_never_reports_clean_without_the_evidence(first):
    fs, good_len = torn()
    want = evidence_bytes(ACCOUNT_ID, 'seg-000001.seg', good_len, TAIL)
    hit = [False]

    def fail(op, p, i):
        if not hit[0] and FIRST_FAILURES[first](op, p):
            hit[0] = True
            return oserror(5)
        return None
    recover(FaultFs(fs, fail=fail))
    r2 = recover(fs)
    r2.journal.close()
    for model in MODELS:
        for pending in PENDING:
            after = fs.crash(model, pending)
            r = recover(after)
            assert r.verdict in (Verdict.CLEAN, Verdict.REPAIRED), (model, pending, r.verdict)
            assert want in _evidence_files(after).values(), (model, pending, r.verdict)
            r.journal.close()


# ---------------------------------------------------------------------------------------------------- HIGH-2
def _bad_crc(fr):
    b = bytearray(fr)
    b[REC.size - 1] ^= 0xFF                                       # the CRC field: a complete-length invalid frame
    return bytes(b)


def _future_event_frame(data):
    payload = records(data)[-1].payload.replace(b'"schema_version":1', b'"schema_version":2')
    assert b'"schema_version":2' in payload
    return frame(RT_EVENT, payload)


def _future_header_frame(data):
    import json
    doc = json.loads(records(data)[0].payload)
    doc['frame_version' if 'frame_version' in doc else 'format_version'] = 99
    return frame(RT_HEADER, canonical_json(doc))


LAYOUTS = {
    'bad_crc_then_future_event':
        lambda d: d + _bad_crc(frame(RT_EVENT, records(d)[-1].payload)) + _future_event_frame(d),
    'garbage_then_future_event': lambda d: d + b'\x01garbage-bytes\xb2' + _future_event_frame(d),
    'mid_segment_damage_then_future_event':
        lambda d: d[:records(d)[2].offset] + _bad_crc(frame(RT_EVENT, records(d)[2].payload))
        + d[records(d)[3].offset:] + _future_event_frame(d),
    'bad_crc_then_future_header': lambda d: d + _bad_crc(frame(RT_EVENT, records(d)[-1].payload))
        + _future_header_frame(d),
    'bad_magic_file_with_a_future_event': lambda d: b'XXXX' + d[4:] + _future_event_frame(d),
}


@pytest.mark.parametrize('layout', sorted(LAYOUTS))
def test_a_future_format_behind_corruption_is_abort_ro_never_damaged(layout):
    fs = mem_journal(SCENARIO[:4])
    data = fs.read_bytes(seg(1))
    fs.put(seg(1), LAYOUTS[layout](data))
    before = fs.snapshot()
    r = recover(fs)
    assert r.verdict is Verdict.ABORT_RO, (layout, r.verdict, r.findings)
    assert r.journal is None and fs.snapshot() == before        # zero writes, no lock file either


def test_damage_without_any_future_frame_stays_damaged():
    fs = mem_journal(SCENARIO[:4])
    d = fs.read_bytes(seg(1))
    fs.put(seg(1), d + _bad_crc(frame(RT_EVENT, records(d)[-1].payload)) + frame(RT_EVENT, records(d)[-1].payload))
    assert recover(fs).verdict is Verdict.DAMAGED
