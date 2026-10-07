"""ZackBot live engine — runs any mix of strategies ("sleeves") from strategies.py on Binance USD-M futures.

Hard stops live on Binance (STOP_MARKET per lot). Softer management (partial take-profit, breakeven,
trailing, pyramiding adds, DCA safety orders, basket take-profit) is checked every few seconds on the mark price.
Signals (entries/exits) are evaluated right after each candle close of the sleeve's timeframe.
Hedge mode is used so longs and shorts on the same coin can coexist.
"""
import atexit, contextlib, csv, functools, inspect, json, math, os, queue, re, time, zlib, logging, threading, copy, collections
from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd

import strategies as S
import trade_audit as TA
import feasibility as F
from binance_client import Futures, MAINNET, TESTNET, BinanceError, AmbiguousOrder, ExchangeUnavailable, is_transient, new_cid, scrub, testnet_faults, testnet_read_outage
from ai_filter import review
import grid as GRID

log = logging.getLogger('zackbot')
from time import monotonic as _mono, sleep as _sleep   # T05 writer: real clock even when replay swaps E.time
FILL_QUEUE_MAX = 1000             # T05: telemetry records waiting for the writer; beyond this they are dropped (counted)
FILL_WINDOW = 5000                # T05: the summary covers the last 5000 records written (rebuilt from disk at start)
FILL_ROTATE_BYTES = 5_000_000     # T05: fills.jsonl -> fills.jsonl.1 above 5 MB (~16k records, so .1 always holds the window)
AUDIT_ROTATE_LINES = 20_000       # T05a: trade_audit.jsonl -> .1 every 20k event lines (bounded files; restart reads .1 + current)
AUDIT_ROTATE_BYTES = 16_000_000   # T05a: ... or at 16 MB, whichever comes first (checkpoint lines can be several KB each)


_NUM_FIELDS = ('expected', 'actual', 'slip_bps', 'qty_req', 'qty_fill', 'wait_s')


def _fill_normalize(x):
    """T05: a record read back from fills.jsonl, made safe for the summary - or None if it is not a usable record.
    Older or hand-edited lines never break /api/status: non-dicts are rejected, wrong field types become null."""
    if not isinstance(x, dict) or not isinstance(x.get('kind'), str) or not x['kind']: return None
    r = dict(x)
    for k in _NUM_FIELDS:
        v = r.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float('inf'), float('-inf'))):
            r[k] = None
    if not isinstance(r.get('outcome'), str): r['outcome'] = 'unknown'
    return r


class FillWriter:
    """T05: the ONE writer of a telemetry file in this process (Codex T05 round 2). Engines replaced by a settings
    restart share it, so two writers can never touch the same file. Lazy: no thread until the first record. close()
    stops accepting, flushes (bounded), signals and joins; a writer that cannot be proven stopped stays registered
    (state 'closing', records dropped and counted) so a second writer is never started next to it."""

    def __init__(self, path, rotate_lines=None, slim=None, rotate_bytes=None):
        self.path = path
        self.rotate_lines = rotate_lines          # T05a: rotate by record count (the window always fits in .1 + current)
        self.rotate_bytes = rotate_bytes          # T05a: ... and by size, whichever comes first (with rotate_lines only)
        self.nbytes = 0                           # bytes in the current file (size at load, then per write)
        self.slim = slim                          # T05a: keep only what the summary reads in the in-memory window
        self.lines = 0                            # lines in the current file (counted at load, then per write)
        self.q = queue.Queue(maxsize=FILL_QUEUE_MAX)
        self.lock = threading.Lock()
        self.win = collections.deque(maxlen=FILL_WINDOW)
        self.ctr = dict(accepted=0, persisted=0, dropped=0, write_errors=0, invalid_records=0)
        self.closing, self.stop_ev, self.thread = False, threading.Event(), None
        self._load()

    def _load(self):
        """The window from fills.jsonl.1 + fills.jsonl (the last FILL_WINDOW valid records on disk). Never raises."""
        for f in (self.path + '.1', self.path):
            try:
                if not os.path.exists(f): continue
                if f == self.path: self.nbytes = os.path.getsize(f)
                with open(f, encoding='utf-8', errors='replace') as fh:
                    for ln in fh:
                        if f == self.path: self.lines += 1
                        try: r = _fill_normalize(json.loads(ln))
                        except Exception: r = None
                        if r is None: self.ctr['invalid_records'] += 1
                        else:
                            w = self._slim(r)
                            if w is not None: self.win.append(w)   # T05a: slim None = not part of the summary window
            except Exception as e:
                log.warning(f'fill telemetry history not loaded from {f} ({e})')

    def emit(self, rec):
        """Admission is ONE atomic step under the lock (Codex T05 round 3): the closing check, the non-blocking put and the
        accepted/dropped count. close() cannot slip in between, so a record is either queued before close() flushes it,
        or rejected and counted - never accepted into a stopped writer's queue."""
        with self.lock:
            if self.closing:
                self.ctr['dropped'] += 1; return False
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name='fill-telemetry', daemon=True)
                self.thread.start()
            try:
                self.q.put_nowait(rec)                                   # never blocks (a full queue raises at once)
            except Exception:
                self.ctr['dropped'] += 1; return False
            self.ctr['accepted'] += 1
            return True

    def _slim(self, rec):
        if self.slim is None: return rec
        try: return self.slim(rec)
        except Exception: return rec

    def write(self, rec):
        ln = json.dumps(rec) + '\n'
        if self.rotate_lines:
            full = self.lines >= self.rotate_lines or (self.rotate_bytes and self.lines and self.nbytes + len(ln) > self.rotate_bytes)
            if full and os.path.exists(self.path): os.replace(self.path, self.path + '.1'); self.lines = self.nbytes = 0
        elif os.path.exists(self.path) and os.path.getsize(self.path) > FILL_ROTATE_BYTES: os.replace(self.path, self.path + '.1')
        with open(self.path, 'a') as f: f.write(ln)
        self.lines += 1; self.nbytes += len(ln)                       # json.dumps is ASCII-only: chars == bytes

    def _run(self):
        while True:
            try: rec = self.q.get(timeout=0.2)
            except queue.Empty:
                if self.stop_ev.is_set(): return
                continue
            try:
                self.write(rec)
                w = self._slim(rec)
                with self.lock:
                    if w is not None: self.win.append(w)
                    self.ctr['persisted'] += 1
            except Exception as e:
                with self.lock: self.ctr['write_errors'] += 1
                log.warning(f'fill telemetry not written ({e})')
            finally:
                self.q.task_done()

    def flush(self, timeout=2.0):
        end = _mono() + timeout
        while self.q.unfinished_tasks and _mono() < end: _sleep(0.01)
        return not self.q.unfinished_tasks

    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    def close(self, timeout=2.0):
        """Stop accepting, flush what is queued (bounded), stop and join. True only if no writer thread remains."""
        end = _mono() + timeout
        with self.lock: self.closing = True
        self.flush(max(0.0, end - _mono()))
        self.stop_ev.set()
        if self.thread is not None: self.thread.join(max(0.0, end - _mono()) + 0.3)
        return not self.alive()

    def summary(self):
        with self.lock: win, ctr = list(self.win), dict(self.ctr)
        out = {}
        for rec in win:
            try:
                s = out.setdefault(str(rec.get('kind', '?')), dict(n=0, partial=0, unfilled=0, unknown=0, fallback=0, _s=[], _w=[]))
                s['n'] += 1; o = rec.get('outcome')
                s['partial'] += o == 'partial'; s['unfilled'] += o == 'unfilled'; s['unknown'] += o == 'unknown'
                s['fallback'] += rec.get('fallback_confirmed') is True
                if isinstance(rec.get('slip_bps'), (int, float)): s['_s'].append(rec['slip_bps'])
                if isinstance(rec.get('wait_s'), (int, float)): s['_w'].append(rec['wait_s'])
            except Exception:
                ctr['invalid_records'] += 1
        for s in out.values():
            sl, w = s.pop('_s'), s.pop('_w')
            s.update(slip_avg_bps=round(sum(sl) / len(sl), 2) if sl else None, slip_worst_bps=max(sl) if sl else None,
                     wait_avg_s=round(sum(w) / len(w), 2) if w else None)
        ctr.update(queued=self.q.qsize(), window=FILL_WINDOW, in_window=len(win),
                   state='closing' if self.closing else ('running' if self.alive() else 'idle'))
        return dict(by_kind=out, recent=[dict(r) for r in win[-10:]], telemetry=ctr)


_FILL_WRITERS, _FILL_WRITERS_LOCK = {}, threading.Lock()


def fill_writer(path, rotate_lines=None, slim=None, rotate_bytes=None):
    """The process-wide writer for this telemetry file. A new one is made only when none exists or the old one has
    provably stopped - never next to a writer that is still alive. rotate_lines (set on creation): rotate by record
    count instead of FILL_ROTATE_BYTES; slim (set on creation): applied to records kept in the in-memory window."""
    path = os.path.abspath(path)
    with _FILL_WRITERS_LOCK:
        w = _FILL_WRITERS.get(path)
        if w is None or (w.closing and not w.alive()):
            w = _FILL_WRITERS[path] = FillWriter(path, rotate_lines, slim, rotate_bytes)
        return w


def close_fill_writer(path, timeout=2.0):
    """Close one file's writer (replay/test cleanup). Returns True when no writer thread remains for it."""
    path = os.path.abspath(path)
    with _FILL_WRITERS_LOCK: w = _FILL_WRITERS.get(path)
    if w is None: return True
    ok = w.close(timeout)
    if ok:
        with _FILL_WRITERS_LOCK:
            if _FILL_WRITERS.get(path) is w: del _FILL_WRITERS[path]
    return ok


def close_fill_writers(timeout=2.0):
    """Close every writer (process exit, tests). Returns the paths whose writer could not be proven stopped."""
    with _FILL_WRITERS_LOCK: paths = list(_FILL_WRITERS)
    return [p for p in paths if not close_fill_writer(p, timeout)]


atexit.register(close_fill_writers, 2.0)

LEV_REFUSAL_COOLDOWN_S = 1800     # T03c: after Binance refuses a leverage change, no new change request for this coin for 30 min
LEV_MARGIN_RATIO_MAX = 0.5        # T03c: worst-case account margin ratio (every stop filled) allowed for an above-cap entry
LEV_STOP_SLIP = 1.5               # T03c: every stop loss in that worst case (open lots, reserved orders, the new entry) is 1.5x worse
LEGACY_LEV_EXC_MAKER = ('maker entry saved by older code through the above-cap leverage exception: not re-priced '
                       'on that old approval (no market fallback)')                    # T03c r2 follow-up
LEV_BRACKET_TTL_S = 3600          # T03c r1: a coin's leverage/maintenance bracket schedule is static venue data: cached 1 h
LEV_EXC_RECHECK_S = 60            # T03c r1: while adds are paused by the above-cap exception, re-read the coin's leverage at most once a minute


class LevReject(Exception):
    """T03c r1. The above-cap exception cannot be proven. .check names the failed check (shown in the panel)."""
    def __init__(self, check, why, numbers=None):
        super().__init__(why); self.check, self.numbers = check, numbers or {}


def lev_num(x, name, pos=False, lo=0.0):
    """A finite number >= lo (> 0 with pos). Anything else (None, text, bool, NaN, inf, out of range) raises LevReject."""
    if isinstance(x, bool): raise LevReject('numeric', f'{name} is not a number ({x!r})')
    try: v = float(x)
    except (TypeError, ValueError): raise LevReject('numeric', f'{name} is not a number ({x!r})')
    if not math.isfinite(v) or v < lo or (pos and v <= 0): raise LevReject('numeric', f'{name} is invalid ({x!r})')
    return v


def check_brackets(b):
    """T03c r1. Validate a leverage-bracket schedule (Binance /fapi/v1/leverageBracket, as Futures.leverage_brackets returns
    it): contiguous [floor, cap) tiers from 0, maintenance rate in (0, 1) and non-decreasing, initial leverage >= 1 and
    non-increasing, cum >= 0 and continuous (cum_i = cum_i-1 + floor_i * (mmr_i - mmr_i-1)). Raises ValueError if not."""
    if not isinstance(b, (list, tuple)) or not b: raise ValueError('empty bracket schedule')
    prev = None
    for i, x in enumerate(b):
        try: f, c, m, cum, lv = (float(x[k]) for k in ('floor', 'cap', 'mmr', 'cum', 'lev'))
        except (KeyError, TypeError, ValueError): raise ValueError(f'bracket {i} malformed')
        if not all(math.isfinite(v) for v in (f, c, m, cum, lv)): raise ValueError(f'bracket {i} not finite')
        if not (0 < m < 1 and cum >= 0 and lv >= 1 and c > f >= 0): raise ValueError(f'bracket {i} out of range')
        if prev is None:
            if f != 0: raise ValueError('first bracket does not start at 0')
        else:
            pf, pc, pm, pcum, plv = prev
            if abs(f - pc) > 1e-9 * max(1.0, pc): raise ValueError(f'bracket {i} not contiguous')
            if m < pm or lv > plv: raise ValueError(f'bracket {i} not monotonic')
            if abs(cum - (pcum + f * (m - pm))) > max(1.0, 1e-6 * f): raise ValueError(f'bracket {i} cum not continuous')
        prev = (f, c, m, cum, lv)
    return b


def bracket_maint(b, notional):
    """T03c r1. Maintenance margin of an aggregate symbol notional: notional * mmr - cum of the bracket whose [floor, cap)
    holds it (tier crossing included). Raises ValueError for a malformed schedule or a notional beyond the last tier."""
    check_brackets(b)
    n = float(notional)
    if not math.isfinite(n) or n < 0: raise ValueError(f'invalid notional {notional!r}')
    for x in b:
        if float(x['floor']) <= n < float(x['cap']):
            return max(0.0, n * float(x['mmr']) - float(x['cum']))
    raise ValueError(f'notional {n:.0f} is beyond the last leverage bracket')


def _fin(v, d=4):
    """Rounded finite number or None (status JSON must never carry NaN/inf)."""
    try: v = float(v)
    except (TypeError, ValueError): return None
    return round(v, d) if math.isfinite(v) else None
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
    'balanced': dict(name='Balanced', note='2% per trade with pyramiding on the trend slots. Backtest 2024-10-20 to 2026-10-04: $500 -> $5,925, max DD -29%, worst month -7.3%, ~10 trades/week.', bt={'end': 5925, 'dd': -29, 'wm': -7.3, 'wk': 10, 'start': '2024-10-20', 'stop': '2026-10-04', 'engine': 'v3', 'insample': True},
                     sleeves=[sleeve('MOM', 'ema_mom', 1 / 3, .02, 8, mgmt=PY), sleeve('ST', 'ema_st', 1 / 3, .02, 4, 'core8', mgmt=PY),
                              sleeve('DCA', 'dca_dip', 1 / 3, .02, 6)]),
    'aggressive': dict(name='Aggressive', note='3% per trade, same mix as Balanced. Backtest 2024-10-20 to 2026-10-04: $500 -> $11,917, max DD -39%, worst month -10.8%, ~10 trades/week.', bt={'end': 11917, 'dd': -39, 'wm': -10.8, 'wk': 10, 'start': '2024-10-20', 'stop': '2026-10-04', 'engine': 'v3', 'insample': True},
                       sleeves=[sleeve('MOM', 'ema_mom', 1 / 3, .03, 8, mgmt=PY), sleeve('ST', 'ema_st', 1 / 3, .03, 4, 'core8', mgmt=PY),
                                sleeve('DCA', 'dca_dip', 1 / 3, .03, 6)]),
    'active': dict(name='Active (1h, more trades)', note='1h DCA dip + 1h Breakout/pyramiding on core 8 - many more trades. 6-month test: $500 -> $935, max DD -26%. WARNING: over 4 years of 1h data (2022-08 to 2026-10) it ended $3,947 but with a -50% max drawdown (Monte Carlo: 18% chance of falling below half the start), driven by the 1h breakout slot - prefer Active DCA (1h) or Steady mix.', bt={'end': 935, 'dd': -26, 'wm': -3.4, 'wk': 21, 'start': '2026-04-08', 'stop': '2026-10-04', 'engine': 'v3.1', 'insample': True},
                   sleeves=[sleeve('DCA1H', 'dca_dip', .5, .02, 4, 'core8', tf='1h'), sleeve('BRK1H', 'breakout_pyramid', .5, .02, 4, 'core8', tf='1h')]),
    'boost_active': dict(name='Boost + Active (4h + 1h mix)', note='Boost on 4h with half the capital, Active 1h with the other half (tested side by side). 6-month test 2026-04-08 to 2026-10-04: $500 -> $1,575, max DD -38%, worst month -11%, ~30 trades/week. 4-year test on core 8 (from 2022-09): $500 -> $8,112, max DD -33%; the 1h breakout slot is the weak part - see Steady mix.', bt={'end': 1575, 'dd': -38, 'wm': -11.4, 'wk': 30, 'start': '2026-04-08', 'stop': '2026-10-04', 'engine': 'v3.1', 'insample': True},
                         sleeves=[sleeve('MOM', 'ema_mom', .25, .05, 8, mgmt=PY), sleeve('DCA', 'dca_dip', .25, .05, 6),
                                  sleeve('DCA1H', 'dca_dip', .25, .02, 4, 'core8', tf='1h'), sleeve('BRK1H', 'breakout_pyramid', .25, .02, 4, 'core8', tf='1h')]),
    'steady_mix': dict(name='Steady mix (4h trend, bull-filtered + 1h DCA)', note='Half the capital: Balanced with the trend slots only trading while BTC is above its 200-day average; other half: 1h DCA dip on core 8 at 2%. Long test on core 8 coins 2022-09-04 to 2026-10-04 (incl. the FTX crash): $500 -> $4,015, max DD -15%, worst month -7.3%. Not yet tested on 40 coins for the 1h half.', bt={'end': 4015, 'dd': -15, 'wm': -7.3, 'wk': 15, 'start': '2022-09-04', 'stop': '2026-10-04', 'engine': 'v3.1', 'insample': True},
                       sleeves=[dict(sleeve('MOM', 'ema_mom', 1 / 6, .02, 8, mgmt=PY), when='bull'), dict(sleeve('ST', 'ema_st', 1 / 6, .02, 4, 'core8', mgmt=PY), when='bull'),
                                sleeve('DCA', 'dca_dip', 1 / 6, .02, 6), sleeve('DCA1H', 'dca_dip', .5, .02, 4, 'core8', tf='1h')]),
    'active_dca': dict(name='Active DCA (1h)', note='Only the 1h DCA dip slot of Active (the 1h breakout slot caused most of Active\'s losses over 4 years). Core 8, 2%. Long test 2022-09-03 to 2026-10-04: $500 -> $4,637, max DD -26%, worst month -13.6%, ~12 trades/week; positive every year incl. late 2022.', bt={'end': 4637, 'dd': -26, 'wm': -13.6, 'wk': 12, 'start': '2022-09-03', 'stop': '2026-10-04', 'engine': 'v3.1', 'insample': True},
                       sleeves=[sleeve('DCA1H', 'dca_dip', 1.0, .02, 4, 'core8', tf='1h')]),
    'boost': dict(name='Boost (short-term, high risk)', note='5% per trade. For short sprints only - deep drawdowns. Backtest 2024-10-20 to 2026-10-04: $500 -> $23,598, max DD -54%, worst month -27.8%, ~9 trades/week.', bt={'end': 23598, 'dd': -54, 'wm': -27.8, 'wk': 9, 'start': '2024-10-20', 'stop': '2026-10-04', 'engine': 'v3', 'insample': True},
                  sleeves=[sleeve('MOM', 'ema_mom', .5, .05, 8, mgmt=PY), sleeve('DCA', 'dca_dip', .5, .05, 6)]),
}

# FBL-BT01 (label only - no default, note or number changed): every profile with a DCA slot was backtested before the
# intrabar path fix (basket TP after a safety order was tested against the same candle's earlier extreme). Its quoted
# numbers stay as they were until they are re-validated with the fixed backtester.
UNVERIFIED_BT01 = 'unverified: backtest path fix pending re-validation (FBL-BT01)'
for _p in PRESETS.values():
    if any(_s['key'] == 'dca_dip' for _s in _p['sleeves']): _p['bt']['unverified'] = UNVERIFIED_BT01
del _p

GLOBAL_DEFAULTS = dict(COMPOUND=False, CAP_SINCE='', CAP_ADJ=[], CAP_CYCLES=[], TELEGRAM_ON=False, TELEGRAM_CHAT='', MAX_LEVERAGE=10, DAILY_LOSS_HALT=0.08, PEAK_DD_FLATTEN=0.0, CAPITAL_CAP=500.0,
                       ENTRIES_PAUSED=False, AI_FILTER=False, PRESET='original',
                       UNIVERSE=list(TOP40), SYMBOLS_ON={}, RUN_IN_BACKGROUND=True,
                       MARKET_COLLECTOR=True,         # observe-only public market-data collector (market_collector.py); owner: collect now
                       # v3.1 - all off by default (risk rules only WARN: they log, never block, until set to 'enforce')
                       ENTRY_ORDER='market', MAKER_FALLBACK=True, MAKER_REPRICE=3, MAKER_WAIT_S=40, FEE_MAKER=0.0002,
                       PUMP_GUARD={}, RISK_RULES={}, GOVERNOR=dict(mode='off', rules=[]))

# Portfolio risk rules: mode 'off' | 'warn' (log + 'WARNING:' entry on the missed list, still trades) | 'enforce' (entry refused)
RISK_RULE_DEFAULTS = dict(
    coin_cap=dict(mode='warn', x=3.0),                 # total notional on one coin (all slots) <= x * bot capital
    open_risk_cap=dict(mode='warn', pct=15.0),         # open risk to stops incl. the new trade <= pct % of bot capital
    correlated_cap=dict(mode='warn', n=3, rho=0.8),    # < n open same-direction trades on coins correlated > rho with the new coin
    btc_breaker=dict(mode='warn', pct=5.0, hours=4.0, tighten=False, dca='pause'),   # BTC moved > pct % in the last hour -> pause `hours`;
    #   while it is active (enforce): no pyramid adds; DCA safety orders follow the lot's policy pause / half_size / continue_plan
    funding_filter=dict(mode='warn', rate=0.001))      # no longs when funding > rate, no shorts when < -rate
GOV_COOLDOWN_S = 24 * 3600                             # minimum time between two automatic profile switches
GOV_MULT_MAX = 2.0                                     # martingale-style recovery (risk_mult > 1) is capped here


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


SAVE_RETRY_S = (0.05, 0.1, 0.25, 0.5)   # AUD-05: os.replace on Windows fails with PermissionError while another process
#                                         (antivirus, indexer, a viewer) holds the target - retried this long, then raised


class CorruptFile(ValueError):
    """AUD-05: a JSON file is present but empty, truncated, not JSON or of the wrong top-level type."""


def _replace_retry(src, dst):
    for d in SAVE_RETRY_S + (None,):
        try:
            os.replace(src, dst); return
        except PermissionError:
            if d is None: raise
            _sleep(d)


def _fsync_dir(d):
    if os.name == 'nt': return                   # Windows cannot open a directory for fsync; NTFS journals the rename
    try:
        fd = os.open(d or '.', os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    except OSError:
        pass


def _dump(obj, f, strict):
    """strict (safety files): canonical - sorted keys, no NaN/Infinity, no silent coercion of unsupported values (a set,
    a datetime, a numpy integer ... raises TypeError and nothing is written). Non-strict (aux / backtest files): as before."""
    if strict: json.dump(obj, f, indent=2, sort_keys=True, allow_nan=False)
    else: json.dump(obj, f, indent=2, default=str)


def _write_durable(path, obj, strict=False):
    """AUD-05: serialise `obj` into a temp file in the same folder, flush + fsync, then atomically replace `path`. A failure at
    any point (unsupported value, disk full, a held file) leaves the previous `path` exactly as it was (never a partially
    written target) and removes the temp file."""
    tmp = f'{path}.{os.getpid()}-{threading.get_ident()}.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='') as f:
            _dump(obj, f, strict); f.flush(); os.fsync(f.fileno())
        _replace_retry(tmp, path)
    except BaseException:
        try: os.remove(tmp)
        except OSError: pass
        raise
    _fsync_dir(os.path.dirname(path))


def read_json(path, kind=None):
    """AUD-05: strict read. Raises CorruptFile for an empty / truncated / non-JSON / wrong-type file, OSError if unreadable."""
    with open(path, 'rb') as f: raw = f.read()
    try: txt = raw.decode('utf-8-sig')             # a BOM (Notepad) is not damage
    except UnicodeDecodeError: raise CorruptFile('not UTF-8 text') from None
    if not txt.strip(): raise CorruptFile('empty file')
    try: obj = json.loads(txt)
    except ValueError as ex: raise CorruptFile(f'not valid JSON ({str(ex)[:80]})') from None
    if kind is not None and not isinstance(obj, kind):
        raise CorruptFile(f'top level is {type(obj).__name__}, expected {kind.__name__}')
    return obj


def read_json_retry(path, kind=None):
    """AUD-05 r1: read_json, with an OSError (sharing violation from antivirus / an indexer) retried on the same backoff
    as a replace. CorruptFile and a missing file are never retried; a persistent OSError is raised."""
    for d in SAVE_RETRY_S + (None,):
        try:
            return read_json(path, kind)
        except (CorruptFile, FileNotFoundError):
            raise
        except OSError:
            if d is None: raise
            _sleep(d)


SECRET_SETTING_KEYS = ('TELEGRAM_TOKEN',)        # AUD-05 r2: secrets live only in the encrypted config, never in settings


def scrub_secrets(doc):
    """A copy of a settings-like dict without secret keys (anything else is returned unchanged)."""
    return {k: v for k, v in doc.items() if k not in SECRET_SETTING_KEYS} if isinstance(doc, dict) else doc


def save_json(path, obj, backup=False, strict=False):
    """Durable JSON write (AUD-05): temp file + fsync + atomic replace (a bad object or a failed write never touches the
    target). strict=True: canonical serialisation (sorted keys, no NaN, no coercion). backup=True first keeps the previous
    file as `<path>.bak` (also durable, secrets scrubbed) - only when that previous file is itself good JSON of the same
    top-level type, so a damaged file never replaces a good backup. A previous file that is damaged at save time is moved
    aside as evidence first (never silently overwritten); its evidence path is returned (else None). If it cannot be moved
    aside nothing is written and OSError is raised."""
    evidence = None
    if backup and os.path.exists(path):
        try: prev = read_json(path, type(obj))
        except CorruptFile:
            prev, evidence = None, quarantine(path)
            if not evidence: raise OSError(f'{os.path.basename(path)} is damaged and could not be moved aside - not saved over')
        except OSError: prev = None
        if prev is not None:
            try: _write_durable(path + '.bak', scrub_secrets(prev), strict)
            except (OSError, TypeError, ValueError) as ex:
                log.warning(f'{os.path.basename(path)}.bak not written ({type(ex).__name__}: {ex}) - main file still saved')
    _write_durable(path, obj, strict)
    return evidence


# ------------------------------------------------------------------ AUD-05 r2: versioned schemas (validated before use)
STATE_SCHEMA = 1           # state.json: v0 = unversioned (before AUD-05 r2) -> migrated by adding the version
SETTINGS_SCHEMA = 1        # settings.json: same; v0 legacy secrets are dropped by the migration
INSTALL_SCHEMA = 1
SIDES = ('LONG', 'SHORT')
_SYM_RE = re.compile(r'[A-Z0-9]{2,30}')
_TAG_RE = re.compile(r'(o|a|c|ac):[A-Za-z0-9_.\-]{1,80}')
_CID_RE = re.compile(r'[A-Za-z0-9_.\-]{1,36}')
PENDING_KINDS = ('add', 'close')
RESTING_STATUS = ('between', 'open', 'cancelling')


class SchemaError(CorruptFile):
    """AUD-05 r2: syntactically valid JSON that breaks the schema (types, finite numbers, enums, ids, ownership records).
    Handled exactly like a damaged file: moved aside, older backup or fail closed."""


def _bad(where, what):
    raise SchemaError(f'{where}: {what}')


def _num(v, where, lo=None, hi=None, opt=False, gt=None):
    if v is None and opt: return
    if isinstance(v, bool) or not isinstance(v, (int, float)): _bad(where, f'not a number ({type(v).__name__})')
    if not math.isfinite(v): _bad(where, 'not finite')
    if lo is not None and v < lo: _bad(where, f'below {lo}')
    if hi is not None and v > hi: _bad(where, f'above {hi}')
    if gt is not None and not v > gt: _bad(where, f'must be > {gt}')


def _int(v, where, lo=0, opt=False):
    if v is None and opt: return
    if isinstance(v, bool) or not isinstance(v, int) or v < lo: _bad(where, f'not an integer >= {lo}')


def _str(v, where, rx=None, opt=False, choices=None):
    if v is None and opt: return
    if not isinstance(v, str): _bad(where, f'not a string ({type(v).__name__})')
    if choices is not None and v not in choices: _bad(where, f'{v[:20]!r} not one of {choices}')
    if rx is not None and not rx.fullmatch(v): _bad(where, f'{v[:30]!r} has the wrong format')


def _dict(v, where, opt=False):
    if v is None and opt: return {}
    if not isinstance(v, dict): _bad(where, f'not an object ({type(v).__name__})')
    return v


def _bool(v, where, opt=False):
    if v is None and opt: return
    if not isinstance(v, bool): _bad(where, 'not true/false')


def _version(doc, cur, name):
    v = doc.get('schema_version', 0)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0: _bad(f'{name}.schema_version', 'not a version number')
    if v > cur: _bad(f'{name}.schema_version', f'{v} is newer than this build understands ({cur})')
    return v


def migrate_state(doc):
    """Explicit migrations of state.json (in place). v0 -> v1: the version field is added; the content is unchanged."""
    if _version(doc, STATE_SCHEMA, 'state') < 1: doc['schema_version'] = 1
    return doc


def migrate_settings(doc):
    """Explicit migrations of settings.json (in place). v0 -> v1: the version is added and a legacy plain-text Telegram
    token is dropped (app.migrate_legacy_secrets moved it into the encrypted config before the engine starts)."""
    if _version(doc, SETTINGS_SCHEMA, 'settings') < 1: doc['schema_version'] = 1
    for k in SECRET_SETTING_KEYS: doc.pop(k, None)
    return doc


def _validate_lot(k, l):
    w = f'lots[{k[:40]}]'
    _dict(l, w)
    _str(l.get('symbol'), w + '.symbol', _SYM_RE); _str(l.get('side'), w + '.side', choices=SIDES)
    _str(l.get('sleeve'), w + '.sleeve')
    _num(l.get('qty'), w + '.qty', lo=0); _num(l.get('avg'), w + '.avg', gt=0); _num(l.get('stop'), w + '.stop', gt=0)
    _str(l.get('stop_id'), w + '.stop_id', _TAG_RE, opt=True)
    _bool(l.get('stop_dirty'), w + '.stop_dirty', opt=True)
    _int(l.get('stop_miss'), w + '.stop_miss', opt=True)
    _num(l.get('stop_miss_t'), w + '.stop_miss_t', opt=True)
    _str(l.get('stop_miss_why'), w + '.stop_miss_why', opt=True, choices=STOP_MISS_ORDER)
    _str(l.get('stop_foreign'), w + '.stop_foreign', _TAG_RE, opt=True)
    _num(l.get('stop_confirmed_t'), w + '.stop_confirmed_t', opt=True)
    _bool(l.get('restored_from_bak'), w + '.restored_from_bak', opt=True)
    pd_ = l.get('pending')
    if pd_ is not None:
        _dict(pd_, w + '.pending')
        _str(pd_.get('kind'), w + '.pending.kind', choices=PENDING_KINDS)
        _num(pd_.get('qty'), w + '.pending.qty', lo=0); _num(pd_.get('t'), w + '.pending.t', lo=0)
        _num(pd_.get('px'), w + '.pending.px', opt=True)
        _str(pd_.get('cid'), w + '.pending.cid', _CID_RE, opt=True)
        _dict(pd_.get('post'), w + '.pending.post', opt=True)
    _dict(l.get('mgmt'), w + '.mgmt', opt=True)


def _validate_ue(k, u):
    w = f'unconfirmed_entries[{k[:40]}]'
    _dict(u, w)
    _str(u.get('symbol'), w + '.symbol', _SYM_RE); _str(u.get('side'), w + '.side', choices=SIDES)
    _str(u.get('cid'), w + '.cid', _CID_RE, opt=True)
    _num(u.get('qty'), w + '.qty', gt=0); _num(u.get('t'), w + '.t', lo=0)
    _str(u.get('prov'), w + '.prov', _TAG_RE, opt=True); _num(u.get('prov_qty'), w + '.prov_qty', lo=0, opt=True)
    _num(u.get('prov_stop'), w + '.prov_stop', gt=0, opt=True); _num(u.get('seen_qty'), w + '.seen_qty', lo=0, opt=True)
    pp = u.get('prov_pending')
    if pp is not None:
        _dict(pp, w + '.prov_pending')
        _str(pp.get('tag'), w + '.prov_pending.tag', _TAG_RE); _str(pp.get('alt'), w + '.prov_pending.alt', _TAG_RE, opt=True)
        _num(pp.get('qty'), w + '.prov_pending.qty', gt=0); _num(pp.get('stop'), w + '.prov_pending.stop', gt=0)
        _num(pp.get('t'), w + '.prov_pending.t', lo=0)
    plan = _dict(u.get('plan'), w + '.plan')
    _num(plan.get('px'), w + '.plan.px', gt=0); _num(plan.get('stop_dist'), w + '.plan.stop_dist', gt=0)


def _validate_resting(k, r_):
    w = f'resting_entries[{k[:40]}]'
    _dict(r_, w)
    _str(r_.get('symbol'), w + '.symbol', _SYM_RE); _str(r_.get('side'), w + '.side', choices=SIDES)
    _num(r_.get('qty'), w + '.qty', gt=0); _num(r_.get('filled'), w + '.filled', lo=0); _num(r_.get('cost'), w + '.cost', lo=0)
    _int(r_.get('n'), w + '.n'); _str(r_.get('status'), w + '.status', choices=RESTING_STATUS)
    _str(r_.get('cid'), w + '.cid', _CID_RE, opt=True)
    if r_.get('status') in ('open', 'cancelling') and not r_.get('cid'): _bad(w + '.cid', 'a working order needs its client id')
    _dict(r_.get('plan'), w + '.plan')


def _validate_trail(k, p):
    w = f'pending_entries[{k[:40]}]'
    _dict(p, w)
    _str(p.get('symbol'), w + '.symbol', _SYM_RE); _str(p.get('side'), w + '.side', choices=SIDES)
    _str(p.get('sleeve'), w + '.sleeve')
    for f in ('ext', 'atr', 'dev', 'until'): _num(p.get(f), f'{w}.{f}')


OWNERSHIP_FAMILIES = ('lots', 'unconfirmed_entries', 'resting_entries', 'pending_entries', 'grids', 'orphans')


def ownership_present(state):
    """AUD-05 r3: THE predicate for 'this state owns something on (or intends something for) the exchange account':
    lots (incl. their pending orders), unconfirmed / resting / trailing entry intents, grids (incl. their pending ops) and
    stops queued for cancellation. Returns the sorted list of non-empty families ([] = owns nothing)."""
    return [f for f in OWNERSHIP_FAMILIES if (state or {}).get(f)]


GRID_STATUS = ('active', 'stopping')
GRID_MODES = ('long', 'short', 'neutral')
GRID_OP_KINDS = ('open', 'add', 'red')
_OP_ID_RE = re.compile(r'[0-9a-f]{6,32}')


GRID_MAX_SPAN = 20.0          # hi / lo of any grid the bot builds stays far below this (pct <= 50 %: 3x)


def _validate_grid(k, g, lots):
    """AUD-05 r3: the persisted grid contract. Quantities must be what build_grid could have produced for the grid's own
    capital (q * lo <= capital * capital_frac / max cells per side), so a tampered cell can never become an order."""
    w = f'grids[{k[:40]}]'
    _dict(g, w)
    _str(g.get('slot'), w + '.slot'); _str(g.get('sym'), w + '.sym', _SYM_RE)
    if g.get('key') != k or k != f"{g.get('slot')}|{g.get('sym')}": _bad(w + '.key', 'does not match slot|symbol')
    _str(g.get('status'), w + '.status', choices=GRID_STATUS); _str(g.get('mode'), w + '.mode', choices=GRID_MODES)
    _str(g.get('tf'), w + '.tf')
    for f in ('p0', 'lo', 'hi', 'stop_lo', 'stop_hi'): _num(g.get(f), f'{w}.{f}', gt=0)
    if not g['lo'] < g['hi']: _bad(w, 'lo must be below hi')
    _num(g.get('capital'), w + '.capital', gt=0, hi=1e9)
    cfg = _dict(g.get('cfg'), w + '.cfg')
    _num(cfg.get('capital_frac', 1.0), w + '.cfg.capital_frac', 0.1, 10)
    _str(cfg.get('mode', g['mode']), w + '.cfg.mode', choices=GRID_MODES)
    cells = g.get('cells')
    if not isinstance(cells, list) or not 1 <= len(cells) <= 40: _bad(w + '.cells', 'not a list of 1-40 cells')
    lines = g.get('lines')
    if not isinstance(lines, list) or len(lines) != len(cells) + 1: _bad(w + '.lines', 'must have one more line than cells')
    for i, x in enumerate(lines): _num(x, f'{w}.lines[{i}]', gt=0)
    if any(lines[i] >= lines[i + 1] for i in range(len(lines) - 1)): _bad(w + '.lines', 'not strictly increasing')
    if g['hi'] / g['lo'] > GRID_MAX_SPAN: _bad(w, f'range {g["lo"]:g}-{g["hi"]:g} wider than {GRID_MAX_SPAN:g}x')
    if not g['lo'] < g['p0'] < g['hi']: _bad(w + '.p0', 'start price outside the range')
    # AUD-05 r4: the executable topology is REBUILT from the grid's own parameters and compared - lines span lo..hi with
    # the configured count and spacing, every cell sits between adjacent lines with its side, and no quantity exceeds what
    # build_grid sizes for that cell (budget / entry reference); persisted metrics are never trusted for the budget.
    try:
        ref = GRID.build_grid(g['p0'], 1.0, dict(cfg, range_kind='lookback', range_value=10, mode=g['mode']), g['capital'],
                              lo_hi=(g['lo'], g['hi']))
    except Exception as ex:
        _bad(w + '.cfg', f'cannot rebuild the grid ({str(ex)[:80]})')
    if len(ref['cells']) != len(cells): _bad(w + '.cells', f"{len(cells)} cells, the configuration builds {len(ref['cells'])}")
    tol = lambda x, y: abs(x - y) <= 1e-9 * max(abs(x), abs(y), 1e-12)
    if not all(tol(x, y) for x, y in zip(lines, ref['lines'])): _bad(w + '.lines', 'do not match lo..hi with the configured spacing')
    n_l = sum(1 for c in cells if isinstance(c, dict) and c.get('k') == 'L')
    n_s = sum(1 for c in cells if isinstance(c, dict) and c.get('k') == 'S')
    per = g['capital'] * float(cfg.get('capital_frac', 1.0)) / max(n_l, n_s, 1)
    for i, c in enumerate(cells):
        wc = f'{w}.cells[{i}]'; _dict(c, wc)
        _num(c.get('a'), wc + '.a', gt=0); _num(c.get('b'), wc + '.b', gt=0)
        if not c['a'] < c['b']: _bad(wc, 'a must be below b')
        _str(c.get('k'), wc + '.k', choices=('L', 'S')); _bool(c.get('f'), wc + '.f'); _bool(c.get('init'), wc + '.init', opt=True)
        _num(c.get('q'), wc + '.q', lo=0)
        rc = ref['cells'][i]
        if not (tol(c['a'], lines[i]) and tol(c['b'], lines[i + 1])): _bad(wc, 'not between its adjacent lines')
        if c.get('k') != rc['k']: _bad(wc + '.k', 'side differs from the one the configuration builds')
        if c['q'] > rc['q'] * (1 + 1e-6) + 1e-12 or c['q'] * g['lo'] > per * (1 + 1e-6) + 1e-12:
            _bad(wc + '.q', f"{c['q']:g} exceeds the grid's own budget for this cell")
        _num(c.get('e'), wc + '.e', gt=0, opt=True)
    for side, lk in _dict(g.get('lots'), w + '.lots').items():
        _str(side, w + '.lots.side', choices=SIDES); _str(lk, f'{w}.lots[{side}]')
        l = lots.get(lk)
        if l is not None and (l.get('symbol') != g['sym'] or l.get('side') != side):
            _bad(f'{w}.lots[{side}]', 'refers to a lot of another coin / side')
    op = g.get('op')
    if op is not None:
        wo = w + '.op'; _dict(op, wo)
        _str(op.get('id'), wo + '.id', _OP_ID_RE); _str(op.get('side'), wo + '.side', choices=SIDES)
        _str(op.get('kind'), wo + '.kind', choices=GRID_OP_KINDS); _str(op.get('cid'), wo + '.cid', _CID_RE, opt=True)
        idx = op.get('cells')
        if not isinstance(idx, list) or not idx: _bad(wo + '.cells', 'not a list of cell indexes')
        for j in idx:
            if isinstance(j, bool) or not isinstance(j, int) or not 0 <= j < len(cells): _bad(wo + '.cells', f'{j!r} is not a cell')
        _num(op.get('qty'), wo + '.qty', gt=0); _num(op.get('px'), wo + '.px', gt=0); _num(op.get('t'), wo + '.t', lo=0)
        _bool(op.get('full'), wo + '.full', opt=True)
        if op['qty'] > sum(cells[j]['q'] for j in idx) * (1 + 1e-6) + 1e-12: _bad(wo + '.qty', 'larger than its cells')
    _int(g.get('cycles', 0), w + '.cycles'); _num(g.get('cycle_pnl', 0.0), w + '.cycle_pnl')
    _int(g.get('trend_bars', 0), w + '.trend_bars')
    met = _dict(g.get('metrics'), w + '.metrics')                 # AUD-05 r4: every metric the bot USES must be there
    _num(met.get('worst_loss_usd'), w + '.metrics.worst_loss_usd', lo=0, hi=g['capital'] * 1e3)


def validate_install(doc):
    """AUD-05 r3: install.json - exact version, created time, and the account fingerprint structure."""
    v = doc.get('schema_version')
    if isinstance(v, bool) or v != INSTALL_SCHEMA: _bad('install.schema_version', f'{v!r} is not the supported {INSTALL_SCHEMA}')
    _str(doc.get('created'), 'install.created')
    if not doc['created']: _bad('install.created', 'empty')
    acct = _dict(doc.get('account'), 'install.account')
    _str(acct.get('mode'), 'install.account.mode', choices=('paper', 'live'))
    _str(acct.get('base'), 'install.account.base')
    _str(acct.get('key'), 'install.account.key')
    if acct['key'] and not re.fullmatch(r'[0-9a-f]{16}', acct['key']): _bad('install.account.key', 'not a 16-hex digest')
    return doc


def validate_state(doc):
    """AUD-05 r2: every AUD-00..04 ownership record of state.json, before it is used. Raises SchemaError."""
    if _version(doc, STATE_SCHEMA, 'state') != STATE_SCHEMA: _bad('state.schema_version', 'not migrated')
    for k, l in _dict(doc.get('lots'), 'lots', opt=True).items(): _validate_lot(k, l)
    for k, u in _dict(doc.get('unconfirmed_entries'), 'unconfirmed_entries', opt=True).items(): _validate_ue(k, u)
    for k, r_ in _dict(doc.get('resting_entries'), 'resting_entries', opt=True).items(): _validate_resting(k, r_)
    for k, p_ in _dict(doc.get('pending_entries'), 'pending_entries', opt=True).items(): _validate_trail(k, p_)
    orph = doc.get('orphans') or []
    if not isinstance(orph, list): _bad('orphans', 'not a list')
    for i, o in enumerate(orph):
        if not (isinstance(o, list) and len(o) == 2): _bad(f'orphans[{i}]', 'not a [symbol, tag] pair')
        _str(o[0], f'orphans[{i}].symbol', _SYM_RE); _str(o[1], f'orphans[{i}].tag', _TAG_RE)
    for f in ('short_seen', 'over_seen'):
        for k, n in _dict(doc.get(f), f, opt=True).items(): _int(n, f'{f}[{k[:40]}]')
    _bool(doc.get('halted'), 'halted', opt=True)
    for f in ('day_start_equity', 'peak_equity'): _num(doc.get(f), f, opt=True)
    _str(doc.get('day'), 'day', opt=True)
    lots = _dict(doc.get('lots'), 'lots', opt=True)
    for k, g in _dict(doc.get('grids'), 'grids', opt=True).items(): _validate_grid(k, g, lots)
    if not isinstance(doc.get('grid_history', []), list): _bad('grid_history', 'not a list')
    return doc


_SETTING_NUM = dict(MAX_LEVERAGE=(1, 125), DAILY_LOSS_HALT=(0.0, 1.0), PEAK_DD_FLATTEN=(0.0, 1.0), CAPITAL_CAP=(0.0, 1e12),
                    MAKER_REPRICE=(0, 10), MAKER_WAIT_S=(0, 3600), FEE_MAKER=(-0.01, 0.01))
_SETTING_BOOL = ('ENTRIES_PAUSED', 'COMPOUND', 'TELEGRAM_ON', 'AI_FILTER', 'MAKER_FALLBACK', 'RUN_IN_BACKGROUND')


def validate_settings(doc):
    """AUD-05 r2: the safety-relevant settings, before use (cosmetic / optional switches keep their lenient fallbacks in
    load_settings). Raises SchemaError."""
    if _version(doc, SETTINGS_SCHEMA, 'settings') != SETTINGS_SCHEMA: _bad('settings.schema_version', 'not migrated')
    for k in SECRET_SETTING_KEYS:
        if k in doc: _bad(k, 'a secret must not be stored in settings')
    for k, (lo, hi) in _SETTING_NUM.items():
        if k in doc: _num(doc[k], k, lo, hi)
    for k in _SETTING_BOOL:
        if k in doc: _bool(doc[k], k)
    if 'ENTRY_ORDER' in doc: _str(doc['ENTRY_ORDER'], 'ENTRY_ORDER', choices=('market', 'maker'))
    if 'UNIVERSE' in doc:
        if not isinstance(doc['UNIVERSE'], list): _bad('UNIVERSE', 'not a list')
        for i, x in enumerate(doc['UNIVERSE']): _str(x, f'UNIVERSE[{i}]', _SYM_RE)
    for k, v in _dict(doc.get('SYMBOLS_ON'), 'SYMBOLS_ON', opt=True).items(): _bool(v, f'SYMBOLS_ON[{k[:30]}]')
    if 'SLEEVES' in doc:
        if not isinstance(doc['SLEEVES'], list): _bad('SLEEVES', 'not a list')
        for i, sl in enumerate(doc['SLEEVES']):
            w = f'SLEEVES[{i}]'; _dict(sl, w)
            _str(sl.get('id'), w + '.id'); _str(sl.get('key'), w + '.key')
            _num(sl.get('share'), w + '.share', 0.0, 1.0); _num(sl.get('risk'), w + '.risk', 0.0, 1.0)
    if 'GRID_SLOTS' in doc and not isinstance(doc['GRID_SLOTS'], list): _bad('GRID_SLOTS', 'not a list')
    return doc


@functools.lru_cache(maxsize=8)
def _key_digest(key):
    """Slow salted KDF (PBKDF2-HMAC-SHA256), not a fast hash: the marker must not make an API key cheap to brute-force
    (CodeQL py/weak-sensitive-data-hashing). Cached: computed once per key per process."""
    import hashlib
    return hashlib.pbkdf2_hmac('sha256', key.encode(), b'zackbot-install-marker-v1', 200_000).hex()[:16]


def account_fingerprint(cfg, base):
    """Non-secret account identity for install.json: mode, exchange URL and a short one-way KDF digest of the API key."""
    key = str((cfg or {}).get('API_KEY') or '')
    return dict(mode='live' if (cfg or {}).get('MODE') == 'live' else 'paper', base=str(base or ''),
                key=_key_digest(key) if key else '')


# AUD-05 r3: raw-byte redaction of legacy secret material, for files that cannot be parsed (damaged settings evidence):
# a secret key name with whatever value follows it (also an unterminated / truncated one), and bot-token-shaped values
_SECRET_KEY_RX = re.compile(rb'"?(?:' + b'|'.join(re.escape(k.encode()) for k in SECRET_SETTING_KEYS) +
                            rb')"?[ \t]*:?[ \t]*(?:"[^"\r\n]*"?|[^,}\r\n]*)')
_BOT_TOKEN_RX = re.compile(rb'\d{5,}:[A-Za-z0-9_\-]{8,}')
_TOKEN_VALUE_RX = re.compile(rb'"(?:' + b'|'.join(re.escape(k.encode()) for k in SECRET_SETTING_KEYS) +
                             rb')"[ \t]*:[ \t]*"(\d{5,15}:[A-Za-z0-9_\-]{20,80})"')
REDACTED = b'"[redacted]": "[redacted]"'


def redact_secrets(raw):
    """Bytes with every recognizable legacy secret (key name + value, bot-token-shaped values) replaced."""
    return _BOT_TOKEN_RX.sub(b'[redacted]', _SECRET_KEY_RX.sub(REDACTED, raw))


def _secret_bearing(path):
    return os.path.basename(path).startswith('settings.json')


def _write_bytes_durable(path, data):
    tmp = f'{path}.{os.getpid()}-{threading.get_ident()}.tmp'
    try:
        with open(tmp, 'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
        _replace_retry(tmp, path)
    except BaseException:
        try: os.remove(tmp)
        except OSError: pass
        raise
    _fsync_dir(os.path.dirname(path))


def migrate_legacy_secrets(data_dir):
    """AUD-05 r2/r3: a v2 settings.json (and its .bak) may hold the Telegram token in plain text. Returns that token (or '')
    - also from a file that no longer parses, when the token is still recognizable in it. Never raises."""
    found = ''
    for p in (os.path.join(data_dir, 'settings.json'), os.path.join(data_dir, 'settings.json.bak')):
        if not os.path.exists(p): continue
        try:
            doc = read_json(p, dict)
            tok = next((doc.get(k) for k in SECRET_SETTING_KEYS if doc.get(k)), '')
            found = found or str(tok or '')
        except (CorruptFile, OSError):
            try:
                with open(p, 'rb') as f: m = _TOKEN_VALUE_RX.search(f.read())
                if m: found = found or m.group(1).decode('ascii')
            except OSError: pass
    return found


def settings_siblings(data_dir):
    """AUD-05 r4: every file that may hold settings bytes: settings.json, .bak, damaged-copy evidence (.corrupt-*) and
    temp files of an interrupted write (settings.json.<pid>-<tid>.tmp, settings.json.bak.<pid>-<tid>.tmp)."""
    try: names = os.listdir(data_dir)
    except OSError: return []
    out = []
    for n in sorted(names):
        if n in ('settings.json', 'settings.json.bak') or (n.startswith('settings.json.corrupt-')) or \
                (n.startswith('settings.json.') and n.endswith('.tmp')):
            p = os.path.join(data_dir, n)
            if os.path.isfile(p): out.append(p)
    return out


def redact_settings_files(data_dir):
    """Remove legacy secret material from every settings sibling (after migrate_legacy_secrets' token reached the encrypted
    config): a parseable file loses its secret keys; anything else is redacted byte-wise (the rest of the evidence stays).
    Each rewrite is atomic. Returns the names that could NOT be cleaned (the caller raises an unresolved-secret incident)."""
    failed = []
    for p in settings_siblings(data_dir):
        try:
            with open(p, 'rb') as f: raw = f.read()
            try:
                doc = json.loads(raw.decode('utf-8-sig'))
            except ValueError:
                doc = None
            if isinstance(doc, dict) and any(k in doc for k in SECRET_SETTING_KEYS):
                _write_durable(p, scrub_secrets(doc))
                with open(p, 'rb') as f: raw = f.read()
            red = redact_secrets(raw)
            if red != raw: _write_bytes_durable(p, red)
        except Exception as ex:
            failed.append(f'{os.path.basename(p)} ({type(ex).__name__})')
    return failed


def corrupt_siblings(path):
    """AUD-05 r1: evidence files `<path>.corrupt-*` left by an earlier start-up."""
    d, fn = os.path.split(path)
    try: return sorted(f for f in os.listdir(d or '.') if f.startswith(os.path.basename(fn) + '.corrupt-'))
    except OSError: return []


def quarantine(path):
    """AUD-05: move a damaged file aside as `<path>.corrupt-<UTC time>` (never overwritten, never deleted). Falls back to a
    byte copy if the rename is refused. Returns the evidence path, or None if it could not be preserved."""
    base = f"{path}.corrupt-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    dst, n = base, 1
    while os.path.exists(dst): n += 1; dst = f'{base}-{n}'
    if _secret_bearing(path):                        # AUD-05 r3: settings evidence never keeps a legacy secret
        try:
            with open(path, 'rb') as f: raw = f.read()
            red = redact_secrets(raw)
        except OSError:
            return None
        if red != raw:
            try:
                with open(dst, 'xb') as f: f.write(red); f.flush(); os.fsync(f.fileno())
                os.remove(path)
                return dst
            except OSError:
                return None
    try:
        os.rename(path, dst); return dst
    except OSError:
        pass
    try:
        with open(path, 'rb') as f: raw = f.read()
        with open(dst, 'xb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
        return dst
    except OSError:
        return None


ORDER_FINAL = ('FILLED', 'EXPIRED', 'CANCELED', 'REJECTED', 'EXPIRED_IN_MATCH')   # AUD-03: executedQty is final in these


class UnfilledOrder(RuntimeError):
    """AUD-03: Binance answered with a FINAL status and nothing executed (e.g. a market order EXPIRED): nothing is booked,
    the lot, its quantity and its stop are exactly as before. The management stage reports it (an add is cooled down)."""


class PartialClose(RuntimeError):
    """AUD-03: a full close executed only partly - the executed part is booked, the lot keeps the rest with its stop."""


def _cid_of(tag):
    return tag[2:] if isinstance(tag, str) and tag.startswith('c:') else None


ADD_RETRY_S, ADD_RETRY_TRANSIENT_S = 300, 60
UNCONF_DROP_S, UNCONF_ADOPT_S = 20, 300        # AUD-03b: an unconfirmed entry is dropped / adopted from the position after   # AUD-02: a failed add is retried after this (not every pass)
JOURNAL_BACKLOG_MAX = 5000            # AUD-01: trades.csv rows kept in memory while the file cannot be written
INCIDENT_IDLE_S = 900                 # T05b: a repeat after 15 quiet minutes starts a new entry
INCIDENT_MAX = 200                    # T05b: incidents kept (oldest dropped first, closed before open)
INCIDENT_LOCK = threading.RLock()     # T05b final: err()/resolve()/sweep run on the loop thread AND the HTTP thread
MANAGE_ALERT_REARM_S = 900            # T05b final: the same management-failure alert is not re-sent within 15 min (flapping)
OUTAGE_ADD = 'Binance outage - no adds until it answers again'
MARK_FRESH_S = 120                    # T05b: a cached mark this recent may validate a manual stop move when Binance is down
# AUD-04 (audit C04): routine exchange verification + repair of each lot's protective stop (budget: see verify_stops)
STOP_VERIFY_S = 60.0                  # each held symbol's open orders are re-read at most about once a minute (+-20% jitter)
STOP_VERIFY_JITTER = 0.2              # fixed per-coin phase: held coins drift apart instead of bursting in one pass
STOP_SETTLE_S = 3.0                   # a stop order action is re-verified this soon after (next manage pass) ...
STOP_ACTION_MIN_S = 30.0              # ... but never sooner than 30 s after that coin's last read (a stop trailing every pass)
STOP_RECHECK_S = 10.0                 # after a miss or an unknown read the symbol is re-checked this soon
STOP_LIST_GRACE_S = 10.0              # a stop placed this recently that is not listed yet is not a miss (delayed visibility)
STOP_FRESH_S = 300.0                  # the panel shows 'protected' only with a confirmation newer than this
STOP_ALGO_UNKNOWN_MISSES = 3          # an algo stop whose status cannot be read is restored only at this many misses AND only
#                                       when that pass's open-order read was strict (classic + algo listing both read)
STOP_GONE = ('CANCELED', 'EXPIRED', 'REJECTED', 'EXPIRED_IN_MATCH')         # the stop no longer exists and never closed
STOP_FIRED = ('FILLED', 'PARTIALLY_FILLED', 'TRIGGERING', 'TRIGGERED', 'FINISHED')   # the stop fired: reconcile books it
STOP_LIVE = ('NEW',)
PROV_PENDING_S = 60          # AUD-04 r2: a provisional stop whose answer was lost: its status decides; unknown this long -> parked
STOP_TYPES = ('STOP_MARKET', 'STOP')
BOT_STOP_CID_RE = re.compile(r'z[ab][0-9a-f]{22}')   # exact new_cid('zb'|'za') shape of the bot's own stop client ids
#   (classic / algo); any other client id (manual, web, other bots, malformed) is never adopted or cancelled
STOP_MISS_ORDER = ('owner_check', 'restoring', 'checking')       # lot['stop_miss_why'] values, most severe first
STOP_MISSING_BLOCKS = dict(
    checking='protective stop not found on Binance for this coin - re-checking it; no new entries or adds until it is confirmed',
    restoring='protective stop missing on Binance for this coin - restoring it; no new entries or adds until it is confirmed',
    owner_check='the bot cannot confirm its own stop for this coin on Binance (another stop is there, or its stop cannot be '
                'read) - remove the extra stop on Binance (the bot then restores its own) or use Move stop in the panel (the '
                'bot places its stop and cancels the other one); no new entries or adds until then')
STOP_MISSING_NOTES = dict(checking='stop not found on Binance - re-checking it before any restore',
                          restoring='stop missing on Binance - restoring it',
                          owner_check='bot stop not confirmed (another stop on Binance or unreadable) - remove the extra '
                                      'stop on Binance or use Move stop')
EXCHANGE_DOWN = ('Binance is not answering status checks (no order was sent) - positions and stops are left as they '
                 'are and re-checked when it answers')


class Engine:
    def __init__(self, cfg, data_dir, dry=False):
        self.cfg, self.dir, self.dry = cfg, data_dir, dry
        self.F = {k: os.path.join(data_dir, f) for k, f in dict(state='state.json', trades='trades.csv', settings='settings.json', history='history.json', missed='missed.json',
                                                                equity='equity.json', fills='fills.jsonl', audit='trade_audit.jsonl').items()}
        self.live = cfg.get('MODE') == 'live'
        self.lock = threading.RLock()
        self.trade = Futures(cfg.get('API_KEY', ''), cfg.get('API_SECRET', ''), MAINNET if self.live else TESTNET)
        self.data = Futures('', '', MAINNET)
        _faults = testnet_faults(getattr(self.trade, 'base', None), 'lev_refuse')
        if _faults: log.warning(f'TESTNET FAULT INJECTION active: leverage changes refused for {sorted(_faults)} (ZB_TESTNET_FAULTS)')
        _ro = testnet_read_outage(getattr(self.trade, 'base', None))
        if _ro: log.warning(f'TESTNET FAULT INJECTION active: non-critical status reads under {_ro[1]} answer HTTP 503 for {_ro[0]} s '
                            f'from the first one (orders and critical reads unaffected) (ZB_TESTNET_FAULTS)')
        self._boot_incidents = []                 # AUD-05: load problems found before self.health exists (reported after)
        self.state_untrusted = {}                 # AUD-05 r2: name -> why a safety file could not be saved (blocks new risk)
        self.install_mismatch = None              # AUD-05 r3: dict(recorded, current, owned) until the owner confirms
        self._install_bad = None
        self._save_hold = {}                      # AUD-05: name -> why its file is never saved over ('unmovable' / 'unreadable')
        self.integrity = {}                       # AUD-05: name -> what happened to a damaged safety file at start-up
        self.F['install'] = os.path.join(data_dir, 'install.json')
        self._installed = self._install_read()    # AUD-05 r3: read BEFORE both safety files (initialized install evidence)
        self.load_settings()
        if (self.integrity.get('settings') or {}).get('status'):
            self._persist_pause()                 # AUD-05: restored or failed: the pause is durable (damaged file already aside)
        # migrate v1 files (single-strategy bot) so logs stay readable
        if os.path.exists(self.F['trades']):
            with open(self.F['trades']) as f: head = f.readline()
            if 'side' not in head: os.replace(self.F['trades'], self.F['trades'].replace('.csv', '_v1.csv'))
        self.state = dict(lots={}, day=None, day_start_equity=None, halted=False, peak_equity=None, last_cycle={})
        # AUD-05: corrupt -> .bak (an OLDER copy); no good copy -> empty. Either way entries are paused, and the pause is
        # made durable BEFORE the damaged file is moved aside (a crash in between can never come back unpaused)
        installed = self._installed               # AUD-05 r2: an initialized install with no state is NOT a first run
        st, status = self._load_safe('state', dict, before_aside=self._pause_or_raise, initialized=installed)
        if st: self.state.update(st)
        if status == 'restored':
            for l in (self.state.get('lots') or {}).values():
                l['restored_from_bak'] = True     # reconcile books nothing for it while entries stay paused (owner review)
        if status != 'ok': self._persist_pause()
        self._install_write(installed)
        self.state.setdefault('orphans', []); self.state.setdefault('last_cycle', {})
        self.state.setdefault('pending_entries', {}); self.state.setdefault('resting_entries', {})
        self.state.setdefault('unconfirmed_entries', {})           # AUD-03b: entries whose order answer was lost
        for l in self.state['lots'].values():           # lots saved by older versions may hold half-filled dca/pyramid blocks
            l['stop_confirmed_t'] = None                 # AUD-04: a confirmation from before this start is not fresh evidence
            l.setdefault('stop_miss', 0)                 #   (unconfirmed until the start-up pass re-verifies it)
            if l.get('key_strategy') in S.STRATEGIES and isinstance(l.get('mgmt'), dict):
                l['mgmt'] = S.merge_mgmt(l['key_strategy'], {k: v for k, v in l['mgmt'].items()})
        if not self.S.get('CAP_SINCE'):                  # first v3 start: bot capital counts closed P&L from now on
            self.S['CAP_SINCE'] = now_utc().isoformat(timespec='seconds'); self.save_settings()
        self.equity_hist = self._load_list('equity')
        self.rules, self._cache, self._kc, self.signals, self.signals_time = {}, {}, {}, {}, None
        self.last_eq = self.last_balance = None
        self.hedge = False
        self.run_now = threading.Event()
        self.history = self._load_list('history'); self.missed = self._load_list('missed')
        self.last_account, self.last_skip, self.corr = {}, '', None
        self.error = None
        self.connected = False
        self.connect_retry = None                 # T05b: {'n', 'next_t', 'since'} while start-up connect waits for Binance
        self.marks, self.marks_t = {}, 0.0
        self.guard_eq = None                      # bot capital used by the safety limits and shown in the app
        self.untracked = {}                       # exchange positions the engine has no record of
        self._journal_backlog = []                # AUD-01: trades.csv rows not written yet (file locked / disk full)
        self._lev = {}                            # leverage already set per symbol
        self.lev_refusals = {}                    # T03a: Binance leverage refusals per symbol (count, outcome, last error)
        self._fillw = fill_writer(self.F['fills'])   # T05: process-wide telemetry writer for this file (lazy thread)
        self._lev_cool = {}                       # T03c: symbol -> time before which a refused leverage change is not re-sent
        self._brk = {}                            # T03c r1: symbol -> (fetched time, leverage bracket schedule), TTL LEV_BRACKET_TTL_S
        self._lev_exc_chk = {}                    # T03c r1: symbol -> last read-only leverage check while adds are paused
        self._auditw = fill_writer(self.F['audit'], rotate_lines=AUDIT_ROTATE_LINES, slim=TA.slim_record, rotate_bytes=AUDIT_ROTATE_BYTES)   # T05a: audit events (same
        #   non-blocking writer contract as T05 fills; the in-memory window keeps only slimmed final trade records)
        self._audit_restored = set()                     # T05a: lots restored without tracking data (flagged late on first observe)
        self._audit_start()
        self._btc1h = None; self._fund = {}; self._regime = None; self._rule_warns = []
        self._sig_raw = {}                        # T05a: '<sleeve>|<symbol>' -> raw long/short flags before side masking
        self._audit_rg = {}                       # T05a owner scope: (symbol, tf) -> trend snapshot of the last CLOSED candle (cycle)
        self._audit_mso = {}                      # T05a owner scope: missed short opportunities since start (bounded, memory)
        self.health = dict(errors=collections.deque(maxlen=30), last_manage_ok=None, last_cycle_ok={}, manage_fail_streak=0,
                           last_sync=None, alerted=False, incidents={}, confirmed={})
        for key, msg in self._boot_incidents: self._integrity_alert(key, msg)   # AUD-05: loud, after health exists
        self._boot_incidents = []
        self.grids = GRID.GridManager(self)
        self.clock = time.time                    # AUD-04: stop-verifier clock (replaceable in tests)
        self._stopv = {}                          # AUD-04: symbol -> time its stops are next due for verification
        self._stopv_last = {}                     # AUD-04: symbol -> time of the last successful open-order read
        self.stopv_stats = dict(reads=0, lookups=0, unknown=0, misses=0, restored=0, adopted=0, extras_cancelled=0, deferred=0,
                                owner_checks=0)

    def _load_list(self, k):
        """Aux lists (history / missed / equity): never raise into start-up or trading (AUD-01). AUD-05: a damaged file is
        moved aside as evidence and reported, then the list starts empty."""
        p = self.F[k]
        if not os.path.exists(p): return []
        try: return read_json(p, list)
        except Exception as ex:
            aside = quarantine(p)
            self._integrity_alert(f'integrity|{k}', f'{os.path.basename(p)} unreadable ({str(ex)[:100]}) - started empty; damaged '
                                  f'copy kept as {os.path.basename(aside) if aside else "(could not be moved aside)"}; trading unaffected')
            return []

    # ------------------------------------------------------------ AUD-05: fail-closed safety files
    def _integrity_alert(self, key, msg):
        """Incident + Telegram for a damaged data file. Before self.health exists (start-up) it is queued and reported
        right after. Never raises."""
        if not hasattr(self, 'health'):
            self._boot_incidents.append((key, msg)); return
        try: self.err(msg, key=key)
        except Exception: log.error(msg)
        try: self.notify('DATA FILE PROBLEM: ' + msg)
        except Exception: pass

    def _pause_or_raise(self):
        """Make ENTRIES_PAUSED durable now; raise if settings.json could not be written (the caller then keeps the
        damaged file in place instead of moving it aside)."""
        self.S['ENTRIES_PAUSED'] = True
        if self.save_settings() is False: raise OSError('settings.json is held - the pause could not be saved')

    def _persist_pause(self):
        """Pause entries and try to persist it; never raises (a failure is one loud incident)."""
        self.S['ENTRIES_PAUSED'] = True
        try: self._pause_or_raise()
        except Exception as ex:
            self._integrity_alert('integrity|pause', f'entries paused in memory but the pause could not be saved '
                                                     f'({type(ex).__name__}: {str(ex)[:100]}) - check the data folder')

    def _install_read(self):
        """AUD-05 r2/r3: install.json marks a data folder that has already been initialized (written once, at the first
        start). Returns the validated marker dict; True when it is damaged / of an unknown version, or missing while its
        damaged copy exists (treated as initialized and failed closed: never silently rewritten); None = never initialized."""
        p = self.F['install']
        if not os.path.exists(p):
            try:                                  # AUD-05 r4: the marker's own backup restores it (same content)
                doc = validate_install(read_json_retry(p + '.bak', dict))
                save_json(p, doc, strict=True)
                self._integrity_alert('integrity|install-restored', 'install.json was missing - restored from install.json.bak')
                return doc
            except Exception:
                pass
            ev = corrupt_siblings(p)
            why = (f'missing while its damaged copy {ev[-1]} exists' if ev else
                   'missing while this data folder holds versioned (AUD-05) settings / state - it was deleted'
                   if self._versioned_data() else None)
            if why:
                self._install_bad = why
                self._integrity_alert('integrity|install', f'install.json {why} - ENTRIES PAUSED until the owner confirms the '
                                                           'account (type THIS_ACCOUNT in Settings)')
                return True
            return None
        try:
            return validate_install(read_json_retry(p, dict))
        except Exception as ex:
            aside = quarantine(p)
            self._install_bad = f'invalid ({str(ex)[:80]})'
            self._integrity_alert('integrity|install', f'install.json {self._install_bad} - treated as an initialized '
                                  f'installation, ENTRIES PAUSED until the owner confirms the account; kept as '
                                  f'{os.path.basename(aside) if aside else "(not movable, left in place)"}')
            return True

    def _versioned_data(self):
        """AUD-05 r4: evidence that this folder was initialized by a marker-writing build: a settings / state file (or its
        backup) carrying schema_version >= 1, or AUD-05 damaged-file evidence. Unversioned files = a legacy install."""
        for name in ('settings', 'state'):
            path = self.F[name]
            if corrupt_siblings(path): return True
            for p in (path, path + '.bak'):
                if not os.path.exists(p): continue
                try:
                    v = read_json(p, dict).get('schema_version', 0)
                except Exception:
                    return True                   # a damaged file next to a missing marker: not a clean legacy install
                if not isinstance(v, bool) and isinstance(v, int) and v >= 1: return True
        return False

    def _install_write(self, installed):
        """First start: write install.json (after the empty state and the settings are durable). Damaged / unknown marker:
        paused, nothing rewritten. Another account than recorded while the state owns anything (ownership_present): the
        original marker is PRESERVED and entries pause until the owner confirms (confirm_install); with no ownership at all
        the new account is recorded."""
        fp = account_fingerprint(self.cfg, getattr(self.trade, 'base', None))
        if installed is True:
            self._persist_pause(); return
        if isinstance(installed, dict):
            if installed.get('account') == fp: return
            # AUD-05 r4: a different account than recorded - NEVER rebound without the owner's confirmation. What this
            # state owned may be unknown (state restored from an older copy, or lost): that is listed too.
            owned = ownership_present(self.state) + (['state ' + self.integrity['state']['status']]
                                                     if (self.integrity.get('state') or {}).get('status') else [])
            self.install_mismatch = dict(recorded=installed.get('account'), current=fp, owned=owned)
            self._integrity_alert('integrity|install-account', 'this data folder was initialized for a different account / mode'
                                  + (f" and holds {', '.join(owned)}" if owned else '') + ' - ENTRIES PAUSED; install.json kept '
                                  'unchanged until the owner confirms the account (type THIS_ACCOUNT in Settings)')
            self._persist_pause(); return
        owned = ownership_present(self.state)
        if installed is None and owned:           # AUD-05 r4: legacy (pre-marker) state that owns something: the account it
            self.install_mismatch = dict(recorded=None, current=fp, owned=owned)   # belongs to is unknown - confirm first
            self._integrity_alert('integrity|install-account', 'this data folder predates the account marker and holds '
                                  f"{', '.join(owned)} - ENTRIES PAUSED until the owner confirms it belongs to this account "
                                  '(type THIS_ACCOUNT in Settings)')
            self._persist_pause(); return
        if installed is None:                     # first run: state and settings must be on disk before the marker is
            if not os.path.exists(self.F['state']) and not self.save_state(): return
            if not os.path.exists(self.F['settings']) and not self.save_settings(): return
        self._install_save(installed.get('created') if isinstance(installed, dict) else None)

    def _install_save(self, created=None):
        doc = dict(schema_version=INSTALL_SCHEMA, created=created or now_utc().isoformat(timespec='seconds'),
                   account=account_fingerprint(self.cfg, getattr(self.trade, 'base', None)))
        try:
            save_json(self.F['install'], doc, strict=True)
            save_json(self.F['install'] + '.bak', doc, strict=True)   # AUD-05 r4: the marker's own durable backup
            return True
        except Exception as ex:
            self._integrity_alert('integrity|install', f'install.json not written ({type(ex).__name__})'); return False

    def confirm_install(self):
        """AUD-05 r3: the owner's explicit resolution of a damaged / unknown / other-account install.json: the CURRENT
        account is recorded (the old marker was already kept aside or is replaced here on purpose). Entries stay paused
        until the owner resumes them."""
        with self.lock:
            cur = self.F['install']
            if os.path.exists(cur) and isinstance(self._installed, dict):
                quarantine(cur)                   # the other account's marker is kept as evidence, not overwritten
            if not self._install_save(): raise OSError('install.json could not be written')
            self._installed = read_json(cur, dict); self.install_mismatch = None; self._install_bad = None
            self.resolve('integrity|install-account', 'account confirmed by the owner')
            self.resolve('integrity|install', 'install.json confirmed by the owner')
            return 'account confirmed - resume entries when ready'

    @staticmethod
    def _prepare(name, doc):
        """Migrate then validate a loaded settings / state document (SchemaError = handled like a damaged file)."""
        if name == 'state': return validate_state(migrate_state(doc))
        if name == 'settings': return validate_settings(migrate_settings(doc))
        return doc

    @staticmethod
    def _read_valid(path, name, kind):
        doc = read_json_retry(path, kind)
        return Engine._prepare(name, doc) if name in ('state', 'settings') else doc

    def _load_safe(self, name, kind, before_aside=None, initialized=None):
        """AUD-05: load a safety-relevant file (settings / state). Returns (obj | None, status):
        - 'ok': good file, or a true first run (no file, no backup, no earlier evidence)
        - 'restored': the file was damaged (or missing while a backup exists) -> the OLDER <name>.bak is returned; the
          caller pauses entries for owner review
        - 'failed': no usable copy (or the file stays unreadable after retries, or it is missing while an earlier
          <name>.corrupt-* exists, i.e. an earlier recovery never finished) -> the caller fails closed (paused)
        A damaged (unparseable / wrong type / empty / truncated) file is moved aside as <name>.corrupt-<UTC>, after
        before_aside() has run (it persists the pause); if that fails, or the move is refused, the damaged file stays in
        place and every later save of it is refused. A persistently UNREADABLE file (OSError) is never moved or saved over."""
        path = self.F[name]; bak = path + '.bak'; fn = os.path.basename(path)
        hold = vars(self).setdefault('_save_hold', {})
        if not os.path.exists(path):
            ev = corrupt_siblings(path)
            if not os.path.exists(bak):
                if ev:
                    return self._load_failed(name, f'missing while the damaged copy {ev[-1]} exists (an earlier recovery did '
                                                   'not finish)', 'no backup', f'damaged copy kept as {ev[-1]}', None)
                if initialized:                       # AUD-05 r2: deleted / lost on an initialized install: NOT a first run
                    return self._load_failed(name, 'missing on an already initialized installation (install.json)', 'no backup',
                                             'resume entries once the account on Binance has been checked', None)
                return None, 'ok'
            why = 'missing while its backup exists'
        else:
            try: return Engine._read_valid(path, name, kind), 'ok'
            except SchemaError as ex: why = f'invalid: {str(ex)[:120]}'
            except CorruptFile as ex: why = f'damaged: {str(ex)[:100]}'
            except OSError as ex:
                hold[name] = 'unreadable'
                if before_aside:
                    try: before_aside()
                    except Exception: pass
                return self._load_failed(name, f'cannot be read ({type(ex).__name__}: {str(ex)[:80]})', 'not touched',
                                         'the file is left exactly as it is and never saved over until a restart', None)
        aside = None
        if os.path.exists(path):
            moved = True
            if before_aside:
                try: before_aside()
                except Exception as ex:
                    moved = False; log.warning(f'{fn}: pause not persisted ({ex}) - damaged file left in place')
            aside = quarantine(path) if moved else None
            if not aside: hold[name] = 'unmovable'
        kept = (f'damaged copy kept as {os.path.basename(aside)}' if aside else
                'damaged file could NOT be moved aside - it is left in place and never saved over' if hold.get(name)
                else 'no damaged file to keep')
        try:
            obj, bwhy = Engine._read_valid(bak, name, kind), ''
        except Exception as ex:
            obj, bwhy = None, ('no backup' if not os.path.exists(bak) else f'backup unreadable too ({str(ex)[:60]})')
        if obj is not None:
            vars(self).setdefault('integrity', {})[name] = dict(status='restored_from_backup', why=why, evidence=aside)
            self._integrity_alert(f'integrity|{name}', f'{fn} {why} - restored from {fn}.bak, an OLDER copy (the save before the '
                                                         f'last) - ENTRIES PAUSED; {kept}. Check open trades and settings against '
                                                         'Binance, then resume entries.')
            return obj, 'restored'
        if os.path.exists(bak):
            baside = quarantine(bak)
            if baside: kept += f'; damaged backup kept as {os.path.basename(baside)}'
        return self._load_failed(name, why, bwhy, kept, aside)

    def _load_failed(self, name, why, bwhy, kept, aside):
        vars(self).setdefault('integrity', {})[name] = dict(status='failed_closed', why=why, evidence=aside)
        fn = os.path.basename(self.F[name])
        what = ('the bot does not know its own open trades: positions on Binance are reported UNTRACKED (their Binance stops '
                'are left alone)' if name == 'state' else 'defaults loaded')
        self._integrity_alert(f'integrity|{name}', f'{fn} {why} and {bwhy} - ENTRIES PAUSED; {what}; {kept}. '
                                                     'Check the account on Binance before resuming entries.')
        return None, 'failed'

    def _save_safe(self, name, obj, strict=False):
        """AUD-05: durable save of a safety file with a .bak. Refused while its file is held (damaged original that could
        not be moved aside, or unreadable at start-up): reported ONCE as a keyed incident, returns False (strict=True:
        raises, e.g. for /api/settings so memory is not changed). A file found damaged at save time is kept as evidence."""
        path = self.F[name]; fn = os.path.basename(path)
        why = self._save_hold.get(name)
        if why == 'unmovable' and (not os.path.exists(path) or quarantine(path)):
            self._save_hold.pop(name, None); why = None
            self.resolve(f'save-held|{name}', f'{fn}: damaged original moved aside - saving again')
        if why:
            msg = (f'{fn} NOT saved: ' + ('its damaged original could not be moved aside (evidence kept)' if why == 'unmovable'
                                          else 'it could not be read at start-up and is never saved over until a restart')
                   + ' - entries stay paused; changes are kept in memory only')
            new_inc = not (self.health.get('incidents', {}).get(f'save-held|{name}') or {}).get('open') if hasattr(self, 'health') else False
            if hasattr(self, 'health'): self.err(msg, key=f'save-held|{name}')
            else: log.warning(msg)
            if new_inc:
                try: self.notify('DATA FILE PROBLEM: ' + msg)
                except Exception: pass
            if strict: raise OSError(msg)
            return False
        if name in ('state', 'settings'):
            obj = dict(obj, schema_version=STATE_SCHEMA if name == 'state' else SETTINGS_SCHEMA)
            if name == 'settings': obj = scrub_secrets(obj)
        try:
            ev = save_json(path, obj, backup=True, strict=True)
        except Exception as ex:                 # AUD-05 r2: disk full, a held/locked file, an unsupported value ...
            self._persist_fault(name, ex)
            if strict: raise
            return False
        if ev:
            self._integrity_alert(f'integrity|{name}|save', f'{fn} was found damaged on disk at save time - kept as '
                                                             f'{os.path.basename(ev)} and replaced by the current in-memory copy')
        unt = vars(self).setdefault('state_untrusted', {})
        if name in unt:                           # cleared only by a successful save of the WHOLE document
            unt.pop(name, None)
            if hasattr(self, 'health'):
                self.resolve(f'persist|{name}', f'{fn} saved again - new risk allowed once entries are resumed')
        return True

    def _persist_fault(self, name, ex):
        """AUD-05 r2: a safety file could not be written. Latch `state_untrusted` (blocks every risk-adding path: entries,
        adds, grid starts/adds, maker placements), pause entries (persisted when settings.json can still be written), alert
        once. Stops, closes, reconcile and the verifier keep running."""
        unt = vars(self).setdefault('state_untrusted', {})
        first = name not in unt
        why = f'{type(ex).__name__}: {str(ex)[:120]}'
        unt[name] = dict(why=why, t=time.time())
        S_ = getattr(self, 'S', None)
        if isinstance(S_, dict) and not S_.get('ENTRIES_PAUSED'):
            S_['ENTRIES_PAUSED'] = True
            if name != 'settings':
                try: self._save_safe('settings', self._settings_on_disk(S_))
                except Exception: pass
        msg = (f'{os.path.basename(self.F[name])} could not be saved ({why}) - NO new risk (entries, adds, grids, maker orders) '
               'until it saves again; stops, closes and reconcile keep running; entries paused')
        if first: self._integrity_alert(f'persist|{name}', msg)
        elif hasattr(self, 'health'): self.err(msg, key=f'persist|{name}')

    def persist_block(self):
        """AUD-05 r2: reason no new risk may be added while a safety file cannot be saved (None = fine)."""
        unt = getattr(self, 'state_untrusted', None)
        if unt: return f"data file not saved ({', '.join(sorted(unt))}.json) - no new risk until it saves again"
        return None

    def _send(self, fn, *a, **cids):
        """Call an exchange order function with explicit client ids when it DECLARES them (binance_client.Futures does; an
        offline harness / test fake may not - checked by signature, never by class, since harnesses replace the class)."""
        try:
            prm = inspect.signature(fn).parameters
            cids = {k: v for k, v in cids.items() if k in prm and prm[k].kind != prm[k].VAR_KEYWORD}
        except (TypeError, ValueError):
            cids = {}
        return fn(*a, **cids)

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
        try: unredacted = redact_settings_files(os.path.dirname(self.F['settings']))   # AUD-05 r3/r4: no legacy secret survives a load
        except Exception as ex: unredacted = [f'scan failed ({type(ex).__name__})']
        if unredacted and hasattr(self, '_integrity_alert'):
            self._integrity_alert('secret-unresolved', 'a legacy Telegram token may still be stored in clear text in: '
                                  + ', '.join(unredacted)[:150] + ' - it could not be redacted; remove these files by hand')
        got, status = Engine._load_safe(self, 'settings', dict, initialized=vars(self).get('_installed'))
        #   AUD-05: corrupt -> older .bak; none -> defaults; either way ENTRIES PAUSED; missing on an initialized install ->
        #   fail closed (called unbound: the offline UI harness loads settings on a bare namespace)
        if got: s.update(got)
        if status != 'ok': s['ENTRIES_PAUSED'] = True
        s.pop('schema_version', None)
        for k in SECRET_SETTING_KEYS: s.pop(k, None)              # AUD-05 r2: never in S (the app migrated it to the config)
        if 'SLEEVES' not in s:
            s['SLEEVES'] = copy.deepcopy(PRESETS[s.get('PRESET', 'original')]['sleeves'])
        for k in ('RISK_PER_TRADE', 'MAX_POSITIONS_PER_SLEEVE', 'STOP_ATR', 'SPLIT_ST', 'SLEEVE_ST', 'SLEEVE_TSM'):
            s.pop(k, None)                                       # v1 keys
        s['SYMBOLS_ON'] = {sym: s.get('SYMBOLS_ON', {}).get(sym, True) for sym in s['UNIVERSE']}
        if not isinstance(s.get('RISK_RULES'), dict): s['RISK_RULES'] = {}
        if not isinstance(s.get('GOVERNOR'), dict): s['GOVERNOR'] = dict(mode='off', rules=[])
        s.setdefault('GRID_SLOTS', [])
        if not isinstance(s.get('MARKET_COLLECTOR'), bool): s['MARKET_COLLECTOR'] = True
        self.S = s

    def risk_rules_cfg(self):
        """RISK_RULES merged over the defaults (a partial setting only overrides what it names)."""
        out = {}
        for k, d in RISK_RULE_DEFAULTS.items():
            v = (self.S.get('RISK_RULES') or {}).get(k) or {}
            out[k] = dict(d, **(v if isinstance(v, dict) else {}))
            if out[k]['mode'] not in ('off', 'warn', 'enforce'): out[k]['mode'] = 'warn'
        return out

    SETTINGS_SECRET_KEYS = SECRET_SETTING_KEYS            # live only in the encrypted config (app.write_cfg), never settings.json

    @classmethod
    def _settings_on_disk(cls, s):
        """What settings.json may hold: everything except secrets (the Telegram token belongs to the encrypted config; the
        Telegram PIN is stored only as its pbkdf2 hash). Defense in depth for CodeQL py/clear-text-storage on PR #30."""
        return {k: v for k, v in s.items() if k not in cls.SETTINGS_SECRET_KEYS}

    def save_settings(self):
        return self._save_safe('settings', self._settings_on_disk(self.S))

    def commit_settings(self, ns):
        """AUD-05: apply a fully validated settings dict atomically: written to disk first (durable, with .bak) - a failed
        save raises and leaves memory untouched - then every changed top-level key is applied in one step under the lock
        (unchanged keys keep their objects)."""
        with self.lock:
            self._save_safe('settings', self._settings_on_disk(ns), strict=True)
            for k in [k for k in self.S if k not in ns]: self.S.pop(k)
            for k, v in ns.items():
                if k not in self.S or self.S[k] != v: self.S[k] = v

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
        return S.merge_mgmt(sl['key'], sl.get('mgmt'))

    # ------------------------------------------------------------ exchange setup
    def connect(self):
        info = self.trade.exchange_info()
        self.rules = F.rules_from_exchange_info(info)          # BT02: the one shared parser (same filters / defaults as before)
        self.rules_meta = dict(source='exchangeInfo (live connection)', environment='mainnet' if self.live else 'testnet',
                               fetched_at=now_utc().isoformat(timespec='seconds'), verified=True)
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
                if self._exchange_down(e): raise          # T05b: Binance unreachable -> the whole connect is retried later
                log.warning(f'hedge mode check failed: {e}')
            self.equity()
        self.connected = True

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _rd(x, step):
        return F.round_step(x, step)                     # BT02: moved verbatim to feasibility.round_step

    @staticmethod
    def _qty_tol(step):
        """Engine lots and Binance positions are both whole multiples of the step, so any difference of one step or more
        is real (a stop fill, a partial fill, a manual close). Only float noise below one step is tolerated - never a whole
        step per lot: with a coarse step (DOGE/1000PEPE step 1, or a 0.002 BTC lot at step 0.001) that hid full stop-outs."""
        return 0.99 * step

    def _dust_cap(self, sym, px=None):
        """A position strictly below this size is dust: under Binance's minimum quantity or minimum notional, so it cannot be
        traded or closed normally (0 = no rule known)."""
        r = self.rules.get(sym)
        if r is None: return 0.0
        return max(r['min_qty'], r['min_notional'] / px if px else 0.0)

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
        if not al:                                     # not enough history on any coin yet (new listings / short cache)
            log.info(f'{tf}: fewer than 250 closed candles on every coin - no signals this cycle'); return {}, dfs
        ctx = S.build_context(al, 'BTCUSDT' if 'BTCUSDT' in al else next(iter(al)))
        out = {}
        for sl in list(self.S['SLEEVES']) + list(extra):
            if sl['tf'] != tf: continue
            for s in syms:
                if s not in al: continue
                raw = S.signals(sl['key'], al[s], ctx[s], sl.get('params'), mask_sides=False)
                d = al[s].iloc[-1]
                try:                                           # T05a: RAW signals before side masking (observe only, kept apart)
                    self._sig_raw[f"{sl['id']}|{s}"] = dict(le=bool(raw['le'][-1]), se=bool(raw['se'][-1]), sides=sl['sides'],
                                                            le_m=bool(raw['le'][-1]) and sl['sides'] in ('long', 'both'),
                                                            se_m=bool(raw['se'][-1]) and sl['sides'] in ('short', 'both'), time=str(d.t))
                except Exception: pass
                out[f"{sl['id']}|{s}"] = dict(le=bool(raw['le'][-1]) and sl['sides'] in ('long', 'both'),
                                              se=bool(raw['se'][-1]) and sl['sides'] in ('short', 'both'),
                                              lx=bool(raw['lx'][-1]), sx=bool(raw['sx'][-1]), close=float(d.c),
                                              vol_rank=float((al[s].atr / al[s].c).rolling(180, min_periods=60).rank(pct=True).iloc[-1]),
                                              atr=float(d.atr), time=str(d.t),
                                              rng_atr=float((d.h - d.l) / d.atr) if d.atr > 0 else 0.0)
        return out, al

    def record_equity(self, eq):
        self.equity_hist.append([int(time.time()), round(eq, 2)])
        self.equity_hist = self.equity_hist[-8000:]
        self._save_aux('equity', self.equity_hist)       # AUD-05 r1: the AUD-01 never-raise contract

    JOURNAL_FIELDS = ['time', 'event', 'sleeve', 'symbol', 'side', 'qty', 'price', 'stop', 'pnl', 'equity', 'note']

    def log_trade(self, **row):
        """Append a row to trades.csv. AUD-01: NEVER raises - it is called inside order paths, after Binance has already
        executed the order. If the file cannot be written (opened in Excel, antivirus, full disk) the row is kept in memory
        (bounded), one incident is raised, and the backlog is written first on the next successful call."""
        bl = self.__dict__.setdefault('_journal_backlog', [])
        rows, done = bl + [row], 0
        try:
            new = not os.path.exists(self.F['trades'])
            with open(self.F['trades'], 'a', newline='') as f:
                w = csv.DictWriter(f, fieldnames=self.JOURNAL_FIELDS)
                if new: w.writeheader()
                for r in rows:
                    w.writerow({k: r.get(k, '') for k in w.fieldnames}); done += 1
        except Exception as ex:
            left = rows[done:]                                           # rows already written are not repeated
            over = max(0, len(left) - JOURNAL_BACKLOG_MAX)
            if over: self.health['journal_dropped'] = self.health.get('journal_dropped', 0) + over
            bl[:] = left[:JOURNAL_BACKLOG_MAX]
            try:
                self.err(f'trade journal (trades.csv) cannot be written ({type(ex).__name__}) - {len(bl)} row(s) kept in memory '
                         'and written when the file is free again; trading and stops are not affected', key='journal')
            except Exception:
                pass
            return
        if len(rows) > 1:
            try: self.resolve('journal', 'trade journal (trades.csv) writable again - kept rows written')
            except Exception: pass
        bl.clear()

    def _save_aux(self, name, obj):
        """AUD-01: history / missed-signal files are written from order and management paths. A failed write must never
        raise there: the in-memory list stays complete and the whole list is written again on the next save."""
        try:
            save_json(self.F[name], obj)
        except Exception as ex:
            try: self.err(f'{name}.json cannot be written ({type(ex).__name__}) - kept in memory, saved again next time', key=f'save|{name}')
            except Exception: pass

    @contextlib.contextmanager
    def _stage(self, key, lot, name, failed, add=False):
        """AUD-02: one management stage of one lot. An unanswered order (AmbiguousOrder) still aborts the lot's management for
        this pass (the lot is now `pending`); any other failure is reported and the NEXT stages still run, so a rejected add
        or exit can never starve the trailing stop, breakeven or the other exits. A failed add is cooled down."""
        try:
            yield
        except AmbiguousOrder:
            raise
        except Exception as ex:
            failed.append(name)
            if add:
                lot['add_retry_at'] = time.time() + (ADD_RETRY_TRANSIENT_S if is_transient(ex) else ADD_RETRY_S)
                self.err(f"{lot['symbol']} [{lot['sleeve']}] {name} failed ({str(ex)[:160]}) - exits and the stop keep being managed; "
                         f"next try in {int(lot['add_retry_at'] - time.time())} s", key=f'add|{key}')
            else:
                self.err(f'manage {key} {name}: {ex}', key=f'manage|{key}|{name}')

    @staticmethod
    def _add_cooling(lot):
        return time.time() < (lot.get('add_retry_at') or 0)

    @staticmethod
    def _exec_of(o, requested, step):
        """AUD-03: how much of an order Binance REPORTS executed (exchange truth), capped at what was requested.
        A final status (FILLED / EXPIRED / CANCELED ...) with executedQty -> that quantity (0 = nothing executed).
        A non-final status (NEW / PARTIALLY_FILLED) -> None: unknown, handled exactly like a lost answer (pending).
        No status at all (simulated / replay answers): a reported quantity > 0 is used, otherwise the request (a real
        Binance RESULT answer always carries both, so this keeps only the simulated paths unchanged)."""
        st = str((o or {}).get('status') or '').upper()
        raw = (o or {}).get('executedQty')
        try: eq = float(raw) if raw not in (None, '') else None
        except (TypeError, ValueError): eq = None
        if st in ORDER_FINAL:
            if eq is None: return requested if st == 'FILLED' else None
            q = min(F.round_step(eq, step), requested)
            if q <= 0 < eq:                       # Cowork #28 F2: below one step - not tradable, booked as nothing (said so)
                log.warning(f'order executed only {eq} (< one step {step}): booked as nothing executed - that dust stays on '
                            'Binance below the minimum, without a bot stop')
            return q
        if st: return None
        if eq: return min(F.round_step(eq, step), requested)
        return requested

    def _unknown_answer(self, lot, kind, qty, px, why, post, o):
        """AUD-03: Binance answered but the order is not final yet: identical to a lost answer - the lot is `pending` with
        the order's client id (reconcile reads that order's final record first) and AmbiguousOrder aborts this pass."""
        cid = (o or {}).get('clientOrderId')
        lot['pending'] = dict(kind=kind, qty=qty, px=px, why=why, post=post or {}, t=time.time(), cid=cid)
        self.save_state()
        self.err(f"{lot['symbol']} {why} answered {(o or {}).get('status')} (not final) - waiting for Binance to confirm")
        raise AmbiguousOrder(f"{lot['symbol']} {kind} status {(o or {}).get('status')}", f'c:{cid}' if cid else None)

    def _after_fill_failed(self, lot, post, why, ex):
        """AUD-01 second layer: something failed AFTER Binance executed a close / add. Mark the event done exactly like
        _resolve_pending does: its own `post` bookkeeping (the same dict an unconfirmed order uses) is applied, and a
        requested `finish` (a full close) or a lot left at zero is finished once - so the event can never fire twice, no
        zero-quantity ghost lot is left, and a remaining lot's stop is re-placed next pass. The recovered state is saved at
        once (best effort, a save failure is reported but never masks the original error). The exception still propagates."""
        try:
            post = dict(post or {})
            fin = post.pop('finish', None)                          # same semantics as _resolve_pending
            lot.update(post)
            lot['stop_dirty'] = True
            self.err(f"{lot['symbol']} [{lot['sleeve']}] {why} done on Binance but local bookkeeping failed "
                     f"({type(ex).__name__}: {str(ex)[:120]}) - marked done, stop re-placed next pass")
        except Exception:
            fin = None
        key = next((k for k, l in self.state['lots'].items() if l is lot), None)
        if key is not None and (fin or (lot.get('qty') or 0) <= 0):
            try:
                self._finish(key, fin or why)             # pops the lot first: terminal bookkeeping cannot repeat
            except Exception as ex2:
                log.warning(f"{lot['symbol']} [{lot['sleeve']}] finish after a post-fill failure incomplete: {type(ex2).__name__}: {ex2}")
        try:                                              # durable at once: a restart must not resurrect a closed lot or
            self.save_state()                             # replay the event (the caller's own save is skipped by the raise)
        except Exception as ex3:                          # reported, never replaces the original post-fill exception
            try: self.err(f"state not saved after post-fill recovery ({type(ex3).__name__}: {str(ex3)[:120]}) - saved again next pass",
                          key='save|state')
            except Exception: pass

    def save_state(self):
        return self._save_safe('state', self.state)

    def _save_wal(self):
        """AUD-05 r2 write-ahead save (before a risk-adding order is sent): raises when the state is not durable - the caller
        must not send."""
        return self._save_safe('state', self.state, strict=True)

    # ------------------------------------------------------------ T05: fill telemetry (observe only)
    # One record per fill the bot itself sends: expected vs actual price, slippage in bps (+ = worse for us), requested
    # vs filled qty, wait, maker tries, fallback state. Order paths only BUILD a record (no I/O) and hand it to the
    # process-wide FillWriter for this file with a non-blocking put; that writer is the only thing that ever touches
    # fills.jsonl. Telemetry never raises into an order path, never waits on the disk there, never changes what is traded.
    FILL_KINDS = ('entry_market', 'entry_maker', 'entry_fallback', 'pyramid_add', 'safety_order', 'exit')

    def _fill_rec(self, kind, sym, side, buy, expected, actual, q_req, q_fill, t0=None, outcome=None, **extra):
        """Build one record (pure: no I/O). q_fill None/0 from the exchange = unknown, never invented as a full fill."""
        try:
            exp = float(expected) if expected else None
            act = float(actual) if actual else None
            slip = round((act - exp) / exp * 1e4 * (1 if buy else -1), 2) + 0.0 if exp and act else None
            q_req = float(q_req or 0)
            try: qf = float(q_fill) if q_fill not in (None, '') else None
            except (TypeError, ValueError): qf = None
            if outcome is None:
                if not qf or qf <= 0: qf, outcome = None, 'unknown'
                else: outcome = 'partial' if q_req and qf + 1e-12 < q_req else 'filled'
            rec = dict(time=now_utc().isoformat(timespec='seconds'), kind=kind, symbol=sym, side=side, buy=bool(buy),
                       expected=exp, actual=act, slip_bps=slip, qty_req=q_req, qty_fill=qf, outcome=outcome,
                       wait_s=round(max(0.0, time.time() - t0), 2) if t0 else None)
            rec.update({k: v for k, v in extra.items() if v is not None})
            return rec
        except Exception as e:
            log.warning(f'fill telemetry record not built ({e})'); return None

    def _fill_emit(self, rec):
        """Hand a finished record to the writer without ever waiting (a full queue or a closing writer drops it, counted)."""
        if not rec: return
        try: self._fillw.emit(rec)
        except Exception: pass

    def _fill(self, kind, sym, side, buy, expected, actual, q_req, q_fill, t0=None, **extra):
        rec = self._fill_rec(kind, sym, side, buy, expected, actual, q_req, q_fill, t0, **extra)
        self._fill_emit(rec)
        return rec

    def _fill_flush(self, timeout=2.0):
        """Wait (bounded) until the writer has handled every queued record. Never call from an order path."""
        return self._fillw.flush(timeout)

    def fill_summary(self):
        """Per-kind counts, average/worst slippage (bps) and average wait over the last FILL_WINDOW written records, the
        10 most recent, and the telemetry's own health."""
        return self._fillw.summary()

    # ------------------------------------------------------------ T05a: causal trade audit (observe only)
    # Every audit step builds an event in memory and hands it to the non-blocking writer (put_nowait): no disk or network
    # I/O in the mark loop or in an order path, no extra save_state(), and an audit failure is swallowed (and counted by the
    # writer) - it can never delay or alter fills, stops, closes, reconciliation or the normal history persistence.
    def _audit_emit(self, ev):
        try:
            if ev: self._auditw.emit(TA.clean(ev))
        except Exception as e: log.debug(f'audit event not queued ({e})')

    def _audit_start(self):
        """Engine start (not the mark loop): continue each open lot's tracking from the newest excursion checkpoint when it
        is ahead of state.json, open a restart tracking gap from the last known observation, and declare policies late
        (coverage 'partial') for lots opened before T05a. Bounded: one pass over the two audit files. Also records, once,
        when the audit first ran (trade_audit.jsonl.since): closed trades before it are 'legacy' in the coverage summary."""
        try:
            sp = self.F['audit'] + '.since'
            if os.path.exists(sp): self._audit_since = open(sp, encoding='utf-8').read().strip()[:40] or None
            else:
                self._audit_since = now_utc().isoformat(timespec='seconds')
                with open(sp, 'w', encoding='utf-8') as fh: fh.write(self._audit_since)
        except Exception as e: log.debug(f'audit start time not recorded ({e})')
        try:
            lots = self.state.get('lots') or {}
            if not lots: return
            try: self._auditw.flush(1.0)                 # a settings restart shares the writer: let queued checkpoints land first
            except Exception: pass
            cks = TA.load_checkpoints((self.F['audit'] + '.1', self.F['audit']), lots.keys())
            t = now_utc().isoformat(timespec='seconds')
            for k, l in lots.items():
                try:
                    if k in cks and TA.restore_checkpoint(l, cks[k]): log.info(f'{k}: audit tracking continued from checkpoint {cks[k].get("t")}')
                    elif k not in cks: TA.checkpoint_lost(l)     # its newest checkpoint rotated away: state.json tracking, flagged
                    if TA.mark_restart(l) is False: self._audit_restored.add(k)
                    if not isinstance(l.get('ap'), dict):
                        ex = l.get('ex') if isinstance(l.get('ex'), dict) else {}
                        d = TA.declare(l, t, late=True, why='lot open before T05a: policies declared at this restart', seq=(ex.get('n') or 0) - 0.5)
                        if d is not None:
                            l['ap'] = d
                            self._audit_emit(TA.coverage_event(t, k, l, 'open lot upgraded to T05a: partial coverage'))
                except Exception: pass
        except Exception as e:
            log.warning(f'audit start-up skipped ({e})')

    def _audit_ctx(self):
        """(missing, down_last) from T05b: samples are missing while the exchange-down incident is open or the trading
        client's outage circuit is in 'outage'; down_last = that incident's last failed check (open or closed)."""
        try:
            inc = (self.health.get('incidents') or {}).get('exchange-down') or {}
            missing = bool(inc.get('open')) or self.exchange_state().get('state') == 'outage'
            return missing, inc.get('last')
        except Exception:
            return False, None

    def _audit_fill(self, lot):
        try:
            key = next((k for k, v in self.state['lots'].items() if v is lot), None)
            for ev in TA.fill_events(lot, key, FEE_EST): self._audit_emit(ev)
        except Exception: pass
        try:                                                       # owner scope: regime known at an add (a dict read, not per mark)
            f = (lot.get('fills') or [None])[-1]
            if f and f[1] in TA.ENTRY_KINDS and f[1] != 'entry':
                TA.note_add(lot, f, TA.regime_at(self._audit_rg.get((lot['symbol'], lot.get('tf'))), f[0]))
        except Exception: pass

    def _audit_cand(self, sl, sym, side, sg, decision, reason=None):
        """A candidate funnel event for a signal that was taken / armed / placed as a maker order, a deferred terminal
        outcome (maker finalize, slot removed), or a RAW signal hidden by the slot's sides (side_masked; emitted only
        when the raw signal of this candle fired). Not-taken candidates otherwise come from miss()."""
        try:
            sid = sl['id'] if isinstance(sl, dict) else sl
            raw = self._sig_raw.get(f"{sid}|{sym}")
            if decision == 'side_masked':
                if not (raw and raw.get('time') == sg.get('time') and (raw.get('le') or raw.get('se'))): return
                side = 'LONG' if raw.get('le') else 'SHORT'
            elif raw and raw.get('time') != sg.get('time'): raw = None    # a later candle's raw signal is not this candidate's
            self._audit_emit(TA.funnel_event(now_utc().isoformat(timespec='seconds'), sid, sym, side, sg.get('time'), decision, reason, raw))
            self._audit_missed_short(sid, sym, side, sg.get('time'), decision, raw, reason)
        except Exception: pass

    def _audit_opened(self, sl, sym, side, sg):
        """After open_lot() returned True: 'taken' only if a lot exists now; a post-only maker order that is resting is
        'order_placed' (its terminal 'taken' / 'not_taken' comes from _maker_finalize)."""
        try:
            placed = f"ME|{sl['id']}|{sym}|{side}" in (self.state.get('resting_entries') or {})
            self._audit_cand(sl, sym, side, sg, 'order_placed' if placed else 'taken')
        except Exception: pass

    def _audit_hold(self, k, l, d, closing, ex, ex0):
        try:
            if d is None or not len(d): return
            row = d.iloc[-1]
            candle = dict(t=pd.Timestamp(row.t).isoformat(), h=float(row.h), l=float(row.l), c=float(row.c), tf_s=TF_SEC.get(l.get('tf')))
            act = ('close_exit_signal' if ex else 'close_time_exit') if closing else 'hold'
            now = now_utc().isoformat(timespec='seconds')
            rg = TA.regime_snapshot(l['symbol'], l.get('tf'), candle['t'], candle['tf_s'], row.get('c'), row.get('e20'), row.get('e50'), row.get('e200'))
            missing = self._audit_ctx()[0]
            self._audit_emit(TA.hold_eval(l, k, candle, now, act, exit_signal=ex0, runner_override=bool(ex0) != bool(ex),
                                          missing=missing, fee_rate=FEE_EST, regime=rg))
            if not missing and not closing: TA.regime_trigger(l, rg, now)    # predeclared regime exit: decision at this close
        except Exception: pass

    def _audit_regime_cache(self, tf, frames):
        """T05a owner scope (cycle path, never per mark): the trend state of the last CLOSED candle of every frame this
        cycle already holds in memory, plus BTCUSDT 4h from the candle cache when present (no fetch). Looked up - a dict
        read - when a lot is created or an add fills, so the snapshot is causal (its candle closed before that moment)."""
        try:
            bar = TF_SEC.get(tf)
            src = [((s, tf), d) for s, d in (frames or {}).items()]
            hit = self._kc.get(('BTCUSDT', '4h'))
            if hit is not None and not (tf == '4h' and 'BTCUSDT' in (frames or {})): src.append((('BTCUSDT', '4h'), hit[0]))
            for (s, tf_), d in src:
                try:
                    if d is None or not len(d): continue
                    row = d.iloc[-1]
                    snap = TA.regime_snapshot(s, tf_, pd.Timestamp(row['t']).isoformat(), TF_SEC.get(tf_) or bar, row.get('c'),
                                              row.get('e20'), row.get('e50'), row.get('e200'))
                    if snap: self._audit_rg[(s, tf_)] = snap
                except Exception: pass
        except Exception: pass

    def _audit_entry_ctx(self, sym, tf, t):
        try:
            b = self._audit_rg.get(('BTCUSDT', '4h')); basis = 'BTCUSDT 4h'
            if b is None: b = self._audit_rg.get(('BTCUSDT', tf)); basis = f'BTCUSDT {tf} (4h not cached)'
            return TA.entry_context(TA.regime_at(self._audit_rg.get((sym, tf)), t), TA.regime_at(b, t), basis)
        except Exception:
            return None

    def _audit_missed_short(self, sid, sym, side, candle, decision, raw, reason=None):
        """Count a missed short opportunity once per (slot, coin, candle) - memory only, bounded."""
        try:
            if raw and raw.get('time') is not None and str(raw.get('time')) != str(candle): raw = None   # another candle's raw signal
            why = TA.missed_short(decision, side, raw)
            if not why: return
            k = (sid, sym, str(candle))
            if k in self._audit_mso: return
            i = TA.reason_info(reason) if reason else {}
            self._audit_mso[k] = dict(sleeve=sid, symbol=sym, candle=str(candle), why=why,
                                      code=f"{i['stage']}/{i['code']}" if i.get('stage') else None)
            while len(self._audit_mso) > 600: self._audit_mso.pop(next(iter(self._audit_mso)))
        except Exception: pass

    def audit_summary(self):
        """T05a: attribution over the trade-audit window (the audit writer's last FILL_WINDOW valid records, rebuilt from
        trade_audit.jsonl(.1) at start; malformed lines are skipped and counted) plus the funnel histogram of the bounded
        missed list. The window holds slimmed records (TA.slim_record) and the attribution is cached until the window or
        the missed list changes, so /api/status polls stay cheap. Read-only and bounded; never raises."""
        try:
            w, ms, hs = self._auditw, self.missed, self.history
            last = ms[-1] if ms else None
            with w.lock: ctr, n, wl = dict(w.ctr), len(w.win), (w.win[-1] if w.win else None)
            key = (ctr['invalid_records'], n, id(wl), len(ms), id(last), last.get('logged') if isinstance(last, dict) else None,
                   len(hs), id(hs[-1]) if hs else None)
            c = getattr(self, '_audit_cache', None)
            if c is None or c[0] != key:                 # recomputed only when the window, missed or history changed
                with w.lock: win = list(w.win)
                a = TA.attribution(win + list(ms[-600:]))
                a['coverage'] = TA.coverage(hs, win, getattr(self, '_audit_since', None))   # legacy / rotated / missing
                a['segments'] = TA.segments(win)                                       # owner scope: segments, flags, samples
                c = self._audit_cache = (key, a)
            out = dict(c[1])
            out['missed_short'] = TA.missed_short_summary(list(getattr(self, '_audit_mso', {}).values()), ms)
            out['telemetry'] = dict(ctr, window=FILL_WINDOW, in_window=n, queued=w.q.qsize())
            return out
        except Exception as e:
            return dict(error=f'audit summary unavailable ({type(e).__name__})')

    @staticmethod
    def _fb_confirmed(lo):
        """The fallback order's own fill record proves an executed quantity (filled or partial)."""
        rec = (lo or {}).get('rec') or {}
        return rec.get('outcome') in ('filled', 'partial') and bool(rec.get('qty_fill'))

    # ------------------------------------------------------------ order helpers
    def err(self, msg, key=None):
        """Recent Activity entry. T05b: a repeat of an OPEN incident (same key; default = the exact message) updates that
        one entry in place (latest text, count, first time) instead of adding a new line. Known floods pass a key."""
        with INCIDENT_LOCK: self._err(scrub(msg), key)

    def _err(self, msg, key):
        keyed = key is not None; key = key or msg[:200]; now = now_utc().isoformat(timespec='seconds')
        incs = self.health.setdefault('incidents', {})
        self._sweep_incidents()
        inc = incs.get(key)
        if inc and inc['open'] and (now_utc() - datetime.fromisoformat(inc['last'])).total_seconds() > INCIDENT_IDLE_S:
            inc['open'] = False                                       # a repeat after a long quiet gap is a new incident
        if inc and inc['open']:
            if not any(x is inc['entry'] for x in self.health['errors']):
                self.health['errors'].append(inc['entry'])           # its line scrolled out: show it again (once)
            inc['count'] += 1; inc['last'] = now; inc['msg'] = msg[:200]
            inc['entry'][0] = now; inc['entry'][1] = f"{msg[:150]} (x{inc['count']} since {inc['first'][11:19]} UTC)"
            if inc['count'] % 20 == 0 or msg != inc.get('logged'):
                log.warning(f"{msg} (x{inc['count']})"); inc['logged'] = msg
            return
        entry = [now, msg[:200]]
        self.health['errors'].append(entry)
        log.warning(msg)
        if len(incs) >= INCIDENT_MAX:                                 # bounded: oldest first, closed before open
            for k in sorted(incs, key=lambda k: (incs[k]['open'], incs[k]['last']))[:len(incs) - INCIDENT_MAX + 1]:
                incs.pop(k, None)
        incs[key] = dict(key=key, msg=msg[:200], first=now, last=now, count=1, open=True, entry=entry, logged=msg, keyed=keyed)

    def _sweep_incidents(self):
        """T05b: a generic (unkeyed) incident with no repeat for INCIDENT_IDLE_S is over: close it, so a one-off error
        is not reported as open for days. Keyed incidents (exchange-down, open-orders|SYM) stay open until resolve()."""
        cut = now_utc().timestamp() - INCIDENT_IDLE_S
        with INCIDENT_LOCK:
            for inc in list((self.health.get('incidents') or {}).values()):
                try:
                    if inc.get('open') and not inc.get('keyed') and datetime.fromisoformat(inc['last']).timestamp() < cut:
                        inc['open'] = False
                except Exception:
                    pass

    def exchange_down_incident(self, where):
        """A step failed only because Binance is unreachable: one shared exchange-down incident (closed by the recovery
        reconcile), no guessing."""
        self.err(f'{where}: {EXCHANGE_DOWN}', key='exchange-down')

    def resolve(self, key, note):
        """Close an open incident with ONE recovery entry (count, first, duration)."""
        with INCIDENT_LOCK:
            inc = self.health.get('incidents', {}).get(key)
            if not inc or not inc['open']: return False
            inc['open'] = False
            t0 = datetime.fromisoformat(inc['first']); dur = int((now_utc() - t0).total_seconds())
            line = f"{note} after {dur // 60}m{dur % 60:02d}s ({inc['count']} failed checks since {inc['first'][11:19]} UTC)"
            self.health['errors'].append([now_utc().isoformat(timespec='seconds'), line[:200]])
        log.info(line)
        return True

    def _positions_critical(self):
        """Positions for sizing an order: never skipped by the outage circuit (a real attempt, like before T05b)."""
        try: return self.trade.positions(critical=True)
        except TypeError: return self.trade.positions()

    def exchange_state(self):
        """T05b: the trading client's shared outage circuit ('ok' when the client has none, e.g. a test fake)."""
        h = getattr(self.trade, '__dict__', {}).get('_health') if self.trade is not None else None
        return h.snapshot() if h is not None else dict(state='ok')

    def _exchange_down(self, e):
        """A read failed because Binance is unreachable (transient), not because of a bad request."""
        return is_transient(e) or type(e).__module__.startswith('requests')

    def _cancel_or_park(self, sym, tag):
        """Cancel a stop; if Binance cannot be reached, remember it and retry later (never leave a stale stop behind).
        Returns True when Binance confirmed the cancel (or that it was already gone), False when it was only parked -
        parking is bookkeeping, not proof that the stop is gone (AUD-04 r3). Existing callers may ignore the result."""
        if self.dry or not tag: return True
        try:
            self.trade.cancel(sym, tag); return True
        except Exception as e:
            orph = self.state.setdefault('orphans', [])
            if [sym, tag] not in orph: orph.append([sym, tag])
            self.err(f'cancel stop {sym} {tag} failed ({e}) - will retry')
            return False

    def _replace_stop(self, lot, stop=None, owner=False):
        """Place the stop at `stop` (or the current lot stop, e.g. after a size change) FIRST, then cancel the old one.
        The lot's recorded stop only changes once Binance has accepted the new order. Returns True on success.
        AUD-04: while the lot is in owner_check (a stop placed outside the bot holds the position, or the bot's own stop
        cannot be read) NO bot stop is placed: the update is owed (stop_dirty) and done once the verifier has proven the
        state (the other stop gone -> restore), or when the owner uses Move stop (owner=True): the bot's stop is placed
        first, then the other stop is cancelled - never two stops."""
        if self.dry:
            if stop is not None: lot['stop'] = stop
            return True
        if lot.get('stop_miss_why') == 'owner_check' and not owner:
            lot['stop_dirty'] = True
            key = next((k for k, l in self.state['lots'].items() if l is lot), lot['symbol'])
            if not (self.health.get('incidents', {}).get(f'stop-missing|{key}') or {}).get('open'):
                self.err(f"{lot['symbol']} {lot['side']} [{lot['sleeve']}]: stop update held - {STOP_MISSING_NOTES['owner_check']}",
                         key=f'stop-missing|{key}')
            log.info(f"{lot['symbol']} [{lot['sleeve']}] stop update deferred: owner check pending (no second stop placed)")
            return False
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
        lot['stop_confirmed_t'] = lot['stop_placed_t'] = self.clock()      # AUD-04: Binance accepted it (id known) - re-verified
        lot['stop_miss'] = 0; lot.pop('stop_miss_t', None); lot.pop('stop_miss_why', None)   # on the open-order list shortly,
        self._stopv_due(lot['symbol'], max(lot['stop_placed_t'] + STOP_SETTLE_S,               # floored so a stop trailing
                                           self._stopv_last.get(lot['symbol'], -1e18) + STOP_ACTION_MIN_S))  # every pass is
        #   not re-read on every pass
        if old and old != new: self._cancel_or_park(lot['symbol'], old)
        fx = lot.pop('stop_foreign', None)                 # AUD-04: Move stop took over from a stop placed outside the bot
        if fx and fx not in (old, new): self._cancel_or_park(lot['symbol'], fx)
        return True

    def _market_close(self, lot, qty, why, mark=None, post=None):
        """Close qty at market. If Binance's answer is lost (AmbiguousOrder) the lot is marked 'pending' and reconcile()
        decides from the real position whether it filled - so a partial take-profit can never fire twice."""
        sym, r = lot['symbol'], self.rules[lot['symbol']]
        qty = self._rd(qty, r['step'])
        if qty <= 0: return 0.0
        px = mark or lot['avg']
        if not self.dry:
            lot['last_order_t'] = t0 = time.time()
            try:
                o = self.trade.close(sym, lot['side'], self._fmt(qty, r['step']))
            except AmbiguousOrder as e:
                lot['pending'] = dict(kind='close', qty=qty, px=px, why=why, post=post or {}, t=time.time(), cid=_cid_of(e.tag))
                self.save_state(); self.err(f'{sym} close unconfirmed ({e}) - waiting for Binance position to confirm'); raise
            got = self._exec_of(o, qty, r['step'])                        # AUD-03: book what Binance executed
            act = float(o.get('avgPrice') or 0) or None
            self._fill('exit', sym, lot['side'], lot['side'] == 'SHORT', mark, act, qty, o.get('executedQty'), t0, reason=why,
                       **({'outcome': 'unfilled'} if got == 0 else {}))
            if got is None: self._unknown_answer(lot, 'close', qty, px, why, post, o)
            if got <= 0:
                raise UnfilledOrder(f"{sym} {why}: Binance {o.get('status')} - nothing executed, the position is unchanged")
            if got < qty and (post or {}).get('finish'):
                post = {k: v for k, v in post.items() if k != 'finish'}       # a partial full-close finishes nothing
                short = qty - got
            else:
                short = 0.0
            qty, px = got, act or px
        else:
            short = 0.0
        try:
            pnl = self._apply_close(lot, qty, px, why)
        except Exception as ex:
            self._after_fill_failed(lot, post, why, ex); raise
        if short > 0 and lot['qty'] > 0:
            raise PartialClose(f"{sym} [{lot['sleeve']}] {why} executed {qty} of {qty + short} - {lot['qty']} kept with its stop, "
                               "the rest is closed on a later pass")
        return pnl

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
        self._audit_fill(lot)                                      # T05a: cohort / runner events (memory + queue only)
        log.info(f"{why.upper()} {sym} {lot['side']} [{lot['sleeve']}] {qty} @ {px} pnl {pnl:+.2f}")
        return pnl

    def _resolve_pending(self, key, have, expected, tol, dust=0.0):
        """A close/add whose answer was lost: decide from the exchange position whether it filled. Returns True if resolved.
        The position may also carry dust the lots do not own (reconcile tolerates it): a match within that dust counts only
        when the other outcome does not match too - otherwise it keeps waiting and the 5-minute resync decides."""
        l = self.state['lots'][key]; pd_ = l['pending']; age = time.time() - pd_.get('t', 0)
        fr = self._order_result(l['symbol'], pd_.get('cid'), pd_['qty'])
        if fr['state'] == 'final':                                       # AUD-03: Binance's own final answer decides
            got, px = fr['qty'], fr['px'] or pd_['px']
            del l['pending']
            if got <= 0:
                log.info(f"{l['symbol']} [{l['sleeve']}] unconfirmed {pd_['kind']} did not execute (order record) - will retry")
                return True
            post = dict(pd_.get('post') or {})
            fin = post.pop('finish', None)
            if got < pd_['qty']: fin = None                              # a partial full-close finishes nothing
            if pd_['kind'] == 'close': self._apply_close(l, got, px, pd_['why'])
            else: self._apply_add(l, got, px, pd_['why'])
            l.update(post)
            if 'dca' in l.get('mgmt', {}) and pd_['kind'] == 'add' and l.get('tp') is not None:
                sd = 1 if l['side'] == 'LONG' else -1
                l['tp'] = l['avg'] + sd * l['mgmt']['dca']['tp_atr'] * l['atr0']
            log.info(f"{l['symbol']} [{l['sleeve']}] unconfirmed {pd_['kind']} confirmed from its order record: {got} of {pd_['qty']} @ {px}")
            if fin or l['qty'] <= 0: self._finish(key, fin or pd_['why'])
            else: self._replace_stop(l)
            return True
        if fr['state'] != 'nosource':                                    # AUD-03 r1: a KNOWN order is decided only by its record
            if fr['state'] == 'notfound' and age > 20:                   # Binance has no such order: it never executed
                del l['pending']; log.info(f"{l['symbol']} [{l['sleeve']}] unconfirmed {pd_['kind']} never reached Binance - will retry")
                return True
            if age > 300:                                                # no record within the evidence window: timed resync
                del l['pending']
                self.err(f"{l['symbol']} [{l['sleeve']}] could not read the order record of a {pd_['kind']} for 5 min "
                         f"({fr['state']}) - resyncing to the exchange")
                return False
            return None                                                  # wait: the position alone could pick the wrong cause
        # a close of the whole position includes that dust (close_lot), and a position never goes below zero
        target = max(0.0, expected - pd_['qty']) if pd_['kind'] == 'close' else expected + pd_['qty']
        near = lambda d: -tol <= d <= tol                                # whole step multiples: only float noise is tolerated
        near_dust = lambda d: near(d) or 0 < d < dust                   # ... plus dust left on the exchange
        filled = near(have - target) or (near_dust(have - target) and not near_dust(have - expected))
        never = not filled and (near(have - expected) or (near_dust(have - expected) and not near_dust(have - target)))
        if filled:                                                       # it filled
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
        if never and age > 20:                                           # it never filled
            del l['pending']; log.info(f"{l['symbol']} [{l['sleeve']}] unconfirmed {pd_['kind']} did not fill - will retry"); return True
        if age > 300:
            del l['pending']; self.err(f"{l['symbol']} [{l['sleeve']}] could not confirm {pd_['kind']} after 5 min - resyncing to the exchange"); return False
        return None

    @staticmethod
    def _fill_px(o):
        """The average price Binance reports for an order (avgPrice, else cumQuote / executedQty), or None."""
        try:
            px = float((o or {}).get('avgPrice') or 0)
            if px > 0: return px
            q, cq = float((o or {}).get('executedQty') or 0), float((o or {}).get('cumQuote') or 0)
            return cq / q if q > 0 and cq > 0 else None
        except (TypeError, ValueError):
            return None

    def _order_result(self, sym, cid, requested):
        """AUD-03 (Codex r1): THE fill-result boundary for an order known by its client id. Returns
        dict(state, qty, px, status, cid) with state:
          'final'      - Binance's final record: qty = executed (capped at the request; 0 = nothing), px = its average
          'pending'    - the order exists but is not final yet (NEW / PARTIALLY_FILLED)
          'notfound'   - Binance has no such order (never reached it)
          'unreadable' - the lookup failed (timeout, outage, error)
          'nosource'   - no client id, or this exchange adapter cannot read order records (simulations)
        Only 'final' is order truth; callers must not infer an outcome for a known id from anything else."""
        res = dict(state='nosource', qty=None, px=None, status=None, cid=cid)
        getter = getattr(self.trade, 'get_order', None)
        if not cid or self.dry or getter is None: return res
        try:
            o = getter(sym, cid)
        except Exception:
            res['state'] = 'unreadable'; return res
        if not o:
            res['state'] = 'notfound'; return res
        st = str(o.get('status') or '').upper(); res['status'] = st
        if st not in ORDER_FINAL:
            res['state'] = 'pending'; return res
        q = self._exec_of(o, requested, self.rules[sym]['step'])
        if q is None:
            res['state'] = 'pending'; return res
        res.update(state='final', qty=q, px=self._fill_px(o) if q > 0 else None)
        return res

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
        """Market-close first; the protective stop is only cancelled after the close succeeded (a failed close keeps it).
        AUD-02: refused while an earlier order of this lot is unconfirmed (`pending`): if that order filled, closing the
        recorded quantity would take size from a sibling lot or a resting maker fill. The size sent is this lot's own
        quantity; what the exchange holds beyond it is swept along ONLY when it is dust (not tradable on its own) - never
        another slot's resting-maker fill or an untracked position."""
        lot = self.state['lots'][key]
        if lot.get('pending'):
            raise RuntimeError(f"{lot['symbol']} [{lot['sleeve']}] close deferred: an earlier order on this lot is not confirmed yet "
                               "- it is resolved on the next management pass, then the close can be retried")
        qty = lot['qty']
        others = [k for k, l in self.state['lots'].items() if k != key and l['symbol'] == lot['symbol'] and l['side'] == lot['side']]
        if not others and not self.dry:            # last lot on this side: no dust may be left behind
            try: held = self._positions_critical().get((lot['symbol'], lot['side']))
            except Exception: held = None          # unreadable: send this lot's own quantity (only the READ is guarded)
            if held:
                extra = held - qty
                if extra <= 0 or (not self._entry_claims(lot['symbol'], lot['side'])
                                  and F.leaves_dust(extra, mark or lot['avg'], self.rules.get(lot['symbol']))): qty = held
        try:
            self._market_close(lot, qty, why, mark, post={'finish': why})    # raises on failure -> lot and its stop stay as they were
        except PartialClose:
            lot['stop_dirty'] = True                       # AUD-03: the rest stays a lot; its stop is resized to what is left
            try: self._replace_stop(lot)
            except Exception as ex: log.warning(f"{lot['symbol']} stop resize after a partial close failed ({ex}) - retried next pass")
            self.save_state(); raise
        self._finish(key, why)
        self.save_state()

    def _entry_claims(self, sym, side):
        """AUD-02 (Codex r1): a local entry on this coin/side may own exchange size that no lot records yet - a resting maker
        entry (its partial fills become its own lot) or a pending/trailing entry. While one exists, size beyond a closing
        lot is never swept, not even when it is dust."""
        recs = (list((self.state.get('resting_entries') or {}).values()) + list((self.state.get('pending_entries') or {}).values())
                + list((self.state.get('unconfirmed_entries') or {}).values()))
        return any(r.get('symbol') == sym and r.get('side') == side for r in recs if isinstance(r, dict))

    def _partial_zero(self, lot, name, q):
        """AUD-02 / C25 (Codex: option b). True when a PARTIAL take-profit floors to zero tradable quantity (a lot too small
        to split): no order is sent and NOTHING is marked done - tp1 / basket part / ladder level stay open and are
        evaluated again on later passes (e.g. after an add made the lot splittable), so no breakeven move, runner promotion
        or DCA completion is ever triggered by a take-profit that never happened. Reported once per lot and stage.
        The backtester applies the same rule (backtest.run: part_zero)."""
        r = self.rules.get(lot['symbol'])
        if not r or self._rd(q, r['step']) > 0: return False     # no rules (Cowork #27 note): the order path decides, never a KeyError
        seen = lot.setdefault('zero_partials', [])
        if name not in seen:
            seen.append(name)
            log.info(f"{lot['symbol']} [{lot['sleeve']}] {name} reached but {lot['qty']} cannot be split (part floors to 0) - "
                     "nothing sent, nothing marked done; checked again on later passes")
        return True

    def _finish(self, key, why):
        """Lot fully closed: write a trade-history record and forget the lot."""
        lot = self.state['lots'].pop(key, None)
        if not lot: return
        self.resolve(f'stop-missing|{key}', f"{lot['symbol']} {lot['side']} [{lot['sleeve']}]: trade closed ({why}) - stop check ended")
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
        try:                                             # T05a: causal trade audit record (observe only, non-blocking writer)
            au = TA.close_record(lot, net, fee_rate=FEE_EST, path=TA.recorded_path(lot))
            if au: au.update(kind='trade_audit', id=key, exit_reason=why, closed=rec['closed'], t=rec['closed']); self._audit_emit(au)
        except Exception as e: log.warning(f'trade audit not recorded ({e})')
        self.history.append(rec); self.history = self.history[-3000:]
        self._save_aux('history', self.history)
        self.notify(f"{'✅' if net > 0 else '❌'} CLOSED {lot['side']} {lot['symbol']} [{lot['sleeve']}] {why} · PnL {net:+.2f} USDT ({rec['r']}R) · {rec['hours']}h")

    def miss(self, sl, sym, side, sg, reason, kind=None):
        """Record a signal that was not taken (kind None) or a risk-rule warning on a trade that WAS taken (kind 'warning')."""
        k = (sl['id'], sym, sg['time'], kind)
        if any((m['sleeve'], m['symbol'], m['candle'], m.get('kind')) == k for m in self.missed[-200:]): return
        reason = scrub(str(reason))                               # AUD-05 r2: exchange error texts can carry signed URLs
        rec = dict(candle=sg['time'], logged=now_utc().isoformat(timespec='seconds'), sleeve=sl['id'], strategy=sl.get('name', sl['id']),
                   symbol=sym, side=side, price=sg['close'], reason=reason)
        if kind: rec['kind'] = kind
        try: rec.update(TA.reason_info(reason))                   # T05a funnel: stage/code(/detail), the text is unchanged
        except Exception: pass                                   # audit never raises into a trading path
        try: self._audit_emit(TA.funnel_event(rec['logged'], sl['id'], sym, side, sg['time'], 'warning' if kind == 'warning' else
                                              (kind or 'not_taken'), reason, self._sig_raw.get(f"{sl['id']}|{sym}")))
        except Exception: pass
        if not kind: self._audit_missed_short(sl['id'], sym, side, sg['time'], 'not_taken', self._sig_raw.get(f"{sl['id']}|{sym}"), reason)
        self.missed.append(rec)
        self.missed = self.missed[-600:]
        self._save_aux('missed', self.missed)

    def _add_qty(self, lot, q, px, why, post=None, cid=None):
        r = self.rules[lot['symbol']]
        d = F.size_check(q, q, px, r)                    # BT02: the shared check (floor to the step, minQty, minNotional)
        if not d['ok']: return False
        q = d['qty']
        if not self.dry:
            pb = self.persist_block()
            if pb: raise RuntimeError(f"{lot['symbol']} {why} not sent: {pb}")
            # AUD-05 r2 write-ahead: the add is recorded durably as this lot's pending order (with its client id) BEFORE it
            # is sent; a crash / lost answer after the send is settled from its order record (_resolve_pending)
            cid = cid or new_cid()
            lot['pending'] = dict(kind='add', qty=q, px=px, why=why, post=post or {}, t=time.time(), cid=cid)
            try:
                self._save_wal()
            except Exception as ex:
                lot.pop('pending', None)
                raise RuntimeError(f"{lot['symbol']} {why} not sent - its write-ahead record could not be saved "
                                   f"({type(ex).__name__})") from None
            lot['last_order_t'] = t0 = time.time()
            try:
                o = self._send(self.trade.open, lot['symbol'], lot['side'], self._fmt(q, r['step']), cid=cid)
            except AmbiguousOrder as e:
                lot['pending'] = dict(kind='add', qty=q, px=px, why=why, post=post or {}, t=time.time(), cid=_cid_of(e.tag) or cid)
                self.save_state(); self.err(f"{lot['symbol']} {why} unconfirmed ({e}) - waiting for Binance position to confirm"); raise
            except Exception:
                lot.pop('pending', None); self.save_state(); raise          # refused by Binance: nothing executed
            lot.pop('pending', None)
            got = self._exec_of(o, q, r['step'])                          # AUD-03: book what Binance executed
            if got is None: self._unknown_answer(lot, 'add', q, px, why, post, o)
            if got <= 0:
                self._fill(why if why in self.FILL_KINDS else 'pyramid_add', lot['symbol'], lot['side'], lot['side'] == 'LONG',
                           px, None, q, o.get('executedQty'), t0, reason=why, outcome='unfilled')
                raise UnfilledOrder(f"{lot['symbol']} {why}: Binance {o.get('status')} - nothing executed, the lot is unchanged")
            q = got
            act = float(o.get('avgPrice') or 0) or None
            self._last_order = dict(rec=self._fill(why if why in self.FILL_KINDS else 'pyramid_add', lot['symbol'], lot['side'], lot['side'] == 'LONG',
                                    px, act, q, o.get('executedQty'), t0, reason=why, fallback_order=(why == 'entry_fallback') or None))
            px = act or px
        try:
            self._apply_add(lot, q, px, why)
        except Exception as ex:
            self._after_fill_failed(lot, post, why, ex); raise
        return True

    def _apply_add(self, lot, q, px, why, fee=None):
        r = self.rules[lot['symbol']]
        lot['avg'] = (lot['avg'] * lot['qty'] + px * q) / (lot['qty'] + q)
        lot['qty'] = self._rd(lot['qty'] + q, r['step'])
        lot['qty_max'] = max(lot.get('qty_max', 0), lot['qty'])
        lot.setdefault('fills', []).append([now_utc().isoformat(timespec='seconds'), why, q, px])
        lot['fees'] = lot.get('fees', 0.0) + q * px * (FEE_EST if fee is None else fee)
        self.log_trade(time=now_utc().isoformat(timespec='seconds'), event=why, sleeve=lot['sleeve'], symbol=lot['symbol'],
                       side=lot['side'], qty=q, price=px, stop=lot['stop'], equity=round(self.last_eq or 0, 2))
        self._audit_fill(lot)                                      # T05a: cohort event (memory + queue only)
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
            if self.state.get('resting_entries'):                  # maker entry orders working on the book
                for rk in list(self.state['resting_entries']):
                    try: changed = self._maker_poll(rk, marks) or changed
                    except Exception as e: self.err(f'maker entry {rk}: {e}')
            try:                                   # drop lots whose exchange stop already filled before touching anything
                n0 = len(self.state['lots'])
                self.reconcile(self.last_eq or 0)
                changed = changed or len(self.state['lots']) != n0
            except Exception as e:
                if self._exchange_down(e):                         # T05b: one incident, plain words; nothing guessed
                    self._manage_failed(EXCHANGE_DOWN, key='exchange-down')
                else:
                    self._manage_failed(f'reconcile: {e}')
                return
            self._after_confirmed()
            changed = self._verify_stage() or changed               # AUD-04: its own stage, budgeted; never blocks the rest
            ok = True
            if self.state.get('pending_entries'):
                try: changed = self._trail_entries(marks) or changed
                except Exception as e: ok = False; self.err(f'trailing entries: {e}')
            if self.risk_rules_cfg()['btc_breaker']['mode'] == 'enforce':
                try: self._breaker()                              # trips the pause (and the optional tighten) without waiting for a signal
                except Exception as e: log.debug(f'breaker: {e}')
            aud = self._audit_ctx()                                # T05a: T05b outage state -> missing samples
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
                if lot.get('restored_from_bak') and lot.get('restored_mismatch'): continue   # AUD-05 r1: stale copy - owner review
                try:                                           # T05a: excursions, path, state, online policies (observe only)
                    t_ = now_utc().isoformat(timespec='seconds')   # memory only: NO save_state / disk / network here; the
                    rs = getattr(self, '_audit_restored', set())    # tracking state rides on the normal save cadence and a
                    TA.observe(lot, m, t_, FEE_EST, restored=key in rs, missing=aud[0], down_last=aud[1])
                    if isinstance(lot.get('ex'), dict): rs.discard(key)   # only once observe() really started tracking
                    self._audit_emit(TA.checkpoint(lot, key, t_))  # bounded checkpoint event goes to the writer queue
                except Exception: pass                         # audit never raises into a trading path
                failed = []                                    # AUD-02: stages of this lot that failed this pass
                try:
                    if lot.get('force_close'):                 # a stop update found price already through the stop
                        self.close_lot(key, 'stop_crossed', m); changed = True; continue
                    if lot.get('restored_from_bak') and not lot.get('stop_id') and lot['symbol'] not in self._stopv_last:
                        pass                                   # AUD-05 r1: Binance may hold its stop - the verifier reads (and
                    elif lot.get('stop_dirty') or not lot.get('stop_id'):   # adopts) first; protection missing -> retry
                        if self._replace_stop(lot): changed = True; log.info(f"{lot['symbol']} [{lot['sleeve']}] stop restored")
                    if 'dca' in g and lot.get('levels'):
                        with self._stage(key, lot, 'safety_order', failed, add=True):
                            while (lot['dca'] < len(lot['levels']) and sd * (lot['levels'][lot['dca']] - m) >= 0
                                   and not self._add_cooling(lot)):
                                q = lot['q0'] * lot['w'][lot['dca']] * (self._breaker_add_mult(lot) or 1.0)   # 0 -> the gate blocks it
                                if self._add_gate(lot, q, m, 'safety_order') or not self._add_qty(lot, q, m, 'safety_order', post={'dca': lot['dca'] + 1}): break
                                lot['dca'] += 1
                                lot['tp'] = lot['avg'] + sd * g['dca']['tp_atr'] * lot['atr0']
                                self._replace_stop(lot); changed = True
                    if 'dca' in g and lot.get('levels'):
                      with self._stage(key, lot, 'basket_tp', failed):
                        if lot.get('tp') is not None and ge(lot['tp']):
                              run = g.get('runner')
                              if run and run.get('dca_frac', 1) < 1:      # runner: bank part, keep the rest at breakeven
                                if not self._partial_zero(lot, 'basket_tp_part', lot['qty'] * run['dca_frac']):   # C25
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
                    if key not in self.state['lots']: continue   # AUD-02: finished by a stage (or its recovery)
                    if 'pyramid' in g and lot['adds'] < g['pyramid']['n'] and ge(lot['next_add']) and not self._add_cooling(lot):
                      with self._stage(key, lot, 'pyramid_add', failed, add=True):
                        if not self._add_gate(lot, lot['q0'] * g['pyramid']['frac'], m, 'pyramid_add'):
                            if self._add_qty(lot, lot['q0'] * g['pyramid']['frac'], m, 'pyramid_add',
                                             post={'adds': lot['adds'] + 1, 'next_add': lot['next_add'] + sd * g['pyramid']['step_r'] * lot['R']}):
                                lot['adds'] += 1; lot['next_add'] += sd * g['pyramid']['step_r'] * lot['R']
                                self._replace_stop(lot); changed = True
                    if key not in self.state['lots']: continue   # AUD-02: finished by a stage (or its recovery)
                    if (g.get('tp1_r') and not lot['tp1'] and ge(lot['e0'] + sd * g['tp1_r'] * lot['R'])
                            and not self._partial_zero(lot, 'take_profit_1', lot['qty'] * g.get('tp1_frac', 0.5))):   # C25
                      with self._stage(key, lot, 'take_profit_1', failed):
                        self._market_close(lot, lot['qty'] * g.get('tp1_frac', 0.5), 'take_profit_1', m, post={'tp1': True})
                        lot['tp1'] = True; changed = True
                        if lot['qty'] <= 0: self._finish(key, 'take_profit_1'); continue
                        self._replace_stop(lot)
                    if key not in self.state['lots']: continue   # AUD-02: finished by a stage (or its recovery)
                    tps = S.norm_tps(g.get('tps'))
                    if tps:                                    # take-profit ladder: each level fires once (fraction of the full size)
                      with self._stage(key, lot, 'take_profit_ladder', failed):
                          done, fired = list(lot.get('tps_done', [])), False
                          rr = self.rules.get(lot['symbol'], dict(min_qty=0, min_notional=0))
                          for k_, (r_, f_) in enumerate(tps):
                              if k_ in done: continue
                              if not ge(lot['e0'] + sd * r_ * lot['R']): break
                              q = min(lot['qty'], lot.get('qty_max', lot['q0']) * f_)
                              rest = lot['qty'] - q
                              if F.leaves_dust(rest, m, rr): q = lot['qty']   # never leave dust (shared with the backtest)
                              if self._partial_zero(lot, 'take_profit_ladder', q): break   # C25: the level stays open
                              done.append(k_)
                              self._market_close(lot, q, 'take_profit_ladder', m, post={'tps_done': list(done)})
                              lot['tps_done'] = list(done); fired = changed = True
                              if lot['qty'] <= 0: break
                          if lot['qty'] <= 0: self._finish(key, 'take_profit_ladder'); continue
                          if fired: self._replace_stop(lot)
                    if g.get('tp_r') and not g.get('runner') and not g.get('ttp') and ge(lot['e0'] + sd * g['tp_r'] * lot['R']):
                      with self._stage(key, lot, 'take_profit', failed):
                          self.close_lot(key, 'take_profit', m); changed = True; continue
                    if key not in self.state['lots']: continue   # AUD-02: finished by a stage (or its recovery)
                    lot['best'] = max(lot['best'], m) if sd == 1 else min(lot['best'], m)
                    # every stop tightening goes through one place: the best candidate that is still on the losing side of price
                    cands = []
                    if g.get('be_r') and sd * (lot['best'] - (lot['e0'] + sd * g['be_r'] * lot['R'])) >= 0:
                        cands.append(lot['avg'])
                    if g.get('trail_atr'):
                        cands.append(lot['best'] - sd * g['trail_atr'] * lot['atr_now'])
                    ttp = g.get('ttp')
                    if ttp and lot['R'] > 0 and sd * (lot['best'] - lot['e0']) / lot['R'] >= ttp.get('at_r', 2.0):
                        cands.append(lot['best'] * (1 - sd * ttp.get('dev_pct', 3.0) / 100))   # trailing take-profit
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
                            if lot.get('stop_miss_why') == 'owner_check':   # AUD-04: the owner must check this coin's
                                pass                                        # stop on Binance - no automatic second stop
                            elif self._replace_stop(lot, tgt):
                                changed = True
                                if run: log.info(f"RUNNER {lot['symbol']} [{lot['sleeve']}] stop raised to {lot['stop']}")
                except Exception as e:
                    ok = False; self.err(f'manage {key}: {e}')
                if failed: ok = False                          # AUD-02: a failed stage is still a failed pass (alerting)
            try: changed = self.grids.on_marks(marks) or changed
            except Exception as e: ok = False; self.err(f'grid: {e}')
            if ok:
                self.health['last_manage_ok'] = now_utc().isoformat(timespec='seconds'); self.health['manage_fail_streak'] = 0
                self.health['alerted'] = False
            else:
                self._manage_failed('see errors')
            if changed: self.save_state()

    def _after_confirmed(self):
        """T05b: positions were just re-read and reconciled. On the first success after an outage, close the incident with
        one recovery entry (the reconcile that just ran IS the recovery reconcile; nothing older is acted on)."""
        h = getattr(self.trade, '__dict__', {}).get('_health')
        if h is not None and h.state == 'outage': return    # T05b final: a later read in this pass re-opened it - not recovered
        if self.health.get('incidents', {}).get('exchange-down', {}).get('open') or (h is not None and h.recovered):
            waiting = self._stops_reconfirmed()
            if waiting is None: return                           # protective orders not readable yet: stays open, next pass
            note = 'Binance answering again - positions re-read and reconciled, stops re-confirmed'
            if waiting: note += f' ({waiting} lot(s) still waiting for a stop - being placed)'
            self.resolve('exchange-down', note)
            if h is not None: h.recovered = False

    def _stops_reconfirmed(self):
        """T05b (canary finding): recovery is only declared once the protective orders were re-read too, not just the
        positions. Reads open orders once per held symbol. Any read failure -> None (nothing changed, retried next pass;
        a non-transient failure gets its own open-orders|SYM incident). A recorded stop id that is not among the open
        orders is reported (keyed stop-unseen, closed when seen again); nothing is changed and no order is sent here: the
        normal reconcile decides fills vs resize from the position size. Returns the number of lots that have no
        confirmed stop yet (being placed by manage), so the recovery line never over-claims."""
        held = [l for l in self.state['lots'].values() if not l.get('pending')]
        waiting = sum(1 for l in held if not l.get('stop_id') or l.get('stop_dirty'))
        if self.dry: return waiting
        lots = [l for l in held if l.get('stop_id')]
        for sym in sorted({l['symbol'] for l in lots}):
            try: tags = self.trade.open_stop_tags(sym)
            except Exception as ex:
                if not self._exchange_down(ex):
                    self.err(f'open orders {sym}: {ex} - nothing changed, re-checked next pass', key=f'open-orders|{sym}')
                return None
            self.health.setdefault('confirmed', {}).setdefault('stops', {})[sym] = now_utc().isoformat(timespec='seconds')
            self.resolve(f'open-orders|{sym}', f'open orders {sym} readable again')
            self._stops_seen(sym, tags, push=True)                      # AUD-04: confirmed now; no second read this pass
            for l in lots:
                if l['symbol'] != sym: continue
                key = f"stop-unseen|{sym}|{l['side']}"
                if l['stop_id'] not in tags:
                    self._stopv_due(sym, 0.0)                           # AUD-04: the verifier checks (and repairs) it now
                    self.err(f"{sym} {l['side']}: stop {l['stop_id']} not seen among Binance open orders after the outage - "
                             'check this position\'s protection (nothing was changed)', key=key)
                else:
                    self.resolve(key, f"{sym} {l['side']}: stop seen among open orders again")
        return waiting

    # ------------------------------------------------------------ AUD-04: routine verification + repair of protective stops
    @staticmethod
    def _fast(fn, *a, **k):
        """Call an exchange read with retry=False when it takes that parameter: the verifier holds the engine lock, so one
        attempt and never a backoff / Retry-After sleep; a failure is 'unknown'. Keyword arguments the callable does not
        take (a test fake, an older client) are dropped."""
        try: ps = inspect.signature(fn).parameters
        except (TypeError, ValueError): ps = {}
        var = any(x.kind == x.VAR_KEYWORD for x in ps.values())
        kw = {n: v for n, v in k.items() if var or n in ps}
        if var or 'retry' in ps: kw['retry'] = False
        return fn(*a, **kw)

    def _stops_seen(self, sym, tags, push=False):
        """Positive evidence from ANY successful open-order read of `sym` (verifier, reconcile, T05b recovery): every lot
        whose recorded stop is listed is confirmed now (miss state cleared, its stop-missing incident closed).
        push=True (the T05b recovery read): when every lot's stop there is listed, the routine verification of the coin
        moves a full period on - the same symbol is not read twice in one pass."""
        now = self.clock(); self._stopv_last[sym] = now
        for k, l in self.state['lots'].items():
            if l['symbol'] != sym or not l.get('stop_id') or l['stop_id'] not in tags: continue
            l['stop_confirmed_t'] = now
            if l.get('stop_miss') or l.get('stop_miss_why'):
                l['stop_miss'] = 0; l.pop('stop_miss_t', None); l.pop('stop_miss_why', None); l.pop('stop_foreign', None)
            self.resolve(f'stop-missing|{k}', f"{sym} {l['side']} [{l['sleeve']}]: protective stop {l['stop_id']} seen on Binance")
        grp_all = [l for l in self.state['lots'].values() if l['symbol'] == sym and not l.get('pending')]
        for side in {l['side'] for l in grp_all}:                  # T05b's post-outage report too, once every stop is listed
            grp = [l for l in grp_all if l['side'] == side]
            if grp and all(l.get('stop_id') in tags for l in grp):
                self.resolve(f'stop-unseen|{sym}|{side}', f'{sym} {side}: stop seen among open orders again')
        if push and grp_all and all(l.get('stop_id') in tags and not l.get('stop_dirty') for l in grp_all):
            self._stopv[sym] = max(self._stopv.get(sym, -1e18), now + self._stopv_period(sym))

    def _stop_rows(self, sym):
        """Open orders of one symbol for the verifier -> (rows, strict). Detailed rows when the client offers them, else
        tag-only rows. While a lot on the coin holds an ALGO stop, an 'algo endpoint unsupported' answer raises (unknown),
        never 'no algo stops'. strict=True: the classic AND the algo listing were both read, so a recorded stop that is
        absent is really not open; the tag-only fallback cannot prove that for algo stops (strict=False)."""
        fn = getattr(self.trade, 'open_stop_orders', None)
        algo = any(l['symbol'] == sym and str(l.get('stop_id') or '').startswith(('a:', 'ac:')) for l in self.state['lots'].values())
        if callable(fn): return list(self._fast(fn, sym, strict_algo=algo)), True
        return [dict(tag=t) for t in self._fast(self.trade.open_stop_tags, sym)], False

    def _stop_lookup(self, sym, tag):
        """Direct status of one stop by id ('o:'/'a:'), or None = unknown (no lookup available, not found, read failed)."""
        fn = getattr(self.trade, 'stop_status', None)
        if not callable(fn): return None
        self.stopv_stats['lookups'] += 1
        try: return self._fast(fn, sym, tag)
        except Exception as ex:
            log.info(f'stop status {sym} {tag} unknown ({ex})'); return None

    def _positions_fast(self):
        """Positions right before a stop restore: critical (never skipped by the circuit), one attempt (no sleep)."""
        return self._fast(self.trade.positions, critical=True)

    def stop_missing_block(self, sym):
        """Entry / add block text while a lot on this coin has a recorded stop the verifier did not find on Binance; the
        text follows the most severe lot state: owner_check > restoring > checking. None = not blocked."""
        whys = {l.get('stop_miss_why') or 'checking' for l in self.state['lots'].values() if l['symbol'] == sym and l.get('stop_miss')}
        return next((STOP_MISSING_BLOCKS[w] for w in STOP_MISS_ORDER if w in whys), None)

    def stop_missing_on(self, sym):
        return self.stop_missing_block(sym) is not None

    def stop_view(self, l, now=None):
        """Panel / Telegram view of one lot's protection. 'protected' needs a recorded, clean stop AND a confirmation on
        Binance within STOP_FRESH_S; an older (or no) confirmation is shown as not confirmed, never as protected. A missing
        stop's note says what the bot is doing about it (lot['stop_miss_why'])."""
        now = self.clock() if now is None else now
        t = l.get('stop_confirmed_t'); age = (now - t) if t else None
        if l.get('stop_miss'): st = 'missing'
        elif not l.get('stop_id') or l.get('stop_dirty'): st = 'placing'
        elif age is None: st = 'unverified'
        elif age > STOP_FRESH_S: st = 'stale'
        else: st = 'confirmed'
        note = dict(placing='stop being placed', missing=STOP_MISSING_NOTES.get(l.get('stop_miss_why') or 'checking'),
                    unverified='stop not verified on Binance yet', confirmed=None,
                    stale=f'stop last confirmed {int(age // 60) if age else 0} min ago').get(st)
        return dict(protected=st == 'confirmed', stop_state=st, stop_age_s=None if age is None else round(age, 1), stop_note=note)

    @staticmethod
    def _stopv_period(sym):
        """Per-symbol verification period in STOP_VERIFY_S * [0.8, 1.2]: a fixed per-coin phase (crc32 of the symbol), so
        held coins drift apart instead of bursting together, and runs stay deterministic (replays / tests)."""
        frac = (zlib.crc32(sym.encode()) % 1000) / 999.0
        return STOP_VERIFY_S * (1 - STOP_VERIFY_JITTER + 2 * STOP_VERIFY_JITTER * frac)

    def _stopv_due(self, sym, when):
        """Bring a symbol's next verification forward to `when` (never pushes an earlier one back)."""
        self._stopv[sym] = min(self._stopv.get(sym, when), when)

    def verify_stops(self, reason='routine', cycle=False):
        """AUD-04: confirm that every held lot's recorded stop is still open on Binance, and repair it when it is not.
        Budget: ONE symbol-scoped openOrders + openAlgoOrders read (weight 1 + 1) per held symbol, at most about once per
        STOP_VERIFY_S (fixed per-coin jitter +-20%) from manage; a candle close (cycle=True) re-reads only symbols whose
        last read is older than their routine period (never a burst of every held symbol); a stop order action brings its symbol forward to STOP_SETTLE_S, but never sooner than
        STOP_ACTION_MIN_S after its last read; the first pass after a start reads every held symbol. A missing stop costs
        one order-status lookup (weight 1) and, before any restore, one critical positions read (weight 5). Every read is
        one attempt with no sleep (it runs under the engine lock). Never runs in dry mode or while the T05b circuit is in
        outage; a read that fails leaves everything unknown (no miss is counted, nothing is changed).
        Returns True when lot state changed (the caller saves)."""
        if self.dry or self.trade is None or not self.connected: return False
        if self.exchange_state().get('state') == 'outage': return False
        now = self.clock(); changed = False
        held = sorted({l['symbol'] for l in self.state['lots'].values() if not l.get('pending')})
        for sym in list(self._stopv):
            if sym not in held: self._stopv.pop(sym, None)
        for sym in held:
            due = self._stopv.get(sym, 0.0) <= now
            if cycle and now - self._stopv_last.get(sym, -1e18) >= self._stopv_period(sym): due = True
            if not due: continue
            self._stopv[sym] = now + self._stopv_period(sym)       # routine next read; a miss / order action pulls it forward
            res = self._verify_symbol(sym, reason)
            if res is None:                                       # unknown: no conclusion, re-checked soon
                self.stopv_stats['unknown'] += 1; self._stopv_due(sym, now + STOP_RECHECK_S)
                if self.exchange_state().get('state') == 'outage': break      # the circuit just opened: stop reading
                continue
            changed = changed or res
        return changed

    def _verify_stage(self, reason='routine', cycle=False):
        """verify_stops as an isolated stage: a crash never blocks the rest of manage / the cycle; it is one keyed,
        deduplicated incident (closed by the next pass that completes)."""
        try:
            res = self.verify_stops(reason, cycle=cycle)
        except Exception as e:
            self.err(f'stop verification failed ({type(e).__name__}: {str(e)[:120]}) - stops not checked this pass, retried next pass',
                     key='stop-verify')
            return False
        self.resolve('stop-verify', 'stop verification running again')
        return res

    def _owner_alert(self, k, l, msg, push):
        """One keyed stop-missing incident per lot; the Telegram push only when the incident is new."""
        key = f'stop-missing|{k}'
        new_inc = not (self.health.get('incidents', {}).get(key) or {}).get('open')
        self.err(msg, key=key)
        if new_inc and push: self.notify(push)

    def _verify_symbol(self, sym, reason):
        """One verification of `sym`. None = unknown (read failed); else True/False = lot state changed."""
        try:
            rows, strict = self._stop_rows(sym)
        except Exception as ex:
            if not self._exchange_down(ex):
                self.err(f'open orders {sym}: {ex} - nothing changed, re-checked next pass', key=f'open-orders|{sym}')
            return None
        self.stopv_stats['reads'] += 1
        self.health.setdefault('confirmed', {}).setdefault('stops', {})[sym] = now_utc().isoformat(timespec='seconds')
        self.resolve(f'open-orders|{sym}', f'open orders {sym} readable again')
        tags = {r['tag'] for r in rows}
        self._stops_seen(sym, tags)
        now, changed, recheck = self.clock(), False, False
        owned = self._owned_stop_tags(sym)        # lot stops AND AUD-03b provisional stops: never an extra, never adopted
        orph = {t for s_, t in self.state.get('orphans') or [] if s_ == sym}   # queued for cancellation: never adopted (the
        def queued(r):                                                          # sweep would cancel the lot's only stop)
            cid = str(r.get('client_id') or '')                                 # nor double-cancelled here
            return r['tag'] in orph or (cid and (f'c:{cid}' in orph or f'ac:{cid}' in orph))
        free = [r for r in rows if not self._row_owned(r, owned) and not queued(r) and str(r.get('type') or '').upper() in STOP_TYPES]
        extras = [r for r in free if BOT_STOP_CID_RE.fullmatch(str(r.get('client_id') or ''))]
        foreign = [r for r in free if not BOT_STOP_CID_RE.fullmatch(str(r.get('client_id') or ''))]
        for r in foreign:                       # AUD-04 r2 (Codex): coverage ledger for this pass - a fixed-quantity stop covers
            r['left'] = float('inf') if r.get('close_position') is True else float(r.get('qty') or 0.0)   # at most its quantity
        for k, l in list(self.state['lots'].items()):
            if l['symbol'] != sym or l.get('pending') or k not in self.state['lots']: continue
            if not l.get('stop_id'):                              # no recorded stop: adopt a matching bot stop if one exists
                if self._adopt_stop(k, l, extras): changed = True
                continue
            if l['stop_id'] in tags: continue                     # confirmed above
            if l.get('stop_dirty') and l.get('stop_miss_why') != 'owner_check': continue   # being replaced by manage already
            if now - (l.get('stop_placed_t') or -1e18) < STOP_LIST_GRACE_S:
                recheck = True; continue                          # just placed: not listed yet is not a miss
            l['stop_miss'] = l.get('stop_miss', 0) + 1; l.setdefault('stop_miss_t', now); changed = recheck = True
            self.stopv_stats['misses'] += 1
            st = self._stop_lookup(sym, l['stop_id'])
            if st in STOP_LIVE:                                   # listed late, but Binance says it is working: confirmed
                l['stop_miss'] = 0; l.pop('stop_miss_t', None); l.pop('stop_miss_why', None); l.pop('stop_foreign', None)
                l['stop_confirmed_t'] = now
                self.resolve(f'stop-missing|{k}', f"{sym} {l['side']} [{l['sleeve']}]: stop {l['stop_id']} confirmed working")
                continue
            why = 'checking'
            if st in STOP_GONE: gone = True
            elif st in STOP_FIRED: gone = l['stop_miss'] >= 3      # 'fired' but the position may be intact: re-check first
            elif st is None and str(l['stop_id']).startswith(('a:', 'ac:')):
                gone = strict and l['stop_miss'] >= STOP_ALGO_UNKNOWN_MISSES   # never on one guess, never unprotected forever
                if not gone and l['stop_miss'] >= 2:
                    why = 'owner_check'; self.stopv_stats['owner_checks'] += 1
                    self._owner_alert(k, l, f"{sym} {l['side']} [{l['sleeve']}]: algo stop {l['stop_id']} not listed and its status "
                                      'cannot be read - not replaced yet (could create a second stop); check it on Binance',
                                      f"🆘 {l['side']} {sym} [{l['sleeve']}]: algo stop {l['stop_id']} cannot be confirmed on Binance - check it.")
            else: gone = l['stop_miss'] >= 2
            if not gone:
                if l.get('stop_miss_why') == 'owner_check' and self._take_cover(l, foreign, l.get('stop_foreign')):
                    why = 'owner_check'                               # still there AND still covering this lot (ledger)
                l['stop_miss_why'] = why
                log.info(f"{sym} {l['side']} [{l['sleeve']}] stop {l['stop_id']} not listed (status {st or 'unknown'}, "
                         f"miss {l['stop_miss']}) - re-checking before any restore"); continue
            self._restore_missing_stop(k, l, st, extras, foreign)
        clean = all(l.get('stop_id') in tags and not l.get('stop_dirty') for l in self.state['lots'].values()
                    if l['symbol'] == sym and not l.get('pending'))
        claims = self._side_entries(sym)
        if extras and clean:                                     # never two live stops: cancel bot stops nothing owns -
            for r in extras:                                     # only once every lot's own stop is listed (never 0 stops)
                if self._row_owned(r, self._owned_stop_tags(sym)): continue
                gk = f"{sym}|{r.get('position_side')}"             # Binance holds more than the lots there (untracked, seen
                if (gk in self.untracked or gk in self.state.get('over_seen', {})   # once, or an entry not booked yet): that
                        or r.get('position_side') in claims):      # stop may be protecting it - kept
                    continue
                self.err(f"{sym}: extra bot stop {r['tag']} ({r.get('position_side') or '?'} @ {r.get('stop_price')}) not owned by "
                         'any trade - cancelled so the position never carries two stops', key=f'stop-extra|{sym}|{r["tag"]}')
                self._cancel_or_park(sym, r['tag']); self.stopv_stats['extras_cancelled'] += 1; changed = True
                if [sym, r['tag']] not in (self.state.get('orphans') or []):      # cancelled -> incident over
                    self.resolve(f'stop-extra|{sym}|{r["tag"]}', f"{sym}: extra bot stop {r['tag']} cancelled")
        if recheck: self._stopv_due(sym, now + STOP_RECHECK_S)
        return changed

    def _side_claims(self, sym):
        """side -> quantity that local entries not booked as lots yet DO hold on Binance: an AUD-03b unconfirmed entry
        owns the size that showed up for it (prov_qty / seen_qty), a resting maker entry its recorded fills. Such size
        never makes a lot's stop look intact; an entry that never executed claims nothing (a sibling lot's missing stop
        is restored at once)."""
        out = {}
        for u in (self.state.get('unconfirmed_entries') or {}).values():
            if u.get('symbol') == sym:
                q = max(float(u.get('prov_qty') or 0), float(u.get('seen_qty') or 0))
                if q > 0: out[u['side']] = out.get(u['side'], 0.0) + q
        for r_ in (self.state.get('resting_entries') or {}).values():
            if r_.get('symbol') == sym and float(r_.get('filled') or 0) > 0:
                out[r_['side']] = out.get(r_['side'], 0.0) + float(r_['filled'])
        return out

    def _side_entries(self, sym):
        """Sides of `sym` with any local entry not booked as a lot yet (unconfirmed or resting): a bot stop nothing owns
        there may be protecting it (e.g. an earlier provisional stop whose answer was lost) - never cancelled as an extra."""
        return ({u['side'] for u in (self.state.get('unconfirmed_entries') or {}).values() if u.get('symbol') == sym}
                | {r_['side'] for r_ in (self.state.get('resting_entries') or {}).values() if r_.get('symbol') == sym})

    @staticmethod
    def _protects(row, side):
        """AUD-04 r1 (Codex): True only for a stop that CLOSES a `side` position - LONG closes with SELL, SHORT with BUY. In
        hedge mode its positionSide must be that side; a one-way (BOTH) row must also be reduceOnly or closePosition.
        A same-positionSide stop that BUYS a LONG (or SELLS a SHORT) is an entry/add order: never protection. Unknown
        direction -> not protection (fail closed: the bot restores its own stop)."""
        close = 'SELL' if side == 'LONG' else 'BUY'
        if str(row.get('side') or '').upper() != close: return False
        ps = str(row.get('position_side') or '').upper()
        if ps == side: return True
        return ps == 'BOTH' and bool(row.get('reduce_only') or row.get('close_position'))

    def _adopt_stop(self, k, l, extras):
        """A bot stop nothing owns, on this lot's side, with this lot's quantity and stop price, IS this lot's stop (e.g.
        a placement whose answer was lost): record it instead of placing a second one."""
        r = self.rules.get(l['symbol']) or dict(step=1e-9, tick=1e-9)
        for x in extras:
            if not self._protects(x, l['side']) or x.get('qty') is None or x.get('stop_price') is None: continue
            if abs(x['qty'] - l['qty']) <= r['step'] / 2 and abs(x['stop_price'] - l['stop']) <= r['tick'] / 2:
                extras.remove(x)
                l['stop_id'], l['stop_dirty'], l['stop_miss'], l['stop_confirmed_t'] = x['tag'], False, 0, self.clock()
                l.pop('stop_miss_t', None); l.pop('stop_miss_why', None); l.pop('stop_foreign', None)
                self.stopv_stats['adopted'] += 1
                log.warning(f"{l['symbol']} {l['side']} [{l['sleeve']}] adopted the matching bot stop {x['tag']} found on Binance")
                self.resolve(f'stop-missing|{k}', f"{l['symbol']} {l['side']} [{l['sleeve']}]: matching stop {x['tag']} found and adopted")
                return True
        return False

    def _take_cover(self, l, foreign, tag=None):
        """AUD-04 r2 (Codex): take this lot's size out of the pass's external-stop coverage ledger. A row covers the lot only
        if it CLOSES the lot's side (_protects) and has enough quantity left (an explicit closePosition row covers the whole
        side: unlimited); unknown / zero quantity without closePosition is not coverage (fail closed). `tag` restricts the
        choice to that row. Returns the row used, or None."""
        step = (self.rules.get(l['symbol']) or dict(step=1e-9))['step']
        for x in foreign:
            if tag is not None and x['tag'] != tag: continue
            if not self._protects(x, l['side']): continue
            if x.get('left', 0.0) >= l['qty'] - step / 2:
                x['left'] = x.get('left', 0.0) - l['qty']
                return x
        return None

    def _restore_missing_stop(self, k, l, status, extras, foreign):
        """Fail closed for a recorded stop that is gone while the position may still be open: adopt a matching bot stop;
        else a stop placed OUTSIDE the bot on this side covering at least this lot's size, or the whole position (the owner
        edited it in the Binance app) -> owner_check alert, nothing placed (never a second stop); else re-read the position (critical, one attempt) RIGHT before acting -
        smaller than the lots (size of entries not booked yet is NOT counted) means the stop or something else closed it
        and reconcile books it, nothing is placed; intact -> keyed alert, lot marked stop_dirty and the stop restored
        through the normal stop-first _replace_stop (a failure stays dirty and is retried every pass)."""
        sym, side = l['symbol'], l['side']
        if self._adopt_stop(k, l, extras): return True
        r = self.rules.get(sym) or dict(step=1e-9)
        grp = [x for x in self.state['lots'].values() if x['symbol'] == sym and x['side'] == side and not x.get('pending')]
        expected = sum(x['qty'] for x in grp)
        fx = self._take_cover(l, foreign)            # r2: a closing stop with quantity LEFT for this lot (ledger), or closePosition
        if fx is not None:
            l['stop_miss_why'] = 'owner_check'; l['stop_foreign'] = fx['tag']; self.stopv_stats['owner_checks'] += 1
            self._owner_alert(k, l, f"{sym} {side} [{l['sleeve']}]: stop {l['stop_id']} is gone and Binance holds a stop for the same "
                              f"size placed outside the bot ({fx['tag']} @ {fx.get('stop_price')}) - not replaced (it would be a "
                              'second stop); check it on Binance',
                              f"🆘 {side} {sym} [{l['sleeve']}]: its bot stop was replaced by another stop on Binance "
                              f"(@ {fx.get('stop_price')}). The bot does not add a second one - check it.")
            return True
        try:
            live = self._positions_fast()
        except Exception as ex:
            l['stop_miss_why'] = 'checking'
            log.info(f'{sym} {side}: position re-check before restoring the stop failed ({ex}) - re-checked next pass'); return True
        claim = self._side_claims(sym).get(side, 0.0)
        have = max(0.0, live.get((sym, side), 0.0) - claim)
        tol = r['step'] * (len(grp) + 1) if sym in self.rules else 1e-9
        if have < expected - tol:
            self.stopv_stats['deferred'] += 1; l['stop_miss_why'] = 'checking'
            log.info(f"{sym} {side} [{l['sleeve']}] stop {l['stop_id']} gone (status {status or 'unknown'}) and the position is "
                     f'smaller ({have} vs {expected:.6g}, {claim} held for unbooked entries) - left to reconcile, no stop placed')
            return True
        old = l['stop_id']; l['stop_miss_why'] = 'restoring'; l.pop('stop_foreign', None)   # verified: no other stop there
        self._owner_alert(k, l, f"{sym} {side} [{l['sleeve']}]: protective stop {old} is no longer open on Binance (status "
                          f"{status or 'not listed'}) while the position is still open - restoring it",
                          f'🆘 {side} {sym} [{l["sleeve"]}]: its stop on Binance disappeared ({status or "not listed"}). Restoring it now.')
        l['stop_dirty'] = True
        if self._replace_stop(l):                                  # clears the miss state on success
            self.stopv_stats['restored'] += 1
            log.warning(f'{sym} {side} [{l["sleeve"]}] stop restored: {old} -> {l["stop_id"]}')
        return True

    def _manage_failed(self, why, key=None):
        h = self.health; h['manage_fail_streak'] += 1
        if why != 'see errors': self.err(f'manage: {why}', key=key)
        cause, t = key or why, time.time()
        if h['manage_fail_streak'] >= 6 and not h['alerted']:
            h['alerted'] = True                                     # re-armed by a good pass; T05b final: the SAME cause is
            last = h.get('alert_last') or (None, 0.0)               # not re-sent within MANAGE_ALERT_REARM_S (flapping outage)
            if last[0] == cause and t - last[1] < MANAGE_ALERT_REARM_S: return
            h['alert_last'] = (cause, t)
            self.notify(f'🆘 Trade management is failing ({why}). Exchange stops are still in place - check the app.')

    def exit_plan(self, l):
        """Plain-language exit plan of an open lot, for the panel and Telegram: dict(title, steps[], next).
        Many trend slots deliberately have NO fixed target - this says so instead of leaving a blank."""
        g, sd = l.get('mgmt') or {}, (1 if l['side'] == 'LONG' else -1)
        R, e0 = l.get('R') or 0.0, l.get('e0', l['avg'])
        p = lambda v: f'{v:.6g}'
        steps, nxt, title = [], None, None
        if l.get('manual') and l.get('tp'):
            title = f"Fixed target {p(l['tp'])}"
        if l.get('levels'):
            n, k = len(l['levels']), l.get('dca', 0)
            if l.get('tp') is not None:
                title = f"Basket target {p(l['tp'])}"; steps.append(f'DCA basket: target moves closer after each safety order ({k} of {n} filled)')
            else:
                title = 'Basket target banked - the rest runs'; steps.append('rest rides with the stop at breakeven or better')
            if k < n and l.get('tp') is not None:
                nxt = f"safety order {k + 1} at {p(l['levels'][k])}"
                steps.append('if the BTC circuit breaker trips: safety orders ' + {'pause': 'pause', 'half_size': 'continue at half size',
                             'continue_plan': 'continue as planned'}.get(l.get('breaker_dca', 'pause'), 'pause'))
        tps = [x for i, x in enumerate(S.norm_tps(g.get('tps'))) if i not in (l.get('tps_done') or [])]
        if tps and R:
            steps.append('take-profit ladder: ' + ', '.join(f'{f * 100:.0f}% at {p(e0 + sd * r * R)} (+{r:g}R)' for r, f in tps))
            nxt = nxt or f'ladder level at {p(e0 + sd * tps[0][0] * R)}'
        if g.get('tp1_r') and not l.get('tp1') and R:
            lvl = e0 + sd * g['tp1_r'] * R
            steps.append(f"take {g.get('tp1_frac', 0.5) * 100:.0f}% profit at {p(lvl)} (+{g['tp1_r']:g}R)"); nxt = nxt or f'partial take-profit at {p(lvl)}'
        if g.get('tp_r') and not g.get('runner') and not g.get('ttp') and R:
            lvl = e0 + sd * g['tp_r'] * R
            title = title or f'Fixed target {p(lvl)}'; steps.append(f"close everything at {p(lvl)} (+{g['tp_r']:g}R)")
        if g.get('be_r') and R and sd * (l['stop'] - l['avg']) < 0:
            lvl = e0 + sd * g['be_r'] * R; steps.append(f"stop moves to breakeven once price reaches {p(lvl)} (+{g['be_r']:g}R)")
            nxt = nxt or f'breakeven trigger at {p(lvl)}'
        if g.get('trail_atr'): steps.append(f"trailing stop {g['trail_atr']:g} ATR behind the best price")
        if g.get('ttp'): steps.append(f"trailing take-profit from +{g['ttp'].get('at_r', 2):g}R, closes on a {g['ttp'].get('dev_pct', 3):g}% pull-back")
        run = g.get('runner')
        if run: steps.append(f"runner: breakeven at +{run.get('be_r', 2):g}R, then the stop locks {run.get('gap_r', 1):g}R behind every new {run.get('step_r', 1):g}R")
        if 'pyramid' in g and l.get('adds', 0) < g['pyramid'].get('n', 0) and l.get('next_add') is not None:
            steps.append(f"pyramid add {l.get('adds', 0) + 1} of {g['pyramid']['n']} at {p(l['next_add'])}"); nxt = nxt or f"pyramid add at {p(l['next_add'])}"
        if g.get('max_bars'): steps.append(f"time exit after {g['max_bars']} candles")
        if not l.get('manual') and l.get('key_strategy'):
            steps.append(f"or the strategy's own exit signal at a candle close")
        if title is None and l.get('manual'):
            title = 'Manual trade: stop only'
        if title is None:
            title = 'No fixed target - lets the winner run'
            if not steps or steps == ["or the strategy's own exit signal at a candle close"]:
                steps.insert(0, "exits only on the strategy's reversal signal or the stop")
        steps.append(f"stop {p(l['stop'])} on Binance protects the position")
        if l.get('add_blocked'): steps.append(f"adds blocked: {l['add_blocked']}")
        return dict(title=title, steps=steps, next=nxt)

    def _add_block(self, lot, q, px):
        """One gate for every quantity ADDED to an open lot (DCA safety order, pyramid add). Returns a reason or None.
        An add is new exposure, so it passes the same checks as an entry: daily halt, paused entries, a confirmed stop,
        the slot's leverage cap (on the share recorded when the lot opened - a removed/replaced slot gets no more adds;
        its stop and exits keep running) and the portfolio risk rules (coin cap, open risk, funding)."""
        st, Sg = self.state, self.S
        pb = self.persist_block()                                 # AUD-05 r2
        if pb: return pb
        if st.get('halted'): return 'daily loss halt is active'
        if Sg.get('ENTRIES_PAUSED'): return 'entries paused'
        if self.exchange_state().get('state') == 'outage': return OUTAGE_ADD     # T05b final: an add is new exposure
        if lot.get('stop_dirty'): return 'stop not confirmed yet'
        blk = self.stop_missing_block(lot['symbol'])               # AUD-04: a stop on this coin is missing on Binance
        if blk: return blk
        if lot.get('manual'): return 'manual trade (no adds)'
        lx = self._lev_exception_block(lot['symbol'])             # T03c r1: the above-cap exception admitted the entry only
        if lx: return lx
        if self._breaker_add_mult(lot) == 0.0:
            return 'BTC circuit breaker active - ' + ('DCA safety orders paused' if lot.get('levels') else 'pyramid adds paused')
        sl = next((x for x in Sg['SLEEVES'] if x['id'] == lot['sleeve']), None)
        if not sl or (lot.get('key_strategy') and sl['key'] != lot['key_strategy']):
            return 'strategy slot removed or replaced - no more adds (stop and exits still run)'
        share = min(float(sl['share']), float(lot.get('share', sl['share'])))
        cap = Sg['MAX_LEVERAGE'] * (self.last_eq or 0) * share
        used = sum(l['qty'] * l['avg'] for l in st['lots'].values() if l['sleeve'] == lot['sleeve'])
        if used + q * px > cap: return f'slot leverage cap ({used + q * px:.0f} > {cap:.0f} USDT)'
        sd = 1 if lot['side'] == 'LONG' else -1
        add_risk = 0.0 if lot.get('levels') else q * max(0.0, sd * (px - lot['stop']))   # DCA levels are already in open risk
        hits = self._rules_eval(sl, lot['symbol'], lot['side'], dict(notional=q * px, risk=add_risk), add=True)
        warn = [m for _, mode, m in hits if mode == 'warn']
        if warn and lot.get('add_warned') != warn[0]:
            lot['add_warned'] = warn[0]; log.warning(f"{lot['symbol']} [{lot['sleeve']}] add allowed but: {warn[0]}")
        enf = [f'risk rule {n}: {m}' for n, mode, m in hits if mode == 'enforce']
        return enf[0] if enf else None

    def _breaker_add_mult(self, lot):
        """Size multiplier for an add while the BTC circuit breaker (enforce) is active: pyramid adds 0; DCA safety orders
        follow the policy recorded on the lot when it opened (pause 0 / half_size 0.5 / continue_plan 1). 1 otherwise."""
        rb = self.risk_rules_cfg()['btc_breaker']
        if rb['mode'] != 'enforce' or (self.state.get('breaker_until') or 0) <= time.time(): return 1.0
        if not lot.get('levels'): return 0.0
        return {'pause': 0.0, 'half_size': 0.5, 'continue_plan': 1.0}.get(lot.get('breaker_dca', 'pause'), 0.0)

    def _add_gate(self, lot, q, px, why):
        """True = the add is BLOCKED (logged once per reason, kept on the lot and in the missed list for the UI)."""
        reason = self._add_block(lot, q, px)
        if not reason:
            lot.pop('add_blocked', None); return False
        if lot.get('add_blocked') != reason:
            lot['add_blocked'] = reason
            self.health['adds_blocked'] = self.health.get('adds_blocked', 0) + 1
            log.warning(f"{lot['symbol']} [{lot['sleeve']}] {why} blocked: {reason}")
            rec = dict(candle=now_utc().isoformat(timespec='seconds'), logged=now_utc().isoformat(timespec='seconds'), sleeve=lot['sleeve'],
                       strategy=lot.get('key_strategy') or lot['sleeve'], symbol=lot['symbol'], side=lot['side'], price=px,
                       reason=f'{why} blocked: {reason}', kind='add_blocked')
            self.missed.append(rec); self.missed = self.missed[-600:]; self._save_aux('missed', self.missed)
        return True

    # ------------------------------------------------------------ reconcile with exchange
    def reconcile(self, eq, live=None):
        """Match engine lots to what Binance actually holds.
        - less on the exchange: the lots whose stop order is no longer open were stopped out (identified by stop-order id);
          if that cannot be determined this round, nothing is guessed - it is retried on the next pass;
          if every stop is still open, the lots are resized to what the exchange holds.
        - more on the exchange (or a position with no lot at all): reported as UNTRACKED and alerted, never ignored."""
        fetched = live is None
        live = self.trade.positions() if live is None else live
        if fetched: self.health.setdefault('confirmed', {})['positions'] = now_utc().isoformat(timespec='seconds')
        if self.state.get('unconfirmed_entries'): self._settle_unconfirmed(live)   # AUD-03b
        st = self.state
        groups = {}
        for k, l in st['lots'].items(): groups.setdefault((l['symbol'], l['side']), []).append(k)
        untracked = {}
        def dust(sym, q):        # below Binance's minimum order: cannot be traded or closed normally, not worth an alarm
            r = self.rules.get(sym); px = (self.marks or {}).get(sym)
            return r is not None and (q < r['min_qty'] or (px and q * px < r['min_notional']))
        resting = {}                       # maker entry orders still working: their fills are not in a lot yet
        for r_ in st.get('resting_entries', {}).values():
            resting[(r_['symbol'], r_['side'])] = resting.get((r_['symbol'], r_['side']), 0.0) + r_['qty']
        for k_, q_ in self._unconfirmed_claims().items(): resting[k_] = resting.get(k_, 0.0) + q_   # AUD-03b: not untracked
        for (sym, side), have in live.items():
            if (sym, side) not in groups and have > 0:
                # one whole step of a position with no lot is real (e.g. 0.001 BTC = a tradable ~$120 orphan): the same sub-step
                # tolerance as the tracked-lot path, not a whole step; dust and filled resting entries stay excluded
                tol = self._qty_tol(self.rules[sym]['step']) if sym in self.rules else 1e-9
                if have > tol + resting.get((sym, side), 0.0) and not dust(sym, have): untracked[(sym, side)] = have
        short_seen = st.setdefault('short_seen', {}); seen_now = set()
        for (sym, side), keys in groups.items():
            expected = sum(st['lots'][k]['qty'] for k in keys)
            have = live.get((sym, side), 0.0)
            tol = self._qty_tol(self.rules[sym]['step']) if sym in self.rules else 1e-9
            rest = [k for k in keys if st['lots'][k].get('restored_from_bak')]
            if rest:                       # AUD-05 r1: lots restored from an OLDER state copy (.bak) - owner review first
                rk = f'restored|{sym}|{side}'
                if not self.S.get('ENTRIES_PAUSED'):                    # the owner checked Binance and resumed entries:
                    for k in rest:                                      # normal bookkeeping from here on
                        st['lots'][k].pop('restored_from_bak', None); st['lots'][k].pop('restored_mismatch', None)
                    self.resolve(rk, f'{sym} {side}: entries resumed - restored trade managed normally again')
                elif abs(have - expected) <= tol:                       # Binance holds exactly the restored size
                    for k in rest: st['lots'][k].pop('restored_mismatch', None)
                    self.resolve(rk, f'{sym} {side}: restored trade matches Binance')
                    continue
                else:                                                   # stale copy: nothing booked (no exit, no P&L)
                    for k in rest: st['lots'][k]['restored_mismatch'] = True
                    new_inc = not (self.health.get('incidents', {}).get(rk) or {}).get('open')
                    msg = (f'{sym} {side}: trade(s) restored from an older state copy hold {expected:g} but Binance holds '
                           f'{have:g} - nothing booked (no exit, no P&L, no orders for it) while entries are paused; check '
                           'Binance, then resume entries and the bot books the difference')
                    self.err(msg, key=rk)
                    if new_inc: self.notify(msg)
                    continue
            pend = [k for k in keys if st['lots'][k].get('pending')]
            if pend:                                                    # an unanswered order on this coin/side: settle it first
                res = self._resolve_pending(pend[0], have, expected, tol, self._dust_cap(sym, (self.marks or {}).get(sym)))
                if res is not False: continue                           # resolved (re-checked next pass) or still waiting
                keys = [k for k in keys if k in st['lots']]; expected = sum(st['lots'][k]['qty'] for k in keys)
            gk = f'{sym}|{side}'
            if have > expected + tol:
                if have > expected + tol + resting.get((sym, side), 0.0) and not dust(sym, have - expected):
                    untracked[(sym, side)] = have - expected
                continue
            if have >= expected - tol: continue
            sd = 1 if side == 'LONG' else -1
            try:
                open_tags = self.trade.open_stop_tags(sym)
            except Exception as ex:
                self.err(f'open orders {sym}: {ex} - nothing changed, re-checked next pass', key=f'open-orders|{sym}'); continue
            self.health.setdefault('confirmed', {}).setdefault('stops', {})[sym] = now_utc().isoformat(timespec='seconds')
            self.resolve(f'open-orders|{sym}', f'open orders {sym} readable again')
            self._stops_seen(sym, open_tags)                            # AUD-04: listed stops are confirmed now
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
                step = self.rules[sym]['step']
                # each lot floored to the step, then the leftover whole steps go to the largest remainders: the resized lots
                # add up to what the exchange holds (flooring each lot alone left up to n-1 steps that no lot owned)
                raw = {k: st['lots'][k]['qty'] * scale for k in rest}
                newqs = {k: self._rd(raw[k], step) for k in rest}
                spare = int(round((self._rd(have, step) - sum(newqs.values())) / step))
                for k in sorted(rest, key=lambda k: newqs[k] - raw[k])[:max(0, spare)]:
                    newqs[k] = self._rd(newqs[k] + step, step)
                for k in rest:
                    l = st['lots'][k]
                    newq = newqs[k]
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
        try: self.governor_tick()
        except Exception as ex: self.err(f'governor: {ex}')
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
            self._verify_stage('candle close', cycle=True)          # AUD-04: before any entry decision
            sleeves = [sl for sl in Sg['SLEEVES'] if sl['tf'] == tf]
            syms = sorted({s for sl in sleeves for s in self.sleeve_symbols(sl, include_off=True)} | {l['symbol'] for l in st['lots'].values() if l.get('tf') == tf})
            if not syms: return
            log.info(f'--- {tf} cycle ({reason}) | equity {eq:.2f} ---')
            sigs, frames = self.compute_signals(tf, syms, extra=self._orphan_sleeves(tf))
            self.signals.update(sigs); self.signals_time = now_utc().isoformat(timespec='seconds')
            self._audit_regime_cache(tf, frames)               # T05a: closed-candle trend snapshots (memory only, observe only)
            # refresh ATR for trailing + signal/time exits
            for k, l in list(st['lots'].items()):
                if l.get('tf') != tf or l.get('manual'): continue
                d = frames.get(l['symbol'])
                if d is not None: l['atr_now'] = float(d.atr.iloc[-1])
                sg = sigs.get(f"{l['sleeve']}|{l['symbol']}")
                sl = next((x for x in Sg['SLEEVES'] if x['id'] == l['sleeve']), None)
                bars = (now_utc() - datetime.fromisoformat(l['opened'])).total_seconds() / TF_SEC[tf]
                ex = sg and (sg['lx'] if l['side'] == 'LONG' else sg['sx'])
                ex0 = bool(ex)                                         # T05a: the exit signal before the runner override
                run = l['mgmt'].get('runner')
                if run and d is not None and len(d):
                    sd_ = 1 if l['side'] == 'LONG' else -1; c_ = float(d.c.iloc[-1])
                    trend_ok = (c_ > d.e50.iloc[-1] and d.st.iloc[-1] > 0) if sd_ == 1 else (c_ < d.e50.iloc[-1] and d.st.iloc[-1] < 0)
                    winning = sd_ * (c_ - l['avg']) > 0
                    if winning and trend_ok:
                        if ex: log.info(f"RUNNER {l['symbol']} [{l['sleeve']}] exit signal ignored - winning and trend still up")
                        ex = False
                    elif winning and run.get('trend_exit') and not trend_ok: ex = True
                closing = ex or (l['mgmt'].get('max_bars') and not run and bars >= l['mgmt']['max_bars']) or (sl is None and False)
                self._audit_hold(k, l, d, closing, ex, ex0)            # T05a: candle-close hold/close evaluation (observe only)
                if closing:
                    try: self.close_lot(k, 'exit_signal' if ex else 'time_exit', sg['close'] if sg else None)
                    except Exception as e: self.err(f'exit {l["symbol"]} [{l["sleeve"]}] failed: {e} - stop stays in place, retried next cycle')
            # entries (every signal that is not taken is logged with the reason)
            if Sg['ENTRIES_PAUSED'] or st['halted']:
                log.info('entries paused' + (' (daily halt)' if st['halted'] else ''))
            for sl in sleeves:
                held = [l['symbol'] for l in st['lots'].values() if l['sleeve'] == sl['id']]
                for s in self.sleeve_symbols(sl, include_off=True):
                    sg = sigs.get(f"{sl['id']}|{s}")
                    if not sg or not (sg['le'] or sg['se']):
                        if sg: self._audit_cand(sl, s, None, sg, 'side_masked')   # T05a: a raw signal the slot's sides hide
                        continue
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
                    te = sl.get('trail_entry') or {}
                    if why is None and te.get('dev_atr'):          # trailing entry: wait for a rebound off the extreme
                        why = self.entry_block(sl, s, side, sg=sg)
                        if why is None:
                            self.state['pending_entries'][f"{sl['id']}|{s}"] = dict(
                                sleeve=sl['id'], symbol=s, side=side, ext=sg['close'], atr=sg['atr'], dev=float(te['dev_atr']),
                                sg=dict(sg), tf=sl['tf'], created=time.time(),
                                until=time.time() + int(te.get('max_bars', 3)) * TF_SEC.get(sl['tf'], 14400))
                            log.info(f"TRAIL ENTRY armed {s} {side} [{sl['id']}] rebound {te['dev_atr']} ATR from the extreme")
                            self._audit_cand(sl, s, side, sg, 'armed_trailing')
                            held.append(s); continue
                    if why is None:
                        self.last_skip = ''
                        try:
                            if self.open_lot(sl, s, side, sg, frames.get(s), eq):
                                self._audit_opened(sl, s, side, sg)     # T05a: 'taken', or 'order_placed' (maker)
                                held.append(s); continue
                        except Exception as e:                     # one failed order never stops the other entries
                            self.err(f'entry {s} [{sl["id"]}] failed: {e}'); self.last_skip = f'order failed: {e}'
                        why = self.last_skip or 'order failed'
                    self.miss(sl, s, side, sg, why)
            try: self.grids.on_cycle(tf)
            except Exception as e: self.err(f'grid cycle {tf}: {e}')
            st['last_cycle'][tf] = now_utc().isoformat(timespec='minutes')
            self.health['last_cycle_ok'][tf] = now_utc().isoformat(timespec='seconds')
            self.save_state()
            self.record_equity(self.guard_eq or eq)

    def entry_block(self, sl, sym, side, manual=False, size=None, sg=None, skip_pending=None):
        """One gate for every way a trade can be opened (automatic, 'Take now', trailing entry, manual). Returns a reason or None.
        size: dict(notional, risk) of the trade being opened (for the size-based risk rules); sg: the signal (pump guard).
        Risk-rule warnings of this call are left in self._rule_warns."""
        self._rule_warns = []
        st, Sg = self.state, self.S
        pb = self.persist_block()                                 # AUD-05 r2: a safety file cannot be saved -> no new risk
        if pb: return pb
        if not self.connected or self.error: return 'not connected to Binance'
        if self.exchange_state().get('state') == 'outage': return 'Binance outage - no new entries until it answers again'
        if not SYM_RE.match(sym or '') or sym not in self.rules: return f'{sym} is not tradable'
        if side not in ('LONG', 'SHORT'): return 'bad direction'
        if side == 'SHORT' and not self.hedge: return 'hedge mode off (shorts unavailable)'
        if st.get('halted'): return 'daily loss halt is active'
        if any(l['symbol'] == sym and l['side'] == side and l.get('stop_dirty') for l in st['lots'].values()):
            return 'an open trade on this coin is waiting for its stop to be confirmed'
        blk = self.stop_missing_block(sym)                         # AUD-04 (entries only, both sides: exits never consult it)
        if blk: return blk
        if f'{sym}|{side}' in self.untracked: return 'Binance holds an untracked position on this coin/side - resolve it first'
        if (sym, side) in self._unconfirmed_claims(): return 'an earlier entry on this coin/side is not confirmed yet'
        if manual: return self._rules_block(sl, sym, side, size, manual=True)
        if Sg.get('ENTRIES_PAUSED'): return 'entries paused'
        if not Sg['SYMBOLS_ON'].get(sym, True): return 'coin switched off'
        if not sl.get('enabled', True): return 'strategy slot switched off'
        held = [l for l in st['lots'].values() if l['sleeve'] == sl['id']]
        if any(l['symbol'] == sym for l in held): return 'already in a trade on this coin'
        working = [p for k, p in st.get('pending_entries', {}).items() if p['sleeve'] == sl['id'] and k != skip_pending] + \
                  [r for r in st.get('resting_entries', {}).values() if r['sleeve'] == sl['id']]
        if any(p['symbol'] == sym for p in working): return 'an entry is already working on this coin'
        if len(held) + len(working) >= sl['max_pos']: return f"max positions reached ({sl['max_pos']})"
        when = sl.get('when') or 'any'
        if when != 'any':
            try:
                rg = self.regime_now()
                if not rg.get(when): return f'regime: {S.regime_label(rg)} market'
            except Exception as e:
                return f'regime unknown ({e})'                 # a slot restricted to a regime does not trade blind
        pg = sl.get('pump_guard') or Sg.get('PUMP_GUARD') or {}
        if pg.get('max_candle_atr') and sg and sg.get('rng_atr', 0) > pg['max_candle_atr']:
            return f"pump guard: signal candle {sg['rng_atr']:.1f} ATR > {pg['max_candle_atr']}"
        if pg.get('btc_1h_pct'):
            try:
                mv = self.btc_move_1h()
                if mv > pg['btc_1h_pct']: return f"pump guard: BTC moved {mv:.1f}% in the last hour (> {pg['btc_1h_pct']}%)"
            except Exception as e: log.info(f'pump guard: BTC 1h data unavailable ({e})')
        return self._rules_block(sl, sym, side, size)

    def _ensure_leverage(self, sym, notional=None, risk=None):
        """Exchange leverage = the configured cap (bounded by what Binance allows for the coin). Failure blocks the entry,
        unless the read-only fallback below proves the entry is safe anyway (T03a: already within the cap; T03c: cross
        margin and the account-wide exposure proof of _exposure_check). Returns how the entry may go ahead:
        'set' (leverage set or already cached), 'within_cap' or 'exposure' (the above-cap exception); raises otherwise."""
        want = max(1, int(self.S['MAX_LEVERAGE']))
        if self._lev.get(sym) == want:
            try: current, _ = self._margin_state(sym); current = lev_num(current, 'current leverage', pos=True)
            except Exception: current = None
            if current is not None and current <= want: return 'set'
            self._lev.pop(sym, None)                              # external/unknown change: prove or set it again
        cool = self._lev_cool.get(sym, 0)
        if cool > time.time():                                  # T03c: decided BEFORE any venue write or bracket fetch (r1 P3)
            mx = self._lev_max(sym, fetch=False)                # cached schedule only; cur <= coin max anyway
            return self._leverage_fallback(sym, min(want, mx) if mx else want, None, notional, risk, via='cooldown',
                                           wait=int(cool - time.time()))
        try: self.trade.set_margin_type(sym, 'CROSSED')        # cross is Binance's default; a refusal here is not a safety issue
        except Exception as e: log.info(f'{sym}: margin type not changed ({e})')
        mx = self._lev_max(sym)
        lev = min(want, mx) if mx else want
        try: self.trade.set_leverage(sym, lev)
        except Exception as e:                                  # transient testnet/API errors: one retry, then the fallback below
            self._lev_api_refused(sym, e)                       # every failed leverage request counts (runtime finding)
            log.info(f'{sym}: leverage retry after {e}'); time.sleep(1)
            try: self.trade.set_leverage(sym, lev)
            except Exception as e2:
                self._lev_api_refused(sym, e2)
                self._lev_cool[sym] = time.time() + LEV_REFUSAL_COOLDOWN_S
                return self._leverage_fallback(sym, lev, e2, notional, risk)   # proceeds only if proven safe (read-only)
        self._lev_cool.pop(sym, None)
        self._lev[sym] = want
        return 'set'

    def _brackets(self, sym, fetch=True):
        """T03c r1. The coin's validated bracket schedule, cached LEV_BRACKET_TTL_S (static venue data). A failed, missing
        or malformed answer raises LevReject (never cached, never a stale schedule past its TTL). fetch=False: cache only."""
        c = self._brk.get(sym)
        if c and time.time() - c[0] < LEV_BRACKET_TTL_S: return c[1]
        if not fetch: return None
        if not hasattr(self.trade, 'leverage_brackets'): raise LevReject('brackets', 'leverage brackets unavailable')
        try: b = check_brackets(self.trade.leverage_brackets(sym))
        except Exception as e: raise LevReject('brackets', f'{sym} leverage brackets unavailable or malformed ({e})')
        self._brk[sym] = (time.time(), b)
        return b

    def _lev_max(self, sym, fetch=True):
        """The coin's maximum initial leverage (first bracket), or None if unknown/invalid."""
        try:
            if hasattr(self.trade, 'leverage_brackets'):
                b = self._brackets(sym, fetch); mx = b[0]['lev'] if b else None
            else:
                mx = self.trade.leverage_max(sym) if fetch else None     # older client
            mx = float(mx) if mx is not None else None
            return int(mx) if mx is not None and math.isfinite(mx) and mx >= 1 else None
        except Exception:
            return None

    def _margin_state(self, sym):
        """(leverage, margin type 'CROSSED'/'ISOLATED'/None) as Binance reports the coin now. Read-only; None = unknown."""
        if hasattr(self.trade, 'margin_state'):
            ms = self.trade.margin_state(sym) or {}
            return ms.get('leverage'), ms.get('margin_type')
        return self.trade.current_leverage(sym), None          # older client: leverage only, margin type unknown

    def _exposure_check(self, sym, notional, risk):
        """T03c. Whether an entry is safe although the coin's exchange leverage stays above the cap. Returns (ok, why, numbers);
        numbers['check'] names the failed check. Any unknown, unconfirmed or invalid fact rejects (see _exposure_proof)."""
        try: return self._exposure_proof(sym, notional, risk)
        except LevReject as e: return False, str(e), dict(e.numbers, check=e.check)

    def _exposure_proof(self, sym, notional, risk):
        """T03c (round 1). Under CROSS margin the coin's leverage setting only changes the margin Binance reserves; the size
        is capped by the bot and liquidation is account-wide. The entry is proven safe only from a FRESH exchange snapshot
        (account, every position on every symbol and side, every open order), and only if ALL hold:
          reconcile    - no untracked position, no unconfirmed order, every exchange position matches the bot's lots
                         (a bot maker entry still working may explain extra quantity);
          stops        - every lot has a confirmed exchange stop: stop_id set, not dirty, open on Binance on the right
                         symbol/side and covering the lot's quantity;
          working      - no exposure-increasing order is working on Binance other than the bot's own maker entries;
          effective    - (gross current notional of all positions + reserved exposure + this entry) / totalMarginBalance
                         <= MAX_LEVERAGE. Reserved: bot maker entries working, DCA safety orders not filled yet, pyramid
                         adds left, and active grids (full-fill notional);
          worst case   - every position moves from its CURRENT mark to its stop (loss x LEV_STOP_SLIP), every reserved
                         order fills and stops out, and this entry stops out (x LEV_STOP_SLIP). totalMarginBalance already
                         holds the unrealized P&L, so the projected balance is margin balance minus those mark-to-stop
                         losses. Maintenance = totalMaintMargin + the bracket increase (notional * mmr - cum of the tier,
                         aggregate both sides) from each symbol's current to its worst-case notional. Ratio <= 50%.
        Every input and derived value must be finite and in range. Raises LevReject; returns (True, why, numbers)."""
        cap = lev_num(self.S['MAX_LEVERAGE'], 'leverage cap', pos=True)
        if notional is None or risk is None: raise LevReject('entry_size', 'entry size unknown')
        notional = lev_num(notional, 'entry notional', pos=True); risk = lev_num(risk, 'entry risk')
        st, slip = self.state, LEV_STOP_SLIP
        lots = list(st['lots'].values())
        rest = list((st.get('resting_entries') or {}).values())
        grids = [g for g in (st.get('grids') or {}).values() if isinstance(g, dict)]
        if self.untracked: raise LevReject('reconcile', f'Binance holds an untracked position ({next(iter(self.untracked))})')
        for l in lots:
            if l.get('pending') or l.get('force_close'):
                raise LevReject('reconcile', f"an order on {l.get('symbol')} {l.get('side')} is still unconfirmed")
        if any(g.get('op') for g in grids): raise LevReject('reconcile', 'a grid order is still unconfirmed')
        # ---- the lots themselves: numeric fields and a known stop
        for l in lots:
            s_ = l.get('stop')
            if isinstance(s_, bool) or not isinstance(s_, (int, float)) or not math.isfinite(s_) or s_ <= 0:
                raise LevReject('stops', 'an open lot has no known stop')
            if l.get('side') not in ('LONG', 'SHORT') or not l.get('symbol'): raise LevReject('numeric', 'an open lot is malformed')
            lev_num(l.get('qty'), f"{l['symbol']} lot quantity", pos=True); lev_num(l.get('avg'), f"{l['symbol']} lot entry", pos=True)
        # ---- fresh exchange snapshot (read-only)
        acc = self.trade.account() or {}
        if not isinstance(acc, dict) or 'totalMarginBalance' not in acc or 'totalMaintMargin' not in acc:
            raise LevReject('account', 'account margin data unavailable')
        bal = lev_num(acc['totalMarginBalance'], 'account margin balance', pos=True)
        mm = lev_num(acc['totalMaintMargin'], 'account maintenance margin')
        try: rows, orders, marks = self.trade.position_risk(), self.trade.open_orders_all(), self.trade.marks()
        except Exception as e: raise LevReject('snapshot', f'exchange positions/orders unavailable ({e})')
        pos = {}                                                 # (sym, side) -> [qty, notional, mark]
        for r in rows or []:
            if not isinstance(r, dict) or r.get('side') not in ('LONG', 'SHORT') or not r.get('symbol'):
                raise LevReject('snapshot', 'malformed position row')
            q = lev_num(r.get('qty'), f"{r['symbol']} position quantity")
            if q == 0: continue
            mk = lev_num(r.get('mark'), f"{r['symbol']} mark price", pos=True)
            nt = max(lev_num(r.get('notional'), f"{r['symbol']} position notional"), q * mk)
            p_ = pos.setdefault((r['symbol'], r['side']), [0.0, 0.0, mk]); p_[0] += q; p_[1] += nt; p_[2] = mk
        def mark_of(s):
            for sd_ in ('LONG', 'SHORT'):
                if (s, sd_) in pos: return pos[(s, sd_)][2]
            return lev_num((marks or {}).get(s), f'{s} mark price', pos=True)
        # ---- reconcile engine lots with the exchange
        groups, resting = {}, {}
        for l in lots: groups.setdefault((l['symbol'], l['side']), []).append(l)
        for rec in rest:                                         # only the FILLED part of a maker entry may exist on Binance
            k_ = (rec.get('symbol'), rec.get('side'))
            q_ = lev_num(rec.get('qty'), f'{k_[0]} maker entry quantity', pos=True)
            f_ = lev_num(rec.get('filled', 0.0), f'{k_[0]} maker entry filled quantity')
            if f_ > q_ + 1e-12: raise LevReject('numeric', f'{k_[0]} maker entry filled more than its quantity')
            resting[k_] = resting.get(k_, 0.0) + f_
        for k_ in set(pos) | set(groups):
            have = pos.get(k_, [0.0])[0]; exp = sum(l['qty'] for l in groups.get(k_, []))
            tol = self._qty_tol(float((self.rules.get(k_[0]) or {}).get('step', 1e-9)))
            extra = have - exp - resting.get(k_, 0.0)                  # dust above the lots is tolerated, as reconcile() does
            if have < exp - tol or (extra > tol and not extra < self._dust_cap(k_[0], mark_of(k_[0]))):
                raise LevReject('reconcile', f'{k_[0]} {k_[1]}: Binance holds {have:g}, the bot expects {exp:g}')
        # ---- every lot has a confirmed exchange stop
        by_tag = {o.get('tag'): o for o in (orders or []) if isinstance(o, dict) and o.get('tag')}
        per_order = {}                                           # stop order tag -> [lot qty it must cover, lots, step]
        for l in lots:
            nm = f"{l['symbol']} {l['side']}"
            if l.get('stop_dirty') or not l.get('stop_id'): raise LevReject('stops', f'{nm}: stop not confirmed yet')
            o = by_tag.get(l['stop_id'])
            if o is None: raise LevReject('stops', f"{nm}: stop {l['stop_id']} is not open on Binance")
            closing = 'SELL' if l['side'] == 'LONG' else 'BUY'
            if o.get('symbol') != l['symbol'] or o.get('side') != closing or o.get('position_side') not in (l['side'], 'BOTH'):
                raise LevReject('stops', f"{nm}: stop {l['stop_id']} does not protect this position")
            if (o.get('order_type') != 'STOP_MARKET' or o.get('working_type') != 'MARK_PRICE'
                    or o.get('status') not in ('NEW', 'ACCEPTED') or o.get('close_position') is not False):
                raise LevReject('stops', f"{nm}: stop {l['stop_id']} has the wrong type or trigger basis")
            step = float((self.rules.get(l['symbol']) or {}).get('step', 1e-9))
            if lev_num(o.get('qty'), f'{nm} stop quantity') < l['qty'] - self._qty_tol(step):
                raise LevReject('stops', f'{nm}: stop covers less than the position')
            tick = float((self.rules.get(l['symbol']) or {}).get('tick', 1e-8))
            if abs(lev_num(o.get('stop_price'), f'{nm} stop price', pos=True) - l['stop']) > tick * 1.001 + 1e-12:
                raise LevReject('stops', f"{nm}: stop on Binance is at {o.get('stop_price')}, the bot expects {l['stop']}")
            c_ = per_order.setdefault(l['stop_id'], [0.0, 0, step]); c_[0] += l['qty']; c_[1] += 1
        for tag, (need, n_lots, step) in per_order.items():     # one stop ORDER can protect only up to its own size
            if need > lev_num(by_tag[tag].get('qty'), 'stop quantity') + self._qty_tol(step):
                raise LevReject('stops', f'stop {tag} is shared by {n_lots} lots ({need:g}) but covers only {by_tag[tag].get("qty")}')
        # ---- working orders that could add exposure: only the bot's own maker entries (reserved below)
        bot_cids = {rec.get('cid') for rec in rest if rec.get('cid')}
        stop_tags = {l['stop_id'] for l in lots}
        for o in by_tag.values():
            if o['tag'] in stop_tags or o.get('reduce_only'): continue
            ps, sd_ = o.get('position_side'), o.get('side')
            if not (ps == 'BOTH' or (ps == 'LONG' and sd_ == 'BUY') or (ps == 'SHORT' and sd_ == 'SELL')): continue
            if o.get('client_id') and o.get('client_id') in bot_cids: continue
            raise LevReject('working_orders', f"an order on {o.get('symbol')} that can add exposure is working on Binance (not a bot entry)")
        def move(sd, mk, stop):
            """Worst loss per unit from the current mark to the stop, slippage included. A stop already through the mark
            gives no gain: it is charged its distance from the mark as slippage instead (0 when exactly at the mark)."""
            d = sd * (mk - stop)
            return d * slip if d > 0 else abs(d)
        # ---- reserved exposure: (symbol, side, notional, worst loss incl. slippage); side None = may be either (grids)
        res, filled_parts, unprotected = [], [], []
        for rec in rest:
            q = lev_num(rec['qty'], 'maker entry quantity', pos=True); plan = rec.get('plan') or {}
            f_ = lev_num(rec.get('filled', 0.0), 'maker entry filled quantity')
            dist = lev_num(plan.get('stop_dist'), 'maker entry stop distance', pos=True)
            px0 = lev_num(rec.get('price') or plan.get('px'), 'maker entry price', pos=True)
            sd = 1 if rec['side'] == 'LONG' else -1
            if f_ > 0:                                           # already filled: a position with NO stop yet (it gets one as a lot)
                fill = lev_num(rec.get('cost'), 'maker entry cost') / f_ if rec.get('cost') else px0
                filled_parts.append((rec['symbol'], sd, f_, fill - sd * dist))
                unprotected.append(f"{rec['symbol']} {rec['side']} {f_:g}")
            if q - f_ > 0:
                px = max(px0, mark_of(rec['symbol']))
                res.append((rec['symbol'], rec['side'], (q - f_) * px, (q - f_) * dist * slip))
        for l in lots:
            if l.get('manual'): continue                         # manual trades get no adds
            sd = 1 if l['side'] == 'LONG' else -1; s = l['symbol']
            if l.get('levels'):
                for k in range(int(lev_num(l.get('dca', 0), 'DCA step')), len(l['levels'])):
                    q = lev_num(l.get('q0'), 'DCA base quantity', pos=True) * lev_num(l['w'][k], 'DCA weight', pos=True)
                    lv = lev_num(l['levels'][k], 'DCA level', pos=True)
                    res.append((s, l['side'], q * max(lv, mark_of(s)), q * abs(lv - l['stop']) * slip))
            py = (l.get('mgmt') or {}).get('pyramid')
            if py:
                left = int(lev_num(py.get('n'), 'pyramid adds')) - int(lev_num(l.get('adds', 0), 'pyramid adds done'))
                q = lev_num(l.get('q0'), 'pyramid base quantity', pos=True) * lev_num(py.get('frac'), 'pyramid fraction')
                for j in range(max(0, left)):
                    pj = lev_num(l.get('next_add'), 'pyramid level', pos=True) + sd * j * lev_num(py.get('step_r'), 'pyramid step') * lev_num(l.get('R'), 'lot R')
                    res.append((s, l['side'], q * max(pj, mark_of(s)), q * max(0.0, sd * (pj - l['stop'])) * slip))
        for g in grids:
            m, k = g.get('metrics') or {}, 2 if g.get('mode') == 'neutral' else 1
            res.append((g.get('sym'), None, k * lev_num(m.get('max_notional'), 'grid notional'), k * lev_num(m.get('worst_loss_usd'), 'grid worst loss') * slip))
        # ---- current notional, mark-to-stop losses, worst-case notional per symbol
        ncur, nws, loss_lots = {}, {}, 0.0
        for (s, _), (q, nt, mk) in pos.items(): ncur[s] = ncur.get(s, 0.0) + nt
        nws.update(ncur)
        held = [(l['symbol'], 1 if l['side'] == 'LONG' else -1, l['qty'], l['stop']) for l in lots] + filled_parts
        for s, sd, q, stop in held:                              # lots and filled maker parts: current mark -> stop
            mv = move(sd, mark_of(s), stop)
            loss_lots += q * mv
            if sd == -1: nws[s] = nws.get(s, 0.0) + q * mv     # a short's notional grows to its stop
        for s, side, nt, ls in res:                              # reserved orders: notional, and a short grows to its stop
            nws[s] = nws.get(s, 0.0) + nt + (ls if side != 'LONG' else 0.0)
        nws[sym] = nws.get(sym, 0.0) + notional + risk * slip
        maint_add = 0.0
        for s, n_ in nws.items():
            if n_ <= ncur.get(s, 0.0) + 1e-9: continue
            b = self._brackets(s)
            try: maint_add += bracket_maint(b, n_) - bracket_maint(b, ncur.get(s, 0.0))
            except ValueError as e: raise LevReject('brackets', f'{s}: {e}')
        loss_res = sum(x[3] for x in res)
        gross = sum(p_[1] for p_ in pos.values()) + sum(x[2] for x in res) + notional
        eff = gross / bal
        left = bal - loss_lots - loss_res - risk * slip
        maint = mm + maint_add
        open_risk = sum(lev_num(self._lot_risk(l), 'lot risk') for l in lots)    # entry-to-stop risk: reported, not the margin basis
        for v, nm in ((gross, 'gross notional'), (eff, 'effective leverage'), (maint, 'maintenance margin'),
                      (loss_lots, 'loss to stops'), (loss_res, 'reserved loss'), (maint_add, 'bracket maintenance')):
            lev_num(v, nm)
        lev_num(left, 'balance after stops', lo=-math.inf)
        worst = maint / left if left > 0 else None
        n = dict(effective_leverage=_fin(eff, 2), worst_margin_ratio=_fin(worst), margin_balance=_fin(bal, 2),
                 balance_after_stops=_fin(left, 2), maint_after_stops=_fin(maint, 2), gross_notional=_fin(gross, 2),
                 reserved_notional=_fin(sum(x[2] for x in res), 2), loss_to_stops=_fin(loss_lots + loss_res + risk * slip, 2),
                 open_risk=_fin(open_risk, 2), positions=len(pos))
        if unprotected:
            raise LevReject('unprotected', f'a maker entry has a filled part without a stop yet ({unprotected[0]})', n)
        if eff > cap: raise LevReject('effective_leverage', f'account effective leverage {eff:.1f}x would exceed the {cap:g}x cap', n)
        if worst is None or worst > LEV_MARGIN_RATIO_MAX:
            raise LevReject('worst_margin_ratio', f"worst-case margin ratio {'over 100%' if worst is None else f'{worst:.0%}'} "
                            f'if every stop fills is above {LEV_MARGIN_RATIO_MAX:.0%}', n)
        return True, 'cross margin, within the leverage cap and the worst-case margin limit', dict(n, check=None)

    def _lev_api_refused(self, sym, err):
        """Count ONE failed leverage request (POST refused or unanswered) in lev_refusals[sym].api_refusals and keep its error:
        last_api_error/_time (+ the last_error alias) and last_api_errors (the most recent two, so a try + retry keeps both)."""
        r = self.lev_refusals.setdefault(sym, dict(count=0, proceeded=0, skipped=0))
        for k in ('by_exposure', 'cooldown_checks', 'api_refusals'): r.setdefault(k, 0)
        now = now_utc().isoformat(timespec='seconds'); msg = str(err)[:160]
        r['api_refusals'] += 1
        r['last_api_error'] = r['last_error'] = msg; r['last_api_time'] = now
        r['last_api_errors'] = (list(r.get('last_api_errors') or []) + [dict(time=now, error=msg)])[-2:]

    def _leverage_fallback(self, sym, lev, err, notional=None, risk=None, via='refused', wait=None):
        """T03a/T03c. Binance refused the leverage change (testnet answers -1000 on some coins; mainnet can refuse too, e.g.
        with open orders or venue rules), or refused it recently (via='cooldown': no POST was sent). Read-only checks decide:
        proceed if the coin is already at or below the cap (T03a), or if the exposure proof accepts the entry under cross
        margin (T03c); otherwise raise, so the entry is skipped exactly as before. Returns 'within_cap' or 'exposure'.
        lev_refusals[sym]: count = decisions (proceeded + skipped), api_refusals = EVERY failed leverage request, counted by
        _lev_api_refused at each attempt (a try + retry = 2; last_api_error/_time, last_error alias, last_api_errors = last
        two), cooldown_checks = decisions without a POST, and the last decision: outcome ('went_ahead'|'skipped'), reason (code), detail (text), via, exposure numbers."""
        r = self.lev_refusals.setdefault(sym, dict(count=0, proceeded=0, skipped=0))
        for k in ('by_exposure', 'cooldown_checks', 'api_refusals'): r.setdefault(k, 0)
        now = now_utc().isoformat(timespec='seconds')
        r['count'] += 1                                         # every decision (= proceeded + skipped), as in T03a
        if via == 'cooldown':
            r['cooldown_checks'] += 1
            head = f'leverage {lev}x refused recently (no new request during the cooldown)'   # stable; seconds: cooldown_left
        else:                                                   # the failed requests were already counted at each attempt
            head = f'leverage {lev}x refused ({err})'
        r.update(last_time=now, via=via, cooldown_left=wait if via == 'cooldown' else None, cap=lev, current=None,
                 margin_type=None, accepted=None, exposure=None)
        cur = mtype = None; read_err = None
        try: cur, mtype = self._margin_state(sym)
        except Exception as e3: read_err = str(e3)[:120]; log.info(f'{sym}: current leverage unavailable ({e3})')
        if cur is not None:
            try: cur = lev_num(cur, 'current leverage', pos=True)
            except LevReject: read_err, cur = f'invalid current leverage {cur!r}', None
            else: cur = int(cur) if cur == int(cur) else cur
        r.update(current=cur, margin_type=mtype if mtype in ('CROSSED', 'ISOLATED') else None)

        def done(ok, reason, detail):
            r.update(outcome='went_ahead' if ok else 'skipped', reason=reason, detail=str(detail)[:200])
            r['proceeded' if ok else 'skipped'] += 1
            if ok: return
            raise RuntimeError(f'{head}; {detail}')

        if cur is not None and cur >= 1 and cur <= lev:
            r['accepted'] = 'within_cap'
            log.warning(f'{sym}: {head}; the coin is already at {cur}x <= cap - entry proceeds')
            done(True, 'within_cap', f'the coin is already at {cur}x, within the {lev}x cap')
            return 'within_cap'
        if cur is None:
            done(False, 'leverage_unknown', 'current leverage unknown' + (f' ({read_err})' if read_err else ''))
        if mtype != 'CROSSED':
            done(False, 'margin_isolated' if mtype == 'ISOLATED' else 'margin_unknown',
                 f'current leverage {cur}x is above the {lev}x cap and the margin type is {mtype or "unknown"} (not cross)')
        try: ok, why, n = self._exposure_check(sym, notional, risk)
        except Exception as e4: ok, why, n = False, f'exposure check failed ({e4})', dict(check='error')
        r['exposure'] = dict({k: (_fin(v) if isinstance(v, float) else v) for k, v in n.items()}, ok=ok, why=str(why)[:200])
        if ok:
            r['by_exposure'] += 1; r['accepted'] = 'exposure'
            log.warning(f'{sym}: {head}; the coin stays at {cur}x but the entry is safe: {why} {n}')
            done(True, 'exposure_ok', why)
            return 'exposure'
        done(False, 'exposure_rejected', f'current leverage {cur}x is above the {lev}x cap and {why}')

    def _lev_exception_block(self, sym, flagged=False):
        """T03c r1. While a coin trades through the above-cap exception (its Binance leverage stayed above the cap), every
        exposure-increasing follow-up order on it - DCA/pyramid adds, grid adds, the maker entry's market fallback/remainder -
        is paused: the exception proof covered only the entry it admitted. The pause ends once the bot set the leverage
        since, or a read-only check (at most once per LEV_EXC_RECHECK_S) shows the coin at or below the cap.
        flagged: the caller's own order came through the exception (e.g. a maker plan). Returns a reason or None."""
        lots = [l for l in self.state['lots'].values() if l.get('symbol') == sym and l.get('lev_exception')]
        if not (flagged or lots): return None
        want = max(1, int(self.S['MAX_LEVERAGE']))
        back = self._lev.get(sym) == want
        if not back and not self.dry and time.time() - self._lev_exc_chk.get(sym, 0) >= LEV_EXC_RECHECK_S:
            self._lev_exc_chk[sym] = time.time()
            try:
                cur, _ = self._margin_state(sym)
                back = cur is not None and 1 <= lev_num(cur, 'current leverage', pos=True) <= want
            except Exception as e: log.info(f'{sym}: leverage re-check unavailable ({e})')
        if back:
            for l in lots: l.pop('lev_exception', None)
            return None
        return (f'{sym} leverage on Binance is still above your {want}x cap (entered through the cross-margin exception) '
                f'- orders that add exposure are paused until it is back within the cap')

    def open_lot(self, sl, sym, side, sg, df, eq, risk=None, manual=False, stop_atr=None, tp_r=None):
        block = self.entry_block(sl, sym, side, manual, sg=sg)
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
        risk = risk if risk is not None else sl['risk'] * self.kelly_mult(sl) * self.governor_mult()
        risk_usd = sleeve_eq * risk
        if 'dca' in g:                                  # martingale DCA: a hard basket stop is mandatory, depth/scale bounded
            dc = g['dca']
            if not dc.get('stop_atr') or dc['stop_atr'] <= 0:
                self.last_skip = 'DCA basket without a hard stop (stop_atr) is not allowed'; return False
            if not (1 <= int(dc.get('n', 0)) <= 8 and 1.0 <= float(dc.get('scale', 1)) <= 3.0):
                self.last_skip = 'DCA settings out of range (n 1-8, scale 1-3)'; return False
        px = self.trade.marks().get(sym) if not self.dry else sg['close']
        atr = sg['atr']
        z = F.risk_qty(g, risk_usd, px, atr, sd)        # BT02: shared sizing (DCA basket or stop_atr), same formula as before
        qty, stop, R = z['qty'], z['stop'], z['R']
        qty_raw = qty
        used = sum(l['qty'] * l['avg'] for l in self.state['lots'].values() if l['sleeve'] == ('MAN' if manual else sl['id']))
        qty = min(qty, max(0.0, self.S['MAX_LEVERAGE'] * sleeve_eq - used) / px)        # leverage cap applies to manual trades too
        d = F.size_check(qty_raw, qty, px, r)            # BT02: THE shared exchange-filter check - floors to the step, never rounds up
        qty = d['qty']
        if not d['ok']:
            log.info(f"SKIP {sym} [{sl['id'] if sl else 'MAN'}] size {qty} below Binance minimum")
            # same texts as feasibility.REASON_* (kept literal here: the trade-audit reason scan reads them from this file)
            self.last_skip = ('leverage cap reached for this slot' if d['code'] == 'leverage_cap'
                              else 'size below Binance minimum (raise capital or risk)')
            return False
        block = self.entry_block(sl, sym, side, manual, size=dict(notional=qty * px, risk=risk_usd * qty / qty_raw if qty_raw else 0), sg=sg)
        if block:
            self.last_skip = block
            if manual: raise ValueError(block)
            return False
        warns = list(self._rule_warns)
        reason = 'manual' if manual else 'signal'
        if not manual and self.S.get('AI_FILTER'):
            if df is None: df = self.candles(sym, sl['tf'])
            last = df.tail(12)[['o', 'h', 'l', 'c']].round(6).values.tolist() if df is not None else []
            try: funding = self.data.premium(sym)['lastFundingRate']
            except Exception: funding = 'n/a'
            dec, why, meta = review(dict(self.cfg, AI_FILTER='on'), sym, f"{sl['name']} {side}", dict(sg, e20=float(df.e20.iloc[-1]), e50=float(df.e50.iloc[-1]),
                                    e200=float(df.e200.iloc[-1]), st_dir=int(df.st.iloc[-1]), ret42=float(df.ret42.iloc[-1]), ret180=float(df.ret180.iloc[-1])),
                                    last, funding, tf=sl.get('tf', '4h'), side=side)
            log.info(f'AI review {sym} {side} [{sl["id"]} {sl.get("tf", "4h")}]: {dec} - {why} | {meta}')
            if dec == 'skip':
                self.log_trade(time=sg['time'], event='ai_veto', sleeve=sl['id'], symbol=sym, side=side, qty=qty, price=px, note=why)
                self.last_skip = f'Claude review vetoed: {why}'
                return False
            reason = why
        if self.dry:
            log.info(f'[dry] would open {side} {qty} {sym} stop {stop:.6g}'); return True
        try:
            how = self._ensure_leverage(sym, notional=qty * px, risk=risk_usd * qty / qty_raw if qty_raw else None)
        except Exception as e:
            self.last_skip = f'could not set leverage/margin on Binance: {e}'
            log.warning(f'{sym}: {self.last_skip}')
            if manual: raise ValueError(self.last_skip)
            return False
        for w_ in warns:                                 # risk rules in 'warn' mode: logged, the trade still goes ahead
            log.warning(f'RISK WARNING {sym} {side}: {w_}')
            if sl and not manual: self.miss(sl, sym, side, sg, 'WARNING: ' + w_, kind='warning')
        plan = dict(sl=None if manual else {k: sl[k] for k in ('id', 'key', 'tf', 'name', 'share') if k in sl}, sym=sym, side=side, qty=qty,
                    stop_dist=abs(px - stop), atr=atr, g=g, risk_usd=risk_usd, eq=eq, manual=manual, reason=reason, px=px,
                    sg=dict(time=sg.get('time'), close=sg.get('close')))
        if how == 'exposure': plan['lev_exception'] = True     # T03c r1: no adds / market remainder until leverage is within the cap
        if not manual and self.S.get('ENTRY_ORDER') == 'maker' and how != 'exposure':
            return self._maker_start(plan)
        return self._market_entry(plan)

    def _market_entry(self, plan):
        sym, side, r = plan['sym'], plan['side'], self.rules[plan['sym']]
        pb = self.persist_block()
        if pb: self.last_skip = pb; return False
        # AUD-05 r2 write-ahead: the entry is recorded DURABLY (as an unconfirmed entry with its client id) BEFORE it is sent.
        # A crash or a lost answer after the send is then settled from Binance's order record; a failed record = no send.
        cid = new_cid()
        try:
            uk = self._remember_unconfirmed(plan, cid, strict=True)
        except Exception as ex:
            self.last_skip = f'entry not sent: its write-ahead record could not be saved ({type(ex).__name__})'
            self.err(f'ENTRY {sym} {side} NOT sent - the write-ahead record could not be saved ({str(ex)[:120]})', key='wal|entry')
            return False
        t0 = time.time()
        try:
            o = self._send(self.trade.open, sym, side, self._fmt(plan['qty'], r['step']), cid=cid)
        except AmbiguousOrder as e:
            self._last_order = dict(pending=str(e)[:160])                # T05: answer lost - reconcile decides, not "failed"
            ue = self.state['unconfirmed_entries'].get(uk)
            if ue is not None and _cid_of(e.tag): ue['cid'] = _cid_of(e.tag)
            self.save_state()
            self.err(f'ENTRY {sym} {side} unconfirmed ({e}) - protected and settled from Binance as soon as it shows')
            self.last_skip = 'entry order unconfirmed'
            return False
        except Exception:
            self.state['unconfirmed_entries'].pop(uk, None)          # Binance refused it: nothing was opened
            self.save_state()
            raise
        act = float(o.get('avgPrice') or 0) or None
        fill = act or plan['px']
        got = self._exec_of(o, plan['qty'], r['step'])                    # AUD-03: exchange truth, never the request
        self._last_order = dict(rec=self._fill('entry_fallback' if plan.get('fallback') else 'entry_market', sym, side, side == 'LONG',
                                               plan['px'], act, plan['qty'], o.get('executedQty'), t0, signal_px=plan.get('signal_px'),
                                               maker_tries=plan.get('maker_tries'), fallback_order=plan.get('fallback') or None,
                                               manual=plan.get('manual') or None, **({'outcome': 'unfilled'} if got == 0 else {})))
        if got is None:                                                   # answered, not final: like a lost answer
            ue = self.state['unconfirmed_entries'].get(uk)
            if ue is not None and o.get('clientOrderId'): ue['cid'] = o.get('clientOrderId')
            self.save_state()
            self.err(f"ENTRY {sym} {side} answered {o.get('status')} (not final) - settled from Binance as soon as it shows")
            self.last_skip = 'entry order unconfirmed'
            return False
        self.state['unconfirmed_entries'].pop(uk, None)                  # answered and final: the intent becomes the lot
        if got <= 0:                                                      # EXPIRED / CANCELED with nothing executed
            log.info(f"ENTRY {sym} {side}: Binance {o.get('status')} - nothing executed, no trade opened")
            self.last_skip = 'entry order not filled'
            self.save_state()
            return False
        return self._create_lot(plan, got, fill)

    # ------------------------------------------------------------ AUD-03b: entries whose answer was lost
    # An entry whose order answer is lost (or not final) is remembered with its client order id. Every reconcile pass:
    #   1. its FINAL order record decides: executed > 0 -> booked as a lot (exact qty and price), 0 -> forgotten;
    #   2. until then, size on the exchange beyond the lots is protected AT ONCE by a provisional stop at the planned stop
    #      price (no position waits hours for the next candle), and is never reported as untracked;
    #   3. no record and no position after {UNCONF_DROP_S} s -> it never executed; a position but no record after
    #      {UNCONF_ADOPT_S} s -> adopted from the position at the mark price.
    # The lot inherits the provisional stop (_replace_stop places the real one first, then cancels it). New entries on that
    # coin/side wait until it is settled. Engine._owned_stop_tags lists every stop the bot owns (for AUD-04's verifier).
    def _remember_unconfirmed(self, plan, cid, strict=False):
        """Record an entry by its client id. strict=True (AUD-05 r2 write-ahead): raises if the record is not durable - the
        record is then removed from memory too and the caller must not send."""
        sl = plan.get('sl') or {}
        ue = self.state.setdefault('unconfirmed_entries', {})
        k = f"UE|{'MAN' if plan.get('manual') else sl.get('id')}|{plan['sym']}|{plan['side']}|{int(time.time() * 1000)}"
        while k in ue: k += '+'
        ue[k] = dict(symbol=plan['sym'], side=plan['side'], cid=cid, qty=plan['qty'], t=time.time(), prov=None, prov_qty=0.0,
                     plan=plan)
        try:
            self._save_wal() if strict else self.save_state()
        except Exception:
            ue.pop(k, None); raise
        return k

    def _owned_stop_tags(self, sym=None):
        """Every exchange stop the bot owns: lot stops and AUD-03b provisional stops (a verifier must never cancel these)."""
        tags = {l.get('stop_id') for l in self.state['lots'].values() if sym in (None, l['symbol'])}
        for u in (self.state.get('unconfirmed_entries') or {}).values():
            if sym in (None, u['symbol']):
                tags.add(u.get('prov')); tags.add((u.get('prov_pending') or {}).get('tag'))   # AUD-04 r2: lost answer = still ours
        return {t for t in tags if t}

    @staticmethod
    def _row_owned(r, owned):
        """A listed stop row is owned when its tag OR its client id (c:/ac: tags of a lost answer) is in `owned`."""
        cid = str(r.get('client_id') or '')
        return r['tag'] in owned or bool(cid and (f'c:{cid}' in owned or f'ac:{cid}' in owned))

    def _unconfirmed_claims(self):
        out = {}
        for u in (self.state.get('unconfirmed_entries') or {}).values():
            out[(u['symbol'], u['side'])] = out.get((u['symbol'], u['side']), 0.0) + u['qty']
        return out

    def _prov_set(self, uk, u, qty):
        """AUD-03 r1: keep an unconfirmed entry's provisional stop EXACTLY at the unresolved size on Binance: placed or resized
        replace-first (new stop, then cancel the old), cancelled when nothing unresolved remains. If a SHRINK cannot be placed,
        the oversized old stop is removed at once (cancelled, or parked for retry with its ownership cleared) and the gap is
        alerted and retried - so a live provisional stop is never larger than what it protects and can never close a sibling
        lot's size. A failed first placement / grow keeps the smaller old stop (conservative) and retries."""
        sym, side, r, plan = u['symbol'], u['side'], self.rules[u['symbol']], u['plan']
        if u.get('prov_pending') and not self._prov_pending_settled(sym, u): return   # r2: nothing new while it is unknown
        old = u.get('prov')
        if qty <= 0:
            if old:
                u['prov'], u['prov_qty'] = None, 0.0; self.save_state()
                self._cancel_or_park(sym, old)
                log.info(f"ENTRY {sym} {side} unconfirmed: nothing unresolved on Binance any more - provisional stop cancelled")
            return
        if old and abs(qty - u.get('prov_qty', 0.0)) < 1e-12: return
        sd = 1 if side == 'LONG' else -1
        stop = self._rd(plan['px'] - sd * plan['stop_dist'], r['tick'])
        # AUD-05 r2 write-ahead: both client ids (classic + algo fallback) are owned durably BEFORE the stop is sent; a crash
        # after the send is settled by _prov_pending_settled from the order's status. A failed record = no send.
        cid, acid = new_cid('zb'), new_cid('za')
        u['prov_pending'] = dict(tag=f'c:{cid}', alt=f'ac:{acid}', qty=qty, stop=stop, t=time.time())
        try:
            self._save_wal()
        except Exception as ex:
            u.pop('prov_pending', None)
            self.err(f"{sym} {side} unconfirmed entry: provisional stop NOT sent - its write-ahead record could not be saved "
                     f"({type(ex).__name__}); retried next pass", key=f'prov|{uk}')
            return
        try:
            tag = self._send(self.trade.stop, sym, side, self._fmt(qty, r['step']), self._fmt(stop, r['tick']), cid=cid, acid=acid)
        except AmbiguousOrder as ex:                          # AUD-04 r2 (Codex): it may be LIVE on Binance - keep owning it
            if not ex.tag: raise
            u['prov_pending'] = dict(tag=ex.tag, qty=qty, stop=stop, t=time.time())
            if old and qty < u.get('prov_qty', 0.0):          # a shrink: the old one is oversized - removed as before
                u['prov'], u['prov_qty'] = None, 0.0
                self.save_state(); self._cancel_or_park(sym, old)
            else:
                self.save_state()
            self.err(f"{sym} {side} unconfirmed entry: provisional stop answer lost ({ex.tag}) - owned as pending, no second stop "
                     "until Binance shows its status", key=f'prov|{uk}')
            return
        except Exception as ex:
            u.pop('prov_pending', None)                       # refused by Binance: nothing was placed
            if old and qty < u.get('prov_qty', 0.0):          # Codex r2: a SHRINK failed - the old stop is now oversized and could
                u['prov'], u['prov_qty'] = None, 0.0          # close a sibling's size: drop it at once (cancel, or park + retry),
                self.save_state()                             # ownership cleared durably; the unresolved part is re-protected
                self._cancel_or_park(sym, old)                # next pass
                self.err(f"{sym} {side} unconfirmed entry: resizing its provisional stop to {qty} failed ({str(ex)[:120]}) - the "
                         f"oversized stop was removed, {qty} is UNPROTECTED until the retry next pass", key=f'prov|{uk}')
                self.notify(f'⚠️ {sym} {side}: {qty} of an unconfirmed entry has NO stop for a moment (the provisional stop could '
                            'not be resized; the oversized one was removed so it cannot close another trade). Retrying now.')
            else:                                             # first placement / a grow failed: the smaller old stop (if any) stays
                self.err(f"{sym} {side} unconfirmed entry: provisional stop for {qty} failed ({str(ex)[:120]}) - retried next pass",
                         key=f'prov|{uk}')
            return
        u['prov'], u['prov_qty'], u['prov_stop'] = tag, qty, stop
        u.pop('prov_pending', None)
        self.save_state()
        if old and old != tag: self._cancel_or_park(sym, old)
        log.info(f"ENTRY {sym} {side} unconfirmed: {qty} on Binance protected by a provisional stop at {stop}")

    def _prov_pending_settled(self, sym, u):
        """AUD-04 r2: a provisional stop whose placement answer was lost (c:/ac: client-id tag). Its exchange status decides:
        working -> it becomes the provisional stop (the old one, if any, is cancelled); gone / never reached Binance ->
        forgotten; unknown -> nothing is placed, and after PROV_PENDING_S it is cancelled or parked (orphans: retried,
        never adopted) before a replacement is allowed. Returns True when settled (the caller may act)."""
        pend = u['prov_pending']; tag = pend['tag']
        try:
            st = self.trade.stop_status(sym, tag, retry=False) if hasattr(self.trade, 'stop_status') else 'UNKNOWN'
            known = st in STOP_LIVE or st in STOP_GONE or st in STOP_FIRED or (st is None and tag.startswith('c:'))
            if st is None and tag.startswith('c:') and pend.get('alt'):   # AUD-05 r2: the classic id never reached Binance -
                st2 = self.trade.stop_status(sym, pend['alt'], retry=False)   # the algo fallback id may have
                if st2 in STOP_LIVE: tag, st = pend['alt'], st2
                elif st2 is None: known = False if pend['alt'].startswith('a:') else known
                else: st = st2
        except Exception:
            st, known = None, False
        if known and st in STOP_LIVE:
            old = u.get('prov')
            u['prov'], u['prov_qty'], u['prov_stop'] = tag, pend['qty'], pend['stop']; u.pop('prov_pending', None)
            self.save_state()
            if old and old != tag: self._cancel_or_park(sym, old)
            log.info(f"{sym} {u['side']} unconfirmed entry: the provisional stop {tag} whose answer was lost is working - owned")
            return True
        if known:
            u.pop('prov_pending', None); self.save_state()
            log.info(f"{sym} {u['side']} unconfirmed entry: lost provisional stop {tag} is not working ({st}) - placing anew")
            return True
        if time.time() - pend.get('t', 0) > PROV_PENDING_S:
            if self._cancel_or_park(sym, tag):                # r3 (Codex): ONLY a confirmed cancel unlocks a replacement
                u.pop('prov_pending', None); self.save_state()
                self.err(f"{sym} {u['side']} unconfirmed entry: status of provisional stop {tag} unknown for {PROV_PENDING_S} s - "
                         "cancelled on Binance, a new one is placed", key=f"prov|{sym}|{u['side']}")
                return True
            self.save_state()                                 # parked in orphans too, but still pending: it may be LIVE, so
            self.err(f"{sym} {u['side']} unconfirmed entry: provisional stop {tag} - status unknown and its cancel failed; no "
                     "replacement until Binance confirms it gone or cancelled", key=f"prov|{sym}|{u['side']}")   # no second stop
        return False

    def _settle_unconfirmed(self, live):
        ue = self.state.get('unconfirmed_entries') or {}
        for uk, u in list(ue.items()):
            sym, side = u['symbol'], u['side']
            r = self.rules.get(sym)
            if r is None: continue
            age = time.time() - u.get('t', 0)
            plan = u['plan']
            fr = self._order_result(sym, u.get('cid'), u['qty'])
            if fr['state'] == 'final':                                     # 1. the order's final record decides
                got = fr['qty']
                ue.pop(uk, None)
                if got > 0:
                    px = fr['px'] or plan['px']
                    log.info(f"ENTRY {sym} {side} unconfirmed -> confirmed from its order record: {got} @ {px}")
                    self._create_lot(plan, got, px, adopt_stop=u.get('prov'))
                else:
                    log.info(f"ENTRY {sym} {side} unconfirmed -> its order record shows nothing executed")
                    if u.get('prov'): self._cancel_or_park(sym, u['prov'])
                self.save_state(); continue
            lots_q = sum(l['qty'] for l in self.state['lots'].values() if l['symbol'] == sym and l['side'] == side)
            rest_q = sum(x['qty'] for x in (self.state.get('resting_entries') or {}).values()
                         if x['symbol'] == sym and x['side'] == side)
            # Cowork #28 F1: a sibling lot's own unconfirmed ADD may already be on Binance before its lot grows - that size is
            # the add's, never this entry's (else it would get this entry's stop and, after 5 min, be adopted twice)
            add_q = sum((l.get('pending') or {}).get('qty', 0.0) for l in self.state['lots'].values()
                        if l['symbol'] == sym and l['side'] == side and (l.get('pending') or {}).get('kind') == 'add')
            extra = self._rd(min(u['qty'], max(0.0, live.get((sym, side), 0.0) - lots_q - rest_q - add_q)), r['step'])
            u['seen_qty'] = extra                                          # AUD-04: size this entry owns NOW (stop verifier)
            self._prov_set(uk, u, extra)                                   # 2. protect exactly what is unresolved
            if extra > 0:
                if age > UNCONF_ADOPT_S:                                   # 3b. no record in 5 min: adopt the position
                    ue.pop(uk, None)
                    px = (self.marks or {}).get(sym) or plan['px']
                    self.err(f"ENTRY {sym} {side} could not be confirmed from its order record ({fr['state']}) - adopted from "
                             f"the position ({extra} @ mark {px})")
                    self._create_lot(plan, extra, px, adopt_stop=u.get('prov'))
                    self.save_state()
            elif age > (UNCONF_DROP_S if fr['state'] in ('notfound', 'nosource') else UNCONF_ADOPT_S):   # 3a. never executed
                ue.pop(uk, None)
                if u.get('prov'): self._cancel_or_park(sym, u['prov'])
                log.info(f"ENTRY {sym} {side} unconfirmed -> nothing on Binance after {int(age)} s ({fr['state']}), forgotten")
                self.save_state()

    def _create_lot(self, plan, qty, fill, maker_qty=0.0, adopt_stop=None):
        """Record a filled entry as a lot and protect it with its exchange stop (recorded BEFORE the stop is sent)."""
        sym, side, sl, manual, g, atr = plan['sym'], plan['side'], plan['sl'], plan['manual'], plan['g'], plan['atr']
        r, sd, eq, risk_usd, reason, px = self.rules[sym], (1 if side == 'LONG' else -1), plan['eq'], plan['risk_usd'], plan['reason'], plan['px']
        stop = self._rd(fill - sd * plan['stop_dist'], r['tick'])
        key = f"{'MAN' if manual else sl['id']}|{sym}|{side}|{int(time.time())}"
        fee = maker_qty * fill * float(self.S.get('FEE_MAKER', 0.0002)) + (qty - maker_qty) * fill * FEE_EST
        lot = dict(symbol=sym, side=side, sleeve='MAN' if manual else sl['id'], key_strategy=None if manual else sl['key'],
                   qty=qty, q0=qty, avg=fill, e0=fill, entry0=fill, R=abs(fill - stop), stop=stop, stop_id=None, stop_dirty=True, tp1=False, adds=0, dca=0,
                   best=fill, atr0=atr, atr_now=atr, mgmt=g, tf=(sl['tf'] if sl else '4h'), manual=manual,
                   opened=now_utc().isoformat(timespec='seconds'), risk_usd=round(risk_usd, 2), eq_at_entry=round(eq, 2),
                   qty_max=qty, fills=[[now_utc().isoformat(timespec='seconds'), 'entry', qty, fill]], fees=fee)
        if sl and not manual and sl.get('share') is not None: lot['share'] = float(sl['share'])            # cap basis for later adds (survives slot edits)
        if maker_qty > 0: lot['maker_qty'] = maker_qty
        if plan.get('lev_exception'): lot['lev_exception'] = True    # T03c r1: entered while Binance leverage stayed above the cap
        if 'pyramid' in g: lot['next_add'] = fill + sd * g['pyramid']['step_r'] * lot['R']
        if 'dca' in g:
            lot['levels'] = [fill - sd * k * g['dca']['step_atr'] * atr for k in range(1, g['dca']['n'] + 1)]
            lot['w'] = [g['dca']['scale'] ** k for k in range(1, g['dca']['n'] + 1)]
            lot['tp'] = fill + sd * g['dca']['tp_atr'] * atr
            lot['breaker_dca'] = self.risk_rules_cfg()['btc_breaker'].get('dca', 'pause')   # fixed for the life of this basket
        try:                                                      # T05a: causal policies PREDECLARED at entry (observe only)
            ap = TA.declare(lot, lot['opened'])
            if ap is not None:
                ap['ctx'] = dict(entry=self._audit_entry_ctx(sym, lot['tf'], lot['opened']))   # regime known at entry
                lot['ap'] = ap
        except Exception: pass
        if adopt_stop: lot['stop_id'] = adopt_stop               # AUD-03b: the provisional stop is replaced (new first, then cancelled)
        self.state['lots'][key] = lot; self.save_state()          # recorded BEFORE the stop: a crash here can never orphan the position
        self._last_lot_key = key
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

    # ------------------------------------------------------------ v3.1: market data for the entry rules (cached)
    def regime_now(self):
        """BTC market regime of the last closed 4h candle: dict(bull, bear, range) - strategies.regime (shared with the backtest)."""
        df = self.candles('BTCUSDT', '4h')
        last = str(df.t.iloc[-1])
        if self._regime is None or self._regime[0] != last:
            r = S.regime(df).iloc[-1]
            self._regime = (last, dict(bull=bool(r['bull']), bear=bool(r['bear']), range=bool(r['range'])))
        return self._regime[1]

    def btc_move_1h(self):
        """|%| close-to-close move of BTC over the last closed 1h candle (refreshed at most once a minute)."""
        now = time.time()
        if self._btc1h and now - self._btc1h[0] < 60: return self._btc1h[1]
        raw = self.data.klines('BTCUSDT', '1h', 3)
        closed = [x for x in raw if float(x[6]) < now * 1000]
        mv = abs(float(closed[-1][4]) / float(closed[-2][4]) - 1) * 100 if len(closed) >= 2 else 0.0
        self._btc1h = (now, mv)
        return mv

    def funding_rate(self, sym):
        hit = self._fund.get(sym)
        if hit and time.time() - hit[0] < 300: return hit[1]
        f = float(self.data.premium(sym)['lastFundingRate'])
        self._fund[sym] = (time.time(), f)
        return f

    def _lot_risk(self, l):
        """Open risk of a lot to its stop (0 once the stop is past breakeven), incl. DCA safety orders not filled yet."""
        sd = 1 if l['side'] == 'LONG' else -1
        r = max(0.0, sd * (l['avg'] - l['stop'])) * l['qty']
        if l.get('levels'):
            for k in range(l.get('dca', 0), len(l['levels'])):
                r += l['q0'] * l['w'][k] * abs(l['levels'][k] - l['stop'])
        return r

    def _corr(self, a, b):
        c = self.corr
        if not c or a not in c.get('symbols', []) or b not in c.get('symbols', []): return None
        return c['m'][c['symbols'].index(a)][c['symbols'].index(b)]

    # ------------------------------------------------------------ v3.1: portfolio risk rules
    def _rules_eval(self, sl, sym, side, size=None, manual=False, add=False):
        """[(rule, mode, message)] for every rule (not 'off') the entry would break. Data problems never block (logged)."""
        R, out = self.risk_rules_cfg(), []
        cap = self.guard_eq or self.last_eq or 0.0
        lots = list(self.state['lots'].values())
        mk = self.marks or {}
        size = size or {}
        r = R['coin_cap']
        if r['mode'] != 'off' and cap > 0:
            tot = sum(l['qty'] * mk.get(l['symbol'], l['avg']) for l in lots if l['symbol'] == sym) + size.get('notional', 0.0)
            if tot > r['x'] * cap: out.append(('coin_cap', r['mode'], f"{sym} exposure {tot:.0f} USDT > {r['x']:g}x bot capital ({r['x'] * cap:.0f})"))
        r = R['open_risk_cap']
        if r['mode'] != 'off' and cap > 0:
            tot = sum(self._lot_risk(l) for l in lots) + size.get('risk', 0.0)
            if tot > r['pct'] / 100 * cap: out.append(('open_risk_cap', r['mode'], f"open risk to stops {tot / cap * 100:.1f}% > {r['pct']:g}% of bot capital"))
        if manual: return out
        r = R['correlated_cap']
        if r['mode'] != 'off' and self.corr and not add:     # an add does not open a new correlated position
            same = {l['symbol'] for l in lots if l['side'] == side and l['symbol'] != sym}
            n = sum(1 for s2 in same if (self._corr(sym, s2) or 0) > r['rho'])
            if n >= r['n']: out.append(('correlated_cap', r['mode'], f"{n} open {side.lower()}s on coins correlated > {r['rho']:g} with {sym} (max {r['n']})"))
        r = R['btc_breaker']
        if r['mode'] != 'off' and not add:                   # the breaker pauses NEW trades; adds follow their plan
            try:
                b = self._breaker()
                if b and b['active']:
                    out.append(('btc_breaker', r['mode'], f"BTC moved {b['move']:.1f}% in an hour - entries paused until "
                                                          f"{datetime.fromtimestamp(b['until'], timezone.utc).isoformat(timespec='minutes')}"))
            except Exception as e: log.info(f'btc breaker: data unavailable ({e})')
        r = R['funding_filter']
        if r['mode'] != 'off':
            try:
                f = self.funding_rate(sym)
                if (side == 'LONG' and f > r['rate']) or (side == 'SHORT' and f < -r['rate']):
                    out.append(('funding_filter', r['mode'], f"funding {f * 100:.3f}% against a {side.lower()} (limit {r['rate'] * 100:.3f}%)"))
            except Exception as e: log.info(f'funding filter: data unavailable ({e})')
        return out

    def _rules_block(self, sl, sym, side, size=None, manual=False):
        hits = self._rules_eval(sl, sym, side, size, manual)
        self._rule_warns = [m for _, mode, m in hits if mode == 'warn']
        enf = [f'risk rule {n}: {m}' for n, mode, m in hits if mode == 'enforce']
        return enf[0] if enf else None

    def _breaker(self):
        """BTC circuit breaker: a > pct % move in the last hour pauses entries for `hours` (enforce) or warns (warn).
        In enforce mode with tighten, every winning lot's stop moves to breakeven when it trips."""
        r = self.risk_rules_cfg()['btc_breaker']
        if r['mode'] == 'off': return None
        k = 'breaker_until' if r['mode'] == 'enforce' else 'breaker_warn_until'
        now, mv = time.time(), self.btc_move_1h()
        if mv > r['pct']:
            was = (self.state.get(k) or 0) > now
            self.state[k] = max(self.state.get(k) or 0, now + r['hours'] * 3600)
            if not was:
                log.warning(f"BTC CIRCUIT BREAKER ({r['mode']}): BTC moved {mv:.1f}% in the last hour")
                self.notify(f"⚡ BTC moved {mv:.1f}% in an hour - " + (f"entries paused for {r['hours']:g}h" if r['mode'] == 'enforce' else 'warning only'))
                if r['mode'] == 'enforce' and r.get('tighten'): self._tighten_all()
                self.save_state()
        return dict(active=(self.state.get(k) or 0) > now, move=mv, until=self.state.get(k) or 0)

    def _tighten_all(self):
        for l in list(self.state['lots'].values()):
            m = (self.marks or {}).get(l['symbol'])
            if not m or l.get('pending') or l.get('manual'): continue
            sd = 1 if l['side'] == 'LONG' else -1
            be = l['avg'] * (1 + sd * BE_BUF)
            if sd * (m - be) > 0 and sd * (be - l['stop']) > 0 and self._replace_stop(l, be):
                log.info(f"BREAKER {l['symbol']} [{l['sleeve']}] stop moved to breakeven {l['stop']}")

    def risk_rules_status(self):
        """Current values vs limits of every portfolio risk rule, for the UI."""
        R, out = self.risk_rules_cfg(), {}
        cap = self.guard_eq or self.last_eq or 0.0
        lots = list(self.state['lots'].values()); mk = self.marks or {}
        per = {}
        for l in lots: per[l['symbol']] = per.get(l['symbol'], 0.0) + l['qty'] * mk.get(l['symbol'], l['avg'])
        top = max(per.items(), key=lambda x: x[1]) if per else (None, 0.0)
        v = top[1] / cap if cap else 0.0
        out['coin_cap'] = dict(mode=R['coin_cap']['mode'], value=round(v, 2), limit=R['coin_cap']['x'], ok=v <= R['coin_cap']['x'], detail=top[0])
        v = sum(self._lot_risk(l) for l in lots) / cap * 100 if cap else 0.0
        out['open_risk_cap'] = dict(mode=R['open_risk_cap']['mode'], value=round(v, 2), limit=R['open_risk_cap']['pct'], ok=v <= R['open_risk_cap']['pct'])
        rc = R['correlated_cap']; worst, detail = 0, None
        if self.corr:
            for side in ('LONG', 'SHORT'):
                ss = sorted({l['symbol'] for l in lots if l['side'] == side})
                for a in ss:
                    n = 1 + sum(1 for b in ss if b != a and (self._corr(a, b) or 0) > rc['rho'])
                    if n > worst: worst, detail = n, f'{a} {side.lower()}'
        out['correlated_cap'] = dict(mode=rc['mode'], value=worst, limit=rc['n'], ok=worst <= rc['n'], detail=detail if self.corr else 'correlation not computed yet')
        rb = R['btc_breaker']
        try: mv = self.btc_move_1h() if rb['mode'] != 'off' else None
        except Exception: mv = None
        until = self.state.get('breaker_until' if rb['mode'] == 'enforce' else 'breaker_warn_until') or 0
        out['btc_breaker'] = dict(mode=rb['mode'], value=None if mv is None else round(mv, 2), limit=rb['pct'], ok=until <= time.time(),
                                  paused_until=datetime.fromtimestamp(until, timezone.utc).isoformat(timespec='minutes') if until > time.time() else None)
        rf = R['funding_filter']; fr = {s: v_[1] for s, v_ in self._fund.items() if time.time() - v_[0] < 3600}
        worst = max(fr.items(), key=lambda x: abs(x[1])) if fr else (None, 0.0)
        out['funding_filter'] = dict(mode=rf['mode'], value=round(worst[1], 6), limit=rf['rate'], ok=abs(worst[1]) <= rf['rate'], detail=worst[0])
        return out

    # ------------------------------------------------------------ v3.1: risk governor (equity rules)
    def _gov_rules(self):
        G = self.S.get('GOVERNOR') or {}
        out = []
        for r in G.get('rules', []) or []:
            if not isinstance(r, dict) or r.get('if') not in ('growth_gte', 'dd_gte') or not isinstance(r.get('then'), dict): continue
            out.append(r)
        return G.get('mode', 'off'), out

    def governor_tick(self):
        """Evaluate the equity rules (called from check_guards). 'suggest': notify once per trigger; 'auto': apply."""
        mode, rules = self._gov_rules()
        gs = self.state.setdefault('governor', dict(rules={}, last_switch=0, since=self.S.get('CAP_SINCE')))
        if mode not in ('suggest', 'auto') or not rules:
            gs['rules'] = {}; return
        if gs.get('since') != self.S.get('CAP_SINCE'):               # fresh capital cycle: every rule starts over
            gs['rules'] = {}; gs['since'] = self.S.get('CAP_SINCE')
        g = self.guard_eq
        if not g: return
        info = self.capital_info()
        growth = info.get('growth') or 0.0
        peak = self.state.get('peak_equity') or g
        dd = max(0.0, (1 - g / peak) * 100) if peak else 0.0
        now = time.time()
        for i, r in enumerate(rules):
            sig = json.dumps(r, sort_keys=True)
            rs = gs['rules'].get(str(i))
            if not rs or rs.get('sig') != sig: rs = gs['rules'][str(i)] = dict(sig=sig, on=False)
            cond = (growth >= r['value']) if r['if'] == 'growth_gte' else (dd >= r['value'])
            if not rs['on']:
                if not cond: continue
                rs.update(on=True, t=now, peak=peak, done=False)
                what = f"risk x{r['then']['risk_mult']}" if 'risk_mult' in r['then'] else f"profile '{r['then'].get('profile')}'"
                trig = f"{'growth' if r['if'] == 'growth_gte' else 'drawdown'} {growth if r['if'] == 'growth_gte' else dd:.1f}% >= {r['value']}%"
                if mode == 'suggest':
                    self.notify(f'🧭 Risk governor suggests {what} ({trig})'); log.info(f'GOVERNOR suggests {what} ({trig})')
                else:
                    log.warning(f'GOVERNOR {what} ({trig})'); self.notify(f'🧭 Risk governor: {what} ({trig})')
            elif r.get('until') == 'new_high':
                if g > (rs.get('peak') or peak): rs['on'] = False; log.info(f'GOVERNOR rule {i + 1} released at a new equity high')
            elif r.get('until') == 'reset':
                pass
            elif not cond:
                rs['on'] = False
            if rs['on'] and mode == 'auto' and 'profile' in r['then'] and not rs.get('done'):
                key = r['then']['profile']
                if key not in PRESETS: rs['done'] = True; self.err(f'governor: unknown profile {key}')
                elif now - (gs.get('last_switch') or 0) >= GOV_COOLDOWN_S:
                    self.apply_preset(key); gs['last_switch'] = now; rs['done'] = True
                    self.notify(f"🧭 Risk governor switched the profile to {PRESETS[key]['name']} (open trades keep their own management)")

    def governor_mult(self):
        """Risk multiplier for NEW entries from active 'auto' rules: product, clipped to 0.05-2 (martingale capped at 2x)."""
        mode, rules = self._gov_rules()
        if mode != 'auto': return 1.0
        rs = (self.state.get('governor') or {}).get('rules', {})
        m = 1.0
        for i, r in enumerate(rules):
            if 'risk_mult' in r['then'] and rs.get(str(i), {}).get('on'): m *= float(r['then']['risk_mult'])
        return float(min(GOV_MULT_MAX, max(0.05, m)))

    def governor_status(self):
        mode, rules = self._gov_rules()
        gs = self.state.get('governor') or {}
        g = self.guard_eq or 0; peak = self.state.get('peak_equity') or g
        out = dict(mode=mode, growth=(self.capital_info().get('growth') if g else None), dd=round(max(0.0, (1 - g / peak) * 100), 2) if peak else 0.0,
                   risk_mult=self.governor_mult(), last_switch=gs.get('last_switch') or None,
                   cooldown_h=round(max(0.0, GOV_COOLDOWN_S - (time.time() - (gs.get('last_switch') or 0))) / 3600, 1), rules=[], high_risk=False)
        for i, r in enumerate(rules):
            st_ = (gs.get('rules') or {}).get(str(i), {})
            m = r['then'].get('risk_mult')
            hr = m is not None and float(m) > 1 and r['if'] == 'dd_gte'          # martingale-style: more risk after losing
            out['high_risk'] = out['high_risk'] or hr
            out['rules'].append(dict(rule=r, active=bool(st_.get('on')), since=st_.get('t'), high_risk=hr,
                                     capped=m is not None and float(m) > GOV_MULT_MAX,
                                     suggestion=(mode == 'suggest' and bool(st_.get('on')))))
        return out

    # ------------------------------------------------------------ v3.1: trailing entries (checked on marks)
    def _trail_entries(self, marks):
        changed = False
        pe = self.state['pending_entries']
        for k, p in list(pe.items()):
            sl = next((x for x in self.S['SLEEVES'] if x['id'] == p['sleeve']), None)
            sg = p['sg']; sym, side = p['symbol'], p['side']
            def cancel(why):
                pe.pop(k, None); log.info(f'TRAIL ENTRY {sym} {side} [{p["sleeve"]}] cancelled: {why}')
                if sl: self.miss(sl, sym, side, sg, why)
            if sl is None:
                pe.pop(k, None); changed = True
                self._audit_cand(p['sleeve'], sym, side, sg, 'not_taken', 'trailing entry cancelled: strategy slot removed')   # T05a
                continue
            if time.time() > p['until']: cancel('trailing entry expired (no rebound in time)'); changed = True; continue
            blk = self.entry_block(sl, sym, side, sg=sg, skip_pending=k)
            if blk: cancel(f'trailing entry cancelled: {blk}'); changed = True; continue
            m = marks.get(sym)
            if not m: continue
            sd = 1 if side == 'LONG' else -1
            ext = min(p['ext'], m) if sd == 1 else max(p['ext'], m)
            if ext != p['ext']: p['ext'] = ext; changed = True
            if sd * (m - (ext + sd * p['dev'] * p['atr'])) < 0: continue
            pe.pop(k, None); changed = True
            log.info(f'TRAIL ENTRY {sym} {side} [{sl["id"]}] triggered at {m} (extreme {ext:.6g})')
            self.last_skip = ''
            try:
                ok = self.open_lot(sl, sym, side, dict(sg, close=m), None, self.last_eq or self.equity())
            except Exception as e:
                ok = False; self.err(f'trailing entry {sym} [{sl["id"]}] failed: {e}'); self.last_skip = f'order failed: {e}'
            if not ok: self.miss(sl, sym, side, sg, self.last_skip or 'order failed')
            else: self._audit_opened(sl, sym, side, sg)          # T05a: terminal 'taken' (or 'order_placed' for a maker order)
        return changed

    # ------------------------------------------------------------ v3.1: maker (post-only) entries
    # A resting entry lives in state['resting_entries'] from BEFORE its order is sent until its fills became a lot:
    # polled by client order id every manage pass, cancelled on timeout, re-priced up to MAKER_REPRICE times within
    # MAKER_WAIT_S seconds, then (MAKER_FALLBACK) the rest goes at market. Partial fills always become a lot.
    def _maker_start(self, plan):
        k = f"ME|{plan['sl']['id']}|{plan['sym']}|{plan['side']}"
        if k in self.state['resting_entries']: self.last_skip = 'an entry is already working on this coin'; return False
        rec = dict(key=k, sleeve=plan['sl']['id'], symbol=plan['sym'], side=plan['side'], qty=plan['qty'], filled=0.0, cost=0.0,
                   n=0, t0=time.time(), status='between', cid=None, plan=plan)
        self.state['resting_entries'][k] = rec
        try:
            self._maker_place(rec)
        except Exception:
            if rec['status'] != 'open': self.state['resting_entries'].pop(k, None)   # nothing was sent: forget it
            self.save_state(); raise
        self.save_state()
        self.last_skip = 'maker entry working'
        return True

    def _maker_place(self, rec):
        sym, r = rec['symbol'], self.rules[rec['symbol']]
        pb = self.persist_block()                                    # AUD-05 r2: no new resting order while state is untrusted
        if pb: raise RuntimeError(f'maker entry {sym} not sent: {pb}')
        rem = self._rd(rec['qty'] - rec['filled'], r['step'])
        bt = self.trade._req('GET', '/fapi/v1/ticker/bookTicker', dict(symbol=sym))
        px = float(bt['bidPrice'] if rec['side'] == 'LONG' else bt['askPrice'])
        cid = new_cid('zm')
        rec.setdefault('px0', px)                                    # T05: first posted price = the maker expectation
        prev = {k_: rec.get(k_) for k_ in ('cid', 'price', 'placed_t', 'status', 'n')}
        rec.update(cid=cid, price=px, placed_t=time.time(), status='open', n=rec['n'] + 1)
        try:
            self._save_wal()                             # recorded BEFORE it is sent: never unaccounted
        except Exception:
            rec.update(prev); raise                                  # AUD-05 r2: not durable -> not sent
        try:
            o = self.trade._order(dict(symbol=sym, side='BUY' if rec['side'] == 'LONG' else 'SELL', positionSide=rec['side'], type='LIMIT',
                                       timeInForce='GTX', quantity=self._fmt(rem, r['step']), price=self._fmt(self._rd(px, r['tick']), r['tick']),
                                       newClientOrderId=cid, newOrderRespType='RESULT'))
            log.info(f"MAKER entry {sym} {rec['side']} {rem} @ {px} (try {rec['n']})")
            if o and o.get('status') in ('EXPIRED', 'REJECTED', 'CANCELED', 'FILLED'): self._maker_done_order(rec, o)
        except AmbiguousOrder as e:
            self.err(f'maker entry {sym} unconfirmed ({e}) - polled by its id')     # status stays 'open': resolved by get_order
        except BinanceError as e:                                                   # e.g. -5022 post-only would take: re-price
            log.info(f'maker entry {sym} rejected: {e}'); rec['status'] = 'between'

    def _maker_done_order(self, rec, o):
        ex = float(o.get('executedQty') or 0)
        if ex > 0:
            px = float(o.get('avgPrice') or 0) or rec['price']
            rec['filled'] += ex; rec['cost'] += ex * px
        rec['status'] = 'between'

    def _maker_poll(self, k, marks):
        rec = self.state['resting_entries'][k]
        sym, r = rec['symbol'], self.rules[rec['symbol']]
        K, T = int(self.S.get('MAKER_REPRICE', 3)), float(self.S.get('MAKER_WAIT_S', 40))
        slice_s = T / (K + 1)
        # T03c r2 follow-up: a record whose plan came through the above-cap exception (only older code let one rest) is never
        # re-priced on that old approval: its resting order is cancelled now and the record finalized without a market
        # fallback - whatever filled becomes a lot through the normal finalize path.
        legacy = LEGACY_LEV_EXC_MAKER if (rec.get('plan') or {}).get('lev_exception') else None
        if rec['status'] in ('open', 'cancelling'):
            o = self.trade.get_order(sym, rec['cid'])
            age = time.time() - rec['placed_t']
            if o is None:
                if age < 20: return False                            # may not be visible yet
                rec['status'] = 'between'                            # never reached Binance
            elif o.get('status') in ('FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'EXPIRED_IN_MATCH'):
                self._maker_done_order(rec, o)
            elif rec['status'] == 'open' and (age >= slice_s or legacy):
                if legacy: log.warning(f"{sym}: {legacy} - cancelling its resting order")
                self.trade.cancel(sym, f"c:{rec['cid']}"); rec['status'] = 'cancelling'; rec['cancel_t'] = time.time()
                return True                                          # final fill read on the next pass
            elif rec['status'] == 'cancelling' and time.time() - rec.get('cancel_t', 0) > 30:
                self.trade.cancel(sym, f"c:{rec['cid']}"); rec['cancel_t'] = time.time(); return True
            else:
                return False
        rem = self._rd(rec['qty'] - rec['filled'], r['step'])
        px_ = (marks or {}).get(sym) or rec.get('price') or rec['plan']['px']
        small = rem < r['min_qty'] or rem * px_ < r['min_notional']
        if not legacy and not small and rec['n'] <= K and time.time() - rec['t0'] < T:
            self._maker_place(rec); return True
        self._maker_finalize(k, rem, small, px_, no_fallback=legacy)
        return True

    def _maker_finalize(self, k, rem, small, px, no_fallback=None):
        rec = self.state['resting_entries'].pop(k)
        plan, r = rec['plan'], self.rules[rec['symbol']]
        sl = next((x for x in self.S['SLEEVES'] if x['id'] == rec['sleeve']), None) or dict(plan['sl'], enabled=False, max_pos=0)
        sg = dict(plan['sg'], close=px)
        fallback = bool(self.S.get('MAKER_FALLBACK', True)) and not small and not no_fallback
        buy = rec['side'] == 'LONG'
        if rec['filled'] > 0:
            q = self._rd(rec['filled'], r['step']); avg = rec['cost'] / rec['filled']
            mrec = self._fill_rec('entry_maker', rec['symbol'], rec['side'], buy, rec.get('px0') or rec.get('price'), avg, plan['qty'], q,
                                  rec.get('t0'), signal_px=plan['px'], maker_tries=rec['n'])
            st = dict(fallback_skipped=no_fallback) if no_fallback and rem > 0 else {}   # fallback state, only from what actually happened
            ok = self._create_lot(plan, q, avg, maker_qty=q)
            lot = self.state['lots'].get(getattr(self, '_last_lot_key', None))
            self._audit_cand(sl, rec['symbol'], rec['side'], plan['sg'], 'taken' if ok and lot else 'not_taken',   # T05a terminal
                             None if ok and lot else (self.last_skip or 'maker lot not created'))
            if fallback and rem > 0:
                blk = (self.entry_block(sl, rec['symbol'], rec['side'], manual=True)
                       or self._lev_exception_block(rec['symbol'], bool(plan.get('lev_exception')))) if ok and lot else None
                if not (ok and lot): st['fallback_skipped'] = 'maker lot not created'
                elif blk: st['fallback_blocked'] = blk
                else:
                    st['fallback_attempted'] = True; self._last_order = None
                    try: ran = self._add_qty(lot, rem, px, 'entry_fallback')
                    except AmbiguousOrder as e: st['fallback_pending'] = str(e)[:160]; self.err(f"maker fallback {rec['symbol']}: {e}")
                    except Exception as e: st['fallback_failed'] = str(e)[:160]; self.err(f"maker fallback {rec['symbol']}: {e}")
                    else:
                        if not ran: st = dict(fallback_skipped='remainder below the Binance minimum')
                        elif self._fb_confirmed(self._last_order): st['fallback_confirmed'] = True
                        else: st['fallback_unconfirmed'] = 'Binance answered without an executed quantity'
                        self._replace_stop(lot)
            if mrec: mrec.update(st)
            self._fill_emit(mrec)
            return
        blk = (self.entry_block(sl, rec['symbol'], rec['side'], sg=sg)
               or self._lev_exception_block(rec['symbol'], bool(plan.get('lev_exception')))) if fallback else None
        mrec = self._fill_rec('entry_maker', rec['symbol'], rec['side'], buy, rec.get('px0') or rec.get('price'), None, plan['qty'], 0,
                              rec.get('t0'), outcome='unfilled', signal_px=plan['px'], maker_tries=rec['n'], fallback_blocked=blk or None)
        if mrec and no_fallback: mrec['fallback_skipped'] = no_fallback
        try:
            if fallback:
                if blk: self.miss(sl, rec['symbol'], rec['side'], sg, f'maker entry not filled; market fallback blocked: {blk}'); return
                log.info(f"MAKER entry {rec['symbol']} not filled - market fallback")
                st = dict(fallback_attempted=True); self._last_order = None
                try: ok = self._market_entry(dict(plan, px=px, fallback=True, signal_px=plan['px'], maker_tries=rec['n']))
                except Exception as e:
                    st['fallback_failed'] = str(e)[:160]
                    if mrec: mrec.update(st)
                    self._audit_cand(sl, rec['symbol'], rec['side'], plan['sg'], 'not_taken', f'order failed: {e}')   # T05a terminal
                    raise                                                # unchanged: the error propagates as before
                lo = self._last_order or {}
                if lo.get('pending'): st['fallback_pending'] = lo['pending']
                elif self._fb_confirmed(lo): st['fallback_confirmed'] = True
                elif lo.get('rec'): st['fallback_unconfirmed'] = 'Binance answered without an executed quantity'
                else: st['fallback_failed'] = (self.last_skip or 'order failed')[:160]
                if not ok and lo.get('rec'): st['fallback_note'] = (self.last_skip or 'lot not created')[:160]
                if mrec: mrec.update(st)
                if not ok: self.miss(sl, rec['symbol'], rec['side'], sg, self.last_skip or 'order failed')
                else: self._audit_cand(sl, rec['symbol'], rec['side'], plan['sg'], 'taken')   # T05a terminal: fallback lot
            else:
                self.miss(sl, rec['symbol'], rec['side'], sg, f'maker entry not filled; {no_fallback}' if no_fallback
                          else 'maker entry not filled (no market fallback)')
        finally:
            self._fill_emit(mrec)

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
            mark = self._protective_mark(lot['symbol'])
            if sd * (price - mark) >= 0: raise ValueError(f'stop must be on the losing side of the current price {mark}')
            if not self._replace_stop(lot, price, owner=True):     # AUD-04: also takes over from a stop placed outside the bot
                raise ValueError('Binance did not accept the new stop - the previous stop is still active')
            self.save_state()
            log.info(f"STOP MOVED {lot['symbol']} [{lot['sleeve']}] -> {lot['stop']}")

    def _protective_mark(self, sym):
        """T05b: the price a manual stop move is validated against. A critical read (never refused by the outage
        circuit), else the mark cache if fresh. Unknown -> refuse: a stop already through the price makes
        _replace_stop close at market, so the side check is never skipped (the previous stop stays active)."""
        why = None
        try:
            try: mk = self.trade.marks(critical=True)
            except TypeError: mk = self.trade.marks()
            if mk.get(sym): return mk[sym]
        except Exception as ex:
            why = ex
        if (self.marks or {}).get(sym) and time.time() - (self.marks_t or 0) <= MARK_FRESH_S:
            return self.marks[sym]
        raise ValueError(f'current price of {sym} unavailable ({why or "no mark"}) - stop not moved, the previous stop is still active')

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
            if self.state.get('unconfirmed_entries'):                 # AUD-03b: not a lot yet - settled + protected by reconcile
                res['unconfirmed'] = sorted(f"{u['symbol']}|{u['side']}" for u in self.state['unconfirmed_entries'].values())
            if not self.dry:
                try:
                    live = self.trade.positions()
                    syms = {k.split('|')[1] for k in res['closed'] + [f[0] for f in res['failed']]}
                    res['still_open'] = {f'{a}|{b}': q for (a, b), q in live.items() if a in syms}
                except Exception as e:
                    res['still_open'] = {'?': f'could not verify: {e}'}
            return res
