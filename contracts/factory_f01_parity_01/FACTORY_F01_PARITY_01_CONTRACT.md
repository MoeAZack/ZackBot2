# FACTORY-F01-PARITY-01: C-F01 runtime-vs-replay parity contract

**Status: DRAFT - unsigned.** `factory_f01_parity_01_contract.v1.json` is authoritative; the JSON wins any conflict.

Assignment: Codex ruling 6512403017 (contract/preflight only). **Amendment 1** encodes the OQ1-OQ8 rulings of Codex
issuecomment **6512712537** (reviewed head `0bcb6c4`). Citations are file:function at master `2204e35`. 34 functions are
pinned by source sha256 in the JSON. The seven candidate functions equal the signed family contract pins. A mismatch means
this contract must be re-drafted.

This ticket installs nothing, enables nothing, changes no settings or state, reads no market data and produces no result.
**Canary execution and market-data reads stay forbidden. The authorization is null.**

## Binding

- **C-F01** = (`ema_mom`, long), 4h, risk 1 %, share 1/6, max_pos 4, card mgmt `{}` (effective `{stop_atr: 2.5}`),
  symbols = the exact ordered `CAND_FIXED18`. Family `trend_long`, one shared cap of 4.
- Bound to the SIGNED family contract (ruling 6511411795) and the SIGNED repro contract (ruling 6489310660). Their
  file sha256s at `2204e35` are pinned in `binding.signed_contracts`.
- **C-F02 stays OFF** (family OQ4):
  - Its SLEEVES entry must have `enabled == false` at preflight, at every cycle and at the end.
  - It must have 0 lots, 0 working entries and 0 rows during the canary (P13).
  - A violation triggers stop condition S2.

## Replay and scoring

- **Replay:** the signed repro `semantics` block, F01 cell only. It runs on the exact closed 4h frames the runtime used
  (`frames_manifest.json`) and keeps its own $500 single-cell account.
- **OQ7 - who runs what:**
  - Cowork runs the replay independently and owns `verdict.json`.
  - Build supplies immutable, hashed runtime extracts only. Build never issues a verdict on its own work.
- **OQ2 - scoring start:**
  - At least 250 older closed bars may be read, as unscored warm-up only.
  - The authorization / start timestamp is frozen before any scored bar is read.
  - Scoring starts at the first fully closed 4h decision bar after **both** the authorization and a successful preflight.
  - Bars after 2026-09-28 may be read for this forward PAPER run only. They may never be used to retune or select the
    candidate.

## Trade-set rule and causal window (OQ4)

- **What a refusal explains:** a runtime refusal explains exactly one decision. It counts only if it was **logged at that
  decision** and its reason is in the registered prefix list (literal engine refusal text from P08 / P09 / P10).
- **What the window excludes:** the refusal opens a listed causal window. Only replay-only entries inside that window are
  excluded.
- **When the window closes:** when runtime and replay are both flat for the candidate.
- **What always fails:**
  - an open-ended same-symbol exemption;
  - a post-hoc reason;
  - a pre-gate mismatch (raw signal, decision bar or count), even inside a window;
  - a runtime-only entry;
  - any other divergence.

  Each of these is PARITY-FAIL.

The algorithm and 8 test vectors (V1-V8) are in `causal_window`. The tests implement it as a reference helper inside
the test module only, never in the engine.

**N1 (note for Integration):** under the literal ruling, a runtime-only entry inside a window is PARITY-FAIL. Example:
the replay sits at the cap of 4 because it holds the explained trade, while the runtime has a free slot and enters.
Integration may extend the window to cover this mirror case.

## Parity fields (30)

| class | n | fields |
|---|---:|---|
| MUST-MATCH | 18 | decision bar, le, lx, signal ATR, raw signal count, held refusal, cap refusal, concurrent open count, C-F02 activity = 0, risk fraction, stop distance, qty, initial stop, static mgmt, entry bar, exit bar + why, stop-first, cost status |
| MEASURED-DIFF | 8 | halt windows, leverage cap (actual effects), entry fill, exit fill, trade R, fees, funding, slippage |
| NOT-COMPARABLE | 4 | isolation by another slot, runtime-only refusals (incl. the recorded AI / fee-gate refusals, LISTED), legacy floor, absolute size |

**OQ6 tolerances:**
- P04 / P16: rel 1e-9.
- P14: $0.005. It uses the frozen neutral multipliers.
- P17 (qty) and P19 (stop):
  - the raw value must match at rel 1e-9; **and**
  - the exchange-quantized value (`feasibility.round_step`) must match **exactly**. There is no step or tick tolerance.
  - Vectors Q1-Q5 are in `quantization_rule`.

## Failure injections (offline, FakeX)

| id | injection | tests |
|---|---|---|
| FI1 | restart mid-position | **new** `test_fi1_...` |
| FI2 | pending-cost row > 6 h, C-F02 OFF | existing x2 + **new** `test_fi2_...` |
| FI3 | evicted row behind > 3000 closes + restart | existing x3 |
| FI4 | lost protection (restoring / owner_check) | existing x2 + **new** `test_fi4_...` |
| FI5 | drift / family order / symbol order / guard error | existing x5 |

## PAPER canary: SPECIFIED, NOT AUTHORIZED

- **OQ1 - bounds:** a parity sample, not a profitability verdict.
  - The canary ends at the first of **30 calendar days or 8 closed C-F01 trades**.
  - Max concurrent is 4.
  - Risk is the frozen 1 % of the 1/6 sleeve.
  - It stops at **4.0 R** measured net drawdown from the canary peak.
- **OQ3 - start state:** candidate-flat.
  - C-F01 and C-F02 have no lot and no pending, resting, unconfirmed or deferred entry.
  - Other PAPER slots may stay open only if they are captured in preflight (C1b).
  - A pre-canary C-F01 lot is never scored.
- **OQ5 - neutral sizing:** no global PAPER setting is changed.
  - C-F01 governor, Kelly and control multipliers must be 1.0.
  - Entry must be at market, with the 10x cap.
  - The existing AI and fee-gate values are recorded and never switched off (C6-C8).
- **Stop conditions:**
  - S1: a guard disable.
  - S2: a MUST-MATCH divergence.
  - S3: protection loss.
  - S4: costs pending for 6 h or more.
  - S5: the 4 R drawdown.
  - S6: 30 days or 8 trades reached.
  - S7: a mainnet, credential or non-testnet event.
- **OQ8 - rollback:**
  - Integration disables new C-F01 entries.
  - Open C-F01 lots keep confirmed exchange protection and their normal signal / stop management.
  - Integration force-closes a lot only for one of these:
    - unknown account state;
    - lost or unrestorable protection;
    - environment uncertainty;
    - a separately recorded safety decision.
- **Evidence:**
  - Build writes `preflight.json`, `frames_manifest.json`, `decisions.csv`, `trades.csv` and `MANIFEST.sha256`.
  - Cowork writes `verdict.json`.
  - No order, trade or stop ids appear in any file.

## Forbidden

- Install, restart, or any PAPER action.
- Enabling C-F01 or C-F02, or changing any setting or state (including global PAPER settings).
- Executing the canary.
- Market-data reads under this ticket.
- Importing a pre-canary lot.
- Post-hoc reasons.
- Build issuing a verdict.
- New results, or tuning.
- Order or trade ids.
- Credentials, mainnet, or MODE / LIVE_CONFIRM.

## Resolved rulings (Integration 6512712537)

OQ1 bounds · OQ2 forward read · OQ3 candidate-flat start · OQ4 narrowed causal window · OQ5 no global setting changes ·
OQ6 exact post-quantization · OQ7 Cowork owns the verdict · OQ8 manage open lots by default. Full text:
`resolved_questions`. There are no open questions; N1 is a note for Integration.
