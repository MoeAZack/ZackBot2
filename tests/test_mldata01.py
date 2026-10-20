"""MLDATA-01: causal funding / rank / breadth / corr_count for the ML-shadow decision rows (observe only)."""
import numpy as np
import pandas as pd
import pytest

from ml_shadow import context as C
from ml_shadow.features import features_at

T0 = pd.Timestamp('2026-01-01T00:00:00Z')
H = 3600


def frame(closes, start=T0, step_s=H):
    t = [start + pd.Timedelta(seconds=step_s * k) for k in range(len(closes))]
    c = np.asarray(closes, float)
    return pd.DataFrame(dict(t=t, o=c, h=c * 1.001, l=c * 0.999, c=c, v=np.ones(len(c))))


def trend(n, g, base=100.0, wiggle=0.0, seed=0):
    rng = np.random.default_rng(seed)
    return base * np.exp(np.cumsum(np.full(n, g) + wiggle * rng.standard_normal(n)))


# ------------------------------------------------------------------ funding
def test_funding_is_the_last_settled_rate_at_or_before_the_close():
    close_ms = int((T0 + pd.Timedelta(hours=1)).value // 1_000_000)
    rows = [(close_ms - 8 * H * 1000, '0.0001', None), (close_ms, '0.0003', None), (close_ms + 1, '0.9', None)]
    assert C.funding_at(rows, close_ms) == 0.0003                      # the later row is never seen
    assert C.funding_at(rows[2:], close_ms) is None and C.funding_at(None, close_ms) is None
    assert C.funding_at([(close_ms, None, None)], close_ms) is None    # a missing rate is None, never 0
    assert C.funding_at([(close_ms, 'nan', None)], close_ms) is None


# ------------------------------------------------------------------ rank / breadth
def test_rank_and_breadth_on_a_known_cross_section():
    fr = {'AAA': frame(trend(80, 0.004)), 'BBB': frame(trend(80, 0.001)), 'CCC': frame(trend(80, -0.003))}
    t = fr['AAA'].t.iloc[-1]
    ctx = C.decision_context('BBB', t, H, fr)
    assert ctx['rank'] == 2 and C.decision_context('AAA', t, H, fr)['rank'] == 1
    assert ctx['breadth'] == pytest.approx(2 / 3)                      # AAA and BBB above their EMA50, CCC below


def test_unranked_symbol_is_none_never_a_sentinel():
    fr = {'AAA': frame(trend(80, 0.004)), 'BBB': frame(trend(80, 0.001)), 'NEW': frame(trend(10, 0.01))}
    ctx = C.decision_context('NEW', fr['AAA'].t.iloc[-1], H, fr)
    assert ctx['rank'] is None and ctx['rank'] != 999
    assert C.decision_context('AAA', fr['AAA'].t.iloc[-1], H, {'AAA': fr['AAA']})['rank'] is None   # alone: not ranked
    assert C.decision_context('AAA', fr['AAA'].t.iloc[-1], H, {'AAA': fr['AAA']})['breadth'] is None


# ------------------------------------------------------------------ corr_count
def test_corr_count():
    base = trend(80, 0.001, wiggle=0.01, seed=1)
    twin = base * 1.5
    anti = 1e4 / base
    fr = {'AAA': frame(base), 'TWN': frame(twin), 'ANT': frame(anti), 'NEW': frame(trend(10, 0.0))}
    t = fr['AAA'].t.iloc[-1]
    assert C.decision_context('AAA', t, H, fr, others=[])['corr_count'] == 0
    assert C.decision_context('AAA', t, H, fr, others=['AAA'])['corr_count'] == 0          # its own lot is not "other"
    assert C.decision_context('AAA', t, H, fr, others=['TWN', 'ANT'])['corr_count'] == 1
    assert C.decision_context('AAA', t, H, fr, others=['TWN', 'NEW'])['corr_count'] is None  # unmeasured, never 0
    assert C.decision_context('AAA', t, H, fr, others=['TWN', 'GONE'])['corr_count'] is None


# ------------------------------------------------------------------ causality
def test_future_rows_never_change_the_context():
    fr = {'AAA': frame(trend(80, 0.004, wiggle=0.01, seed=2)), 'BBB': frame(trend(80, 0.001, wiggle=0.01, seed=3)),
          'CCC': frame(trend(80, -0.003, wiggle=0.01, seed=4))}
    t = fr['AAA'].t.iloc[59]
    close_ms = int(t.value // 1_000_000) + H * 1000
    rows = [(close_ms - 1000, '0.0002', None)]
    before = C.decision_context('BBB', t, H, fr, rows, others=['AAA'])
    poisoned = {s: df.assign(c=np.where(df.t > t, df.c * 50, df.c)) for s, df in fr.items()}
    after = C.decision_context('BBB', t, H, poisoned, rows + [(close_ms + 1, '0.5', None)], others=['AAA'])
    assert before == after and None not in before.values()


def test_features_at_takes_the_context_and_missing_mask_is_explicit():
    fr = {'AAA': frame(trend(80, 0.004)), 'BBB': frame(trend(80, 0.001))}
    df = fr['AAA']; i = len(df) - 1
    ctx = C.decision_context('AAA', df.t.iloc[i], H, fr, None, others=[])
    f = features_at(df, i, ctx)
    assert f['features']['rank'] == 1 and f['features']['breadth'] == 1.0 and f['features']['corr_count'] == 0
    assert f['features']['funding'] is None and 'funding' in f['missing']


def test_bad_inputs_never_raise():
    assert C.decision_context('X', 'not a time', H, None) == dict(funding=None, rank=None, breadth=None, corr_count=None)
    assert C.decision_context('X', T0, H, {'X': pd.DataFrame({'a': [1]})})['rank'] is None


# ------------------------------------------------------------------ engine wiring
def test_engine_context_and_coverage_counters(monkeypatch):
    import test_safety as TS
    e, _ = TS.mk_engine()
    fr = {'BTCUSDT': frame(trend(80, 0.002)), 'ETHUSDT': frame(trend(80, 0.001))}
    e.state['lots']['X'] = dict(symbol='BTCUSDT', side='LONG', qty=1.0, avg=100.0)
    sl = dict(id='A', tf='1h')
    ctx = e._ml_context(sl, 'ETHUSDT', dict(time=fr['ETHUSDT'].t.iloc[-1]), fr)
    assert ctx['rank'] == 2 and ctx['breadth'] == 1.0 and ctx['corr_count'] in (0, 1) and ctx['funding'] is None
    e._ml_coverage({'features': {'rank': 2, 'funding': None}})
    e._ml_coverage({'features': {'rank': None, 'funding': None}})
    assert e.health['ml_shadow']['coverage'] == {'rank': [1, 2], 'funding': [0, 2]}


def test_trading_code_never_reads_the_context():
    import os
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'engine.py'), encoding='utf-8').read()
    assert src.count('self._ml_context(') == 1 and src.count('self._ml_coverage(') == 1   # only inside _ml_decision


def test_time_resolution_and_int_ms_frames_slice_identically():
    """Regression: a microsecond-resolution t column must not let rows after T through (seen while building MLDATA-01)."""
    fr = {'AAA': frame(trend(80, 0.004, wiggle=0.01, seed=5)), 'BBB': frame(trend(80, 0.001, wiggle=0.01, seed=6))}
    t = fr['AAA'].t.iloc[59]
    want = C.decision_context('BBB', t, H, {s: d.iloc[:60] for s, d in fr.items()}, others=['AAA'])
    for conv in (lambda c: c.astype('datetime64[us, UTC]'), lambda c: c.astype('datetime64[ns, UTC]'),
                 lambda c: c.astype('datetime64[ms, UTC]'), lambda c: pd.Series(c.astype('datetime64[ms, UTC]').astype('int64'))):
        poisoned = {s: d.assign(c=np.where(d.t > t, d.c * 50, d.c)) for s, d in fr.items()}   # rows after T must not count
        got = C.decision_context('BBB', t, H, {s: d.assign(t=conv(d.t)) for s, d in poisoned.items()}, others=['AAA'])
        assert got == want


@pytest.mark.parametrize('bad', [np.nan, np.inf])
@pytest.mark.parametrize('where', ['last', 'mid'])
def test_breadth_never_counts_a_non_finite_candle_as_below(bad, where):
    """Cowork 6295235148: a NaN/inf close (last bar, or one that poisons the EMA) is unmeasured, never 'not above'."""
    up = trend(80, 0.01)
    c = up.copy(); c[-1 if where == 'last' else 40] = bad
    closes = {'A': up, 'B': up * 1.1, 'C': c}
    assert C.breadth_at(closes) == 1.0
    assert C.breadth_at({'A': up, 'C': c}) is None                     # one measurable symbol is below MIN_SYMBOLS
