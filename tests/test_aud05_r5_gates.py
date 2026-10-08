"""AUD-05 r5 (Codex review of 7cebd1e + addendum):
1 an unresolved install / account binding is ONE central gate (Engine.persist_block -> install_block) in front of every
  risk-adding route - manual / take-now included (before the manual early return), automatic, adds, grids, maker - and
  resuming entries (panel or Telegram) is refused until confirm_install;
2 a grid order is never sent unless the side's stop, rounded to the tick as it will be placed, is a valid protective stop;
3 every grid metric the safety code reads (worst_loss_usd, max_notional) is required and bounded;
4 the secret scrub covers the backup's evidence (settings.json.bak.corrupt-*) and every temp name, derived from the code."""
import glob, json, os
import pandas as pd
import pytest
import test_safety as TS
import test_grid as TG
import test_aud05_r4_grid_marker_secrets as R4
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


def unresolved(shape):
    """Codex repro shapes: (a) install.json + .bak deleted beside versioned owned state, restarted under another key;
    (b) corrupt state with an older EMPTY .bak, restarted under another key."""
    e, tmp = mk_engine(); TS.opened(e); e.save_state()
    if shape == 'marker_deleted':
        for p in (e.F['install'], e.F['install'] + '.bak'): os.remove(p)
    else:
        doc = json.load(open(e.F['state'])); doc['lots'] = {}
        with open(e.F['state'] + '.bak', 'w') as f: json.dump(doc, f)
        with open(e.F['state'], 'w') as f: f.write('{"lots": {')
    e2 = restart(tmp, e.trade, key='z' * 16)
    assert e2.install_block() and e2.S['ENTRIES_PAUSED'] is True
    e2.candles = lambda sym, tf: pd.DataFrame(dict(c=[100.0], atr=[2.0], o=[100.0], h=[101.0], l=[99.0]))
    return e2


def _api(monkeypatch, e):
    import app as A
    app = A.App.__new__(A.App); app.engine = e; app.preview = lambda: None; app.cfg = {}; app.tg = None
    monkeypatch.setattr(A, 'APP', app, raising=False)
    return A


SHAPES = ['marker_deleted', 'state_restored_other_key']


@pytest.mark.parametrize('shape', SHAPES)
def test_resume_via_panel_or_telegram_is_refused_until_the_account_is_confirmed(shape, monkeypatch):
    e = unresolved(shape); A = _api(monkeypatch, e)
    n = e.trade.calls.count('open')
    with pytest.raises(ValueError, match='account not confirmed'):
        A.handle('/api/settings', dict(ENTRIES_PAUSED=False))
    assert e.S['ENTRIES_PAUSED'] is True and json.load(open(e.F['settings']))['ENTRIES_PAUSED'] is True
    import telegram_ctl as T
    tg = T.TelegramControl.__new__(T.TelegramControl)
    assert tg.c_resume(e, [], None).startswith('Cannot resume') and e.S['ENTRIES_PAUSED'] is True
    assert e.trade.calls.count('open') == n
    e.confirm_install()
    A.handle('/api/settings', dict(ENTRIES_PAUSED=False))
    assert e.S['ENTRIES_PAUSED'] is False


@pytest.mark.parametrize('shape', SHAPES)
def test_no_route_adds_risk_while_the_account_is_unconfirmed(shape, monkeypatch):
    """Manual (panel, before the manual early return), automatic (even with the pause forced off), adds, maker, grid."""
    e = unresolved(shape); A = _api(monkeypatch, e)
    n = e.trade.calls.count('open')
    with pytest.raises(ValueError, match='account not confirmed'):
        A.handle('/api/action', dict(action='manual_trade', symbol='ETHUSDT', side='LONG', risk=1, stop_atr=2.5))
    e.S['ENTRIES_PAUSED'] = False                                      # even if the pause were lifted some other way
    assert not e.open_lot(TS.SL, 'ETHUSDT', 'LONG', TS.SG, None, e.equity())
    assert e.last_skip.startswith('account not confirmed')
    lot = dict(symbol='BTCUSDT', side='LONG', sleeve='T', qty=1.0, avg=100.0, stop=95.0, mgmt={})
    assert e._add_block(lot, 0.5, 100.0).startswith('account not confirmed')
    with pytest.raises(RuntimeError, match='account not confirmed'):
        e._add_qty(lot, 0.5, 100.0, 'pyramid_add')
    with pytest.raises(RuntimeError, match='account not confirmed'):
        e._maker_place(dict(symbol='ETHUSDT', side='LONG', qty=1.0, filled=0.0, n=0, status='between'))
    assert e.grids._can_add(dict(sym='ETHUSDT', lots={}, cells=[]), 'LONG') is False
    assert e.trade.calls.count('open') == n, 'zero sends'


def test_telegram_has_no_order_opening_command():
    import telegram_ctl as T
    cmds = {n[2:] for n in dir(T.TelegramControl) if n.startswith('c_')}
    assert not cmds & {'buy', 'sell', 'long', 'short', 'open', 'trade', 'take'}, cmds
    e = unresolved('marker_deleted')
    assert e.persist_block() == e.install_block(), 'the central gate carries the account block'


# ------------------------------------------------------------------ 2 rounded protective stop before a grid send
@pytest.mark.parametrize('side,stop', [('LONG', 'tiny'), ('LONG', 'inside'), ('SHORT', 'below_hi')])
def test_grid_never_sends_without_a_valid_rounded_protective_stop(side, stop):
    e, gm, tmp, g = R4.grid_running()
    if stop == 'tiny': g['stop_lo'] = g['lo'] / 1e6                   # rounds to 0.0 at the 0.01 tick (validation bypassed)
    elif stop == 'inside': g['stop_lo'] = g['lo'] * 1.001
    else: g['stop_hi'] = g['hi'] * 0.999
    kind = 'L' if side == 'LONG' else 'S'
    px = (max(c['a'] for c in g['cells'] if c['k'] == 'L') - 0.05 if kind == 'L'
          else min(c['b'] for c in g['cells'] if c['k'] == 'S') + 0.05)
    n = len(e.trade.opens)
    TG.tick(e, gm, px)
    assert len(e.trade.opens) == n and not e.state['lots'], 'no order without a valid protective stop'


def test_grid_rounded_stop_check_passes_a_normal_grid():
    e, gm, tmp, g = R4.grid_running()
    st = gm._stop_ok(g, 'LONG', 99.0)
    assert 0 < st < g['lo'] and st == e._rd(g['stop_lo'], e.rules['BTCUSDT']['tick'])


# ------------------------------------------------------------------ 3 safety-consumed grid metrics
METRICS = {
    'max_notional_missing': lambda d: R4._g(d)['metrics'].pop('max_notional'),
    'max_notional_nan': lambda d: R4._g(d)['metrics'].update(max_notional=float('nan')),
    'max_notional_negative': lambda d: R4._g(d)['metrics'].update(max_notional=-1.0),
    'worst_loss_missing': lambda d: R4._g(d)['metrics'].pop('worst_loss_usd'),
}


@pytest.mark.parametrize('name', sorted(METRICS))
def test_every_safety_consumed_grid_metric_is_required(name):
    e, gm, tmp, g = R4.grid_running()
    doc = json.load(open(e.F['state'])); METRICS[name](doc)
    with open(e.F['state'], 'w') as f: json.dump(doc, f)
    e2, gm2, _ = TG.mk(tmp, fx=e.trade)
    assert e2.integrity['state']['status'] == 'restored_from_backup', name
    assert 'metrics.' in e2.health['incidents']['integrity|state']['msg']
    assert set(E.GRID_SAFETY_METRICS) >= {'max_notional', 'worst_loss_usd'}


# ------------------------------------------------------------------ 4 every settings evidence / temp form
def test_redaction_covers_every_settings_evidence_and_temp_form(tmp_path):
    tmp = str(tmp_path)
    raw = b'{"TELEGRAM_TOKEN": "' + TOKEN.encode() + b'", "MAX_'
    for n in ('settings.json', 'settings.json.bak'):                  # evidence named by quarantine() itself
        with open(os.path.join(tmp, n), 'wb') as f: f.write(raw)
    real = E._secret_bearing
    E._secret_bearing = lambda p: False                               # as a pre-r3 build would have left it: bytes kept
    try:
        main_ev = E.quarantine(os.path.join(tmp, 'settings.json')); bak_ev = E.quarantine(os.path.join(tmp, 'settings.json.bak'))
    finally:
        E._secret_bearing = real
    assert os.path.basename(bak_ev).startswith('settings.json.bak.corrupt-')
    for n in ('settings.json.bak.corrupt-20260101T000000Z-2', 'settings.json.1-2.tmp', 'settings.json.bak.1-2.tmp'):
        with open(os.path.join(tmp, n), 'wb') as f: f.write(raw)
    assert E.redact_settings_files(tmp) == []
    left = [os.path.basename(p) for p in glob.glob(os.path.join(tmp, '*'))
            if TOKEN[:20].encode() in open(p, 'rb').read() or b'TELEGRAM_TOKEN' in open(p, 'rb').read()]
    assert left == [] and len(glob.glob(os.path.join(tmp, 'settings.json*'))) == 5
