"""REX-01 fixture registry: the synthetic acceptance set must be exactly the frozen 208 cases, by group (v5 section 8
+ addendum P1-P4: F13 20 -> 21, F16 6 -> 11; Codex freeze 6161875941). Every registered case is a parametrized
pytest case in the test_range_vwap_rev_* modules."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rex_synth as S                                                                       # noqa: E402

MODULES = ('test_range_vwap_rev_book', 'test_range_vwap_rev_funding', 'test_range_vwap_rev_overlay',
           'test_range_vwap_rev_notice', 'test_range_vwap_rev_eval')
FROZEN = {'F1': 14, 'F2': 10, 'F3': 18, 'F4': 14, 'F5': 34, 'F6': 12, 'F7': 8, 'F8': 10, 'F9': 14, 'F10': 12,
          'F11': 10, 'F12': 4, 'F13': 21, 'F14': 6, 'F15': 10, 'F16': 11}


def test_fixture_registry_is_the_frozen_208_by_group():
    for m in MODULES:
        importlib.import_module(m)
    got = {g: len(set(v)) for g, v in S.REGISTRY.items()}
    assert all(len(set(v)) == len(v) for v in S.REGISTRY.values())          # no duplicate case ids
    assert got == FROZEN and sum(FROZEN.values()) == 208
