# Instrument classification: source contract (`zb-instrument-classes/2`)

Draft by Claude Code (Build), 10 Oct 2026 Cairo. Implements R4-0 item 2 (#53 comment 6094720765) under the Codex
ruling #53 comment 6094780810 Q5: **Build** drafts this contract and the deterministic builder
(`tools/research/classify.py`); **Cowork** independently verifies sources, point-in-time dating and mappings;
**Codex** accepts and binds the resulting immutable digest. Manual classification (`instrument-classes-v1`) cannot
authorize R4; v2 replaces it with sourced, dated rows. Owner context: XAUUSDT stays in research as its own book; gold
spot and tokenized gold (PAXGUSDT, XAUTUSDT) are distinct classes with distinct cost rows; non-ASCII symbols are
excluded upstream (the universe's `not-addressable:non-ascii-symbol` veto) and are classified here like any other
symbol so that exclusion stays auditable.

## 1. Classes

| v2 class | subclass | Meaning | v1 equivalent (book) |
|---|---|---|---|
| `crypto` | `coin`, `pre-tradfi-cutoff`, `unspecified` | crypto token perp | `crypto` |
| `crypto-index` | `index` | Binance crypto index perp (BTCDOM, ALL, ...) | `crypto` / `crypto-index` |
| `tokenized-gold` | `paxg`, `xaut` | gold-backed token (crypto-native, token-issuer and on-chain risk) | `gold-commodity` / `gold-tokenized` |
| `commodity` | `gold-spot`, `silver-spot`, `platinum-spot`, `palladium-spot`, `base-metal`, `energy-oil`, `energy-gas` | TradFi commodity perp | `gold-commodity` |
| `equity` | `equity`, `hk-equity`, `kr-equity`, `cn-equity` | TradFi equity / ETF perp | `equity` |
| `pre-ipo` | `premarket` | pre-IPO TradFi perp (no public underlying price) | `equity` / `pre-ipo` |
| `fx` | `fx-pair` | TradFi FX perp | `fx` |
| `UNKNOWN` | `UNKNOWN` | missing or contradictory evidence; **not addressable** | `unclassified` |

Splits flagged for Codex (the class -> book mapping is downstream policy, not part of this artifact):
- `tokenized-gold` is its own class, no longer a `gold-commodity` subclass. Which book it ranks in (own book, `crypto`,
  or beside gold) is open.
- `commodity/gold-spot` (XAUUSDT) is distinguishable from the other commodities by subclass; the owner's "XAUUSDT is its
  own book" can be a book rule on `(commodity, gold-spot)` without a new class.
- `pre-ipo` is split from `equity` (no tradable public underlying; cost and funding behave differently).
- Equity single-stock vs ETF vs leveraged ETF (v1 subclasses) is **not** published by `exchangeInfo`; v2 does not
  claim it. If a book needs it, it needs a sourced announcement record per symbol.

## 2. Sources, in priority order

1. **Binance `GET https://fapi.binance.com/fapi/v1/exchangeInfo`** (public, unauthenticated), captured as a
   `zb-exchangeinfo-snapshot/1`: retrieval URL, retrieval time (`retrieved_ms`), SHA-256 of the raw response body, and
   per symbol `symbol, pair, baseAsset, quoteAsset, contractType, underlyingType, underlyingSubType, onboardDate,
   deliveryDate, status`. The raw body is kept outside the repo; its hash is bound. Primary for the class and for the
   listing time (`onboardDate`). Limitation: it lists current and settling contracts only; fully delisted contracts are
   absent, and it is a retrospective observation (see section 4).
2. **Binance announcements** (`https://www.binance.com/en/support/announcement/...`), transcribed into
   `zb-announcement-records/1` records: `record_id, kind (listing | class-change | tradfi-launch), symbol, url,
   published_ms, retrieved_ms, page_sha256, effective_ms, class, subclass`. Listing records corroborate the listing
   time and may assert a class (required where exchangeInfo abstains or the contract is delisted); class-change records
   create a new dated row; the single `tradfi-launch` record (the first TradFi-underlying USDT perp, XAUUSDT, Dec 2025)
   bounds the pre-TradFi crypto rule.
3. **Archive manifest** (`zb-data-manifest/1`, `zb-binance-vision-zip/1`): first/last archived 1d bar per symbol and
   relisting gaps (>= 30 days). Corroborates listing dates; never a class source on its own.
4. **Pre-TradFi rule** (only with the `tradfi-launch` record): a contract with **no** documented listing whose
   contiguous archived run starts before the launch is `crypto/pre-tradfi-cutoff` for that run. Basis: Binance listed no
   TradFi-underlying USDT perp before the launch, so no such contract can be TradFi. Residual risk: a delisted
   pre-launch gold *token* (RWA) would be classed crypto; Cowork checks that none exists (section 6).

## 3. Mapping from exchangeInfo (committed tables in `classify.py`, bound into the artifact's `mapping`)

| underlyingType | contractType required | underlyingSubType / baseAsset | v2 class/subclass |
|---|---|---|---|
| `COIN` | `PERPETUAL` | no `RWA` | `crypto/coin` |
| `COIN` | `PERPETUAL` | `RWA`, base in {PAXG, XAUT} | `tokenized-gold/<base>` |
| `COIN` | `PERPETUAL` | `RWA`, other base | abstain -> needs announcement, else UNKNOWN |
| `INDEX` | `PERPETUAL` | contains `Crypto` | `crypto-index/index` |
| `INDEX` | `PERPETUAL` | no `Crypto` tag | abstain -> needs announcement, else UNKNOWN |
| `COMMODITY` | `TRADIFI_PERPETUAL` | base in commodity table (XAU gold-spot, XAG, XPT, XPD, COPPER, CL, BZ, NATGAS) | `commodity/<subclass>` |
| `COMMODITY` | `TRADIFI_PERPETUAL` | other base | abstain -> needs announcement, else UNKNOWN |
| `EQUITY`, `HK_EQUITY`, `KR_EQUITY`, `CN_EQUITY` | `TRADIFI_PERPETUAL` | - | `equity/<region>` |
| `PREMARKET` | `TRADIFI_PERPETUAL` | - | `pre-ipo/premarket` |
| `FX` | `TRADIFI_PERPETUAL` | - | `fx/fx-pair` |
| anything else, wrong contractType, quoteAsset != USDT | | | `UNKNOWN` |

## 4. Point-in-time rule

- A row is `[effective_from_ms, effective_to_ms)`; the last row of a symbol is open-ended. Rows of one symbol are
  contiguous from its first evidence.
- `effective_from` of the first documented row = the **earliest documented listing time** among `onboardDate` and listing
  records. Documented listing times further apart than 1 day are a contradiction -> the whole symbol is UNKNOWN.
- A fact is never effective before it was published: a class-change row starts at `max(effective_ms, published_ms)`.
  A change creates a new row; the previous row ends there. Rows are never edited or back-dated; a corrected input is a
  new artifact id.
- An undocumented change (two snapshots disagree, no class-change record) dates the new class from the first snapshot
  that shows it; the interval between the last observation of the old class and that snapshot is UNKNOWN.
- Archived bars before the documented listing day (relisted / migrated contracts) are covered only by the pre-TradFi
  rule (if the run started before the launch) and are otherwise UNKNOWN.
- Every row carries `class_first_observed_ms`: the earliest publication/retrieval of its class evidence. For
  exchangeInfo-only rows this is the snapshot time (e.g. 2026-10), i.e. the class of a 2025 listing is a retrospective
  observation. A strict-PIT consumer can require `class_first_observed_ms <= t`; the default (open question for Codex)
  is that a contract's underlying is fixed at listing, so the listing time governs.

## 5. Missing or contradictory evidence -> UNKNOWN (fail closed)

Never guessed. Each UNKNOWN row cites the sources involved and a reason:
`no-documented-listing`, `no-class-source(...abstain reasons...)`, `announcement-vs-exchangeinfo`,
`announcements-disagree`, `listing-time-disagreement`, `undocumented-class-change`, `class-change-before-listing`,
`archive-precedes-documented-listing`, `archive-beyond-pre-tradfi-run`, `contract-type:...`, `quote-asset:...`,
`underlying-type-unmapped:...`. Downstream: `UNKNOWN`, or no row in force at `t`, is **not addressable** (joins no book;
`classify.resolve_at` / `classify.addressable`). Malformed inputs (wrong URL, bad hashes, unknown record kinds, a
class-change without a class, `--tradfi-cutoff` naming no `tradfi-launch` record) are refused outright.

## 6. Artifact and determinism

`zb-instrument-classes/2`: `rows` (symbol, class, subclass, effective_from_ms, effective_to_ms, class_first_observed_ms,
sources, basis), `sources` (each cited id with URL / retrieval time / hashes / manifest digest), `inputs` (announcement
file SHA-256, snapshot ids, record count, cutoff record), `mapping` (the committed tables), `row_counts`, and `digest` =
SHA-256 of the canonical JSON without `digest`. No run clock; inputs sorted; same inputs -> byte-identical file
(`classify.py verify`). Output files are write-once. The builder never fetches; `classify.py extract` turns a saved raw
exchangeInfo body into a snapshot. Market snapshots are not committed until Codex rules where bound inputs live.

## 7. What Cowork independently verifies

1. Re-fetch exchangeInfo independently; diff class fields for every manifest symbol against the bound snapshot (hash
   of the raw body; field-for-field), and report any drift.
2. For every announcement record: open the URL, confirm the publication time, the stated effective time, the symbol and
   the asserted class; recompute `page_sha256` of the archived page or record why it cannot be byte-reproduced.
3. The `tradfi-launch` record: confirm XAUUSDT was the first TradFi-underlying USDT perp and its launch time; confirm no
   delisted pre-launch RWA/gold token exists in the manifest (the pre-TradFi rule's residual risk).
4. Spot-check `onboardDate` against the listing announcement and the archive first bar for a sample per class
   (all TradFi rows, all `tokenized-gold`, all `crypto-index`, 20 random crypto).
5. Review the mapping tables (section 3) and every UNKNOWN row; supply sourced announcement records for UNKNOWNs that
   should be addressable (e.g. MANTRAUSDT, CFGUSDT, DEFIUSDT), never a manual override.
6. Rebuild from the bound inputs and confirm the digest.
