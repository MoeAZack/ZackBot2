"""Legacy engine adapter: the real engine.Engine driven by replay.run_replay against its simulated exchange.

Signals are injected by replacing strategies.signals for the duration of the run (the engine passes no symbol, so the coin
is identified by its close series at the same candle times). All module globals are restored in `finally` (C21 risk until
AUD-12a brings an injectable clock / client / signal provider).

Known v1 limits (recorded, not hidden):
- no event-snapped path yet: fills inside a candle happen at the replay's path step, so cases that compare prices put the
  level on the last step of a leg (the leg's extreme) or declare a tolerance with a reason;
- the replay's own sizing basis (CAPITAL_CAP=0: totalMarginBalance incl. open P&L) is unchanged (BT-C8a, out of scope);
- trades are read from the engine's own trade history (its journal), i.e. the R the bot reports. The journal stores pnl
  rounded to 4 dp (engine.py:2403) and risk_usd to 2 dp (engine.py:4107); every trade carries that resolution as `_res`
  (R: (1e-4 + |R| x 0.005) / risk_usd, pnl: 1e-4) - an output-precision bound, not a behaviour tolerance (0.01 R on a 10 USDT
  risk is 1000x larger).
"""
import copy
from datetime import timedelta

import numpy as np
import pandas as pd

from .. import market
from ..schema import TFS
from .base import Trace, check_costs, check_unsupported, legacy_slot, symbols, side_code

# engine journal exit_reason -> golden code (schema.EXIT_CODE_MEANING). One code per engine reason: 'stop' is the exchange stop
# fill found by reconcile (STOP_HIT), 'stop_crossed' the bot's own market close of a level price had already crossed
# (STOP_CROSSED - it was folded into STOP_HIT before AUD-08). test_vocabulary.py checks that every reason literal engine.py
# can journal is mapped here (none may surface as UNMAPPED:<reason>).
REASON = {'stop': 'STOP_HIT', 'stop_crossed': 'STOP_CROSSED', 'time_exit': 'TIME_EXIT', 'exit_signal': 'SIGNAL_EXIT',
          'take_profit': 'TP_FULL', 'basket_tp': 'TP_BASKET', 'take_profit_1': 'TP_PARTIAL', 'take_profit_ladder': 'TP_LADDER',
          'liquidated': 'LIQUIDATED', 'flatten': 'FLATTEN', 'resync': 'RESYNC', 'stop_failed': 'STOP_FAILED',
          'basket_tp_part': 'BASKET_TP_PART'}
CYCLE_REASONS = {'time_exit', 'exit_signal'}     # decided at a candle close: the replay books them in the next cycle
T0 = 260
STEPS = 8
JOURNAL_PNL_RES = 1e-4          # pnl = round(net, 4) of a gross and fees each rounded to 4 dp: |error| <= 1e-4
JOURNAL_RISK_RES = 0.005        # risk_usd = round(risk, 2): |error| <= 0.005


class LegacyEngine:
    name = 'legacy_engine'

    def run(self, case):
        import engine as E
        import strategies as S
        from replay import run_replay

        from . import CAPS
        check_costs(case, funding=CAPS[self.name]['funding'])
        check_unsupported(case)
        raw = market.build(case)
        key, mg, extras = legacy_slot(case)
        syms = symbols(case)
        sig = market.signal_arrays(case, raw)
        tf = TFS[case['tf']]
        by_t = {s: dict(zip(raw[s].t.values.astype('datetime64[ns]'), raw[s].c.values)) for s in raw}
        sl = case['slot']
        sleeve = E.sleeve('G', key, float(sl.get('share', 1)), float(sl['risk']), int(sl['max_pos']), list(syms), sides=sl['sides'],
                          mgmt=copy.deepcopy(mg), tf=case['tf'])
        sleeve.update(copy.deepcopy(extras))
        opts = (case.get('adapter_options') or {}).get('legacy_engine') or {}
        delay = int(case['clock'].get('entry_cycle_delay_s', 15))

        def ident(d):
            t = pd.to_datetime(d['t']).values.astype('datetime64[ns]')[-5:]
            c = d['c'].values[-5:]
            hit = [s for s in raw if all(by_t[s].get(tt) == cc for tt, cc in zip(t, c))]
            if len(hit) != 1:
                raise AssertionError(f'golden engine adapter: cannot identify the coin of a signals() call ({hit}); '
                                     'give every symbol a distinct price level')
            return hit[0]

        def fake_signals(k, d, ctx, params=None, mask_sides=True):
            s = ident(d)
            pos = {tt: j for j, tt in enumerate(raw[s].t.values.astype('datetime64[ns]'))}
            idx = np.array([pos[tt] for tt in pd.to_datetime(d['t']).values.astype('datetime64[ns]')])
            return {f: sig[s][f][idx].copy() for f in ('le', 'se', 'lx', 'sx')}

        real_signals, real_create = S.signals, E.Engine._create_lot
        extra_s = delay - 15

        def slow_create(self_, *a, **k):          # clock.entry_cycle_delay_s: the entry cycle runs later than the exit cycle
            f = E.now_utc
            E.now_utc = lambda: f() + timedelta(seconds=extra_s)
            try:
                return real_create(self_, *a, **k)
            finally:
                E.now_utc = f

        S.signals = fake_signals
        if extra_s:
            E.Engine._create_lot = slow_create
        try:
            r = run_replay(copy.deepcopy(raw), [sleeve], T0, steps=int(opts.get('steps', STEPS)),
                           start=float(case['account']['equity']), tf_sec=tf)
        finally:
            S.signals, E.Engine._create_lot = real_signals, real_create

        t_index = {pd.Timestamp(ts): k for k, ts in enumerate(raw[syms[0]].t)}

        def bar(ts):
            return t_index[pd.Timestamp(ts).tz_convert('UTC').tz_localize(None).floor(f'{tf}s')]

        trades = []
        for h in r['history']:
            why = h['exit_reason']
            i_out = bar(h['closed']) - (1 if why in CYCLE_REASONS else 0)
            risk, pnl = float(h['risk_usd']), float(h['pnl'])
            trades.append(dict(sym=h['symbol'], side=side_code(h['side']), i_in=bar(h['opened']), i_out=i_out,
                               exit=REASON.get(why, f'UNMAPPED:{why}'), R=pnl / risk, pnl=pnl,
                               _res=dict(R=(JOURNAL_PNL_RES + abs(pnl / risk) * JOURNAL_RISK_RES) / risk, pnl=JOURNAL_PNL_RES)))
        trades.sort(key=lambda x: (x['i_in'], x['sym']))
        m = r['metrics']
        return Trace(trades=trades, final=dict(lots=int(m['lots'])),
                     raw=dict(metrics=m, missed=[(x.get('symbol'), x.get('reason')) for x in r['missed']],
                              engine_end_equity=float(r['engine_curve'].iloc[-1]) if len(r['engine_curve']) else None))
