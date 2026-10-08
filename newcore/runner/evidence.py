"""M3 long-candidate evidence pack for trend_ema_mom.v1 at the canary book: NOT a promotion.

    python -m newcore.runner.evidence run --costs base|2x --kill on|off --work DIR --out part.json --trades OUT.csv
    python -m newcore.runner.evidence report --parts p1.json ... --out docs/newcore/slice/M3_long_candidate_evidence.md

The run is the S4 BookRunner on data_long core 8 (4h, 2021-12-19 -> 2026-10-04), long only, FakeVenue + NC-02a
FileJournal, the canary limits (1% risk, max 4 positions, 3x cap sized with a 10% gap buffer, 3% Cairo-day halt, 10%
drawdown kill), testnet exchange filters. Costs: base = taker 0.05% + slippage 0.02% per side + funding 0.005% of
notional per 4h bar on both sides (the research model); 2x = fee and slippage doubled (funding unchanged), the
research's "2x fee+slip" stress. `--kill off` keeps every other canary limit but disarms the drawdown kill, so the RULE
can be evaluated over the whole window (the canary as configured stops trading for good at its first 10% drawdown).
Statistics reuse the research method (newcore/strategy/research/ema_mom_after_cost.py): mean net R per closed trade,
per-trade bootstrap CI and the episode-clustered CI (trades whose holding windows overlap on any symbol are one
episode; 10,000 resamples, seed 20261008), PF, win rate, max drawdown on mark-to-market equity at every 4h close,
in-sample 2022-2024 / holdout 2025-2026 by entry time, per-year R.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

from newcore.adapters import CostModel, CsvBarSource, FakeVenue, load_rules
from newcore.risk import BookPolicy
from newcore.store import create_journal
from newcore.strategy import Params

from .book import BookRunner
from .parity import ACCT, CORE8, H4, PF, sim_account
from .replay import run_replay
from .reports import trades_csv
from .runner import RunnerConfig
from .signals import EmaMomSignals
from .sizing import SizingPolicy

COSTS = {'base': CostModel(taker_fee=Decimal('0.0005'), slip=Decimal('0.0002'), funding_per_bar=Decimal('0.00005')),
         '2x': CostModel(taker_fee=Decimal('0.0010'), slip=Decimal('0.0004'), funding_per_bar=Decimal('0.00005'))}
HOLDOUT_MS = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def cmd_run(a):
    os.makedirs(a.work, exist_ok=True)
    src = CsvBarSource.from_data_long(a.root, list(CORE8), '4h')
    series = {s: src.all_bars(s)[:a.upto] if a.upto else src.all_bars(s) for s in CORE8}
    if a.upto:
        src = CsvBarSource(series, H4)
    venue = FakeVenue(series, H4, costs=COSTS[a.costs], equity=Decimal('10000'))
    policy = BookPolicy() if a.kill == 'on' else BookPolicy(kill_drawdown_pct=None)
    cfg = RunnerConfig(account=sim_account(), portfolio_id=PF, symbols=CORE8, tf_ms=H4, timeframe='4h',
                       rules=load_rules(os.path.join(a.root, 'data', 'exchange_rules_testnet.json'), CORE8),
                       sizing=SizingPolicy(), sides=('LONG',))
    runner = BookRunner(cfg, policy=policy, journal=create_journal(os.path.join(a.work, f'{a.costs}-{a.kill}'), ACCT, PF),
                        venue=venue, bars=src, signals=EmaMomSignals(H4, '4h', params=Params()))
    curve = []

    def on_cycle(r, t, rec):
        eq = r.mtm_equity()
        if eq is not None:
            curve.append((t, str(eq)))
    first = series[CORE8[0]]
    runner = run_replay(runner, venue, start_ms=first[0].open_ms, end_ms=first[-1].close_ms, tf_ms=H4,
                        on_cycle=on_cycle)
    runner.journal.close()
    with open(a.trades, 'w', encoding='utf-8', newline='') as fh:
        fh.write(trades_csv(runner))
    trades = [dict(sym=t.symbol, side=t.side, t_in=t.entry_ms,
                   t_end=t.exit_ms + (H4 if t.exit_code == 'STOP_HIT' else 0), R=str(t.r), pnl=str(t.pnl),
                   fees=str(t.fees), funding=str(t.funding), exit=t.exit_code) for t in runner.trades()]
    s = runner.summary()
    return dict(costs=a.costs, kill=a.kill, trades=trades, curve=curve, open_lots=s.open_lots, mode=s.mode,
                events=[(e.kind, e.day, str(e.change)) for e in runner.events if e.kind != 'roll'],
                skips=s.counters['skips'], unprotected=s.counters['unprotected_cycles'], equity_end=str(s.equity_end),
                trades_csv=os.path.basename(a.trades))


def _stats(part):
    sys.path.insert(0, os.getcwd())
    from newcore.strategy.research import ema_mom_after_cost as RS
    tr = [dict(t, R=float(t['R']), pnl=float(t['pnl'])) for t in part['trades']]
    curve = [(t, float(v)) for t, v in part['curve']]
    full = RS.summarize(tr) if tr else None
    is_ = RS.summarize([t for t in tr if t['t_in'] < HOLDOUT_MS]) if tr else None
    ho = RS.summarize([t for t in tr if t['t_in'] >= HOLDOUT_MS]) if any(t['t_in'] >= HOLDOUT_MS for t in tr) else None
    years = {}
    for t in tr:
        years.setdefault(RS.year(t['t_in']), []).append(t)
    return dict(full=full, is_=is_, ho=ho, dd=RS.max_dd(curve), dd_is=RS.max_dd(curve, t1=HOLDOUT_MS),
                dd_ho=RS.max_dd(curve, t0=HOLDOUT_MS), net=(curve[-1][1] / 10_000 - 1) if curve else float('nan'),
                years={y: RS.summarize(g) for y, g in sorted(years.items())}, fmt=RS.fmt)


def cmd_report(a):
    parts = [json.load(open(p, encoding='utf-8')) for p in a.parts]
    L = ['# M3: long-candidate evidence pack, `trend_ema_mom.v1` at the canary book', '',
         '**This is evidence for the promotion gate, NOT a promotion.** The candidate stays disabled '
         '(`--enable-candidate` is required to trade it). All numbers are in-sample with respect to the rule choice: '
         'the legacy research already saw this whole window, so the only untouched sample is forward data after '
         '2026-10-04. The core-8 universe is today\'s survivors (no LUNA / FTT), which flatters a long trend rule.', '',
         '**Setup.** S4 BookRunner on `data_long` core 8, 4h, 2021-12-19 -> 2026-10-04, long only, FakeVenue + NC-02a '
         'FileJournal, testnet exchange filters; the canary limits: 1% risk, max 4 positions, 3x cap (10% gap buffer), '
         '3% Cairo-day halt, 10% drawdown kill. Costs `base` = taker 0.05% + slippage 0.02% per side + funding 0.005% '
         'per 4h bar on both sides (the research model; funding is an ASSUMPTION, no point-in-time history: DATA-01); '
         '`2x` = fee + slippage doubled. Statistics: the research method (`ema_mom_after_cost.py`): mean net R per '
         'closed trade, 95% bootstrap CIs per trade and **clustered by episode** (overlapping holding windows on any '
         'symbol = one episode; 10,000 resamples, seed 20261008), PF on net $, max drawdown on mark-to-market equity at '
         'every 4h close, in-sample 2022-2024 vs holdout 2025-2026 by entry. Generated by '
         '`python -m newcore.runner.evidence`; trade lists: `docs/newcore/slice/evidence/`.', '']
    rows = []
    for p in parts:
        st = _stats(p)
        f, fmt = st['full'], st['fmt']
        label = f"{p['costs']}, kill {'ON (canary as configured)' if p['kill'] == 'on' else 'OFF (rule evaluation)'}"
        rows.append((p, st, label))
    L += ['## Summary', '',
          '| run | trades | episodes | win | mean R | trade CI | episode CI | PF | net | max DD | kill / halts | '
          'trades.csv |', '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for p, st, label in rows:
        f, fmt = st['full'], st['fmt']
        if f is None:
            L.append(f'| {label} | 0 | | | | | | | | | | |')
            continue
        kills = [e for e in p['events'] if e[0] == 'kill']
        halts = [e for e in p['events'] if e[0] == 'halt']
        L.append(f"| {label} | {f['n']} | {f['ep']} | {fmt(f['win'], pct=True)} | {fmt(f['exp'])} | "
                 f"[{fmt(f['lo'])}, {fmt(f['hi'])}] | [{fmt(f['clo'])}, {fmt(f['chi'])}] | {fmt(f['pf'], 2)} | "
                 f"{fmt(st['net'], pct=True)} | {fmt(st['dd'], pct=True)} | "
                 f"{'kill ' + kills[0][1] if kills else 'no kill'}, {len(halts)} halts | `{p['trades_csv']}` |")
    L.append('')
    for p, st, label in rows:
        f, fmt = st['full'], st['fmt']
        if f is None:
            continue
        L += [f'## {label}', '',
              f"In-sample (2022-2024): {st['is_']['n']} trades / {st['is_']['ep']} episodes, mean "
              f"{fmt(st['is_']['exp'])} R, episode CI [{fmt(st['is_']['clo'])}, {fmt(st['is_']['chi'])}], max DD "
              f"{fmt(st['dd_is'], pct=True)}.  " + (
                  f"Holdout (2025-2026): {st['ho']['n']} trades / {st['ho']['ep']} episodes, mean {fmt(st['ho']['exp'])}"
                  f" R, episode CI [{fmt(st['ho']['clo'])}, {fmt(st['ho']['chi'])}], max DD {fmt(st['dd_ho'], pct=True)}."
                  if st['ho'] else 'Holdout (2025-2026): no trades.'), '',
              '| year | trades | episodes | mean R | episode CI | sum R | net $ |', '|---|---|---|---|---|---|---|']
        for y, s in st['years'].items():
            L.append(f"| {y} | {s['n']} | {s['ep']} | {fmt(s['exp'])} | [{fmt(s['clo'])}, {fmt(s['chi'])}] | "
                     f"{s['exp'] * s['n']:+.2f} | {s['net']:+,.0f} |")
        L += ['', f"Risk events: {p['events'] or 'none'}; entries refused (positions / halt / kill / size): {p['skips']};"
                  f" final mode {p['mode']}; unprotected cycles {p['unprotected']}.", '']
    L += ['## Reading (honest)', '',
          '- Where an episode-clustered CI includes 0, the expectancy is **not distinguishable from zero** at 95% '
          'once simultaneous trades are counted as the dependent samples they are; the per-trade CI overstates the '
          'evidence.',
          '- The canary as configured trades only until its first 10% drawdown from the peak, then holds for good '
          '(no auto-resume, by design): its row is the behaviour the canary would have shown, not an estimate of the '
          'rule.',
          '- The kill-off rows evaluate the rule under every other canary limit; they are the input for the gate '
          'discussion, together with the cost stress and the holdout split.', '']
    with open(a.out, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(L) + '\n')


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m newcore.runner.evidence')
    p.add_argument('command', choices=('run', 'report'))
    p.add_argument('--root', default='.')
    p.add_argument('--costs', default='base', choices=tuple(COSTS))
    p.add_argument('--kill', default='on', choices=('on', 'off'))
    p.add_argument('--work', default='')
    p.add_argument('--trades', default='')
    p.add_argument('--out', required=True)
    p.add_argument('--parts', nargs='*', default=())
    p.add_argument('--upto', type=int, default=0, help='truncate the series (smoke runs only)')
    a = p.parse_args(argv)
    if a.command == 'report':
        cmd_report(a)
        return 0
    res = cmd_run(a)
    with open(a.out, 'w', encoding='utf-8') as fh:
        json.dump(res, fh, sort_keys=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
