# C24/C41 prospective extended-validation result

Plan/fold head: `f247b5183c03cf37171246a6a2adf561c0c42de3` (parent PR277 exact head
`4827caa250642ca5c175417c494343ac7dd7681c`). Plan SHA-256
`43e7000bef0ba1d821ed519ba470d1391207fffd6c50025b22518a3db3d72b54`; evaluator SHA-256
`0cdf32106371d0cb752868b66fb3a0d2a3ff8d2b112c4bd18460dbe4def00f56`.

The three end-exclusive folds ended before the sealed `2026-09-28T00:00:00Z` holdout boundary. Every packet binds the
same clean head. Frozen parameters and the 5 bp fee +2 bp adverse-slippage per fill + actual signed-funding path were
unchanged. No tuning, rescue, holdout or PAPER/runtime action occurred.

## Fold results

| Candidate/fold | Net R | PF | Max DD R | Trades / symbols | BTC regimes above / below |
|---|---:|---:|---:|---:|---:|
| C24 WF1 | +39.841225 | 1.814866 | 10.444870 | 206 / 9 | 149 / 57 |
| C24 WF2 | -8.305446 | 0.802801 | 13.378588 | 200 / 10 | 40 / 160 |
| C24 WF3 | +48.596628 | 2.062638 | 15.375812 | 196 / 10 | 105 / 91 |
| C41 WF1 | +37.771363 | 1.092055 | 45.126537 | 779 / 18 | 607 / 172 |
| C41 WF2 | -2.272295 | 0.967158 | 31.655581 | 679 / 20 | 203 / 476 |
| C41 WF3 | -17.878433 | 0.916926 | 108.993580 | 1,022 / 20 | 678 / 344 |

## Aggregate verdicts

**C24 `rotation_long_h2`: BLOCK for isolated PAPER.** 602 trades / 10 symbols; +80.132407R, mean +0.133110R,
PF 1.587068, chronological max DD 15.375812R. Gross $480.274583, fees $54.797897, funding $13.398487, net
$412.078200. Regime means: +0.171453R above / +0.096511R below. Maximum symbol absolute contribution 33.2617%.
Mean-R retention versus development 75.0848%; PF improved 0.123554; DD ratio 0.826659. The risk cap remains 0.806454x.
It passed every frozen economic, breadth, regime, concentration, degradation and risk gate, but only 10/18 months were
positive: one-sided sign p=0.407265 versus family-wise 0.00113636. The full-ledger multiplicity gate therefore blocks.
C24's disclosed old folds remain contaminated/void; these are prospective exact-head reproductions, not newly unseen data.

**C41 `volatility_acceleration_long_1h`: BLOCK for isolated PAPER.** 2,480 trades / 20 symbols; +17.620635R,
mean +0.007105R, PF 0.998380, chronological max DD 118.163460R. Gross $463.264720, fees $480.482116, funding
-$9.788133, net -$7.429262. Regime means: +0.005892R above / +0.008925R below. Maximum symbol absolute contribution
20.0975%. Mean-R retention was 20.9881%; PF degraded -0.054392; DD grew to 2.705078x development. Only WF1 was
positive with PF>1. C41 fails aggregate PF, two-of-three folds, retention, extreme-DD and multiplicity gates. The
prospective risk cap falls to 0.126943x, but no PAPER allocation is eligible.

## Artifact hashes

| Packet | Canonical SHA-256 | File SHA-256 |
|---|---|---|
| C24 WF1 | `35a846abcf021b860e4661998b084b6d02ba862522af22e511e01f6445c518f9` | `11f66088e5ee4ed93fa344c693decfe249bc7734e9835de87878bd16bafbe9f7` |
| C24 WF2 | `b8a8a62e06dfbda86b95608145ec5288b1a11f74dc45bce63f62485ddb07908b` | `2906a924493d810509b4cb255a874240c63665a15d2c68b4d860c1dab5ff2364` |
| C24 WF3 | `fcd6ffceac32309455f7e1cc6c48334f88a115aa05ed4ec01a424adb3cfc05bc` | `493a406a83bb407cd5023f62336988a656d188fcbb2771581e9ccd19d7c46235` |
| C41 WF1 | `9daaed00e97f1aadcd63c123116e609d2835ddedb9e7e93ffcfb20a1b9e51c8f` | `7a41c36bb5ef6a95f66f6a67b02465fc370f5a645144bbf26b60a78d811a1324` |
| C41 WF2 | `55d4d877edff971c58a2f1045edc3934409d0999a72dfbf5214af8d292c1df55` | `b1f18d8f7e0d1d1b721ee14026aa8b4b2d4d7fc10f6ab8c122370c512504dd9a` |
| C41 WF3 | `474ec4cb7079fd76dc709717cbc2ad31c71f94017f8e944e27441038b7f9e238` | `0b55d699f9892f8d46dfb41d634d925de3119d476c8e58ada4ea341e335f23c2` |

Validation report internal SHA-256: `63aca02ea6b5f34b62d90ab9ddb454c98f0db4eff0173277f0aa0aaa0e318eac`;
file SHA-256: `611522de4b6c9bffb372f765da6baa1b2f1d6a43926dfedce3f742729dbef14b`.
