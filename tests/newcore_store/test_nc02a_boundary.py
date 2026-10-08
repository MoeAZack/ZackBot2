"""newcore.store boundary (nc02_design.md section 1): every OS call goes through the FsSeam.

- Only fs.py may use the OS (os.open / os.write / os.fsync / ctypes ...); every other module uses `os.path` string
  helpers at most, no IO builtin, no clock, no randomness, no environment, no logging, no global state.
- Imports: an explicit stdlib allowlist, newcore.domain, newcore.ports and the package itself. Nothing legacy.
"""
import ast
import glob
import importlib
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STORE = os.path.join(ROOT, 'newcore', 'store')
FILES = sorted(os.path.basename(p) for p in glob.glob(os.path.join(STORE, '*.py')))
PURE_STDLIB = {'__future__', 'dataclasses', 'decimal', 'enum', 'errno', 'hashlib', 'json', 're', 'struct', 'typing',
               'zlib', 'os'}
SEAM_STDLIB = PURE_STDLIB | {'stat', 'sys', 'ctypes', 'msvcrt', 'fcntl'}
FORBIDDEN_CALLS = {'open', 'print', 'input', 'eval', 'exec', 'compile', '__import__', 'breakpoint', 'globals'}
FORBIDDEN_ATTRS = {'environ', 'getenv', 'time', 'time_ns', 'monotonic', 'perf_counter', 'now', 'utcnow', 'today',
                   'random', 'urandom', 'uuid4', 'system', 'popen', 'read_text', 'write_text', 'read_bytes_',
                   'getLogger'}


def tree(name):
    with open(os.path.join(STORE, name), encoding='utf-8') as fh:
        return ast.parse(fh.read(), name)


def test_the_package_has_the_expected_modules():
    assert FILES == ['__init__.py', 'errors.py', 'evidence.py', 'fold.py', 'frame.py', 'fs.py', 'header.py', 'hold.py',
                     'journal.py', 'recovery.py']


@pytest.mark.parametrize('name', FILES)
def test_imports_are_allowlisted(name):
    allowed = SEAM_STDLIB if name == 'fs.py' else PURE_STDLIB
    for node in ast.walk(tree(name)):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split('.')[0] in allowed, f'{name}: import {a.name}'
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                assert node.level == 1, f'{name}: relative import outside the package'
                continue
            m = node.module
            assert m.split('.')[0] in allowed or m.startswith(('newcore.domain', 'newcore.ports')), f'{name}: {m}'


@pytest.mark.parametrize('name', [f for f in FILES if f != 'fs.py'])
def test_only_the_seam_touches_the_os(name):
    for node in ast.walk(tree(name)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in FORBIDDEN_CALLS, f'{name}:{node.lineno} calls {node.func.id}'
        if isinstance(node, ast.Attribute):
            assert node.attr not in FORBIDDEN_ATTRS, f'{name}:{node.lineno} uses .{node.attr}'
            if isinstance(node.value, ast.Name) and node.value.id == 'os':
                assert node.attr == 'path', f'{name}:{node.lineno} uses os.{node.attr} outside the seam'
        assert not isinstance(node, (ast.Global, ast.Nonlocal)), f'{name}:{node.lineno} global state'


def test_the_seam_has_no_rename_replace_truncate_or_delete():
    names = {n.attr for n in ast.walk(tree('fs.py')) if isinstance(n, ast.Attribute)}
    assert not names & {'rename', 'replace', 'truncate', 'ftruncate', 'remove', 'unlink', 'rmdir', 'removedirs',
                        'rmtree'}, names


def test_runtime_import():
    m = importlib.import_module('newcore.store')
    assert m.FileJournal.__module__ == 'newcore.store.journal'
