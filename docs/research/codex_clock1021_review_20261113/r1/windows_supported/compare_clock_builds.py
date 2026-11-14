"""Read completed unmodified buildchecks; record failure rather than waive a gate."""
from collections import Counter
import hashlib,json,re
from pathlib import Path
ROOT=Path(r'F:\ZackBot2_data\CLOCK-1021-REVIEW-20261113\supported_window_20261114')
receipt=json.loads((ROOT/'receipt.json').read_text(encoding='utf-8'))
def read(name):
    row=next(r for r in receipt if r['name']==name and r['kind']=='buildcheck')
    assert 'finished_cairo' in row
    stdout=ROOT/(name+'_buildcheck.log')
    internal=Path(row['localappdata'])/'ZackBot/build_check.log'
    log=internal.read_text(encoding='utf-8-sig',errors='replace') if internal.exists() else ''
    out=stdout.read_text(encoding='utf-8',errors='replace')
    tail=log[log.rfind('short test summary info'):] if 'short test summary info' in log else ''
    ids=[]
    for line in tail.splitlines():
        m=re.match(r'^\s*(FAILED|ERROR)\s+(tests/.*)',line)
        if m:ids.append((m.group(1),m.group(2).split(' - ',1)[0].strip()))
    summaries=[l.strip().strip('= ').strip() for l in log.splitlines()
               if re.search(r'\d+ passed\b',l) and re.search(r' in \d+(?:\.\d+)?s',l)]
    success=(row['exit_code']==0 and not row['timed_out'] and 'BUILDCHECK_OK' in log and 'BUILDCHECK_OK' in out)
    return {'receipt':row,'passed':success,'safety_test_summary':summaries[-1] if summaries else None,
            'safety_failure_error_ids':sorted(ids),'stdout_sha256':hashlib.sha256(stdout.read_bytes()).hexdigest(),
            'internal_log_sha256':hashlib.sha256(internal.read_bytes()).hexdigest() if internal.exists() else None,
            'last_progress':[l for l in out.splitlines() if re.match(r'^\[\d+/\d+\]',l)][-2:]}
head=read('head_r1');base=read('base')
h=Counter(tuple(x) for x in head['safety_failure_error_ids']);b=Counter(tuple(x) for x in base['safety_failure_error_ids'])
result={'head':head,'base':base,'both_passed':head['passed'] and base['passed'],
        'head_only_safety_failure_error_ids':list((h-b).elements()),
        'base_only_safety_failure_error_ids':list((b-h).elements()),
        'identical_reported_safety_failure_error_multisets':h==b,
        'note':'Only exit 0 plus fresh BUILDCHECK_OK in both logs is a buildcheck PASS; identical failures do not prove an executable self-test passed.'}
(ROOT/'build_comparison.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps(result,indent=2))
