"""Legacy backtester adapter: backtest.Book with injected signals (no strategy code), backtest.run, `why` -> codes."""
import backtest as B

from .. import market
from ..schema import TFS
from .base import Trace, check_costs, legacy_slot, symbols, side_code

WHY = {'stop': 'STOP_HIT', 'time': 'TIME_EXIT', 'signal': 'SIGNAL_EXIT', 'tp1': 'TP_PARTIAL', 'tp_ladder': 'TP_LADDER',
       'liquidated': 'LIQUIDATED'}
T0 = 260            # first decision bar of the engine replay; the backtest starts at the next bar like replay.run_replay


class LegacyBacktest:
    name = 'legacy_backtest'

    def run(self, case):
        check_costs(case)
        raw = market.build(case)
        key, mg, extras = legacy_slot(case)
        syms = symbols(case)
        sig = market.signal_arrays(case, raw)
        bk = B.Book(raw)
        for s in bk.syms:
            bk._sig[(key, s, 'None')] = sig[s]
        sl = case['slot']
        cfg = [dict(key=key, share=float(sl.get('share', 1)), risk=float(sl['risk']), max_pos=int(sl['max_pos']), symbols=syms,
                    sides=sl['sides'], mgmt=mg, **extras)]
        tf = TFS[case['tf']]
        fpb = float((case.get('costs') or {}).get('funding_per_bar', 0))
        acct = case['account']
        tr, cv = B.run(bk, cfg, start=float(acct['equity']), max_lev=float(acct.get('max_leverage', 10)),
                       t0=raw[syms[0]].t[T0 + 1], fund_per_bar=fpb * tf / 14400)
        trades = []
        for r in tr.to_dict('records') if len(tr) else []:
            why = r['why']
            code = WHY.get(why) or (('TP_BASKET' if 'dca' in mg else 'TP_FULL') if why == 'tp' else f'UNMAPPED:{why}')
            trades.append(dict(sym=r['sym'], side=side_code(int(r['side'])), i_in=int(r['i_in']), i_out=int(r['i_out']), exit=code,
                               R=float(r['R']), pnl=float(r['pnl'])))
        trades.sort(key=lambda x: (x['i_in'], x['sym']))
        # final: the backtester exposes no open-position list at the end of the run, so it reports no `final` keys and
        # `expect.final` is compared on the engine only (compare.py compares the keys an adapter reports)
        return Trace(trades=trades, final={}, raw=dict(attrs=dict(getattr(cv, 'attrs', {}))))
