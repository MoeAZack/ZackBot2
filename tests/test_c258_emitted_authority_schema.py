import copy
import json

import pytest

from tools.research import authority_v6 as V6
from tools.research import cross_sectional_relative_strength_continuation_c258_stagea as C


def emitted_docs():
    plan = V6.load_frozen_plan()
    base = json.loads((C.ROOT / plan["base_authorities"]["pit_v8"]["path"]).read_bytes())
    pit = copy.deepcopy(base)
    for monday in V6.NEW_CELLS:
        cell = copy.deepcopy(base["weeks"][-1])
        cell["monday_ms"] = monday
        pit["weeks"].append(cell)
    pit["manifest_digest"] = "a" * 64
    pit["digest"] = V6.U.digest_of(pit)
    pins = C.AuthorityPins("f" * 40, *(
        C.ArtifactPin(path, "b" * 64, digest)
        for path, digest in zip(C.EXPECTED_AUTHORITY_PATHS.values(),
                               ("a" * 64, pit["digest"], "c" * 64, "d" * 64))))
    streams = ["klines:1d", "klines:1h", "markPriceKlines:1h", "fundingRate:null"]
    return {
        "manifest_v6": {"authority_id": C.AUTHORITY_ID, "cutoff_exclusive_ms": V6.CUTOFF_MS},
        "pit_v9": pit,
        "filter_v4": {"pit_universe_path": pins.pit_v9.path,
                      "pit_universe_sha256": pins.pit_v9.byte_sha256,
                      "pit_universe_digest": pins.pit_v9.content_digest,
                      "fold_bindings": plan["acquisition_sequence"][5]["fold_bindings"]},
        "coverage_v4": {
            "format": "zb-strategy-causal-pack-01-authority-coverage-preflight/1",
            "authority_version": 4, "cutoff_exclusive_ms": V6.CUTOFF_MS,
            "required_result": "ZERO_GUARANTEED_PERIOD_GAPS", "required_streams": streams,
            "guaranteed_period_gaps": 0, "guaranteed_period_gaps_by_stream": dict.fromkeys(streams, 0),
            "gaps": [], "manifest_digest": "a" * 64, "pit_universe_digest": pit["digest"],
            "filter_authority_digest": "c" * 64}}, pins


def test_real_builder_schema_accepts_without_synthetic_aliases():
    docs, pins = emitted_docs()
    C._validate_emitted_v6(docs, pins, root=C.ROOT)


@pytest.mark.parametrize("field,value", [
    ("manifest_digest", "e"*64), ("pit_universe_digest", "e"*64),
    ("filter_authority_digest", "e"*64), ("guaranteed_period_gaps", 1),
    ("gaps", [{"stream": "fundingRate:null"}]), ("required_streams", []),
])
def test_real_schema_refuses_wrong_crossbinding_or_gap(field, value):
    docs, pins = emitted_docs()
    docs["coverage_v4"][field] = value
    with pytest.raises(C.IncompleteAuthority):
        C._validate_emitted_v6(docs, pins, root=C.ROOT)


def test_changed_historical_pit_prefix_fails_even_with_new_digest():
    docs, pins = emitted_docs()
    docs["pit_v9"]["weeks"][0]["monday_ms"] += V6.HOUR_MS
    docs["pit_v9"]["digest"] = V6.U.digest_of(docs["pit_v9"])
    with pytest.raises(C.IncompleteAuthority, match="prefix"):
        C._validate_emitted_v6(docs, pins, root=C.ROOT)
