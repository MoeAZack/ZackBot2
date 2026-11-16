# Account 1 executable comparison

Observation: 16 Nov 2026, 17:58 Cairo (Africa/Cairo). The executable digest is
recorded in the manifest. A read-only PyInstaller CArchive/PYZ inspection found
that its bytecode magic matches the installed Python 3.14.6 compiler.

For each embedded entry with a corresponding loose-source file, the investigator
compiled that file without executing it, recursively normalized `co_filename`,
and compared marshaled code objects at optimization levels 0, 1 and 2. This
included the CArchive `app` entry. **45/45 comparable entries matched** the loose
source, with zero differences/errors. Against the lane's unmodified baseline
source, 36/44 entries matched; eight differed: backtest, binance_client, engine,
lab, strategies, strategy_shadow, telegram_ctl and app. The extra loose-source
entry is generated build_info.

This establishes compiled-code equivalence for those 45 entries under the tested
compiler, after filename normalization. It is stronger than a directory-name or
timestamp match. It does not establish identical original source bytes, coverage
of every archive entry/resource/dependency, a reproducible build, install history
or the currently loaded process's identity. No executable/runtime module was run
or imported; only standard compiler and archive-reader operations were used.

The build metadata's claimed source commit was subsequently fetched directly
from this repository's GitHub origin into the lane's own clone. **96/96 included
account-1 original source files are byte-identical to that historical revision**;
generated build_info is absent from the revision. This comparison uses recorded
original source hashes before redaction, not the published placeholders. The
omitted header source remains outside this 96-file claim. The loose source's Git
pointer does not resolve, so it is not itself evidence. No other clones were
searched. `provenance.json` records the revision, identity fields and module lists;
the independently written comparison script is retained as `compare_archive.py`.
It requires the matching installed Python 3.14 interpreter and PyInstaller; the
script refuses a bytecode-magic mismatch. It writes its report in codex-inbox,
not in the runtime. Historical Git verification can be repeated offline with
`python runtime_snapshots/verify.py --provenance` while the fetched object remains
in this clone.
The original read-only report is retained locally in the owner's codex-inbox.

Redactions in the published source alter some files. The 45-entry comparison was
against the original read-only loose source, not the redacted repository copies.
Together the archive and historical-revision comparisons establish a bounded
source-to-code chain for the compared entries. They do not prove the build
command, dependency/resource identity or current process memory.
