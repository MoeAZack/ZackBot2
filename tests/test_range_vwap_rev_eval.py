"""REX-01 synthetic fixtures F9 (INCOMPLETE consumers), F12 (Cairo day / week keys), F13 (baselines and p
resolution), F14 (eligibility) and F15 (dates / access / multiplicity), plus the Codex split-end ruling 6169006548.
Generated tapes and documents only; no store, archive, holdout or market read."""
import calendar
import copy
import datetime as dt
import json
import os
import sys
import zoneinfo

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rex_synth as S                                                                       # noqa: E402
from rex_synth import ms, rex                                                               # noqa: E402

RV = S.RV
import ledger as L                                                                          # noqa: E402
import missing_data as MD                                                                   # noqa: E402
import pit_run as PR                                                                        # noqa: E402
import progress as PG                                                                       # noqa: E402
import r4_readiness as RD                                                                   # noqa: E402
import range_vwap_rev_eval as EV                                                            # noqa: E402
import report as RP                                                                         # noqa: E402
import run_r4 as G                                                                          # noqa: E402

LE = S.long_entry(5)
H = S.H
HI4 = {i: 104.0 for i in range(12, 16)}       # after the mean close every close sits above VWAP (out of bucket)
B2P = {-1: 103.0, 4: 103.0, 5: 105.0}                # B2: z_4 = -1.5 > -2, z_5 = -2.5 <= -2 (first close outside)


def tape1(side='long', n=12, sym='AAA', **kw):
    return {sym: S.build(n=n, side=side, **kw)}


def open_at_end(side='long'):
    tape = tape1(side, 12, vwap=LE)
    return tape, S.run(tape, side, 12)


def fake_book(rs, *, syms=None, weeks=None, status=RV.COMPLETE, inel=None):
    syms = syms or ['A', 'B', 'C', 'D']
    weeks = weeks or list(range(len(rs)))
    tr = [{'symbol': syms[i % len(syms)], 'entry_ms': H + weeks[i] * EV.WEEK + S.TF, 'r': r}
          for i, r in enumerate(rs)]
    doc = {'status': status, 'policy': RV.POLICY, 'trades': tr,
           'ineligible': inel or {'per_symbol': {s: 0.0 for s in syms}, 'book': 0.0}}
    if status != RV.COMPLETE:
        doc['incomplete'] = [{'reason': 'incomplete.gap', 'secondary': False}]
    return doc


def edge_inputs(r=0.5, n=52):
    base = fake_book([r] * n, weeks=list(range(52)) * (n // 52 + 1))
    return base, copy.deepcopy(base), {f'n{i}': copy.deepcopy(base) for i in range(8)}


# ------------------------------------------------------------------ F9 INCOMPLETE consumers (14)
F9 = ['runner_refuses', 'report_gate_refuses', 'readiness_refuses', 'check_tree_nested', 'split_end_censor',
      'b0_inherits', 'neighbour_inherits', 'incomplete_neighbour_is_a_fail', 'sealed_is_no_result_atomic_reveal',
      'fees_slip_x2_conjunction', 'packet_has_no_trades_or_metrics', 'void_monitor_ohlcv_invariant',
      'funding_gap_undetermined_not_void', 'reissue_adds_30']


@rex('F9', F9)
def test_f9_incomplete_consumers(case, tmp_path):
    tape, inc = open_at_end()
    assert inc['status'] == RV.INCOMPLETE
    if case == 'runner_refuses':
        with pytest.raises(PR.PitRunError):
            PR.require_complete(inc)
        with pytest.raises(MD.MissingDataError):
            MD.check(inc)
    elif case == 'report_gate_refuses':
        with pytest.raises(RP.ReportError, match='INCOMPLETE'):
            RP.refuse_incomplete(json.loads(json.dumps(inc)))
    elif case == 'readiness_refuses':
        ident = {'commit': 'a' * 40, 'evaluator': 'b' * 64, 'manifest': None, 'universe': None,
                 'classification': None, 'prereg': None}
        for doc, ok in ((inc, False), (S.run(tape1(n=16, vwap=LE, close={12: 104.0}), 'long', 16), True)):
            d = tmp_path / ('ok' if ok else 'bad')
            pg = PG.Progress(str(d), identity=ident, config={'rex': ok}, clock=None)
            f = d / 'x1.json'
            f.write_text(json.dumps(doc))
            pg.sealed([str(f)], ['x1.json'])
            if ok:
                RD.sealed_artifacts(pg.final_path)
            else:
                with pytest.raises(RD.ReadinessError, match='INCOMPLETE'):
                    RD.sealed_artifacts(pg.final_path)
    elif case == 'check_tree_nested':
        summary = {'status': RV.COMPLETE, 'missing_data_policy': RV.POLICY, 'x1': {'long': inc}}
        with pytest.raises(MD.MissingDataError):
            MD.check_tree(summary)
    elif case == 'split_end_censor':
        assert inc['censor']['reason'] == 'incomplete.split_end' and inc['incomplete'][0]['entry_ms'] == ms(6)
    elif case == 'b0_inherits':
        b = EV.b0(tape, RV.PRIMARY, 'long', inc, first=H, end=ms(12), R=3)
        assert b['status'] == RV.INCOMPLETE and b['inherited']
    elif case == 'neighbour_inherits':
        holed = tape1(n=16, holes=(9,))
        for cfg in RV.neighbours():
            assert S.run(holed, 'long', 16, cfg=cfg)['status'] == RV.INCOMPLETE
    elif case == 'incomplete_neighbour_is_a_fail':
        base, x2, nbs = edge_inputs()
        nbs['n0'] = dict(nbs['n0'], status=RV.INCOMPLETE)
        one = EV.edge00(side='long', H=H, base=base, x2=x2, holm_p={'B0': 1e-4, 'B1': 1e-4, 'B2': 1e-4},
                        neighbours=nbs)
        assert one['neighbours_kept'] == 7 and one['rows']['neighbours'] and one['verdict'] == 'PASS'
        nbs['n1'] = fake_book([-0.5] * 52, weeks=list(range(52)))
        two = EV.edge00(side='long', H=H, base=base, x2=x2, holm_p={'B0': 1e-4, 'B1': 1e-4, 'B2': 1e-4},
                        neighbours=nbs)
        assert two['neighbours_kept'] == 6 and not two['rows']['neighbours'] and two['verdict'] == 'PARK'
    elif case == 'sealed_is_no_result_atomic_reveal':
        dg = {'r_ext': 'e' * 64, 'binding': 'f' * 64}
        rv = EV.Reveal(H)
        with pytest.raises(EV.EvalError, match='interim'):
            rv.reveal(H + EV.HOLDOUT_MS - 1, inc, lambda: None, dg)
        called = []
        out = rv.reveal(H + EV.HOLDOUT_MS, inc, lambda: called.append(1), dg)
        assert out['verdict'] == EV.NO_RESULT and out['core20'] is None and called == []
        assert out['core10']['verdict'] == EV.NO_RESULT
        with pytest.raises(EV.EvalError, match='once'):
            rv.reveal(H + EV.HOLDOUT_MS, inc, lambda: None, dg)
        ok = {'status': RV.COMPLETE, 'policy': RV.POLICY}
        out = EV.Reveal(H).reveal(H + EV.HOLDOUT_MS, ok, lambda: dict(ok, verdict='PARK'), dg)
        assert out['verdict'] == 'PARK' and out['core10'] == ok
        out = EV.Reveal(H).reveal(H + EV.HOLDOUT_MS, ok, lambda: inc, dg)
        assert out['verdict'] == EV.NO_RESULT and out['core20']['verdict'] == EV.NO_RESULT
    elif case == 'fees_slip_x2_conjunction':
        base, x2, nbs = edge_inputs()
        hp = {'B0': 1e-4, 'B1': 1e-4, 'B2': 1e-4}
        assert EV.edge00(side='long', H=H, base=base, x2=x2, holm_p=hp, neighbours=nbs)['verdict'] == 'PASS'
        bad = EV.edge00(side='long', H=H, base=base, x2=fake_book([-0.1] * 52), holm_p=hp, neighbours=nbs)
        assert not bad['rows']['expectancy'] and bad['verdict'] == 'PARK'
        inc_x2 = EV.edge00(side='long', H=H, base=base, x2=dict(x2, status=RV.INCOMPLETE), holm_p=hp,
                           neighbours=nbs)
        assert inc_x2['verdict'] == EV.NO_RESULT
    elif case == 'packet_has_no_trades_or_metrics':
        pk = EV.sealed_incomplete(inc, {'r_ext': 'e' * 64})
        assert tuple(sorted(pk)) == tuple(sorted(EV.PACKET_KEYS)) and pk['verdict'] == EV.NO_RESULT
        txt = json.dumps(pk)
        assert all(k not in txt for k in ('trades', 'series', 'wallet', 'signals', 'refusals', 'mtm', '"r"'))
        assert (pk['class'], pk['first_unaccountable_ms']) == ('split_censoring', ms(12))
    elif case == 'void_monitor_ohlcv_invariant':
        a = tape1(n=16, holes=(9,))
        b = copy.deepcopy(a)
        for k, bar in list(b['AAA'].bars.items()):
            b['AAA'].bars[k] = bar._replace(open=1.0, high=9.0, low=0.5, close=3.0, volume=0.0, quote_volume=7.0)
        va, vb = (EV.void_monitor(EV.presence_of(t), first=H, end=ms(16)) for t in (a, b))
        assert va == vb and va['status'] == 'VOID' and va['holes'] == [('AAA', ms(9))]
        nv = RV.NoticeView({'AAA': ms(8)})
        assert EV.void_monitor(EV.presence_of(a), first=H, end=ms(16), notices=nv)['status'] == 'OK'
    elif case == 'funding_gap_undetermined_not_void':
        v = EV.void_monitor(EV.presence_of(tape1(n=16)), first=H, end=ms(16), funding_gaps=[('AAA', ms(9))],
                            mark_gaps=[('AAA', ms(10))])
        assert v['status'] == 'UNDETERMINED' and v['holes'] == []
    else:
        assert [EV.program_n(k) for k in range(3)] == [65, 95, 125]
        assert EV.check_basis({'trend_ema_mom': 18, 'trend_ema_mom_short': 17, RV.FAMILY: 60}, reissues=1) == 95
        with pytest.raises(EV.EvalError):
            EV.check_basis({'trend_ema_mom': 18, 'trend_ema_mom_short': 17, RV.FAMILY: 30}, reissues=1)


# ------------------------------------------------------------------ F12 Cairo day / week keys (4)
def _hours_of(date):
    y, m, d = map(int, date.split('-'))
    base = calendar.timegm((y, m, d, 0, 0, 0)) * 1000
    return sum(RV.E.cairo_day(base + h * S.HOUR) == date for h in range(-30, 54))


@rex('F12', ['day_25h', 'day_23h', 'table_equals_iana', 'week_boundaries'])
def test_f12_cairo_and_weeks(case):
    w = EV.windows(EV.H_RECOMMENDED)
    trs = [t for t, _ in RV.E.CAIRO_OFFSETS if w['holdout'][0] <= t < w['holdout'][1]]
    assert len(trs) == 2                             # one spring-forward, one fall-back inside [H, H + 364 d)
    if case in ('day_25h', 'day_23h'):
        offs = dict(RV.E.CAIRO_OFFSETS)
        t = next(x for x in trs if (offs[x] == 7200) == (case == 'day_25h'))
        local = RV.E.cairo_day(t - 1 if case == 'day_25h' else t)      # fall back repeats 23:00, spring skips 00:00
        assert _hours_of(local) == (25 if case == 'day_25h' else 23)
    elif case == 'table_equals_iana':
        z = zoneinfo.ZoneInfo('Africa/Cairo')
        for t in range(w['holdout'][0], w['holdout'][1], S.HOUR):
            assert RV.E.cairo_day(t) == dt.datetime.fromtimestamp(t / 1000, z).strftime('%Y-%m-%d')
    else:
        Hr = EV.H_RECOMMENDED
        assert w['weeks'][0] == (Hr, Hr + EV.WEEK) and len(w['weeks']) == 52 and len(w['blocks']) == 13
        for j in (0, 1, 51):
            assert EV.week_of(Hr + j * EV.WEEK, Hr) == j and EV.week_of(Hr + (j + 1) * EV.WEEK - 1, Hr) == j
            assert EV.is_monday_midnight(Hr + j * EV.WEEK)
        assert EV.WEEK // S.TF == 42 and w['holdout'] == (Hr, Hr + 364 * 86_400_000)


# ------------------------------------------------------------------ F13 baselines / p resolution (7 + 7, + 7)
F13_SIDE = ['b0_b2_under_x1_admission', 'b0_counts_fixed_first_seeded', 'b0_side_bucket_only',
            'b0_quintile_bins_empty_bin_shortfall', 'b0_mean_abs_z_match_else_park', 'b2_first_close_trigger',
            'weekly_r_mtm_units']
F13_GLOBAL = ['b0_min_p_below_first_step', 'min_attainable_p_unresolvable_park', 'k6_passes_k7_fails',
              'exact_at_m_le_24', 'sign_flip_seeds_reproducible', 'b1_sizing', 'b0_shortfall_cap_25pct_park']


def _ind(zp, z, side):
    s = 1 if side == 'long' else -1
    f = RV.fr
    return RV.Ind(f(100), f(101), f(101), f(101), f(1), f(1), f(s * z), f(s * zp), True, f(100))


@rex('F13', F13_SIDE, sides=True)
def test_f13_baselines_side(side, case):
    sg = 1 if side == 'long' else -1
    if case == 'b0_b2_under_x1_admission':
        five = {s: S.build(n=12, side=side, vwap=B2P) for s in 'ABCDE'}
        b2 = RV.run_book(five, RV.PRIMARY, side, first=H, end=ms(12), mode='b2')
        assert b2['refusals'] == {'book.max_positions': 1} and len(b2['open_lots']) == 4
        x1 = S.run({s: S.build(n=12, side=side, vwap=LE) for s in 'ABCDE'}, side, 12)
        assert {v['qty'] for v in b2['open_lots'].values()} == {v['qty'] for v in x1['open_lots'].values()}
        b0 = RV.run_book(five, RV.PRIMARY, side, first=H, end=ms(12), mode='b0', draws={(s, ms(6)) for s in 'ABCDE'})
        assert b0['refusals'] == {'book.max_positions': 1} and len(b0['open_lots']) == 4
    elif case == 'b0_counts_fixed_first_seeded':
        tape = tape1(side, 16, vwap=LE, close=HI4)
        x1 = S.run(tape, side, 16)
        before = copy.deepcopy(x1)
        a = EV.b0(tape, RV.PRIMARY, side, x1, first=H, end=ms(16), R=4)
        b = EV.b0(tape, RV.PRIMARY, side, x1, first=H, end=ms(16), R=4)
        assert a == b and x1 == before and a['status'] == RV.COMPLETE and a['R'] == 4
        zt = EV.z_table(tape, RV.PRIMARY, side, first=H, end=ms(16))
        seqs = [np.random.Generator(np.random.PCG64(s)) for s in np.random.SeedSequence(EV.SEED).spawn(4)]
        seqs2 = [np.random.Generator(np.random.PCG64(s)) for s in np.random.SeedSequence(20261026).spawn(4)]
        d1 = [EV.b0_draws(x1, zt, RV.PRIMARY, side, H=H, rng=g) for g in seqs]
        assert d1 == [EV.b0_draws(x1, zt, RV.PRIMARY, side, H=H, rng=g) for g in seqs2]
    elif case == 'b0_side_bucket_only':
        tape = tape1(side, 16, vwap=LE, close=HI4)
        x1 = S.run(tape, side, 16)
        zt = EV.z_table(tape, RV.PRIMARY, side, first=H, end=ms(16))
        assert any(sg * z >= 0 for z in zt.values())                      # out-of-bucket closes exist ...
        for seed in range(5):
            draws, _ = EV.b0_draws(x1, zt, RV.PRIMARY, side, H=H, rng=np.random.default_rng(seed))
            assert draws and all(-2 < sg * zt[d] < 0 for d in draws)       # ... and are never drawn
        nobucket = {k: abs(v) * sg for k, v in zt.items()}               # every close outside the bucket
        draws, short = EV.b0_draws(x1, nobucket, RV.PRIMARY, side, H=H, rng=np.random.default_rng(0))
        assert draws == set() and short == len(x1['signals']) == 1
    elif case == 'b0_quintile_bins_empty_bin_shortfall':
        zs = [-1.9, -1.5, -1.2, -0.8, -0.3]
        x1 = {'trades': [{'z': sg * z} for z in zs], 'open_lots': {},
              'signals': [('A', H + i * S.TF, sg * z) for i, z in enumerate(zs)]}
        edges = EV.b0_bins(x1)
        assert len(edges) == 4 and [EV.bin_of(sg * z, edges) for z in zs] == ([0, 1, 2, 3, 4] if sg > 0
                                                                               else [4, 3, 2, 1, 0])
        zt = {('A', H + 20 * S.TF + i * S.TF): sg * z for i, z in enumerate([-1.85, -1.45, -1.15, -0.75])}
        draws, short = EV.b0_draws(x1, zt, RV.PRIMARY, side, H=H, rng=np.random.default_rng(1))
        assert len(draws) == 4 and short == 1                              # the top bin has no close: shortfall
        assert sorted(EV.bin_of(zt[d], edges) for d in draws) == sorted(EV.bin_of(sg * z, edges) for z in zs[:4])
    elif case == 'b0_mean_abs_z_match_else_park':
        assert EV.b0_park(1.5, 1.6, 0.0) == [] and EV.b0_park(1.5, 1.4, 0.0) == []
        assert EV.b0_park(1.5, 1.61, 0.0) == ['B0-UNMATCHED'] and EV.b0_park(1.5, None, 0.0) == ['B0-UNMATCHED']
        tape = tape1(side, 16, vwap=LE, close=HI4)
        res = EV.b0(tape, RV.PRIMARY, side, S.run(tape, side, 16), first=H, end=ms(16), R=3)
        assert res['row'] == ('PARK' if res['park'] else 'TESTED')
        assert (abs(res['b0_mean_abs_z'] - res['x1_mean_abs_z']) > 0.1) == ('B0-UNMATCHED' in res['park'])
    elif case == 'b2_first_close_trigger':
        cfg = RV.PRIMARY
        assert RV.entry_b2(cfg, _ind(-1.9, -2.0, side), side)            # first close at / outside the band
        assert not RV.entry_b2(cfg, _ind(-2.0, -2.5, side), side)        # already outside: not the first
        assert not RV.entry_b2(cfg, _ind(-1.9, -1.99, side), side)
        r = RV.run_book(tape1(side, 12, vwap=B2P), cfg, side, first=H, end=ms(12), mode='b2')
        assert [x[:2] for x in r['signals']] == [('AAA', ms(6))]
    else:
        series = [(H, 10_000.0), (H + EV.WEEK, 10_100.0), (H + 2 * EV.WEEK, 9_898.0)]
        assert EV.weekly_r(series, H, H + 2 * EV.WEEK) == {0: pytest.approx(1.0), 1: pytest.approx(-2.0)}
        tape = tape1(side, 84, vwap=LE)
        b1 = EV.b1_book(tape, side, first=H, end=ms(84))
        x1 = S.run(tape1(side, 84, vwap=LE, close={12: 104.0}), side, 84)
        b2 = RV.run_book(tape1(side, 84, vwap=B2P), RV.PRIMARY, side, first=H, end=ms(84), mode='b2')
        for doc in (b1, x1, b2):
            wr = EV.weekly_r(doc['series'], H, ms(84))
            eq = dict(doc['series'])
            assert list(wr) == [0, 1]
            assert wr[1] == pytest.approx((eq[ms(84)] - eq[ms(42)]) / (0.01 * eq[ms(42)]))


@rex('F13', F13_GLOBAL)
def test_f13_p_resolution(case):
    first = EV.ALPHA / EV.PROGRAM_N
    if case == 'b0_min_p_below_first_step':
        assert EV.mc_p(0) == 1 / 10_001 < first and EV.R_B0 == 10_000
    elif case == 'min_attainable_p_unresolvable_park':
        assert not EV.resolvable(EV.min_attainable_p('exact', 10)) and EV.resolvable(EV.min_attainable_p('exact', 11))
        assert not EV.resolvable(EV.min_attainable_p('mc', R=1000))
    elif case == 'k6_passes_k7_fails':
        assert EV.mc_p(6) <= first < EV.mc_p(7)
        hp = EV.holm({'B0': EV.mc_p(6), 'B1': EV.mc_p(7)}, EV.PROGRAM_N)
        assert hp['B0'] < 0.05 <= hp['B1']
    elif case == 'exact_at_m_le_24':
        d24 = [0.1] * 24 + [0.0] * 3
        r = EV.sign_flip(d24, seed=EV.SEED_B1)
        assert (r['method'], r['m'], r['p']) == ('exact', 24, 2.0 ** -24)
        assert EV.sign_flip([0.1] * 25, seed=EV.SEED_B1)['method'] == 'mc'
        r = EV.sign_flip([1.0, -1.0, 2.0], seed=EV.SEED_B1)          # sums >= 2: (+,+,+) (+,-,+) (-,-,+)... exact
        assert r['p'] == 3 / 8
    elif case == 'sign_flip_seeds_reproducible':
        d = list(np.linspace(-0.5, 1.0, 40))
        a, b = EV.sign_flip(d, seed=EV.SEED_B1), EV.sign_flip(d, seed=[20261026, 1])
        assert a == b and a['method'] == 'mc' and EV.sign_flip(d, seed=EV.SEED_B2)['p'] is not None
        assert (EV.SEED_B1, EV.SEED_B2, EV.SEED) == ([20261026, 1], [20261026, 2], 20261026)
    elif case == 'b1_sizing':
        rules = {'A': S.RULE, 'B': S.RULE}
        q = EV.b1_targets(10_000.0, {'A': RV.fr(2), 'B': RV.fr(2)}, {'A': 100.0, 'B': 100.0}, rules)
        assert q == {'A': 16.666, 'B': 16.666}                          # (1% x E / N) / (1.5 x TR-SMA14), floored
        q = EV.b1_targets(10_000.0, {'A': RV.fr('0.01'), 'B': RV.fr('0.01')}, {'A': 100.0, 'B': 100.0}, rules)
        assert q == {'A': 136.363, 'B': 136.363}                        # scaled to gross 3x BEFORE flooring
        assert sum(v * 100 * 1.1 for v in q.values()) <= 30_000
    else:
        assert EV.b0_park(1.5, 1.5, 0.25) == [] and EV.b0_park(1.5, 1.5, 0.2501) == ['B0-SHORTFALL']


# ------------------------------------------------------------------ F14 eligibility (6)
F14 = ['series_start_within_w_not_eligible', 'notice_known_no_entry', 'notice_exited_later_holes_ignored',
       'exchange_halt_without_notice_is_a_hole', 'ineligible_share_reported', 'share_above_cap_not_evaluable']


@rex('F14', F14)
def test_f14_eligibility(case):
    if case == 'series_start_within_w_not_eligible':
        r = S.run(tape1(n=12, pre=50, vwap=LE), 'long', 12)
        assert r['signals'] == [] and r['status'] == RV.COMPLETE
        assert r['ineligible']['per_symbol']['AAA'] == pytest.approx(10 / 12)    # eligible from H + 40 h
    elif case == 'notice_known_no_entry':
        r = S.run(tape1(n=12, vwap=LE), 'long', 12, notices=RV.NoticeView({'AAA': ms(3)}))
        assert r['signals'] == [] and r['status'] == RV.COMPLETE
    elif case == 'notice_exited_later_holes_ignored':
        r = S.run(tape1(n=20, vwap=LE, holes=(11, 12, 15)), 'long', 20, notices=RV.NoticeView({'AAA': ms(8) + 1}))
        assert r['status'] == RV.COMPLETE and r['trades'][0]['reason'] == 'exit.delist'
    elif case == 'exchange_halt_without_notice_is_a_hole':
        r = S.run(tape1(n=20, holes=(8, 9, 10)), 'long', 20)
        assert r['status'] == RV.INCOMPLETE and r['censor']['reason'] == 'incomplete.gap'
    elif case == 'ineligible_share_reported':
        tape = {'A': S.build(n=20), 'B': S.build(n=20)}
        r = S.run(tape, 'long', 20, notices=RV.NoticeView({'B': ms(10)}))
        assert r['ineligible'] == {'per_symbol': {'A': 0.0, 'B': 0.5}, 'book': 0.25, 'cycles': 20}
    else:
        assert EV.ineligibility({'A': 0.25, 'B': 0.0}, 0.10)['status'] == 'EVALUABLE'
        assert EV.ineligibility({'A': 0.26, 'B': 0.0}, 0.05)['status'] == 'NOT-EVALUABLE'
        assert EV.ineligibility({'A': 0.2, 'B': 0.2}, 0.2)['status'] == 'NOT-EVALUABLE'
        base, x2, nbs = edge_inputs()
        base['ineligible'] = {'per_symbol': {'A': 0.3, 'B': 0, 'C': 0, 'D': 0}, 'book': 0.075}
        e = EV.edge00(side='long', H=H, base=base, x2=x2, holm_p={'B0': 1e-4, 'B1': 1e-4, 'B2': 1e-4},
                      neighbours=nbs)
        assert e['verdict'] == 'PARK' and e['ineligibility']['over_symbol_cap'] == ['A']


# ------------------------------------------------------------------ F15 dates / access / multiplicity (10)
F15 = ['h_shift_shifts_every_window', 'never_read_overlap_refused', 'forward_window_mismatch_refused',
       'n12_first_decision_reads_warmup', 'forward_record_passes_unchanged_ledger', 'read_at_or_after_h_refused',
       'strat_multiplicity_still_35', 'range_register_frozen_65', 'strat_n_40_keeps_frozen_65',
       'seeds_identical_when_h_shifts']


def _rec(H0, a, b, split='forward'):
    return {'kind': 'data_access', 'split': split, 'window': {'start': EV.iso(a), 'end': EV.iso(b)}}


@rex('F15', F15)
def test_f15_dates_access(case, tmp_path):
    H0 = EV.H_RECOMMENDED
    reg = H0 - 20 * 86_400_000
    wa, wb = EV.windows(H0)['warmup']
    if case == 'h_shift_shifts_every_window':
        a, b = EV.windows(H0), EV.windows(H0 + EV.WEEK)
        for k in ('warmup', 'holdout'):
            assert tuple(x + EV.WEEK for x in a[k]) == b[k]
        assert b['never_read'] == (EV.S_END, a['never_read'][1] + EV.WEEK)          # S_END is the only literal
        assert [tuple(x + EV.WEEK for x in w) for w in a['weeks']] == b['weeks']
        assert EV.slide_h(H0, wa - 1) == H0 and EV.slide_h(H0, wa) == H0 + EV.WEEK
        assert EV.iso(H0) == '2026-11-16T00:00:00Z' and EV.iso(wa) == '2026-11-06T00:00:00Z'
    elif case == 'never_read_overlap_refused':
        with pytest.raises(EV.EvalError, match='never-read'):
            EV.check_access([_rec(H0, wa - S.TF, wb)], H=H0, registration_ms=reg)
        with pytest.raises(EV.EvalError, match='never-read'):
            EV.check_access([_rec(H0, EV.S_END, EV.S_END + S.TF, 'development')], H=H0, registration_ms=reg - 10**11)
    elif case == 'forward_window_mismatch_refused':
        EV.check_access([_rec(H0, wa, wb)], H=H0, registration_ms=reg)
        with pytest.raises(EV.EvalError, match='exactly'):
            EV.check_access([_rec(H0, wa + S.TF, wb)], H=H0, registration_ms=reg)
    elif case == 'n12_first_decision_reads_warmup':
        nb = next(c for c in RV.neighbours() if c.n == RV.N + 12)
        assert EV.first_read(H0, nb) == (wa, wb) and EV.first_read(H0, RV.PRIMARY)[0] > wa
    elif case == 'forward_record_passes_unchanged_ledger':
        rec = EV.warmup_record(H0, {'klines': 'a' * 64})
        p = str(tmp_path / f'{RV.FAMILY}.jsonl')
        L.append(p, kind=rec['kind'], candidate_id=rec['candidate_id'], split=rec['split'], window=rec['window'],
                 author='t', cairo_date='2026-10-14', detail=rec['detail'], lineage='root')
        recs = L._check_state(p)[RV.FAMILY]
        assert recs[0]['split'] == 'forward' and L.n_trials(recs) == 0
        EV.check_access(recs, H=H0, registration_ms=reg)
    elif case == 'read_at_or_after_h_refused':
        with pytest.raises(EV.EvalError, match='before the reveal'):
            EV.check_access([_rec(H0, wa, wb), _rec(H0, H0, H0 + S.TF, 'holdout')], H=H0, registration_ms=reg)
        EV.check_access([_rec(H0, wa, wb), _rec(H0, H0, H0 + S.TF, 'holdout')], H=H0, registration_ms=reg,
                        revealed=True)
        with pytest.raises(EV.EvalError, match='registration at or after H'):
            EV.check_access([], H=H0, registration_ms=H0)
        with pytest.raises(EV.EvalError, match='first read'):
            EV.check_access([_rec(H0, wa, wb)], H=H0, registration_ms=wa)
    elif case == 'strat_multiplicity_still_35':
        fams = {'trend_ema_mom': [{'kind': 'variant'}] * 18, 'trend_ema_mom_short': [{'kind': 'grid_point'}] * 17,
                RV.FAMILY: [{'kind': 'baseline'}] * 30 + [{'kind': 'data_access'}]}
        assert G.multiplicity(fams)['program'] == 35 and RV.FAMILY not in G.PROGRAM_FAMILIES
    elif case == 'range_register_frozen_65':
        assert EV.check_basis({'trend_ema_mom': 18, 'trend_ema_mom_short': 17, RV.FAMILY: 30}) == 65
        assert EV.PROGRAM_BASIS == {'trend_ema_mom': 18, 'trend_ema_mom_short': 17, RV.FAMILY: 30}
        assert EV.TRIALS_PER_SIDE == 15 and 1 + len(RV.neighbours()) + 2 + 4 == 15
        with pytest.raises(EV.EvalError):
            EV.check_basis({'trend_ema_mom': 18, 'trend_ema_mom_short': 17, RV.FAMILY: 29})
    elif case == 'strat_n_40_keeps_frozen_65':
        assert EV.check_basis({'trend_ema_mom': 23, 'trend_ema_mom_short': 17, RV.FAMILY: 30}) == 65
        hp = EV.holm({'B0': 7e-4}, EV.check_basis({'trend_ema_mom': 23, 'trend_ema_mom_short': 17, RV.FAMILY: 30}))
        assert hp['B0'] == pytest.approx(65 * 7e-4)
    else:
        zt = {('A', H0 + i * S.TF): -1.5 for i in range(10)}
        x1 = {'trades': [{'z': -1.5}], 'open_lots': {}, 'signals': [('A', H0 + 2 * S.TF, -1.5)]}
        sh = lambda d: {(k[0], k[1] + EV.WEEK): v for k, v in d.items()}                          # noqa: E731
        x1s = dict(x1, signals=[('A', H0 + EV.WEEK + 2 * S.TF, -1.5)])
        for ss in np.random.SeedSequence(EV.SEED).spawn(3):
            d0, _ = EV.b0_draws(x1, zt, RV.PRIMARY, 'long', H=H0, rng=np.random.Generator(np.random.PCG64(ss)))
            d1, _ = EV.b0_draws(x1s, sh(zt), RV.PRIMARY, 'long', H=H0 + EV.WEEK,
                                rng=np.random.Generator(np.random.PCG64(ss)))
            assert {(s, t + EV.WEEK) for s, t in d0} == d1


# ------------------------------------------------------------------ Codex ruling 6169006548 (split end)
def test_ruling_6169006548_split_end_is_incomplete_no_result():
    """A lot open at the declared end, with no causal in-window exit, is incomplete.split_end -> INCOMPLETE /
    NO-RESULT. Refused alternatives: (1) synthesize a close, (2) carry the lot past the boundary, (3) use a later
    mark, (4) exclude the trade."""
    for side in RV.SIDES:
        tape, r = open_at_end(side)
        (row,) = r['incomplete']
        assert r['status'] == RV.INCOMPLETE and r['censor']['reason'] == 'incomplete.split_end'
        assert r['trades'] == []                                               # (1) no synthesized close
        assert (row['symbol'], row['entry_ms'], row['phase']) == ('AAA', ms(6), 'split_end')   # (4) not excluded
        assert r['open_lots']['AAA']['entry_ms'] == ms(6)
        later = {'AAA': S.build(n=40, side=side, vwap=LE, close={12: 104.0, 13: 104.0})}
        for t in range(ms(12), ms(40), S.HOUR):
            later['AAA'].marks[t] = 150.0                                      # (3) later marks are never read
        assert S.run(later, side, 12) == r                                     # (2) later bars change nothing
        pend = {'AAA': S.build(n=14, side=side, vwap=LE, close={11: 104.0})}
        rp = S.run(pend, side, 12)                                             # exit decided AT the end: its open is
        assert rp['status'] == RV.INCOMPLETE and rp['trades'] == []            # outside [H, end) -> still split_end
        assert EV.sealed_incomplete(r, {})['verdict'] == EV.NO_RESULT
        base, x2, nbs = edge_inputs()
        assert EV.edge00(side=side, H=H, base=r, x2=x2, holm_p={}, neighbours=nbs)['verdict'] == EV.NO_RESULT
        with pytest.raises(MD.MissingDataError):
            MD.check(r)
