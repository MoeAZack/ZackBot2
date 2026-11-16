"""LANE3-F5 (PR #359 red-team): two confirmed fills of one sleeve/coin/side in the same second (or after a clock step
back) are two lots - the second never replaces the first. Real lot-construction path; every side effect faked."""
import os, sys, socket, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import requests
import engine as E                                                         # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k): raise AssertionError('network forbidden in this test')
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(requests.sessions.Session, 'request', refuse)


def bare(monkeypatch, t=1_799_000_000.25):
    e = object.__new__(E.Engine)
    e.rules = {'BTCUSDT': {'tick': .01, 'step': .001}}
    e.S, e.state = {}, {'lots': {}}
    e.saves = []
    e.save_state = lambda: e.saves.append({k: dict(v) for k, v in e.state['lots'].items()}) or True
    e._ml_link = lambda *a: None
    e._replace_stop = lambda lot: True
    e._tp_wanted = lambda lot: False
    e.log_trade = lambda **kw: None
    e.notify = lambda text: None
    clock = {'t': t}
    monkeypatch.setattr(E.time, 'time', lambda: clock['t'])
    return e, clock


PLAN = dict(sym='BTCUSDT', side='LONG', sl=None, manual=True, g={}, atr=2., eq=10000., risk_usd=10., reason='synthetic',
            px=100., stop_dist=5.)


def test_red_team_repro_same_second_manual_fills_preserve_both_lots(monkeypatch):
    """Codex #359 reproducer."""
    e, _ = bare(monkeypatch)
    assert e._create_lot(dict(PLAN), 1., 100.)
    assert e._create_lot(dict(PLAN), 2., 101.)
    assert len(e.state['lots']) == 2, 'second manual fill overwrote the first lot'
    assert sum(l['qty'] for l in e.state['lots'].values()) == 3.


def test_three_fills_same_second_get_distinct_keys_and_keep_the_symbol_field(monkeypatch):
    e, _ = bare(monkeypatch)
    keys = []
    for q in (1., 2., 3.):
        assert e._create_lot(dict(PLAN), q, 100.); keys.append(e._last_lot_key)
    assert len(set(keys)) == 3 and keys[0] == 'MAN|BTCUSDT|LONG|1799000000'
    assert keys[1:] == ['MAN|BTCUSDT|LONG|1799000000-2', 'MAN|BTCUSDT|LONG|1799000000-3']
    assert all(k.split('|')[1] == 'BTCUSDT' for k in keys)                   # app / Telegram read the symbol from [1]
    assert [e.state['lots'][k]['qty'] for k in keys] == [1., 2., 3.]
    assert all(e.saves[-1][k]['qty'] == q for k, q in zip(keys, (1., 2., 3.)))   # each one saved, none replaced


def test_clock_step_back_never_reuses_a_live_lot_key(monkeypatch):
    e, clock = bare(monkeypatch)
    e._create_lot(dict(PLAN), 1., 100.); first = e._last_lot_key
    clock['t'] += 5; e._create_lot(dict(PLAN), 2., 100.)
    clock['t'] -= 5; e._create_lot(dict(PLAN), 4., 100.)                      # the clock stepped back to the first second
    assert len(e.state['lots']) == 3 and e.state['lots'][first]['qty'] == 1.
    assert sum(l['qty'] for l in e.state['lots'].values()) == 7.


def test_a_closed_lot_key_may_be_used_again(monkeypatch):
    """Only LIVE lots are protected from replacement: after the first lot is gone, the plain key is free."""
    e, _ = bare(monkeypatch)
    e._create_lot(dict(PLAN), 1., 100.); k = e._last_lot_key
    del e.state['lots'][k]
    e._create_lot(dict(PLAN), 2., 100.)
    assert e._last_lot_key == k and e.state['lots'][k]['qty'] == 2.


def test_sleeve_lots_unchanged_when_no_collision(monkeypatch):
    e, _ = bare(monkeypatch)
    sl = dict(id='T', key='trend', tf='4h')
    e._create_lot(dict(PLAN, manual=False, sl=sl), 1., 100.)
    assert e._last_lot_key == 'T|BTCUSDT|LONG|1799000000'
