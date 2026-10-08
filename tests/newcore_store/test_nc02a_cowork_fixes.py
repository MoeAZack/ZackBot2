"""Repros for Cowork's verified findings on nc-02a-journal 5e9029c (PR #38), written before the fixes.

1 HIGH   a bit flip in the LAST acknowledged record must be DAMAGED (fail closed), never a torn tail that drops the
         event; in particular a SENT record can never turn its intent into durable_not_sent (a resend).
         Only an INCOMPLETE frame is a torn tail: a short header, or a length past EOF whose bytes are not a complete
         record with a corrupted length field. The zero-fill relaxation exists only behind a pinned flag, OFF.
2 MEDIUM malformed headers (a copied segment, a wrong segment_no) must be DAMAGED, never an uncaught exception.
3 MEDIUM writer fence: one writer per journal (journal/.lock), and an append refuses when the segment size moved.
5 LOW    the header record must be canonical JSON, like events.
7 LOW    a stray file in journal/ that is not seg-*.seg is ignored (reported), not DAMAGED; garbage magic stays HOLD.
"""
import json
import os

import pytest

from nc02a_events import ACCOUNT_ID, AGGREGATE_ID, INTENTS, SCENARIO
from nc02a_memfs import FaultFs, MemFs
from nc02a_util import ACCT_DIR, header_doc, mem_journal, rebuild, records, recover, seg, tree
from newcore.store import IntentRecovery, Verdict, create_journal, recover_journal
from newcore.store import errors as store_errors
from newcore.store import frame as frame_mod
from newcore.store.errors import DurabilityUnavailable
from newcore.store.frame import KIND_SEGMENT, scan

SENT_AT = 3                       # SCENARIO[2] is the entry's `sent` (sequence 3)


def _flips(data, start, end):
    for i in range(start, end):
        for bit in (0x01, 0x80):
            yield i, bit, data[:i] + bytes([data[i] ^ bit]) + data[i + 1:]


# ---------------------------------------------------------------------------------------------------- finding 1
def test_every_single_bit_flip_in_the_last_acknowledged_record_is_damage_never_a_tail():
    fs = mem_journal(SCENARIO[:SENT_AT])
    data = fs.read_bytes(seg(1))
    last = records(data)[-1]
    bad = []
    for i, bit, flipped in _flips(data, last.offset, last.end):
        g = MemFs(os.path.dirname(ACCT_DIR))
        g.put(seg(1), flipped)
        f = FaultFs(g)
        r = recover(f)
        if r.verdict is not Verdict.DAMAGED or [op for _, op, _ in f.trace]:
            bad.append((i - last.offset, bit, str(r.verdict)))
    assert bad == [], bad[:10]


def test_a_sent_intent_never_becomes_durable_not_sent_through_recovery():
    fs = mem_journal(SCENARIO[:SENT_AT])
    data = fs.read_bytes(seg(1))
    last = records(data)[-1]
    entry = INTENTS['entry'].intent_id
    for i, bit, flipped in _flips(data, last.offset, last.end):
        g = MemFs(os.path.dirname(ACCT_DIR))
        g.put(seg(1), flipped)
        r = recover(g)
        if r.state is not None and r.state.intent(entry) is not None:
            assert r.state.intent(entry).recovery is not IntentRecovery.DURABLE_NOT_SENT, (i, bit, r.verdict)
    nxt = frame_mod.frame(frame_mod.RT_EVENT, __import__('newcore.domain', fromlist=['canonical_bytes'])
                          .canonical_bytes(SCENARIO[SENT_AT]))
    for cut in (1, 7, 12, 13, len(nxt) // 2, len(nxt) - 1):                     # genuine torn appends after `sent`
        g = MemFs(os.path.dirname(ACCT_DIR))
        g.put(seg(1), data + nxt[:cut])
        r = recover(g)
        assert r.verdict is Verdict.REPAIRED
        assert r.state.intent(entry).recovery is IntentRecovery.UNKNOWN_NEEDS_QUERY


def test_the_zero_fill_relaxation_is_pinned_and_off_by_default():
    fs = mem_journal(SCENARIO[:SENT_AT])
    data = fs.read_bytes(seg(1))
    zeroed = data + b'\0' * 300
    assert frame_mod.ZERO_FILL_IS_TAIL is False
    sc = scan(zeroed, KIND_SEGMENT)
    assert sc.damage is not None and sc.tail_offset is None                       # default: fail closed
    sc2 = scan(zeroed, KIND_SEGMENT, zero_fill_is_tail=True)
    assert sc2.damage is None and sc2.tail_offset == len(data)                    # the pinned relaxation
    g = MemFs(os.path.dirname(ACCT_DIR))
    g.put(seg(1), zeroed)
    assert recover(g).verdict is Verdict.DAMAGED


# ---------------------------------------------------------------------------------------------------- finding 2
def _two_segments():
    fs = mem_journal(SCENARIO[:4])
    fs.put(seg(1), fs.read_bytes(seg(1)) + b'\x5a\xb2\x0a\x00\xff')
    r = recover(fs)
    assert r.verdict is Verdict.REPAIRED
    r.journal.append(SCENARIO[4])
    r.journal.close()
    return fs


def test_a_copied_segment_is_damaged_not_an_exception():
    fs = mem_journal(SCENARIO[:4])
    fs.put(seg(2), fs.read_bytes(seg(1)))
    assert recover(fs).verdict is Verdict.DAMAGED


def test_a_header_with_the_wrong_segment_number_is_damaged_not_an_exception():
    fs = _two_segments()
    data = fs.read_bytes(seg(2))
    fs.put(seg(2), rebuild(data, header_doc={**header_doc(data), 'segment_no': 1, 'seals': []}))
    assert recover(fs).verdict is Verdict.DAMAGED


VALUES = [None, True, False, -1, 0, 1, 2, 3, 2 ** 70, 'x', '', [], {}, [1], {'a': 1}]


def _mutations(doc):
    for k in sorted(doc):
        for v in VALUES:
            if doc[k] != v or type(doc[k]) is not type(v):
                yield f'{k}={v!r}', {**doc, k: v}
        yield f'-{k}', {kk: vv for kk, vv in doc.items() if kk != k}
    yield '+extra', {**doc, 'extra': 1}
    for i, s in enumerate(doc['seals']):
        for k in sorted(s):
            for v in VALUES:
                seals = [dict(x) for x in doc['seals']]
                seals[i][k] = v
                yield f'seals[{i}].{k}={v!r}', {**doc, 'seals': seals}
    yield 'seals+1', {**doc, 'seals': doc['seals'] + doc['seals']}
    yield 'seals=[]', {**doc, 'seals': []}


@pytest.mark.parametrize('which', [1, 2])
def test_header_mutation_fuzz_never_raises(which):
    base = _two_segments()
    data = base.read_bytes(seg(which))
    doc = header_doc(data)
    n = 0
    for name, mut in _mutations(doc):
        fs = base.crash('ntfs', 'all')
        fs.put(seg(which), rebuild(data, header_doc=mut))
        before = fs.snapshot()
        r = recover(FaultFs(fs))                                                  # must never raise
        n += 1
        assert r.verdict in (Verdict.DAMAGED, Verdict.ABORT_RO, Verdict.CLEAN), (name, r.verdict)
        if r.verdict is not Verdict.CLEAN:
            assert fs.snapshot() == before, name
        else:
            assert r.journal.read() == tuple(SCENARIO[:5]), name
            r.journal.close()
    assert n > 150


# ---------------------------------------------------------------------------------------------------- finding 3
def test_one_writer_per_journal_on_the_real_file_system(tmp_path):
    acct = str(tmp_path / ACCOUNT_ID)
    j = create_journal(acct, ACCOUNT_ID, AGGREGATE_ID)
    for e in SCENARIO[:3]:
        j.append(e)
    with pytest.raises(getattr(store_errors, 'JournalLocked', type(None))):
        recover_journal(acct, ACCOUNT_ID, AGGREGATE_ID)                           # a second writer is refused
    j.append(SCENARIO[3])
    j.close()
    r = recover_journal(acct, ACCOUNT_ID, AGGREGATE_ID)                           # the lock died with the writer
    assert r.verdict is Verdict.CLEAN and r.journal.read() == tuple(SCENARIO[:4])
    r.journal.close()


def test_one_writer_per_journal_in_memory():
    fs = MemFs(os.path.dirname(ACCT_DIR))
    j = create_journal(ACCT_DIR, ACCOUNT_ID, AGGREGATE_ID, fs=fs)
    with pytest.raises(getattr(store_errors, 'JournalLocked', type(None))):
        recover(fs)
    j.close()
    assert recover(fs).verdict is Verdict.CLEAN


def test_an_append_refuses_when_the_segment_grew_behind_the_writers_back():
    fs = MemFs(os.path.dirname(ACCT_DIR))
    j = create_journal(ACCT_DIR, ACCOUNT_ID, AGGREGATE_ID, fs=fs)
    for e in SCENARIO[:2]:
        j.append(e)
    fs.files[os.path.normpath(seg(1))].current += b'another writer'
    before = fs.snapshot()
    with pytest.raises(DurabilityUnavailable):
        j.append(SCENARIO[2])
    assert fs.snapshot() == before and j.last_sequence() == 2
    with pytest.raises(DurabilityUnavailable):
        j.append(SCENARIO[2])                                                     # fenced for good


# ---------------------------------------------------------------------------------------------------- findings 5, 7
def test_a_non_canonical_header_record_is_damaged():
    fs = mem_journal(SCENARIO[:3])
    data = fs.read_bytes(seg(1))
    doc = header_doc(data)
    pretty = json.dumps(doc, sort_keys=True, indent=1).encode()                   # same content, not canonical
    fs.put(seg(1), rebuild(data, header_doc=pretty))
    assert recover(fs).verdict is Verdict.DAMAGED


def test_a_stray_file_in_the_journal_dir_is_ignored_but_garbage_magic_holds():
    fs = mem_journal(SCENARIO[:3])
    fs.put(os.path.join(ACCT_DIR, 'journal', 'notes.txt'), b'hello')
    r = recover(fs)
    assert r.verdict is Verdict.CLEAN and any(f.kind == 'stray_file' for f in r.findings)
    r.journal.close()
    fs.put(seg(2), b'GARBAGE!' * 100)
    assert recover(fs).verdict is Verdict.DAMAGED


def test_real_fs_tree_helper_is_importable():
    assert callable(tree)
