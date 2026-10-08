"""TNET-01 known gap, pinned (xfail strict): on a venue that refuses classic STOP_MARKET (Binance -4120: use the algo
service, as testnet does), a MANAGED lot must stay open, protected by an algo-route PROTECT child (G6: the fallback of
the refused classic attempt). Today the driver uses one route for every draft, so the refusal reaches the core as
Rejected(STOP) and the plan's lot is closed at once (exit.stop_failed). The driver-side fallback (draft `route` +
`route_fallback()`, management lane) flips this test; strict, so the flip is noticed."""
import pytest

from newcore.domain import IntentState, Purpose, ReasonCode
from newcore.ports.keys import route_of
from mg_helpers import MgWorld, intents_of, path, signals

pytestmark = pytest.mark.usefixtures('journal_kind')


def _run(side):
    w = MgWorld(path(24, {}, side), signals(side))
    w.venue.refuse_classic_stops(-4120)
    w.run(8)
    return w


@pytest.mark.parametrize('side', ('LONG', 'SHORT'))
def test_current_behaviour_a_refused_classic_plan_stop_closes_the_lot(side):
    """What happens today (the gap): classic refused -> the core closes the lot at market; never naked."""
    w = _run(side)
    r = w.runner
    stops = intents_of(r, Purpose.PROTECT)
    assert [route_of(s.intent_id, s.intent.client_order_id) for s in stops] == ['classic']
    assert stops[0].state is IntentState.REJECTED
    close, = intents_of(r, Purpose.CLOSE)
    assert close.intent.reason is ReasonCode.EXIT_STOP_FAILED and close.executed > 0
    assert not r.fold.open_lots() and r.counters.unprotected_cycles == 0


@pytest.mark.xfail(strict=True, reason='TNET-01: managed stops have no algo-route fallback yet (driver draft route + '
                                       'route_fallback(), management lane); a refused classic stop closes the lot')
@pytest.mark.parametrize('side', ('LONG', 'SHORT'))
def test_tnet01_a_refused_classic_plan_stop_falls_back_to_an_algo_protect_child(side):
    w = _run(side)
    r = w.runner
    lot, = r.fold.open_lots()                                       # still open: no stop-failed close
    stops = intents_of(r, Purpose.PROTECT)
    assert [route_of(s.intent_id, s.intent.client_order_id) for s in stops][:2] == ['classic', 'algo']
    assert lot.carrier.state is IntentState.WORKING and lot.carrier.intent.qty >= lot.qty
    assert not intents_of(r, Purpose.CLOSE) and r.counters.unprotected_cycles == 0
    assert lot.carrier.intent.stop_price == stops[0].intent.stop_price   # the plan level, same as the refused one
