"""FEE-RECOVER-01 (#172, PAPER/testnet): a fill whose commission reads timed out is recovered from ONE bounded userTrades
time-window read - only a unique, complete, quote-asset match, applied once as a revision (cost_rev), never a guess."""
import json, os, sys, time
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety import opened                                           # noqa: E402
from test_exec_cal00_enrich import EX, mk, recs, fast                    # noqa: E402,F401
import actual_costs as AC                                                # noqa: E402
import engine as E                                                       # noqa: E402

T0 = 1_792_522_859_000


def tr(i, oid, qty, comm, t, buyer, asset='USDT', sym='AKEUSDT', maker=False):
    return dict(symbol=sym, id=i, orderId=oid, qty=str(qty), price='0.001', commission=str(comm),
                commissionAsset=asset, time=t, buyer=buyer, maker=maker)


# The #172 shape: entry SELL 96,026; TP BUY 48,013 split 164 + 47,849 under one order; stop BUY 48,013 (resolved).
AKE = [tr(1, 11, 96026, '1.48320319', T0, False),
       tr(2, 12, 164, '0.00252162', T0 + 1_500_000, True, maker=True),
       tr(3, 12, 47849, '0.73569142', T0 + 1_500_200, True, maker=True),
       tr(4, 13, 48013, '0.74485447', T0 + 3_520_000, True)]


# ------------------------------------------------------------------ pure matching
def test_the_172_shape_recovers_entry_and_split_tp_uniquely():
    st, raw = AC.match_fill(AKE, 'AKEUSDT', False, 96026, 'USDT', T0 - 130_000, T0 + 11_000)
    assert st == 'ok' and raw == [dict(asset='USDT', maker=False, amount=1.48320319, n=1)]
    st, raw = AC.match_fill(AKE, 'AKEUSDT', True, 48013, 'USDT', T0 + 1_380_000, T0 + 1_511_000)
    assert st == 'ok' and raw == [dict(asset='USDT', maker=True, amount=pytest.approx(0.73821304), n=2)]
    assert round(1.48320319 + 0.73821304 + 0.74485447, 8) == 2.9662707          # the #172 complete total


def test_two_equal_orders_in_the_window_are_ambiguous_never_picked():
    st, raw = AC.match_fill(AKE, 'AKEUSDT', True, 48013, 'USDT', T0, T0 + 3_600_000)   # TP and stop both inside
    assert (st, raw) == ('ambiguous', None)


def test_an_order_reaching_outside_the_window_is_not_a_candidate():
    st, _ = AC.match_fill(AKE, 'AKEUSDT', True, 48013, 'USDT', T0 + 1_400_000, T0 + 1_500_100)   # TP's 2nd trade outside
    assert st == 'no_match'


@pytest.mark.parametrize('rows,want', [
    ([tr(1, 11, 96026, '1.4', T0, False, asset='BNB')], 'non_quote_asset'),
    ([tr(1, 11, 96026, 'x', T0, False)], 'amount_unreadable'),
    ([dict(tr(1, 11, 96026, '1.4', T0, False), orderId=None)], 'unreadable'),
    ([tr(1, 11, 96026, '1.4', T0, False)] * 3, 'too_many_rows'),
    ('oops', 'unreadable'),
    ([], 'no_match'),
])
def test_every_unprovable_case_fails_closed(rows, want):
    assert AC.match_fill(rows, 'AKEUSDT', False, 96026, 'USDT', T0 - 1, T0 + 1, limit=3) == (want, None)


def test_a_repeated_trade_row_is_counted_once():
    st, raw = AC.match_fill([AKE[0], dict(AKE[0])], 'AKEUSDT', False, 96026, 'USDT', T0 - 1, T0 + 1)
    assert st == 'ok' and raw[0]['amount'] == 1.48320319 and raw[0]['n'] == 1


def test_bad_evidence_is_refused():
    assert AC.match_fill(AKE, 'AKEUSDT', None, 96026, 'USDT', T0 - 1, T0 + 1)[0] == 'bad_evidence'
    assert AC.match_fill(AKE, 'AKEUSDT', False, 0, 'USDT', T0 - 1, T0 + 1)[0] == 'bad_evidence'


def test_recover_fill_is_idempotent_and_keeps_the_estimate():
    t = dict(cost_refs=['a', 'b'], cost_refs_done=['a', 'b'], cost_fills_missing=1, fees=2.9615,
             fees_actual_by_asset={'USDT': 0.74485447}, taker_fills=1)
    raw = [dict(asset='USDT', maker=False, amount=1.48320319, n=1)]
    assert AC.recover_fill(t, 'z', raw) is False                     # not this target's ref
    assert AC.recover_fill(t, 'a', raw) is True
    assert AC.recover_fill(t, 'a', raw) is False                     # once only
    assert AC.recover_fill(t, 'b', raw) is False                     # no missing fill left
    assert t['fees_actual_by_asset'] == {'USDT': pytest.approx(2.22805766)} and t['cost_fills_missing'] == 0
    assert t['fees'] == 2.9615 and t['cost_refs_recovered'] == ['a'] and t['taker_fills'] == 2
    AC.finalize(t, 'USDT', 2)
    assert t['fees_actual_partial'] is False and t['fees_actual'] == pytest.approx(2.22805766)
    two = dict(cost_refs=['a', 'b'], cost_refs_done=['a', 'b'], cost_fills_missing=2)   # both missing: the guard is
    assert AC.recover_fill(two, 'a', raw) and not AC.recover_fill(two, 'a', raw)        # per ref, not the count
    assert two['cost_fills_missing'] == 1 and two['fees_actual_by_asset'] == {'USDT': 1.48320319}


# ------------------------------------------------------------------ engine (PAPER/testnet fake)
class RX(EX):
    """EX whose userTrades rows carry time + buyer, plus the time-window reader (scripted failures)."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.win_calls, self.win_script = [], []

    def _exec(self, name, s, ps, q):
        o, ex = super()._exec(name, s, ps, q)
        for r in self.rows[o['orderId']]:
            r.update(time=int(time.time() * 1000), buyer=(name == 'open') == (ps == 'LONG'), maker=False)
        return o, ex

    def user_trades_window(self, s, lo, hi, limit=1000):
        self.win_calls.append((s, lo, hi))
        if self.win_script:
            r = self.win_script.pop(0)
            if isinstance(r, Exception): raise r
        return [dict(x) for rows in self.rows.values() for x in rows if x['symbol'] == s and lo <= x['time'] <= hi]


def missing_entry(e):
    e.trade.ut_script = [E.BinanceError(-2011, 'unknown order')]          # the entry's own read gives nothing
    k = opened(e); recs(e); e.apply_costs()
    lot = e.state['lots'][k]
    assert lot['fees_actual_partial'] == ['fill_commission_missing'] and lot['cost_fills_missing'] == 1
    return k


def truth(e):
    return sum(float(x['commission']) for rows in e.trade.rows.values() for x in rows)


def test_a_timed_out_lot_commission_is_recovered_once():
    e, _ = mk(RX); k = missing_entry(e); lot = e.state['lots'][k]; est = lot['fees']
    assert e.cost_recover_tick(force=True) == 'ok'
    assert lot['fees_actual_partial'] is False and lot['fees_actual'] == pytest.approx(truth(e))
    assert lot['fees'] == est and lot['cost_refs_recovered'] == lot['cost_refs']
    st = e.state['cost_recover'][lot['cost_refs'][0]]
    assert st['status'] == 'recovered' and st['amount'] == {'USDT': pytest.approx(truth(e))}
    n = len(e.trade.win_calls)
    assert e.cost_recover_tick(force=True) == 'idle' and len(e.trade.win_calls) == n      # nothing left, no read
    assert lot['fees_actual'] == pytest.approx(truth(e))


def test_a_closed_row_is_revised_with_cost_rev_and_saved():
    e, _ = mk(RX); k = missing_entry(e)
    e.close_lot(k, 'signal', mark=101.0); recs(e); e.apply_costs()
    h = e.history[-1]; rev, gross = int(h.get('cost_rev') or 0), h.get('pnl_gross')
    assert h['fees_actual'] is None and 'fill_commission_missing' in h['fees_actual_partial']
    assert e.cost_recover_tick(force=True) == 'ok'
    assert h['fees_actual_partial'] is False and h['fees_actual'] == pytest.approx(truth(e))
    assert h['cost_rev'] == rev + 1 and h['pnl_gross'] == gross
    saved = json.load(open(os.path.join(e.dir, 'history.json')))[-1]
    assert saved['fees_actual'] == h['fees_actual'] and saved['cost_rev'] == h['cost_rev']
    log = [json.loads(x) for x in open(os.path.join(e.dir, 'cost_recover.jsonl'))]
    assert len(log) == 1 and log[0]['id'] == k and log[0]['cost_rev'] == h['cost_rev']
    assert 'orderId' not in json.dumps(log) and 'orderId' not in json.dumps(saved)


def test_restart_never_double_counts(monkeypatch):
    e, tmp = mk(RX); k = missing_entry(e)
    e.close_lot(k, 'signal', mark=101.0); recs(e); e.apply_costs()
    assert e.cost_recover_tick(force=True) == 'ok'
    fa = e.history[-1]['fees_actual']
    e2, _ = mk(RX, tmp=tmp); e2.trade.rows = e.trade.rows
    assert e2.cost_recover_tick(force=True) == 'idle' and e2.trade.win_calls == []
    assert e2.history[-1]['fees_actual'] == pytest.approx(fa) and e2.history[-1]['cost_fills_missing'] == 0


def test_a_crash_after_the_row_was_saved_is_not_applied_twice():
    e, _ = mk(RX); k = missing_entry(e); lot = e.state['lots'][k]
    ref = lot['cost_refs'][0]
    assert e.cost_recover_tick(force=True) == 'ok'
    e.state['cost_recover'][ref].update(status='pending', next_s=0)     # the state save was lost, the lot kept it
    assert e.cost_recover_tick(force=True) == 'ok'
    assert e.state['cost_recover'][ref]['why'] == 'already_applied' and lot['fees_actual'] == pytest.approx(truth(e))


def test_timeout_backs_off_then_recovers():
    e, _ = mk(RX); k = missing_entry(e); lot = e.state['lots'][k]; ref = lot['cost_refs'][0]
    e.trade.win_script = [TimeoutError('read timed out')]
    assert e.cost_recover_tick(force=True) == 'read_failed'
    st = e.state['cost_recover'][ref]
    assert st['status'] == 'pending' and st['tries'] == 1 and st['next_s'] > time.time() + 200
    assert lot['fees_actual'] is None and lot['cost_fills_missing'] == 1                      # nothing guessed
    assert e.cost_recover_tick(force=True) == 'idle'                                          # not due yet
    st['next_s'] = 0
    assert e.cost_recover_tick(force=True) == 'ok' and lot['fees_actual'] == pytest.approx(truth(e))


def test_ambiguity_changes_nothing_and_is_final():
    e, _ = mk(RX); k = missing_entry(e); lot = e.state['lots'][k]; ref = lot['cost_refs'][0]
    oid = next(iter(e.trade.rows)); twin = [dict(x, orderId=999, id=x['id'] + 5000) for x in e.trade.rows[oid]]
    e.trade.rows[999] = twin                                       # a second identical order inside the window
    before = json.dumps(lot, sort_keys=True, default=str)
    assert e.cost_recover_tick(force=True) == 'ambiguous'
    assert json.dumps(lot, sort_keys=True, default=str) == before
    assert e.state['cost_recover'][ref] ['status'] == 'failed'
    assert e.cost_recover_tick(force=True) == 'idle'               # final: never retried into a guess


def test_non_quote_asset_is_final_and_unapplied():
    e, _ = mk(RX); k = missing_entry(e); lot = e.state['lots'][k]
    for rows in e.trade.rows.values():
        for x in rows: x['commissionAsset'] = 'BNB'
    assert e.cost_recover_tick(force=True) == 'non_quote_asset'
    assert lot['fees_actual'] is None and lot['cost_fills_missing'] == 1 and 'cost_refs_recovered' not in lot


def test_a_partially_recorded_commission_is_never_topped_up():
    e, _ = mk(RX); k = missing_entry(e); lot = e.state['lots'][k]; ref = lot['cost_refs'][0]
    p = e.F['fills']
    rs = [json.loads(x) for x in open(p)]
    for r in rs:
        if r.get('cost_ref') == ref: r['commission_raw'] = [dict(asset='USDT', maker=False, amount=0.1, n=1)]
    with open(p, 'w') as f: f.write(''.join(json.dumps(r) + '\n' for r in rs))
    assert e.cost_recover_tick(force=True) == 'idle'
    assert e.state['cost_recover'][ref] == dict(status='failed', why='partial_raw_recorded') and e.trade.win_calls == []


def test_live_or_non_testnet_never_reads():
    e, _ = mk(RX); missing_entry(e)
    e.live = True
    assert e.cost_recover_tick(force=True) == 'live'
    e.live = False; e.trade.base = E.MAINNET
    assert e.cost_recover_tick(force=True) == 'not_testnet' and e.trade.win_calls == []


def test_window_reader_is_one_bounded_audit_read(monkeypatch):
    import binance_client as BC
    c = BC.Futures.__new__(BC.Futures); seen = []
    monkeypatch.setattr(BC.Futures, '_audit_req', lambda self, path, p: seen.append((path, p)) or [])
    assert c.user_trades_window('AKEUSDT', 1, 2) == []
    assert seen == [('/fapi/v1/userTrades', dict(symbol='AKEUSDT', startTime=1, endTime=2, limit=1000))]
