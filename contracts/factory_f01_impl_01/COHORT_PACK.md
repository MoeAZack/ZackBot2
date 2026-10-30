# FACTORY-F01-IMPL-01: C-F01 bounded PAPER canary - cohort pack

**Status: NOT ACTIVATED. `authorization: null`.** `cohort_manifest.v1.json` is authoritative; the JSON wins any conflict.

Assignment: Codex ruling 6513808658 (parallel implementation; only activation waits). The governing contract is
FACTORY-F01-PARITY-01 (PR #248, head `d255b3f`, OQ1-OQ8 ruled 6512712537). This pack operationalizes that contract. It
changes no rule in it. Activation needs Cowork's PASS on the #248 exact head and a separate Integration authorization
comment.

## Flag

- Settings key `F01_CANARY`. It is **absent by default, which means OFF**. When OFF the engine behaves exactly as on master.
- Shape: `{"on": true, "start_utc": "<tz-aware ISO UTC>", "authorization_comment_id": "<id>", "cohort_dir": "<abs path outside repo>"|null}`.
  Only the JSON boolean `true` turns the flag on.
- **Testnet guard.** The flag is honoured only when `Engine.live is False` **and** the trading client's base URL is exactly
  `binance_client.TESTNET`. Anywhere else every new C-F01 entry is refused and the refusal is logged (S7, fail closed).
- **What the flag never does:**
  - enable C-F01 (the slot's `enabled` flag stays Integration's switch);
  - touch C-F02, which stays OFF;
  - change a global PAPER setting;
  - close or resize a lot.
- **Code:**
  - `f01_canary.py`;
  - `Engine.f01_canary_block`, called from `Engine._rules_block` (automatic entries only);
  - `Engine.f01_canary_preflight`;
  - observe-only hooks in `miss`, `_audit_opened`, `_measured_close` and `_measured_followup`.
- **Pins:** none of the 34 functions pinned by the parity contract is edited. A test re-hashes all of them against
  `pins_expected`.

## Bounds (OQ1) and rollback semantics (OQ8)

- **When new C-F01 entries stop:** at the first of these:
  - 30 calendar days from `start_utc`;
  - 8 closed C-F01 trades, counted from the start;
  - 4.0 R measured net drawdown from the canary peak.
- **What counts:** only rows opened **and** closed at or after the start. A pre-canary lot is never counted.
- **How R is measured:**
  - R per trade = measured / acknowledged net pnl / `risk_usd`.
  - The peak includes the 0 R start.
- **Pending costs fail closed:** a counted close whose costs are pending blocks new C-F01 entries until it is measured.
  "Pending" is read from the row view or the durable `candidate_cost_pending` index.
- **When a bound is reached:**
  - new C-F01 entries are refused with the bound text;
  - open lots keep confirmed protection and normal management;
  - nothing is force-closed.

## Frozen start and scoring start (OQ2)

- **Start time:** Integration writes `start_utc` once, only after `preflight.pass` is true, in the same settings change
  that sets `on: true`. It is never moved.
- **Before the start:** the gate refuses new C-F01 entries.
- **Scored decisions:** a runtime decision is marked `scored` when its decision bar's open time is at or after
  `start_utc`. Warm-up bars (at least 250 older closed bars) are never scored.

## Preflight (read-only)

- **Call:** `Engine.f01_canary_preflight(head_sha, pins_expected)`.
- **Returns:** `{pass, checks, recorded}` with these checks:
  - ENV: PAPER plus the testnet URL;
  - C1: candidate-flat. No C-F01 / C-F02 lot, and no pending, resting, unconfirmed or deferred entry;
  - C1b: other open slots are recorded as symbol / slot / side;
  - C2: C-F02 OFF;
  - C3: guard green;
  - C4: empty `candidate_cost_pending`;
  - C5: no drift;
  - C6: governor and Kelly multipliers = 1.0;
  - C7: ctrl `long_factor` = 1.0;
  - C8: market entry and the 10x cap. AI_FILTER / FEE_EDGE_GATE are recorded and left unchanged;
  - C9: head SHA plus pin re-hash;
  - C10: authorization id plus start.
- **Side effects:** none. It writes, switches and sends nothing. Record its output as a `preflight` extract.

## Clean cohort / reset record

Use the template in `clean_cohort_record_template`. It holds:
- cohort id, head SHA, authorization id and start;
- the preflight sha256 and pass flag;
- the C-F01 slot card, the C-F02 flag and the pending-index size;
- other open slots;
- the AI / fee-gate values, entry order and leverage cap.

It holds no order, trade, stop or client ids.

## Rollback (Integration only)

- **R1:** on the PAPER bot only, through its own settings path, with the bot running, set
  `SLEEVES[id == 'C-F01'].enabled = false`. No installer, no restart, no MODE / LIVE_CONFIRM change. Turning
  `F01_CANARY.on` off is **not** a rollback, because it removes the bounds.
- **R2 / R2b:** open lots are not closed. Force-close only for unknown account state, lost or unrestorable protection,
  environment uncertainty, or a separately recorded safety decision.
- **R3:** after the next 4h cycle confirm three things:
  - no new C-F01 entry;
  - every open C-F01 lot has a confirmed stop;
  - C-F02 is still OFF.
- **R4:** finalize the extracts with the stop condition id. Cowork publishes `verdict.json`.

## Telemetry checks (read-only)

- Log lines `FACTORY-F01 canary:` (logged once per distinct refusal reason).
- Settings: `F01_CANARY` is unchanged and C-F02 is OFF.
- State:
  - S1: `candidate_disabled` has no C-F01 entry;
  - S3: no C-F01 lot with `stop_miss_why` restoring / owner_check;
  - S4: no C-F01 pending-cost entry 6 h old or older.
- `f01_canary.bound_status(...)` reports closed trades, net R, drawdown R and any stop (S5 / S6).
- In the extracts, each file's name equals the sha256 of its bytes.

## Stop conditions

S1-S7 as in the parity contract. The runtime gate enforces S5, S6 and S7 itself. S1, S3 and S4 come from the existing
`candidate_guard`. S2 comes from Cowork's verdict.

## Evidence outputs and hashing

- **Extracts:**
  - Path: `<cohort_dir>/extracts/{decision,trade,preflight}/<sha256>.json`.
  - Format: canonical JSON (sorted keys, ASCII, non-finite floats as `{"float": "nan"}`).
  - Each file is created once (exclusive create). The same bytes again is a no-op; different bytes is an error.
  - Fields are whitelisted. A record carrying any id-like key is refused. The cohort dir must be outside the repo.
- **Finalize:** `ExtractWriter(cohort_dir).finalize()` writes `preflight.json`, `decisions.csv`, `trades.csv` and
  `MANIFEST.sha256` (`<sha256>  <name>` per line). Each file is written once.
- **Left for Cowork:** the `rp_*`, `causal_window_id` and `result` columns stay empty. `verdict.json` is Cowork's (OQ7).

## Open questions

- **Q1:** `frames_manifest.json` and the true raw qty / raw stop (P17 / P19) live inside pinned functions:
  `compute_signals`, `open_lot` and `_create_lot`.
  - The extracts record the quantized values that were sent, plus raw values re-derived from the lot inputs.
  - Integration to decide: accept the derived raw values, or amend the pins to allow an observe-only hook.
- **Q2:** with `F01_CANARY` absent, an enabled C-F01 trades without bounds (this is the existing behaviour). Should
  enabling C-F01 require the flag?
- **Q3:** confirm that "scored" means decision bar open >= `start_utc`.

ROADMAP FIT: Strategy Factory -> rapid candidate-to-PAPER pipeline.
DEFERRED: activation (#248 Cowork PASS + Integration authorization); mainnet; tuning from canary outcomes.
