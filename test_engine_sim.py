"""Replay test: real Engine vs simulated Binance, compared with backtest.run on the same window."""
import logging; logging.basicConfig(level=logging.INFO, format="%(message)s")
import sys, os, tempfile, time, json, types
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
import load_data, backtest as B, strategies as S
import engine as E

SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT']
RAW = load_data.load(syms=SYMS)
N = len(RAW['BTCUSDT'])
T0 = int(sys.argv[1]) if len(sys.argv) > 1 else N - 900
SLEEVES = json.loads(sys.argv[2]) if len(sys.argv) > 2 else [
    E.sleeve('MOM', 'ema_mom', .4, .03, 4, 'core8', mgmt=E.PY),
    E.sleeve('DCA', 'dca_dip', .3, .03, 4, 'core8'),
    E.sleeve('SQZ', 'squeeze_tp', .3, .03, 4, 'core8', sides='both')]
FEE, SLIP = B.FEE, B.SLIP
D = {s: RAW[s].reset_index(drop=True) for s in SYMS}


class W:   # simulated exchange world
    i = None; mark = {}; cash = 500.0; lots = []; stops = {}; n = 0; closed = []


class Fake:
    def __init__(self, *a, **k): pass
    def sync_time(self): pass
    def exchange_info(self):
        return {'symbols': [{'symbol': s, 'contractType': 'PERPETUAL', 'status': 'TRADING', 'filters': [
            {'filterType': 'MARKET_LOT_SIZE', 'stepSize': '0.00001', 'minQty': '0.00001'}, {'filterType': 'PRICE_FILTER', 'tickSize': '0.000001'},
            {'filterType': 'MIN_NOTIONAL', 'notional': str(B.MIN_NOTIONAL.get(s, 5))}]} for s in SYMS]}
    def klines(self, s, tf, limit=1500, **k):
        d = D[s].iloc[max(0, W.i - 1000 + 1):W.i + 2]
        ms = (d.t.astype('datetime64[ns]').astype('int64') // 10 ** 6).values
        return [[int(t), o, h, l, c, v, int(t) + 14400000 - 1] for t, o, h, l, c, v in zip(ms, d.o, d.h, d.l, d.c, d.v)]
    def marks(self): return dict(W.mark)
    def premium(self, s): return {'lastFundingRate': '0.0001'}
    def account(self): return {'totalMarginBalance': str(W.equity())}
    def positions(self):
        out = {}
        for l in W.lots: out[(l['s'], l['ps'])] = out.get((l['s'], l['ps']), 0) + l['q']
        return {k: v for k, v in out.items() if v > 1e-12}
    def hedge_mode(self): return True
    def set_hedge_mode(self, on=True): pass
    def set_leverage(self, *a): pass
    def set_margin_type(self, *a): pass
    def open(self, s, ps, qty):
        q = float(qty); sd = 1 if ps == 'LONG' else -1; px = W.mark[s] * (1 + sd * SLIP)
        W.cash -= q * px * FEE; W.lots.append(dict(s=s, ps=ps, q=q, avg=px)); W.merge(s, ps)
        return {'avgPrice': str(px)}
    def close(self, s, ps, qty):
        q = float(qty); sd = 1 if ps == 'LONG' else -1; px = W.mark[s] * (1 - sd * SLIP)
        W.reduce(s, ps, q, px); return {'avgPrice': str(px)}
    def stop(self, s, ps, qty, price):
        W.n += 1; tag = f'o:{W.n}'; W.stops[tag] = dict(s=s, ps=ps, q=float(qty), p=float(price)); return tag
    def cancel(self, s, tag): W.stops.pop(tag, None)
    def open_stop_tags(self, s): return {k for k, v in W.stops.items() if v['s'] == s}
    def cancel_all(self, s):
        for k in [k for k, v in W.stops.items() if v['s'] == s]: W.stops.pop(k)


def _merge(s, ps):
    ls = [l for l in W.lots if l['s'] == s and l['ps'] == ps]
    if len(ls) > 1:
        q = sum(l['q'] for l in ls); avg = sum(l['q'] * l['avg'] for l in ls) / q
        for l in ls: W.lots.remove(l)
        W.lots.append(dict(s=s, ps=ps, q=q, avg=avg))
def _reduce(s, ps, q, px):
    l = next(l for l in W.lots if l['s'] == s and l['ps'] == ps)
    q = min(q, l['q']); sd = 1 if ps == 'LONG' else -1
    W.cash += sd * (px - l['avg']) * q - q * px * FEE; l['q'] -= q
    if l['q'] <= 1e-12: W.lots.remove(l)
def _equity():
    return W.cash + sum((1 if l['ps'] == 'LONG' else -1) * (W.mark[l['s']] - l['avg']) * l['q'] for l in W.lots)
def _move(px_by_sym):
    """Move marks; trigger exchange stops."""
    for s, p in px_by_sym.items():
        W.mark[s] = p
        for tag, st in list(W.stops.items()):
            if st['s'] != s: continue
            sd = 1 if st['ps'] == 'LONG' else -1
            if sd * (p - st['p']) <= 0:
                have = sum(l['q'] for l in W.lots if l['s'] == s and l['ps'] == st['ps'])
                q = min(st['q'], have)
                if q > 0: _reduce(s, st['ps'], q, st['p'] * (1 - sd * SLIP) if True else p)
                W.stops.pop(tag)
W.merge, W.reduce, W.equity = staticmethod(_merge), staticmethod(_reduce), staticmethod(_equity)

E.Futures = Fake
import ai_filter
tmp = tempfile.mkdtemp()
fake_now = [0.0]
E.now_utc = lambda: pd.Timestamp(fake_now[0], unit='s', tz='UTC').to_pydatetime()
E.time = types.SimpleNamespace(time=lambda: fake_now[0], sleep=lambda x: None)
eng = E.Engine(dict(MODE='paper', API_KEY='x', API_SECRET='y'), tmp)
eng.S['SLEEVES'] = SLEEVES; eng.S['UNIVERSE'] = SYMS; eng.S['SYMBOLS_ON'] = {s: True for s in SYMS}; eng.S['CAPITAL_CAP'] = 0
eng.S['MAX_LEVERAGE'] = 10
eng.equity_hist = []; eng.record_equity = lambda e: None
eng.connect()
curve = []
for i in range(T0, N - 1):
    W.i = i
    fake_now[0] = D['BTCUSDT'].t[i].timestamp() + 14400 + 15
    W.mark.update({s: D[s].o[i + 1] for s in SYMS})
    eng.cycle('4h')
    # intrabar path of candle i+1: open -> low -> high -> close (stops first, then favourable)
    j = i + 1
    for col in ('l', 'h', 'c'):
        _move({s: D[s][col][j] for s in SYMS})
        eng.manage(dict(W.mark))
    W.cash -= sum(l['q'] * W.mark[l['s']] * B.FUND_PER_BAR for l in W.lots)
    curve.append((D['BTCUSDT'].t[j], _equity()))
cv = pd.Series([c for _, c in curve], index=[t for t, _ in curve])
tr = pd.read_csv(os.path.join(tmp, 'trades.csv')) if os.path.exists(os.path.join(tmp, 'trades.csv')) else pd.DataFrame()
ev = tr.event.value_counts().to_dict() if len(tr) else {}
print(f'ENGINE  500 -> {cv.iloc[-1]:.0f} ({(cv.iloc[-1]/500-1)*100:+.1f}%)  maxDD {(cv/cv.cummax()-1).min()*100:.1f}%  events {ev}')
bk = B.Book({s: RAW[s] for s in SYMS})
cfg = [dict(key=s['key'], share=s['share'], risk=s['risk'], max_pos=s['max_pos'], symbols=SYMS, sides=s['sides'], mgmt=s['mgmt']) for s in SLEEVES]
t2, c2 = B.run(bk, cfg, start=500, max_lev=10, t0=D['BTCUSDT'].t[T0 + 1])
print(f'BACKTEST 500 -> {c2.iloc[-1]:.0f} ({(c2.iloc[-1]/500-1)*100:+.1f}%)  maxDD {(c2/c2.cummax()-1).min()*100:.1f}%  trades {len(t2)} by sleeve {t2.sleeve.value_counts().to_dict() if len(t2) else {}}  exits {t2.why.value_counts().to_dict() if len(t2) else {}}')
ent = tr[tr.event == 'entry'] if len(tr) else tr
print('engine entries by sleeve', ent.sleeve.value_counts().to_dict() if len(ent) else {}, '| leftover exchange lots', len(W.lots), 'stops', len(W.stops), 'engine lots', len(eng.state['lots']))
print('EXCHANGE LOTS', [(l['s'], l['ps'], round(l['q'], 6)) for l in W.lots])
print('ENGINE LOTS', [(l['symbol'], l['side'], l['qty'], l['sleeve']) for l in eng.state['lots'].values()])
print('STOPS', list(W.stops.values()))
import collections
print('HISTORY', len(eng.history), 'net pnl sum', round(sum(h['pnl'] for h in eng.history), 2), 'sample', {k: eng.history[-1][k] for k in ('symbol','side','strategy','pnl','r','roi_capital','move_pct','hours','exit_reason','fees')})
print('MISSED', len(eng.missed), collections.Counter(m['reason'] for m in eng.missed).most_common(6))
json.dump(eng.history, open('/tmp/claude-0/-home-claude/d6ad53d0-10de-5d7c-a74c-77ea7be8c649/scratchpad/seed_history.json','w'), default=str); json.dump(eng.missed, open('/tmp/claude-0/-home-claude/d6ad53d0-10de-5d7c-a74c-77ea7be8c649/scratchpad/seed_missed.json','w'), default=str)
