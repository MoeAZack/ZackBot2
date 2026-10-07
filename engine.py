"""ZackBot live engine — runs any mix of strategies ("sleeves") from strategies.py on Binance USD-M futures.

Hard stops live on Binance (STOP_MARKET per lot). Softer management (partial take-profit, breakeven,
trailing, pyramiding adds, DCA safety orders, basket take-profit) is checked every few seconds on the mark price.
Signals (entries/exits) are evaluated right after each candle close of the sleeve's timeframe.
Hedge mode is used so longs and shorts on the same coin can coexist.
"""
import atexit, csv, json, math, os, queue, re, time, logging, threading, copy, collections
from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd

import strategies as S
import trade_audit as TA
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
# Owner decision 2026-10-07 (label only - presets are not edited): the DCA slots of a profile stay in it but open nothing
# while the DCA_ENABLED setting is off (entry_block); the app shows S.DCA_OFF_LABEL on them.
for _p in PRESETS.values():
    _p['dca_slots'] = [_s['id'] for _s in _p['sleeves'] if S.uses_dca(_s)]
del _p
DCA_PAUSED = S.DCA_PAUSED_REASON

GLOBAL_DEFAULTS = dict(COMPOUND=False, CAP_SINCE='', CAP_ADJ=[], CAP_CYCLES=[], TELEGRAM_ON=False, TELEGRAM_TOKEN='', TELEGRAM_CHAT='', MAX_LEVERAGE=10, DAILY_LOSS_HALT=0.08, PEAK_DD_FLATTEN=0.0, CAPITAL_CAP=500.0,
                       ENTRIES_PAUSED=False, AI_FILTER=False, PRESET='original',
                       DCA_ENABLED=S.DCA_ENABLED_DEFAULT,   # owner decision 2026-10-07: DCA off (see strategies.DCA_OFF_LABEL)
                       UNIVERSE=list(TOP40), SYMBOLS_ON={}, RUN_IN_BACKGROUND=True,
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


def save_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f: json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


INCIDENT_IDLE_S = 900                 # T05b: a repeat after 15 quiet minutes starts a new entry
INCIDENT_MAX = 200                    # T05b: incidents kept (oldest dropped first, closed before open)
INCIDENT_LOCK = threading.RLock()     # T05b final: err()/resolve()/sweep run on the loop thread AND the HTTP thread
MANAGE_ALERT_REARM_S = 900            # T05b final: the same management-failure alert is not re-sent within 15 min (flapping)
OUTAGE_ADD = 'Binance outage - no adds until it answers again'
MARK_FRESH_S = 120                    # T05b: a cached mark this recent may validate a manual stop move when Binance is down
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
        self.state.setdefault('pending_entries', {}); self.state.setdefault('resting_entries', {})
        for l in self.state['lots'].values():           # lots saved by older versions may hold half-filled dca/pyramid blocks
            if l.get('key_strategy') in S.STRATEGIES and isinstance(l.get('mgmt'), dict):
                l['mgmt'] = S.merge_mgmt(l['key_strategy'], {k: v for k, v in l['mgmt'].items()})
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
        self.connect_retry = None                 # T05b: {'n', 'next_t', 'since'} while start-up connect waits for Binance
        self.marks, self.marks_t = {}, 0.0
        self.guard_eq = None                      # bot capital used by the safety limits and shown in the app
        self.untracked = {}                       # exchange positions the engine has no record of
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
        self.grids = GRID.GridManager(self)

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
        if not isinstance(s.get('RISK_RULES'), dict): s['RISK_RULES'] = {}
        if not isinstance(s.get('GOVERNOR'), dict): s['GOVERNOR'] = dict(mode='off', rules=[])
        s.setdefault('GRID_SLOTS', [])
        s['DCA_ENABLED'] = s.get('DCA_ENABLED', S.DCA_ENABLED_DEFAULT) is True     # owner decision 2026-10-07: only an explicit True runs DCA
        self.S = s

    def risk_rules_cfg(self):
        """RISK_RULES merged over the defaults (a partial setting only overrides what it names)."""
        out = {}
        for k, d in RISK_RULE_DEFAULTS.items():
            v = (self.S.get('RISK_RULES') or {}).get(k) or {}
            out[k] = dict(d, **(v if isinstance(v, dict) else {}))
            if out[k]['mode'] not in ('off', 'warn', 'enforce'): out[k]['mode'] = 'warn'
        return out

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
        return S.merge_mgmt(sl['key'], sl.get('mgmt'))

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
                if self._exchange_down(e): raise          # T05b: Binance unreachable -> the whole connect is retried later
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
        save_json(self.F['equity'], self.equity_hist)

    def log_trade(self, **row):
        new = not os.path.exists(self.F['trades'])
        with open(self.F['trades'], 'a', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['time', 'event', 'sleeve', 'symbol', 'side', 'qty', 'price', 'stop', 'pnl', 'equity', 'note'])
            if new: w.writeheader()
            w.writerow({k: row.get(k, '') for k in w.fieldnames})

    def save_state(self):
        save_json(self.F['state'], self.state)

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
            lot['last_order_t'] = t0 = time.time()
            try:
                o = self.trade.close(sym, lot['side'], self._fmt(qty, r['step']))
            except AmbiguousOrder as e:
                lot['pending'] = dict(kind='close', qty=qty, px=px, why=why, post=post or {}, t=time.time())
                self.save_state(); self.err(f'{sym} close unconfirmed ({e}) - waiting for Binance position to confirm'); raise
            act = float(o.get('avgPrice') or 0) or None
            self._fill('exit', sym, lot['side'], lot['side'] == 'SHORT', mark, act, qty, o.get('executedQty'), t0, reason=why)
            px = act or px
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
        self._audit_fill(lot)                                      # T05a: cohort / runner events (memory + queue only)
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
            try: qty = self._positions_critical().get((lot['symbol'], lot['side']), qty) or qty
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
        try:                                             # T05a: causal trade audit record (observe only, non-blocking writer)
            au = TA.close_record(lot, net, fee_rate=FEE_EST, path=TA.recorded_path(lot))
            if au: au.update(kind='trade_audit', id=key, exit_reason=why, closed=rec['closed'], t=rec['closed']); self._audit_emit(au)
        except Exception as e: log.warning(f'trade audit not recorded ({e})')
        self.history.append(rec); self.history = self.history[-3000:]
        save_json(self.F['history'], self.history)
        self.notify(f"{'✅' if net > 0 else '❌'} CLOSED {lot['side']} {lot['symbol']} [{lot['sleeve']}] {why} · PnL {net:+.2f} USDT ({rec['r']}R) · {rec['hours']}h")

    def miss(self, sl, sym, side, sg, reason, kind=None):
        """Record a signal that was not taken (kind None) or a risk-rule warning on a trade that WAS taken (kind 'warning')."""
        k = (sl['id'], sym, sg['time'], kind)
        if any((m['sleeve'], m['symbol'], m['candle'], m.get('kind')) == k for m in self.missed[-200:]): return
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
        save_json(self.F['missed'], self.missed)

    def _add_qty(self, lot, q, px, why, post=None):
        r = self.rules[lot['symbol']]
        q = self._rd(q, r['step'])
        if q < r['min_qty'] or q * px < r['min_notional']: return False
        if not self.dry:
            lot['last_order_t'] = t0 = time.time()
            try:
                o = self.trade.open(lot['symbol'], lot['side'], self._fmt(q, r['step']))
            except AmbiguousOrder as e:
                lot['pending'] = dict(kind='add', qty=q, px=px, why=why, post=post or {}, t=time.time())
                self.save_state(); self.err(f"{lot['symbol']} {why} unconfirmed ({e}) - waiting for Binance position to confirm"); raise
            act = float(o.get('avgPrice') or 0) or None
            self._last_order = dict(rec=self._fill(why if why in self.FILL_KINDS else 'pyramid_add', lot['symbol'], lot['side'], lot['side'] == 'LONG',
                                    px, act, q, o.get('executedQty'), t0, reason=why, fallback_order=(why == 'entry_fallback') or None))
            px = act or px
        self._apply_add(lot, q, px, why)
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
                try:                                           # T05a: excursions, path, state, online policies (observe only)
                    t_ = now_utc().isoformat(timespec='seconds')   # memory only: NO save_state / disk / network here; the
                    rs = getattr(self, '_audit_restored', set())    # tracking state rides on the normal save cadence and a
                    TA.observe(lot, m, t_, FEE_EST, restored=key in rs, missing=aud[0], down_last=aud[1])
                    if isinstance(lot.get('ex'), dict): rs.discard(key)   # only once observe() really started tracking
                    self._audit_emit(TA.checkpoint(lot, key, t_))  # bounded checkpoint event goes to the writer queue
                except Exception: pass                         # audit never raises into a trading path
                try:
                    if lot.get('force_close'):                 # a stop update found price already through the stop
                        self.close_lot(key, 'stop_crossed', m); changed = True; continue
                    if lot.get('stop_dirty') or not lot.get('stop_id'):   # protection missing/outdated -> retry every pass
                        if self._replace_stop(lot): changed = True; log.info(f"{lot['symbol']} [{lot['sleeve']}] stop restored")
                    if 'dca' in g and lot.get('levels'):
                        while lot['dca'] < len(lot['levels']) and sd * (lot['levels'][lot['dca']] - m) >= 0:
                            q = lot['q0'] * lot['w'][lot['dca']] * (self._breaker_add_mult(lot) or 1.0)   # 0 -> the gate blocks it
                            if self._add_gate(lot, q, m, 'safety_order') or not self._add_qty(lot, q, m, 'safety_order', post={'dca': lot['dca'] + 1}): break
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
                        if not self._add_gate(lot, lot['q0'] * g['pyramid']['frac'], m, 'pyramid_add'):
                            if self._add_qty(lot, lot['q0'] * g['pyramid']['frac'], m, 'pyramid_add',
                                             post={'adds': lot['adds'] + 1, 'next_add': lot['next_add'] + sd * g['pyramid']['step_r'] * lot['R']}):
                                lot['adds'] += 1; lot['next_add'] += sd * g['pyramid']['step_r'] * lot['R']
                                self._replace_stop(lot); changed = True
                    if g.get('tp1_r') and not lot['tp1'] and ge(lot['e0'] + sd * g['tp1_r'] * lot['R']):
                        self._market_close(lot, lot['qty'] * g.get('tp1_frac', 0.5), 'take_profit_1', m, post={'tp1': True})
                        lot['tp1'] = True; changed = True
                        if lot['qty'] <= 0: self._finish(key, 'take_profit_1'); continue
                        self._replace_stop(lot)
                    tps = S.norm_tps(g.get('tps'))
                    if tps:                                    # take-profit ladder: each level fires once (fraction of the full size)
                        done, fired = list(lot.get('tps_done', [])), False
                        rr = self.rules.get(lot['symbol'], dict(min_qty=0, min_notional=0))
                        for k_, (r_, f_) in enumerate(tps):
                            if k_ in done: continue
                            if not ge(lot['e0'] + sd * r_ * lot['R']): break
                            q = min(lot['qty'], lot.get('qty_max', lot['q0']) * f_)
                            rest = lot['qty'] - q
                            if rest > 0 and (rest < rr['min_qty'] or rest * m < rr['min_notional']): q = lot['qty']   # never leave dust
                            done.append(k_)
                            self._market_close(lot, q, 'take_profit_ladder', m, post={'tps_done': list(done)})
                            lot['tps_done'] = list(done); fired = changed = True
                            if lot['qty'] <= 0: break
                        if lot['qty'] <= 0: self._finish(key, 'take_profit_ladder'); continue
                        if fired: self._replace_stop(lot)
                    if g.get('tp_r') and not g.get('runner') and not g.get('ttp') and ge(lot['e0'] + sd * g['tp_r'] * lot['R']):
                        self.close_lot(key, 'take_profit', m); changed = True; continue
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
                            if self._replace_stop(lot, tgt):
                                changed = True
                                if run: log.info(f"RUNNER {lot['symbol']} [{lot['sleeve']}] stop raised to {lot['stop']}")
                except Exception as e:
                    ok = False; self.err(f'manage {key}: {e}')
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
            for l in lots:
                if l['symbol'] != sym: continue
                key = f"stop-unseen|{sym}|{l['side']}"
                if l['stop_id'] not in tags:
                    self.err(f"{sym} {l['side']}: stop {l['stop_id']} not seen among Binance open orders after the outage - "
                             'check this position\'s protection (nothing was changed)', key=key)
                else:
                    self.resolve(key, f"{sym} {l['side']}: stop seen among open orders again")
        return waiting

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
        if st.get('halted'): return 'daily loss halt is active'
        if Sg.get('ENTRIES_PAUSED'): return 'entries paused'
        if self.exchange_state().get('state') == 'outage': return OUTAGE_ADD     # T05b final: an add is new exposure
        if lot.get('stop_dirty'): return 'stop not confirmed yet'
        if lot.get('manual'): return 'manual trade (no adds)'
        if lot.get('levels') and not Sg.get('DCA_ENABLED'): return 'DCA paused (owner decision)'   # open basket: stop/TP/exits keep running
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
            self.missed.append(rec); self.missed = self.missed[-600:]; save_json(self.F['missed'], self.missed)
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
        for (sym, side), have in live.items():
            if (sym, side) not in groups and have > 0:
                tol = self.rules[sym]['step'] if sym in self.rules else 1e-9
                if have > tol + resting.get((sym, side), 0.0) and not dust(sym, have): untracked[(sym, side)] = have
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
        if not self.connected or self.error: return 'not connected to Binance'
        if self.exchange_state().get('state') == 'outage': return 'Binance outage - no new entries until it answers again'
        if not SYM_RE.match(sym or '') or sym not in self.rules: return f'{sym} is not tradable'
        if side not in ('LONG', 'SHORT'): return 'bad direction'
        if side == 'SHORT' and not self.hedge: return 'hedge mode off (shorts unavailable)'
        if st.get('halted'): return 'daily loss halt is active'
        if any(l['symbol'] == sym and l['side'] == side and l.get('stop_dirty') for l in st['lots'].values()):
            return 'an open trade on this coin is waiting for its stop to be confirmed'
        if f'{sym}|{side}' in self.untracked: return 'Binance holds an untracked position on this coin/side - resolve it first'
        if manual: return self._rules_block(sl, sym, side, size, manual=True)
        if Sg.get('ENTRIES_PAUSED'): return 'entries paused'
        if not Sg['SYMBOLS_ON'].get(sym, True): return 'coin switched off'
        if not sl.get('enabled', True): return 'strategy slot switched off'
        if not Sg.get('DCA_ENABLED') and S.uses_dca(sl): return 'DCA paused (owner decision)'   # = DCA_PAUSED (owner decision 2026-10-07)
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
            tol = float((self.rules.get(k_[0]) or {}).get('step', 1e-9)) * (len(groups.get(k_, [])) + 1)
            if have < exp - tol or have > exp + tol + resting.get(k_, 0.0):
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
            if lev_num(o.get('qty'), f'{nm} stop quantity') < l['qty'] - step:
                raise LevReject('stops', f'{nm}: stop covers less than the position')
            tick = float((self.rules.get(l['symbol']) or {}).get('tick', 1e-8))
            if abs(lev_num(o.get('stop_price'), f'{nm} stop price', pos=True) - l['stop']) > tick * 1.001 + 1e-12:
                raise LevReject('stops', f"{nm}: stop on Binance is at {o.get('stop_price')}, the bot expects {l['stop']}")
            c_ = per_order.setdefault(l['stop_id'], [0.0, 0, step]); c_[0] += l['qty']; c_[1] += 1
        for tag, (need, n_lots, step) in per_order.items():     # one stop ORDER can protect only up to its own size
            if need > lev_num(by_tag[tag].get('qty'), 'stop quantity') + step * n_lots:
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
        t0 = time.time()
        try:
            o = self.trade.open(sym, side, self._fmt(plan['qty'], r['step']))
        except AmbiguousOrder as e:
            self._last_order = dict(pending=str(e)[:160])                # T05: answer lost - reconcile decides, not "failed"
            self.err(f'ENTRY {sym} {side} unconfirmed ({e}) - reconcile will flag it if it filled')
            self.last_skip = 'entry order unconfirmed'
            return False
        act = float(o.get('avgPrice') or 0) or None
        fill = act or plan['px']
        filled = float(o.get('executedQty') or 0)
        self._last_order = dict(rec=self._fill('entry_fallback' if plan.get('fallback') else 'entry_market', sym, side, side == 'LONG',
                                               plan['px'], act, plan['qty'], o.get('executedQty'), t0, signal_px=plan.get('signal_px'),
                                               maker_tries=plan.get('maker_tries'), fallback_order=plan.get('fallback') or None,
                                               manual=plan.get('manual') or None))
        qty = self._rd(filled, r['step']) if filled > 0 else plan['qty']
        return self._create_lot(plan, qty, fill)

    def _create_lot(self, plan, qty, fill, maker_qty=0.0):
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
        rem = self._rd(rec['qty'] - rec['filled'], r['step'])
        bt = self.trade._req('GET', '/fapi/v1/ticker/bookTicker', dict(symbol=sym))
        px = float(bt['bidPrice'] if rec['side'] == 'LONG' else bt['askPrice'])
        cid = new_cid('zm')
        rec.setdefault('px0', px)                                    # T05: first posted price = the maker expectation
        rec.update(cid=cid, price=px, placed_t=time.time(), status='open', n=rec['n'] + 1)
        self.save_state()                                            # recorded BEFORE it is sent: never unaccounted
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
            if not self._replace_stop(lot, price): raise ValueError('Binance did not accept the new stop - the previous stop is still active')
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
            if not self.dry:
                try:
                    live = self.trade.positions()
                    syms = {k.split('|')[1] for k in res['closed'] + [f[0] for f in res['failed']]}
                    res['still_open'] = {f'{a}|{b}': q for (a, b), q in live.items() if a in syms}
                except Exception as e:
                    res['still_open'] = {'?': f'could not verify: {e}'}
            return res
