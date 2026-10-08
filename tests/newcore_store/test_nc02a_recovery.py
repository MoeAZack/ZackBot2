"""recover_journal: torn tail -> evidence + seal-and-roll; damage / unreadable / missing -> HOLD with zero writes; a
future or unknown version anywhere -> ABORT_RO with zero writes (rule 1 beats everything)."""
import json
import os

import pytest

from nc02a_events import ACCOUNT_ID, AGGREGATE_ID, SCENARIO
from nc02a_memfs import FaultFs, MemFs, oserror
from nc02a_util import ACCT_DIR, header_doc, mem_journal, rebuild, records, recover, seg, tree
from newcore.domain import HoldKind, ReasonCode, canonical_bytes
from newcore.ports.journal import Admission
from newcore.store import StoreOutcome, Verdict, recover_journal
from newcore.store.frame import (FILE_HEADER, KIND_EVIDENCE, KIND_SEGMENT, RT_EVENT, RT_EVIDENCE_BODY, RT_HEADER,
                                 frame, scan)
from newcore.store.header import EMPTY_SHA, header_frame_max
from newcore.store.recovery import evidence_bytes

N = 6                                            # events durably in the journal before the fault
TORN_FRAME = frame(RT_EVENT, canonical_bytes(SCENARIO[N]))


def put(fs, path, data):
    fs.put(path, data)


def watched(fs):
    """(FaultFs that records every mutating call, snapshot before)."""
    return FaultFs(fs), fs.snapshot()


def assert_zero_writes(f, fs, before):
    assert f.trace == [], f.trace
    assert fs.snapshot() == before


# ------------------------------------------------------------------------------------------------------- torn tail
TAILS = {
    'half_frame': TORN_FRAME[:len(TORN_FRAME) // 2],
    'header_only': TORN_FRAME[:7],
    'all_but_one_byte': TORN_FRAME[:-1],
    'frame_then_zero_fill': TORN_FRAME[:20] + b'\0' * 40,
}
# Cowork finding 1 (fail closed): a COMPLETE-length frame that fails its check is damage, even at the very end
NOT_TAILS = {
    'zero_fill': b'\0' * len(TORN_FRAME),
    'complete_frame_bad_crc': TORN_FRAME[:-1] + bytes([TORN_FRAME[-1] ^ 0xFF]),
}


@pytest.mark.parametrize('tail', sorted(NOT_TAILS))
def test_a_complete_length_bad_frame_at_the_end_is_damage_not_a_tail(tail):
    fs = mem_journal(SCENARIO[:N])
    fs.put(seg(1), fs.read_bytes(seg(1)) + NOT_TAILS[tail])
    f = FaultFs(fs)
    before = fs.snapshot()
    r = recover(f)
    assert r.verdict is Verdict.DAMAGED and r.journal is None
    assert f.trace == [] and fs.snapshot() == before


@pytest.mark.parametrize('tail', sorted(TAILS))
def test_torn_tail_is_copied_to_evidence_then_sealed_and_rolled_never_truncated(tail):
    fs = mem_journal(SCENARIO[:N])
    good = fs.read_bytes(seg(1))
    put(fs, seg(1), good + TAILS[tail])
    r = recover(fs)
    assert r.verdict is Verdict.REPAIRED and r.directive.outcome is StoreOutcome.PROCEED
    assert [f.kind for f in r.findings] == ['torn_tail'] and r.findings[0].offset == len(good)
    assert fs.read_bytes(seg(1)) == good + TAILS[tail]                 # never truncated: the torn bytes stay
    (ev,) = r.evidence
    assert (ev.segment, ev.offset, ev.size) == ('seg-000001.seg', len(good), len(TAILS[tail]))
    blob = fs.read_bytes(os.path.join(ACCT_DIR, *ev.name.split('/')))
    assert blob == evidence_bytes(ACCOUNT_ID, 'seg-000001.seg', len(good), TAILS[tail])
    body = scan(blob, KIND_EVIDENCE).records
    assert [x.rtype for x in body] == [RT_HEADER, RT_EVIDENCE_BODY] and body[1].payload == TAILS[tail]
    assert json.loads(body[0].payload)['sha256'] == ev.sha256
    doc = header_doc(fs.read_bytes(seg(2)))
    assert doc['segment_no'] == 2 and doc['lsn_base'] == N
    assert doc['seals'] == [{'segment_no': 1, 'sealed_len': len(good),
                             'sha256': __import__('hashlib').sha256(good).hexdigest()}]
    assert sorted(r.created) == sorted([ev.name, 'journal/seg-000002.seg'])
    j = r.journal
    assert j.segment_no == 2 and j.read() == tuple(SCENARIO[:N])
    assert [j.append(e) for e in SCENARIO[N:]] == [Admission.APPLY] * (len(SCENARIO) - N)
    j.close()
    before = fs.snapshot()
    r2 = recover(fs)
    assert r2.verdict is Verdict.CLEAN and r2.journal.segment_no == 2 and r2.journal.read() == tuple(SCENARIO)
    assert fs.snapshot() == before


def test_a_second_torn_tail_in_the_rolled_segment_rolls_again_and_keeps_every_seal():
    fs = mem_journal(SCENARIO[:N])
    put(fs, seg(1), fs.read_bytes(seg(1)) + TAILS['half_frame'])
    j = recover(fs).journal
    j.append(SCENARIO[N])
    j.close()
    put(fs, seg(2), fs.read_bytes(seg(2)) + TORN_FRAME[:30])
    r = recover(fs)
    assert r.verdict is Verdict.REPAIRED and r.journal.segment_no == 3
    seals = header_doc(fs.read_bytes(seg(3)))['seals']
    assert [s['segment_no'] for s in seals] == [1, 2] and seals[0] == header_doc(fs.read_bytes(seg(2)))['seals'][0]
    assert r.journal.read() == tuple(SCENARIO[:N + 1])


def test_existing_identical_evidence_is_reused_and_a_partial_one_is_kept_not_trusted():
    fs = mem_journal(SCENARIO[:N])
    good = fs.read_bytes(seg(1))
    tail = TAILS['half_frame']
    put(fs, seg(1), good + tail)
    sha = __import__('hashlib').sha256(tail).hexdigest()
    stem = os.path.join(ACCT_DIR, 'evidence', f'torn-seg-000001-o{len(good):010d}-{sha[:16]}')
    full = evidence_bytes(ACCOUNT_ID, 'seg-000001.seg', len(good), tail)
    put(fs, stem + '.ev', full[:10])                                  # a crashed earlier copy: partial
    r = recover(fs)
    assert r.evidence[0].name.endswith('-a1.ev') and fs.read_bytes(stem + '.ev') == full[:10]
    fs2 = mem_journal(SCENARIO[:N])
    put(fs2, seg(1), good + tail)
    put(fs2, stem + '.ev', full)                                      # a complete earlier copy: reused
    r2 = recover(fs2)
    assert r2.evidence[0].name.endswith(f'{sha[:16]}.ev') and r2.created == ('journal/seg-000002.seg',)


# ------------------------------------------------------------------------------------------- interrupted create
@pytest.mark.parametrize('content', [b'', b'ZB', b'\0' * 5, None, 'zero_header'])
def test_interrupted_first_segment_create_is_sealed_at_zero(content):
    fs = mem_journal(())
    full = fs.read_bytes(seg(1))
    data = {None: full[:len(full) - 3], 'zero_header': b'\0' * (len(full) - 3)}.get(content, content)
    put(fs, seg(1), data)
    r = recover(fs)
    assert r.verdict is Verdict.REPAIRED and [f.kind for f in r.findings] == ['void_segment']
    assert header_doc(fs.read_bytes(seg(2)))['seals'] == [{'segment_no': 1, 'sealed_len': 0, 'sha256': EMPTY_SHA}]
    assert len(r.evidence) == (1 if data else 0)
    assert r.journal.last_sequence() == 0 and str(r.state.ownership) == 'unknown'


def test_all_nul_segment_longer_than_any_header_is_damage_not_an_empty_journal():   # NF-28 analogue
    fs = MemFs(ACCT_DIR)
    put(fs, seg(1), b'\0' * (header_frame_max(1) + 1))
    f, before = watched(fs)
    r = recover(f)
    assert r.verdict is Verdict.DAMAGED and r.journal is None and r.state is None
    assert_zero_writes(f, fs, before)


@pytest.mark.parametrize('layout', ['no_journal_dir', 'empty_journal_dir'])
def test_missing_journal_is_hold_with_zero_writes(layout):
    fs = MemFs(ACCT_DIR)
    if layout == 'empty_journal_dir':
        fs.mkdir(os.path.join(ACCT_DIR, 'journal'))
    f, before = watched(fs)
    r = recover(f)
    assert r.verdict is Verdict.MISSING and r.journal is None
    d = r.directive
    assert (d.outcome, d.hold_kind, d.reason) == (StoreOutcome.HOLD, HoldKind.NORMAL, ReasonCode.RECOVERY_STATE_MISSING)
    assert_zero_writes(f, fs, before)


# ----------------------------------------------------------------------------------------- mid-segment damage
def _flip(data, at):
    return data[:at] + bytes([data[at] ^ 0x01]) + data[at + 1:]


def _end_of(data, off):
    return next(r.end for r in records(data) if r.offset == off)


def _rec_offset(fs, i):
    return records(fs.read_bytes(seg(1)))[i].offset


DAMAGE = {
    'crc_payload_bitflip': lambda d, o: _flip(d, o + 20),
    'crc_field': lambda d, o: _flip(d, o + 9),
    'sync_bytes': lambda d, o: _flip(d, o),
    'length_field_huge': lambda d, o: d[:o + 4] + b'\xff\xff\xff\x7f' + d[o + 8:],
    'zeroed_record': lambda d, o: d[:o] + b'\0' * (_end_of(d, o) - o) + d[_end_of(d, o):],
    'header_record_bitflip': lambda d, o: _flip(d, FILE_HEADER.size + 30),
}


@pytest.mark.parametrize('kind', sorted(DAMAGE))
def test_mid_segment_damage_is_hold_with_zero_writes(kind):
    fs = mem_journal(SCENARIO[:N])
    data = fs.read_bytes(seg(1))
    put(fs, seg(1), DAMAGE[kind](data, _rec_offset(fs, 3)))
    f, before = watched(fs)
    r = recover(f)
    assert r.verdict is Verdict.DAMAGED and r.journal is None and r.state is None and r.evidence == ()
    d = r.directive
    assert (d.outcome, d.hold_kind, d.reason) == (StoreOutcome.HOLD, HoldKind.NORMAL, ReasonCode.RECOVERY_SCHEMA_INVALID)
    assert_zero_writes(f, fs, before)


def _payload_variant(name):
    good = canonical_bytes(SCENARIO[N - 1])
    doc = good.decode()
    return {
        'duplicate_key': doc[:-1] + ',"format":"zackbot.newcore"}',                          # NF-30
        'nan': doc.replace('"at_ms":', '"at_ms":NaN,"x":', 1),                                  # NF-31
        'huge_int': doc.replace(f'"sequence":{N}', '"sequence":' + '9' * 401, 1),                 # NF-32
        'float': doc.replace(f'"sequence":{N}', f'"sequence":{N}.0', 1),
        'whitespace': doc.replace(',', ', ', 1),
        'qty_zero': doc.replace('"executed_qty":"5"', '"executed_qty":"0"', 1) if '"executed_qty"' in doc else
        doc.replace('"qty":"5"', '"qty":"0"', 1),                                                # NF-41
        'qty_1e300': doc.replace('"qty":"5"', '"qty":"1' + '0' * 300 + '"', 1),                   # NF-42
        'missing_field': doc.replace('"reason":', '"reasonX":', 1),                               # NF-40
        'not_json': doc[:-5],
        'foreign_document': json.dumps({'lots': []}),
    }[name].encode()


@pytest.mark.parametrize('name', ['duplicate_key', 'nan', 'huge_int', 'float', 'whitespace', 'qty_zero', 'qty_1e300',
                                  'missing_field', 'not_json', 'foreign_document'])
def test_crc_valid_but_hostile_or_invalid_event_is_damage_never_skipped(name):
    fs = mem_journal(SCENARIO[:N])
    data = fs.read_bytes(seg(1))
    payloads = [r.payload for r in records(data)[1:]]
    bad = _payload_variant(name)
    assert bad != payloads[-1]
    put(fs, seg(1), rebuild(data, payloads=payloads[:-1] + [bad]))
    f, before = watched(fs)
    r = recover(f)
    assert r.verdict is Verdict.DAMAGED, r.findings
    assert_zero_writes(f, fs, before)


@pytest.mark.parametrize('how', ['duplicate_record', 'swapped_records', 'gap'])
def test_broken_chain_on_disk_is_damage(how):
    fs = mem_journal(SCENARIO[:N])
    data = fs.read_bytes(seg(1))
    p = [r.payload for r in records(data)[1:]]
    p = {'duplicate_record': p + [p[-1]], 'swapped_records': p[:2] + [p[3], p[2]] + p[4:], 'gap': p[:2] + p[3:]}[how]
    put(fs, seg(1), rebuild(data, payloads=p))
    f, before = watched(fs)
    assert recover(f).verdict is Verdict.DAMAGED
    assert_zero_writes(f, fs, before)


# ------------------------------------------------------------------------------------------ structural damage
def _two_segments():
    fs = mem_journal(SCENARIO[:N])
    put(fs, seg(1), fs.read_bytes(seg(1)) + TAILS['half_frame'])
    j = recover(fs).journal
    j.append(SCENARIO[N])
    j.close()
    return fs


STRUCT = {
    'sealed_bytes_changed': lambda fs: put(fs, seg(1), _flip(fs.read_bytes(seg(1)), 40)),
    'sealed_segment_deleted': lambda fs: fs.files.pop(os.path.normpath(seg(1))),
    'seal_length_beyond_file': lambda fs: put(fs, seg(1), fs.read_bytes(seg(1))[:100]),
    'garbage_magic_segment': lambda fs: put(fs, seg(3), b'garbage!'),
    'garbage_segment_after': lambda fs: put(fs, seg(3), b'garbage!' * 200),
    'foreign_account_header': lambda fs: put(fs, seg(2), rebuild(
        fs.read_bytes(seg(2)), header_doc={**header_doc(fs.read_bytes(seg(2))), 'account_id': 'acct_' + 'f' * 32})),
    'wrong_lsn_base': lambda fs: put(fs, seg(2), rebuild(
        fs.read_bytes(seg(2)), header_doc={**header_doc(fs.read_bytes(seg(2))), 'lsn_base': N - 1})),
    'header_missing_a_seal': lambda fs: put(fs, seg(2), rebuild(
        fs.read_bytes(seg(2)), header_doc={**header_doc(fs.read_bytes(seg(2))), 'seals': []})),
    'header_extra_key': lambda fs: put(fs, seg(2), rebuild(
        fs.read_bytes(seg(2)), header_doc={**header_doc(fs.read_bytes(seg(2))), 'extra': 1})),
    'wrong_file_kind': lambda fs: put(fs, seg(2), fs.read_bytes(seg(2))[:4] + bytes([KIND_EVIDENCE])
                                      + fs.read_bytes(seg(2))[5:]),
}


@pytest.mark.parametrize('kind', sorted(STRUCT))
def test_structural_damage_is_hold_with_zero_writes(kind):
    fs = _two_segments()
    assert recover(fs).verdict is Verdict.CLEAN
    STRUCT[kind](fs)
    f, before = watched(fs)
    r = recover(f)
    assert r.verdict is Verdict.DAMAGED, (kind, r.verdict, r.findings)
    assert_zero_writes(f, fs, before)


# ------------------------------------------------------------------------------------------------ unreadable
@pytest.mark.parametrize('how', ['read_error', 'segment_is_a_directory', 'journal_is_a_file', 'listdir_error'])
def test_unreadable_member_is_hold_with_zero_writes(how):                       # NF-13 / NF-33 analogues
    fs = mem_journal(SCENARIO[:N])
    read_fail = None
    if how == 'read_error':
        read_fail = lambda op, p: oserror(13) if op == 'read_bytes' and p.endswith('.seg') else None   # noqa: E731
    elif how == 'listdir_error':
        read_fail = lambda op, p: oserror(13) if op == 'listdir' else None                             # noqa: E731
    elif how == 'segment_is_a_directory':
        fs.files.pop(os.path.normpath(seg(1)))
        fs.dirs[os.path.normpath(seg(1))] = True
    else:
        for p in list(fs.files):
            fs.files.pop(p)
        fs.dirs.pop(os.path.normpath(os.path.join(ACCT_DIR, 'journal')))
        put(fs, os.path.join(ACCT_DIR, 'journal'), b'x')
    f = FaultFs(fs, read_fail=read_fail)
    before = fs.snapshot()
    r = recover(f)
    assert r.verdict is Verdict.UNREADABLE and r.journal is None
    assert r.directive.reason is ReasonCode.RECOVERY_STATE_UNREADABLE
    assert_zero_writes(f, fs, before)


# -------------------------------------------------------------------------------------- future / unknown: rule 1
def _hdr(fs, **kw):
    data = fs.read_bytes(seg(1))
    put(fs, seg(1), rebuild(data, header_doc={**header_doc(data), **kw}))


FUTURE = {
    'frame_version_2': lambda fs: put(fs, seg(1), fs.read_bytes(seg(1))[:5] + b'\x02' + fs.read_bytes(seg(1))[6:]),
    'reserved_bits': lambda fs: put(fs, seg(1), fs.read_bytes(seg(1))[:6] + b'\x01\x00' + fs.read_bytes(seg(1))[8:]),
    'format_version_2': lambda fs: _hdr(fs, format_version=2),                                    # NF-22
    'min_reader_version_2': lambda fs: _hdr(fs, min_reader_version=2),
    'format_version_string': lambda fs: _hdr(fs, format_version='2'),                             # NF-23
    'format_version_bool': lambda fs: _hdr(fs, format_version=True),                              # NF-23
    'format_version_float': lambda fs: put(fs, seg(1), rebuild(fs.read_bytes(seg(1)), header_doc=json.dumps(
        {**header_doc(fs.read_bytes(seg(1))), 'format_version': 1.5}, sort_keys=True).replace('1.5', '1.0').encode())),
    'format_version_null': lambda fs: _hdr(fs, format_version=None),                              # NF-25
    'format_version_negative': lambda fs: _hdr(fs, format_version=-1),                            # NF-25
    'format_version_zero': lambda fs: _hdr(fs, format_version=0),
    'future_event_schema': lambda fs: put(fs, seg(1), rebuild(fs.read_bytes(seg(1)), payloads=[
        r.payload for r in records(fs.read_bytes(seg(1)))[1:-1]]
        + [records(fs.read_bytes(seg(1)))[-1].payload.replace(b'"schema_version":1', b'"schema_version":2')])),
    'unknown_event_schema': lambda fs: put(fs, seg(1), rebuild(fs.read_bytes(seg(1)), payloads=[
        r.payload for r in records(fs.read_bytes(seg(1)))[1:-1]]
        + [records(fs.read_bytes(seg(1)))[-1].payload.replace(b'"schema_version":1', b'"schema_version":"1"')])),
}


@pytest.mark.parametrize('kind', sorted(FUTURE))
def test_future_or_unknown_version_aborts_read_only_with_zero_writes(kind):
    fs = mem_journal(SCENARIO[:N])
    FUTURE[kind](fs)
    f, before = watched(fs)
    r = recover(f)
    assert r.verdict is Verdict.ABORT_RO, (kind, r.findings)
    assert r.journal is None and r.state is None
    d = r.directive
    assert d.outcome is StoreOutcome.ABORT_RO and not d.account_context and d.reason is ReasonCode.RECOVERY_SCHEMA_FUTURE
    assert_zero_writes(f, fs, before)
    assert recover(f).verdict is Verdict.ABORT_RO                     # a restart aborts again (NF-07): still durable
    assert_zero_writes(f, fs, before)


@pytest.mark.parametrize('also', ['torn_tail', 'mid_damage', 'unreadable_other_segment', 'future_in_sealed_segment'])
def test_rule_one_wins_over_damage_torn_tail_and_unreadable(also):            # NF-29 analogue
    fs = _two_segments()
    target = seg(2)
    if also == 'future_in_sealed_segment':
        target = seg(1)
    data = fs.read_bytes(target)
    put(fs, target, rebuild(data, header_doc={**header_doc(data), 'format_version': 9}))
    other = seg(1) if target == seg(2) else seg(2)
    read_fail = None
    if also == 'torn_tail':
        put(fs, seg(2), fs.read_bytes(seg(2)) + b'\0' * 11)
    elif also == 'mid_damage':
        put(fs, other, _flip(fs.read_bytes(other), 40))
    elif also == 'unreadable_other_segment':
        put(fs, seg(3), b'x' * 5)
        read_fail = lambda op, p: oserror(13) if op == 'read_bytes' and p.endswith('seg-000003.seg') else None  # noqa
    f = FaultFs(fs, read_fail=read_fail)
    before = fs.snapshot()
    assert recover(f).verdict is Verdict.ABORT_RO
    assert_zero_writes(f, fs, before)


def test_future_version_on_a_real_file_system_leaves_bytes_mtimes_and_listing_unchanged(tmp_path):
    from newcore.store import create_journal
    os.mkdir(tmp_path / 'accounts')
    acct = str(tmp_path / 'accounts' / ACCOUNT_ID)
    j = create_journal(acct, ACCOUNT_ID, AGGREGATE_ID)
    for e in SCENARIO[:N]:
        j.append(e)
    j.close()
    p = os.path.join(acct, 'journal', 'seg-000001.seg')
    with open(p, 'rb') as fh:
        data = fh.read()
    with open(p, 'wb') as fh:
        fh.write(rebuild(data, header_doc={**header_doc(data), 'format_version': 2}))
    os.utime(p, ns=(1_791_400_000_000_000_000, 1_791_400_000_000_000_000))
    before = tree(str(tmp_path))
    for _ in range(2):
        r = recover_journal(acct, ACCOUNT_ID, AGGREGATE_ID)
        assert r.verdict is Verdict.ABORT_RO
        assert tree(str(tmp_path)) == before


def test_damage_on_a_real_file_system_leaves_bytes_mtimes_and_listing_unchanged(tmp_path):
    from newcore.store import create_journal
    os.mkdir(tmp_path / 'accounts')
    acct = str(tmp_path / 'accounts' / ACCOUNT_ID)
    j = create_journal(acct, ACCOUNT_ID, AGGREGATE_ID)
    for e in SCENARIO[:N]:
        j.append(e)
    j.close()
    p = os.path.join(acct, 'journal', 'seg-000001.seg')
    with open(p, 'rb') as fh:
        data = fh.read()
    with open(p, 'wb') as fh:
        fh.write(_flip(data, records(data)[2].offset + 20))
    before = tree(str(tmp_path))
    assert recover_journal(acct, ACCOUNT_ID, AGGREGATE_ID).verdict is Verdict.DAMAGED
    assert tree(str(tmp_path)) == before


def test_torn_tail_on_a_real_file_system(tmp_path):
    from newcore.store import create_journal
    os.mkdir(tmp_path / 'accounts')
    acct = str(tmp_path / 'accounts' / ACCOUNT_ID)
    j = create_journal(acct, ACCOUNT_ID, AGGREGATE_ID)
    for e in SCENARIO[:N]:
        j.append(e)
    j.close()
    p = os.path.join(acct, 'journal', 'seg-000001.seg')
    with open(p, 'ab') as fh:
        fh.write(TAILS['half_frame'])
    with open(p, 'rb') as fh:
        torn = fh.read()
    r = recover_journal(acct, ACCOUNT_ID, AGGREGATE_ID)
    assert r.verdict is Verdict.REPAIRED and r.journal.read() == tuple(SCENARIO[:N])
    for e in SCENARIO[N:]:
        r.journal.append(e)
    r.journal.close()
    with open(p, 'rb') as fh:
        assert fh.read() == torn
    assert sorted(os.listdir(os.path.join(acct, 'journal'))) == ['.lock', 'seg-000001.seg', 'seg-000002.seg']
    assert len(os.listdir(os.path.join(acct, 'evidence'))) == 1
    r2 = recover_journal(acct, ACCOUNT_ID, AGGREGATE_ID)
    assert r2.verdict is Verdict.CLEAN and r2.journal.read() == tuple(SCENARIO)
    r2.journal.close()
