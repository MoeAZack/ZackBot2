# LLM-LEAK-01 — blinded point-in-time analyst evaluation contract

**Status: DRAFT. Base: `851b0cd8ff990f6ed5f66ddde84e650a7c22201f`. Shadow evaluation only; no alpha, order, PAPER, or mainnet authority.**

## Purpose

Measure whether a shadow LLM analyst's rankings depend on information it should not receive: strategy identity, symbol identity, calendar identity, post-cutoff data, label leakage, or prompt-order artifacts. Passing proves only that the evaluation harness can detect leakage; it does not prove predictive value or authorize trading.

## Blinded request

Every candidate receives a stable one-way alias derived from the frozen run key and candidate identity. The alias mapping is stored in a separate encrypted-or-access-controlled evaluation artifact and is excluded from model requests, model logs, score reports, caches, and result packets.

Requests remove strategy/family names, symbols, human descriptions, absolute timestamps, event names, and calendar labels. They retain only preregistered numeric geometry and coarse relative-time fields available at the cutoff. Candidate and field ordering is deterministically permuted from the frozen seed.

## Point-in-time identity

The request builder binds an availability manifest and cutoff. Appending any row strictly after the cutoff must leave the canonical request bytes and digest unchanged. Changing an admitted pre-cutoff row must change the digest. Missing availability metadata or an ambiguous timestamp fails closed.

## Controls

Each frozen evaluation includes:

1. Original blinded request.
2. Null control with outcome/performance fields removed.
3. Permuted-label and permuted-candidate controls.
4. Equal-information deterministic baseline.
5. Deliberately future-tainted positive control that the detector must flag.
6. Repeated fixed-request/fixed-seed calls for stability measurement.

The same validation, candidate set, token budget, temperature, and scoring rubric apply to every arm.

## Scoring and grounding

Responses must pass the existing strict schema and may reference only provided aliases. The rubric records validity, rank stability, agreement above deterministic/random controls, sensitivity to null/permutation, and whether the future-tainted arm is detected. Alias-to-candidate mapping occurs only after scoring and cannot alter the score.

No profitability or ranking outcome is an acceptance gate. The harness passes only when request isolation, cutoff invariance, control sensitivity, deterministic replay, immutable provenance, and fail-closed behavior are demonstrated.

## Immutable result packet

The packet binds request and availability-manifest hashes, code/tool/model identifiers, seed, rubric version, all control digests, raw-response hashes, validation outcomes, scores, and exclusions. It contains aliases only and refuses a changed result under an existing packet key.

## Authority boundary

The analyst remains default-OFF or PAPER shadow-only and cannot veto, approve, size, route, or place trades. Model responses are not alpha evidence. This slice does not edit `shadow_analyst.py` or runtime code.

## Deferred

- Blinding/evaluation tool implementation.
- Independent synthetic leak challenge.
- Any model comparison or prospective analyst study.
- Any trading authority.
