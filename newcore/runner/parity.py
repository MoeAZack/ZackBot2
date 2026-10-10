"""M3 short mechanical parity: the long rule on data_long vs the mirrored short rule on the mirrored series (mirror.py),
through the full Runner (FakeVenue + NC-02a FileJournal), single-symbol and as the S4 book.

    python -m newcore.runner.parity single --symbols BTCUSDT,ETHUSDT --costs zero --work DIR --out part.json
    python -m newcore.runner.parity book   --costs zero --work DIR --out book.json
    python -m newcore.runner.parity report --parts a.json b.json ... --out docs/newcore/slice/M3_short_parity.md

Pairing: by symbol and entry candle. Asserted per pair: entry candle, exit candle, exit code, quantity, R (exact
Decimal equality at zero costs). The leverage cap is lifted (1000x) in the parity runs because a notional cap depends
on the price LEVEL, which the mirror changes; so do proportional costs, which the `base` rows measure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal

from newcore.adapters import CostModel, CsvBarSource, FakeVenue, load_rules
from newcore.domain import (Account, AccountBinding, BindingConfirmation, BindingState, Environment, Venue,
                            confirmation_phrase)
from newcore.risk import BookPolicy
from newcore.store import create_journal
from newcore.strategy import Params

from .book import BookRunner
from .mirror import mirror_bars
from .replay import run_replay
from .redact import describe
from .runner import Runner, RunnerConfig
from .signals import EmaMomSignals
from .sizing import SizingPolicy

H4 = 14_400_000
CORE8 = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT')
ZERO = Decimal(0)
COSTS = {'zero': CostModel(taker_fee=ZERO, slip=ZERO, funding_per_bar=ZERO),
         'base': CostModel(taker_fee=Decimal('0.0005'), slip=Decimal('0.0002'), funding_per_bar=Decimal('0.00005'))}
NO_CAP = Decimal('1000')
ACCT, PF, DIGEST = 'acct_' + 'a' * 32, 'pf_' + 'b' * 32, '0123456789abcdef'


def sim_account():
    b = AccountBinding(venue=Venue.BINANCE_USDM, environment=Environment.SIM, settlement_asset='USDT',
                       key_digest=DIGEST, exchange_uid=None)
    c = BindingConfirmation(account_id=ACCT, old_key_digest=None, new_key_digest=DIGEST,
                            typed_phrase=confirmation_phrase(ACCT, DIGEST), confirmed_at_ms=946_684_800_000)
    return Account(account_id=ACCT, label='m3-parity', hedge_mode=True, binding=b, binding_state=BindingState.CONFIRMED,
                   proposed_binding=None, confirmation=c)


def candles(root, symbols):
    src = CsvBarSource.from_data_long(root, list(symbols), '4h')
    orig = {s: src.all_bars(s) for s in symbols}
    return orig, {s: mirror_bars(b) for s, b in orig.items()}


def run_single(root, sym, series, side, costs, journal_dir):
    venue = FakeVenue({sym: series}, H4, costs=COSTS[costs], equity=Decimal('10000'))
    cfg = RunnerConfig(account=sim_account(), portfolio_id=PF, symbols=(sym,), tf_ms=H4, timeframe='4h',
                       rules=load_rules(os.path.join(root, 'data', 'exchange_rules_testnet.json'), [sym]),
                       sizing=SizingPolicy(Decimal('0.01'), NO_CAP), sides=(side,))
    runner = Runner(cfg, journal=create_journal(journal_dir, ACCT, PF), venue=venue,
                    bars=CsvBarSource({sym: series}, H4),
                    signals=EmaMomSignals(H4, '4h', params=Params(enable_short=side == 'SHORT')))
    runner = run_replay(runner, venue, start_ms=series[0].open_ms, end_ms=series[-1].close_ms, tf_ms=H4)
    runner.journal.close()
    return runner


def run_book(root, series_by_sym, side, costs, journal_dir, policy):
    syms = tuple(series_by_sym)
    venue = FakeVenue(series_by_sym, H4, costs=COSTS[costs], equity=Decimal('10000'))
    cfg = RunnerConfig(account=sim_account(), portfolio_id=PF, symbols=syms, tf_ms=H4, timeframe='4h',
                       rules=load_rules(os.path.join(root, 'data', 'exchange_rules_testnet.json'), syms),
                       sizing=SizingPolicy(), sides=(side,))
    runner = BookRunner(cfg, policy=policy, journal=create_journal(journal_dir, ACCT, PF), venue=venue,
                        bars=CsvBarSource(series_by_sym, H4),
                        signals=EmaMomSignals(H4, '4h', params=Params(enable_short=side == 'SHORT')))
    first = series_by_sym[syms[0]]
    runner = run_replay(runner, venue, start_ms=first[0].open_ms, end_ms=first[-1].close_ms, tf_ms=H4)
    runner.journal.close()
    return runner


def trade_rows(runner, t0):
    out = []
    for t in sorted(runner.trades(), key=lambda t: (t.entry_ms, t.symbol)):
        out.append(dict(sym=t.symbol, side=t.side, i_in=(t.entry_ms - t0) // H4, i_out=(t.exit_ms - t0) // H4,
                        exit=t.exit_code, qty=str(t.qty), r=str(t.r), pnl=str(t.pnl)))
    return out


def pair(longs, shorts):
    L = {(r['sym'], r['i_in']): r for r in longs}
    S = {(r['sym'], r['i_in']): r for r in shorts}
    same, same_cc, diffs, dr = 0, 0, [], []
    for k in sorted(set(L) & set(S), key=lambda k: (k[1], k[0])):
        a, b = L[k], S[k]
        d = Decimal(a['r']) - Decimal(b['r'])
        dr.append(abs(d))
        same_cc += (a['i_out'], a['exit']) == (b['i_out'], b['exit'])
        if (a['i_out'], a['exit'], a['qty']) == (b['i_out'], b['exit'], b['qty']):
            same += 1
        elif (a['i_out'], a['exit']) != (b['i_out'], b['exit']):
            diffs.append(dict(long=a, short=b))
    return dict(longs=len(L), shorts=len(S), paired=len(set(L) & set(S)), same_exit_qty=same, same_candles_code=same_cc,
                median_abs_dr=str(sorted(dr)[len(dr) // 2]) if dr else '0',
                only_long=sorted(set(L) - set(S)), only_short=sorted(set(S) - set(L)), diffs=diffs[:20],
                max_abs_dr=str(max(dr, default=ZERO)), r_equal=sum(1 for x in dr if x == 0),
                sum_r_long=str(sum((Decimal(r['r']) for r in longs), ZERO)),
                sum_r_short=str(sum((Decimal(r['r']) for r in shorts), ZERO)))


def cmd_single(a):
    orig, mirr = candles(a.root, a.symbols)
    out = {}
    for s in a.symbols:
        t0 = orig[s][0].open_ms
        lr = run_single(a.root, s, orig[s], 'LONG', a.costs, os.path.join(a.work, f'{s}-{a.costs}-long'))
        sr = run_single(a.root, s, mirr[s], 'SHORT', a.costs, os.path.join(a.work, f'{s}-{a.costs}-short'))
        res = pair(trade_rows(lr, t0), trade_rows(sr, t0))
        res.update(unprotected=(lr.counters.unprotected_cycles, sr.counters.unprotected_cycles))
        out[s] = res
        print(s, a.costs, {k: res[k] for k in ('longs', 'shorts', 'same_exit_qty', 'r_equal', 'max_abs_dr')},
              flush=True)
    return {'kind': 'single', 'costs': a.costs, 'symbols': out}


def cmd_book(a):
    orig, mirr = candles(a.root, CORE8)
    policy = BookPolicy(max_leverage=NO_CAP,                              # the canary, minus the level-dependent cap
                        kill_drawdown_pct=BookPolicy().kill_drawdown_pct if a.kill == 'on' else None)
    t0 = orig[CORE8[0]][0].open_ms
    lr = run_book(a.root, orig, 'LONG', a.costs, os.path.join(a.work, f'book-{a.costs}-{a.kill}-long'), policy)
    sr = run_book(a.root, mirr, 'SHORT', a.costs, os.path.join(a.work, f'book-{a.costs}-{a.kill}-short'), policy)
    ev = lambda r: [(e.kind, e.day, str(e.change)) for e in r.events if e.kind != 'roll']
    res = pair(trade_rows(lr, t0), trade_rows(sr, t0))
    res.update(events_long=ev(lr), events_short=ev(sr), events_equal=ev(lr) == ev(sr),
               skips=(lr.counters.skips, sr.counters.skips), modes=(str(lr.fold.mode), str(sr.fold.mode)),
               unprotected=(lr.counters.unprotected_cycles, sr.counters.unprotected_cycles))
    print('book', a.costs, {k: res[k] for k in ('longs', 'shorts', 'same_exit_qty', 'r_equal', 'events_equal')})
    return {'kind': 'book', 'costs': a.costs, 'kill': a.kill, 'book': res}


def cmd_report(a):
    raw = [json.load(open(p, encoding='utf-8')) for p in a.parts]
    parts, singles = [], {}
    for p in raw:                                             # one table per cost model (parts are symbol halves)
        if p['kind'] == 'single':
            if p['costs'] not in singles:
                singles[p['costs']] = {'kind': 'single', 'costs': p['costs'], 'symbols': {}}
                parts.append(singles[p['costs']])
            singles[p['costs']]['symbols'].update(p['symbols'])
        else:
            parts.append(p)
    for p in parts:
        if p['kind'] == 'single':
            p['symbols'] = {s: p['symbols'][s] for s in CORE8 if s in p['symbols']}
    L = ['# M3: short-side mechanical parity (mirror proof)', '',
         'Long `trend_ema_mom.v1` on `data_long` 4h vs the mirrored-short fixture (`enable_short`) on the mirrored '
         'series (`p -> 2*P0 - p`, high/low swapped, P0 = the highest high; `newcore/runner/mirror.py`), each through '
         'the full Runner: FakeVenue + NC-02a FileJournal, testnet exchange filters, 1% risk of equity. Generated by '
         '`python -m newcore.runner.parity`. A short trade on the mirror is paired with the long trade on the '
         'original by symbol and entry candle; "same" = entry candle, exit candle, exit code and quantity equal.', '',
         '**What the mirror can and cannot prove.** Signals, stop distances and the intrabar walk are mirror-exact, so '
         'at **zero costs** with the notional cap lifted the two runs must agree exactly, R included (Decimal '
         'equality). Fees, slippage and funding are proportional to the price LEVEL, which the mirror changes, and so '
         'is a notional (leverage) cap: those rows measure the asymmetry, they cannot be zero by construction.', '']
    for p in parts:
        if p['kind'] == 'single':
            L += [f"## Single symbol, costs = {p['costs']}", '',
                  '| symbol | long trades | short trades | same entry/exit candle + code | + same qty | R exactly equal | '
                  'median abs dR | max abs dR | sum R long | sum R short | unprotected cycles (L/S) |',
                  '|---|---|---|---|---|---|---|---|---|---|---|']
            tot = [0, 0, 0, 0, 0]
            for s, r in p['symbols'].items():
                cc = r.get('same_candles_code', r['same_exit_qty'])
                tot = [tot[0] + r['longs'], tot[1] + r['shorts'], tot[2] + cc, tot[3] + r['same_exit_qty'],
                       tot[4] + r['r_equal']]
                L.append(f"| {s} | {r['longs']} | {r['shorts']} | {cc} | {r['same_exit_qty']} | {r['r_equal']} | "
                         f"{Decimal(r.get('median_abs_dr', r['max_abs_dr'])):.6g} | {Decimal(r['max_abs_dr']):.6g} | "
                         f"{Decimal(r['sum_r_long']):.4f} | {Decimal(r['sum_r_short']):.4f} | "
                         f"{r['unprotected'][0]} / {r['unprotected'][1]} |")
            L += [f'| **total** | **{tot[0]}** | **{tot[1]}** | **{tot[2]}** | **{tot[3]}** | **{tot[4]}** | | | | | |', '']
            for s, r in p['symbols'].items():
                if r['diffs'] or r['only_long'] or r['only_short']:
                    d = '; '.join(f"entry candle {x['long']['i_in']}: long exits {x['long']['i_out']} "
                                  f"{x['long']['exit']}, short exits {x['short']['i_out']} {x['short']['exit']}"
                                  for x in r['diffs'])
                    L.append(f"- {s}: {d}" + (f"; only long {r['only_long']}" if r['only_long'] else '') +
                             (f"; only short {r['only_short']}" if r['only_short'] else ''))
            L.append('')
        else:
            r = p['book']
            kill = '10% kill' if p.get('kill', 'on') == 'on' else 'kill disarmed'
            L += [f"## S4 book (core 8, canary: 4 positions, 3% Cairo-day halt, {kill}; cap lifted), costs = "
                  f"{p['costs']}", '',
                  f"- trades long / short: {r['longs']} / {r['shorts']}; same entry/exit candle + code: "
                  f"{r.get('same_candles_code', r['same_exit_qty'])}; + same qty: {r['same_exit_qty']};"
                  f" R exactly equal: {r['r_equal']}; max abs dR {Decimal(r['max_abs_dr']):.6g}",
                  f"- refusals (max positions, halt, kill) long / short: {r['skips'][0]} / {r['skips'][1]}; final mode "
                  f"{r['modes'][0]} / {r['modes'][1]}; unprotected cycles {r['unprotected'][0]} / {r['unprotected'][1]}",
                  f"- risk events (halt / kill: kind, Cairo day, equity change) equal: **{r['events_equal']}**",
                  f"  - long: {[(k, d, f'{float(c):+.4%}') for k, d, c in r['events_long']]}",
                  f"  - short: {[(k, d, f'{float(c):+.4%}') for k, d, c in r['events_short']]}", '']
    L += ['## Short side under crash / restart', '',
          '- `tests/newcore_slice/test_short_parity.py::test_short_side_crash_restart_sample_matches_the_uninterrupted_run`'
          ' (mirrored BTCUSDT: a path stop; mirrored SOLUSDT: signal closes): the process dies before and after each of '
          'the first venue effects (entry, stop, cancel, close), restarts from the FileJournal, and finishes with the '
          'identical trade list; a forced external stop cancel is restored with exactly one replacement stop.',
          '- `tests/newcore_slice/test_crash_safety.py` (MemoryJournal and FileJournal): the SHORT points of the '
          'crash matrix: `trade-SHORT` (entry, stop, cancel, signal close), `replacement-SHORT` (stop replace after an '
          'external cancel) and `stopped-SHORT` (a gapped stop filled at the open), crash before / after every venue '
          'effect: never ACTIVE naked, at most one resting stop, one entry, the same trade as uninterrupted.',
          '', '## Reading the cost-on rows', '',
          '- Fees, slippage and funding are a fraction of the PRICE, and the mirrored price level differs (e.g. '
          'SOLUSDT at 20 mirrors to about 500): costs per trade differ, so the equity paths and therefore the sizes '
          'diverge (quantities match only by chance) and R differs by the cost difference in R.',
          '- The few pairs whose exit differs (a long SIGNAL_EXIT vs a short STOP_HIT) come from the same cause: the '
          'stop is set from the FILL, and the fill carries slippage of a different price level on each side '
          '(long: o x (1 + s) - d; mirrored short: o\' x (1 - s) + d, which mirrors back to o - d + s x (2 P0 - o), not '
          'o - d + s x o), so the mirrored stop sits a little closer and is reached where the original one was not. '
          'At zero costs this term is 0 and every pair agrees exactly (tables above).', '']
    with open(a.out, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(L) + '\n')
    return None


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m newcore.runner.parity')
    p.add_argument('command', choices=('single', 'book', 'report'))
    p.add_argument('--root', default='.')
    p.add_argument('--symbols', default=','.join(CORE8))
    p.add_argument('--costs', default='zero', choices=tuple(COSTS))
    p.add_argument('--work', default='')
    p.add_argument('--out', required=True)
    p.add_argument('--parts', nargs='*', default=())
    p.add_argument('--kill', default='on', choices=('on', 'off'), help='book: the 10%% drawdown kill')
    a = p.parse_args(argv)
    a.symbols = tuple(a.symbols.split(','))
    try:
        if a.work:
            os.makedirs(a.work, exist_ok=True)                            # create_journal makes one level only
        if a.command == 'report':
            cmd_report(a)
            return 0
        res = (cmd_single if a.command == 'single' else cmd_book)(a)
    except (OSError, ValueError, KeyError) as ex:                         # e.g. no data under --root: a clean refusal
        print(f'parity: {describe(ex, (ValueError,))}', file=sys.stderr)   # research tool over our own data
        return 2
    with open(a.out, 'w', encoding='utf-8') as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    return 1 if mismatch(res, a.costs) else 0


def mismatch(res, costs):
    """The CI gate (Cowork M2): at ZERO costs the mirror must be exact - every trade paired, the same exit and qty,
    R equal. With costs the asymmetry is measured, not gated (fees / funding scale with the price level)."""
    if costs != 'zero':
        return False
    rows = list(res['symbols'].values()) if res['kind'] == 'single' else [res['book']]
    return any(r['only_long'] or r['only_short'] or r['diffs'] or r['same_exit_qty'] != r['paired']
               or r['r_equal'] != r['paired'] or r['longs'] != r['shorts'] for r in rows)


if __name__ == '__main__':
    sys.exit(main())
