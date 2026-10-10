"""M4 management test builders: a World whose runner is the ManagedRunner (synthetic plan on FakeVenue)."""
from decimal import Decimal as D

from newcore.domain import Action, Purpose
from newcore.management import CostModel
from newcore.runner import InjectedSignals
from newcore.runner.managed import ManagedRunner, ManagementConfig, SyntheticPlans
from slice_helpers import H4, T0, World, flat_bars

SYM = 'SOLUSDT'
ZERO_COSTS = CostModel(taker_fee=D(0), slip=D(0))
K = D(200)                                      # mirror axis: a long price p is the short price 200 - p


def mirror_bar(o, h, l, c, side):
    o, h, l, c = (D(str(x)) for x in (o, h, l, c))
    if side == 'SHORT':
        return K - o, K - l, K - h, K - c
    return o, h, l, c


def path(n, overrides, side):
    """flat_bars at 100 (wick 0.5) with `overrides` given in the LONG frame, mirrored for a short."""
    ov = {i: mirror_bar(*v, side) for i, v in overrides.items()}
    return flat_bars(n, px='100', overrides=ov)


def signals(side, enter_bar=5, close_bar=None):
    s = {(SYM, T0 + (enter_bar + 1) * H4): (('enter', side),)}
    if close_bar is not None:
        s[(SYM, T0 + (close_bar + 1) * H4)] = (('close', side),)
    return InjectedSignals(s, stop_atr=D('2'))


class MgWorld(World):
    def __init__(self, candles, sigs, *, plans=None, enabled=True, **kw):
        self.management = ManagementConfig(enabled=enabled, plans=plans or SyntheticPlans(costs=ZERO_COSTS))
        super().__init__(candles, sigs, **kw)

    def new_runner(self, hard_hold=None):
        return ManagedRunner(self.config, journal=self.journal, venue=self.port, bars=self.bars, signals=self.signals,
                             account_reads=self.venue, hard_hold=hard_hold, management=self.management)


def mg_decisions(runner, purpose=None):
    out = []
    for d in runner.fold.decisions.values():
        if d.detail.startswith('mg ') and d.action is not Action.WAIT:
            if purpose is None or d.intents[0].purpose is purpose:
                out.append(d)
    return out


def intents_of(runner, purpose):
    return [iv for iv in runner.fold.intents.values() if iv.purpose is purpose]


__all__ = ['SYM', 'ZERO_COSTS', 'path', 'signals', 'MgWorld', 'mg_decisions', 'intents_of', 'Purpose', 'H4', 'T0']
