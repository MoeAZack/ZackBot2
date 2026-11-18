"""CAPS-01 r6 (Codex #365 round 6, c6929819143): every handover state that is saved is loadable by the REAL state loader.
The process 'crashes' right after a real durable write (the first one holding the handover, or the strict _save_wal),
a NEW Engine is constructed on that same data directory (real load + validation, no state injected), and the order's
terminal record then books exactly once with one stop per lot - for zero and for positive known maker fills."""
import copy, glob, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import engine as E
import test_v31_engine as TV                                               # noqa: E402
from test_safety import SL, SG                                             # noqa: E402


class _Crash(BaseException):
    """A process crash: not an Exception, so no engine handler swallows it."""


def _crash_at(e, cid, point):
    """Make the process die right after the real write: 'first' = the first state write holding the order's unconfirmed
    entry; 'wal' = the first strict write-ahead (_save_wal) holding it."""
    real = e._save_safe
    def spy(name, doc, strict=False):
        out = real(name, doc, strict=strict)
        held = any(u.get('cid') == cid for u in (e.state.get('unconfirmed_entries') or {}).values())
        if name == 'state' and held and (point == 'first' or strict): raise _Crash()
        return out
    e._save_safe = spy


def _engine_on(data_dir, fake):
    """A new process on the same data directory and the same (fake) exchange: the real constructor loads the state."""
    E.Futures = lambda *a, **k: fake
    e2 = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), data_dir)
    e2.data = e2.trade
    e2.S.update(dict(UNIVERSE=['BTCUSDT', 'ETHUSDT'], SYMBOLS_ON={'BTCUSDT': True, 'ETHUSDT': True}, CAPITAL_CAP=500.0,
                     ENTRY_ORDER='maker', MAKER_REPRICE=2, MAKER_WAIT_S=30))
    return e2


def _to_crash(known, point):
    e, bk = TV.maker_engine()
    data_dir = os.path.dirname(e.F['state'])
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    rec = next(iter(e.state['resting_entries'].values())); q = rec['qty']
    if known:                                                                 # a first order fills 40 % and ends: known fills
        bk.fill(0.4); TV.age(e, 30); e.manage(e.trade.marks())              # cancel requested
        e.manage(e.trade.marks())                                            # final record read -> re-priced (2nd order)
        assert rec['filled'] == pytest.approx(round(q * .4, 3)) and rec['n'] == 2 and rec['status'] == 'open'
    cid = rec['cid']
    bk.fill(0.4)                                                              # this order fills 40 % of what it was placed for
    placed = float(next(o for o in bk.orders.values() if o['clientOrderId'] == cid)['quantity'])
    TV.age(e, 30); e.manage(e.trade.marks())                                  # cancel requested
    _crash_at(e, cid, point)
    e.trade.get_order = lambda s, c: None                                     # the lookup now says 'not found'
    TV.age(e, 30)
    with pytest.raises(_Crash): e.manage(e.trade.marks())
    return e, bk, data_dir, cid, (rec['filled'] if known else 0.0), round(placed * .4, 3)


@pytest.mark.parametrize('point', ['first', 'wal'])
@pytest.mark.parametrize('known', [False, True], ids=['no-known-fills', 'known-fills'])
def test_r6_a_crash_right_after_the_real_handover_write_reloads_and_books_once(known, point):
    e, bk, data_dir, cid, known_q, exec_q = _to_crash(known, point)
    e2 = _engine_on(data_dir, e.trade)
    assert not glob.glob(os.path.join(data_dir, 'state.json.corrupt-*')), 'the loader quarantined the handover state'
    assert [u['cid'] for u in e2.state['unconfirmed_entries'].values()] == [cid], 'the unconfirmed entry was lost'
    assert not any(r.get('cid') == cid for r in e2.state['resting_entries'].values())
    if known: assert any(l['qty'] == pytest.approx(known_q) for l in e2.state['lots'].values())
    e2.connect(); e2.marks = e.trade.marks()
    if known: time.sleep(1.1)   # master's lot key has 1-s resolution: two lots in one second collide - that is #363 (F5),
    #                             not this PR; the restart takes longer than a second in any case
    e2.trade.get_order = bk.get                                               # its terminal record is readable: 40 % executed
    for _ in range(3): e2.manage(e2.trade.marks()); e2.reconcile(500)
    lots = [l for l in e2.state['lots'].values() if l['symbol'] == 'BTCUSDT' and l['side'] == 'LONG']
    assert sum(l['qty'] for l in lots) == pytest.approx(known_q + exec_q), [l['qty'] for l in lots]
    assert sorted(round(l['qty'], 6) for l in lots) == sorted(round(x, 6) for x in ([known_q] if known else []) + [exec_q])
    stops = [v for v in e2.trade.stops.values() if v[0] == 'BTCUSDT' and v[1] == 'LONG']
    assert len(stops) == len(lots) and sum(s[2] for s in stops) == pytest.approx(known_q + exec_q)   # one stop per lot
    assert e2.trade.pos[('BTCUSDT', 'LONG')] == pytest.approx(known_q + exec_q)
    assert not e2.state['resting_entries'] and not e2.state['unconfirmed_entries'] and not e2.untracked


@pytest.mark.parametrize('known', [False, True], ids=['no-known-fills', 'known-fills'])
def test_r6_every_state_written_during_the_handover_passes_the_real_validator(known):
    e, bk = TV.maker_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    rec = next(iter(e.state['resting_entries'].values()))
    if known:
        bk.fill(0.4); TV.age(e, 30); e.manage(e.trade.marks()); e.manage(e.trade.marks())
    bk.fill(0.4); TV.age(e, 30); e.manage(e.trade.marks())
    seen, real = [], e._save_safe
    def spy(name, doc, strict=False):
        if name == 'state': seen.append(copy.deepcopy(doc))
        return real(name, doc, strict=strict)
    e._save_safe = spy
    e.trade.get_order = lambda s, c: None
    TV.age(e, 30); e.manage(e.trade.marks())
    assert seen
    for doc in seen: E.validate_state(E.migrate_state(doc))                   # raises on any unloadable snapshot


def test_r6_a_handover_whose_lot_write_was_not_durable_is_not_reported_done():
    """Known fills: the lot's own save is best effort; the strict write-ahead after it must surface a disk failure."""
    e, bk = TV.maker_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    bk.fill(0.4); TV.age(e, 30); e.manage(e.trade.marks()); e.manage(e.trade.marks())
    bk.fill(0.4); TV.age(e, 30); e.manage(e.trade.marks())
    def disk(name, doc, strict=False):
        if strict: raise OSError('disk full')
        return False
    e._save_safe = disk
    e.trade.get_order = lambda s, c: None
    TV.age(e, 30); e.manage(e.trade.marks())
    assert any('disk full' in (i.get('msg') or '') and 'maker entry' in (i.get('msg') or '')
               for i in e.health.get('incidents', {}).values()), 'a non-durable handover was treated as finished'
