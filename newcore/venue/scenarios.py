"""S5 cassette harness: scripted scenarios that drive TestnetVenue (the frozen VenuePort) over recorded-shape fake HTTP.

No network. Each Scenario is a list of Steps; a Step is ONE port call, the venue answers it is allowed to consume, and the
outcome fields it must produce. run_scenario():
  1. drives a TestnetVenue whose transport sends through a ScriptedHttp wrapped in a CassetteRecorder;
  2. checks every step consumed exactly its scripted answers (one request per order call: no hidden retry / fallback)
     and produced the expected outcome;
  3. serializes the sanitized cassette (CassetteRecorder.to_json: fail-closed leak audit);
  4. replays the cassette through a FRESH TestnetVenue (other dummy key, other clock) and requires identical outcomes.

Client ids are derived exactly as the Runner will (newcore.ports.keys): one id per intent, the classic -> algo stop
fallback is a NEW protect intent (child ordinal + 1) with client_id_for(id, 'algo').
The answers are hand-written in Binance's documented shapes; the same runner replays a REAL S5 cassette later
(replay_cassette) once the owner records one.
"""
import json
from dataclasses import dataclass, field
from decimal import Decimal

from newcore.ports import keys as K
from newcore.ports import venue as P

from .cassette import CassettePlayer, CassetteRecorder
from .credentials import StaticCredentials
from .testnet_venue import HedgeModeRequired, TestnetVenue, VenueBootUnknown
from .transport import BinanceTestnetTransport, PositionMode
from .wire import HttpResponse, WireSeamError, WireTimeout

# Obvious dummies (never real): the scenarios sign with them, the cassette must not contain them.
SCENARIO_KEY = 'SCENARIODUMMYKEY' + 'k' * 48
SCENARIO_SECRET = 'SCENARIODUMMYSECRET' + 's' * 45
REPLAY_KEY = 'REPLAYDUMMYKEY' + 'r' * 50
REPLAY_SECRET = 'REPLAYDUMMYSECRET' + 'q' * 47
T0 = 1759924800000                         # a 4h candle close (on the grid)
ACCOUNT = 'acct_' + '5c' * 16
SYMBOL = 'SOLUSDT'


class UnscriptedRequest(WireSeamError):
    """A step sent a request nobody scripted (a hidden retry / fallback). Propagates through the transport."""


class ScenarioFailed(AssertionError):
    pass


# ---------------------------------------------------------------------------------------------- the Runner's ids
ENTRY_KEY = K.decision_key('trend_ema_mom', 'v1', '4h', SYMBOL, 'LONG', T0, 'entry')
ENTRY_INTENT = K.derive_intent_id(ACCOUNT, ENTRY_KEY)
LOT = K.derive_lot_id(ACCOUNT, ENTRY_INTENT)
STOP_INTENT = K.derive_child_intent_id(ACCOUNT, ENTRY_INTENT, 'protect', 0)
STOP_ALGO_INTENT = K.derive_child_intent_id(ACCOUNT, ENTRY_INTENT, 'protect', 1)     # the fallback = a NEW intent
CLOSE_INTENT = K.derive_child_intent_id(ACCOUNT, LOT, 'close', 0)

ENTRY_REF = P.OrderRef(symbol=SYMBOL, client_id=K.client_id_for(ENTRY_INTENT))
STOP_REF = P.OrderRef(symbol=SYMBOL, client_id=K.client_id_for(STOP_INTENT))
STOP_ALGO_REF = P.OrderRef(symbol=SYMBOL, client_id=K.client_id_for(STOP_ALGO_INTENT, 'algo'), route='algo')
CLOSE_REF = P.OrderRef(symbol=SYMBOL, client_id=K.client_id_for(CLOSE_INTENT))


# ---------------------------------------------------------------------------------------------- recorded-shape answers
def answer(obj, status=200, weight=1):
    return HttpResponse(status, {'Content-Type': 'application/json', 'X-MBX-USED-WEIGHT-1M': str(weight)},
                        json.dumps(obj).encode())


def error(code, msg, status=400):
    return answer({'code': code, 'msg': msg}, status)


def order(ref, *, status, side, position_side='LONG', type_='MARKET', qty='1', executed='0', avg='0', order_id=7001,
          stop='0'):
    return answer({'orderId': order_id, 'symbol': ref.symbol, 'status': status, 'clientOrderId': ref.client_id,
                   'price': '0', 'avgPrice': avg, 'origQty': qty, 'executedQty': executed,
                   'cumQuote': str(Decimal(executed) * Decimal(avg)), 'timeInForce': 'GTC', 'type': type_,
                   'origType': type_, 'reduceOnly': False, 'closePosition': False, 'side': side,
                   'positionSide': position_side, 'stopPrice': stop, 'workingType': 'MARK_PRICE',
                   'priceProtect': False, 'updateTime': T0 + 1000})


def algo(ref, *, status, side='SELL', position_side='LONG', qty='1', trigger='200', algo_id=9001, child=None):
    body = {'algoId': algo_id, 'clientAlgoId': ref.client_id, 'algoType': 'CONDITIONAL', 'orderType': 'STOP_MARKET',
            'symbol': ref.symbol, 'side': side, 'positionSide': position_side, 'quantity': qty,
            'algoStatus': status, 'triggerPrice': trigger, 'workingType': 'MARK_PRICE', 'reduceOnly': True,
            'closePosition': False, 'createTime': T0 + 1000, 'updateTime': T0 + 2000}
    if child is not None:
        body.update(actualOrderId=str(child), actualPrice=trigger)
    return answer(body)


def algo_ack(ref, algo_id=9001):
    return answer({'algoId': algo_id, 'clientAlgoId': ref.client_id, 'code': '200', 'msg': 'success'})


def dual(on):
    return answer({'dualSidePosition': on})


# ---------------------------------------------------------------------------------------------- scenario model
@dataclass
class Step:
    label: str
    call: object                      # callable(TestnetVenue) -> outcome
    answers: tuple                    # the venue answers this step may consume (exactly)
    expect: dict = field(default_factory=dict)      # outcome attribute -> value
    raises: object = None             # expected exception type (boot checks)


@dataclass
class Scenario:
    name: str
    description: str
    steps: list


@dataclass
class StepResult:
    label: str
    requests: int
    outcome: object


@dataclass
class ScenarioReport:
    name: str
    steps: list
    cassette: str
    replayed: bool


class ScriptedHttp:
    """Answers in order; counts requests so a step that sends more (or fewer) than scripted is caught."""

    def __init__(self, answers):
        self._answers = list(answers)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if not self._answers:
            raise UnscriptedRequest(f'unscripted request {request.method} {request.url}')
        a = self._answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        return a

    @property
    def remaining(self):
        return len(self._answers)


def _venue(http, key, secret, clock_ms):
    t = BinanceTestnetTransport(environment='testnet', http=http, clock=lambda: clock_ms,
                                position_mode=PositionMode.HEDGE, credentials=StaticCredentials(key, secret))
    return TestnetVenue(t, lambda: clock_ms, sleep=lambda _s: None)


def _run_step(step, venue):
    if step.raises is not None:
        try:
            step.call(venue)
        except step.raises as ex:
            return type(ex).__name__
        raise ScenarioFailed(f'{step.label}: expected {step.raises.__name__}')
    return step.call(venue)


def _check(step, outcome):
    if step.raises is not None:
        return
    for attr, want in step.expect.items():
        got = getattr(outcome, attr)
        if attr == 'kind':
            got = got.value
        if got != want:
            raise ScenarioFailed(f'{step.label}: {attr} = {got!r}, expected {want!r}')


def run_scenario(scenario):
    answers = [a for s in scenario.steps for a in s.answers]
    http = ScriptedHttp(answers)
    recorder = CassetteRecorder(http, redact=(SCENARIO_SECRET,), note=f'scenario {scenario.name}')
    venue = _venue(recorder, SCENARIO_KEY, SCENARIO_SECRET, T0 + 5000)
    results = []
    for step in scenario.steps:
        before = len(http.requests)
        outcome = _run_step(step, venue)
        sent = len(http.requests) - before
        if sent != len(step.answers):
            raise ScenarioFailed(f'{step.label}: sent {sent} request(s), scripted {len(step.answers)}')
        _check(step, outcome)
        results.append(StepResult(step.label, sent, outcome))
    if http.remaining:
        raise ScenarioFailed(f'{http.remaining} scripted answer(s) were never requested')
    cassette = recorder.to_json()
    for v in (SCENARIO_KEY, SCENARIO_SECRET):
        if v in cassette:
            raise ScenarioFailed('the cassette holds a dummy credential')
    replay_cassette(scenario, cassette, [r.outcome for r in results])
    return ScenarioReport(scenario.name, results, cassette, True)


def replay_cassette(scenario, cassette, expected_outcomes=None):
    """Drive the scenario's calls over a cassette (scripted or a REAL recorded one) with a fresh venue and other dummy
    credentials. Returns the outcomes; with expected_outcomes, requires them to be identical."""
    player = CassettePlayer(json.loads(cassette))
    venue = _venue(player, REPLAY_KEY, REPLAY_SECRET, T0 + 5000)
    outcomes = []
    for i, step in enumerate(scenario.steps):
        out = _run_step(step, venue)
        _check(step, out)
        if expected_outcomes is not None and out != expected_outcomes[i]:
            raise ScenarioFailed(f'{step.label}: replay differs from the recording')
        outcomes.append(out)
    player.assert_exhausted()
    return outcomes


# ---------------------------------------------------------------------------------------------- the scenarios
def _market(ref, reduce, qty='1'):
    return lambda v: v.submit_market(P.MarketOrder(ref=ref, position_side='LONG', qty=Decimal(qty), reduce=reduce))


def _stop(ref, qty='1', price='200'):
    return lambda v: v.submit_stop(P.StopOrder(ref=ref, position_side='LONG', qty=Decimal(qty),
                                               stop_price=Decimal(price)))


LIFECYCLE = Scenario('lifecycle_classic', 'hedge check -> entry -> reduce-only stop -> query -> cancel -> close', [
    Step('boot: account is in hedge mode', lambda v: v.check_hedge_mode(), (dual(True),)),
    Step('entry: market BUY LONG fills', _market(ENTRY_REF, False),
         (order(ENTRY_REF, status='FILLED', side='BUY', executed='1', avg='210.5'),),
         dict(kind='final', executed_qty=Decimal('1'), avg_price=Decimal('210.5'))),
    Step('protect: classic reduce-only STOP_MARKET rests', _stop(STOP_REF),
         (order(STOP_REF, status='NEW', side='SELL', type_='STOP_MARKET', stop='200', order_id=7002),),
         dict(kind='known', status='NEW', executed_qty=None)),
    Step('query the stop by client id', lambda v: v.query(STOP_REF),
         (order(STOP_REF, status='NEW', side='SELL', type_='STOP_MARKET', stop='200', order_id=7002),),
         dict(kind='known', exchange_order_id='7002')),
    Step('cancel the stop before the close', lambda v: v.cancel(STOP_REF),
         (order(STOP_REF, status='CANCELED', side='SELL', type_='STOP_MARKET', stop='200', order_id=7002),),
         dict(kind='final', executed_qty=Decimal('0'), avg_price=None)),
    Step('close: market SELL LONG (reduce) fills', _market(CLOSE_REF, True),
         (order(CLOSE_REF, status='FILLED', side='SELL', executed='1', avg='211.0', order_id=7003),),
         dict(kind='final', executed_qty=Decimal('1'))),
])

ALGO_ROUTE = Scenario('algo_route', 'classic stop refused -4120 -> NEW protect intent on the algo route', [
    Step('protect (classic) is refused with an algo hint, ONE request', _stop(STOP_REF),
         (error(-4120, 'Order type not supported for this endpoint. Please use the Algo Order API endpoints instead.'),),
         dict(kind='rejected', error_code=-4120, detail='algo_route')),
    Step('protect (algo, new intent, algo client id) rests', _stop(STOP_ALGO_REF),
         (algo(STOP_ALGO_REF, status='NEW'),), dict(kind='known', exchange_order_id='9001')),
    Step('query the algo stop', lambda v: v.query(STOP_ALGO_REF), (algo(STOP_ALGO_REF, status='NEW'),),
         dict(kind='known', status='NEW')),
    # Codex P1(b) (first testnet P1 probe): the DELETE ack alone is not cancellation truth - the by-id record is polled
    # until terminal, and the real testnet still said NEW right after the ack.
    Step('cancel the algo stop: ack, still NEW, then CANCELED by id', lambda v: v.cancel(STOP_ALGO_REF),
         (algo_ack(STOP_ALGO_REF), algo(STOP_ALGO_REF, status='NEW'), algo(STOP_ALGO_REF, status='CANCELED')),
         dict(kind='final', executed_qty=Decimal('0'))),
    Step('confirm: CANCELED, nothing executed', lambda v: v.query(STOP_ALGO_REF),
         (algo(STOP_ALGO_REF, status='CANCELED'),), dict(kind='final', executed_qty=Decimal('0'))),
    Step('a triggered algo stop points at its child order', lambda v: v.query(STOP_ALGO_REF),
         (algo(STOP_ALGO_REF, status='FINISHED', child=7777),),
         dict(kind='known', exchange_order_id='7777', detail='algo_triggered', executed_qty=None)),
])

DUPLICATE = Scenario('duplicate_client_id', '-4116 on a resend means the order EXISTS -> query by client id', [
    Step('entry resend is refused as a duplicate: UNKNOWN, not rejected', _market(ENTRY_REF, False),
         (error(-4116, 'ClientOrderId is duplicated.'),),
         dict(kind='unknown', detail='duplicate_client_id', executed_qty=None)),
    Step('query by client id finds the filled original', lambda v: v.query(ENTRY_REF),
         (order(ENTRY_REF, status='FILLED', side='BUY', executed='1', avg='210.5'),),
         dict(kind='final', executed_qty=Decimal('1'))),
])

NOT_FOUND_THEN_FINAL = Scenario('not_found_then_final', 'lost answer -> NOT_FOUND books nothing -> later FINAL', [
    Step('entry answer lost: UNKNOWN', _market(ENTRY_REF, False), (WireTimeout(),),
         dict(kind='unknown', detail='timeout', executed_qty=None)),
    Step('query: not visible yet -> NOT_FOUND with evidence, books nothing', lambda v: v.query(ENTRY_REF),
         (error(-2013, 'Order does not exist.'),),
         dict(kind='not_found', error_code=-2013, detail='query_no_such_order', executed_qty=None)),
    Step('query again: FINAL with the executed quantity', lambda v: v.query(ENTRY_REF),
         (order(ENTRY_REF, status='FILLED', side='BUY', executed='1', avg='210.5'),),
         dict(kind='final', executed_qty=Decimal('1'))),
])

HEDGE_MODE = Scenario('hedge_mode_check', 'boot refuses a one-way account and an unreadable position mode', [
    Step('one-way account is refused', lambda v: v.check_hedge_mode(), (dual(False),), raises=HedgeModeRequired),
    Step('unreadable mode is refused (never assumed fine)', lambda v: v.check_hedge_mode(),
         (error(-1000, 'An unknown error occurred while processing the request.', status=500),),
         raises=VenueBootUnknown),
    Step('hedge account passes', lambda v: v.check_hedge_mode(), (dual(True),)),
])

SCENARIOS = (LIFECYCLE, ALGO_ROUTE, DUPLICATE, NOT_FOUND_THEN_FINAL, HEDGE_MODE)


def run_all():
    return [run_scenario(s) for s in SCENARIOS]
