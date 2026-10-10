"""newcore.strategy.indicators: known values, causality, and parity with legacy strategies.indicators (pandas)."""
import math

import pytest

from newcore.strategy import indicators as ind
from ncs_helpers import load, synthetic


def close(a, b, rel=1e-12):
    return all((x is None and y is None) or math.isclose(x, y, rel_tol=rel, abs_tol=0) for x, y in zip(a, b, strict=True))


def test_ema_known_values():
    # a = 2/(3+1) = 0.5, seeded at the first value
    assert close(ind.ema([1, 2, 3, 4, 5], 3), [1.0, 1.5, 2.25, 3.125, 4.0625])
    # n = 1 is the identity
    assert ind.ema([3.0, 7.0, 1.0], 1) == (3.0, 7.0, 1.0)
    # a constant series stays constant
    assert ind.ema([5.0] * 50, 20) == (5.0,) * 50
    with pytest.raises(ValueError):
        ind.ema([1.0], 0)


def test_true_range_and_wilder_atr_known_values():
    h, l, c = [10, 12, 11, 15], [8, 9, 7, 13], [9, 11, 8, 14]
    # TR0 = h0-l0 (no previous close); TR1 = max(3, |12-9|, |9-9|) = 3; TR2 = max(4, 0, |7-11|) = 4; TR3 = max(2, 7, 5) = 7
    assert ind.true_range(h, l, c) == (2.0, 3.0, 4.0, 7.0)
    # Wilder n=2: alpha 1/2 -> 2, 2.5, 3.25, 5.125
    assert close(ind.wilder_atr(h, l, c, 2), [2.0, 2.5, 3.25, 5.125])
    # n=14: alpha 1/14
    a = 1 / 14
    exp = [2.0]
    for tr in (3.0, 4.0, 7.0):
        exp.append((1 - a) * exp[-1] + a * tr)
    assert close(ind.wilder_atr(h, l, c, 14), exp)


def test_pct_return_and_cross_up_known_values():
    r = ind.pct_return([100.0, 110.0, 121.0, 60.5], 1)
    assert r[0] is None and close(r[1:], [0.1, 0.1, -0.5])
    r2 = ind.pct_return([100.0, 110.0, 121.0, 60.5], 2)
    assert r2[:2] == (None, None) and close(r2[2:], [0.21, -0.45])
    a = [1.0, 2.0, 3.0, 2.0, 2.0, 4.0]
    b = [2.0, 2.0, 2.0, 2.0, 3.0, 3.0]
    # bar 2: 3 > 2 and prev 2 <= 2 -> cross; bar 5: 4 > 3 and prev 2 <= 3 -> cross; equality is not "above"
    assert ind.cross_up(a, b) == (False, False, True, False, False, True)
    assert ind.cross_up([None, 3.0], [2.0, 2.0]) == (False, False)


def test_indicators_are_causal_bit_for_bit():
    b = synthetic(1500)
    full = (ind.ema(b.c, 20), ind.ema(b.c, 200), ind.wilder_atr(b.h, b.l, b.c, 14), ind.pct_return(b.c, 180))
    for n in (1, 2, 199, 200, 777, 1499):
        cut = (ind.ema(b.c[:n], 20), ind.ema(b.c[:n], 200), ind.wilder_atr(b.h[:n], b.l[:n], b.c[:n], 14),
               ind.pct_return(b.c[:n], 180))
        for f, p in zip(full, cut):
            assert f[:n] == p


def test_parity_with_legacy_pandas_indicators():
    pd = pytest.importorskip('pandas')
    import strategies as S
    b = load('BTCUSDT')
    df = pd.DataFrame(dict(t=pd.to_datetime(b.t_ms, unit='ms'), o=b.o, h=b.h, l=b.l, c=b.c, v=[1.0] * len(b)))
    leg = S.indicators(df)
    for n in (20, 50, 200):
        assert close(ind.ema(b.c, n), leg[f'e{n}'].tolist(), rel=1e-9)
    assert close(ind.wilder_atr(b.h, b.l, b.c, 14), leg['atr'].tolist(), rel=1e-9)
    for k, col in ((42, 'ret42'), (180, 'ret180')):
        mine = ind.pct_return(b.c, k)
        theirs = [None if math.isnan(x) else x for x in leg[col].tolist()]
        assert close(mine, theirs, rel=1e-12)
