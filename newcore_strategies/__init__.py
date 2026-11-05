"""STRAT-01: frozen research strategies as a NEWCORE EntrySource.  Outside newcore/runtime by design (imports pandas
and the legacy strategies.py).  Factory: ``newcore_strategies.build_strategy_entries``."""
from newcore_strategies.entry_source import (CROSS_SECTIONAL, CandleFeed, RiskContext, RiskContextProvider, Signal,
                                             StrategyConfigError, StrategyEntrySource, StrategySlot, slot_from_dict)
from newcore_strategies.feeds import CsvCandleFeed
from newcore_strategies.presets import CORE_20, TESTNET_SET_2026_11_05, build_slots


def build_strategy_entries(*, feed, risk, wall_clock, base_risk_fraction, leverage, rows=TESTNET_SET_2026_11_05,
                           **kwargs):
    """Factory for the runtime's config-selected entries hook.  Returns (StrategyEntrySource, refused slots)."""
    slots, refused = build_slots(rows)
    if not slots:
        raise StrategyConfigError('no_slots_available')
    return StrategyEntrySource(slots, feed=feed, risk=risk, wall_clock=wall_clock,
                               base_risk_fraction=base_risk_fraction, leverage=leverage, **kwargs), refused


__all__ = ['CORE_20', 'CROSS_SECTIONAL', 'CandleFeed', 'CsvCandleFeed', 'RiskContext', 'RiskContextProvider', 'Signal',
           'StrategyConfigError', 'StrategyEntrySource', 'StrategySlot', 'TESTNET_SET_2026_11_05',
           'build_slots', 'build_strategy_entries', 'slot_from_dict']
