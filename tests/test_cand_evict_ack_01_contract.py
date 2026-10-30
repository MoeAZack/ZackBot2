"""CAND-EVICT-ACK-01 (DRAFT, contract only): structural checks of the JSON twin and MD, and that every cited engine /
actual_costs function exists on this head. Parses source with ast; imports nothing from the bot, reads no state."""
import ast
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CDIR = os.path.join(ROOT, 'contracts', 'cand_evict_ack_01')
JS = os.path.join(CDIR, 'cand_evict_ack_01_contract.v1.json')
MD = os.path.join(CDIR, 'CAND_EVICT_ACK_01_CONTRACT.md')
REQUIRED = ('contract', 'version', 'status', 'doc', 'cited_functions', 'index_key', 'ack_record_key', 'authority',
            'input', 'admission', 'effect', 'idempotent', 'never', 'canary', 'required_tests', 'open_questions')


@pytest.fixture(scope='module')
def c():
    with open(JS, encoding='utf-8') as fh:
        return json.load(fh)


def _defs(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as fh:
        tree = ast.parse(fh.read())
    return {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_json_draft_and_keys(c):
    assert c['contract'] == 'CAND-EVICT-ACK-01' and c['status'] == 'DRAFT'
    assert c['signed_by'] is None and c['signed_at'] is None
    assert [k for k in REQUIRED if k not in c] == []


def test_md_exists_and_draft(c):
    assert os.path.isfile(MD) and c['doc'] == 'contracts/cand_evict_ack_01/CAND_EVICT_ACK_01_CONTRACT.md'
    with open(MD, encoding='utf-8') as fh:
        md = fh.read()
    assert '**Status: DRAFT**' in md
    for q in c['open_questions']:
        assert f'**{q}**' in md
    for t in c['required_tests']:
        assert f'| {t} |' in md


def test_authority_and_safety_terms(c):
    a = c['authority']
    assert a['who'] == 'integration' and a['paper_only'] is True
    assert re.fullmatch(a['comment_id_pattern'], '6508707059') and not re.fullmatch(a['comment_id_pattern'], 'abc')
    assert set(c['input']['required']) == {'row_id', 'reason', 'comment_id'}
    assert c['effect']['persist'] == 'save_state' and c['effect']['fail_closed_on_save_failure'] is True
    assert any('re-enable' in n for n in c['never']) and any('order' in n for n in c['never'])
    assert all(re.fullmatch(r'OQ\d+', k) for k in c['resolved_questions'])


def test_index_key_matches_engine(c):
    with open(os.path.join(ROOT, 'engine.py'), encoding='utf-8') as fh:
        src = fh.read()
    assert f"CANDIDATE_PENDING_KEY = '{c['index_key']}'" in src
    assert c['ack_record_key'] not in src              # contract only: no runtime code yet


@pytest.mark.parametrize('path', ['engine.py', 'actual_costs.py'])
def test_cited_functions_exist(c, path):
    names = c['cited_functions'][path]
    assert names and [n for n in names if n not in _defs(path)] == []


RULING = '6515377452'


def test_rulings_resolved_no_open_oqs(c):
    assert c['status'] == 'DRAFT' and c['open_questions'] == {}
    assert c['ruling'] == RULING and c['resolved_questions_source'] == RULING
    assert sorted(c['resolved_questions']) == [f'OQ{i}' for i in range(1, 9)]
    with open(MD, encoding='utf-8') as fh:
        md = fh.read()
    assert '## Open questions' not in md and f'## RESOLVED (Integration ruling {RULING}' in md
    assert 'verbatim' not in md.replace('not recorded verbatim', '')     # OQ5: the A3.6/A6/T9 contradiction is gone
    assert c['canary']['resolved'] is True and c['canary']['scope'] == 'C-F01 only' and 'never assume 0 R' in c['canary']['rule']
    assert c['authority']['rows_per_call'] == 1 and 'no CLI' in c['authority']['transport']


def test_record_whitelist_and_save_fault(c):
    r = c['record']
    assert r['entry_whitelist'] == ['sleeve', 'symbol', 'closed'] and r['raw_entry_stored'] is False
    assert 'entry_sha256' in r['fields']
    e = c['effect']
    assert e['rollback_removes_new_empty_ack_dict'] is True
    assert '_persist_fault' in e['failed_save_side_effects'] and 'ENTRIES_PAUSED' in e['failed_save_side_effects']
    assert 'self.lock' in e['concurrency']


def test_required_tests_cover_pause_and_interleaving(c):
    assert c['required_tests'] == [f'T{i}' for i in range(1, 21)]
    n = c['required_test_notes']
    assert 'ENTRIES_PAUSED' in n['T7'] and 'state_untrusted' in n['T7'] and 'empty' in n['T7']
    assert 'interleaving' in n['T17'] and 'half-state' in n['T17']
    with open(MD, encoding='utf-8') as fh:
        md = fh.read()
    t7 = next(l for l in md.splitlines() if l.startswith('| T7 |'))
    t17 = next(l for l in md.splitlines() if l.startswith('| T17 |'))
    assert 'ENTRIES_PAUSED' in t7 and 'state_untrusted' in t7
    assert 'interleaving' in t17 and 'self.lock' in t17
    assert 'f01_canary' in n['T14'] and '#249' in c['ordering']
