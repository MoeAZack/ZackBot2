"""TNET-01 venue-side harness pieces (roadmap PR #42 C11: a bounded, repeatable harness that owns setup, assertions and
cleanup and produces one redacted exact-build report).

tnet_preflight(venue, account_reader, symbols, ...) -> PreflightResult
    testnet host + hedge mode + a minimum disposable balance + the scenario symbols flat with no open orders. Anything
    foreign (an order whose client id is not NEWCORE, or a position NEWCORE did not open) refuses, unless listed in
    adopt_foreign (client ids, or 'SYMBOL:SIDE' for a position, which then becomes the cleanup baseline).
tnet_cleanup(venue, symbols, ...) -> CleanupResult
    guaranteed teardown: cancel every open NEWCORE order (zbn1o- / zbn1a- / zbn1e-), flatten every position above its
    adopted baseline with a reduce-only MARKET close under a deterministic client id, re-read, repeat up to
    max_attempts. Never touches a foreign order. Never raises: every failure is a note. Exit code CLEANUP_CLEAN or
    CLEANUP_DIRTY (distinct), with the remaining exchange truth.
guarded(run, cleanup)
    runs `run`, then ALWAYS `cleanup` (in finally, also on Ctrl+C / SIGINT; a second Ctrl+C cannot abort the teardown).
tnet_report(...) -> (json path, markdown path)
    one redacted JSON + Markdown report (exact build, config digest, per-scenario assertions, final exchange truth,
    fees / PnL, cassette path) written to %LOCALAPPDATA%\\ZackBotNC\\reports and leak-audited before it is written.
"""
import hashlib
import json
import os
import re
import signal
import subprocess
from dataclasses import dataclass, field
from decimal import Decimal

from newcore.ports import keys as K
from newcore.ports import venue as P
from newcore.ports.values import PortValueError

from .cassette import _sensitive_json_leftovers, _sensitive_text_leftovers
from .credentials import CredentialStoreError, check_root
from .guard import TESTNET_BASE_URL, TESTNET_ENVIRONMENT
from .redact import contains_values, is_sensitive_name
from .testnet_venue import HedgeModeRequired, VenueBootUnknown

NEWCORE_CID_RE = re.compile(r'zbn1[oae]-[a-z2-7]{26}')
CLEANUP_CLEAN, CLEANUP_DIRTY = 0, 9
PREFLIGHT_REFUSED = 10
DEFAULT_MIN_BALANCE = Decimal('100')


def is_newcore_cid(cid):
    return isinstance(cid, str) and NEWCORE_CID_RE.fullmatch(cid) is not None


def _read(fn, *a, **k):
    """A port read that never raises (an exception is just an unknown answer)."""
    try:
        return fn(*a, **k)
    except Exception as ex:
        return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=1_000_000_000_000,
                             detail=f'exception_{type(ex).__name__}'[:32])


# ---------------------------------------------------------------------------------------------- preflight
@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    refusals: tuple                  # stable tokens, e.g. 'one_way_mode', 'foreign_order:SOLUSDT:web_x'
    available_balance: object        # Decimal | None
    positions: tuple                 # VenuePosition with qty > 0 on the scenario symbols
    orders: tuple                    # VenueOrder open on the scenario symbols
    baseline: dict                   # {(symbol, side): qty} adopted foreign positions (cleanup keeps them)

    @property
    def exit_code(self):
        return 0 if self.ok else PREFLIGHT_REFUSED


def tnet_preflight(venue, account_reader, symbols, *, min_balance=DEFAULT_MIN_BALANCE, asset='USDT',
                   adopt_foreign=()):
    refusals, positions, orders, baseline = [], [], [], {}
    adopt = set(adopt_foreign)
    transport = getattr(venue, 'transport', None)
    if getattr(transport, 'base_url', None) != TESTNET_BASE_URL or \
            getattr(transport, 'environment', None) != TESTNET_ENVIRONMENT:
        refusals.append('not_testnet_host')
    try:
        venue.check_hedge_mode()
    except HedgeModeRequired:
        refusals.append('one_way_mode')
    except VenueBootUnknown:
        refusals.append('position_mode_unknown')
    balance = None
    eq = _read(account_reader.equity, asset)
    if eq.kind is P.ReadKind.OK:
        balance = eq.value[0].available_balance
        if balance < min_balance:
            refusals.append(f'balance_below_min:{balance}<{min_balance}')
    else:
        refusals.append(f'balance_unknown:{eq.detail or eq.kind.value}')
    for sym in symbols:
        pos = _read(venue.positions, sym)
        if pos.kind is not P.ReadKind.OK:
            refusals.append(f'positions_unknown:{sym}')
        else:
            for p in pos.value:
                if p.qty == 0:
                    continue
                positions.append(p)
                key = f'{p.symbol}:{p.side}'
                if key in adopt:
                    baseline[(p.symbol, p.side)] = p.qty
                else:
                    refusals.append(f'not_flat:{key}')
        oo = _read(venue.open_orders, sym)
        if oo.kind is not P.ReadKind.OK:
            refusals.append(f'orders_unknown:{sym}')
        else:
            for o in oo.value:
                orders.append(o)
                cid = o.ref.client_id
                if is_newcore_cid(cid):
                    refusals.append(f'leftover_newcore_order:{sym}:{cid}')      # run tnet_cleanup first
                elif cid not in adopt:
                    refusals.append(f'foreign_order:{sym}:{cid}')
    return PreflightResult(not refusals, tuple(refusals), balance, tuple(positions), tuple(orders), baseline)


# ---------------------------------------------------------------------------------------------- cleanup
@dataclass
class CleanupResult:
    clean: bool
    attempts: int
    cancelled: list = field(default_factory=list)       # (symbol, client id, outcome kind)
    closes: list = field(default_factory=list)          # (symbol, side, qty, client id, outcome kind, executed)
    remaining_positions: list = field(default_factory=list)
    remaining_orders: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def exit_code(self):
        return CLEANUP_CLEAN if self.clean else CLEANUP_DIRTY


def cleanup_client_ref(run_id, symbol, side, attempt):
    """Deterministic reduce-only close id per (run, symbol, side, attempt): a re-run of the same attempt reuses it
    (the venue's duplicate-id answer then means the close exists); a new attempt never collides with an old one."""
    intent = 'int_' + hashlib.sha256(f'zackbot.newcore.tnet.cleanup.v1\x00{run_id}\x00{symbol}\x00{side}\x00'
                                     f'{attempt}'.encode('ascii')).hexdigest()[:32]
    return P.OrderRef(symbol=symbol, client_id=K.client_id_for(intent, 'classic'))


def _cancel(venue, order):
    try:
        return venue.cancel(order.ref).kind.value
    except PortValueError:                       # e.g. an emergency zbn1e- id the port's submit grammar does not know
        t = getattr(venue, 'transport', None)
        if t is None:
            return 'not_cancellable'
        try:
            out = (t.cancel_algo_order(order.ref.client_id) if order.ref.route == 'algo'
                   else t.cancel_order(order.ref.symbol, order.ref.client_id))
            return out.kind.value
        except Exception as ex:
            return f'error_{type(ex).__name__}'
    except Exception as ex:
        return f'error_{type(ex).__name__}'


def _truth(venue, symbols, baseline):
    """(clean, newcore orders open, positions above baseline, notes) from a fresh read of every symbol."""
    orders, positions, notes, known = [], [], [], True
    for sym in symbols:
        oo = _read(venue.open_orders, sym)
        if oo.kind is P.ReadKind.OK:
            orders += [o for o in oo.value if is_newcore_cid(o.ref.client_id)]
        else:
            known = False
            notes.append(f'orders_unknown:{sym}:{oo.detail or oo.kind.value}')
        pos = _read(venue.positions, sym)
        if pos.kind is P.ReadKind.OK:
            positions += [p for p in pos.value if p.qty > baseline.get((p.symbol, p.side), Decimal(0))]
        else:
            known = False
            notes.append(f'positions_unknown:{sym}:{pos.detail or pos.kind.value}')
    return known and not orders and not positions, orders, positions, notes


def tnet_cleanup(venue, symbols, *, run_id, max_attempts=3, baseline=None):
    baseline = dict(baseline or {})
    res = CleanupResult(clean=False, attempts=0)
    for attempt in range(1, max_attempts + 1):
        res.attempts = attempt
        clean, orders, positions, notes = _truth(venue, symbols, baseline)
        res.notes += [f'attempt {attempt}: {n}' for n in notes]
        if clean:
            res.clean = True
            break
        for o in orders:                                        # NEWCORE orders only: foreign ones are never touched
            res.cancelled.append((o.ref.symbol, o.ref.client_id, _cancel(venue, o)))
        if orders:                                              # a cancel can race a fill: re-read positions after
            _, _, positions, more = _truth(venue, symbols, baseline)
            res.notes += [f'attempt {attempt} after cancels: {n}' for n in more]
        for p in positions:
            qty = p.qty - baseline.get((p.symbol, p.side), Decimal(0))
            ref = cleanup_client_ref(run_id, p.symbol, p.side, attempt)
            try:
                out = venue.submit_market(P.MarketOrder(ref=ref, position_side=p.side, qty=qty, reduce=True))
                res.closes.append((p.symbol, p.side, qty, ref.client_id, out.kind.value, out.executed_qty))
            except Exception as ex:
                res.closes.append((p.symbol, p.side, qty, ref.client_id, f'error_{type(ex).__name__}', None))
    else:
        clean, orders, positions, notes = _truth(venue, symbols, baseline)
        res.notes += [f'final: {n}' for n in notes]
        res.clean = clean
    if not res.clean:
        _, orders, positions, _ = _truth(venue, symbols, baseline)
    else:
        orders, positions = [], []
    res.remaining_orders = [(o.ref.symbol, o.ref.client_id, o.order_type, str(o.qty)) for o in orders]
    res.remaining_positions = [(p.symbol, p.side, str(p.qty)) for p in positions]
    return res


def format_cleanup(res):
    lines = [f'CLEANUP {"CLEAN" if res.clean else "NOT CLEAN"} after {res.attempts} attempt(s) '
             f'(exit {res.exit_code})']
    lines += [f'  cancel {s} {cid}: {k}' for s, cid, k in res.cancelled]
    lines += [f'  close {s} {side} {q} {cid}: {k} executed {e}' for s, side, q, cid, k, e in res.closes]
    if not res.clean:
        lines.append('  REMAINING EXCHANGE TRUTH (check the testnet UI):')
        lines += [f'    position {s} {side} {q}' for s, side, q in res.remaining_positions]
        lines += [f'    order {s} {cid} {t} {q}' for s, cid, t, q in res.remaining_orders]
        lines += [f'    note {n}' for n in res.notes]
    return '\n'.join(lines)


def guarded(run, cleanup):
    """run() then ALWAYS cleanup(): in finally, also on Ctrl+C (SIGINT). While the teardown runs, SIGINT is ignored so a
    second Ctrl+C cannot abort it. Returns (run result or None, cleanup result); re-raises run's exception after the
    teardown."""
    prev = signal.getsignal(signal.SIGINT)

    def on_sigint(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, on_sigint)
    result, cleanup_result = None, None
    try:
        result = run()
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            cleanup_result = cleanup()
        finally:
            signal.signal(signal.SIGINT, prev)
    return result, cleanup_result


# ---------------------------------------------------------------------------------------------- report
class ReportLeak(Exception):
    """A secret value or a sensitive field is present in the report; nothing was written."""


@dataclass(frozen=True)
class ScenarioOutcome:
    name: str
    passed: bool
    assertions: tuple                # ((text, ok), ...)


def git_build(repo_root, *, run=None):
    """{'sha': HEAD, 'dirty': bool} of the repository (exact build). Unknown -> sha None, dirty True."""
    run = run or (lambda args: subprocess.run(['git', *args], cwd=repo_root, capture_output=True, text=True,
                                              timeout=30))
    try:
        head = run(['rev-parse', 'HEAD'])
        status = run(['status', '--porcelain'])
        sha = head.stdout.strip() if head.returncode == 0 else None
        if sha is not None and not re.fullmatch(r'[0-9a-f]{40}', sha):
            sha = None
        return {'sha': sha, 'dirty': sha is None or status.returncode != 0 or bool(status.stdout.strip())}
    except (OSError, subprocess.SubprocessError):
        return {'sha': None, 'dirty': True}


def config_digest(config):
    """sha256 of the canonical config. A secret-named field (key / secret / token / password / ...) is refused."""
    def walk(node, path=''):
        if isinstance(node, dict):
            for k, v in node.items():
                if is_sensitive_name(k) and k != 'key_digest':
                    raise ReportLeak(f'config field {path + k} looks like a credential; refusing to digest it')
                walk(v, f'{path}{k}.')
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v, path)
    walk(config)
    text = json.dumps(config, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def default_report_dir():
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        raise CredentialStoreError('LOCALAPPDATA is not set: pass an explicit report directory')
    return os.path.join(base, 'ZackBotNC', 'reports')


def _markdown(doc):
    b = doc['build']
    lines = [f"# TNET-01 report {doc['run_id']}", '',
             f"- build: `{b['sha']}`{' (DIRTY tree)' if b['dirty'] else ''}",
             f"- config digest: `{doc['config_digest']}`", f"- cassette: `{doc['cassette']}`",
             f"- result: **{'PASS' if doc['passed'] else 'FAIL'}**", '', '## Scenarios', '']
    for s in doc['scenarios']:
        lines.append(f"### {'PASS' if s['passed'] else 'FAIL'} - {s['name']}")
        lines += [f"- [{'x' if ok else ' '}] {text}" for text, ok in s['assertions']]
        lines.append('')
    t = doc['final_exchange_truth']
    lines += ['## Final exchange truth', '', f"- clean: {t['clean']} (cleanup attempts {t['attempts']})"]
    lines += [f'- position {p}' for p in t['remaining_positions']] + [f'- order {o}' for o in t['remaining_orders']]
    lines += ['', '## Fees / PnL (disposable testnet)', '', f"- fees: {doc['fees']}", f"- realized pnl: {doc['pnl']}", '']
    return '\n'.join(lines)


def _audit(texts, values):
    for text in texts:
        if values and contains_values(text, values):
            raise ReportLeak('a secret value is present in the report; nothing was written')
        if _sensitive_text_leftovers(text):
            raise ReportLeak('a sensitive field still has a value in the report; nothing was written')


def tnet_report(*, run_id, scenarios, cleanup, config, cassette_path, fees, pnl, build, now_ms, out_dir=None,
                redact=()):
    """Write tnet-<now_ms>.json and .md (atomically) after a fail-closed leak audit. Returns (json path, md path)."""
    doc = {'format': 'zb-newcore-tnet-report/1', 'run_id': str(run_id), 'generated_ms': int(now_ms),
           'build': {'sha': build.get('sha'), 'dirty': bool(build.get('dirty'))},
           'config_digest': config_digest(config), 'cassette': cassette_path,
           'passed': bool(scenarios) and all(s.passed for s in scenarios) and cleanup.clean,
           'scenarios': [{'name': s.name, 'passed': bool(s.passed),
                          'assertions': [[str(t), bool(ok)] for t, ok in s.assertions]} for s in scenarios],
           'final_exchange_truth': {'clean': cleanup.clean, 'attempts': cleanup.attempts,
                                    'remaining_positions': [list(p) for p in cleanup.remaining_positions],
                                    'remaining_orders': [list(o) for o in cleanup.remaining_orders],
                                    'notes': list(cleanup.notes)},
           'fees': str(fees), 'pnl': str(pnl)}
    text_json = json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + '\n'
    text_md = _markdown(doc)
    if _sensitive_json_leftovers(json.loads(text_json)):
        raise ReportLeak('a sensitive JSON field still has a value in the report; nothing was written')
    _audit((text_json, text_md), [v for v in redact if isinstance(v, str)])
    directory = check_root(out_dir or default_report_dir())
    os.makedirs(directory, exist_ok=True)
    paths = []
    for ext, text in (('json', text_json), ('md', text_md)):
        path = os.path.join(directory, f'tnet-{int(now_ms)}.{ext}')
        tmp = f'{path}.tmp{os.getpid()}'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        paths.append(path)
    return tuple(paths)
