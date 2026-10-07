"""BT02 (issue #14): exchange-filter feasibility - ONE pure function (feasibility.size_check) shared by the backtester,
replay, the live engine's entry / add gates and the panel preflight.

Covers: step rounding (floor, like the engine's _rd), minQty, minNotional (BTC 50 / ETH 20 elevated, altcoins 5),
skipped-signal accounting instead of a synthetic fill, rule changes between snapshots, stale / unverified / missing
snapshots -> 'unknown' (never a green pass), and the safety rule: a quantity is never increased above the risk size.
Engine parity: the pre-BT02 engine code is kept below verbatim as a reference and compared on a dense input grid.
"""
import copy, json, math, os, random, tempfile
import numpy as np, pandas as pd, pytest
import backtest as B
import feasibility as F
import exchange_rules as XR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = 1_790_000_000.0                                   # 2026-09-21 (fixed: the module never reads the clock)
ISO_NOW = '2026-09-21T00:00:00Z'


# ---------------------------------------------------------------- the pre-BT02 engine code, verbatim (reference)
def old_rd(x, step):
    dec = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(math.floor(x / step + 1e-9) * step, dec)


def old_open_lot_gate(qty_raw, qty, px, r):
    """engine.open_lot before BT02: returns (skip?, qty, last_skip)."""
    qty = old_rd(qty, r['step'])
    if qty < r['min_qty'] or qty * px < r['min_notional']:
        return True, qty, ('leverage cap reached for this slot' if qty_raw * px >= r['min_notional'] and qty_raw >= r['min_qty']
                           else 'size below Binance minimum (raise capital or risk)')
    return False, qty, None


def old_add_gate(q, px, r):
    """engine._add_qty before BT02: returns (refused?, qty)."""
    q = old_rd(q, r['step'])
    return (q < r['min_qty'] or q * px < r['min_notional']), q


def old_connect_rules(info):
    rules = {}
    for s in info['symbols']:
        if s.get('contractType') != 'PERPETUAL' or s.get('status') != 'TRADING': continue
        f = {x['filterType']: x for x in s['filters']}
        rules[s['symbol']] = dict(step=float(f['MARKET_LOT_SIZE']['stepSize']), min_qty=float(f['MARKET_LOT_SIZE']['minQty']),
                                  tick=float(f['PRICE_FILTER']['tickSize']),
                                  min_notional=float(f.get('MIN_NOTIONAL', {}).get('notional', 5)))
    return rules


def info(rules, extra=()):
    return {'symbols': [{'symbol': s, 'contractType': 'PERPETUAL', 'status': 'TRADING', 'filters': [
        {'filterType': 'MARKET_LOT_SIZE', 'stepSize': str(r['step']), 'minQty': str(r['min_qty'])},
        {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'}] + ([{'filterType': 'MIN_NOTIONAL', 'notional': str(r['min_notional'])}]
                                                              if r.get('min_notional') is not None else [])} for s, r in rules.items()] + list(extra)}


TESTNET = dict(BTCUSDT=dict(step=0.001, min_qty=0.001, min_notional=50.0), ETHUSDT=dict(step=0.001, min_qty=0.001, min_notional=20.0),
               SOLUSDT=dict(step=0.01, min_qty=0.01, min_notional=5.0), DOGEUSDT=dict(step=1.0, min_qty=1.0, min_notional=5.0))


def snap(rules=TESTNET, env='testnet', verified=True, fetched_at=ISO_NOW, version=1):
    return dict(schema=F.SCHEMA, version=version, environment=env, source='test', fetched_at=fetched_at, verified=verified, note='',
                symbols=copy.deepcopy(rules))


# ---------------------------------------------------------------- 1) engine parity: identical decisions before / after
def test_round_step_is_the_old_engine_rd_bit_for_bit():
    rng = random.Random(7)
    for _ in range(20000):
        step = rng.choice([1e-5, 1e-4, 0.001, 0.01, 0.1, 1.0, 10.0, 0.5, 0.005])
        x = rng.choice([rng.uniform(0, 5), rng.uniform(0, 5000), rng.randint(0, 999) * step, rng.randint(0, 999) * step * (1 - 1e-12)])
        assert F.round_step(x, step) == old_rd(x, step), (x, step)


def test_entry_and_add_gate_decisions_identical_to_pre_bt02_engine():
    """Dense grid incl. exact boundaries: same skip / same quantity / same skip text as the old inline engine code."""
    rng = random.Random(3)
    rules = [dict(step=s, min_qty=mq, min_notional=mn) for s, mq in ((0.001, 0.001), (0.01, 0.01), (1.0, 1.0), (1e-5, 1e-5), (0.1, 0.3))
             for mn in (5.0, 20.0, 50.0, 100.0)]
    n = 0
    for r in rules:
        for _ in range(1500):
            px = rng.choice([0.08, 1.7, 23.4, 101.0, 2500.0, 65000.0, 120000.0])
            qty_raw = rng.choice([rng.uniform(0, 3) * r['min_notional'] / px, r['min_notional'] / px, r['min_qty'],
                                  r['min_qty'] * rng.randint(1, 5), rng.uniform(0, 4) * r['min_qty']])
            qty = rng.choice([qty_raw, qty_raw * rng.uniform(0, 1), r['min_notional'] / px * rng.uniform(.5, 1.5)])
            qty = min(qty, qty_raw)
            skip, q_old, why_old = old_open_lot_gate(qty_raw, qty, px, r)
            d = F.size_check(qty_raw, qty, px, r)
            assert (not d['ok']) == skip and d['qty'] == q_old and (d['reason'] or None) == why_old, (r, px, qty_raw, qty, d)
            ref, q2 = old_add_gate(qty, px, r)
            d2 = F.size_check(qty, qty, px, r)
            assert (not d2['ok']) == ref and (d2['qty'] == q2), (r, px, qty, d2)
            n += 1
    assert n == len(rules) * 1500


def test_engine_add_below_minimum_is_refused_without_an_order():
    import test_safety as TS
    import engine as E
    e, _ = TS.mk_engine()
    e.rules['ETHUSDT'] = dict(step=0.001, min_qty=0.001, tick=0.01, min_notional=20.0)
    assert e.open_lot(E.sleeve('B', 'ema_st', 1.0, .01, 2, ['ETHUSDT']), 'ETHUSDT', 'LONG',
                      dict(close=50.0, atr=1.0, time='2026-01-01T00:00:00'), None, 500.0) is True
    lot = list(e.state['lots'].values())[0]; q0 = lot['qty']; n_open = e.trade.calls.count('open')
    assert e._add_qty(lot, 0.3, 50.0, 'pyramid_add') is False            # 15 USDT < 20: refused, nothing sent
    assert e.trade.calls.count('open') == n_open and lot['qty'] == q0
    assert e._add_qty(lot, 0.4567, 50.0, 'pyramid_add') is True          # 22.8 USDT: sent, floored to the step
    assert lot['qty'] == pytest.approx(q0 + 0.456)


def test_engine_skip_texts_match_the_shared_reasons():
    src = open(os.path.join(ROOT, 'engine.py'), encoding='utf-8').read()
    assert f"'{F.REASON_LEV_CAP}'" in src and f"'{F.REASON_BELOW_MIN}'" in src


def test_connect_parser_identical_to_pre_bt02_engine():
    extra = [{'symbol': 'XQUARTER', 'contractType': 'CURRENT_QUARTER', 'status': 'TRADING', 'filters': []},
             {'symbol': 'XHALT', 'contractType': 'PERPETUAL', 'status': 'BREAK', 'filters': []}]
    rules = dict(TESTNET, NOMINUSDT=dict(step=0.1, min_qty=0.1, min_notional=None))
    i = info(rules, extra)
    assert F.rules_from_exchange_info(i) == old_connect_rules(i)
    assert F.rules_from_exchange_info(i)['NOMINUSDT']['min_notional'] == 5.0          # default kept
    assert 'XQUARTER' not in F.rules_from_exchange_info(i) and 'XHALT' not in F.rules_from_exchange_info(i)


def test_engine_open_lot_uses_the_shared_check(monkeypatch):
    """The live engine skips with the same text and logs, and calls feasibility.size_check exactly once per entry."""
    import test_safety as TS
    import engine as E
    e, _ = TS.mk_engine()
    e.rules['BTCUSDT'] = dict(step=0.001, min_qty=0.001, tick=0.01, min_notional=50.0)
    calls = []
    real = F.size_check
    monkeypatch.setattr(F, 'size_check', lambda *a: calls.append(a) or real(*a))
    sl = E.sleeve('A', 'ema_st', 1.0, .01, 2, ['BTCUSDT'])
    sg = dict(close=100.0, atr=2.0, time='2026-01-01T00:00:00')
    # risk 500 * 1% = 5 USDT / (2.5 * 2) = 1 BTC-unit at 100 -> 100 USDT notional: placed, qty floored to the step
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', sg, None, 500.0) is True
    assert len(calls) == 1 and calls[0][3]['min_notional'] == 50.0
    assert e.state['lots'] and list(e.state['lots'].values())[0]['qty'] == 1.0
    # ETH (min 20, mark 50): 0.5 USDT risk / 5 -> 0.1 ETH = 5 USDT < 20: skipped with the old wording, no order sent
    e.rules['ETHUSDT'] = dict(step=0.001, min_qty=0.001, tick=0.01, min_notional=20.0)
    n_open = e.trade.calls.count('open')
    assert e.open_lot(E.sleeve('B', 'ema_st', 1.0, .01, 2, ['ETHUSDT']), 'ETHUSDT', 'LONG', sg, None, 500.0, risk=0.001) is False
    assert e.last_skip == 'size below Binance minimum (raise capital or risk)' and e.trade.calls.count('open') == n_open
    assert len(calls) == 2 and calls[1][3]['min_notional'] == 20.0


# ---------------------------------------------------------------- 2) pure checks: minimums, step, no round-up
def test_btc_eth_elevated_minimums_and_altcoin_5():
    px = dict(BTCUSDT=60000.0, ETHUSDT=2500.0, SOLUSDT=150.0)
    for notional, ok in ((30.0, dict(BTCUSDT=False, ETHUSDT=True, SOLUSDT=True)), (15.0, dict(BTCUSDT=False, ETHUSDT=False, SOLUSDT=True)),
                         (4.0, dict(BTCUSDT=False, ETHUSDT=False, SOLUSDT=False)), (130.0, dict(BTCUSDT=True, ETHUSDT=True, SOLUSDT=True))):
        for s, p in px.items():
            q = notional / p
            d = F.size_check(q, q, p, TESTNET[s])
            assert d['ok'] is ok[s], (s, notional, d)
            if not ok[s]: assert d['reason'] == F.REASON_BELOW_MIN and d['code'] in ('below_min_notional', 'below_min_qty')
    # BTC: 0.001 BTC at 60k = 60 USDT -> the step/minQty, not the 50 notional, is what binds at 55 USDT of risk notional
    d = F.size_check(55 / 60000, 55 / 60000, 60000.0, TESTNET['BTCUSDT'])
    assert d['ok'] is False and d['code'] == 'below_min_qty' and d['qty'] == 0.0


def test_step_rounding_floors_never_rounds_up():
    d = F.size_check(2.79, 2.79, 10.0, TESTNET['DOGEUSDT'])
    assert d['ok'] and d['qty'] == 2.0
    d = F.size_check(0.0199999, 0.0199999, 3000.0, TESTNET['ETHUSDT'])
    assert d['ok'] and d['qty'] == 0.019
    rng = random.Random(11)
    for _ in range(5000):
        r = rng.choice(list(TESTNET.values())); px = rng.uniform(0.05, 90000); q = rng.uniform(0, 5) * r['min_notional'] / px
        cap = q * rng.uniform(0.3, 1.0)
        d = F.size_check(q, cap, px, r)
        assert d['qty'] <= cap + 1e-12 <= q + 1e-12            # never above the leverage-capped, never above the risk size


def test_leverage_cap_is_reported_separately():
    r = TESTNET['SOLUSDT']
    d = F.size_check(1.0, 0.02, 150.0, r)                    # risk size 150 USDT ok; the cap left 3 USDT
    assert d['ok'] is False and d['code'] == 'leverage_cap' and d['reason'] == F.REASON_LEV_CAP


def test_no_rule_means_unknown_not_pass():
    d = F.size_check(1.0, 1.0, 100.0, None)
    assert d['ok'] is None and d['code'] == 'unknown' and d['qty'] == 1.0


def test_required_qty():
    assert F.required_qty(60000.0, TESTNET['BTCUSDT']) == 0.001
    assert F.required_qty(20000.0, TESTNET['BTCUSDT']) == 0.003          # 50 / 20000 = 0.0025 -> 0.003
    assert F.required_qty(0.1, TESTNET['DOGEUSDT']) == 50.0
    for p in (0.07, 1.3, 33.3, 2999.0):
        for r in TESTNET.values():
            q = F.required_qty(p, r)
            assert F.size_check(q, q, p, r)['ok'] and not F.size_check(q - r['step'], q - r['step'], p, r)['ok']


def test_risk_qty_dca_formula():
    """n=3, step 1 ATR, scale 1.5, stop 2 ATR beyond the last add -> qty = risk / (24.5 x ATR) (Codex's capital snapshot)."""
    g = dict(dca=dict(n=3, step_atr=1.0, scale=1.5, tp_atr=1.0, stop_atr=2.0))
    z = F.risk_qty(g, 10.0, 100.0, 2.0, 1)
    assert z['qty'] == pytest.approx(10.0 / (24.5 * 2.0)) and z['stop'] == pytest.approx(90.0)
    z = F.risk_qty(dict(stop_atr=2.0), 10.0, 100.0, 2.0, -1)
    assert z['qty'] == pytest.approx(2.5) and z['stop'] == pytest.approx(104.0)


# ---------------------------------------------------------------- 3) snapshots: versions, environments, staleness
def test_snapshot_states():
    assert F.snapshot_state(None, NOW)[0] == 'unavailable'
    assert F.snapshot_state(dict(schema='x'), NOW)[0] == 'invalid'
    bad = snap(); bad['symbols']['BTCUSDT']['step'] = 0
    assert F.snapshot_state(bad, NOW)[0] == 'invalid'
    assert F.snapshot_state(snap(verified=False), NOW)[0] == 'unverified'
    assert F.snapshot_state(snap(fetched_at='2026-07-01T00:00:00Z'), NOW)[0] == 'stale'
    assert F.snapshot_state(snap(fetched_at=None), NOW)[0] == 'stale'
    assert F.snapshot_state(snap(env='mainnet'), NOW, 'testnet')[0] == 'wrong_environment'
    assert F.snapshot_state(snap(), NOW, 'testnet')[0] == 'ok'
    assert F.snapshot_state(snap(fetched_at='2026-09-01T00:00:00Z'), NOW, max_age_days=30)[0] == 'ok'


def test_shipped_testnet_snapshot_is_a_verified_direct_fetch_and_mainnet_is_absent():
    """BT02 review P1: the placeholder is replaced by a direct public fetch of the testnet exchangeInfo, with provenance."""
    s = XR.load('testnet')
    assert s['schema'] == F.SCHEMA and s['environment'] == 'testnet' and s['verified'] is True
    assert s['provenance'] == 'direct_fetch' and s['source_url'] == 'https://testnet.binancefuture.com/fapi/v1/exchangeInfo'
    assert len(s['raw_sha256']) == 64 and int(s['raw_sha256'], 16) >= 0
    t = F._ts(s['fetched_at'])
    assert F.snapshot_state(s, t + 86400, 'testnet')[0] == 'ok'
    assert F.snapshot_state(s, t + 31 * 86400, 'testnet')[0] == 'stale'            # it expires: refresh with `fetch`
    assert F.snapshot_state(s, t + 86400, 'mainnet')[0] == 'wrong_environment'
    r = s['symbols']                                       # the values the Codex review fetched independently
    assert (r['BTCUSDT']['step'], r['BTCUSDT']['min_qty'], r['BTCUSDT']['min_notional']) == (0.0001, 0.0001, 50.0)
    assert r['ETHUSDT']['min_notional'] == 20.0 and r['LINKUSDT']['min_notional'] == 5.0 and r['SOLUSDT']['min_notional'] == 5.0
    assert r['DOGEUSDT']['step'] == r['1000PEPEUSDT']['step'] == 1.0
    assert XR.load('mainnet') is None or XR.load('mainnet')['environment'] == 'mainnet'


def test_file_import_never_self_certifies(tmp_path):
    """BT02 review P2: only a direct fetch is verified automatically; a file keeps its real capture time, never 'now'."""
    p = tmp_path / 'exchangeInfo.json'
    p.write_text(json.dumps(dict(info(TESTNET), serverTime=1_789_000_000_000)), encoding='utf-8')
    out = str(tmp_path / 'exchange_rules_testnet.json')
    s1, old = XR.build_file(str(p), 'testnet', out=out)
    assert old is None and s1['version'] == 1 and s1['verified'] is False and s1['provenance'] == 'file_import'
    assert s1['fetched_at'] == '2026-09-10T00:26:40Z' == s1['server_time']            # the file's own serverTime
    assert s1['symbols'] == F.rules_from_exchange_info(info(TESTNET))
    assert s1['raw_sha256'] == __import__('hashlib').sha256(p.read_bytes()).hexdigest()
    assert F.snapshot_state(XR.load('testnet', path=out), NOW, 'testnet')[0] == 'unverified'
    # a saved MAINNET answer imported as --env testnet with a fresh --fetched-at is still not green
    s2, _ = XR.build_file(str(p), 'testnet', out=out, source='https://fapi.binance.com/fapi/v1/exchangeInfo', fetched_at=ISO_NOW)
    assert s2['verified'] is False and F.snapshot_state(s2, NOW, 'testnet')[0] == 'unverified'
    # no capture time at all -> stale even when trusted; never stamped with the current time
    p.write_text(json.dumps(info(TESTNET)), encoding='utf-8')
    warned = []
    s3, _ = XR.build_file(str(p), 'testnet', out=out, trust=True, warn=warned.append)
    assert s3['fetched_at'] is None and F.snapshot_state(s3, NOW, 'testnet')[0] == 'stale'
    assert s3['verified'] is True and s3['provenance'] == 'file_import_trusted' and warned and 'VERIFIED' in warned[0]
    # an old file trusted by hand keeps its old time -> stale
    p.write_text(json.dumps(dict(info(TESTNET), serverTime=1_780_000_000_000)), encoding='utf-8')
    s4, _ = XR.build_file(str(p), 'testnet', out=out, trust=True)
    assert F.snapshot_state(s4, NOW, 'testnet')[0] == 'stale'
    # trusting a file whose --source is the other environment's URL is refused
    with pytest.raises(ValueError):
        XR.build_file(str(p), 'testnet', out=out, source='https://fapi.binance.com/fapi/v1/exchangeInfo', trust=True)
    # versions + diff
    changed = copy.deepcopy(TESTNET); changed['BTCUSDT']['min_notional'] = 100.0; changed['SOLUSDT']['step'] = 1.0
    p.write_text(json.dumps(info(changed)), encoding='utf-8')
    s5, old = XR.build_file(str(p), 'testnet', out=out, fetched_at=ISO_NOW)
    assert s5['version'] == old['version'] + 1
    assert F.diff_snapshots(old, s5)['changed'] == {'BTCUSDT': {'min_notional': (50.0, 100.0)}, 'SOLUSDT': {'step': (0.01, 1.0)}}
    with pytest.raises(ValueError): F.build_snapshot(info(TESTNET), 'paper', 'x')


def test_direct_fetch_is_verified_with_provenance(tmp_path):
    body = json.dumps(dict(info(TESTNET), serverTime=1_789_000_000_000)).encode('utf-8')
    class R:
        content = body
        def raise_for_status(self): pass
    urls = []
    s, _ = XR.fetch('testnet', out=str(tmp_path / 'x.json'), get=lambda u: urls.append(u) or R())
    assert urls == ['https://testnet.binancefuture.com/fapi/v1/exchangeInfo']
    assert s['verified'] is True and s['provenance'] == 'direct_fetch' and s['source_url'] == urls[0]
    assert s['raw_sha256'] == __import__('hashlib').sha256(body).hexdigest() and s['server_time'] == '2026-09-10T00:26:40Z'
    assert XR.env_of_url(urls[0]) == 'testnet' and XR.env_of_url('https://fapi.binance.com/x') == 'mainnet' and XR.env_of_url('x') is None


# ---------------------------------------------------------------- 4) backtest: skipped signals instead of synthetic fills
def _book(syms_px, n=60, sig_at=(10,), atr=1.0, candles=None):
    """Flat candles at each coin's price (candles: {sym: {index: (o, h, l, c)}} overrides), ATR forced to atr % of the
    price, one long signal per coin on each candle of sig_at (entry at the next open)."""
    t = pd.date_range('2024-01-01', periods=n, freq='4h'); raw = {}
    for s, p in syms_px.items():
        o, h, l, c = (np.full(n, float(p)) for _ in range(4))
        for k, v in ((candles or {}).get(s) or {}).items(): o[k], h[k], l[k], c[k] = v
        raw[s] = pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=1000.0))
    bk = B.Book(raw)
    z = np.zeros(n, bool)
    for s, p in syms_px.items():
        bk.arr[s]['atr'] = np.full(n, atr * p / 100)
        le = z.copy(); le[list(sig_at)] = True
        bk._sig[('ema_mom', s, 'None')] = dict(le=le, se=z.copy(), lx=z.copy(), sx=z.copy())
    return bk


def _run(bk, syms, risk=0.02, start=500.0, **kw):
    sl = [dict(key='ema_mom', share=1.0, risk=risk, max_pos=len(syms), sides='long', mgmt={'stop_atr': 2.0}, symbols=list(syms), id='MOM')]
    return B.run(bk, sl, start=start, warmup=5, maint_margin=0, fund_per_bar=0, **kw)


def test_backtest_skips_btc_eth_records_reason_and_trades_altcoin():
    """Risk 0.05% of 500 = 0.25 USDT over a 2-ATR stop (2% of price) -> 12.5 USDT notional per coin:
    BTC (50, and 0.001 BTC = 60 USDT) and ETH (20) are skipped, SOL (5) is placed."""
    px = dict(BTCUSDT=60000.0, ETHUSDT=2500.0, SOLUSDT=150.0)
    bk = _book(px)
    tr, cv = _run(bk, list(px), risk=0.0005, exchange_rules=snap())
    f = cv.attrs['feasibility']
    assert f['mode'] == 'rules' and f['attempts'] == 3 and f['executed'] == 1 and f['executable_pct'] == pytest.approx(33.3)
    assert f['by_symbol']['BTCUSDT'] == dict(attempts=1, skipped=1) and f['by_symbol']['SOLUSDT'] == dict(attempts=1, skipped=0)
    assert f['by_slot']['MOM'] == dict(attempts=3, skipped=2)
    sk = {x['sym']: x for x in f['skips']}
    assert set(sk) == {'BTCUSDT', 'ETHUSDT'} and sk['ETHUSDT']['reason'] == 'size below Binance minimum (raise capital or risk)'
    assert sk['ETHUSDT']['min_notional'] == 20.0 and sk['ETHUSDT']['notional'] < 20.0
    assert sk['BTCUSDT']['code'] == 'below_min_qty' and sk['BTCUSDT']['qty'] == 0.0
    # nothing was filled synthetically for the skipped coins: equity moved only by SOL's entry fee
    assert cv.iloc[-1] == pytest.approx(500.0 - 12.5 / 150.0 * 150.0 * (1 + B.SLIP) * B.FEE, rel=1e-3)
    assert len(tr) == 0


def test_backtest_step_rounding_scales_pnl_exactly():
    """Same book, rule step 1 vs no step: the position (and so every fee and the P&L of the stop-out) is floor(q)/q."""
    px = dict(DOGEUSDT=100.0)
    def book(): return _book(px, sig_at=(10,), atr=1.0, candles={'DOGEUSDT': {12: (100.0, 100.0, 95.0, 96.0)}})
    q_raw = 500 * 0.02 / (2.0 * 1.0)                         # 5.0 -> make it fractional with risk 0.0277
    a, _ = _run(book(), ['DOGEUSDT'], risk=0.0277, exchange_rules={'DOGEUSDT': dict(step=None, min_qty=0, min_notional=5.0)})
    b, cvb = _run(book(), ['DOGEUSDT'], risk=0.0277, exchange_rules={'DOGEUSDT': dict(step=1.0, min_qty=1.0, min_notional=5.0)})
    q = 500 * 0.0277 / 2.0
    assert len(a) == len(b) == 1 and a.why[0] == b.why[0] == 'stop'
    assert b.pnl[0] / a.pnl[0] == pytest.approx(math.floor(q) / q, rel=1e-9)
    assert cvb.attrs['feasibility']['executed'] == 1 and q_raw == 5.0


def test_backtest_rule_change_between_snapshots():
    px = dict(BTCUSDT=60000.0, SOLUSDT=150.0)
    old = snap(version=1); new = copy.deepcopy(old); new['version'] = 2; new['symbols']['SOLUSDT']['min_notional'] = 100.0
    _, c1 = _run(_book(px), list(px), risk=0.004, exchange_rules=old)     # 50 USDT notional each
    _, c2 = _run(_book(px), list(px), risk=0.004, exchange_rules=new)
    assert c1.attrs['feasibility']['by_symbol']['SOLUSDT']['skipped'] == 0
    assert c2.attrs['feasibility']['by_symbol']['SOLUSDT']['skipped'] == 1
    assert F.diff_snapshots(old, new)['changed'] == {'SOLUSDT': {'min_notional': (5.0, 100.0)}}


def test_backtest_legacy_default_unchanged_and_missing_symbol_unknown():
    px = dict(BTCUSDT=60000.0, SOLUSDT=150.0, NEWUSDT=3.0)
    _, c0 = _run(_book(px), list(px), risk=0.0016)                      # 40 USDT notional each, no rules: legacy floor
    f0 = c0.attrs['feasibility']
    assert f0['mode'] == 'legacy' and f0['by_symbol']['BTCUSDT']['skipped'] == 1 and f0['by_symbol']['SOLUSDT']['skipped'] == 0
    _, c1 = _run(_book(px), list(px), risk=0.0016, exchange_rules=snap())
    f1 = c1.attrs['feasibility']
    assert f1['unknown_symbols'] == ['NEWUSDT'] and f1['by_symbol']['NEWUSDT']['skipped'] == 0      # legacy floor (5) for it


def test_backtest_pyramid_add_below_minimum_is_skipped_not_filled():
    """Entry passes (0.3 USDT risk / 2% stop = 15 USDT >= 10) but the pyramid add (half size, ~7.6 USDT) is below a 10 USDT minimum."""
    px = dict(SOLUSDT=100.0)
    up = {k: (100.0 + 3 * (k - 11), 103.0 + 3 * (k - 11), 100.0 + 3 * (k - 11), 103.0 + 3 * (k - 11)) for k in range(12, 20)}
    bk = _book(px, sig_at=(10,), candles={'SOLUSDT': up})
    sl = [dict(key='ema_mom', share=1.0, risk=0.0003, max_pos=1, sides='long', id='P', symbols=['SOLUSDT'],
               mgmt={'stop_atr': 2.0, 'pyramid': {'n': 1, 'step_r': 0.5, 'frac': 0.5}})]
    rule = {'SOLUSDT': dict(step=0.001, min_qty=0.001, min_notional=10.0)}
    _, cv = B.run(bk, sl, start=1000.0, warmup=5, maint_margin=0, fund_per_bar=0, exchange_rules=rule)
    f = cv.attrs['feasibility']
    assert f['executed'] == 1 and f['add_skipped'] == {'below_min_notional': 1}
    _, cv2 = B.run(bk, sl, start=1000.0, warmup=5, maint_margin=0, fund_per_bar=0,
                   exchange_rules={'SOLUSDT': dict(step=0.001, min_qty=0.001, min_notional=5.0)})
    assert cv2.attrs['feasibility']['add_skipped'] == {}
    assert cv2.iloc[-1] != cv.iloc[-1]                       # the add really fired there (a larger position on the rally)


def _run_side(side, rule, mgmt, candles, risk=0.01, start=1000.0):
    """One SOL position (px 100, ATR 1, stop 2 ATR) long or short; candles for the short case are mirrored around 100."""
    if side == 'short': candles = {k: (200 - o, 200 - l, 200 - h, 200 - c) for k, (o, h, l, c) in candles.items()}
    bk = _book(dict(SOLUSDT=100.0), sig_at=(10,), atr=1.0, candles={'SOLUSDT': candles})
    if side == 'short':
        g = bk._sig[('ema_mom', 'SOLUSDT', 'None')]; g['se'], g['le'] = g['le'], g['se'].copy()
    sl = [dict(key='ema_mom', share=1.0, risk=risk, max_pos=1, sides=side, id='X', symbols=['SOLUSDT'], mgmt=mgmt)]
    return B.run(bk, sl, start=start, warmup=5, maint_margin=0, fund_per_bar=0, exchange_rules=rule)


TP1_THEN_STOP = {12: (100.0, 102.5, 100.0, 102.0), 13: (102.0, 102.0, 97.0, 97.0)}   # +1R (tp1 at 102), then the stop (98)
STEP = lambda st, mn=5.0: {'SOLUSDT': dict(step=st, min_qty=st, min_notional=mn)}


@pytest.mark.parametrize('side', ['long', 'short'])
def test_backtest_partial_exit_is_floored_to_the_step_like_the_engine(side):
    """BT02 review P1: qty 5, tp1 half = 2.5. Step 0.5 closes 2.5 (= the legacy run); step 1 closes floor(2.5) = 2 like
    engine._market_close, so 0.5 more rides to the stop: P&L differs by exactly 0.5 x (stop fill - tp1 fill) after costs."""
    m = {'stop_atr': 2.0, 'tp1_r': 1.0, 'tp1_frac': 0.5}
    leg, _ = _run_side(side, None, m, TP1_THEN_STOP)
    half, _ = _run_side(side, STEP(0.5), m, TP1_THEN_STOP)
    one, _ = _run_side(side, STEP(1.0), m, TP1_THEN_STOP)
    assert len(leg) == len(half) == len(one) == 1 and one.why[0] == 'stop' and one.i_out[0] == 13
    assert half.pnl[0] == pytest.approx(leg.pnl[0], abs=1e-9), 'an exact step multiple behaves exactly like before'
    k = (1 - B.SLIP) * (1 - B.FEE) if side == 'long' else (1 + B.SLIP) * (1 + B.FEE)
    assert one.pnl[0] - half.pnl[0] == pytest.approx(-0.5 * 4 * k, rel=1e-9)


@pytest.mark.parametrize('side', ['long', 'short'])
def test_backtest_partial_that_floors_to_zero_sends_nothing(side):
    """qty 1 (step 1): tp1 half = 0.5 floors to 0 -> no partial order (the engine's _market_close returns 0); the whole
    position rides to the stop, exactly as if tp1 had never been reached."""
    m = {'stop_atr': 2.0, 'tp1_r': 1.0, 'tp1_frac': 0.5}
    hit, _ = _run_side(side, STEP(1.0), m, TP1_THEN_STOP, risk=0.002)                    # 2 USDT / 2 = 1 SOL
    no_tp1 = {12: (100.0, 101.0, 100.0, 101.0), 13: (101.0, 101.0, 97.0, 97.0)}
    plain, _ = _run_side(side, STEP(1.0), m, no_tp1, risk=0.002)
    assert len(hit) == len(plain) == 1 and hit.why[0] == plain.why[0] == 'stop'
    assert hit.pnl[0] == pytest.approx(plain.pnl[0], abs=1e-9)
    leg, _ = _run_side(side, None, m, TP1_THEN_STOP, risk=0.002)                          # legacy: 0.5 closed at tp1
    assert leg.pnl[0] > hit.pnl[0]


@pytest.mark.parametrize('side', ['long', 'short'])
def test_backtest_ladder_closes_the_whole_lot_instead_of_leaving_dust(side):
    """Ladder level 1 closes half (2.5 of 5). With a 300 USDT minimum the 2.5 left (~255 USDT) would be dust -> the
    engine's rule (feasibility.leaves_dust) closes the whole lot there; with a 5 USDT minimum the rest rides on."""
    m = {'stop_atr': 2.0, 'tps': [[1.0, 0.5], [5.0, 0.5]]}
    dust, _ = _run_side(side, STEP(0.5, 300.0), m, TP1_THEN_STOP)
    keep, _ = _run_side(side, STEP(0.5, 5.0), m, TP1_THEN_STOP)
    assert len(dust) == 1 and dust.why[0] == 'tp_ladder' and dust.i_out[0] == 12
    assert len(keep) == 1 and keep.why[0] == 'stop' and keep.i_out[0] == 13
    leg, _ = _run_side(side, None, m, TP1_THEN_STOP)                                      # legacy: no dust rule, unchanged
    assert leg.pnl[0] == pytest.approx(keep.pnl[0], abs=1e-9)


@pytest.mark.parametrize('mode', ['tp1 half', pytest.param('ladder', marks=pytest.mark.slow)])
def test_partial_exits_match_the_engine_through_replay(mode):
    """Engine vs backtest on the same candles and the same coarse rules (step 1): every trade matches and the return gap is
    small. On the pre-fix backtest (raw fractional partials, no dust rule) tp1 was 1.07 % apart and the ladder matched
    only 1 of 2 trades (1.51 % apart)."""
    import engine as E
    from test_causality import synth, SYMS, T0
    from replay import run_replay
    sl = {'tp1 half': [E.sleeve('M', 'ema_mom', 1.0, .02, 3, 'core8', mgmt={'tp1_r': 1.0, 'tp1_frac': 0.5})],
          'ladder': [E.sleeve('L', 'ema_mom', 1.0, .02, 3, 'core8', mgmt={'tps': [[0.8, 0.4], [1.6, 0.4]]})]}[mode]
    rules = {s: dict(step=1.0, min_qty=1.0, min_notional=5.0, tick=0.001) for s in SYMS}
    m = run_replay(synth(n=520, seed=6), copy.deepcopy(sl), T0, steps=6, exchange_rules=rules)['metrics']
    assert m['trades_engine'] >= 1 and m['trades_engine'] == m['trades_bt'] == m['matched'], m
    assert m['mismatches'] == 0 and m['ret_gap'] <= 0.25, m


def test_leaves_dust_is_the_engine_rule():
    r = dict(step=0.01, min_qty=0.1, min_notional=5.0)
    assert F.leaves_dust(0.05, 100.0, r) and F.leaves_dust(0.2, 10.0, r) and not F.leaves_dust(0.2, 100.0, r)
    assert not F.leaves_dust(0.0, 100.0, r) and not F.leaves_dust(0.05, 100.0, None)
    src = open(os.path.join(ROOT, 'engine.py'), encoding='utf-8').read()
    assert 'if F.leaves_dust(rest, m, rr): q = lot[\'qty\']' in src, 'the engine ladder uses the shared predicate'


# ---------------------------------------------------------------- 5) preflight: executable %, minimum capital, unknown
def _slots(risk=0.02, share=1.0, key='dca_dip', mgmt=None, syms=('BTCUSDT', 'SOLUSDT'), tf='4h'):
    import strategies as S
    return [dict(id='DCA', key=key, share=share, risk=risk, tf=tf, symbols=list(syms), mgmt=S.merge_mgmt(key, mgmt))]


MARKET = {('BTCUSDT', '4h'): dict(px=60000.0, atr=600.0), ('SOLUSDT', '4h'): dict(px=150.0, atr=1.5),
          ('ETHUSDT', '4h'): dict(px=2500.0, atr=25.0)}


def test_preflight_partial_with_minimum_capital_and_risk():
    r = F.preflight(_slots(), 500.0, MARKET, TESTNET, 'ok')
    # DCA first order = 500 * 2% / (24.5 * 1% px) -> 40.8 USDT notional: BTC needs 0.001 (60 USDT); SOL passes
    assert r['status'] == 'partial' and r['executable_pct'] == 50.0 and r['pairs'] == 2
    u = r['undersized'][0]
    assert u['symbol'] == 'BTCUSDT' and u['code'] == 'below_min_qty'
    assert u['min_capital'] == pytest.approx(500 * 60 / (10 / 24.5 / 600 * 60000), rel=2e-3)      # ~735 USDT
    # at the estimated minimum capital the very same check passes, just below it fails
    o = F.slot_order_qtys(_slots()[0], u['min_capital'] * 1.001, 60000.0, 600.0)
    assert F.size_check(o['qty_raw'], o['qty'], 60000.0, TESTNET['BTCUSDT'])['ok']
    o = F.slot_order_qtys(_slots()[0], u['min_capital'] * 0.99, 60000.0, 600.0)
    assert not F.size_check(o['qty_raw'], o['qty'], 60000.0, TESTNET['BTCUSDT'])['ok']
    assert u['min_risk_pct'] == pytest.approx(2.0 * u['min_capital'] / 500, rel=1e-3)
    assert 'below the Binance minimum' in r['warning'] and 'about' in r['warning']


def test_preflight_infeasible_warning():
    r = F.preflight(_slots(risk=0.001), 500.0, MARKET, TESTNET, 'ok')
    assert r['status'] == 'infeasible' and r['executable_pct'] == 0.0 and 'cannot place any trade' in r['warning']


def test_preflight_never_green_on_unknown_rules():
    good = F.preflight(_slots(risk=0.05), 5000.0, MARKET, TESTNET, 'ok')
    assert good['status'] == 'ok' and good['executable_pct'] == 100.0
    for st in ('unverified', 'stale', 'unavailable', 'invalid', 'wrong_environment'):
        r = F.preflight(_slots(risk=0.05), 5000.0, MARKET, TESTNET, st, 'x')
        assert r['status'] == 'unknown' and r['estimate'] == 'ok' and 'not a pass' in r['warning'], st
    r = F.preflight(_slots(risk=0.05, syms=('BTCUSDT', 'NEWUSDT')), 5000.0, MARKET, TESTNET, 'ok')
    assert r['status'] == 'unknown' and r['unknown'][0]['symbol'] == 'NEWUSDT'
    r = F.preflight(_slots(risk=0.05), 5000.0, {}, TESTNET, 'ok')                 # no prices at all
    assert r['status'] == 'unknown' and r['pairs'] == 0


def test_preflight_flags_undersized_pyramid_add():
    """BT02 review P1 (Codex repro): SOL, $500, 0.03 % risk, pyramid frac 0.5 -> the entry passes but the add cannot:
    never 'ok' / 100 %, and the minimum capital is set by the add."""
    rule = dict(SOLUSDT=dict(step=0.01, min_qty=0.01, min_notional=5.0))
    sl = lambda risk, n=1: _slots(key='ema_mom', mgmt={'pyramid': {'n': n, 'step_r': 1.5, 'frac': 0.5}}, syms=('SOLUSDT',), risk=risk)
    r = F.preflight(sl(0.0006), 500.0, MARKET, rule, 'ok')
    assert r['status'] == 'ok' and r['plan_executable_pct'] == r['entry_executable_pct'] == 100.0 and r['add_undersized'] == []
    r = F.preflight(sl(0.0003), 500.0, MARKET, rule, 'ok')
    assert r['status'] == 'partial' and r['estimate'] == 'partial'
    assert r['entry_executable_pct'] == 100.0 and r['plan_executable_pct'] == 0.0 and r['executable_pct'] == 0.0
    a = r['add_undersized']
    assert len(a) == 1 and a[0]['leg'] == 'pyramid add 1' and a[0]['code'] == 'below_min_notional' and a[0]['notional'] < 5
    assert r['min_capital_all'] > 500 and r['min_capital_binding']['leg'] == 'pyramid add 1'
    assert 'later order' in r['warning'] and 'pyramid add 1' in r['warning']
    # at the stated minimum capital every planned order passes, just below it the add fails
    legs = F.check_legs(F.slot_order_legs(sl(0.0003)[0], r['min_capital_all'] * 1.001, 150.0, 1.5, rule=rule['SOLUSDT']), rule['SOLUSDT'])
    assert all(x['ok'] for x in legs)
    legs = F.check_legs(F.slot_order_legs(sl(0.0003)[0], r['min_capital_all'] * 0.99, 150.0, 1.5, rule=rule['SOLUSDT']), rule['SOLUSDT'])
    assert not all(x['ok'] for x in legs)
    # n = 2 adds -> two legs, both checked
    assert [x['leg'] for x in F.slot_order_legs(sl(0.0003, 2)[0], 500.0, 150.0, 1.5)] == ['entry', 'pyramid_add', 'pyramid_add']


def test_preflight_dca_safety_order_sets_the_minimum_capital():
    """A DCA plan whose deep safety order (low price, scale 1) is the smallest notional: entry passes, safety order 3
    does not -> partial, and min_capital_all is set by 'safety order 3'."""
    mg = {'dca': {'n': 3, 'step_atr': 20.0, 'scale': 1.0, 'stop_atr': 2.0, 'tp_atr': 2.0}}
    rule = dict(SOLUSDT=dict(step=0.01, min_qty=0.01, min_notional=5.0))
    r = F.preflight(_slots(key='dca_dip', mgmt=mg, syms=('SOLUSDT',), risk=0.027), 500.0, MARKET, rule, 'ok')
    legs = F.slot_order_legs(_slots(key='dca_dip', mgmt=mg, syms=('SOLUSDT',), risk=0.027)[0], 500.0, 150.0, 1.5)
    assert [x['leg'] for x in legs] == ['entry', 'safety_order', 'safety_order', 'safety_order']
    assert legs[3]['px'] == pytest.approx(150.0 - 3 * 20 * 1.5)                         # DCA level 3 (engine levels)
    assert r['entry_executable_pct'] == 100.0 and r['plan_executable_pct'] == 0.0 and r['status'] == 'partial'
    assert [a['leg'] for a in r['add_undersized']] == ['safety order 3']
    assert r['min_capital_binding']['leg'] == 'safety order 3' and r['min_capital_all'] > 500


def test_panel_never_shows_tradable_when_a_later_order_fails():
    """The green tag is mapped only from status 'ok', and preflight() never returns 'ok' with an undersized add."""
    rule = dict(SOLUSDT=dict(step=0.01, min_qty=0.01, min_notional=5.0))
    for risk in (0.0001, 0.0002, 0.0003, 0.0004, 0.0006, 0.001):
        r = F.preflight(_slots(key='ema_mom', mgmt={'pyramid': {'n': 2, 'step_r': 1.5, 'frac': 0.5}}, syms=('SOLUSDT',), risk=risk),
                        500.0, MARKET, rule, 'ok')
        assert not (r['status'] == 'ok' and r['add_undersized']), risk
    html = open(os.path.join(ROOT, 'panel.html'), encoding='utf-8').read()
    assert "ok:['t-long','Tradable at your capital']" in html


def test_feasibility_module_is_pure():
    src = open(os.path.join(ROOT, 'feasibility.py'), encoding='utf-8').read()
    for bad in ('open(', 'requests', 'time.time', 'os.', 'socket', 'urllib', 'datetime.now'):
        assert bad not in src, bad


def test_replay_serves_the_same_rules_to_engine_and_backtest():
    """replay.run_replay(exchange_rules=...) hands the simulated exchange exactly these filters (engine side) and the
    same rules to the backtester."""
    rules = {'SOLUSDT': dict(step=0.01, min_qty=0.01, min_notional=5.0, tick=0.001)}
    got = F.rules_from_exchange_info(F.exchange_info_from_rules(rules))
    assert got == rules


# ---------------------------------------------------------------- 6) app: backtest result + preflight use the shared functions
def _csv_candles(sym, tf, days):
    d = pd.read_csv(os.path.join(ROOT, 'data' if tf == '4h' else 'data1h', f'{sym}_{tf}.csv'), parse_dates=['t'])
    return d.tail(int(days * 86400 / 14400) + 260).reset_index(drop=True)


def _app_bt(monkeypatch, A, E, xsnap, xstate, rules='on'):
    monkeypatch.setattr(A, 'exchange_rules_now', lambda e=None: (xsnap, xstate, f'test {xstate}'))
    jid = f'20260101-000000-bt02-{xstate}-{rules}'
    A.JOBS[jid] = dict(status='queued')
    A.run_backtest_job(jid, dict(name='bt02', sleeves=copy.deepcopy(E.PRESETS['calm']['sleeves']), days=120, tf='4h', start=500,
                                 universe=list(E.CORE8), exchange_rules=rules))
    j = A.JOBS.pop(jid)
    assert j['status'] == 'done', j
    return j['result']


def test_app_backtest_applies_only_verified_fresh_rules_of_this_environment(monkeypatch):
    """BT02 review P1: unverified / stale / invalid / wrong-environment / missing rules must not change the canonical
    result (trades, curve, stats) - it equals the run with exchange rules off, and says the rules were not applied."""
    import app as A, engine as E
    monkeypatch.setattr(A, 'APP', None)
    monkeypatch.setattr(A, 'get_candles', _csv_candles)
    real = XR.load('testnet')
    off = _app_bt(monkeypatch, A, E, None, 'off', rules='off')
    ok = _app_bt(monkeypatch, A, E, real, 'ok')
    f = ok['feasibility']
    assert f['rules_applied'] is True and f['promotable'] is True and f['groups']['4h']['mode'] == 'rules'
    assert f['rules_version'] == real['version'] and f['rules_fetched_at'] == real['fetched_at']
    assert f['by_symbol'].get('BTCUSDT', {}).get('skipped', 0) >= 1          # calm's small slots cannot buy BTC at $500
    assert ok['curve'] != off['curve']
    for st in ('unverified', 'stale', 'invalid', 'wrong_environment', 'unavailable'):
        r = _app_bt(monkeypatch, A, E, None if st == 'unavailable' else real, st)
        g = r['feasibility']
        assert g['rules_applied'] is False and g['promotable'] is False and g['rules_state'] == st, st
        assert g['groups']['4h']['mode'] == 'legacy', st
        assert r['curve'] == off['curve'] and r['stats'] == off['stats'] and r['by_symbol'] == off['by_symbol'], st


def test_app_preflight_calls_the_shared_function(monkeypatch):
    import app as A, test_safety as TS
    e, _ = TS.mk_engine()
    ap = A.App.__new__(A.App); ap.engine = e
    calls = []
    real = F.preflight
    monkeypatch.setattr(F, 'preflight', lambda *a, **k: calls.append(a) or real(*a, **k))
    r = ap.preflight()
    assert len(calls) == 2 * len(r['presets']) and '__current' in r['presets'] and 'calm' in r['presets']   # headline + planning
    assert all('planning' in v for v in r['presets'].values()) and 'latest closed candle' in r['market_basis']
    assert r['rules_state'] == 'ok' and 'live connection' in r['rules_detail'] and r['environment'] == 'testnet'
    assert all(v['status'] in ('ok', 'partial', 'infeasible', 'unknown') for v in r['presets'].values())
    e.rules = {}                                            # not connected -> an unverified file snapshot -> never green
    monkeypatch.setattr(XR, 'load', lambda env, root=None, path=None: snap(verified=False))
    r = ap.preflight()
    assert r['rules_state'] == 'unverified' and all(v['status'] == 'unknown' for v in r['presets'].values())


def test_backtest_accounting_separates_risk_rule_refusals():
    px = dict(BTCUSDT=60000.0, ETHUSDT=2500.0, SOLUSDT=150.0)
    _, cv = _run(_book(px), list(px), risk=0.01, exchange_rules=snap(),
                 risk_rules={'open_risk_cap': {'mode': 'enforce', 'pct': 1.5}})       # room for one 1% entry only
    f = cv.attrs['feasibility']
    assert f['attempts'] == 3 and f['executed'] == 1 and f['rule_blocked'] == 2 and sum(f['skipped'].values()) == 0
    assert f['executable_pct'] == 100.0                     # a risk-rule refusal is not an exchange-filter skip


def test_preflight_market_uses_the_latest_closed_candle_atr_like_the_engine():
    """BT02 review P2: the headline 'would execute now' uses the last closed candle's ATR (the engine's signal atr); the
    180-candle median is only a separate planning estimate, and the basis / time is reported."""
    import app as A, types
    n = 200
    df = pd.DataFrame(dict(t=pd.date_range('2026-01-01', periods=n, freq='4h'), c=np.full(n, 150.0), atr=np.full(n, 1.5)))
    df.loc[n - 1, 'atr'] = 15.0                                 # volatility just exploded: latest ATR 10x the median
    e = types.SimpleNamespace(_kc={('SOLUSDT', '4h'): (df,)})
    m, asof = A.preflight_market(e, {('SOLUSDT', '4h')})
    x = m[('SOLUSDT', '4h')]
    assert x['atr'] == 15.0 and x['atr_median'] == pytest.approx(1.5) and x['px'] == 150.0
    assert x['basis'] == 'latest closed candle' and x['t'] == str(df.t.iloc[-1])[:16] and 'engine' in asof['SOLUSDT 4h']
    # the status follows the latest ATR: 10x the ATR -> 1/10 the size -> below the minimum now, fine on the median
    rule = dict(SOLUSDT=dict(step=0.01, min_qty=0.01, min_notional=5.0))
    sl = _slots(key='ema_mom', syms=('SOLUSDT',), risk=0.0006)
    now = F.preflight(sl, 500.0, m, rule, 'ok')
    typ = F.preflight(sl, 500.0, {k: dict(v, atr=v['atr_median']) for k, v in m.items()}, rule, 'ok')
    assert now['status'] == 'infeasible' and typ['status'] == 'ok'
