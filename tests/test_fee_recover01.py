"""FEE-RECOVER-01 (#172, PAPER/testnet): a fill whose commission reads timed out is recovered from ONE bounded userTrades
time-window read - only a unique, complete, quote-asset match, applied once as a revision (cost_rev), never a guess."""
import json, os, sys, time
from datetime import datetime
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety import opened                                           # noqa: E402
from test_exec_cal00_enrich import EX, mk, recs, fast                    # noqa: E402,F401
import actual_costs as AC                                                # noqa: E402
import engine as E                                                       # noqa: E402

def ms(iso):
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def tr(i, oid, qty, comm, t, buyer, asset='USDT', sym='AKEUSDT', maker=False):
    return dict(symbol=sym, id=i, orderId=oid, qty=str(qty), price='0.001', commission=str(comm),
                commissionAsset=asset, time=t, buyer=buyer, maker=maker)


# The #172 evidence (Codex 6317688635): exact quantities and per-execution commissions; exchange ids synthetic. Record
# times as persisted (20 Oct, Cairo UTC+3): entry 22:00:59, TP 22:24:36, close 22:59:40; wait_s 0.44 gives Codex's windows.
# Execution times are placed inside their record's window in the real order (the exact exchange ms are not in #172).
ENTRY_REC = dict(time='2026-10-20T19:00:59+00:00', wait_s=0.44)
TP_REC = dict(time='2026-10-20T19:24:36+00:00', wait_s=0.44)
T0 = ms('2026-10-20T19:00:58.900+00:00')
AKE = [tr(1, 11, 96026, '1.48320319', T0, False),                                           # entry SELL 96,026
       tr(2, 12, 164, '0.00251075', ms('2026-10-20T19:24:35.200+00:00'), True, maker=True),     # TP BUY 48,013 =
       tr(3, 12, 47849, '0.73570229', ms('2026-10-20T19:24:35.400+00:00'), True, maker=True),   # 164 + 47,849, one order
       tr(4, 13, 48013, '0.74485447', ms('2026-10-20T19:59:39.500+00:00'), True)]               # stop BUY (resolved)


# ------------------------------------------------------------------ pure matching
def test_the_runtime_windows_are_the_persisted_record_windows():
    assert E.Engine._cost_recover_window(ENTRY_REC) == (ms('2026-10-20T18:58:58.560+00:00'),
                                                       ms('2026-10-20T19:01:10+00:00'))       # 21:58:58.56-22:01:10 Cairo
    assert E.Engine._cost_recover_window(TP_REC) == (ms('2026-10-20T19:22:35.560+00:00'),
                                                    ms('2026-10-20T19:24:47+00:00'))          # 22:22:35.56-22:24:47 Cairo


def test_the_172_evidence_recovers_entry_and_split_tp_uniquely():
    st, raw, _k = AC.match_fill(AKE, 'AKEUSDT', False, 96026, 'USDT', *E.Engine._cost_recover_window(ENTRY_REC))
    assert st == 'ok' and raw == [dict(asset='USDT', maker=False, amount=1.48320319, n=1)]
    st, raw, _k = AC.match_fill(AKE, 'AKEUSDT', True, 48013, 'USDT', *E.Engine._cost_recover_window(TP_REC))
    assert st == 'ok' and raw == [dict(asset='USDT', maker=True, amount=0.73821304, n=2)]   # 0.00251075 + 0.73570229
    assert round(1.48320319 + raw[0]['amount'] + 0.74485447, 8) == 2.9662707          # the #172 complete total


def test_two_equal_orders_in_the_window_are_ambiguous_never_picked():
    st, raw, _k = AC.match_fill(AKE, 'AKEUSDT', True, 48013, 'USDT', T0, T0 + 3_600_000)   # TP and stop both inside
    assert (st, raw) == ('ambiguous', None)


def test_an_order_straddling_the_window_fails_closed_never_skipped():
    """Cowork H1: the fill's own order straddles hi while a same-side same-qty sibling lies wholly inside - skipping the
    straddler would book the sibling's fee. A straddling candidate makes the match ambiguous."""
    lo, _hi = E.Engine._cost_recover_window(TP_REC)
    hi = ms('2026-10-20T19:24:35.300+00:00')                                     # the TP's 2nd execution is outside
    st, _, _k = AC.match_fill(AKE, 'AKEUSDT', True, 48013, 'USDT', lo, hi)
    assert st == 'ambiguous'
    sib = AKE + [tr(5, 14, 48013, '0.7448', ms('2026-10-20T19:24:00+00:00'), True)]   # a resolved sibling inside
    assert AC.match_fill(sib, 'AKEUSDT', True, 48013, 'USDT', lo, hi) == ('ambiguous', None, None)
    other = [tr(1, 11, 30000, '0.4', lo - 5, True), tr(2, 11, 18000, '0.3', lo + 5, True)]   # a different qty straddles
    assert AC.match_fill(other + [AKE[3]], 'AKEUSDT', True, 48013, 'USDT', lo, hi)[0] == 'no_match'


@pytest.mark.parametrize('rows,want', [
    ([tr(1, 11, 96026, '1.4', T0, False, asset='BNB')], 'non_quote_asset'),
    ([tr(1, 11, 96026, 'x', T0, False)], 'amount_unreadable'),
    ([dict(tr(1, 11, 96026, '1.4', T0, False), orderId=None)], 'unreadable'),
    ([tr(1, 11, 96026, '1.4', T0, False)] * 3, 'too_many_rows'),
    ('oops', 'unreadable'),
    ([], 'no_match'),
])
def test_every_unprovable_case_fails_closed(rows, want):
    assert AC.match_fill(rows, 'AKEUSDT', False, 96026, 'USDT', T0 - 1, T0 + 1, limit=3) == (want, None, None)


def test_a_repeated_trade_row_is_counted_once():
    st, raw, _k = AC.match_fill([AKE[0], dict(AKE[0])], 'AKEUSDT', False, 96026, 'USDT', T0 - 1, T0 + 1)
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


# ------------------------------------------------------------------ P1 (Codex 6317688635): durable commit protocol
def failing_writes(monkeypatch):
    on, rev, aux = {'v': True}, E.Engine._cohort_rev, E.Engine._save_aux
    monkeypatch.setattr(E.Engine, '_cohort_rev', lambda self, row, cid=None: False if on['v'] else rev(self, row, cid))
    monkeypatch.setattr(E.Engine, '_save_aux',
                        lambda self, n, o: False if on['v'] and n == 'history' else aux(self, n, o))
    return on


def closed_missing(e):
    k = missing_entry(e)
    e.close_lot(k, 'signal', mark=101.0); recs(e); e.apply_costs()
    h = e.history[-1]
    assert h['fees_actual'] is None and h['cost_fills_missing'] == 1
    return k, h['cost_refs'][0]


def disk_row(tmp):
    return json.load(open(os.path.join(tmp, 'history.json')))[-1]


def test_failed_durable_writes_then_restart_commit_from_state_without_a_read(monkeypatch):
    e, tmp = mk(RX); k, ref = closed_missing(e)
    on = failing_writes(monkeypatch)
    assert e.cost_recover_tick(force=True) == 'ok'
    st = e.state['cost_recover'][ref]
    assert st['status'] == 'apply_pending' and st['why'] == 'persist_failed' and st['raw']   # never terminal in memory only
    assert disk_row(tmp)['fees_actual'] is None and disk_row(tmp)['cost_fills_missing'] == 1
    on['v'] = False
    e2, _ = mk(RX, tmp=tmp); e2.trade.rows = e.trade.rows             # restart: the in-memory revision is gone
    h = e2.history[-1]
    assert h['id'] == k and h['fees_actual'] is None and h['cost_fills_missing'] == 1
    assert e2.cost_recover_tick(force=True) == 'idle' and e2.trade.win_calls == []    # the stored proof, no new read
    st = e2.state['cost_recover'][ref]
    assert st['status'] == 'recovered' and 'raw' not in st
    assert h['fees_actual'] == pytest.approx(truth(e)) and h['cost_fills_missing'] == 0 and h['cost_refs_recovered'] == [ref]
    assert disk_row(tmp)['fees_actual'] == pytest.approx(truth(e)) and disk_row(tmp)['cost_refs_recovered'] == [ref]
    e3, _ = mk(RX, tmp=tmp)                                            # a further restart: nothing left, nothing doubled
    assert e3.cost_recover_tick(force=True) == 'idle' and e3.trade.win_calls == []
    assert e3.history[-1]['fees_actual'] == pytest.approx(truth(e)) and e3.history[-1]['cost_fills_missing'] == 0


def test_failed_durable_writes_without_restart_retry_the_save_only(monkeypatch):
    e, tmp = mk(RX); k, ref = closed_missing(e)
    on = failing_writes(monkeypatch)
    assert e.cost_recover_tick(force=True) == 'ok'
    h = e.history[-1]; fa, rev = h['fees_actual'], h['cost_rev']
    assert fa == pytest.approx(truth(e)) and e.state['cost_recover'][ref]['status'] == 'apply_pending'
    assert e.cost_recover_tick(force=True) == 'idle'                   # still failing: still pending, applied once
    assert e.state['cost_recover'][ref]['status'] == 'apply_pending' and h['cost_fills_missing'] == 0
    on['v'] = False
    assert e.cost_recover_tick(force=True) == 'idle' and len(e.trade.win_calls) == 1
    assert e.state['cost_recover'][ref]['status'] == 'recovered' and h['fees_actual'] == pytest.approx(fa)
    assert h['cost_rev'] > rev and disk_row(tmp)['fees_actual'] == pytest.approx(fa)


def test_a_commit_whose_target_never_loads_ends_final_and_frees_the_queue():
    e, _ = mk(RX); k, ref = closed_missing(e)
    e.state.setdefault('cost_recover', {})[ref] = dict(status='apply_pending', raw=[dict(asset='USDT', maker=False, amount=1.0, n=1)],
                                        symbol='BTCUSDT')
    e.history.clear(); e.cohort_rows = []
    for _ in range(E.Engine.COST_RECOVER_TRIES):
        e.cost_recover_tick(force=True)
    assert e.state['cost_recover'][ref] == dict(status='failed', why='target_gone', symbol='BTCUSDT',
                                                commit_tries=E.Engine.COST_RECOVER_TRIES)


# ------------------------------------------------------------------ F1 (Cowork 6317772714, Codex 6317814442): one order, one fill
def test_the_match_returns_a_hashed_order_key_never_the_id():
    st, raw, key = AC.match_fill(AKE, 'AKEUSDT', True, 48013, 'USDT', *E.Engine._cost_recover_window(TP_REC))
    assert st == 'ok' and key == AC.order_key('AKEUSDT', 12) and len(key) == 16 and '12' != key
    assert AC.order_key('AKEUSDT', 12) != AC.order_key('AKEUSDT', 13) != AC.order_key('XRPUSDT', 13)
    t = dict(cost_refs=['a'], cost_refs_done=['a'], cost_fills_missing=1)
    assert AC.recover_fill(t, 'a', raw, key) and t['cost_order_keys'] == [['a', key]]
    assert AC.order_owner(t, key) == 'a' and AC.order_owner(t, 'x' * 16) is None


def twin_ref(e, k):
    """A second missing fill on the same lot: same symbol / side / qty / time as the first (overlapping windows), but
    the exchange shows ONE order - the Cowork F1 shape."""
    lot = e.state['lots'][k]; a = lot['cost_refs'][0]; b = a + '-twin'
    p = e.F['fills']
    rs = [json.loads(x) for x in open(p)]
    rec = next(r for r in rs if r.get('cost_ref') == a)
    with open(p, 'a') as f: f.write(json.dumps(dict(rec, cost_ref=b)) + '\n')
    lot['cost_refs'].append(b); lot['cost_refs_done'].append(b)
    lot['cost_fills_missing'] = int(lot['cost_fills_missing']) + 1
    return lot, a, b


def test_two_refs_one_order_overlapping_windows_never_book_the_fee_twice():
    e, _ = mk(RX); k = missing_entry(e); lot, a, b = twin_ref(e, k)
    assert e.cost_recover_tick(force=True) == 'ok'
    assert e.cost_recover_tick(force=True) == 'order_already_consumed'
    st = e.state['cost_recover']
    won, lost = (a, b) if st[a]['status'] == 'recovered' else (b, a)
    assert st[won]['status'] == 'recovered' and st[lost] == dict(st[lost], status='failed', why='order_already_consumed')
    assert lot['fees_actual_by_asset'] == {'USDT': pytest.approx(truth(e))}         # one order's fee, once
    assert lot['cost_fills_missing'] == 1 and lot['fees_actual'] is None              # the other fill stays unproven
    assert lot['cost_order_keys'] == [[won, st[won]['order_key']]] and 'raw' not in st[lost]


def test_the_consumed_key_survives_a_restart_even_without_its_state(monkeypatch):
    e, tmp = mk(RX); k = missing_entry(e); lot, a, b = twin_ref(e, k)
    e.state.setdefault('cost_recover', {})[b] = dict(status='pending', symbol='hold', next_s=10 ** 12)   # b waits
    assert e.cost_recover_tick(force=True) == 'ok' and e.state['cost_recover'][a]['status'] == 'recovered'
    e.state['cost_recover'].pop(b); e.state['cost_recover'].pop(a)        # state progress lost; the lot keeps the key
    e.save_state()
    e2, _ = mk(RX, tmp=tmp); e2.trade.rows = e.trade.rows
    assert e2.cost_recover_tick(force=True) == 'order_already_consumed'                       # b: refused by the lot's key
    assert a not in e2.state['cost_recover']                           # a is not rediscovered: the lot lists it recovered
    lot2 = e2.state['lots'][k]
    assert lot2['fees_actual_by_asset'] == {'USDT': pytest.approx(truth(e))} and lot2['cost_fills_missing'] == 1


def test_a_pending_commit_owns_its_order_before_it_is_durable(monkeypatch):
    e, _ = mk(RX); k, ref = closed_missing(e)
    on = failing_writes(monkeypatch)
    assert e.cost_recover_tick(force=True) == 'ok' and e.state['cost_recover'][ref]['status'] == 'apply_pending'
    key = e.state['cost_recover'][ref]['order_key']
    assert e._cost_order_consumer(key, 'someone-else') == ref and e._cost_order_consumer(key, ref) is None
    on['v'] = False
    assert e.cost_recover_tick(force=True) == 'idle' and e.state['cost_recover'][ref]['status'] == 'recovered'
    log = [json.loads(x) for x in open(os.path.join(e.dir, 'cost_recover.jsonl'))]
    assert log[-1]['order_key'] == key and 'orderId' not in json.dumps(log)


def test_after_a_restart_a_pending_commit_still_owns_its_order(monkeypatch):
    e, tmp = mk(RX); k, ref = closed_missing(e)
    on = failing_writes(monkeypatch)
    assert e.cost_recover_tick(force=True) == 'ok'
    key = e.state['cost_recover'][ref]['order_key']
    on['v'] = False
    e2, _ = mk(RX, tmp=tmp)                                            # the disk row has no key yet: state alone owns it
    assert AC.order_owner(e2.history[-1], key) is None
    assert e2._cost_order_consumer(key, 'another-ref') == ref


def test_a_pending_commit_whose_order_another_fill_holds_is_refused_not_applied():
    e, _ = mk(RX); k = missing_entry(e); lot = e.state['lots'][k]; ref = lot['cost_refs'][0]
    lot['cost_order_keys'] = [['other-ref', 'k' * 16]]
    e.state.setdefault('cost_recover', {})[ref] = dict(status='apply_pending', order_key='k' * 16, symbol='BTCUSDT',
                                                       raw=[dict(asset='USDT', maker=False, amount=1.0, n=1)])
    e.cost_recover_tick(force=True)
    assert e.state['cost_recover'][ref]['status'] == 'failed' and e.state['cost_recover'][ref]['why'] == 'order_already_consumed'
    assert lot['cost_fills_missing'] == 1 and 'cost_refs_recovered' not in lot and not lot.get('fees_actual_by_asset')
