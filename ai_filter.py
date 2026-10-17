"""Optional Claude review of each entry signal. Claude can only VETO a trade, never create or resize one.
If the API key is missing or the call fails, the trade is taken (the backtested rules stay in charge)."""
import json, re, requests

PROMPT_VERSION = 'v3-structured'
PROMPT = """You are a cautious second reader for a systematic crypto futures bot. Its backtested rules already produced
a {side} signal. You see ONLY the indicator values and candles below - no news, no exchange announcements, no listing
status, no order book. Do not claim knowledge of events you were not given. Your only job is to veto a trade when the
data below shows a clear, concrete problem (for example: price extremely stretched from its averages in the trade's
direction, a single outsized candle making the whole move, or funding strongly against the {side_l}).
When in doubt, TAKE the trade.

Signal: {sleeve} on {symbol}, {tf} candles, candle closed {time} UTC
Close {close}  EMA20 {e20}  EMA50 {e50}  EMA200 {e200}  ATR {atr}
Return over the last 42 candles {ret42}, last 180 candles {ret180}  Supertrend {st}
Funding rate (8h): {funding}
Last 12 {tf} candles (open, high, low, close): {candles}

Reply with JSON only: {{"decision": "take" or "skip", "reason": "<one sentence citing the numbers above>"}}"""


DEFAULT_MODEL = 'claude-sonnet-5-5'          # proven on the owner's PAPER account; settable via /api/keys

OUTPUT_SCHEMA = {
    'type': 'object',
    'properties': {
        'decision': {'type': 'string', 'enum': ['take', 'skip']},
        'reason': {'type': 'string', 'minLength': 1, 'maxLength': 400},
    },
    'required': ['decision', 'reason'],
    'additionalProperties': False,
}


def _number(sig, key):
    """A missing/non-finite indicator is explicit context, never an uncaught KeyError."""
    try:
        x = float(sig.get(key))
        return f'{x:.6g}' if x == x and abs(x) != float('inf') else 'unavailable'
    except (TypeError, ValueError):
        return 'unavailable'


def _percent(sig, key):
    try:
        x = float(sig.get(key))
        return f'{x:+.1%}' if x == x and abs(x) != float('inf') else 'unavailable'
    except (TypeError, ValueError):
        return 'unavailable'


def _text_block(body):
    """Exactly one final text block is accepted; thinking blocks may precede it."""
    blocks = body.get('content') if isinstance(body, dict) else None
    if not isinstance(blocks, list): raise APIReviewError('malformed_response')
    texts = [x.get('text') for x in blocks if isinstance(x, dict) and x.get('type') == 'text' and isinstance(x.get('text'), str)]
    if len(texts) != 1: raise APIReviewError('malformed_response')
    return texts[0]


def _decision(text):
    d = json.loads(text)
    if not isinstance(d, dict) or set(d) != {'decision', 'reason'}:
        raise APIReviewError('schema_mismatch')
    dec, reason = d.get('decision'), d.get('reason')
    if dec not in ('take', 'skip') or not isinstance(reason, str) or not reason.strip() or len(reason) > 400:
        raise APIReviewError('schema_mismatch')
    return dec, reason.strip()


def review(cfg, symbol, sleeve, sig, candles, funding, tf='4h', side='LONG'):
    """-> (decision, reason, meta). meta = model, prompt version, latency, error - logged by the engine."""
    import time
    key = cfg.get('ANTHROPIC_API_KEY')
    if not key or cfg.get('AI_FILTER', 'on') != 'on':
        return 'take', 'AI filter off', {}
    model = cfg.get('ANTHROPIC_MODEL') or DEFAULT_MODEL
    meta = dict(model=model, prompt=PROMPT_VERSION, tf=tf)
    t0 = time.time()
    try:
        st = sig.get('st_dir')
        st = 'up' if st is not None and float(st) > 0 else ('down' if st is not None else 'unavailable')
        msg = PROMPT.format(sleeve=sleeve, symbol=symbol, tf=tf, side=side, side_l=side.lower(), st=st,
                            funding=funding, candles=candles, time=str(sig.get('time', 'unavailable')),
                            close=_number(sig, 'close'), e20=_number(sig, 'e20'), e50=_number(sig, 'e50'),
                            e200=_number(sig, 'e200'), atr=_number(sig, 'atr'), ret42=_percent(sig, 'ret42'),
                            ret180=_percent(sig, 'ret180'))
        payload = dict(model=model, max_tokens=400, messages=[{'role': 'user', 'content': msg}],
                       output_config={'format': {'type': 'json_schema', 'schema': OUTPUT_SCHEMA}})
        r = requests.post('https://api.anthropic.com/v1/messages', timeout=60, headers={
            'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json=payload)
        meta['http_status'] = r.status_code
        err = api_error_type(r)
        if err:
            meta['api_error'] = err
            raise APIReviewError(err)
        body = r.json()
        if body.get('stop_reason') not in (None, 'end_turn'):
            raise APIReviewError('incomplete_response')
        dec, reason = _decision(_text_block(body))
        meta['latency_s'] = round(time.time() - t0, 1)
        return dec, reason, meta
    except Exception as e:
        meta.update(latency_s=round(time.time() - t0, 1), error=e.__class__.__name__)
        why = (f"http {meta['http_status']} {meta.get('api_error', e.__class__.__name__)}" if 'http_status' in meta
               else e.__class__.__name__)
        return 'take', f'AI review failed ({why}) - taking trade by rules', meta


class APIReviewError(Exception):
    """The API answered with an error object (or an unusable body) instead of a message."""


_ERR_TYPE = re.compile(r'[a-z_]{1,40}')


def api_error_type(r):
    """None for a usable 200 message; otherwise a short NON-SECRET error class for the log: the API's own error.type when it
    is a plain identifier (e.g. authentication_error, not_found_error, rate_limit_error), else a fixed label. Never returns
    anything from the response body except that identifier - no message text, headers, key or account ids."""
    try:
        body = r.json()
    except Exception:                                   # noqa: BLE001 - not JSON at all
        return 'malformed_response'
    if not isinstance(body, dict):
        return 'malformed_response'
    e = body.get('error')
    if r.status_code != 200 or e is not None or body.get('type') == 'error':
        t = e.get('type') if isinstance(e, dict) else None
        return t if isinstance(t, str) and _ERR_TYPE.fullmatch(t) else 'api_error'
    c = body.get('content')
    if not (isinstance(c, list) and any(isinstance(x, dict) and x.get('type') == 'text' and isinstance(x.get('text'), str) for x in c)):
        return 'malformed_response'
    return None
