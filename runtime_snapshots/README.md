# Lane 1: bounded, read-only runtime inventory

This is **partial evidence**, not a deployable reconstruction or proof of loaded
process memory. No runtime was stopped, restarted, written, configured or traded.
Nothing here authorizes deployment or activation. The reference master commit and
Africa/Cairo capture time are in `manifest.json`.
The directory attributes preserve original line endings and whitespace in Git.

## Coverage and boundaries

- Account 1: the explicit read-only inventory exception was used. Captured the
  loose source at `%LOCALAPPDATA%/ZackBot/src`; hashed the on-disk packaged
  executable separately. The observed process runs the packaged executable.
  **The loose source is not proven to be the executable's embedded source.**
- Account 2: observed Python launcher `OLD3-account2/start_old3.py`; it prepends
  its sibling `source` and sets LOCALAPPDATA to sibling `runtime`. Captured
  launcher and the source scope below.
- Account 3: observed Python launcher `ACCOUNT3-breakeven/start_account3.py`;
  same source/runtime redirection. Captured launcher and the source scope below.
- Account 4: **excluded** because the active launcher is in the protected R6
  canary area. Directory names/process-path metadata were observed during initial
  discovery; no canary file contents were read and nothing was changed.
- The lead-forward clone, daily task, scheduler, watchdogs, orders and credentials
  were not operated on. No integration watcher was installed or edited: this lane
  explicitly prohibits scheduler/watchdog actions.

The source scope is top-level `.py/.html/.js/.css/.bat/.ps1` plus recursive
`newcore` and `ml_shadow`, excluding bytecode. Data, logs, dependencies, research
trees, binaries and backup source trees are excluded. The obsolete account-2
panel backup is captured because it is a top-level HTML file; this does not imply
it is served. Imported external packages and OS environment are not reconstructed.

## Identity and configuration

Each manifest file has SHA-256 and size. During capture, its runtime bytes were
read again and compared with both the initial read and repository copy. This
proves on-disk identity during those reads, **not** identity with code already
loaded in a long-running Python process. No atomic multi-file snapshot was taken.

Credential-screened source files are omitted entirely, including their hashes.
The screen is conservative and may exclude harmless placeholders. `config.env`
was never opened. Selected JSON configurations are represented as schemas only:
all values, list contents and sensitive/identifier-like keys are omitted; no
source hashes are published for these secret-bearing files. These schemas cannot
reproduce settings or prove configuration identity.

`DRIFT.md` enumerates modified/runtime-only captured files and absent master files
within the declared source scope. It gives line counts and conservative risk
categories. Screened files remain unassessed; a complete semantic drift audit is
an open item. Account 1 build provenance, account 4's scope conflict, screened
source, configurations and external dependencies prevent the requested exact
four-account reconstruction. Final lane status is **STOPPED / partial artifact**.

## Review and validation

Run `python runtime_snapshots/verify.py` from this checkout to verify published
hashes, sizes and Python syntax without reading or executing any runtime. The
capture script is retained for audit and is deliberately workstation-bound;
do not rerun it casually because its reads are authorized only for this handoff.
No bot tests or runtime imports were run, and no strategy/profitability claim is
made. This draft must not be merged or marked ready by this lane.

ROADMAP FIT: owner-approved lane 1 deployed-source traceability for PAPER/testnet.
DEFERRED: deployment, activation, semantic drift acceptance, account-4 R6 access,
account-1 embedded-source recovery and all live/mainnet work.
