"""NEWSTRAT-CP N6 / N8 (research module only; strategies.py stays byte-pinned): frozen definitions are causal and research-only."""
import os, sys
import numpy as np, pandas as pd, pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, 'research'))
import backtest as BT, strategies as S
import newstrat_cp2 as N2

SYMS = ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'SOLUSDT', 'DOGEUSDT', 'BNBUSDT', 'LINKUSDT', 'AVAXUSDT']
PAIRS = [('btc_lead_catchup_4h', 'cp_n6_btc_lead_catchup_4h'), ('squeeze_breakdown_short_4h', 'cp_n8_squeeze_breakdown_short_4h')]


def _raw(n=2500):
    return {s: pd.read_csv(os.path.join(ROOT, 'data_long', '4h', f'{s}_4h.csv'), parse_dates=['t']).tail(n).reset_index(drop=True)
            for s in SYMS}


@pytest.fixture(scope='module')
def book():
    raw = _raw(); N2.register(); N2.set_btc(raw['BTCUSDT'])
    return BT.Book(raw)


@pytest.mark.parametrize('live', [p[1] for p in PAIRS])
def test_causal_truncation_does_not_change_past_signals(live):
    raw = _raw(1500); N2.register(); N2.set_btc(raw['BTCUSDT'])
    full = BT.Book(raw); N2.set_btc(raw['BTCUSDT'].iloc[:1200]); cut = BT.Book({s: d.iloc[:1200] for s, d in _raw(1500).items()})
    for s in SYMS[1:]:
        a, b = full.raw_sig(live, s), cut.raw_sig(live, s)
        for k in ('le', 'se'):
            assert np.array_equal(a[k][:1200], b[k]), (live, s, k)


def test_research_only_and_sides():
    N2.register()
    assert S.STRATEGIES['cp_n6_btc_lead_catchup_4h']['research_only'] and S.STRATEGIES['cp_n6_btc_lead_catchup_4h']['sides'] == 'both'
    assert S.STRATEGIES['cp_n8_squeeze_breakdown_short_4h']['research_only'] and S.STRATEGIES['cp_n8_squeeze_breakdown_short_4h']['sides'] == 'short'


def test_not_in_shipped_registry():
    src = open(os.path.join(ROOT, 'strategies.py'), encoding='utf-8').read()
    assert 'cp_n' not in src and 'btc_lead_catchup' not in src and 'squeeze_breakdown_short' not in src
