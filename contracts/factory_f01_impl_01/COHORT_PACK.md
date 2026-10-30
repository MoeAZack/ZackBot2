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
  "cohort_dir": "<absolute path outside the repo>", "source_commit": "<40-hex accepted merged commit>",
  "source_manifest": {"engine.py": "<sha256>", ...}}`.
  - `source_manifest` covers `engine.py`, `strategies.py`, `feasibility.py`, `actual_costs.py` and `f01_canary.py`.
  - Each value is the sha256 of the file's bytes with CRLF normalized to LF.
  - Compute it with `f01_canary.source_manifest(<checkout of the accepted commit>)`.
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
  - CAND-EVICT-ACK-01 (#250): `pins_expected` carries `candidate_guard` at `5c70e0d9…30748` (its body now holds
    `self.lock`), per pin ruling Codex 6518282215 and the manifest-only correction Codex 6518560081
    (`contracts/cand_evict_ack_01/pin_amendment.v1.json`). Every other pin is unchanged.

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

## Two-stage start (review 6514438491 #3)

This order removes the preflight / config circularity:

1. **(a) Write the config.** With C-F01 still **disabled**, Integration writes the immutable authorized `F01_CANARY`
   config with a **future** `start_utc`. The config holds `on`, the authorization id, `cohort_dir`, `source_commit` and
   `source_manifest`.
2. **(b) Run preflight.** Integration runs `Engine.f01_canary_preflight(pins_expected, write=True)`.
   - It checks ENV, STAGE (C-F01 disabled) and C1-C10. C10 requires `start_utc` to be strictly in the future.
   - It returns `record_sha256` and writes the record as an immutable `preflight` extract (`extract_sha256`).
3. **(c) Enable after PASS.** Only after PASS, Integration enables C-F01 (slot `enabled = true`) **before** `start_utc`.
   Until `start_utc` the gate refuses every new C-F01 entry.
4. **(d) On failure.** C-F01 stays disabled. Fix the cause and re-run (b), with a new future start if the old one has
   passed.

`start_utc` never moves after (a).

## Scoring start (OQ2)

- **Scored decisions:** a decision is scored only when both of these hold:
  - its 4h bar opened at or after `start_utc`;
  - the engine confirmed the bar fully closed (bar open + 4h <= the decision time).

  So the bar spanning the start never scores, and the next fully closed bar is the first scored bar. Warm-up bars (at
  least 250 older closed bars) are never scored.

## Preflight (read-only)

- **Call:** `Engine.f01_canary_preflight(pins_expected, write=True)`.
- **Returns:** `{pass, checks, recorded, record_sha256, extract_sha256}` with these checks:
  - ENV: PAPER plus the testnet URL;
  - STAGE: C-F01 is disabled (two-stage start);
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
  - C9: installed-build provenance, with no git read (the installer strips `.git`). All of these must hold:
    - `build_info.BUILD_ID` is not `dev`;
    - `build.json` names that build id, and its `source_commit` equals the frozen `source_commit`;
    - the running source manifest equals the frozen `source_manifest`;
    - every pin matches.

    A dev build, a mismatch or a missing identity fails;
  - C10: flag on; a well-formed config (positive numeric authorization id, absolute `cohort_dir` outside the repo,
    40-hex `source_commit`, a complete `source_manifest`); `start_utc` in the future at preflight time.
- **Side effects:** settings and engine state are never changed. `write=True` only adds the immutable preflight extract.

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
  - Path: `<cohort_dir>/extracts/{decision,trade,preflight,frame}/<sha256>.json`.
  - **Frames** (review 6514438491 #1):
    - Captured by an observe-only hook in `Engine.candles`, which is not pinned and builds the exact closed 4h frame
      `compute_signals` decides on. `compute_signals` and `cycle` keep their pins.
    - Capture runs per C-F01 symbol, only while the canary is ON in PAPER / testnet with a `cohort_dir`.
    - Each frame record holds the symbol, the first / last bar open time, the row count, and `frame_sha256` (the sha256
      of the canonical JSON rows `[open ms, o, h, l, c, v]`).
    - Each decision extract carries the `frame_sha256` of its bar.
    - The capture never feeds back into signals and never raises.
  - Format: canonical JSON (sorted keys, ASCII, non-finite floats as `{"float": "nan"}`).
  - Each file is created once (exclusive create). The same bytes again is a no-op; different bytes is an error.
  - Fields are whitelisted. A record carrying any id-like key is refused. The cohort dir must be outside the repo.
- **Finalize:** `ExtractWriter(cohort_dir).finalize()` writes `preflight.json`, `frames_manifest.json` (per symbol, sorted),
  `decisions.csv`, `trades.csv` and
  `MANIFEST.sha256` (`<sha256>  <name>` per line). Each file is written once.
- **Left for Cowork:** the `rp_*`, `causal_window_id` and `result` columns stay empty. `verdict.json` is Cowork's (OQ7).

## Reviews

- **6514109966:** points 1-7 are resolved. The amended pins (`open_lot`, `_create_lot`) are in `pin_amendment.v1.json`.
- **6514438491:** points 1-3 are resolved: frames manifest, installed-build C9, two-stage start. They need no further
  pin change.
- No open questions.

ROADMAP FIT: Strategy Factory -> rapid candidate-to-PAPER pipeline.
DEFERRED: activation (#248 Cowork PASS + Integration authorization); mainnet; tuning from canary outcomes.
