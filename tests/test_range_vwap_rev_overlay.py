"""REX-01 synthetic fixtures F11 (SCOUT-EXIT-01 overlay isolation, TAPE-LEVEL R). Generated tapes only."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rex_synth as S                                                                       # noqa: E402
from rex_synth import ms, rex                                                               # noqa: E402

RV = S.RV
LE = S.long_entry(5)
F11 = ['hole_after_x1_exit', 'gross_overlap', 'runner_censored', 'incomplete_share_gt_5pct', 'r_below_m1_and_label']
HIGH = {i: 104.0 for i in range(12, 40)}


def _pair(tr, status='COMPLETE', r=0.5):
    return {'status': status, 'symbol': tr['symbol'], 'entry_ms': tr['entry_ms'], 'r': r, 'exit_ms': tr['exit_ms']}


@rex('F11', F11, sides=True)
def test_f11_overlay_isolation(side, case):
    if case == 'hole_after_x1_exit':                 # the runner touches a hole after X1 exited: pair INCOMPLETE only
        nv = RV.NoticeView({'AAA': ms(14) + S.HOUR})
        clean = {'AAA': S.build(n=20, side=side, vwap=LE, close=HIGH)}
        holed = {'AAA': S.build(n=20, side=side, vwap=LE, close=HIGH, holes=(14,))}
        a, b = (S.run(t, side, 20, notices=nv) for t in (clean, holed))
        assert a == b and b['status'] == RV.COMPLETE                         # X1 unchanged by the hole
        (t,) = b['trades']
        assert t['exit_ms'] == ms(13)
        p = RV.overlay(holed, RV.PRIMARY, side, t, 'x3', end=ms(20), notices=nv)
        assert (p['status'], p['reason'], p['first_unaccountable_ms']) == (RV.INCOMPLETE, 'incomplete.gap', ms(14))
    elif case == 'gross_overlap':                    # the X3 tape keeps running past X1's next entry: counted
        vw = dict(LE)
        vw.update({b: round(103 + 0.33 * (b - 13), 2) for b in range(14, 30)})
        vw[30] = 107.9
        tape = {'AAA': S.build(n=40, side=side, vwap=vw, close={**HIGH, 33: 108.0})}
        r = S.run(tape, side, 40)
        assert r['status'] == RV.COMPLETE and [t['entry_ms'] for t in r['trades']] == [ms(6), ms(31)]
        p = RV.overlay(tape, RV.PRIMARY, side, r['trades'][0], 'x3', end=ms(40))
        assert p['status'] == RV.COMPLETE and p['exit_ms'] > ms(31)
        cmp = RV.compare(r, {'x3': [p]}, side)
        assert cmp['x3']['gross_overlap'] == 1 and cmp['gating'] is False
    elif case == 'runner_censored':                  # the runner is still open at the declared end: censored pair
        tape = {'AAA': S.build(n=20, side=side, vwap=LE, close=HIGH)}
        r = S.run(tape, side, 20)
        assert r['status'] == RV.COMPLETE
        p = RV.overlay(tape, RV.PRIMARY, side, r['trades'][0], 'x2', end=ms(20))
        assert (p['status'], p['reason']) == (RV.INCOMPLETE, 'incomplete.split_end')
    elif case == 'incomplete_share_gt_5pct':
        trades = [{'symbol': f'S{i}', 'entry_ms': ms(i), 'exit_ms': ms(i + 1), 'r': 0.1} for i in range(20)]
        x1 = {'status': RV.COMPLETE, 'trades': trades}
        one = [_pair(t, RV.INCOMPLETE if i == 0 else RV.COMPLETE) for i, t in enumerate(trades)]
        two = [_pair(t, RV.INCOMPLETE if i < 2 else RV.COMPLETE) for i, t in enumerate(trades)]
        assert RV.compare(x1, {'x2': one}, side)['x2']['status'] == 'EVALUABLE'          # 5% exactly
        out = RV.compare(x1, {'x2': two}, side)['x2']
        assert out['status'] == 'NOT-EVALUABLE' and out['paired_mean_diff'] is None
    else:
        tape = {'AAA': S.build(n=12, side=side, vwap=LE, ohlc={8: (96.5, 98, 95, 97.5)})}
        r = S.run(tape, side, 12)
        t = r['trades'][0]
        pairs = {a: [RV.overlay(tape, RV.PRIMARY, side, t, a, end=ms(12))] for a in ('x2', 'x3')}
        cmp = RV.compare(dict(r, trades=[t], status=RV.COMPLETE), pairs, side)
        assert cmp['label'] == RV.TAPE_LABEL == 'TAPE-LEVEL R - not book-level, not deployable'
        assert cmp['x1']['r_lt_m1'] == 1 and cmp['x1']['r_lt_m1_share'] == 1.0
        assert cmp['x2']['r_lt_m1'] == cmp['x3']['r_lt_m1'] == 1
        assert all(p[0]['label'] == RV.TAPE_LABEL for p in pairs.values())
