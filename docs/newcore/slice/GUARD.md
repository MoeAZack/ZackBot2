# Run notes: the tail-loss guard (exit 4)

## When it runs

`python -m newcore.run` becomes the guard when the store refuses the journal:
- DAMAGED or UNREADABLE (this includes a pre-freeze journal, see `JOURNAL_FORMAT_BREAK.md`);
- missing or empty while the venue holds a position on our symbols, or one of our (zbn1) orders.

The guard runs one pass over exchange truth, then exits 4 (`EXIT_STORE_HOLD`). It writes no journal and no reports.

## What it does

- It never opens risk.
- It protects only exposure that the venue's own trades prove is ours (Codex P1-1):
  - the latest own entry is found by its deterministic client id, looking back over the last 60 candles;
  - every trade on that side since the entry fill must be ours: the entry, the lot's child orders (protect, close,
    reduce or add), our strategy closes, or our emergency (zbn1e-) orders;
  - the surviving net is protected, and never more than that. It gets an emergency stop at the bounded fallback level
    (DEGRADED). If no stop can be confirmed, it gets a reduce-only emergency close of that quantity.
- It cancels our own stops that rest on a side the venue shows flat. It never cancels a foreign order.

## Disclosed limits

- **Our lot mixed with a foreign add protects nothing.** If any trade on that side since our entry is not ours (a
  manual add, another bot), the guard cannot attribute the position. It touches nothing on that side, logs a
  `provenance not proven ... ambiguous` incident, keeps the HOLD and exits 4. Our existing resting stop is kept. The
  owner must resolve it by hand. This is deliberate (Codex P1-1, Cowork 6065286201 #4): a guess could stop or close
  someone else's quantity.
- **Same rule at runtime.** A lost journal tail with a non-empty journal is handled by the runtime recovery (Cowork
  NEW A). An entry that is partly exited and whose exit records were also lost is not adopted. It stays a surfaced
  HOLD.
- **The trades read is mandatory.** A venue adapter without `trades(symbol, side, from_ms)`, or one whose read fails,
  makes every guard provenance ambiguous, so nothing is touched.

## Operator checklist after exit 4

1. Read the `INCIDENT` lines. An `ambiguous` incident means a side the guard did not touch. A `DEGRADED` health token
   means emergency protection was placed.
2. Check the venue: our resting stops, our emergency stops (zbn1e-) and any foreign quantity.
3. Flatten or adopt by hand, archive the journal directory and start fresh (see `JOURNAL_FORMAT_BREAK.md`).
