"""EDGE-04 adapter into the existing causal PIT data/evaluation boundary.

No function in this module opens the sealed holdout.  Accounting consumes this tape
through the existing cost, funding and exchange-rule modules in ``pit_run``; this pass
does not run a market result.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile

import pit as P
import pit_run as PR
import splits as S

FORMAT = "zb-edge04-pit-adapter/1"
PREREG = "docs/newcore/research/EDGE04_LEGACY_REEVAL_PREREG_v1.json"
ADDENDUM = "docs/newcore/research/EDGE04_MANAGEMENT_ADDENDUM_v1.json"
ORCHESTRATION_ADDENDUM = "docs/newcore/research/EDGE04_ORCHESTRATION_ADDENDUM_v1.json"
EVALUATOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "edge04_eval.py")
FUNCTION = "tape"


class Edge04Error(ValueError):
    pass


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def contract(repo):
    path = os.path.join(repo, PREREG)
    with open(path, encoding="utf-8") as fh:
        c = json.load(fh)
    if c.get("format") != "zb-edge04-prereg/1" or c.get("boundaries", {}).get("holdout_read") is not False:
        raise Edge04Error("EDGE-04 preregistration is missing or does not seal the holdout")
    if _sha(os.path.join(repo, "strategies.py")) != c["source"]["strategies_sha256"]:
        raise Edge04Error("runtime strategy rules differ from the frozen EDGE-04 source")
    return c


def management_addendum(repo):
    path = os.path.join(repo, ADDENDUM)
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("format") != "zb-edge04-management-addendum/1" or doc.get("candidate_id") != \
            "edge04_legacy_reeval.v1" or doc.get("boundaries", {}).get("market_data_opened") is not False:
        raise Edge04Error("EDGE-04 management addendum is missing or was not frozen before data")
    return doc


def plan(repo):
    c = contract(repo)
    w = c["windows"]
    splits = [dict(name="calibration", **w["calibration"]), dict(name="train", **w["train"])]
    splits += [dict(name="walk_forward", fold=x["fold"], start=x["start"], end=x["end"])
               for x in w["walk_forward"]]
    splits.append(dict(name="holdout", start=w["holdout"]["start"], end=w["holdout"]["end"]))
    return S.SplitPlan(splits, interval="4h", lookback_bars=240, horizon_bars=180)


def dataset(manifest, store, static, *, repo):
    """The preregistered CORE-10-ELIGIBLE-9 in frozen rank order (HYPE stays sensitivity-only)."""
    symbols = contract(repo)["universe"]["headline"]
    return PR.StaticDataset(manifest, store, static, symbols, exact_subset=True)


def development_window(plan_, key):
    """The shared fail-closed guard; calibration and holdout are never opened here."""
    return PR.dev_window(plan_, key)


def evaluate_split(access, key, times, *, repo):
    """Attest one development signal tape using ``pit.evaluate``'s two-process/perturbation proof."""
    contract(repo)  # source-rule pins are checked before Access can append or read data
    management_addendum(repo)
    p = access.plan
    development_window(p, key)
    if p.split(key)["name"] == "holdout":  # defensive duplicate: do not rely on a caller-supplied Access.
        raise Edge04Error("EDGE-04 adapter never opens the sealed holdout")
    times = list(times)
    first, last = p.decision_range(key)
    if any(type(t) is not int or t < first or t > last or t % p.interval_ms for t in times):
        raise Edge04Error("decision schedule leaves the split's purged, embargoed, finite-horizon range")
    window = access.open(key, detail={"purpose": "EDGE-04 causal signal-tape attestation"})
    return P.evaluate(window, EVALUATOR, FUNCTION, times)


def binding(repo):
    """Immutable identity material for a later run envelope; no data is opened."""
    c = contract(repo)
    management_addendum(repo)
    return {
        "format": FORMAT,
        "candidate_id": c["candidate_id"],
        "prereg_sha256": _sha(os.path.join(repo, PREREG)),
        "management_addendum_sha256": _sha(os.path.join(repo, ADDENDUM)),
        "evaluator_sha256": _sha(EVALUATOR),
        "accounting_sha256": _sha(os.path.join(repo, "tools/research/edge04_account.py")),
        "orchestration_addendum_sha256": _sha(os.path.join(repo, ORCHESTRATION_ADDENDUM)),
        "orchestration_sha256": _sha(os.path.join(repo, "tools/research/edge04_orchestrate.py")),
        "costs_sha256": _sha(os.path.join(repo, "tools/research/costs.py")),
        "pit_runner_sha256": _sha(os.path.join(repo, "tools/research/pit_run.py")),
        "exchange_rules": {"path": PR.RULES_PATH, "sha256": PR.RULES_SHA256, "environment": PR.RULES_ENV},
        "hypotheses": [{"id": h["id"], "key": h["key"], "side": h["side"],
                         "management": h["management"]} for h in c["hypotheses"]],
    }


def write_once(path, doc):
    """Atomic immutable artifact: identical replay is a no-op; differing bytes are refused."""
    data = json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n"
    if os.path.exists(path):
        with open(path, "rb") as fh:
            if fh.read() == data:
                return path
        raise Edge04Error(f"{path} exists with different content; EDGE-04 outputs are immutable")
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)  # link is create-only: it never replaces a concurrently created final
        except FileExistsError:
            with open(path, "rb") as fh:
                if fh.read() != data:
                    raise Edge04Error(f"{path} was concurrently sealed with different content")
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    return path
