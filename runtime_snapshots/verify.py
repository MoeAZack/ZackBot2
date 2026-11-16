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
for entry in manifest['files']:
    path = (root / entry['account'] / entry['path']).resolve()
    assert path.is_relative_to(root), 'Path leaves snapshot'
    data = path.read_bytes()
    assert len(data) == entry['size'], f'Size mismatch: {entry["path"]}'
    assert hashlib.sha256(data).hexdigest() == entry['sha256'], f'Hash mismatch: {entry["path"]}'
    if '--index' in sys.argv:
        relative = path.relative_to(root.parent).as_posix()
        staged = subprocess.check_output(['git', 'show', ':' + relative], cwd=root.parent)
        assert staged == data, f'Git index altered bytes: {entry["path"]}'
    hashes += 1
    if path.suffix == '.py':
        ast.parse(data, filename=entry['path'])
        syntax += 1
print(f'PASS: {hashes} SHA-256/size checks; {syntax} Python AST parses; 0 runtime imports/actions')
