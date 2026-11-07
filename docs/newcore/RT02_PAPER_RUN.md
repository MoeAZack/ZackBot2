# RT-02 PAPER run, install, restart and rollback

**State:** built by Claude Projects on the RT-01/RT-02 branch (PR #296). PAPER only. Nothing here reads a key or
opens a socket. Times are Africa/Cairo.

## What runs

`python -m newcore.runtime run` boots the real NEWCORE runtime for every configured account:

- the NC-02 journal on disk;
- NC-04 `AccountContext` and NC-05 runner threads;
- the journal projection (`JournalProjection`) and venue evidence (`VenueEvidence`), reconciled by REC-02;
- the journaling sink (`JournalingActionSink`), which also watches resting stops;
- a `PaperVenue`, an in-memory Binance USD-M hedge-mode book.

Each step it does three things:
- ticks the venue, so stops trigger against the marks file;
- saves the book to `<account_dir>/paper_book.json`, so a restart sees the same positions and stops;
- every `--status-every` steps, prints one JSON status line (`zackbot.newcore.runtime.paper_status.v1`).

The run stops in three cases: `--max-steps` is reached, the `--stop-file` appears, or Ctrl-C. On stop it stops
NC-05 with the configured grace, closes the journals and prints a final `stopped` line with the reason.

It refuses to start:

| Condition | Code |
|---|---|
| A live/mainnet switch variable is present | `live_switch_present` |
| A testnet config | `testnet_run_unavailable` (the testnet open-orders read cannot prove evidence yet) |
| `entries_enabled: true` | `entries_source_missing` (no strategy source is wired) |
| An account directory that does not exist | `account_not_initialised` |
| A marks file that is missing, empty, or has a non-string, non-finite or non-positive price | `marks_*` |
| A corrupt paper book | `paper_book_invalid` |

An account whose journal has no confirmed binding still boots, but REC-02 holds it and nothing trades.

Exit codes:
- `0` when no account is held at the end;
- `3` when an account is held;
- `2` when the run is refused;
- `64` for a usage error.

## First setup (once)

1. Write a runtime config with `"environment": "paper_sim"` (schema `zackbot.newcore.runtime.config.v1`). Each
   account needs `account_id` (`acct_` + 32 hex), `aggregate_id` (`pf_` + 32 hex), `account_dir` and `egress_id`.
2. Write a marks file, for example `{"BTCUSDT": "100000"}`. Prices are strings. It is re-read whenever it changes,
   and its symbols are the watched symbols.
3. Run `python -m newcore.runtime init-paper --config <config>`. This creates each journal and journals a confirmed
   **SIM** binding. It refuses an account that is already bound, so it never rebinds a testnet account to paper.

## Install, start, stop, restart, rollback

Use `tools/newcore_release.py --root <dir>`. On Windows, `newcore_paper.bat <command>` does the same with root
`%LOCALAPPDATA%\ZackBotNewcore`, where it expects `paper_config.json` and `marks.json`.

| Command | What it does |
|---|---|
| `install --source <repo> --config <cfg>` | Copies the `newcore` package (`.py` files only) to `releases/<id>`. The id is the first 12 hex of the SHA-256 of the file manifest. It re-hashes every file, refuses extra files, and requires `check --config` to pass on the new release. Only then does it move `current.json` (atomically) and remember the replaced release as `previous`. A failed install leaves the pointer alone. A running bot is not touched. |
| `start --config <cfg> --marks <marks>` | Re-verifies the current release, then starts `run` from it in the background. Its stop file is `<root>/STOP` and its log is `logs/run-<n>.jsonl`. Refuses while a run is live. |
| `stop` | Creates the stop file and waits for the run's final `stopped` line. |
| `restart` | Stops, then starts with the config and marks recorded in `run.json`. |
| `rollback` | Re-verifies the previous release, swaps current and previous, and restarts if a run was live. |
| `status` | Shows the pointer, whether a run is live, and its last status line. |

## Testnet run (`tools/newcore_testnet_run.py`)

The launcher needs TNET-02 (PR #294, which stacks on #291). It lives outside `newcore/runtime`, because the runtime
package may not import `newcore.tnet02` or the HTTP sender. Each run covers one dedicated testnet account.

1. **Account file.** Make the TNET-02 account file once, with TNET-02's tool. You type `BIND <account_id>
   <key_digest>`. The file never holds keys.
2. **`init`.** Run `python tools/newcore_testnet_run.py init --config <testnet cfg> --account-file <file>`. It
   journals the file's CONFIRMED testnet binding into the account's journal. A second run with the same binding does
   nothing; a run with a different binding is refused.
3. **Keys.** Set `ZACKBOT_TNET02_API_KEY` and `ZACKBOT_TNET02_API_SECRET` on your PC. They are the dedicated testnet
   key and nothing else.
4. **`run`.** Run `python tools/newcore_testnet_run.py run --config <cfg> --account-file <file> --symbols BTCUSDT`.
   It does these steps in order:
   - opens the journal read-only and refuses unless the account file's binding is the journal's last CONFIRMED
     binding (`binding_not_confirmed`, `account_file_not_the_journaled_binding`, `journal_locked`), before any
     request;
   - builds the venue (`make_testnet_venue` over `TestnetHttpSender`);
   - refuses keys whose digest is not the journaled binding, before sending anything;
   - pins the key to its origin;
   - reads the NC-04 limit payloads;
   - drives the same runtime as the PAPER run.

It refuses:
- any live or mainnet switch variable;
- a config that is not testnet;
- more than one account;
- `entries_enabled` without an entries factory (Python API);
- more than 8 symbols.

Tests: `tests/newcore_runtime/test_rt02_testnet_run.py` runs against a scripted testnet sender, with no network and
throwaway keys. With #294 merged in a scratch worktree, all 17 pass (also under the CP1252 shim). On this branch
alone, the ones that need TNET-02 are skipped and say so.

Launcher review (separate agent, runnable repros): 3 confirmed findings, all fixed with regression tests `r1`-`r3`.
F1 (High): the key digest was checked against the account file, not the journal, so a swapped file could bind other
keys. F2 (Medium): signed limit reads were sent before the journaled binding was checked. F3 (Low): `init` crashed
with a raw `JournalLocked` while a runtime held the journal. Mutation check of the four new guard lines: 4 of 4
killed.

## Watchdog and kill switch (`tools/newcore_watchdog.py`)

This tool keeps a long run alive. It wraps any NEWCORE run command that writes JSON status lines and takes
`--stop-file`, such as the testnet launcher or the strategy run in #297. The watchdog appends `--stop-file
<root>/STOP` itself.

```
python tools/newcore_watchdog.py watch --root <dir> [--stall-s 300] [--startup-grace-s 300] [--max-restarts 5] -- <run command ...>
python tools/newcore_watchdog.py kill   --root <dir>
python tools/newcore_watchdog.py status --root <dir>
```

- **Kill switch.** `kill` writes `<root>/STOP` and waits for the run to stop. The runtime stops and closes. It
  never cancels orders, so the protective stops stay on the venue. After a kill, nothing restarts the run.
  `watch` refuses while the stop file exists (`stop_file_present`); `--clear-stop` removes it deliberately.
- **Restart.** A crash (any other exit code, including 1 for an unhandled exception) is restarted after a backoff of
  5 s that doubles up to 300 s. So is a hang, meaning no output for `--stall-s`, or for `--startup-grace-s` before
  the first status line. The hung run is terminated first. At most `--max-restarts` restarts are allowed per hour;
  past that the watchdog gives up (exit 1). Boot recovery and the REC-02 restore proof make a restart safe.
- **Never restarts:**
  - exit 0 (clean stop), 2 (refused), 3 (held) or 64 (usage);
  - a crash whose last status line shows a hold outside the runtime's clearable evidence holds, because unknown
    account state stops for a human (`held_needs_human`).

  A second `watch` against the same account cannot double-run it: the journal's writer lock refuses the second
  runtime (exit 2).
- **Files.** The run's output goes to `logs/run-<n>.jsonl`. Watchdog events go to `watchdog.jsonl`.
  `watchdog.json` is the heartbeat: run number, restarts, steps since the last boot, the last status, and the
  time of the last line. The run inherits the environment unchanged, because the testnet keys come only from it.
  Neither the command line nor any environment value is written to a file.
- Any live or mainnet switch variable is refused.

Tests: `tests/newcore_runtime/test_rt02_watchdog.py` has 17 tests with real child processes, covering crash, hang,
slow start, cap, kill, kill during backoff, sticky hold, inherited environment and no secret in files. They also
pass under the CP1252 shim. Mutation check of the eight policy lines: 8 of 8 killed.

## Tests (non-official, sandbox)

- `tests/newcore_runtime/test_rt02_paper_run.py`:
  - init-paper binds once;
  - the run proves a flat account;
  - the stop file, the refusals and the live switch;
  - marks validation;
  - the paper book round trip.
- `test_rt02_paper_e2e.py::test_paper_restart_with_a_saved_book_reconciles_protected`: after a restart from the
  saved book, the account reconciles PROTECTED. The restored stop still fires and reconciles flat.
- `test_rt02_release.py` uses real subprocesses:
  - install, verify and unchanged;
  - a failing check keeps the pointer;
  - two releases and a rollback;
  - a tampered or extra file is refused;
  - start, stop, restart, a new install not touching the running bot, and rollback while running.

## Not done

- **Testnet run.** `python -m newcore.runtime run` still refuses a testnet config; use the launcher above. It works
  once #291 (combined classic and algo open-order read) and #294 (TNET-02) are merged, and needs the owner's
  testnet keys and account file on the PC.
- **Entries in PAPER.** `run_paper(..., entries_factory=f)` accepts `f(spec, *, wall_clock) -> EntrySource` from a
  launcher outside `newcore/runtime`. An example is the strategy source in PR #297. Without a factory, `run` refuses
  `entries_enabled` (`entries_source_missing`). The CLI passes no factory.
  - A factory that also takes `journal` gets a callable that returns a read-only `JournalView` of the account's
    live journal. The view has `read`, `state`, `last_sequence` and `account_id`, and no `append` or `close`.
  - The callable returns `None` until the runtime has booted.
  - It is read from the cycle hook on the account's own runner thread, so no second reader is needed and the
    writer lock is not involved.
- **Marks feed.** The marks come from a file. A live public mark feed is not wired.
