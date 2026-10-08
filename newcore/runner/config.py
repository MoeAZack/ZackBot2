"""The run configuration of `python -m newcore.run` (one TOML or JSON file). Validated at load; refused, never fixed up.

    mode = "PAPER"                       # PAPER | TESTNET only. Anything else (LIVE, MAINNET, REAL, ...) is refused.
    [venue]
    kind = "fake"                        # fake (FakeVenue over data_long, state saved between runs) | replay | testnet
    factory = "pkg.mod:make"             # testnet ONLY: the adapter factory hook, factory(config) -> dict(venue=,
                                         # bars=, account_reads=); the TestnetVenue adapter is the transport lane's
    data_root = "."                      # fake / replay: the repository root holding data_long/ and data/
    start = "2022-02-25 00:00:00"        # fake / replay: first candle the venue plays (UTC); default the first
    end = ""                             # replay: last candle close (UTC); default the end of the data
    [journal]
    dir = ""                             # default %LOCALAPPDATA%\\ZackBotNC\\data; never inside %LOCALAPPDATA%\\ZackBot
    [strategy]
    rule = "trend_ema_mom.v1"            # the only rule; DISABLED unless enabled = true AND --enable-candidate
    enabled = false
    mirrored_short = false               # the mirrored short fixture (mechanics only, never an edge claim)
    symbols = ["BTCUSDT"]
    tf = "4h"
    [book]                               # the canary: max_pos 4 / 3x / 3% Cairo day / 10% DD kill
    risk_pct = "0.01"; max_positions = 4; max_leverage = "3"; cap_gap_buffer = "0.10"
    daily_loss_pct = "0.03"; kill_drawdown_pct = "0.10"
    [cycle]
    cadence_s = 0                        # pause between cycles in a loop (seconds); 0 = back to back
    delay_s = 15                         # testnet: seconds after the candle close before the cycle runs
    mark_poll_s = 10                     # testnet + management: mark-price polls between candle closes (0 = off)
    tick_s = 30                          # testnet: check-only cycle between candles while needs_tick() (0 = off)
    [reconcile]                          # REC-02 (newcore.reconcile); OFF by default until the gate flips it
    rec02 = false
    [management]                         # M4 position management (NC-07 driver); OFF by default
    enabled = false
    plan = "range_bb_mr_v1"              # the only plan: a DISABLED mechanics fixture (4h), no edge claimed
    cap_mult = "2.5"                     # plan risk cap = cap_mult x the entry's risk to its stop (reserved add)
    [tnet]                               # TNET-01 harness hooks: TESTNET + venue.kind testnet ONLY (refused otherwise)
    enabled = false
    raw_qty = ""                         # H2: send this entry qty unsized (the venue's min-qty refusal, T10b)
    [account]
    name = "nc-smoke"                   # account / portfolio ids derive from it (or give id / portfolio_id)
    equity = "10000"                     # fake / replay starting equity
    key_digest = "0123456789abcdef"      # the binding's NON-secret 16-hex key digest (never a key)

No credential of any kind is accepted (a key / secret / token / password field anywhere is refused): keys never live in
this file. Mainnet is impossible by construction: the mode allow-list, the venue-kind allow-list, and a scan that refuses
any value naming live or mainnet.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

MODES = ('PAPER', 'TESTNET')
KINDS = {'PAPER': ('fake', 'replay'), 'TESTNET': ('testnet',)}
FORBIDDEN_VALUE = re.compile(r'(^|[^a-z])(live|mainnet|real|prod|production)([^a-z]|$)', re.I)
FORBIDDEN_KEY = re.compile(r'(key|secret|token|password|passphrase|api)', re.I)
ALLOWED_KEY_NAMES = {'key_digest'}                 # the binding's non-secret digest
TIMEFRAMES = ('1h', '4h')
RULES = ('trend_ema_mom.v1',)
SECTIONS = {
    '': {'mode', 'venue', 'journal', 'strategy', 'book', 'cycle', 'account', 'output', 'management', 'tnet',
         'reconcile'},
    'venue': {'kind', 'factory', 'data_root', 'start', 'end'},
    'journal': {'dir'},
    'strategy': {'rule', 'enabled', 'mirrored_short', 'symbols', 'tf'},
    'book': {'risk_pct', 'max_positions', 'max_leverage', 'cap_gap_buffer', 'daily_loss_pct', 'kill_drawdown_pct'},
    'cycle': {'cadence_s', 'delay_s', 'mark_poll_s', 'tick_s'},
    'reconcile': {'rec02'},
    'account': {'name', 'id', 'portfolio_id', 'equity', 'key_digest'},
    'output': {'dir'},
    'management': {'enabled', 'plan', 'cap_mult'},
    'tnet': {'enabled', 'raw_qty'},
}
PLANS = ('range_bb_mr_v1',)


class ConfigError(ValueError):
    """The configuration is refused (the run does not start)."""


@dataclass(frozen=True)
class RunConfig:
    mode: str
    venue_kind: str
    factory: str | None
    data_root: str
    start: str | None
    end: str | None
    journal_dir: str
    output_dir: str
    rule: str
    enabled: bool
    mirrored_short: bool
    symbols: tuple
    tf: str
    risk_pct: Decimal
    max_positions: int | None
    max_leverage: Decimal
    cap_gap_buffer: Decimal
    daily_loss_pct: Decimal | None
    kill_drawdown_pct: Decimal | None
    cadence_s: float
    delay_s: float
    account_id: str
    portfolio_id: str
    equity: Decimal
    key_digest: str
    source: str = field(default='')
    mg_enabled: bool = False                       # [management] enabled: position management OFF unless true
    mg_plan: str = 'range_bb_mr_v1'
    mg_cap_mult: Decimal = Decimal('2.5')
    mark_poll_s: float = 10.0                      # [cycle] testnet + management: mark polls between closes (0 = off)
    tick_s: float = 30.0                           # [cycle] tick_s: testnet check-only cycles between candles
    rec02: bool = False                            # [reconcile] rec02: the REC-02 fold in the cycle
    tnet_enabled: bool = False                     # [tnet] the TNET-01 harness hooks (TESTNET + testnet venue only)
    tnet_raw_qty: Decimal | None = None            # H2: unsized entry quantity (T10b venue min-qty refusal)


def _scan(node, path=''):
    """Refuse credentials anywhere and any value naming live / mainnet."""
    if isinstance(node, dict):
        for k, v in node.items():
            p = f'{path}.{k}' if path else k
            if FORBIDDEN_KEY.search(k) and k not in ALLOWED_KEY_NAMES:
                raise ConfigError(f'{p}: credentials never live in the run config (no keys / secrets / tokens)')
            _scan(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _scan(v, f'{path}[{i}]')
    elif isinstance(node, str) and FORBIDDEN_VALUE.search(node):
        raise ConfigError(f'{path}={node!r}: live / mainnet values are refused (PAPER / TESTNET only)')


def _sections(doc):
    unknown = set(doc) - SECTIONS['']
    if unknown:
        raise ConfigError(f'unknown top-level keys {sorted(unknown)}')
    for name, keys in SECTIONS.items():
        if name and name in doc:
            if not isinstance(doc[name], dict):
                raise ConfigError(f'[{name}] must be a table')
            extra = set(doc[name]) - keys
            if extra:
                raise ConfigError(f'[{name}]: unknown keys {sorted(extra)}')


def _dec(v, path, *, lo=None, hi=None, optional=False):
    if v is None or v == '':
        if optional:
            return None
        raise ConfigError(f'{path} is required')
    if isinstance(v, bool) or not isinstance(v, (str, int)):
        raise ConfigError(f'{path}: give a decimal string (no float), not {v!r}')
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise ConfigError(f'{path}: {v!r} is not a decimal') from None
    if not d.is_finite() or (lo is not None and d < lo) or (hi is not None and d > hi):
        raise ConfigError(f'{path}={v!r} outside [{lo}, {hi}]')
    return d


def _id(prefix, name):
    return f'{prefix}_' + hashlib.sha256(f'zackbot.newcore.run.{prefix}.v1\x00{name}'.encode()).hexdigest()[:32]


def default_journal_dir(environ=None):
    env = os.environ if environ is None else environ
    base = env.get('LOCALAPPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'ZackBotNC', 'data')


def _inside(path, root):
    path, root = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:                                   # different drives
        return False


def load(path, *, environ=None):
    with open(path, 'rb') as fh:
        raw = fh.read()
    if path.lower().endswith('.toml'):
        import tomllib
        doc = tomllib.loads(raw.decode('utf-8'))
    else:
        doc = json.loads(raw.decode('utf-8'))
    return validate(doc, source=path, environ=environ)


def validate(doc, *, source='', environ=None):
    if not isinstance(doc, dict):
        raise ConfigError('the config is one table')
    _scan(doc)
    _sections(doc)
    env = os.environ if environ is None else environ
    mode = doc.get('mode', 'PAPER')
    if mode not in MODES:
        raise ConfigError(f'mode={mode!r}: only {MODES} (no live / mainnet mode exists)')
    v, j, s, b, c, a, o = (doc.get(k, {}) for k in ('venue', 'journal', 'strategy', 'book', 'cycle', 'account', 'output'))
    kind = v.get('kind', 'fake' if mode == 'PAPER' else 'testnet')
    if kind not in KINDS[mode]:
        raise ConfigError(f'venue.kind={kind!r} is not allowed in {mode} (allowed {KINDS[mode]})')
    factory = v.get('factory') or None
    if kind == 'testnet':
        if not (isinstance(factory, str) and re.fullmatch(r'[A-Za-z_][\w.]*:[A-Za-z_]\w*', factory)):
            raise ConfigError('venue.factory "module:callable" is required for testnet (the adapter factory hook)')
    elif factory is not None:
        raise ConfigError('venue.factory is for testnet only')
    jdir = j.get('dir') or default_journal_dir(env)
    legacy = os.path.join(env.get('LOCALAPPDATA') or os.path.expanduser('~'), 'ZackBot')
    if _inside(jdir, legacy):
        raise ConfigError(f'journal.dir {jdir!r} is inside the legacy %LOCALAPPDATA%\\ZackBot: refused')
    odir = o.get('dir') or os.path.join(jdir, 'reports')
    if _inside(odir, legacy):
        raise ConfigError(f'output.dir {odir!r} is inside the legacy %LOCALAPPDATA%\\ZackBot: refused')
    rule = s.get('rule', 'trend_ema_mom.v1')
    if rule not in RULES:
        raise ConfigError(f'strategy.rule={rule!r}: only {RULES}')
    for flag in ('enabled', 'mirrored_short'):
        if type(s.get(flag, False)) is not bool:
            raise ConfigError(f'strategy.{flag} must be true / false')
    symbols = s.get('symbols', ['BTCUSDT'])
    if not (isinstance(symbols, list) and symbols and all(isinstance(x, str) and re.fullmatch(r'[A-Z0-9]{2,30}', x)
                                                         for x in symbols) and len(set(symbols)) == len(symbols)):
        raise ConfigError('strategy.symbols: a non-empty list of distinct symbols')
    tf = s.get('tf', '4h')
    if tf not in TIMEFRAMES:
        raise ConfigError(f'strategy.tf={tf!r}: one of {TIMEFRAMES}')
    mp = b.get('max_positions', 4)
    if mp is not None and (type(mp) is not int or mp < 1):
        raise ConfigError('book.max_positions: a positive int')
    name = a.get('name', 'nc-smoke')
    acct = a.get('id') or _id('acct', name)
    pf = a.get('portfolio_id') or _id('pf', name)
    if not re.fullmatch(r'acct_[0-9a-f]{32}', acct) or not re.fullmatch(r'pf_[0-9a-f]{32}', pf):
        raise ConfigError('account.id / account.portfolio_id must be acct_ / pf_ + 32 hex')
    digest = a.get('key_digest', '0123456789abcdef')
    if not re.fullmatch(r'[0-9a-f]{16}', digest):
        raise ConfigError('account.key_digest: the 16-hex NON-secret binding digest')
    for k in ('cadence_s', 'delay_s', 'mark_poll_s', 'tick_s'):
        if not isinstance(c.get(k, 0), (int, float)) or isinstance(c.get(k, 0), bool) or c.get(k, 0) < 0:
            raise ConfigError(f'cycle.{k}: seconds >= 0')
    r = doc.get('reconcile', {})
    if type(r.get('rec02', False)) is not bool:
        raise ConfigError('reconcile.rec02 must be true / false')
    m = doc.get('management', {})
    if type(m.get('enabled', False)) is not bool:
        raise ConfigError('management.enabled must be true / false')
    mg_plan = m.get('plan', 'range_bb_mr_v1')
    if mg_plan not in PLANS:
        raise ConfigError(f'management.plan={mg_plan!r}: only {PLANS} (a disabled mechanics fixture)')
    if m.get('enabled', False) and tf != '4h':
        raise ConfigError(f'management.plan={mg_plan!r} is a 4h plan; strategy.tf is {tf!r}')
    tn = doc.get('tnet', {})
    if type(tn.get('enabled', False)) is not bool:
        raise ConfigError('tnet.enabled must be true / false')
    if tn.get('enabled', False) and (mode != 'TESTNET' or kind != 'testnet'):
        raise ConfigError(f'[tnet] is the TNET-01 harness: TESTNET + venue.kind testnet only (got {mode} / {kind})')
    raw = _dec(tn.get('raw_qty'), 'tnet.raw_qty', lo=Decimal('1E-12'), optional=True)
    if raw is not None and not tn.get('enabled', False):
        raise ConfigError('tnet.raw_qty needs tnet.enabled = true (an explicit TESTNET-only override)')
    return RunConfig(
        tick_s=float(c.get('tick_s', 30)), rec02=r.get('rec02', False),
        tnet_enabled=tn.get('enabled', False), tnet_raw_qty=raw, mark_poll_s=float(c.get('mark_poll_s', 10)),
        mg_enabled=m.get('enabled', False), mg_plan=mg_plan,
        mg_cap_mult=_dec(m.get('cap_mult', '2.5'), 'management.cap_mult', lo=1, hi=10),
        mode=mode, venue_kind=kind, factory=factory, data_root=v.get('data_root', '.'), start=v.get('start') or None,
        end=v.get('end') or None, journal_dir=jdir, output_dir=odir, rule=rule, enabled=s.get('enabled', False),
        mirrored_short=s.get('mirrored_short', False), symbols=tuple(symbols), tf=tf,
        risk_pct=_dec(b.get('risk_pct', '0.01'), 'book.risk_pct', lo=Decimal('0.0001'), hi=Decimal('0.02')),
        max_positions=mp, max_leverage=_dec(b.get('max_leverage', '3'), 'book.max_leverage', lo=1, hi=5),
        cap_gap_buffer=_dec(b.get('cap_gap_buffer', '0.10'), 'book.cap_gap_buffer', lo=0, hi=1),
        daily_loss_pct=_dec(b.get('daily_loss_pct', '0.03'), 'book.daily_loss_pct', lo=Decimal('0.001'), hi=1,
                            optional=True),
        kill_drawdown_pct=_dec(b.get('kill_drawdown_pct', '0.10'), 'book.kill_drawdown_pct', lo=Decimal('0.01'), hi=1,
                               optional=True),
        cadence_s=float(c.get('cadence_s', 0)), delay_s=float(c.get('delay_s', 15)), account_id=acct, portfolio_id=pf,
        equity=_dec(a.get('equity', '10000'), 'account.equity', lo=1), key_digest=digest, source=source)
