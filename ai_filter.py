"""Optional Claude review of each entry signal. Claude can only VETO a trade, never create or resize one.
If the API key is missing or the call fails, the trade is taken (the backtested rules stay in charge)."""
import json, requests

PROMPT_VERSION = 'v2'
PROMPT = """You are a cautious second reader for a systematic crypto futures bot. Its backtested rules already produced
a {side} signal. You see ONLY the indicator values and candles below - no news, no exchange announcements, no listing
status, no order book. Do not claim knowledge of events you were not given. Your only job is to veto a trade when the
data below shows a clear, concrete problem (for example: price extremely stretched from its averages in the trade's
direction, a single outsized candle making the whole move, or funding strongly against the {side_l}).
When in doubt, TAKE the trade.

Signal: {sleeve} on {symbol}, {tf} candles, candle closed {time} UTC
Close {close}  EMA20 {e20:.6g}  EMA50 {e50:.6g}  EMA200 {e200:.6g}  ATR {atr:.6g}
Return over the last 42 candles {ret42:+.1%}, last 180 candles {ret180:+.1%}  Supertrend {st}
Funding rate (8h): {funding}
Last 12 {tf} candles (open, high, low, close): {candles}

Reply with JSON only: {{"decision": "take" or "skip", "reason": "<one sentence citing the numbers above>"}}"""


def review(cfg, symbol, sleeve, sig, candles, funding, tf='4h', side='LONG'):
    """-> (decision, reason, meta). meta = model, prompt version, latency, error - logged by the engine."""
    import time
    key = cfg.get('ANTHROPIC_API_KEY')
    if not key or cfg.get('AI_FILTER', 'on') != 'on':
        return 'take', 'AI filter off', {}
    model = cfg.get('ANTHROPIC_MODEL') or 'claude-sonnet-4-5'
    meta = dict(model=model, prompt=PROMPT_VERSION, tf=tf)
    t0 = time.time()
    msg = PROMPT.format(sleeve=sleeve, symbol=symbol, tf=tf, side=side, side_l=side.lower(), st='up' if sig['st_dir'] > 0 else 'down',
                        funding=funding, candles=candles, **{k: sig[k] for k in
                        ('time', 'close', 'e20', 'e50', 'e200', 'atr', 'ret42', 'ret180')})
    try:
        r = requests.post('https://api.anthropic.com/v1/messages', timeout=60, headers={
            'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json=dict(model=model, max_tokens=200,
                      messages=[{'role': 'user', 'content': msg}]))
        text = r.json()['content'][0]['text']
        d = json.loads(text[text.find('{'): text.rfind('}') + 1])
        dec = 'skip' if str(d.get('decision', '')).lower() == 'skip' else 'take'
        meta['latency_s'] = round(time.time() - t0, 1)
        return dec, d.get('reason', ''), meta
    except Exception as e:
        meta.update(latency_s=round(time.time() - t0, 1), error=e.__class__.__name__)
        return 'take', f'AI review failed ({e.__class__.__name__}) - taking trade by rules', meta
