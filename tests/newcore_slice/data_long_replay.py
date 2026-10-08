"""Replay one data_long symbol through the S1 Runner (FakeVenue + MemoryJournal + CsvBarSource + ema_mom) and line its
trades up with the research after-cost trade list (fixtures/ema_mom_long_base_trades.csv).

The fixture is the `mode=long, costs=base` rows of the research trade list written by
`newcore/strategy/research/ema_mom_after_cost.py --trades-csv` at d21d113 (re-run on this branch: byte-identical file,
sha256 8cc863a9d8a7fef56250c005e55e627a12b4728ddf0cad465b62bbe3c73868b2). That run is the core-8 BOOK (shared
equity, 3x gross cap across the 8 symbols); this replay is ONE symbol on its own equity. Both use 1% risk of closed
equity, next-open entries, 2.5 ATR stops from the fill, taker 0.05% + slip 0.02% per side, funding 0.005% per 4h bar.

Research i_out: the bar whose stop / gap / signal closed the trade (a signal exit fills at that bar's CLOSE). Runner: a
stop fills inside its bar (same index); a signal exit is decided at that bar's close and fills at the NEXT bar's open, so
the runner's decision bar is compared with the research i_out.
"""
import csv
import os
from decimal import Decimal

from newcore.adapters import CostModel, CsvBarSource, FakeVenue, MemoryJournal, load_rules
from newcore.domain import ReasonCode
from newcore.runner import EmaMomSignals, Runner, RunnerConfig, SizingPolicy, run_replay
from slice_helpers import ACCOUNT_ID, H4, PORTFOLIO_ID, sim_account

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'ema_mom_long_base_trades.csv')
BASE = CostModel(taker_fee=Decimal('0.0005'), slip=Decimal('0.0002'), funding_per_bar=Decimal('0.00005'))
WHY = {'stop': 'STOP_HIT', 'gap_stop': 'STOP_HIT', 'signal': 'SIGNAL_EXIT'}


def replay(symbol, *, equity='10000', window=1500, upto=None):
    src = CsvBarSource.from_data_long(ROOT, [symbol], '4h')
    bars = src.all_bars(symbol)
    if upto is not None:
        bars = bars[:upto]
        src = CsvBarSource({symbol: bars}, H4)
    venue = FakeVenue({symbol: bars}, H4, costs=BASE, equity=Decimal(equity))
    journal = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    rules = load_rules(os.path.join(ROOT, 'data', 'exchange_rules_testnet.json'), [symbol])
    cfg = RunnerConfig(account=sim_account(bars[0].open_ms - 1), portfolio_id=PORTFOLIO_ID, symbols=(symbol,),
                       tf_ms=H4, timeframe='4h', rules=rules, sizing=SizingPolicy(Decimal('0.01'), Decimal('3')),
                       sides=('LONG',))
    signals = EmaMomSignals(H4, '4h', window=window)
    runner = Runner(cfg, journal=journal, venue=venue, bars=src, signals=signals)
    runner = run_replay(runner, venue, start_ms=bars[0].open_ms, end_ms=bars[-1].close_ms, tf_ms=H4)
    return runner, venue, journal, bars


def research_rows(symbol):
    with open(FIXTURE, newline='', encoding='utf-8') as fh:
        return [r for r in csv.DictReader(fh) if r['sym'] == symbol]


def runner_rows(runner, t0):
    out = []
    for t in runner.trades():
        i_in = (t.entry_ms - t0) // H4
        if t.exit_reason is ReasonCode.EXIT_SIGNAL:
            i_dec = (t.exit_signal_close_ms - t0) // H4 - 1           # the decision bar (research i_out)
        else:
            i_dec = (t.exit_ms - t0) // H4
        out.append(dict(i_in=i_in, i_out=i_dec, exit=t.exit_code, px_in=t.entry_price, px_out=t.exit_price,
                        R=t.r, pnl=t.pnl, trade=t))
    return out


def compare(symbol, runner, bars):
    """Pairs research and runner trades by entry bar. Returns (pairs, only_research, only_runner)."""
    t0 = bars[0].open_ms
    mine = {r['i_in']: r for r in runner_rows(runner, t0)}
    theirs = {int(r['i_in']): r for r in research_rows(symbol)}
    pairs = []
    for i in sorted(set(mine) & set(theirs)):
        a, b = mine[i], theirs[i]
        pairs.append(dict(i_in=i, i_out=(a['i_out'], int(b['i_out'])), exit=(a['exit'], WHY[b['why']]),
                          why=b['why'], px_in=(a['px_in'], Decimal(b['px_in'])), px_out=(a['px_out'], Decimal(b['px_out'])),
                          R=(a['R'], Decimal(b['R'])), dR=a['R'] - Decimal(b['R'])))
    return pairs, sorted(set(theirs) - set(mine)), sorted(set(mine) - set(theirs))
