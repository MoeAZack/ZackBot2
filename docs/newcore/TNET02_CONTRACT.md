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
2. Adapter, journal, faults, wiring, writer, gate and CLI `plan`/`proof`, all against hostile fakes. Still **no network or order call**: `wiring.build_adapter` takes an injected sender, and the CLI `run` command is refused. See §9 for how the build differs from this plan.
3. Concrete `AccountPort`/`FiltersPort` over signed reads and the CLI `run` wiring to the #291 venue and `http_sender`. This needs the `http_sender._PATHS` additions requested on #219 (unsigned `exchangeInfo`, `/fapi/v1/rateLimit/order`, `/fapi/v2/balance`) and a credential arrangement; it waits for Codex.
4. Real twelve-scenario run on the confirmed dedicated USD-M testnet account, only after hostile fakes pass and the binding is confirmed.

## 8. Open points for Codex

* Credential source: master has no `CredentialSource` implementation or key store. The real run needs a testnet-only signer on the owner's PC; this slice will not load, print or store secrets. Proposed: CLI accepts an injected signer module path supplied by Codex/owner, and refuses to run without it.
* Offsets for stop/target cases (proposed: max(5 ticks, 0.05 %) from the read mark) and whether one replace-closer step is allowed inside the 12-order ceiling.
* Host ID for the proof: proposed `tnet_<sha256(hostname)[:12]>`, matching the `base_clean` identifier pattern.

## 9. As built (commit 2): deviations and disclosures

Files actually added: `ports.py`, `faults.py`, `journal.py`, `adapter.py`, `wiring.py`, `writer.py`, `gate.py`, `tools/tnet02_run.py`, and tests under `tests/newcore_tnet02/`. `binance_market.py` is not built yet (commit 3); the ports are `AccountPort` (hedge mode, settlement asset, equity, per-symbol mark and leverage) and `FiltersPort`. The CLI "dry run" became `plan`, which validates identity and prints the frozen manifest without building an adapter.

* **Stops are algo-only.** The NC-04 cost table has no classic `SUBMIT_STOP`, so every protective stop goes on the algo route (`Action.PROTECT`). This departs from G6 (classic first, algo on `-4120`). Changing it means an NC-04 cost-table edit, which is a shared file and is Codex's call.
* **Binding isolation read bypasses NC-04.** `confirm_binding` reads `venue.open_orders(None)` directly (all symbols, classic + algo) because NC-04 requires a symbol for open orders. It is one read, before any case, and sends nothing.
* **418 "protection lane preserved"** means: under the latched ban nothing is sent, and the confirmed resting stop is unchanged (same client ID, quantity and trigger) when re-read from local truth. No order is attempted under the ban to prove it.
* **Synthetic latches only are released.** Cleanup releases a rate block only when this case latched it through the fault injector. A real exchange 429/418 makes cleanup HOLD and stay blocked.
* **Aggregate notional** is bounded per order as `(position + add) × mark × 1.02 ≤ min(case max_notional, $2,000)`. Cases run one at a time and each must end flat, so the account-wide exposure never exceeds one case's.
* **The harness journal is not NC-02.** `journal.py` is a hash-chained, fsynced JSON-lines file per case, used for write-ahead order state and the restart scenario. Reopen verifies the chain, sequence, identity and torn tail, and HOLDs on any mismatch.
* **Equity comes from `AccountPort`**, which in commit 3 needs `/fapi/v2/balance`.
* **Cleanup order:** flatten with a reduce-only market close first while the stop still rests, then cancel the stop, then require REC-02 `FLAT_CONFIRMED`. The second cleanup call with the same token is read-only.
* **§6 coverage differences.** Item 8 does not apply (no classic stop route; one algo rejection fails the case and cleanup flattens). Item 9: the second cleanup call sends no POST/DELETE but does re-read positions and open orders (GET) to confirm the state is unchanged. Item 16: there is no fake-backed dry run in the CLI; the whole suite runs against fakes in the tests instead. Item 1: account-ID, venue and confirmation-age mismatches are enforced by the kernel's `_binding_matches`, not separately by the adapter. Item 6: an early-resume probe before `Retry-After` is not separately tested (the 429 scenario never probes before it).
* **Direct guard tests.** `test_tnet02_adapter_guards.py` calls the adapter's binding, setup and cleanup guards directly, because the kernel's own checks masked mutants of them in the end-to-end suite. All 15 mutated guard lines are now killed.
* **Adversarial review fixes** (regressions in `test_tnet02_review_regressions.py`): cleanup HOLDs on any unsettled entry/close instead of sending a second close; journal reopen must match an in-process tail anchor (head digest and record count at close), and a restart whose rebuilt state differs from memory HOLDs before adopting it, leaving the stop resting; a stop that the exchange ends without a fill HOLDs (so a 418 case cannot pass with its stop gone); the gate re-checks every frozen bound, case time order and suite window instead of trusting the unkeyed digest, and `proof` refuses a report for another account or key digest; the writer and journal re-check their directory identity at write time (and pin it with a no-follow dir fd where the OS supports it); the venue clock check now advances.
* **Report authenticity.** The report digest is unkeyed, so it proves integrity of the bytes, not who produced them. Authenticity comes from Codex running the exact head on a known host.

## 10. Commit 3: live wiring (owner-directed, 4 Nov 2026)

* `newcore/tnet02/live.py`: `EnvCredentialSource` reads the dedicated testnet key from `ZACKBOT_TNET02_API_KEY` / `ZACKBOT_TNET02_API_SECRET` on the owner's PC only; HMAC-SHA256 signing (checked against Binance's documented example); redacted `repr`, no pickling or copying, codes-only errors. `SignedReader` (GET only, through the injected sender), `LiveAccountPort` (hedge mode, mark price and the higher side leverage from signed `positionRisk`; equity = balance + cross unrealized from `/fapi/v2/balance`), `LiveFiltersPort` (PRICE_FILTER, the stricter of LOT_SIZE/MARKET_LOT_SIZE, MIN_NOTIONAL; only TRADING USDT perpetuals; pinned by SHA-256 of the exact `exchangeInfo` bytes), and `read_limit_payloads` for NC-04.
* **Shared file edited: `newcore/venue/http_sender.py`** (path posted on #219 at push time). Adds the signed read routes `/fapi/v1/rateLimit/order` and `/fapi/v2/balance`, and one unsigned public route, `GET /fapi/v1/exchangeInfo`, accepted only with an empty query and no headers so no key ever travels with it. Exact host, no retry and no redirect are unchanged. Tests in `tests/newcore_tnet02/test_tnet02_sender_routes.py`; the existing sender tests are untouched and pass.
* CLI `run`: identity check, account file, credentials from the environment, and refusal **before any network** unless `key_digest(api key)` equals the confirmed account digest. Without `--execute` it is a read-only preflight (rules, limits, account facts, binding/isolation) that sends no order and exits 3 when the account is not isolated. `--execute TESTNET` runs the twelve cases, writes the report once and prints the GateProof checked against the account and digest.
* Account reads in `LiveAccountPort` go straight through the sender, not the NC-04 budget (a handful of GETs per case). A real 429/418 on them fails closed as `read.account_facts`. *Closed in commit 6 (§13).*
* Adapter HOLD codes were renamed out of the NC-01 reason namespaces (`bind.*`, `cfg.*`, `stop.*`, `open.*`) so the reason-code literal scan stays clean.

## 11. Commit 4: real-exchange hardening (5 Nov 2026)

* A triggered stop whose child order has only partly filled stays WORKING until the child finishes (or the position is gone), instead of being marked CANCELLED early. Callers poll again or HOLD.
* `_cancel` now waits `poll_ms` between polls while a triggered child is still settling, so it does not spend the whole poll budget at once and HOLD spuriously on a real exchange.
* Cleanup turns *any* exception, not only coded `AdapterError`s, into a recorded HOLD with the adapter halted, so the second call returns the same record and sends nothing.
* New test: no general read reaches the exchange between a 429 and its `Retry-After`.
* Python 3.12 (the owner PC's version) passes the tnet02, venue and testnet suites.
* **Open question for the real run, testnet host.** Binance's current USD-M docs (General Info, "Testnet API Information") name `https://demo-fapi.binance.com` as the testnet REST base URL, and new test keys are issued through Futures Demo Trading. NEWCORE's venue (#291) and sender are bound only to `testnet.binancefuture.com`. The paper bot still trades there with an older key, but it is unconfirmed whether a new Demo Trading key works on that host. If it does not, the run needs `demo-fapi.binance.com` added as a second exact testnet origin in the #291 venue and the sender. That is Codex's file and has not been changed here.
  *Resolved in commit 5 (§12).*

## 12. Commit 5: second exact testnet origin (5 Nov 2026)

Codex ruling on #219 (6660693481), with owner approval: support the exact origin `https://demo-fapi.binance.com` beside `https://testnet.binancefuture.com`. #291 stays frozen, so this change lives on #294.

* **Venue.** The venue now offers `DEMO_TESTNET_BASE_URL` and `TESTNET_ORIGINS`, which holds exactly the two strings. The venue accepts only an exact member. It pins the chosen origin at construction, and every request re-checks it, so a swap mid-run is refused even to the other approved origin (`binding_changed`, nothing sent).
* **Sender.** `TestnetHttpSender(base_url=TESTNET_BASE_URL)` binds to one origin at construction. It maps that origin to its one host, and the same host is used for URL validation, `getaddrinfo`, the TLS `server_hostname` and the `Host` header. The sender has `__slots__`, and a forged origin/host pair is refused before DNS. A request for the other approved origin is also refused before DNS. Redirects are still returned unchanged and never followed. The default keeps the original origin, so existing callers and bindings behave exactly as before.
* **Reader and wiring.** `SignedReader(base_url=...)` and `build_adapter(base_url=...)` take the same single origin. The wiring refuses a sender that declares a different origin (`wiring.origin_mismatch`).
* **CLI and binding.** The account file may carry an optional `origin` key with one of the two exact strings. A file without the key keeps the original origin. `run --origin` is required and must equal the account file's origin; otherwise the CLI refuses with `origin_mismatch`. Before any network use, each key digest is pinned to the first origin it ran with, both in a per-user store on the machine (`~/.zackbot/tnet02_origin_pins.json`) and in `<journal-dir>/origin_pins.json`. All stores are checked before any is written. *(Commit 7: an adversarial review found that a journal-only pin could be bypassed with a fresh journal directory. The machine store closes that, and the wiring now also refuses an account reader bound to another origin.)* Reusing a key on the other origin is refused (`credentials_origin_reuse`), and corrupt pins are refused as well. `PLAN` and `PREFLIGHT` print the bare origin; they never print request URLs.
* **Not done.**
  * There is no automatic fallback or host migration; that is deferred by the ruling.
  * The report schema does not record the origin. The account file and the preflight output do.
* **Tests.** `tests/newcore_tnet02/test_tnet02_origins.py` covers:
  * both origins end to end, including the full 12-case matrix over the fake exchange on the demo origin;
  * mainnet, lookalike, case, port, slash, userinfo and downgrade refusals at the venue, sender and reader;
  * cross-origin and tampered-pair refusals before DNS;
  * redirect not followed;
  * CLI origin selection, mismatch, key reuse across origins and corrupt pins.
* **Mutation check.** 25 of 28 mutants on the new lines were killed. The 3 that survive are the overlapping URL checks inside `_validate` (prefix, scheme/host/port, exact reconstruction). For an approved origin/host pair, removing any one of them is caught by the other two, so each is equivalent alone. They are the same layering the sender had before this change.

## 13. Commit 6: account reads go through the NC-04 budget (5 Nov 2026)

This closes the disclosure in §10.

* **Reservation.** `LiveAccountPort.bind_budget(budget)` is called by `build_adapter` with the venue's own `AccountBudget`. Before each signed `positionRisk` or `balance` read, the port reserves the documented weight 5 from the **GENERAL** pool.
* **Refusal.** The reservation is refused (`account.budget_refused`, nothing sent) in three cases:
  * during `BLOCKED_UNTIL`;
  * during `BLOCKED_UNKNOWN_DURATION`, including a 418 ban;
  * when general weight is saturated.

  Account facts are never charged to the protection reserve. The adapter turns the refusal into the HOLD `read.account_facts`.
* **Rate evidence.** `SignedReader.set_rate_observer` passes every account-read response (status, Retry-After, used weight) to `budget.observe`. A 429 or 418 seen on an account read therefore blocks the venue's general work too, and an `X-MBX-USED-WEIGHT-1M` header raises the tracked usage. The budget and the observer can each be bound only once. If the observer fails, the read fails closed (`reader.rate_observer`).
* **Startup reads.** The startup reads (`exchangeInfo` and `rateLimit/order`) happen before the budget exists, because the budget is built from them. They stay outside it, once per run.
* **Tests.** `tests/newcore_tnet02/test_tnet02_account_budget.py` has 13 tests.
* **Mutation check.** 10 of 10 mutants on the new lines were killed.

## 14. bounded_dca trigger follows the mark it is compared with (6 Nov 2026)

The first real run (head cf7fc97, 6 Nov 2026 02:20-02:47Z, report_sha256 c8214b8f…) passed 11 of 12 cases. bounded_dca failed `dca_adds == 1`. It placed only an entry, a stop and a close, and ended flat.
- The ETHUSDT long filled at 2238.26 while mark was about 2239.17. The trigger was taken 0.05% under the fill (2237.14), but it is compared with polled mark prices.
- Mark only reached 2236.98, inside a single 1-minute candle, so the polls missed it.
- The 8-minute wait then ran out.

Change:
- The DCA trigger is `trigger_offset` beyond the most adverse mark the case has read. That includes the mark read just after entry, so only a fresh adverse move can fire it.
- The trigger is also clamped to at least one tick adverse to the actual fill.
- The wait is `min(9 min, case deadline - 60 s)` (see §15 for the shared settings).
- `dca_adds == 1` and every NC-06 and protection check are unchanged.
- The CLI writes `diagnostics-<head12>-<ms>.json` next to the report. It records why each DCA path ended (`dca.added`, `dca.no_trigger`, `dca.no_proposal`, `dca.stop_crossed`, `dca.nc06_refused`, `dca.add_unconfirmed`), with mark low/high and the trigger, and holds prices only.
- The diagnostics file is not part of the report, digest or proof.
- The case still depends on the market: a quiet 9 minutes fails honestly as `assertion`.

## 15. Targets follow the mark; shared trigger window; error codes recorded (6 Nov 2026)

The second real run (head 7636dfa, 02:59-03:32Z, report_sha256 81acd9f4…) passed 9 of 12; bounded_dca passed.
- **short_stop** failed. The mark rose 0.042% against a stop 0.05% away.
- **partial_close** failed. The target was 0.05% above a fill that sat above mark; the mark reached +0.036%.
- **restart_open_position** failed as `adapter` with no recorded cause. Its journal shows the reopen succeeded and cleanup closed it flat.

Changes (pass conditions unchanged):
- `_target_price` uses the same rule as the DCA level: `trigger_offset` beyond the most favourable mark seen, and at least one tick beyond the fill.
- The default `trigger_offset` drops from 0.05% to 0.03%. Every trigger case still needs a real mark move and a real exchange fill or trigger, so the change only affects how far the market must move in the window. All six misses across both runs reached at least 0.036%.
- Target, stop and DCA waits all use `min(trigger_wait_ms = 9 min, case deadline - finish_margin_ms = 60 s)`. These replace `dca_wait_ms` / `dca_finish_margin_ms` from §14.
- `_refresh_stops` retries a stop read that did not complete, or returned UNKNOWN/NOT_FOUND, up to `settle_polls` times. These are read-only retries. It still HOLDs `refresh.stop_unknown` if the stop stays unknown.
- `execute` records `adapter.<code>` for an `AdapterError` and `error.<ClassName>` for any other exception in the diagnostics notes. Messages are never recorded. Target and stop paths also note `target.no_trigger` / `target.no_reduce` / `stop.no_trigger` / `stop.unconfirmed`.

## 16. Bounded retry of account-facts reads; failure notes carry level and cause (6 Nov 2026)

The third real run (head 777badeb, 03:45-04:16Z, report_sha256 95ffe75f…) passed 9 of 12.
- **long_target** and **partial_close** failed as `target.no_trigger`. BTC drifted down while both longs waited for the target.
- **bounded_dca** failed as `adapter.read.account_facts`. A single failed read ended the case, and the port code that caused it was dropped.

Changes:
- `_facts` retries a read-only account read up to `settle_polls` times, with `poll_ms` between attempts, for transient codes only: `reader.transport`, `reader.response`, `reader.http_status`, `reader.malformed`, and `account.mark_price` (LONG and SHORT positionRisk rows caught mid-update).
- Rate answers (429/418), budget refusals, and credential, origin or shape errors are not retried.
- The final failure HOLDs `read.account_facts` as before, and now carries `cause=<port code>`, or `error.<Class>` for an unexpected exception.
- Adapter-failure notes now record `cause` and the case's current target, stop or DCA `level`. Notes never record messages.

The direction dependence of target and stop cases is settled in §17.

## 17. Bounded further attempts for market-trigger scenarios (owner decision, 6 Nov 2026)

Across four real runs (cf7fc97, 7636dfa, 777badeb and 5d63b5c), every scenario passed at least once, but never all twelve in the same run. The six market-trigger scenarios passed 15 times in 24 attempts. Every failure in run 4 was price not moving far enough in the needed direction within the window. moe chose "Retry a scenario that saw no move" on the decision card.

Rule (kernel `newcore/testnet_harness.py`, report schema `zackbot-tnet01/2`):

- `RETRYABLE_SCENARIOS` = long/short target, long/short stop, partial close and bounded DCA. `MAX_ATTEMPTS = 3`.
- An attempt is a **clean miss** (`_retryable_miss`) only if every one of these holds:
  - the failure is ASSERTION;
  - the evidence identity matches the context;
  - the entry was NC-06-authorized, filled and protected;
  - no rate-limit evidence was recorded;
  - the attempt ended reconciled FLAT or PROTECTED_RECONCILED;
  - cleanup reconciled it FLAT.

  Adapter, setup, deadline, bounds, cleanup and HOLD failures are never retried.
- Further attempts run only after the whole first pass, in manifest order, so each one sees a later market window. Each attempt gets:
  - a fresh case id: `_derive("case", suite_id, original, "attempt", n)`;
  - from that id, fresh intent ids, client order ids, a fresh journal and a fresh cleanup token.
- No further attempts are made if any other scenario already failed, because the suite cannot pass and orders would be wasted.
- `CaseReport` gains `attempt` (1..3) and `attempt_of` (the first attempt's case id, or null). Every attempt is reported.
- Pass rule `attempts_complete`, shared by the kernel and `gate.report_passed`:
  - the first twelve reports are attempt 1 of each required scenario, in order;
  - every later report is attempt n+1 of a retryable scenario's chain;
  - every attempt but the last in a chain is FAILED / ASSERTION / FLAT;
  - every chain ends PASSED.
- The gate still re-checks bounds, safe terminals and time order for **every** attempt.
- `MAX_SUITE_MS` rises from 60 to 120 minutes. The per-scenario limits (10 min, 12 orders, $1,000, 3x) and the $2,000 aggregate are unchanged.

## Deferred

Replacement entrypoint, runtime/PAPER wiring, installer, UI, leverage *setting* (the run refuses instead when account leverage exceeds the case), live canaries and all mainnet activity.
