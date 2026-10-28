"""AI-SCAN-03 writer-hook contract twin (Codex 6460083994): the JSON twin is authoritative, stays DRAFT/unsigned, forbids
order gating, model calls and live mode, and its join key / cost identity agree with the real ai_scan on master.

Contract only - no runtime code. Both twins live under contracts/ai_scan_03/ (never the documentation tree). Parity is
checked from the JSON side: every name the JSON lists must appear in the markdown.
"""
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import ai_scan as A                                                                         # noqa: E402
from ml_shadow.ledger import decision_id                                                    # noqa: E402

CONTRACT_DIR = os.path.join(ROOT, 'contracts', 'ai_scan_03')
JS = os.path.join(CONTRACT_DIR, 'ai_scan_03_contract.v1.json')
MD = os.path.join(CONTRACT_DIR, 'AI_SCAN_03_CONTRACT.md')

OPEN, CUT, DEC = '2026-10-20T12:00:00Z', '2026-10-20T12:15:00Z', '2026-10-20T12:15:10Z'
COSTS0 = dict(fees_actual=0.5, funding_actual=0.0, pnl_gross=12.5, pnl_fill_adj=0.0, risk_usd=10.0)
COSTS1 = dict(COSTS0, fees_actual=0.75, funding_actual=-2.75)
CMID = 'actual-cost-model-v1'


@pytest.fixture(scope='module')
def c():
    with open(JS, encoding='utf-8') as fh:
        return json.load(fh)


@pytest.fixture(scope='module')
def md():
    with open(MD, encoding='utf-8') as fh:
        return fh.read()


def _sig(status='taken'):
    snap = dict(last_close=CUT, available_at='2026-10-20T12:15:03Z', features=dict(rsi=55.0, atr_pct=0.8))
    at = lambda e, a: dict(event_at=e, available_at=a)                                    # noqa: E731
    inputs = dict(funding=at('2026-10-20T12:00:00Z', '2026-10-20T12:00:05Z'),
                  universe=at('2026-10-20T00:00:00Z', '2026-10-20T00:00:30Z'),
                  exchange_rules=at('2026-10-20T06:00:00Z', '2026-10-20T06:00:02Z'))
    return A.build_signal(sleeve='S1', strategy='trend_ema_mom', family='trend', symbol='btcusdt', side='LONG', tf='15m',
                          candle_open=OPEN, decided_at=DEC, rules_status=status, rules_reason='', snapshot=snap,
                          plan=dict(entry_lo=100.0, entry_hi=101.0, stop=98.0, targets=[104.0]), inputs=inputs,
                          costs=dict(fee_r=0.05))


def _out(s, costs, rev, closed='2026-10-20T14:00:00Z'):
    return A.build_outcome(s, source='measured', closed_at=closed, exit_reason='target', cost_basis='measured',
                           actual_costs=costs, cost_model_id=CMID, rev=rev)


# ------------------------------------------------------------------ status / shape
def test_json_parses_and_is_draft_unsigned(c, md):
    assert c['contract'] == 'AI-SCAN-03' and c['version'] == 1
    assert c['status'] == 'DRAFT' and c['signed_by'] is None and c['frozen_at'] is None
    assert c['assignment'] == '6460083994' and c['doc'] == os.path.basename(MD)
    assert '**Status: DRAFT — unsigned.**' in md


def test_required_sections_and_keys(c):
    for k in ('builds_on', 'slice', 'scope', 'hooks', 'decision_id', 'join', 'never_gates', 'paper_only', 'identity',
              'counters', 'failure_modes', 'test_plan', 'deferred', 'open_questions', 'rulings',
              'rotation_prerequisite', 'queue_gaps'):
        assert k in c, k
    assert {h['id'] for h in c['hooks']} == {'decision', 'shadow', 'measured_close', 'cost_restatement'}
    for h in c['hooks']:
        assert {'record_kind', 'call_site', 'fires', 'builder', 'receives', 'runtime_status'} <= set(h)
        assert h['runtime_status'] == 'DEFERRED'
    assert {h['record_kind'] for h in c['hooks']} == {'signal', 'shadow', 'outcome'}
    for k in ('mechanism', 'queue', 'writer_thread', 'exceptions', 'proof_test'):
        assert k in c['never_gates']


def test_contract_only_slice_changes_no_runtime_file(c):
    s = c['slice']
    assert s['kind'] == 'contract_only' and s['runtime_code_changed'] is False
    assert all(f.startswith(('contracts/ai_scan_03/', 'tests/')) for f in s['files'])
    assert {'ai_scan.py', 'ai_scan_ledger.py', 'engine.py', 'app.py'} <= set(s['untouched_runtime_files'])
    assert c['builds_on']['AI-SCAN-02']['edited_here'] is False
    assert c['builds_on']['ML-PAIR-01']['imported_here'] is False
    assert c['join']['ml_pair_01_consistency']['import_from_unmerged_branch'] is False


# ------------------------------------------------------------------ forbids gating, model calls, live mode
def test_forbids_order_gating_model_calls_and_live(c):
    s = c['scope']
    assert s['flag'] == A.FLAG and s['default_on'] is False and s['paper_only'] is True
    assert s['live_behavior'] == 'refused' and s['authority'] == 'none'
    for k in ('order_gating', 'trading_reads_output', 'model_calls', 'network_calls', 'settings_writes',
              'engine_state_writes', 'holds_engine_lock'):
        assert s[k] is False, k
    p = c['paper_only']
    assert p['default'] is False and p['model_or_api_call_in_slice'] is False and p['authority'] == 'none'
    assert 'refused' in p['live_check']
    q = c['never_gates']['queue']
    assert q['bounded'] is True and q['offer_blocks'] is False and q['offer_raises'] is False
    w = c['never_gates']['writer_thread']
    assert w['holds_engine_lock'] is False and w['reads_engine_state'] is False
    assert c['never_gates']['proof_test'] in {t['name'] for t in c['test_plan']}


def test_deferred_list(c):
    assert set(c['deferred']) >= {'runtime_producer_hook', 'remote_model_api_call', 'model_promotion', 'ui_controls',
                                  'trading_authority'}
    assert 'ledger_rotation' not in c['deferred']                         # R6: a prerequisite, not deferred


# ------------------------------------------------------------------ key / ordering vs the real ai_scan
def test_outcome_key_fields_match_ai_scan(c):
    s = _sig()
    o = _out(s, COSTS0, 0)
    for kind, rec in (('signal', s), ('outcome', o)):
        fields = c['join']['record_key'][kind]
        mine = tuple(rec[f] for f in fields)
        if hasattr(A, 'record_key'):                     # #222 adds ai_scan.record_key; master keys inline in fold()
            assert mine == A.record_key(rec)
        else:
            assert mine[0] == kind and mine[1] == s['signal_id']
    assert c['join']['order_field'] == 'rev' and c['join']['order_field_in_key'] is False
    assert 'rev' not in c['join']['record_key']['outcome']
    assert c['join']['economic_key'] == ['signal_id', 'cost_revision_id']


def test_fold_semantics_match_contract(c):
    """Behavioural pin on master's fold(): rev is not in the key, a new revision appends, a duplicate is a no-op, a
    conflicting replay and a shared rev are refused."""
    j = c['join']
    s = _sig()
    o0, o1 = _out(s, COSTS0, 0), _out(s, COSTS1, 1)
    v = A.fold([s, o0, o1, o0])[s['signal_id']]                         # append + exact duplicate no-op
    assert j['new_revision'] == 'append' and j['exact_duplicate'] == 'no_op'
    assert set(v['revisions']) == {o0['cost_revision_id'], o1['cost_revision_id']} and v['measured']['rev'] == 1
    assert j['conflicting_replay'] == 'refused_unwritten'
    with pytest.raises(A.AIScanError, match='conflicting replay'):     # same key (cost id), different rev -> rev not in key
        A.fold([s, o0, _out(s, COSTS0, 5)])
    assert j['same_rev_different_cost'] == 'refused_unwritten'
    with pytest.raises(A.AIScanError, match='share rev'):
        A.fold([s, o0, _out(s, COSTS1, 0)])
    assert j['outcome_before_signal'] == 'refused_unwritten'
    with pytest.raises(A.AIScanError, match='joins no recorded signal'):
        A.fold([o0])


def test_measured_only_for_taken_and_measured_basis(c):
    j = c['join']
    assert j['measured_requires_rules_status'] == 'taken' and j['eligible_close_status'] == ['measured']
    assert set(j['never_measured_outcome']) == {'booked', 'acknowledged', 'pending', 'incomplete'}
    assert j['hook_net_basis'] == 'measured' and j['hook_net_basis'] in A.COST_BASES['measured']
    for st in ('skipped', 'pending'):
        with pytest.raises(A.AIScanError, match='taken only'):
            _out(_sig(st), COSTS0, 0)
    with pytest.raises(A.AIScanError):                                  # incomplete cost: no identity, no outcome
        _out(_sig(), dict(COSTS0, fees_actual=None), 0)


def test_cost_identity_matches_ai_scan(c):
    cr = c['join']['cost_revision']
    assert cr['schema'] == A.COST_REV_SCHEMA and tuple(cr['fields']) == A.COST_REV_FIELDS
    net, r = A.measured_net(COSTS1)
    k = COSTS1
    assert net == k['pnl_gross'] + k['pnl_fill_adj'] - k['fees_actual'] + k['funding_actual'] and r == net / k['risk_usd']
    assert cr['net_formula'] == 'pnl_gross + pnl_fill_adj - fees_actual + funding_actual'
    p = c['join']['ml_pair_01_consistency']
    assert p['pair_key'] == ['decision_id', 'cost_rev_id'] and p['maps_to'] == c['join']['economic_key']


def test_signal_id_is_the_ml_decision_id(c):
    d = c['decision_id']
    assert d['function'] == 'ml_shadow.ledger.decision_id'
    assert d['fields'] == ['sleeve', 'strategy', 'symbol', 'side', 'tf', 'candle_open']
    assert _sig()['signal_id'] == decision_id('S1', 'trend_ema_mom', 'btcusdt', 'LONG', '15m', OPEN)


def test_hook_call_sites_exist_in_engine(c):
    with open(os.path.join(ROOT, 'engine.py'), encoding='utf-8') as fh:
        src = fh.read()
    for h in c['hooks']:
        cs = h['call_site']
        if cs.startswith('engine.Engine.'):
            assert re.search(rf'\n    def {cs.rsplit(".", 1)[1]}\(', src), cs
        else:
            assert cs.split()[0] == 'ai_scan.ScanQueue' and hasattr(A, 'ScanQueue')
    for h in c['hooks']:
        fn = h['builder'].split('(')[0].split(' | ')[0]
        assert hasattr(A, fn.split('.', 1)[1]), fn


# ------------------------------------------------------------------ identity, counters, test plan
def test_identity_gate(c):
    i = c['identity']
    assert i['fields'] == ['build_id', 'source_commit', 'bundle_hash']
    assert i['reasons'] == ['dev_build', 'source_commit', 'bundle_hash']
    assert 'refused' in i['incomplete']['outcome'] and 'identity_incomplete' in i['incomplete']['outcome']
    assert 'never eligible' in i['incomplete']['signal']
    assert 'identity_incomplete' in c['counters']


def test_failure_modes_use_declared_counters(c):
    ctr = c['counters']
    assert len(ctr) == len(set(ctr))
    for fm in c['failure_modes']:
        assert fm['counter'] is None or fm['counter'] in ctr, fm


def test_test_plan_names(c):
    names = [t['name'] for t in c['test_plan']]
    assert len(names) == len(set(names)) and all(n.startswith('test_') and t['asserts'] for n, t in
                                                 zip(names, c['test_plan']))
    for need in ('test_trading_byte_identical_with_ai_scan_on_off_and_failing_writer', 'test_live_mode_refused_before_build',
                 'test_no_model_or_network_call', 'test_restatement_appends_new_revision_never_rewrites',
                 'test_booked_acknowledged_pending_never_measured', 'test_incomplete_identity_refuses_outcome_flags_signal'):
        assert need in names


# ------------------------------------------------------------------ MD parity, from the JSON side
def test_md_names_everything_the_json_lists(c, md):
    for h in c['hooks']:
        assert f"`{h['id']}`" in md and f"`{h['call_site']}`" in md
    for k in c['counters'] + c['deferred'] + [t['name'] for t in c['test_plan']]:
        assert f'`{k}`' in md, k
    for f in c['join']['cost_revision']['fields']:
        assert f in md
    assert f"`{c['join']['cost_revision']['net_formula']}`" in md and c['join']['cost_revision']['schema'] in md
    assert '**default OFF**' in md and '**PAPER only**' in md and '**No model or API call' in md
    assert c['open_questions'] == [] and 'open questions: none' in md


# ------------------------------------------------------------------ Codex 6460621068 rulings R1-R8
def test_rulings_r1_to_r8_are_applied(c, md):
    r = c['rulings']
    assert r['source'].startswith('Codex 6460621068') and sum(k.startswith('R') for k in r) == 8
    assert 'neither feature is a prerequisite' in c['decision_id']['flag_independence']                     # R1
    assert 'ML_SHADOW or AI_SCAN is exactly True' in c['decision_id']['on_lot']
    assert c['join']['hook_net_basis'] == 'measured' and 'stays compatible' in c['join']['runtime_net_basis_rule']  # R2
    rs = c['join']['ml_pair_01_consistency']['restatements']                                                 # R3
    assert 'every measured' in rs['ai_scan'] and 'first measured close only' in rs['ml_pair_01']
    assert 'never silently multiplied' in rs['before_training']
    assert 'ai-scan-schema-v2' in c['identity']['schema_bump'] and 'every record' in c['identity']['per_record']  # R4
    w = c['never_gates']['writer_thread']                                                                    # R5
    assert w['dedicated'] is True and w['coupled_to_ml_shadow'] is False and w['single_writer_per_ledger'] is True
    rp = c['rotation_prerequisite']                                                                          # R6
    assert 'may not be enabled' in rp['rule'] and 'producer_enable_refused_without_rotation' in rp['requires']
    g = c['queue_gaps']                                                                                      # R7
    assert g['durable'] is True and 'ineligible' in g['window_rule'] and 'gaps' in c['counters']
    assert any(fm['counter'] == 'gaps' for fm in c['failure_modes'])
    for name in ('test_flags_independent_either_flag_stamps_decision_id', 'test_writer_thread_isolated_from_ml_shadow',
                 'test_every_record_carries_code_identity_schema_v2', 'test_rotation_segments_manifest_and_replay',
                 'test_producer_enable_refused_without_rotation', 'test_queue_drop_opens_durable_gap_and_window_ineligible'):
        assert name in {t['name'] for t in c['test_plan']}
    for pr in ('AI-SCAN-02', 'ML-PAIR-01'):                                                                  # R8
        b = c['builds_on'][pr]
        assert 'head' not in b and 'accepted_merge_sha' in b and 'superseded review head' in b['refresh_rule']
    assert '**accepted merge SHAs**' in md
