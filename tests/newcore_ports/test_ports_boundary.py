"""Step-0 import boundary: newcore.ports imports only the stdlib and the merged NC-01 package newcore.domain (no legacy
module, no adapter, no newcore.venue / newcore.strategy) and performs no IO, clock, randomness or environment access."""
import ast
import importlib
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORTS = os.path.join(ROOT, 'newcore', 'ports')
FILES = sorted(f for f in os.listdir(PORTS) if f.endswith('.py'))
NC01 = 'newcore.domain'
ALLOWED = {'__future__', 'dataclasses', 'decimal', 'enum', 'hashlib', 'json', 're', 'typing'}
FORBIDDEN_CALLS = {'open', 'exec', 'eval', 'compile', '__import__', 'input', 'print', 'breakpoint', 'globals', 'setattr'}
FORBIDDEN_ATTRS = {'environ', 'getenv', 'time', 'time_ns', 'monotonic', 'perf_counter', 'now', 'utcnow', 'today',
                   'random', 'urandom', 'uuid4', 'uuid1', 'system', 'popen', 'read_text', 'write_text', 'read_bytes',
                   'write_bytes'}


def tree(name):
    with open(os.path.join(PORTS, name), encoding='utf-8') as fh:
        return ast.parse(fh.read(), name)


def test_the_package_has_the_expected_modules():
    assert FILES == ['__init__.py', 'bars.py', 'journal.py', 'keys.py', 'values.py', 'venue.py']


@pytest.mark.parametrize('name', FILES)
def test_only_allowed_stdlib_and_relative_imports(name):
    for node in ast.walk(tree(name)):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split('.')[0] in ALLOWED, f'{name}: import {a.name}'
        elif isinstance(node, ast.ImportFrom):
            if node.level:                                      # relative: only siblings inside newcore.ports
                assert node.level == 1, f'{name}: from {"." * node.level}{node.module}'
            else:
                ok = node.module.split('.')[0] in ALLOWED or node.module == NC01 or node.module.startswith(NC01 + '.')
                assert ok, f'{name}: from {node.module}'


@pytest.mark.parametrize('name', FILES)
def test_no_io_clock_randomness_or_environment(name):
    for node in ast.walk(tree(name)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in FORBIDDEN_CALLS, f'{name}:{node.lineno} calls {node.func.id}'
        if isinstance(node, ast.Attribute):
            assert node.attr not in FORBIDDEN_ATTRS, f'{name}:{node.lineno} uses .{node.attr}'
        if isinstance(node, ast.Global):
            raise AssertionError(f'{name}:{node.lineno} mutates global state')


def test_runtime_import_pulls_in_no_forbidden_module():
    mod = importlib.import_module('newcore.ports')
    for sub in ('bars', 'journal', 'keys', 'values', 'venue'):
        m = importlib.import_module(f'newcore.ports.{sub}')
        names = {getattr(v, '__name__', '') for v in vars(m).values() if type(v).__name__ == 'module'}
        assert all(n.split('.')[0] in ALLOWED or n.startswith(('newcore.ports', NC01)) for n in names), (sub, names)
    assert mod.decision_key('x', 'v1', '4h', 'BTCUSDT', 'LONG', 1759924800000, 'entry').__module__ == NC01 + '.decision'
