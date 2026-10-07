"""BT02 Codex review fixes for the preset preflight (feasibility.preflight, app.preflight_market, /api/preflight).

P1-B: a coin/slot pair is fully tradable only when the first order AND every predeclared exposure-increasing order of its
      plan (DCA safety orders, pyramid adds) pass the exchange minimums. status 'ok' (the panel's green "Tradable at your
      capital") requires the whole plan; min_capital_all covers every planned leg and names the leg that sets it.
P2-B: the primary "would execute now" status sizes with the ATR / close of the LATEST CLOSED candle (as the engine's
      open_lot does), the still-forming candle excluded with the engine's own rule; the 180-candle median ATR is only a
      separate planning estimate. The response carries atr_basis / atr_ts / price_ts.
"""
import os
import pandas as pd, pytest
import feasibility as F
import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOL = dict(SOLUSDT=dict(step=0.01, min_qty=0.01, min_notional=5.0))
MKT = {('SOLUSDT', '4h'): dict(px=150.0, atr=1.5)}
PY1 = {'pyramid': {'n': 1, 'step_r': 1.5, 'frac': 0.5}}


def slot(key='ema_mom', mgmt=None, risk=0.0003, syms=('SOLUSDT',), sid='MOM', share=1.0, tf='4h', sides=None):
    d = dict(id=sid, key=key, share=share, risk=risk, tf=tf, symbols=list(syms), mgmt=S.merge_mgmt(key, mgmt))
    if sides: d['sides'] = sides
    return d


def plan_ok_at(sl, capital, px, atr, rule):
    d, lds, _ = F._plan_check(sl, capital, px, atr, rule, F.planned_legs(sl, px, atr), 10.0)
    return bool(d['ok']) and all(x['ok'] for x in lds)


# ---------------------------------------------------------------- P1-B: the exact Codex repro
def test_sol_repro_undersized_pyramid_add_is_not_tradable():
    """SOL, $500, entry risk 0.03 %, pyramid fraction 0.5, 5 USDT minimum: the first order fits (6 USDT), the pyramid
    add does not (0.02 SOL ~ 2.9 USDT). Before the fix: status ok, executable 100 %, min_capital_all 500."""
    r = F.preflight([slot(mgmt=PY1)], 500.0, MKT, SOL, 'ok')
    assert r['status'] == 'partial' and r['estimate'] == 'partial'
    assert r['entry_executable_pct'] == 100.0 and r['plan_executable_pct'] == 0.0 and r['executable_pct'] == 0.0
    assert r['ok'] == 1 and r['plan_ok'] == 0 and r['undersized'] == []
    assert len(r['add_undersized']) == 1
    a = r['add_undersized'][0]
    assert a['symbol'] == 'SOLUSDT' and a['slot'] == 'MOM' and [x['leg'] for x in a['legs']] == ['pyramid add 1']
    leg = a['legs'][0]
    assert leg['ok'] is False and leg['code'] == 'below_min_notional' and leg['qty'] == pytest.approx(0.02)
    assert leg['notional'] < 5.0
    # min capital covers the add: q0 must reach 0.08 SOL (0.04 SOL add >= 5 USDT at the add price) -> 1,000 USDT
    assert r['min_capital_all'] == 1000.0 and r['min_capital_all'] > 500
    assert r['min_capital_leg'] == 'pyramid add 1' and r['min_capital_pair'] == 'MOM SOLUSDT 4h'
    assert a['min_capital'] == 1000.0 and a['min_risk_pct'] == pytest.approx(0.06)
    row_entry_cap = F.preflight([slot()], 500.0, MKT, SOL, 'ok')['min_capital_all']          # same slot without the add
    assert row_entry_cap < r['min_capital_all']
    assert 'later orders' in r['warning'] and 'pyramid add 1' in r['warning'] and '1,000' in r['warning']
    # the estimate is real: the whole plan passes at the minimum capital and fails just below it
    sl = slot(mgmt=PY1)
    assert plan_ok_at(sl, 1000.0, 150.0, 1.5, SOL['SOLUSDT'])
    assert not plan_ok_at(sl, 999.0, 150.0, 1.5, SOL['SOLUSDT'])
    # and at that capital the preflight itself is green
    assert F.preflight([sl], 1000.0, MKT, SOL, 'ok')['status'] == 'ok'


def test_adds_pass_status_ok():
    r = F.preflight([slot(mgmt=PY1, risk=0.0006)], 500.0, MKT, SOL, 'ok')
    assert r['status'] == 'ok' and r['entry_executable_pct'] == 100.0 and r['plan_executable_pct'] == 100.0
    assert r['add_undersized'] == [] and r['undersized'] == []
    assert r['min_capital_all'] <= 500 and r['min_capital_leg'] == 'pyramid add 1'      # the smallest leg binds
    row = F.preflight([slot(mgmt=PY1, risk=0.0006)], 500.0, MKT, SOL, 'ok')
    assert 'later orders' not in row['warning'] and 'Estimated capital' not in row['warning']


def test_unknown_rules_never_green_even_when_plan_passes():
    for st in ('unverified', 'stale', 'unavailable'):
        r = F.preflight([slot(mgmt=PY1, risk=0.0006)], 500.0, MKT, SOL, st, 'x')
        assert r['status'] == 'unknown' and r['estimate'] == 'ok'
        r = F.preflight([slot(mgmt=PY1)], 500.0, MKT, SOL, st, 'x')
        assert r['status'] == 'unknown' and r['estimate'] == 'partial'


def test_every_pyramid_add_is_checked_and_add_price_side():
    """breakout_pyramid plans 2 adds; both are listed. A long-only slot's add fills ABOVE the entry (bigger notional);
    a slot that can short is checked at the lower (short-side) price - the harder case is never skipped."""
    sl = slot(key='breakout_pyramid', sid='BRK', risk=0.0003)
    legs = F.planned_legs(sl, 150.0, 1.5)
    assert [x['leg'] for x in legs] == ['pyramid add 1', 'pyramid add 2']
    R = 2.0 * 1.5
    assert legs[0]['px'] == pytest.approx(150 - R) and legs[1]['px'] == pytest.approx(150 - 2 * R)      # sides unknown -> both
    long_legs = F.planned_legs(dict(sl, sides='long'), 150.0, 1.5)
    assert long_legs[0]['px'] == pytest.approx(150 + R) and long_legs[1]['px'] == pytest.approx(150 + 2 * R)
    short_legs = F.planned_legs(dict(sl, sides='short'), 150.0, 1.5)
    assert short_legs[1]['px'] == pytest.approx(150 - 2 * R)
    # q0 = 0.05 SOL, add 0.025 -> floor 0.02: 3 USDT, fails on both legs
    r = F.preflight([sl], 500.0, MKT, SOL, 'ok')
    assert r['status'] == 'partial' and [x['leg'] for x in r['add_undersized'][0]['legs']] == ['pyramid add 1', 'pyramid add 2']
    # wider ATR (R = 12): add 1 at 138 needs 0.037 SOL (q0 0.074), add 2 at 126 needs 0.040 (q0 0.080) -> add 2 sets the
    # minimum capital (q0 per USDT = 0.15 / 24 / 500 -> 2,960 vs 3,200 USDT)
    fine = dict(SOLUSDT=dict(step=0.001, min_qty=0.001, min_notional=5.0))
    wide = {('SOLUSDT', '4h'): dict(px=150.0, atr=6.0)}
    r = F.preflight([sl], 500.0, wide, fine, 'ok')
    row = r['undersized'][0]                                  # the first order is too small here too (0.012 SOL = 1.8 USDT; needs 0.034)
    caps = {x['leg']: x['min_capital'] for x in row['legs']}
    assert caps == {'pyramid add 1': 2960.0, 'pyramid add 2': 3200.0} and row['entry_min_capital'] == 1360.0
    assert r['min_capital_leg'] == 'pyramid add 2' and r['min_capital_all'] == 3200.0
    assert plan_ok_at(sl, 3200.0, 150.0, 6.0, fine['SOLUSDT'])
    assert not plan_ok_at(sl, 3199.0, 150.0, 6.0, fine['SOLUSDT'])


def test_dca_safety_order_can_set_the_minimum_and_n0_plans_none():
    """Coarse step + lower DCA level price: the first order (1 unit x 5 = 5 USDT) fits, safety order 1 (1.5 -> floor 1
    unit at 4 USDT) does not, so the full plan is not tradable and safety order 1 sets the minimum capital."""
    rule = dict(COARSEUSDT=dict(step=1.0, min_qty=1.0, min_notional=5.0))
    mkt = {('COARSEUSDT', '4h'): dict(px=5.0, atr=1.0)}
    # DCA first order = risk / (24.5 ATR): pick risk so q0 = 1.2 units -> floor 1
    sl = slot(key='dca_dip', sid='DCA', risk=1.2 * 24.5 / 500, syms=('COARSEUSDT',))
    legs = F.planned_legs(sl, 5.0, 1.0)
    assert [x['leg'] for x in legs] == ['DCA safety order 1', 'DCA safety order 2', 'DCA safety order 3']
    assert legs[0]['px'] == pytest.approx(4.0) and legs[0]['mult'] == pytest.approx(1.5)
    r = F.preflight([sl], 500.0, mkt, rule, 'ok')
    assert r['entry_executable_pct'] == 100.0 and r['status'] == 'partial'
    bad = {x['leg']: x for x in r['add_undersized'][0]['legs']}
    assert list(bad) == ['DCA safety order 1'] and bad['DCA safety order 1']['code'] == 'below_min_notional'
    assert bad['DCA safety order 1']['qty'] == 1.0 and bad['DCA safety order 1']['notional'] == pytest.approx(4.0)
    assert r['min_capital_leg'] == 'DCA safety order 1' and r['min_capital_all'] > 500
    assert plan_ok_at(sl, r['min_capital_all'], 5.0, 1.0, rule['COARSEUSDT'])           # rounded up: always enough
    assert not plan_ok_at(sl, r['min_capital_all'] - 1, 5.0, 1.0, rule['COARSEUSDT'])
    # a DCA block with n = 0 plans no safety orders: only what the preset actually plans is checked
    sl0 = slot(key='dca_dip', sid='DCA', risk=1.2 * 24.5 / 500, syms=('COARSEUSDT',), mgmt={'dca': {'n': 0}})
    assert F.planned_legs(sl0, 5.0, 1.0) == []


def test_min_capital_all_attribution_across_pairs():
    rules = dict(SOL, ETHUSDT=dict(step=0.001, min_qty=0.001, min_notional=20.0))
    mkt = {**MKT, ('ETHUSDT', '4h'): dict(px=2500.0, atr=25.0)}
    slots = [slot(mgmt=PY1, risk=0.0003, sid='MOM'), slot(key='ema_mom', sid='ST', risk=0.004, syms=('ETHUSDT',))]
    r = F.preflight(slots, 500.0, mkt, rules, 'ok')
    rows = {(x['slot'], x['symbol']): x for x in r['add_undersized'] + r['undersized']}
    worst = max(rows.values(), key=lambda x: x['min_capital'])
    assert r['min_capital_all'] == worst['min_capital']
    assert r['min_capital_pair'] == f"{worst['slot']} {worst['symbol']} 4h" and r['min_capital_leg'] == worst['min_capital_leg']
    assert r['min_capital_pair'] == 'MOM SOLUSDT 4h' and r['min_capital_leg'] == 'pyramid add 1'


def test_per_leg_detail_kept_in_rows():
    r = F.preflight([slot(mgmt=PY1)], 500.0, MKT, SOL, 'ok')
    a = r['add_undersized'][0]
    assert a['reason'].startswith('pyramid add 1') and 'Binance minimum' in a['reason']
    assert set(a['legs'][0]) >= {'leg', 'ok', 'code', 'qty', 'px', 'notional', 'min_capital'}


# ---------------------------------------------------------------- P2-B: latest closed candle drives the status
def _frame(n, t_end, step_h, atr_last, atr_typ, px=2500.0):
    t = pd.date_range(end=t_end, periods=n, freq=f'{step_h}h')
    atr = [atr_typ] * (n - 1) + [atr_last]
    return pd.DataFrame(dict(t=t, c=[px] * n, atr=atr))


class _Eng:
    def __init__(self, kc): self._kc = kc


def test_closed_only_matches_engine_rule():
    import app as A
    now = pd.Timestamp('2026-10-05 12:00:00').timestamp()
    df = pd.DataFrame(dict(t=pd.to_datetime(['2026-10-05 04:00:00', '2026-10-05 08:00:00', '2026-10-05 08:00:01']), c=1.0, atr=1.0))
    got = A._closed_only(df, '4h', now)
    # engine.candles keeps a kline when its close time (open + step - 1 ms) < now: 08:00 closes 11:59:59.999 -> kept;
    # a candle opening one second later is still forming -> dropped
    assert list(got.t.astype(str)) == ['2026-10-05 04:00:00', '2026-10-05 08:00:00']
    step_ms = 14400 * 1000; now_ms = now * 1000
    for t in df.t:
        open_ms = t.timestamp() * 1000
        assert ((open_ms + step_ms - 1) < now_ms) == (t in set(got.t))


def test_preflight_market_latest_closed_vs_median_and_forming_candle_excluded():
    import app as A
    now = pd.Timestamp('2026-10-05 12:00:00').timestamp()
    df = _frame(200, '2026-10-05 08:00:00', 4, atr_last=250.0, atr_typ=25.0)
    forming = pd.DataFrame(dict(t=[pd.Timestamp('2026-10-05 12:00:00')], c=[9999.0], atr=[9999.0]))
    e = _Eng({('ETHUSDT', '4h'): (pd.concat([df, forming], ignore_index=True), 0)})
    m, asof = A.preflight_market(e, {('ETHUSDT', '4h')}, now=now)
    x = m[('ETHUSDT', '4h')]
    assert x['px'] == 2500.0 and x['atr'] == 250.0                       # latest closed candle, not the forming one
    assert x['atr_median'] == pytest.approx(25.0) and x['atr_basis'] == 'latest_closed'
    assert x['atr_ts'] == '2026-10-05 08:00' and x['price_ts'] == '2026-10-05 08:00' and x['src'] == 'engine'
    assert asof['ETHUSDT 4h'].startswith('2026-10-05 08:00')
    # divergence: at 0.05 % risk the latest (10 % ATR) sizing is below the minimum, the median (1 %) sizing is not
    rules = dict(ETHUSDT=dict(step=0.001, min_qty=0.001, min_notional=5.0))
    sl = [slot(sid='ST', risk=0.0005, syms=('ETHUSDT',))]
    now_r = F.preflight(sl, 500.0, m, rules, 'ok')
    med_r = F.preflight(sl, 500.0, {k: dict(v, atr=v['atr_median']) for k, v in m.items()}, rules, 'ok')
    assert now_r['status'] == 'infeasible' and med_r['status'] == 'ok'
    assert now_r['undersized'][0]['atr_basis'] == 'latest_closed' and now_r['undersized'][0]['atr_ts'] == '2026-10-05 08:00'


def test_shipped_file_last_row_dropped():
    """A shipped candle file has no close-time column: its last row may have been forming when saved, so it is not used."""
    import app as A
    m, _ = A.preflight_market(_Eng({}), {('SOLUSDT', '4h')}, now=pd.Timestamp('2026-10-07').timestamp())
    raw = pd.read_csv(os.path.join(ROOT, 'data', 'SOLUSDT_4h.csv'), parse_dates=['t'], encoding='utf-8')
    x = m[('SOLUSDT', '4h')]
    assert x['src'] == 'shipped file' and x['price_ts'] == str(raw.t.iloc[-2])[:16] and x['px'] == float(raw.c.iloc[-2])


def test_app_preflight_status_uses_latest_closed_and_reports_basis(monkeypatch):
    import app as A, test_safety as TS
    e, _ = TS.mk_engine()
    now = pd.Timestamp('2026-10-05 12:00:00').timestamp()
    e._kc = {('ETHUSDT', '4h'): (_frame(200, '2026-10-05 08:00:00', 4, atr_last=250.0, atr_typ=25.0), 0)}
    e.S['SLEEVES'] = [dict(id='ST', key='ema_mom', share=1.0, risk=0.0005, tf='4h', symbols=['ETHUSDT'], sides='long', mgmt={})]
    real = A.preflight_market
    monkeypatch.setattr(A, 'preflight_market', lambda eng, keys: real(eng, keys, now=now))
    ap = A.App.__new__(A.App); ap.engine = e
    r = ap.preflight()
    assert r['rules_state'] == 'ok' and r['atr_basis'] == 'latest_closed' and r['price_basis'] == 'latest_closed_close'
    assert r['atr_ts']['newest'] == '2026-10-05 08:00' and r['price_ts']['newest'] == '2026-10-05 08:00'
    cur = r['presets']['__current']
    assert cur['atr_basis'] == 'latest_closed'
    assert cur['status'] == 'infeasible'                                  # what the engine would do now
    assert cur['planning_median_atr']['status'] == 'ok'                  # a separate estimate, not the status


def test_panel_green_only_for_full_plan():
    src = open(os.path.join(ROOT, 'panel.html'), encoding='utf-8').read()
    i = src.index('function pfBadge(k)'); body = src[i:src.index('function renderPresets()', i)]
    assert "ok:['t-long','Tradable at your capital']" in body
    tagmap = body[body.index('const tag={'):body.index('}[r.status]')]
    assert tagmap.count('t-long') == 1 and "partial:['t-warn'" in tagmap      # green for the full-plan 'ok' only
    assert 'later orders too small' in body and 'add_undersized' in body
    assert 'latest closed candle' in body and 'atr_ts' in body and 'planning_median_atr' in body
    assert 'full order plan' in body
