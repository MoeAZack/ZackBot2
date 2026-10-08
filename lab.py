"""ZackBot Backtest Lab: parameter optimisation, walk-forward, Monte Carlo, lookahead-bias check, liquidation distance.

Pure functions on top of backtest.Book / backtest.run / backtest.stats - no network, no global state.
Every long function takes  progress(fraction, text)  and  should_stop()  callables; when should_stop() turns True the
function returns what it has so far with  cancelled=True.

Sleeves may be given in the engine/panel format (symbols 'all' / 'core8' / list, enabled, tf, id ...) - they are resolved
against the Book like app.run_backtest_job does (shares normalised to sum to 1, coins limited to the Book).

Entry points:  optimize, walk_forward, monte_carlo (+ monte_carlo_daily), lookahead_check (+ regime_lookahead), liquidation_report,
               validate_lab_request(kind, req), run_lab_job(kind, req, get_book, progress, should_stop)
"""
import copy, inspect, itertools, math, re, time
import numpy as np
import pandas as pd

import backtest as BT
import strategies as S

CORE8_FALLBACK = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT']
TF_SEC = {'15m': 900, '1h': 3600, '4h': 14400}
METRICS = ('calmar', 'sharpe', 'return', 'return_dd', 'profit_factor')
KINDS = ('optimize', 'walk_forward', 'monte_carlo', 'lookahead', 'liquidation')
RUN_OPTION_KEYS = ('entry_order', 'maker_fallback', 'fee_maker', 'maint_margin', 'pessimistic', 'pump_guard', 'risk_rules', 'governor')
HOLDOUT_FRAC = 0.25
MAX_TRIALS, MAX_WINDOWS, MAX_WF_TOTAL = 300, 30, 1500
MAX_DIMS, MAX_VALUES = 6, 50
MAX_MC = 10000

# ------------------------------------------------------------------ allowed optimisation paths and value ranges
TOP_RANGES = {'risk': (0.0005, 0.10, float), 'share': (0.01, 1.0, float), 'max_pos': (1, 40, int)}
MGMT_RANGES = {'stop_atr': (0.2, 20, float), 'trail_atr': (0, 20, float), 'be_r': (0, 20, float), 'tp1_r': (0, 20, float),
               'tp1_frac': (0.05, 1, float), 'tp_r': (0, 50, float), 'max_bars': (0, 2000, int)}
SUB_RANGES = {'pyramid': {'n': (0, 10, int), 'step_r': (0.1, 10, float), 'frac': (0.05, 3, float)},
              'dca': {'n': (0, 10, int), 'step_atr': (0.1, 10, float), 'scale': (0.5, 5, float), 'tp_atr': (0.1, 20, float),
                      'stop_atr': (0.1, 20, float)},
              'runner': {'be_r': (0, 50, float), 'step_r': (0.1, 99, float), 'gap_r': (0, 99, float), 'giveback': (0, 1, float),
                         'gb_from': (0, 50, float), 'dca_frac': (0.05, 1, float), 'trend_exit': (0, 1, int)}}
SUB_DEFAULTS = {'pyramid': dict(n=1, step_r=1.5, frac=0.5), 'dca': dict(n=3, step_atr=1.0, scale=1.5, tp_atr=1.0, stop_atr=2.0),
                'runner': {}}
PARAM_RANGE = (-1e6, 1e6, float)
PATH_RE = re.compile(r'^sleeves\[(\d{1,2})\]\.(risk|share|max_pos|mgmt\.[a-z_0-9]+(?:\.[a-z_0-9]+)?|params\.[a-z_][a-z0-9_]{0,30})$')


class Cancelled(Exception):
    pass


def _noop(*a, **k):
    return None


def _never():
    return False


def _core8():
    try:
        from engine import CORE8          # lazy: the engine module is heavy and edited in parallel
        return list(CORE8)
    except Exception:
        return list(CORE8_FALLBACK)


# ------------------------------------------------------------------ small helpers
def _f(x, nd=4):
    """JSON-safe float (None for NaN/inf)."""
    if x is None: return None
    try: x = float(x)
    except (TypeError, ValueError): return None
    return round(x, nd) if math.isfinite(x) else None


def _js(o):
    """Recursively convert numpy / pandas objects into plain JSON-serialisable Python."""
    if isinstance(o, dict): return {str(k): _js(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_js(v) for v in o]
    if isinstance(o, (bool, np.bool_)): return bool(o)
    if isinstance(o, (int, np.integer)): return int(o)
    if isinstance(o, (float, np.floating)): return _f(o, 6)
    if isinstance(o, (pd.Timestamp, np.datetime64)): return str(pd.Timestamp(o))
    if isinstance(o, np.ndarray): return _js(o.tolist())
    return o


def _ts(x):
    return None if x is None else pd.Timestamp(x)


def bar_delta(book):
    t = book.t
    return pd.Timedelta(pd.Series(t).diff().dropna().median()) if len(t) > 1 else pd.Timedelta(hours=4)


def default_period(book, warmup=220, days=None):
    """[t0, t1) covered by the Book after warm-up (optionally only the last `days`)."""
    t1 = pd.Timestamp(book.t.iloc[-1]) + bar_delta(book)
    t0 = pd.Timestamp(book.t.iloc[min(warmup, len(book.t) - 1)])
    if days: t0 = max(t0, t1 - pd.Timedelta(days=days))
    return t0, t1


def resolve_sleeves(sleeves, book, universe=None, tf=None):
    """Panel/engine sleeves -> backtest.run sleeves (like app.run_backtest_job): enabled only, matching tf, symbols resolved
    against the Book, shares normalised to sum 1."""
    act = [sl for sl in sleeves if sl.get('enabled', True)] or list(sleeves)
    if tf: act = [sl for sl in act if (sl.get('tf') or tf) == tf]
    if not act: raise ValueError('no enabled slot for this timeframe')
    tot = sum(float(sl['share']) for sl in act) or 1.0
    uni = list(universe) if universe else list(book.syms)
    out = []
    for sl in act:
        sy = sl.get('symbols', 'all')
        sy = _core8() if sy == 'core8' else uni if sy == 'all' else list(sy)
        cfg = dict(key=sl['key'], share=float(sl['share']) / tot, risk=float(sl['risk']), max_pos=int(sl['max_pos']),
                   sides=sl.get('sides') or S.STRATEGIES[sl['key']]['sides'], mgmt=copy.deepcopy(sl.get('mgmt') or {}),
                   symbols=[s for s in sy if s in book.syms], id=sl.get('id'))
        for k in ('params', 'hours', 'vol_max_pct', 'kelly', 'when', 'trail_entry', 'pump_guard'):
            if sl.get(k) is not None: cfg[k] = copy.deepcopy(sl[k])
        out.append(cfg)
    return out


def _run_accepts():
    sig = inspect.signature(BT.run)
    if any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values()): return None
    return set(sig.parameters)


def call_run(book, sleeves, **kw):
    """backtest.run with keyword args only; kwargs this version of run does not know are dropped."""
    ok = _run_accepts()
    if ok is not None: kw = {k: v for k, v in kw.items() if k in ok}
    return BT.run(book, sleeves, **kw)


def _stats(tr, cv, start):
    if cv is None or cv.empty: return {}
    mid = cv.index[0] + (cv.index[-1] - cv.index[0]) / 2
    st = BT.stats(tr, cv, start=start, split=str(mid))
    days = max(1e-9, (cv.index[-1] - cv.index[0]).total_seconds() / 86400)
    end = float(cv.iloc[-1])
    st['cagr'] = round(((end / start) ** (365.25 / days) - 1) * 100, 1) if end > 0 and days >= 1 else None
    st['days'] = round(days, 1)
    st['start'] = start
    return {k: _js(v) for k, v in st.items()}


def score(st, metric):
    """Higher is better. Busted / empty runs get -inf."""
    if not st or st.get('busted'): return -math.inf
    dd = abs(st.get('maxdd') or 0.0) or 1.0
    if metric == 'calmar':
        c = st.get('cagr')
        return -math.inf if c is None else c / dd
    if metric == 'sharpe': return float(st.get('sharpe') or 0.0)
    if metric == 'return': return float(st.get('ret') or 0.0)
    if metric == 'return_dd':
        r, wm = float(st.get('ret') or 0.0), float(st.get('worst_month') or 0.0)
        pen = 1 + min(0.0, wm) / 25.0            # worst month -10% -> x0.6, -25% or worse -> 0
        base = r / dd
        return base * max(0.0, pen) if base >= 0 else base / max(0.05, pen)
    if metric == 'profit_factor':
        pf = st.get('pf')
        return 99.0 if pf is None and (st.get('trades') or 0) > 0 and (st.get('ret') or 0) > 0 else float(pf or 0.0)
    raise ValueError(f'unknown metric {metric}')


def run_period(book, sleeves, t0, t1, run_kwargs):
    kw = dict(run_kwargs); kw['t0'], kw['t1'] = t0, t1
    start = float(kw.get('start', 500.0))
    tr, cv = call_run(book, sleeves, **kw)
    return tr, cv, _stats(tr, cv, start)


# ------------------------------------------------------------------ search space
def expand_values(spec):
    if isinstance(spec, dict):
        lo, hi, st = float(spec['min']), float(spec['max']), float(spec['step'])
        if st <= 0 or hi < lo: raise ValueError('range needs min <= max and step > 0')
        n = int(math.floor((hi - lo) / st + 1e-9)) + 1
        if n > MAX_VALUES: raise ValueError(f'a range may have at most {MAX_VALUES} values (got {n})')
        return [round(lo + k * st, 10) for k in range(n)]
    if isinstance(spec, (list, tuple)):
        if not spec: raise ValueError('empty value list')
        if len(spec) > MAX_VALUES: raise ValueError(f'at most {MAX_VALUES} values per parameter')
        return list(spec)
    raise ValueError('values must be a list or {min, max, step}')


def path_range(path):
    """(lo, hi, type) for an allowed path; ValueError for anything else."""
    m = PATH_RE.match(path or '')
    if not m: raise ValueError(f'parameter "{str(path)[:60]}" is not allowed (use sleeves[i].risk / share / max_pos / mgmt.<key> / params.<name>)')
    rest = m.group(2)
    if rest in TOP_RANGES: return TOP_RANGES[rest]
    if rest.startswith('params.'): return PARAM_RANGE
    parts = rest.split('.')[1:]
    if len(parts) == 1:
        if parts[0] not in MGMT_RANGES: raise ValueError(f'unknown management setting "{parts[0]}" in {path}')
        return MGMT_RANGES[parts[0]]
    grp, k = parts
    if grp not in SUB_RANGES or k not in SUB_RANGES[grp]: raise ValueError(f'unknown management setting "{grp}.{k}" in {path}')
    return SUB_RANGES[grp][k]


def set_path(sleeves, path, value):
    """Sets one value in a (deep-copied) sleeve list. Sub-dicts (pyramid/dca/runner) start from the effective settings."""
    m = PATH_RE.match(path)
    if not m: raise ValueError(f'bad path {path}')
    i, rest = int(m.group(1)), m.group(2)
    if i >= len(sleeves): raise ValueError(f'{path}: there are only {len(sleeves)} slots')
    lo, hi, typ = path_range(path)
    if typ is int: v = int(value)
    elif isinstance(value, (bool, np.bool_)): v = bool(value)
    elif rest.startswith('params.') and isinstance(value, (int, np.integer)): v = int(value)     # window lengths stay ints
    else: v = float(value)
    sl = sleeves[i]
    if rest in TOP_RANGES: sl[rest] = v; return
    if rest.startswith('params.'):
        sl.setdefault('params', {})[rest.split('.', 1)[1]] = v; return
    parts = rest.split('.')[1:]
    mg = sl.setdefault('mgmt', {})
    if len(parts) == 1: mg[parts[0]] = v; return
    grp, k = parts
    eff = mg.get(grp) or S.STRATEGIES[sl['key']]['mgmt'].get(grp) or SUB_DEFAULTS[grp]
    mg[grp] = dict(eff); mg[grp][k] = v


def _apply(base, params):
    """Copy of base with params applied. Shares are relative weights: when a share is optimised they are re-normalised
    to sum to the base total, so the slots never get more capital together than before."""
    sl = copy.deepcopy(base)
    for p, v in params.items(): set_path(sl, p, v)
    if any(p.endswith('.share') for p in params):
        tot0, tot = sum(float(x['share']) for x in base), sum(float(x['share']) for x in sl)
        if tot > 0:
            for x in sl: x['share'] = float(x['share']) * tot0 / tot
    return sl


def _combos(space, n_trials, method, seed):
    paths = [d['path'] for d in space]
    vals = [expand_values(d['values']) for d in space]
    total = int(np.prod([len(v) for v in vals])) if vals else 1
    rng = np.random.default_rng(seed)
    if total <= n_trials:
        idxs = list(range(total))
    elif method == 'grid':
        idxs = sorted(rng.choice(total, n_trials, replace=False).tolist())        # grid too big: an even random subset of it
    else:
        idxs = rng.choice(total, n_trials, replace=False).tolist()
    sizes = [len(v) for v in vals]
    out = []
    for ix in idxs:
        c, r = {}, ix
        for p, v, n in zip(reversed(paths), reversed(vals), reversed(sizes)):
            c[p] = v[r % n]; r //= n
        out.append({p: c[p] for p in paths})
    return out, total


# ------------------------------------------------------------------ 1) optimize
def optimize(book, base_sleeves, space, metric='calmar', n_trials=60, seed=0, t0=None, t1=None, method='random', min_trades=30,
             holdout=True, top_k=10, progress=None, should_stop=None, **run_kwargs):
    """Search `space` on the first 75% of [t0, t1) (or all of it with holdout=False); report the top results also on the
    untouched last 25%. Returns dict(results=[{rank, params, score, stats, holdout?}], baseline, rejected, ...)."""
    progress, should_stop = progress or _noop, should_stop or _never
    if metric not in METRICS: raise ValueError(f'metric must be one of {", ".join(METRICS)}')
    T0, T1 = default_period(book, run_kwargs.get('warmup', 220))
    t0 = max(_ts(t0), T0) if t0 is not None else T0
    t1 = min(_ts(t1), T1) if t1 is not None else T1
    if t1 <= t0: raise ValueError('empty period')
    split = (t0 + (t1 - t0) * (1 - HOLDOUT_FRAC)).floor('s') if holdout else t1
    combos, total = _combos(space, int(n_trials), method, seed)
    t_start = time.time()
    _, _, base_st = run_period(book, base_sleeves, t0, split, run_kwargs)
    base = dict(params={}, stats=base_st, score=_f(score(base_st, metric)))
    res, rejected, cancelled = [], 0, False
    for k, c in enumerate(combos):
        if should_stop(): cancelled = True; break
        progress(k / max(1, len(combos)) * (0.9 if holdout else 1.0), f'trial {k + 1}/{len(combos)}')
        _, _, st = run_period(book, _apply(base_sleeves, c), t0, split, run_kwargs)
        if not st or st.get('trades', 0) < min_trades: rejected += 1; continue
        sc = score(st, metric)
        res.append(dict(params=c, stats=st, score=sc))
    res.sort(key=lambda r: r['score'], reverse=True)
    for r_, r in enumerate(res):
        r['rank'] = r_ + 1; r['score'] = _f(r['score'])
    if holdout and not cancelled:
        progress(0.92, 'hold-out check')
        _, _, hb = run_period(book, base_sleeves, split, t1, run_kwargs)
        base['holdout'] = dict(stats=hb, score=_f(score(hb, metric)))
        for r in res[:top_k]:
            if should_stop(): cancelled = True; break
            _, _, hs = run_period(book, _apply(base_sleeves, r['params']), split, t1, run_kwargs)
            r['holdout'] = dict(stats=hs, score=_f(score(hs, metric)))
            r['holdout_stats'] = hs
    progress(1.0, 'done')
    return dict(kind='optimize', metric=metric, method=method, results=res, baseline=base, rejected=rejected, evaluated=len(res) + rejected,
                space_size=total, n_trials=len(combos), min_trades=min_trades, cancelled=cancelled,
                period=dict(train=[str(t0), str(split)], holdout=[str(split), str(t1)] if holdout else None),
                elapsed_s=round(time.time() - t_start, 1))


# ------------------------------------------------------------------ 2) walk-forward
def wf_windows(t0, t1, train_days, test_days, step_days):
    """Rolling windows: train [s, s+train), test [s+train, s+train+test); s += step. A last test window shorter than the
    others is kept if it has at least half of test_days."""
    if step_days < test_days: raise ValueError('step_days must be >= test_days (test windows must not overlap to chain capital)')
    out, s = [], pd.Timestamp(t0)
    tr, te, sp = pd.Timedelta(days=train_days), pd.Timedelta(days=test_days), pd.Timedelta(days=step_days)
    while s + tr < t1:
        a, b = s + tr, min(s + tr + te, t1)
        if b - a < te / 2: break
        out.append(([s, a], [a, b]))
        s += sp
    return out


def walk_forward(book, base_sleeves, space, train_days=180, test_days=60, step_days=60, metric='calmar', n_trials=20, seed=0,
                 method='random', min_trades=10, t0=None, t1=None, progress=None, should_stop=None, **run_kwargs):
    progress, should_stop = progress or _noop, should_stop or _never
    T0, T1 = default_period(book, run_kwargs.get('warmup', 220))
    t0 = max(_ts(t0), T0) if t0 is not None else T0
    t1 = min(_ts(t1), T1) if t1 is not None else T1
    wins = wf_windows(t0, t1, train_days, test_days, step_days)
    if not wins: raise ValueError('period too short for one train + test window')
    if len(wins) > MAX_WINDOWS: raise ValueError(f'{len(wins)} windows - at most {MAX_WINDOWS}; use a bigger step')
    start0 = float(run_kwargs.get('start', 500.0))
    cap, cap_b, out, pieces, pieces_b, cancelled = start0, start0, [], [], [], False
    t_start = time.time()
    for w, (trn, tst) in enumerate(wins):
        if should_stop(): cancelled = True; break
        sub = lambda f, txt, w=w: progress((w + f) / len(wins), f'window {w + 1}/{len(wins)}: {txt}')
        opt = optimize(book, base_sleeves, space, metric=metric, n_trials=n_trials, seed=seed + w, t0=trn[0], t1=trn[1], method=method,
                       min_trades=min_trades, holdout=False, progress=lambda f, txt: sub(0.9 * f, txt), should_stop=should_stop, **run_kwargs)
        if opt['cancelled']: cancelled = True; break
        best = opt['results'][0] if opt['results'] else None
        params = best['params'] if best else {}
        sub(0.95, 'out-of-sample test')
        kw = dict(run_kwargs, start=cap)
        _, cv, st = run_period(book, _apply(base_sleeves, params), tst[0], tst[1], kw)
        _, cvb, stb = run_period(book, base_sleeves, tst[0], tst[1], dict(run_kwargs, start=cap_b))
        end = float(cv.iloc[-1]) if len(cv) else cap
        endb = float(cvb.iloc[-1]) if len(cvb) else cap_b
        out.append(dict(train=[str(trn[0]), str(trn[1])], test=[str(tst[0]), str(tst[1])], best_params=params, fallback=best is None,
                        train_score=best['score'] if best else opt['baseline']['score'], train_stats=best['stats'] if best else opt['baseline']['stats'],
                        test_score=_f(score(st, metric)), test_stats=st, start_cap=round(cap, 2), end_cap=round(end, 2),
                        test_ret=_f((end / cap - 1) * 100, 2), baseline_test_ret=_f((endb / cap_b - 1) * 100, 2), rejected=opt['rejected']))
        if len(cv): pieces.append(cv)
        if len(cvb): pieces_b.append(cvb)
        cap, cap_b = end, endb
        if cap <= start0 * 0.02: break
    oos = pd.concat(pieces) if pieces else pd.Series(dtype=float)
    oosb = pd.concat(pieces_b) if pieces_b else pd.Series(dtype=float)
    progress(1.0, 'done')
    summ = {}
    if len(oos):
        dd = float((oos / oos.cummax() - 1).min() * 100)
        dd = min(dd, float(oos.iloc[0] / start0 - 1) * 100) if oos.iloc[0] < start0 else dd
        is_sc = [w['train_score'] for w in out if w['train_score'] is not None and math.isfinite(w['train_score'])]
        oos_sc = [w['test_score'] for w in out if w['test_score'] is not None and math.isfinite(w['test_score'])]
        days = max(1.0, (oos.index[-1] - oos.index[0]).total_seconds() / 86400)
        is_cagr = [w['train_stats'].get('cagr') for w in out if w['train_stats'] and w['train_stats'].get('cagr') is not None]
        oos_cagr = ((float(oos.iloc[-1]) / start0) ** (365.25 / days) - 1) * 100 if oos.iloc[-1] > 0 else -100.0
        summ = dict(oos_return=_f((oos.iloc[-1] / start0 - 1) * 100, 1), oos_end=_f(oos.iloc[-1], 2), oos_maxdd=_f(dd, 1), oos_cagr=_f(oos_cagr, 1),
                    baseline_oos_return=_f((oosb.iloc[-1] / start0 - 1) * 100, 1) if len(oosb) else None,
                    baseline_oos_maxdd=_f((oosb / oosb.cummax() - 1).min() * 100, 1) if len(oosb) else None,
                    pct_profitable=_f(100 * np.mean([(w['test_ret'] or 0) > 0 for w in out]), 1), windows=len(out),
                    pct_profitable_windows=_f(100 * np.mean([(w['test_ret'] or 0) > 0 for w in out]), 1),
                    is_score_mean=_f(np.mean(is_sc)) if is_sc else None, oos_score_mean=_f(np.mean(oos_sc)) if oos_sc else None,
                    # walk-forward efficiency = annualised out-of-sample return / mean annualised in-sample return (of the chosen
                    # params). Score ratios are reported with medians: annualised scores of short windows explode.
                    efficiency=_f(oos_cagr / np.mean(is_cagr)) if is_cagr and np.mean(is_cagr) > 0 else None,
                    efficiency_score=_f(np.median(oos_sc) / np.median(is_sc)) if is_sc and oos_sc and np.median(is_sc) > 0 else None)
    return dict(kind='walk_forward', metric=metric, windows=out, summary=summ, cancelled=cancelled,
                curve=_curve_pts(oos), baseline_curve=_curve_pts(oosb), elapsed_s=round(time.time() - t_start, 1))


def _curve_pts(cv, max_pts=400):
    if cv is None or not len(cv): return []
    d = cv.resample('1D').last().dropna()
    step = max(1, int(math.ceil(len(d) / max_pts)))
    d = pd.concat([d.iloc[::step], d.iloc[-1:]])
    d = d[~d.index.duplicated(keep='last')]
    return [[str(t.date()), round(float(v), 2)] for t, v in d.items()]


# ------------------------------------------------------------------ 3) Monte Carlo
EQ_COLS = ('eq_in', 'equity_in', 'eq_entry', 'equity_at_entry')


def add_equity_at_entry(tr, cv, book, start):
    """Adds eq_in = marked equity at the close before each trade's entry candle (from the run's curve)."""
    if not len(tr): return tr
    T = book.t.values
    cvi = cv.copy(); cvi.index = pd.to_datetime(cvi.index)
    ts = pd.to_datetime(T[np.clip(tr.i_in.values - 1, 0, len(T) - 1)])
    eq = cvi.reindex(ts).values
    eq = np.where(np.isfinite(eq), eq, start)
    return tr.assign(eq_in=eq)


def trade_fractions(trades, risk_per_trade=None):
    """P&L of each trade as a fraction of equity at entry, in closing order. Returns (fractions, mode)."""
    df = pd.DataFrame(trades) if not isinstance(trades, pd.DataFrame) else trades
    if not len(df): return np.zeros(0), 'none'
    if 'i_out' in df: df = df.sort_values(['i_out', 'i_in'] if 'i_in' in df else 'i_out', kind='stable')
    if 'frac' in df and risk_per_trade is None: f, mode = df['frac'].values.astype(float), 'frac'
    elif risk_per_trade is None and any(c in df for c in EQ_COLS):
        c = next(c for c in EQ_COLS if c in df)
        f, mode = (df['pnl'] / df[c]).values.astype(float), 'pnl/equity_at_entry'
    elif risk_per_trade is not None and 'R' in df:
        f, mode = df['R'].values.astype(float) * float(risk_per_trade), 'R*risk_per_trade'
    else:
        raise ValueError('trades need an equity-at-entry column (eq_in) or R with risk_per_trade')
    f = f[np.isfinite(f)]
    return np.clip(f, -0.999, None), mode


def _pcts(a, nd=2):
    return {f'p{q}': _f(np.percentile(a, q), nd) for q in (5, 25, 50, 75, 95)}


def monte_carlo(trades_df, start=500.0, n=2000, method='shuffle', risk_per_trade=None, seed=0, n_samples=20, max_pts=200,
                progress=None, should_stop=None):
    """Resamples the trade sequence. shuffle = same trades in random order (end capital identical for pure compounding,
    path risk varies); bootstrap = draws trades with replacement (end capital varies too)."""
    progress, should_stop = progress or _noop, should_stop or _never
    if method not in ('shuffle', 'bootstrap'): raise ValueError('method must be shuffle or bootstrap')
    f, mode = trade_fractions(trades_df, risk_per_trade)
    m = len(f)
    if m < 5: raise ValueError(f'need at least 5 trades for Monte Carlo (got {m})')
    rng = np.random.default_rng(seed)
    t_start = time.time()
    chunk = max(1, min(n, 2_000_000 // m))
    ends, dds, streaks, ruin50, ruin20, low_min = [], [], [], [], [], []
    samples, bands = [], []
    def stats_of(F):
        eq = start * np.cumprod(1 + F, axis=1)
        eq = np.concatenate([np.full((F.shape[0], 1), start), eq], axis=1)
        peak = np.maximum.accumulate(eq, axis=1)
        dd = (eq / peak - 1).min(axis=1) * 100
        run = np.zeros(F.shape[0]); best = np.zeros(F.shape[0])
        neg = F < 0
        for j in range(F.shape[1]):
            run = (run + 1) * neg[:, j]; best = np.maximum(best, run)
        lo = eq.min(axis=1)
        return eq, dd, best, lo
    done, cancelled = 0, False
    while done < n:
        if should_stop(): cancelled = True; break
        k = min(chunk, n - done)
        if method == 'shuffle': F = np.array([f[rng.permutation(m)] for _ in range(k)])
        else: F = f[rng.integers(0, m, size=(k, m))]
        eq, dd, st, lo = stats_of(F)
        ends.append(eq[:, -1]); dds.append(dd); streaks.append(st); low_min.append(lo)
        if len(samples) < n_samples:
            samples.extend(eq[:n_samples - len(samples)])
        bands.append(eq)  if sum(b.shape[0] for b in bands) < 1000 else None
        done += k
        progress(done / n, f'{done}/{n} paths')
    if not ends: return dict(kind='monte_carlo', cancelled=True)
    E_, D_, L_, LO = map(np.concatenate, (ends, dds, streaks, low_min))
    actual_eq, actual_dd, actual_st, _ = stats_of(f[None, :])
    step = max(1, int(math.ceil((m + 1) / max_pts)))
    xs = list(range(0, m + 1, step)) + ([m] if m % step else [])
    B = np.concatenate(bands)
    band = {f'p{q}': [_f(v, 2) for v in np.percentile(B[:, xs], q, axis=0)] for q in (5, 50, 95)}
    progress(1.0, 'done')
    return dict(kind='monte_carlo', method=method, mode=mode, n=int(len(E_)), trades=m, start=start, cancelled=cancelled,
                end_capital=_pcts(E_), max_dd=_pcts(D_, 1), losing_streak=_pcts(L_, 0),
                prob_loss=_f(np.mean(E_ < start), 4), ruin_50=_f(np.mean(LO < 0.5 * start), 4), ruin_20=_f(np.mean(LO < 0.2 * start), 4),
                prob_dd_30=_f(np.mean(D_ <= -30), 4), prob_dd_50=_f(np.mean(D_ <= -50), 4),
                actual=dict(end=_f(actual_eq[0, -1], 2), max_dd=_f(actual_dd[0], 1), losing_streak=int(actual_st[0])),
                chart=dict(x=xs, bands=band, samples=[[_f(v, 2) for v in s_[xs]] for s_ in samples], actual=[_f(v, 2) for v in actual_eq[0][xs]]),
                elapsed_s=round(time.time() - t_start, 2))


def monte_carlo_daily(cv, start=500.0, n=2000, block_days=5, seed=0):
    """Block bootstrap of the backtest's DAILY marked-to-market returns (blocks of `block_days` days, drawn with replacement).
    Unlike the trade-sequence Monte Carlo this keeps overlapping positions and open-trade drawdowns, so its drawdowns are
    comparable with the backtest's own max DD. Returns percentiles of end capital / max DD and ruin probabilities."""
    d = cv.resample('1D').last().dropna()
    r = (d / d.shift(1).fillna(start) - 1).values
    m = len(r)
    if m < 2 * block_days: raise ValueError('need a longer backtest for the daily Monte Carlo')
    rng = np.random.default_rng(seed + 11)
    nb = int(math.ceil(m / block_days))
    starts = rng.integers(0, m - block_days + 1, size=(n, nb))
    ix = (starts[:, :, None] + np.arange(block_days)[None, None, :]).reshape(n, -1)[:, :m]
    eq = start * np.cumprod(1 + r[ix], axis=1)
    eq = np.concatenate([np.full((n, 1), start), eq], axis=1)
    dd = (eq / np.maximum.accumulate(eq, axis=1) - 1).min(axis=1) * 100
    lo = eq.min(axis=1)
    act = start * np.cumprod(1 + r)
    return dict(method='daily_block_bootstrap', days=m, block_days=block_days, n=n, end_capital=_pcts(eq[:, -1]), max_dd=_pcts(dd, 1),
                prob_loss=_f(np.mean(eq[:, -1] < start), 4), ruin_50=_f(np.mean(lo < 0.5 * start), 4), ruin_20=_f(np.mean(lo < 0.2 * start), 4),
                prob_dd_30=_f(np.mean(dd <= -30), 4), prob_dd_50=_f(np.mean(dd <= -50), 4),
                actual=dict(end=_f(act[-1], 2), max_dd=_f((np.r_[start, act] / np.maximum.accumulate(np.r_[start, act]) - 1).min() * 100, 1)))


# ------------------------------------------------------------------ 4) lookahead check
def _raw_from(book_or_raw):
    if isinstance(book_or_raw, BT.Book):
        return {s: d[['t', 'o', 'h', 'l', 'c', 'v']].copy() for s, d in book_or_raw.d.items()}
    return {s: d.drop_duplicates('t').sort_values('t').reset_index(drop=True) for s, d in book_or_raw.items()}


def _same(a, b):
    a, b = np.asarray(a), np.asarray(b)
    if a.dtype == bool or b.dtype == bool: return a.astype(bool) == b.astype(bool)
    a, b = a.astype(float), b.astype(float)
    both_nan = np.isnan(a) & np.isnan(b)
    with np.errstate(invalid='ignore'):
        return both_nan | (np.abs(a - b) <= 1e-9 * np.maximum(1.0, np.abs(b)))


REGIME_COLS = ('bull', 'bear', 'range')


def regime_lookahead(df, n_points=40, seed=0, min_bar=250, max_examples=8, use_indicators=True):
    """strategies.regime() on data cut at random bars k vs the full-data result for every bar <= k.
    df: t,o,h,l,c,v (any timeframe <= 4h). use_indicators=True passes the frame with indicator columns (the 4h path that
    reuses adx/bbw_pct), False passes plain OHLCV (regime computes / resamples itself, the 1h/15m path)."""
    d = df.drop_duplicates('t').sort_values('t').reset_index(drop=True)
    d = S.indicators(d) if use_indicators else d[[c for c in ('t', 'o', 'h', 'l', 'c', 'v') if c in d]]
    N = len(d)
    rep_ = {c: dict(ok=True, mismatches=0, examples=[]) for c in REGIME_COLS}
    if N < 10: return rep_
    full = S.regime(d)
    rng = np.random.default_rng(seed + 7)
    lo = min(min_bar, max(2, N // 3))
    cuts = sorted(set(rng.choice(np.arange(lo, N - 1), size=min(n_points, N - 1 - lo), replace=False).tolist()) | {N - 2})
    for k in cuts:
        n = k + 1
        part = S.regime(S.indicators(d.iloc[:n][['t', 'o', 'h', 'l', 'c', 'v']].reset_index(drop=True)) if use_indicators else d.iloc[:n])
        for c in REGIME_COLS:
            eq = part[c].values == full[c].values[:n]
            if not eq.all():
                bad = np.where(~eq)[0]; r = rep_[c]; r['ok'] = False; r['mismatches'] += len(bad)
                if len(r['examples']) < max_examples:
                    b = int(bad[-1]); r['examples'].append(dict(cut=str(d.t.iloc[k]), bar=str(d.t.iloc[b]), at_last_bar=bool(b == n - 1),
                                                               truncated=bool(part[c].values[b]), full=bool(full[c].values[b])))
    return rep_


def lookahead_check(book_or_raw, keys=None, n_points=40, seed=0, symbols=None, min_bar=250, max_examples=8,
                    check_regime=True, progress=None, should_stop=None):
    """Recomputes indicators, context and signals on data cut at random bars k and compares them with the full-data values
    for every bar <= k. A causal computation gives identical values; any difference = information from the future.
    Reports per strategy (le/se/lx/sx) and per indicator column, with the bars where it happened."""
    progress, should_stop = progress or _noop, should_stop or _never
    keys = list(keys or S.STRATEGIES)
    for k in keys:
        if k not in S.STRATEGIES: raise ValueError(f'unknown strategy {k}')
    raw = _raw_from(book_or_raw)
    full = book_or_raw if isinstance(book_or_raw, BT.Book) else BT.Book(raw)
    syms = [s for s in (symbols or full.syms) if s in full.syms]
    T = full.t.values
    N = len(T)
    rng = np.random.default_rng(seed)
    lo = min(min_bar, max(2, N // 3))
    cuts = sorted(set(rng.choice(np.arange(lo, N - 1), size=min(n_points, N - 1 - lo), replace=False).tolist()) | {N - 2})
    sig_full = {(k, s): S.signals(k, full.d[s], full.ctx[s], None, mask_sides=False) for k in keys for s in syms}
    ind_cols = [c for c in full.d[syms[0]].columns if c != 't']
    ctx_cols = ['btc_down', 'rank', 'rank7']
    sig_rep = {k: dict(ok=True, mismatches=0, last_bar_mismatches=0, checked=0, examples=[]) for k in keys}
    ind_rep = {c: dict(ok=True, mismatches=0, examples=[]) for c in ind_cols + ['ctx.' + c for c in ctx_cols]}
    t_start, cancelled, done = time.time(), False, 0
    for ci, k in enumerate(cuts):
        if should_stop(): cancelled = True; break
        progress(ci / len(cuts), f'cut {ci + 1}/{len(cuts)}')
        tk = pd.Timestamp(T[k])
        part = BT.Book({s: d[pd.to_datetime(d.t) <= tk] for s, d in raw.items()})
        n = k + 1
        if len(part.t) != n: raise RuntimeError('truncated book misaligned')       # should never happen (same common timestamps)
        for s in syms:
            dp, df_ = part.d[s], full.d[s]
            for c in ind_cols:
                eq = _same(dp[c].values, df_[c].values[:n])
                if not eq.all():
                    bad = np.where(~eq)[0]; r = ind_rep[c]; r['ok'] = False; r['mismatches'] += len(bad)
                    if len(r['examples']) < max_examples:
                        b = int(bad[-1]); r['examples'].append(dict(sym=s, cut=str(tk), bar=str(pd.Timestamp(T[b])), truncated=_f(dp[c].values[b], 8), full=_f(df_[c].values[b], 8)))
            for c in ctx_cols:
                a, b_ = np.asarray(part.ctx[s][c]), np.asarray(full.ctx[s][c])[:n]
                eq = _same(a, b_)
                if not eq.all():
                    bad = np.where(~eq)[0]; r = ind_rep['ctx.' + c]; r['ok'] = False; r['mismatches'] += len(bad)
                    if len(r['examples']) < max_examples:
                        b = int(bad[-1]); r['examples'].append(dict(sym=s, cut=str(tk), bar=str(pd.Timestamp(T[b])), truncated=_f(a[b], 8), full=_f(b_[b], 8)))
            for key in keys:
                sp = S.signals(key, part.d[s], part.ctx[s], None, mask_sides=False)
                sf = sig_full[(key, s)]
                r = sig_rep[key]; r['checked'] += 1
                for col in ('le', 'se', 'lx', 'sx'):
                    eq = sp[col] == sf[col][:n]
                    if not eq.all():
                        bad = np.where(~eq)[0]; r['ok'] = False; r['mismatches'] += len(bad)
                        if not eq[-1]: r['last_bar_mismatches'] += 1
                        if len(r['examples']) < max_examples:
                            b = int(bad[-1])
                            r['examples'].append(dict(sym=s, column=col, cut=str(tk), bar=str(pd.Timestamp(T[b])), at_last_bar=bool(b == n - 1),
                                                      truncated=bool(sp[col][b]), full=bool(sf[col][b])))
        done += 1
    reg_rep = {}
    if check_regime and not cancelled:
        progress(0.97, 'regime')
        btc = 'BTCUSDT' if 'BTCUSDT' in full.d else full.syms[0]
        for mode, ui in (('4h_indicators', True), ('ohlcv', False)):
            for c, r in regime_lookahead(full.d[btc], n_points=n_points, seed=seed, min_bar=min_bar, max_examples=max_examples,
                                         use_indicators=ui).items():
                reg_rep[f'regime.{c}.{mode}'] = r
    progress(1.0, 'done')
    leaks = [k for k, r in sig_rep.items() if not r['ok']]
    ind_leaks = [c for c, r in ind_rep.items() if not r['ok']] + [c for c, r in reg_rep.items() if not r['ok']]
    return dict(kind='lookahead', ok=not leaks and not ind_leaks, strategies=sig_rep, indicators=ind_rep, regime=reg_rep,
                leaky_strategies=leaks, leaky_indicators=ind_leaks, cuts=done, symbols=len(syms), cancelled=cancelled,
                elapsed_s=round(time.time() - t_start, 1))


# ------------------------------------------------------------------ 5) liquidation distance (backtest.run liquidates on maintenance margin;
#                                                                      this adds how CLOSE the account came to it)
def liquidation_report(book, sleeves, tr, cv, max_lev=10.0, mm=0.005, start=500.0):
    """Cross-margin estimate. Position size of each trade is rebuilt from its risk (|pnl / R|) and the sizing rules of
    backtest.run (stop distance from ATR, leverage cap, pyramid adds / DCA safety orders counted when price reached them;
    partial take-profits ignored -> conservative). Per bar: N = open notional, E = equity.
      distance = (E - mm*N) / (N*(1-mm))   = uniform adverse move of every open position that would liquidate the account
      intrabar: equity if every position sat at its candle's adverse extreme at the same time, vs maintenance margin."""
    if tr is None or not len(tr) or cv is None or not len(cv):
        return dict(kind='liquidation', trades=0, note='no trades')
    T = book.t.values
    N = np.zeros(len(T)); ADV = np.zeros(len(T))
    by_key = {}
    for sl in sleeves: by_key.setdefault(sl['key'], sl)
    tre = add_equity_at_entry(tr, cv, book, start) if 'eq_in' not in tr else tr
    skipped = 0
    for row in tre.itertuples(index=False):
        sl = by_key.get(row.sleeve)
        if sl is None or not np.isfinite(row.R) or abs(row.R) < 1e-6: skipped += 1; continue
        risk = abs(row.pnl / row.R)
        a = book.arr[row.sym]; i, j, sd = int(row.i_in), int(row.i_out), int(row.side)
        px, atr = a['o'][i], a['atr'][i - 1]
        m = S.merge_mgmt(row.sleeve, sl.get('mgmt'))
        cap = max_lev * row.eq_in * sl['share'] / px
        h, l = a['h'][i:j + 1], a['l'][i:j + 1]
        fav = np.maximum.accumulate(h) if sd == 1 else np.minimum.accumulate(l)
        adv = np.minimum.accumulate(l) if sd == 1 else np.maximum.accumulate(h)
        qty = np.zeros(j - i + 1)
        if 'dca' in m:
            dc = m['dca']
            lv = [px - sd * k * dc['step_atr'] * atr for k in range(dc['n'] + 1)]
            w = [dc['scale'] ** k for k in range(dc['n'] + 1)]
            stop = lv[-1] - sd * dc['stop_atr'] * atr
            q0 = min(risk / sum(wk * abs(lk - stop) for wk, lk in zip(w, lv)), cap)
            qty += q0
            for lvl, wk in zip(lv[1:], w[1:]):
                qty += np.where((adv <= lvl) if sd == 1 else (adv >= lvl), q0 * wk, 0.0)
        else:
            dist = m.get('stop_atr', 2.5) * atr
            q0 = min(risk / dist, cap)
            qty += q0
            if 'pyramid' in m:
                py = m['pyramid']
                for k in range(1, int(py['n']) + 1):
                    lvl = px + sd * k * py['step_r'] * dist
                    qty += np.where((fav >= lvl) if sd == 1 else (fav <= lvl), q0 * py['frac'], 0.0)
        qty = np.minimum(qty, max_lev * row.eq_in * sl['share'] / px * 1.0 + 0 * qty)      # leverage cap holds for adds too
        c = a['c'][i:j + 1]
        N[i:j + 1] += qty * c
        ext = l if sd == 1 else h
        ADV[i:j + 1] += qty * np.abs(c - ext)
    cvi = cv.copy(); cvi.index = pd.to_datetime(cvi.index)
    E = cvi.reindex(pd.to_datetime(T)).values
    ok = np.isfinite(E) & (N > 0)
    if not ok.any(): return dict(kind='liquidation', trades=len(tr), note='no open exposure')
    dist = np.full(len(T), np.inf); dist[ok] = (E[ok] - mm * N[ok]) / (N[ok] * (1 - mm))
    lev = np.zeros(len(T)); lev[ok] = N[ok] / E[ok]
    worst_eq = E - ADV
    cushion = np.full(len(T), np.inf); cushion[ok] = worst_eq[ok] / (mm * N[ok])
    i_d, i_l, i_c = int(np.argmin(dist)), int(np.argmax(lev)), int(np.argmin(cushion))
    return dict(kind='liquidation', trades=int(len(tr)), skipped=skipped, max_lev=max_lev, maintenance_margin=mm,
                min_distance_pct=_f(dist[i_d] * 100, 2), min_distance_at=str(pd.Timestamp(T[i_d])),
                max_effective_leverage=_f(lev[i_l], 2), max_leverage_at=str(pd.Timestamp(T[i_l])),
                p95_effective_leverage=_f(np.percentile(lev[ok], 95), 2),
                min_intrabar_cushion=_f(cushion[i_c], 2), min_cushion_at=str(pd.Timestamp(T[i_c])),
                liquidated=bool(np.any(worst_eq[ok] <= mm * N[ok])),
                note='estimate: sizes rebuilt from each trade\'s risk; cushion = worst-case intrabar equity / maintenance margin (<=1 means liquidation)')


# ------------------------------------------------------------------ 6) validation + job wrapper
def _num(v, lo, hi, name, typ=float):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise ValueError(f'{name} must be a number')
    if typ is int and float(v) != int(v): raise ValueError(f'{name} must be a whole number')
    if not lo <= v <= hi: raise ValueError(f'{name} must be between {lo:g} and {hi:g}')
    return typ(v)


def _validate_sleeves(sls):
    if not isinstance(sls, list) or not 1 <= len(sls) <= 8: raise ValueError('give 1 to 8 strategy slots')
    for i, sl in enumerate(sls):
        if not isinstance(sl, dict) or sl.get('key') not in S.STRATEGIES: raise ValueError(f'slot {i + 1}: unknown strategy')
        _num(sl.get('share'), 0.001, 1, f'slot {i + 1} capital share'); _num(sl.get('risk'), 0.0005, 0.25, f'slot {i + 1} risk per trade')
        _num(sl.get('max_pos'), 1, 40, f'slot {i + 1} max positions', int)
        sy = sl.get('symbols', 'all')
        if not (sy in ('all', 'core8') or (isinstance(sy, list) and len(sy) <= 120 and all(isinstance(x, str) and re.fullmatch(r'[A-Z0-9]{2,20}USDT', x) for x in sy))):
            raise ValueError(f'slot {i + 1}: coins must be all, core8 or a list of symbols')
        if sl.get('sides', 'long') not in ('long', 'short', 'both', None): raise ValueError(f'slot {i + 1}: direction must be long/short/both')
        if not isinstance(sl.get('mgmt') or {}, dict) or not isinstance(sl.get('params') or {}, dict): raise ValueError(f'slot {i + 1}: bad settings')


def _validate_space(space, n_sleeves):
    if not isinstance(space, list) or not 1 <= len(space) <= MAX_DIMS: raise ValueError(f'choose 1 to {MAX_DIMS} parameters to optimise')
    seen = set()
    for d in space:
        if not isinstance(d, dict) or not isinstance(d.get('path'), str): raise ValueError('each parameter needs a path')
        p = d['path']
        if p in seen: raise ValueError(f'{p} is listed twice')
        seen.add(p)
        lo, hi, typ = path_range(p)
        i = int(PATH_RE.match(p).group(1))
        if i >= n_sleeves: raise ValueError(f'{p}: there are only {n_sleeves} slots')
        v = d.get('values')
        if isinstance(v, dict):
            for k in ('min', 'max', 'step'):
                if k not in v: raise ValueError(f'{p}: range needs min, max and step')
                _num(v[k], -1e9, 1e9, f'{p} {k}')
        vals = expand_values(v)
        for x in vals:
            if isinstance(x, (bool, np.bool_)) and (p.endswith('trend_exit') or '.params.' in p): continue
            _num(x, lo, hi, p, typ if typ is int else float)
    total = int(np.prod([len(expand_values(d['values'])) for d in space]))
    return total


def validate_lab_request(kind, req):
    """Raises ValueError with a human message; returns the cleaned request (defaults filled in)."""
    if kind not in KINDS: raise ValueError(f'kind must be one of {", ".join(KINDS)}')
    if not isinstance(req, dict): raise ValueError('bad request')
    out = dict(req)
    out['tf'] = req.get('tf', '4h')
    if out['tf'] not in TF_SEC: raise ValueError('timeframe must be 15m, 1h or 4h')
    out['days'] = _num(req.get('days', 730), 30, 1500, 'days', int)
    out['start'] = _num(req.get('start', 500), 50, 1e7, 'start capital')
    out['max_lev'] = _num(req.get('max_lev', 10), 1, 125, 'max leverage')
    out['daily_halt'] = _num(req.get('daily_halt', 0.08), 0.005, 1, 'daily loss halt')
    out['seed'] = _num(req.get('seed', 0), 0, 2 ** 31 - 1, 'seed', int)
    if req.get('universe') is not None:
        u = req['universe']
        if not isinstance(u, list) or len(u) > 120 or not all(isinstance(x, str) and re.fullmatch(r'[A-Z0-9]{2,20}USDT', x) for x in u):
            raise ValueError('universe must be a list of up to 120 USDT symbols')
    for k in ('t0', 't1'):
        if req.get(k) is not None:
            try: pd.Timestamp(req[k])
            except Exception: raise ValueError(f'{k} must be a date like 2025-01-31')
    if kind != 'lookahead':
        if req.get('preset') is not None and not req.get('sleeves'):
            try: from engine import PRESETS
            except Exception: raise ValueError('presets unavailable')
            if req['preset'] not in PRESETS: raise ValueError(f'unknown preset {str(req["preset"])[:30]}')
            out['sleeves'] = copy.deepcopy(PRESETS[req['preset']]['sleeves'])
        _validate_sleeves(out.get('sleeves'))
    if kind in ('optimize', 'walk_forward'):
        out['metric'] = req.get('metric', 'calmar')
        if out['metric'] not in METRICS: raise ValueError(f'metric must be one of {", ".join(METRICS)}')
        out['method'] = req.get('method', 'random')
        if out['method'] not in ('random', 'grid'): raise ValueError('method must be random or grid')
        out['space_size'] = _validate_space(req.get('space'), len(out['sleeves']))
        out['min_trades'] = _num(req.get('min_trades', 30 if kind == 'optimize' else 10), 0, 100000, 'minimum trades', int)
    if kind == 'optimize':
        out['n_trials'] = _num(req.get('n_trials', 60), 1, MAX_TRIALS, 'number of trials', int)
        if out['method'] == 'grid' and out['space_size'] > MAX_TRIALS:
            raise ValueError(f'the grid has {out["space_size"]} combinations - at most {MAX_TRIALS}; use random search or fewer values')
    if kind == 'walk_forward':
        out['train_days'] = _num(req.get('train_days', 180), 14, 1500, 'train days', int)
        out['test_days'] = _num(req.get('test_days', 60), 7, 730, 'test days', int)
        out['step_days'] = _num(req.get('step_days', out['test_days']), 7, 730, 'step days', int)
        if out['step_days'] < out['test_days']: raise ValueError('step days must be at least the test days (test windows may not overlap)')
        out['n_trials'] = _num(req.get('n_trials', 20), 1, 100, 'trials per window', int)
        span = out['days'] - out['train_days']
        if span < out['test_days'] / 2: raise ValueError('period too short: days must exceed train days + half a test window')
        nw = int(math.floor(max(0, span - out['test_days'] / 2) / out['step_days'])) + 1
        if nw > MAX_WINDOWS: raise ValueError(f'that makes ~{nw} windows - at most {MAX_WINDOWS}; increase the step')
        if nw * out['n_trials'] > MAX_WF_TOTAL: raise ValueError(f'{nw} windows x {out["n_trials"]} trials is too much work (max {MAX_WF_TOTAL})')
        out['windows_est'] = nw
    if kind == 'monte_carlo':
        out['n'] = _num(req.get('n', 2000), 100, MAX_MC, 'number of simulations', int)
        out['method'] = req.get('method', 'shuffle')
        if out['method'] not in ('shuffle', 'bootstrap'): raise ValueError('method must be shuffle or bootstrap')
        if req.get('risk_per_trade') is not None: out['risk_per_trade'] = _num(req['risk_per_trade'], 0.0001, 0.5, 'risk per trade')
    if kind == 'lookahead':
        keys = req.get('keys') or list(S.STRATEGIES)
        if not isinstance(keys, list) or any(k not in S.STRATEGIES for k in keys): raise ValueError('unknown strategy in keys')
        out['keys'] = keys
        out['n_points'] = _num(req.get('n_points', 40), 3, 200, 'check points', int)
    if kind == 'liquidation':
        out['mm'] = _num(req.get('mm', 0.005), 0, 0.05, 'maintenance margin')
    out['run_options'] = _validate_run_options(req.get('run_options') or {})
    return out


def _validate_run_options(o):
    """Optional backtest.run settings (v3.1). Only these keys are passed on."""
    if not isinstance(o, dict): raise ValueError('run_options must be an object')
    bad = set(o) - set(RUN_OPTION_KEYS)
    if bad: raise ValueError(f'unknown backtest option {sorted(bad)[0][:30]} (allowed: {", ".join(RUN_OPTION_KEYS)})')
    out = {}
    if 'entry_order' in o:
        if o['entry_order'] not in ('market', 'maker'): raise ValueError('entry order must be market or maker')
        out['entry_order'] = o['entry_order']
    if 'maker_fallback' in o: out['maker_fallback'] = bool(o['maker_fallback'])
    if 'fee_maker' in o: out['fee_maker'] = _num(o['fee_maker'], -0.001, 0.002, 'maker fee')
    if 'maint_margin' in o: out['maint_margin'] = _num(o['maint_margin'], 0, 0.05, 'maintenance margin')
    if 'pessimistic' in o:
        if o['pessimistic'] not in ('path', 'worst', False): raise ValueError('pessimistic must be path, worst or false')
        out['pessimistic'] = o['pessimistic']
    for k in ('pump_guard', 'risk_rules', 'governor'):
        if o.get(k) is not None:
            if not isinstance(o[k], dict): raise ValueError(f'{k} must be an object')
            out[k] = copy.deepcopy(o[k])
    return out


def run_lab_job(kind, req, get_book, progress=None, should_stop=None):
    """kind + raw request -> JSON-serialisable result. get_book(clean_req) must return a backtest.Book for req['tf'] covering
    the coins of the slots (+ BTCUSDT) and at least req['days'] + warm-up."""
    progress, should_stop = progress or _noop, should_stop or _never
    q = validate_lab_request(kind, req)
    t_cpu, t_wall = time.process_time(), time.time()
    book = get_book(q)
    if not isinstance(book, BT.Book): raise ValueError('no data')
    run_kwargs = dict(q.get('run_options') or {}, start=q['start'], max_lev=q['max_lev'], daily_halt=q['daily_halt'],
                      fund_per_bar=BT.FUND_PER_BAR * TF_SEC[q['tf']] / 14400)
    btc1h = getattr(book, 'btc1h', None)                     # AUD-07 C13b: BTC 1h candles from get_book, when it loaded them
    if btc1h is not None: run_kwargs['btc1h'] = btc1h
    T0, T1 = default_period(book, 220, q['days'])
    t0 = max(T0, pd.Timestamp(q['t0'])) if q.get('t0') else T0
    t1 = min(T1, pd.Timestamp(q['t1'])) if q.get('t1') else T1
    if kind == 'lookahead':
        res = lookahead_check(book, keys=q['keys'], n_points=q['n_points'], seed=q['seed'], progress=progress, should_stop=should_stop)
    else:
        sl = resolve_sleeves(q['sleeves'], book, q.get('universe'), q['tf'])
        if kind == 'optimize':
            res = optimize(book, sl, q['space'], metric=q['metric'], n_trials=q['n_trials'], seed=q['seed'], t0=t0, t1=t1, method=q['method'],
                           min_trades=q['min_trades'], progress=progress, should_stop=should_stop, **run_kwargs)
        elif kind == 'walk_forward':
            res = walk_forward(book, sl, q['space'], train_days=q['train_days'], test_days=q['test_days'], step_days=q['step_days'],
                               metric=q['metric'], n_trials=q['n_trials'], seed=q['seed'], method=q['method'], min_trades=q['min_trades'],
                               t0=t0, t1=t1, progress=progress, should_stop=should_stop, **run_kwargs)
        else:
            progress(0.02, 'backtest')
            tr, cv, st = run_period(book, sl, t0, t1, run_kwargs)
            if kind == 'monte_carlo':
                tre = add_equity_at_entry(tr, cv, book, q['start'])
                res = monte_carlo(tre, start=q['start'], n=q['n'], method=q['method'], risk_per_trade=q.get('risk_per_trade'), seed=q['seed'],
                                  progress=lambda f, t: progress(0.05 + 0.9 * f, t), should_stop=should_stop)
                if not res.get('cancelled'):
                    progress(0.97, 'daily Monte Carlo')
                    try: res['daily'] = monte_carlo_daily(cv, start=q['start'], n=q['n'], seed=q['seed'])
                    except ValueError as e: res['daily'] = dict(error=str(e))
            else:
                res = liquidation_report(book, sl, tr, cv, max_lev=q['max_lev'], mm=q['mm'], start=q['start'])
            res['backtest'] = st
    progress(1.0, 'done')
    ro = q.get('run_options') or {}
    if kind != 'lookahead' and BT.needs_btc_move(sl, ro.get('pump_guard'), ro.get('risk_rules')):
        res['btc_move_source'] = BT.btc_move_source(book, btc1h)   # AUD-07 C13b: 'proxy' = no 1h BTC data, hourly spikes unseen
    res['period'] = res.get('period') or [str(t0), str(t1)]
    res['cpu_s'] = round(time.process_time() - t_cpu, 1)
    res['wall_s'] = round(time.time() - t_wall, 1)
    res['request'] = {k: v for k, v in q.items() if k not in ('sleeves',)}
    res['sleeves'] = q.get('sleeves')
    return _js(res)
