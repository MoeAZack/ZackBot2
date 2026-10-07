"""ZackBot RANGE-MARKET bots: futures GRID (long / short / neutral) and COMBO (DCA entry + grid take-profits).

Optional, toggled strategy slots with explicit worst-case risk. Everything in this module is shared by the backtest
and the live engine: the grid math (build_grid / risk_metrics / validate_cfg / triggers) is pure and used by both.

==================================================================================================================
GRID MODEL
------------------------------------------------------------------------------------------------------------------
* A range [lo, hi] (from ATR, %, or the high/low of the last N candles) is cut into `levels` cells (Binance "grid
  number"): arithmetic or geometric spacing. Cell k = (a_k, b_k).
* Cell kinds:  long mode: every cell is a LONG cell;  short mode: every cell is a SHORT cell;
  neutral: cells below the start price are LONG cells, cells above are SHORT cells (hedge mode: both sides at once).
* LONG cell : empty & price <= a -> buy q        filled & price >= b -> sell q   (one completed cycle)
  SHORT cell: empty & price >= b -> short q      filled & price <= a -> cover q
  Long mode buys the cells above the start price at start (Binance-style), short mode shorts the cells below.
* Size: every cell has the same notional; with ALL cells of one side filled the notional is
  capital_frac x slot capital  (capital_frac = notional multiple of the grid's capital, <= max_lev).
* Hard stop beyond the range: stop_lo = lo x (1 - stop_out_pct%), stop_hi = hi x (1 + stop_out_pct%).
  Live: one exchange STOP_MARKET per inventory lot (LONG lot at stop_lo, SHORT lot at stop_hi), sized to the lot.
  Price beyond stop_lo / stop_hi -> the grid closes everything and stops.
* Regime (need_range, default on): a grid starts only while the coin's own 4h `range` flag of strategies.regime() is on
  (ADX < 20 and Bollinger-width percentile < 0.35); it closes everything and stops when the flag stays off for
  `trend_bars` candles. need_range off = no regime gate and no trend stop (the hard range stop still applies).
* Worst case (risk_metrics.worst_loss_usd): all cells of one side filled, then the hard stop, incl. fees+slippage.

COMBO MODEL (backtest only here; live = an engine DCA slot with a TP ladder, see the research note in the report)
* Dip entry when (EMA20 - close) / ATR > dip_atr, safety orders every so_step_atr ATR below (weights so_scale^k),
  tp_n take-profits of 1/tp_n of the max size every tp_step_atr ATR above the average, each re-buys when price comes
  back to the level below it (grid behaviour), hard basket stop stop_atr ATR under the last safety order.

==================================================================================================================
LIVE: GridManager(engine) - integration (engine.py / app.py are NOT edited by this module; add these lines)
------------------------------------------------------------------------------------------------------------------
1) engine.py, top:                      import grid as GRID
   Engine.__init__ (after self.health = ...):
                                        self.grids = GRID.GridManager(self)
   load_settings(), after `s = copy.deepcopy(GLOBAL_DEFAULTS)` handling (anywhere before self.S = s):
                                        s.setdefault('GRID_SLOTS', [])
2) Engine.manage(), right AFTER the `for key in list(self.state['lots']): ...` loop and BEFORE `if ok:`:
            try: changed = self.grids.on_marks(marks) or changed
            except Exception as e: ok = False; self.err(f'grid: {e}')
3) Engine.cycle(tf), right BEFORE `st['last_cycle'][tf] = ...`:
            try: self.grids.on_cycle(tf)
            except Exception as e: self.err(f'grid cycle {tf}: {e}')
4) app.py loop():
     - manage must also run while a grid waits flat:   if e.state['lots'] or e.state.get('grids'): e.manage(marks)
     - candle cycles for grid timeframes:  tfs = sorted({...existing...} | {g['tf'] for g in e.S.get('GRID_SLOTS', [])})
5) app.py handle():
     if path == '/api/grid_slots':                     # body {'slots': [...]}
         if not isinstance(b.get('slots'), list) or len(b['slots']) > 6: raise ValueError('0-6 grid slots')
         slots = [GRID.validate_slot(x, i) for i, x in enumerate(b['slots'])]       # raises ValueError with a human message
         if len({x['id'] for x in slots}) != len(slots): raise ValueError('each grid slot needs a unique name')
         used = sum(x['share'] for x in e.S['SLEEVES'] if x['enabled']) + sum(x['share'] for x in slots if x['enabled'])
         if used > 1.0001: raise ValueError('capital shares of strategies + grids add up to more than 100%')
         with e.lock: e.S['GRID_SLOTS'] = slots; e.save_settings()
         return 'grid slots saved'
     if path == '/api/grid_start': with e.lock: return e.grids.start(b['slot'], b['symbol'])          # dict status
     if path == '/api/grid_stop':  with e.lock: return e.grids.stop(b['slot'], b['symbol'], 'manual')
     if path == '/api/grid_preview': return GRID.validate_cfg(b.get('cfg') or {}, price=..., atr_pct=...)[1]
     GET status: e.grids.status()   (list of dicts: levels, filled cells, inventory, cycles, cycle_pnl, metrics)
     Also in the '/api/sleeves' share check add the enabled grid slots' shares.

Grid slot JSON for the UI (defaults; ranges enforced by validate_slot / validate_cfg):
{
  "id": "G1",                 // 1-12 chars [A-Za-z0-9_-], not MAN, not a strategy slot id
  "enabled": false,           // toggled slot
  "auto": true,               // start by itself on its coins whenever the coin is ranging (else only via grid_start)
  "symbols": "core8",         // 'core8' | 'all' | ['BTCUSDT', ...]
  "tf": "4h",                 // '1h' | '4h' - timeframe of the regime / trend check and of ATR / lookback ranges
  "share": 0.25,              // 0.01-1   share of bot capital for this slot
  "max_coins": 4,             // 1-20     grids running at once; each grid gets share x capital / max_coins
  "mode": "neutral",          // 'neutral' | 'long' | 'short'
  "range_kind": "atr",        // 'atr' (start +- k ATR) | 'pct' (start +- x %) | 'lookback' (low/high of N candles)
  "range_value": 4,           // atr 1-20 ATR | pct 1-50 % | lookback 10-500 candles
  "levels": 12,               // 4-40 cells
  "spacing": "geom",          // 'arith' | 'geom'
  "capital_frac": 1.0,        // 0.1-10   notional with one side fully filled, x grid capital (= effective leverage)
  "stop_out_pct": 3.0,        // 0.5-20   hard stop this % beyond the range
  "max_lev": 3.0,             // 1-10     cap on capital_frac and on total grid notional
  "trend_bars": 6,            // 1-50     regime not 'range' this many candles in a row -> close all & stop
  "cooldown_bars": 6,         // 0-100    candles to wait after a stop before a new grid on that coin
  "need_range": true,         // only start while strategies.regime() says range
  "max_worst_loss_pct": 25    // 1-100    refuse a grid whose worst case > this % of the grid's capital
}
risk_metrics() fields: worst_loss_usd, worst_loss_pct (all cells of the worse side filled then stopped, incl. fees
and slippage, % of the grid capital), max_notional, eff_leverage, profit_per_cycle_after_fees (mean $ per cell
round trip, taker 0.05% + slippage 0.02% each side), profit_per_cycle_min, profit_per_cycle_pct (min, % of a cell's
notional), spacing_pct (smallest cell, %), min_fee_positive_spacing_pct, fee_share_pct (fees as % of the gross
of the tightest cycle), liq_price / liq_distance_pct (cross margin, maintenance 0.5%, only the grid capital as
margin = conservative; distance from the hard stop to the liquidation price, > 0 means the stop fires first),
cycles_to_recover (worst loss / mean cycle profit), range_pct, stop_lo, stop_hi, cell_qty, cells.

Live design (GridManager): MARKET orders at virtual levels checked on mark prices every manage pass (no resting
orders on the book); inventory per coin+side = one engine lot (key '<slot>|<sym>|<side>|<ts>', key_strategy 'grid',
empty mgmt so the engine's generic management never touches it) created when inventory goes 0 -> >0 and finished
when it returns to 0; grid bookkeeping (cells, filled flags, cycles, cycle_pnl, pending op) in
engine.state['grids'][<slot>|<sym>] -> survives restarts. Adds / reductions via engine._add_qty / _market_close
with post={'grid_ack': op_id}; a lost answer (AmbiguousOrder) leaves lot['pending'] that engine.reconcile resolves
from the real position; the grid applies the op only once the ack shows up on the lot (never twice, never guessed).
Never acts on a side whose lot is pending. The initial start passes engine.entry_block for every side it can open
(incl. risk rules with the grid's max notional / worst loss); later inventory increases are refused while entries are
paused, the daily halt is on, or the coin/side is untracked or waiting for a stop.
"""
import math, re, time, uuid, logging
import numpy as np
import pandas as pd

import strategies as S
import backtest as B

log = logging.getLogger('zackbot')
FEE, SLIP, FUND_PER_BAR = B.FEE, B.SLIP, B.FUND_PER_BAR
MAINT = 0.005
TF_BAR = {'15m': 900, '1h': 3600, '4h': 14400}

GRID_DEFAULTS = dict(mode='neutral', range_kind='atr', range_value=4.0, levels=12, spacing='geom', capital_frac=1.0,
                     stop_out_pct=3.0, max_lev=3.0, trend_bars=6, cooldown_bars=6, need_range=True, max_worst_loss_pct=25.0)
SLOT_DEFAULTS = dict(id='G1', enabled=False, auto=True, symbols='core8', tf='4h', share=0.25, max_coins=4)
RANGE_LIMITS = {'atr': (1, 20), 'pct': (1, 50), 'lookback': (10, 500)}
NUM_LIMITS = dict(levels=(4, 40), capital_frac=(0.1, 10), stop_out_pct=(0.5, 20), max_lev=(1, 10), trend_bars=(1, 50),
                  cooldown_bars=(0, 100), max_worst_loss_pct=(1, 100), share=(0.01, 1), max_coins=(1, 20))
COMBO_DEFAULTS = dict(dip_atr=1.5, so_n=3, so_step_atr=1.0, so_scale=1.5, tp_n=5, tp_step_atr=0.5, stop_atr=2.0,
                      capital_frac=1.0, max_coins=4, share=1.0, need_range=False, need_uptrend=True, rebuy=True, max_bars=60,
                      max_lev=3.0)


# ================================================================================================ pure grid math
def _num(v, lo, hi, name):
    try: x = float(v)
    except (TypeError, ValueError): raise ValueError(f'{name} must be a number')
    if x != x or not (lo <= x <= hi): raise ValueError(f'{name} must be between {lo:g} and {hi:g}')
    return x


def clean_cfg(cfg):
    """Defaults + type/range checks. Raises ValueError with every problem found (human readable)."""
    c = dict(GRID_DEFAULTS, **{k: v for k, v in (cfg or {}).items() if v is not None})
    errs = []
    if c['mode'] not in ('neutral', 'long', 'short'): errs.append('mode must be neutral, long or short')
    if c['range_kind'] not in RANGE_LIMITS: errs.append('range must be atr, pct or lookback')
    if c['spacing'] not in ('arith', 'geom'): errs.append('spacing must be arith or geom')
    for k, (lo, hi) in NUM_LIMITS.items():
        if k in ('share', 'max_coins'): continue
        try: c[k] = _num(c[k], lo, hi, k.replace('_', ' '))
        except ValueError as e: errs.append(str(e))
    if c['range_kind'] in RANGE_LIMITS:
        try: c['range_value'] = _num(c['range_value'], *RANGE_LIMITS[c['range_kind']], f"range ({c['range_kind']})")
        except ValueError as e: errs.append(str(e))
    if not errs:
        c['levels'] = int(round(c['levels'])); c['trend_bars'] = int(round(c['trend_bars'])); c['cooldown_bars'] = int(round(c['cooldown_bars']))
        if c['range_kind'] == 'lookback': c['range_value'] = int(round(c['range_value']))
        if c['capital_frac'] > c['max_lev']: errs.append(f"capital x{c['capital_frac']:g} is above the grid's max leverage x{c['max_lev']:g}")
    c['need_range'] = bool(c.get('need_range', True))
    if errs: raise ValueError('; '.join(errs))
    return c


def grid_range(price, atr, cfg, lo_hi=None):
    k, v = cfg['range_kind'], cfg['range_value']
    if k == 'atr': lo, hi = price - v * atr, price + v * atr
    elif k == 'pct': lo, hi = price * (1 - v / 100), price * (1 + v / 100)
    else:
        if lo_hi is None: raise ValueError('lookback range needs the recent low/high')
        lo, hi = float(lo_hi[0]), float(lo_hi[1])
    if not (0 < lo < price < hi): raise ValueError(f'price {price:.6g} is not inside the range {lo:.6g} - {hi:.6g}')
    return lo, hi


def grid_lines(lo, hi, n, spacing='geom'):
    if spacing == 'geom': return [lo * (hi / lo) ** (k / n) for k in range(n + 1)]
    return [lo + (hi - lo) * k / n for k in range(n + 1)]


def build_grid(price, atr, cfg, capital=1.0, lo_hi=None, step=None):
    """Grid for a start price. Returns dict(p0, lo, hi, lines, cells=[{a, b, k:'L'|'S', f:filled, q, e:entry, init}],
    stop_lo, stop_hi, mode, capital). step: exchange quantity step (cell qty rounded down to it)."""
    cfg = clean_cfg(cfg)
    lo, hi = grid_range(price, atr, cfg, lo_hi)
    n, mode = cfg['levels'], cfg['mode']
    lines = grid_lines(lo, hi, n, cfg['spacing'])
    cells = []
    for k in range(n):
        a, b = lines[k], lines[k + 1]
        kind = 'L' if mode == 'long' else ('S' if mode == 'short' else ('L' if (a + b) / 2 < price else 'S'))
        # long mode holds the cells above the start price from the start; short mode is short the cells below it
        init = (mode == 'long' and (a + b) / 2 >= price) or (mode == 'short' and (a + b) / 2 <= price)
        cells.append(dict(a=a, b=b, k=kind, f=False, q=0.0, e=None, init=bool(init)))
    nl, ns = sum(c['k'] == 'L' for c in cells), sum(c['k'] == 'S' for c in cells)
    per = capital * cfg['capital_frac'] / max(nl, ns, 1)                 # equal notional per cell
    for c in cells:
        ref = price if c['init'] else (c['a'] if c['k'] == 'L' else c['b'])
        q = per / ref
        if step: q = math.floor(q / step + 1e-9) * step
        c['q'] = q
    return dict(p0=price, lo=lo, hi=hi, lines=lines, cells=cells, mode=mode, capital=capital,
                stop_lo=lo * (1 - cfg['stop_out_pct'] / 100), stop_hi=hi * (1 + cfg['stop_out_pct'] / 100))


def risk_metrics(grid, fee=FEE, slip=SLIP, maint=MAINT):
    """Worst case and economics of a grid (see module doc). Pure function of build_grid's output."""
    c_ = fee + slip
    cells, cap = grid['cells'], grid['capital']
    out = dict(stop_lo=grid['stop_lo'], stop_hi=grid['stop_hi'], range_pct=(grid['hi'] / grid['lo'] - 1) * 100, cells=len(cells))
    sides = {}
    for kind, stop in (('L', grid['stop_lo']), ('S', grid['stop_hi'])):
        cs = [c for c in cells if c['k'] == kind and c['q'] > 0]
        if not cs: continue
        ent = [(c['e'] if c['f'] and c['e'] else (grid['p0'] if c['init'] else (c['a'] if kind == 'L' else c['b']))) for c in cs]
        Q = sum(c['q'] for c in cs)
        notl = sum(c['q'] * e for c, e in zip(cs, ent))
        A = notl / Q
        sd = 1 if kind == 'L' else -1
        loss = sd * (A - stop) * Q + notl * c_ + Q * stop * c_
        if sd == 1: liq = (Q * A - cap) / (Q * (1 - maint)); dist = (stop - liq) / stop * 100 if liq > 0 else 100.0
        else: liq = (cap + Q * A) / (Q * (1 + maint)); dist = (liq - stop) / stop * 100
        sides[kind] = dict(loss=loss, notional=notl, liq=max(liq, 0.0), dist=dist)
    worst = max(sides.values(), key=lambda x: x['loss']) if sides else dict(loss=0, notional=0, liq=0, dist=100)
    prof = [c['q'] * (c['b'] - c['a']) - c['q'] * (c['a'] + c['b']) * c_ for c in cells]
    gross = [c['q'] * (c['b'] - c['a']) for c in cells]
    sp = min((c['b'] / c['a'] - 1) * 100 for c in cells)
    mean_p = float(np.mean(prof)) if prof else 0.0
    out.update(worst_loss_usd=round(worst['loss'], 4), worst_loss_pct=round(worst['loss'] / cap * 100, 2) if cap else None,
               max_notional=round(max(s['notional'] for s in sides.values()) if sides else 0.0, 4),
               eff_leverage=round((max(s['notional'] for s in sides.values()) if sides else 0.0) / cap, 3) if cap else None,
               profit_per_cycle_after_fees=round(mean_p, 6), profit_per_cycle_min=round(min(prof), 6),
               profit_per_cycle_pct=round(min(p / (c['q'] * c['a']) * 100 for p, c in zip(prof, cells) if c['q'] > 0), 4) if any(c['q'] > 0 for c in cells) else 0.0,
               spacing_pct=round(sp, 4), min_fee_positive_spacing_pct=round(((1 + c_) / (1 - c_) - 1) * 100, 4),
               fee_share_pct=round(max((g - p) / g * 100 for g, p in zip(gross, prof) if g > 0), 1) if any(g > 0 for g in gross) else 100.0,
               liq_price=round(worst['liq'], 8), liq_distance_pct=round(min(s['dist'] for s in sides.values()), 2) if sides else 100.0,
               cycles_to_recover=round(worst['loss'] / mean_p, 1) if mean_p > 0 else None,
               cell_qty=round(float(np.mean([c['q'] for c in cells])), 10))
    return out


def check_metrics(m, cfg, max_worst_loss_pct=None):
    """Human messages for a grid that must be refused ([] = ok)."""
    lim = cfg.get('max_worst_loss_pct', 25.0) if max_worst_loss_pct is None else max_worst_loss_pct
    errs = []
    if m['spacing_pct'] <= m['min_fee_positive_spacing_pct']:
        errs.append(f"grid spacing {m['spacing_pct']:.3f}% does not cover fees+slippage ({m['min_fee_positive_spacing_pct']:.3f}% "
                    f"needed per cell) - use fewer levels or a wider range")
    elif m['fee_share_pct'] > 50:
        errs.append(f"fees would eat {m['fee_share_pct']:.0f}% of the tightest cycle - spacing {m['spacing_pct']:.3f}% must be at least "
                    f"{2 * m['min_fee_positive_spacing_pct']:.3f}% (fewer levels or a wider range)")
    if m['worst_loss_pct'] is not None and m['worst_loss_pct'] > lim:
        errs.append(f"worst case (all cells filled, then the stop) loses {m['worst_loss_pct']:.1f}% of the grid capital, above the "
                    f"{lim:g}% limit - lower capital x, a narrower range or a closer stop")
    if m['liq_distance_pct'] <= 0:
        errs.append(f"liquidation ({m['liq_price']:.6g}) would come before the hard stop - lower capital x")
    if (m['eff_leverage'] or 0) > cfg.get('max_lev', 3.0) + 1e-9:
        errs.append(f"effective leverage x{m['eff_leverage']:.2f} above the grid's max x{cfg.get('max_lev', 3.0):g}")
    return errs


def validate_cfg(cfg, max_worst_loss_pct=None, price=100.0, atr_pct=0.02, lo_hi=None, capital=1000.0):
    """Clean a grid config and check it on a sample grid (price, ATR as a fraction of price, lookback low/high).
    Returns (clean_cfg, risk_metrics). Raises ValueError with human messages for a non-fee-positive spacing or a
    worst-case loss above max_worst_loss_pct (default cfg['max_worst_loss_pct'])."""
    c = clean_cfg(cfg)
    if c['range_kind'] == 'lookback' and lo_hi is None:              # typical recent range: +-(4 ATR) around price
        lo_hi = (price * (1 - 4 * atr_pct), price * (1 + 4 * atr_pct))
    g = build_grid(price, price * atr_pct, c, capital, lo_hi)
    m = risk_metrics(g)
    errs = check_metrics(m, c, max_worst_loss_pct)
    if errs: raise ValueError('; '.join(errs))
    return c, m


def validate_slot(sl, i=0, strategy_ids=()):
    """A grid slot from the UI (see the JSON in the module doc). Returns the cleaned slot or raises ValueError."""
    if not isinstance(sl, dict): raise ValueError('bad grid slot')
    sid = str(sl.get('id') or f'G{i + 1}')[:12]
    if not re.fullmatch(r'[A-Za-z0-9_\-]{1,12}', sid): raise ValueError('grid slot names: letters, digits, - and _ only (max 12)')
    if sid == 'MAN' or sid in strategy_ids: raise ValueError(f'grid slot name {sid} is already used')
    syms = sl.get('symbols', 'core8')
    if syms not in ('core8', 'all'):
        if not isinstance(syms, list) or len(syms) > 40 or not all(isinstance(x, str) and re.fullmatch(r'[A-Z0-9]{2,20}USDT', x) for x in syms):
            raise ValueError('grid coins must be core8, all or a list of up to 40 USDT perpetuals')
    tf = sl.get('tf', '4h')
    if tf not in ('1h', '4h'): raise ValueError('grid timeframe must be 1h or 4h')
    cfg = {k: sl[k] for k in GRID_DEFAULTS if k in sl}
    c, m = validate_cfg(cfg)
    out = dict(id=sid, enabled=bool(sl.get('enabled', False)), auto=bool(sl.get('auto', True)), symbols=syms, tf=tf,
               share=_num(sl.get('share', SLOT_DEFAULTS['share']), *NUM_LIMITS['share'], 'capital share'),
               max_coins=int(_num(sl.get('max_coins', SLOT_DEFAULTS['max_coins']), *NUM_LIMITS['max_coins'], 'max coins')), **c)
    return out


def triggers(grid, m):
    """Cells to act on at price m: {'L': {'add': [i], 'red': [i]}, 'S': {...}, 'stop': None|'lo'|'hi'}.
    L cell: buy when empty and m <= a (or init), sell when filled and m >= b. S cell: short when empty and m >= b
    (or init), cover when filled and m <= a."""
    out = {'L': {'add': [], 'red': []}, 'S': {'add': [], 'red': []}, 'stop': None}
    if m <= grid['stop_lo']: out['stop'] = 'lo'
    elif m >= grid['stop_hi']: out['stop'] = 'hi'
    for i, c in enumerate(grid['cells']):
        if c['q'] <= 0: continue
        if c['k'] == 'L':
            if not c['f'] and (m <= c['a'] or c.get('init')): out['L']['add'].append(i)
            elif c['f'] and m >= c['b']: out['L']['red'].append(i)
        else:
            if not c['f'] and (m >= c['b'] or c.get('init')): out['S']['add'].append(i)
            elif c['f'] and m <= c['a']: out['S']['red'].append(i)
    return out


def range_flags(book, sym):
    """Per-bar 'range' flag of strategies.regime() on the coin's own candles (4h; a 1h book is resampled), cached."""
    cache = book.__dict__.setdefault('_grid_rg', {})
    if sym not in cache:
        cache[sym] = S.regime(book.d[sym])['range'].values.astype(bool)
    return cache[sym]


def _segments(prev_c, o, h, l, c):
    """Candle path: previous close -> open (gap), then green O->L->H->C, red O->H->L->C. [(from, to, is_gap)]."""
    pts = [o, l, h, c] if c >= o else [o, h, l, c]
    seg = [(prev_c, o, True)]
    for x, y in zip(pts[:-1], pts[1:]):
        if x != y: seg.append((x, y, False))
    return seg


def _fillpx(lvl, p, q):
    return lvl if min(p, q) <= lvl <= max(p, q) else p


# ================================================================================================ GRID backtest
def run_grid(book, syms, cfg, start=500.0, t0=None, t1=None, warmup=220, share=1.0, max_coins=None, maint=MAINT,
             fund_per_bar=None):
    """Backtest grids on `syms` (one grid per coin at a time). Capital per grid = equity x share / max_coins at its start.
    Taker fee + slippage on every fill, funding per bar on open inventory (scaled to the bar length), hard stop beyond
    the range, max_lev on total notional, cross-margin liquidation check. Returns (trades, equity curve) like
    backtest.run; trades = one row per inventory lot (0 -> >0 -> 0) with sleeve 'grid', R = pnl / the grid's worst loss.
    curve.attrs: cycles, cycles_per_week, active_pct, grids, stops, trend_stops, refused, liquidations, cycle_pnl."""
    cfg = clean_cfg(cfg)
    syms = [s for s in syms if s in book.syms]
    T = book.t.values
    bar_sec = float(pd.Series(T).diff().median() / np.timedelta64(1, 's')) if len(T) > 1 else 14400.0
    FPB = (FUND_PER_BAR if fund_per_bar is None else fund_per_bar) * bar_sec / 14400
    idx = np.arange(warmup, len(T))
    if t0 is not None: idx = idx[T[idx] >= np.datetime64(t0)]
    if t1 is not None: idx = idx[T[idx] < np.datetime64(t1)]
    MC = int(max_coins or len(syms))
    RG = {s: range_flags(book, s) for s in syms}
    A = book.arr
    st = {s: dict(g=None, inv={}, nr=0, cool=-1, pend=False, risk=0.0) for s in syms}
    eq, trades, curve = start, [], []
    stats = dict(cycles=0, cycle_pnl=0.0, grids=0, stops=0, trend_stops=0, refused=0, liquidations=0, active_bars=0)
    cost = FEE + SLIP

    def notional_all(px_of):
        return sum(v['q'] * px_of(s_) for s_, x in st.items() for v in x['inv'].values())

    def fill(s, kind, q, px, i, add, why=None):
        nonlocal eq
        x = st[s]; sd = 1 if kind == 'L' else -1
        pe = px * (1 + sd * SLIP) if add else px * (1 - sd * SLIP)
        fee = q * pe * FEE
        eq -= fee
        v = x['inv'].get(kind)
        if add:
            if v is None: v = x['inv'][kind] = dict(q=0.0, avg=pe, real=0.0, i0=i)
            v['avg'] = (v['avg'] * v['q'] + pe * q) / (v['q'] + q); v['q'] += q; v['real'] -= fee
            return pe
        pnl = sd * (pe - v['avg']) * q
        eq += pnl; v['real'] += pnl - fee; v['q'] -= q
        if v['q'] <= 1e-12 * max(1.0, q):
            trades.append(dict(sleeve='grid', sym=s, side=sd, i_in=v['i0'], i_out=i, pnl=v['real'],
                               R=v['real'] / x['risk'] if x['risk'] > 0 else 0.0, why=why or 'grid'))
            del x['inv'][kind]
        return pe

    def close_all(s, px_lo, px_hi, i, why):
        """Close every cell; longs at px_lo, shorts at px_hi (the relevant extreme / stop price)."""
        x = st[s]
        for kind in list(x['inv']):
            fill(s, kind, x['inv'][kind]['q'], px_lo if kind == 'L' else px_hi, i, False, why)
        x['g'] = None; x['nr'] = 0; x['cool'] = i + cfg['cooldown_bars']

    def act_cells(s, g, acts, p, q_, i, gap, mtm):
        nonlocal eq
        for kind in ('L', 'S'):
            sd = 1 if kind == 'L' else -1
            for j in acts[kind]['red']:
                c = g['cells'][j]
                lvl = c['b'] if kind == 'L' else c['a']
                px = q_ if gap else _fillpx(lvl, p, q_)
                pe = fill(s, kind, c['q'], px, i, False)
                stats['cycles'] += 1; stats['cycle_pnl'] += sd * (pe - c['e']) * c['q'] - c['q'] * (pe + c['e']) * FEE
                c['f'] = False; c['e'] = None
            for j in acts[kind]['add']:
                c = g['cells'][j]
                lvl = c['a'] if kind == 'L' else c['b']
                px = q_ if (gap or c.get('init')) else _fillpx(lvl, p, q_)
                if notional_all(lambda s_: A[s_]['c'][i - 1]) + c['q'] * px > cfg['max_lev'] * max(mtm, 0): continue
                c['e'] = fill(s, kind, c['q'], px, i, True); c['f'] = True; c['init'] = False

    for i in idx:
        up_prev = sum(sd_ * (A[s]['c'][i - 1] - v['avg']) * v['q'] for s in syms for k, v in st[s]['inv'].items()
                      for sd_ in ((1 if k == 'L' else -1),))
        mtm = eq + up_prev
        n_act = sum(1 for s in syms if st[s]['g'] is not None)
        for s in syms:
            x = st[s]; a = A[s]
            o, h, l, c = a['o'][i], a['h'][i], a['l'][i], a['c'][i]
            # ---- start a grid at this candle's open (decided on the previous close)
            if x['pend'] and x['g'] is None:
                x['pend'] = False
                if n_act < MC:
                    try:
                        lb = cfg['range_value'] if cfg['range_kind'] == 'lookback' else 0
                        lo_hi = (a['l'][i - lb:i].min(), a['h'][i - lb:i].max()) if lb else None
                        g = build_grid(o, a['atr'][i - 1], cfg, max(mtm, 0) * share / MC, lo_hi)
                        m = risk_metrics(g)
                        if check_metrics(m, cfg): raise ValueError('refused')
                        x['g'], x['risk'], x['nr'] = g, m['worst_loss_usd'], 0
                        stats['grids'] += 1; n_act += 1
                        act_cells(s, g, triggers(g, o), o, o, i, True, mtm)       # long/short-mode initial fills at the open
                    except ValueError:
                        stats['refused'] += 1
            g = x['g']
            if g is not None:
                stats['active_bars'] += 1
                for p, q_, gap in _segments(a['c'][i - 1], o, h, l, c):
                    if gap and abs(q_ - p) < 1e-15: continue
                    acts = triggers(g, q_)
                    act_cells(s, g, acts, p, q_, i, gap, mtm)          # every level before the stop fills first
                    if acts['stop']:
                        lvl = g['stop_lo'] if acts['stop'] == 'lo' else g['stop_hi']
                        px = q_ if gap else _fillpx(lvl, p, q_)
                        stats['stops'] += bool(x['inv'])
                        close_all(s, px, px, i, 'stop'); g = None; break
            # ---- funding on open inventory
            for v in x['inv'].values():
                f = v['q'] * c * FPB; eq -= f; v['real'] -= f
            # ---- regime: trend persisting -> close everything at the close
            rg = RG[s][i]
            if x['g'] is not None:
                if cfg['need_range']:
                    x['nr'] = 0 if rg else x['nr'] + 1
                    if x['nr'] >= cfg['trend_bars']:
                        stats['trend_stops'] += 1; close_all(s, c, c, i, 'trend')
            elif (rg or not cfg['need_range']) and i >= x['cool'] and i + 1 < len(T):
                x['pend'] = True
        # ---- liquidation check (cross margin over all grids at each coin's adverse extreme)
        allv = [(s, k, v) for s in syms for k, v in st[s]['inv'].items()]
        if allv and maint:
            adv = lambda s, k: A[s]['l'][i] if k == 'L' else A[s]['h'][i]
            upl = sum((1 if k == 'L' else -1) * (adv(s, k) - v['avg']) * v['q'] for s, k, v in allv)
            notl = sum(v['q'] * adv(s, k) for s, k, v in allv)
            if eq + upl < maint * notl:
                stats['liquidations'] += 1
                for s in syms:
                    if st[s]['g'] is not None or st[s]['inv']:
                        close_all(s, A[s]['l'][i], A[s]['h'][i], i, 'liquidated')
        up = sum((1 if k == 'L' else -1) * (A[s]['c'][i] - v['avg']) * v['q'] for s in syms for k, v in st[s]['inv'].items())
        curve.append(eq + up)
        if eq + up <= start * 0.02: break
    # ---- end: mark open inventory to the last close (reported as 'end', not closed)
    last = idx[len(curve) - 1] if curve else (idx[0] if len(idx) else 0)
    for s in syms:
        for k, v in list(st[s]['inv'].items()):
            trades.append(dict(sleeve='grid', sym=s, side=1 if k == 'L' else -1, i_in=v['i0'], i_out=int(last),
                               pnl=v['real'] + (1 if k == 'L' else -1) * (A[s]['c'][last] - v['avg']) * v['q'],
                               R=0.0, why='end'))
    cv = pd.Series(curve, index=pd.to_datetime(T[idx[:len(curve)]]))
    weeks = max(1e-9, len(curve) * bar_sec / (7 * 86400))
    cv.attrs.update(stats, cycles_per_week=round(stats['cycles'] / weeks, 2),
                    active_pct=round(stats['active_bars'] / max(1, len(curve) * len(syms)) * 100, 1),
                    liquidations=stats['liquidations'])
    return pd.DataFrame(trades, columns=['sleeve', 'sym', 'side', 'i_in', 'i_out', 'pnl', 'R', 'why']), cv


# ================================================================================================ COMBO
def combo_metrics(cfg, atr_pct, capital=1.0, fee=FEE, slip=SLIP):
    """Worst case of a COMBO basket (all safety orders filled, then the basket stop), per grid capital."""
    c = dict(COMBO_DEFAULTS, **(cfg or {}))
    n, sc, st_ = int(c['so_n']), float(c['so_scale']), float(c['so_step_atr'])
    lv = [1 - k * st_ * atr_pct for k in range(n + 1)]
    if lv[-1] <= 0: raise ValueError('safety orders reach a zero price')
    w = [sc ** k for k in range(n + 1)]
    q0 = capital * c['capital_frac'] / sum(wk * lk for wk, lk in zip(w, lv))
    stop = lv[-1] - c['stop_atr'] * atr_pct
    Q = q0 * sum(w); notl = q0 * sum(wk * lk for wk, lk in zip(w, lv))
    loss = notl - Q * stop + (notl + Q * stop) * (fee + slip)
    tp1 = q0 * w[0] / c['tp_n'] * c['tp_step_atr'] * atr_pct
    return dict(worst_loss_usd=round(loss, 6), worst_loss_pct=round(loss / capital * 100, 2), max_notional=round(notl, 6),
                eff_leverage=round(notl / capital, 3), depth_pct=round((1 - stop) * 100, 2),
                tp_step_pct=round(c['tp_step_atr'] * atr_pct * 100, 3),
                tp_fee_positive=bool(c['tp_step_atr'] * atr_pct > ((1 + fee + slip) / (1 - fee - slip) - 1)))


def run_combo(book, syms, cfg, start=500.0, t0=None, t1=None, warmup=220, maint=MAINT, fund_per_bar=None):
    """COMBO backtest: dip entry (EMA20 - close > dip_atr ATR, optional uptrend EMA50 > EMA200 / range regime),
    entry at the next open; safety orders every so_step_atr ATR below the entry (weights so_scale^k); tp_n take-profits
    of qty_max / tp_n every tp_step_atr ATR (entry ATR) above the average (recomputed after each safety order);
    with rebuy a fired TP re-buys its slice when price returns to the level below it; hard basket stop stop_atr ATR
    under the last safety order; time exit after max_bars. Capital per basket = equity x share / max_coins."""
    c = dict(COMBO_DEFAULTS, **(cfg or {}))
    syms = [s for s in syms if s in book.syms]
    T = book.t.values
    bar_sec = float(pd.Series(T).diff().median() / np.timedelta64(1, 's')) if len(T) > 1 else 14400.0
    FPB = (FUND_PER_BAR if fund_per_bar is None else fund_per_bar) * bar_sec / 14400
    idx = np.arange(warmup, len(T))
    if t0 is not None: idx = idx[T[idx] >= np.datetime64(t0)]
    if t1 is not None: idx = idx[T[idx] < np.datetime64(t1)]
    A = book.arr; D = book.d
    MC = int(c['max_coins'])
    RG = {s: range_flags(book, s) for s in syms} if c['need_range'] else None
    sig = {}
    for s in syms:
        d = D[s]
        ok = np.array(((d.e20 - d.c) / d.atr > c['dip_atr']).values, dtype=bool)
        if c['need_uptrend']: ok &= (d.e50 > d.e200).values
        if RG is not None: ok &= RG[s]
        sig[s] = ok
    pos, pend = {}, set()
    eq, trades, curve = start, [], []
    stats = dict(baskets=0, tps=0, rebuys=0, stops=0, liquidations=0, active_bars=0)

    def fill(s, p, q, px, add):
        nonlocal eq
        pe = px * (1 + SLIP) if add else px * (1 - SLIP)
        fee = q * pe * FEE; eq -= fee; p['real'] -= fee
        if add:
            p['avg'] = (p['avg'] * p['q'] + pe * q) / (p['q'] + q); p['q'] += q; p['qmax'] = max(p['qmax'], p['q'])
        else:
            pnl = (pe - p['avg']) * q; eq += pnl; p['real'] += pnl; p['q'] -= q

    def finish(s, p, i, why):
        trades.append(dict(sleeve='combo', sym=s, side=1, i_in=p['i0'], i_out=i, pnl=p['real'],
                           R=p['real'] / p['risk'] if p['risk'] > 0 else 0.0, why=why))
        del pos[s]

    def set_tps(p):
        step = c['tp_step_atr'] * p['atr0']
        p['tp'] = [p['avg'] + (j + 1) * step for j in range(int(c['tp_n']))]
        p['tp_done'] = [False] * int(c['tp_n'])

    for i in idx:
        up_prev = sum((A[s]['c'][i - 1] - p['avg']) * p['q'] for s, p in pos.items())
        mtm = eq + up_prev
        for s in list(pend):
            pend.discard(s)
            if s in pos or len(pos) >= MC: continue
            a = A[s]; atr = a['atr'][i - 1]; o = a['o'][i]
            cap = max(mtm, 0) * c['share'] / MC
            try: m = combo_metrics(c, atr / o, cap)
            except ValueError: continue
            n = int(c['so_n'])
            lv = [o - k * c['so_step_atr'] * atr for k in range(n + 1)]
            w = [c['so_scale'] ** k for k in range(n + 1)]
            q0 = cap * c['capital_frac'] / sum(wk * lk for wk, lk in zip(w, lv))
            notl = sum(p_['q'] * A[s_]['c'][i - 1] for s_, p_ in pos.items())
            if notl + q0 * o > c['max_lev'] * max(mtm, 0): continue
            p = dict(q=0.0, avg=o, real=0.0, qmax=0.0, i0=i, atr0=atr, lv=lv[1:], w=w[1:], q0=q0, so=0,
                     stop=lv[-1] - c['stop_atr'] * atr, risk=m['worst_loss_usd'], rebuy=[])
            pos[s] = p; fill(s, p, q0, o, True); set_tps(p); stats['baskets'] += 1
        for s in list(pos):
            p = pos[s]; a = A[s]
            o, h, l, cl = a['o'][i], a['h'][i], a['l'][i], a['c'][i]
            segs = _segments(a['c'][i - 1], o, h, l, cl)
            if p['i0'] == i: segs = segs[1:]                          # entered at this open: no gap segment
            stats['active_bars'] += 1
            done = False
            for x, y, gap in segs:
                if y < x:                                             # down: rebuys, safety orders, stop
                    for j in sorted(p['rebuy'], reverse=True):
                        lvl = p['tp'][j - 1] if j > 0 else p['tp0base']
                        if y <= lvl:
                            fill(s, p, p['qmax'] / c['tp_n'], y if gap else _fillpx(lvl, x, y), True)
                            p['tp_done'][j] = False; p['rebuy'].remove(j); stats['rebuys'] += 1
                    while p['so'] < len(p['lv']) and y <= p['lv'][p['so']]:
                        q = p['q0'] * p['w'][p['so']]
                        fill(s, p, q, y if gap else _fillpx(p['lv'][p['so']], x, y), True); p['so'] += 1
                        set_tps(p); p['rebuy'] = []
                    if y <= p['stop']:
                        fill(s, p, p['q'], y if gap else min(p['stop'], x), False); stats['stops'] += 1
                        finish(s, p, i, 'stop'); done = True; break
                else:                                                 # up: take-profits
                    for j, lvl in enumerate(p['tp']):
                        if p['tp_done'][j] or y < lvl: continue
                        q = min(p['q'], p['qmax'] / c['tp_n'])
                        if j == 0: p['tp0base'] = p['avg']
                        fill(s, p, q, y if gap else _fillpx(lvl, x, y), False); p['tp_done'][j] = True; stats['tps'] += 1
                        if c['rebuy'] and p['q'] > 1e-12: p['rebuy'].append(j)
                        if p['q'] <= 1e-12 * max(1, p['qmax']) or all(p['tp_done']):
                            if p['q'] > 1e-12: fill(s, p, p['q'], y if gap else _fillpx(lvl, x, y), False)
                            finish(s, p, i, 'tp'); done = True; break
                    if done: break
            if done: continue
            f = p['q'] * cl * FPB; eq -= f; p['real'] -= f
            if c.get('max_bars') and i - p['i0'] >= c['max_bars']:
                fill(s, p, p['q'], cl, False); finish(s, p, i, 'time')
        if pos and maint:
            upl = sum((A[s]['l'][i] - p['avg']) * p['q'] for s, p in pos.items())
            notl = sum(p['q'] * A[s]['l'][i] for s, p in pos.items())
            if eq + upl < maint * notl:
                stats['liquidations'] += 1
                for s in list(pos):
                    fill(s, pos[s], pos[s]['q'], A[s]['l'][i], False); finish(s, pos[s], i, 'liquidated')
        for s in syms:
            if s not in pos and sig[s][i] and i + 1 < len(T): pend.add(s)
        up = sum((A[s]['c'][i] - p['avg']) * p['q'] for s, p in pos.items())
        curve.append(eq + up)
        if eq + up <= start * 0.02: break
    cv = pd.Series(curve, index=pd.to_datetime(T[idx[:len(curve)]]))
    cv.attrs.update(stats, active_pct=round(stats['active_bars'] / max(1, len(curve) * len(syms)) * 100, 1),
                    tps_per_week=round(stats['tps'] / max(1e-9, len(curve) * bar_sec / (7 * 86400)), 2))
    return pd.DataFrame(trades, columns=['sleeve', 'sym', 'side', 'i_in', 'i_out', 'pnl', 'R', 'why']), cv


# ================================================================================================ LIVE manager
class GridManager:
    """Live grids on top of an Engine. Stateless itself: everything lives in engine.state['grids'] (+ 'grid_history')."""
    SIDE = {'L': 'LONG', 'S': 'SHORT'}

    def __init__(self, engine):
        self.e = engine

    # ---------------------------------------------------------------- helpers
    @property
    def grids(self):
        return self.e.state.setdefault('grids', {})

    def _slot(self, slot):
        if isinstance(slot, dict): return dict(SLOT_DEFAULTS, **slot)
        for x in self.e.S.get('GRID_SLOTS', []) or []:
            if x.get('id') == slot: return dict(SLOT_DEFAULTS, **x)
        raise ValueError(f'unknown grid slot {slot}')

    def _sl(self, slot):
        """Pseudo strategy slot for engine.entry_block (max_pos is enforced by the grid itself)."""
        return dict(id=slot['id'], name=f"Grid {slot['id']}", key='grid', tf=slot.get('tf', '4h'), enabled=bool(slot.get('enabled', True)),
                    max_pos=10 ** 6, when='any', symbols=[])

    def range_now(self, sym, tf):
        """(range flag, last candle frame) of the last CLOSED candle of sym on tf (strategies.regime on the coin)."""
        df = self.e.candles(sym, tf)
        return bool(S.regime(df)['range'].iloc[-1]), df

    def _capital(self, slot):
        eq = self.e.last_eq or self.e.equity()
        return eq * float(slot['share']) / max(1, int(slot['max_coins']))

    def _lot(self, g, side):
        k = g['lots'].get(side)
        return self.e.state['lots'].get(k) if k else None

    def _side_qty(self, g, kind):
        return sum(c['q'] for c in g['cells'] if c['k'] == kind and c['f'])

    def _can_add(self, g, side):
        e = self.e
        if e.S.get('ENTRIES_PAUSED') or e.state.get('halted'): return False
        if getattr(e, 'state_untrusted', None): return False             # AUD-05 r2: a safety file cannot be saved - no new risk
        if e.exchange_state().get('state') == 'outage': return False     # T05b: no new grid exposure while Binance is down
        if f"{g['sym']}|{side}" in e.untracked: return False
        if any(l['symbol'] == g['sym'] and l['side'] == side and l.get('stop_dirty') for l in e.state['lots'].values()): return False
        if hasattr(e, 'stop_missing_on') and e.stop_missing_on(g['sym']): return False   # AUD-04: a stop on this coin is missing
        if e._lev_exception_block(g['sym']): return False     # T03c r1: coin traded through the above-cap leverage exception
        return True

    # ---------------------------------------------------------------- start / stop
    def start(self, slot, sym, force=False):
        """Start a grid of `slot` on `sym` at the current mark. Raises ValueError with the reason if it may not start."""
        e = self.e
        slot = self._slot(slot)
        cfg = clean_cfg({k: slot[k] for k in GRID_DEFAULTS if k in slot})
        key = f"{slot['id']}|{sym}"
        if key in self.grids: raise ValueError(f'a grid is already running on {sym} for {slot["id"]}')
        if not slot.get('enabled', True) and not force: raise ValueError('grid slot switched off')
        running = [g for g in self.grids.values() if g['slot'] == slot['id']]
        if len(running) >= int(slot['max_coins']): raise ValueError(f"max coins reached ({slot['max_coins']})")
        sl = self._sl(slot)
        sides = {'long': ['LONG'], 'short': ['SHORT'], 'neutral': ['LONG', 'SHORT']}[cfg['mode']]
        for sd in sides:
            blk = e.entry_block(sl, sym, sd)
            if blk: raise ValueError(f'grid not started: {blk}')
        tf = slot.get('tf', '4h')
        ranging, df = self.range_now(sym, tf)
        if cfg['need_range'] and not ranging and not force: raise ValueError(f'{sym} is not in a range regime ({tf})')
        price = (e.marks or {}).get(sym) or e.trade.marks().get(sym)
        if not price: raise ValueError(f'no mark price for {sym}')
        atr = float(df.atr.iloc[-1])
        n = int(cfg['range_value'])
        lo_hi = (float(df.l.iloc[-n:].min()), float(df.h.iloc[-n:].max())) if cfg['range_kind'] == 'lookback' else None
        r = e.rules[sym]
        cap = self._capital(slot)
        g = build_grid(price, atr, cfg, cap, lo_hi, step=r['step'])
        m = risk_metrics(g)
        errs = check_metrics(m, cfg)
        if errs: raise ValueError('grid refused: ' + '; '.join(errs))
        small = [c for c in g['cells'] if c['q'] < r['min_qty'] or c['q'] * min(c['a'], price) < r['min_notional']]
        if small: raise ValueError(f"grid refused: a cell ({small[0]['q']:g} {sym}) is below Binance's minimum order - fewer levels or more capital")
        if m['eff_leverage'] > float(e.S.get('MAX_LEVERAGE', 10)) + 1e-9: raise ValueError('grid leverage above the bot max leverage')
        for sd in sides:                                     # size-based risk rules with the grid's full-fill notional / worst case
            blk = e.entry_block(sl, sym, sd, size=dict(notional=m['max_notional'], risk=m['worst_loss_usd']))
            if blk: raise ValueError(f'grid not started: {blk}')
        if not e.dry:
            try: e._ensure_leverage(sym)
            except Exception as ex: raise ValueError(f'could not set leverage/margin on Binance: {ex}')
        rec = dict(key=key, slot=slot['id'], sym=sym, tf=tf, cfg=cfg, p0=g['p0'], lo=g['lo'], hi=g['hi'], lines=g['lines'],
                   cells=g['cells'], stop_lo=e._rd(g['stop_lo'], r['tick']), stop_hi=e._rd(g['stop_hi'], r['tick']), mode=cfg['mode'],
                   capital=round(cap, 4), metrics=m, lots={}, cycles=0, cycle_pnl=0.0, started=_now(), status='active',
                   trend_bars=0, last_bar=None, op=None, why=None)
        self.grids[key] = rec
        e.save_state()
        log.info(f"GRID START {sym} [{slot['id']}] {cfg['mode']} {g['lo']:.6g}-{g['hi']:.6g} x{cfg['levels']} worst {m['worst_loss_usd']:.2f} USDT")
        e.notify(f"🔲 GRID {cfg['mode']} {sym} [{slot['id']}] {g['lo']:.6g}-{g['hi']:.6g}, {cfg['levels']} cells, worst case -{m['worst_loss_usd']:.2f} USDT")
        try: self._step(rec, price)
        except Exception as ex: e.err(f'grid {key} first step: {ex}')
        return self.status_one(rec)

    def stop(self, slot, sym, why='manual'):
        sid = slot['id'] if isinstance(slot, dict) else slot
        g = self.grids.get(f'{sid}|{sym}')
        if not g: raise ValueError('no grid running there')
        self._stop(g, why)
        return self.status_one(g)

    def _stop(self, g, why):
        if g['status'] != 'stopping':
            g['status'], g['why'] = 'stopping', why
            log.info(f"GRID STOP {g['sym']} [{g['slot']}] {why}")
        self._close_rest(g)

    def _close_rest(self, g):
        e = self.e
        for side, k in list(g['lots'].items()):
            lot = e.state['lots'].get(k)
            if lot is None: g['lots'].pop(side, None); continue
            if lot.get('pending'): continue
            try:
                e.close_lot(k, 'grid_' + re.sub(r'\W+', '_', g['why'] or 'stop'), (e.marks or {}).get(g['sym']))
                g['lots'].pop(side, None)
            except Exception as ex:
                e.err(f"grid {g['key']} close {side} failed: {ex} - exchange stop stays, retried")
        if not g['lots'] and not (g.get('op') and g['op']['kind'] == 'open'):
            for c in g['cells']: c['f'] = False; c['init'] = False
            h = e.state.setdefault('grid_history', [])
            h.append({k: g[k] for k in ('key', 'slot', 'sym', 'mode', 'p0', 'lo', 'hi', 'started', 'cycles', 'cycle_pnl', 'why', 'capital')}
                     | dict(stopped=_now(), worst_loss_usd=g['metrics']['worst_loss_usd']))
            del h[:-200]
            self.grids.pop(g['key'], None)
            e.notify(f"🔲 GRID closed {g['sym']} [{g['slot']}] ({g['why']}) · {g['cycles']} cycles · cycle P&L {g['cycle_pnl']:+.2f}")
        e.save_state()

    # ---------------------------------------------------------------- reconcile hook: settle unanswered orders, vanished lots
    def reconcile(self):
        """Settle grid ops whose answer was lost, adopt a lost initial fill, and notice lots closed outside the grid
        (exchange stop, flatten, resync). Called at the start of every on_marks."""
        e = self.e; changed = False
        for g in list(self.grids.values()):
            op = g.get('op')
            if op:
                side = op['side']; lot = self._lot(g, side)
                if op['kind'] == 'open':
                    if lot is None: changed = self._resolve_open(g, op) or changed
                    else: g['op'] = None
                    continue
                if lot is None:
                    if op.get('full'): self._apply(g, op, op['px']); changed = True     # the closing order filled and finished the lot
                    else: g['op'] = None
                elif lot.get('pending'): continue
                elif lot.get('grid_ack') == op['id']: self._apply(g, op, op['px']); changed = True
                else:
                    g['op'] = None; changed = True; log.info(f"grid {g['key']}: unconfirmed {op['kind']} did not fill - retried")
            for side, k in list(g['lots'].items()):
                if k in e.state['lots']: continue
                g['lots'].pop(side, None); changed = True
                kind = 'L' if side == 'LONG' else 'S'
                for c in g['cells']:
                    if c['k'] == kind: c['f'] = False; c['init'] = False
                if g['status'] == 'active':
                    last = next((h for h in reversed(e.history) if h.get('id') == k), None)
                    self._stop(g, 'stop' if (last or {}).get('exit_reason') == 'stop' else 'lot closed outside the grid')
            for side, k in g['lots'].items():                # a resync resized the lot: follow the exchange
                lot = e.state['lots'].get(k)
                if lot and not lot.get('pending') and not g.get('op'):
                    self._sync_side(g, 'L' if side == 'LONG' else 'S', lot['qty'])
        return changed

    def _resolve_open(self, g, op):
        e = self.e; sym, side = g['sym'], op['side']
        try: live = e.trade.positions()
        except Exception: return False
        others = sum(l['qty'] for l in e.state['lots'].values() if l['symbol'] == sym and l['side'] == side)
        extra = live.get((sym, side), 0.0) - others - sum(r_['filled'] for r_ in e.state.get('resting_entries', {}).values()
                                                          if r_['symbol'] == sym and r_['side'] == side)
        step = e.rules[sym]['step']
        if op['qty'] - 1.5 * step <= extra <= op['qty'] * 1.5 + step:     # exactly our order (anything else stays untracked)
            log.info(f"grid {g['key']}: unconfirmed first {side} order found on Binance - adopted")
            self._create(g, op, e._rd(extra, step), (e.marks or {}).get(sym) or op['px'])
            return True
        if time.time() - op['t'] > 20:
            g['op'] = None; log.info(f"grid {g['key']}: unconfirmed first {side} order did not fill - retried"); return True
        return False

    def _sync_side(self, g, kind, lot_qty):
        cells = [c for c in g['cells'] if c['k'] == kind]
        if not cells: return
        have = self._side_qty(g, kind); q = min(c['q'] for c in cells)
        if abs(have - lot_qty) <= 0.5 * q: return
        sd = 1 if kind == 'L' else -1
        order = sorted(cells, key=lambda c: sd * c['b'], reverse=True)      # cells nearest to their take-profit go first
        for c in order:
            if have - lot_qty > 0.5 * q and c['f']: c['f'] = False; c['e'] = None; have -= c['q']
        log.warning(f"grid {g['key']}: {kind} cells resynced to the lot size {lot_qty}")

    # ---------------------------------------------------------------- the mark-price step
    def on_marks(self, marks):
        """Called from engine.manage (under engine.lock) after reconcile and lot management. Returns True if state changed."""
        changed = self.reconcile()
        for g in list(self.grids.values()):
            m = marks.get(g['sym'])
            if not m: continue
            if g['status'] == 'stopping': self._close_rest(g); changed = True; continue
            try: changed = self._step(g, m) or changed
            except Exception as ex: self.e.err(f"grid {g['key']}: {ex}")
        if changed: self.e.save_state()
        return changed

    def _step(self, g, m):
        if g.get('op'): return False                               # one order at a time per grid, settled first
        acts = triggers(g, m)
        if acts['stop']:
            self._stop(g, f"range exit {'below' if acts['stop'] == 'lo' else 'above'}"); return True
        changed = False
        for kind in ('L', 'S'):
            side = self.SIDE[kind]
            lot = self._lot(g, side)
            if lot is not None and lot.get('pending'): continue
            red, add = acts[kind]['red'], acts[kind]['add']
            if red and lot is not None:
                q = sum(g['cells'][j]['q'] for j in red)
                full = all(not c['f'] or j in red for j, c in enumerate(g['cells']) if c['k'] == kind)
                op = self._op(g, side, 'red', red, q, m, full=full)
                try:
                    if full:
                        k = g['lots'][side]
                        self.e.close_lot(k, 'grid_flat', m)
                        px = next((h['exit'] for h in reversed(self.e.history) if h.get('id') == k), m)
                    else:
                        self.e._market_close(lot, q, 'grid_tp', m, post={'grid_ack': op['id']})
                        px = lot['fills'][-1][3]
                        if lot['qty'] <= 0: self.e._finish(g['lots'][side], 'grid_flat'); op['full'] = True
                        else: self.e._replace_stop(lot)
                except Exception as ex:
                    if not _ambiguous(ex): g['op'] = None
                    self.e.err(f"grid {g['key']} take-profit: {ex}"); return True
                self._apply(g, op, px); changed = True
                lot = self._lot(g, side)
            if add and self._can_add(g, side):
                q = sum(g['cells'][j]['q'] for j in add)
                if lot is None:
                    op = self._op(g, side, 'open', add, q, m)
                    self._open(g, op); changed = True
                else:
                    op = self._op(g, side, 'add', add, q, m)
                    try:
                        ok = self.e._add_qty(lot, q, m, 'grid_buy' if kind == 'L' else 'grid_sell', post={'grid_ack': op['id']})
                    except Exception as ex:
                        if not _ambiguous(ex): g['op'] = None
                        self.e.err(f"grid {g['key']} add: {ex}"); return True
                    if not ok: g['op'] = None; continue
                    px = lot['fills'][-1][3]
                    self.e._replace_stop(lot)
                    self._apply(g, op, px); changed = True
                if g.get('op'): return True                           # unconfirmed: stop here, settle next pass
        return changed

    def _op(self, g, side, kind, cells, qty, px, full=False):
        op = dict(id=uuid.uuid4().hex[:12], side=side, kind=kind, cells=list(cells), qty=qty, px=px, t=time.time(), full=full)
        g['op'] = op
        try: self.e._save_wal()                         # recorded BEFORE the order is sent (AUD-05 r2: durably,
        except Exception:                                           # or it is not sent at all)
            g['op'] = None; raise
        return op

    def _open(self, g, op):
        e = self.e; sym, side, r = g['sym'], op['side'], e.rules[g['sym']]
        q = e._rd(op['qty'], r['step'])
        px = op['px']
        if not e.dry:
            try:
                o = e.trade.open(sym, side, e._fmt(q, r['step']))
            except Exception as ex:
                if _ambiguous(ex): e.err(f"grid {g['key']} first {side} order unconfirmed ({ex}) - checking the position"); e.save_state(); return
                g['op'] = None; e.err(f"grid {g['key']} first {side} order failed: {ex}"); return
            px = float(o.get('avgPrice') or 0) or px
            filled = float(o.get('executedQty') or 0)
            if filled > 0: q = e._rd(filled, r['step'])
        self._create(g, op, q, px)

    def _create(self, g, op, q, px):
        e = self.e; side = op['side']; sd = 1 if side == 'LONG' else -1
        stop = g['stop_lo'] if side == 'LONG' else g['stop_hi']
        risk = g['metrics']['worst_loss_usd']
        plan = dict(sl=dict(id=g['slot'], key='grid', tf=g['tf'], name=f"Grid {g['slot']}"), sym=g['sym'], side=side, qty=q,
                    stop_dist=abs(px - stop), atr=abs(g['cells'][0]['b'] - g['cells'][0]['a']), g={}, risk_usd=risk,
                    eq=e.last_eq or 0, manual=False, reason='grid', px=px, sg={})
        ok = e._create_lot(plan, q, px)
        key = getattr(e, '_last_lot_key', None)
        if not ok or key not in e.state['lots']:
            g['op'] = None
            self._stop(g, 'stop order failed'); return
        g['lots'][side] = key
        self._apply(g, op, px)

    def _apply(self, g, op, px):
        kind = 'L' if op['side'] == 'LONG' else 'S'; sd = 1 if kind == 'L' else -1
        for j in op['cells']:
            c = g['cells'][j]
            if op['kind'] in ('open', 'add'):
                c['f'], c['e'], c['init'] = True, px, False
            else:
                if c['f'] and c['e']:
                    g['cycle_pnl'] += sd * (px - c['e']) * c['q'] - c['q'] * (px + c['e']) * FEE
                    g['cycles'] += 1
                c['f'], c['e'] = False, None
        if op['kind'] == 'red' and op.get('full'): g['lots'].pop(op['side'], None)     # inventory back to 0: lot finished
        g['op'] = None
        g['cycle_pnl'] = round(g['cycle_pnl'], 6)

    # ---------------------------------------------------------------- candle close: regime / trend stop / auto start
    def on_cycle(self, tf):
        e = self.e
        for g in list(self.grids.values()):
            if g['tf'] != tf or g['status'] != 'active' or not g['cfg'].get('need_range', True): continue
            try: rg, df = self.range_now(g['sym'], tf)
            except Exception as ex: e.err(f"grid {g['key']} regime: {ex}"); continue
            bar = str(df.t.iloc[-1])
            if g.get('last_bar') == bar: continue
            g['last_bar'] = bar
            g['trend_bars'] = 0 if rg else g.get('trend_bars', 0) + 1
            if g['trend_bars'] >= g['cfg']['trend_bars']: self._stop(g, 'trend')
        for slot in e.S.get('GRID_SLOTS', []) or []:
            if slot.get('tf', '4h') != tf or not slot.get('enabled') or not slot.get('auto', True): continue
            for sym in self._symbols(slot):
                if f"{slot['id']}|{sym}" in self.grids: continue
                if not self._cool_ok(slot, sym): continue
                try: self.start(slot['id'], sym)
                except ValueError as ex: log.debug(f"grid {slot['id']} {sym} not started: {ex}")
                except Exception as ex: e.err(f"grid {slot['id']} {sym} start: {ex}")
        e.save_state()

    def _symbols(self, slot):
        e = self.e
        u = [s for s in e.S['UNIVERSE'] if s in e.rules and e.S['SYMBOLS_ON'].get(s, True)]
        sy = slot.get('symbols', 'core8')
        if sy == 'core8': return [s for s in ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT') if s in u]
        if sy == 'all': return u
        return [s for s in sy if s in u]

    def _cool_ok(self, slot, sym):
        h = [x for x in self.e.state.get('grid_history', []) if x['slot'] == slot['id'] and x['sym'] == sym]
        if not h: return True
        try:
            from datetime import datetime
            age = time.time() - datetime.fromisoformat(h[-1]['stopped']).timestamp()
        except Exception: return True
        return age >= int(slot.get('cooldown_bars', 6)) * TF_BAR.get(slot.get('tf', '4h'), 14400)

    # ---------------------------------------------------------------- status for the UI
    def status_one(self, g):
        e = self.e; mk = (e.marks or {}).get(g['sym'])
        out = dict(key=g['key'], slot=g['slot'], symbol=g['sym'], mode=g['mode'], status=g['status'], why=g.get('why'), tf=g['tf'],
                   p0=g['p0'], lo=g['lo'], hi=g['hi'], stop_lo=g['stop_lo'], stop_hi=g['stop_hi'], levels=len(g['cells']),
                   lines=[round(x, 8) for x in g['lines']], cycles=g['cycles'], cycle_pnl=g['cycle_pnl'], started=g['started'],
                   trend_bars=g.get('trend_bars', 0), pending=bool(g.get('op')), capital=g['capital'], metrics=g['metrics'], mark=mk)
        for kind, side in self.SIDE.items():
            lot = self._lot(g, side)
            out[f'filled_{side.lower()}'] = sum(1 for c in g['cells'] if c['k'] == kind and c['f'])
            out[f'cells_{side.lower()}'] = sum(1 for c in g['cells'] if c['k'] == kind)
            out[f'inv_{side.lower()}'] = lot['qty'] if lot else 0.0
            out[f'avg_{side.lower()}'] = lot['avg'] if lot else None
            out[f'upnl_{side.lower()}'] = round((1 if kind == 'L' else -1) * (mk - lot['avg']) * lot['qty'], 4) if (lot and mk) else 0.0
        return out

    def status(self):
        return dict(grids=[self.status_one(g) for g in self.grids.values()],
                    history=list(self.e.state.get('grid_history', []))[-50:], slots=self.e.S.get('GRID_SLOTS', []))


def _ambiguous(ex):
    return type(ex).__name__ == 'AmbiguousOrder'


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec='seconds')
