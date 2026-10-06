"""T05: fill telemetry (observe only). Every fill the bot sends records expected vs actual price, slippage in bps
(+ = worse for us), requested vs filled qty, wait, maker tries and fallback - without ever changing what is traded."""
import json, os, sys
import pytest
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
    assert e._fill_flush(5), 'the telemetry writer did not catch up'
    p = e.F['fills']
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def summ(e):
    e._fill_flush(5)
    return e.fill_summary()


def test_market_entry_records_adverse_slippage_for_a_long():
    e, _ = mk_engine()
    fill_at(e, 100.5)                                   # decided at mark 100.0, filled at 100.5
    opened(e)
    r = [x for x in recs(e) if x['kind'] == 'entry_market'][-1]
    assert (r['symbol'], r['side'], r['buy'], r['expected'], r['actual']) == ('BTCUSDT', 'LONG', True, 100.0, 100.5)
    assert r['slip_bps'] == 50.0 and r['outcome'] == 'filled' and r['qty_fill'] == r['qty_req'] > 0 and r['wait_s'] >= 0
    s = summ(e)['by_kind']['entry_market']
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
    assert not any(k.startswith('fallback') for k in r) and not any(x['kind'] == 'entry_fallback' for x in recs(e))


def test_maker_partial_then_market_fallback_records_both_parts():
    e, bk = maker_engine(); e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    bk.fill(0.4); age(e); e.manage(e.trade.marks())
    for _ in range(3): age(e, total=True); e.manage(e.trade.marks())
    mk = [x for x in recs(e) if x['kind'] == 'entry_maker'][-1]
    fb = [x for x in recs(e) if x['kind'] == 'entry_fallback'][-1]
    assert mk['outcome'] == 'partial' and mk['fallback_attempted'] is True and mk['fallback_confirmed'] is True and mk['maker_tries'] >= 1
    assert fb['fallback_order'] is True and fb['outcome'] == 'filled' and fb['qty_fill'] > 0
    assert abs(mk['qty_fill'] + fb['qty_fill'] - lot_of(e)['qty']) < 0.002
    s = summ(e)['by_kind']
    assert s['entry_maker']['partial'] == 1 and s['entry_maker']['fallback'] == 1


def test_maker_unfilled_with_fallback_records_the_miss_and_the_market_order():
    e, bk = maker_engine(); e.S['SLEEVES'] = [dict(SL, enabled=True)]
    e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    for _ in range(5): age(e, total=True); e.manage(e.trade.marks())
    mk = [x for x in recs(e) if x['kind'] == 'entry_maker'][-1]
    fb = [x for x in recs(e) if x['kind'] == 'entry_fallback'][-1]
    assert mk['outcome'] == 'unfilled' and mk['actual'] is None and mk['fallback_confirmed'] is True
    assert fb['signal_px'] == 100.0 and fb['maker_tries'] == mk['maker_tries'] and fb['fallback_order'] is True
    assert summ(e)['by_kind']['entry_maker']['unfilled'] == 1


def test_maker_unfilled_without_fallback_records_only_the_miss():
    e, bk = maker_engine(MAKER_FALLBACK=False); e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    for _ in range(5): age(e, total=True); e.manage(e.trade.marks())
    kinds = [x['kind'] for x in recs(e)]
    assert kinds == ['entry_maker'] and recs(e)[0]['outcome'] == 'unfilled' and not any(k.startswith('fallback') for k in recs(e)[0])


def test_telemetry_failure_never_blocks_a_trade(tmp_path):
    e, d = mk_engine()
    os.makedirs(e.F['fills'])                           # the log path is a directory: every write fails
    k = opened(e)
    assert e.state['lots'][k]['stop_id'] in e.trade.stops, 'the trade and its stop happened anyway'
    e.close_lot(k, 'signal', mark=100.0)
    assert k not in e.state['lots']


def test_summary_survives_a_restart():
    e, d = mk_engine()
    fill_at(e, 100.2); opened(e); assert e._fill_flush(5)
    e2, _ = mk_engine(d)
    s = summ(e2)
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
    s = summ(e)
    assert set(s) == {'by_kind', 'recent', 'telemetry'} and json.dumps(s)          # JSON-serialisable for /api/status


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
    assert r['kind'] == 'entry_maker' and r['outcome'] == 'unfilled' and 'fallback_attempted' not in r and 'fallback_confirmed' not in r
    assert r['fallback_blocked'] == 'strategy slot switched off' and not any(x['kind'] == 'entry_fallback' for x in recs(e))


def test_zero_slippage_is_not_negative_zero():
    e, _ = mk_engine(); opened(e, side='SHORT')
    assert json.dumps(recs(e)[-1]['slip_bps']) == '0.0'


# ---------------------------------------------------------------- Codex T05 review fixes
import tempfile, threading, time                                          # noqa: E402


def block_writer(e):
    """The writer's disk operation hangs until the returned event is set (slow disk, antivirus, file lock)."""
    gate, real = threading.Event(), e._fillw.write
    e._fillw.write = lambda rec: (gate.wait(8), real(rec))[1]
    return gate


def test_a_hung_writer_never_delays_lot_persistence_stops_adds_or_closes():
    e, _ = mk_engine()
    gate = block_writer(e)
    t = time.time()
    k = opened(e)                                                          # market entry -> fill -> lot -> stop
    lot = e.state['lots'][k]
    assert lot['stop_id'] in e.trade.stops and k in json.load(open(e.F['state']))['lots']
    assert e._add_qty(lot, lot['qty'], 100.0, 'pyramid_add')               # add -> fill -> applied
    e._replace_stop(lot); assert lot['stop_id'] in e.trade.stops
    e.close_lot(k, 'signal', mark=100.0)                                   # close -> fill -> applied
    assert k not in e.state['lots'] and time.time() - t < 3, 'order paths must not wait for the telemetry disk'
    assert e._fillw.q.unfinished_tasks >= 2                                 # the records are queued, not lost
    gate.set()
    kinds = [r['kind'] for r in recs(e)]
    assert kinds == ['entry_market', 'pyramid_add', 'exit']


def test_a_full_queue_drops_records_without_waiting_and_counts_them(monkeypatch):
    monkeypatch.setattr(E, 'FILL_QUEUE_MAX', 2)
    e, _ = mk_engine()
    gate = block_writer(e)
    t = time.time()
    for i in range(6): e._fill('exit', 'BTCUSDT', 'LONG', False, 100.0, 100.0, 1, 1)
    assert time.time() - t < 1
    c = e.fill_summary()['telemetry']
    assert c['dropped'] >= 3 and c['accepted'] + c['dropped'] == 6 and c['persisted'] == 0
    gate.set(); e._fill_flush(5)
    c = summ(e)['telemetry']
    assert c['persisted'] == c['accepted'] and summ(e)['by_kind']['exit']['n'] == c['persisted']


def test_failed_writes_leave_no_phantom_counts_and_are_visible():
    e, d = mk_engine()
    os.makedirs(e.F['fills'])                                              # every write fails
    opened(e)
    s = summ(e)
    assert s['by_kind'] == {} and s['recent'] == []                       # nothing claimed that is not on disk
    assert s['telemetry']['write_errors'] >= 1 and s['telemetry']['persisted'] == 0 and s['telemetry']['accepted'] >= 1
    e2, _ = mk_engine(d)
    assert summ(e2)['by_kind'] == {}                                       # the restart agrees


def test_the_summary_window_is_the_same_before_and_after_a_restart_across_rotation(monkeypatch):
    monkeypatch.setattr(E, 'FILL_ROTATE_BYTES', 600)
    monkeypatch.setattr(E, 'FILL_WINDOW', 6)
    e, d = mk_engine()
    for i in range(12): e._fill('exit', 'BTCUSDT', 'LONG', False, 100.0, 100.0 - i * 0.01, 1, 1)
    before = summ(e)
    assert os.path.exists(e.F['fills'] + '.1'), 'rotation happened'
    e2, _ = mk_engine(d)
    after = summ(e2)
    assert before['by_kind'] == after['by_kind'] and before['recent'] == after['recent']
    assert after['by_kind']['exit']['n'] == 6 and after['telemetry']['in_window'] == 6


@pytest.mark.parametrize('qty', [None, '0', ''])
def test_missing_executed_quantity_is_unknown_not_a_full_fill(qty):
    e, _ = mk_engine()
    real = e.trade.open
    e.trade.open = lambda s, ps, q: {k: v for k, v in dict(real(s, ps, q), executedQty=qty).items() if v is not None}
    k = opened(e)
    r = [x for x in recs(e) if x['kind'] == 'entry_market'][-1]
    assert r['qty_fill'] is None and r['outcome'] == 'unknown' and r['qty_req'] > 0
    assert e.state['lots'][k]['qty'] == r['qty_req'], 'trading unchanged: the lot still uses the requested quantity'
    assert summ(e)['by_kind']['entry_market']['unknown'] == 1


def _partial_maker(e, bk):
    e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    bk.fill(0.4); age(e); e.manage(e.trade.marks())
    for _ in range(3): age(e, total=True); e.manage(e.trade.marks())
    return [x for x in recs(e) if x['kind'] == 'entry_maker'][-1], [x for x in recs(e) if x['kind'] == 'entry_fallback']


def test_partial_maker_with_a_blocked_fallback_does_not_claim_it_ran():
    e, bk = maker_engine()
    real = e.entry_block
    e.entry_block = lambda *a, **k: 'daily loss halt' if k.get('manual') else real(*a, **k)
    mk, fb = _partial_maker(e, bk)
    assert mk['outcome'] == 'partial' and 'fallback_attempted' not in mk and 'fallback_confirmed' not in mk and mk['fallback_blocked'] == 'daily loss halt' and fb == []


def test_partial_maker_with_a_failed_fallback_order_records_the_failure():
    e, bk = maker_engine()
    e.trade.fail.add('open')                                               # the market remainder order is refused
    mk, fb = _partial_maker(e, bk)
    assert mk['fallback_attempted'] is True and 'fallback_confirmed' not in mk and 'injected open failure' in mk['fallback_failed'] and fb == []
    assert lot_of(e)['stop_id'] in e.trade.stops                           # the maker part stays protected


def test_partial_maker_without_a_lot_records_the_fallback_as_skipped():
    e, bk = maker_engine()
    e._create_lot = lambda *a, **k: False
    mk, fb = _partial_maker(e, bk)
    assert 'fallback_attempted' not in mk and 'fallback_confirmed' not in mk and mk['fallback_skipped'] == 'maker lot not created' and fb == []


def test_unfilled_maker_whose_market_fallback_fails_records_the_failure():
    e, bk = maker_engine(); e.S['SLEEVES'] = [dict(SL, enabled=True)]
    e.trade.fail.add('open')
    e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    for _ in range(5): age(e, total=True); e.manage(e.trade.marks())
    mk = [x for x in recs(e) if x['kind'] == 'entry_maker'][-1]
    assert mk['outcome'] == 'unfilled' and mk['fallback_attempted'] is True and 'fallback_confirmed' not in mk and mk['fallback_failed']


def test_order_paths_do_no_telemetry_file_io():
    """Static guard: the record is built and queued in the order path; only the writer touches the disk."""
    import inspect
    for fn in (E.Engine._fill, E.Engine._fill_rec, E.Engine._fill_emit):
        src = inspect.getsource(fn)
        assert 'open(' not in src and 'os.' not in src and '.put(' not in src.replace('.put_nowait(', ''), fn.__name__


def test_engines_sharing_a_file_share_one_writer_so_two_never_overlap():
    """Codex round 2 P1: a settings restart makes a new Engine on the same data folder. It gets the SAME process-wide
    writer, so a blocked old writer can never run next to a new one - by construction, not by a timed handover."""
    e, d = mk_engine()
    gate = block_writer(e)
    opened(e)                                                              # the writer is now blocked on a write
    e2, _ = mk_engine(d)                                                   # the settings-restart replacement
    try:
        assert e2._fillw is e._fillw and e._fillw.alive()
        assert [p for p in E._FILL_WRITERS if p == os.path.abspath(e.F['fills'])] == [os.path.abspath(e.F['fills'])]
        e2._fill('exit', 'BTCUSDT', 'LONG', False, 100.0, 100.0, 1, 1)
        assert e2._fillw.thread is e._fillw.thread                          # still the one and only writer thread
    finally:
        gate.set()
    assert 'old._fill_stop' not in open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
    assert summ(e2)['by_kind']['entry_market']['n'] == 1 and summ(e2)['by_kind']['exit']['n'] == 1


def test_a_writer_that_cannot_be_stopped_is_never_replaced_by_a_second_one():
    e, d = mk_engine()
    gate = block_writer(e)
    opened(e)
    t = time.time()
    assert E.close_fill_writer(e.F['fills'], 0.2) is False                 # cannot prove the stop while the write hangs
    assert time.time() - t < 3                                            # bounded
    e2, _ = mk_engine(d)
    assert e2._fillw is e._fillw and e2.fill_summary()['telemetry']['state'] == 'closing'   # visible, not ignored
    e2._fill('exit', 'BTCUSDT', 'LONG', False, 100.0, 100.0, 1, 1)
    assert e2.fill_summary()['telemetry']['dropped'] >= 1                 # drop-only while closing, counted
    assert e2._fillw.thread is e._fillw.thread                              # no second thread for this file
    gate.set(); e._fillw.thread.join(5)
    assert not e._fillw.alive()
    e3, _ = mk_engine(d)
    assert e3._fillw is not e._fillw                                      # only now a fresh writer may exist


def test_closing_works_even_when_the_queue_is_full(monkeypatch):
    monkeypatch.setattr(E, 'FILL_QUEUE_MAX', 1)
    e, _ = mk_engine()
    gate = block_writer(e)
    for _ in range(4): e._fill('exit', 'BTCUSDT', 'LONG', False, 100.0, 100.0, 1, 1)
    assert E.close_fill_writer(e.F['fills'], 0.2) is False
    gate.set()
    e._fillw.thread.join(5)
    assert not e._fillw.alive(), 'the stop signal does not depend on free queue space'


def test_no_writer_thread_until_the_first_record_and_none_left_after_close():
    engines = [mk_engine()[0] for _ in range(5)]
    assert all(x._fillw.thread is None for x in engines), 'lazy: engines that emit nothing start no thread'
    for x in engines: x._fill('exit', 'BTCUSDT', 'LONG', False, 100.0, 100.0, 1, 1)
    threads = [x._fillw.thread for x in engines]
    assert all(t.is_alive() for t in threads)
    assert all(E.close_fill_writer(x.F['fills'], 5) for x in engines)
    assert not any(t.is_alive() for t in threads)
    assert not [p for p in E._FILL_WRITERS if any(p == os.path.abspath(x.F['fills']) for x in engines)]


def test_construction_failure_leaves_no_writer_thread(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setattr(E.Engine, 'connect', lambda self: (_ for _ in ()).throw(RuntimeError('boom')))
    with pytest.raises(RuntimeError): mk_engine(d)
    w = E._FILL_WRITERS.get(os.path.abspath(os.path.join(d, 'fills.jsonl')))
    assert w is None or w.thread is None, 'a half-built engine never started a writer thread'


def test_replay_closes_its_writer():
    src = open(os.path.join(ROOT, 'replay.py'), encoding='utf-8').read()
    fin = src[src.index('    finally:\n        E.Futures, E.now_utc, E.time = saved'):]
    assert "E.close_fill_writer(os.path.join(tmp, 'fills.jsonl'))" in fin[:400]


@pytest.mark.parametrize('qty', [None, '0'])
def test_a_fallback_without_executed_quantity_is_never_confirmed(qty):
    e, bk = maker_engine(); e.S['SLEEVES'] = [dict(SL, enabled=True)]
    real = e.trade.open
    e.trade.open = lambda s, ps, q: {k: v for k, v in dict(real(s, ps, q), executedQty=qty).items() if v is not None}
    e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    for _ in range(5): age(e, total=True); e.manage(e.trade.marks())
    mk = [x for x in recs(e) if x['kind'] == 'entry_maker'][-1]
    fb = [x for x in recs(e) if x['kind'] == 'entry_fallback'][-1]
    assert fb['outcome'] == 'unknown' and mk['fallback_attempted'] is True
    assert 'fallback_confirmed' not in mk and mk['fallback_unconfirmed'] and summ(e)['by_kind']['entry_maker']['fallback'] == 0


def test_partial_maker_fallback_without_executed_quantity_is_never_confirmed():
    e, bk = maker_engine()
    real = e.trade.open
    e.trade.open = lambda s, ps, q: {k: v for k, v in real(s, ps, q).items() if k != 'executedQty'}
    mk, fb = _partial_maker(e, bk)
    assert fb and fb[-1]['outcome'] == 'unknown' and 'fallback_confirmed' not in mk and mk['fallback_unconfirmed']


def test_ambiguous_fallback_answers_are_pending_not_failed():
    e, bk = maker_engine(); e.S['SLEEVES'] = [dict(SL, enabled=True)]
    def lost(s, ps, q): raise E.AmbiguousOrder('answer lost', 'c:x')
    e.trade.open = lost
    e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    for _ in range(5): age(e, total=True); e.manage(e.trade.marks())
    mk = [x for x in recs(e) if x['kind'] == 'entry_maker'][-1]
    assert 'answer lost' in mk['fallback_pending'] and 'fallback_failed' not in mk and 'fallback_confirmed' not in mk
    e2, bk2 = maker_engine()
    e2.trade.open = lost
    mk2, fb2 = _partial_maker(e2, bk2)
    assert 'answer lost' in mk2['fallback_pending'] and 'fallback_failed' not in mk2 and fb2 == []


@pytest.mark.parametrize('line', ['[]', '7', '"text"', 'null', '{"kind": 5}', '{"no_kind": 1}', '{"kind": "exit", "slip_bps": "x"',
                                  '{"kind": "exit", "slip_bps": "bad", "wait_s": [1], "outcome": 3}'])
def test_malformed_lines_never_break_the_status(line):
    e, d = mk_engine()
    with open(e.F['fills'], 'w') as f:
        f.write(json.dumps(dict(kind='entry_market', outcome='filled', slip_bps=5.0, fallback=True)) + '\n')   # old schema
        f.write(line + '\n')
        f.write(json.dumps(dict(kind='entry_maker', outcome='partial', slip_bps=1.0, fallback_confirmed=True)) + '\n')
    E.close_fill_writer(e.F['fills'])                                      # restart: reload from disk
    e2, _ = mk_engine(d)
    s = e2.fill_summary()
    assert json.dumps(s) and s['by_kind']['entry_market']['n'] == 1 and s['by_kind']['entry_maker']['fallback'] == 1
    assert s['by_kind']['entry_market']['fallback'] == 0                   # an old unverified "fallback" claim is not counted
    good = line.startswith('{"kind": "exit", "slip_bps": "bad"')
    assert s['telemetry']['invalid_records'] == (0 if good else 1)
    if good: assert s['by_kind']['exit']['slip_avg_bps'] is None and s['by_kind']['exit']['unknown'] == 1
