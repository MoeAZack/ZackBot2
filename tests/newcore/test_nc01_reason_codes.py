"""NC-01 reason-code registry (contract section 4, ruling 6): append-only pinned order, semantic entries, namespaces,
the legacy gate seed, explicit golden exit / signal mapping, and a scan proving no literal reason bypasses the registry."""
import ast
import os
import re

import pytest

from newcore.domain import GOLDEN_EXIT, GOLDEN_EXIT_CODES, GOLDEN_SIGNAL, MEANING, ReasonCode
from newcore.domain.reasons import DEPRECATED, NAMESPACES, golden_exit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CODE_RE = re.compile(r'[a-z][a-z_]*\.[a-z0-9][a-z0-9_]*')


def _pinned():
    return [ln for ln in open(os.path.join(HERE, 'reason_codes_v1.txt'), encoding='ascii').read().splitlines()
            if ln and not ln.startswith('#')]


def test_registry_is_append_only_and_pinned():
    """Rename, removal, reuse or insertion anywhere but the end fails; new codes are appended to the file too."""
    assert [r.value for r in ReasonCode] == _pinned()


def test_codes_are_lowercase_dotted_unique_and_namespaced():
    values = [r.value for r in ReasonCode]
    assert len(values) == len(set(values))
    for r in ReasonCode:
        assert CODE_RE.fullmatch(r.value), r
        assert r.namespace in NAMESPACES, r
    assert {r.namespace for r in ReasonCode} == set(NAMESPACES)


def test_contract_namespace_catalog_is_covered():
    catalog = {'ownership': {'ownership'}, 'binding': {'binding'}, 'lifecycle': {'lifecycle', 'trailing'},
               'exchange evidence': {'evidence'}, 'protection': {'protect'},
               'risk/capacity': {'risk_gateway', 'capacity'}, 'strategy decision': {'entry', 'exit'},
               'recovery': {'recovery'}, 'reconciliation': {'reconcile'}, 'operator authority': {'operator'}}
    have = {r.namespace for r in ReasonCode}
    for name, spaces in catalog.items():
        assert spaces <= have, name


def test_every_code_has_a_semantic_entry():
    assert set(MEANING) == set(ReasonCode)
    assert all(isinstance(v, str) and len(v) > 10 for v in MEANING.values())


def test_deprecated_codes_stay_reserved():
    for code, repl in DEPRECATED.items():
        assert code in ReasonCode and code.deprecated and MEANING[code].startswith('DEPRECATED')
        assert repl is None or repl in ReasonCode
    assert not ReasonCode.EXIT_STOP.deprecated


def test_legacy_unmapped_values_cannot_enter():
    for bad in ('UNMAPPED:flatten', 'other.other', 'STOP_HIT', 'exit.STOP', 'exit.'):
        with pytest.raises(ValueError):
            ReasonCode(bad)


# ----------------------------------------------------------------------------------------------------------- legacy seed
def _assigned_literal(path, name):
    tree = ast.parse(open(path, encoding='utf-8').read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
    return None


def test_legacy_gate_reasons_seed_the_registry():
    """Every legacy trade_audit reason_info stage/code (and every engine risk rule) has the same-valued code."""
    import trade_audit as TA
    pairs = set(TA._EXACT.values()) | {(s, c) for _, s, c in TA._PREFIX} | {(s, c) for _, s, c, _n in TA._WRAP if c}
    pairs.add(('risk_gateway', 'not_tradable'))
    rules = _assigned_literal(os.path.join(ROOT, 'engine.py'), 'RISK_RULE_DEFAULTS')
    assert isinstance(rules, ast.Call) and rules.keywords
    pairs |= {('risk_gateway', kw.arg) for kw in rules.keywords}
    values = {r.value for r in ReasonCode}
    missing = sorted(f'{s}.{c}' for s, c in pairs if f'{s}.{c}' not in values)
    assert not missing, missing
    assert TA.reason_info('trailing entry cancelled: max positions reached (2)')['code'] == 'max_positions'


# ----------------------------------------------------------------------------------------------------------- golden
def _golden_vocab():
    path = os.path.join(ROOT, 'tests', 'golden', 'goldenlib', 'schema.py')
    meaning = _assigned_literal(path, 'EXIT_CODE_MEANING')
    codes = [k.value for k in meaning.keys] if isinstance(meaning, ast.Dict) else \
        list(ast.literal_eval(_assigned_literal(path, 'EXIT_CODES')))
    signals = list(ast.literal_eval(_assigned_literal(path, 'SIGNAL_KINDS')))
    reason = ast.literal_eval(_assigned_literal(os.path.join(ROOT, 'tests', 'golden', 'goldenlib', 'adapters',
                                                             'legacy_engine.py'), 'REASON'))
    return codes, signals, reason


def test_every_exit_code_maps_explicitly():
    exits = {r for r in ReasonCode if r.namespace == 'exit'}
    assert set(GOLDEN_EXIT) == exits
    assert {v for v in GOLDEN_EXIT.values() if v is not None} == set(GOLDEN_EXIT_CODES)    # every golden code has a preimage
    with pytest.raises(KeyError):
        golden_exit(ReasonCode.ENTRY_SIGNAL)


def test_expanded_vocabulary_is_distinct():
    assert golden_exit('exit.stop') == 'STOP_HIT' and golden_exit('exit.stop_crossed') == 'STOP_CROSSED'
    assert len({golden_exit(c) for c in ('exit.flatten', 'exit.resync', 'exit.stop_failed', 'exit.basket_tp_part',
                                         'exit.take_profit_1')}) == 5


def test_in_tree_golden_registry_is_a_pinned_prefix():
    """tests/golden's append-only EXIT_CODES must be a prefix of the pinned expanded registry (master has 9 codes,
    the AUD-08 expansion on golden-short-mirrors has all 13)."""
    codes, signals, reason = _golden_vocab()
    assert tuple(codes) == GOLDEN_EXIT_CODES[:len(codes)]
    assert set(signals) == set(GOLDEN_SIGNAL)
    for why, golden in reason.items():
        code = ReasonCode(f'exit.{why}')                      # every legacy journal exit has a NEWCORE code
        if GOLDEN_EXIT[code] in codes:                        # where the in-tree registry knows our projection, agree
            assert GOLDEN_EXIT[code] == golden, why


def test_golden_signals_map_to_reasons_and_sides():
    for kind, (code, side) in GOLDEN_SIGNAL.items():
        assert code.namespace == ('entry' if kind.startswith('enter') else 'exit')
        assert side == ('LONG' if kind.endswith('long') else 'SHORT')


# ----------------------------------------------------------------------------------------------------------- literal scan
def test_no_literal_reason_bypasses_the_registry():
    """Outside reasons.py no NEWCORE source may spell a reason code as a string: constructors and transitions must use
    ReasonCode members, so a typo or an unregistered code cannot slip in."""
    values = {r.value for r in ReasonCode}
    hits = []
    for dp, _, fns in os.walk(os.path.join(ROOT, 'newcore')):
        for fn in fns:
            if not fn.endswith('.py') or (fn == 'reasons.py' and dp.endswith('domain')):
                continue
            path = os.path.join(dp, fn)
            for node in ast.walk(ast.parse(open(path, encoding='utf-8').read())):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    s = node.value
                    if s in values or (CODE_RE.fullmatch(s) and s.split('.')[0] in NAMESPACES):
                        hits.append(f'{os.path.relpath(path, ROOT)}:{node.lineno} {s!r}')
    assert hits == []
