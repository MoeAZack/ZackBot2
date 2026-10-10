# Instrument classification: source contract (`zb-instrument-classes/2`)

Draft r2 by Claude Code (Build), 10 Oct 2026 Cairo. Implements R4-0 item 2 (#53 comment 6094720765) under the Codex
ruling #53 comment 6094780810 Q5 and the five rulings in #53 comment 6094970068. **Build** drafts this contract and the
deterministic builder (`tools/research/classify.py`); **Cowork** independently verifies sources, point-in-time dating
and mappings; **Codex** accepts and binds the resulting immutable digest. Manual classification
(`instrument-classes-v1`) cannot authorize R4. Non-ASCII symbols are excluded upstream (the universe's
`not-addressable:non-ascii-symbol` veto); they are classified here like any other symbol so that the exclusion stays
auditable.

**Sequence (ruling 5):** bound inputs are committed under `research_evidence/inputs/instrument-classes-v2/` ->
Cowork verifies the inputs and the MANTRA / CFG / DEFI / XAU-first-TradFi cases -> only then is a new immutable
`instrument-classes-v2` built, followed by a new universe artifact. v1 and the v4 universe are never edited in place.

## 1. Classes

| v2 class | subclass | Meaning |
|---|---|---|
| `crypto` | `coin`, `pre-tradfi-cutoff`, `unspecified` | crypto token perp |
| `crypto-index` | `index` | Binance crypto index perp (BTCDOM, ALL, ...) |
| `tokenized-gold` | `paxg`, `xaut` | gold-backed token (token-issuer and on-chain risk) |
| `commodity` | `gold-spot`, `silver-spot`, `platinum-spot`, `palladium-spot`, `base-metal`, `energy-oil`, `energy-gas` | TradFi commodity perp |
| `equity` | `<market>/<kind>`: market `equity`, `hk-equity`, `kr-equity`, `cn-equity`; kind `single-stock`, `etf`, `leveraged-etf` or `UNKNOWN` | TradFi equity / ETF perp |
| `pre-ipo` | `premarket` | pre-IPO TradFi perp (no public underlying price) |
| `fx` | `fx-pair` | TradFi FX perp |
| `UNKNOWN` | `UNKNOWN` | missing, ambiguous or contradictory evidence; **not addressable** |

- **Ruling 1:** `tokenized-gold` (PAXG, XAUT and similar) is its own class and book. Native gold (`commodity/gold-spot`,
  XAUUSDT), tokenized gold and crypto are reported separately until evidence justifies a shared strategy. The builder
  never maps one onto another; the book assignment lives in the universe (next ticket).
- **Ruling 4:** the equity *kind* (single-stock / ETF / leveraged ETF) comes only from a dated, hashed, unambiguous
  announcement record. exchangeInfo gives only the market, so without such a record the kind is `UNKNOWN`
  (e.g. `equity/UNKNOWN`). Conflicting kinds are a disagreement, so the row becomes UNKNOWN.

## 2. Sources, in priority order

1. **Binance `GET https://fapi.binance.com/fapi/v1/exchangeInfo`** (public, unauthenticated), normalized into a
   `zb-exchangeinfo-snapshot/1` by `classify.py extract`. It keeps the retrieval URL, the retrieval time
   (`retrieved_ms`, from the client clock, which matches the HTTP `Date` header), the SHA-256 of the raw response body,
   the body's own `serverTime` (for audit only) and a `scope` line naming the manifest digest. It holds **only the
   fields the builder reads**, for **only the manifest's symbols**: `symbol, pair, baseAsset, quoteAsset, contractType,
   underlyingType, underlyingSubType, onboardDate`. The raw body is not committed; its hash is bound. Limitations:
   - fully delisted contracts are absent;
   - it is a retrospective observation (section 4).
2. **Binance announcements** (`https://www.binance.com/...`), transcribed into `zb-announcement-records/1` records:
   `record_id, kind (listing | class-change | tradfi-launch), symbol, url, published_ms, retrieved_ms, page_sha256,
   effective_ms, class, subclass`. Only structured records are committed, never full web pages.
   - **Listing records** corroborate the listing time. They may assert a class, which is required where exchangeInfo
     abstains or the contract is delisted, and an equity kind.
   - **Class-change records** create a new dated row.
   - **The single `tradfi-launch` record** (the first TradFi-underlying USDT perp) enables the pre-TradFi rule.
3. **Archive manifest** (`zb-data-manifest/1`, `zb-binance-vision-zip/1`): the first and last archived 1d bar per
   symbol, and relisting gaps (>= 30 days). It corroborates listing dates and is never a class source on its own.
4. **Pre-TradFi rule** (only with a `tradfi-launch` record): a contract with **no** listing source at all, whose
   contiguous archived run starts before the launch, is `crypto/pre-tradfi-cutoff` for that run. The basis: Binance
   listed no TradFi-underlying USDT perp before the launch. Residual risk: it would class a delisted pre-launch gold
   *token* as crypto (Cowork check, section 7). Codex may disallow this rule; without it these contracts are UNKNOWN.

## 3. Mapping from exchangeInfo (committed tables in `classify.py`, copied into the artifact's `mapping`)

| underlyingType | contractType required | underlyingSubType / baseAsset | v2 class/subclass |
|---|---|---|---|
| (any) | | `pair != symbol` or `baseAsset + quoteAsset != symbol` | `UNKNOWN` (identity-mismatch) |
| `COIN` | `PERPETUAL` | no `RWA` | `crypto/coin` |
| `COIN` | `PERPETUAL` | `RWA`, base in {PAXG, XAUT} | `tokenized-gold/<base>` |
| `COIN` | `PERPETUAL` | `RWA`, other base | abstain: needs an announcement, else UNKNOWN |
| `INDEX` | `PERPETUAL` | contains `Crypto` | `crypto-index/index` |
| `INDEX` | `PERPETUAL` | no `Crypto` tag | abstain: needs an announcement, else UNKNOWN |
| `COMMODITY` | `TRADIFI_PERPETUAL` | base in commodity table (XAU gold-spot, XAG, XPT, XPD, COPPER, CL, BZ, NATGAS) | `commodity/<subclass>` |
| `COMMODITY` | `TRADIFI_PERPETUAL` | other base | abstain: needs an announcement, else UNKNOWN |
| `EQUITY`, `HK_EQUITY`, `KR_EQUITY`, `CN_EQUITY` | `TRADIFI_PERPETUAL` | - | `equity/<market>/<kind from announcement or UNKNOWN>` |
| `PREMARKET` | `TRADIFI_PERPETUAL` | - | `pre-ipo/premarket` |
| `FX` | `TRADIFI_PERPETUAL` | - | `fx/fx-pair` |
| anything else, or the wrong contractType, or quoteAsset != USDT | | | `UNKNOWN` |

## 4. Point-in-time rule (ruling 2)

- **What a row asserts.** A row `[effective_from_ms, effective_to_ms)` asserts the contract's **class only**. Listing,
  delisting and tradability are **never** taken from a class row. They stay strictly point-in-time from the archive and
  timestamped observations (the universe's own rules), so no later snapshot can invent earlier availability. The last
  row of a symbol is open-ended.
- **Identity fact at listing.** A contract's class/underlying applies from its documented listing time when it is an
  immutable identity fact. This holds even when the bound exchangeInfo snapshot was retrieved later.
  - The documented listing time is the earliest of `onboardDate` and the listing records.
  - Documented listing times more than 1 day apart are a contradiction: the whole symbol becomes UNKNOWN.
- **Provenance is explicit on every row:**
  - `basis` is one of:
    - `identity-fact-at-listing:exchangeinfo`
    - `identity-fact-at-listing:exchangeinfo+announcement`
    - `identity-fact-at-listing:announcement`
    - `class-change:announcement`
    - `class-change:exchangeinfo+announcement`
    - `observed:exchangeinfo-snapshot`
    - `rule:pre-tradfi-cutoff`
    - `UNKNOWN:<reason>`
  - `class_first_observed_ms` is the earliest publication or retrieval of the class evidence.
  - `provenance` is `contemporaneous` when that observation is at or before `effective_from_ms`, `retrospective`
    otherwise, and null for UNKNOWN rows.
- **Mutable contracts, rebrands, ambiguity and contradictions become UNKNOWN** until a contemporaneous announcement
  resolves them:
  - identity mismatch;
  - archived bars before the documented listing day (a relisted, migrated or rebranded contract). The pre-TradFi rule
    is **not** applied here;
  - snapshots that disagree with no class-change record. The gap is UNKNOWN, and the new class dates only from the
    first snapshot that shows it;
  - unmapped underlyings.
- **Changes.** A class change takes effect at `max(effective_ms, published_ms)`, so it is never before publication.
  It creates a new row, and the previous row ends there. Rows are never edited; a corrected input means a new artifact
  id.

## 5. UNKNOWN reasons (fail closed, never guessed)

The reasons are:
- `no-documented-listing`
- `no-class-source(<abstain reasons>)`
- `identity-mismatch`
- `announcement-vs-exchangeinfo`
- `announcements-disagree`
- `listing-time-disagreement`
- `undocumented-class-change`
- `class-change-before-listing`
- `archive-precedes-documented-listing`
- `archive-beyond-pre-tradfi-run`
- `contract-type:...`
- `quote-asset:...`
- `underlying-type-unmapped:...`

Downstream, a row of `UNKNOWN`, or no row in force at `t`, is **not addressable**: it joins no book
(`classify.resolve_at`, `classify.addressable`). Malformed inputs are refused outright. These include a wrong URL, a
bad hash, an unknown record kind, a class-change without a class, an equity kind outside the list, extra snapshot
fields, and a `--tradfi-cutoff` that names no `tradfi-launch` record.

## 6. Bound inputs, artifact and determinism (ruling 3)

`research_evidence/inputs/instrument-classes-v2/`:
- `exchangeinfo-fapi-20261010.json`: the normalized extract. It holds 869 of the 900 manifest symbols, at about
  172 KB, with one symbol per line. The raw response body (1.1 MB) is bound by SHA-256 and is not committed.
- `announcements.json`: the structured records. It is **empty in r2**: `www.binance.com` was unreachable from the Build
  machine (connection reset), so no announcement could be retrieved and hashed. Every case that needs one stays UNKNOWN
  until Cowork supplies verified records.

The builder runs offline from only these files plus the archive manifest. The output `zb-instrument-classes/2`
contains:
- `rows`: symbol, class, subclass, effective_from_ms, effective_to_ms, class_first_observed_ms, provenance, sources,
  basis;
- `sources`: each cited id with its URL, retrieval/publication time, hashes, input file SHA-256 and the manifest digest;
- `inputs`, `mapping` and `row_counts`;
- `digest`: the SHA-256 of the canonical JSON without `digest`.

There is no run clock. The same inputs produce a byte-identical file, which `classify.py verify` checks. Outputs are
write-once.

## 7. What Cowork independently verifies before the v2 build

1. **exchangeInfo.**
   - Re-fetch it independently and diff the 8 fields for every manifest symbol against the bound extract.
   - Report any drift.
   - Note: the bound body's `serverTime` (2026-10-09 07:16 UTC) is about 24 h older than its retrieval
     (2026-10-10 06:57 UTC). This is probably a CDN-cached body; confirm whether that matters.
2. **XAU first TradFi.** Supply the `tradfi-launch` record (URL, publication time, launch time, page SHA-256) that
   confirms XAUUSDT was the first TradFi-underlying USDT perp. Confirm that no delisted pre-launch RWA/gold token is in
   the manifest.
3. **MANTRAUSDT and CFGUSDT** (exchangeInfo `COIN` + `RWA`, base not gold). Supply dated listing records that assert
   the class. Also check for a rebrand: MANTRA is likely a renamed OM. If it is a rebrand, its pre-rename history stays
   a separate contract.
4. **DEFIUSDT** (`INDEX` without a `Crypto` tag; v1 had crypto-index). Supply a dated record that asserts
   `crypto-index`.
5. **The 11 relisted / migrated contracts** whose archive starts before `onboardDate`: AIA, BNX, CTK, CVC, CVX, ICP,
   LIT, MAVIA, PUMP, SLP, TLM (all `...USDT`).
   - Find the contemporaneous announcements (relisting, contract migration, rebrand) that resolve the earlier segment.
   - If none resolves it, the segment stays UNKNOWN.
6. **The 31 delisted contracts with no listing source**, covered only by the pre-TradFi rule once a `tradfi-launch`
   record exists: 1000BTTC, AERGO, AKRO, ANC, ANT, AUDIO, BDXN, BLUEBIRD, BTCST, BTS, BTT, BZRX, COCOS, DODO, DOTECO,
   EOS, FOOTBALL, FRONT, GAL, HNT, KEEP, LEND, LUNA, MATIC, MBL, NU, RNDR, SRM, SXP, TOMO, YFII (all `...USDT`).
   - Confirm that none is a non-crypto underlying.
   - BLUEBIRD and FOOTBALL were index perps: they need listing records if `crypto-index` rather than `crypto` matters.
7. **Equity kinds.** Supply dated listing records for any equity perps where single-stock vs ETF matters to a book
   (ruling 4). Otherwise the kind stays `UNKNOWN`.
8. **Mapping and rebuild.** Review the mapping tables (section 3). Rebuild from the bound inputs and confirm the digest.
