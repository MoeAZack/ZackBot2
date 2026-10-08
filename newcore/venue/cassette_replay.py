"""Replay a REAL recorded cassette (e.g. an S5 smoke cassette) through the scenario machinery, interaction by interaction.

Each recorded interaction is turned back into the transport call that produced it (from its method, path and recorded
query parameters) and becomes one scenario Step. The steps run over a CassettePlayer with other dummy credentials and a
fixed clock, exactly like scenarios.replay_cassette(): the player checks method, URL, signed flag and every non-volatile
parameter, and the transport parses the recorded answer into a typed outcome. An interaction PASSES when the rebuilt
request matches and a typed outcome comes back; the first failure stops the replay (the player is order-sensitive) and
every later interaction is reported SKIPPED. A name-based leak audit of the whole cassette runs as well.
"""
import json
from dataclasses import dataclass
from decimal import Decimal

from .cassette import CASSETTE_FORMAT, CassetteLeak, CassettePlayer, CassetteRecorder
from .scenarios import REPLAY_KEY, REPLAY_SECRET, T0, Scenario, Step, _venue
from .transport import CLOSING_SIDE, StopRoute


class UnsupportedInteraction(Exception):
    pass


@dataclass(frozen=True)
class InteractionResult:
    index: int
    label: str
    status: str                 # PASS | FAIL | SKIPPED
    detail: str


def _int(q, k):
    return int(q[k]) if k in q else None


def _call_for(req):
    """The transport call that produced a recorded request: (label, callable(transport) -> outcome)."""
    m, path = req['method'], req['url'].split('binancefuture.com', 1)[-1]
    q = {k: v for k, v in req['query']}
    label = f'{m} {path}'
    if m == 'GET':
        simple = {'/fapi/v1/time': lambda t: t.server_time(), '/fapi/v1/exchangeInfo': lambda t: t.exchange_info(),
                  '/fapi/v2/account': lambda t: t.account(), '/fapi/v1/positionSide/dual':
                  lambda t: t.dual_side_position(), '/fapi/v2/positionRisk': lambda t: t.positions(q.get('symbol')),
                  '/fapi/v1/openOrders': lambda t: t.open_orders(q.get('symbol')),
                  '/fapi/v1/openAlgoOrders': lambda t: t.open_algo_orders(q.get('symbol')),
                  '/fapi/v1/order': lambda t: t.query_order(q['symbol'], q['origClientOrderId']),
                  '/fapi/v1/algoOrder': lambda t: t.query_algo_order(q['clientAlgoId']),
                  '/fapi/v1/klines': lambda t: t.klines(q['symbol'], q['interval'], start_ms=_int(q, 'startTime'),
                                                        end_ms=_int(q, 'endTime'), limit=_int(q, 'limit')),
                  '/fapi/v1/income': lambda t: t.income(symbol=q.get('symbol'), income_type=q.get('incomeType'),
                                                        start_ms=_int(q, 'startTime'), end_ms=_int(q, 'endTime'),
                                                        limit=_int(q, 'limit')),
                  '/fapi/v1/userTrades': lambda t: t.user_trades(q['symbol'], order_id=_int(q, 'orderId'),
                                                                 start_ms=_int(q, 'startTime'),
                                                                 end_ms=_int(q, 'endTime'), from_id=_int(q, 'fromId'),
                                                                 limit=_int(q, 'limit'))}
        if path in simple:
            return label, simple[path]
    if m == 'DELETE' and path == '/fapi/v1/order':
        return label, lambda t: t.cancel_order(q['symbol'], q['origClientOrderId'])
    if m == 'DELETE' and path == '/fapi/v1/algoOrder':
        return label, lambda t: t.cancel_algo_order(q['clientAlgoId'])
    if m == 'POST' and path in ('/fapi/v1/order', '/fapi/v1/algoOrder'):
        if 'positionSide' not in q:
            raise UnsupportedInteraction(f'{label}: a one-way-mode order cannot be replayed (hedge mode only)')
        ps = q['positionSide']
        if path == '/fapi/v1/algoOrder':
            return label + ' (algo stop)', lambda t: t.place_stop_market(
                q['symbol'], ps, Decimal(q['quantity']), Decimal(q['triggerPrice']), q['clientAlgoId'],
                route=StopRoute.ALGO)
        if q.get('type') == 'STOP_MARKET':
            return label + ' (stop)', lambda t: t.place_stop_market(
                q['symbol'], ps, Decimal(q['quantity']), Decimal(q['stopPrice']), q['newClientOrderId'],
                route=StopRoute.CLASSIC)
        if q.get('type') == 'MARKET':
            return label + ' (market)', lambda t: t.place_market(
                q['symbol'], q['side'], ps, Decimal(q['quantity']), q['newClientOrderId'],
                reduce_only=q['side'] == CLOSING_SIDE[ps])
    raise UnsupportedInteraction(f'{label}: not a replayable endpoint')


def scenario_from_cassette(doc):
    steps = []
    for i, it in enumerate(doc['interactions']):
        label, call = _call_for(it['request'])
        steps.append(Step(f'#{i} {label}', (lambda c: lambda v: c(v.transport))(call), ()))
    return Scenario('recorded cassette', 'rebuilt from a recorded cassette', steps)


def leak_audit(doc):
    """Name-based audit of a cassette file (no secret values are known here): None if clean, else the reason."""
    rec = CassetteRecorder(lambda r: None)
    rec.interactions = doc['interactions']
    try:
        rec._audit(json.dumps(doc, ensure_ascii=False))
    except CassetteLeak as ex:
        return str(ex)
    return None


def _outcome_text(out):
    kind = getattr(getattr(out, 'kind', None), 'value', None)
    reason = getattr(out, 'unknown_reason', None)
    return f'{kind}' + (f' ({reason})' if reason else '')


def replay_report(doc):
    """[InteractionResult, ...] for every interaction of the cassette document."""
    if not isinstance(doc, dict) or doc.get('format') != CASSETTE_FORMAT or not isinstance(doc.get('interactions'),
                                                                                          list):
        raise ValueError(f'not a {CASSETTE_FORMAT} cassette')
    items = doc['interactions']
    results, failed = [], False
    player = CassettePlayer(doc)
    venue = _venue(player, REPLAY_KEY, REPLAY_SECRET, T0 + 5000)
    for i, it in enumerate(items):
        req = it.get('request', {}) if isinstance(it, dict) else {}
        label = f"#{i} {req.get('method', '?')} {str(req.get('url', '?')).split('binancefuture.com', 1)[-1]}"
        if failed:
            results.append(InteractionResult(i, label, 'SKIPPED', 'after an earlier failure'))
            continue
        try:
            label, call = _call_for(req)
            label = f'#{i} {label}'
            out = call(venue.transport)
            results.append(InteractionResult(i, label, 'PASS', _outcome_text(out)))
        except Exception as ex:                # unsupported / mismatch / seam error: this interaction fails
            failed = True
            results.append(InteractionResult(i, label, 'FAIL', f'{type(ex).__name__}: {ex}'[:200]))
    if not failed and player.remaining:
        results.append(InteractionResult(len(items), 'end', 'FAIL', f'{player.remaining} interaction(s) not replayed'))
    return results
