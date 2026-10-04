"""ZackBot live engine — runs any mix of strategies ("sleeves") from strategies.py on Binance USD-M futures.

Hard stops live on Binance (STOP_MARKET per lot). Softer management (partial take-profit, breakeven,
trailing, pyramiding adds, DCA safety orders, basket take-profit) is checked every few seconds on the mark price.
Signals (entries/exits) are evaluated right after each candle close of the sleeve's timeframe.
Hedge mode is used so longs and shorts on the same coin can coexist.
"""
import csv, json, math, os, time, logging, threading, copy
from datetime import datetime, timezone
import numpy as np
import pandas as pd

import strategies as S
from binance_client import Futures, MAINNET, TESTNET, BinanceError
from ai_filter import review

log = logging.getLogger('zackbot')
TF_SEC = {'15m': 900, '1h': 3600, '4h': 14400}
FEE_EST = 0.0005
BE_BUF = 0.0015          # taker fee estimate per fill, used for net PnL in the trade history
CORE8 = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT']
TOP40 = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT', 'SANDUSDT', 'ZECUSDT',
         'QNTUSDT', 'WLDUSDT', 'NEARUSDT', 'SUIUSDT', 'UNIUSDT', 'ONEUSDT', 'MOVRUSDT', 'STRKUSDT', 'ZROUSDT', 'ENAUSDT',
         'TAOUSDT', 'AAVEUSDT', 'LTCUSDT', '1000PEPEUSDT', 'MANAUSDT', 'ADAUSDT', 'ONDOUSDT', 'FILUSDT', 'ARBUSDT', 'SUPERUSDT',
         'BCHUSDT', 'DOTUSDT', 'ENJUSDT', 'INJUSDT', 'GALAUSDT', 'ARKUSDT', 'FETUSDT', 'XLMUSDT', 'ETCUSDT', 'APTUSDT']
PY = dict(pyramid=dict(n=1, step_r=1.5, frac=0.5))


def sleeve(id, key, share, risk, max_pos, symbols='all', sides=None, mgmt=None, tf='4h', enabled=True):
    st = S.STRATEGIES[key]
    return dict(id=id, key=key, name=st['name'], enabled=enabled, share=share, risk=risk, max_pos=max_pos,
                symbols=symbols, sides=sides or st['sides'], mgmt=mgmt or {}, tf=tf)


# Ready-made profiles (backtested Sep 2024 -> Oct 2026, $500 start, 10x cap; see Research tab)
PRESETS = {
    'original': dict(name='Original A/B (core 8)', note='What ran first: EMAx|Supertrend + EMAx|Momentum on 8 coins, 3%.',
                     sleeves=[sleeve('A', 'ema_st', .5, .03, 4, 'core8'), sleeve('B', 'ema_mom', .5, .03, 4, 'core8')]),
    'calm': dict(name='Calm', note='Backtest: $500 -> $1,545, max DD -13%, worst month -3%. Both years positive.',
                 sleeves=[sleeve('MOM', 'ema_mom', 1 / 3, .01, 8), sleeve('ST', 'ema_st', 1 / 3, .01, 4, 'core8'),
                          sleeve('DCA', 'dca_dip', 1 / 3, .01, 6)]),
    'balanced': dict(name='Balanced', note='Backtest: $500 -> $5,964, max DD -29%, worst month -7%.',
                     sleeves=[sleeve('MOM', 'ema_mom', 1 / 3, .02, 8, mgmt=PY), sleeve('ST', 'ema_st', 1 / 3, .02, 4, 'core8', mgmt=PY),
                              sleeve('DCA', 'dca_dip', 1 / 3, .02, 6)]),
    'aggressive': dict(name='Aggressive', note='Backtest: $500 -> $12,412, max DD -39%, worst month -11%.',
                       sleeves=[sleeve('MOM', 'ema_mom', 1 / 3, .03, 8, mgmt=PY), sleeve('ST', 'ema_st', 1 / 3, .03, 4, 'core8', mgmt=PY),
                                sleeve('DCA', 'dca_dip', 1 / 3, .03, 6)]),
    'active': dict(name='Active (1h, more trades)', note='1h DCA dip + 1h Breakout/pyramiding on core 8. Only 6 months tested: $500 -> $1,021 (+104%), max DD -24%, ~20 trades/week.',
                   sleeves=[sleeve('DCA1H', 'dca_dip', .5, .02, 4, 'core8', tf='1h'), sleeve('BRK1H', 'breakout_pyramid', .5, .02, 4, 'core8', tf='1h')]),
    'boost_active': dict(name='Boost + Active (4h + 1h mix)', note='Boost on 4h with half the capital, Active 1h with the other half. Last 6 months: $500 -> $1,293 (+159%), max DD -30%, worst month -9%, ~30 trades/week (Boost alone: -46% DD).',
                         sleeves=[sleeve('MOM', 'ema_mom', .25, .05, 8, mgmt=PY), sleeve('DCA', 'dca_dip', .25, .05, 6),
                                  sleeve('DCA1H', 'dca_dip', .25, .02, 4, 'core8', tf='1h'), sleeve('BRK1H', 'breakout_pyramid', .25, .02, 4, 'core8', tf='1h')]),
    'boost': dict(name='Boost (short-term, high risk)', note='Backtest: $500 -> $30,361, max DD -51%, worst month -25%. For short sprints only.',
                  sleeves=[sleeve('MOM', 'ema_mom', .5, .05, 8, mgmt=PY), sleeve('DCA', 'dca_dip', .5, .05, 6)]),
}

GLOBAL_DEFAULTS = dict(COMPOUND=False, CAP_SINCE='', CAP_ADJ=[], CAP_CYCLES=[], TELEGRAM_ON=False, TELEGRAM_TOKEN='', TELEGRAM_CHAT='', MAX_LEVERAGE=10, DAILY_LOSS_HALT=0.08, PEAK_DD_FLATTEN=0.0, CAPITAL_CAP=500.0,
                       ENTRIES_PAUSED=False, AI_FILTER=False, PRESET='original',
                       UNIVERSE=list(TOP40), SYMBOLS_ON={}, RUN_IN_BACKGROUND=True)


def now_utc():
    return datetime.now(timezone.utc)


def save_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f: json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


class Engine:
    def __init__(self, cfg, data_dir, dry=False):
        self.cfg, self.dir, self.dry = cfg, data_dir, dry
        self.F = {k: os.path.join(data_dir, f) for k, f in dict(state='state.json', trades='trades.csv', settings='settings.json', history='history.json', missed='missed.json',
                                                                equity='equity.json').items()}
        self.live = cfg.get('MODE') == 'live'
        self.lock = threading.RLock()
        self.trade = Futures(cfg.get('API_KEY', ''), cfg.get('API_SECRET', ''), MAINNET if self.live else TESTNET)
        self.data = Futures('', '', MAINNET)
        self.load_settings()
        # migrate v1 files (single-strategy bot) so logs stay readable
        if os.path.exists(self.F['trades']):
            with open(self.F['trades']) as f: head = f.readline()
            if 'side' not in head: os.replace(self.F['trades'], self.F['trades'].replace('.csv', '_v1.csv'))
        self.state = dict(lots={}, day=None, day_start_equity=None, halted=False, peak_equity=None, last_cycle={})
        if os.path.exists(self.F['state']):
            try: self.state.update(json.load(open(self.F['state'])))
            except Exception: log.warning('state.json unreadable - starting empty')
        self.state['day'] = None
        self.equity_hist = json.load(open(self.F['equity'])) if os.path.exists(self.F['equity']) else []
        self.rules, self._cache, self._kc, self.signals, self.signals_time = {}, {}, {}, {}, None
        self.last_eq = self.last_balance = None
        self.hedge = False
        self.run_now = threading.Event()
        self.history = self._load_list('history'); self.missed = self._load_list('missed')
        self.last_account, self.last_skip, self.corr = {}, '', None
        self.error = None
        self.connected = False

    def _load_list(self, k):
        try: return json.load(open(self.F[k])) if os.path.exists(self.F[k]) else []
        except Exception: return []

    # ------------------------------------------------------------ notifications (Telegram, optional)
    def notify(self, text):
        S_ = self.S
        if not (S_.get('TELEGRAM_ON') and S_.get('TELEGRAM_TOKEN') and S_.get('TELEGRAM_CHAT')): return
        def _send():
            try:
                import requests
                requests.post(f"https://api.telegram.org/bot{S_['TELEGRAM_TOKEN']}/sendMessage", timeout=10,
                              data=dict(chat_id=S_['TELEGRAM_CHAT'], text=f"[ZackBot {'LIVE' if self.live else 'paper'}] {text}"))
            except Exception as ex: log.warning(f'telegram: {ex}')
        threading.Thread(target=_send, daemon=True).start()

    # ------------------------------------------------------------ settings
    def load_settings(self):
        s = copy.deepcopy(GLOBAL_DEFAULTS)
        if os.path.exists(self.F['settings']):
            try: s.update(json.load(open(self.F['settings'])))
            except Exception: log.warning('settings.json unreadable - defaults used')
        if 'SLEEVES' not in s:
            s['SLEEVES'] = copy.deepcopy(PRESETS[s.get('PRESET', 'original')]['sleeves'])
        for k in ('RISK_PER_TRADE', 'MAX_POSITIONS_PER_SLEEVE', 'STOP_ATR', 'SPLIT_ST', 'SLEEVE_ST', 'SLEEVE_TSM'):
            s.pop(k, None)                                       # v1 keys
        s['SYMBOLS_ON'] = {sym: s.get('SYMBOLS_ON', {}).get(sym, True) for sym in s['UNIVERSE']}
        self.S = s

    def save_settings(self):
        save_json(self.F['settings'], self.S)

    def apply_preset(self, name):
        with self.lock:
            self.S['SLEEVES'] = copy.deepcopy(PRESETS[name]['sleeves'])
            self.S['PRESET'] = name
            self.save_settings()
        log.info(f'preset applied: {PRESETS[name]["name"]} (open trades keep their own management)')

    def sleeve_symbols(self, sl, include_off=False):
        u = [s for s in self.S['UNIVERSE'] if s in self.rules]
        if sl['symbols'] == 'core8': base = [s for s in CORE8 if s in u]
        elif sl['symbols'] == 'all': base = u
        else: base = [s for s in sl['symbols'] if s in self.rules]
        return [s for s in base if include_off or self.S['SYMBOLS_ON'].get(s, True)]

    def mgmt(self, sl):
        return dict(S.STRATEGIES[sl['key']]['mgmt'], **sl.get('mgmt', {}))

    # ------------------------------------------------------------ exchange setup
    def connect(self):
        info = self.trade.exchange_info()
        self.rules = {}
        for s in info['symbols']:
            if s.get('contractType') != 'PERPETUAL' or s.get('status') != 'TRADING': continue
            f = {x['filterType']: x for x in s['filters']}
            self.rules[s['symbol']] = dict(step=float(f['MARKET_LOT_SIZE']['stepSize']), min_qty=float(f['MARKET_LOT_SIZE']['minQty']),
                                           tick=float(f['PRICE_FILTER']['tickSize']),
                                           min_notional=float(f.get('MIN_NOTIONAL', {}).get('notional', 5)))
        missing = [s for s in self.S['UNIVERSE'] if s not in self.rules]
        if missing: log.warning(f'not tradable on this exchange, skipped: {missing}')
        if self.cfg.get('API_KEY'):
            try:
                self.hedge = self.trade.hedge_mode()
                if not self.hedge:
                    if not self.trade.positions():
                        self.trade.set_hedge_mode(True); self.hedge = True
                        log.info('Hedge mode switched ON (longs and shorts can run together)')
                    else:
                        log.warning('Account has open positions in one-way mode: shorts disabled until flat')
            except Exception as e:
                log.warning(f'hedge mode check failed: {e}')
            self.equity()
        self.connected = True

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _rd(x, step):
        dec = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
        return round(math.floor(x / step + 1e-9) * step, dec)

    @staticmethod
    def _fmt(x, step):
        dec = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
        return f'{x:.{dec}f}'

    def equity(self):
        acc = self.trade.account()
        self.last_account = {k: float(acc.get(k, 0) or 0) for k in ('totalMarginBalance', 'totalInitialMargin', 'totalMaintMargin',
                                                                     'availableBalance', 'totalUnrealizedProfit', 'totalWalletBalance')}
        eq = float(acc['totalMarginBalance'])
        self.last_balance = eq
        cap = float(self.S.get('CAPITAL_CAP') or 0)
        info = self.capital_info(acc)
        if self.S.get('COMPOUND') and cap > 0:
            self.last_eq = max(0.0, min(eq, info['capital']))     # start amount + closed P&L since start + deposits - withdrawals
        else:
            self.last_eq = min(eq, cap) if cap > 0 else eq
        return self.last_eq

    # ------------------------------------------------------------ capital: fixed / compounding, withdrawals, fresh starts
    def capital_info(self, acc=None):
        S = self.S
        since = S.get('CAP_SINCE') or ''
        realized = sum(h.get('pnl') or 0 for h in self.history if h.get('closed', '') >= since)
        adj = sum(a['amount'] for a in S.get('CAP_ADJ', []))
        upnl = float((acc or {}).get('totalUnrealizedProfit', self.last_account.get('totalUnrealizedProfit', 0) if self.last_account else 0) or 0)
        base = float(S.get('CAPITAL_CAP') or 0)
        cap = base + realized + adj + upnl
        self.cap = dict(mode='compound' if S.get('COMPOUND') else 'fixed', base=base, since=since, realized=round(realized, 2),
                        withdrawn=round(-sum(a['amount'] for a in S.get('CAP_ADJ', []) if a['amount'] < 0), 2),
                        deposited=round(sum(a['amount'] for a in S.get('CAP_ADJ', []) if a['amount'] > 0), 2),
                        unrealized=round(upnl, 2), capital=round(cap, 2), growth=round((cap - adj) / base * 100 - 100, 2) if base else None,
                        adj=S.get('CAP_ADJ', [])[-50:], cycles=S.get('CAP_CYCLES', [])[-50:])
        return self.cap

    def capital_action(self, kind, amount=None, note=''):
        with self.lock:
            S, st, now = self.S, self.state, now_utc().isoformat(timespec='seconds')
            if not S.get('CAP_SINCE'): S['CAP_SINCE'] = now
            info = self.capital_info()
            if kind == 'compound':
                S['COMPOUND'] = bool(amount)
                if S['COMPOUND'] and not S.get('CAP_ADJ') and not S.get('CAP_CYCLES'): S['CAP_SINCE'] = now   # first time: count from today
                msg = 'compounding on - profits stay in and trade sizes grow' if S['COMPOUND'] else 'compounding off - trades sized on the fixed capital'
            elif kind in ('withdraw', 'deposit'):
                amt = abs(float(amount or 0))
                if amt <= 0: raise ValueError('enter an amount')
                if kind == 'withdraw' and amt > info['capital']: raise ValueError(f'more than the bot capital ({info["capital"]:.2f})')
                sgn = -1 if kind == 'withdraw' else 1
                S.setdefault('CAP_ADJ', []).append(dict(time=now, amount=sgn * amt, note=note or kind))
                for k in ('peak_equity', 'day_start_equity'):       # so a withdrawal never looks like a loss to the safety limits
                    if st.get(k): st[k] = max(0.0, st[k] + sgn * amt)
                msg = f'{kind} of {amt:.2f} recorded'
            elif kind == 'reset':
                new = float(amount) if amount not in (None, '') else info['capital']
                if new <= 0: raise ValueError('start amount must be above 0')
                S.setdefault('CAP_CYCLES', []).append(dict(start=info['since'], end=now, start_cap=info['base'], end_cap=info['capital'],
                                                           pnl=info['realized'], withdrawn=info['withdrawn'], deposited=info['deposited'], note=note))
                S['CAPITAL_CAP'] = new; S['CAP_SINCE'] = now; S['CAP_ADJ'] = []
                st['peak_equity'] = None; st['day_start_equity'] = None; st['day'] = None; st['halted'] = False
                msg = f'fresh start with {new:.2f}'
            else:
                raise ValueError('unknown capital action')
            self.save_settings(); self.save_state()
            try: self.equity()
            except Exception as ex: log.warning(f'equity refresh: {ex}')
            log.info('CAPITAL ' + msg); self.notify('💰 ' + msg)
            return msg

    def candles(self, sym, tf):
        """Closed candles with indicators. Cached until the next candle closes (no refetch on every click)."""
        step = TF_SEC[tf]
        last_closed_open = (math.floor(time.time() / step) - 1) * step * 1000
        hit = self._kc.get((sym, tf))
        if hit is not None and hit[1] >= last_closed_open:
            return hit[0]
        raw = self.data.klines(sym, tf, 1500)
        now_ms = time.time() * 1000
        rows = [r[:7] for r in raw if r[6] < now_ms]
        df = pd.DataFrame(rows, columns=['t', 'o', 'h', 'l', 'c', 'v', 'ct']).astype(float)
        last_open = float(df.t.iloc[-1]) if len(df) else 0
        df['t'] = pd.to_datetime(df.t, unit='ms')
        df = S.indicators(df[['t', 'o', 'h', 'l', 'c', 'v']])
        self._kc[(sym, tf)] = (df, last_open)
        return df

    def compute_signals(self, tf, syms):
        """Signals for every enabled sleeve of this timeframe, on all its symbols."""
        need_ctx = any(sl['key'] in ('rotation', 'hot_coin', 'bear_breakdown') for sl in self.S['SLEEVES'] if sl['tf'] == tf)
        universe = sorted(set(syms) | ({'BTCUSDT'} if 'BTCUSDT' in self.rules else set()))
        dfs = {}
        for s in (universe if need_ctx else syms + (['BTCUSDT'] if 'BTCUSDT' in self.rules else [])):
            try: dfs[s] = self.candles(s, tf)
            except Exception as e: log.warning(f'candles {s} {tf}: {e}')
        al = {s: d for s, d in dfs.items() if len(d) >= 250}
        ctx = S.build_context(al, 'BTCUSDT' if 'BTCUSDT' in al else next(iter(al)))
        out = {}
        for sl in self.S['SLEEVES']:
            if sl['tf'] != tf: continue
            for s in syms:
                if s not in al: continue
                raw = S.signals(sl['key'], al[s], ctx[s], sl.get('params'), mask_sides=False)
                d = al[s].iloc[-1]
                out[f"{sl['id']}|{s}"] = dict(le=bool(raw['le'][-1]) and sl['sides'] in ('long', 'both'),
                                              se=bool(raw['se'][-1]) and sl['sides'] in ('short', 'both'),
                                              lx=bool(raw['lx'][-1]), sx=bool(raw['sx'][-1]), close=float(d.c),
                                              vol_rank=float((al[s].atr / al[s].c).rolling(180, min_periods=60).rank(pct=True).iloc[-1]),
                                              atr=float(d.atr), time=str(d.t))
        return out, al

    def record_equity(self, eq):
        self.equity_hist.append([int(time.time()), round(eq, 2)])
        self.equity_hist = self.equity_hist[-8000:]
        save_json(self.F['equity'], self.equity_hist)

    def log_trade(self, **row):
        new = not os.path.exists(self.F['trades'])
        with open(self.F['trades'], 'a', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['time', 'event', 'sleeve', 'symbol', 'side', 'qty', 'price', 'stop', 'pnl', 'equity', 'note'])
            if new: w.writeheader()
            w.writerow({k: row.get(k, '') for k in w.fieldnames})

    def save_state(self):
        save_json(self.F['state'], self.state)

    # ------------------------------------------------------------ order helpers
    def _replace_stop(self, lot):
        if self.dry: return
        r = self.rules[lot['symbol']]
        new = self.trade.stop(lot['symbol'], lot['side'], self._fmt(lot['qty'], r['step']), self._fmt(lot['stop'], r['tick']))
        self.trade.cancel(lot['symbol'], lot.get('stop_id'))
        lot['stop_id'] = new

    def _market_close(self, lot, qty, why, mark=None):
        sym, r = lot['symbol'], self.rules[lot['symbol']]
        qty = self._rd(qty, r['step'])
        if qty <= 0: return 0.0
        px = mark or lot['avg']
        if not self.dry:
            o = self.trade.close(sym, lot['side'], self._fmt(qty, r['step']))
            px = float(o.get('avgPrice') or 0) or px
        sd = 1 if lot['side'] == 'LONG' else -1
        pnl = sd * (px - lot['avg']) * qty
        lot['realized'] = lot.get('realized', 0.0) + pnl
        lot.setdefault('fills', []).append([now_utc().isoformat(timespec='seconds'), why, qty, px])
        lot['fees'] = lot.get('fees', 0.0) + qty * px * FEE_EST
        lot['qty'] = max(0.0, self._rd(lot['qty'] - qty, r['step']))
        self.log_trade(time=now_utc().isoformat(timespec='seconds'), event=why, sleeve=lot['sleeve'], symbol=sym,
                       side=lot['side'], qty=qty, price=px, pnl=round(pnl, 4), equity=round(self.last_eq or 0, 2))
        log.info(f"{why.upper()} {sym} {lot['side']} [{lot['sleeve']}] {qty} @ {px} pnl {pnl:+.2f}")
        return pnl

    def _record_r(self, lot):
        if lot.get('manual') or not lot.get('risk_usd'): return
        h = self.state.setdefault('hist', {}).setdefault(lot['sleeve'], [])
        h.append(round(lot.get('realized', 0.0) / lot['risk_usd'], 3)); del h[:-200]

    def kelly_mult(self, sl):
        k = sl.get('kelly')
        h = self.state.get('hist', {}).get(sl['id'], [])
        if not k or len(h) < k.get('min_trades', 20): return 1.0
        h = np.array(h[-k.get('n', 40):]); w, l = h[h > 0], h[h <= 0]
        if not len(w) or not len(l): return k.get('max', 2.0) if len(w) else k.get('min', 0.25)
        p, b = len(w) / len(h), w.mean() / abs(l.mean())
        f = (p * b - (1 - p)) / b
        return float(np.clip(k.get('frac', 0.5) * f / k.get('ref', 0.1) * 2, k.get('min', 0.25), k.get('max', 2.0)))

    def close_lot(self, key, why, mark=None):
        lot = self.state['lots'][key]
        if not self.dry: self.trade.cancel(lot['symbol'], lot.get('stop_id'))
        qty = lot['qty']
        others = [k for k, l in self.state['lots'].items() if k != key and l['symbol'] == lot['symbol'] and l['side'] == lot['side']]
        if not others and not self.dry:            # last lot on this side: close exactly what the exchange holds (no dust)
            try: qty = self.trade.positions().get((lot['symbol'], lot['side']), qty) or qty
            except Exception: pass
        self._market_close(lot, qty, why, mark)
        self._finish(key, why)
        self.save_state()

    def _finish(self, key, why):
        """Lot fully closed: write a trade-history record and forget the lot."""
        lot = self.state['lots'].pop(key, None)
        if not lot: return
        self._record_r(lot)
        sd = 1 if lot['side'] == 'LONG' else -1
        closes = [f for f in lot.get('fills', []) if f[1] not in ('entry', 'pyramid_add', 'safety_order')]
        cq = sum(f[2] for f in closes) or 1e-12
        exit_avg = sum(f[2] * f[3] for f in closes) / cq if closes else lot['avg']
        gross = lot.get('realized', 0.0); fees = lot.get('fees', 0.0); net = gross - fees
        opened = datetime.fromisoformat(lot['opened']); closed = now_utc()
        rec = dict(id=key, sleeve=lot['sleeve'], strategy=(next((x['name'] for x in self.S['SLEEVES'] if x['id'] == lot['sleeve']), None)
                   or ('Manual' if lot.get('manual') else lot.get('key_strategy'))), symbol=lot['symbol'], side=lot['side'], tf=lot.get('tf'),
                   opened=lot['opened'], closed=closed.isoformat(timespec='seconds'), hours=round((closed - opened).total_seconds() / 3600, 1),
                   entry=lot['e0'], avg_entry=lot['avg'], exit=exit_avg, qty_max=lot.get('qty_max', lot['q0']),
                   notional=round(lot.get('qty_max', lot['q0']) * lot['e0'], 2), risk_usd=lot.get('risk_usd'),
                   pnl_gross=round(gross, 4), fees=round(fees, 4), pnl=round(net, 4),
                   r=round(net / lot['risk_usd'], 2) if lot.get('risk_usd') else None,
                   roi_capital=round(net / lot['eq_at_entry'] * 100, 3) if lot.get('eq_at_entry') else None,
                   move_pct=round(sd * (exit_avg - lot['e0']) / lot['e0'] * 100, 2), exit_reason=why,
                   adds=lot.get('adds', 0), dca=lot.get('dca', 0), tp1=lot.get('tp1', False), manual=lot.get('manual', False),
                   fills=lot.get('fills', []))
        self.history.append(rec); self.history = self.history[-3000:]
        save_json(self.F['history'], self.history)
        self.notify(f"{'✅' if net > 0 else '❌'} CLOSED {lot['side']} {lot['symbol']} [{lot['sleeve']}] {why} · PnL {net:+.2f} USDT ({rec['r']}R) · {rec['hours']}h")

    def miss(self, sl, sym, side, sg, reason):
        k = (sl['id'], sym, sg['time'])
        if any((m['sleeve'], m['symbol'], m['candle']) == k for m in self.missed[-200:]): return
        self.missed.append(dict(candle=sg['time'], logged=now_utc().isoformat(timespec='seconds'), sleeve=sl['id'], strategy=sl['name'],
                                symbol=sym, side=side, price=sg['close'], reason=reason))
        self.missed = self.missed[-600:]
        save_json(self.F['missed'], self.missed)

    def _add_qty(self, lot, q, px, why):
        r = self.rules[lot['symbol']]
        q = self._rd(q, r['step'])
        if q < r['min_qty'] or q * px < r['min_notional']: return False
        if not self.dry:
            o = self.trade.open(lot['symbol'], lot['side'], self._fmt(q, r['step']))
            px = float(o.get('avgPrice') or 0) or px
        lot['avg'] = (lot['avg'] * lot['qty'] + px * q) / (lot['qty'] + q)
        lot['qty'] = self._rd(lot['qty'] + q, r['step'])
        lot['qty_max'] = max(lot.get('qty_max', 0), lot['qty'])
        lot.setdefault('fills', []).append([now_utc().isoformat(timespec='seconds'), why, q, px])
        lot['fees'] = lot.get('fees', 0.0) + q * px * FEE_EST
        self.log_trade(time=now_utc().isoformat(timespec='seconds'), event=why, sleeve=lot['sleeve'], symbol=lot['symbol'],
                       side=lot['side'], qty=q, price=px, stop=lot['stop'], equity=round(self.last_eq or 0, 2))
        log.info(f"{why.upper()} {lot['symbol']} {lot['side']} [{lot['sleeve']}] +{q} @ {px} (avg {lot['avg']:.6g})")
        return True

    # ------------------------------------------------------------ fast loop: soft management on mark price
    def manage(self, marks):
        with self.lock:
            changed = False
            try:                                   # drop lots whose exchange stop already filled before touching anything
                n0 = len(self.state['lots'])
                self.reconcile(self.last_eq or 0)
                changed = len(self.state['lots']) != n0
            except Exception as e:
                log.warning(f'reconcile in manage: {e}'); return
            for key in list(self.state['lots']):
                lot = self.state['lots'].get(key)
                if not lot: continue
                m = marks.get(lot['symbol'])
                if not m: continue
                sd = 1 if lot['side'] == 'LONG' else -1
                g = lot['mgmt']
                ge = lambda lvl: sd * (m - lvl) >= 0           # price at/through a favourable level
                try:
                    if 'dca' in g and lot.get('levels'):
                        while lot['dca'] < len(lot['levels']) and sd * (lot['levels'][lot['dca']] - m) >= 0:
                            if not self._add_qty(lot, lot['q0'] * lot['w'][lot['dca']], m, 'safety_order'): break
                            lot['dca'] += 1
                            lot['tp'] = lot['avg'] + sd * g['dca']['tp_atr'] * lot['atr0']
                            self._replace_stop(lot); changed = True
                        if lot['tp'] is not None and ge(lot['tp']):
                            run = g.get('runner')
                            if run and run.get('dca_frac', 1) < 1:      # runner: bank part, keep the rest at breakeven
                                self._market_close(lot, lot['qty'] * run['dca_frac'], 'basket_tp_part', m)
                                if lot['qty'] <= 0: self._finish(key, 'basket_tp'); changed = True; continue
                                lot['tp'] = None; lot['tp1'] = True; lot['dca'] = len(lot['levels'])
                                be = lot['avg'] * (1 + sd * BE_BUF)
                                if sd * (be - lot['stop']) > 0: lot['stop'] = self._rd(be, self.rules[lot['symbol']]['tick'])
                                lot['e0'] = lot['avg']; lot['R'] = max(lot['R'], abs(lot['avg'] - lot['stop']))
                                self._replace_stop(lot); changed = True
                            else:
                                self.close_lot(key, 'basket_tp', m); changed = True; continue
                    if 'pyramid' in g and lot['adds'] < g['pyramid']['n'] and ge(lot['next_add']):
                        if self._within_cap(lot, lot['q0'] * g['pyramid']['frac'] * m):
                            if self._add_qty(lot, lot['q0'] * g['pyramid']['frac'], m, 'pyramid_add'):
                                lot['adds'] += 1; lot['next_add'] += sd * g['pyramid']['step_r'] * lot['R']
                                self._replace_stop(lot); changed = True
                    if g.get('tp1_r') and not lot['tp1'] and ge(lot['e0'] + sd * g['tp1_r'] * lot['R']):
                        self._market_close(lot, lot['qty'] * g.get('tp1_frac', 0.5), 'take_profit_1', m)
                        lot['tp1'] = True
                        if lot['qty'] <= 0: self._finish(key, 'take_profit_1'); changed = True; continue
                        self._replace_stop(lot); changed = True
                    if g.get('be_r') and ge(lot['e0'] + sd * g['be_r'] * lot['R']):
                        be = lot['avg']
                        if sd * (be - lot['stop']) > 0:
                            lot['stop'] = self._rd(be, self.rules[lot['symbol']]['tick']); self._replace_stop(lot); changed = True
                    if g.get('tp_r') and not g.get('runner') and ge(lot['e0'] + sd * g['tp_r'] * lot['R']):
                        self.close_lot(key, 'take_profit', m); changed = True; continue
                    lot['best'] = max(lot['best'], m) if sd == 1 else min(lot['best'], m)
                    if g.get('trail_atr'):
                        cand = lot['best'] - sd * g['trail_atr'] * lot['atr_now']
                        if sd * (cand - lot['stop']) > 0.1 * lot['atr_now']:
                            lot['stop'] = self._rd(cand, self.rules[lot['symbol']]['tick']); self._replace_stop(lot); changed = True
                    run = g.get('runner')
                    if run and lot['R'] > 0:                       # ratchet: breakeven, then lock profit behind the best R reached
                        bestR = sd * (lot['best'] - lot['e0']) / lot['R']; tgt = None
                        if bestR >= run.get('be_r', 2.0): tgt = lot['avg'] * (1 + sd * BE_BUF)
                        lock = math.floor(bestR / run.get('step_r', 99)) * run.get('step_r', 99) - run.get('gap_r', 99)
                        if run.get('giveback') and bestR >= run.get('gb_from', 4.0): lock = max(lock, bestR * (1 - run['giveback']))
                        if lock > 0:
                            lv = lot['e0'] + sd * lock * lot['R']; tgt = lv if tgt is None else (max(tgt, lv) if sd == 1 else min(tgt, lv))
                        if tgt is not None and sd * (tgt - lot['stop']) > 0.05 * lot.get('atr_now', lot['R']):
                            lot['stop'] = self._rd(tgt, self.rules[lot['symbol']]['tick']); self._replace_stop(lot); changed = True
                            log.info(f"RUNNER {lot['symbol']} [{lot['sleeve']}] stop raised to {lot['stop']} (best {bestR:.1f}R)")
                except Exception as e:
                    log.warning(f'manage {key}: {e}')
            if changed: self.save_state()

    def _within_cap(self, lot, add_notional):
        sl = next((x for x in self.S['SLEEVES'] if x['id'] == lot['sleeve']), None)
        if not sl or lot.get('manual'): return True
        sleeve_eq = (self.last_eq or 0) * sl['share']
        used = sum(l['qty'] * l['avg'] for l in self.state['lots'].values() if l['sleeve'] == lot['sleeve'])
        return used + add_notional <= self.S['MAX_LEVERAGE'] * sleeve_eq

    # ------------------------------------------------------------ reconcile with exchange
    def reconcile(self, eq, live=None):
        live = self.trade.positions() if live is None else live
        st = self.state
        groups = {}
        for k, l in st['lots'].items(): groups.setdefault((l['symbol'], l['side']), []).append(k)
        for (sym, side), keys in groups.items():
            expected = sum(st['lots'][k]['qty'] for k in keys)
            have = live.get((sym, side), 0.0)
            tol = self.rules[sym]['step'] * (len(keys) + 1) if sym in self.rules else 1e-9
            if have >= expected - tol: continue
            sd = 1 if side == 'LONG' else -1
            try:
                open_tags = self.trade.open_stop_tags(sym)
            except Exception as ex:
                log.warning(f'open orders {sym}: {ex}'); open_tags = None
            gone = [k for k in keys if open_tags is not None and st['lots'][k].get('stop_id') not in open_tags]
            if not gone:      # fallback: infer from price (highest stop for longs triggers first)
                gone = sorted(keys, key=lambda k: -sd * st['lots'][k]['stop'])
            for k in gone:
                l = st['lots'][k]
                pnl = sd * (l['stop'] - l['avg']) * l['qty']
                log.info(f"STOPPED {sym} {side} [{l['sleeve']}] stop {l['stop']} ~pnl {pnl:+.2f}")
                self.log_trade(time=now_utc().isoformat(timespec='seconds'), event='stop', sleeve=l['sleeve'], symbol=sym,
                               side=side, qty=l['qty'], price=l['stop'], pnl=round(pnl, 4), equity=round(eq, 2))
                l['realized'] = l.get('realized', 0.0) + pnl
                l.setdefault('fills', []).append([now_utc().isoformat(timespec='seconds'), 'stop', l['qty'], l['stop']])
                l['fees'] = l.get('fees', 0.0) + l['qty'] * l['stop'] * FEE_EST
                expected -= l['qty']; self._finish(k, 'stop')
                self.notify(f"🛑 STOP {side} {sym} [{l['sleeve']}] at {l['stop']}")
                if have >= expected - tol: break
            # anything still unexplained: resync the remaining lots to what the exchange actually holds
            rest = [k for k in keys if k in st['lots']]
            if rest and have < expected - tol:
                scale = have / expected if expected > 0 else 0
                for k in rest:
                    st['lots'][k]['qty'] = self._rd(st['lots'][k]['qty'] * scale, self.rules[sym]['step'])
                    if st['lots'][k]['qty'] <= 0: self._finish(k, 'resync')
                    else:
                        try: self._replace_stop(st['lots'][k])
                        except Exception as ex: log.warning(f'stop resync {sym}: {ex}')
                log.warning(f'{sym} {side}: exchange holds less than expected ({have} vs {expected:.6g}, gone={gone}, open_tags={open_tags}) - lots resized to match')

    # ------------------------------------------------------------ candle-close cycle
    def cycle(self, tf, reason='candle close'):
        with self.lock:
            st, Sg = self.state, self.S
            eq = self.equity()
            today = now_utc().date().isoformat()
            if st['day'] != today: st.update(day=today, day_start_equity=eq, halted=False)
            st['peak_equity'] = max(st.get('peak_equity') or eq, eq)
            if eq / st['day_start_equity'] - 1 <= -Sg['DAILY_LOSS_HALT'] and not st['halted']:
                st['halted'] = True; log.warning(f'DAILY LOSS HALT at equity {eq:.2f}'); self.notify(f'⚠️ Daily loss halt hit - equity {eq:.2f}')
            if Sg['PEAK_DD_FLATTEN'] > 0 and eq <= st['peak_equity'] * (1 - Sg['PEAK_DD_FLATTEN']):
                log.warning('PEAK DRAWDOWN LIMIT - closing all bot trades and pausing'); self.flatten(manual_too=False)
            self.reconcile(eq)
            sleeves = [sl for sl in Sg['SLEEVES'] if sl['tf'] == tf]
            syms = sorted({s for sl in sleeves for s in self.sleeve_symbols(sl, include_off=True)} | {l['symbol'] for l in st['lots'].values() if l.get('tf') == tf})
            if not syms: return
            log.info(f'--- {tf} cycle ({reason}) | equity {eq:.2f} ---')
            sigs, frames = self.compute_signals(tf, syms)
            self.signals.update(sigs); self.signals_time = now_utc().isoformat(timespec='seconds')
            # refresh ATR for trailing + signal/time exits
            for k, l in list(st['lots'].items()):
                if l.get('tf') != tf or l.get('manual'): continue
                d = frames.get(l['symbol'])
                if d is not None: l['atr_now'] = float(d.atr.iloc[-1])
                sg = sigs.get(f"{l['sleeve']}|{l['symbol']}")
                sl = next((x for x in Sg['SLEEVES'] if x['id'] == l['sleeve']), None)
                bars = (now_utc() - datetime.fromisoformat(l['opened'])).total_seconds() / TF_SEC[tf]
                ex = sg and (sg['lx'] if l['side'] == 'LONG' else sg['sx'])
                run = l['mgmt'].get('runner')
                if run and d is not None and len(d):
                    sd_ = 1 if l['side'] == 'LONG' else -1; c_ = float(d.c.iloc[-1])
                    trend_ok = (c_ > d.e50.iloc[-1] and d.st.iloc[-1] > 0) if sd_ == 1 else (c_ < d.e50.iloc[-1] and d.st.iloc[-1] < 0)
                    winning = sd_ * (c_ - l['avg']) > 0
                    if winning and trend_ok:
                        if ex: log.info(f"RUNNER {l['symbol']} [{l['sleeve']}] exit signal ignored - winning and trend still up")
                        ex = False
                    elif winning and run.get('trend_exit') and not trend_ok: ex = True
                if ex or (l['mgmt'].get('max_bars') and not run and bars >= l['mgmt']['max_bars']) or (sl is None and False):
                    self.close_lot(k, 'exit_signal' if ex else 'time_exit', sg['close'] if sg else None)
            # entries (every signal that is not taken is logged with the reason)
            if Sg['ENTRIES_PAUSED'] or st['halted']:
                log.info('entries paused' + (' (daily halt)' if st['halted'] else ''))
            for sl in sleeves:
                held = [l['symbol'] for l in st['lots'].values() if l['sleeve'] == sl['id']]
                for s in self.sleeve_symbols(sl, include_off=True):
                    sg = sigs.get(f"{sl['id']}|{s}")
                    if not sg or not (sg['le'] or sg['se']): continue
                    side = 'LONG' if sg['le'] else 'SHORT'
                    why = None
                    if not Sg['SYMBOLS_ON'].get(s, True): why = 'coin switched off'
                    elif not sl['enabled']: why = 'strategy slot switched off'
                    elif Sg['ENTRIES_PAUSED']: why = 'entries paused'
                    elif st['halted']: why = 'daily loss halt'
                    elif s in held: why = 'already in a trade on this coin'
                    elif len(held) >= sl['max_pos']: why = f"max positions reached ({sl['max_pos']})"
                    elif sl.get('hours') and pd.Timestamp(sg['time']).hour not in sl['hours']: why = 'outside entry hours'
                    elif sl.get('vol_max_pct') and sg.get('vol_rank', 0.5) > sl['vol_max_pct']: why = 'volatility filter'
                    elif side == 'SHORT' and not self.hedge: why = 'hedge mode off (shorts unavailable)'
                    if why is None:
                        self.last_skip = ''
                        if self.open_lot(sl, s, side, sg, frames.get(s), eq):
                            held.append(s); continue
                        why = self.last_skip or 'order failed'
                    self.miss(sl, s, side, sg, why)
            st['last_cycle'][tf] = now_utc().isoformat(timespec='minutes')
            self.save_state()
            self.record_equity(eq)

    def open_lot(self, sl, sym, side, sg, df, eq, risk=None, manual=False, stop_atr=None, tp_r=None):
        r = self.rules[sym]
        g = dict(self.mgmt(sl)) if sl else {}
        if manual:
            g = dict(stop_atr=stop_atr or 2.5)
            if tp_r: g['tp_r'] = tp_r
        sd = 1 if side == 'LONG' else -1
        sleeve_eq = eq if manual else eq * sl['share']
        risk = risk if risk is not None else sl['risk'] * self.kelly_mult(sl)
        risk_usd = sleeve_eq * risk
        px = self.trade.marks().get(sym) if not self.dry else sg['close']
        atr = sg['atr']
        if 'dca' in g:
            dc = g['dca']
            lv = [px - sd * k * dc['step_atr'] * atr for k in range(dc['n'] + 1)]
            w = [dc['scale'] ** k for k in range(dc['n'] + 1)]
            stop = lv[-1] - sd * dc['stop_atr'] * atr
            qty = risk_usd / sum(wk * abs(lk - stop) for wk, lk in zip(w, lv))
            R = abs(px - stop)
        else:
            R = g.get('stop_atr', 2.5) * atr
            qty = risk_usd / R
            stop = px - sd * R
        qty_raw = qty
        if not manual:
            used = sum(l['qty'] * l['avg'] for l in self.state['lots'].values() if l['sleeve'] == sl['id'])
            qty = min(qty, max(0.0, self.S['MAX_LEVERAGE'] * sleeve_eq - used) / px)
        qty = self._rd(qty, r['step'])
        if qty < r['min_qty'] or qty * px < r['min_notional']:
            log.info(f"SKIP {sym} [{sl['id'] if sl else 'MAN'}] size {qty} below Binance minimum")
            self.last_skip = ('leverage cap reached for this slot' if qty_raw * px >= r['min_notional'] and qty_raw >= r['min_qty']
                              else 'size below Binance minimum (raise capital or risk)')
            return False
        reason = 'manual' if manual else 'signal'
        if not manual and self.S.get('AI_FILTER'):
            last = df.tail(12)[['o', 'h', 'l', 'c']].round(6).values.tolist() if df is not None else []
            try: funding = self.data.premium(sym)['lastFundingRate']
            except Exception: funding = 'n/a'
            dec, why = review(dict(self.cfg, AI_FILTER='on'), sym, f"{sl['name']} {side}", dict(sg, e20=float(df.e20.iloc[-1]), e50=float(df.e50.iloc[-1]),
                              e200=float(df.e200.iloc[-1]), st_dir=int(df.st.iloc[-1]), ret42=float(df.ret42.iloc[-1]), ret180=float(df.ret180.iloc[-1])), last, funding)
            log.info(f'AI review {sym} {side}: {dec} - {why}')
            if dec == 'skip':
                self.log_trade(time=sg['time'], event='ai_veto', sleeve=sl['id'], symbol=sym, side=side, qty=qty, price=px, note=why)
                self.last_skip = f'Claude review vetoed: {why}'
                return False
            reason = why
        if self.dry:
            log.info(f'[dry] would open {side} {qty} {sym} stop {stop:.6g}'); return True
        try:
            self.trade.set_margin_type(sym, 'CROSSED')
            self.trade.set_leverage(sym, max(10, int(self.S['MAX_LEVERAGE'])))
        except Exception as e:
            log.warning(f'leverage/margin setup {sym}: {e}')
        o = self.trade.open(sym, side, self._fmt(qty, r['step']))
        fill = float(o.get('avgPrice') or 0) or px
        stop = self._rd(fill - sd * (abs(px - stop)), r['tick'])
        key = f"{'MAN' if manual else sl['id']}|{sym}|{side}|{int(time.time())}"
        lot = dict(symbol=sym, side=side, sleeve='MAN' if manual else sl['id'], key_strategy=None if manual else sl['key'],
                   qty=qty, q0=qty, avg=fill, e0=fill, R=abs(fill - stop), stop=stop, stop_id=None, tp1=False, adds=0, dca=0,
                   best=fill, atr0=atr, atr_now=atr, mgmt=g, tf=(sl['tf'] if sl else '4h'), manual=manual,
                   opened=now_utc().isoformat(timespec='seconds'), risk_usd=round(risk_usd, 2), eq_at_entry=round(eq, 2),
                   qty_max=qty, fills=[[now_utc().isoformat(timespec='seconds'), 'entry', qty, fill]], fees=qty * fill * FEE_EST)
        if 'pyramid' in g: lot['next_add'] = fill + sd * g['pyramid']['step_r'] * lot['R']
        if 'dca' in g:
            lot['levels'] = [fill - sd * k * g['dca']['step_atr'] * atr for k in range(1, g['dca']['n'] + 1)]
            lot['w'] = [g['dca']['scale'] ** k for k in range(1, g['dca']['n'] + 1)]
            lot['tp'] = fill + sd * g['dca']['tp_atr'] * atr
        try:
            self._replace_stop(lot)
        except Exception as e:
            log.error(f'STOP FAILED {sym} ({e}) - closing for safety')
            self.trade.close(sym, side, self._fmt(qty, r['step'])); self.last_skip = f'stop order failed: {e}'; return False
        self.state['lots'][key] = lot
        self.log_trade(time=now_utc().isoformat(timespec='seconds'), event='entry', sleeve=lot['sleeve'], symbol=sym, side=side,
                       qty=qty, price=fill, stop=stop, equity=round(eq, 2), note=reason)
        log.info(f"ENTRY {sym} {side} [{lot['sleeve']}] {qty} @ {fill} stop {stop}")
        self.notify(f"🚀 OPEN {side} {sym} [{lot['sleeve']}] {qty} @ {fill} · stop {stop} · risk {risk_usd:.2f} USDT")
        self.save_state()
        return True

    # ------------------------------------------------------------ panel actions
    def manual_trade(self, sym, side, risk_pct, stop_atr, tp_r=None):
        with self.lock:
            eq = self.equity()
            df = self.candles(sym, '4h')
            sg = dict(close=float(df.c.iloc[-1]), atr=float(df.atr.iloc[-1]), time=now_utc().isoformat(timespec='minutes'))
            return self.open_lot(None, sym, side, sg, df, eq, risk=risk_pct, manual=True, stop_atr=stop_atr, tp_r=tp_r)

    def take_signal(self, sleeve_id, sym):
        """Open a trade now from a signal shown on the Signals tab (uses that slot's risk and management)."""
        with self.lock:
            sl = next((x for x in self.S['SLEEVES'] if x['id'] == sleeve_id), None)
            if not sl: raise ValueError('unknown strategy slot')
            sg = self.signals.get(f'{sleeve_id}|{sym}')
            if not sg or not (sg['le'] or sg['se']): raise ValueError('no active signal for this coin/slot')
            if any(l['sleeve'] == sleeve_id and l['symbol'] == sym for l in self.state['lots'].values()):
                raise ValueError('this slot already holds that coin')
            side = 'LONG' if sg['le'] else 'SHORT'
            if side == 'SHORT' and not self.hedge: raise ValueError('hedge mode is off - shorts unavailable')
            eq = self.equity()
            return self.open_lot(sl, sym, side, sg, self.candles(sym, sl['tf']), eq)

    def move_stop(self, key, price):
        with self.lock:
            lot = self.state['lots'][key]
            sd = 1 if lot['side'] == 'LONG' else -1
            mark = self.trade.marks().get(lot['symbol'])
            if sd * (price - mark) >= 0: raise ValueError(f'stop must be on the losing side of the current price {mark}')
            lot['stop'] = self._rd(price, self.rules[lot['symbol']]['tick'])
            self._replace_stop(lot); self.save_state()
            log.info(f"STOP MOVED {lot['symbol']} [{lot['sleeve']}] -> {lot['stop']}")

    def flatten(self, manual_too=True):
        with self.lock:
            for k in list(self.state['lots']):
                if manual_too or not self.state['lots'][k].get('manual'):
                    try: self.close_lot(k, 'flatten')
                    except Exception as e: log.error(f'flatten {k}: {e}')
            self.S['ENTRIES_PAUSED'] = True; self.save_settings()
