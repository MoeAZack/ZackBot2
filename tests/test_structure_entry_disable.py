"""PAPER pruning (Codex 6207997736): the breadth rotation refuses NEW exp_structure entries; an existing Structure lot
keeps its exchange stop and normal exit management. Synthetic only (tests/test_safety.FakeX), no network, no real data."""
import os, sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_safety import mk_engine                     # noqa: E402  (FakeX engine, PAPER)
import engine as E                                     # noqa: E402
import strategies as S                                 # noqa: E402

KEY = 'exp_structure'
SID = E.breadth_id(KEY)
TF = S.STRATEGIES[KEY]['tf']


def group_of(key):
    return next(i for i, g in enumerate(E.breadth_groups()) if key in g)


@pytest.fixture
def rot(monkeypatch, tmp_path):
    t = group_of(KEY) * E.ROTATION_PERIOD_S + 3600                 # inside the window where Structure's group is active
    real = E.rotation_at
    monkeypatch.setattr(E, 'rotation_at', lambda ts=None: real(t if ts is None else ts))
    e, _ = mk_engine(str(tmp_path)); e.apply_preset(E.ROTATION_PRESET)
    assert SID in e.rotation_status()['active']
    return e, monkeypatch


def sig(le=False, lx=False, close=100.0):
    return dict(close=close, atr=2.0, time='2026-10-16 00:00:00', le=le, se=False, lx=lx, sx=False)


def cycle(e, sigs):
    e.compute_signals = lambda tf, syms, extra=(): ({f'{SID}|{s}': dict(g) for s, g in sigs.items()} if tf == TF else {}, {})
    e.cycle(TF)


def slot(e, key=KEY):
    return next(x for x in e.S['SLEEVES'] if x['key'] == key)


def lots(e, sym=None):
    return [l for l in e.state['lots'].values() if l['sleeve'] == SID and (sym is None or l['symbol'] == sym)]


def test_structure_is_refused_for_new_entries_and_nothing_else_is():
    assert E.ROTATION_ENTRY_DISABLED == frozenset({KEY})
    assert KEY in S.STRATEGIES and KEY in E.ROTATION_OVERLAY_STRATS        # still registered / research-visible


def test_no_new_structure_entry_even_in_its_active_group(rot):
    e, _ = rot
    assert 'exp_structure disabled for new entries' in (e.rotation_block(slot(e)) or '')
    assert 'exp_structure disabled' in (e.entry_block(slot(e), 'BTCUSDT', 'LONG') or '')
    cycle(e, {'BTCUSDT': sig(le=True)})
    assert not lots(e) and 'exp_structure disabled for new entries' in e.missed[-1]['reason']
    others = [x for x in e.S['SLEEVES'] if x['id'] in E.rotation_at()['active'] and x['key'] != KEY]
    assert others and all(e.rotation_block(x) is None for x in others)    # the rest of the group is unaffected


def test_existing_structure_lot_keeps_its_stop_and_exits_normally(rot):
    e, mp = rot
    mp.setattr(E, 'ROTATION_ENTRY_DISABLED', frozenset())                # a lot opened before the disable
    cycle(e, {'BTCUSDT': sig(le=True)})
    mp.setattr(E, 'ROTATION_ENTRY_DISABLED', frozenset({KEY}))
    (l,) = lots(e, 'BTCUSDT')
    assert l['stop_id'] in e.trade.stops and e.trade.stops[l['stop_id']][3] == l['stop']   # exchange stop in place
    e.trade.mark['BTCUSDT'] = 101.0; e.manage(e.trade.marks())          # management still runs on it
    assert lots(e, 'BTCUSDT') and lots(e, 'BTCUSDT')[0]['stop_id'] in e.trade.stops
    cycle(e, {'BTCUSDT': sig(lx=True, close=101.0)})                     # its own exit signal closes it
    assert not lots(e, 'BTCUSDT') and e.history[-1]['sleeve'] == SID and e.history[-1]['exit_reason'] == 'exit_signal'


def test_existing_structure_lot_closed_by_its_exchange_stop(rot):
    e, mp = rot
    mp.setattr(E, 'ROTATION_ENTRY_DISABLED', frozenset())
    cycle(e, {'BTCUSDT': sig(le=True)})
    mp.setattr(E, 'ROTATION_ENTRY_DISABLED', frozenset({KEY}))
    (l,) = lots(e, 'BTCUSDT')
    e.trade.stops.pop(l['stop_id']); e.trade.pos[('BTCUSDT', 'LONG')] = 0.0  # the exchange stop filled
    e.reconcile(e.equity())
    assert not lots(e, 'BTCUSDT') and e.history[-1]['sleeve'] == SID


def test_other_presets_ignore_the_disable(tmp_path):
    e, _ = mk_engine(str(tmp_path))
    assert e.S['PRESET'] != E.ROTATION_PRESET and e.rotation_block(dict(id=SID, key=KEY)) is None
