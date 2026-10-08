"""newcore.strategy.ema_mom (trend_ema_mom.v1): rule, causality, days-based lookbacks, long/short symmetry,
closed-candle-only input, Decimal stop distance, determinism and purity."""
import ast
import math
import os
from decimal import Decimal, localcontext

import pytest

import newcore.strategy as S
from newcore.strategy import ema_mom as M
from ncs_helpers import CORE8, H1, H4, as_of, load, reflect, synthetic


@pytest.fixture(scope='module')
def btc4h():
    return load('BTCUSDT', '4h')


@pytest.fixture(scope='module')
def syn():
    return synthetic(4000)


# ------------------------------------------------------------------ identity / vocabulary
def test_rule_vocabulary_matches_nc01_values():
    assert S.RULE_ID == 'trend_ema_mom.v1'
    assert S.REASON_ENTRY == 'entry.signal' and S.REASON_EXIT == 'exit.exit_signal'     # NC-01 ReasonCode values
    assert [s.value for s in S.Side] == ['LONG', 'SHORT']                               # NC-01 orders.Side
    assert [a.value for a in S.SignalAction] == ['enter', 'close']                      # NC-01 Action.ENTER / CLOSE
    p = S.Params()
    assert (p.ema_fast, p.ema_slow, p.ema_trend, p.atr_len, p.stop_atr) == (20, 50, 200, 14, Decimal('2.5'))
    assert (p.mom_short_days, p.mom_long_days, p.warmup_bars, p.enable_short) == (7, 30, 220, False)


def test_params_validation():
    with pytest.raises(ValueError):
        S.Params(stop_atr=2.5)              # float refused: money-adjacent values are Decimal
    with pytest.raises(ValueError):
        S.Params(stop_atr=Decimal('0'))
    with pytest.raises(ValueError):
        S.Params(ema_fast=50, ema_slow=20)


# ------------------------------------------------------------------ rule semantics
def test_rule_matches_its_definition_bar_by_bar(syn):
    ev = S.evaluate(syn, as_of_ms=as_of(syn))
    assert ev.start == 220 and (ev.k_short, ev.k_long) == (42, 180)
    c = syn.c
    up = lambda a, b, i: a[i] > b[i] and a[i - 1] <= b[i - 1]
    for i in range(len(c)):
        if i < ev.start:
            assert not (ev.le[i] or ev.se[i] or ev.lx[i] or ev.sx[i])
            continue
        ml = ev.ret_short[i] > 0 and ev.ret_long[i] > 0
        ms = ev.ret_short[i] < 0 and ev.ret_long[i] < 0
        assert ev.le[i] == (up(ev.ema_fast, ev.ema_slow, i) and c[i] > ev.ema_trend[i] and ml)
        assert ev.se[i] == (up(ev.ema_slow, ev.ema_fast, i) and c[i] < ev.ema_trend[i] and ms)
        assert ev.lx[i] == (ev.ema_fast[i] < ev.ema_slow[i] or not ml)
        assert ev.sx[i] == (ev.ema_fast[i] > ev.ema_slow[i] or not ms)
    assert sum(ev.le) >= 3 and sum(ev.se) >= 3, 'fixture must exercise both entry sides'


def test_entry_decision_fields(syn):
    ev = S.evaluate(syn, as_of_ms=as_of(syn))
    ents = S.entries(ev)
    assert ents and all(d.side is S.Side.LONG for d in ents)          # short is off by default
    for d in ents:
        i = d.bar_index
        assert ev.le[i] and d.action is S.SignalAction.ENTER and d.reason == S.REASON_ENTRY
        assert d.rule == S.RULE_ID and d.symbol == 'SYNUSDT' and not d.mechanics_only
        assert d.bar_open_ms == syn.t_ms[i] and d.signal_time_ms == syn.t_ms[i] + H4
        assert type(d.atr) is Decimal and type(d.stop_distance) is Decimal
        assert d.atr == Decimal(repr(ev.atr[i]))
        assert d.stop_distance == Decimal('2.5') * d.atr
        assert math.isclose(float(d.stop_distance), 2.5 * ev.atr[i], rel_tol=1e-15)


def test_stop_distance_is_decimal_quantizable_and_context_independent():
    sd = S.stop_distance(123.456789012345, Decimal('2.5'))
    assert sd == Decimal('308.6419725308625')
    assert sd.quantize(Decimal('0.01')) == Decimal('308.64')
    with localcontext() as ctx:
        ctx.prec = 3                                                  # a hostile ambient context changes nothing
        assert S.stop_distance(123.456789012345, Decimal('2.5')) == sd


def test_close_decisions_are_emitted_before_entries_and_short_needs_enable(syn):
    ev_off = S.evaluate(syn, as_of_ms=as_of(syn))
    ev_on = S.evaluate(syn, S.Params(enable_short=True), as_of_ms=as_of(syn))
    seen_short = False
    for i in range(ev_on.start, len(syn)):
        off, on = S.decisions_at(ev_off, i), S.decisions_at(ev_on, i)
        assert all(d.side is S.Side.LONG for d in off)
        acts = [d.action for d in on]
        assert acts == sorted(acts, key=lambda a: a is S.SignalAction.ENTER)       # CLOSE before ENTER
        for d in on:
            if d.side is S.Side.SHORT:
                seen_short = True
                assert d.mechanics_only
                flag = ev_on.se[i] if d.action is S.SignalAction.ENTER else ev_on.sx[i]
                assert flag
            if d.action is S.SignalAction.CLOSE:
                assert d.stop_distance is None and d.reason == S.REASON_EXIT
    assert seen_short
    assert S.decisions_at(ev_on, ev_on.start - 1) == () and S.decisions_at(ev_on, len(syn)) == ()


# ------------------------------------------------------------------ causality
def test_truncating_the_future_never_changes_a_past_signal(btc4h):
    full = S.evaluate(btc4h, S.Params(enable_short=True), as_of_ms=as_of(btc4h))
    full_dec = {i: S.decisions_at(full, i) for i in range(len(btc4h))}
    for n in (221, 400, 1000, 3333, 7000, len(btc4h) - 1):
        part = S.evaluate(btc4h.head(n), S.Params(enable_short=True), as_of_ms=btc4h.close_ms(n - 1))
        for name in ('ema_fast', 'ema_slow', 'ema_trend', 'atr', 'ret_short', 'ret_long', 'le', 'se', 'lx', 'sx'):
            assert getattr(part, name) == getattr(full, name)[:n], name
        for i in range(n):
            assert S.decisions_at(part, i) == full_dec[i]


def test_rewriting_the_future_never_changes_a_past_signal(syn):
    n = 2500
    wild = synthetic(4000, seed=99)                 # a completely different future, spliced onto the same past
    k = len(syn) - n
    shift = syn.c[n - 1] / wild.c[0]
    sc = lambda xs: tuple(round(x * shift * 256) / 256 for x in xs[:k])
    spliced = S.Bars(syn.symbol, syn.tf_ms, syn.t_ms, syn.o[:n] + sc(wild.o), syn.h[:n] + sc(wild.h),
                     syn.l[:n] + sc(wild.l), syn.c[:n] + sc(wild.c))
    a = S.evaluate(syn, as_of_ms=as_of(syn))
    b = S.evaluate(spliced, as_of_ms=as_of(spliced))
    assert [S.decisions_at(a, i) for i in range(n)] == [S.decisions_at(b, i) for i in range(n)]
    assert a.le[n:] != b.le[n:] or a.lx[n:] != b.lx[n:]          # the future really did differ


def test_decide_reports_only_the_last_closed_candle(btc4h):
    full = S.evaluate(btc4h, as_of_ms=as_of(btc4h))
    i = S.entries(full)[5].bar_index
    head = btc4h.head(i + 1)
    got = S.decide(head, as_of_ms=head.close_ms(i))
    assert got == S.decisions_at(full, i)
    assert any(d.action is S.SignalAction.ENTER for d in got)


# ------------------------------------------------------------------ days, not bars
def test_lookback_conversion_is_in_days():
    assert (S.lookback_bars(7, H4), S.lookback_bars(30, H4)) == (42, 180)       # == legacy ret42 / ret180
    assert (S.lookback_bars(7, H1), S.lookback_bars(30, H1)) == (168, 720)
    assert S.lookback_bars(30, 900_000) == 2880                                 # 15m
    assert S.lookback_bars(1, 86_400_000) == 1                                  # 1d
    for bad_tf in (7 * H1, 0, -H4, 14_400_000.0):
        with pytest.raises(ValueError):
            S.lookback_bars(7, bad_tf)
    with pytest.raises(ValueError):
        S.lookback_bars(0, H4)


def _agg_4h(b1):
    """Aggregate aligned 1h candles into 4h candles (UTC 00/04/08/..)."""
    first = next(j for j, t in enumerate(b1.t_ms) if t % H4 == 0)
    t, o, h, l, c, last = [], [], [], [], [], []
    for j in range(first, len(b1) - 3, 4):
        t.append(b1.t_ms[j]); o.append(b1.o[j]); h.append(max(b1.h[j:j + 4])); l.append(min(b1.l[j:j + 4]))
        c.append(b1.c[j + 3]); last.append(j + 3)
    return S.Bars(b1.symbol, H4, tuple(t), tuple(o), tuple(h), tuple(l), tuple(c)), last


@pytest.mark.parametrize('src', ['synthetic', 'data_long'])
def test_1h_and_4h_momentum_filters_are_the_same_days(src):
    b1 = synthetic(6000, seed=3, tf_ms=H1) if src == 'synthetic' else load('ETHUSDT', '1h')
    b4, last = _agg_4h(b1)
    e1 = S.evaluate(b1, as_of_ms=as_of(b1))
    e4 = S.evaluate(b4, as_of_ms=as_of(b4))
    assert (e1.k_short, e1.k_long, e1.start) == (168, 720, 720)                 # warmup also covers the 30d lookback
    assert (e4.k_short, e4.k_long, e4.start) == (42, 180, 220)
    checked = 0
    for i, j in enumerate(last):
        if e4.ret_long[i] is None:
            assert e1.ret_long[j] is None or i < 180
            continue
        # same calendar window, same closes -> bit-identical returns at every 4h close
        assert e1.ret_short[j] == e4.ret_short[i] and e1.ret_long[j] == e4.ret_long[i]
        checked += 1
    assert checked > 500


# ------------------------------------------------------------------ long / mirrored-short symmetry
@pytest.mark.parametrize('seed', [7, 11, 2024])
def test_short_is_the_exact_mirror_of_long(seed):
    b = synthetic(4000, seed=seed)
    m2 = 2 * math.ceil(max(b.h)) + 256.0
    r = reflect(b, m2)
    p = S.Params(enable_short=True)
    e, f = S.evaluate(b, p, as_of_ms=as_of(b)), S.evaluate(r, p, as_of_ms=as_of(r))
    assert e.le == f.se and e.se == f.le and e.lx == f.sx and e.sx == f.lx
    assert sum(e.le) > 0 and sum(e.se) > 0
    for x, y in zip(e.atr, f.atr):
        assert math.isclose(x, y, rel_tol=1e-9)
    for d, g in zip(S.entries(e), S.entries(f), strict=True):
        assert d.bar_index == g.bar_index and d.signal_time_ms == g.signal_time_ms
        assert {d.side, g.side} == {S.Side.LONG, S.Side.SHORT}
        assert g.mechanics_only == (g.side is S.Side.SHORT) and d.mechanics_only == (d.side is S.Side.SHORT)
        assert math.isclose(float(d.stop_distance), float(g.stop_distance), rel_tol=1e-9)


# ------------------------------------------------------------------ closed candles only / input validation
def test_forming_candle_is_refused(btc4h):
    last_close = as_of(btc4h)
    S.evaluate(btc4h, as_of_ms=last_close)                                     # exactly closed: accepted
    for early in (last_close - 1, btc4h.t_ms[-1], btc4h.t_ms[-1] + 60_000):
        with pytest.raises(S.FormingCandle):
            S.evaluate(btc4h, as_of_ms=early)
        with pytest.raises(S.FormingCandle):
            S.decide(btc4h, as_of_ms=early)
    with pytest.raises(TypeError):
        S.evaluate(btc4h, as_of_ms=float(last_close))
    with pytest.raises(TypeError):
        S.evaluate(btc4h)                                                       # as_of_ms is mandatory


def test_bad_bars_are_refused(syn):
    b = syn.head(10)
    ok = dict(symbol='X', tf_ms=H4, t_ms=b.t_ms, o=b.o, h=b.h, l=b.l, c=b.c)
    S.Bars(**ok)
    bad = [
        dict(t_ms=b.t_ms[:5] + b.t_ms[6:] + (b.t_ms[-1] + H4,)),               # gap
        dict(t_ms=(b.t_ms[1], b.t_ms[0]) + b.t_ms[2:]),                         # disorder
        dict(t_ms=tuple(t + 1 for t in b.t_ms)),                                # misaligned to the timeframe
        dict(t_ms=tuple(float(t) for t in b.t_ms)),                             # not integer ms
        dict(tf_ms=7 * H1),                                                     # timeframe does not divide a day
        dict(c=b.c[:-1]),                                                       # length mismatch
        dict(c=b.c[:-1] + (float('nan'),)),
        dict(l=b.l[:-1] + (-1.0,)),
        dict(h=b.h[:-1] + (b.l[-1] * 0.5,)),                                    # high below low / open / close
    ]
    for patch in bad:
        with pytest.raises(S.BadBars):
            S.Bars(**{**ok, **patch})


# ------------------------------------------------------------------ determinism / purity
def test_deterministic_output(btc4h):
    p = S.Params(enable_short=True)
    a = S.evaluate(btc4h, p, as_of_ms=as_of(btc4h))
    b = S.evaluate(load('BTCUSDT', '4h'), p, as_of_ms=as_of(btc4h))
    assert a == b
    da, db = S.entries(a), S.entries(b)
    assert da == db and repr(da) == repr(db) and len(da) > 20


def test_module_has_no_io_clock_or_randomness_imports():
    pkg = os.path.dirname(M.__file__)
    allowed = {'__future__', 'enum', 'math', 'dataclasses', 'decimal', 'typing'}
    for fn in ('ema_mom.py', 'indicators.py', '__init__.py'):
        tree = ast.parse(open(os.path.join(pkg, fn), encoding='utf-8').read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert {a.name.split('.')[0] for a in node.names} <= allowed, (fn, ast.dump(node))
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                assert node.module.split('.')[0] in allowed, (fn, node.module)
            elif isinstance(node, ast.Name):
                assert node.id not in ('open', 'print', 'input', 'eval', 'exec'), (fn, node.id)


def test_core8_data_is_accepted_as_closed_contiguous_candles():
    for s in CORE8:
        b = load(s, '4h')
        assert len(b) == 10499 and b.t_ms[0] == 1639900800000                  # 2021-12-19 08:00 UTC
        ev = S.evaluate(b, as_of_ms=as_of(b))
        assert sum(ev.le) > 0
