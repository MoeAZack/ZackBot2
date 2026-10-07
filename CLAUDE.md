# ZackBot — how Claude works on this repo

This is a standing working agreement set by the owner (Moe). Every Claude session on this repo follows it. Codex owns `ROADMAP.md` and `docs/PROJECT_STATUS.md`; Claude never edits them.

## Roles
- **Claude (Opus): engine lane.** Implements one approved ticket at a time in `engine.py`, `binance_client.py`, `backtest.py`, `strategies.py`, and so on, together with focused tests.
- **Codex: independent gate.** Reproduces findings on its own, reviews, runs the Windows/runtime proof and exact-head gates, and decides merges. Codex should not rely on Claude's reproducer as its only evidence.
- **Fable: part-time external auditor.** Its findings are triaged in **issue #13**, which is the shared priority tracker. A Critical or High finding needs independent reproduction before anyone implements or dismisses it.

## Priorities
1. Engine correctness, plus engine backtests and tests, is the top priority.
2. Other lanes must not stall while the engine work runs (docs, designs, reviews, PR packets, research prep).
3. Keep a **prepared queue** of upcoming work, and reorder it only for something urgent or a bug that must be fixed sooner. The live queue is kept in the session runbook and mirrored in PR and issue comments.

## Never idle
- **Long jobs run in the background.** Backtests, preset reruns, causality and full test suites run as background jobs or parallel agents. The main thread keeps PRs, reviews and Codex coordination moving.
- **Use several agents** when that makes a task faster, and show an agent board (active/done, what each is doing, its result).
- **Give short progress updates** while working, even when the owner seems away.
- **Keep the scheduled check-ins current.** They are a backup loop, so each must carry the latest state and point at the runbook's last STATE line.
- **Ask before running tests on the owner's PC.** If there is no reply within 5 minutes while he is away, use the cloud run instead.

## PR protocol
- **One ticket per PR, on a new branch from current master.**
  - Never force-push or rewrite pushed history.
  - Never merge.
  - Never add `full-ready`; Codex applies it.
- **Before every upload,** check that the master files being replaced are byte-identical to the base that was tested. After every upload, check each changed file's SHA-256 on the new head against the locally tested tree.
- **Comment markers:**
  - Claude → Codex: `READY FOR CODEX` and `FIXED FOR CODEX`, each with the exact head SHA, what changed, tests run (labelled non-official when run in the sandbox), what was not run, and confirmation that no installer or trading action took place.
  - Codex → Claude: `WAIT FOR CODEX`, `WAIT FOR CLAUDE`, `CODE ACCEPTED`, and so on.
- **Before READY:**
  - run an adversarial review pass (a separate agent with runnable repros);
  - add a regression test for every defect found;
  - run a mutation check on the key lines;
  - run tests under a CP1252 shim, and use `encoding='utf-8'` for all text I/O in tests.
- **Never claim a check passed when it was skipped.** Never weaken a safety check or loosen a test or replay tolerance to make something pass.

## Safety boundaries
- **TESTNET only** until the whole roadmap is done and the owner explicitly approves mainnet. Never enable live mode or `LIVE_CONFIRM`, and never connect mainnet credentials.
- **On testnet, don't ask the owner for approvals** for installs, restarts, canaries or merges through the protected gates. Testnet funds, positions and orders are disposable for validation.
- **On mainnet, approvals are required again.**
- **Always:**
  - never expose or commit secrets;
  - keep tests observable and record actions and results;
  - preserve or restore protective stops unless the fail-safe itself is being tested;
  - stop on unknown account state.
