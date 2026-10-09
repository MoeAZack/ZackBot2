"""VenuePort.order_by_id conformance: the SAME contract cases against FakeVenue (slice S1) and TestnetVenue (fake
HTTP). FakeVenue lives on nc-s1-slice (newcore.adapters); until that branch is merged here its column is skipped,
never faked."""
import json
from decimal import Decimal as D

import pytest

from newcore.ports import keys as K
from newcore.ports import venue as P
from newcore.venue.credentials import StaticCredentials
from newcore.venue.testnet_venue import TestnetVenue
from newcore.venue.transport import BinanceTestnetTransport, PositionMode

from ncv_support import DUMMY_KEY, DUMMY_SECRET, NOW_MS, FakeHttp, fixture, raw

SYM = 'SOLUSDT'
CID = K.client_id_for('int_' + '5' * 32, 'classic')
H4 = 14_400_000
T0 = 1_704_067_200_000


def _testnet(case):
    """-> (venue, exchange order id of the resting order): a NEW classic stop, 2 requested, nothing executed."""
    b = json.loads(fixture('order_query_filled').body)
    b.update(clientOrderId=CID, status='NEW', type='STOP_MARKET', origQty='2', executedQty='0', avgPrice='0',
             side='SELL', reduceOnly=True)
    eoid = str(b['orderId'])
    script = {'known': [raw(200, json.dumps(b).encode())],
              'missing': ['err_no_such_order', 'err_no_such_order'],
              'other_symbol': ['err_no_such_order', 'err_no_such_order']}[case]
    t = BinanceTestnetTransport(environment='testnet', http=FakeHttp(*script), clock=lambda: NOW_MS,
                                position_mode=PositionMode.HEDGE,
                                credentials=StaticCredentials(DUMMY_KEY, DUMMY_SECRET))
    return TestnetVenue(t, lambda: NOW_MS), eoid


def _fake(case):
    fv = pytest.importorskip('newcore.adapters.fake_venue', reason='FakeVenue.order_by_id is on nc-s1-slice')
    from newcore.ports.bars import Bar
    bars = tuple(Bar(open_ms=T0 + i * H4, close_ms=T0 + (i + 1) * H4, open=D(100), high=D('100.5'), low=D('99.5'),
                     close=D(100), volume=D(1000)) for i in range(5))
    v = fv.FakeVenue({SYM: bars}, H4)
    o = v._new_order(P.OrderRef(symbol=SYM, client_id=CID), 'STOP_MARKET', 'LONG', D('2'), True, D('90'))
    return v, o.exchange_order_id


VENUES = {'fake': _fake, 'testnet': _testnet}


@pytest.fixture(params=sorted(VENUES))
def make(request):
    return VENUES[request.param]


def test_is_a_venue_port(make):
    v, _ = make('known')
    assert isinstance(v, P.VenuePort) and callable(v.order_by_id)


def test_known_order_is_exactly_the_one_asked(make):
    v, eoid = make('known')
    out = v.order_by_id(SYM, eoid)
    assert out.kind is P.ReadKind.OK and out.error_code is None and type(out.value) is tuple
    rec, = out.value
    assert isinstance(rec, P.VenueOrderRecord)
    assert (rec.ref.symbol, rec.ref.client_id, rec.ref.route, rec.exchange_order_id) == (SYM, CID, 'classic', eoid)
    assert (rec.position_side, rec.status, rec.orig_qty, rec.executed_qty) == ('LONG', 'NEW', D('2'), D('0'))
    assert type(rec.orig_qty) is D and type(rec.executed_qty) is D


def test_missing_order_is_rejected_2013_never_empty(make):
    v, eoid = make('missing')
    out = v.order_by_id(SYM, str(int(eoid) + 777))
    assert out.kind is P.ReadKind.REJECTED and out.error_code == -2013 and out.value is None


def test_order_of_another_symbol_is_not_found(make):
    v, eoid = make('other_symbol')
    out = v.order_by_id('BTCUSDT', eoid)
    assert out.kind is P.ReadKind.REJECTED and out.error_code == -2013 and out.value is None
