"""T05: fill telemetry (observe only). Every fill the bot sends records expected vs actual price, slippage in bps
(+ = worse for us), requested vs filled qty, wait, maker tries and fallback - without ever changing what is traded."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety import mk_engine, SL, SG, opened                       # noqa: E402
from test_v31_engine import maker_engine, age, lot_of                    # noqa: E402
import engine as E                                                       # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fill_at(e, px):
    """The fake exchange fills at px, while the bot decided at the current mark (its 'expected' price)."""
    real = e.trade.open
    e.trade.open = lambda s, ps, q: dict(real(s, ps, q), avgPrice=str(px))


def recs(e):
    p = e.F['fills']
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def test_market_entry_records_adverse_slippage_for_a_long():
    e, _ = mk_engine()
    fill_at(e, 100.5)                                   # decided at mark 100.0, filled at 100.5
    opened(e)
    r = [x for x in recs(e) if x['kind'] == 'entry_market'][-1]
    assert (r['symbol'], r['side'], r['buy'], r['expected'], r['actual']) == ('BTCUSDT', 'LONG', True, 100.0, 100.5)
    assert r['slip_bps'] == 50.0 and r['outcome'] == 'filled' and r['qty_fill'] == r['qty_req'] > 0 and r['wait_s'] >= 0
    s = e.fill_summary()['by_kind']['entry_market']
    assert s['n'] == 1 and s['slip_avg_bps'] == 50.0 and s['slip_worst_bps'] == 50.0


def test_short_entry_selling_lower_is_adverse_too():
    e, _ = mk_engine()
    fill_at(e, 99.5)
    opened(e, side='SHORT')
    r = recs(e)[-1]
    assert r['buy'] is False and r['expected'] == 100.0 and r['slip_bps'] == 50.0, r


def test_exit_records_the_close_against_the_mark_it_was_decided_at():
    e, _ = mk_engine()
    k = opened(e)
    e.trade.mark['BTCUSDT'] = 100.8                     # decided at mark 101.0, sold at 100.8
    e.close_lot(k, 'signal', mark=101.0)
    r = [x for x in recs(e) if x['kind'] == 'exit'][-1]
    assert (r['side'], r['buy'], r['expected'], r['actual'], r['reason']) == ('LONG', False, 101.0, 100.8, 'signal')
    assert abs(r['slip_bps'] - 19.8) < 0.01


def test_missing_average_price_is_recorded_as_unknown_not_invented():
    e, _ = mk_engine()
    real = e.trade.open
    e.trade.open = lambda s, ps, q: dict(real(s, ps, q), avgPrice='0')
    opened(e)
    r = recs(e)[-1]
    assert r['actual'] is None and r['slip_bps'] is None and r['expected'] == 100.0


def test_maker_full_fill_is_measured_against_the_posted_price():
    e, bk = maker_engine()
    e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    bk.fill(); e.manage(e.trade.marks())
    r = [x for x in recs(e) if x['kind'] == 'entry_maker'][-1]
    assert (r['expected'], r['actual'], r['slip_bps'], r['signal_px'], r['maker_tries'], r['outcome']) == (99.9, 99.9, 0.0, 100.0, 1, 'filled')
    assert 'fallback' not in r and not any(x['kind'] == 'entry_fallback' for x in recs(e))


def test_maker_partial_then_market_fallback_records_both_parts():
    e, bk = maker_engine(); e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    bk.fill(0.4); age(e); e.manage(e.trade.marks())
    for _ in range(3): age(e, total=True); e.manage(e.trade.marks())
    mk = [x for x in recs(e) if x['kind'] == 'entry_maker'][-1]
    fb = [x for x in recs(e) if x['kind'] == 'entry_fallback'][-1]
    assert mk['outcome'] == 'partial' and mk['fallback'] is True and mk['maker_tries'] >= 1
    assert fb['fallback'] is True and fb['outcome'] == 'filled' and fb['qty_fill'] > 0
    assert abs(mk['qty_fill'] + fb['qty_fill'] - lot_of(e)['qty']) < 0.002
    s = e.fill_summary()['by_kind']
    assert s['entry_maker']['partial'] == 1 and s['entry_fallback']['fallback'] == 1


def test_maker_unfilled_with_fallback_records_the_miss_and_the_market_order():
    e, bk = maker_engine(); e.S['SLEEVES'] = [dict(SL, enabled=True)]
    e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    for _ in range(5): age(e, total=True); e.manage(e.trade.marks())
    mk = [x for x in recs(e) if x['kind'] == 'entry_maker'][-1]
    fb = [x for x in recs(e) if x['kind'] == 'entry_fallback'][-1]
    assert mk['outcome'] == 'unfilled' and mk['actual'] is None and mk['fallback'] is True
    assert fb['signal_px'] == 100.0 and fb['maker_tries'] == mk['maker_tries'] and fb['fallback'] is True
    assert e.fill_summary()['by_kind']['entry_maker']['unfilled'] == 1


def test_maker_unfilled_without_fallback_records_only_the_miss():
    e, bk = maker_engine(MAKER_FALLBACK=False); e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    for _ in range(5): age(e, total=True); e.manage(e.trade.marks())
    kinds = [x['kind'] for x in recs(e)]
    assert kinds == ['entry_maker'] and recs(e)[0]['outcome'] == 'unfilled' and 'fallback' not in recs(e)[0]


def test_telemetry_failure_never_blocks_a_trade(tmp_path):
    e, d = mk_engine()
    os.makedirs(e.F['fills'])                           # the log path is a directory: every write fails
    k = opened(e)
    assert e.state['lots'][k]['stop_id'] in e.trade.stops, 'the trade and its stop happened anyway'
    e.close_lot(k, 'signal', mark=100.0)
    assert k not in e.state['lots']


def test_summary_survives_a_restart():
    e, d = mk_engine()
    fill_at(e, 100.2); opened(e)
    e2, _ = mk_engine(d)
    s = e2.fill_summary()
    assert s['by_kind']['entry_market']['n'] == 1 and s['recent'][-1]['actual'] == 100.2


def test_dry_mode_records_nothing():
    e, _ = mk_engine()
    e.dry = True
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert recs(e) == []


def test_status_exposes_the_fill_summary():
    src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
    assert "fills=e.fill_summary() if hasattr(e, 'fill_summary') else None" in src
    e, _ = mk_engine(); opened(e)
    s = e.fill_summary()
    assert set(s) == {'by_kind', 'recent'} and json.dumps(s)          # JSON-serialisable for /api/status


def test_telemetry_does_not_change_what_is_traded():
    """Same decisions with and without a working telemetry file: identical lots, stops and exchange calls."""
    out = []
    for broken in (False, True):
        e, _ = mk_engine()
        if broken: os.makedirs(e.F['fills'])
        k = opened(e); lot = e.state['lots'][k]
        out.append((lot['qty'], lot['avg'], lot['stop'], [c for c in e.trade.calls if isinstance(c, str)]))
    assert out[0] == out[1]


def test_blocked_maker_fallback_is_recorded_as_blocked_not_as_fallback():
    """The slot is not configured, so the market fallback is refused: the record must say so, not claim a fallback."""
    e, bk = maker_engine(); e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    for _ in range(5): age(e, total=True); e.manage(e.trade.marks())
    r = recs(e)[-1]
    assert r['kind'] == 'entry_maker' and r['outcome'] == 'unfilled' and 'fallback' not in r
    assert r['fallback_blocked'] == 'strategy slot switched off' and not any(x['kind'] == 'entry_fallback' for x in recs(e))


def test_zero_slippage_is_not_negative_zero():
    e, _ = mk_engine(); opened(e, side='SHORT')
    assert json.dumps(recs(e)[-1]['slip_bps']) == '0.0'
