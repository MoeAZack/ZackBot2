# FACTORY-F01-PARITY-01: C-F01 runtime-vs-replay parity contract

**Status: DRAFT - unsigned.** `factory_f01_parity_01_contract.v1.json` is authoritative; the JSON wins any conflict.

Assignment: Codex ruling 6512403017 (contract/preflight only). Citations are file:function at master `2204e35`. 33
functions are pinned by source sha256 in the JSON (the seven candidate functions equal the signed family contract pins).
A mismatch means this contract must be re-drafted.

This ticket installs nothing, enables nothing, changes no settings or state, reads no market data and produces no result.

## Binding

- **C-F01** = (`ema_mom`, long), 4h, risk 1 %, share 1/6, max_pos 4, card mgmt `{}` (effective `{stop_atr: 2.5}`),
  symbols = the exact ordered `CAND_FIXED18`. Family `trend_long`, one shared cap of 4.
- Bound to the SIGNED family contract (ruling 6511411795) and the SIGNED repro contract (ruling 6489310660). Their
  file sha256s at `2204e35` are pinned in `binding.signed_contracts`.
- **C-F02 stays OFF** (family OQ4). The check: its SLEEVES entry has `enabled == false` at preflight, at every cycle
  and at the end. It has 0 lots, working entries and rows during the canary (P13). A violation is stop condition S2.

## Replay

The replay is the signed repro `semantics` block, F01 cell only. It runs on **the exact closed 4h frames the runtime
used**, hashed in `frames_manifest.json`. It keeps its own $500 single-cell account. The Evidence lane runs it with an
independent harness. Build does not compute verdicts.

Trade-set rule: the runtime and replay trade sets on (symbol, entry bar, side) must match. The only exceptions are
entries the runtime refused for a logged NOT-COMPARABLE or MEASURED-DIFF reason, plus the replay trades that follow from
that divergence on the same symbol. Every exception is listed.

## Parity fields (30)

| class | n | fields |
|---|---:|---|
| MUST-MATCH | 18 | decision bar, le, lx, signal ATR, raw signal count, held refusal, cap refusal, concurrent open count, C-F02 activity = 0, risk fraction 1/6 x 1 %, stop distance 2.5 ATR, qty formula, initial stop = fill - R_dist, static mgmt, entry bar, exit bar + why, stop-first, cost status |
| MEASURED-DIFF | 8 | halt windows, leverage cap, entry fill, exit fill, trade R, fees, funding, slippage |
| NOT-COMPARABLE | 4 | isolation by another slot, runtime-only refusals (exchange rules, outage, gates, order failure, deferral), legacy floor, absolute size |

Tolerances are exact, except: signal ATR and stop distance (rel 1e-9), risk_usd (abs $0.005), qty (one exchange step,
floor only) and stop level (one tick). Each field cites a runtime source and a replay `semantics` key.

## Failure injections (offline, FakeX)

| id | injection | tests |
|---|---|---|
| FI1 | restart mid-position | **new** `test_fi1_...` |
| FI2 | pending-cost row > 6 h, C-F02 OFF | existing x2 + **new** `test_fi2_...` |
| FI3 | evicted row behind > 3000 closes + restart | existing x3 (cand02 family safety) |
| FI4 | lost protection (restoring / owner_check) | existing x2 + **new** `test_fi4_...` |
| FI5 | drift / family order / symbol order / guard error | existing x5 |

## PAPER canary: SPECIFIED, NOT AUTHORIZED

`authorization` is null. Running the canary needs a separate Integration comment id.

- **Preconditions C1-C10:**
  - a flat start, or a reconciled start that Integration accepts;
  - C-F02 OFF;
  - guard green and an empty pending index;
  - no card drift;
  - neutral sizing: governor off, kelly none, ctrl factor 1.0;
  - market entries, AI filter off, fee gate off, 10x leverage;
  - pins equal the running build;
  - the authorization id recorded.
- **Proposed bounds (OQ1):** 30 days, 8 closed trades, max 4 concurrent, 4 R drawdown.
- **Stop conditions S1-S7:**
  - S1: a guard disable;
  - S2: any MUST-MATCH divergence;
  - S3: protection loss;
  - S4: costs pending >= 6 h;
  - S5: the drawdown bound;
  - S6: the bound reached;
  - S7: any mainnet, credential or non-testnet event.
- **Rollback:** Integration alone switches C-F01 off in settings. Open lots keep their stop and management. Confirm on
  the next cycle, then publish the evidence.
- **Evidence:** `preflight.json`, `frames_manifest.json`, `decisions.csv`, `trades.csv`, `verdict.json`,
  `MANIFEST.sha256`. No order, trade or stop ids.

## Forbidden

- Install, restart or any PAPER action.
- Enabling C-F01 or C-F02, or any settings or state mutation.
- Running the canary without authorization.
- Any market-data read under this ticket.
- New results, or tuning.
- Order or trade ids.
- Credentials, mainnet, or MODE / LIVE_CONFIRM.

## Open questions for Integration

1. Canary bounds: duration, trade count and drawdown (proposals above).
2. Sealed holdout vs forward data. The runtime needs >= 250 closed 4h bars, so canary frames include bars after
   2026-09-28. Is that a permitted forward read, and from which bar does the comparison start?
3. Flat start only, or a reconciled start?
4. Confirm the trade-set exception rule.
5. Confirm the neutral sizing settings (C6-C8).
6. Accept the tolerances.
7. Confirm Cowork as the replay and verdict executor.
8. Should open lots on rollback be managed to their own exit (the default here) or closed by Integration?
