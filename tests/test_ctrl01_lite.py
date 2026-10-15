"""CTRL-01-LITE: global Direction Bias + Risk controls for NEW automatic entries only (Codex 6177020197, Cowork attack
list 6177156165). Synthetic only: the test_safety fake exchange, no network, no market-data store, no credentials.

Covers: mapping + monotonic risk, Neutral + Maximum Test == today's sizing, entries are a subset of the Neutral baseline
(never invented, never flipped), x0 disables and never inverts, the scaled size is never rounded up to the exchange minimum,
stops are never widened and always present, caps hold for bias x risk, open / pending / partially filled entries are
byte-identical after a control change, preview == engine, logging only when the control had an effect, persistence +
default migration, manual / 'Take now' not covered."""
import ast, copy, json, logging, os, random
import pandas as pd, pytest
import test_safety as TS
import engine as E
import feasibility as F
import trade_audit as TA
import binance_client as BC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMBOS = [(b, r) for b in E.CTRL_BIAS for r in E.CTRL_RISK]
LOT_FIELDS = ('symbol', 'side', 'qty', 'stop', 'risk_usd', 'sleeve')


def mk(**S):
    e, tmp = TS.mk_engine(**S)
    return e, tmp


def set_ctrl(e, b, r):
    e.S['CTRL_BIAS'], e.S['CTRL_RISK'] = b, r


def reset(e):
    e.state['lots'].clear(); e.trade.pos.clear(); e.trade.stops.clear()


def lot_of(e, sym, side):
    ls = [l for l in e.state['lots'].values() if l['symbol'] == sym and l['side'] == side]
    assert len(ls) <= 1
    return ls[0] if ls else None


def sig(i, close, atr):
    return dict(close=close, atr=atr, time=f'2026-10-{1 + i // 24:02d} {i % 24:02d}:00:00', le=True, se=False)


def stream(seed=7, n=24):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        sym = rnd.choice(['BTCUSDT', 'ETHUSDT'])
        out.append(dict(i=i, sym=sym, side=rnd.choice(['LONG', 'SHORT']), eq=rnd.choice([30.0, 60.0, 150.0, 500.0, 2000.0]),
                        risk=rnd.choice([0.005, 0.01, 0.02, 0.03]), atr=rnd.choice([0.5, 2.0, 6.0])))
    return out


def run(e, s, ctrl=True):
    """One automatic entry decision on a clean book. -> lot fields or None, and the skip text."""
    reset(e)
    sl = E.sleeve('T', 'ema_st', 1.0, s['risk'], 4, 'all')
    px = e.trade.mark[s['sym']]
    ok = e.open_lot(sl, s['sym'], s['side'], sig(s['i'], px, s['atr']), None, s['eq'], ctrl=ctrl)
    l = lot_of(e, s['sym'], s['side'])
    assert bool(ok) == (l is not None), e.last_skip
    return ({k: l[k] for k in LOT_FIELDS} if l else None), e.last_skip


# ---------------------------------------------------------------- 1) mapping
def test_mapping_exact_and_monotonic():
    m = {b: (lm, sm) for b, (_, lm, sm) in E.CTRL_BIAS.items()}
    assert m == dict(strong_bearish=(0.0, 1.0), bearish=(0.5, 1.0), neutral=(1.0, 1.0), bullish=(1.0, 0.5), strong_bullish=(1.0, 0.0))
    pct = [E.CTRL_RISK[k][1] for k in ('low', 'medium', 'high', 'max_test')]
    assert pct == [0.5, 1.0, 1.5, 2.0] and pct == sorted(pct) and E.CTRL_RISK_BASE_PCT == 2.0
    for b, r in COMBOS:
        x = E.ctrl_effect(b, r)
        assert 0.0 <= x['long_factor'] <= 1.0 and 0.0 <= x['short_factor'] <= 1.0, 'a control can never add risk'
    base = E.ctrl_effect('neutral', 'max_test')
    assert base['long_factor'] == base['short_factor'] == base['risk_factor'] == 1.0
    assert E.CTRL_DEFAULT == dict(CTRL_BIAS='neutral', CTRL_RISK='max_test')
    assert E.GLOBAL_DEFAULTS['CTRL_BIAS'] == 'neutral' and E.GLOBAL_DEFAULTS['CTRL_RISK'] == 'max_test'
    with pytest.raises(ValueError): E.ctrl_effect('sideways', 'low')
    with pytest.raises(ValueError): E.ctrl_effect('neutral', 'yolo')


def test_risk_monotonic_on_real_sizing():
    e, _ = mk()
    for b in ('neutral', 'bearish'):
        last = -1.0
        for r in ('low', 'medium', 'high', 'max_test'):
            set_ctrl(e, b, r)
            got, _ = run(e, dict(i=0, sym='BTCUSDT', side='SHORT', eq=2000.0, risk=0.02, atr=2.0))
            assert got and got['qty'] >= last; last = got['qty']


# ---------------------------------------------------------------- 2) Neutral + Maximum Test == today
def test_neutral_max_test_is_todays_behaviour(monkeypatch):
    e, _ = mk()
    set_ctrl(e, 'neutral', 'max_test')
    calls = []
    real = F.size_check
    monkeypatch.setattr(F, 'size_check', lambda *a: calls.append(a) or real(*a))
    for s in stream(11, 30):
        n0 = len(calls)
        a = run(e, s)
        assert len(calls) - n0 <= 1, 'no extra baseline sizing at the default'
        b = run(e, s, ctrl=False)                      # the control path switched off entirely
        assert a == b
        if a[0]:                                       # and the pre-CTRL-01 formula: risk / (2.5 ATR), floored, lev cap
            raw = s['eq'] * s['risk'] / (2.5 * s['atr'])
            px = e.trade.mark[s['sym']]
            q = F.round_step(min(raw, e.S['MAX_LEVERAGE'] * s['eq'] / px), 0.001)
            assert a[0]['qty'] == q and a[0]['stop'] == pytest.approx(px - (1 if s['side'] == 'LONG' else -1) * 2.5 * s['atr'])


# ---------------------------------------------------------------- 3) subset, no flip, stop present + never widened, x0
def test_every_level_is_a_subset_of_neutral_never_flips_never_widens():
    e, _ = mk()
    sigs = stream(3, 24)
    set_ctrl(e, 'neutral', 'max_test')
    base = {s['i']: run(e, s)[0] for s in sigs}
    assert any(base.values()) and not all(base.values()), 'stream mixes taken and too-small entries'
    for b, r in COMBOS:
        set_ctrl(e, b, r)
        x = E.ctrl_effect(b, r)
        for s in sigs:
            got, why = run(e, s)
            if got is None: continue
            bl = base[s['i']]
            assert bl is not None, f'{b}/{r} took an entry the baseline refused: {s}'
            assert got['side'] == s['side'] == bl['side'] and got['symbol'] == bl['symbol']
            assert got['stop'] == bl['stop'], 'stop is never moved to compensate for size'
            sd = 1 if s['side'] == 'LONG' else -1
            assert sd * (e.trade.mark[s['sym']] - got['stop']) > 0, 'a protective stop on the right side'
            assert got['qty'] <= bl['qty'] and got['risk_usd'] <= bl['risk_usd'] + 1e-9
            f = x['long_factor' if s['side'] == 'LONG' else 'short_factor']
            assert f > 0
            assert got['risk_usd'] == pytest.approx(round(s['eq'] * s['risk'] * f, 2), abs=0.011)
        # the opposite side is never created
        assert all(l['side'] in ('LONG', 'SHORT') for l in e.state['lots'].values())


@pytest.mark.parametrize('bias,side', [('strong_bearish', 'LONG'), ('strong_bullish', 'SHORT')])
def test_zero_multiplier_disables_never_inverts(bias, side):
    e, _ = mk()
    for r in E.CTRL_RISK:
        set_ctrl(e, bias, r)
        n_open = e.trade.calls.count('open')
        got, why = run(e, dict(i=1, sym='BTCUSDT', side=side, eq=2000.0, risk=0.02, atr=2.0))
        assert got is None and why.startswith('control: direction bias') and e.trade.calls.count('open') == n_open
        assert not e.state['lots'], 'no lot at all - in particular not the other side'
        other = 'SHORT' if side == 'LONG' else 'LONG'
        assert run(e, dict(i=1, sym='BTCUSDT', side=other, eq=2000.0, risk=0.02, atr=2.0))[0] is not None


def test_scaled_below_minimum_is_skipped_never_rounded_up():
    e, _ = mk()
    s = dict(i=2, sym='BTCUSDT', side='LONG', eq=60.0, risk=0.02, atr=2.0)   # 1.2 / 5 = 0.24 BTC = 24 USDT
    set_ctrl(e, 'neutral', 'max_test'); assert run(e, s)[0]['qty'] == 0.24
    set_ctrl(e, 'bearish', 'low')                                               # x0.125 -> 3 USDT < 5 minimum
    got, why = run(e, s)
    assert got is None and why.startswith('control: scaled size') and 'never rounded up' in why
    assert TA.reason_code(why) == ('execution', 'ctrl_size_min')
    assert TA.reason_code('control: direction bias Strong Bearish disables long entries (long x0 / short x1)') == ('filter', 'ctrl_bias')


# ---------------------------------------------------------------- 4) caps hold for bias x risk
def test_caps_hold_for_every_combination():
    e, _ = mk(MAX_LEVERAGE=1)
    s = dict(i=3, sym='BTCUSDT', side='LONG', eq=500.0, risk=0.3, atr=2.0)     # 150 / 5 = 30 BTC = 3000 USDT > 1x * 500
    set_ctrl(e, 'neutral', 'max_test'); bl = run(e, s)[0]
    assert bl['qty'] == 5.0
    for b, r in COMBOS:
        set_ctrl(e, b, r)
        got = run(e, s)[0]
        if got: assert got['qty'] * 100.0 <= 500.0 + 1e-9 and got['qty'] <= bl['qty']


def test_enforced_rule_refusing_the_baseline_refuses_every_level():
    e, _ = mk(RISK_RULES=dict(open_risk_cap=dict(mode='enforce', pct=1.5)))
    s = dict(i=4, sym='BTCUSDT', side='SHORT', eq=500.0, risk=0.02, atr=2.0)   # 2 % of bot capital at risk > 1.5 %
    set_ctrl(e, 'neutral', 'max_test')
    got, why = run(e, s)
    assert got is None and why.startswith('risk rule open_risk_cap')
    for b, r in COMBOS:                                   # a smaller size would pass the rule - the control must not
        set_ctrl(e, b, r)
        assert run(e, s)[0] is None, (b, r)


# ---------------------------------------------------------------- 5) open / pending / partially filled: untouched
def test_existing_positions_pending_and_partial_are_byte_identical():
    e, _ = mk()
    set_ctrl(e, 'neutral', 'max_test')
    for side in ('LONG', 'SHORT'):
        assert e.open_lot(E.sleeve('A' + side[0], 'ema_st', 1.0, 0.02, 4, 'all'), 'BTCUSDT', side, sig(5, 100.0, 2.0), None, 500.0), e.last_skip
    e.state['pending_entries']['P|ETHUSDT'] = dict(sleeve='P', symbol='ETHUSDT', side='LONG', ext=50.0, atr=1.0, dev=0.5,
                                                   sg=dict(close=50.0, atr=1.0, time='2026-10-05 00:00:00'), tf='4h',
                                                   created=1.0, until=9e12)
    e.state['resting_entries']['R|ETHUSDT'] = dict(sleeve='R', symbol='ETHUSDT', side='SHORT', qty=2.0, filled=0.7, cost=35.0,
                                                   n=1, status='open', cid=BC.new_cid('E'), plan=dict(qty=2.0, stop_dist=2.5))
    e.save_state()
    keep = ('lots', 'pending_entries', 'resting_entries')
    snap = lambda: (json.dumps({k: e.state[k] for k in keep}, sort_keys=True), json.dumps(e.trade.stops, sort_keys=True))
    before = snap()
    for b, r in COMBOS:
        set_ctrl(e, b, r)
        e.save_settings(); e.save_state()
        assert snap() == before, (b, r)
    # a later NEW entry on another slot is the only thing that differs
    set_ctrl(e, 'bearish', 'low')
    assert e.open_lot(E.sleeve('C', 'ema_st', 1.0, 0.02, 4, 'all'), 'ETHUSDT', 'SHORT', sig(6, 50.0, 1.0), None, 500.0)
    after = json.loads(snap()[0])
    old = json.loads(before[0])
    assert {k: after['lots'][k] for k in old['lots']} == old['lots']
    assert after['pending_entries'] == old['pending_entries'] and after['resting_entries'] == old['resting_entries']


def test_controls_are_read_only_inside_open_lot():
    """Structural: the control is consulted ONLY by open_lot (new entries) and the status/preview - never by adds, DCA
    safety orders, maker re-prices / fallbacks, exits or stop management."""
    tree = ast.parse(open(os.path.join(ROOT, 'engine.py'), encoding='utf-8').read())
    users = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for n in ast.walk(fn):
                if isinstance(n, ast.Attribute) and n.attr in ('ctrl_now',) or isinstance(n, ast.Constant) and n.value in ('CTRL_BIAS', 'CTRL_RISK'):
                    users.add(fn.name)
    assert users <= {'open_lot', 'ctrl_now', 'ctrl_status', 'load_settings', 'ctrl_norm'}, users


# ---------------------------------------------------------------- 6) manual / Take now: not covered
def test_manual_and_take_now_ignore_the_controls(monkeypatch):
    e, _ = mk()
    set_ctrl(e, 'strong_bearish', 'low')
    df = pd.DataFrame(dict(c=[100.0], atr=[2.0]))
    monkeypatch.setattr(e, 'candles', lambda *a, **k: df)
    assert e.manual_trade('BTCUSDT', 'LONG', 0.01, 2.5)
    assert lot_of(e, 'BTCUSDT', 'LONG')['qty'] == pytest.approx(F.round_step(500 * 0.01 / 5.0, 0.001))
    reset(e)
    sl = E.sleeve('A', 'ema_st', 1.0, 0.02, 4, 'all'); e.S['SLEEVES'] = [sl]
    e.signals['A|BTCUSDT'] = dict(sig(7, 100.0, 2.0), le=True, se=False)
    assert e.take_signal('A', 'BTCUSDT')
    assert lot_of(e, 'BTCUSDT', 'LONG')['qty'] == 2.0, 'Take now uses the slot risk unscaled (owner-override rules)'


# ---------------------------------------------------------------- 7) preview == engine
def test_preview_equals_engine():
    e, _ = mk()
    sls = [E.sleeve('A', 'ema_st', 0.5, 0.02, 4, 'all'), E.sleeve('B', 'ema_mom', 0.5, 0.005, 4, 'all')]
    e.S['SLEEVES'] = sls
    for b, r in COMBOS:
        set_ctrl(e, b, r)
        st = e.ctrl_status()
        pv = st['table'][f'{b}|{r}']
        assert pv == st['current'] == E.ctrl_effect(b, r, sls)
        eng = e.ctrl_now()
        assert (eng['long_factor'], eng['short_factor']) == (pv['long_factor'], pv['short_factor'])
        for sl, row in zip(sls, pv['sleeves']):
            assert row['long_risk_pct'] == pytest.approx(sl['risk'] * 100 * eng['long_factor'])
            assert row['short_risk_pct'] == pytest.approx(sl['risk'] * 100 * eng['short_factor'])
            for side, f in (('LONG', eng['long_factor']), ('SHORT', eng['short_factor'])):
                got = run(e, dict(i=8, sym='BTCUSDT', side=side, eq=2000.0, risk=sl['risk'], atr=2.0))[0]
                want = row['long_risk_pct' if side == 'LONG' else 'short_risk_pct'] / 100 * 2000.0
                if f == 0: assert got is None
                elif got: assert got['risk_usd'] == pytest.approx(round(want, 2), abs=0.011)
    src = open(os.path.join(ROOT, 'panel.html'), encoding='utf-8').read()
    assert "c.table[sel.bias+'|'+sel.risk]" in src, 'the panel previews from the engine table, not its own math'
    assert "('ctrl', e.ctrl_status)" in open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()


# ---------------------------------------------------------------- 8) logging
def _ctrl_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith('CTRL-01 ')]


def test_logging_only_when_the_control_had_an_effect(caplog):
    caplog.set_level(logging.INFO)
    e, _ = mk()
    s = dict(i=9, sym='BTCUSDT', side='LONG', eq=500.0, risk=0.02, atr=2.0)
    set_ctrl(e, 'neutral', 'max_test'); caplog.clear(); run(e, s)
    assert _ctrl_lines(caplog) == [] and not getattr(e, 'ctrl_events', [])
    set_ctrl(e, 'bullish', 'max_test'); caplog.clear(); run(e, s)        # long x1 at max test: no effect
    assert _ctrl_lines(caplog) == []
    set_ctrl(e, 'bearish', 'medium'); caplog.clear(); run(e, s)          # 2 BTC -> x0.25 -> 0.5 BTC
    ln = _ctrl_lines(caplog)
    assert len(ln) == 1 and ln[0].startswith('CTRL-01 SCALE BTCUSDT LONG') and 'long x0.5 / short x1' in ln[0] and 'x0.25' in ln[0]
    ev = e.ctrl_events[-1]
    assert ev['event'] == 'ctrl_scale' and ev['base_qty'] == 2.0 and ev['qty'] == 0.5 and ev['factor'] == 0.25
    set_ctrl(e, 'strong_bearish', 'high'); caplog.clear(); run(e, s)
    ln = _ctrl_lines(caplog)
    assert len(ln) == 1 and ln[0].startswith('CTRL-01 REJECT') and 'long x0 / short x1' in ln[0]
    # both baseline and scaled too small: an ordinary skip, the control had no effect -> no control line
    tiny = dict(s, eq=5.0)
    set_ctrl(e, 'bearish', 'low'); caplog.clear()
    got, why = run(e, tiny)
    assert got is None and why.startswith('size below Binance minimum') and _ctrl_lines(caplog) == []
    # the leverage cap binds both: same final size -> no effect -> no line
    e.S['MAX_LEVERAGE'] = 1
    set_ctrl(e, 'neutral', 'low'); caplog.clear()
    got, _ = run(e, dict(s, risk=0.5))
    assert got['qty'] == 5.0 and _ctrl_lines(caplog) == []


# ---------------------------------------------------------------- 9) persistence + default migration
@pytest.fixture
def api(monkeypatch):
    import app as A
    e, tmp = mk()
    e.save_settings()
    app = A.App.__new__(A.App); app.engine = e; app.preview = lambda: None; app.cfg = {}; app.tg = None
    monkeypatch.setattr(A, 'APP', app, raising=False)
    return A, e, tmp


def _restart(tmp, fake):
    E.Futures = lambda *a, **k: fake
    e = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)
    e.data = e.trade; e.connect(); e.marks = e.trade.marks()
    return e


def test_api_save_and_reload(api):
    A, e, tmp = api
    assert (e.S['CTRL_BIAS'], e.S['CTRL_RISK']) == ('neutral', 'max_test')
    A.handle('/api/settings', dict(CTRL_BIAS='bullish', CTRL_RISK='low'))
    assert (e.S['CTRL_BIAS'], e.S['CTRL_RISK']) == ('bullish', 'low')
    e2 = _restart(tmp, e.trade)
    assert (e2.S['CTRL_BIAS'], e2.S['CTRL_RISK']) == ('bullish', 'low') and not e2.S['ENTRIES_PAUSED']


@pytest.mark.parametrize('body', [dict(CTRL_BIAS='sideways'), dict(CTRL_RISK='yolo'), dict(CTRL_RISK=2.0),
                                  dict(CTRL_BIAS='bullish', CTRL_RISK='Maximum Test'), dict(CTRL_BIAS=None)])
def test_api_rejects_invalid_and_changes_nothing(api, body):
    A, e, tmp = api
    before = json.dumps(e.S, sort_keys=True, default=str), open(e.F['settings'], 'rb').read()
    with pytest.raises(ValueError):
        A.handle('/api/settings', body)
    assert (json.dumps(e.S, sort_keys=True, default=str), open(e.F['settings'], 'rb').read()) == before


def test_missing_on_disk_migrates_to_todays_behaviour():
    e, tmp = mk()
    e.S.pop('CTRL_BIAS'); e.S.pop('CTRL_RISK')                           # an old (pre-CTRL-01) settings file
    e.save_settings()
    raw = json.loads(open(e.F['settings'], encoding='utf-8').read())
    assert raw.get('CTRL_BIAS') is None and raw.get('CTRL_RISK') is None
    e2 = _restart(tmp, e.trade)
    assert (e2.S['CTRL_BIAS'], e2.S['CTRL_RISK']) == ('neutral', 'max_test')
    assert not e2.S['ENTRIES_PAUSED']
    x = e2.ctrl_now()
    assert x['long_factor'] == x['short_factor'] == 1.0


@pytest.mark.parametrize('bad', [None, 'sideways', 7, ['neutral'], 'NEUTRAL'])
def test_invalid_on_disk_fails_down_to_low_risk(bad):
    e, tmp = mk()
    e.S['CTRL_BIAS'] = bad; e.S['CTRL_RISK'] = bad
    e.save_settings()
    e2 = _restart(tmp, e.trade)
    assert (e2.S['CTRL_BIAS'], e2.S['CTRL_RISK']) == ('neutral', 'low')
    assert not e2.S['ENTRIES_PAUSED'], 'a bad control value is migrated, not treated as a corrupt file'
    x = e2.ctrl_now()
    assert x['long_factor'] == x['short_factor'] == .25
