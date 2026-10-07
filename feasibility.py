"""Exchange-filter feasibility (BT02, issue #14) - ONE pure module shared by the backtester / replay, the live engine's
entry and add gates, and the panel's preset preflight. No I/O, no clock, no network: callers pass everything in.

Binance USD-M filters modelled (per symbol): MARKET_LOT_SIZE stepSize / minQty, MIN_NOTIONAL notional, PRICE_FILTER tick.
Safety rule (never changed here): a quantity is only ever rounded DOWN to the step. If the risk-sized quantity is below the
exchange minimum the order is SKIPPED with a reason - it is never rounded up, because that would exceed the declared risk.

Snapshots (versioned JSON, see exchange_rules.py for the file builder):
  {"schema": SCHEMA, "version": int, "environment": "testnet"|"mainnet", "source": str, "fetched_at": ISO-8601 UTC or null,
   "verified": bool, "note": str, "symbols": {"BTCUSDT": {"step": .., "min_qty": .., "min_notional": .., "tick": ..}}}
Testnet and mainnet rules are separate snapshots and are never assumed identical. A snapshot that is missing, invalid,
unverified, for the wrong environment or older than max_age_days gives the state 'unknown' upstream - never a green pass.
"""
import math

SCHEMA = 'zackbot.exchange_rules/1'
ENVIRONMENTS = ('testnet', 'mainnet')
DEFAULT_MIN_NOTIONAL = 5.0          # engine.connect default when a symbol has no MIN_NOTIONAL filter
MAX_AGE_DAYS = 30
# BT02 review P2 (Codex comparison note): only rules read directly from the exchange may be trusted. 'direct_fetch' must
# come from exactly this environment's exchangeInfo URL; 'engine' is the live connection (built in memory, never
# accepted from a file - exchange_rules.load demotes it). A file import is never trusted, whatever it claims.
TRUSTED_SOURCES = {'testnet': 'https://testnet.binancefuture.com/fapi/v1/exchangeInfo',
                   'mainnet': 'https://fapi.binance.com/fapi/v1/exchangeInfo'}

# engine skip texts (unchanged wording - the panel and the trade history show them)
REASON_LEV_CAP = 'leverage cap reached for this slot'
REASON_BELOW_MIN = 'size below Binance minimum (raise capital or risk)'


# ------------------------------------------------------------------ rounding (the engine's _rd, moved here verbatim)
def round_step(x, step):
    """Floor x to a multiple of step (the engine's _rd). step must be > 0."""
    dec = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(math.floor(x / step + 1e-9) * step, dec)


def ceil_step(x, step):
    """Smallest multiple of step that is >= x (used only for the minimum-capital estimate, never to size an order)."""
    if not step: return x
    q = round_step(x, step)
    return q if q >= x - 1e-12 else round_step(q + step, step)


# ------------------------------------------------------------------ rules
def rules_from_exchange_info(info):
    """exchangeInfo JSON -> {symbol: dict(step, min_qty, tick, min_notional)}: PERPETUAL + TRADING only, MARKET_LOT_SIZE for
    the quantity filters, MIN_NOTIONAL default 5 - exactly what engine.connect has always built."""
    out = {}
    for s in info['symbols']:
        if s.get('contractType') != 'PERPETUAL' or s.get('status') != 'TRADING': continue
        f = {x['filterType']: x for x in s['filters']}
        out[s['symbol']] = dict(step=float(f['MARKET_LOT_SIZE']['stepSize']), min_qty=float(f['MARKET_LOT_SIZE']['minQty']),
                                tick=float(f['PRICE_FILTER']['tickSize']),
                                min_notional=float(f.get('MIN_NOTIONAL', {}).get('notional', DEFAULT_MIN_NOTIONAL)))
    return out


def exchange_info_from_rules(rules):
    """The inverse (for simulated exchanges, e.g. replay.py): {symbol: rule} -> a minimal exchangeInfo dict."""
    return {'symbols': [{'symbol': s, 'contractType': 'PERPETUAL', 'status': 'TRADING', 'filters': [
        {'filterType': 'MARKET_LOT_SIZE', 'stepSize': repr(float(r['step'])), 'minQty': repr(float(r['min_qty']))},
        {'filterType': 'PRICE_FILTER', 'tickSize': repr(float(r.get('tick') or 1e-6))},
        {'filterType': 'MIN_NOTIONAL', 'notional': repr(float(r['min_notional']))}]} for s, r in rules.items()]}


def build_snapshot(info, environment, source, fetched_at=None, verified=False, version=1, note=''):
    """exchangeInfo JSON -> versioned snapshot dict. verified=True only for rules really read from that environment's
    exchangeInfo (the builder CLI sets it for --fetch / a file the user says came from exchangeInfo)."""
    if environment not in ENVIRONMENTS: raise ValueError(f'environment must be one of {ENVIRONMENTS}')
    return dict(schema=SCHEMA, version=int(version), environment=environment, source=str(source), fetched_at=fetched_at,
                verified=bool(verified), note=str(note), symbols=rules_from_exchange_info(info))


def _ts(x):
    """ISO-8601 (Z or offset) or epoch seconds -> epoch seconds; None if unparseable. Pure (no clock)."""
    if x is None: return None
    if isinstance(x, (int, float)): return float(x)
    try:
        from datetime import datetime, timezone
        d = datetime.fromisoformat(str(x).replace('Z', '+00:00'))
        if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def _rule_ok(r):
    try:
        return all(math.isfinite(float(r[k])) and float(r[k]) >= 0 for k in ('step', 'min_qty', 'min_notional')) and float(r['step']) > 0
    except Exception:
        return False


def snapshot_state(snap, now, environment=None, max_age_days=MAX_AGE_DAYS):
    """-> (state, detail). state 'ok' only for a valid, verified, fresh snapshot of the asked environment whose provenance is
    a direct fetch from that environment's exchangeInfo URL or the live engine connection; otherwise one of
    'unavailable' | 'invalid' | 'wrong_environment' | 'unverified' | 'stale' (all shown as 'unknown' to the user).
    now: epoch seconds (passed in - this module never reads the clock)."""
    if not snap: return 'unavailable', 'no exchange-rule snapshot'
    if not isinstance(snap, dict) or snap.get('schema') != SCHEMA or not isinstance(snap.get('symbols'), dict) or not snap['symbols']:
        return 'invalid', 'exchange-rule snapshot is not in the expected format'
    bad = [s for s, r in snap['symbols'].items() if not _rule_ok(r)]
    if bad: return 'invalid', f'exchange-rule snapshot has invalid rules for {", ".join(sorted(bad)[:3])}'
    env = snap.get('environment')
    if environment and env != environment:
        return 'wrong_environment', f'snapshot is for {env}, not {environment} (testnet and mainnet rules differ)'
    if not snap.get('verified'):
        return 'unverified', snap.get('note') or 'unverified, refresh from exchangeInfo'
    prov = snap.get('provenance')
    if not (prov == 'engine' or (prov == 'direct_fetch' and snap.get('source_url') == TRUSTED_SOURCES.get(env))):
        return 'unverified', (f'snapshot provenance {prov!r} is not a direct exchangeInfo fetch of {env} '
                              f'(run: python exchange_rules.py fetch --env {env})')
    t = _ts(snap.get('fetched_at'))
    if t is None: return 'stale', 'snapshot has no fetch time'
    age = (float(now) - t) / 86400
    if age > max_age_days: return 'stale', f'snapshot is {age:.0f} days old (limit {max_age_days})'
    if age < -1: return 'invalid', 'snapshot fetch time is in the future'
    return 'ok', f'{env} rules from {snap.get("source")}, {age:.1f} days old'


def snapshot_rules(snap):
    """Snapshot dict, or a plain {symbol: rule} dict -> {symbol: rule}. None -> None."""
    if snap is None: return None
    return snap['symbols'] if isinstance(snap, dict) and 'symbols' in snap else snap


def diff_snapshots(old, new):
    """Rule changes between two snapshots: {symbol: {field: (old, new)}}, plus symbols added / removed."""
    a, b = snapshot_rules(old) or {}, snapshot_rules(new) or {}
    out = dict(changed={}, added=sorted(set(b) - set(a)), removed=sorted(set(a) - set(b)))
    for s in sorted(set(a) & set(b)):
        ch = {k: (a[s].get(k), b[s].get(k)) for k in ('step', 'min_qty', 'min_notional', 'tick') if a[s].get(k) != b[s].get(k)}
        if ch: out['changed'][s] = ch
    return out


# ------------------------------------------------------------------ sizing (the engine's open_lot formula)
def risk_qty(mgmt, risk_usd, px, atr, sd):
    """Risk-sized first-order quantity, as engine.open_lot / backtest.open_pos compute it.
    DCA basket (mgmt['dca']): levels px - sd*k*step_atr*ATR (k = 0..n), weights scale**k, stop step past the last level;
    qty = risk_usd / sum(w_k * |level_k - stop|)  (n=3, step 1, scale 1.5, stop 2 -> risk_usd / (24.5 ATR)).
    Otherwise: qty = risk_usd / (stop_atr * ATR). Returns dict(qty, stop, R, levels, weights) (levels/weights for DCA)."""
    if 'dca' in mgmt:
        dc = mgmt['dca']
        lv = [px - sd * k * dc['step_atr'] * atr for k in range(dc['n'] + 1)]
        w = [dc['scale'] ** k for k in range(dc['n'] + 1)]
        stop = lv[-1] - sd * dc['stop_atr'] * atr
        qty = risk_usd / sum(wk * abs(lk - stop) for wk, lk in zip(w, lv))
        return dict(qty=qty, stop=stop, R=abs(px - stop), levels=lv, weights=w)
    R = mgmt.get('stop_atr', 2.5) * atr
    return dict(qty=risk_usd / R, stop=px - sd * R, R=R, levels=None, weights=None)


# ------------------------------------------------------------------ THE order-size check
def size_check(qty_raw, qty, px, rule):
    """Can this order be placed? qty_raw: the risk-sized quantity; qty: after the leverage cap (<= qty_raw); px: price.
    rule: dict(step, min_qty, min_notional) or None (no rule known for the symbol).
    Rounds qty DOWN to the step (step falsy -> no rounding) and checks minQty / minNotional - never rounds up.
    -> dict(ok: True/False/None, qty: the quantity to send, code, reason)
       code 'ok' | 'below_min_qty' | 'below_min_notional' | 'leverage_cap' | 'unknown' (rule None: ok=None, qty unchanged).
    'leverage_cap' = the risk-sized quantity itself would pass the minimums, only the leverage cap made it too small
    (checked on the unrounded qty_raw, exactly like the engine always did)."""
    if rule is None: return dict(ok=None, qty=qty, code='unknown', reason='no exchange rule for this symbol')
    step = rule.get('step')
    q = round_step(qty, step) if step else qty
    mq, mn = rule.get('min_qty', 0), rule.get('min_notional', 0)
    if q < mq or q * px < mn:
        if qty_raw * px >= mn and qty_raw >= mq: return dict(ok=False, qty=q, code='leverage_cap', reason=REASON_LEV_CAP)
        return dict(ok=False, qty=q, code='below_min_qty' if q < mq else 'below_min_notional', reason=REASON_BELOW_MIN)
    return dict(ok=True, qty=q, code='ok', reason='')


def required_qty(px, rule):
    """Smallest order quantity this rule accepts at price px (a multiple of the step, >= minQty, notional >= minNotional)."""
    mq, mn, step = float(rule.get('min_qty', 0)), float(rule.get('min_notional', 0)), rule.get('step')
    need = max(mq, mn / px if px > 0 else math.inf)
    q = ceil_step(need, step) if step else need
    while step and (q < mq or q * px < mn): q = round_step(q + step, step)     # float edge: one more step
    return q


def leaves_dust(rest, px, rule):
    """The engine's take-profit-ladder no-dust rule: True when a partial close would leave a remainder that is positive
    but below the venue minimum (minQty or minNotional at px) - the caller then closes the whole lot instead.
    rule None -> False (no rule known: no dust check, as the legacy backtest always did). This is an ENGINE-PARITY rule;
    whether Binance itself would accept a reduce-only close of such a remainder is not modelled here."""
    if rule is None or not rest > 0: return False
    return rest < float(rule.get('min_qty', 0)) or rest * px < float(rule.get('min_notional', 0))


# ------------------------------------------------------------------ preset preflight (panel / API)
def slot_order_legs(sl, capital, px, atr, max_lev=10.0, rule=None):
    """EVERY order the engine would plan for this slot's position on one coin at `capital` (no open lots, Kelly and governor
    multipliers 1). sl['mgmt'] must be the FULL merged management (strategies.merge_mgmt).
    -> [dict(leg, k, qty_raw, qty, px)], the entry first. Engine parity (engine.open_lot / manage):
      entry          qty_raw = the risk-sized quantity, qty = after the leverage cap (size_check floors it)
      safety_order k q0 * scale**k at DCA level k            (q0 = the entry quantity floored to the step, the engine's lot q0)
      pyramid_add k  q0 * frac, n adds, k * step_r * R from the entry
    Each add is priced where the engine would place it for the side(s) this slot trades (sl['sides']: 'long' | 'short' |
    'both'; default 'both'): long DCA levels / short pyramid adds below the entry, short DCA levels / long pyramid adds above
    it. For 'both' the side with the SMALLER notional is used, so the check is never optimistic."""
    g = sl.get('mgmt') or {}
    sleeve_eq = capital * float(sl['share'])
    risk_usd = sleeve_eq * float(sl['risk'])
    sds = {'long': (1,), 'short': (-1,)}.get(sl.get('sides'), (1, -1))
    z = risk_qty(g, risk_usd, px, atr, 1)
    cap = max(0.0, max_lev * sleeve_eq) / px
    q = min(z['qty'], cap)
    q0 = size_check(z['qty'], q, px, rule)['qty'] if rule is not None else q
    legs = [dict(leg='entry', k=0, qty_raw=z['qty'], qty=q, px=px)]
    for k, w in enumerate((z['weights'] or [])[1:], start=1):
        lpx = min(px + (z['levels'][k] - px) * sd for sd in sds)            # levels[k] is the long level (sd = 1)
        legs.append(dict(leg='safety_order', k=k, qty_raw=q0 * w, qty=q0 * w, px=lpx if lpx > 0 else px))
    py = g.get('pyramid')
    if py:
        for k in range(1, int(py.get('n', 1)) + 1):
            lpx = min(px + sd * k * float(py.get('step_r', 1.5)) * z['R'] for sd in sds)
            legs.append(dict(leg='pyramid_add', k=k, qty_raw=q0 * float(py.get('frac', 0.5)), qty=q0 * float(py.get('frac', 0.5)),
                             px=lpx if lpx > 0 else px))
    return legs


def slot_order_qtys(sl, capital, px, atr, max_lev=10.0, rule=None):
    """The entry quantities (qty_raw, qty after the leverage cap) and the later orders' quantities (see slot_order_legs)."""
    legs = slot_order_legs(sl, capital, px, atr, max_lev, rule)
    sleeve_eq = capital * float(sl['share'])
    return dict(qty_raw=legs[0]['qty_raw'], qty=legs[0]['qty'], adds=[x['qty'] for x in legs[1:]], legs=legs,
                risk_usd=sleeve_eq * float(sl['risk']), cap_qty=max(0.0, max_lev * sleeve_eq) / px)


def check_legs(legs, rule):
    """size_check of every leg at its own price -> [dict(leg, ok, code, reason, qty, notional)]."""
    out = []
    for x in legs:
        d = size_check(x['qty_raw'], x['qty'], x['px'], rule)
        if x['leg'] != 'entry' and d['code'] == 'leverage_cap':
            d = dict(d, code='below_min_qty' if d['qty'] < float(rule.get('min_qty', 0)) else 'below_min_notional', reason=REASON_BELOW_MIN)
        out.append(dict(leg=x['leg'], k=x['k'], ok=bool(d['ok']), code=d['code'], reason=d['reason'], qty=d['qty'], px=x['px'],
                        notional=round(d['qty'] * x['px'], 4)))
    return out


def _min_capital(sl, capital, px, atr, max_lev, rule, upto=None):
    """Smallest capital at which the legs [0:upto] all pass (upto None = every leg), and the leg that sets it.
    Linear estimate per leg (a quantity scales with capital), then confirmed with the real check (floors make it step-y)."""
    legs = slot_order_legs(sl, capital, px, atr, max_lev, None)[:upto]     # unfloored: an entry floored to 0 must not hide its adds
    best, binding = 0.0, None
    for x in legs:
        per_cap = x['qty'] / capital if capital > 0 else 0
        need = required_qty(x['px'], rule)
        m = need / per_cap if per_cap > 0 else math.inf
        if m > best: best, binding = m, x
    if not math.isfinite(best): return math.inf, binding
    for _ in range(600):                                     # confirm: every leg passes at this capital (<= ~3.3x)
        chk = check_legs(slot_order_legs(sl, best, px, atr, max_lev, rule)[:upto], rule)
        bad = [c for c in chk if not c['ok']]
        if not bad: return best, binding
        binding = next(x for x in legs if x['leg'] == bad[-1]['leg'] and x['k'] == bad[-1]['k'])
        best *= 1.002
    return math.inf, binding


def _leg_name(x):
    if x is None: return None
    return 'entry' if x['leg'] == 'entry' else f"{x['leg'].replace('_', ' ')} {x['k']}"


def preflight(slots, capital, market, rules, rules_state='ok', rules_detail='', max_lev=10.0):
    """Exchange-filter preflight of a set of slots at `capital` (what the engine would do at today's prices) - for EVERY
    planned order of each coin/slot pair: the entry, each DCA safety order and each pyramid add (BT02 review P1).
    slots: [dict(id, key, share, risk, tf, symbols: [..], mgmt: FULL merged management)] (symbols already resolved);
    market: {(symbol, tf): dict(px, atr)}; rules: {symbol: rule}; rules_state: snapshot_state()[0].
    -> dict(status, estimate, entry_executable_pct, plan_executable_pct, executable_pct (= plan), pairs, ok (= entry_ok,
            pairs whose first order passes - unchanged meaning), entry_ok, plan_ok (pairs whose every order passes), undersized: [...], unknown: [...], add_undersized: [... per failing later order ...],
            min_capital_all (every order of every pair) + min_capital_binding, warning, rules_state, rules_detail)
    A pair is fully tradable only when its entry AND every later order pass. status 'ok' only then for every pair;
    'partial' when any entry or any later order fails; 'infeasible' when no entry can be placed. All of it only when
    rules_state == 'ok' and every pair had a rule and market data; otherwise 'unknown' (estimate keeps what the rules say).
    Never a green pass on unknown rules or on a plan with an order that cannot execute."""
    rows, under, unknown, add_under, stale = [], [], [], [], []
    for sl in slots:
        for sym in sl['symbols']:
            m = market.get((sym, sl.get('tf') or '4h')); r = (rules or {}).get(sym)
            tag = dict(slot=sl.get('id') or sl['key'], key=sl['key'], symbol=sym, tf=sl.get('tf') or '4h')
            if not m or not r or not (m.get('px') or 0) > 0 or not (m.get('atr') or 0) > 0:
                unknown.append(dict(tag, why='no exchange rule' if not r else 'no recent price / ATR')); continue
            px, atr = float(m['px']), float(m['atr'])
            if m.get('stale'): stale.append(dict(tag, t=m.get('t')))
            legs = slot_order_legs(sl, capital, px, atr, max_lev, r)
            chk = check_legs(legs, r)
            d = chk[0]
            entry_ok = d['ok']
            bad_adds = [c for c in chk[1:] if not c['ok']] if entry_ok else []
            plan_ok = entry_ok and not bad_adds
            min_entry, _ = _min_capital(sl, capital, px, atr, max_lev, r, upto=1)
            min_cap, bind = _min_capital(sl, capital, px, atr, max_lev, r)
            min_risk = float(sl['risk']) * min_cap / capital if capital > 0 and math.isfinite(min_cap) else math.inf
            o = legs[0]
            row = dict(tag, ok=entry_ok, entry_ok=entry_ok, plan_ok=plan_ok, code=d['code'], qty=d['qty'], notional=round(d['qty'] * px, 4),
                       risk_notional=round(o['qty_raw'] * px, 4), min_notional=r['min_notional'], min_qty=r['min_qty'],
                       step=r['step'], orders=len(legs),
                       min_capital=float(math.ceil(min_cap)) if math.isfinite(min_cap) else None,
                       min_capital_entry=float(math.ceil(min_entry)) if math.isfinite(min_entry) else None,
                       binding_leg=_leg_name(bind) if math.isfinite(min_cap) else None,
                       min_risk_pct=math.ceil(min_risk * 10000 - 1e-9) / 100 if math.isfinite(min_risk) else None)
            rows.append(row)
            if not entry_ok: under.append(dict(row, reason=d['reason']))
            for c in bad_adds:
                add_under.append(dict(tag, leg=_leg_name(c), code=c['code'], qty=c['qty'], px=round(c['px'], 8), notional=c['notional'],
                                      min_capital=row['min_capital'],
                                      reason=f"{_leg_name(c)} of this slot is below the Binance minimum ({c['notional']:.2f} USDT) "
                                             'and will be skipped'))
    n = len(rows)
    n_entry = sum(1 for x in rows if x['entry_ok']); n_plan = sum(1 for x in rows if x['plan_ok'])
    entry_pct = round(n_entry / n * 100, 1) if n else None
    plan_pct = round(n_plan / n * 100, 1) if n else None
    est = 'infeasible' if n and not n_entry else 'partial' if n_plan < n else 'ok' if n else 'unknown'
    status = est if rules_state == 'ok' and not unknown and not stale and n else 'unknown'
    min_all = max((x['min_capital'] for x in rows if x['min_capital'] is not None), default=None)
    if any(x['min_capital'] is None for x in rows): min_all = None
    binding = max((x for x in rows if x['min_capital'] is not None), key=lambda x: x['min_capital'], default=None) if min_all else None
    w = []
    if est == 'infeasible': w.append(f'This profile cannot place any trade at {capital:,.0f} USDT: every coin/slot is below the Binance minimum order size.')
    elif under:
        w.append(f'{len(under)} of {n} coin/slot pairs are below the Binance minimum order size at {capital:,.0f} USDT - their signals will be skipped.')
    if add_under:
        npairs = len({(x['slot'], x['symbol'], x['tf']) for x in add_under})
        w.append(f'{npairs} pair(s) can open but {len(add_under)} later order(s) (safety orders / pyramid adds) are below the Binance '
                 'minimum and will be skipped, so the position would not be built as planned.')
    if min_all and (under or add_under):
        w.append(f'Estimated capital for every planned order to be tradable: about {min_all:,.0f} USDT'
                 + (f" (set by {binding['binding_leg']} on {binding['symbol']} {binding['slot']})" if binding and binding.get('binding_leg') else '')
                 + ' (or raise the risk %).')
    if status == 'unknown':
        if rules_state != 'ok':
            w.append('Exchange rules unknown or unverified (' + (rules_detail or rules_state) + ') - this check is an estimate, not a pass.')
        if unknown: w.append(f'{len(unknown)} coin/slot pair(s) could not be checked (no rule or no recent price).')
        if stale: w.append(f'{len(stale)} coin/slot pair(s) use old prices (latest candle {min(x["t"] or "" for x in stale)}) - '
                           'an estimate, not a pass, until fresh candles are loaded.')
    return dict(status=status, estimate=est, executable_pct=plan_pct, entry_executable_pct=entry_pct, plan_executable_pct=plan_pct,
                pairs=n, ok=n_entry, entry_ok=n_entry, plan_ok=n_plan, undersized=under, unknown=unknown, add_undersized=add_under, stale=stale,
                min_capital_all=min_all, min_capital_binding=(dict(symbol=binding['symbol'], slot=binding['slot'], tf=binding['tf'],
                                                                     leg=binding['binding_leg']) if binding else None),
                warning=' '.join(w), rules_state=rules_state, rules_detail=rules_detail, capital=capital)
