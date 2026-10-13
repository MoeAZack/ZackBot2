"""STRAT-02 `trend_ema_mom_short.v1.cap180`: short-side R4b runner symmetry (contract
docs/newcore/research/STRAT02_SHORT_CONTRACT.md, frozen at PR #71) on synthetic fixtures only - no market data, no
registration, no ledger. Each test carries the Evidence case id(s) it covers (S02-<AREA>-<n>, 48 cases); the long
(STRAT-01) path is covered unchanged by tests/test_strat01_r4b_pit.py."""
import math
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'research'))
import costs as C                                                                           # noqa: E402
import pit as P                                                                             # noqa: E402
import pit_eval as PE                                                                       # noqa: E402
import pit_run as PR                                                                        # noqa: E402
import splits as S                                                                          # noqa: E402
import trend_ema_mom as T                                                                   # noqa: E402

TF, HOUR, DAY = T.TF_MS, 3_600_000, 86_400_000
T0 = S.parse_utc('2024-01-01T00:00:00Z')                    # a Monday
RULE = {'tick': 0.01, 'step': 0.001, 'min_qty': 0.001, 'min_notional': 5.0}
BASE = C.STRESS['base']
SCFG = {c['name']: c for c in PR.configs_short()}
PRIM = PR.SHORT_PRIMARY_NAME


def t_of(i):
    return T0 + (i + 1) * TF


def mk_tape(closes, *, sym='AAAUSDT', le=(), lx=(), d='2.0', a='0.8', lows=None, highs=None, opens=None, drop=(),
            fund_drop=(), mark_none=(), regime='btc_up', n_last=True, rate=0.0001, foff=3):
    """A short-side tape (`side: short`; `e` / `x` carry se / sx): bar i closes at t_of(i), open = previous close
    unless `opens` says otherwise; funding every 8h at calc_time + `foff` ms with mark = the bar open; `a` = ATR14."""
    out = []
    op = lambda i: (opens or {}).get(i, closes[i - 1] if i else closes[0])
    for i, c in enumerate(closes):
        t = t_of(i)
        o = op(i)
        lo = (lows or {}).get(i, min(o, c) - 0.25)
        h = (highs or {}).get(i, max(o, c) + 0.25)
        f = [[ft + foff, rate(ft) if callable(rate) else rate, 8, None if ft in mark_none else o]
             for ft in range(T0, t + 1, 8 * HOUR) if t - TF < ft + foff <= t and ft not in fund_drop]
        g = regime(i) if callable(regime) else regime
        if i in drop:
            out.append({'t': t, 'r': [{'s': sym, 'f': f}] if f else [], 'g': g, 'side': 'short'})
            continue
        nxt = op(i + 1) if i + 1 < len(closes) else ((opens or {}).get(i + 1, c) if n_last else None)
        rec = {'s': sym, 'b': [o, h, lo, c], 'f': f, 'w': '1' * 13, 'x': ('1' if i in lx else '0') * 13,
               'e': {str(k): d for k in range(13)} if i in le else {}, 'd': d, 'n': nxt, 'a': a}
        out.append({'t': t, 'r': [rec], 'g': g, 'side': 'short'})
    return out


def merge(*tapes):
    return [{'t': cyc[0]['t'], 'r': [r for c in cyc for r in c['r']], 'g': cyc[0]['g'], 'side': 'short'}
            for cyc in zip(*tapes)]


def acct(tape, cfg=PRIM, first=None, last=None, hi=None, rows=('base',), rules=None):
    syms = sorted({r['s'] for o in tape for r in o['r']})
    return PR.account(tape, SCFG[cfg], symbols=syms, first=first if first is not None else t_of(20),
                      last=last if last is not None else t_of(len(tape) - 2), hi=hi or tape[-1]['t'], slip_c=0.05,
                      rules=rules or {s: RULE for s in syms}, rows=rows, side='short')


def sym_of(tape, s='AAAUSDT'):
    return PR.read_tape(tape)[1][s]


def entry_fill(tape, i, tick=0.01, s='AAAUSDT', scen=BASE):
    """The expected short entry fill for a se at bar i: sell at bar i+1's open, slipped DOWN, floored to the tick."""
    x = sym_of(tape, s)
    return C.fill_price(x.rec[t_of(i + 1)]['b'][0], 'sell', x.bps(t_of(i), scen.vol, 0.05), scen, tick=tick)


FLAT = [100.0] * 60


# ------------------------------------------------------------------ STOP
def test_s02_stop_01_stop_is_ceiled_to_the_tick_above_the_fill():
    rule = dict(RULE, tick=0.5)
    tape = mk_tape(FLAT, le={25}, lx={40}, d='2.3')
    (tr,) = acct(tape, rules={'AAAUSDT': rule})['counted']
    fill = entry_fill(tape, 25, tick=0.5)
    assert tr['entry_price'] == fill and tr['stop_price'] == math.ceil((fill + 2.3) / 0.5 - 1e-9) * 0.5
    assert tr['stop_price'] - tr['entry_price'] >= 2.3                 # rounding can only widen the risk


def test_s02_stop_02_a_bar_opening_above_the_stop_fills_at_its_open():
    tape = mk_tape(FLAT, le={25}, opens={28: 110.0})
    (tr,) = acct(tape)['counted']
    x = sym_of(tape)
    assert tr['exit_reason'] == 'exit.stop' and tr['gap'] and tr['exit_ms'] == t_of(27)      # the bar OPEN
    assert tr['exit_price'] == C.fill_price(110.0, 'buy', x.bps(t_of(27), C.TR_SMA14, 0.05), BASE, gap=True,
                                            tick=0.01)
    assert tr['r']['base'] < -1.0


def test_s02_stop_03_an_open_exactly_at_the_stop_is_a_gap_fill():
    probe = mk_tape(FLAT, le={25})
    stop = math.ceil((entry_fill(probe, 25) + 2.0) / 0.01 - 1e-9) * 0.01
    stop = round(stop, 2)
    tape = mk_tape(FLAT, le={25}, opens={28: stop})
    (tr,) = acct(tape)['counted']
    assert tr['stop_price'] == stop and tr['gap'] and tr['exit_ms'] == t_of(27)


def test_s02_stop_04_an_intrabar_high_at_the_stop_fills_at_the_stop_plus_slip_rounded_up():
    probe = mk_tape(FLAT, le={25})
    stop = round(math.ceil((entry_fill(probe, 25) + 2.0) / 0.01 - 1e-9) * 0.01, 2)
    tape = mk_tape(FLAT, le={25}, highs={28: stop})
    (tr,) = acct(tape)['counted']
    x = sym_of(tape)
    assert tr['exit_reason'] == 'exit.stop' and not tr['gap'] and tr['exit_ms'] == t_of(28)  # the stop-bar CLOSE
    assert tr['exit_price'] == C.fill_price(stop, 'buy', x.bps(t_of(27), C.TR_SMA14, 0.05), BASE, tick=0.01)
    assert tr['exit_price'] >= stop


def test_s02_stop_05_a_high_one_tick_below_the_stop_does_not_trigger():
    probe = mk_tape(FLAT, le={25})
    stop = round(math.ceil((entry_fill(probe, 25) + 2.0) / 0.01 - 1e-9) * 0.01, 2)
    (tr,) = acct(mk_tape(FLAT, le={25}, lx={35}, highs={28: round(stop - 0.01, 2)}))['counted']
    assert tr['exit_reason'] == 'exit.signal' and tr['exit_ms'] == t_of(35)


# ------------------------------------------------------------------ ORDER
def test_s02_order_01_a_signal_cover_due_at_the_open_fills_before_the_bar_is_played():
    (tr,) = acct(mk_tape(FLAT, le={25}, lx={27}, highs={28: 150.0}))['counted']
    assert tr['exit_reason'] == 'exit.signal' and tr['exit_ms'] == t_of(27) and not tr['gap']


def test_s02_order_02_the_cap_cover_at_the_open_precedes_a_stop_in_the_cap_bar():
    closes = [100.0] * 260
    (tr,) = acct(mk_tape(closes, le={25}, highs={206: 1000.0}))['counted']
    assert tr['exit_reason'] == 'exit.time_cap' and tr['exit_ms'] == t_of(205)


# ------------------------------------------------------------------ SIZE / GROSS
def test_s02_size_01_qty_is_one_percent_of_actual_risk_floored_to_the_step():
    tape = mk_tape(FLAT, le={25}, lx={30})
    a = acct(tape, rows=tuple(C.STRESS))
    (tr,) = a['counted']
    fill = entry_fill(tape, 25)
    stop = round(math.ceil((fill + 2.0) / 0.01 - 1e-9) * 0.01, 2)
    q = math.floor(min(10_000 * 0.01 / (stop - fill), 3 * 10_000 / (100.0 * 1.10)) / 0.001 + 1e-9) * 0.001
    assert tr['qty'] == pytest.approx(q) and tr['stop_price'] == pytest.approx(stop)
    assert tr['r']['base'] == pytest.approx(tr['pnl'] / (tr['qty'] * (stop - fill)))
    assert a['rows']['base']['wallet_end'] == pytest.approx(10_000 + tr['pnl'])
    assert set(a['rows']) == set(C.STRESS)


def test_s02_size_02_below_min_qty_is_a_counted_refusal():
    a = acct(mk_tape(FLAT, le={25}, lx={40}), rules={'AAAUSDT': dict(RULE, min_qty=1e6)})
    assert a['counted'] == [] and a['refusals'] == {'execution.size_min': 1}


def test_s02_size_03_min_notional_is_checked_on_the_executable_fill_not_the_signal_close():
    tape = lambda: mk_tape(FLAT, le={25}, lx={40}, opens={26: 90.0})     # the sell fills ~10% below the signal close
    (tr,) = acct(tape(), rules={'AAAUSDT': dict(RULE, min_notional=1.0)})['counted']
    q, fill = tr['qty'], tr['entry_price']
    assert fill < 90.0 and q * fill < q * 100.0
    mid = (q * fill + q * 100.0) / 2                                     # passes on q x close, fails on q x fill
    bad = acct(tape(), rules={'AAAUSDT': dict(RULE, min_notional=mid)}, rows=tuple(C.STRESS))
    assert bad['counted'] == [] and bad['refusals'] == {'execution.size_min': 1}
    assert all(x['counted'] == [] for x in bad['rows'].values())


@pytest.mark.parametrize('d', ['0', '-1.5'])
def test_s02_size_04_a_zero_or_negative_stop_risk_is_refused_never_sized(d):
    a = acct(mk_tape(FLAT, le={25}, lx={40}, d=d), rows=tuple(C.STRESS))
    assert a['counted'] == [] and a['refusals'] == {'execution.stop_risk_invalid': 1}


@pytest.mark.parametrize('d', ['NaN', 'Infinity'])
def test_s02_size_05_a_non_finite_stop_risk_is_refused_never_sized(d):
    a = acct(mk_tape(FLAT, le={25}, lx={40}, d=d), rows=tuple(C.STRESS))
    assert a['counted'] == [] and a['refusals'] == {'execution.stop_risk_invalid': 1}
    assert all(x['counted'] == [] for x in a['rows'].values())


@pytest.mark.parametrize('tick', [0.01, 0.5, 1.0, 3.0, 7.0])
def test_s02_size_06_initial_risk_never_exceeds_one_percent_on_coarse_ticks(tick):
    rule = dict(RULE, tick=tick, step=0.0001)
    for d in ('0.3', '1.7', '2.0', '4.9', '9.99'):
        closes = [100.0 - 0.137 * i for i in range(60)]
        a = acct(mk_tape(closes, le={25}, lx={40}, d=d), rules={'AAAUSDT': rule}, rows=tuple(C.STRESS))
        for row, x in a['rows'].items():
            for tr in x['counted']:
                risk_u = tr['stop_price'] - tr['entry_price']
                assert risk_u >= float(d) - 1e-9, (tick, d, row)
                assert tr['qty'] * risk_u <= 0.01 * tr['snapshot'] * (1 + 1e-9), (tick, d, row)
                assert abs(tr['stop_price'] / tick - round(tr['stop_price'] / tick)) < 1e-6
                assert abs(tr['entry_price'] / tick - round(tr['entry_price'] / tick)) < 1e-6


def test_s02_gross_01_gross_notional_is_capped_at_3x_on_the_signal_close_x_1_10():
    (tr,) = acct(mk_tape(FLAT, le={25}, lx={40}, d='0.01'))['counted']
    assert tr['qty'] * 100.0 * 1.10 <= 3 * 10_000 + 1e-6
    assert tr['qty'] == pytest.approx(math.floor(3 * 10_000 / 110.0 / 0.001 + 1e-9) * 0.001)


# ------------------------------------------------------------------ FUND
def _events(tape, a, b):
    return sym_of(tape).events(a, b)


@pytest.mark.parametrize('rate,sign', [(0.0001, -1), (-0.0001, 1)])
def test_s02_fund_01_02_a_short_receives_positive_and_pays_negative_funding(rate, sign):
    tape = mk_tape(FLAT, le={25}, lx={30}, rate=rate)
    (tr,) = acct(tape)['counted']
    ev = _events(tape, t_of(25), t_of(30))
    assert len(ev) == 3
    want = C.funding_cost('short', tr['qty'], ev, BASE)
    assert tr['funding'] == pytest.approx(want, abs=1e-12) and math.copysign(1, tr['funding']) == sign
    assert want == pytest.approx(-math.fsum(r * tr['qty'] * m for _, r, m in ev) if rate > 0 else
                                 math.fsum(-r * tr['qty'] * m for _, r, m in ev))


def test_s02_fund_03_events_are_charged_on_entry_le_t_lt_exit():
    tape = mk_tape(FLAT, le={25}, lx={31}, foff=0)        # events exactly on bar boundaries: 104h (entry) ... 128h
    (tr,) = acct(tape)['counted']
    assert (tr['entry_ms'], tr['exit_ms']) == (t_of(25), t_of(31))
    ev = _events(tape, t_of(25), t_of(31))
    times = [e[0] for e in ev]
    assert times[0] == t_of(25) and t_of(31) not in times and len(times) == 3     # entry counts, exit does not
    assert tr['funding'] == pytest.approx(C.funding_cost('short', tr['qty'], ev, BASE), abs=1e-12)


def test_s02_fund_04_funding_x2_doubles_the_signed_funding():
    a = acct(mk_tape(FLAT, le={25}, lx={30}), rows=('base', 'funding_x2'))
    b, x2 = a['rows']['base']['counted'][0], a['rows']['funding_x2']['counted'][0]
    assert b['qty'] == x2['qty'] and x2['funding'] == pytest.approx(2 * b['funding'], abs=1e-12)
    assert b['funding'] < 0 and x2['pnl'] > b['pnl']                 # positive rate: x2 helps the short


def _has_event(i):
    return any(t_of(i) - TF <= ft + 3 < t_of(i) for ft in range(T0, t_of(i) + 1, 8 * HOUR))


def test_s02_fund_05_a_non_gap_stop_pays_the_stop_bars_funding_and_a_gap_stop_does_not():
    k = next(k for k in range(28, 36) if _has_event(k))
    probe = mk_tape(FLAT, le={25})
    stop = round(math.ceil((entry_fill(probe, 25) + 2.0) / 0.01 - 1e-9) * 0.01, 2)
    (ng,) = acct(mk_tape(FLAT, le={25}, highs={k: stop + 1}))['counted']
    assert not ng['gap'] and ng['exit_ms'] == t_of(k)
    tape = mk_tape(FLAT, le={25})
    assert ng['funding'] == pytest.approx(C.funding_cost('short', ng['qty'], _events(tape, t_of(25), t_of(k)), BASE))
    (gp,) = acct(mk_tape(FLAT, le={25}, opens={k: stop + 1}))['counted']
    assert gp['gap'] and gp['exit_ms'] == t_of(k - 1)
    assert gp['funding'] == pytest.approx(C.funding_cost('short', gp['qty'], _events(tape, t_of(25), t_of(k - 1)),
                                                         BASE))
    assert len(_events(tape, t_of(25), t_of(k))) == len(_events(tape, t_of(25), t_of(k - 1))) + 1


@pytest.mark.parametrize('kw,reason', [({'mark_none': {T0 + 14 * 8 * HOUR}}, 'incomplete.funding_mark'),
                                       ({'fund_drop': {T0 + 13 * 8 * HOUR}}, 'incomplete.funding')])
def test_s02_fund_06_a_missing_mark_or_funding_event_makes_the_trade_incomplete(kw, reason):
    a = acct(mk_tape([100.0] * 80, le={25}, lx={32}, **kw))
    assert a['counted'] == [] and [i['reason'] for i in a['incomplete']] == [reason]


# ------------------------------------------------------------------ CAP
def test_s02_cap_01_the_180_bar_cap_covers_at_the_next_open_rounded_up():
    closes = [100.0] * 260
    tape = mk_tape(closes, le={25}, opens={206: 50.0})
    (tr,) = acct(tape)['counted']
    assert tr['exit_reason'] == 'exit.time_cap' and tr['exit_ms'] - tr['entry_ms'] == 180 * TF and tr['bars'] == 180
    x = sym_of(tape)
    assert tr['exit_price'] == C.fill_price(50.0, 'buy', x.bps(tr['exit_ms'], C.TR_SMA14, 0.05), BASE, tick=0.01)


def test_s02_cap_02_a_cap_due_at_the_split_end_reads_the_next_open_or_is_incomplete():
    closes = [100.0] * 206
    hi = t_of(205)
    (tr,) = acct(mk_tape(closes, le={25}, opens={206: 77.0}), hi=hi, last=t_of(25))['counted']
    assert tr['exit_ms'] == hi and tr['exit_reason'] == 'exit.time_cap' and tr['exit_price'] > 77.0
    b = acct(mk_tape(closes, le={25}, n_last=False), hi=hi, last=t_of(25))
    assert b['counted'] == [] and [i['reason'] for i in b['incomplete']] == ['incomplete.split_end']


# ------------------------------------------------------------------ MFE / MAE
def _stopped(low=50.0):
    probe = mk_tape(FLAT, le={25})
    stop = round(math.ceil((entry_fill(probe, 25) + 2.0) / 0.01 - 1e-9) * 0.01, 2)
    (tr,) = acct(mk_tape(FLAT, le={25}, highs={28: stop + 5.0}, lows={28: low}))['counted']
    return tr


def test_s02_mfe_01_the_stop_bar_bounds_mfe_by_its_open_never_its_low():
    tr = _stopped()
    risk = tr['stop_price'] - tr['entry_price']
    assert tr['mfe_r'] == pytest.approx((tr['entry_price'] - 99.75) / risk)    # earlier lows (99.75) + open (100)
    assert tr['mfe_r'] < (tr['entry_price'] - 50.0) / risk


def test_s02_mfe_02_the_stop_bar_bounds_mae_by_the_stop():
    tr = _stopped()
    assert tr['mae_r'] == pytest.approx(-1.0)                          # never the stop bar's own high (stop + 5)


# ------------------------------------------------------------------ CROSS / ENTRY / EXIT (rule predicates)
def test_s02_cross_01_cross_down_is_strict_now_and_inclusive_before():
    assert PE.cross_down([1.0, 0.5], [1.0, 1.0], 1)                     # equal before, below now
    assert not PE.cross_down([1.0, 1.0], [1.0, 1.0], 1)                 # equal now: no cross
    assert not PE.cross_down([0.9, 0.5], [1.0, 1.0], 1)                 # already below: no cross
    assert not PE.cross_down([0.5], [1.0], 0)                           # no previous bar


def _bars(n, f=lambda i: 100.0 + 3 * math.sin(i / 9.0) - 0.01 * i):
    return [P.Bar(T0 + i * TF, f(i - 1), max(f(i), f(i - 1)) * 1.003, min(f(i), f(i - 1)) * 0.997, f(i), 1, 1,
                  T0 + (i + 1) * TF) for i in range(n)]


def test_s02_cross_02_short_signals_are_the_exact_mirror_formula_for_every_configuration():
    bars = _bars(900)
    seen = 0
    for n in range(225, 900, 7):
        got, lng = PE.short_signals(bars[:n]), PE.signals(bars[:n])
        c = [b.close for b in bars[:n]]
        h = [b.high for b in bars[:n]]
        lo = [b.low for b in bars[:n]]
        i = n - 1
        for (name, p), g, gl in zip(PE.SIGNAL_CONFIGS, got, lng):
            ef, es, et = T.ema(c, p.ema_fast), T.ema(c, p.ema_slow), T.ema(c, p.ema_trend)
            rs, rl = T.pct_return(c, p.bars_of(p.mom_short_days), i), T.pct_return(c, p.bars_of(p.mom_long_days), i)
            if i < p.start():
                assert g is None and gl is None, (name, n)
                continue
            mom = rs < 0 and rl < 0
            se = ef[i] < es[i] and ef[i - 1] >= es[i - 1] and c[i] < et[i] and mom
            assert g == (se, ef[i] > es[i] or not mom, T.wilder_atr(h, lo, c, p.atr_len)[i]), (name, n)
            assert not (g[0] and gl[0])                                  # never a long and a short entry at once
            seen += g[0]
    assert seen > 0


def _sig(monkeypatch, *, ef, es, et, close, rs, rl):
    """(se, sx) of the primary with the indicator values at bars i-1, i forced (rule predicate edges)."""
    n = T.PRIMARY.start() + 1
    bars = [P.Bar(T0 + i * TF, 100.0, 101.0, 99.0, 100.0 if i < n - 1 else close, 1, 1, T0 + (i + 1) * TF)
            for i in range(n)]
    arrs = {T.PRIMARY.ema_fast: ef, T.PRIMARY.ema_slow: es, T.PRIMARY.ema_trend: et}
    monkeypatch.setattr(PE.T, 'ema', lambda c, k: [0.0] * (len(c) - 2) + list(arrs[k]))
    monkeypatch.setattr(PE.T, 'pct_return', lambda c, k, i: rs if k == 42 else rl)
    (g,) = PE.short_signals(bars, (('primary', T.PRIMARY),))
    return g[0], g[1]


def test_s02_cross_03_close_equal_to_the_trend_ema_is_not_an_entry(monkeypatch):
    kw = dict(ef=(1.0, 0.5), es=(1.0, 1.0), rs=-0.01, rl=-0.02)
    assert _sig(monkeypatch, et=(0, 100.0), close=100.0, **kw) == (False, False)
    assert _sig(monkeypatch, et=(0, 100.0), close=99.99, **kw) == (True, False)


def test_s02_cross_04_a_zero_return_is_not_negative_momentum(monkeypatch):
    kw = dict(ef=(1.0, 0.5), es=(1.0, 1.0), et=(0, 200.0), close=100.0)
    assert _sig(monkeypatch, rs=0.0, rl=-0.02, **kw) == (False, True)       # no entry, and momentum loss = cover
    assert _sig(monkeypatch, rs=-1e-12, rl=-0.02, **kw) == (True, False)


def test_s02_cross_05_fast_equal_to_slow_is_neither_a_cross_nor_a_cover(monkeypatch):
    kw = dict(et=(0, 200.0), close=100.0, rs=-0.01, rl=-0.02)
    assert _sig(monkeypatch, ef=(1.1, 1.0), es=(1.0, 1.0), **kw) == (False, False)
    assert _sig(monkeypatch, ef=(1.1, 1.0 + 1e-12), es=(1.0, 1.0), **kw) == (False, True)   # strictly above: cover


def test_s02_cross_06_a_non_negative_long_return_covers(monkeypatch):
    kw = dict(ef=(0.5, 0.5), es=(1.0, 1.0), et=(0, 200.0), close=100.0, rs=-0.01)
    assert _sig(monkeypatch, rl=0.0, **kw) == (False, True)
    assert _sig(monkeypatch, rl=-0.02, **kw) == (False, False)


# ------------------------------------------------------------------ WINDOW (contract section 3.1)
class _View:
    def __init__(self, bars, j):
        self._b, self.t = bars[:j + 1], bars[j].available_ms

    def members(self):
        return frozenset({'AAAUSDT'})

    def bars(self, s, tf, n):
        return tuple(self._b[-n:])

    def funding(self, s, since):
        return ()

    def mark_bars(self, s, n):
        return ()

    def next_open(self, s, tf):
        return None


def _rebased(bars, k0):
    """The same prices re-timed so the list ends at the same times (prefix changes keep the window's times)."""
    end = bars[-1].open_ms
    n = len(bars)
    return [P.Bar(end - (n - 1 - i) * TF, b.open, b.high, b.low, b.close, 1, 1, end - (n - 2 - i) * TF)
            for i, b in enumerate(bars)]


def test_s02_window_01_gating_exact_prefix_invariance():
    """Changing, adding or removing any bar BEFORE the 1500-bar read window leaves se / sx / stop distance (and the
    whole record) bit-identical."""
    base = _bars(1800)
    changed = [P.Bar(b.open_ms, b.open * 1.7, b.high * 1.9, b.low * 0.5, b.close * 1.3, 1, 1, b.available_ms)
               if i < 300 else b for i, b in enumerate(base)]
    removed = _rebased(base[250:], 0)
    added = _rebased(_bars(200, lambda i: 55.0 + i) + base, 0)
    for j_from_end in range(0, 6):
        outs = []
        for bars in (base, changed, removed, added):
            j = len(bars) - 1 - j_from_end
            outs.append(PE.Tape(side='short').decide(_View(bars, j)))
        assert outs[0]['r'][0]['w'] == '1' * 13
        assert all(o == outs[0] for o in outs[1:]), j_from_end


def _ind(bars):
    c = [b.close for b in bars]
    h = [b.high for b in bars]
    lo = [b.low for b in bars]
    out = {f'ema{n}': T.ema(c, n)[-1] for n in sorted({p.ema_fast for _, p in PE.SIGNAL_CONFIGS}
                                                      | {p.ema_slow for _, p in PE.SIGNAL_CONFIGS}
                                                      | {p.ema_trend for _, p in PE.SIGNAL_CONFIGS})}
    out['atr14'] = T.wilder_atr(h, lo, c, 14)[-1]
    return out


def test_s02_window_02_declared_tolerance_vs_longer_history_is_1e_5_relative():
    long_hist = _bars(3000)
    full, window = _ind(long_hist), _ind(long_hist[-T.WINDOW:])
    assert 'ema250' in full and 'ema200' in full
    for k in full:
        assert abs(window[k] - full[k]) <= 1e-5 * abs(full[k]), k


def test_s02_window_03_before_a_full_window_decisions_depend_on_the_history_start_by_design():
    bars = _bars(600)
    a, b = _ind(bars[-300:]), _ind(bars[-260:])                    # two contiguous-history starts, warm-up reached
    assert abs(a['ema200'] - b['ema200']) > 1e-5 * abs(a['ema200'])  # declared, accepted (contract 3.1 point 3)
    assert ((200 - 1) / (200 + 1)) ** 220 == pytest.approx(0.1108, abs=1e-3)


# ------------------------------------------------------------------ COST / RND
def test_s02_cost_01_02_entry_sells_below_the_open_cover_buys_above_both_taker():
    tape = mk_tape(FLAT, le={25}, lx={30})
    (tr,) = acct(tape)['counted']
    x = sym_of(tape)
    ef = C.fill_price(100.0, 'sell', x.bps(t_of(25), C.TR_SMA14, 0.05), BASE, tick=0.01)
    xf = C.fill_price(100.0, 'buy', x.bps(t_of(30), C.TR_SMA14, 0.05), BASE, tick=0.01)
    assert tr['entry_price'] == ef < 100.0 and tr['exit_price'] == xf > 100.0
    assert tr['fees'] == pytest.approx((ef + xf) * tr['qty'] * 0.0005)
    assert tr['pnl'] == pytest.approx((ef - xf) * tr['qty'] - tr['fees'] - tr['funding'])


def test_s02_rnd_01_worked_example_sell_fill_down_stop_ceiled():
    """open 100.0, slip 7 bps (0.07), tick 0.1, ATR 2.02 -> fill 99.9, stop 105.0, risk 5.1 (Cowork spec v2)."""
    fill = C.fill_price(100.0, 'sell', 7.0, BASE, tick=0.1)
    dist = float(T.stop_distance(2.02, T.PRIMARY))
    stop = PR._ceil(fill + dist, 0.1)
    assert (fill, dist, stop) == (99.9, 5.05, 105.0) and round(stop - fill, 9) == 5.1


def test_s02_rnd_02_cover_fills_round_up_and_the_book_uses_the_rounded_values():
    assert C.fill_price(100.0, 'buy', 7.0, BASE, tick=0.1) == 100.1
    rule = dict(RULE, tick=0.1)
    (tr,) = acct(mk_tape(FLAT, le={25}, lx={30}), rules={'AAAUSDT': rule})['counted']
    for k in ('entry_price', 'exit_price', 'stop_price'):
        assert abs(tr[k] * 10 - round(tr[k] * 10)) < 1e-9, k
    assert tr['exit_price'] >= 100.0 and tr['entry_price'] <= 100.0


def test_s02_rnd_03_on_tick_values_are_unchanged():
    assert PR._ceil(105.0, 0.1) == 105.0 and PR._ceil(105.0000001, 0.1) == 105.1
    assert C.round_adverse(99.9, 0.1, 'sell') == 99.9 and C.round_adverse(100.1, 0.1, 'buy') == 100.1


# ------------------------------------------------------------------ BOOK
def test_s02_book_01_exits_free_a_slot_before_entries_in_frozen_rank_order():
    syms = [f'S{k}USDT' for k in range(5)]
    tapes = [mk_tape(FLAT, sym=s, le={25}, lx={40} if s == 'S0USDT' else {50}) for s in syms[:4]]
    tapes.append(mk_tape(FLAT, sym='S4USDT', le={25, 40}, lx={50}))
    a = acct(merge(*tapes))
    assert a['refusals'] == {'capacity.max_positions': 1}               # S4's bar-25 signal: book full
    late = [t for t in a['trades'] if t['symbol'] == 'S4USDT']
    assert [t['entry_ms'] for t in late] == [t_of(40)]                  # S0's cover at that open freed the slot
    first = sorted(a['trades'], key=lambda t: (t['entry_ms'], t['symbol']))[:4]
    assert [t['symbol'] for t in first] == syms[:4]


def test_s02_book_02_the_cairo_day_mtm_halt_refuses_that_days_entries():
    closes = [100.0] * 32 + [102.0] * 40                                # bar 32 gaps up through a 0.5 stop
    a = acct(merge(mk_tape(closes, sym='AAAUSDT', le={25}, d='0.5', opens={32: 102.0}),
                   mk_tape([100.0] * 72, sym='BBBUSDT', le={32})))
    (tr,) = a['counted']
    assert tr['symbol'] == 'AAAUSDT' and tr['gap'] and tr['pnl'] < -300
    assert a['refusals'] == {'filter.halt': 1}


# ------------------------------------------------------------------ LIQ (PARK-only diagnostic)
def test_s02_liq_01_a_worst_case_bar_below_initial_margin_is_flagged_and_changes_nothing():
    tape = mk_tape(FLAT, le={25}, d='0.3', highs={26: 115.0})
    res = PR.run_book(PR.read_tape(tape), SCFG[PRIM], ['AAAUSDT'], first=t_of(20), last=t_of(58), hi=tape[-1]['t'],
                      slip_c=0.05, rules={'AAAUSDT': RULE}, side='short')
    liq = res['liquidation']
    (tr,) = res['trades']
    g = tr['qty'] * 115.0
    w = 10_000 - tr['qty'] * tr['entry_price'] * 0.0005 + tr['qty'] * (tr['entry_price'] - 115.0)
    assert w < g / 3 and liq['flagged_bars'] == 1 and liq['park'] and liq['bars'] == 1
    assert liq['min_w_over_g'] == pytest.approx(w / g)
    assert liq['max_bar_high_over_open'] == pytest.approx(0.15)
    assert tr['exit_reason'] == 'exit.stop' and not tr['gap'] and tr['exit_ms'] == t_of(26)   # the stop still rules


def test_s02_liq_02_an_ordinary_book_is_not_flagged_and_the_long_book_has_no_diagnostic():
    tape = mk_tape(FLAT, le={25}, lx={40})
    res = PR.run_book(PR.read_tape(tape), SCFG[PRIM], ['AAAUSDT'], first=t_of(20), last=t_of(58), hi=tape[-1]['t'],
                      slip_c=0.05, rules={'AAAUSDT': RULE}, side='short')
    assert res['liquidation']['flagged_bars'] == 0 and not res['liquidation']['park']
    assert res['liquidation']['bars'] == 15 and res['liquidation']['min_w_over_g'] > 1 / 3
    long_cfg = {c['name']: c for c in PR.configs()}[PR.PRIMARY_NAME]
    lres = PR.run_book(PR.read_tape(tape), long_cfg, ['AAAUSDT'], first=t_of(20), last=t_of(58), hi=tape[-1]['t'],
                       slip_c=0.05, rules={'AAAUSDT': RULE})
    assert 'liquidation' not in lres and 'side' not in lres


# ------------------------------------------------------------------ DELIST (bars end while held / flat)
def test_s02_delist_01_a_held_symbol_whose_bars_end_is_incomplete_never_a_silent_close():
    a = acct(mk_tape(FLAT, le={25}, drop=set(range(41, 60))))
    assert a['counted'] == [] and [i['reason'] for i in a['incomplete']] == ['incomplete.gap']


def test_s02_delist_02_a_flat_symbol_whose_bars_end_leaves_no_record():
    a = acct(mk_tape(FLAT, drop=set(range(41, 60))))
    assert a['trades'] == [] and a['incomplete'] == [] and a['refusals'] == {}


# ------------------------------------------------------------------ B0 (matched random SHORT entries)
def test_s02_b0_01_matched_random_short_draws_use_the_short_fill_stop_and_funding():
    closes = [100.0 + 2 * math.sin(i / 7) for i in range(200)]
    lab = lambda i: 'btc_down' if i == 60 else None                     # the pool = the candidate's own decision
    tape = mk_tape(closes, le={60}, lx={70}, regime=lab)
    prim = acct(tape, first=t_of(20), last=t_of(150))
    (tr,) = prim['counted']
    kw = dict(first=t_of(20), last=t_of(150), hi=tape[-1]['t'], slip_c=0.05, draws=20, side='short',
              rules={'AAAUSDT': RULE})
    a, b = PR.b0_pairs(tape, prim, seed_key='k', **kw), PR.b0_pairs(tape, prim, seed_key='k', **kw)
    assert a == b and a['invalid_draws'] == 0 and a['episodes'] == 1
    x = sym_of(tape)
    e, h = t_of(60), tr['bars']
    ef = C.fill_price(x.rec[e + TF]['b'][0], 'sell', x.bps(e, C.TR_SMA14, 0.05), BASE, tick=0.01)
    stop = PR._ceil(ef + 2.0, 0.01)
    xf = C.fill_price(x.rec[e + (h + 1) * TF]['b'][0], 'buy', x.bps(e + h * TF, C.TR_SMA14, 0.05), BASE, tick=0.01)
    fees = C.fee(ef, 'taker', C.CRYPTO, BASE) + C.fee(xf, 'taker', C.CRYPTO, BASE)
    fund = C.funding_cost('short', 1.0, x.events(e, e + h * TF), BASE)
    want = (ef - xf - fees - fund) / (stop - ef)
    assert a['diffs'] == [pytest.approx(tr['r']['base'] - want)]
    with pytest.raises(PR.PitRunError):
        PR.b0_pairs(tape, prim, seed_key='k', **dict(kw, rules=None))


# ------------------------------------------------------------------ B1 (perp short benchmark book, section 7.1)
def _b1(tapes, *, rules=None, first=None, last=None, row='base'):
    tape = merge(*tapes) if isinstance(tapes, list) else tapes
    syms = sorted({r['s'] for o in tape for r in o['r']})
    return PR.b1_short_book(tape, symbols=syms, first=first or t_of(20), last=last or t_of(len(tape) - 2),
                            hi=tape[-1]['t'], slip_c=0.05, rules=rules or {s: RULE for s in syms}, row=row)


def test_s02_b1_01_monday_rebalances_and_the_sizing_equation():
    closes = [100.0] * 120
    res = _b1([mk_tape(closes, sym='AAAUSDT', a='0.8'), mk_tape(closes, sym='BBBUSDT', a='0.4')], last=t_of(100))
    taus = [r['tau'] for r in res['rebalances']]
    assert taus[0] == t_of(20) and all(datetime.fromtimestamp(t / 1000, tz=timezone.utc).weekday() == 0
                                       and t % DAY == 0 for t in taus[1:])
    assert taus == [t_of(20), t_of(41), t_of(83)]
    first = [lg for lg in res['legs'] if lg['time_ms'] == t_of(20)]
    want = {'AAAUSDT': (0.01 * 10_000 / 2) / (2.5 * 0.8), 'BBBUSDT': (0.01 * 10_000 / 2) / (2.5 * 0.4)}
    assert {lg['symbol']: lg['qty'] for lg in first} == pytest.approx({s: math.floor(q / 0.001 + 1e-9) * 0.001
                                                                       for s, q in want.items()})
    assert all(lg['action'] == 'sell' and lg['fill'] < 100.0 for lg in first)
    assert res['status'] == 'COMPLETE' and res['incomplete'] == []


def test_s02_b1_02_the_3x_scale_down_and_legs_below_the_minimums():
    closes = [100.0] * 60
    res = _b1([mk_tape(closes, sym='AAAUSDT', a='0.001'), mk_tape(closes, sym='BBBUSDT', a='0.002')], last=t_of(30))
    first = {lg['symbol']: lg['qty'] for lg in res['legs'] if lg['time_ms'] == t_of(20)}
    raw = {'AAAUSDT': 50 / 0.0025, 'BBBUSDT': 50 / 0.005}
    k = 3 * 10_000 / math.fsum(q * 100.0 * 1.10 for q in raw.values())
    assert first == pytest.approx({s: math.floor(q * k / 0.001 + 1e-9) * 0.001 for s, q in raw.items()})
    assert math.fsum(q * 100.0 * 1.10 for q in first.values()) <= 3 * 10_000 + 1e-6
    tiny = _b1([mk_tape(closes, sym='AAAUSDT'), mk_tape(closes, sym='BBBUSDT')], last=t_of(30),
               rules={s: dict(RULE, min_notional=1e9) for s in ('AAAUSDT', 'BBBUSDT')})
    assert tiny['legs'] == [] and tiny['refusals'] == {'b1.size_min': 2}


def test_s02_b1_03_signed_funding_and_month_block_r():
    closes = [100.0] * 200
    pos = _b1([mk_tape(closes, sym='AAAUSDT', rate=0.0001)], last=t_of(150))
    neg = _b1([mk_tape(closes, sym='AAAUSDT', rate=-0.0001)], last=t_of(150))
    assert pos['wallet_end'] > 10_000 - 1 > neg['wallet_end'] - 1 and pos['wallet_end'] > neg['wallet_end']
    assert pos['month_r'] == PR.month_r(pos['series'], t_of(20), t_of(150))
    mo = PR.months_of(t_of(20), t_of(150))
    assert list(pos['month_r']) == mo
    e = lambda ms: [m for t, m in pos['series'] if t <= ms][-1]
    end = S.parse_utc('2024-02-01T00:00:00Z')
    assert pos['month_r']['2024-01'] == pytest.approx((e(end) - 10_000) / (0.01 * 10_000))
    miss = _b1([mk_tape(closes, sym='AAAUSDT', mark_none={T0 + 20 * 8 * HOUR})], last=t_of(150))
    assert miss['status'] == 'INCOMPLETE' and [i['reason'] for i in miss['incomplete']] == ['incomplete.funding_mark']
    assert miss['excluded_symbols'] == ['AAAUSDT']


# ------------------------------------------------------------------ B2 short (not an Evidence case; rule check)
def test_b2_short_enters_below_the_close_20_bars_earlier_only():
    down = acct(mk_tape([100.0 - 0.1 * i for i in range(260)]), cfg='B2', first=t_of(20), last=t_of(30))
    assert down['counted'] and down['counted'][0]['entry_ms'] == t_of(20)
    up = acct(mk_tape([100.0 + 0.1 * i for i in range(200)]), cfg='B2', first=t_of(20), last=t_of(150))
    assert up['counted'] == []
    with pytest.raises(PR.PitRunError):
        PR._signal(SCFG['B1'], None, {'d': '1.0'}, t_of(20), 'short')
