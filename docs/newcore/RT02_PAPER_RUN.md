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
throwaway keys. With #294 merged in a scratch worktree, all 14 pass. On this branch alone, the 9 that need TNET-02
are skipped and say so.

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

- **Testnet run.** The combined classic and algo open-order read is Codex's PR #291
  (`codex/tnet02-real-adapter`), so this branch does not touch the adapter. After #291 lands, a testnet port builder
  is still missing. It needs:
  - the sender;
  - credentials from the owner's machine (never from the repo);
  - a typed-confirmation testnet binding.

  Until that exists, `run` refuses a testnet config.
- **Entries in PAPER.** `run_paper(..., entries_factory=f)` accepts `f(spec, *, wall_clock) -> EntrySource` from a
  launcher outside `newcore/runtime`. An example is the strategy source in PR #297. Without a factory, `run` refuses
  `entries_enabled` (`entries_source_missing`). The CLI passes no factory.
  - A factory that also takes `journal` gets a callable that returns a read-only `JournalView` of the account's
    live journal. The view has `read`, `state`, `last_sequence` and `account_id`, and no `append` or `close`.
  - The callable returns `None` until the runtime has booted.
  - It is read from the cycle hook on the account's own runner thread, so no second reader is needed and the
    writer lock is not involved.
- **Marks feed.** The marks come from a file. A live public mark feed is not wired.
