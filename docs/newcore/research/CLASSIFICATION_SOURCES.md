# Instrument classification: source contract (`zb-instrument-classes/2`)

Draft r4 by Claude Code (Build), 10 Oct 2026 Cairo. Implements R4-0 item 2 (#53 comment 6094720765) under:
- Codex Q5 ruling, #53 comment 6094780810;
- rulings 1-5, #53 comment 6094970068;
- the **owner scope cut**, #53 comment 6095065913;
- Codex PR #57 rulings, comment 6095886569: (1) `crypto-index` is not active; (2) no positive class without positive,
  dated, source-backed identity evidence (the pre-TradFi chronology rule is **removed**);
- the **owner scope correction**, PR #57 comment 6095979330 (supersedes broader wording above).

Roles: **Build** drafts this contract and the deterministic builder (`tools/research/classify.py`); **Cowork**
independently verifies the in-scope inputs; **Codex** accepts and binds the immutable digest. Manual classification
(`instrument-classes-v1`) cannot authorize R4.

Sequence (ruling 5): bound inputs in `research_evidence/inputs/instrument-classes-v2/` -> Cowork verification
(section 7) -> a new immutable `instrument-classes-v2` -> a new universe artifact. v1 and v4 are never edited in place.

## 1. Scope (owner scope correction, PR #57 6095979330)

**Detailed rows exist only for** the dated top-40 candidates, i.e. every symbol the committed universe
(`pit-top40-qv30d-v4`, crypto book) ever selected (`classify.top40_candidates`, 415 symbols), plus the two gold-pilot
identities. **Every other manifest symbol** is neither researched nor classified individually: it is covered by one
blanket `exclusion` = `{class OUT_OF_SCOPE, scope deferred, count, symbols_sha256}` over the canonical sorted list of
manifest symbols without a detailed row.

| scope | Members | Active? |
|---|---|---|
| `crypto-research` | the top-40 candidates | only rows of class `crypto` (`classify.active()`). `crypto-index` keeps its explicit class but is **inactive** (ruling 1: a basket product, possible later separate book). A candidate whose derived class is not crypto becomes `OUT_OF_SCOPE` |
| `gold-pilot` | `XAUUSDT` (expected `commodity/gold-spot`) and `PAXGUSDT` (expected `tokenized-gold/paxg`) | **no**. The class is derived from sources and must equal the expected identity, otherwise UNKNOWN. Recorded but inactive (Codex #53 6095682220); activation needs its own future prereg |
| `deferred` | a candidate that exchangeInfo shows as `TRADIFI_PERPETUAL`, or tokenized gold outside the pilot (XAUT): one `OUT_OF_SCOPE` row; **and** the blanket exclusion (everything else) | **no** |

- **Candidate binding (F2, Codex PR #57 6096110707).** `classify.PINNED_CANDIDATES` pins the historical candidate
  set: v4 universe id + digest, count 415, and the SHA-256 of the sorted symbol list. `classify.check(c, manifest,
  universe)` fails closed unless all of these hold:
  - the outer digest matches;
  - the candidate pin equals both the universe's and the pinned set;
  - the detailed symbols are **exactly** the candidates plus XAUUSDT and PAXGUSDT (nothing added or removed);
  - each symbol's rows are ordered, contiguous and non-overlapping (no duplicates), with the right scope (gold stays
    `gold-pilot`);
  - every classified row lists positive `evidence` that it also cites in its `sources`;
  - the exclusion count + digest equal the manifest minus the detailed symbols, so the exclusion is disjoint and
    complete. `build` refuses snapshot symbols outside the manifest, so every remaining snapshot symbol is covered.

  A forged row with recomputed digests fails the set, order, scope or evidence checks.
- **Membership proof.** `classify.active_at(c, manifest, universe, symbol, t)` runs `check`, then requires all of:
  - membership of the pinned candidate set;
  - dated top-40 membership in the universe week containing `t`;
  - a `crypto` / `crypto-research` row in force at `t` with positive evidence.

  Gold is never active. A symbol with no detailed row is never addressable or active.
- Non-addressable: UNKNOWN, OUT_OF_SCOPE, and everything in the exclusion (stocks, indices, commodities, FX, other
  products). Not active: additionally `crypto-index` and the gold pilot.

- Routing of a detailed symbol reads only `contractType`, plus the RWA/gold-base check (XAUT) needed to keep tokenized
  gold out of the crypto book. It is not a class claim. A symbol whose snapshots disagree on routing stays in `crypto-research`, where
  the contract-type check makes it UNKNOWN.
- XAUTUSDT is not needed for the pilot (PAXG is the tokenized leg). It is flagged and deferred, not dropped.
- Not addressable, and joins no book: `UNKNOWN`, `OUT_OF_SCOPE`, or no row in force at `t`
  (`classify.resolve_at` / `classify.addressable`). Book membership also requires the row's `scope`.

## 2. Classes (in scope only)

| class | subclass | Meaning |
|---|---|---|
| `crypto` | `coin`, `unspecified` | crypto token perp (the only active class) |
| `crypto-index` | `index` | Binance crypto index perp (BTCDOM, ALL, ...): explicit class, **inactive** |
| `commodity` | `gold-spot` | XAUUSDT only (gold pilot) |
| `tokenized-gold` | `paxg` (`xaut` recognised for routing only) | PAXGUSDT (gold pilot) |
| `UNKNOWN` | `UNKNOWN` | missing, ambiguous or contradictory evidence |
| `OUT_OF_SCOPE` | `DEFERRED` | deferred by the scope cut |

Ruling 1 holds: crypto, direct gold and tokenized gold are three separate classes and books. The equity, FX,
energy and other-metal mapping, and the equity-kind (ruling 4) work, are **removed** until that scope returns.

## 3. Sources, in priority order

1. **exchangeInfo.** `GET https://fapi.binance.com/fapi/v1/exchangeInfo` (public, unauthenticated), normalized by
   `classify.py extract` into `zb-exchangeinfo-snapshot/1`.
   - Header: retrieval URL, retrieval time, the SHA-256 of the raw body (the raw body is not committed), the body's
     `serverTime` (audit only), and a `scope` line naming the manifest digest.
   - Content: only the 8 fields the builder reads (`symbol, pair, baseAsset, quoteAsset, contractType, underlyingType,
     underlyingSubType, onboardDate`), for manifest symbols only.
   - Deferred symbols keep their entries (about 43 KB of the 172 KB) because their routing, and so their audited
     exclusion, is derived from them.
   - Limitation: fully delisted contracts are absent.
2. **Binance announcements.** Structured `zb-announcement-records/1` records: `record_id, kind (listing | class-change),
   symbol, url, published_ms, retrieved_ms, page_sha256, effective_ms, class, subclass`. No full pages are committed.
   Only for top-40 candidates absent from exchangeInfo (delisted).
3. **Archive manifest.** First/last archived 1d bar and relisting gaps (>= 30 days). It corroborates listing dates and
   is never a class source on its own.
4. **No chronology rule.** Listing before the first TradFi perp is absence-based inference, not identity evidence
   (ruling 2). The rule, its `tradfi-launch` record kind and the `--tradfi-cutoff` option are removed; a candidate
   with no positive, dated source stays UNKNOWN and non-addressable.

## 4. Mapping from exchangeInfo (in-scope symbols)

| Condition | Result |
|---|---|
| `pair != symbol` or `baseAsset + quoteAsset != symbol`, or quoteAsset != USDT | UNKNOWN |
| `COIN`, `PERPETUAL`, explicit `Crypto` tag, no conflicting tag | `crypto/coin` (F1: the only exchangeInfo route to an active class) |
| `COIN`, `PERPETUAL`, no `Crypto` tag (COIN alone, or sector tags only: DeFi, Layer-1, AI, ...) | abstain: UNKNOWN unless a cited listing record asserts `crypto` |
| `COIN`, `PERPETUAL`, only conflicting tag `RWA`, base PAXG / XAUT | `tokenized-gold/<base>` (XAUT is then deferred) |
| `COIN`, `PERPETUAL`, any other conflicting tag (TradFi, Stock, Equity, ETF, Index, RWA, Commodity, FX, Pre-IPO, Metal, Energy) | UNKNOWN (`conflicting-tag:*`); an announcement cannot outvote it |
| `INDEX`, `PERPETUAL`, `Crypto` tag | `crypto-index/index` |
| `INDEX` without a `Crypto` tag (DEFI) | abstain: UNKNOWN unless a listing record asserts the class |
| `COMMODITY`, `TRADIFI_PERPETUAL`, base XAU | `commodity/gold-spot` (pilot) |
| any other combination | UNKNOWN |

## 5. Point-in-time rule (ruling 2)

- **What a row asserts.** A row `[effective_from_ms, effective_to_ms)` asserts **class only**. Listing, delisting and
  tradability stay strictly point-in-time from the archive and timestamped observations in the universe.
- **Identity fact at listing.** The class applies from the documented listing time (the earliest of `onboardDate` and
  listing records) as an immutable identity fact, even when the snapshot was retrieved later.
  - Each row states its evidence: `basis` (`identity-fact-at-listing:*`, `class-change:*`,
    `observed:exchangeinfo-snapshot`, `UNKNOWN:*`, `OUT_OF_SCOPE:*`),
    `class_first_observed_ms`, and `provenance` (`contemporaneous` | `retrospective`).
  - Listing times more than 1 day apart are a contradiction, so the symbol becomes UNKNOWN.
- **These become UNKNOWN** until a contemporaneous announcement resolves them:
  - rebrands, relistings or migrations (archived bars before the documented listing day);
  - identity mismatches;
  - undocumented changes between snapshots. The gap is UNKNOWN, and the new class dates from the first snapshot that
    shows it;
  - unmapped underlyings;
  - contradictions.
- **Changes.** A class change takes effect at `max(effective_ms, published_ms)`. It creates a new row, and rows are
  never edited.

## 6. Bound inputs and determinism (ruling 3)

`research_evidence/inputs/instrument-classes-v2/`:
- `exchangeinfo-fapi-20261010.json`: 869 of the 900 manifest symbols, 172,377 bytes. The raw body is bound by SHA-256
  `8f12708b...`. For non-candidates it is parsed for nothing but the blanket exclusion.
- `announcements.json`: empty. Binance announcement pages rate-limited Build (HTTP 429) before any page could be
  hashed, so no record is committed and the delisted candidates stay UNKNOWN (section 7).
- The candidate set comes offline from the committed `research_evidence/universe/pit-top40-qv30d-v4.json` (bound in
  `inputs.candidates` by universe id + digest).

The builder runs offline on these files plus the archive manifest. Its output has `rows` (detailed set only, with
`scope`), `exclusion` (count + digest), `sources`, `inputs`, `mapping` (`gold_pilot`, `active_classes`,
`active_scopes`), `row_counts` keyed `scope:class`, and a `digest` over the canonical JSON. Same inputs give
byte-identical output (`classify.py verify`); outputs are write-once. `classify.py coverage` reports, in memory only,
which candidates are positive on every week they were selected.

Offline result on these inputs (artifact **not** built): 900 manifest symbols = 417 detailed (415 candidates + 2 gold)
+ 483 excluded. Candidates: **335 positive**, **80 not positive**. F1 moved 52 previously positive candidates to UNKNOWN
(no explicit `Crypto` tag).

## 7. Cowork's checklist (active set + exclusion bypasses only)

1. Verify the snapshot hash and its extraction for the candidates and XAUUSDT / PAXGUSDT.
2. Gold pilot inactive; XAUT deferred.
3. UNKNOWN, crypto-index and excluded symbols are non-addressable and never active.
4. Try to make an unapproved symbol active: a TradFi symbol, a crypto-index symbol, XAUT, a symbol outside the
   candidate set, a tampered exclusion digest. Do not audit or research the wider market.

UNKNOWN candidates (80), listed, not researched further (owner contract section 0.1):
- 53 with no `Crypto` tag in exchangeInfo, of which 52 were positive before F1 (BNX was already UNKNOWN for its
  earlier segment): AGIX, AI16Z, AI, ALPACA, ALPHA, AMB, BAKE, BAL, BLZ, BNX, BOND, DAR, DEGO, DENT, FLM, FTM, FTT,
  FUN, HIFI, HIGH, HOOK, ICX, IP, KLAY, LEVER, LINA, LOOM, LRC, MEMEFI, MKR, NEIROETH, OCEAN, OMG, OMNI, OM, PERP, RAY,
  REEF, REN, STMX, STORJ, STRAX, TON, TROY, UNFI, UXLINK, VANRY, VIC, VIDT, VINE, VOXEL, WAVES, ZKJ (all `...USDT`).
- 21 delisted, no committed source: AERGO, ANC, ANT, AUDIO, BTT, BZRX, COCOS, DODO, EOS, FRONT, GAL, HNT, KEEP, LEND,
  LUNA, MATIC, RNDR, SRM, SXP, TOMO, YFII (all `...USDT`). Binance launch announcements were located for all but BTT
  (whose 2020-09 listing was cancelled), but no page could be fetched and hashed.
- 6 relisted / migrated contracts whose archive precedes the current `onboardDate` (the earlier segment is UNKNOWN):
  AIA, BNX, CVC, ICP, LIT, TLM.
- DEFIUSDT: `INDEX` without a `Crypto` tag. Inactive in any case (crypto-index, ruling 1).
