"""Synthetic tests for PFX-1H (no market data, no outcomes)."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pfx_signals as P

N = 400


def frames(seed=3):
    rng = np.random.default_rng(seed); ix = pd.date_range('2025-01-01', periods=N, freq='h')
    lo = -0.0005 + 0.0001 * rng.standard_normal(N); hi = 0.0005 + 0.0001 * rng.standard_normal(N)
    prem = pd.DataFrame(dict(o=0.0, h=hi, l=lo, c=0.0), index=ix)
    fut = pd.DataFrame(dict(o=100.0, h=100.5, l=99.5, c=100.0), index=ix)
    return prem, fut, pd.Series(1.0, index=ix)


def flush(prem, fut, k, down=True, depth=0.01, move=1.5):
    if down: prem.iloc[k, 2] = -depth; fut.iloc[k] = [100, 100.1, 100 - move - .1, 100 - move]
    else: prem.iloc[k, 1] = depth; fut.iloc[k] = [100, 100 + move + .1, 99.9, 100 + move]


def test_exact_raw_mad_and_current_excluded():
    prem, fut, a = frames(); k = 300; flush(prem, fut, k)
    side, d = P.signal(prem, fut, a)
    w = prem.l.iloc[k - 168:k].to_numpy(); m = np.median(w); mad = np.median(np.abs(w - m))
    assert abs(d['mL'].iloc[k] - m) < 1e-15 and abs(d['madL'].iloc[k] - mad) < 1e-15
    assert side.iloc[k] == 1


def test_symmetric_directions_and_price_units():
    prem, fut, a = frames(); flush(prem, fut, 300, down=False)
    assert P.signal(prem, fut, a)[0].iloc[300] == -1
    prem, fut, a = frames(); flush(prem, fut, 300, move=0.9)           # displacement 0.9 < ATR 1.0 (price units)
    assert P.signal(prem, fut, a)[0].iloc[300] == 0
    prem, fut, a = frames(); flush(prem, fut, 300, depth=0.0006)       # premium not extreme enough (threshold ~ -0.00077)
    assert P.signal(prem, fut, a)[0].iloc[300] == 0



def test_threshold_is_four_raw_mads():
    prem, fut, a = frames(); k = 300; flush(prem, fut, k)
    d = P.signal(prem, fut, a)[1]; m, mad = d['mL'].iloc[k], d['madL'].iloc[k]
    prem.iloc[k, 2] = m - 3 * mad; assert P.signal(prem, fut, a)[0].iloc[k] == 0
    prem.iloc[k, 2] = m - 4 * mad - 1e-12; assert P.signal(prem, fut, a)[0].iloc[k] == 1


def test_level_rule_consecutive_bars():
    prem, fut, a = frames(); flush(prem, fut, 300); flush(prem, fut, 301)
    s = P.signal(prem, fut, a)[0]; assert s.iloc[300] == 1 and s.iloc[301] == 1


def test_contiguous_168_history_and_gap():
    prem, fut, a = frames(); flush(prem, fut, 300); prem.iloc[200, 2] = np.nan
    assert P.signal(prem, fut, a)[0].iloc[300] == 0                      # gap inside prior window
    prem, fut, a = frames(); flush(prem, fut, 160)
    assert P.signal(prem, fut, a)[0].iloc[160] == 0                      # cold start (<168 prior)


def test_mad_zero_skips_and_signed_zero_premium():
    prem, fut, a = frames(); prem['l'] = 0.0; prem['h'] = 0.0; prem['o'] = 0.0; prem['c'] = 0.0
    flush(prem, fut, 300)
    assert P.signal(prem, fut, a)[0].iloc[300] == 0                      # MAD 0 -> skip
    prem, fut, a = frames(); prem['h'] = -0.0001; prem['o'] = prem['c'] = -0.0002; prem['l'] = prem['l'] - 0.0002
    flush(prem, fut, 300); prem.iloc[300, 0] = prem.iloc[300, 3] = -0.0002; prem.iloc[300, 1] = -0.0001
    assert P.signal(prem, fut, a)[0].iloc[300] == 1                      # all-negative premiums are valid


def test_malformed_ohlc_and_bad_atr():
    prem, fut, a = frames(); flush(prem, fut, 300); prem.iloc[300, 1] = -0.02   # high < low
    assert P.signal(prem, fut, a)[0].iloc[300] == 0
    prem, fut, a = frames(); flush(prem, fut, 300); fut.iloc[300, 1] = 50.0      # futures high < open
    assert P.signal(prem, fut, a)[0].iloc[300] == 0
    prem, fut, a = frames(); flush(prem, fut, 300); a.iloc[300] = 0.0
    assert P.signal(prem, fut, a)[0].iloc[300] == 0


def test_no_future_data_used():
    prem, fut, a = frames(); flush(prem, fut, 300)
    full = P.signal(prem, fut, a)[0].iloc[:301]; cut = P.signal(prem.iloc[:301], fut.iloc[:301], a.iloc[:301])[0]
    assert (full == cut).all()


def test_runner_next_open_original_atr_and_count_field_unused():
    import inspect, pfx_run
    src = inspect.getsource(pfx_run.main)
    assert "dec.append((a['t'][k + 1], int(sd), k, dist))" in src and "dist = 3 * float(a['atr'][k])" in src
    assert 'p[8]' not in inspect.getsource(pfx_run.load_premium)


if __name__ == '__main__':
    for n, f in list(globals().items()):
        if n.startswith('test_'): f(); print('PASS', n)
