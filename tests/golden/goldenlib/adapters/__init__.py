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
