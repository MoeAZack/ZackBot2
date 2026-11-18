"""CAPS-01 (#365) + LANE3-F1 risk trim (#367) + LANE3-F1 resting TP (#368) integration: ONE shared new-risk fence.

Engine._new_risk_block(sym) is the same-coin fence (stop missing > emergency-stop cleanup > unresolved risk trim of a
closed trade > held take-profit of a closed trade); Engine.new_risk_fence(sym) = account risk caps, then that fence, and
is rechecked at EVERY final risk-increasing dispatch: a staged grid open, a grid add, an add (_add_qty), a maker
placement / re-price, the pinned market entry's write-ahead (_remember_unconfirmed strict) and a manual open_lot. No
fence ever blocks an exit, a partial close or a stop move. Offline fakes only; nothing is sent to any exchange."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import engine as E
import test_grid as TG                                                      # noqa: E402
import test_safety as TS                                                    # noqa: E402
import test_v31_engine as TV                                                # noqa: E402
from test_safety import SL, SG                                              # noqa: E402

EMG = 'zm0123456789abcdef012345'
TPQ = 'zt0123456789abcdef012345'
TRIM = 'zbrfintegrat'


def _emg(e, sym='BTCUSDT'): e.state['emg_cleanup'] = {EMG: dict(symbol=sym, side='LONG', t=1.0)}
def _emg_off(e): e.state.pop('emg_cleanup', None)


def _trim(e, sym='BTCUSDT'):
    e.state['trim_owned'] = {TRIM: dict(cid=TRIM, symbol=sym, side='LONG', lot='gone|lot', sleeve='T', send_qty=0.01,
                                        sent_t=1.0)}
def _trim_off(e): e.state.pop('trim_owned', None)


def _tpq(e, sym='BTCUSDT'):
    e.state['tp_quarantine'] = {TPQ: dict(symbol=sym, side='LONG', tag='o:77', cid=TPQ, acid=None, lot='gone|lot', t=1.0,
                                          qty=1.0)}
def _tpq_off(e): e.state.pop('tp_quarantine', None)


def _caps(e, sym='BTCUSDT'): e.S['DAILY_LOSS_HALT'] = 0.08                  # looser than policy (3 %)
def _caps_off(e): e.S['DAILY_LOSS_HALT'] = 0.03


FENCES = {                       # name -> (set, clear, text of its refusal, per coin)
    'emg_cleanup': (_emg, _emg_off, 'emergency stop', True),
    'trim_owned': (_trim, _trim_off, 'risk trim', True),
    'tp_quarantine': (_tpq, _tpq_off, 'take-profit', True),
    'risk_caps': (_caps, _caps_off, 'risk caps looser than policy', False),
}
COIN = [f for f, v in FENCES.items() if v[3]]


def _spy_open(e):
    sent = []
    real = e.trade.open
    def open_(s, ps, q, *a, **k): sent.append((s, ps, q)); return real(s, ps, q, *a, **k)
    e.trade.open = open_
    return sent


# ------------------------------------------------------------------ the helper itself
@pytest.mark.parametrize('f', COIN)
def test_each_coin_fence_is_one_shared_reason_on_its_coin_only(f):
    e, _ = TS.mk_engine()
    set_, clear, txt, _ = FENCES[f]
    assert e._new_risk_block('BTCUSDT') is None and e.new_risk_fence('BTCUSDT') is None
    set_(e)
    for fn in (e._new_risk_block, e.stop_missing_block, e.new_risk_fence):
        assert txt in (fn('BTCUSDT') or ''), (f, fn.__name__)
        assert fn('ETHUSDT') is None, (f, fn.__name__)
    assert e.stop_missing_on('BTCUSDT') and not e.stop_missing_on('ETHUSDT')
    assert txt in (e.entry_block(SL, 'BTCUSDT', 'LONG', False, sg=SG) or '')     # the pinned gates see it too
    assert txt in (e.entry_block(SL, 'BTCUSDT', 'SHORT', True, sg=SG) or '')     # both sides, manual too
    clear(e)
    assert e._new_risk_block('BTCUSDT') is None and e.new_risk_fence('BTCUSDT') is None


def test_caps_are_in_the_dispatch_fence_but_not_in_the_coin_fence():
    e, _ = TS.mk_engine()
    _caps(e)
    assert 'risk caps' in e.new_risk_fence('BTCUSDT') and 'risk caps' in e.new_risk_fence('ETHUSDT')
    assert e._new_risk_block('BTCUSDT') is None and e.stop_missing_block('BTCUSDT') is None


def test_reason_order_and_releasing_one_fence_keeps_the_others():
    e, _ = TS.mk_engine()
    k = TS.opened(e)
    e.state['lots'][k]['stop_miss'] = True
    _emg(e); _trim(e); _tpq(e); _caps(e)
    assert 'risk caps' in e.new_risk_fence('BTCUSDT')                              # account caps first at the dispatch
    assert e._new_risk_block('BTCUSDT') == E.STOP_MISSING_BLOCKS['checking']
    order = [(lambda: e.state['lots'][k].pop('stop_miss'), 'emergency stop'), (lambda: _emg_off(e), 'risk trim'),
             (lambda: _trim_off(e), 'take-profit'), (lambda: _tpq_off(e), None)]
    _caps_off(e)
    for release, nxt in order:
        release()
        got = e.new_risk_fence('BTCUSDT')
        assert (got is None) if nxt is None else (nxt in got), (nxt, got)


def test_two_fences_at_once_then_releasing_one_keeps_the_other_on_every_dispatch():
    e, _ = TS.mk_engine()
    k = TS.opened(e)
    lot = e.state['lots'][k]
    _trim(e); _tpq(e)
    sent = _spy_open(e)
    with pytest.raises(RuntimeError, match='risk trim'): e._add_qty(lot, 0.1, 100.0, 'pyramid_add')
    _trim_off(e)
    with pytest.raises(RuntimeError, match='take-profit'): e._add_qty(lot, 0.1, 100.0, 'pyramid_add')
    with pytest.raises(E.NewRiskFenced, match='take-profit'):
        e._remember_unconfirmed(dict(sym='BTCUSDT', side='SHORT', qty=1.0, px=100.0, stop_dist=5.0, sl=SL), 'zbx', strict=True)
    assert sent == [] and not lot.get('pending')
    _tpq_off(e)
    assert e._add_qty(lot, 0.1, 100.0, 'pyramid_add') and sent


# ------------------------------------------------------------------ every final dispatch path, every fence
@pytest.mark.parametrize('f', list(FENCES))
def test_a_staged_grid_open_is_refused_at_its_final_dispatch(f):
    e, gm, _ = TG.mk()
    with e.lock: gm.start('G1', 'BTCUSDT')
    g = TG.grid_of(e)
    m = max(c['a'] for c in g['cells'] if c['k'] == 'L') - 0.05
    q = g['cells'][0]['q']
    gm._order_ok(g, q, m, 'LONG')                                              # fine before the fence
    op = gm._op(g, 'LONG', 'open', [0], q, m)                                  # staged (owned, durable) ...
    FENCES[f][0](e)                                                            # ... then the coin is fenced
    sent = _spy_open(e)
    gm._open(g, op)
    assert sent == [] and g.get('op') is None and not e.state['lots']
    with pytest.raises(ValueError, match=FENCES[f][2]): gm._order_ok(g, q, m, 'LONG')    # the grid add's preflight
    with pytest.raises(ValueError, match=FENCES[f][2]): gm._order_ok(g, q, m, 'SHORT')
    FENCES[f][1](e)
    gm._order_ok(g, q, m, 'LONG')                                              # released: allowed again


@pytest.mark.parametrize('f', list(FENCES))
def test_an_add_is_refused_at_its_final_dispatch(f):
    e, _ = TS.mk_engine()
    k = TS.opened(e)
    lot = e.state['lots'][k]
    FENCES[f][0](e)
    sent = _spy_open(e)
    with pytest.raises(RuntimeError, match=FENCES[f][2]): e._add_qty(lot, 0.1, 100.0, 'pyramid_add')
    assert sent == [] and not lot.get('pending')
    if FENCES[f][3]:
        FENCES[f][1](e); FENCES[f][0](e, 'ETHUSDT')                            # another coin: not this lot's fence
        assert e._add_qty(lot, 0.1, 100.0, 'pyramid_add') and sent


def test_a_missing_stop_is_the_same_dispatch_fence():
    e, _ = TS.mk_engine()
    k = TS.opened(e)
    lot = e.state['lots'][k]
    lot['stop_miss'] = True
    sent = _spy_open(e)
    with pytest.raises(RuntimeError, match='protective stop not found'): e._add_qty(lot, 0.1, 100.0, 'pyramid_add')
    with pytest.raises(E.NewRiskFenced):
        e._remember_unconfirmed(dict(sym='BTCUSDT', side='SHORT', qty=1.0, px=100.0, stop_dist=5.0, sl=SL), 'zbx', strict=True)
    assert sent == []


@pytest.mark.parametrize('f', list(FENCES))
def test_a_maker_placement_and_a_re_price_are_refused_and_never_fall_back_to_market(f):
    e, bk = TV.maker_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    rec = next(iter(e.state['resting_entries'].values()))
    FENCES[f][0](e)
    n = e.trade.calls.count('limit')
    with pytest.raises(RuntimeError, match=FENCES[f][2]): e._maker_place(rec)
    assert e.trade.calls.count('limit') == n
    sent = _spy_open(e)
    for _ in range(4): TV.age(e, 30, total=False); e.manage(e.trade.marks())  # the window passes: cancelled, no re-price
    assert e.trade.calls.count('limit') == n and sent == [] and not e.state['resting_entries']


@pytest.mark.parametrize('f', list(FENCES))
def test_the_market_entry_write_ahead_is_its_final_dispatch_check(f):
    e, _ = TS.mk_engine()
    FENCES[f][0](e)
    e.entry_block = lambda *a, **k: None                                       # a decision taken before the fence appeared
    sent = _spy_open(e)
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert sent == [] and not e.state['unconfirmed_entries'] and not e.state['lots']
    with pytest.raises(E.NewRiskFenced, match=FENCES[f][2]):
        e._remember_unconfirmed(dict(sym='BTCUSDT', side='LONG', qty=1.0, px=100.0, stop_dist=5.0, sl=SL), 'zbx', strict=True)
    assert not e.state['unconfirmed_entries']
    e._remember_unconfirmed(dict(sym='BTCUSDT', side='LONG', qty=1.0, px=100.0, stop_dist=5.0, sl=SL), 'zby')   # bookkeeping
    assert e.state['unconfirmed_entries']                                      # (non-strict) is never a dispatch: not fenced


@pytest.mark.parametrize('f', list(FENCES))
def test_a_manual_open_lot_is_refused(f):
    e, _ = TS.mk_engine()
    FENCES[f][0](e)
    sent = _spy_open(e)
    with pytest.raises(ValueError, match=FENCES[f][2]):                        # the manual gate (entry_block, pinned)
        e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity(), manual=True)
    real = e.entry_block
    e.entry_block = lambda *a, **k: None                                       # past the gate: the write-ahead still refuses
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity(), manual=True)
    assert 'NewRiskFenced' in (e.last_skip or '')
    assert sent == [] and not e.state['unconfirmed_entries'] and not e.state['lots']
    e.entry_block = real
    FENCES[f][1](e)
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity(), manual=True), e.last_skip
    assert sent


# ------------------------------------------------------------------ exits and protection are never fenced
def test_no_fence_blocks_exits_partial_closes_or_stop_moves():
    e, _ = TS.mk_engine()
    k = TS.opened(e)
    k2 = TS.opened(e, sym='ETHUSDT')
    lot = e.state['lots'][k]
    for f in FENCES: FENCES[f][0](e)                                           # every fence at once
    lot['stop_miss'] = True
    assert e.new_risk_fence('BTCUSDT')
    assert e._replace_stop(lot, lot['stop'] - 1.0)                             # protection moves
    q0 = lot['qty']
    e._market_close(lot, e._rd(q0 / 2, e.rules['BTCUSDT']['step']), 'manual', 100.0)   # a partial exit
    assert lot['qty'] < q0
    e.close_lot(k, 'manual', 100.0)                                            # a full exit
    e.close_lot(k2, 'manual', 100.0)
    assert k not in e.state['lots'] and k2 not in e.state['lots']


# ------------------------------------------------------------------ durable ownership of every new family
@pytest.mark.parametrize('f', ['emg_cleanup', 'trim_owned', 'tp_quarantine'])
def test_every_new_ownership_family_is_ownership_and_survives_the_state_check(f):
    e, _ = TS.mk_engine()
    FENCES[f][0](e)
    assert f in E.OWNERSHIP_FAMILIES and E.ownership_present(e.state) == [f]
    doc = dict(e.state, schema_version=E.STATE_SCHEMA)
    E.validate_state(doc)
    rec = next(iter(doc[f].values()))
    bad = dict(doc, **{f: {next(iter(doc[f])): dict(rec, symbol=None)}})
    with pytest.raises(E.SchemaError): E.validate_state(bad)                   # each family is validated at load


def test_a_trim_owner_record_requires_its_lot():
    """Codex #367 nonblocking note: the owner record schema requires `lot` (the key the sweep reconciles it by)."""
    e, _ = TS.mk_engine()
    _trim(e)
    doc = dict(e.state, schema_version=E.STATE_SCHEMA)
    E.validate_state(doc)
    rec = dict(doc['trim_owned'][TRIM]); rec.pop('lot')
    with pytest.raises(E.SchemaError, match='lot'): E.validate_state(dict(doc, trim_owned={TRIM: rec}))
    with pytest.raises(E.SchemaError, match='lot'):
        E.validate_state(dict(doc, trim_owned={TRIM: dict(rec, lot=7)}))
    e.state['trim_owned'] = {TRIM: rec}                                        # in memory: incomplete -> still a fence
    assert 'incomplete' in e.new_risk_fence('BTCUSDT')


def test_every_fence_family_survives_a_save_and_reload():
    e, tmp = TS.mk_engine()
    _emg(e); _trim(e); _tpq(e)
    assert e.save_state()
    e2, _ = TS.mk_engine(tmp)
    for f in ('emg_cleanup', 'trim_owned', 'tp_quarantine'): assert e2.state.get(f) == e.state[f], f
    assert 'emergency stop' in e2.new_risk_fence('BTCUSDT')


def test_adv1_a_closed_trade_trim_on_a_coin_without_rules_never_stops_management():
    """Adversarial ADV-1: a trim owner on a coin with no loaded exchange rules (a TradFi coin after a restart with TradFi
    off) once crashed manage() with KeyError on every pass once its record was final. It stays owned (a fence on its
    coin), is alerted as unreadable, and every other lot keeps being managed."""
    e, _ = TS.mk_engine()
    k = TS.opened(e)
    e.state['trim_owned'] = {TRIM: dict(cid=TRIM, symbol='XAUUSDT', side='LONG', lot='T|XAUUSDT|LONG|1', sleeve='T',
                                        send_qty=0.01, sent_t=1.0)}
    assert 'XAUUSDT' not in e.rules
    e.trade.get_order = lambda s, cid: dict(status='FILLED', executedQty='0.01', avgPrice='2000', clientOrderId=cid)
    seen = []
    orig = e.reconcile
    e.reconcile = lambda *a, **kw: (seen.append(1), orig(*a, **kw))[1]
    for _ in range(2): e.manage(e.trade.marks())
    assert seen and k in e.state['lots']
    assert TRIM in e.state['trim_owned'] and 'risk trim' in e.new_risk_fence('XAUUSDT')
    assert any(x.startswith('risk-trim-unknown|') for x in (e.health.get('incidents') or {}))
