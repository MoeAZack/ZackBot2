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
    return dict(id=R.SLEEVE_ID, key=R.KEY, name=S.STRATEGIES[R.KEY]['name'], enabled=bool(enabled), share=SHARE,
                risk=RISK, max_pos=MAX_POS, symbols=list(R.SYMBOLS), sides='both', mgmt={}, tf='4h')


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
        try: return orig_validate(sl, *a, **k)
        finally: flag.on = False
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


def notes(engine):
    """Settings that can only scale risk DOWN or refuse an entry (allowed; reported for the canary record)."""
    Sg = engine.S
    return dict(CTRL_BIAS=Sg.get('CTRL_BIAS'), CTRL_RISK=Sg.get('CTRL_RISK'), FEE_EDGE_GATE=Sg.get('FEE_EDGE_GATE'),
                AI_FILTER=Sg.get('AI_FILTER'), COMPOUND=Sg.get('COMPOUND'), CAPITAL_CAP=Sg.get('CAPITAL_CAP'),
                MAX_LEVERAGE=Sg.get('MAX_LEVERAGE'))


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
            if not sl.get('enabled'): return 'EX14 switched off'
            return None
        except Exception as ex:
            return f'settings check error ({type(ex).__name__})'
    return guard


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
        if pace and _slot(engine) is not None: install_weight_pacer(engine.data)
        if app_module is not None: allow_exact_list(app_module)
        R.register_for_paper('paper', base, frame_resolver(engine), h1_fetcher(engine), guard=entry_guard(engine))
    except Exception as ex:
        if _referenced(engine): R.register_exits_only(str(ex))
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
        engine.S['SLEEVES'] = new; engine.save_settings()
    install_weight_pacer(engine.data)              # from now on the engine downloads 163 more 4h candle sets per close
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
