# AUD-01 Codex review

Reviewed implementation head `f7a0997bc728e7e18a0449decd9894667bc79436` against its parent `ef6abc0`.

## Verdict: changes requested

The journal failure is correctly made non-throwing, the backlog is bounded, and the focused cases cover the original
repeated TP/add and reconcile freeze. One P1 hole remains in the second-layer recovery, and the branch must also be
rebased onto current master after BT02.

### P1 — a full close followed by local failure leaves a zero-quantity ghost lot

`_market_close(..., post={'finish': why})` is used by `close_lot()`. If Binance closes the position and `_apply_close()`
then raises, `_after_fill_failed()` applies the post dictionary with `lot.update(post)`. That creates an unused
`lot['finish']` field; it does not perform `_finish(key, why)`, unlike `_resolve_pending()`, which explicitly pops and
executes `finish`.

Independent reproduction on this head:

1. Open one fake BTC lot.
2. Make `_audit_fill()` raise after the exchange close and local quantity mutation.
3. Call `close_lot(..., 'exit_signal')`.

Observed:

- exchange position: `0.0`;
- lot remains in `state['lots']` with quantity `0.0`;
- `lot['finish'] == 'exit_signal'` and `stop_dirty == true`;
- no history record is created.

Because reconcile sees expected and held quantity as zero, it does not remove the ghost. The next management pass can
try to replace a zero-size stop. The intended exactly-once recovery therefore needs explicit terminal semantics.

Required fix:

- make the post-fill recovery handle `finish` exactly as pending resolution does: finish the correct lot once with the
  intended reason, without attempting another exchange close;
- add a regression for a full `close_lot` whose local post-fill hook raises;
- add the corresponding add-path unexpected-failure regression (pyramid or DCA) so both halves of the AUD-01 contract are
  directly covered, not only the partial-close half;
- preserve the original exception/incident evidence without allowing the terminal bookkeeping itself to repeat.

### Integration gate

BT02 merged to protected master as `3fa11e59bab6fc20b77b0a820af5745a35008959` after this branch was created. Rebase
AUD-01 onto that master and rerun the focused and full gates before the next verdict.

No runtime, installer, credential, or order action was performed.

## Round 2 — one durability P1 remains

Reviewed master-based fix head `3534d0d67224439df4b28a6dcced8c2cc56e7a0e`. The zero-quantity in-memory ghost is
fixed and the requested full-close/add regressions pass. Focused AUD-01 + BT02 tests pass **60/60**.

### P1 — terminal recovery is not saved, so restart resurrects the already-closed lot

The exceptional full-close path now calls `_finish()`, but then re-raises. Consequently `close_lot()` never reaches its
normal `save_state()`. `_finish()` itself does not save state.

Independent reproduction:

1. Open and save one fake BTC lot.
2. Make `_audit_fill()` fail after Binance fully closes it.
3. Call `close_lot()` and read `state.json` immediately.

Observed on `3534d0d`:

- exchange position: `0.0`;
- in-memory lot: removed;
- on-disk lot: still present.

A process exit in this window resurrects a position that no longer exists and can replay terminal bookkeeping after
restart. Partial-close/add recovery has the same durability gap: its `post` fields and `stop_dirty` are only in memory
until a later successful management pass.

Required:

- after `_after_fill_failed()` applies terminal or non-terminal recovery, make one immediate best-effort state save before
  propagating the original exception;
- failure of that recovery save must be reported but must not replace/mask the original post-fill exception;
- add restart-style regressions reading the saved state for the full-close case and at least one add/partial case.

**Round-2 verdict: CHANGES REQUESTED.**
