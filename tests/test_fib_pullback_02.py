"""FIB-PULLBACK-02 (Codex 6486879287): causal, symmetric 1h Fibonacci continuation with structure/participation
confirmation; research-only and fail-closed exactly like v1.

Synthetic only: no real candles, cache, store, settings, credentials or network. The wrapper test writes a throwaway
1h / 4h candle cache and a stub exchange-rule snapshot under tmp_path.
"""
import hashlib
import inspect
import json
import os
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import app as A
import engine as E
import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

KEY, V1 = 'fib_pullback_02', 'fib_pullback'
ENTRY = 258                               # the decision candle of the base tape
PREREG = 'research_evidence/prereg/fib_pullback_02.md'
PREREG_SHA256_LF = 'a71fa17c9ca28533a4b1c15ed15efc3ac690cf3e05cdf5b67bdc159684186fed'
# v1 at master 184ec2e: the signal source and its preregistration (LF bytes) must not move. (Its recorded result lives
# under the documentation tree, which executed code never reads - test_ci.test_nothing_executed_reads_docs.)
V1_SOURCE_SHA256 = '1f710fc4b8464e82358a153238b44401e4e8635d02aaed3e58915dfda88b1f90'
V1_DOCS = {'research_evidence/prereg/fib_pullback_01.md': '829d8b7684204294d740291cb9563fd9317e4756fe41b520e8d529c529b83d99'}


# ------------------------------------------------------------------ synthetic 1h tapes
def tape(n=300, c_edit=None, o_edit=None, hl_edit=None, v_edit=None, pre=None):
    """Up-trend, swing low at 250 (low 178), swing high at 253 (high 222), pullback into the zone at 256-257 and a
    confirmed reclaim at 258: span 44, L(0.382) 205.192, L(0.5) 200, L(0.618) 194.808, L(0.786) 187.416."""
    c = np.linspace(100.0, 196.0, n) if pre is None else np.r_[pre, np.zeros(n - len(pre))]
    c[250:260] = [196, 204, 212, 218, 215, 210, 202, 198, 203, 208]
    c[260:] = np.linspace(213, 225, n - 260)
    for i, x in (c_edit or {}).items(): c[i] = x
    o = np.r_[c[0], c[:-1]]
    o[258] = 198.0
    for i, x in (o_edit or {}).items(): o[i] = x
    h, l = np.maximum(o, c) + 0.5, np.minimum(o, c) - 0.5
    l[250], h[253] = 178.0, 222.0
    for (k, i), x in (hl_edit or {}).items(): (h if k == 'h' else l)[i] = x
    v = np.full(n, 1000.0)
    for i, x in (v_edit or {}).items(): v[i] = x
    t = pd.date_range('2025-01-01', periods=n, freq='1h')
    return pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=v))


def mirror(raw, ceiling=320.0):
    d = raw.copy()
    d['o'], d['c'] = ceiling - raw.o, ceiling - raw.c
    d['h'], d['l'] = ceiling - raw.l, ceiling - raw.h
    return d


def sig(raw, key=KEY, params=None):
    d = S.indicators(raw)
    return d, S.signals(key, d, dict(btc_down=np.zeros(len(d), dtype=bool)), params=params, mask_sides=False)


def entries(raw, params=None):
    _, s = sig(raw, params=params)
    return np.flatnonzero(s['le']).tolist(), np.flatnonzero(s['se']).tolist()


# ------------------------------------------------------------------ registry / fail-closed runtime exclusion
def test_registered_research_only_1h_one_parameter_set():
    st = S.STRATEGIES[KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'both' and st['fn'] is S.sig_fib_pullback_02
    assert st['params'] == S.FIB02 == dict(left=2, right=2, min_impulse_atr=3.0, zone_lo=0.382, trigger=0.5, zone_hi=0.618,
                                           invalid=0.786, body_atr=0.35, vol_median_bars=20)
    assert st['mgmt'] == S.STRATEGIES[V1]['mgmt']                                  # frozen management reused


def test_absent_from_paper_rotation_settings_and_slots():
    assert KEY in E.RESEARCH_ONLY_KEYS and V1 in E.RESEARCH_ONLY_KEYS
    for tradfi in (False, True):
        assert KEY not in {k for g in E.breadth_groups(tradfi=tradfi) for k in g}
        assert KEY not in {x['key'] for x in E.breadth_sleeves(tradfi=tradfi)}
    assert KEY not in E.SCALP_KEYS and KEY not in {v[0] for v in E.CANDIDATE_SLOTS.values()}
    assert KEY not in {x.get('key') for p in E.PRESETS.values() for x in p.get('sleeves', [])}


def test_validate_sleeve_refuses_without_allow_research():
    sl = dict(key=KEY, share=0.2, risk=0.01, max_pos=2)
    with pytest.raises(ValueError, match='research-only'):
        A.validate_sleeve(sl, 0)
    out = A.validate_sleeve(sl, 0, allow_research=True)                            # /api/backtest only
    assert out['key'] == KEY and out['tf'] == '1h'


def test_engine_load_guard_drops_persisted_v2_sleeve(monkeypatch, tmp_path):
    bad = dict(PRESET='custom', SLEEVES=[dict(id='FIB2', key=KEY), dict(id='FIB1', key=V1)], UNIVERSE=['BTCUSDT'])
    monkeypatch.setattr(E.Engine, '_load_safe', lambda *a, **k: (bad, 'ok'))
    alerts = []
    bare = SimpleNamespace(F={'settings': str(tmp_path / 'settings.json')}, _installed=True, live=False,
                           _integrity_alert=lambda *x: alerts.append(x))
    E.Engine.load_settings(bare)
    assert bare.S['SLEEVES'] == []
    assert alerts and alerts[-1][0] == 'integrity|research-only-sleeve' and KEY in alerts[-1][1]


# ------------------------------------------------------------------ symmetry, structural stop, causality
def test_base_tape_fires_once_and_mirror_gives_the_mirrored_short():
    dl, lg = sig(tape())
    ds, sh = sig(mirror(tape()))
    assert np.flatnonzero(lg['le']).tolist() == np.flatnonzero(sh['se']).tolist() == [ENTRY]
    assert not lg['se'].any() and not sh['le'].any()
    for f in ('lx', 'sx'):
        np.testing.assert_array_equal(lg[f], sh['sx' if f == 'lx' else 'lx'])
    span = 222.0 - 178.0
    long_inv, short_inv = 222.0 - 0.786 * span, (320.0 - 222.0) + 0.786 * span
    assert lg['atr'][ENTRY] == pytest.approx(dl.c.iloc[ENTRY] - long_inv)
    assert sh['atr'][ENTRY] == pytest.approx(short_inv - ds.c.iloc[ENTRY])
    assert lg['atr'][ENTRY] == pytest.approx(sh['atr'][ENTRY])


def test_pivots_are_2_2_and_usable_only_two_candles_later():
    d = S.indicators(tape())
    ph, pl = S.fib02_pivots(d)
    assert ph[253 + 2] and not ph[253] and not ph[253 + 1]                          # high at 253 usable from 255
    assert pl[250 + 2] and not pl[250] and not pl[250 + 1]                          # low at 250 usable from 252
    assert np.flatnonzero(ph[:ENTRY + 1]).tolist() == [255]
    # with the pivot high not yet confirmed there is no impulse: a prefix ending at 254 cannot hold any entry
    assert entries(tape().iloc[:255].copy()) == ([], [])


@pytest.mark.parametrize('mirrored', [False, True])
def test_prefix_causality(mirrored):
    raw = mirror(tape()) if mirrored else tape()
    _, full = sig(raw)
    for k in (252, 255, 257, ENTRY, ENTRY + 1, 280, len(raw)):
        _, part = sig(raw.iloc[:k].copy())
        for f in ('le', 'se', 'lx', 'sx'):
            np.testing.assert_array_equal(part[f], full[f][:k])
        np.testing.assert_allclose(part['atr'], full['atr'][:k], equal_nan=True)


# ------------------------------------------------------------------ each entry condition gates independently
def _conds(raw, i=ENTRY):
    """The candle-local entry conditions at i (long), computed independently of the signal function."""
    d = S.indicators(raw)
    f500, f786 = 200.0, 222.0 - 0.786 * 44.0
    c, o, h, v, a = d.c.to_numpy(), d.o.to_numpy(), d.h.to_numpy(), d.v.to_numpy(), d.atr.to_numpy()
    med = np.median(v[i - 20:i])
    return dict(reclaim=c[i - 1] <= f500 < c[i], prior_break=c[i] > h[i - 1], body=c[i] - o[i] >= 0.35 * a[i],
                volume=v[i] >= med, above_786=c[i] > f786, ema=d.e50.iloc[i] > d.e200.iloc[i],
                impulse=44.0 >= 3.0 * a[i])


GATES = {
    # name: (tape kwargs breaking exactly that condition, the condition that must be the one failing at ENTRY or None)
    'volume': (dict(v_edit={ENTRY: 999.0}), 'volume'),
    'body': (dict(o_edit={ENTRY: 202.0}), 'body'),
    'prior_break': (dict(hl_edit={('h', 257): 203.25}), 'prior_break'),
    'reclaim': (dict(c_edit={257: 201.0}), 'reclaim'),
    # never overlaps 0.382-0.618 before the entry candle: 254-256 stay above 205.19, 257 gaps below 194.81
    'zone_arming': (dict(c_edit={254: 215.0, 255: 210.0, 256: 207.0, 257: 190.0}, o_edit={257: 192.0, ENTRY: 190.0}), None),
    # 257 closes through 0.786 (187.416): disarmed before the otherwise valid reclaim at 258
    'invalidation_0786': (dict(c_edit={257: 187.0}, o_edit={ENTRY: 187.0}), None),
    # down-trend history: EMA50 < EMA200 at the entry candle, same local structure
    'ema_alignment': (dict(pre=np.linspace(400.0, 196.0, 250)), 'ema'),
}


@pytest.mark.parametrize('name', sorted(GATES))
@pytest.mark.parametrize('mirrored', [False, True])
def test_each_gate_blocks_on_its_own(name, mirrored):
    kw, failing = GATES[name]
    raw = tape(**kw)
    conds = _conds(raw)
    others = {k: v for k, v in conds.items() if k != failing}
    if failing:
        assert not conds[failing], (name, conds)
    assert all(others.values()), (name, conds)                       # only the gate under test fails at the entry candle
    got = entries(mirror(raw) if mirrored else raw)
    assert got == ([], []), (name, got)
    assert entries(mirror(tape()) if mirrored else tape()) == (([], [ENTRY]) if mirrored else ([ENTRY], []))


def test_invalidation_raises_the_exit_signal():
    _, s = sig(tape(c_edit={257: 187.0}, o_edit={ENTRY: 187.0}))
    assert s['lx'][257] and not s['lx'][256]


@pytest.mark.parametrize('field,value_of', [
    ('min_impulse_atr', lambda d: 44.0 / d.atr.iloc[ENTRY]),
    ('body_atr', lambda d: (d.c.iloc[ENTRY] - d.o.iloc[ENTRY]) / d.atr.iloc[ENTRY])])
def test_impulse_and_body_thresholds_are_exact(field, value_of):
    d = S.indicators(tape())
    x = float(value_of(d))
    assert x > S.FIB02[field]                                         # the frozen threshold passes on the base tape
    assert entries(tape(), {field: x * (1 + 1e-9)})[0] == []
    assert entries(tape(), {field: x * (1 - 1e-9)})[0] == [ENTRY]


def test_volume_median_excludes_the_candle_itself_and_needs_twenty():
    assert entries(tape(v_edit={ENTRY: 1000.0}))[0] == [ENTRY]       # equal to the median passes
    hist = {i: 2000.0 for i in range(ENTRY - 10, ENTRY)}              # 10 of the prior 20 at 2000: median 1500
    assert entries(tape(v_edit={**hist, ENTRY: 1499.0}))[0] == []
    assert entries(tape(v_edit={**hist, ENTRY: 1500.0}))[0] == [ENTRY]


def test_one_entry_per_impulse():
    # after the 258 entry price falls back under 0.5 and reclaims again with a full confirmation at 261
    raw = tape(c_edit={259: 199.0, 260: 198.0, 261: 206.0})
    assert _conds(raw, 261)['reclaim'] and _conds(raw, 261)['prior_break'] and _conds(raw, 261)['body']
    assert entries(raw)[0] == [ENTRY]


def test_bad_fibonacci_order_rejected():
    with pytest.raises(ValueError, match='zone_lo < trigger < zone_hi'):
        entries(tape(), dict(trigger=0.7))


# ------------------------------------------------------------------ v1 unchanged
def test_v1_unchanged():
    st = S.STRATEGIES[V1]
    assert st['fn'] is S.sig_fib_pullback and st['tf'] == '4h' and st['research_only'] is True
    assert S.FIB == dict(left=3, right=3, min_impulse_atr=4.0, zone_lo=0.382, trigger=0.5, zone_hi=0.618, invalid=0.786)
    assert st['params'] == S.FIB
    assert st['mgmt'] == dict(stop_atr=1.0, tp1_r=1.0, tp1_frac=0.5, be_r=1.0, tp_r=2.0, max_bars=30)
    assert hashlib.sha256(inspect.getsource(S.sig_fib_pullback).encode()).hexdigest() == V1_SOURCE_SHA256
    for rel, want in V1_DOCS.items():
        with open(os.path.join(ROOT, rel), 'rb') as f:
            assert hashlib.sha256(f.read().replace(b'\r\n', b'\n')).hexdigest() == want, rel


# ------------------------------------------------------------------ preregistration frozen before evaluation
def test_prereg_frozen_and_complete():
    with open(os.path.join(ROOT, PREREG), 'rb') as f:
        raw = f.read().replace(b'\r\n', b'\n')
    assert hashlib.sha256(raw).hexdigest() == PREREG_SHA256_LF
    md = raw.decode('ascii')
    for s in ('FROZEN / NOT EVALUATED / RESEARCH ONLY', '`fib_pullback_02`', '6486879287', '2-left / 2-right',
              '3.0 x ATR(14)', '0.382-0.618', 'close[i] > high[i-1]', '0.35 x ATR(14)', 'median(volume[i-20 .. i-1])',
              'L(0.786)', "next candle's open", 'E1 timeframe', 'E2 exchange floors', 'CORE-20',
              '2026-09-28T00:00:00Z exclusive', 'net expectancy (mean net R per closed trade) is <= 0',
              'anchored walk-forward', 'Two cells (long, short), one parameter set', 'holdout'):
        assert s in md, s


# ------------------------------------------------------------------ the Phase-B wrapper (synthetic cache only)
@pytest.fixture
def fx():
    import run_breadth_candidate as B
    import run_fib02_candidate as W
    return B, W


def test_frozen_runner_alone_still_refuses_1h(fx):
    B, _ = fx
    with pytest.raises(B.RunnerError, match='BLOCKED: no 1h cache'):
        B.check_key(KEY)
    assert '1h' not in B.TFS


def _rules(tmp_path, env='mainnet', verified=True):
    p = tmp_path / f'rules_{env}.json'
    snap = dict(environment=env, verified=verified, provenance='direct_fetch', fetched_at='2026-10-11T15:16:58Z',
                schema='zackbot.exchange_rules/1',
                symbols={s: dict(step=0.001, min_qty=0.001, min_notional=5.0, tick=0.01)
                         for s in ('BTCUSDT', 'ETHUSDT', 'SOLUSDT')})
    p.write_text(json.dumps(snap))
    return str(p)


def _cache_1h(d, VT):
    os.makedirs(d, exist_ok=True)
    H1 = 3_600_000
    t = np.arange(VT.LO - 220 * H1 - 12 * H1, VT.HI + 40 * H1, H1, dtype=np.int64)
    for j, s in enumerate(['BTCUSDT'] + VT.SYMS):
        rng = np.random.default_rng(j + 11)
        n = len(t)
        c = 100.0 * (j + 1) * np.exp(np.cumsum(rng.standard_t(4, n) * 0.004) + 0.06 * np.sin(np.arange(n) / 9.0))
        o = np.r_[c[0], c[:-1]]
        w = np.abs(rng.normal(0, 0.003, n)) * c
        v = np.round(800.0 + 900 * rng.random(n), 3)
        pd.DataFrame(dict(t=t.astype(float), o=o, h=np.maximum(o, c) + w, l=np.minimum(o, c) - w, c=c, v=v,
                          qv=(c * v))).to_csv(os.path.join(d, f'{s}_1h.csv'), index=False)
    return d


def test_wrapper_runs_frozen_runner_with_scoped_equivalents(fx, tmp_path):
    B, W = fx
    import backtest as BT
    import test_vwaprev_candidate_runner as VT
    before_tfs, before_run = dict(B.TFS), BT.run
    c1 = _cache_1h(str(tmp_path / 'c1h'), VT)
    c4 = VT.write_cache(str(tmp_path / 'c4h'), VT.frames())
    repo = VT.git_repo(str(tmp_path / 'repo'))
    out = str(tmp_path / 'long.json')
    pkt = W.run(cache=c1, regime_cache=c4, window=VT.WINDOW, out=out, side='long', symbols=VT.SYMS, repo=repo,
                rules_path=_rules(tmp_path))
    assert B.TFS == before_tfs and BT.run is before_run                       # E1 / E2 restored after the call
    assert pkt['strategy']['key'] == KEY and pkt['window']['tf'] == '1h' and pkt['side'] == 'long'
    assert pkt['strategy']['mgmt_mode'] == 'defaults' and pkt['strategy']['mgmt_override'] == {}
    assert pkt['window']['end'] <= B.SEALED_CUTOFF and pkt['non_promotable'] is True
    assert pkt['costs']['fund_per_bar'] == pytest.approx(BT.FUND_PER_BAR / 4)
    f = pkt['fib02']
    assert f['feasibility']['mode'] == 'rules' and f['exchange_rules']['environment'] == 'mainnet'
    assert f['prereg_sha256_lf'] == PREREG_SHA256_LF and 'mainnet snapshot' in pkt['costs']['exchange_rules']
    body = {k: v for k, v in pkt.items() if k != 'packet_sha256'}
    assert hashlib.sha256(B._canon(body)).hexdigest() == pkt['packet_sha256']
    with open(out, 'rb') as fh:
        assert json.loads(fh.read())['packet_sha256'] == pkt['packet_sha256']
    with pytest.raises(W.Fib02Error, match='never overwritten'):
        W.run(cache=c1, regime_cache=c4, window=VT.WINDOW, out=out, side='long', symbols=VT.SYMS, repo=repo,
              rules_path=_rules(tmp_path))


def test_wrapper_refuses_holdout_and_non_mainnet_rules(fx, tmp_path):
    B, W = fx
    import test_vwaprev_candidate_runner as VT
    c1 = _cache_1h(str(tmp_path / 'c1h'), VT)
    repo = VT.git_repo(str(tmp_path / 'repo'))
    with pytest.raises(W.Fib02Error, match='verified direct-fetch mainnet'):
        W.run(cache=c1, regime_cache=c1, window=VT.WINDOW, out=str(tmp_path / 'x.json'), side='short',
              symbols=VT.SYMS, repo=repo, rules_path=_rules(tmp_path, env='testnet'))
    with pytest.raises(B.RunnerError, match='sealed|holdout'):
        W.run(cache=c1, regime_cache=c1, window='2026-09-28T00:00:00Z/2026-10-12T00:00:00Z',
              out=str(tmp_path / 'y.json'), side='short', symbols=VT.SYMS, repo=repo, rules_path=_rules(tmp_path))
    assert '1h' not in B.TFS
    assert not os.path.exists(tmp_path / 'x.json') and not os.path.exists(tmp_path / 'y.json')


def test_wrapper_cli_has_no_grid_symbol_or_mgmt_override(fx):
    _, W = fx
    src = inspect.getsource(W.main)
    for flag in ('--symbols', '--mgmt', '--params', '--key', '--tp1', '--be-r', '--max-bars'):
        assert flag not in src, flag
