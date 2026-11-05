"""STRAT-01 presets.  TESTNET_SET_2026_11_05 is the shortlist in reports/testnet-strategy-pick-2026-11-05.md,
minus the three candidates that FAILED the one-shot sealed holdout of 5 Nov 2026 (prereg sha256 d3f93330..., issue
#219): C24 rotation_long_h2 (-15.70R), C213 evening_star_short_1h (-6.05R) and C164 starc_band_breakout_long_1h
(-20.98R).  "Losing after costs = no PAPER", so they get no testnet slot.  C101 passed weakly (16 trades) and stays.

Risk multipliers are the 3 Nov 2026 drawdown-policy caps (``min(1, 15 / dev max DD R)``), never raised:
C101 9.689494R -> 1.0.
The 4h range book (exp_range / exp_vwaprev) has positive forward PAPER P&L but no frozen backtest record, so it
runs at 0.5x as forward-evidence-only sleeves.  Keys that this checkout's ``strategies.py`` does not define (C101, C164
and C213 live on research branches until merged) are reported as unavailable, never approximated.
"""
from __future__ import annotations

from newcore_strategies.entry_source import StrategyConfigError, slot_from_dict

CORE_20 = ('BTCUSDT', 'ETHUSDT', 'ZECUSDT', 'SOLUSDT', 'XRPUSDT', 'NEARUSDT', 'HYPEUSDT', 'DOGEUSDT', 'UNIUSDT',
           'SUIUSDT', 'QNTUSDT', 'BNBUSDT', 'LSKUSDT', 'ENAUSDT', '1000PEPEUSDT', 'WLDUSDT', 'PUMPUSDT', 'ADAUSDT',
           'AVAXUSDT', 'LINKUSDT')   # research_evidence/universe/static-top40-minhist365-contract-20261011.json [:20]


def _slot(key, name, tf, side, mult):
    return dict(key=key, name=name, version='v1', tf=tf, side=side, symbols=list(CORE_20), risk_mult=mult,
                strategy_id=name, book_id='testnet_strat01')


HOLDOUT_FAILED_2026_11_05 = frozenset({'rotation_long_h2', 'evening_star_short_1h', 'starc_band_breakout_long_1h'})

TESTNET_SET_2026_11_05 = (
    _slot('kalman_innovation_recovery_long_1h', 'c101_kalman_long', '1h', 'LONG', '1'),
    _slot('exp_range', 'range4h_long', '4h', 'LONG', '0.5'),
    _slot('exp_range', 'range4h_short', '4h', 'SHORT', '0.5'),
    _slot('exp_vwaprev', 'vwaprev4h_long', '4h', 'LONG', '0.5'),
    _slot('exp_vwaprev', 'vwaprev4h_short', '4h', 'SHORT', '0.5'),
)


def build_slots(rows=TESTNET_SET_2026_11_05):
    """Returns (slots, {name: refusal code}) - every row either builds exactly or is reported, never substituted."""
    slots, refused = [], {}
    for row in rows:
        if row.get('key') in HOLDOUT_FAILED_2026_11_05:
            refused[row.get('name', '?')] = 'holdout_failed'
            continue
        try:
            slots.append(slot_from_dict(dict(row)))
        except StrategyConfigError as e:
            refused[row.get('name', '?')] = e.code
    return tuple(slots), refused


__all__ = ['CORE_20', 'HOLDOUT_FAILED_2026_11_05', 'TESTNET_SET_2026_11_05', 'build_slots']
