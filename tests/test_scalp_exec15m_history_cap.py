"""#206 full RANGE cell EXIT 1 (Codex 6391959028, Build root cause 6391998856): Engine._finish keeps only the last
3,000 history rows, so a cell closing more trades than that compared every fill-log trade against a truncated history.

Pins: the harness returns every history row the engine wrote (same objects, close order) while the engine keeps its
own capped list; fills and decisions are unchanged; the wrapper is always removed; a history that is not the tail of
the collected rows refuses; the engine's cap is still the one this fix was written against.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'tools', 'research')]

import costs as C                                         # noqa: E402
import engine as E                                        # noqa: E402
import scalp_exec_15m as X                                # noqa: E402

SYM = 'SOLUSDT'
T0 = 260
CAP = 3


def _minutes(n15, seed, start='2025-01-06'):
    rng = np.random.default_rng(seed)
    n = n15 * 15
    t = pd.date_range(start, periods=n, freq='1min')
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.0012, n)))
    o = np.r_[100.0, c[:-1]]
    w = np.abs(rng.normal(0, 0.0006, n)) * c
    h, l = np.maximum(o, c) + w, np.minimum(o, c) - w
    return pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=rng.uniform(50, 150, n)))


def _agg(m):
    g = m.groupby(m.t.dt.floor('15min'))
    return pd.DataFrame(dict(t=g.t.first().dt.floor('15min').values, o=g.o.first().values, h=g.h.max().values,
                             l=g.l.min().values, c=g.c.last().values, v=g.v.sum().values))


def _sleeve():
    return [E.sleeve('SC', 'exp_scalp', 1.0, 0.01, 2, [SYM], sides='both', mgmt={}, tf='15m')]


def _cal():
    import json
    with open(os.path.join(ROOT, 'research_evidence', 'prereg', 'trend_ema_mom.v1.json'), encoding='utf-8') as fh:
        return json.load(fh)['costs']['slip_cal_v1']


@pytest.fixture(scope='module')
def data():
    m = _minutes(420, 11)
    return {SYM: _agg(m)}, {SYM: m}


@pytest.fixture
def small_cap(monkeypatch):
    """The engine's own cap, lowered to CAP rows (the real one is 3,000: too many trades for a unit test)."""
    real = E.Engine._finish

    def capped(self, key, why):
        r = real(self, key, why)
        self.history = self.history[-CAP:]
        return r
    monkeypatch.setattr(E.Engine, '_finish', capped)
    return capped


def _run(data, **k):
    raw, mins = data
    return X._engine(raw, mins, _sleeve(), T0, start=2000.0, rules=None, settings=None, latency_s=0, **k)


def test_every_history_row_is_returned_when_the_engine_caps_its_own(data, small_cap):
    out, fills, _ = _run(data)
    n = len(X.trades_of(fills, out['history']))           # the count check passes: one row per fill-log trade
    assert n > CAP and len(out['history']) == n
    assert E.Engine._finish is small_cap                   # the harness wrapper was removed again


def test_capped_and_uncapped_runs_are_identical(data, monkeypatch):
    out0, f0, l0 = _run(data)
    real = E.Engine._finish

    def capped(self, key, why):
        r = real(self, key, why)
        self.history = self.history[-CAP:]
        return r
    monkeypatch.setattr(E.Engine, '_finish', capped)
    out1, f1, l1 = _run(data)
    assert f1 == f0 and l1 == l0                            # no decision or fill depends on the harness copy
    assert [h['id'] for h in out1['history']] == [h['id'] for h in out0['history']]
    assert len(out0['history']) > CAP


def test_run_cell_completes_past_the_cap(data, small_cap):
    raw, mins = data
    fund = {SYM: [(int(t.value // 10 ** 6), 0.0001, 100.0) for t in raw[SYM].t[::32]]}
    pk = X.run_cell(raw, mins, _sleeve(), T0, model=C.CostModel(_cal(), {SYM: 'crypto'}), funding=fund, start=2000.0)
    assert pk['status'] == 'COMPLETE' and len(pk['repriced']['trades']) > CAP
    for name in X.ACCOUNT_ROWS:
        assert len(pk['account'][name]['trades']) > CAP


def test_wrapper_is_removed_when_the_replay_raises(data, monkeypatch):
    import replay as RP
    real = E.Engine._finish

    def boom(*a, **k):
        raise RuntimeError('replay failed')
    monkeypatch.setattr(RP, 'run_replay', boom)
    with pytest.raises(RuntimeError):
        _run(data)
    assert E.Engine._finish is real


def test_a_history_that_is_not_the_collected_tail_refuses(data, monkeypatch):
    real = E.Engine._finish

    def copies(self, key, why):                             # the engine's list no longer holds the written rows
        r = real(self, key, why)
        self.history = [dict(h) for h in self.history[-CAP:]]
        return r
    monkeypatch.setattr(E.Engine, '_finish', copies)
    with pytest.raises(X.ScalpExecError, match='does not end with'):
        _run(data)
    assert E.Engine._finish is copies                       # restored although the post-replay guard raised


def test_rows_captured_but_an_empty_engine_history_refuses(data, monkeypatch):
    import replay as RP
    real_run, real = RP.run_replay, E.Engine._finish

    def empty(*a, **k):                                     # the replay writes rows but reports no history
        out = real_run(*a, **k)
        return dict(out, history=[])
    monkeypatch.setattr(RP, 'run_replay', empty)
    with pytest.raises(X.ScalpExecError, match='does not end with'):
        _run(data)
    assert E.Engine._finish is real


def test_engine_cap_is_the_one_this_fix_was_written_for():
    with open(os.path.join(ROOT, 'engine.py'), encoding='utf-8') as fh:
        src = fh.read()
    assert src.count('self.history = self.history[-3000:]') == 1


def test_a_finish_that_writes_no_row_collects_nothing(data, monkeypatch):
    real = E.Engine._finish

    def twice(self, key, why):                              # a repeated terminal call: the lot is gone, no new row
        r = real(self, key, why)
        if key != 'MISSING':
            E.Engine._finish(self, 'MISSING', why)          # goes through the harness wrapper
        return r
    monkeypatch.setattr(E.Engine, '_finish', twice)
    out, fills, _ = _run(data)
    ids = [h['id'] for h in out['history']]
    assert len(ids) == len(set(ids)) == len(X.trades_of(fills, out['history']))


def test_rows_keep_their_fill_time_cost_updates_past_the_cap(data, monkeypatch):
    import json
    raw, mins = data
    fund = {SYM: [(int(t.value // 10 ** 6), 0.0001, 100.0) for t in raw[SYM].t[::32]]}
    model = C.CostModel(_cal(), {SYM: 'crypto'})

    def run():
        pr = X.Pricer(C.STRESS['base'], model, raw, funding=fund)
        return _run(data, pricer=pr)
    out0, f0, _ = run()
    real = E.Engine._finish

    def capped(self, key, why):
        r = real(self, key, why)
        self.history = self.history[-CAP:]
        return r
    monkeypatch.setattr(E.Engine, '_finish', capped)
    out1, f1, _ = run()
    dump = lambda h: json.dumps(h, sort_keys=True, default=str)
    assert f1 == f0 and len(out1['history']) > CAP
    assert [dump(h) for h in out1['history']] == [dump(h) for h in out0['history']]   # every field, every row, in order
    assert all(h.get('fees') for h in out1['history'][:-CAP])                          # fill-time costs on evicted rows


def test_a_later_in_place_update_reaches_rows_the_engine_already_evicted(data, monkeypatch):
    real = E.Engine._finish

    def deferred(self, key, why):                           # like apply_costs: fold into the PREVIOUS row afterwards
        prev = self.history[-1] if self.history else None
        r = real(self, key, why)
        if prev is not None:
            prev['late_cost'] = prev['id']
        self.history = self.history[-CAP:]
        return r
    monkeypatch.setattr(E.Engine, '_finish', deferred)
    out, _, _ = _run(data)
    rows = out['history']
    assert len(rows) > CAP and all(h.get('late_cost') == h['id'] for h in rows[:-1])
