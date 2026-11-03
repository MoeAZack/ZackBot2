# C24/C41 prospective extended-validation plan v1

Status: **FROZEN / NOT EVALUATED** on 3 November 2026 Cairo, before this plan's fold packets were created. Base is PR277
exact head `4827caa250642ca5c175417c494343ac7dd7681c`. This plan changes no strategy, threshold, management or runtime state.

## Bound candidates and evidence

- C24 `rotation_long_h2`: original prereg SHA-256 `dc4ffe6100359cd22bc081745c81435a0bd9566bf6c6cc92a02a2365cc1f3c27`;
  development canonical/file hashes `474ba2eddf2e046bc4b8652fcd17b5182d396c20cb0e3537a721a8acb328ee66` /
  `39cbaa56a058473659f9001d857998f63a9c735f11030a28e0984cc609f3175f`.
- C41 `volatility_acceleration_long_1h`: original prereg SHA-256
  `64ef49330b27cfd4dd363fcc4ab39b8c20b5d015d14443263091460344f1f677`; development canonical/file hashes
  `a530ab55e1ab12607bec6ae85323d699139e0d2490f251ec6b8984de2615f69b` /
  `9ec03d496e58381b515d3a8c451d211549a5a7230e4c39cdecfd211062a0ff6c`.
- Funding manifest SHA-256: `94617184d0b6d409e90b5eb15f2a41f40f0465fb01fa5d659899be25e1a199fb`.

The exact frozen strategy keys and realistic cost path remain unchanged: 5 bp taker fee plus 2 bp adverse slippage per
fill and checksum-verified signed funding. CORE-20, current-universe/survivorship-biased. C24's earlier prematurely
launched folds are disclosed/contaminated and remain void; this plan authorizes fresh exact-head chronological
reproduction, not a claim that those dates are newly unseen. C41 has no prior fold access recorded.

## Only permitted reads

Run each candidate independently on these end-exclusive pre-holdout windows, unchanged:

1. `2025-04-01T00:00:00Z/2025-09-30T00:00:00Z`
2. `2025-10-01T00:00:00Z/2026-03-31T00:00:00Z`
3. `2026-04-01T00:00:00Z/2026-09-28T00:00:00Z`

The sealed interval at and after `2026-09-28T00:00:00Z` is forbidden. No alternate window, fold deletion, side,
threshold, symbol, regime, exit or cost row is permitted.

## Frozen analysis and isolated-PAPER eligibility gate

`tools/research/evaluate_c24_c41_wf.py` is the authoritative evaluator pinned by the same commit as this plan. It verifies
packet hashes, clean/same-head execution and exact windows. Per fold it reports net R, mean R, PF, max DD, trades,
breadth and BTC-regime counts. Chronological aggregate reports the same plus regime mean R, symbol concentration,
monthly sign evidence, and development degradation (`OOS mean R / development mean R`; PF delta; DD ratio).

The full numbered tested-candidate ledger through C44 is the multiplicity family. A candidate needs one-sided exact
monthly sign-test `p <= 0.05/44 = 0.00113636` (zero months count non-positive), not merely nominal significance. It also
needs: positive aggregate net/mean R; aggregate PF >1.05; at least two of three folds with net R >0 and PF >1.0; >=200
closes; >=8 symbols; >=20 closes in each BTC regime with each regime mean R >=-0.05; max symbol absolute contribution
<=35%; OOS mean-R retention >=50% of development; and aggregate DD <=60R. DD otherwise controls a maximum risk cap of
`min(1, 15/development_DD, 15/OOS_DD)` under the prospective policy. Any failure is **BLOCK**. PASS means eligible only
for a later independently approved isolated PAPER canary; this task performs no PAPER/runtime action.

No tuning, rescue, holdout, pooling between candidates, or activation follows any outcome.
