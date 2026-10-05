"""Causality and engine/backtest parity on synthetic data (fast enough for every build).

1. Engine causality (perturbation test proposed by the independent reviewer): replay the LIVE engine to a point INSIDE a
   candle, record every decision; change everything the engine cannot have seen yet (the rest of that candle and all later
   candles); replay to the same point; the decisions must be identical. Runs across long/short, trailing stops, pyramids,
   DCA, partial profit and runner modes.
2. Backtest parity: the backtester must reproduce the causal engine trade by trade on the same synthetic candles. The
   backtester evaluates a whole candle at once, so this (not truncation) is what catches a backtester that uses a candle's
   unfinished information - e.g. the same-candle ATR in the trailing stop found on 2026-10-05.
"""
import copy
import numpy as np, pandas as pd, pytest
import engine as E
import backtest as B
from replay import run_replay

SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
T0 = 260
STEPS = 3


def synth(n=420, seed=6, amp=0.003, per=15, vol=0.008):
    rng = np.random.default_rng(seed); t = pd.date_range('2024-01-01', periods=n, freq='4h'); out = {}
    for j, s in enumerate(SYMS):
        r = rng.standard_t(4, n) * vol + amp * np.sin(np.arange(n) / (per + 9 * j))
        c = 100 * (j + 1) * np.exp(np.cumsum(r)); o = np.r_[c[0], c[:-1]]
        w = np.abs(rng.normal(0, vol * 0.6, n)) * c
        out[s] = pd.DataFrame(dict(t=t, o=o, h=np.maximum(o, c) + w, l=np.minimum(o, c) - w, c=c, v=1000.0 + j))
    return out


def perturb(raw, j, seen_legs, seed=11):
    """Copy of raw where everything not yet seen at the end of leg `seen_legs` (1 or 2) of candle j is changed:
    seen_legs=1 -> open + first extreme kept, second extreme + close + volume changed (the candle keeps its colour);
    seen_legs=2 -> open + both extremes kept, close + volume changed; every candle after j is replaced by noise."""
    rng = np.random.default_rng(seed); out = {}
    for s, d in raw.items():
        d = d.copy(); o, h, l, c = d.loc[j, ['o', 'h', 'l', 'c']]
        green = c >= o
        if seen_legs == 1:
            if green: h = max(h, o) * (1 + rng.uniform(0.02, 0.08)); c = o + (h - o) * rng.uniform(0.05, 0.95)
            else: l = min(l, o) * (1 - rng.uniform(0.02, 0.08)); c = o - (o - l) * rng.uniform(0.05, 0.95)
        else:
            c = o + (h - o) * rng.uniform(0.05, 0.95) if green else o - (o - l) * rng.uniform(0.05, 0.95)
        d.loc[j, ['h', 'l', 'c', 'v']] = [h, l, c, d.loc[j, 'v'] * 7]
        k = d.index > j
        noise = np.exp(np.cumsum(rng.normal(0, 0.03, k.sum())))
        base = d.loc[j, 'c']
        d.loc[k, 'c'] = base * noise; d.loc[k, 'o'] = np.r_[base, d.loc[k, 'c'].values[:-1]]
        d.loc[k, 'h'] = d.loc[k, ['o', 'c']].max(axis=1) * 1.01; d.loc[k, 'l'] = d.loc[k, ['o', 'c']].min(axis=1) * 0.99
        out[s] = d
    return out


MODES = {
    'trend long + pyramid': [E.sleeve('P', 'ema_mom', 1.0, .02, 3, 'core8', mgmt={'pyramid': {'n': 2, 'step_r': 0.7, 'frac': 0.5}})],
    'trailing both sides': [E.sleeve('D', 'donchian_ens', 1.0, .02, 3, 'core8', sides='both')],
    'breakout pyramid + trail': [E.sleeve('B', 'breakout_pyramid', 1.0, .02, 3, 'core8')],
    'DCA basket': [E.sleeve('C', 'dca_dip', 1.0, .02, 3, 'core8')],
    'partial TP + breakeven': [E.sleeve('Q', 'squeeze_tp', 1.0, .02, 3, 'core8', sides='both')],
    'runner ladder': [E.sleeve('R', 'ema_st', 1.0, .02, 3, 'core8', mgmt={'runner': {'be_r': 1, 'step_r': 1, 'gap_r': 1, 'dca_frac': 1}})],
}


def _decisions(snap):
    lots = {k: {f: v for f, v in l.items() if f not in ('atr_now',)} for k, l in snap['lots'].items()}
    return dict(lots=lots, events=snap['events'], history=snap['history'], stops=snap['stops'], cash=round(snap['cash'], 9))


CHECKPOINTS = ((350, 1), (395, 2))      # (candle, legs already walked): end of the first / second leg of that candle


PARITY_SEED = {'trend long + pyramid': 23}       # a market where this slow trend strategy trades often enough
CORE = ('trailing both sides', 'breakout pyramid + trail', 'DCA basket')     # run in every build; the rest in run_checks
PARAMS = [m if m in CORE else pytest.param(m, marks=pytest.mark.slow) for m in MODES]


@pytest.mark.parametrize('mode', PARAMS)
def test_engine_decisions_never_depend_on_unseen_prices(mode):
    raw = synth(); sl = MODES[mode]
    pts = [(j, legs - 1, STEPS) for j, legs in CHECKPOINTS]
    base = run_replay(raw, copy.deepcopy(sl), T0, steps=STEPS, stop_at=pts)       # one pass, a snapshot at every checkpoint
    seen = 0
    for (j, legs), pt in zip(CHECKPOINTS, pts):
        b = run_replay(perturb(raw, j, legs), copy.deepcopy(sl), T0, steps=STEPS, stop_at=pt)
        assert _decisions(base[pt]) == _decisions(b), f'{mode}: decisions at candle {j} after leg {legs} changed when unseen prices changed'
        seen += len(base[pt]['lots']) + len(base[pt]['history'])
    assert seen > 0, f'{mode}: scenario produced no trades - the check would be meaningless'


@pytest.mark.parametrize('mode', PARAMS)
def test_backtest_matches_causal_engine_trade_by_trade(mode):
    r = run_replay(synth(n=520, seed=PARITY_SEED.get(mode, 6)), copy.deepcopy(MODES[mode]), T0, steps=6)
    m = r['metrics']
    assert m['mismatches'] == 0 and m['stops'] == m['lots'], m
    assert m['trades_engine'] >= 2, f'{mode}: too few trades ({m})'
    unmatched = m['trades_engine'] + m['trades_bt'] - 2 * m['matched']
    # small samples: one threshold flip (a level reached by a hair in one model only) is allowed, else the 98 % target
    assert unmatched <= max(1, int(0.02 * max(m['trades_engine'], m['trades_bt']))), m
    assert m['med_dr'] <= 0.05 and m['p95_dr'] <= 0.25, m


def spiky(n=520, seed=6, wick=1.0):
    """Synthetic market with long one-sided wicks: the candle's own range (and ATR) differs a lot from the previous one,
    which is exactly where a backtester that peeks at the unfinished candle drifts from the live engine."""
    rng = np.random.default_rng(seed); t = pd.date_range('2024-01-01', periods=n, freq='4h'); out = {}
    for j, s in enumerate(SYMS):
        r = rng.standard_t(4, n) * 0.008 + 0.003 * np.sin(np.arange(n) / (15 + 9 * j))
        c = 100 * (j + 1) * np.exp(np.cumsum(r)); o = np.r_[c[0], c[:-1]]
        w = np.abs(rng.standard_t(2, n)) * 0.006 * c * wick
        up = rng.random(n) < 0.5
        out[s] = pd.DataFrame(dict(t=t, o=o, h=np.maximum(o, c) + np.where(up, w, w * 0.2),
                                   l=np.minimum(o, c) - np.where(up, w * 0.2, w), c=c, v=1000.0))
    return out


def test_parity_catches_same_candle_information():
    """Leak detector. With the 2026-10-05 bug planted back (trailing stop on the unfinished candle's ATR) this scenario gave
    median |dR| 0.089, p95 0.72 and a 6-point return gap; the fixed backtester gives 0.03 / 0.09 / 0.5."""
    r = run_replay(spiky(), copy.deepcopy(MODES['trailing both sides']), T0, steps=6)
    m = r['metrics']
    assert m['trades_engine'] >= 10 and m['matched'] >= m['trades_engine'] - 1, m
    assert m['med_dr'] <= 0.05 and m['p95_dr'] <= 0.25 and m['ret_gap'] <= 3.0, m
