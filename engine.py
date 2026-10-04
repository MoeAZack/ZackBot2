"""ZackBot live engine — runs any mix of strategies ("sleeves") from strategies.py on Binance USD-M futures.

Hard stops live on Binance (STOP_MARKET per lot). Softer management (partial take-profit, breakeven,
trailing, pyramiding adds, DCA safety orders, basket take-profit) is checked every few seconds on the mark price.
Signals (entries/exits) are evaluated right after each candle close of the sleeve's timeframe.
Hedge mode is used so longs and shorts on the same coin can coexist.
"""
import csv, json, math, os, re, time, logging, threading, copy, collections
from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd

import strategies as S
from binance_client import Futures, MAINNET, TESTNET, BinanceError, AmbiguousOrder
from ai_filter import review

log = logging.getLogger('zackbot')
TF_SEC = {'15m': 900, '1h': 3600, '4h': 14400}
FEE_EST = 0.0005        # taker fee estimate per fill, used for net PnL in the trade history
BE_BUF = 0.0015         # breakeven stops sit just past the average entry so fees are covered
MANUAL_MAX_RISK = 0.05  # manual trades: at most 5% of bot capital at risk
SYM_RE = re.compile(r'^[A-Z0-9]{2,20}USDT$')
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
    'original': dict(name='Original A/B (core 8)', note='What ran first: EMAx|Supertrend + EMAx|Momentum on 8 coins, 3%. Backtest 2024-10-20 to 2026-10-04: $500 -> $3,223, max DD -39%, worst month -7.8%, ~2 trades/week.', bt={'end': 3223, 'dd': -39, 'wm': -7.8, 'wk': 2, 'start': '2024-10-20', 'stop': '2026-10-04', 'engine': 'v3', 'insample': True},
                     sleeves=[sleeve('A', 'ema_st', .5, .03, 4, 'core8'), sleeve('B', 'ema_mom', .5, .03, 4, 'core8')]),
    'calm': dict(name='Calm', note='Lowest risk: 1% per trade, three strategies. Both years positive. Backtest 2024-10-20 to 2026-10-04: $500 -> $1,545, max DD -13%, worst month -3.3%, ~7 trades/week.', bt={'end': 1545, 'dd': -13, 'wm': -3.3, 'wk': 7, 'start': '2024-10-20', 'stop': '2026-10-04', 'engine': 'v3', 'insample': True},
                 sleeves=[sleeve('MOM', 'ema_mom', 1 / 3, .01, 8), sleeve('ST', 'ema_st', 1 / 3, .01, 4, 'core8'),
                          sleeve('DCA', 'dca_dip', 1 / 3, .01, 6)]),
    'balanced': dict(name='Balanced', note='2% per trade with pyramiding on the trend slots. Backtest 2024-10-20 to 2026-10-04: $500 -> $5,974, max DD -29%, worst month -7.3%, ~10 trades/week.', bt={'end': 5974, 'dd': -29, 'wm': -7.3, 'wk': 10, 'start': '2024-10-20', 'stop': '2026-10-04', 'engine': 'v3', 'insample': True},
                     sleeves=[sleeve('MOM', 'ema_mom', 1 / 3, .02, 8, mgmt=PY), sleeve('ST', 'ema_st', 1 / 3, .02, 4, 'core8', mgmt=PY),
                              sleeve('DCA', 'dca_dip', 1 / 3, .02, 6)]),
    'aggressive': dict(name='Aggressive', note='3% per trade, same mix as Balanced. Backtest 2024-10-20 to 2026-10-04: $500 -> $11,886, max DD -39%, worst month -10.8%, ~10 trades/week.', bt={'end': 11886, 'dd': -39, 'wm': -10.8, 'wk': 10, 'start': '2024-10-20', 'stop': '2026-10-04', 'engine': 'v3', 'insample': True},
                       sleeves=[sleeve('MOM', 'ema_mom', 1 / 3, .03, 8, mgmt=PY), sleeve('ST', 'ema_st', 1 / 3, .03, 4, 'core8', mgmt=PY),
                                sleeve('DCA', 'dca_dip', 1 / 3, .03, 6)]),
    'active': dict(name='Active (1h, more trades)', note='1h DCA dip + 1h Breakout/pyramiding on core 8 - many more trades, only 6 months of 1h data tested. Backtest 2026-04-15 to 2026-10-04: $500 -> $961, max DD -24%, worst month -3.8%, ~20 trades/week.', bt={'end': 961, 'dd': -24, 'wm': -3.8, 'wk': 20, 'start': '2026-04-15', 'stop': '2026-10-04', 'engine': 'v3', 'insample': True},
                   sleeves=[sleeve('DCA1H', 'dca_dip', .5, .02, 4, 'core8', tf='1h'), sleeve('BRK1H', 'breakout_pyramid', .5, .02, 4, 'core8', tf='1h')]),
    'boost_active': dict(name='Boost + Active (4h + 1h mix)', note='Boost on 4h with half the capital, Active 1h with the other half (tested side by side). Backtest 2026-04-15 to 2026-10-04: $500 -> $1,196, max DD -32%, worst month -8.0%, ~29 trades/week.', bt={'end': 1196, 'dd': -32, 'wm': -8.0, 'wk': 29, 'start': '2026-04-15', 'stop': '2026-10-04', 'engine': 'v3', 'insample': True},
                         sleeves=[sleeve('MOM', 'ema_mom', .25, .05, 8, mgmt=PY), sleeve('DCA', 'dca_dip', .25, .05, 6),
                                  sleeve('DCA1H', 'dca_dip', .25, .02, 4, 'core8', tf='1h'), sleeve('BRK1H', 'breakout_pyramid', .25, .02, 4, 'core8', tf='1h')]),
    'boost': dict(name='Boost (short-term, high risk)', note='5% per trade. For short sprints only - deep drawdowns. Backtest 2024-10-20 to 2026-10-04: $500 -> $23,792, max DD -54%, worst month -27.8%, ~9 trades/week.', bt={'end': 23792, 'dd': -54, 'wm': -27.8, 'wk': 9, 'start': '2024-10-20', 'stop': '2026-10-04', 'engine': 'v3', 'insample': True},
                  sleeves=[sleeve('MOM', 'ema_mom', .5, .05, 8, mgmt=PY), sleeve('DCA', 'dca_dip', .5, .05, 6)]),
}

GLOBAL_DEFAULTS = dict(COMPOUND=False, CAP_SINCE='', CAP_ADJ=[], CAP_CYCLES=[], TELEGRAM_ON=False, TELEGRAM_TOKEN='', TELEGRAM_CHAT='', MAX_LEVERAGE=10, DAILY_LOSS_HALT=0.08, PEAK_DD_FLATTEN=0.0, CAPITAL_CAP=500.0,
                       ENTRIES_PAUSED=False, AI_FILTER=False, PRESET='original',
                       UNIVERSE=list(TOP40), SYMBOLS_ON={}, RUN_IN_BACKGROUND=True)


def now_utc():
    return datetime.now(timezone.utc)


def _egypt_offset(t):
    """Fallback when no tz database is available: Egypt is UTC+2, UTC+3 from the last Friday of April to the last Thursday of October."""
    def last_wd(y, m, wd):
        d = datetime(y, m + 1, 1) - timedelta(days=1) if m < 12 else datetime(y, 12, 31)
        return d - timedelta(days=(d.weekday() - wd) % 7)
    # DST starts at local midnight in winter time (UTC+2) and ends at local midnight in summer time (UTC+3)
    y = t.year
    start, end = last_wd(y, 4, 4), last_wd(y, 10, 3) + timedelta(days=1)
    return timedelta(hours=3) if start.date() <= (t + timedelta(hours=2)).date() and (t + timedelta(hours=3)).date() < end.date() else timedelta(hours=2)


try:
    from zoneinfo import ZoneInfo
    CAIRO = ZoneInfo('Africa/Cairo')
except Exception:          # no tzdata on this machine
    CAIRO = None


def cairo_now():
    t = now_utc()
    if CAIRO is not None: return t.astimezone(CAIRO)
    return (t + _egypt_offset(t)).replace(tzinfo=None)


def trading_day():
    """The bot's trading day (daily loss halt, daily summary) runs midnight-to-midnight Cairo time, DST-aware."""
    return cairo_now().date().isoformat()


def next_reset_utc():
    c = cairo_now()
    nxt = datetime.combine(c.date() + timedelta(days=1), datetime.min.time())
    if CAIRO is not None: return nxt.replace(tzinfo=CAIRO).astimezone(timezone.utc).isoformat(timespec='seconds')
    return (nxt - _egypt_offset(nxt)).replace(tzinfo=timezone.utc).isoformat(timespec='seconds')


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
        self.state.setdefault('orphans', []); self.state.setdefault('last_cycle', {})
        if not self.S.get('CAP_SINCE'):                  # first v3 start: bot capital counts closed P&L from now on
            self.S['CAP_SINCE'] = now_utc().isoformat(timespec='seconds'); self.save_settings()
        self.equity_hist = json.load(open(self.F['equity'])) if os.path.exists(self.F['equity']) else []
        self.rules, self._cache, self._kc, self.signals, self.signals_time = {}, {}, {}, {}, None
        self.last_eq = self.last_balance = None
        self.hedge = False
        self.run_now = threading.Event()
        self.history = self._load_list('history'); self.missed = self._load_list('missed')
        self.last_account, self.last_skip, self.corr = {}, '', None
        self.error = None
        self.connected = False
        self.marks, self.marks_t = {}, 0.0
        self.guard_eq = None                      # bot capital used by the safety limits and shown in the app
        self.untracked = {}                       # exchange positions the engine has no record of
        self._lev = {}                            # leverage already set per symbol
        self.health = dict(errors=collections.deque(maxlen=30), last_manage_ok=None, last_cycle_ok={}, manage_fail_streak=0,
                           last_sync=None, alerted=False)

    def _load_list(self, k):
        try: return json.load(open(self.F[k])) if os.path.exists(self.F[k]) else []
        except Exception: return []

    # ------------------------------------------------------------ notifications (Telegram, optional)
    def notify(self, text):
        S_ = self.S
        tok = self.cfg.get('TELEGRAM_TOKEN') or S_.get('TELEGRAM_TOKEN')
        if not (S_.get('TELEGRAM_ON') and tok and S_.get('TELEGRAM_CHAT')): return
        def _send():
            try:
                import requests
                requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", timeout=10,
                              data=dict(chat_id=S_['TELEGRAM_CHAT'], text=f"[ZackBot {'LIVE' if self.live else 'paper'}] {text}"))
            except Exception as ex: log.warning('telegram send failed: ' + type(ex).__name__)
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

    def bot_unrealized(self):
        """Open P&L of the engine's own lots on the latest mark prices (falls back to the account figure)."""
        lots = list(self.state['lots'].values())
        if not lots: return 0.0
        if self.marks and all(l['symbol'] in self.marks for l in lots):
            return sum((1 if l['side'] == 'LONG' else -1) * (self.marks[l['symbol']] - l['avg']) * l['qty'] for l in lots)
        return float(self.last_account.get('totalUnrealizedProfit', 0) or 0)

    def equity(self):
        """Returns the SIZING equity (what new trades are sized on) and refreshes guard_eq (bot capital incl. open P&L,
        what the daily-loss halt, drawdown flatten and the dashboard use).
          fixed mode:    sizing = start amount
          compound mode: sizing = start amount + closed P&L since start - withdrawals + deposits
          guard (both):  start + closed P&L + open P&L - withdrawals + deposits   (all capped by the real account)
        With start amount 0 the whole account balance is used for everything."""
        acc = self.trade.account()
        self.last_account = {k: float(acc.get(k, 0) or 0) for k in ('totalMarginBalance', 'totalInitialMargin', 'totalMaintMargin',
                                                                     'availableBalance', 'totalUnrealizedProfit', 'totalWalletBalance')}
        bal = float(acc['totalMarginBalance'])
        self.last_balance = bal
        self.health['last_sync'] = now_utc().isoformat(timespec='seconds')
        info = self.capital_info()
        base = info['base']
        if base <= 0:
            self.guard_eq = self.last_eq = bal
        else:
            self.guard_eq = max(0.0, min(bal, info['capital']))
            sizing = info['base'] + info['realized'] + info['adj_total'] if self.S.get('COMPOUND') else base
            self.last_eq = max(0.0, min(bal, sizing))
        return self.last_eq

    # ------------------------------------------------------------ capital: fixed / compounding, withdrawals, fresh starts
    def capital_info(self, acc=None):
        S = self.S
        since = S.get('CAP_SINCE') or ''
        realized = sum(h.get('pnl') or 0 for h in self.history if h.get('closed', '') >= since)
        adjs = S.get('CAP_ADJ', [])
        adj = sum(a['amount'] for a in adjs)
        upnl = self.bot_unrealized()
        base = float(S.get('CAPITAL_CAP') or 0)
        cap = base + realized + adj + upnl
        self.cap = dict(mode='compound' if S.get('COMPOUND') else 'fixed', base=base, since=since, realized=round(realized, 2),
                        withdrawn=round(-sum(a['amount'] for a in adjs if a['amount'] < 0), 2),
                        deposited=round(sum(a['amount'] for a in adjs if a['amount'] > 0), 2), adj_total=round(adj, 2),
                        unrealized=round(upnl, 2), capital=round(cap, 2), growth=round((cap - adj) / base * 100 - 100, 2) if base else None,
                        sizing=round(self.last_eq or 0, 2), adj=adjs[-50:], cycles=S.get('CAP_CYCLES', [])[-50:])
        return self.cap

    def shift_guards(self, delta):
        """Bot capital moved for a non-trading reason (withdrawal, deposit, new start amount): move the safety baselines with it."""
        st = self.state
        for k in ('peak_equity', 'day_start_equity'):
            if st.get(k) is not None: st[k] = max(0.01, st[k] + delta)

    def set_capital_base(self, new):
        with self.lock:
            new = float(new); old = float(self.S.get('CAPITAL_CAP') or 0)
            if new < 0: raise ValueError('start amount cannot be negative')
            if old > 0 and new > 0: self.shift_guards(new - old)
            else: self.state['peak_equity'] = None; self.state['day_start_equity'] = None
            self.S['CAPITAL_CAP'] = new; self.save_settings(); self.save_state()
            try: self.equity()
            except Exception as ex: log.warning(f'equity refresh: {ex}')
            if self.state.get('day_start_equity') is None and self.guard_eq: self.state['day_start_equity'] = self.state['peak_equity'] = self.guard_eq

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
                self.shift_guards(sgn * amt)                       # a withdrawal is not a trading loss for the safety limits
                msg = f'{kind} of {amt:.2f} recorded'
            elif kind == 'reset':
                new = float(amount) if amount not in (None, '') else info['capital']
                if new <= 0: raise ValueError('start amount must be above 0')
                S.setdefault('CAP_CYCLES', []).append(dict(start=info['since'], end=now, start_cap=info['base'], end_cap=info['capital'],
                                                           pnl=info['realized'], withdrawn=info['withdrawn'], deposited=info['deposited'], note=note))
                S['CAPITAL_CAP'] = new; S['CAP_SINCE'] = now; S['CAP_ADJ'] = []
                st['peak_equity'] = None; st['day_start_equity'] = None; st['halted'] = False
                msg = f'fresh start with {new:.2f}'
            else:
                raise ValueError('unknown capital action')
            self.save_settings(); self.save_state()
            try: self.equity()
            except Exception as ex: log.warning(f'equity refresh: {ex}')
            if st.get('day_start_equity') is None and self.guard_eq: st['day_start_equity'] = st['peak_equity'] = self.guard_eq
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

    def _orphan_sleeves(self, tf):
        """Open lots whose strategy slot was renamed/removed still get their own strategy's exit signals."""
        ids = {x['id'] for x in self.S['SLEEVES']}; out = {}
        for l in self.state['lots'].values():
            if l.get('manual') or l.get('tf', '4h') != tf or l['sleeve'] in ids or not l.get('key_strategy'): continue
            if l['key_strategy'] not in S.STRATEGIES: continue
            out[l['sleeve']] = dict(id=l['sleeve'], key=l['key_strategy'], tf=tf, sides='both', symbols=[], params=None)
        return list(out.values())

    def compute_signals(self, tf, syms, extra=()):
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
        for sl in list(self.S['SLEEVES']) + list(extra):
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
    def err(self, msg):
        self.health['errors'].append([now_utc().isoformat(timespec='seconds'), str(msg)[:200]])
        log.warning(msg)

    def _cancel_or_park(self, sym, tag):
        """Cancel a stop; if Binance cannot be reached, remember it and retry later (never leave a stale stop behind)."""
        if self.dry or not tag: return
        try: self.trade.cancel(sym, tag)
        except Exception as e:
            self.state.setdefault('orphans', []).append([sym, tag])
            self.err(f'cancel stop {sym} {tag} failed ({e}) - will retry')

    def _replace_stop(self, lot, stop=None):
        """Place the stop at `stop` (or the current lot stop, e.g. after a size change) FIRST, then cancel the old one.
        The lot's recorded stop only changes once Binance has accepted the new order. Returns True on success."""
        if self.dry:
            if stop is not None: lot['stop'] = stop
            return True
        r = self.rules[lot['symbol']]
        new_stop = self._rd(stop, r['tick']) if stop is not None else lot['stop']
        sd = 1 if lot['side'] == 'LONG' else -1
        try:
            new = self.trade.stop(lot['symbol'], lot['side'], self._fmt(lot['qty'], r['step']), self._fmt(new_stop, r['tick']))
        except AmbiguousOrder as e:
            if e.tag: self.state.setdefault('orphans', []).append([lot['symbol'], e.tag])   # cancel it if it exists; a fresh stop follows
            lot['stop_dirty'] = True; lot['last_order_t'] = time.time()
            self.err(f"stop update {lot['symbol']} [{lot['sleeve']}] unconfirmed: {e} - previous stop kept, retrying")
            return False
        except BinanceError as e:
            if e.code == -2021:          # stop would trigger immediately: price is already through it -> close now
                log.warning(f"{lot['symbol']} [{lot['sleeve']}] stop {new_stop} already crossed - closing at market")
                lot['force_close'] = True
            lot['stop_dirty'] = True
            self.err(f"stop update {lot['symbol']} [{lot['sleeve']}] failed: {e} - previous stop kept")
            return False
        except Exception as e:
            lot['stop_dirty'] = True
            self.err(f"stop update {lot['symbol']} [{lot['sleeve']}] failed: {e} - previous stop kept")
            return False
        old = lot.get('stop_id')
        lot['stop_id'], lot['stop'], lot['stop_dirty'], lot['last_order_t'] = new, new_stop, False, time.time()
        if old and old != new: self._cancel_or_park(lot['symbol'], old)
        return True

    def _market_close(self, lot, qty, why, mark=None, post=None):
        """Close qty at market. If Binance's answer is lost (AmbiguousOrder) the lot is marked 'pending' and reconcile()
        decides from the real position whether it filled - so a partial take-profit can never fire twice."""
        sym, r = lot['symbol'], self.rules[lot['symbol']]
        qty = self._rd(qty, r['step'])
        if qty <= 0: return 0.0
        px = mark or lot['avg']
        if not self.dry:
            lot['last_order_t'] = time.time()
            try:
                o = self.trade.close(sym, lot['side'], self._fmt(qty, r['step']))
            except AmbiguousOrder as e:
                lot['pending'] = dict(kind='close', qty=qty, px=px, why=why, post=post or {}, t=time.time())
                self.save_state(); self.err(f'{sym} close unconfirmed ({e}) - waiting for Binance position to confirm'); raise
            px = float(o.get('avgPrice') or 0) or px
        return self._apply_close(lot, qty, px, why)

    def _apply_close(self, lot, qty, px, why):
        sym, r = lot['symbol'], self.rules[lot['symbol']]
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

    def _resolve_pending(self, key, have, expected, tol):
        """A close/add whose answer was lost: decide from the exchange position whether it filled. Returns True if resolved."""
        l = self.state['lots'][key]; pd_ = l['pending']; age = time.time() - pd_.get('t', 0)
        target = expected - pd_['qty'] if pd_['kind'] == 'close' else expected + pd_['qty']
        if abs(have - target) <= tol:                                    # it filled
            del l['pending']
            if pd_['kind'] == 'close':
                self._apply_close(l, pd_['qty'], pd_['px'], pd_['why'])
            else:
                self._apply_add(l, pd_['qty'], pd_['px'], pd_['why'])
            post = dict(pd_.get('post') or {})
            fin = post.pop('finish', None)
            l.update(post)
            if 'dca' in l.get('mgmt', {}) and pd_['kind'] == 'add' and l.get('tp') is not None:
                sd = 1 if l['side'] == 'LONG' else -1
                l['tp'] = l['avg'] + sd * l['mgmt']['dca']['tp_atr'] * l['atr0']
            log.info(f"{l['symbol']} [{l['sleeve']}] unconfirmed {pd_['kind']} confirmed from the position")
            if fin or l['qty'] <= 0: self._finish(key, fin or pd_['why'])
            else: self._replace_stop(l)
            return True
        if abs(have - expected) <= tol and age > 20:                       # it never filled
            del l['pending']; log.info(f"{l['symbol']} [{l['sleeve']}] unconfirmed {pd_['kind']} did not fill - will retry"); return True
        if age > 300:
            del l['pending']; self.err(f"{l['symbol']} [{l['sleeve']}] could not confirm {pd_['kind']} after 5 min - resyncing to the exchange"); return False
        return None

    def _record_r(self, lot):
        if lot.get('manual') or not lot.get('risk_usd'): return
        h = self.state.setdefault('hist', {}).setdefault(lot['sleeve'], [])
        h.append(round((lot.get('realized', 0.0) - lot.get('fees', 0.0)) / lot['risk_usd'], 3)); del h[:-200]

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
        """Market-close first; the protective stop is only cancelled after the close succeeded (a failed close keeps it)."""
        lot = self.state['lots'][key]
        qty = lot['qty']
        others = [k for k, l in self.state['lots'].items() if k != key and l['symbol'] == lot['symbol'] and l['side'] == lot['side']]
        if not others and not self.dry:            # last lot on this side: close exactly what the exchange holds (no dust)
            try: qty = self.trade.positions().get((lot['symbol'], lot['side']), qty) or qty
            except Exception: pass
        self._market_close(lot, qty, why, mark, post={'finish': why})    # raises on failure -> lot and its stop stay as they were
        self._finish(key, why)
        self.save_state()

    def _finish(self, key, why):
        """Lot fully closed: write a trade-history record and forget the lot."""
        lot = self.state['lots'].pop(key, None)
        if not lot: return
        if why != 'stop': self._cancel_or_park(lot['symbol'], lot.get('stop_id'))   # no stale stop may survive its lot
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
                   entry=lot.get('entry0', lot['e0']), avg_entry=lot['avg'], exit=exit_avg, qty_max=lot.get('qty_max', lot['q0']),
                   notional=round(lot.get('qty_max', lot['q0']) * lot['e0'], 2), risk_usd=lot.get('risk_usd'),
                   pnl_gross=round(gross, 4), fees=round(fees, 4), pnl=round(net, 4),
                   r=round(net / lot['risk_usd'], 2) if lot.get('risk_usd') else None,
                   roi_capital=round(net / lot['eq_at_entry'] * 100, 3) if lot.get('eq_at_entry') else None,
                   move_pct=round(sd * (exit_avg - lot.get('entry0', lot['e0'])) / lot.get('entry0', lot['e0']) * 100, 2), exit_reason=why,
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

    def _add_qty(self, lot, q, px, why, post=None):
        r = self.rules[lot['symbol']]
        q = self._rd(q, r['step'])
        if q < r['min_qty'] or q * px < r['min_notional']: return False
        if not self.dry:
            lot['last_order_t'] = time.time()
            try:
                o = self.trade.open(lot['symbol'], lot['side'], self._fmt(q, r['step']))
            except AmbiguousOrder as e:
                lot['pending'] = dict(kind='add', qty=q, px=px, why=why, post=post or {}, t=time.time())
                self.save_state(); self.err(f"{lot['symbol']} {why} unconfirmed ({e}) - waiting for Binance position to confirm"); raise
            px = float(o.get('avgPrice') or 0) or px
        self._apply_add(lot, q, px, why)
        return True

    def _apply_add(self, lot, q, px, why):
        r = self.rules[lot['symbol']]
        lot['avg'] = (lot['avg'] * lot['qty'] + px * q) / (lot['qty'] + q)
        lot['qty'] = self._rd(lot['qty'] + q, r['step'])
        lot['qty_max'] = max(lot.get('qty_max', 0), lot['qty'])
        lot.setdefault('fills', []).append([now_utc().isoformat(timespec='seconds'), why, q, px])
        lot['fees'] = lot.get('fees', 0.0) + q * px * FEE_EST
        self.log_trade(time=now_utc().isoformat(timespec='seconds'), event=why, sleeve=lot['sleeve'], symbol=lot['symbol'],
                       side=lot['side'], qty=q, price=px, stop=lot['stop'], equity=round(self.last_eq or 0, 2))
        log.info(f"{why.upper()} {lot['symbol']} {lot['side']} [{lot['sleeve']}] +{q} @ {px} (avg {lot['avg']:.6g})")

    # ------------------------------------------------------------ fast loop: soft management on mark price
    def manage(self, marks):
        with self.lock:
            self.marks, self.marks_t = dict(marks), time.time()
            changed = False
            if self.state.get('orphans') and not self.dry:          # stale stops whose cancel failed earlier
                keep = []
                for sym, tag in self.state['orphans']:
                    try: self.trade.cancel(sym, tag)
                    except Exception: keep.append([sym, tag])
                changed = len(keep) != len(self.state['orphans']); self.state['orphans'] = keep
            try:                                   # drop lots whose exchange stop already filled before touching anything
                n0 = len(self.state['lots'])
                self.reconcile(self.last_eq or 0)
                changed = changed or len(self.state['lots']) != n0
            except Exception as e:
                self._manage_failed(f'reconcile: {e}'); return
            ok = True
            for key in list(self.state['lots']):
                lot = self.state['lots'].get(key)
                if not lot: continue
                m = marks.get(lot['symbol'])
                if not m: continue
                sd = 1 if lot['side'] == 'LONG' else -1
                g = lot['mgmt']
                tick = self.rules[lot['symbol']]['tick'] if lot['symbol'] in self.rules else 1e-8
                ge = lambda lvl: sd * (m - lvl) >= 0           # price at/through a favourable level
                if lot.get('pending'): continue                # waiting for the exchange to confirm an unanswered order
                try:
                    if lot.get('force_close'):                 # a stop update found price already through the stop
                        self.close_lot(key, 'stop_crossed', m); changed = True; continue
                    if lot.get('stop_dirty') or not lot.get('stop_id'):   # protection missing/outdated -> retry every pass
                        if self._replace_stop(lot): changed = True; log.info(f"{lot['symbol']} [{lot['sleeve']}] stop restored")
                    if 'dca' in g and lot.get('levels'):
                        while lot['dca'] < len(lot['levels']) and sd * (lot['levels'][lot['dca']] - m) >= 0:
                            q = lot['q0'] * lot['w'][lot['dca']]
                            if not self._within_cap(lot, q * m) or not self._add_qty(lot, q, m, 'safety_order', post={'dca': lot['dca'] + 1}): break
                            lot['dca'] += 1
                            lot['tp'] = lot['avg'] + sd * g['dca']['tp_atr'] * lot['atr0']
                            self._replace_stop(lot); changed = True
                        if lot.get('tp') is not None and ge(lot['tp']):
                            run = g.get('runner')
                            if run and run.get('dca_frac', 1) < 1:      # runner: bank part, keep the rest at breakeven
                                self._market_close(lot, lot['qty'] * run['dca_frac'], 'basket_tp_part', m,
                                                   post={'tp': None, 'tp1': True, 'dca': len(lot['levels']), 'e0': lot['avg']})
                                if lot['qty'] <= 0: self._finish(key, 'basket_tp'); changed = True; continue
                                lot['tp'] = None; lot['tp1'] = True; lot['dca'] = len(lot['levels'])
                                lot['e0'] = lot['avg']; changed = True
                                be = lot['avg'] * (1 + sd * BE_BUF)
                                if sd * (be - lot['stop']) > 0 and sd * (m - be) > 0: self._replace_stop(lot, be)
                                else: self._replace_stop(lot)
                                lot['R'] = max(lot['R'], abs(lot['avg'] - lot['stop']))
                            else:
                                self.close_lot(key, 'basket_tp', m); changed = True; continue
                    if 'pyramid' in g and lot['adds'] < g['pyramid']['n'] and ge(lot['next_add']):
                        if self._within_cap(lot, lot['q0'] * g['pyramid']['frac'] * m):
                            if self._add_qty(lot, lot['q0'] * g['pyramid']['frac'], m, 'pyramid_add',
                                             post={'adds': lot['adds'] + 1, 'next_add': lot['next_add'] + sd * g['pyramid']['step_r'] * lot['R']}):
                                lot['adds'] += 1; lot['next_add'] += sd * g['pyramid']['step_r'] * lot['R']
                                self._replace_stop(lot); changed = True
                    if g.get('tp1_r') and not lot['tp1'] and ge(lot['e0'] + sd * g['tp1_r'] * lot['R']):
                        self._market_close(lot, lot['qty'] * g.get('tp1_frac', 0.5), 'take_profit_1', m, post={'tp1': True})
                        lot['tp1'] = True; changed = True
                        if lot['qty'] <= 0: self._finish(key, 'take_profit_1'); continue
                        self._replace_stop(lot)
                    if g.get('tp_r') and not g.get('runner') and ge(lot['e0'] + sd * g['tp_r'] * lot['R']):
                        self.close_lot(key, 'take_profit', m); changed = True; continue
                    lot['best'] = max(lot['best'], m) if sd == 1 else min(lot['best'], m)
                    # every stop tightening goes through one place: the best candidate that is still on the losing side of price
                    cands = []
                    if g.get('be_r') and sd * (lot['best'] - (lot['e0'] + sd * g['be_r'] * lot['R'])) >= 0:
                        cands.append(lot['avg'])
                    if g.get('trail_atr'):
                        cands.append(lot['best'] - sd * g['trail_atr'] * lot['atr_now'])
                    run = g.get('runner')
                    if run and lot['R'] > 0:                       # ratchet: breakeven, then lock profit behind the best R reached
                        bestR = sd * (lot['best'] - lot['e0']) / lot['R']
                        if bestR >= run.get('be_r', 2.0): cands.append(lot['avg'] * (1 + sd * BE_BUF))
                        lock = math.floor(bestR / run.get('step_r', 99)) * run.get('step_r', 99) - run.get('gap_r', 99)
                        if run.get('giveback') and bestR >= run.get('gb_from', 4.0): lock = max(lock, bestR * (1 - run['giveback']))
                        if lock > 0: cands.append(lot['e0'] + sd * lock * lot['R'])
                    if cands:
                        tgt = max(cands) if sd == 1 else min(cands)
                        min_step = (0.1 if g.get('trail_atr') and not run else 0.05) * lot.get('atr_now', lot['R'])
                        if sd * (tgt - lot['stop']) > max(min_step, tick):
                            if sd * (m - tgt) <= 0:                # price already back through the level the stop should be at
                                log.info(f"{lot['symbol']} [{lot['sleeve']}] price {m} already through protective level {tgt:.6g} - closing")
                                self.close_lot(key, 'stop_crossed', m); changed = True; continue
                            if self._replace_stop(lot, tgt):
                                changed = True
                                if run: log.info(f"RUNNER {lot['symbol']} [{lot['sleeve']}] stop raised to {lot['stop']}")
                except Exception as e:
                    ok = False; self.err(f'manage {key}: {e}')
            if ok:
                self.health['last_manage_ok'] = now_utc().isoformat(timespec='seconds'); self.health['manage_fail_streak'] = 0
                self.health['alerted'] = False
            else:
                self._manage_failed('see errors')
            if changed: self.save_state()

    def _manage_failed(self, why):
        h = self.health; h['manage_fail_streak'] += 1
        if why != 'see errors': self.err(f'manage: {why}')
        if h['manage_fail_streak'] >= 6 and not h['alerted']:
            h['alerted'] = True
            self.notify(f'🆘 Trade management is failing ({why}). Exchange stops are still in place - check the app.')

    def _within_cap(self, lot, add_notional):
        sl = next((x for x in self.S['SLEEVES'] if x['id'] == lot['sleeve']), None)
        if not sl or lot.get('manual'): return True
        sleeve_eq = (self.last_eq or 0) * sl['share']
        used = sum(l['qty'] * l['avg'] for l in self.state['lots'].values() if l['sleeve'] == lot['sleeve'])
        return used + add_notional <= self.S['MAX_LEVERAGE'] * sleeve_eq

    # ------------------------------------------------------------ reconcile with exchange
    def reconcile(self, eq, live=None):
        """Match engine lots to what Binance actually holds.
        - less on the exchange: the lots whose stop order is no longer open were stopped out (identified by stop-order id);
          if that cannot be determined this round, nothing is guessed - it is retried on the next pass;
          if every stop is still open, the lots are resized to what the exchange holds.
        - more on the exchange (or a position with no lot at all): reported as UNTRACKED and alerted, never ignored."""
        live = self.trade.positions() if live is None else live
        st = self.state
        groups = {}
        for k, l in st['lots'].items(): groups.setdefault((l['symbol'], l['side']), []).append(k)
        untracked = {}
        def dust(sym, q):        # below Binance's minimum order: cannot be traded or closed normally, not worth an alarm
            r = self.rules.get(sym); px = (self.marks or {}).get(sym)
            return r is not None and (q < r['min_qty'] or (px and q * px < r['min_notional']))
        for (sym, side), have in live.items():
            if (sym, side) not in groups and have > 0:
                tol = self.rules[sym]['step'] if sym in self.rules else 1e-9
                if have > tol and not dust(sym, have): untracked[(sym, side)] = have
        short_seen = st.setdefault('short_seen', {}); seen_now = set()
        for (sym, side), keys in groups.items():
            expected = sum(st['lots'][k]['qty'] for k in keys)
            have = live.get((sym, side), 0.0)
            tol = self.rules[sym]['step'] * (len(keys) + 1) if sym in self.rules else 1e-9
            pend = [k for k in keys if st['lots'][k].get('pending')]
            if pend:                                                    # an unanswered order on this coin/side: settle it first
                res = self._resolve_pending(pend[0], have, expected, tol)
                if res is not False: continue                           # resolved (re-checked next pass) or still waiting
                keys = [k for k in keys if k in st['lots']]; expected = sum(st['lots'][k]['qty'] for k in keys)
            gk = f'{sym}|{side}'
            if have > expected + tol:
                if not dust(sym, have - expected): untracked[(sym, side)] = have - expected
                continue
            if have >= expected - tol: continue
            sd = 1 if side == 'LONG' else -1
            try:
                open_tags = self.trade.open_stop_tags(sym)
            except Exception as ex:
                log.warning(f'open orders {sym}: {ex} - reconcile retried next pass'); continue
            gone = [k for k in keys if st['lots'][k].get('stop_id') and st['lots'][k]['stop_id'] not in open_tags]
            gone.sort(key=lambda k: -sd * st['lots'][k]['stop'])       # highest long stop triggers first
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
            rest = [k for k in keys if k in st['lots']]
            if rest and have < expected - tol:
                # every stop is still open but the position is smaller: only act after it is seen twice in a row and no order
                # was sent on this coin in the last 30 s (Binance's position report can lag right after an order)
                recent = any(time.time() - st['lots'][k].get('last_order_t', 0) < 30 for k in rest)
                short_seen[gk] = short_seen.get(gk, 0) + 1; seen_now.add(gk)
                if recent or short_seen[gk] < 2: continue
                scale = have / expected if expected > 0 else 0
                mk = (self.marks or {}).get(sym)
                for k in rest:
                    l = st['lots'][k]
                    newq = self._rd(l['qty'] * scale, self.rules[sym]['step'])
                    gone_q = l['qty'] - newq
                    if gone_q > 0: self._apply_close(l, gone_q, mk or l['stop'], 'resync')    # book what left at the market price
                    if l['qty'] <= 0: self._finish(k, 'resync')
                    else: self._replace_stop(l)
                self.err(f'{sym} {side}: exchange holds less than expected ({have} vs {expected:.6g}) - lots resized to match')
        for k in list(short_seen):
            if k not in seen_now: short_seen.pop(k, None)
        over = st.setdefault('over_seen', {})
        cand = {f'{s_}|{d}': q for (s_, d), q in untracked.items()}
        for k in list(over):
            if k not in cand: over.pop(k, None)
        for k in cand: over[k] = over.get(k, 0) + 1
        new = {k: q for k, q in cand.items() if over[k] >= 2 or k in self.untracked}     # seen twice before alerting
        for k, q in new.items():
            if k not in self.untracked:
                self.err(f'UNTRACKED position {k} qty {q} on Binance - not managed by the bot and has no bot stop')
                self.notify(f'⚠️ Untracked position on Binance: {k} qty {q}. It has no bot stop - check it.')
        self.untracked = new

    # ------------------------------------------------------------ safety limits (run every cycle and every few minutes)
    def check_guards(self):
        """Daily loss halt (resets at midnight Cairo) and peak-drawdown flatten, both on bot capital incl. open P&L."""
        st, Sg, g = self.state, self.S, self.guard_eq
        if not g: return
        today = trading_day()
        if st.get('day') != today:
            if st.get('day') and st.get('day_start_equity'): self.daily_summary(st['day'], st['day_start_equity'], g)
            st.update(day=today, day_start_equity=g, halted=False)
        if not st.get('day_start_equity'): st['day_start_equity'] = g
        st['peak_equity'] = max(st.get('peak_equity') or g, g)
        if g / st['day_start_equity'] - 1 <= -Sg['DAILY_LOSS_HALT'] and not st['halted']:
            st['halted'] = True; log.warning(f'DAILY LOSS HALT at bot capital {g:.2f}')
            self.notify(f'⚠️ Daily loss halt: bot capital {g:.2f} (day start {st["day_start_equity"]:.2f}). No new trades until midnight Cairo.')
        if Sg['PEAK_DD_FLATTEN'] > 0 and g <= st['peak_equity'] * (1 - Sg['PEAK_DD_FLATTEN']):
            log.warning('PEAK DRAWDOWN LIMIT - closing all bot trades and pausing')
            res = self.flatten(manual_too=False)
            st['peak_equity'] = g                    # new baseline: entries stay paused until you resume them
            self.notify(f"🧯 Drawdown limit hit - bot trades closed ({len(res['closed'])} ok, {len(res['failed'])} failed) and entries paused.")
        self.save_state()

    def daily_summary(self, day, start, end):
        H = [h for h in self.history if h.get('closed', '')[:10] >= day]
        try:
            n = len(H); w = sum(1 for h in H if h['pnl'] > 0)
            self.notify(f"📊 Day {day}: bot capital {start:.2f} -> {end:.2f} ({(end / start - 1) * 100:+.2f}%) · {n} closed trades, {w} won · "
                        f"{len(self.state['lots'])} open")
        except Exception: pass

    # ------------------------------------------------------------ candle-close cycle
    def cycle(self, tf, reason='candle close'):
        with self.lock:
            st, Sg = self.state, self.S
            eq = self.equity()                       # sizing equity
            self.check_guards()
            self.reconcile(eq)
            sleeves = [sl for sl in Sg['SLEEVES'] if sl['tf'] == tf]
            syms = sorted({s for sl in sleeves for s in self.sleeve_symbols(sl, include_off=True)} | {l['symbol'] for l in st['lots'].values() if l.get('tf') == tf})
            if not syms: return
            log.info(f'--- {tf} cycle ({reason}) | equity {eq:.2f} ---')
            sigs, frames = self.compute_signals(tf, syms, extra=self._orphan_sleeves(tf))
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
                    try: self.close_lot(k, 'exit_signal' if ex else 'time_exit', sg['close'] if sg else None)
                    except Exception as e: self.err(f'exit {l["symbol"]} [{l["sleeve"]}] failed: {e} - stop stays in place, retried next cycle')
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
                        try:
                            if self.open_lot(sl, s, side, sg, frames.get(s), eq):
                                held.append(s); continue
                        except Exception as e:                     # one failed order never stops the other entries
                            self.err(f'entry {s} [{sl["id"]}] failed: {e}'); self.last_skip = f'order failed: {e}'
                        why = self.last_skip or 'order failed'
                    self.miss(sl, s, side, sg, why)
            st['last_cycle'][tf] = now_utc().isoformat(timespec='minutes')
            self.health['last_cycle_ok'][tf] = now_utc().isoformat(timespec='seconds')
            self.save_state()
            self.record_equity(self.guard_eq or eq)

    def entry_block(self, sl, sym, side, manual=False):
        """One gate for every way a trade can be opened (automatic, 'Take now', manual). Returns a reason or None."""
        st, Sg = self.state, self.S
        if not self.connected or self.error: return 'not connected to Binance'
        if not SYM_RE.match(sym or '') or sym not in self.rules: return f'{sym} is not tradable'
        if side not in ('LONG', 'SHORT'): return 'bad direction'
        if side == 'SHORT' and not self.hedge: return 'hedge mode off (shorts unavailable)'
        if st.get('halted'): return 'daily loss halt is active'
        if any(l['symbol'] == sym and l['side'] == side and l.get('stop_dirty') for l in st['lots'].values()):
            return 'an open trade on this coin is waiting for its stop to be confirmed'
        if f'{sym}|{side}' in self.untracked: return 'Binance holds an untracked position on this coin/side - resolve it first'
        if manual: return None
        if Sg.get('ENTRIES_PAUSED'): return 'entries paused'
        if not Sg['SYMBOLS_ON'].get(sym, True): return 'coin switched off'
        if not sl.get('enabled', True): return 'strategy slot switched off'
        held = [l for l in st['lots'].values() if l['sleeve'] == sl['id']]
        if any(l['symbol'] == sym for l in held): return 'already in a trade on this coin'
        if len(held) >= sl['max_pos']: return f"max positions reached ({sl['max_pos']})"
        return None

    def _ensure_leverage(self, sym):
        """Exchange leverage = the configured cap (bounded by what Binance allows for the coin). Failure blocks the entry."""
        want = max(1, int(self.S['MAX_LEVERAGE']))
        if self._lev.get(sym) == want: return
        try: self.trade.set_margin_type(sym, 'CROSSED')        # cross is Binance's default; a refusal here is not a safety issue
        except Exception as e: log.info(f'{sym}: margin type not changed ({e})')
        mx = self.trade.leverage_max(sym)
        lev = min(want, mx) if mx else want
        try: self.trade.set_leverage(sym, lev)
        except Exception as e:                                  # transient testnet/API errors: one retry, then block the entry
            log.info(f'{sym}: leverage retry after {e}'); time.sleep(1); self.trade.set_leverage(sym, lev)
        self._lev[sym] = want

    def open_lot(self, sl, sym, side, sg, df, eq, risk=None, manual=False, stop_atr=None, tp_r=None):
        block = self.entry_block(sl, sym, side, manual)
        if block:
            self.last_skip = block
            if manual: raise ValueError(block)
            return False
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
        used = sum(l['qty'] * l['avg'] for l in self.state['lots'].values() if l['sleeve'] == ('MAN' if manual else sl['id']))
        qty = min(qty, max(0.0, self.S['MAX_LEVERAGE'] * sleeve_eq - used) / px)        # leverage cap applies to manual trades too
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
            self._ensure_leverage(sym)
        except Exception as e:
            self.last_skip = f'could not set leverage/margin on Binance: {e}'
            log.warning(f'{sym}: {self.last_skip}')
            if manual: raise ValueError(self.last_skip)
            return False
        try:
            o = self.trade.open(sym, side, self._fmt(qty, r['step']))
        except AmbiguousOrder as e:
            self.err(f'ENTRY {sym} {side} unconfirmed ({e}) - reconcile will flag it if it filled')
            self.last_skip = 'entry order unconfirmed'
            return False
        fill = float(o.get('avgPrice') or 0) or px
        filled = float(o.get('executedQty') or 0)
        if filled > 0: qty = self._rd(filled, r['step'])
        stop = self._rd(fill - sd * (abs(px - stop)), r['tick'])
        key = f"{'MAN' if manual else sl['id']}|{sym}|{side}|{int(time.time())}"
        lot = dict(symbol=sym, side=side, sleeve='MAN' if manual else sl['id'], key_strategy=None if manual else sl['key'],
                   qty=qty, q0=qty, avg=fill, e0=fill, entry0=fill, R=abs(fill - stop), stop=stop, stop_id=None, stop_dirty=True, tp1=False, adds=0, dca=0,
                   best=fill, atr0=atr, atr_now=atr, mgmt=g, tf=(sl['tf'] if sl else '4h'), manual=manual,
                   opened=now_utc().isoformat(timespec='seconds'), risk_usd=round(risk_usd, 2), eq_at_entry=round(eq, 2),
                   qty_max=qty, fills=[[now_utc().isoformat(timespec='seconds'), 'entry', qty, fill]], fees=qty * fill * FEE_EST)
        if 'pyramid' in g: lot['next_add'] = fill + sd * g['pyramid']['step_r'] * lot['R']
        if 'dca' in g:
            lot['levels'] = [fill - sd * k * g['dca']['step_atr'] * atr for k in range(1, g['dca']['n'] + 1)]
            lot['w'] = [g['dca']['scale'] ** k for k in range(1, g['dca']['n'] + 1)]
            lot['tp'] = fill + sd * g['dca']['tp_atr'] * atr
        self.state['lots'][key] = lot; self.save_state()          # recorded BEFORE the stop: a crash here can never orphan the position
        if not self._replace_stop(lot):
            log.error(f'STOP FAILED {sym} - closing for safety')
            try:
                self.close_lot(key, 'stop_failed', px)
                self.last_skip = 'stop order failed - trade closed again'; return False
            except Exception as e:
                self.err(f'{sym}: stop AND safety close failed ({e}) - kept as unprotected, retrying every few seconds')
                self.notify(f'🆘 {side} {sym} has NO stop on Binance (stop and close both failed). The bot keeps retrying - check it.')
                return True
        self.log_trade(time=now_utc().isoformat(timespec='seconds'), event='entry', sleeve=lot['sleeve'], symbol=sym, side=side,
                       qty=qty, price=fill, stop=stop, equity=round(eq, 2), note=reason)
        log.info(f"ENTRY {sym} {side} [{lot['sleeve']}] {qty} @ {fill} stop {stop}")
        self.notify(f"🚀 OPEN {side} {sym} [{lot['sleeve']}] {qty} @ {fill} · stop {stop} · risk {risk_usd:.2f} USDT")
        self.save_state()
        return True

    # ------------------------------------------------------------ panel actions
    def manual_trade(self, sym, side, risk_pct, stop_atr, tp_r=None):
        risk_pct, stop_atr = float(risk_pct), float(stop_atr)
        if not 0 < risk_pct <= MANUAL_MAX_RISK: raise ValueError(f'manual risk must be between 0 and {MANUAL_MAX_RISK * 100:.0f}% of bot capital')
        if not 0.5 <= stop_atr <= 10: raise ValueError('stop distance must be 0.5-10 ATR')
        if tp_r is not None and not 0.5 <= float(tp_r) <= 50: raise ValueError('take profit must be 0.5-50 R')
        if not SYM_RE.match(sym or ''): raise ValueError('bad symbol')
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
            side = 'LONG' if sg['le'] else 'SHORT'
            block = self.entry_block(sl, sym, side)
            if block: raise ValueError(block)
            eq = self.equity()
            if not self.open_lot(sl, sym, side, sg, self.candles(sym, sl['tf']), eq): raise ValueError(self.last_skip or 'not opened')
            return True

    def move_stop(self, key, price):
        with self.lock:
            lot = self.state['lots'][key]
            sd = 1 if lot['side'] == 'LONG' else -1
            mark = self.trade.marks().get(lot['symbol'])
            if sd * (price - mark) >= 0: raise ValueError(f'stop must be on the losing side of the current price {mark}')
            if not self._replace_stop(lot, price): raise ValueError('Binance did not accept the new stop - the previous stop is still active')
            self.save_state()
            log.info(f"STOP MOVED {lot['symbol']} [{lot['sleeve']}] -> {lot['stop']}")

    def flatten(self, manual_too=True):
        """Close every (bot) position. Returns {'closed': [...], 'failed': [[key, error]], 'still_open': {...}}.
        Failed closes keep their exchange stop. Entries are paused either way."""
        with self.lock:
            res = dict(closed=[], failed=[], still_open={})
            for k in list(self.state['lots']):
                if manual_too or not self.state['lots'][k].get('manual'):
                    try: self.close_lot(k, 'flatten'); res['closed'].append(k)
                    except Exception as e: res['failed'].append([k, str(e)[:160]]); self.err(f'flatten {k}: {e}')
            self.S['ENTRIES_PAUSED'] = True; self.save_settings()
            if not self.dry:
                try:
                    live = self.trade.positions()
                    syms = {k.split('|')[1] for k in res['closed'] + [f[0] for f in res['failed']]}
                    res['still_open'] = {f'{a}|{b}': q for (a, b), q in live.items() if a in syms}
                except Exception as e:
                    res['still_open'] = {'?': f'could not verify: {e}'}
            return res
