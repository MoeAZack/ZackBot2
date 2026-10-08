"""NF fixtures for NC-02b (prep/nc02-negative-fixtures @ a6364de; fixture_runner_skeleton.py, acceptance draft r4 5.1).

Two passes on the REAL file system (tmp dirs only; the production DPAPI cipher and DACL):

a. REJECT (5.1a, every fixture except the installer rows NF-18..20): the fixture's legacy file names sit in data\\ (with
   its layout: NF-33's state.json is a directory; and its inject: NF-13 read error, NF-34 ENOSPC, NF-35 EROFS on every
   write in data\\). NEWCORE must REJECT them - bytes, mtimes and listing unchanged, reported once out-of-band, never
   read as state - and INIT per exchange: flat -> MANAGE known-empty, non-flat -> HOLD-INIT; an unwritable data folder
   -> INIT_WAIT (flat) or hard HOLD-INIT (non-flat).
b. SCENARIO (5.1b): a ScenarioBuilder writes the fixture's NEWCORE store with the store's own API (generations G1 init,
   G2 promotion, G3 checkpoint = "G"; G2 = "G-1"), applies the fixture's damage, boots twice (start + restart) and checks
   the skeleton's expected outcome: mode, hold kind, ownership, evidence, candidate, writes, untouched members.
The 26 fixtures NC-02a deferred are answered here (NF-18..20 stay installer rows); the 23 NC-02a answered at journal
level are re-run at store level where the store adds a member (NF-03/04/05 settings / binding, NF-48 anchor).
"""
import dataclasses
import hashlib
import os
import shutil
import sys
from dataclasses import dataclass
from decimal import Decimal as D

import pytest

from nc02a_memfs import FaultFs, oserror
from nc02a_util import tree
from nc02b_helpers import ACCT, OTHER_DIGEST, T, FakeExchange, account, owned
from newcore.domain import Ownership
from newcore.store import RealFs
from newcore.store.envelope import write_envelope
from newcore.store.frame import FILE_HEADER, RT_BINDING, RT_SETTINGS, frame, record_at
from newcore.store.header import canonical_json
from newcore.store.reconcile import ExOrder, ExPosition
from newcore.store.records import provenance, snap_name
from newcore.store.store import Mode, boot

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='the production evidence cipher is DPAPI (Windows)')
DEC = 'dec_' + '7' * 32
INSTALLER = ('NF-18', 'NF-19', 'NF-20')

# id -> (contract_sha prefix, legacy input names, layout, inject, exchange flat?)
NF = {
    'NF-01': ('7f3561b93dacf816', 6, {}, None, False), 'NF-02': ('0e27f17b8e4ae2b6', 6, {}, None, False),
    'NF-03': ('5d02db9ef87b061c', 6, {}, None, False), 'NF-04': ('973da082c07a9690', 6, {}, None, False),
    'NF-05': ('8555ef7e5f214417', 6, {}, None, False), 'NF-06': ('327ed907cd406bd5', 6, {}, None, False),
    'NF-07': ('16a09d06cf69471d', 6, {}, None, False), 'NF-08': ('c1f5cafee265b792', 6, {}, None, False),
    'NF-09': ('4d491dfe4ee12514', 6, {}, None, False), 'NF-10': ('3da4f05ca0ff7e38', 6, {}, None, False),
    'NF-11': ('60606ad4df379252', 6, {}, None, False), 'NF-12': ('4469a89bc63dfd64', 6, {}, None, False),
    'NF-13': ('274bcd99be111951', 6, {}, 'read', False), 'NF-14': ('5414c4c6f43e2eee', 4, {}, None, False),
    'NF-15': ('73b9e3f62ba10c0e', 2, {}, None, False), 'NF-16': ('7bf8c616313e94ff', 0, {}, None, False),
    'NF-17': ('bcc8dbc51471e095', 1, {}, None, False), 'NF-21': ('7466ebd77d9e9cbb', 2, {}, None, False),
    'NF-22': ('a308344fe6200ffc', 5, {}, None, False), 'NF-23': ('1d5d5834ec4de4fd', 6, {}, None, False),
    'NF-24': ('08f61e17eaa403f7', 6, {}, None, False), 'NF-25': ('bff26d7d96e2f37f', 6, {}, None, False),
    'NF-26': ('f66b32bf5898100e', 5, {}, None, False), 'NF-27': ('df99ea585009bf09', 6, {}, None, False),
    'NF-28': ('6b962e417f296626', 5, {}, None, False), 'NF-29': ('f75e1ae2abd2f9ef', 6, {}, None, False),
    'NF-30': ('90167953d9fb0d70', 6, {}, None, False), 'NF-31': ('bd92e55f7a1ee15f', 6, {}, None, False),
    'NF-32': ('05193ecafb7f578f', 6, {}, None, False), 'NF-33': ('af2fe51ec2d912b0', 5, {'state.json': 'dir'}, None,
                                                                False),
    'NF-34': ('6fdb6cd60e91c499', 6, {}, 28, False), 'NF-35': ('bfb0459d8a2926ed', 6, {}, 30, False),
    'NF-36': ('0f57c21c7a9b6568', 7, {}, None, True), 'NF-37': ('e6cd97f40ff8eb58', 7, {}, None, False),
    'NF-38': ('b0e2c4f63cad126c', 6, {}, None, False), 'NF-39': ('36d3ba20e74831da', 2, {}, None, False),
    'NF-40': ('f566ab2ae8966c74', 6, {}, None, False), 'NF-41': ('4aeb9cd03f231418', 6, {}, None, False),
    'NF-42': ('81d791df56fd83d9', 6, {}, None, False), 'NF-43': ('a76675e99081f815', 6, {}, None, True),
    'NF-44': ('4b48cbb827832b8b', 6, {}, None, False), 'NF-45': ('080bb31e6ed113e3', 6, {}, None, False),
    'NF-46': ('e64e28287703a1c4', 6, {}, None, False), 'NF-47': ('ad45c54144efae47', 6, {}, None, False),
    'NF-48': ('85aaad65e64efd33', 6, {}, None, False), 'NF-49': ('abc9a2ca59f742a6', 6, {}, None, False),
}
NAMES = {
    0: [], 1: ['settings.json'], 2: ['settings.json', 'state.json'],
    4: ['install.json', 'install.json.bak', 'settings.json', 'settings.json.bak'],
    5: ['install.json', 'install.json.bak', 'settings.json', 'settings.json.bak', 'state.json'],
    6: ['install.json', 'install.json.bak', 'settings.json', 'settings.json.bak', 'state.json', 'state.json.bak'],
    7: ['install.json', 'install.json.bak', 'settings.json', 'settings.json.bak', 'state.json',
         'state.json.4242-1111.tmp', 'state.json.bak'],
}
SPECIAL_NAMES = {'NF-15': ['settings.json', 'settings.json.bak'], 'NF-33': ['install.json', 'install.json.bak',
                 'settings.json', 'settings.json.bak', 'state.json.bak'],
                 'NF-38': ['install.json', 'install.json.bak', 'settings.json', 'settings.json.bak', 'state.json.bak',
                           'state.json.corrupt-20261008T000000Z']}


def names_of(fid):
    return SPECIAL_NAMES.get(fid) or NAMES[NF[fid][1]]


def exchange_for(fid):
    pf, pos, orders = owned()
    if NF[fid][4]:
        return FakeExchange()
    if fid == 'NF-39':
        return FakeExchange([ExPosition('ETHUSDT', 'SHORT', D('0.5'))], [])
    return FakeExchange(pos, orders)


def data_dir(base):
    return os.path.join(base, 'data')


def mutate_fail(base, code):
    root = os.path.normcase(os.path.normpath(data_dir(base)))
    return lambda op, p, i: oserror(code) if os.path.normcase(os.path.normpath(p)).startswith(root) else None


# ---------------------------------------------------------------------------------------------------- pass a: REJECT
@pytest.mark.parametrize('fid', sorted(f for f in NF if f not in INSTALLER))
def test_reject_pass_legacy_files_are_never_read_or_touched_then_init(fid, tmp_path):
    base = str(tmp_path / 'base')
    os.makedirs(data_dir(base))
    legacy = names_of(fid)
    for n in legacy:
        with open(os.path.join(data_dir(base), n), 'wb') as fh:
            fh.write(f'{fid}:{n}:legacy bytes'.encode())
        os.utime(os.path.join(data_dir(base), n), ns=(1_791_400_000_000_000_000,) * 2)
    for n, kind in NF[fid][2].items():
        os.mkdir(os.path.join(data_dir(base), n))
    inject = NF[fid][3]
    fail = mutate_fail(base, inject) if isinstance(inject, int) else None
    read_fail = (lambda op, p: oserror(13) if op == 'read_bytes' and p.endswith('state.json') else None) \
        if inject == 'read' else None
    before = {k: v for k, v in tree(base).items() if k.startswith('data/') and k.count('/') == 1}
    ex = exchange_for(fid)
    for run in ('start', 'restart'):
        fs = FaultFs(RealFs(), fail=fail, read_fail=read_fail)
        r = boot(base, account(), exchange=ex, now_ms=T, fs=fs)
        after = {k: v for k, v in tree(base).items() if k.startswith('data/') and k.count('/') == 1
                 and k.split('/')[1] in set(legacy) | set(NF[fid][2])}
        assert after == {k: v for k, v in before.items()}, (fid, run)              # bytes, mtimes, listing
        assert sorted(x.name for x in r.legacy) == sorted(set(legacy) | set(NF[fid][2])), (fid, r.legacy)
        if NF[fid][4]:
            want = Mode.INIT_WAIT if fail else Mode.MANAGE
        else:
            want = Mode.HOLD_INIT
        assert r.mode is want, (fid, run, r.mode, r.items)
        if want is Mode.MANAGE:
            assert r.portfolio.ownership is Ownership.KNOWN_EMPTY
        if want is Mode.HOLD_INIT:
            assert (r.hold_kind.value == 'durability_unavailable') == bool(fail), fid
            assert r.store is None or r.store.current.portfolio.positions is None   # unknown, never empty
        if r.store is not None and r.store.journal is not None:
            r.store.journal.close()


# ---------------------------------------------------------------------------------------------------- pass b builders
def _rd(p):
    with open(p, 'rb') as fh:
        return fh.read()


def _wr(p, data):
    with open(p, 'wb') as fh:
        fh.write(data)


def acct_dir(base):
    return os.path.join(data_dir(base), 'accounts', ACCT)


def snapp(base, g):
    return os.path.join(acct_dir(base), 'snap', snap_name(g))


def _close(r):
    if r.store is not None and r.store.journal is not None:
        r.store.journal.close()


def reconciled(base, *, empty_first=False):
    """G1 init, G2 promotion (owner adopts the exchange position), G3 checkpoint. empty_first: G1 is a flat INIT,
    G2 the HOLD the position caused, G3 the promotion."""
    pf, pos, orders = owned()
    ex = FakeExchange(pos, orders)
    if empty_first:
        _close(boot(base, account(), exchange=FakeExchange(), now_ms=T))
        r = boot(base, account(), exchange=ex, now_ms=T)
        r.store.promote(ex, T, candidate=pf, owner_decision_id=DEC)
    else:
        r = boot(base, account(), exchange=ex, now_ms=T)
        r.store.promote(ex, T, candidate=pf, owner_decision_id=DEC)
        r.store.checkpoint(r.store.current.portfolio, T + 1)
    _close(r)
    return ex


def _rewrite(base, g, rtype, fn):
    raw = _rd(snapp(base, g))
    out, off = raw[:FILE_HEADER.size], FILE_HEADER.size
    while off < len(raw):
        r = record_at(raw, off, max_len=64 * 1024 * 1024)
        out += frame(r.rtype, fn(r.payload) if r.rtype == rtype else r.payload)
        off = r.end
    _wr(snapp(base, g), out)


def _future_settings(p):
    import json
    d = json.loads(p)
    d['format_version'] = 2
    return canonical_json(d)


def b_settings_future_current(base):                    # NF-03
    reconciled(base)
    _rewrite(base, 3, RT_SETTINGS, _future_settings)


def b_settings_future_both(base):                       # NF-04
    reconciled(base)
    for g in (2, 3):
        _rewrite(base, g, RT_SETTINGS, _future_settings)


def b_binding_future(base):                             # NF-05
    reconciled(base)
    _rewrite(base, 3, RT_BINDING, lambda p: p.replace(b'"schema_version":1', b'"schema_version":2'))


def b_g_truncated(base):                                # NF-08
    reconciled(base)
    _wr(snapp(base, 3), _rd(snapp(base, 3))[:300])


def b_g_schema_invalid(base):                           # NF-09: a lot quantity that is not a decimal
    reconciled(base)
    raw = _rd(snapp(base, 3))
    _wr(snapp(base, 3), raw.replace(b'"qty":"1"', b'"qty":"abc"', 1))


def b_g_and_g1_invalid(base):                           # NF-10
    reconciled(base)
    for g in (2, 3):
        raw = _rd(snapp(base, g))
        _wr(snapp(base, g), raw.replace(b'"qty":"1"', b'"qty":"abc"', 1))


def b_g_truncated_g1_empty(base):                       # NF-11
    reconciled(base, empty_first=True)
    _wr(snapp(base, 3), _rd(snapp(base, 3))[:300])


def b_settings_damaged_both(base):                      # NF-12
    reconciled(base)
    for g in (2, 3):
        _rewrite(base, g, RT_SETTINGS, lambda p: p[:len(p) // 2])


def b_all_generations_missing(base):                    # NF-14
    reconciled(base)
    for g in (1, 2, 3):
        os.remove(snapp(base, g))


def b_head_missing(base):                               # NF-15
    reconciled(base)
    for w in 'ab':
        p = os.path.join(acct_dir(base), f'HEAD.{w}')
        if os.path.exists(p):
            os.remove(p)


def b_empty(base):                                      # NF-16 (no anchor either)
    pass


def b_only_generation_truncated(base):                  # NF-26
    pf, pos, orders = owned()
    _close(boot(base, account(), exchange=FakeExchange(pos, orders), now_ms=T))
    _wr(snapp(base, 1), _rd(snapp(base, 1))[:300])


def b_complete_orphan_newer(base):                      # NF-36: uncommitted complete G+1 (lot closed)
    reconciled(base)
    r = boot(base, account(), exchange=exchange_for('NF-37'), now_ms=T)
    st = r.store
    empty = _empty_like(st)
    raw_before = {g: _rd(snapp(base, g)) for g in (1, 2, 3)}
    st.checkpoint(empty, T + 5)                          # G4 committed ...
    _close(r)
    g4 = _rd(snapp(base, 4))
    _restore_head_to(base, 3)                            # ... then HEAD rolled back: G4 is an uncommitted orphan
    assert {g: _rd(snapp(base, g)) for g in (1, 2, 3)} == raw_before and g4


def b_torn_orphan(base):                                # NF-37
    reconciled(base)
    _wr(snapp(base, 9), _rd(snapp(base, 3))[:150])


def b_g_missing_evidence_present(base):                 # NF-38: crash after a copy of G into the envelope
    reconciled(base)
    raw = _rd(snapp(base, 3))
    write_envelope(RealFs(), acct_dir(base), ACCT, raw, source='damaged_member', rel_path=f'snap/{snap_name(3)}',
                   incident_id='inc-legacy-move-aside')
    os.remove(snapp(base, 3))


def b_interrupted_init(base):                           # NF-39
    fs = FaultFs(RealFs(), fail=lambda op, p, i: oserror(5) if os.path.basename(p) == 'HEAD.a' else None)
    _close(boot(base, account(), exchange=exchange_for('NF-39'), now_ms=T, fs=fs))


def b_unproven_empty(base):                             # NF-43
    reconciled(base)
    r = boot(base, account(), exchange=exchange_for('NF-37'), now_ms=T)
    r.store.checkpoint(_empty_like(r.store), T + 5)
    _close(r)


def b_intact(base):                                     # NF-44 / 45 / 46: the exchange / binding differ
    reconciled(base)


def b_rollback(base):                                   # NF-48: the whole data folder replaced by an older copy
    reconciled(base)
    old = os.path.join(os.path.dirname(base), 'old-data')
    shutil.copytree(data_dir(base), old)
    r = boot(base, account(), exchange=exchange_for('NF-37'), now_ms=T)
    r.store.checkpoint(r.store.current.portfolio, T + 7)
    _close(r)
    shutil.rmtree(data_dir(base))
    shutil.copytree(old, data_dir(base))


def _empty_like(st):
    pf = st.current.portfolio
    return dataclasses.replace(pf, ownership=Ownership.KNOWN_EMPTY, positions=(), intents=(), entry_stops=(),
                               proof=dataclasses.replace(pf.proof, kind=type(pf.proof.kind)('flat_snapshot'),
                                                         key_digest=st.account.binding.key_digest))


def _restore_head_to(base, g):
    from newcore.store.frame import KIND_HEAD
    from newcore.store.slots import read_pair, write_slot
    fs = RealFs()
    pair = read_pair(fs, acct_dir(base), 'HEAD', KIND_HEAD)
    doc = dict(pair.doc)
    prev = next(r for r in doc['retained'] if r['generation'] == g)
    doc.update(commit_seq=doc['commit_seq'] + 1, generation=g, snapshot=prev,
               retained=[r for r in doc['retained'] if r['generation'] < g],
               high_water={'generation': g, 'writer_seq': doc['high_water']['writer_seq']})
    write_slot(fs, acct_dir(base), 'HEAD', pair.write_next, KIND_HEAD, doc)
    anchors = os.path.join(base, 'anchors')
    for w in 'ab':
        p = os.path.join(anchors, f'{ACCT}.{w}')
        if os.path.exists(p):
            os.remove(p)                                 # the anchor never saw G4 committed in this scenario


@dataclass(frozen=True)
class Case:
    build: object
    mode: Mode
    hold: str | None = None
    ownership: str | None = None
    evidence: bool | None = None
    candidate: str | None | tuple = ('G-1', 'trivially_empty', 'current', None)
    exchange: object = None
    acct: object = None
    protected: tuple = ()             # snapshot generations that must stay byte-identical


ABORT = dict(mode=Mode.ABORT_RO)
HOLD_DAMAGE = dict(mode=Mode.HOLD, hold='normal', ownership='unknown', evidence=True)
HOLD_PLAIN = dict(mode=Mode.HOLD, hold='normal')
CASES = {
    'NF-03': Case(b_settings_future_current, **ABORT),
    'NF-04': Case(b_settings_future_both, **ABORT),
    'NF-05': Case(b_binding_future, **ABORT),
    'NF-08': Case(b_g_truncated, **HOLD_DAMAGE, candidate='G-1', protected=(3,)),
    'NF-09': Case(b_g_schema_invalid, **HOLD_DAMAGE, candidate='G-1', protected=(3,)),
    'NF-10': Case(b_g_and_g1_invalid, **HOLD_DAMAGE, candidate=None, protected=(2, 3)),
    'NF-11': Case(b_g_truncated_g1_empty, **HOLD_DAMAGE, candidate='trivially_empty', protected=(3,)),
    'NF-12': Case(b_settings_damaged_both, **HOLD_DAMAGE, protected=(2, 3)),
    'NF-14': Case(b_all_generations_missing, mode=Mode.HOLD, hold='normal', ownership=None),
    'NF-15': Case(b_head_missing, mode=Mode.HOLD, hold='normal'),
    'NF-16': Case(b_empty, mode=Mode.HOLD_INIT, hold='normal', ownership='unknown'),
    'NF-17': Case(b_empty, mode=Mode.HOLD_INIT, hold='normal', ownership='unknown'),
    'NF-21': Case(b_empty, mode=Mode.HOLD_INIT, hold='normal', ownership='unknown'),
    'NF-26': Case(b_only_generation_truncated, **HOLD_DAMAGE, candidate=None, protected=(1,)),
    'NF-36': Case(b_complete_orphan_newer, mode=Mode.HOLD, hold='normal', protected=(4,)),
    'NF-37': Case(b_torn_orphan, mode=Mode.MANAGE, ownership='known'),
    'NF-38': Case(b_g_missing_evidence_present, mode=Mode.HOLD, hold='normal', ownership='unknown', candidate='G-1'),
    'NF-39': Case(b_interrupted_init, mode=Mode.HOLD_INIT, hold='normal', ownership='unknown'),
    'NF-43': Case(b_unproven_empty, mode=Mode.HOLD, hold='normal'),
    'NF-44': Case(b_intact, mode=Mode.HOLD, hold='normal'),
    'NF-45': Case(b_intact, mode=Mode.HOLD, hold='normal'),
    'NF-46': Case(b_intact, mode=Mode.HOLD, hold='normal'),
    'NF-48': Case(b_rollback, mode=Mode.HOLD, hold='normal'),
    'NF-49': Case(b_empty, mode=Mode.HOLD_INIT, hold='normal', ownership='unknown'),     # + the REJECT pass
}


def scenario_exchange(fid):
    pf, pos, orders = owned()
    if fid == 'NF-44':
        return FakeExchange(pos + [ExPosition('ETHUSDT', 'SHORT', D('0.5'))],
                            orders + [ExOrder('o:99', 'ETHUSDT', 'SHORT', D('0.5'), True, 'stop', 'working')])
    if fid == 'NF-45':
        return FakeExchange([ExPosition('BTCUSDT', 'LONG', D('0.6'))], orders)
    if fid == 'NF-46':
        return FakeExchange(pos, orders, digest=OTHER_DIGEST)
    return exchange_for(fid)


def test_every_fixture_is_answered_somewhere():
    from test_nc02a_nf_fixtures import CASES as NC02A, DEFERRED
    answered = set(NC02A) | set(CASES)
    assert set(DEFERRED) - set(CASES) == set(INSTALLER)
    assert answered | set(INSTALLER) == {f'NF-{i:02d}' for i in range(1, 50)}


@pytest.mark.parametrize('fid', sorted(CASES))
def test_scenario_pass(fid, tmp_path):
    case = CASES[fid]
    base = str(tmp_path / 'base')
    os.makedirs(data_dir(base))
    case.build(base)
    acct = account(OTHER_DIGEST) if fid == 'NF-46' else account()
    ex = scenario_exchange(fid)
    protected = {g: _rd(snapp(base, g)) for g in case.protected if os.path.exists(snapp(base, g))}
    for run in ('start', 'restart'):
        before = tree(base)
        r = boot(base, acct, exchange=ex, now_ms=T)
        after = tree(base)
        assert r.mode is case.mode, (fid, run, r.mode, r.items, r.findings)
        if case.hold is not None:
            assert r.hold_kind is not None and r.hold_kind.value == case.hold, (fid, r.hold_kind)
        if case.ownership is not None and r.portfolio is not None:
            assert str(r.portfolio.ownership) == case.ownership, (fid, r.portfolio.ownership)
        if case.mode is Mode.ABORT_RO:
            assert {k: v for k, v in after.items() if not k.startswith('incidents')} == \
                {k: v for k, v in before.items() if not k.startswith('incidents')}, fid   # zero writes, anchors too
        if case.evidence is True and run == 'start':
            assert r.evidence and 'evidence_copy' in r.writes, fid
            for e in r.evidence:
                raw = _rd(os.path.join(acct_dir(base), *e.name.split('/')))
                assert hashlib.sha256(raw).hexdigest() != e.sha256                   # only ciphertext on disk
        if not isinstance(case.candidate, tuple):
            assert r.candidate_kind == case.candidate, (fid, r.candidate_kind)
        for g, raw in protected.items():
            assert _rd(snapp(base, g)) == raw, (fid, g)                              # damaged members untouched
        if run == 'restart' and case.mode is not Mode.ABORT_RO:
            changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)
                       and not k.startswith(('incidents', 'anchors'))}
            assert not changed, (fid, changed)                                        # A05: no new write on restart
        _close(r)
    if case.mode in (Mode.HOLD,) and fid in ('NF-08', 'NF-09'):
        r = boot(base, acct, exchange=ex, now_ms=T)
        assert r.store.promote(ex, T).mode is Mode.MANAGE                             # A08 with the G-1 candidate
        _close(r)


def test_scenario_cases_cover_the_twenty_three():
    assert len(CASES) == 24 and not set(CASES) & set(INSTALLER)              # the 23 deferred + NF-37
    assert all(len(NF[f][0]) == 16 for f in NF)
    assert provenance                                                                 # (module import kept)
