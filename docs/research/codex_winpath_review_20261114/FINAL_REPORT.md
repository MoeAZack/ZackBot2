# Final independent WINPATH-01 acceptance

**Windows full suite PASS; real Windows buildcheck PASS** at exact head `615e14b394400a2bbe188d3b4d7dee5991e7e1d6`.

- Full native Windows: 11149 passed, 0 failed, 0 errors, 41 skipped, 32 expected failures (11222 unique cases). Normal C: temp/F: checkout; isolated app-data; six workers; no drive-simulation plugin.
- Pinned tested CLOCK-1021 baseline 6e259ea5 (tree-identical to master 3d8a87a4): 11095 passed / 22 failed / 0 errors. Original pinned 9cb4341f: 11056 passed / the same 22 failed / 0 errors. All 22 failed IDs fixed; no new failed/errored IDs; no removed baseline test IDs; 32 new path/identity cases. Comparison and raw artifact hashes are recorded separately.
- Unmodified `build_app.bat buildcheck`, private app-data and 14400-second deadline: exit 0, no timeout, 4277.51 s; completed 2026-11-14T07:55:00.941130+02:00 Africa/Cairo.
- Real executable self-test: `ok=true`, build `20261114-064350`, matching the installer's new build ID. Native BUILDCHECK_OK was present in both logs. Executable SHA-256: `e3a8f13c340590f28a761ff8b93755bc667b2e07a53ac1faa5aee45b499c2eb1`.
- Staged safety tests: 11112 passed, 53 skipped, 44 deselected, 13 xfailed, 4 warnings in 4099.59s (1:08:19).

The executable was built and tested only in the private buildcheck tree. No installation, account, key, position, runtime or scheduler change. The exact source checkout remains clean. A staged executable may be removed by the normal worktree cleanup; its validated hash and actual self-test remain in the retained build log and this evidence.

Source review: the initial new-helper module-identity omission was corrected in aa7246ae and retained byte-identically in the five WINPATH files at this final merged-base head. Focused native tests passed 216/216; prior Git dirty/commit checks still worked, so no new authorization-bypass claim was made. Broader pre-existing alias/post-preflight issues remain deferred. No protected research records, economic results or strategy rules were changed.

Protected GitHub checks and exact-head match must still be revalidated immediately before any normal merge. These local artifacts are not a branch-protection bypass and do not authorize deployment.
