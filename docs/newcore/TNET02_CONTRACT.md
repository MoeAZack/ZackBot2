# TNET-02 — real testnet harness adapter, exact-build writer, CLI and gate proof

Status: first-handoff plan (interface review requested from Codex before any network code lands).
Roadmap fit: Wave C / C11 TNET-01 follow-up / BASE-CLEAN hold `real_tnet_missing`.
Base: stacked on PR #291 exact head `c0318c102ca3b3a47a117e2a954f53f309941882` (master `54c41dc6fe60c432bd0ce70dabb99dd3f06ee1d8` + complete classic/algo open-order truth). Rebase onto the accepted #291 merge head if it lands first.

## 1. What this slice adds

TNET-01 (`newcore/testnet_harness.py`, frozen) owns ordering, bounds, assertions, deadlines, cleanup idempotency and the canonical redacted `zackbot-tnet01/1` report. It calls an injected `HarnessAdapter` and an injected `ReportWriter`. TNET-02 supplies the concrete implementations of both, a CLI that binds a run to one exact clean build, and the conversion of the written report into `GateProof(REAL_TNET_EXACT_HEAD, ...)` for `newcore.base_clean`.

No existing file is edited. The kernel, its bounds and its report schema are used unchanged.

## 2. Files (all new)

| Path | Role | Effects |
| --- | --- | --- |
| `newcore/tnet02/__init__.py` | package marker, explicit exports | none |
| `newcore/tnet02/ports.py` | narrow typed ports the adapter needs beyond `VenuePort`: `MarketPort` (mark price, leverage, hedge mode per symbol), `FiltersPort` (tick/step/min-qty/min-notional), `Clock`, `JournalOpener` (open/reopen an NC-02 journal under a given boot ID), `CaseFaults` (see §5) | none (protocols + frozen records) |
| `newcore/tnet02/adapter.py` | `RealTestnetAdapter`: the concrete `HarnessAdapter` (`confirm_binding`, `setup`, `execute`, `cleanup`) and the twelve scenario scripts | only through injected ports |
| `newcore/tnet02/faults.py` | `FaultingSender`: case-scoped deterministic transport fault injection wrapping the #291 sender type | only through the wrapped sender |
| `newcore/tnet02/binance_market.py` | concrete `MarketPort`/`FiltersPort` over signed `GET /fapi/v2/positionRisk` (already allowed by `http_sender`) and the pinned `data/exchange_rules_testnet.json` snapshot | injected sender only (commit 2+) |
| `newcore/tnet02/writer.py` | `ExactBuildReportWriter`: verifies canonical bytes, writes one report atomically, never overwrites | filesystem (out dir only) |
| `newcore/tnet02/gate.py` | strict `decode_report(bytes) -> SuiteReport` and `gate_proof(bytes, *, expected_head, host_id) -> GateProof` | none |
| `tools/tnet02_run.py` | CLI: exact-head + clean-tree identity, manifest build, wiring, dry run | git read, filesystem, network only in non-dry run |
| `tests/newcore_tnet02/…` | hostile fakes and contract tests (§6) | none |

## 3. Interfaces against `newcore/testnet_harness.py`

`RealTestnetAdapter(*, venue: VenuePort, market: MarketPort, filters: FiltersPort, context: AccountContext, journal: JournalOpener, faults: CaseFaults, clock: Clock, risk_policy, management_policy)`

* `confirm_binding(expected)` → `BindingEvidence`. Reads positions and complete classic+algo open orders for **all** symbols (#291 order truth). `isolated` is true only when both reads are `OK`, every position is zero and no open order exists; any `UNKNOWN`/`REJECTED` read raises (kernel → `BINDING`). `binding_digest` is the PBKDF2 key digest (legacy `account_fingerprint` formula, reimplemented without importing `engine.py`) of the API key the injected credential source signs with. `environment` is `TESTNET` only because the #291 venue refuses any other host at construction and per request. `exchange_uid` is `None` (Binance USD-M testnet exposes no UID; the manifest account must carry `None`, so equality stays exact). Hedge mode read from the venue must equal `Account.hedge_mode`.
* `setup(context)` → `SetupEvidence`. Fresh REC-02 `plan_reconciliation` over the journal projection and an exhaustive venue snapshot; `initial_truth=FLAT` and `reconciled=True` only for an empty clean plan. Verifies leverage on the case symbol is ≤ `case.leverage` (read, never assumed). Opens the case journal under `context.boot_id`.
* `execute(context)` → `ScenarioEvidence`. Runs the scenario script (§4). Every risk-increasing send (entry, DCA add) is an NC-06 `risk.evaluate` approval bound to `context.synthetic_intent_id`/`context.authorization_id`; reductions, closes and protection bypass NC-06 as NC-06 requires. Every send goes through NC-04 `AccountContext` (protection lane + rate blocks). Local pre-send guards refuse before the wire: order count would exceed `case.max_orders`; `qty × mark` would exceed `case.max_notional`; suite aggregate would exceed 2,000; leverage above `case.leverage`. Evidence timestamps come only from the injected clock.
* `cleanup(context)` → `CleanupEvidence`. Idempotent by `context.token`: first call cancels owned orders and flattens with reduce-only market orders, then REC-02 reconciles; the second call with the same token re-reads, finds the same terminal state and sends **zero** orders, returning an equal record. Escalation follows the frozen 15 s / 60 s / 120 s deadlines; any unknown read ends in `HOLD` with `reconciled=False` and new work stopped. Cleanup orders are counted.

`ExactBuildReportWriter(out_dir, build_sha)` is a `ReportWriter` (`Callable[[bytes], None]`). It decodes the bytes with `gate.decode_report`, requires `report.build_sha == build_sha`, and writes `tnet01-<build_sha>-<report_sha256[:16]>.json` with exclusive create, fsync and atomic rename. It never overwrites and never writes outside `out_dir`.

`gate_proof(data, *, expected_head, host_id)` returns `GateProof(ProofKind.REAL_TNET_EXACT_HEAD, build_sha, host_id, passed, sha256(data))`. Decoding refuses non-ASCII, duplicate keys, NaN/Infinity, unknown or missing keys, non-canonical bytes (round-trip through `canonical_report_bytes` must be byte-identical) and a `report_sha256` that does not match the kernel recipe. `build_sha != expected_head` is an error, not a failed proof. `passed` is true only when suite status is `passed`, failure is null, exactly the twelve required cases appear in order, and every case is `passed` with final truth `flat` or `protected_reconciled`.

## 4. Scenario mechanics (real testnet)

| # | Case | Mechanism |
| --- | --- | --- |
| 1, 3 | long/short target | market entry → classic `STOP_MARKET` protection (algo fallback only on `-4120`, journal rule G6) → bot-side NC-07 target at a small favourable offset; when the read mark crosses it, reduce-only market close |
| 2, 4 | long/short stop | entry → protection at a small adverse offset; evidence requires the exchange-side stop to have triggered and filled (query + fills) |
| 5 | partial close | entry → protection → NC-07 reduce of half, stop resized; cleanup flattens the rest |
| 6 | bounded DCA | entry → protection → exactly one NC-06-approved add → stop resized to full size |
| 7 | cancel/replace race | protection replaced (cancel + new stop) while a concurrent query observes; exactly one working stop at the end |
| 8 | lost answer | `FaultingSender` forwards the real entry request and drops the real accepted answer (`TimeoutError`); REC-02 adopts the accepted order by client ID; the order is never resubmitted |
| 9 | minimum refusal | qty below the symbol's step/min-notional; NC-06 refuses before any order request; zero entry |
| 10 | restart | protected position; journal closed and reopened under `restart_boot_id`; REC-02 reconciles venue truth to the reopened journal |
| 11 | HTTP 429 | `FaultingSender` returns a synthetic 429 with `Retry-After` for non-protection requests only; NC-04 blocks non-protection sends, protection still goes out, resume at or after the boundary |
| 12 | HTTP 418 | synthetic 418 latches account HOLD; protection lane preserved; no resume inside the scenario |

429/418 are injected at the transport boundary on purpose: deliberately provoking a real Binance IP ban is outside the pre-approved scope and would block the owner's other testnet work. A real 429/418 from Binance is still honoured by the same NC-04 path. Stop/target cases depend on the market crossing the chosen offset inside the 10-minute case deadline; if it does not, the case fails `DEADLINE` honestly and cleanup flattens.

## 5. Fault injection rules

`FaultingSender` is active only while a case token is armed by the adapter for cases 8, 11 and 12. It never fabricates a 2xx answer, never changes a request, never drops a protection request, and is disarmed before cleanup. Each injection is counted and appears only as typed evidence (`accepted_at_ms`, `answer_lost_at_ms`, `RateLimitEvidence`).

## 6. Hostile-fake tests (written before any network code)

1. Binding: fake venue reports another key digest, account ID, venue, or settlement asset; a confirmation older than suite start; a foreign position; a foreign classic or algo order → `BINDING`, zero order requests on the wire.
2. Partial order truth (algo read fails, classic OK) or `UNKNOWN` positions → never flat; binding/setup refused or cleanup `HOLD`.
3. Setup with a pre-existing owned or foreign position/order → `SETUP`, no orders.
4. Bounds: fill or mark price that would push notional over 1,000, or the aggregate over 2,000 → refused before send; a 13th order (including cleanup) refused locally; account leverage above the case leverage → refused.
5. Lost answer: the fake records every wire request; the adapter must reconcile and submit the entry exactly once. Duplicate client ID `-4116` is treated as unknown → reconcile, never resend.
6. 429: early resume attempt (fake clock before `Retry-After`) refused; protection request admitted during the block.
7. 418: HOLD latched, no resume; protection request admitted.
8. Stop route: classic `-4120` → exactly one algo fallback; a second rejection → HOLD, no third attempt.
9. Cleanup idempotency: second call with the same token sends zero requests and returns an equal record; a fake that diverges → `CLEANUP_NOT_IDEMPOTENT`.
10. Cleanup budget: flatten that would exceed 12 orders → HOLD rather than send.
11. Restart: corrupt/missing journal on reopen → HOLD; reuse of the original boot ID refused.
12. Secrets: credential source returning a marker key/signature, and a sender raising an exception whose text contains the marker, a URL and headers → marker, URL and header names absent from report bytes, `HarnessError` text, writer output and CLI stdout/stderr.
13. Clock regression or evidence before setup → fails closed.
14. Writer: existing file, wrong `build_sha`, non-canonical bytes, path traversal in out dir → refused, nothing written.
15. Gate: tampered byte, whitespace, duplicate key, NaN, wrong schema, recomputed digest mismatch, missing/reordered case, `passed` status with a failed case, head mismatch → error or `passed=False` exactly as §3 states.
16. CLI: dirty tree or `HEAD != --expected-head` refused before any adapter/network construction; `--dry-run` builds the manifest and runs the whole suite against fakes only.

Plus a mutation check on the key guard lines and a CP1252-shim run, per `CLAUDE.md`.

## 7. Commit plan

1. This contract, ports, hostile fakes, adapter logic against fakes, writer, gate, CLI dry-run. **No network or order call exists in this commit**: nothing imports `newcore.venue.http_sender`, and the adapter only receives injected ports.
2. `binance_market.py` and the CLI real-run wiring to the existing #291 venue and `http_sender` (no change to `http_sender._PATHS` is needed: mark price and leverage come from the already-allowed signed `positionRisk`, filters from the pinned rules snapshot).
3. Real twelve-scenario run on the confirmed dedicated USD-M testnet account, only after hostile fakes pass and the binding is confirmed.

## 8. Open points for Codex

* Credential source: master has no `CredentialSource` implementation or key store. The real run needs a testnet-only signer on the owner's PC; this slice will not load, print or store secrets. Proposed: CLI accepts an injected signer module path supplied by Codex/owner, and refuses to run without it.
* Offsets for stop/target cases (proposed: max(5 ticks, 0.05 %) from the read mark) and whether one replace-closer step is allowed inside the 12-order ceiling.
* Host ID for the proof: proposed `tnet_<sha256(hostname)[:12]>`, matching the `base_clean` identifier pattern.

## Deferred

Replacement entrypoint, runtime/PAPER wiring, installer, UI, leverage *setting* (the run refuses instead when account leverage exceeds the case), live canaries and all mainnet activity.
