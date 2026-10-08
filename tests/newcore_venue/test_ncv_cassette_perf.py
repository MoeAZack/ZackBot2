"""Recorder cost stays linear-ish: each secret pattern is compiled ONCE per recorder (it dominated a 30-cycle TNET
recording: 4 minutes on fake HTTP), and older interactions are rescrubbed only when a newly learned value really
occurs in them - while a value first seen in a LATER answer is still removed from an earlier one."""
import json

import pytest

from newcore.venue import redact as R
from newcore.venue.cassette import CassetteRecorder
from newcore.venue.credentials import SecretScrubber
from newcore.venue.wire import HttpRequest, HttpResponse

URL = 'https://testnet.binancefuture.com/fapi/v1/order'


def req(i, extra=''):
    sig = '%064x' % (i + 7)
    return HttpRequest('GET', URL, f'symbol=SOLUSDT&origClientOrderId=c{i}{extra}&timestamp={i}&signature={sig}', (),
                       10.0, True)


def test_patterns_compile_once_per_value_and_signatures_do_not_trigger_rescrubs(monkeypatch):
    compiled = []
    real = R.value_pattern

    def counting(v):
        compiled.append(v)
        return real(v)
    monkeypatch.setattr(R, 'value_pattern', counting)
    rescrubs = []
    rec = CassetteRecorder(lambda r: HttpResponse(200, {}, b'{"status": "NEW"}'), redact=('SECRETVALUE123456',))
    real_rescrub = rec._rescrub
    monkeypatch.setattr(rec, '_rescrub', lambda obj, key=None: rescrubs.append(1) or real_rescrub(obj, key))
    for i in range(60):
        rec(req(i))
    assert len(compiled) == len(set(compiled))                 # never compiled twice
    assert len(set(compiled)) <= 61 + 1                         # the redact value + one per learned signature
    assert rescrubs == []                                       # a signature never occurs in an earlier interaction
    doc = json.loads(rec.to_json())
    assert len(doc['interactions']) == 60 and 'SECRETVALUE123456' not in rec.to_json()


def test_a_value_learned_later_is_still_removed_from_earlier_interactions():
    answers = iter([HttpResponse(200, {}, b'{"msg": "LATERSECRETabcdef1234"}'),
                    HttpResponse(200, {}, b'{"listenKey": "LATERSECRETabcdef1234"}')])
    rec = CassetteRecorder(lambda r: next(answers))
    rec(req(1))
    assert 'LATERSECRETabcdef1234' in json.dumps(rec.interactions)            # not known yet
    rec(req(2))                                                               # learned by name here
    text = rec.to_json()
    assert 'LATERSECRETabcdef1234' not in text and text.count('<redacted>') >= 2


def test_a_value_learned_later_is_removed_from_an_earlier_binary_body():
    answers = iter([HttpResponse(200, {}, b'\xff\xfe raw LATERSECRETabcdef1234 tail'),
                    HttpResponse(200, {}, b'{"listenKey": "LATERSECRETabcdef1234"}')])
    rec = CassetteRecorder(lambda r: next(answers))
    rec(req(1))
    assert 'body_b64' in rec.interactions[0]['response']
    rec(req(2))
    rec.to_json()                                              # the audit decodes the binary body: no leak left


def test_pattern_cache_is_owned_and_checks_values():
    c = R.PatternCache()
    assert c.get('ABCDEFGH12345678') is c.get('ABCDEFGH12345678')
    with pytest.raises(ValueError):
        c.get('short')
    assert 'ABCDEFGH' not in repr(c)


def test_scrubber_uses_its_own_cache(monkeypatch):
    compiled = []
    real = R.value_pattern
    monkeypatch.setattr(R, 'value_pattern', lambda v: compiled.append(v) or real(v))
    s = SecretScrubber()
    s.register('SCRUBMEabcdefgh1234')
    for _ in range(50):
        assert 'SCRUBME' not in s.scrub('x SCRUBMEabcdefgh1234 y')
    assert compiled == ['SCRUBMEabcdefgh1234']
