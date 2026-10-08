# REC-02 runner wiring (patch, NOT applied)

Owner of the files touched: the S1 agent (`newcore/runner/*`). This document and
`docs/newcore/reconcile/rec02_wiring.patch` are prepared on `nc-rec02`. The S1 agent applies them after the management
wiring. Nothing here is applied on any branch yet.

- **Target head:** `origin/nc-s1-slice` `b453027` ("NC M4: intra-candle marks for management"). `git apply --check` is
  clean on it. The patch was re-merged twice while `nc-s1-slice` moved (`a30e971` -> `7f35c5c` -> `b453027`). It keeps
  the H2 `raw_qty`, the management fee rates and the mark polls next to the REC-02 changes. If the branch moves again,
  regenerate with a 3-way merge from these files.
- **Apply:** `git apply docs/newcore/reconcile/rec02_wiring.patch`, from the repo root. It needs `newcore/reconcile/`
  from `nc-rec02` (merge `nc-rec02`, or cherry-pick its `newcore/reconcile` + `tests/newcore_reconcile` commits).
- **Default OFF:** `RunnerConfig.rec02 = False` and `[reconcile] rec02 = false`. With the flag off, every path is
  reconcile v0, byte for byte; the evidence is in section 5.
- **Tests:** `tests/newcore_reconcile/test_rec02_wiring.py` (W1-W10) is already on `nc-rec02`. It skips itself until
  `RunnerConfig` has `rec02`, then runs unchanged.

---

## 1. What the patch changes

| File | Change |
|---|---|
| `runner.py` | `RunnerConfig.rec02` / `rec_policy`; `Runner(clock=...)`; `_cycle` calls `_reconcile_step(trigger)` (v0 or REC-02); `rec02()` / `_rec_snapshot` / `_rec_apply` / `_rec_reconciliation`; `tick()` + `needs_tick()`; per-cycle by-id answer cache (`_remember`, used by `_sync` / `_resolve`); entry gate refuses new risk while a PENDING verdict still re-reads |
| `managed.py` | `rec02_reconciler`: the REC-02 slot. A driver HOLD item (`HOLD_ITEMS`) triggers an UNCERTAIN fold pass, then the stub's handling; without the flag it is the stub alone |
| `app.py` | `Session` passes `rec02` and the factory clock to the runner; the testnet loop runs **check-only ticks** between candles (`tick_due` / `tick`), next to the existing management mark polls |
| `config.py` | `[reconcile] rec02 = false`, `[cycle] tick_s = 30` (validated: bool / seconds >= 0) |
| `testnet_hook.py` | `build()` also returns the factory's server-aligned `clock` (5-tuple); REC-02 judges read freshness on it |

`RUNNER_REC_POLICY` (runner.py) keeps the four unruled questions **off** until NC-01 can record them (section 4):
`quarantine_per_side=False`, `adopt_external_change=False`, `protect_surplus=False`, `auto_clear_hold=False`. It also
sets `settle_ms=10_000` (one positionRisk lag window) and `max_attempts=4`.

## 2. How each decision goes through the existing machinery

| Fold decision | Runner action (existing path) |
|---|---|
| `PROTECT_ONLY` on an owned lot | **nothing here.** The protect phase owns lot protection: a pending close first, bounded `_secure` attempts, management flush. Re-securing from the fold would break the bounded-attempt rule (`test_4_stop_and_close_both_refused_is_bounded_never_recursion`). |
| `PROTECT_ONLY` without a lot (surplus / unresolved fill) | owner item: incident + durable HOLD. Q3 is off: no journaled owner kind for that stop (NC-01 A3). |
| `RESOLVE_*` from exchange evidence (`exchange_final`, `late_fill`, `partial_final`, `final_*`, `algo_stop_filled`) | `_apply(iv, <the snapshot's by-id answer>)`: the runner's own `result_from` records the venue record. One source of results, no second query. |
| `RESOLVE_*` from corroboration (`not_found_corroborated`, `position_adopted`) | left to `_corroborate_entry`, which already journals the RECONCILE decision + result. |
| `RESOLVE_FILLED supersedes_not_found_corroborated`, `ADOPT` | owner item (NC-01 A1 / A2 missing). |
| `HOLD` / `QUARANTINE` | `_rec_owner_item`: one deduplicated incident with evidence + owner actions, then `_hold([reason])`. Account-wide until NC-01 A4. |
| `CLEAR_HOLD` | incident only ("clearable; owner resume until NC-01 A5"): `ModeChanged` to ACTIVE needs `operator.resume` today. |
| `REREAD` | another pass in this cycle (at most `REC_PASSES_PER_CYCLE = 2`), else the next cycle / check-only tick. |

**Hard HOLD** (store down) keeps the existing `_hard_hold_cycle`: the A24 emergency set and reconcile v0. REC-02 runs
only when the journal can record its decisions. Its own verdict in a hard HOLD is advisory anyway (Cowork C7).

## 3. PENDING, REREAD and check-only cycles (plan gap 6)

- **Episodes:** `rec02()` runs at most 2 passes per call. A PENDING verdict keeps an **episode attempt counter across
  cycles and ticks** (`_rec_attempt`), so a read lag gets `max_attempts` passes spread over time, not a burst of
  re-reads in one millisecond. Only the last attempt judges: then a lag becomes an owner HOLD.
- **New risk:** blocked while the last verdict is PENDING **and** still asks to re-read (`_entry_gate` ->
  `reconcile.unreconciled` SKIP, journaled like every gate). Actions the cycle can finish itself (an owned lot's stop,
  a resolution just applied) do not block.
- **Ticks:** `Runner.tick(now)` is `cycle(now, decide=False)`: sync, reconcile, protect, never a decision. The testnet
  loop calls it every `tick_s` while `needs_tick()` is true:
  - a PENDING episode;
  - a SUBMITTED / UNKNOWN intent;
  - a HOLD that a fresh match could clear (only once `auto_clear_hold` is on).
  Ticks run only while the current candle is already cycled, so the next candle's `now` is always later (the runner's
  clock never goes back).
- **Lost entries** are now settled between candles: `_corroborate_entry` takes its second read on a tick, not 4 h
  later.
- **One query per intent per cycle:** `_sync` / `_resolve` remember each by-id answer with the intent state and result
  count it was applied to. The first REC-02 pass reuses an answer while both are unchanged. A pass-2 ask (`needs`)
  always re-queries.

## 4. NC-01 amendments the REC-02 path still needs (vs `origin/nc-01-r3-draft` `71cc7ae`)

**Already in r3:**
- `Incident` + `IncidentRecorded`, so the runner's in-memory `incidents` can become journal events;
- the reason codes `reconcile.manual_close`, `reconcile.manual_add`, `reconcile.foreign_quarantine`,
  `reconcile.stale_read`, `reconcile.late_fill_after_not_found`.

**Missing:**

| # | Needed for | Exactly what is missing in r3 |
|---|---|---|
| A1 | Q2 external reduction (R11 adopt; R03 confirmed by the owner) | (1) an `Evidence` value for venue trades of order ids the account never placed (proposal `exchange_external`), executing, REDUCE / CLOSE only; (2) a never-sendable booking intent: `check_result_for_intent` lets a never-submitted intent end only `NOT_SENT`, and `INTENT_TRANSITIONS[DURABLE]` has no FILLED / CANCELLED. Proposal: `OrderType.EXTERNAL` (`may_send` false) with DURABLE -> FILLED / CANCELLED via that evidence only; (3) `OrderResult` cannot carry venue trade ids (corroboration is `PositionRead` only, `exchange_order_id` is one id) -> a bounded `venue_refs: tuple[str, ...]`, required for `exchange_external`; (4) `resolved_by` is allowed only for DECIDED evidence -> allow it (required) for `exchange_external`; (5) `ACTION_PURPOSES[RECONCILE]` is {PROTECT, CLOSE} -> add REDUCE (a partial manual close); (6) a client-id namespace for booking intents that no venue adapter will send (proposal `zbn1x-`, refused by `TestnetVenue._owned`) |
| A2 | Q2 late FINAL after `not_found_corroborated` (R08) | TERMINAL states have no transitions and a second FINAL is not representable. Proposal: one supersede rule. A FINAL `exchange_final` result with `supersedes: res_` + `resolved_by: dec_` (a RECONCILE decision, reason `reconcile.late_fill_after_not_found`) may follow exactly a `not_found_corroborated` FINAL, once; CANCELLED -> FILLED / CANCELLED(executed > 0); `check_event_chain` + the ledger book from the superseding result. `nc-01-domain` `8e7a66d` added `replaces_intent_id` for cancel-replace; Cowork confirms it does not cover a NOT_FOUND predecessor |
| A3 | Q3 protect an unowned surplus (R12 / R06) | `OWNER_KINDS[PROTECT]` allows PORTFOLIO, but a PORTFOLIO owner is orphan cancel-only (`CANCEL_ONLY`). Proposal: `OwnerKind.POSITION` (`pos_` id) for a reduce-only stop on a quarantined surplus: never part of a lot's `confirmed_coverage`, cancelled only by the owner, accepted by `check_account_portfolio` |
| A4 | Q1 per-side quarantine | HOLD is portfolio-wide (`entries_mode` / `hold_kind`). Proposal: a `Quarantine(symbol, side, reasons, since_ms, decision_id)` list on `Portfolio`, a `QuarantineChanged` event, and `permitted(..., quarantined=)` forbidding opening risk on that side only |
| A5 | Q4 auto-clear (R15) | `ModeChanged._check` requires reason `operator.resume` for any change to ACTIVE, and `Decision` makes RESUME OPERATOR-only. Proposal: allow `reconcile.match` with a RECONCILIATION-authority RECONCILE decision, only from HOLD / NORMAL, with every from-reason in an NC-01 auto-clearable set, with `reconciliation_id` (A08) |
| A6 | A08 "a reconciliation record" | `rec_` ids exist but no record. Proposal: `ReconciliationRecorded(reconciliation_id, trigger, outcome, digest, item tokens)`; the REC-02 `snapshot_digest` is that digest. Or rule that `IncidentRecorded` + decision evidence is enough |
| A7 | REC-02 vocabulary not in r3 | `reconcile.fill_mismatch` (R17), `reconcile.unjournaled_order` (R05), `reconcile.duplicate_listed_order` (C4), `reconcile.value_out_of_range` (C10), `reconcile.settling` (R14 b). Today these use `reconcile.unreconciled` / `protect.owner_check` |
| A8 | `Incident.evidence` | It accepts only `ID_PREFIXES` ids, so venue trade / order ids cannot be cited. Proposal: a separate `venue_refs` tuple (opaque, bounded), as A1(3) |

Interface items for the transport lane:
- `trades(symbol, side, from_ms)`: userTrades by time window. FaultVenue has it; TestnetVenue does not. Without it the
  runner reads UNKNOWN, and every deficit stays a HOLD.
- `VenueFill` has no BUY / SELL side, so an opening foreign fill cannot be told from a reducing one. Cowork C8 is closed
  by never adopting against a flat venue; a side field would let partial adoption check direction too.

## 5. Evidence

Copies of `origin/nc-s1-slice` + `nc-rec02` `newcore/reconcile` (`51acd0e`), with this patch applied:

| Head | Run | Result |
|---|---|---|
| `b453027` (target) | patch applied, `rec02` **off**: `tests/newcore_slice` + `tests/newcore_reconcile` | **4339 passed, 1 skipped, 0 failed** (571 s). v0 is unchanged; W1-W10 run, none skip |
| `b453027` (target) | patch applied, `rec02` **forced ON** for every runner: `tests/newcore_slice` | **1159 passed, 1 failed, 1 skipped** (716 s) |
| `7f35c5c` | the same two runs | 4229 passed / 1 skipped; forced ON 1049 passed / 1 failed (the same test) |

The one forced-on difference is expected, and the slice test pins v0 timing:
`test_testnet_semantics.py::test_3_triggered_algo_stop_is_booked_from_the_child_fills[True]`. A triggered algo stop's
first by-id answer has no child id. v0 books the child fill one cycle later. REC-02's second pass re-queries in the same
cycle and books it at once, where the test asserts "nothing booked yet". Before the cache and the PROTECT_ONLY rule
(section 2), forced-on failed 77 slice tests. Every one of those causes was fixed in this patch, not in the tests:
- re-securing outside the bounded protect phase;
- extra queries consuming scripted fault budgets;
- a missing stop level for a lot with no protect intent yet;
- a closed lot's stop awaiting its cancel.

## 6. Tests to run after applying

```
python -m pytest -q tests/newcore_reconcile                 # fold + FaultVenue + wiring W1-W10 (no skips once applied)
python -m pytest -q tests/newcore_slice                     # v0 unchanged (rec02 off)
```

`test_rec02_wiring.py`:

| Test | What it proves |
|---|---|
| W1 | a clean long / short run with rec02 on journals exactly what v0 journals |
| W2 | a vanished stop is restored, no HOLD |
| W3 | a manual close is ONE owner HOLD naming the venue trade, and new risk stays blocked |
| W4 | a positionRisk lag right after the fill settles on a check-only tick, where v0 HOLDs |
| W5 | `needs_tick` follows unknown answers |
| W6 | the first pass after a restart is STARTUP |
| W7 | config |
| W8 | crash at each journal write of the reconcile cycle, then restart: HOLD recorded once, no order sent, ids unique, never naked |
| W9 | repeated reconcile is idempotent (no event, no incident) |
| W10 | a stop fill landing **during** the reconcile (between the by-id answers and the position read) is booked by the second pass, not adopted |

## 7. The exact diff

```diff
diff --git a/newcore/runner/app.py b/newcore/runner/app.py
index b954cf4..243b891 100644
--- a/newcore/runner/app.py
+++ b/newcore/runner/app.py
@@ -164,21 +164,32 @@ class Session:
             else:
                 start = parse_utc_ms(cfg.start) if cfg.start else max(b[0].open_ms for b in candles.values())
                 self.venue = FakeVenue(candles, self.tf_ms, costs=BASE_COSTS, equity=cfg.equity, start_ms=start)
-            reads, port, venue_rules = self.venue, self.venue, None
+            reads, port, venue_rules, clock = self.venue, self.venue, None, None
             self.reads = reads
         else:
             from .testnet_hook import build
-            port, self.bars, reads, venue_rules = build(cfg)
+            port, self.bars, reads, venue_rules, clock = build(cfg)
             self.reads = reads
             self.venue, self.last_close = None, None
         rcfg = RunnerConfig(account=account(cfg), portfolio_id=cfg.portfolio_id, symbols=cfg.symbols,
                             tf_ms=self.tf_ms, timeframe=cfg.tf, rules=venue_rules or rules_for(cfg, cfg.symbols),
                             sizing=SizingPolicy(cfg.risk_pct, cfg.max_leverage, cfg.cap_gap_buffer),
-                            sides=sides_for(cfg), strict=False,
+                            sides=sides_for(cfg), strict=False, rec02=cfg.rec02,
                             raw_qty=cfg.tnet_raw_qty if cfg.tnet_enabled else None)
         self.runner = ManagedBookRunner(rcfg, policy=policy_for(cfg), journal=self.journal, venue=port,
                                         bars=self.bars, signals=signals_for(cfg, enabled), account_reads=reads,
-                                        management=management_for(cfg))
+                                        management=management_for(cfg), clock=clock)
+        self._last_tick = 0
+
+    def tick_due(self, wall_ms):
+        """Testnet: a check-only cycle is due (REC-02 PENDING / an unknown answer / a clearable HOLD), at most every
+        cycle.tick_s; only called while the current candle is already cycled, so the next candle cycle is later."""
+        return (self.venue is None and self.cfg.tick_s > 0 and wall_ms >= self._last_tick + self.cfg.tick_s * 1000
+                and self.runner.needs_tick())
+
+    def tick(self, wall_ms):
+        self._last_tick = wall_ms
+        self.runner.tick(wall_ms)
 
     def next_close(self, wall_ms=None):
         """The candle close of the next cycle, or None when the fake data is exhausted."""
@@ -254,6 +265,11 @@ def cmd_run(cfg, args, out, stop):
                     if cfg.mark_poll_s and time.time() - polled >= cfg.mark_poll_s:
                         s.poll_marks()
                         polled = time.time()
+                    wall = int(time.time() * 1000)
+                    if t == last and s.tick_due(wall):              # REC-02 check-only cycle between candles
+                        s.tick(wall)
+                        s.save()
+                        print(health_line(s.runner), file=out, flush=True)
                     if stop.wait(1.0):
                         break
                     continue
diff --git a/newcore/runner/config.py b/newcore/runner/config.py
index 9449b0e..62d35d9 100644
--- a/newcore/runner/config.py
+++ b/newcore/runner/config.py
@@ -23,6 +23,9 @@
     cadence_s = 0                        # pause between cycles in a loop (seconds); 0 = back to back
     delay_s = 15                         # testnet: seconds after the candle close before the cycle runs
     mark_poll_s = 10                     # testnet + management: mark-price polls between candle closes (0 = off)
+    tick_s = 30                          # testnet: check-only cycle between candles while needs_tick() (0 = off)
+    [reconcile]                          # REC-02 (newcore.reconcile); OFF by default until the gate flips it
+    rec02 = false
     [management]                         # M4 position management (NC-07 driver); OFF by default
     enabled = false
     plan = "range_bb_mr_v1"              # the only plan: a DISABLED mechanics fixture (4h), no edge claimed
@@ -56,12 +59,14 @@ ALLOWED_KEY_NAMES = {'key_digest'}                 # the binding's non-secret di
 TIMEFRAMES = ('1h', '4h')
 RULES = ('trend_ema_mom.v1',)
 SECTIONS = {
-    '': {'mode', 'venue', 'journal', 'strategy', 'book', 'cycle', 'account', 'output', 'management', 'tnet'},
+    '': {'mode', 'venue', 'journal', 'strategy', 'book', 'cycle', 'account', 'output', 'management', 'tnet',
+         'reconcile'},
     'venue': {'kind', 'factory', 'data_root', 'start', 'end'},
     'journal': {'dir'},
     'strategy': {'rule', 'enabled', 'mirrored_short', 'symbols', 'tf'},
     'book': {'risk_pct', 'max_positions', 'max_leverage', 'cap_gap_buffer', 'daily_loss_pct', 'kill_drawdown_pct'},
-    'cycle': {'cadence_s', 'delay_s', 'mark_poll_s'},
+    'cycle': {'cadence_s', 'delay_s', 'mark_poll_s', 'tick_s'},
+    'reconcile': {'rec02'},
     'account': {'name', 'id', 'portfolio_id', 'equity', 'key_digest'},
     'output': {'dir'},
     'management': {'enabled', 'plan', 'cap_mult'},
@@ -106,6 +111,8 @@ class RunConfig:
     mg_plan: str = 'range_bb_mr_v1'
     mg_cap_mult: Decimal = Decimal('2.5')
     mark_poll_s: float = 10.0                      # [cycle] testnet + management: mark polls between closes (0 = off)
+    tick_s: float = 30.0                           # [cycle] tick_s: testnet check-only cycles between candles
+    rec02: bool = False                            # [reconcile] rec02: the REC-02 fold in the cycle
     tnet_enabled: bool = False                     # [tnet] the TNET-01 harness hooks (TESTNET + testnet venue only)
     tnet_raw_qty: Decimal | None = None            # H2: unsized entry quantity (T10b venue min-qty refusal)
 
@@ -233,9 +240,12 @@ def validate(doc, *, source='', environ=None):
     digest = a.get('key_digest', '0123456789abcdef')
     if not re.fullmatch(r'[0-9a-f]{16}', digest):
         raise ConfigError('account.key_digest: the 16-hex NON-secret binding digest')
-    for k in ('cadence_s', 'delay_s', 'mark_poll_s'):
+    for k in ('cadence_s', 'delay_s', 'mark_poll_s', 'tick_s'):
         if not isinstance(c.get(k, 0), (int, float)) or isinstance(c.get(k, 0), bool) or c.get(k, 0) < 0:
             raise ConfigError(f'cycle.{k}: seconds >= 0')
+    r = doc.get('reconcile', {})
+    if type(r.get('rec02', False)) is not bool:
+        raise ConfigError('reconcile.rec02 must be true / false')
     m = doc.get('management', {})
     if type(m.get('enabled', False)) is not bool:
         raise ConfigError('management.enabled must be true / false')
@@ -253,6 +263,7 @@ def validate(doc, *, source='', environ=None):
     if raw is not None and not tn.get('enabled', False):
         raise ConfigError('tnet.raw_qty needs tnet.enabled = true (an explicit TESTNET-only override)')
     return RunConfig(
+        tick_s=float(c.get('tick_s', 30)), rec02=r.get('rec02', False),
         tnet_enabled=tn.get('enabled', False), tnet_raw_qty=raw, mark_poll_s=float(c.get('mark_poll_s', 10)),
         mg_enabled=m.get('enabled', False), mg_plan=mg_plan,
         mg_cap_mult=_dec(m.get('cap_mult', '2.5'), 'management.cap_mult', lo=1, hi=10),
diff --git a/newcore/runner/managed.py b/newcore/runner/managed.py
index c4d3357..781f20b 100644
--- a/newcore/runner/managed.py
+++ b/newcore/runner/managed.py
@@ -110,11 +110,22 @@ def hold_reconciler(runner, lot_id, items):
         runner._hold([ReasonCode.RECONCILE_UNRECONCILED])
 
 
+def rec02_reconciler(runner, lot_id, items):
+    """The REC-02 slot: a driver item that can mean unowned / unbooked exposure (HOLD_ITEMS) is an UNCERTAIN answer, so
+    the account-level fold runs now (exchange evidence, resolutions it can prove, protection gaps). The driver item
+    itself keeps the stub's handling (HOLD for HOLD_ITEMS, incident otherwise): the driver could not book it, so the
+    owner (or a later REC-02 rule) resolves it. Without rec02: the stub alone."""
+    if getattr(runner.cfg, 'rec02', False) and any(item[0] in HOLD_ITEMS for item in items):
+        from newcore.reconcile import Trigger
+        runner.rec02(Trigger.UNCERTAIN)
+    hold_reconciler(runner, lot_id, items)
+
+
 @dataclass(frozen=True)
 class ManagementConfig:
     enabled: bool = False                    # default OFF: management is opt-in per run
     plans: object = None                     # callable(EntryInfo) -> ManagementPlan | None (None: unmanaged lot)
-    reconciler: object = hold_reconciler     # callable(runner, lot_id, items): the REC-02 slot
+    reconciler: object = rec02_reconciler    # callable(runner, lot_id, items): the REC-02 slot
     quote_asset: str = 'USDT'                # fees in this asset are booked as they are
     fee_rates: tuple = ()                    # ((asset, Decimal rate in quote), ...) for fees in another asset
 
diff --git a/newcore/runner/runner.py b/newcore/runner/runner.py
index 56ff14a..cd94aa3 100644
--- a/newcore/runner/runner.py
+++ b/newcore/runner/runner.py
@@ -68,7 +68,11 @@ from newcore.domain.portfolio import Fill
 from newcore.ports.journal import JournalUnavailable
 from newcore.store.hold import durability_hold, hard_hold_permits
 from newcore.ports.keys import check_decision_key, route_of
-from newcore.ports.venue import MarketOrder, OrderOutcome, OrderRef, OutcomeKind, ReadKind, StopOrder
+from newcore.ports.venue import MarketOrder, OrderOutcome, OrderRef, OutcomeKind, ReadKind, ReadOutcome, StopOrder
+from newcore.reconcile import DecisionKind as RK
+from newcore.reconcile import Outcome as RecOutcome
+from newcore.reconcile import (ReadPlan, RecPolicy, TradeWindow, Trigger, VenueSnapshot, plan_reads, reconcile as
+                               rec_fold, view_from_fold)
 
 from . import ids
 from .fold import Fold, OPEN_STATES
@@ -150,6 +154,20 @@ class RunnerConfig:
     raw_qty: Decimal | None = None             # H2 (TNET-01 T10b only): send THIS entry quantity unsized and unfiltered,
                                                # so the venue's own min-qty / min-notional refusal is exercised;
                                                # refused unless the account is bound to TESTNET
+    rec02: bool = False                        # REC-02 fold replaces reconcile v0 in the normal cycle
+    rec_policy: RecPolicy = None               # None -> RUNNER_REC_POLICY
+
+
+# REC-02 in the runner, until the NC-01 amendments land (docs/newcore/reconcile/RUNNER_WIRING.md section 4):
+#   Q1 per-side quarantine  -> off: the runner's HOLD is account-wide (no per-side mode record yet)
+#   Q2 adopt external       -> off: an external reduction / late fill becomes an owner item (no evidence kind yet)
+#   Q3 protect surplus      -> off: no owner kind for a stop that protects an unowned surplus
+#   Q4 auto clear           -> off: a HOLD -> ACTIVE change needs operator.resume today
+# settle_ms: one positionRisk lag window; max_attempts counts passes per episode, spread over cycles / ticks.
+RUNNER_REC_POLICY = RecPolicy(quarantine_per_side=False, adopt_external_change=False, protect_surplus=False,
+                              auto_clear_hold=False, settle_ms=10_000, max_attempts=4)
+REC_PASSES_PER_CYCLE = 2                       # in-cycle re-read passes; the rest waits for the next cycle / tick
+CORROBORATED = frozenset({'not_found_corroborated', 'position_adopted'})   # journaled by _corroborate_entry
 
 
 @dataclass(frozen=True)
@@ -190,8 +208,18 @@ class Counters:
 
 
 class Runner:
-    def __init__(self, config: RunnerConfig, *, journal, venue, bars, signals, account_reads=None, hard_hold=None):
+    def __init__(self, config: RunnerConfig, *, journal, venue, bars, signals, account_reads=None, hard_hold=None,
+                 clock=None):
         self.cfg = config
+        self.clock = clock                                                # venue-aligned ms (testnet OffsetClock)
+        self.rec_policy = config.rec_policy or RUNNER_REC_POLICY
+        self.last_verdict = None                                          # REC-02 Verdict of the last pass
+        self._rec_attempt = 0                                             # PENDING passes of the open episode
+        self._rec_symbols = {}                                            # exchange order id -> symbol (fills reads)
+        self._rec_reported = set()                                        # owner items already surfaced
+        self._started = False
+        self._rec_running = False
+        self._answers = {}                                                # client id -> (state, n results, answer)
         self.acct = config.account.account_id
         self.pf = config.portfolio_id
         self.journal, self.venue, self.bars, self.signals = journal, venue, bars, signals
@@ -320,7 +348,9 @@ class Runner:
     def _resolve(self, iv):
         """One query for an intent whose answer was lost or acknowledged only."""
         if iv.live and iv.state in (IntentState.SUBMITTED, IntentState.UNKNOWN):
-            self._apply(iv, self.venue.query(self._ref(iv)), submit=False)
+            out = self.venue.query(self._ref(iv))
+            self._apply(iv, out, submit=False)
+            self._remember(iv, out)
             if self._not_found(iv) and iv.purpose in REDUCE_ONLY:
                 self._resend(iv)                                          # never landed: same id, same route
 
@@ -341,6 +371,7 @@ class Runner:
             raise ValueError('the runner clock never goes back')
         self.now = now_ms
         self.counters.cycles += 1
+        self._answers = {}                                                # by-id answers are per cycle
         if self.hard_hold is not None:
             return self._hard_hold_cycle()
         try:
@@ -471,27 +502,211 @@ class Runner:
         return None
 
     def _cycle(self, decide):
+        trigger = Trigger.CYCLE if decide else Trigger.TICK
+        if not self._started:
+            trigger, self._started = Trigger.STARTUP, True
         self._sync()
-        rec = self.reconcile()
-        if not rec.ok:
-            self._hold([ITEM_REASON[k] for k, _ in rec.items])
+        rec = self._reconcile_step(trigger)
         self._protect_all()
         self._handover_emergency()
         if decide:
             self._decide_all()
-        rec = self.reconcile()
+        rec = self._reconcile_step(Trigger.CYCLE if decide else Trigger.TICK)
         if not rec.ok:
             self.counters.mismatch_cycles += 1
-            self._hold([ITEM_REASON[k] for k, _ in rec.items])
         self.check_invariants(rec)
         return rec
 
+    def _reconcile_step(self, trigger):
+        """Reconcile v0 (items -> HOLD), or the REC-02 fold when the config enables it (it holds by itself)."""
+        if self.cfg.rec02:
+            return self.rec02(trigger)
+        rec = self.reconcile()
+        if not rec.ok:
+            self._hold([ITEM_REASON[k] for k, _ in rec.items])
+        return rec
+
+    def tick(self, now_ms):
+        """A check-only cycle between candles (plan gap 6): sync, reconcile, protect - never a decision."""
+        return self.cycle(now_ms, decide=False)
+
+    def needs_tick(self):
+        """True while something only a re-read can settle: a PENDING REC-02 episode, a sent intent whose answer is not
+        known, or a HOLD whose reasons a fresh match could clear (the owner still resumes it until NC-01 A5)."""
+        if self.hard_hold is not None:
+            return False
+        if self.last_verdict is not None and self.last_verdict.outcome is RecOutcome.PENDING:
+            return True
+        if any(iv.state in (IntentState.SUBMITTED, IntentState.UNKNOWN) for iv in self.fold.live_intents()):
+            return True
+        return (self.rec_policy.auto_clear_hold and self.fold.mode is EntriesMode.HOLD
+                and set(self.fold.mode_reasons) <= set(self.rec_policy.auto_clearable))
+
+    # ----------------------------------------------------------------------------------------------- REC-02
+    def _rec_now(self):
+        return self.clock() if self.clock is not None else self.now
+
+    def rec02(self, trigger):
+        """REC-02 (newcore.reconcile): one account-level fold of the journal against fresh venue truth, applied through
+        the runner's own journal-first machinery. At most REC_PASSES_PER_CYCLE passes here; a PENDING episode keeps its
+        attempt count across cycles / ticks (so a read lag gets max_attempts passes spread over time, not in one burst)
+        and ends in HOLD only when the attempts are spent."""
+        if self._rec_running:                     # re-entered from an action it applied (management flush): once only
+            return self.last_rec
+        self._rec_running = True
+        try:
+            return self._rec02(trigger)
+        finally:
+            self._rec_running = False
+
+    def _rec02(self, trigger):
+        needs, verdict, snap = ReadPlan(), None, None
+        for _ in range(REC_PASSES_PER_CYCLE):
+            self.counters.reconciliations += 1
+            view = view_from_fold(self.fold, binding_confirmed=self._binding_confirmed(), corroboration=self._reads)
+            snap = self._rec_snapshot(view, needs)
+            view = view_from_fold(self.fold, binding_confirmed=self._binding_confirmed(), corroboration=self._reads,
+                                  stop_hints=dict(self._rec_stop_hints(snap)))
+            verdict = rec_fold(view, snap, now_ms=self._rec_now(), trigger=trigger,
+                               attempt=min(self._rec_attempt, self.rec_policy.max_attempts - 1), policy=self.rec_policy)
+            acted = self._rec_apply(verdict, snap)
+            if verdict.outcome is not RecOutcome.PENDING:
+                self._rec_attempt = 0
+                break
+            self._rec_attempt += 1
+            if not acted and verdict.needs == needs:
+                break                                                     # nothing new this cycle: next cycle / tick
+            needs = verdict.needs
+        self.last_verdict = verdict
+        rec = self._rec_reconciliation(verdict, snap)
+        self.last_rec = rec
+        return rec
+
+    def _binding_confirmed(self):
+        return str(self.cfg.account.binding_state) == 'confirmed'
+
+    def _rec_snapshot(self, view, needs):
+        """The reads one pass needs: by-id queries (plan_reads + the last pass's asks), the fills of every FINAL with an
+        execution or triggered algo stop, userTrades windows the fold asked for, then positions + open orders (last,
+        so they are the newest reads)."""
+        plan = plan_reads(view, now_ms=self._rec_now(), policy=self.rec_policy)
+        queries = []
+        for c in sorted(set(plan.queries) | set(needs.queries)):
+            iv = self.fold.by_client_id.get(c)
+            if iv is None:
+                continue
+            hit = self._answers.get(c)
+            if c not in needs.queries and hit is not None and hit[:2] == (iv.state, len(iv.results)):
+                q = hit[2]                                                # this cycle's sync answer, still current
+            else:
+                q = self.venue.query(self._ref(iv))
+                self._remember(iv, q)
+            queries.append((c, q))
+            if q.exchange_order_id is not None:
+                self._rec_symbols[q.exchange_order_id] = iv.intent.symbol
+        eoids = {q.exchange_order_id for _, q in queries if q.exchange_order_id is not None and (
+            (q.kind is OutcomeKind.FINAL and q.executed_qty) or q.detail == ALGO_TRIGGERED)} | set(needs.fills)
+        fills = tuple((e, self.venue.fills(self._rec_symbols[e], e)) for e in sorted(eoids) if e in self._rec_symbols)
+        trades = tuple(((s, side), TradeWindow(from_ms=since, read=self._rec_trades(s, side, since)))
+                       for s, side, since in needs.trades)
+        return VenueSnapshot(positions=self.venue.positions(), orders=self.venue.open_orders(), queries=tuple(queries),
+                             fills=fills, trades=trades)
+
+    def _remember(self, iv, out):
+        """A by-id answer of this cycle, keyed to the intent's state + result count it was applied to (reused by
+        REC-02 only while both are unchanged: one venue query per intent per cycle, not two)."""
+        self._answers[iv.intent.client_order_id] = (iv.state, len(iv.results), out)
+
+    def _rec_trades(self, symbol, side, since):
+        """userTrades of one side since `since`: the venue's `trades` read when it has one (FaultVenue does; the
+        TestnetVenue port does not yet - interface item for the transport lane), else UNKNOWN (never empty)."""
+        read = getattr(self.venue, 'trades', None)
+        if read is None:
+            return ReadOutcome(kind=ReadKind.UNKNOWN, observed_at_ms=self._rec_now(), detail='no_trades_read')
+        return read(symbol, side, since)
+
+    def _rec_stop_hints(self, snap):
+        """Stop levels the fold cannot see in the journal: a lot with no protect intent yet (a crash between its fill and
+        its stop: the level the protect phase will use, stop_price_of) and a side with a position and no lot (an
+        unresolved / adopted opening fill: the entry's stop distance from the venue entry price, the A23 rule)."""
+        hints = {}
+        for lot in self.fold.open_lots():
+            if not lot.protects:
+                try:
+                    hints[(lot.symbol, lot.side)] = self._protect_price(lot)
+                except (LookupError, AttributeError):
+                    pass
+        if snap.positions.kind is not ReadKind.OK:
+            return tuple(sorted(hints.items()))
+        lots = {(x.symbol, x.side) for x in self.fold.open_lots()}
+        for p in snap.positions.value:
+            if p.qty > 0 and (p.symbol, p.side) not in lots and p.symbol in self.cfg.rules:
+                try:
+                    level = self._emergency_stop_price(p)
+                except (LookupError, AttributeError):
+                    level = None
+                if level is not None:
+                    hints[(p.symbol, p.side)] = level
+        return tuple(sorted(hints.items()))
+
+    def _rec_apply(self, verdict, snap):
+        """Each decision through the runner's existing paths. True when something was sent or journaled."""
+        acted = False
+        for d in verdict.decisions:
+            if d.kind is RK.PROTECT_ONLY:
+                if d.lot_id is not None and self._lot(d.lot_id) is not None:
+                    continue        # an owned lot: the protect phase owns it (pending close first, bounded attempts)
+                self._rec_owner_item(d, ReasonCode.PROTECT_CHECKING)     # Q3 off: no journaled owner for this stop
+            elif d.kind in (RK.RESOLVE_FILLED, RK.RESOLVE_NOT_EXECUTED):
+                iv = self.fold.intents.get(d.intent_id)
+                if iv is None or d.detail in CORROBORATED:
+                    continue                                              # _corroborate_entry journals these
+                if d.detail == 'supersedes_not_found_corroborated':
+                    self._rec_owner_item(d, ReasonCode.EVIDENCE_LATE_FILL_NOT_ACTIVE)    # NC-01 A2
+                elif iv.live and snap.query(iv.intent.client_order_id) is not None:
+                    self._apply(iv, snap.query(iv.intent.client_order_id), submit=False)   # the venue's own record
+                    acted = True
+            elif d.kind is RK.ADOPT:
+                self._rec_owner_item(d, ReasonCode.OWNERSHIP_UNTRACKED_POSITION)        # NC-01 A1
+            elif d.kind in (RK.HOLD, RK.QUARANTINE):
+                if d.detail != 'owner_resume_required':
+                    self._rec_owner_item(d, d.reasons[0] if d.reasons else ReasonCode.RECONCILE_UNRECONCILED)
+            elif d.kind is RK.CLEAR_HOLD:
+                self._rec_report(d, f'rec02 {verdict.reconciliation_id}: HOLD clearable by a fresh full match; '
+                                    f'owner resume until NC-01 A5')
+        return acted
+
+    def _rec_report(self, d, text):
+        key = (d.kind, d.row, d.detail, d.symbol, d.side, d.client_id, d.intent_id)
+        if key not in self._rec_reported:
+            self._rec_reported.add(key)
+            self._incident(text)
+
+    def _rec_owner_item(self, d, reason):
+        """An item only the owner can resolve: one incident (deduplicated) with its evidence and owner actions, and a
+        durable HOLD (account-wide until NC-01 A4)."""
+        self._rec_report(d, f'rec02 {d.row} {d.kind} {d.detail} {d.symbol or ""} {d.side or ""} qty={d.qty} '
+                            f'actions={",".join(d.owner_actions)} evidence={",".join(d.evidence[:6])}')
+        self._hold([reason], reason=reason)
+
+    def _rec_reconciliation(self, verdict, snap):
+        """The verdict as the runner's Reconciliation (invariants, portfolio proof, resume): items = everything that is
+        not settled (a HOLD only the owner's resume is waiting for is not an item, so resume() can pass)."""
+        items = tuple((f'{d.row}:{d.detail}', d.symbol or '') for d in verdict.decisions
+                      if d.kind in (RK.HOLD, RK.QUARANTINE, RK.REREAD) and d.detail != 'owner_resume_required')
+        if verdict.outcome is RecOutcome.PENDING and not items:
+            items = (('pending', verdict.reconciliation_id),)
+        ok = snap is not None and snap.positions.kind is ReadKind.OK and snap.orders.kind is ReadKind.OK
+        return Reconciliation(self.now, verdict.reconciliation_id, items, snap.positions.value if ok else None,
+                              snap.orders.value if ok else None)
+
     # ----------------------------------------------------------------------------------------------- 1. sync
     def _sync(self):
         for iv in list(self.fold.live_intents()):
             if iv.state in OPEN_STATES:
                 out = self.venue.query(self._ref(iv))
                 self._apply(iv, out, submit=False)
+                self._remember(iv, out)
                 if iv.state is IntentState.CANCELLING and out.kind is OutcomeKind.KNOWN:
                     self._apply(iv, self.venue.cancel(self._ref(iv)), submit=False)    # the cancel never reached it
                 if self._not_found(iv):
@@ -859,6 +1074,9 @@ class Runner:
     def _entry_gate(self, symbol, side):
         if self.fold.mode is not EntriesMode.ACTIVE:
             return GATE_REASON[self.fold.mode]
+        v = self.last_verdict
+        if self.cfg.rec02 and v is not None and v.outcome is RecOutcome.PENDING and v.of(RK.REREAD):
+            return ReasonCode.RECONCILE_UNRECONCILED                      # venue truth still unverified: no new risk
         if any(x.symbol == symbol and x.side == side for x in self.fold.open_lots()):
             return ReasonCode.CAPACITY_IN_TRADE
         if self.fold.live_entries(symbol, side):
diff --git a/newcore/runner/testnet_hook.py b/newcore/runner/testnet_hook.py
index 501f8a0..4ee78a1 100644
--- a/newcore/runner/testnet_hook.py
+++ b/newcore/runner/testnet_hook.py
@@ -66,11 +66,12 @@ def load_factory(spec):
 
 
 def build(config):
-    """(venue, bars, account_reads, instrument_rules or None) from the configured factory (newcore.venue.factory:
-    build_testnet on nc-venue-testnet returns venue, bars, account_reader and the venue's own instrument_rules)."""
+    """(venue, bars, account_reads, instrument_rules or None, clock or None) from the configured factory
+    (newcore.venue.factory:build_testnet on nc-venue-testnet returns venue, bars, account_reader, the venue's own
+    instrument_rules and its server-aligned OffsetClock: REC-02 judges read freshness on that clock)."""
     parts = load_factory(config.factory)(config)
     missing = {'venue', 'bars'} - set(parts)
     if missing or not ({'account_reader', 'account_reads'} & set(parts)):
         raise ValueError(f'the testnet factory must return venue, bars and account_reader / account_reads ({parts!r})')
     reads = parts.get('account_reads') or AccountReadsShim(parts['account_reader'])
-    return parts['venue'], parts['bars'], reads, parts.get('instrument_rules')
+    return parts['venue'], parts['bars'], reads, parts.get('instrument_rules'), parts.get('clock')
```
