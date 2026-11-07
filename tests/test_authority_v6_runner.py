"""Hostile offline checks for the authority-v6 orchestration boundary.

All acquisition/build work is dependency-injected.  These tests do not open the
repository market store, call the network, or read candidate/holdout outcomes.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.research import authority_v6 as V6
from tools.research import authority_v6_runner as R


def _plan():
    return {
        "authority_id": "BINANCE-UM-PRECUTOFF-AUTHORITY-V6",
        "version": 6,
        "outputs": {
            "manifest_v6": {"path": "out/manifest.json.gz"},
            "pit_v9": {"path": "out/pit.json"},
            "filter_v4": {"path": "out/filter.json"},
            "coverage_v4": {"path": "out/coverage.json"},
        },
    }


def _config(tmp_path):
    return R.RunConfig(
        root=tmp_path, store=tmp_path / "store", ledger=tmp_path / "ledger.jsonl",
        confirm_plan_sha256=V6.PLAN_SHA256, max_seed_bytes=101,
        max_intraday_bytes=202, max_funding_bytes=303,
        accessed_cairo="2026-11-07T12:00:00+02:00")


class OfflineBackend:
    def __init__(self, *, gaps=0, hostile_union=False):
        self.steps = []
        self.gaps = gaps
        self.hostile_union = hostile_union

    def load_plan(self):
        self.steps.append("plan")
        return _plan()

    def seed_symbols(self):
        self.steps.append("seed-symbols")
        return ("AAAUSDT", "BBBUSDT")

    def seed_requests(self, symbols):
        self.steps.append("seed-requests")
        return tuple(SimpleNamespace(path=f"seed/{symbol}", symbol=symbol) for symbol in symbols)

    def acquire_vision(self, requests, *, max_total_bytes, phase, pit_v9=None):
        self.steps.append(f"acquire-{phase}:{max_total_bytes}")
        return {"verified": [row.path for row in requests],
                "official_404": [], "checksum_failure": [], "bytes": 1}

    def build_first_pass(self, seed_paths, provisional_digest):
        self.steps.append("first-pass")
        assert len(provisional_digest) == 64
        return {"kind": "first", "digest": "1" * 64}

    def intraday_symbols(self, pit_v9):
        self.steps.append("intraday-symbols")
        return ("ATTACKERUSDT",) if self.hostile_union else ("AAAUSDT",)

    def intraday_requests(self, symbols):
        self.steps.append("intraday-requests")
        return tuple(SimpleNamespace(path=f"intraday/{symbol}", symbol=symbol) for symbol in symbols)

    def capture_funding(self, symbols, pit_v9):
        self.steps.append("funding:303")
        return {"completed": list(symbols), "incomplete": [], "bytes": 1}

    def funding_paths(self, symbols):
        self.steps.append("funding-paths")
        return ("funding/AAAUSDT.json",)

    def build_manifest(self, paths, pit_v9):
        self.steps.append("manifest")
        assert paths == tuple(sorted(set(paths)))
        return {"format": "fixture-manifest", "digest": "2" * 64}

    def replay_pit(self, first_pass, manifest):
        self.steps.append("final-pit")
        return {"format": "fixture-pit", "digest": "3" * 64}

    def build_filter(self, pit_v9):
        self.steps.append("filter")
        return {"format": "fixture-filter", "digest": "4" * 64}

    def build_coverage(self, manifest, pit_v9, filter_v4):
        self.steps.append("coverage")
        gaps = ([{"stream": "klines:1h", "symbol": "AAAUSDT",
                  "start_ms": 1, "end_ms": 2}] if self.gaps else [])
        return {"format": "fixture-coverage", "digest": "5" * 64,
                "required_result": "ZERO_GUARANTEED_PERIOD_GAPS",
                "guaranteed_period_gaps": self.gaps, "gaps": gaps}


def test_one_command_orders_full_vertical_and_publishes_only_zero_gap(tmp_path):
    backend, emitted = OfflineBackend(), []
    result = R.run(_config(tmp_path), backend=backend, emit=emitted.append)
    assert backend.steps == [
        "plan", "seed-symbols", "seed-requests", "acquire-seed:101", "first-pass",
        "intraday-symbols", "intraday-requests", "acquire-intraday:202",
        "funding:303", "funding-paths", "manifest", "final-pit", "filter", "coverage",
    ]
    notice = json.loads(emitted[0])
    assert notice == {
        "authority_id": "BINANCE-UM-PRECUTOFF-AUTHORITY-V6", "version": 6,
        "plan_sha256": V6.PLAN_SHA256, "cutoff_exclusive_ms": V6.CUTOFF_MS,
        "seed_symbols": 2, "seed_requests": 2, "confirmed": True,
    }
    assert result["required_result"] == "ZERO_GUARANTEED_PERIOD_GAPS"
    outputs = {name: Path(path) for name, path in result["outputs"].items()}
    assert all(path.exists() for path in outputs.values())
    assert json.loads(gzip.decompress(outputs["manifest_v6"].read_bytes()))["digest"] == "2" * 64
    assert R.resolve_current_outputs(_plan(), tmp_path) == outputs
    assert Path(result["current_pointer"]).exists()
    assert not any((tmp_path / row["path"]).exists() for row in _plan()["outputs"].values())
    assert not (tmp_path / R.PUBLICATION_ROOT_REL / R.STAGE_NAME).exists()


def test_gap_report_fails_before_any_output_is_staged(tmp_path):
    backend = OfflineBackend(gaps=1)
    with pytest.raises(R.RunnerError, match="ZERO_GUARANTEED_PERIOD_GAPS"):
        R.run(_config(tmp_path), backend=backend, emit=lambda _: None)
    assert not (tmp_path / "out").exists()
    assert not (tmp_path / R.PUBLICATION_ROOT_REL).exists()


def test_hostile_pit_union_escape_is_refused_before_intraday_network_seam(tmp_path):
    backend = OfflineBackend(hostile_union=True)
    with pytest.raises(R.RunnerError, match="exact sorted PIT-v9 subset"):
        R.run(_config(tmp_path), backend=backend, emit=lambda _: None)
    assert not any(step.startswith("acquire-intraday") for step in backend.steps)


@pytest.mark.parametrize("partial", ["output", "stage", "store-part", "torn-ledger"])
def test_restart_refuses_partial_or_torn_state_before_acquisition(tmp_path, partial):
    config, backend = _config(tmp_path), OfflineBackend()
    if partial == "output":
        target = tmp_path / "out/pit.json"; target.parent.mkdir(); target.write_bytes(b"partial")
    elif partial == "stage":
        (tmp_path / R.PUBLICATION_ROOT_REL / R.STAGE_NAME).mkdir(parents=True)
    elif partial == "store-part":
        target = config.store / "x/y.zip.part"; target.parent.mkdir(parents=True); target.write_bytes(b"partial")
    else:
        config.ledger.write_bytes(b'{"kind":"VISION"}')
    with pytest.raises(V6.AuthorityV6Error):
        R.run(config, backend=backend, emit=lambda _: None)
    assert not any(step.startswith("acquire-") for step in backend.steps)


@pytest.mark.parametrize("boundary", range(1, 8))
def test_interruption_at_every_publication_boundary_exposes_nothing(
        tmp_path, monkeypatch, boundary):
    config, backend = _config(tmp_path), OfflineBackend()
    real_replace, transitions = R.os.replace, 0

    def interrupt_boundary(source, target):
        nonlocal transitions
        transitions += 1
        if transitions == boundary:
            raise OSError("hostile power loss")
        return real_replace(source, target)

    monkeypatch.setattr(R.os, "replace", interrupt_boundary)
    with pytest.raises(OSError, match="power loss"):
        R.run(config, backend=backend, emit=lambda _: None)
    monkeypatch.setattr(R.os, "replace", real_replace)
    assert not (tmp_path / R.CURRENT_POINTER_REL).exists()
    assert not any((tmp_path / row["path"]).exists() for row in _plan()["outputs"].values())
    with pytest.raises(R.RunnerError, match="no committed"):
        R.resolve_current_outputs(_plan(), tmp_path)
    fresh = OfflineBackend()
    with pytest.raises(R.RunnerError):
        R.run(config, backend=fresh, emit=lambda _: None)
    assert not any(step.startswith("acquire-") for step in fresh.steps)


def test_interruption_after_pointer_commit_exposes_complete_publication(tmp_path, monkeypatch):
    config, backend = _config(tmp_path), OfflineBackend()
    real_replace, transitions = R.os.replace, 0

    def crash_after_pointer(source, target):
        nonlocal transitions
        transitions += 1
        result = real_replace(source, target)
        if transitions == 7:
            raise OSError("power loss after commit")
        return result

    monkeypatch.setattr(R.os, "replace", crash_after_pointer)
    with pytest.raises(OSError, match="after commit"):
        R.run(config, backend=backend, emit=lambda _: None)
    monkeypatch.setattr(R.os, "replace", real_replace)
    resolved = R.resolve_current_outputs(_plan(), tmp_path)
    assert set(resolved) == set(R.ARTIFACT_FILES)
    assert all(path.exists() for path in resolved.values())


@pytest.mark.parametrize("raw", [
    b"{}\n",
    b'{"kind":"VISION","path":"x.zip","attempt":1,"decision":"CHECKSUM_FETCH_FAILED"}\n',
    (b'{"kind":"VISION","path":"x.zip","attempt":0,'
     b'"decision":"VISION_PARSER_SCHEMA_CUTOFF_ACCEPTED",'
     b'"acquisition_record_sha256":"' + b"0" * 64 + b'","acquisition_attempt":0}\n'),
])
def test_semantically_malformed_ledger_fails_before_any_acquisition(tmp_path, raw):
    config, backend = _config(tmp_path), OfflineBackend()
    config.ledger.write_bytes(raw)
    with pytest.raises(V6.AuthorityV6Error):
        R.run(config, backend=backend, emit=lambda _: None)
    assert not any(step.startswith("acquire-") for step in backend.steps)


def test_wrong_plan_confirmation_refuses_before_acquisition(tmp_path):
    config = R.RunConfig(**{**_config(tmp_path).__dict__, "confirm_plan_sha256": "0" * 64})
    backend = OfflineBackend()
    with pytest.raises(R.RunnerError, match="operator confirmation"):
        R.run(config, backend=backend, emit=lambda _: None)
    assert not any(step.startswith("acquire-") for step in backend.steps)


def test_new_bounded_funding_provenance_replays_without_legacy_server_time(tmp_path):
    symbol = "BTCUSDT"
    rel = (f"um/api/fundingRate/{symbol}/"
           f"{symbol}-fundingRate-2026-11-01-2026-11-04-p000.json")
    target = tmp_path / Path(*rel.split("/")); target.parent.mkdir(parents=True)
    raw = V6.canonical([{"symbol": symbol, "fundingTime": V6.API_START_MS,
                         "fundingRate": "0.0001"}])
    digest = V6.sha256(raw)
    url = V6.funding_url(symbol, V6.API_START_MS)
    target.write_bytes(raw)
    Path(str(target) + ".ok").write_text(digest + "\n", encoding="ascii")
    provenance = {
        "format": "zb-bounded-funding-response/2", "url": url,
        "params": {"symbol": symbol, "startTime": V6.API_START_MS,
                   "endTime": V6.API_END_MS, "limit": 1000},
        "response_sha256": digest, "raw_bytes": len(raw), "rows": 1,
        "first_funding_time_ms": V6.API_START_MS,
        "last_funding_time_ms": V6.API_START_MS, "terminal_page": True,
        "accessed_cairo": "2026-11-07T12:00:00+02:00",
    }
    Path(str(target) + ".provenance.json").write_bytes(V6.canonical(provenance))
    record = V6._scan_v6_record_unledgered(tmp_path, rel)
    meta, rows = R._read_record_rows(tmp_path, record, new=True)
    assert meta["series"] == "fundingRate"
    assert rows == [{"symbol": symbol, "fundingTime": V6.API_START_MS,
                     "fundingRate": "0.0001"}]
