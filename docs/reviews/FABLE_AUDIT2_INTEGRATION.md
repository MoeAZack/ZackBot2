# Fable Audit2 integration review

**Codex review — 08 Oct 2026, Africa/Cairo**  
**Repository base:** protected `master` at `cff3f8811f016ff76f3d844a5fc1f30f3923c3a0`  
**Source:** `ZackBot_Audit2_Fable_2026-10-08.pdf` supplied by the owner; 157,348 bytes; SHA-256
`577ad80543c04b6974316da535117c9c9f2af0c7ad535793ec8d7e38109c60de`

## Verdict

The audit is useful and directionally correct. Its strongest contribution is not a request for a broad cleanup: it
identifies legacy safety gaps that must be converted into explicit NEWCORE contracts, then argues for freezing rather
than continuing to grow or clean up the 4,625-line legacy engine.

Codex independently reproduced the state/schema, pending-resolution, false-not-found and resting-maker failures. Severity
was adjusted where safety controls already limit impact. The roadmap adopts the findings below without interrupting the
already accepted AUD-07 golden gate or the active bounded product corrections.

## Adopted as NEWCORE contracts

1. **REC-01 — P1 recovery/schema contract for NC-02.** Future schemas abort without modifying state or backup. Current-schema
   damage fails closed and preserves evidence; it never silently becomes a managed empty account. Recovery requires
   explicit exchange/state reconciliation.
2. **OPS-UI-01 — P1 account-binding recovery plus P2 incidents UX for NC-04/NC-09.** Surface sanitized binding state and
   existing incidents persistently. Typed confirmation remains fail-closed and does not resume entries.
3. **ORD-REC-01 — P1/P2 order/recovery tests for NC-03/NC-05/NC-07.** No network retry sleeps under state locks;
   transactional pending resolution; position-corroborated not-found; maker drain on pause/halt/flatten; late fills
   protected and flattened.
4. **Mechanical legacy freeze.** No cleanup or product fixes in `engine.py`. Exceptions exist only where the old bot
   prevents trustworthy evidence/NEWCORE validation or escapes the confirmed testnet boundary.
5. **AUD-08 expansion.** Golden characterization must cover long/short, protection, exits/adds, maker, gaps, exchange
   minimums, outages, restart, ambiguity and the newly reproduced failures before NC-01.

## Adopted into the engine/research plan

- Supersede, rather than execute, the old T06–T09 extraction sequence; map its contracts to NC-01/06/07/08.
- Add DATA-01a execution-grade, venue-specific inputs and pinned source/dataset provenance.
- Treat legacy DCA-1h results as historical/unverified and exclude them from the candidate catalog. A replacement DCA or
  single micro-DCA experiment must earn promotion from new causal evidence.
- Add EDGE-00 to stop strategy count from growing when candidates do not beat simple after-cost baselines distinctly.
- Make ENG-GATE-01 numeric and versioned. Set thresholds from ZackBot baselines rather than importing generic targets.
- Require minimum operational mechanisms before the engine-candidate gate; leave prolonged drills in the next wave.
- Validate Binance XAUUSDT as the first exchange-gold venue where available; retain PAXG/XAUT and broker XAUUSD as
  explicit separate fallbacks/studies. Binance's official 05 Jan 2026 launch announcement and later official funding-
  parameter update confirm the product exists; account/region eligibility and runtime exchange rules still require a
  fresh adapter snapshot before any experiment:
  <https://www.binance.com/en/square/post/34799625224482> and
  <https://www.binance.com/en-IN/support/announcement/detail/db04567123a44a89b112b9ae403a9c7d>.

## Modified or not adopted

- **Severity:** account-binding recovery is P1 availability/recovery, not P0, because central entry gates work. The
  incidents display is P2 on its own because safety behavior, logs and Telegram remain available.
- **Broad cleanup or repair now:** rejected. Duplication and fixes land inside the owning NEWCORE component. The old
  failures remain valuable as frozen negative fixtures, not as another implementation backlog.
- **Generic numeric profitability targets:** rejected. DATA-01/RES-01 must establish the measurement baseline and the
  gate contract must version its thresholds before they become acceptance criteria.
- **Shrinking the AUD-07 golden ledger merely because Git stores history:** not adopted. The automated append-only
  contract currently prevents silent rebaselining. It may be simplified only with equivalent machine-enforced integrity.
- **Full tax/profit-share/SaaS operations before NEWCORE:** deferred. Only mechanisms needed for engine safety and
  deterministic exchange accounting move ahead of ENG-GATE-01.
- **Continuing active AUD-07 product fixes/C12 as legacy product work:** rejected after owner clarification. Preserve and
  merge only reusable contract/test evidence that does not grow legacy behavior; supersede the remainder.

## Independent reproductions on `cff3f88`

- Invalid current schema and future `schema_version=2` in both state copies caused both files to be quarantined and the
  runtime to load zero lots while the fake exchange position remained.
- A resolved quantity-only pending add updated memory but left disk with the old quantity and pending record.
- A false `notfound` older than 20 seconds removed pending ownership and allowed an add that increased the fake exchange
  position from 1.5 to 2.0.
- Flatten left a resting maker; a later fill created a new protected lot while the engine remained paused.
- Existing-state save performs one read and two durable writes. Local median was 4.19 ms and p95 5.78 ms; overhead is
  real, but severe local latency was not reproduced, so optimization stays behind correctness.

## Lanes

- **Build — Claude Code:** close AUD-07/C12 as reusable evidence only; then implement recovery/order ownership in NEWCORE.
- **Evidence — Cowork:** pre-stage REC-01 and LEG-LOCK-01 crash/adversarial matrices; continue STRAT-00 evidence work on
  frozen inputs without promoting runtime behavior.
- **Integration — Codex:** merge this roadmap amendment after review; publish the first NEWCORE contract/base and reject
  legacy-only expansion.
