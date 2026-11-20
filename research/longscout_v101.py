"""V101 prospective native-daily peer tail isolation and OU reversion.

Both consume only complete native UTC daily bars, are research-only/default OFF,
and decide at daily close for next-open execution. Universe selection is point
in time: BTC excluded, target excluded from peers, 31 consecutive closes and
prior-30 median quote volume >= $10m; each modeling peer needs 122 complete
consecutive closes. The target needs the same history and liquidity eligibility.

L101: each coin's prior-120 log returns define its linear 10th percentile. On
at least eight own strict tail days, average the fraction of eligible OTHER peers
also in their own prior-window tails. Require <=.20, current median peer return
<=-.015 and own current return >=0. First statistical edge; R1 rising/sideways.

R101: select highest positive prior-60 return correlation >.80 among eligible
other peers (correlations compared/ranked at 12-decimal numerical resolution,
then lexical tie break). Regress prior-120 own log closes on intercept and
peer log closes. Require positive beta, identifiable residual SD, and AR(1) with
intercept, 0<phi<1 and half-life 1..10 days. Current residual z<=-2 goes long,
>=2 goes short, on each direction's statistical edge; R1 sideways. Exit long at
z>=0 and short at z<=0 using the current day's freshly fitted prior-only model.
This is an unhedged relative-value hypothesis, not market-neutral pair trading.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1

_R = _LOG = _ELIGIBLE = _HISTORY = _TAIL_THRESHOLD = None
_DATES = _SYMBOLS = None
_CACHE = {}


def _native_daily(d):
    """Validate daily labels; invalid prices remain unavailable, never forward-filled."""
    t = pd.DatetimeIndex(pd.to_datetime(d.t))
    if t.tz is not None or not t.is_unique or not t.is_monotonic_increasing or (t != t.normalize()).any():
        raise ValueError('daily timestamps must be sorted unique UTC midnights')
    c = pd.to_numeric(pd.Series(d.c.to_numpy(), index=t), errors='coerce')
    q = pd.to_numeric(pd.Series(d.qv.to_numpy(), index=t), errors='coerce')
    c = c.where(np.isfinite(c) & (c > 0))
    q = q.where(np.isfinite(q) & (q >= 0))
    return c, q


def set_panel(panel):
    """Build aligned point-in-time price, return and eligibility matrices."""
    global _R, _LOG, _ELIGIBLE, _HISTORY, _TAIL_THRESHOLD, _DATES, _SYMBOLS, _CACHE
    _R = _LOG = _ELIGIBLE = _HISTORY = _TAIL_THRESHOLD = _DATES = _SYMBOLS = None
    _CACHE = {}
    coins = {s: _native_daily(d) for s, d in sorted(panel.items()) if s != 'BTCUSDT'}
    if not coins:
        raise ValueError('non-BTC panel required')
    nonempty = [c.index for c, _ in coins.values() if len(c)]
    if not nonempty:
        raise ValueError('empty panel')
    _DATES = pd.date_range(min(i.min() for i in nonempty), max(i.max() for i in nonempty), freq='D')
    _SYMBOLS = list(coins)
    closes = pd.DataFrame({s: c.reindex(_DATES) for s, (c, _) in coins.items()}, index=_DATES)
    quote = pd.DataFrame({s: q.reindex(_DATES) for s, (_, q) in coins.items()}, index=_DATES)
    _LOG = np.log(closes)
    _R = _LOG.diff()
    _HISTORY = closes.notna().rolling(122, min_periods=122).sum().eq(122)
    _ELIGIBLE = (closes.notna().rolling(31, min_periods=31).sum().eq(31)
                 & (quote.shift().rolling(30, min_periods=30).median() >= 1e7))
    _TAIL_THRESHOLD = _R.shift().rolling(120, min_periods=120).quantile(.10, interpolation='linear')


def _known(symbol):
    if _R is None or not isinstance(symbol, str) or symbol not in _SYMBOLS:
        raise ValueError('explicit known research_symbol required')
    return _SYMBOLS.index(symbol)


def _positive_variance(x):
    """Reject nonfinite and numerically unidentifiable variance."""
    x = np.asarray(x, dtype=float)
    if not np.isfinite(x).all():
        return False
    centered = x - x.mean()
    return float(np.dot(centered, centered)) > 64 * np.finfo(float).eps * float(np.dot(x, x))


def model_ou(own, peer):
    """Prior-only OLS and AR(1); return (intercept, beta, SD), or None."""
    own, peer = np.asarray(own, float), np.asarray(peer, float)
    if len(own) != 120 or len(peer) != 120 or not np.isfinite(own).all() or not _positive_variance(peer):
        return None
    dx = peer - peer.mean(); dy = own - own.mean()
    beta = float(np.dot(dx, dy) / np.dot(dx, dx))
    if not np.isfinite(beta) or beta <= 0:
        return None
    alpha = float(own.mean() - beta * peer.mean())
    residual = own - (alpha + beta * peer)
    # Compare residual variance with original OLS input variation, not level.
    rss = float(np.dot(residual, residual))
    if not np.isfinite(rss) or rss <= 64 * np.finfo(float).eps * float(np.dot(dy, dy)):
        return None
    sd = float(np.sqrt(rss / 120))
    lag, now = residual[:-1], residual[1:]
    if not _positive_variance(lag):
        return None
    xl = lag - lag.mean(); yn = now - now.mean()
    phi = float(np.dot(xl, yn) / np.dot(xl, xl))
    if not 0 < phi < 1:
        return None
    half_life = -np.log(2.) / np.log(phi)
    if not 1 <= half_life <= 10:
        return None
    return alpha, beta, sd


def peer_statistics(symbol):
    """Return daily raw conditions and dynamic exit z for one explicit symbol."""
    own_index = _known(symbol)
    if symbol in _CACHE:
        return _CACHE[symbol]
    r = _R.to_numpy(); logs = _LOG.to_numpy()
    eligible = (_ELIGIBLE & _HISTORY).to_numpy()
    thresholds = _TAIL_THRESHOLD.to_numpy()
    n = len(_DATES)
    tail_hit = np.zeros(n, dtype=bool)
    lower = np.zeros(n, dtype=bool)
    upper = np.zeros(n, dtype=bool)
    zvalues = np.full(n, np.nan)
    for j in range(121, n):
        if not eligible[j, own_index] or not np.isfinite(r[j, own_index]):
            continue
        peer_idx = np.flatnonzero(eligible[j])
        peer_idx = peer_idx[peer_idx != own_index]
        if len(peer_idx) < 50:
            continue
        own_prior = r[j-120:j, own_index]
        peer_prior = r[j-120:j, peer_idx]
        if not np.isfinite(own_prior).all() or not np.isfinite(peer_prior).all():
            continue
        own_tail = own_prior < thresholds[j, own_index]
        if own_tail.sum() >= 8 and np.isfinite(thresholds[j, peer_idx]).all():
            peer_tails = peer_prior < thresholds[j, peer_idx]
            score = float(peer_tails[own_tail].mean())
            if score <= .20 and np.median(r[j, peer_idx]) <= -.015 and r[j, own_index] >= 0:
                tail_hit[j] = True
        # The return correlation uses t=j-60..j-1 only.
        y = r[j-60:j, own_index]
        x = r[j-60:j, peer_idx]
        if not _positive_variance(y):
            continue
        yc = y - y.mean(); xc = x - x.mean(axis=0)
        yss = float(np.dot(yc, yc)); xss = np.sum(xc * xc, axis=0)
        rawss = np.sum(x * x, axis=0)
        valid = (xss > 64 * np.finfo(float).eps * rawss)
        corr = np.full(len(peer_idx), np.nan)
        corr[valid] = (yc @ xc[:, valid]) / np.sqrt(yss * xss[valid])
        corr = np.round(corr, 12)
        candidates = np.flatnonzero(np.isfinite(corr) & (corr > .80))
        if not len(candidates):
            continue
        chosen = min(candidates, key=lambda k: (-corr[k], _SYMBOLS[peer_idx[k]]))
        peer = peer_idx[chosen]
        model = model_ou(logs[j-120:j, own_index], logs[j-120:j, peer])
        if model is None or not np.isfinite(logs[j, peer]) or not np.isfinite(logs[j, own_index]):
            continue
        alpha, beta, sd = model
        z = (logs[j, own_index] - alpha - beta * logs[j, peer]) / sd
        if np.isfinite(z):
            zvalues[j] = z
            lower[j] = z <= -2
            upper[j] = z >= 2
    result = pd.DataFrame({'tail': tail_hit, 'lower': lower, 'upper': upper, 'z': zvalues}, index=_DATES)
    _CACHE[symbol] = result
    return result


def _empty(d):
    return {k: pd.Series(False, index=d.index) for k in ('le', 'se', 'lx', 'sx')}


def _context_symbol(ctx):
    if not isinstance(ctx, dict) or not isinstance(ctx.get('research_symbol'), str):
        raise ValueError('research_symbol context required')
    return ctx['research_symbol']


def entries(d, ctx, mode):
    symbol = _context_symbol(ctx)
    c, _ = _native_daily(d)
    stat = peer_statistics(symbol)
    # Statistical edges are computed on the full calendar before regime gating.
    tail = stat['tail'] & ~stat['tail'].shift(fill_value=False)
    lo = stat['lower'] & ~stat['lower'].shift(fill_value=False)
    hi = stat['upper'] & ~stat['upper'].shift(fill_value=False)
    regime = L1.regime_daily(c).to_numpy()
    aligned = lambda x: x.reindex(c.index, fill_value=False).to_numpy()
    out = _empty(d)
    if mode == 'tail':
        out['le'] = S.X(pd.Series(aligned(tail) & np.isin(regime, (0., 1.)), index=d.index))
    else:
        out['le'] = S.X(pd.Series(aligned(lo) & (regime == 0), index=d.index))
        out['se'] = S.X(pd.Series(aligned(hi) & (regime == 0), index=d.index))
        z = stat['z'].reindex(c.index).to_numpy()
        out['lx'] = S.X(pd.Series(np.isfinite(z) & (z >= 0), index=d.index))
        out['sx'] = S.X(pd.Series(np.isfinite(z) & (z <= 0), index=d.index))
    return out


def sig_tail(d, p, ctx): return entries(d, ctx, 'tail')
def sig_ou(d, p, ctx): return entries(d, ctx, 'ou')


CANDIDATES = {
    'l101_peer_tail_isolation_1d': dict(name='[RESEARCH] L101 peer tail isolation', style='reversal', fn=sig_tail,
        sides='long', tf='1d', research_only=True, params={}, mgmt=dict(stop_atr=2., tp_r=2., max_bars=5)),
    'r101_relative_peer_ou_1d': dict(name='[RESEARCH] R101 unhedged peer OU reversion', style='range', fn=sig_ou,
        sides='both', tf='1d', research_only=True, params={}, mgmt=dict(stop_atr=2., tp_r=2., max_bars=5)),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
