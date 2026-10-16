"""SCALP-4: four EXPERIMENTAL / PAPER-only 15m scalp strategies + their independent breadth-rotation slots.

Signals: each fires LONG on a designed long setup and SHORT on its exact price mirror, not on a near-miss, reads only closed
candles (a future extreme candle changes nothing before it), returns finite values, and never takes both sides on one candle.
Engine: the scalp slots are NOT rotated (existing groups / schedule pinned byte-identical), each is switched by its own
`enabled` flag, PAPER only (refused in live mode), absent from every other profile, and added to an installed bot's saved
rotation slots without touching the existing ones. Synthetic data only (tests/scalp_synth.py, tests/test_safety.FakeX)."""
import copy
import hashlib
import json
import os
import sys
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_safety import mk_engine                       # noqa: E402  (FakeX engine, PAPER)
import engine as E                                       # noqa: E402
import scalp_synth as Y                                  # noqa: E402
import strategies as S                                   # noqa: E402

KEYS = ('range_scalp', 'failed_breakout_scalp', 'momentum_burst_scalp', 'quick_bank_runner')
SETUP = {'range_scalp': Y.range_long, 'failed_breakout_scalp': Y.failed_breakout_long,
         'momentum_burst_scalp': Y.momentum_long, 'quick_bank_runner': Y.qbank_long}


def sig(key, raw):
    d = S.indicators(raw)
    return S.signals(key, d, dict(btc_down=np.zeros(len(d), bool)), mask_sides=False), d


# ------------------------------------------------------------------ registry contract
def test_registry_entries_are_labelled_paper_experimental_symmetric_15m():
    assert E.SCALP_KEYS == set(KEYS)
    for k in KEYS:
        st = S.STRATEGIES[k]
        assert st['sides'] == 'both' and st['tf'] == '15m' and 'PAPER' in st['name'] and S.SCALP_TAG in st['desc']
        g = st['mgmt']
        assert g['stop_atr'] > 0 and g['max_bars'] <= 16 and (g.get('tp_r') or g.get('tp1_r'))   # stop + target + short cap
        for word in ('edge', 'profit', 'proven'): assert word not in st['desc'].lower(), (k, word)
        assert set(g) <= {'stop_atr', 'tp_r', 'max_bars', 'tp1_r', 'tp1_frac', 'be_r'}             # keys manage() honours
    qb = S.STRATEGIES['quick_bank_runner']['mgmt']
    assert qb['tp1_frac'] >= 0.6 and qb['tp1_r'] <= 1.0 and qb['be_r'] <= qb['tp1_r'] and qb['tp_r'] > qb['tp1_r']


# ------------------------------------------------------------------ signals
@pytest.mark.parametrize('key', KEYS)
def test_fires_long_on_setup_and_short_on_mirror(key):
    raw = SETUP[key]()
    r, _ = sig(key, raw)
    m, _ = sig(key, Y.mirror(raw))
    assert r['le'][-1] and not r['se'][-1]
    assert m['se'][-1] and not m['le'][-1]


def _near_range():
    """The same range-edge candle, but bearish (open and close swapped): no long."""
    x = Y.range_long(); i = x.index[-1]
    x.loc[i, ['o', 'c']] = [x.c.iloc[-1], x.o.iloc[-1]]
    return x


def _near_failed():
    """The break below the range low is never closed back inside (the last candle closes lower still)."""
    x = Y.failed_breakout_long(); i = x.index[-1]
    p = x.c.iloc[-2]
    x.loc[i, ['o', 'h', 'l', 'c']] = [p, p + 0.1, p - 0.3, p - 0.2]
    return x


def _near_momentum():
    """Impulse range, ordinary volume."""
    x = Y.momentum_long()
    x.loc[x.index[-1], 'v'] = x.v.iloc[-21:-1].mean()
    return x


def _near_qbank():
    """Bullish close above EMA9, but the candle never dips to EMA9."""
    x = Y.qbank_long(); i = x.index[-1]
    x.loc[i, ['o', 'l']] = [x.c.iloc[-1] - 0.01, x.c.iloc[-1] - 0.01]
    return x


NEAR_MISS = {'range_scalp': _near_range, 'failed_breakout_scalp': _near_failed, 'momentum_burst_scalp': _near_momentum,
             'quick_bank_runner': _near_qbank}


@pytest.mark.parametrize('key', KEYS)
def test_no_entry_on_a_near_miss(key):
    raw = NEAR_MISS[key]()
    r, _ = sig(key, raw)
    m, _ = sig(key, Y.mirror(raw))
    assert not r['le'][-1] and not r['se'][-1] and not m['le'][-1] and not m['se'][-1]


@pytest.mark.parametrize('key', KEYS)
def test_closed_candles_only_no_lookahead(key):
    raw = SETUP[key]()
    base, _ = sig(key, raw)
    for ext in ((1e6, 1e6, 1.0, 1e6), (1.0, 1e6, 0.5, 0.5)):                 # a future candle with extreme values
        fut = Y.append(raw, *ext, v=1e9)
        r, _ = sig(key, fut)
        for k_ in ('le', 'se', 'lx', 'sx'):
            assert np.array_equal(r[k_][:len(raw)], base[k_]), (key, k_)
        if 'atr' in base:
            assert np.allclose(r['atr'][:len(raw)], base['atr'], equal_nan=True)
    for cut in (len(raw) - 40, len(raw) - 1):                                  # prefix causality
        r, _ = sig(key, raw.iloc[:cut].reset_index(drop=True))
        for k_ in ('le', 'se', 'lx', 'sx'): assert np.array_equal(r[k_], base[k_][:cut]), (key, k_, cut)


@pytest.mark.parametrize('key', KEYS)
def test_outputs_finite_bool_and_never_both_sides(key):
    for raw in (SETUP[key](), Y.mirror(SETUP[key]()), Y.range_base(), Y.trend_base(), Y.mirror(Y.trend_base())):
        r, d = sig(key, raw)
        for k_ in ('le', 'se', 'lx', 'sx'): assert r[k_].dtype == bool and len(r[k_]) == len(d)
        assert not (r['le'] & r['se']).any()
        if 'atr' in r:
            ent = r['le'] | r['se']
            assert np.isfinite(r['atr'][ent]).all() and (r['atr'][ent] > 0).all()


@pytest.mark.parametrize('key', ('range_scalp', 'momentum_burst_scalp', 'quick_bank_runner'))
def test_ambiguous_doji_takes_no_side(key):
    """A doji (close == open) at the setup is ambiguous for the candle-direction rules: documented choice = no entry."""
    raw = SETUP[key]()
    raw.loc[raw.index[-1], 'o'] = raw.c.iloc[-1]
    r, _ = sig(key, raw)
    assert not r['le'][-1] and not r['se'][-1]


def test_failed_breakout_outside_candle_takes_the_close_side_only():
    """An outside candle piercing BOTH range edges after a close below the range: documented single choice - the close
    decides (back inside after the downside break -> long only), never both."""
    raw = Y.failed_breakout_long()
    i = raw.index[-1]
    raw.loc[i, 'h'] = raw.h.iloc[-30:].max() + 2.0
    r, _ = sig('failed_breakout_scalp', raw)
    assert r['le'][-1] and not r['se'][-1]


def test_one_side_wrapper_drops_a_candle_with_both_entries():
    t = pd.Series([True, True, False]); f = pd.Series([True, False, False])
    s = S._one_side(dict(le=t, se=f, lx=f, sx=f))
    assert list(s['le']) == [False, True, False] and list(s['se']) == [False, False, False]


def test_failed_breakout_stop_beyond_the_break_extreme():
    raw = Y.failed_breakout_long()
    r, d = sig('failed_breakout_scalp', raw)
    ext = d.l.iloc[-(S.FB['w'] + 1):].min()
    a = d.atr.iloc[-1]
    want = max(d.c.iloc[-1] - ext + S.FB['buf_atr'] * a, S.FB['min_atr'] * a)
    assert r['atr'][-1] == pytest.approx(want)
    assert d.c.iloc[-1] - S.STRATEGIES['failed_breakout_scalp']['mgmt']['stop_atr'] * r['atr'][-1] < ext   # stop below the low
    m, dm = sig('failed_breakout_scalp', Y.mirror(raw))
    assert m['atr'][-1] == pytest.approx(want)                                 # mirror: same distance, above the high


def test_range_scalp_blocked_after_invalidation():
    b = Y.range_base()
    hi, a = b.h.iloc[-S.RS['n']:].max(), float(S.indicators(b).atr.iloc[-1])
    j = b.index[-6]                                                             # a close 1 ATR+ above the range, 6 candles ago
    b.loc[j, ['o', 'c']] = [b.c.iloc[-7], hi + 1.2 * a + 0.5]
    b.loc[j, 'h'] = b.loc[j, 'c'] + 0.05
    raw = Y.range_long(b)
    r, d = sig('range_scalp', raw)
    assert not r['le'][-1] and not r['se'][-1]
    assert r['lx'][-1] and r['sx'][-1]                                          # invalidated: an open trade exits too
    m, _ = sig('range_scalp', Y.mirror(raw))
    assert not m['le'][-1] and not m['se'][-1]


def test_momentum_burst_cost_gate_blocks_small_atr():
    raw = Y.momentum_long()
    hi_px = Y.shift_price(raw, 2000.0)                                          # same candles, ATR% ~20x smaller
    r, d = sig('momentum_burst_scalp', hi_px)
    assert not r['le'][-1] and not r['se'][-1]
    assert not S.scalp_cost_ok(d).iloc[-1] and S.scalp_cost_ok(S.indicators(raw)).iloc[-1]
    d0 = S.indicators(raw).iloc[-1]                                             # the rule: stop >= cost_x x round trip
    assert S.MB['stop_atr'] * d0.atr >= S.MB['cost_x'] * S.SCALP_RT_COST * d0.c


# ------------------------------------------------------------------ rotation: existing groups / schedule unchanged
FROZEN_GROUPS = [['donchian_ens', 'exp_trend', 'pullback_rsi', 'bear_breakdown', 'exp_structure', 'rotation'],
                 ['ema_mom', 'exp_mom_short', 'exp_range', 'breakout_pyramid', 'squeeze_tp', 'dca_dip'],
                 ['ema_st', 'hot_coin', 'exp_vwaprev', 'exp_breakout', 'exp_scalp', 'exp_rip_sell_dca']]
FROZEN_GROUPS_TRADFI = [['TF-MOM', 'ema_st', 'pullback_rsi', 'TF-BRK', 'exp_structure', 'dca_dip'],
                        ['TF-TREND', 'exp_trend', 'TF-RANGE', 'bear_breakdown', 'squeeze_tp', 'exp_rip_sell_dca'],
                        ['donchian_ens', 'exp_mom_short', 'exp_range', 'breakout_pyramid', 'exp_scalp'],
                        ['ema_mom', 'hot_coin', 'exp_vwaprev', 'exp_breakout', 'rotation']]
# sha256 of json.dumps(..., sort_keys=True), computed on master 9e8d645 (before SCALP-4)
SHA_SLEEVES = '25604c6c979aeb49d5a83071f50d742be162c0e5be63047b06b04f6b6aca7e3f'
SHA_SLEEVES_TRADFI = '0d53b146af107eec49c5eeffdaa5ab4333ac1c93298504171090042a5a901b87'
SHA_SCHEDULE = '7086769a812fb288e46a32150c6941e5e66e4b174c813d01924d634728aa8986'
SHA_SCHEDULE_TRADFI = '58148f01bc64abec71d282c65a2bc51573dd62d5fbd1f857eeef70ae4b2ca0be'


def _sha(o):
    return hashlib.sha256(json.dumps(o, sort_keys=True).encode()).hexdigest()


def test_existing_groups_and_schedule_byte_identical():
    assert E.breadth_groups() == FROZEN_GROUPS and E.breadth_groups(tradfi=True) == FROZEN_GROUPS_TRADFI
    pre = sorted(k for k in S.STRATEGIES if k not in E.SCALP_KEYS)
    assert E.breadth_groups() == E.breadth_groups(pre)                         # = the list computed without the new keys
    assert not E.SCALP_KEYS & {k for g in E.breadth_groups(tradfi=True) for k in g}
    rot = lambda tr: [x for x in E.breadth_sleeves(tradfi=tr) if x['key'] not in E.SCALP_KEYS]
    assert _sha(rot(False)) == SHA_SLEEVES and _sha(rot(True)) == SHA_SLEEVES_TRADFI
    P = E.ROTATION_PERIOD_S
    assert _sha([E.rotation_at(t) for t in range(0, 30 * P, P)]) == SHA_SCHEDULE
    assert _sha([E.rotation_at(t, tradfi=True) for t in range(0, 30 * P, P)]) == SHA_SCHEDULE_TRADFI


def test_scalp_slots_appended_after_the_rotation_slots():
    sl = E.breadth_sleeves()
    assert [x['id'] for x in sl[-4:]] == list(E.SCALP_SLOTS) and [x['key'] for x in sl[-4:]] == list(KEYS)
    for x in sl[-4:]:
        assert x['enabled'] is E.SCALP_DEFAULT_ENABLED and x['tf'] == '15m' and x['sides'] == 'both'
        assert x['symbols'] == 'core8' and x['max_pos'] == E.SCALP_MAX_POS and x['mgmt'] == {}
        assert x['id'] not in E.rotation_at(0)['active'] and len(x['id']) <= 12


# ------------------------------------------------------------------ engine integration
SG = dict(close=100.0, atr=2.0, time='2026-10-04 00:00:00', le=True, se=False, lx=False, sx=False)
QB = 'SC-QBANK'


def slot(e, sid):
    return next(x for x in e.S['SLEEVES'] if x['id'] == sid)


def cycle15(e, sigs):
    e.compute_signals = lambda tf, syms, extra=(): ({k: dict(v) for k, v in sigs.items()} if tf == '15m' else {}, {})
    e.cycle('15m')


def lots(e, sid, sym=None):
    return [l for l in e.state['lots'].values() if l['sleeve'] == sid and (sym is None or l['symbol'] == sym)]


@pytest.fixture
def rot(tmp_path):
    e, tmp = mk_engine(str(tmp_path)); e.apply_preset(E.ROTATION_PRESET)
    return e, tmp


@pytest.mark.parametrize('day_shift', (0, 1, 2))
def test_scalp_slot_opens_paper_lot_with_exchange_stop_in_any_rotation_window(rot, monkeypatch, day_shift):
    e, _ = rot
    real = E.rotation_at
    monkeypatch.setattr(E, 'rotation_at', lambda ts=None, **k: real(day_shift * E.ROTATION_PERIOD_S + 60 if ts is None else ts, **k))
    sid = 'SC-RANGE'
    assert sid not in E.rotation_at()['active'] and e.scalp_block(slot(e, sid)) is None
    cycle15(e, {f'{sid}|BTCUSDT': SG})
    (l,) = lots(e, sid, 'BTCUSDT')
    assert l['stop_id'] in e.trade.stops and e.trade.stops[l['stop_id']][3] == l['stop'] < l['avg']
    assert e.live is False


def test_disabled_scalp_slot_blocks_new_entries_but_its_open_lot_stays_managed(rot):
    e, _ = rot
    cycle15(e, {f'{QB}|BTCUSDT': SG})
    (l,) = lots(e, QB, 'BTCUSDT')
    slot(e, QB)['enabled'] = False
    others = [x for x in e.S['SLEEVES'] if x['key'] in E.SCALP_KEYS and x['id'] != QB]
    assert all(x['enabled'] for x in others)                                    # switched independently
    cycle15(e, {f'{QB}|ETHUSDT': dict(SG, close=50.0, atr=1.0)})
    assert not lots(e, QB, 'ETHUSDT') and e.missed[-1]['reason'] == 'strategy slot switched off'
    assert e.entry_block(slot(e, QB), 'ETHUSDT', 'LONG') == 'strategy slot switched off'
    cycle15(e, {f'{others[0]["id"]}|ETHUSDT': dict(SG, close=50.0, atr=1.0)})  # another scalp slot still enters
    assert lots(e, others[0]['id'], 'ETHUSDT')
    # the disabled slot's open lot: partial + breakeven by manage(), then its own exit signal closes the rest
    R = l['R']; e.trade.mark['BTCUSDT'] = l['e0'] + 0.85 * R
    e.manage(e.trade.marks())
    l = lots(e, QB, 'BTCUSDT')[0]
    assert l['tp1'] and l['stop'] == pytest.approx(l['avg'])
    cycle15(e, {f'{QB}|BTCUSDT': dict(SG, le=False, lx=True, close=l['e0'] + 0.85 * R)})
    assert not lots(e, QB, 'BTCUSDT') and e.history[-1]['exit_reason'] == 'exit_signal'


def test_quick_bank_runner_partial_majority_then_breakeven_then_time_cap(rot):
    e, _ = rot
    cycle15(e, {f'{QB}|BTCUSDT': SG})
    (l,) = lots(e, QB, 'BTCUSDT')
    q0, R, g = l['qty'], l['R'], l['mgmt']
    assert g['tp1_frac'] >= 0.6 and g == S.merge_mgmt('quick_bank_runner')
    e.trade.mark['BTCUSDT'] = l['e0'] + 0.5 * R; e.manage(e.trade.marks())
    assert lots(e, QB)[0]['qty'] == pytest.approx(q0) and not lots(e, QB)[0]['tp1']    # below 0.8R: nothing banked
    e.trade.mark['BTCUSDT'] = l['e0'] + g['tp1_r'] * R + 0.01; e.manage(e.trade.marks())
    l = lots(e, QB, 'BTCUSDT')[0]
    assert l['tp1'] and l['qty'] <= q0 * (1 - g['tp1_frac']) + 0.001 and l['qty'] > 0    # majority banked (one 0.001 step), runner left
    assert l['stop'] == pytest.approx(l['avg']) and e.trade.stops[l['stop_id']][3] == pytest.approx(l['avg'])
    l['opened'] = (E.now_utc() - timedelta(minutes=15 * (g['max_bars'] + 1))).isoformat()
    cycle15(e, {})
    assert not lots(e, QB, 'BTCUSDT') and e.history[-1]['exit_reason'] == 'time_exit'


def test_live_mode_refuses_scalp_entries(rot):
    e, _ = rot
    e.live = True
    for sid in E.SCALP_SLOTS:
        sl = slot(e, sid)
        assert 'PAPER' in e.scalp_block(sl) and 'PAPER' in (e.entry_block(sl, 'BTCUSDT', 'LONG') or '')
    cycle15(e, {f'{QB}|BTCUSDT': SG})
    assert not lots(e, QB) and 'PAPER' in e.missed[-1]['reason']
    assert e.sync_scalp_slots() == []
    # a scalp key on a hand-made slot of another profile is refused in live mode too (fail closed)
    custom = E.sleeve('MYSCALP', 'range_scalp', 0.5, 0.01, 1, 'core8', sides='both', tf='15m')
    assert 'PAPER' in e.scalp_block(custom)


def test_other_presets_unaffected(tmp_path):
    e, _ = mk_engine(str(tmp_path))
    for name in ('original', 'balanced', 'steady_mix'):
        e.apply_preset(name)
        before = copy.deepcopy(e.S['SLEEVES'])
        assert e.sync_scalp_slots() == [] and e.S['SLEEVES'] == before == E.PRESETS[name]['sleeves']
        assert not {x['key'] for x in e.S['SLEEVES']} & E.SCALP_KEYS
        assert all(e.scalp_block(x) is None for x in e.S['SLEEVES'])
    for p in E.PRESETS.values(): assert not {x['key'] for x in p['sleeves']} & E.SCALP_KEYS


def test_installed_rotation_gets_missing_scalp_slots_once_and_existing_slots_byte_identical(tmp_path):
    e, tmp = mk_engine(str(tmp_path)); e.apply_preset(E.ROTATION_PRESET)
    # simulate a pre-SCALP-4 install: saved rotation SLEEVES without the scalp slots, one existing slot customised
    old = [x for x in e.S['SLEEVES'] if x['key'] not in E.SCALP_KEYS]
    old[0]['enabled'] = False; old[1]['share'] = 0.123
    e.S['SLEEVES'] = old; e.save_settings()
    snap = json.dumps(old, sort_keys=True)
    e2 = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)
    e2.data = e2.trade; e2.connect()                                            # restart: connect adds the missing slots
    sl = e2.S['SLEEVES']
    assert json.dumps(sl[:len(old)], sort_keys=True) == snap
    assert [x['id'] for x in sl[len(old):]] == list(E.SCALP_SLOTS)
    assert e2.sync_scalp_slots() == [] and len(e2.S['SLEEVES']) == len(old) + 4  # idempotent
    # persisted: a further restart reads them from disk; an owner-disabled scalp slot stays disabled, never duplicated
    e2.S['SLEEVES'][-1]['enabled'] = False; e2.save_settings()
    e3 = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)
    e3.data = e3.trade; e3.connect()
    assert [x['id'] for x in e3.S['SLEEVES']] == [x['id'] for x in sl] and e3.S['SLEEVES'][-1]['enabled'] is False
    assert json.dumps(e3.S['SLEEVES'][:len(old)], sort_keys=True) == snap
    # a partial set (one scalp slot already present) only gets the other three
    part = old + [E.scalp_sleeves()[1]]
    new, added = E.with_scalp_slots(part)
    assert added == [i for i in E.SCALP_SLOTS if i != 'SC-FAILBRK'] and all(a is b for a, b in zip(new, part))


def test_live_engine_never_adds_scalp_slots(tmp_path):
    e, tmp = mk_engine(str(tmp_path)); e.apply_preset(E.ROTATION_PRESET)
    old = [x for x in e.S['SLEEVES'] if x['key'] not in E.SCALP_KEYS]
    e.S['SLEEVES'] = old; e.save_settings()
    e.live = True
    assert e.sync_scalp_slots() == [] and e.S['SLEEVES'] == old
