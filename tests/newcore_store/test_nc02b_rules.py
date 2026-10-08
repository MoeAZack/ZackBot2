"""NC-02b boot rules 0-4 on the store unit: legacy REJECT, ABORT_RO with zero writes in data\\ (M01-M05, M28, M29,
M32, D7, D8), rule 2 (writer history), rule 3 unreadable (M18, D6), rule 4 damage with evidence + candidate (M13, M14,
M16, M17, M19, M20, M36, M38), migration (M11, A13), interrupted INIT (M37, D9), R-KNOWN-EMPTY (M39, NF-43)."""
import dataclasses
import json
import os

import pytest

from nc02a_memfs import FaultFs, oserror
from nc02b_helpers import (ACCT, CIPHER, MEM_BASE, T, FakeExchange, account, data_files, mem, outside_files, owned)
from newcore.domain import Ownership, canonical_bytes
from newcore.store.envelope import read_envelope
from newcore.store.frame import (FILE_HEADER, KIND_HEAD, RT_BINDING, RT_SETTINGS, RT_SNAPSHOT, frame, record_at)
from newcore.store.header import canonical_json
from newcore.store.records import Reader, sha256_hex, snap_name
from newcore.store.slots import read_pair, slot_path, write_slot
from newcore.store.snapfile import decode_snapshot
from newcore.store.store import Mode, Paths, boot

P = Paths(MEM_BASE, ACCT)
DEC = 'dec_' + '9' * 32


def go(fs, ex, acct=None, reader=None, now=T):
    return boot(MEM_BASE, acct or account(), exchange=ex, now_ms=now, fs=fs, cipher=CIPHER, reader=reader)


def reconciled(checkpoints=1):
    """A MANAGED store owning a lot, with `checkpoints` extra MANAGED generations (G1 init_hold, G2 promotion, G3..)."""
    fs = mem()
    pf, pos, orders = owned()
    ex = FakeExchange(pos, orders)
    r = go(fs, ex)
    r.store.promote(ex, T, candidate=pf, owner_decision_id=DEC)
    for i in range(checkpoints):
        r.store.checkpoint(r.store.current.portfolio, T + 1 + i)
    r.store.journal.close()
    return fs, ex


def snap(fs, g):
    return os.path.join(P.snap, snap_name(g))


def head(fs):
    return read_pair(fs, P.account, 'HEAD', KIND_HEAD).doc


def records_of(raw):
    out, off = [], FILE_HEADER.size
    while off < len(raw):
        r = record_at(raw, off, max_len=64 * 1024 * 1024)
        out.append(r)
        off = r.end
    return out


def rewrite_record(fs, g, rtype, fn):
    """Replace one record of snapshot g (re-framed; the end hash and HEAD reference are NOT fixed: callers that need a
    consistent file use refix)."""
    raw = fs.read_bytes(snap(fs, g))
    out = raw[:FILE_HEADER.size]
    for r in records_of(raw):
        out += frame(r.rtype, fn(r.payload) if r.rtype == rtype else r.payload)
    fs.put(snap(fs, g), out)


def assert_abort_zero_writes(fs, ex, **kw):
    before_data, before_out = data_files(fs), {k: v for k, v in outside_files(fs).items() if 'anchors' in k}
    r = go(fs, ex, **kw)
    assert r.mode is Mode.ABORT_RO and not r.account_context, r.findings
    assert data_files(fs) == before_data                                     # zero writes in data\
    assert {k: v for k, v in outside_files(fs).items() if 'anchors' in k} == before_out    # no anchor write
    assert any(i['kind'] == 'abort_ro' and i['logged'] for i in r.incidents)  # out-of-band report
    assert go(fs, ex, **kw).mode is Mode.ABORT_RO and data_files(fs) == before_data          # NF-07: again
    return r


# ---------------------------------------------------------------------------------------------------- rule 1
def _future_settings(p):
    d = json.loads(p)
    d['format_version'] = 2
    return canonical_json(d)


def _future_nc01(p):
    return p.replace(b'"schema_version":1', b'"schema_version":2')


FUTURE = {
    'head_slot': lambda fs: write_slot(fs, P.account, 'HEAD', read_pair(fs, P.account, 'HEAD', KIND_HEAD).write_next,
                                       KIND_HEAD, {**head(fs), 'commit_seq': 99, 'min_reader_version': 2}),
    'current_snapshot_header': lambda fs: fs.put(snap(fs, 3), fs.read_bytes(snap(fs, 3))[:5] + b'\x02' +
                                                 fs.read_bytes(snap(fs, 3))[6:]),
    'retained_snapshot_settings': lambda fs: rewrite_record(fs, 2, RT_SETTINGS, _future_settings),     # NF-03 / 04
    'current_binding': lambda fs: rewrite_record(fs, 3, RT_BINDING, _future_nc01),                      # NF-05
    'current_portfolio': lambda fs: rewrite_record(fs, 3, RT_SNAPSHOT, _future_nc01),
    'orphan_snapshot': lambda fs: fs.put(snap(fs, 9), fs.read_bytes(snap(fs, 3))[:5] + b'\x07' +
                                         fs.read_bytes(snap(fs, 3))[6:]),                                # D7
    'newer_writer_in_head': lambda fs: write_slot(fs, P.account, 'HEAD',
                                                  read_pair(fs, P.account, 'HEAD', KIND_HEAD).write_next, KIND_HEAD,
                                                  {**head(fs), 'commit_seq': 99,
                                                   'high_water': {**head(fs)['high_water'], 'writer_seq': 9}}),  # D8
    'evidence_envelope': lambda fs: fs.put(os.path.join(P.account, 'evidence', 'x.zbe'),
                                           b'ZBNC\x06\x05\x00\x00' + b'\0' * 30),
    'journal_segment': lambda fs: fs.put(os.path.join(P.account, 'journal', 'seg-000001.seg'),
                                         fs.read_bytes(os.path.join(P.account, 'journal', 'seg-000001.seg'))[:5] +
                                         b'\x03' + fs.read_bytes(os.path.join(P.account, 'journal',
                                                                              'seg-000001.seg'))[6:]),
}


@pytest.mark.parametrize('what', sorted(FUTURE))
def test_any_future_member_aborts_read_only(what):
    fs, ex = reconciled()
    FUTURE[what](fs)
    assert_abort_zero_writes(fs, ex)


def test_rule_one_wins_over_damage_nf29():                                       # M29
    fs, ex = reconciled()
    fs.put(snap(fs, 3), fs.read_bytes(snap(fs, 3))[:300])                       # G damaged (truncated)
    rewrite_record(fs, 2, RT_SETTINGS, _future_settings)                         # G-1 future
    assert_abort_zero_writes(fs, ex)


def test_an_older_format_without_a_migration_path_aborts():
    fs, ex = reconciled()
    assert_abort_zero_writes(fs, ex, reader=Reader(snapshot_format=2))


# ---------------------------------------------------------------------------------------------------- migration
def test_an_older_format_migrates_into_a_new_hold_generation_then_promotes():  # M11, A13
    fs, ex = reconciled()
    old_files = set(fs.listdir(P.snap))

    def v1_to_v2(raw, account_id):
        return decode_snapshot(raw, Reader(snapshot_format=1), account_id=account_id)
    reader = Reader(snapshot_format=2, migrators={1: v1_to_v2})
    r = go(fs, ex, reader=reader)
    assert r.mode is Mode.HOLD and 'migration' in r.writes and r.candidate is not None
    h = head(fs)
    assert h['generation'] == 4 and set(fs.listdir(P.snap)) == old_files | {snap_name(4)}
    assert h['retained'][0]['generation'] == 3                                   # the old generation stays readable
    assert r.store.current.provenance['kind'] == 'migration' and r.store.current.header['format_version'] == 2
    ok = r.store.promote(ex, T)
    assert ok.mode is Mode.MANAGE
    r.store.journal.close()
    assert go(fs, ex, reader=reader).mode is Mode.MANAGE


# ---------------------------------------------------------------------------------------------------- rule 3
@pytest.mark.parametrize('how', ['read_error', 'directory'])
def test_an_unreadable_current_snapshot_is_hold_with_no_write(how):              # M18 / NF-13, NF-33, D6
    fs, ex = reconciled()
    target = snap(fs, 3)
    read_fail = None
    if how == 'read_error':
        read_fail = lambda op, p: oserror(13) if op == 'read_bytes' and os.path.normpath(p) == os.path.normpath(
            target) else None                                                    # noqa: E731
    else:
        del fs.files[os.path.normpath(target)]
        fs.dirs[os.path.normpath(target)] = True
    f = FaultFs(fs, read_fail=read_fail)
    before = data_files(fs)
    r = boot(MEM_BASE, account(), exchange=ex, now_ms=T, fs=f, cipher=CIPHER)
    assert r.mode is Mode.HOLD and r.reason.value == 'recovery.state_unreadable'
    assert data_files(fs) == before


# ---------------------------------------------------------------------------------------------------- rule 4
@pytest.mark.parametrize('damage', ['truncated', 'bitflip', 'trailing_nul', 'missing'])
def test_a_damaged_or_missing_current_generation_is_hold_with_evidence_and_candidate(damage):  # M13, M19, A04, A07
    fs, ex = reconciled()
    raw = fs.read_bytes(snap(fs, 3))
    if damage == 'truncated':
        fs.put(snap(fs, 3), raw[:200])
    elif damage == 'bitflip':
        fs.put(snap(fs, 3), raw[:400] + bytes([raw[400] ^ 1]) + raw[401:])
    elif damage == 'trailing_nul':
        fs.put(snap(fs, 3), raw + b'\0' * 512)
    else:
        del fs.files[os.path.normpath(snap(fs, 3))]
    damaged = None if damage == 'missing' else fs.read_bytes(snap(fs, 3))
    r = go(fs, ex)
    assert r.mode is Mode.HOLD and r.candidate_kind == 'G-1' and r.candidate is not None
    assert r.portfolio.ownership is Ownership.UNKNOWN and r.portfolio.positions is None
    if damaged is not None:
        assert fs.read_bytes(snap(fs, 3)) == damaged                              # never moved, never rewritten
        (ev,) = [e for e in r.evidence if e.rel_path == f'snap/{snap_name(3)}']
        meta, plain = read_envelope(fs.read_bytes(os.path.join(P.account, *ev.name.split('/'))), CIPHER, ACCT)
        assert plain == damaged and meta['incident_id'] and 'evidence_copy' in r.writes
    h = head(fs)
    assert h['generation'] == 4 and any(q['generation'] == 3 for q in h['quarantined']) or damage == 'missing'
    r.store.journal.close()
    before = data_files(fs)
    again = go(fs, ex)                                                           # A05: still HOLD, nothing new
    assert again.mode is Mode.HOLD and data_files(fs) == before
    ok = again.store.promote(ex, T)                                              # the candidate matches: A08
    assert ok.mode is Mode.MANAGE


def test_both_generations_damaged_is_hold_without_a_candidate():                 # M16 / NF-10
    fs, ex = reconciled()
    for g in (2, 3):
        raw = fs.read_bytes(snap(fs, g))
        fs.put(snap(fs, g), raw[:400] + bytes([raw[400] ^ 1]) + raw[401:])
    r = go(fs, ex)
    assert r.mode is Mode.HOLD and r.candidate is None and r.portfolio.ownership is Ownership.UNKNOWN


def test_a_journal_behind_its_snapshot_is_damage():                              # journal rolled back / truncated
    fs, ex = reconciled()
    r = go(fs, ex)
    st = r.store
    from newcore.store.records import provenance
    st.commit(st.current.portfolio, provenance('checkpoint', 'managed', from_generation=3), T + 50, lsn_upto=5)
    st.journal.close()
    h = go(fs, ex)
    assert h.mode is Mode.HOLD and any('journal behind its snapshot' in i.ref for i in h.items)


def test_head_lost_while_generations_exist_is_hold_nf15():                       # M20 / A11
    fs, ex = reconciled()
    for w in 'ab':
        p = slot_path(P.account, 'HEAD', w)
        if fs.kind(p) == 'file':
            del fs.files[os.path.normpath(p)]
    r = go(fs, ex)
    assert r.mode is Mode.HOLD and any('HEAD missing' in i.ref for i in r.items)


def test_the_store_binding_differs_from_the_configured_one_nf46():                # M40
    fs, ex = reconciled()
    from nc02b_helpers import OTHER_DIGEST
    r = go(fs, FakeExchange(ex.positions, ex.orders, digest=OTHER_DIGEST), account(OTHER_DIGEST))
    assert r.mode is Mode.HOLD and any(i.cause == 'identity' for i in r.items)


# ---------------------------------------------------------------------------------------------------- rule 8 / D9
def test_an_interrupted_init_is_rerun_nf39():                                   # M37
    fs = mem()
    pf, pos, orders = owned()
    ex = FakeExchange(pos, orders)
    f = FaultFs(fs, fail=lambda op, p, i: oserror(5) if os.path.basename(p) == 'HEAD.a' else None)
    r = boot(MEM_BASE, account(), exchange=ex, now_ms=T, fs=f, cipher=CIPHER)    # G1 written, HEAD never
    assert r.mode is Mode.HOLD_INIT and r.hold_kind.value == 'durability_unavailable'
    assert fs.listdir(P.snap) == [snap_name(1)]
    r2 = go(fs, ex)                                                              # D9: re-INIT, G1 kept as orphan
    assert r2.mode is Mode.HOLD_INIT and head(fs)['generation'] == 2
    assert any('orphan' in f for f in r2.findings)
    assert r2.store.promote(ex, T).mode is Mode.HOLD_INIT                        # confirming alone never clears it


# ---------------------------------------------------------------------------------------------------- R-KNOWN-EMPTY
def test_an_unproven_empty_checkpoint_after_history_is_hold_nf43():              # M39
    fs, ex = reconciled(0)
    r = go(fs, ex)
    st = r.store
    lot_pf = st.current.portfolio
    empty = dataclasses.replace(lot_pf, ownership=Ownership.KNOWN_EMPTY, positions=(), intents=(), entry_stops=(),
                                proof=dataclasses.replace(lot_pf.proof, kind=type(lot_pf.proof.kind)('flat_snapshot'),
                                                          key_digest=st.account.binding.key_digest))
    st.checkpoint(empty, T + 9)                                                  # a "clean" empty G, no result chain
    st.journal.close()
    flat = FakeExchange()
    h = go(fs, flat)
    assert h.mode is Mode.HOLD and any(i.cause == 'trivially_empty' for i in h.items)
    assert h.store.promote(flat, T).mode is Mode.HOLD                            # never automatically


def test_a_hard_hold_boot_hands_over_a_read_only_journal_view():                # S3 runner, A23 / A24 pricing
    fs, ex = reconciled()
    data_root = os.path.normpath(os.path.join(MEM_BASE, 'data'))
    ro = FaultFs(fs, fail=lambda op, p, i: oserror(30) if os.path.normpath(p).startswith(data_root) else None)
    r = boot(MEM_BASE, account(), exchange=ex, now_ms=T, fs=ro, cipher=CIPHER)
    assert r.mode is Mode.HOLD and r.hold_kind.value == 'durability_unavailable'
    assert r.view is not None and r.view.readonly and r.view.last_sequence() == 0
    with pytest.raises(Exception):
        r.view.append(object())


def test_legacy_files_are_rejected_untouched_and_init_proceeds_nf49():           # M26, A14, D15
    fs = mem()
    data = os.path.join(MEM_BASE, 'data')
    for n in ('state.json', 'state.json.bak', 'settings.json', 'install.json', 'state.json.4242-1111.tmp',
              'state.json.corrupt-20261008T000000Z'):
        fs.put(os.path.join(data, n), b'{"legacy": "' + n.encode() + b'"}')
    legacy = {k: v for k, v in fs.snapshot().items() if os.path.dirname(k) == os.path.normpath(data)}
    pf, pos, orders = owned()
    r = go(fs, FakeExchange(pos, orders))
    assert r.mode is Mode.HOLD_INIT and len(r.legacy) == 6
    assert {k: v for k, v in fs.snapshot().items() if os.path.dirname(k) == os.path.normpath(data) and k in legacy} \
        == legacy
    assert any(i['kind'] == 'legacy_rejected' for i in r.incidents)
    log = fs.read_bytes(os.path.join(MEM_BASE, 'incidents', 'boot.jsonl'))
    assert b'legacy' not in log.replace(b'legacy_rejected', b'') or b'"legacy": "' not in log   # hashes only
    assert canonical_bytes                                                       # (import kept for the module)
