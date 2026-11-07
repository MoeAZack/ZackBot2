import copy
import hashlib
import json
from pathlib import Path

import pytest

from tools.research import strategy_causal_pack_01_archive as A


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = ROOT / "contracts" / "strategy_causal_pack_01" / "exchange_filter_authority.v3.json"
CONTRACT = ROOT / "contracts" / "strategy_causal_pack_01" / "strategy_causal_pack_01_contract.v1.json"


def _authority():
    return json.loads(AUTHORITY.read_text(encoding="utf-8"))


def _rebound_contract(authority):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    contract["data"].update(
        exchange_filter_authority_sha256=hashlib.sha256(AUTHORITY.read_bytes()).hexdigest(),
        pit_universe_path=authority["pit_universe_path"],
        pit_universe_sha256=authority["pit_universe_sha256"],
        pit_universe_digest=authority["pit_universe_digest"],
    )
    return contract


def test_v3_rebind_accepts_exact_pit_v8_and_preserves_all_filter_evidence():
    authority = _authority()
    contract = _rebound_contract(authority)
    result = A.load_fold_filter_authorities(
        AUTHORITY, contract["data"]["exchange_filter_authority_sha256"], ROOT, contract
    )
    assert tuple(result) == ("wf1", "wf2", "wf3")
    assert result["wf1"].snapshot_sha256 == authority["snapshots"][0]["sha256"]
    assert result["wf3"].snapshot_sha256 == authority["snapshots"][1]["sha256"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value["supersedes"].update(byte_sha256="0" * 64), "prior authority checksum"),
        (lambda value: value["snapshots"][0].update(captured_at="2024-12-12T00:00:00Z"), "immutable filter evidence"),
        (lambda value: value["fold_bindings"].update(wf1="2026-02-17T04:13:28Z"), "immutable filter evidence"),
        (lambda value: value["weekly_membership_sha256"].update(wf1="0" * 64), "immutable filter evidence"),
        (lambda value: value["rebind_scope"].update(candidate_outcomes_read=True), "PIT-identity-only"),
        (lambda value: value["supersedes"].update(path="../outside.json"), "escapes repository root"),
    ],
)
def test_v3_rebind_refuses_hostile_mutations(mutate, message):
    authority = copy.deepcopy(_authority())
    mutate(authority)
    with pytest.raises(A.ArchiveIncomplete, match=message):
        A._validate_filter_authority_rebind_v3(authority, ROOT)


def test_v3_rebind_refuses_contract_pit_identity_mismatch():
    authority = _authority()
    contract = _rebound_contract(authority)
    contract["data"]["pit_universe_digest"] = "0" * 64
    with pytest.raises(A.ArchiveIncomplete, match="contract/filter authority PIT binding changed"):
        A.load_fold_filter_authorities(
            AUTHORITY, contract["data"]["exchange_filter_authority_sha256"], ROOT, contract
        )
