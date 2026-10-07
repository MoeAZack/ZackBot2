"""Adapters: run(case) -> canonical trace. A neutral key an adapter cannot express raises NotExpressible (a hard error;
the case must then declare that adapter `not_applicable` with a reason)."""
from .base import NotExpressible, Trace  # noqa: F401


def get(name):
    if name == 'legacy_backtest':
        from .legacy_backtest import LegacyBacktest
        return LegacyBacktest()
    if name == 'legacy_engine':
        from .legacy_engine import LegacyEngine
        return LegacyEngine()
    raise KeyError(f'no adapter {name!r} in this tree (NEWCORE adapters arrive with NC-07/NC-08)')
