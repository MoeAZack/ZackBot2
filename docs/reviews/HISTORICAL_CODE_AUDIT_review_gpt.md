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
