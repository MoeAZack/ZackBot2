"""Protected entry point for the signed CARRY-01 historical study.

Importing this module never reads data or computes a result.  The command must be
invoked explicitly with a frozen-data root and writes a new report exactly once.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.research import carry01_history as history
from tools.research import carry01_runner as runner


def _timestamp_ms(raw: str) -> int:
    return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000)


def _overlap_windows(windows: list[list[str]], start_ms: int) -> list[tuple[str, str, int, int]]:
    overlaps = []
    for raw_start, raw_end in windows:
        left, right = max(_timestamp_ms(raw_start), start_ms), _timestamp_ms(raw_end)
        if right - left >= 30 * 86_400_000:
            overlaps.append((datetime.fromtimestamp(left / 1000, tz=timezone.utc)
                             .isoformat().replace("+00:00", "Z"), raw_end, left, right))
    return overlaps


def build_primary_report(contract_path: Path, data_root: Path) -> dict[str, Any]:
    """Run only the promotable USDT primary over the five signed fresh-start folds."""
    contract = history.load_signed_contract(contract_path)
    symbols = {
        "BTC": history.load_symbol_history(contract, data_root, "BTCUSDT"),
        "ETH": history.load_symbol_history(contract, data_root, "ETHUSDT"),
    }
    suites: list[dict[str, runner.FoldReplay]] = []
    fold_output = []
    for index, (raw_start, raw_end) in enumerate(contract["windows"]["walk_forward"], 1):
        start_ms, end_ms = _timestamp_ms(raw_start), _timestamp_ms(raw_end)
        inputs = runner.build_fold_inputs(symbols, start_ms, end_ms)
        suite = runner.replay_stress_suite(inputs.observations, inputs.funding)
        suites.append(suite)
        asset_diagnostics = {}
        for sleeve in ("BTC", "ETH"):
            asset_inputs = runner.build_fold_inputs({sleeve: symbols[sleeve]}, start_ms, end_ms)
            asset_diagnostics[sleeve] = runner.fold_report(
                runner.replay_fold(asset_inputs.observations, asset_inputs.funding))
        fold_output.append({
            "fold": index,
            "start": raw_start,
            "end_exclusive": raw_end,
            "common_observations": len(inputs.observations),
            "eligible_funding_events": len(inputs.funding),
            "incomplete_funding_events": inputs.incomplete_funding_events,
            "scenarios": {name: runner.fold_report(replay)
                          for name, replay in sorted(suite.items())},
            "asset_diagnostics": asset_diagnostics,
        })
    gate = runner.evaluate_historical_gates(suites)
    usdc_symbols = {
        "BTC": history.load_symbol_history(contract, data_root, "BTCUSDC"),
        "ETH": history.load_symbol_history(contract, data_root, "ETHUSDC"),
    }
    usdc_start = max(item.start_ms for item in usdc_symbols.values())
    usdc_output = []
    for raw_start, raw_end, start_ms, end_ms in _overlap_windows(
            contract["windows"]["walk_forward"], usdc_start):
        inputs = runner.build_fold_inputs(usdc_symbols, start_ms, end_ms)
        usdc_output.append({
            "start": raw_start,
            "end_exclusive": raw_end,
            "common_observations": len(inputs.observations),
            "eligible_funding_events": len(inputs.funding),
            "incomplete_funding_events": inputs.incomplete_funding_events,
            "base_x1": runner.fold_report(
                runner.replay_fold(inputs.observations, inputs.funding)),
        })
    return {
        "format": "zb-carry01-historical-report/1",
        "candidate_id": contract["candidate_id"],
        "contract_digest_sha256": contract["contract_digest_sha256"],
        "primary": "equal-weight BTCUSDT and ETHUSDT",
        "folds": fold_output,
        "diagnostics": {
            "individual_primary_assets": "reported inside each primary fold",
            "equal_weight_usdc_common_overlap": usdc_output,
            "cash_benchmark_apr": contract["costs"]["cash_benchmark_apr"],
        },
        "historical_gate": gate,
        "paper_authority": False,
        "mainnet_authority": False,
    }


def write_report_once(report: dict[str, Any], output: Path) -> str:
    """Create a report and digest without overwriting prior evidence."""
    payload = (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    digest = hashlib.sha256(payload).hexdigest()
    digest_path = output.with_name(output.name + ".sha256")
    if output.exists() or digest_path.exists():
        raise FileExistsError("historical report or digest already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(payload)
    try:
        with digest_path.open("x", encoding="ascii") as handle:
            handle.write(digest + "\n")
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the protected CARRY-01 history study")
    parser.add_argument("--contract", type=Path, default=history.PREREG)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_primary_report(args.contract, args.data_root)
    digest = write_report_once(report, args.output)
    print(json.dumps({"output": str(args.output), "sha256": digest,
                      "verdict": report["historical_gate"]["verdict"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
