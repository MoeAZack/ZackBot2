# Instrument classification: source contract (`zb-instrument-classes/2`)

Draft r3 by Claude Code (Build), 10 Oct 2026 Cairo. Implements R4-0 item 2 (#53 comment 6094720765) under:
- Codex Q5 ruling, #53 comment 6094780810;
- rulings 1-5, #53 comment 6094970068;
- the **owner scope cut**, #53 comment 6095065913.

Roles: **Build** drafts this contract and the deterministic builder (`tools/research/classify.py`); **Cowork**
independently verifies the in-scope inputs; **Codex** accepts and binds the immutable digest. Manual classification
(`instrument-classes-v1`) cannot authorize R4.

Sequence (ruling 5): bound inputs in `research_evidence/inputs/instrument-classes-v2/` -> Cowork verification
(section 7) -> a new immutable `instrument-classes-v2` -> a new universe artifact. v1 and v4 are never edited in place.

## 1. Scope (owner scope cut)

Every manifest symbol gets rows with a `scope`:

| scope | Members | Classified? |
|---|---|---|
| `crypto-research` | every manifest symbol not routed elsewhere: native Binance crypto perps (exchangeInfo `COIN` / `INDEX`, `PERPETUAL`) and delisted contracts absent from exchangeInfo | yes. Only `crypto` / `crypto-index` rows can join the crypto books; any other derived class becomes `OUT_OF_SCOPE` |
| `gold-pilot` | `XAUUSDT` (direct gold, expected `commodity/gold-spot`) and `PAXGUSDT` (tokenized gold, expected `tokenized-gold/paxg`) | yes. The class is still derived from sources and must equal the expected identity, otherwise UNKNOWN. Separate books and cost rows; never in the crypto books; cannot block crypto |
| `deferred` | every exchangeInfo `TRADIFI_PERPETUAL` symbol except XAUUSDT (equity, ETF, index, FX, oil/energy, other metals, pre-IPO), and tokenized gold outside the pilot (XAUTUSDT) | **no**. One audited `OUT_OF_SCOPE` / `DEFERRED` row per symbol, never active and never researched |

- Routing reads only `contractType`, plus the RWA/gold-base check (XAUT) needed to keep tokenized gold out of the
  crypto book. It is not a class claim. A symbol whose snapshots disagree on routing stays in `crypto-research`, where
  the contract-type check makes it UNKNOWN.
- XAUTUSDT is not needed for the pilot (PAXG is the tokenized leg). It is flagged and deferred, not dropped.
- Not addressable, and joins no book: `UNKNOWN`, `OUT_OF_SCOPE`, or no row in force at `t`
  (`classify.resolve_at` / `classify.addressable`). Book membership also requires the row's `scope`.

## 2. Classes (in scope only)

| class | subclass | Meaning |
|---|---|---|
| `crypto` | `coin`, `pre-tradfi-cutoff`, `unspecified` | crypto token perp |
| `crypto-index` | `index` | Binance crypto index perp (BTCDOM, ALL, ...) |
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
2. **Binance announcements.** Structured `zb-announcement-records/1` records: `record_id, kind (listing | class-change |
   tradfi-launch), symbol, url, published_ms, retrieved_ms, page_sha256, effective_ms, class, subclass`. No full pages
   are committed.
3. **Archive manifest.** First/last archived 1d bar and relisting gaps (>= 30 days). It corroborates listing dates and
   is never a class source on its own.
4. **Pre-TradFi rule.** It applies only with a verified `tradfi-launch` record. A crypto-research contract with **no**
   listing source, whose contiguous archived run starts before the first TradFi-underlying perp launch (XAUUSDT,
   Dec 2025), is `crypto/pre-tradfi-cutoff` for that run. It is **still needed**: 21 delisted contracts that reached
   the v4 crypto top 40 have no other source (section 7).

## 4. Mapping from exchangeInfo (in-scope symbols)

| Condition | Result |
|---|---|
| `pair != symbol` or `baseAsset + quoteAsset != symbol`, or quoteAsset != USDT | UNKNOWN |
| `COIN`, `PERPETUAL`, no `RWA` tag | `crypto/coin` |
| `COIN`, `PERPETUAL`, `RWA`, base PAXG / XAUT | `tokenized-gold/<base>` (XAUT is then deferred) |
| `COIN`, `PERPETUAL`, `RWA`, other base (MANTRA, CFG) | abstain: UNKNOWN unless a listing record asserts the class |
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
    `observed:exchangeinfo-snapshot`, `rule:pre-tradfi-cutoff`, `UNKNOWN:*`, `OUT_OF_SCOPE:*`),
    `class_first_observed_ms`, and `provenance` (`contemporaneous` | `retrospective`).
  - Listing times more than 1 day apart are a contradiction, so the symbol becomes UNKNOWN.
- **These become UNKNOWN** until a contemporaneous announcement resolves them:
  - rebrands, relistings or migrations (archived bars before the documented listing day; the pre-TradFi rule is not
    applied there);
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
  `8f12708b...`.
- `announcements.json`: empty. `www.binance.com` was unreachable from Build (connection reset).

The builder runs offline on these files plus the archive manifest. Its output has:
- `rows` (with `scope`);
- `sources`, `inputs` and `mapping` (including `gold_pilot`);
- `row_counts` keyed `scope:class`;
- a `digest` over the canonical JSON.

The same inputs give byte-identical output (`classify.py verify`), and outputs are write-once.

## 7. Cowork's narrowed checklist (top-40 crypto + the two gold identities)

1. **exchangeInfo.** Re-fetch it and diff the bound fields. Do this for every crypto-research symbol, and for XAUUSDT
   and PAXGUSDT. Deferred symbols need only `contractType`. Confirm whether the body's `serverTime` (2026-10-09 07:16
   UTC, about 24 h before retrieval) indicates a cached response that matters.
2. **Gold identities.** Supply dated, hashed listing records for **XAUUSDT** (direct gold; `onboardDate`
   2025-12-11 08:05 UTC) and **PAXGUSDT** (tokenized gold; `onboardDate` 2025-03-27 10:30 UTC).
3. **XAU first-TradFi `tradfi-launch` record.** It is needed because the pre-TradFi rule is the only source for 21
   delisted contracts that reached the v4 crypto top 40: AERGO, ANC, ANT, AUDIO, BTT, BZRX, COCOS, DODO, EOS, FRONT,
   GAL, HNT, KEEP, LEND, LUNA, MATIC, RNDR, SRM, SXP, TOMO, YFII (all `...USDT`). Confirm that none of them is a
   non-crypto underlying.
4. **Top-40 relisted/migrated contracts** whose archive starts before `onboardDate`: AIA, BNX, CVC, ICP, LIT, PUMP,
   TLM (all `...USDT`). Supply the contemporaneous relisting, migration or rebrand records for the earlier segment;
   otherwise it stays UNKNOWN.
5. **DEFIUSDT.** It reached the v4 top 40 and has no `Crypto` tag. Supply a dated listing record asserting
   `crypto-index`, or it stays UNKNOWN.
6. **No other UNKNOWN needs verification now.** MANTRA, CFG, the 4 other relisted contracts (CTK, CVX, MAVIA, SLP)
   and the 10 other undocumented delisted contracts never reached the v4 crypto top 40. Note: v4 was built from the
   v1 classes; once a v2 universe exists, re-check whether any of them enters the top 40.
7. **Rebuild** from the bound inputs and confirm the digest.
