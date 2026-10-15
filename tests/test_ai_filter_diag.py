"""AI_FILTER diagnostic (Codex 6202310326): a failed review records only the HTTP status and the API's error.type - never the
response body, headers, key or account ids - and still fails open to the rules."""
import json

import pytest

import ai_filter as A

SIG = dict(time='t', close=1.0, e20=1, e50=1, e200=1, atr=0.1, ret42=0.1, ret180=0.2, st_dir=1)
CFG = {'ANTHROPIC_API_KEY': 'sk-ant-TESTKEY-not-real-0123456789', 'AI_FILTER': 'on'}
SECRETS = ('sk-ant-TESTKEY', 'org_SECRET123', 'req_SECRET456', 'acct-9876543210', 'Your credit balance')


class Resp:
    def __init__(self, status, body, raw=None):
        self.status_code, self._body, self._raw = status, body, raw

    def json(self):
        if self._raw is not None:
            return json.loads(self._raw)
        return self._body


def run(monkeypatch, resp):
    seen = {}

    def post(url, **kw):
        seen.update(kw)
        if isinstance(resp, Exception):
            raise resp
        return resp
    monkeypatch.setattr(A.requests, 'post', post)
    return A.review(CFG, 'BTCUSDT', 'X', SIG, [], 0)


def leaks(*parts):
    blob = json.dumps(parts, default=str)
    return [s for s in SECRETS if s in blob]


def err(t, msg='Your credit balance is too low; org_SECRET123 req_SECRET456 acct-9876543210'):
    return {'type': 'error', 'error': {'type': t, 'message': msg}, 'request_id': 'req_SECRET456'}


def test_success_unchanged(monkeypatch):
    body = {'content': [{'type': 'text', 'text': '{"decision": "skip", "reason": "stretched 4 ATR above EMA20"}'}]}
    dec, why, meta = run(monkeypatch, Resp(200, body))
    assert (dec, why) == ('skip', 'stretched 4 ATR above EMA20')
    assert meta['http_status'] == 200 and 'api_error' not in meta and 'error' not in meta


@pytest.mark.parametrize('status,etype', [(401, 'authentication_error'), (403, 'permission_error'), (404, 'not_found_error'),
                                          (400, 'invalid_request_error'), (429, 'rate_limit_error'), (529, 'overloaded_error'),
                                          (500, 'api_error')])
def test_api_error_records_status_and_type_only(monkeypatch, status, etype):
    dec, why, meta = run(monkeypatch, Resp(status, err(etype)))
    assert dec == 'take' and 'taking trade by rules' in why                  # fail-open unchanged
    assert meta['http_status'] == status and meta['api_error'] == etype
    assert f'http {status} {etype}' in why
    assert not leaks(why, meta)                                              # no message / request id / account id / key


def test_billing_message_text_never_logged(monkeypatch):
    dec, why, meta = run(monkeypatch, Resp(400, err('invalid_request_error')))
    assert 'credit' not in why.lower() and 'credit' not in json.dumps(meta).lower()


def test_unusual_error_type_is_replaced_by_fixed_label(monkeypatch):
    dec, why, meta = run(monkeypatch, Resp(400, err('Bad Thing: sk-ant-TESTKEY org_SECRET123')))
    assert meta['api_error'] == 'api_error' and not leaks(why, meta)


@pytest.mark.parametrize('resp', [Resp(200, None, raw='<html>gateway timeout</html>'), Resp(200, ['x']),
                                  Resp(200, {'content': []}), Resp(200, {'content': [{'type': 'tool_use'}]}),
                                  Resp(502, None, raw='upstream')])
def test_malformed_response(monkeypatch, resp):
    dec, why, meta = run(monkeypatch, resp)
    assert dec == 'take' and meta['api_error'] == 'malformed_response' and meta['http_status'] == resp.status_code


def test_network_failure_has_no_status(monkeypatch):
    dec, why, meta = run(monkeypatch, A.requests.ConnectionError('dns fail for acct-9876543210'))
    assert dec == 'take' and 'http_status' not in meta and meta['error'] == 'ConnectionError'
    assert 'ConnectionError' in why and not leaks(why, meta)


def test_unparseable_model_text_still_fails_open(monkeypatch):
    dec, why, meta = run(monkeypatch, Resp(200, {'content': [{'type': 'text', 'text': 'no json here'}]}))
    assert dec == 'take' and meta['http_status'] == 200 and 'api_error' not in meta and meta['error']


def test_no_key_or_off_never_calls(monkeypatch):
    monkeypatch.setattr(A.requests, 'post', lambda *a, **k: pytest.fail('must not call the API'))
    assert A.review({}, 'BTCUSDT', 'X', SIG, [], 0)[0] == 'take'
    assert A.review(dict(CFG, AI_FILTER='off'), 'BTCUSDT', 'X', SIG, [], 0)[0] == 'take'
