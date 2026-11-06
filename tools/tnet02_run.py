"""TNET-02 CLI: exact-build identity, suite manifest plan, and REAL_TNET_EXACT_HEAD proof conversion.

    python -m tools.tnet02_run plan  --expected-head SHA --account-file ACCOUNT.json --symbols BTCUSDT[,ETHUSDT]
    python -m tools.tnet02_run proof --expected-head SHA --account-file ACCOUNT.json --report REPORT.json --host-id tnet_<id>
    python -m tools.tnet02_run run   --expected-head SHA --account-file ACCOUNT.json --symbols BTCUSDT
                                     --origin https://demo-fapi.binance.com
                                     --journal-dir DIR --out-dir DIR --host-id tnet_<id> [--execute TESTNET]

``plan`` checks that HEAD equals --expected-head with a clean tracked tree, builds and validates the frozen
twelve-case manifest, and prints it.  It sends nothing and reads no credential.  ``proof`` decodes an exact report
file, refuses a report for another account or key digest, and prints the GateProof as canonical JSON.

``run`` reads the dedicated TESTNET key from ZACKBOT_TNET02_API_KEY / ZACKBOT_TNET02_API_SECRET on this machine and
refuses before any network use unless the key's digest equals the confirmed account file.  The account file names
exactly one approved testnet origin (optional ``origin`` key; a file without it keeps the original
``https://testnet.binancefuture.com`` binding), and ``--origin`` must repeat it.  Each key digest is pinned to the first
origin it was used with, both in a per-user store on this machine (``~/.zackbot/tnet02_origin_pins.json``) and in the
journal directory; a digest pinned to the other origin in either place is refused, so changing the journal directory
or the account file does not let one key reach both origins.  There is no fallback
between origins.  Without ``--execute`` it
is a read-only preflight (exchange rules, rate limits, account facts and the binding/isolation check) and sends no
order.  With ``--execute`` it runs the frozen twelve-case suite on Binance USD-M TESTNET, writes the exact-build
report once, and prints the GateProof.  Output never contains credentials, request URLs, query strings or
exception text; it names only the bare approved origin.

Exit codes: 0 ok / passed proof, 1 failed proof, 3 refused precondition.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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

EXECUTE_ACK = "TESTNET"


EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 3
_ACCOUNT_KEYS = {"account_id", "label", "key_digest", "hedge_mode", "settlement_asset", "confirmed_at_ms",
                 "typed_phrase"}
_ORIGIN_KEY = "origin"
_ORIGIN_PINS = "origin_pins.json"
MACHINE_PIN_STORE = Path.home() / ".zackbot" / "tnet02_origin_pins.json"


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


def _account_raw(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise Refused("account_file_unreadable") from None
    if type(raw) is not dict or set(raw) not in (_ACCOUNT_KEYS, _ACCOUNT_KEYS | {_ORIGIN_KEY}):
        raise Refused("account_file_keys")
    return raw


def account_origin(path: Path) -> str:
    """The one testnet origin this confirmed account is bound to; files without the key keep the original origin."""
    from newcore.venue.binance_usdm_testnet import TESTNET_BASE_URL, TESTNET_ORIGINS

    origin = _account_raw(path).get(_ORIGIN_KEY, TESTNET_BASE_URL)
    if type(origin) is not str or origin not in TESTNET_ORIGINS:
        raise Refused("account_file_origin")
    return origin


def _read_pins(path: Path) -> dict:
    try:
        pins = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise Refused("origin_pins_unreadable") from None
    if type(pins) is not dict or any(type(k) is not str or type(v) is not str for k, v in pins.items()):
        raise Refused("origin_pins_unreadable")
    return pins


def pin_origin(journal_dir: Path, digest: str, origin: str) -> None:
    """Refuse a key digest already used with the other origin; record it on first use.  No network.

    Every store is checked before any is written, so a refusal leaves both unchanged."""
    stores = [Path(MACHINE_PIN_STORE), Path(journal_dir) / _ORIGIN_PINS]
    loaded = [(path, _read_pins(path)) for path in stores]
    if any(pins.get(digest, origin) != origin for _path, pins in loaded):
        raise Refused("credentials_origin_reuse")
    for path, pins in loaded:
        if pins.get(digest) == origin:
            continue
        pins[digest] = origin
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(pins, sort_keys=True), encoding="utf-8")
        except OSError:
            raise Refused("origin_pins_unwritable") from None


def load_account(path: Path) -> Account:
    raw = _account_raw(path)
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
             f"PLAN origin={account_origin(Path(args.account_file))}",
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


def _live_parts(args, account: Account, origin: str):
    """Credential check first (no network), then the read-only exchange reads and the wired adapter."""
    import time

    from newcore.tnet02.journal import FileJournalOpener
    from newcore.tnet02.loss_store import FileLossStore
    from newcore.tnet02.live import (SIGNED_RECV_WINDOW_MS, EnvCredentialSource, LiveAccountPort, LiveFiltersPort,
                                     SignedReader, SyncedClock, read_limit_payloads, server_offset_ms)
    from newcore.tnet02.ports import PortError
    from newcore.tnet02.wiring import build_adapter, key_digest

    try:
        credentials = EnvCredentialSource()
    except PortError as error:
        raise Refused(error.code.replace(".", "_")) from None
    if key_digest(credentials.api_key()) != account.binding.key_digest:
        raise Refused("credentials_digest_mismatch")
    try:
        journals = FileJournalOpener(args.journal_dir)
    except Exception:
        raise Refused("journal_dir_invalid") from None
    pin_origin(Path(args.journal_dir), account.binding.key_digest, origin)
    from newcore.venue import TestnetHttpSender

    try:
        sender = TestnetHttpSender(origin)
    except ValueError:
        raise Refused("origin_invalid") from None

    def local_clock() -> int:
        return time.time_ns() // 1_000_000

    def monotonic() -> int:
        return time.monotonic_ns() // 1_000_000

    try:
        # Sign in server time (measured once, unsigned) with the widest recvWindow the venue accepts.
        offset = server_offset_ms(SignedReader(sender=sender, credentials=credentials, clock=local_clock,
                                               base_url=origin), local_clock)
    except PortError as error:
        raise Refused(error.code.replace(".", "_")) from None
    clock = SyncedClock(local_clock, offset)
    reader = SignedReader(sender=sender, credentials=credentials, clock=clock, base_url=origin,
                          recv_window_ms=SIGNED_RECV_WINDOW_MS)
    try:
        exchange, limits, info, raw = read_limit_payloads(reader)
        filters = LiveFiltersPort(info, raw)
        wired = build_adapter(sender=sender, credentials=credentials, account_id=account.account_id,
                              egress_id="egress-tnet02-cli", exchange_info=exchange, account_order_limits=limits,
                              account=LiveAccountPort(reader, clock), filters=filters, journals=journals,
                              loss_store=FileLossStore(args.journal_dir),
                              clock=clock, monotonic=monotonic, sleeper=lambda ms: time.sleep(ms / 1000),
                              base_url=origin, recv_window_ms=SIGNED_RECV_WINDOW_MS)
    except PortError as error:
        raise Refused(error.code.replace(".", "_")) from None
    except Exception:
        raise Refused("live_wiring_failed") from None
    return wired, clock


def _write_diagnostics(out_dir: Path, head: str, at_ms: int, notes) -> None:
    """Side file of scenario path reasons (prices only).  It is not part of the report or its proof."""
    body = json.dumps({"head_sha": head, "at_ms": at_ms, "notes": list(notes)}, sort_keys=True, indent=1)
    path = out_dir / f"diagnostics-{head[:12]}-{at_ms}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags, 0o600)        # never follow or overwrite a planted file, like the report writer
        with os.fdopen(fd, "wb") as handle:
            handle.write((body + "\n").encode("utf-8"))
    except OSError:
        print("DIAGNOSTICS not written")
        return
    for note in notes:
        print("NOTE " + " ".join(f"{k}={note[k]}" for k in sorted(note)))


def _run(args) -> int:
    from newcore.testnet_harness import run_suite
    from newcore.tnet02.writer import ExactBuildReportWriter, WriterError

    check_identity(args.expected_head)
    account = load_account(Path(args.account_file))
    origin = account_origin(Path(args.account_file))
    if args.origin != origin:
        raise Refused("origin_mismatch")
    if args.execute and args.execute != EXECUTE_ACK:
        raise Refused("execute_ack")
    try:
        writer = ExactBuildReportWriter(args.out_dir, args.expected_head) if args.execute else None
    except WriterError:
        raise Refused("out_dir_invalid") from None
    wired, clock = _live_parts(args, account, origin)
    spec = build_manifest(build_sha=args.expected_head, account=account, symbols=_symbols(args.symbols),
                          started_ms=clock())
    if not args.execute:
        try:
            evidence = wired.adapter.confirm_binding(spec.binding)
        except Exception as error:
            raise Refused("preflight_" + str(getattr(error, "code", "failed")).replace(".", "_")) from None
        print(f"PREFLIGHT origin={origin} clock_offset_ms={clock.offset_ms} account={evidence.account_id} "
              f"digest={evidence.binding_digest} "
              f"environment={evidence.environment.value} settlement={evidence.settlement_asset} "
              f"isolated={str(evidence.isolated).lower()}")
        print("PREFLIGHT read-only: no order was sent")
        return EXIT_OK if evidence.isolated else EXIT_REFUSED
    try:
        run_suite(spec, wired.adapter, writer)
    except Exception:
        _write_diagnostics(Path(args.out_dir), args.expected_head, clock(), wired.adapter.notes)
        raise Refused("suite_aborted") from None
    _write_diagnostics(Path(args.out_dir), args.expected_head, clock(), wired.adapter.notes)
    data = writer.path.read_bytes()
    try:
        proof = gate_proof(data, expected_head=args.expected_head, host_id=args.host_id,
                           expected_account_id=account.account_id,
                           expected_binding_digest=account.binding.key_digest)
    except GateError as error:
        raise Refused(error.code.replace(".", "_")) from None
    print(json.dumps({"kind": proof.kind.value, "head_sha": proof.head_sha, "host_id": proof.host_id,
                      "passed": proof.passed, "evidence_sha256": proof.evidence_sha256,
                      "report": writer.path.name}, sort_keys=True, separators=(",", ":")))
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
    run = sub.add_parser("run")
    run.add_argument("--expected-head", required=True)
    run.add_argument("--account-file", required=True)
    run.add_argument("--symbols", required=True)
    run.add_argument("--origin", required=True, help="exact testnet origin named in the account file")
    run.add_argument("--journal-dir", required=True)
    run.add_argument("--out-dir", required=True)
    run.add_argument("--host-id", required=True)
    run.add_argument("--execute", default=None, help=f"type {EXECUTE_ACK} to place testnet orders")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            return _plan(args)
        if args.command == "proof":
            return _proof(args)
        return _run(args)
    except Refused as refused:
        print(f"REFUSED: {refused.code}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
