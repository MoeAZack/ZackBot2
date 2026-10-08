"""AUD-08 exit-code vocabulary: a stable machine registry, and every legacy exit reason mapped to exactly one code.

Registry rules (schema.EXIT_CODE_MEANING): append-only within a schema version - a code is never removed, renamed, reordered
or reused for another meaning; it is deprecated instead (schema.DEPRECATED_EXIT_CODES) and stays valid. Free text (titles,
reasons, notes) is display only and never compared. Mutations that must fail here: drop / rename / reorder a pinned code,
fold stop_crossed back into STOP_HIT, add an engine.py / backtest.py exit reason without mapping it."""
import copy, os, re, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from goldenlib import REPO_ROOT, schema  # noqa: E402
from goldenlib.adapters import legacy_backtest, legacy_engine  # noqa: E402

# zb-golden/1 registry as published. Append new codes to schema.EXIT_CODE_MEANING (and here); never edit this prefix.
PINNED_V1 = ('STOP_HIT', 'TIME_EXIT', 'SIGNAL_EXIT', 'TP_FULL', 'TP_BASKET', 'TP_PARTIAL', 'TP_LADDER', 'LIQUIDATED', 'FLATTEN',
             'STOP_CROSSED', 'RESYNC', 'STOP_FAILED', 'BASKET_TP_PART')
CODE = re.compile(r'[A-Z][A-Z0-9_]*')


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
