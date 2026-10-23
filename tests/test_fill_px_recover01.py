"""FILL-PX-RECOVER-01 (#189, PAPER/testnet): the unique userTrades match that FEE-RECOVER-01 uses to prove a timed-out
commission also proves that fill's exchange price - qty-weighted over the one matched order's executions. It is applied
once (px_refs_recovered) and clears px_missing; anything unprovable leaves the price missing. Booked fills never change."""
import json, os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety import opened                                           # noqa: E402
from test_exec_cal00_enrich import mk, recs, fast, OID0                  # noqa: E402,F401
from test_fee_recover01 import RX, missing_entry, truth, ms, tr, failing_writes   # noqa: E402
import actual_costs as AC                                                # noqa: E402
import engine as E                                                       # noqa: E402

SPLIT = [(0.25, 99.0), (0.75, 101.0)]                                    # one entry order, two executions


def entry_px_missing(e, split=SPLIT):
    e.trade.split = split
    k = missing_entry(e)
    e.trade.split = [(1.0, None)]
    lot = e.state['lots'][k]
    ref = lot['cost_refs'][0]
    assert lot['px_missing'] == [ref] and 'fill_px_missing' in lot['fill_px_partial']
    return k, lot, ref


# ------------------------------------------------------------------ pure
def test_the_btc_evidence_gives_the_codex_weighted_exit():
    t0 = ms('2026-10-23T16:01:11.000+00:00')                            # 19:01:11 Cairo, two executions of one BUY
    rows = [tr(1, 77, '0.0017', '0.05', t0, True, sym='BTCUSDT'), tr(2, 77, '0.0169', '0.55', t0 + 40, True, sym='BTCUSDT')]
    rows[0]['price'], rows[1]['price'] = '81638.70', '81642.90'
    st, raw, key, px = AC.match_order(rows, 'BTCUSDT', True, 0.0186, 'USDT', t0 - 1000, t0 + 1000)
    assert st == 'ok' and px == pytest.approx(81642.516129, abs=1e-6) and key == AC.order_key('BTCUSDT', 77)
    assert AC.match_fill(rows, 'BTCUSDT', True, 0.0186, 'USDT', t0 - 1000, t0 + 1000)[0] == 'ok'     # old API unchanged


def test_an_unreadable_price_gives_no_px_and_the_fee_path_is_unchanged():
    t0 = 1_000_000
    rows = [tr(1, 5, '1', '0.1', t0, True), tr(2, 5, '1', '0.1', t0 + 1, True)]
    rows[1]['price'] = 'x'
    st, raw, key, px = AC.match_order(rows, 'AKEUSDT', True, 2, 'USDT', t0 - 10, t0 + 10)
    assert st == 'ok' and raw and px is None


def test_an_unprovable_fee_still_names_the_order_and_its_price():
    t0 = 1_000_000
    rows = [tr(1, 5, '2', '0.1', t0, True, asset='BNB')]
    assert AC.match_order(rows, 'AKEUSDT', True, 2, 'USDT', t0 - 10, t0 + 10)[0::2] == ('non_quote_asset', AC.order_key('AKEUSDT', 5))
    assert AC.match_fill(rows, 'AKEUSDT', True, 2, 'USDT', t0 - 10, t0 + 10) == ('non_quote_asset', None, None)


def test_recover_px_only_for_a_missing_linked_fill_and_once():
    t = dict(fill_refs=[[0, 'c1-1-1'], [1, 'c2-1-1']], px_missing=['c1-1-1'], px_actual={'c2-1-1': 10.0})
    assert not AC.recover_px(t, 'c9-1-1', 5.0)                         # not this target's fill
    assert not AC.recover_px(t, 'c2-1-1', 5.0)                         # already measured
    assert not AC.recover_px(t, 'c1-1-1', 0) and not AC.recover_px(t, 'c1-1-1', 'nan')
    assert AC.recover_px(t, 'c1-1-1', 5.0, 'k1')
    assert t['px_missing'] == [] and t['px_actual']['c1-1-1'] == 5.0 and t['px_refs_recovered'] == ['c1-1-1']
    assert t['cost_order_keys'] == [['c1-1-1', 'k1']]
    assert not AC.recover_px(t, 'c1-1-1', 6.0) and t['px_actual']['c1-1-1'] == 5.0
    bad = dict(fill_refs=[[0, 'c3-1-1']], px_missing=['c3-1-1'], px_actual={'c3-1-1': 7.0})   # inconsistent state
    assert not AC.recover_px(bad, 'c3-1-1', 8.0) and bad['px_actual']['c3-1-1'] == 7.0


def test_a_record_that_has_its_price_is_left_to_the_local_path_without_a_read():
    e, _ = mk(RX); k = opened(e); recs(e); e.apply_costs()
    lot = e.state['lots'][k]; ref = lot['cost_refs'][0]
    assert ref in lot['px_actual']
    lot['px_actual'].pop(ref); lot['px_missing'] = [ref]           # the price exists in its own fill record
    assert e.cost_recover_tick(force=True) == 'idle' and e.trade.win_calls == []
    assert e.state['cost_recover']['px|' + ref]['status'] == 'not_missing'


# ------------------------------------------------------------------ engine: one read recovers fee AND price
def test_a_fee_recovery_also_recovers_the_missing_price_multi_fill_weighted():
    e, _ = mk(RX); k, lot, ref = entry_px_missing(e)
    booked = [list(f) for f in lot['fills']]
    assert e.cost_recover_tick(force=True) == 'ok' and len(e.trade.win_calls) == 1
    w = 0.25 * 99.0 + 0.75 * 101.0
    assert lot['px_actual'][ref] == pytest.approx(w, abs=1e-9) and lot['px_missing'] == []
    assert 'fill_px_missing' not in (lot['fill_px_partial'] or ()) and lot['px_refs_recovered'] == [ref]
    assert lot['fees_actual'] == pytest.approx(truth(e)) and [list(f) for f in lot['fills']] == booked   # booked unchanged
    assert lot['avg_fill_adj'] == pytest.approx(w - booked[0][3], abs=1e-9)
    assert e.state['cost_recover'][ref]['px'] == pytest.approx(w)
    assert e.cost_recover_tick(force=True) == 'idle' and len(e.trade.win_calls) == 1               # nothing left


def test_a_closed_row_gets_its_price_saved_and_logged_without_ids():
    e, tmp = mk(RX); k, lot, ref = entry_px_missing(e)
    e.close_lot(k, 'signal', mark=101.0); recs(e); e.apply_costs()
    h = e.history[-1]; rev = int(h.get('cost_rev') or 0)
    assert 'fill_px_missing' in h['fill_px_partial']
    assert e.cost_recover_tick(force=True) == 'ok'
    assert h['px_missing'] == [] and h['fill_px_partial'] is False and h['cost_rev'] == rev + 1
    saved = json.load(open(os.path.join(tmp, 'history.json')))[-1]
    assert saved['px_actual'][ref] == h['px_actual'][ref] and saved['fill_px_partial'] is False
    assert saved['pnl_fill_adj'] == pytest.approx(h['pnl_fill_adj']) and saved['px_refs_recovered'] == [ref]
    log = [json.loads(x) for x in open(os.path.join(tmp, 'cost_recover.jsonl'))]
    assert log[-1]['px_actual'] == pytest.approx(h['px_actual'][ref])
    txt = open(os.path.join(tmp, 'cost_recover.jsonl')).read() + json.dumps(saved) + json.dumps(e.state['cost_recover'])
    assert str(OID0)[:9] not in txt and 'orderId' not in txt and 'zbcid' not in txt


# ------------------------------------------------------------------ engine: a price-only job (the 19:01 BTC case)
def prefix_fee_recovery(e, monkeypatch):
    """The fee was recovered by the pre-fix code: the commission applied, the price left missing."""
    with monkeypatch.context() as m:
        m.setattr(AC, 'recover_px', lambda *a, **k: False)
        assert e.cost_recover_tick(force=True) == 'ok'


def test_a_fee_recovered_before_the_fix_gets_a_price_only_job(monkeypatch):
    e, _ = mk(RX); k, lot, ref = entry_px_missing(e)
    prefix_fee_recovery(e, monkeypatch)
    assert lot['px_missing'] == [ref] and e.state['cost_recover'][ref]['status'] == 'recovered'
    fa = lot['fees_actual']
    assert e.cost_recover_tick(force=True) == 'ok' and len(e.trade.win_calls) == 2
    job = e.state['cost_recover']['px|' + ref]
    assert job['status'] == 'recovered' and job['mode'] == 'px' and job['px'] == pytest.approx(0.25 * 99 + 0.75 * 101)
    assert lot['px_missing'] == [] and lot['fees_actual'] == fa and lot['cost_fills_missing'] == 0     # fee untouched
    assert e.cost_recover_tick(force=True) == 'idle' and len(e.trade.win_calls) == 2


def test_price_only_restart_is_idempotent(monkeypatch):
    e, tmp = mk(RX); k, lot, ref = entry_px_missing(e)
    prefix_fee_recovery(e, monkeypatch)
    assert e.cost_recover_tick(force=True) == 'ok'
    px = lot['px_actual'][ref]
    e2, _ = mk(RX, tmp=tmp); e2.trade.rows = e.trade.rows
    l2 = e2.state['lots'][k]
    assert e2.cost_recover_tick(force=True) == 'idle' and e2.trade.win_calls == []
    assert l2['px_actual'][ref] == px and l2['px_refs_recovered'] == [ref] and l2['px_missing'] == []
    e2.state['cost_recover']['px|' + ref].update(status='pending', next_s=0)   # the state save was lost, the lot kept it
    assert e2.cost_recover_tick(force=True) == 'ok'
    assert e2.state['cost_recover']['px|' + ref]['why'] == 'already_applied' and l2['px_actual'][ref] == px


def test_price_only_job_after_failed_writes_commits_from_state_without_a_read(monkeypatch):
    e, tmp = mk(RX); k, lot, ref = entry_px_missing(e)
    e.close_lot(k, 'signal', mark=101.0); recs(e); e.apply_costs()
    prefix_fee_recovery(e, monkeypatch)
    on = failing_writes(monkeypatch)
    assert e.cost_recover_tick(force=True) == 'ok'
    on['v'] = False
    st = e.state['cost_recover']['px|' + ref]
    assert st['status'] == 'apply_pending' and st['px'] and 'fill_px_missing' in (
        json.load(open(os.path.join(tmp, 'history.json')))[-1]['fill_px_partial'])
    e2, _ = mk(RX, tmp=tmp); e2.trade.rows = e.trade.rows             # restart: the in-memory revision is gone
    assert e2.cost_recover_tick(force=True) == 'idle' and e2.trade.win_calls == []    # the stored proof, no new read
    h = e2.history[-1]
    assert h['px_missing'] == [] and h['px_refs_recovered'] == [ref] and e2.state['cost_recover']['px|' + ref]['status'] == 'recovered'
    assert json.load(open(os.path.join(tmp, 'history.json')))[-1]['fill_px_partial'] is False


# ------------------------------------------------------------------ fail closed
def test_ambiguity_leaves_the_price_missing_and_opens_no_second_read():
    e, _ = mk(RX); k, lot, ref = entry_px_missing(e)
    oid = OID0 + 1; e.trade.rows[999] = [dict(x, orderId=999, id=x['id'] + 5000) for x in e.trade.rows[oid]]
    assert e.cost_recover_tick(force=True) == 'ambiguous'
    assert lot['px_missing'] == [ref] and ref not in (lot.get('px_actual') or {})
    assert e.cost_recover_tick(force=True) == 'idle' and len(e.trade.win_calls) == 1 and 'px|' + ref not in e.state['cost_recover']


def test_a_wrong_side_or_wrong_size_order_is_never_used():
    e, _ = mk(RX); k, lot, ref = entry_px_missing(e)
    oid = OID0 + 1; rows = e.trade.rows.pop(oid)
    e.trade.rows[555] = [dict(x, orderId=555, buyer=not x['buyer'], price='5') for x in rows]             # wrong side
    e.trade.rows[556] = [dict(x, orderId=556, id=x['id'] + 7000, qty=str(float(x['qty']) * 2), price='6') for x in rows]
    assert e.cost_recover_tick(force=True) == 'no_match'
    assert lot['px_missing'] == [ref] and ref not in (lot.get('px_actual') or {})


def test_an_order_another_fill_holds_is_refused_for_the_price(monkeypatch):
    e, _ = mk(RX); k, lot, ref = entry_px_missing(e)
    prefix_fee_recovery(e, monkeypatch)
    key = e.state['cost_recover'][ref]['order_key']
    lot['cost_order_keys'] = [['c1-0-0', key]]                          # the order now belongs to another fill
    assert e.cost_recover_tick(force=True) == 'order_already_consumed'
    assert lot['px_missing'] == [ref] and e.state['cost_recover']['px|' + ref]['why'] == 'order_already_consumed'


def test_a_non_quote_fee_still_recovers_the_price_with_its_own_job():
    e, _ = mk(RX); e.trade.asset = 'BNB'
    k, lot, ref = entry_px_missing(e)
    assert e.cost_recover_tick(force=True) == 'non_quote_asset' and lot['px_missing'] == [ref]
    assert e.cost_recover_tick(force=True) == 'ok'
    assert lot['px_missing'] == [] and lot['fees_actual'] is None and lot['cost_fills_missing'] == 1   # fee stays missing


def test_live_or_non_testnet_never_reads_for_a_price():
    e, _ = mk(RX); k, lot, ref = entry_px_missing(e)
    e.live = True
    assert e.cost_recover_tick(force=True) == 'live' and e.trade.win_calls == []


def test_a_price_proven_without_its_fee_survives_failed_writes_and_a_restart(monkeypatch):
    e, tmp = mk(RX); e.trade.asset = 'BNB'
    k, lot, ref = entry_px_missing(e)
    e.close_lot(k, 'signal', mark=101.0); recs(e); e.apply_costs()
    assert e.cost_recover_tick(force=True) == 'non_quote_asset'
    on = failing_writes(monkeypatch)
    assert e.cost_recover_tick(force=True) == 'ok'
    st = e.state['cost_recover']['px|' + ref]
    assert st['status'] == 'apply_pending' and st.get('raw') is None and st['px']        # only the price is proven
    on['v'] = False
    e2, _ = mk(RX, tmp=tmp); e2.trade.rows = e.trade.rows
    n = len(e2.trade.win_calls)
    assert e2.cost_recover_tick(force=True) == 'idle' and len(e2.trade.win_calls) == n
    h = e2.history[-1]
    assert h['px_missing'] == [] and h['px_refs_recovered'] == [ref] and h['cost_fills_missing'] >= 1
    assert e2.state['cost_recover']['px|' + ref]['status'] == 'recovered'
