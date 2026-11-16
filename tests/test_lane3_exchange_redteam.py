import json

from newcore.ports.keys import client_id_for
from test_lane3_recovery_redteam import no_network  # noqa: F401 - autouse fixture
from newcore.ports.venue import OrderRef, OutcomeKind, ReadKind
from newcore.venue.binance_usdm_testnet import BinanceUsdMTestnetVenue, HttpResponse, TESTNET_BASE_URL

NOW = 1_799_000_000_000
CID = client_id_for('int_' + '12' * 16)
REF = OrderRef(symbol='BTCUSDT', client_id=CID)


class FakeCredentials:
    def api_key(self):
        return 'fake'

    def sign(self, payload):
        return 'fake'


def make_venue(*docs):
    answers = iter(docs)
    def sender(_request):
        return HttpResponse(200, json.dumps(next(answers)).encode())
    return BinanceUsdMTestnetVenue(environment='testnet', base_url=TESTNET_BASE_URL,
                                  sender=sender, credentials=FakeCredentials(), clock=lambda: NOW)


def test_final_order_missing_execution_quantity_is_unknown():
    # A canceled order may have partially filled before cancellation.
    venue = make_venue(dict(symbol='BTCUSDT', clientOrderId=CID, orderId=123, status='CANCELED'))
    outcome = venue.query(REF)
    assert outcome.kind is OutcomeKind.UNKNOWN
    assert outcome.executed_qty is None


def test_open_partial_order_missing_execution_quantity_is_unknown():
    row = dict(symbol='BTCUSDT', clientOrderId=CID, orderId=123, positionSide='LONG',
               type='LIMIT', status='PARTIALLY_FILLED', origQty='1', stopPrice='0',
               reduceOnly=False, closePosition=False)
    outcome = make_venue([row], []).open_orders('BTCUSDT')
    assert outcome.kind is ReadKind.UNKNOWN
    assert outcome.value is None


def test_non_power_of_ten_step_is_floored_without_leaving_grid():
    from feasibility import size_check
    rule = dict(step=0.25, min_qty=0.25, min_notional=1)
    result = size_check(0.76, 0.76, 100, rule)
    assert result['ok'] is True
    assert result['qty'] == 0.75
