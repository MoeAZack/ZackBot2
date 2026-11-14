"""Require a real unmodified buildcheck/self-test before publishing acceptance."""
import hashlib,json,re,subprocess
from pathlib import Path
ROOT=Path(r'F:\ZackBot2_data\WINPATH-01-REVIEW-20261114-evidence\final_615e14b3')
REPO=Path(r'F:\ZackBot2_data\WINPATH-01-REVIEW-20261114')
DEST=Path(r'F:\ZackBot2_data\C1H-20261112\review-branch\docs\research\codex_winpath_review_20261114')
HEAD='615e14b394400a2bbe188d3b4d7dee5991e7e1d6'
receipt=json.loads((ROOT/'receipt.json').read_text(encoding='utf-8'))
assert len(receipt)==2 and all('finished_cairo' in x and x['exit_code']==0 and not x['timed_out'] and x['clean_at_end'] for x in receipt)
row=next(x for x in receipt if x['kind']=='buildcheck')
internal=Path(row['localappdata'])/'ZackBot/build_check.log'
text=internal.read_text(encoding='utf-8-sig',errors='replace')
outer=(ROOT/'buildcheck.log').read_text(encoding='utf-8',errors='replace')
matches=re.findall(r'BUILDCHECK_OK build=(\S+) sha256=([0-9a-fA-F]{64}) exe=(.+)',text)
assert matches and 'BUILDCHECK_OK' in outer
build,exe_sha,exe=matches[-1]
selftests=[json.loads(l.split('selftest: ',1)[1]) for l in text.splitlines() if l.startswith('selftest: ')]
assert selftests and selftests[-1].get('ok') is True and selftests[-1].get('build')==build
assert Path(exe).resolve().is_relative_to(Path(row['localappdata']).resolve())
summaries=[l.strip().strip('= ').strip() for l in text.splitlines() if re.search(r'\d+ passed\b',l) and re.search(r' in \d+(?:\.\d+)?s',l)]
actual=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
dirty=subprocess.check_output(['git','status','--porcelain'],cwd=REPO,text=True).strip()
assert actual==HEAD and not dirty
proof={'head':HEAD,'mode':'buildcheck','passed':True,'build':build,'exe_sha256':exe_sha.lower(),
       'exe_path':exe,'exe_retained_after_cleanup':Path(exe).exists(),'selftest':selftests[-1],
       'safety_test_summary':summaries[-1] if summaries else None,'receipt':row,
       'internal_log_sha256':hashlib.sha256(internal.read_bytes()).hexdigest(),
       'outer_log_sha256':hashlib.sha256((ROOT/'buildcheck.log').read_bytes()).hexdigest(),
       'source_clean_at_end':True,'no_install_or_account_action':True}
def put(name,data):(DEST/name).write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8',newline='\n')
put('buildcheck.json',proof);put('full_run_receipt.json',receipt)
for n in ('publish_winpath_final.py',):
    (DEST/n).write_bytes(Path(__file__).with_name(n).read_bytes().replace(b'\r\n',b'\n'))
report=f'''# Final independent WINPATH-01 acceptance

**Windows full suite PASS; real Windows buildcheck PASS** at exact head `{HEAD}`.

- Full native Windows: 11149 passed, 0 failed, 0 errors, 41 skipped, 32 expected failures (11222 unique cases). Normal C: temp/F: checkout; isolated app-data; six workers; no drive-simulation plugin.
- Pinned tested CLOCK-1021 baseline 6e259ea5 (tree-identical to master 3d8a87a4): 11095 passed / 22 failed / 0 errors. Original pinned 9cb4341f: 11056 passed / the same 22 failed / 0 errors. All 22 failed IDs fixed; no new failed/errored IDs; no removed baseline test IDs; 32 new path/identity cases. Comparison and raw artifact hashes are recorded separately.
- Unmodified `build_app.bat buildcheck`, private app-data and 14400-second deadline: exit 0, no timeout, {row['seconds']} s; completed {row['finished_cairo']} Africa/Cairo.
- Real executable self-test: `ok=true`, build `{build}`, matching the installer's new build ID. Native BUILDCHECK_OK was present in both logs. Executable SHA-256: `{exe_sha.lower()}`.
- Staged safety tests: {proof['safety_test_summary']}.

The executable was built and tested only in the private buildcheck tree. No installation, account, key, position, runtime or scheduler change. The exact source checkout remains clean. A staged executable may be removed by the normal worktree cleanup; its validated hash and actual self-test remain in the retained build log and this evidence.

Source review: the initial new-helper module-identity omission was corrected in aa7246ae and retained byte-identically in the five WINPATH files at this final merged-base head. Focused native tests passed 216/216; prior Git dirty/commit checks still worked, so no new authorization-bypass claim was made. Broader pre-existing alias/post-preflight issues remain deferred. No protected research records, economic results or strategy rules were changed.

Protected GitHub checks and exact-head match must still be revalidated immediately before any normal merge. These local artifacts are not a branch-protection bypass and do not authorize deployment.
'''
(DEST/'FINAL_REPORT.md').write_text(report,encoding='utf-8',newline='\n')
put('SHA256.json',{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DEST.iterdir()) if p.is_file() and p.name!='SHA256.json'})
print(json.dumps({'head':HEAD,'buildcheck':'PASS','build':build,'selftest_ok':True,'exe_sha256':exe_sha,'seconds':row['seconds'],'safety_tests':proof['safety_test_summary']},indent=2))
