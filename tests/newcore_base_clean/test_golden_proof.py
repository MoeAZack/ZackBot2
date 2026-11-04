from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from newcore.base_clean import ProofKind
from tools import base_clean_golden_proof as G


HEAD = "a" * 40
OTHER_HEAD = "b" * 40
HERE = Path(__file__).parents[2]


def _trace_for(case):
    trades = []
    for expected in case["expect"]["trades"]:
        row = {key: value for key, value in expected.items() if key != "tol"}
        for key in ("R", "pnl"):
            if key in row:
                row[key] = float(row[key])
        trades.append(row)
    return {"trades": trades, "final": {}}


def _accepted(monkeypatch):
    """Full exact 30-case / 60-pair matrix built from the real golden pack."""
    loaded = G.schema.load_all()
    cases = {case["id"]: case for case in loaded}
    for adapter in G.NEWCORE_ADAPTERS:
        monkeypatch.setitem(G.adapters.CAPS, adapter, {"final": frozenset(), "funding": True})
    plan = tuple(sorted(
        (G.PairSpec(case["id"], adapter, G.schema.contract_sha(case), "core")
         for case in loaded for adapter in G.NEWCORE_ADAPTERS),
        key=lambda row: (row.case_id, row.adapter),
    ))
    traces = tuple((row.case_id, row.adapter, _trace_for(cases[row.case_id])) for row in reversed(plan))
    artifact = G._seal_artifact(HEAD, plan, traces, cases)
    return artifact, plan, cases, traces


def _reseal(doc):
    """Re-encode a mutated artifact document with a correct envelope digest."""
    body = json.dumps(doc["body"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    doc["artifact_sha256"] = hashlib.sha256(body).hexdigest()
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def test_pending_known_divergence_and_missing_adapter_all_refuse_plan(monkeypatch) -> None:
    loaded = G.schema.load_all()
    monkeypatch.setattr(G.schema, "load_all", lambda: loaded)
    monkeypatch.setattr(G.schema, "status", lambda _case, _adapter: "pending_adapter")
    with pytest.raises(G.CollectorError, match="golden_status_not_required"):
        G._load_plan()

    monkeypatch.setattr(G.schema, "status", lambda _case, _adapter: "known_divergence")
    with pytest.raises(G.CollectorError, match="golden_status_not_required"):
        G._load_plan()

    monkeypatch.setattr(G.schema, "status", lambda _case, _adapter: "required")
    monkeypatch.setattr(G.adapters, "CAPS", {})
    with pytest.raises(G.CollectorError, match="adapter_unavailable"):
        G._load_plan()


def test_canonical_round_trip_and_gate_proof_bind_one_digest(monkeypatch) -> None:
    artifact, plan, cases, _traces = _accepted(monkeypatch)
    encoded = G.encode_artifact(artifact)
    decoded = G.decode_artifact(encoded)
    monkeypatch.setattr(G, "_load_plan", lambda: (plan, cases))
    proof = G.gate_evidence(decoded)
    assert decoded == artifact
    assert encoded == G.encode_artifact(decoded)
    assert proof.kind is ProofKind.NEWCORE_GOLDEN_ADAPTERS
    assert proof.head_sha == HEAD
    assert proof.host_id == G.HOST_ID
    assert proof.passed
    assert proof.evidence_sha256 == artifact.artifact_sha256


def test_artifact_is_deterministic_for_trace_input_order(monkeypatch) -> None:
    artifact, plan, cases, traces = _accepted(monkeypatch)
    second = G._seal_artifact(HEAD, plan, tuple(reversed(traces)), cases)
    assert artifact == second
    assert len(artifact.body.pairs) == G.EXPECTED_PAIRS == 60
    assert len({row.case_id for row in artifact.body.pairs}) == G.EXPECTED_CASES == 30
    keys = [(row.case_id, row.adapter) for row in artifact.body.pairs]
    assert keys == sorted(keys)
    assert artifact.body.pack_sha256 == hashlib.sha256(G.canonical_bytes(plan)).hexdigest()


def test_artifact_stores_canonical_trace_bound_to_its_digest(monkeypatch) -> None:
    artifact, _plan, _cases, traces = _accepted(monkeypatch)
    supplied = {(case_id, adapter): trace for case_id, adapter, trace in traces}
    for row in artifact.body.pairs:
        assert row.trace == supplied[(row.case_id, row.adapter)]
        assert hashlib.sha256(G.canonical_bytes(row.trace)).hexdigest() == row.trace_sha256
    assert b'"trace":{"final"' in G.encode_artifact(artifact)


def test_mismatch_and_expectation_poison_refuse(monkeypatch) -> None:
    _artifact, plan, cases, traces = _accepted(monkeypatch)
    by_key = {(c, a): t for c, a, t in traces}
    victim = next(row for row in plan if cases[row.case_id]["expect"]["trades"])
    wrong = json.loads(json.dumps(by_key[(victim.case_id, victim.adapter)]))
    wrong["trades"][0]["exit"] = "FLATTEN"
    mutated = tuple((c, a, wrong if (c, a) == (victim.case_id, victim.adapter) else t) for c, a, t in traces)
    with pytest.raises(G.CollectorError, match="golden_comparison_mismatch"):
        G._seal_artifact(HEAD, plan, mutated, cases)

    poisoned = json.loads(json.dumps(by_key[(victim.case_id, victim.adapter)]))
    poisoned["trades"][0]["sym"] = G.adapters.POISON
    mutated = tuple((c, a, poisoned if (c, a) == (victim.case_id, victim.adapter) else t) for c, a, t in traces)
    with pytest.raises(G.CollectorError, match="adapter_expectation_leak"):
        G._seal_artifact(HEAD, plan, mutated, cases)


def test_missing_duplicate_and_unknown_trace_refuse(monkeypatch) -> None:
    _artifact, plan, cases, traces = _accepted(monkeypatch)
    with pytest.raises(G.CollectorError, match="golden_trace_missing"):
        G._seal_artifact(HEAD, plan, (), cases)
    with pytest.raises(G.CollectorError, match="golden_trace_missing"):
        G._seal_artifact(HEAD, plan, traces[1:], cases)
    with pytest.raises(G.CollectorError, match="golden_trace_duplicate"):
        G._seal_artifact(HEAD, plan, traces + (traces[0],), cases)
    with pytest.raises(G.CollectorError, match="golden_trace_unknown"):
        G._seal_artifact(HEAD, plan, (("unknown-case", plan[0].adapter, traces[0][2]),) + traces, cases)


def test_seal_refuses_any_plan_that_is_not_exactly_30_cases_and_60_pairs(monkeypatch) -> None:
    _artifact, plan, cases, traces = _accepted(monkeypatch)
    for short in (plan[:-1], plan[:2], plan[:1], ()):
        kept = {(row.case_id, row.adapter) for row in short}
        with pytest.raises(G.CollectorError, match="golden_matrix_cardinality"):
            G._seal_artifact(HEAD, short, tuple(t for t in traces if (t[0], t[1]) in kept), cases)
    # 30 cases with one adapter dropped from one case, padded with a duplicate-case row to keep 60
    one_adapter = tuple(row for row in plan if (row.case_id, row.adapter) != (plan[0].case_id, plan[0].adapter))
    with pytest.raises(G.CollectorError, match="golden_matrix_cardinality"):
        G._seal_artifact(HEAD, one_adapter, tuple(t for t in traces if (t[0], t[1]) in
                         {(r.case_id, r.adapter) for r in one_adapter}), cases)
    # 31 cases / 62 pairs
    extra_case = dict(cases[plan[0].case_id], id="zz-extra")
    extra_plan = plan + tuple(G.PairSpec("zz-extra", adapter, plan[0].contract_sha256, plan[0].tier)
                              for adapter in G.NEWCORE_ADAPTERS)
    extra_cases = dict(cases, **{"zz-extra": extra_case})
    extra_traces = traces + tuple(("zz-extra", adapter, _trace_for(extra_case)) for adapter in G.NEWCORE_ADAPTERS)
    with pytest.raises(G.CollectorError, match="golden_matrix_cardinality"):
        G._seal_artifact(HEAD, extra_plan, extra_traces, extra_cases)
    # 30 cases x 2 adapters but one pair listed twice (61 specs): still refused
    with pytest.raises(G.CollectorError, match="golden_matrix_cardinality"):
        G._seal_artifact(HEAD, plan + (plan[0],), traces, cases)


def test_load_plan_refuses_wrong_case_count(monkeypatch) -> None:
    loaded = G.schema.load_all()
    monkeypatch.setattr(G.schema, "status", lambda _case, _adapter: "required")
    monkeypatch.setattr(G.tiers, "load", lambda ids: {case_id: "core" for case_id in ids})
    for adapter in G.NEWCORE_ADAPTERS:
        monkeypatch.setitem(G.adapters.CAPS, adapter, {"final": frozenset(), "funding": True})
    monkeypatch.setattr(G.schema, "load_all", lambda: loaded[:-1])
    with pytest.raises(G.CollectorError, match="golden_case_count"):
        G._load_plan()
    monkeypatch.setattr(G.schema, "load_all", lambda: loaded + [dict(loaded[0])])
    with pytest.raises(G.CollectorError, match="golden_case_count"):
        G._load_plan()


def test_decode_refuses_artifact_without_exact_matrix(monkeypatch) -> None:
    artifact, _plan, _cases, _traces = _accepted(monkeypatch)
    encoded = G.encode_artifact(artifact)
    for mutate in (
        lambda pairs: pairs.pop(),                               # 59 pairs
        lambda pairs: pairs.__delitem__(slice(0, 2)),            # one whole case missing
        lambda pairs: pairs.__delitem__(0),                      # one adapter of one case missing
        lambda pairs: pairs.append(dict(pairs[-1], case_id="zz-extra")),   # 61 pairs / 31 cases
    ):
        doc = json.loads(encoded)
        mutate(doc["body"]["pairs"])
        with pytest.raises(G.CollectorError, match=r"validation\.artifact\.(cardinality|matrix)"):
            G.decode_artifact(_reseal(doc))


def test_stored_trace_is_revalidated_independently_of_collection(monkeypatch) -> None:
    artifact, plan, cases, _traces = _accepted(monkeypatch)
    monkeypatch.setattr(G, "_load_plan", lambda: (plan, cases))
    encoded = G.encode_artifact(artifact)
    index = next(i for i, row in enumerate(artifact.body.pairs) if cases[row.case_id]["expect"]["trades"])

    # a self-consistent forgery: trace changed, trace digest and envelope digest recomputed
    doc = json.loads(encoded)
    row = doc["body"]["pairs"][index]
    row["trace"]["trades"][0]["exit"] = "FLATTEN"
    row["trace_sha256"] = hashlib.sha256(G.canonical_bytes(row["trace"])).hexdigest()
    forged = G.decode_artifact(_reseal(doc))          # structurally valid ...
    with pytest.raises(G.CollectorError, match="golden_comparison_mismatch"):
        G.gate_evidence(forged)                      # ... but semantics fail on revalidation

    # trace changed without refreshing its digest
    doc = json.loads(encoded)
    doc["body"]["pairs"][index]["trace"]["trades"][0]["exit"] = "FLATTEN"
    with pytest.raises(G.CollectorError, match="validation.result.trace_digest"):
        G.decode_artifact(_reseal(doc))

    # expectation poison smuggled into a stored trace
    doc = json.loads(encoded)
    row = doc["body"]["pairs"][index]
    row["trace"]["trades"][0]["sym"] = G.adapters.POISON
    row["trace_sha256"] = hashlib.sha256(G.canonical_bytes(row["trace"])).hexdigest()
    with pytest.raises(G.CollectorError, match="adapter_expectation_leak"):
        G.decode_artifact(_reseal(doc))

    # a trace dict mutated in memory after sealing is caught before a GateProof exists
    artifact.body.pairs[index].trace["trades"][0]["exit"] = "FLATTEN"
    with pytest.raises(G.CollectorError, match="validation.result.trace_digest"):
        G.gate_evidence(artifact)


# Hostile values stay in the parameter objects; the explicit ids keep them out of collected node IDs.
@pytest.mark.parametrize("key,value", [
    pytest.param("secret_token", "sk-live-abc123", id="extra_key_secret"),
    pytest.param("url", "https://example.invalid/exfil?k=1", id="extra_key_url"),
    pytest.param("note", "x", id="extra_key_plain"),
    pytest.param("_res_extra", {"R": 0.0}, id="extra_key_res_lookalike"),
    pytest.param("tol", {"R": 0.001}, id="extra_key_tol"),
], )
def test_stored_trade_with_any_extra_key_refuses_everywhere(monkeypatch, key, value) -> None:
    artifact, plan, cases, traces = _accepted(monkeypatch)
    by_key = {(c, a): t for c, a, t in traces}
    victim = next(row for row in plan if cases[row.case_id]["expect"]["trades"])
    bad = json.loads(json.dumps(by_key[(victim.case_id, victim.adapter)]))
    bad["trades"][0][key] = value
    mutated = tuple((c, a, bad if (c, a) == (victim.case_id, victim.adapter) else t) for c, a, t in traces)
    with pytest.raises(G.CollectorError, match=r"validation\.trace\.trade_keys|golden_trace_schema"):
        G._seal_artifact(HEAD, plan, mutated, cases)
    # self-consistent forged stored artifact (trace + trace digest + envelope digest all recomputed)
    doc = json.loads(G.encode_artifact(artifact))
    row = next(r for r in doc["body"]["pairs"] if (r["case_id"], r["adapter"]) == (victim.case_id, victim.adapter))
    row["trace"]["trades"][0][key] = value
    row["trace_sha256"] = hashlib.sha256(G.canonical_bytes(row["trace"])).hexdigest()
    with pytest.raises(G.CollectorError, match=r"validation\.trace\.trade_keys"):
        G.decode_artifact(_reseal(doc))


def test_undeclared_final_key_refuses(monkeypatch) -> None:
    _artifact, plan, cases, traces = _accepted(monkeypatch)
    bad = {"trades": traces[0][2]["trades"], "final": {"secret_token": "sk-live"}}
    mutated = ((traces[0][0], traces[0][1], bad),) + traces[1:]
    with pytest.raises(G.CollectorError, match="golden_comparison_invalid"):
        G._seal_artifact(HEAD, plan, mutated, cases)


def test_trade_field_outside_that_cases_expectation_refuses(monkeypatch) -> None:
    _artifact, plan, cases, traces = _accepted(monkeypatch)
    victim = next(row for row in plan if any("pnl" not in t for t in cases[row.case_id]["expect"]["trades"]))
    index = next(i for i, t in enumerate(cases[victim.case_id]["expect"]["trades"]) if "pnl" not in t)
    base = {(c, a): t for c, a, t in traces}[(victim.case_id, victim.adapter)]
    extra = json.loads(json.dumps(base))
    extra["trades"][index]["pnl"] = 1.0       # in the global vocabulary, but not in this trade's expectation
    mutated = tuple((c, a, extra if (c, a) == (victim.case_id, victim.adapter) else t) for c, a, t in traces)
    with pytest.raises(G.CollectorError, match="golden_trace_schema"):
        G._seal_artifact(HEAD, plan, mutated, cases)


def test_case_exact_trade_fields_are_required_and_res_is_bounded(monkeypatch) -> None:
    artifact, plan, cases, traces = _accepted(monkeypatch)
    by_key = {(c, a): t for c, a, t in traces}
    victim = next(row for row in plan if cases[row.case_id]["expect"]["trades"])
    base = by_key[(victim.case_id, victim.adapter)]

    def seal_with(trace):
        mutated = tuple((c, a, trace if (c, a) == (victim.case_id, victim.adapter) else t) for c, a, t in traces)
        return G._seal_artifact(HEAD, plan, mutated, cases)

    # a field the expected trade does not carry (but which is in the global vocabulary) is refused per case
    expected_keys = set(cases[victim.case_id]["expect"]["trades"][0]) - {"tol"}
    spare = sorted(G.TRADE_FIELDS - expected_keys)
    if spare:
        extra = json.loads(json.dumps(base))
        extra["trades"][0][spare[0]] = 1
        with pytest.raises(G.CollectorError, match="golden_trace_schema"):
            seal_with(extra)

    # permitted `_res` with exact key/type bounds is accepted; anything else refused
    ok = json.loads(json.dumps(base))
    ok["trades"][0]["_res"] = {"R": 0.0001, "pnl": 0.0001}
    assert seal_with(ok)
    for bad_res in ({"R": 0.0, "url": 1.0}, {"R": "0.1"}, {"R": True}, [0.1], "x", {"pnl": None}):
        bad = json.loads(json.dumps(base))
        bad["trades"][0]["_res"] = bad_res
        with pytest.raises(G.CollectorError, match=r"validation\.trace\.res"):
            seal_with(bad)
    huge = json.loads(json.dumps(base))
    huge["trades"][0]["_res"] = {"R": 1.0}          # above RES_MAX: comparator refuses it
    with pytest.raises(G.CollectorError, match="golden_comparison_invalid"):
        seal_with(huge)

    # non-dict trade rows refuse
    nondict = json.loads(json.dumps(base))
    nondict["trades"][0] = ["sym"]
    with pytest.raises(G.CollectorError, match="validation.trace.trade"):
        seal_with(nondict)


def test_altered_digest_body_pack_or_matrix_refuse(monkeypatch) -> None:
    artifact, plan, cases, _traces = _accepted(monkeypatch)
    encoded = G.encode_artifact(artifact)
    doc = json.loads(encoded)
    doc["artifact_sha256"] = "f" * 64
    with pytest.raises(G.CollectorError, match="validation.envelope.digest"):
        G.decode_artifact(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii"))

    doc = json.loads(encoded)
    doc["body"]["pairs"][0]["trace_sha256"] = "e" * 64
    with pytest.raises(G.CollectorError, match="validation.result.trace_digest"):
        G.decode_artifact(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii"))

    doc = json.loads(encoded)
    doc["body"]["head_sha"] = "c" * 40
    with pytest.raises(G.CollectorError, match="validation.envelope.digest"):
        G.decode_artifact(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii"))

    altered = (G.PairSpec(plan[0].case_id, plan[0].adapter, "d" * 64, plan[0].tier),) + plan[1:]
    monkeypatch.setattr(G, "_load_plan", lambda: (altered, cases))
    with pytest.raises(G.CollectorError, match="artifact_pack_mismatch"):
        G.gate_evidence(artifact)


def test_duplicate_unknown_and_noncanonical_json_refuse(monkeypatch) -> None:
    artifact, _plan, _cases, _traces = _accepted(monkeypatch)
    encoded = G.encode_artifact(artifact)
    duplicate = encoded.replace(b'{"artifact_sha256":',
                                b'{"artifact_sha256":"' + b'f' * 64 + b'","artifact_sha256":', 1)
    with pytest.raises(G.CollectorError, match="validation.json.duplicate"):
        G.decode_artifact(duplicate)
    doc = json.loads(encoded)
    doc["body"]["extra"] = None
    with pytest.raises(G.CollectorError, match="validation.artifact.keys"):
        G.decode_artifact(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii"))
    with pytest.raises(G.CollectorError, match="validation.json.canonical"):
        G.decode_artifact(encoded + b"\n")


def test_records_are_frozen_and_reconstructed_before_proof(monkeypatch) -> None:
    artifact, plan, cases, _traces = _accepted(monkeypatch)
    with pytest.raises(FrozenInstanceError):
        artifact.body.head_sha = OTHER_HEAD  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        artifact.body.pairs[0].trace_sha256 = "f" * 64  # type: ignore[misc]
    monkeypatch.setattr(G, "_load_plan", lambda: (plan, cases))
    assert G.gate_evidence(artifact).evidence_sha256 == artifact.artifact_sha256


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"actual_head": OTHER_HEAD}, "head_mismatch"),
        ({"dirty": True}, "worktree_dirty"),
    ],
)
def test_exact_clean_head_is_mandatory(changes, code) -> None:
    values = {"expected_head": HEAD, "actual_head": HEAD, "dirty": False}
    values.update(changes)
    with pytest.raises(G.CollectorError, match=code):
        G._identity(**values)


def test_worker_timeout_and_failure_are_stable_without_raw_text(monkeypatch) -> None:
    monkeypatch.setattr(G.deadline, "run", lambda *_args, **_kwargs: SimpleNamespace(
        timed_out=True, returncode=None, stdout="", stderr="SECRET"))
    with pytest.raises(G.CollectorError, match="adapter_timeout") as timeout:
        G._run_worker()
    assert "SECRET" not in str(timeout.value)

    monkeypatch.setattr(G.deadline, "run", lambda *_args, **_kwargs: SimpleNamespace(
        timed_out=False, returncode=2, stdout="", stderr="SECRET"))
    with pytest.raises(G.CollectorError, match="adapter_failed") as failed:
        G._run_worker()
    assert "SECRET" not in str(failed.value)


def test_collection_failure_and_interrupt_suppress_exception_chaining(monkeypatch) -> None:
    monkeypatch.setattr(G, "_git_state", lambda _repo: (HEAD, False))
    monkeypatch.setattr(G, "_load_plan", lambda: (_ for _ in ()).throw(RuntimeError("SECRET")))
    with pytest.raises(G.CollectorError, match="collection_failed") as failed:
        G.collect(expected_head=HEAD, repo=HERE)
    assert failed.value.__cause__ is None and failed.value.__context__ is None
    assert "SECRET" not in str(failed.value)

    monkeypatch.setattr(G, "_load_plan", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(G.CollectorError, match="collection_interrupted") as interrupted:
        G.collect(expected_head=HEAD, repo=HERE)
    assert interrupted.value.__cause__ is None and interrupted.value.__context__ is None


def test_collector_is_default_off_and_has_no_network_runtime_or_strategy_imports() -> None:
    source = (HERE / "tools" / "base_clean_golden_proof.py").read_text(encoding="utf-8")
    forbidden = ("requests", "urllib", "socket", "binance_client", "engine", "strategies", "paper_books")
    assert all(f"import {name}" not in source and f"from {name}" not in source for name in forbidden)
    for path in (HERE / "verify.py", HERE / "tests" / "golden" / "runner.py",
                 HERE / "tests" / "golden" / "pack_worker.py"):
        assert "base_clean_golden_proof" not in path.read_text(encoding="utf-8")
