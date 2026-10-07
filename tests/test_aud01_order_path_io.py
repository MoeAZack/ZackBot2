"""AUD-01 (historical audit C01 / ENG-B01, P0): a failing journal write (trades.csv locked by Excel, AV, a full disk...) must
never break the bookkeeping of an order Binance already executed. Before the fix the exception skipped tp1/adds/dca
bookkeeping, so the same partial close or add fired again every pass (repro: 5 buys for a 1-add plan, stop covering 1)."""
import builtins, os
import pytest
import test_safety as TS
from test_safety import mk_engine, SL, SG

E = TS.E


@pytest.fixture
def locked_journal(monkeypatch):
    """trades.csv cannot be opened (PermissionError), every other file works."""
    real = builtins.open
    state = dict(on=True)
    def fake(path, *a, **k):
        if state['on'] and str(path).endswith('trades.csv'): raise PermissionError(13, 'locked by another program', str(path))
        return real(path, *a, **k)
    monkeypatch.setattr(builtins, 'open', fake)
    return state


def _passes(e, n=4):
    for _ in range(n): e.manage(e.trade.marks())


def test_tp1_fires_exactly_once_while_the_journal_is_locked(locked_journal):
    e, _ = mk_engine(); sl = dict(SL, mgmt={'tp1_r': 1.0, 'tp1_frac': 0.5}); e.S['SLEEVES'] = [sl]
    locked_journal['on'] = False
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity())
    locked_journal['on'] = True
    lot = next(iter(e.state['lots'].values())); q0 = lot['qty']
    e.trade.mark['BTCUSDT'] = 120.0
    _passes(e)
    assert e.trade.calls.count('close') == 1, e.trade.calls
    assert abs(e.trade.pos[('BTCUSDT', 'LONG')] - q0 / 2) < 0.002 and lot['tp1'] is True
    stop_q = [v[2] for v in e.trade.stops.values() if v[0] == 'BTCUSDT']
    assert stop_q and abs(stop_q[-1] - lot['qty']) < 0.002, 'the exchange stop covers exactly what is left'
    assert any('journal' in (i.get('msg') or i.get('text') or str(i)).lower() for i in (e.health.get('incidents') or {}).values())


def test_pyramid_add_fires_exactly_once_and_the_stop_covers_the_position(locked_journal):
    e, _ = mk_engine(); sl = dict(SL, mgmt={'pyramid': {'n': 1, 'step_r': 1.0, 'frac': 0.5}}); e.S['SLEEVES'] = [sl]
    locked_journal['on'] = False
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity())
    locked_journal['on'] = True
    lot = next(iter(e.state['lots'].values())); q0 = lot['qty']
    e.trade.mark['BTCUSDT'] = 106.0
    _passes(e)
    assert e.trade.calls.count('open') == 2, e.trade.calls             # the entry + exactly one add
    assert lot['adds'] == 1 and abs(e.trade.pos[('BTCUSDT', 'LONG')] - q0 * 1.5) < 0.002
    stop_q = [v[2] for v in e.trade.stops.values() if v[0] == 'BTCUSDT']
    assert stop_q and abs(stop_q[-1] - lot['qty']) < 0.002


def test_entry_and_stop_out_are_kept_and_journal_rows_are_flushed_later(locked_journal, tmp_path):
    e, tmp = mk_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())       # journal locked: the entry still counts
    assert len(e.state['lots']) == 1 and e._journal_backlog
    k = next(iter(e.state['lots'])); lot = e.state['lots'][k]
    e.trade.stops.pop(lot['stop_id'])                                     # the stop fired on Binance
    e.trade.pos[('BTCUSDT', 'LONG')] = 0.0
    e.reconcile(e.equity())                                               # journal still locked: reconcile must not raise
    assert k not in e.state['lots'] and e.history and e.history[-1]['exit_reason'] == 'stop'
    locked_journal['on'] = False
    e.log_trade(time='t', event='note')                                   # next good write flushes the backlog first
    rows = open(e.F['trades'], encoding='utf-8').read().splitlines()
    assert [r.split(',')[1] for r in rows[1:]] == ['entry', 'stop', 'note'] and not e._journal_backlog


def test_other_lots_are_still_managed_while_the_journal_is_locked(locked_journal):
    e, _ = mk_engine(); sl = dict(SL, mgmt={'tp1_r': 1.0, 'tp1_frac': 0.5}); e.S['SLEEVES'] = [sl]
    locked_journal['on'] = False
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert e.open_lot(sl, 'ETHUSDT', 'LONG', dict(SG, close=50.0, atr=1.0), None, e.equity())
    locked_journal['on'] = True
    e.trade.mark['BTCUSDT'] = 120.0; e.trade.mark['ETHUSDT'] = 60.0
    _passes(e, 2)
    assert all(l['tp1'] for l in e.state['lots'].values())
    assert e.trade.calls.count('close') == 2


def test_a_failure_after_the_exchange_executed_marks_the_event_done(monkeypatch):
    """Second layer: whatever fails after Binance executed a partial close / add, the order's own bookkeeping (its `post`)
    is applied and the stop is flagged for re-placement, so the event can never fire twice."""
    e, _ = mk_engine(); sl = dict(SL, mgmt={'tp1_r': 1.0, 'tp1_frac': 0.5}); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity())
    lot = next(iter(e.state['lots'].values()))
    boom = dict(n=0)
    def explode(*a, **k):
        boom['n'] += 1
        if boom['n'] == 1: raise RuntimeError('unexpected local failure after the fill')
    monkeypatch.setattr(e, '_audit_fill', explode)
    e.trade.mark['BTCUSDT'] = 120.0
    _passes(e)
    assert e.trade.calls.count('close') == 1 and lot['tp1'] is True
    stop_q = [v[2] for v in e.trade.stops.values() if v[0] == 'BTCUSDT']
    assert stop_q and abs(stop_q[-1] - lot['qty']) < 0.002


def test_journal_backlog_is_bounded():
    e, _ = mk_engine()
    e._journal_backlog = [dict(event='x')] * E.JOURNAL_BACKLOG_MAX
    real = builtins.open
    def fake(path, *a, **k):
        if str(path).endswith('trades.csv'): raise PermissionError(13, 'locked', str(path))
        return real(path, *a, **k)
    builtins.open = fake
    try:
        for _ in range(10): e.log_trade(time='t', event='y')
    finally:
        builtins.open = real
    assert len(e._journal_backlog) == E.JOURNAL_BACKLOG_MAX and e.health.get('journal_dropped', 0) == 10


def test_a_full_close_followed_by_a_local_failure_finishes_the_lot_once(monkeypatch):
    """Codex AUD-01 review P1: Binance closed the whole position, then a local hook raised. The lot must be finished with
    the intended reason exactly once (history written, lot gone) - no zero-quantity ghost lot, no second close order."""
    e, _ = mk_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    k = next(iter(e.state['lots']))
    def explode(*a, **kw): raise RuntimeError('unexpected local failure after the close')
    monkeypatch.setattr(e, '_audit_fill', explode)
    with pytest.raises(RuntimeError):
        e.close_lot(k, 'exit_signal')
    assert k not in e.state['lots'], 'no ghost lot'
    assert e.history and e.history[-1]['exit_reason'] == 'exit_signal' and e.history[-1]['id'] == k
    assert e.trade.calls.count('close') == 1 and abs(e.trade.pos.get(('BTCUSDT', 'LONG'), 0.0)) < 1e-12
    monkeypatch.undo()
    _passes(e); e.reconcile(e.equity())
    assert e.trade.calls.count('close') == 1 and len(e.history) == 1, 'nothing repeats on later passes'


def test_an_add_followed_by_a_local_failure_is_marked_done_and_the_stop_covers_it(monkeypatch):
    """The add half of the AUD-01 contract: Binance filled the pyramid add, then a local hook raised. The add is counted
    once (adds = 1, no second buy) and the stop is re-placed for the full position."""
    e, _ = mk_engine(); sl = dict(SL, mgmt={'pyramid': {'n': 1, 'step_r': 1.0, 'frac': 0.5}}); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity())
    lot = next(iter(e.state['lots'].values())); q0 = lot['qty']
    boom = dict(n=0)
    def explode(*a, **kw):
        boom['n'] += 1
        if boom['n'] == 1: raise RuntimeError('unexpected local failure after the add')
    monkeypatch.setattr(e, '_audit_fill', explode)
    e.trade.mark['BTCUSDT'] = 106.0
    _passes(e)
    assert e.trade.calls.count('open') == 2, e.trade.calls                 # the entry + exactly one add
    assert lot['adds'] == 1 and abs(e.trade.pos[('BTCUSDT', 'LONG')] - q0 * 1.5) < 0.002
    stop_q = [v[2] for v in e.trade.stops.values() if v[0] == 'BTCUSDT']
    assert stop_q and abs(stop_q[-1] - lot['qty']) < 0.002 and not lot.get('stop_dirty')
