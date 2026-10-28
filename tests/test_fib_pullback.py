"""FIB-01: causal, symmetric Fibonacci pullback research candidate.

Synthetic only.  The candidate is registered for the shared backtester but must remain absent from PAPER rotation until
historical development, walk-forward and untouched-holdout gates independently pass.
"""
import numpy as np
import pandas as pd
import pytest

import engine as E
import strategies as S


KEY = 'fib_pullback'


def long_tape(n=290):
    c = np.linspace(100.0, 196.0, n)
    c[250:260] = [196, 204, 212, 218, 215, 210, 202, 198, 203, 208]
    c[260:] = np.linspace(213, 225, n - 260)
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) + 0.5, np.minimum(o, c) - 0.5
    l[250], h[253] = 178.0, 222.0
    o[258] = 198.0
    t = pd.date_range('2022-01-01', periods=n, freq='4h')
    return pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=np.full(n, 1000.0)))


def mirror(raw, ceiling=320.0):
    d = raw.copy()
    d['o'], d['c'] = ceiling - raw.o, ceiling - raw.c
    d['h'], d['l'] = ceiling - raw.l, ceiling - raw.h
    return d


def signals(raw):
    d = S.indicators(raw)
    ctx = dict(btc_down=np.zeros(len(d), dtype=bool))
    return d, S.signals(KEY, d, ctx, mask_sides=False)


def test_registered_for_research_but_never_paper_rotation():
    assert S.STRATEGIES[KEY]['research_only'] is True and KEY in E.RESEARCH_ONLY_KEYS
    assert KEY not in {k for g in E.breadth_groups() for k in g}
    assert KEY not in {x['key'] for x in E.breadth_sleeves()}


def test_long_and_short_are_mirrored_and_use_0786_structural_stop():
    dl, lg = signals(long_tape())
    ds, sh = signals(mirror(long_tape()))
    il, is_ = np.flatnonzero(lg['le']), np.flatnonzero(sh['se'])
    assert il.tolist() == is_.tolist() == [258]
    assert not lg['se'].any() and not sh['le'].any()
    span = 222.0 - 178.0
    long_invalid, short_invalid = 222.0 - S.FIB['invalid'] * span, (320.0 - 222.0) + S.FIB['invalid'] * span
    assert lg['atr'][258] == pytest.approx(dl.c.iloc[258] - long_invalid)
    assert sh['atr'][258] == pytest.approx(short_invalid - ds.c.iloc[258])
    assert long_invalid < dl.c.iloc[258] and short_invalid > ds.c.iloc[258]


def test_pivot_confirmation_is_delayed_and_prefix_causal():
    raw = long_tape()
    _, full = signals(raw)
    assert not full['le'][:258].any() and full['le'][258]
    for k in (254, 257, 259, len(raw)):
        _, part = signals(raw.iloc[:k].copy())
        for field in ('le', 'se', 'lx', 'sx'):
            np.testing.assert_array_equal(part[field], full[field][:k])
        np.testing.assert_allclose(part['atr'], full['atr'][:k], equal_nan=True)


def test_bad_fibonacci_order_is_rejected():
    d = S.indicators(long_tape())
    ctx = dict(btc_down=np.zeros(len(d), dtype=bool))
    try:
        S.signals(KEY, d, ctx, params=dict(trigger=0.7), mask_sides=False)
    except ValueError as exc:
        assert 'zone_lo < trigger < zone_hi' in str(exc)
    else:
        raise AssertionError('invalid Fibonacci ordering was accepted')
