"""CAPS-01 r5 (Codex #365 round 5, c6929027122): (1) the maker -> unconfirmed-entry handover is crash-idempotent - a
restart from the real write-ahead snapshot (or from an older mid-handover snapshot / duplicate owners) books a 40 %
terminal fill exactly once with exactly one stop; (2) invalid risk operands (negative / NaN / inf price, stop or size)
are unknown risk -> the named refusal, never zero. Offline fakes only."""
import copy, math, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import engine as E
import test_v31_engine as TV                                               # noqa: E402
from test_safety import mk_engine, SL, SG                                 # noqa: E402

NAN, INF = float('nan'), float('inf')


# ---------------------------------------------------------------- 2: invalid operands
@pytest.mark.parametrize('field, bad', [('px', -1.0), ('px', NAN), ('px', INF), ('px', 0.0), ('qty', NAN), ('qty', -0.5),
                                        ('qty', INF), ('px', True), ('px', 'x')])
def test_r5_an_invalid_pending_add_operand_is_unknown_risk(field, bad):
    e, _ = mk_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    lot = TV.lot_of(e)
    lot['pending'] = dict(kind='add', qty=0.5, px=lot['avg'], why='dca', t=time.time(), cid='zp'); lot['pending'][field] = bad
    assert e._reserved_risk() is None
    assert E.OPEN_RISK_UNKNOWN in (e.entry_block(SL, 'ETHUSDT', 'LONG', manual=True) or '')


@pytest.mark.parametrize('stop', [NAN, -5.0, 0.0, INF])
def test_r5_an_invalid_lot_stop_under_a_pending_add_is_unknown_risk(stop):
    e, _ = mk_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    lot = TV.lot_of(e)
    lot['pending'] = dict(kind='add', qty=0.5, px=lot['avg'], why='dca', t=time.time(), cid='zp')
    assert E.Engine._side_risk('LONG', lot['avg'], stop, 0.5) is None


def test_r5_a_valid_profitable_stop_still_counts_zero():
    assert E.Engine._side_risk('LONG', 100.0, 101.0, 2.0) == 0.0 and E.Engine._side_risk('SHORT', 100.0, 99.0, 2.0) == 0.0
    assert E.Engine._side_risk('LONG', 100.0, 98.0, 2.0) == 4.0 and E.Engine._side_risk('SHORT', 100.0, 102.0, 2.0) == 4.0
    assert E.Engine._side_risk('BOTH', 100.0, 98.0, 2.0) is None


@pytest.mark.parametrize('plan, qty', [(dict(stop_dist=NAN), 1.0), (dict(stop_dist=-1.0), 1.0), (dict(stop_dist=INF), 1.0),
                                       (dict(stop_dist=2.0, risk_usd=NAN), NAN), (dict(stop_dist=2.0), -1.0),
                                       (dict(stop_dist=True), 1.0)])
def test_r5_invalid_plan_operands_are_unknown(plan, qty):
    assert E.Engine._plan_risk(plan, qty) is None


def test_r5_valid_plan_operands_still_count():
    assert E.Engine._plan_risk(dict(stop_dist='2'), 1.0) == 2.0 and E.Engine._plan_risk(dict(stop_dist=NAN, risk_usd=3), 1.0) == 3.0


@pytest.mark.parametrize('add', [NAN, INF, -1.0, None, 'x'])
def test_r5_open_risk_room_refuses_an_invalid_order_risk(add):
    e, _ = mk_engine()
    assert e.open_risk_room(0.0) is None
    assert e.open_risk_room(add)


# ---------------------------------------------------------------- 1: crash-idempotent handover
class _Crash(BaseException):
    """A process crash: not an Exception, so no engine handler swallows it."""


def _to_handover(e, bk):
    """Partial (40 %) fill, cancel requested, then the lookup says 'not found': the handover runs; every write-ahead
    snapshot taken during it is returned (the real WAL contents, not a hand-built state)."""
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    rec = next(iter(e.state['resting_entries'].values())); cid, q = rec['cid'], rec['qty']
    bk.fill(0.4)
    TV.age(e, 30); e.manage(e.trade.marks())
    assert rec['status'] == 'cancelling'
    snaps, real = [], e._save_wal
    def spy():                                    # the process dies right after the durable handover write (crash point)
        real(); snaps.append(copy.deepcopy(e.state))
        if any(u.get('cid') == cid for u in (e.state.get('unconfirmed_entries') or {}).values()): raise _Crash()
    e._save_wal = spy
    e.trade.get_order = lambda s, c: None
    TV.age(e, 30)
    with pytest.raises(_Crash): e.manage(e.trade.marks())
    e._save_wal = real
    snaps = [s for s in snaps if any(u.get('cid') == cid for u in (s.get('unconfirmed_entries') or {}).values())]
    assert snaps, 'no write-ahead snapshot holds the handed-over order'
    return cid, q, snaps[0]


def _restart(e_old, bk, snap):
    """A new engine process on the same fake exchange, loaded from the snapshot; the order record is readable again."""
    e2, _ = TV.maker_engine()
    e2.trade = e2.data = e_old.trade
    e2.state = copy.deepcopy(snap); e2.marks = e_old.trade.marks()
    e2.trade.get_order = bk.get                                               # CANCELED, 40 % executed
    return e2


def _one_lot_one_stop(e, q):
    for _ in range(3): e.manage(e.trade.marks()); e.reconcile(500)
    lots = [l for l in e.state['lots'].values() if l['symbol'] == 'BTCUSDT' and l['side'] == 'LONG']
    st = [v for v in e.trade.stops.values() if v[0] == 'BTCUSDT' and v[1] == 'LONG']
    assert len(lots) == 1 and lots[0]['qty'] == pytest.approx(round(q * .4, 3)), lots
    assert len(st) == 1 and abs(st[0][2] - lots[0]['qty']) < 1e-9, st
    assert not e.state['resting_entries'] and not e.state['unconfirmed_entries']


def test_r5_codex_restart_from_the_real_handover_write_ahead_books_once():
    e, bk = TV.maker_engine()
    cid, q, snap = _to_handover(e, bk)
    owners = [r for r in snap['resting_entries'].values() if r.get('cid') == cid and r.get('status') != 'handed']
    assert not owners, 'the write-ahead holds the order under two owners'
    _one_lot_one_stop(_restart(e, bk, snap), q)


def test_r5_restart_from_an_older_mid_handover_snapshot_is_normalized():
    """The r4 shape Codex captured: the resting record (still 'cancelling', full size) AND an unconfirmed entry, one cid."""
    e, bk = TV.maker_engine()
    cid, q, snap = _to_handover(e, bk)
    old = copy.deepcopy(snap)
    for r in old['resting_entries'].values():
        if r['cid'] == cid: r.update(status='cancelling', qty=q)
    if not old['resting_entries']:
        rk = f"ME|T|BTCUSDT|LONG"
        old['resting_entries'][rk] = dict(key=rk, sleeve='T', symbol='BTCUSDT', side='LONG', qty=q, filled=0.0, cost=0.0, n=1,
                                          t0=time.time(), status='cancelling', cid=cid, price=99.9, placed_t=time.time() - 60,
                                          cancel_t=time.time() - 30, plan=next(iter(old['unconfirmed_entries'].values()))['plan'])
    _one_lot_one_stop(_restart(e, bk, old), q)


def test_r5_two_unconfirmed_owners_of_one_order_merge_before_settlement():
    e, bk = TV.maker_engine()
    cid, q, snap = _to_handover(e, bk)
    dup = copy.deepcopy(snap)
    (uk, u), = [(k, v) for k, v in dup['unconfirmed_entries'].items() if v['cid'] == cid]
    dup['unconfirmed_entries'][uk + '+'] = dict(copy.deepcopy(u), t=u['t'] + 1)
    _one_lot_one_stop(_restart(e, bk, dup), q)


def test_r5_the_handover_keeps_the_reservation_and_the_partial_protection():
    e, bk = TV.maker_engine()
    cid, q, snap = _to_handover(e, bk)
    e = _restart(e, bk, snap); e.trade.get_order = lambda s, c: None        # restarted; the record still unreadable
    e.manage(e.trade.marks())
    assert [u['cid'] for u in e.state['unconfirmed_entries'].values()] == [cid] and e._reserved_risk() > 0
    e.reconcile(500); e.reconcile(500)
    (u,) = e.state['unconfirmed_entries'].values()
    assert u.get('prov') in e.trade.stops and abs(e.trade.stops[u['prov']][2] - round(q * .4, 3)) < 1e-9


def test_r5_a_failed_handover_write_changes_nothing():
    e, bk = TV.maker_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    rec = next(iter(e.state['resting_entries'].values())); q = rec['qty']
    e.trade.get_order = lambda s, c: None
    def boom(): raise OSError('disk full')
    e._save_wal = boom
    TV.age(e, 25); e.manage(e.trade.marks())
    assert rec['status'] != 'handed' and rec['qty'] == q and not e.state['unconfirmed_entries']


def test_r5_the_handover_itself_is_idempotent_by_client_id():
    e, bk = TV.maker_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    (k, rec), = e.state['resting_entries'].items()
    e._remember_unconfirmed(dict(rec['plan'], qty=rec['qty']), rec['cid'])   # an owner already exists for this order
    e._maker_unknown(k, rec)
    assert [u['cid'] for u in e.state['unconfirmed_entries'].values()] == [rec['cid']] and not e.state['resting_entries']


def test_r5_reconcile_alone_normalizes_before_it_settles_fills():
    """After a restart the first reconcile can run before any maker poll: the owners are normalized there too."""
    e, bk = TV.maker_engine()
    cid, q, snap = _to_handover(e, bk)
    old = copy.deepcopy(snap)
    plan = next(iter(old['unconfirmed_entries'].values()))['plan']
    rk = 'ME|T|BTCUSDT|LONG'
    old['resting_entries'] = {rk: dict(key=rk, sleeve='T', symbol='BTCUSDT', side='LONG', qty=q, filled=0.0, cost=0.0, n=1,
                                       t0=time.time(), status='cancelling', cid=cid, price=99.9, placed_t=time.time() - 60,
                                       cancel_t=time.time() - 30, plan=plan)}
    e2 = _restart(e, bk, old)
    e2.reconcile(500)
    assert not e2.state['resting_entries'], 'fills settled while the order still had two owners'
    _one_lot_one_stop(e2, q)


def test_r5_a_failed_finish_still_leaves_one_owner_with_only_its_known_fills():
    e, bk = TV.maker_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    (k, rec), = e.state['resting_entries'].items()
    rec.update(filled=0.1, cost=0.1 * 99.9, status='cancelling')
    e._remember_unconfirmed(dict(rec['plan'], qty=rec['qty'] - 0.1), rec['cid'])
    def boom(*a, **kw): raise RuntimeError('finalize failed')
    e._maker_finalize = boom
    e._normalize_order_owners()
    assert rec['status'] == 'handed' and rec['qty'] == pytest.approx(0.1)          # never re-polled / double-reserved
