"""AUD-08 exit-code vocabulary: a stable machine registry, and every legacy exit reason mapped to exactly one code.

Registry rules (schema.EXIT_CODE_MEANING): append-only within a schema version - a code is never removed, renamed, reordered
or reused for another meaning; it is deprecated instead (schema.DEPRECATED_EXIT_CODES) and stays valid. Free text (titles,
reasons, notes) is display only and never compared. Mutations that must fail here: drop / rename / reorder a pinned code,
fold stop_crossed back into STOP_HIT, add an engine.py / backtest.py exit reason without mapping it.
Behaviour registry (schema.BEHAVIOUR_MEANING, Codex golden r3 residual ruling point 1): the same append-only rule, with the
meanings pinned too; a case whose `behaviours` holds an unknown or duplicated ID is a CaseError. The registry is data
(BEHAVIOURS.json, Codex golden r5 ruling #1) and its real anchor is the base tree (test_ledger rule 8); the extension rule is
unit-tested here on synthetic registries."""
import copy, os, re, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from goldenlib import REPO_ROOT, registry, schema  # noqa: E402
from goldenlib.adapters import legacy_backtest, legacy_engine  # noqa: E402

# zb-golden/1 registry as published. Append new codes to schema.EXIT_CODE_MEANING (and here); never edit this prefix.
PINNED_V1 = ('STOP_HIT', 'TIME_EXIT', 'SIGNAL_EXIT', 'TP_FULL', 'TP_BASKET', 'TP_PARTIAL', 'TP_LADDER', 'LIQUIDATED', 'FLATTEN',
             'STOP_CROSSED', 'RESYNC', 'STOP_FAILED', 'BASKET_TP_PART')
CODE = re.compile(r'[A-Z][A-Z0-9_]*')
# Behaviour registry zb-golden-behaviours/1 as published (Codex golden r3 residual ruling, point 1): the ordered IDs AND the
# sha256 of their [id, meaning] pairs. Append new IDs to tests/golden/BEHAVIOURS.json; never edit this prefix or its meanings.
# Defence in depth only: this pin lives in a mutable file, so the anchor is the base tree's BEHAVIOURS.json (test_ledger rule 8).
PINNED_BEHAVIOURS_VERSION = 'zb-golden-behaviours/1'
PINNED_BEHAVIOURS_V1 = ('stop', 'gap', 'target', 'time_exit', 'cairo_day', 'daily_halt', 'dca', 'costs', 'pyramid', 'trail',
                        'trail_entry', 'max_pos', 'short_side', 'exit_codes', 'partial', 'tp1', 'min_size', 'outage', 'restart',
                        'ambiguity', 'entry')
PINNED_BEHAVIOURS_V1_SHA = '9142440f1f09df2bda3ea1b341dd591bb712d68bc3972d8a62118de9c6a0b8ec'
BEHAVIOUR_ID = registry.BEHAVIOUR_ID


def _src(name):
    with open(os.path.join(REPO_ROOT, name), encoding='utf-8') as f:
        return f.read()


def engine_exit_reasons():
    """Every literal exit reason engine.py can journal: the reason argument of close_lot / _finish / _market_close /
    _apply_close (incl. `'a' if x else 'b'`). Dynamic reasons (pd_['why'] of a confirmed pending close) are one of these."""
    s = _src('engine.py')
    lit = r"('\w+'(?:\s+if\s+[^,()]+\s+else\s+'\w+')?)"
    pats = (r'\.close_lot\(\s*[^,()]+,\s*' + lit, r'\._finish\(\s*[^,()]+,\s*' + lit,
            r'\._market_close\(\s*[^,()]+,\s*[^,]+?,\s*' + lit, r'\._apply_close\(\s*[^,()]+,\s*[^,()]+,\s*[^,]+?,\s*' + lit)
    out = set()
    for p in pats:
        for m in re.finditer(p, s):
            out |= set(re.findall(r"'(\w+)'", m.group(1)))
    return out


def backtest_exit_reasons():
    """Every literal `why` of backtest.close(sl, s, p, px, frac, i, why) (incl. `'a' if x else 'b'`)."""
    out = set()
    for line in _src('backtest.py').splitlines():
        m = re.search(r'\bclose\(sl, s, p, .*?,\s*i,\s*(.+)\)', line)
        if m:
            out |= set(re.findall(r"'(\w+)'(?=\s+if|\s*\)|\s*$)", m.group(1).rstrip(') ;')))
            out |= set(re.findall(r"else\s+'(\w+)'", m.group(1)))
    return out


def test_registry_is_append_only():
    assert schema.SCHEMA == 'zb-golden/1', 'a new schema version starts a new pinned registry'
    assert schema.EXIT_CODES[:len(PINNED_V1)] == PINNED_V1, \
        'the exit-code registry is append-only: a pinned code was removed, renamed or reordered (deprecate it instead)'
    assert len(set(schema.EXIT_CODES)) == len(schema.EXIT_CODES)
    for c in schema.EXIT_CODES:
        assert CODE.fullmatch(c), c
        assert len(schema.EXIT_CODE_MEANING[c].strip()) >= 20, f'{c}: every code states its meaning'


def test_deprecated_codes_stay_valid():
    for old, new in schema.DEPRECATED_EXIT_CODES.items():
        assert old in schema.EXIT_CODES and new in schema.EXIT_CODES and new not in schema.DEPRECATED_EXIT_CODES, (old, new)


def test_reason_scanners_find_the_known_reasons():
    """The scanners are not vacuous: they find today's reasons (a scanner that finds nothing would pass every mapping check)."""
    assert {'stop', 'stop_crossed', 'time_exit', 'exit_signal', 'take_profit', 'basket_tp', 'basket_tp_part', 'take_profit_1',
            'take_profit_ladder', 'flatten', 'resync', 'stop_failed'} <= engine_exit_reasons()
    assert {'stop', 'tp', 'tp1', 'tp_ladder', 'signal', 'time', 'liquidated'} <= backtest_exit_reasons()


def test_every_engine_reason_maps_to_one_registered_code():
    found = engine_exit_reasons()
    missing = sorted(found - set(legacy_engine.REASON))
    assert not missing, f'engine.py journals exit reasons the golden engine adapter does not map (would be UNMAPPED:...): {missing}'
    assert set(legacy_engine.REASON.values()) <= set(schema.EXIT_CODES)
    vals = list(legacy_engine.REASON.values())
    assert len(vals) == len(set(vals)), 'two engine reasons fold into one code: every reason keeps its own meaning'


def test_every_backtest_reason_maps_to_a_registered_code():
    found = backtest_exit_reasons()
    missing = sorted(found - set(legacy_backtest.WHY) - {'tp'})       # 'tp' -> TP_FULL / TP_BASKET by the slot
    assert not missing, f'backtest.py writes `why` values the golden backtest adapter does not map: {missing}'
    assert set(legacy_backtest.WHY.values()) <= set(schema.EXIT_CODES)


def test_stop_hit_and_stop_crossed_are_distinct():
    """An exchange stop fill and the bot's market close of a crossed level are different executions (who fills, at what price)."""
    assert legacy_engine.REASON['stop'] == 'STOP_HIT'
    assert legacy_engine.REASON['stop_crossed'] == 'STOP_CROSSED'
    assert legacy_engine.REASON['flatten'] == 'FLATTEN'


@pytest.mark.parametrize('code', ['UNMAPPED:flatten', 'STOP', 'stop_hit', 'STOP_HIT ', '', None])
def test_schema_rejects_codes_outside_the_registry(code):
    c = copy.deepcopy(schema.load(schema.case_paths()[0]))
    c['expect']['trades'][0]['exit'] = code
    with pytest.raises(schema.CaseError):
        schema.validate(c)


# ------------------------------------------------------------------ slot.dca values (Cowork review of #40)
def _dca_case():
    return copy.deepcopy(schema.load(os.path.join(schema.CASES_DIR, 'G-GAP-DCA-ONEADD-L-01.json')))


def test_every_dca_case_in_the_pack_validates():
    dca = [c for c in schema.load_all() if 'dca' in c['slot']]
    assert {c['id'] for c in dca} >= {'G-GAP-DCA-L-01', 'G-GAP-DCA-ONEADD-L-01', 'G-DCA-SLIP-L-01'}
    for c in dca:
        schema.validate(c)


@pytest.mark.parametrize('n', [0, -1, 11, 10 ** 9, 1.5, 1.0, True, False, '1', 'nan', None])
def test_schema_rejects_a_bad_dca_n(n):
    c = _dca_case()
    c['slot']['dca']['n'] = n
    with pytest.raises(schema.CaseError, match='slot.dca.n'):
        schema.validate(c)


@pytest.mark.parametrize('key', ['step_atr', 'scale', 'tp_atr', 'stop_atr'])
@pytest.mark.parametrize('v', ['0', '0.0', '-1', '101', '1e9', '1E2', 'inf', 'Infinity', 'nan', '+1', ' 1', '1.', '.5',
                               '01', '', 1, 1.5, 0.5, True, None])
def test_schema_rejects_a_bad_dca_multiple(key, v):
    c = _dca_case()
    c['slot']['dca'][key] = v
    with pytest.raises(schema.CaseError, match=f'slot.dca.{key}'):
        schema.validate(c)


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'not_a_dict'])
def test_schema_rejects_a_malformed_dca_block(mutation):
    c = _dca_case()
    if mutation == 'missing':
        del c['slot']['dca']['tp_atr']
    elif mutation == 'extra':
        c['slot']['dca']['frac'] = '0.5'
    else:
        c['slot']['dca'] = [1, '1', '1', '1', '1']
    with pytest.raises(schema.CaseError, match='slot.dca'):
        schema.validate(c)


@pytest.mark.parametrize('n,v', [(1, '1'), (3, '1.5'), (10, '100'), (1, '0.25')])
def test_schema_accepts_valid_dca_values(n, v):
    c = _dca_case()
    c['slot']['dca'].update(n=n, scale=v)
    schema.validate(c)


# ------------------------------------------------------------------ behaviour registry (Codex golden r3 residual ruling, 1)
def test_behaviour_registry_is_versioned_and_append_only():
    """One meaning per ID: the published prefix (IDs, order AND meanings) is pinned; an ID is deprecated, never removed,
    renamed, reordered or re-worded into another meaning."""
    assert schema.BEHAVIOURS_VERSION == PINNED_BEHAVIOURS_VERSION, 'a new registry version starts a new pinned prefix'
    assert schema.BEHAVIOURS[:len(PINNED_BEHAVIOURS_V1)] == PINNED_BEHAVIOURS_V1, \
        'the behaviour registry is append-only: a pinned ID was removed, renamed or reordered (deprecate it instead)'
    pairs = [[b, schema.BEHAVIOUR_MEANING[b]] for b in PINNED_BEHAVIOURS_V1]
    assert schema.sha256(pairs) == PINNED_BEHAVIOURS_V1_SHA, \
        'the meaning of a pinned behaviour ID changed: an ID keeps its one meaning (append a new ID instead)'
    assert len(set(schema.BEHAVIOURS)) == len(schema.BEHAVIOURS)
    for b in schema.BEHAVIOURS:
        assert BEHAVIOUR_ID.fullmatch(b), b
        assert len(schema.BEHAVIOUR_MEANING[b].strip()) >= 20, f'{b}: every behaviour ID states its meaning'
    for old, new in schema.DEPRECATED_BEHAVIOURS.items():
        assert old in schema.BEHAVIOURS and new in schema.BEHAVIOURS and new not in schema.DEPRECATED_BEHAVIOURS, (old, new)


def test_the_registry_file_is_what_schema_serves():
    """schema.BEHAVIOUR_MEANING is read from BEHAVIOURS.json (no second in-code copy that could drift from the anchored file)."""
    doc = registry.read(registry.BEHAVIOURS_PATH)
    assert doc['schema'] == schema.BEHAVIOURS_VERSION
    assert [(e['id'], e['meaning']) for e in doc['behaviours']] == list(schema.BEHAVIOUR_MEANING.items())
    assert doc['deprecated'] == schema.DEPRECATED_BEHAVIOURS


def _beh_base():
    return copy.deepcopy(registry.read(registry.BEHAVIOURS_PATH))


def test_behaviour_registry_extension_controls():
    """Codex golden r5 ruling #1, positive controls: an unchanged registry, an appended ID with its meaning, and an appended
    deprecation extend the base."""
    base = _beh_base()
    registry.extends_behaviours(base, copy.deepcopy(base))
    now = copy.deepcopy(base)
    now['behaviours'].append({'id': 'new_causal_behaviour', 'meaning': 'a new behaviour appended with its one meaning'})
    registry.extends_behaviours(base, now)
    now['deprecated'] = {'entry': 'new_causal_behaviour'}
    registry.extends_behaviours(base, now)


@pytest.mark.parametrize('mutation', ['meaning_changed', 'meaning_whitespace', 'reordered', 'removed', 'renamed', 'inserted',
                                      'version', 'deprecation_dropped', 'duplicate_id', 'duplicate_json_key', 'short_meaning',
                                      'extra_key'])
def test_behaviour_registry_extension_mutations(mutation):
    """Codex golden r5 ruling #1: against the base registry (a synthetic stand-in for the base tree's copy), a changed
    meaning - whatever the local pin says - a reorder, a removal, a rename, an insertion before a base ID, a version swap or a
    dropped deprecation fails."""
    base = _beh_base()
    base['deprecated'] = {'entry': 'stop'}
    now = copy.deepcopy(base)
    bs = now['behaviours']
    if mutation == 'meaning_changed':
        bs[0]['meaning'] = 'the stop of the position, reworded into another meaning entirely'
    elif mutation == 'meaning_whitespace':
        bs[3]['meaning'] += ' '
    elif mutation == 'reordered':
        bs[0], bs[1] = bs[1], bs[0]
    elif mutation == 'removed':
        bs.pop(5)
    elif mutation == 'renamed':
        bs[2]['id'] = 'take_profit'
    elif mutation == 'inserted':
        bs.insert(0, {'id': 'new_causal_behaviour', 'meaning': 'a new behaviour inserted before the base prefix'})
    elif mutation == 'version':
        now['schema'] = 'zb-golden-behaviours/2'
    elif mutation == 'deprecation_dropped':
        now['deprecated'] = {}
    elif mutation == 'duplicate_id':
        bs.append(copy.deepcopy(bs[0]))
    elif mutation == 'short_meaning':
        bs.append({'id': 'new_causal_behaviour', 'meaning': 'too short'})
    elif mutation == 'extra_key':
        bs.append({'id': 'new_causal_behaviour', 'meaning': 'a new behaviour with an unknown key', 'note': 'x'})
    if mutation == 'duplicate_json_key':
        with pytest.raises(registry.RegistryError, match='duplicate JSON keys'):
            registry.loads('{"schema": "zb-golden-behaviours/1", "schema": "zb-golden-behaviours/1"}')
        return
    with pytest.raises(registry.RegistryError):
        registry.extends_behaviours(base, now)


def test_every_registered_behaviour_is_used_by_the_pack():
    """The registry is the pack's vocabulary, not a wish list: an ID no case uses is either deprecated or a typo'd addition."""
    used = {b for c in schema.load_all() for b in c['behaviours']}
    assert set(schema.BEHAVIOURS) - set(schema.DEPRECATED_BEHAVIOURS) <= used, \
        sorted(set(schema.BEHAVIOURS) - set(schema.DEPRECATED_BEHAVIOURS) - used)


@pytest.mark.parametrize('mutation', ['unknown', 'duplicate', 'empty', 'not_a_list', 'not_a_string', 'case_variant'])
def test_schema_rejects_behaviours_outside_the_registry(mutation):
    c = copy.deepcopy(schema.load(schema.case_paths()[0]))
    b = c['behaviours']
    c['behaviours'] = {'unknown': b + ['new_causal_behaviour'], 'duplicate': b + [b[0]], 'empty': [], 'not_a_list': b[0],
                       'not_a_string': b + [None], 'case_variant': [b[0].upper()] + b[1:]}[mutation]
    with pytest.raises(schema.CaseError, match='behaviours'):
        schema.validate(c)


def test_behaviours_and_divergence_ids_are_inside_the_contract_hash():
    """Codex golden r3 residual ruling: nothing the per-commit tier gate reads is unhashed. Removing / adding / swapping a
    behaviour or renaming a divergence_id moves contract_sha (so it needs a ledger record); title / notes / provenance do not."""
    assert schema.DESCRIPTIVE == ('title', 'provenance', 'notes')
    c = _kd_case()
    h = schema.contract_sha(c)
    for mut in (lambda x: x['behaviours'].append('short_side'), lambda x: x['behaviours'].clear(),
                lambda x: x['behaviours'].__setitem__(0, 'stop'),
                lambda x: x['known_divergences'][0].__setitem__('divergence_id', 'AUD07-C11B'),
                lambda x: x['known_divergences'][0].__setitem__('finding', 'C11 reworded')):
        m = copy.deepcopy(c)
        mut(m)
        assert schema.contract_sha(m) != h
    for k in schema.DESCRIPTIVE:
        assert schema.contract_sha(dict(c, **{k: 'changed'})) == h, k


# ------------------------------------------------------------------ divergence identity (Codex golden r3 residual ruling, 2)
def _kd_case(cid='G-TIME-L-02'):
    return copy.deepcopy(schema.load(os.path.join(os.path.dirname(schema.case_paths()[0]), cid + '.json')))


def test_every_known_divergence_has_a_stable_id():
    ids = {}
    for c in schema.load_all():
        for d in c['known_divergences']:
            assert schema.DIVERGENCE_ID.fullmatch(d['divergence_id']), (c['id'], d['divergence_id'])
            ids.setdefault(d['divergence_id'], set()).add((d['ticket'], d['finding']))
    assert ids, 'the pack records known divergences'
    # one meaning per ID across the pack: every occurrence of an ID carries the same recorded defect (ticket, finding)
    many = {i: sorted(v) for i, v in ids.items() if len(v) > 1}
    assert not many, f'a divergence_id names more than one recorded defect (ticket, finding): {many}'


@pytest.mark.parametrize('did', [None, '', 'aud07-c11', 'AUD07', 'AUD07_C11', 'AUD07-C11 ', '-C11', 'AUD07--C11', 'AUD07-', 7])
def test_schema_rejects_a_malformed_divergence_id(did):
    c = _kd_case()
    c['known_divergences'][0]['divergence_id'] = did
    with pytest.raises(schema.CaseError, match='divergence_id'):
        schema.validate(c)


def test_schema_rejects_a_missing_divergence_id():
    c = _kd_case()
    c['known_divergences'][0].pop('divergence_id')
    with pytest.raises(schema.CaseError, match='divergence_id'):
        schema.validate(c)


def test_schema_rejects_a_duplicate_divergence_id_on_one_adapter():
    """The uniqueness rule names the duplicate (it runs before the one-entry-per-adapter rule)."""
    c = _kd_case()
    c['known_divergences'].append(copy.deepcopy(c['known_divergences'][0]))
    with pytest.raises(schema.CaseError, match='divergence_id listed more than once on one adapter'):
        schema.validate(c)


def test_the_same_divergence_id_on_two_adapters_is_valid():
    """G-TIME-L-02: AUD07-C11 is recorded on both legacy adapters - unique per adapter, not per case."""
    c = _kd_case()
    assert sorted((d['adapter'], d['divergence_id']) for d in c['known_divergences']) == \
        [('legacy_backtest', 'AUD07-C11'), ('legacy_engine', 'AUD07-C11')]
    schema.validate(c)


# ------------------------------------------------------------------ divergence registry (Codex golden r5 ruling, 2)
def test_divergence_registry_matches_the_pack():
    """DIVERGENCES.json is the pack's defect vocabulary: every ACTIVE ID is recorded by at least one case (a defect no case
    records any more is retired), and no case records a retired one (schema.validate rejects it)."""
    used = {d['divergence_id'] for c in schema.load_all() for d in c['known_divergences']}
    active = {i for i, e in schema.DIVERGENCE_REGISTRY.items() if e['status'] == 'active'}
    assert used <= active, sorted(used - active)
    assert active <= used, f'active divergence IDs no case records (retire them): {sorted(active - used)}'


@pytest.mark.parametrize('mutation', ['unregistered', 'retired', 'other_adapter', 'other_ticket', 'other_finding'])
def test_schema_rejects_a_divergence_outside_the_registry(mutation):
    """A case cannot reference an unregistered or retired ID, use it on an adapter outside its scope, or give it another
    meaning (ticket / finding) than the registry binds it to."""
    c = _kd_case()
    d = c['known_divergences'][0]
    reg = copy.deepcopy(schema.DIVERGENCE_REGISTRY)
    if mutation == 'unregistered':
        d['divergence_id'] = 'AUD07-C11B'
    elif mutation == 'retired':
        reg[d['divergence_id']]['status'] = 'retired'
    elif mutation == 'other_adapter':
        reg[d['divergence_id']]['adapters'] = [a for a in reg[d['divergence_id']]['adapters'] if a != d['adapter']]
    elif mutation == 'other_ticket':
        d['ticket'] = 'AUD-7'
    elif mutation == 'other_finding':
        d['finding'] = 'C11 reworded'
    with pytest.raises(schema.CaseError, match='divergence_id'):
        schema._validate_divergence_ref(d, c['id'], reg)
    if mutation in ('unregistered', 'other_ticket', 'other_finding'):         # the same through the full validation
        with pytest.raises(schema.CaseError, match='divergence_id'):
            schema.validate(c)


def _div_base():
    return copy.deepcopy(registry.read(registry.DIVERGENCES_PATH))


def _new_div(i='AUD08-NEW-DEFECT', **kw):
    return dict(dict(id=i, adapters=['legacy_engine'], ticket='AUD-08', finding='a newly recorded defect', status='active'), **kw)


def test_divergence_registry_extension_controls():
    """Codex golden r5 ruling #2, positive controls: unchanged, an appended ID, a retirement (active -> retired), and an
    appended ID next to a retired one all extend the base."""
    base = _div_base()
    ext = lambda now: registry.extends_divergences(base, now, schema.ADAPTERS)
    ext(copy.deepcopy(base))
    now = copy.deepcopy(base)
    now['divergences'].append(_new_div())
    ext(now)
    now['divergences'][0]['status'] = 'retired'
    ext(now)
    ret = copy.deepcopy(base)
    ret['divergences'][0]['status'] = 'retired'
    now2 = copy.deepcopy(ret)
    now2['divergences'].append(_new_div())
    registry.extends_divergences(ret, now2, schema.ADAPTERS)                  # a retired base entry stays retired


@pytest.mark.parametrize('mutation', ['rename', 'rebind_ticket', 'rebind_finding', 'rebind_scope', 'removed', 'reordered',
                                      'inserted', 'unretire', 'reuse_retired', 'reuse_active', 'second_id_same_defect',
                                      'second_id_same_defect_after_retire', 'bad_status', 'version', 'unsorted_scope',
                                      'unknown_adapter', 'extra_key', 'malformed_id'])
def test_divergence_registry_extension_mutations(mutation):
    """Codex golden r5 ruling #2: against the base registry, a rename, a semantic rebinding (ticket, finding or scope), a
    removal, a reorder, an insertion, an un-retirement, the reuse of a retired (or active) ID and a second ID for an already
    recorded defect all fail."""
    base = _div_base()
    base['divergences'][1]['status'] = 'retired'
    now = copy.deepcopy(base)
    ds = now['divergences']
    if mutation == 'rename':
        ds[0]['id'] = 'AUD07-C11B'
    elif mutation == 'rebind_ticket':
        ds[0]['ticket'] = 'AUD-08'
    elif mutation == 'rebind_finding':
        ds[0]['finding'] = 'C11 (another defect)'
    elif mutation == 'rebind_scope':
        ds[0]['adapters'] = ['legacy_engine']
    elif mutation == 'removed':
        ds.pop(2)
    elif mutation == 'reordered':
        ds[2], ds[3] = ds[3], ds[2]
    elif mutation == 'inserted':
        ds.insert(0, _new_div())
    elif mutation == 'unretire':
        ds[1]['status'] = 'active'
    elif mutation == 'reuse_retired':
        ds.append(_new_div(ds[1]['id']))
    elif mutation == 'reuse_active':
        ds.append(_new_div(ds[0]['id']))
    elif mutation == 'second_id_same_defect':
        ds.append(_new_div('AUD07-C11B', ticket=ds[0]['ticket'], finding=ds[0]['finding']))
    elif mutation == 'second_id_same_defect_after_retire':
        ds[0]['status'] = 'retired'
        ds.append(_new_div('AUD07-C11B', ticket=ds[0]['ticket'], finding=ds[0]['finding'], adapters=ds[0]['adapters']))
    elif mutation == 'bad_status':
        ds[0]['status'] = 'deprecated'
    elif mutation == 'version':
        now['schema'] = 'zb-golden-divergences/2'
    elif mutation == 'unsorted_scope':
        ds.append(_new_div(adapters=['legacy_engine', 'legacy_backtest']))
    elif mutation == 'unknown_adapter':
        ds.append(_new_div(adapters=['legacy_engine2']))
    elif mutation == 'extra_key':
        ds.append(dict(_new_div(), until='later'))
    elif mutation == 'malformed_id':
        ds.append(_new_div('aud08_new'))
    with pytest.raises(registry.RegistryError):
        registry.extends_divergences(base, now, schema.ADAPTERS)
