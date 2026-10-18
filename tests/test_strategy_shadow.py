"""TRUE-BOOK-02 (TB02a) Build tests: shadow-model-v1 exit parity with backtest.walk_path, funding exposure timing,
unsupported management, and the engine A/B (observer on / off / throwing / disk-failing -> identical trading).
Codex's independent acceptance harness is tests/test_true_book_shadow_adversarial.py. Fakes only, no network."""
import copy, json, os, sys, tempfile
from datetime import datetime, timedelta, timezone

import numpy as np, pandas as pd, pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['LOCALAPPDATA'] = tempfile.mkdtemp()            # never the real %LOCALAPPDATA%\ZackBot
from test_safety import mk_engine                          # noqa: E402  (FakeX engine, PAPER)
import engine as E                                         # noqa: E402
import strategy_shadow as SSH                              # noqa: E402

T0 = datetime(2026, 10, 18, 1, 0, tzinfo=timezone.utc)
SLIP = 2.0 / 1e4
FEE = 0.0005


def iso(m=0):
    return (T0 + timedelta(minutes=m)).isoformat()


def book(tmp_path, name='s.jsonl'):
    return SSH.ShadowBook(tmp_path / name, 'C1', dict(SSH.MODEL_V1), start=2000.0, min_n=20)


def slot(key='ema_mom', sid='A', **mgmt):
    m = dict(stop_atr=1.0)
    m.update(mgmt)
    return dict(id=sid, key=key, tf='4h', sides='both', risk=0.02, max_pos=1, share=1.0, params={}, mgmt=m)


def enter(b, sl, side='LONG', sym='BTCUSDT', px=100.0, atr=2.0, t=0):
    return b.on_signal(slot=sl, symbol=sym, side=side, signal=dict(time=iso(t), close=px, atr=atr, le=side == 'LONG',
                       se=side == 'SHORT', lx=False, sx=False), mark=dict(time=iso(t), price=px, source='premiumIndex'),
                       live_decision=dict(status='skipped', reason='x'))


def bar(b, m, o, h, l, c, sym='BTCUSDT', atr=None, sig=None, key='ema_mom'):
    b.on_bar(tf='4h', bars={sym: dict(time=iso(m), o=o, h=h, l=l, c=c, atr=atr)}, signals={key: {sym: sig}} if sig else {})


def ep(b, rec):
    return b.snapshot()['episodes'][rec['candidate_id']]


E0 = 100.0 * (1 + SLIP)                                    # long entry at the observed mark + 2 bps


# ------------------------------------------------------------------ exit parity (backtest.walk_path semantics)
def test_stop_first_when_the_opens_stop_is_inside_the_candle(tmp_path):
    b = book(tmp_path); r = enter(b, slot(tp_r=1.0))
    bar(b, 10, o=100, h=103, l=97, c=98)                   # red: the path meets the target first, but the open's stop
                                                           # (98.02) is inside the candle -> stop-first convention
    e = ep(b, r)
    assert e['exit_reason'] == 'stop' and e['exit'] == pytest.approx((E0 - 2) * (1 - SLIP))


def test_red_candle_path_reaches_the_target_first(tmp_path):
    b = book(tmp_path); r = enter(b, slot(tp_r=1.0))
    bar(b, 10, o=100, h=103, l=99, c=99.5)                 # o -> h -> l -> c, stop not in candle
    e = ep(b, r)
    assert e['exit_reason'] == 'tp' and e['exit'] == pytest.approx((E0 + 2) * (1 - SLIP))


def test_gap_through_stop_fills_at_the_open(tmp_path):
    b = book(tmp_path); r = enter(b, slot(tp_r=1.0))
    bar(b, 10, o=97, h=99, l=97, c=98.5)                   # opens through the stop, never trades lower afterwards
    assert ep(b, r)['exit_reason'] == 'stop' and ep(b, r)['exit'] == pytest.approx(97 * (1 - SLIP))


def test_gap_through_target_fills_at_the_level_like_the_backtest(tmp_path):
    b = book(tmp_path); r = enter(b, slot(tp_r=1.0))
    bar(b, 10, o=105, h=106, l=104.5, c=105.5)
    assert ep(b, r)['exit_reason'] == 'tp' and ep(b, r)['exit'] == pytest.approx((E0 + 2) * (1 - SLIP))


def test_breakeven_ratchets_after_the_leg_and_is_hit_later(tmp_path):
    b = book(tmp_path); r = enter(b, slot(be_r=1.0))
    bar(b, 10, o=100, h=102.5, l=99.5, c=102)              # best 102.5 >= e0 + 1R -> stop to avg
    assert ep(b, r)['status'] == 'open' and ep(b, r)['stop'] == pytest.approx(E0)
    bar(b, 20, o=101, h=101.5, l=99, c=99.5)
    assert ep(b, r)['exit_reason'] == 'stop' and ep(b, r)['exit'] == pytest.approx(E0 * (1 - SLIP))


def test_trail_uses_the_last_closed_candles_atr_not_the_current_one(tmp_path):
    b = book(tmp_path); r = enter(b, slot(trail_atr=1.0), atr=2.0)
    bar(b, 10, o=100, h=104, l=99.9, c=103.9, atr=10.0)    # this candle's own ATR (10) applies from the NEXT candle
    assert ep(b, r)['stop'] == pytest.approx(102.0)
    assert ep(b, r)['atr'] == 10.0


def test_tp1_partial_then_target_in_price_order(tmp_path):
    b = book(tmp_path); r = enter(b, slot(tp1_r=1.0, tp1_frac=0.5, tp_r=2.0))
    q = ep(b, r)['qty']
    bar(b, 10, o=100, h=104.5, l=99.5, c=104)
    e = ep(b, r)
    g = 0.5 * q * ((E0 + 2) * (1 - SLIP) - E0) + 0.5 * q * ((E0 + 4) * (1 - SLIP) - E0)
    assert e['exit_reason'] == 'tp' and e['gross'] == pytest.approx(g)


def test_time_exit_after_max_bars_at_the_close(tmp_path):
    b = book(tmp_path); r = enter(b, slot(max_bars=2))
    for k in (1, 2):
        bar(b, 10 * k, o=100, h=100.5, l=99.5, c=100.2)
        assert ep(b, r)['status'] == 'open'
    bar(b, 30, o=100, h=100.5, l=99.5, c=100.3)
    assert ep(b, r)['exit_reason'] == 'time' and ep(b, r)['exit'] == pytest.approx(100.3 * (1 - SLIP))


def test_signal_exit_at_the_close(tmp_path):
    b = book(tmp_path); r = enter(b, slot())
    bar(b, 10, o=100, h=100.5, l=99.5, c=100.4, sig=dict(lx=True, sx=False))
    assert ep(b, r)['exit_reason'] == 'signal' and ep(b, r)['exit'] == pytest.approx(100.4 * (1 - SLIP))


def test_short_mirror(tmp_path):
    b = book(tmp_path); r = enter(b, slot(tp_r=1.0), side='SHORT')
    e0 = 100.0 * (1 - SLIP)
    bar(b, 10, o=100, h=100.5, l=97, c=97.5)               # green? no: red, o -> h -> l: target e0 - 2 reached
    assert ep(b, r)['exit_reason'] == 'tp' and ep(b, r)['exit'] == pytest.approx((e0 - 2) * (1 + SLIP))


def test_bar_at_or_before_the_entry_is_never_managed(tmp_path):
    b = book(tmp_path); r = enter(b, slot(tp_r=1.0), t=10)
    bar(b, 10, o=100, h=110, l=90, c=100)                  # the decision candle itself (closed at the decision time)
    assert ep(b, r)['status'] == 'open' and ep(b, r)['bars'] == 0


def test_unsupported_management_is_incomplete_and_unmanaged(tmp_path):
    b = book(tmp_path)
    r = b.on_signal(slot=dict(slot('dca_dip'), mgmt={}), symbol='BTCUSDT', side='LONG',
                    signal=dict(time=iso(), close=100, atr=2, le=True), mark=dict(time=iso(), price=100),
                    live_decision=dict(status='taken', reason=''))
    bar(b, 10, o=100, h=200, l=1, c=100)
    row = next(x for x in b.compute()['strategies'] if x['key'] == 'dca_dip')
    assert any(w.startswith('unsupported_mgmt:dca') for w in row['incomplete']) and not row['complete']
    assert ep(b, r)['bars'] == 0


def test_exchange_filter_rejection_is_an_explicit_outcome(tmp_path):
    b = book(tmp_path)
    r = b.on_signal(slot=slot(), symbol='BTCUSDT', side='LONG', signal=dict(time=iso(), close=100, atr=2),
                    mark=dict(time=iso(), price=100), live_decision=dict(status='skipped', reason='x'),
                    rule=dict(step=1.0, min_qty=1.0, min_notional=1e6))
    assert ep(b, r)['status'] == 'rejected' and ep(b, r)['exit_reason'].startswith('exchange_filter:')


# ------------------------------------------------------------------ funding exposure
def test_close_phase_exit_at_the_funding_instant_pays_it_intrabar_exit_does_not(tmp_path):
    b = book(tmp_path)
    at = 7 * 60 * 60 // 60                                 # T0 01:00 + 7h = 08:00 UTC settlement, in minutes
    a = enter(b, slot('ema_mom', 'A'), sym='BTCUSDT')
    c = enter(b, slot('donchian_ens', 'D'), sym='BTCUSDT')
    b.on_bar(tf='4h', bars={'BTCUSDT': dict(time=iso(at), o=100, h=100.5, l=99.5, c=100.1)},
             signals={'ema_mom': {'BTCUSDT': dict(lx=True)}})
    assert ep(b, a)['exit_reason'] == 'signal' and ep(b, c)['status'] == 'open'
    b.on_funding(symbol='BTCUSDT', at=iso(at), rate=0.001, mark=100.0)    # the record lags the bar: still settled
    assert ep(b, a)['funding'] == pytest.approx(-ep(b, a)['q0'] * 100 * 0.001)
    b2 = book(tmp_path, 'b2.jsonl')
    s = enter(b2, slot(tp_r=1.0))
    b2.on_bar(tf='4h', bars={'BTCUSDT': dict(time=iso(at), o=100, h=103, l=99.5, c=101)}, signals={})
    b2.on_funding(symbol='BTCUSDT', at=iso(at), rate=0.001, mark=100.0)
    assert ep(b2, s)['exit_reason'] == 'tp' and ep(b2, s)['funding'] == 0.0


def test_exposure_across_a_settlement_without_a_record_is_unread_never_zero(tmp_path):
    b = book(tmp_path)
    b.on_cadence(symbol='BTCUSDT', interval_s=8 * 3600, source='test')
    enter(b, slot())
    bar(b, 8 * 60, o=100, h=100.5, l=99.5, c=100.1)        # 09:00 UTC: exposed at 08:00, no funding record
    row = b.compute()['strategies'][0]
    assert not row['complete'] and any(w.startswith('funding_unread:BTCUSDT@') for w in row['incomplete'])
    b.on_funding(symbol='BTCUSDT', at='2026-10-18T08:00:00+00:00', rate=0.0001, mark=100.0)
    row = b.compute()['strategies'][0]
    assert row['complete'] and row['funding'] < 0


def test_funding_conflict_is_a_coverage_gap(tmp_path):
    b = book(tmp_path)
    b.on_funding(symbol='BTCUSDT', at=iso(), rate=0.001, mark=100.0)
    b.on_funding(symbol='BTCUSDT', at=iso(), rate=0.002, mark=100.0)
    assert not b.snapshot()['coverage']['complete']


def test_model_mismatch_on_reload_is_a_gap(tmp_path):
    b = book(tmp_path); enter(b, slot())
    other = SSH.ShadowBook(tmp_path / 's.jsonl', 'C1', dict(SSH.MODEL_V1, fee_rate=0.0004))
    assert not other.snapshot()['coverage']['complete'] and other.snapshot()['episodes'] == {}


# ------------------------------------------------------------------ engine A/B: the observer never changes trading
FIXED = datetime(2026, 10, 4, 8, 0, 30, tzinfo=timezone.utc)
SLA = E.sleeve('A', 'ema_mom', 0.5, 0.02, 1, 'all')        # max 1 position -> the second coin is skipped


def frame(px, n=260, t0='2026-09-01'):
    t = pd.date_range(t0, periods=n, freq='4h')
    c = np.linspace(px * 0.9, px, n)
    return pd.DataFrame(dict(t=t, o=c, h=c * 1.01, l=c * 0.99, c=c, v=np.full(n, 1000.0), atr=np.full(n, px * 0.02),
                             e50=c * 0.98, st=np.ones(n)))


def sig(time, le=False, lx=False, close=100.0):
    return dict(close=close, atr=2.0, time=time, le=le, se=False, lx=lx, sx=False)


SCRIPT = [
    {'BTCUSDT': sig('2026-10-04 04:00:00', le=True), 'ETHUSDT': sig('2026-10-04 04:00:00', le=True, close=50.0)},
    {'BTCUSDT': sig('2026-10-04 04:00:00', lx=True), 'ETHUSDT': sig('2026-10-04 04:00:00', le=True, close=50.0)},
]


def run_fixture(tmp, monkeypatch, on, mode=None):
    monkeypatch.setattr(E, 'now_utc', lambda: FIXED)
    monkeypatch.setattr(E.time, 'time', lambda: FIXED.timestamp())
    os.makedirs(str(tmp), exist_ok=True)
    e, _ = mk_engine(str(tmp))
    e.S['STRATEGY_SHADOW'] = on
    e.S['SLEEVES'] = [SLA]
    e.state['cohort'] = dict(id='CH-AB')
    e.marks, e.marks_t = {'BTCUSDT': 100.0, 'ETHUSDT': 50.0}, FIXED.timestamp()
    calls = []
    for name in ('open', 'close', 'stop', 'cancel'):
        f = getattr(e.trade, name)
        def wrap(*a, _f=f, _n=name):
            calls.append((_n,) + tuple(a)); return _f(*a)
        setattr(e.trade, name, wrap)
    if mode == 'boom':
        def raise_(*a, **k): raise RuntimeError('shadow boom')
        for n in ('on_signal', 'on_bar', 'complete_cycle'):
            monkeypatch.setattr(SSH.ShadowBook, n, raise_)
    if mode == 'disk':
        def bad(self, rec): raise OSError('disk full')
        monkeypatch.setattr(SSH.ShadowBook, '_append', bad)
    frames = {'BTCUSDT': frame(100.0), 'ETHUSDT': frame(50.0)}
    for sigs in SCRIPT:
        e.compute_signals = lambda tf, syms, extra=(), sigs=sigs: ({f'A|{s}': dict(v) for s, v in sigs.items()}, frames)
        e.cycle('4h')
    state = copy.deepcopy(e.state)
    return e, dict(calls=calls, state=json.dumps(state, sort_keys=True, default=str),
                   history=json.dumps(e.history, sort_keys=True, default=str),
                   missed=[(m['symbol'], m['candle'], m['reason']) for m in e.missed])


def shadow_dir(e):
    return os.path.join(e.dir, e.STRAT_SHADOW_DIR)


def test_observer_on_off_identical_trading_and_records_every_candidate(tmp_path, monkeypatch):
    e0, off = run_fixture(tmp_path / 'off', monkeypatch, False)
    e1, on = run_fixture(tmp_path / 'on', monkeypatch, True)
    assert off['calls'] and any('max positions' in r for _, _, r in off['missed'])     # the fixture trades AND skips
    assert on == off
    assert not os.path.exists(shadow_dir(e0)) and 'strategy_shadow' not in e0.health
    assert e1.health['strategy_shadow']['errors'] == 0
    b = e1._shadow_book()
    eps = b.snapshot()['episodes'].values()
    assert {(x['symbol'], x['live_decision']['status']) for x in eps} >= {('BTCUSDT', 'taken'), ('ETHUSDT', 'skipped')}
    assert all(x['entry_ref_time'] == FIXED.isoformat() for x in eps if x['status'] != 'incomplete')
    assert b.snapshot()['coverage']['records'] >= 3


@pytest.mark.parametrize('mode', ['boom', 'disk'])
def test_observer_failure_never_changes_trading(tmp_path, monkeypatch, mode):
    _, off = run_fixture(tmp_path / 'off', monkeypatch, False)
    e2, bad = run_fixture(tmp_path / mode, monkeypatch, True, mode)
    assert bad == off
    assert e2.health['strategy_shadow']['errors'] >= 2


def test_observer_is_paper_only_and_off_by_default(tmp_path):
    assert E.GLOBAL_DEFAULTS['STRATEGY_SHADOW'] is False
    e, _ = mk_engine(str(tmp_path))
    e.S['STRATEGY_SHADOW'] = True
    e.state['cohort'] = dict(id='CH')
    e.live = True
    assert e._shadow_book() is None


# ------------------------------------------------------------------ review round 1 regressions (one per finding)
def test_r1_same_instance_same_symbol_while_open_is_held_not_stacked(tmp_path):
    b = book(tmp_path); a = enter(b, slot(), t=0); c = enter(b, slot(), t=5)
    assert ep(b, a)['status'] == 'open' and ep(b, c)['status'] == 'held' and ep(b, c).get('qty') is None
    row = b.compute()['strategies'][0]
    assert row['held'] == 1 and row['open'] == 1 and row['complete']


def test_r1_nan_values_never_poison_a_record(tmp_path):
    b = book(tmp_path); r = enter(b, slot(tp_r=1.0))
    bar(b, 10, o=100, h=103, l=99, c=99.5, atr=float('nan'))       # NaN ATR (short history) - the bar still lands
    assert ep(b, r)['exit_reason'] == 'tp'
    x = b.on_signal(slot=slot('donchian_ens', 'D'), symbol='ETHUSDT', side='LONG',
                    signal=dict(time=iso(), close=50, atr=float('nan')), mark=dict(time=iso(), price=50),
                    live_decision=dict(status='skipped', reason='x'))
    assert ep(b, x)['incomplete'] == ['missing_signal_atr']
    assert SSH.ShadowBook(tmp_path / 's.jsonl', 'C1', dict(SSH.MODEL_V1)).snapshot()['coverage']['complete']


def test_r1_unresolved_write_ahead_candidate_is_incomplete_after_its_window(tmp_path):
    b = book(tmp_path)
    b.on_candidate(slot=slot(), symbol='BTCUSDT', side='LONG', signal=dict(time=iso(), close=100, atr=2),
                   live_decision=dict(status='skipped', reason='x'))
    again = book(tmp_path)                                          # restart: the in-memory queue is gone
    assert again.snapshot()['coverage']['pending'] == 1
    assert again.compute()['strategies'][0]['complete']             # still inside the mark window
    bar(again, 10, o=100, h=101, l=99, c=100)                       # the clock passes the window
    row = again.compute()['strategies'][0]
    assert not row['complete'] and any(w.startswith('candidate_unresolved:BTCUSDT') for w in row['incomplete'])


def test_r1_candidate_resolved_by_signal_is_not_unresolved(tmp_path):
    b = book(tmp_path)
    b.on_candidate(slot=slot(), symbol='BTCUSDT', side='LONG', signal=dict(time=iso(), close=100, atr=2),
                   live_decision=dict(status='skipped', reason='x'))
    enter(b, slot())
    bar(b, 10, o=100, h=100.5, l=99.5, c=100)
    assert b.snapshot()['coverage']['pending'] == 0 and b.compute()['strategies'][0]['complete']


def test_r1_mark_window_is_capped_at_half_a_short_candle():
    assert SSH.ref_lag_s('4h') == SSH.MAX_REF_LAG_S == 300 and SSH.ref_lag_s('5m') == 150 and SSH.ref_lag_s('1m') == 30


def test_r1_bar_recorded_before_a_late_entry_is_incomplete(tmp_path):
    b = book(tmp_path)
    bar(b, 1, o=100, h=101, l=99, c=100)                            # the next candle landed before the late signal
    r = b.on_signal(slot=slot(), symbol='BTCUSDT', side='LONG', signal=dict(time=iso(0), close=100, atr=2),
                    mark=dict(time=iso(0.5), price=100), live_decision=dict(status='skipped', reason='x'))
    assert ep(b, r)['incomplete'] == ['bar_recorded_before_entry']


def close_win20(b, sl, n, sym='BTCUSDT'):
    t = 20 * n
    b.on_signal(slot=sl, symbol=sym, side='LONG', signal=dict(time=iso(t), close=100, atr=2),
                mark=dict(time=iso(t), price=100), live_decision=dict(status='skipped', reason='x'))
    b.on_bar(tf='4h', bars={sym: dict(time=iso(t + 10), o=100, h=104, l=99, c=103)}, signals={})


def test_r1_retired_instance_does_not_spoil_the_others(tmp_path):
    b = book(tmp_path)
    old, keep = slot('ema_mom', 'A'), slot('donchian_ens', 'D', tp_r=1.0)
    for k in range(20): close_win20(b, keep, k)
    enter(b, old, sym='ETHUSDT', t=400)
    ids = sorted(b.snapshot()['instances'])
    oid = SSH.identity(old)[0]
    b.complete_cycle(cycle_id='4h|a', tf='4h', decision_time=iso(500), instance_ids=ids)
    b.complete_cycle(cycle_id='4h|b', tf='4h', decision_time=iso(510), instance_ids=[i for i in ids if i != oid])
    kept = next(r for r in b.compute()['strategies'] if r['key'] == 'donchian_ens')
    gone = next(r for r in b.compute()['strategies'] if r['instance_id'] == oid)
    assert kept['complete'] and kept['rankable'] and not gone['complete']     # gone had an open episode when it vanished
    assert not b.snapshot()['coverage']['complete']


def test_r1_returning_instance_gets_its_own_gap(tmp_path):
    b = book(tmp_path); a, d = slot('ema_mom', 'A'), slot('donchian_ens', 'D')
    enter(b, a, sym='ETHUSDT'); enter(b, d)
    bar(b, 10, o=100, h=100, l=100, c=100, sym='ETHUSDT'); bar(b, 10, o=100, h=100, l=100, c=100)
    ia, idd = SSH.identity(a)[0], SSH.identity(d)[0]
    b.complete_cycle(cycle_id='1', tf='4h', decision_time=iso(1), instance_ids=[ia, idd])
    b.complete_cycle(cycle_id='2', tf='4h', decision_time=iso(2), instance_ids=[idd])
    b.complete_cycle(cycle_id='3', tf='4h', decision_time=iso(3), instance_ids=[ia, idd])
    rows = {r['instance_id']: r for r in b.compute()['strategies']}
    assert any('absent' in w for w in rows[ia]['incomplete']) and not any('absent' in w for w in rows[idd]['incomplete'])


def test_r1_missing_bar_for_an_open_episode_is_visible(tmp_path):
    b = book(tmp_path); r = enter(b, slot())
    bar(b, 10, o=50, h=51, l=49, c=50, sym='ETHUSDT')               # BTC candle fetch failed this cycle
    assert ep(b, r)['incomplete'] == ['bar_missing:BTCUSDT@' + iso(10)]


def test_r1_conflicting_signal_replay_is_a_gap(tmp_path):
    b = book(tmp_path); enter(b, slot())
    b.on_signal(slot=slot(sid='B'), symbol='BTCUSDT', side='LONG', signal=dict(time=iso(), close=100, atr=2.0, le=True,
                se=False, lx=False, sx=False), mark=dict(time=iso(), price=101.0, source='premiumIndex'),
                live_decision=dict(status='skipped', reason='x'))
    assert not b.compute()['strategies'][0]['complete']
    assert any('conflicting_signal_replay' in g for g in b.snapshot()['coverage']['gaps'])


def test_r1_tp1_partial_is_floored_to_the_exchange_step(tmp_path):
    b = book(tmp_path)
    r = b.on_signal(slot=slot(tp1_r=1.0, tp1_frac=0.5), symbol='BTCUSDT', side='LONG',
                    signal=dict(time=iso(), close=100, atr=2), mark=dict(time=iso(), price=100),
                    live_decision=dict(status='skipped', reason='x'), rule=dict(step=7.0, min_qty=0.0, min_notional=0.0))
    assert ep(b, r)['qty'] == 14.0                                  # ~20 risk-sized -> floored to the 7 step
    bar(b, 10, o=100, h=102.5, l=99.5, c=102)
    assert ep(b, r)['qty'] == 7.0 and ep(b, r)['tp1']


def test_r1_tp1_part_that_floors_to_zero_is_retried_not_taken(tmp_path):
    b = book(tmp_path)
    r = b.on_signal(slot=slot(tp1_r=1.0, tp1_frac=0.1), symbol='BTCUSDT', side='LONG',
                    signal=dict(time=iso(), close=100, atr=2), mark=dict(time=iso(), price=100),
                    live_decision=dict(status='skipped', reason='x'), rule=dict(step=7.0, min_qty=0.0, min_notional=0.0))
    bar(b, 10, o=100, h=102.5, l=99.5, c=102)
    assert ep(b, r)['qty'] == 14.0 and not ep(b, r)['tp1'] and ep(b, r)['status'] == 'open'


def test_r1_entry_filters_are_part_of_the_identity():
    a = slot()
    assert SSH.identity(a)[0] != SSH.identity(dict(a, hours=[13, 14]))[0]
    assert SSH.identity(a)[0] != SSH.identity(dict(a, vol_max_pct=0.9))[0]


def test_r1_one_bad_queue_item_never_blocks_the_rest(tmp_path, monkeypatch):
    e, _ = mk_engine(str(tmp_path))
    e.S['STRATEGY_SHADOW'] = True
    e.state['cohort'] = dict(id='CH-Q')
    sb = e._shadow_book()
    real = SSH.ShadowBook.on_signal

    def picky(self, **kw):
        if kw['symbol'] == 'BAD': raise ValueError('poison')
        return real(self, **kw)
    monkeypatch.setattr(SSH.ShadowBook, 'on_signal', picky)
    monkeypatch.setattr(E.time, 'time', lambda: 1.9e9 + 10)
    e.marks, e.marks_t = {'BAD': 1.0, 'BTCUSDT': 100.0}, 1.9e9 + 5
    t0 = datetime.fromtimestamp(1.9e9, timezone.utc).isoformat()
    mk = lambda s: dict(slot=slot(), symbol=s, side='LONG', t_close=1.9e9, tf='4h', fails=0, rule=None,
                        signal=dict(time=t0, close=100, atr=2), live_decision=dict(status='skipped', reason='x'))
    e._shadow_q = [mk('BAD'), mk('BTCUSDT')]
    e._shadow_flush()
    assert [x['symbol'] for x in e._shadow_q] == ['BAD'] and e._shadow_q[0]['fails'] == 1
    e._shadow_flush(); e._shadow_flush()
    assert e._shadow_q == [] and e.health['strategy_shadow']['dropped'] == 1
    assert len(sb.snapshot()['episodes']) == 1


def test_r1_observer_failure_withholds_the_cycle_watermark(tmp_path, monkeypatch):
    e, _ = run_fixture(tmp_path / 'on', monkeypatch, True)
    n = e.health['strategy_shadow']['cycles']
    assert n == len(SCRIPT)
    e._shadow_failed = True
    e._shadow_cycle('4h', {'BTCUSDT': frame(100.0)})
    assert e.health['strategy_shadow']['cycles'] == n and not getattr(e, '_shadow_failed', False)


def test_r1_app_flushes_queued_candidates_while_flat():
    src = open(os.path.join(os.path.dirname(E.__file__), 'app.py'), encoding='utf-8').read()
    seg = src.split('else:\n                                e.marks, e.marks_t = marks, now', 1)[1][:200]
    assert 'e._shadow_flush()' in seg


# ------------------------------------------------------------------ testnet funding timeline (shadow_funding.py) + engine feed
import market_data as MD                                   # noqa: E402
import shadow_funding as SF                                # noqa: E402


class FakeTestnet:
    """/fapi/v1/fundingRate pages; records every request so the source and the symbols are checkable."""
    def __init__(self, rows, info=()):
        self.rows, self.calls, self.info = rows, [], info

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        if path == '/fapi/v1/fundingInfo': return list(self.info)
        if path != '/fapi/v1/fundingRate': raise MD.Refused(400, 'unexpected')
        return [r for r in self.rows.get(params['symbol'], []) if r['fundingTime'] >= params['startTime']][:params['limit']]


def _eng(tmp_path, on=True, live=False):
    e, _ = mk_engine(str(tmp_path))
    e.S['STRATEGY_SHADOW'] = on
    e.S['UNIVERSE'] = ['BTCUSDT']
    e.live = live
    e.state['cohort'] = dict(id='CH-F', started_utc='2026-10-18T00:00:00+00:00')
    return e


def _fr(t_iso, rate, mark='100.0'):
    t = int(datetime.fromisoformat(t_iso).timestamp() * 1000)
    return dict(symbol='BTCUSDT', fundingTime=t, fundingRate=rate, markPrice=mark)


def test_funding_collector_reads_testnet_only_from_the_cohort_start(tmp_path):
    e = _eng(tmp_path)
    tr = FakeTestnet({'BTCUSDT': [_fr('2026-10-17T16:00:00+00:00', '0.0002'), _fr('2026-10-18T08:00:00+00:00', '0.0001')]})
    sf = SF.ShadowFunding(lambda: e, str(tmp_path), transport_factory=lambda: tr)
    sf.FIRST_DELAY_S = 0
    out = sf.run_once()
    assert out is not None, sf.status()
    assert out['rows_added'] == 1 and out['errors'] == 0
    assert {p for p, _ in tr.calls} == {'/fapi/v1/fundingRate', '/fapi/v1/fundingInfo'}
    assert [c for c in tr.calls if c[0] == '/fapi/v1/fundingRate'][0][1]['startTime'] == int(datetime(2026, 10, 18, tzinfo=timezone.utc).timestamp() * 1000)
    rows = SF.read_funding(str(tmp_path), 'BTCUSDT')
    assert [(r[1], r[2]) for r in rows] == [('0.0001', '100.0')]
    assert MD.read_json(os.path.join(SF.out_dir(str(tmp_path)), 'manifest.json'))['series']['funding_rate/BTCUSDT']['source'] == MD.TESTNET


@pytest.mark.parametrize('on,live', [(False, False), (True, True)])
def test_funding_collector_is_off_unless_paper_and_shadow_on(tmp_path, on, live):
    e = _eng(tmp_path, on=on, live=live)
    tr = FakeTestnet({})
    sf = SF.ShadowFunding(lambda: e, str(tmp_path), transport_factory=lambda: tr)
    assert sf.run_once() is None and tr.calls == [] and sf.status()['skipped'] == 1


def test_funding_collector_default_transport_is_testnet():
    sf = SF.ShadowFunding(lambda: None, tempfile.mkdtemp())
    t = sf.transport_factory()
    assert getattr(t, 'base', None) == MD.TESTNET or MD.TESTNET in repr(vars(t))


def test_engine_feeds_collected_funding_once_and_settles_the_exposed_episode(tmp_path, monkeypatch):
    e = _eng(tmp_path)
    sb = e._shadow_book()
    sb.on_signal(slot=slot(), symbol='BTCUSDT', side='LONG', signal=dict(time='2026-10-18T04:00:00+00:00', close=100, atr=2),
                 mark=dict(time='2026-10-18T04:00:05+00:00', price=100.0), live_decision=dict(status='skipped', reason='x'))
    tr = FakeTestnet({'BTCUSDT': [_fr('2026-10-18T08:00:00+00:00', '0.001')]})
    SF.ShadowFunding(lambda: e, e.dir, transport_factory=lambda: tr).run_once()
    fr = frame(100.0, t0='2026-09-15')                              # last candle opens 2026-10-18 08:00 -> closes 12:00
    fr = fr[fr.t <= pd.Timestamp('2026-10-18 08:00:00')]
    e._shadow_bar('4h', {'BTCUSDT': fr}, {})
    e._shadow_bar('4h', {'BTCUSDT': fr}, {})                        # second feed: cursor -> nothing new
    x = next(iter(sb.snapshot()['episodes'].values()))
    assert x['funding'] == pytest.approx(-x['q0'] * 100.0 * 0.001)
    funding_recs = [l for l in open(sb.path, encoding='utf-8') if '"kind":"funding"' in l]
    assert len(funding_recs) == 1
    row = sb.compute()['strategies'][0]
    assert not any(w.startswith('funding_unread') for w in row['incomplete'])
    e.__dict__.pop('_shadow_fcur')                                  # restart: cursor lost, exact replay appends nothing
    e._shadow_bar('4h', {'BTCUSDT': fr}, {})
    assert len([l for l in open(sb.path, encoding='utf-8') if '"kind":"funding"' in l]) == 1
    assert e.health['strategy_shadow']['errors'] == 0


def test_engine_funding_feed_failure_never_blocks_the_bar(tmp_path, monkeypatch):
    e = _eng(tmp_path)
    sb = e._shadow_book()
    SF.ShadowFunding(lambda: e, e.dir, transport_factory=lambda: FakeTestnet(
        {'BTCUSDT': [_fr('2026-10-18T08:00:00+00:00', '0.001')]})).run_once()
    def boom(*a, **k): raise OSError('unreadable')
    monkeypatch.setattr(SF, 'read_funding', boom)
    sb.on_signal(slot=slot(), symbol='BTCUSDT', side='LONG', signal=dict(time='2026-10-18T04:00:00+00:00', close=100, atr=2),
                 mark=dict(time='2026-10-18T04:00:05+00:00', price=100.0), live_decision=dict(status='skipped', reason='x'))
    fr = frame(100.0, t0='2026-09-15')
    fr = fr[fr.t <= pd.Timestamp('2026-10-18 04:00:00')]
    e._shadow_bar('4h', {'BTCUSDT': fr}, {})
    assert e.health['strategy_shadow']['errors'] == 1 and e.health['strategy_shadow']['bars'] == 1


# ------------------------------------------------------------------ funding review regressions
def test_f2_funding_time_a_few_ms_after_the_hour_still_covers_the_grid(tmp_path):
    b = book(tmp_path)
    b.on_cadence(symbol='BTCUSDT', interval_s=8 * 3600, source='test')
    enter(b, slot())
    bar(b, 8 * 60, o=100, h=100.5, l=99.5, c=100.1)
    b.on_funding(symbol='BTCUSDT', at=int(datetime(2026, 10, 18, 8, tzinfo=timezone.utc).timestamp() * 1000) + 2,
                 rate=0.0001, mark=100.0)
    row = b.compute()['strategies'][0]
    assert row['complete'] and row['funding'] < 0


def _fund(b, h, rate=0.0001):
    b.on_funding(symbol='BTCUSDT', at=datetime(2026, 10, 18, h, tzinfo=timezone.utc).isoformat(), rate=rate, mark=100.0)


def test_f2_four_hour_cadence_symbol_has_every_instant_checked(tmp_path):
    b = book(tmp_path)
    b.on_cadence(symbol='BTCUSDT', interval_s=4 * 3600, source='test')
    enter(b, slot())                                              # 01:00, held past 04:00 and 08:00
    bar(b, 8 * 60, o=100, h=100.5, l=99.5, c=100.1)
    _fund(b, 8)
    row = b.compute()['strategies'][0]
    assert row['incomplete'] == ['funding_unread:BTCUSDT@2026-10-18T04:00:00+00:00']
    _fund(b, 4)
    assert b.compute()['strategies'][0]['complete']


def test_f2_cowork_f1_records_alone_never_prove_their_own_cadence(tmp_path):
    """4h symbol exposed 01:00-17:00 with only the 08:00 and 16:00 records: 04:00 and 12:00 must be unread."""
    b = book(tmp_path)
    b.on_cadence(symbol='BTCUSDT', interval_s=4 * 3600, source='test')
    enter(b, slot())
    for h in (8, 16): _fund(b, h)
    bar(b, 16 * 60, o=100, h=100.5, l=99.5, c=100.1)              # 17:00
    row = b.compute()['strategies'][0]
    assert sorted(row['incomplete']) == ['funding_unread:BTCUSDT@2026-10-18T04:00:00+00:00',
                                         'funding_unread:BTCUSDT@2026-10-18T12:00:00+00:00']


def test_f2_unknown_schedule_is_incomplete_once_exposed_at_a_whole_hour(tmp_path):
    b = book(tmp_path)
    enter(b, slot())                                              # 01:00
    bar(b, 30, o=100, h=100.5, l=99.5, c=100.1)                   # 01:30: not yet exposed at a whole hour
    assert b.compute()['strategies'][0]['complete']
    bar(b, 70, o=100, h=100.5, l=99.5, c=100.1)                   # 02:10: held at 02:00, schedule never received
    assert b.compute()['strategies'][0]['incomplete'] == ['funding_cadence_unknown:BTCUSDT']
    b.on_cadence(symbol='BTCUSDT', interval_s=8 * 3600, source='test')
    assert b.compute()['strategies'][0]['complete']


def test_f2_records_closer_than_the_schedule_tighten_the_grid(tmp_path):
    b = book(tmp_path)
    b.on_cadence(symbol='BTCUSDT', interval_s=8 * 3600, source='test')
    for h in (0, 4):                                              # yesterday's records are 4h apart: tighten, never widen
        b.on_funding(symbol='BTCUSDT', at=datetime(2026, 10, 17, h, tzinfo=timezone.utc).isoformat(), rate=0.0001, mark=100.0)
    enter(b, slot())
    bar(b, 8 * 60, o=100, h=100.5, l=99.5, c=100.1)
    _fund(b, 8)
    assert b.compute()['strategies'][0]['incomplete'] == ['funding_unread:BTCUSDT@2026-10-18T04:00:00+00:00']


def test_f2_conflicting_funding_is_recorded_once_across_restarts(tmp_path):
    b = book(tmp_path)
    b.on_funding(symbol='BTCUSDT', at=iso(), rate=0.001, mark=100.0)
    b.on_funding(symbol='BTCUSDT', at=iso(), rate=0.002, mark=100.0)
    n = sum(1 for _ in open(b.path, encoding='utf-8'))
    again = book(tmp_path)
    again.on_funding(symbol='BTCUSDT', at=iso(), rate=0.002, mark=100.0)
    assert sum(1 for _ in open(b.path, encoding='utf-8')) == n
    assert not again.snapshot()['coverage']['complete']


def test_f2_feed_skips_unchanged_files(tmp_path, monkeypatch):
    e = _eng(tmp_path)
    sb = e._shadow_book()
    sb.on_signal(slot=slot(), symbol='BTCUSDT', side='LONG', signal=dict(time='2026-10-18T04:00:00+00:00', close=100, atr=2),
                 mark=dict(time='2026-10-18T04:00:05+00:00', price=100.0), live_decision=dict(status='skipped', reason='x'))
    SF.ShadowFunding(lambda: e, e.dir, transport_factory=lambda: FakeTestnet(
        {'BTCUSDT': [_fr('2026-10-18T08:00:00+00:00', '0.001')]})).run_once()
    reads = []
    real = SF.read_funding
    monkeypatch.setattr(SF, 'read_funding', lambda *a, **k: (reads.append(a), real(*a, **k))[1])
    e._shadow_funding_feed(sb); e._shadow_funding_feed(sb)
    assert len(reads) == 1


def test_f2_collector_survives_a_locked_csv(tmp_path, monkeypatch):
    e = _eng(tmp_path)
    e.S['UNIVERSE'] = ['BTCUSDT', 'ETHUSDT']
    tr = FakeTestnet({'BTCUSDT': [_fr('2026-10-18T08:00:00+00:00', '0.001')],
                      'ETHUSDT': [dict(_fr('2026-10-18T08:00:00+00:00', '0.001'), symbol='ETHUSDT')]})
    real = MD.Collector.collect_forward
    def flaky(self, ds, sym, per, start):
        if sym == 'BTCUSDT': raise PermissionError('file in use')
        return real(self, ds, sym, per, start)
    monkeypatch.setattr(MD.Collector, 'collect_forward', flaky)
    out = SF.ShadowFunding(lambda: e, str(tmp_path), transport_factory=lambda: tr).run_once()
    assert out == dict(rows_added=1, errors=1, symbols=2)


def test_f2_cadence_comes_from_testnet_funding_info(tmp_path):
    e = _eng(tmp_path)
    e.S['UNIVERSE'] = ['BTCUSDT', 'ETHUSDT']
    assert SF.cadence_s(e.dir, 'BTCUSDT') is None                 # nothing received yet
    tr = FakeTestnet({}, info=[dict(symbol='ETHUSDT', fundingIntervalHours=4, adjustedFundingRateCap='0.02',
                                    adjustedFundingRateFloor='-0.02')])
    SF.ShadowFunding(lambda: e, e.dir, transport_factory=lambda: tr).run_once()
    assert SF.cadence_s(e.dir, 'ETHUSDT') == 4 * 3600 and SF.cadence_s(e.dir, 'BTCUSDT') == 8 * 3600


def test_f2_engine_feeds_the_schedule_and_retries_an_incomplete_feed(tmp_path, monkeypatch):
    e = _eng(tmp_path)
    sb = e._shadow_book()
    sb.on_signal(slot=slot(), symbol='BTCUSDT', side='LONG', signal=dict(time='2026-10-18T04:00:00+00:00', close=100, atr=2),
                 mark=dict(time='2026-10-18T04:00:05+00:00', price=100.0), live_decision=dict(status='skipped', reason='x'))
    SF.ShadowFunding(lambda: e, e.dir, transport_factory=lambda: FakeTestnet(
        {'BTCUSDT': [_fr('2026-10-18T08:00:00+00:00', '0.001')]})).run_once()
    real = SSH.ShadowBook.on_funding
    monkeypatch.setattr(SSH.ShadowBook, 'on_funding', lambda *a, **k: (_ for _ in ()).throw(OSError('disk')))
    e._shadow_funding_feed(sb)
    assert sb.cadence == {'BTCUSDT': 8 * 3600} and sb.funding_seen == {}
    monkeypatch.setattr(SSH.ShadowBook, 'on_funding', real)
    e._shadow_funding_feed(sb)                                    # same file mtime: still retried (Cowork F2)
    assert len(sb.funding_seen) == 1


def test_n1_rejected_cadence_is_recorded_once_not_on_every_flush(tmp_path):
    b = book(tmp_path)
    for _ in range(5): b.on_cadence(symbol='BTCUSDT', interval_s=1800, source='test')
    lines = [l for l in open(b.path, encoding='utf-8') if '"cadence"' in l]
    assert len(lines) == 1 and b.snapshot()['coverage']['gaps'].count('bad_cadence:BTCUSDT') == 1
    b2 = book(tmp_path)                                           # restart: the dedupe survives replay
    b2.on_cadence(symbol='BTCUSDT', interval_s=1800, source='test')
    assert sum('"cadence"' in l for l in open(b.path, encoding='utf-8')) == 1


def test_n1_collector_never_reports_a_sub_hour_listing(tmp_path):
    e = _eng(tmp_path)
    e.S['UNIVERSE'] = ['BTCUSDT']
    tr = FakeTestnet({}, info=[dict(symbol='BTCUSDT', fundingIntervalHours=0, adjustedFundingRateCap='0.02',
                                    adjustedFundingRateFloor='-0.02')])
    SF.ShadowFunding(lambda: e, e.dir, transport_factory=lambda: tr).run_once()
    assert SF.cadence_s(e.dir, 'BTCUSDT') is None


def test_n2_cadence_widening_mid_episode_never_turns_it_complete(tmp_path):
    b = book(tmp_path)
    b.on_cadence(symbol='BTCUSDT', interval_s=4 * 3600, source='test')
    enter(b, slot())                                              # 01:00, held to 17:00
    for h in (8, 16): _fund(b, h)
    bar(b, 16 * 60, o=100, h=100.5, l=99.5, c=100.1)
    assert not b.compute()['strategies'][0]['complete']
    b.on_cadence(symbol='BTCUSDT', interval_s=8 * 3600, source='test')   # exchange moves it to 8h
    row = b.compute()['strategies'][0]
    assert sorted(row['incomplete']) == ['funding_unread:BTCUSDT@2026-10-18T04:00:00+00:00',
                                         'funding_unread:BTCUSDT@2026-10-18T12:00:00+00:00']
