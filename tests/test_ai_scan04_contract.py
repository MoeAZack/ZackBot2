"""AI-SCAN-04 producer-wiring contract twin (Codex 6468672436): the JSON twin is authoritative, stays DRAFT/unsigned,
grants no producer / network / PAPER / trading authority, and carries concrete numeric bounds for the three pre-producer
requirements: (a) bounded periodic integrity refresh, (b) active-segment exposure + fail-closed recovery, (c)
process-private pagination pages. Overlapping limits must equal the ai_scan_ledger constants on master.

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
              'page_privacy', 'counters_added', 'test_plan', 'deferred', 'open_questions'):
        assert k in c, k
    assert (c['refresh_cadence']['requirement'], c['active_segment']['requirement'],
            c['page_privacy']['requirement']) == ('a', 'b', 'c')
    for k in ('refresh_cadence', 'active_segment', 'page_privacy'):
        assert c[k]['proposal_for_integration'] is True and c[k]['problem']


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
    assert r['tick_period_s'] == 60 and r['sealed_segments_per_tick'] == L.SEGS_PER_READ
    assert r['max_sealed_bytes_per_tick'] == r['sealed_segments_per_tick'] * L.SEG_MAX == 64 * MIB
    assert r['max_active_bytes_per_tick'] == L.SEG_MAX
    assert r['max_segment_bytes_per_tick'] == r['max_sealed_bytes_per_tick'] + r['max_active_bytes_per_tick']
    assert r['max_full_sweep_ticks'] == math.ceil(L.MAX_SEGMENTS / r['sealed_segments_per_tick']) == 1024
    assert r['max_full_sweep_s'] == r['max_full_sweep_ticks'] * r['tick_period_s'] <= 24 * 3600
    assert r['max_bytes_per_seal_refresh'] == L.SEG_MAX                    # never a corpus re-read on a seal
    assert r['max_segment_bytes_per_tick'] < L.MAX_SEGMENTS * L.SEG_MAX
    assert r['tick_period_s'] < r['eligibility_max_age_s'] <= 3 * r['tick_period_s']
    assert 'never the trading thread' in r['thread'] and 'never the whole corpus' in r['seal_refresh']
    assert 'no disk I/O' in r['eligibility_age_rule'] and 'eligibility_stale' in r['eligibility_age_rule']


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
    for need in ('test_refresh_tick_verifies_at_most_four_sealed_segments', 'test_stale_eligibility_is_refused_on_the_hot_path',
                 'test_active_same_size_edit_detected_within_one_tick', 'test_corrupt_active_is_quarantined_never_sealed_or_deleted',
                 'test_pages_never_leave_the_fold', 'test_producer_stays_default_off'):
        assert need in names


def test_open_questions_are_listed(c, md):
    ids = [q['id'] for q in c['open_questions']]
    assert ids == ['Q1', 'Q2', 'Q3']
    for q in ids:
        assert f'**{q}:**' in md


def test_md_names_everything_the_json_lists(c, md):
    for k in c['counters_added'] + c['deferred'] + [t['name'] for t in c['test_plan']] + \
            [s['id'] for s in c['active_segment']['recovery']]:
        assert f'`{k}`' in md, k
    for sec in ('refresh_cadence', 'active_segment'):
        for k, v in c[sec].items():
            if _posint(v) and k != 'max_active_bytes_cap':
                assert f'`{k}`' in md, k
    assert '`max_active_bytes_cap`' in md and '`process_private`' in md
