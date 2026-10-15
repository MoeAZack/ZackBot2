"""PAPER breadth_rotation per-key management overlay (Codex #13 choice (c)).

Applying the rotation from the owner's custom sleeves keeps the six experimental keys' profit-capture overrides
(be_r / tp1_r / tp1_frac / max_bars) as their breadth slot `mgmt`; every other slot stays defaults-only. Persisted as
settings ROTATION_MGMT_OVERLAY = {strategy_key: mgmt}. Synthetic only: FakeX engine, stubbed signals, no network."""
import copy, os, sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_safety import mk_engine                     # noqa: E402  (FakeX engine, PAPER)
import engine as E                                     # noqa: E402

OV = {'exp_trend': dict(be_r=1.0, tp1_r=1.5, tp1_frac=0.33),
      'exp_range': dict(be_r=.5, tp1_r=.5, tp1_frac=.5, max_bars=18),
      'exp_breakout': dict(be_r=1.0, tp1_r=1.25, tp1_frac=.5),
      'exp_scalp': dict(be_r=.75, tp1_r=.75, tp1_frac=.5),
      'exp_structure': dict(be_r=.75, tp1_r=1.0, tp1_frac=.5),
      'exp_vwaprev': dict(be_r=.5, tp1_r=.5, tp1_frac=.5, max_bars=18)}
SHAPE = ('id', 'key', 'name', 'enabled', 'share', 'risk', 'max_pos', 'symbols', 'sides', 'tf')
P = E.ROTATION_PERIOD_S
KEY = 'donchian_ens'                                   # no overlay; 4h, core 8, both sides
SID = E.breadth_id(KEY)


def custom(e):
    """The owner's current custom sleeves: the six experimental keys with their overrides, plus one plain slot."""
    sl = [E.sleeve(E.breadth_id(k), k, 1 / 8, .02, 4, 'core8', mgmt=dict(m)) for k, m in OV.items()]
    sl.append(E.sleeve('MOM', 'ema_mom', 1 / 8, .02, 4))
    e.S['SLEEVES'], e.S['PRESET'] = sl, 'custom'


def rot_engine(tmp_path, with_overlay=True):
    e, tmp = mk_engine(str(tmp_path))
    if with_overlay: custom(e)
    e.apply_preset(E.ROTATION_PRESET)
    return e, tmp


def by_key(e):
    return {x['key']: x for x in e.S['SLEEVES']}


def reload(tmp):
    return E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)


def test_bounds_are_app_clean_mgmt_bounds():
    import app as A
    assert E.ROTATION_MGMT_KEYS == {k: A.MG_NUM[k] for k in ('be_r', 'tp1_r', 'tp1_frac', 'max_bars')}


def test_preset_from_custom_carries_exactly_the_six_overrides(tmp_path):
    e, _ = rot_engine(tmp_path)
    assert e.S['PRESET'] == E.ROTATION_PRESET and e.S['ROTATION_MGMT_OVERLAY'] == OV
    assert {x['key']: x['mgmt'] for x in e.S['SLEEVES'] if x['mgmt']} == OV
    assert all(x['mgmt'] == {} for x in e.S['SLEEVES'] if x['key'] not in OV)
    sl = by_key(e)['exp_range']
    assert e.mgmt(sl)['max_bars'] == 18 and e.mgmt(sl)['tp1_frac'] == .5    # consumed via the normal merge_mgmt path


def test_slot_shape_identical_to_breadth_sleeves(tmp_path):
    e, _ = rot_engine(tmp_path)
    base = [{k: x[k] for k in SHAPE} for x in E.breadth_sleeves()]
    assert [{k: x[k] for k in SHAPE} for x in e.S['SLEEVES']] == base
    assert [{k: x[k] for k in SHAPE} for x in E.breadth_sleeves(overlay=OV)] == base


def test_named_profile_carries_no_overlay(tmp_path):
    e, _ = mk_engine(str(tmp_path)); e.apply_preset(E.ROTATION_PRESET)       # from 'original' (built-in pyramid mgmt)
    assert e.S['ROTATION_MGMT_OVERLAY'] == {} and all(x['mgmt'] == {} for x in e.S['SLEEVES'])


def test_overlay_survives_reload_and_rebuild(tmp_path):
    e, tmp = rot_engine(tmp_path)
    before = copy.deepcopy(e.S['SLEEVES'])
    e2 = reload(tmp)
    assert e2.S['ROTATION_MGMT_OVERLAY'] == OV and e2.S['SLEEVES'] == before
    e2.S.pop('SLEEVES'); e2.save_settings()                                  # settings-load rebuild path
    e3 = reload(tmp)
    assert e3.S['PRESET'] == E.ROTATION_PRESET
    assert {x['key']: x['mgmt'] for x in e3.S['SLEEVES'] if x['mgmt']} == OV
    assert [x['id'] for x in e3.S['SLEEVES']] == [x['id'] for x in before]
    e3.apply_preset(E.ROTATION_PRESET)                                       # re-applying keeps it
    assert e3.S['ROTATION_MGMT_OVERLAY'] == OV


@pytest.mark.parametrize('bad', [{'exp_trend': {'trail_atr': 2.0}}, {'exp_trend': {'pyramid': {'n': 1}}},
                                 {'exp_trend': {'tp1_frac': 1.5}}, {'exp_trend': {'be_r': 'x'}},
                                 {'exp_trend': {'max_bars': True}}, {'no_such_key': {'be_r': 1.0}}, {'exp_trend': 3}, []])
def test_invalid_or_unknown_mgmt_refused(tmp_path, bad):
    with pytest.raises(ValueError):
        E.clean_rotation_overlay(bad)
    e, _ = mk_engine(str(tmp_path))
    with pytest.raises(ValueError):
        e.apply_preset(E.ROTATION_PRESET, overlay=bad)
    assert e.S['PRESET'] != E.ROTATION_PRESET                               # nothing applied


def test_custom_sleeve_with_disallowed_key_refuses_the_preset(tmp_path):
    e, _ = mk_engine(str(tmp_path)); custom(e)
    e.S['SLEEVES'][0]['mgmt']['trail_atr'] = 2.0
    with pytest.raises(ValueError, match='not allowed'):
        e.apply_preset(E.ROTATION_PRESET)
    assert e.S['PRESET'] == 'custom'


def test_invalid_overlay_on_disk_falls_back_to_defaults_only(tmp_path):
    e, tmp = rot_engine(tmp_path)
    e.S['ROTATION_MGMT_OVERLAY'] = {'exp_trend': {'stop_atr': 9}}; e.S.pop('SLEEVES'); e.save_settings()
    e2 = reload(tmp)
    assert e2.S['ROTATION_MGMT_OVERLAY'] == {} and all(x['mgmt'] == {} for x in e2.S['SLEEVES'])


def test_live_mode_still_refused(tmp_path):
    e, _ = mk_engine(str(tmp_path)); custom(e)
    e.live = True
    with pytest.raises(ValueError, match='PAPER/testnet only'):
        e.apply_preset(E.ROTATION_PRESET)
    with pytest.raises(ValueError, match='PAPER/testnet only'):
        e.apply_preset(E.ROTATION_PRESET, overlay=OV)
    assert e.S['PRESET'] == 'custom'


def _entry(monkeypatch, tmp_path, with_overlay):
    t = next(i for i, g in enumerate(E.breadth_groups()) if KEY in g) * P + 3600
    real = E.rotation_at
    monkeypatch.setattr(E, 'rotation_at', lambda ts=None, **kw: real(t if ts is None else ts, **kw))
    e, _ = rot_engine(tmp_path, with_overlay)
    g = dict(close=100.0, atr=2.0, time='2026-10-04 00:00:00', le=True, se=False, lx=False, sx=False)
    e.compute_signals = lambda tf, syms, extra=(): ({f'{SID}|BTCUSDT': dict(g)} if tf == '4h' else {}, {})
    e.cycle('4h')
    monkeypatch.undo()
    keep = ('symbol', 'side', 'sleeve', 'key_strategy', 'qty', 'e0', 'sl', 'tp', 'R', 'mgmt', 'tf')
    return [{k: l.get(k) for k in keep} for l in e.state['lots'].values() if l['sleeve'] == SID]


def test_orders_identical_for_keys_without_overlay(monkeypatch, tmp_path):
    (tmp_path / 'a').mkdir(); (tmp_path / 'b').mkdir()
    a = _entry(monkeypatch, tmp_path / 'a', True)
    b = _entry(monkeypatch, tmp_path / 'b', False)
    assert len(a) == 1 and a == b


# ------------------------------------------------------------------ Cowork #117 findings
def test_switch_away_and_back_keeps_the_stored_overlay(tmp_path):
    e, tmp = rot_engine(tmp_path)
    e.apply_preset('original')                         # the governor auto-switch path: apply_preset(key), no overlay
    assert e.S['PRESET'] == 'original' and e.S['ROTATION_MGMT_OVERLAY'] == OV
    e.apply_preset(E.ROTATION_PRESET)                  # original -> rotation: falls back to the stored overlay
    assert e.S['ROTATION_MGMT_OVERLAY'] == OV
    assert {x['key']: x['mgmt'] for x in e.S['SLEEVES'] if x['mgmt']} == OV
    e2 = reload(tmp); e2.apply_preset('original'); e2.apply_preset(E.ROTATION_PRESET)   # across a restart too
    assert {x['key']: x['mgmt'] for x in e2.S['SLEEVES'] if x['mgmt']} == OV


def test_seventh_key_is_not_inherited(tmp_path, caplog):
    e, _ = mk_engine(str(tmp_path)); custom(e)
    e.S['SLEEVES'][-1]['mgmt'] = dict(be_r=1.0, tp1_r=2.0, tp1_frac=.5)       # ema_mom: allowed mgmt keys, not an exp key
    with caplog.at_level('WARNING'):
        e.apply_preset(E.ROTATION_PRESET)
    assert e.S['ROTATION_MGMT_OVERLAY'] == OV and by_key(e)['ema_mom']['mgmt'] == {}
    assert any('ema_mom' in r.getMessage() and 'not carried' in r.getMessage() for r in caplog.records)
    with pytest.raises(ValueError, match='experimental'):
        E.clean_rotation_overlay({'ema_mom': {'be_r': 1.0}})


def test_invalid_overlay_on_disk_is_surfaced_in_health(tmp_path):
    e, tmp = rot_engine(tmp_path)
    e.S['ROTATION_MGMT_OVERLAY'] = {'exp_trend': {'stop_atr': 9}}; e.save_settings()
    e2 = reload(tmp)
    assert 'integrity|rotation-overlay' in e2.health['incidents']
