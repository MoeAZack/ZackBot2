"""TNET-01 harness pieces: preflight, guaranteed cleanup, SIGINT guard and the redacted report. A stateful fake venue at
the port level (plus one end-to-end preflight through TestnetVenue over fake HTTP). No network, dummy keys only."""
import json
import os
import signal
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from newcore.ports import keys as K
from newcore.ports import venue as P
from newcore.ports.values import PortValueError
from newcore.venue import tnet as T
from newcore.venue.credentials import CredentialStoreError, StaticCredentials
from newcore.venue.guard import TESTNET_BASE_URL
from newcore.venue.testnet_venue import HedgeModeRequired, TestnetAccountReader, TestnetVenue
from newcore.venue.transport import BinanceTestnetTransport, PositionMode

from ncv_support import DUMMY_KEY, DUMMY_SECRET, FakeHttp, fixture, raw

OBS = 1759917600000
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SYMS = ('SOLUSDT', 'BTCUSDT')


def ncid(n, route='classic'):
    return K.client_id_for('int_' + format(n, '032x'), route)


def order(sym, cid, side='LONG', route='classic', otype='STOP_MARKET'):
    return P.VenueOrder(ref=P.OrderRef(symbol=sym, client_id=cid, route=route), exchange_order_id='1',
                        position_side=side, reduce=True, order_type=otype, status='NEW', qty=D('1'),
                        close_position=False, stop_price=D('100'))


class FakeTransport:
    base_url, environment = TESTNET_BASE_URL, 'testnet'

    def __init__(self, venue):
        self.venue = venue

    def cancel_order(self, symbol, cid):
        self.venue._remove(cid)
        return SimpleNamespace(kind=SimpleNamespace(value='final'))

    def cancel_algo_order(self, cid):
        self.venue._remove(cid)
        return SimpleNamespace(kind=SimpleNamespace(value='acknowledged'))


class FakeVenue:
    """Positions {(symbol, side): qty} and open orders, with injectable faults:
    lost_cancel={cid}: the cancel happens but its answer is lost (UNKNOWN);
    fill_on_cancel={cid: (symbol, side, qty)}: cancelling races a trigger - a position appears;
    partial_close=n: the first n closes execute only half;  cancel_raises={cid};  unknown_reads=n (first n reads)."""

    def __init__(self, positions=None, orders=(), hedge=True, balance=D('5000'), **faults):
        self.pos = dict(positions or {})
        self.orders = list(orders)
        self.hedge, self.balance, self.f = hedge, balance, faults
        self.transport = FakeTransport(self)
        self.calls = []

    def _remove(self, cid):
        self.orders = [o for o in self.orders if o.ref.client_id != cid]
        if cid in self.f.get('fill_on_cancel', {}):
            s, side, q = self.f['fill_on_cancel'].pop(cid)
            self.pos[(s, side)] = self.pos.get((s, side), D(0)) + q

    def check_hedge_mode(self):
        if not self.hedge:
            raise HedgeModeRequired('one-way')

    def _unknown_read(self):
        n = self.f.get('unknown_reads', 0)
        if n:
            self.f['unknown_reads'] = n - 1
            return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=OBS, detail='timeout')
        return None

    def positions(self, symbol=None):
        self.calls.append(('positions', symbol))
        u = self._unknown_read()
        if u:
            return u
        return P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=OBS, value=tuple(
            P.VenuePosition(symbol=s, side=side, qty=q, entry_price=D('100')) for (s, side), q in sorted(self.pos.items())
            if symbol in (None, s)))

    def open_orders(self, symbol=None):
        self.calls.append(('open_orders', symbol))
        return P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=OBS,
                             value=tuple(o for o in self.orders if symbol in (None, o.ref.symbol)))

    def cancel(self, ref):
        self.calls.append(('cancel', ref.client_id))
        if not K.is_newcore_client_id(ref.client_id):
            raise PortValueError('ref.client_id', 'not a NEWCORE id')           # as TestnetVenue._owned
        if ref.client_id in self.f.get('cancel_raises', set()):
            raise RuntimeError('boom')
        self._remove(ref.client_id)
        if ref.client_id in self.f.get('lost_cancel', set()):
            return P.OrderOutcome(kind=P.OutcomeKind.UNKNOWN, ref=ref, observed_at_ms=OBS, detail='timeout')
        return P.OrderOutcome(kind=P.OutcomeKind.FINAL, ref=ref, observed_at_ms=OBS, status='CANCELED',
                              exchange_order_id='1', executed_qty=D('0'))

    def submit_market(self, o):
        self.calls.append(('close', o.ref.client_id, o.position_side, o.qty))
        assert o.reduce, 'cleanup must only ever reduce'
        have = self.pos.get((o.ref.symbol, o.position_side), D(0))
        q = min(o.qty, have)
        if self.f.get('partial_close', 0):
            self.f['partial_close'] -= 1
            q = q / 2
        self.pos[(o.ref.symbol, o.position_side)] = have - q
        return P.OrderOutcome(kind=P.OutcomeKind.FINAL, ref=o.ref, observed_at_ms=OBS, status='FILLED' if q else
                              'EXPIRED', exchange_order_id='2', executed_qty=q, avg_price=D('100') if q else None)


class FakeReader:
    def __init__(self, venue, unknown=False):
        self.v, self.unknown = venue, unknown

    def equity(self, asset='USDT'):
        if self.unknown:
            return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=OBS, detail='timeout')
        return P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=OBS,
                             value=(SimpleNamespace(asset=asset, available_balance=self.v.balance),))


# ---------- preflight ----------

def test_preflight_ok_when_flat_hedged_funded():
    v = FakeVenue()
    r = T.tnet_preflight(v, FakeReader(v), SYMS)
    assert r.ok and r.exit_code == 0 and r.refusals == () and r.available_balance == D('5000')


@pytest.mark.parametrize('venue_kw,reader_kw,token', [
    (dict(hedge=False), {}, 'one_way_mode'),
    (dict(balance=D('50')), {}, 'balance_below_min'),
    ({}, dict(unknown=True), 'balance_unknown'),
    (dict(positions={('SOLUSDT', 'LONG'): D('1')}), {}, 'not_flat:SOLUSDT:LONG'),
    (dict(orders=[order('SOLUSDT', 'web_foreign1')]), {}, 'foreign_order:SOLUSDT:web_foreign1'),
    (dict(orders=[order('BTCUSDT', ncid(1))]), {}, 'leftover_newcore_order:BTCUSDT'),
    (dict(unknown_reads=1), {}, 'positions_unknown:SOLUSDT'),
])
def test_preflight_refusals_are_typed(venue_kw, reader_kw, token):
    v = FakeVenue(**venue_kw)
    r = T.tnet_preflight(v, FakeReader(v, **reader_kw), SYMS)
    assert not r.ok and r.exit_code == T.PREFLIGHT_REFUSED and any(x.startswith(token) for x in r.refusals)


def test_preflight_adopt_foreign_explicitly():
    v = FakeVenue(positions={('SOLUSDT', 'SHORT'): D('3')}, orders=[order('SOLUSDT', 'web_foreign1', side='SHORT')])
    r = T.tnet_preflight(v, FakeReader(v), SYMS, adopt_foreign=('SOLUSDT:SHORT', 'web_foreign1'))
    assert r.ok and r.baseline == {('SOLUSDT', 'SHORT'): D('3')}
    assert not T.tnet_preflight(v, FakeReader(v), SYMS, adopt_foreign=('web_foreign1',)).ok      # position not listed


def test_preflight_refuses_a_non_testnet_host():
    v = FakeVenue()
    v.transport.base_url = 'https://fapi.binance.com'
    assert 'not_testnet_host' in T.tnet_preflight(v, FakeReader(v), SYMS).refusals


def test_preflight_end_to_end_over_the_real_adapter():
    rows = json.loads(fixture('position_risk_hedge').body)
    for r in rows:
        r['positionAmt'] = '0'
    flat = raw(200, json.dumps(rows).encode())
    http = FakeHttp('dual_side_true', 'account_v2', flat, raw(200, b'[]'), raw(200, b'[]'),
                    flat, 'open_orders', raw(200, b'[]'))
    t = BinanceTestnetTransport(environment='testnet', http=http, clock=lambda: OBS, position_mode=PositionMode.HEDGE,
                                credentials=StaticCredentials(DUMMY_KEY, DUMMY_SECRET))
    v = TestnetVenue(t, lambda: OBS)
    r = T.tnet_preflight(v, TestnetAccountReader(t, lambda: OBS), SYMS)
    # SOLUSDT is clean; BTCUSDT carries the fixture's stop with the foreign-looking id zb-es-... -> refused
    assert not r.ok and r.refusals == ('foreign_order:BTCUSDT:zb-es-AAAABBBBCCCCDDDDEEEE',)
    assert all(req.method == 'GET' for req in http.requests)


# ---------- cleanup ----------

def test_cleanup_of_a_clean_account_does_nothing():
    v = FakeVenue(orders=[order('SOLUSDT', 'web_foreign1')])
    r = T.tnet_cleanup(v, SYMS, run_id='r1')
    assert r.clean and r.exit_code == T.CLEANUP_CLEAN and r.attempts == 1 and r.cancelled == [] and r.closes == []
    assert not [c for c in v.calls if c[0] in ('cancel', 'close')]


def test_cleanup_cancels_newcore_orders_and_flattens_never_touching_foreign():
    foreign = order('SOLUSDT', 'web_foreign1')
    v = FakeVenue(positions={('SOLUSDT', 'LONG'): D('2'), ('BTCUSDT', 'SHORT'): D('0.5')},
                  orders=[order('SOLUSDT', ncid(1)), order('BTCUSDT', ncid(2, 'algo'), side='SHORT', route='algo'),
                          foreign])
    r = T.tnet_cleanup(v, SYMS, run_id='r1')
    assert r.clean and r.attempts == 2
    assert {c for _, c, _ in r.cancelled} == {ncid(1), ncid(2, 'algo')}
    assert v.orders == [foreign] and ('cancel', 'web_foreign1') not in v.calls
    assert all(q == 0 for q in v.pos.values())
    assert {(s, side) for s, side, *_ in r.closes} == {('SOLUSDT', 'LONG'), ('BTCUSDT', 'SHORT')}


def test_lost_cancel_answer_is_resolved_by_the_re_read():
    v = FakeVenue(orders=[order('SOLUSDT', ncid(1))], lost_cancel={ncid(1)})
    r = T.tnet_cleanup(v, SYMS, run_id='r1')
    assert r.clean and r.cancelled == [('SOLUSDT', ncid(1), 'unknown')] and v.orders == []


def test_partial_flatten_is_finished_by_the_next_attempt_under_a_new_client_id():
    v = FakeVenue(positions={('SOLUSDT', 'LONG'): D('4')}, partial_close=1)
    r = T.tnet_cleanup(v, SYMS, run_id='r1')
    assert r.clean and r.attempts == 3 and [c[2] for c in r.closes] == [D('4'), D('2')]
    assert r.closes[0][3] != r.closes[1][3]
    assert r.closes[0][3] == T.cleanup_client_ref('r1', 'SOLUSDT', 'LONG', 1).client_id


def test_position_appearing_after_a_cancel_is_flattened_in_the_same_attempt():
    v = FakeVenue(orders=[order('SOLUSDT', ncid(1))], fill_on_cancel={ncid(1): ('SOLUSDT', 'SHORT', D('1'))})
    r = T.tnet_cleanup(v, SYMS, run_id='r1')
    assert r.clean and r.closes and r.closes[0][:3] == ('SOLUSDT', 'SHORT', D('1')) and v.pos[('SOLUSDT', 'SHORT')] == 0
    assert r.attempts == 2


def test_attempts_run_out_gives_the_dirty_exit_code_and_the_remaining_truth():
    v = FakeVenue(positions={('SOLUSDT', 'LONG'): D('8')}, partial_close=10)
    r = T.tnet_cleanup(v, SYMS, run_id='r1', max_attempts=2)
    assert not r.clean and r.exit_code == T.CLEANUP_DIRTY and r.attempts == 2
    assert r.remaining_positions == [('SOLUSDT', 'LONG', '2')]
    text = T.format_cleanup(r)
    assert 'NOT CLEAN' in text and 'REMAINING EXCHANGE TRUTH' in text and 'position SOLUSDT LONG 2' in text


def test_unknown_reads_never_count_as_clean():
    v = FakeVenue(unknown_reads=100)
    r = T.tnet_cleanup(v, SYMS, run_id='r1', max_attempts=2)
    assert not r.clean and r.exit_code == T.CLEANUP_DIRTY and any('positions_unknown' in n for n in r.notes)


def test_adopted_foreign_position_is_kept_at_its_baseline():
    v = FakeVenue(positions={('SOLUSDT', 'SHORT'): D('5')})
    v.pos[('SOLUSDT', 'SHORT')] += D('1')                                  # the scenario added 1 on top
    r = T.tnet_cleanup(v, SYMS, run_id='r1', baseline={('SOLUSDT', 'SHORT'): D('5')})
    assert r.clean and v.pos[('SOLUSDT', 'SHORT')] == D('5') and r.closes[0][2] == D('1')


def test_emergency_and_raising_cancels_never_crash_the_teardown():
    e_cid = 'zbn1e-' + 'a' * 26
    v = FakeVenue(orders=[order('SOLUSDT', e_cid), order('BTCUSDT', ncid(3))], cancel_raises={ncid(3)})
    r = T.tnet_cleanup(v, SYMS, run_id='r1', max_attempts=2)
    assert ('SOLUSDT', e_cid, 'final') in r.cancelled                       # via the transport fallback
    assert ('BTCUSDT', ncid(3), 'error_RuntimeError') in r.cancelled and not r.clean


def test_cleanup_only_reduces():
    v = FakeVenue(positions={('SOLUSDT', 'LONG'): D('1'), ('SOLUSDT', 'SHORT'): D('1')})
    T.tnet_cleanup(v, SYMS, run_id='r1')                                   # FakeVenue asserts reduce=True
    assert all(q == 0 for q in v.pos.values())


# ---------- guarded teardown ----------

def test_guarded_runs_cleanup_after_success_and_after_an_error():
    seen = []
    assert T.guarded(lambda: 'ok', lambda: seen.append('c') or 'cleaned') == ('ok', 'cleaned')
    with pytest.raises(ValueError):
        T.guarded(lambda: (_ for _ in ()).throw(ValueError('x')), lambda: seen.append('c2'))
    assert seen == ['c', 'c2']


def test_sigint_during_the_run_still_cleans_up_and_a_second_sigint_cannot_abort_it():
    seen = []

    def run():
        signal.raise_signal(signal.SIGINT)
        seen.append('not reached')

    def cleanup():
        signal.raise_signal(signal.SIGINT)                                 # ignored during the teardown
        seen.append('cleanup finished')
        return 'cleaned'
    before = signal.getsignal(signal.SIGINT)
    with pytest.raises(KeyboardInterrupt):
        T.guarded(run, cleanup)
    assert seen == ['cleanup finished'] and signal.getsignal(signal.SIGINT) == before


# ---------- report ----------

def git_ok(args):
    if args[0] == 'rev-parse':
        return SimpleNamespace(returncode=0, stdout='a' * 40 + '\n')
    return SimpleNamespace(returncode=0, stdout='')


def make_report(tmp_path, **kw):
    v = FakeVenue()
    cleanup = T.tnet_cleanup(v, SYMS, run_id='r1')
    args = dict(run_id='r1', cleanup=cleanup, config={'mode': 'TESTNET', 'symbols': list(SYMS), 'key_digest': 'ab' * 8},
                cassette_path=r'C:\x\cassettes\smoke-1.json', fees=D('0.22'), pnl=D('-0.5'),
                build=T.git_build(REPO, run=git_ok), now_ms=OBS, out_dir=str(tmp_path / 'reports'),
                scenarios=[T.ScenarioOutcome('entry_stop_close', True, (('stop resting after fill', True),
                                                                       ('flat after close', True)))])
    args.update(kw)
    return T.tnet_report(**args)


def test_report_json_and_markdown(tmp_path):
    jp, mp = make_report(tmp_path)
    assert os.path.basename(jp) == f'tnet-{OBS}.json' and os.path.basename(mp) == f'tnet-{OBS}.md'
    doc = json.load(open(jp, encoding='utf-8'))
    assert doc['build'] == {'sha': 'a' * 40, 'dirty': False} and doc['passed'] is True
    assert len(doc['config_digest']) == 64 and doc['fees'] == '0.22' and doc['pnl'] == '-0.5'
    assert doc['scenarios'][0]['assertions'] == [['stop resting after fill', True], ['flat after close', True]]
    assert doc['final_exchange_truth']['clean'] is True
    md = open(mp, encoding='utf-8').read()
    assert '- [x] stop resting after fill' in md and '`' + 'a' * 40 + '`' in md


def test_report_fails_overall_when_a_scenario_or_the_cleanup_fails(tmp_path):
    jp, _ = make_report(tmp_path, scenarios=[T.ScenarioOutcome('x', False, (('flat', False),))])
    assert json.load(open(jp, encoding='utf-8'))['passed'] is False


def test_git_build_dirty_and_unknown():
    dirty = T.git_build(REPO, run=lambda a: SimpleNamespace(returncode=0, stdout='a' * 40 if a[0] == 'rev-parse'
                                                             else ' M x.py\n'))
    assert dirty == {'sha': 'a' * 40, 'dirty': True}
    assert T.git_build(REPO, run=lambda a: SimpleNamespace(returncode=128, stdout='')) == {'sha': None, 'dirty': True}
    real = T.git_build(REPO)
    assert real['sha'] is None or len(real['sha']) == 40


def test_config_with_a_credential_field_is_refused(tmp_path):
    with pytest.raises(T.ReportLeak):
        make_report(tmp_path, config={'api_secret': 'x'})
    assert not os.path.exists(tmp_path / 'reports')


def test_secret_value_in_the_report_is_refused_before_writing(tmp_path):
    with pytest.raises(T.ReportLeak):
        make_report(tmp_path, redact=(DUMMY_SECRET,),
                    scenarios=[T.ScenarioOutcome('x', True, ((f'saw {DUMMY_SECRET.lower()}', True),))])
    with pytest.raises(T.ReportLeak):
        make_report(tmp_path, scenarios=[T.ScenarioOutcome('x', True, (('listenKey=abcdefghij', True),))])
    assert not os.path.exists(tmp_path / 'reports')


def test_report_dir_inside_the_repo_or_legacy_is_refused(tmp_path, monkeypatch):
    with pytest.raises(CredentialStoreError):
        make_report(tmp_path, out_dir=os.path.join(REPO, 'reports'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    with pytest.raises(CredentialStoreError):
        make_report(tmp_path, out_dir=str(tmp_path / 'ZackBot' / 'reports'))
    assert T.default_report_dir() == os.path.join(str(tmp_path), 'ZackBotNC', 'reports')
