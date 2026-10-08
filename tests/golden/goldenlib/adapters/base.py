"""Adapter protocol and the legacy neutral-slot mapping (design section 3, MGMT_MAP)."""
from dataclasses import dataclass, field


class NotExpressible(Exception):
    """The adapter cannot express a neutral key of the case."""


@dataclass
class Trace:
    """Canonical output of one adapter run. trades: [{sym, side, i_in, i_out, exit, R, pnl}] sorted by (i_in, sym); a trade may
    carry `_res` = {R, pnl}: the adapter's stated output resolution for that trade (e.g. the engine journal's rounding), never
    compared. final: exactly the keys the adapter declares in adapters.CAPS (compare() treats a declared key that is missing
    as a mismatch)."""
    trades: list
    final: dict
    raw: dict = field(default_factory=dict)      # adapter-native extras for debugging (never compared)

    def as_dict(self):
        return dict(trades=self.trades, final=self.final)


# Neutral slot key -> legacy (engine sleeve mgmt == backtester mgmt) key. Both legacy models share one mgmt vocabulary.
MGMT_MAP = {
    ('stop', 'atr'): 'stop_atr',
    ('trail', 'atr'): 'trail_atr',
    ('target', 'r'): 'tp_r',
    ('time_exit', 'bars'): 'max_bars',
}
BLOCKS = {'dca': ('n', 'step_atr', 'scale', 'tp_atr', 'stop_atr'), 'pyramid': ('n', 'step_r', 'frac')}
# Carrier strategy keys: signals are injected, so the key only decides the strategy-default mgmt that merge_mgmt adds.
# Every default the carrier brings is overridden below, so the effective mgmt is exactly the case's.
CARRIER = {'plain': 'ema_st', 'dca': 'dca_dip'}
NO_TIME_EXIT = 10 ** 6
# P2-3b: backtest.close() appends ONE trade row per position (why = the final close) and the engine journal ONE history row per
# lot (exit_reason = the final close; partial closes only inside `fills`). The canonical v1 trace is one row per position, so a
# partial exit would surface as e.g. SIGNAL_EXIT with a blended R. Until the AUD-08 event trace these keys are NotExpressible.
PARTIAL_EXITS = ('tp1', 'ladder', 'runner')


def _num(x):
    v = float(x)
    return int(v) if v.is_integer() else v


def legacy_slot(case):
    """Neutral slot -> (carrier key, mgmt dict, extras dict) for both legacy models."""
    sl = case['slot']
    mg = {}
    for (blk, k), leg in MGMT_MAP.items():
        if sl.get(blk) is not None:
            if k not in sl[blk]:
                raise NotExpressible(f'slot.{blk} needs {k!r}')
            mg[leg] = _num(sl[blk][k])
            extra = set(sl[blk]) - {kk for (b, kk) in MGMT_MAP if b == blk}
            if extra:
                raise NotExpressible(f'slot.{blk}: keys {sorted(extra)} not expressible in the legacy models')
    for blk, keys in BLOCKS.items():
        if sl.get(blk) is not None:
            if set(sl[blk]) != set(keys):
                raise NotExpressible(f'slot.{blk} must give exactly {keys} (no silent strategy defaults)')
            mg[blk] = {k: _num(sl[blk][k]) for k in keys}
    for blk in PARTIAL_EXITS:
        if sl.get(blk) is not None:
            raise NotExpressible(f'slot.{blk}: partial exits are not expressible in the v1 canonical trace (one row per position '
                                 'with its final exit code; both legacy outputs fold a tp1 / ladder / runner part into that row, '
                                 'so TP_PARTIAL / TP_LADDER could never be emitted). Comes with the AUD-08 event trace.')
    key = CARRIER['dca' if 'dca' in mg else 'plain']
    if 'max_bars' not in mg:
        mg['max_bars'] = NO_TIME_EXIT            # dca_dip's default max_bars=60 must not leak into a case without a time exit
    if 'stop_atr' not in mg and 'dca' not in mg:
        raise NotExpressible('slot.stop.atr is required (the carrier default 2.5 must not leak in)')
    extras = {}
    ent = sl.get('entry') or {'type': 'market'}
    if ent['type'] == 'trail':
        extras['trail_entry'] = {'dev_atr': _num(ent['dev_atr']), 'max_bars': _num(ent['max_bars'])}
    elif ent['type'] != 'market':
        raise NotExpressible(f"slot.entry.type {ent['type']!r}: maker entries need the C12 fill-point model (not in v1)")
    return key, mg, extras


def check_costs(case, funding):
    """The legacy models use their module constants (backtest.FEE / SLIP; the replay's exchange charges the same). A case
    that declares other costs is not expressible there - never silently run with the wrong costs.
    funding: whether the adapter models costs.funding_per_bar (the backtester does; the engine replay charges no funding, so
    a non-zero funding cost there would be silently dropped - P2-3a)."""
    import backtest as B
    from ..schema import COST_KEYS
    c = case.get('costs') or {}
    unknown = set(c) - set(COST_KEYS)
    if unknown:
        raise NotExpressible(f'costs: unknown keys {sorted(unknown)}')
    for k, v in (('taker_fee', B.FEE), ('slip', B.SLIP)):
        if k in c and abs(float(c[k]) - v) > 1e-12:
            raise NotExpressible(f'costs.{k}={c[k]}: the legacy models are fixed at {v}')
    if not funding and float(c.get('funding_per_bar', 0)) != 0:
        raise NotExpressible(f"costs.funding_per_bar={c['funding_per_bar']}: this adapter charges no funding")


def symbols(case):
    return list(case['slot'].get('symbols') or list(case['market']))


def side_code(sd):
    return 'LONG' if sd in (1, 'LONG') else 'SHORT'


def check_unsupported(case):
    """AUD-08 inputs the legacy adapters cannot run: a case that declares them must mark the legacy adapters not_applicable.
    Never silently dropped - a fault the replay does not inject, or exchange filters it does not apply, would make the
    legacy trace look like the faulted / filtered one."""
    if case.get('faults'):
        raise NotExpressible(f"faults {[f.get('kind') for f in case['faults']]}: the legacy replay / backtester cannot inject "
                             'outages, restarts or lost order answers (the fault-capable fake comes with NC-03 / NC-08)')
    if case.get('instruments') is not None:
        raise NotExpressible('instruments: exchange filters are not wired into the legacy adapters in v1 (replay / backtest '
                             'exchange_rules); a case with its own step / minimums is not expressible there')
