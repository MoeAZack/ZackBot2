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
