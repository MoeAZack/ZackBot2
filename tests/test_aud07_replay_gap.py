"""AUD-07 C13f (BT-C8b): replay-harness truth for a candle that OPENS through the stop.

replay.run_replay used to set the next candle's open with W.mark.update(...), which runs no simulated stop, so a stop the
open had gapped through was filled at the STOP price on the first path step. A Binance stop-market fills where the market
is - at the gapped open - which is what the backtester books (backtest.py: gap at the open -> close at o, no adds). The
replay therefore flattered the engine on every gap and the strict dR / return metrics absorbed the difference.

The open is now a price move through replay's move(at_open=True): the simulated exchange fills a gapped stop at the open
with the exit slippage. Harness only - no product file changes. Account-level truth is asserted (the replay's equity curve,
which reads the simulated exchange); the engine's own journal R is a separate product question (golden case
G-GAP-STOP-L-01, known divergence C13g).
"""
import numpy as np, pandas as pd, pytest

import backtest as B
import strategies as S

SYM = 'SOLUSDT'
N, E_BAR = 300, 280                      # signal on 279 -> entry at the open of 280; the gap candle is 282


def _market(side, gap_candle):
    t = pd.date_range('2024-01-01', periods=N, freq='4h')
    px = np.full(N, 100.0)
    o, c = px.copy(), px.copy(); h, l = px + 0.5, px - 0.5           # flat, true range 1 -> ATR exactly 1
    o_, h_, l_, c_ = gap_candle
    if side == -1:                                                    # mirror around 100 for the short
        o_, h_, l_, c_ = 200 - o_, 200 - l_, 200 - h_, 200 - c_
    o[E_BAR + 2], h[E_BAR + 2], l[E_BAR + 2], c[E_BAR + 2] = o_, h_, l_, c_
    for j in range(E_BAR + 3, N):
        o[j] = c[j] = c_; h[j] = c_ + 0.5; l[j] = c_ - 0.5
    return {SYM: pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=1000.0))}


def _run(side, gap_candle):
    import engine as E
    from replay import run_replay
    raw = _market(side, gap_candle)
    sig_t = raw[SYM].t[E_BAR - 1]
    real = S.signals

    def fake(key, d, ctx, params=None, mask_sides=True):
        out = {k: np.zeros(len(d), bool) for k in ('le', 'se', 'lx', 'sx')}
        out['le' if side == 1 else 'se'][:] = (pd.to_datetime(d.t) == sig_t).values
        return out
    S.signals = fake
    try:
        sl = [E.sleeve('G', 'ema_st', 1.0, .02, 1, [SYM], sides='long' if side == 1 else 'short', mgmt={'stop_atr': 2.0})]
        r = run_replay(raw, sl, 260, steps=8)
    finally:
        S.signals = real
    return r


def _account_r(r):
    """The trade's R as the simulated exchange account saw it (one trade, flat afterwards; same funding in both curves)."""
    risk = float(r['history'][0]['risk_usd'])
    return (float(r['engine_curve'].iloc[-1]) - 500.0) / risk, (float(r['bt_curve'].iloc[-1]) - 500.0) / risk


@pytest.mark.parametrize('side', [1, -1])
def test_a_stop_gapped_at_the_open_fills_at_the_open_in_the_replay(side):
    """Stop 98.02 (long); candle 282 opens at 95: the exchange fills at 95 x (1 - slip) -> about -2.57 R, like the backtest.
    On b8c11c8 the simulated exchange filled at the stop (98.02): account -1.06 R vs backtest -2.57 R, ret gap 3.02 pp."""
    r = _run(side, (95.0, 95.2, 94.8, 95.0))
    bt = r['bt_trades']
    assert len(bt) == 1 and len(r['history']) == 1, (bt, r['history'])
    assert bt.R.iloc[0] == pytest.approx(-2.57, abs=0.015)             # the backtest's (correct) gap fill, incl. replay funding
    eng_r, bt_r = _account_r(r)
    assert eng_r == pytest.approx(bt_r, abs=0.05), f'engine account R {eng_r:.3f} vs backtest {bt_r:.3f}'
    m = r['metrics']
    assert m['ret_gap'] <= 0.05 and m['dd_gap'] <= 0.05, m
    assert m['mismatches'] == 0 and m['stops'] == m['lots'] == 0, m


@pytest.mark.parametrize('side', [1, -1])
def test_a_stop_reached_inside_the_candle_still_fills_at_the_stop(side):
    """Twin without a gap: the open is above the stop, the low reaches it inside the candle -> filled at the stop price
    (the move at the open must not change in-path fills)."""
    r = _run(side, (100.0, 100.2, 97.5, 97.8))
    eng_r, bt_r = _account_r(r)
    assert bt_r == pytest.approx(-1.06, abs=0.015)                    # incl. replay funding
    assert eng_r == pytest.approx(bt_r, abs=0.01), f'engine account R {eng_r:.3f} vs backtest {bt_r:.3f}'
    assert r['metrics']['ret_gap'] <= 0.05, r['metrics']
