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


# ------------------------------------------------------------------ Codex 6461183917 rulings F1-F5
def _v2_fold(c, outcomes):
    """Reference v2 semantics from the contract (no runtime code): key = record_key_v2.outcome_measured."""
    key, seen, latest = c['join']['record_key_v2']['outcome_measured'], {}, None
    for o in outcomes:
        k = tuple(o[f] for f in key)
        if k in seen:
            if seen[k] != o['content']: raise ValueError('conflicting replay')
            continue
        seen[k] = o['content']
        latest = o if latest is None or o['rev'] > latest['rev'] else latest
    return seen, latest


def test_rulings_f1_to_f5_are_applied(c, md):
    j = c['join']
    v1, v2 = j['record_key']['outcome'], j['record_key_v2']['outcome_measured']
    assert v2 == v1 + ['rev'] and j['order_field_in_key_v2'] is True                                       # F1
    assert j['economic_key'] == ['signal_id', 'cost_revision_id'] and 'rev 2' in j['revert']
    base = dict(kind='outcome', signal_id='s' * 64, source='measured', cost_model_hash='m' * 64)
    A_, B_ = dict(base, cost_revision_id='a' * 64), dict(base, cost_revision_id='b' * 64)
    seen, latest = _v2_fold(c, [dict(A_, rev=0, content=1), dict(B_, rev=1, content=2), dict(A_, rev=2, content=1)])
    assert len(seen) == 3 and latest['rev'] == 2 and latest['cost_revision_id'] == 'a' * 64
    with pytest.raises(ValueError, match='conflicting replay'):
        _v2_fold(c, [dict(A_, rev=0, content=1), dict(A_, rev=0, content=9)])
    i = c['identity']                                                                                        # F2
    assert 'excluding only code and record_hash' in i['content_hash'] and 'no-op' in i['cross_build_replay']
    assert 'need not match' in i['fit_identity'] and 'provenance' in i['fit_identity']
    g = c['queue_gaps']                                                                                      # F3
    assert g['event_at'] == {'signal': 'decided_at', 'shadow': 'created_at', 'outcome': 'closed_at'}
    assert 'inclusive UTC' in g['bounds'] and g['merge_across_writer_ids'] is False
    assert 'seq' in g['writer_session'] and 'clean-close' in g['writer_session'] and 'never trading' in g['flush_failure']
    rp = c['rotation_prerequisite']                                                                          # F4
    assert 'every hook call' in rp['enable_check'] and 'disabled' in rp['on_mismatch']
    assert 'never a partial plausible row' in rp['read_path']
    f = c['fitting']                                                                                         # F5
    assert 'frozen' in f['before_policy_frozen'] and 'exactly one latest valid' in f['after']
    for k in ('replays_other_build', 'gap_flush_failed', 'producer_disabled'):
        assert k in c['counters'] and any(fm['counter'] == k for fm in c['failure_modes'])
    names = {t['name'] for t in c['test_plan']}
    for n in ('test_v2_restatement_revert_appends_and_latest_is_rev2', 'test_other_build_replay_is_noop_not_conflict',
              'test_drop_crash_restart_window_ineligible', 'test_manifest_mismatch_disables_producer',
              'test_api_view_bounded_and_causally_complete', 'test_one_trade_one_training_row'):
        assert n in names
    assert c['rulings_f']['source'].startswith('Codex 6461183917') and c['status'] == 'DRAFT'
    # R8: builds_on pins the accepted merge SHAs, never a review head
    assert c['builds_on']['AI-SCAN-02']['accepted_merge_sha'] == 'd3740f1fea518e00a640ba05a6dd3f4f3b0c3983'
    assert c['builds_on']['ML-PAIR-01']['accepted_merge_sha'] == 'db85b75aba19c3f037c55eb77d97d72152f41d96'
    assert all(c['builds_on'][p]['state'] == 'merged' for p in ('AI-SCAN-02', 'ML-PAIR-01'))
    assert '`(kind, signal_id, source, cost_revision_id, cost_model_hash, rev)`' in md


# ------------------------------------------------------------------ Codex 6462174084 rulings G1-G6 (reference models)
def _eligible(window, signals, gaps, sessions):
    """Reference G1/G2 model: a signal is eligible when its decided_at is covered by a clean enabled session, it is
    named in no gap, and its decided_at is inside no time gap. Gaps: {'from', 'to', 'signals'}."""
    lo, hi = window
    out = []
    spans = _hb_spans([s['heartbeats'] for s in sessions if s['clean'] and s['enabled']])   # C1 x C2: heartbeats only
    whole = _covered(window, spans)                         # C2: the whole [start, end) window, never the point alone
    for sid, t in signals.items():
        if not lo <= t < hi:                                # half-open [start, end) (Codex 6462613229 #4)
            continue
        covered = whole
        named = any(sid in g.get('signals', ()) for g in gaps)
        timed = any(g['from'] <= t <= g['to'] for g in gaps)
        out.append((sid, covered and not named and not timed))
    return dict(out)


def _S(start, close, clean=True, enabled=True, hole=None):
    """A session fixture whose heartbeats run every 60 s from start to close; hole=(a, b) drops beats strictly inside."""
    beats = list(range(start, close, 60)) + [close]
    if hole:
        beats = [b for b in beats if not hole[0] < b < hole[1]]
    return {'start': start, 'close': close, 'clean': clean, 'enabled': enabled, 'heartbeats': beats}


def _coverage_gaps(sessions):
    """G2: every interval between one session's clean close and the next durable start is a gap; a disabled or unclean
    session's whole span is a gap."""
    gaps, ss = [], sorted(sessions, key=lambda s: s['start'])
    for a, b in zip(ss, ss[1:]):
        if b['start'] > a['close']:
            gaps.append({'from': a['close'], 'to': b['start']})
    gaps += [{'from': s['start'], 'to': s['close']} for s in ss if not (s['clean'] and s['enabled'])]
    return gaps


class _Writer:
    """G3 model: seq allocated at the enqueue attempt; a drop records its seq and owning context; no clean close while a
    gap is pending or the queue is not drained."""
    def __init__(self, cap):
        self.seq, self.cap, self.q, self.pending = 0, cap, [], []

    def offer(self, rec):
        self.seq += 1
        if len(self.q) >= self.cap:
            self.pending.append({'seq': self.seq, 'signal_id': rec['signal_id'], 'decided_at': rec['decided_at']})
            return False
        self.q.append(dict(rec, seq=self.seq))
        return True

    def clean_close(self):
        if self.q or self.pending:
            raise RuntimeError('clean close forbidden: queue not drained or gap pending')
        return 'closed'


def _select(revs):
    """G5: highest rev first; an invalid latest excludes the signal, no fallback."""
    if not revs:
        return None
    top = max(revs, key=lambda r: r['rev'])
    return top if top['valid'] else None


def _apply_upgrade(orig, up):
    """G6: an equal payload with a complete identity may upgrade; a mismatch is a conflict; the original is untouched."""
    if up['payload'] != orig['payload']:
        raise ValueError('conflict')
    if not up['code_complete']:
        return None
    return {'kind': 'identity_upgrade', 'ref': orig['record_hash'], 'payload': up['payload']}


def test_rulings_g1_to_g6_are_applied(c, md):
    g, rp, f, i = c['queue_gaps'], c['rotation_prerequisite'], c['fitting'], c['identity']
    assert 'owning signal decided_at' in g['window_axis'] and 'signal_id and the owning decided_at' in g['outcome_envelope']
    assert 'conservatively incomplete' in g['unprovable_ownership']
    assert 'clean close to the next durable start' in g['coverage'] and 'not counters only' in g['coverage']
    assert 'enqueue attempt, before the non-blocking offer' in g['sequence'] and 'drained' in g['clean_close']
    assert 'disables the session' in g['start_marker']
    assert 'no disk I/O' in rp['hot_path'] and 'positive integer' in rp['segment_bound'] and 'never a truthy flag' in rp['segment_bound']
    assert 'never fall back' in f['latest_rule'] and 'identity_upgrade record' in i['identity_upgrade']
    for k in ('identity_upgrades', 'coverage_gaps'):
        assert k in c['counters'] and any(fm['counter'] == k for fm in c['failure_modes'])
    names = {t['name'] for t in c['test_plan']}
    for n in ('test_dropped_outcome_makes_owning_signal_ineligible_by_decided_at', 'test_uncovered_and_disabled_intervals_are_gaps',
              'test_sequence_allocated_at_enqueue_and_drop_records_it', 'test_clean_close_forbidden_with_pending_gap',
              'test_hook_reads_cached_generation_only', 'test_invalid_latest_revision_excludes_signal_no_fallback',
              'test_identity_upgrade_record_and_conflict'):
        assert n in names
    assert 'no fallback' in next(t['asserts'] for t in c['test_plan'] if t['name'] == 'test_one_trade_one_training_row')
    assert c['rulings_g']['source'].startswith('Codex 6462174084') and c['status'] == 'DRAFT'
    assert '**Window axis (G1):**' in md and '**Identity upgrade (G6):**' in md


def test_g1_dropped_outcome_closing_outside_window_still_excludes_its_signal():
    sess = [_S(0, 100)]
    w = _Writer(cap=0)                                                      # every offer drops
    w.offer({'signal_id': 's1', 'decided_at': 10, 'closed_at': 250})        # outcome closes far outside the window
    gaps = [{'from': p['decided_at'], 'to': p['decided_at'], 'signals': {p['signal_id']}} for p in w.pending]
    v = _eligible((0, 50), {'s1': 10, 's2': 20}, gaps, sess)
    assert v == {'s1': False, 's2': True}                                   # Cowork's 12:30-close probe now excluded


def test_g2_clean_close_to_next_start_and_disabled_session_are_gaps():
    sess = [_S(0, 110), _S(130, 200), _S(210, 260, enabled=False)]           # the last one is producer_disabled
    gaps = _coverage_gaps(sess)
    assert {'from': 110, 'to': 130} in gaps and {'from': 210, 'to': 260} in gaps
    sig = {'a': 50, 'b': 120, 'c': 150, 'd': 220}
    assert _eligible((0, 300), sig, gaps, sess) == {'a': False, 'b': False, 'c': False, 'd': False}   # crosses gaps
    assert _eligible((0, 100), sig, gaps, sess) == {'a': True}
    assert _eligible((130, 200), sig, gaps, sess) == {'c': True}
    assert _eligible((140, 190), {}, gaps, sess) == {} and _covered((140, 190), [(130, 200)])   # zero events, covered


def test_g3_seq_at_enqueue_drop_records_seq_and_close_refused_while_pending():
    w = _Writer(cap=1)
    assert w.offer({'signal_id': 's1', 'decided_at': 1}) is True
    assert w.offer({'signal_id': 's2', 'decided_at': 2}) is False
    assert [r['seq'] for r in w.q] == [1] and w.pending == [{'seq': 2, 'signal_id': 's2', 'decided_at': 2}]
    with pytest.raises(RuntimeError, match='clean close forbidden'):
        w.clean_close()
    w.q.clear(); w.pending.clear()                                          # drained and the gap flushed
    assert w.clean_close() == 'closed'


def test_g5_invalid_latest_excludes_without_fallback_and_g6_upgrade_rules():
    assert _select([{'rev': 0, 'valid': True}, {'rev': 1, 'valid': True}])['rev'] == 1
    assert _select([{'rev': 0, 'valid': True}, {'rev': 1, 'valid': False}]) is None
    orig = {'record_hash': 'h' * 64, 'payload': {'net': 1.0}, 'code_complete': False}
    up = _apply_upgrade(orig, {'payload': {'net': 1.0}, 'code_complete': True})
    assert up == {'kind': 'identity_upgrade', 'ref': 'h' * 64, 'payload': {'net': 1.0}} and orig['code_complete'] is False
    with pytest.raises(ValueError, match='conflict'):
        _apply_upgrade(orig, {'payload': {'net': 2.0}, 'code_complete': True})


# ------------------------------------------------------------------ Codex 6462613229 #1-#5 (stall-takeover review)
def _covered(window, spans):
    """H3: is [lo, hi) continuously covered by spans [a, b)? Spans: clean enabled sessions, or an open session up to its
    last durable heartbeat."""
    lo, hi = window
    at = lo
    for a, b in sorted(spans):
        if a > at:
            return False
        at = max(at, b)
        if at >= hi:
            return True
    return at >= hi


def _uch(r):
    """U1: upgrade_content_hash over (orig_record_hash, original content_hash, complete code) - no transport fields."""
    return (r['orig_record_hash'], r['content_hash'], r.get('code', 'C'))


def _upgrade_fold(records, page=None):
    """H2 + U1/U2: key (kind, orig_record_hash); same upgrade_content_hash is a no-op across sessions, a different one is a
    conflict that poisons the signal (never raises); an original outside the bounded page is unresolved_original.
    Returns (applied, invalid, poisoned, unresolved)."""
    orig = {r['record_hash']: r for r in records if r['kind'] != 'identity_upgrade'}
    seen, applied, invalid, poisoned, unresolved = {}, {}, 0, set(), set()
    for r in records:
        if r['kind'] != 'identity_upgrade':
            continue
        k = (r['kind'], r['orig_record_hash'])
        if k in seen:
            if seen[k] != _uch(r):
                poisoned.add(r['orig_record_hash'])
                applied.pop(r['orig_record_hash'], None)
            continue
        seen[k] = _uch(r)
        o = orig.get(r['orig_record_hash'])
        if o is None and page is not None and r['orig_record_hash'] not in page:
            unresolved.add(r['orig_record_hash'])
        elif o and o['content_hash'] == r['content_hash'] and not o['code_complete'] and r['code_complete']:
            applied[o['record_hash']] = r['record_hash']
        else:
            invalid += 1
    for h in poisoned:
        applied.pop(h, None)
    return applied, invalid, poisoned, unresolved


def _segment_ok(c, nbytes, nseg):
    lim = c['rotation_prerequisite']['limits']
    return (isinstance(nbytes, int) and not isinstance(nbytes, bool) and lim['min_segment_bytes'] <= nbytes <= lim['max_segment_bytes']
            and isinstance(nseg, int) and 0 < nseg <= lim['max_segments_per_read'])


def test_h1_close_hooks_receive_decided_at_from_the_lot(c):
    for h in c['hooks']:
        if h['id'] in ('measured_close', 'cost_restatement'):
            assert 'decided_at' in h['receives'] and 'decision_id' in h['receives']
    src = c['decision_id']['decided_at_source']
    assert "lot['ml_decided_at']" in src and 'never from disk on the trading thread' in src and 'no_decided_at' in src
    assert 'no_decided_at' in c['counters']


def test_h2_identity_upgrade_schema_replay_conflict_and_fold(c):
    u = c['identity']['identity_upgrade_record']
    assert u['kind'] == 'identity_upgrade' and u['key'] == ['kind', 'orig_record_hash']
    assert {'orig_record_hash', 'content_hash', 'code', 'record_hash'} <= set(u['fields'])
    o = {'kind': 'outcome', 'record_hash': 'o1', 'content_hash': 'c1', 'code_complete': False}
    up = {'kind': 'identity_upgrade', 'orig_record_hash': 'o1', 'record_hash': 'u1', 'content_hash': 'c1', 'code_complete': True}
    assert _upgrade_fold([o, up, up]) == ({'o1': 'u1'}, 0, set(), set())                  # exact replay: no-op
    assert _upgrade_fold([o, dict(up, content_hash='cX')]) == ({}, 1, set(), set())        # payload mismatch: not applied
    assert _upgrade_fold([dict(o, code_complete=True), up]) == ({}, 1, set(), set())       # nothing to upgrade
    assert _upgrade_fold([up]) == ({}, 1, set(), set())                                    # proven absent: invalid


def test_h3_window_needs_continuous_coverage_including_endcaps(c):
    g = c['queue_gaps']
    assert 'before the first durable start' in g['endcaps'] and 'heartbeat' in g['endcaps']
    assert 'any time not inside a covered span is a gap' in g['inferred_gaps']
    spans = [(100, 200), (200, 260)]                                                         # clean session, then an open one to its last heartbeat
    assert _covered((120, 250), spans) is True
    assert _covered((50, 150), spans) is False                                               # before the first start
    assert _covered((150, 300), spans) is False                                              # after the last heartbeat
    assert _covered((150, 250), [(100, 180), (190, 260)]) is False                           # a between-session hole


def test_h4_window_boundary_is_half_open(c):
    assert '[start, end)' in c['queue_gaps']['window_interval']
    sess = [_S(0, 300)]
    a = _eligible((0, 100), {'s': 100}, [], sess)
    b = _eligible((100, 200), {'s': 100}, [], sess)
    assert a == {} and b == {'s': True}                                                      # exactly one membership


def test_h5_segment_bound_numeric_limits(c):
    lim = c['rotation_prerequisite']['limits']
    assert (lim['min_segment_bytes'], lim['max_segment_bytes'], lim['max_segments_per_read'], lim['max_manifest_segments']) == \
        (1 << 20, 16 << 20, 4, 4096)
    assert _segment_ok(c, 8 << 20, 4) and not _segment_ok(c, (16 << 20) + 1, 1) and not _segment_ok(c, 1 << 19, 1)
    assert not _segment_ok(c, True, 1) and not _segment_ok(c, 8 << 20, 5) and not _segment_ok(c, '8MiB', 1)
    assert c['rulings_h']['source'].startswith('Codex 6462613229')


# ------------------------------------------------------------------ Codex 6462855816 U1/U2/C1/C2/D1/L1
import re as _re

_ISO6 = _re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z')


def _hb_spans(sessions, max_gap=75):
    """C1: half-open spans between consecutive heartbeats with gap <= max_gap; a non-increasing step ends that session's
    trustworthy coverage (a new durable session must start)."""
    spans = []
    for hbs in sessions:
        for a, b in zip(hbs, hbs[1:]):
            if b <= a:
                break
            if b - a <= max_gap:
                spans.append((a, b))
    return spans


def _paginate(pages, expect, max_segments=4):
    """L1 + Codex 6463297130 #3: each page <= max_segments, chained by prev-hash, AND the walk must reach the bound
    terminal: expected page count and final end hash from the verified manifest. A truncated prefix is incomplete."""
    prev = None
    for p in pages:
        if len(p['segments']) > max_segments or p['prev'] != prev:
            return 'incomplete'
        prev = p['hash']
    if len(pages) != expect['pages'] or prev != expect['end_hash']:
        return 'incomplete'
    return 'complete'


def test_rulings_u_c_d_l_are_applied(c, md):
    u = c['identity']['identity_upgrade_record']
    assert 'excluding the transport fields' in u['upgrade_content_hash'] and 'no-op across sessions' in u['exact_replay']
    assert 'unresolved_original' in u['fold'] and 'never raises' in c['join']['read_side_conflict']
    g = c['queue_gaps']
    assert 'at most 75 s' in g['heartbeat'] and 'backward' in g['heartbeat'] and '_covered([start, end))' in g['eligibility']
    assert 'only that signal / outcome' in c['decision_id']['decided_at_source'] and 'stays clean' in c['decision_id']['decided_at_source']
    assert 'six-digit microseconds' in c['decision_id']['decided_at_form']
    rp = c['rotation_prerequisite']
    assert 'manifest_full' in rp['manifest_full'] and 'hash-chain / cursor continuity' in rp['pagination'] and 'measure' in rp['measurement']
    for k in ('unresolved_original', 'decided_at_mismatch', 'manifest_full'):
        assert k in c['counters'] and any(fm['counter'] == k for fm in c['failure_modes'])
    names = {t['name'] for t in c['test_plan']}
    assert all(n in names for n in ('test_upgrade_replay_with_changed_transport_is_noop', 'test_heartbeat_hole_over_75s_and_backward_step',
                                     'test_whole_window_eligibility_and_t_equals_close', 'test_manifest_full_disables_before_append_and_pagination_continuity'))
    assert c['rulings_u']['source'].startswith('Codex 6462855816') and '**Heartbeats (C1):**' in md


def test_u1_replay_with_changed_transport_is_noop_and_u2_conflict_poisons():
    o = {'kind': 'outcome', 'record_hash': 'o1', 'content_hash': 'c1', 'code_complete': False}
    up = {'kind': 'identity_upgrade', 'orig_record_hash': 'o1', 'record_hash': 'u1', 'content_hash': 'c1',
          'code_complete': True, 'code': 'C1', 'writer_id': 'w1', 'seq': 7, 'event_at': 100}
    replay = dict(up, record_hash='u9', writer_id='w2', seq=1, event_at=900)                # transport only
    assert _upgrade_fold([o, up, replay]) == ({'o1': 'u1'}, 0, set(), set())
    other_code = dict(up, record_hash='u2', code='C2')
    applied, _, poisoned, _ = _upgrade_fold([o, up, other_code])                            # never raises
    assert applied == {} and poisoned == {'o1'}


def test_u2_original_outside_page_is_unresolved_not_invalid():
    up = {'kind': 'identity_upgrade', 'orig_record_hash': 'oX', 'record_hash': 'u1', 'content_hash': 'c1', 'code_complete': True}
    assert _upgrade_fold([up], page={'o1'}) == ({}, 0, set(), {'oX'})


def test_c1_heartbeat_hole_and_backward_step():
    sess = [[0, 60, 120, 720, 780]]                                                          # a 10-minute stall
    spans = _hb_spans(sess)
    assert _covered((0, 120), spans) and not _covered((100, 760), spans)
    assert _hb_spans([[0, 60, 50, 110]]) == [(0, 60)]                                        # backward step ends coverage
    assert _hb_spans([[0, 76]]) == [] and _hb_spans([[0, 75]]) == [(0, 75)]                 # 75 s tolerance edge


def test_c2_whole_window_and_t_equals_close():
    sess = [_S(0, 100)]
    assert _eligible((50, 150), {'s': 60}, [], sess) == {'s': False}                         # signal covered, window not
    assert _eligible((0, 100), {'s': 60}, [], sess) == {'s': True}
    assert not _covered((100, 101), [(0, 100)])                                              # t == close is outside [0, 100)


def test_d1_decided_at_canonical_form_and_mismatch():
    assert _ISO6.fullmatch('2026-10-28T08:00:00.000000Z')
    for bad in ('2026-10-28T08:00:00Z', '2026-10-28T08:00:00.000Z', '2026-10-28 08:00:00.000000Z', '2026-10-28T08:00:00.000000+00:00'):
        assert not _ISO6.fullmatch(bad)
    sig = {'signal_id': 's', 'decided_at': '2026-10-28T08:00:00.000000Z'}
    out = {'signal_id': 's', 'decided_at': '2026-10-28T08:00:00.000001Z'}
    assert sig['decided_at'] != out['decided_at']                                            # fold counts decided_at_mismatch


def test_l1_pagination_continuity():
    pages = [{'segments': [1, 2, 3, 4], 'prev': None, 'hash': 'h1'}, {'segments': [5, 6], 'prev': 'h1', 'hash': 'h2'}]
    exp = {'pages': 2, 'end_hash': 'h2'}
    assert _paginate(pages, exp) == 'complete'
    assert _paginate([pages[0], dict(pages[1], prev='hX')], exp) == 'incomplete'            # changed page
    assert _paginate([{'segments': [1, 2, 3, 4, 5], 'prev': None, 'hash': 'h'}], {'pages': 1, 'end_hash': 'h'}) == 'incomplete'


def test_pagination_must_reach_the_bound_terminal(c):
    assert 'terminal cursor' in c['rotation_prerequisite']['pagination_terminal']
    p1 = {'segments': [1, 2, 3, 4], 'prev': None, 'hash': 'h1'}
    p2 = {'segments': [5, 6, 7, 8], 'prev': 'h1', 'hash': 'h2'}
    p3 = {'segments': [9], 'prev': 'h2', 'hash': 'h3'}
    exp = {'pages': 3, 'end_hash': 'h3'}
    assert _paginate([p1, p2, p3], exp) == 'complete'
    assert _paginate([p1, p2], exp) == 'incomplete'                                          # valid prefix, final page missing
    assert _paginate([p1, p3], exp) == 'incomplete'                                          # missing middle page
    assert _paginate([p1], exp) == 'incomplete'



def test_clean_close_session_with_heartbeat_hole_fails_whole_window():
    """Codex 6463297130 #2: C1 composed with C2 - a clean, enabled session whose heartbeats stall for 10 minutes."""
    sess = [_S(0, 1800, hole=(600, 1260))]                                                   # beats 600 ... 1260 gap = 660 s
    assert _eligible((0, 1800), {'s': 100}, [], sess) == {'s': False}
    assert _eligible((0, 600), {'s': 100}, [], sess) == {'s': True}                          # before the hole
    assert _eligible((1260, 1800), {'s': 1300}, [], sess) == {'s': True}                     # after it
    assert _eligible((0, 1800), {'s': 100}, [], [_S(0, 1800)]) == {'s': True}               # no hole: eligible


def test_test_plan_has_no_session_scoped_decided_at_clause(c):
    for t in c['test_plan']:
        assert 'missing decided_at makes the writer session' not in t['asserts'], t['name']
