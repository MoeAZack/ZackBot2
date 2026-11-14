"""Synthetic tests for the admission-aware controls (no market data, no outcomes)."""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import controls as C
K = C.K
import strategies as S

H1 = pd.Timedelta(hours=1)
T0 = pd.Timestamp('2025-03-01 00:00')
RULES = {s: dict(step=0.001, min_qty=0.001, min_notional=5.0) for s in ('BTCUSDT', 'ETHUSDT', 'XRPUSDT')}


def book(n=400, seed=2):
    rng = np.random.default_rng(seed); t = pd.date_range(T0 - 300 * H1, periods=n, freq='h'); out = {}
    for s in ('BTCUSDT', 'ETHUSDT', 'XRPUSDT'):
        c = 100 * np.exp(np.cumsum(rng.normal(0, .003, n))); o = np.r_[c[0], c[:-1]]
        out[s] = pd.DataFrame(dict(t=t, o=o, h=np.maximum(o, c) * 1.002, l=np.minimum(o, c) * .998, c=c, v=1.0, qv=100.0))
    return out


def strat(bars):
    """Long signal on the same bars for every coin; exits by a 5-bar time stop."""
    def fn(d, p, ctx):
        le = pd.Series(False, index=d.index); le.iloc[bars] = True
        f = pd.Series(False, index=d.index)
        return dict(le=le, se=f, lx=f.copy(), sx=f.copy())
    return dict(name='t', desc='t', style='t', fn=fn, sides='long', tf='1h', params={}, mgmt=dict(stop_atr=50.0, max_bars=5))


def run(order, cap=1, bars=(310, 312, 320), guards=True):
    S.STRATEGIES['zz_ctl'] = strat(list(bars))
    old = (K.START, K.END); K.START, K.END = T0, T0 + 99 * H1
    try:
        raw = {s: d[d.t < K.END].reset_index(drop=True) for s, d in book().items()}
        return C.native_capacity_run(raw, ('EMA', 'zz_ctl', 'long', '1h', cap), 15, 'path', RULES, order, guards=guards)
    finally:
        K.START, K.END = old; S.STRATEGIES.pop('zz_ctl', None)


def test_engine_uses_symbol_priority_list():
    a, _ = run(['BTCUSDT', 'ETHUSDT', 'XRPUSDT'])
    b, _ = run(['XRPUSDT', 'ETHUSDT', 'BTCUSDT'])
    assert a[0]['sym'] == 'BTCUSDT' and b[0]['sym'] == 'XRPUSDT' and a[0]['t_in'] == b[0]['t_in']


def test_actual_capacity_no_phantom_masking():
    lots, _ = run(['BTCUSDT', 'ETHUSDT', 'XRPUSDT'], cap=1)
    # signal bar 310 -> BTC admitted (cap 1, ETH/XRP refused, no position created); bar 312 signal ignored while BTC
    # is held; after BTC's 5-bar exit, bar 320 signal is taken on BTC again (first in priority) - nothing masked it
    assert [l['sym'] for l in lots] == ['BTCUSDT', 'BTCUSDT']
    assert all(sum(1 for m in lots if m['t_in'] <= l['t_in'] < m['t_out']) == 1 for l in lots)
    lots2, _ = run(['BTCUSDT', 'ETHUSDT', 'XRPUSDT'], cap=2)
    assert sorted(l['sym'] for l in lots2 if l['t_in'] == lots[0]['t_in']) == ['BTCUSDT', 'ETHUSDT']


def test_fixed_sizing_and_identity_in_capacity_run():
    lots, cv = run(['BTCUSDT', 'ETHUSDT', 'XRPUSDT'], cap=2)
    for l in lots:
        assert l['risk_usd'] <= K.RISK_USD + 1e-9 and l['qty'] * l['e_fill'] <= K.CAP_NOTIONAL + 1e-9
    assert any(l['closed'] for l in lots)                     # build_lots ran the native identity check


def test_guard_rerun_and_nonparity_report():
    g, cv = run(['BTCUSDT', 'ETHUSDT', 'XRPUSDT'], cap=2, guards=True)
    u, _ = run(['BTCUSDT', 'ETHUSDT', 'XRPUSDT'], cap=2, guards=False)
    old = (K.START, K.END); K.START, K.END = T0, T0 + 99 * H1
    try:
        closes = {s: dict(zip(d.t, d.c)) for s, d in book().items()}
        closes.update({s: closes['BTCUSDT'] for s in K.COINS if s not in closes})
        rep, recs = C.case_report('EMA', g, cv, 15, closes, [T0 + k * H1 for k in range(99)], u)
    finally:
        K.START, K.END = old
    assert rep['nonparity_refusals'] == 0 and rep['guard_changed_entries'] == 0 and rep['admitted'] == len(g)
    # a lot the target replay would refuse (own-sleeve cap) is reported as NONPARITY, not dropped silently
    dup = dict(g[0], id=g[0]['id'] + '#dup', sym='ETHUSDT')
    old = (K.START, K.END); K.START, K.END = T0, T0 + 99 * H1
    try:
        rep2, _ = C.case_report('EMA', g + [dict(dup, sym='XRPUSDT')] + [dict(dup, sym='XRPUSDT', id=dup['id'] + '2')], cv,
                                15, closes, [T0 + k * H1 for k in range(99)], u)
    finally:
        K.START, K.END = old
    assert rep2['nonparity_refusals'] >= 1


def test_native_daily_halt_guard_is_applied_and_reported():
    t = pd.date_range(T0 - 300 * H1, periods=400, freq='h'); raw = {}
    for s, px in (('BTCUSDT', 100.0), ('ETHUSDT', 200.0), ('XRPUSDT', 300.0)):
        c = np.full(400, px)
        c[312:] *= 0.95 if s != 'XRPUSDT' else 1.0                 # BTC/ETH gap 5% down at bar 312
        o = np.r_[c[0], c[:-1]]; o[312] = c[312]                    # true gap: opens below the stop
        raw[s] = pd.DataFrame(dict(t=t, o=o, h=np.maximum(o, c) * 1.001, l=np.minimum(o, c) * .999, c=c, v=1.0, qv=100.0))
    def fn(d, p, ctx):
        le = pd.Series(False, index=d.index); lvl = d.c.iloc[0]
        if lvl < 250: le.iloc[310] = True                          # BTC/ETH enter at 311
        else: le.iloc[314] = True                                  # XRP signal after the loss, same Cairo day
        f = pd.Series(False, index=d.index)
        return dict(le=le, se=f, lx=f.copy(), sx=f.copy())
    S.STRATEGIES['zz_halt'] = dict(name='t', desc='t', style='t', fn=fn, sides='long', tf='1h', params={},
                                   mgmt=dict(stop_atr=2.0, max_bars=20))
    old = (K.START, K.END); K.START, K.END = T0, T0 + 99 * H1
    try:
        raw = {s: d[d.t < K.END].reset_index(drop=True) for s, d in raw.items()}
        sl = ('EMA', 'zz_halt', 'long', '1h', 3)
        g, cv = C.native_capacity_run(raw, sl, 15, 'path', RULES, ['BTCUSDT', 'ETHUSDT', 'XRPUSDT'], guards=True)
        u, _ = C.native_capacity_run(raw, sl, 15, 'path', RULES, ['BTCUSDT', 'ETHUSDT', 'XRPUSDT'], guards=False)
    finally:
        K.START, K.END = old; S.STRATEGIES.pop('zz_halt', None)
    assert sum(l['native_pnl'] or 0 for l in g if l['sym'] != 'XRPUSDT') < -120
    assert 'XRPUSDT' in {l['sym'] for l in u} and 'XRPUSDT' not in {l['sym'] for l in g}
    assert len({l['id'] for l in g} ^ {l['id'] for l in u}) == 1
    closes = {s: dict(zip(d.t, d.c)) for s, d in raw.items()}
    closes.update({s: closes['BTCUSDT'] for s in K.COINS if s not in closes})
    old = (K.START, K.END); K.START, K.END = T0, T0 + 99 * H1
    try:
        rep, _ = C.case_report('EMA', g, cv, 15, closes, [T0 + k * H1 for k in range(99)], u)
    finally:
        K.START, K.END = old
    assert rep['guard_changed_entries'] == 1 and rep['guard_only_missing'][0].startswith('EMA|XRPUSDT')


if __name__ == '__main__':
    for k, f in sorted(globals().items()):
        if k.startswith('test_'): f(); print('PASS', k)
