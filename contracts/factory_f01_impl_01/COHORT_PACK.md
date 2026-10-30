# FACTORY-F01-IMPL-01: C-F01 bounded PAPER canary - cohort pack

**Status: NOT ACTIVATED. `authorization: null`.** `cohort_manifest.v1.json` is authoritative; the JSON wins any conflict.

Assignment: Codex ruling 6513808658 (parallel implementation; only activation waits). The governing contract is
FACTORY-F01-PARITY-01 (PR #248, head `d255b3f`, OQ1-OQ8 ruled 6512712537). This pack operationalizes that contract. It
changes no rule in it. Activation needs Cowork's PASS on the #248 exact head and a separate Integration authorization
comment.

## Flag

- Settings key `F01_CANARY`. It is **absent by default, which means OFF**.
- **C-F01 trades only inside the canary** (review 6514109966 #2). With the flag missing, malformed or OFF, an enabled
  C-F01 is refused every new entry. Other slots are unaffected.
- Shape: `{"on": true, "start_utc": "<tz-aware ISO UTC>", "authorization_comment_id": "<positive numeric comment id>",
  "cohort_dir": "<absolute path outside the repo>", "impl_head": "<40-hex commit>", "impl_tree": "<40-hex tree>"}`.
  - Only the JSON boolean `true` turns the flag on.
  - Every other field is required. A malformed value refuses new C-F01 entries and fails preflight C10.
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
- **Raw sizing capture** (review #1). `open_lot` passes the exact pre-quantization qty in the entry plan. For a candidate
  lot, `_create_lot` stores `lot['sizing_raw'] = {qty_raw, stop_raw, stop_dist}`. The capture is observe-only: no trading,
  sizing, stop or exit path reads it. The values sent are unchanged.
- **Pins:** 32 of the 34 pinned functions are unchanged. `open_lot` and `_create_lot` carry the capture.
  - #248 is not merged, so the pin change is proposed in a separate file, `pin_amendment.v1.json`. The #248 files are not
    edited.
  - A test re-hashes all 34 functions against `pins_expected`, which is the contract pins with the amendment applied.

## Bounds (OQ1) and rollback semantics (OQ8)

- **When new C-F01 entries stop:** at the first of these:
  - 30 calendar days from `start_utc`;
  - 8 closed C-F01 trades, counted from the start;
  - 4.0 R measured net drawdown from the canary peak.
- **What counts:** only rows opened **and** closed at or after the start, each with a usable unique string id.
  - The history is read first, then the cohort store, matched by id. A row is never hidden by a `None` id.
  - A C-F01 row without a usable id or close time fails closed.
  - A pre-canary lot is never counted.
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

- **Start time:** Integration writes `start_utc`, the authorization id, `cohort_dir`, `impl_head` and `impl_tree` once, only after `preflight.pass` is true, in the same settings change
  that sets `on: true`. It is never moved.
- **Before the start:** the gate refuses new C-F01 entries.
- **Scored decisions** (review #3): a decision is scored only when both of these hold:
  - its 4h bar opened at or after `start_utc`;
  - the engine confirmed the bar fully closed (bar open + 4h <= the decision time).

  So the bar spanning the start is excluded, and the next fully closed bar is the first scored bar. Warm-up bars (at least
  250 older closed bars) are never scored.

## Preflight (read-only)

- **Call:** `Engine.f01_canary_preflight(head_sha, pins_expected)`.
- **Returns:** `{pass, checks, recorded}` with these checks:
  - ENV: PAPER plus the testnet URL;
  - C1: candidate-flat. No C-F01 / C-F02 lot, and no pending, resting, unconfirmed or deferred entry;
  - C1b: other open slots are recorded as symbol / slot / side;
  - C2: C-F02 OFF;
  - C3: guard green;
  - C4: empty `candidate_cost_pending`;
  - C5: no drift, and the slot equals the frozen card: share == 1/6, risk 0.01, max_pos 4, key `ema_mom`, sides
    long, tf 4h, the ordered fixed-18 symbols, mgmt `{}`;
  - C6: governor and Kelly multipliers = 1.0;
  - C7: ctrl `long_factor` = 1.0;
  - C8: market entry and the 10x cap. AI_FILTER / FEE_EDGE_GATE are recorded and left unchanged;
  - C9: the running checkout's head and tree (read-only git) equal `impl_head` / `impl_tree`, the checkout is clean,
    and every pin matches. An arbitrary or short SHA fails;
  - C10: flag on, tz-aware start, a positive numeric authorization id, an absolute `cohort_dir` outside the repo, and
    40-hex head / tree.
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

## Review 6514109966 (points 1-7) and remaining open item

- Points 1-7 are resolved as described above. The amended pins are in `pin_amendment.v1.json`.
- **Open:** `frames_manifest.json` (the closed 4h frame sha256 per symbol) still needs a hook inside the pinned
  `compute_signals` / `cycle`. It is not built here. Integration to rule whether a further observe-only pin amendment is
  wanted, or whether Cowork hashes the frames from its own fetch.

ROADMAP FIT: Strategy Factory -> rapid candidate-to-PAPER pipeline.
DEFERRED: activation (#248 Cowork PASS + Integration authorization); mainnet; tuning from canary outcomes.
