"""NC-08 golden adapter for the NEWCORE management core: case -> ManagementPlan -> ManagementDriver + step(), driven by a
deterministic path venue that walks every candle along zb-path/1 (the pack's path policy).

Scope (v1): one position per case, market entry, flat market base (ATR = 2 x wick exactly), `stop`, `target`, `tp1`,
`time_exit` and `dca` (the core places ONE add: a dca ladder of n levels maps to its FIRST level only, scale and spacing
unchanged, with the basket stop and the legacy_replay sizing of the whole ladder - see NC07-ONE-ADD-CAP), strategy exit
signals (a close request at that candle's close). Anything else raises NotExpressible.

Mapping:
- entry: the candle after the signal, at its open adverse-adjusted by the slippage, taker fee on the notional;
- sizing (account.sizing legacy_replay): risk_usd = equity x slot.risk x share; plain stop: q0 = risk_usd / (stop_atr x
  ATR); dca: q0 = risk_usd / sum_k(scale^k x dist_k), dist_k = ((n - k) x step_atr + stop_atr) x ATR (the legacy ladder);
  quantities floor at the case's instrument step (default 1e-12), prices on its tick (default 1e-12);
- levels: plain stop e -/+ stop_atr x ATR; dca basket stop e -/+ (n x step_atr + stop_atr) x ATR, the add at
  e -/+ step_atr x ATR (scale x q0), basket target avg +/- tp_atr x ATR; tp1 / target in R of the entry stop distance;
- risk_cap (OPEN for a Codex ruling): CAP_POLICY 'budget_plus_costs' (default) = risk_usd + the plan's reserved costs
  (planned_risk - the pure price risk to the stop), so the legacy "1R = price risk" budget admits its own sizing;
  'budget' = risk_usd exactly (the NEWCORE cap is cost-inclusive, so a legacy-sized plan is refused / loses its add);
- output: one canonical row per position: i_in / i_out = entry / flat candle, exit code from the final exit leg,
  pnl = realized_pnl (fees included, no funding), R = pnl / risk_usd.
"""
from decimal import Decimal as D

from newcore.domain import Capability, InstrumentId, InstrumentRules, ReasonCode, Side, Venue, make_id
from newcore.domain.instrument import Rounding
from newcore.management import CostModel, Leg, PlanRefused, Stage, build_plan, planned_risk, realized_pnl
from newcore.management import driver as DR
from newcore.management.plan import market_fill
from newcore.management.sim import path_points
from newcore.management.state import Candle
from newcore.ports.venue import OrderOutcome, OrderRef, OutcomeKind, VenueFill

from ..schema import TFS, clock_ms
from .base import NotExpressible, Trace

CAP_POLICY = 'budget_plus_costs'
# Not registered in adapters.CAPS / adapters.get yet: test_golden pins CAPS to the legacy adapters until a case binds
# newcore_sim as required (Codex call). Callers that compare a trace add this entry for the duration of the comparison.
CAPS_ENTRY = dict(final=frozenset({'lots'}), funding=False)
POLICIES = ('budget_plus_costs', 'budget')
ACCT = make_id('acct', 1)
LOT = make_id('lot', 1)
EXIT_CODE = {ReasonCode.EXIT_TIME: 'TIME_EXIT', ReasonCode.EXIT_SIGNAL: 'SIGNAL_EXIT',
             ReasonCode.EXIT_STOP_CROSSED: 'STOP_CROSSED', ReasonCode.EXIT_FLATTEN: 'FLATTEN',
             ReasonCode.EXIT_STOP_FAILED: 'STOP_FAILED'}
SUPPORTED_SLOT = {'id', 'sides', 'risk', 'share', 'max_pos', 'symbols', 'entry', 'stop', 'target', 'tp1', 'dca',
                  'time_exit'}


class NewcoreSim:
    def __init__(self, cap_policy=None):
        self.cap_policy = cap_policy or CAP_POLICY
        assert self.cap_policy in POLICIES, self.cap_policy

    def run(self, case):
        spec = _spec(case)
        plan, notes, risk_usd = _plan(case, spec, self.cap_policy)
        v = _PathVenue(plan, spec)
        v.run()
        st = v.ds.pos
        trades = []
        if st.stage is Stage.DONE:
            pnl = realized_pnl(plan, st)
            trades.append(dict(sym=spec['sym'], side=plan.side.value, i_in=spec['i_in'], i_out=v.i_out,
                               exit=v.exit_code(), R=float(pnl / risk_usd), pnl=float(pnl)))
        return Trace(trades=trades, final={'lots': 0 if st.stage is Stage.DONE else 1},
                     raw=dict(plan_notes=[n.value for n in notes], cap=str(plan.risk_cap),
                              planned_risk=str(planned_risk(plan)), add_qty=str(plan.add_qty)))


# ------------------------------------------------------------------------------------------------------ the case
def _spec(case):
    if case.get('faults'):
        raise NotExpressible('faults: the NEWCORE golden adapter v1 has no fault injection')
    sl = case['slot']
    extra = set(sl) - SUPPORTED_SLOT
    if extra:
        raise NotExpressible(f'slot keys {sorted(extra)} are not expressible in the NEWCORE management core v1')
    if (sl.get('entry') or {'type': 'market'})['type'] != 'market':
        raise NotExpressible('only market entries')
    if len(case['market']) != 1:
        raise NotExpressible('one symbol per case')
    sym, mk = next(iter(case['market'].items()))
    entries = [g for g in case['signals'] if g['kind'] in ('enter_long', 'enter_short')]
    if len(entries) != 1:
        raise NotExpressible('exactly one entry signal')
    sig = entries[0]
    side = Side.LONG if sig['kind'] == 'enter_long' else Side.SHORT
    k = int(sig['bar'])
    bars = {int(i): tuple(D(x) for x in row) for i, row in (mk.get('bars') or {}).items()}
    if any(i <= k for i in bars):
        raise NotExpressible('a declared bar at / before the signal: the ATR is no longer exactly 2 x wick')
    b = mk['base']
    exits = {int(g['bar']): ReasonCode.EXIT_SIGNAL for g in case['signals']
             if g['kind'] == ('exit_long' if side is Side.LONG else 'exit_short')}
    return dict(sym=sym, side=side, i_sig=k, i_in=k + 1, n=int(b['n']), px=D(b['px']), wick=D(b['wick']), bars=bars,
                dca='dca' in sl,
                exits=exits, tf_ms=TFS[case['tf']] * 1000, t0=clock_ms(case))


def _candles(spec):
    out, last = {}, None
    w = spec['wick']
    for j in range(spec['n']):
        if j in spec['bars']:
            o, h, l, c = spec['bars'][j]
            last = c
        else:
            p = spec['px'] if last is None else last
            o, h, l, c = p, p + w, p - w, p
        out[j] = Candle(open_ms=spec['t0'] + j * spec['tf_ms'], open=o, high=h, low=l, close=c)
    return out


def _rules(case, sym):
    ins = (case.get('instruments') or {}).get(sym)
    if ins is None:
        step = tick = D('1e-12')
        return InstrumentRules(instrument=InstrumentId(venue=Venue.BINANCE_USDM, symbol=sym), tick_size=tick,
                               step_size=step, min_qty=step, max_qty=D('1000000000'), min_notional=D(0),
                               capabilities=(Capability.STOP_MARKET, Capability.REDUCE_ONLY))
    return InstrumentRules(instrument=InstrumentId(venue=Venue.BINANCE_USDM, symbol=sym), tick_size=D(ins['tick']),
                           step_size=D(ins['step']), min_qty=D(ins['min_qty']), max_qty=D('1000000000'),
                           min_notional=D(ins['min_notional']), capabilities=(Capability.STOP_MARKET,
                                                                               Capability.REDUCE_ONLY))


def _plan(case, spec, policy):
    sl, side = case['slot'], spec['side']
    s = 1 if side is Side.LONG else -1
    co = case.get('costs') or {}
    if D(co.get('funding_per_bar', '0')) != 0:
        raise NotExpressible('funding_per_bar: funding accrual comes with the runner ledger')
    costs = CostModel(taker_fee=D(co.get('taker_fee', '0.0005')), slip=D(co.get('slip', '0.0002')))
    rules = _rules(case, spec['sym'])
    atr = 2 * spec['wick']
    o = _candles(spec)[spec['i_in']].open
    e = market_fill(o, side, costs.slip, opening=True)
    risk_usd = D(case['account']['equity']) * D(sl['risk']) * D(sl.get('share', '1'))
    add_price = add_scale = tp1_frac = tp1_off = tp2_off = None
    dca = sl.get('dca')
    if dca is not None:
        n, step, scale = int(dca['n']), D(dca['step_atr']), D(dca['scale'])
        stop_d = (n * step + D(dca['stop_atr'])) * atr
        weights = sum(scale ** k * ((n - k) * step + D(dca['stop_atr'])) * atr for k in range(n + 1))
        q0 = rules.quantize_qty(risk_usd / weights, Rounding.DOWN)
        stop = e - s * stop_d
        add_price, add_scale = e - s * step * atr, scale
        tp2_off = D(dca['tp_atr']) * atr
    else:
        if sl.get('stop') is None:
            raise NotExpressible('a stop is required')
        stop_d = D(sl['stop']['atr']) * atr
        q0 = rules.quantize_qty(risk_usd / stop_d, Rounding.DOWN)
        stop = e - s * stop_d
        if sl.get('tp1') is not None:
            tp1_frac, tp1_off = D(sl['tp1']['frac']), D(sl['tp1']['r']) * stop_d
        if sl.get('target') is not None:
            tp2_off = D(sl['target']['r']) * stop_d
    tex = sl.get('time_exit')
    kw = dict(rules=rules, side=side, entry_price=e, entry_qty=q0, entry_candle_open_ms=_candles(spec)[spec['i_in']].open_ms,
              candle_seconds=spec['tf_ms'] // 1000, stop_price=rules.quantize_price(stop, Rounding.NEAREST), costs=costs,
              add_price=None if add_price is None else rules.quantize_price(add_price, Rounding.NEAREST),
              add_scale=add_scale, tp1_frac=tp1_frac, tp1_offset=tp1_off, tp2_offset=tp2_off,
              be_after_tp1=tp1_frac is not None, time_exit_candles=None if tex is None else int(tex['bars']),
              trail_offset=None)
    probe = build_plan(risk_cap=D('1e15'), **kw).plan                      # the plan's own risk, cap not binding
    price_risk = sum((q * abs(px - probe.stop_price) for q, px in
                      ((probe.entry_qty, probe.entry_price),) + (((probe.add_qty, probe.add_price),)
                                                                if probe.add_qty is not None else ())), D(0))
    cap = risk_usd if policy == 'budget' else risk_usd + (planned_risk(probe) - price_risk)
    try:
        b = build_plan(risk_cap=cap, **kw)
    except PlanRefused as ex:
        raise NotExpressible(f'risk_cap policy {policy!r}: {ex}') from None
    return b.plan, b.notes, risk_usd


# ------------------------------------------------------------------------------------------------ the path venue
class _PathVenue:
    """Deterministic venue: stops rest here and fill when the zb-path/1 walk crosses them (a level already through at
    the current point fills at that point: the gap rule); bot-side triggers are offered to the driver at their level;
    market orders fill at once at the current price with adverse slippage and the taker fee."""

    PRIORITY = ('stop', Leg.TP1, Leg.TP2, Leg.ADD)

    def __init__(self, plan, spec):
        self.plan, self.spec, self.side = plan, spec, plan.side
        self.candles = _candles(spec)
        self.stops, self.n, self.mark, self.i_out, self.last_exit = {}, 0, plan.entry_price, None, None
        fee = plan.costs.taker_fee * plan.entry_qty * plan.entry_price
        self.ds = None
        self.at = spec['t0']
        self.handle(DR.start(plan, account_id=ACCT, lot_id=LOT, entry_fee=fee))

    def xid(self):
        self.n += 1
        return f'x{self.n}'

    def handle(self, drive):
        self.ds = drive.state
        for d in drive.submits:
            xid = self.xid()
            ref = OrderRef(symbol=self.plan.symbol, client_id=d.client_id, route=d.route)
            if d.stop_price is not None:
                self.stops[d.client_id] = (d.qty, d.stop_price, xid)
                self.handle(DR.on_outcome(self.ds, OrderOutcome(kind=OutcomeKind.KNOWN, ref=ref, observed_at_ms=self.at,
                                                                status='NEW', exchange_order_id=xid), submit=True))
            else:
                fp = market_fill(self.mark, self.side, self.plan.costs.slip, opening=d.reduce_only is False)
                if d.reduce_only:
                    self.last_exit = (d.leg, d.reason)
                self.handle(DR.on_outcome(self.ds, OrderOutcome(kind=OutcomeKind.FINAL, ref=ref, observed_at_ms=self.at,
                                                                status='FILLED', exchange_order_id=xid,
                                                                executed_qty=d.qty, avg_price=fp), submit=True))
                self.handle(DR.on_fills(self.ds, (self.fill(xid, d.qty, fp),)))
        for c in drive.cancels:
            st = self.stops.pop(c.client_id, None)
            ref = OrderRef(symbol=self.plan.symbol, client_id=c.client_id, route=c.route)
            self.handle(DR.on_outcome(self.ds, OrderOutcome(kind=OutcomeKind.FINAL, ref=ref, observed_at_ms=self.at,
                                                            status='CANCELED', exchange_order_id=st[2] if st else self.xid(),
                                                            executed_qty=D(0)), submit=False))

    def fill(self, xid, q, price):
        self.n += 1
        return VenueFill(trade_id=f't{self.n}', exchange_order_id=xid, symbol=self.plan.symbol,
                         position_side=self.side.value, qty=q, price=price, fee=self.plan.costs.taker_fee * q * price,
                         fee_asset='USDT', realized_pnl=D(0), maker=False, at_ms=self.at)

    def levels(self, blocked):
        long = self.side is Side.LONG
        out = [('stop', sp, long, cid) for cid, (q, sp, x) in self.stops.items()]
        for t in DR.armed_triggers(self.ds):
            if t.leg is Leg.ADD:
                out.append((Leg.ADD, t.price, long != self.plan.add_is_pyramid, None))
            elif not blocked:
                out.append((t.leg, t.price, not long, None))
        return out

    def hit(self, kind, level, cid, px):
        self.mark = px
        if kind == 'stop':
            q, sp, xid = self.stops.pop(cid)
            fp = market_fill(px, self.side, self.plan.costs.slip, opening=False)
            self.last_exit = (Leg.STOP, None)
            ref = OrderRef(symbol=self.plan.symbol, client_id=cid)
            self.handle(DR.on_outcome(self.ds, OrderOutcome(kind=OutcomeKind.FINAL, ref=ref, observed_at_ms=self.at,
                                                            status='FILLED', exchange_order_id=xid, executed_qty=q,
                                                            avg_price=fp), submit=False))
            self.handle(DR.on_fills(self.ds, (self.fill(xid, q, fp),)))
        else:
            self.handle(DR.on_mark(self.ds, px))

    def run(self):
        sp = self.spec
        for j in range(sp['i_in'], sp['n']):
            c = self.candles[j]
            self.at = c.open_ms
            cur = [v[1] for v in self.stops.values()]
            blocked = any(c.low <= p <= c.high for p in cur)
            pts = path_points(c, self.side)
            x, guard = pts[0], 0
            for y in pts:
                while self.ds.pos.stage is not Stage.DONE:
                    guard += 1
                    assert guard < 200, 'path venue did not settle'
                    lv = self.levels(blocked)
                    through = [(self.PRIORITY.index(k), k, p, cid) for k, p, falls, cid in lv
                               if (x <= p if falls else x >= p)]
                    if through:
                        _, k, p, cid = min(through, key=lambda t: t[0])
                        self.hit(k, p, cid, x)
                        continue
                    cand = [(abs(p - x), self.PRIORITY.index(k), k, p, cid) for k, p, falls, cid in lv
                            if (falls and y <= p < x) or (not falls and x < p <= y)]
                    if not cand:
                        break
                    _, _, k, p, cid = min(cand)
                    x = p
                    self.hit(k, p, cid, p)
                x = y
            if self.ds.pos.stage is not Stage.DONE:
                self.mark = c.close
                self.at = c.open_ms + sp['tf_ms'] - 1
                self.handle(DR.on_candle(self.ds, c, close_request=sp['exits'].get(j)))
            if self.ds.pos.stage is Stage.DONE:
                self.i_out = j
                return

    def exit_code(self):
        leg, reason = self.last_exit
        if leg is Leg.STOP:
            return 'STOP_HIT'
        if leg is Leg.TP2:
            return 'TP_BASKET' if self.spec['dca'] else 'TP_FULL'
        if leg is Leg.TP1:
            return 'TP_PARTIAL'
        return EXIT_CODE.get(reason, f'UNMAPPED:{reason}')
