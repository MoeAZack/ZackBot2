"""NC-01 purity (contract section 1, 6.5): an AST import-boundary test and a runtime import smoke test.

newcore/** imports only an explicit stdlib allowlist and its own package: no legacy module (engine, backtest, app,
grid, ...), no file / network / clock / environment / logging / process / randomness module, no pickle; and the code
calls no IO builtin and declares no global state."""
import ast
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PACKAGE = os.path.join(ROOT, 'newcore', 'domain')     # NC-01's boundary (Codex: scoped to newcore/domain)

STDLIB_ALLOWED = {'__future__', 'dataclasses', 'decimal', 'enum', 'functools', 'hashlib', 'json', 're', 'types', 'typing',
                  'unicodedata'}                     # unicodedata: the NFC text check (PR #44, Cowork 5)
FORBIDDEN_RUNTIME = {'socket', 'ssl', 'http', 'urllib', 'requests', 'logging', 'subprocess', 'threading', 'asyncio',
                     'sqlite3', 'pickle', 'shelve', 'tempfile', 'shutil', 'pathlib', 'random', 'secrets', 'uuid', 'time',
                     'datetime', 'zoneinfo', 'pandas', 'numpy', 'binance_client', 'multiprocessing', 'concurrent'}
IO_BUILTINS = {'open', 'print', 'input', 'eval', 'exec', 'compile', '__import__', 'breakpoint', 'globals', 'setattr'}


def legacy_modules():
    """Every top-level legacy module of the repository (root *.py), computed - a new legacy file is covered at once."""
    return {os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(ROOT, '*.py'))}


def sources():
    out = sorted(glob.glob(os.path.join(PACKAGE, '**', '*.py'), recursive=True))
    assert len(out) >= 10
    return out


def test_imports_are_stdlib_allowlist_or_own_package():
    legacy = legacy_modules()
    assert {'engine', 'backtest', 'app', 'grid', 'trade_audit'} <= legacy
    bad = []
    for path in sources():
        rel = os.path.relpath(path, ROOT)
        for node in ast.walk(ast.parse(open(path, encoding='utf-8').read(), rel)):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:                              # relative import: inside the package by construction
                    continue
                names = [node.module or '']
            else:
                continue
            for n in names:
                top = n.split('.')[0]
                if n.startswith('newcore.domain') or top == '__future__':
                    continue
                if top in legacy or top not in STDLIB_ALLOWED:
                    bad.append(f'{rel}:{node.lineno} imports {n}')
    assert bad == []


def test_no_io_builtins_and_no_global_state():
    bad = []
    for path in sources():
        rel = os.path.relpath(path, ROOT)
        for node in ast.walk(ast.parse(open(path, encoding='utf-8').read(), rel)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in IO_BUILTINS:
                bad.append(f'{rel}:{node.lineno} calls {node.func.id}()')
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                bad.append(f'{rel}:{node.lineno} declares global / nonlocal state')
            if isinstance(node, ast.Attribute) and node.attr in ('environ', 'getenv', 'system', 'urandom'):
                bad.append(f'{rel}:{node.lineno} uses .{node.attr}')
    assert bad == []


SMOKE = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
before = set(sys.modules)
import newcore.domain as D
from newcore.domain import codec
new = sorted(set(sys.modules) - before)
print(json.dumps(dict(new=new, types=sorted(codec.RECORD_TYPES))))
'''


def test_runtime_import_loads_nothing_impure():
    """A fresh isolated interpreter imports the package and reports every module that import pulled in."""
    out = subprocess.run([sys.executable, '-I', '-c', SMOKE, ROOT], capture_output=True, text=True, timeout=120,
                         cwd=HERE)
    assert out.returncode == 0, out.stderr
    rep = json.loads(out.stdout)
    tops = {m.split('.')[0] for m in rep['new']}
    assert not (tops & FORBIDDEN_RUNTIME), sorted(tops & FORBIDDEN_RUNTIME)
    assert not (tops & legacy_modules()), sorted(tops & legacy_modules())
    assert 'newcore' in tops and len(rep['types']) == 21
