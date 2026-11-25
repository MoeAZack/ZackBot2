"""Synthetic proofs for matured chronological path matching."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v133 as M

PLANTED_MUTANTS = [
    ('include_path_overlapping_decision', 'i - 248, i - 8', 'i - 243, i - 3',
     'test_matches_earlier_nonoverlapping_paths_only'),
    ('use_farthest_analogues', "np.argsort(distance, kind='stable')[:15]", "np.argsort(distance, kind='stable')[-15:]",
     'test_matches_earlier_nonoverlapping_paths_only'),
    ('allow_partial_sample', 'len(source) < 200', 'len(source) < 100',
     'test_known_sample_floor'),
]


def test_matches_earlier_nonoverlapping_paths_only():
    rng = np.random.default_rng(133)
    paths = rng.normal(size=(350, 5))
    labels = np.linspace(.01, .50, len(paths))
    current = 300
    paths[current-4] = paths[current]
    labels[current-4] = -100
    source = range(current-248, current-8)
    nearest = sorted(source, key=lambda j: np.linalg.norm(paths[j]-paths[current]))[:15]
    valid = labels[nearest]
    actual = M.analogue_statistics(paths, labels)[current]
    np.testing.assert_allclose(actual, [np.median(valid), (valid>0).sum(), (valid<0).sum()])


def test_known_sample_floor():
    paths = np.ones((329, 5))
    labels = np.ones(len(paths))
    labels[80:140] = np.nan
    assert np.isnan(M.analogue_statistics(paths, labels)[300]).all()


def test_price_prefix_and_default_off():
    rng = np.random.default_rng(733)
    price = pd.Series(100*np.exp(rng.normal(0, .02, 350).cumsum()))
    full = M.analogue_statistics(*M.history_features(price))
    for cut in (200, 250, 290, 320):
        np.testing.assert_array_equal(M.analogue_statistics(*M.history_features(price.iloc[:cut])), full[:cut])
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'both'
