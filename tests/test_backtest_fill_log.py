"""USDC-PAIR-RUNNER view A (Codex 6245962231): backtest.run(fill_log=...) records every fill before the legacy slippage.
Absent (the default) the run is byte-identical; present, it changes nothing, and the logged fills re-priced with the
legacy constants rebuild every trade's P&L exactly - so no fill path (entry, DCA, pyramid add, partial or full exit,
gap or path stop, liquidation) goes unlogged."""
import numpy as np
import pandas as pd
import pytest

import backtest as BT

SYMS = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT')


def raw(n=1400, seed=3):
    rng = np.random.default_rng(seed)
    t = pd.date_range('2024-01-01', periods=n, freq='4h')
    out = {}
    for j, s in enumerate(SYMS):
        c = 100 * (1 + j) * np.exp(np.cumsum(rng.normal(0, .02, n)))
        o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, .004, n))               # gaps at the open
        wick = np.abs(rng.normal(0, .012, n))
        out[s] = pd.DataFrame({'t': t, 'o': o, 'h': np.maximum(o, c) * (1 + wick), 'l': np.minimum(o, c) * (1 - wick),
                               'c': c, 'v': 1e3, 'qv': 1e6})
    return out


CASES = [
    dict(key='breakout_pyramid', sides='long', mgmt={'pyramid': {'n': 2, 'step_r': 1.0, 'frac': 0.5}, 'tp1_r': 1.5,
                                                      'tp1_frac': 0.4}),
    dict(key='exp_trend', sides='short', mgmt={'tps': [[1.0, 0.3], [2.0, 0.3]]}),
    dict(key='exp_trend', sides='long', mgmt={'max_bars': 12}),
    dict(key='dca_dip', sides='long', mgmt={'dca': {'n': 3, 'step_atr': 1.0, 'scale': 1.5, 'tp_atr': 1.0,
                                                    'stop_atr': 1.0}}),
    dict(key='bear_breakdown', sides='short', mgmt={}),
]


def go(case, fill_log=None, **kw):
    bk = BT.Book(raw())
    cfg = [dict(key=case['key'], share=1.0, risk=0.02, max_pos=3, sides=case['sides'], mgmt=case['mgmt'],
                symbols=list(SYMS))]
    return BT.run(bk, cfg, start=1000.0, max_lev=20.0, daily_halt=1.0, fill_log=fill_log, **kw)


def legacy_pnl(fills, fpb_cost=0.0):
    """Re-price logged fills with the legacy constants: market entries and exits slip SLIP, adds fill at the level."""
    tot = 0.0
    for f in fills:
        sign = 1 if f['action'] == 'buy' else -1
        if f['kind'] == 'entry':
            px = f['ref'] if f['liq'] == 'maker' else f['ref'] * (1 + f['side'] * BT.SLIP)
            fee = BT.FEE_MAKER if f['liq'] == 'maker' else BT.FEE
        elif f['kind'] == 'exit':
            px, fee = f['ref'] * (1 - f['side'] * BT.SLIP), BT.FEE
        else:
            px, fee = f['ref'], BT.FEE
        tot += -sign * px * f['qty'] - px * f['qty'] * fee
    return tot - fpb_cost


@pytest.mark.parametrize('case', CASES, ids=lambda c: f"{c['key']}-{c['sides']}")
def test_the_log_changes_nothing_and_rebuilds_every_trade(case):
    tr0, cv0 = go(case, fund_per_bar=0.0)
    log = []
    tr1, cv1 = go(case, fill_log=log, fund_per_bar=0.0)
    assert tr0.equals(tr1) and cv0.equals(cv1) and cv0.attrs == cv1.attrs
    assert len(tr0) >= 5, 'fixture must trade'
    by_pos = {}
    for f in log:
        by_pos.setdefault((f['sleeve'], f['sym'], f['pos_i']), []).append(f)
    closed = 0
    for r in tr0.itertuples():
        fills = by_pos.pop((r.sleeve, r.sym, r.i_in))
        assert fills[0]['kind'] == 'entry' and fills[-1]['kind'] == 'exit' and fills[-1]['i'] == r.i_out
        qin = sum(f['qty'] for f in fills if f['kind'] != 'exit')
        assert sum(f['qty'] for f in fills if f['kind'] == 'exit') == pytest.approx(qin, rel=1e-12)
        assert legacy_pnl(fills) == pytest.approx(r.pnl, rel=1e-9, abs=1e-9)
        closed += 1
    assert all(sum(f['qty'] for f in v if f['kind'] != 'exit') > sum(f['qty'] for f in v if f['kind'] == 'exit')
               for v in by_pos.values())                                  # only positions still open at the end remain
    assert closed == len(tr0)


def test_the_cases_cover_every_fill_kind():
    kinds, whys, gaps = set(), set(), set()
    for case in CASES:
        log = []
        go(case, fill_log=log, fund_per_bar=0.0)
        kinds |= {f['kind'] for f in log}; whys |= {f['why'] for f in log if f['kind'] == 'exit'}
        gaps |= {(f['why'], f['gap']) for f in log if f['kind'] == 'exit' and f['why'] == 'stop'}
    assert kinds == {'entry', 'add', 'dca', 'exit'}
    assert {'stop', 'tp1', 'tp_ladder', 'time'} <= whys
    assert gaps == {('stop', True), ('stop', False)}


def test_maker_entries_are_logged_at_their_own_price():
    log = []
    tr, _ = go(CASES[2], fill_log=log, fund_per_bar=0.0, entry_order='maker')
    assert {f['liq'] for f in log if f['kind'] == 'entry'} == {'maker', 'taker'}
    pos = {}
    for f in log:
        pos.setdefault((f['sleeve'], f['sym'], f['pos_i']), []).append(f)
    for r in tr.itertuples():
        assert legacy_pnl(pos[(r.sleeve, r.sym, r.i_in)]) == pytest.approx(r.pnl, rel=1e-9, abs=1e-9)


def test_the_default_run_is_unchanged_with_funding_on():
    tr0, cv0 = go(CASES[0])
    tr1, cv1 = go(CASES[0], fill_log=[])
    assert tr0.equals(tr1) and cv0.equals(cv1)
