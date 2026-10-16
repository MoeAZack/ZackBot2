"""Wave B1: htf_pullback_scalp - EXPERIMENTAL / PAPER-only 15m scalp (completed 4h trend + 15m EMA20 pullback / reclaim).

Signal: fires LONG on a designed setup and SHORT on its exact price mirror, not on near-misses (weak reclaim close, 4h
context against, no pullback), reads only closed candles (future candles change nothing before them; prefix truncation),
the 4h context uses only COMPLETED 4h candles (the in-progress 4h candle - its remaining 15m candles, however extreme -
never changes the context or the signal; a gappy 4h bucket is never used), finite outputs, never both sides.
Engine: slot SC-HTFPB defaults OFF, is appended (sync only appends), and is refused in live mode.
Synthetic data only (tests/scalp_synth.py, tests/test_safety.FakeX)."""
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_safety import mk_engine                       # noqa: E402  (FakeX engine, PAPER)
import engine as E                                       # noqa: E402
import scalp_synth as Y                                  # noqa: E402
import strategies as S                                   # noqa: E402

KEY, SID = 'htf_pullback_scalp', 'SC-HTFPB'
PER = S.H4_MS // (15 * 60_000)                           # 15m candles per 4h candle


def sig(raw):
    d = S.indicators(raw)
    return S.signals(KEY, d, dict(btc_down=np.zeros(len(d), bool)), mask_sides=False), d


def ctx(raw):
    return S.htf_context(S.indicators(raw))


# ------------------------------------------------------------------ registry contract
def test_registry_entry_labelled_paper_experimental_symmetric_15m():
    st = S.STRATEGIES[KEY]
    assert st['sides'] == 'both' and st['tf'] == '15m' and 'PAPER' in st['name'] and S.SCALP_TAG in st['desc']
    for word in ('edge', 'profit', 'proven'):
        assert word not in st['desc'].lower() and word not in st['name'].lower(), word
    g = st['mgmt']
    assert g == dict(stop_atr=1.0, tp_r=1.5, max_bars=12)              # stop = the signal's atr (structural), 1.5R, 12 candles
    assert E.SCALP_SLOTS[SID] == KEY and list(E.SCALP_SLOTS)[-1] == SID and len(SID) <= 12
    assert KEY not in {k for gr in E.breadth_groups(tradfi=True) for k in gr}       # never rotated


# ------------------------------------------------------------------ signals
def test_fires_long_on_setup_and_short_on_mirror():
    raw = Y.htf_long()
    r, d = sig(raw)
    m, _ = sig(Y.mirror(raw))
    assert r['le'][-1] and not r['se'][-1]
    assert m['se'][-1] and not m['le'][-1]
    up, dn = ctx(raw)
    assert up[-1] and not dn[-1]
    mu, md = ctx(Y.mirror(raw))
    assert md[-1] and not mu[-1]
    assert r['atr'][-1] == pytest.approx(m['atr'][-1])                  # mirror: same stop distance


def _near_weak_close():
    """Closes back above EMA20 but in the middle of its range (a long upper wick): not in the outer 40 %."""
    x = Y.htf_long(); i = x.index[-1]
    x.loc[i, 'h'] = x.c.iloc[-1] + 3 * (x.c.iloc[-1] - x.l.iloc[-1])
    return x


def _near_htf_against():
    """The same 15m pullback / reclaim shape, but the completed 4h candles are in a DOWN-trend."""
    return Y.htf_long(Y.mirror(Y.htf_base()))


def _near_no_pullback():
    """A strong bullish close above EMA20 in the up-trend, but no candle touched EMA20 in the last 8."""
    x = Y.htf_base()
    d = S.indicators(x)
    lows_above = (d.l > d.e20).to_numpy()
    j = int(np.flatnonzero(~lows_above)[-1])                            # the last touch in the base tape
    x = x.iloc[:j + 1].reset_index(drop=True)
    for _ in range(S.HP['n'] + 1):                                      # 9 candles riding above EMA20, closing at the top
        e20 = float(S.indicators(x).e20.iloc[-1]); o = max(float(x.c.iloc[-1]), e20 + 0.8)
        x = Y.append(x, o, o + 0.45, o - 0.05, o + 0.4)
    assert (S.indicators(x).l.iloc[-S.HP['n']:] > S.indicators(x).e20.iloc[-S.HP['n']:]).all()
    return x


@pytest.mark.parametrize('near', (_near_weak_close, _near_htf_against, _near_no_pullback))
def test_no_entry_on_a_near_miss(near):
    raw = near()
    r, _ = sig(raw)
    m, _ = sig(Y.mirror(raw))
    assert not r['le'][-1] and not r['se'][-1] and not m['le'][-1] and not m['se'][-1]


def test_htf_against_is_a_true_near_miss_of_the_15m_shape():
    """The 15m part of the rule holds on the htf-against tape: only the 4h context blocks it."""
    raw = _near_htf_against()
    up, dn = ctx(raw)
    assert dn[-1] and not up[-1]
    d = S.indicators(raw)
    assert d.c.iloc[-1] > d.e20.iloc[-1] and (d.l.iloc[-3:] <= d.e20.iloc[-3:]).any()


def test_only_the_first_reclaim_after_a_touch_fires():
    raw = Y.htf_long()
    o = float(raw.c.iloc[-1])
    nxt = Y.append(raw, o, o + 0.65, o - 0.02, o + 0.6)                 # a second strong close above, no new touch
    r, d = sig(nxt)
    assert r['le'][-2] and d.l.iloc[-1] > d.e20.iloc[-1] and not r['le'][-1]


def test_stop_is_the_wider_of_atr_floor_and_the_pullback_extreme():
    raw = Y.htf_long()
    r, d = sig(raw)
    a, c, n = d.atr.iloc[-1], d.c.iloc[-1], S.HP['n']
    want = max(S.HP['stop_atr'] * a, c - d.l.iloc[-n:].min() + S.HP['buf_atr'] * a, S.HP['min_atr'] * a)
    assert r['atr'][-1] == pytest.approx(want) and want >= S.HP['stop_atr'] * a
    assert c - S.STRATEGIES[KEY]['mgmt']['stop_atr'] * r['atr'][-1] < d.l.iloc[-n:].min()      # beyond the pullback low


def test_cost_gate_blocks_small_atr():
    raw = Y.htf_long()
    hi_px = Y.shift_price(raw, 2000.0)                                   # same shapes, ATR% ~20x smaller
    r, d = sig(hi_px)
    assert not r['le'][-1] and not r['se'][-1]
    assert not S.scalp_cost_ok(d, stop_atr=S.HP['stop_atr']).iloc[-1]


# ------------------------------------------------------------------ causality: closed 15m candles + completed 4h candles only
def test_closed_candles_only_no_lookahead():
    raw = Y.htf_long()
    base, _ = sig(raw)
    for ext in ((1e6, 1e6, 1.0, 1e6), (1.0, 1e6, 0.5, 0.5)):           # a future candle with extreme values
        r, _ = sig(Y.append(raw, *ext, v=1e9))
        for k in ('le', 'se', 'lx', 'sx'): assert np.array_equal(r[k][:len(raw)], base[k]), k
        assert np.allclose(r['atr'][:len(raw)], base['atr'], equal_nan=True)
    for cut in (len(raw) - 300, len(raw) - 40, len(raw) - 1):            # prefix truncation
        r, _ = sig(raw.iloc[:cut].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'): assert np.array_equal(r[k], base[k][:cut]), (k, cut)
        assert np.allclose(r['atr'], base['atr'][:cut], equal_nan=True)


def test_htf_context_prefix_causal_across_4h_boundaries():
    """For every row around several 4h boundaries, the context computed on the prefix ending at that row equals the full
    frame's context at that row (the row sees exactly the 4h candles that had closed by its own close)."""
    raw = Y.htf_long()
    up, dn = ctx(raw)
    rows = [i for b in (52, 60, 68) for i in range(b * PER - 2, b * PER + 2) if i < len(raw)] + [len(raw) - 1]
    for i in rows:
        pu, pd_ = ctx(raw.iloc[:i + 1].reset_index(drop=True))
        assert (pu[-1], pd_[-1]) == (up[i], dn[i]), i


def test_in_progress_4h_candle_never_changes_context_or_signal():
    """The decision candle sits inside an in-progress 4h bucket. Completing that bucket with extreme 15m candles (a crash
    that makes the 4h candle close far below its EMA50) changes nothing at or before the decision candle - and DOES flip
    the context once the bucket has closed (so the test is sensitive)."""
    raw = Y.htf_long()
    i = len(raw) - 1
    left = PER - 1 - (int(pd.Timestamp(raw.t.iloc[-1]).value // 10**6) % S.H4_MS) // (15 * 60_000)
    assert left >= 1                                                     # the decision candle's 4h bucket is in progress
    base, _ = sig(raw)
    up0, dn0 = ctx(raw)
    fut = raw
    for _ in range(left):
        fut = Y.append(fut, 5.0, 5.2, 4.8, 5.0)                          # crash candles completing the 4h bucket
    r, _ = sig(fut)
    up1, dn1 = ctx(fut)
    for k in ('le', 'se', 'lx', 'sx'): assert np.array_equal(r[k][:i + 1], base[k]), k
    assert np.array_equal(up1[:i + 1], up0) and np.array_equal(dn1[:i + 1], dn0)
    assert r['le'][i] and up1[i]
    assert not up1[-1]                                                   # the completed crash 4h candle is used from its close
    assert all(up1[i + 1:-1])                                            # ...but never before it closed


def test_incomplete_4h_bucket_is_never_used():
    """A 4h bucket with a missing 15m candle is dropped; rows after it keep the previous completed 4h candle's context."""
    raw = Y.htf_long()
    b = 60
    gap = raw.drop(index=b * PER + 5).reset_index(drop=True)            # bucket 60 now has 15 of 16 candles
    d = S.indicators(gap)
    tm = pd.to_datetime(d.t).values.astype('datetime64[ms]').astype(np.int64)
    cnt = pd.Series(tm // S.H4_MS).value_counts()
    assert cnt[int(tm[b * PER] // S.H4_MS)] == PER - 1
    # replace bucket 60's last close by a crash: if the incomplete bucket were used, the row closing it would flip
    j = b * PER + PER - 2                                                # last row of bucket 60 in the gapped frame
    crash = gap.copy(); crash.loc[j, ['o', 'h', 'l', 'c']] = [5.0, 5.2, 4.8, 5.0]
    up_g, dn_g = S.htf_context(S.indicators(gap))
    up_c, dn_c = S.htf_context(S.indicators(crash))
    assert np.array_equal(up_c[:j + PER], up_g[:j + PER]) and np.array_equal(dn_c[:j + PER], dn_g[:j + PER])
    full = raw.copy(); k = b * PER + PER - 1                             # sensitivity: the same crash on the COMPLETE bucket
    full.loc[k, ['o', 'h', 'l', 'c']] = [5.0, 5.2, 4.8, 5.0]
    up_f, _ = S.htf_context(S.indicators(full))
    assert up_g[j] and not up_f[k]                                       # ...flips the context from that bucket's close


def test_outputs_finite_bool_and_never_both_sides():
    for raw in (Y.htf_long(), Y.mirror(Y.htf_long()), Y.htf_base(), Y.mirror(Y.htf_base()), Y.range_base(n=1100),
                Y.trend_base(n=1100)):
        r, d = sig(raw)
        for k in ('le', 'se', 'lx', 'sx'): assert r[k].dtype == bool and len(r[k]) == len(d)
        assert not (r['le'] & r['se']).any()
        ent = r['le'] | r['se']
        assert np.isfinite(r['atr'][ent]).all() and (r['atr'][ent] > 0).all()
    short = Y.htf_long().iloc[:700].reset_index(drop=True)              # < 50 completed 4h candles: no context, no entry
    r, _ = sig(short)
    assert not r['le'].any() and not r['se'].any()


# ------------------------------------------------------------------ engine slot
def slot(e, sid):
    return next(x for x in e.S['SLEEVES'] if x['id'] == sid)


def test_slot_defaults_off_and_is_appended_last():
    x = next(x for x in E.scalp_sleeves() if x['id'] == SID)
    assert x['enabled'] is False and x['key'] == KEY and x['tf'] == '15m' and x['sides'] == 'both'
    assert x['symbols'] == 'core8' and x['max_pos'] == E.SCALP_MAX_POS and x['mgmt'] == {}
    sl = E.breadth_sleeves()
    assert sl[-1]['id'] == SID and SID not in E.rotation_at(0)['active']


def test_sync_only_appends_the_new_slot(tmp_path):
    e, tmp = mk_engine(str(tmp_path)); e.apply_preset(E.ROTATION_PRESET)
    old = [x for x in e.S['SLEEVES'] if x['key'] != KEY]                 # an install saved before Wave B1 (SCALP-4 present)
    old[-1]['enabled'] = True; old[0]['share'] = 0.111                   # owner customisations
    e.S['SLEEVES'] = old; e.save_settings()
    snap = json.dumps(old, sort_keys=True)
    e2 = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)
    e2.data = e2.trade; e2.connect()
    assert json.dumps(e2.S['SLEEVES'][:len(old)], sort_keys=True) == snap
    assert [x['id'] for x in e2.S['SLEEVES'][len(old):]] == [SID] and slot(e2, SID)['enabled'] is False
    assert e2.sync_scalp_slots() == [] and len(e2.S['SLEEVES']) == len(old) + 1
    assert e2.entry_block(slot(e2, SID), 'BTCUSDT', 'LONG') == 'strategy slot switched off'


def test_refused_in_live_mode(tmp_path):
    e, _ = mk_engine(str(tmp_path)); e.apply_preset(E.ROTATION_PRESET)
    s = slot(e, SID); s['enabled'] = True
    assert e.scalp_block(s) is None and e.entry_block(s, 'BTCUSDT', 'LONG') is None
    e.live = True
    assert 'PAPER' in e.scalp_block(s) and 'PAPER' in (e.entry_block(s, 'BTCUSDT', 'LONG') or '')
    old = [x for x in e.S['SLEEVES'] if x['key'] != KEY]
    e.S['SLEEVES'] = old
    assert e.sync_scalp_slots() == [] and e.S['SLEEVES'] == old           # live never adds the slot
