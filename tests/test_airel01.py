"""AIREL-01: AI review reliability - fixed failure labels + non-secret telemetry, ONE bounded retry, a degraded circuit after
consecutive failures, and an explicit fail policy (PAPER fails open, a live mode fails closed). No prompt or P&L tuning."""
import json

import pandas as pd
import pytest

import ai_filter as A
import trade_audit as TA

SIG = dict(time='t', close=1.0, e20=1, e50=1, e200=1, atr=0.1, ret42=0.1, ret180=0.2, st_dir=1)
CFG = {'ANTHROPIC_API_KEY': 'sk-ant-TESTKEY-not-real-0123456789', 'AI_FILTER': 'on'}
OK = {'content': [{'type': 'text', 'text': '{"decision":"skip","reason":"stretched 3 ATR above EMA20"}'}], 'stop_reason': 'end_turn'}


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.content = json.dumps(body).encode()

    def json(self):
        return self._body


def text(t, stop='end_turn'):
    return Resp(200, {'content': [{'type': 'text', 'text': t}], 'stop_reason': stop})


def run(monkeypatch, *resps, cfg=CFG):
    calls = []

    def post(url, **kw):
        calls.append(kw.get('timeout'))
        r = resps[min(len(calls), len(resps)) - 1]
        if isinstance(r, Exception): raise r
        return r
    monkeypatch.setattr(A.requests, 'post', post)
    return A.review(cfg, 'BTCUSDT', 'X', SIG, [], 0), calls


# ------------------------------------------------------------------ labels + telemetry
@pytest.mark.parametrize('resp, label', [
    (text('```json\n{"decision":"skip","reason":"x"}\n```'), 'not_json'),
    (text('{"decision":"maybe","reason":"x"}'), 'schema_mismatch'),
    (text('{"decision":"take","reason":"x"}', stop='max_tokens'), 'incomplete_response'),
    (Resp(200, {'content': [{'type': 'thinking'}], 'stop_reason': 'end_turn'}), 'malformed_response'),
])
def test_unusable_200_answers_get_a_fixed_label_and_are_retried_once(monkeypatch, resp, label):
    (dec, why, meta), calls = run(monkeypatch, resp)
    assert dec == 'take' and 'taking trade by rules' in why and meta['failure'] == label
    assert meta['attempts'] == 2 and len(calls) == 2 and meta['http_status'] == 200 and meta['policy'] == 'open'
    h = A.health()
    assert h['failed'] == 1 and h['retries'] == 1 and h['consecutive'] == 1 and h['by_failure'] == {label: 1}


def test_stop_reason_block_types_and_response_hash_are_recorded_without_the_body(monkeypatch):
    secret = 'SECRET-REASON-TEXT-123'
    (dec, why, meta), _ = run(monkeypatch, text('{"decision":"skip","reason":"' + secret + '"}', stop='max_tokens'))
    assert meta['stop_reason'] == 'max_tokens' and meta['blocks'] == ['text']
    assert isinstance(meta['resp_sha'], str) and len(meta['resp_sha']) == 12
    assert secret not in json.dumps(meta) and secret not in why and 'sk-ant-TESTKEY' not in json.dumps(meta)


def test_retry_recovers_and_counts(monkeypatch):
    (dec, why, meta), calls = run(monkeypatch, text('not json'), Resp(200, OK))
    assert (dec, why) == ('skip', 'stretched 3 ATR above EMA20') and meta['attempts'] == 2
    assert 'failure' not in meta and 'error' not in meta and meta['recovered'] is True and meta['first_failure'] == 'not_json'
    assert [t['failure'] for t in meta['tries']] == ['not_json', None] and meta['stop_reason'] == 'end_turn'
    assert meta['resp_sha'] == meta['tries'][1]['resp_sha'] != meta['tries'][0]['resp_sha']    # the final fields are attempt 2's
    assert len(calls) == 2 and calls[1] <= A.BUDGET_S                    # the retry stays inside the one budget
    h = A.health()
    assert h['ok'] == 1 and h['failed'] == 0 and h['retries'] == 1 and h['consecutive'] == 0


@pytest.mark.parametrize('status, etype', [(401, 'authentication_error'), (400, 'invalid_request_error')])
def test_client_errors_are_not_retried(monkeypatch, status, etype):
    (dec, _, meta), calls = run(monkeypatch, Resp(status, {'type': 'error', 'error': {'type': etype, 'message': 'm'}}))
    assert dec == 'take' and len(calls) == 1 and meta['failure'] == 'http_error' and meta['api_error'] == etype


def test_overload_and_network_are_retried_once(monkeypatch):
    (_, _, meta), calls = run(monkeypatch, Resp(529, {'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'm'}}))
    assert len(calls) == 2 and meta['failure'] == 'http_error'
    (_, _, meta), calls = run(monkeypatch, A.requests.ConnectionError('x'))
    assert len(calls) == 2 and meta['failure'] == 'network' and 'http_status' not in meta


def test_no_retry_when_the_budget_is_nearly_spent(monkeypatch):
    clock = iter([0.0, 0.0, 55.0, 55.0, 55.0, 55.0, 55.0])
    monkeypatch.setattr(A.time, 'time', lambda: next(clock, 55.0))
    (_, _, meta), calls = run(monkeypatch, text('not json'))
    assert len(calls) == 1 and meta['attempts'] == 1


# ------------------------------------------------------------------ circuit
def test_consecutive_failures_open_the_circuit_and_skip_the_api(monkeypatch):
    for _ in range(A.CIRCUIT_N):
        run(monkeypatch, text('not json'))
    h = A.health()
    assert h['degraded'] is True and h['opened'] == 1 and h['consecutive'] == A.CIRCUIT_N
    (dec, why, meta), calls = run(monkeypatch, Resp(200, OK))
    assert calls == [] and dec == 'take' and meta['failure'] == 'circuit_open' and meta['degraded'] is True
    assert 'degraded: 3 consecutive failures' in why
    A._STATE['open_until'] = 0.0                                          # cooldown over
    (dec, _, meta), calls = run(monkeypatch, Resp(200, OK))
    assert len(calls) == 1 and dec == 'skip' and A.health()['consecutive'] == 0 and A.health()['degraded'] is False


def test_a_success_between_failures_resets_the_count(monkeypatch):
    run(monkeypatch, text('x')); run(monkeypatch, text('x')); run(monkeypatch, Resp(200, OK)); run(monkeypatch, text('x'))
    assert A.health()['consecutive'] == 1 and A.health()['degraded'] is False


# ------------------------------------------------------------------ policy
def test_fail_policy_defaults():
    assert A.fail_policy({}) == 'open' and A.fail_policy({'MODE': 'paper'}) == 'open'
    assert A.fail_policy({'MODE': 'live'}) == 'closed' and A.fail_policy({'MODE': 'LIVE '}) == 'closed'
    assert A.fail_policy({'MODE': 'live', 'AI_FAIL_POLICY': 'open'}) == 'open'
    assert A.fail_policy({'MODE': 'paper', 'AI_FAIL_POLICY': 'closed'}) == 'closed'
    assert A.fail_policy({'MODE': 'live', 'AI_FAIL_POLICY': 'bogus'}) == 'closed'


def test_fail_closed_refuses_with_its_own_reason_code(monkeypatch):
    (dec, why, meta), _ = run(monkeypatch, text('not json'), cfg=dict(CFG, AI_FAIL_POLICY='closed'))
    assert dec == 'skip' and why.startswith('AI review unavailable (') and meta['failed'] and meta['policy'] == 'closed'
    assert TA.reason_code(why) == ('filter', 'ai_unavailable')
    (dec, why, meta), _ = run(monkeypatch, Resp(200, OK), cfg=dict(CFG, AI_FAIL_POLICY='closed'))
    assert dec == 'skip' and 'failed' not in meta                          # a real veto stays a veto


def test_engine_fail_closed_is_not_logged_as_a_veto(monkeypatch):
    import test_safety as TS
    import engine as E
    e, _ = TS.mk_engine()
    e.S['AI_FILTER'] = True
    df = pd.DataFrame(dict(o=[100.0], h=[101.0], l=[99.0], c=[100.0], e20=[99.0], e50=[98.0], e200=[90.0], st=[1],
                           ret42=[0.1], ret180=[0.2]))
    monkeypatch.setattr(E, 'review', lambda *a, **k: ('skip', 'AI review unavailable (not_json) - fail-closed policy, entry refused',
                                                      {'failed': True, 'policy': 'closed'}))
    sl = E.sleeve('A', 'ema_st', 1.0, .01, 2, ['BTCUSDT'])
    n_open = e.trade.calls.count('open')
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', dict(close=100.0, atr=2.0, time='2026-01-01T00:00:00'), df, 500.0) is False
    assert e.last_skip.startswith('AI review unavailable') and e.trade.calls.count('open') == n_open
    assert not any(t.get('event') == 'ai_veto' for t in e.history)


def test_new_reason_code_is_registered():
    from newcore.domain.reasons import ReasonCode, MEANING
    assert ReasonCode('filter.ai_unavailable') in MEANING


def test_missing_key_follows_the_fail_policy(monkeypatch):
    monkeypatch.setattr(A.requests, 'post', lambda *a, **k: pytest.fail('must not call the API'))
    assert A.review({'AI_FILTER': 'on'}, 'BTCUSDT', 'X', SIG, [], 0) == ('take', 'AI filter off', {})
    dec, why, meta = A.review({'AI_FILTER': 'on', 'MODE': 'live'}, 'BTCUSDT', 'X', SIG, [], 0)
    assert dec == 'skip' and why.startswith('AI review unavailable (no API key)') and meta['failure'] == 'no_key'
    assert A.review({'AI_FILTER': 'off', 'MODE': 'live'}, 'BTCUSDT', 'X', SIG, [], 0)[0] == 'take'   # filter off stays off


def test_malformed_then_network_keeps_no_stale_response_fields(monkeypatch):
    """Codex 6263583505: the final record describes the final attempt only; attempt 1 lives in tries[0]."""
    (dec, why, meta), calls = run(monkeypatch, Resp(200, {'content': [{'type': 'thinking'}], 'stop_reason': 'end_turn'}),
                                  A.requests.ConnectionError('x'))
    assert dec == 'take' and len(calls) == 2 and meta['failure'] == 'network' and meta['error'] == 'ConnectionError'
    for k in ('http_status', 'api_error', 'resp_sha', 'blocks', 'stop_reason'):
        assert k not in meta, k
    assert meta['tries'][0]['failure'] == 'malformed_response' and meta['tries'][0]['http_status'] == 200
    assert len(meta['tries'][0]['resp_sha']) == 12 and meta['tries'][1] == {'failure': 'network'}
    assert meta['tries'][0]['blocks'] == ['thinking']                       # Codex 6263780286: block types kept per attempt
    assert 'recovered' not in meta


def test_single_success_has_one_clean_try(monkeypatch):
    (dec, _, meta), _ = run(monkeypatch, Resp(200, OK))
    assert dec == 'skip' and meta['attempts'] == 1 and 'failure' not in meta and 'recovered' not in meta
    assert meta['tries'] == [{'http_status': 200, 'stop_reason': 'end_turn', 'blocks': ['text'], 'resp_sha': meta['resp_sha'],
                              'failure': None}]
