"""TNET-01 venue harness: fault seam, probes P1 / P2, the spec executor and tools/newcore_tnet.py, end to end through the
real transport against a stateful fake Binance (no network, dummy keys)."""
import importlib.util
import io
import json
import os
from decimal import Decimal as D

import pytest

from newcore.ports import venue as P
from newcore.venue.credentials import CredentialStore, StaticCredentials
from newcore.venue.testnet_venue import TestnetVenue
from newcore.venue.tnet_exec import run_spec
from newcore.venue.tnet_probes import ProbeAborted, probe_p1, probe_p2
from newcore.venue.tnet_seams import FaultHttp
from newcore.venue.tnet_spec import validate_spec
from newcore.venue.transport import BinanceTestnetTransport, PositionMode
from newcore.venue.wire import WireNotSent, WireTimeout

from fake_binance import FakeBinance
from ncv_support import DUMMY_KEY, DUMMY_SECRET
from test_ncv_credential_store import XorProtector

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
EXAMPLE = os.path.join(HERE, 'fixtures', 'tnet_entry_stop_close_long.json')
NOW = 1759917600000
ACCOUNT = '8c1d2e3f-4a5b-4c6d-8e7f-90a1b2c3d4e5'


def venue_over(http):
    t = BinanceTestnetTransport(environment='testnet', http=http, clock=lambda: NOW, position_mode=PositionMode.HEDGE,
                                credentials=StaticCredentials(DUMMY_KEY, DUMMY_SECRET))
    return TestnetVenue(t, lambda: NOW), t


def sol_rules(t):
    return t.exchange_info().value.symbols['SOLUSDT']


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


# ---------- the fault seam ----------

@pytest.mark.parametrize('kind,sent,reason', [('lost_response', 1, 'timeout'), ('timeout', 0, 'timeout'),
                                              ('reset', 0, 'connection'), ('http_5xx', 0, 'http_5xx'),
                                              ('dns_timeout', 0, 'not_sent_dns_timeout'),
                                              ('duplicate_resend', 2, 'duplicate_client_id')])
def test_fault_kinds(kind, sent, reason):
    fb = FakeBinance()
    seam = FaultHttp(fb)
    v, _ = venue_over(seam)
    seam.arm(kind)
    ref = P.OrderRef(symbol='SOLUSDT', client_id='zbn1o-' + 'a' * 26)
    out = v.submit_market(P.MarketOrder(ref=ref, position_side='LONG', qty=D('1'), reduce=False))
    assert out.kind is P.OutcomeKind.UNKNOWN and out.detail == reason
    assert len(fb.requests) == sent and not seam.armed and seam.injected[0][0] == kind
    assert v.submit_market(P.MarketOrder(ref=P.OrderRef(symbol='SOLUSDT', client_id='zbn1o-' + 'b' * 26),
                                         position_side='LONG', qty=D('1'), reduce=False)).kind is P.OutcomeKind.FINAL


def test_fault_seam_validates():
    with pytest.raises(ValueError):
        FaultHttp(FakeBinance()).arm('meteor')


# ---------- P1 ----------

@pytest.mark.parametrize('reuse', [False, True])
def test_p1_measures_client_id_reuse_and_leaves_the_account_flat(reuse):
    fb = FakeBinance(reuse_ids=reuse)
    v, t = venue_over(fb)
    r = probe_p1(v, symbol='SOLUSDT', rules=sol_rules(t), price=D('220'), run_id='run1', route='algo')
    want = 'accepted' if reuse else 'refused:duplicate_client_id'
    assert (r.stop_id_after_cancel, r.filled_id_reused) == (want, want) and r.conclusive
    assert fb.flat() and fb.open_cids() == set()
    d = r.as_dict()
    assert d['cancelled_stop_id'] == want and d['filled_order_id'] == want and d['route'] == 'algo'


def test_p1_classic_route_refused_by_the_venue_is_skipped_not_guessed():
    fb = FakeBinance(classic_stops='algo_required')
    v, t = venue_over(fb)
    r = probe_p1(v, symbol='SOLUSDT', rules=sol_rules(t), price=D('220'), run_id='run1', route='classic')
    assert r.stop_id_after_cancel == 'skipped:stop_rejected' and not r.conclusive and fb.flat()


def test_p1_classic_route_measured_when_the_venue_accepts_it():
    fb = FakeBinance(classic_stops='accept', reuse_ids=True)
    v, t = venue_over(fb)
    r = probe_p1(v, symbol='SOLUSDT', rules=sol_rules(t), price=D('220'), run_id='run1', route='classic')
    assert r.stop_id_after_cancel == 'accepted' and fb.flat() and fb.open_cids() == set()


def test_p1_refused_entry_aborts_without_exposure_and_unclosable_position_aborts_with_exposure():
    fb = FakeBinance(refuse_closes=True)
    v, t = venue_over(fb)
    with pytest.raises(ProbeAborted) as ei:
        probe_p1(v, symbol='SOLUSDT', rules=sol_rules(t), price=D('220'), run_id='run1')
    assert ei.value.exposure_possible and not fb.flat()


# ---------- P2 ----------

def test_p2_measures_the_lags():
    fb = FakeBinance(position_lag_reads=2, order_lag_reads=1)
    v, t = venue_over(fb)
    c = FakeClock()
    r = probe_p2(v, symbol='SOLUSDT', rules=sol_rules(t), price=D('220'), run_id='run1', samples=2, poll_ms=100,
                 max_wait_ms=5000, monotonic=c.monotonic, sleep=c.sleep)
    assert r.conclusive and fb.flat() and fb.open_cids() == set()
    assert r.position_after_fill_ms == [100, 100] and r.order_visible_after_place_ms == [100, 100]   # 2nd read
    assert r.flat_after_close_ms == [100, 100]
    d = r.as_dict()
    assert d['position_after_fill_p95'] == 100 and d['algo_child_visible'] == 'not_measured'


def test_p2_that_never_converges_is_inconclusive_and_bounded():
    fb = FakeBinance(position_lag_reads=10_000)
    v, t = venue_over(fb)
    c = FakeClock()
    r = probe_p2(v, symbol='SOLUSDT', rules=sol_rules(t), price=D('220'), run_id='run1', samples=1, poll_ms=500,
                 max_wait_ms=2000, monotonic=c.monotonic, sleep=c.sleep)
    assert not r.conclusive and r.position_after_fill_ms == [None] and c.t - 100.0 < 10


# ---------- the spec executor ----------

def test_example_spec_with_a_lost_entry_answer_passes():
    fb = FakeBinance()
    seam = FaultHttp(fb)
    v, t = venue_over(seam)
    spec = validate_spec(json.load(open(EXAMPLE, encoding='utf-8')))
    outcome, records = run_spec(spec, v, rules_by_symbol={'SOLUSDT': sol_rules(t)}, price_by_symbol={'SOLUSDT': D('220')},
                                run_id='run1', seam=seam)
    assert outcome.passed, outcome.assertions
    assert [r.outcome for r in records] == ['unknown', 'final', 'known', 'known', 'acknowledged', 'final', None]
    assert seam.injected == [('lost_response', 'POST', '/fapi/v1/order')] and fb.flat()


def test_spec_with_an_unmet_expectation_fails():
    fb = FakeBinance()
    v, t = venue_over(fb)
    spec = validate_spec(json.load(open(EXAMPLE, encoding='utf-8')))
    spec['faults'] = []                                       # no lost answer: entry is FINAL, but 'unknown' expected
    outcome, _ = run_spec(spec, v, rules_by_symbol={'SOLUSDT': sol_rules(t)}, price_by_symbol={'SOLUSDT': D('220')},
                          run_id='run1')
    assert not outcome.passed and ('entry: outcome final in [\'unknown\']', False) in outcome.assertions


# ---------- the CLI ----------

def tool():
    spec = importlib.util.spec_from_file_location('ncv_tnet_cli', os.path.join(REPO, 'tools', 'newcore_tnet.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path):
    root = str(tmp_path / 'secrets')
    CredentialStore(ACCOUNT, root=root, protector=XorProtector(), harden_acl=False).save(
        'testnet', DUMMY_KEY, DUMMY_SECRET, now_ms=NOW)
    return dict(root=root, cassettes=str(tmp_path / 'cassettes'), reports=str(tmp_path / 'reports'), tmp=tmp_path)


def git_clean(args):
    from types import SimpleNamespace
    return SimpleNamespace(returncode=0, stdout=('a' * 40) if args[0] == 'rev-parse' else '')


def run(env, extra, http=None, mod=None, **kw):
    out = io.StringIO()
    c = FakeClock()
    rc = (mod or tool()).main(['--account-id', ACCOUNT, '--root', env['root'], '--cassette-dir', env['cassettes'],
                               '--report-dir', env['reports'], *extra], http=http, local_clock=lambda: NOW,
                              protector=XorProtector(), out=out, monotonic=c.monotonic, sleep=c.sleep,
                              git_run=git_clean, **kw)
    return rc, out.getvalue()


def only(d):
    files = os.listdir(d) if os.path.isdir(d) else []
    return [os.path.join(d, f) for f in files]


def test_dry_run_prints_the_plan_without_network_or_key(env):
    class NoDecrypt(XorProtector):
        def unprotect(self, data, entropy):
            raise AssertionError('decrypted during a dry run')

    def no_http(r):
        raise AssertionError('network during a dry run')
    out = io.StringIO()
    rc = tool().main(['--account-id', ACCOUNT, '--root', env['root'], '--cassette-dir', env['cassettes'],
                      '--report-dir', env['reports'], '--probe', 'P1', '--probe', 'P2', '--scenario', EXAMPLE,
                      '--dry-run'], http=no_http, local_clock=lambda: NOW, protector=NoDecrypt(), out=out,
                     git_run=git_clean)
    text = out.getvalue()
    assert rc == 0 and 'PLAN (dry run' in text and 'P1 client-id reuse' in text and 'P2 read lag' in text
    assert 'scenario entry_stop_close_long' in text and 'fault lost_response x1 at entry' in text
    assert only(env['cassettes']) == [] and only(env['reports']) == []


def test_p1_and_p2_full_run_writes_report_and_cassette(env):
    fb = FakeBinance(reuse_ids=False)
    rc, out = run(env, ['--probe', 'P1', '--probe', 'P2', '--p2-samples', '1'], http=fb)
    assert rc == 0, out
    assert 'preflight OK' in out and 'P1: conclusive' in out and 'CLEANUP CLEAN' in out and fb.flat()
    (rep,) = [p for p in only(env['reports']) if p.endswith('.json')]
    doc = json.load(open(rep, encoding='utf-8'))
    assert doc['passed'] and doc['probes']['P1_client_id_reuse']['cancelled_stop_id'] == 'refused:duplicate_client_id'
    assert doc['probes']['P2_read_lag']['conclusive'] and doc['build']['sha'] == 'a' * 40
    (cas,) = only(env['cassettes'])
    text = open(cas, encoding='utf-8').read() + open(rep, encoding='utf-8').read()
    assert DUMMY_KEY not in text and DUMMY_SECRET not in text
    assert all(r.method in ('GET', 'POST', 'DELETE') for r in fb.requests)


def test_scenario_run_passes_and_fails(env, tmp_path):
    fb = FakeBinance()
    rc, out = run(env, ['--scenario', EXAMPLE], http=fb)
    assert rc == 0 and 'scenario entry_stop_close_long: PASS' in out
    spec = json.load(open(EXAMPLE, encoding='utf-8'))
    spec['faults'] = []
    bad = tmp_path / 'bad.json'
    bad.write_text(json.dumps(spec), encoding='utf-8')
    rc, out = run(env, ['--scenario', str(bad)], http=FakeBinance())
    assert rc == 7 and 'FAIL' in out


def test_preflight_refuses_foreign_orders_unless_adopted(env):
    rc, out = run(env, ['--probe', 'P1'], http=FakeBinance(foreign_orders=['web_foreign1']))
    assert rc == 4 and 'foreign_order:SOLUSDT:web_foreign1' in out
    fb = FakeBinance(foreign_orders=['web_foreign1'])
    rc, out = run(env, ['--probe', 'P1', '--adopt-foreign', 'web_foreign1'], http=fb)
    assert rc == 0 and 'web_foreign1' in fb.open_cids()                  # never touched by the cleanup


def test_one_way_account_is_refused_at_preflight(env):
    rc, out = run(env, ['--probe', 'P1'], http=FakeBinance(dual=False))
    assert rc == 4 and 'one_way_mode' in out


def test_inconclusive_probe_is_exit_one(env):
    rc, out = run(env, ['--probe', 'P2', '--p2-samples', '1'], http=FakeBinance(position_lag_reads=100_000))
    assert rc == 1 and 'P2: INCONCLUSIVE' in out


def test_residue_is_exit_eight_with_the_truth(env):
    fb = FakeBinance(refuse_closes=True)
    rc, out = run(env, ['--probe', 'P1'], http=fb)
    assert rc == 8 and 'NOT CLEAN' in out and 'position SOLUSDT LONG' in out


def test_ctrl_c_still_cleans_up_and_is_exit_six(env, monkeypatch):
    mod = tool()

    def interrupted(venue, **kw):
        ref = P.OrderRef(symbol='SOLUSDT', client_id='zbn1o-' + 'c' * 26)
        venue.submit_market(P.MarketOrder(ref=ref, position_side='LONG', qty=D('1'), reduce=False))
        raise KeyboardInterrupt
    monkeypatch.setattr(mod, 'probe_p1', interrupted)
    fb = FakeBinance()
    rc, out = run(env, ['--probe', 'P1'], http=fb, mod=mod)
    assert rc == 6 and fb.flat() and 'CLEANUP CLEAN' in out


@pytest.mark.parametrize('extra,expect', [([], 'give at least one'), (['--probe', 'P3'], ''),
                                          (['--scenario', 'missing.json'], 'REFUSED'),
                                          (['--probe', 'P1', '--min-balance', 'lots'], 'REFUSED')])
def test_usage_errors_are_exit_two(env, extra, expect):
    rc, out = run(env, extra, http=FakeBinance())
    assert rc == 2 and expect in out


def test_gate_refuses_a_dirty_tree(env):
    from types import SimpleNamespace
    out = io.StringIO()
    rc = tool().main(['--account-id', ACCOUNT, '--root', env['root'], '--probe', 'P1', '--gate', '--dry-run',
                      '--cassette-dir', env['cassettes'], '--report-dir', env['reports']],
                     local_clock=lambda: NOW, protector=XorProtector(), out=out,
                     git_run=lambda a: SimpleNamespace(returncode=0, stdout=('a' * 40) if a[0] == 'rev-parse'
                                                       else ' M x.py'))
    assert rc == 2 and 'clean, committed tree' in out.getvalue()


def test_missing_credentials_is_exit_three(env, tmp_path):
    out = io.StringIO()
    rc = tool().main(['--account-id', ACCOUNT, '--root', str(tmp_path / 'nokeys'), '--probe', 'P1',
                      '--cassette-dir', env['cassettes'], '--report-dir', env['reports']],
                     http=FakeBinance(), local_clock=lambda: NOW, protector=XorProtector(), out=out, git_run=git_clean)
    assert rc == 3 and 'newcore_keys.py set' in out.getvalue()
