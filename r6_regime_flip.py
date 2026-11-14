"""R6-PAPER-01: r6_regime_flip_4h as a default-OFF PAPER strategy (one strategy, existing 4h runtime).

Importing this module registers NOTHING. register_for_paper() adds the PAPER entry only for mode 'paper' at an exact
Binance futures TESTNET HTTPS origin. It is evidence-driven research made runnable, not a claim of future profit.

Frozen rule (candidate source acc8d0ee research/longscout_v6.py, R1 from research/longscout_v1.py, unchanged):
  R1 on COMPLETED daily closes built from complete 4h bins (6 of 6): sma = 100-day mean, slope = sma - sma 20 days
  earlier; rising = close > sma and slope > 0; falling = close < sma and slope < 0; unknown before 120 daily closes.
  A 4h row sees every day whose close (day + 1d) is <= the row's close.
  Long entry: first 4h row whose R1 is rising. Short entry: first row whose R1 is falling. Entry = next bar (market).
  Long exit: R1 not rising (signal exit at the row close). Short exit: R1 not falling. Initial stop 3 ATR, cap 120 bars.
  No take-profit, break-even, trailing, pyramid, DCA or runner.

Deployment-fit admission (Codex R6-TNET163-FIT-D01, freeze 7a81c7fd, freeze.json sha256 50f6e767...):
  * exactly the frozen 163 symbols (SYMBOLS) - an entry on any other symbol is refused;
  * D01 liquidity at the decision bar's LAST 1h candle (t + 3h): a contiguous closed-1h run >= 720 hours ending there,
    and the median of the prior 30 COMPLETE (24-candle) UTC days' quote volume >= 10,000,000 USDT;
  * fail closed: an entry is emitted only for the freshly closed decision row, on a gap-free 4h frame, with the 1h
    data fetched up to that exact cutoff. Anything stale, gapped, unknown or unfetchable -> no entry (reason recorded).
Exits are never gated: they follow the frozen rule on the engine's frame, and the exchange stop protects the lot.
"""
import hashlib
import logging
import threading
import time
from collections import OrderedDict

import numpy as np
import pandas as pd
import strategies as S

log = logging.getLogger('zackbot')

KEY = 'r6_regime_flip_4h'
SLEEVE_ID = 'EX14'
CANDIDATE_SOURCE = 'acc8d0eea5fbc964d45d582923732e2bbb5ba322'
FIT_FREEZE = '7a81c7fd5e3ea2238fdad02a755d84a4b228faf9'
FIT_FREEZE_JSON_SHA256 = '50f6e767df1c5111b30cb96f94b76566731552cd4febc311b96711eb059c53d9'
TESTNET_ORIGINS = ('https://testnet.binancefuture.com', 'https://demo-fapi.binance.com')

# Frozen 163 (freeze.json 'symbols', sorted; ICPUSDT/TLMUSDT excluded as unavailable on public testnet).
SYMBOLS = (
    '1000BONKUSDT', '1000FLOKIUSDT', '1000LUNCUSDT', '1000PEPEUSDT', '1000RATSUSDT', '1000SATSUSDT', '1000SHIBUSDT',
    '1000XECUSDT', '1INCHUSDT', 'AAVEUSDT', 'ACEUSDT', 'ACHUSDT', 'AGLDUSDT', 'ALGOUSDT', 'ALICEUSDT', 'ANKRUSDT',
    'APEUSDT', 'API3USDT', 'APTUSDT', 'ARBUSDT', 'ARKMUSDT', 'ARKUSDT', 'ARPAUSDT', 'ARUSDT', 'ASTRUSDT', 'ATOMUSDT',
    'AUCTIONUSDT', 'AXSUSDT', 'BANDUSDT', 'BATUSDT', 'BCHUSDT', 'BEAMXUSDT', 'BELUSDT', 'BICOUSDT', 'BIGTIMEUSDT',
    'BLURUSDT', 'BNTUSDT', 'BSVUSDT', 'C98USDT', 'CAKEUSDT', 'CELOUSDT', 'CELRUSDT', 'CFXUSDT', 'CHRUSDT', 'CHZUSDT',
    'CKBUSDT', 'COMPUSDT', 'COTIUSDT', 'CRVUSDT', 'CTSIUSDT', 'CYBERUSDT', 'DASHUSDT', 'DODOXUSDT', 'DOTUSDT',
    'DUSKUSDT', 'DYDXUSDT', 'EDUUSDT', 'EGLDUSDT', 'ENAUSDT', 'ENJUSDT', 'ENSUSDT', 'ETCUSDT', 'ETHWUSDT', 'FETUSDT',
    'FILUSDT', 'FLOWUSDT', 'GALAUSDT', 'GASUSDT', 'GMTUSDT', 'GMXUSDT', 'GRTUSDT', 'GTCUSDT', 'HBARUSDT', 'HOTUSDT',
    'IDUSDT', 'ILVUSDT', 'IMXUSDT', 'INJUSDT', 'IOSTUSDT', 'IOTAUSDT', 'IOTXUSDT', 'JASMYUSDT', 'JOEUSDT', 'JTOUSDT',
    'KASUSDT', 'KAVAUSDT', 'KNCUSDT', 'KSMUSDT', 'LDOUSDT', 'LPTUSDT', 'LQTYUSDT', 'LTCUSDT', 'LUNA2USDT',
    'MAGICUSDT', 'MANAUSDT', 'MASKUSDT', 'MAVUSDT', 'MEMEUSDT', 'MINAUSDT', 'MOVRUSDT', 'MTLUSDT', 'NEARUSDT',
    'NEOUSDT', 'NMRUSDT', 'OGNUSDT', 'ONDOUSDT', 'ONEUSDT', 'ONGUSDT', 'ONTUSDT', 'OPUSDT', 'ORDIUSDT', 'PENDLEUSDT',
    'PEOPLEUSDT', 'POLYXUSDT', 'POWRUSDT', 'PYTHUSDT', 'QNTUSDT', 'QTUMUSDT', 'RIFUSDT', 'RLCUSDT', 'ROSEUSDT',
    'RSRUSDT', 'RUNEUSDT', 'RVNUSDT', 'SANDUSDT', 'SEIUSDT', 'SFPUSDT', 'SKLUSDT', 'SNXUSDT', 'SPELLUSDT', 'SSVUSDT',
    'STEEMUSDT', 'STRKUSDT', 'STXUSDT', 'SUIUSDT', 'SUPERUSDT', 'SUSHIUSDT', 'TAOUSDT', 'THETAUSDT', 'TIAUSDT',
    'TRBUSDT', 'TRXUSDT', 'TUSDT', 'TWTUSDT', 'UMAUSDT', 'UNIUSDT', 'USTCUSDT', 'VETUSDT', 'WAXPUSDT', 'WLDUSDT',
    'WOOUSDT', 'XLMUSDT', 'XMRUSDT', 'XTZUSDT', 'XVGUSDT', 'XVSUSDT', 'YFIUSDT', 'YGGUSDT', 'ZECUSDT', 'ZENUSDT',
    'ZILUSDT', 'ZROUSDT', 'ZRXUSDT',
)
SYMBOLS_SHA256 = '7a28fc2d5a4150737075484849097dda865107b6497e65583cec952e52f3fa0f'   # sha256 of the newline-joined list

STEP = pd.Timedelta(hours=4)
H1 = pd.Timedelta(hours=1)
LIQ_QV_MIN = 1e7               # D01: prior-30-complete-day median quote volume (USDT)
RUN_MIN_HOURS = 720            # D01: contiguous closed 1h candles ending at the decision bar's last hour
QV_DAYS = 30
R1_CONTIG_BARS = 128 * 6       # 4h rows that must be gap-free before an entry (covers R1's 120 daily closes + slack)
H1_LOOKBACK_DAYS = QV_DAYS + 1 # 1h fetch starts this many whole days before the decision day
H1_LIMIT = 1000                # one request (Binance weight 5); 31 days + <= 24 h = <= 768 candles
MAX_H1_FETCHES_PER_BAR = 163   # D01 fetches per decision bar (raw flips only; weight 5 each, paced); beyond -> fail closed
ENTRY_WINDOW_S = 1200          # an entry is admitted only within 20 min of the decision bar's close (next-open fill)


def symbols_sha256(symbols=None):
    return hashlib.sha256('\n'.join(SYMBOLS if symbols is None else symbols).encode('utf-8')).hexdigest()


# ------------------------------------------------------------------ frozen R1 core (verbatim from longscout_v1 @ acc8d0ee)
def _bar(t):
    t = pd.to_datetime(pd.Series(t)).reset_index(drop=True)
    step = t.diff().median() if len(t) > 1 else pd.Timedelta(hours=4)
    return t, (step if pd.notna(step) and step > pd.Timedelta(0) else pd.Timedelta(hours=4))


def daily_closes(t, c):
    """Completed daily closes from a 4h or 1d frame (4h: a day needs all 6 bins)."""
    t, step = _bar(t)
    s = pd.Series(np.asarray(c, float), index=pd.DatetimeIndex(t))
    if step >= pd.Timedelta(days=1):
        return s
    g = s.resample('1D')
    last, cnt = g.last(), g.count()
    return last[cnt == int(pd.Timedelta(days=1) / step)].dropna()


def regime_daily(dc):
    """R1 on a daily close series: +1 rising, -1 falling, 0 sideways, NaN unknown."""
    sma = dc.rolling(100, min_periods=100).mean()
    slope = sma - sma.shift(20)
    r = pd.Series(0.0, index=dc.index)
    r[(dc > sma) & (slope > 0)] = 1.0
    r[(dc < sma) & (slope < 0)] = -1.0
    r[np.arange(len(dc)) < 119] = np.nan
    r[sma.isna() | slope.isna()] = np.nan
    return r


def on_rows(daily_vals, t):
    """Map a per-day series onto bar rows: a row sees every day whose close (day + 1d) is <= the row's close."""
    t, step = _bar(t)
    left = pd.DataFrame({'ct': (t + step).astype('datetime64[ns]'), 'row': np.arange(len(t))})
    right = pd.DataFrame({'avail': (pd.DatetimeIndex(daily_vals.index) + pd.Timedelta(days=1)).astype('datetime64[ns]'),
                          'v': daily_vals.values}).sort_values('avail')
    out = pd.merge_asof(left, right, left_on='ct', right_on='avail', direction='backward').sort_values('row')
    return out['v'].values


def coin_regime(d):
    return on_rows(regime_daily(daily_closes(d.t, d.c)), d.t)


def _first(x):
    x = S.X(x)
    return x & ~x.shift(fill_value=False)


def raw_signals(d):
    """The frozen research rule (sig_regime_flip), every row, no admission. Deterministic and causal."""
    cr = pd.Series(coin_regime(d), index=d.index)
    up, dn = cr == 1, cr == -1
    return dict(le=_first(up), se=_first(dn), lx=S.X(~up), sx=S.X(~dn))


# ------------------------------------------------------------------ D01 admission (pure)
def d01_check(h1, decision_open):
    """D01 liquidity for the 4h decision bar opening at decision_open, from closed 1h candles h1 (columns t = open time,
    qv = quote volume). Uses only candles with open <= decision_open + 3h. -> (ok, reason)."""
    decision_open = pd.Timestamp(decision_open)
    last = decision_open + pd.Timedelta(hours=3)
    if h1 is None or len(h1) == 0: return False, 'D01: no 1h data'
    h = pd.DataFrame({'t': pd.to_datetime(h1['t']), 'qv': pd.to_numeric(h1['qv'], errors='coerce')})
    h = h[h.t <= last]
    if not len(h) or h.t.iloc[-1] != last: return False, 'D01: 1h candle at the decision cutoff missing'
    if not (h.t.is_unique and h.t.is_monotonic_increasing): return False, 'D01: 1h candles not strictly increasing'
    if h.qv.isna().any() or (h.qv < 0).any(): return False, 'D01: invalid 1h quote volume'
    runs = h.groupby(h.t.diff().ne(H1).cumsum()).cumcount() + 1
    run = int(runs.iloc[-1])
    if run < RUN_MIN_HOURS: return False, f'D01: contiguous 1h run {run} < {RUN_MIN_HOURS}'
    day = h.t.dt.floor('D')
    q = h.groupby(day).qv.sum().where(h.groupby(day).size() == 24)
    q = q.reindex(pd.date_range(q.index.min(), q.index.max(), freq='D'))
    med = q.rolling(QV_DAYS).median().shift()
    m = med.get(last.floor('D'), np.nan)
    if not np.isfinite(m): return False, 'D01: fewer than 30 complete prior days'
    if m < LIQ_QV_MIN: return False, f'D01: 30-day median quote volume {m:,.0f} < {LIQ_QV_MIN:,.0f}'
    return True, f'D01 ok (run {run} h, median qv {m:,.0f})'


def window_problem(decision_open, now):
    """None while an entry may still be SUBMITTED: at most ENTRY_WINDOW_S after the decision bar's close (Codex: a
    disclosed submission ceiling, not next-open fill parity). Never applied to exits or protective actions."""
    late = now - (pd.Timestamp(decision_open) + STEP).timestamp()
    if late > ENTRY_WINDOW_S: return f'decision close passed {late:.0f}s ago (> {ENTRY_WINDOW_S}s): no late entry'
    if late < 0: return 'decision bar not closed yet'
    return None


def frame_check(d, now):
    """Coverage of the engine's 4h frame for an entry decision. -> (ok, reason)."""
    if len(d) < R1_CONTIG_BARS: return False, f'4h history {len(d)} < {R1_CONTIG_BARS} rows'
    t = pd.to_datetime(d.t).reset_index(drop=True)
    expect = pd.Timestamp(int(now // 14400 - 1) * 14400, unit='s')
    if t.iloc[-1] != expect: return False, f'stale 4h frame (last {t.iloc[-1]}, expected {expect})'
    w = window_problem(expect, now)
    if w: return False, w
    tail = t.iloc[-R1_CONTIG_BARS:]
    if not (tail.diff().iloc[1:] == STEP).all(): return False, '4h frame gapped inside the R1 window'
    tail_c = pd.to_numeric(d.c, errors='coerce').iloc[-R1_CONTIG_BARS:]
    if not np.isfinite(tail_c.values).all() or (tail_c <= 0).any(): return False, 'invalid 4h closes'
    return True, 'frame ok'


# ------------------------------------------------------------------ live gate (wired only by register_for_paper)
_PAPER_ENTRY = None
_CTX = dict(resolve=None, fetch_1h=None, clock=time.time, guard=None, lot_entry=None)
_LOCK = threading.Lock()
_D01_CACHE = OrderedDict()     # (symbol, decision_open) -> (ok, reason); bounded
_FETCHES = {}                  # decision_open -> number of 1h fetches made for that bar
LAST_GATE = OrderedDict()      # symbol -> dict(time, side, ok, reason) - visibility of every gated entry decision


def _note(sym, t, side, ok, why):
    LAST_GATE[sym] = dict(time=str(t), side=side, ok=bool(ok), reason=why)
    LAST_GATE.move_to_end(sym)
    while len(LAST_GATE) > 400: LAST_GATE.popitem(last=False)
    (log.info if ok else log.warning)(f'R6 {sym} {side} @ {t}: ' + ('entry admitted - ' if ok else 'entry refused - ') + why)


def _d01(sym, decision_open):
    k = (sym, str(decision_open))
    with _LOCK:
        if k in _D01_CACHE: return _D01_CACHE[k]
        n = _FETCHES.get(str(decision_open), 0)
        if n >= MAX_H1_FETCHES_PER_BAR: return False, f'D01: 1h fetch budget ({MAX_H1_FETCHES_PER_BAR}/bar) used'
        if str(decision_open) not in _FETCHES: _FETCHES.clear()      # a new decision bar: the budget restarts
        _FETCHES[str(decision_open)] = n + 1
    fetch = _CTX['fetch_1h']
    try:
        start = pd.Timestamp(decision_open).floor('D') - pd.Timedelta(days=H1_LOOKBACK_DAYS)
        end = pd.Timestamp(decision_open) + STEP
        h1 = fetch(sym, start, end)
        out = d01_check(h1, decision_open)
    except Exception as ex:                       # network / parse failure: no entry this bar (not cached: retry allowed)
        return False, f'D01: 1h data unavailable ({type(ex).__name__})'
    with _LOCK:
        _D01_CACHE[k] = out
        while len(_D01_CACHE) > 2000: _D01_CACHE.popitem(last=False)
    return out


def gate(d):
    """Admission for an entry on the frame's last row. -> (symbol or None, ok, reason). Never raises."""
    sym = None
    try:
        resolve = _CTX['resolve']
        if resolve is None or _CTX['fetch_1h'] is None: return None, False, 'not registered for PAPER (no data context)'
        sym = resolve(d)
        if sym is None: return None, False, 'symbol of the frame unknown'
        if sym not in SYMBOLS: return sym, False, f'{sym} is not in the frozen 163 list'
        g = _CTX['guard']() if _CTX['guard'] else None          # account settings re-checked at every entry decision
        if g: return sym, False, g
        ok, why = frame_check(d, _CTX['clock']())
        if not ok: return sym, False, why
        ok, why = _d01(sym, pd.Timestamp(d.t.iloc[-1]))
        if ok:                                                    # the fetch may have waited: window re-checked after D01
            w = window_problem(pd.Timestamp(d.t.iloc[-1]), _CTX['clock']())
            if w: return sym, False, w
        return sym, ok, why
    except Exception as ex:
        return sym, False, f'gate error ({type(ex).__name__})'


def deadline_exits(d, lx, sx):
    """Canonical R6 time exit: research exits when i - i_entry >= MAX_BARS, i.e. at the close of the row that opens at
    entry_bar_open + MAX_BARS x 4h, from the lot's OWN entry bar - not from wall-clock time since the fill. A row at or
    after that open closes the lot (a late or restarted cycle exits at its first chance). Never raises; a lot that cannot
    be resolved keeps the engine's max_bars backstop."""
    try:
        f, res = _CTX['lot_entry'], _CTX['resolve']
        if f is None or res is None or not len(d): return lx, sx
        sym = res(d)
        hit = f(sym) if sym else None
        if not hit: return lx, sx
        side, entry_open = hit
        due = pd.to_datetime(d.t).values >= np.datetime64(pd.Timestamp(entry_open) + MAX_BARS * STEP)
        due = pd.Series(due, index=d.index)
        if side == 'LONG': lx = lx | due
        elif side == 'SHORT': sx = sx | due
    except Exception:
        pass
    return lx, sx


def signal(d, p, ctx):
    """PAPER signal: frozen rule; entries only on the freshly closed decision row and only when admitted."""
    raw = raw_signals(d)
    le = pd.Series(False, index=d.index); se = pd.Series(False, index=d.index)
    for key, side, out in (('le', 'long', le), ('se', 'short', se)):
        if len(d) and bool(raw[key].iloc[-1]):
            sym, ok, why = gate(d)
            _note(sym or '?', d.t.iloc[-1], side, ok, why)
            if ok: out.iloc[-1] = True
    lx, sx = deadline_exits(d, raw['lx'], raw['sx'])
    return dict(le=le, se=se, lx=lx, sx=sx)


RESEARCH = dict(name='[RESEARCH] R6A Coin R1 regime flip L/S 4h', desc='Frozen research rule (acc8d0ee).',
                style='trend', fn=lambda d, p, ctx: raw_signals(d), sides='both', tf='4h', research_only=True,
                params={}, mgmt=dict(stop_atr=3.0, max_bars=120))
# Time exit. The research exits when i - i_entry >= 120 (close of bar entry+120). The engine's own max_bars counts
# WALL-CLOCK bars since the fill, which drifts with cycle delays (one bar early or late). The canonical deadline is
# therefore an R6 exit signal from the lot's recorded entry bar (deadline_exits). The engine's max_bars stays only as a
# protective BACKSTOP that can never fire before the canonical deadline: an entry is submitted at most ENTRY_WINDOW_S
# after its bar opens, so wall-clock bars at the close of entry+k are >= k + 1 - 1/12 and 124 needs k >= 123.
MAX_BARS = 120
BACKSTOP_BARS = 124
PAPER_MGMT = dict(stop_atr=3.0, max_bars=BACKSTOP_BARS)


def register_for_paper(mode, base, resolve, fetch_1h, clock=time.time, guard=None, lot_entry=None):
    """Register the PAPER entry only for mode 'paper' at an exact testnet HTTPS origin, with the data context the gate
    needs (resolve: frame -> symbol; fetch_1h(symbol, start, end) -> DataFrame t, qv of CLOSED 1h candles)."""
    global _PAPER_ENTRY, _EXITS_ONLY
    if mode != 'paper' or base not in TESTNET_ORIGINS:
        raise ValueError('R6 registration requires PAPER and an exact testnet HTTPS origin')
    if not callable(resolve) or not callable(fetch_1h): raise ValueError('R6 registration needs resolve and fetch_1h')
    if symbols_sha256() != SYMBOLS_SHA256 or len(SYMBOLS) != 163 or list(SYMBOLS) != sorted(set(SYMBOLS)):
        raise ValueError('R6 frozen symbol list altered')
    existing = S.STRATEGIES.get(KEY)
    if existing is not None and existing is _EXITS_ONLY: existing = None    # the fail-safe is upgraded, never kept
    elif KEY in S.STRATEGIES and not (_PAPER_ENTRY is not None and existing is _PAPER_ENTRY):
        raise ValueError('R6 strategy key already registered')
    entry = existing or dict(name='[PAPER CANARY] R6 regime flip L/S 4h', desc='Frozen R1 regime flip; default-OFF canary.',
                             style='trend', fn=signal, sides='both', tf='4h', research_only=False, params={},
                             mgmt=dict(PAPER_MGMT))
    _CTX.update(resolve=resolve, fetch_1h=fetch_1h, clock=clock, guard=guard, lot_entry=lot_entry)
    S.STRATEGIES[KEY] = entry
    _PAPER_ENTRY = entry
    _EXITS_ONLY = None
    return entry


_EXITS_ONLY = None


def register_exits_only(reason, resolve=None, lot_entry=None):
    """Fail-safe when a saved EX14 slot or lot refers to KEY but the PAPER install did not happen / failed: without a
    registered key the engine's compute_signals raises KeyError and EVERY 4h slot stops getting signals and exits. This
    entry never enters (le/se False) and keeps the frozen exits. Needs no origin: it cannot add risk.
    resolve / lot_entry: the READ-ONLY context the canonical deadline exit needs (frame -> symbol, symbol -> lot entry
    bar). Given ones replace the current ones (a fresh process has none); omitted ones keep what is there. They never
    enable a fetch or an entry: fetch_1h and guard are always cleared."""
    global _EXITS_ONLY, _PAPER_ENTRY
    cur = S.STRATEGIES.get(KEY)
    if cur is not None and cur is not _EXITS_ONLY and cur is not _PAPER_ENTRY:
        return cur                                                       # a foreign entry is never replaced here
    _PAPER_ENTRY = None                                                  # an owned PAPER entry is DOWNGRADED
    _CTX.update(fetch_1h=None, guard=None)                               # no entry path
    if callable(resolve): _CTX['resolve'] = resolve                      # deadline context for exits only
    if callable(lot_entry): _CTX['lot_entry'] = lot_entry
    if cur is not None and cur is _EXITS_ONLY: return cur

    def exits_only(d, p, ctx):
        raw = raw_signals(d)
        z = pd.Series(False, index=d.index)
        lx, sx = deadline_exits(d, raw['lx'], raw['sx'])
        return dict(le=z, se=z.copy(), lx=lx, sx=sx)
    entry = dict(name='[EXITS ONLY] R6 regime flip L/S 4h', desc=f'Entries off: {reason}'[:200], style='trend',
                 fn=exits_only, sides='both', tf='4h', research_only=False, params={}, mgmt=dict(PAPER_MGMT))
    S.STRATEGIES[KEY] = entry
    _EXITS_ONLY = entry
    log.warning(f'R6: registered EXITS ONLY ({reason})')
    return entry


def unregister():
    """Remove the PAPER / exits-only entry and its data context (tests). The overlay's rollback refuses this while a slot
    or lot still refers to KEY. Safe when not registered."""
    global _PAPER_ENTRY, _EXITS_ONLY
    if _EXITS_ONLY is not None and S.STRATEGIES.get(KEY) is _EXITS_ONLY: del S.STRATEGIES[KEY]
    _EXITS_ONLY = None
    if _PAPER_ENTRY is not None and S.STRATEGIES.get(KEY) is _PAPER_ENTRY: del S.STRATEGIES[KEY]
    _PAPER_ENTRY = None
    _CTX.update(resolve=None, fetch_1h=None, clock=time.time, guard=None, lot_entry=None)
    with _LOCK: _D01_CACHE.clear(); _FETCHES.clear()
    LAST_GATE.clear()
