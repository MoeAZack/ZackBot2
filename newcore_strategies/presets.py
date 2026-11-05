"""STRAT-01 presets.  TESTNET_SET_2026_11_05 is the shortlist in reports/testnet-strategy-pick-2026-11-05.md.

Risk multipliers are the 3 Nov 2026 drawdown-policy caps (``min(1, 15 / dev max DD R)``), never raised:
C24 18.60R -> 0.806; C213 32.369926R -> 0.4634; C164 18.784330R -> 0.7985; C101 9.689494R -> 1.0.
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


TESTNET_SET_2026_11_05 = (
    _slot('rotation_long_h2', 'c24_rotation_long', '4h', 'LONG', '0.806'),
    _slot('evening_star_short_1h', 'c213_evening_star_short', '1h', 'SHORT', '0.4634'),
    _slot('starc_band_breakout_long_1h', 'c164_starc_long', '1h', 'LONG', '0.7985'),
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
        try:
            slots.append(slot_from_dict(dict(row)))
        except StrategyConfigError as e:
            refused[row.get('name', '?')] = e.code
    return tuple(slots), refused


__all__ = ['CORE_20', 'TESTNET_SET_2026_11_05', 'build_slots']
