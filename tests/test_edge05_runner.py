"""EDGE05 low-RAM runner: synthetic in-memory / tmp_path data only (no market data directory is ever read)."""
import atexit
import copy
import gzip
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'research'))
import costs as C                                                                           # noqa: E402
import edge05_runner as R                                                                   # noqa: E402
import splits as S                                                                          # noqa: E402

PREREG = ROOT / 'research_evidence' / 'prereg' / 'funding_carry_xsec.v1.json'
H, DAY, TF = R.HOUR, R.DAY, R.TF
SYMS = [f'S{i:02d}USDT' for i in range(18)]
RULE = {'tick': 0.0001, 'step': 0.001, 'min_qty': 0.001, 'min_notional': 5.0, 'max_qty': 1e9, 'max_notional': 1e12}
CODE = {'commit': 'c' * 40, 'dirty': False, 'modules': {m: '0' * 64 for m in R.CODE_MODULES}}


SNAP = 'exchangeInfo@2019-01-01T00:00:00Z'
PRODUCER = {'commit': 'd' * 40, 'script': 'tools/research/produce_pit_rules.py', 'script_sha256': 'e' * 64}


def prov(kind, files, coverage, manifest_sha='f' * 64):
    """files: [(name, sha256[, as_of])]."""
    return {'source': {'kind': kind, 'endpoint': R.PROVENANCE_SOURCES[kind]}, 'source_manifest_sha256': manifest_sha,
            'upstream_files': [{'name': f[0], 'sha256': f[1], 'as_of': f[2] if len(f) > 2 else '2026-09-28T00:00:00Z'}
                               for f in files],
            'producer': dict(PRODUCER), 'coverage': coverage}


SNAPDIR = tempfile.mkdtemp(prefix='edge05-synthetic-snapshots-')   # synthetic exchangeInfo files only
atexit.register(shutil.rmtree, SNAPDIR, True)
SNAP2 = 'exchangeInfo@2021-03-02T00:00:00Z'
FILTER_OF = {'tick': ('PRICE_FILTER', 'tickSize'), 'step': ('MARKET_LOT_SIZE', 'stepSize'),
             'min_qty': ('MARKET_LOT_SIZE', 'minQty'),
             'max_qty': ('MARKET_LOT_SIZE', 'maxQty'), 'min_notional': ('MIN_NOTIONAL', 'notional'),
             'max_notional': ('MAX_NOTIONAL', 'maxNotional')}


def source_manifest(kind, files, expect=True):
    """An independently bound upstream list: files [(name, sha256, as_of[, path])]."""
    doc = {'format': R.SOURCE_MANIFEST_FORMAT, 'kind': kind,
           'files': [dict({'name': f[0], 'sha256': f[1], 'as_of': f[2]}, **({'path': f[3]} if len(f) > 3 else {}))
                     for f in files]}
    raw = R.canon(doc)
    return R.SourceManifest(doc, raw, R.sha(raw) if expect is True else expect, kind, SNAPDIR)


def snap_entry(sym, rule, strip=()):
    """A Binance futures exchangeInfo symbols[] entry carrying exactly `rule` (keys absent from it are omitted)."""
    by = {}
    for k, (ft, key) in FILTER_OF.items():
        if k in rule and (ft, key) not in strip and ft not in strip:
            by.setdefault(ft, {'filterType': ft})[key] = repr(float(rule[k]))
    return {'symbol': sym, 'status': 'TRADING', 'filters': [by[ft] for ft in sorted(by)]}


def write_snapshot(entries, strip=None):
    strip = strip or {}
    doc = {'timezone': 'UTC', 'symbols': [snap_entry(sym, r, strip.get(sym, ())) for sym, r in sorted(entries.items())]}
    b = R.canon(doc)
    rel = R.sha(b) + '.json'
    with open(os.path.join(SNAPDIR, rel), 'wb') as fh:
        fh.write(b)
    return R.sha(b), rel


def build(per=None, syms=SYMS, drop=(), strip=None):
    """(rules doc, its bound rules-source manifest): every interval's values are written into a synthetic snapshot
    (a symbol needing two different tuples gets a further snapshot captured at 2019-01-01T00:00:0k)."""
    per = per or {}
    symbols = {s: [dict(iv) for iv in
                   per.get(s, [dict(RULE, **{'from': '2019-01-01T00:00:00Z', 'to': '2027-01-01T00:00:00Z'})])]
               for s in syms}
    contents = {SNAP: {}, SNAP2: {}}
    for s, ivs in symbols.items():
        for iv in ivs:
            rule = {k: iv[k] for k in R.RULE_KEYS if k in iv}
            if 'snapshot' not in iv:
                k = 0
                while True:
                    name = SNAP if k == 0 else f'exchangeInfo@2019-01-01T00:00:{k:02d}Z'
                    have = contents.setdefault(name, {}).get(s)
                    if have is None or have == rule:
                        break
                    k += 1
                iv['snapshot'] = name
            contents.setdefault(iv['snapshot'], {})[s] = rule
    files = []
    for name, entries in sorted(contents.items()):
        h, rel = write_snapshot({k: v for k, v in entries.items() if k not in drop}, strip)
        files.append((name, h, name.split('@')[1], rel))
    src = source_manifest('binance-um-exchangeinfo-snapshots', files)
    cov = [{'symbol': s, 'series': 'rules', 'from': iv['from'], 'to': iv['to'], 'rows': 1,
            'upstream': [iv['snapshot']]} for s, ivs in symbols.items() for iv in ivs]
    return {'format': R.RULES_FORMAT,
            'provenance': prov('binance-um-exchangeinfo-snapshots', [f[:3] for f in files], cov, src.sha256),
            'symbols': symbols}, src, files


_, RSRC, SNAP_FILES = build()


def rules_doc(per=None, syms=SYMS):
    return build(per, syms)[0]


def pit(per=None, **kw):
    doc, src, _ = build(per, **kw)
    return R.PitRules(doc, R.canon(doc), src)


RULES = pit()


def ra(t):
    return dict(RULE)


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
    assert '0.05% unfitted fallback' in p['costs']['slippage'] and R.SLIP_FLOOR_BPS == 5.0
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


# ------------------------------------------------------------------ code identity
def test_code_modules_are_every_repo_module_the_runner_imports():
    code = ('import os, sys; sys.path.insert(0, os.path.join(sys.argv[1], "tools", "research")); '
            'import edge05_runner, runlock; r = os.path.abspath(sys.argv[1]); '
            'print("\\n".join(sorted(os.path.relpath(m.__file__, r).replace(os.sep, "/") for m in '
            'list(sys.modules.values()) if getattr(m, "__file__", None) and '
            'os.path.abspath(m.__file__).startswith(r + os.sep))))')
    out = subprocess.run([sys.executable, '-c', code, str(ROOT)], capture_output=True, text=True, check=True)
    assert tuple(out.stdout.split()) == R.CODE_MODULES


def test_code_identity_hashes_the_modules_and_reads_git():
    ci = R.code_identity()
    assert len(ci['commit']) == 40 and isinstance(ci['dirty'], bool) and set(ci['modules']) == set(R.CODE_MODULES)
    assert ci['modules']['tools/research/edge05_runner.py'] == hashlib.sha256(
        (ROOT / 'tools' / 'research' / 'edge05_runner.py').read_bytes()).hexdigest()


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
BASE = R.STRESS_ROWS['base_x1']


def test_entry_bar_stop_first_8h_grid():
    path = [(100.0, 104.0, 95.0, 103.0)] + FLAT[1:]               # entry bar: big up AND touches stop: stop-first
    ep = R.replay('long', D0, BASE, 4.0, _day(path), ra, prm(), D0 + 10 * DAY)
    assert ep['why'] == 'exit.stop' and ep['exit_ms'] == D0 + 8 * H and not ep['gap']
    assert ep['ref_out'] == ep['stop'] and ep['mtm_u'] == [] and ep['n_funding'] == 0


def test_entry_bar_stop_funding_and_gap_open_fill():
    ep = R.replay('long', D0, BASE, 4.0, _day([(100.0, 104.0, 95.0, 103.0)] + FLAT[1:], grid=4), ra, prm(),
                  D0 + 10 * DAY)
    assert ep['n_funding'] == 1                                      # the 04:00 event at entry counts, 08:00 does not
    path = [FLAT[0], (90.0, 91.0, 89.0, 90.0)] + FLAT[2:]         # second bar opens through the stop
    ep = R.replay('long', D0, BASE, 4.0, _day(path), ra, prm(), D0 + 10 * DAY)
    assert ep['gap'] and ep['ref_out'] == 90.0 and ep['exit_ms'] == D0 + 8 * H and len(ep['mtm_u']) == 1
    sp = [FLAT[0], (110.0, 111.0, 109.0, 110.0)] + FLAT[2:]
    ep = R.replay('short', D0, BASE, 4.0, _day(sp), ra, prm(), D0 + 10 * DAY)
    assert ep['gap'] and ep['ref_out'] == 110.0 and ep['r'] < -2.4


@pytest.mark.parametrize('grid,n', [(8, 3), (4, 6), (1, 24)])
def test_full_hold_charges_every_event_entry_le_T_lt_exit(grid, n):
    ep = R.replay('long', D0, BASE, 4.0, _day(FLAT, grid=grid), ra, prm(), D0 + 10 * DAY)
    assert ep['why'] == 'exit.time' and ep['exit_ms'] == D0 + 28 * H and ep['n_funding'] == n
    assert len(ep['mtm_u']) == 6
    assert ep['funding_u'] == pytest.approx(-n * 1e-4 * 100.0)       # long pays a positive rate
    assert ep['net_u'] == pytest.approx(ep['gross_u'] + ep['fill_adj_u'] - ep['fees_u'] + ep['funding_u'])


def test_slippage_records_model_and_floor_per_fill():
    ep = R.replay('long', D0, BASE, 4.0, _day(FLAT), ra, prm(), D0 + 10 * DAY)
    for k in ('slip_in', 'slip_out'):
        s = ep[k]
        assert s['floor_bps'] == 5.0 and s['charged_bps'] == max(s['model_bps'], 5.0)
    assert ep['slip_in']['model_bps'] == pytest.approx(C.slip_bps([R.Bar(0, 100, 100.5, 99.5, 100, 1, 1, 1)] * 15, 0.05))
    assert ep['fill_in'] == pytest.approx(100.0 * (1 + ep['slip_in']['charged_bps'] / 1e4), abs=1e-4)


def test_missing_bar_or_funding_or_mark_is_incomplete():
    d = _day(FLAT)
    del d['bars4'][D0 + 12 * H]
    ep = R.safe_replay('long', D0, BASE, 4.0, d, ra, prm(), D0 + 10 * DAY)
    assert ep['st'] == 'INCOMPLETE' and ep['why'] == 'incomplete.gap'
    d = _day(FLAT)
    d['funding'] = [r for r in d['funding'] if r.time_ms - 3 != D0 + 16 * H]
    assert R.safe_replay('long', D0, BASE, 4.0, d, ra, prm(), D0 + 10 * DAY)['why'] == 'incomplete.funding'
    d = _day(FLAT)
    del d['mark'][D0 + 15 * H]
    assert R.safe_replay('long', D0, BASE, 4.0, d, ra, prm(), D0 + 10 * DAY)['why'] == 'incomplete.mark'
    ep = R.safe_replay('long', D0, R.STRESS_ROWS['entry_latency_1bar'], 4.0, _day(FLAT), ra, prm(), D0 + 32 * H)
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
    b = R.replay('short', D0, st['base_x1'], 4.0, _day(FLAT), ra, prm(), D0 + 10 * DAY)
    x2 = R.replay('short', D0, st['all_variable_costs_x2'], 4.0, _day(FLAT), ra, prm(), D0 + 10 * DAY)
    assert b['funding_u'] > 0 and x2['funding_u'] == pytest.approx(b['funding_u'] / 2)
    assert x2['fees_u'] == pytest.approx(2 * b['fees_u'], rel=1e-3) and x2['fill_adj_u'] < b['fill_adj_u'] < 0
    zero = R.replay('short', D0, st['funding_zero'], 4.0, _day(FLAT), ra, prm(), D0 + 10 * DAY)
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


# ------------------------------------------------------------------ point-in-time rules
def test_pit_rules_interval_gap_is_rules_uncovered_and_changes_apply_by_date():
    a = dict(RULE, **{'from': '2019-01-01T00:00:00Z', 'to': '2021-03-01T06:00:00Z'})
    b = dict(RULE, tick=0.5, step=0.01, **{'from': '2021-03-01T06:00:00Z', 'to': '2021-03-03T00:00:00Z'})
    rules = pit({SYMS[0]: [a, b]})
    assert rules.at(SYMS[0], D0 + 4 * H)['tick'] == 0.0001 and rules.at(SYMS[0], D0 + 6 * H)['tick'] == 0.5
    assert rules.at(SYMS[0], S.parse_utc('2021-03-03T00:00:00Z')) is None          # [from, to): `to` is excluded
    ep = R.replay('long', D0, BASE, 4.0, _day(FLAT), lambda t: rules.at(SYMS[0], t), prm(), D0 + 10 * DAY)
    assert ep['rule']['tick'] == 0.0001 and ep['rule_exit']['tick'] == 0.5        # entry rule vs exit rule by date
    gap = pit({SYMS[0]: [a, dict(b, **{'from': '2021-03-02T06:00:00Z'})]})             # exit D+28h falls in the gap
    assert gap.at(SYMS[0], D0 + 28 * H) is None
    # entry 04:00 covered; exit D+28h is past both intervals -> rules_uncovered, never a substitute rule
    ep = R.safe_replay('long', D0, BASE, 4.0, _day(FLAT), lambda t: gap.at(SYMS[0], t), prm(), D0 + 10 * DAY)
    assert ep == {'st': 'INCOMPLETE', 'why': 'rules_uncovered', 'at_ms': D0 + 28 * H}
    none = pit({SYMS[0]: [dict(RULE, **{'from': '2021-03-02T00:00:00Z', 'to': '2022-01-01T00:00:00Z'})]})
    ep = R.safe_replay('long', D0, BASE, 4.0, _day(FLAT), lambda t: none.at(SYMS[0], t), prm(), D0 + 10 * DAY)
    assert ep['why'] == 'rules_uncovered' and ep['at_ms'] == D0 + 4 * H
    with pytest.raises(R.Incomplete, match='provenance_uncovered'):                    # outside declared coverage
        gap.lookup(SYMS[0], D0 + 28 * H)
    ep = R.safe_replay('long', D0, BASE, 4.0, _day(FLAT), lambda t: gap.lookup(SYMS[0], t), prm(), D0 + 10 * DAY)
    assert ep['why'] == 'provenance_uncovered' and 'provenance_uncovered' in R.GATE_FAILING_REASONS


def test_pit_rules_validation():
    a = dict(RULE, **{'from': '2019-01-01T00:00:00Z', 'to': '2021-01-01T00:00:00Z'})
    with pytest.raises(R.RunnerError):
        pit({SYMS[0]: [a, dict(a, **{'from': '2020-06-01T00:00:00Z'})]})                # overlap
    with pytest.raises(R.RunnerError):
        pit({SYMS[0]: [dict(a, step=0.0)]})
    with pytest.raises(R.RunnerError):
        pit({SYMS[0]: [{k: v for k, v in a.items() if k != 'min_notional'}]})
    with pytest.raises(R.RunnerError):
        R.PitRules({'format': 'flat', 'source': 'x', 'symbols': {}}, b'')
    ident = RULES.identity()
    assert ident['sha256'] == hashlib.sha256(R.canon(rules_doc())).hexdigest() and ident['bad_coverage_entries'] == 0
    assert ident['provenance']['producer'] == PRODUCER and len(ident['provenance']['digest']) == 64


# ------------------------------------------------------------------ accounting classification
def test_classify_ok_incomplete_and_unresolved():
    ep = R.replay('long', D0, BASE, 4.0, _day(FLAT), ra, prm(), D0 + 10 * DAY)
    ep['side'] = 'long'
    assert R.classify(ep) == ('OK', '')
    for why in R.INCOMPLETE_REASONS:
        assert R.classify({'st': 'INCOMPLETE', 'why': why}) == ('INCOMPLETE', why)
    assert R.classify({'st': 'INCOMPLETE', 'why': 'mystery'})[0] == 'UNRESOLVED'
    assert R.classify({'st': 'MAYBE'}) == ('UNRESOLVED', 'unknown_status')
    assert R.classify(None)[0] == 'UNRESOLVED'
    assert R.classify(dict(ep, net_u=ep['net_u'] + 0.01)) == ('UNRESOLVED', 'net_mismatch')
    assert R.classify(dict(ep, r=ep['r'] + 0.01)) == ('UNRESOLVED', 'r_mismatch')
    assert R.classify(dict(ep, fees_u=float('nan'))) == ('UNRESOLVED', 'non_finite')
    assert R.classify(dict(ep, fill_out=ep['fill_out'] + 1, net_u=ep['net_u'])) == ('UNRESOLVED', 'fill_mismatch')
    assert R.classify(dict(ep, rule=None)) == ('UNRESOLVED', 'no_rule')
    assert R.classify(dict(ep, slip_in=dict(ep['slip_in'], charged_bps=1.0))) == ('UNRESOLVED', 'slippage_components')
    b = R.Book(prm())
    b.cohort(D0, [{'symbol': 'A', 'f24': 0.0, 'qv24': None, 'ep': {'st': 'MAYBE'}},
                  {'symbol': 'B', 'f24': 0.0, 'qv24': None, 'ep': {'st': 'INCOMPLETE', 'why': 'incomplete.gap'}}])
    assert (b.unresolved, b.incomplete, b.unresolved_by) == (1, 1, {'unknown_status': 1})


# ------------------------------------------------------------------ full runs, resume, memory, holdout
def _run(out, src=None, rules=RULES, code=CODE, max_rss_mb=8192, **kw):
    p, raw = short_prereg()
    kw.setdefault('expect_rules_sha256', rules.sha256 if rules is not None else None)
    return R.run(str(out), src or Synth(grids={SYMS[3]: 4, SYMS[7]: 1}), SYMS, rules, p, raw, code=code,
                 max_rss_mb=max_rss_mb, **kw)


def _bytes(d):
    return {f: (Path(d) / f).read_bytes() for f in ('run.json',) + R.RESULT_FILES}


@pytest.fixture(scope='module')
def full(tmp_path_factory):
    d = tmp_path_factory.mktemp('full')
    return _run(d / 'a'), _bytes(d / 'a')


def test_full_run_summary_identity_and_conventions(full, tmp_path):
    summ, b = full
    wf = summ['walk_forward']
    assert set(wf) == {'short', 'long'} and all(wf[s]['verdict'] in ('PASS', 'FAIL', 'INCOMPLETE') for s in wf)
    assert wf['short']['checks']['activity'] is False and wf['short']['checks']['data'] is True
    assert all(f['unresolved'] == 0 and f['days'] == f['scheduled_days'] for f in wf['short']['data']['folds'].values())
    assert wf['short']['capacity_inputs_missing'] == 0 and wf['short']['checks']['capacity'] is True
    assert summ['policy']['boot_n'] == 10000 and summ['dev_end'] is None
    assert {'AGE-ONLY-NO-COHORT-REPORTED', 'DATA-LOSS-NO-COHORT-GATED', 'ENTRY-EXIT-MAX-FILTERS',
            'UW-COUNTS-RECOVERY-POINT', 'PROVENANCE-BOUND-ARTIFACTS'} <= set(summ['labels'])
    assert summ['conventions'] == R.CONVENTIONS and 'WORST-COHORT-NET-R-IS-SUM' in summ['labels']
    ident = json.loads(b['run.json'])
    assert ident['code'] == CODE and ident['rules'] == RULES.identity() and ident['conventions'] == R.CONVENTIONS
    assert ident['holdout_freeze_record_sha256'] is None
    sp = summ['splits']['walk_forward[0]']['short']
    assert sp['B0_p95_mean_r'] is not None and sp['B1']['episodes'] > sp['rows']['base_x1']['episodes']
    lines = b['days.jsonl'].splitlines()
    run_d = prev = hashlib.sha256(R.canon(ident)).hexdigest()
    assert summ['identity_sha256'] == run_d
    for ln in lines:
        rec = json.loads(ln)
        h = rec.pop('hash')
        assert rec['run'] == run_d and rec['prev'] == prev
        assert hashlib.sha256(R.canon(rec)).hexdigest() == h
        prev = h


def test_resume_is_byte_identical_and_torn_tail_recovers(full, tmp_path):
    _, want = full
    n = len(want['days.jsonl'].splitlines())
    part = _run(tmp_path / 'b', stop_after_days=5)
    assert part == {'status': 'PARTIAL', 'days_done': 5, 'days_total': n}
    assert not (tmp_path / 'b' / 'summary.json').exists()
    with open(tmp_path / 'b' / 'days.jsonl', 'ab') as fh:            # a torn line from a crash mid-write
        fh.write(b'{"format":"zb-edge05-day/2","D":"2021-')
    _run(tmp_path / 'b', stop_after_days=4)
    _run(tmp_path / 'b')
    assert _bytes(tmp_path / 'b') == want
    with pytest.raises(R.RunnerError):                               # sealed: no second summary
        _run(tmp_path / 'b')


def _partial(tmp_path, n=4):
    _run(tmp_path / 'p', stop_after_days=n)
    return tmp_path / 'p' / 'days.jsonl'


def test_resume_refuses_an_edited_valid_json_line(tmp_path):
    p = _partial(tmp_path)
    lines = p.read_bytes().splitlines(keepends=True)
    rec = json.loads(lines[1])
    rec['eligible'] += 1                                             # still valid JSON, same (split, D)
    lines[1] = R.canon(rec) + b'\n'
    p.write_bytes(b''.join(lines))
    with pytest.raises(R.RunnerError, match='record hash'):
        _run(tmp_path / 'p')
    rec.pop('hash')                                                  # re-hashed edit: the chain still breaks
    rec['hash'] = hashlib.sha256(R.canon(rec)).hexdigest()
    lines[1] = R.canon(rec) + b'\n'
    p.write_bytes(b''.join(lines))
    with pytest.raises(R.RunnerError, match='chain'):
        _run(tmp_path / 'p')


def test_resume_refuses_a_dropped_line_and_a_reformatted_line(tmp_path):
    p = _partial(tmp_path)
    lines = p.read_bytes().splitlines(keepends=True)
    p.write_bytes(lines[0] + b''.join(lines[2:]))
    with pytest.raises(R.RunnerError, match='chain'):
        _run(tmp_path / 'p')
    p.write_bytes(b''.join(lines[:1]) + json.dumps(json.loads(lines[1]), indent=1).replace('\n', ' ').encode() + b'\n')
    with pytest.raises(R.RunnerError):
        _run(tmp_path / 'p')


def test_resume_refuses_a_code_identity_change(tmp_path):
    _partial(tmp_path)
    changed = copy.deepcopy(CODE)
    changed['modules']['tools/research/costs.py'] = '1' * 64
    with pytest.raises(R.RunnerError, match='identity'):
        _run(tmp_path / 'p', code=changed)
    with pytest.raises(R.RunnerError, match='identity'):
        _run(tmp_path / 'p', code=dict(CODE, commit='d' * 40))


def test_dirty_tree_refuses_a_real_run_unless_test_flag(tmp_path):
    dirty = dict(CODE, dirty=True)
    with pytest.raises(R.RunnerError, match='dirty'):
        _run(tmp_path / 'd', code=dirty)
    assert not (tmp_path / 'd').exists()
    assert _run(tmp_path / 'd', code=dirty, allow_dirty_test_only=True, stop_after_days=1)['status'] == 'PARTIAL'


def test_missing_rules_artifact_refuses_a_real_run_and_dry_run_reports(tmp_path):
    with pytest.raises(R.RunnerError, match='rules'):
        _run(tmp_path / 'r', rules=None)
    assert not (tmp_path / 'r').exists()
    rep = _run(tmp_path / 'r', rules=None, dry_run=True)
    assert rep['rules'] is None and rep['rules_uncovered_days_by_symbol'] is None
    short = pit({SYMS[0]: [dict(RULE, **{'from': '2019-01-01T00:00:00Z', 'to': '2021-03-01T00:00:00Z'})]})
    rep = _run(tmp_path / 'r', rules=short, dry_run=True)
    assert set(rep['rules_uncovered_days_by_symbol']) == {SYMS[0]} and not (tmp_path / 'r').exists()


def test_rules_gap_makes_episodes_incomplete_in_a_run(tmp_path):
    gap = pit({s: [dict(RULE, **{'from': '2019-01-01T00:00:00Z', 'to': '2021-03-10T00:00:00Z'}),
                   dict(RULE, **{'from': '2021-03-20T00:00:00Z', 'to': '2027-01-01T00:00:00Z'})] for s in SYMS})
    summ = _run(tmp_path / 'g', rules=gap, only_keys=['walk_forward[1]'])
    row = summ['splits']['walk_forward[1]']['short']['rows']['base_x1']
    assert row['incomplete_by_reason'].get('provenance_uncovered', 0) > 0 and row['unresolved'] == 0
    assert summ['walk_forward']['short']['verdict'] == 'INCOMPLETE'
    fold = summ['walk_forward']['short']['data']['folds']['walk_forward[1]']
    assert fold['gate_failing_incomplete'] > 0 and not fold['ok']


def test_baselines_run_through_the_book_with_refusals_and_cap(tmp_path):
    big = {s: [dict(RULE, min_notional=1e12, **{'from': '2019-01-01T00:00:00Z', 'to': '2027-01-01T00:00:00Z'})]
           for s in SYMS[:6]}
    summ = _run(tmp_path / 'f', rules=pit(big), only_keys=['walk_forward[0]'])
    sp = summ['splits']['walk_forward[0]']['long']
    assert sp['B1']['refused'].get('below_min_notional', 0) > 0
    assert sp['rows']['base_x1']['refused'].get('below_min_notional', 0) > 0           # long book = S00..S04
    assert sp['B0_refused'].get('below_min_notional', 0) > 0
    # B1 is executed: its episode count excludes the refused symbols (a per-unit sum would count them)
    days = [json.loads(x) for x in (tmp_path / 'f' / 'days.jsonl').read_bytes().splitlines()]
    ok = sum(1 for d in days if not d['no_cohort'] for s, ep in d['base']['long'].items() if ep['st'] == 'OK')
    assert sp['B1']['episodes'] == ok - sum(1 for d in days if not d['no_cohort'] for s in SYMS[:6]
                                            if d['base']['long'][s]['st'] == 'OK')
    # the leverage cap scales B1's 18-position cohort: gross entry notional <= 3x equity
    assert sp['B1']['daily_turnover_mean'] <= 2 * 3.0 * 1.01


def test_unresolved_row_makes_the_side_incomplete(tmp_path, monkeypatch):
    real_replay = R.replay

    def bad(side, D, st, *a, **kw):
        ep = real_replay(side, D, st, *a, **kw)
        if side == 'short' and st.name == 'base_x1':
            ep['net_u'] += 1.0                                       # an unexplained component mismatch
        return ep

    monkeypatch.setattr(R, 'replay', bad)
    summ = _run(tmp_path / 'u', only_keys=['walk_forward[0]'])
    assert summ['walk_forward']['short']['verdict'] == 'INCOMPLETE'
    assert summ['walk_forward']['short']['checks']['data'] is False
    assert summ['splits']['walk_forward[0]']['short']['rows']['base_x1']['unresolved_by_reason'] == \
        {'net_mismatch': summ['splits']['walk_forward[0]']['short']['rows']['base_x1']['selected']}


def test_memory_guard_aborts_mid_day_without_a_partial_line_and_resumes(full, tmp_path, monkeypatch):
    _, want = full
    calls = {'n': 0}

    def rss():
        calls['n'] += 1
        return 10_000.0 if calls['n'] > 30 else 1.0                  # trips mid-day (18 symbol checks per day)

    monkeypatch.setattr(R, 'rss_mb', rss)
    with pytest.raises(R.MemoryLimit):
        _run(tmp_path / 'm', max_rss_mb=500)
    assert len((tmp_path / 'm' / 'days.jsonl').read_bytes().splitlines()) == 1
    assert (tmp_path / 'm' / 'days.jsonl').read_bytes().endswith(b'\n')
    monkeypatch.setattr(R, 'rss_mb', lambda: 1.0)
    _run(tmp_path / 'm', max_rss_mb=500)
    assert _bytes(tmp_path / 'm') == want


def test_unmeasurable_rss_refuses_to_start(tmp_path, monkeypatch):
    def broken():
        raise R.RunnerError('cannot measure RSS')
    monkeypatch.setattr(R, 'rss_mb', broken)
    with pytest.raises(R.RunnerError, match='RSS'):
        _run(tmp_path / 'n', max_rss_mb=500)
    assert not (tmp_path / 'n' / 'days.jsonl').exists()
    monkeypatch.undo()
    with pytest.raises(R.RunnerError, match='mandatory'):           # no cap at all: refused
        _run(tmp_path / 'n2', max_rss_mb=None)
    with pytest.raises(R.RunnerError, match='mandatory'):
        _run(tmp_path / 'n2', max_rss_mb=float('inf'))
    assert not (tmp_path / 'n2').exists()


def test_real_rss_is_positive():
    assert R.rss_mb() > 0


def test_holdout_freeze_record_hash_is_bound(tmp_path):
    rec = tmp_path / 'freeze.json'
    rec.write_bytes(b'{"kind":"holdout_freeze"}\n')
    _run(tmp_path / 'h', holdout_freeze_record=str(rec), stop_after_days=1)
    ident = json.loads((tmp_path / 'h' / 'run.json').read_bytes())
    assert ident['holdout_freeze_record_sha256'] == hashlib.sha256(rec.read_bytes()).hexdigest()
    with pytest.raises(R.RunnerError, match='identity'):            # a different record cannot resume the run
        _run(tmp_path / 'h')


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
        R.run(str(tmp_path / 'x'), Synth(leak=cut), SYMS, RULES, p, raw, code=CODE, max_rss_mb=8192,
              expect_rules_sha256=RULES.sha256)


def test_dry_run_writes_nothing(tmp_path):
    rep = _run(tmp_path / 'dry', dry_run=True)
    assert rep['mode'] == 'dry-run' and not (tmp_path / 'dry').exists()
    assert set(rep['splits']) == {'train'} | {f'walk_forward[{i}]' for i in range(5)}
    assert rep['rules_uncovered_days_by_symbol'] == {}


# ------------------------------------------------------------------ books / statistics
def test_book_levcap_scales_the_whole_cohort_and_r_is_quantity_free():
    q = prm()
    b = R.Book(q)
    ep = R.replay('long', D0, BASE, 0.5, _day(FLAT), ra, q, D0 + 10 * DAY)
    ep['side'] = 'long'
    pos = [{'symbol': s, 'f24': 1e-4, 'qv24': 1e9, 'ep': ep} for s in SYMS[:5]]
    b.cohort(D0, pos)                                                # 1%/0.5 -> 2x equity each, 10x > 3x cap
    notional = sum(b.notional_traded) * b.e0 / 2                     # entry + exit ~ 2x
    assert notional <= 3.0 * b.e0 * 1.01 and b.r == [ep['r']] * 5
    assert b.f24_sign == {'pos': 5, 'zero': 0, 'neg': 0}
    small = R.Book(q)
    small.cohort(D0, [{'symbol': 'X', 'f24': 0.0, 'qv24': None, 'ep': dict(ep, rule=dict(RULE, min_notional=1e12))}])
    assert small.refused == {'below_min_notional': 1} and small.r == []
    light = R.Book(q, light=True)
    light.cohort(D0, pos)
    assert light.mean_r() == pytest.approx(ep['r']) and light.r == [] and light.eq == pytest.approx(b.eq)


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
    with pytest.raises(R.Incomplete, match='provenance_uncovered'):                 # never opened, never silent []
        src.funding('AAAUSDT', S.parse_utc('2021-02-01T00:00:00Z'), S.parse_utc('2021-02-05T00:00:00Z'))
    (store / (files[0]['path'] + '.ok')).write_text('0' * 64)        # tampered sidecar: refused on the next open
    src2 = R.ArchiveSource(str(man), str(store), ['AAAUSDT'], cut)
    with pytest.raises(Exception):
        src2.funding('AAAUSDT', S.parse_utc('2021-01-10T00:00:00Z'), S.parse_utc('2021-01-11T00:00:00Z'))


# ------------------------------------------------------------------ review addendum mutants
class StraddleSynth(Synth):
    """A source whose archive has a never-opened month straddling the cutoff from 2021-06-01."""
    straddle_from = S.parse_utc('2021-06-01T00:00:00Z')
    straddle = None


def amend(dev_end, **over):
    p, raw = short_prereg()
    doc = {'format': R.AMENDMENT_FORMAT, 'candidate_id': R.CANDIDATE, 'prereg_sha256': R.sha(raw),
           'status': 'FROZEN - SIGNED BY CODEX', 'signed': True, 'signed_by': 'codex', 'dev_end': dev_end,
           'reason': 'synthetic test amendment'}
    doc.update(over)
    b = R.canon(doc)
    return doc, b, raw


def load_amend(dev_end, expect=True, **over):
    doc, b, raw = amend(dev_end, **over)
    return R.Amendment(doc, b, raw, R.params_of(short_prereg()[0]).cutoff_ms, R.sha(b) if expect is True else expect)


def test_straddle_month_is_never_a_silent_drop(tmp_path):
    with pytest.raises(R.RunnerError, match='straddle'):             # evidence run without the artifact refuses
        _run(tmp_path / 's', src=StraddleSynth())
    assert not (tmp_path / 's').exists()
    rep = _run(tmp_path / 's', src=StraddleSynth(), dry_run=True)
    assert rep['straddle_unresolved_from'] == '2021-06-01T00:00:00Z' and rep['expected_artifact_mismatch'] == ['straddle']
    with pytest.raises(R.RunnerError, match='straddle'):             # an amendment past the straddle month: refused
        _run(tmp_path / 's', src=StraddleSynth(), amendment=load_amend('2021-06-10T00:00:00Z'))
    with pytest.raises(R.HoldoutRefused):
        load_amend('2021-06-01T12:00:00Z')
    with pytest.raises(R.RunnerError, match='Amendment'):            # a bare end value is never accepted
        _run(tmp_path / 's', src=StraddleSynth(), amendment=S.parse_utc('2021-06-01T00:00:00Z'))
    with pytest.raises(TypeError):
        _run(tmp_path / 's', src=StraddleSynth(), dev_end=S.parse_utc('2021-06-01T00:00:00Z'))
    dev = S.parse_utc('2021-06-01T00:00:00Z')
    am = load_amend('2021-06-01T00:00:00Z')
    summ = _run(tmp_path / 's', src=StraddleSynth(), amendment=am)
    ident = json.loads((tmp_path / 's' / 'run.json').read_bytes())
    assert ident['dev_end'] == summ['dev_end'] == '2021-06-01T00:00:00Z'
    assert ident['amendment'] == {'format': R.AMENDMENT_FORMAT, 'sha256': am.sha256, 'dev_end': '2021-06-01T00:00:00Z',
                                  'signed_by': 'codex'}
    assert summ['dev_end_excluded_days']['walk_forward[4]'] > 0 == summ['dev_end_excluded_days']['walk_forward[0]']
    assert ident['dev_end_excluded_days'] == summ['dev_end_excluded_days']
    last = json.loads((tmp_path / 's' / 'days.jsonl').read_bytes().splitlines()[-1])
    assert S.parse_utc(last['D']) + R.ENTRY_OFFSET + DAY + TF <= dev
    art = StraddleSynth()
    art.straddle = SimpleNamespace(sha256='ab' * 32)                  # an attached pre-cutoff artifact
    with pytest.raises(R.RunnerError, match='expected artifact'):     # no / another expected hash: refused
        _run(tmp_path / 't', src=art, stop_after_days=1)
    with pytest.raises(R.RunnerError, match='expected artifact'):
        _run(tmp_path / 't', src=art, stop_after_days=1, expect_straddle_sha256='cd' * 32)
    assert _run(tmp_path / 't', src=art, stop_after_days=1, expect_straddle_sha256='ab' * 32)['status'] == 'PARTIAL'


def test_amendment_record_must_be_signed_hash_bound_and_for_this_prereg():
    load_amend('2021-06-01T00:00:00Z')
    with pytest.raises(R.RunnerError, match='expect-amendment'):
        load_amend('2021-06-01T00:00:00Z', expect=None)
    with pytest.raises(R.RunnerError, match='expect-amendment'):
        load_amend('2021-06-01T00:00:00Z', expect='0' * 64)
    for over in ({'signed': False}, {'status': 'DRAFT'}, {'signed_by': ' '}):
        with pytest.raises(R.RunnerError, match='signed'):
            load_amend('2021-06-01T00:00:00Z', **over)
    with pytest.raises(R.RunnerError, match='prereg'):
        load_amend('2021-06-01T00:00:00Z', prereg_sha256='0' * 64)
    with pytest.raises(R.HoldoutRefused):
        load_amend('2021-08-02T00:00:00Z')                            # past the sealed cutoff


def test_cli_has_no_dev_end_option(capsys):
    with pytest.raises(SystemExit) as e:
        R.main(['--store', 'x', '--max-rss-mb', '100', '--dev-end', '2021-06-01T00:00:00Z', '--dry-run'])
    assert e.value.code == 2 and 'dev-end' in capsys.readouterr().err


def test_real_run_refuses_a_missing_or_unexpected_rules_artifact_hash(tmp_path):
    with pytest.raises(R.RunnerError, match='expected artifact.*rules'):
        _run(tmp_path / 'x', expect_rules_sha256=None)
    with pytest.raises(R.RunnerError, match='expected artifact.*rules'):
        _run(tmp_path / 'x', expect_rules_sha256='0' * 64)
    assert not (tmp_path / 'x').exists()
    assert _run(tmp_path / 'x', expect_rules_sha256='0' * 64, dry_run=True)['expected_artifact_mismatch'] == ['rules']


def test_rules_provenance_free_text_bad_hash_and_bad_coverage():
    doc = rules_doc()
    for mut in (lambda d: d['provenance'].__setitem__('source', 'synthetic-test'),
                lambda d: d['provenance']['source'].__setitem__('endpoint', 'https://example.invalid/'),
                lambda d: d['provenance']['source'].__setitem__('kind', 'binance-vision-archive'),
                lambda d: d['provenance']['upstream_files'][0].__setitem__('sha256', 'xyz'),
                lambda d: d['provenance']['upstream_files'][0].__setitem__('name', 'whatever.json'),
                lambda d: d['provenance'].__setitem__('source_manifest_sha256', 'nope'),
                lambda d: d['provenance']['producer'].__setitem__('commit', 'HEAD'),
                lambda d: d['provenance'].pop('producer'),
                lambda d: d['provenance'].__setitem__('coverage', []),
                lambda d: d['provenance']['coverage'][0].__setitem__('upstream', ['exchangeInfo@2020-01-01T00:00:00Z']),
                lambda d: d.__setitem__('source', 'synthetic-test')):
        d = copy.deepcopy(doc)
        mut(d)
        with pytest.raises(R.RunnerError):
            R.PitRules(d, R.canon(d), RSRC)
    d = copy.deepcopy(doc)                                            # omitted row: coverage says 2 intervals
    d['provenance']['coverage'][0]['rows'] = 2
    r = R.PitRules(d, R.canon(d), RSRC)
    assert r.bad_coverage == 1 and r.identity()['bad_coverage_entries'] == 1
    with pytest.raises(R.Incomplete, match='provenance_uncovered'):
        r.lookup(SYMS[0], D0)
    assert r.lookup(SYMS[1], D0)['tick'] == RULE['tick']
    d = copy.deepcopy(doc)                                            # a symbol without declared coverage
    d['provenance']['coverage'] = [c for c in d['provenance']['coverage'] if c['symbol'] != SYMS[2]]
    with pytest.raises(R.Incomplete, match='provenance_uncovered'):
        R.PitRules(d, R.canon(d), RSRC).lookup(SYMS[2], D0)


def test_uncovered_rules_fail_the_data_gate_even_below_two_percent(tmp_path):
    d = rules_doc()
    d['provenance']['coverage'][0]['rows'] = 2                        # one symbol of 18 voided -> few episodes
    r = R.PitRules(d, R.canon(d), RSRC)
    summ = _run(tmp_path / 'u', rules=r, only_keys=['walk_forward[0]'])
    folds = [summ['walk_forward'][s]['data']['folds']['walk_forward[0]'] for s in R.SIDES]
    assert sum(f['gate_failing_incomplete'] for f in folds) > 0
    assert all(not f['ok'] or f['gate_failing_incomplete'] == 0 for f in folds)
    assert any(summ['walk_forward'][s]['verdict'] == 'INCOMPLETE' for s in R.SIDES)


FUND_SEP = 'um/monthly/fundingRate/AUSDT/AUSDT-fundingRate-2026-09.zip'
K4_SEP = 'um/monthly/klines/AUSDT/4h/AUSDT-4h-2026-09.zip'


def straddle_doc(series, cov_rows=(1, 1)):
    cov = [{'symbol': 'AUSDT', 'series': 'funding', 'from': '2026-09-01T00:00:00Z', 'to': '2026-09-28T00:00:00Z',
            'rows': cov_rows[0], 'upstream': [FUND_SEP]},
           {'symbol': 'AUSDT', 'series': '4h', 'from': '2026-09-01T00:00:00Z', 'to': '2026-09-28T00:00:00Z',
            'rows': cov_rows[1], 'upstream': [K4_SEP]}]
    return {'format': R.STRADDLE_FORMAT, 'cutoff': '2026-09-28T00:00:00Z',
            'provenance': prov('binance-vision-archive', [(FUND_SEP, '1' * 64), (K4_SEP, '2' * 64)], cov),
            'series': series}


def test_straddle_artifact_is_pre_cutoff_only_and_hash_bound():
    cut = S.parse_utc('2026-09-28T00:00:00Z')
    t = S.parse_utc('2026-09-27T16:00:00Z')
    doc = straddle_doc({'funding': {'AUSDT': [[t + 5, 1e-4, 8]]}, '4h': {'AUSDT': [[t, 1, 2, 0.5, 1.5, 3, 4]]}})
    art = R.StraddleArtifact(doc, R.canon(doc), cut)
    assert art.bad_coverage == 0 and art.unauthenticated == 2           # nothing is covered until a source vouches
    assert not art.provenance.covered('AUSDT', 'funding', S.parse_utc('2026-09-01T00:00:00Z'), cut)
    assert art.sha256 == hashlib.sha256(R.canon(doc)).hexdigest()
    assert [r.time_ms for r in art.slice('AUSDT', 'funding', t, cut)] == [t + 5]
    for bad in ([[cut, 0.0, 8]], [[S.parse_utc('2026-08-31T16:00:00Z'), 0.0, 8]]):
        d = copy.deepcopy(doc)
        d['series']['funding']['AUSDT'] = bad
        with pytest.raises(R.HoldoutRefused):
            R.StraddleArtifact(d, R.canon(d), cut)
    d = copy.deepcopy(doc)
    d['series']['4h']['AUSDT'] = [[S.parse_utc('2026-09-27T22:00:00Z') - 2 * H, 1, 2, 0.5, 1.5, 3, 4]]   # closes 00:00
    R.StraddleArtifact(d, R.canon(d), cut)
    d['series']['4h']['AUSDT'] = [[S.parse_utc('2026-09-27T21:00:00Z'), 1, 2, 0.5, 1.5, 3, 4]]          # closes 01:00
    with pytest.raises(R.HoldoutRefused):
        R.StraddleArtifact(d, R.canon(d), cut)
    with pytest.raises(R.HoldoutRefused):
        R.StraddleArtifact(dict(doc, cutoff='2026-09-01T00:00:00Z'), b'', cut)


def test_straddle_provenance_mutants():
    cut = S.parse_utc('2026-09-28T00:00:00Z')
    t = S.parse_utc('2026-09-27T16:00:00Z')
    series = {'funding': {'AUSDT': [[t + 5, 1e-4, 8]]}, '4h': {'AUSDT': [[t, 1, 2, 0.5, 1.5, 3, 4]]}}
    d = straddle_doc(series, cov_rows=(2, 1))                         # omitted row: count mismatch voids the entry
    art = R.StraddleArtifact(d, R.canon(d), cut)
    assert art.bad_coverage == 1 and [e[3] for e in art.provenance.coverage[('AUSDT', 'funding')]] == [False]
    assert [e[3] for e in art.provenance.coverage[('AUSDT', '4h')]] == [True]
    for mut in (lambda x: x['provenance'].__setitem__('source', 'data.binance.vision'),          # free text
                lambda x: x['provenance']['upstream_files'][0].__setitem__('sha256', 'Z' * 64),
                lambda x: x['provenance']['coverage'][0].__setitem__('upstream', [K4_SEP]),    # wrong series file
                lambda x: x['provenance']['coverage'].pop(1),                               # undeclared 4h rows
                lambda x: x.pop('provenance')):
        x = copy.deepcopy(straddle_doc(series))
        mut(x)
        with pytest.raises(R.RunnerError):
            R.StraddleArtifact(x, R.canon(x), cut)


def test_straddle_artifact_binds_manifest_and_upstream_hashes_and_covers_reads(tmp_path):
    store = tmp_path / 'store'
    cut = S.parse_utc('2021-02-10T00:00:00Z')
    files = []
    for y_m, lo, n in (('2021-01', '2021-01-01T00:00:00Z', 93), ('2021-02', '2021-02-01T00:00:00Z', 84)):
        ts = [S.parse_utc(lo) + i * 8 * H + 5 for i in range(n)]
        rel = f'um/monthly/fundingRate/AAAUSDT/AAAUSDT-fundingRate-{y_m}.zip'
        csv = 'calc_time,funding_interval_hours,last_funding_rate\n' + ''.join(f'{t},8,0.0001\n' for t in ts)
        files.append({'path': rel, 'series': 'fundingRate', 'symbol': 'AAAUSDT', 'interval': None,
                      'sha256': _zip(store, rel, csv), 'bytes': 1, 'rows': n,
                      'first_open_ms': ts[0], 'last_open_ms': ts[-1], 'source': {}})
    man = tmp_path / 'm.json.gz'
    with gzip.open(man, 'wt', encoding='utf-8') as f:
        f.write(json.dumps({'format': 'x', 'files': files}, indent=1))
    feb = S.parse_utc('2021-02-01T00:00:00Z')
    pre = [[feb + i * 8 * H + 5, 1e-4, 8] for i in range(27)]           # 9 days x 3 pre-cutoff rows
    msha = R.file_sha(str(man))

    def art(upstream_sha=files[1]['sha256'], manifest_sha=msha, rows=27, name=files[1]['path'], cited=None):
        cited = cited or [(name, upstream_sha)]
        cov = [{'symbol': 'AAAUSDT', 'series': 'funding', 'from': '2021-02-01T00:00:00Z',
                'to': '2021-02-10T00:00:00Z', 'rows': rows, 'upstream': [c[0] for c in cited]}]
        doc = {'format': R.STRADDLE_FORMAT, 'cutoff': S.utc(cut),
               'provenance': prov('binance-vision-archive', cited, cov, manifest_sha),
               'series': {'funding': {'AAAUSDT': pre}}}
        return R.StraddleArtifact(doc, R.canon(doc), cut)
    src = R.ArchiveSource(str(man), str(store), ['AAAUSDT'], cut)
    with pytest.raises(R.RunnerError, match='upstream hash'):
        src.attach_straddle(art(upstream_sha='9' * 64))
    with pytest.raises(R.RunnerError, match='source manifest'):
        src.attach_straddle(art(manifest_sha='9' * 64))
    src.attach_straddle(art())
    rows = src.funding('AAAUSDT', S.parse_utc('2021-01-31T00:00:00Z'), S.parse_utc('2021-02-02T00:00:00Z'))
    assert len(rows) == 6 and src.identity()['straddle']['sha256'] == src.straddle.sha256
    src.attach_straddle(art(rows=28))                                 # count mismatch: every read there is unproven
    with pytest.raises(R.Incomplete, match='provenance_uncovered'):
        src.funding('AAAUSDT', feb, S.parse_utc('2021-02-02T00:00:00Z'))
    absent = 'um/monthly/fundingRate/AAAUSDT/AAAUSDT-fundingRate-2020-12.zip'
    with pytest.raises(R.RunnerError, match='absent from the run source manifest'):
        src.attach_straddle(art(name=absent, upstream_sha='9' * 64))
    src.attach_straddle(art(name=files[0]['path'], upstream_sha=files[0]['sha256']))   # January file for February
    assert src.straddle.unauthenticated == 1                          # time-disjoint: supports nothing
    with pytest.raises(R.Incomplete, match='provenance_uncovered'):
        src.funding('AAAUSDT', feb, S.parse_utc('2021-02-02T00:00:00Z'))
    days = [f'um/daily/fundingRate/AAAUSDT/AAAUSDT-fundingRate-2021-02-{d:02d}.zip' for d in range(1, 10)]
    daily_files = [(n, f'{i:064x}', f'2021-02-{i + 1:02d}T00:00:00Z') for i, n in enumerate(days, 1)]
    by_day = art(manifest_sha=msha, cited=daily_files)
    src.attach_straddle(by_day)                                       # no bound daily manifest: unauthenticated
    assert by_day.unauthenticated == 1 and src.identity()['daily_source_manifest'] is None
    with pytest.raises(R.Incomplete, match='provenance_uncovered'):
        src.funding('AAAUSDT', feb, S.parse_utc('2021-02-02T00:00:00Z'))
    daily = source_manifest('binance-vision-archive', daily_files)
    src.attach_straddle(by_day, daily)
    assert by_day.unauthenticated == 0 and src.identity()['daily_source_manifest']['sha256'] == daily.sha256
    assert len(src.funding('AAAUSDT', feb, S.parse_utc('2021-02-02T00:00:00Z'))) == 3
    with pytest.raises(R.RunnerError, match='absent from the bound daily'):
        src.attach_straddle(by_day, source_manifest('binance-vision-archive', daily_files[1:]))
    with pytest.raises(R.RunnerError, match='differ from the daily'):
        src.attach_straddle(by_day, source_manifest('binance-vision-archive',
                                                    [daily_files[0][:1] + ('f' * 64, daily_files[0][2])]
                                                    + daily_files[1:]))
    late = [(n, h, '2021-02-11T00:00:00Z' if i == 8 else a) for i, (n, h, a) in enumerate(daily_files)]
    src.attach_straddle(art(manifest_sha=msha, cited=late), source_manifest('binance-vision-archive', late))
    assert src.straddle.unauthenticated == 1                          # a file published after the cutoff is not used
    with pytest.raises(R.RunnerError, match='as_of precedes'):         # captured before its own data ended
        source_manifest('binance-vision-archive', [(days[0], '1' * 64, '2021-02-01T12:00:00Z')])
    with pytest.raises(R.RunnerError, match='expected value'):
        source_manifest('binance-vision-archive', daily_files, expect=None)


def test_rules_need_a_bound_source_manifest_and_causal_snapshots(tmp_path):
    doc = rules_doc()
    with pytest.raises(R.RunnerError, match='expected value'):        # unbound rules-source manifest
        source_manifest('binance-um-exchangeinfo-snapshots', SNAP_FILES, expect=None)
    with pytest.raises(R.RunnerError, match='expected value'):
        source_manifest('binance-um-exchangeinfo-snapshots', SNAP_FILES, expect='0' * 64)
    other = source_manifest('binance-um-exchangeinfo-snapshots', SNAP_FILES[:1])
    with pytest.raises(R.RunnerError, match='another rules-source manifest'):   # mismatched manifest
        R.PitRules(doc, R.canon(doc), other)
    d = copy.deepcopy(doc)
    d['provenance']['source_manifest_sha256'] = other.sha256
    with pytest.raises(R.RunnerError, match='absent'):                # cited snapshot absent from the manifest
        R.PitRules(d, R.canon(d), other)
    d = copy.deepcopy(doc)
    d['provenance']['upstream_files'][0]['sha256'] = '0' * 64
    with pytest.raises(R.RunnerError, match='differ'):                # snapshot hash mismatch
        R.PitRules(d, R.canon(d), RSRC)
    with pytest.raises(R.RunnerError, match='capture time'):
        source_manifest('binance-um-exchangeinfo-snapshots', [(SNAP, 'a' * 64, '2019-02-01T00:00:00Z', 'x.json')])
    with pytest.raises(R.RunnerError, match='exactly'):               # a snapshot entry without a readable path
        source_manifest('binance-um-exchangeinfo-snapshots', [(SNAP, 'a' * 64, '2019-01-01T00:00:00Z')])
    unbound = R.PitRules(doc, R.canon(doc))                           # no source manifest: nothing authenticated
    assert unbound.unauthenticated == len(SYMS)
    with pytest.raises(R.Incomplete, match='provenance_uncovered'):
        unbound.lookup(SYMS[0], D0)
    with pytest.raises(R.RunnerError, match='rules-source manifest'):
        _run(tmp_path / 'u', rules=unbound)
    # a later snapshot (captured 2021-03-02) claimed for an interval starting 2021-03-01: backfill, unsupported
    a = dict(RULE, **{'from': '2019-01-01T00:00:00Z', 'to': '2021-03-01T00:00:00Z'})
    b = dict(RULE, snapshot='exchangeInfo@2021-03-02T00:00:00Z',
             **{'from': '2021-03-01T00:00:00Z', 'to': '2027-01-01T00:00:00Z'})
    r = pit({SYMS[0]: [a, b]})
    assert r.unsupported == 1 and r.at(SYMS[0], D0 + 4 * H) is None and r.bad_coverage == 1
    with pytest.raises(R.Incomplete, match='provenance_uncovered'):
        r.lookup(SYMS[0], D0 + 4 * H)
    ok = pit({SYMS[0]: [a, dict(b, **{'from': '2021-03-02T00:00:00Z'})], })   # gap before it: also uncovered
    assert ok.unsupported == 0 and ok.lookup(SYMS[0], S.parse_utc('2021-03-02T04:00:00Z'))['tick'] == RULE['tick']


class LossSynth(Synth):
    """Funding for four symbols goes missing over a stretch of fold 0: data-loss no-cohort days."""
    def funding(self, s, lo, hi):
        rows = super().funding(s, lo, hi)
        a, b = S.parse_utc('2021-02-01T00:00:00Z'), S.parse_utc('2021-02-10T00:00:00Z')
        return [r for r in rows if not (s in SYMS[:4] and a <= r.time_ms < b)]


def test_data_loss_no_cohort_days_are_gated(tmp_path):
    summ = _run(tmp_path / 'l', src=LossSynth(), only_keys=['walk_forward[0]'])
    f = summ['walk_forward']['short']['data']['folds']['walk_forward[0]']
    assert f['data_loss_no_cohort_days'] > 0 and f['ok'] is False
    assert summ['walk_forward']['short']['verdict'] == 'INCOMPLETE'
    assert summ['counts']['walk_forward[0]']['data_loss_no_cohort_days'] == f['data_loss_no_cohort_days']


def _mutate_rows(monkeypatch, fn):
    real_replay = R.replay

    def mut(side, D, st, *a, **kw):
        return fn(side, st, real_replay(side, D, st, *a, **kw))
    monkeypatch.setattr(R, 'replay', mut)


def test_stress_only_incomplete_counts_in_the_data_gate(tmp_path, monkeypatch):
    def fn(side, st, ep):
        if side == 'short' and st.name == 'all_variable_costs_x1.5':
            raise R.Incomplete('incomplete.gap', ep['entry_ms'])
        return ep
    _mutate_rows(monkeypatch, fn)
    summ = _run(tmp_path / 'x', only_keys=['walk_forward[0]'])
    f = summ['walk_forward']['short']['data']['folds']['walk_forward[0]']
    assert f['incomplete_share']['all_variable_costs_x1.5'] == 1.0 and f['incomplete_share']['base_x1'] == 0.0
    assert f['stress_only_incomplete'] > 0 and summ['walk_forward']['short']['verdict'] == 'INCOMPLETE'
    assert summ['walk_forward']['long']['checks']['data'] is True


def test_stress_episode_identity_mismatch_is_incomplete(tmp_path, monkeypatch):
    def fn(side, st, ep):
        if side == 'long' and st.name == 'all_variable_costs_x2':
            ep = dict(ep, entry_ms=ep['entry_ms'] + TF)                   # a different episode under the stress row
        return ep
    _mutate_rows(monkeypatch, fn)
    summ = _run(tmp_path / 'y', only_keys=['walk_forward[0]'])
    f = summ['walk_forward']['long']['data']['folds']['walk_forward[0]']
    assert f['identity_mismatch'] > 0 and summ['walk_forward']['long']['verdict'] == 'INCOMPLETE'


class NoVolumeSynth(Synth):
    def _bars(self, s, iv, lo, hi):
        return [b._replace(quote_volume=None) if iv == TF else b for b in super()._bars(s, iv, lo, hi)]


def test_missing_capacity_inputs_cannot_evade_the_gate(tmp_path):
    summ = _run(tmp_path / 'c', src=NoVolumeSynth(), only_keys=['walk_forward[0]'])
    w = summ['walk_forward']['short']
    assert w['capacity_inputs_missing'] > 0 and w['median_notional_over_qv24'] is None
    assert w['checks']['capacity'] is False
    assert summ['splits']['walk_forward[0]']['short']['rows']['base_x1']['capacity_inputs_missing'] > 0


def test_drawdown_and_underwater_continue_across_a_fold_boundary():
    d = [S.parse_utc('2021-01-01T00:00:00Z') + i * DAY for i in range(20)]
    f0 = SimpleNamespace(path=[(d[0], 1.0, True), (d[5], 0.9, True)], eq=9000.0, e0=10000.0, first_D=d[0], last_D=d[5])
    f1 = SimpleNamespace(path=[(d[10], 0.95, True), (d[15], 0.97, True), (d[19], 1.2, True)], eq=12000.0,
                         e0=10000.0, first_D=d[10], last_D=d[19])
    ch = R.chain_folds([f0, f1])
    assert ch['max_equity_dd'] == pytest.approx(1 - 0.9 * 0.95)          # 14.5%: not reset at the boundary
    assert ch['max_equity_dd'] > max(0.10, 0.05)                         # both fold-local values are smaller
    assert ch['longest_underwater_days'] == 14                            # d[5] -> recovery d[19], across the boundary
    assert R.chain_folds([f1])['longest_underwater_days'] == 9            # fold-local would start the clock at d[10]
    assert ch['growth'] == pytest.approx(0.9 * 1.2)
    rec = SimpleNamespace(path=[(d[10], 1.2, True)], eq=12000.0, e0=10000.0, first_D=d[10], last_D=d[10])
    assert R.chain_folds([f0, rec])['longest_underwater_days'] == 5      # recovered at the first point of fold 1


def test_private_high_working_set_low_trips_the_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(R, '_mem_counters', lambda: (10.0, 10_000.0))
    assert R.rss_mb() == 10_000.0
    with pytest.raises(R.MemoryLimit):
        _run(tmp_path / 'pv', max_rss_mb=500)
    assert not (tmp_path / 'pv' / 'days.jsonl').read_bytes()
    monkeypatch.setattr(R, '_mem_counters', lambda: (_ for _ in ()).throw(OSError('no counters')))
    with pytest.raises(R.RunnerError, match='measure'):
        R.rss_mb()
    monkeypatch.setattr(R, '_mem_counters', lambda: (0.0, 0.0))
    with pytest.raises(R.RunnerError, match='measure'):
        R.rss_mb()


def test_real_memory_counters_report_private_and_working_set():
    ws, pv = R._mem_counters()
    assert ws > 0 and pv > 0 and R.rss_mb() == max(ws, pv)


def _fund_store(tmp_path, months, member=None):
    store = tmp_path / 'store'
    files = []
    for y_m in months:
        lo = S.parse_utc(y_m + '-01T00:00:00Z')
        ts = [lo + i * 8 * H + 5 for i in range(84)]
        rel = f'um/monthly/fundingRate/AAAUSDT/AAAUSDT-fundingRate-{y_m}.zip'
        csv = member if member is not None else \
            'calc_time,funding_interval_hours,last_funding_rate\n' + ''.join(f'{t},8,0.0001\n' for t in ts)
        p = store / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(p, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr(rel.rsplit('/', 1)[1][:-4] + '.csv', csv)
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        (store / (rel + '.ok')).write_text(h)
        files.append({'path': rel, 'series': 'fundingRate', 'symbol': 'AAAUSDT', 'interval': None, 'sha256': h,
                      'bytes': 1, 'rows': 84, 'first_open_ms': ts[0], 'last_open_ms': ts[-1], 'source': {}})
    man = tmp_path / 'm.json.gz'
    with gzip.open(man, 'wt', encoding='utf-8') as f:
        f.write(json.dumps({'format': 'x', 'files': files}, indent=1))
    return R.ArchiveSource(str(man), str(store), ['AAAUSDT'], S.parse_utc('2022-01-01T00:00:00Z'))


def test_small_archive_that_expands_past_the_budget_is_refused_before_the_peak(tmp_path, monkeypatch):
    bomb = '0' * (20 * 2 ** 20)                                       # ~20 KB compressed, 20 MB expanded
    src = _fund_store(tmp_path, ['2021-02'], member=bomb)
    zp = tmp_path / 'store' / 'um/monthly/fundingRate/AAAUSDT/AAAUSDT-fundingRate-2021-02.zip'
    assert zp.stat().st_size < 2 ** 20
    monkeypatch.setattr(R, '_mem_counters', lambda: (90.0, 90.0))      # 10 MB of a 100 MB cap remain
    src.set_memory(lambda: None, 100)

    def never(*a, **k):
        raise AssertionError('expanded before admission')
    monkeypatch.setattr(src, '_stream_rows', never)
    with pytest.raises(R.MemoryLimit, match='before decompressing'):
        src.funding('AAAUSDT', S.parse_utc('2021-02-02T00:00:00Z'), S.parse_utc('2021-02-03T00:00:00Z'))


def test_archive_rows_stream_with_checkpoints_and_cache_evicts_by_measured_bytes(tmp_path, monkeypatch):
    src = _fund_store(tmp_path, ['2021-02', '2021-03', '2021-04'])
    calls = []
    src.set_memory(lambda: calls.append(1), 8192)
    src.CHECK_ROWS = 10
    assert src.cache_budget == 8192 * 2 ** 20 // 4
    rows = src.funding('AAAUSDT', S.parse_utc('2021-02-02T00:00:00Z'), S.parse_utc('2021-02-03T00:00:00Z'))
    assert len(rows) == 3 and len(calls) >= 84 // 10 + 2               # per-N-rows checkpoints + before/after
    one = src.cache_bytes
    assert one == next(iter(src._lru.values()))[2] and one > 84 * 100  # measured, not a per-month estimate
    src.cache_budget = int(one * 1.5)                                  # room for one measured month only
    src.funding('AAAUSDT', S.parse_utc('2021-03-02T00:00:00Z'), S.parse_utc('2021-03-03T00:00:00Z'))
    src.funding('AAAUSDT', S.parse_utc('2021-04-02T00:00:00Z'), S.parse_utc('2021-04-03T00:00:00Z'))
    assert list(src._lru) == ['um/monthly/fundingRate/AAAUSDT/AAAUSDT-fundingRate-2021-04.zip']
    assert src.cache_bytes == src._lru[list(src._lru)[0]][2]


@pytest.mark.parametrize('name,value', [('BOOT_N', 9999), ('BOOT_SEED', 1), ('BOOT_BLOCK', 20), ('B0_BOOKS', 999),
                                        ('ENTRY_OFFSET', 8 * H), ('AGE_DAYS', 29), ('DAILY_BARS', 14),
                                        ('SLIP_FLOOR_BPS', 4.0), ('SLIP_C_CRYPTO', 0.06), ('HOLM_ALPHA', 0.1),
                                        ('DATA_MAX_INCOMPLETE', 0.05), ('LOOKBACK_BARS', 170)])
def test_policy_constant_drift_in_code_refuses(monkeypatch, name, value):
    monkeypatch.setattr(R, name, value)
    with pytest.raises(R.RunnerError, match='policy drift'):
        R.params_of(real()[0])


def test_policy_drift_in_sources_refuses(monkeypatch):
    p, _ = real()
    for path, old, new in [(('gates', 'expectancy', 'lcb'), 'seed 20261028', 'seed 1'),
                           (('costs', 'stress_rows', 'all_variable_costs_x2'), 'fees x2', 'fees x3'),
                           (('costs', 'fee'), 'taker 0.05%', 'taker 0.04%'),
                           (('execution', 'entry'), '04:00 UTC', '08:00 UTC')]:
        q = copy.deepcopy(p)
        node = q
        for k in path[:-1]:
            node = node[k]
        assert old in node[path[-1]]
        node[path[-1]] = node[path[-1]].replace(old, new)
        with pytest.raises(R.RunnerError, match='policy drift'):
            R.params_of(q)
    q = copy.deepcopy(p)
    q['signal']['availability_lag_seconds'] = 4 * 3600 + 1                 # entry would move to 08:00
    with pytest.raises(R.RunnerError, match='policy drift'):
        R.params_of(q)
    with pytest.raises(R.RunnerError, match='costs_sha256'):
        R.params_of(p, costs_sha='0' * 64)
    acc = json.loads((ROOT / 'research_evidence' / 'accounting' / 'strat00-accounting-inputs-v1.json').read_bytes())
    acc['costs']['slip_c'] = 0.07
    with pytest.raises(R.RunnerError, match='policy drift'):
        R.params_of(p, accounting=acc)
    monkeypatch.setitem(R.STRESS_ROWS, 'all_variable_costs_x2', R.Stress('all_variable_costs_x2', mult=1.9))
    with pytest.raises(R.RunnerError, match='stress_rows'):
        R.params_of(p)


def test_max_qty_and_max_notional_refuse_and_count():
    q = prm()
    ep = R.replay('long', D0, BASE, 0.5, _day(FLAT), ra, q, D0 + 10 * DAY)
    ep['side'] = 'long'
    for rule_key, lim, code in [('rule', {'max_notional': 100.0}, 'above_max_notional'),
                                ('rule', {'max_qty': 0.01}, 'above_max_qty'),
                                ('rule_exit', {'max_qty': 0.01}, 'exit_above_max_qty'),
                                ('rule_exit', {'max_notional': 100.0}, 'exit_above_max_notional')]:
        b = R.Book(q)
        b.cohort(D0, [{'symbol': 'X', 'f24': 0.0, 'qv24': 1e9, 'ep': dict(ep, **{rule_key: dict(ep[rule_key], **lim)})}])
        assert b.refused == {code: 1} and b.r == [], code
    b = R.Book(q)
    b.cohort(D0, [{'symbol': 'X', 'f24': 0.0, 'qv24': 1e9, 'ep': ep}])
    assert b.refused == {} and len(b.r) == 1
    iv = dict(RULE, **{'from': '2019-01-01T00:00:00Z', 'to': '2027-01-01T00:00:00Z'})
    for bad in ({k: v for k, v in iv.items() if k != 'max_qty'}, dict(iv, max_notional=0.0)):
        with pytest.raises(R.RunnerError):
            pit({SYMS[0]: [bad]})
    late = pit({SYMS[0]: [dict(iv, to='2021-03-01T00:00:00Z')]})               # no effective interval at entry
    ep2 = R.safe_replay('long', D0, BASE, 0.5, _day(FLAT), lambda t: late.at(SYMS[0], t), q, D0 + 10 * DAY)
    assert ep2['why'] == 'rules_uncovered'


# ------------------------------------------------------------------ rule values parsed from the cited snapshot
def test_interval_values_must_equal_the_tuple_parsed_from_the_cited_snapshot():
    doc = rules_doc()
    for key, val in (('tick', 0.5), ('step', 0.01), ('min_qty', 0.002), ('min_notional', 6.0), ('max_qty', 1e8),
                     ('max_notional', 1e11)):
        d = copy.deepcopy(doc)                                        # hash-valid snapshot, altered claimed value
        d['symbols'][SYMS[0]][0][key] = val
        with pytest.raises(R.RunnerError, match='differ from snapshot'):
            R.PitRules(d, R.canon(d), RSRC)
    ident = RULES.identity()
    assert set(ident['snapshot_tuple_digests']) == {SNAP} and ident['unsourced_intervals'] == 0
    parsed = RSRC.snapshot_rules(SNAP, set(SYMS))
    assert parsed[SYMS[0]] == tuple(float(RULE[k]) for k in R.RULE_KEYS)
    assert ident['snapshot_tuple_digests'][SNAP] == R.sha(R.canon({k: list(v) for k, v in sorted(parsed.items())}))


def test_snapshot_missing_symbol_or_field_leaves_the_interval_unsupported():
    r = pit(drop={SYMS[1]})                                           # the snapshot does not list S01
    assert r.unsourced == 1 and r.identity()['unsourced_intervals'] == 1
    with pytest.raises(R.Incomplete, match='provenance_uncovered'):
        r.lookup(SYMS[1], D0)
    assert r.lookup(SYMS[0], D0)['tick'] == RULE['tick']
    for strip in ('MIN_NOTIONAL', ('MARKET_LOT_SIZE', 'stepSize'), ('MARKET_LOT_SIZE', 'minQty'), 'MARKET_LOT_SIZE'):
        r = pit(strip={SYMS[2]: (strip,)})                            # a missing filter field is never defaulted
        assert r.unsourced == 1 and r.at(SYMS[2], D0) is None
        with pytest.raises(R.Incomplete, match='provenance_uncovered'):
            r.lookup(SYMS[2], D0)


def test_snapshot_field_sources_and_max_notional_derivation():
    ent = snap_entry('X', RULE)
    assert R.snapshot_rule_tuple(ent) == tuple(float(RULE[k]) for k in R.RULE_KEYS)
    no_max = {'symbol': 'X', 'filters': [f for f in ent['filters'] if f['filterType'] != 'MAX_NOTIONAL']}
    assert R.snapshot_rule_tuple(no_max) is None                      # no MAX_NOTIONAL and no maxPrice: no source
    for f in no_max['filters']:
        if f['filterType'] == 'PRICE_FILTER':
            f['maxPrice'] = '2000'
    assert R.snapshot_rule_tuple(no_max)[5] == 2000 * RULE['max_qty']  # derived maxPrice x MARKET_LOT_SIZE.maxQty
    dup = dict(ent, filters=ent['filters'] + [ent['filters'][0]])
    assert R.snapshot_rule_tuple(dup) is None
    bad = copy.deepcopy(ent)
    bad['filters'][0][next(k for k in bad['filters'][0] if k != 'filterType')] = 'NaN-ish'
    assert R.snapshot_rule_tuple(bad) is None
    with pytest.raises(R.RunnerError, match='twice'):
        R.snapshot_tuples({'symbols': [ent, ent]}, {'X'})
    with pytest.raises(R.RunnerError, match='exchangeInfo'):
        R.snapshot_tuples({'rateLimits': []}, {'X'})


def test_snapshot_is_read_only_through_the_manifest_hash_and_memory_checked(monkeypatch):
    doc, src, files = build({SYMS[0]: [dict(RULE, tick=0.125, **{'from': '2019-01-01T00:00:00Z',
                                                                  'to': '2027-01-01T00:00:00Z'})]})
    R.PitRules(doc, R.canon(doc), src)
    monkeypatch.setattr(R, '_mem_counters', lambda: (90.0, 90.0))
    with pytest.raises(R.MemoryLimit, match='before reading'):
        R.PitRules(doc, R.canon(doc), src, max_mb=90.0001)
    monkeypatch.undo()
    snap_path = os.path.join(SNAPDIR, dict((f[0], f[3]) for f in files)[SNAP])
    raw = open(snap_path, 'rb').read()
    try:
        with open(snap_path, 'wb') as fh:                             # same path, tampered bytes
            fh.write(raw.replace(b'0.125', b'0.250'))
        with pytest.raises(R.RunnerError, match='bytes differ'):
            R.PitRules(doc, R.canon(doc), src)
    finally:
        with open(snap_path, 'wb') as fh:
            fh.write(raw)



def test_market_lot_size_feeds_step_and_min_qty_not_lot_size():
    """Codex 6464073013: EDGE05 fills are market orders, so step / min_qty / max_qty come from MARKET_LOT_SIZE. A LOT_SIZE
    filter that differs must change nothing - in the canonical tuple and in the Book's floor / refusal path."""
    mkt = dict(RULE, step=0.1, min_qty=1.0)
    ent = snap_entry('X', mkt)
    lot = {'filterType': 'LOT_SIZE', 'stepSize': '0.001', 'minQty': '0.001', 'maxQty': '1e9'}   # the limit-order filter
    ent['filters'] = sorted(ent['filters'] + [lot], key=lambda f: f['filterType'])
    tup = dict(zip(R.RULE_KEYS, R.snapshot_rule_tuple(ent)))
    assert tup['step'] == 0.1 and tup['min_qty'] == 1.0                                    # market values, not LOT_SIZE
    assert R.snapshot_rule_tuple(dict(ent, filters=[f for f in ent['filters'] if f['filterType'] != 'LOT_SIZE'])) == \
        R.snapshot_rule_tuple(ent)                                                         # LOT_SIZE is irrelevant
    assert 'MARKET_LOT_SIZE.stepSize' in R.CONVENTIONS['snapshot_rule_values']
    # Book path: the market minQty refuses a size the LOT_SIZE minimum would have admitted
    q = prm()
    ep = R.replay('long', D0, BASE, 0.5, _day(FLAT), ra, q, D0 + 10 * DAY)
    ep['side'] = 'long'
    b_lot = R.Book(q)
    b_lot.cohort(D0, [{'symbol': 'X', 'f24': 0.0, 'qv24': 1e9, 'ep': dict(ep, rule=dict(ep['rule'], step=0.001, min_qty=0.001))}])
    huge = 1e9                                                                             # far above any sized qty
    b_mkt = R.Book(q)
    b_mkt.cohort(D0, [{'symbol': 'X', 'f24': 0.0, 'qv24': 1e9,
                       'ep': dict(ep, rule=dict(ep['rule'], step=0.001, min_qty=huge))}])
    assert b_lot.refused == {} and len(b_lot.r) == 1
    assert sum(b_mkt.refused.values()) == 1 and b_mkt.r == []                              # the market minimum binds


# ------------------------------------------------------------------ EDGE05-AB-01 (Codex ruling 6495672591)
AMEND_REL = 'research_evidence/amendments/edge05_dev_end_20260901.json'
RAW_SNAP_REL = 'research_evidence/rules/binance-um-exchangeinfo-mainnet-raw-20261011T151658Z.json'


def _git(repo, *a):
    return subprocess.run(['git', '-C', str(repo), *a], capture_output=True, check=True).stdout


def prospective_repo(tmp_path, drop=(), strip=None, commit=True):
    """A throwaway git repo holding one synthetic raw exchangeInfo snapshot: (repo, path, sha256)."""
    repo = tmp_path / 'repo'
    repo.mkdir(parents=True)
    _git(repo, 'init', '-q')
    strip = strip or {}
    doc = {'timezone': 'UTC', 'symbols': [snap_entry(s, RULE, strip.get(s, ())) for s in SYMS if s not in drop]}
    raw = R.canon(doc)
    p = repo / 'snap.json'
    p.write_bytes(raw)
    if commit:
        _git(repo, 'add', 'snap.json')
        _git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 's')
    return repo, p, R.sha(raw)


def test_prospective_rules_accepts_a_committed_snapshot_and_counts_skips(tmp_path):
    repo, p, h = prospective_repo(tmp_path, drop=(SYMS[1],), strip={SYMS[2]: (('MARKET_LOT_SIZE', 'maxQty'),)})
    pr = R.ProspectiveRules.load(str(p), h, SYMS, repo=str(repo))
    ident = pr.identity()
    assert ident['mode'] == ident['non_promotable_rules'] == 'prospective' and ident['ruling'] == '6495672591'
    assert ident['sha256'] == h and ident['snapshot_path'] == 'snap.json' and ident['rules_source']['sha256'] == h
    assert ident['skipped_symbols'] == {SYMS[1]: 'absent_from_snapshot', SYMS[2]: 'missing_or_non_numeric_field'}
    assert ident['skipped_by_reason'] == {'absent_from_snapshot': 1, 'missing_or_non_numeric_field': 1}
    assert ident['symbols_with_rules'] == len(SYMS) - 2
    early = S.parse_utc('2019-01-01T00:00:00Z')                      # causality bypassed: same tuple at every time
    assert pr.lookup(SYMS[0], early) == pr.lookup(SYMS[0], early + 10 ** 11)
    assert {k: pr.lookup(SYMS[0], early)[k] for k in R.RULE_KEYS} == RULE
    assert pr.lookup(SYMS[1], early) is None and pr.lookup(SYMS[2], early) is None    # never defaulted


def test_prospective_rules_refuses_uncommitted_modified_or_unbound_snapshots(tmp_path):
    repo, p, h = prospective_repo(tmp_path / 'a', commit=False)
    with pytest.raises(R.RunnerError, match='not committed'):
        R.ProspectiveRules.load(str(p), h, SYMS, repo=str(repo))
    repo, p, h = prospective_repo(tmp_path / 'b')
    for bad in (None, '', 'xyz'):
        with pytest.raises(R.RunnerError, match='expect-prospective-rules-sha256'):
            R.ProspectiveRules.load(str(p), bad, SYMS, repo=str(repo))
    with pytest.raises(R.RunnerError, match='differs from --expect-prospective'):
        R.ProspectiveRules.load(str(p), '0' * 64, SYMS, repo=str(repo))
    p.write_bytes(p.read_bytes() + b' ')
    with pytest.raises(R.RunnerError, match='differs from --expect-prospective'):    # modified: not the bound hash
        R.ProspectiveRules.load(str(p), h, SYMS, repo=str(repo))
    with pytest.raises(R.RunnerError, match='differ from HEAD'):                      # modified and re-bound: refused
        R.ProspectiveRules.load(str(p), R.sha(p.read_bytes()), SYMS, repo=str(repo))
    out = tmp_path / 'outside.json'
    out.write_bytes(b'{}')
    with pytest.raises(R.RunnerError, match='inside the repo'):
        R.ProspectiveRules.load(str(out), R.sha(b'{}'), SYMS, repo=str(repo))


@pytest.mark.parametrize('extra', [['--rules-artifact', 'r.json'], ['--rules-source-manifest', 'm.json'],
                                   ['--expect-rules-sha256', 'ab' * 32], ['--expect-rules-source-sha256', 'ab' * 32]])
def test_prospective_rules_cli_is_exclusive_with_the_pit_arguments(extra, capsys):
    rc = R.main(['--store', 'x', '--max-rss-mb', '100', '--dry-run', '--prospective-rules', RAW_SNAP_REL,
                 '--expect-prospective-rules-sha256', 'ab' * 32] + extra)
    assert rc == 3 and 'mutually exclusive' in capsys.readouterr().err
    rc = R.main(['--store', 'x', '--max-rss-mb', '100', '--dry-run', '--expect-prospective-rules-sha256', 'ab' * 32])
    assert rc == 3 and 'without --prospective-rules' in capsys.readouterr().err


def test_prospective_run_binds_identity_and_labels_every_packet(tmp_path):
    repo, p, h = prospective_repo(tmp_path / 'r')
    pr = R.ProspectiveRules.load(str(p), h, SYMS, repo=str(repo))
    summ = _run(tmp_path / 'p', rules=pr, only_keys=['walk_forward[1]'])
    ident = json.loads((tmp_path / 'p' / 'run.json').read_bytes())
    assert ident['rules'] == pr.identity() and ident['rules_mode'] == ident['non_promotable_rules'] == 'prospective'
    assert '6495672591' in ident['conventions']['prospective_rules']
    lines = (tmp_path / 'p' / 'days.jsonl').read_bytes().splitlines()
    assert lines and all(json.loads(ln)['non_promotable_rules'] == 'prospective' for ln in lines)
    assert summ['non_promotable_rules'] == summ['rules_mode'] == 'prospective' and summ['promotable'] is False
    assert 'PROSPECTIVE-EXCHANGE-RULES' in summ['labels'] and 'PIT-EXCHANGE-RULES' not in summ['labels']
    assert summ['rules_snapshot'] == {'path': 'snap.json', 'sha256': h} and summ['rules_skips']['count'] == 0
    for s in R.SIDES:
        wf = summ['walk_forward'][s]
        if 'promotion_x2_ok' in wf:
            assert wf['promotion_x2_ok'] is False and isinstance(wf['promotion_x2_ok_computed'], bool)
    repo2, p2, h2 = prospective_repo(tmp_path / 's', drop=(SYMS[4],))
    sk = R.ProspectiveRules.load(str(p2), h2, SYMS, repo=str(repo2))
    rep = _run(tmp_path / 'd', rules=sk, dry_run=True)
    assert rep['non_promotable_rules'] == 'prospective' and SYMS[4] in rep['rules_uncovered_days_by_symbol']
    assert rep['rules']['skipped_symbols'] == {SYMS[4]: 'absent_from_snapshot'} and not (tmp_path / 'd').exists()


def test_pit_default_path_carries_no_prospective_labels(full):
    _, b = full
    ident = json.loads(b['run.json'])
    assert 'rules_mode' not in ident and 'non_promotable_rules' not in ident
    assert all('non_promotable_rules' not in json.loads(ln) for ln in b['days.jsonl'].splitlines())
    summ = json.loads(b['summary.json'])
    assert 'PIT-EXCHANGE-RULES' in summ['labels'] and 'non_promotable_rules' not in summ


def test_committed_raw_snapshot_is_the_2026_10_11_mainnet_capture_and_parses():
    raw = (ROOT / RAW_SNAP_REL).read_bytes()
    derived = json.loads((ROOT / 'data' / 'exchange_rules_mainnet.json').read_bytes())
    assert R.sha(raw) == derived['raw_sha256'] and derived['fetched_at'].startswith('2026-10-11')
    p, _ = real()
    syms = R.CV.load_universe(str(ROOT / p['universe']['artifact']))
    t = R.snapshot_tuples(json.loads(raw), set(syms))
    assert set(t) == set(syms) and all(v is not None for v in t.values())


def test_committed_dev_end_amendment_binds_ruling_and_needs_no_straddle(tmp_path):
    p, raw = real()
    b = (ROOT / AMEND_REL).read_bytes()
    cutoff = R.params_of(p).cutoff_ms
    am = R.Amendment.load(str(ROOT / AMEND_REL), raw, cutoff, R.sha(b))
    assert am.doc['dev_end'] == '2026-09-01T00:00:00Z' and '6495672591' in am.identity()['signed_by']
    assert am.identity()['sha256'] == R.sha(b) and '6495672591' in am.doc['reason']
    with pytest.raises(R.RunnerError, match='expect-amendment'):
        R.Amendment.load(str(ROOT / AMEND_REL), raw, cutoff, '0' * 64)

    class SeptStraddle(Synth):                                        # the real never-opened month: Sep 2026
        straddle_from, straddle = S.parse_utc('2026-09-01T00:00:00Z'), None

    rep = R.run(str(tmp_path / 'x'), SeptStraddle(), SYMS, RULES, p, raw, dry_run=True, max_rss_mb=8192, code=CODE,
                amendment=am, expect_rules_sha256=RULES.sha256)
    assert rep['straddle_unresolved_from'] is None and rep['expected_artifact_mismatch'] == []
    assert rep['splits']['walk_forward[4]']['last_D'] < '2026-09-01'
    rep0 = R.run(str(tmp_path / 'x'), SeptStraddle(), SYMS, RULES, p, raw, dry_run=True, max_rss_mb=8192, code=CODE,
                 expect_rules_sha256=RULES.sha256)
    assert rep0['straddle_unresolved_from'] == '2026-09-01T00:00:00Z' and rep0['expected_artifact_mismatch'] == ['straddle']
    assert not (tmp_path / 'x').exists()
