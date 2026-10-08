"""Runner-driven TNET-01 scenarios: ONE entry point for both targets.

    run_scenario(spec, target, *, run_nonce, ...) -> ScenarioResult
    run_suite(specs, target, *, run_nonce, ...)   -> SuiteResult (preflight once, scenarios in order, exit code)

The Runner is constructed directly (plan 6.1) with a TNET RunnerConfig: 1m candles, the spec's sizing, the target's
instrument rules, ScriptedSignals named 'tnet_<nonce>' (fresh DecisionKeys -> fresh client ids per run), a fresh
MemoryJournal per scenario, and every submit through BoundedPort (order / notional caps + ledger).

Per scenario: steps -> expectations -> final exchange truth (T12: flat, or protected AND reconciled) -> guarded cleanup
(newcore.venue.tnet.guarded + tnet_cleanup on the raw venue; runs also on an exception / Ctrl+C).

Verdicts: PASS; FAIL (an assertion, a safety counter, a bound, an invariant breach or an exception); INCONCLUSIVE
(an await_exit elapsed without an exit - nothing about safety failed); SKIPPED (the spec does not target this target).
Always asserted, whatever the spec says: unprotected_cycles == 0, and cleanup CLEAN.

Exit codes (plan 6.6): 0 all PASS + cleanup clean; 1 an INCONCLUSIVE; 4 preflight refused; 6 deadline; 7 a FAIL;
8 cleanup residue (exposure may be left). (2 / 3 / 5 are the CLI's: usage, credentials, report.)

RUNNER HOOK REQUESTS (for the S1 lane; nothing in newcore/runner was edited):
  H1  Runner(..., distance_store=) or a journaled stop distance: after a REAL process restart before the first stop
      is placed, _entry_distance re-derives the distance from signals.decide; ScriptedSignals keeps its fired map only
      in-process, so T11 as a subprocess restart needs the distance persisted (or the signal source re-armed).
  H2  A TNET-only raw-quantity override for an entry (refused by config outside the harness profile), so T10b can be
      a GENUINE venue refusal (-4164 / -1111); today T10b is a synthetic refusal at the HTTP seam.
  H3  cycle(now_ms, decide=False) is used for reconcile-only ticks; an explicit Runner.reconcile_only(now_ms) that
      also skips _protect_all would let T12 read truth without side effects (today the final check calls reconcile()).
  H4  An observable per-intent route for the summary (Runner.summary() / TradeOutcome carry no stop route; the harness
      derives it with ports.keys.route_of).
"""
import hashlib
import time
from dataclasses import dataclass, field
from decimal import Decimal

from newcore.domain import (Account, AccountBinding, BindingConfirmation, BindingState, EntriesMode, IntentState,
                            Purpose, Venue, confirmation_phrase)
from newcore.ports import venue as P
from newcore.ports.keys import route_of
from newcore.runner import Runner, RunnerConfig, SizingPolicy
from newcore.runner.runner import InvariantBreach
from newcore.venue.tnet import guarded, is_newcore_cid

from .rspec import expectations, validate_rspec
from .seams import AdoptedView, BoundedPort, BoundExceeded, DeadlineExceeded  # noqa: F401  (re-exported)
from .signals import ScriptedSignals
from .targets import TF_MS

PASS, FAIL, INCONCLUSIVE, SKIPPED = 'PASS', 'FAIL', 'INCONCLUSIVE', 'SKIPPED'
EXIT_PASS, EXIT_INCONCLUSIVE, EXIT_PREFLIGHT, EXIT_DEADLINE, EXIT_FAIL, EXIT_RESIDUE = 0, 1, 4, 6, 7, 8
EXIT_NOTHING_RAN = 2                                                 # every selected scenario was SKIPPED
SIM_KEY_DIGEST = '0123456789abcdef'


def _h(*parts):
    return hashlib.sha256('\x00'.join(parts).encode('ascii')).hexdigest()[:32]


def scenario_nonce(run_nonce, scenario_id):
    """8 hex per (run, scenario): its own strategy name -> its own DecisionKeys / client ids (never reused)."""
    if not isinstance(run_nonce, str) or not run_nonce or len(run_nonce) > 64 or not run_nonce.isascii():
        raise ValueError('run_nonce must be short ascii text')
    return hashlib.sha256(f'tnet.scenario\x00{run_nonce}\x00{scenario_id}'.encode('ascii')).hexdigest()[:8]


def make_account(environment, account_id, key_digest):
    binding = AccountBinding(venue=Venue.BINANCE_USDM, environment=environment, settlement_asset='USDT',
                             key_digest=key_digest, exchange_uid=None)
    conf = BindingConfirmation(account_id=account_id, old_key_digest=None, new_key_digest=key_digest,
                               typed_phrase=confirmation_phrase(account_id, key_digest),
                               confirmed_at_ms=946_684_800_000)
    return Account(account_id=account_id, label='tnet', hedge_mode=True, binding=binding,
                   binding_state=BindingState.CONFIRMED, proposed_binding=None, confirmation=conf)


@dataclass
class ScenarioResult:
    id: str
    name: str
    target: str
    verdict: str
    assertions: list = field(default_factory=list)       # (name, ok, detail)
    counters: dict = field(default_factory=dict)
    orders: list = field(default_factory=list)
    ledger: list = field(default_factory=list)
    injected: list = field(default_factory=list)
    final_truth: dict = field(default_factory=dict)
    cleanup: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)
    error: str | None = None
    wall_s: float = 0.0
    interrupted: bool = False                              # Ctrl+C / SystemExit (N2)
    attempts: int = 1                                      # bracket scenarios: runs until observed (spec attempts)
    cycle_times: list = field(default_factory=list)        # the candle closes the Runner cycled at (replay tape)
    cassette: str | None = None                            # testnet: the sanitized cassette of this scenario

    @property
    def residue(self):
        return bool(self.cleanup) and not self.cleanup.get('clean', False)

    def as_dict(self):
        return {'id': self.id, 'name': self.name, 'target': self.target, 'verdict': self.verdict,
                'assertions': [{'name': n, 'ok': ok, 'detail': d} for n, ok, d in self.assertions],
                'counters': self.counters, 'orders': self.orders, 'ledger': self.ledger, 'injected': self.injected,
                'final_truth': self.final_truth, 'cleanup': self.cleanup, 'trades': self.trades, 'error': self.error,
                'wall_s': round(self.wall_s, 3), 'cycle_times': list(self.cycle_times), 'cassette': self.cassette}


class _Run:
    """The state of one scenario run (one Runner at a time; a restart swaps it)."""

    def __init__(self, spec, target, nonce, monotonic, baseline=None, adopted=()):
        self.baseline, self.adopted = dict(baseline or {}), tuple(adopted)
        self.spec, self.target, self.nonce = spec, target, nonce
        self.sym, self.side = spec['symbol'], spec['side']
        self.bound = spec['bound']
        self.monotonic, self.t0 = monotonic, monotonic()
        self.deadline = self.t0 + spec['bound']['max_wall_s']        # ABSOLUTE (monotonic seconds)
        self.ticks, self.now = 0, None
        self.cycle_times = []
        self.inconclusive = []
        self.checks = []
        self.signals = ScriptedSignals(nonce=nonce, stop=spec['stop'], window=30)
        self.ledger = []
        self.port = None
        self.runner = None

    def build(self):
        t = self.target
        if t.kind == 'testnet':
            acct_id, digest = t.config.account_id, t.config.key_digest
        else:
            acct_id, digest = 'acct_' + _h('tnet.fake.account', self.nonce), SIM_KEY_DIGEST
        self.account = make_account(t.environment, acct_id, digest)
        self.portfolio_id = 'pf_' + _h('tnet.portfolio', self.nonce, self.spec['id'])
        t.prepare(self.spec, self.account, self.portfolio_id)
        self.injected0 = len(t.injected)
        t_port = AdoptedView(t.port, baseline=self.baseline, adopted_orders=self.adopted) \
            if (self.baseline or self.adopted) else t.port
        self.truth_venue = AdoptedView(t.raw, baseline=self.baseline, adopted_orders=self.adopted) \
            if (self.baseline or self.adopted) else t.raw
        self.port = BoundedPort(t_port, max_orders=self.bound['max_orders'],
                                max_notional=Decimal(self.bound['max_notional_usdt']),
                                price_of=self.signals.last_close.get, ledger=self.ledger, expired=self.expired)
        sz = self.spec['sizing']
        self.config = RunnerConfig(account=self.account, portfolio_id=self.portfolio_id, symbols=(self.sym,),
                                   tf_ms=TF_MS, timeframe='1m', rules=dict(t.rules),
                                   sizing=SizingPolicy(risk_pct=Decimal(sz['risk_pct']),
                                                       max_leverage=Decimal(sz['max_leverage'])),
                                   sides=('LONG', 'SHORT'), strict=True)
        self.runner = self.new_runner()

    def new_runner(self):
        m = self.spec.get('management')
        if m is not None and m['enabled']:                  # T05-T08: the ManagedRunner (management.enabled)
            from newcore.management import CostModel as MgCosts
            from newcore.runner.managed import ManagedRunner, ManagementConfig, SyntheticPlans
            kw = {k: (Decimal(v) if isinstance(v, str) else v) for k, v in m['plan'].items()}
            plans = SyntheticPlans(costs=MgCosts(taker_fee=Decimal('0.0005'), slip=Decimal('0.0002')), **kw)
            return ManagedRunner(self.config, journal=self.target.journal, venue=self.port, bars=self.target.bars,
                                 signals=self.signals, account_reads=self.target.reads,
                                 management=ManagementConfig(enabled=True, plans=plans))
        return Runner(self.config, journal=self.target.journal, venue=self.port, bars=self.target.bars,
                      signals=self.signals, account_reads=self.target.reads)

    # ------------------------------------------------------------------------------------------------ steps
    def remaining(self):
        return self.deadline - self.monotonic()

    def expired(self):
        return self.monotonic() > self.deadline

    def tick(self):
        """One cycle. The absolute deadline is checked BEFORE the candle wait, bounds the wait itself (a target never
        sleeps past it) and is checked again AFTER the wait, immediately before the Runner may send anything."""
        if self.ticks >= self.bound['max_ticks']:
            raise BoundExceeded(f'more than {self.bound["max_ticks"]} cycles')
        if self.expired():
            raise DeadlineExceeded(f'max_wall_s {self.bound["max_wall_s"]} elapsed before cycle {self.ticks + 1}')
        now = self.target.next_close(deadline_s=self.remaining())
        if self.expired():
            raise DeadlineExceeded(f'max_wall_s {self.bound["max_wall_s"]} elapsed during the candle wait '
                                   f'(cycle {self.ticks + 1}); nothing was sent')
        self.now = now
        self.ticks += 1
        self.cycle_times.append(now)
        self.runner.cycle(self.now)

    def step(self, st):
        op = st['op']
        if op in ('enter', 'close'):
            self.signals.arm(self.sym, op, st.get('side', self.side))
            self.tick()
        elif op == 'tick':
            for _ in range(st['n']):
                self.tick()
        elif op == 'fault':
            self.target.arm_fault(st['on'], st['kind'], st.get('code'))
        elif op == 'restart':
            self.target.reopen_journal()
            self.runner = self.new_runner()
        elif op == 'resume':
            ok = self.runner.resume(self.now)
            self.checks.append(('resume_succeeds', ok, f'mode {self.runner.fold.mode}'))
        elif op == 'await_exit':
            for _ in range(st['max_ticks']):
                self.tick()
                if not self.runner.fold.open_lots():
                    return True
            self.inconclusive.append(f'no exit within {st["max_ticks"]} cycles')
            return False
        elif op == 'move':
            pass                                                      # applied to the fake market up front
        elif op == 'check':
            truth = final_truth(self.runner, self.truth_venue, self.sym)
            self.checks.append((f'check_{st["what"]}@cycle{self.ticks}', truth['outcome'] == st['what'],
                                f'observed {truth["outcome"]}'))
        return True


def final_truth(runner, venue, symbol):
    """T12: a fresh reconciliation plus a direct read of the symbol. outcome = 'flat' (reconciled, no position, no
    NEWCORE order), 'protected_reconciled' (reconciled, every open lot's stop WORKING and listed) or 'unreconciled'."""
    rec = runner.reconcile()
    pos, oo = venue.positions(symbol), venue.open_orders(symbol)
    doc = {'reconciliation_id': rec.reconciliation_id, 'reconciled': rec.ok, 'items': [list(x) for x in rec.items]}
    if pos.kind is not P.ReadKind.OK or oo.kind is not P.ReadKind.OK:
        doc.update(outcome='unreconciled', detail='venue unreadable')
        return doc
    open_pos = [(p.side, str(p.qty)) for p in pos.value if p.qty != 0]
    nc_orders = [o.ref.client_id for o in oo.value if is_newcore_cid(o.ref.client_id)]
    doc.update(positions=open_pos, newcore_orders=nc_orders)
    listed = {o.ref.client_id for o in oo.value}
    lots = runner.fold.open_lots()
    protected = bool(lots) and all(x.live_stop is not None and x.live_stop.state is IntentState.WORKING and
                                   x.live_stop.intent.client_order_id in listed for x in lots)
    if rec.ok and not open_pos and not nc_orders and not lots:
        doc['outcome'] = 'flat'
    elif rec.ok and protected:
        doc['outcome'] = 'protected_reconciled'
    else:
        doc['outcome'] = 'unreconciled'
    return doc


def _orders(runner):
    out = []
    for iv in runner.fold.intents.values():
        it = iv.intent
        out.append({'intent': iv.intent_id, 'client_id': it.client_order_id, 'purpose': str(iv.purpose),
                    'route': route_of(iv.intent_id, it.client_order_id), 'state': str(iv.state),
                    'phases': [str(r.phase) for r in iv.results], 'qty': str(it.qty),
                    'executed': None if iv.final is None or iv.final.executed_qty is None else str(iv.final.executed_qty),
                    'avg': None if iv.final is None or iv.final.avg_price is None else str(iv.final.avg_price),
                    'exchange_order_id': None if iv.final is None else iv.final.exchange_order_id})
    return out


def fills_match(runner, venue, rules):
    """Every FINAL market intent with an execution: sum(fills qty) == executed and |fills VWAP - avg| <= one tick."""
    problems, checked = [], 0
    for iv in runner.fold.intents.values():
        if iv.purpose not in (Purpose.ENTRY, Purpose.CLOSE, Purpose.REDUCE) or iv.final is None:
            continue
        f = iv.final
        if not f.executed_qty or f.exchange_order_id is None:
            continue
        checked += 1
        r = venue.fills(iv.intent.symbol, f.exchange_order_id)
        if r.kind is not P.ReadKind.OK or not r.value:
            problems.append(f'{iv.intent_id}: fills unreadable / empty')
            continue
        q = sum((x.qty for x in r.value), Decimal(0))
        vwap = sum((x.qty * x.price for x in r.value), Decimal(0)) / q
        tick = rules[iv.intent.symbol].tick_size
        if q != f.executed_qty or abs(vwap - f.avg_price) > tick:
            problems.append(f'{iv.intent_id}: fills {q} @ {vwap} vs FINAL {f.executed_qty} @ {f.avg_price}')
    return checked > 0 and not problems, problems, checked


def _evaluate(run, exp, truth):
    r = run.runner
    c = r.counters
    out = [('safety: unprotected_cycles == 0', c.unprotected_cycles == 0, str(c.unprotected_cycles))]
    entries = [iv for iv in r.fold.intents.values() if iv.purpose is Purpose.ENTRY]
    first = entries[0] if entries else None
    if 'entries' in exp:
        out.append((f'entries == {exp["entries"]}', c.entries == exp['entries'], str(c.entries)))
    if 'skips' in exp:
        out.append((f'skips == {exp["skips"]}', c.skips == exp['skips'], str(c.skips)))
    if 'skip_reason' in exp:
        reasons = [str(d.reason) for d in r.fold.decisions.values() if str(d.action) == 'skip']
        out.append((f'skip reason {exp["skip_reason"]}', bool(reasons) and set(reasons) == {exp['skip_reason']},
                    str(reasons)))
    if 'trades' in exp:
        codes = [t.exit_code for t in r.trades()]
        out.append((f'trades {exp["trades"]}', codes == exp['trades'], str(codes)))
    if 'entry_phases' in exp:
        ph = [str(x.phase) for x in first.results] if first else []
        out.append((f'entry phases {exp["entry_phases"]}', ph == exp['entry_phases'], str(ph)))
    if 'entry_state' in exp:
        st = str(first.state) if first else None
        out.append((f'entry state {exp["entry_state"]}', st == exp['entry_state'], str(st)))
    if 'hold_seen' in exp:
        out.append((f'hold seen == {exp["hold_seen"]}', (c.holds > 0) == exp['hold_seen'], str(c.holds)))
    if 'mode_end' in exp:
        m = str(r.fold.mode)
        out.append((f'mode at end {exp["mode_end"]}', m == exp['mode_end'], m))
    if 'max_orders' in exp:
        out.append((f'orders <= {exp["max_orders"]}', run.port.submits <= exp['max_orders'], str(run.port.submits)))
    if 'incidents' in exp:
        out.append((f'incidents == {exp["incidents"]}', c.incidents == exp['incidents'], str(c.incidents)))
    if 'fills_match' in exp:
        ok, problems, n = fills_match(r, run.target.raw, run.config.rules)
        out.append((f'fills match FINAL ({n} orders)', ok == exp['fills_match'], '; '.join(problems) or 'ok'))
    if 'stop_route' in exp:
        routes = [route_of(iv.intent_id, iv.intent.client_order_id) for iv in r.fold.intents.values()
                  if iv.purpose is Purpose.PROTECT and any(str(x.phase) in ('known', 'final') for x in iv.results)
                  and iv.state is not IntentState.REJECTED]
        want = exp['stop_route']
        ok = bool(routes) and (want == 'any' or routes[0] == want)
        out.append((f'stop route {want}', ok, str(routes)))
    if 'final' in exp:
        out.append((f'final truth {exp["final"]}', truth.get('outcome') == exp['final'], truth.get('outcome')))
    for key, purpose in (('adds', Purpose.ADD), ('reduces', Purpose.REDUCE)):
        if key in exp:
            n = sum(1 for iv in r.fold.intents.values() if iv.purpose is purpose and iv.executed > 0)
            out.append((f'{key} filled == {exp[key]}', n == exp[key], str(n)))
    if 'open_orders_end' in exp:                  # T08: NO stop / order of any kind may be left on the venue
        oo = run.target.raw.open_orders(run.sym)
        n = len(oo.value) if oo.kind is P.ReadKind.OK else None
        out.append((f'open orders at the end == {exp["open_orders_end"]}', n == exp['open_orders_end'], str(n)))
    return out


def run_scenario(spec, target, *, run_nonce, monotonic=time.monotonic, baseline=None, adopted=()):
    spec = validate_rspec(spec)
    res = ScenarioResult(spec['id'], spec['name'], target.kind, SKIPPED)
    if target.kind not in spec['targets']:
        res.error = f'not a {target.kind} scenario'
        return res
    nonce = scenario_nonce(run_nonce, spec['id'])
    run = _Run(spec, target, nonce, monotonic, baseline, adopted)
    state = {}

    def body():
        try:
            run.build()
            for st in spec['steps']:
                if not run.step(st):
                    break
            state['truth'] = final_truth(run.runner, run.truth_venue, run.sym)
        except (BoundExceeded, DeadlineExceeded, InvariantBreach) as ex:
            state['error'] = f'{type(ex).__name__}: {ex}'
        except Exception as ex:                                       # noqa: BLE001 - a FAIL, never a crash
            state['error'] = f'{type(ex).__name__}: {ex}'
        except (KeyboardInterrupt, SystemExit) as ex:                 # N2: stop here; the teardown still runs
            state['error'] = f'Interrupted ({type(ex).__name__})'
            state['interrupted'] = True

    def clean():
        if run.port is None:                                          # build failed: nothing was sent
            return None
        return target.cleanup([run.sym], 'tnet_' + nonce, baseline=baseline)

    _, cleanup = guarded(body, clean)
    res.wall_s = monotonic() - run.t0
    res.ledger = list(run.ledger)
    res.cycle_times = list(run.cycle_times)
    res.injected = [list(x) for x in target.injected[run.injected0:]] if run.port is not None else []
    if cleanup is not None:
        res.cleanup = {'clean': cleanup.clean, 'attempts': cleanup.attempts,
                       'cancelled': [list(x) for x in cleanup.cancelled],
                       'closes': [[s, side, str(q), cid, k, None if e is None else str(e)]
                                  for s, side, q, cid, k, e in cleanup.closes],
                       'remaining_positions': [list(x) for x in cleanup.remaining_positions],
                       'remaining_orders': [list(x) for x in cleanup.remaining_orders]}
    if run.runner is not None:
        res.counters = {k: v for k, v in vars(run.runner.counters).items()}
        res.orders = _orders(run.runner)
        res.trades = [{'side': t.side, 'exit_code': t.exit_code, 'entry': str(t.entry_price), 'exit': str(t.exit_price),
                       'qty': str(t.qty), 'fees': str(t.fees), 'pnl': str(t.pnl), 'r': str(t.r)}
                      for t in _safe_trades(run.runner)]
    res.final_truth = state.get('truth', {})
    res.interrupted = bool(state.get('interrupted'))
    res.assertions = list(run.checks)
    if 'error' in state:
        res.error = state['error']
        res.assertions.append(('no error', False, state['error']))
        res.verdict = FAIL
    elif run.inconclusive:
        res.error = '; '.join(run.inconclusive)
        res.assertions.append(('safety: unprotected_cycles == 0', run.runner.counters.unprotected_cycles == 0,
                               str(run.runner.counters.unprotected_cycles)))
        res.verdict = FAIL if not all(ok for _, ok, _ in res.assertions) else INCONCLUSIVE
    else:
        try:
            res.assertions += _evaluate(run, expectations(spec, target.kind), res.final_truth)
        except Exception as ex:                                       # noqa: BLE001 - e.g. a fills read that fails
            res.error = f'{type(ex).__name__}: {ex}'
            res.assertions.append(('expectations evaluated', False, res.error))
        res.verdict = PASS if all(ok for _, ok, _ in res.assertions) else FAIL
        missed = _unobserved(run, expectations(spec, target.kind).get('observe')) if res.verdict == PASS else []
        if missed:                                    # a market event that did not happen: not a safety failure
            res.verdict = INCONCLUSIVE
            res.error = 'not observed: ' + '; '.join(missed)
    if cleanup is not None:
        res.assertions.append(('cleanup clean', cleanup.clean, f'attempts {cleanup.attempts}'))
        if not cleanup.clean:
            res.verdict = FAIL
    return res


def _unobserved(run, observe):
    """The 'observe' conditions (testnet price brackets) the market did not deliver this attempt."""
    if not observe:
        return []
    r, out = run.runner, []
    if 'trades_in' in observe:
        codes = [t.exit_code for t in _safe_trades(r)]
        if not codes or any(c not in observe['trades_in'] for c in codes):
            out.append(f'exits {codes} not in {observe["trades_in"]}')
    for key, purpose in (('reduces_min', Purpose.REDUCE), ('adds_min', Purpose.ADD)):
        if key in observe:
            n = sum(1 for iv in r.fold.intents.values() if iv.purpose is purpose and iv.executed > 0)
            if n < observe[key]:
                out.append(f'{key[:-4]} filled {n} < {observe[key]}')
    return out


def attempt_nonce(run_nonce, attempt):
    """Attempt 1 keeps the run nonce; a retry gets its own (fresh decision keys, fresh client ids)."""
    return run_nonce if attempt == 1 else f'{run_nonce}r{attempt}'


def _safe_trades(runner):
    try:
        return runner.trades()
    except Exception:                                                 # noqa: BLE001 - a report detail, not a verdict
        return []


def adopted_orders(pre):
    """The foreign open orders the preflight accepted (--adopt-foreign client ids)."""
    return tuple(o.ref.client_id for o in getattr(pre, 'orders', ()) if not is_newcore_cid(o.ref.client_id))


@dataclass
class SuiteResult:
    preflight: object
    scenarios: list
    exit_code: int


def suite_exit_code(results, preflight_ok=True):
    if not preflight_ok:
        return EXIT_PREFLIGHT
    if any(r.residue for r in results):
        return EXIT_RESIDUE
    if any(r.interrupted for r in results):
        return EXIT_DEADLINE                                         # as the venue CLI's Ctrl+C
    if not results or all(r.verdict == SKIPPED for r in results):
        return EXIT_NOTHING_RAN                                      # N5: SKIPPED is never a pass
    if any(r.error and r.error.startswith('DeadlineExceeded') for r in results):
        return EXIT_DEADLINE
    if any(r.verdict == FAIL for r in results):
        return EXIT_FAIL
    if any(r.verdict == INCONCLUSIVE for r in results):
        return EXIT_INCONCLUSIVE
    return EXIT_PASS


def run_suite(specs, target, *, run_nonce, monotonic=time.monotonic, min_balance=None, adopt_foreign=(),
              on_result=None):
    """Preflight once (testnet: flat, hedge, no foreign orders, balance), then every spec in order. A scenario that
    leaves residue stops the suite (nothing else may run on an account that may hold exposure)."""
    symbols = sorted({s['symbol'] for s in specs if target.kind in s['targets']})
    kw = {} if min_balance is None else {'min_balance': min_balance}
    pre = target.preflight(symbols, adopt_foreign=adopt_foreign, **kw)
    if not pre.ok:
        return SuiteResult(pre, [], EXIT_PREFLIGHT)
    results = []
    for spec in specs:
        for attempt in range(1, spec.get('attempts', 1) + 1):
            r = run_scenario(spec, target, run_nonce=attempt_nonce(run_nonce, attempt), monotonic=monotonic,
                             baseline=pre.baseline, adopted=adopted_orders(pre))
            r.attempts = attempt
            if r.verdict != INCONCLUSIVE or r.residue or r.interrupted:
                break
        results.append(r)
        if on_result is not None:
            on_result(r)
        if r.residue or r.interrupted:
            break
    return SuiteResult(pre, results, suite_exit_code(results))
