"""C268 metadata only: no source, claim or economic authority."""
import hashlib
import json
from pathlib import Path

import pytest
from tools.research import c268_prereg as C


def test_accepted_metadata_byte_bindings_clarification_and_locked_scope():
    doc = C.load_metadata()
    assert doc['root_review']['reference'] == C.REVIEW
    assert doc['pre_data_clarifications']['minimum_fit_count_scope'] == 'STATISTICALLY_DEFINED_BEFORE_POSITIVE_ELIGIBILITY'
    assert doc['mechanism_risk']['simple_48h_basis_points'] == '3.287671232876712328767123288'
    assert doc['mechanism_risk']['base_round_trip_bps'] == 14
    assert doc['statistics_clarification']['largest_null_exceedance_count_passing'] == 2
    assert not any(doc[k] for k in ('execution_authorized', 'source_authority_bound', 'paper_qualified', 'forward_authorized', 'holdout_authorized'))
    with pytest.raises(C.C268Locked):
        C.assert_execution_authorized()


@pytest.mark.parametrize('file', ['nomination.v1.json', 'prereg_review_draft.v1.json', 'preregistration.v1.json'])
def test_byte_drift_refused_before_any_loader(file, tmp_path, monkeypatch):
    for name in ('nomination.v1.json', 'prereg_review_draft.v1.json', 'preregistration.v1.json'):
        path = tmp_path / C.DIRECTORY / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((C.ROOT / C.DIRECTORY / name).read_bytes())
    path = tmp_path / C.DIRECTORY / file
    path.write_bytes(path.read_bytes() + b' ')
    monkeypatch.setattr(C, 'ROOT', tmp_path)
    with pytest.raises(C.C268Locked, match='BYTE_DRIFT'):
        C.load_metadata()


def test_accepted_nomination_and_review_draft_exact_bytes_preserved():
    for name, digest in (('nomination.v1.json', C.NOMINATION_SHA256), ('prereg_review_draft.v1.json', C.DRAFT_SHA256)):
        assert hashlib.sha256((C.ROOT / C.DIRECTORY / name).read_bytes()).hexdigest() == digest
    original = json.loads((C.ROOT / C.DIRECTORY / 'nomination.v1.json').read_bytes())
    assert C.load_metadata()['specification'] == original
