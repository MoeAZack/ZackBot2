"""REVIEW-ONLY structure features: causal / non-repainting (appending future candles never changes past values)."""
import os, sys
import numpy as np, pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'research'))
import structure_features as SF                         # noqa: E402


def tape(n=600, seed=1):
    r = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(r.normal(0, 0.01, n) + 0.002 * np.sin(np.arange(n) / 25)))
    o = np.r_[c[0], c[:-1]]
    w = np.abs(r.normal(0, 0.006, n)) * c
    return pd.DataFrame(dict(o=o, h=np.maximum(o, c) + w, l=np.minimum(o, c) - w, c=c))


def same(a, b):
    num = [k for k in a if k != 'event']
    return np.allclose(a[num].to_numpy(float), b[num].to_numpy(float), equal_nan=True) and (a['event'] == b['event']).all()


def test_appending_future_bars_never_changes_past_values():
    d = tape()
    full = SF.structure(d)
    for cut in (50, 137, 301, 450, 599):
        assert same(SF.structure(d.iloc[:cut]), full.iloc[:cut])
    assert {'BOS', 'MSS'} <= set(full['event'])                 # the tape exercises both kinds of break


def test_swing_is_invisible_until_n_bars_after_the_pivot():
    h = [1, 2, 3, 10, 4, 3, 2, 1, 1, 1]
    d = pd.DataFrame(dict(h=h, l=[x - 0.5 for x in h], c=[x - 0.2 for x in h]))
    s = SF.structure(d)
    assert np.isnan(s['sh'].iloc[5]) and s['sh'].iloc[6] == 10      # pivot at bar 3, confirmed at bar 3 + N = 6
