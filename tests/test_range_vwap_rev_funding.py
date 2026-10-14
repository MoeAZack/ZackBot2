"""REX-01 synthetic fixtures F5: `funding-window/2` (family-local; STRAT-01/02 keep the legacy window). Every case is
mirrored (the short tape flips the funding sign, so 'adverse' stays adverse). unit = |rate| x qty x mark."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rex_synth as S                                                                       # noqa: E402
from rex_synth import ms, rex                                                               # noqa: E402

RV = S.RV
P = S.P
LE = S.long_entry(5)
Q, Q2 = 33.222, 16.611
RATE = 1e-4
UNIT = RATE * Q * 100.0
X1 = dict(vwap=LE, close={12: 104.0})                # entry open ms(6) = H + 24h, mean exit at the open ms(13) = H + 52h
F5 = ['entry_eq_stop_T_at_bo_4h', 'entry_eq_stop_interior_1h', 'x2_runner_stop_in_partial_bar', 'rate_sign',
      'cadence_4h', 'cadence_8h', 'T_eq_partial_both_slices', 'missing_rate', 'missing_mark',
      'T_open_plus14_at_entry_not_charged', 'T_at_signal_exit_open_charged', 'gap_stop_at_funding_open_charged',
      'intrabar_1h_stop_adverse_only', 'held_through_2h_all_charged', 'off_grid_stamp', 'proof_anchor_at_entry',
      'proof_exit_open_covered']


def run1(side, n, **kw):
    tape = {'AAA': S.build(n=n, side=side, **kw)}
    return tape, S.run(tape, side, n)


def trade(side, n, **kw):
    tape, r = run1(side, n, **kw)
    assert r['status'] == RV.COMPLETE, r.get('censor')
    (t,) = r['trades']
    return tape, r, t


def x2(tape, side, r, n):
    return RV.overlay(tape, RV.PRIMARY, side, r['trades'][0], 'x2', end=ms(n))


@rex('F5', F5, sides=True)
def test_f5_funding_window_2(side, case):
    if case == 'entry_eq_stop_T_at_bo_4h':           # entry at a 4h funding open, stopped in that bar: not charged
        tape, r, t = trade(side, 10, vwap=LE, fund_iv=4, fund_off=14, ohlc={6: (100, 101, 97.0, 99)})
        assert t['exit_ms'] == ms(6) and t['funding'] == 0.0
        assert any(f.time_ms == ms(6) + 14 for f in tape['AAA'].funding)
    elif case == 'entry_eq_stop_interior_1h':        # interior 1h events: adverse charged, favourable skipped
        _, _, a = trade(side, 10, vwap=LE, fund_iv=1, ohlc={6: (100, 101, 97.0, 99)})
        _, _, f = trade(side, 10, vwap=LE, fund_iv=1, fund_rate=-RATE, ohlc={6: (100, 101, 97.0, 99)})
        assert a['funding'] == pytest.approx(3 * UNIT) and f['funding'] == 0.0
    elif case == 'x2_runner_stop_in_partial_bar':    # T at the partial open charged (bo > entry), interior adverse
        tape, r = run1(side, 12, vwap=LE, fund_iv=1, close={7: 103.5}, ohlc={8: (103.5, 104, 99.5, 101)})
        p = x2(tape, side, r, 12)
        assert [s['reason'] for s in p['slices']] == ['exit.partial', 'exit.breakeven']
        assert p['funding'] == pytest.approx(RATE * 100 * (8 * Q + 3 * Q2))
    elif case == 'rate_sign':                        # the side pays an adverse rate and receives a favourable one
        _, _, a = trade(side, 16, fund_iv=1, **X1)
        _, _, f = trade(side, 16, fund_iv=1, fund_rate=-RATE, **X1)
        assert a['funding'] == pytest.approx(28 * UNIT) and f['funding'] == pytest.approx(-28 * UNIT)
    elif case in ('cadence_4h', 'cadence_8h'):
        iv, k = (4, 7) if case == 'cadence_4h' else (8, 3)
        _, _, t = trade(side, 16, fund_iv=iv, **X1)
        assert t['funding'] == pytest.approx(k * UNIT)
    elif case == 'T_eq_partial_both_slices':         # the partial fills at a funding open: q1 and q2 both pay it
        tape, r = run1(side, 12, vwap=LE, close={7: 103.5},
                       ohlc={8: (103.5, 104, 101, 102), 9: (102, 102.5, 99.5, 100)})
        p = x2(tape, side, r, 12)
        assert [(s['reason'], s['exit_ms']) for s in p['slices']] == [('exit.partial', ms(8)),
                                                                      ('exit.breakeven', ms(9))]
        assert ms(8) % (8 * S.HOUR) == 0 and p['funding'] == pytest.approx(UNIT)
    elif case in ('missing_rate', 'missing_mark'):
        tape = {'AAA': S.build(n=16, side=side, **X1)}
        x = tape['AAA']
        if case == 'missing_rate':
            x.funding = tuple(f for f in x.funding if f.time_ms != S.H + 40 * S.HOUR)
        else:
            del x.marks[S.H + 39 * S.HOUR]
        r = S.run(tape, side, 16)
        assert r['status'] == RV.INCOMPLETE and r['trades'] == []
        assert r['censor']['reason'] == ('incomplete.funding' if case == 'missing_rate' else 'incomplete.funding_mark')
    elif case == 'T_open_plus14_at_entry_not_charged':
        tape, _, t = trade(side, 16, fund_iv=4, fund_off=14, **X1)
        assert t['funding'] == pytest.approx(7 * UNIT)                  # (entry, exit]: 7 events, not the one at entry
        assert len(RV.legacy_window(tape['AAA'], t['entry_ms'], t['exit_ms'])) == 7
        assert RV.legacy_window(tape['AAA'], t['entry_ms'], t['exit_ms'])[0][0] == ms(6) + 14   # legacy pays entry T
    elif case == 'T_at_signal_exit_open_charged':
        tape, _, t = trade(side, 16, vwap=LE, close={13: 104.0})
        assert t['exit_ms'] == ms(14) == S.H + 56 * S.HOUR and t['funding'] == pytest.approx(4 * UNIT)
        leg = RV.legacy_window(tape['AAA'], t['entry_ms'], t['exit_ms'])
        assert [x[0] for x in leg] == [S.H + h * S.HOUR for h in (24, 32, 40, 48)]          # legacy misses exit T
    elif case == 'gap_stop_at_funding_open_charged':
        _, r = run1(side, 12, vwap=LE, ohlc={8: (96.5, 98, 95, 97.5)})
        t = r['trades'][0]
        assert t['gap'] and t['exit_ms'] == ms(8) == S.H + 32 * S.HOUR and t['funding'] == pytest.approx(UNIT)
    elif case == 'intrabar_1h_stop_adverse_only':
        _, _, a = trade(side, 12, vwap=LE, fund_iv=1, ohlc={8: (100, 101, 97.0, 99)})
        _, _, f = trade(side, 12, vwap=LE, fund_iv=1, fund_rate=-RATE, ohlc={8: (100, 101, 97.0, 99)})
        assert a['funding'] == pytest.approx(11 * UNIT) and f['funding'] == pytest.approx(-8 * UNIT)
    elif case == 'held_through_2h_all_charged':
        _, _, t = trade(side, 16, fund_iv=2, fund_rate=-RATE, **X1)
        assert t['funding'] == pytest.approx(-14 * UNIT)                # favourable events of held bars are charged
    elif case == 'off_grid_stamp':
        for extra in (P.Funding(S.H + 30 * S.HOUR, RATE, 8, 0), P.Funding(S.H + 40 * S.HOUR + 120_000, RATE, 8, 0)):
            tape = {'AAA': S.build(n=16, side=side, **X1)}
            tape['AAA'].funding = tuple(sorted(tape['AAA'].funding + (extra,), key=lambda f: f.time_ms))
            r = S.run(tape, side, 16)
            assert r['status'] == RV.INCOMPLETE and r['censor']['reason'] == 'incomplete.funding'
    elif case == 'proof_anchor_at_entry':
        x = S.build(n=16, side=side, fund_off=14, **X1)
        e = ms(6)
        x.funding = tuple(f for f in x.funding if f.time_ms >= e)       # the event at entry is the first row
        assert [T for T, _ in RV.funding_events(x, e, e, e + 8 * S.HOUR, incl=True)] == [e + 8 * S.HOUR]
        x.funding = tuple(f for f in x.funding if f.time_ms > e + 14)   # no anchor at / before entry
        with pytest.raises(RV.Incomplete) as ex:
            RV.funding_events(x, e, e, e + 8 * S.HOUR, incl=True)
        assert ex.value.reason == 'incomplete.funding'
    else:                                            # T == exit open (+14 ms) is covered and paid; one short: refused
        kw = dict(vwap=LE, close={13: 104.0}, fund_off=14)
        tape = {'AAA': S.build(n=16, side=side, **kw)}
        x = tape['AAA']
        x.funding = tuple(f for f in x.funding if f.time_ms <= ms(14) + 14)
        r = S.run(tape, side, 15)
        assert r['status'] == RV.COMPLETE and r['trades'][0]['funding'] == pytest.approx(4 * UNIT)
        x.funding = tuple(f for f in x.funding if f.time_ms < ms(14))
        r = S.run(tape, side, 15)
        assert r['status'] == RV.INCOMPLETE and r['censor']['reason'] == 'incomplete.funding'
