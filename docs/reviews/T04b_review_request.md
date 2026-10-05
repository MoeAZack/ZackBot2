# T04b: deterministic CI maintenance — review request

*Started 2026-10-06 01:20 Cairo (Africa/Cairo). Branch `t04b-ci-maintenance`, based on protected `master` at `7e0c245`.*

## Purpose

Remove the two non-failing GitHub warnings accepted with T04 before GitHub changes `ubuntu-latest` on 2026-10-19. This ticket changes CI infrastructure only; it does not change trading, backtesting, the panel, the installer or the installed application.

## Changes

- Pin both jobs to `ubuntu-24.04` instead of the moving `ubuntu-latest` label.
- Move checkout, Python setup and artifact upload to their current Node-24 major release.
- Pin each external action to its exact 40-character commit SHA, with a readable `# v7` comment.
- Add a regression test proving both runner pins and all immutable action references.

## Evidence required

1. Targeted regression test passes locally.
2. `verify fast` and `verify full` pass on the pull request.
3. The former Node 20 and Ubuntu 26 migration annotations are absent.
4. The action SHAs resolve to the official `actions/*` v7 tags.
5. `git diff master` contains no application/trading file changes.

## Explicitly out of scope

- Git-history secret scanning and dependency/security scanning: proposed as the next T04b security sub-ticket after this deterministic runner change is green.
- Indicator warm-up analysis: later verification/research sub-ticket.
- Windows self-hosted runner: after T03b.

## Rollback

Close the pull request. `master` remains unchanged.
