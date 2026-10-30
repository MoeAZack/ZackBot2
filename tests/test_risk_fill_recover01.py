"""RISK-FILL-RECOVER-01 (Codex #13 6399929339, PAPER ETHUSDT exp_vwaprev 25 Oct): an entry whose enrichment ended no_trades
is recorded risk_fill 'unmeasured' (fail closed). When FEE/PX-RECOVER later proves that fill's exchange price, the post-fill
check must be decided on it once - the lot no longer reports unmeasured and adds are judged on the measured risk. A record
that was already decided on a measured price is never re-decided."""
import os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_exec_cal00_enrich import mk                                    # noqa: E402
from test_fee_recover01 import RX                                        # noqa: E402
from test_fill_px_recover01 import entry_px_missing, SPLIT               # noqa: E402

W = sum(q * p for q, p in SPLIT)


def test_a_recovered_entry_price_replaces_the_unmeasured_risk_record():
    e, _ = mk(RX); k, lot, ref = entry_px_missing(e)
    assert lot['risk_fill']['0']['state'] == 'unmeasured' and lot['risk_fill']['0']['measured_px'] is None
    assert e._risk_fill_block(lot) == 'risk_unmeasured: an entry price was never measured'
    assert e.cost_recover_tick(force=True) == 'ok'
    d = lot['risk_fill']['0']
    assert d['measured_px'] == pytest.approx(W, abs=1e-9) and d['state'] != 'unmeasured'
    assert d['fee_basis'] == 'actual' and d['remeasured'] == 'risk_unmeasured'
    assert e._risk_fill_block(lot) in (None, 'risk_trim in progress')
    assert e.risk_fill_view(lot)['0']['measured_px'] == pytest.approx(W, abs=1e-9)


def test_an_adverse_recovered_price_makes_its_trim_or_unwind_due():
    e, _ = mk(RX); k, lot, ref = entry_px_missing(e, split=[(1.0, 130.0)])   # far above the booked 100: risk >> plan
    assert e.cost_recover_tick(force=True) == 'ok'
    d = lot['risk_fill']['0']
    assert d['measured_px'] == 130.0 and d['action'] in ('trim', 'unwind') and d['state'] == 'due'
    assert e._risk_fill_block(lot) == 'risk_trim in progress'


def test_a_price_only_recovery_also_decides_the_risk_record(monkeypatch):
    from test_fill_px_recover01 import prefix_fee_recovery
    e, _ = mk(RX); k, lot, ref = entry_px_missing(e)
    prefix_fee_recovery(e, monkeypatch)                     # fee applied, price still missing: stays unmeasured
    assert lot['risk_fill']['0']['state'] == 'unmeasured'
    assert e.cost_recover_tick(force=True) == 'ok'          # the 'px|' job
    d = lot['risk_fill']['0']
    assert d['measured_px'] == pytest.approx(W, abs=1e-9) and d['state'] != 'unmeasured' and d['remeasured']


def test_the_late_decision_survives_a_restart_and_is_never_repeated():
    e, tmp = mk(RX); k, lot, ref = entry_px_missing(e)
    assert e.cost_recover_tick(force=True) == 'ok'
    d = dict(lot['risk_fill']['0'])
    e2, _ = mk(RX, tmp=tmp); e2.trade.rows = e.trade.rows
    l2 = e2.state['lots'][k]
    assert l2['risk_fill']['0'] == d
    e2.state['cost_recover'][ref].update(status='pending', next_s=0)   # a lost state save: the commit runs again
    assert e2.cost_recover_tick(force=True) == 'ok' and l2['risk_fill']['0'] == d


def test_a_record_decided_on_a_measured_price_is_never_re_decided():
    e, _ = mk(RX); k, lot, ref = entry_px_missing(e)
    assert e.cost_recover_tick(force=True) == 'ok'
    d = dict(lot['risk_fill']['0'])
    lot['px_actual'][ref] = 130.0                           # whatever arrives later, the measured decision stands
    e._risk_fill_mark(lot, dict(cost_ref=ref))
    assert lot['risk_fill']['0'] == d


@pytest.mark.parametrize('rec', [dict(why='risk_check_error'), dict(why='risk_unmeasured', attempts=1),
                                 dict(why='risk_unmeasured', measured_px=99.0)])
def test_only_an_untouched_no_price_record_is_replaced(rec):
    e, _ = mk(RX); k, lot, ref = entry_px_missing(e)
    old = dict(dict(fill=0, action='unmeasured', state='unmeasured', attempts=0, measured_px=None), **rec)
    lot['risk_fill']['0'] = dict(old)
    assert e.cost_recover_tick(force=True) == 'ok'
    assert lot['risk_fill']['0'] == old


def test_no_price_yet_keeps_the_record_unmeasured():
    e, _ = mk(RX); k, lot, ref = entry_px_missing(e)
    d = dict(lot['risk_fill']['0'])
    e._risk_fill_mark(lot, dict(cost_ref=ref))
    assert lot['risk_fill']['0'] == d and e._risk_fill_block(lot).startswith('risk_unmeasured')


# ------------------------------------------------------------------ Cowork 6400354056: the lot changed before recovery
def test_a_breakeven_stop_moved_before_recovery_never_unwinds_the_lot():
    e, _ = mk(RX); k, lot, ref = entry_px_missing(e)
    entry_stop = lot['risk_fill']['0']['stop']
    lot['stop'] = lot['fills'][0][3] * 1.0015                # the normal breakeven move, then the price is proven
    assert e.cost_recover_tick(force=True) == 'ok'
    d = lot['risk_fill']['0']
    assert d['stop'] == entry_stop and d['why'] != 'impossible_plan'      # judged on the stop the fill was planned on
    assert d['late_stale'] and d['state'] == 'done' and e._risk_fill_block(lot) is None


def test_a_partial_exit_before_recovery_queues_no_trim():
    e, _ = mk(RX); k, lot, ref = entry_px_missing(e, split=[(1.0, 130.0)])   # adverse enough to want a trim / unwind
    lot['qty'] = lot['qty'] * 0.4
    assert e.cost_recover_tick(force=True) == 'ok'
    d = lot['risk_fill']['0']
    assert d['action'] in ('trim', 'unwind') and d['late_stale'] and d['state'] == 'done'   # evidence, nothing queued
    assert e._risk_fill_block(lot) is None


def test_an_unchanged_lot_still_gets_its_late_trim():
    e, _ = mk(RX); k, lot, ref = entry_px_missing(e, split=[(1.0, 130.0)])
    assert e.cost_recover_tick(force=True) == 'ok'
    d = lot['risk_fill']['0']
    assert d['state'] == 'due' and 'late_stale' not in d
