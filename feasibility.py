"""Exchange-filter feasibility (BT02, issue #14) - ONE pure module shared by the backtester / replay, the live engine's
entry and add gates, and the panel's preset preflight. No I/O, no clock, no network: callers pass everything in.

Binance USD-M filters modelled (per symbol): MARKET_LOT_SIZE stepSize / minQty, MIN_NOTIONAL notional, PRICE_FILTER tick.
Safety rule (never changed here): a quantity is only ever rounded DOWN to the step. If the risk-sized quantity is below the
exchange minimum the order is SKIPPED with a reason - it is never rounded up, because that would exceed the declared risk.

Snapshots (versioned JSON, see exchange_rules.py for the file builder):
  {"schema": SCHEMA, "version": int, "environment": "testnet"|"mainnet", "source": str, "fetched_at": ISO-8601 UTC or null,
   "verified": bool, "provenance": "fetch"|"engine"|"file", "note": str,
   "symbols": {"BTCUSDT": {"step": .., "min_qty": .., "min_notional": .., "tick": ..}}}
Testnet and mainnet rules are separate snapshots and are never assumed identical. A snapshot that is missing, invalid,
unverified, imported from a file (provenance not a direct fetch / the engine's own connection), for the wrong environment
or older than max_age_days gives the state 'unknown' upstream - never a green pass - and a backtest whose headline numbers
are reported to the user may apply exchange rules ONLY when the state is 'ok' (trusted_rules()).
"""
import math

SCHEMA = 'zackbot.exchange_rules/1'
ENVIRONMENTS = ('testnet', 'mainnet')
DEFAULT_MIN_NOTIONAL = 5.0          # engine.connect default when a symbol has no MIN_NOTIONAL filter
MAX_AGE_DAYS = 30
TRUSTED_PROVENANCE = ('fetch', 'engine')   # read directly from the exchange (fetch CLI / the engine's connect); a file import is not

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


# ------------------------------------------------------------------ partial closes (the engine's _market_close / ladder)
# Engine-parity rule (P1-C), shared by engine.py and backtest.py:
#   - every close quantity is floored to the step (close_qty); a close that floors to 0 sends NO order and leaves the
#     position unchanged (the engine's `if qty <= 0: return 0.0`) - the partial (tp1 / ladder level / runner part) is still
#     marked done by its caller, exactly like the engine;
#   - the remaining quantity is floored again (remaining_qty, the engine's _apply_close);
#   - only the take-profit LADDER has the no-dust rule (ladder_qty): if the rest after a level would be below minQty or
#     below minNotional at the trigger price, the level closes the whole remainder. tp1 and the runner's basket part have
#     no such rule in the engine (floor only), so neither has the backtest.
# Separate from engine parity (NOT modelled): Binance exempts reduce-only / closing orders from MIN_NOTIONAL (minQty and
# the step still apply), so a floored partial below minQty would be rejected by the venue; the engine does not check it
# before sending and the backtest, mirroring the engine, does not either. Unverified on testnet (needs exchangeInfo + a
# live close); see docs/reviews/BT02_review_request.md.
def close_qty(qty, step):
    """Quantity actually sent for a close of `qty`: floored to the step (step falsy -> unchanged). <= 0 -> no order."""
    return round_step(qty, step) if step else qty


def remaining_qty(qty, closed, step):
    """Position left after closing `closed` of `qty` (the engine's _apply_close: max(0, floor(qty - closed)))."""
    return max(0.0, round_step(qty - closed, step) if step else qty - closed)


def ladder_qty(qty, want, px, rule):
    """Take-profit ladder level (engine): close min(qty, want); if the rest would be dust (below minQty or below
    minNotional at px) close the whole qty instead. Returned unfloored - the close floors it (close_qty)."""
    q = min(qty, want)
    rest = qty - q
    if rest > 0 and (rest < rule['min_qty'] or rest * px < rule['min_notional']): q = qty   # never leave dust
    return q


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


def build_snapshot(info, environment, source, fetched_at=None, verified=False, version=1, note='', provenance='file'):
    """exchangeInfo JSON -> versioned snapshot dict. verified=True / provenance='fetch' only for rules read directly from that
    environment's exchangeInfo endpoint (exchange_rules.py fetch); a file import is provenance 'file' and is never 'ok'."""
    if environment not in ENVIRONMENTS: raise ValueError(f'environment must be one of {ENVIRONMENTS}')
    return dict(schema=SCHEMA, version=int(version), environment=environment, source=str(source), fetched_at=fetched_at,
                verified=bool(verified), provenance=str(provenance), note=str(note), symbols=rules_from_exchange_info(info))


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
    """-> (state, detail). state 'ok' only for a valid, verified, fresh snapshot of the asked environment; otherwise one of
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
    if snap.get('provenance') not in TRUSTED_PROVENANCE:
        return 'unverified', (f'snapshot provenance {snap.get("provenance")!r} is not a direct exchangeInfo fetch '
                              '(run: python exchange_rules.py fetch --env ' + str(env) + ')')
    t = _ts(snap.get('fetched_at'))
    if t is None: return 'stale', 'snapshot has no fetch time'
    age = (float(now) - t) / 86400
    if age > max_age_days: return 'stale', f'snapshot is {age:.0f} days old (limit {max_age_days})'
    if age < -1: return 'invalid', 'snapshot fetch time is in the future'
    return 'ok', f'{env} rules from {snap.get("source")}, {age:.1f} days old'


def trusted_rules(snap, state, environment):
    """The ONLY gate for applying exchange rules to a backtest whose numbers are reported (app / profile / study runs).
    -> (snapshot or None, rules_applied, reason). Rules are applied only when state == 'ok' for exactly this environment;
    otherwise None (= the legacy floor, results identical to exchange_rules=None) and the reason they were NOT applied."""
    if state == 'ok' and isinstance(snap, dict) and environment in ENVIRONMENTS and snap.get('environment') == environment:
        return snap, True, ''
    why = 'state ' + str(state) if state != 'ok' else f'snapshot environment {(snap or {}).get("environment")!r} is not {environment!r}'
    return None, False, f'exchange rules NOT applied ({why}) - legacy minimum-notional floor, not execution-realistic'


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


# ------------------------------------------------------------------ preset preflight (panel / API)
def slot_order_qtys(sl, capital, px, atr, max_lev=10.0):
    """The quantities the engine would size for this slot's first order on one coin at `capital` (no open lots, Kelly and
    governor multipliers 1). sl['mgmt'] must be the FULL merged management (strategies.merge_mgmt).
    -> dict(qty_raw, qty (leverage-capped), adds: [quantities of later orders: DCA safety orders / pyramid add])."""
    g = sl.get('mgmt') or {}
    sleeve_eq = capital * float(sl['share'])
    risk_usd = sleeve_eq * float(sl['risk'])
    z = risk_qty(g, risk_usd, px, atr, 1)
    cap = max(0.0, max_lev * sleeve_eq) / px
    q = min(z['qty'], cap)
    adds = [q * w for w in (z['weights'] or [])[1:]]
    if 'pyramid' in g: adds.append(q * float(g['pyramid'].get('frac', 0.5)))
    return dict(qty_raw=z['qty'], qty=q, adds=adds, risk_usd=risk_usd, cap_qty=cap)


def _sides_sd(sl):
    """Directions the slot can trade -> list of sd (+1 long, -1 short). Unknown -> both (conservative)."""
    sides = (sl.get('sides') or 'both') if isinstance(sl, dict) else 'both'
    return [1] if sides == 'long' else [-1] if sides == 'short' else [1, -1]


def planned_legs(sl, px, atr):
    """The exposure-increasing orders the engine predeclares after the first order of this slot (what _create_lot plans):
    DCA safety order k (k = 1..dca.n): quantity q0 * scale**k at level fill - sd*k*step_atr*ATR;
    pyramid add j (j = 1..pyramid.n): quantity q0 * frac at fill + sd*j*step_r*R.
    q0 = the executed (step-floored) first-order quantity. The price of each leg is the LOWEST planned price over the
    directions the slot trades (lower price = smaller notional = the harder case for minNotional), so a pass is never
    claimed on the easier side only. -> [dict(leg, mult, px)] (empty when nothing is planned, e.g. dca.n = 0)."""
    g = sl.get('mgmt') or {}
    sds = _sides_sd(sl)
    legs = []
    if 'dca' in g:
        dc = g['dca']
        for k in range(1, int(dc.get('n', 0) or 0) + 1):
            lv = min(px - sd * k * float(dc['step_atr']) * atr for sd in sds)
            legs.append(dict(leg=f'DCA safety order {k}', mult=float(dc['scale']) ** k, px=max(lv, 1e-12)))
    if 'pyramid' in g:
        py = g['pyramid']
        R = risk_qty(g, 1.0, px, atr, 1)['R']
        for j in range(1, int(py.get('n', 1) or 0) + 1):
            lv = min(px + sd * j * float(py.get('step_r', 0)) * R for sd in sds)
            legs.append(dict(leg=f'pyramid add {j}', mult=float(py.get('frac', 0.5)), px=max(lv, 1e-12)))
    return legs


def _plan_check(sl, capital, px, atr, rule, legs, max_lev):
    """Entry + every planned leg at `capital`, exactly as the engine gates them (size_check on the first order, then
    size_check(q0 * mult) per leg, q0 = the floored first-order quantity). -> (entry decision, [leg decisions], o)."""
    o = slot_order_qtys(sl, capital, px, atr, max_lev)
    d = size_check(o['qty_raw'], o['qty'], px, rule)
    q0 = d['qty']
    return d, [size_check(q0 * lg['mult'], q0 * lg['mult'], lg['px'], rule) for lg in legs], o


def _confirm_min_cap(min_cap, ok_at):
    """Float safety net: raise the linear estimate until the real check passes (bounded)."""
    if not math.isfinite(min_cap): return min_cap
    for _ in range(60):
        if ok_at(min_cap): break
        min_cap *= 1.0005
    return min_cap


def _cap_up(c):
    """Minimum capital for display: rounded UP to a whole USDT (never shows a capital that is still too small)."""
    return float(math.ceil(round(c, 6))) if math.isfinite(c) else None


def preflight(slots, capital, market, rules, rules_state='ok', rules_detail='', max_lev=10.0):
    """Exchange-filter preflight of a set of slots at `capital` (what the engine would do at today's prices).
    slots: [dict(id, key, share, risk, tf, symbols: [..], mgmt: FULL merged management, sides (optional, default both))];
    market: {(symbol, tf): dict(px, atr, optional atr_basis / atr_ts / price_ts)}; rules: {symbol: rule};
    rules_state: snapshot_state()[0].
    A coin/slot pair is FULLY tradable only when the first order AND every predeclared exposure-increasing order of its
    plan (DCA safety orders, pyramid adds - planned_legs) pass the exchange minimums.
    -> dict(status, estimate, entry_executable_pct (first order passes), plan_executable_pct (whole plan passes),
            executable_pct (= plan_executable_pct, kept for older callers), pairs, ok (entry ok), plan_ok, undersized
            (first order too small), unknown, add_undersized (first order ok, a later leg too small, with per-leg detail),
            min_capital_all (capital for every pair's whole plan), min_capital_leg / min_capital_pair (what sets it),
            warning, rules_state, rules_detail)
    status 'ok' only when every pair's whole plan passes; 'partial' when any first order or later leg fails;
    'infeasible' when no first order passes - and only when rules_state == 'ok' and every pair had a rule and market
    data; otherwise 'unknown' (estimate keeps what the placeholder rules say). Never a green pass on unknown rules."""
    rows, under, unknown, add_under = [], [], [], []
    for sl in slots:
        for sym in sl['symbols']:
            m = market.get((sym, sl.get('tf') or '4h')); r = (rules or {}).get(sym)
            tag = dict(slot=sl.get('id') or sl['key'], key=sl['key'], symbol=sym, tf=sl.get('tf') or '4h')
            if not m or not r or not (m.get('px') or 0) > 0 or not (m.get('atr') or 0) > 0:
                unknown.append(dict(tag, why='no exchange rule' if not r else 'no recent price / ATR')); continue
            px, atr = float(m['px']), float(m['atr'])
            legs = planned_legs(sl, px, atr)
            d, lds, o = _plan_check(sl, capital, px, atr, r, legs, max_lev)
            per_cap = o['qty'] / capital if capital > 0 else 0          # quantity scales linearly with capital
            step = r.get('step')
            # minimum capital per leg: the first order needs q >= required_qty(px); leg k needs floor(q0 * mult) >= its
            # required quantity, i.e. q0 >= ceil_step(required / mult) (q0 itself a step multiple)
            caps = []
            need = required_qty(px, r)
            mc = need / per_cap if per_cap > 0 else math.inf
            caps.append(('first order', _confirm_min_cap(mc, lambda c: bool(_plan_check(sl, c, px, atr, r, [], max_lev)[0]['ok']))))
            for i, lg in enumerate(legs):
                q0_need = required_qty(lg['px'], r) / lg['mult'] if lg['mult'] > 0 else math.inf
                if step and math.isfinite(q0_need): q0_need = ceil_step(q0_need, step)
                mc = q0_need / per_cap if per_cap > 0 else math.inf
                caps.append((lg['leg'], _confirm_min_cap(mc, lambda c, i=i: bool(_plan_check(sl, c, px, atr, r, legs[i:i + 1], max_lev)[1][0]['ok']))))
            leg_name, min_cap = max(caps, key=lambda x: x[1])
            entry_cap = caps[0][1]
            min_risk = float(sl['risk']) * min_cap / capital if capital > 0 and math.isfinite(min_cap) else math.inf
            # leg code from the floored quantity (size_check(q, q) would call a step-floor failure 'leverage_cap')
            leg_rows = [dict(leg=lg['leg'], ok=bool(ld['ok']),
                             code='ok' if ld['ok'] else 'below_min_qty' if ld['qty'] < r['min_qty'] else 'below_min_notional',
                             qty=ld['qty'], px=round(lg['px'], 10), notional=round(ld['qty'] * lg['px'], 4), min_capital=_cap_up(c))
                        for lg, ld, (_, c) in zip(legs, lds, caps[1:])]
            plan_ok = bool(d['ok']) and all(x['ok'] for x in leg_rows)
            row = dict(tag, ok=bool(d['ok']), plan_ok=plan_ok, code=d['code'], qty=d['qty'], notional=round(d['qty'] * px, 4),
                       risk_notional=round(o['qty_raw'] * px, 4), min_notional=r['min_notional'], min_qty=r['min_qty'],
                       step=r['step'], px=px, atr=atr, atr_basis=m.get('atr_basis'), atr_ts=m.get('atr_ts'), price_ts=m.get('price_ts'),
                       legs=leg_rows,
                       min_capital=_cap_up(min_cap), min_capital_leg=leg_name, entry_min_capital=_cap_up(entry_cap),
                       min_risk_pct=round(min_risk * 100, 2) if math.isfinite(min_risk) else None)
            rows.append(row)
            if not d['ok']: under.append(dict(row, reason=d['reason']))
            elif not plan_ok:
                bad = [x for x in leg_rows if not x['ok']]
                add_under.append(dict(tag, legs=bad, min_capital=row['min_capital'], min_capital_leg=leg_name,
                                      min_risk_pct=row['min_risk_pct'],
                                      reason=f'{", ".join(x["leg"] for x in bad)} of this slot is below the Binance minimum and will be skipped'))
    n = len(rows); n_ok = sum(1 for x in rows if x['ok']); n_plan = sum(1 for x in rows if x['plan_ok'])
    pct_entry = round(n_ok / n * 100, 1) if n else None
    pct_plan = round(n_plan / n * 100, 1) if n else None
    est = 'infeasible' if n and not n_ok else 'partial' if n_plan < n else 'ok' if n else 'unknown'
    status = est if rules_state == 'ok' and not unknown and n else 'unknown'
    worst = max((x for x in rows if x['min_capital'] is not None), key=lambda x: x['min_capital'], default=None)
    min_all = worst['min_capital'] if worst else None
    if any(x['min_capital'] is None for x in rows): min_all = worst = None
    min_leg = worst['min_capital_leg'] if worst else None
    min_pair = f"{worst['slot']} {worst['symbol']} {worst['tf']}" if worst else None
    w = []
    if est == 'infeasible': w.append(f'This profile cannot place any trade at {capital:,.0f} USDT: every coin/slot is below the Binance minimum order size.')
    elif est == 'partial':
        if under: w.append(f'{len(under)} of {n} coin/slot pairs are below the Binance minimum order size at {capital:,.0f} USDT - their signals will be skipped.')
        if add_under: w.append(f'{len(add_under)} of {n} coin/slot pair(s) can open but some planned later orders (pyramid adds / safety orders) are below the Binance minimum and will be skipped.')
    if min_all and est != 'ok':
        w.append(f'Estimated capital for every pair\'s full order plan to be tradable: about {min_all:,.0f} USDT, set by {min_leg} on {min_pair} (or raise the risk %).')
    if status == 'unknown':
        w.append('Exchange rules unknown or unverified (' + (rules_detail or rules_state) + ') - this check is an estimate, not a pass.'
                 if rules_state != 'ok' else f'{len(unknown)} coin/slot pair(s) could not be checked (no rule or no recent price).')
    return dict(status=status, estimate=est, executable_pct=pct_plan, entry_executable_pct=pct_entry, plan_executable_pct=pct_plan,
                pairs=n, ok=n_ok, plan_ok=n_plan, undersized=under, unknown=unknown,
                add_undersized=add_under, min_capital_all=min_all, min_capital_leg=min_leg, min_capital_pair=min_pair,
                warning=' '.join(w), rules_state=rules_state, rules_detail=rules_detail, capital=capital)
