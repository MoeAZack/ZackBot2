"""BREADTH-01 B2: automatic PAPER breadth rotation (engine.ROTATION_PRESET).

Codex rulings: a rotation changes only which slots are ELIGIBLE FOR NEW ENTRIES; open lots stay owned and managed by
their original slot (stop, exits, adds) after it rotates out. At most 6 active slots, equal shares. Both sides only where
the signal function already emits both; one-sided strategies stay one-sided. PAPER/testnet only (fail closed in live).
Synthetic only: fake exchange (tests/test_safety.FakeX), stubbed signals, no network, no real data, no optimisation."""
import os, sys
import numpy as np, pandas as pd, pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_safety import mk_engine                     # noqa: E402  (FakeX engine, PAPER)
import engine as E                                     # noqa: E402
import strategies as S                                 # noqa: E402

P = E.ROTATION_PERIOD_S
KEY = 'donchian_ens'                                   # 4h, core 8, both sides (BREADTH_BOTH), trail_atr -> managed stop
SID = E.breadth_id(KEY)


def group_of(key):
    return next(i for i, g in enumerate(E.breadth_groups()) if key in g)


def ts_in(idx, day=0):
    """A unix time inside the window where group `idx` is active (day = how many full cycles later)."""
    n = len(E.breadth_groups())
    return (idx + day * n) * P + 3600


# ------------------------------------------------------------------ schedule / grouping
def test_groups_at_most_six_equal_shares_and_cover_every_strategy():
    groups = E.breadth_groups()
    assert all(1 <= len(g) <= E.ROTATION_MAX_ACTIVE for g in groups)
    assert max(map(len, groups)) - min(map(len, groups)) <= 1
    flat = [k for g in groups for k in g]
    assert sorted(flat) == sorted(S.STRATEGIES) and len(flat) == len(set(flat))      # each strategy in exactly one group
    sl = E.breadth_sleeves()
    assert {x['key'] for x in sl} == set(S.STRATEGIES) and len({x['id'] for x in sl}) == len(sl)
    by_key = {x['key']: x for x in sl}
    for g in groups:
        shares = [by_key[k]['share'] for k in g]
        assert all(abs(s - 1 / len(g)) < 1e-12 for s in shares) and abs(sum(shares) - 1) < 1e-9
    fams = {E.BREADTH_FAMILY.get(k, 'Other') for k in S.STRATEGIES}
    assert set(E.BREADTH_FAMILIES) <= fams                                               # every family gets a turn


def test_families_spread_across_groups():
    for fam in E.BREADTH_FAMILIES:
        keys = [k for k in S.STRATEGIES if E.BREADTH_FAMILY.get(k) == fam]
        n_groups = len({group_of(k) for k in keys})
        assert n_groups == min(len(keys), len(E.breadth_groups())), fam


def test_schedule_deterministic_cycles_all_groups_and_switches_on_the_boundary():
    n = len(E.breadth_groups())
    assert n >= 2
    seen = [E.rotation_at(ts_in(i))['index'] for i in range(n)]
    assert seen == list(range(n))                                                        # every group once per cycle
    assert E.rotation_at(ts_in(0, day=5)) == E.rotation_at(ts_in(0, day=5))              # pure function of time
    r = E.rotation_at(ts_in(1))
    assert r['next_switch'] == pd.Timestamp((1 + 1) * P, unit='s', tz='UTC').isoformat()
    assert E.rotation_at((1 + 1) * P - 1)['index'] == 1 and E.rotation_at((1 + 1) * P)['index'] == 2 % n
    assert len(r['active']) <= 6 and r['share'] == round(1 / len(r['active']), 6)


def test_restart_gives_the_same_group_and_slots(tmp_path):
    e, tmp = mk_engine(str(tmp_path)); e.apply_preset(E.ROTATION_PRESET)
    before = (e.rotation_status(ts_in(1)), [x['id'] for x in e.S['SLEEVES']])
    e2 = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)     # restart: settings from disk
    assert e2.S['PRESET'] == E.ROTATION_PRESET
    assert (e2.rotation_status(ts_in(1)), [x['id'] for x in e2.S['SLEEVES']]) == before
    # settings without SLEEVES (e.g. hand-edited) rebuild the rotation slots instead of a KeyError on PRESETS
    e2.S.pop('SLEEVES'); e2.save_settings()
    e3 = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)
    assert e3.S['PRESET'] == E.ROTATION_PRESET and [x['id'] for x in e3.S['SLEEVES']] == before[1]
    assert E.ROTATION_PRESET not in E.PRESETS                                            # no backtest/UI/governor loop


# ------------------------------------------------------------------ entries vs management
@pytest.fixture
def rot(monkeypatch, tmp_path):
    clock = dict(t=ts_in(group_of(KEY)))
    real = E.rotation_at
    monkeypatch.setattr(E, 'rotation_at', lambda ts=None: real(clock['t'] if ts is None else ts))
    e, _ = mk_engine(str(tmp_path)); e.apply_preset(E.ROTATION_PRESET)
    assert e.S['PRESET'] == E.ROTATION_PRESET and SID in e.rotation_status()['active']
    return e, clock


def sig(le=False, lx=False, close=100.0):
    return dict(close=close, atr=2.0, time='2026-10-04 00:00:00', le=le, se=False, lx=lx, sx=False)


def cycle(e, sigs):
    e.compute_signals = lambda tf, syms, extra=(): ({f'{SID}|{s}': dict(g) for s, g in sigs.items()} if tf == '4h' else {}, {})
    e.cycle('4h')


def lots(e, sym=None):
    return [l for l in e.state['lots'].values() if l['sleeve'] == SID and (sym is None or l['symbol'] == sym)]


def rotate_out(clock):
    clock['t'] = ts_in(group_of(KEY) + 1)
    assert SID not in E.rotation_at()['active']


def test_active_slot_enters_and_rotated_out_slot_gets_no_new_entry(rot):
    e, clock = rot
    cycle(e, {'BTCUSDT': sig(le=True)})
    assert len(lots(e, 'BTCUSDT')) == 1
    rotate_out(clock)
    cycle(e, {'ETHUSDT': sig(le=True, close=50.0)})
    assert not lots(e, 'ETHUSDT')
    assert 'rotation: slot not in the active group' in e.missed[-1]['reason']
    assert 'rotation' in (e.entry_block(next(x for x in e.S['SLEEVES'] if x['id'] == SID), 'ETHUSDT', 'LONG') or '')
    assert len(lots(e, 'BTCUSDT')) == 1                                                  # the open lot is untouched
    other = next(x for x in e.S['SLEEVES'] if x['id'] in E.rotation_at()['active'])
    assert e.rotation_block(other) is None                                               # the new group is eligible


def test_rotated_out_lot_keeps_trailing_stop_and_closes_on_its_exit_signal(rot):
    e, clock = rot
    cycle(e, {'BTCUSDT': sig(le=True)})
    l = lots(e, 'BTCUSDT')[0]; stop0 = l['stop']
    assert l['stop_id'] in e.trade.stops
    rotate_out(clock)
    e.trade.mark['BTCUSDT'] = 130.0; e.manage(e.trade.marks())                           # trailing stop still managed
    l = lots(e, 'BTCUSDT')[0]
    assert l['stop'] > stop0 and e.trade.stops[l['stop_id']][3] == l['stop']
    cycle(e, {'BTCUSDT': sig(lx=True, close=130.0)})                                     # its own exit signal closes it
    assert not lots(e, 'BTCUSDT') and e.history[-1]['sleeve'] == SID
    assert e.history[-1]['exit_reason'] == 'exit_signal'


def test_rotated_out_lot_closed_by_its_stop(rot):
    e, clock = rot
    cycle(e, {'BTCUSDT': sig(le=True)})
    l = lots(e, 'BTCUSDT')[0]
    rotate_out(clock)
    e.trade.stops.pop(l['stop_id']); e.trade.pos[('BTCUSDT', 'LONG')] = 0.0             # the exchange stop filled
    e.reconcile(e.equity())
    assert not lots(e, 'BTCUSDT') and e.history[-1]['sleeve'] == SID


# ------------------------------------------------------------------ PAPER guard
def test_live_mode_refuses_the_preset_and_every_entry(tmp_path):
    e, _ = mk_engine(str(tmp_path))
    e.live = True
    with pytest.raises(ValueError, match='PAPER'):
        e.apply_preset(E.ROTATION_PRESET)
    assert e.S['PRESET'] != E.ROTATION_PRESET
    e.live = False; e.apply_preset(E.ROTATION_PRESET); e.live = True                    # e.g. a config flipped to live later
    act = E.rotation_at()['active'][0]
    sl = next(x for x in e.S['SLEEVES'] if x['id'] == act)
    assert 'PAPER' in e.rotation_block(sl) and 'PAPER' in (e.entry_block(sl, 'BTCUSDT', 'LONG') or '')
    assert e.rotation_status()['live_refused'] is True
    e.apply_preset('original')                                                          # other profiles are unaffected
    assert e.rotation_block(e.S['SLEEVES'][0]) is None


def test_other_presets_ignore_the_rotation(tmp_path):
    e, _ = mk_engine(str(tmp_path))
    assert e.S['PRESET'] != E.ROTATION_PRESET and all(e.rotation_block(x) is None for x in e.S['SLEEVES'])
    assert e.rotation_status()['on'] is False


# ------------------------------------------------------------------ sides
def _tape(n=700, drift=0.0, seed=0, period=40.0):
    rng = np.random.default_rng(seed)
    i = np.arange(n)
    c = 100 * np.exp(drift * i + 0.12 * np.sin(2 * np.pi * i / period) + np.cumsum(rng.normal(0, 0.01, n)))
    o = np.r_[c[0], c[:-1]]
    h, lo = np.maximum(o, c) * 1.004, np.minimum(o, c) * 0.996
    v = rng.uniform(800, 1200, n) * (1 + 2 * (rng.uniform(size=n) > 0.95))
    t = pd.date_range('2024-01-01', periods=n, freq='4h')
    return S.indicators(pd.DataFrame(dict(t=t, o=o, h=h, l=lo, c=c, v=v, qv=v * c)))


def test_both_sides_only_where_designed():
    by_key = {x['key']: x for x in E.breadth_sleeves()}
    for k, st in S.STRATEGIES.items():
        want = 'both' if (k in E.BREADTH_BOTH or st['sides'] == 'both') else st['sides']
        assert by_key[k]['sides'] == want, k
    assert by_key['dca_dip']['sides'] == 'long' and by_key['hot_coin']['sides'] == 'long'
    assert by_key['bear_breakdown']['sides'] == 'short' and by_key['rotation']['sides'] == 'long'
    # one-sided strategies never emit their other side (so 'both' would be meaningless for them); the BREADTH_BOTH ones do
    dfs = {s: _tape(drift=d, seed=j) for j, (s, d) in enumerate((('BTCUSDT', -0.002), ('ETHUSDT', 0.002), ('SOLUSDT', -0.001),
                                                                 ('XRPUSDT', 0.0)))}
    ctx = S.build_context(dfs, 'BTCUSDT')
    fired = {}
    for k in S.STRATEGIES:
        if k == 'exp_vwaprev': continue                                                  # needs a gap-free qv window
        for s, d in dfs.items():
            r = S.signals(k, d, ctx[s], None, mask_sides=False)
            fired.setdefault(k, set()).update({sd for sd in ('le', 'se') if r[sd][250:].any()})
    for k in ('dca_dip', 'hot_coin'): assert 'se' not in fired[k], k
    assert 'le' not in fired['bear_breakdown'] and 'se' not in fired['rotation']
    for k in E.BREADTH_BOTH: assert 'se' in fired[k], f'{k} never emitted a short on the down tapes'
