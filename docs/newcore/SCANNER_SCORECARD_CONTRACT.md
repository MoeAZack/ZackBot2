# SCORE-01: point-in-time candidate board and forward evidence

**Status:** contract foundation. It does not add a strategy, change sizing or enable automation.

This contract incorporates the useful engineering lessons from Coil's scanner and published live record while keeping
ZackBot's independent long/short, crypto-first and evidence-first requirements. ZackBot does not copy Coil's fixed
weights, long-only book, leveraged-ETF acceleration or claimed backtest edge.

Machine-readable contracts: `contracts/candidate_score_v1.schema.json` (one candidate) and
`contracts/candidate_evaluation_v1.schema.json` (one scoring cycle / evaluation run). Rules a JSON Schema cannot express
(time order, TTL, hash recomputation, coverage, counts, reason-registry membership, automation ceiling, proof-of-prior
ordering) are enforced by `newcore.contracts.signal_v1` (`check_candidate_score`, `check_candidate_evaluation`).

## 1. Separate facts, never one magic score

Every asset / timeframe / strategy / side candidate publishes separate 0–100 dimensions:

- opportunity;
- entry quality;
- hold quality;
- regime fit;
- evidence readiness;
- operational readiness.

The UI may rank by a versioned composite, but it always shows the components and weights. A high opportunity score can
never hide weak evidence, poor operational readiness or a mismatched regime. Scores never authorize an order or raise
the owner's mechanical risk ceiling.

### Ranking is not a blanket veto

The board exists to find and size enough valid opportunities for the selected operating profile, not to demand that
every score be perfect. Only integrity and account-safety failures are unconditional new-entry blocks: malformed or
expired input, unusable market data, duplicate/conflicting identity, unsupported venue capability, inability to place
required protection, unresolved ownership/reconciliation, or a reached account/session hard-loss ceiling.

Everything else is a versioned profile input. Conservative through explicitly high-risk profiles may use different
opportunity and regime thresholds, concurrency, cooldown, leverage ceilings, DCA permission and trade-frequency targets.
Those mappings are backtested and visible in the preview. They may accept more marginal but still valid candidates; they
never fabricate a signal, exceed the owner's dollar-risk/drawdown ceilings or bypass the unconditional blocks above.

An evidence/operationally immature strategy may run in research, observe, dry-run or bounded testnet according to its
readiness, but cannot silently become automatic mainnet merely because the owner selected a higher risk grade. Manual
mainnet control remains a separate explicit mode and still uses exchange protection and account hard limits.

Every candidate names its `evidence_class` (`backtest`, `paper`, `testnet`, `live_forward`), the `profile_id` /
`profile_version` that computed its state, and an `automation_ceiling`. Evidence immaturity only lowers that ceiling
(backtest or paper evidence caps automatic use at testnet); it is never a universal no-trade gate. There is no fixed
READY score floor in the contract: READY is computed server-side from the selected versioned profile after the hard
integrity gates, so READY with low evidence or operational readiness is a valid record.

Each profile has an expected opportunity-frequency band by strategy/regime. If actual eligible entries stay below that
band, ZackBot reports a starvation diagnostic: candidates seen, rejection counts by reason, closest missed thresholds
and the estimated effect of each user-selectable profile. It never silently loosens thresholds or forces a trade.

## 2. Explicit entry state

Every candidate has one state:

- `READY`: eligible for the risk gateway now;
- `SETUP`: near eligibility but not yet actionable;
- `WAIT`: valid idea without an entry;
- `EXTENDED`: move already stretched; do not chase;
- `BREAKING`: structure is failing for the proposed side;
- `STAND_DOWN`: the strategy/regime cell should hold cash or remain inactive. It may carry a high opportunity
  score when a regime or operational fact vetoes entry, but it must name that veto (a `stand_down.*` or gate reason).

Direction is separate, so the same states apply independently to LONG and SHORT. ZackBot does not turn a long rule
upside down and call it a calibrated short strategy.

## 3. Cycle priority and stale-input behavior

Every cycle processes protection, exits and reconciliation before new-entry ranking. A stale or missing candidate feed
means no new entries; existing positions continue to be protected and managed from durable local rules and exchange
truth. `STAND_DOWN` is a valid outcome, not a system failure.

Every candidate carries `as_of_ms`, `expires_at_ms` (strictly later; 24 h is a ceiling, not a default), strategy/version, the universe
id plus its point-in-time snapshot hash and as-of (not after the candidate), the scoring version plus weights hash, and
a frozen source-manifest hash. No digest may be all-zero. An expired or mismatched record cannot be promoted into a
signal.

## 4. Structural entries and exits

The candidate records a structural stop in an explicit unit when one is known. The later strategy plan may combine:

- quick partial profit or ladder exits;
- a protected runner;
- a rule-based exit when the original thesis/structure stops working;
- a hard protective stop as the backstop;
- the separately proven, strictly bounded micro-DCA or basket policy.

Stops do not guarantee the fill price through gaps. Backtests and UI disclosures preserve gap/slippage risk.

## 5. Honest research and forward record

Research must use point-in-time universes and retain delisted/failed assets where relevant. Fills occur no earlier than
the next executable event and include fees, spread, slippage, funding/borrow, gaps and capacity. Each promoted strategy
is compared with simple benchmarks for the same window and exposure.

Backtest, paper, testnet and live-forward results are separate datasets and labels. A strong backtest cannot overwrite
weak forward evidence. The forward ledger includes every eligible outcome, explicit flat/stand-down periods, exclusions
and corrected-defect policy. Exclusions are counted and reason-coded, never silently removed: each evaluation run
lists its universe snapshot (delisted and halted members retained and excluded as such), every candidate id, every
exclusion with a registered reason code and the exact per-reason counts; every member is either scored or excluded,
and the universe, weights and benchmark-set hashes are recomputed from the record. Each evaluation cell (strategy,
version, symbol, side) has at most one candidate; exclusion rows never overlap and a cell is never both scored and
excluded, so counts cannot be inflated. Using a candidate requires the trusted clock (`check_candidate_score(...,
now_ms=...)`). Benchmarks are a pre-registered
set bound by `benchmark_set_sha256`.

Before a sealed evaluation or public/lead claim, ZackBot commits the canonical digest of the evaluation and its
candidate records, including the evaluated window bounds and the benchmark-set hash (only the anchor facts that
exist after the digest are outside it). That proof-of-prior counts only when it is independently anchored
- an RFC 3161 timestamp, a public append-only log or a signed public git tag that ZackBot cannot edit - after the
candidates were scored and strictly before the first bar of the evaluated forward window (at most 366 days long); every committed candidate must still be
valid when that window opens. A digest anchored later, or
held only in ZackBot's own storage, proves nothing. It is never evidence for a backtest (a backtest's outcome is known
before any commitment). Even when valid it shows only that the call was fixed before its window; it does not prove
brokerage returns. The validator checks the ordering and the digest; verifying the anchor itself is an evidence-review
step.

## 6. Automation and ML boundary

Mechanical rules and frozen validation gates remain the authority. ML starts in shadow after the Phase 3 data exists.
It may recommend, veto or scale requested risk only within 0–1×; it may not edit its own acceptance tests, entries,
exits, stops or account caps. Champion/challenger changes require a new strategy/model version and forward comparison.
