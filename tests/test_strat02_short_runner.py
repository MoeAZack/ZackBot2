"""STRAT-02 `trend_ema_mom_short.v1.cap180`: short-side R4b runner symmetry (contract
docs/newcore/research/STRAT02_SHORT_CONTRACT.md, frozen at PR #71) on synthetic fixtures only - no market data, no
registration, no ledger. Cowork's synthetic spec v2 (#13 6152124742, 48 cases, sha256 3107b100...a11c) is bound by
name: every case id S02-<ID> has at least one test named test_s02_<id> (lower case, '-' -> '_'); `_book` tests are the
same case through the real book. `test_x_*` tests are additional Build checks outside the 48. The long (STRAT-01)
path is covered unchanged by tests/test_strat01_r4b_pit.py."""
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
def test_s02_stop_02_ceil_book_coarse_tick():
    rule = dict(RULE, tick=0.5)
    tape = mk_tape(FLAT, le={25}, lx={40}, d='2.3')
    (tr,) = acct(tape, rules={'AAAUSDT': rule})['counted']
    fill = entry_fill(tape, 25, tick=0.5)
    assert tr['entry_price'] == fill and tr['stop_price'] == math.ceil((fill + 2.3) / 0.5 - 1e-9) * 0.5
    assert tr['stop_price'] - tr['entry_price'] >= 2.3                 # rounding can only widen the risk


def test_s02_gap_01_book():
    tape = mk_tape(FLAT, le={25}, opens={28: 110.0})
    (tr,) = acct(tape)['counted']
    x = sym_of(tape)
    assert tr['exit_reason'] == 'exit.stop' and tr['gap'] and tr['exit_ms'] == t_of(27)      # the bar OPEN
    assert tr['exit_price'] == C.fill_price(110.0, 'buy', x.bps(t_of(27), C.TR_SMA14, 0.05), BASE, gap=True,
                                            tick=0.01)
    assert tr['r']['base'] < -1.0


def test_s02_gap_02_eq_book():
    probe = mk_tape(FLAT, le={25})
    stop = math.ceil((entry_fill(probe, 25) + 2.0) / 0.01 - 1e-9) * 0.01
    stop = round(stop, 2)
    tape = mk_tape(FLAT, le={25}, opens={28: stop})
    (tr,) = acct(tape)['counted']
    assert tr['stop_price'] == stop and tr['gap'] and tr['exit_ms'] == t_of(27)


def test_s02_intra_01_book():
    probe = mk_tape(FLAT, le={25})
    stop = round(math.ceil((entry_fill(probe, 25) + 2.0) / 0.01 - 1e-9) * 0.01, 2)
    tape = mk_tape(FLAT, le={25}, highs={28: stop})
    (tr,) = acct(tape)['counted']
    x = sym_of(tape)
    assert tr['exit_reason'] == 'exit.stop' and not tr['gap'] and tr['exit_ms'] == t_of(28)  # the stop-bar CLOSE
    assert tr['exit_price'] == C.fill_price(stop, 'buy', x.bps(t_of(27), C.TR_SMA14, 0.05), BASE, tick=0.01)
    assert tr['exit_price'] >= stop


def test_s02_intra_02_book():
    probe = mk_tape(FLAT, le={25})
    stop = round(math.ceil((entry_fill(probe, 25) + 2.0) / 0.01 - 1e-9) * 0.01, 2)
    (tr,) = acct(mk_tape(FLAT, le={25}, lx={35}, highs={28: round(stop - 0.01, 2)}))['counted']
    assert tr['exit_reason'] == 'exit.signal' and tr['exit_ms'] == t_of(35)


# ------------------------------------------------------------------ ORDER
def test_s02_order_01_signal_cover_at_open_before_the_stop():
    (tr,) = acct(mk_tape(FLAT, le={25}, lx={27}, highs={28: 150.0}))['counted']
    assert tr['exit_reason'] == 'exit.signal' and tr['exit_ms'] == t_of(27) and not tr['gap']


def test_s02_order_02_cap_cover_at_open_before_the_stop():
    closes = [100.0] * 260
    (tr,) = acct(mk_tape(closes, le={25}, highs={206: 1000.0}))['counted']
    assert tr['exit_reason'] == 'exit.time_cap' and tr['exit_ms'] == t_of(205)


# ------------------------------------------------------------------ SIZE / GROSS
def test_s02_size_01_book():
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


def test_s02_size_03_minqty_book():
    a = acct(mk_tape(FLAT, le={25}, lx={40}), rules={'AAAUSDT': dict(RULE, min_qty=1e6)})
    assert a['counted'] == [] and a['refusals'] == {'execution.size_min': 1}


def test_s02_size_04_minnotional_fill_book():
    tape = lambda: mk_tape(FLAT, le={25}, lx={40}, opens={26: 90.0})     # the sell fills ~10% below the signal close
    (tr,) = acct(tape(), rules={'AAAUSDT': dict(RULE, min_notional=1.0)})['counted']
    q, fill = tr['qty'], tr['entry_price']
    assert fill < 90.0 and q * fill < q * 100.0
    mid = (q * fill + q * 100.0) / 2                                     # passes on q x close, fails on q x fill
    bad = acct(tape(), rules={'AAAUSDT': dict(RULE, min_notional=mid)}, rows=tuple(C.STRESS))
    assert bad['counted'] == [] and bad['refusals'] == {'execution.size_min': 1}
    assert all(x['counted'] == [] for x in bad['rows'].values())


@pytest.mark.parametrize('d', ['0', '-1.5'])
def test_s02_size_05_zero_risk_book(d):
    a = acct(mk_tape(FLAT, le={25}, lx={40}, d=d), rows=tuple(C.STRESS))
    assert a['counted'] == [] and a['refusals'] == {'execution.stop_risk_invalid': 1}


@pytest.mark.parametrize('d', ['NaN', 'Infinity'])
def test_s02_size_06_nonfinite_book(d):
    a = acct(mk_tape(FLAT, le={25}, lx={40}, d=d), rows=tuple(C.STRESS))
    assert a['counted'] == [] and a['refusals'] == {'execution.stop_risk_invalid': 1}
    assert all(x['counted'] == [] for x in a['rows'].values())


@pytest.mark.parametrize('tick', [0.01, 0.5, 1.0, 3.0, 7.0])
def test_x_initial_risk_never_exceeds_one_percent_on_coarse_ticks(tick):
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


def test_s02_gross_01_book():
    (tr,) = acct(mk_tape(FLAT, le={25}, lx={40}, d='0.01'))['counted']
    assert tr['qty'] * 100.0 * 1.10 <= 3 * 10_000 + 1e-6
    assert tr['qty'] == pytest.approx(math.floor(3 * 10_000 / 110.0 / 0.001 + 1e-9) * 0.001)


# ------------------------------------------------------------------ FUND
def _events(tape, a, b):
    return sym_of(tape).events(a, b)


@pytest.mark.parametrize('rate,sign', [(0.0001, -1), (-0.0001, 1)])
def test_s02_fund_01_pos_fund_02_neg_book(rate, sign):
    tape = mk_tape(FLAT, le={25}, lx={30}, rate=rate)
    (tr,) = acct(tape)['counted']
    ev = _events(tape, t_of(25), t_of(30))
    assert len(ev) == 3
    want = C.funding_cost('short', tr['qty'], ev, BASE)
    assert tr['funding'] == pytest.approx(want, abs=1e-12) and math.copysign(1, tr['funding']) == sign
    assert want == pytest.approx(-math.fsum(r * tr['qty'] * m for _, r, m in ev) if rate > 0 else
                                 math.fsum(-r * tr['qty'] * m for _, r, m in ev))


def test_s02_fund_03_boundary():
    tape = mk_tape(FLAT, le={25}, lx={31}, foff=0)        # events exactly on bar boundaries: 104h (entry) ... 128h
    (tr,) = acct(tape)['counted']
    assert (tr['entry_ms'], tr['exit_ms']) == (t_of(25), t_of(31))
    ev = _events(tape, t_of(25), t_of(31))
    times = [e[0] for e in ev]
    assert times[0] == t_of(25) and t_of(31) not in times and len(times) == 3     # entry counts, exit does not
    assert tr['funding'] == pytest.approx(C.funding_cost('short', tr['qty'], ev, BASE), abs=1e-12)


def test_s02_fund_04_x2_book():
    a = acct(mk_tape(FLAT, le={25}, lx={30}), rows=('base', 'funding_x2'))
    b, x2 = a['rows']['base']['counted'][0], a['rows']['funding_x2']['counted'][0]
    assert b['qty'] == x2['qty'] and x2['funding'] == pytest.approx(2 * b['funding'], abs=1e-12)
    assert b['funding'] < 0 and x2['pnl'] > b['pnl']                 # positive rate: x2 helps the short


def _has_event(i):
    return any(t_of(i) - TF <= ft + 3 < t_of(i) for ft in range(T0, t_of(i) + 1, 8 * HOUR))


def test_s02_fund_05_stopbar():
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
def test_s02_fund_06_missingmark(kw, reason):
    a = acct(mk_tape([100.0] * 80, le={25}, lx={32}, **kw))
    assert a['counted'] == [] and [i['reason'] for i in a['incomplete']] == [reason]


# ------------------------------------------------------------------ CAP
def test_s02_cap_01_next_open_after_180_bars():
    closes = [100.0] * 260
    tape = mk_tape(closes, le={25}, opens={206: 50.0})
    (tr,) = acct(tape)['counted']
    assert tr['exit_reason'] == 'exit.time_cap' and tr['exit_ms'] - tr['entry_ms'] == 180 * TF and tr['bars'] == 180
    x = sym_of(tape)
    assert tr['exit_price'] == C.fill_price(50.0, 'buy', x.bps(tr['exit_ms'], C.TR_SMA14, 0.05), BASE, tick=0.01)


def test_s02_cap_02_splitend():
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


def test_x_mfe_stop_bar_open_bound_flat_book():
    tr = _stopped()
    risk = tr['stop_price'] - tr['entry_price']
    assert tr['mfe_r'] == pytest.approx((tr['entry_price'] - 99.75) / risk)    # earlier lows (99.75) + open (100)
    assert tr['mfe_r'] < (tr['entry_price'] - 50.0) / risk


def test_x_mae_stop_bound_flat_book():
    tr = _stopped()
    assert tr['mae_r'] == pytest.approx(-1.0)                          # never the stop bar's own high (stop + 5)


# ------------------------------------------------------------------ CROSS / ENTRY / EXIT (rule predicates)
def test_x_cross_down_edges():
    assert PE.cross_down([1.0, 0.5], [1.0, 1.0], 1)                     # equal before, below now
    assert not PE.cross_down([1.0, 1.0], [1.0, 1.0], 1)                 # equal now: no cross
    assert not PE.cross_down([0.9, 0.5], [1.0, 1.0], 1)                 # already below: no cross
    assert not PE.cross_down([0.5], [1.0], 0)                           # no previous bar


def _bars(n, f=lambda i: 100.0 + 3 * math.sin(i / 9.0) - 0.01 * i):
    return [P.Bar(T0 + i * TF, f(i - 1), max(f(i), f(i - 1)) * 1.003, min(f(i), f(i - 1)) * 0.997, f(i), 1, 1,
                  T0 + (i + 1) * TF) for i in range(n)]


def test_x_short_signals_are_the_exact_mirror_formula_for_every_configuration():
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


def test_s02_entry_02_strict(monkeypatch):
    kw = dict(ef=(1.0, 0.5), es=(1.0, 1.0), rs=-0.01, rl=-0.02)
    assert _sig(monkeypatch, et=(0, 100.0), close=100.0, **kw) == (False, False)
    assert _sig(monkeypatch, et=(0, 100.0), close=99.99, **kw) == (True, False)


def test_s02_entry_01_strict_and_exit_01(monkeypatch):
    kw = dict(ef=(1.0, 0.5), es=(1.0, 1.0), et=(0, 200.0), close=100.0)
    assert _sig(monkeypatch, rs=0.0, rl=-0.02, **kw) == (False, True)       # no entry, and momentum loss = cover
    assert _sig(monkeypatch, rs=-1e-12, rl=-0.02, **kw) == (True, False)


def test_x_fast_equal_to_slow_is_neither_a_cross_nor_a_cover(monkeypatch):
    kw = dict(et=(0, 200.0), close=100.0, rs=-0.01, rl=-0.02)
    assert _sig(monkeypatch, ef=(1.1, 1.0), es=(1.0, 1.0), **kw) == (False, False)
    assert _sig(monkeypatch, ef=(1.1, 1.0 + 1e-12), es=(1.0, 1.0), **kw) == (False, True)   # strictly above: cover


def test_x_a_non_negative_long_return_covers(monkeypatch):
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


def test_s02_window_01_prefix():
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


def test_s02_window_02_tol():
    long_hist = _bars(3000)
    full, window = _ind(long_hist), _ind(long_hist[-T.WINDOW:])
    assert 'ema250' in full and 'ema200' in full
    for k in full:
        assert abs(window[k] - full[k]) <= 1e-5 * abs(full[k]), k


def test_s02_window_03_early():
    bars = _bars(600)
    a, b = _ind(bars[-300:]), _ind(bars[-260:])                    # two contiguous-history starts, warm-up reached
    assert abs(a['ema200'] - b['ema200']) > 1e-5 * abs(a['ema200'])  # declared, accepted (contract 3.1 point 3)
    assert ((200 - 1) / (200 + 1)) ** 220 == pytest.approx(0.1108, abs=1e-3)


# ------------------------------------------------------------------ COST / RND
def test_s02_entryfill_01_fee_01_book():
    tape = mk_tape(FLAT, le={25}, lx={30})
    (tr,) = acct(tape)['counted']
    x = sym_of(tape)
    ef = C.fill_price(100.0, 'sell', x.bps(t_of(25), C.TR_SMA14, 0.05), BASE, tick=0.01)
    xf = C.fill_price(100.0, 'buy', x.bps(t_of(30), C.TR_SMA14, 0.05), BASE, tick=0.01)
    assert tr['entry_price'] == ef < 100.0 and tr['exit_price'] == xf > 100.0
    assert tr['fees'] == pytest.approx((ef + xf) * tr['qty'] * 0.0005)
    assert tr['pnl'] == pytest.approx((ef - xf) * tr['qty'] - tr['fees'] - tr['funding'])


def test_s02_rnd_01_sell():
    """open 100.0, slip 7 bps (0.07), tick 0.1, ATR 2.02 -> fill 99.9, stop 105.0, risk 5.1 (Cowork spec v2)."""
    fill = C.fill_price(100.0, 'sell', 7.0, BASE, tick=0.1)
    dist = float(T.stop_distance(2.02, T.PRIMARY))
    stop = PR._ceil(fill + dist, 0.1)
    assert (fill, dist, stop) == (99.9, 5.05, 105.0) and round(stop - fill, 9) == 5.1


def test_s02_rnd_02_cover():
    assert C.fill_price(100.0, 'buy', 7.0, BASE, tick=0.1) == 100.1
    rule = dict(RULE, tick=0.1)
    (tr,) = acct(mk_tape(FLAT, le={25}, lx={30}), rules={'AAAUSDT': rule})['counted']
    for k in ('entry_price', 'exit_price', 'stop_price'):
        assert abs(tr[k] * 10 - round(tr[k] * 10)) < 1e-9, k
    assert tr['exit_price'] >= 100.0 and tr['entry_price'] <= 100.0


def test_s02_rnd_03_ontick():
    assert PR._ceil(105.0, 0.1) == 105.0 and PR._ceil(105.0000001, 0.1) == 105.1
    assert C.round_adverse(99.9, 0.1, 'sell') == 99.9 and C.round_adverse(100.1, 0.1, 'buy') == 100.1


# ------------------------------------------------------------------ BOOK
def test_s02_book_01():
    syms = [f'S{k}USDT' for k in range(5)]
    tapes = [mk_tape(FLAT, sym=s, le={25}, lx={40} if s == 'S0USDT' else {50}) for s in syms[:4]]
    tapes.append(mk_tape(FLAT, sym='S4USDT', le={25, 40}, lx={50}))
    a = acct(merge(*tapes))
    assert a['refusals'] == {'capacity.max_positions': 1}               # S4's bar-25 signal: book full
    late = [t for t in a['trades'] if t['symbol'] == 'S4USDT']
    assert [t['entry_ms'] for t in late] == [t_of(40)]                  # S0's cover at that open freed the slot
    first = sorted(a['trades'], key=lambda t: (t['entry_ms'], t['symbol']))[:4]
    assert [t['symbol'] for t in first] == syms[:4]


def test_s02_halt_01():
    closes = [100.0] * 32 + [102.0] * 40                                # bar 32 gaps up through a 0.5 stop
    a = acct(merge(mk_tape(closes, sym='AAAUSDT', le={25}, d='0.5', opens={32: 102.0}),
                   mk_tape([100.0] * 72, sym='BBBUSDT', le={32})))
    (tr,) = a['counted']
    assert tr['symbol'] == 'AAAUSDT' and tr['gap'] and tr['pnl'] < -300
    assert a['refusals'] == {'filter.halt': 1}


# ------------------------------------------------------------------ LIQ (PARK-only diagnostic)
def test_s02_liq_01_book():
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


def test_x_liq_ordinary_book_not_flagged_and_long_has_no_diagnostic():
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
def test_x_held_symbol_whose_bars_end_is_incomplete_never_a_silent_close():
    a = acct(mk_tape(FLAT, le={25}, drop=set(range(41, 60))))
    assert a['counted'] == [] and [i['reason'] for i in a['incomplete']] == ['incomplete.gap']


def test_x_flat_symbol_whose_bars_end_leaves_no_record():
    a = acct(mk_tape(FLAT, drop=set(range(41, 60))))
    assert a['trades'] == [] and a['incomplete'] == [] and a['refusals'] == {}


# ------------------------------------------------------------------ B0 (matched random SHORT entries)
def test_s02_b0_01():
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


def test_x_b1_monday_rebalances_and_the_sizing_equation():
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


def test_x_b1_3x_scale_down_and_legs_below_the_minimums():
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


def test_x_b1_signed_funding_and_month_block_r():
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
def test_x_b2_short_enters_below_the_close_20_bars_earlier_only():
    down = acct(mk_tape([100.0 - 0.1 * i for i in range(260)]), cfg='B2', first=t_of(20), last=t_of(30))
    assert down['counted'] and down['counted'][0]['entry_ms'] == t_of(20)
    up = acct(mk_tape([100.0 + 0.1 * i for i in range(200)]), cfg='B2', first=t_of(20), last=t_of(150))
    assert up['counted'] == []
    with pytest.raises(PR.PitRunError):
        PR._signal(SCFG['B1'], None, {'d': '1.0'}, t_of(20), 'short')


# ================================================================== Cowork spec v2: the hand-checkable numbers
D = Decimal
R01 = dict(RULE, tick=0.1)


def test_s02_stop_01():
    q, stop, why = PR.size_short(10_000.0, 100.0, float(T.stop_distance(2.0, T.PRIMARY)), 100.0, 0.0, R01)
    assert why is None and stop == 105.0


def test_s02_stop_02_ceil():
    q, stop, why = PR.size_short(10_000.0, 100.0, float(T.stop_distance(2.02, T.PRIMARY)), 100.0, 0.0, R01)
    assert why is None and stop == 105.1                                # raw 105.05: a floor (105.0) would be wrong


def test_s02_gap_01():
    ref, gap = C.stop_reference('short', 105.0, 107.0)
    assert (ref, gap) == (107.0, True) and C.fill_price(ref, 'buy', 0.0, BASE, gap=True, tick=0.1) == 107.0
    assert D(repr(ref)) - D('100.0') == D('7.0')                        # not the stop level 105.0


def test_s02_gap_02_eq():
    assert C.stop_reference('short', 105.0, 105.0) == (105.0, True)     # open == stop: at-or-above = gap at the open


def test_s02_intra_01():
    o, h = 103.0, 105.0
    assert (o >= 105.0 or h >= 105.0) and C.stop_reference('short', 105.0, o) == (105.0, False)   # bar-close exit


def test_s02_intra_02():
    o, h = 103.0, 104.9
    assert not (o >= 105.0 or h >= 105.0)


def test_s02_size_01():
    q, stop, why = PR.size_short(10_000.0, 100.0, 5.0, 100.0, 0.0, dict(RULE, min_notional=5.0))
    assert (q, stop, why) == (20.0, 105.0, None) and q * 100.0 == 2000.0


def test_s02_size_02_floor():
    q, stop, why = PR.size_short(10_000.0, 100.0, 3.7, 100.0, 0.0, R01)
    assert why is None and q == 27.027                                  # 27.027027... floored, never upsized


def test_s02_size_03_minqty():
    q, stop, why = PR.size_short(10_000.0, 100.0, 5000.0, 100.0, 0.0, dict(RULE, min_qty=0.1))
    assert (q, why) == (None, 'execution.size_min')                     # raw 0.02 < 0.1: refused, not upsized


def test_s02_size_04_minnotional_fill():
    rule = dict(RULE, tick=0.01, step=0.0001, min_qty=0.0001, min_notional=5.0)
    dist = 1980.0                                                        # 100 / (stop - fill) floors to qty 0.0505
    assert PR._floor(100.0 / (PR._ceil(99.0 + dist, 0.01) - 99.0), 0.0001) == 0.0505
    assert D('0.0505') * D('100.0') == D('5.05000') and D('0.0505') * D('99.0') == D('4.99950')
    assert PR.size_short(10_000.0, 99.0, dist, 100.0, 0.0, rule) == (None, None, 'execution.size_min')
    assert PR.size_short(10_000.0, 100.0, dist, 100.0, 0.0, rule)[2] is None     # the same qty at 100 passes


def test_s02_size_05_zero_risk():
    assert PR.size_short(10_000.0, 100.0, 0.0, 100.0, 0.0, R01) == (None, None, 'execution.stop_risk_invalid')


def test_s02_size_06_nonfinite():
    nan = float(D('NaN'))
    assert PR.size_short(10_000.0, 100.0, nan, 100.0, 0.0, R01) == (None, None, 'execution.stop_risk_invalid')
    assert PR.size_short(10_000.0, 100.0, math.inf, 100.0, 0.0, R01) == (None, None, 'execution.stop_risk_invalid')


def test_s02_gross_01():
    """Spec v2 expects REFUSED for 29,000 open + 1,500 new > 30,000; the registered book (identical to v1, contract
    section 4) CLIPS the new lot to the remaining room on signal close x 1.10 - gross never exceeds 3x either way."""
    q, _, why = PR.size_short(10_000.0, 100.0, 0.5, 100.0, 29_000.0, R01)
    assert why is None and 29_000.0 + q * 100.0 * 1.10 <= 30_000.0 + 1e-9
    assert q == PR._floor(1_000.0 / 110.0, 0.001)
    assert PR.size_short(10_000.0, 100.0, 0.5, 100.0, 30_000.0, R01) == (None, None, 'execution.size_min')


def test_s02_fund_01_pos():
    cost = C.funding_cost('short', 1000.0, [(0, 0.0001, 1.0)], BASE)
    assert -cost == pytest.approx(0.1, abs=1e-15)                       # cash flow +0.1000: the short RECEIVES


def test_s02_fund_02_neg():
    cost = C.funding_cost('short', 1000.0, [(0, -0.0001, 1.0)], BASE)
    assert -cost == pytest.approx(-0.1, abs=1e-15)                      # cash flow -0.1000: the short PAYS


def test_s02_fund_04_x2():
    assert -C.funding_cost('short', 1000.0, [(0, -0.0001, 1.0)], C.STRESS['funding_x2']) == pytest.approx(-0.2)
    assert -C.funding_cost('short', 1000.0, [(0, 0.0001, 1.0)], C.STRESS['funding_x2']) == pytest.approx(0.2)


def test_s02_entryfill_01():
    assert C.fill_price(100.0, 'sell', 10.0, BASE, tick=0.1) == 99.9     # slip 0.1 on 100 = 10 bps, below the open
    assert C.fill_price(100.0, 'buy', 10.0, BASE, tick=0.1) == 100.1     # the cover, above the open


def test_s02_fee_01():
    assert C.fee(100.0 * 10, 'taker', C.CRYPTO, BASE) == pytest.approx(0.5)


def test_s02_cross_01():
    assert PE.cross_down([10.0, 9.9], [10.0, 10.0], 1)


def test_s02_cross_02():
    assert not PE.cross_down([10.1, 10.0], [10.0, 10.0], 1)


def test_s02_cross_03():
    assert not PE.cross_down([9.9, 9.8], [10.0, 10.0], 1)


def test_s02_exit_01(monkeypatch):
    se, sx = _sig(monkeypatch, ef=(0.5, 0.5), es=(1.0, 1.0), et=(0, 200.0), close=100.0, rs=0.0, rl=-0.02)
    assert sx and not se


def test_s02_mfe_01_and_mae_01():
    """Entry ~100 (fill 99.9 on a 0.1 tick), earlier lows 97 / 96, stop 105.0; the stop bar o 104, h 109, l 90:
    MFE uses min(97, 96, 104) = 96 (never the stop bar's own low 90); MAE is bounded by the stop (never 109)."""
    tape = mk_tape(FLAT, le={25}, d='5.05', lows={26: 97.0, 27: 96.0, 28: 90.0}, opens={28: 104.0},
                   highs={28: 109.0})
    (tr,) = acct(tape, rules={'AAAUSDT': R01})['counted']
    assert (tr['entry_price'], tr['stop_price']) == (99.9, 105.0) and tr['exit_ms'] == t_of(28)
    risk = 105.0 - 99.9
    assert tr['mfe_r'] == pytest.approx((99.9 - 96.0) / risk) and tr['mae_r'] == pytest.approx(-1.0)


test_s02_mae_01 = test_s02_mfe_01_and_mae_01


def test_s02_liq_01():
    w, g, flag = PR.liq_point(10_000.0, [(300.0, 100.0, 134.0)])        # gross 30,000 at entry, adverse 0.34
    assert w == pytest.approx(-200.0) and g == pytest.approx(40_200.0) and flag


def test_s02_liq_02():
    w, g, flag = PR.liq_point(10_000.0, [(300.0, 100.0, 120.0)])        # adverse 0.2
    assert w == pytest.approx(4_000.0) and g == pytest.approx(36_000.0) and flag   # 4,000 < 12,000 (high-based G/3)
    assert not PR.liq_point(10_000.0, [(30.0, 100.0, 101.0)])[2]        # an ordinary 1%-risk book is not flagged


def test_s02_delist_01():
    fill, ms, why = PR.delist_cover(notice_ms=t_of(30) + 5, opens=[(t_of(30), 99.0), (t_of(31), 97.0)],
                                    last_mark=95.0, bps=10.0, scen=BASE, tick=0.1)
    assert (ms, why) == (t_of(31), 'exit.delist') and fill == C.fill_price(97.0, 'buy', 10.0, BASE, tick=0.1)


def test_s02_delist_02():
    fill, ms, why = PR.delist_cover(notice_ms=None, opens=[(t_of(30), 99.0)], last_mark=95.0, bps=10.0, scen=BASE,
                                    tick=0.01)
    assert (ms, why) == (None, 'exit.delist_forced') and fill == C.fill_price(95.0, 'buy', 50.0, BASE, tick=0.01)
    assert fill > 95.0                                                   # 5x slip, adverse (above L) for a cover


def test_s02_repaint_01():
    base = _bars(1700)
    j = 1600
    mutated = base[:j + 1] + [P.Bar(b.open_ms, b.open * 3, b.high * 3, b.low * 3, b.close * 3, 1, 1, b.available_ms)
                              for b in base[j + 1:]]
    a, b = PE.Tape(side='short').decide(_View(base, j)), PE.Tape(side='short').decide(_View(mutated, j))
    assert a == b                                                       # the decision at i never sees bars after i
    tape = mk_tape(FLAT, le={25}, lx={30})
    tape2 = mk_tape(FLAT, le={25}, lx={30}, opens={27: 101.0, 28: 99.0})
    assert acct(tape)['counted'][0]['entry_price'] == acct(tape2)['counted'][0]['entry_price']   # fill = open of i+1


def test_s02_repaint_02():
    base = _bars(1700)
    one, two = PE.Tape(side='short'), PE.Tape(side='short')
    a = [one.decide(_View(base[:1650], j)) for j in range(1550, 1650)]
    b = [two.decide(_View(base, j)) for j in range(1550, 1700)][:100]
    assert a == b                                                       # appending bars changes no earlier decision


SPEC_V2_IDS = (
    'S02-B0-01', 'S02-BOOK-01', 'S02-CAP-01', 'S02-CAP-02-SPLITEND', 'S02-CROSS-01', 'S02-CROSS-02', 'S02-CROSS-03',
    'S02-DELIST-01', 'S02-DELIST-02', 'S02-ENTRY-01-STRICT', 'S02-ENTRY-02-STRICT', 'S02-ENTRYFILL-01', 'S02-EXIT-01',
    'S02-FEE-01', 'S02-FUND-01-POS', 'S02-FUND-02-NEG', 'S02-FUND-03-BOUNDARY', 'S02-FUND-04-X2',
    'S02-FUND-05-STOPBAR', 'S02-FUND-06-MISSINGMARK', 'S02-GAP-01', 'S02-GAP-02-EQ', 'S02-GROSS-01', 'S02-HALT-01',
    'S02-INTRA-01', 'S02-INTRA-02', 'S02-LIQ-01', 'S02-LIQ-02', 'S02-MAE-01', 'S02-MFE-01', 'S02-ORDER-01',
    'S02-ORDER-02', 'S02-REPAINT-01', 'S02-REPAINT-02', 'S02-RND-01-SELL', 'S02-RND-02-COVER', 'S02-RND-03-ONTICK',
    'S02-SIZE-01', 'S02-SIZE-02-FLOOR', 'S02-SIZE-03-MINQTY', 'S02-SIZE-04-MINNOTIONAL-FILL', 'S02-SIZE-05-ZERO-RISK',
    'S02-SIZE-06-NONFINITE', 'S02-STOP-01', 'S02-STOP-02-CEIL', 'S02-WINDOW-01-PREFIX', 'S02-WINDOW-02-TOL',
    'S02-WINDOW-03-EARLY')


def test_x_every_spec_v2_case_id_has_a_named_test():
    assert len(SPEC_V2_IDS) == len(set(SPEC_V2_IDS)) == 48
    names = [n for n in globals() if n.startswith('test_s02_')]
    norm = lambda i: 'test_' + i.lower().replace('-', '_')
    missing = [i for i in SPEC_V2_IDS if not any(n.startswith(norm(i)) for n in names)]
    assert missing == []
