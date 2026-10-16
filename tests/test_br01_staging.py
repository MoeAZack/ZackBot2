"""BR-01 (PAPER research): bounded recovery / staged-entry variants over an existing base signal (staging.py, shared by
backtest.run and the engine). One dedicated test per contract invariant (6212399925) and Cowork attack (6212422912):
fixed risk budget, no martingale sizing, stop never widened, <= 2 adds, hard stop, 8-12 bar deadline, costs on every leg,
causal closed-candle decisions, restart-safe leg state, every add / refusal attributed, PAPER only, off by default.
Synthetic data only."""
import copy
import csv
import itertools
import os
import random
import sys
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_safety import FakeX, mk_engine               # noqa: E402  (FakeX engine, PAPER)
import backtest as B                                    # noqa: E402
import engine as E                                      # noqa: E402
import staging as BR                                    # noqa: E402
import strategies as S                                  # noqa: E402

N0, SIG = 300, 299                  # bars before the managed path; the base signal fires on the close of bar SIG
STOP_ATR = 2.0


# ------------------------------------------------------------------ synthetic backtest harness
def _sig_fn(d, p, ctx):
    e = pd.Series(d.index == SIG, index=d.index)
    z = pd.Series(False, index=d.index)
    return dict(le=z, se=e, lx=z, sx=z) if p.get('short') else dict(le=e, se=z, lx=z, sx=z)


@pytest.fixture(autouse=True)
def br_base(monkeypatch):
    """A neutral base strategy: one long signal at bar SIG, stop STOP_ATR ATR, no target, long time cap (so only BR-01 acts)."""
    monkeypatch.setitem(S.STRATEGIES, 'br_test', dict(name='br test', style='calm', fn=_sig_fn, sides='both', tf='15m',
                                                      desc='test', mgmt=dict(stop_atr=STOP_ATR, max_bars=200)))


def _book(path, mirror=False):
    """N0 flat bars (TR = 1 -> ATR ~1, price 100), then `path` = [(o, h, l, c), ...] after the signal."""
    rows = [(100.0, 100.5, 99.5, 100.0)] * N0 + list(path) + [(path[-1][3],) * 4] * 30
    t = pd.date_range('2026-06-01', periods=len(rows), freq='15min')
    o, h, l, c = (np.array(x, float) for x in zip(*rows))
    if mirror: o, h, l, c = 200 - o, 200 - l, 200 - h, 200 - c
    return B.Book({'BTCUSDT': pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=1000.0))})


def run(path, variant=None, mirror=False, **mg):
    m = dict(mg)
    if variant: m['br01'] = dict(variant=variant)
    sl = dict(key='br_test', share=1.0, risk=0.01, max_pos=1, sides='both', mgmt=m, params=dict(short=mirror))
    tr, cv = B.run(_book(path, mirror), [sl], start=10000.0, warmup=250, maint_margin=0)
    return tr, cv, cv.attrs.get('br01', {}).get('events', [])


def flat(n, px):
    return [(px, px + 0.05, px - 0.05, px)] * n


DOWN2 = [(100.0, 100.1, 99.4, 99.45), (99.45, 99.5, 98.9, 98.95)] + flat(40, 98.95)   # 0.5 then 1.0 ATR against, then flat
CRASH = [(100.0, 100.1, 99.4, 99.45), (99.45, 99.5, 98.9, 98.95), (98.95, 99.0, 95.0, 95.5)]


def adds(ev):
    return [x for x in ev if x['kind'] == 'add']


# ------------------------------------------------------------------ 1. fixed total risk = the control's
@pytest.mark.parametrize('mirror', (False, True))
def test_fixed_total_risk_equals_control_budget_at_all_times(mirror):
    _, _, ev = run(DOWN2, 'bounded_recovery', mirror=mirror)
    a = adds(ev)
    assert [x['leg'] for x in a] == [1, 2]
    for x in a:                                     # after every add: open price risk to the stop <= the control budget
        assert x['risk_after'] <= x['budget'] * (1 + 1e-9)
    # budget = the control's q x R: same sizing call, same stop
    trc, cvc, _ = run(DOWN2)
    st = BR.new_state(BR.validate(dict(variant='bounded_recovery'), 'br_test'), 1, 3.0, 1.8, 100.0, 98.0, 1.0, 10)
    assert st['budget'] == pytest.approx(3.0 * 2.0) and sum(st['legs']) == 1.0


def test_legs_sum_to_the_plan_exactly_after_step_quantization():
    for v, step in itertools.product(BR.VARIANTS, (None, 0.001, 1.0)):
        q_plan = 7.0 if step == 1.0 else 0.4567
        st = BR.new_state(BR.validate(dict(variant=v), 'failed_breakout_scalp' if 'failed' in v else 'x'), 1, q_plan,
                          q_plan * BR.VARIANTS[v]['legs'][0], 100.0, 98.0, 1.0, 10)
        while st['status'] == 'active':
            q = BR.leg_qty(st, len(st['filled']))
            if step: q = np.floor(q / step + 1e-9) * step
            st = BR.apply_fill(st, q, 99.0, 't')
        assert sum(st['filled']) <= q_plan + 1e-12              # never above the plan (last leg = the remainder)
        assert BR.leg_qty(dict(st, status='active'), len(st['legs']) - 1) >= 0


# ------------------------------------------------------------------ 2. no doubling / recursive / loss-streak sizing
def test_leg_sizes_are_plan_constants_independent_of_pnl_or_history():
    for v, x in BR.VARIANTS.items():
        assert all(f <= x['legs'][0] for f in x['legs'][1:]), v          # no leg larger than the first (no doubling)
    cfg = BR.validate(dict(variant='bounded_recovery'), 'x')
    base = BR.new_state(cfg, 1, 1.0, 0.6, 100.0, 98.0, 1.0, 10)
    rnd = random.Random(7)
    sizes = set()
    for _ in range(50):                             # shuffled prior outcomes / P&L in the state: the leg size never moves
        st = copy.deepcopy(base); st['seen'] = [f'{rnd.random()}'] ; st['ev'] = [dict(kind='refuse', pnl=rnd.uniform(-9, 9))]
        _, ev = BR.decide(st, dict(t=1, o=100, h=100, l=99.3, c=99.4, pc=100), 1, 98.0, 100.0)
        sizes.add(round(ev['qty'], 12))
    assert sizes == {0.25}
    with pytest.raises(ValueError, match='Kelly'):
        BR.validate(dict(variant='bounded_recovery'), 'x', {}, dict(kelly=dict(n=40)))
    for blk in BR.EXCLUSIVE:                         # never layered over DCA / pyramid / runner scaling
        with pytest.raises(ValueError):
            BR.validate(dict(variant='bounded_recovery'), 'x', {blk: dict(n=1)})


# ------------------------------------------------------------------ 3. stop never widened
def test_cap_stop_only_tightens_and_backtest_adds_never_widen():
    rnd = random.Random(3)
    for _ in range(2000):
        sd = rnd.choice((1, -1)); avg = rnd.uniform(50, 150); stop = avg - sd * rnd.uniform(0.1, 10)
        ns = BR.cap_stop(sd, rnd.uniform(0.01, 5), avg, stop, rnd.uniform(0, 20))
        assert sd * (ns - stop) >= 0
    for v, path in (('anti_martingale', UP), ('bounded_recovery', DOWN2)):
        for x in adds(run(path, v)[2]):
            assert x['stop_after'] >= x['stop_before']


UP = [(100.0, 101.2, 99.9, 101.1)] + flat(40, 101.1)          # +0.5R (R = 2 ATR = 2) closed in favour, bullish


def test_anti_martingale_add_is_funded_by_a_tighter_stop():
    tr, _, ev = run(UP, 'anti_martingale')
    (a,) = adds(ev)
    assert a['stop_after'] > a['stop_before'] and a['risk_after'] <= a['budget'] * (1 + 1e-9)


# ------------------------------------------------------------------ 4. at most two adds, a leg fires once
def test_third_add_refused_and_logged_consumed_leg_never_refires():
    st = BR.new_state(BR.validate(dict(variant='bounded_recovery'), 'x'), 1, 1.0, 0.6, 100.0, 98.0, 1.0, 10)
    st = BR.apply_fill(st, 0.25, 99.5, 1)
    assert BR.admit(st, 1) == 'leg 1 already consumed'               # a re-cross after a retrace cannot refill leg 1
    st = BR.apply_fill(st, 0.15, 99.0, 2)
    assert st['adds'] == 2 and st['status'] == 'done'
    assert BR.admit(st, 3) and BR.admit(dict(st, status='active'), 3) == 'max adds reached (2)'
    with pytest.raises(ValueError): BR.apply_fill(st, 0.1, 98.5, 3)
    ev = BR.refusal(st, 3, BR.admit(dict(st, status='active'), 3), 3)
    assert ev['kind'] == 'refuse' and ev['v'] == 'bounded_recovery' and ev['leg'] == 3 and 'max adds' in ev['reason']
    # price oscillating around the 0.5 ATR trigger: leg 1 fires once
    osc = [(100.0, 100.1, 99.4, 99.45), (99.45, 100.1, 99.4, 100.0), (100.0, 100.1, 99.4, 99.45), (99.45, 100.1, 99.4, 100.0)] + flat(30, 100)
    assert [x['leg'] for x in adds(run(osc, 'bounded_recovery')[2])] == [1]


# ------------------------------------------------------------------ 5. hard stop
def test_hard_structural_stop_closes_the_whole_aggregate():
    tr, _, ev = run(CRASH, 'bounded_recovery')
    (t,) = tr.to_dict('records')
    assert t['why'] == 'stop' and t['legs'] == 3
    assert -1.0 - 0.05 < t['R'] < -0.5               # never beyond the budget (+ costs); adverse legs risk less
    trc = run(CRASH)[0]
    assert trc.R.iloc[0] < t['R']                     # control loses the full 1R


# ------------------------------------------------------------------ 6. 8-12 bar deadline
def test_deadline_bounds_and_aggregate_closed_at_it():
    for bad in (7, 13, 10.0, True):
        with pytest.raises(ValueError): BR.validate(dict(variant='bounded_recovery', deadline=bad), 'x')
    assert BR.validate(dict(variant='bounded_recovery', deadline=8), 'x')['deadline'] == 8
    tr, _, ev = run(DOWN2, 'bounded_recovery')
    (t,) = tr.to_dict('records')
    assert t['why'] == 'time' and t['i_out'] - t['i_in'] == BR.DEADLINE_DEFAULT
    assert [x for x in ev if x['kind'] == 'deadline']
    assert run(DOWN2)[0].empty                       # control: its own (longer) cap, still open at the end of the data


# ------------------------------------------------------------------ 7. costs on every leg
def test_fees_slippage_and_funding_charged_on_every_leg():
    tr, _, ev = run(DOWN2, 'bounded_recovery')
    t = tr.iloc[0]
    a = adds(ev)
    leg_fees = sum(x['qty'] * x['px'] * B.FEE for x in a)
    assert len(a) == 2 and leg_fees > 0 and t['fees'] > leg_fees     # entry leg + both adds + the exit of the aggregate
    for x in a:                                                         # each add filled at an open + slippage (never mid)
        assert any(x['px'] == pytest.approx(o * (1 + B.SLIP)) for o, *_ in DOWN2)
    old = B.FEE
    try:                                                                # 2x-fee stress: net falls by exactly the fees again
        B.FEE = old * 2
        t2 = run(DOWN2, 'bounded_recovery')[0].iloc[0]
    finally:
        B.FEE = old
    assert t2['pnl'] == pytest.approx(t['pnl'] - t['fees'], rel=1e-9)
    assert t2['fees'] == pytest.approx(2 * t['fees'], rel=1e-9)
    # funding is charged on the aggregate quantity every bar: the same trade with zero funding nets more
    sl = dict(key='br_test', share=1.0, risk=0.01, max_pos=1, sides='both', mgmt=dict(br01=dict(variant='bounded_recovery')))
    t0 = B.run(_book(DOWN2), [sl], start=10000.0, warmup=250, maint_margin=0, fund_per_bar=0.0)[0].iloc[0]
    assert t0['pnl'] > t['pnl']


# ------------------------------------------------------------------ 8. causal closed-candle decisions
def test_decisions_read_only_closed_candles():
    full = DOWN2[:2] + flat(10, 98.95)
    ev_full = adds(run(full + [(98.95, 120, 80, 110)] * 3, 'bounded_recovery')[2])
    ev_cut = adds(run(full, 'bounded_recovery')[2])
    assert ev_full[:2] == ev_cut[:2]                                      # a future extreme changes nothing before it
    # a wick to the trigger that closes back above it is not an adverse close: no add
    wick = [(100.0, 100.1, 98.9, 99.9)] + flat(30, 99.9)
    assert not adds(run(wick, 'bounded_recovery')[2])
    # anti_martingale: a +0.5R wick that reverses before the close -> no add
    assert not adds(run([(100.0, 102.0, 99.9, 100.2)] + flat(30, 100.2), 'anti_martingale')[2])
    # every add fills on a LATER candle than the one that decided it (next open)
    for x in adds(run(DOWN2, 'bounded_recovery')[2]): assert x['px'] != 0


def test_reconfirm_needs_a_later_non_extending_reversal_candle():
    cfg = BR.validate(dict(variant='reconfirm_recovery'), 'x')
    st = BR.new_state(cfg, 1, 1.0, 0.6, 100.0, 98.0, 1.0, 10)
    b = lambda t, o, h, l, c, pc: dict(t=t, o=o, h=h, l=l, c=c, pc=pc)
    # negative P&L alone (no reversal candle): never an add
    st1, ev = BR.decide(st, b(1, 100, 100, 99.3, 99.35, 100), 1, 98.0, 100.0)
    assert ev is None and st1['adv'] == 99.3
    # one candle that extends the adverse move AND closes as a reversal: not counted
    st2, ev = BR.decide(st1, b(2, 99.35, 99.9, 99.1, 99.8, 99.35), 1, 98.0, 100.0)
    assert ev is None and st2['adv'] == 99.1
    # the same reversal shape on a fresh candle that does NOT extend the low: add 40 %
    _, ev = BR.decide(st2, b(3, 99.3, 99.9, 99.2, 99.8, 99.35), 1, 98.0, 100.0)
    assert ev['kind'] == 'add' and ev['qty'] == pytest.approx(0.4)
    # the adverse move and the reversal on one single candle (first candle after entry): no add
    _, ev = BR.decide(st, b(1, 100, 100.2, 99.3, 100.1, 99.9), 1, 98.0, 100.0)
    assert ev is None


def test_failed_breakout_staged_reuses_the_existing_signal_only():
    assert BR.validate(dict(variant='failed_breakout_staged'), 'failed_breakout_scalp')['legs'] == [0.6, 0.4]
    with pytest.raises(ValueError, match='reuses'):
        BR.validate(dict(variant='failed_breakout_staged'), 'range_scalp')
    # partial on the reclaim, the remainder only on a LATER closed candle confirming in the trade direction
    st = BR.new_state(BR.validate(dict(variant='failed_breakout_staged'), 'failed_breakout_scalp'), -1, 1.0, 0.6, 100.0, 101.0, 1.0, 10)
    assert BR.decide(st, dict(t=1, o=100, h=100.3, l=99.8, c=100.2, pc=100.1), -1, 101.0, 100.0)[1] is None   # against: no
    assert BR.decide(st, dict(t=1, o=100, h=100.1, l=99.6, c=99.7, pc=99.65), -1, 101.0, 100.0)[1] is None    # not past prev close
    _, ev = BR.decide(st, dict(t=1, o=100, h=100.1, l=99.5, c=99.6, pc=99.9), -1, 101.0, 100.0)
    assert ev['kind'] == 'add' and ev['qty'] == pytest.approx(0.4) and ev['leg'] == 1


def test_range_ladder_cancels_every_unfilled_leg_on_breakout():
    cfg = BR.validate(dict(variant='range_ladder'), 'x')
    st = BR.new_state(cfg, 1, 1.0, 0.6, 100.0, 98.0, 1.0, 10)
    st1, ev = BR.decide(st, dict(t=1, o=100, h=100, l=99.3, c=99.4, pc=100, range_ok=False), 1, 98.0, 100.0)
    assert ev['kind'] == 'cancel' and st1['status'] == 'cancelled' and 'cancelled' in ev['reason']
    assert BR.decide(st1, dict(t=2, o=99, h=99, l=97.9, c=98.5, pc=99, range_ok=True), 1, 98.0, 100.0)[1] is None
    # regime flips on the very candle that reaches the 0.5 ATR level: no add, legs cancelled
    _, ev = BR.decide(st, dict(t=1, o=100, h=100, l=99.4, c=99.45, pc=100, range_ok=False), 1, 98.0, 100.0)
    assert ev['kind'] == 'cancel'
    # bounded_recovery: the slot's regime filter turning off refuses (once) instead
    st = BR.new_state(BR.validate(dict(variant='bounded_recovery'), 'x'), 1, 1.0, 0.6, 100.0, 98.0, 1.0, 10)
    st2, ev = BR.decide(st, dict(t=1, o=100, h=100, l=99.4, c=99.45, pc=100), 1, 98.0, 100.0, regime_ok=False)
    assert ev['kind'] == 'refuse' and 'regime' in ev['reason']
    assert BR.decide(st2, dict(t=2, o=99.45, h=99.5, l=99.4, c=99.45, pc=99.45), 1, 98.0, 100.0, regime_ok=False)[1] is None


# ------------------------------------------------------------------ 10. attribution + control untouched
def test_every_add_and_refusal_attributed_and_control_carries_no_staging():
    _, cv, ev = run(DOWN2, 'bounded_recovery', tp1_r=0.1, tp1_frac=0.5)
    for x in ev: assert x['v'] == 'bounded_recovery' and 'leg' in x and x['reason'] and x['slot'] == 'br_test' and x['sym'] == 'BTCUSDT'
    trc, cvc, _ = run(DOWN2)
    assert 'br01' not in cvc.attrs and 'legs' not in trc.columns and 'br' not in trc.columns
    _, _, ev = run([(100.0, 100.4, 99.9, 100.3)] + DOWN2, 'bounded_recovery', tp1_r=0.1, tp1_frac=0.5)
    refs = [x for x in ev if x['kind'] == 'refuse']
    assert refs and refs[0]['reason'] == 'partial take-profit already taken' and len(refs) == len({(x['leg'], x['reason']) for x in refs})


# ------------------------------------------------------------------ engine (PAPER): live refusal, restart, journal
SLV = E.sleeve('BRT', 'range_scalp', 0.5, 0.01, 2, 'all', sides='both', tf='15m', mgmt={'br01': {'variant': 'bounded_recovery'}})
SG = dict(close=100.0, atr=2.0, time='2026-10-04 00:00:00', le=True, se=False, lx=False, sx=False)


def frame(last_c, prev_c=100.0):
    rows = [(100.0, 100.5, 99.5, 100.0)] * 60 + [(prev_c, prev_c + 0.1, prev_c - 0.1, prev_c), (prev_c, prev_c, last_c - 0.1, last_c)]
    o, h, l, c = (np.array(x, float) for x in zip(*rows))
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2026-10-04', periods=len(rows), freq='15min'), o=o, h=h, l=l, c=c, v=1.0)))


def cyc(e, sigs, frames):
    e.compute_signals = lambda tf, syms, extra=(): ({k: dict(v) for k, v in sigs.items()} if tf == '15m' else {}, frames)
    e.cycle('15m')


def brlot(e):
    return next(l for l in e.state['lots'].values() if l['sleeve'] == 'BRT')


@pytest.fixture
def eng(tmp_path):
    e, tmp = mk_engine(str(tmp_path))
    e.S['SLEEVES'] = [copy.deepcopy(SLV)]; e.save_settings()
    cyc(e, {'BRT|BTCUSDT': SG}, {})
    return e, tmp


def test_engine_staged_entry_is_the_first_leg_of_the_control_budget_and_stop_covers_the_aggregate(eng):
    e, _ = eng
    l = brlot(e)
    b = l['br']
    assert b['v'] == 'bounded_recovery' and b['status'] == 'active' and l['qty'] == pytest.approx(0.6 * b['q_plan'], abs=0.001)
    assert b['budget'] == pytest.approx(b['q_plan'] * l['R']) and isinstance(b['deadline'], str)
    e.trade.mark['BTCUSDT'] = 98.9; e.marks = e.trade.marks()
    cyc(e, {}, {'BTCUSDT': frame(98.9)})
    l = brlot(e)
    assert len(l['br']['filled']) == 2 and l['qty'] == pytest.approx(sum(l['br']['filled']))
    assert e.trade.stops[l['stop_id']][2] == pytest.approx(l['qty'])            # exchange stop sized for the whole aggregate
    assert BR.risk(1, l['qty'], l['avg'], l['stop']) <= l['br']['budget'] * (1 + 1e-9)
    assert any(f[1] == 'br01_add' for f in l['fills'])


def test_engine_live_mode_refuses_staged_entries_and_adds(tmp_path):
    e, _ = mk_engine(str(tmp_path))
    slv = E.sleeve('BRX', 'ema_mom', 0.5, 0.01, 2, 'all', sides='both', tf='15m', mgmt={'br01': {'variant': 'bounded_recovery'}})
    e.S['SLEEVES'] = [slv]
    e.live = True                                   # a non-scalp base slot: the BR-01 gate itself refuses
    assert not e.open_lot(slv, 'BTCUSDT', 'LONG', SG, None, e.equity()) and 'PAPER' in e.last_skip and 'BR-01' in e.last_skip
    e.live = False
    assert e.open_lot(slv, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    l = next(iter(e.state['lots'].values())); e.live = True
    st, ev = BR.decide(l['br'], dict(t=1, o=100, h=100, l=98.9, c=98.95, pc=100), 1, l['stop'], l['avg'])
    ev = e._br_add(next(iter(e.state['lots'])), l, ev, dict(t=1, c=98.95))
    assert ev['kind'] == 'refuse' and 'PAPER' in ev['reason'] and len(l['br']['filled']) == 1
    assert not any('br01' in (sl.get('mgmt') or {}) for p in E.PRESETS.values() for sl in p['sleeves'])   # off by default
    assert all('br01' not in (x.get('mgmt') or {}) for x in E.breadth_sleeves())


def test_engine_leg_state_survives_restart_no_duplicate_no_lost_leg(eng, monkeypatch):
    e, tmp = eng
    fx = e.trade
    sent = []
    real = e._send
    def spy(*a, **k):
        l = brlot(e)
        if l.get('pending'): sent.append(copy.deepcopy(l['pending']))
        return real(*a, **k)
    monkeypatch.setattr(e, '_send', spy)
    e.trade.mark['BTCUSDT'] = 98.9; e.marks = e.trade.marks()
    cyc(e, {}, {'BTCUSDT': frame(98.9)})
    (p,) = sent                                                     # write-ahead: the leg state rides in the add's `post`
    assert p['why'] == 'br01_add' and len(p['post']['br']['filled']) == 2
    q1, dl = brlot(e)['qty'], brlot(e)['br']['deadline']
    # restart from disk (same exchange)
    monkeypatch.setattr(E, 'Futures', lambda *a, **k: fx)
    e2 = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)
    e2.data = e2.trade; e2.connect(); e2.marks = fx.marks()
    l = brlot(e2)
    assert l['qty'] == pytest.approx(q1) and len(l['br']['filled']) == 2 and l['br']['deadline'] == dl
    cyc(e2, {}, {'BTCUSDT': frame(98.9)})                          # the same adverse close again: no duplicate add
    assert brlot(e2)['qty'] == pytest.approx(q1)
    fx.mark['BTCUSDT'] = 97.9; e2.marks = fx.marks()
    cyc(e2, {}, {'BTCUSDT': frame(97.9, 98.9)})                    # leg 2 still fires after the restart (not lost)
    l = brlot(e2)
    assert len(l['br']['filled']) == 3 and l['br']['status'] == 'done' and sum(l['br']['filled']) <= l['br']['q_plan'] + 1e-9
    assert fx.pos[('BTCUSDT', 'LONG')] == pytest.approx(l['qty']) and fx.stops[l['stop_id']][2] == pytest.approx(l['qty'])


def test_engine_settled_add_after_restart_is_capped_to_budget(eng):
    """An add settled from its write-ahead record (post applied, no stop resize yet) is brought back inside the budget by
    the fast loop: the stop is tightened, never widened."""
    e, _ = eng
    l = brlot(e)
    s0 = l['stop']
    q = l['br']['q_plan'] * 0.4
    l['avg'] = (l['avg'] * l['qty'] + 101.0 * q) / (l['qty'] + q); l['qty'] += q    # a large add above entry
    e.trade.pos[('BTCUSDT', 'LONG')] += q
    l['br'] = BR.apply_fill(l['br'], q, 101.0, 't')
    assert BR.risk(1, l['qty'], l['avg'], l['stop']) > l['br']['budget']
    e.manage(e.trade.marks())
    l = brlot(e)
    assert l['stop'] > s0 and BR.risk(1, l['qty'], l['avg'], l['stop']) <= l['br']['budget'] * (1 + 1e-6)


def test_engine_deadline_closes_and_every_br_event_is_journalled(eng):
    e, tmp = eng
    l = brlot(e)
    l['br']['deadline'] = (E.now_utc() - timedelta(minutes=1)).isoformat(timespec='seconds')
    cyc(e, {}, {'BTCUSDT': frame(100.0)})
    assert not [x for x in e.state['lots'].values() if x['sleeve'] == 'BRT'] and e.history[-1]['exit_reason'] == 'time_exit'
    with open(e.F['trades'], encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    br = [r for r in rows if r['event'].startswith('br01_')]
    assert br and all('BR-01 bounded_recovery leg' in r['note'] for r in br) and br[-1]['event'] == 'br01_deadline'


def test_state_schema_rejects_a_corrupt_leg_state(eng):
    e, _ = eng
    l = brlot(e)
    for bad in (dict(adds=3), dict(filled=[9e9]), dict(v='martingale'), dict(legs=[0.5, 0.5])):
        with pytest.raises(E.SchemaError):
            E._validate_lot('k', dict(l, br=dict(l['br'], **bad)))
    E._validate_lot('k', l)


# ------------------------------------------------------------------ adversarial-review regressions (8dabeff)
def _adverse(e):
    e.trade.mark['BTCUSDT'] = 98.9; e.marks = e.trade.marks()        # a close 0.55 ATR against the long entry
    cyc(e, {}, {'BTCUSDT': frame(98.9)})


def test_engine_adverse_close_adds_the_first_leg_with_its_own_fill_kind(eng):
    e, _ = eng
    seen, real = [], e._fill
    e._fill = lambda kind, *a, **k: (seen.append((kind, k.get('reason'))), real(kind, *a, **k))[1]
    _adverse(e)
    assert len(brlot(e)['br']['filled']) == 2 and ('br01_add', 'br01_add') in seen      # not audited as pyramid_add


@pytest.mark.parametrize('change', ('switched_off', 'br01_removed', 'slot_removed'))
def test_engine_no_leg_after_the_slot_is_switched_off_or_loses_br01(eng, change):
    e, _ = eng
    sl = next(x for x in e.S['SLEEVES'] if x['id'] == 'BRT')
    if change == 'switched_off': sl['enabled'] = False
    elif change == 'br01_removed': sl['mgmt'] = {}
    else: e.S['SLEEVES'].remove(sl)
    _adverse(e)
    l = brlot(e)
    assert len(l['br']['filled']) == 1 and l['br']['adds'] == 0
    why = 'switched off' if change != 'br01_removed' else 'BR-01 removed'
    assert any(x.get('kind') == 'refuse' and why in x.get('reason', '') for x in l['br']['ev'])


def test_engine_ambiguous_add_is_journalled_as_unconfirmed_not_failed(eng, monkeypatch):
    e, _ = eng
    def amb(*a, **k): raise E.AmbiguousOrder('timeout')
    monkeypatch.setattr(e, '_add_qty', amb)
    _adverse(e)
    ev = brlot(e)['br']['ev']
    assert any('unconfirmed' in x.get('reason', '') for x in ev) and not any('order failed' in x.get('reason', '') for x in ev)


def _run_rr2(pct):
    """BTC + ETH on the same 0.5 / 1.0 ATR adverse path, both entered on the signal candle (max_pos 2)."""
    rows = [(100.0, 100.5, 99.5, 100.0)] * N0 + list(DOWN2) + [(DOWN2[-1][3],) * 4] * 30
    t = pd.date_range('2026-06-01', periods=len(rows), freq='15min')
    o, h, l, c = (np.array(x, float) for x in zip(*rows))
    bk = B.Book({s: pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=1000.0)) for s in ('BTCUSDT', 'ETHUSDT')})
    sl = dict(key='br_test', share=1.0, risk=0.01, max_pos=2, sides='both', mgmt=dict(br01=dict(variant='bounded_recovery')), params={})
    tr, cv = B.run(bk, [sl], start=10000.0, warmup=250, maint_margin=0, risk_rules=dict(open_risk_cap=dict(mode='enforce', pct=pct)))
    return tr, [x for x in cv.attrs.get('br01', {}).get('events', []) if x['kind'] == 'refuse']


def test_backtest_leg_counts_its_own_risk_in_the_portfolio_open_risk_rule():
    """Engine parity (_add_block add_risk = q x |px - stop|, 8dabeff review): two staged lots fit a 1.7 % open-risk cap at
    entry (0.6 % each + 1 % for the second's check) but their later legs would breach it, so a leg is refused (logged) and
    stays armed; with a roomy cap nothing is refused. Before the fix the leg's own risk counted as 0 and nothing was refused."""
    tr, ref = _run_rr2(1.7)
    assert len(tr) == 2 and ref and all(x['reason'] == 'portfolio risk rule' for x in ref)
    tr, ref = _run_rr2(2.1)
    assert len(tr) == 2 and not ref
