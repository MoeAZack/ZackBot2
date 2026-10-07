# T05a causal trade audit — Codex review

*2026-10-07 06:29 Africa/Cairo; exact reviewed head `8f5577b339f70496038d02963159a95d20523a73`.*

## Verdict

**Changes requested — Windows gate fails before the substantive review can be accepted.**

### P2 — three new tests use the Windows locale instead of UTF-8

The official Windows build environment ran the focused T05a/T05b set and produced **149 passed, 3 failed**. All failures
are deterministic encoding errors in `tests/test_trade_audit.py`:

- lines 299 and 2383 open UTF-8 `app.py` without `encoding='utf-8'`, so Windows CP1252 cannot decode the file;
- line 2419 creates a temporary JavaScript file without `encoding='utf-8'`, so CP1252 cannot encode the Unicode minus
  character copied from the panel source.

Use explicit UTF-8 for both source reads and the `NamedTemporaryFile`, add/retain a Windows-safe regression, and re-run
`test_trade_audit.py`, `test_t05a_t05b_interaction.py`, and `test_t05b_startup_outage.py` in the official Windows build
environment. No installer or runtime canary should run until this source gate is green.

## Evidence

Command: build-environment Python 3.14, focused three-file set. Result: **3 failed, 149 passed in 74.11 s**. The failures
are test portability defects; no trading action or installed-app change occurred.
