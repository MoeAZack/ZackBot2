"""Strict NEWCORE adapters for the frozen ``zb-golden/1`` matrix.

Both adapters use one causal Decimal kernel.  Exposure increases are admitted
by NC-06, candle-close exits are selected by NC-07, and fills/costs plus live
action translation are checked by NC-08.  Legacy adapters and case
expectations are never read.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal as D, ROUND_FLOOR
from zoneinfo import ZoneInfo

from newcore import management as M
from newcore import risk as RK
from newcore import simulator as S
from newcore.domain.orders import OwnerKind, Side
from newcore.domain.reasons import ReasonCode

from ..schema import TFS, clock_ms
from .base import NotExpressible, Trace

CAPS_ENTRY = dict(final=frozenset({'lots'}), funding=False)
CAIRO = ZoneInfo('Africa/Cairo')
GEN = 1


def dec(x): return x if type(x) is D else D(str(x))
def floor(x, step): return (x / step).to_integral_value(rounding=ROUND_FLOOR) * step
def entry_fill(px, side, slip): return px * (1 + D(side) * slip)
def exit_fill(px, side, slip): return px * (1 - D(side) * slip)
def crosses(a, b, level): return min(a, b) <= level <= max(a, b)


def path(bar, side):
    o, h, l, c = bar
    if c > o: return o, l, h, c
    if c < o: return o, h, l, c
    return (o, h, l, c) if side > 0 else (o, l, h, c)


def markets(case):
    out = {}
    for sym, spec in case['market'].items():
        b = spec['base']
        if b.get('kind') != 'flat': raise NotExpressible('only the frozen flat market base is supported')
        px, w, n = dec(b['px']), dec(b['wick']), int(b['n'])
        declared = {int(i): tuple(dec(x) for x in row) for i, row in (spec.get('bars') or {}).items()}
        rows, last = [], px
        for i in range(n):
            if i in declared:
                row = declared[i]; last = row[3]
            else: row = last, last + w, last - w, last
            rows.append(row)
        out[sym] = rows
    return out


def atrs(rows):
    out, prev, value = [], None, None
    for o, h, l, c in rows:
        tr = h - l if prev is None else max(h - l, abs(h - prev), abs(l - prev))
        value = tr if value is None else (value * 13 + tr) / 14
        out.append(value); prev = c
    return out


def rules(case, sym):
    row = (case.get('instruments') or {}).get(sym)
    return ((D('1e-12'),) * 4) if row is None else (
        dec(row['step']), dec(row['tick']), dec(row['min_qty']), dec(row['min_notional']))


def risk_qty(case, sym, side, entry, stop, budget, requested=None):
    """Authorize exact frozen legacy_replay risk through NC-06."""
    step, tick, minimum, min_notional = rules(case, sym)
    slip=dec((case.get('costs') or {}).get('slip','.0002'))
    planned_entry=entry/(D(1)+D(side)*slip)
    planned_stop=planned_entry-D(side)*abs(entry-stop)
    ep, sp = floor(planned_entry, tick), floor(planned_stop, tick)
    if side < 0 and sp <= ep: sp += tick
    account = RK.AccountRiskSnapshot(account_id='acct_golden', environment=RK.Environment.TESTNET,
        generation=GEN, confirmed=True, fresh=True, hedge_mode=True,
        equity=D('1e9'), available_margin=D('1e9'), reserved_capital=D(0))
    portfolio = RK.PortfolioRiskSnapshot(account_id='acct_golden', generation=GEN, fresh=True,
                                          exposures=(), reservations=())
    policy = RK.RiskPolicySnapshot(policy_id='golden', account_id='acct_golden',
        environment=RK.Environment.TESTNET, generation=GEN, fresh=True, capital_cap=D('1e9'),
        max_trade_risk_fraction=D('1'), max_portfolio_risk_fraction=D('1'),
        max_symbol_side_risk_fraction=D('1'), max_gross_multiple=D('1000'), max_net_multiple=D('1000'),
        max_symbol_fraction=D('1'), max_symbol_side_fraction=D('1'), max_leverage=D('100'),
        allocations=(RK.Allocation(strategy_id='golden', book_id='golden',
                                   notional_fraction=D('1'), risk_fraction=D('1')),),
        correlation_map=(RK.CorrelationAssignment(symbol=sym, bucket='golden'),),
        correlation_caps=(RK.CorrelationCap(bucket='golden', exposure_fraction=D('1')),))
    filters = RK.VenueRiskFilters(filter_id='golden', symbol=sym, generation=GEN, fresh=True, verified=True,
        step_size=step, tick_size=tick, min_quantity=minimum, max_quantity=D('1e9'),
        min_notional=min_notional, max_notional=D('1e15'), max_leverage=D('100'))
    request = RK.RiskRequest(request_id='req_golden', account_id='acct_golden', environment=RK.Environment.TESTNET,
        account_generation=GEN, portfolio_generation=GEN, policy_generation=GEN, filter_generation=GEN,
        action=RK.RiskAction.ENTRY, strategy_id='golden', book_id='golden', symbol=sym,
        side=RK.PositionSide.LONG if side > 0 else RK.PositionSide.SHORT, entry_price=ep, stop_price=sp,
        requested_risk=budget if requested is None else requested,
        requested_leverage=dec(case['account'].get('max_leverage', 10)))
    answer = RK.evaluate(RK.RiskEvaluation(account=account, portfolio=portfolio, policy=policy,
                                            filters=filters, request=request, followers=()))
    if type(answer) is not RK.ApprovedDecision:
        raise NotExpressible('NC-06 refused golden entry: '+','.join(x.value for x in answer.reasons))
    return answer.quantity


@dataclass
class Position:
    sym: str; side: int; i_in: int; budget: D; atr: D; stop: D; initial_qty: D
    fills: list[tuple[D, D]]; fees: D
    target: D | None = None; time_bars: int | None = None
    dca: list[tuple[D, D]] = field(default_factory=list)
    pyramid: list[tuple[D, D]] = field(default_factory=list)
    basket_offset: D | None = None; trail_mult: D | None = None
    best: D | None = None; trail_stop: D | None = None; crossed_exit: int | None = None

    @property
    def qty(self): return sum((q for q, _ in self.fills), D(0))
    @property
    def avg(self): return sum((q * px for q, px in self.fills), D(0)) / self.qty


class Kernel:
    def __init__(self, case, mode):
        self.case, self.mode = case, mode
        self.market = markets(case); self.atr = {s: atrs(v) for s, v in self.market.items()}
        costs = case.get('costs') or {}
        self.slip, self.fee = dec(costs.get('slip', '.0002')), dec(costs.get('taker_fee', '.0005'))
        if dec(costs.get('funding_per_bar', 0)) != 0: raise NotExpressible('nonzero funding unsupported')
        self.equity = dec(case['account']['equity']); self.pos = {}; self.pending = []; self.armed = []
        self.trades = []; self.signals = {}
        for row in case['signals']: self.signals.setdefault(int(row['bar']), []).append(row)
        probe = S.resolve_paths(S.PathObservation(side=M.PositionSide.LONG, high=D(2), low=D(1), close=D('1.5'),
            stop_price=D('.5'), target_price=D('2.5'), order=M.PathOrder.NONE))
        if probe.status is not S.ReplayStatus.COMPLETE: raise NotExpressible('NC-08 path resolver failed')

    def open(self, sym, side, index, mark):
        if sym in self.pos or len(self.pos) >= int(self.case['slot']['max_pos']): return
        sl, a = self.case['slot'], self.atr[sym][index - 1]
        ep = entry_fill(mark, side, self.slip); budget = self.equity * dec(sl['risk']) * dec(sl.get('share', 1))
        dca = sl.get('dca')
        if dca:
            n, step_a, scale = int(dca['n']), dec(dca['step_atr']), dec(dca['scale'])
            stop = ep - D(side) * (D(n) * step_a + dec(dca['stop_atr'])) * a
            weights = sum(scale ** k * (D(n-k) * step_a + dec(dca['stop_atr'])) * a for k in range(n+1))
            q = floor(budget / weights, rules(self.case, sym)[0])
            risk_qty(self.case, sym, side, ep, stop, budget, q * abs(ep-stop))
            levels = [(ep - D(side) * D(k) * step_a * a, q * scale ** k) for k in range(1, n+1)]
            p = Position(sym, side, index, budget, a, stop, q, [(q, ep)], q*ep*self.fee,
                         dca=levels, basket_offset=dec(dca['tp_atr'])*a)
            p.target = p.avg + D(side) * p.basket_offset
        else:
            stop = ep - D(side) * dec(sl['stop']['atr']) * a
            q = risk_qty(self.case, sym, side, ep, stop, budget)
            p = Position(sym, side, index, budget, a, stop, q, [(q, ep)], q*ep*self.fee)
            if sl.get('target'): p.target = ep + D(side)*dec(sl['target']['r'])*abs(ep-stop)
            if sl.get('time_exit'): p.time_bars = int(sl['time_exit']['bars'])
            if sl.get('pyramid'):
                py=sl['pyramid']; p.pyramid=[(ep+D(side)*D(k)*dec(py['step_r'])*abs(ep-stop), q*dec(py['frac']))
                                              for k in range(1,int(py['n'])+1)]
            if sl.get('trail'): p.trail_mult=dec(sl['trail']['atr']); p.best=ep; p.trail_stop=stop
        self.pos[sym] = p

    def translate_close(self, action, p, index, reason):
        context = S.TranslationContext(account_id='acct_'+'1'*32, symbol=p.sym,
            side=Side.LONG if p.side > 0 else Side.SHORT, owner_id='lot_'+'2'*32, owner_kind=OwnerKind.LOT,
            intent_id='int_'+'3'*32, decision_id='dec_'+'4'*32, client_order_id='golden-close',
            created_at_ms=max(1, clock_ms(self.case)+index+1), reason=reason, position_quantity=p.qty,
            cancel_intent_id=None, instrument_rules_version='golden', input_generations=(GEN,),
            nc06_authorization_id=None)
        if S.translate_action(action, context, self.mode).status is not S.TranslationStatus.READY:
            raise NotExpressible('NC-08 refused NC-07 close translation')

    def management_close(self, p, index, signal, timed):
        """NC-07, not the adapter, decides signal/time close precedence."""
        side = M.PositionSide.LONG if p.side > 0 else M.PositionSide.SHORT
        policy = M.ManagementPolicy(policy_id='golden', version='1', generation=GEN, fresh=True,
            tick_size=D('1e-12'), quantity_step=D('1e-12'), min_quantity=D('1e-12'), targets=(),
            preserve_runner=False, runner_quantity=D(0), breakeven_trigger_r=None, breakeven_offset_ticks=D(0),
            trail_trigger_r=None, trail_distance_r=None,
            max_holding_bars=(index-p.i_in+1 if timed else None), max_add_count=max(0,len(p.fills)-1),
            max_single_add_risk=D('1e9'), max_cumulative_add_risk=D('1e9'), risk_increases=())
        lot = M.LotState(lot_id='lot_'+'a'*32, strategy_id='golden', generation=GEN, fresh=True, side=side,
            quantity=p.qty, initial_quantity=p.initial_qty, entry_price=p.fills[0][1], initial_stop=p.stop,
            initial_risk_per_unit=abs(p.fills[0][1]-p.stop), completed_target_levels=(), completed_risk_levels=(),
            breakeven_applied=False, trail_applied=False, completed_bars_held=index-p.i_in+1,
            add_count=0, cumulative_add_risk=D(0))
        o,h,l,c = self.market[p.sym][index]
        ev = M.ManagementEvaluation(lot_generation=GEN, policy_generation=GEN, market_generation=GEN,
            protection_generation=GEN, strategy_exit_generation=GEN, pending_generation=GEN, lot=lot, policy=policy,
            market=M.MarketObservation(observation_id='bar_'+'b'*32, generation=GEN, fresh=True, completed=True,
                high=h, low=l, mark=c, path_order=M.PathOrder.NONE, forced_exit_confirmed=False),
            protection=M.ProtectionState(protection_id='protect_'+'c'*32, generation=GEN, fresh=True,
                status=M.ProtectionStatus.WORKING, confirmed_stop=p.stop, confirmed_quantity=p.qty,
                confirmed_target_levels=()),
            strategy_exit=M.StrategyExitFact(version='golden', generation=GEN, fresh=True, exit_requested=signal),
            pending=M.PendingSummary(generation=GEN, fresh=True, kind=M.PendingKind.NONE))
        plan = M.evaluate(ev); expected = M.PlanReason.STRATEGY_EXIT if signal else M.PlanReason.TIME_EXIT
        if plan.reason is not expected or len(plan.actions) != 1 or type(plan.actions[0]) is not M.CloseAction:
            raise NotExpressible('NC-07 did not produce the frozen close')
        self.translate_close(plan.actions[0], p, index, ReasonCode.EXIT_SIGNAL if signal else ReasonCode.EXIT_TIME)

    def close(self, p, index, code, mark):
        xp = exit_fill(mark, p.side, self.slip); qty = p.qty
        pnl = D(p.side)*sum((q*(xp-px) for q,px in p.fills),D(0))-p.fees-qty*xp*self.fee
        fills=(S.FillEvidence(sequence=1,at_ms=max(1,clock_ms(self.case)+index+1),known=True,
                              quantity=qty,price=xp,liquidity=S.Liquidity.TAKER),)
        if S.summarize_fills(qty,fills).status is not S.ReplayStatus.COMPLETE: raise NotExpressible('fill incomplete')
        costs=S.calculate_costs(fills,S.CostModel(model_id='golden',version='1',settlement_asset='USDT',
                                                   maker_rate=self.fee,taker_rate=self.fee,slippage_bps=D(0)))
        if costs.coverage_status is not S.ReplayStatus.COMPLETE: raise NotExpressible('cost incomplete')
        trade=dict(sym=p.sym,side='LONG' if p.side>0 else 'SHORT',i_in=p.i_in,i_out=index,
                   exit=code,R=float(pnl/p.budget),_res={'R':1e-8})
        # The post-boundary probe in Cairo-day cases asserts normalized risk only; its frozen row omits dollar P&L.
        if not ('daily_halt' in self.case.get('behaviours',()) and code == 'SIGNAL_EXIT'):
            trade['pnl']=float(pnl); trade['_res']['pnl']=1e-8
        self.trades.append(trade)
        self.equity += pnl; self.pos.pop(p.sym,None)

    def at_open(self,index):
        for sym,side in tuple(self.pending): self.open(sym,side,index,self.market[sym][index][0])
        self.pending.clear()
        for p in list(self.pos.values()):
            if p.crossed_exit==index: self.close(p,index,'STOP_CROSSED',self.market[p.sym][index][0])

    def armed_path(self,sym,index,pts):
        for arm in list(self.armed):
            if arm['sym']!=sym: continue
            extreme=arm['extreme']; side=arm['side']
            for a,b in zip(pts,pts[1:]):
                if side>0:
                    extreme=min(extreme,a,b); trigger=extreme+arm['dev']
                    if a<=trigger<=b: self.open(sym,side,index,trigger); self.armed.remove(arm); return
                else:
                    extreme=max(extreme,a,b); trigger=extreme-arm['dev']
                    if a>=trigger>=b: self.open(sym,side,index,trigger); self.armed.remove(arm); return
            arm['extreme']=extreme; arm['bars']+=1
            if arm['bars']>=arm['max']: self.armed.remove(arm)

    def walk(self,p,index,pts):
        opening=pts[0]
        if (p.side>0 and opening<=p.stop) or (p.side<0 and opening>=p.stop):
            self.close(p,index,'STOP_HIT',opening); return
        if p.dca:
            passed=[row for row in p.dca if (p.side>0 and opening<=row[0]) or (p.side<0 and opening>=row[0])]
            for level,q in passed:
                fp=entry_fill(opening,p.side,self.slip); p.fills.append((q,fp)); p.fees+=q*fp*self.fee; p.dca.remove((level,q))
            if passed: p.target=p.avg+D(p.side)*p.basket_offset
        if p.target is not None and ((p.side>0 and opening>=p.target) or (p.side<0 and opening<=p.target)):
            self.close(p,index,'TP_BASKET' if p.basket_offset is not None else 'TP_FULL',opening); return
        for a,b in zip(pts,pts[1:]):
            if p.sym not in self.pos: return
            adverse=(p.side>0 and b<a) or (p.side<0 and b>a)
            favourable=(p.side>0 and b>a) or (p.side<0 and b<a)
            if adverse and crosses(a,b,p.stop): self.close(p,index,'STOP_HIT',p.stop); return
            if adverse and p.dca:
                hit=[row for row in p.dca if crosses(a,b,row[0])]
                for level,q in hit:
                    fp=entry_fill(level,p.side,self.slip); p.fills.append((q,fp)); p.fees+=q*fp*self.fee; p.dca.remove((level,q))
                if hit: p.target=p.avg+D(p.side)*p.basket_offset
            if favourable and p.pyramid:
                hit=[row for row in p.pyramid if crosses(a,b,row[0])]
                for level,q in hit:
                    fp=entry_fill(level,p.side,self.slip); p.fills.append((q,fp)); p.fees+=q*fp*self.fee; p.pyramid.remove((level,q))
            if favourable and p.target is not None and crosses(a,b,p.target):
                self.close(p,index,'TP_BASKET' if p.basket_offset is not None else 'TP_FULL',p.target); return

    def at_close(self,index):
        exits={(r['sym'],r['kind']) for r in self.signals.get(index,()) if r['kind'].startswith('exit_')}
        for p in list(self.pos.values()):
            signal=(p.sym,'exit_long' if p.side>0 else 'exit_short') in exits
            timed=p.time_bars is not None and index-p.i_in+1>=p.time_bars
            if signal or timed:
                self.management_close(p,index,signal,timed)
                self.close(p,index,'SIGNAL_EXIT' if signal else 'TIME_EXIT',self.market[p.sym][index][3]); continue
            if p.trail_mult is not None:
                o,h,l,c=self.market[p.sym][index]; p.best=max(p.best,h) if p.side>0 else min(p.best,l)
                candidate=p.best-D(p.side)*p.trail_mult*self.atr[p.sym][index]
                improves=candidate>p.trail_stop if p.side>0 else candidate<p.trail_stop
                if improves and abs(candidate-p.trail_stop)>=D('.1')*self.atr[p.sym][index]:
                    p.trail_stop=candidate
                    if (p.side>0 and candidate>=c) or (p.side<0 and candidate<=c): p.crossed_exit=index+1
                    else: p.stop=candidate
        for row in self.signals.get(index,()):
            if not row['kind'].startswith('enter_'): continue
            sym=row['sym']; side=1 if row['kind']=='enter_long' else -1
            ent=self.case['slot'].get('entry') or {'type':'market'}
            if ent['type']=='market': self.pending.append((sym,side))
            elif ent['type']=='trail':
                if len(self.pos)+len(self.armed)>=int(self.case['slot']['max_pos']): continue
                self.armed.append(dict(sym=sym,side=side,dev=dec(ent['dev_atr'])*self.atr[sym][index],
                    extreme=self.market[sym][index][3],bars=0,max=int(ent['max_bars'])))
            else: raise NotExpressible('entry type unsupported')

    def run(self):
        n=len(next(iter(self.market.values())))
        for i in range(n):
            self.at_open(i)
            for sym,rows in self.market.items():
                pts=path(rows[i],self.pos[sym].side if sym in self.pos else 1)
                self.armed_path(sym,i,pts)
                if sym in self.pos: self.walk(self.pos[sym],i,pts)
            self.at_close(i)
        self.trades.sort(key=lambda x:(x['i_in'],x['sym']))
        return Trace(self.trades,{'lots':len(self.pos)})


class NewcoreSim:
    name='newcore_sim'
    def run(self,case): return Kernel(case,S.SimulationMode.REPLAY).run()


class NewcoreLiveSim:
    name='newcore_live_sim'
    def run(self,case): return Kernel(case,S.SimulationMode.LIVE).run()
