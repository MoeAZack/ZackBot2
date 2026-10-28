"""EDGE05 low-RAM runner: synthetic in-memory / tmp_path data only (no market data directory is ever read)."""
import copy
import gzip
import hashlib
import json
import math
import os
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'research'))
import costs as C                                                                           # noqa: E402
import edge05_runner as R                                                                   # noqa: E402
import splits as S                                                                          # noqa: E402

PREREG = ROOT / 'research_evidence' / 'prereg' / 'funding_carry_xsec.v1.json'
H, DAY, TF = R.HOUR, R.DAY, R.TF
SYMS = [f'S{i:02d}USDT' for i in range(18)]
RULES = {s: {'tick': 0.0001, 'step': 0.001, 'min_qty': 0.001, 'min_notional': 5.0} for s in SYMS}


def real():
    raw = PREREG.read_bytes()
    return json.loads(raw), raw


def short_prereg():
    """The real prereg with short (35-day) windows ending before the real sealed cutoff, for synthetic runs."""
    p, _ = real()
    p = copy.deepcopy(p)
    b = ['2020-11-02', '2020-12-07', '2021-01-11', '2021-02-15', '2021-03-22', '2021-04-26', '2021-05-31',
         '2021-07-05', '2021-08-09']
    z = [x + 'T00:00:00Z' for x in b]
    w = p['windows']
    w['calibration'] = {'start': z[0], 'end': z[1], 'use': 'warm-up'}
    w['train'] = {'start': z[1], 'end': z[2]}
    w['walk_forward'] = [{'fold': i, 'start': z[2 + i], 'end': z[3 + i]} for i in range(5)]
    w['holdout'] = dict(w['holdout'], start=z[7], end=z[8])
    return p, R.canon(p)


def prm():
    return R.params_of(real()[0])


class Synth:
    """Deterministic formula-generated source; nothing is materialised beyond what a call asks for."""

    def __init__(self, syms=SYMS, grids=None, drop=(), extra=(), first=None, calls=None, leak=None):
        self.syms, self.grids = list(syms), grids or {}
        self.drop, self.extra = set(drop), list(extra)          # drop: {(sym, T)}; extra: [(sym, time_ms, rate, iv)]
        self.first = first or {}
        self.calls = calls if calls is not None else []
        self.leak = leak

    def identity(self):
        return {'kind': 'synthetic-test'}

    def price(self, s, t):
        i = self.syms.index(s)
        return 100.0 * (1 + i / 10) * (1 + 0.04 * math.sin(t / (1.7 * DAY) + i))

    def _bars(self, s, iv, lo, hi):
        out = []
        o = -(-lo // iv) * iv
        while o < hi:
            a, c = self.price(s, o), self.price(s, o + iv)
            out.append(R.Bar(o, a, max(a, c) * 1.003, min(a, c) * 0.997, c, 1.0, 5e7, o + iv))
            o += iv
        return out

    def bars(self, s, interval, lo, hi):
        self.calls.append(('bars' + interval, s, lo, hi))
        return self._bars(s, {'4h': TF, '1d': DAY}[interval], lo, hi)

    def mark(self, s, lo, hi):
        self.calls.append(('mark', s, lo, hi))
        return self._bars(s, H, lo, hi)

    def rate(self, s, T):
        return (self.syms.index(s) - 9) * 1e-5 + 1e-6 * ((T // H) % 7)

    def funding(self, s, lo, hi):
        self.calls.append(('funding', s, lo, hi))
        g = self.grids.get(s, 8) * H
        out = []
        T = (lo - 2 * 60_000) // g * g
        while T < hi + 60_000:
            t = T + 7                                            # archived calc_time a few ms after T
            if (s, T) not in self.drop and lo <= t < hi:
                out.append(R.Funding(t, self.rate(s, T), g // H, t))
            T += g
        for (es, t, rate, iv) in self.extra:
            if es == s and lo <= t < hi:
                out.append(R.Funding(t, rate, iv, t))
        if self.leak and s == self.syms[0]:
            out.append(R.Funding(self.leak, 0.0, 8, self.leak))
        return sorted(out)

    def first_ms(self, s):
        return self.first.get(s, {'funding': S.parse_utc('2019-01-01T00:00:00Z'),
                                  '4h': S.parse_utc('2019-01-01T00:00:00Z')})


D0 = S.parse_utc('2021-03-01T00:00:00Z')


# ------------------------------------------------------------------ prereg binding
def test_prose_constants_match_the_prereg_text():
    p, _ = real()
    assert 'at least 30 days before D' in p['universe']['eligibility_at_decision'] and R.AGE_DAYS == 30
    assert 'the 15 closed 1d bars ending at D' in p['execution']['r_unit'] and R.DAILY_BARS == 15
    assert '04:00 UTC' in p['execution']['entry'] and R.ENTRY_OFFSET == 4 * H
    assert 'lookback 180 bars' in p['windows']['split_rule'] and 'horizon 6 bars' in p['windows']['split_rule']
    assert '1000 seeded random books' in p['baselines'][0]['rule'] and R.B0_BOOKS == 1000
    assert 'block 30 cohorts, 10000 resamples, seed 20261028' in p['gates']['expectancy']['lcb']
    assert '0.05% unfitted fallback' in p['costs']['slippage'] and R.SLIP_FALLBACK_BPS == 5.0
    assert set(p['costs']['stress_rows']) == set(R.ROWS)
    assert p['baselines'][0]['seed'] == "sha256('funding_carry_xsec.v1|' + side + '|' + D_iso + '|' + book_index)"
    q = R.params_of(p)
    assert (q.k, q.lag_ms, q.min_eligible, q.stop_mult, q.hold_ms) == (5, 300_000, 15, 2.0, 24 * H)
    assert q.cutoff_ms == S.parse_utc('2026-09-28T00:00:00Z') and q.expected == {8: 3, 4: 6, 1: 24}


def test_real_plan_never_schedules_past_the_cutoff():
    p, _ = real()
    q, plan = R.params_of(p), R.split_plan(p)
    keys = R.evaluated_keys(plan)
    assert keys == ['train'] + [f'walk_forward[{i}]' for i in range(5)]
    days = R.decision_days(plan, 'walk_forward[4]', q)
    assert days[-1] + R.ENTRY_OFFSET + q.hold_ms + TF <= q.cutoff_ms
    for k in ('calibration', 'holdout'):
        with pytest.raises(R.HoldoutRefused):
            R.decision_days(plan, k, q)


def test_unsigned_or_holdout_crossing_prereg_is_refused():
    p, _ = real()
    bad = copy.deepcopy(p)
    bad['signed'] = False
    with pytest.raises(R.RunnerError):
        R.params_of(bad)
    late = copy.deepcopy(p)
    late['windows']['walk_forward'][4]['end'] = '2026-10-05T00:00:00Z'
    with pytest.raises(R.HoldoutRefused):
        R.params_of(late)


# ------------------------------------------------------------------ F24
@pytest.mark.parametrize('grid,n', [(8, 3), (4, 6), (1, 24)])
def test_f24_window_is_exactly_T_in_D_minus_24h_to_D(grid, n):
    s = Synth(grids={SYMS[0]: grid})
    rows = s.funding(SYMS[0], D0 - 40 * H, D0 + 9 * H)
    # an event at T = D computed early (calc 30 s before D) must not enter: T + 300 s > D
    rows = [r for r in rows if r.time_ms - 7 != D0] + [R.Funding(D0 - 30_000, 1.0, grid, D0 - 30_000)]
    want = math.fsum(s.rate(SYMS[0], T) for T in range(D0 - DAY, D0, grid * H))
    assert len(range(D0 - DAY, D0, grid * H)) == n
    assert R.f24(SYMS[0], sorted(rows), D0, prm()) == want


def test_sequence_check_only_sees_available_rows(monkeypatch):
    seen = []
    real_check = C.check_funding_sequence

    def spy(sym, rows, **kw):
        seen.append((list(rows), kw))
        return real_check(sym, rows, **kw)

    monkeypatch.setattr(R.C, 'check_funding_sequence', spy)
    s = Synth(grids={SYMS[0]: 4})
    R.f24(SYMS[0], s.funding(SYMS[0], D0 - 40 * H, D0 + 9 * H), D0, prm())
    rows, kw = seen[0]
    assert kw == {'start_ms': D0 - DAY, 'end_ms': D0}
    assert rows and all(r.time_ms + 300_000 <= D0 for r in rows) and rows[0].time_ms <= D0 - DAY


@pytest.mark.parametrize('case,reason', [('missing', 'funding_sequence'), ('dup', 'funding_sequence'),
                                         ('offgrid', 'funding_off_grid'), ('noanchor', 'funding_no_anchor')])
def test_incomplete_sequence_makes_symbol_ineligible(case, reason):
    s0 = SYMS[0]
    if case == 'missing':
        s = Synth(drop={(s0, D0 - 16 * H)})
    elif case == 'dup':
        s = Synth(extra=[(s0, D0 - 16 * H + 20, 1e-4, 8)])
    elif case == 'offgrid':
        s = Synth(drop={(s0, D0 - 16 * H)}, extra=[(s0, D0 - 16 * H + 5 * 60_000, 1e-4, 8)])
    else:
        s = Synth()
    rows = s.funding(s0, D0 - 40 * H, D0 + 9 * H)
    if case == 'noanchor':
        rows = [r for r in rows if r.time_ms > D0 - DAY + 60_000]
    with pytest.raises(R.Incomplete) as e:
        R.f24(s0, rows, D0, prm())
    assert e.value.reason == reason


def test_missing_event_at_D_is_never_required():
    s = Synth(drop={(SYMS[0], D0)})
    assert R.f24(SYMS[0], s.funding(SYMS[0], D0 - 40 * H, D0 + 9 * H), D0, prm()) is not None


def test_below_min_eligible_is_a_no_cohort_day():
    drop = {(s, D0 - 8 * H) for s in SYMS[:4]}                   # 18 - 4 = 14 < 15
    g = R.Guard(Synth(drop=drop), prm().cutoff_ms)
    rec = R.run_day(g, SYMS, D0, 'walk_forward[0]', prm(), RULES, D0 + 10 * DAY)
    assert rec['eligible'] == 14 and rec['no_cohort'] and 'primary' not in rec
    assert rec['ineligible'] == {'funding_sequence': 4}
    young = {s: {'funding': D0 - 29 * DAY, '4h': D0 - 40 * DAY} for s in SYMS[:4]}
    rec = R.run_day(R.Guard(Synth(first=young), prm().cutoff_ms), SYMS, D0, 'x', prm(), RULES, D0 + 10 * DAY)
    assert rec['ineligible'] == {'age': 4} and rec['no_cohort']


# ------------------------------------------------------------------ selection
def test_rank_k_per_side_and_ties_by_symbol_ascending():
    f = {'E': 0.0, 'A': 0.0, 'B': 3.0, 'C': 3.0, 'D': -1.0, 'F': 2.0, 'G': 1.0}
    sel = R.select(f, 3)
    assert sel['short'] == ['B', 'C', 'F'] and sel['long'] == ['D', 'A', 'E']
    assert sel['b2_short'] == sel['long'] and sel['b2_long'] == sel['short']
    g = R.Guard(Synth(), prm().cutoff_ms)
    rec = R.run_day(g, SYMS, D0, 'x', prm(), RULES, D0 + 10 * DAY)
    assert [p['symbol'] for p in rec['primary']['short']] == SYMS[-5:][::-1]
    assert [p['symbol'] for p in rec['primary']['long']] == SYMS[:5]
    assert set(rec['base']['long']) == set(SYMS) and rec['eligible'] == 18


# ------------------------------------------------------------------ replay
def _day(path, grid=8, rate=1e-4, mark=100.0):
    """bars4: 15 flat warm-up bars then `path` = [(o, h, l, c)] from the 04:00 entry bar on."""
    bars = {}
    entry = D0 + 4 * H
    for o in range(entry - 15 * TF, entry, TF):
        bars[o] = R.Bar(o, 100.0, 100.5, 99.5, 100.0, 1.0, 1e7, o + TF)
    for j, (op, hi, lo, cl) in enumerate(path):
        o = entry + j * TF
        bars[o] = R.Bar(o, op, hi, lo, cl, 1.0, 1e7, o + TF)
    fund = [R.Funding(T + 3, rate, grid, T + 3) for T in range(D0 - 2 * DAY, D0 + 3 * DAY, grid * H)]
    marks = {o: R.Bar(o, mark, mark, mark, mark, 1.0, 1.0, o + H) for o in range(D0 - DAY, D0 + 3 * DAY, H)}
    return {'bars4': bars, 'funding': fund, 'mark': marks}


FLAT = [(100.0, 100.5, 99.5, 100.0)] * 7


def test_entry_bar_stop_funding_and_gap_open_fill():
    ep = R.replay('long', D0, R.STRESS_ROWS['base_x1'], 4.0, _day([(100.0, 104.0, 95.0, 103.0)] + FLAT[1:], grid=4),
                  RULES[SYMS[0]], prm(), D0 + 10 * DAY)
    assert ep['n_funding'] == 1                                      # the 04:00 event at entry counts, 08:00 does not
    path = [FLAT[0], (90.0, 91.0, 89.0, 90.0)] + FLAT[2:]         # second bar opens through the stop
    ep = R.replay('long', D0, R.STRESS_ROWS['base_x1'], 4.0, _day(path), RULES[SYMS[0]], prm(), D0 + 10 * DAY)
    assert ep['gap'] and ep['ref_out'] == 90.0 and ep['exit_ms'] == D0 + 8 * H and len(ep['mtm_u']) == 1
    sp = [FLAT[0], (110.0, 111.0, 109.0, 110.0)] + FLAT[2:]
    ep = R.replay('short', D0, R.STRESS_ROWS['base_x1'], 4.0, _day(sp), RULES[SYMS[0]], prm(), D0 + 10 * DAY)
    assert ep['gap'] and ep['ref_out'] == 110.0 and ep['r'] < -2.4



def test_entry_bar_stop_first_8h_grid():
    path = [(100.0, 104.0, 95.0, 103.0)] + FLAT[1:]
    ep = R.replay('long', D0, R.STRESS_ROWS['base_x1'], 4.0, _day(path), RULES[SYMS[0]], prm(), D0 + 10 * DAY)
    assert ep['why'] == 'exit.stop' and ep['exit_ms'] == D0 + 8 * H and not ep['gap']
    assert ep['ref_out'] == ep['stop'] and ep['mtm_u'] == [] and ep['n_funding'] == 0


@pytest.mark.parametrize('grid,n', [(8, 3), (4, 6), (1, 24)])
def test_full_hold_charges_every_event_entry_le_T_lt_exit(grid, n):
    ep = R.replay('long', D0, R.STRESS_ROWS['base_x1'], 4.0, _day(FLAT, grid=grid), RULES[SYMS[0]], prm(),
                  D0 + 10 * DAY)
    assert ep['why'] == 'exit.time' and ep['exit_ms'] == D0 + 28 * H and ep['n_funding'] == n
    assert len(ep['mtm_u']) == 6
    assert ep['funding_u'] == pytest.approx(-n * 1e-4 * 100.0)       # long pays a positive rate
    assert ep['net_u'] == pytest.approx(ep['gross_u'] + ep['fill_adj_u'] - ep['fees_u'] + ep['funding_u'])


def test_missing_bar_or_funding_or_mark_is_incomplete():
    d = _day(FLAT)
    del d['bars4'][D0 + 12 * H]
    ep = R.safe_replay('long', D0, R.STRESS_ROWS['base_x1'], 4.0, d, None, prm(), D0 + 10 * DAY)
    assert ep['st'] == 'INCOMPLETE' and ep['why'] == 'incomplete.gap'
    d = _day(FLAT)
    d['funding'] = [r for r in d['funding'] if r.time_ms - 3 != D0 + 16 * H]
    assert R.safe_replay('long', D0, R.STRESS_ROWS['base_x1'], 4.0, d, None, prm(), D0 + 10 * DAY)['why'] == \
        'incomplete.funding'
    d = _day(FLAT)
    del d['mark'][D0 + 15 * H]
    assert R.safe_replay('long', D0, R.STRESS_ROWS['base_x1'], 4.0, d, None, prm(), D0 + 10 * DAY)['why'] == \
        'incomplete.mark'
    ep = R.safe_replay('long', D0, R.STRESS_ROWS['entry_latency_1bar'], 4.0, _day(FLAT), None, prm(), D0 + 32 * H)
    assert ep['why'] == 'incomplete.split_end'


def test_adverse_stress_pays_times_m_and_receives_divided_by_m():
    st = R.STRESS_ROWS
    paid = R.funding_paid('long', 1e-4, 100.0, st['base_x1'])
    assert paid == pytest.approx(1e-2)
    assert R.funding_paid('long', 1e-4, 100.0, st['all_variable_costs_x1.5']) == pytest.approx(1.5e-2)
    assert R.funding_paid('short', 1e-4, 100.0, st['all_variable_costs_x2']) == pytest.approx(-0.5e-2)
    assert R.funding_paid('long', -1e-4, 100.0, st['all_variable_costs_x2']) == pytest.approx(-0.5e-2)
    assert R.funding_paid('short', -1e-4, 100.0, st['all_variable_costs_x1.5']) == pytest.approx(1.5e-2)
    assert R.funding_paid('long', 1e-4, 100.0, st['funding_zero']) == 0.0
    assert R.funding_paid('long', 1e-4, 100.0, st['funding_mark_adverse']) > paid
    assert R.funding_paid('short', 1e-4, 100.0, st['funding_mark_adverse']) > -paid      # receives less
    b = R.replay('short', D0, st['base_x1'], 4.0, _day(FLAT), None, prm(), D0 + 10 * DAY)
    x2 = R.replay('short', D0, st['all_variable_costs_x2'], 4.0, _day(FLAT), None, prm(), D0 + 10 * DAY)
    assert b['funding_u'] > 0 and x2['funding_u'] == pytest.approx(b['funding_u'] / 2)
    assert x2['fees_u'] == pytest.approx(2 * b['fees_u'], rel=1e-3) and x2['fill_adj_u'] < b['fill_adj_u'] < 0
    zero = R.replay('short', D0, st['funding_zero'], 4.0, _day(FLAT), None, prm(), D0 + 10 * DAY)
    assert zero['funding_u'] == 0.0 and zero['net_u'] == pytest.approx(b['net_u'] - b['funding_u'])


def test_t_nom_matches_range_vwap_rev():
    import range_vwap_rev as V
    for t, iv in [(D0 + 8 * H + 30_000, 8), (D0 + 4 * H - 59_000, 4), (D0 + 13 * H, 1), (D0 + 2 * H, 2)]:
        assert R.t_nom(R.Funding(t, 0.0, iv, t)) == V.t_nom(R.Funding(t, 0.0, iv, t))
    for t, iv in [(D0 + 8 * H + 61_000, 8), (D0 + 4 * H, 8), (D0, 3)]:
        with pytest.raises(R.Incomplete):
            R.t_nom(R.Funding(t, 0.0, iv, t))
        with pytest.raises(V.Incomplete):
            V.t_nom(R.Funding(t, 0.0, iv, t))


# ------------------------------------------------------------------ full runs, resume, memory, holdout
def _run(out, src=None, **kw):
    p, raw = short_prereg()
    return R.run(str(out), src or Synth(grids={SYMS[3]: 4, SYMS[7]: 1}), SYMS, RULES, p, raw, **kw)


def _bytes(d):
    return {f: (Path(d) / f).read_bytes() for f in ('run.json',) + R.RESULT_FILES}


def test_full_run_summary_and_resume_is_byte_identical(tmp_path):
    full = _run(tmp_path / 'a')
    wf = full['walk_forward']
    assert set(wf) == {'short', 'long'} and all(wf[s]['verdict'] in ('PASS', 'FAIL', 'INCOMPLETE') for s in wf)
    assert wf['short']['checks']['activity'] is False                # 20 synthetic days cannot pass activity
    assert full['splits']['walk_forward[0]']['short']['B0_p95_mean_r'] is not None
    lines = (tmp_path / 'a' / 'days.jsonl').read_bytes().splitlines()
    assert len(lines) > 10 and all(json.loads(x)['format'] == R.DAY_FORMAT for x in lines)
    part = _run(tmp_path / 'b', stop_after_days=5)
    assert part == {'status': 'PARTIAL', 'days_done': 5, 'days_total': len(lines)}
    assert not (tmp_path / 'b' / 'summary.json').exists()
    with open(tmp_path / 'b' / 'days.jsonl', 'ab') as fh:            # a torn line from a crash mid-write
        fh.write(b'{"format":"zb-edge05-day/1","D":"2021-')
    _run(tmp_path / 'b', stop_after_days=4)
    _run(tmp_path / 'b')
    assert _bytes(tmp_path / 'a') == _bytes(tmp_path / 'b')
    with pytest.raises(R.RunnerError):                               # sealed: no second summary
        _run(tmp_path / 'b')


def test_memory_guard_aborts_cleanly_and_resumes(tmp_path, monkeypatch):
    _run(tmp_path / 'a')
    monkeypatch.setattr(R, 'rss_mb', lambda: 10_000.0)
    with pytest.raises(R.MemoryLimit):
        _run(tmp_path / 'b', max_rss_mb=500)
    assert len((tmp_path / 'b' / 'days.jsonl').read_bytes().splitlines()) == 1
    monkeypatch.setattr(R, 'rss_mb', lambda: 1.0)
    _run(tmp_path / 'b', max_rss_mb=500)
    assert _bytes(tmp_path / 'a') == _bytes(tmp_path / 'b')


def test_resume_into_a_different_identity_is_refused(tmp_path):
    _run(tmp_path / 'a', stop_after_days=2)
    p, raw = short_prereg()
    with pytest.raises(R.RunnerError):
        R.run(str(tmp_path / 'a'), Synth(), SYMS, {}, p, raw)


def test_loader_is_called_per_day_never_with_the_full_range(tmp_path):
    calls = []
    _run(tmp_path / 'a', src=Synth(calls=calls), only_keys=['walk_forward[1]'])
    p, _ = short_prereg()
    plan = R.split_plan(p)
    lo, hi = plan.access_range('walk_forward[1]')
    n_days = len(R.decision_days(plan, 'walk_forward[1]', R.params_of(p)))
    assert n_days >= 2
    assert all(lo <= a < b <= hi for _, _, a, b in calls)
    assert max(b - a for _, _, a, b in calls) <= 15 * DAY
    assert max(b - a for k, _, a, b in calls if k != 'bars1d') <= 4 * DAY
    for kind in ('funding', 'bars4h', 'bars1d', 'mark'):
        assert sum(1 for k, *_ in calls if k == kind) == n_days * len(SYMS)


def test_holdout_and_split_boundary_are_refused(tmp_path):
    q = prm()
    g = R.Guard(Synth(), q.cutoff_ms)
    with pytest.raises(R.HoldoutRefused):
        g.funding(SYMS[0], q.cutoff_ms - DAY, q.cutoff_ms + 1)
    with pytest.raises(R.HoldoutRefused):
        g.scope(q.cutoff_ms - DAY, q.cutoff_ms + TF)
    g.scope(D0 - 40 * DAY, D0 + 5 * DAY)
    with pytest.raises(R.HoldoutRefused):
        g.bars(SYMS[0], '4h', D0, D0 + 6 * DAY)                    # past the split end
    with pytest.raises(R.HoldoutRefused):
        g.bars(SYMS[0], '4h', D0 - 41 * DAY, D0)                    # before the split start
    leak = R.Guard(Synth(leak=q.cutoff_ms), q.cutoff_ms)            # a source that returns a sealed row
    with pytest.raises(R.HoldoutRefused):
        leak.funding(SYMS[0], q.cutoff_ms - 2 * DAY, q.cutoff_ms)
    # the short prereg's own holdout start (2021-07-05) is the cutoff: a source leaking rows past it aborts the run
    p, raw = short_prereg()
    cut = R.params_of(p).cutoff_ms
    assert cut == S.parse_utc('2021-07-05T00:00:00Z')
    with pytest.raises(R.HoldoutRefused):
        R.run(str(tmp_path / 'x'), Synth(leak=cut), SYMS, RULES, p, raw)


def test_dry_run_writes_nothing(tmp_path):
    rep = _run(tmp_path / 'dry', dry_run=True)
    assert rep['mode'] == 'dry-run' and not (tmp_path / 'dry').exists()
    assert set(rep['splits']) == {'train'} | {f'walk_forward[{i}]' for i in range(5)}
    assert rep['symbols_without_rules'] == []


# ------------------------------------------------------------------ books / statistics
def test_book_levcap_scales_the_whole_cohort_and_r_is_quantity_free():
    q = prm()
    b = R.Book(q)
    ep = R.replay('long', D0, R.STRESS_ROWS['base_x1'], 0.5, _day(FLAT), RULES[SYMS[0]], q, D0 + 10 * DAY)
    pos = [{'symbol': s, 'f24': 1e-4, 'qv24': 1e9, 'ep': ep} for s in SYMS[:5]]
    b.cohort(D0, pos, RULES)                                         # 1%/0.5 -> 2x equity each, 10x > 3x cap
    notional = sum(b.notional_traded) * b.e0 / 2                     # entry + exit ~ 2x
    assert notional <= 3.0 * b.e0 * 1.01 and b.r == [ep['r']] * 5
    assert b.f24_sign == {'pos': 5, 'zero': 0, 'neg': 0}
    b2 = R.Book(q)
    b2.cohort(D0, [{'symbol': 'NORULE', 'f24': 0.0, 'qv24': None, 'ep': ep}], RULES)
    assert b2.refused == {'unknown': 1} and b2.r == []


def test_bootstrap_and_holm_are_deterministic():
    xs = [0.1 * math.sin(i) + 0.02 for i in range(200)]
    a, b = R.bootstrap(xs), R.bootstrap(xs)
    assert a == b and a['lcb95'] < sum(xs) / len(xs) and 0 < a['p'] < 1
    assert R.holm_reject({'short': 0.01, 'long': 0.04}, 0.05) == {'short': True, 'long': True}
    assert R.holm_reject({'short': 0.03, 'long': 0.04}, 0.05) == {'short': False, 'long': False}
    assert R.b0_seed('short', '2021-03-01T00:00:00Z', 0) == int.from_bytes(
        hashlib.sha256(b'funding_carry_xsec.v1|short|2021-03-01T00:00:00Z|0').digest()[:8], 'big')


# ------------------------------------------------------------------ archive source (synthetic zips in tmp_path)
def _zip(store, rel, csv):
    p = store / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    name = rel.rsplit('/', 1)[1][:-4] + '.csv'
    with zipfile.ZipFile(p, 'w') as z:
        z.writestr(name, csv)
    raw = p.read_bytes()
    (store / (rel + '.ok')).write_text(hashlib.sha256(raw).hexdigest())
    return hashlib.sha256(raw).hexdigest()


def test_archive_source_reads_months_lazily_and_never_opens_the_straddle_month(tmp_path):
    store = tmp_path / 'store'
    cut = S.parse_utc('2021-02-10T00:00:00Z')
    files = []
    for y_m, lo in (('2021-01', '2021-01-01T00:00:00Z'), ('2021-02', '2021-02-01T00:00:00Z')):
        t0 = S.parse_utc(lo)
        n = 31 * 3 if y_m == '2021-01' else 28 * 3
        ts = [t0 + i * 8 * H + 5 for i in range(n)]
        rel = f'um/monthly/fundingRate/AAAUSDT/AAAUSDT-fundingRate-{y_m}.zip'
        csv = 'calc_time,funding_interval_hours,last_funding_rate\n' + ''.join(f'{t},8,0.0001\n' for t in ts)
        files.append({'path': rel, 'series': 'fundingRate', 'symbol': 'AAAUSDT', 'interval': None,
                      'sha256': _zip(store, rel, csv), 'bytes': 1, 'rows': n,
                      'first_open_ms': ts[0], 'last_open_ms': ts[-1], 'source': {}})
    man = tmp_path / 'm.json.gz'
    with gzip.open(man, 'wt', encoding='utf-8') as f:
        f.write(json.dumps({'format': 'x', 'files': files}, indent=1))
    src = R.ArchiveSource(str(man), str(store), ['AAAUSDT'], cut, cache_months=1)
    assert src.straddle_skipped == 1 and src.first_ms('AAAUSDT')['funding'] == files[0]['first_open_ms']
    rows = src.funding('AAAUSDT', S.parse_utc('2021-01-10T00:00:00Z'), S.parse_utc('2021-01-11T00:00:00Z'))
    assert [r.time_ms - 5 for r in rows] == [S.parse_utc('2021-01-10T00:00:00Z') + i * 8 * H for i in range(3)]
    assert src.funding('AAAUSDT', S.parse_utc('2021-02-01T00:00:00Z'), S.parse_utc('2021-02-05T00:00:00Z')) == []
    (store / (files[0]['path'] + '.ok')).write_text('0' * 64)        # tampered sidecar: refused on the next open
    src2 = R.ArchiveSource(str(man), str(store), ['AAAUSDT'], cut)
    with pytest.raises(Exception):
        src2.funding('AAAUSDT', S.parse_utc('2021-01-10T00:00:00Z'), S.parse_utc('2021-01-11T00:00:00Z'))
