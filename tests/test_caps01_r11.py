"""CAPS-01 r11 (Codex #365 round 10 review, c6932688031):
- P1-a: the emergency-cleanup fence is re-checked at EVERY final risk-increasing dispatch - a staged grid open, an add
  (pyramid / DCA / grid / entry fallback), a maker placement or re-price and the market entry's write-ahead - while exits
  and protection stay available, and other coins are not fenced;
- P1-b: a state that holds only leftover emergency stops (emg_cleanup) is exchange ownership: the legacy / unbound
  install path never records the account marker as if the state were empty;
- nonblocking: a client without typed stop lookups is UNKNOWN for a formerly held entry (claim kept, nothing settled,
  no new generation, an alert), never "nothing was ever sent".
Offline fakes only; nothing is sent to any exchange."""
import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import engine as E
import test_grid as TG                                                      # noqa: E402
import test_safety as TS                                                    # noqa: E402
import test_v31_engine as TV                                                # noqa: E402
import test_caps01_r6 as R6                                                 # noqa: E402
from test_safety import SL, SG                                              # noqa: E402

CID = 'zm0123456789abcdef012345'


def _fence(e, sym='BTCUSDT'):
    e.state['emg_cleanup'] = {CID: dict(symbol=sym, side='LONG', t=1.0)}


def _spy_open(e):
    sent = []
    real = e.trade.open
    def open_(s, ps, q, *a, **k): sent.append((s, ps, q)); return real(s, ps, q)
    e.trade.open = open_
    return sent


def test_r11_a_staged_grid_open_is_refused_at_its_final_dispatch():
    e, gm, _ = TG.mk()
    with e.lock: gm.start('G1', 'BTCUSDT')
    g = TG.grid_of(e)
    m = max(c['a'] for c in g['cells'] if c['k'] == 'L') - 0.05
    q = g['cells'][0]['q']
    gm._order_ok(g, q, m, 'LONG')                                              # fine before the fence
    op = gm._op(g, 'LONG', 'open', [0], q, m)                                  # staged (owned, durable) ...
    _fence(e)                                                                  # ... then the coin is fenced
    sent = _spy_open(e)
    gm._open(g, op)
    assert sent == [] and g.get('op') is None
    with pytest.raises(ValueError, match='emergency stop'): gm._order_ok(g, q, m, 'LONG')
    # integration (#365 + #367 + #368, one shared fence): _order_ok guards risk-adding grid orders only (every caller passes
    # the side; reducing grid orders never call it), and the unified fence holds whether or not a side is given - as the
    # accepted #367 / #368 grid tests require. Exits stay unfenced: test_r11_exits_and_protection_stay_available_under_the_fence
    with pytest.raises(ValueError, match='emergency stop'): gm._order_ok(g, q, m)


def test_r11_an_add_is_refused_at_its_final_dispatch_but_not_on_another_coin():
    e, _ = TS.mk_engine()
    k = TS.opened(e)
    lot = e.state['lots'][k]
    _fence(e)
    sent = _spy_open(e)
    with pytest.raises(RuntimeError, match='emergency stop'): e._add_qty(lot, 0.1, 100.0, 'pyramid_add')
    assert sent == [] and not lot.get('pending')
    _fence(e, 'ETHUSDT')                                                       # another coin: not this lot's fence
    assert e._add_qty(lot, 0.1, 100.0, 'pyramid_add') and sent


def test_r11_exits_and_protection_stay_available_under_the_fence():
    e, _ = TS.mk_engine()
    k = TS.opened(e)
    lot = e.state['lots'][k]
    _fence(e)
    assert e._replace_stop(lot, lot['stop'] - 1.0)                             # protection moves
    e.close_lot(k, 'manual', 100.0)                                            # an exit is never fenced
    assert k not in e.state['lots']


def test_r11_a_maker_placement_and_a_re_price_are_refused_and_never_fall_back_to_market():
    e, bk = TV.maker_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    rec = next(iter(e.state['resting_entries'].values()))
    _fence(e)
    n = e.trade.calls.count('limit')
    with pytest.raises(RuntimeError, match='emergency stop'): e._maker_place(rec)
    assert e.trade.calls.count('limit') == n
    sent = _spy_open(e)
    for _ in range(4): TV.age(e, 30, total=False); e.manage(e.trade.marks())  # the window passes: cancelled, no re-price
    assert e.trade.calls.count('limit') == n and sent == [] and not e.state['resting_entries']


def test_r11_the_market_entry_write_ahead_is_its_final_dispatch_check():
    e, _ = TS.mk_engine()
    _fence(e)
    e.entry_block = lambda *a, **k: None                                       # a decision taken before the fence appeared
    sent = _spy_open(e)
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert sent == [] and not e.state['unconfirmed_entries'] and not e.state['lots']
    with pytest.raises(E.NewRiskFenced):
        e._remember_unconfirmed(dict(sym='BTCUSDT', side='LONG', qty=1.0, px=100.0, stop_dist=5.0, sl=SL), 'zbx', strict=True)


def test_r11_a_cleanup_only_state_is_ownership_for_the_account_binding():
    e, _ = TS.mk_engine()
    _fence(e)
    assert E.ownership_present(e.state) == ['emg_cleanup']
    saved = []
    e._install_save = lambda *a, **k: saved.append(a) or True
    e.install_mismatch = None
    e._install_write(None)                                                     # legacy / unbound state
    assert saved == [] and e.install_mismatch and 'emg_cleanup' in e.install_mismatch['owned']


def test_r11_a_client_without_typed_lookups_is_unknown_never_absence():
    e, bk, data_dir, cid, _, exec_q = R6._to_crash(False, 'wal')
    e2 = R6._engine_on(data_dir, e.trade)
    e2.connect(); e2.marks = e.trade.marks(); e2.trade.get_order = bk.get
    assert not hasattr(e2.trade, 'stop_state')                                 # an adapter without the typed lookup
    (uk, u), = e2.state['unconfirmed_entries'].items()
    u['emg_check'] = True                                                      # it WAS held (may own an emergency stop)
    n = e2.trade.calls.count('stop')
    for _ in range(3): e2.reconcile(500)
    assert uk in e2.state['unconfirmed_entries'] and e2.state['unconfirmed_entries'][uk].get('emg_check')
    assert not e2.state['lots'] and e2.trade.calls.count('stop') == n          # nothing settled, no new stop
    assert any(k.startswith('emg-unknown|') for k in (e2.health.get('incidents') or {}))
