"""Offline-only verification of captured artifacts; never imports runtime code."""
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
hashes = syntax = 0
identical = redacted = 0
expected_paths = set()
redactions = json.loads((root / 'redactions.json').read_text(encoding='utf-8'))
counts = {}
for record in redactions:
    counts[record['file']] = counts.get(record['file'], 0) + 1
for entry in manifest['files']:
    path = (root / entry['account'] / entry['path']).resolve()
    assert path.is_relative_to(root), 'Path leaves snapshot'
    data = path.read_bytes()
    assert len(data) == entry['size'], f'Size mismatch: {entry["path"]}'
    assert hashlib.sha256(data).hexdigest() == entry['sha256'], f'Hash mismatch: {entry["path"]}'
    expected_paths.add(path)
    relative = path.relative_to(root.parent).as_posix()
    assert counts.get(relative, 0) == entry['redaction_count'], 'Redaction count mismatch'
    if entry['byte_identical']:
        assert entry['source_sha256'] == entry['sha256'] and entry['source_size'] == entry['size']
        identical += 1
    else:
        assert entry['redaction_count'] > 0 and b'REDACTED_VALUE' in data
        redacted += 1
    if '--index' in sys.argv:
        relative = path.relative_to(root.parent).as_posix()
        staged = subprocess.check_output(['git', 'show', ':' + relative], cwd=root.parent)
        assert staged == data, f'Git index altered bytes: {entry["path"]}'
    hashes += 1
    if path.suffix == '.py':
        ast.parse(data, filename=entry['path'])
        syntax += 1
actual_paths = {p.resolve() for account in root.glob('acct*') for folder in ['source', 'launcher', 'overlay'] for p in (account / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
assert actual_paths == expected_paths, 'Unmanifested or missing source artifact'
if '--provenance' in sys.argv:
    provenance = json.loads((root / 'provenance.json').read_text(encoding='utf-8'))
    assert provenance['exe_sha256'] == manifest['acct1_executable']['sha256'], 'Executable evidence differs'
    revision = provenance['build_metadata_claim']['source_commit']
    tracked = set(subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', revision], cwd=root.parent, text=True).splitlines())
    matched = 0
    missing = []
    for entry in manifest['files']:
        if entry['account'] != 'acct1' or not entry['path'].startswith('source/'):
            continue
        relative = entry['path'].removeprefix('source/')
        if relative not in tracked:
            missing.append(relative)
            continue
        original = subprocess.check_output(['git', 'show', revision + ':' + relative], cwd=root.parent)
        assert hashlib.sha256(original).hexdigest() == entry['source_sha256'], 'Historical source hash differs'
        matched += 1
    comparison = provenance['historical_revision_comparison']
    assert matched == comparison['byte_identical'] == comparison['compared']
    assert missing == comparison['not_in_revision'] and not comparison['different']
    print(f'PASS: {matched} historical Git source hashes; generated/untracked files explicitly recorded')
print(f'PASS: {hashes} snapshot SHA-256/size checks; {syntax} Python AST parses; {identical} byte-identical files; {redacted} redacted files; 0 runtime imports/actions')
