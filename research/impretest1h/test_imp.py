"""Synthetic tests for the IMP-RETEST-1H state machine (no market data, no outcomes)."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imp_signals as I

N = 60


def frame():
    ix = pd.date_range('2025-01-01', periods=N, freq='h')
    d = pd.DataFrame(dict(o=100.0, h=100.3, l=99.7, c=100.0, v=1000.0), index=ix)
    return d, pd.Series(1.0, index=ix)


def bar(d, k, o, h, l, c, v=1000.0):
    d.iloc[k] = [o, h, l, c, v]


def bull_impulse(d, k):  # body 2 (ATR 1), close at high, volume 3x
    bar(d, k, 100, 102.1, 99.9, 102, 3000)


def bear_impulse(d, k):
    bar(d, k, 100, 100.1, 97.9, 98, 3000)


def ev(d, a):
    g, ok = I.grid(d, a); return I.run_state(g, ok)


def trig(e): return [(x['i'], x['j'], x['side']) for x in e if x['kind'] == 'trigger']


def test_bull_trigger_and_mirrored_bear():
    d, a = frame(); bull_impulse(d, 30); bar(d, 31, 101.5, 101.8, 100.9, 101.2)     # low<=M=101, close>=M, close>open? no
    bar(d, 32, 100.95, 101.5, 100.9, 101.3)                                          # low<=M, close>=M, close>open -> trigger
    assert trig(ev(d, a)) == [(30, 32, 1)]
    d, a = frame(); bear_impulse(d, 30); bar(d, 31, 99.05, 99.1, 98.5, 98.7)
    assert trig(ev(d, a)) == [(30, 31, -1)]


def test_cancel_precedes_trigger_and_equality_does_not_cancel():
    d, a = frame(); bull_impulse(d, 30); bar(d, 31, 99.5, 101.2, 99.4, 99.9)          # close<O=100 and would-be... cancel first
    e = ev(d, a); assert e[0]['kind'] == 'cancel' and trig(e) == []
    d, a = frame(); bull_impulse(d, 30); bar(d, 31, 99.9, 101.2, 99.8, 100.0)         # close == O: no cancel, low<=M? 99.8<=101 but close 100<M -> nothing
    bar(d, 32, 100.9, 101.4, 100.8, 101.1)
    assert trig(ev(d, a)) == [(30, 32, 1)]


def test_window_i12_accepted_i13_refused():
    d, a = frame(); bull_impulse(d, 30)
    for k in range(31, 43): bar(d, k, 101.6, 101.9, 101.5, 101.7)                    # above M, no trigger
    bar(d, 42, 100.95, 101.5, 100.9, 101.3)                                          # i+12 trigger
    assert trig(ev(d, a)) == [(30, 42, 1)]
    d, a = frame(); bull_impulse(d, 30)
    for k in range(31, 44): bar(d, k, 101.6, 101.9, 101.5, 101.7)
    bar(d, 43, 100.95, 101.5, 100.9, 101.3)                                          # i+13: expired
    e = ev(d, a); assert trig(e) == [] and any(x['kind'] == 'expire' and x['j'] == 42 for x in e)


def test_oldest_setup_kept_newer_ignored():
    d, a = frame(); bull_impulse(d, 30); bar(d, 31, 101.6, 101.9, 101.5, 101.7); bear_impulse(d, 32)
    bar(d, 32, 101.7, 101.8, 99.6, 99.7, 3000)   # bearish impulse-like bar; also closes < O=100 -> cancels the bull setup
    e = ev(d, a); assert e[0]['kind'] == 'cancel' and e[0]['j'] == 32
    assert all(x['i'] != 32 for x in e)          # resolving bar may not start a new setup
    d, a = frame(); bull_impulse(d, 30); bull_impulse(d, 31)                          # newer impulse ignored while pending
    bar(d, 31, 101.6, 104, 101.5, 103.9, 3000)
    bar(d, 32, 100.95, 101.5, 100.9, 101.3)
    assert trig(ev(d, a)) == [(30, 32, 1)]


def test_gap_invalidates_and_never_counts_as_bar():
    d, a = frame(); bull_impulse(d, 30); d = d.drop(d.index[31]); a = a.drop(a.index[31])
    bar(d, 31, 100.95, 101.5, 100.9, 101.3)      # first bar after the hole would have triggered
    e = ev(d, a); assert trig(e) == [] and e[0]['kind'] == 'invalid'


def test_no_trigger_on_impulse_bar_and_atr_and_volume_bounds():
    d, a = frame(); bar(d, 30, 100, 104, 99.9, 103.9, 3000); a.iloc[30] = 1.0          # body 3.9 > 3*ATR -> not impulse
    bar(d, 31, 101.9, 102.5, 101.8, 102.2)
    assert ev(d, a) == []
    d, a = frame(); bar(d, 30, 100, 102.1, 99.9, 102, 1900)                            # volume < 2x median
    assert ev(d, a) == []
    d, a = frame(); bar(d, 30, 100, 102.6, 99.9, 102, 3000)                            # close not in outer 20%
    assert ev(d, a) == []



def test_volume_median_excludes_impulse_bar():
    d, a = frame(); d.iloc[6:30, 4] = [1000.0] * 12 + [1600.0] * 12      # prior-24 median 1300
    bar(d, 30, 100, 102.1, 99.9, 102, 2700)                              # 2700 >= 2*1300 only if bar 30 is excluded
    bar(d, 31, 100.95, 101.5, 100.9, 101.3)
    assert trig(ev(d, a)) == [(30, 31, 1)]


def test_trigger_consumes_then_new_setup_from_next_bar():
    d, a = frame(); bull_impulse(d, 30); bar(d, 31, 100.95, 101.5, 100.9, 101.3); bull_impulse(d, 32)
    bar(d, 33, 100.95, 101.5, 100.9, 101.3)
    assert trig(ev(d, a)) == [(30, 31, 1), (32, 33, 1)]


def test_no_future_data_used():
    d, a = frame(); bull_impulse(d, 30); bar(d, 32, 100.95, 101.5, 100.9, 101.3)
    full = trig(ev(d, a)); cut = trig(ev(d.iloc[:33], a.iloc[:33]))
    assert full == cut == [(30, 32, 1)]



def test_decision_uses_trigger_atr_next_open_and_ineligible_consumes():
    import imp_run as RN
    d, a = frame(); bull_impulse(d, 30); bar(d, 32, 100.95, 101.5, 100.9, 101.3); bull_impulse(d, 40); bar(d, 41, 100.95, 101.5, 100.9, 101.3)
    atr = np.arange(N, dtype=float) / 10 + 1; atr[30] = atr[40] = 1.0
    elig = np.ones(N, bool); elig[41] = False
    A = dict(t=d.index.values, atr=atr, elig=elig)
    g, ok = I.grid(d, atr); dec, rows, kinds = RN.decisions(A, g, ok, d.index[0], d.index[-1])
    assert dec == [(d.index.values[33], 1, 32, 3 * atr[32])]               # fill j+1, stop 3*ATR_j (not impulse ATR)
    assert rows[1]['refused'] == 'ineligible' and kinds['trigger'] == 2    # ineligible trigger still consumed


if __name__ == '__main__':
    for n, f in list(globals().items()):
        if n.startswith('test_'): f(); print('PASS', n)
