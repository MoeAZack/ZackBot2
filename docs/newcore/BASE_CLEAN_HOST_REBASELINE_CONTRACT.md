# BASE-CLEAN-PROOF-01 current-host rebaseline contract

Status: frozen offline evidence-collector slice. Base:
`c6cc7d304ab98088bd18ff08bf7bf707f4eac886`. This slice prepares the
`current_host_codec_baseline` proof; it does not run the one-hour evidence job
as part of implementation or review.

## Roadmap fit and interface

This is the smallest proof slice after Checkpoint C BASE-CLEAN. It removes only
`host_codec_rebaseline_missing` when an independently accepted exact-head
artifact is supplied. The remaining golden-adapter, real-TNET, and two-account
proofs stay HOLD.

The collector module has three small interfaces:

```text
collect_current_host(expected_head, expected_host_id) -> HostArtifact
encode_artifact / decode_artifact -> canonical verified bytes / HostArtifact
gate_evidence(HostArtifact) -> HostEvidenceBundle
```

`HostEvidenceBundle` contains exactly one `GateProof`, `LatencyEvidence`, and
`MemoryEvidence`. All three name the same exact head and privacy-safe host ID
and must carry the same accepted artifact SHA-256. BASE-CLEAN rejects a missing
row or any digest split. The digest is SHA-256 over the canonical artifact body;
it is not a caller assertion.

## Exact run identity

The evidence run is valid only when both pre-run and post-run checks prove:

- the checkout HEAD equals the caller-pinned 40-character SHA;
- tracked files are clean (ignored `dev_out/` output does not alter the tree);
- the derived host ID equals the caller-pinned `host_<32 lowercase hex>` ID;
- the interpreter is exactly Python `3.12.10`.

The host ID hashes the local node, machine, and Windows platform identities; the
raw values never enter the artifact. Wrong, malformed, changed, or unavailable
identity evidence refuses collection. A later code head requires a new run.

## Frozen workloads and measurements

The codec workload is the largest canonical record in
`tests/newcore/fixtures/nc01_samples_v1.jsonl`; the artifact binds the SHA-256
of that entire fixture. Validation reconstructs the frozen record, encoding
uses `newcore.domain.dumps`, and decoding uses `newcore.domain.loads`.

The scheduler workload is a one-account, in-memory NC-05 probe using the real
`Scheduler` interface and inert hooks/worker. Measurements use
`perf_counter_ns`, warm up first, and record canonical finite non-negative
Decimals:

| Field | Aggregation | Ceiling |
| --- | --- | ---: |
| `validate_us` | median of 5 x 200 calls | 1,000 us |
| `encode_us` | median of 5 x 40 calls | 6,750 us |
| `decode_us` | median of 5 x 20 calls | 23,400 us |
| `submit_ms` | median of 5 x 10 calls | 10 ms |
| `step_ms` | median of 25 owner steps | 50 ms |
| `snapshot_mean_ms` | median of 5 x 10,000 reads | 1 ms |
| `publish_ms` | maximum of the same 25 step-to-publication calls | 250 ms |

The memory phase repeatedly exercises codec validation/encode/decode plus
scheduler snapshot/step for at least 3,600,000 monotonic milliseconds. Windows
`QueryWorkingSet` supplies resident pages and the collector counts only pages
whose PSAPI shared bit is clear. Peak private working set must be
at most 512 MiB and peak-minus-start growth at most 32 MiB. These remain
provisional evidence requirements, not general performance claims.

## Closed outcomes and canonical artifact

Only a complete, under-ceiling run can build an accepted artifact. Partial
duration, interruption, dirty or changed head, wrong host/Python, malformed or
noncanonical data, duplicate JSON fields, altered body/digest, unavailable
memory evidence, or a latency/memory ceiling breach raises one stable
`CollectorError` code. Raw exception text is never retained across the
collector interface. Interrupted collection produces no accepted artifact.

The output is canonical ASCII JSON with exact keys, sorted compact encoding,
canonical decimal strings, and schema `zackbot-base-clean-host-rebaseline/1`.
The command writes once, without overwrite, to:

```text
dev_out/base_clean/host_rebaseline_<exact-head>.json
```

Host identification is read-only:

```text
python -m tools.base_clean_host_rebaseline --print-host-id
```

The later evidence run uses:

```text
python -m tools.base_clean_host_rebaseline \
  --expected-head <exact-head> --expected-host-id <host-id>
```

## Acceptance matrix

Focused tests cover canonical round trips, exact digest derivation and
three-record binding; every latency and memory ceiling; wrong head, host,
Python, dirty tree and partial duration; altered digest/body; duplicate,
wrong-type and noncanonical JSON; interruption with suppressed exception
chaining; immutable records; and absence of network/credential imports.

After the collector head is independently accepted, run the one-hour collector
on that unchanged exact head. Independent review verifies the artifact hash,
reconstructs the three BASE-CLEAN records, and confirms the resulting gate has
exactly the other three missing-proof HOLDs.

## Safety boundary and deferred work

The collector reads local git identity, the frozen fixture, monotonic time and
its own Windows process memory; it writes only the ignored artifact. It has no
network, exchange, credentials, service/runtime, PAPER, installer, UI, deletion
or mainnet behavior. The historical `tests/newcore/perf_baseline_v1.json` is
preserved unchanged.

Golden adapters/comparison, real Binance Testnet lifecycle execution,
two-account isolation, evidence publication, runtime wiring, PAPER activation,
source cleanup and every mainnet action remain deferred.
