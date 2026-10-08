"""Exchange-rules fetcher: exchangeInfo -> zackbot.exchange_rules/1 snapshot (S1 loader shape) + hashes. No network."""
import hashlib
import json
from decimal import Decimal as D

import pytest

from newcore.domain import InstrumentRules
from newcore.venue import rules_fetch as RF
from newcore.venue.guard import VenueGuardError
from newcore.venue.wire import HttpResponse, WireTimeout

from ncv_support import FakeHttp, fixture

NOW = 1759917600000


def info_with(**symbol_patches):
    body = json.loads(fixture('exchange_info').body)
    for sym, patch in symbol_patches.items():
        for s in body['symbols']:
            if s['symbol'] == sym:
                s.update(patch)
    return HttpResponse(200, {}, json.dumps(body).encode())


def s1_load_rules(text, symbols):
    """The S1 adapter's reading logic (origin/nc-s1-slice newcore/adapters/exchange_rules.py), reproduced: exact
    Decimals from the JSON text, schema check, per symbol tick / step / min_qty / min_notional."""
    doc = json.loads(text, parse_float=D, parse_int=D)
    assert doc.get('schema') == 'zackbot.exchange_rules/1'
    return {s: (doc['symbols'][s]['tick'], doc['symbols'][s]['step'], doc['symbols'][s]['min_qty'],
                doc['symbols'][s]['min_notional']) for s in symbols}


def test_snapshot_shape_hashes_and_exact_numbers():
    answer = fixture('exchange_info')
    snap = RF.fetch_rules(FakeHttp(answer), symbols=['SOLUSDT', 'BTCUSDT'], clock=lambda: NOW)
    doc = snap.doc
    assert doc['schema'] == 'zackbot.exchange_rules/1' and doc['environment'] == 'testnet'
    assert doc['source_url'] == 'https://testnet.binancefuture.com/fapi/v1/exchangeInfo'
    assert doc['raw_sha256'] == hashlib.sha256(answer.body).hexdigest() == snap.raw_sha256
    assert doc['fetched_at'] == '2025-10-08T10:00:00Z' and doc['server_time'] == '2025-10-08T10:00:00Z'
    sol = doc['symbols']['SOLUSDT']
    assert (sol['tick'], sol['step'], sol['min_qty'], sol['min_notional'], sol['max_qty']) == \
        (D('0.0100'), D('1'), D('1'), D('5'), D('5000'))                  # MARKET_LOT_SIZE quantities (legacy rule)
    text = snap.to_json()
    assert '"tick": 0.01' in text and '"step": 0.001' in text and 'e-' not in text.lower().replace('"note"', '')
    assert s1_load_rules(text, ['SOLUSDT', 'BTCUSDT']) == {'SOLUSDT': (D('0.01'), D('1'), D('1'), D('5')),
                                                           'BTCUSDT': (D('0.1'), D('0.001'), D('0.001'), D('100'))}


def test_rules_hash_is_stable_and_content_addressed():
    a = RF.fetch_rules(FakeHttp('exchange_info'), symbols=['SOLUSDT', 'BTCUSDT'], clock=lambda: NOW)
    b = RF.fetch_rules(FakeHttp('exchange_info'), symbols=['BTCUSDT', 'SOLUSDT'], clock=lambda: NOW + 5)
    assert a.rules_sha256 == b.rules_sha256 and len(a.rules_sha256) == 64
    c = RF.fetch_rules(FakeHttp(info_with(SOLUSDT={'filters': [
        f if f['filterType'] != 'MIN_NOTIONAL' else dict(f, notional='10')
        for f in json.loads(fixture('exchange_info').body)['symbols'][0]['filters']]})),
        symbols=['SOLUSDT', 'BTCUSDT'], clock=lambda: NOW)
    assert c.rules_sha256 != a.rules_sha256


def test_instrument_rules_are_nc01_records():
    snap = RF.fetch_rules(FakeHttp('exchange_info'), symbols=['SOLUSDT'], clock=lambda: NOW)
    r = snap.instrument_rules()['SOLUSDT']
    assert isinstance(r, InstrumentRules) and r.tick_size == D('0.01') and r.max_qty == D('5000')


@pytest.mark.parametrize('status', ['SETTLING', 'PENDING_TRADING', 'CLOSE', 'BREAK'])
def test_symbol_not_trading_is_refused(status):
    with pytest.raises(RF.SymbolNotTrading) as ei:
        RF.fetch_rules(FakeHttp(info_with(SOLUSDT={'status': status})), symbols=['SOLUSDT'], clock=lambda: NOW)
    assert ei.value.symbol == 'SOLUSDT' and status in ei.value.reason


def test_unknown_unparseable_and_non_perpetual_are_refused():
    with pytest.raises(RF.SymbolUnknown):
        RF.fetch_rules(FakeHttp('exchange_info'), symbols=['XRPUSDT'], clock=lambda: NOW)
    with pytest.raises(RF.SymbolRefused):
        RF.fetch_rules(FakeHttp('exchange_info'), symbols=['ODDUSDT'], clock=lambda: NOW)
    with pytest.raises(RF.SymbolRefused):
        RF.fetch_rules(FakeHttp(info_with(SOLUSDT={'contractType': 'CURRENT_QUARTER'})), symbols=['SOLUSDT'],
                       clock=lambda: NOW)


def test_one_refused_symbol_produces_no_partial_snapshot():
    with pytest.raises(RF.SymbolNotTrading):
        RF.fetch_rules(FakeHttp(info_with(BTCUSDT={'status': 'SETTLING'})), symbols=['SOLUSDT', 'BTCUSDT'],
                       clock=lambda: NOW)


@pytest.mark.parametrize('answer,reason', [(WireTimeout(), 'timeout'), ('err_502_html', 'http_5xx'),
                                           ('ok_non_json', 'unreadable_body')])
def test_unreadable_exchange_info_is_typed(answer, reason):
    with pytest.raises(RF.RulesUnavailable) as ei:
        RF.fetch_rules(FakeHttp(answer), symbols=['SOLUSDT'], clock=lambda: NOW)
    assert ei.value.reason == reason


def test_mainnet_is_impossible():
    with pytest.raises(VenueGuardError):
        RF.fetch_rules(FakeHttp('exchange_info'), symbols=['SOLUSDT'], clock=lambda: NOW, environment='mainnet')


def test_bad_symbol_lists_refused():
    for bad in ([], ['SOLUSDT', 'SOLUSDT']):
        with pytest.raises(ValueError):
            RF.fetch_rules(FakeHttp(), symbols=bad, clock=lambda: NOW)


def test_save_is_atomic_and_round_trips(tmp_path):
    snap = RF.fetch_rules(FakeHttp('exchange_info'), symbols=['SOLUSDT'], clock=lambda: NOW)
    p = snap.save(str(tmp_path / 'exchange_rules_testnet.json'))
    text = open(p, encoding='utf-8').read()
    assert text == snap.to_json() and [f.name for f in tmp_path.iterdir()] == ['exchange_rules_testnet.json']
    assert json.loads(text, parse_float=D)['rules_sha256'] == snap.rules_sha256


def test_dumps_exact_never_goes_through_float():
    text = RF.dumps_exact({'a': D('0.1000000000000000055511151231257827'), 'b': D('1E+2'), 'c': D('-0.0')})
    assert '0.1000000000000000055511151231257827' in text and '"b": 100' in text and '"c": 0' in text
