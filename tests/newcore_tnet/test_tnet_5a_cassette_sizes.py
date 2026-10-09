"""Official 5a at 74dc47c: 8/9 PASS, T11 FAIL, every cassette (preflight + 9) refused with CassetteLeak.

Proven offline (real 1297ee3 cassettes re-audited with the 74dc47c code): the factory boot's exchangeInfo body holds
25,334 base64-like runs in ONE string (742 symbols x ~34 runs: 13-digit dates, ids, precisions), above the per-string
MAX_DECODED_TOKENS = 20,000 that c958811 left unchanged while it lowered the run minimum from 16 to 11 characters. Every
cassette starts with that boot, so every cassette was refused at save. The recorder itself never failed during the
run; it still could have (an unrecordable API key raised CassetteLeak straight into the transport), so it is now
isolated: a recorder failure only refuses the cassette at save, never changes what a venue read or an order sees.

Bodies here are built in the REAL shapes (exchangeInfo symbol entries, the account-wide positionRisk of 1,486 rows,
the account positions) and go through the same recorder + fault seam + factory the testnet target uses."""
import base64
import json

import pytest

from fake_binance import FakeBinance
from fake_binance import ok as fok
from ncv_support import DUMMY_KEY, DUMMY_SECRET, FakeHttp
from test_ncv_cassette_leaks import ok, req
from tnet_support import World

from newcore.tnet.recording import run_recorded_suite
from newcore.tnet.rspec import bundled
from newcore.venue import cassette as C
from newcore.venue.cassette import CassetteLeak, CassetteRecorder

N_SYMBOLS = 742                 # the testnet exchangeInfo of the official run
N_POSITION_ROWS = 1486          # its account-wide positionRisk (both hedge sides of every symbol)


def _name(i):
    letters = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        letters = chr(65 + r) + letters
    return f'{letters}USDT'


def symbol_entry(i):
    """One exchangeInfo symbol in the testnet's real shape (BTCUSDT's entry, renamed)."""
    s = _name(i)
    return {'symbol': s, 'pair': s, 'contractType': 'PERPETUAL', 'deliveryDate': 4133404802000,
            'onboardDate': 1569398400000 + i * 86_400_000, 'status': 'TRADING', 'maintMarginPercent': '2.5000',
            'requiredMarginPercent': '5.0000', 'baseAsset': s[:-4], 'quoteAsset': 'USDT', 'marginAsset': 'USDT',
            'pricePrecision': 2, 'quantityPrecision': 4, 'baseAssetPrecision': 8, 'quotePrecision': 8,
            'underlyingType': 'COIN', 'underlyingSubType': [], 'triggerProtect': '0.0500',
            'liquidationFee': '0.020000', 'marketTakeBound': '0.30', 'maxMoveOrderLimit': 1000,
            'filters': [{'minPrice': '261.10', 'filterType': 'PRICE_FILTER', 'maxPrice': '809484', 'tickSize': '0.10'},
                        {'stepSize': '0.0001', 'minQty': '0.0001', 'filterType': 'LOT_SIZE', 'maxQty': '1000'},
                        {'stepSize': '0.0001', 'maxQty': '120', 'filterType': 'MARKET_LOT_SIZE',
                         'minQty': '0.0001'},
                        {'limit': 10000, 'filterType': 'MAX_NUM_ORDERS'},
                        {'notional': '50', 'filterType': 'MIN_NOTIONAL'},
                        {'multiplierDown': '0.9500', 'filterType': 'PERCENT_PRICE', 'multiplierUp': '1.0500',
                         'multiplierDecimal': '4'}],
            'orderTypes': ['LIMIT', 'MARKET', 'STOP', 'STOP_MARKET', 'TAKE_PROFIT', 'TAKE_PROFIT_MARKET',
                           'TRAILING_STOP_MARKET'],
            'timeInForce': ['GTC', 'IOC', 'FOK', 'GTX', 'GTD']}


def position_row(i):
    """One account-wide positionRisk row in the real shape (a flat symbol, hedge side by parity)."""
    return {'symbol': _name(i // 2), 'positionAmt': '0', 'entryPrice': '0.0', 'breakEvenPrice': '0.0',
            'markPrice': '0.00000000', 'unRealizedProfit': '0.00000000', 'liquidationPrice': '0', 'leverage': '20',
            'maxNotionalValue': '25000', 'marginType': 'cross', 'isolatedMargin': '0.00000000',
            'isAutoAddMargin': 'false', 'positionSide': 'SHORT' if i % 2 else 'LONG', 'notional': '0',
            'isolatedWallet': '0', 'updateTime': 0, 'isolated': False}


def account_position(i):
    return {'symbol': _name(i // 2), 'initialMargin': '0', 'maintMargin': '0', 'unrealizedProfit': '0.00000000',
            'positionInitialMargin': '0', 'openOrderInitialMargin': '0', 'leverage': '20', 'isolated': False,
            'entryPrice': '0.0', 'breakEvenPrice': '0.0', 'maxNotional': '60000',
            'positionSide': 'SHORT' if i % 2 else 'LONG', 'positionAmt': '0', 'notional': '0', 'isolatedWallet': '0',
            'updateTime': 0, 'bidNotional': '0', 'askNotional': '0'}


class RealShapedBinance(FakeBinance):
    """FakeBinance answering the account-wide reads and exchangeInfo at the testnet's real size and shape (the
    fake's own SOLUSDT rows stay first, so the scenarios behave exactly as on the plain fake)."""

    def _get_fapi_v1_exchangeInfo(self, q):
        doc = json.loads(super()._get_fapi_v1_exchangeInfo(q).body)
        have = {s['symbol'] for s in doc['symbols']}
        doc['symbols'] += [e for e in map(symbol_entry, range(N_SYMBOLS)) if e['symbol'] not in have]
        return fok(doc)

    def _get_fapi_v2_positionRisk(self, q):
        r = super()._get_fapi_v2_positionRisk(q)
        if q.get('symbol'):
            return r
        rows = json.loads(r.body)
        have = {x['symbol'] for x in rows}
        return fok(rows + [x for x in map(position_row, range(N_POSITION_ROWS)) if x['symbol'] not in have])

    def _get_fapi_v2_account(self, q):
        doc = json.loads(super()._get_fapi_v2_account(q).body)
        doc['positions'] = list(doc.get('positions', [])) + [account_position(i) for i in range(N_POSITION_ROWS)]
        return fok(doc)


def _runs(text):
    return sum(1 for _ in C._B64_RUN.finditer(text)) + sum(1 for _ in C._HEX_RUN.finditer(text))


def test_the_real_shaped_exchange_info_is_above_the_old_per_string_cap():
    """The shape matters: one exchangeInfo body is ONE string leaf with more runs than 74dc47c allowed (20,000)."""
    body = RealShapedBinance()._get_fapi_v1_exchangeInfo({}).body.decode()
    assert _runs(body) > 20_000
    assert _runs(body) < C.MAX_DECODED_TOKENS == 50_000
    assert C.MAX_AUDIT_RUNS == 4_000_000                   # the whole-cassette budget is not derived from it


def _junk(n):
    return ' '.join('QUJDREVGR0hJSktMTU5PUA' for _ in range(n))


def test_exactly_the_per_string_cap_is_audited_and_one_more_is_refused():
    rec = CassetteRecorder(FakeHttp(ok(json.dumps({'msg': _junk(C.MAX_DECODED_TOKENS)}))),
                           redact=(DUMMY_KEY, DUMMY_SECRET))
    rec(req())
    assert json.loads(rec.to_json())['interactions']
    rec = CassetteRecorder(FakeHttp(ok(json.dumps({'msg': _junk(C.MAX_DECODED_TOKENS + 1)}))),
                           redact=(DUMMY_KEY, DUMMY_SECRET))
    rec(req())
    with pytest.raises(CassetteLeak, match='too many encoded runs'):
        rec.to_json()


def test_a_real_shaped_boot_cassette_is_produced():
    """Repro (fails on 74dc47c: 'too many encoded runs'): the factory boot's exchangeInfo + account-wide reads."""
    fb = RealShapedBinance()
    rec = CassetteRecorder(fb, redact=(DUMMY_KEY, DUMMY_SECRET))
    for path in ('/fapi/v1/exchangeInfo', '/fapi/v2/positionRisk', '/fapi/v2/account'):
        rec(req(url='https://testnet.binancefuture.com' + path, method='GET'))
    doc = json.loads(rec.to_json())
    assert len(doc['interactions']) == 3


def test_a_secret_hidden_in_a_real_shaped_body_is_still_refused():
    """The raised cap keeps the encoded-form audit: a base64 echo of the secret at the END of the largest body."""
    hidden = base64.b64encode(DUMMY_SECRET.encode()).decode()
    big = RealShapedBinance()._get_fapi_v1_exchangeInfo({}).body.decode()     # one string leaf, > 20,000 runs
    rec = CassetteRecorder(FakeHttp(ok(json.dumps({'msg': big + ' ' + hidden}))), redact=(DUMMY_KEY, DUMMY_SECRET))
    rec(req())
    with pytest.raises(CassetteLeak, match='encoded form'):
        rec.to_json()


def _suite(tmp_path, ids):
    w = World()
    w.fb = RealShapedBinance(now_ms=w.fb.now)
    specs = [s for s in bundled() if s['id'] in ids]
    return run_recorded_suite(specs, lambda rec: w.target(recorder=rec), run_nonce='r5a', cassette_dir=str(tmp_path),
                              redact=(DUMMY_KEY, DUMMY_SECRET), monotonic=w.monotonic)


def test_t11_on_real_shaped_bodies_passes_and_every_cassette_is_written(tmp_path):
    """Repro of the 5a refusal through the testnet seam (fails on 74dc47c: errors [('preflight', 'CassetteLeak'),
    ('T11', 'CassetteLeak')]): the recorded run on real-shaped bodies writes the preflight and the T11 cassette."""
    res, pre, errs = _suite(tmp_path, {'T11'})
    assert errs == [] and pre
    (r,) = res.scenarios
    assert r.verdict == 'PASS' and r.cassette, (r.verdict, r.error, r.assertions)
