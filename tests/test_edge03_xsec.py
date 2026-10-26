"""Synthetic-only tests for the isolated EDGE-03 cross-sectional candidate."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import edge03_xsec as X                                                                    # noqa: E402


N = 220
START = pd.Timestamp('2025-01-01T00:00:00Z').value // 1_000_000


def frames(slopes=None, n=N):
    slopes = slopes or {}
    out = {}
    t = START + np.arange(n, dtype=np.int64) * X.TF_MS
    for j, symbol in enumerate(X.CORE_10_ELIGIBLE_9):
        slope = slopes.get(symbol, (j - 4) * 0.00035)
        # Non-zero deterministic return variance without changing the ordering by drift.
        r = slope + 0.0002 * np.sin(np.arange(n) * 0.31 + j)
        c = (100.0 + j) * np.exp(np.cumsum(r))
        o = np.r_[c[0], c[:-1]]
        w = c * 0.003
        out[symbol] = pd.DataFrame(dict(t=t, o=o, h=np.maximum(o, c) + w,
                                             l=np.minimum(o, c) - w, c=c))
    return out


def decision_times(fs):
    return [int(t) + X.TF_MS for t in fs['BTCUSDT'].t if (int(t) + X.TF_MS) % X.DAY_MS == 0]


def funding_tape(fs, rate=0.0, missing=(), late=()):
    """Explicit synthetic 08:00/16:00 UTC settlements; never derived inside the candidate."""
    lo = min(int(d.t.min()) for d in fs.values())
    hi = max(int(d.t.max()) for d in fs.values()) + X.TF_MS
    interval = 2 * X.TF_MS
    first = ((lo + interval - 1) // interval) * interval
    settlements = tuple(range(first, hi + 1, interval))
    records = []
    missing = set(missing)
    late = set(late)
    for symbol, d in fs.items():
        for t in settlements:
            if (symbol, t) in missing:
                continue
            i = int(np.searchsorted(d.t.to_numpy(np.int64), t))
            mark = float(d.c.iloc[min(i, len(d) - 1)])
            records.append(X.FundingRecord(symbol, t, t + (1 if (symbol, t) in late else 0), rate, mark))
    return X.FundingTape(settlements, records)


def test_literal_midnight_causal_180_sample_score_and_next_open():
    fs = frames()
    d0 = next(t for t in decision_times(fs) if t >= START + 181 * X.TF_MS)
    d = X.rank_at(X.prepare(fs), d0)
    assert d.close_ms % X.DAY_MS == 0 and not d.skipped and len(d.eligible) == 9
    top = d.ranking[0]
    f = fs[top.symbol]
    j = np.flatnonzero(f.t.to_numpy() == d0 - X.TF_MS)[0]
    lr = np.diff(np.log(f.c.to_numpy()[j - 180:j + 1]))
    assert top.score == pytest.approx(np.log(f.c.iloc[j] / f.c.iloc[j - 180]) / np.std(lr, ddof=1))
    with pytest.raises(X.XsecError, match='00:00 UTC'):
        X.rank_at(X.prepare(fs), d0 + X.TF_MS)
    r = X.run_side(fs, 'long', funding=funding_tape(fs), start_ms=d0, end_ms=d0 + X.DAY_MS)
    assert r.trades and all(t.entry_ms == d0 for t in r.trades)
    assert all(t.exit_ms <= t.entry_ms + 6 * X.TF_MS for t in r.trades)


def test_rank_ties_break_by_symbol_and_qualification_does_not_cross_sign():
    fs = frames({s: 0.0003 for s in X.CORE_10_ELIGIBLE_9})
    # Identical price paths make exact score ties, while symbol-dependent levels do not affect log returns.
    for s in X.CORE_10_ELIGIBLE_9:
        fs[s].loc[:, ['o', 'h', 'l', 'c']] = fs['BTCUSDT'][['o', 'h', 'l', 'c']].to_numpy()
    d0 = next(t for t in decision_times(fs) if t >= START + 181 * X.TF_MS)
    d = X.rank_at(X.prepare(fs), d0)
    assert [r.symbol for r in d.ranking] == sorted(X.CORE_10_ELIGIBLE_9)
    assert d.long == tuple(sorted(X.CORE_10_ELIGIBLE_9)[:2])
    assert d.short == ()


def test_future_mutation_cannot_change_prior_decision_or_trade():
    fs = frames()
    d0 = next(t for t in decision_times(fs) if t >= START + 181 * X.TF_MS)
    end = d0 + X.DAY_MS
    before = X.run_side(fs, 'long', funding=funding_tape(fs), start_ms=d0, end_ms=end)
    changed = {s: d.copy() for s, d in fs.items()}
    for d in changed.values():
        m = d.t >= end + X.TF_MS
        d.loc[m, ['o', 'h', 'l', 'c']] *= 1000.0
    after = X.run_side(changed, 'long', funding=funding_tape(changed), start_ms=d0, end_ms=end)
    assert before == after


def test_eligibility_requires_eight_complete_symbols_and_never_compresses_below():
    fs = frames()
    d0 = next(t for t in decision_times(fs) if t >= START + 181 * X.TF_MS)
    open0 = d0 - X.TF_MS
    one_gap = {s: d.copy() for s, d in fs.items()}
    one_gap['SUIUSDT'] = one_gap['SUIUSDT'][one_gap['SUIUSDT'].t != open0 - 10 * X.TF_MS].reset_index(drop=True)
    ok = X.rank_at(X.prepare(one_gap), d0)
    assert not ok.skipped and len(ok.ranking) == 8 and 'SUIUSDT' not in ok.eligible
    two_gap = {s: d.copy() for s, d in one_gap.items()}
    two_gap['UNIUSDT'] = two_gap['UNIUSDT'][two_gap['UNIUSDT'].t != open0 - 11 * X.TF_MS].reset_index(drop=True)
    blocked = X.rank_at(X.prepare(two_gap), d0)
    assert blocked.skipped == 'eligible 7/8' and blocked.ranking == () and blocked.long == blocked.short == ()


def test_stop_gap_time_cap_full_bar_lock_and_side_isolation():
    # All-positive paths keep the same two long leaders selected on consecutive days.
    fs = frames({s: 0.0004 + i * 0.00001 for i, s in enumerate(X.CORE_10_ELIGIBLE_9)}, n=240)
    times = [t for t in decision_times(fs) if t >= START + 181 * X.TF_MS]
    d0 = times[0]
    tape = funding_tape(fs)
    res = X.run_side(fs, 'long', funding=tape, start_ms=d0, end_ms=d0 + 2 * X.DAY_MS)
    assert len(res.trades) == 2 and {t.exit_reason for t in res.trades} == {'time'}
    assert all(t.exit_ms == t.entry_ms + 6 * X.TF_MS for t in res.trades)
    assert set(res.blocked_by_lock) == {(d0 + X.DAY_MS, s) for s in X.rank_at(X.prepare(fs), d0).long}
    # The short evaluator owns separate state; long time locks never appear in it.
    short = X.run_side(fs, 'short', funding=tape, start_ms=d0, end_ms=d0 + 2 * X.DAY_MS)
    assert short.blocked_by_lock == ()

    # Force a gap through one selected long's stop on its second holding bar.
    g = {s: d.copy() for s, d in fs.items()}
    selected = X.rank_at(X.prepare(g), d0).long[0]
    base = X.run_side(g, 'long', funding=funding_tape(g), start_ms=d0, end_ms=d0 + X.DAY_MS)
    victim = next(t for t in base.trades if t.symbol == selected)
    i = g[selected].index[g[selected].t == d0 + X.TF_MS][0]
    g[selected].loc[i, ['o', 'h', 'l', 'c']] = victim.stop * 0.98
    stopped = X.run_side(g, 'long', funding=funding_tape(g), start_ms=d0, end_ms=d0 + X.DAY_MS)
    trade = next(t for t in stopped.trades if t.symbol == selected)
    assert trade.exit_reason == 'stop_gap' and trade.exit_ref < trade.stop
    assert (trade.exit_ms, selected, trade.exit_ms + X.TF_MS, 'stop_gap') in stopped.cooldowns

    # Intrabar (non-gap) stops create the identical one-full-bar lock.
    h = {s: d.copy() for s, d in fs.items()}
    i = h[selected].index[h[selected].t == d0 + X.TF_MS][0]
    h[selected].loc[i, 'o'] = victim.stop * 1.01
    h[selected].loc[i, 'l'] = victim.stop * 0.99
    h[selected].loc[i, 'h'] = max(h[selected].loc[i, 'h'], h[selected].loc[i, 'o'])
    h[selected].loc[i, 'c'] = victim.stop * 1.005
    stopped = X.run_side(h, 'long', funding=funding_tape(h), start_ms=d0, end_ms=d0 + X.DAY_MS)
    trade = next(t for t in stopped.trades if t.symbol == selected)
    assert trade.exit_reason == 'stop' and trade.exit_ref == pytest.approx(trade.stop)
    assert (trade.exit_ms, selected, trade.exit_ms + X.TF_MS, 'stop') in stopped.cooldowns


def test_base_and_1_5x_cost_rows_are_frozen_and_isolated():
    fs = frames()
    d0 = next(t for t in decision_times(fs) if t >= START + 181 * X.TF_MS)
    pair = X.run_cost_pair(fs, 'long', funding=funding_tape(fs), start_ms=d0, end_ms=d0 + X.DAY_MS)
    base, stress = pair['base'], pair['stress_1_5x']
    assert base.cost_multiplier == 1.0 and stress.cost_multiplier == 1.5
    assert [(t.symbol, t.decision_ms) for t in base.trades] == [(t.symbol, t.decision_ms) for t in stress.trades]
    assert all(s.net_r < b.net_r for b, s in zip(base.trades, stress.trades))
    assert X.CostModel(1.5).fee == pytest.approx(1.5 * X.BASE_FEE)
    assert X.CostModel(1.5).slip == pytest.approx(1.5 * X.BASE_SLIP)
    with pytest.raises(X.XsecError, match='frozen base'):
        X.CostModel(1.25)


def test_signed_point_in_time_funding_and_missing_rows_are_incomplete():
    # Give both sides qualified selections, then compare equal positive funding rates.
    fs = frames()
    d0 = next(t for t in decision_times(fs) if t >= START + 181 * X.TF_MS)
    tape = funding_tape(fs, rate=0.001)
    long = X.run_side(fs, 'long', funding=tape, start_ms=d0, end_ms=d0 + X.DAY_MS)
    short = X.run_side(fs, 'short', funding=tape, start_ms=d0, end_ms=d0 + X.DAY_MS)
    assert long.trades and short.trades
    assert all(t.funding_r < 0 and t.net_r == pytest.approx(t.gross_r - t.fee_r + t.funding_r)
               for t in long.trades)
    assert all(t.funding_r > 0 and t.net_r == pytest.approx(t.gross_r - t.fee_r + t.funding_r)
               for t in short.trades)

    negative = funding_tape(fs, rate=-0.001)
    paid_short = X.run_side(fs, 'short', funding=negative, start_ms=d0, end_ms=d0 + X.DAY_MS)
    received_long = X.run_side(fs, 'long', funding=negative, start_ms=d0, end_ms=d0 + X.DAY_MS)
    assert all(t.funding_r < 0 for t in paid_short.trades)
    assert all(t.funding_r > 0 for t in received_long.trades)

    victim = long.trades[0].symbol
    crossed = tape.crossed(victim, d0, d0 + X.DAY_MS)
    missing = funding_tape(fs, rate=0.001, missing={(victim, crossed[0])})
    bad = X.run_side(fs, 'long', funding=missing, start_ms=d0, end_ms=d0 + X.DAY_MS)
    assert victim not in {t.symbol for t in bad.trades}
    inc = next(e for e in bad.incomplete if e.symbol == victim)
    assert inc.reason == f'missing funding settlement {crossed[0]}' and inc.exit_ms is not None
    assert any(x[1] == victim for x in bad.cooldowns)  # close still happened; only accounting is incomplete

    late = funding_tape(fs, rate=0.001, late={(victim, crossed[0])})
    noncausal = X.run_side(fs, 'long', funding=late, start_ms=d0, end_ms=d0 + X.DAY_MS)
    assert next(e for e in noncausal.incomplete if e.symbol == victim).reason.startswith('non-causal funding')
    with pytest.raises(X.XsecError, match='funding input'):
        X.run_side(fs, 'long', start_ms=d0, end_ms=d0 + X.DAY_MS)


def test_research_module_has_no_runtime_registry_or_funding_oi_candidate():
    source = open(X.__file__, encoding='utf-8').read()
    assert 'strategies.STRATEGIES' not in source
    assert 'funding_extreme_reclaim' not in source
    assert not hasattr(X, 'main')
