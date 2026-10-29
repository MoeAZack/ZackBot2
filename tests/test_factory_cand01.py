"""FACTORY-CAND-01 (Codex 96-h factory, #13 6442094571 / 6443422894): isolated PAPER candidate slots C-F01 / C-F02.

Pins: each slot is the exact sleeve its fixed-18 factory packet ran and is OFF by default; the slots sit outside the rotation
(PAPER only, live refused) and are appended to an installed rotation without touching existing slots; a candidate never
shares a coin with another slot and the correlated F01/F02 family has ONE position cap; candidate_guard switches a slot off
on protection / accounting / drift failure while its open lots stay protected; attribution is per candidate.
Synthetic data only (tests/test_safety.FakeX)."""
import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_safety import mk_engine                       # noqa: E402  (FakeX engine, PAPER)
import candidates as CD                                  # noqa: E402
import engine as E                                       # noqa: E402

FIXED18 = {'BTCUSDT', 'ETHUSDT', 'ZECUSDT', 'SOLUSDT', 'XRPUSDT', 'NEARUSDT', 'DOGEUSDT', 'UNIUSDT', 'SUIUSDT', 'QNTUSDT',
           'BNBUSDT', 'LSKUSDT', 'ENAUSDT', '1000PEPEUSDT', 'WLDUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT'}
SG = dict(close=100.0, atr=2.0, time='2026-10-04 00:00:00', le=True, se=False, lx=False, sx=False)
F01, F02 = 'C-F01', 'C-F02'


def slot(e, sid):
    return next(x for x in e.S['SLEEVES'] if x['id'] == sid)


def lots(e, sid, sym=None):
    return [l for l in e.state['lots'].values() if l['sleeve'] == sid and (sym is None or l['symbol'] == sym)]


def cycle4(e, sigs):
    e.compute_signals = lambda tf, syms, extra=(): ({k: dict(v) for k, v in sigs.items()} if tf == '4h' else {}, {})
    e.cycle('4h')


@pytest.fixture
def rot(tmp_path):
    e, tmp = mk_engine(str(tmp_path)); e.apply_preset(E.ROTATION_PRESET)
    assert not any(x['id'] in E.CANDIDATE_SLOTS for x in e.S['SLEEVES'])    # the preset itself is unchanged (pinned)
    assert e.sync_candidate_slots() == [F01, F02]                                 # added like an installed bot's, at start-up
    return e, tmp


@pytest.fixture
def on(rot):
    e, tmp = rot
    for sid in E.CANDIDATE_SLOTS: slot(e, sid)['enabled'] = True
    return e, tmp


# ------------------------------------------------------------------ the frozen cards
def test_each_slot_is_its_packets_exact_sleeve_and_off_by_default():
    assert E.CANDIDATE_SLOTS == {F01: ('ema_mom', 'long'), F02: ('exp_trend', 'long')}
    assert E.CANDIDATE_DEFAULT_ENABLED is False
    for sl in E.candidate_sleeves():
        assert sl['enabled'] is False and sl['sides'] == 'long' and sl['risk'] == 0.01 and sl['max_pos'] == 4
        assert sl['mgmt'] == {} and sl['tf'] == '4h' and set(sl['symbols']) == FIXED18 and len(sl['symbols']) == 18
    assert E.CANDIDATE_FAMILY[F01] == E.CANDIDATE_FAMILY[F02]                  # one correlated family (131 shared fills)


def test_slots_are_outside_the_rotation_and_appended_last_off(rot):
    e, _ = rot
    ids = [x['id'] for x in e.S['SLEEVES']]
    assert ids[-2:] == [F01, F02] and not slot(e, F01)['enabled'] and not slot(e, F02)['enabled']
    assert not any(sid in g for g in E.breadth_groups() for sid in E.CANDIDATE_SLOTS)
    assert ids[:-2] == [x['id'] for x in E.breadth_sleeves()]                 # every existing slot unchanged, in order
    assert e.entry_block(slot(e, F01), 'BTCUSDT', 'LONG') == 'strategy slot switched off'


def test_installed_rotation_gets_the_slots_once_and_existing_slots_byte_identical(rot):
    e, _ = rot
    e.S['SLEEVES'] = [x for x in e.S['SLEEVES'] if x['id'] not in E.CANDIDATE_SLOTS]   # saved before FACTORY-CAND-01
    e.S['SLEEVES'][0]['enabled'] = False
    before = copy.deepcopy(e.S['SLEEVES'])
    assert e.sync_candidate_slots() == [F01, F02]
    assert e.S['SLEEVES'][:len(before)] == before and e.sync_candidate_slots() == []
    slot(e, F01)['enabled'] = True                                             # an Integration opt-in survives a re-sync
    assert e.sync_candidate_slots() == [] and slot(e, F01)['enabled'] is True


def test_live_mode_refuses_candidate_entries_and_never_adds_slots(on):
    e, _ = on
    e.live = True
    for sid in E.CANDIDATE_SLOTS:
        assert 'PAPER' in e.scalp_block(slot(e, sid)) and 'PAPER' in (e.entry_block(slot(e, sid), 'BTCUSDT', 'LONG') or '')
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    assert not lots(e, F01)
    assert e.sync_candidate_slots() == []


# ------------------------------------------------------------------ entries, isolation, family cap
def test_an_enabled_candidate_opens_a_protected_paper_lot_outside_the_rotation(on):
    e, _ = on
    assert F01 not in E.rotation_at()['active'] and e.scalp_block(slot(e, F01)) is None
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    (l,) = lots(e, F01, 'BTCUSDT')
    assert l['stop_id'] in e.trade.stops and e.trade.stops[l['stop_id']][3] == l['stop'] < l['avg'] and e.live is False


def test_a_candidate_never_shares_a_coin_with_another_slot(on):
    e, _ = on
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    assert lots(e, F01, 'BTCUSDT')
    assert e.candidate_block(slot(e, F02), 'BTCUSDT') == f'candidate isolation: slot {F01} holds this coin'
    other = next(x for x in e.S['SLEEVES'] if x['key'] == 'ema_mom' and x['id'] not in E.CANDIDATE_SLOTS)
    assert e.candidate_block(other, 'BTCUSDT') == f'candidate isolation: slot {F01} holds this coin'
    cycle4(e, {f'{F02}|BTCUSDT': SG})
    assert not lots(e, F02) and 'candidate isolation' in e.missed[-1]['reason']
    assert e.candidate_block(slot(e, F01), 'BTCUSDT') is None                  # its own coin: the usual per-slot rules
    assert e.candidate_block(other, 'ETHUSDT') is None                         # non-candidate coins: unchanged


def test_a_rotation_lot_blocks_a_candidate_on_that_coin(on):
    e, _ = on
    e.state['lots']['x'] = dict(symbol='ETHUSDT', sleeve='ema_mom', side='LONG')
    assert e.candidate_block(slot(e, F01), 'ETHUSDT') == 'candidate isolation: slot ema_mom holds this coin'
    assert e.candidate_block(slot(e, F01), 'BTCUSDT') is None


def test_the_correlated_family_shares_one_position_cap(on):
    e, _ = on
    held = [(F01, 'SOLUSDT'), (F01, 'XRPUSDT'), (F02, 'NEARUSDT')]
    for i, (sid, sym) in enumerate(held): e.state['lots'][f'h{i}'] = dict(symbol=sym, sleeve=sid, side='LONG')
    assert e.candidate_block(slot(e, F02), 'ETHUSDT') is None                  # 3 family lots < the shared cap of 4
    e.state['lots']['h3'] = dict(symbol='DOGEUSDT', sleeve=F02, side='LONG')
    want = f'candidate family trend_long: 4 open (one shared cap of {E.CANDIDATE_MAX_POS})'
    assert e.candidate_block(slot(e, F01), 'ETHUSDT') == want == e.candidate_block(slot(e, F02), 'ETHUSDT')
    e.state['lots']['h4'] = dict(symbol='ADAUSDT', sleeve='ema_mom', side='LONG')   # a non-family lot never counts
    del e.state['lots']['h3']
    assert e.candidate_block(slot(e, F01), 'ETHUSDT') is None


# ------------------------------------------------------------------ automatic disable
def test_a_lost_exchange_stop_switches_the_candidate_off_and_its_lot_stays_protected(on):
    e, _ = on
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    (l,) = lots(e, F01, 'BTCUSDT')
    l.update(stop_miss=True, stop_miss_why='checking')                         # a re-check alone does not disable
    assert e.candidate_guard() == [] and slot(e, F01)['enabled']
    l['stop_miss_why'] = 'restoring'
    assert e.candidate_guard() == [F01, F02]                                   # FACTORY-CAND-02: the family fails closed
    assert slot(e, F01)['enabled'] is False and slot(e, F02)['enabled'] is False
    assert e.state['candidate_disabled'][F01]['why'] == 'protection: BTCUSDT stop restoring'
    assert lots(e, F01, 'BTCUSDT') == [l] and l['stop']                         # never closed or unprotected by the guard
    assert e.candidate_guard() == []                                           # once


def test_unmeasured_costs_switch_the_candidate_off_only_after_the_grace(on, monkeypatch):
    e, _ = on
    monkeypatch.setattr(E.AC, 'row_view', lambda row: dict(status=row['_st'], pnl=None))
    t0 = 1_800_000_000.0
    closed = E.pd.Timestamp(t0, unit='s', tz='UTC').isoformat()
    e.history.append(dict(sleeve=F02, symbol='ETHUSDT', closed=closed, _st='pending'))
    e.history.append(dict(sleeve='ema_mom', symbol='BTCUSDT', closed='2020-01-01T00:00:00+00:00', _st='pending'))
    assert e.candidate_guard(now=t0 + E.CANDIDATE_COST_PENDING_S - 1) == []
    assert e.candidate_guard(now=t0 + E.CANDIDATE_COST_PENDING_S) == [F01, F02]   # FACTORY-CAND-02: F01 by family
    assert e.state['candidate_disabled'][F02]['why'].startswith('accounting: ETHUSDT trade costs unmeasured 6.0 h')
    assert e.state['candidate_disabled'][F01]['why'].startswith(f'family trend_long fail-closed by {F02}: accounting')


@pytest.mark.parametrize('field,value', [('risk', 0.05), ('max_pos', 8), ('sides', 'both'), ('mgmt', {'tp_r': 9}),
                                         ('symbols', ['BTCUSDT']), ('key', 'ema_st'), ('tf', '1h')])
def test_a_slot_that_drifted_from_its_frozen_card_is_switched_off(on, field, value):
    e, _ = on
    slot(e, F02)[field] = value
    assert e.candidate_drift(slot(e, F02)) == [field]
    assert e.candidate_guard() == [F01, F02] and slot(e, F02)['enabled'] is False   # FACTORY-CAND-02: family fail-closed
    assert e.state['candidate_disabled'][F02]['why'] == f'drift: {field} differ from the frozen card'


def test_a_missing_mgmt_is_not_drift_but_reordered_symbols_are(on):
    e, _ = on
    sl = slot(e, F01)
    sl['mgmt'] = None
    assert e.candidate_drift(sl) == [] and e.candidate_guard() == []
    sl['symbols'] = list(reversed(sl['symbols']))                             # FACTORY-CAND-02 (OQ3): order is pinned
    assert e.candidate_drift(sl) == ['symbols']


def test_the_guard_ignores_disabled_and_non_candidate_slots_and_survives_a_restart(on):
    e, tmp = on
    slot(e, F01)['enabled'] = False; slot(e, F01)['risk'] = 0.5                # off already: nothing to do
    other = next(x for x in e.S['SLEEVES'] if x['id'] not in E.CANDIDATE_SLOTS)
    other['risk'] = 0.5
    assert e.candidate_guard() == []
    slot(e, F02)['risk'] = 0.5
    assert e.candidate_guard() == [F02]
    e2, _ = mk_engine(tmp)
    assert slot(e2, F02)['enabled'] is False and e2.state['candidate_disabled'][F02]['why'].startswith('drift')


def test_the_guard_runs_before_entries_in_the_cycle(on):
    e, _ = on
    slot(e, F01)['risk'] = 0.05
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    assert not lots(e, F01) and slot(e, F01)['enabled'] is False and e.missed[-1]['reason'] == 'strategy slot switched off'


# ------------------------------------------------------------------ attribution
def test_attribution_is_per_candidate_and_pending_rows_are_counted_never_valued():
    sls = E.candidate_sleeves() + [E.sleeve('ema_mom', 'ema_mom', 0.1, 0.02, 4)]
    lots_ = [dict(sleeve=F01, risk_usd=5.0), dict(sleeve='ema_mom', risk_usd=9.0)]
    hist = [dict(sleeve=F01, pnl=6.0, fees=0.4), dict(sleeve=F01, pnl=-2.0, fees=0.3), dict(sleeve=F02, pnl=-1.0, fees=0.1),
            dict(sleeve='ema_mom', pnl=100.0)]
    out = {c['id']: c for c in CD.summary(sls, lots_, hist, {F02: dict(why='drift: risk differ')})}
    assert set(out) == {F01, F02}
    a, b = out[F01], out[F02]
    assert (a['open'], a['open_risk'], a['closed'], a['wins'], a['losses'], a['net_pnl'], a['pf']) == (1, 5.0, 2, 1, 1, 4.0, 3.0)
    assert a['fees'] == pytest.approx(0.7) and a['disabled'] is None
    assert (b['closed'], b['net_pnl'], b['pf'], b['disabled']['why']) == (1, -1.0, 0.0, 'drift: risk differ')
    assert CD.summary(sls, (), [dict(sleeve=F01, pnl=2.0)])[0]['pf'] is None          # no loss yet: undefined, never inf


def test_the_status_snapshot_carries_the_candidates(on):
    e, _ = on
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    c = {x['id']: x for x in e.paper_books_status()['candidates']}
    assert c[F01]['open'] == 1 and c[F01]['enabled'] and c[F02]['open'] == 0


def test_a_restart_appends_the_slots_once_and_keeps_every_saved_slot(tmp_path):
    import json
    e, tmp = mk_engine(str(tmp_path)); e.apply_preset(E.ROTATION_PRESET)
    old = [x for x in e.S['SLEEVES'] if x['id'] not in E.CANDIDATE_SLOTS]
    old[0]['enabled'] = False
    e.S['SLEEVES'] = old; e.save_settings()
    snap = json.dumps(old, sort_keys=True)
    e2, _ = mk_engine(tmp)                                                     # connect() runs the start-up syncs
    assert json.dumps(e2.S['SLEEVES'][:len(old)], sort_keys=True) == snap
    assert [x['id'] for x in e2.S['SLEEVES'][len(old):]] == [F01, F02] and not slot(e2, F01)['enabled']
    slot(e2, F01)['enabled'] = True; e2.save_settings()
    e3, _ = mk_engine(tmp)
    assert [x['id'] for x in e3.S['SLEEVES']] == [x['id'] for x in e2.S['SLEEVES']] and slot(e3, F01)['enabled'] is True


# ------------------------------------------------------------------ Codex 6444017152: fail closed, working entries count
def _work(e, kind, owner, sym, n=0):
    """A working entry of slot `owner` on `sym` (pending trail entry / resting maker entry / unconfirmed entry)."""
    if kind == 'pending':
        e.state.setdefault('pending_entries', {})[f'{owner}|{sym}'] = dict(sleeve=owner, symbol=sym, side='LONG')
    elif kind == 'resting':
        e.state.setdefault('resting_entries', {})[f'R{n}|{owner}|{sym}'] = dict(sleeve=owner, symbol=sym, side='LONG')
    elif kind == 'unconfirmed_plan':
        e.state.setdefault('unconfirmed_entries', {})[f'UE|x|{sym}|LONG|{n}'] = dict(symbol=sym, side='LONG', plan=dict(sl=dict(id=owner)))
    else:                                                                      # an older record: the slot from its key
        e.state.setdefault('unconfirmed_entries', {})[f'UE|{owner}|{sym}|LONG|{n}'] = dict(symbol=sym, side='LONG')


KINDS = ('pending', 'resting', 'unconfirmed_plan', 'unconfirmed_key')


def test_a_guard_error_switches_every_enabled_candidate_off_and_blocks_entries(on, monkeypatch):
    e, _ = on
    e.history.append(dict(sleeve=F01, symbol='ETHUSDT', closed='2026-10-01T00:00:00+00:00'))

    def boom(row): raise RuntimeError('unreadable row')
    monkeypatch.setattr(E.AC, 'row_view', boom)
    assert sorted(e.candidate_guard()) == [F01, F02]
    assert not slot(e, F01)['enabled'] and not slot(e, F02)['enabled']
    assert e.state['candidate_disabled'][F01]['why'] == 'guard_error: RuntimeError'
    monkeypatch.undo()                                                         # the rest of the cycle reads rows too
    cycle4(e, {f'{F01}|BTCUSDT': SG, f'{F02}|ETHUSDT': dict(SG, close=50.0, atr=1.0)})
    assert not lots(e, F01) and not lots(e, F02)


def test_a_guard_error_after_a_lot_opened_never_touches_its_protection(on, monkeypatch):
    e, _ = on
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    (l,) = lots(e, F01, 'BTCUSDT')
    stop = dict(e.trade.stops[l['stop_id']]) if isinstance(e.trade.stops[l['stop_id']], dict) else e.trade.stops[l['stop_id']]
    monkeypatch.setattr(e, 'candidate_drift', lambda sl: (_ for _ in ()).throw(KeyError('x')))
    assert F01 in e.candidate_guard()
    assert lots(e, F01, 'BTCUSDT') == [l] and e.trade.stops[l['stop_id']] == stop


@pytest.mark.parametrize('kind', KINDS)
def test_a_working_entry_reserves_its_coin_in_both_directions(on, kind):
    e, _ = on
    _work(e, kind, 'ema_mom', 'ETHUSDT')
    assert e.candidate_block(slot(e, F01), 'ETHUSDT') == 'candidate isolation: slot ema_mom holds this coin'
    e2_owner = F01
    _work(e, kind, e2_owner, 'SOLUSDT', 1)
    other = next(x for x in e.S['SLEEVES'] if x['key'] == 'ema_st')
    assert e.candidate_block(other, 'SOLUSDT') == f'candidate isolation: slot {F01} holds this coin'
    assert e.candidate_block(slot(e, F02), 'SOLUSDT') == f'candidate isolation: slot {F01} holds this coin'


@pytest.mark.parametrize('kind', KINDS)
def test_working_entries_count_toward_the_shared_family_cap(on, kind):
    e, _ = on
    e.state['lots']['a'] = dict(symbol='SOLUSDT', sleeve=F01, side='LONG')
    e.state['lots']['b'] = dict(symbol='XRPUSDT', sleeve=F02, side='LONG')
    e.state['lots']['c'] = dict(symbol='NEARUSDT', sleeve=F01, side='LONG')
    assert e.candidate_block(slot(e, F02), 'ETHUSDT') is None
    _work(e, kind, F02, 'DOGEUSDT', 2)                                         # the 4th family claim is a working entry
    assert e.candidate_block(slot(e, F02), 'ETHUSDT') == f'candidate family trend_long: 4 open (one shared cap of {E.CANDIDATE_MAX_POS})'


def test_a_firing_trail_entry_does_not_count_against_itself(on):
    e, _ = on
    for i, sym in enumerate(('SOLUSDT', 'XRPUSDT', 'NEARUSDT')): e.state['lots'][f'l{i}'] = dict(symbol=sym, sleeve=F01, side='LONG')
    _work(e, 'pending', F02, 'ETHUSDT')
    assert e.candidate_block(slot(e, F02), 'ETHUSDT').startswith('candidate family')
    assert e.candidate_block(slot(e, F02), 'ETHUSDT', skip_pending=f'{F02}|ETHUSDT') is None


# ------------------------------------------------------------------ Codex 6444251851: sizing isolated from the books
def _book_engine(true_books):
    from test_true_books import tb_engine, T_SL
    e, tmp = tb_engine()
    e.S['TRUE_BOOKS'] = true_books
    e.S['SLEEVES'] = [T_SL] + E.candidate_sleeves()
    for sid in E.CANDIDATE_SLOTS: slot(e, sid)['enabled'] = True
    return e, T_SL


def _share_risk(e, sl):
    return e.equity() * sl['share'] * sl['risk'] * e.kelly_mult(sl) * e.governor_mult()


@pytest.mark.parametrize('true_books', [False, True])
def test_a_candidate_sizes_on_its_own_share_basis_in_every_book_mode(true_books):
    from test_true_books import size_of
    e, _ = _book_engine(true_books)
    for sid in E.CANDIDATE_SLOTS:
        sl = slot(e, sid)
        assert size_of(e, sl) == pytest.approx(_share_risk(e, sl), abs=0.005)   # 1/6 x 1 % of equity (cents), never a book NAV
    e2, _ = mk_engine()                                                    # share mode (no books): the identical basis
    e2.S['SLEEVES'] = E.candidate_sleeves(); slot(e2, F01)['enabled'] = True
    assert _share_risk(e2, slot(e2, F01)) == pytest.approx(e2.equity() * E.CANDIDATE_SHARE * E.CANDIDATE_RISK
                                                          * e2.kelly_mult(slot(e2, F01)) * e2.governor_mult())


@pytest.mark.parametrize('true_books', [False, True])
def test_a_candidate_lot_carries_no_book_and_leaves_every_book_unchanged(true_books):
    from test_true_books import SG as TSG
    e, _ = _book_engine(true_books)
    before = json.dumps(e.paper_books_status()['books'], sort_keys=True, default=str)
    tb0 = e.true_books(persist=False) if true_books else None
    assert e.open_lot(slot(e, F01), 'BTCUSDT', 'LONG', TSG, None, e.equity()), e.last_skip
    (l,) = lots(e, F01)
    assert l.get('book') is None
    assert json.dumps(e.paper_books_status()['books'], sort_keys=True, default=str) == before
    if true_books:
        tb1 = e.true_books(persist=False)
        for k in tb0:
            assert tb1[k]['lots'] == tb0[k]['lots'] and tb1[k]['notional'] == tb0[k]['notional']
            assert not any(x['reason'] == 'unowned_account_activity' for x in tb1[k]['incomplete'])
        assert e.true_book_block(slot(e, F02), sym='ETHUSDT', side='LONG') is None    # the book gate is not theirs


def test_candidate_working_entries_and_rows_are_never_unowned_book_activity():
    e, _ = _book_engine(True)
    e.state['resting_entries']['r1'] = dict(symbol='ETHUSDT', side='LONG', qty=1.0, sleeve=F01,
                                            plan=dict(qty=1.0, px=100.0, stop_dist=2.0, sl=dict(id=F01)))
    e.state['unconfirmed_entries'][f'UE|{F02}|SOLUSDT|LONG|1'] = dict(symbol='SOLUSDT', side='LONG', qty=1.0)
    e.state['pending_entries'][f'{F01}|XRPUSDT'] = dict(sleeve=F01, symbol='XRPUSDT', side='LONG')
    e.history.append(dict(id='c1', sleeve=F02, symbol='NEARUSDT', closed='2026-10-17T15:00:00+00:00', pnl=1.0))
    for b in e.true_books(persist=False).values():
        assert b['working'] == 0 and not any(x['reason'] == 'unowned_account_activity' for x in b['incomplete'])
    e.state['resting_entries']['r2'] = dict(symbol='ADAUSDT', side='LONG', qty=1.0, sleeve='MAN', manual=True,
                                            plan=dict(qty=1.0, px=1.0, stop_dist=0.1))
    assert all(any(x['reason'] == 'unowned_account_activity' for x in b['incomplete'])          # the rule still holds
               for b in e.true_books(persist=False).values())                                    # for anything else


def test_the_canary_is_labelled_a_downscale_of_the_research_packet():
    assert E.CANDIDATE_SHARE == pytest.approx(1 / 6) and E.CANDIDATE_RISK == 0.01                # packet: share 1.0


def test_a_full_trend_book_never_blocks_or_counts_a_candidate():
    from test_true_books import fake_lot, T_SL
    e, _ = _book_engine(True)
    for i in range(3): e.state['lots'][f'b{i}'] = fake_lot(e, 'B3_TREND', sleeve='T', stop=99.9, qty=0.01)   # on ETHUSDT
    e.state['pending_entries'][f'{F01}|XRPUSDT'] = dict(sleeve=F01, symbol='XRPUSDT', side='LONG')
    assert e.true_book_block(T_SL, sym='BTCUSDT', side='LONG') is None        # the candidate's armed entry holds no B3 slot
    e.state['lots']['b3'] = fake_lot(e, 'B3_TREND', sleeve='T', stop=99.9, qty=0.01)
    assert 'full' in e.true_book_block(T_SL, sym='BTCUSDT', side='LONG')       # B3 itself is full (4/4)
    assert e.true_book_block(slot(e, F02), sym='BTCUSDT', side='LONG') is None  # ... a candidate is never judged by it
