"""NEWSTRAT-CP N6 / N8: registry definitions match the frozen research definitions, are causal, and stay research-only."""
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


@pytest.mark.parametrize('live,frozen', PAIRS)
def test_signals_match_frozen_research_definition(book, live, frozen):
    for s in SYMS[1:]:
        a, b = book.raw_sig(live, s), book.raw_sig(frozen, s)
        for k in ('le', 'se', 'lx', 'sx'):
            assert np.array_equal(a[k], b[k]), (live, s, k)
    assert sum(int(book.raw_sig(live, s)['le'].sum() + book.raw_sig(live, s)['se'].sum()) for s in SYMS[1:]) > 0


@pytest.mark.parametrize('live,frozen', PAIRS)
def test_backtest_trades_match_frozen(book, live, frozen):
    syms = SYMS[1:]
    a, _ = BT.run(book, [dict(key=live, share=1.0, risk=0.004, max_pos=2, symbols=syms)], start=4000)
    b, _ = BT.run(book, [dict(key=frozen, share=1.0, risk=0.004, max_pos=2, symbols=syms)], start=4000)
    assert len(a) > 0
    pd.testing.assert_frame_equal(a.drop(columns='sleeve'), b.drop(columns='sleeve'))


@pytest.mark.parametrize('live', [p[0] for p in PAIRS])
def test_causal_truncation_does_not_change_past_signals(live):
    full = BT.Book(_raw(1500)); cut = BT.Book({s: d.iloc[:1200] for s, d in _raw(1500).items()})
    for s in SYMS[1:]:
        a, b = full.raw_sig(live, s), cut.raw_sig(live, s)
        for k in ('le', 'se'):
            assert np.array_equal(a[k][:1200], b[k]), (live, s, k)


def test_research_only_and_sides():
    assert S.STRATEGIES['btc_lead_catchup_4h']['research_only'] and S.STRATEGIES['btc_lead_catchup_4h']['sides'] == 'both'
    assert S.STRATEGIES['squeeze_breakdown_short_4h']['research_only'] and S.STRATEGIES['squeeze_breakdown_short_4h']['sides'] == 'short'


def test_btc_lead_without_context_never_signals():
    d = S.indicators(_raw(600)['ETHUSDT'])
    g = S.signals('btc_lead_catchup_4h', d, dict(btc_down=np.zeros(len(d), bool)), mask_sides=False)
    assert not g['le'].any() and not g['se'].any()
