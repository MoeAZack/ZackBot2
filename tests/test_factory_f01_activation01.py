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
    for act in ('stage', 'preflight', 'enable'):
        code, obj = _post(A, f'/api/f01_canary/{act}', body(), token)
        assert code == 401 and obj['ok'] is False
    assert snap(e) == before and FC.CANARY_KEY not in e.S


def test_authenticated_post_stages(api):
    A, e = api
    code, obj = _post(A, '/api/f01_canary/stage', body(), A.TOKEN)
    assert code == 200 and obj['ok'] and e.S[FC.CANARY_KEY]['start_utc'] == iso(START)


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
    sl = [dict(x, enabled=False) if x['id'] == F01 else dict(x) for x in e.S['SLEEVES']]
    A.handle('/api/sleeves', dict(sleeves=sl))                                     # the documented rollback
    assert slot(e, F01)['enabled'] is False


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
