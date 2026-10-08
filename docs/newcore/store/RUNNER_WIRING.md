# RUNNER_WIRING: the S1 runner on the NC-02b account store

Status: **proposal for the S1 lane, not applied.** `newcore/runner/**` and `tests/newcore_slice/**` belong to S1; this
branch (nc-02b-store) only ships the store side and this patch.

| | |
|---|---|
| Patch | `docs/newcore/store/RUNNER_WIRING.patch` (the same bytes are reproduced at the end of this file) |
| Base it applies to | `origin/nc-s1-slice` **b453027** (`newcore/runner/app.py`, `tests/newcore_slice/test_run_cli.py`, `tests/newcore_slice/test_hard_hold_a24.py`) |
| Store it needs | `newcore/store` from `nc-02b-store` **5c3896e** or later (`open_store`, `Outcome`, `VenueExchangeView`, `AccountStore.checkpoint / checkpoint_due / close`) |
| Verified | On 2026-10-08 (Cairo), in a temp export of `origin/nc-s1-slice` b453027 plus `newcore/store` at 5c3896e (and earlier at 8bace5a). The patch passed `git apply --check`, and the full `tests/newcore_slice` gave **1165 passed, 1 skipped**. The patch does **not** apply to facd4b6 (see the status section below). |

## How to apply

1. On `nc-s1-slice`, take the NC-02b store: `git merge origin/nc-02b-store` (normal merge, no force-push). After that
   merge the only `newcore/store` user that still needs changing is `newcore/runner/app.py`.
2. From the repo root: `git apply --check docs/newcore/store/RUNNER_WIRING.patch`, then `git apply` the same file.
3. Run the tests listed under "Tests to run". All must pass before the S1 head is handed to Codex.

If `nc-s1-slice` has moved past b453027 and the patch no longer applies, ask the Build lane to regenerate it.
The patch is produced by a script from exact-match anchors, so regenerating it is quick.

## S1 has moved past the base: facd4b6 (guard mode) - status and plan

At facd4b6 S1 added three things:

* the tail-loss **GUARD** (Codex ruling 13): when the store returns HOLD, `cmd_run` routes to `cmd_guard`, which
  runs one emergency-set pass over exchange truth on an in-memory journal and then exits 4;
* Cowork M2: a zero-byte segment means HOLD;
* the "deleted journal while the venue holds exposure" refusal.

The `open_journal` anchor changed, so **this patch does not apply to facd4b6**. Here is how the integration goes
there. It will be regenerated when S1 settles, or S1 can apply it directly.

1. `Session(cfg, enabled, guard=None)`: with `guard`, keep S1's `MemoryJournal` and `hard_hold=guard`, and do **not**
   boot the store, because the guard never touches the store. Without `guard`, call `boot_store` exactly as below.
2. `boot_store` keeps its mapping. HOLD and HOLD_INIT raise `StoreRefused(EXIT_STORE_HOLD)`, and facd4b6's `cmd_run`
   already routes that to `cmd_guard`. So a store HOLD gets the guard pass and then exit 4, instead of exiting with
   no pass at all.
3. S1's zero-byte-segment check and its "no history while the venue holds exposure" check are both now answered by
   the store. A journal behind its committed generation, or a lost / damaged journal member, is rule 4 HOLD. An INIT
   against a non-flat exchange is HOLD-INIT. Keep S1's `_venue_exposure` check after boot as a second guard, but
   close the store before raising.
4. Several S1 tests edit `<journal dir>/<account id>/journal` directly: `test_cowork37_meds` (zero-byte),
   `test_cowork_recheck` (deleted journal), `test_tail_loss_guard`, `test_journal_compat` and `test_run_cli`. They
   move to `<journal dir>/data/accounts/<account id>/journal`. Their expected exit codes stay 4 (store HOLD, then the
   guard).

## What the patch changes

`newcore/runner/app.py`:

* The `open_journal()` function, which called `create_journal` / `recover_journal` directly, is replaced by
  `boot_store()`. That function builds a `VenueExchangeView` over the venue's reads and calls
  `newcore.store.open_store`, which returns a typed outcome (see below).
* `Session.__init__` builds the runner through a `make(journal, hard_hold=None)` factory. It also passes the store a
  `fold` hook, then keeps `self.boot`, `self.store` and `self.journal` (the writable journal, or the read-only view
  in hard HOLD).
* `Session.checkpoint()` runs after every `cycle()` in `cmd_run`.
* `Session.close()` also closes the store, which releases the writer lock even in hard HOLD.
* `StoreDown` is the boot directive handed to `ManagedBookRunner(hard_hold=...)`.
* The module docstring's exit codes are updated.
* The replay command (`cmd_replay`) is unchanged: it still writes a fresh `create_journal()` per symbol, outside the
  account store.

`tests/newcore_slice/test_run_cli.py`: two path assertions move to the store layout. The journal-bytes comparison
reads only `*.seg` files.

`tests/newcore_slice/test_hard_hold_a24.py`: the "file journal really runs" guard ignores `journal/.lock`.
This is **independent of the wiring**. `.lock` is the NC-02a writer fence (Cowork finding 3, in `nc-02a-journal`
since ba837fb), and that guard fails on any `nc-s1-slice` that takes the current store, with or without this patch.

`tests/newcore_slice/test_store_wiring.py` (new): the five wiring tests listed under "Tests to run".

## Boot: outcome to runner behaviour

`boot_store()` maps `open_store(...)` like this. A refusal writes nothing.

| `open_store` outcome | Meaning | Runner |
|---|---|---|
| `INIT` | First run, flat exchange, confirmed binding: the store was created known-empty. | runs; entries allowed |
| `MANAGE` | The committed generation is MANAGED and a fresh exchange snapshot matches it. Alternatively, the fold of an uncovered journal tail matched and was checkpointed at once. | runs |
| `HOLD`, `hold_kind = DURABILITY_UNAVAILABLE` (hard HOLD) | The store cannot write. `b.view` is the read-only journal: events, gate and state can be read, and every append is refused. | runs with `hard_hold=StoreDown(reason)`, so only the A23 / A24 emergency set runs, priced from the view |
| `HOLD` (normal) | Quarantine. `b.items` say why: exchange mismatch, journal tail not covered, identity, D11 marker, and so on. | **exit 4**; the items are printed |
| `HOLD_INIT` | First run against a non-flat exchange, or an unconfirmed binding. | **exit 4** |
| `ABORT_RO` | A future or unknown format somewhere: no account context, zero writes. | **exit 5** |
| `REJECT` | Not started: another process holds the account (`locked: ...`), or a first run must wait (exchange down, or the data folder cannot be written). | **exit 3** |

Exit-code change: **3 used to mean "store cannot write at boot"**. That case is now hard HOLD, and the process runs
the emergency set instead of exiting. **3 now means REJECT** (locked, or INIT waits). Codes 4 and 5 keep their
meaning, and HOLD_INIT also exits 4.

**HOLD and HOLD_INIT run nothing until `AccountStore.promote()`.** That is the only way out of HOLD (A08). It needs a
fresh exchange snapshot that matches, or an owner decision id for adoption, identity changes and trivially-empty
stores. There is **no owner promote CLI yet**. Follow-up ticket for S1: a `run promote --config ... [--decision <id>]`
command that calls `open_store`, prints `b.items` and `b.candidate`, then calls
`b.store.promote(view, now, candidate=b.candidate, owner_decision_id=...)` and exits 0 only on MANAGE.

## Checkpoint (ruling item 6)

`Session.checkpoint()` runs after every cycle. It does nothing in these cases: no store, the runner is in hard HOLD,
or `store.checkpoint_due` is false. `checkpoint_due` is true only when the journal has events past the committed
generation's `lsn_upto`.

* `store.checkpoint(runner.portfolio(), runner.now)` writes a new generation that holds the runner's NC-01 portfolio
  at the journal's last sequence. That generation is trusted as MANAGED only if the store is MANAGE and the portfolio
  is proven and not in HOLD. Otherwise it is a HOLD generation, and the next start lands in HOLD (A05).
* `ValueError` means the checkpoint is not provable right now: the portfolio names another account or aggregate, or
  its proof reaches past the journal. The runner logs an incident and tries again next cycle.
* `DurabilityUnavailable` sends the runner to `store_unavailable(ex)`, which is hard HOLD in-process. The store
  itself also goes into hard HOLD: it writes the D11 marker best-effort and refuses every later checkpoint, so the
  next start lands in HOLD even with a writable disk.
* A checkpoint is an ordinary generation commit (snapshot create, then the HEAD slot write). If the process crashes
  anywhere inside it, the store keeps the previous generation plus the journal tail. The store's crash drill checks
  this: `tests/newcore_store/test_nc02b_checkpoint_drill.py` covers every write boundary, under four power-loss
  models, with and without the fold hook.

## The fold hook (crash between an event and its checkpoint)

```python
def fold(journal, snapshot_portfolio):
    probe = make(journal)        # a second ManagedBookRunner over the store's journal; folds journal.read()
    probe.now = now
    return probe.portfolio()
```

The store calls the hook only when the journal tail after the committed generation holds ownership-changing events.
It accepts the result only under all of these conditions:

* the proof is `JOURNAL` and `through_sequence == journal.last_sequence()`;
* it names the same account and aggregate;
* it then matches the fresh exchange snapshot.

When all of those hold, the boot is MANAGE and the tail is checkpointed at once, so later boots do not need the hook.
Anything else is HOLD with the item `journal_tail_unapplied`, and a hook that raises is also HOLD.

**S1 check:** the probe must have no side effects. Constructing a `ManagedBookRunner` and calling `.portfolio()` must
not send, journal or touch the venue. At b453027 that holds: the constructors (`Runner`, `BookRunner`,
`ManagedBookRunner`) only fold `journal.read()` and set up in-memory state, and `portfolio()` only builds the NC-01 value. The patch relies on it, so S1 must keep it true.

## VenueExchangeView

`VenueExchangeView(reads, key_digest=cfg.key_digest, steps={symbol: rule.step_size})`:

* `key_digest` is the **configured** binding digest, the same one `account(cfg)` binds. It is not derived from the
  venue.
* `snapshot()` reads positions and open orders once each. An `UNKNOWN` or `REJECTED` read raises `ExchangeDown`,
  because an unknown read is never treated as an empty account. On a first run that gives REJECT (INIT waits);
  otherwise it gives HOLD.
* Flat position sides are dropped. A `closePosition` order counts as infinite reduce-only qty.

## Store layout (changes where files live)

```
<journal dir>/data/accounts/<account id>/HEAD.a|HEAD.b      dual-slot commit record (D1)
<journal dir>/data/accounts/<account id>/snap/              write-once generations (g<20>.snap)
<journal dir>/data/accounts/<account id>/journal/           NC-02a segments + .lock (writer fence)
<journal dir>/data/accounts/<account id>/evidence/          DPAPI envelopes, private DACL (D5)
<journal dir>/anchors/<account id>.a|.b                     high-water anchor + D11 hard-HOLD marker (D2, outside data\)
<journal dir>/anchors/by-binding/<key digest>               which account a binding belongs to (written once)
<journal dir>/incidents/boot.jsonl                          boot incidents (D14, outside data\)
<journal dir>/<account id>/                                 fake-venue state only (unchanged, not the store)
<journal dir>/reports/                                      unchanged
```

An existing PAPER journal at `<journal dir>/<account id>/journal` is **not migrated**. The store neither reads it nor
deletes it. Its fake-venue state still exists, so if that state holds a position, the first wired start sees a
non-flat exchange and lands in HOLD_INIT (exit 4). For a clean PAPER start, use a new journal dir, or remove the old
PAPER `<account id>` folder by hand. This applies to PAPER / testnet only.

## Tests to run

On the patched `nc-s1-slice`:

```
python -m pytest -p no:cacheprovider -q tests/newcore_slice/test_store_wiring.py tests/newcore_slice/test_run_cli.py tests/newcore_slice/test_hard_hold_a24.py
python -m pytest -p no:cacheprovider -q tests/newcore_slice
python -m pytest -p no:cacheprovider -q tests/newcore_store
```

`test_store_wiring.py`:

* a restart mid-trade MANAGEs from the cycle checkpoint, and the boot fold hook does not run;
* a second process on the same account exits 3 (locked), and the account starts after the first one closes;
* a store HOLD (the fake exchange no longer matches) exits 4, prints `promote()`, and runs no cycle;
* a checkpoint the store cannot write sends the runner into hard HOLD;
* the store layout.

The restart test is also a mutation guard. With `s.checkpoint()` removed from `cmd_run`, it fails, because the boot
then needs the fold hook.

Expected results: `tests/newcore_slice` 1165 passed, 1 skipped at b453027. A mutant with the per-cycle checkpoint removed fails `test_a_restart_mid_trade_manages_from_the_last_checkpoint`.

## The patch (verbatim copy of RUNNER_WIRING.patch)

```diff
--- a/newcore/runner/app.py
+++ b/newcore/runner/app.py
@@ -14,7 +14,9 @@
          <journal dir>/replay-<symbol> (or in memory), reports per symbol; --compare prints the match against the
          research trade list (the 224 / 224 check over the core 8).
 The strategy (trend_ema_mom.v1) is DISABLED unless the config says enabled = true AND --enable-candidate is given.
-Exit codes: 0 ok, 2 config refused, 3 store cannot write at boot, 4 store HOLD verdict, 5 store ABORT-RO.
+Exit codes: 0 ok, 2 config refused, 3 store refused to start (another process / INIT waits), 4 store HOLD /
+HOLD-INIT (items printed; leaving it is the store's promote()), 5 store ABORT-RO.
+The account store (NC-02b) lives under <journal dir>/data/accounts/<account id>; boot = newcore.store.open_store.
 """
 from __future__ import annotations

@@ -33,7 +35,8 @@
 from newcore.domain import (Account, AccountBinding, BindingConfirmation, BindingState, Environment, Venue,
                             confirmation_phrase)
 from newcore.risk import BookPolicy
-from newcore.store import Verdict, create_journal, recover_journal
+from newcore.store import DurabilityUnavailable, Outcome, create_journal, open_store
+from newcore.store.exchange_view import VenueExchangeView
 from newcore.strategy import Params

 from . import config as C
@@ -51,6 +54,10 @@
 EXIT_CONFIG, EXIT_STORE_DOWN, EXIT_STORE_HOLD, EXIT_ABORT_RO = 2, 3, 4, 5


+class StoreDown(Exception):
+    """The boot directive of a hard HOLD (the store cannot write): Runner(hard_hold=...)."""
+
+
 class StoreRefused(Exception):
     def __init__(self, code, text):
         super().__init__(text)
@@ -94,20 +101,22 @@
                    binding_state=BindingState.CONFIRMED, proposed_binding=None, confirmation=conf)


-def open_journal(account_dir, cfg):
-    """FileJournal of the account: created on the first run, recovered (CLEAN / REPAIRED) on every later one."""
-    jd = os.path.join(account_dir, 'journal')
-    if not os.path.isdir(jd) or not os.listdir(jd):
-        return create_journal(account_dir, cfg.account_id, cfg.portfolio_id)
-    r = recover_journal(account_dir, cfg.account_id, cfg.portfolio_id)
-    if r.verdict in (Verdict.CLEAN, Verdict.REPAIRED):
-        return r.journal
-    if r.verdict is Verdict.DURABILITY_UNAVAILABLE:
-        raise StoreRefused(EXIT_STORE_DOWN, f'store cannot write at boot ({r.findings}): hard HOLD needs the '
-                                            'emergency set with a read-only journal view (NC-02a gap)')
-    if r.verdict is Verdict.ABORT_RO:
-        raise StoreRefused(EXIT_ABORT_RO, f'journal of an unknown / future format: ABORT-RO ({r.findings})')
-    raise StoreRefused(EXIT_STORE_HOLD, f'journal {r.verdict}: HOLD, nothing is run ({r.findings})')
+def boot_store(cfg, reads, rules, now_ms, fold):
+    """NC-02b boot (newcore.store.open_store): the typed outcome decides whether this process runs at all.
+    INIT / MANAGE: the writable journal. HOLD with hold_kind DURABILITY_UNAVAILABLE (hard HOLD): the read-only view
+    (events, gate, state; every append refused) so the runner can price the A23 / A24 emergency set. Anything else
+    refuses to start with its exit code; nothing is written by a refusal."""
+    view = VenueExchangeView(reads, key_digest=cfg.key_digest, steps={s: r.step_size for s, r in rules.items()})
+    b = open_store(cfg.journal_dir, account(cfg), exchange=view, now_ms=now_ms, aggregate_id=cfg.portfolio_id,
+                   fold=fold)
+    if b.outcome in (Outcome.INIT, Outcome.MANAGE) or (b.outcome is Outcome.HOLD and b.hard_hold):
+        return b
+    if b.outcome is Outcome.ABORT_RO:
+        raise StoreRefused(EXIT_ABORT_RO, f'store of an unknown / future format: ABORT-RO ({b.findings})')
+    if b.outcome is Outcome.REJECT:
+        raise StoreRefused(EXIT_STORE_DOWN, f'store refused to start: {b.reason}')
+    items = '; '.join(f'{i.scope} {i.cause} {i.ref}'.strip() for i in b.items)
+    raise StoreRefused(EXIT_STORE_HOLD, f'store {b.outcome}: nothing is run until promote() ({items})')


 def signals_for(cfg, enabled):
@@ -150,9 +159,8 @@
     def __init__(self, cfg, enabled):
         self.cfg = cfg
         self.tf_ms = TF_MS[cfg.tf]
-        self.account_dir = os.path.join(cfg.journal_dir, cfg.account_id)
-        os.makedirs(cfg.journal_dir, exist_ok=True)
-        self.journal = open_journal(self.account_dir, cfg)
+        self.account_dir = os.path.join(cfg.journal_dir, cfg.account_id)       # fake venue state (not the store)
+        os.makedirs(self.account_dir, exist_ok=True)
         if cfg.venue_kind == 'fake':
             self.bars = data_source(cfg, cfg.symbols)
             candles = {s: self.bars.all_bars(s) for s in cfg.symbols}
@@ -176,9 +184,24 @@
                             sizing=SizingPolicy(cfg.risk_pct, cfg.max_leverage, cfg.cap_gap_buffer),
                             sides=sides_for(cfg), strict=False,
                             raw_qty=cfg.tnet_raw_qty if cfg.tnet_enabled else None)
-        self.runner = ManagedBookRunner(rcfg, policy=policy_for(cfg), journal=self.journal, venue=port,
-                                        bars=self.bars, signals=signals_for(cfg, enabled), account_reads=reads,
-                                        management=management_for(cfg))
+        now = self.venue.now_ms if self.venue is not None else int(time.time() * 1000)
+
+        def make(journal, hard_hold=None):
+            return ManagedBookRunner(rcfg, policy=policy_for(cfg), journal=journal, venue=port, bars=self.bars,
+                                     signals=signals_for(cfg, enabled), account_reads=reads,
+                                     management=management_for(cfg), hard_hold=hard_hold)
+
+        def fold(journal, snapshot_portfolio):
+            """Only for a journal tail no checkpoint covers (a crash between an event and its checkpoint)."""
+            probe = make(journal)                     # folds journal.read(); sends nothing
+            probe.now = now
+            return probe.portfolio()
+
+        self.boot = b = boot_store(cfg, reads, rcfg.rules, now, fold)
+        self.store = b.store
+        self.journal = b.journal or b.view
+        hard = b.hard_hold
+        self.runner = make(self.journal, hard_hold=StoreDown(b.reason) if hard else None)

     def next_close(self, wall_ms=None):
         """The candle close of the next cycle, or None when the fake data is exhausted."""
@@ -216,6 +239,18 @@
                 n += self.runner.mark(sym, r.value[0], now)
         return n

+    def checkpoint(self):
+        """Ruling item 6: after every cycle that journaled an ownership-changing event, a new generation holding the
+        runner's portfolio, so the next start MANAGEs without an event -> portfolio apply in the store."""
+        if self.store is None or self.runner.hard_hold is not None or not self.store.checkpoint_due:
+            return
+        try:
+            self.store.checkpoint(self.runner.portfolio(), self.runner.now)
+        except DurabilityUnavailable as ex:           # the store cannot write: hard HOLD now (NC-02 A21)
+            self.runner.store_unavailable(ex)
+        except ValueError as ex:                      # not provable now (e.g. no fresh flat snapshot): next cycle
+            self.runner.incidents.append((self.runner.now, f'checkpoint skipped: {ex}'))
+
     def save(self):
         if self.venue is not None:
             path = os.path.join(self.account_dir, STATE_FILE)
@@ -229,6 +264,8 @@
         close = getattr(self.journal, 'close', None)
         if close is not None:
             close()
+        if self.store is not None:                    # releases the writer lock in hard HOLD too (idempotent)
+            self.store.close()


 # ---------------------------------------------------------------------------------------------------- commands
@@ -258,6 +295,7 @@
                         break
                     continue
             s.cycle(t)
+            s.checkpoint()
             last = t
             done += 1
             s.save()
--- a/tests/newcore_slice/test_run_cli.py
+++ b/tests/newcore_slice/test_run_cli.py
@@ -109,7 +109,8 @@
     assert 'Cairo (' in health[0] and 'mode=ACTIVE hold=- positions=flat protected=yes' in health[0]
     acct = C.load(cfg_file(tmp_path)).account_id
     base = tmp_path / 'nc'
-    assert (base / acct / 'journal').is_dir() and (base / acct / A.STATE_FILE).is_file()
+    store = base / 'data' / 'accounts' / acct                               # the NC-02b account store
+    assert (store / 'journal').is_dir() and (store / 'HEAD.a').is_file() and (base / acct / A.STATE_FILE).is_file()
     assert (base / 'reports' / 'trades.csv').read_text().startswith('symbol,side,lot_id')
     assert (base / 'reports' / 'incidents.jsonl').exists()

@@ -135,8 +136,8 @@
         assert (tmp_path / 'one' / 'reports' / f).read_bytes() == (tmp_path / 'two' / 'reports' / f).read_bytes()
     acct = C.load(one).account_id
     assert (tmp_path / 'one' / acct / A.STATE_FILE).read_bytes() == (tmp_path / 'two' / acct / A.STATE_FILE).read_bytes()
-    segs = lambda d: b''.join((d / acct / 'journal' / n).read_bytes()
-                              for n in sorted(os.listdir(d / acct / 'journal')))
+    jd = lambda d: d / 'data' / 'accounts' / acct / 'journal'
+    segs = lambda d: b''.join((jd(d) / n).read_bytes() for n in sorted(os.listdir(jd(d))) if n.endswith('.seg'))
     assert segs(tmp_path / 'one') == segs(tmp_path / 'two')                  # the journal bytes too


--- a/tests/newcore_slice/test_hard_hold_a24.py
+++ b/tests/newcore_slice/test_hard_hold_a24.py
@@ -289,6 +289,7 @@
     if journal_kind == "file":
         from file_journal_harness import FileJournalProxy
         assert isinstance(w.journal, FileJournalProxy)
-        segs = os.listdir(os.path.join(w.journal._j.account_dir, 'journal'))
+        segs = [n for n in os.listdir(os.path.join(w.journal._j.account_dir, 'journal'))
+                if n != '.lock']                                    # NC-02a writer fence (Cowork finding 3)
         assert segs and all(s.endswith('.seg') for s in segs)
         assert len(w.journal.reopen().read()) == len(w.journal.read()) > 0
--- /dev/null
+++ b/tests/newcore_slice/test_store_wiring.py
@@ -0,0 +1,70 @@
+"""The runner on the NC-02b account store (docs/newcore/store/RUNNER_WIRING.md): boot = open_store's typed outcome,
+checkpoint after every cycle, so a restart MANAGEs from a checkpoint; a second process is refused; a store HOLD runs
+nothing; a checkpoint the store cannot write is hard HOLD in-process. Fake venue, tmp dirs, no network."""
+import os
+
+import pytest
+
+from newcore.runner import app as A
+from newcore.runner import config as C
+from newcore.store import DurabilityUnavailable, Outcome
+from test_run_cli import cfg_file, run
+
+
+def test_a_restart_mid_trade_manages_from_the_last_checkpoint(tmp_path):
+    p = cfg_file(tmp_path)
+    assert run(['run', '--config', p, '--cycles', '27', '--enable-candidate'])[0] == 0          # mid-trade
+    s = A.Session(C.load(p), True)
+    try:
+        assert s.boot.outcome is Outcome.MANAGE and s.runner.hard_hold is None
+        assert s.journal.last_sequence() > 0 and not s.store.checkpoint_due
+        assert 'checkpoint' not in s.store.writes                 # covered by the cycle checkpoint, not the boot fold
+        assert s.store.current.lsn_upto == s.journal.last_sequence()
+    finally:
+        s.close()
+
+
+def test_a_second_process_on_the_same_account_is_refused_with_exit_3(tmp_path):
+    p = cfg_file(tmp_path)
+    s = A.Session(C.load(p), True)
+    try:
+        code, out = run(['run', '--config', p, '--once', '--enable-candidate'])
+        assert code == A.EXIT_STORE_DOWN and 'locked' in out
+    finally:
+        s.close()
+    assert run(['run', '--config', p, '--once', '--enable-candidate'])[0] == 0                 # lock released
+
+
+def test_a_store_hold_runs_nothing_and_exits_4(tmp_path):
+    p = cfg_file(tmp_path)
+    assert run(['run', '--config', p, '--cycles', '27', '--enable-candidate'])[0] == 0
+    acct = C.load(p).account_id
+    os.remove(tmp_path / 'nc' / acct / A.STATE_FILE)            # the fake exchange restarts flat: no longer matches
+    before = sorted(os.listdir(tmp_path / 'nc' / 'reports'))
+    code, out = run(['run', '--config', p, '--once', '--enable-candidate'])
+    assert code == A.EXIT_STORE_HOLD and 'promote()' in out and 'HEALTH' not in out
+    assert sorted(os.listdir(tmp_path / 'nc' / 'reports')) == before
+
+
+def test_a_checkpoint_the_store_cannot_write_is_hard_hold(tmp_path, monkeypatch):
+    s = A.Session(C.load(cfg_file(tmp_path)), True)
+    try:
+        def boom(portfolio, now_ms):
+            raise DurabilityUnavailable('snapshot create', None)
+        monkeypatch.setattr(type(s.store), 'checkpoint_due', property(lambda self: True))
+        monkeypatch.setattr(s.store, 'checkpoint', boom)
+        s.cycle(s.next_close())
+        s.checkpoint()
+        assert s.runner.hard_hold is not None                     # A21: only the A23 / A24 emergency set from here
+        s.checkpoint()                                            # and no further checkpoint is attempted
+    finally:
+        s.close()
+
+
+def test_the_store_layout(tmp_path):
+    p = cfg_file(tmp_path)
+    assert run(['run', '--config', p, '--once', '--enable-candidate'])[0] == 0
+    acct = C.load(p).account_id
+    store = tmp_path / 'nc' / 'data' / 'accounts' / acct
+    assert {'journal', 'snap', 'HEAD.a'} <= set(os.listdir(store))
+    assert (tmp_path / 'nc' / 'anchors').is_dir() and not (tmp_path / 'nc' / acct / 'journal').exists()
```
