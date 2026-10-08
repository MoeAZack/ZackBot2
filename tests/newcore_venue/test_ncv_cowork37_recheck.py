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
