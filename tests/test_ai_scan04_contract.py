"""AI-SCAN-04 producer-wiring contract twin (Codex 6468672436): the JSON twin is authoritative, stays DRAFT/unsigned,
grants no producer / network / PAPER / trading authority, and carries concrete numeric bounds for the three pre-producer
requirements: (a) bounded periodic integrity refresh, (b) active-segment exposure + fail-closed recovery, (c)
process-private pagination pages. Overlapping limits must equal the ai_scan_ledger constants on master.
Integration review 6480873345: R1 durable active checkpoint; Q1/Q2/Q3 resolved; Q3 numbers frozen (16 segments/tick).
Integration ruling 6483289354: C1 two-phase intent/commit checkpoint, C2 sealed exposure, C3 startup I/O cap, C4
checkpoint persistence failure is fail-closed.
Integration ruling 6483743382: E1 seal/rotation crash protocol, E2 orphan checkpoint temp files never authoritative,
E3 one row per transaction (frozen, no batching), E4 partial append / ENOSPC confirmed fail-closed.

Contract only - no runtime code. Parity is checked from the JSON side: every name the JSON lists must appear in the md.
"""
import json
import math
import os
import re
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())    # never the real %LOCALAPPDATA%\ZackBot (import is pure)
import ai_scan as A                                          # noqa: E402
import ai_scan_ledger as L                                   # noqa: E402

CONTRACT_DIR = os.path.join(ROOT, 'contracts', 'ai_scan_04')
JS = os.path.join(CONTRACT_DIR, 'ai_scan_04_contract.v1.json')
MD = os.path.join(CONTRACT_DIR, 'AI_SCAN_04_CONTRACT.md')
JS03 = os.path.join(ROOT, 'contracts', 'ai_scan_03', 'ai_scan_03_contract.v1.json')
MIB = 1 << 20


@pytest.fixture(scope='module')
def c():
    with open(JS, encoding='utf-8') as fh:
        return json.load(fh)


@pytest.fixture(scope='module')
def md():
    with open(MD, encoding='utf-8') as fh:
        return fh.read()


def _posint(v):
    return type(v) is int and v > 0                          # never a bool, str or float


# ------------------------------------------------------------------ status / shape
def test_json_parses_and_is_draft_unsigned(c, md):
    assert c['contract'] == 'AI-SCAN-04' and c['version'] == 1
    assert c['status'] == 'DRAFT' and c['signed_by'] is None and c['frozen_at'] is None
    assert c['assignment'] == '6468672436' and c['ruling_source'].startswith('Codex 6468672436')
    assert c['doc'] == os.path.basename(MD)
    assert '**Status: DRAFT — unsigned.**' in md and 'the JSON wins any conflict' in md


def test_required_sections_and_keys(c):
    for k in ('builds_on', 'slice', 'scope', 'ledger_constants', 'threat_model', 'refresh_cadence', 'active_segment',
              'page_privacy', 'counters_added', 'test_plan', 'deferred', 'open_questions', 'resolved_questions'):
        assert k in c, k
    assert (c['refresh_cadence']['requirement'], c['active_segment']['requirement'],
            c['page_privacy']['requirement']) == ('a', 'b', 'c')
    for k in ('refresh_cadence', 'active_segment', 'page_privacy'):
        assert c[k]['problem']
    assert c['integration_rulings'] == ['6480873345', '6483289354', '6483743382']
    assert set(c['ruling_6483289354']) == {'C1', 'C2', 'C3', 'C4'}
    assert set(c['ruling_6483743382']) == {'E1', 'E2', 'E3', 'E4'}
    for k in ('refresh_cadence', 'active_segment'):                       # Q3 numbers frozen by ruling, not proposals
        assert c[k]['proposal_for_integration'] is False
        assert c[k]['numbers_status'].startswith('frozen by Integration ruling 6480873345')
    assert c['page_privacy']['proposal_for_integration'] is True


def test_builds_on_pins_accepted_merge_shas(c, md):
    b = c['builds_on']
    assert set(b) == {'AI-SCAN-03', 'AI-SCAN-03A'}
    assert (b['AI-SCAN-03']['pr'], b['AI-SCAN-03A']['pr']) == (227, 229)
    assert b['AI-SCAN-03']['accepted_merge_sha'] == '612b53733043912e6ce8ce29acb725ad13a88e68'
    assert b['AI-SCAN-03A']['accepted_merge_sha'] == 'f75606863c63e0ccdd49e3e61433c10af3ea2f29'
    for p in b.values():
        assert p['state'] == 'merged' and p['edited_here'] is False and re.fullmatch(r'[0-9a-f]{40}', p['accepted_merge_sha'])
        assert 'never pin a superseded review head' in p['refresh_rule']
    assert '**accepted merge SHAs**' in md and '`612b537`' in md and '`f756068`' in md


def test_contract_only_slice_changes_no_runtime_file(c):
    s = c['slice']
    assert s['kind'] == 'contract_only' and s['runtime_code_changed'] is False
    assert all(f.startswith(('contracts/ai_scan_04/', 'tests/')) for f in s['files'])
    assert {'ai_scan.py', 'ai_scan_ledger.py', 'engine.py', 'app.py'} <= set(s['untouched_runtime_files'])
    assert all(os.path.isfile(os.path.join(ROOT, f)) for f in s['files'])


# ------------------------------------------------------------------ authority boundaries
def test_producer_default_off_and_no_authority(c, md):
    s = c['scope']
    assert s['flag'] == A.FLAG and s['producer_default_on'] is False and s['paper_only'] is True
    assert s['live_behavior'] == 'refused' and s['authority'] == 'none' and s['grants'] == []
    assert 'separate Integration ruling' in s['producer_enable_requires']
    for k in ('order_gating', 'trading_reads_output', 'model_calls', 'network_calls', 'paper_order_authority',
              'settings_writes', 'engine_state_writes', 'holds_engine_lock'):
        assert s[k] is False, k
    assert '**default OFF**' in md and '**a separate Integration ruling**' in md and '**PAPER only.**' in md
    assert '**Authority: none.**' in md and '**No model or API call' in md


def test_deferred_list(c):
    assert set(c['deferred']) >= {'runtime_producer_hook', 'producer_enable', 'ipc_page_transport',
                                  'keyed_ledger_authentication', 'remote_model_api_call', 'model_promotion',
                                  'ui_controls', 'trading_authority', 'paper_order_authority'}
    assert 'ipc_page_transport' in c['page_privacy']['external_boundary_requires']
    assert 'keyed_ledger_authentication' in c['threat_model']['does_not_detect']


# ------------------------------------------------------------------ constants equal ai_scan_ledger (and #227)
def test_ledger_constants_equal_ai_scan_ledger(c):
    k = c['ledger_constants']
    assert (k['SEG_MIN'], k['SEG_MAX'], k['SEGS_PER_READ'], k['MAX_SEGMENTS']) == \
        (L.SEG_MIN, L.SEG_MAX, L.SEGS_PER_READ, L.MAX_SEGMENTS)
    assert k['max_corpus_bytes'] == L.MAX_SEGMENTS * L.SEG_MAX == 64 << 30
    with open(JS03, encoding='utf-8') as fh:
        lim = json.load(fh)['rotation_prerequisite']['limits']
    assert (lim['min_segment_bytes'], lim['max_segment_bytes'], lim['max_segments_per_read'],
            lim['max_manifest_segments']) == (L.SEG_MIN, L.SEG_MAX, L.SEGS_PER_READ, L.MAX_SEGMENTS)


# ------------------------------------------------------------------ (a) bounded refresh cadence
def test_a_refresh_cadence_has_concrete_bounds(c):
    r = c['refresh_cadence']
    for k in ('tick_period_s', 'sealed_segments_per_tick', 'max_sealed_bytes_per_tick', 'max_active_bytes_per_tick',
              'max_segment_bytes_per_tick', 'max_full_sweep_ticks', 'max_full_sweep_s', 'max_bytes_per_seal_refresh',
              'eligibility_max_age_s'):
        assert _posint(r[k]), k
    assert r['tick_period_s'] == 60 and r['sealed_segments_per_tick'] == 16             # Q3 ruling 6480873345
    assert r['max_sealed_bytes_per_tick'] == r['sealed_segments_per_tick'] * L.SEG_MAX == 16 * (16 * MIB) == 256 * MIB
    assert r['max_active_bytes_per_tick'] == L.SEG_MAX == 16 * MIB
    assert r['max_segment_bytes_per_tick'] == r['max_sealed_bytes_per_tick'] + r['max_active_bytes_per_tick']
    assert r['max_full_sweep_ticks'] == math.ceil(L.MAX_SEGMENTS / r['sealed_segments_per_tick']) == math.ceil(4096 / 16) == 256
    assert r['max_full_sweep_s'] == r['max_full_sweep_ticks'] * r['tick_period_s'] == 256 * 60 == 15360
    assert r['max_full_sweep_s'] == 4 * 3600 + 16 * 60                                  # 4 h 16 m
    assert r['eligibility_max_age_s'] == 180
    assert r['max_bytes_per_seal_refresh'] == L.SEG_MAX                    # never a corpus re-read on a seal
    assert r['max_segment_bytes_per_tick'] < L.MAX_SEGMENTS * L.SEG_MAX
    assert r['tick_period_s'] < r['eligibility_max_age_s'] <= 3 * r['tick_period_s']
    assert 'never the trading thread' in r['thread'] and 'never the whole corpus' in r['seal_refresh']
    assert 'no disk I/O' in r['eligibility_age_rule'] and 'eligibility_stale' in r['eligibility_age_rule']
    assert 'fail-closed' in r['repeated_overrun'] and 'eligibility_max_age_s' in r['repeated_overrun']


def test_a_sealed_verification_is_sequential_one_buffer(c, md):
    r = c['refresh_cadence']
    assert r['verification_order'] == 'sequential' and r['max_segments_held_in_memory'] == 1
    assert 'never held simultaneously' in r['memory_bound']
    assert "one segment's streaming buffer at a time" in r['memory_bound']
    assert 'sequentially' in r['per_tick'][1]
    assert r['max_segments_held_in_memory'] * L.SEG_MAX < r['max_sealed_bytes_per_tick']   # bound is one, not 16
    assert '**sequentially**' in md and "**one segment's streaming buffer at a time**" in md
    assert 'test_refresh_tick_holds_one_segment_buffer_at_a_time' in [t['name'] for t in c['test_plan']]


def test_a_startup_verification_q1_conditions(c):
    r = c['refresh_cadence']
    sr = r['startup_rules']
    assert sr['off_trading_thread'] is True and sr['producer_ineligible_until_complete'] is True
    assert sr['blocks_engine_or_paper_startup'] is False
    assert 'pre-live optimization' in sr['bounded_indexed_startup'] and 'not a blocker' in sr['bounded_indexed_startup']
    assert 'exclusively off the trading thread' in r['startup'] and 'never blocks engine or PAPER startup' in r['startup']


def _sweep(n_sealed, per_tick, edited):
    """Rolling cursor model: -> the tick (1-based) at which sealed segment `edited` (1..n) is re-verified."""
    cur = 0
    for t in range(1, math.ceil(n_sealed / per_tick) + 1):
        batch = [(cur + i) % n_sealed + 1 for i in range(min(per_tick, n_sealed))]
        if edited in batch: return t
        cur = (cur + per_tick) % n_sealed
    return None


@pytest.mark.parametrize('n', [1, 3, 4, 9, 100, 4096])
def test_a_rolling_sweep_reaches_every_segment_within_the_bound(c, n):
    r = c['refresh_cadence']
    worst = max(_sweep(n, r['sealed_segments_per_tick'], e) for e in range(1, n + 1))
    assert worst <= math.ceil(n / r['sealed_segments_per_tick']) <= r['max_full_sweep_ticks']


# ------------------------------------------------------------------ (b) active-segment exposure + recovery
def test_b_active_segment_bounds(c):
    a = c['active_segment']
    for k in ('max_active_bytes_cap', 'max_active_rows', 'max_active_age_s', 'max_undetected_exposure_s',
              'max_auto_recoveries', 'recovery_window_s'):
        assert _posint(a[k]), k
    assert a['max_active_bytes_cap'] == L.SEG_MAX and 'segment_bound' in a['max_active_bytes']
    assert a['max_active_rows'] == 10000 and a['max_active_age_s'] == 6 * 3600
    assert a['max_undetected_exposure_s'] == c['refresh_cadence']['tick_period_s']
    assert L.MAX_SEGMENTS * a['max_active_age_s'] >= 365 * 86400               # age-only seals last over a year
    assert (a['max_auto_recoveries'], a['recovery_window_s']) == (1, 86400)
    for k in ('running_digest', 'digest_check', 'pre_seal_check', 'forced_seal', 'startup_seal', 'torn_tail'):
        assert a[k], k
    assert 'before hashing' in a['pre_seal_check'] and 'BEFORE the first append' in a['startup_seal']


def test_b_durable_checkpoint_fields_and_atomic_replace(c, md):
    a = c['active_segment']
    cp = a['durable_checkpoint']
    assert cp['fields'] == ['committed', 'pending']
    assert {'segment', 'byte_length', 'sha256'} <= set(cp['state_fields'])
    assert 'intent before every append' in cp['written'] and 'after every fsynced append' in cp['written']
    assert cp['atomic_replace'] == ['write temp file', 'fsync temp file', 'os.replace over the checkpoint',
                                    'fsync the directory']
    assert cp['never_written_in_place'] is True
    assert 'durable checkpoint' in a['digest_check']
    assert '**temp + fsync + `os.replace`**' in md and '**every fsynced append**' in md


def test_b_startup_seal_requires_checkpoint_match(c, md):
    a = c['active_segment']
    assert 'only when its bytes match the durable checkpoint' in a['startup_seal']
    for f in ('segment', 'byte_length', 'sha256'):
        assert f in a['startup_match_rule']
    m = a['startup_mismatch']
    assert all(w in m for w in ('missing', 'malformed', 'mismatched', 'active_corrupt', 'durable gap', 'quarantine'))
    ids = [s['id'] for s in a['recovery']]
    assert ids.index('disable') < ids.index('open_gap') < ids.index('quarantine')
    assert 'checkpoint_mismatch' in c['counters_added']
    assert 'does not survive' not in json.dumps(c) and 'does not survive' not in md   # no in-memory-only startup basis
    assert '**only when its bytes match the checkpoint**' in md and '**missing, malformed or mismatched checkpoint**' in md


def test_b_restart_tamper_test_is_required(c, md):
    name = 'test_restart_refuses_same_size_parse_valid_active_edit'
    plan = {t['name']: t for t in c['test_plan']}
    assert plan[name].get('required') is True
    assert 'same-size, parse-valid edit' in plan[name]['asserts'] and 'writer is down' in plan[name]['asserts']
    assert 'refused at restart' in plan[name]['asserts']
    assert a_txt(c).startswith(name) and f'`{name}` (required)' in md
    for n in ('test_checkpoint_atomically_replaced_after_every_fsynced_append',
              'test_startup_seals_a_nonempty_active_only_when_checkpoint_matches',
              'test_missing_malformed_or_mismatched_checkpoint_is_active_corrupt'):
        assert n in plan


def a_txt(c):
    return c['active_segment']['restart_tamper_test_required']


def test_b_recovery_steps_are_ordered_and_fail_closed(c):
    steps = c['active_segment']['recovery']
    assert [s['step'] for s in steps] == list(range(1, len(steps) + 1))
    assert [s['id'] for s in steps] == ['disable', 'persist_marker', 'open_gap', 'quarantine', 'fresh_active',
                                        'rearm_limit']
    txt = {s['id']: s['action'] for s in steps}
    assert 'append and seal are refused' in txt['disable']
    assert 'never deleted' in txt['quarantine'] and 'never sealed' in txt['quarantine']
    assert 'not re-imported' in txt['quarantine']
    assert 'Integration ruling' in txt['rearm_limit']


# ------------------------------------------------------------------ (c) process-private pages
def test_c_pages_are_process_private(c):
    p = c['page_privacy']
    assert p['chosen_option'] == 'process_private'
    assert p['pages_cross_process_boundary'] is False and p['pages_serialized'] is False
    assert p['api_returns_page_objects'] is False
    assert p['max_pages_held_per_fold'] == 1
    assert p['max_page_bytes_in_memory'] == L.SEGS_PER_READ * L.SEG_MAX
    assert 'authenticated page contents' in p['external_boundary_requires']


# ------------------------------------------------------------------ test plan / counters / parity
def test_test_plan_covers_a_b_c(c):
    names = [t['name'] for t in c['test_plan']]
    assert len(names) == len(set(names)) and all(n.startswith('test_') and t['asserts'] for n, t in
                                                  zip(names, c['test_plan']))
    assert not any('four' in n for n in names)
    for need in ('test_refresh_tick_verifies_at_most_sixteen_sealed_segments', 'test_stale_eligibility_is_refused_on_the_hot_path',
                 'test_active_same_size_edit_detected_within_one_tick', 'test_corrupt_active_is_quarantined_never_sealed_or_deleted',
                 'test_pages_never_leave_the_fold', 'test_producer_stays_default_off'):
        assert need in names


def test_open_questions_resolved_by_ruling(c, md):
    assert c['open_questions'] == []
    rq = {q['id']: q for q in c['resolved_questions']}
    assert list(rq) == ['Q1', 'Q2', 'Q3'] and all(q['ruling'] == '6480873345' for q in rq.values())
    for q in rq:
        assert f'**{q} (resolved):**' in md
    assert '## 10. Resolved by Integration ruling 6480873345' in md and 'Open questions' not in md
    q1 = rq['Q1']
    assert q1['scope'] == 'PAPER/shadow only' and 'pre-live optimization' in q1['pre_live']
    assert all(w in q1['resolution'] for w in ('exclusively off the trading thread', 'ineligible until complete',
                                                 'never blocks engine/PAPER startup'))
    q2 = rq['Q2']
    assert q2['scope'] == 'PAPER only' and 'mandatory pre-live decision' in q2['pre_live']
    assert 'claims no protection against a local actor who can rewrite the chain' in q2['resolution']
    t = c['threat_model']
    assert t['local_rewrite_protection_claimed'] is False and 'PAPER only' in t['accepted_for']
    assert 'mandatory pre-live decision' in t['pre_live_requirement']
    assert '**no protection against a local actor who can rewrite the chain**' in md
    q3 = rq['Q3']['resolution']
    for frag in ('16 sequential', '256 MiB sealed + 16 MiB active', '256 ticks = 15360 s', '4 h 16 m', '180 s',
                 '10000-row', 'fail-closed', 'frozen'):
        assert frag in q3, frag
    assert '17.1' not in json.dumps(c) and '17.1' not in md and '61440' not in json.dumps(c)


def test_md_names_everything_the_json_lists(c, md):
    for k in c['counters_added'] + c['deferred'] + [t['name'] for t in c['test_plan']] + \
            [s['id'] for s in c['active_segment']['recovery']]:
        assert f'`{k}`' in md, k
    for sec in ('refresh_cadence', 'active_segment'):
        for k, v in c[sec].items():
            if _posint(v) and k != 'max_active_bytes_cap':
                assert f'`{k}`' in md, k
    assert '`max_active_bytes_cap`' in md and '`process_private`' in md


# ------------------------------------------------------------------ Integration ruling 6483289354 (C1..C4)
def _startup_state(file_state, committed, pending):
    """C1 model: -> 'committed' (discard pending), 'pending' (promote) or 'active_corrupt'."""
    if file_state == committed: return 'committed'
    if pending is not None and file_state == pending: return 'pending'
    return 'active_corrupt'


def test_c1_two_phase_intent_commit_checkpoint(c, md):
    a = c['active_segment']
    cp = a['durable_checkpoint']
    assert cp['protocol'] == 'two_phase_intent_commit' and cp['protocol_ruling'].startswith('6483289354 (C1)')
    assert [s['id'] for s in cp['sequence']] == ['persist_intent', 'append_fsync', 'commit']
    assert [s['step'] for s in cp['sequence']] == [1, 2, 3]
    assert 'before each append' in cp['sequence'][0]['action'] and '{committed, pending}' in cp['sequence'][0]['action']
    assert 'fsync' in cp['sequence'][1]['action'] and 'promote pending to committed' in cp['sequence'][2]['action']
    assert all(w in cp['pending_rule'] for w in ('segment n', 'byte_length', 'sha256'))
    assert set(cp['startup_accepted_states']) == {'committed', 'pending'} and len(cp['startup_accepted_states']) == 2
    assert set(cp['startup_state_rules']) == {'committed', 'pending'}
    assert 'discard pending' in cp['startup_state_rules']['committed']
    assert 'promote pending' in cp['startup_state_rules']['pending']
    assert all(w in cp['other_states'] for w in ('partial', 'third state', 'active_corrupt', 'gap', 'quarantine'))
    assert cp['benign_crash_spends_recovery_budget'] is False
    assert all(w in cp['benign_crash'] for w in ('never discards a valid complete tail', 'never quarantines',
                                                 'max_auto_recoveries'))
    assert 'crash between the append fsync and the checkpoint replace' not in json.dumps(c) + md   # old rule gone
    assert 'neither committed nor pending' in a['startup_mismatch']
    plan = {t['name']: t for t in c['test_plan']}
    for n in ('test_restart_refuses_same_size_parse_valid_active_edit', 'test_crash_after_intent_before_append_accepts_committed',
              'test_crash_after_append_fsync_before_commit_promotes_pending', 'test_torn_or_partial_append_is_active_corrupt'):
        assert plan[n].get('required') is True and f'`{n}`' in md, n
    assert 'recovery budget is untouched' in plan['test_crash_after_append_fsync_before_commit_promotes_pending']['asserts']
    assert {'checkpoint_pending_promoted', 'checkpoint_pending_discarded'} <= set(c['counters_added'])
    assert '**Startup accepts exactly two states:**' in md and '**never spends the automatic-recovery budget**' in md


@pytest.mark.parametrize('case,expect', [
    ('crash_after_intent_before_append', 'committed'),
    ('crash_after_append_fsync_before_commit', 'pending'),
    ('clean_after_commit', 'committed'),
    ('torn_partial_append', 'active_corrupt'),
    ('same_size_edit', 'active_corrupt'),
])
def test_c1_startup_state_model(case, expect):
    import hashlib
    old, row = b'{"a":1}\n', b'{"b":2}\n'
    st = lambda b: (1, len(b), hashlib.sha256(b).hexdigest())      # noqa: E731
    committed, pending = st(old), st(old + row)
    file_bytes = {'crash_after_intent_before_append': old, 'crash_after_append_fsync_before_commit': old + row,
                  'clean_after_commit': old + row, 'torn_partial_append': old + row[:3],
                  'same_size_edit': old.replace(b'1', b'9')}[case]
    if case == 'clean_after_commit':
        committed, pending = pending, None
    assert _startup_state(st(file_bytes), committed, pending) == expect


def test_c2_sealed_exposure_is_published_separately(c, md):
    r, a = c['refresh_cadence'], c['active_segment']
    se = r['sealed_exposure']
    assert _posint(r['sealed_max_undetected_s']) and r['sealed_max_undetected_s'] == 15360 == r['max_full_sweep_s']
    assert r['sealed_max_undetected_s'] == math.ceil(L.MAX_SEGMENTS / r['sealed_segments_per_tick']) * r['tick_period_s']
    assert a['max_undetected_exposure_s'] == 60 and r['eligibility_max_age_s'] == 180
    assert len({r['sealed_max_undetected_s'], a['max_undetected_exposure_s'], r['eligibility_max_age_s']}) == 3
    assert se['ruling'].startswith('6483289354 (C2)') and se['active_bound_covers_sealed'] is False
    assert se['worst_case_refusal_s'] == r['sealed_max_undetected_s'] + r['eligibility_max_age_s'] == 15540
    assert 'within sealed_max_undetected_s' in se['worst_case'] and 'at most eligibility_max_age_s' in se['worst_case']
    assert 'ACTIVE segment only' in a['exposure_scope'] and 'sealed_max_undetected_s' in a['exposure_scope']
    flat = ' '.join(md.split())
    assert '**detected within 15,360 s**' in flat and '**at most the 180 s eligibility age on top**' in flat
    assert 'it does not cover sealed files' in flat


def test_c3_startup_io_is_bounded(c, md):
    r, k = c['refresh_cadence'], c['ledger_constants']
    io = r['startup_io']
    assert io['ruling'].startswith('6483289354 (C3)')
    assert io['off_trading_thread'] is True and io['producer_ineligible_until_complete'] is True
    assert io['blocks_engine_or_paper_startup'] is False
    assert io['read_cap_bytes_per_s'] == 64 * MIB and io['max_segment_buffers'] == 1
    assert io['read_cap_configurable'].startswith('downward only')
    assert 'cooperative yield' in io['yield_between_segments'] and 'every segment' in io['yield_between_segments']
    assert io['max_bytes'] == k['max_corpus_bytes'] == 64 << 30
    assert io['worst_case_s'] == io['max_bytes'] // io['read_cap_bytes_per_s'] == (64 << 30) // (64 << 20) == 1024
    assert io['max_bytes'] % io['read_cap_bytes_per_s'] == 0
    assert '64 GiB / 64 MiB/s = 1024 s' in io['worst_case_basis']
    flat = ' '.join(md.split())
    assert '**64 GiB / 64 MiB/s = 1024 s**' in flat and '**configurable downward only**' in flat
    assert '`read_cap_bytes_per_s`' in flat and '**cooperative yield between segments**' in flat


def test_c4_checkpoint_persist_failure_is_fail_closed(c, md):
    f = c['active_segment']['checkpoint_persist_failure']
    assert f['ruling'].startswith('6483289354 (C4)')
    assert [p.split(' (')[0] for p in f['failure_points']] == ['temp write', 'temp fsync', 'os.replace', 'directory fsync']
    assert 'PermissionError retries' in f['failure_points'][2]
    assert f['counter'] == 'checkpoint_persist_failed' and f['counter'] in c['counters_added']
    assert f['fail_closed'] is True and f['trading_affected'] is False
    assert f['in_ram_digest_may_lead_durable_state'] is False
    assert 'immediately' in f['action'] and 'append and seal are refused' in f['action']
    assert 'normal recovery path' in f['recovery'] and 'in-RAM digest ahead of the durable state' in f['rule']
    assert 'test_checkpoint_persist_failure_disables_the_producer' in [t['name'] for t in c['test_plan']]
    flat = ' '.join(md.split())
    assert '**disabled immediately**' in flat
    assert '**never continues with an in-RAM digest ahead of the durable state**' in flat


def test_ruling_6483289354_keeps_draft_off_no_authority(c):
    assert c['status'] == 'DRAFT' and c['signed_by'] is None and c['frozen_at'] is None
    s = c['scope']
    assert s['producer_default_on'] is False and s['authority'] == 'none' and s['grants'] == []
    assert c['slice']['runtime_code_changed'] is False


# ------------------------------------------------------------------ Integration ruling 6483743382 (E1..E4)
EMPTY_SHA = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'


def _startup_seal_boundary(cp, manifest, file_state, orphan_temp=False):
    """C1 + E1/E2/E4 startup model.

    cp: None (malformed canonical) or {'committed': (seg, len, sha), 'pending': state or None}; manifest: {seg: (seg,
    len, sha)} of durable sealed entries; file_state: the active file's (seg, len, sha), or 'partial' / 'enospc'.
    -> (outcome, quarantined, recovery_budget_spent, orphan_temp_removed)."""
    corrupt = ('active_corrupt', True, True, False)          # E4: gap -> quarantine, budget accounted; temp untouched
    if cp is None:
        return corrupt                                       # E2: malformed canonical fails closed; temp never used
    n = cp['committed'][0]
    last = max(manifest, default=0)
    if last == n:                                            # E1: crash between the seal and the checkpoint advance
        if manifest[n] != cp['committed'] or cp['pending'] is not None:
            return corrupt
        return ('seal_advanced', False, False, orphan_temp)
    if last != n - 1:                                        # reverse / ambiguous: names n+1 while manifest lacks n
        return corrupt
    if file_state in ('partial', 'enospc'):
        return corrupt                                       # E4: third state, no truncation salvage
    st = _startup_state(file_state, cp['committed'], cp['pending'])
    if st == 'active_corrupt':
        return corrupt
    return (st, False, False, orphan_temp)                   # E2: temp removed only after validation


def test_e1_seal_rotation_crash_protocol(c, md):
    import hashlib
    a = c['active_segment']
    sr = a['seal_rotation']
    assert sr['ruling'].startswith('6483743382 (E1)')
    assert sr['applies_to'] == ['forced_seal', 'startup_seal'] and sr['forced_seal_triggers'] == ['bytes', 'rows', 'age']
    assert [s['id'] for s in sr['sequence']] == ['seal_manifest', 'advance_checkpoint']
    assert [s['step'] for s in sr['sequence']] == [1, 2]
    assert 'durably first' in sr['sequence'][0]['action']
    assert 'atomically replaced and fsynced' in sr['sequence'][0]['action']
    assert all(w in sr['sequence'][1]['action'] for w in ('committed empty n+1', 'segment n+1', 'length 0',
                                                           'sha256 of empty bytes'))
    assert sr['empty_sha256'] == hashlib.sha256(b'').hexdigest() == EMPTY_SHA
    assert 'exactly matches' in sr['startup_seal_boundary']
    assert 'segment, length and sha256' in sr['startup_seal_boundary']
    assert sr['seal_boundary_quarantines'] is False and sr['seal_boundary_spends_recovery_budget'] is False
    assert sr['counter'] == 'checkpoint_seal_advanced' and sr['counter'] in c['counters_added']
    fc = ' '.join(sr['fail_closed_states'])
    assert 'names n+1 while the manifest lacks entry n' in fc and 'mismatches' in fc
    assert 'reverse, partial or ambiguous' in fc
    assert sr['fail_closed_path'] == 'active_corrupt -> durable gap -> quarantine'
    assert 'seal_rotation' in a['forced_seal'] and 'seal_rotation' in a['startup_seal']
    plan = {t['name']: t for t in c['test_plan']}
    t1 = plan['test_crash_between_seal_and_checkpoint_advance']
    assert t1.get('required') is True and 'forced seal' in t1['asserts'] and 'startup seal' in t1['asserts']
    assert plan['test_seal_reverse_order_state_is_refused'].get('required') is True
    flat = ' '.join(md.split())
    assert '**seal manifest entry n durably first**' in flat
    assert '**then advance the checkpoint to committed empty n+1**' in flat
    assert '**no quarantine and no recovery-budget spend**' in flat and '**stays fail-closed**' in flat
    assert '`test_crash_between_seal_and_checkpoint_advance` (required)' in md
    assert '`test_seal_reverse_order_state_is_refused` (required)' in md


@pytest.mark.parametrize('seal', ['forced_seal', 'startup_seal'])
@pytest.mark.parametrize('case,expect', [
    ('cp_n_manifest_n_equal', ('seal_advanced', False, False, False)),
    ('cp_n_manifest_n_equal_orphan_temp', ('seal_advanced', False, False, True)),
    ('cp_n_plus_1_manifest_lacks_n', ('active_corrupt', True, True, False)),
    ('manifest_n_mismatches_cp', ('active_corrupt', True, True, False)),
    ('advanced_clean', ('committed', False, False, False)),
    ('orphan_temp_after_validation', ('committed', False, False, True)),
    ('malformed_canonical_with_orphan_temp', ('active_corrupt', True, True, False)),
    ('partial_append', ('active_corrupt', True, True, False)),
    ('enospc', ('active_corrupt', True, True, False)),
])
def test_e1_e2_e4_startup_seal_boundary_model(seal, case, expect):
    import hashlib
    n, body = 3, b'{"a":1}\n'
    trip = lambda seg, b: (seg, len(b), hashlib.sha256(b).hexdigest())      # noqa: E731
    sealed_n, empty_next = trip(n, body), (n + 1, 0, EMPTY_SHA)
    before = {i: trip(i, body) for i in range(1, n)}                        # durable entries 1..n-1
    after = {**before, n: sealed_n}                                   # entry n durable
    cp_old, cp_new = {'committed': sealed_n, 'pending': None}, {'committed': empty_next, 'pending': None}
    cp_inflight = {'committed': empty_next, 'pending': trip(n + 1, body)}
    temp = 'orphan_temp' in case
    args = {
        'cp_n_manifest_n_equal': (cp_old, after, sealed_n),
        'cp_n_manifest_n_equal_orphan_temp': (cp_old, after, sealed_n),
        'cp_n_plus_1_manifest_lacks_n': (cp_new, before, empty_next),
        'manifest_n_mismatches_cp': (cp_old, {**before, n: trip(n, body + b"x")}, sealed_n),
        'advanced_clean': (cp_new, after, empty_next),
        'orphan_temp_after_validation': (cp_new, after, empty_next),
        'malformed_canonical_with_orphan_temp': (None, after, empty_next),
        'partial_append': (cp_inflight, after, 'partial'),
        'enospc': (cp_inflight, after, 'enospc'),
    }[case]
    assert _startup_seal_boundary(*args, orphan_temp=temp) == expect, (seal, case)


def test_e2_orphan_checkpoint_temp_never_authoritative(c, md):
    o = c['active_segment']['durable_checkpoint']['orphan_temp_files']
    assert o['ruling'].startswith('6483743382 (E2)') and o['authoritative'] is False
    assert o['removed_before_canonical_validated'] is False
    assert 'ignored' in o['startup'] and 'only after the canonical checkpoint is validated' in o['startup']
    assert 'still fails closed' in o['malformed_canonical']
    assert 'test_orphan_checkpoint_temp_is_never_authoritative' in [t['name'] for t in c['test_plan']]
    flat = ' '.join(md.split())
    assert '**never authoritative**' in flat
    assert '**removes it only after the canonical checkpoint is validated**' in flat


def test_e3_one_row_per_transaction_no_batching(c, md):
    cp = c['active_segment']['durable_checkpoint']
    g = cp['granularity']
    assert g['ruling'].startswith('6483743382 (E3)') and g['status'] == 'frozen'
    assert type(g['rows_per_transaction']) is int and g['rows_per_transaction'] == 1
    assert g['batching_allowed'] is False
    assert 'exactly one complete canonical ledger row' in g['rule']
    assert 'measured append latency and overruns' in g['batching_amendment']
    assert 'later contract amendment' in g['batching_amendment']
    assert 'integrity wins over throughput' in g['priority'] and 'OFF' in g['priority']
    per_row = 2 * (len(cp['atomic_replace']) - 1) + 1                  # intent + commit (3 each) + the append fsync
    assert g['fsync_replace_ops_per_row'] == per_row == 7 and 'seven' in g['durability_cost']
    assert not re.search(r'"batch(ing)?_(size|rows|enabled|max)"', json.dumps(c))   # no batching knob anywhere
    flat = ' '.join(md.split())
    assert '**Exactly one complete canonical ledger row per intent/append/commit transaction.**' in flat
    assert '**about seven fsync/replace operations**' in flat
    assert '**Integrity wins over throughput while the producer is OFF.**' in flat


def test_e4_partial_append_or_enospc_is_fail_closed(c, md):
    p = c['active_segment']['durable_checkpoint']['partial_append']
    assert p['ruling'].startswith('6483743382 (E4)')
    assert p['cases'] == ['partial append', 'ENOSPC'] and p['state'] == 'third_state'
    assert p['outcome'] == 'active_corrupt' and p['path'] == ['active_corrupt', 'durable gap', 'quarantine']
    assert p['spends_recovery_budget'] is True and p['truncation_salvage'] is False
    assert 'pending digest alone cannot prove a partial suffix safe' in p['basis']
    assert 'test_partial_append_or_enospc_is_active_corrupt' in [t['name'] for t in c['test_plan']]
    flat = ' '.join(md.split())
    assert '**including recovery-budget accounting**' in flat and '**no truncation salvage**' in flat


def test_ruling_6483743382_keeps_draft_off_no_authority(c):
    assert c['status'] == 'DRAFT' and c['signed_by'] is None and c['frozen_at'] is None
    s = c['scope']
    assert s['producer_default_on'] is False and s['authority'] == 'none' and s['grants'] == []
    assert c['slice']['runtime_code_changed'] is False
