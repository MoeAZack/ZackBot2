# D01 v3 input receipt
- Runner: research/h5wide_d01/run_d01_v3.py sha256 2c3985688956daa97719d369592e235300b87760f1e3a095f09070369221f84a, frozen at head 90080a1b (tests 15/15, mutation-checked) before any v3 outcome.
- Canonical data: C:\Dev\ZackBot_research_cache\h5_wide_d01_20261112\clean, input_manifest.json sha256 99af9bfb4a9b29ede80f8f3ff54541912d74d03b3b93d984432832c934bdfa95 (source manifest bc15c895, prereg 622635c7).
- All 40 CSV sha256 checked against the manifest on the owner PC and again after staging: 0 mismatches. Per-file hashes are in d01v3_result.json `input_sha256`.
- Command: python research/h5wide_d01/run_d01_v3.py <clean dir> docs/research/h5wide_d01/v3 ; exclusions: none.
- v1 (c1de983a) outputs stay WITHDRAWN; v2 (9a236b23) never produced outcomes.
