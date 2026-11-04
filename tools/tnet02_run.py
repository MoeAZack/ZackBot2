"""TNET-02 CLI: exact-build identity, suite manifest plan, and REAL_TNET_EXACT_HEAD proof conversion.

    python -m tools.tnet02_run plan  --expected-head SHA --account-file ACCOUNT.json --symbols BTCUSDT[,ETHUSDT]
    python -m tools.tnet02_run proof --expected-head SHA --account-file ACCOUNT.json --report REPORT.json --host-id tnet_<id>
    python -m tools.tnet02_run run   ...   (refused in this build: real wiring lands in a later commit)

``plan`` checks that HEAD equals --expected-head with a clean tracked tree, builds and validates the frozen
twelve-case manifest, and prints it.  It sends nothing and reads no credential.  ``proof`` decodes an exact report
file, refuses a report for another account or key digest, and prints the GateProof as canonical JSON.  Output never contains credentials, URLs or exception text.

Exit codes: 0 ok / passed proof, 1 failed proof, 3 refused precondition.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from newcore.domain.account import (Account, AccountBinding, BindingConfirmation, BindingState,  # noqa: E402
                                    Environment, Venue)
from newcore.domain.orders import Side  # noqa: E402
from newcore.testnet_harness import (MAX_LEVERAGE, MAX_ORDERS_PER_SCENARIO, MAX_SCENARIO_NOTIONAL,  # noqa: E402
                                     REQUIRED_SCENARIOS, HarnessBinding, HarnessError, ScenarioCase, ScenarioKind,
                                     SuiteManifest)
from newcore.tnet02.gate import GateError, gate_proof  # noqa: E402


EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 3
_ACCOUNT_KEYS = {"account_id", "label", "key_digest", "hedge_mode", "settlement_asset", "confirmed_at_ms",
                 "typed_phrase"}


class Refused(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _git(*args: str) -> str:
    try:
        return subprocess.run(("git", *args), cwd=ROOT, capture_output=True, text=True, check=True,
                              timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise Refused("git_unavailable") from None


def check_identity(expected_head: str) -> None:
    if len(expected_head) != 40 or any(c not in "0123456789abcdef" for c in expected_head):
        raise Refused("expected_head_invalid")
    if _git("rev-parse", "HEAD") != expected_head:
        raise Refused("head_mismatch")
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise Refused("worktree_dirty")


def load_account(path: Path) -> Account:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise Refused("account_file_unreadable") from None
    if type(raw) is not dict or set(raw) != _ACCOUNT_KEYS:
        raise Refused("account_file_keys")
    try:
        binding = AccountBinding(venue=Venue.BINANCE_USDM, environment=Environment.TESTNET,
                                 settlement_asset=raw["settlement_asset"], key_digest=raw["key_digest"],
                                 exchange_uid=None)
        confirmation = BindingConfirmation(account_id=raw["account_id"], old_key_digest=None,
                                           new_key_digest=raw["key_digest"], typed_phrase=raw["typed_phrase"],
                                           confirmed_at_ms=raw["confirmed_at_ms"])
        return Account(account_id=raw["account_id"], label=raw["label"], hedge_mode=raw["hedge_mode"],
                       binding=binding, binding_state=BindingState.CONFIRMED, proposed_binding=None,
                       confirmation=confirmation)
    except Exception:
        raise Refused("account_file_invalid") from None


def build_manifest(*, build_sha: str, account: Account, symbols: tuple[str, ...], started_ms: int) -> SuiteManifest:
    cases = []
    for index, kind in enumerate(REQUIRED_SCENARIOS):
        side = Side.SHORT if kind in (ScenarioKind.SHORT_TARGET, ScenarioKind.SHORT_STOP) else Side.LONG
        case_id = "case_" + hashlib.sha256(f"{build_sha}:{started_ms}:{index}".encode("ascii")).hexdigest()[:32]
        cases.append(ScenarioCase(case_id, kind, symbols[index % len(symbols)], side, MAX_LEVERAGE,
                                  MAX_SCENARIO_NOTIONAL, MAX_ORDERS_PER_SCENARIO))
    boot = "boot_" + hashlib.sha256(f"{build_sha}:{started_ms}:boot".encode("ascii")).hexdigest()[:32]
    try:
        return SuiteManifest(build_sha, started_ms, boot, HarnessBinding(account, True, symbols), tuple(cases))
    except HarnessError as error:
        raise Refused("manifest_" + error.code.replace(".", "_")) from None


def _symbols(text: str) -> tuple[str, ...]:
    return tuple(part for part in text.split(",") if part)


def _plan(args) -> int:
    check_identity(args.expected_head)
    account = load_account(Path(args.account_file))
    spec = build_manifest(build_sha=args.expected_head, account=account, symbols=_symbols(args.symbols),
                          started_ms=args.started_ms)
    lines = [f"PLAN build={spec.build_sha} account={account.account_id} digest={account.binding.key_digest}",
             f"PLAN environment=TESTNET symbols={','.join(spec.binding.symbols)} leverage<={MAX_LEVERAGE} "
             f"notional<={MAX_SCENARIO_NOTIONAL} orders<={MAX_ORDERS_PER_SCENARIO}"]
    for case in spec.cases:
        lines.append(f"CASE {case.case_id} {case.kind.value} {case.symbol} {case.side.value}")
    lines.append("PLAN no order, network or credential use in this command")
    print("\n".join(lines))
    return EXIT_OK


def _proof(args) -> int:
    account = load_account(Path(args.account_file))
    try:
        data = Path(args.report).read_bytes()
    except OSError:
        raise Refused("report_unreadable") from None
    try:
        proof = gate_proof(data, expected_head=args.expected_head, host_id=args.host_id,
                           expected_account_id=account.account_id,
                           expected_binding_digest=account.binding.key_digest)
    except GateError as error:
        raise Refused(error.code.replace(".", "_")) from None
    print(json.dumps({"kind": proof.kind.value, "head_sha": proof.head_sha, "host_id": proof.host_id,
                      "passed": proof.passed, "evidence_sha256": proof.evidence_sha256},
                     sort_keys=True, separators=(",", ":")))
    return EXIT_OK if proof.passed else EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tnet02_run")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--expected-head", required=True)
    plan.add_argument("--account-file", required=True)
    plan.add_argument("--symbols", required=True)
    plan.add_argument("--started-ms", type=int, default=1)
    proof = sub.add_parser("proof")
    proof.add_argument("--expected-head", required=True)
    proof.add_argument("--account-file", required=True)
    proof.add_argument("--report", required=True)
    proof.add_argument("--host-id", required=True)
    sub.add_parser("run")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            return _plan(args)
        if args.command == "proof":
            return _proof(args)
        raise Refused("real_run_not_wired_in_this_build")
    except Refused as refused:
        print(f"REFUSED: {refused.code}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
