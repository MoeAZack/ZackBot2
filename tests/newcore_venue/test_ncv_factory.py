"""newcore.venue.factory:build_testnet - the runner's testnet factory hook. Fake HTTP + fake credential store; no network,
dummy keys only."""
import hashlib
import importlib
import json
from dataclasses import dataclass, field
from decimal import Decimal as D

import pytest

from newcore.ports import bars as B
from newcore.ports import venue as P
from newcore.venue import factory as F
from newcore.venue.credentials import (CredentialsUnavailable, StaticCredentials, binding_digest)
from newcore.venue.guard import VenueGuardError
from newcore.venue.rules_fetch import RulesUnavailable, SymbolNotTrading
from newcore.venue.testnet_venue import HedgeModeRequired, TestnetAccountReader, TestnetVenue, VenueBootUnknown
from newcore.venue.wire import HttpResponse, WireTimeout

from ncv_support import DUMMY_KEY, DUMMY_SECRET, FakeHttp, fixture

NOW = 1759917600000
DIGEST = binding_digest(DUMMY_KEY)


@dataclass(frozen=True)
class Cfg:
    """The fields of newcore.runner.config.RunConfig the factory reads (origin/nc-s1-slice)."""
    mode: str = 'TESTNET'
    venue_kind: str = 'testnet'
    factory: str = 'newcore.venue.factory:build_testnet'
    account_id: str = 'acct_' + 'ab' * 16
    key_digest: str = DIGEST
    symbols: tuple = ('SOLUSDT', 'BTCUSDT')
    tf: str = '4h'


class FakeStore:
    def __init__(self, key=DUMMY_KEY, secret=DUMMY_SECRET, fail=None):
        self.key, self.secret, self.fail, self.registered = key, secret, fail, None

    def load(self, scrubber=None):
        if self.fail is not None:
            raise self.fail
        if scrubber is not None:
            scrubber.register(self.key, self.secret)
            self.registered = scrubber
        return StaticCredentials(self.key, self.secret)


def server_time():
    return HttpResponse(200, {}, json.dumps({'serverTime': NOW}).encode())


def script(**override):
    s = dict(t1=server_time(), t2=server_time(), t3=server_time(), dual=fixture('dual_side_true'),
             info=fixture('exchange_info'))
    s.update(override)
    return [v for v in s.values() if v is not None]


def build(cfg=None, *answers, store=None, **kw):
    http = FakeHttp(*(answers or script()))
    parts = F.build_testnet(cfg or Cfg(), http=http, local_clock=lambda: NOW, store=store or FakeStore(), **kw)
    return parts, http


def test_builds_the_runner_parts():
    parts, http = build()
    assert isinstance(parts['venue'], TestnetVenue) and isinstance(parts['venue'], P.VenuePort)
    assert isinstance(parts['bars'], B.BarSource) and isinstance(parts['account_reader'], TestnetAccountReader)
    assert set(parts['instrument_rules']) == {'SOLUSDT', 'BTCUSDT'} and parts['binding_digest'] == DIGEST
    assert parts['rules'].doc['schema'] == 'zackbot.exchange_rules/1'
    paths = [r.url.split('binancefuture.com', 1)[1] for r in http.requests]
    assert paths == ['/fapi/v1/time'] * 3 + ['/fapi/v1/positionSide/dual', '/fapi/v1/exchangeInfo']
    assert all(r.method == 'GET' for r in http.requests)                   # building never places anything
    assert all(r.url.startswith('https://testnet.binancefuture.com/') for r in http.requests)


def test_the_runner_hook_shape_and_module_callable_spec():
    mod, _, name = Cfg().factory.partition(':')
    fn = getattr(importlib.import_module(mod), name)
    assert fn is F.build_testnet
    parts, _ = build()
    assert {'venue', 'bars'} <= set(parts) and {'account_reader', 'account_reads'} & set(parts)   # testnet_hook.build


def test_the_built_venue_works_through_the_same_transport():
    parts, http = build(None, *script(), fixture('position_risk_hedge'))
    out = parts['venue'].positions('SOLUSDT')
    assert out.kind is P.ReadKind.OK and http.last.wire_header('X-MBX-APIKEY') == DUMMY_KEY


@pytest.mark.parametrize('cfg', [Cfg(mode='PAPER'), Cfg(venue_kind='fake'), Cfg(mode='LIVE'), Cfg(mode='MAINNET'),
                                 Cfg(symbols=())])
def test_wrong_mode_kind_or_symbols_is_refused_before_anything(cfg):
    store, http = FakeStore(), FakeHttp()
    with pytest.raises(F.FactoryRefused):
        F.build_testnet(cfg, http=http, local_clock=lambda: NOW, store=store)
    assert http.requests == [] and store.registered is None


@dataclass(frozen=True)
class CfgWithHost(Cfg):
    base_url: str = 'https://fapi.binance.com'


def test_a_non_testnet_host_is_refused():
    http = FakeHttp()
    with pytest.raises(VenueGuardError):
        F.build_testnet(CfgWithHost(), http=http, local_clock=lambda: NOW, store=FakeStore())
    assert http.requests == []
    parts, _ = build(CfgWithHost(base_url='https://testnet.binancefuture.com'))      # the pinned host itself is fine
    assert parts['venue']


def test_missing_credentials_are_typed_and_nothing_is_sent():
    http = FakeHttp()
    with pytest.raises(CredentialsUnavailable) as ei:
        F.build_testnet(Cfg(), http=http, local_clock=lambda: NOW,
                        store=FakeStore(fail=CredentialsUnavailable('no stored credentials', 'missing')))
    assert ei.value.reason == 'missing' and http.requests == []


def test_binding_mismatch_is_refused_before_any_request():
    http = FakeHttp()
    other = 'OTHERDUMMYKEY' + 'x' * 51
    with pytest.raises(F.BindingMismatch) as ei:
        F.build_testnet(Cfg(), http=http, local_clock=lambda: NOW, store=FakeStore(key=other))
    assert http.requests == [] and DUMMY_KEY not in str(ei.value) and other not in str(ei.value)
    with pytest.raises(F.BindingMismatch):                                   # the config's placeholder digest
        F.build_testnet(Cfg(key_digest='0123456789abcdef'), http=FakeHttp(), local_clock=lambda: NOW,
                        store=FakeStore())


def test_binding_digest_is_the_legacy_fingerprint_formula():
    expect = hashlib.pbkdf2_hmac('sha256', DUMMY_KEY.encode(), b'zackbot-install-marker-v1', 200_000).hex()[:16]
    assert binding_digest(DUMMY_KEY) == expect and len(expect) == 16


def test_one_way_account_is_refused():
    with pytest.raises(HedgeModeRequired):
        build(None, *script(dual=HttpResponse(200, {}, b'{"dualSidePosition": false}')))
    with pytest.raises(VenueBootUnknown):
        build(None, *script(dual=WireTimeout()))


def test_clock_calibration_failure_is_refused():
    with pytest.raises(F.FactoryRefused):
        build(None, WireTimeout(), WireTimeout(), WireTimeout())


def test_rules_refusals_propagate():
    body = json.loads(fixture('exchange_info').body)
    body['symbols'][1]['status'] = 'SETTLING'                                # BTCUSDT
    with pytest.raises(SymbolNotTrading):
        build(None, *script(info=HttpResponse(200, {}, json.dumps(body).encode())))
    with pytest.raises(RulesUnavailable):
        build(None, *script(info=WireTimeout()))


def test_the_key_is_registered_with_the_scrubber_and_never_in_the_parts():
    store = FakeStore()
    parts, _ = build(store=store)
    assert DUMMY_SECRET in store.registered.redaction_values()
    text = repr(parts) + json.dumps(parts['rules'].doc, default=str)
    assert DUMMY_KEY not in text and DUMMY_SECRET not in text


def test_build_does_not_retry():
    http = FakeHttp(*script(info=WireTimeout()))
    with pytest.raises(RulesUnavailable):
        F.build_testnet(Cfg(), http=http, local_clock=lambda: NOW, store=FakeStore())
    assert len(http.requests) == 5


# ---------- the owner path: store under the runner AccountId, read the binding digest for the config ----------

def test_keys_tool_accepts_the_runner_account_id_and_prints_the_binding_digest(tmp_path):
    import io
    import os
    from newcore.venue.cli_args import argv_refusal
    from newcore.venue.credentials import CredentialStore
    from test_ncv_credential_store import XorProtector, load_tool
    acct = Cfg().account_id
    assert argv_refusal(['status', '--account-id', acct]) is None
    assert argv_refusal(['status', '--account-id', 'acct_' + 'AB' * 16]) is not None          # lowercase hex only
    root = str(tmp_path / 's')
    CredentialStore(acct, root=root, protector=XorProtector(), harden_acl=False).save('testnet', DUMMY_KEY,
                                                                                    DUMMY_SECRET, now_ms=NOW)
    out = io.StringIO()
    rc = load_tool().main(['status', '--account-id', acct, '--root', root], out=out, protector=XorProtector())
    assert rc == 0 and f'binding     : {DIGEST}' in out.getvalue() and DUMMY_KEY not in out.getvalue()
