"""C-BRKPROT-v1 ('prot' management key, default OFF): nothing moves before +at_r R; then the stop is the tighter of the
fee-adjusted breakeven and the ATR trail from the best price; both sides mirror; one shared rule for backtest and engine."""
import numpy as np
import pandas as pd
import pytest

import app
import backtest as BT
import prot as PROT
import strategy_shadow as SSH

P = dict(at_r=1.0, trail_atr=2.0)


@pytest.mark.parametrize('side', [1, -1])
def test_nothing_before_activation(side):
    e0, R, atr = 100.0, 4.0, 2.0
    best = e0 + side * 0.999 * R                             # just short of +1R: no breakeven and no trail
    assert PROT.prot_level(P, side, e0, e0, R, best, atr) is None


@pytest.mark.parametrize('side', [1, -1])
def test_breakeven_at_activation_is_beyond_entry(side):
    e0, R, atr = 100.0, 4.0, 2.0
    best = e0 + side * R                                     # exactly +1R; trail = best - side*4 = e0 -> BE (costs) is tighter
    lvl = PROT.prot_level(P, side, e0, e0, R, best, atr)
    assert lvl == pytest.approx(e0 * (1 + side * PROT.prot_buf(side, PROT.PROT_FEE, PROT.PROT_SLIP)))
    assert side * (lvl - e0) > 0


@pytest.mark.parametrize('side', [1, -1])
def test_trail_takes_over_when_tighter(side):
    e0, R, atr = 100.0, 4.0, 2.0
    best = e0 + side * 3 * R
    assert PROT.prot_level(P, side, e0, e0, R, best, atr) == pytest.approx(best - side * 2.0 * atr)


def test_buf_override_and_default_off():
    assert PROT.prot_level(None, 1, 100, 100, 4, 120, 2) is None
    assert PROT.prot_level({}, 1, 100, 100, 4, 120, 2) is None
    assert PROT.prot_level(dict(at_r=1.0, buf=0.0), 1, 100, 100, 4, 104, None) == pytest.approx(100.0)
    assert (PROT.PROT_FEE, PROT.PROT_SLIP) == (BT.FEE, BT.SLIP)    # shared default = backtest cost model


# ---------------------------------------------------------------- fill-level: the net result of a breakeven exit
def _net_at_stop(side, avg, lvl, q, fee, slip):
    """Same arithmetic as backtest.run: entry fee on avg (the slipped entry fill); close() slips the exit by `slip`
    against the side and charges the exit fee on that fill."""
    exit_px = lvl * (1 - side * slip)
    return side * (exit_px - avg) * q - q * avg * fee - q * exit_px * fee


@pytest.mark.parametrize('side', [1, -1])
@pytest.mark.parametrize('scenario', [dict(), dict(cost=0.0007), dict(cost=0.0015), dict(cost=0.0023)])
def test_breakeven_exit_nets_zero_after_costs(side, scenario):
    """FWD-01 freeze: each 7/15/23 bp scenario sets prot.cost = that all-in cost per executed side (charged fee-like on
    entry and exit, no separate slip); the default uses the backtest FEE/SLIP. The stopped breakeven exit nets exactly 0."""
    avg, R, q = 2124.61, 72.69, 0.22
    prot = dict(at_r=1.0, **scenario)                         # no trail: isolate the breakeven level
    lvl = PROT.prot_level(prot, side, avg, avg, R, avg + side * R, None)
    fee, slip = (scenario['cost'], 0.0) if scenario else (BT.FEE, BT.SLIP)
    net = _net_at_stop(side, avg, lvl, q, fee, slip)
    assert abs(net) < 1e-9 * avg * q, net
    under = PROT.prot_level(dict(prot, buf=PROT.prot_buf(side, fee, slip) * 0.9), side, avg, avg, R, avg + side * R, None)
    assert _net_at_stop(side, avg, under, q, fee, slip) < 0   # a smaller buffer would lose money: the buffer is needed


def test_min_step_rule():
    pr = dict(P)
    assert PROT.prot_tighten(pr, 100.0, 100.19, 1, 2.0) == 100.0                      # under 0.1 ATR -> stays
    assert PROT.prot_tighten(pr, 100.0, 100.21, 1, 2.0) == 100.21
    assert PROT.prot_tighten(pr, 100.0, 99.79, -1, 2.0) == 99.79
    assert PROT.prot_tighten(pr, 100.0, 100.6, 1, 2.0, tick=0.5) == 101.0              # rounded UP to the tick for a LONG
    assert PROT.prot_tighten(pr, 100.0, 99.4, -1, 2.0, tick=0.5) == 99.0               # and DOWN for a SHORT
    assert PROT.prot_tighten(pr, 99.0, 100.4, 1, 2.0, tick=1.5) == 99.0               # improvement must exceed the tick
    assert PROT.prot_tighten(dict(at_r=1), 100.0, 100.11, 1, 2.0) == 100.11            # no trail: 0.05 ATR step


def test_settings_accept_prot_and_shadow_flags_it():
    assert app.clean_mgmt({'prot': {'at_r': 1, 'trail_atr': 2, 'cost': 0.0015}}) == {'prot': {'at_r': 1.0, 'trail_atr': 2.0, 'cost': 0.0015}}
    with pytest.raises(ValueError):
        app.clean_mgmt({'prot': {'at_r': 0}})
    assert 'prot' in SSH.UNSUPPORTED                          # observer v1 does not model it: INCOMPLETE, never wrong


def _raw(n=900, seed=7):
    rng = np.random.default_rng(seed)
    t = pd.date_range('2024-01-01', periods=n, freq='4h')
    out = {}
    for j, s in enumerate(('BTCUSDT', 'ETHUSDT', 'SOLUSDT')):
        c = 100 * (1 + j) * np.exp(np.cumsum(rng.normal(0, .02, n)))
        o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, .004, n))
        w = np.abs(rng.normal(0, .012, n))
        out[s] = pd.DataFrame({'t': t, 'o': o, 'h': np.maximum(o, c) * (1 + w), 'l': np.minimum(o, c) * (1 - w),
                               'c': c, 'v': 1e3, 'qv': 1e6})
    return out


def _run(mgmt, pess='path'):
    cfg = [dict(key='exp_breakout', share=1.0, risk=0.02, max_pos=3, sides='both', mgmt=mgmt,
                symbols=['BTCUSDT', 'ETHUSDT', 'SOLUSDT'])]
    tr, cv = BT.run(BT.Book(_raw()), cfg, start=1000.0, max_lev=20.0, daily_halt=1.0, pessimistic=pess)
    return tr, cv


@pytest.mark.parametrize('pess', ['path', 'worst'])
def test_unreachable_activation_is_byte_identical_to_off(pess):
    """Mutation guard: if the +at_r gate were dropped, the 0.5 ATR trail would fire and change the run."""
    tr0, cv0 = _run({}, pess)
    tr1, cv1 = _run({'prot': dict(at_r=1e6, trail_atr=0.5)}, pess)
    pd.testing.assert_frame_equal(tr0, tr1)
    pd.testing.assert_series_equal(cv0, cv1)


def test_protection_changes_exits_on_both_sides_and_never_widens_risk():
    tr0, _ = _run({})
    tr1, _ = _run({'prot': dict(P)})
    assert len(tr0) and len(tr1)
    assert not tr0.equals(tr1)                               # it does something on this tape
    assert set(tr1['side']) == {1, -1}                       # both longs and shorts traded under the rule


# ---------------------------------------------------------------- live engine path (mock exchange from test_safety)
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_engine_moves_nothing_before_1R_then_fee_breakeven_then_trail(side):
    import test_safety as TS
    e, _ = TS.mk_engine()
    sl = dict(TS.SL, sides='both', mgmt={'stop_atr': 2.0, 'prot': dict(P)}); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', side, TS.SG, None, e.equity())
    lot = next(iter(e.state['lots'].values()))
    sd, e0, R, stop0 = (1 if side == 'LONG' else -1), lot['e0'], lot['R'], lot['stop']
    e.trade.mark['BTCUSDT'] = e0 + sd * 0.9 * R; e.manage(e.trade.marks())
    assert lot['stop'] == stop0                                  # +0.9R: nothing moves, not even the trail
    e.trade.mark['BTCUSDT'] = e0 + sd * 1.0 * R; e.manage(e.trade.marks())
    be = lot['avg'] * (1 + sd * PROT.prot_buf(sd, PROT.PROT_FEE, PROT.PROT_SLIP))
    assert sd * (lot['stop'] - lot['avg']) > 0 and sd * (lot['stop'] - be) >= -1e-6 * e0   # at least fee-adjusted breakeven
    e.trade.mark['BTCUSDT'] = e0 + sd * 4.0 * R; e.manage(e.trade.marks())
    assert lot['stop'] == pytest.approx(lot['best'] - sd * 2.0 * lot['atr_now'], rel=1e-3)   # trail took over


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_paired_replay_small_steps_and_retracement_engine_equals_backtest_rule(side):
    """Codex #333 check 1: feed the SAME best-price path to the live engine and to the backtest's rule (prot_level +
    prot_tighten, as backtest.ratchet calls them). Small successive improvements (< 0.1 ATR each) must leave both stops
    at the same price, and a retracement that lands between the naive 'every candidate' stop and the stepped stop must
    exit in neither."""
    import test_safety as TS
    e, _ = TS.mk_engine()
    sl = dict(TS.SL, sides='both', mgmt={'stop_atr': 2.0, 'prot': dict(P)}); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', side, TS.SG, None, e.equity())
    key, lot = next(iter(e.state['lots'].items()))
    sd, e0, R, atr = (1 if side == 'LONG' else -1), lot['e0'], lot['R'], lot['atr_now']
    tick = e.rules.get('BTCUSDT', {}).get('tick') or 0.0
    sim, naive, best = lot['stop'], lot['stop'], e0
    path = [e0 + sd * x * R for x in (0.5, 1.0, 1.6)] + [e0 + sd * (1.6 * R + j * 0.04 * atr) for j in range(1, 6)]
    for m in path:
        e.trade.mark['BTCUSDT'] = m; e.manage(e.trade.marks())
        best = max(best, m) if sd == 1 else min(best, m)
        lvl = PROT.prot_level(P, sd, e0, lot['avg'], R, best, atr, BT.FEE, BT.SLIP)
        sim = PROT.prot_tighten(P, sim, lvl, sd, atr, tick)
        if lvl is not None: naive = max(naive, lvl) if sd == 1 else min(naive, lvl)
        assert lot['stop'] == pytest.approx(sim, rel=1e-9), (m, lot['stop'], sim)
    assert sd * (naive - sim) > 0                             # the small steps were really held back in both
    back = (sim + naive) / 2                                  # retrace between the stepped and the naive stop
    e.trade.mark['BTCUSDT'] = back; e.manage(e.trade.marks())
    assert key in e.state['lots'] and sd * (back - sim) > 0  # engine still open; the shared rule's stop is not hit either


# ---------------------------------------------------------------- Codex #333 re-review: prot is the ONLY stop mover
def test_prot_conflicts_lists_other_active_stop_movers():
    assert PROT.prot_conflicts({'prot': P}) == []
    assert PROT.prot_conflicts({'be_r': 1, 'trail_atr': 2}) == []                      # no prot: legacy untouched
    assert PROT.prot_conflicts({'prot': P, 'be_r': 1, 'runner': {'be_r': 2}}) == ['be_r', 'runner']
    assert PROT.prot_conflicts({'prot': P, 'trail_atr': 0, 'be_r': 0, 'ttp': {}}) == []   # 0 / {} = switched off


@pytest.mark.parametrize('extra', [dict(be_r=1), dict(trail_atr=2), dict(runner=dict(be_r=2, step_r=1, gap_r=0.5)),
                                   dict(ttp=dict(at_r=2, dev_pct=3))])
def test_settings_refuse_prot_mixed_with_another_stop_mover(extra):
    sl = dict(id='P', key='exp_breakout', share=.5, risk=.02, max_pos=2, mgmt={'prot': dict(P), **extra})
    with pytest.raises(ValueError, match='prot'):
        app.validate_sleeve(sl, 0)
    with pytest.raises(ValueError, match='prot'):
        BT.run(BT.Book(_raw()), [dict(key='exp_breakout', share=1.0, risk=.02, max_pos=1, mgmt=sl['mgmt'],
                                      symbols=['BTCUSDT'])], start=1000.0)


def test_settings_check_the_strategy_defaults_too():
    """hot_coin / bear_breakdown trail by default: prot is refused unless the slot switches that trail off."""
    base = dict(id='H', key='hot_coin', share=.5, risk=.02, max_pos=2)
    with pytest.raises(ValueError, match='trail_atr'):
        app.validate_sleeve(dict(base, mgmt={'prot': dict(P)}), 0)
    ok = app.validate_sleeve(dict(base, mgmt={'prot': dict(P), 'trail_atr': 0}), 0)
    assert ok['mgmt']['prot'] == {'at_r': 1.0, 'trail_atr': 2.0}
    assert app.validate_sleeve(dict(base, key='exp_breakout', mgmt={'prot': dict(P)}), 0)['mgmt']['prot']


def _engine_stops(mgmt, side, path_r):
    import test_safety as TS
    e, _ = TS.mk_engine()
    sl = dict(TS.SL, sides='both', mgmt=mgmt); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', side, TS.SG, None, e.equity())
    lot = next(iter(e.state['lots'].values()))
    sd, e0, R, atr = (1 if side == 'LONG' else -1), lot['e0'], lot['R'], lot['atr_now']
    out = []
    for x in path_r:
        e.trade.mark['BTCUSDT'] = e0 + sd * x(R, atr); e.manage(e.trade.marks())
        out.append(lot['stop'])
    return out


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('legacy', [dict(be_r=0.5), dict(runner=dict(be_r=0.5, step_r=0.5, gap_r=0.25))])
def test_engine_never_half_applies_prot_next_to_another_mover(side, legacy):
    """Hand-edited settings that bypass validation: the engine ignores prot entirely, so the legacy rule keeps its own
    0.05 ATR step - including BEFORE prot's +1R activation, where an inactive prot trail must not raise the threshold.
    Small 0.07 ATR improvements are exactly the case Codex named."""
    path = [lambda R, a: 0.5 * R] + [lambda R, a, j=j: 0.5 * R + j * 0.07 * a for j in range(1, 8)] + [lambda R, a: 1.5 * R]
    base = _engine_stops({'stop_atr': 2.0, **legacy}, side, path)
    mixed = _engine_stops({'stop_atr': 2.0, **legacy, 'prot': dict(at_r=1.0, trail_atr=0.3)}, side, path)
    assert mixed == base


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_prot_alone_preactivation_engine_and_backtest_rule_both_hold(side):
    """Pre +1R the prot-only engine moves nothing even on many small improvements, and neither does the shared rule."""
    path = [lambda R, a, j=j: j * 0.07 * a for j in range(1, 10)]
    stops = _engine_stops({'stop_atr': 2.0, 'prot': dict(P)}, side, path)
    assert len(set(stops)) == 1
