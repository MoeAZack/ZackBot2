"""Cowork 6063794395 item 2: the 20k fuzz (289 unprotected-in-HOLD) and NEW-3 (orphan stop on a flat side),
confirmed with a seeded systematic fuzz over the same scenario space (fuzz_s1.py; Cowork's own scripts are not in the
repo). The gate runs a fixed slice of seeds; the full 20k is `fuzz_run.py 0 20000` (the result is in the S1 report)."""
import collections
import os

import pytest

from fuzz_s1 import run_seed

SEEDS = int(os.environ.get('NC_FUZZ_SEEDS', '600'))


def test_fuzz_s1_no_naked_exposure_no_orphan_stop_no_entry_in_hold():
    hist, fails = collections.Counter(), []
    for s in range(SEEDS):
        v, p, d = run_seed(s)
        hist[v] += 1
        if v.startswith('FAIL'):
            fails.append((s, v, p, d))
    assert not fails, fails[:3]
    # not vacuous: the space reaches covered exposure, flat endings and the surfaced unprovable contract
    assert hist['ok_covered'] and hist['ok_flat'] and hist['surfaced'], dict(hist)


@pytest.mark.parametrize('seed', range(3))
def test_fuzz_s1_is_deterministic(seed):
    assert run_seed(seed)[0] == run_seed(seed)[0]


# the 20k seeds a recovered (adopted) entry's trades window missed its own fill: the entry intent is recorded AFTER
# it filled, so the window must start at its proven fill (Runner._lots_since) - never UNKNOWN ownership from that
REGRESSION = (2426, 2858, 2905, 4571, 4596, 4662, 4692, 5851, 6336, 6422, 6582, 7671, 7685, 8629, 9379)


@pytest.mark.parametrize('seed', REGRESSION)
def test_fuzz_s1_regression_seeds(seed):
    v, p, d = run_seed(seed)
    assert not v.startswith('FAIL'), (p, d)
