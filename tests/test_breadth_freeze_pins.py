"""Breadth freeze (Codex 6200896386, ruling e): the frozen breadth runner c8c7b92, its bounded collector, the code it
executes and the Static-40 universe are pinned by SHA-256. Any byte drift fails here; a change needs a new reviewed
freeze (and, once registered, a new preregistration: no retune of a registered hypothesis).

Hashes are of the REGISTERED bytes: the blobs of the registration commit Y behind the annotated tag
(tests/breadth_registered.py, Codex ruling), never the mutable working tree, so a post-registration edit on master
cannot fail - or satisfy - these pins. A missing / moved / lightweight tag fails closed.
"""
import json
import os

import pytest

from breadth_registered import assert_pinned, registered_blob


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FROZEN_COMMIT = 'c8c7b92'          # the runner commit every breadth packet of #105-#114 was produced with

PINS = {
    # brought onto master byte-identical to c8c7b92
    'tools/research/run_breadth_candidate.py': 'dad619aaf2df00fd79ce9a2ca2d4848f298b745920fe7f6c56898e7b4f6922f2',
    'tools/research/collect_klines_research.py': 'b6a2a70a59d805f67541be42019e393a6677a954e0be889bfb06eb1f66a46856',
    # executed by the runner / collector; identical at c8c7b92 and master 3904cde (engine.py: see below)
    'strategies.py': '0e28e823894ecadf5bc1d634f4f9fa5e3fe934b4f2a53ae68386d7c6cce27c18',
    'backtest.py': '40456e94741854350101547b519f2abbeee922ff2a489afcfefef986cb09bc75',
    # engine.py re-frozen to accepted master 9206f17: #117 (breadth_rotation per-key mgmt overlay) intentionally
    # changed it (was 088b2827... at c8c7b92 / 3904cde). Dev-window parity of the 13 Static-40 slices at this head vs
    # the #114 packets is the review evidence for the re-freeze (Codex issue-13 6204084063).
    'engine.py': '40ae636eab315d5b844aea0692f3233791aafc3227807349d6da059d7ef57a32',
    'tools/research/run_vwaprev_candidate.py': 'c2f43564cc1da4cd79e12253ece82d10b3f82c1ecdbbc11aeafa80202060d23e',
    'market_data.py': 'aa16e76fdadfd1d39d3cb4cd4e8d144044aea750e8ba44ae1348c644e6440148',
    # the frozen Static-40 universe (owner-approved list)
    'research_evidence/universe/static-top40-minhist365-contract-20261011.json':
        'd3460e6db3dca9cc4a629693b63d0b476f6ceaebf618283aed302da7f095b031',
}
UNIVERSE = 'research_evidence/universe/static-top40-minhist365-contract-20261011.json'
UNIVERSE_ID = 'static-top40-minhist365-contract-20261011'
UNIVERSE_DIGEST = '1c06c18d2fc82413b740260f0051a9f8286e9b0cb87d7d9614a2f88d78393fce'
UNIVERSE_LIST_DIGEST = 'd95ba63c126029e23196e253c74cd95918cb5c9102079792194c972d20884bc5'
# Late listings / gaps inside the 4h warm-up + development window (#113 manifest): INCOMPLETE, excluded, never filled.
STATIC40_INCOMPLETE = ('AKEUSDT', 'BRUSDT', 'HYPEUSDT', 'MUBARAKUSDT', 'PENGUUSDT', 'PUMPUSDT', 'TRUMPUSDT')


@pytest.mark.parametrize('rel', sorted(PINS))
def test_frozen_file_sha256(rel):
    assert_pinned(ROOT, rel, PINS[rel])     # Y's registered (LF) bytes vs the breadth freeze (FROZEN_COMMIT lineage)


def test_static40_universe_identity():
    u = json.loads(registered_blob(ROOT, UNIVERSE))
    assert (u['universe_id'], u['digest'], u['list_digest'], u['owner_approved']) == (
        UNIVERSE_ID, UNIVERSE_DIGEST, UNIVERSE_LIST_DIGEST, True)
    syms = [x['symbol'] for x in u['symbols']]
    assert len(syms) == 40 == len(set(syms)) and [x['rank'] for x in u['symbols']] == list(range(1, 41))
    assert set(STATIC40_INCOMPLETE) <= set(syms)


def test_runner_keeps_frozen_guards():
    """The pinned runner still carries the sealed cutoff and the bounded collector its exclusive end (belt and braces:
    the sha pin already fixes the bytes; this names what the pin protects)."""
    import sys
    sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
    import run_breadth_candidate as B
    import collect_klines_research as K
    assert B.SEALED_CUTOFF == '2026-09-28T00:00:00Z'
    assert K.FORMAT == 'zb-klines-research/1'
