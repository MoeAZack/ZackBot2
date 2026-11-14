"""R6-PAPER-01 account-4 overlay: wires r6_regime_flip into an ALREADY RUNNING PAPER engine without changing engine.py,
app.py or any other deployed file. Nothing here runs on import. NOT DEPLOYED: the canary step is Integration's call.

install(engine, app_module)          after connect(), before the trading loop: refuse unless PAPER, exact testnet origin,
                                     no book mode, market entries, no rotation preset, and a testnet rule for every frozen
                                     symbol (no silent dropping); register the PAPER strategy with a per-decision settings
                                     guard; let exactly the frozen 163-coin list through the panel's 80-coin check, only
                                     for slot EX14 of this strategy. If it fails while a slot/lot already refers to the
                                     strategy, an EXITS-ONLY entry is registered so the other slots keep cycling.
add_sleeve(engine, enabled=False)    put slot EX14 into the settings and pace the public data client. Only at canary
                                     time: a slot - even a switched-off one - makes the engine download candles for all
                                     163 coins every 4h.
set_enabled(engine, on)              switch EX14 on / off (rollback = off: open lots keep their stop and exits).
remove_sleeve(engine)                drop EX14 once it holds nothing (stops the extra data downloads).
uninstall(engine, app_module)        unregister - refused while any slot or lot still refers to the strategy.
"""
import json
import logging
import os
import threading
import time

import pandas as pd
import strategies as S
import r6_regime_flip as R

log = logging.getLogger('zackbot')

SHARE = 1 / 12                 # account 4 slot basis: $4,000 / 12 = $333.33 slot equity
RISK = 0.024                   # 0.024 x 333.33 = $8.00 risk per trade; MAX_LEVERAGE 10 x 333.33 = $3,333.33 notional cap
MAX_POS = 1
WEIGHT_SOFT_LIMIT = 1200       # pacer: wait for the next minute once the IP's used weight reaches this (Binance cap 2400)


class R6InstallError(RuntimeError):
    pass


def sleeve(enabled=False):
    """The exact EX14 slot (explicit frozen list: bypasses the global UNIVERSE and its 120-coin truncation)."""
    return dict(id=R.SLEEVE_ID, key=R.KEY, name=S.STRATEGIES.get(R.KEY, {}).get('name', '[PAPER CANARY] R6 regime flip L/S 4h'), enabled=bool(enabled), share=SHARE,
                risk=RISK, max_pos=MAX_POS, symbols=list(R.SYMBOLS), sides='both', mgmt={}, tf='4h')


RISK_USD_MAX = 8.0              # first-canary ceilings (Codex): at most $8 risk, $3,333.33 notional cap
NOTIONAL_CAP_MAX = 3333.34
_SHAPE = ('id', 'key', 'share', 'risk', 'max_pos', 'symbols', 'sides', 'mgmt', 'tf')
_ALLOWED = set(_SHAPE) | {'name', 'enabled'}


def slot_problem(sl):
    """None when sl is exactly the frozen first-canary EX14 shape (only 'enabled' may differ)."""
    want = dict(id=R.SLEEVE_ID, key=R.KEY, share=SHARE, risk=RISK, max_pos=MAX_POS, symbols=list(R.SYMBOLS),
                sides='both', mgmt={}, tf='4h')
    if not isinstance(sl, dict): return 'EX14 slot malformed'
    extra = sorted(k for k in sl if k not in _ALLOWED and sl.get(k) not in (None, '', [], {}, 'any'))
    if extra: return f"EX14 slot has extra settings: {', '.join(extra)}"
    for k in _SHAPE:
        v, w = sl.get(k), want[k]
        if k in ('share', 'risk'):
            try: ok = abs(float(v) - w) <= 1e-9
            except (TypeError, ValueError): ok = False
        elif k == 'symbols': ok = isinstance(v, (list, tuple)) and list(v) == w
        elif k == 'mgmt': ok = not v
        else: ok = v == w
        if not ok: return f'EX14 slot {k} differs from the frozen first-canary shape'
    return None


def slots_problem(sleeves):
    """The strategy may live ONLY in the one EX14 slot, in its frozen shape; EX14 may hold nothing else."""
    r6 = [x for x in sleeves or [] if isinstance(x, dict) and (x.get('key') == R.KEY or x.get('id') == R.SLEEVE_ID)]
    if len(r6) > 1: return 'more than one slot uses R6 / the EX14 id'
    if r6:
        if r6[0].get('id') != R.SLEEVE_ID: return f"R6 is reserved for slot {R.SLEEVE_ID} (found on {r6[0].get('id')})"
        return slot_problem(r6[0])
    return None


def frame_resolver(engine):
    """frame -> symbol, by identity with the engine's own closed-candle cache (read only). compute_signals passes the
    cached DataFrame object itself to the strategy; anything else resolves to None (entry refused)."""
    def resolve(d):
        for (sym, tf), hit in list(getattr(engine, '_kc', {}).items()):
            if tf == '4h' and hit and hit[0] is d: return sym
        return None
    return resolve


def h1_fetcher(engine, clock=time.time):
    """fetch_1h(symbol, start, end): CLOSED 1h candles of the public data client (same source as the engine's candles),
    open time in [start, end), one request of <= R.H1_LIMIT candles."""
    def fetch(sym, start, end):
        s_ms = int(pd.Timestamp(start).value // 10**6); e_ms = int(pd.Timestamp(end).value // 10**6) - 1
        raw = engine.data.klines(sym, '1h', R.H1_LIMIT, end_time=e_ms, start_time=s_ms)
        now_ms = clock() * 1000
        rows = [(int(r[0]), float(r[7])) for r in raw if float(r[6]) < now_ms and s_ms <= int(r[0]) <= e_ms]
        return pd.DataFrame({'t': pd.to_datetime([x[0] for x in rows], unit='ms'), 'qv': [x[1] for x in rows]})
    return fetch


def install_weight_pacer(client, soft_limit=WEIGHT_SOFT_LIMIT, clock=time.time, sleep=time.sleep):
    """Wrap ONE client's _once (the public market-data client): after Binance reports X-MBX-USED-WEIGHT-1M >= soft_limit
    for the current minute, the next request waits for the next minute. Idempotent; order/stop clients are not touched."""
    if getattr(client, '_r6_paced', False): return client
    orig = client._once
    st = dict(used=0, minute=None)

    def paced(*a, **k):
        m = int(clock() // 60)
        if st['minute'] == m and st['used'] >= soft_limit:
            wait = 60 - clock() % 60 + 0.5
            log.info(f'R6 pacer: IP weight {st["used"]} >= {soft_limit} this minute - waiting {wait:.1f}s')
            sleep(wait)
        r, data = orig(*a, **k)
        try:
            w = (getattr(r, 'headers', None) or {}).get('X-MBX-USED-WEIGHT-1M')
            if w is not None: st.update(used=int(w), minute=int(clock() // 60))
        except (TypeError, ValueError):
            pass
        return r, data
    client._once = paced
    client._r6_paced = True
    client._r6_pacer_state = st
    return client


def allow_exact_list(app_module):
    """The panel's clean_symbols refuses lists above 80 coins and /api/sleeves re-validates EVERY slot on each save, so
    a 163-coin EX14 would block all slot saves (including rollbacks). Allow exactly the frozen list, and only while
    validating the EX14 slot of this strategy; every other slot / list keeps the stock check."""
    if getattr(app_module.validate_sleeve, '_r6_exact', False): return
    orig_clean, orig_validate = app_module.clean_symbols, app_module.validate_sleeve
    flag = threading.local()

    def clean_symbols(v):
        if getattr(flag, 'on', False) and isinstance(v, list) and v == list(R.SYMBOLS): return list(v)
        return orig_clean(v)

    def validate_sleeve(sl, *a, **k):
        flag.on = isinstance(sl, dict) and sl.get('id') == R.SLEEVE_ID and sl.get('key') == R.KEY
        try: out = orig_validate(sl, *a, **k)
        finally: flag.on = False
        if isinstance(sl, dict) and (sl.get('key') == R.KEY or sl.get('id') == R.SLEEVE_ID or out.get('key') == R.KEY
                                     or out.get('id') == R.SLEEVE_ID):
            why = slots_problem([out])
            if why: raise ValueError(why)
        return out
    clean_symbols._r6_orig = orig_clean
    validate_sleeve._r6_exact = True
    validate_sleeve._r6_orig = orig_validate
    app_module.clean_symbols = clean_symbols
    app_module.validate_sleeve = validate_sleeve


def restore_panel(app_module):
    v = app_module.validate_sleeve
    if getattr(v, '_r6_exact', False):
        app_module.validate_sleeve = v._r6_orig
        app_module.clean_symbols = app_module.clean_symbols._r6_orig


def admission_report(engine):
    missing = [s for s in R.SYMBOLS if s not in (engine.rules or {})]
    off = [s for s in R.SYMBOLS if engine.S.get('SYMBOLS_ON', {}).get(s, True) is False]
    return dict(symbols=len(R.SYMBOLS), symbols_sha256=R.symbols_sha256(), missing_rules=missing, switched_off=off,
                rules_source=(getattr(engine, 'rules_meta', None) or {}).get('environment'))


def settings_problem(engine):
    """Account settings that would break the frozen execution / $8 slot sizing. None = fine. Checked at install AND at
    every entry decision (settings can change at runtime through the panel)."""
    import engine as E
    Sg = engine.S
    if getattr(engine, 'live', True) is not False or (getattr(engine, 'cfg', None) or {}).get('MODE') != 'paper':
        return 'engine is not PAPER'
    if getattr(getattr(engine, 'trade', None), 'base', None) not in R.TESTNET_ORIGINS: return 'trading origin is not exact testnet'
    if Sg.get('PAPER_BOOKS') is True or Sg.get('TRUE_BOOKS') is True:
        return 'book mode would size EX14 from a book (B5_DISCOVERY), not the $8 slot basis'
    if Sg.get('ENTRY_ORDER') == 'maker': return 'maker entries would replace the frozen next-open market entry'
    if Sg.get('PRESET') == E.ROTATION_PRESET: return 'the breadth-rotation preset blocks non-rotation slots'
    return None


def overlap(engine):
    """Codex decision (r1): EX13/EX14 coin overlap ACCEPTED for the max1 / $8 canary - recorded, not blocked. Lists every
    other slot that can trade a frozen coin and the worst combined first-entry risk on one coin (slot basis)."""
    out, eq = {}, float(engine.S.get('CAPITAL_CAP') or 0)
    for sl in engine.S.get('SLEEVES', []):
        if sl.get('id') == R.SLEEVE_ID: continue
        common = sorted(set(engine.sleeve_symbols(sl, include_off=True)) & set(R.SYMBOLS))
        if common: out[sl['id']] = dict(key=sl.get('key'), enabled=bool(sl.get('enabled')), coins=len(common),
                                        risk_usd=round(eq * float(sl.get('share', 0)) * float(sl.get('risk', 0)), 2))
    worst = max([v['risk_usd'] for v in out.values()] or [0.0])
    return dict(slots=out, worst_combined_risk_usd_one_coin=round(RISK_USD_MAX + worst, 2))


def notes(engine):
    """Settings that can only scale risk DOWN or refuse an entry (allowed; reported for the canary record)."""
    Sg = engine.S
    return dict(overlap=overlap(engine), CTRL_BIAS=Sg.get('CTRL_BIAS'), CTRL_RISK=Sg.get('CTRL_RISK'), FEE_EDGE_GATE=Sg.get('FEE_EDGE_GATE'),
                AI_FILTER=Sg.get('AI_FILTER'), COMPOUND=Sg.get('COMPOUND'), CAPITAL_CAP=Sg.get('CAPITAL_CAP'),
                MAX_LEVERAGE=Sg.get('MAX_LEVERAGE'))


def risk_problem(engine, sl, eq):
    """Actual first-entry risk and notional cap of EX14 at sizing equity eq, with the engine's own multipliers (Kelly,
    governor - which can be > 1 - and the direction/risk control). None when both stay within the canary ceilings."""
    if eq is None: return 'sizing equity unknown'
    eq = float(eq)
    m = float(engine.kelly_mult(sl)) * float(engine.governor_mult())
    try:
        ce = engine.ctrl_now()
        if ce: m *= max(float(ce.get('long_factor', 1.0)), float(ce.get('short_factor', 1.0)))
    except Exception:
        pass
    risk_usd = eq * float(sl['share']) * float(sl['risk']) * m
    cap = float(engine.S.get('MAX_LEVERAGE') or 0) * eq * float(sl['share'])
    if risk_usd > RISK_USD_MAX + 1e-9: return f'EX14 risk ${risk_usd:.2f} > ${RISK_USD_MAX:.2f} (multipliers x{m:g})'
    if cap > NOTIONAL_CAP_MAX: return f'EX14 notional cap ${cap:,.2f} > ${NOTIONAL_CAP_MAX:,.2f}'
    return None


def _slot(engine):
    return next((x for x in engine.S.get('SLEEVES', []) if x.get('id') == R.SLEEVE_ID), None)


def _referenced(engine):
    return (any(x.get('key') == R.KEY for x in engine.S.get('SLEEVES', []))
            or any(l.get('key_strategy') == R.KEY or l.get('sleeve') == R.SLEEVE_ID for l in engine.state['lots'].values()))


def entry_guard(engine):
    """Per-decision guard handed to the strategy: settings still fine and the EX14 slot is on (an off slot spends no 1h
    fetches). Never raises."""
    def guard():
        try:
            why = settings_problem(engine)
            if why: return why
            sl = _slot(engine)
            if sl is None or sl.get('key') != R.KEY: return 'no EX14 slot'
            why = slots_problem(engine.S.get('SLEEVES'))
            if why: return why
            if not sl.get('enabled'): return 'EX14 switched off'
            return risk_problem(engine, sl, getattr(engine, 'last_eq', None))
        except Exception as ex:
            return f'settings check error ({type(ex).__name__})'
    return guard


def lot_entry(engine):
    """symbol -> (side, entry bar open) of the EX14 lot on that symbol, from the bar recorded at entry (r6_entry_bar), else
    the 4h bar its fill fell in (an entry is submitted within ENTRY_WINDOW_S of its bar's open)."""
    def f(sym):
        for l in list(engine.state['lots'].values()):
            if l.get('sleeve') == R.SLEEVE_ID and l.get('symbol') == sym and not l.get('manual'):
                b = l.get('r6_entry_bar')
                t = pd.Timestamp(b or l['opened'])
                if t.tzinfo is not None: t = t.tz_convert(None)
                return l['side'], (t if b else t.floor('4h'))
        return None
    return f


def _record(engine, row):
    try:
        with open(os.path.join(engine.dir, 'r6_entries.jsonl'), 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(row, default=str) + '\n')
    except Exception as ex:
        log.warning(f'R6 entry record not written ({type(ex).__name__})')


def wrap_open_lot(engine, clock=time.time):
    """Instance-level wrapper (no engine.py change) for automatic EX14 entries ONLY: re-check the submission window, the
    settings / slot shape guard and the actual risk at the eq the engine is about to size with, immediately before the
    entry attempt; record the entry bar on the lot (canonical time exit) and the submission latency / slippage. Manual
    trades, other slots, adds, exits and stops are untouched."""
    if getattr(engine, '_r6_open_wrapped', False): return
    orig = engine.open_lot
    guard = entry_guard(engine)
    sent = threading.local()                           # write-ahead time of THIS call's EX14 entry order (send time)
    orig_ue = engine._remember_unconfirmed

    def remember_unconfirmed(plan, cid, *a, **k):
        key = orig_ue(plan, cid, *a, **k)              # raises = nothing sent; then nothing is recorded here
        try:
            if getattr(sent, 'on', False) and ((plan or {}).get('sl') or {}).get('id') == R.SLEEVE_ID:
                sent.t = engine.state['unconfirmed_entries'][key]['t']
        except Exception:
            pass
        return key
    engine._remember_unconfirmed = remember_unconfirmed

    def open_lot(sl, sym, side, sg, df, eq, *a, **k):
        if not (isinstance(sl, dict) and sl.get('id') == R.SLEEVE_ID and not k.get('manual') and not (len(a) > 1 and a[1])):
            return orig(sl, sym, side, sg, df, eq, *a, **k)
        now = clock()
        dec = pd.Timestamp(str((sg or {}).get('time')))
        why = R.window_problem(dec, now) or guard() or risk_problem(engine, sl, eq)
        close_ts = (dec + R.STEP).timestamp()
        iso = lambda t: pd.Timestamp(t, unit='s').isoformat() if t is not None else None
        lat = lambda t: round(t - close_ts, 1) if t is not None else None
        # attempt_* = this wrapper's window/guard/risk check, BEFORE orig(open_lot) does its own work; send_* = the engine's
        # write-ahead record made immediately before the market order goes to the exchange (None = no order sent);
        # fill_* = when the engine recorded the filled lot (after the exchange answered).
        row = dict(event='entry', symbol=sym, side=side, decision_open=str(dec), decision_close=str(dec + R.STEP),
                   attempt_utc=iso(now), attempt_latency_s=lat(now), send_utc=None, send_latency_s=None,
                   fill_utc=None, fill_latency_s=None, signal_close=(sg or {}).get('close'))
        if why:
            engine.last_skip = f'R6: {why}'; row.update(result='refused', reason=why); _record(engine, row)
            log.warning(f'R6 {sym} {side}: entry refused before submission - {why}')
            return False
        before = set(engine.state['lots'])
        sent.on, sent.t = True, None
        try:
            ok = orig(sl, sym, side, sg, df, eq, *a, **k)
        finally:
            sent.on = False
        row.update(send_utc=iso(sent.t), send_latency_s=lat(sent.t))
        new = [kk for kk in engine.state['lots'] if kk not in before and engine.state['lots'][kk].get('sleeve') == R.SLEEVE_ID]
        if ok and new:
            lot = engine.state['lots'][new[-1]]
            lot['r6_entry_bar'] = (dec + R.STEP).isoformat()
            sd = 1 if side == 'LONG' else -1
            c = float((sg or {}).get('close') or 0)
            try:
                ft = pd.Timestamp(lot['opened'])
                ft = (ft.tz_convert('UTC') if ft.tzinfo is not None else ft.tz_localize('UTC')).timestamp()
            except Exception:
                ft = None
            row.update(fill_utc=iso(ft), fill_latency_s=lat(ft))
            row.update(result='filled', fill=lot.get('avg'), qty=lot.get('qty'), risk_usd=lot.get('risk_usd'),
                       slippage_bps=round(sd * (float(lot['avg']) - c) / c * 1e4, 2) if c else None)
        else:
            row.update(result='opened' if ok else 'not opened', reason=getattr(engine, 'last_skip', ''))
        _record(engine, row)
        return ok
    engine.open_lot = open_lot
    engine._r6_open_wrapped = True


def install(engine, app_module=None, pace=True):
    """Call after engine.connect() (rules loaded) and BEFORE the trading loop starts. Raises R6InstallError when anything
    is off; if a saved EX14 slot / lot already refers to the strategy, an EXITS-ONLY entry is registered first so the
    other slots keep their 4h cycle (a missing key would raise KeyError for every slot)."""
    try:
        why = settings_problem(engine)
        if why: raise R6InstallError(f'R6 overlay: {why}')
        base = engine.trade.base
        if not engine.rules: raise R6InstallError('R6 overlay: exchange rules not loaded (connect first)')
        rep = admission_report(engine)
        if rep['missing_rules']:
            raise R6InstallError(f"R6 overlay: {len(rep['missing_rules'])} frozen symbol(s) without a testnet rule: "
                                 f"{', '.join(rep['missing_rules'])} - not installed (no silent dropping)")
        why = slots_problem(engine.S.get('SLEEVES'))
        if why: raise R6InstallError(f'R6 overlay: {why}')
        if pace:                                       # before any slot can become visible through any path
            if engine.data is engine.trade: raise R6InstallError('R6 overlay: data client is the order client - not paced')
            install_weight_pacer(engine.data)
        if app_module is not None: allow_exact_list(app_module)
        wrap_open_lot(engine)
        R.register_for_paper('paper', base, frame_resolver(engine), h1_fetcher(engine), guard=entry_guard(engine),
                             lot_entry=lot_entry(engine))
    except Exception as ex:
        if _referenced(engine) or R._PAPER_ENTRY is not None:
            try:                                       # read-only deadline context; never a fetch or an entry path
                res, le = frame_resolver(engine), lot_entry(engine)
            except Exception:
                res = le = None
            R.register_exits_only(str(ex), resolve=res, lot_entry=le)
        raise
    rep.update(registered=R.KEY, base=base, paced=bool(getattr(engine.data, '_r6_paced', False)),
               panel_exact_list=app_module is not None, notes=notes(engine))
    log.info('R6 overlay installed: ' + json.dumps(rep, default=str))
    return rep


def _shares_ok(engine, sl_list):
    gshare = sum(g.get('share', 0) for g in engine.S.get('GRID_SLOTS', []) if g.get('enabled'))
    return sum(x['share'] for x in sl_list if x['enabled']) + gshare <= 1.0001


def add_sleeve(engine, enabled=False):
    if S.STRATEGIES.get(R.KEY) is not R._PAPER_ENTRY or R._PAPER_ENTRY is None:
        raise R6InstallError('R6 overlay: install() first')
    want = sleeve(enabled)
    with engine.lock:
        cur = [x for x in engine.S['SLEEVES'] if x['id'] == R.SLEEVE_ID]
        if cur:
            if {k: v for k, v in cur[0].items() if k != 'enabled'} != {k: v for k, v in want.items() if k != 'enabled'}:
                raise R6InstallError('R6 overlay: a different EX14 slot already exists - not changed')
            return cur[0]
        new = list(engine.S['SLEEVES']) + [want]
        if not _shares_ok(engine, new): raise R6InstallError('R6 overlay: enabled capital shares would exceed 100%')
        why = slots_problem(new)
        if why: raise R6InstallError(f'R6 overlay: {why}')
        if not getattr(engine.data, '_r6_paced', False) and engine.data is not engine.trade:
            install_weight_pacer(engine.data)
        engine.S['SLEEVES'] = new; engine.save_settings()
    log.info(f"R6 overlay: slot {R.SLEEVE_ID} added ({'on' if enabled else 'off'})")
    return want


def set_enabled(engine, on):
    with engine.lock:
        new = [dict(x, enabled=bool(on)) if x['id'] == R.SLEEVE_ID else x for x in engine.S['SLEEVES']]
        if not any(x['id'] == R.SLEEVE_ID for x in new): raise R6InstallError('R6 overlay: no EX14 slot')
        if on and not _shares_ok(engine, new): raise R6InstallError('R6 overlay: enabled capital shares would exceed 100%')
        engine.S['SLEEVES'] = new; engine.save_settings()
    log.info(f"R6 overlay: slot {R.SLEEVE_ID} switched {'on' if on else 'off'}")


def remove_sleeve(engine):
    with engine.lock:
        if any(l.get('sleeve') == R.SLEEVE_ID for l in engine.state['lots'].values()):
            raise R6InstallError('R6 overlay: EX14 still holds a position - switch it off and let it exit first')
        engine.S['SLEEVES'] = [x for x in engine.S['SLEEVES'] if x['id'] != R.SLEEVE_ID]; engine.save_settings()
    log.info(f'R6 overlay: slot {R.SLEEVE_ID} removed')


def uninstall(engine, app_module=None):
    """Last rollback step: unregister only when no slot and no lot refers to the strategy (else KeyError for all slots)."""
    if _referenced(engine): raise R6InstallError('R6 overlay: a slot or lot still refers to the strategy - remove it first')
    R.unregister()
    if app_module is not None: restore_panel(app_module)
