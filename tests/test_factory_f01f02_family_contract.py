"""FACTORY-F01F02-FAMILY correlated-family contract (Codex 6507317942, DRAFT; amendment 1 encodes the resolved rulings
6507748888 / 6508164005 / 6508707059): pins the JSON twin to the FACTORY-CAND-02 engine (CANDIDATE_* constants incl.
CANDIDATE_PENDING_KEY, family map, source sha256 of the seven candidate functions, slot priority in Engine.cycle), to the
SIGNED repro contract's 160 / 171 / 131 numbers and to the OQ1-OQ6 resolved-rulings block, and exercises candidate_block /
candidate_guard behaviour for the family: same-bar collision dedup (C-F01 wins), the shared cap of 4 across both slots,
family-level fail-closed on every auto-disable trigger, the order pins and the fail-closed guard error. The OQ5/OQ6
adversarial proofs live in tests/test_factory_cand02_family_safety.py and are cited by name. Synthetic data only
(tests/test_safety.FakeX); never reads market data or packets."""
import ast
import hashlib
import json
import os
import sys
from fractions import Fraction

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_safety import mk_engine                       # noqa: E402  (FakeX engine, PAPER)
import engine as E                                       # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CDIR = os.path.join(ROOT, 'contracts', 'factory_f01f02_family')
JS = os.path.join(CDIR, 'factory_f01f02_family_contract.v1.json')
MD = os.path.join(CDIR, 'FACTORY_F01F02_FAMILY_CONTRACT.md')
REPRO = os.path.join(ROOT, 'contracts', 'factory_f01f02_repro', 'factory_f01f02_repro_contract.v1.json')
CAND02 = os.path.join(ROOT, 'tests', 'test_factory_cand02_family_safety.py')
RULINGS = ('6507748888', '6508164005', '6508707059')
F01, F02 = 'C-F01', 'C-F02'
SG = dict(close=100.0, atr=2.0, time='2026-10-04 00:00:00', le=True, se=False, lx=False, sx=False)


@pytest.fixture(scope='module')
def c():
    with open(JS, encoding='utf-8') as fh:
        return json.load(fh)


@pytest.fixture(scope='module')
def repro():
    with open(REPRO, encoding='utf-8') as fh:
        return json.load(fh)


@pytest.fixture(scope='module')
def engine_src():
    with open(os.path.join(ROOT, 'engine.py'), 'rb') as fh:
        src = fh.read().decode('utf-8').replace('\r\n', '\n')
    cls = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == 'Engine')
    return src, {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}


def slot(e, sid):
    return next(x for x in e.S['SLEEVES'] if x['id'] == sid)


def lots(e, sid, sym=None):
    return [l for l in e.state['lots'].values() if l['sleeve'] == sid and (sym is None or l['symbol'] == sym)]


def cycle4(e, sigs):
    e.compute_signals = lambda tf, syms, extra=(): ({k: dict(v) for k, v in sigs.items()} if tf == '4h' else {}, {})
    e.cycle('4h')


@pytest.fixture
def on(tmp_path):
    e, tmp = mk_engine(str(tmp_path)); e.apply_preset(E.ROTATION_PRESET)
    assert e.sync_candidate_slots() == [F01, F02]
    for sid in E.CANDIDATE_SLOTS: slot(e, sid)['enabled'] = True
    return e, tmp


# ------------------------------------------------------------------ the contract pins the code
def test_status_is_unsigned_draft_and_md_present(c):
    assert c['contract'] == 'FACTORY-F01F02-FAMILY' and c['version'] == 1
    assert c['status'] == 'DRAFT' and c['signed_by'] is None and '6507317942' in c['assignment']
    md = open(MD, encoding='utf-8').read()
    assert 'DRAFT' in md and 'Amendment 1' in md
    for r in RULINGS: assert r in c['amended_at'] and r in c['assignment'] and r in md
    assert c['code']['citation_base_short'] == '7b8e3e9' and '0dc9f05' in c['code']['original_citation_base']


def test_engine_constants_equal_the_json(c):
    k = c['engine_constants']
    assert {s: tuple(v) for s, v in k['CANDIDATE_SLOTS'].items()} == E.CANDIDATE_SLOTS
    assert list(k['CANDIDATE_SLOTS']) == list(E.CANDIDATE_SLOTS)                   # F01 first: the append order
    assert k['CANDIDATE_FAMILY'] == E.CANDIDATE_FAMILY and set(E.CANDIDATE_FAMILY.values()) == {c['family']['id']}
    assert k['CANDIDATE_MAX_POS'] == E.CANDIDATE_MAX_POS == 4
    assert k['CANDIDATE_RISK'] == E.CANDIDATE_RISK and Fraction(k['CANDIDATE_SHARE']) == Fraction(E.CANDIDATE_SHARE).limit_denominator(100)
    assert k['CANDIDATE_DEFAULT_ENABLED'] is E.CANDIDATE_DEFAULT_ENABLED is False
    assert k['CANDIDATE_COST_PENDING_S'] == E.CANDIDATE_COST_PENDING_S == 6 * 3600
    assert tuple(k['CANDIDATE_STOP_FAIL']) == E.CANDIDATE_STOP_FAIL and tuple(k['CANDIDATE_CARD']) == E.CANDIDATE_CARD
    assert tuple(k['CAND_FIXED18']) == E.CAND_FIXED18
    assert k['CANDIDATE_PENDING_KEY'] == E.CANDIDATE_PENDING_KEY == 'candidate_cost_pending'
    assert c['family']['primary'] == F01 and c['family']['secondary'] == F02


def test_candidate_function_sources_match_their_pins(c, engine_src):
    src, fns = engine_src
    pins = {k: v for k, v in c['code']['pinned_function_sha256'].items() if not k.startswith('_')}
    assert len(pins) == 7 and {p.split('.')[-1] for p in pins} == {
        '_claims', 'candidate_block', 'candidate_drift', 'candidate_guard',
        '_candidate_pending_why', '_candidate_pending_note', '_candidate_pending_clear'}
    for cite, sha in pins.items():
        name = cite.split('.')[-1]
        assert hashlib.sha256(ast.get_source_segment(src, fns[name]).encode('utf-8')).hexdigest() == sha, cite


def _cites(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'source' and isinstance(v, str): yield v
            else: yield from _cites(v)
    elif isinstance(obj, list):
        for v in obj: yield from _cites(v)


def test_every_cited_engine_function_exists(c, engine_src):
    _src, fns = engine_src
    mod = {n.name for n in ast.parse(_src).body if isinstance(n, ast.FunctionDef)}
    names = set()
    for s in list(_cites(c)) + [c['auto_disable']['where']]:
        for part in s.replace('(', ',').replace(')', ',').split(','):
            part = part.strip().split(' ')[0]
            if part.startswith('engine.py:'): names.add(part[len('engine.py:'):])
    assert {'Engine._claims', 'Engine.candidate_block', 'Engine.candidate_guard', 'Engine.cycle', 'Engine._reconsider_one',
            'Engine._finish', 'Engine._cost_final', 'Engine._candidate_pending_why', 'Engine._candidate_pending_clear'} <= names
    for n in names:
        if n.startswith('Engine.'): assert n.split('.', 1)[1] in fns, n
        else: assert n in mod, n


def test_cycle_decides_slot_by_slot_after_the_guard(engine_src):
    src, fns = engine_src
    body = ast.get_source_segment(src, fns['cycle'])
    g, outer = body.index('self.candidate_guard()'), body.index('for sl in sleeves:')
    inner = body.index('for s in self.sleeve_symbols(sl, include_off=True):', outer)
    assert g < outer < inner and body.index('self.candidate_block(sl, s)', inner) > inner


def test_repro_numbers_carried_exactly(c, repro):
    r, ov = c['repro_contract'], repro['overlap']
    assert repro['status'] == 'SIGNED'
    assert r['f01_closed_trades'] == repro['cells']['F01']['expected']['closed_trades'] == 160
    assert r['f02_closed_trades'] == repro['cells']['F02']['expected']['closed_trades'] == 171
    assert r['raw_entry_signals_each'] == repro['cells']['F01']['expected']['raw_entry_signals'] == \
        repro['cells']['F02']['expected']['raw_entry_signals'] == 228
    assert r['shared_round_trip'] == ov['shared_exact_fills'] == 131 and r['round_trip_key'] == ov['exact_fill_key']
    assert r['jaccard_round_trip'] == ov['jaccard'] == round(131 / (160 + 171 - 131), 3)
    es = ov['entry_stream']
    assert (r['entry_stream_shared'], r['entry_stream_union']) == (es['shared'], es['union']) == (160, 171)
    assert r['entry_stream_key'] == es['key'] and r['every_f01_entry_in_f02'] is es['every_f01_entry_in_f02'] is True
    assert r['f01_net_r'] == repro['cells']['F01']['expected']['net_r'] and r['f02_net_r'] == repro['cells']['F02']['expected']['net_r']
    assert c['family_book_replay']['inputs']['F01']['file_sha256'] == repro['cells']['F01']['full_packet']['file_sha256']
    assert c['family_book_replay']['inputs']['F02']['file_sha256'] == repro['cells']['F02']['full_packet']['file_sha256']


def test_replay_is_labelled_derived_and_internally_consistent(c, repro):
    rp = c['family_book_replay']
    assert 'DERIVED' in rp['label'] and 'non-promotable' in rp['label'] and 'NOT an evaluation' in rp['label']
    res, b, f01 = rp['result'], rp['f02_suppression_breakdown'], repro['cells']['F01']['expected']
    assert (res['family_trades'], res['family_net_r'], res['family_pf'], res['family_max_dd_r']) == \
        (f01['closed_trades'], f01['net_r'], f01['pf'], f01['max_dd_r'])
    assert res['f02_trades_taken'] == 0 and res['f02_trades_suppressed'] == 171
    assert b['shared_round_trip']['n'] + b['same_entry_different_exit']['n'] + b['f02_only_entries']['n'] == 171
    assert b['shared_round_trip']['n'] + b['same_entry_different_exit']['n'] == 160          # = the entry-stream overlap
    o = b['f02_only_entries']
    assert o['verified_from_closed_trades'] + o['not_derivable_from_closed_trades'] == o['n'] == 171 - 160
    assert abs(b['shared_round_trip']['sum_r'] + b['same_entry_different_exit']['f02_sum_r'] + o['sum_r']
               - res['f02_suppressed_standalone_net_r']) < 1e-4


def test_the_same_bar_exit_convention_behind_8_proven_is_stated(c, repro):
    """Codex 6510348874: 8 vs 7 proven depends on whether a C-F01 lot exiting on the F02 fill bar still holds its slot. The
    contract must state the convention and tie it to the signed per-bar order (fills at step 2, before that bar's exits)."""
    conv = c['family_book_replay']['f02_suppression_breakdown']['f02_only_entries']['same_bar_exit_convention']
    assert 'still occupies' in conv and '(step 2)' in conv and '(step 3)' in conv and 'would be 7' in conv
    order = repro['semantics']['per_bar_management_order']['rule']
    assert order.index('(2) fill pending entries') < order.index('(3) for each open position')


def test_auto_disable_triggers_and_reenable_owner(c):
    a = c['auto_disable']
    assert [t['id'] for t in a['triggers']] == ['drift', 'protection', 'family_order', 'accounting', 'guard_error']
    assert 'Integration' in a['re_enable'] and 'never' in a['re_enable']
    assert a['scope'].startswith('per family') and 'every enabled member' in a['family_effect']
    d = a['durable_pending_index']
    assert "state['candidate_cost_pending']" in d['key'] and '_finish' in d['set'] and 'exactly once' in d['cleared']
    assert 'acknowledged' in d['cleared'] and 'save_state()' in d['persisted'] and 'never time-cleared' in d['evicted_unsettleable']
    assert 'C-F01 ONLY' in c['family']['enable_policy'] and 'OFF' in c['family']['enable_policy']
    assert c['collision']['slot_priority']['winner_on_same_bar_collision'] == F01


def test_forbidden_and_resolved_rulings(c):
    f = ' '.join(c['forbidden'])
    for word in ('2026-09-28', 'PAPER', 'full-risk', 'order ids', 'mainnet', 'tuning', 'promoting'):
        assert word in f
    assert c['open_questions'] == []
    rr = c['resolved_rulings']
    assert [q['id'] for q in rr] == [f'OQ{i}' for i in range(1, 7)]
    assert all('6507748888' in q['ruling'] for q in rr) and all(r in rr[5]['ruling'] for r in RULINGS)
    want = {'OQ1': 'family-level fail closed', 'OQ2': 'C-F01 before C-F02', 'OQ3': 'exact ordered CAND_FIXED18',
            'OQ4': 'C-F01 only', 'OQ5': 'counted in _claims', 'OQ6': 'durable'}
    for q in rr: assert want[q['id']] in q['resolution'], q['id']
    assert 'never time-cleared' in rr[5]['resolution'] and 'FILL-PNL-02' in rr[5]['resolution']


# the OQ5 / OQ6 adversarial proofs required by the rulings (cited by name; they run in their own module)
CAND02_PROOFS = {
    'OQ5': ('test_a_deferred_entry_reserves_its_coin_against_every_other_slot',
            'test_a_deferred_entry_counts_toward_the_shared_family_cap', 'test_a_deferred_retry_never_counts_against_itself'),
    'OQ6': ('test_a_pending_cost_row_older_than_3000_newer_rows_still_disables',
            'test_an_evicted_pending_candidate_close_still_disables_the_family_after_a_restart',
            'test_settlement_clears_the_index_entry_exactly_once', 'test_the_guard_reconciles_a_settled_row_still_in_history',
            'test_a_malformed_index_fails_closed'),
}


def test_the_ruling_proofs_exist():
    with open(CAND02, encoding='utf-8') as fh:
        have = {n.name for n in ast.parse(fh.read()).body if isinstance(n, ast.FunctionDef)}
    for oq, names in CAND02_PROOFS.items():
        for n in names: assert n in have, (oq, n)


# ------------------------------------------------------------------ behaviour: collision dedup + shared cap
@pytest.mark.parametrize('order', ['f01_first', 'f02_first'])
def test_same_bar_shared_signal_is_filled_once_by_the_primary(on, order):
    e, _ = on
    sigs = {f'{F01}|BTCUSDT': SG, f'{F02}|BTCUSDT': SG}
    if order == 'f02_first': sigs = dict(reversed(list(sigs.items())))         # the signal dict order plays no part
    cycle4(e, sigs)
    assert len(lots(e, F01, 'BTCUSDT')) == 1 and not lots(e, F02)
    miss = [m for m in e.missed if m.get('sleeve') == F02 and m.get('symbol') == 'BTCUSDT']
    assert miss and miss[-1]['reason'] == f'candidate isolation: slot {F01} holds this coin'


def test_a_secondary_holding_blocks_the_primary_on_that_coin(on):
    e, _ = on
    e.state['lots']['x'] = dict(symbol='ETHUSDT', sleeve=F02, side='LONG')
    assert e.candidate_block(slot(e, F01), 'ETHUSDT') == f'candidate isolation: slot {F02} holds this coin'
    assert e.candidate_block(slot(e, F01), 'SOLUSDT') is None


def test_same_bar_cap_of_four_is_shared_across_both_slots(on, monkeypatch):
    e, _ = on
    monkeypatch.setattr(e, 'reconcile', lambda eq: None)                       # the cloned lots are not on the exchange
    cycle4(e, {f'{F01}|BTCUSDT': SG})                                          # one real lot, cloned onto 3 other coins
    (k0, l0), = [(k, l) for k, l in e.state['lots'].items() if l['sleeve'] == F01]
    del e.state['lots'][k0]
    for i, s in enumerate(('SOLUSDT', 'XRPUSDT', 'NEARUSDT')):               # 3 family lots already open
        e.state['lots'][f'h{i}'] = dict(l0, symbol=s)
    cycle4(e, {f'{F02}|ETHUSDT': SG, f'{F01}|BTCUSDT': SG})                     # same bar: F01 takes the 4th claim
    assert len(lots(e, F01, 'BTCUSDT')) == 1 and not lots(e, F02)
    want = f'candidate family trend_long: 4 open (one shared cap of {E.CANDIDATE_MAX_POS})'
    miss = [m for m in e.missed if m.get('sleeve') == F02 and m.get('symbol') == 'ETHUSDT']
    assert miss and miss[-1]['reason'] == want == e.candidate_block(slot(e, F01), 'ETHUSDT')


def test_mixed_family_claims_count_once_each_toward_the_cap(on):
    e, _ = on
    e.state['lots']['a'] = dict(symbol='SOLUSDT', sleeve=F01, side='LONG')
    e.state['lots']['b'] = dict(symbol='XRPUSDT', sleeve=F02, side='LONG')
    e.state.setdefault('resting_entries', {})['R|C-F01|NEARUSDT'] = dict(sleeve=F01, symbol='NEARUSDT', side='LONG')
    assert e.candidate_block(slot(e, F02), 'ETHUSDT') is None and e.candidate_block(slot(e, F01), 'ETHUSDT') is None
    e.state.setdefault('unconfirmed_entries', {})[f'UE|{F02}|DOGEUSDT|LONG|0'] = dict(symbol='DOGEUSDT', side='LONG')
    assert 'shared cap of 4' in e.candidate_block(slot(e, F01), 'ETHUSDT')
    e.state['lots']['z'] = dict(symbol='ADAUSDT', sleeve='ema_mom', side='LONG')   # non-family never counts
    del e.state['lots']['a']
    assert e.candidate_block(slot(e, F01), 'ETHUSDT') is None


def test_secondary_trades_only_when_the_primary_is_off(on):
    e, _ = on
    slot(e, F01)['enabled'] = False                     # an Integration switch-off (OQ4 policy keeps C-F02 OFF instead)
    cycle4(e, {f'{F01}|BTCUSDT': SG, f'{F02}|BTCUSDT': SG})
    assert not lots(e, F01) and len(lots(e, F02, 'BTCUSDT')) == 1


# ------------------------------------------------------------------ behaviour: auto-disable
def _guard_once(e, sid):
    """OQ1: the triggering member and every other enabled family member go off in the same pass, never back on."""
    assert e.candidate_guard() == [F01, F02]
    why = e.state['candidate_disabled']
    other = F02 if sid == F01 else F01
    assert why[other]['why'] == f"family trend_long fail-closed by {sid}: {why[sid]['why']}"
    assert e.candidate_guard() == [] and not slot(e, F01)['enabled'] and not slot(e, F02)['enabled']


@pytest.mark.parametrize('why', ['restoring', 'owner_check'])
def test_protection_trigger_disables_the_family_and_keeps_the_lot(on, why):
    e, _ = on
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    (l,) = lots(e, F01, 'BTCUSDT')
    l.update(stop_miss=True, stop_miss_why='checking')
    assert e.candidate_guard() == []
    l['stop_miss_why'] = why
    _guard_once(e, F01)
    assert e.state['candidate_disabled'][F01]['why'] == f'protection: BTCUSDT stop {why}'
    assert lots(e, F01, 'BTCUSDT') == [l] and l['stop_id'] in e.trade.stops


def test_drift_trigger(on):
    e, _ = on
    slot(e, F01)['max_pos'] = 8                                                # a second budget would be drift
    _guard_once(e, F01)
    assert e.state['candidate_disabled'][F01]['why'] == 'drift: max_pos differ from the frozen card'


def test_symbol_order_is_pinned_oq3(on):
    e, _ = on
    sl = slot(e, F02); sl['symbols'] = list(reversed(sl['symbols']))           # same set, different order
    assert e.candidate_drift(sl) == ['symbols']
    _guard_once(e, F02)


def test_family_priority_order_is_pinned_oq2(on):
    e, _ = on
    sv = e.S['SLEEVES']; i, j = sv.index(slot(e, F01)), sv.index(slot(e, F02))
    sv[i], sv[j] = sv[j], sv[i]                                                # C-F02 before C-F01
    assert sorted(e.candidate_guard()) == [F01, F02]
    assert e.state['candidate_disabled'][F01]['why'].startswith('drift: family trend_long order')


def test_accounting_trigger_at_exactly_six_hours(on, monkeypatch):
    e, _ = on
    monkeypatch.setattr(E.AC, 'row_view', lambda row: dict(status=row['_st'], pnl=None))
    t0 = 1_800_000_000.0
    e.history.append(dict(sleeve=F01, symbol='ETHUSDT', closed=E.pd.Timestamp(t0, unit='s', tz='UTC').isoformat(), _st='pending'))
    assert e.candidate_guard(now=t0 + 6 * 3600 - 1) == []
    assert e.candidate_guard(now=t0 + 6 * 3600) == [F01, F02] and slot(e, F02)['enabled'] is False


def test_guard_error_fails_closed_for_the_whole_family(on, monkeypatch):
    e, _ = on
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    (l,) = lots(e, F01, 'BTCUSDT')
    monkeypatch.setattr(e, 'candidate_drift', lambda sl: (_ for _ in ()).throw(KeyError('x')))
    assert sorted(e.candidate_guard()) == [F01, F02]
    assert all(e.state['candidate_disabled'][s]['why'] == 'guard_error: KeyError' for s in (F01, F02))
    assert lots(e, F01, 'BTCUSDT') == [l] and l['stop_id'] in e.trade.stops
    monkeypatch.undo()
    cycle4(e, {f'{F01}|ETHUSDT': SG, f'{F02}|SOLUSDT': SG})
    assert [x['symbol'] for x in lots(e, F01)] == ['BTCUSDT'] and not lots(e, F02)
