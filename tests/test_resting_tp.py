"""Resting exchange take-profit (per-sleeve mgmt.resting_tp, default OFF). Synthetic only: the test_safety fake exchange
extended with a TAKE_PROFIT_MARKET order book and order records - no network, no credentials, nothing live.

Covers: OFF is byte-identical; placement (side, price on the safe side of the tick, reduce-only, hedge positionSide, zt
client id, only after the stop); a TP fill finishes the lot as 'take_profit_resting' (stop cancelled, history + audit);
a stop fill / any other close cancels the TP (a failed cancel never blocks the close and is retried); a missing TP is
placed again; an orphaned TP is cancelled; a placement failure falls back to the poll path with the stop untouched;
restart persistence; partial fills; excluded exits never place a TP; validation, plan text and the reason registry."""
import json, os, re, tempfile, time
import pytest, requests
import test_safety as TS
import engine as E
import trade_audit as TA
import binance_client as BC


class FakeTP(TS.FakeX):
    """FakeX + resting take-profits: tps[tag] = dict(s, ps, q, p, cid, ro); recs[cid] = the order record get_order returns."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.tps, self.recs = {}, {}

    def take_profit(self, s, ps, q, p, reduce_only=True, cid=None, acid=None):
        self.calls.append('tp')
        if 'tp' in self.fail: raise BC.BinanceError(-1000, 'injected tp failure')
        if 'tp_2021' in self.fail: raise BC.BinanceError(-2021, 'Order would immediately trigger.')
        self.n += 1; tag = f'o:{self.n}'
        self.tps[tag] = dict(s=s, ps=ps, q=float(q), p=float(p), raw_p=p, cid=cid, ro=reduce_only)
        self.recs[cid] = dict(orderId=self.n, status='NEW', executedQty='0', avgPrice='0', tag=tag)
        return tag

    def get_order(self, s, cid):
        r = self.recs.get(cid)
        return {k: v for k, v in r.items() if k != 'tag'} if r else None

    def cancel(self, s, tag):
        self._f('cancel'); self.stops.pop(tag, None)
        t = self.tps.pop(tag, None)
        if t and self.recs[t['cid']]['status'] == 'NEW': self.recs[t['cid']]['status'] = 'CANCELED'
        return True

    def open_stop_tags(self, s):
        self._f('tags')
        return {k for k, v in self.stops.items() if v[0] == s} | {k for k, v in self.tps.items() if v['s'] == s}

    def open_stop_orders(self, s, strict_algo=False):
        self._f('tags')
        rows = [dict(tag=k, type='STOP_MARKET', position_side=v[1], side='SELL' if v[1] == 'LONG' else 'BUY', qty=v[2],
                     stop_price=v[3], client_id=None, status='NEW', close_position=False, reduce_only=False)
                for k, v in self.stops.items() if v[0] == s]
        rows += [dict(tag=k, type='TAKE_PROFIT_MARKET', position_side=v['ps'], side='SELL' if v['ps'] == 'LONG' else 'BUY',
                      qty=v['q'], stop_price=v['p'], client_id=v['cid'], status='NEW', close_position=False, reduce_only=False)
                 for k, v in self.tps.items() if v['s'] == s]
        return rows

    def fill_tp(self, tag, qty=None, final=True, px=None):
        """The TP executes qty (default: all of it) at px (default: its trigger)."""
        t = self.tps[tag]; rec = self.recs[t['cid']]
        q = t['q'] if qty is None else qty
        done = float(rec['executedQty']) + q
        self.pos[(t['s'], t['ps'])] = self.pos.get((t['s'], t['ps']), 0) - q
        rec.update(executedQty=f'{done:g}', avgPrice=str(px or t['p']),
                   status=('FILLED' if done >= t['q'] - 1e-12 else 'EXPIRED') if final else 'PARTIALLY_FILLED')
        if final: self.tps.pop(tag)


def mk(tmp=None, cls=FakeTP, **S):
    tmp = tmp or tempfile.mkdtemp()
    E.Futures = cls
    e = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)
    e.data = e.trade
    e.S.update(dict(UNIVERSE=['BTCUSDT', 'ETHUSDT'], SYMBOLS_ON={'BTCUSDT': True, 'ETHUSDT': True}, CAPITAL_CAP=500.0), **S)
    e.connect()
    e.marks = e.trade.marks()
    return e, tmp


def sl_(**mg):
    return E.sleeve('T', 'ema_mom', 0.5, 0.02, 2, 'all', sides=['long', 'short'], mgmt=mg)


ON = dict(tp_r=2.0, resting_tp=True)
SG_S = dict(TS.SG, le=False, se=True)


def open_(e, mg=ON, side='LONG', sym='BTCUSDT'):
    assert e.open_lot(sl_(**mg), sym, side, TS.SG if side == 'LONG' else SG_S, None, e.equity()), e.last_skip
    return next(k for k, l in e.state['lots'].items() if l['symbol'] == sym and l['side'] == side)


def verify(e, later=0.0):
    e._stopv.clear()
    if later: e.clock = lambda: time.time() + later
    return e.verify_stops()


def manage(e):
    e.manage(e.trade.marks())


# ------------------------------------------------------------------ OFF: byte-identical
@pytest.mark.parametrize('mg', [dict(tp_r=2.0), dict(tp_r=2.0, resting_tp=False)])
def test_option_off_is_byte_identical(mg):
    runs = []
    for m in (dict(tp_r=2.0), mg):
        e, _ = mk(); k = open_(e, m); lot = e.state['lots'][k]
        plan = e.exit_plan(lot)
        verify(e, later=100); manage(e)
        assert 'tp' not in e.trade.calls and not e.trade.tps
        assert not any(f.startswith('tp_') for f in lot)
        e.trade.mark['BTCUSDT'] = 110.5; manage(e)                       # today's poll path: closed at market
        assert k not in e.state['lots'] and e.history[-1]['exit_reason'] == 'take_profit'
        h = {x: v for x, v in e.history[-1].items() if x not in ('id', 'opened', 'closed', 'fills', 'hours')}
        runs.append((list(e.trade.calls), plan, h))
    assert runs[0] == runs[1]
    assert 'resting' not in json.dumps(runs[0][1])


# ------------------------------------------------------------------ placement
def test_tp_placed_with_side_price_reduce_only_after_the_stop():
    e, _ = mk(); k = open_(e, dict(tp_r=1.0011, resting_tp=True)); lot = e.state['lots'][k]
    assert len(e.trade.tps) == 1
    tag, tp = next(iter(e.trade.tps.items()))
    assert tp['ps'] == 'LONG' and tp['s'] == 'BTCUSDT' and tp['ro'] is True
    assert tp['q'] == lot['qty'] and tp['raw_p'] == '105.00'             # 105.0055 rounded DOWN (toward the entry) for a long
    assert re.fullmatch(r'zt[0-9a-f]{22}', tp['cid']) and lot['tp_cid'] == tp['cid']
    assert lot['tp_id'] == tag and lot['tp_px'] == 105.0 and lot['tp_qty'] == lot['qty']
    assert e.trade.calls.index('stop') < e.trade.calls.index('tp')       # never before the stop
    assert lot['stop_id'] in e.trade.stops and lot['stop'] == 95.0       # stop exactly as without the option
    assert 'resting exchange target' in ' '.join(e.exit_plan(lot)['steps'])


def test_hedge_short_uses_short_position_side_and_rounds_up():
    e, _ = mk(); k = open_(e, dict(tp_r=1.0011, resting_tp=True), side='SHORT'); lot = e.state['lots'][k]
    tp = next(iter(e.trade.tps.values()))
    assert tp['ps'] == 'SHORT' and tp['raw_p'] == '95.00' and tp['ro'] is True   # 94.9945 rounded UP (toward the entry)
    assert lot['tp_px'] == 95.0


def test_client_take_profit_params(monkeypatch):
    c, calls = TS.client_with([TS.Resp(200, {'orderId': 9})])
    assert c.take_profit('BTCUSDT', 'LONG', '0.01', '105.0', cid='zt' + 'a' * 22) == 'o:9'
    p = calls[0][2]
    assert (p['side'], p['positionSide'], p['type'], p['stopPrice'], p['workingType']) == ('SELL', 'LONG', 'TAKE_PROFIT_MARKET', '105.0', 'MARK_PRICE')
    assert 'reduceOnly' not in p and p['newClientOrderId'] == 'zt' + 'a' * 22   # hedge: the close side IS reduce-only
    c, calls = TS.client_with([TS.Resp(200, {'orderId': 10})])
    c.take_profit('BTCUSDT', 'SHORT', '0.01', '95.0')
    assert calls[0][2]['side'] == 'BUY' and calls[0][2]['positionSide'] == 'SHORT'
    with pytest.raises(ValueError): c.take_profit('BTCUSDT', 'LONG', '0.01', '1', reduce_only=False)


# ------------------------------------------------------------------ fills
def test_tp_fill_finishes_the_lot_and_cancels_the_stop(monkeypatch):
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]; stop = lot['stop_id']; tag = lot['tp_id']
    ev = []; monkeypatch.setattr(e, '_audit_emit', lambda x: ev.append(x))
    e.trade.fill_tp(tag)
    manage(e)
    assert k not in e.state['lots'] and stop not in e.trade.stops and not e.trade.tps
    h = e.history[-1]
    assert h['exit_reason'] == 'take_profit_resting' and h['exit'] == 110.0 and h['pnl_gross'] > 0
    assert any(x and x.get('kind') == 'trade_audit' and x.get('exit_reason') == 'take_profit_resting' for x in ev)


def test_tp_fill_seen_by_the_verifier():
    e, _ = mk(); k = open_(e); stop = e.state['lots'][k]['stop_id']; tag = e.state['lots'][k]['tp_id']
    e.trade.fill_tp(tag)
    assert verify(e, later=100)
    assert k not in e.state['lots'] and stop not in e.trade.stops and e.history[-1]['exit_reason'] == 'take_profit_resting'


def test_tp_fill_without_order_record_decided_by_position_while_stop_listed(monkeypatch):
    e, _ = mk(); k = open_(e); tag = e.state['lots'][k]['tp_id']
    e.trade.fill_tp(tag)
    monkeypatch.setattr(e.trade, 'get_order', lambda s, cid: (_ for _ in ()).throw(requests.Timeout('x')))
    manage(e)
    assert k not in e.state['lots'] and e.history[-1]['exit_reason'] == 'take_profit_resting' and not e.trade.stops


def test_stop_fill_cancels_the_tp():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]; tag = lot['tp_id']
    e.trade.stops.pop(lot['stop_id']); e.trade.pos[('BTCUSDT', 'LONG')] = 0
    manage(e)
    assert k not in e.state['lots'] and e.history[-1]['exit_reason'] == 'stop'
    assert tag not in e.trade.tps and e.trade.recs[lot['tp_cid']]['status'] == 'CANCELED'


@pytest.mark.parametrize('why', ['exit_signal', 'time_exit', 'manual_close', 'flatten'])
def test_any_other_close_cancels_the_tp(why):
    e, _ = mk(); k = open_(e); tag = e.state['lots'][k]['tp_id']
    if why == 'flatten': e.flatten()
    else: e.close_lot(k, why)
    assert k not in e.state['lots'] and not e.trade.tps and not e.trade.stops and e.history[-1]['exit_reason'] == why
    assert tag not in e.trade.tps


def test_tp_cancel_failure_never_blocks_the_close_and_is_retried():
    e, _ = mk(); k = open_(e); tag = e.state['lots'][k]['tp_id']
    e.trade.fail.add('cancel')
    e.close_lot(k, 'exit_signal')
    assert k not in e.state['lots'] and ['BTCUSDT', tag] in e.state['orphans'] and tag in e.trade.tps
    e.trade.fail.clear(); manage(e)
    assert e.state['orphans'] == [] and not e.trade.tps


# ------------------------------------------------------------------ reconciliation
def test_missing_tp_is_placed_again():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]; old = lot['tp_id']
    e.trade.cancel('BTCUSDT', old)                                      # gone without a fill (e.g. cancelled on the website)
    assert verify(e, later=100)
    assert lot['tp_id'] and lot['tp_id'] != old and lot['tp_id'] in e.trade.tps and len(e.trade.tps) == 1
    assert e.trade.tps[lot['tp_id']]['q'] == lot['qty'] and k in e.state['lots']


def test_just_placed_tp_not_listed_is_not_a_miss():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]; old = lot['tp_id']
    e.trade.tps.pop(old)
    verify(e)                                                           # inside the listing grace: nothing re-placed
    assert lot['tp_id'] == old and 'tp' not in e.trade.calls[e.trade.calls.index('tp') + 1:]


def test_orphaned_tp_is_cancelled():
    e, _ = mk(); open_(e)
    cid = BC.new_cid('zt'); e.trade.tps['o:999'] = dict(s='BTCUSDT', ps='LONG', q=1.0, p=120.0, raw_p='120', cid=cid, ro=True)
    e.trade.recs[cid] = dict(orderId=999, status='NEW', executedQty='0', avgPrice='0')
    foreign = BC.new_cid('web'); e.trade.tps['o:998'] = dict(s='BTCUSDT', ps='LONG', q=1.0, p=120.0, raw_p='120', cid=foreign, ro=True)
    verify(e, later=100)
    assert 'o:999' not in e.trade.tps and 'o:998' in e.trade.tps          # only the bot's own (zt) orphan is cancelled
    assert len([t for t in e.trade.tps.values() if t['cid'] != foreign]) == 1


# ------------------------------------------------------------------ placement failure: poll fallback, stop untouched
@pytest.mark.parametrize('fail', ['tp', 'tp_2021'])
def test_tp_failure_falls_back_to_poll_and_stop_is_unaffected(fail):
    e0, _ = mk(); k0 = open_(e0, dict(tp_r=2.0)); base = e0.state['lots'][k0]
    e, _ = mk(); e.trade.fail.add(fail); k = open_(e); lot = e.state['lots'][k]
    assert lot['tp_failed'] and not lot.get('tp_id') and not e.trade.tps
    assert (lot['stop'], lot['qty'], lot['stop_dirty']) == (base['stop'], base['qty'], base['stop_dirty'])
    assert e.trade.stops[lot['stop_id']] == e0.trade.stops[base['stop_id']]
    assert 'resting' not in ' '.join(e.exit_plan(lot)['steps'])
    e.trade.fail.clear(); verify(e, later=100)
    assert not e.trade.tps                                              # never retried: the poll path owns it now
    e.trade.mark['BTCUSDT'] = 110.5; manage(e)
    assert k not in e.state['lots'] and e.history[-1]['exit_reason'] == 'take_profit'


def test_poll_close_is_skipped_while_tp_rests():
    e, _ = mk(); k = open_(e)
    e.trade.mark['BTCUSDT'] = 110.5; manage(e)                          # the exchange order owns the exit
    assert k in e.state['lots'] and 'close' not in e.trade.calls


# ------------------------------------------------------------------ persistence
def test_restart_keeps_the_tp():
    e, tmp = mk(); k = open_(e); lot = dict(e.state['lots'][k]); fake = e.trade
    on_disk = json.load(open(e.F['state'], encoding='utf-8'))['lots'][k]
    assert on_disk['tp_id'] == lot['tp_id'] and on_disk['tp_cid'] == lot['tp_cid'] and on_disk['tp_px'] == lot['tp_px']
    e2, _ = mk(tmp); e2.trade = e2.data = fake
    l2 = e2.state['lots'][k]
    assert (l2['tp_id'], l2['tp_cid'], l2['tp_qty']) == (lot['tp_id'], lot['tp_cid'], lot['tp_qty'])
    n = fake.calls.count('tp'); verify(e2, later=100); manage(e2)
    assert fake.calls.count('tp') == n and len(fake.tps) == 1             # owned again, never a second TP
    fake.fill_tp(l2['tp_id']); manage(e2)
    assert k not in e2.state['lots'] and e2.history[-1]['exit_reason'] == 'take_profit_resting'


def test_state_schema_validates_tp_fields():
    e, _ = mk(); k = open_(e)
    doc = json.load(open(e.F['state'], encoding='utf-8'))
    E.validate_state(doc)
    doc['lots'][k]['tp_id'] = 'bad tag'
    with pytest.raises(E.SchemaError): E.validate_state(doc)


# ------------------------------------------------------------------ partial fills
def test_partial_tp_fill_reduces_qty_and_resizes_the_stop():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]; q0 = lot['qty']; tag = lot['tp_id']; old_stop = lot['stop_id']
    half = E.Engine._rd(q0 / 2, 0.001)
    e.trade.fill_tp(tag, qty=half, final=False)                         # still working (PARTIALLY_FILLED)
    manage(e)
    assert k in e.state['lots'] and abs(lot['qty'] - (q0 - half)) < 1e-9 and lot['tp_id'] == tag
    assert old_stop not in e.trade.stops and e.trade.stops[lot['stop_id']][2] == pytest.approx(lot['qty'])
    assert lot['stop'] == 95.0                                          # resized, never moved
    assert lot['fills'][-1][1] == 'take_profit_resting' and lot['fills'][-1][2] == pytest.approx(half)
    e.trade.fill_tp(tag); manage(e)                                     # the rest fills: booked once, lot finished
    assert k not in e.state['lots'] and not e.trade.stops
    h = e.history[-1]
    assert h['exit_reason'] == 'take_profit_resting' and sum(f[2] for f in h['fills'] if f[1] == 'take_profit_resting') == pytest.approx(q0)


def test_partial_tp_that_ended_gets_a_new_tp_for_the_rest():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]; q0 = lot['qty']; tag = lot['tp_id']
    half = E.Engine._rd(q0 / 2, 0.001)
    e.trade.fill_tp(tag, qty=half, final=True)                          # EXPIRED after a partial execution
    manage(e)                                                           # reconcile books the part and resizes the stop ...
    assert k in e.state['lots'] and not lot.get('tp_id') and abs(lot['qty'] - (q0 - half)) < 1e-9
    verify(e, later=100)                                                # ... the verifier places the TP for what is left
    assert lot['tp_id'] != tag and lot['tp_id'] in e.trade.tps
    assert e.trade.tps[lot['tp_id']]['q'] == pytest.approx(lot['qty']) == pytest.approx(q0 - half)
    assert e.trade.stops[lot['stop_id']][2] == pytest.approx(lot['qty'])


def test_resync_shrink_resizes_the_tp_with_the_stop():
    e, _ = mk(); k = open_(e); lot = e.state['lots'][k]; q0 = lot['qty']; tag = lot['tp_id']
    lot['qty'] = E.Engine._rd(q0 / 2, 0.001); lot['stop_dirty'] = True
    e._replace_stop(lot)
    assert tag not in e.trade.tps and len(e.trade.tps) == 1 and e.trade.tps[lot['tp_id']]['q'] == pytest.approx(lot['qty'])


# ------------------------------------------------------------------ excluded exits
@pytest.mark.parametrize('extra', [dict(runner=dict(be_r=2.0)), dict(ttp=dict(at_r=2.0, dev_pct=3.0)), dict(tp1_r=1.0),
                                   dict(tps=[[1.0, 0.5]]), dict(pyramid=dict(n=1, step_r=1.5, frac=0.5))])
def test_excluded_mgmt_never_places_a_tp(extra):
    e, _ = mk(); k = open_(e, dict(ON, **extra)); lot = e.state['lots'][k]
    verify(e, later=100); manage(e)
    assert 'tp' not in e.trade.calls and not e.trade.tps and not lot.get('tp_id')
    assert 'resting' not in ' '.join(e.exit_plan(lot)['steps'])


def test_eligibility_rules():
    ok = E.resting_tp_eligible
    assert ok(dict(tp_r=2, resting_tp=True)) and ok(dict(tp_r=2, resting_tp=True, be_r=1, trail_atr=2, pyramid=dict(n=0)))
    for g in (dict(tp_r=2), dict(tp_r=2, resting_tp=1), dict(tp_r=0, resting_tp=True), dict(resting_tp=True),
              dict(tp_r=2, resting_tp=True, dca=dict(n=3)), dict(tp_r=2, resting_tp=True, grid=True), None):
        assert not ok(g)


def test_manual_trade_never_places_a_tp(monkeypatch):
    e, _ = mk()
    assert e.open_lot(None, 'BTCUSDT', 'LONG', TS.SG, None, e.equity(), risk=0.01, manual=True, stop_atr=2.5, tp_r=2.0)
    assert not e.trade.tps


# ------------------------------------------------------------------ validation + registry
def test_mgmt_validation():
    import app as A
    assert A.clean_mgmt({'tp_r': 2, 'resting_tp': True}) == {'tp_r': 2.0, 'resting_tp': True}
    assert A.clean_mgmt({'resting_tp': False}) == {'resting_tp': False}
    for bad in ('yes', 1, 'true', 0.0):
        with pytest.raises(ValueError): A.clean_mgmt({'resting_tp': bad})


def test_exit_reason_registered_append_only():
    assert E.TP_EXIT == 'take_profit_resting' == TA.EXIT_REASONS[-1]
    assert TA.EXIT_REASONS[:15] == ('stop', 'take_profit', 'take_profit_1', 'take_profit_ladder', 'basket_tp', 'basket_tp_part',
                                    'stop_crossed', 'stop_failed', 'exit_signal', 'time_exit', 'flatten', 'resync',
                                    'manual_close', 'closed_from_panel', 'closed_from_telegram')
    assert len(set(TA.EXIT_REASONS)) == len(TA.EXIT_REASONS) and E.TP_EXIT not in TA.ENTRY_KINDS
    assert not E.BOT_STOP_CID_RE.fullmatch(BC.new_cid('zt')) and E.TP_CID_RE.fullmatch(BC.new_cid('zt'))
