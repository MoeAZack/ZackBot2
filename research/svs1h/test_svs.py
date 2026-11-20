"""Synthetic tests for SVS-1H signal rules (no market data, no outcomes)."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import svs_signals as V
from spb_signals_6f16d49a import decision

N = 24 * 75


def base(seed=1):
    rng = np.random.default_rng(seed)
    ix = pd.date_range('2025-01-01', periods=N, freq='h')
    sq = 6e5 * (1 + 0.05 * rng.standard_normal(N)); fq = 1.4e6 * (1 + 0.05 * rng.standard_normal(N))
    spot = pd.DataFrame(dict(c=100.0, qv=sq), index=ix)
    fut = pd.DataFrame(dict(o=100.0, h=100.5, l=99.5, c=100.0, qv=fq), index=ix)
    return spot, fut, pd.Series(1.0, index=ix)


def event(spot, fut, k, up=True, share=0.6, mult=3.0):
    Q = mult * 2e6; spot.iloc[k, 1] = share * Q; fut.iloc[k, 4] = (1 - share) * Q
    fut.iloc[k, fut.columns.get_loc('c')] = 101.5 if up else 98.5
    fut.iloc[k, fut.columns.get_loc('h')] = 101.6; fut.iloc[k, fut.columns.get_loc('l')] = 98.4


def test_direction_mapping_continue_and_fade():
    for up, share, want in ((True, .6, 1), (False, .6, -1), (True, .05, -1), (False, .05, 1)):
        s, f, a = base(); k = N - 10; event(s, f, k, up, share)
        sg, z, sh, Q = V.signal(s, f, a)
        assert sg.iloc[k] == want, (up, share, sg.iloc[k], z.iloc[k])


def test_level_rule_every_qualifying_bar():
    s, f, a = base(); k = N - 10; event(s, f, k); event(s, f, k + 1)
    sg = V.signal(s, f, a)[0]
    assert sg.iloc[k] == 1 and sg.iloc[k + 1] == 1


def test_prior_excludes_current_and_requires_168():
    s, f, a = base(); k = N - 10; event(s, f, k)
    sg, z, sh, Q = V.signal(s, f, a)
    prior = sh.iloc[k - 168:k]
    assert abs(z.iloc[k] - (sh.iloc[k] - prior.mean()) / prior.std(ddof=1)) < 1e-9
    s.iloc[k - 100, 0] = np.nan  # one missing paired hour inside the prior window -> no z
    assert not np.isfinite(V.signal(s, f, a)[1].iloc[k])


def test_volume_body_atr_and_doji_filters():
    s, f, a = base(); k = N - 10; event(s, f, k, mult=1.5)            # Q below 2x prior median
    assert V.signal(s, f, a)[0].iloc[k] == 0
    s, f, a = base(); event(s, f, k); a.iloc[k] = 2.0                   # body 1.5 < ATR 2
    assert V.signal(s, f, a)[0].iloc[k] == 0
    s, f, a = base(); event(s, f, k); a.iloc[k] = 0.0
    assert V.signal(s, f, a)[0].iloc[k] == 0


def test_zero_std_skips():
    ix = pd.date_range('2025-01-01', periods=N, freq='h')
    s = pd.DataFrame(dict(c=100.0, qv=6e5), index=ix); f = pd.DataFrame(dict(o=100.0, h=100.5, l=99.5, c=100.0, qv=1.4e6), index=ix)
    event(s, f, N - 10)
    assert V.signal(s, f, pd.Series(1.0, index=ix))[0].iloc[N - 10] == 0


def test_eligibility_gap_fails_closed():
    s, f, a = base(); k = N - 10; event(s, f, k)
    f.iloc[k - 300, f.columns.get_loc('qv')] = np.nan   # breaks 720 consecutive paired hours
    assert V.signal(s, f, a)[0].iloc[k] == 0



def test_futures_liquidity_required_separately():
    s, f, a = base(); k = N - 10; event(s, f, k)
    f['qv'] = 3e5; event(s, f, k)                      # futures daily qv 7.2m < 10m while spot stays eligible
    assert V.signal(s, f, a)[0].iloc[k] == 0


def test_decision_t_plus_2_and_signal_atr():
    t = np.array(pd.date_range('2025-01-01', periods=10, freq='h').values); atr = np.arange(10, dtype=float) + 1
    d = decision(dict(t=t, atr=atr), pd.Timestamp(t[4]), 1)
    assert pd.Timestamp(d[0]) == pd.Timestamp(t[6]) and d[2] == 5 and d[3] == 3 * atr[4]
    assert decision(dict(t=np.delete(t, 5), atr=np.delete(atr, 5)), pd.Timestamp(t[4]), 1) is None


if __name__ == '__main__':
    for n, f in list(globals().items()):
        if n.startswith('test_'): f(); print('PASS', n)
