"""Signal parity: NEWCORE ema_mom v1 vs legacy strategies.sig_ema_mom on data_long 4h, core 8.

On 4h the days-based lookbacks equal legacy's 42 / 180 bars, so every le / se / lx / sx must agree from the warmup
(220, = backtest.run's warmup) on. Before the warmup NEWCORE emits nothing by design (legacy lx is True on NaN returns)."""
import pytest

import newcore.strategy as S
from ncs_helpers import CORE8, as_of, load

pd = pytest.importorskip('pandas')


@pytest.mark.parametrize('sym', CORE8)
def test_signals_equal_legacy_on_4h(sym):
    import strategies as L
    b = load(sym, '4h')
    df = pd.DataFrame(dict(t=pd.to_datetime(b.t_ms, unit='ms'), o=b.o, h=b.h, l=b.l, c=b.c, v=[1.0] * len(b)))
    d = L.indicators(df)
    leg = {k: v.fillna(False).astype(bool).tolist() for k, v in L.sig_ema_mom(d, {}, {}).items()}
    ev = S.evaluate(b, S.Params(enable_short=True), as_of_ms=as_of(b))
    for k in ('le', 'se', 'lx', 'sx'):
        mine = getattr(ev, k)
        diff = [i for i in range(ev.start, len(b)) if mine[i] != leg[k][i]]
        assert not diff, f'{sym} {k}: first differing bars {diff[:5]}'
    assert sum(ev.le) > 5
