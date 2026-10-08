"""Adapters: run(case) -> canonical trace. A neutral key an adapter cannot express raises NotExpressible (a hard error;
the case must then declare that adapter `not_applicable` with a reason)."""
from .base import NotExpressible, Trace  # noqa: F401

# P2-1: what each adapter can report. `final` = the expect.final keys it reports in every trace (a declared key missing from a
# trace is a mismatch, an undeclared one is ignored for that adapter and listed in the report); `funding` = models
# costs.funding_per_bar (otherwise a non-zero funding cost raises NotExpressible).
CAPS = {
    'legacy_backtest': dict(final=frozenset(), funding=True),         # backtest.run returns trades + equity curve, no open book
    # replay metrics carry the open lots. funding: the engine journal (what is compared) carries no funding at all - the
    # replay's simulated account charges backtest.FUND_PER_BAR whatever the case says, which only reaches sizing equity
    'legacy_engine': dict(final=frozenset({'lots'}), funding=False),
}


def get(name):
    if name == 'legacy_backtest':
        from .legacy_backtest import LegacyBacktest
        return LegacyBacktest()
    if name == 'legacy_engine':
        from .legacy_engine import LegacyEngine
        return LegacyEngine()
    raise KeyError(f'no adapter {name!r} in this tree (NEWCORE adapters arrive with NC-07/NC-08)')


POISON = 'ADAPTER-READ-THE-EXPECTATION'


def blind(case):
    """The case as the adapters see it in the pack (Cowork r2 attack (a)): `expect` replaced by a poisoned expectation and
    `known_divergences` removed. Every input an adapter needs is untouched, so a correct adapter's trace is identical; an
    adapter that reads or echoes the expectation produces poison and fails its golden comparison (the comparison itself
    always uses the real, unmodified case)."""
    import copy
    c = copy.deepcopy(case)
    c['expect'] = dict(trades=[dict(sym=POISON, side=POISON, i_in=-1, i_out=-1, exit=POISON, R='999', pnl='999')
                               for _ in (case.get('expect') or {}).get('trades') or [None]],
                       final=dict(lots=-999))
    c.pop('known_divergences', None)
    return c
