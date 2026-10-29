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
    ok = AUTH(pf)
    with pytest.raises(R.WriteRefused):
        R.write_result(res, str(ROOT.joinpath(*R.LEDGER_REL[:-1], 'x.json')), authorization=ok)
    with pytest.raises(R.WriteRefused):
        R.write_result(res, str(tmp_path / 'verdict.json'), authorization=ok)
    assert not out.exists()
    src = (ROOT / 'tools' / 'research' / 'bpyr_runner.py').read_text(encoding='utf-8')
    body = src[src.index('def load_authorization('):src.index('def _runlock(')]
    assert src.count('EvalAuthorization(') == body.count('EvalAuthorization(') == 1   # only the verifier issues one


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

    def set_memory(self, check, max_mb):
        self.bound = (check, max_mb)


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
    return R.EvalAuthorization('codex', '6478332951', pf.prereg_sha256, 'a' * 64, 'b' * 40, ('development',))


FIXED = sorted(json.loads((ROOT / 'research_evidence' / 'prereg' / 'breakout_pyramid_falsify.v1.json').read_text())['universe']['symbols'])
_SRC = {}


def _fixed_src():
    if 'src' not in _SRC:
        _SRC['src'] = _src(FIXED)
    return _SRC['src']


def _pit_rules(h='c' * 64, **bad):
    """A rules callable carrying an immutable PitRules-shaped identity (test-only)."""
    f = lambda s, t: RULE                                                                # noqa: E731
    f.identity = lambda: dict({'format': 'test-rules', 'sha256': h}, **dict(dict.fromkeys(R.RULES_ZERO_KEYS, 0), **bad))
    return f


def _execute(pf, out, **kw):
    args = dict(symbols=list(reversed(FIXED)), rules=_pit_rules(), authorization=AUTH(pf), max_rss_mb=4096,
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
    assert res['evaluated'] is True and res['decided'] is False                # authorized statistics, no decision
    assert res['identity_sha256'] and 'verdict' not in json.dumps(res).lower()
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
    assert len(s40) == 40


def test_report_only_universes_carry_no_decision_inputs(pf):
    w = W(400)
    s40 = sorted(R.universe_symbols(pf.prereg, 'STATIC-40'))
    for res in (R.run(_dummy(s40), pf, windows=w, resamples=10, universe='STATIC-40', rules=lambda s, t: RULE),
                R.run(_dummy(FIXED[:2]), pf, windows=w, resamples=10, allow_partial_universe_test_only=True,
                      book_stage='S3', rules=lambda s, t: RULE)):
        assert res['report_only'] and res['gate_inputs'] is None and 'gates' not in res
        assert 'report-only universe' in res['decision_inputs_refused']
        assert not set(R.DECISION_KEYS) & set(res['report_statistics'])   # no H1-H3 / credited stage to read
        with pytest.raises(R.RunnerError, match='no decision inputs'):
            R.decide(res['gate_inputs'])
        with pytest.raises(R.RunnerError, match='no decision inputs'):
            R.decide(dict(res['report_statistics'], report_only=True))
    assert res['book']['stage'] == 'S3'                                   # an explicit report book only
    assert R.run(_dummy(FIXED), pf, windows=w, resamples=10)['gate_inputs'] is not None   # the primary universe does


def test_execute_binds_an_immutable_rules_identity(pf, tmp_path):
    missing = _pit_rules()
    missing.identity = lambda: {'format': 'test-rules', 'sha256': 'c' * 64}               # N7: absent counts refuse
    for bad in (lambda s, t: RULE, _pit_rules(h='xyz'), _pit_rules(unauthenticated_entries=1), _pit_rules(unsourced_intervals=2),
                missing, _pit_rules(bad_coverage_entries=None), _pit_rules(causally_unsupported_intervals=False),
                _pit_rules(unsourced_intervals=0.0)):
        with pytest.raises(R.RunnerError, match='rules'):
            _execute(pf, tmp_path / 'x', rules=bad)
        assert not (tmp_path / 'x').exists()                               # refused before any read or write
    a = _execute(pf, tmp_path / 'a', stop_after=1)
    run_a = json.loads((tmp_path / 'a' / 'run.json').read_text())
    assert run_a['rules_identity']['sha256'] == 'c' * 64
    with pytest.raises(R.RunnerError, match='identity differs'):          # other rules: other run identity
        _execute(pf, tmp_path / 'a', rules=_pit_rules(h='d' * 64))
    assert a['status'] == 'PARTIAL'
    flip = _pit_rules()
    calls = [0]

    def ident():
        calls[0] += 1
        return dict(dict.fromkeys(R.RULES_ZERO_KEYS, 0), format='test-rules', sha256=('c' if calls[0] == 1 else 'e') * 64)
    flip.identity = ident
    with pytest.raises(R.RunnerError, match='rules identity changed'):    # swapped mid-run: result discarded
        _execute(pf, tmp_path / 'b', rules=flip)
    res = _execute(pf, tmp_path / 'c')
    assert res['rules_identity'] == run_a['rules_identity']


def test_run_refuses_a_symbol_data_under_another_key(pf):
    w = W(400)
    data = _dummy(FIXED)
    data['BTCUSDT'], data['ETHUSDT'] = data['ETHUSDT'], data['BTCUSDT']
    with pytest.raises(R.IdentityMismatch, match='holds SymbolData'):
        R.run(data, pf, windows=w, resamples=10)


def test_unfilled_fill_bar_is_a_k9_data_gap_in_every_stage(pf):
    w = W(400)
    sd = mk('BTCUSDT', flat(400), le={150}, drop={151})                  # the next (fill) bar is missing
    E = R.entry_set({'BTCUSDT': sd}, w, R.stage_mgmt(pf.prereg, 'S1'))
    assert E['unfilled'] == [{'sym': 'BTCUSDT', 't': T0 + 151 * TF, 'reason': 'unfilled_fill_bar', 'slice': 2}]
    assert E['unfilled_gap'] == 1 and not E['episodes']
    res = R.run({'BTCUSDT': sd}, pf, windows=w, resamples=10, allow_partial_universe_test_only=True)
    assert res['E']['unfilled_gap'] == 1
    for s in ('S1', 'S2', 'S3'):
        d = res['report_statistics']['data_by_stage'][s]
        assert d['incomplete_frac'][1] == 1.0 and d['pass'] is False         # inside the per-slice 2% fraction


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

    def set_memory(self, check, max_mb):
        self.base.set_memory(check, max_mb)

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


# ================================================================== slice 3: real store formats (tmp_path only), CLI, gate inputs
import contextlib                                                                           # noqa: E402
import gzip                                                                                 # noqa: E402
import hashlib                                                                              # noqa: E402
import zipfile                                                                              # noqa: E402
from datetime import datetime, timezone                                                     # noqa: E402
from types import SimpleNamespace                                                           # noqa: E402

PRODUCER = {'commit': 'd' * 40, 'script': 'tools/research/produce_pit_rules.py', 'script_sha256': 'e' * 64}
SER = {'4h': ('klines', '4h', TF), 'funding': ('fundingRate', None, 8 * R.HOUR), 'mark': ('markPriceKlines', '1h', R.HOUR)}


def _month(t):
    d = datetime.fromtimestamp(t / 1000, timezone.utc)
    return f'{d.year:04d}-{d.month:02d}'


def _month_bounds(ym):
    y, m = map(int, ym.split('-'))
    lo = int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
    return lo, int(datetime(y + m // 12, m % 12 + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _rel(sym, kind, ym):
    series, iv, _ = SER[kind]
    return (f'um/monthly/{series}/{sym}/{sym}-fundingRate-{ym}.zip' if kind == 'funding'
            else f'um/monthly/{series}/{sym}/{iv}/{sym}-{iv}-{ym}.zip')


def _entry(sym, kind, ym, sha, rows, first, last):
    series, iv, _ = SER[kind]
    return {'path': _rel(sym, kind, ym), 'series': series, 'symbol': sym, 'interval': iv, 'sha256': sha,
            'bytes': 1, 'rows': rows, 'first_open_ms': first, 'last_open_ms': last, 'source': {}}


def _write_manifest(path, files):
    with gzip.open(path, 'wt', encoding='utf-8') as f:                  # pretty-printed: the store's streamed format
        f.write(json.dumps({'format': 'x', 'files': files}, indent=1))
    return path


def _f(x):
    return repr(float(x))


def _series_rows(raw):
    """4h bars, 8h funding (calc_time on the grid) and 1h mark klines from synthetic (t, o, h, l, c, v) rows."""
    k4 = [(t, o, h, lo, c, v) for t, o, h, lo, c, v in raw]
    fund = [(t, 0.0001, 8) for t, *_ in raw if t % (8 * R.HOUR) == 0]
    mark = [(t, c, c, c, c, 0.0) for t0, _, _, _, c, _ in raw for t in range(t0, t0 + TF, R.HOUR)]
    return {'4h': k4, 'funding': fund, 'mark': mark}


def _csv(kind, rows):
    if kind == 'funding':
        return M.FUNDING_HEADER.decode() + '\n' + ''.join(f'{t},{iv},{_f(r)}\n' for t, r, iv in rows)
    step = SER[kind][2]
    return M.KLINE_HEADER.decode() + '\n' + ''.join(
        f'{t},{_f(o)},{_f(h)},{_f(lo)},{_f(c)},{_f(v)},{t + step - 1},100.5,1,0,0,0\n' for t, o, h, lo, c, v in rows)


def _archive(tmp, syms, lo, hi, lie_until=None):
    """Synthetic store in the real layout (um/monthly/<series>/<SYM>/[<iv>/]<SYM>-<iv>-YYYY-MM.zip + .ok sha sidecar)
    and a gzip manifest; rows from lo to hi. `lie_until`: the manifest understates last_open_ms of every month
    holding rows at/after it (a corrupted manifest that would let a post-seal month be opened)."""
    store, files, raws = tmp / 'store', [], {}
    for k, sym in enumerate(syms):
        raw = _walk((hi - lo) // TF, seed=50 + k, start=lo)
        raws[sym] = raw
        for kind, rows in _series_rows(raw).items():
            by = {}
            for r in rows:
                by.setdefault(_month(r[0]), []).append(r)
            for ym, rs in sorted(by.items()):
                rel = _rel(sym, kind, ym)
                p = store / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(p, 'w') as z:
                    z.writestr(rel.rsplit('/', 1)[1][:-4] + '.csv', _csv(kind, rs))
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                (store / (rel + '.ok')).write_text(h)
                last = rs[-1][0]
                if lie_until is not None and last >= lie_until:
                    last = lie_until - 1
                files.append(_entry(sym, kind, ym, h, len(rs), rs[0][0], last))
    return _write_manifest(tmp / 'manifest.json.gz', files), store, raws, files


def _mem_src(raws, cut):
    bars, fund, marks = {}, {}, {}
    for sym, raw in raws.items():
        s = _series_rows(raw)
        bars[sym] = [E5.Bar(t, float(o), float(h), float(lo), float(c), float(v), 100.5, t + TF)
                     for t, o, h, lo, c, v in s['4h'] if t + TF <= cut]
        fund[sym] = [E5.Funding(t, r, iv, t) for t, r, iv in s['funding'] if t < cut]
        marks[sym] = [E5.Bar(t, float(c), float(c), float(c), float(c), 0.0, 100.5, t + R.HOUR)
                      for t, _, _, _, c, _ in s['mark'] if t + R.HOUR <= cut]
    return FakeSrc(bars, fund, marks)


def _prov(kind, files, coverage, manifest_sha):
    return {'source': {'kind': kind, 'endpoint': E5.PROVENANCE_SOURCES[kind]}, 'source_manifest_sha256': manifest_sha,
            'upstream_files': [{'name': n, 'sha256': h, 'as_of': a} for n, h, a in files], 'producer': dict(PRODUCER),
            'coverage': coverage}


def _straddle(path, man, files, cut, syms, raws=None):
    """zb-edge05-straddle artifact: pre-cutoff rows of the never-opened straddle month (empty series when raws is
    None), each coverage entry citing that month's archive by its manifest sha256."""
    m_lo = _month_bounds(_month(cut))[0]
    by = {(f['symbol'], f['path']): f['sha256'] for f in files}
    up, cov, series = [], [], {'4h': {}, 'funding': {}, 'mark': {}}
    for sym in syms:
        s = _series_rows(raws[sym]) if raws else {k: [] for k in SER}
        for kind in SER:
            step = SER[kind][2] if kind != 'funding' else 0
            rows = [r for r in s[kind] if m_lo <= r[0] and r[0] + max(step, 1) <= cut]
            if kind == 'funding':
                series['funding'][sym] = [[t, r, iv] for t, r, iv in rows]
            else:
                series[kind][sym] = [[t, float(o), float(h), float(lo), float(c), float(v), 100.5]
                                     for t, o, h, lo, c, v in rows]
            rel = _rel(sym, kind, _month(cut))
            up.append((rel, by[(sym, rel)], '2026-10-01T00:00:00Z'))
            cov.append({'symbol': sym, 'series': kind, 'from': R._iso(m_lo), 'to': R._iso(cut), 'rows': len(rows),
                        'upstream': [rel]})
    doc = {'format': E5.STRADDLE_FORMAT, 'cutoff': R._iso(cut),
           'provenance': _prov('binance-vision-archive', up, cov, R.file_sha(str(man))), 'series': series}
    path.write_bytes(E5.canon(doc))
    return path


FILTER_OF = {'tick': ('PRICE_FILTER', 'tickSize'), 'step': ('MARKET_LOT_SIZE', 'stepSize'),
             'min_qty': ('MARKET_LOT_SIZE', 'minQty'), 'max_qty': ('MARKET_LOT_SIZE', 'maxQty'),
             'min_notional': ('MIN_NOTIONAL', 'notional'), 'max_notional': ('MAX_NOTIONAL', 'maxNotional')}
FULL_RULE = {'tick': 0.0001, 'step': 0.001, 'min_qty': 0.001, 'min_notional': 5.0, 'max_qty': 1e9, 'max_notional': 1e12}
SNAP = 'exchangeInfo@2019-01-01T00:00:00Z'


def _rules(d, syms, frm='2019-01-01T00:00:00Z', to='2027-01-01T00:00:00Z'):
    """Real zb-exchange-rules-pit artifact + its zb-upstream-source-manifest + one exchangeInfo snapshot file."""
    snaps = d / 'snaps'
    snaps.mkdir(parents=True, exist_ok=True)
    ents = []
    for s in sorted(syms):
        by = {}
        for k, (ft, key) in FILTER_OF.items():
            by.setdefault(ft, {'filterType': ft})[key] = repr(float(FULL_RULE[k]))
        ents.append({'symbol': s, 'status': 'TRADING', 'filters': [by[ft] for ft in sorted(by)]})
    sb = E5.canon({'timezone': 'UTC', 'symbols': ents})
    (snaps / 'snap.json').write_bytes(sb)
    sm = {'format': E5.SOURCE_MANIFEST_FORMAT, 'kind': 'binance-um-exchangeinfo-snapshots',
          'files': [{'name': SNAP, 'sha256': E5.sha(sb), 'as_of': '2019-01-01T00:00:00Z', 'path': 'snap.json'}]}
    smb = E5.canon(sm)
    (snaps / 'rules-source.json').write_bytes(smb)
    cov = [{'symbol': s, 'series': 'rules', 'from': frm, 'to': to, 'rows': 1, 'upstream': [SNAP]} for s in sorted(syms)]
    doc = {'format': E5.RULES_FORMAT,
           'provenance': _prov('binance-um-exchangeinfo-snapshots', [(SNAP, E5.sha(sb), '2019-01-01T00:00:00Z')], cov,
                               E5.sha(smb)),
           'symbols': {s: [dict(FULL_RULE, **{'from': frm, 'to': to, 'snapshot': SNAP})] for s in sorted(syms)}}
    (d / 'rules.json').write_bytes(E5.canon(doc))
    return d / 'rules.json', snaps / 'rules-source.json'


# ---- 2. the adapter end-to-end on the real archive / manifest / straddle / rules formats
MAR1 = R.iso_ms('2021-03-01T00:00:00Z')
DEC1 = R.iso_ms('2020-12-01T00:00:00Z')
APR1 = R.iso_ms('2021-04-01T00:00:00Z')


def test_archive_adapter_end_to_end_matches_the_in_memory_source(tmp_path):
    w = W(336, warm=200)
    assert w.sealed == MAR1
    man, store, raws, files = _archive(tmp_path, ['BTCUSDT'], DEC1, APR1)
    src = E5.ArchiveSource(str(man), str(store), ['BTCUSDT'], MAR1)
    assert src.straddle_skipped == 3                                     # the March files hold post-seal rows
    for f in files:                                                      # ... and are provably never opened
        if f['path'].endswith('2021-03.zip'):
            (store / f['path']).write_bytes(b'not a zip')
    sd = R.load_symbol(src, 'BTCUSDT', w, warmup_start=DEC1)
    ref = R.load_symbol(_mem_src(raws, MAR1), 'BTCUSDT', w, warmup_start=DEC1)
    assert sd.source_sha256 == ref.source_sha256                         # every bar / funding / mark row identical
    assert sd.t == ref.t and sd.fund_runs == ref.fund_runs == [(DEC1, MAR1)] and sd.t[-1] == MAR1 - TF
    assert R.source_identity(src)['manifest_file_sha256'] == R.file_sha(str(man))
    (store / files[0]['path']).write_bytes(b'tampered')                  # bytes != .ok / manifest: refused
    with pytest.raises(E5.RunnerError):
        R.load_symbol(E5.ArchiveSource(str(man), str(store), ['BTCUSDT'], MAR1), 'BTCUSDT', w, warmup_start=DEC1)


def test_execute_binds_the_rss_cap_to_the_source_before_any_read(pf, tmp_path, monkeypatch):
    seen = []

    class Spy(FakeSrc):
        def set_memory(self, check, max_mb):
            seen.append(('bind', max_mb, check))

        def bars(self, sym, iv, lo, hi):
            seen.append(('read', sym))
            return super().bars(sym, iv, lo, hi)
    base = _fixed_src()
    spy = Spy(base.b, base.f, base.m)
    rss = [100.0]
    _execute(pf, tmp_path / 's', src=spy, stop_after=1, max_rss_mb=777, rss=lambda: rss[0])
    assert seen[0][:2] == ('bind', 777) and seen[1][0] == 'read'          # bound first, with the mandatory cap
    rss[0] = 900.0
    with pytest.raises(R.MemoryLimit):                                    # the bound check is execute()'s own guard
        seen[0][2]()

    class NoBind(FakeSrc):
        set_memory = None
    with pytest.raises(R.RunnerError, match='set_memory'):                # unbindable source: refused before any read
        _execute(pf, tmp_path / 'n', src=NoBind(base.b, base.f, base.m))
    assert not (tmp_path / 'n').exists()
    # the real ArchiveSource, once bound, refuses a month expansion that would cross the cap BEFORE decompressing
    w = W(336, warm=200)
    man, store, raws, files = _archive(tmp_path / 'a', ['BTCUSDT'], DEC1, APR1)
    src = E5.ArchiveSource(str(man), str(store), ['BTCUSDT'], MAR1)
    src.set_memory(lambda: None, 64)
    monkeypatch.setattr(E5, 'rss_mb', lambda: 63.999)
    opened = []
    monkeypatch.setattr(src, '_stream_rows', lambda *a, **k: opened.append(1) or [])
    with pytest.raises(E5.MemoryLimit, match='refused before decompressing'):
        R.load_symbol(src, 'BTCUSDT', w, warmup_start=DEC1)
    assert not opened


def test_sealed_boundary_is_refused_before_and_after_the_read(tmp_path):
    cut = R.iso_ms('2021-02-15T00:00:00Z')                              # mid-month: February straddles the seal
    w = R.Windows(T0, cut - 40 * TF, cut, ((T0, cut),), 200)
    man, store, raws, files = _archive(tmp_path, ['BTCUSDT'], DEC1, MAR1)
    src = E5.ArchiveSource(str(man), str(store), ['BTCUSDT'], cut)
    assert src.straddle_skipped == 3
    with pytest.raises(R.SourceUncovered, match='provenance_uncovered from 2021-02-01'):   # never opened, never guessed
        R.load_symbol(src, 'BTCUSDT', w, warmup_start=DEC1)
    with pytest.raises(E5.HoldoutRefused):                               # before the read: a request past the seal
        E5.Guard(src, cut).bars('BTCUSDT', '4h', DEC1, cut + TF)
    with pytest.raises(E5.HoldoutRefused):
        E5.Guard(src, cut).scope(DEC1, cut + 1)
    # after the read: a corrupted manifest lets the straddle month in, and a store that ignores the range hands back
    # rows at/after the seal -> the guard refuses them after the read
    man2 = _write_manifest(tmp_path / 'lie.json.gz', _archive(tmp_path / 'b', ['BTCUSDT'], DEC1, MAR1, lie_until=cut)[3])

    class Leaky(E5.ArchiveSource):
        def _rows(self, sym, kind, lo, hi):
            return [r for m in self.index.get((sym, kind), ()) for r in self._month(m[2], m[3], m[4], kind)[1]]
    leaky = Leaky(str(man2), str(tmp_path / 'b' / 'store'), ['BTCUSDT'], cut)
    assert leaky.straddle_skipped == 0
    with pytest.raises(E5.HoldoutRefused, match='sealed boundary'):
        R.load_symbol(leaky, 'BTCUSDT', w, warmup_start=DEC1)
    # with an authenticated pre-cutoff straddle artifact the same read succeeds and matches the in-memory rows
    art = _straddle(tmp_path / 'straddle.json', man, files, cut, ['BTCUSDT'], raws)
    src.attach_straddle(E5.StraddleArtifact.load(str(art), cut))
    assert src.straddle.unauthenticated == 0 and src.straddle.bad_coverage == 0
    sd = R.load_symbol(src, 'BTCUSDT', w, warmup_start=DEC1)
    assert sd.source_sha256 == R.load_symbol(_mem_src(raws, cut), 'BTCUSDT', w, warmup_start=DEC1).source_sha256
    assert sd.t[-1] == cut - TF


def test_pit_rules_real_format_drive_rules_of(tmp_path):
    rp, sp = _rules(tmp_path, ['BTCUSDT'], frm='2021-01-01T00:00:00Z')
    rsrc = E5.SourceManifest.load(str(sp), R.file_sha(str(sp)), 'binance-um-exchangeinfo-snapshots')
    pit = E5.PitRules.load(str(rp), rsrc)
    ident = pit.identity()
    assert (ident['bad_coverage_entries'], ident['unauthenticated_entries'], ident['unsourced_intervals']) == (0, 0, 0)
    at = R.rules_of(pit)
    assert at('BTCUSDT', T0)['min_notional'] == 5.0 and at('BTCUSDT', T0)['tick'] == 0.0001
    assert R.rules_identity(at)['sha256'] == ident['sha256']          # the real PitRules identity carries every count
    assert at('BTCUSDT', R.iso_ms('2020-12-31T00:00:00Z')) is None and at('ETHUSDT', T0) is None   # uncovered
    with pytest.raises(E5.RunnerError):                                  # a rules-source manifest of another hash
        E5.SourceManifest.load(str(sp), '0' * 64, 'binance-um-exchangeinfo-snapshots')


# ---- 1. CLI: real ArchiveSource / PitRules wiring; preflight-only without an Integration authorization
SEALED = R.iso_ms('2026-09-28T00:00:00Z')


def _meta_manifest(path, syms, drop=()):
    """Metadata-only manifest over the real calibration..sealed span (no archive file exists: preflight opens none)."""
    files, ym = [], '2020-02'
    while ym <= '2026-09':
        lo, hi = _month_bounds(ym)
        for s in sorted(syms):
            for kind in SER:
                if (s, kind, ym) not in drop:
                    step = SER[kind][2]
                    files.append(_entry(s, kind, ym, hashlib.sha256(f'{s}{kind}{ym}'.encode()).hexdigest(),
                                        (hi - lo) // step, lo, hi - step))
        y, m = map(int, ym.split('-'))
        ym = f'{y + m // 12:04d}-{m % 12 + 1:02d}'
    return _write_manifest(path, files), files


@pytest.fixture(scope='module')
def cli_inputs(tmp_path_factory):
    d = tmp_path_factory.mktemp('cli')
    man, files = _meta_manifest(d / 'manifest.json.gz', FIXED)
    rp, sp = _rules(d, FIXED)
    st = _straddle(d / 'straddle.json', man, files, SEALED, FIXED)
    return SimpleNamespace(d=d, man=man, files=files, rules=rp, rsrc=sp, straddle=st)


def _cli(ci, *extra, manifest=None, git=CLEAN, rules=True, straddle=True, **inj):
    args = ['--store', str(ci.d / 'no-store'), '--max-rss-mb', '512']
    if rules:
        args += ['--rules-artifact', str(ci.rules), '--expect-rules-sha256', R.file_sha(str(ci.rules)),
                 '--rules-source-manifest', str(ci.rsrc), '--expect-rules-source-sha256', R.file_sha(str(ci.rsrc))]
    if straddle:
        args += ['--straddle-artifact', str(ci.straddle), '--expect-straddle-sha256', R.file_sha(str(ci.straddle))]
    return R.main(args + list(extra), _inject=dict({'git': git, 'manifest_check': NO_MANIFEST,
                                                     'manifest_path': str(manifest or ci.man)}, **inj))


def test_cli_preflight_only_reports_identity_and_coverage_and_writes_nothing(cli_inputs, capsys, tmp_path):
    out = tmp_path / 'out'
    assert _cli(cli_inputs, '--out', str(out)) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep['mode'] == 'preflight-only' and rep['evaluated'] is False and rep['written'] is None
    assert rep['coverage']['ready'] is True and rep['coverage']['straddle_files_never_opened'] == 17 * 3
    assert rep['source_identity']['manifest_file_sha256'] == R.file_sha(str(cli_inputs.man))
    assert rep['source_identity']['straddle']['unauthenticated_entries'] == 0
    assert rep['rules_identity']['sha256'] == R.file_sha(str(cli_inputs.rules)) and rep['symbols'] == FIXED
    assert rep['sealed_cutoff'] == '2026-09-28T00:00:00Z' and not out.exists()
    assert not (cli_inputs.d / 'no-store').exists()                     # no archive opened in preflight
    assert _cli(cli_inputs, straddle=False) == 0                        # without the straddle artifact: not ready
    cov = json.loads(capsys.readouterr().out)['coverage']
    assert not cov['ready'] and len(cov['series_gaps']) == 17 * 3
    assert {g['uncovered_from'] for g in cov['series_gaps']} == {'2026-09-01T00:00:00Z'}
    man, _ = _meta_manifest(tmp_path / 'gap.json.gz', FIXED, drop={('ETHUSDT', 'mark', '2023-05')})
    assert _cli(cli_inputs, manifest=man) == 3                          # the straddle artifact binds the other manifest
    assert 'another source manifest' in capsys.readouterr().err
    assert _cli(cli_inputs, manifest=man, straddle=False) == 0
    gaps = [g for g in json.loads(capsys.readouterr().out)['coverage']['series_gaps'] if g['symbol'] == 'ETHUSDT']
    assert {'symbol': 'ETHUSDT', 'series': 'mark', 'uncovered_from': '2023-05-01T00:00:00Z'} in gaps and len(gaps) == 3
    assert _cli(cli_inputs, rules=False) == 0
    cov = json.loads(capsys.readouterr().out)['coverage']
    assert cov['rules_present'] is False and cov['ready'] is False


def test_cli_refuses_dirty_tree_bad_hashes_and_missing_rss_cap(cli_inputs, capsys):
    assert _cli(cli_inputs, git=DIRTY) == 3 and 'dirty' in capsys.readouterr().err
    with pytest.raises(SystemExit):                                     # --max-rss-mb is required
        R.main(['--store', 'x'], _inject={'git': CLEAN, 'manifest_check': NO_MANIFEST})
    capsys.readouterr()
    assert R.main(['--store', 'x', '--max-rss-mb', 'nan'], _inject={'git': CLEAN}) == 3
    ci = cli_inputs
    bad = ['--store', 'x', '--max-rss-mb', '512', '--rules-artifact', str(ci.rules), '--expect-rules-sha256', '0' * 64,
           '--rules-source-manifest', str(ci.rsrc), '--expect-rules-source-sha256', R.file_sha(str(ci.rsrc))]
    assert R.main(bad, _inject={'git': CLEAN, 'manifest_check': NO_MANIFEST, 'manifest_path': str(ci.man)}) == 3
    assert 'rules file sha256' in capsys.readouterr().err
    bad[bad.index('--expect-rules-sha256') + 1] = R.file_sha(str(ci.rules))
    bad[-1] = '0' * 64
    assert R.main(bad, _inject={'git': CLEAN, 'manifest_check': NO_MANIFEST, 'manifest_path': str(ci.man)}) == 3
    assert _cli(ci, '--expect-straddle-sha256', '1' * 64) == 3          # a later flag overrides the right hash
    assert '--allow-dirty' not in (ROOT / 'tools' / 'research' / 'bpyr_runner.py').read_text(encoding='utf-8')


def test_cli_never_evaluates_without_an_integration_loader(cli_inputs, capsys, tmp_path, monkeypatch, pf):
    out = tmp_path / 'out'
    auth = tmp_path / 'auth.json'
    auth.write_text('{}')
    assert _cli(cli_inputs, '--authorization', str(auth), '--out', str(out)) == 3
    assert 'expect-authorization-sha256' in capsys.readouterr().err and not out.exists()
    assert _cli(cli_inputs, '--authorization', str(auth), '--expect-authorization-sha256', R.file_sha(str(auth)),
                '--out', str(out)) == 3                                 # the real verifier: outside the committed dir
    assert 'research_evidence/authorizations/' in capsys.readouterr().err and not out.exists()
    for name in ('bpyr_eval_authorization', 'bpyr_auth'):                # a module planted on sys.path is never used
        monkeypatch.setitem(sys.modules, name, SimpleNamespace(load=lambda p, h: AUTH(pf)))
    assert _cli(cli_inputs, '--authorization', str(auth), '--out', str(out)) == 3 and not out.exists()
    assert 'importlib' not in (ROOT / 'tools' / 'research' / 'bpyr_runner.py').read_text(encoding='utf-8')
    calls = []

    def fake_execute(outdir, src, pf_, **kw):
        calls.append(kw)
        return {'candidate_id': R.CANDIDATE, 'prereg_sha256': pf_.prereg_sha256, 'identity_sha256': 'i' * 64,
                'evaluated': True, 'decided': False}
    monkeypatch.setattr(R, 'execute', fake_execute)
    lock = lambda *a: contextlib.nullcontext()                                           # noqa: E731
    bad_tok = lambda p, pf_: 'not a token'                                              # noqa: E731
    assert _cli(cli_inputs, '--authorization', str(auth), '--out', str(out), lock=lock, authorize=bad_tok) == 3 and not calls
    tok = lambda p, pf_: AUTH(pf_)                                                       # noqa: E731  test-only seam
    assert _cli(cli_inputs, '--authorization', str(auth), '--out', str(out), straddle=False, lock=lock, authorize=tok) == 3
    assert 'not ready' in capsys.readouterr().err and not calls          # not ready: refused before execute
    assert _cli(cli_inputs, '--authorization', str(auth), lock=lock, authorize=tok) == 3 and not calls   # no --out
    out.mkdir()
    assert _cli(cli_inputs, '--authorization', str(auth), '--out', str(out), lock=lock, authorize=tok) == 0
    assert calls[0]['authorization'] == AUTH(pf) and calls[0]['max_rss_mb'] == 512.0 and calls[0]['universe'] == 'FIXED-17'
    assert calls[0]['symbols'] == FIXED and calls[0]['rules']('BTCUSDT', T0)['tick'] == 0.0001
    written = json.loads(capsys.readouterr().out)['written']
    assert Path(written) == out / 'result.json' and 'verdict' not in (out / 'result.json').read_text()
    assert _cli(cli_inputs, '--authorization', str(auth), '--out', str(out), lock=lock, authorize=tok) == 3   # never overwritten


# ---- 3. gate_inputs: pure decide()-shaped assembly; the write path stays authorization-gated
def _g(stage='S2', s_data=True, **kw):
    g = {'data': True, 'activity': True, 'b0': {'status': 'OK', 'pass': True}, 'H1': True, 'H2': stage in ('S2', 'S3'),
         'H3': stage == 'S3', 'credited_stage': stage, 'data_by_stage': {s: {'pass': True} for s in ('S1', 'S2', 'S3')}}
    if stage:
        g['data_by_stage'][stage]['pass'] = s_data
    g.update(kw)
    return g


def _bk(stage='S2', caus=True, data=True, dd=True, tails=True):
    rep = {'data': {'pass': data}, 'causality': {'pass': caus, 'checks': {}}}
    return {'stage': stage, 'dd_tier': {'pass': dd}, 'tails_worst_ok': tails,
            'replays': {'0.01:base_x1': rep, '0.01:all_variable_costs_x1.5': {'data': {'pass': True},
                                                                               'causality': {'pass': True}}}}


def test_gate_inputs_are_exactly_what_decide_consumes():
    gi = R.gate_inputs(_g(), _bk(), 0.01)
    assert set(R.GATE_KEYS) <= set(gi) and R.decide(gi) == 'SURVIVES_DEV(S2)'
    assert R.decide(R.gate_inputs(_g(), _bk(caus=False), 0.01)) == 'FAIL'
    assert R.decide(R.gate_inputs(_g(), _bk(tails=False), 0.01)) == 'FAIL'
    assert R.decide(R.gate_inputs(_g(), _bk(dd=False), 0.01)) == 'FAIL'
    assert R.decide(R.gate_inputs(_g(), _bk(data=False), 0.01)) == 'INCOMPLETE'      # unresolved / book data state
    assert R.decide(R.gate_inputs(_g(s_data=False), _bk(), 0.01)) == 'INCOMPLETE'   # credited stage's own data gate
    for later in ('S2', 'S3'):                     # K9 precedence: an untestable later stage never falls back to S1/S2
        g = _g(stage='S1' if later == 'S2' else 'S2', H2=False if later == 'S2' else True)
        g['data_by_stage'][later]['pass'] = False
        gi = R.gate_inputs(g, _bk(stage=g['credited_stage']), 0.01)
        assert gi['data'] is False and R.decide(gi) == 'INCOMPLETE'
    # N6: an UNREACHED stage's gap never turns a valid earlier result into INCOMPLETE
    g = _g(stage=None, H1=False, H2=False)
    g['data_by_stage']['S2']['pass'] = g['data_by_stage']['S3']['pass'] = False
    gi = R.gate_inputs(g, None, 0.01)
    assert gi['data'] is True and R.decide(gi) == 'FALSIFIED'                 # H1 failed: S2 / S3 never reached
    g = _g(stage=None, H1=False, H2=True)                                    # inconsistent input: H2 without H1
    g['data_by_stage']['S3']['pass'] = False
    gi = R.gate_inputs(g, None, 0.01)
    assert gi['data'] is True and R.decide(gi) == 'FALSIFIED'                 # unreachable S3 gap cannot override H1
    g = _g(stage='S1', H2=False)
    g['data_by_stage']['S3']['pass'] = False
    gi = R.gate_inputs(g, _bk(stage='S1'), 0.01)
    assert gi['data'] is True and R.decide(gi) == 'SURVIVES_DEV(S1)'          # H2 failed: S3 never reached
    g = _g(stage=None, H1=False, H2=False)
    g['data_by_stage']['S1']['pass'] = False
    assert R.decide(R.gate_inputs(g, None, 0.01)) == 'INCOMPLETE'              # S1's own gate always applies
    assert R.decide(R.gate_inputs(_g(), None, 0.01)) == 'FAIL'                       # no credited-stage book: never survives
    none = R.gate_inputs(_g(stage=None, H1=False, H2=False), None, 0.01)
    assert R.decide(none) == 'FALSIFIED' and none['causality'] is False


CLOSED = (flat(2) + [(100.0, 100.3, 99.9, 100.2), (100.2, 103.0, 100.1, 102.9)] + flat(5, 102.9)
          + [(95.0, 95.2, 94.8, 95.0)] + flat(3, 95.0))                          # one entry, stopped out: closed


def test_book_causality_and_unresolved_rows_feed_data_and_causality(P):
    w = W(400)
    bk = R.run_book({'BTCUSDT': mk('BTCUSDT', CLOSED, le={0})},
                    P, 'S1', w, lambda s, t: RULE)
    m = R.book_metrics(bk, w)
    assert len(bk['trades']) == 1 and not bk['incomplete']
    assert m['causality']['pass'] and m['data']['pass'] and m['data']['unresolved_accounting'] == 0
    back = dict(bk, equity_seq=list(reversed(bk['equity_seq'])))
    assert R.book_causality(back, w)['checks']['equity_sequence_causal'] is False
    late = dict(bk, trades=[dict(bk['trades'][0], t_exit=w.sealed)])
    assert R.book_causality(late, w)['checks']['exits_before_seal'] is False
    un = R.run_book({'BTCUSDT': mk('BTCUSDT', CLOSED, le={0})},
                    P, 'S1', w, lambda s, t: None)
    mu = R.book_metrics(un, w)
    assert mu['unresolved_accounting'] == 1 and not mu['data']['pass'] and not mu['causality']['pass']


def test_run_assembles_gate_inputs_and_never_decides(pf, tmp_path):
    w = R.Windows.of(pf.prereg)
    data = {s: R.prepare_symbol(s, _walk(900, seed=k, start=T0 - 300 * TF), w) for k, s in enumerate(FIXED)}
    res = R.run(data, pf, rules=lambda s, t: RULE, resamples=50)
    assert res['evaluated'] is False and res['decided'] is False and not res['report_only']
    gi = res['gate_inputs']
    assert set(R.GATE_KEYS) <= set(gi) and gi['credited_stage'] == res['gates']['credited_stage']
    assert R.decide(gi) in ('INCOMPLETE', 'FALSIFIED', 'FAIL') or gi['credited_stage']      # pure, callable
    assert not any(v in json.dumps(res) for v in ('SURVIVES_DEV', 'FALSIFIED', '"FAIL"'))   # run() never decides
    res2 = R.run(data, pf, rules=lambda s, t: RULE, resamples=50, book_stage='S3')
    if res2['gates']['credited_stage'] != 'S3':                          # a book for another stage feeds nothing
        assert res2['gate_inputs']['book_dd']['pass'] is False and res2['gate_inputs']['causality'] is False
    ok = AUTH(pf)
    p = R.write_result(res, str(tmp_path / 'result.json'), authorization=ok)
    assert Path(p).read_bytes() == R.canon(R._json_safe(res))
    assert R._json_safe({'a': [math.inf, -math.inf, math.nan, 1.0]}) == {'a': ['Infinity', '-Infinity', 'NaN', 1.0]}
    with pytest.raises(FileExistsError):
        R.write_result(res, p, authorization=ok)
    with pytest.raises(R.WriteRefused):
        R.write_result(dict(res, verdict='x'), str(tmp_path / 'r2.json'), authorization=ok)
    with pytest.raises(R.WriteRefused):
        R.write_result(res, str(tmp_path / 'r3.json'), authorization=R.EvalAuthorization('cowork', '1', pf.prereg_sha256))


# ---- 4. load_authorization: the committed, identity-pinned verifier (temp git repo; no real artifact is committed)
import subprocess                                                                           # noqa: E402


def _git(repo, *a):
    return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t', '-c',
                           'core.autocrlf=false', '-c', 'commit.gpgsign=false', *a],
                          capture_output=True, text=True, check=True).stdout


def _git_bytes(repo, *a):
    return subprocess.run(['git', '-C', str(repo), *a], capture_output=True, check=True).stdout


def _auth_repo(tmp_path, pf, name='a.json', drop=(), between=False, raw=None, **over):
    """code commit c0, then (optionally an unrelated change, then) the artifact committed on top -> HEAD."""
    repo = tmp_path / 'repo'
    d = repo / 'research_evidence' / 'authorizations'
    d.mkdir(parents=True)
    _git(repo, 'init', '-q')
    (repo / 'code.py').write_text('x = 1\n')
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', 'code')
    c0 = _git(repo, 'rev-parse', 'HEAD').strip()
    if between:
        (repo / 'code.py').write_text('x = 2\n')
    fields = {'format': R.AUTH_FORMAT, 'candidate_id': R.CANDIDATE, 'prereg_sha256': pf.prereg_sha256,
              'issuer': 'codex', 'comment_id': '6478332951', 'code_commit': c0, 'scope': ['development']}
    fields.update(over)
    for k in drop:
        del fields[k]
    f = d / name
    f.write_bytes(raw if raw is not None else R.authorization_bytes(fields))
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', 'auth')
    head = _git(repo, 'rev-parse', 'HEAD').strip()
    return repo, f, R.Preflight(pf.prereg, pf.prereg_sha256, dict(pf.code, commit=head), pf.sources), c0


def _load(repo, f, pf_, sha=None):
    return R.load_authorization(str(f), pf_, expect_sha256=sha or R.file_sha(str(f)), repo=str(repo))


def test_load_authorization_accepts_a_committed_pinned_artifact(tmp_path, pf):
    repo, f, pf_, c0 = _auth_repo(tmp_path, pf)
    tok = _load(repo, f, pf_)
    assert isinstance(tok, R.EvalAuthorization)
    assert (tok.issuer, tok.comment_id, tok.prereg_sha256) == ('codex', '6478332951', pf.prereg_sha256)
    assert tok.artifact_sha256 == R.file_sha(str(f)) and tok.code_commit == c0 and tok.scope == ('development',)
    assert tok == R.EvalAuthorization('codex', '6478332951', pf.prereg_sha256, R.file_sha(str(f)), c0, ('development',))
    assert not list(ROOT.glob('research_evidence/authorizations/*'))     # no real artifact is committed


@pytest.mark.parametrize('over, drop, why', [
    ({'prereg_sha256': '0' * 64}, (), 'signed contract hash'),
    ({'issuer': 'cowork'}, (), 'issuer'),
    ({'comment_id': '64x1'}, (), 'comment_id'),
    ({'comment_id': 6478332951}, (), 'comment_id'),
    ({'code_commit': 'f' * 40}, (), 'not an ancestor'),
    ({'code_commit': 'F' * 40}, (), 'code_commit'),
    ({'scope': ['S1']}, (), 'scope'),
    ({'candidate_id': 'other.v1'}, (), 'candidate_id'),
    ({'format': 'zb-eval-authorization/2'}, (), 'format'),
    ({'extra': 1}, (), 'keys must be exactly'),
    ({}, ('scope',), 'keys must be exactly'),
])
def test_load_authorization_refuses_bad_fields(tmp_path, pf, over, drop, why):
    repo, f, pf_, _ = _auth_repo(tmp_path, pf, drop=drop, **over)
    with pytest.raises(R.AuthorizationRefused, match=why):
        _load(repo, f, pf_)


def test_load_authorization_refuses_unpinned_uncommitted_or_misplaced_files(tmp_path, pf):
    repo, f, pf_, c0 = _auth_repo(tmp_path, pf)
    with pytest.raises(R.AuthorizationRefused, match='sha256'):                     # hash mismatch
        _load(repo, f, pf_, sha='0' * 64)
    for bad in (None, 'A' * 64, 'abc'):
        with pytest.raises(R.AuthorizationRefused, match='expect-authorization-sha256'):
            R.load_authorization(str(f), pf_, expect_sha256=bad, repo=str(repo))
    u = f.with_name('b.json')                                                       # untracked
    u.write_bytes(f.read_bytes())
    with pytest.raises(R.AuthorizationRefused, match='not tracked'):
        _load(repo, u, pf_)
    out = repo / 'a.json'                                                           # outside the directory
    out.write_bytes(f.read_bytes())
    with pytest.raises(R.AuthorizationRefused, match='directly under'):
        _load(repo, out, pf_)
    with pytest.raises(R.AuthorizationRefused, match='directly under'):            # missing file
        _load(repo, f.with_name('nope.json'), pf_, sha='0' * 64)
    other = R.Preflight(pf_.prereg, pf_.prereg_sha256, dict(pf_.code, commit=c0), pf_.sources)
    with pytest.raises(R.AuthorizationRefused, match='preflight commit'):          # HEAD is not the preflight commit
        _load(repo, f, other)
    f.write_bytes(f.read_bytes().replace(b'6478332951', b'6478332952'))             # modified after commit
    with pytest.raises(R.AuthorizationRefused, match='differs from HEAD'):
        _load(repo, f, pf_)


def test_load_authorization_refuses_code_drift_and_non_canonical_bytes(tmp_path, pf):
    repo, f, pf_, _ = _auth_repo(tmp_path / 'drift', pf, between=True)              # code changed after code_commit
    with pytest.raises(R.AuthorizationRefused, match='more than this artifact'):
        _load(repo, f, pf_)
    fields = {'format': R.AUTH_FORMAT, 'candidate_id': R.CANDIDATE, 'prereg_sha256': pf.prereg_sha256,
              'issuer': 'codex', 'comment_id': '1', 'code_commit': '0' * 40, 'scope': ['development']}
    repo, f, pf_, _ = _auth_repo(tmp_path / 'pretty', pf, raw=json.dumps(fields, indent=1).encode())
    with pytest.raises(R.AuthorizationRefused, match='canonical'):
        _load(repo, f, pf_)
    dup = R.authorization_bytes(fields)[:-2] + b',"issuer":"codex"}\n'
    repo, f, pf_, _ = _auth_repo(tmp_path / 'dup', pf, raw=dup)
    with pytest.raises(R.AuthorizationRefused, match='duplicate'):
        _load(repo, f, pf_)


# ---- 4b. Codex 6496429137 (Cowork A1-A3): CR bytes, undecodable git output, renames
def test_load_authorization_refuses_any_cr_byte(tmp_path, pf):
    _, f, _, _ = _auth_repo(tmp_path, pf)
    crlf = f.read_bytes()[:-1] + b'\r\n'                                            # committed with CRLF
    repo, f, pf_, _ = _auth_repo(tmp_path / 'crlf', pf, raw=crlf)
    with pytest.raises(R.AuthorizationRefused, match='CR byte'):
        _load(repo, f, pf_)


def test_load_authorization_compares_raw_head_bytes_not_text(tmp_path, pf):
    """Codex 6496652384: HEAD holds CRLF bytes, the working tree is rewritten to canonical LF and pinned. A text-mode
    `git show` would fold the CRLF and compare equal; the raw-byte comparison refuses."""
    _, f0, _, _ = _auth_repo(tmp_path / 'lf', pf)
    lf = f0.read_bytes()
    repo, f, pf_, _ = _auth_repo(tmp_path / 'crlf', pf, raw=lf[:-1] + b'\r\n')
    assert _git_bytes(repo, 'show', 'HEAD:research_evidence/authorizations/a.json').endswith(b'\r\n')
    f.write_bytes(lf)
    with pytest.raises(R.AuthorizationRefused, match='differs from HEAD'):
        R.load_authorization(str(f), pf_, expect_sha256=R.file_sha(str(f)), repo=str(repo))


def test_load_authorization_types_undecodable_git_output(tmp_path, pf):
    repo, f, pf_, _ = _auth_repo(tmp_path, pf)
    real = lambda *a: _git(repo, *a)                                                     # noqa: E731

    def bad_show(*a):                                       # HEAD:<path> output that cannot be re-encoded
        return '\udcff' if a[0] == 'show' else real(*a)
    with pytest.raises(R.AuthorizationRefused, match='not decodable'):
        R.load_authorization(str(f), pf_, expect_sha256=R.file_sha(str(f)), repo=str(repo), git=bad_show)


def test_load_authorization_types_a_decode_error_in_the_real_git_wrapper(tmp_path, pf, monkeypatch):
    repo, f, pf_, _ = _auth_repo(tmp_path, pf)

    def undecodable(*a, **k):                               # subprocess text decoding fails inside real_git
        raise UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid start byte')
    monkeypatch.setattr(R.subprocess, 'run', undecodable)
    with pytest.raises(R.AuthorizationRefused, match='git ls-files failed'):
        _load(repo, f, pf_)


def test_load_authorization_refuses_a_rename_into_the_artifact(tmp_path, pf):
    """A3: code_commit holds a file that HEAD renames into the artifact. With rename detection `--name-only` would list
    only the artifact path; --no-renames lists the deleted source too, so the delta is refused."""
    repo = tmp_path / 'repo'
    d = repo / 'research_evidence' / 'authorizations'
    d.mkdir(parents=True)
    _git(repo, 'init', '-q')
    _git(repo, 'commit', '-q', '--allow-empty', '-m', 'root')
    fields = {'format': R.AUTH_FORMAT, 'candidate_id': R.CANDIDATE, 'prereg_sha256': pf.prereg_sha256,
              'issuer': 'codex', 'comment_id': '6478332951', 'code_commit': '0' * 40, 'scope': ['development']}
    (repo / 'old.json').write_bytes(R.authorization_bytes(fields))
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', 'code')
    c0 = _git(repo, 'rev-parse', 'HEAD').strip()
    _git(repo, 'mv', 'old.json', 'research_evidence/authorizations/a.json')
    f = d / 'a.json'
    f.write_bytes(R.authorization_bytes(dict(fields, code_commit=c0)))
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', 'auth')
    assert _git(repo, 'diff', '-M', '--name-only', c0, 'HEAD').split() == ['research_evidence/authorizations/a.json']
    head = _git(repo, 'rev-parse', 'HEAD').strip()
    pf_ = R.Preflight(pf.prereg, pf.prereg_sha256, dict(pf.code, commit=head), pf.sources)
    with pytest.raises(R.AuthorizationRefused, match='more than this artifact'):
        _load(repo, f, pf_)


# ---- 5. Codex review #239: strict ancestor, complete token, full run-identity binding
def test_load_authorization_refuses_code_commit_equal_to_head(tmp_path, pf):
    repo, f, pf_, c0 = _auth_repo(tmp_path, pf)
    real = lambda *a: _git(repo, *a)                                                     # noqa: E731

    def head_is_c0(*a):                                     # a HEAD that equals the authorized code_commit
        return c0 + '\n' if a == ('rev-parse', 'HEAD') else real(*a)
    pinned = R.Preflight(pf_.prereg, pf_.prereg_sha256, dict(pf_.code, commit=c0), pf_.sources)
    with pytest.raises(R.AuthorizationRefused, match='strict ancestor'):
        R.load_authorization(str(f), pinned, expect_sha256=R.file_sha(str(f)), repo=str(repo), git=head_is_c0)


def _forged(pf):
    import dataclasses
    ok = AUTH(pf)
    return [dataclasses.replace(ok, artifact_sha256=None), dataclasses.replace(ok, artifact_sha256='A' * 64),
            dataclasses.replace(ok, artifact_sha256='a' * 63), dataclasses.replace(ok, code_commit=None),
            dataclasses.replace(ok, code_commit='b' * 39), dataclasses.replace(ok, scope=()),
            dataclasses.replace(ok, scope=('S1',)), dataclasses.replace(ok, scope=['development']),
            dataclasses.replace(ok, scope=('development', 'holdout')),
            R.EvalAuthorization('codex', '6478332951', pf.prereg_sha256)]


def test_execute_and_write_result_refuse_incomplete_or_forged_tokens(pf, tmp_path):
    for i, tok in enumerate(_forged(pf)):
        out = tmp_path / f'e{i}'
        with pytest.raises(R.WriteRefused, match='incomplete authorization'):
            _execute(pf, out, authorization=tok)
        assert not out.exists()                                          # refused before anything is written
        res = {'candidate_id': R.CANDIDATE, 'prereg_sha256': pf.prereg_sha256, 'decided': False}
        with pytest.raises(R.WriteRefused, match='incomplete authorization'):
            R.write_result(res, str(tmp_path / f'r{i}.json'), authorization=tok)
        assert not (tmp_path / f'r{i}.json').exists()


def test_run_json_binds_the_complete_authorization_and_resume_under_another_fails(pf, tmp_path):
    import dataclasses
    out = tmp_path / 'r'
    _execute(pf, out, stop_after=1)
    got = json.loads((out / 'run.json').read_text())['authorization']
    assert got == {'issuer': 'codex', 'comment_id': '6478332951', 'prereg_sha256': pf.prereg_sha256,
                   'artifact_sha256': 'a' * 64, 'code_commit': 'b' * 40, 'scope': ['development']}
    for k, v in (('artifact_sha256', 'c' * 64), ('code_commit', 'd' * 40), ('comment_id', '6478332952')):
        with pytest.raises(R.RunnerError, match='identity'):
            _execute(pf, out, authorization=dataclasses.replace(AUTH(pf), **{k: v}))
    assert _execute(pf, out, stop_after=1)['symbols_done'] == 2           # the same authorization resumes


# ---- BPYR-INPUTS-01 (Codex ruling 6497964865 item 1): prospective rules + listing-aware coverage
RAW_SNAP_REL = 'research_evidence/prereg/binance-um-exchangeinfo-mainnet-raw-20261011T151658Z.json'
RAW_SNAP_SHA = 'b512355ad8ffcca88de5c5a1c9f30fb8a4418a957edc77f74c68003431e59c83'
WARM = R.iso_ms('2020-02-03T00:00:00Z')
SEP1 = R.iso_ms('2026-09-01T00:00:00Z')
LATE = 'AVAXUSDT'                                                   # synthetic listing 2020-09-23, like the real one
LATE_L = R.iso_ms('2020-09-23T07:00:00Z')
EARLY_L = R.iso_ms('2020-01-31T08:00:00Z')


def _next_ym(ym):
    y, m = map(int, ym.split('-'))
    return f'{y + m // 12:04d}-{m % 12 + 1:02d}'


def _snap_entry(sym, onboard=None, drop_field=False):
    by = {}
    for k, (ft, key) in FILTER_OF.items():
        by.setdefault(ft, {'filterType': ft})[key] = repr(float(FULL_RULE[k]))
    if drop_field:
        del by['MARKET_LOT_SIZE']['maxQty']
    e = {'symbol': sym, 'status': 'TRADING', 'filters': [by[ft] for ft in sorted(by)]}
    if onboard is not None:
        e['onboardDate'] = onboard
    return e


def _pro_repo(tmp_path, onboard, drop=(), strip=(), commit=True):
    """A throwaway git repo holding one synthetic raw exchangeInfo snapshot: (repo, path, sha256)."""
    repo = tmp_path / 'repo'
    repo.mkdir(parents=True)
    _git(repo, 'init', '-q')
    ents = [_snap_entry(s, onboard.get(s, EARLY_L), s in strip) for s in FIXED if s not in drop]
    raw = E5.canon({'timezone': 'UTC', 'symbols': ents})
    p = repo / 'snap.json'
    p.write_bytes(raw)
    if commit:
        _git(repo, 'add', 'snap.json')
        _git(repo, 'commit', '-qm', 's')
    return repo, p, R.sha(raw)


def _pro(tmp_path, onboard=None, **kw):
    repo, p, h = _pro_repo(tmp_path, {LATE: LATE_L} if onboard is None else onboard, **kw)
    return R.ProspectiveRules.load(str(p), h, FIXED, repo=str(repo))


def test_prospective_rules_accept_a_committed_snapshot_and_count_skips(tmp_path):
    pr = _pro(tmp_path, onboard={LATE: LATE_L, 'BNBUSDT': 'x', 'ADAUSDT': True}, drop=('ETHUSDT',),
              strip=('SOLUSDT',))
    ident = pr.identity()
    assert ident['mode'] == ident['non_promotable_rules'] == 'prospective' and ident['ruling'] == '6497964865'
    assert ident['snapshot_path'] == 'snap.json' and ident['rules_source']['sha256'] == ident['sha256']
    assert ident['skipped_symbols'] == {'ETHUSDT': 'absent_from_snapshot', 'SOLUSDT': 'missing_or_non_numeric_field'}
    assert ident['symbols_with_rules'] == 15
    assert ident['listing_skips'] == {'ADAUSDT': 'onboard_date_missing_or_invalid', 'BNBUSDT':
                                      'onboard_date_missing_or_invalid', 'ETHUSDT': 'absent_from_snapshot'}
    assert ident['onboard_dates'][LATE] == '2020-09-23T07:00:00Z' and pr.listing_ms('ADAUSDT') is None
    at = R.rules_of(pr)
    assert at('BTCUSDT', 0) == at('BTCUSDT', SEALED) and at('BTCUSDT', 0)['max_qty'] == 1e9   # causality bypassed
    assert at('ETHUSDT', T0) is None and at('SOLUSDT', T0) is None                         # never defaulted
    assert R.rules_identity(at)['sha256'] == ident['sha256'] and R.is_prospective(at)


def test_prospective_rules_refuse_uncommitted_modified_swapped_or_unbound_snapshots(tmp_path):
    repo, p, h = _pro_repo(tmp_path / 'a', {}, commit=False)
    with pytest.raises(R.RunnerError, match='not committed'):
        R.ProspectiveRules.load(str(p), h, FIXED, repo=str(repo))
    repo, p, h = _pro_repo(tmp_path / 'b', {})
    for bad in (None, '', 'xyz', h.upper()):
        with pytest.raises(R.RunnerError, match='expect-prospective-rules-sha256'):
            R.ProspectiveRules.load(str(p), bad, FIXED, repo=str(repo))
    with pytest.raises(R.RunnerError, match='differs from --expect-prospective'):          # hash swap
        R.ProspectiveRules.load(str(p), RAW_SNAP_SHA, FIXED, repo=str(repo))
    p.write_bytes(p.read_bytes().replace(b'"1000000000.0"', b'"9000000000.0"', 1))
    with pytest.raises(R.RunnerError, match='differs from --expect-prospective'):          # modified, old hash
        R.ProspectiveRules.load(str(p), h, FIXED, repo=str(repo))
    with pytest.raises(R.RunnerError, match='differ from HEAD'):                           # modified and re-bound
        R.ProspectiveRules.load(str(p), R.sha(p.read_bytes()), FIXED, repo=str(repo))
    _git(repo, 'checkout', '--', 'snap.json')
    p.write_bytes(p.read_bytes() + b'\r\n')
    with pytest.raises(R.RunnerError, match='differ from HEAD'):                           # raw bytes, no text folding
        R.ProspectiveRules.load(str(p), R.sha(p.read_bytes()), FIXED, repo=str(repo))
    out = tmp_path / 'outside.json'
    out.write_bytes(b'{}')
    with pytest.raises(R.RunnerError, match='inside the repo'):
        R.ProspectiveRules.load(str(out), R.sha(b'{}'), FIXED, repo=str(repo))


def test_committed_raw_snapshot_is_bound_and_covers_fixed_17():
    raw = (ROOT / RAW_SNAP_REL).read_bytes()
    assert R.sha(raw) == RAW_SNAP_SHA
    attr = _git(ROOT, 'check-attr', 'text', '--', RAW_SNAP_REL)
    assert attr.strip().endswith('text: unset')                                           # -text: never EOL-converted
    pr = R.ProspectiveRules.load(str(ROOT / RAW_SNAP_REL), RAW_SNAP_SHA, FIXED)
    ident = pr.identity()
    assert ident['symbols_with_rules'] == 17 and ident['skipped_symbols'] == {} and ident['listing_skips'] == {}
    assert ident['onboard_dates']['ENAUSDT'] == '2024-04-02T12:30:00Z'


@pytest.mark.parametrize('extra', [['--rules-artifact', 'r.json'], ['--rules-source-manifest', 'm.json'],
                                   ['--expect-rules-sha256', 'ab' * 32], ['--expect-rules-source-sha256', 'ab' * 32],
                                   ['--rules-snapshot-root', '.']])
def test_prospective_rules_cli_is_exclusive_with_the_pit_arguments(cli_inputs, extra, capsys):
    assert _cli(cli_inputs, '--prospective-rules', RAW_SNAP_REL, '--expect-prospective-rules-sha256', RAW_SNAP_SHA,
                *extra, rules=False) == 3
    assert 'mutually exclusive' in capsys.readouterr().err
    assert _cli(cli_inputs, '--expect-prospective-rules-sha256', RAW_SNAP_SHA, rules=False) == 3
    assert 'without --prospective-rules' in capsys.readouterr().err
    assert _cli(cli_inputs, '--prospective-rules', str(ROOT / RAW_SNAP_REL), '--expect-prospective-rules-sha256',
                '0' * 64, rules=False) == 3                                          # hash swap at the CLI
    assert 'differs from --expect-prospective' in capsys.readouterr().err


class _CovSrc:
    """Metadata-only source for coverage_report: monthly index entries per (sym, kind); Sep 2026 straddle covered."""
    def __init__(self, first, drop=(), straddle=True):
        self.index, self.straddle_month, self.straddle_skipped = {}, {}, 0
        cov = SimpleNamespace(covered=lambda s, k, lo, hi: straddle)
        self.straddle = SimpleNamespace(provenance=cov)
        for s in FIXED:
            for kind in R.SERIES_KINDS:
                ym, ents = _month(first.get(s, R.iso_ms('2020-02-01T00:00:00Z'))), []
                while ym <= '2026-08':
                    if (s, kind, ym) not in drop and (s, ym) not in drop:
                        ents.append((*_month_bounds(ym), 'p', 'h', 1))
                    ym = _next_ym(ym)
                self.index[(s, kind)] = ents
                self.straddle_month[(s, kind)] = SEP1


def _cov(P, src, pit):
    return R.coverage_report(src, pit, FIXED, WARM, R.Windows.of(P))


def _gaps(cov, sym):
    return sorted({(g['series'], g['uncovered_from']) for g in cov['series_gaps'] if g['symbol'] == sym})


def test_late_listed_symbol_starts_at_its_authenticated_listing(P, tmp_path):
    src = _CovSrc({LATE: R.iso_ms('2020-09-01T00:00:00Z')})
    cov = _cov(P, src, _pro(tmp_path))
    assert cov['ready'] is True and cov['series_gaps'] == [] and cov['rules_ok'] is True
    assert cov['non_promotable_rules'] == 'prospective'
    lb = cov['listing_boundaries']
    assert set(lb) == {LATE} and lb[LATE]['onboard_date'] == '2020-09-23T07:00:00Z'
    assert set(lb[LATE]['series']) == set(R.SERIES_KINDS)
    assert all(v == {'start': '2020-09-23T07:00:00Z', 'first_month': '2020-09-01T00:00:00Z', 'admitted': True}
               for v in lb[LATE]['series'].values())
    pit = _cov(P, src, None)                                                     # no bound snapshot: unchanged
    assert not pit['ready'] and _gaps(pit, LATE) == [(k, '2020-02-03T00:00:00Z') for k in sorted(R.SERIES_KINDS)]
    assert pit['listing_boundaries'] == {}


@pytest.mark.parametrize('first, onboard, drop, want', [
    # already listed before warm-up, early month missing: still a gap from warm-up
    ({LATE: R.iso_ms('2020-03-01T00:00:00Z')}, {LATE: EARLY_L}, (), '2020-02-03T00:00:00Z'),
    # late listing but its listing month is missing (first archived month after listing): refused
    ({LATE: R.iso_ms('2020-10-01T00:00:00Z')}, {LATE: LATE_L}, (), '2020-02-03T00:00:00Z'),
    # forged onboardDate LATER than the first archived month: refused
    ({LATE: R.iso_ms('2020-09-01T00:00:00Z')}, {LATE: R.iso_ms('2020-11-05T00:00:00Z')}, (), '2020-02-03T00:00:00Z'),
    # forged onboardDate EARLIER than the first archived month: refused
    ({LATE: R.iso_ms('2020-09-01T00:00:00Z')}, {LATE: R.iso_ms('2020-07-10T09:00:00Z')}, (), '2020-02-03T00:00:00Z'),
    # onboardDate not an int: no listing, refused
    ({LATE: R.iso_ms('2020-09-01T00:00:00Z')}, {LATE: str(LATE_L)}, (), '2020-02-03T00:00:00Z'),
    # admitted late start, then a mid-series hole: refused at the hole
    ({LATE: R.iso_ms('2020-09-01T00:00:00Z')}, {LATE: LATE_L}, ((LATE, '2023-05'),), '2023-05-01T00:00:00Z'),
    # the listing month missing in one series only (mark): that series refused, the others admitted
    ({LATE: R.iso_ms('2020-09-01T00:00:00Z')}, {LATE: LATE_L}, ((LATE, 'mark', '2020-09'),), None),
])
def test_listing_moves_only_the_initial_boundary_and_never_admits_a_hole(P, tmp_path, first, onboard, drop, want):
    cov = _cov(P, _CovSrc(first, drop=drop), _pro(tmp_path, onboard=onboard))
    assert cov['ready'] is False
    g = _gaps(cov, LATE)
    if want is None:
        assert g == [('mark', '2020-02-03T00:00:00Z')]
        assert cov['listing_boundaries'][LATE]['series']['mark']['admitted'] is False
        assert cov['listing_boundaries'][LATE]['series']['4h']['admitted'] is True
    else:
        assert g == [(k, want) for k in sorted(R.SERIES_KINDS)]
    assert all(g_['symbol'] == LATE for g_ in cov['series_gaps'])


def test_symbol_absent_from_snapshot_is_refused_in_series_and_rules(P, tmp_path):
    cov = _cov(P, _CovSrc({LATE: R.iso_ms('2020-09-01T00:00:00Z')}), _pro(tmp_path, drop=(LATE,)))
    assert not cov['ready'] and cov['rules_gaps'] == [LATE] and cov['rules_ok'] is False
    assert _gaps(cov, LATE) == [(k, '2020-02-03T00:00:00Z') for k in sorted(R.SERIES_KINDS)]


def test_straddle_still_required_after_a_late_start(P, tmp_path):
    cov = _cov(P, _CovSrc({LATE: R.iso_ms('2020-09-01T00:00:00Z')}, straddle=False), _pro(tmp_path))
    assert not cov['ready'] and {g['uncovered_from'] for g in cov['series_gaps']} == {'2026-09-01T00:00:00Z'}
    assert len(cov['series_gaps']) == 17 * 3


def test_rules_gaps_resolve_a_late_symbol_at_its_listing(P, tmp_path):
    late, L = 'ENAUSDT', R.iso_ms('2024-04-02T12:30:00Z')
    pr = _pro(tmp_path, onboard={late: L})
    seen = {}

    class Spy(R.ProspectiveRules):
        def lookup(self, sym, t):
            seen.setdefault(sym, []).append(t)
            return None if sym == late and t < L else super().lookup(sym, t)
    spy = Spy.__new__(Spy)
    spy.__dict__.update(pr.__dict__)
    cov = _cov(P, _CovSrc({late: R.iso_ms('2024-04-01T00:00:00Z')}), spy)
    assert cov['rules_gaps'] == [] and cov['ready'] is True
    assert seen[late][0] == L and seen['BTCUSDT'][0] == R.Windows.of(P).dev_start      # max(dev_start, listing)


def test_prospective_execute_binds_identity_and_labels_every_packet(pf, tmp_path):
    pr = _pro(tmp_path / 's')
    out = tmp_path / 'r'
    res = _execute(pf, out, rules=R.rules_of(pr))
    run = json.loads((out / 'run.json').read_text())
    ident = pr.identity()
    assert run['rules_identity'] == json.loads(R.canon(ident)) and run['rules_identity']['mode'] == 'prospective'
    assert run['rules_mode'] == run['non_promotable_rules'] == 'prospective'
    assert run['rules_snapshot'] == {'path': 'snap.json', 'sha256': ident['sha256']}
    lines = (out / 'symbols.jsonl').read_bytes().splitlines()
    assert len(lines) == 17 and all(json.loads(x)['non_promotable_rules'] == 'prospective' for x in lines)
    assert res['non_promotable_rules'] == res['rules_mode'] == 'prospective' and res['promotable'] is False
    assert res['rules_identity']['sha256'] == ident['sha256'] and res['rules_snapshot'] == run['rules_snapshot']
    other = _pro(tmp_path / 't', onboard={LATE: LATE_L + 1})                 # another snapshot: another run identity
    with pytest.raises(R.RunnerError, match='identity differs'):
        _execute(pf, out, rules=R.rules_of(other))
    bad = R.rules_of(pr)
    bad.identity = lambda: dict(ident, non_promotable_rules=None)            # an unlabelled prospective identity
    with pytest.raises(R.RunnerError, match='prospective rules identity'):
        _execute(pf, tmp_path / 'x', rules=bad)
    bad.identity = lambda: dict(ident, rules_source=dict(ident['rules_source'], sha256='e' * 64))
    with pytest.raises(R.RunnerError, match='prospective rules identity'):
        _execute(pf, tmp_path / 'x', rules=bad)
    assert not (tmp_path / 'x').exists()


def test_pit_default_path_carries_no_prospective_label(pf, tmp_path, cli_inputs, capsys):
    _execute(pf, tmp_path / 'p', stop_after=1)
    run = json.loads((tmp_path / 'p' / 'run.json').read_text())
    assert not {'rules_mode', 'non_promotable_rules', 'rules_snapshot'} & set(run)
    assert all('non_promotable_rules' not in json.loads(x)
               for x in (tmp_path / 'p' / 'symbols.jsonl').read_bytes().splitlines())
    assert _cli(cli_inputs) == 0
    rep = json.loads(capsys.readouterr().out)
    assert 'non_promotable_rules' not in rep and 'rules_mode' not in rep['coverage']
    assert rep['coverage']['listing_boundaries'] == {} and rep['coverage']['ready'] is True


def test_cli_preflight_with_the_committed_snapshot_admits_real_listings(tmp_path, capsys):
    snap = json.loads((ROOT / RAW_SNAP_REL).read_bytes())
    onboard = {e['symbol']: e['onboardDate'] for e in snap['symbols'] if e['symbol'] in FIXED}
    drop = set()
    for s, ob in onboard.items():
        ym = '2020-02'
        while ym < _month(ob):
            drop |= {(s, k, ym) for k in SER}
            ym = _next_ym(ym)
    man, _ = _meta_manifest(tmp_path / 'm.json.gz', FIXED, drop=drop)
    pro = ('--prospective-rules', str(ROOT / RAW_SNAP_REL), '--expect-prospective-rules-sha256', RAW_SNAP_SHA)
    assert _cli(SimpleNamespace(d=tmp_path, man=man), *pro, rules=False, straddle=False) == 0
    rep = json.loads(capsys.readouterr().out)
    cov = rep['coverage']
    assert rep['non_promotable_rules'] == rep['rules_mode'] == 'prospective'
    assert rep['rules_identity']['sha256'] == RAW_SNAP_SHA and rep['rules_identity']['snapshot_path'] == RAW_SNAP_REL
    assert rep['rules_snapshot'] == {'path': RAW_SNAP_REL, 'sha256': RAW_SNAP_SHA}
    assert cov['rules_ok'] is True and cov['rules_gaps'] == []
    assert {g['uncovered_from'] for g in cov['series_gaps']} == {'2026-09-01T00:00:00Z'}     # only the straddle
    late = {s for s, ob in onboard.items() if ob > WARM}
    assert set(cov['listing_boundaries']) == late and len(late) == 12
    assert all(v['admitted'] for s in late for v in cov['listing_boundaries'][s]['series'].values())
    man2, _ = _meta_manifest(tmp_path / 'm2.json.gz', FIXED, drop=drop | {('SOLUSDT', k, '2020-09') for k in SER})
    assert _cli(SimpleNamespace(d=tmp_path, man=man2), *pro, rules=False, straddle=False) == 0
    gaps = json.loads(capsys.readouterr().out)['coverage']['series_gaps']
    assert {(g['series'], g['uncovered_from']) for g in gaps if g['symbol'] == 'SOLUSDT'} == \
        {(k, '2020-02-03T00:00:00Z') for k in SER}                         # listing month missing: refused
