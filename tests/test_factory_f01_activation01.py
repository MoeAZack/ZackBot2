"""FACTORY-F01-ACT-01 (Codex ruling 6517762340): authenticated PAPER/testnet-only stage / preflight / enable of the C-F01
canary through app.handle -> f01_activation, under the engine lock and Engine.commit_settings.

Pins: the routes need the panel token; live mode and a non-testnet base are refused; a malformed config, a past start, a
re-stage (start never moves), enable without a stored PASS, enable after a FAIL / card change / altered extract, and enable
after the start passed are refused with settings and C-F01 unchanged; a failed save rolls back (memory + disk unchanged);
generic /api/settings and /api/sleeves cannot write the flag or switch C-F01 on; the happy path persists across a reload.
Synthetic data only (tests/test_safety.FakeX)."""
import copy
import io
import json
import os
import re
import shutil
import sys
import types
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_factory_cand01 import F01, F02, rot, slot                              # noqa: E402,F401
from test_factory_f01_impl01 import CD, MAN, NOW, SC, _cohort_dir, build, cfg, f01, iso, now_at   # noqa: E402,F401
import engine as E                                                                 # noqa: E402
import f01_canary as FC                                                            # noqa: E402
import f01_activation as FA                                                        # noqa: E402

START = NOW + timedelta(hours=2)


def body(**kw):
    c = cfg(start=START, **kw); c.pop('on'); return c


def recovery_body(old_hash, **kw):
    c = cfg(start=START + timedelta(hours=1), auth='6525193522', cohort_dir=CD[0] + '_recovered', **kw)
    c.pop('on')
    return dict(config_sha256=old_hash, **c)


@pytest.fixture
def api(f01, build, monkeypatch):
    """C-F01 present and DISABLED (stage (a) precondition), PAPER on testnet, the installed build matches the frozen one."""
    import app as A
    e, tmp = f01
    e.S.update(ENTRY_ORDER='market', MAX_LEVERAGE=10)
    slot(e, F01)['enabled'] = False
    e.save_settings()
    monkeypatch.setattr(A, 'APP', types.SimpleNamespace(engine=e, preview=lambda: None, cfg={}, tg=None))
    return A, e


def snap(e):
    return json.dumps(e.S, sort_keys=True, default=str), open(e.F['settings'], 'rb').read()


def refused(A, e, path, b, match):
    before = snap(e)
    with pytest.raises(ValueError, match=match):
        A.handle(path, b)
    assert snap(e) == before and slot(e, F01)['enabled'] is False


# ------------------------------------------------------------------ happy path
def test_stage_preflight_enable_happy_path_and_it_persists(api, monkeypatch):
    A, e = api
    out = A.handle('/api/f01_canary/stage', body())
    assert out['staged'] and e.S[FC.CANARY_KEY] == cfg(start=START) and slot(e, F01)['enabled'] is False
    pf = A.handle('/api/f01_canary/preflight', {})
    assert pf['pass'], {k: v for k, v in pf['record']['checks'].items() if not v['ok']}
    (rec,) = FC.ExtractWriter(CD[0]).records('preflight')
    assert pf['extract_sha256'] == FC.sha256(FC.canonical(rec)) and rec['pass'] is True
    assert e.S[FA.PREFLIGHT_KEY]['passed'] is True and slot(e, F01)['enabled'] is False   # still off until enable
    assert A.handle('/api/f01_canary/enable', {})['enabled'] and slot(e, F01)['enabled'] is True
    assert slot(e, F02)['enabled'] is False and e.S[FC.CANARY_KEY]['start_utc'] == iso(START)
    disk = json.load(open(e.F['settings'], encoding='utf-8'))
    assert disk[FC.CANARY_KEY] == cfg(start=START) and FA._slot(disk)['enabled'] is True   # committed durably
    assert e.f01_canary_block(slot(e, F01)) == 'F01 canary: before the frozen start - no new C-F01 entry'
    with pytest.raises(ValueError, match='already enabled'):
        A.handle('/api/f01_canary/enable', {})


# ------------------------------------------------------------------ auth (the panel token, as every POST route)
def _post(A, path, b, token):
    h = A.H.__new__(A.H)
    raw = json.dumps(b).encode()
    h.path, h.rfile, out = path, io.BytesIO(raw), []
    h.headers = {'Host': A.HOSTS[0], 'Content-Type': 'application/json', 'Content-Length': str(len(raw)),
                 **({'X-ZB-Token': token} if token is not None else {})}
    h._json = lambda obj, code=200: out.append((code, obj))
    h.do_POST()
    return out[0]


@pytest.mark.parametrize('token', [None, '', 'wrong-token'])
def test_routes_need_the_panel_token(api, token):
    A, e = api
    before = snap(e)
    for act in ('stage', 'recover_stage', 'preflight', 'enable'):
        code, obj = _post(A, f'/api/f01_canary/{act}', body(), token)
        assert code == 401 and obj['ok'] is False
    assert snap(e) == before and FC.CANARY_KEY not in e.S


def test_authenticated_post_stages(api):
    A, e = api
    code, obj = _post(A, '/api/f01_canary/stage', body(), A.TOKEN)
    assert code == 200 and obj['ok'] and e.S[FC.CANARY_KEY]['start_utc'] == iso(START)


# ------------------------------------------------------------------ one-time packaged-pins recovery (#254)
def _stage_for_recovery(A):
    return A.handle('/api/f01_canary/stage', body())['config_sha256']


def test_recover_stage_replaces_failed_stage_atomically_and_preserves_everything_else(api):
    A, e = api
    old_hash = _stage_for_recovery(A)
    old_cfg = copy.deepcopy(e.S[FC.CANARY_KEY])
    old_evidence = os.path.join(CD[0], 'old-evidence.json')
    os.makedirs(CD[0], exist_ok=True)
    with open(old_evidence, 'w', encoding='utf-8') as f: json.dump({'keep': True}, f)
    e.S[FA.PREFLIGHT_KEY] = {'passed': False, 'record_sha256': 'f' * 64}
    e.commit_settings(copy.deepcopy(e.S))
    untouched = copy.deepcopy((e.state, e.history, getattr(e, 'cohort_rows', None), e.S['SLEEVES'], e.S.get('ENTRIES_PAUSED')))

    out = A.handle('/api/f01_canary/recover_stage', recovery_body(old_hash))

    assert out['recovered'] and out['config_sha256'] == FA._sha(e.S[FC.CANARY_KEY]) != old_hash
    assert FA.PREFLIGHT_KEY not in e.S and slot(e, F01)['enabled'] is False
    assert os.path.exists(old_evidence) and json.load(open(old_evidence, encoding='utf-8')) == {'keep': True}
    assert (e.state, e.history, getattr(e, 'cohort_rows', None), e.S['SLEEVES'], e.S.get('ENTRIES_PAUSED')) == untouched
    audit = e.S[FA.RECOVERY_KEY][-1]
    assert audit == out['audit'] and audit['old_config_sha256'] == old_hash
    assert audit['new_config_sha256'] == out['config_sha256']
    assert audit['old_authorization_comment_id'] == old_cfg['authorization_comment_id']
    assert audit['new_authorization_comment_id'] == '6525193522'
    assert audit['reason'] == 'packaged_pins_missing' and FC._ts(audit['at_utc']) is not None
    disk = json.load(open(e.F['settings'], encoding='utf-8'))
    assert disk[FC.CANARY_KEY] == out['config'] and disk[FA.RECOVERY_KEY] == [audit]
    refused(A, e, '/api/f01_canary/recover_stage', recovery_body(old_hash), 'does not match')


@pytest.mark.parametrize('env', ['live', E.MAINNET, None, 'https://example.invalid'])
def test_recover_stage_is_paper_testnet_only(api, env):
    A, e = api
    old_hash = _stage_for_recovery(A)
    if env == 'live': e.live = True
    else: e.trade.base = env
    refused(A, e, '/api/f01_canary/recover_stage', recovery_body(old_hash),
            'PAPER/testnet only|not confirmed as Binance Futures testnet')


@pytest.mark.parametrize('change,match', [
    (lambda b: b.update(config_sha256='x'), 'exact 64-hex'),
    (lambda b: b.update(config_sha256='0' * 64), 'does not match'),
    (lambda b: b.update(extra=True), 'unknown'),
    (lambda b: b.update(start_utc='2026-11-01T09:00:00'), 'tz-aware'),
    (lambda b: b.update(start_utc=iso(NOW)), 'strictly in the future'),
    (lambda b: b.update(cohort_dir='relative'), 'absolute path'),
    (lambda b: b.update(cohort_dir=FC.REPO_ROOT), 'outside the repository'),
    (lambda b: b.update(source_commit='a' * 39), 'full 40-hex'),
    (lambda b: b.update(source_manifest={'engine.py': '0' * 64}), 'sha256 map'),
    (lambda b: b.update(authorization_comment_id=cfg()['authorization_comment_id']), 'new Codex authorization'),
    (lambda b: b.update(cohort_dir=CD[0]), 'new cohort directory'),
])
def test_recover_stage_rejects_bad_or_partial_replacement(api, change, match):
    A, e = api
    old_hash = _stage_for_recovery(A)
    b = recovery_body(old_hash); change(b)
    refused(A, e, '/api/f01_canary/recover_stage', b, match)


def test_recover_stage_refuses_pass_enabled_and_durable_ever_enabled_proof(api):
    A, e = api
    old_hash = _stage_for_recovery(A)
    e.S[FA.PREFLIGHT_KEY] = {'passed': True}; e.commit_settings(copy.deepcopy(e.S))
    refused(A, e, '/api/f01_canary/recover_stage', recovery_body(old_hash), 'PASS preflight')
    ns = copy.deepcopy(e.S); ns[FA.PREFLIGHT_KEY] = {'passed': False}; FA._slot(ns)['enabled'] = True; e.commit_settings(ns)
    before = snap(e)
    with pytest.raises(ValueError, match='is enabled'):
        A.handle('/api/f01_canary/recover_stage', recovery_body(old_hash))
    assert snap(e) == before and slot(e, F01)['enabled'] is True
    ns = copy.deepcopy(e.S); FA._slot(ns)['enabled'] = False; e.commit_settings(ns)
    e.state.setdefault('candidate_disabled', {})[F01] = {'why': 'guard disabled after enable'}
    refused(A, e, '/api/f01_canary/recover_stage', recovery_body(old_hash), 'ever enabled.*candidate_disabled')


def test_recover_stage_save_failure_restores_all_three_settings_subtrees(api, monkeypatch):
    A, e = api
    old_hash = _stage_for_recovery(A)
    e.S[FA.PREFLIGHT_KEY] = {'passed': False}; e.S[FA.RECOVERY_KEY] = [{'prior': True}]
    e.commit_settings(copy.deepcopy(e.S))
    before = snap(e), copy.deepcopy((e.S[FC.CANARY_KEY], e.S[FA.PREFLIGHT_KEY], e.S[FA.RECOVERY_KEY]))
    monkeypatch.setattr(e, '_save_safe', lambda *a, **k: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        A.handle('/api/f01_canary/recover_stage', recovery_body(old_hash))
    assert (snap(e), (e.S[FC.CANARY_KEY], e.S[FA.PREFLIGHT_KEY], e.S[FA.RECOVERY_KEY])) == before


def test_installer_recipe_builds_the_exact_f01_frozen_layout_and_every_input_fails_closed_if_changed(tmp_path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(root, 'installer.ps1'), encoding='utf-8-sig').read()
    # The simulated bundle is derived from the recipe itself: every literal $src Combine add-data source and destination.
    pat = re.compile(r"'--add-data', \(\[IO\.Path\]::Combine\(\$src,\s*((?:'[^']+'\s*,?\s*)+)\) \+ ';([^']+)'\)")
    entries = []
    for parts, dest in pat.findall(text):
        rel = os.path.join(*re.findall(r"'([^']+)'", parts)); entries.append((rel, dest))
    layout = tmp_path / 'bundle'
    for rel, dest in entries:
        src, out = os.path.join(root, rel), layout / dest / os.path.basename(rel)
        if os.path.isfile(src): out.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src, out)
    pins = FA.bundled_pins(str(layout))
    assert {k: v for k, v in FC.function_pins(pins, str(layout)).items() if not k.startswith('_')} == \
           {k: v for k, v in pins.items() if not k.startswith('_')}
    expected_manifest = FC.source_manifest(root)
    assert FC.source_manifest(str(layout)) == expected_manifest

    pins_path = layout / FA.PINS_FILE
    original_pins = pins_path.read_bytes()
    for victim in (FA.PINS_FILE, *FC.MANIFEST_FILES):
        path = layout / victim; original = path.read_bytes(); path.unlink()
        if victim == FA.PINS_FILE:
            with pytest.raises(ValueError, match='not bundled'): FA.bundled_pins(str(layout))
            path.parent.mkdir(parents=True, exist_ok=True)
            altered = json.loads(original)
            key = next(k for k in altered['pins_expected'] if not k.startswith('_'))
            altered['pins_expected'][key] = '0' * 64
            path.write_text(json.dumps(altered), encoding='utf-8')
        else:
            assert FC.identity_gap({'build_id': 'packaged', 'source_commit': SC, 'manifest': FC.source_manifest(str(layout))},
                                   cfg(commit=SC, manifest=expected_manifest)) == f'source_manifest_mismatch: {victim}'
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(original + b'\n# altered')
        if victim == FA.PINS_FILE:
            changed = FA.bundled_pins(str(layout))
            actual = FC.function_pins(changed, str(layout))
            assert actual[key] != changed[key]                         # preflight C9 fails closed on a valid-looking alteration
        else: assert victim in FC.identity_gap({'build_id': 'packaged', 'source_commit': SC,
                                                'manifest': FC.source_manifest(str(layout))},
                                               cfg(commit=SC, manifest=expected_manifest))
        path.write_bytes(original)
    assert pins_path.read_bytes() == original_pins and FC.source_manifest(str(layout)) == expected_manifest


# ------------------------------------------------------------------ environment
@pytest.mark.parametrize('act', ['stage', 'preflight', 'enable'])
@pytest.mark.parametrize('env', ['live', E.MAINNET, None, 'https://example.invalid'])
def test_live_or_non_testnet_is_refused(api, act, env):
    A, e = api
    if act != 'stage': A.handle('/api/f01_canary/stage', body())
    if act == 'enable': assert A.handle('/api/f01_canary/preflight', {})['pass']
    if env == 'live': e.live = True
    else: e.trade.base = env
    refused(A, e, f'/api/f01_canary/{act}', body(), 'PAPER/testnet only|not confirmed as Binance Futures testnet')


# ------------------------------------------------------------------ stage refusals
@pytest.mark.parametrize('b, match', [
    (body(auth='0123'), 'authorization id'),
    (body(auth=True), 'authorization id'),
    (body(cohort_dir='relative/dir'), 'cohort_dir'),
    (body(cohort_dir=FC.REPO_ROOT), 'cohort_dir'),
    (body(commit='abc'), 'source_commit'),
    (body(manifest={'engine.py': 'd' * 64}), 'source_manifest'),
    (dict(body(), start_utc='2026-11-01T02:00:00'), 'tz-aware'),               # naive
    (dict(body(), start_utc=1793500000), 'tz-aware ISO'),
    ({k: v for k, v in body().items() if k != 'cohort_dir'}, 'missing'),
    (dict(body(), extra=1), 'unknown F01 canary field'),
    (dict(body(), on=False), 'JSON boolean true'),
    (dict(body(), on='true'), 'JSON boolean true'),
    ([1], 'bad request'),
])
def test_malformed_config_is_refused(api, b, match):
    A, e = api
    refused(A, e, '/api/f01_canary/stage', b, match)
    assert FC.CANARY_KEY not in e.S


@pytest.mark.parametrize('start', [NOW, NOW - timedelta(minutes=1)])
def test_stage_with_a_start_not_in_the_future_is_refused(api, start):
    A, e = api
    refused(A, e, '/api/f01_canary/stage', dict(body(), start_utc=iso(start)), 'strictly in the future')


def test_restage_is_refused_and_the_start_never_moves(api):
    A, e = api
    A.handle('/api/f01_canary/stage', body())
    refused(A, e, '/api/f01_canary/stage', dict(body(), start_utc=iso(START + timedelta(days=1))), 'immutable')
    refused(A, e, '/api/f01_canary/stage', body(), 'immutable')                    # even the same config
    assert e.S[FC.CANARY_KEY]['start_utc'] == iso(START)


def test_stage_needs_c_f01_disabled(api):
    A, e = api
    slot(e, F01)['enabled'] = True
    with pytest.raises(ValueError, match='must be disabled'):
        A.handle('/api/f01_canary/stage', body())
    assert FC.CANARY_KEY not in e.S


def test_generic_settings_and_sleeves_cannot_write_the_flag_or_switch_c_f01_on(api):
    A, e = api
    for k in FA.OWNED_KEYS:
        refused(A, e, '/api/settings', {k: cfg(start=START)}, 'immutable once staged')
    A.handle('/api/f01_canary/stage', body())
    refused(A, e, '/api/settings', {FC.CANARY_KEY: {'on': False}}, 'immutable once staged')
    sl = [dict(x, enabled=True) if x['id'] == F01 else dict(x) for x in e.S['SLEEVES']]
    refused(A, e, '/api/sleeves', dict(sleeves=sl), 'only by /api/f01_canary/enable')


def test_sleeves_may_still_switch_c_f01_off(api):
    A, e = api
    A.handle('/api/f01_canary/stage', body())
    assert A.handle('/api/f01_canary/preflight', {})['pass']
    A.handle('/api/f01_canary/enable', {})
    card = copy.deepcopy(slot(e, F01))
    sl = [dict(x, enabled=False) if x['id'] == F01 else dict(x) for x in e.S['SLEEVES']]
    A.handle('/api/sleeves', dict(sleeves=sl))                                     # the documented rollback
    assert slot(e, F01) == dict(card, enabled=False)                               # every other field byte-identical
    assert FA._slot(json.load(open(e.F['settings'], encoding='utf-8'))) == dict(card, enabled=False)


# ------------------------------------------------------------------ the C-F01 card is frozen once staged (6518282236 #3)
def _post_card(e, **kw):
    return [dict(x, **kw) if x['id'] == F01 else dict(x) for x in e.S['SLEEVES']]


def _enabled(A):
    A.handle('/api/f01_canary/stage', body())
    assert A.handle('/api/f01_canary/preflight', {})['pass']
    A.handle('/api/f01_canary/enable', {})


@pytest.mark.parametrize('edit', [dict(risk=0.02), dict(max_pos=3), dict(share=0.1), dict(symbols=['BTCUSDT']),
                                  dict(mgmt={'stop_atr': 2.0}), dict(sides='both')])
def test_a_card_edit_while_staged_and_disabled_is_refused(api, edit):
    A, e = api
    A.handle('/api/f01_canary/stage', body())
    refused(A, e, '/api/sleeves', dict(sleeves=_post_card(e, **edit)), 'frozen|only by /api/f01_canary/enable')


@pytest.mark.parametrize('edit', [dict(risk=0.02), dict(max_pos=3), dict(symbols=['BTCUSDT'])])
def test_a_card_edit_while_enabled_is_refused(api, edit):
    A, e = api
    _enabled(A)
    before = snap(e)
    with pytest.raises(ValueError, match='frozen'):
        A.handle('/api/sleeves', dict(sleeves=_post_card(e, **edit)))
    assert snap(e) == before and slot(e, F01)['enabled'] is True


def test_a_rollback_that_also_changes_another_field_is_refused(api):
    A, e = api
    _enabled(A)
    before = snap(e)
    with pytest.raises(ValueError, match='frozen'):
        A.handle('/api/sleeves', dict(sleeves=_post_card(e, enabled=False, risk=0.02)))
    assert snap(e) == before and slot(e, F01)['enabled'] is True


def test_an_unchanged_c_f01_and_other_slot_edits_pass_while_staged(api):
    A, e = api
    A.handle('/api/f01_canary/stage', body())
    card = copy.deepcopy(slot(e, F01))
    other = next(x for x in e.S['SLEEVES'] if x['id'] not in (F01, F02) and x.get('enabled'))
    sl = [dict(x, risk=round(x['risk'] / 2, 6)) if x['id'] == other['id'] else dict(x) for x in e.S['SLEEVES']]
    A.handle('/api/sleeves', dict(sleeves=sl))
    assert slot(e, F01) == card and slot(e, other['id'])['risk'] == round(other['risk'] / 2, 6)


def test_before_staging_card_edits_stay_allowed_but_not_switching_on(api):
    A, e = api
    A.handle('/api/sleeves', dict(sleeves=_post_card(e, risk=0.02)))
    assert slot(e, F01)['risk'] == 0.02 and slot(e, F01)['enabled'] is False
    refused(A, e, '/api/sleeves', dict(sleeves=_post_card(e, enabled=True)), 'only by /api/f01_canary/enable')


# ------------------------------------------------------------------ enable re-runs the preflight (6518282236 #1)
def _f02_on(e, cur): slot(e, F02)['enabled'] = True
def _maker(e, cur): e.S['ENTRY_ORDER'] = 'maker'
def _cost_pending(e, cur): e.state['candidate_cost_pending'] = {'C-F01|BTCUSDT|LONG|1': {}}
def _guard_off(e, cur): e.state.setdefault('candidate_disabled', {})[F01] = 'stop failure'
def _provenance(e, cur): cur[0]['source_commit'] = 'e' * 40
def _family_lot(e, cur): e.state['lots']['f'] = dict(symbol='BTCUSDT', sleeve=F02, side='LONG')


@pytest.mark.parametrize('change', [_f02_on, _maker, _cost_pending, _guard_off, _provenance, _family_lot])
def test_enable_reruns_the_preflight_and_refuses_a_changed_dynamic_condition(api, build, change):
    A, e = api
    A.handle('/api/f01_canary/stage', body())
    pf = A.handle('/api/f01_canary/preflight', {})
    assert pf['pass']
    change(e, build)                                                               # after the stored PASS
    refused(A, e, '/api/f01_canary/enable', {}, 'fresh preflight fails')
    assert len(FC.ExtractWriter(CD[0]).records('preflight')) == 1                  # the re-check writes no second extract
    assert e.S[FA.PREFLIGHT_KEY]['extract_sha256'] == pf['extract_sha256']


def test_a_successful_enable_writes_no_second_extract(api):
    A, e = api
    _enabled(A)
    assert slot(e, F01)['enabled'] is True and len(FC.ExtractWriter(CD[0]).records('preflight')) == 1


# ------------------------------------------------------------------ preflight / enable refusals
def test_preflight_needs_a_staged_config(api):
    A, e = api
    refused(A, e, '/api/f01_canary/preflight', {}, 'not staged')
    refused(A, e, '/api/f01_canary/enable', {}, 'not staged')


def test_enable_without_a_preflight_is_refused(api):
    A, e = api
    A.handle('/api/f01_canary/stage', body())
    refused(A, e, '/api/f01_canary/enable', {}, 'no PASS preflight')


def test_a_failed_preflight_keeps_c_f01_disabled(api):
    A, e = api
    A.handle('/api/f01_canary/stage', body())
    e.S['ENTRY_ORDER'] = 'maker'                                                   # C8 fails
    pf = A.handle('/api/f01_canary/preflight', {})
    assert pf['pass'] is False and e.S[FA.PREFLIGHT_KEY]['passed'] is False
    refused(A, e, '/api/f01_canary/enable', {}, 'no PASS preflight')


def test_a_later_fail_replaces_an_earlier_pass(api):
    A, e = api
    A.handle('/api/f01_canary/stage', body())
    assert A.handle('/api/f01_canary/preflight', {})['pass']
    e.S['ENTRY_ORDER'] = 'maker'
    assert A.handle('/api/f01_canary/preflight', {})['pass'] is False
    refused(A, e, '/api/f01_canary/enable', {}, 'no PASS preflight')


def test_enable_after_the_start_passed_is_refused(api, monkeypatch):
    A, e = api
    A.handle('/api/f01_canary/stage', body())
    assert A.handle('/api/f01_canary/preflight', {})['pass']
    now_at(monkeypatch, START)
    refused(A, e, '/api/f01_canary/enable', {}, 'start_utc has passed')


def test_enable_refuses_a_changed_card_or_config_or_altered_extract(api):
    A, e = api
    A.handle('/api/f01_canary/stage', body())
    pf = A.handle('/api/f01_canary/preflight', {})
    assert pf['pass']
    slot(e, F01)['risk'] = 0.02                                                    # card edited after the PASS
    refused(A, e, '/api/f01_canary/enable', {}, 'card changed')
    slot(e, F01)['risk'] = 0.01
    good = copy.deepcopy(e.S[FC.CANARY_KEY])
    e.S[FC.CANARY_KEY]['authorization_comment_id'] = '6500000001'                   # config differs from the PASS
    refused(A, e, '/api/f01_canary/enable', {}, 'another config')
    e.S[FC.CANARY_KEY] = good
    p = os.path.join(CD[0], 'extracts', 'preflight', pf['extract_sha256'] + '.json')
    os.remove(p)
    refused(A, e, '/api/f01_canary/enable', {}, 'extract is missing or altered')


def test_preflight_refused_once_enabled_and_unknown_action(api):
    A, e = api
    A.handle('/api/f01_canary/stage', body())
    assert A.handle('/api/f01_canary/preflight', {})['pass']
    A.handle('/api/f01_canary/enable', {})
    with pytest.raises(ValueError, match='already enabled'):
        A.handle('/api/f01_canary/preflight', {})
    with pytest.raises(ValueError, match='unknown F01 canary action'):
        A.handle('/api/f01_canary/start', {})


def test_missing_bundled_pins_refuse_preflight(api, monkeypatch):
    A, e = api
    A.handle('/api/f01_canary/stage', body())
    monkeypatch.setattr(FA, 'PINS_FILE', os.path.join('contracts', 'nope.json'))
    refused(A, e, '/api/f01_canary/preflight', {}, 'not bundled')


# ------------------------------------------------------------------ rollback on save failure
@pytest.mark.parametrize('act', ['stage', 'preflight', 'enable'])
def test_a_failed_save_rolls_back(api, monkeypatch, act):
    A, e = api
    if act != 'stage': A.handle('/api/f01_canary/stage', body())
    if act == 'enable': assert A.handle('/api/f01_canary/preflight', {})['pass']
    before = snap(e)

    def boom(*a, **k): raise OSError('disk full')
    monkeypatch.setattr(e, '_save_safe', boom)
    with pytest.raises(OSError):
        A.handle(f'/api/f01_canary/{act}', body() if act == 'stage' else {})
    assert snap(e) == before and slot(e, F01)['enabled'] is False


# ------------------------------------------------------------------ no profile (preset) change while staged
@pytest.mark.parametrize('name', ['calm', E.ROTATION_PRESET])
@pytest.mark.parametrize('enabled', [False, True])
def test_preset_apply_is_refused_while_staged(api, name, enabled):
    A, e = api
    if enabled: _enabled(A)
    else: A.handle('/api/f01_canary/stage', body())
    before = snap(e)
    with pytest.raises(ValueError, match='refused while the F01 canary is staged'):
        A.handle('/api/preset', dict(name=name))
    assert snap(e) == before and slot(e, F01)['enabled'] is enabled
    with pytest.raises(ValueError, match='refused while the F01 canary is staged'):
        e.apply_preset(name)                                                       # the engine itself fails closed too
    assert snap(e) == before


def test_preset_apply_still_works_before_staging(api):
    A, e = api
    A.handle('/api/preset', dict(name='calm'))
    assert e.S['PRESET'] == 'calm' and FA._slot(e.S) is None


def test_the_governor_never_switches_the_profile_while_staged(api, monkeypatch):
    A, e = api
    A.handle('/api/f01_canary/stage', body())
    e.S['GOVERNOR'] = {'mode': 'auto', 'rules': [{'if': 'dd_gte', 'value': 10, 'then': {'profile': 'calm'}, 'until': None}]}
    e.equity(); e.state['peak_equity'] = (e.guard_eq or 500.0) * 2
    errs = []
    monkeypatch.setattr(e, 'err', lambda msg, key=None: errs.append(msg))
    sleeves, preset = copy.deepcopy(e.S['SLEEVES']), e.S['PRESET']
    e.governor_tick(); e.governor_tick()
    assert e.S['SLEEVES'] == sleeves and e.S['PRESET'] == preset
    assert len(errs) == 1 and 'refused while the F01 canary is staged' in errs[0]


def test_staged_with_a_missing_card_refuses_every_sleeves_edit(api):
    A, e = api
    A.handle('/api/f01_canary/stage', body())
    card = copy.deepcopy(slot(e, F01))
    e.S['SLEEVES'] = [x for x in e.S['SLEEVES'] if x['id'] != F01]                 # simulated missing stored card
    e.save_settings()
    others = [dict(x) for x in e.S['SLEEVES']]
    for posted in (others + [dict(card)],                                           # re-add the frozen card
                   others + [dict(card, enabled=True)],                             # add it switched on
                   others + [dict(card, risk=0.02)],                                # add a replacement card
                   others):                                                         # any other edit
        before = snap(e)
        with pytest.raises(ValueError, match='stored C-F01 card is missing'):
            A.handle('/api/sleeves', dict(sleeves=posted))
        assert snap(e) == before and FA._slot(e.S) is None
        assert FA._slot(json.load(open(e.F['settings'], encoding='utf-8'))) is None
