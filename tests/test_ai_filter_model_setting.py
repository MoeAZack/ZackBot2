"""AI_FILTER model id is settable through the normal /api/keys path and read back (non-secret) in /api/status."""
import os
import sys
import threading
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ai_filter  # noqa: E402
import app as A  # noqa: E402

SIG = dict(time='t', close=1.0, e20=1, e50=1, e200=1, atr=0.1, ret42=0.1, ret180=0.2, st_dir=1)


@pytest.fixture
def fake(monkeypatch):
    writes, starts = [], []
    eng = types.SimpleNamespace(state={'lots': []}, lock=threading.RLock())
    fapp = types.SimpleNamespace(engine=eng, cfg={'MODE': 'paper'}, lock=threading.RLock(),
                                 start_engine=lambda: starts.append(1))
    monkeypatch.setattr(A, 'APP', fapp)
    monkeypatch.setattr(A, 'write_cfg', lambda u: writes.append(dict(u)))
    return writes, starts


def test_model_is_written_and_engine_restarted(fake):
    writes, starts = fake
    assert A.handle('/api/keys', {'ANTHROPIC_MODEL': ' claude-sonnet-5-5 '}) == 'saved - engine restarted'
    assert writes == [{'ANTHROPIC_MODEL': 'claude-sonnet-5-5'}] and starts == [1]


def test_blank_model_resets_to_default(fake):
    writes, _ = fake
    A.handle('/api/keys', {'ANTHROPIC_MODEL': ''})
    assert writes == [{'ANTHROPIC_MODEL': ''}]
    assert ai_filter.DEFAULT_MODEL and isinstance(ai_filter.DEFAULT_MODEL, str)


@pytest.mark.parametrize('bad', ['claude sonnet', 'model/../x', 'a' * 81, 'm;rm', 'm\nMODE=live'])
def test_bad_model_refused_and_nothing_written(fake, bad):
    writes, starts = fake
    with pytest.raises(ValueError):
        A.handle('/api/keys', {'ANTHROPIC_MODEL': bad})
    assert writes == [] and starts == []


def test_absent_model_leaves_it_untouched(fake):
    writes, _ = fake
    A.handle('/api/keys', {'MODE': 'paper'})
    assert writes and all('ANTHROPIC_MODEL' not in w for w in writes)


def test_model_change_never_switches_mode(fake):
    writes, _ = fake
    A.handle('/api/keys', {'ANTHROPIC_MODEL': 'claude-sonnet-5-5'})
    assert all('MODE' not in w and 'LIVE_CONFIRM' not in w for w in writes)


def test_review_uses_configured_model_else_default(monkeypatch):
    seen = []

    class Boom(Exception):
        pass

    def post(url, json=None, **kw):
        seen.append(json['model'])
        raise Boom()
    monkeypatch.setattr(ai_filter.requests, 'post', post)
    for cfg, want in (({'ANTHROPIC_MODEL': 'claude-sonnet-5-5'}, 'claude-sonnet-5-5'), ({}, ai_filter.DEFAULT_MODEL),
                      ({'ANTHROPIC_MODEL': ''}, ai_filter.DEFAULT_MODEL)):
        dec, _, meta = ai_filter.review(dict(cfg, ANTHROPIC_API_KEY='k' * 12, AI_FILTER='on'), 'BTCUSDT', 'x LONG', SIG, [], 0.0)
        assert dec == 'take' and meta['model'] == want                      # fail-open unchanged
    assert seen == ['claude-sonnet-5-5', ai_filter.DEFAULT_MODEL, ai_filter.DEFAULT_MODEL]
