"""ML-SHADOW-01 integration hook: the engine decision/close hooks are observation-only, and the app analyst wiring is
PAPER-only, report-only and never echoes the workspace id. Fakes only - no network, no real keys, no real data folder."""
import copy, json, logging, os, sys, tempfile, types
from datetime import datetime, timezone

import numpy as np, pandas as pd, pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['LOCALAPPDATA'] = tempfile.mkdtemp()            # never the real %LOCALAPPDATA%\ZackBot
from test_safety import mk_engine                          # noqa: E402  (FakeX engine, PAPER)
import engine as E                                         # noqa: E402
import ml_shadow as MLS                                    # noqa: E402
import shadow_analyst as SA                                # noqa: E402

FIXED = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
SLA = E.sleeve('A', 'ema_mom', 0.5, 0.02, 1, 'all')        # max 1 position -> the second coin is skipped
WS = 'wrkspc_SECRETWORKSPACE123'
KEY = 'sk-ant-test-' + 'x' * 24


def frame(px, n=260, t0='2026-09-01'):
    t = pd.date_range(t0, periods=n, freq='4h', tz='UTC')
    c = np.linspace(px * 0.9, px, n)
    return pd.DataFrame(dict(t=t, o=c, h=c * 1.01, l=c * 0.99, c=c, v=np.full(n, 1000.0), atr=np.full(n, px * 0.02),
                             e50=c * 0.98, st=np.ones(n)))


def sig(time, le=False, lx=False, close=100.0):
    return dict(close=close, atr=2.0, time=time, le=le, se=False, lx=lx, sx=False)


SCRIPT = [  # (decision candle, {symbol: signal})
    ('2026-10-04 00:00:00', {'BTCUSDT': sig('2026-10-04 00:00:00', le=True), 'ETHUSDT': sig('2026-10-04 00:00:00', le=True, close=50.0)}),
    ('2026-10-04 04:00:00', {'BTCUSDT': sig('2026-10-04 04:00:00', lx=True), 'ETHUSDT': sig('2026-10-04 04:00:00', le=True, close=50.0)}),
    ('2026-10-04 08:00:00', {'ETHUSDT': sig('2026-10-04 08:00:00', lx=True, close=50.0)}),
]


def run_fixture(tmp, monkeypatch, ml_on, boom=False):
    monkeypatch.setattr(E, 'now_utc', lambda: FIXED)
    monkeypatch.setattr(E.time, 'time', lambda: FIXED.timestamp())
    os.makedirs(str(tmp), exist_ok=True)
    e, _ = mk_engine(str(tmp))
    e.S['ML_SHADOW'] = ml_on
    e.S['SLEEVES'] = [SLA]
    orders = []
    for name in ('open', 'close', 'stop', 'cancel'):
        f = getattr(e.trade, name)
        def wrap(*a, _f=f, _n=name):
            orders.append((_n,) + tuple(a)); return _f(*a)
        setattr(e.trade, name, wrap)
    if boom:
        def raise_(*a, **k): raise RuntimeError('ml boom')
        monkeypatch.setattr(MLS, 'build_decision_record', raise_)
        monkeypatch.setattr(MLS, 'build_label_record', raise_)
    frames = {'BTCUSDT': frame(100.0), 'ETHUSDT': frame(50.0)}
    for _t, sigs in SCRIPT:
        e.compute_signals = lambda tf, syms, extra=(), sigs=sigs: ({f'A|{s}': dict(v) for s, v in sigs.items()}, frames)
        e.cycle('4h')
    state = copy.deepcopy(e.state)
    for l in state['lots'].values(): l.pop('ml_decision_id', None)      # the only state the hook may add (ON only)
    for w in (state.get('measured_wait') or {}).values(): w.pop('ml', None)   # ... carried by a close awaiting its measured net
    return e, dict(orders=orders, state=json.dumps(state, sort_keys=True, default=str),
                   lots=json.dumps(state['lots'], sort_keys=True, default=str),
                   history=json.dumps(e.history, sort_keys=True, default=str),
                   missed=[(m['symbol'], m['candle'], m['reason']) for m in e.missed])


def settle(e):
    """FILL-PNL-02: the fake exchange delivers no cost records - mark every closed row measured at its booked prices and
    run the real revision hook, which writes the label a close was waiting for (labels carry the measured net only)."""
    for r in e.history:
        if r.get('fill_px_partial') or r.get('fees_actual') is None:
            r.update(fees_actual=r.get('fees') or 0.0, fees_actual_partial=False, fill_px_partial=False)
            e._row_changed(r)


def ledger_rows(e, kind):
    assert e.ml_flush(5.0)                                  # #104: rows are written by the background writer
    p = e.ml_paths()[kind]
    return [json.loads(x) for x in open(p, encoding='utf-8')] if os.path.exists(p) else []


def test_close_excursions_only_labelled_when_audit_is_fully_observed(tmp_path, monkeypatch):
    e, _ = mk_engine(str(tmp_path))
    e.S['ML_SHADOW'] = True
    e._ml_ledgers = lambda: (object(), object())
    captured = []
    monkeypatch.setattr(MLS, 'build_label_record', lambda *a, **kw: (captured.append(kw), kw)[1])
    lot = {'ml_decision_id': 'd' * 32}
    rec = dict(r=1.0, pnl=5.0, exit_reason='take_profit', closed='2026-10-17T00:00:00+00:00')
    metrics = {'mfe': {'r': 2.0}, 'mae': {'r': -0.5}}
    e._ml_label('partial', rec, lot, dict(coverage='partial', observations=9, target=120, target_touch_t='x', metrics=metrics))
    e._ml_label('targetless', rec, lot, dict(coverage='full', observations=9, target=None, target_touch_t=None, metrics=metrics))
    e._ml_label('full', rec, lot, dict(coverage='full', observations=9, target=120, target_touch_t=None, metrics=metrics))
    assert captured[0]['mfe_r'] is None and captured[0]['mae_r'] is None and captured[0]['target_before_stop'] is None
    assert captured[1]['mfe_r'] == 2.0 and captured[1]['mae_r'] == -0.5 and captured[1]['target_before_stop'] is None
    assert captured[2]['target_before_stop'] is False


def test_ml_shadow_on_vs_off_identical_trading(tmp_path, monkeypatch):
    e0, off = run_fixture(tmp_path / 'off', monkeypatch, False)
    e1, on = run_fixture(tmp_path / 'on', monkeypatch, True)
    assert off['orders'] and any(o[0] == 'close' for o in off['orders'])          # the fixture really trades and closes
    assert any('max positions' in r for _, _, r in off['missed'])                 # ... and really skips
    assert on == off                                                              # orders, state, lots, history, skips
    assert not os.path.exists(e0.ml_paths()['dir'])                               # OFF: nothing written at all
    assert 'ml_shadow' not in e0.health
    dec = ledger_rows(e1, 'decisions')
    assert {(r['symbol'], r['status']) for r in dec} >= {('BTCUSDT', 'taken'), ('ETHUSDT', 'skipped'), ('ETHUSDT', 'taken')}
    assert all(r['schema'] == MLS.SCHEMA_VERSION == 'ml_shadow.v2' for r in dec)
    skip = next(r for r in dec if r['status'] == 'skipped')
    assert 'max positions' in skip['reason'] and skip['family'] == 'Trend' and skip['strategy'] == 'ema_mom'
    assert skip['features']['atr_pct'] is not None                                # features_at ran on the candle frame
    settle(e1)
    labels = ledger_rows(e1, 'labels')
    assert len(labels) == 2 and {l['exit_reason'] for l in labels} == {'exit_signal'}
    assert all('mfe_r' in l and 'mae_r' in l for l in labels)                     # no fabricated values without mark samples
    taken_ids = {r['decision_id'] for r in dec if r['taken']}
    assert {l['decision_id'] for l in labels} <= taken_ids
    assert e1.health['ml_shadow']['errors'] == 0 and e1.health['ml_shadow']['labels'] == 2


def test_ml_hook_exceptions_never_change_trading(tmp_path, monkeypatch):
    _, off = run_fixture(tmp_path / 'off', monkeypatch, False)
    e2, boom = run_fixture(tmp_path / 'boom', monkeypatch, True, boom=True)
    assert boom == off
    assert e2.health['ml_shadow']['errors'] >= 3


def test_ml_hook_disk_failure_never_changes_trading(tmp_path, monkeypatch):
    _, off = run_fixture(tmp_path / 'off', monkeypatch, False)
    def bad_append(self, row): raise OSError('disk full')
    monkeypatch.setattr(MLS.JsonlLedger, 'append', bad_append)
    _, on = run_fixture(tmp_path / 'bad', monkeypatch, True)
    assert on == off


def test_ml_shadow_defaults_off_and_independent_of_ai_filter():
    d = E.GLOBAL_DEFAULTS
    assert d['ML_SHADOW'] is False and d['CLAUDE_SHADOW_ANALYST'] is False and d['AI_FILTER'] is False
    assert d['CLAUDE_SHADOW_MODEL'] == SA.DEFAULT_MODEL


# ------------------------------------------------------------------ app wiring (no HTTP server, no real config file)
@pytest.fixture
def app_env(tmp_path, monkeypatch):
    import app as A
    monkeypatch.setattr(A, 'config_path', lambda: str(tmp_path / 'config.env'))
    monkeypatch.setattr(A, 'protect', lambda v: v)                 # DPAPI is per-user; the path through write_cfg is what matters
    os.makedirs(str(tmp_path / 'data'), exist_ok=True)
    e, _ = mk_engine(str(tmp_path / 'data'))
    cfg = dict(MODE='paper', ANTHROPIC_API_KEY=KEY)
    e.cfg = cfg
    app = types.SimpleNamespace(engine=e, cfg=cfg)
    monkeypatch.setattr(A, 'APP', app, raising=False)
    return A, app, e, tmp_path


class FakeTransport:
    def __init__(self, *responses): self.responses, self.calls = list(responses), []
    def __call__(self, url, headers, body, timeout):
        self.calls.append(dict(url=url, headers=dict(headers)))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def fake_analyst(A, app, transport):
    a = SA.ShadowAnalyst(os.path.join(app.engine.dir, 'analyst'), transport=transport, sleep=lambda s: None)
    a._zb_dir = app.engine.dir
    app._analyst = a
    return a


def test_workspace_id_stored_protected_and_never_echoed(app_env, caplog):
    A, app, e, tmp = app_env
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ValueError): A.handle('/api/analyst/workspace', {'ANTHROPIC_WORKSPACE_ID': 'bad id\n'})
    msg = A.handle('/api/analyst/workspace', {'ANTHROPIC_WORKSPACE_ID': WS})
    assert WS not in msg and 'ANTHROPIC_WORKSPACE_ID' in A.CFG_SECRET
    assert app.cfg['ANTHROPIC_WORKSPACE_ID'] == WS and WS in A.SECRETS              # scrubbed from every log line
    fake_analyst(A, app, FakeTransport((200, json.dumps({'type': 'message', 'content': [], 'request_id': 'req_1'}))))
    view = A.analyst_view(app)
    assert view['workspace_id_set'] is True and view['key_set'] is True
    st = A.handle('/api/analyst/auth_self_test', {})
    assert set(st) == {'status', 'request_id'}
    blob = json.dumps(view) + json.dumps(st) + json.dumps(e.S) + caplog.text
    assert WS not in blob and KEY not in blob
    assert A.handle('/api/analyst/workspace', {'ANTHROPIC_WORKSPACE_ID': ''}) == 'workspace id cleared'
    assert A.analyst_view(app)['workspace_id_set'] is False


def test_analyst_endpoints_no_analysis_and_auth_status(app_env):
    A, app, e, _ = app_env
    t = FakeTransport((401, json.dumps({'type': 'error', 'error': {'type': 'authentication_error', 'message': 'x'}})))
    fake_analyst(A, app, t)
    r = A.handle('/api/analyst/analyze_now', {})
    assert r['status'] == SA.STATUS_NO and r['reason'] == 'disabled' and not t.calls        # OFF by default
    e.S['CLAUDE_SHADOW_ANALYST'] = True
    r = A.handle('/api/analyst/analyze_now', {})
    assert r['status'] == SA.STATUS_NO and r['reason'] == 'insufficient_data' and not t.calls
    st = A.handle('/api/analyst/auth_self_test', {})
    assert st == {'status': SA.AUTH_REJECTED, 'request_id': None}
    v = A.analyst_view(app)
    assert v['latest']['status'] == SA.STATUS_NO and 'day' in v['usage']
    app.cfg.pop('ANTHROPIC_API_KEY')
    assert A.handle('/api/analyst/auth_self_test', {})['status'] == SA.AUTH_UNAVAILABLE


def test_analyst_timer_runs_only_when_enabled(app_env):
    A, app, e, _ = app_env
    t = FakeTransport((500, '{}'))
    fake_analyst(A, app, t)
    assert A.analyst_tick(app, now=datetime(2026, 10, 15, 10, 30, tzinfo=timezone.utc)) is None    # OFF: no-op
    e.S['CLAUDE_SHADOW_ANALYST'] = True
    r = A.analyst_tick(app, now=datetime(2026, 10, 15, 10, 30, tzinfo=timezone.utc))   # 13:30 Cairo: 12:15 slot due
    assert r and r['status'] == SA.STATUS_NO and not t.calls                            # empty ledgers -> no call


def test_analyst_aggregate_counts_only(app_env, monkeypatch):
    A, app, e, tmp = app_env
    e.S['ML_SHADOW'] = True
    dec, lab = e._ml_ledgers()
    for k in range(12):
        tm = f'2026-10-0{1 + k % 9} 0{k % 6}:00:00'
        MLS.record_decision(dec, sleeve='A', strategy='ema_mom', family='Trend', symbol='BTCUSDT', side='LONG', tf='4h',
                            candle_time=tm, taken=True, features={'features': {'regime': 'RANGE'}}, trade_id=f'A|BTCUSDT|LONG|{k}')
        did = MLS.decision_id('A', 'ema_mom', 'BTCUSDT', 'LONG', '4h', tm)
        MLS.record_label(lab, dec, decision_id=did, net_r=(1.5 if k % 2 else -1.0), net_pnl=1.0, exit_reason='stop',
                         mfe_r=(2.0 if k % 2 else 0.8), mae_r=-0.4, trade_id=f'A|BTCUSDT|LONG|{k}')
    raw = A.ml_aggregate(e)
    agg = SA.build_aggregate(raw)
    assert agg['n_trades'] == raw['n_trades'] >= 10 and agg['performance']['all']['n'] == raw['n_trades']
    assert agg['mfe_mae_coverage'] == {'mfe': 1.0, 'mae': 1.0}
    assert agg['outcome_diagnostics']['profitable_then_lost'] == 6
    assert agg['regime_performance'] == {'RANGE.n': 12, 'RANGE.net_r': 3.0, 'RANGE.win_rate': 0.5,
                                          'RANGE.avg_r': 0.25, 'RANGE.pf': 1.5}
    assert agg['source_coverage']['point_in_time_news'] == 0.0
    assert all(agg['source_coverage'][k] == 0.0 for k in ('qv_ratio', 'funding', 'rank', 'exposure_count', 'corr_count'))
    assert agg['portfolio_pressure'] == {}
    blob = json.dumps(agg)
    assert 'A|BTCUSDT' not in blob and 'decision_id' not in blob


def test_completed_backtest_is_compact_analyst_evidence_without_trade_ledger(app_env, monkeypatch):
    A, app, e, tmp = app_env
    data = type(tmp)(e.dir); (data / 'backtests').mkdir(parents=True)
    result = {
        'name': 'SCALP holdout', 'stats': {'trades': 321, 'ret': 18.2, 'pf': 1.31, 'maxdd': -7.4, 'win': 54.0},
        'oos': {'ret_last': 4.3, 'dd_last': -3.1},
        'feasibility': {'execution_realistic': True, 'promotable': False},
    }
    (data / 'backtests' / 'safe.json').write_text(json.dumps(result), encoding='utf-8')
    raw = A.ml_aggregate(e)
    assert raw['n_trades'] == 321 and raw['notes'] == []
    b = SA.build_aggregate(raw)['backtests'][0]                      # F5: structured, nothing cut at a string cap
    assert b == dict(name='SCALP holdout', trades=321, ret=18.2, pf=1.31, maxdd=-7.4, win=54.0, oos_ret=4.3, oos_dd=-3.1,
                     realistic=True, promotable=False)
    assert 'request' not in json.dumps(raw)


def test_ai_filter_unchanged_by_analyst_settings(app_env):
    A, app, e, _ = app_env
    before = e.S['AI_FILTER']
    ns, _ = A.settings_update(e, {'CLAUDE_SHADOW_ANALYST': True, 'CLAUDE_SHADOW_MODEL': 'claude-sonnet-5-5', 'ML_SHADOW': True})
    assert ns['AI_FILTER'] == before and ns['CLAUDE_SHADOW_ANALYST'] is True and ns['ML_SHADOW'] is True
    with pytest.raises(ValueError): A.settings_update(e, {'ML_SHADOW': 'yes'})
    with pytest.raises(ValueError): A.settings_update(e, {'CLAUDE_SHADOW_MODEL': 'bad model!'})
    E.validate_settings(dict(schema_version=E.SETTINGS_SCHEMA, ML_SHADOW=True, CLAUDE_SHADOW_ANALYST=False))
    with pytest.raises(E.SchemaError): E.validate_settings(dict(schema_version=E.SETTINGS_SCHEMA, ML_SHADOW='on'))


def test_live_mode_refuses_analyst(app_env):
    A, app, e, _ = app_env
    t = FakeTransport((200, '{}'))
    fake_analyst(A, app, t)
    e.live = True
    e.S['CLAUDE_SHADOW_ANALYST'] = True
    with pytest.raises(ValueError): A.settings_update(e, {'CLAUDE_SHADOW_ANALYST': True})
    r = A.handle('/api/analyst/analyze_now', {})
    assert r['status'] == SA.STATUS_NO and r['reason'] == 'live_refused'
    with pytest.raises(ValueError): A.handle('/api/analyst/auth_self_test', {})
    assert A.analyst_view(app)['mode'] == 'LIVE' and not t.calls


# ------------------------------------------------------------------ pending decisions (resting maker / armed trailing)
from test_v31_engine import Book                           # noqa: E402  (fake order book for maker entries)
from ml_shadow.trainer import join                        # noqa: E402


def one_cycle(e, sigs, frames=None):
    e.compute_signals = lambda tf, syms, extra=(): ({f'A|{s}': dict(v) for s, v in sigs.items()}, frames or {})
    e.cycle('4h')


def maker_setup(tmp):
    os.makedirs(str(tmp), exist_ok=True)
    e, _ = mk_engine(str(tmp), ENTRY_ORDER='maker', MAKER_REPRICE=2, MAKER_WAIT_S=30)
    e.S['ML_SHADOW'] = True; e.S['SLEEVES'] = [SLA]
    bk = Book(e.trade)
    one_cycle(e, {'BTCUSDT': sig('2026-10-04 00:00:00', le=True)}, {'BTCUSDT': frame(100.0)})
    assert e.state['resting_entries'] and not e.state['lots']
    bk.fill(); e.manage(e.trade.marks())
    (k, lot), = e.state['lots'].items()
    return e, k, lot


def test_maker_rests_fills_closes_one_pending_decision_one_label(tmp_path):
    e, k, lot = maker_setup(tmp_path)
    dec = ledger_rows(e, 'decisions')
    assert len(dec) == 1 and dec[0]['status'] == 'pending' and dec[0]['taken'] is False
    assert lot['ml_decision_id'] == dec[0]['decision_id']
    one_cycle(e, {'BTCUSDT': sig('2026-10-04 04:00:00', lx=True)}, {'BTCUSDT': frame(100.0)})
    assert not e.state['lots']
    assert len(ledger_rows(e, 'decisions')) == 1                       # no second row on fill
    settle(e)
    labels = ledger_rows(e, 'labels')
    assert len(labels) == 1 and labels[0]['decision_id'] == dec[0]['decision_id']
    assert len(join(ledger_rows(e, 'decisions'), labels)) == 1          # trainer accepts a filled pending decision


def test_restart_between_fill_and_close_still_labels(tmp_path):
    e, k, lot = maker_setup(tmp_path)
    e.save_state()
    pos, stops = dict(e.trade.pos), dict(e.trade.stops)
    assert e.ml_close(10)                                              # #104: the old process (and its ML writer) is gone
    e2, _ = mk_engine(str(tmp_path))                                   # new process: same data folder, fresh ledgers
    e2.trade.pos.update(pos); e2.trade.stops.update(stops)
    e2.S['ML_SHADOW'] = True
    assert e2.state['lots'][k]['ml_decision_id'] == lot['ml_decision_id']
    e2.close_lot(k, 'exit_signal')
    settle(e2)
    labels = ledger_rows(e2, 'labels')
    assert [l['decision_id'] for l in labels] == [lot['ml_decision_id']]


def test_cancelled_pending_gets_no_label_and_trainer_excludes_it(tmp_path):
    os.makedirs(str(tmp_path), exist_ok=True)
    e, _ = mk_engine(str(tmp_path))
    e.S['ML_SHADOW'] = True
    e.S['SLEEVES'] = [dict(SLA, trail_entry={'dev_atr': 0.5, 'max_bars': 2})]
    e.compute_signals = lambda tf, syms, extra=(): ({'A|BTCUSDT': sig('2026-10-04 00:00:00', le=True)}, {'BTCUSDT': frame(100.0)})
    e.cycle('4h')
    assert e.state['pending_entries']
    e.state['pending_entries']['A|BTCUSDT']['until'] = 0
    e.manage(e.trade.marks())
    assert not e.state['pending_entries'] and not e.state['lots']
    dec = ledger_rows(e, 'decisions')
    assert [r['status'] for r in dec] == ['pending']                   # stays pending, nothing appended on expiry
    assert ledger_rows(e, 'labels') == []
    fake = [dict(decision_id=dec[0]['decision_id'], net_r=-1.0)]
    assert join(dec, []) == [] and len(join(dec, fake)) == 1            # only a real (filled) label could ever join it
    assert not MLS.record_label(e._ml_ledgers()[1], e._ml_ledgers()[0], decision_id='0' * 32, net_r=1, net_pnl=1, exit_reason='x')


def test_skipped_decisions_never_labelled(tmp_path):
    led = MLS.JsonlLedger(str(tmp_path / 'd.jsonl')); lab = MLS.JsonlLedger(str(tmp_path / 'l.jsonl'))
    kw = dict(sleeve='A', strategy='ema_mom', family='Trend', symbol='BTCUSDT', side='LONG', tf='4h', features={'features': {}})
    sk = MLS.record_decision(led, candle_time='2026-10-01 00:00:00', taken=False, **kw)
    pe = MLS.record_decision(led, candle_time='2026-10-01 04:00:00', taken=False, status='pending', **kw)
    assert sk['status'] == 'skipped' and pe['status'] == 'pending'
    assert MLS.record_label(lab, led, decision_id=sk['decision_id'], net_r=1, net_pnl=1, exit_reason='x') is None
    assert MLS.record_label(lab, led, decision_id=pe['decision_id'], net_r=1, net_pnl=1, exit_reason='x') is not None
    assert MLS.record_decision(led, candle_time='2026-10-01 08:00:00', taken=False, status='maybe', **kw) is None


# ------------------------------------------------------------------ Cowork F1: vectorised causal BTC cutoff
def _old_mask(col, ti):
    from ml_shadow.common import to_utc
    return np.array([to_utc(x) <= ti for x in col], bool)


def test_btc_cutoff_vectorised_equals_loop_incl_future_rows():
    import time as _t
    from ml_shadow.common import to_utc
    from ml_shadow.features import btc_keep_mask, features_at
    n = 1500
    t = pd.date_range('2026-01-01', periods=n, freq='15min', tz='UTC')
    c = 100 + np.cumsum(np.sin(np.arange(n) / 7.0))
    btc = pd.DataFrame(dict(t=t, o=c, h=c + 1, l=c - 1, c=c, v=np.ones(n)))
    ms = (btc.t - pd.Timestamp('1970-01-01', tz='UTC')) // pd.Timedelta('1ms')
    cols = [btc.t, btc.t.dt.tz_localize(None), btc.t.astype(str), ms, btc.t.dt.tz_convert('Asia/Tokyo'),
            pd.Series(list(btc.t.astype(str)[:10]) + list(ms[10:20]), dtype=object)]     # mixed -> slow path, same answer
    for k in (0, 7, 700, n - 2):                                                      # future rows beyond k exist
        ti = to_utc(btc.t.iloc[k])
        for col in cols:
            assert (btc_keep_mask(col, ti) == _old_mask(col, ti)).all()
        assert btc_keep_mask(btc.t, ti).sum() == k + 1
    df = btc.iloc[:800].copy()
    df['t'] = btc.t.iloc[:800]
    old = features_at(df, 799, {'btc_dir': None, 'btc_df': btc})['features']['btc_dir']
    from ml_shadow.features import _dir
    assert old == _dir(btc.c.to_numpy(float)[:800])                                   # identical to the strictly-causal slice
    t0 = _t.perf_counter()
    for k in range(200): btc_keep_mask(btc.t, to_utc(btc.t.iloc[n - 1 - k]))
    assert _t.perf_counter() - t0 < 0.5                                               # was ~6.5 ms/decision with the loop


# ------------------------------------------------------------------ PR #127 correction: bounded scan, lock scope, dispatch
import pathlib, threading, time                              # noqa: E402


def _bt_file(d, name, i, extra=None, mtime=None):
    r = {'name': f'bt {i}', 'stats': {'trades': 100 + i, 'pf': 1.1, 'maxdd': -5.0}, 'oos': {'ret_last': 1.0, 'dd_last': -2.0},
         'feasibility': {'execution_realistic': True, 'promotable': True}, 'request': {'sleeves': []}}
    r.update(extra or {})
    p = d / name
    p.write_text(json.dumps(r), encoding='utf-8')
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _bt_dir(e):
    d = pathlib.Path(e.dir) / 'backtests'
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_backtest_scan_skips_oversize_before_parsing(app_env, monkeypatch):
    A, app, e, _ = app_env
    d = _bt_dir(e)
    _bt_file(d, 'ok.json', 1, mtime=1000)
    big = _bt_file(d, 'big.json', 2, extra={'pad': 'x' * 5000}, mtime=2000)       # newest, but oversize
    monkeypatch.setattr(A, 'BT_FILE_MAX_BYTES', 2000)
    seen = []
    real = json.loads
    monkeypatch.setattr(A.json, 'loads', lambda s, *a, **k: (seen.append(len(s)), real(s, *a, **k))[1])
    bts = A._recent_backtests(str(d))
    assert [b['name'] for b in bts] == ['bt 1'] and os.path.getsize(big) > 2000
    assert seen and max(seen) <= 2000                                              # the oversize file was never parsed


def test_backtest_scan_bounded_file_count_newest_first(app_env, monkeypatch):
    A, app, e, _ = app_env
    d = _bt_dir(e)
    for i in range(20):
        _bt_file(d, f'r{i:02d}.json', i, extra={'lab': True} if i in (19, 18) else None, mtime=1000 + i)
    (d / 'junk.json').write_text('{not json', encoding='utf-8')
    os.utime(d / 'junk.json', (5000, 5000))
    opened = []
    real_open = open
    monkeypatch.setattr('builtins.open', lambda f, *a, **k: (opened.append(str(f)), real_open(f, *a, **k))[1])
    monkeypatch.setattr(A, 'BT_SCAN_MAX_FILES', 4)
    bts = A._recent_backtests(str(d))
    assert len(opened) == 4                                                       # junk + 2 lab + 1 valid, then stop
    assert [b['name'] for b in bts] == ['bt 17']
    opened.clear()
    monkeypatch.setattr(A, 'BT_SCAN_MAX_FILES', 24)
    bts = A._recent_backtests(str(d))
    assert [b['name'] for b in bts] == [f'bt {i}' for i in range(17, 11, -1)] and len(opened) <= 24
    monkeypatch.setattr(A, 'BT_SCAN_MAX_ENTRIES', 3)
    assert len(A._recent_backtests(str(d))) <= 3
    assert A._recent_backtests(str(d / 'missing')) == []


def test_analyst_lock_not_held_while_scanning(app_env, monkeypatch):
    A, app, e, _ = app_env
    fake_analyst(A, app, FakeTransport((500, '{}')))
    e.S['CLAUDE_SHADOW_ANALYST'] = True
    held = []
    real = A._recent_backtests
    monkeypatch.setattr(A, '_recent_backtests', lambda p: (held.append(A.ANALYST_LOCK.locked()), real(p))[1])
    A.handle('/api/analyst/analyze_now', {})
    A.analyst_tick(app, now=datetime(2026, 10, 15, 10, 30, tzinfo=timezone.utc))
    A.analyst_backtest_review()
    assert held == [False, False, False]


def _drain():
    import app as A
    deadline = time.time() + 5
    while A.ANALYST_REVIEW_Q.unfinished_tasks and time.time() < deadline:
        time.sleep(0.01)
    assert A.ANALYST_REVIEW_Q.unfinished_tasks == 0


def test_slow_analyst_never_blocks_the_research_worker(app_env, monkeypatch):
    A, app, e, _ = app_env
    e.S['CLAUDE_SHADOW_ANALYST'] = True
    gate, started, runs = threading.Event(), threading.Event(), []

    def slow_review():
        runs.append(1)
        started.set()
        gate.wait(5)
    monkeypatch.setattr(A, 'analyst_backtest_review', slow_review)
    _drain()
    t0 = time.monotonic()
    assert A.request_backtest_review() is True                 # first finished backtest
    assert started.wait(2)
    queued = [A.request_backtest_review() for _ in range(5)]   # five more backtests finish while the critique is stuck
    assert time.monotonic() - t0 < 1.0                         # the worker returned at once every time
    assert queued == [True, False, False, False, False]        # one pending review; the rest coalesce into it
    assert A.ANALYST_REVIEW_Q.qsize() <= 1
    gate.set()
    _drain()
    assert len(runs) == 2                                      # in-flight + one coalesced follow-up


def test_failing_analyst_does_not_stop_later_reviews(app_env, monkeypatch):
    A, app, e, _ = app_env
    e.S['CLAUDE_SHADOW_ANALYST'] = True
    runs = []

    def boom():
        runs.append(1)
        raise RuntimeError('api down')
    monkeypatch.setattr(A, 'analyst_backtest_review', boom)
    _drain()
    for _ in range(3):
        assert A.request_backtest_review() is True
        _drain()
    assert len(runs) == 3 and A._ANALYST_REVIEW_THREAD.is_alive()


def test_review_not_queued_when_off_or_live(app_env, monkeypatch):
    A, app, e, _ = app_env
    monkeypatch.setattr(A, 'analyst_backtest_review', lambda: pytest.fail('must not run'))
    _drain()
    assert A.request_backtest_review() is False                # analyst OFF
    e.S['CLAUDE_SHADOW_ANALYST'] = True
    e.live = True
    assert A.request_backtest_review() is False                # LIVE
    assert A.ANALYST_REVIEW_Q.qsize() == 0


def test_backtest_worker_only_queues_the_critique():
    import ast
    import inspect
    import app as A
    src = inspect.getsource(A.run_backtest_job)
    calls = {n.func.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert 'request_backtest_review' in calls and 'analyst_backtest_review' not in calls
    assert src.index("job.update(status='done'") < src.index('request_backtest_review()')


def test_concurrent_analyses_are_serialised(app_env, monkeypatch):
    A, app, e, _ = app_env
    e.S['CLAUDE_SHADOW_ANALYST'] = True
    active, peak, n = [0], [0], [0]
    lock = threading.Lock()
    analysis = {k: [] for k in SA.SCHEMA_KEYS}
    analysis['role_reviews'] = [
        {'role': role, 'stance': 'neutral', 'confidence': 0.5, 'evidence': 'mixed evidence'} for role in SA.ROLE_ORDER]
    body = json.dumps({'type': 'message', 'stop_reason': 'end_turn', 'usage': {'input_tokens': 10, 'output_tokens': 5},
                       'content': [{'type': 'text', 'text': json.dumps(analysis)}]})

    def transport(url, headers, b, timeout):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return 200, body
    a = fake_analyst(A, app, transport)
    assert a.auth_self_test(KEY, None, now_utc=FIXED)['status'] == SA.AUTH_ACCEPTED

    def agg(_e):
        with lock:
            n[0] += 1
            k = n[0]
        return {'n_trades': 20 + k, 'performance': {'all': {'n': 20 + k, 'net_r': 1.0}}, 'counts': {}}
    monkeypatch.setattr(A, 'ml_aggregate', agg)
    out = []
    ths = [threading.Thread(target=lambda: out.append(A.handle('/api/analyst/analyze_now', {}))) for _ in range(6)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(10)
    assert len(out) == 6 and all(r['status'] == SA.STATUS_OK for r in out)
    assert peak[0] == 1                                        # the API call and state update stay serialised
    assert a.usage()['day']['calls'] == 6


def test_backtest_scan_newest_by_mtime_not_name_order(app_env, monkeypatch):
    A, app, e, _ = app_env
    d = _bt_dir(e)
    for i in range(12):                                     # names sort OPPOSITE to age: 'z...' oldest, 'a...' newest
        _bt_file(d, f'{chr(122 - i)}.json', i, mtime=1000 + i)
    monkeypatch.setattr(A, 'BT_SCAN_MAX_FILES', 3)
    assert [b['name'] for b in A._recent_backtests(str(d))] == ['bt 11', 'bt 10', 'bt 9']


def test_analyst_instance_created_once_under_concurrency(app_env, monkeypatch):
    A, app, e, _ = app_env
    app.__dict__.pop('_analyst', None)
    real = A.SA.ShadowAnalyst
    made = []

    class Slow(real):
        def __init__(self, *a, **k):
            made.append(1)
            time.sleep(0.05)
            super().__init__(*a, **k)
    monkeypatch.setattr(A.SA, 'ShadowAnalyst', Slow)
    got = []
    ths = [threading.Thread(target=lambda: got.append(A._analyst(app))) for _ in range(5)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(5)
    assert len(made) == 1 and len({id(x) for x in got}) == 1
