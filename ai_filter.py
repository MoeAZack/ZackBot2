"""Optional Claude review of each entry signal. Claude can only VETO a trade, never create or resize one.
If the API key is missing or the call fails, the trade is taken (the backtested rules stay in charge).

AIREL-01 reliability: every failed review carries a fixed failure label (FAILURES), the stop_reason, the content block type
names, the attempt count and a short sha256 of the response bytes - never the body itself. A retryable failure is retried
ONCE inside the same 60 s budget. CIRCUIT_N consecutive failed reviews open a circuit: the API is skipped for CIRCUIT_COOL_S
and the review reports 'degraded'. What a failure means is an explicit policy (fail_policy): PAPER fails OPEN (take by the
rules, as before); a live mode fails CLOSED unless AI_FAIL_POLICY says otherwise - no live mode is enabled here."""
import hashlib, json, re, threading, time, requests

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


BUDGET_S = 60.0              # one review, retry included, never takes longer than the single call always could
RETRY_MIN_LEFT_S = 10.0      # no retry when less than this is left of the budget
CIRCUIT_N = 3                # consecutive failed reviews that open the circuit
CIRCUIT_COOL_S = 900.0       # how long an open circuit skips the API
FAILURES = ('http_error', 'malformed_response', 'incomplete_response', 'not_json', 'schema_mismatch', 'timeout', 'network',
            'circuit_open', 'internal')
_RETRYABLE = {'malformed_response', 'incomplete_response', 'not_json', 'schema_mismatch', 'timeout', 'network'}
_RETRY_HTTP = {429, 500, 502, 503, 504, 529}
_LOCK = threading.Lock()
_STATE = {}


def _reset():
    """Fresh process-wide review health (tests; a new process starts fresh anyway)."""
    with _LOCK:
        _STATE.clear()
        _STATE.update(calls=0, ok=0, failed=0, retries=0, consecutive=0, open_until=0.0, opened=0, last_failure=None,
                      last_ok=None, by_failure={})


_reset()


def health(now=None):
    """Counters for /api/status: calls, ok, failed, retries, consecutive failures, degraded (circuit open), by_failure."""
    now = time.time() if now is None else now
    with _LOCK:
        s = dict(_STATE, by_failure=dict(_STATE['by_failure']))
    s['degraded'] = s['open_until'] > now
    s['open_for_s'] = max(0, int(s['open_until'] - now)) if s['degraded'] else 0
    return s


def fail_policy(cfg):
    """'open' (take by the rules) or 'closed' (refuse the entry) when the review is unusable. AI_FAIL_POLICY wins when valid;
    otherwise PAPER/testnet = 'open' and any live mode = 'closed' (the live default is never fail-open)."""
    p = str(cfg.get('AI_FAIL_POLICY') or '').strip().lower()
    if p in ('open', 'closed'): return p
    return 'closed' if str(cfg.get('MODE') or '').strip().lower() == 'live' else 'open'


def _failure(e):
    if isinstance(e, APIReviewError): return str(e) if str(e) in FAILURES else 'http_error'
    if isinstance(e, json.JSONDecodeError): return 'not_json'
    if isinstance(e, requests.Timeout): return 'timeout'
    if isinstance(e, requests.RequestException): return 'network'
    return 'internal'


def _sha(r):
    try:
        b = r.content if isinstance(getattr(r, 'content', None), (bytes, bytearray)) else json.dumps(r.json(), sort_keys=True).encode()
        return hashlib.sha256(b).hexdigest()[:12]
    except Exception:                                   # noqa: BLE001 - a hash is telemetry only
        return None


def _block_types(body):
    blocks = body.get('content') if isinstance(body, dict) else None
    if not isinstance(blocks, list): return None
    return [t if isinstance(t, str) and _ERR_TYPE.fullmatch(t) else '?' for t in
            (x.get('type') if isinstance(x, dict) else None for x in blocks)][:8]


def _note(ok, failure=None, now=None):
    now = time.time() if now is None else now
    with _LOCK:
        if ok:
            _STATE['ok'] += 1; _STATE['consecutive'] = 0; _STATE['last_ok'] = now
            return
        _STATE['failed'] += 1; _STATE['consecutive'] += 1; _STATE['last_failure'] = failure
        _STATE['by_failure'][failure] = _STATE['by_failure'].get(failure, 0) + 1
        if _STATE['consecutive'] >= CIRCUIT_N and _STATE['open_until'] <= now:
            _STATE['open_until'] = now + CIRCUIT_COOL_S; _STATE['opened'] += 1


def _failed(cfg, meta, why):
    """The unusable-review outcome under the configured policy."""
    meta['policy'] = pol = fail_policy(cfg)
    meta['failed'] = True
    if pol == 'closed':
        return 'skip', f'AI review unavailable ({why}) - fail-closed policy, entry refused', meta
    return 'take', f'AI review failed ({why}) - taking trade by rules', meta


def review(cfg, symbol, sleeve, sig, candles, funding, tf='4h', side='LONG'):
    """-> (decision, reason, meta). meta = model, prompt version, latency, error, failure, attempts - logged by the engine."""
    key = cfg.get('ANTHROPIC_API_KEY')
    if not key or cfg.get('AI_FILTER', 'on') != 'on':
        return 'take', 'AI filter off', {}
    model = cfg.get('ANTHROPIC_MODEL') or DEFAULT_MODEL
    meta = dict(model=model, prompt=PROMPT_VERSION, tf=tf)
    t0 = time.time()
    with _LOCK:
        _STATE['calls'] += 1
        open_until, n = _STATE['open_until'], _STATE['consecutive']
    if open_until > t0:                                 # circuit open: no API call at all
        meta.update(failure='circuit_open', degraded=True, attempts=0, latency_s=0.0)
        return _failed(cfg, meta, f'degraded: {n} consecutive failures, retry in {int(open_until - t0)}s')
    attempt = 0
    while True:
        attempt += 1
        meta.pop('http_status', None); meta.pop('api_error', None)
        dec, why, meta, failure = _attempt(cfg, key, model, meta, symbol, sleeve, sig, candles, funding, tf, side,
                                           timeout=max(1.0, BUDGET_S - (time.time() - t0)))
        meta['attempts'] = attempt
        if failure is None:
            _note(True)
            meta['latency_s'] = round(time.time() - t0, 1)
            return dec, why, meta
        meta['failure'] = failure
        retry = (attempt == 1 and BUDGET_S - (time.time() - t0) >= RETRY_MIN_LEFT_S and
                 (failure in _RETRYABLE or meta.get('http_status') in _RETRY_HTTP))
        if not retry: break
        with _LOCK: _STATE['retries'] += 1
    _note(False, failure)
    meta['latency_s'] = round(time.time() - t0, 1)
    return _failed(cfg, meta, why)


def _attempt(cfg, key, model, meta, symbol, sleeve, sig, candles, funding, tf, side, timeout=BUDGET_S):
    """One API call -> (decision, reason, meta, failure label or None)."""
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
        r = requests.post('https://api.anthropic.com/v1/messages', timeout=timeout, headers={
            'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json=payload)
        meta['http_status'] = r.status_code
        meta['resp_sha'] = _sha(r)
        err = api_error_type(r)
        if err:
            meta['api_error'] = err
            raise APIReviewError(err if err == 'malformed_response' else 'http_error')
        body = r.json()
        meta['blocks'] = _block_types(body)
        sr = body.get('stop_reason')
        meta['stop_reason'] = sr if isinstance(sr, str) and _ERR_TYPE.fullmatch(sr) else (None if sr is None else '?')
        if sr not in (None, 'end_turn'):
            raise APIReviewError('incomplete_response')
        dec, reason = _decision(_text_block(body))
        meta.pop('error', None)
        return dec, reason, meta, None
    except Exception as e:
        meta['error'] = e.__class__.__name__
        why = (f"http {meta['http_status']} {meta.get('api_error', e.__class__.__name__)}" if 'http_status' in meta
               else e.__class__.__name__)
        return 'take', why, meta, _failure(e)


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
