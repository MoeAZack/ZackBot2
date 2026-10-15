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
        monkeypatch.setattr(MLS, 'record_decision', raise_)
        monkeypatch.setattr(MLS, 'record_label', raise_)
    frames = {'BTCUSDT': frame(100.0), 'ETHUSDT': frame(50.0)}
    for _t, sigs in SCRIPT:
        e.compute_signals = lambda tf, syms, extra=(), sigs=sigs: ({f'A|{s}': dict(v) for s, v in sigs.items()}, frames)
        e.cycle('4h')
    state = copy.deepcopy(e.state)
    return e, dict(orders=orders, state=json.dumps(state, sort_keys=True, default=str),
                   lots=json.dumps(e.state['lots'], sort_keys=True, default=str),
                   history=json.dumps(e.history, sort_keys=True, default=str),
                   missed=[(m['symbol'], m['candle'], m['reason']) for m in e.missed])


def ledger_rows(e, kind):
    p = e.ml_paths()[kind]
    return [json.loads(x) for x in open(p, encoding='utf-8')] if os.path.exists(p) else []


def test_ml_shadow_on_vs_off_identical_trading(tmp_path, monkeypatch):
    e0, off = run_fixture(tmp_path / 'off', monkeypatch, False)
    e1, on = run_fixture(tmp_path / 'on', monkeypatch, True)
    assert off['orders'] and any(o[0] == 'close' for o in off['orders'])          # the fixture really trades and closes
    assert any('max positions' in r for _, _, r in off['missed'])                 # ... and really skips
    assert on == off                                                              # orders, state, lots, history, skips
    assert not os.path.exists(e0.ml_paths()['dir'])                               # OFF: nothing written at all
    assert 'ml_shadow' not in e0.health
    dec = ledger_rows(e1, 'decisions')
    assert {(r['symbol'], r['taken']) for r in dec} >= {('BTCUSDT', True), ('ETHUSDT', False), ('ETHUSDT', True)}
    skip = next(r for r in dec if not r['taken'])
    assert 'max positions' in skip['reason'] and skip['family'] == 'Trend' and skip['strategy'] == 'ema_mom'
    assert skip['features']['atr_pct'] is not None                                # features_at ran on the candle frame
    labels = ledger_rows(e1, 'labels')
    assert len(labels) == 2 and {l['exit_reason'] for l in labels} == {'exit_signal'}
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
        MLS.record_label(lab, dec, decision_id=did, net_r=(1.5 if k % 2 else -1.0), net_pnl=1.0, exit_reason='stop', trade_id=f'A|BTCUSDT|LONG|{k}')
    raw = A.ml_aggregate(e)
    agg = SA.build_aggregate(raw)
    assert agg['n_trades'] == raw['n_trades'] >= 10 and agg['performance']['all']['n'] == raw['n_trades']
    blob = json.dumps(agg)
    assert 'A|BTCUSDT' not in blob and 'decision_id' not in blob


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
