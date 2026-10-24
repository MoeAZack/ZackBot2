"""#187 P3 hardening (Cowork 6381111570, Codex 6381140527 / 6381403495): pins the shared step-aware flatness boundary
that the accepted fix 62292db introduced, so each of the eight surviving mutants fails a test, plus the pre-trades_of
fill persistence that keeps a reconstruction refusal from losing the replay."""
import gzip
import inspect
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'tools', 'research'), ROOT, os.path.join(ROOT, 'tests')]

import costs as C                                         # noqa: E402
import replay as RP                                       # noqa: E402
import scalp_exec_15m as X                                # noqa: E402
from test_scalp_exec15m_adds import SYM, f, row           # noqa: E402

STEP = 0.1
RULES = {SYM: {'step': STEP}}


def _lot(close_qty, entered=10.0):
    fl = [f(20, 'open', entered, 100.0), f(21, 'close', close_qty, 101.0)]
    return fl


# (a) the boundary is 0.99 x the symbol's own step: just below is dust, just above is real quantity
def test_flat_tolerance_is_ninety_nine_percent_of_the_symbols_step():
    assert RP.qty_flat_tolerance(SYM, RULES) == pytest.approx(0.99 * STEP, rel=1e-15)
    assert RP.qty_flat_tolerance(SYM, None) == pytest.approx(0.99 * 1e-5, rel=1e-15)   # default step
    assert RP.qty_flat_tolerance(SYM, {SYM: {'step': 1e-15}}) == 1e-12                # floor


@pytest.mark.parametrize('residual,flat', [(0.98 * STEP, True), (0.995 * STEP, False), (1.0 * STEP, False),
                                           (1.2 * STEP, False)])
def test_a_residual_is_flat_only_strictly_below_the_boundary(residual, flat):
    fl = _lot(10.0 - residual)
    if flat:
        ts = X.trades_of(fl, [row(fl)], RULES)
        assert len(ts) == 1
    else:                                       # still open: the history row has no matching closed trade
        with pytest.raises(X.ScalpExecError, match='disagree on the number of closed trades'):
            X.trades_of(fl, [row(fl)], RULES)


# (b) the replay's own reduce() and positions() use the same shared boundary as trades_of. Both are closures inside
# run_replay, so this is a source pin; the behaviour is covered by the replay suites.
def test_replay_reduce_and_positions_use_the_shared_boundary():
    src = inspect.getsource(RP.run_replay)
    red = src[src.index('def reduce('):src.index('def equity(')]
    pos = src[src.index('def positions(self)'):src.index('def hedge_mode(')]
    assert 'qty_flat_tolerance(s, XR)' in red and "if l['q'] <= tol: W.lots.remove(l)" in red
    assert 'qty_flat_tolerance(k[0], XR)' in pos and '1e-12' not in pos


# (c) a non-default step reaches trades_of from run_cell's reference replay and from every account replay
def test_run_cell_passes_its_exchange_rules_to_every_reconstruction(monkeypatch):
    import test_scalp_exec15m as T
    seen, real = [], X.trades_of

    def spy(fills, history, exchange_rules=None):
        seen.append(exchange_rules)
        return real(fills, history, exchange_rules)
    monkeypatch.setattr(X, 'trades_of', spy)
    m = T._minutes(420, 11)
    raw = {T.SYM: T._agg(m)}
    rules = {T.SYM: dict(step=0.001, min_qty=0.001, tick=0.0001, min_notional=5)}
    X.run_cell(raw, {T.SYM: m}, T._sleeve(), T.T0, model=C.CostModel(T._cal(), {T.SYM: 'crypto'}),
               exchange_rules=rules, start=2000.0)
    assert len(seen) == 1 + len(X.ACCOUNT_ROWS) and all(r == rules for r in seen)


# (d) a folded dust remainder makes the closes add up to exactly what was entered
def test_closes_sum_to_the_entered_quantity_after_a_dust_fold():
    fl = _lot(10.0 - 0.5 * STEP)
    (t,) = X.trades_of(fl, [row(fl)], RULES)
    assert sum(x['qty'] for x in t['closes']) == pytest.approx(10.0, abs=1e-12)
    assert t['seq'][-1] == (False, pytest.approx(10.0, abs=1e-12))


# (e) an exact close of the next lot discards the old carry, so a stale carry cannot absorb a later over-close
def test_a_stale_carry_cannot_absorb_a_later_lot():
    a = [f(20, 'open', 10.0, 100.0), f(21, 'close', 10.0 - 0.5 * STEP, 101.0)]          # carry 0.05
    b = [f(22, 'open', 1.0, 100.0), f(23, 'close', 1.0, 101.0)]                          # exact: carry dropped
    c = [f(24, 'open', 1.0, 100.0), f(25, 'close', 1.0 + 0.5 * STEP, 101.0)]             # would need the old carry
    with pytest.raises(X.ScalpExecError, match='exceed'):
        X.trades_of(a + b + c, [row(a), row(b), row(c)], RULES)
    assert len(X.trades_of(a + c, [row(a), row(c)], RULES)) == 2                       # the carry itself is legal


# fill persistence: the raw replays are saved BEFORE reconstruction, so a refusal keeps them
def test_fills_are_saved_before_a_reconstruction_refusal(monkeypatch, tmp_path):
    import test_scalp_exec15m as T

    def refuse(*a, **k):
        raise X.ScalpExecError('the closes exceed the quantity entered on the lot')
    monkeypatch.setattr(X, 'trades_of', refuse)
    m = T._minutes(420, 11)
    d = str(tmp_path / 'cell.json.fills')
    with pytest.raises(X.ScalpExecError, match='exceed'):
        X.run_cell({T.SYM: T._agg(m)}, {T.SYM: m}, T._sleeve(), T.T0,
                   model=C.CostModel(T._cal(), {T.SYM: 'crypto'}), start=2000.0, on_fills=X.fill_saver(d))
    assert os.listdir(d) == ['reference.json.gz']
    with gzip.open(os.path.join(d, 'reference.json.gz'), 'rt', encoding='utf-8') as fh:
        doc = json.load(fh)
    assert doc['replay'] == 'reference' and doc['fills'] and isinstance(doc['history'], list)
    assert {x['act'] for x in doc['fills']} <= {'open', 'close', 'stop_set'}


def test_fill_saver_never_overwrites(tmp_path):
    d = str(tmp_path / 'x.fills')
    save = X.fill_saver(d)
    save('base', [], [])
    with pytest.raises(FileExistsError):
        save('base', [], [])
    with pytest.raises(FileExistsError):
        X.fill_saver(d)
