# Delist-notice register artifacts (`zb-delist-notice/1`)

REX-03, under Codex ruling #81 6172019060 §3. A CORE-10 `range_vwap_rev` run passes these with `--notice` / `--notice-sha256`.

## Status: PENDING (no artifact committed yet)

The 2026-10-14 collection did not complete:

1. The first crawl was cut off by a TLS connection reset partway through catalog paging.
2. The re-run got **HTTP 429** from the official API on its first request.

The collector stopped and wrote nothing, as required. It did not bypass the block. There is no artifact, so a CORE-10
`range_vwap_rev` run has nothing to pin and must refuse at its notice preflight.

To finish: re-run the collector once the rate limit clears, commit the dated pair, then set `STEM` and the three digests in
`tests/test_delist_notice_core10.py`.

## Files (once collected)

- `zb-delist-notice-core-10-<YYYYMMDD>.json` is the artifact. It is validated by
  `tools/research/range_vwap_rev_artifact.validate`, and the run command pins its `digest`.
- `zb-delist-notice-core-10-<YYYYMMDD>.coverage.json` is the coverage proof. It records:
  - every announcement-list page that was crawled, with its URL, fetch time and SHA-256;
  - every catalog's total, and whether that catalog was crawled to the end or past the window start;
  - the body scan of every delist-titled announcement released on or after 2021-01-04, with its URL, fetch time,
    SHA-256 and the CORE-10 pairs and base assets it names;
  - one verdict per symbol (`notice`, `proven_none` or `unproven`).

  The coverage file is bound to the artifact by `artifact_digest`. Its `page_manifest_digest` is the artifact
  fetch's `raw_digest`.

## Provenance

- **Source.** Only Binance's public CMS announcement API is used (`www.binance.com/bapi/composite/v1/public/cms/article/...`):
  the catalog list, paged article lists and article details. The public page URL of each item is
  `https://www.binance.com/en/support/announcement/detail/<code>`. The collector sends no credentials and touches no market
  data, holdout or app state.
- **What is stored.** Raw responses are not committed. Only their SHA-256 values are kept, plus minimal excerpts:
  - each item's title, `releaseDate` and code;
  - the hit lists from the body scans.
- **Inclusion rule.** Notices are classified by the frozen inclusion rule, `range_vwap_rev_notice.py`. The artifact's
  `parser_sha` pins it, so any edit to the rule invalidates the artifact. A notice is never inferred from the last candle.
- **Unproven symbols.** A symbol whose coverage cannot be proven is left out of the artifact's `frozen` list. The runtime
  then turns its rows into a whole-window notice GAP, so they halt as `incomplete.notice_gap`.
- **Rebuilding.** Run `python tools/research/delist_notice_collect.py collect --tier CORE-10`. The output order is
  deterministic. A re-run writes a new dated pair, because fetch times and live digests differ. Existing files are never
  overwritten. If the source puts up a bot wall or captcha, the collector stops with exit 2 and does not bypass it.
- **Tests.** `tests/test_delist_notice_core10.py` pins the digests.
