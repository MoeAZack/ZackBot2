"""FACTORY-CAND-02 (Codex ruling 6507748888 on PR #246): correlated-family safety for the PAPER candidate slots.

Pins: OQ1 any member's drift / protection / accounting disable switches every enabled member of its CANDIDATE_FAMILY off in
the same guard pass (open lots stay protected, each reason names the triggering member and cause); OQ2 enabled family
members keep CANDIDATE_SLOTS priority order in SLEEVES; OQ3 the fixed-18 symbol list is pinned in order; OQ5 a deferred
entry reserves its coin and counts toward the shared family cap (never against its own retry); OQ6 the accounting check
scans every history row, not the newest 3000. OQ4: defaults stay OFF. Synthetic data only (tests/test_safety.FakeX)."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_factory_cand01 import F01, F02, SG, cycle4, lots, on, rot, slot   # noqa: E402,F401  (fixtures + helpers)
import engine as E                                                            # noqa: E402

FAM = 'trend_long'


# ------------------------------------------------------------------ OQ1: family-level fail-closed
def test_a_protection_failure_of_one_member_switches_the_whole_family_off(on):
    e, _ = on
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    (l,) = lots(e, F01, 'BTCUSDT')
    stop = e.trade.stops[l['stop_id']]
    l.update(stop_miss=True, stop_miss_why='owner_check')
    assert e.candidate_guard() == [F01, F02]
    assert not slot(e, F01)['enabled'] and not slot(e, F02)['enabled']
    why = e.state['candidate_disabled']
    assert why[F01]['why'] == 'protection: BTCUSDT stop owner_check'
    assert why[F02]['why'] == f'family {FAM} fail-closed by {F01}: protection: BTCUSDT stop owner_check'
    assert lots(e, F01, 'BTCUSDT') == [l] and e.trade.stops[l['stop_id']] == stop    # never closed or unprotected
    assert e.candidate_guard() == []                                                 # once


@pytest.mark.parametrize('trigger', [F01, F02])
def test_a_drift_of_either_member_disables_the_other_in_the_same_pass(on, trigger):
    e, _ = on
    slot(e, trigger)['risk'] = 0.02
    other = F02 if trigger == F01 else F01
    assert sorted(e.candidate_guard()) == [F01, F02]
    assert e.state['candidate_disabled'][other]['why'] == f'family {FAM} fail-closed by {trigger}: drift: risk differ from the frozen card'
    cycle4(e, {f'{other}|ETHUSDT': SG})
    assert not lots(e, other) and e.missed[-1]['reason'] == 'strategy slot switched off'


def test_an_already_disabled_member_keeps_its_own_reason(on):
    e, _ = on
    slot(e, F02)['enabled'] = False
    e.state['candidate_disabled'] = {F02: dict(why='integration: parked', t='x')}
    slot(e, F01)['tf'] = '1h'
    assert e.candidate_guard() == [F01] and e.state['candidate_disabled'][F02]['why'] == 'integration: parked'


def test_a_candidate_outside_the_family_is_unaffected(on, monkeypatch):
    e, _ = on
    monkeypatch.setattr(E, 'CANDIDATE_FAMILY', {F01: FAM})                    # F02 as an unrelated, non-family candidate
    slot(e, F02)['risk'] = 0.02
    assert e.candidate_guard() == [F02] and slot(e, F01)['enabled']
    slot(e, F01)['risk'] = 0.02; slot(e, F02).update(enabled=True, risk=0.01)
    assert e.candidate_guard() == [F01] and slot(e, F02)['enabled']


# ------------------------------------------------------------------ OQ2: priority pin
def test_family_members_out_of_priority_order_are_family_drift(on):
    e, _ = on
    sl = e.S['SLEEVES']
    i1, i2 = sl.index(slot(e, F01)), sl.index(slot(e, F02))
    sl[i1], sl[i2] = sl[i2], sl[i1]                                            # C-F02 ahead of C-F01
    assert e.candidate_drift(slot(e, F01)) == [] and e.candidate_drift(slot(e, F02)) == []   # each card is intact
    assert sorted(e.candidate_guard()) == [F01, F02]
    for sid in (F01, F02):
        assert e.state['candidate_disabled'][sid]['why'] == \
            f"drift: family {FAM} order ['{F02}', '{F01}'] differs from the pinned priority ['{F01}', '{F02}']"


def test_a_single_enabled_member_has_no_order_to_pin(on):
    e, _ = on
    sl = e.S['SLEEVES']
    i1, i2 = sl.index(slot(e, F01)), sl.index(slot(e, F02))
    sl[i1], sl[i2] = sl[i2], sl[i1]
    slot(e, F02)['enabled'] = False
    assert e.candidate_guard() == [] and slot(e, F01)['enabled']


def test_the_pinned_order_passes(on):
    e, _ = on
    assert [x['id'] for x in e.S['SLEEVES'] if x['id'] in E.CANDIDATE_SLOTS] == [F01, F02] and e.candidate_guard() == []


# ------------------------------------------------------------------ OQ3: symbol-order pin
def test_a_reordered_universe_is_drift_and_fails_the_family_closed(on):
    e, _ = on
    syms = list(E.CAND_FIXED18); syms[0], syms[1] = syms[1], syms[0]
    slot(e, F02)['symbols'] = syms
    assert e.candidate_drift(slot(e, F02)) == ['symbols']
    assert e.candidate_guard() == [F01, F02]
    slot(e, F02)['symbols'] = tuple(E.CAND_FIXED18)                          # same order: never drift
    assert e.candidate_drift(slot(e, F02)) == []


# ------------------------------------------------------------------ OQ5: deferred entries are claims
def _defer(e, sid, sym):
    sg = dict(SG, time=E.pd.Timestamp(time.time(), unit='s').floor('4h').strftime('%Y-%m-%d %H:%M:%S'))
    r = dict(sl=slot(e, sid) if sid in E.CANDIDATE_SLOTS else next(x for x in e.S['SLEEVES'] if x['id'] == sid),
             sym=sym, side='LONG', sg=sg, frames={}, n=0, row={})
    vars(e).setdefault('_deferred', []).append(r)
    return r


def test_a_deferred_entry_reserves_its_coin_against_every_other_slot(on):
    e, _ = on
    _defer(e, F01, 'ETHUSDT')
    assert e.candidate_block(slot(e, F02), 'ETHUSDT') == f'candidate isolation: slot {F01} holds this coin'
    assert e.entry_block(slot(e, F02), 'ETHUSDT', 'LONG') == f'candidate isolation: slot {F01} holds this coin'
    other = next(x for x in e.S['SLEEVES'] if x['key'] == 'ema_st')
    assert e.candidate_block(other, 'ETHUSDT') == f'candidate isolation: slot {F01} holds this coin'
    rot_ = next(x for x in e.S['SLEEVES'] if x['id'] not in E.CANDIDATE_SLOTS)
    _defer(e, rot_['id'], 'SOLUSDT')                                           # a rotation deferral blocks a candidate
    assert e.candidate_block(slot(e, F01), 'SOLUSDT') == f"candidate isolation: slot {rot_['id']} holds this coin"


def test_a_deferred_entry_counts_toward_the_shared_family_cap(on):
    e, _ = on
    for i, sym in enumerate(('SOLUSDT', 'XRPUSDT', 'NEARUSDT')): e.state['lots'][f'l{i}'] = dict(symbol=sym, sleeve=F01, side='LONG')
    assert e.candidate_block(slot(e, F02), 'DOGEUSDT') is None
    _defer(e, F02, 'ETHUSDT')                                                  # 4th family claim: would let a 5th open
    want = f'candidate family {FAM}: 4 open (one shared cap of {E.CANDIDATE_MAX_POS})'
    assert e.candidate_block(slot(e, F02), 'DOGEUSDT') == want == e.candidate_block(slot(e, F01), 'DOGEUSDT')


def test_a_deferred_retry_never_counts_against_itself(on, monkeypatch):
    e, _ = on
    for i, sym in enumerate(('SOLUSDT', 'XRPUSDT', 'NEARUSDT')): e.state['lots'][f'l{i}'] = dict(symbol=sym, sleeve=F01, side='LONG')
    r = _defer(e, F02, 'ETHUSDT')
    seen = []

    def fake_open(sl, s, side, sg, df, eq, **kw):
        seen.append(e.candidate_block(sl, s)); e.last_skip = 'x'; return False
    monkeypatch.setattr(e, 'open_lot', fake_open)
    monkeypatch.setattr(e, 'true_books', lambda persist=False: {})
    e._reconsider_one(r)
    assert seen == [None] and e._defer_cur is None


# ------------------------------------------------------------------ OQ6: complete accounting scan
def test_a_pending_cost_row_older_than_3000_newer_rows_still_disables(on, monkeypatch):
    e, _ = on
    monkeypatch.setattr(E.AC, 'row_view', lambda row: dict(status=row.get('_st', 'measured'), pnl=0.0))
    e.history[:] = [dict(sleeve=F01, symbol='ETHUSDT', closed='2026-01-01T00:00:00+00:00', _st='pending')] + \
        [dict(sleeve=F01 if i % 2 else 'ema_mom', symbol='BTCUSDT', closed='2026-02-01T00:00:00+00:00') for i in range(3001)]
    assert e.candidate_guard() == [F01, F02]
    assert e.state['candidate_disabled'][F01]['why'].startswith('accounting: ETHUSDT trade costs unmeasured')


# ------------------------------------------------------------------ OQ6 (Codex 6508164005): the durable pending-cost index
def _close_f01(e):
    """A real candidate close (open via the cycle, close via _finish): FakeX leaves its costs pending."""
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    (k,) = [k for k, l in e.state['lots'].items() if l['sleeve'] == F01]
    e._finish(k, 'stop')
    row = e.history[-1]
    assert row['id'] == k and E.AC.row_view(row)['status'] == 'pending'
    return k, row


def _other_close(e, sym='ETHUSDT'):
    """A real close of a NON-candidate lot (test_safety's sleeve 'T'; the rotation's group gate is not under test here)."""
    from test_safety import opened
    gate, e.rotation_block = e.rotation_block, lambda sl, ts=None: None
    try: e._finish(opened(e, sym), 'stop')
    finally: e.rotation_block = gate


def _reenable(e):
    for sid in E.CANDIDATE_SLOTS: slot(e, sid)['enabled'] = True


def test_an_evicted_pending_candidate_close_still_disables_the_family_after_a_restart(on):
    from test_safety import mk_engine
    e, tmp = on
    k, row = _close_f01(e)
    assert e.state[E.CANDIDATE_PENDING_KEY] == {k: dict(sleeve=F01, symbol='BTCUSDT', closed=row['closed'])}
    e.history[:] = [row] + [dict(id=f'x{i}', sleeve='ema_mom', symbol='ETHUSDT', closed=row['closed']) for i in range(3000)]
    _other_close(e)                                          # one more real close: the trim evicts the candidate row
    assert len(e.history) == 3000 and all(h.get('id') != k for h in e.history)
    e.save_state()
    e2, _ = mk_engine(tmp)                                   # restart from the persisted state + history
    _reenable(e2)
    assert all(h.get('id') != k for h in e2.history) and k in e2.state[E.CANDIDATE_PENDING_KEY]
    t = E.pd.Timestamp(row['closed']).timestamp()
    assert e2.candidate_guard(now=t + E.CANDIDATE_COST_PENDING_S - 1) == []
    assert e2.candidate_guard(now=t + E.CANDIDATE_COST_PENDING_S) == [F01, F02]
    why = e2.state['candidate_disabled']
    assert why[F01]['why'] == 'accounting: BTCUSDT trade costs unmeasured 6.0 h after close'
    assert why[F02]['why'] == f'family {FAM} fail-closed by {F01}: accounting: BTCUSDT trade costs unmeasured 6.0 h after close'
    assert k in e2.state[E.CANDIDATE_PENDING_KEY]                # only a settlement clears it


def test_settlement_clears_the_index_entry_exactly_once(on, monkeypatch):
    e, _ = on
    real = E.AC.row_view
    monkeypatch.setattr(E.AC, 'row_view', lambda r: dict(status='measured', pnl=0.0, r=None, why=[]) if r.get('_settled') else real(r))
    k, row = _close_f01(e)
    t = E.pd.Timestamp(row['closed']).timestamp() + E.CANDIDATE_COST_PENDING_S
    e._cost_final('row', row)                                # a settlement pass that is still incomplete: kept
    assert k in e.state[E.CANDIDATE_PENDING_KEY]
    row['_settled'] = True
    e._cost_final('row', row)                                # the settlement path every cost record goes through
    assert e.state[E.CANDIDATE_PENDING_KEY] == {}
    e._cost_final('row', row)                                # second settlement: a no-op, never an error
    assert e._candidate_pending_clear(row) is False and e.state[E.CANDIDATE_PENDING_KEY] == {}
    assert e.candidate_guard(now=t) == [] and e.candidate_guard(now=t) == []
    assert slot(e, F01)['enabled'] and slot(e, F02)['enabled']
    from test_safety import mk_engine
    e2, _ = mk_engine(on[1])                                 # restart WITHOUT an explicit save: the clear itself persisted
    assert e2.state.get(E.CANDIDATE_PENDING_KEY) == {}


def test_the_guard_reconciles_a_settled_row_still_in_history(on, monkeypatch):
    e, _ = on
    real = E.AC.row_view
    monkeypatch.setattr(E.AC, 'row_view', lambda r: dict(status='measured', pnl=0.0, r=None, why=[]) if r.get('_settled') else real(r))
    k, row = _close_f01(e)
    row['_settled'] = True                                   # settled without passing the hook (e.g. an older build)
    assert e.candidate_guard(now=E.pd.Timestamp(row['closed']).timestamp() + 10 * 3600) == []
    assert e.state[E.CANDIDATE_PENDING_KEY] == {}
    from test_safety import mk_engine
    e2, _ = mk_engine(on[1])                                 # restart WITHOUT an explicit save: the reconciliation persisted
    assert e2.state.get(E.CANDIDATE_PENDING_KEY) == {}


def test_a_pending_non_candidate_close_never_enters_the_index(on):
    e, _ = on
    _other_close(e)
    assert E.AC.row_view(e.history[-1])['status'] == 'pending' and e.history[-1]['sleeve'] not in E.CANDIDATE_SLOTS
    assert not e.state.get(E.CANDIDATE_PENDING_KEY)


@pytest.mark.parametrize('idx,sym', [({'r1': 'garbage'}, 'r1'), ({'r1': dict(sleeve=F01, symbol='SOLUSDT', closed='not a time')}, 'SOLUSDT'),
                                     ({'r1': dict(sleeve='??', symbol='SOLUSDT', closed='2020-01-01T00:00:00+00:00')}, 'SOLUSDT'),
                                     ({'r1': dict(symbol='SOLUSDT')}, 'SOLUSDT'), (['r1'], None)])
def test_a_malformed_index_fails_closed(on, idx, sym):
    e, _ = on
    e.state[E.CANDIDATE_PENDING_KEY] = idx
    assert e.candidate_guard() == [F01, F02]
    why = e.state['candidate_disabled'][F01]['why']
    assert why.startswith(f'accounting: {sym} trade costs unmeasured' if sym else 'accounting: pending-cost index unreadable')


def test_an_older_state_without_the_index_loads_and_passes(on):
    from test_safety import mk_engine
    e, tmp = on
    e.state.pop(E.CANDIDATE_PENDING_KEY, None); e.save_state()
    e2, _ = mk_engine(tmp)
    _reenable(e2)
    assert E.CANDIDATE_PENDING_KEY not in e2.state and e2.candidate_guard() == []


def test_history_retention_is_unchanged(on):
    e, _ = on
    e.history[:] = [dict(id=f'x{i}', sleeve='ema_mom') for i in range(3000)]
    _other_close(e)
    assert len(e.history) == 3000 and e.history[0]['id'] == 'x1'


# ------------------------------------------------------------------ OQ4: C-F01-only policy, defaults unchanged
def test_defaults_stay_off():
    assert E.CANDIDATE_DEFAULT_ENABLED is False and all(not x['enabled'] for x in E.candidate_sleeves())
    assert list(E.CANDIDATE_SLOTS) == [F01, F02]
