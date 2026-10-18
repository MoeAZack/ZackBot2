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
    bar(b, 10, o=100, h=103, l=97, c=101)                  # target 102.02 AND stop 98.02 inside: stop-first convention
    e = ep(b, r)
    assert e['exit_reason'] == 'stop' and e['exit'] == pytest.approx((E0 - 2) * (1 - SLIP))


def test_red_candle_path_reaches_the_target_first(tmp_path):
    b = book(tmp_path); r = enter(b, slot(tp_r=1.0))
    bar(b, 10, o=100, h=103, l=99, c=99.5)                 # o -> h -> l -> c, stop not in candle
    e = ep(b, r)
    assert e['exit_reason'] == 'tp' and e['exit'] == pytest.approx((E0 + 2) * (1 - SLIP))


def test_gap_through_stop_fills_at_the_open(tmp_path):
    b = book(tmp_path); r = enter(b, slot(tp_r=1.0))
    bar(b, 10, o=97, h=99, l=96, c=98)
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
