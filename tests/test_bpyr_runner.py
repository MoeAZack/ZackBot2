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
    assert b['incomplete'] == [{'sym': 'BTCUSDT', 't_entry': T0 + TF, 'reason': 'incomplete.boundary'}]
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
    res = R.run(data, pf, rules=lambda s, t: RULE, b0_replicates=2, book_stage='S3')
    assert res['evaluated'] is False and 'verdict' not in json.dumps(res).lower() and set(res['stages']) == set(R.STAGE_IDS)
    assert res['B0']['status'] in ('OK', 'INCOMPLETE') and res['book']['stage'] == 'S3'
    with pytest.raises(R.IdentityMismatch):
        R.run({'LSKUSDT': data['BTCUSDT']}, pf)
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
