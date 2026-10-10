"""A NEWCORE adapter for zb-golden/1 cases (the `newcore_sim` adapter, slice S1 + S4 scope).

Runs a case through the S1 Runner (one symbol) or the S4 BookRunner (several symbols or max_pos > 1): FakeVenue over the case's market (goldenlib/market.py 'flat' base + declared bars),
InjectedSignals at the case's signal bars (stop = slot.stop.atr x Wilder ATR14 of the signal candle), legacy_replay
sizing (risk_usd = equity x slot.risk x slot.share; qty = risk_usd / stop distance; leverage cap
account.max_leverage), the case's costs. Returns the canonical trace {trades: [{sym, side, i_in, i_out, exit, R, pnl}],
final: {lots}}.

S1 + S4 can express: market entries, an ATR stop, signal exits, a lost entry answer, one book across symbols with
max_pos and the Cairo-day halt (S4 BookRunner, legacy 8%), no other faults, no exchange filters, no management. Anything else
raises NotExpressible (never silently dropped): targets / trail / tp1 / time exits (S2), outage / restart faults (S3),
DCA / pyramid / trailing entry (range slice), `instruments` filters (S2).

M4: run_case(case, management=True) runs the S2 slots (target / tp1 / time_exit / trail) through the ManagedRunner: the
plan is the golden newcore_sim mapping (tests/golden/goldenlib/adapters/newcore_sim.py `_plan`: stop e -/+ stop_atr x
ATR, tp1 / target in R of that distance, break-even after tp1, time exit in candles, risk cap = budget + the plan's
reserved costs) plus trail = trail_atr x ATR. The runner decides bot-side triggers and management exits at the candle
close and its market orders fill at the next candle's open (FakeVenue), so a management exit is booked on its decision
candle (i_out = fill candle - 1), like a signal exit. Targets trigger INSIDE the candle (newcore/runner/intrabar.py:
the zb-path/1 walk feeds the runner its marks), so a target exit is booked on the candle it filled in.
"""
import json
import os
from decimal import Decimal

from newcore.adapters import CostModel, CsvBarSource, FakeVenue, MemoryJournal
from newcore.domain import ReasonCode
from newcore.ports.bars import Bar
from newcore.risk import BookPolicy
from newcore.runner import InjectedSignals, Runner, RunnerConfig, SizingPolicy, run_replay
from newcore.runner.book import BookRunner
from newcore.runner.intrabar import play_candle
from newcore.runner.managed import ManagedBookRunner, ManagedRunner, ManagementConfig
from slice_helpers import ACCOUNT_ID, PORTFOLIO_ID, fine_rules, sim_account

CASES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'golden_cases')
TFS = {'4h': 14_400_000, '1h': 3_600_000}
KINDS = {'enter_long': ('enter', 'LONG'), 'enter_short': ('enter', 'SHORT'), 'exit_long': ('close', 'LONG'),
         'exit_short': ('close', 'SHORT')}
SLOT_OK = {'id', 'sides', 'risk', 'share', 'max_pos', 'symbols', 'entry', 'stop'}
EPS = 1e-9
LEGACY_DAILY_HALT = Decimal('0.08')


class NotExpressible(Exception):
    pass


def load(case_id, folder=CASES):
    with open(os.path.join(folder, case_id + '.json'), encoding='utf-8') as fh:
        return json.load(fh)


def market(case, tf_ms):
    t0 = int(case['clock']['start'])
    out = {}
    for sym, spec in case['market'].items():
        b = spec['base']
        if b['kind'] != 'flat':
            raise NotExpressible(f"market base {b['kind']}")
        n, px, w = int(b['n']), Decimal(b['px']), Decimal(b['wick'])
        decl = {int(k): tuple(Decimal(x) for x in v) for k, v in (spec.get('bars') or {}).items()}
        bars, last = [], px
        for j in range(n):
            if j in decl:
                o, h, l, c = decl[j]
                last = c
            else:
                o = c = last
                h, l = last + w, last - w
            bars.append(Bar(open_ms=t0 + j * tf_ms, close_ms=t0 + (j + 1) * tf_ms, open=o, high=h, low=l, close=c,
                            volume=Decimal('1000')))
        out[sym] = tuple(bars)
    return out


S2_SLOTS = {'target', 'tp1', 'time_exit', 'trail'}
INTRABAR_EXITS = frozenset({ReasonCode.EXIT_TAKE_PROFIT, ReasonCode.EXIT_TP1})


def check_expressible(case, management=False):
    sl = case['slot']
    if management:                                 # M4: the S2 slots run through the ManagedRunner
        sl = {k: v for k, v in sl.items() if k not in S2_SLOTS}
        case = dict(case, slot=sl, instruments=None)              # instrument filters: case_rules()
    for f in case.get('faults') or ():
        if f['kind'] == 'lost_response' and f['order'] != 'entry':
            raise NotExpressible(f"lost_response on {f['order']} (owner: S3 runner, not built)")
        if f['kind'] not in ('lost_response', 'exchange_outage', 'restart'):
            raise NotExpressible(f"fault {f['kind']} (owner: NC-02 fault vocabulary)")
    extra = {k for k, v in sl.items() if v is not None} - SLOT_OK
    owner = {'target': 'S2 management (NC-07)', 'trail': 'S2 management (NC-07)', 'time_exit': 'S2 management (NC-07)',
             'tp1': 'S2 management (NC-07)', 'ladder': 'S2 management (NC-07)', 'runner': 'S2 management (NC-07)',
             'dca': 'range slice', 'pyramid': 'range slice'}
    if extra:
        raise NotExpressible('slot ' + ', '.join(f'{k} (owner: {owner.get(k, "unowned")})' for k in sorted(extra)))
    if case.get('instruments') is not None:
        raise NotExpressible('instruments filters in the golden adapter (owner: S2 management, with tp1 C25)')
    if (sl.get('entry') or {'type': 'market'})['type'] != 'market':
        raise NotExpressible('trailing / maker entry (owner: range slice)')
    if set(sl.get('stop') or {}) != {'atr'}:
        raise NotExpressible('slot.stop must be exactly {atr} (owner: S2 management)')
    if case['account'].get('sizing') != 'legacy_replay':
        raise NotExpressible('sizing (owner: S4 risk)')


def case_rules(case, sym):
    """The case's instrument filters (tick / step / min qty / min notional), else the never-binding fine rules."""
    from newcore.domain import Capability, InstrumentId, InstrumentRules, Venue
    ins = (case.get('instruments') or {}).get(sym)
    if ins is None:
        return fine_rules(sym)
    return InstrumentRules(instrument=InstrumentId(venue=Venue.BINANCE_USDM, symbol=sym), tick_size=Decimal(ins['tick']),
                           step_size=Decimal(ins['step']), min_qty=Decimal(ins['min_qty']), max_qty=Decimal('1000000000'),
                           min_notional=Decimal(ins['min_notional']),
                           capabilities=(Capability.HEDGE_MODE, Capability.STOP_MARKET, Capability.REDUCE_ONLY))


class GoldenPlans:
    """The golden newcore_sim plan mapping (see the module doc), from the runner's EntryInfo."""

    def __init__(self, case):
        self.case = case

    def __call__(self, info):
        from newcore.domain.instrument import Rounding
        from newcore.management import CostModel as PlanCosts, build_plan, planned_risk
        sl, co = self.case['slot'], self.case.get('costs') or {}
        costs = PlanCosts(taker_fee=Decimal(co.get('taker_fee', '0.0005')), slip=Decimal(co.get('slip', '0.0002')))
        d = info.stop_distance                                     # stop_atr x Wilder ATR14 (= stop_atr x ATR, flat)
        atr = d / Decimal(sl['stop']['atr'])
        s = 1 if info.side.value == 'LONG' else -1
        e = info.entry_price
        tp1, tgt, tex, trl = sl.get('tp1'), sl.get('target'), sl.get('time_exit'), sl.get('trail')
        kw = dict(rules=info.rules, side=info.side, entry_price=e, entry_qty=info.entry_qty,
                  entry_candle_open_ms=info.entry_candle_open_ms, candle_seconds=info.tf_ms // 1000,
                  stop_price=info.rules.quantize_price(e - s * d, Rounding.NEAREST), costs=costs,
                  tp1_frac=None if tp1 is None else Decimal(tp1['frac']),
                  tp1_offset=None if tp1 is None else Decimal(tp1['r']) * d,
                  tp2_offset=None if tgt is None else Decimal(tgt['r']) * d, be_after_tp1=tp1 is not None,
                  time_exit_candles=None if tex is None else int(tex['bars']),
                  trail_offset=None if trl is None else Decimal(trl['atr']) * atr)
        probe = build_plan(risk_cap=Decimal('1e15'), **kw).plan
        price_risk = probe.entry_qty * abs(probe.entry_price - probe.stop_price)
        return build_plan(risk_cap=price_risk + (planned_risk(probe) - price_risk), **kw).plan


def run_case(case, management=False):
    check_expressible(case, management)
    tf = TFS[case['tf']]
    candles = market(case, tf)
    syms = tuple(case['slot'].get('symbols') or candles)
    bars = candles[syms[0]]
    t0 = bars[0].open_ms
    book = len(syms) > 1 or int(case['slot'].get('max_pos', 1)) > 1
    sig = {}
    for g in case['signals']:
        sig.setdefault((g['sym'], t0 + (int(g['bar']) + 1) * tf), []).append(KINDS[g['kind']])
    sl, acct, costs = case['slot'], case['account'], case.get('costs') or {}
    sides = {'long': ('LONG',), 'short': ('SHORT',), 'both': ('LONG', 'SHORT')}[sl['sides']]
    venue = FakeVenue(candles, tf, equity=Decimal(acct['equity']),
                      costs=CostModel(taker_fee=Decimal(costs.get('taker_fee', '0.0005')),
                                      slip=Decimal(costs.get('slip', '0.0002')),
                                      funding_per_bar=Decimal(costs.get('funding_per_bar', '0'))))
    lost = {int(f['nth']): f['truth'] for f in case.get('faults') or () if f['kind'] == 'lost_response'}
    if lost:                                       # the nth ENTRY market order reaches the venue, its answer is lost
        submit, seen = venue.submit_market, [0]

        def submit_market(order):
            if not order.reduce:
                seen[0] += 1
                if seen[0] in lost:
                    venue.lose_next_market_answer(lost[seen[0]])
            return submit(order)
        venue.submit_market = submit_market
    risk_pct = Decimal(sl['risk']) * Decimal(sl.get('share', '1'))
    max_lev = Decimal(str(acct.get('max_leverage', 10)))
    cfg = RunnerConfig(account=sim_account(t0 - 1), portfolio_id=PORTFOLIO_ID, symbols=syms, tf_ms=tf,
                       timeframe=case['tf'], rules={s: case_rules(case, s) for s in syms},
                       sizing=SizingPolicy(risk_pct=risk_pct, max_leverage=max_lev), sides=sides)
    signals = InjectedSignals({k: tuple(v) for k, v in sig.items()}, stop_atr=Decimal(sl['stop']['atr']),
                              tf_label=case['tf'])
    kw = dict(journal=MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID), venue=venue, bars=CsvBarSource(candles, tf),
              signals=signals)
    if management:
        kw['management'] = ManagementConfig(enabled=True, plans=GoldenPlans(case))
    if book:                       # S4: one book, max_pos, the legacy 8% Cairo-day halt (backtest.run default), no kill
        runner = (ManagedBookRunner if management else BookRunner)(
            cfg, policy=BookPolicy(risk_pct=risk_pct, max_positions=int(sl['max_pos']), max_leverage=max_lev,
                                   daily_loss_pct=LEGACY_DAILY_HALT, kill_drawdown_pct=None,
                                   cap_gap_buffer=Decimal(0)), **kw)
    else:
        runner = (ManagedRunner if management else Runner)(cfg, **kw)
    outage = [(f['from_ms'], f['to_ms']) for f in case.get('faults') or () if f['kind'] == 'exchange_outage']
    restarts = [(f['at_ms'], f['at_ms'] + f['down_ms']) for f in case.get('faults') or () if f['kind'] == 'restart']
    if outage:
        runner.venue = runner.reads = Outage(venue, outage)
    runner = drive(runner, venue, t0, bars[-1].close_ms, tf, restarts)
    trades = []
    for t in runner.trades():
        i_in = (t.entry_ms - t0) // tf
        if t.exit_reason is ReasonCode.EXIT_SIGNAL:              # golden: a signal exit is booked on its decision bar
            i_out = (t.exit_signal_close_ms - t0) // tf - 1
        elif management and t.exit_reason in INTRABAR_EXITS:      # a target triggered at its level in the candle
            i_out = (t.exit_ms - t0) // tf
        elif management and t.exit_reason is not ReasonCode.EXIT_STOP:   # a candle-close decision: its bar
            i_out = (t.exit_ms - t0) // tf - 1
        else:
            i_out = (t.exit_ms - t0) // tf
        trades.append(dict(sym=t.symbol, side=t.side, i_in=i_in, i_out=i_out, exit=t.exit_code, R=float(t.r),
                           pnl=float(t.pnl)))
    trades.sort(key=lambda x: (x['i_in'], x['sym']))
    return dict(trades=trades, final=dict(lots=len(runner.fold.open_lots()))), runner


class Outage:
    """exchange_outage fault: while from <= venue time < to, the bot can neither read nor send (every answer UNKNOWN);
    orders resting ON the exchange keep working (the FakeVenue walks them as usual)."""

    def __init__(self, venue, windows):
        self.inner, self.windows = venue, windows

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _down(self):
        return any(a <= self.inner.now_ms < b for a, b in self.windows)

    def _order(self, name, ref, *args):
        if self._down():
            from newcore.ports.venue import OrderOutcome, OutcomeKind
            return OrderOutcome(kind=OutcomeKind.UNKNOWN, ref=ref, observed_at_ms=self.inner.now_ms, detail='outage')
        return getattr(self.inner, name)(*args)

    def _read(self, name, *args, **kw):
        if self._down():
            from newcore.ports.venue import ReadKind, ReadOutcome
            return ReadOutcome(kind=ReadKind.UNKNOWN, observed_at_ms=self.inner.now_ms, detail='outage')
        return getattr(self.inner, name)(*args, **kw)

    def submit_market(self, order):
        return self._order('submit_market', order.ref, order)

    def submit_stop(self, order):
        return self._order('submit_stop', order.ref, order)

    def cancel(self, ref):
        return self._order('cancel', ref, ref)

    def query(self, ref):
        return self._order('query', ref, ref)

    def positions(self, symbol=None):
        return self._read('positions', symbol)

    def open_orders(self, symbol=None):
        return self._read('open_orders', symbol)

    def equity(self):
        return self._read('equity')


def drive(runner, venue, start, end, tf, restarts):
    """The replay clock with restart faults: no cycle while the process is down; it comes back as a NEW Runner over
    the same journal (the fold is the only state that survives)."""
    t = start + tf
    was_down = False
    while t <= end:
        down = any(a <= t < b for a, b in restarts)
        if hasattr(runner, 'mgmt') and runner.mgmt.enabled and not down and not was_down:
            play_candle(venue, runner, t)                         # M4: intra-candle marks along zb-path/1
        else:
            venue.advance_to(t)
        if not down:
            if was_down:
                runner = type(runner)(runner.cfg, journal=runner.journal, venue=runner.venue, bars=runner.bars,
                                      signals=runner.signals, account_reads=runner.reads,
                                      **({'policy': runner.policy} if hasattr(runner, 'policy') else {}),
                                      **({'management': runner.mgmt} if hasattr(runner, 'mgmt') else {}))
            runner.cycle(t, decide=t < end)
        was_down = down
        t += tf
    return runner


def diff(case, trace):
    """Golden compare (goldenlib/compare.py): codes, bars and sides exactly; R / pnl to EPS. Empty = equal."""
    exp = case['expect']['trades']
    out = []
    if len(exp) != len(trace['trades']):
        return [('trades', len(exp), len(trace['trades']))]
    for k, (e, a) in enumerate(zip(exp, trace['trades'])):
        for f in ('sym', 'side', 'i_in', 'i_out', 'exit'):
            if e[f] != a[f]:
                out.append((f'trades[{k}].{f}', e[f], a[f]))
        for f in ('R', 'pnl'):
            if f not in e:                                       # a case may leave a value unasserted
                continue
            if abs(float(e[f]) - a[f]) > EPS:
                out.append((f'trades[{k}].{f}', e[f], a[f]))
    fin = case['expect'].get('final') or {}
    if 'lots' in fin and fin['lots'] != trace['final']['lots']:
        out.append(('final.lots', fin['lots'], trace['final']['lots']))
    return out
