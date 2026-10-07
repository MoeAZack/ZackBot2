# Codex gate — historical code audit

Audit input: `audit/historical-code-audit@31fc0804d1ba7b32d6c7a0b01ef895429dc27e81`.

Status: **phase 1 accepted and ordered; full C01–C30 disposition remains in progress.** This document is the independent
gate, not a blanket acceptance of Claude Code's severity labels or proposed ticket grouping.

## Working contract

- **Claude Code is the only product-code implementer.** It starts each accepted `AUD-*` ticket from current protected
  master and may use `cowork/*` branches only as reference evidence. It does not cherry-pick them blindly.
- **Cowork validates runtime, Windows, UI, exchange behaviour and adversarial scenarios** after an exact Code head is
  handed off. Cowork does not edit the product implementation.
- **Codex owns independent reproduction, severity, ticket boundaries, acceptance and protected merge.** The implementer's
  reproducer is never the only evidence.
- One narrow ticket/PR at a time. No broad cleanup is mixed into an order-path fix. Every fix must include a regression
  that fails on its parent commit and pass the existing safety/replay gates.

## Phase 1 disposition — C01 through C10

| ID | Codex decision | Final placement |
|---|---|---|
| C01 | **Accept P0; PAPER/testnet blocker. Independently reproduced.** A failing synchronous trade-log write repeats exchange actions and prevents post-fill state/stop updates. | `AUD-01`, first audit implementation ticket. |
| C02 | **Accept P1; PAPER/testnet blocker. Independently reproduced.** A rejected add aborts all later management for the lot, including its trailing stop. | `AUD-02`. |
| C03 | **Accept P1, but split the duplicate.** PR #20 fixes the loose quantity-tolerance part. Success-path fill truth and adoption/protection of ambiguous filled entries remain open. | Finish/merge PR #20, then `AUD-03`; use `cowork/eng02` only as reference. |
| C04 | **Accept P1.** Routine stop existence/repair remains open and must be tested with AUD-03 provisional stops so one safety layer cannot cancel the other's protection. | `AUD-04` after AUD-03; use `cowork/eng01` only as reference. |
| C05 | **Retain as an unproven mainnet P1 scenario, not an implementation fact.** Same-client-id resend needs a controlled testnet latency probe before choosing retry semantics. | Evidence subtask of the later rate/order-budget ticket; cannot block PAPER work but blocks mainnet. |
| C06 | **Accept the confirmed parts; split scope.** Global 418/429 handling is a small client ticket. Shared weight budgeting and transient-stop policy are separate order-router work. | `AUD-11a` rate semantics after AUD-04; `AUD-11b` shared budget/transient protection after lock extraction. |
| C07 | **Accept P1 for controls/resilience.** Network/AI/sleeps under the engine lock and a stale-green panel are confirmed. | `AUD-10`, after lifecycle fixes AUD-01–04 to avoid competing edits. |
| C08 | **Accept P1; PAPER/testnet blocker.** Corrupt settings/state may silently reset safety policy or ownership. | `AUD-05`, after AUD-02; independent of strategy work. |
| C09 | **Raise P2 → P1.** A pending close can consume a sibling lot's exchange quantity and invalidate its stop coverage. | Merge into `AUD-02` with the management-stage isolation work. |
| C10 | **Accept, split truthfulness from behaviour.** Relabelling invalid DCA results is safe and immediate; disabling new DCA entries and publishing corrected numbers require separate evidence. | `AUD-06a` = PR #21 labels; `AUD-06b` = DCA off by default + BT01/BT02 reruns after BT02 merge. |

## Independent reproductions

These were run on the audited master `ef6abc0`, using the repository fake exchange but fresh Codex scenarios rather than
the audit's scripts.

### C01 — locked trade log repeats TP1 and leaves stop quantity stale

Setup: one 1.0 BTC fake lot, 50% TP1, price moved through TP1, and `log_trade` raises `PermissionError` as a Windows
exclusive file lock would. Four management passes produced:

- exchange position: `0.063`;
- engine lot quantity: `0.063`;
- `tp1`: still `false`;
- stop quantity: still `1.0`.

The close repeated four times and the exchange stop was never resized. This confirms P0 and the AUD-01 ordering.

### C02 — rejected add starves the trailing stop

Setup: pyramid + 1 ATR trailing management, price `100 → 130`, and every add rejected. After management:

- `best`: still `100`;
- stop: still `95`;
- no durable `add_blocked` state;
- the add error was recorded, but the trailing stage never executed.

This confirms the single per-lot try block is an unsafe sequencing boundary.

## Implementation order handed to Code

1. Finish the narrow PR #20 orphan-boundary correction already under review.
2. `AUD-01` — order-path bookkeeping must survive every logging/persistence failure.
3. `AUD-02` — management-stage isolation plus pending/resting close ownership.
4. `AUD-03` — exchange-truth quantities and ambiguous-entry adoption/protection.
5. `AUD-04` — routine stop verification/repair, including AUD-03 interaction.
6. `AUD-05` — fail-closed durable settings/state and atomic settings application.
7. `AUD-06a` / `AUD-06b` — truthful DCA labels, then DCA-off and verified numbers.
8. `AUD-10`, then split `AUD-11a`/`AUD-11b` — lock responsiveness, rate semantics, shared request budget and transient
   protection policy.

BT02 remains the immediate protected-merge dependency. Remaining C11–C30 grading will be appended before the audit is
declared fully accepted. No mainnet authority is granted.

## Phase 2 disposition — C11 through C30

| ID | Codex decision | Final placement |
|---|---|---|
| C11 | **Accept, lower P1 → P2.** The engine uses elapsed wall-clock bars while the backtest counts entry-bar indices, so parity is wrong; this blocks published results, not current stop protection. | `AUD-07`. |
| C12 | **Accept, lower to P2 while maker entry remains off by default.** Promote to P1 if maker becomes a default or copy-profile option before correction. | `AUD-07`. |
| C13 | **Accept as several P2 parity tasks, not one implementation change.** Each behaviour needs its own regression and reason-code comparison. | `AUD-07`; corresponding coverage in `AUD-08`. |
| C14 | **Accept P1 structural correctness debt.** Duplicated sizing/Kelly/governor/risk logic already disagrees and prevents trustworthy parity. Extraction must follow characterization, never precede it. | `AUD-09` after AUD-08 and test injection. |
| C15 | **Accept P2 gate weakness.** Missing modes explain why behaviour drift survived. | `AUD-08`; move the corrected BT01 modes into the per-commit gate. |
| C16 | **Raise P2 → P1 mainnet blocker, split UI from accounting.** Estimated commissions/funding and truncated capital history must not drive live guards, Kelly or compounding. | Accounting in `AUD-15`; truncation/presentation in `AUD-14`. |
| C17 | **Raise P2 → P1 release blocker.** Hard-kill/provenance/secret-copy concerns do not block PAPER research but do block distributable or mainnet builds. | `AUD-13`. |
| C18 | **Conditional P1 if reproduced; currently P2/runtime-evidence-needed.** Two engines on one account would be severe, but the Windows bind race needs an independent two-process proof. Paper→live state separation is accepted as a release requirement. | `AUD-13`; Cowork owns the Windows proof. |
| C19 | **Accept P2 mainnet.** Chat-level authorization and optional PIN are unsuitable for real-fund remote control; thread fan-out is resilience debt. | `AUD-15`. |
| C20 | **Lower P2 → P3 for the current single-owner testnet repository.** Before outside contributors or release, workflow trust must move outside editable PR content or require an independent protected approval. | `AUD-12` process hardening. |
| C21 | **Accept as enabling test debt, not a product severity.** Prioritize only seams needed for AUD-01–11; do not pause safety fixes for a sweeping test rewrite. | `AUD-12`, incremental. |
| C22 | **Accept P1, split fact from policy.** Divergent validators are confirmed. The Kelly×governor maximum is a risk-policy decision and needs one explicit combined ceiling rather than an accidental product. | Validator/risk contract in `AUD-09`; owner-visible ceiling in the same ticket. |
| C23 | **Accept P2, split resilience from performance.** Silent worker death and invisible audit health are correctness issues; full-file rewrites are later optimization. | `AUD-14`; rewrite optimization only after correctness. |
| C24 | **Accept P2 truthfulness for truncation/error visibility.** Lower the symbol/side escaping concern to P3 because venue-controlled values are constrained, while still fixing it as defense in depth. | `AUD-14`. |
| C25 | **Split.** Partial-TP rounding-to-zero is **P1 testnet** and belongs with management correctness. T03c default policy, ordinary-maker leverage race and one-way mode are P2/mainnet gates. | Zero-close regression in `AUD-02`; remaining items in `AUD-15`. |
| C26 | **Raise P2 → P1 release blocker.** Heartbeat, kill switch and incident playbook are mandatory before unattended mainnet, not before local PAPER research. | `AUD-15`. |
| C27 | **Accept P3 cleanup.** Instruction conflict is already resolved by the owner; remove only proven dead code. | `AUD-16`. |
| C28 | **Accept P3.** Bound memory/payload growth and synchronize shared registries when touched by their owning tickets. | `AUD-16`, or opportunistically with direct regression tests. |
| C29 | **Accept P3.** Client error hygiene; no separate project gate. | `AUD-16`. |
| C30 | **Accept P3 research-method debt.** Enforce labels/assumptions before strategy promotion. | T09a research gates, not engine work. |

## Final deduplicated ticket spine

The original 16-ticket proposal is retained as a source map but implemented through this narrower spine:

1. **Drain accepted/open gates:** BT02 protected merge; finish PR #20; rebase/review PR #19; rebase/review PR #21
   (`AUD-06a`).
2. **Order-path safety:** `AUD-01` → `AUD-02` → `AUD-03` → `AUD-04`.
3. **Durability:** `AUD-05`.
4. **DCA truth:** `AUD-06b` (off by default and verified numbers; labels already in 06a).
5. **Backtest truth and gates:** `AUD-07` → `AUD-08`.
6. **Test seams/shared contracts:** incremental `AUD-12` prerequisites → `AUD-09`.
7. **Responsiveness and exchange traffic:** `AUD-10` → `AUD-11a` (418/429 semantics) → `AUD-11b` (shared budget,
   transient protection and the C05 probe decision).
8. **Release/mainnet lane:** `AUD-13` → `AUD-14` → `AUD-15`.
9. **Cleanup:** `AUD-16`, with C30 enforced through T09a rather than product-code churn.

This ordering makes Code's implementation base safer: exchange-state truth and protection are fixed before refactoring;
characterization precedes shared-core extraction; and Cowork receives small exact heads with explicit runtime claims to
validate instead of being asked to reinterpret a large mixed branch.
