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


# ------------------------------------------------------------------ OQ4: C-F01-only policy, defaults unchanged
def test_defaults_stay_off():
    assert E.CANDIDATE_DEFAULT_ENABLED is False and all(not x['enabled'] for x in E.candidate_sleeves())
    assert list(E.CANDIDATE_SLOTS) == [F01, F02]
