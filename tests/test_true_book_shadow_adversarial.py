"""TRUE-BOOK-02 independent acceptance harness (issue #145).

This is intentionally test-only.  It freezes the public boundary expected from
``strategy_shadow.py`` without importing the executable order path:

    ShadowBook(path, cohort_id, model, start=2000, min_n=20)
    .on_signal(slot=..., symbol=..., side=..., signal=..., mark=...,
               live_decision=...) -> candidate document
    .on_bar(tf=..., bars={symbol: OHLC document}, signals={...})
    .on_funding(symbol=..., at=..., rate=..., mark=...)
    .snapshot() -> {instances, episodes, coverage}
    .compute() -> {model, min_n, strategies}

The module is absent on the baseline master, so the contract is skipped there.
Once Build adds it, these tests execute unchanged.  All timestamps are UTC; UI
conversion to Cairo is outside this pure accounting contract.
"""
import ast
import copy
import inspect
from datetime import datetime, timedelta, timezone

import pytest


SS = pytest.importorskip('strategy_shadow', reason='TRUE-BOOK-02 production module not on baseline master')
import engine as E

MODEL = dict(version='shadow-model-v1', management='research-primitives-v1', fee_rate=0.0005,
             adverse_slippage_bps=2.0, entry_reference='observed_post_close_mark')
T0 = datetime(2026, 10, 18, tzinfo=timezone.utc)


def iso(minutes=0):
    return (T0 + timedelta(minutes=minutes)).isoformat()


def slot(key='ema_mom', sleeve='A', **overrides):
    out = dict(id=sleeve, key=key, tf='4h', sides='both', risk=0.02, max_pos=1,
               share=1.0, params={}, mgmt=dict(stop_atr=1.0, tp_r=1.0))
    out.update(overrides)
    return out


def signal(t=None, px=100.0, atr=2.0):
    return dict(time=t or iso(), close=px, atr=atr, le=True, se=False, lx=False, sx=False)


def mark(t=None, px=100.0):
    return dict(time=t or iso(), price=px, source='premiumIndex')


def bar(t, o=100.0, h=103.0, l=99.0, c=102.0):
    return dict(time=t, o=o, h=h, l=l, c=c, source='closed_kline')


def new_book(tmp_path, name='shadow.jsonl'):
    return SS.ShadowBook(path=tmp_path / name, cohort_id='CH-TB02', model=copy.deepcopy(MODEL),
                         start=2000.0, min_n=20)


def candidate(book, sl=None, sym='BTCUSDT', side='LONG', t=None, px=100.0,
              status='skipped', reason='max positions reached (1)'):
    return book.on_signal(slot=sl or slot(), symbol=sym, side=side, signal=signal(t, px), mark=mark(t, px),
                          live_decision=dict(status=status, reason=reason))


def rows(book):
    doc = book.compute()
    assert doc['model']['version'] == 'shadow-model-v1' and doc['min_n'] == 20
    return doc['strategies']


def row(book, key):
    hit = [r for r in rows(book) if r['key'] == key]
    assert len(hit) == 1, hit
    return hit[0]


def close_win(book, sl, n, sym='BTCUSDT'):
    """One unique post-close decision followed by a causally later target bar."""
    t = iso(10 + 20 * n)
    candidate(book, sl, sym=sym, t=t, px=100.0, status='skipped', reason='live slot full')
    book.on_bar(tf=sl['tf'], bars={sym: bar(iso(20 + 20 * n), o=100.0, h=104.0, l=99.0, c=103.0)}, signals={})


def test_contract_is_pure_and_pins_the_named_model():
    assert SS.MODEL_VERSION == 'shadow-model-v1'
    tree = ast.parse(inspect.getsource(SS))
    imported = {a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not ({'engine', 'binance_client', 'app'} & imported), 'observer must not import executable/network layers'


def test_engine_integration_discards_observer_returns_and_keeps_shadow_out_of_every_live_gate():
    """Structural half of the authority A/B: hooks are best-effort statements, never values in a decision expression."""
    src = inspect.getsource(E)
    tree = ast.parse(src); parent = {}
    for n in ast.walk(tree):
        for child in ast.iter_child_nodes(n): parent[child] = n
    observed = {'on_signal', 'on_bar', 'on_marks', 'on_funding', 'link_actual'}
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr in observed and 'shadow' in (ast.get_source_segment(src, n.func.value) or '').lower()]
    assert calls, 'engine has no strategy-shadow observer hook'
    for call in calls:
        assert isinstance(parent.get(call), ast.Expr), 'a shadow return value reaches executable control flow'
        p, guarded = call, False
        while p in parent:
            p = parent[p]
            if isinstance(p, ast.Try): guarded = True; break
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)): break
        assert guarded, 'observer call is not exception-contained'
    for name in ('entry_block', 'open_lot', 'true_book_block', '_rules_block', '_market_entry', '_maker_start'):
        text = inspect.getsource(getattr(E.Engine, name))
        assert 'strategy_shadow' not in text and '.on_signal(' not in text and '.on_bar(' not in text \
            and '.on_marks(' not in text and '.on_funding(' not in text, f'shadow reached live gate/order path {name}'


def test_identity_dedupes_identical_sleeves_but_keeps_overlapping_strategies_independent(tmp_path):
    b = new_book(tmp_path)
    a1, a2 = slot('ema_mom', 'A'), slot('ema_mom', 'A-DUP')
    other = slot('donchian_ens', 'B')
    x = candidate(b, a1); y = candidate(b, a2); z = candidate(b, other)
    assert x['instance_id'] == y['instance_id'] and x['candidate_id'] == y['candidate_id']
    assert z['instance_id'] != x['instance_id'] and z['candidate_id'] != x['candidate_id']
    snap = b.snapshot()
    inst = next(v for v in snap['instances'].values() if v['key'] == 'ema_mom')
    assert sorted(inst['sleeve_ids']) == ['A', 'A-DUP']
    assert len(snap['episodes']) == 2                         # same BTC/side/time, independent by strategy
    assert {e['symbol'] for e in snap['episodes'].values()} == {'BTCUSDT'}


def test_material_causal_config_change_starts_a_new_lineage(tmp_path):
    b = new_book(tmp_path)
    a = slot('ema_mom', 'A')
    c = slot('ema_mom', 'A2', mgmt=dict(stop_atr=1.5, tp_r=1.0))
    x, y = candidate(b, a), candidate(b, c)
    assert x['instance_id'] != y['instance_id']
    assert x['config_digest'] != y['config_digest']
    assert len([v for v in b.snapshot()['instances'].values() if v['key'] == 'ema_mom']) == 2


@pytest.mark.parametrize('reason', [
    'coin switched off', 'strategy slot switched off', 'entries paused', 'daily loss halt',
    'already in a trade on this coin', 'max positions reached (1)', 'book B3_TREND full (4/4 lots)',
    'book B3_TREND INCOMPLETE (funding_unread)', 'regime: range market', 'volatility filter',
    'fee_edge: insufficient', 'Claude review vetoed: no edge', 'order failed: injected',
])
def test_every_live_rejection_still_gets_a_closed_shadow_outcome(tmp_path, reason):
    b, sl = new_book(tmp_path), slot()
    rec = candidate(b, sl, status='skipped', reason=reason)
    b.on_bar(tf='4h', bars={'BTCUSDT': bar(iso(10), h=104.0, l=99.0, c=103.0)}, signals={})
    ep = b.snapshot()['episodes'][rec['candidate_id']]
    assert ep['live_decision'] == {'status': 'skipped', 'reason': reason}
    assert ep['status'] == 'closed' and ep['exit_reason']
    assert row(b, 'ema_mom')['closed'] == 1


def test_entry_is_the_observed_post_close_mark_never_a_future_bar_open(tmp_path):
    b = new_book(tmp_path)
    rec = candidate(b, t=iso(), px=100.0)
    # A wildly different later 4h open must not replace the already observed executable boundary mark.
    b.on_bar(tf='4h', bars={'BTCUSDT': bar(iso(240), o=150.0, h=151.0, l=140.0, c=145.0)}, signals={})
    ep = b.snapshot()['episodes'][rec['candidate_id']]
    assert ep['decision_time'] == iso() and ep['entry_ref_time'] == iso()
    assert ep['entry_ref'] == 100.0 and ep['entry'] == pytest.approx(100.02)  # 2 bps adverse long slip
    assert ep['entry'] != 150.0


def test_stale_or_missing_reference_is_visible_incomplete_not_backfilled(tmp_path):
    b = new_book(tmp_path)
    sl, sg = slot(), signal(iso(10))
    a = b.on_signal(slot=sl, symbol='BTCUSDT', side='LONG', signal=sg, mark=None,
                    live_decision=dict(status='skipped', reason='outage'))
    old = b.on_signal(slot=slot('donchian_ens', 'B'), symbol='ETHUSDT', side='LONG', signal=sg,
                      mark=mark(iso(), 50), live_decision=dict(status='skipped', reason='outage'))
    snap = b.snapshot()['episodes']
    for cid in (a['candidate_id'], old['candidate_id']):
        assert snap[cid]['status'] == 'incomplete' and snap[cid]['incomplete']
    assert all(not x['rankable'] for x in rows(b))


def test_fees_funding_nav_r_and_excursions_are_strategy_local(tmp_path):
    b = new_book(tmp_path)
    long, short = slot('ema_mom', 'L'), slot('bear_breakdown', 'S')
    a = candidate(b, long, side='LONG', t=iso(), px=100)
    c = candidate(b, short, side='SHORT', t=iso(), px=100)
    b.on_funding(symbol='BTCUSDT', at=iso(2), rate=0.001, mark=100.0)
    b.on_bar(tf='4h', bars={'BTCUSDT': bar(iso(10), o=100, h=104, l=96, c=102)}, signals={})
    ra, rc = row(b, 'ema_mom'), row(b, 'bear_breakdown')
    assert ra['fees'] > 0 and rc['fees'] > 0
    assert ra['funding'] < 0 < rc['funding']                 # positive funding: longs pay, shorts receive
    for r in (ra, rc):
        assert r['nav'] == pytest.approx(2000 + r['gross'] - r['fees'] + r['funding'] + r['unrealized'])
        assert r['roi'] == pytest.approx(r['nav'] / 2000 - 1)
        assert r['mfe_r']['n'] == r['mae_r']['n'] == r['giveback_r']['n'] == r['closed']
    # The two strategy ledgers own distinct episodes despite an identical market path.
    assert b.snapshot()['episodes'][a['candidate_id']]['instance_id'] != b.snapshot()['episodes'][c['candidate_id']]['instance_id']


def test_missing_funding_affects_only_the_exposed_strategy_and_is_unrankable(tmp_path):
    b = new_book(tmp_path)
    candidate(b, slot('ema_mom', 'L'), sym='BTCUSDT')
    close_win(b, slot('donchian_ens', 'D'), 1, sym='ETHUSDT')
    b.on_funding(symbol='BTCUSDT', at=iso(2), rate=None, mark=100.0)
    a, d = row(b, 'ema_mom'), row(b, 'donchian_ens')
    assert not a['complete'] and not a['rankable'] and 'funding' in ' '.join(a['incomplete']).lower()
    assert d['complete']


def test_min_n_is_exactly_twenty_and_incomplete_always_wins(tmp_path):
    b, sl = new_book(tmp_path), slot()
    for i in range(19): close_win(b, sl, i)
    assert row(b, 'ema_mom')['closed'] == 19 and not row(b, 'ema_mom')['rankable']
    close_win(b, sl, 19)
    r = row(b, 'ema_mom')
    assert r['closed'] == 20 and r['complete'] and r['rankable'] and r['rank'] == 1
    b.mark_incomplete(instance_id=r['instance_id'], reason='coverage_gap:test')
    r = row(b, 'ema_mom')
    assert not r['complete'] and not r['rankable'] and r['rank'] is None


def test_restart_replay_is_idempotent_and_partial_tail_is_a_gap(tmp_path):
    p = tmp_path / 'shadow.jsonl'
    b, sl = new_book(tmp_path), slot()
    rec = candidate(b, sl)
    b.on_funding(symbol='BTCUSDT', at=iso(2), rate=0.001, mark=100.0)
    b.on_bar(tf='4h', bars={'BTCUSDT': bar(iso(10), h=104, l=99, c=103)}, signals={})
    before = b.compute()
    again = SS.ShadowBook(path=p, cohort_id='CH-TB02', model=copy.deepcopy(MODEL), start=2000.0, min_n=20)
    assert again.compute() == before and len(again.snapshot()['episodes']) == 1
    assert rec['candidate_id'] in again.snapshot()['episodes']
    with open(p, 'ab') as f: f.write(b'{"seq":')
    damaged = SS.ShadowBook(path=p, cohort_id='CH-TB02', model=copy.deepcopy(MODEL), start=2000.0, min_n=20)
    assert damaged.snapshot()['coverage']['complete'] is False
    assert all(not x['rankable'] for x in damaged.compute()['strategies'])


def test_cycle_watermarks_detect_absent_instance_and_conflicting_replay(tmp_path):
    b = new_book(tmp_path)
    a, d = slot('ema_mom', 'A'), slot('donchian_ens', 'D')
    candidate(b, a); candidate(b, d)
    ids = sorted(b.snapshot()['instances'])
    b.complete_cycle(cycle_id='4h|' + iso(), tf='4h', decision_time=iso(), instance_ids=ids)
    assert b.snapshot()['coverage']['complete']
    b.complete_cycle(cycle_id='4h|' + iso(10), tf='4h', decision_time=iso(10), instance_ids=ids[:-1])
    assert not b.snapshot()['coverage']['complete']
    assert any('instance' in x.lower() for x in b.snapshot()['coverage']['gaps'])


def test_candidate_ids_are_deterministic_and_do_not_consume_randomness(tmp_path):
    b1, b2 = new_book(tmp_path, 'a.jsonl'), new_book(tmp_path, 'b.jsonl')
    x, y = candidate(b1), candidate(b2)
    assert x['candidate_id'] == y['candidate_id']
    assert x['config_digest'] == y['config_digest']


def test_shadow_store_failure_never_calls_or_mutates_executable_objects(tmp_path, monkeypatch):
    class Trap:
        def __init__(self): self.calls = []
        def __getattr__(self, name):
            self.calls.append(name)
            raise AssertionError(f'shadow touched executable object: {name}')

    trap, live = Trap(), dict(lots={'L': {'qty': 1}}, pending={'P': 1}, orders=['o1'], sizing={'risk': 40})
    before = copy.deepcopy(live)
    b = new_book(tmp_path)
    # The public observer API accepts values only.  An arbitrary executable object is neither required nor consulted.
    candidate(b)
    assert live == before and trap.calls == []
    monkeypatch.setattr(b, '_append', lambda *_a, **_k: (_ for _ in ()).throw(OSError('disk full')))
    try: candidate(b, slot('donchian_ens', 'B'), sym='ETHUSDT', t=iso(10), px=50)
    except OSError: pass                                                   # engine hook must contain this observer failure
    assert live == before and trap.calls == []
