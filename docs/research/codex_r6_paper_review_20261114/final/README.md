# R6-PAPER-01 final independent review

Verdict: **REVIEW PASS with documented environmental rechecks**, exact head `71e93c98030f05ca5b1e9006201d91f9c03486dc`, base `42ef787f`. This is review acceptance only. No slot, deployment, merge or label was performed or authorized by this review-only task.

## Requested items 1–6

1. Canonical exit: **PASS**. The lot's entry 4h bar now anchors the close of entry+120. The 124-wall-clock-bar value is a later protective fallback, not the primary timer. Native tests cover both sides and recorded/fallback entry timestamps; the independent fresh-start witness now resolves the due exit even when install fails on an unrelated missing rule.
2. Panel/canary shape: **PASS**. The frozen 163-symbol list is restricted to the reserved EX14/R6 pairing, max1, share1/12, risk0.024 and original management. Decision and entry checks enforce the $8 budget and notional ceiling, including upward multipliers. Existing sleeve controls remain available.
3. Pacing/data: **PASS for the standalone public-data measurement**. Pacing is established before a slot can become visible and rejects a shared order/data client. All163 frozen symbols were available, with valid required history. Public-only batch: 164 requests including exchangeInfo, 93.969s including one7.653s wait; raw signal CPU1.170s. This does not measure worst shared-IP competition or the running engine lock. A later paused canary cycle/protection observation remains an activation gate.
4. Missing key/failure paths: **PASS**. Fresh failed install and failed reinstall now preserve exits-only plus read-only deadline context; no fetch/admission path is retained. Other slots can continue. Never remove registration while a saved slot/lot still refers to the key.
5. EX13 overlap: **ACCEPTED for the bounded PAPER canary**, under max1/$8 and existing account limits. No skip-held rule or #339 activation. Quantity-sized protection and lot reconciliation still require fresh activation evidence; overlap/combined risk remain recorded.
6. Entry window: **ACCEPTED as a 20-minute entry-attempt cutoff**, after signal and D01 checks, with attempt, write-ahead/send and fill observations recorded separately. It is a disclosed operational deviation, not a hard network-arrival guarantee or research next-open fill parity.

## Windows evidence, including the non-green initial run

- All five tested files match their exact Git blobs after line-ending normalization; the tracked checkout remains clean at the reviewed head.
- Final-head native targeted R6 tests: **101 passed in34.90s**.
- Unmodified real-source/settings-copy fake receipt: **PASS**, exit0. Allocated risk$8, quantity1.333/notional$133.30 at price100/ATR2; quantity33.333/notional$3,333.30 at ATR0.01; no book. Real settings/install/source hashes unchanged; no real credential read/use.
- Full native Windows suite, six workers: **11,247 passed,3 failed,41 skipped,32 xfailed in24m13.93s**. The raw run exited1; it must not be described as an initially green full run.
- Two failures were clean-tree guards detecting review artifacts I had left untracked in this private checkout. I moved only those review-created artifacts outside Git, verified a clean tree, and changed no PR source.
- The third failure was the unchanged golden subprocess-deadline test: Windows tree cleanup took1.54s against its1.25s ceiling under the parallel suite. Its timeout/returncode/no-completion assertions had passed; the later grandchild-survival assertion was reached and passed on retry.
- Focused clean-checkout retry: **60 passed in65.34s**, covering the complete58-test deadline module and both failed registration/preregistration guards. All three failed IDs passed.
- Paired baseline deadline module at `615e14b394400a2bbe188d3b4d7dee5991e7e1d6` (the already verified WINPATH tree corresponding to master42ef787f): **58 passed in67.35s**. The failed test/helper files are byte-unchanged by PR350.
- No unresolved code finding remains. This acceptance uses the full run plus explicitly documented focused environmental rechecks; it does not invent a second all-green full run.
- GitHub verify-fast and CodeQL checks were successful on the same exact head. No executable buildcheck is claimed by this packet.

Raw full/clean-retry/baseline JUnit and logs remain private under `F:/ZackBot2_data/R6-PAPER-01-FINAL-GATES-20261114/`; sanitized counts, IDs and hashes accompany this report.

## Boundaries and next state

No account, key, position, strategy setting, runtime or scheduler changed. Account1 was not accessed. Account4 retains its existing custom engine image; the source was not replaced. First later canary remains max1 with$8 planned entry risk and$4,000 capital basis, subject to its existing protection/paused-cycle gates and any later in-scope instruction. Nothing is installed from this review.

S16 and R17 independent economics and R6/R17 overlap are already published at [72ca9f06](https://github.com/MoeAZack/ZackBot2/blob/72ca9f068c5675488965db5835743171cada8358/docs/research/codex_s16_r17_review_20261114/README.md). S16 stays record-only; R17 is related but not identical to R6 and remains a research lead behind it. No S16/R17 PAPER.

The existing control-loop automation was read and remains owner-paused; no schedule was restored. Research routing honors the reported bounded V30 batch and assigns the signed-funding PhaseA audit next, with no new alpha/outcomes before its freeze.
