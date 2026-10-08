"""newcore.store boundary (nc02_design.md section 1): every OS call goes through the FsSeam.

- Only fs.py may use the file system (os.open / os.write / os.fsync / ctypes ...) and only cipher.py may call DPAPI;
  every other module uses `os.path` string helpers at most, no IO builtin, no clock, no randomness, no environment, no
  logging, no global state.
- Imports: an explicit stdlib allowlist, newcore.domain, newcore.ports and the package itself. Nothing legacy.
- The seam has no rename / replace / truncate; its one delete is unlink_own_partial (D4), called by envelope.py only.
"""
import ast
import glob
import importlib
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STORE = os.path.join(ROOT, 'newcore', 'store')
FILES = sorted(os.path.basename(p) for p in glob.glob(os.path.join(STORE, '*.py')))
OS_MODULES = {'fs.py', 'cipher.py'}
PURE_STDLIB = {'__future__', 'dataclasses', 'decimal', 'enum', 'errno', 'hashlib', 'json', 're', 'struct', 'typing',
               'zlib', 'os'}
SEAM_STDLIB = PURE_STDLIB | {'stat', 'sys', 'ctypes', 'msvcrt', 'fcntl'}
FORBIDDEN_CALLS = {'open', 'print', 'input', 'eval', 'exec', 'compile', '__import__', 'breakpoint', 'globals'}
FORBIDDEN_ATTRS = {'environ', 'getenv', 'time', 'time_ns', 'monotonic', 'perf_counter', 'now', 'utcnow', 'today',
                   'random', 'urandom', 'uuid4', 'system', 'popen', 'read_text', 'write_text', 'getLogger'}
EXPECTED = ['__init__.py', 'api.py', 'cipher.py', 'envelope.py', 'errors.py', 'exchange_view.py', 'fold.py', 'frame.py',
            'fs.py', 'header.py',
            'incidents.py', 'legacy.py', 'reconcile.py', 'records.py', 'slots.py', 'snapfile.py', 'store.py',
            'hold.py', 'journal.py', 'recovery.py']


def tree(name):
    with open(os.path.join(STORE, name), encoding='utf-8') as fh:
        return ast.parse(fh.read(), name)


def test_the_package_has_the_expected_modules():
    assert FILES == sorted(EXPECTED)


@pytest.mark.parametrize('name', FILES)
def test_imports_are_allowlisted(name):
    allowed = SEAM_STDLIB if name in OS_MODULES else PURE_STDLIB
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


@pytest.mark.parametrize('name', [f for f in FILES if f not in OS_MODULES])
def test_only_the_seam_touches_the_os(name):
    for node in ast.walk(tree(name)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in FORBIDDEN_CALLS, f'{name}:{node.lineno} calls {node.func.id}'
        if isinstance(node, ast.Attribute):
            assert node.attr not in FORBIDDEN_ATTRS, f'{name}:{node.lineno} uses .{node.attr}'
            if isinstance(node.value, ast.Name) and node.value.id == 'os':
                assert node.attr == 'path', f'{name}:{node.lineno} uses os.{node.attr} outside the seam'
        assert not isinstance(node, (ast.Global, ast.Nonlocal)), f'{name}:{node.lineno} global state'


def test_the_seam_has_no_rename_replace_or_truncate_and_one_narrow_delete():
    t = tree('fs.py')
    names = {n.attr for n in ast.walk(t) if isinstance(n, ast.Attribute)}
    assert not names & {'rename', 'replace', 'truncate', 'ftruncate', 'remove', 'rmdir', 'removedirs', 'rmtree'}, names
    unlinks = [n for n in ast.walk(t) if isinstance(n, ast.Attribute) and n.attr == 'unlink']
    owners = [f.name for f in ast.walk(t) if isinstance(f, ast.FunctionDef)
              and any(isinstance(n, ast.Attribute) and n.attr == 'unlink' for n in ast.walk(f))]
    assert len(unlinks) == 1 and owners == ['unlink_own_partial']
    callers = {name for name in FILES if name != 'fs.py' and any(
        isinstance(n, ast.Attribute) and n.attr == 'unlink_own_partial' for n in ast.walk(tree(name)))}
    assert callers <= {'envelope.py'}, callers


def test_runtime_import():
    m = importlib.import_module('newcore.store')
    assert m.FileJournal.__module__ == 'newcore.store.journal'
