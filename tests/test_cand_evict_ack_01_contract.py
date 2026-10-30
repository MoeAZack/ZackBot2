"""CAND-EVICT-ACK-01 (SIGNED 6517958103, contract): structural checks of the JSON twin and MD, and that every cited engine /
actual_costs function exists on this head. Parses source with ast; imports nothing from the bot, reads no state."""
import ast
import hashlib
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
    assert c['contract'] == 'CAND-EVICT-ACK-01' and c['status'] == 'SIGNED'
    assert '6517958103' in c['signed_by'] and 'feddc63' in c['signed_by'] and c['signed_at'] == '2026-10-30T10:07+02:00'
    assert [k for k in REQUIRED if k not in c] == []


def test_md_exists_and_draft(c):
    assert os.path.isfile(MD) and c['doc'] == 'contracts/cand_evict_ack_01/CAND_EVICT_ACK_01_CONTRACT.md'
    with open(MD, encoding='utf-8') as fh:
        md = fh.read()
    assert '**Status: SIGNED**' in md
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
    with open(os.path.join(ROOT, 'cand_evict_ack.py'), encoding='utf-8') as fh:
        assert f"ACK_KEY = '{c['ack_record_key']}'" in fh.read()      # the implementation (Codex 6518282215)


@pytest.mark.parametrize('path', ['engine.py', 'actual_costs.py'])
def test_cited_functions_exist(c, path):
    names = c['cited_functions'][path]
    assert names and [n for n in names if n not in _defs(path)] == []


RULING = '6515377452'


def test_rulings_resolved_no_open_oqs(c):
    assert c['status'] == 'SIGNED' and c['open_questions'] == {}
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
    assert [t for t in c['required_tests'] if t != 'T11a'] == [f'T{i}' for i in range(1, 21)]
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


# --- Codex 6515460695 signing fixes -------------------------------------------------------------------------------
SIGN = '6515460695'


def _engine_src():
    with open(os.path.join(ROOT, 'engine.py'), encoding='utf-8') as fh:
        return fh.read()


def _engine_consts():
    tree = ast.parse(_engine_src())
    out = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            if n.targets[0].id == 'CANDIDATE_SLOTS':
                out['slots'] = set(ast.literal_eval(n.value))
            elif n.targets[0].id == 'SYM_RE':
                out['sym'] = n.value.args[0].value
    return out


def _ref_sanitize(entry, slots, sym_pat):
    """Reference reading of A4.1 (content-strict), used only to pin the contract's worked cases."""
    from datetime import datetime, timezone
    e = entry if isinstance(entry, dict) else {}
    sl, sy, cl = e.get('sleeve'), e.get('symbol'), e.get('closed')
    out = dict(sleeve=sl if isinstance(sl, str) and sl in slots else None,
               symbol=sy if isinstance(sy, str) and re.fullmatch(sym_pat, sy) else None, closed=None)
    if isinstance(cl, str):
        try:
            ts = datetime.fromisoformat(cl)
            ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
            out['closed'] = ts.isoformat()
        except ValueError:
            pass
    return out


def _strict_sha(entry):
    return hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                                     allow_nan=False).encode('ascii')).hexdigest()


def test_signing_fixes_recorded(c):
    assert c['signing_fixes'] == SIGN and c['status'] == 'SIGNED'
    with open(MD, encoding='utf-8') as fh:
        md = fh.read()
    assert f'**Signing fixes (Codex {SIGN}):**' in md


def test_content_strict_rules_match_engine(c):
    k = _engine_consts()
    rules = c['record']['entry_rules']
    assert 'CANDIDATE_SLOTS' in rules['sleeve'] and k['sym'] == '^[A-Z0-9]{2,20}USDT$' and k['sym'] in rules['symbol']
    assert 'UTC' in rules['closed'] and rules['repair_or_truncate'] is False
    assert set(c['cited_constants']['engine.py']) >= {'CANDIDATE_SLOTS', 'SYM_RE'}
    assert 'default=' not in c['record']['entry_sha256'].replace('no default=', '')
    assert 'refused' in c['record']['non_serializable_original']
    with open(MD, encoding='utf-8') as fh:
        md = fh.read()
    assert 'default=repr' not in md.replace('No `default=repr` path exists', '')
    assert '`SYM_RE`' in md and 'exact member of engine.py `CANDIDATE_SLOTS`' in md


@pytest.mark.parametrize('entry,want', [
    ({'sleeve': 'C-F01', 'symbol': 'BTCUSDT', 'closed': '2026-10-01 12:00:00'},
     {'sleeve': 'C-F01', 'symbol': 'BTCUSDT', 'closed': '2026-10-01T12:00:00+00:00'}),
    ({'sleeve': 'c-f01', 'symbol': '8389765432123', 'closed': 'not a time', 'orderId': 8389765432123},
     {'sleeve': None, 'symbol': None, 'closed': None}),
    ({'sleeve': 'C-F01 ', 'symbol': 'x-ab12cd', 'closed': 12345}, {'sleeve': None, 'symbol': None, 'closed': None}),
    ({'sleeve': 'C-F02', 'symbol': 'btcusdt', 'closed': '2026-10-01T15:00:00+03:00'},
     {'sleeve': 'C-F02', 'symbol': None, 'closed': '2026-10-01T12:00:00+00:00'}),
    (['C-F01', 'BTCUSDT'], {'sleeve': None, 'symbol': None, 'closed': None}),
    ({'sleeve': 'C-F01', 'symbol': 'BTCUSDT\n', 'closed': 1759320000.0},          # Cowork 6517853909
     {'sleeve': 'C-F01', 'symbol': None, 'closed': None}),
])
def test_content_strict_worked_cases(entry, want):
    k = _engine_consts()
    got = _ref_sanitize(entry, k['slots'], k['sym'])
    assert got == want
    assert set(got) == {'sleeve', 'symbol', 'closed'}                  # no extra / id-bearing keys survive
    assert len(_strict_sha(entry)) == 64


@pytest.mark.parametrize('bad', [{'symbol': {1, 2}}, {'closed': float('nan')}, {'x': object()}])
def test_non_serializable_original_has_no_hash(bad):
    with pytest.raises((TypeError, ValueError)):
        _strict_sha(bad)                                                # A3.6a: refused, never hashed via repr


def test_concurrency_both_paths_lock(c):
    e = c['effect']
    assert e['guard_must_acquire_lock'] is True and 'candidate_guard acquires self.lock itself' in e['concurrency']
    assert {'candidate_guard', 'cycle'} <= set(c['cited_functions']['engine.py'])
    src = _engine_src()
    assert 'self.lock = threading.RLock()' in src                        # re-entrant: cycle() -> guard stays valid
    with open(MD, encoding='utf-8') as fh:
        md = fh.read()
    assert '**both** paths must hold `self.lock`' in md and 'must acquire `self.lock` **itself**' in md
    t17 = next(l for l in md.splitlines() if l.startswith('| T17 |'))
    assert 'calls `engine.candidate_guard()` **directly**' in t17 and 'not through `cycle()`' in t17
    assert 'directly' in c['required_test_notes']['T17']


def test_save_failure_wording(c):
    e = c['effect']
    assert e['whole_state_unchanged_on_failed_save'] is False
    assert e['rollback_restores_exactly'][0] == 'candidate_cost_pending'
    assert 'remains' in e['failed_save_side_effects']
    with open(MD, encoding='utf-8') as fh:
        md = fh.read()
    t7 = next(l for l in md.splitlines() if l.startswith('| T7 |'))
    assert 'byte-identical' not in t7 and 'Exactly the pending-index and ack-record subtrees are restored' in t7
    assert 'must remain' in t7 and 'does not assert the whole state unchanged' in t7
    a4 = md[md.index('### A4. Effect'):md.index('### A5.')]
    assert 'byte-identical' not in a4 and 'Exactly two subtrees are restored exactly' in a4
    assert 'T11a' in c['required_tests']


def test_signing_advisories_fullmatch_and_closed_str(c):
    adv = c['signing_advisories']
    assert adv['source'] == '6517853909' and 'fullmatch' in adv['symbol_fullmatch'] and 'str' in adv['closed_str_only']
    rules = c['record']['entry_rules']
    assert 're.fullmatch' in rules['symbol'] and rules['closed'].startswith('str only')
    pat = _engine_consts()['sym']
    assert re.match(pat, 'BTCUSDT\n') and not re.fullmatch(pat, 'BTCUSDT\n')    # why fullmatch is mandatory
    with open(MD, encoding='utf-8') as fh:
        md = fh.read()
    assert '**`re.fullmatch` semantics**' in md and 'must be a **`str`** before any parsing' in md
    t11 = next(l for l in md.splitlines() if l.startswith('| T11 |'))
    assert '"BTCUSDT\\n"' in t11 and '1759320000.0' in t11
