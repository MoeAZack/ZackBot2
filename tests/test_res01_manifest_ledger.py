"""R1: tools/research/manifest.py (zb-data-manifest/1) and tools/research/ledger.py (zb-ledger/1).

Small deterministic fixtures written to tmp_path; the committed legacy manifest and seeded family ledgers are checked
against the repo."""
import hashlib
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import ledger as L                                                                          # noqa: E402
import manifest as M                                                                        # noqa: E402

RES = os.path.join(ROOT, 'research_evidence')
W = {'start': '2025-01-01T00:00:00Z', 'end': '2025-07-01T00:00:00Z'}
RD = 'a' * 64
MD = 'b' * 64
ED = 'e' * 64
REAL_RECOMPUTE = L.recompute_run_digest


def fixture_root(tmp_path):
    d = tmp_path / 'fx' / '4h'
    d.mkdir(parents=True)
    (d / 'BTCUSDT_4h.csv').write_bytes(b't,o,h,l,c,v\n2025-01-01 00:00:00,1,2,0.5,1.5,10\n'
                                       b'2025-01-01 04:00:00,1.5,2,1,1.2,11\n')
    (d / 'ETHUSDT_4h.csv').write_bytes(b't,o,h,l,c,v\n2025-01-01 00:00:00,3,4,2,3.5,7\n')
    (d / 'notes.txt').write_bytes(b'ignored\n')
    return tmp_path


def build(base):
    return M.build(['fx'], manifest_id='fixture-v1', source_class='fixture', base=str(base), accessed_cairo=None)


# ---------------------------------------------------------------- manifest

def test_manifest_fields_and_pit_rule(tmp_path):
    m = build(fixture_root(tmp_path))
    assert [f['path'] for f in m['files']] == ['fx/4h/BTCUSDT_4h.csv', 'fx/4h/ETHUSDT_4h.csv']
    btc = m['files'][0]
    assert (btc['symbol'], btc['interval'], btc['rows']) == ('BTCUSDT', '4h', 2)
    assert btc['first_open_ms'] == 1735689600000 and btc['last_open_ms'] == 1735689600000 + 4 * 3_600_000
    assert btc['sha256'] == hashlib.sha256((tmp_path / 'fx/4h/BTCUSDT_4h.csv').read_bytes()).hexdigest()
    assert m['available_rule'] == 'open_ms + interval_ms' and m['format'] == 'zb-data-manifest/1'
    assert m['data_root'] == 'repo' and str(tmp_path) not in json.dumps(m)      # logical root, no machine path


def test_manifest_digest_is_deterministic_and_pinned(tmp_path):
    a, b = build(fixture_root(tmp_path / 'a')), build(fixture_root(tmp_path / 'b'))
    assert a['digest'] == b['digest'] == M.digest_of(a)
    # golden: any change to the schema or canonical form must change this pin deliberately
    assert a['digest'] == '7653c501b4846c4304667ae66ce43f3d29d33509549936ef3e6f91ee82f3e30d'


def test_manifest_validate_rejects_tamper(tmp_path):
    m = build(fixture_root(tmp_path))
    for mutate in (lambda x: x['files'][0].__setitem__('rows', 3),
                   lambda x: x.__setitem__('survivor_only', False),
                   lambda x: x['files'].reverse(),
                   lambda x: x.__setitem__('extra', 1),
                   lambda x: x['files'][0].__setitem__('symbol', 'ETHUSDT')):
        bad = json.loads(json.dumps(m))
        mutate(bad)
        with pytest.raises(M.ManifestError):
            M.validate(bad)


def test_manifest_verify_detects_changed_bytes(tmp_path):
    base = fixture_root(tmp_path)
    m = build(base)
    assert M.verify(m, str(base)) == []
    p = base / 'fx/4h/ETHUSDT_4h.csv'
    p.write_bytes(p.read_bytes() + b'2025-01-01 04:00:00,3,4,2,3.5,7\n')
    assert any('sha256' in x for x in M.verify(m, str(base)))
    os.remove(p)
    assert M.verify(m, str(base)) == ['fx/4h/ETHUSDT_4h.csv: missing']


def test_manifest_write_is_immutable(tmp_path):
    base = fixture_root(tmp_path)
    m = build(base)
    out = str(tmp_path / 'm.json')
    M.write(m, out)
    M.write(m, out)                                           # identical bytes: no-op
    other = M.build(['fx'], manifest_id='fixture-v2', source_class='fixture', base=str(base))
    with pytest.raises(M.ManifestError, match='immutable'):
        M.write(other, out)
    assert M.load(out) == m


@pytest.mark.parametrize('bad', ['', 'fx//BTCUSDT_4h.csv', './fx/BTCUSDT_4h.csv', 'fx/./BTCUSDT_4h.csv',
                                 '../outside/BTCUSDT_4h.csv', 'fx/../../BTCUSDT_4h.csv', '/fx/BTCUSDT_4h.csv',
                                 'C:/fx/BTCUSDT_4h.csv', 'C:fx/BTCUSDT_4h.csv', '//server/share/BTCUSDT_4h.csv',
                                 'fx' + chr(92) + 'BTCUSDT_4h.csv', 'fx/BTC' + chr(0) + 'USDT_4h.csv', 'fx/'])
def test_manifest_rejects_non_canonical_paths(tmp_path, bad):
    with pytest.raises(M.ManifestError, match='canonical'):
        M.check_rel_path(bad)
    m = build(fixture_root(tmp_path))
    m['files'][0]['path'] = bad
    m['digest'] = M.digest_of(m)
    with pytest.raises(M.ManifestError):
        M.validate(m)
    with pytest.raises(M.ManifestError):
        M.build([bad], manifest_id='x', source_class='fixture', base=str(tmp_path / 'fx'))


def test_manifest_rejects_root_outside_store(tmp_path):
    fixture_root(tmp_path / 'outside')
    store = tmp_path / 'store'
    store.mkdir()
    with pytest.raises(M.ManifestError, match='canonical'):
        M.build(['../outside/fx'], manifest_id='x', source_class='fixture', base=str(store))


def test_manifest_rejects_symlink_escape(tmp_path):
    fixture_root(tmp_path / 'outside')
    store = tmp_path / 'store'
    store.mkdir()
    roots = ['fx', 'in']                                          # symlinked root dir, symlinked file
    try:
        os.symlink(str(tmp_path / 'outside' / 'fx'), str(store / 'fx'), target_is_directory=True)
        (store / 'in').mkdir()
        os.symlink(str(tmp_path / 'outside' / 'fx' / '4h' / 'BTCUSDT_4h.csv'), str(store / 'in' / 'BTCUSDT_4h.csv'))
    except (OSError, NotImplementedError):
        if os.name != 'nt':
            pytest.skip('symlinks not permitted on this machine')
        # Windows without symlink privilege: a directory junction escapes the same way
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(store / 'fx'), str(tmp_path / 'outside' / 'fx')],
                       check=True, capture_output=True)
        roots = ['fx']
    for root in roots:
        with pytest.raises(M.ManifestError, match='outside'):
            M.build([root], manifest_id='x', source_class='fixture', base=str(store))
    with pytest.raises(M.ManifestError, match='outside'):
        M.contained(str(store), 'fx/4h/BTCUSDT_4h.csv')


def test_manifest_verify_refuses_escape_before_reading(tmp_path):
    base = fixture_root(tmp_path / 'store')
    m = build(base)
    m['files'][0]['path'] = '../outside/BTCUSDT_4h.csv'
    m['digest'] = M.digest_of(m)
    with pytest.raises(M.ManifestError):
        M.verify(m, str(base))


def test_manifest_data_root_must_be_logical_name(tmp_path):
    m = build(fixture_root(tmp_path))
    for bad in ('C:/Dev/data', '/home/x', '', 'Repo'):
        x = dict(m, data_root=bad)
        x['digest'] = M.digest_of(x)
        with pytest.raises(M.ManifestError, match='data_root'):
            M.validate(x)


def test_manifest_rejects_bad_header(tmp_path):
    d = tmp_path / 'fx'
    d.mkdir()
    (d / 'BTCUSDT_4h.csv').write_bytes(b'time,open\n')
    with pytest.raises(M.ManifestError, match='header'):
        M.build(['fx'], manifest_id='x', source_class='fixture', base=str(tmp_path))


def test_committed_legacy_manifest_matches_disk_and_data_manifest():
    m = M.load(os.path.join(RES, 'manifests', 'legacy-unverified-v1.json'))
    assert m['source_class'] == 'legacy-unverified' and m['survivor_only'] is True and m['data_root'] == 'repo'
    assert M.verify(m) == []
    legacy = json.load(open(os.path.join(ROOT, 'DATA_MANIFEST.json'), encoding='utf-8'))['files']
    assert {f['path'] for f in m['files']} == set(legacy)
    assert all(legacy[f['path']]['sha256'] == f['sha256'] for f in m['files'])



REPRO_DIGEST = 'a42c36927eccf48a16c09085192dd53a7ef7ba607dfc1021cf96a67c917e8bb6'
CORE8 = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT')


def test_committed_m3_repro_manifest_is_bound_derived_and_reproduction_only():
    """legacy-m3-repro-v1 (Codex #53 6094970068): exactly the data_long/4h core-8 files the M3 harness served, their
    metadata copied verbatim from legacy-unverified-v1, digest pinned, marked reproduction-only, bytes still on disk."""
    m = M.load(os.path.join(RES, 'manifests', 'legacy-m3-repro-v1.json'))
    legacy = M.load(os.path.join(RES, 'manifests', 'legacy-unverified-v1.json'))
    assert m['digest'] == REPRO_DIGEST and m['manifest_id'] == 'legacy-m3-repro-v1'
    assert m['source_class'] == M.REPRO_ONLY and M.promotion_eligible(m) is False and m['survivor_only'] is True
    assert M.promotion_eligible(legacy) is True                                 # only repro-only is excluded here
    assert [f['path'] for f in m['files']] == sorted(f'data_long/4h/{s}_4h.csv' for s in CORE8)
    assert M.subset(legacy, [f['path'] for f in m['files']], manifest_id=m['manifest_id'],
                    source_class=M.REPRO_ONLY, note=m['note']) == m
    assert M.verify(m) == []


def test_repro_only_manifest_must_be_survivor_only_and_subset_refuses_unknown_paths():
    legacy = M.load(os.path.join(RES, 'manifests', 'legacy-unverified-v1.json'))
    m = M.subset(legacy, ['data_long/4h/BTCUSDT_4h.csv'], manifest_id='x', source_class=M.REPRO_ONLY, note='')
    bad = dict(m, survivor_only=False)
    bad['digest'] = M.digest_of(bad)
    with pytest.raises(M.ManifestError, match='survivor_only true'):
        M.validate(bad)
    with pytest.raises(M.ManifestError, match='not in legacy-unverified-v1'):
        M.subset(legacy, ['data_long/4h/NOPEUSDT_4h.csv'], manifest_id='x', source_class=M.REPRO_ONLY, note='')


# ---------------------------------------------------------------- ledger

@pytest.fixture(autouse=True)
def stub_envelope_prover(monkeypatch, tmp_path):
    """These R1 tests check the ledger's sequencing rules (spend, rerun identity, atomic groups) with synthetic
    digests. Since R3 every holdout record needs the immutable runs store and its frozen envelope; that proof is
    exercised for real in tests/test_res03_core.py, so here a stub store accepts any digest it is asked to prove."""
    stub = tmp_path / 'runs_stub'
    stub.mkdir(exist_ok=True)
    monkeypatch.setattr(L, '_runs_of', lambda d: str(stub))
    monkeypatch.setattr(L, 'recompute_run_digest',
                        lambda r, runs_dir=None: r['run_digest'] if runs_dir else 'no-immutable-runs-store')


def rec(path, **kw):
    if not os.path.exists(str(path)):                       # a new family is declared as an independent root
        kw.setdefault('lineage', 'root')
    base = dict(kind='data_access', candidate_id='c.v1', split='train', window=W, author='t', cairo_date='2026-10-09')
    base.update(kw)
    return L.append(str(path), **base)


def test_ledger_append_chain_and_trials(tmp_path):
    p = tmp_path / 'fam_a.jsonl'
    rec(p)
    rec(p, kind='variant')
    rec(p, kind='grid_point', detail={'ema': 21})
    recs = L._parse_family(p.read_bytes(), 'fam_a')
    assert [r['seq'] for r in recs] == [1, 2, 3] and recs[0]['prev'] == L.GENESIS
    assert recs[1]['prev'] == hashlib.sha256(p.read_bytes().splitlines(keepends=True)[0]).hexdigest()
    assert L.n_trials(recs) == 2


@pytest.mark.parametrize('edit', ['reorder', 'drop_first', 'change_field', 'truncate', 'non_canonical', 'wrong_family'])
def test_ledger_rejects_edits(tmp_path, edit):
    p = tmp_path / 'fam_a.jsonl'
    for k in ('data_access', 'variant', 'baseline'):
        rec(p, kind=k)
    lines = p.read_bytes().splitlines(keepends=True)
    data, fam = p.read_bytes(), 'fam_a'
    if edit == 'reorder':
        data = lines[1] + lines[0] + lines[2]
    elif edit == 'drop_first':
        data = b''.join(lines[1:])
    elif edit == 'change_field':
        data = data.replace(b'"variant"', b'"grid_point"')
    elif edit == 'truncate':
        data = data[:-1]
    elif edit == 'non_canonical':
        data = json.dumps(json.loads(lines[0]), indent=1).encode().replace(b'\n', b' ') + b'\n'
    elif edit == 'wrong_family':
        fam = 'fam_b'
    with pytest.raises(L.LedgerError):
        L._parse_family(data, fam)


def hold(path, kind, **kw):
    base = dict(kind=kind, split='holdout', run_digest=RD, manifest_digest=MD, candidate_id='c.v1',
                detail={'eval_digest': ED})
    base.update(kw)
    return rec(path, **base)


def test_holdout_reveal_spends_window_family_wide(tmp_path):
    p = tmp_path / 'fam_h.jsonl'
    hold(p, 'holdout_reveal')
    hold(p, 'holdout_rerun')
    with pytest.raises(L.LedgerError, match='identical run_digest'):
        hold(p, 'holdout_rerun', run_digest='c' * 64)
    with pytest.raises(L.LedgerError, match='already revealed'):
        hold(p, 'holdout_reveal')
    # a renamed descendant in the same family cannot reveal an overlapping window
    w2 = {'start': '2025-06-01T00:00:00Z', 'end': '2025-09-01T00:00:00Z'}
    with pytest.raises(L.LedgerError, match='overlaps'):
        hold(p, 'holdout_reveal', run_digest='d' * 64, window=w2, candidate_id='c_renamed.v2')
    w3 = {'start': '2025-07-01T00:00:00Z', 'end': '2025-10-01T00:00:00Z'}
    hold(p, 'holdout_reveal', run_digest='d' * 64, window=w3)


@pytest.mark.parametrize('field', ['family', 'candidate_id', 'manifest_digest', 'window', 'eval_digest', 'run_digest'])
def test_holdout_rerun_must_repeat_every_identity_field(tmp_path, field):
    """Codex R1 P1 repro: same run_digest/window but a different candidate or manifest was accepted."""
    p = tmp_path / 'fam_r.jsonl'
    hold(p, 'holdout_reveal')
    before = p.read_bytes()
    if field == 'family':                       # the reveal's bytes copied under another family name
        (tmp_path / 'copy').mkdir()
        other = tmp_path / 'copy' / 'fam_other.jsonl'
        other.write_bytes(before)
        with pytest.raises(L.LedgerError, match='family'):
            hold(other, 'holdout_rerun')
    else:
        mutated = {'candidate_id': dict(candidate_id='DIFFERENT'), 'manifest_digest': dict(manifest_digest='c' * 64),
                   'window': dict(window={'start': '2025-01-01T00:00:00Z', 'end': '2025-06-01T00:00:00Z'}),
                   'eval_digest': dict(detail={'eval_digest': 'f' * 64}),
                   'run_digest': dict(run_digest='d' * 64)}[field]
        with pytest.raises(L.LedgerError, match='identical'):
            hold(p, 'holdout_rerun', **mutated)
    assert p.read_bytes() == before                                # nothing appended
    hold(p, 'holdout_rerun')                                       # the exact identity still reruns


def test_holdout_records_need_eval_digest(tmp_path):
    p = tmp_path / 'fam_e.jsonl'
    with pytest.raises(L.LedgerError, match='eval_digest'):
        hold(p, 'holdout_reveal', detail={})
    assert REAL_RECOMPUTE({'run_digest': RD}) == 'no-immutable-runs-store'   # R3: no store can prove nothing


def test_window_spent_blocks_reveal(tmp_path):
    p = tmp_path / 'fam_s.jsonl'
    rec(p, kind='window_spent', split='development')
    with pytest.raises(L.LedgerError, match='overlaps'):
        hold(p, 'holdout_reveal')


def test_ledger_rejects_bad_window_and_digest(tmp_path):
    p = tmp_path / 'fam_v.jsonl'
    with pytest.raises(L.LedgerError, match='window'):
        rec(p, window={'start': '2025-07-01T00:00:00Z', 'end': '2025-01-01T00:00:00Z'})
    with pytest.raises(L.LedgerError, match='holdout'):
        hold(p, 'holdout_reveal', split='train')
    assert not p.exists() or p.read_bytes() == b''


def _git(repo, *a):
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t', *a], check=True,
                   capture_output=True)


def test_check_git_ancestry(tmp_path):
    repo = tmp_path / 'r'
    repo.mkdir()
    _git(repo, 'init', '-q')
    p = repo / 'fam_g.jsonl'
    rec(p)
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', '1')
    rec(p, kind='variant')
    _git(repo, 'commit', '-qam', '2')
    g = hashlib.sha256((repo / L.REGISTRY).read_bytes().splitlines(keepends=True)[0]).hexdigest()
    assert len(L.verify(str(p), registry_genesis=g)['fam_g']) == 2
    lines = p.read_bytes().splitlines(keepends=True)
    p.write_bytes(lines[0])                                    # rewrite history: drop the last record
    _git(repo, 'commit', '-qam', '3')
    with pytest.raises(L.LedgerError, match='rewritten'):
        L.verify(str(p), registry_genesis=g)


def _base():
    """CI names the PR base (ZB_GOLDEN_BASE, fetched explicitly); locally origin/master."""
    return os.environ.get('ZB_LEDGER_BASE') or os.environ.get('ZB_GOLDEN_BASE') or 'origin/master'


def test_committed_family_ledgers_mark_legacy_window_spent():
    d = os.path.join(RES, 'ledger')
    out = L.verify(d, base=_base())                            # records + registry + Git history + base anchor
    assert sorted(out) == ['range_bb_mr', 'res01_infra', 'short_breakdown', 'trend_ema_mom']
    # R3: the infrastructure family holds only development-split shape/coverage smokes (no trials, no holdout)
    infra = out.pop('res01_infra')
    assert {(r['kind'], r['split']) for r in infra} == {('data_access', 'development')} and L.n_trials(infra) == 0
    for fam, recs in out.items():
        spent = [r['window'] for r in recs if r['kind'] == 'window_spent']
        assert any(w['start'] <= '2025-01-01T00:00:00Z' and w['end'] >= '2026-10-04T00:00:00Z' for w in spent), fam
    assert L.main(['check', d, '--base', _base()]) == 0


# ---------------------------------------------------------------- R1 hardening (Cowork 6072246284)

def _fam(path, kind='data_access', lineage='root', **kw):
    """Append to a family file, declaring the family (root or a named parent) when the file is new."""
    if not os.path.exists(str(path)) or not os.path.getsize(str(path)):
        kw['lineage'] = lineage
    return rec(path, kind=kind, **kw)


def test_holdout_reruns_are_capped(tmp_path):
    p = tmp_path / 'fam_c.jsonl'
    hold(p, 'holdout_reveal', lineage='root')
    with pytest.raises(L.LedgerError, match='rerun cap'):
        for _ in range(5):
            hold(p, 'holdout_rerun')
    assert sum(r['kind'] == 'holdout_rerun' for r in L._check_state(str(p))['fam_c']) == L.MAX_RERUNS


@pytest.mark.parametrize('declared,ok', [(0, 0), (1, 1), (True, None), (99, None), ('1', None)])
def test_reveal_may_declare_a_lower_rerun_cap(tmp_path, declared, ok):
    p = tmp_path / 'fam_d.jsonl'
    d = {'eval_digest': ED, 'max_reruns': declared}
    if ok is None:
        with pytest.raises(L.LedgerError, match='max_reruns'):
            hold(p, 'holdout_reveal', detail=d, lineage='root')
        return
    hold(p, 'holdout_reveal', detail=d, lineage='root')
    for _ in range(ok):
        hold(p, 'holdout_rerun', detail=d)
    with pytest.raises(L.LedgerError, match='rerun cap'):
        hold(p, 'holdout_rerun', detail=d)


def test_descendant_or_renamed_family_cannot_reveal_a_spent_window(tmp_path):
    hold(tmp_path / 'fam_a.jsonl', 'holdout_reveal', lineage='root')
    for name, lineage in (('fam_a_v2', 'fam_a'), ('fam_a_v3', 'fam_a_v2')):     # child and grandchild
        with pytest.raises(L.LedgerError, match='spent'):
            hold(tmp_path / f'{name}.jsonl', 'holdout_reveal', run_digest='d' * 64, lineage=lineage)
        _fam(tmp_path / f'{name}.jsonl', lineage=lineage)                       # declared, may work off-holdout
    # an undeclared copy (a hand-made renamed file) is refused outright
    (tmp_path / 'fam_b.jsonl').write_bytes((tmp_path / 'fam_a.jsonl').read_bytes().replace(b'fam_a', b'fam_b'))
    with pytest.raises(L.LedgerError, match='not declared'):
        L._check_state(str(tmp_path))
    os.remove(tmp_path / 'fam_b.jsonl')
    # deleting a declared family file (rename = delete + new file) is refused: its spends live on in the registry
    data = (tmp_path / 'fam_a.jsonl').read_bytes()
    os.remove(tmp_path / 'fam_a.jsonl')
    with pytest.raises(L.LedgerError, match='missing'):
        L._check_state(str(tmp_path))
    (tmp_path / 'fam_a.jsonl').write_bytes(data)
    L._check_state(str(tmp_path))
    # an explicitly independent root family is a separate hypothesis and keeps its own spend
    hold(tmp_path / 'other.jsonl', 'holdout_reveal', run_digest='d' * 64, lineage='root')


def test_registry_spend_copy_must_match_family(tmp_path):
    hold(tmp_path / 'fam_a.jsonl', 'holdout_reveal', lineage='root')
    reg = tmp_path / L.REGISTRY
    reg.write_bytes(reg.read_bytes().splitlines(keepends=True)[0])   # spend copy dropped
    with pytest.raises(L.LedgerError, match='spend'):
        L._check_state(str(tmp_path))


def test_new_family_needs_explicit_lineage(tmp_path):
    with pytest.raises(L.LedgerError, match='lineage'):
        L.append(str(tmp_path / 'fam_n.jsonl'), kind='data_access', candidate_id='c', split='train', window=W,
                 author='t', cairo_date='2026-10-09')
    with pytest.raises(L.LedgerError, match='lineage'):
        _fam(tmp_path / 'fam_n.jsonl', lineage='nobody')               # parent must be declared
    assert not (tmp_path / 'fam_n.jsonl').exists() and not (tmp_path / L.REGISTRY).exists()


@pytest.mark.parametrize('kind', ['data_access', 'variant', 'grid_point', 'baseline'])
def test_holdout_split_records_only_inside_the_atomic_reveal(tmp_path, kind):
    p = tmp_path / 'fam_h.jsonl'
    with pytest.raises(L.LedgerError, match='atomic'):
        _fam(p, kind=kind, split='holdout', run_digest=RD, manifest_digest=MD, detail={'eval_digest': ED})
    hold(p, 'holdout_reveal', lineage='root')
    with pytest.raises(L.LedgerError, match='complete identity'):    # Codex R3 P2: another candidate never joins
        hold(p, kind, candidate_id='baseline.flat')
    hold(p, kind)                                                    # part of the reveal: same identity, contiguous
    with pytest.raises(L.LedgerError, match='atomic'):
        hold(p, kind, window={'start': '2025-01-01T00:00:00Z', 'end': '2025-06-01T00:00:00Z'})
    rec(p, kind='data_access', split='train', window={'start': '2024-01-01T00:00:00Z', 'end': '2024-06-01T00:00:00Z'})
    with pytest.raises(L.LedgerError, match='atomic'):                # the reveal is closed now
        hold(p, kind)


@pytest.mark.parametrize('kind', ['variant', 'grid_point'])
def test_no_tuning_on_a_revealed_window(tmp_path, kind):
    p = tmp_path / 'fam_t.jsonl'
    hold(p, 'holdout_reveal', lineage='root')
    rec(p, kind='data_access', split='train', window={'start': '2024-01-01T00:00:00Z', 'end': '2024-06-01T00:00:00Z'})
    for split in ('train', 'walk_forward', 'development'):            # relabelling the split does not help
        with pytest.raises(L.LedgerError, match='tuning'):
            rec(p, kind=kind, split=split, window={'start': '2025-03-01T00:00:00Z', 'end': '2025-09-01T00:00:00Z'})
    rec(p, kind=kind, split='train', window={'start': '2024-01-01T00:00:00Z', 'end': '2024-06-01T00:00:00Z'})


def _repo(tmp_path):
    repo = tmp_path / 'r'
    (repo / 'led').mkdir(parents=True)
    _git(repo, 'init', '-q', '-b', 'master')
    return repo


def _reg_genesis(repo):
    return hashlib.sha256((repo / 'led' / L.REGISTRY).read_bytes().splitlines(keepends=True)[0]).hexdigest()


def test_check_records_is_not_a_public_verification():
    assert not hasattr(L, 'check_records')                           # bytes alone can never prove append-only


def test_verify_always_checks_history(tmp_path):
    repo = _repo(tmp_path)
    p = repo / 'led' / 'fam_g.jsonl'
    _fam(p)
    _fam(p, kind='variant')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', '1')
    g = _reg_genesis(repo)
    assert [r['seq'] for r in L.verify(str(p), registry_genesis=g)['fam_g']] == [1, 2]
    good = p.read_bytes()
    p.write_bytes(good.splitlines(keepends=True)[0])                 # truncated tail, still a valid chain
    with pytest.raises(L.LedgerError, match='append'):
        L.verify(str(p), registry_genesis=g)
    assert L.main(['check', str(p)]) == 1
    L.append(str(p), kind='baseline', candidate_id='c.v1', split='train', window=W, author='t', cairo_date='2026-10-09')
    with pytest.raises(L.LedgerError, match='append'):                # a fully re-chained forgery of record 2
        L.verify(str(p), registry_genesis=g)
    p.write_bytes(good)
    L.verify(str(p), registry_genesis=g)


def test_verify_refuses_undeclared_genesis(tmp_path):
    repo = _repo(tmp_path)
    p = repo / 'led' / 'fam_g.jsonl'
    _fam(p)
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', '1')
    with pytest.raises(L.LedgerError, match='genesis'):              # the registry must match its pinned genesis
        L.verify(str(p), registry_genesis='0' * 64)
    # a branch off a base without the ledger that starts a fresh ledger
    _git(repo, 'checkout', '-q', '--orphan', 'fresh')
    _git(repo, 'rm', '-rqf', '.')
    (repo / 'led').mkdir(exist_ok=True)
    _fam(p, kind='baseline')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'fresh')
    with pytest.raises(L.LedgerError, match='genesis'):              # the committed pin refuses a fresh registry
        L.verify(str(p))
    with pytest.raises(L.LedgerError, match='base'):                 # and the base anchor refuses the restart
        L.verify(str(p), registry_genesis=_reg_genesis(repo), base='master')


def test_verify_outside_git_fails_closed(tmp_path):
    p = tmp_path / 'fam_x.jsonl'
    _fam(p)
    with pytest.raises(L.LedgerError, match='git'):
        L.verify(str(p))


@pytest.mark.parametrize('field,value', [('window', {'start': '2025-13-45T99:99:99Z', 'end': '2026-01-01T00:00:00Z'}),
                                         ('window', {'start': '2025-02-30T00:00:00Z', 'end': '2026-01-01T00:00:00Z'}),
                                         ('cairo_date', '2026-99-99'), ('cairo_date', '2026-02-30')])
def test_ledger_strict_dates(tmp_path, field, value):
    with pytest.raises(L.LedgerError, match=field):
        _fam(tmp_path / 'fam_q.jsonl', **{field: value})


def test_ledger_rejects_bool_seq_and_nan(tmp_path):
    p = tmp_path / 'fam_b.jsonl'
    _fam(p)
    line = p.read_bytes()
    with pytest.raises(L.LedgerError, match='seq'):
        L._parse_family(line.replace(b'"seq":1', b'"seq":true'), 'fam_b')
    with pytest.raises(L.LedgerError, match='JSON'):
        L._parse_family(line.replace(b'"detail":{}', b'"detail":{"x":NaN}'), 'fam_b')


def test_ledger_append_lock(tmp_path):
    p = tmp_path / 'fam_l.jsonl'
    _fam(p)
    assert not (tmp_path / L.LOCK).exists()                          # released after a normal append
    (tmp_path / L.LOCK).write_bytes(b'')
    with pytest.raises(L.LedgerError, match='lock'):
        rec(p, kind='variant')
    assert len(p.read_bytes().splitlines()) == 1


@pytest.mark.parametrize('mutate', [
    lambda f, m: f.__setitem__('rows', True),
    lambda f, m: f.__setitem__('bytes', False),
    lambda f, m: f.__setitem__('first_open_ms', True),
    lambda f, m: m.__setitem__('note', float('nan')),
    lambda f, m: m.__setitem__('note', 7),
    lambda f, m: m.__setitem__('loader_version', 'junk'),
    lambda f, m: f.__setitem__('source', 'junk'),
    lambda f, m: f['source'].__setitem__('archive_checksum', 'junk'),
    lambda f, m: m.__setitem__('accessed_cairo', '2026-99-99'),
    lambda f, m: m.__setitem__('accessed_cairo', 7),
])
def test_manifest_rejects_bool_nan_and_junk(tmp_path, mutate):
    m = build(fixture_root(tmp_path))
    mutate(m['files'][0], m)
    try:
        m['digest'] = M.digest_of(m)
    except ValueError:
        pass
    with pytest.raises(M.ManifestError):
        M.validate(m)


def test_manifest_load_rejects_nan(tmp_path):
    m = build(fixture_root(tmp_path))
    out = tmp_path / 'm.json'
    M.write(m, str(out))
    out.write_bytes(out.read_bytes().replace(b'"note": ""', b'"note": NaN'))
    with pytest.raises(M.ManifestError, match='JSON'):
        M.load(str(out))


@pytest.mark.parametrize('body', [b'2025-01-01 04:00:00,1,2,0.5,1.5,10\n2025-01-01 00:00:00,1,2,0.5,1.5,10\n',
                                  b'2025-01-01 00:00:00,1,2,0.5,1.5,10\n2025-01-01 00:00:00,1,2,0.5,1.5,10\n',
                                  b'2025-02-30 00:00:00,1,2,0.5,1.5,10\n'])
def test_manifest_refuses_unordered_duplicate_or_bad_bars(tmp_path, body):
    d = tmp_path / 'fx'
    d.mkdir()
    (d / 'BTCUSDT_4h.csv').write_bytes(b't,o,h,l,c,v\n' + body)
    with pytest.raises(M.ManifestError):
        M.build(['fx'], manifest_id='x', source_class='fixture', base=str(tmp_path))
