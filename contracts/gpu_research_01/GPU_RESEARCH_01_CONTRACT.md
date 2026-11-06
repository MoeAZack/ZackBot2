# GPU-RESEARCH-01 — bounded CUDA research contract

**Status: DRAFT. Base: `851b0cd8ff990f6ed5f66ddde84e650a7c22201f`. Contract-only: no runtime dependency, data run, PAPER authority, holdout authority, or mainnet authority.**

## Purpose

Use the local NVIDIA GPU to shorten isolated, default-OFF research jobs without changing ZackBot's causal rules or treating GPU output as interchangeable with a CPU model. The first permitted implementation is an XGBoost histogram-training adapter for ML-TRAIN-01 after its frozen data gates are met. A later synthetic stress-batch adapter may reuse this contract only when its own numerical semantics are frozen.

The local capability probe used Python 3.12 and XGBoost 3.4.1 on an RTX 3080 Ti. A fixed-seed 160,000-row by 48-feature synthetic workload took 0.602 seconds on CUDA and 2.731 seconds on a two-thread CPU reference. The predictions were highly correlated but not identical. Those timings demonstrate capacity only; they are not strategy evidence and set no promotion threshold.

## Non-substitution rule

CPU and GPU-trained models are distinct candidates with distinct artifact hashes. GPU output never replaces the CPU acceptance oracle byte-for-byte. Each candidate must independently pass the frozen ML-TRAIN-01 point-in-time, walk-forward, embargo, calibration, cost-stress, concentration, activity, and prospective gates. Agreement statistics are reported for diagnosis and are never used to waive a failed gate.

The running EDGE04 B0 event/replay process is out of scope. It remains on its accepted exact head until completion; no attempt may migrate, restart, or reinterpret it through CUDA.

## Frozen resource guard

- Python 3.12 isolated environment; no package enters the ZackBot runtime environment.
- GPU device must be explicitly `cuda:0`; silent CPU fallback is a failure.
- Maximum requested GPU memory: 8 GiB, leaving headroom for the desktop.
- Maximum host allocation: 4 GiB and maximum two CPU worker threads.
- Refuse to start when host free memory is below 4 GiB, GPU free memory is below 8 GiB, PAPER health is unknown, or another bounded research manifest owns the GPU.
- An out-of-memory, driver reset, NaN/Inf, missing manifest field, or fallback ends the job fail-closed. It never retries with a larger resource request.

## Frozen comparison packet

Every run binds the source commit, adapter, input pack, feature/label/split manifests, model parameters, seed, dependency lock, Python version, XGBoost version, NVIDIA driver, CUDA version, GPU identity, CPU identity, and every output artifact by SHA-256.

For a CPU/GPU pair, both sides consume the same ordered rows, fold indices, features, labels, costs, seed, and parameters. The packet reports training time, peak host/VRAM use, fold metrics, prediction Pearson and Spearman correlation, mean and maximum absolute prediction delta, threshold-decision disagreement, and artifact hashes. Binary model equality is not expected.

Two same-device GPU repeats are required. Their ordered predictions and complete metric packet must be byte-identical after canonical serialization. A nondeterministic repeat blocks that GPU candidate until separately ruled.

## Data gate

No measured ML training may begin before ML-TRAIN-01 has at least 1,000 paired labels, 100 labels per admitted family/side cell, four future folds, 50 labels per fold, 30 Cairo days, and two regimes. The capability probe and future synthetic fixtures do not count toward those totals.

## Acceptance

1. CUDA use is explicit and verified in the saved model configuration; there is no silent fallback.
2. Resource caps and start refusals are tested, including simulated host/VRAM pressure.
3. Two GPU repeats are canonically identical.
4. CPU and GPU candidates each independently pass all applicable frozen ML-TRAIN-01 gates.
5. Agreement diagnostics are complete and finite; a disagreement can block or separate candidates but can never be normalized away.
6. Input, environment, code, artifact, and result hashes verify before any result is considered.
7. No runtime, PAPER, holdout, order, sizing, strategy-selection, or mainnet authority is introduced.

## Deferred

- GPU adapter implementation and dependency lock.
- Measured ledger training until the frozen data gate is met.
- Synthetic stress-kernel contract and numerical tolerances.
- Independent execution and review.
- Any runtime packaging, PAPER influence, strategy promotion, or mainnet use.

