# AUD-05 Codex review

## Accepted implementation

Reviewed exact implementation head `75238d56d3ce11412a9a7a3d5c53e129fcb992eb` on PR #30.

AUD-05 now provides a fail-closed durable boundary for settings, state, exchange-order ownership and account binding:

- risk-adding exchange sends have durable write-ahead ownership and client IDs;
- failed state/settings writes latch a central no-new-risk gate while protection, reconciliation and closing remain available;
- versioned schemas reject malformed ownership, grid and safety-setting records;
- missing or changed account markers require explicit owner confirmation and cannot be bypassed by manual trades or Resume;
- grid topology, quantities, stops and safety metrics are rebuilt or validated before any order;
- legacy Telegram secrets are migrated and redacted from settings, backups, temporary files and corrupt evidence.

## Final r5 evidence

- Codex Windows focused AUD-05 r1-r5 suites: **204 passed**.
- Independent adversarial lanes re-ran both account-recovery shapes, grid stop/metric mutations and every settings evidence form: no new order was sent and no plaintext token survived.
- `git diff --check`: clean.
- CodeQL action and Python analyses: passed.
- Claude's broader focused Windows set: **766 passed**; offline UI **172/173**, with only the already-known local calendar-day check.

## Verdict

**Accepted for the protected full gate and merge.** No remaining AUD-05 code finding. The existing grid take-profit close path without a pre-recorded CID is risk-reducing and remains an explicit follow-up rather than an acceptance blocker.

No installer, bot restart, credentials, mainnet action or real-funds action was used for this review.
