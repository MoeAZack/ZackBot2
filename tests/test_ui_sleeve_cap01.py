"""UI-SLEEVE-CAP-01 (Codex 6218761375): /api/sleeves cap follows the registry; under the breadth rotation the panel may
edit slots but never the slot set, shares are checked per rotation group (SCALP its own group) and the preset stays."""
import copy
import json
import os
import sys
import threading
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app as A  # noqa: E402
import engine as ENG  # noqa: E402


def _eng(sleeves, preset, tradfi=False, grids=()):
    saves = []
    e = types.SimpleNamespace(S={'SLEEVES': sleeves, 'PRESET': preset, 'GRID_SLOTS': list(grids)}, lock=threading.RLock(),
                              tradfi_on=lambda: tradfi, state={'lots': []})
    e.save_settings = lambda: saves.append(json.dumps(e.S, sort_keys=True))
    return e, saves


@pytest.fixture
def app(monkeypatch):
    previews = []
    fapp = types.SimpleNamespace(engine=None, preview=lambda: previews.append(1), lock=threading.RLock(), cfg={'MODE': 'paper'})
    monkeypatch.setattr(A, 'APP', fapp)

    def use(e):
        fapp.engine = e
        return fapp
    return use


def _rotation(scalp_on=True, tradfi=False):
    sl = ENG.breadth_sleeves(tradfi=tradfi)
    for x in sl:
        if x['key'] in ENG.SCALP_KEYS: x['enabled'] = scalp_on        # the running cohort enabled all four scalp slots
    return sl


def _post(sl):
    return A.handle('/api/sleeves', {'sleeves': json.loads(json.dumps(sl))})     # what the panel sends: JSON round-trip


def test_cap_follows_the_registry_and_is_above_the_old_12():
    assert A.MAX_SLEEVES == max(12, len(ENG.breadth_sleeves(tradfi=True)))
    assert A.MAX_SLEEVES >= len(ENG.breadth_sleeves()) > 12


def test_unchanged_rotation_round_trip_saves_and_keeps_the_rotation(app):
    sl = _rotation()
    e, saves = _eng(copy.deepcopy(sl), ENG.ROTATION_PRESET)
    app(e)
    assert _post(sl) == 'strategies saved'
    assert e.S['PRESET'] == ENG.ROTATION_PRESET and len(saves) == 1
    assert json.dumps(e.S['SLEEVES'], sort_keys=True) == json.dumps(sl, sort_keys=True)


def test_rotation_round_trip_leaves_every_scalp_slot_byte_identical(app):
    """Cowork's invariant: enabled flag and risk fields of every SCALP slot are byte-identical after an unchanged save."""
    sl = _rotation()
    e, _ = _eng(copy.deepcopy(sl), ENG.ROTATION_PRESET)
    app(e)
    before = {x['id']: json.dumps(x, sort_keys=True) for x in sl if x['key'] in ENG.SCALP_KEYS}
    _post(sl)
    after = {x['id']: json.dumps(x, sort_keys=True) for x in e.S['SLEEVES'] if x['key'] in ENG.SCALP_KEYS}
    assert before == after and set(before) == set(ENG.SCALP_SLOTS)
    for x in e.S['SLEEVES']:
        if x['key'] in ENG.SCALP_KEYS:
            assert x['enabled'] is True and x['risk'] == ENG.SCALP_RISK and x['max_pos'] == ENG.SCALP_MAX_POS


def test_rotation_round_trip_with_tradfi_slots(app):
    sl = _rotation(tradfi=True)
    e, _ = _eng(copy.deepcopy(sl), ENG.ROTATION_PRESET, tradfi=True)
    app(e)
    assert _post(sl) == 'strategies saved' and e.S['PRESET'] == ENG.ROTATION_PRESET


def test_rotation_slot_edit_is_saved_and_the_preset_stays(app):
    sl = _rotation()
    e, _ = _eng(copy.deepcopy(sl), ENG.ROTATION_PRESET)
    app(e)
    sl[0]['enabled'] = False; sl[1]['risk'] = 0.002
    _post(sl)
    assert e.S['PRESET'] == ENG.ROTATION_PRESET
    assert e.S['SLEEVES'][0]['enabled'] is False and e.S['SLEEVES'][1]['risk'] == 0.002


@pytest.mark.parametrize('mut', ['add', 'remove', 'rekey', 'rename'])
def test_rotation_slot_set_change_is_refused_and_nothing_saved(app, mut):
    sl = _rotation()
    e, saves = _eng(copy.deepcopy(sl), ENG.ROTATION_PRESET)
    app(e)
    before = json.dumps(e.S, sort_keys=True)
    rot = [x for x in sl if x['key'] not in ENG.SCALP_KEYS]
    if mut == 'add': sl.append(dict(rot[0], id='EXTRA', enabled=False))
    elif mut == 'remove': sl.pop(0)
    elif mut == 'rekey': sl[0]['key'] = next(k for k in A.S.STRATEGIES if k != sl[0]['key'] and k not in ENG.SCALP_KEYS)
    else: sl[0]['id'] = 'RENAMED'
    with pytest.raises(ValueError, match='rotation keeps its slot set'):
        _post(sl)
    assert json.dumps(e.S, sort_keys=True) == before and saves == []


def test_rotation_group_over_100_percent_is_refused(app):
    sl = _rotation()
    e, saves = _eng(copy.deepcopy(sl), ENG.ROTATION_PRESET)
    app(e)
    grp = A.rotation_share_groups(e.S['SLEEVES'])
    g0 = [x for x in sl if grp.get(x['id']) == 0]
    g0[0]['share'] = 1.0                                   # group 0 now > 100% while every other group is untouched
    with pytest.raises(ValueError, match='rotation group'):
        _post(sl)
    assert saves == [] and e.S['PRESET'] == ENG.ROTATION_PRESET


def test_scalp_slots_are_their_own_share_group(app):
    sl = _rotation()
    e, saves = _eng(copy.deepcopy(sl), ENG.ROTATION_PRESET)
    app(e)
    for x in sl:
        if x['key'] in ENG.SCALP_KEYS: x['share'] = 0.3      # 4 x 0.3 = 120% inside the SCALP group only
    with pytest.raises(ValueError, match='rotation group'):
        _post(sl)
    assert saves == []
    assert all(A.rotation_share_groups(e.S['SLEEVES']).get(i) is None for i in ENG.SCALP_SLOTS)


def _custom(n, share):
    keys = [k for k in A.S.STRATEGIES]
    return [dict(id=f'C{i}', key=keys[i % len(keys)], share=share, risk=0.005, max_pos=1, enabled=True) for i in range(n)]


def test_custom_set_above_12_up_to_the_cap_is_saved(app):
    e, saves = _eng([], 'custom')
    app(e)
    n = A.MAX_SLEEVES
    assert _post(_custom(n, 1 / n - 1e-6)) == 'strategies saved'
    assert len(e.S['SLEEVES']) == n and e.S['PRESET'] == 'custom' and len(saves) == 1


def test_set_over_the_cap_is_refused(app):
    e, saves = _eng([], 'custom')
    app(e)
    with pytest.raises(ValueError, match=f'1-{A.MAX_SLEEVES} strategy slots'):
        _post(_custom(A.MAX_SLEEVES + 1, 0.01))
    assert saves == [] and e.S['SLEEVES'] == []


def test_set_over_the_cap_is_refused_under_rotation_too(app):
    sl = _rotation()
    e, saves = _eng(copy.deepcopy(sl), ENG.ROTATION_PRESET)
    app(e)
    big = sl + [dict(sl[0], id=f'X{i}') for i in range(A.MAX_SLEEVES + 1 - len(sl))]
    with pytest.raises(ValueError, match='strategy slots'):
        _post(big)
    assert saves == []


def test_custom_total_share_guard_unchanged(app):
    e, saves = _eng([], 'custom')
    app(e)
    with pytest.raises(ValueError, match='more than 100%'):
        _post(_custom(13, 0.1))
    assert saves == []


def test_custom_grid_shares_still_count(app):
    e, saves = _eng([], 'custom', grids=[dict(enabled=True, share=0.5)])
    app(e)
    with pytest.raises(ValueError, match='and grids'):
        _post(_custom(2, 0.3))
    assert saves == []


def test_custom_preset_never_becomes_the_rotation(app):
    sl = _rotation()
    e, _ = _eng([], 'balanced')
    app(e)
    with pytest.raises(ValueError, match='more than 100%'):        # the rotation set is NOT a valid custom set (shares 300%)
        _post(sl)
    assert e.S['PRESET'] == 'balanced'


def test_backtest_slice_uses_the_same_cap(app, monkeypatch):
    e, _ = _eng([], 'custom', )
    e.S['UNIVERSE'] = ['BTCUSDT']
    app(e)
    seen = {}
    monkeypatch.setattr(A, 'enqueue', lambda fn, jid, req: seen.update(req))
    A.handle('/api/backtest', {'sleeves': _custom(A.MAX_SLEEVES + 3, 0.01), 'universe': ['BTCUSDT']})
    assert len(seen['sleeves']) == A.MAX_SLEEVES


@pytest.mark.parametrize('mut', ['duplicate_id', 'unknown_key'])
def test_rotation_invalid_slot_is_refused_and_nothing_saved(app, mut):
    sl = _rotation()
    e, saves = _eng(copy.deepcopy(sl), ENG.ROTATION_PRESET)
    app(e)
    before = json.dumps(e.S, sort_keys=True)
    if mut == 'duplicate_id': sl[1]['id'] = sl[0]['id']
    else: sl[0]['key'] = 'no_such_strategy'
    with pytest.raises(ValueError):
        _post(sl)
    assert json.dumps(e.S, sort_keys=True) == before and saves == []


def test_rotation_round_trip_keeps_saved_scalp_state_not_registry_defaults(app):
    """Cowork: the registry ships the SCALP slots OFF; the running cohort has them ON in saved settings. A save compares
    and keeps the SAVED values - it never re-seeds from the registry."""
    assert all(x['enabled'] is False for x in ENG.scalp_sleeves())
    sl = _rotation(scalp_on=True)
    e, _ = _eng(copy.deepcopy(sl), ENG.ROTATION_PRESET)
    app(e)
    _post(sl)
    assert [x['id'] for x in e.S['SLEEVES']] == [x['id'] for x in sl]
    assert all(x['enabled'] is True for x in e.S['SLEEVES'] if x['key'] in ENG.SCALP_KEYS)


@pytest.mark.parametrize('scalp_on', [False, True])
def test_stored_tradfi_slots_with_tradfi_now_off_still_round_trip(app, scalp_on):
    """Review F1: TradFi slots stay stored when TradFi goes off (or in live, where tradfi_on() is False) and keep the
    TradFi group shares; the share groups follow the STORED set, so an unchanged save is not refused."""
    sl = _rotation(scalp_on=scalp_on, tradfi=True)
    e, saves = _eng(copy.deepcopy(sl), ENG.ROTATION_PRESET, tradfi=False)
    app(e)
    assert _post(sl) == 'strategies saved' and len(saves) == 1 and e.S['PRESET'] == ENG.ROTATION_PRESET
    assert json.dumps(e.S['SLEEVES'], sort_keys=True) == json.dumps(sl, sort_keys=True)
