# CORE-10 fixr packets (gzip -9 -n of the local run packets)
After `gunzip`, each file must match the sha256 in `../packet_digests.sha256` (fixr/CORE-10_*). They contain per-trade rows from the dev window only (all before 2026-09-28). Data only; the code head stays at 2380e81.
