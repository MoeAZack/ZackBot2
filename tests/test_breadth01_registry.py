"""BREADTH-01 slice B1: registry-wide quick-test harness over EVERY strategies.STRATEGIES entry and every side its signal
function is designed to emit (EMITS below; `se=d.c < -1` / `le=d.c < -1` = never).

Per (strategy, side), on a deterministic synthetic tape (tests/breadth01_synth.py, qv included for exp_vwaprev):
  1. the signal fires on the intended side (never both sides on one candle where `_one_side` applies);
  2. direction: through the engine's own mapping (engine.Engine.compute_signals on a fake engine + the entry loop's
     `'LONG' if sg['le'] else 'SHORT'`) le -> LONG and se -> SHORT, and the order spec (feasibility.risk_qty with the
     strategy mgmt and the engine's stop ATR, engine._sig_atr) puts the protective stop on the correct side;
  3. the backtester (backtest.Book / backtest.run) takes it on that side, every opened trade carries a stop on the correct
     side, every closed trade has an exit reason, and the strategy's own exit path (signal / time cap / target) closes some;
  4. restart: the strategies are stateless functions of the candle frame - recomputing from a reloaded (JSON round-trip)
     frame gives identical signals; the one persisted strategy state (VWAPREV re-entry lock, from trade history)
     round-trips;
  5. prefix causality: signals on candles[:k] equal the first k signals of the full series, for every strategy.
Plus family coverage (FAMILY table) and the side-emission contract (designed-never sides never fire).

Long-default strategies (registry sides='long') whose signal function ALSO emits shorts - a slot with sides='both'/'short'
takes them: ema_st, ema_mom, donchian_ens, breakout_pyramid, squeeze_tp, pullback_rsi, rotation (shorts only with
params shorts=True). Runtime-untested for shorts (no engine / replay test runs them with a short-enabled slot):
ema_st, ema_mom, breakout_pyramid, pullback_rsi, rotation(shorts=True). donchian_ens and squeeze_tp run sides='both' in
the test_causality / test_qty_tolerance replays, but no test asserts a SHORT there. This file covers signal -> engine
mapping -> stop -> backtest only, not the live engine order path.

Synthetic data only; no app, settings, credentials, network or real data; no optimisation. Quick (< ~60 s).
"""
import json
import zlib
from functools import lru_cache
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import backtest as B
import engine as E
import feasibility as F
import strategies as S
import breadth01_synth as Y

SYM = 'ETHUSDT'
SYMS = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT')
WARM = 250                                      # engine compute_signals needs >= 250 closed candles

SHAPES = dict(trend=dict(n=600, dr=0.004, A=0.10, P=40.0),          # up-drift + swing (mirror -> down-drift)
              range=dict(n=600, dr=0.0, A=0.06, P=12.0, noise=0.004))  # no drift, fast swing: crosses + reversions

# key -> sides its signal function is designed to emit
EMITS = {'ema_st': ('le', 'se'), 'ema_mom': ('le', 'se'), 'donchian_ens': ('le', 'se'), 'breakout_pyramid': ('le', 'se'),
         'squeeze_tp': ('le', 'se'), 'pullback_rsi': ('le', 'se'), 'dca_dip': ('le',), 'bear_breakdown': ('se',),
         'fib_pullback': ('le', 'se'),
         'rotation': ('le', 'se'), 'hot_coin': ('le',), 'exp_trend': ('le', 'se'), 'exp_range': ('le', 'se'),
         'exp_breakout': ('le', 'se'), 'exp_scalp': ('le', 'se'), 'exp_structure': ('le', 'se'), 'exp_vwaprev': ('le', 'se'),
         'exp_mom_short': ('se',), 'exp_rip_sell_dca': ('se',),
         # SCALP-4 (PAPER, 15m)
         'range_scalp': ('le', 'se'), 'failed_breakout_scalp': ('le', 'se'), 'momentum_burst_scalp': ('le', 'se'),
         'quick_bank_runner': ('le', 'se')}
# sides that need a non-default param to be emitted
SIDE_PARAMS = {('rotation', 'se'): dict(shorts=True)}
LONG_DEFAULT_SHORT_EMITTERS = {'ema_st', 'ema_mom', 'donchian_ens', 'breakout_pyramid', 'squeeze_tp', 'pullback_rsi',
                               'rotation'}
RUNTIME_UNTESTED_SHORTS = {'ema_st', 'ema_mom', 'breakout_pyramid', 'pullback_rsi', 'rotation'}
ONE_SIDE = {k for k in S.STRATEGIES if k.startswith('exp_')} | E.SCALP_KEYS | {'fib_pullback'}

FAMILY = {'ema_st': 'Trend', 'ema_mom': 'Trend', 'donchian_ens': 'Trend', 'exp_trend': 'Trend',
          'pullback_rsi': 'Momentum', 'fib_pullback': 'Momentum', 'hot_coin': 'Momentum',
          'exp_range': 'Range', 'exp_vwaprev': 'Range',
          'breakout_pyramid': 'Breakout', 'squeeze_tp': 'Breakout', 'bear_breakdown': 'Breakout', 'exp_breakout': 'Breakout',
          'exp_structure': 'Breakout',
          'exp_scalp': 'Scalp',
          'rotation': 'Rotation',
          'dca_dip': 'Dip-DCA',
          'exp_mom_short': 'Momentum', 'exp_rip_sell_dca': 'Dip-DCA',   # B3 short mirrors (#94)
          'range_scalp': 'Scalp', 'failed_breakout_scalp': 'Scalp', 'momentum_burst_scalp': 'Scalp',
          'quick_bank_runner': 'Scalp'}                                  # SCALP-4 (PAPER slots, not rotated)
FAMILIES = ('Trend', 'Momentum', 'Range', 'Breakout', 'Scalp', 'Rotation', 'Dip-DCA')

# (key, side) -> (shape, mirrored). Long fixtures are up-drift tapes, shorts their exact price mirror (BTC down too).
RANGE_KEYS = {'ema_st', 'ema_mom', 'exp_trend', 'exp_range', 'exp_vwaprev', 'range_scalp', 'failed_breakout_scalp'}


def fixture_of(key, side):
    if key in RANGE_KEYS: return ('range', side == 'se')
    return ('trend', side == 'se')


CASES = [(k, sd) for k in S.STRATEGIES for sd in EMITS.get(k, ())]
IDS = [f'{k}-{sd}' for k, sd in CASES]
EXIT_REASONS = {'stop', 'tp', 'signal', 'time'}


@lru_cache(maxsize=None)
def raw_of(shape, mirrored):
    return Y.book_raw(SHAPES[shape], syms=SYMS, short=mirrored)


@lru_cache(maxsize=None)
def book_of(shape, mirrored):
    return B.Book(raw_of(shape, mirrored))


def params_of(key, side):
    return SIDE_PARAMS.get((key, side))


def sigs(key, side):
    return book_of(*fixture_of(key, side)).raw_sig(key, SYM, params_of(key, side))


def first_hit(key, side):
    s = sigs(key, side)
    hits = np.flatnonzero(s[side][WARM:]) + WARM
    assert len(hits), (key, side, 'never fires after warm-up')
    return int(hits[0])


# ------------------------------------------------------------------ registry contract
def test_every_registry_key_has_a_side_table_and_family():
    assert set(EMITS) == set(S.STRATEGIES), set(S.STRATEGIES) ^ set(EMITS)
    assert set(FAMILY) == set(S.STRATEGIES), f'new strategy without a family: {set(S.STRATEGIES) - set(FAMILY)}'
    assert set(FAMILY.values()) <= set(FAMILIES)


def test_family_coverage_long_and_short():
    for fam in FAMILIES:
        keys = [k for k, f in FAMILY.items() if f == fam]
        assert keys, fam
        assert any('le' in EMITS[k] for k in keys), f'{fam}: no long emitter'
        if fam != 'Dip-DCA':
            assert any('se' in EMITS[k] for k in keys), f'{fam}: no short emitter'


def test_long_default_short_emitters_marked():
    """Long-default registry entries whose signal function emits shorts (runtime-untested shorts: RUNTIME_UNTESTED_SHORTS)."""
    got = {k for k, st in S.STRATEGIES.items() if st['sides'] == 'long' and 'se' in EMITS[k]}
    assert got == LONG_DEFAULT_SHORT_EMITTERS
    assert RUNTIME_UNTESTED_SHORTS <= LONG_DEFAULT_SHORT_EMITTERS


def test_designed_never_sides_never_fire():
    """A side outside EMITS (e.g. dca_dip / hot_coin shorts, bear_breakdown longs, rotation shorts without shorts=True)
    stays False on every fixture tape."""
    for key in S.STRATEGIES:
        for side in ('le', 'se'):
            if side in EMITS[key]: continue
            for fx in (('trend', False), ('trend', True), ('range', False), ('range', True)):
                s = book_of(*fx).raw_sig(key, SYM, None)
                assert not s[side].any(), (key, side, fx)


# ------------------------------------------------------------------ 1. fires on the intended side
@pytest.mark.parametrize('key,side', CASES, ids=IDS)
def test_fires_on_intended_side(key, side):
    s = sigs(key, side)
    assert s[side][WARM:].any(), (key, side)
    if key in ONE_SIDE:
        assert not (s['le'] & s['se']).any(), key


# ------------------------------------------------------------------ 2. direction through the engine mapping + stop side
def _fake_engine(key, side, raw, i):
    sides = 'long' if side == 'le' else 'short'
    sl = E.sleeve('X', key, 1.0, .01, 1, sides=sides, tf='4h')
    if params_of(key, side): sl['params'] = params_of(key, side)
    frames = {s: S.indicators(d.iloc[:i + 1].reset_index(drop=True)) for s, d in raw.items()}
    return SimpleNamespace(S={'SLEEVES': [sl]}, rules={s: {} for s in raw}, _sig_raw={},
                           candles=lambda s, tf: frames[s]), sl


@pytest.mark.parametrize('key,side', CASES, ids=IDS)
def test_engine_direction_and_stop_side(key, side):
    i = first_hit(key, side)
    raw = raw_of(*fixture_of(key, side))
    fake, sl = _fake_engine(key, side, raw, i)
    out, al = E.Engine.compute_signals(fake, '4h', [SYM])
    sg = out[f'X|{SYM}']
    assert sg['le'] or sg['se'], (key, side, i)
    eng_side = 'LONG' if sg['le'] else 'SHORT'                      # engine entry loop mapping
    assert eng_side == ('LONG' if side == 'le' else 'SHORT')
    sd = 1 if eng_side == 'LONG' else -1
    full = sigs(key, side)
    d = al[SYM].iloc[-1]
    if 'atr' in full:                                               # strategy-defined stop ATR (VWAPREV TR-SMA14)
        assert np.isfinite(full['atr'][i]) and sg['atr'] == pytest.approx(float(full['atr'][i]))
    else:
        assert sg['atr'] == pytest.approx(float(d.atr))
    g = S.merge_mgmt(key)
    px = sg['close']
    z = F.risk_qty(g, 10.0, px, sg['atr'], sd)
    assert z['qty'] > 0 and z['R'] > 0
    assert (z['stop'] < px) if sd == 1 else (z['stop'] > px)
    if 'dca' not in g:
        assert z['R'] == pytest.approx(g.get('stop_atr', 2.5) * sg['atr'])


# ------------------------------------------------------------------ 3. backtest: stop on every entry, exit reason on every trade
@lru_cache(maxsize=None)
def _bt(key, side):
    fx = fixture_of(key, side)
    calls = []
    real = F.risk_qty

    def spy(mgmt, risk_usd, px, atr, sd):
        z = real(mgmt, risk_usd, px, atr, sd)
        calls.append(dict(px=px, atr=atr, sd=sd, stop=z['stop'], R=z['R']))
        return z
    cfg = dict(key=key, share=1.0, risk=0.01, max_pos=1, sides='long' if side == 'le' else 'short')
    if params_of(key, side): cfg['params'] = params_of(key, side)
    F.risk_qty = spy
    try:
        tr, cv = B.run(book_of(*fx), [cfg], warmup=WARM)
    finally:
        F.risk_qty = real
    return tr, calls


@pytest.mark.parametrize('key,side', CASES, ids=IDS)
def test_backtest_stop_and_exit(key, side):
    tr, calls = _bt(key, side)
    sd = 1 if side == 'le' else -1
    assert len(tr), (key, side, 'no closed trade')
    assert calls and len(calls) >= len(tr)                         # every closed trade was opened through risk_qty
    for c in calls:
        assert c['sd'] == sd and c['R'] > 0 and np.isfinite(c['stop'])
        assert (c['stop'] < c['px']) if sd == 1 else (c['stop'] > c['px'])
    assert set(tr.side) == {sd}
    assert set(tr.why) <= EXIT_REASONS, set(tr.why)
    g = S.merge_mgmt(key)
    managed = tr.why != 'stop'                                      # signal / time cap / target
    if g.get('trail_atr') or g.get('be_r'):                         # a ratcheted (trailing / breakeven) stop is the exit path too
        managed |= (tr.why == 'stop') & (tr.R > -0.9)
    assert managed.any(), (key, side, 'only initial-stop exits - the strategy exit path never closed a trade')


def _bt_one(key, side):
    """Single-symbol backtest of (key, side) on its fixture: (signals, risk_qty calls)."""
    bk = B.Book({SYM: raw_of(*fixture_of(key, side))[SYM]})
    calls, real = [], F.risk_qty

    def spy(mgmt, risk_usd, px, atr, sd):
        calls.append(atr)
        return real(mgmt, risk_usd, px, atr, sd)
    F.risk_qty = spy
    try:
        B.run(bk, [dict(key=key, share=1.0, risk=0.01, max_pos=1, sides='long' if side == 'le' else 'short')], warmup=WARM)
    finally:
        F.risk_qty = real
    return bk, bk.raw_sig(key, SYM), calls


@pytest.mark.parametrize('side', ['le', 'se'])
def test_backtest_vwaprev_stop_uses_strategy_atr(side):
    """Engine parity (B1 finding, fixed): backtest VWAPREV stops use the strategy TR-SMA14 like engine._sig_atr."""
    bk, s, calls = _bt_one('exp_vwaprev', side)
    hits = np.flatnonzero(s[side][WARM:]) + WARM
    want = [float(s['atr'][i]) for i in hits]
    assert calls and all(any(abs(a - w) <= 1e-9 * w for w in want) for a in calls), (calls[:3], want[:3])


# ------------------------------------------------------------------ 4. restart: reload recomputes identical signals
def _reload(raw):
    """A persisted-and-reloaded candle cache (JSON records, ms timestamps)."""
    rec = raw.assign(t=pd.to_datetime(raw.t).astype('datetime64[ns]').astype('int64') // 1_000_000)
    back = pd.DataFrame(json.loads(json.dumps(rec.to_dict(orient='list'))))
    back['t'] = pd.to_datetime(back['t'], unit='ms')
    return back


def _same(a, b):
    for col in ('le', 'se', 'lx', 'sx'):
        assert (a[col] == b[col]).all(), col
    if 'atr' in a or 'atr' in b:
        np.testing.assert_array_equal(a['atr'], b['atr'])


@pytest.mark.parametrize('key,side', CASES, ids=IDS)
def test_restart_recompute_identical(key, side):
    fx = fixture_of(key, side)
    raw = raw_of(*fx)
    before = {s: d.copy() for s, d in raw.items()}
    first = B.Book(raw).raw_sig(key, SYM, params_of(key, side))
    for s in raw:
        pd.testing.assert_frame_equal(raw[s], before[s])            # the signal path never mutates its input
    again = B.Book({s: d.copy() for s, d in raw.items()}).raw_sig(key, SYM, params_of(key, side))
    _same(first, again)                                             # no hidden state between cycles
    rel = {s: _reload(d) for s, d in raw.items()}
    for s in raw:                                                   # the reload is value-faithful (ms + float repr)
        np.testing.assert_array_equal(rel[s][['o', 'h', 'l', 'c', 'v', 'qv']].to_numpy(),
                                      raw[s][['o', 'h', 'l', 'c', 'v', 'qv']].to_numpy())
    _same(first, B.Book(rel).raw_sig(key, SYM, params_of(key, side)))


def test_restart_reentry_lock_state_round_trips():
    """The only strategy state the engine persists between cycles: the reentry_lock history (exp_vwaprev)."""
    locked = [k for k, st in S.STRATEGIES.items() if st.get('reentry_lock')]
    assert locked == ['exp_vwaprev']
    t0 = pd.Timestamp('2025-01-01 08:00')
    hist = [dict(sleeve='V', symbol=SYM, side=sd, closed=(t0 + pd.Timedelta(seconds=20)).tz_localize('UTC').isoformat(),
                 exit_reason='exit_signal') for sd in ('LONG', 'SHORT')]
    rel = json.loads(json.dumps(hist))
    sl = E.sleeve('V', 'exp_vwaprev', 1.0, .01, 4, tf='4h')
    for k in range(3):
        for sd in ('LONG', 'SHORT'):
            sg = dict(time=str(t0 - pd.Timedelta(hours=4) + k * pd.Timedelta(hours=4)), le=sd == 'LONG', se=sd == 'SHORT')
            a = E.Engine.reentry_lock(SimpleNamespace(history=hist), sl, SYM, sd, sg)
            b = E.Engine.reentry_lock(SimpleNamespace(history=rel), sl, SYM, sd, sg)
            assert a == b and bool(a) == (k == 0)


# ------------------------------------------------------------------ 5. prefix causality (every strategy)
@pytest.mark.parametrize('key', list(S.STRATEGIES))
def test_prefix_causality(key):
    for side in EMITS[key]:
        fx = fixture_of(key, side)
        raw, full = raw_of(*fx), sigs(key, side)
        n = len(raw[SYM])
        hits = np.flatnonzero(full[side][WARM:]) + WARM
        rng = np.random.default_rng(zlib.crc32(key.encode()))
        cuts = sorted(set([int(x) + 1 for x in hits[:2]] + [int(x) for x in rng.integers(WARM, n, 3)]))
        for k in cuts:
            pre = B.Book({s: d.iloc[:k] for s, d in raw.items()}).raw_sig(key, SYM, params_of(key, side))
            for col in ('le', 'se', 'lx', 'sx'):
                diff = np.flatnonzero(pre[col] != full[col][:k])
                assert len(diff) == 0, (key, side, col, k, diff[:5])
            if 'atr' in full:
                np.testing.assert_array_equal(pre['atr'], full['atr'][:k])
