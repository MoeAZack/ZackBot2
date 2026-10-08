"""Replay the core-8 BOOK of data_long (4h) through the S4 BookRunner and line its trades up with the research book
(newcore/strategy/research/ema_mom_after_cost.py simulate(): shared equity, 1% of closed equity, 3x gross cap, next-open
entries in the CORE8 order, base costs incl. funding). Fixture: fixtures/ema_mom_long_book_trades.csv = the research
trade list's `long` rows for costs 'base' (no position cap) and 'base, max_pos 4'.

The research book has no daily halt and no drawdown kill, so the comparison runs with both off; the canary profile
(3% halt, 10% kill) is run separately and reported as a delta.
"""
import csv
import os
from decimal import Decimal

from data_long_replay import BASE, ROOT, WHY
from newcore.adapters import CsvBarSource, FakeVenue, MemoryJournal, load_rules
from newcore.domain import ReasonCode
from newcore.risk import BookPolicy
from newcore.runner import EmaMomSignals, RunnerConfig, SizingPolicy, run_replay
from newcore.runner.book import BookRunner
from slice_helpers import ACCOUNT_ID, H4, PORTFOLIO_ID, sim_account

CORE8 = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT')
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'ema_mom_long_book_trades.csv')
RESEARCH = BookPolicy(max_positions=None, daily_loss_pct=None, kill_drawdown_pct=None)       # the research book
RESEARCH_MAXPOS4 = BookPolicy(max_positions=4, daily_loss_pct=None, kill_drawdown_pct=None)
CANARY = BookPolicy()                                                                         # 4 / 3x / 3% / 10%


def world(policy, *, upto=None, equity='10000'):
    src = CsvBarSource.from_data_long(ROOT, list(CORE8), '4h')
    candles = {s: src.all_bars(s) for s in CORE8}
    if upto is not None:
        candles = {s: b[:upto] for s, b in candles.items()}
        src = CsvBarSource(candles, H4)
    venue = FakeVenue(candles, H4, costs=BASE, equity=Decimal(equity))
    t0 = candles[CORE8[0]][0].open_ms
    cfg = RunnerConfig(account=sim_account(t0 - 1), portfolio_id=PORTFOLIO_ID, symbols=CORE8, tf_ms=H4, timeframe='4h',
                       rules=load_rules(os.path.join(ROOT, 'data', 'exchange_rules_testnet.json'), CORE8),
                       sizing=SizingPolicy(), sides=('LONG',))
    journal = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    signals = EmaMomSignals(H4, '4h')

    def make(j=None):
        return BookRunner(cfg, policy=policy, journal=j or journal, venue=venue, bars=src, signals=signals)
    return make, venue, journal, candles[CORE8[0]]


def run(policy, *, upto=None, restart_at=(), on_cycle=None):
    make, venue, journal, bars = world(policy, upto=upto)
    state = {'journal': journal}

    def remake():
        state['journal'] = state['journal'].reopen()
        return make(state['journal'])
    runner = run_replay(make(), venue, start_ms=bars[0].open_ms, end_ms=bars[-1].close_ms, tf_ms=H4,
                        make_runner=remake, restart_at=restart_at, on_cycle=on_cycle)
    return runner, venue, bars


def research_rows(costs, *, before_bar=None):
    with open(FIXTURE, newline='', encoding='utf-8') as fh:
        rows = [r for r in csv.DictReader(fh) if r['costs'] == costs]
    if before_bar is not None:
        rows = [r for r in rows if int(r['i_out']) < before_bar]
    return rows


def runner_rows(runner, t0):
    out = []
    for t in runner.trades():
        i_out = ((t.exit_signal_close_ms - t0) // H4 - 1) if t.exit_reason is ReasonCode.EXIT_SIGNAL \
            else (t.exit_ms - t0) // H4
        out.append(dict(sym=t.symbol, i_in=(t.entry_ms - t0) // H4, i_out=i_out, exit=t.exit_code, R=t.r, pnl=t.pnl,
                        qty=t.qty))
    return out


def compare(runner, bars, costs, *, before_bar=None):
    t0 = bars[0].open_ms
    mine = {(r['sym'], r['i_in']): r for r in runner_rows(runner, t0) if before_bar is None or r['i_out'] < before_bar}
    theirs = {(r['sym'], int(r['i_in'])): r for r in research_rows(costs, before_bar=before_bar)}
    pairs = []
    for k in sorted(set(mine) & set(theirs), key=lambda k: (k[1], k[0])):
        a, b = mine[k], theirs[k]
        pairs.append(dict(sym=k[0], i_in=k[1], same_exit=(a['i_out'], a['exit']) == (int(b['i_out']), WHY[b['why']]),
                          i_out=(a['i_out'], int(b['i_out'])), exit=(a['exit'], WHY[b['why']]), R=(a['R'], Decimal(b['R'])),
                          dR=a['R'] - Decimal(b['R']), pnl=(a['pnl'], Decimal(b['pnl'])),
                          dpnl=a['pnl'] - Decimal(b['pnl'])))
    return pairs, sorted(set(theirs) - set(mine)), sorted(set(mine) - set(theirs))
