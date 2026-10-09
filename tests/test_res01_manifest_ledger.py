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


# ---------------------------------------------------------------- ledger

def rec(path, **kw):
    base = dict(kind='data_access', candidate_id='c.v1', split='train', window=W, author='t', cairo_date='2026-10-09')
    base.update(kw)
    return L.append(str(path), **base)


def test_ledger_append_chain_and_trials(tmp_path):
    p = tmp_path / 'fam_a.jsonl'
    rec(p)
    rec(p, kind='variant')
    rec(p, kind='grid_point', detail={'ema': 21})
    recs = L.check_records(p.read_bytes(), 'fam_a')
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
        L.check_records(data, fam)


def test_ledger_check_append():
    L.check_append(b'a\n', b'a\nb\n')
    with pytest.raises(L.LedgerError):
        L.check_append(b'a\nb\n', b'a\n')


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
        other = tmp_path / 'fam_other.jsonl'
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
    assert L.recompute_run_digest({'run_digest': RD}) is None      # R3 hook: not yet checked


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
    assert L.check_git(str(repo), 'fam_g.jsonl') == 2
    lines = p.read_bytes().splitlines(keepends=True)
    p.write_bytes(lines[0])                                    # rewrite history: drop the last record
    _git(repo, 'commit', '-qam', '3')
    with pytest.raises(L.LedgerError, match='rewritten'):
        L.check_git(str(repo), 'fam_g.jsonl')


def test_committed_family_ledgers_mark_legacy_window_spent():
    d = os.path.join(RES, 'ledger')
    fams = sorted(f[:-6] for f in os.listdir(d) if f.endswith('.jsonl'))
    assert fams == ['range_bb_mr', 'short_breakdown', 'trend_ema_mom']
    for fam in fams:
        recs = L.check_records(open(os.path.join(d, fam + '.jsonl'), 'rb').read(), fam)
        spent = [r['window'] for r in recs if r['kind'] == 'window_spent']
        assert any(w['start'] <= '2025-01-01T00:00:00Z' and w['end'] >= '2026-10-04T00:00:00Z' for w in spent), fam
