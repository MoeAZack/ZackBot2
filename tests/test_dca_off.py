"""Owner decision 2026-10-07: DCA (safety-order averaging) is OFF until a better margin strategy for short-term regimes is
found. DCA logic stays in the engine and the backtester; the live bot runs it only with DCA_ENABLED switched on, research
passes dca_enabled=True explicitly. Covers: defaults, settings migration, the entry gate + its missed record / reason code,
open baskets (stop / target / exits kept, no new safety orders), explicit enable = the old behaviour, backtester and Lab
defaults, the app API / panel label."""
import copy, json, os, sys, tempfile, types

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())

import engine as E          # noqa: E402
import strategies as S      # noqa: E402
import backtest as B        # noqa: E402
import trade_audit as TA    # noqa: E402
from test_safety import mk_engine, SL, SG   # noqa: E402

LABEL = 'DCA off — paused pending a better short-term strategy (owner decision 2026-10-07)'
REASON = 'DCA paused (owner decision)'
DCA_MG = {'dca': {'n': 3, 'step_atr': 1.0, 'scale': 1.5, 'tp_atr': 1.0, 'stop_atr': 2.0}}


def dca_sl(id='D'):
    return dict(SL, id=id, key='dca_dip', name='DCA', mgmt=copy.deepcopy(DCA_MG))


def _close(e):
    try: E.close_fill_writer(e.F['audit'])
    except Exception: pass


# ------------------------------------------------------------------ defaults and settings migration
def test_defaults_are_off_with_the_owner_label():
    assert S.DCA_ENABLED_DEFAULT is False and E.GLOBAL_DEFAULTS['DCA_ENABLED'] is False
    assert S.DCA_OFF_LABEL == LABEL and S.DCA_PAUSED_REASON == REASON == E.DCA_PAUSED
    e, _ = mk_engine()
    try: assert e.S['DCA_ENABLED'] is False
    finally: _close(e)


def test_uses_dca_detects_strategy_and_mgmt_blocks():
    assert S.uses_dca(dict(key='dca_dip')) and S.uses_dca(dict(key='dca_dip', mgmt={'dca': {'n': 4}}))
    assert S.uses_dca(dict(key='ema_mom', mgmt=copy.deepcopy(DCA_MG)))          # any slot with a dca block averages down
    assert not S.uses_dca(dict(key='ema_mom', mgmt={'pyramid': {'n': 1}})) and not S.uses_dca(dict(key='ema_st'))
    assert not S.uses_dca(None) and S.uses_dca(dict(key='dca_dip', mgmt='garbage'))


@pytest.mark.parametrize('stored, want', [(None, False), (True, True), (False, False), ('yes', False), (1, False)])
def test_settings_migration_of_an_older_settings_json(stored, want):
    """An installed bot upgraded with a settings.json written before this change (no DCA_ENABLED key, a DCA profile)
    loads with DCA off - only an explicit JSON true keeps it on."""
    tmp = tempfile.mkdtemp()
    old = dict(PRESET='balanced', SLEEVES=copy.deepcopy(E.PRESETS['balanced']['sleeves']), MAX_LEVERAGE=10, CAP_SINCE='2026-01-01T00:00:00+00:00')
    if stored is not None: old['DCA_ENABLED'] = stored
    with open(os.path.join(tmp, 'settings.json'), 'w', encoding='utf-8') as f: json.dump(old, f)
    e, _ = mk_engine(tmp)
    try:
        assert e.S['DCA_ENABLED'] is want and e.S['PRESET'] == 'balanced'
        assert [s['id'] for s in e.S['SLEEVES']] == ['MOM', 'ST', 'DCA']          # the profile itself is not edited
        e.save_settings()
        with open(os.path.join(tmp, 'settings.json'), encoding='utf-8') as f: assert json.load(f)['DCA_ENABLED'] is want
    finally: _close(e)


# ------------------------------------------------------------------ entry gate
def test_cycle_skips_the_dca_slot_and_records_a_stable_reason():
    e, _ = mk_engine()
    try:
        d = dca_sl(); e.S['SLEEVES'] = [SL, d]
        e.compute_signals = lambda tf, syms, extra=(): ({f"{SL['id']}|BTCUSDT": dict(SG), f"{d['id']}|ETHUSDT": dict(SG)}, {})
        n_orders = len(e.trade.calls) if hasattr(e.trade, 'calls') else None
        e.cycle('4h')
        lots = list(e.state['lots'].values())
        assert [l['sleeve'] for l in lots] == [SL['id']]                       # the non-DCA slot still trades
        m = [r for r in e.missed if r['sleeve'] == d['id']]
        assert len(m) == 1 and m[0]['reason'] == REASON and m[0]['symbol'] == 'ETHUSDT' and 'kind' not in m[0]
        assert (m[0]['stage'], m[0]['code']) == ('filter', 'dca_paused')
        if n_orders is not None:
            assert not any('ETHUSDT' in str(c) for c in e.trade.calls[n_orders:] if 'open' in str(c).lower())
    finally: _close(e)


def test_every_entry_route_is_gated():
    e, _ = mk_engine()
    try:
        d = dca_sl(); e.S['SLEEVES'] = [d]
        assert e.entry_block(d, 'BTCUSDT', 'LONG') == REASON
        assert not e.open_lot(d, 'BTCUSDT', 'LONG', SG, None, e.equity()) and e.last_skip == REASON
        e.signals[f"{d['id']}|BTCUSDT"] = dict(SG)
        with pytest.raises(ValueError, match='DCA paused'): e.take_signal(d['id'], 'BTCUSDT')    # "Take now" / Telegram
        mg = dict(SL, id='M', mgmt=copy.deepcopy(DCA_MG)); e.S['SLEEVES'] = [mg]                # dca block on another strategy
        assert e.entry_block(mg, 'BTCUSDT', 'LONG') == REASON
        assert e.entry_block(SL, 'BTCUSDT', 'LONG') is None                                    # non-DCA untouched
        assert not e.state['lots']
    finally: _close(e)


def test_trailing_entry_armed_earlier_is_cancelled_with_the_dca_reason():
    """A trailing entry armed while DCA was on and triggered after it was switched off goes through entry_block too."""
    e, _ = mk_engine()
    try:
        d = dict(dca_sl(), trail_entry={'dev_atr': 0.5, 'max_bars': 3}); e.S['SLEEVES'] = [d]; e.S['DCA_ENABLED'] = True
        e.compute_signals = lambda tf, syms, extra=(): ({f"{d['id']}|BTCUSDT": dict(SG)}, {})
        e.cycle('4h')
        assert e.state['pending_entries'], e.missed[-1:]
        e.S['DCA_ENABLED'] = False
        e.trade.mark['BTCUSDT'] = 100.0 + 2.0                                  # rebound > 0.5 ATR off the extreme
        e.manage(e.trade.marks())
        assert not e.state['lots'] and not e.state['pending_entries']
        r = e.missed[-1]
        assert r['reason'].startswith('trailing entry cancelled') and REASON in r['reason'], r
        assert r['code'] == 'dca_paused' and r['stage'] == 'trailing'
    finally: _close(e)


def test_reason_code_table_and_not_other():
    assert TA.reason_code(REASON) == ('filter', 'dca_paused')
    assert TA.reason_info('trailing entry cancelled: ' + REASON)['code'] == 'dca_paused'
    from test_trade_audit import _engine_reason_literals
    assert REASON in _engine_reason_literals()                         # the scan sees the literal gate text


# ------------------------------------------------------------------ open baskets
def _basket(e):
    d = dca_sl(); e.S['SLEEVES'] = [d]; e.S['DCA_ENABLED'] = True
    assert e.open_lot(d, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    return next(iter(e.state['lots'].items()))


def test_open_basket_gets_no_new_safety_orders_but_keeps_target_and_stop():
    e, _ = mk_engine()
    try:
        k, lot = _basket(e)
        stop, tp, qty, stop_id = lot['stop'], lot['tp'], lot['qty'], lot['stop_id']
        e.S['DCA_ENABLED'] = False                                    # switched off with a basket open
        e.trade.mark['BTCUSDT'] = lot['levels'][1] - 0.01             # through two safety-order levels
        e.manage(e.trade.marks())
        assert lot['dca'] == 0 and lot['qty'] == qty and lot['tp'] == tp and lot['stop'] == stop and lot['stop_id'] == stop_id
        assert lot['add_blocked'] == REASON
        ab = [r for r in e.missed if r.get('kind') == 'add_blocked']
        assert len(ab) == 1 and ab[0]['reason'] == 'safety_order blocked: ' + REASON
        e.manage(e.trade.marks())                                     # logged once, not every pass
        assert len([r for r in e.missed if r.get('kind') == 'add_blocked']) == 1
        assert any('adds blocked: ' + REASON in s for s in e.exit_plan(lot)['steps'])
        e.trade.mark['BTCUSDT'] = tp + 0.01                           # the basket target still closes it
        e.manage(e.trade.marks())
        assert k not in e.state['lots'] and e.history[-1]['exit_reason'] == 'basket_tp'
    finally: _close(e)


def test_open_basket_stop_still_runs_while_dca_is_off():
    e, _ = mk_engine()
    try:
        k, lot = _basket(e)
        e.S['DCA_ENABLED'] = False
        e.trade.mark['BTCUSDT'] = lot['levels'][2] - 0.5                # through every level, still above the basket stop
        e.manage(e.trade.marks())
        assert lot['dca'] == 0 and not any(f[1] == 'safety_order' for f in lot['fills'])
        assert e.trade.stops[lot['stop_id']] == ('BTCUSDT', 'LONG', lot['qty'], lot['stop'])   # protective stop unchanged on Binance
        e.trade.stops.pop(lot['stop_id']); e.trade.pos[('BTCUSDT', 'LONG')] -= lot['qty']       # Binance fills the stop
        e.trade.mark['BTCUSDT'] = lot['stop'] - 0.5
        e.reconcile(e.equity())
        assert k not in e.state['lots'] and e.history[-1]['exit_reason'] == 'stop'
    finally: _close(e)


def _scenario(e):
    """Old DCA behaviour: open, three safety orders, basket TP. Returns the observable path."""
    k, lot = _basket(e)
    out = [dict(levels=[round(x, 8) for x in lot['levels']], w=lot['w'], tp=round(lot['tp'], 8), q=lot['qty'], stop=lot['stop'])]
    for j in range(3):
        e.trade.mark['BTCUSDT'] = lot['levels'][j] - 0.001
        e.manage(e.trade.marks())
        out.append(dict(dca=lot['dca'], q=round(lot['qty'], 8), avg=round(lot['avg'], 8), tp=round(lot['tp'], 8), blocked=lot.get('add_blocked')))
    e.trade.mark['BTCUSDT'] = lot['tp'] + 0.01
    e.manage(e.trade.marks())
    out.append(dict(open=k in e.state['lots'], why=e.history[-1]['exit_reason']))
    return out


def test_explicit_enable_restores_the_old_dca_behaviour():
    """DCA_ENABLED True = the pre-change engine: every level fills in order (q0 * scale**k), the target is re-computed from
    the new average after each fill, the basket TP closes it. Re-enabling after a pause resumes the remaining levels."""
    e, _ = mk_engine()
    try:
        p = _scenario(e)
        q0, lv = p[0]['q'], p[0]['levels']
        assert p[0]['w'] == [1.5, 2.25, 3.375] and lv == [round(100 - k * 2.0, 8) for k in (1, 2, 3)]
        assert [x['dca'] for x in p[1:4]] == [1, 2, 3] and all(x['blocked'] is None for x in p[1:4])
        assert p[1]['q'] > q0 and p[3]['q'] == pytest.approx(q0 * (1 + 1.5 + 2.25 + 3.375), rel=1e-3)
        assert all(x['tp'] == pytest.approx(x['avg'] + 2.0, abs=1e-6) for x in p[1:4])
        assert p[-1] == dict(open=False, why='basket_tp')
    finally: _close(e)
    e, _ = mk_engine()                                                # pause, then switch back on
    try:
        k, lot = _basket(e); e.S['DCA_ENABLED'] = False
        e.trade.mark['BTCUSDT'] = lot['levels'][0] - 0.001; e.manage(e.trade.marks())
        assert lot['dca'] == 0
        e.S['DCA_ENABLED'] = True; e.manage(e.trade.marks())
        assert lot['dca'] == 1 and 'add_blocked' not in lot
    finally: _close(e)


def test_replay_harness_runs_dca_explicitly_on_both_sides():
    """replay.run_replay is a research / parity harness: DCA on for the engine AND the backtester unless told otherwise."""
    import inspect, replay
    assert inspect.signature(replay.run_replay).parameters['dca_enabled'].default is True
    assert inspect.signature(B.run).parameters['dca_enabled'].default is True


# ------------------------------------------------------------------ backtester / Lab
def _book(n=900, seed=3, syms=('BTCUSDT', 'ETHUSDT', 'SOLUSDT')):
    rng = np.random.default_rng(seed); t = pd.date_range('2024-01-01', periods=n, freq='4h'); raw = {}
    for j, s in enumerate(syms):
        c = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.012, n) + 0.004 * np.sin(np.arange(n) / (9 + j))))
        o = np.r_[c[0], c[:-1]]
        raw[s] = pd.DataFrame(dict(t=t, o=o, h=np.maximum(o, c) * 1.006, l=np.minimum(o, c) * 0.994, c=c, v=1000.0))
    return B.Book(raw)


def _cfg(sleeves, syms):
    return [dict(key=s['key'], share=s['share'], risk=s['risk'], max_pos=s['max_pos'], sides=s.get('sides'), mgmt=s.get('mgmt', {}),
                 symbols=list(syms), id=s['id']) for s in sleeves if s.get('tf', '4h') == '4h']


def test_backtest_presets_without_dca_take_no_dca_trades():
    bk = _book(); syms = list(bk.syms)
    for name in ('calm', 'balanced', 'boost'):
        cfg = _cfg(E.PRESETS[name]['sleeves'], syms)
        tr, cv = B.run(bk, cfg, start=500.0, dca_enabled=False)
        assert not len(tr) or 'dca_dip' not in set(tr.sleeve), name
        assert cv.attrs['dca']['enabled'] is False and cv.attrs['dca']['paused_slots'] == ['DCA'] and cv.attrs['dca']['paused_signals'] > 0
        tr_on, cv_on = B.run(bk, cfg, start=500.0, dca_enabled=True)
        assert cv_on.attrs['dca'] == dict(enabled=True, paused_signals=0, paused_slots=[])
        if name != 'calm': assert 'dca_dip' in set(tr_on.sleeve), name          # calm's 1% baskets fall below the minimum here


def test_backtest_off_equals_the_run_without_the_dca_slot_and_default_equals_old():
    bk = _book(seed=5); syms = list(bk.syms)
    cfg = _cfg(E.PRESETS['balanced']['sleeves'], syms)
    off, cv_off = B.run(bk, cfg, start=500.0, dca_enabled=False)
    no_dca, cv_no = B.run(bk, [c for c in cfg if c['key'] != 'dca_dip'], start=500.0)
    pd.testing.assert_frame_equal(off.reset_index(drop=True), no_dca.reset_index(drop=True))
    assert np.allclose(cv_off.values, cv_no.values)
    dflt, cv_d = B.run(bk, cfg, start=500.0)                          # research API default: the sleeves it is given
    on, cv_on = B.run(bk, cfg, start=500.0, dca_enabled=True)
    pd.testing.assert_frame_equal(dflt, on); assert np.allclose(cv_d.values, cv_on.values)


def test_lab_defaults_follow_the_flag_and_research_can_enable():
    import lab
    q = lab.validate_lab_request('monte_carlo', dict(preset='balanced'))
    assert q['run_options']['dca_enabled'] is False
    q = lab.validate_lab_request('monte_carlo', dict(preset='balanced', run_options=dict(dca_enabled=True)))
    assert q['run_options']['dca_enabled'] is True
    with pytest.raises(ValueError, match='dca_enabled'): lab.validate_lab_request('monte_carlo', dict(preset='balanced', run_options=dict(dca_enabled='yes')))
    bk = _book(n=700)
    res = lab.run_lab_job('liquidation', dict(preset='balanced', days=60, universe=list(bk.syms)), lambda q: bk)
    assert res['dca']['enabled'] is False and res['dca']['label'] == LABEL and res['dca']['paused_slots'] == ['DCA']


# ------------------------------------------------------------------ app API and panel
def _app(e, monkeypatch):
    import app as A
    monkeypatch.setattr(A, 'APP', types.SimpleNamespace(engine=e, preview=lambda: None, tg=None, cfg={}))
    return A


def test_settings_toggle_is_explicit_and_validated(monkeypatch):
    e, _ = mk_engine()
    try:
        A = _app(e, monkeypatch)
        for bad in ('true', 1, None):
            with pytest.raises(ValueError, match='DCA switch'): A.handle('/api/settings', {'DCA_ENABLED': bad})
        assert e.S['DCA_ENABLED'] is False
        A.handle('/api/settings', {'DCA_ENABLED': True}); assert e.S['DCA_ENABLED'] is True
        A.handle('/api/settings', {'DCA_ENABLED': False}); assert e.S['DCA_ENABLED'] is False
        with open(e.F['settings'], encoding='utf-8') as f: assert json.load(f)['DCA_ENABLED'] is False
    finally: _close(e)


def test_meta_presets_carry_the_label_and_dca_slots(monkeypatch):
    import app as A
    e, _ = mk_engine()
    try:
        ap = A.App.__new__(A.App); ap.engine = e
        m = ap.meta()
        assert m['dca'] == dict(enabled=False, label=LABEL)
        for k, p in m['presets'].items():
            want = [s['id'] for s in E.PRESETS[k]['sleeves'] if s['key'] == 'dca_dip']
            assert p['dca_slots'] == want, k
            assert p['dca_off'] == (LABEL if want else None), k
        assert m['presets']['active_dca']['dca_slots'] == ['DCA1H'] and m['presets']['original']['dca_off'] is None
        e.S['DCA_ENABLED'] = True
        assert all(p['dca_off'] is None for p in ap.meta()['presets'].values())
    finally: _close(e)


def test_backtest_and_lab_requests_follow_the_setting_unless_asked(monkeypatch):
    e, _ = mk_engine()
    try:
        A = _app(e, monkeypatch)
        got = []
        monkeypatch.setattr(A, 'enqueue', lambda fn, *a: got.append(a))
        sl = copy.deepcopy(E.PRESETS['calm']['sleeves'])
        A.handle('/api/backtest', dict(sleeves=sl)); assert got[-1][1]['run_options']['dca_enabled'] is False
        A.handle('/api/backtest', dict(sleeves=sl, run_options=dict(dca_enabled=True))); assert got[-1][1]['run_options']['dca_enabled'] is True
        e.S['DCA_ENABLED'] = True
        A.handle('/api/backtest', dict(sleeves=sl)); assert got[-1][1]['run_options']['dca_enabled'] is True
        e.S['DCA_ENABLED'] = False
        A.handle('/api/lab', dict(kind='monte_carlo', preset='balanced')); assert got[-1][2]['run_options']['dca_enabled'] is False
        A.handle('/api/lab', dict(kind='monte_carlo', preset='balanced', run_options=dict(dca_enabled=True)))
        assert got[-1][2]['run_options']['dca_enabled'] is True
        with pytest.raises(ValueError, match='run_options must be an object'): A.handle('/api/lab', dict(kind='monte_carlo', preset='balanced', run_options=[1]))
        for k in list(A.JOBS): A.JOBS.pop(k, None)
    finally: _close(e)


def _csv_candles(sym, tf, days):
    """Offline get_candles: the shipped data/*.csv candles (no network)."""
    d = pd.read_csv(os.path.join(ROOT, 'data' if tf == '4h' else 'data1h', f'{sym}_{tf}.csv'), parse_dates=['t'])
    return d.tail(int(days * 86400 / 14400) + 260).reset_index(drop=True)


def test_app_backtest_job_of_a_dca_profile_reports_the_label(monkeypatch):
    import app as A
    monkeypatch.setattr(A, 'APP', None)
    monkeypatch.setattr(A, 'get_candles', _csv_candles)
    res = {}
    for on in (False, True):
        jid = f'20260101-000000-dca{int(on)}'
        A.JOBS[jid] = dict(status='queued')
        A.run_backtest_job(jid, dict(name='dca', sleeves=copy.deepcopy(E.PRESETS['boost']['sleeves']), days=200, tf='4h', start=500,
                                     universe=list(E.CORE8), run_options=dict(dca_enabled=on)))
        j = A.JOBS.pop(jid); assert j['status'] == 'done', j
        res[on] = j['result']
    off, on = res[False], res[True]
    assert off['dca']['enabled'] is False and off['dca']['label'] == LABEL and off['dca']['paused_slots'] == ['DCA']
    assert off['dca']['paused_signals'] > 0 and 'dca_dip' not in {r['sleeve'] for r in off['by_sleeve']}
    assert on['dca'] == dict(enabled=True, label=None, paused_signals=0, paused_slots=[])
    assert 'dca_dip' in {r['sleeve'] for r in on['by_sleeve']}


def test_panel_shows_the_switch_label_and_research_option():
    with open(os.path.join(ROOT, 'panel.html'), encoding='utf-8') as f: h = f.read()
    for s in ('id="sw_dca"', 'setDca(this)', "setS({DCA_ENABLED:x.checked})", 'function dcaOff(p)', 'inactive (DCA off)',
              'id="ro_dca"', 'o.dca_enabled=true', 'DCA slots on (research)', 'META.dca.label', "sw('sw_dca',S.DCA_ENABLED===true)"):
        assert s in h, s
    assert 'confirm(\'Turn DCA back on?' in h                          # enabling needs an explicit confirmed toggle
