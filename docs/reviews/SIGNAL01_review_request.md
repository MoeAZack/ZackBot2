# SIGNAL-01 / SCORE-01 contract foundation: review request

**Base:** `origin/master` at `6cd6e4f`

**Scope:** contracts, roadmap and tests only. This branch does not add a network listener, place or alter orders, change
risk, install a build, or touch the active testnet candidate.

## What to review

1. `contracts/signal_intent_v1.schema.json` and `signal_result_v1.schema.json`
   - sender cannot select environment or automation authority;
   - strict versioning, expiry, explicit units and canonical positive decimal strings;
   - durable `signal_id` semantics, optional grouping-only `trade_family_id`, dry run and stable result codes;
   - entries require risk and stop data; reduce/close/modify require a concrete target reference.
2. `contracts/candidate_score_v1.schema.json`
   - separate opportunity, entry, hold, regime, evidence and operational dimensions;
   - explicit long/short direction and ready/setup/wait/extended/breaking/stand-down state;
   - no score or risk multiplier can authorize or increase risk.
3. `docs/newcore/SIGNAL_INGRESS_CONTRACT.md` and `SCANNER_SCORECARD_CONTRACT.md`
   - exits/protection/reconciliation precede new entries;
   - stale external inputs block new risk while existing positions keep local protection;
   - backtest, paper, testnet and live-forward evidence remain separate and benchmarked;
   - future ML is shadow/0-1x only and cannot rewrite gates.
4. Roadmap/execution/owner-contract edits for sequencing and consistency.

## Evidence already run

- `python -m pytest -q tests/test_signal_contract_schema.py` -> **5 passed**
- all three JSON contracts parse successfully;
- `git diff --check` -> pass.

## Requested independent checks

- Claude Code: adversarial contract review for bypasses, ambiguity, idempotency and future adapter implementability.
- Cowork: evidence-language review, especially point-in-time universes, benchmarks, exclusions and proof-of-prior limits.
- Codex remains integration owner; no self-acceptance or merge before independent review.

## Explicitly deferred

- HTTP/HMAC/nonce implementation;
- durable intake service and runtime adapter;
- TradingView UI/setup wizard and streamed result view;
- any strategy weights or strategy promotion;
- any testnet or mainnet behavior change.

LANES: Build — review the frozen contract and prepare adapter notes only; Evidence — attack scorecard/evidence claims;
Integration — own this branch, resolve review findings and decide sequencing after the Binance vertical slice.
