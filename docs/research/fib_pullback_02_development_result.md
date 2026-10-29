# FIB-PULLBACK-02 development result

Decision: **REJECT (both cells) / NON-PROMOTABLE / DO NOT PROMOTE** (Codex verdict 6488154237)  
Evaluated: 29 October 2026, Cairo  
Exact code: `b28456f90ffad98e98badb7a627a5d4e9deac834` (clean tree)  
Preregistration: `research_evidence/prereg/fib_pullback_02.md`, LF SHA-256
`256318453ddede993fccc0a043756877e7f1c9173ec78ff888ec7c053081df48`  
Window: 14 October 2024 00:00 UTC through 28 September 2026 00:00 UTC, end-exclusive  
Sealed holdout: **not read**

The frozen shared candidate runner, called through `tools/research/run_fib02_candidate.py`, evaluated long and short as
separate cells on 1h CORE-20 bars. Execution was:
- next-open entries;
- 5 bp taker fee per fill;
- 2 bp slippage per fill;
- funding of 0.125 bp per 1h bar (equal to 0.5 bp per 4h bar), charged per bar by the runner.

The 1h and 4h BTC kline manifests are committed under `research_evidence/manifests/`. The candle cache stays outside the
repository.

HYPEUSDT and PUMPUSDT were excluded as `INCOMPLETE` (5,702 and 6,683 missing 1h bars), which left 18 symbols.

Exchange floors (E2) applied the current mainnet rules snapshot `data/exchange_rules_mainnet.json` (rules SHA-256
`a34ec71130683101183cd6039fcdd37096457544ccb9c07bbcdaa09d90680aa3`). This is a prospective / current-rule feasibility
check, not point-in-time; no entry was blocked by it. Both packets are labelled `non_promotable`.

| Side | Raw signals | Closed trades | Symbols | Net P&L | Net R | Mean R | Win rate | PF | Max equity DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Long | 145 | 145 | 18 | -$72.7162 | -15.0184R | -0.1036R | 42.07% | 0.7713 | -24.53% |
| Short | 160 | 155 | 18 | -$18.4199 | -2.5750R | -0.0166R | 45.81% | 0.9508 | -17.95% |

Breadth: the preregistered gate needs at least 100 closed trades across at least 8 symbols. Both cells passed it, so both
rejections are conclusive development evidence, not INCONCLUSIVE.

Long is negative before costs: gross -$30.95, fees $38.56, funding $3.21. Short is positive before costs and negative
after them: gross +$30.91, fees $45.51, funding $3.82. The short cell's BTC-above-EMA200 row (56 trades, +0.040R) is
descriptive only. It is not a rescue, and it may only inform a separately preregistered future hypothesis.

Compared with FIB-PULLBACK-01 (`fib_pullback_01_development_result.md`), the confirmation filters raised the trade count
(long 39 to 145, short 67 to 155) and reduced the loss per trade (long -0.150R to -0.104R, short -0.252R to -0.017R).
Neither side became positive.

Packets (kept outside the repository):

- Long: `C:\Dev\fib_pullback_02_long_dev.json`
  - file SHA-256 `654907894091eec0f35a4869f98f0eb3ecf973908f939fa46686176067caf7fa`
  - `packet_sha256` `f98e2a26888d1dede2831463e49b0c5abe5f96247f0a991a4558b6bc4ed82b93`
- Short: `C:\Dev\fib_pullback_02_short_dev.json`
  - file SHA-256 `2591cdc4c3cfeb3b24ef0f385311f210f1ac9545d4a7ea6bd2daaf3a082ae033`
  - `packet_sha256` `af60152ecd0977daf2e5b597835c467371181f8be1648fabff1ee3d7f45f212d`

## Ruling

For this frozen version:
- no tuning on these packets;
- no walk-forward, and the holdout stays closed;
- no PAPER registration and no runtime wiring.

`fib_pullback_02` stays research-only (`RESEARCH_ONLY_KEYS`). Any successor must be a new preregistered hypothesis. It
must not be a parameter search over Fibonacci ratios, filters or a post-hoc regime or symbol subset of these packets.

ROADMAP FIT: strategy discovery, then a sealed development rejection, then durable research memory.
DEFERRED: any FIB-02 tuning, holdout, walk-forward, and PAPER or runtime integration.
