"""FileJournal: the JournalPort contract, the on-disk format, durability order and idempotent re-append."""
import dataclasses
import json
import os

import pytest

from nc02a_events import ACCOUNT_ID, AGGREGATE_ID, ENTRY_DEC, INTENTS, SCENARIO, event_id
from nc02a_memfs import FaultFs, MemFs
from nc02a_util import ACCT_DIR, mem_journal, records, recover, seg, tree
from newcore.domain import IntentState, IntentStateChanged, canonical_bytes, contract_sha256
from newcore.ports.journal import Admission, JournalConflict, JournalGate, JournalPort, JournalUnavailable
from newcore.store import (FileJournal, JournalExists, SequenceConflict, Verdict, create_journal,
                           recover_journal)
from newcore.store.frame import FILE_HEADER, KIND_SEGMENT, MAGIC, RT_EVENT, RT_HEADER, scan
from newcore.store.header import FORMAT, FORMAT_VERSION


def new(fs=None):
    fs = fs or MemFs(os.path.dirname(ACCT_DIR))
    return fs, create_journal(ACCT_DIR, ACCOUNT_ID, AGGREGATE_ID, fs=fs)


def test_is_a_journal_port():
    _, j = new()
    assert isinstance(j, JournalPort) and isinstance(j, FileJournal)
    assert j.last_sequence() == 0 and j.read() == () and j.find_decision(ENTRY_DEC) is None


def test_full_scenario_appends_reads_and_finds_decisions():
    _, j = new()
    assert [j.append(e) for e in SCENARIO] == [Admission.APPLY] * len(SCENARIO)
    assert j.last_sequence() == len(SCENARIO)
    assert j.read() == tuple(SCENARIO) and j.read(5) == tuple(SCENARIO[5:])
    assert j.find_decision(ENTRY_DEC) is SCENARIO[0]
    st = j.state()
    assert st.intents == () and [i for i, _ in st.closed] == [INTENTS[k].intent_id for k in ('entry', 'close', 'stop')]
    assert str(st.ownership) == 'unknown'                  # the journal alone never proves ownership
    assert st.booked == ((('SOLUSDT', 'LONG'), 0),)
    JournalGate.rebuild(ACCOUNT_ID, AGGREGATE_ID, SCENARIO)               # the step-0 gate agrees


def test_on_disk_format_is_magic_version_header_then_canonical_crc_framed_events():
    fs = mem_journal()
    data = fs.read_bytes(seg(1))
    assert data[:4] == MAGIC and data[4] == KIND_SEGMENT and data[5] == 1 and data[6:8] == b'\0\0'
    recs = records(data)
    assert recs[0].offset == FILE_HEADER.size and recs[0].rtype == RT_HEADER
    doc = json.loads(recs[0].payload)
    assert (doc['format'], doc['format_version'], doc['min_reader_version']) == (FORMAT, FORMAT_VERSION, 1)
    assert (doc['segment_no'], doc['lsn_base'], doc['seals']) == (1, 0, [])
    assert [r.rtype for r in recs[1:]] == [RT_EVENT] * len(SCENARIO)
    assert [r.payload for r in recs[1:]] == [canonical_bytes(e) for e in SCENARIO]
    assert scan(data, KIND_SEGMENT).clean


def test_identical_reappend_is_a_noop_that_writes_nothing():
    fs, j = new()
    for e in SCENARIO[:4]:
        j.append(e)
    before = fs.snapshot()
    f = FaultFs(fs)
    j._fs = f
    assert j.append(SCENARIO[2]) is Admission.ALREADY_APPLIED
    assert j.append(dataclasses.replace(SCENARIO[3])) is Admission.ALREADY_APPLIED       # equal, not identical object
    assert f.trace == [] and fs.snapshot() == before and j.last_sequence() == 4


@pytest.mark.parametrize('how', ['same_sequence_other_bytes', 'same_id_other_sequence', 'other_id_used_sequence'])
def test_non_identical_reappend_at_a_used_sequence_is_a_typed_conflict(how):
    fs, j = new()
    for e in SCENARIO[:4]:
        j.append(e)
    e = SCENARIO[2]
    bad = {'same_sequence_other_bytes': dataclasses.replace(e, at_ms=e.at_ms + 1),
           'same_id_other_sequence': dataclasses.replace(SCENARIO[4], event_id=e.event_id),
           'other_id_used_sequence': dataclasses.replace(e, event_id=event_id(999))}[how]
    before = fs.snapshot()
    with pytest.raises(SequenceConflict) as ex:
        j.append(bad)
    assert isinstance(ex.value, JournalConflict)
    assert fs.snapshot() == before and j.last_sequence() == 4 and not j.poisoned
    assert j.append(SCENARIO[4]) is Admission.APPLY                                  # the journal is still usable


def test_gap_and_grammar_and_chain_refusals_change_nothing():
    fs, j = new()
    for e in SCENARIO[:2]:
        j.append(e)
    before = fs.snapshot()
    with pytest.raises(SequenceConflict):
        j.append(SCENARIO[3])                                  # gap: sequence 4 after 2
    e = INTENTS['entry']
    with pytest.raises(JournalConflict):                       # terminal before a FINAL result (NC-01 chain / G9)
        j.append(IntentStateChanged(event_id=event_id(3), account_id=ACCOUNT_ID, aggregate_id=AGGREGATE_ID, sequence=3,
                                    at_ms=e.created_at_ms, reason=e.reason, intent_id=e.intent_id,
                                    from_state=IntentState.DURABLE, to_state=IntentState.NOT_SENT))
    with pytest.raises(JournalConflict):
        j.append('not an event')
    with pytest.raises(JournalConflict):                       # another aggregate (G1)
        j.append(dataclasses.replace(SCENARIO[2], aggregate_id='pf_' + 'c' * 32))
    assert fs.snapshot() == before and j.last_sequence() == 2
    assert [j.append(x) for x in SCENARIO[2:]] == [Admission.APPLY] * (len(SCENARIO) - 2)


def test_append_returns_only_after_write_then_fsync():
    fs, j = new()
    f = FaultFs(fs)
    j._fs = f
    j.append(SCENARIO[0])
    assert [op for _, op, _ in f.trace] == ['write', 'fsync']
    assert [label for _, label in f.marks] == ['C-E1', 'C-E2', 'C-E3']
    assert fs.files[os.path.normpath(seg(1))].durable == fs.files[os.path.normpath(seg(1))].current


def test_segment_create_fsyncs_file_then_directory():
    fs = MemFs(os.path.dirname(ACCT_DIR))
    f = FaultFs(fs)
    create_journal(ACCT_DIR, ACCOUNT_ID, AGGREGATE_ID, fs=f)
    assert [op for _, op, _ in f.trace] == ['mkdir', 'fsync_dir', 'mkdir', 'fsync_dir', 'open_new', 'write', 'fsync',
                                           'fsync_dir', 'open_append']


def test_create_refuses_an_existing_journal_and_bad_ids():
    fs = mem_journal(SCENARIO[:2])
    with pytest.raises(JournalExists):
        create_journal(ACCT_DIR, ACCOUNT_ID, AGGREGATE_ID, fs=fs)
    with pytest.raises(ValueError):
        create_journal(ACCT_DIR, 'acct_nothex', AGGREGATE_ID, fs=MemFs(ACCT_DIR))
    with pytest.raises(ValueError):
        recover_journal(ACCT_DIR, ACCOUNT_ID, 'lot_' + 'a' * 32, fs=fs)


def test_recover_clean_resumes_appending_in_the_same_segment():
    fs = mem_journal(SCENARIO[:7])
    r = recover(fs)
    assert r.verdict is Verdict.CLEAN and r.journal.segment_no == 1 and r.created == () and r.evidence == ()
    assert r.journal.read() == tuple(SCENARIO[:7])
    assert [r.journal.append(e) for e in SCENARIO[6:]] == [Admission.ALREADY_APPLIED] + [Admission.APPLY] * 11
    r.journal.close()
    assert recover(fs).journal.read() == tuple(SCENARIO)


def test_recovered_digests_are_the_stored_canonical_bytes():
    fs = mem_journal()
    r = recover(fs)
    for e, rec in zip(r.journal.read(), records(fs.read_bytes(seg(1)))[1:]):
        assert contract_sha256(e) == __import__('hashlib').sha256(rec.payload).hexdigest()


def test_real_file_system_round_trip(tmp_path):
    acct = str(tmp_path / 'accounts' / ACCOUNT_ID)
    os.mkdir(tmp_path / 'accounts')                            # the caller owns the tree above the account dir
    j = create_journal(acct, ACCOUNT_ID, AGGREGATE_ID)
    for e in SCENARIO[:9]:
        assert j.append(e) is Admission.APPLY
    j.close()
    r = recover_journal(acct, ACCOUNT_ID, AGGREGATE_ID)
    assert r.verdict is Verdict.CLEAN and r.journal.read() == tuple(SCENARIO[:9])
    for e in SCENARIO[9:]:
        r.journal.append(e)
    r.journal.close()
    before = tree(str(tmp_path))
    r2 = recover_journal(acct, ACCOUNT_ID, AGGREGATE_ID)
    assert r2.verdict is Verdict.CLEAN and r2.journal.read() == tuple(SCENARIO)
    r2.journal.close()
    assert tree(str(tmp_path)) == before                       # a clean recovery writes nothing
    assert sorted(os.listdir(os.path.join(acct, 'journal'))) == ['seg-000001.seg']


def test_closed_journal_refuses_appends_as_unavailable():
    _, j = new()
    j.close()
    with pytest.raises(JournalUnavailable):
        j.append(SCENARIO[0])
