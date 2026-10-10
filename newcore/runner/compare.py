"""Line a Runner's closed trades up with a research trade list (the after-cost research CSV of
newcore/strategy/research/ema_mom_after_cost.py --trades-csv: columns sym, side, i_in, i_out, why, R, ... and optionally
mode / costs, filtered to mode=long, costs=base).

Conventions: research i_out is the bar whose stop / gap / signal closed the trade (a signal exits at that bar's close);
the Runner's signal exit is decided at that close and fills at the next open, so its DECISION bar is compared. A pair
matches when entry bar, exit bar and exit code agree; R is reported, not required to be equal (size / tick floors).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal

from newcore.domain import ReasonCode

WHY = {'stop': 'STOP_HIT', 'gap_stop': 'STOP_HIT', 'signal': 'SIGNAL_EXIT'}


@dataclass(frozen=True)
class Comparison:
    symbol: str
    runner: int
    research: int
    matched: int
    only_research: tuple
    only_runner: tuple
    max_abs_dr: Decimal
    sum_r_runner: Decimal
    sum_r_research: Decimal

    def line(self):
        return (f'{self.symbol}: {self.matched}/{self.research} research trades matched (runner {self.runner}); '
                f'only research {list(self.only_research)}, only runner {list(self.only_runner)}; '
                f'max |dR| {self.max_abs_dr:.6f}; sum R {self.sum_r_runner:.4f} vs {self.sum_r_research:.4f}')


def research_rows(path, symbol):
    with open(path, newline='', encoding='utf-8') as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        if r.get('mode', 'long') != 'long' or r.get('costs', 'base') != 'base' or r['sym'] != symbol:
            continue
        out.append(r)
    return out


def compare(runner, symbol, first_open_ms, tf_ms, research_csv) -> Comparison:
    mine = {}
    for t in runner.trades():
        if t.symbol != symbol:
            continue
        i_in = (t.entry_ms - first_open_ms) // tf_ms
        if t.exit_reason is ReasonCode.EXIT_SIGNAL:
            i_out = (t.exit_signal_close_ms - first_open_ms) // tf_ms - 1
        else:
            i_out = (t.exit_ms - first_open_ms) // tf_ms
        mine[i_in] = (i_out, t.exit_code, t.r)
    theirs = {int(r['i_in']): (int(r['i_out']), WHY[r['why']], Decimal(r['R'])) for r in research_rows(research_csv,
                                                                                                       symbol)}
    both = set(mine) & set(theirs)
    matched = sum(1 for i in both if mine[i][:2] == theirs[i][:2])
    dr = [abs(mine[i][2] - theirs[i][2]) for i in both]
    return Comparison(symbol=symbol, runner=len(mine), research=len(theirs), matched=matched,
                      only_research=tuple(sorted(set(theirs) - set(mine))),
                      only_runner=tuple(sorted(set(mine) - set(theirs))),
                      max_abs_dr=max(dr, default=Decimal(0)), sum_r_runner=sum((m[2] for m in mine.values()), Decimal(0)),
                      sum_r_research=sum((r[2] for r in theirs.values()), Decimal(0)))
