"""Optional Claude review of each entry signal. Claude can only VETO a trade, never create or resize one.
If the API key is missing or the call fails, the trade is taken (the backtested rules stay in charge)."""
import json, requests

PROMPT = """You are the risk reviewer for a systematic crypto futures trend-following bot.
The rules below already produced a LONG signal that was profitable in a 2-year backtest. Your only job
is to veto trades with a clear, concrete reason (e.g. the move is a single news-driven spike, the coin is
delisting, extreme funding, price far stretched from its averages). When in doubt, TAKE the trade.

Signal: {sleeve} on {symbol} 4h, candle closed {time} UTC
Close {close}  EMA20 {e20:.6g}  EMA50 {e50:.6g}  EMA200 {e200:.6g}  ATR {atr:.6g}
7-day return {ret42:+.1%}  30-day return {ret180:+.1%}  Supertrend {st}
Funding rate (8h): {funding}
Last 12 candles (open, high, low, close): {candles}

Reply with JSON only: {{"decision": "take" or "skip", "reason": "<one sentence>"}}"""


def review(cfg, symbol, sleeve, sig, candles, funding):
    key = cfg.get('ANTHROPIC_API_KEY')
    if not key or cfg.get('AI_FILTER', 'on') != 'on':
        return 'take', 'AI filter off'
    msg = PROMPT.format(sleeve=sleeve, symbol=symbol, st='up' if sig['st_dir'] > 0 else 'down',
                        funding=funding, candles=candles, **{k: sig[k] for k in
                        ('time', 'close', 'e20', 'e50', 'e200', 'atr', 'ret42', 'ret180')})
    try:
        r = requests.post('https://api.anthropic.com/v1/messages', timeout=60, headers={
            'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json=dict(model=cfg.get('ANTHROPIC_MODEL', 'claude-sonnet-4-5'), max_tokens=200,
                      messages=[{'role': 'user', 'content': msg}]))
        text = r.json()['content'][0]['text']
        d = json.loads(text[text.find('{'): text.rfind('}') + 1])
        dec = 'skip' if str(d.get('decision', '')).lower() == 'skip' else 'take'
        return dec, d.get('reason', '')
    except Exception as e:
        return 'take', f'AI review failed ({e.__class__.__name__}) - taking trade by rules'
