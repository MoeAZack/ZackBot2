import copy
import hashlib
import hmac
import io
import json
import os
import threading
import types
from datetime import datetime, timedelta, timezone

import pytest

import candidate_activation as C
import engine as E
import paper_cohort_cli as CLI


CARD = dict(id='C-RBR', key='ema_mom', name='candidate', enabled=False, share=0.1,
            risk=0.005, max_pos=2, symbols=['BTCUSDT'], sides='short', mgmt={}, tf='4h')
BOUNDS = dict(max_calendar_days=14, max_closed_trades=30, max_drawdown_r=6.0,
              max_open_positions=2, max_risk_per_trade=0.005)
COMMIT = 'a' * 40
NOW = datetime(2026, 11, 7, 0, 0, tzinfo=timezone.utc)
START = NOW + timedelta(hours=1)


class FakeEngine:
    BOUNDARY_BUSY = ('lots', 'pending_entries', 'resting_entries', 'unconfirmed_entries', 'grids')

    def __init__(self, root, runtime, evidence):
        self.lock = threading.RLock(); self.live = False
        self.trade = types.SimpleNamespace(base=E.TESTNET,
                                           positions=lambda **_kw: {}, open_orders_all=lambda: [])
        self.dir = str(runtime); self.S = {'SLEEVES': [copy.deepcopy(CARD)]}
        self.state = dict(cohort={'id': 'cohort-1'}, lots={}, pending_entries={}, resting_entries={},
                          unconfirmed_entries={}, grids={}, candidate_cost_pending={})
        self.history, self.cohort_rows, self._deferred = [], [], []
        self.saved = []
        self.boundary_calls = []; self.evidence = evidence

    def commit_settings(self, ns):
        self.saved.append(copy.deepcopy(ns)); self.S = copy.deepcopy(ns)

    def cohort_boundary(self, path, note='', accept_drift=False):
        self.boundary_calls.append((path, note, accept_drift))
        return dict(closed='cohort-1', new='cohort-2', pack=os.path.join(path, 'cohort-1'), pack_sha='b' * 64)


@pytest.fixture
def world(tmp_path, monkeypatch):
    root, runtime, evidence = tmp_path / 'repo', tmp_path / 'runtime', tmp_path / 'evidence'
    root.mkdir(); runtime.mkdir(); evidence.mkdir(); (root / 'candidate.py').write_text('EDGE = 1\n')
    e = FakeEngine(root, runtime, evidence)
    monkeypatch.setattr(C, '_running_commit', lambda engine: COMMIT)
    monkeypatch.setattr(C, '_now', lambda: NOW)
    return e, root, evidence


def stage(e, root, evidence):
    return C.stage(e, dict(slot_id='C-RBR', bounds=BOUNDS, source_commit=COMMIT,
                           source_files=['candidate.py'], evidence_path=str(evidence),
                           start_utc=START.isoformat()),
                   testnet=E.TESTNET, root=str(root))


def test_generic_stage_preflight_enable_bound_and_rollback(world, monkeypatch):
    e, root, evidence = world
    frozen = stage(e, root, evidence)
    cfg = e.S[C.CONFIG_KEY]['C-RBR']
    assert not e.S['SLEEVES'][0]['enabled'] and cfg['config_sha256'] == frozen['config_sha256']
    assert cfg['card_sha256'] == C._sha(C._card(CARD)) and cfg['source_sha256'] == C._sha(cfg['source_manifest'])

    pf = C.preflight(e, {'slot_id': 'C-RBR'}, testnet=E.TESTNET, root=str(root))
    assert pf['passed'] and pf['cohort_id'] == 'cohort-1'
    out = C.enable(e, {'slot_id': 'C-RBR'}, testnet=E.TESTNET, root=str(root))
    assert out['enabled'] and e.S['SLEEVES'][0]['enabled'] is True
    assert 'before its frozen start' in C.runtime_block(e, e.S['SLEEVES'][0], testnet=E.TESTNET)
    monkeypatch.setattr(C, '_now', lambda: START + timedelta(hours=1))

    # Measured R, not booked P&L, trips this candidate's own frozen drawdown bound.
    e.history.append(dict(id='x', sleeve='C-RBR', symbol='BTCUSDT', pnl=-7.0, pnl_gross=-7.0,
                          pnl_fill_adj=0.0, fees_actual=0.0, funding_actual=0.0, fill_refs=[],
                          risk_usd=1.0, closed='2026-11-07T00:00:00+00:00'))
    assert 'measured drawdown' in C.runtime_block(e, e.S['SLEEVES'][0], testnet=E.TESTNET)
    rolled = C.rollback(e, {'slot_id': 'C-RBR', 'reason': 'bound reached'}, testnet=E.TESTNET)
    assert rolled['enabled'] is False and e.S['SLEEVES'][0]['enabled'] is False
    assert [x['action'] for x in e.S[C.AUDIT_KEY]] == ['enable', 'rollback']


@pytest.mark.parametrize('change,match', [
    (lambda e: setattr(e, 'live', True), 'PAPER/testnet only'),
    (lambda e: setattr(e.trade, 'base', 'https://fapi.binance.com'), 'exact Binance USD-M TESTNET'),
    (lambda e: e.state['lots'].update(x={'symbol': 'BTCUSDT'}), 'not flat/no-working'),
    (lambda e: e.state['pending_entries'].update(x={'symbol': 'BTCUSDT'}), 'not flat/no-working'),
    (lambda e: e.state['candidate_cost_pending'].update(x={'sleeve': 'C-RBR', 'symbol': 'BTCUSDT'}), 'pending'),
    (lambda e: setattr(e.trade, 'positions', lambda **_kw: {('BTCUSDT', 'LONG'): 1.0}), 'open position'),
    (lambda e: setattr(e.trade, 'open_orders_all', lambda: [{'symbol': 'BTCUSDT'}]), 'working order'),
])
def test_preflight_fails_closed_on_environment_flatness_working_and_cost_gates(world, change, match):
    e, root, evidence = world; stage(e, root, evidence); change(e)
    pf = C.preflight(e, {'slot_id': 'C-RBR'}, testnet=E.TESTNET, root=str(root))
    assert not pf['passed'] and match.lower() in ' '.join(pf['failures']).lower()
    with pytest.raises(ValueError, match='fresh preflight|stored PASS|PAPER/testnet|exact Binance'):
        C.enable(e, {'slot_id': 'C-RBR'}, testnet=E.TESTNET, root=str(root))
    assert e.S['SLEEVES'][0]['enabled'] is False


def test_source_config_and_card_are_immutable_and_sleeves_cannot_bypass_enable(world):
    e, root, evidence = world; stage(e, root, evidence)
    with pytest.raises(ValueError, match='immutable once staged'):
        stage(e, root, evidence)
    (root / 'candidate.py').write_text('EDGE = 2\n')
    pf = C.preflight(e, {'slot_id': 'C-RBR'}, testnet=E.TESTNET, root=str(root))
    assert not pf['passed'] and any('source hash changed' in x for x in pf['failures'])
    posted = copy.deepcopy(e.S['SLEEVES']); posted[0]['enabled'] = True
    with pytest.raises(ValueError, match='only through'):
        C.sleeves_guard(e.S, posted)
    posted[0]['enabled'] = False; posted[0]['risk'] = 0.004
    with pytest.raises(ValueError, match='immutable'):
        C.sleeves_guard(e.S, posted)


def test_forged_or_corrupt_config_fails_closed(world):
    e, root, evidence = world; stage(e, root, evidence)
    e.S[C.CONFIG_KEY]['C-RBR']['card_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='no valid immutable'):
        C.preflight(e, {'slot_id': 'C-RBR'}, testnet=E.TESTNET, root=str(root))


def test_stage_requires_authenticated_installed_commit(world, monkeypatch):
    e, root, evidence = world; monkeypatch.setattr(C, '_running_commit', lambda engine: 'b' * 40)
    with pytest.raises(ValueError, match='authenticated installed build'):
        stage(e, root, evidence)


def _post(app, path, body, token):
    h = app.H.__new__(app.H); raw = json.dumps(body).encode(); out = []
    h.path, h.rfile = path, io.BytesIO(raw)
    h.headers = {'Host': app.HOSTS[0], 'Content-Type': 'application/json', 'Content-Length': str(len(raw)),
                 **({'X-ZB-Token': token} if token else {})}
    h._json = lambda obj, code=200: out.append((code, obj)); h.do_POST(); return out[0]


def test_boundary_route_is_authenticated_and_passes_explicit_drift_decision(world, monkeypatch):
    import app as A
    e, _root, evidence = world
    monkeypatch.setattr(A, 'APP', types.SimpleNamespace(engine=e, preview=lambda: None))
    body = dict(evidence_path=str(evidence), note='new qualified cohort', accept_drift=False)
    code, out = _post(A, C.BOUNDARY_ROUTE, body, None)
    assert code == 401 and not e.boundary_calls
    code, out = _post(A, C.BOUNDARY_ROUTE, body, A.TOKEN)
    assert code == 200 and out['ok'] and e.boundary_calls == [(str(evidence), 'new qualified cohort', False)]
    assert out['msg']['accept_drift'] is False and out['msg']['evidence_path'] == str(evidence)


def test_boundary_requires_explicit_boolean_and_exact_environment(world):
    e, _root, evidence = world
    base = dict(evidence_path=str(evidence), note='x', accept_drift=False)
    for bad in ({k: v for k, v in base.items() if k != 'accept_drift'}, dict(base, accept_drift='false')):
        with pytest.raises(ValueError, match='exactly|explicit JSON boolean'):
            C.boundary(e, bad, testnet=E.TESTNET)
    e.trade.base = 'https://fapi.binance.com'
    with pytest.raises(ValueError, match='exact Binance USD-M TESTNET'):
        C.boundary(e, base, testnet=E.TESTNET)


def test_cli_verifies_hmac_then_posts_authenticated_explicit_boundary(tmp_path, monkeypatch):
    token, port = 't' * 43, 8765
    session = tmp_path / 'session.json'; session.write_text(json.dumps({'token': token, 'port': port}))
    evidence = tmp_path / 'evidence'; evidence.mkdir(); calls = []

    class Response:
        def __init__(self, body): self.body = json.dumps(body).encode()
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self): return self.body

    def open_(req, timeout=5):
        calls.append(req)
        if len(calls) == 1:
            nonce = req.full_url.split('nonce=', 1)[1]
            return Response({'app': 'zackbot', 'proof': hmac.new(token.encode(), nonce.encode(), hashlib.sha256).hexdigest()})
        payload = json.loads(req.data)
        assert req.headers['X-zb-token'] == token and payload['accept_drift'] is True
        return Response({'ok': True, 'msg': {'closed': 'old', **payload}})

    monkeypatch.setattr(CLI, 'urlopen', open_)
    out = CLI.call_boundary(str(session), str(evidence), 'explicit', True)
    assert out['closed'] == 'old' and out['accept_drift'] is True and len(calls) == 2


def test_engine_pack_seals_accept_drift_in_note_and_archive():
    from test_book_ledger import books_engine
    e, _ = books_engine(); dest = os.path.join(os.path.dirname(e.dir), 'external-packs')
    out = e.cohort_boundary(dest, note='explicit', accept_drift=False)
    with open(os.path.join(out['pack'], 'note.json'), encoding='utf-8') as fh: note = json.load(fh)
    assert note == {'accept_drift': False, 'note': 'explicit'}
    assert e.state['cohort_archive'][-1]['accept_drift'] is False
