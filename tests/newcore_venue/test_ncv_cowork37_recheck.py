"""Cowork re-check (PR #37, before the owner run): NEW-S1, NEW-R2a, NEW-C1, NEW-C2, N2, NEW-R3, mark freshness.
Repro first; fake HTTP, DUMMY keys."""
import json
import os
import signal
from decimal import Decimal as D

import pytest

from fake_binance import FakeBinance
from ncv_support import DUMMY_KEY, DUMMY_SECRET, FakeHttp, NOW_MS
from test_ncv_cassette_leaks import ok, req
from test_ncv_tnet_harness import env, run, tool  # noqa: F401  (env is a fixture)
from test_ncv_venue_reads import premium, reader
from test_ncv_venue_reads import ok as rok

from newcore.ports import venue as P
from newcore.venue.cassette import MAX_DECODED_TOKENS, CassetteLeak, CassetteRecorder

STOP = 'zbn1o-' + 'b' * 26


# ---------------------------------------------------------------------------------------------- NEW-S1
@pytest.mark.parametrize('body', [b'{"msg": "x\\ud800y"}', b'{"\\ud800": "v"}'])
def test_s1_a_lone_surrogate_is_a_typed_leak_and_leaves_no_tmp_file(tmp_path, body):
    rec = CassetteRecorder(FakeHttp(ok(body)))
    rec(req())
    with pytest.raises(CassetteLeak, match='not encodable'):
        rec.save(str(tmp_path / 'c.json'))
    assert os.listdir(tmp_path) == []


def test_s1_cli_writes_the_report_and_exits_5(env):  # noqa: F811
    class Surrogate(FakeBinance):
        def _get_fapi_v1_time(self, q):
            from newcore.venue.wire import HttpResponse
            return HttpResponse(200, {}, ('{"serverTime": %d, "msg": "\\ud800"}' % self.now).encode())
    rc, out = run(env, ['--probe', 'P1'], http=Surrogate())
    assert rc == 5 and 'cassette not written' in out and 'report: ' in out
    assert not [n for n in os.listdir(env['cassettes']) if '.tmp' in n]


# ---------------------------------------------------------------------------------------------- NEW-R2a
def test_r2a_a_secret_after_the_decode_cap_is_never_stored():
    junk = ' '.join('QUJDREVGR0hJSktMTU5PUA' for _ in range(MAX_DECODED_TOKENS + 5))
    import base64
    hidden = base64.b64encode(DUMMY_SECRET.encode()).decode()
    rec = CassetteRecorder(FakeHttp(ok(json.dumps({'msg': junk + ' ' + hidden}))), redact=(DUMMY_KEY, DUMMY_SECRET))
    rec(req())
    with pytest.raises(CassetteLeak):
        rec.to_json()


# ---------------------------------------------------------------------------------------------- NEW-C1 / C2
def stranded_with_foreign(foreign='2', leftover='1'):
    fb = FakeBinance()
    q = D(foreign) + D(leftover)
    fb.pos[('SOLUSDT', 'LONG')] = q
    fb.visible_pos[('SOLUSDT', 'LONG')] = q
    fb._rest_classic(STOP, 'SOLUSDT', 'SELL', 'LONG', D(leftover), D('150'))
    return fb


def test_c1_cleanup_never_keeps_a_newcore_leftover_as_foreign(env):  # noqa: F811
    fb = stranded_with_foreign()
    rc, out = run(env, ['--cleanup', '--close-positions', '--adopt-foreign', 'SOLUSDT:LONG'], http=fb)
    assert rc == 2 and 'SYMBOL:SIDE:QTY' in out and fb.requests == []           # refused: no quantity declared
    rc, out = run(env, ['--cleanup', '--close-positions', '--adopt-foreign', 'SOLUSDT:LONG:2'], http=fb)
    assert rc == 0 and fb.pos[('SOLUSDT', 'LONG')] == D('2') and '1 will be closed, 2 kept as foreign' in out


def test_c1_preflight_with_a_declared_foreign_quantity_refuses_more(env):  # noqa: F811
    fb = stranded_with_foreign()
    fb.orders.clear()
    rc, out = run(env, ['--probe', 'P1', '--adopt-foreign', 'SOLUSDT:LONG:2'], http=fb)
    assert rc == 4 and 'not_flat:SOLUSDT:LONG:3>2' in out


def test_c2_ctrl_c_during_the_cleanup_cannot_abort_it(env):  # noqa: F811
    class CtrlCOnce(FakeBinance):
        def _delete_fapi_v1_order(self, q):
            if not getattr(self, 'fired', False):
                self.fired = True
                signal.raise_signal(signal.SIGINT)                # ignored inside guarded's teardown
            return super()._delete_fapi_v1_order(q)
    fb = CtrlCOnce()
    fb._rest_classic(STOP, 'SOLUSDT', 'SELL', 'LONG', D('1'), D('150'))
    rc, out = run(env, ['--cleanup'], http=fb)
    assert rc == 0 and fb.orders[STOP]['status'] == 'CANCELED'


# ---------------------------------------------------------------------------------------------- N2
def test_n2_ctrl_c_in_the_preflight_is_typed(env):  # noqa: F811
    class CtrlCPreflight(FakeBinance):
        def _get_fapi_v1_positionSide_dual(self, q):
            raise KeyboardInterrupt
    fb = CtrlCPreflight()
    rc, out = run(env, ['--probe', 'P1'], http=fb)
    assert rc == 6 and 'INTERRUPTED' in out and not [q for q in fb.requests if q.method in ('POST', 'DELETE')]


def test_n2_ctrl_c_in_the_report_phase_is_typed(env, monkeypatch):  # noqa: F811
    mod = tool()

    def interrupt(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(mod, 'tnet_report', interrupt)
    fb = FakeBinance()
    rc, out = run(env, ['--probe', 'P1'], http=fb, mod=mod)
    assert rc == 6 and 'INTERRUPTED' in out and fb.flat()


# ---------------------------------------------------------------------------------------------- NEW-R3
def test_r3_a_key_like_value_in_a_known_header_is_redacted():
    token = 'K' * 40
    rec = CassetteRecorder(FakeHttp(ok('{}', {'Retry-After': token, 'X-MBX-USED-WEIGHT-1M': '3'})))
    rec(req())
    it = json.loads(rec.to_json())['interactions'][0]
    assert it['response']['headers'] == {'Retry-After': '<redacted>', 'X-MBX-USED-WEIGHT-1M': '3'}


# ---------------------------------------------------------------------------------------------- mark freshness
@pytest.mark.parametrize('t,detail', [(1, 'stale_mark'), (5 * 10 ** 18, 'future_mark')])
def test_mark_times_far_from_the_server_clock_are_unknown(t, detail):
    r, _ = reader(rok(premium(t=t)))
    out = r.mark_price('SOLUSDT')
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == detail
    assert NOW_MS


# ---------------------------------------------------------------------------------------------- NEW-P1
@pytest.mark.parametrize('body', [
    '&'.join(f'token{i}=v{i}abcdefgh' for i in range(300000)),
    json.dumps({'rows': [{'secret': f'v{i}abcdefgh'} for i in range(200000)]}),
], ids=['form', 'json'])
def test_p1_a_body_teaching_too_many_secrets_fails_closed_fast(body):
    import time
    t0 = time.monotonic()
    rec = CassetteRecorder(FakeHttp(ok(body)), redact=(DUMMY_KEY, DUMMY_SECRET))
    rec(req())
    with pytest.raises(CassetteLeak, match='too many distinct secret values'):
        rec.to_json()
    assert time.monotonic() - t0 < 30


# ---------------------------------------------------------------------------------------------- lows
def test_finding4_a_stray_key_beside_a_valid_answer_fails_the_replay():
    from newcore.venue.cassette import CassetteMismatch, CassettePlayer
    from newcore.venue.wire import HttpRequest
    base = {'request': {'method': 'GET', 'url': 'https://testnet.binancefuture.com/fapi/v1/time', 'signed': False,
                        'query': [], 'headers': []},
            'response': {'status': 200, 'headers': {}, 'body_text': '{"serverTime": 1}'}}
    for stray in ({'error': 'WireTimeout'}, {'response': dict(base['response'], body_b64='e30=')}):
        doc = {'format': 'zb-newcore-cassette/1', 'interactions': [dict(base, **stray)]}
        with pytest.raises(CassetteMismatch, match='malformed'):
            CassettePlayer(doc)(HttpRequest('GET', 'https://testnet.binancefuture.com/fapi/v1/time', '', (), 10.0, False))


def test_finding5_paths_are_scrubbed_per_component():
    from newcore.venue.redact import scrub_path
    p = '/home/owner/.local/share/ZackBotNC/reports/tnet-1759917600000.json'
    assert scrub_path(p) == p                                        # a normal POSIX path stays readable
    assert DUMMY_KEY not in scrub_path('/tmp/' + DUMMY_KEY + '/x.json')
    win = chr(92).join(['C:', 'Users', 'owner', 'AppData', 'Local', 'ZackBotNC', 'reports', 'r.json'])
    assert scrub_path(win) == win


def test_x1_a_long_foreign_client_id_is_adopted_through_a_file(env, tmp_path):  # noqa: F811
    long_id = 'web_' + 'a' * 32
    fb = FakeBinance(foreign_orders=(long_id,))
    rc, out = run(env, ['--probe', 'P1', '--adopt-foreign', long_id], http=fb)
    assert rc == 2                                                   # key-shaped on the command line: refused
    f = tmp_path / 'adopt.txt'
    f.write_text(long_id + '\n\n', encoding='utf-8')
    rc, out = run(env, ['--probe', 'P1', '--adopt-file', str(f)], http=fb)
    assert rc == 0 and fb.orders[long_id]['status'] == 'NEW'


def test_x1_an_adopt_file_position_still_needs_its_quantity_for_cleanup(env, tmp_path):  # noqa: F811
    f = tmp_path / 'adopt.txt'
    f.write_text('SOLUSDT:LONG\n', encoding='utf-8')
    fb = stranded_with_foreign()
    rc, out = run(env, ['--cleanup', '--close-positions', '--adopt-file', str(f)], http=fb)
    assert rc == 2 and 'SYMBOL:SIDE:QTY' in out and fb.requests == []


def test_unknown_spec_keys_are_echoed_scrubbed(env, tmp_path):  # noqa: F811
    from test_ncv_tnet_harness import EXAMPLE
    s = json.load(open(EXAMPLE, encoding='utf-8'))
    s[DUMMY_KEY] = 1
    p = tmp_path / 's.json'
    p.write_text(json.dumps(s), encoding='utf-8')
    rc, out = run(env, ['--scenario', str(p)], http=FakeBinance())
    assert rc == 2 and DUMMY_KEY not in out


@pytest.mark.parametrize('name', ['\uff53\uff49\uff47\uff4e\uff41\uff54\uff55\uff52\uff45', 'api\u200bkey'])
def test_r3_unicode_names_in_a_non_json_body_are_redacted(name):
    v = 'z' * 24
    rec = CassetteRecorder(FakeHttp(ok(f'<p>{name}={v}</p>')))
    rec(req())
    assert v not in rec.to_json()


# ---------------------------------------------------------------------------------------------- 6063794395
def test_c1b_an_over_declared_foreign_quantity_is_refused_in_the_cleanup(env):  # noqa: F811
    fb = stranded_with_foreign()                                      # holds 3 = foreign 2 + NEWCORE leftover 1
    rc, out = run(env, ['--cleanup', '--close-positions', '--adopt-foreign', 'SOLUSDT:LONG:5'], http=fb)
    assert rc == 4 and 'declares 5 but holds 3' in out and 'CLEANUP CLEAN' not in out
    assert fb.pos[('SOLUSDT', 'LONG')] == D('3') and not [q for q in fb.requests if q.method in ('POST', 'DELETE')]


def test_c1b_an_over_declared_foreign_quantity_is_refused_at_preflight(env):  # noqa: F811
    fb = stranded_with_foreign()
    fb.orders.clear()
    rc, out = run(env, ['--probe', 'P1', '--adopt-foreign', 'SOLUSDT:LONG:5'], http=fb)
    assert rc == 4 and 'adopted_qty_above_position:SOLUSDT:LONG:3<5' in out


def test_n2b_ctrl_c_during_the_cleanup_listing_is_truthful(env):  # noqa: F811
    class CtrlCList(FakeBinance):
        def _get_fapi_v1_openOrders(self, q):
            raise KeyboardInterrupt
    fb = CtrlCList()
    rc, out = run(env, ['--cleanup'], http=fb)
    assert rc == 6 and 'teardown has run' not in out and '--cleanup' in out
    assert not [q for q in fb.requests if q.method in ('POST', 'DELETE')]


# ---------------------------------------------------------------------------------------------- first testnet 5a run
def _big_bodies(n, hidden=''):
    junk = ' '.join('QUJDREVGR0hJSktMTU5PUA' for _ in range(MAX_DECODED_TOKENS // 2 + 1000))
    return [ok(json.dumps({'msg': junk + (' ' + hidden if i == n - 1 else '')})) for i in range(n)]


def test_many_large_bodies_each_under_the_cap_still_produce_a_cassette():
    """38da6f7 5a: account-wide bodies (~4,500 base64-like runs each) summed past the cap across one scenario, and
    every cassette was refused. The cap is per string now: three bodies of cap/2 runs each are audited and saved."""
    rec = CassetteRecorder(FakeHttp(*_big_bodies(3)), redact=(DUMMY_KEY, DUMMY_SECRET))
    for _ in range(3):
        rec(req())
    assert json.loads(rec.to_json())['interactions']


def test_a_secret_encoded_in_one_of_many_large_bodies_is_still_caught():
    import base64
    hidden = base64.b64encode(DUMMY_SECRET.encode()).decode()
    rec = CassetteRecorder(FakeHttp(*_big_bodies(3, hidden)), redact=(DUMMY_KEY, DUMMY_SECRET))
    for _ in range(3):
        rec(req())
    with pytest.raises(CassetteLeak):
        rec.to_json()


# ---------------------------------------------------------------------------------------------- Codex P1s on 1297ee3
import base64 as _b64  # noqa: E402

from newcore.venue import cassette as C  # noqa: E402

SHORT_SECRETS = tuple(('Zq7#kP2!wX9$mN4@' * 2)[:n] for n in range(8, 16))


def _encodings(secret):
    b = secret.encode()
    std = _b64.b64encode(b).decode()
    url = _b64.urlsafe_b64encode(b).decode()
    shifted = _b64.b64encode(b'zq' + b).decode()          # inside a larger blob, another byte alignment
    return {'std': std, 'unpadded': std.rstrip('='), 'urlsafe': url, 'urlsafe_unpadded': url.rstrip('='),
            'in_blob': shifted}


def test_p1_an_encoded_secret_in_the_recorder_note_is_refused():
    """Codex P1 (1): the per-string encoded audit walked interactions only; the _provenance note was never decoded."""
    for form in _encodings(DUMMY_SECRET).values():
        rec = CassetteRecorder(FakeHttp(ok(b'{"serverTime": 1}')), redact=(DUMMY_KEY, DUMMY_SECRET),
                               note='5a smoke ' + form)
        rec(req())
        with pytest.raises(CassetteLeak, match='encoded'):
            rec.to_json()


def test_p1_a_plain_note_still_produces_a_cassette():
    rec = CassetteRecorder(FakeHttp(ok(b'{"serverTime": 1}')), redact=(DUMMY_KEY, DUMMY_SECRET), note='5a smoke run 3')
    rec(req())
    assert '5a smoke run 3' in json.loads(rec.to_json())['_provenance']


@pytest.mark.parametrize('secret', SHORT_SECRETS, ids=lambda s: f'len{len(s)}')
@pytest.mark.parametrize('kind', ['std', 'unpadded', 'urlsafe', 'urlsafe_unpadded', 'in_blob'])
def test_p1_base64_of_a_minimum_length_secret_is_refused(secret, kind):
    """Codex P1 (2): _B64_RUN needs 16+ characters, so the base64 of an 8-11 byte registered secret passed."""
    form = _encodings(secret)[kind]
    rec = CassetteRecorder(FakeHttp(ok(json.dumps({'msg': 'echo ' + form}))), redact=(DUMMY_KEY, secret))
    rec(req())
    with pytest.raises(CassetteLeak):
        rec.to_json()


@pytest.mark.parametrize('secret', SHORT_SECRETS[:4], ids=lambda s: f'len{len(s)}')
def test_p1_a_minimum_length_secret_encoded_in_the_note_is_refused(secret):
    rec = CassetteRecorder(FakeHttp(ok(b'{"serverTime": 1}')), redact=(DUMMY_KEY, secret),
                           note=_encodings(secret)['unpadded'])
    rec(req())
    with pytest.raises(CassetteLeak):
        rec.to_json()


def test_p2_the_whole_audit_has_a_total_run_budget(monkeypatch):
    """Codex P2: per-string caps alone let many small strings / interactions make unbounded audit work."""
    monkeypatch.setattr(C, 'MAX_AUDIT_RUNS', 100, raising=False)
    junk = ' '.join('QUJDREVGR0hJSktMTU5PUA' for _ in range(60))        # each body far under the per-string cap
    rec = CassetteRecorder(FakeHttp(*[ok(json.dumps({'msg': junk})) for _ in range(2)]),
                           redact=(DUMMY_KEY, DUMMY_SECRET))
    rec(req())
    rec(req())
    with pytest.raises(CassetteLeak, match='too many encoded runs'):
        rec.to_json()


@pytest.mark.parametrize('limit', ['MAX_AUDIT_STRINGS', 'MAX_AUDIT_CHARS'])
def test_p2_the_whole_audit_has_a_total_string_and_char_budget(monkeypatch, limit):
    monkeypatch.setattr(C, limit, 30, raising=False)
    rec = CassetteRecorder(FakeHttp(*[ok(b'{"serverTime": 1}') for _ in range(12)]), redact=(DUMMY_KEY, DUMMY_SECRET))
    for _ in range(12):
        rec(req())
    with pytest.raises(CassetteLeak, match='too much text'):
        rec.to_json()


# ---------------------------------------------------------------------------------------------- Codex on bc5a351
from newcore.venue.cassette_replay import leak_audit  # noqa: E402

CASED_SECRETS = tuple('aBcDeFgHiJkLmNoP'[:n] for n in range(8, 16))


@pytest.mark.parametrize('secret', CASED_SECRETS, ids=lambda s: f'len{len(s)}')
@pytest.mark.parametrize('case', ['lower', 'upper', 'swapcase'])
@pytest.mark.parametrize('kind', ['std', 'unpadded', 'urlsafe_unpadded'])
def test_bc5_p1_base64_of_a_case_variant_of_a_short_secret_is_refused(secret, case, kind):
    """Codex P1: value matching is case-insensitive, but only the exact spelling's base64 was generated and runs
    under 16 characters were never decoded: base64('ABCDEFGH') passed with 'abcdefgh' registered."""
    form = _encodings(getattr(secret, case)())[kind]
    rec = CassetteRecorder(FakeHttp(ok(json.dumps({'msg': 'echo ' + form}))), redact=(DUMMY_KEY, secret))
    rec(req())
    with pytest.raises(CassetteLeak, match='encoded'):
        rec.to_json()


def test_bc5_p1_codex_repro_upper_case_of_abcdefgh():
    rec = CassetteRecorder(FakeHttp(ok(json.dumps({'msg': 'QUJDREVGR0g='}))), redact=(DUMMY_KEY, 'abcdefgh'))
    rec(req())
    with pytest.raises(CassetteLeak):
        rec.to_json()


def _clean_doc(n=12):
    rec = CassetteRecorder(FakeHttp(*[ok(b'{"serverTime": 1}') for _ in range(n)]))
    for _ in range(n):
        rec(req())
    return json.loads(rec.to_json())


@pytest.mark.parametrize('limit,value', [('MAX_AUDIT_CHARS', 10), ('MAX_AUDIT_STRINGS', 5)])
def test_bc5_p2_leak_audit_without_registered_values_is_bounded(monkeypatch, limit, value):
    """Codex P2 repro: MAX_AUDIT_CHARS=10 and a replay document of >100 characters, no redact values: it passed."""
    doc = _clean_doc()
    assert len(json.dumps(doc)) > 100 and leak_audit(doc) is None
    monkeypatch.setattr(C, limit, value)
    assert 'too much text' in leak_audit(doc)


def test_bc5_p2_recorder_without_registered_values_is_bounded(monkeypatch):
    rec = CassetteRecorder(FakeHttp(ok(b'{"serverTime": 1}')))
    rec(req())
    monkeypatch.setattr(C, 'MAX_AUDIT_CHARS', 10)
    with pytest.raises(CassetteLeak, match='too much text'):
        rec.to_json()


def test_bc5_p2_the_document_is_charged_before_any_value_scan(monkeypatch):
    def scan(*a, **k):
        raise AssertionError('value scan before the budget')
    rec = CassetteRecorder(lambda r: None, redact=(DUMMY_KEY, DUMMY_SECRET))
    rec.interactions = _clean_doc()['interactions']
    monkeypatch.setattr(C, 'MAX_AUDIT_CHARS', 10)
    monkeypatch.setattr(C, 'contains_values', scan)
    with pytest.raises(CassetteLeak, match='too much text'):
        rec.to_json()


def test_bc5_p2_secret_derived_work_is_capped_before_forms_are_built(monkeypatch):
    """Codex P2 on bc5a351, kept: b64_forms is never built for values over the caps (now refused at registration)."""
    def forms(values):
        raise AssertionError('forms built before the secret caps')
    monkeypatch.setattr(C, 'MAX_SECRET_TOTAL_BYTES', 40)
    monkeypatch.setattr(C, 'b64_forms', forms)
    with pytest.raises(ValueError):
        CassetteRecorder(FakeHttp(), redact=('k' * 30, 's' * 30))


# ---------------------------------------------------------------------------------------------- Codex on bcc2d8d
import urllib.parse as _up  # noqa: E402

from newcore.venue import redact as _redact  # noqa: E402


def _refused(text_or_note, secret, where='msg'):
    body = json.dumps({'msg': text_or_note}) if where == 'msg' else '{"serverTime": 1}'
    rec = CassetteRecorder(FakeHttp(ok(body)), redact=(DUMMY_KEY, secret),
                           note=text_or_note if where == 'note' else '')
    rec(req())
    with pytest.raises(CassetteLeak):
        rec.to_json()


def test_bcc_p1_codex_repro_percent_encoded_case_variant_in_the_note():
    """Codex P1: decoded_views ran on the percent-encoded string only; QUJDREF%2BQUE%3D = b64('ABCDA~AA') passed."""
    _refused('QUJDREF%2BQUE%3D', 'abcda~aa', 'note')


@pytest.mark.parametrize('where', ['msg', 'note'])
@pytest.mark.parametrize('secret', ['abcda~aa', 'abcd~?a>b', 'ab~c?d>e?f', 'ab?cd~ef>gh'], ids=len)
@pytest.mark.parametrize('alphabet', ['std', 'urlsafe'])
def test_bcc_p1_percent_encoded_base64_of_a_case_variant_is_refused(where, secret, alphabet):
    enc = _b64.b64encode if alphabet == 'std' else _b64.urlsafe_b64encode
    form = enc(secret.upper().encode()).decode()
    assert form != _up.quote(form, safe='') or alphabet == 'urlsafe'
    _refused(_up.quote(form, safe=''), secret, where)


def test_bcc_p2_secret_caps_hold_before_any_pattern_is_compiled(monkeypatch):
    """Codex P2: a MAX_SECRET_CHARS + 1 value reached value_pattern before the audit refused it."""
    seen = []
    real = _redact.value_pattern
    monkeypatch.setattr(_redact, 'value_pattern', lambda v: seen.append(v) or real(v))
    monkeypatch.setattr(C, 'MAX_SECRET_BYTES', 64)
    with pytest.raises(ValueError):
        CassetteRecorder(FakeHttp(), redact=(DUMMY_KEY, 'x' * 65))
    long_key = 'L' * 65                                            # a learned value (sensitive JSON field)
    rec = CassetteRecorder(FakeHttp(ok(json.dumps({'listenKey': long_key}))), redact=(DUMMY_KEY, DUMMY_SECRET))
    rec(req())
    with pytest.raises(CassetteLeak):
        rec.to_json()
    assert long_key not in seen and 'x' * 65 not in seen


def test_bcc_p2_secret_caps_count_utf8_bytes(monkeypatch):
    """Codex P2: eight 'é' passed an 8-unit cap while being 16 UTF-8 bytes."""
    monkeypatch.setattr(C, 'MAX_SECRET_BYTES', 10)
    with pytest.raises(ValueError):
        CassetteRecorder(FakeHttp(), redact=('é' * 8,))


def test_bcc_p2_to_json_charges_the_document_before_serializing(monkeypatch):
    """Codex P2: to_json serialized the whole document before the budget saw it."""
    rec = CassetteRecorder(FakeHttp(ok(b'{"serverTime": 1}')), redact=(DUMMY_KEY, DUMMY_SECRET))
    rec(req())
    calls = []
    real = json.dumps
    monkeypatch.setattr(C.json, 'dumps', lambda *a, **k: calls.append(1) or real(*a, **k))
    monkeypatch.setattr(C, 'MAX_AUDIT_CHARS', 10)
    with pytest.raises(CassetteLeak, match='too much text'):
        rec.to_json()
    assert calls == []


def test_bcc_p2_structure_and_numbers_are_charged():
    """Codex P2 repro: a zero-secret replay document with a 10,000-number list passed a 4-string / 46-unit budget."""
    doc = {'format': 'zb-newcore-cassette/1', 'interactions': [], 'pad': list(range(10000))}
    assert leak_audit(doc) is None
    import unittest.mock as um
    with um.patch.object(C, 'MAX_AUDIT_STRINGS', 4), um.patch.object(C, 'MAX_AUDIT_CHARS', 46):
        assert 'too much text' in leak_audit(doc)
    with um.patch.object(C, 'MAX_AUDIT_NODES', 100):
        assert 'too much text' in leak_audit(doc)


def test_bcc_p2_document_bytes_not_characters(monkeypatch):
    doc = {'format': 'é' * 40, 'interactions': []}
    monkeypatch.setattr(C, 'MAX_AUDIT_CHARS', 70)                  # 40 chars fit, 80 bytes do not
    assert 'too much text' in leak_audit(doc)


# self-attack on the decoded-views pipeline: every transform chain below stacks on a CASE-VARIANT of a short secret
SECRET8 = 'abcdefgh'
UP = SECRET8.upper().encode()


@pytest.mark.parametrize('text', [
    'pre' + _b64.b64encode(b'Abcdefgh').decode() + 'suf',                    # Cowork LOW-1: glued, alignment lost
    'x' + _b64.b64encode(UP).decode(),
    'xy' + _b64.urlsafe_b64encode(UP).decode().rstrip('='),
    _b64.b64encode(_b64.b64encode(UP)).decode(),                              # double base64
    _b64.b64encode(_up.quote(SECRET8.upper(), safe='').encode()).decode(),   # base64 of percent-encoded
    _b64.b64encode(UP.hex().encode()).decode(),                               # base64 of hex
    'a' + UP.hex(),                                                           # hex glued, odd offset
    ''.join(chr(ord(c) + 0xFEE0) for c in _b64.b64encode(UP).decode().rstrip('=')),  # fullwidth (NFKC)
    ''.join('\\u%04x' % ord(c) for c in _b64.b64encode(UP).decode()),        # unicode-escaped base64
    _b64.b64encode(UP).decode()[:6] + '\n' + _b64.b64encode(UP).decode()[6:],  # MIME line wrap
    _up.quote(_up.quote(_b64.b64encode(UP).decode(), safe=''), safe=''),     # double percent-encoding
], ids=['glued', 'prefix1', 'prefix2_urlsafe', 'double_b64', 'b64_of_pct', 'b64_of_hex', 'hex_glued', 'fullwidth',
        'unicode_escape', 'line_wrap', 'double_pct'])
@pytest.mark.parametrize('where', ['msg', 'note'])
def test_bcc_self_attack_transform_chains_of_a_case_variant_are_refused(text, where):
    _refused(text, SECRET8, where)


def test_bcc_benign_text_is_still_saved():
    """No false refusals on ordinary answers: ids, prices, base64-looking symbols, percent / escapes."""
    body = json.dumps({'msg': 'BTCUSDT 1759917000050 0.00012 %41 \\w QUJDREVGR0hJSktMTU5PUA tok+en/abc='})
    rec = CassetteRecorder(FakeHttp(ok(body)), redact=(DUMMY_KEY, DUMMY_SECRET), note='5a smoke')
    rec(req())
    assert json.loads(rec.to_json())['interactions']
