# Read-only runtime source inventory ? accounts 1?4

This is an evidence archive, not an installer or deployable reconstruction.
The owner extended account-4/R6 read-only authorization on 16 Nov 2026. No runtime
file was edited, copied over, configured, stopped, restarted or traded. No
scheduler/watchdog/task, lead-forward clone or other lane was operated on.
The original PR snapshot is preserved in Git history; the manifest records this
capture time in Africa/Cairo and the pinned comparison master.

## Coverage

Account 1 loose source is under LOCALAPPDATA/ZackBot/src. Accounts 2, 3 and 4 use
their F-drive source directories beside the observed launchers. The launchers
prepend those source directories and redirect LOCALAPPDATA to sibling runtime
roots. Account 4 includes R6 strategy/overlay source and the launcher-adjacent
runtime gate, panel client and read-only monitor. These are archived as source;
none was executed. Source scope: top-level Python/HTML/JS/CSS/BAT/PS1 plus
recursive newcore/ml_shadow. Bytecode, dependencies, logs, data, state and backup
trees are outside scope. A top-level obsolete HTML backup remains labeled by its
filename; its presence does not imply that it is served.

Account 1 now has stronger executable evidence: 45/45 comparable embedded Python
code entries match the loose source after compiler filename normalization. See
PROVENANCE.md for the independent method, module coverage and limitations. The
historical build revision also matches all 96 included tracked source files;
generated build_info is untracked there. Loaded memory was not inspected.

## Identity and redactions

For every included file, the manifest records original on-disk SHA-256/size and
snapshot SHA-256/size. Runtime bytes were reread and compared during capture.
Files marked byte_identical retain original bytes including line endings through
Git attributes. Redacted files deliberately differ: sensitive-looking literals
are replaced with REDACTED_VALUE and documented by file/line/kind in REDACTIONS.md
and redactions.json. No original values appear in those reports. Conservative
replacements can include public hashes, test fixtures and harmless placeholders;
this is not a claim that all replacements were real credentials.

All 14 previously withheld files were reconsidered under this rule. Doubtful,
malformed or unresolved files are omitted entirely and listed in the manifest.
They must not be deployed from this archive. Redactions and omissions mean that
this archive cannot claim byte identity for every source file or reproduce all
runtime behavior. It does not prove atomic multi-file or already-loaded process
identity. No stored digest is a hash of a standalone credential/config value.

Secret-bearing config.env, account state and trade logs were not opened.
Selected settings/cohort/install JSON is represented as types only: every scalar
value and list item is omitted, as are sensitive/identifier-like field names.
Those schemas cannot reproduce settings or establish config identity.

DRIFT.md lists source changes and master-only files within the declared scope.
Counts compare original on-disk source before redaction, with conservative risk
categories. It is an inventory, not semantic acceptance or a profitability claim.

## Validation and publication gate

Run python runtime_snapshots/verify.py for offline snapshot hashes, redaction
bookkeeping, file coverage and Python syntax. Add --index to compare staged Git
bytes too. No runtime imports or bot/full-suite execution is necessary for this
inert archive. The capture/compare helpers are workstation-bound read-only audit
utilities; rerun only within the explicitly authorized inventory scope.

The owner requires a strict secret scan of the complete PR diff and the local
redaction list in C:/Dev/codex-inbox/lane1-redactions.md before any new push.
These changes remain local pending that check. The PR stays draft; this lane may
not merge or mark ready. Secret-scan evidence names locations/kinds only.

ROADMAP FIT: owner-approved PAPER/testnet runtime source traceability.
DEFERRED: deployment, semantic drift acceptance,
complete binary/dependency/process-memory provenance and all live/mainnet work.
