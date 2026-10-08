"""NC-03 wire layer: credentials never reach repr / str / logs / exceptions / outcomes, and the package is pure
(stdlib only, no legacy import, no network module, nothing needing credentials at import)."""
import ast
import logging
import os
import subprocess
import sys
import traceback
from decimal import Decimal as D

import pytest

from newcore.venue.credentials import CredentialsUnavailable
from newcore.venue.transport import StopRoute, VenueInputError
from newcore.venue.wire import HttpRequest, WireConnectionError, WireTimeout

from ncv_support import DUMMY_KEY, DUMMY_SECRET, make, raw

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'newcore', 'venue')


def _calls(t):
    return [
        t.server_time, t.exchange_info, lambda: t.klines('SOLUSDT', '4h'), t.account, t.positions,
        t.dual_side_position, t.open_orders, t.open_algo_orders, lambda: t.user_trades('SOLUSDT'),
        lambda: t.place_market('SOLUSDT', 'BUY', 'LONG', D('1'), 'zb-a', reduce_only=False),
        lambda: t.place_stop_market('SOLUSDT', 'LONG', D('1'), D('200'), 'zb-b', route=StopRoute.CLASSIC),
        lambda: t.place_stop_market('SOLUSDT', 'LONG', D('1'), D('200'), 'zb-c', route=StopRoute.ALGO),
        lambda: t.query_order('SOLUSDT', 'zb-a'), lambda: t.query_algo_order('zb-c'),
        lambda: t.cancel_order('SOLUSDT', 'zb-b'), lambda: t.cancel_algo_order('zb-c'),
    ]


def _leaky_answers():
    """Answers that try hard to leak: exceptions and venue messages that embed the key / secret / signed URL."""
    leak = f'{DUMMY_KEY} {DUMMY_SECRET}'
    return [
        WireTimeout(f'timeout on https://testnet.binancefuture.com/fapi/v1/order?signature=deadbeef {leak}'),
        WireConnectionError(f'reset {leak}'),
        RuntimeError(f'urllib3 said {leak}'),
        raw(400, ('{"code": -1022, "msg": "bad signature for key %s"}' % DUMMY_KEY).encode()),
        raw(400, ('{"code": -3999, "msg": "%s"}' % DUMMY_SECRET).encode()),
        raw(503, ('{"code": -1008, "msg": "%s"}' % leak).encode()),
        raw(200, leak.encode()),
        raw(200, b'{}'),
    ]


def test_no_secret_in_repr_str_logs_or_outcomes(caplog):
    caplog.set_level(logging.DEBUG)
    seen = []
    signatures = []
    for answer in _leaky_answers():
        n = len(_calls(make()[0]))
        t, http = make(*([answer] * n))
        seen += [repr(t), str(t)]
        for call in _calls(t):
            out = call()
            seen += [repr(out), str(out)]
        for req in http.requests:
            seen += [repr(req), str(req)]
            if req.signed:
                signatures.append(req.query.rsplit('&signature=', 1)[1])
    seen.append(caplog.text)
    seen += [r.getMessage() for r in caplog.records]
    blob = '\n'.join(seen)
    assert DUMMY_SECRET not in blob
    assert DUMMY_KEY not in blob
    assert signatures and not any(sig in blob for sig in signatures)
    assert 'newcore.venue.transport' in caplog.text           # the transport did log; the logs were checked


def test_request_repr_redacts_header_and_signature():
    t, http = make(raw(200, b'{}'))
    t.account()
    req = http.last
    assert req.header('X-MBX-APIKEY') == DUMMY_KEY                  # the wire still carries it ...
    sig = req.query.rsplit('&signature=', 1)[1]
    for text in (repr(req), str(req), f'{req}'):                    # ... the text form never does
        assert DUMMY_KEY not in text and sig not in text and '<redacted>' in text


def test_exceptions_carry_no_secret():
    texts = []
    t, _ = make(creds=False)
    try:
        t.account()
    except CredentialsUnavailable:
        texts.append(traceback.format_exc())

    class Broken:
        def api_key(self): return DUMMY_KEY
        def sign(self, payload): raise OSError(f'hsm said {DUMMY_SECRET}')
    t2, _ = make()
    object.__setattr__(t2, '_credentials', Broken())
    try:
        t2.account()
    except CredentialsUnavailable:
        texts.append(traceback.format_exc())
    t3, _ = make()
    try:
        t3.place_market('SOLUSDT', 'BUY', 'LONG', 1.5, 'zb-a', reduce_only=False)
    except VenueInputError:
        texts.append(traceback.format_exc())
    assert len(texts) == 3
    blob = '\n'.join(texts)
    assert DUMMY_SECRET not in blob and DUMMY_KEY not in blob


def test_transport_has_no_secret_attribute():
    t, _ = make()
    assert not hasattr(t, '__dict__')                     # __slots__: no ad-hoc attribute can hold a copy
    for name in t.__slots__:
        v = getattr(t, name)
        assert v != DUMMY_SECRET and v != DUMMY_KEY


# ---------- import boundary ----------

ALLOWED_STDLIB = {'__future__', 'dataclasses', 'decimal', 'enum', 'hashlib', 'hmac', 'json', 'logging', 're',
                  'typing', 'urllib', 'urllib.parse'}
FORBIDDEN = {'socket', 'ssl', 'http', 'http.client', 'urllib.request', 'requests', 'urllib3', 'aiohttp', 'httpx',
             'os', 'time', 'datetime', 'random', 'uuid', 'subprocess', 'threading'}
LEGACY = {'binance_client', 'engine', 'app', 'backtest', 'strategies', 'grid', 'exchange_rules', 'market_data',
          'instance', 'telegram_ctl', 'ai_filter', 'lab', 'replay', 'verify', 'feasibility', 'trade_audit'}


def _modules():
    for name in sorted(os.listdir(PKG)):
        if name.endswith('.py'):
            yield name, ast.parse(open(os.path.join(PKG, name), encoding='utf-8').read())


TRANSPORT_CORE = {'__init__.py', 'guard.py', 'signing.py', 'wire.py', 'errors.py', 'records.py', 'outcomes.py',
                  'transport.py'}


@pytest.mark.parametrize('fname', sorted(TRANSPORT_CORE))
def test_transport_core_imports_only_stdlib_allowlist(fname):
    tree = ast.parse(open(os.path.join(PKG, fname), encoding='utf-8').read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                                 # relative: inside newcore.venue only
                assert node.level == 1, fname
                continue
            names = [node.module]
        else:
            continue
        for n in names:
            assert n in ALLOWED_STDLIB, f'{fname} imports {n}'
            assert n.split('.')[0] not in LEGACY and n not in FORBIDDEN, f'{fname} imports {n}'


def test_no_module_imports_legacy_or_network():
    for fname, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or '']
                for n in names:
                    assert n.split('.')[0] not in LEGACY, f'{fname} imports legacy {n}'
                    assert n not in ('socket', 'requests', 'urllib.request', 'http.client', 'urllib3'), \
                        f'{fname} imports network module {n}'


def test_import_needs_no_credentials_and_touches_nothing():
    """Fresh interpreter: importing every module neither needs credentials nor imports network modules."""
    root = os.path.dirname(os.path.dirname(PKG))
    mods = [n[:-3] for n in os.listdir(PKG) if n.endswith('.py') and n != '__init__.py']
    code = ('import sys; sys.path.insert(0, sys.argv[1]); '
            + '; '.join(f'import newcore.venue.{m}' for m in mods)
            + "; bad=[m for m in ('socket','requests','urllib.request','http.client','binance_client','engine')"
              " if m in sys.modules]; print('BAD' if bad else 'CLEAN', bad)")
    env = {k: v for k, v in os.environ.items() if 'KEY' not in k.upper() and 'SECRET' not in k.upper()}
    r = subprocess.run([sys.executable, '-I', '-c', code, root], cwd=root, capture_output=True, text=True,
                       encoding='utf-8', env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith('CLEAN'), r.stdout
