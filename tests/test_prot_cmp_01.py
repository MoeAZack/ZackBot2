"""PROT-CMP-01: the research harness that compares profit-protection variants, and the slot settings it recommends."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))

import app  # noqa: E402
import backtest as BT  # noqa: E402
import prot as PROT  # noqa: E402
import prot_compare as PC  # noqa: E402
import prot_path_check as PPC  # noqa: E402
import strategies as S  # noqa: E402


@pytest.mark.parametrize('key,sides,base', PC.COHORT)
def test_recommended_mgmt_is_a_valid_slot_and_prot_is_the_only_stop_mover(key, sides, base):
    m = PC.recommended_mgmt(key, base)
    if key not in PC.RECOMMENDED:
        assert m is None
        return
    out = app.validate_sleeve(dict(id='T', key=key, share=0.1, risk=0.01, max_pos=1, sides=sides, mgmt=m, tf='4h'), 0)
    eff = S.merge_mgmt(key, out['mgmt'])
    assert eff['prot']['at_r'] == 1.0 and PROT.prot_conflicts(eff) == []
    own = S.merge_mgmt(key, base).get('trail_atr') or None          # the strategy's own trail moves INTO prot, nothing else
    assert eff['prot'].get('trail_atr') == own
    for k, v in base.items():                                      # cohort overrides (tp1, max_bars) are kept
        if k not in PC.OFF: assert eff[k] == v


def test_recommended_set_is_frozen():
    assert PC.RECOMMENDED == ('bear_breakdown', 'ema_st', 'exp_breakout', 'exp_mom_short', 'exp_range', 'exp_trend',
                              'hot_coin', 'pullback_rsi')
    for k in ('donchian_ens', 'ema_mom', 'rotation', 'squeeze_tp', 'breakout_pyramid'):
        assert PC.recommended_mgmt(k) is None


def test_every_variant_runs_through_backtest_refusal_rules():
    for key, sides, base in PC.COHORT:
        V = PC.variants(key, base)
        assert V['BASE'] == base                                   # the reference is the cohort slot exactly as it runs
        for name, m in V.items():
            assert not PROT.prot_conflicts(S.merge_mgmt(key, m)), (key, name)


class _Book:
    def __init__(self, h, l):
        self.arr = {'X': {'h': np.array(h, float), 'l': np.array(l, float)}}


def test_trade_rows_mfe_long_and_short_from_entry_fill():
    book = _Book(h=[10, 12, 15, 11, 9], l=[8, 9, 10, 7, 5])
    tr = pd.DataFrame([dict(sleeve='s', sym='X', side=1, i_in=1, i_out=3, pnl=0, R=-0.5, why='stop'),
                       dict(sleeve='s', sym='X', side=-1, i_in=3, i_out=4, pnl=0, R=1.0, why='tp')])
    fl = [dict(kind='add', sleeve='s', sym='X', pos_i=1, ref=14.0, stop_dist=2.0),    # an add is never the entry price,
          dict(kind='entry', sleeve='s', sym='X', pos_i=1, ref=11.0, stop_dist=2.0),  # whatever the order of the log
          dict(kind='entry', sleeve='s', sym='X', pos_i=3, ref=10.0, stop_dist=2.5)]
    out = PC.trade_rows(book, tr, fl)
    assert out['mfe_R'].tolist() == [pytest.approx((15 - 11) / 2), pytest.approx((10 - 5) / 2.5)]


def test_trade_rows_missing_entry_is_nan_not_zero():
    out = PC.trade_rows(_Book([1, 2], [1, 1]), pd.DataFrame([dict(sleeve='s', sym='X', side=1, i_in=0, i_out=1, pnl=0, R=0,
                                                                   why='x')]), [])
    assert np.isnan(out['mfe_R'].iloc[0])


def test_cost_level_sets_the_all_in_side_cost(monkeypatch):
    monkeypatch.setattr(BT, 'FEE', BT.FEE)
    seen = {}

    def fake_run(*a, **k):
        seen['fee'] = BT.FEE
        return pd.DataFrame(), pd.Series(dtype=float)
    monkeypatch.setattr(BT, 'run', fake_run)
    monkeypatch.setattr(PC, 'books', lambda: {'S': None, 'L': None})
    monkeypatch.setattr(PC, 'periods', lambda: {'A': ('S', None, None)})
    monkeypatch.setattr(PROT, 'prot_level', PROT.prot_level)
    for bp in (7, 15, 23):
        PC.one(('ema_mom', 'both', 'BASE', {}, bp, 'A'))
        assert seen['fee'] + BT.SLIP == pytest.approx(bp / 1e4)
    assert 0.0005 + BT.SLIP == pytest.approx(7 / 1e4)       # 7 bp = the backtest's own default


def test_path_check_modes_never_stack_and_path_restores_the_original(monkeypatch):
    orig = BT.path_points
    key = (100.0, 110.0, 90.0, 105.0)
    sub = [(100.0, 110.0, 99.0, 108.0), (108.0, 108.0, 90.0, 95.0), (95.0, 104.0, 94.0, 101.0), (101.0, 106.0, 100.0, 105.0)]
    monkeypatch.setattr(PPC, '_STATE', {'sub': ({key: sub}, 0)})
    try:
        PPC._install('h1')
        a = BT.path_points(*key, 1)
        PPC._install('h1worst'); PPC._install('h1')                  # re-installing must not wrap the previous patch
        assert BT.path_points(*key, 1) == a
        # 4 hourly candles walked in time order: the 1h path visits the low (90) AFTER the high (110)
        assert a[0] == 100.0 and a[-1] == 105.0 and a.index(110.0) < a.index(90.0)
        assert BT.path_points(1.0, 2.0, 0.5, 1.5, 1) == orig(1.0, 2.0, 0.5, 1.5, 1)   # unknown candle: backtest's rule
        PPC._install('h1worst')
        w = BT.path_points(*key, 1)
        assert len(w) >= len(a) and w[0] == 100.0 and w[-1] == 105.0
        assert w != a                       # green hours walk low-first in h1, high-first (worst for a long) in h1worst
        PPC._install('path')
        assert BT.path_points is orig
    finally:
        BT.path_points = orig


def test_buffer_is_pinned_to_the_live_engine_default_whatever_the_cost_level():
    orig = PROT.prot_level
    try:
        PC._pin_buffer('live')
        PC._pin_buffer('live')                                         # never wraps itself
        live = PROT.prot_level({'at_r': 1.0}, 1, 100.0, 100.0, 2.0, 103.0, 1.0, 0.0021, 0.0002)   # 23 bp passed in
        assert live == pytest.approx(orig({'at_r': 1.0}, 1, 100.0, 100.0, 2.0, 103.0, 1.0, PROT.PROT_FEE, PROT.PROT_SLIP))
        assert live < orig({'at_r': 1.0}, 1, 100.0, 100.0, 2.0, 103.0, 1.0, 0.0021, 0.0002)
        PC._pin_buffer('cost')
        assert PROT.prot_level is orig
    finally:
        PROT.prot_level = orig
