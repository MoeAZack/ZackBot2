"""AUD-05 r4 (Codex review of 8558b0f):
1 a persisted grid's executable topology is rebuilt from its parameters (lines span lo..hi, cells between adjacent lines,
  sides, per-cell quantity) and a risk-adding grid order is checked against the mark and the grid's own capital;
2 the metrics the bot uses are required, and the lot's worst case is recomputed BEFORE any send (no send-then-crash);
3 a deleted install.json next to versioned data fails closed (owner confirms THIS_ACCOUNT); install.json.bak restores it;
  a legacy (unversioned) folder that owns something needs the confirmation too;
4 the upgrade scrub covers settings evidence (.corrupt-*) and abandoned temp files; failures are an unresolved-secret incident;
5 an account change after a state restore / loss keeps the old marker and requires the confirmation (owned set unknown)."""
import glob, json, os
import pytest
import test_safety as TS
import test_grid as TG
from test_safety import mk_engine

E, BC = TS.E, TS.BC
TOKEN = '123456789:AAHsecretTelegramTokenValue_0123456'


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(E.Engine, 'notify', lambda self, t: None)
    monkeypatch.setattr(E, '_sleep', lambda s: None)


def restart(tmp, fake, key='k' * 16):
    E.Futures = lambda *a, **k: fake
    e = E.Engine(dict(MODE='paper', API_KEY=key, API_SECRET='s' * 16), tmp)
    e.data = e.trade; e.connect(); e.marks = e.trade.marks()
    return e


def inc(e, key):
    return (e.health.get('incidents') or {}).get(key) or {}


# ------------------------------------------------------------------ 1/2 grids
class OrdersX(TG.FakeX):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.opens = []

    def open(self, s, ps, q, cid=None):
        self.opens.append((s, ps, float(q), cid)); return super().open(s, ps, q)


def grid_running():
    e, gm, tmp = TG.mk(fx=OrdersX())
    with e.lock: gm.start('G1', 'BTCUSDT')
    e.save_state(); e.save_state()                                    # .bak = the same valid active (flat) grid
    return e, gm, tmp, TG.grid_of(e)


def _g(doc): return next(iter(doc['grids'].values()))


def _shift_up(doc):
    """Codex r3 repro: keep q * lo small but move the executable lines to ~1e8 (lines no longer span lo..hi)."""
    g = _g(doc); f = 1e6
    g['lines'] = [x * f for x in g['lines']]
    for c in g['cells']: c['a'] *= f; c['b'] *= f


TOPOLOGY = {
    'lines_scaled': _shift_up,
    'lines_spacing': lambda d: _g(d)['lines'].__setitem__(3, (_g(d)['lines'][2] + _g(d)['lines'][3]) / 2),
    'cell_not_adjacent': lambda d: _g(d)['cells'][2].update(a=_g(d)['lines'][1]),
    'cell_sides_swapped': lambda d: (_g(d)['cells'][0].update(k='S'), _g(d)['cells'][-1].update(k='L')),   # counts unchanged
    'cell_qty_above_build': lambda d: _g(d)['cells'][-1].update(q=_g(d)['cells'][-1]['q'] * 1.1),   # q*lo still < budget
    'span_too_wide': lambda d: _g(d).update(lo=_g(d)['lo'] / 100),
    'p0_outside': lambda d: _g(d).update(p0=_g(d)['hi'] * 2),
    'levels_mismatch': lambda d: _g(d)['cfg'].update(levels=12),
    'metrics_missing': lambda d: _g(d).update(metrics={}),
    'metrics_nan': lambda d: _g(d)['metrics'].update(worst_loss_usd=float('nan')),
    'metrics_negative': lambda d: _g(d)['metrics'].update(worst_loss_usd=-5),
}


@pytest.mark.parametrize('name', sorted(TOPOLOGY))
def test_grid_topology_and_metrics_are_rebuilt_and_checked_before_use(name):
    e, gm, tmp, g = grid_running()
    doc = json.load(open(e.F['state'])); TOPOLOGY[name](doc)
    with open(e.F['state'], 'w') as f: json.dump(doc, f)
    e2, gm2, _ = TG.mk(tmp, fx=e.trade)
    assert e2.integrity['state']['status'] == 'restored_from_backup', name
    assert 'invalid' in inc(e2, 'integrity|state')['msg'] and e2.S['ENTRIES_PAUSED'] is True


@pytest.mark.parametrize('name', ['lines_scaled', 'metrics_missing'])
def test_a_crafted_grid_without_backup_never_sends(name):
    """Codex r3 repros: 50 BTC at ~1e8 / a LONG sent then _create crashing on missing metrics. No .bak: fail closed, no send."""
    e, gm, tmp, g = grid_running()
    os.remove(e.F['state'] + '.bak')
    doc = json.load(open(e.F['state'])); TOPOLOGY[name](doc)
    with open(e.F['state'], 'w') as f: json.dump(doc, f)
    n = len(e.trade.opens)
    e2, gm2, _ = TG.mk(tmp, fx=e.trade)
    for px in (90.0, 95.0, 99.5, 1e8): TG.tick(e2, gm2, px)
    assert len(e.trade.opens) == n and e2.S['ENTRIES_PAUSED'] is True and not e2.state.get('grids')


def test_grid_first_order_risk_is_computed_before_the_send(monkeypatch):
    """If the lot's worst case cannot be computed, nothing is sent (it used to be sent first, then _create raised)."""
    e, gm, tmp, g = grid_running()
    import grid as G
    monkeypatch.setattr(G, 'risk_metrics', lambda *a, **k: dict(worst_loss_usd=float('nan')))
    top = max(c['a'] for c in g['cells'] if c['k'] == 'L')
    n = len(e.trade.opens)
    TG.tick(e, gm, top - 0.05)
    assert len(e.trade.opens) == n and not g.get('op') and not e.state['lots']


def test_grid_order_beyond_its_own_capital_is_never_sent():
    e, gm, tmp, g = grid_running()
    top = max(c['a'] for c in g['cells'] if c['k'] == 'L')
    g['capital'] = g['capital'] / 1000                                # in memory only (as if the budget shrank)
    n = len(e.trade.opens)
    TG.tick(e, gm, top - 0.05)
    assert len(e.trade.opens) == n and not e.state['lots']


# ------------------------------------------------------------------ 3 deleted marker
def test_deleted_marker_next_to_versioned_data_fails_closed_under_another_key():
    """Codex repro: one lot, delete only install.json (and its backup), restart under a different API key."""
    e, tmp = mk_engine(); TS.opened(e); e.save_state()
    for p in (e.F['install'], e.F['install'] + '.bak'): os.remove(p)
    e2 = restart(tmp, e.trade, key='z' * 16)
    assert e2.S['ENTRIES_PAUSED'] is True and 'deleted' in inc(e2, 'integrity|install')['msg']
    assert not os.path.exists(e.F['install']), 'never rebound to the new key without confirmation'
    assert 'confirmed' in e2.confirm_install() and os.path.exists(e.F['install'])


def test_marker_backup_restores_a_deleted_marker():
    e, tmp = mk_engine(); TS.opened(e); e.save_state()
    good = open(e.F['install'], 'rb').read()
    os.remove(e.F['install'])
    e2 = restart(tmp, e.trade)
    assert e2.S['ENTRIES_PAUSED'] is False and json.load(open(e.F['install'])) == json.loads(good)
    assert inc(e2, 'integrity|install-restored')['open'], 'restored from install.json.bak, not re-initialized'
    e3 = restart(tmp, e.trade, key='z' * 16)                          # and the restored marker still guards the account
    assert e3.S['ENTRIES_PAUSED'] is True and e3.install_mismatch


def test_legacy_unversioned_folder_that_owns_something_needs_confirmation():
    """Decision: a pre-marker folder cannot say which account its lots belong to - confirm once after the upgrade."""
    e, tmp = mk_engine(); TS.opened(e); e.save_state()
    doc = json.load(open(e.F['state'])); doc.pop('schema_version')
    for p in (e.F['install'], e.F['install'] + '.bak', e.F['state'] + '.bak', e.F['settings'], e.F['settings'] + '.bak'):
        if os.path.exists(p): os.remove(p)
    with open(e.F['state'], 'w') as f: json.dump(doc, f)
    e2 = restart(tmp, e.trade)
    assert e2.S['ENTRIES_PAUSED'] is True and e2.install_mismatch and not os.path.exists(e.F['install'])
    assert len(e2.state['lots']) == 1, 'the legacy ownership is kept (and reconciled), only new risk waits'


# ------------------------------------------------------------------ 4 secrets in evidence / temp siblings
def _token_files(tmp):
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(tmp, '*'))
                  if os.path.isfile(p) and (TOKEN[:20].encode() in open(p, 'rb').read() or b'TELEGRAM_TOKEN' in open(p, 'rb').read()))


def test_upgrade_scrub_covers_evidence_and_abandoned_temp_files(monkeypatch, tmp_path):
    import app as A
    tmp = str(tmp_path)
    seeds = {'settings.json.corrupt-20260101T000000Z': b'{"TELEGRAM_TOKEN": "' + TOKEN.encode() + b'", "MAX_',
             'settings.json.1234-5678.tmp': json.dumps(dict(TELEGRAM_TOKEN=TOKEN, MAX_LEVERAGE=5)).encode(),
             'settings.json.bak.1234-5678.tmp': b'\x00junk ' + TOKEN.encode(),
             'state.json': b'{}'}
    for n, raw in seeds.items():
        with open(os.path.join(tmp, n), 'wb') as f: f.write(raw)
    monkeypatch.setattr(A, 'write_cfg', lambda u: None)
    A.migrate_settings_secrets({}, tmp)
    assert _token_files(tmp) == []
    assert b'"MAX_' in open(os.path.join(tmp, 'settings.json.corrupt-20260101T000000Z'), 'rb').read(), 'the evidence is kept'


def test_an_unredactable_secret_sibling_is_an_unresolved_secret_incident(monkeypatch, tmp_path):
    tmp = str(tmp_path)
    with open(os.path.join(tmp, 'settings.json.corrupt-20260101T000000Z'), 'wb') as f: f.write(b'"TELEGRAM_TOKEN": "' + TOKEN.encode())
    real = E._write_bytes_durable
    def refuse(p, data):
        if '.corrupt-' in p: raise PermissionError(13, 'locked')
        return real(p, data)
    monkeypatch.setattr(E, '_write_bytes_durable', refuse)
    assert E.scrub_legacy_secrets(tmp) and 'corrupt' in E.scrub_legacy_secrets(tmp)[0]
    e = restart(tmp, TS.FakeX())
    assert inc(e, 'secret-unresolved')['open']


# ------------------------------------------------------------------ 5 account change after a state restore / loss
@pytest.mark.parametrize('how', ['restored', 'lost'])
def test_account_change_after_state_recovery_keeps_the_old_marker(how):
    e, tmp = mk_engine(); TS.opened(e); e.save_state()
    if how == 'lost':
        os.remove(e.F['state'] + '.bak')
    else:                                                             # .bak = an older copy with NO lot
        doc = json.load(open(e.F['state'])); doc['lots'] = {}
        with open(e.F['state'] + '.bak', 'w') as f: json.dump(doc, f)
    with open(e.F['state'], 'w') as f: f.write('{"lots": {')
    before = open(e.F['install'], 'rb').read()
    e2 = restart(tmp, e.trade, key='z' * 16)
    assert e2.S['ENTRIES_PAUSED'] is True and e2.install_mismatch and open(e.F['install'], 'rb').read() == before
    assert 'state' in ' '.join(e2.install_mismatch['owned'])
