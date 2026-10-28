"""breakout_pyramid_falsify.v1 runner, slice 1: synthetic bars only (no market data, no manifest load, no git call)."""
import copy
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'research'))
import bpyr_runner as R                                                                     # noqa: E402
import manifest as M                                                                        # noqa: E402

TF = R.TF
T0 = R.iso_ms('2021-01-04T00:00:00Z')
CLEAN = lambda *a: ('c' * 40 + '\n') if a[0] == 'rev-parse' else ''                        # noqa: E731
DIRTY = lambda *a: ('c' * 40 + '\n') if a[0] == 'rev-parse' else ' M tools/research/bpyr_runner.py\n'   # noqa: E731
NO_MANIFEST = lambda p, repo: None                                                          # noqa: E731  (never loads the 7 MB manifest)
RULE = {'step': 0.001, 'min_qty': 0.001, 'min_notional': 5.0}


@pytest.fixture(scope='module')
def pf():
    return R.preflight(git=CLEAN, manifest_check=NO_MANIFEST)


@pytest.fixture(scope='module')
def P(pf):
    return pf.prereg


def ledger():
    return R.load_jsonl(str(ROOT.joinpath(*R.LEDGER_REL))), R.load_jsonl(str(ROOT.joinpath(*R.REGISTRY_REL)))


def W(n=400, last=None, warm=1):
    """Test windows: three slices of 100 bars from T0; last-entry cutoff at bar `last` (default n - 40)."""
    last = n - 40 if last is None else last
    return R.Windows(T0, T0 + last * TF, T0 + n * TF, ((T0, T0 + 100 * TF), (T0 + 100 * TF, T0 + 200 * TF),
                                                        (T0 + 200 * TF, T0 + n * TF)), warm)


def mk(sym, ohlc, le=(), atr=1.0, slip=0.0, start=0, drop=(), funding=()):
    """Hand-built SymbolData: bar k opens at T0 + (start + k) * 4h; `drop` removes bar indices (a data gap)."""
    rows = [(T0 + (start + k) * TF, *x) for k, x in enumerate(ohlc) if k not in drop]
    keep = [k for k in range(len(ohlc)) if k not in drop]
    n = len(rows)
    return R.SymbolData(sym, [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows],
                        [r[4] for r in rows], [atr] * n, [k in le for k in keep], [True] * n, [slip] * n,
                        list(funding))


def flat(n, px=100.0, w=0.2):
    return [(px, px + w, px - w, px)] * n


# ------------------------------------------------------------------ preflight / identity
def test_preflight_binds_signed_contract_and_six_ledger_ids(pf):
    rows, _ = ledger()
    assert pf.prereg_sha256 == R.file_sha(str(ROOT.joinpath(*R.PREREG_REL))) == rows[0]['detail']['prereg_sha256']
    assert [r['detail']['id'] for r in rows] == list(R.LEDGER_IDS)
    assert pf.code['dirty'] is False and set(pf.code['modules']) == set(R.CODE_MODULES)
    assert pf.sources['fn_source_sha256'] == pf.prereg['identity']['fn_source_sha256']


def test_dirty_tree_refused_unless_test_flag():
    with pytest.raises(R.DirtyTree):
        R.preflight(git=DIRTY, manifest_check=NO_MANIFEST)
    assert R.preflight(git=DIRTY, manifest_check=NO_MANIFEST, allow_dirty_test_only=True).code['dirty'] is True
    with pytest.raises(R.PreflightError, match='commit'):
        R.preflight(git=lambda *a: '', manifest_check=NO_MANIFEST)


@pytest.mark.parametrize('mutate', [
    lambda rs: rs.reverse(),
    lambda rs: rs.pop(),
    lambda rs: rs[3]['detail'].__setitem__('id', 'S1T'),
    lambda rs: rs[3]['detail'].__setitem__('gated', True),
    lambda rs: rs[4].__setitem__('kind', 'variant'),
    lambda rs: rs[0]['detail'].__setitem__('prereg_sha256', '0' * 64),
    lambda rs: rs[2].__setitem__('prev', '1' * 64),
    lambda rs: rs[5].__setitem__('run_digest', 'f' * 64),
    lambda rs: rs.append(dict(rs[-1], seq=7)),
])
def test_any_ledger_id_deviation_fails_closed(P, pf, mutate):
    rows, reg = ledger()
    R.verify_ledger(P, pf.prereg_sha256, rows, reg)
    rs = copy.deepcopy(rows)
    mutate(rs)
    with pytest.raises(R.LedgerMismatch):
        R.verify_ledger(P, pf.prereg_sha256, rs, reg)
    with pytest.raises(R.LedgerMismatch):                                   # a contract edit moves the signed hash
        R.verify_ledger(P, '1' * 64, rows, reg)
    with pytest.raises(R.LedgerMismatch):
        R.verify_ledger(P, pf.prereg_sha256, rows, [r for r in reg if r.get('family') != R.LEDGER_FAMILY])


def test_prereg_tamper_and_stage_ids_fail_closed(P):
    for path, val, err in ((('signed',), False, R.PreregInvalid), (('boundaries', 'evaluation_run'), True, R.PreregInvalid),
                           (('trial_budget', 'gated_ids'), ['S1', 'S2', 'S3', 'S4'], R.LedgerMismatch)):
        q = copy.deepcopy(P)
        d = q
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = val
        with pytest.raises(err):
            R.verify_prereg(q)
    with pytest.raises(R.LedgerMismatch):
        R.stage_mgmt(P, 'S4')
    assert R.stage_mgmt(P, 'S1') == {'stop_atr': 2.0, 'max_bars': 42}


def test_code_cost_and_manifest_hash_bindings_fail_closed(P):
    R.verify_code(P)
    R.verify_costs(P)
    for k in ('fn_source_sha256', 'indicators_source_sha256', 'ema_source_sha256'):
        q = copy.deepcopy(P)
        q['identity'][k] = '0' * 64
        with pytest.raises(R.IdentityMismatch):
            R.verify_code(q)
    q = copy.deepcopy(P)
    q['stages']['S3']['mgmt']['pyramid']['n'] = 3
    with pytest.raises(R.IdentityMismatch):
        R.verify_code(q)
    q = copy.deepcopy(P)
    q['source']['costs_sha256'] = '0' * 64
    with pytest.raises(R.CostHashMismatch):
        R.verify_costs(q)
    fake = {'format': 'x', 'files': [{'path': 'a', 'sha256': '0' * 64}]}
    q = copy.deepcopy(P)
    q['source']['manifest_digest'] = M.digest_of(fake)
    R.verify_manifest(q, loader=lambda path: fake)
    with pytest.raises(R.ManifestMismatch):
        R.verify_manifest(P, loader=lambda path: fake)


def test_code_identity_uses_injected_git():
    ci = R.code_identity(git=DIRTY)
    assert ci['commit'] == 'c' * 40 and ci['dirty'] is True and len(ci['path_points_sha256']) == 64


def test_windows_are_the_signed_ones(P):
    w = R.Windows.of(P)
    assert w.last_entry == R.iso_ms('2026-08-29T00:00:00Z') and w.sealed == R.iso_ms('2026-09-28T00:00:00Z')
    assert w.dev_start == T0 and len(w.slices) == 3 and w.warmup_bars == 240
    q = copy.deepcopy(P)
    q['windows']['last_entry_cutoff']['bars'] = 179
    with pytest.raises(R.PreregInvalid):
        R.Windows.of(q)


# ------------------------------------------------------------------ signals / data preparation
def _walk(n, seed=7, start=T0 - 300 * TF):
    rng = np.random.default_rng(seed)
    cl = 100 * np.exp(np.cumsum(rng.normal(0.002, 0.02, n)))
    op = np.r_[cl[0], cl[:-1]]
    return [(start + k * TF, op[k], max(op[k], cl[k]) * 1.01, min(op[k], cl[k]) * 0.99, cl[k], 1.0) for k in range(n)]


def test_prepare_symbol_uses_live_signal_per_segment_and_refuses_the_seal(P):
    import pandas as pd
    import strategies as ST
    w = R.Windows.of(P)
    bars = _walk(700)
    sd = R.prepare_symbol('BTCUSDT', bars, w)
    df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
    assert sd.le == list(ST.sig_breakout_pyramid(ST.indicators(df), {}, {})['le']) and sum(sd.le) >= 5
    assert sd.elig[238] is False and sd.elig[239] is True and math.isnan(sd.slip[14]) and sd.slip[15] >= R.SLIP_FLOOR_BPS
    gap = R.prepare_symbol('BTCUSDT', bars[:400] + bars[401:], w)            # a gap restarts eligibility (no fill)
    assert gap.elig[399] is True and gap.elig[400] is False and gap.elig[638] is False and gap.elig[639] is True
    with pytest.raises(R.HoldoutRefused):
        R.prepare_symbol('BTCUSDT', bars + [(w.sealed, 1, 1, 1, 1, 1)], w)
    with pytest.raises(R.HoldoutRefused):
        R.prepare_symbol('BTCUSDT', bars, w, funding=[(w.sealed, 0.0001, 100.0)])


# ------------------------------------------------------------------ unit episode rules
def test_gap_stop_fills_at_the_open_in_phase_1(P):
    sd = mk('BTCUSDT', flat(2) + [(97.0, 97.5, 96.0, 97.2)] + flat(5), le={0})
    pos = R.replay_unit(sd, 1, R.stage_mgmt(P, 'S1'))
    assert pos.stop == 98.0 and pos.exit == (2, 'stop_gap', 2) and pos.fills[-1]['ref'] == 97.0 and pos.fills[-1]['gap']


def test_stop_first_beats_target_in_same_candle(P):
    sd = mk('BTCUSDT', flat(2) + [(100.0, 107.0, 97.0, 101.0)] + flat(3), le={0})
    pos = R.replay_unit(sd, 1, R.stage_mgmt(P, 'S1-T'))                     # 3R target 106 and stop 98 both in range
    assert pos.exit[1] == 'stop' and pos.fills[-1]['ref'] == 98.0
    sd = mk('BTCUSDT', flat(2) + [(100.0, 107.0, 99.0, 106.5)] + flat(3), le={0})
    assert R.replay_unit(sd, 1, R.stage_mgmt(P, 'S1-T')).exit[1] == 'tp'


def test_adds_fire_before_the_stop_and_raised_stop_is_live_later_in_the_bar(P):
    # red candle: open -> high (add at +1R = 102, trail ratchets to 102.5 - 3) -> low (raised stop 99.5)
    sd = mk('BTCUSDT', flat(2) + [(100.5, 102.5, 97.0, 97.5)] + flat(3), le={0})
    pos = R.replay_unit(sd, 1, R.stage_mgmt(P, 'S3'))
    assert [x['kind'] for x in pos.fills] == ['entry', 'add', 'exit']
    assert pos.fills[1]['ref'] == 102.0 and pos.fills[1]['qty'] == 0.5 and pos.fills[-1]['qty'] == 1.5
    assert pos.fills[-1]['ref'] == 99.5 and pos.exit[1] == 'stop'
    s2 = R.replay_unit(sd, 1, R.stage_mgmt(P, 'S2'))                       # S2 = S3 without the add
    assert [x['kind'] for x in s2.fills] == ['entry', 'exit'] and s2.fills[-1]['ref'] == 99.5


def test_s1_time_exit_at_the_open_after_42_bars(P):
    sd = mk('BTCUSDT', flat(60), le={0})
    pos = R.replay_unit(sd, 1, R.stage_mgmt(P, 'S1'))
    assert pos.exit == (43, 'time', 43) and sd.t[43] - sd.t[1] == 42 * TF


def test_boundary_and_gap_are_incomplete_never_closed(P):
    with pytest.raises(R.Incomplete, match='incomplete.boundary'):
        R.replay_unit(mk('BTCUSDT', flat(30), le={0}), 1, R.stage_mgmt(P, 'S1'))
    with pytest.raises(R.Incomplete, match='incomplete.gap'):
        R.replay_unit(mk('BTCUSDT', flat(60), le={0}, drop={10}), 1, R.stage_mgmt(P, 'S1'))
    with pytest.raises(R.Incomplete, match='incomplete.boundary'):            # S2/S3 have no cap: finite only by data
        R.replay_unit(mk('BTCUSDT', flat(200), le={0}), 1, R.stage_mgmt(P, 'S3'))


# ------------------------------------------------------------------ entry set E (fill-time re-arm, cutoff)
def test_rearm_is_decided_at_fill_time_after_phase_1(P):
    s1 = R.stage_mgmt(P, 'S1')
    # episode 1 fills at 1, gap-stopped at the open of 3; the signal at the close of 2 fills at 3 -> accepted
    gap = mk('BTCUSDT', flat(2) + [(99.0, 99.2, 98.8, 99.0), (97.0, 97.2, 96.8, 97.0)] + flat(60, 97.0), le={0, 2})
    E = R.entry_set({'BTCUSDT': gap}, W(), s1)
    assert [e['f'] for e in E['episodes'][:2]] == [1, 3]
    # same but the stop is hit INTRABAR in bar 3: the symbol is still open at that open -> the signal is skipped
    intra = mk('BTCUSDT', flat(2) + [(99.0, 99.2, 98.8, 99.0), (99.0, 99.2, 97.0, 97.5)] + flat(60, 97.5), le={0, 2})
    E = R.entry_set({'BTCUSDT': intra}, W(), s1)
    assert [e['f'] for e in E['episodes']] == [1] and E['episodes'][0]['free'] == 4


def test_last_entry_cutoff_and_incomplete_lock(P):
    s1 = R.stage_mgmt(P, 'S1')
    sd = mk('BTCUSDT', flat(400), le={359, 300})
    E = R.entry_set({'BTCUSDT': sd}, W(400, last=360), s1)                 # fill at 360 == cutoff: refused
    assert [e['f'] for e in E['episodes']] == [301] and E['incomplete'] == []
    sd = mk('BTCUSDT', flat(400), le={390})                                # still open at the seal: INCOMPLETE
    E = R.entry_set({'BTCUSDT': sd}, W(400, last=399), s1)
    assert [x['reason'] for x in E['incomplete']] == ['incomplete.boundary'] and E['episodes'] == []


def test_paired_replays_share_E_and_d3_is_adds_only(P):
    sd = mk('BTCUSDT', flat(2) + [(100.0, 100.5, 99.6, 100.2)] * 3 + [(100.2, 103.0, 100.0, 102.8),
            (102.8, 103.0, 95.0, 95.5)] + flat(10, 95.5), le={0})
    data = {'BTCUSDT': sd}
    E = R.entry_set(data, W(), R.stage_mgmt(P, 'S1'))
    reps = {s: R.unit_replays(data, E, P, s) for s in R.STAGE_IDS}
    assert {s: [e['f'] for e in r['episodes']] for s, r in reps.items()} == {s: [1] for s in R.STAGE_IDS}
    s2, s3 = reps['S2']['episodes'][0], reps['S3']['episodes'][0]
    assert s3['adds'] == 1 and s2['adds'] == 0 and s2['exit_j'] == s3['exit_j']
    assert s3['rows']['base_x1']['net_r'] != s2['rows']['base_x1']['net_r']


def test_stress_reprices_the_same_episode_and_never_inflates_received_funding(P):
    fund = [(T0 + 2 * TF, 0.001, 100.0), (T0 + 3 * TF, -0.001, 100.0)]
    sd = mk('BTCUSDT', flat(2) + [(97.0, 97.5, 96.0, 97.2)] + flat(5), le={0}, slip=10.0, funding=fund)
    sd2 = mk('BTCUSDT', flat(5) + [(97.0, 97.5, 96.0, 97.2)] + flat(5), le={0}, slip=10.0, funding=fund)
    pos = R.replay_unit(sd, 1, R.stage_mgmt(P, 'S1'))
    assert pos.funding == [] and len(R.replay_unit(sd2, 1, R.stage_mgmt(P, 'S1')).funding) == 2
    rows = {r: R.price_episode(pos, r) for r in R.ROWS}
    assert rows['base_x1']['gross'] == rows['all_variable_costs_x1.5']['gross'] == -3.0
    assert rows['all_variable_costs_x2']['net_r'] < rows['all_variable_costs_x1.5']['net_r'] < rows['base_x1']['net_r']
    assert R.funding_paid(0.001, 1, 100, 'all_variable_costs_x1.5') == pytest.approx(0.15)
    assert R.funding_paid(-0.001, 1, 100, 'all_variable_costs_x1.5') == pytest.approx(-0.1 / 1.5)
    assert R.funding_paid(0.001, 1, 100, 'funding_zero') == 0.0
    p2 = R.replay_unit(sd2, 1, R.stage_mgmt(P, 'S1'))                       # entry <= T < exit (exit open excluded)
    assert [e[0] for e in p2.funding] == [T0 + 2 * TF, T0 + 3 * TF]
    assert R.price_episode(p2, 'base_x1')['funding'] == pytest.approx(-0.1 + 0.1)


# ------------------------------------------------------------------ book: four phases, capacity, skips
def _book(P, data, stage='S1', rules=lambda s, t: RULE, w=None, **kw):
    return R.run_book(data, P, stage, w or W(), rules, **kw)


SYMS5 = ['ADAUSDT', 'BNBUSDT', 'BTCUSDT', 'ETHUSDT', 'SOLUSDT']


def test_capacity_uses_phase_1_state_only(P):
    # four positions open at bar 1; at bar 3 ADA gap-stops (phase 1) and BNB stops intrabar (phase 3).
    gap_bar, intra_bar = (97.0, 97.2, 96.8, 97.0), (99.0, 99.2, 97.0, 97.5)
    data = {s: mk(s, flat(3) + [gap_bar if s == 'ADAUSDT' else intra_bar if s == 'BNBUSDT' else flat(1)[0]] +
                  flat(60), le={0}) for s in SYMS5[:4]}
    data['SOLUSDT'] = mk('SOLUSDT', flat(64), le={0, 2})                    # signal at bar 0 (5th lot) and bar 2
    b = _book(P, data)
    entries = [(t, s) for t, ph, s, what in b['events'] if what == 'entry']
    assert entries[:4] == [(T0 + TF, s) for s in SYMS5[:4]] and b['skips']['skipped_max_open'] == 1
    assert (T0 + 3 * TF, 'SOLUSDT') in entries                            # ADA's phase-1 gap stop freed the slot


def test_intrabar_exit_never_frees_capacity_for_the_same_open(P):
    intra_bar = (99.0, 99.2, 97.0, 97.5)
    data = {s: mk(s, flat(3) + [intra_bar if s == 'ADAUSDT' else flat(1)[0]] + flat(60), le={0}) for s in SYMS5[:4]}
    data['SOLUSDT'] = mk('SOLUSDT', flat(64), le={2})
    b = _book(P, data)
    assert ('SOLUSDT' not in [s for t, ph, s, w in b['events'] if w == 'entry' and t == T0 + 3 * TF])
    assert b['skips']['skipped_max_open'] == 1
    order = [(t, ph) for t, ph, s, w in b['events']]
    assert order == sorted(order)                                         # per bar: phase 1 < 2 < 3, no lookahead


def test_open_symbol_skip_is_decided_at_fill_time(P):
    gap = mk('BTCUSDT', flat(2) + [(99.0, 99.2, 98.8, 99.0), (97.0, 97.2, 96.8, 97.0)] + flat(60, 97.0), le={0, 2})
    b = _book(P, {'BTCUSDT': gap})
    assert [t for t, ph, s, w in b['events'] if w == 'entry'][:2] == [T0 + TF, T0 + 3 * TF]
    intra = mk('BTCUSDT', flat(2) + [(99.0, 99.2, 98.8, 99.0), (99.0, 99.2, 97.0, 97.5)] + flat(60, 97.5), le={0, 2})
    b = _book(P, {'BTCUSDT': intra})
    assert b['skips']['skipped_open_symbol'] == 1


def test_gross_cap_and_filters_skip_never_scale(P):
    tiny = mk('BTCUSDT', flat(64), le={0}, atr=0.05)                         # 1% risk / 0.1 stop = 1000 units = 10x equity
    b = _book(P, {'BTCUSDT': tiny})
    assert b['skips']['skipped_gross_cap'] == 1 and b['trades'] == []
    b = _book(P, {'BTCUSDT': mk('BTCUSDT', flat(64), le={0})}, rules=lambda s, t: dict(RULE, min_notional=1e9))
    assert b['refusals'] == {'below_min_notional': 1} and b['trades'] == []
    b = _book(P, {'BTCUSDT': mk('BTCUSDT', flat(64), le={0})}, rules=lambda s, t: None)
    assert b['refusals'] == {'rules_uncovered': 1}


def test_add_gross_cap_refusal_is_counted(P):
    up = flat(2) + [(100.0, 100.3, 99.9, 100.2), (100.2, 103.0, 100.1, 102.9)] + flat(10, 102.9)
    data = {s: mk(s, up, le={0}, atr=0.4) for s in SYMS5[:4]}              # 4 x 1250 units x 100 = 5x equity at entry
    b = _book(P, data, stage='S3')
    assert b['skips']['skipped_gross_cap'] >= 1 and b['skips']['skipped_add_gross_cap'] >= 1


def test_book_boundary_incomplete_and_cutoff(P):
    b = _book(P, {'BTCUSDT': mk('BTCUSDT', flat(30), le={0})})
    assert [{k: x[k] for k in ('sym', 't_entry', 'reason')} for x in b['incomplete']] ==         [{'sym': 'BTCUSDT', 't_entry': T0 + TF, 'reason': 'incomplete.boundary'}]
    b = _book(P, {'BTCUSDT': mk('BTCUSDT', flat(100), le={50})}, w=W(100, last=51))
    assert b['events'] == [] and b['trades'] == []


# ------------------------------------------------------------------ B0
def _b0_data():
    rng = np.random.default_rng(3)
    out = {}
    for s in ('BTCUSDT', 'ETHUSDT'):
        cl = 100 + np.cumsum(rng.normal(0, 0.5, 400))
        ohlc = [(c0, max(c0, c1) + 0.3, min(c0, c1) - 0.3, c1) for c0, c1 in zip(np.r_[cl[0], cl[:-1]], cl)]
        out[s] = mk(s, ohlc, le={10, 120, 130, 220})
    return out


def test_b0_is_seeded_without_replacement_non_overlapping_and_count_matched(P):
    data, w, s1 = _b0_data(), W(400, last=360), R.stage_mgmt(P, 'S1')
    E = R.entry_set(data, w, s1)
    need = {}
    for e in E['episodes']:
        need[(e['sym'], e['slice'])] = need.get((e['sym'], e['slice']), 0) + 1
    a = R.b0_sample(data, E, w, s1, replicates=3, base_seed=20261028)
    assert a == R.b0_sample(data, E, w, s1, replicates=3, base_seed=20261028)
    assert a[0] != a[1]
    for rep in a:
        got = {}
        for e in rep:
            got[(e['sym'], e['slice'])] = got.get((e['sym'], e['slice']), 0) + 1
        assert got == need
        for s in data:
            iv = sorted((e['f'], R.replay_unit(data[s], e['f'], s1).exit[2]) for e in rep if e['sym'] == s)
            assert len({f for f, _ in iv}) == len(iv) and all(b0 <= a1 for (_, b0), (a1, _) in zip(iv, iv[1:]))
    assert a[0] == R.b0_sample(data, E, w, s1, replicates=1, base_seed=20261028)[0]   # Random(base + r)
    assert len(R.b0_means(data, a, s1)) == 3


def test_b0_unmatched_fails_closed(P):
    data, s1 = _b0_data(), R.stage_mgmt(P, 'S1')
    w = W(400, last=360)
    E = R.entry_set(data, w, s1)
    E['episodes'] += [dict(E['episodes'][0])] * 200                          # more S1 episodes than random bars can host
    with pytest.raises(R.BaselineIncomplete):
        R.b0_sample(data, E, w, s1, replicates=1, base_seed=20261028)


# ------------------------------------------------------------------ run + no verdict / no spend writes
def test_run_is_in_memory_without_verdict_and_writes_are_refused(pf, tmp_path):
    w = R.Windows.of(pf.prereg)
    data = {s: R.prepare_symbol(s, _walk(900, seed=k, start=T0 - 300 * TF), w) for k, s in enumerate(('BTCUSDT', 'ETHUSDT'))}
    res = R.run(data, pf, rules=lambda s, t: RULE, b0_replicates=2, book_stage='S3', resamples=50,
                allow_partial_universe_test_only=True)
    assert res['universe'] == 'PARTIAL-TEST(FIXED-17)'
    assert res['evaluated'] is False and 'verdict' not in json.dumps(res).lower() and set(res['stages']) == set(R.STAGE_IDS)
    assert res['B0']['status'] in ('OK', 'INCOMPLETE') and res['book']['stage'] == 'S3'
    with pytest.raises(R.IdentityMismatch):
        R.run({'LSKUSDT': data['BTCUSDT']}, pf, allow_partial_universe_test_only=True)
    with pytest.raises(R.PreflightError):
        R.run(data, pf.prereg)
    out = tmp_path / 'result.json'
    with pytest.raises(R.WriteRefused):
        R.write_result(res, str(out))
    with pytest.raises(R.WriteRefused):
        R.write_result(res, str(out), authorization='yes')
    with pytest.raises(R.WriteRefused):
        R.write_result(res, str(out), authorization=R.EvalAuthorization('codex', '1', '0' * 64))
    ok = R.EvalAuthorization('codex', '1', pf.prereg_sha256)
    with pytest.raises(R.WriteRefused):
        R.write_result(res, str(ROOT.joinpath(*R.LEDGER_REL[:-1], 'x.json')), authorization=ok)
    with pytest.raises(R.WriteRefused):
        R.write_result(res, str(tmp_path / 'verdict.json'), authorization=ok)
    assert not out.exists()
    src = (ROOT / 'tools' / 'research' / 'bpyr_runner.py').read_text(encoding='utf-8')
    assert 'EvalAuthorization(' not in src                                  # nothing in the runner issues a token


# ================================================================== slice 2
import edge05_runner as E5                                                                  # noqa: E402

H1MS = R.HOUR


# ------------------------------------------------------------------ disclosures (Codex 6478332951 items 4 and 6)
def test_disclosures_are_in_docstring_result_and_run_json(pf, tmp_path):
    assert len(R.DISCLOSURES) == 2
    assert 'not path-identical' in R.DISCLOSURES[0] and 'discards' in R.DISCLOSURES[0] and '6478332951' in R.DISCLOSURES[0]
    assert 'conservatively' in R.DISCLOSURES[1] and '8h' in R.DISCLOSURES[1] and '6478332951' in R.DISCLOSURES[1]
    doc = R.__doc__
    assert 'B0 is not path-identical to E' in doc and 'charged conservatively' in doc and '(the 8h grid) are exact' in doc
    res = R.run({'BTCUSDT': mk('BTCUSDT', flat(60), le={0})}, pf, windows=W(400), resamples=20,
                allow_partial_universe_test_only=True)
    assert res['disclosures'] == list(R.DISCLOSURES) and res['parity_declared'] == list(R.PARITY_DECLARED)
    out = _execute(pf, tmp_path / 'r', stop_after=1)
    assert out['status'] == 'PARTIAL'
    assert json.loads((tmp_path / 'r' / 'run.json').read_text())['disclosures'] == list(R.DISCLOSURES)


# ------------------------------------------------------------------ conservative later-in-bar funding
def test_later_in_bar_funding_is_conservative_and_open_events_exact(P):
    t2 = T0 + 2 * TF
    fund = [(t2, 0.001, 100.0), (t2 + H1MS, 0.001, 100.0), (t2 + 2 * H1MS, -0.001, 100.0)]
    # S3: the add fires in bar 2 (high 102.5), no exit -> paid later event on the larger qty, received on the smaller
    sd = mk('BTCUSDT', flat(2) + [(100.5, 102.5, 100.4, 102.4)] + flat(3, 102.4) + [(80.0, 80, 80, 80)], le={0},
            funding=fund)
    pos = R.replay_unit(sd, 1, R.stage_mgmt(P, 'S3'))
    assert {t: q for t, _, _, q in pos.funding} == {t2: 1.0, t2 + H1MS: 1.5, t2 + 2 * H1MS: 1.0}
    # intrabar stop exit in bar 2: paid later events still charged, received ones dropped (0)
    sd = mk('BTCUSDT', flat(2) + [(99.0, 99.2, 97.0, 97.5)] + flat(3), le={0}, funding=fund)
    pos = R.replay_unit(sd, 1, R.stage_mgmt(P, 'S1'))
    assert {t: q for t, _, _, q in pos.funding} == {t2: 1.0, t2 + H1MS: 1.0, t2 + 2 * H1MS: 0.0}
    # missing mark -> INCOMPLETE; unproven funding span -> INCOMPLETE
    bad = mk('BTCUSDT', flat(2) + [(97.0, 97.5, 96.0, 97.2)] + flat(5), le={0}, funding=[(T0 + TF, 0.001, math.nan)])
    with pytest.raises(R.Incomplete, match='incomplete.mark'):
        R.replay_unit(bad, 1, R.stage_mgmt(P, 'S1'))
    unproven = mk('BTCUSDT', flat(2) + [(97.0, 97.5, 96.0, 97.2)] + flat(5), le={0})
    unproven.fund_runs = [(T0 + 2 * TF, T0 + 9 * TF)]
    with pytest.raises(R.Incomplete, match='incomplete.funding'):
        R.replay_unit(unproven, 1, R.stage_mgmt(P, 'S1'))


# ------------------------------------------------------------------ statistics
def test_bootstrap_lcb_is_seeded_week_blocked_and_conservative():
    wk = 7 * 24 * H1MS
    eps = [(T0 + k * wk + 3 * H1MS, 0.5 if k % 3 else -0.4) for k in range(60)]
    m, lcb = R.bootstrap_lcb(eps, resamples=500)
    assert m == pytest.approx(sum(r for _, r in eps) / 60) and lcb < m
    assert (m, lcb) == R.bootstrap_lcb(eps, resamples=500) and lcb != R.bootstrap_lcb(eps, resamples=500, seed=1)[1]
    assert R.iso_week(T0) == R.iso_week(T0 + 6 * 24 * H1MS + 23 * H1MS) != R.iso_week(T0 + wk)   # T0 is a Monday
    assert R.bootstrap_lcb([], resamples=10) == (None, None)
    sparse = [(T0, 1.0), (T0 + 30 * wk, 1.0)]                               # empty resamples count as -inf
    assert R.bootstrap_lcb(sparse, resamples=200)[1] == -math.inf


def _ep(sym, sl, r, t=T0):
    return {'sym': sym, 'slice': sl, 't': t, 'f': 1, 'rows': {k: {'net_r': r, 'gross': r, 'fill_adj': 0.0, 'fees': 0.0,
                                                                   'funding': 0.0, 'net': r, 'r_unit': 1.0} for k in R.ROWS}}


def test_concentration_remove_best_activity_and_b0_gate(P):
    eps = [_ep(f'S{i:02d}', 1 + i % 3, 1.0) for i in range(20)]
    assert R.concentration(eps, P)['pass'] and R.remove_best_symbol(eps)['pass']
    lop = eps + [_ep('S00', 1, 30.0)]
    assert R.concentration(lop, P)['pass'] is False and R.concentration(lop, P)['max_share'] > 0.25
    neg = [_ep('A', 1, 10.0)] + [_ep('B', 1, -1.0)] * 3
    assert R.remove_best_symbol(neg) == {'removed': 'A', 'mean': -1.0, 'pass': False}
    w = R.Windows.of(P)
    many = [_ep(f'S{i % 17:02d}', s, 0.1) for s in (1, 2, 3) for i in range(100)]
    a = R.activity(many, P, w)
    assert a['total'] == 300 and a['per_slice'] == [100] * 3 and a['active_total'] == 17
    assert R.slice_spans(P, w)[2] == (w.last_entry - w.slices[2][0]) / R.DAY_MS                    # slice 3 ends 08-29
    assert a['pass'] is True
    assert R.activity(many[:274], P, w)['pass'] is False                   # slice 3 at 74 < 75
    assert R.b0_gate(0.2, [0.0] * 950 + [0.3] * 50)['pass'] is True        # p95 = order statistic 950 -> 0.0
    assert R.b0_gate(0.2, [0.0] * 949 + [0.3] * 51)['pass'] is False
    assert R.b0_gate(0.2, None) == {'status': 'INCOMPLETE', 'p95': None, 'pass': False}
    assert R.b0_gate(0.2, [0.1, None])['pass'] is False


def test_paired_d3_and_stage_quality(P):
    w = W(400)
    s2 = {'stage': 'S2', 'episodes': [_ep('A', 1 + k % 3, 0.1) for k in range(60)], 'incomplete': []}
    s3 = {'stage': 'S3', 'episodes': [_ep('A', 1 + k % 3, 0.3) for k in range(60)], 'incomplete': []}
    for k, (a, b) in enumerate(zip(s2['episodes'], s3['episodes'])):
        a['f'] = b['f'] = k
        a['t'] = b['t'] = T0 + k * 7 * 24 * H1MS
    d = R.paired_d3(s2, s3, w, resamples=200)
    assert d['n'] == 60 and d['base']['mean'] == pytest.approx(0.2) and d['pass'] is True
    q = R.stage_quality(s3, P, w, resamples=200)
    assert q['checks']['expectancy'] and q['checks']['slices'] and q['concentration']['pass'] is False   # one symbol
    assert q['data']['pass'] and q['rows']['base_x1']['pf'] == math.inf


def test_dd_tier_paths_and_decide_precedence(P):
    base = {'max_equity_dd': 0.30, 'closed_r_dd': 20, 'longest_underwater_days': 100, 'calmar': 0.1,
            'total_r_over_closed_r_dd': 1.0}
    x15 = {'max_equity_dd': 0.40}
    assert R.dd_tier(base, x15, P) == {'ordinary': True, 'rescue': False, 'ceilings': True, 'pass': True, 'label': None}
    rescue = dict(base, max_equity_dd=0.50, calmar=0.8, total_r_over_closed_r_dd=2.5)
    assert R.dd_tier(rescue, x15, P)['label'] == 'HIGH-RISK SLEEVE'
    assert R.dd_tier(dict(rescue, max_equity_dd=0.56), x15, P)['pass'] is False                   # > 55% on any path
    assert R.dd_tier(base, {'max_equity_dd': 0.66}, P)['pass'] is False
    assert R.dd_tier(dict(base, longest_underwater_days=366), x15, P)['pass'] is False
    ok = {'data': True, 'activity': True, 'b0': {'status': 'OK'}, 'H1': True, 'H2': True, 'H3': False,
          'book_dd': {'pass': True}, 'tails': True, 'causality': True}
    assert R.decide(ok) == 'SURVIVES_DEV(S2)' and R.decide(dict(ok, H2=False)) == 'SURVIVES_DEV(S1)'
    assert R.decide(dict(ok, H1=False, H2=True, H3=True)) == 'FALSIFIED'                           # never credited later
    assert R.decide(dict(ok, b0={'status': 'INCOMPLETE'})) == 'INCOMPLETE'
    assert R.decide(dict(ok, activity=False, H1=False)) == 'INCOMPLETE'
    assert R.decide(dict(ok, book_dd={'pass': False})) == 'FAIL'
    assert R.credited_stage(True, True, True) == 'S3' and R.credited_stage(False, True, True) is None


# ------------------------------------------------------------------ data adapter (synthetic in-memory source)
class FakeSrc:
    def __init__(self, bars, funding, marks):
        self.b, self.f, self.m = bars, funding, marks

    def bars(self, sym, iv, lo, hi):
        return [b for b in self.b.get(sym, []) if lo <= b.open_ms < hi]

    def funding(self, sym, lo, hi):
        return [r for r in self.f.get(sym, []) if lo <= r.time_ms < hi]

    def mark(self, sym, lo, hi):
        return [b for b in self.m.get(sym, []) if lo <= b.open_ms < hi]

    def first_ms(self, sym):
        return {}

    def identity(self):
        return {'kind': 'fake', 'manifest_file_sha256': 'a' * 64}


def _src(syms, n=700, start=T0 - 300 * TF, drop_funding=(), seed=0):
    bars, fund, marks = {}, {}, {}
    for k, s in enumerate(syms):
        raw = _walk(n, seed=seed + k, start=start)
        bars[s] = [E5.Bar(t, o, h, l, c, v, v, t + TF) for t, o, h, l, c, v in raw]
        fund[s] = [E5.Funding(t, 0.0001, 8, t) for t in range(start, start + n * TF, 8 * H1MS) if t not in drop_funding]
        marks[s] = [E5.Bar(t, c, c, c, c, 0, 0, t + H1MS) for t0, _, _, _, c, _ in raw for t in range(t0, t0 + TF, H1MS)]
    return FakeSrc(bars, fund, marks)


def test_adapter_loads_through_guard_with_cadence_proof_and_marks():
    w = W(400, warm=240)
    src = _src(['BTCUSDT'])
    sd = R.load_symbol(src, 'BTCUSDT', w, warmup_start=T0 - 300 * TF)
    assert len(sd.t) == 700 and sd.fund_runs == [(T0 - 300 * TF, T0 + 400 * TF)]
    assert math.isnan(sd.funding[0][2]) and all(math.isfinite(m) for _, _, m in sd.funding[1:])   # no mark before data
    assert sd.trend is not None
    hole = T0 + 100 * TF
    sd2 = R.load_symbol(_src(['BTCUSDT'], drop_funding={hole}), 'BTCUSDT', w, warmup_start=T0 - 300 * TF)
    assert len(sd2.fund_runs) == 2 and not sd2.funding_covered(hole - TF, hole + TF)
    leaky = _src(['BTCUSDT'], n=701)
    leaky.bars = lambda sym, iv, lo, hi: leaky.b[sym]                      # ignores the range: hands back the seal bar
    with pytest.raises(E5.HoldoutRefused):
        R.load_symbol(leaky, 'BTCUSDT', w, warmup_start=T0 - 300 * TF)
    with pytest.raises(E5.HoldoutRefused):                                 # a read request past the seal is refused
        E5.Guard(src, w.sealed).bars('BTCUSDT', '4h', T0, w.sealed + TF)
    off = _src(['BTCUSDT'])
    r5 = off.f['BTCUSDT'][5]
    off.f['BTCUSDT'][5] = E5.Funding(r5.time_ms + 30 * 60_000, 0.0001, 8, r5.time_ms)
    assert R.load_symbol(off, 'BTCUSDT', w, warmup_start=T0 - 300 * TF).fund_runs == []   # off-grid: fail closed


def test_rules_of_maps_uncovered_to_refusal():
    class Pit:
        def lookup(self, sym, t):
            if t < T0:
                raise E5.Incomplete('provenance_uncovered', t)
            return RULE
    at = R.rules_of(Pit())
    assert at('BTCUSDT', T0) == RULE and at('BTCUSDT', T0 - 1) is None


# ------------------------------------------------------------------ authorized, resumable, chained execution
def AUTH(pf):                                                                # test-only Integration token
    return R.EvalAuthorization('codex', '6478332951', pf.prereg_sha256)


FIXED = sorted(json.loads((ROOT / 'research_evidence' / 'prereg' / 'breakout_pyramid_falsify.v1.json').read_text())['universe']['symbols'])
_SRC = {}


def _fixed_src():
    if 'src' not in _SRC:
        _SRC['src'] = _src(FIXED)
    return _SRC['src']


def _execute(pf, out, **kw):
    args = dict(symbols=list(reversed(FIXED)), rules=lambda s, t: RULE, authorization=AUTH(pf), max_rss_mb=4096,
                rss=lambda: 100.0, warmup_start=T0 - 300 * TF, b0_replicates=2, windows=W(400, warm=240), resamples=20)
    src = kw.pop('src', None) or _fixed_src()
    args.update(kw)
    return R.execute(str(out), src, pf, **args)


def test_execute_is_authorized_rss_guarded_chained_and_resumable(pf, tmp_path):
    with pytest.raises(R.WriteRefused):
        _execute(pf, tmp_path / 'a', authorization=None)
    with pytest.raises(R.RunnerError, match='max-rss-mb'):
        _execute(pf, tmp_path / 'a', max_rss_mb=None)
    with pytest.raises(R.MemoryLimit):
        _execute(pf, tmp_path / 'a', rss=lambda: 5000.0)
    with pytest.raises(R.DirtyTree):
        _execute(R.Preflight(pf.prereg, pf.prereg_sha256, dict(pf.code, dirty=True), pf.sources), tmp_path / 'a')
    with pytest.raises(R.WriteRefused):
        _execute(pf, ROOT.joinpath(*R.LEDGER_REL[:-1]))
    out = tmp_path / 'r'
    assert _execute(pf, out, stop_after=1) == {'status': 'PARTIAL', 'symbols_done': 1, 'symbols_total': 17}
    first = (out / 'symbols.jsonl').read_bytes()
    res = _execute(pf, out)                                                   # resumes after BTCUSDT
    lines = (out / 'symbols.jsonl').read_bytes()
    assert lines.startswith(first) and lines.count(b'\n') == 17
    assert res['evaluated'] is False and res['identity_sha256'] and 'verdict' not in json.dumps(res).lower()
    full = tmp_path / 'f'
    _execute(pf, full)                                                        # uninterrupted run: byte-identical
    assert (full / 'symbols.jsonl').read_bytes() == lines and (full / 'run.json').read_bytes() == (out / 'run.json').read_bytes()
    bad = tmp_path / 'b'
    _execute(pf, bad, stop_after=1)
    (bad / 'symbols.jsonl').write_bytes((bad / 'symbols.jsonl').read_bytes().replace(b'"n":', b'"n": '))
    with pytest.raises(R.RunnerError, match='hash'):
        _execute(pf, bad)
    with pytest.raises(R.RunnerError, match='identity'):
        _execute(pf, out, resamples=21)


# ------------------------------------------------------------------ (d) parity with backtest.py
def _par_bars(n, seed):
    rng = np.random.default_rng(seed)
    cl = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, n)))
    op = np.r_[cl[0], cl[:-1]]
    hi = np.maximum(op, cl) * (1 + np.abs(rng.normal(0, 0.01, n)))
    lo = np.minimum(op, cl) * (1 - np.abs(rng.normal(0, 0.01, n)))
    op = np.where(rng.random(n) < 0.03, op * 0.93, op)                      # gap-down opens (gap stops)
    op = np.where(rng.random(n) < 0.02, op * 1.05, op)                      # gap-up opens (adds at the open)
    hi, lo = np.maximum(hi, op), np.minimum(lo, op)
    return [(T0 + k * TF, float(op[k]), float(hi[k]), float(lo[k]), float(cl[k]), 1.0) for k in range(n)]


def _parity(P, seeds, stage, override, n=1500):
    import pandas as pd
    import backtest as BT
    w = R.Windows(T0, T0 + n * TF, T0 + n * TF, ((T0, T0 + n * TF),), 240)
    data, raw = {}, {}
    for s, seed in zip(('BTCUSDT', 'ETHUSDT'), seeds):
        raw[s] = _par_bars(n, seed)
        data[s] = R.prepare_symbol(s, raw[s], w)
        data[s].slip = [2.0] * n                                              # backtest.SLIP = 2 bp
    legacy = lambda s, t: {'step': None, 'min_qty': 0.0, 'min_notional': BT.MIN_NOTIONAL.get(s, 5)}   # noqa: E731
    bk = R.run_book(data, P, stage, w, legacy)
    ours = {s: [] for s in data}
    for x in sorted(bk['trades'] + bk['incomplete'], key=lambda x: x['t_entry']):
        ours[x['sym']] += [(k, j, round(ref, 9), gap) for k, j, ref, gap, _tick in x['fills']]
    log = []
    dfs = {s: pd.DataFrame(raw[s], columns=list('tohlcv')) for s in raw}
    for d in dfs.values():
        d['t'] = pd.to_datetime(d['t'], unit='ms')
    BT.run(BT.Book(dfs), [dict(key='breakout_pyramid', share=1.0, risk=0.01, max_pos=4, mgmt=override)],
           start=10000.0, max_lev=1000.0, daily_halt=1e9, warmup=239, fund_per_bar=0.0, maint_margin=0, fill_log=log)
    theirs = {s: [(x['kind'], x['i'], round(x['ref'], 9), x['gap']) for x in log if x['sym'] == s] for s in data}
    return ours, theirs, data


@pytest.mark.parametrize('stage,override', [('S2', {'pyramid': {'n': 0}}), ('S3', {})])
def test_backtest_parity_fill_for_fill(P, stage, override):
    """Same bars, same strategy / mgmt, slippage pinned to backtest.SLIP: every entry / add / stop / trail exit
    (reference price, bar, gap flag) of the runner's book equals backtest.run's fill_log, two symbols at once."""
    ours, theirs, _ = _parity(P, (1, 5), stage, override)
    for s in ours:
        assert ours[s] == theirs[s] and len(ours[s]) >= 30
    allf = ours['BTCUSDT'] + ours['ETHUSDT']
    assert sum(x[3] for x in allf) >= 5 and any(x[0] == 'exit' and not x[3] for x in allf)
    assert (stage == 'S3') == any(x[0] == 'add' for x in allf)


def test_backtest_parity_over_many_seeds_diverges_only_by_declared_fill_time_rearm(P):
    """20 seed pairs x 2 symbols, S3: each symbol's fill sequence is identical to backtest.py, or its FIRST
    difference is PARITY_DECLARED[1] - a runner entry at the very open where its previous episode was gap-stopped
    (phase 1 frees the symbol), a signal backtest.py had already dropped."""
    same = declared = 0
    for seed in range(10, 30):
        ours, theirs, _ = _parity(P, (seed, seed + 100), 'S3', {}, n=1200)
        for s in ours:
            o, t = ours[s], theirs[s]
            k = next((i for i, (a, b) in enumerate(zip(o, t)) if a != b), None)
            if k is None:
                assert len(o) == len(t)
                same += 1
                continue
            assert o[k - 1][0] == 'exit' and o[k - 1][3] is True and o[k][0] == 'entry' and o[k][1] == o[k - 1][1]
            assert t[k][1] > o[k][1]
            declared += 1
    assert same >= 10 and declared >= 1


def test_backtest_parity_declared_time_exit(P):
    """PARITY_DECLARED[0]: S1 exits at the OPEN after 42 bars; backtest.py at the CLOSE of that bar. Up to the first
    time exit the fills are identical."""
    ours, theirs, data = _parity(P, (1, 5), 'S1', {'pyramid': {'n': 0}, 'trail_atr': 0, 'max_bars': 42})
    o, t = ours['BTCUSDT'], theirs['BTCUSDT']
    k = next(i for i, x in enumerate(o) if x[0] == 'exit' and x[1] == o[i - 1][1] + 42 and not x[3])
    f = o[k - 1][1]
    assert o[:k] == t[:k] and o[k] == ('exit', f + 42, round(data['BTCUSDT'].o[f + 42], 9), False)
    assert t[k] == ('exit', f + 42, round(data['BTCUSDT'].c[f + 42], 9), False)


# ================================================================== Codex 6478876708 regressions
import sys as _sys                                                                          # noqa: E402


# ---- 1. frozen universe: exact set equality for run() and execute()
def _dummy(syms):
    return {s: mk(s, flat(60)) for s in syms}


def test_run_requires_the_exact_frozen_universe(pf):
    w = W(400)
    assert R.run(_dummy(FIXED), pf, windows=w, resamples=10)['universe'] == 'FIXED-17'
    for syms in (FIXED[:-1], FIXED + ['LSKUSDT'], FIXED[:-1] + ['LSKUSDT']):              # subset, superset, swap
        with pytest.raises(R.IdentityMismatch):
            R.run(_dummy(syms), pf, windows=w, resamples=10)
    for syms in (FIXED + ['LSKUSDT'], FIXED[:-1] + ['LSKUSDT']):                          # the test flag never widens
        with pytest.raises(R.IdentityMismatch):
            R.run(_dummy(syms), pf, windows=w, resamples=10, allow_partial_universe_test_only=True)
    with pytest.raises(R.IdentityMismatch):                                               # STATIC-40 is exact too
        R.run(_dummy(FIXED), pf, windows=w, resamples=10, universe='STATIC-40')
    s40 = sorted(R.universe_symbols(pf.prereg, 'STATIC-40'))
    assert len(s40) == 40 and R.run(_dummy(s40), pf, windows=w, resamples=10, universe='STATIC-40')['report_only']


def test_execute_requires_the_exact_frozen_universe(pf, tmp_path):
    for k, syms in enumerate((FIXED[:-1], FIXED + ['LSKUSDT'], FIXED[:-1] + ['LSKUSDT'], FIXED + [FIXED[0]])):
        with pytest.raises(R.IdentityMismatch):
            _execute(pf, tmp_path / str(k), symbols=syms)
        assert not (tmp_path / str(k)).exists()                                          # refused before any write


# ---- 2. every credited stage needs its own data gate
def _stage(name, r, n_inc=0, inc_slice=1):
    eps = []
    for k in range(300):
        e = _ep(FIXED[k % 17], 1 + k // 100, r + 0.01 * (k % 5), t=T0 + k * 2 * 24 * H1MS)
        e['f'] = k
        eps.append(e)
    return {'stage': name, 'episodes': eps,
            'incomplete': [{'sym': 'BTCUSDT', 'slice': inc_slice, 'reason': 'incomplete.gap'}] * n_inc}


def test_s2_or_s3_breaching_their_own_data_gate_are_never_credited(P):
    w, means = W(400), [0.0] * 20
    clean = {'S1': _stage('S1', 0.4), 'S2': _stage('S2', 0.5), 'S3': _stage('S3', 0.6)}
    g = R.gates(P, w, clean, means, resamples=200)
    assert (g['H1'], g['H2'], g['H3'], g['credited_stage']) == (True, True, True, 'S3')
    assert set(g['data_by_stage']) == {'S1', 'S2', 'S3'} and all(d['pass'] for d in g['data_by_stage'].values())
    bad2 = dict(clean, S2=_stage('S2', 0.5, n_inc=3))                                     # 3 / 103 > 2% in slice 1
    g = R.gates(P, w, bad2, means, resamples=200)
    assert g['data'] is True and g['data_by_stage']['S2']['pass'] is False
    assert g['quality']['S2']['checks']['data'] is False and g['credited_stage'] == 'S1' and g['H3'] is False
    bad3 = dict(clean, S3=_stage('S3', 0.6, n_inc=5, inc_slice=3))
    g = R.gates(P, w, bad3, means, resamples=200)
    assert g['credited_stage'] == 'S2' and g['data_by_stage']['S3']['pass'] is False
    ok2 = dict(clean, S2=_stage('S2', 0.5, n_inc=2))                                      # 2 / 102 <= 2%: credited
    assert R.gates(P, w, ok2, means, resamples=200)['credited_stage'] == 'S3'


# ---- 3. point-in-time rules are queried at every order (adds, exits), never carried from the entry
def _rule_book(P, after):
    bars = (flat(2) + [(100.0, 100.3, 99.9, 100.2), (100.2, 103.0, 100.1, 102.9)] + flat(5, 102.9)
            + [(95.0, 95.2, 94.8, 95.0)] + flat(3, 95.0))
    before = dict(RULE, tick=0.01)
    rules = lambda s, t: before if t < T0 + 3 * TF else after                       # noqa: E731
    return R.run_book({'BTCUSDT': mk('BTCUSDT', bars, le={0})}, P, 'S3', W(400), rules)


def test_rule_change_between_entry_and_add_or_exit_applies_the_new_rule(P):
    bk = _rule_book(P, dict(RULE, min_notional=1e9, tick=0.5))
    tr = bk['trades'][0]
    assert bk['refusals'] == {'add_below_min_notional': 1} and tr['adds'] == 0           # the new minimum refused it
    assert tr['fills'][0][4] == 0.01 and tr['fills'][-1][:2] == ('exit', 9) and tr['fills'][-1][4] == 0.5
    assert R.unresolved(bk) == 0
    bk = _rule_book(P, None)                                                              # no rule after the change
    assert bk['refusals'] == {'add_rules_uncovered': 1, 'exit_rules_uncovered': 1} and R.unresolved(bk) == 2
    assert R.book_metrics(bk, W(400))['unresolved_accounting'] == 2
    bk = _rule_book(P, dict(RULE, max_qty=10.0))                                          # exit above the new max
    assert bk['refusals'].get('exit_above_max_qty') == 1 and R.unresolved(bk) == 1
    tight = _rule_book(P, dict(RULE, tick=0.01))
    assert tight['refusals'] == {} and tight['trades'][0]['adds'] == 1


# ---- 4. --max-rss-mb bounds the heavy loops (B0 replicates, bootstrap batches, books, unit replays)
def _trip_in(fn):
    def rss():
        f = _sys._getframe(1)
        while f is not None:
            if f.f_code.co_name == fn:
                return 5000.0
            f = f.f_back
        return 100.0
    return rss


@pytest.mark.parametrize('where', ['b0_sample', 'bootstrap_lcb', 'unit_replays'])
def test_rss_guard_trips_inside_heavy_work_and_writes_nothing_past_the_checkpoint(pf, tmp_path, where):
    out = tmp_path / 'r'
    with pytest.raises(R.MemoryLimit):
        _execute(pf, out, rss=_trip_in(where))
    files = sorted(x.name for x in out.iterdir())
    assert files == ['run.json', 'symbols.jsonl']
    n = (out / 'symbols.jsonl').read_bytes().count(b'\n')
    assert n == (0 if where == 'unit_replays' else 17)
    full = tmp_path / 'f'
    _execute(pf, full)
    _execute(pf, out)                                                                     # resumes to the same chain
    assert (out / 'symbols.jsonl').read_bytes() == (full / 'symbols.jsonl').read_bytes()


def test_run_book_calls_the_guard():
    calls = []
    R.run_book({'BTCUSDT': mk('BTCUSDT', flat(600), le={0})}, json.loads(
        (ROOT.joinpath(*R.PREREG_REL)).read_text(encoding='utf-8')), 'S1', W(600, last=560), lambda s, t: RULE,
        check=lambda: calls.append(1))
    assert len(calls) == 3                                                                # bars 0, 256, 512


# ---- 5. pass 2 is bound to the verified pass-1 chain and an immutable source identity
class _Mutating:
    def __init__(self, base, after_calls, mutate_bars=False, mutate_identity=False, identity=None):
        self.base, self.n, self.after = base, 0, after_calls
        self.mb, self.mi, self.ident = mutate_bars, mutate_identity, identity

    def bars(self, sym, iv, lo, hi):
        self.n += 1
        rows = self.base.bars(sym, iv, lo, hi)
        if self.mb and self.n > self.after and sym == 'BTCUSDT':
            b = rows[400]
            rows = rows[:400] + [b._replace(close=b.close * 1.0001)] + rows[401:]
        return rows

    def funding(self, sym, lo, hi):
        return self.base.funding(sym, lo, hi)

    def mark(self, sym, lo, hi):
        return self.base.mark(sym, lo, hi)

    def identity(self):
        if self.ident is not None:
            return self.ident
        h = 'b' * 64 if (self.mi and self.n > self.after) else 'a' * 64
        return {'kind': 'fake', 'manifest_file_sha256': h}


def test_pass2_refuses_a_source_mutated_between_passes_or_without_identity(pf, tmp_path):
    with pytest.raises(R.RunnerError, match='pass-1 chain'):
        _execute(pf, tmp_path / 'a', src=_Mutating(_fixed_src(), 17, mutate_bars=True))
    with pytest.raises(R.RunnerError, match='identity changed'):
        _execute(pf, tmp_path / 'b', src=_Mutating(_fixed_src(), 17, mutate_identity=True))
    for k, ident in enumerate(({'kind': 'fake'}, {'manifest_file_sha256': None}, {'manifest_file_sha256': 'x' * 64})):
        with pytest.raises(R.RunnerError, match='immutable identity'):
            _execute(pf, tmp_path / f'c{k}', src=_Mutating(_fixed_src(), 0, identity=ident))
    lines = [json.loads(x) for x in (tmp_path / 'a' / 'symbols.jsonl').read_text().splitlines()]
    assert len(lines) == 17 and all(len(x['source_sha256']) == 64 for x in lines)
    assert json.loads((tmp_path / 'a' / 'run.json').read_text())['source_identity']['manifest_file_sha256'] == 'a' * 64


# ---- ruling 4 amended: underwater on the path-aware equity sequence (same as DD)
def test_underwater_and_dd_use_the_path_aware_equity_sequence(P):
    assert R.underwater_days([(0, 10000.0), (H1MS, 9900.0), (2 * H1MS, 10100.0), (4 * H1MS, 10100.0)]) == 1 / 24
    assert R.underwater_days([(0, 10000.0), (H1MS, 9000.0), (TF, 9000.0)]) == 3 / 24      # still under at the end
    bars = flat(2) + [(100.0, 101.0, 99.9, 101.0), (101.0, 102.0, 100.9, 102.0), (102.0, 103.0, 101.9, 103.0),
                      (103.0, 104.0, 99.0, 104.0)] + flat(3, 104.0)
    bk = R.run_book({'BTCUSDT': mk('BTCUSDT', bars, le={0})}, P, 'S1', W(400), lambda s, t: RULE)
    close_dd = R.max_dd([(t, e) for t, e, _ in bk['curve']])
    t5 = T0 + 5 * TF
    dip = min(e for t, e in bk['equity_seq'] if t5 < t < t5 + TF)
    assert dip < min(e for t, e, _ in bk['curve'] if t == t5) - 150                       # intrabar low captured
    assert bk['max_equity_dd'] > close_dd + 0.01 and bk['max_equity_dd'] == R.max_dd(bk['equity_seq'])
    m = R.book_metrics(bk, W(400))
    assert m['longest_underwater_days'] == R.underwater_days(bk['equity_seq'])
    seq_ts = [t for t, _ in bk['equity_seq']]
    assert seq_ts == sorted(seq_ts)                                                       # frozen event order is causal
