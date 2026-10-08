"""NF fixture hookup for NC-02a (prep/nc02-negative-fixtures @ a6364de: impl/fixture_runner_skeleton.py approach).

The frozen NF fixtures are legacy files (state.json, .bak, settings, install). The skeleton's SCENARIO pass needs a
ScenarioBuilder that turns each fixture's NEWCORE scenario into store bytes; for NC-02a the store is the journal, so a
"generation" maps to a segment (primary G = the active segment, backup G-1 = the sealed segment) and a "snapshot
record" to an event record. For every fixture NC-02a can answer, this file:
  1. builds the journal analogue on the real file system (tmp dir) with the store's own writer (ScenarioBuilder);
  2. applies the fixture's inject as a fault plan on the seam (FaultFs around RealFs);
  3. calls the adapter (recover_journal -> the skeleton's RecoveryResult vocabulary) TWICE (start + restart);
  4. checks mode / hold kind / writes and the files-unchanged rule (bytes + mtimes + listing) both times.
Fixtures pinned by contract_sha so a fixture change is visible here. Everything else is listed in DEFERRED with the
reason (NC-02b, installer or reconciliation), and skipped with that reason.
"""
import os
from dataclasses import dataclass

import pytest

from nc02a_events import ACCOUNT_ID, AGGREGATE_ID, INTENTS, SCENARIO, T0, ev, result
from nc02a_memfs import FaultFs, oserror
from nc02a_util import header_doc, rebuild, records, tree
from newcore.domain import Lookup, ResultObserved, ResultPhase, canonical_bytes
from newcore.store import IntentRecovery, RealFs, StoreOutcome, Verdict, create_journal, recover_journal
from newcore.store.frame import RT_EVENT, frame

PREP = 'prep/nc02-negative-fixtures@a6364de'
N = 6


# ---------------------------------------------------------------------------------------------------- adapter
@dataclass
class Result:
    """The skeleton's RecoveryResult fields NC-02a can answer."""
    mode: str                     # ABORT_RO | HOLD | PROCEED (the store does not hold; MANAGE needs reconciliation)
    hold_kind: str | None         # soft | hard
    ownership: str | None         # 'unknown' whenever an account context exists (the journal never proves ownership)
    writes: frozenset             # 'evidence_copy' / 'tail_seal'
    account_context_created: bool
    recovery: object


def adapter(acct_dir, fs):
    r = recover_journal(acct_dir, ACCOUNT_ID, AGGREGATE_ID, fs=fs)
    d = r.directive
    mode = {StoreOutcome.ABORT_RO: 'ABORT_RO', StoreOutcome.HOLD: 'HOLD', StoreOutcome.HARD_HOLD: 'HOLD',
            StoreOutcome.PROCEED: 'PROCEED'}[d.outcome]
    hold = {StoreOutcome.HOLD: 'soft', StoreOutcome.HARD_HOLD: 'hard'}.get(d.outcome)
    writes = frozenset('evidence_copy' if n.startswith('evidence/') else 'tail_seal' for n in r.created)
    if r.journal is not None:
        r.journal.close()
    return Result(mode, hold, 'unknown' if d.account_context else None, writes, d.account_context, r)


# ---------------------------------------------------------------------------------------------------- builders
def _read(p):
    with open(p, 'rb') as fh:
        return fh.read()


def _write(p, data):
    with open(p, 'wb') as fh:
        fh.write(data)


def segp(acct, n):
    return os.path.join(acct, 'journal', f'seg-{n:06d}.seg')


def one_segment(acct, events=SCENARIO[:N]):
    j = create_journal(acct, ACCOUNT_ID, AGGREGATE_ID)
    for e in events:
        j.append(e)
    j.close()


def two_segments(acct):
    """seg 1 = the backup generation (sealed), seg 2 = the primary (active)."""
    one_segment(acct, SCENARIO[:3])
    _write(segp(acct, 1), _read(segp(acct, 1)) + b'\0' * 9)
    r = recover_journal(acct, ACCOUNT_ID, AGGREGATE_ID)
    assert r.verdict is Verdict.REPAIRED
    for e in SCENARIO[3:N]:
        r.journal.append(e)
    r.journal.close()


def set_version(acct, n, value):
    data = _read(segp(acct, n))
    doc = header_doc(data)
    if isinstance(value, bytes):                                   # a JSON token json.dumps cannot spell (1.0)
        import json
        payload = json.dumps({**doc, 'format_version': 77}, sort_keys=True, separators=(',', ':')).encode()
        _write(segp(acct, n), rebuild(data, header_doc=payload.replace(b'"format_version":77', b'"format_version":' + value)))
    else:
        _write(segp(acct, n), rebuild(data, header_doc={**doc, 'format_version': value}))


def flip(acct, n, rec_index):
    data = _read(segp(acct, n))
    at = records(data)[rec_index].offset + 20
    _write(segp(acct, n), data[:at] + bytes([data[at] ^ 1]) + data[at + 1:])


def last_payload(acct, n, fn):
    data = _read(segp(acct, n))
    ps = [r.payload for r in records(data)[1:]]
    bad = fn(ps[-1].decode()).encode()
    assert bad != ps[-1]
    _write(segp(acct, n), rebuild(data, payloads=ps[:-1] + [bad]))


TORN = frame(RT_EVENT, canonical_bytes(SCENARIO[N]))[:41]


def b_future_primary_current_backup(a):
    two_segments(a)
    set_version(a, 2, 99)


def b_future_both(a):
    two_segments(a)
    set_version(a, 1, 2)
    set_version(a, 2, 2)


def b_current_primary_future_backup(a):
    two_segments(a)
    set_version(a, 1, 99)


def b_only_generation_future(a):
    one_segment(a)
    set_version(a, 1, 99)


def b_string_and_bool(a):
    two_segments(a)
    set_version(a, 2, '2')
    set_version(a, 1, True)


def b_float_primary(a):
    two_segments(a)
    set_version(a, 2, b'1.0')


def b_null_and_negative(a):
    two_segments(a)
    set_version(a, 2, None)
    set_version(a, 1, -1)


def b_damaged_primary_future_backup(a):
    two_segments(a)
    flip(a, 2, 1)
    set_version(a, 1, 99)


def b_valid_plus_nul_backup_all_nul(a):
    two_segments(a)
    _write(segp(a, 2), _read(segp(a, 2)) + b'\0' * 512)
    _write(segp(a, 1), b'\0' * len(_read(segp(a, 1))))


def b_all_nul_only(a):
    one_segment(a)
    _write(segp(a, 1), b'\0' * len(_read(segp(a, 1))))


def b_payload(fn):
    def build(a):
        one_segment(a)
        last_payload(a, 1, fn)
    return build


def b_directory_at_primary(a):
    two_segments(a)
    os.remove(segp(a, 2))
    os.mkdir(segp(a, 2))


def b_torn_tail(a):
    one_segment(a)
    _write(segp(a, 1), _read(segp(a, 1)) + TORN)


def b_bare_not_found_after_close_sent(a):
    c = INTENTS['close']
    nf = ev(ResultObserved, 14, T0 + 30_000, reason=c.reason,
            result=result(c, 0, T0 + 30_000, phase=ResultPhase.UNKNOWN, lookup=Lookup.NOT_FOUND))
    one_segment(a, SCENARIO[:13] + [nf])


def _fail_all(code):
    return lambda op, p, i: oserror(code)


def _read_fail_segments(op, p):
    return oserror(13) if op == 'read_bytes' and p.endswith('.seg') else None


# ---------------------------------------------------------------------------------------------------- the table
@dataclass(frozen=True)
class Case:
    contract_sha: str
    fixture_outcome: str          # the fixture's own nc02_outcome / 5a row
    build: object
    mode: str
    hold_kind: str | None = None
    unchanged: str = 'all'        # 'all' | 'existing' (existing files byte-identical, only new files added)
    writes: frozenset = frozenset()
    fail: object = None
    read_fail: object = None
    note: str = ''


ABORT = dict(mode='ABORT_RO')
HOLD_SOFT = dict(mode='HOLD', hold_kind='soft')
HOLD_HARD = dict(mode='HOLD', hold_kind='hard')
DUP = lambda d: d[:-1] + ',"format":"zackbot.newcore"}'                             # noqa: E731
CASES = {
    'NF-01': Case('7f3561b93dacf8162467df3b72c8b53e5b1099e42ffe377b0be808900dd902bc', 'ABORT-RO (5a)',
                  b_future_primary_current_backup, **ABORT),
    'NF-02': Case('0e27f17b8e4ae2b6746eeac8975084a119a40f964eac669be2622d312a7736dd', 'ABORT-RO (5a)',
                  b_future_both, **ABORT),
    'NF-06': Case('327ed907cd406bd5d62c3a510a69fbb8dee8c5dddaf75c6683982a7b604feff1', 'ABORT-RO (5a)',
                  b_current_primary_future_backup, **ABORT),
    'NF-07': Case('16a09d06cf69471da3628ab58cddd4fe78d55cfce0877abd59d31902fa5d056e', 'ABORT-RO (5a), durable',
                  b_future_both, **ABORT, note='restart run proves the refusal is durable without a write'),
    'NF-13': Case('274bcd99be111951b24522cac03bf0a71f20e508bce3e155feec2ab6abfe4f5c', 'HOLD (M18)',
                  two_segments, **HOLD_SOFT, read_fail=_read_fail_segments),
    'NF-22': Case('a308344fe6200ffc1c3dcf506550c13f9bca7fe5c25cf5b3d3bc5d697cb0445d', 'ABORT-RO',
                  b_only_generation_future, **ABORT),
    'NF-23': Case('1d5d5834ec4de4fd492c85f228325e6dd99610402f6235a424f3e0b1e05a4638', 'ABORT-RO',
                  b_string_and_bool, **ABORT),
    'NF-24': Case('08f61e17eaa403f7f5a901dd7a89c5189f8ae4aea78551d98e12a6b9ecff7fe8', 'ABORT-RO',
                  b_float_primary, **ABORT),
    'NF-25': Case('bff26d7d96e2f37fb1f8325e638ad0a20c39865928a59bdbcd2da5b8cd73713a', 'ABORT-RO',
                  b_null_and_negative, **ABORT),
    'NF-27': Case('df99ea585009bf09623d77210d159c50c1d65fb8a4d0e4226f8d5fbc81c4e4fb', 'HOLD',
                  b_valid_plus_nul_backup_all_nul, **HOLD_SOFT, note='evidence copy of damage: NC-02b envelope'),
    'NF-28': Case('6b962e417f296626a318946af8760d9417f4ee1021578bc947110075baff68aa', 'HOLD',
                  b_all_nul_only, **HOLD_SOFT),
    'NF-29': Case('f75e1ae2abd2f9efab2cc716645f4a5c68c4c917bb515ddffab4e2cb900c4635', 'ABORT-RO (rule 1 wins)',
                  b_damaged_primary_future_backup, **ABORT),
    'NF-30': Case('90167953d9fb0d70493a494a0918cac7b59a3bf904bdf4996d77b8eb8ffaac1c', 'HOLD',
                  b_payload(DUP), **HOLD_SOFT),
    'NF-31': Case('bd92e55f7a1ee15ffd24f6925fcfa1968c259dfbe64e0190aae798f627f46810', 'HOLD',
                  b_payload(lambda d: d.replace('"at_ms":', '"at_ms":NaN,"x":', 1)), **HOLD_SOFT),
    'NF-32': Case('05193ecafb7f578f4de50c861895c8b18396dde46824f28986c8e370e2f92d16', 'HOLD',
                  b_payload(lambda d: d.replace('"sequence":6', '"sequence":' + '1' + '0' * 400, 1)), **HOLD_SOFT),
    'NF-33': Case('af2fe51ec2d912b0aac7488ec5ad6f2cffbe04e280cb44af7c98f418eca93300', 'HOLD (soft per D6)',
                  b_directory_at_primary, **HOLD_SOFT),
    'NF-34': Case('6fdb6cd60e91c499d278e338908cd5b02abf52d249cd9167a5c7b59f4740d0a7', 'HOLD hard, no partial',
                  b_torn_tail, **HOLD_HARD, fail=_fail_all(28)),
    'NF-35': Case('bfb0459d8a2926ed19fe3a3dea5404985dfff019cd93dd901f43eba9c709d47e', 'HOLD hard (5b)',
                  one_segment, **HOLD_HARD, fail=_fail_all(30)),
    'NF-37': Case('e6cd97f40ff8eb5857449b3f4f7ba7f0c1daddb9c9987a934e7dee8f7d3929ec', 'MANAGE (positive control)',
                  b_torn_tail, mode='PROCEED', unchanged='existing', writes=frozenset({'evidence_copy', 'tail_seal'}),
                  note='store proceeds; MANAGE itself needs reconciliation (NC-02b / S1)'),
    'NF-40': Case('f566ab2ae8966c74e9a5a3e7f18b198064bcd6f14a5910d37beb3bff365337e9', 'HOLD (A18)',
                  b_payload(lambda d: d.replace('"reason":', '"reasonX":', 1)), **HOLD_SOFT),
    'NF-41': Case('4aeb9cd03f23141899725d6555043a70732063e8ee287b1b2632124988e10b12', 'HOLD (A18)',
                  b_payload(lambda d: d.replace('"qty":"5"', '"qty":"0"', 1)), **HOLD_SOFT),
    'NF-42': Case('81d791df56fd83d9b9af351f2c3929fcc23b487ca6579af5e593d715da4ad12c', 'HOLD (A18)',
                  b_payload(lambda d: d.replace('"qty":"5"', '"qty":"1' + '0' * 300 + '"', 1)), **HOLD_SOFT),
    'NF-47': Case('ad45c54144efae47e55b053ac767abd2a0c672aa48838ab272cd98ec9882a15b', 'HOLD symbol/side (M41)',
                  b_bare_not_found_after_close_sent, mode='PROCEED',
                  note='journal side: the close stays UNKNOWN_NEEDS_QUERY and books nothing; the symbol/side HOLD is '
                       'the Runner\'s (S1)'),
}

DEFERRED = {
    'NF-03': 'NC-02b: settings live in the snapshot (design D13), not in the journal',
    'NF-04': 'NC-02b: settings live in the snapshot (design D13)',
    'NF-05': 'NC-02b: install marker = anchor / HEAD binding',
    'NF-08': 'NC-02b: candidate generation G-1 + evidence envelope (A07)',
    'NF-09': 'NC-02b: candidate generation G-1 + evidence envelope (A07)',
    'NF-10': 'NC-02b: HOLD snapshot across saves (generations)',
    'NF-11': 'NC-02b: trivially-empty candidate generation',
    'NF-12': 'NC-02b: settings record inside a generation (D13)',
    'NF-14': 'NC-02b: missing member with HEAD / binding present',
    'NF-15': 'NC-02b: binding identity (A11) and HEAD',
    'NF-16': 'NC-02b: INIT / HOLD-INIT and anchors (D2)',
    'NF-17': 'NC-02b: INIT / HOLD-INIT',
    'NF-18': 'installer row (I01-I03), not an NC-02 reader case',
    'NF-19': 'installer row (I01-I03), not an NC-02 reader case',
    'NF-20': 'installer row (I01-I03), not an NC-02 reader case',
    'NF-21': 'NC-02b: rule 0 legacy REJECT scan + INIT',
    'NF-26': 'NC-02b: a truncated SNAPSHOT is damage; a truncated journal tail is a torn tail (A15), covered here',
    'NF-36': 'NC-02b: complete uncommitted orphan generation needs the HEAD commit point',
    'NF-38': 'NC-02b: move-aside evidence linkage (evidence envelope)',
    'NF-39': 'NC-02b: interrupted INIT -> HOLD-INIT (the journal-level interrupted create is drilled here)',
    'NF-43': 'NC-02b: known-empty proof in snapshot provenance (the NC-02a fold is always UNKNOWN)',
    'NF-44': 'NC-02b / reconciliation: foreign exchange position and order (A22)',
    'NF-45': 'NC-02b / reconciliation: quantity delta without a result',
    'NF-46': 'NC-02b: marker binds another account (A11)',
    'NF-48': 'NC-02b: whole-store rollback needs the anchor / high-water (A03)',
    'NF-49': 'NC-02b: rule 0 legacy REJECT pass + HOLD-INIT',
}


def test_every_fixture_is_answered_or_deferred_with_a_reason():
    ids = {f'NF-{i:02d}' for i in range(1, 50)}
    assert set(CASES) | set(DEFERRED) == ids and not set(CASES) & set(DEFERRED)
    assert all(len(c.contract_sha) == 64 for c in CASES.values())


@pytest.mark.parametrize('fid', sorted(CASES))
def test_nf_fixture_analogue(fid, tmp_path):
    case = CASES[fid]
    os.mkdir(tmp_path / 'data')
    os.mkdir(tmp_path / 'data' / 'accounts')
    acct = str(tmp_path / 'data' / 'accounts' / ACCOUNT_ID)
    case.build(acct)
    for run in ('start', 'restart'):
        before = tree(str(tmp_path))
        fs = FaultFs(RealFs(), fail=case.fail, read_fail=case.read_fail)
        res = adapter(acct, fs)
        assert (res.mode, res.hold_kind) == (case.mode, case.hold_kind), (fid, run, res.recovery.findings)
        assert res.account_context_created == (case.mode != 'ABORT_RO')
        assert res.ownership == (None if case.mode == 'ABORT_RO' else 'unknown')
        after = tree(str(tmp_path))
        if run == 'start':
            assert res.writes == case.writes, (fid, res.writes)
        else:
            assert res.writes == frozenset(), (fid, 'a restart must not write again')
        if case.unchanged == 'all' or run == 'restart':
            assert after == before, fid
            if case.fail is None:                                                       # attempts that failed wrote nothing
                assert [op for _, op, _ in fs.trace if op not in ('open_append', 'lock')] == [], fid
        else:
            assert {k: v for k, v in after.items() if k in before} == before, fid      # existing bytes + mtimes kept
    if fid == 'NF-47':
        st = res.recovery.state
        c = INTENTS['close'].intent_id
        assert st.intent(c).recovery is IntentRecovery.UNKNOWN_NEEDS_QUERY and st.intent(c).final_result is None
        assert dict(st.booked) == {('SOLUSDT', 'LONG'): INTENTS['entry'].qty}            # the close books nothing


@pytest.mark.parametrize('fid', sorted(DEFERRED))
def test_deferred_fixture(fid):
    where = 'installer drill (section 4)' if fid in ('NF-18', 'NF-19', 'NF-20') else 'test_nc02b_nf_fixtures.py'
    pytest.skip(f'journal level: {DEFERRED[fid]}; answered in {where}')
