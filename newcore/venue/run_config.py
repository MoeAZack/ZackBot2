"""READ-ONLY view of a NEWCORE TESTNET run config for the venue-lane tools (tools/newcore_tnet.py --config).

The file is the runner's run config (newcore.runner.config on nc-s1-slice: TOML or JSON), e.g. the owner's
%LOCALAPPDATA%\\ZackBotNC\\config\\testnet.toml. This module only READS the fields a venue tool needs and never writes
the file:

    mode = "TESTNET"
    [venue]    kind = "testnet", factory = "newcore.venue.factory:build_testnet"
    [strategy] symbols = ["BTCUSDT", "ETHUSDT"]
    [account]  id = "acct_<32 hex>", key_digest = "<16 hex binding digest>", portfolio_id = "pf_<32 hex>"

Stricter than the runner loader where the tools need it: account.id and account.key_digest must be EXPLICIT (the
runner derives a default id from a name and has a placeholder digest; a venue tool must never attach to those).
A field whose name looks like a credential (key / secret / token / ...; key_digest excepted) refuses the file: keys
never live in a config.
"""
import json
import os
import re
from dataclasses import dataclass

from .redact import is_sensitive_name

FACTORY = 'newcore.venue.factory:build_testnet'
ACCOUNT_RE = re.compile(r'acct_[0-9a-f]{32}')
PORTFOLIO_RE = re.compile(r'pf_[0-9a-f]{32}')
DIGEST_RE = re.compile(r'[0-9a-f]{16}')
SYMBOL_RE = re.compile(r'[A-Z0-9]{2,30}')


class RunConfigError(ValueError):
    """The file is not a usable TESTNET run config (nothing was attached, nothing was written)."""


@dataclass(frozen=True)
class TestnetRunConfig:
    __test__ = False
    source: str
    account_id: str
    key_digest: str
    symbols: tuple
    portfolio_id: str | None


def default_testnet_config_path(environ=None):
    env = os.environ if environ is None else environ
    return os.path.join(env.get('LOCALAPPDATA') or os.path.expanduser('~'), 'ZackBotNC', 'config', 'testnet.toml')


MAX_DEPTH = 16


def _scan(root):
    """Iterative (no recursion: a deeply nested file is refused, never a RecursionError). Names only are echoed."""
    stack = [(root, '', 0)]
    while stack:
        node, path, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise RunConfigError(f'the config is nested deeper than {MAX_DEPTH} levels')   # no key path echoed
        if isinstance(node, dict):
            for k, v in node.items():
                if is_sensitive_name(str(k)) and k != 'key_digest':
                    raise RunConfigError('a credential-like field name in a run config is refused (keys live in '
                                         'the DPAPI store only)')
                stack.append((v, f'{path}{k}.' if isinstance(k, str) and len(k) <= 40 else f'{path}?.', depth + 1))
        elif isinstance(node, list):
            stack.extend((v, path, depth + 1) for v in node)


def parse_testnet_config(doc, source=''):
    if not isinstance(doc, dict):
        raise RunConfigError('the config is one table')
    _scan(doc)
    sections = {k: doc.get(k, {}) for k in ('venue', 'strategy', 'account')}
    if not all(isinstance(v, dict) for v in sections.values()):
        raise RunConfigError('venue / strategy / account must be tables')
    v, s, a = sections['venue'], sections['strategy'], sections['account']
    if doc.get('mode') != 'TESTNET':
        raise RunConfigError('mode: a TESTNET config is required')
    if v.get('kind', 'testnet') != 'testnet' or v.get('factory') != FACTORY:
        raise RunConfigError(f'venue.kind must be testnet and venue.factory {FACTORY}')
    acct, digest = a.get('id'), a.get('key_digest')
    if not (isinstance(acct, str) and ACCOUNT_RE.fullmatch(acct)):
        raise RunConfigError('account.id: an explicit acct_ + 32 hex is required')
    if not (isinstance(digest, str) and DIGEST_RE.fullmatch(digest)) or digest == '0123456789abcdef':
        raise RunConfigError('account.key_digest: the explicit 16-hex binding digest is required (not the placeholder)')
    pf = a.get('portfolio_id')
    if pf is not None and not (isinstance(pf, str) and PORTFOLIO_RE.fullmatch(pf)):
        raise RunConfigError('account.portfolio_id: pf_ + 32 hex')
    symbols = s.get('symbols', ['BTCUSDT'])
    if not (isinstance(symbols, list) and symbols and all(isinstance(x, str) and SYMBOL_RE.fullmatch(x)
                                                           for x in symbols) and len(set(symbols)) == len(symbols)):
        raise RunConfigError('strategy.symbols: a non-empty list of distinct symbols')
    return TestnetRunConfig(source=source, account_id=acct, key_digest=digest, symbols=tuple(symbols), portfolio_id=pf)


def load_testnet_config(path):
    """Read (never write) and validate. TOML by extension, else JSON."""
    try:
        with open(path, 'rb') as fh:
            raw = fh.read()
    except OSError as ex:
        raise RunConfigError(f'cannot read the config ({type(ex).__name__})') from None
    try:
        if str(path).lower().endswith('.toml'):
            import tomllib
            doc = tomllib.loads(raw.decode('utf-8'))
        else:
            doc = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError, RecursionError) as ex:
        raise RunConfigError(f'not valid TOML / JSON ({type(ex).__name__})') from None
    return parse_testnet_config(doc, source=str(path))
