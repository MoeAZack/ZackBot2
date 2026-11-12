"""C-BRKPROT-v1 (management key 'prot', default OFF): one protective-stop rule shared by backtest.run and the engine.

Kept out of strategies.py on purpose: frozen research evaluations pin the strategies.py bytes."""
import math

PROT_FEE, PROT_SLIP = 0.0005, 0.0002   # C-BRKPROT-v1 default cost model (= backtest FEE / SLIP, engine FEE_EST + exit slip)


def prot_buf(side, fee, slip):
    """Exact breakeven buffer (fraction of avg) so that a full exit stopped AT avg*(1+side*buf) nets zero after costs:
    the entry fee on avg, the exit filled at the level slipped by `slip` against the side, and the exit fee on that fill.
    LONG: (1+b)(1-slip)(1-fee) = 1+fee.  SHORT: (1-b)(1+slip)(1+fee) = 1-fee.  (avg is the already-slipped entry fill.)"""
    if side == 1: return (1 + fee) / ((1 - slip) * (1 - fee)) - 1
    return 1 - (1 - fee) / ((1 + slip) * (1 + fee))


def prot_level(prot, side, e0, avg, R, best, atr, fee=PROT_FEE, slip=PROT_SLIP):
    """C-BRKPROT-v1 protective stop candidate, shared by backtest.run and the engine (one rule, two callers).

    prot = {'at_r', 'trail_atr', and at most one of 'buf' / 'cost'} (management key 'prot', default OFF). side +1/-1.
    None until the best price reached is at least at_r x R in favour (nothing moves before activation, not even the trail).
    Then the tighter of the breakeven level and, if trail_atr, best - side x trail_atr x atr. Breakeven buffer, frozen per
    cost scenario: 'buf' = an explicit fraction; 'cost' = one all-in cost per executed side (fee-like, no separate slip:
    FWD-01's 7/15/23 bp scenarios) -> prot_buf(side, cost, 0); neither = the caller's fee / slip -> prot_buf(side, fee, slip).
    Apply with prot_tighten (the runtime's minimum-step rule) so backtest and engine move the stop at the same prices."""
    if not prot or not R or R <= 0: return None
    if side * (best - e0) < prot.get('at_r', 1.0) * R: return None
    if 'buf' in prot: b = prot['buf']
    elif 'cost' in prot: b = prot_buf(side, prot['cost'], 0.0)
    else: b = prot_buf(side, fee, slip)
    lvl = avg * (1 + side * b)
    k = prot.get('trail_atr')
    if k and atr and atr == atr:
        tr = best - side * k * atr
        lvl = max(lvl, tr) if side == 1 else min(lvl, tr)
    return lvl


def prot_round(lvl, side, tick):
    """Round a protective level to the price tick on the PROTECTIVE side (LONG up, SHORT down), so the exchange stop is never
    looser than the computed breakeven - the engine's later floor to the tick then leaves it unchanged."""
    if lvl is None or not tick: return lvl
    k = lvl / tick
    n = math.ceil(k - 1e-9) if side == 1 else math.floor(k + 1e-9)
    return round(n * tick, 12)


PROT_EXCLUSIVE = ('be_r', 'trail_atr', 'runner', 'ttp')


def prot_conflicts(mgmt):
    """Other stop movers that are switched on next to 'prot' in an EFFECTIVE management dict (strategy defaults + the
    slot's overrides). The engine moves its stop once for the best of all candidates with one min-step, while the backtest
    tightens each rule on its own; mixing them would make runtime and replay disagree, so 'prot' must be the only stop
    mover. 0 / None / {} switch a rule off (e.g. trail_atr=0 on bear_breakdown / hot_coin, whose defaults trail)."""
    if not (mgmt or {}).get('prot'): return []
    return [k for k in PROT_EXCLUSIVE if mgmt.get(k)]


def prot_tighten(prot, stop, lvl, side, atr, tick=0.0):
    """The engine's stop-move rule, shared: move only when the improvement exceeds max(min_step, tick), min_step =
    0.1 ATR with a trail (0.05 ATR without). Below that the stop stays where it is (backtest and runtime alike)."""
    if lvl is None: return stop
    lvl = prot_round(lvl, side, tick)
    a = atr if (atr and atr == atr) else 0.0
    step = (0.1 if prot.get('trail_atr') else 0.05) * a
    return lvl if side * (lvl - stop) > max(step, tick or 0.0) else stop
