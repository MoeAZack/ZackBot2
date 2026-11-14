"""Compare final native WINPATH results to the already completed pinned Windows baseline."""
from collections import Counter
import hashlib,json
from pathlib import Path
import xml.etree.ElementTree as ET
OUT=Path(r'F:\ZackBot2_data\WINPATH-01-REVIEW-20261114-evidence\final_615e14b3')
BASE=Path(r'F:\ZackBot2_data\CLOCK-1021-REVIEW-20261113\supported_window_20261114')
def read(path):
    r=ET.parse(path).getroot();ids=set();bad=Counter();counts=Counter()
    for c in r.iter('testcase'):
        identity=(c.get('classname',''),c.get('name',''));ids.add(identity)
        failed=False
        for kind in ('failure','error'):
            for x in c.findall(kind):bad[(*identity,kind)]+=1;counts[kind]+=1;failed=True
        s=c.find('skipped')
        if not failed:counts['xfailed' if s is not None and s.get('type')=='pytest.xfail' else 'skipped' if s is not None else 'passed']+=1
    return ids,bad,dict(counts)
receipt=json.loads((OUT/'receipt.json').read_text(encoding='utf-8'))
row=next(x for x in receipt if x['kind']=='tests')
assert 'finished_cairo' in row and not row['timed_out'] and row['exit_code'] in (0,1)
h,hb,hc=read(OUT/'tests.xml');b,bb,bc=read(BASE/'head_r1_tests.xml');o,ob,oc=read(BASE/'base_tests.xml')
added=sorted(h-b);removed=sorted(b-h)
assert not removed and len(added)==32 and all(c.endswith('test_xvol_paths') for c,n in added)
result={'status':'COMPLETE','head':'615e14b394400a2bbe188d3b4d7dee5991e7e1d6',
        'tested_baseline':'6e259ea5acf2379fe09bff933d7c5b05cfaa269e',
        'baseline_tree_equivalent_master':'3d8a87a418c248b27403e0927d2fd05a7e5cd689',
        'baseline_note':'The completed native baseline was 6e259ea5; normal squash master 3d8a87a4 has the identical tree, verified at merge. No new baseline run is implied.',
        'head_counts':hc,'baseline_counts':bc,'head_unique_cases':len(h),'baseline_unique_cases':len(b),
        'head_only_failure_error_ids':list((hb-bb).elements()),'fixed_baseline_failure_error_ids':list((bb-hb).elements()),
        'remaining_failure_error_ids':list((hb&bb).elements()),'added_test_ids':added,'removed_test_ids':removed,
        'original_pinned_baseline':'9cb4341f2d94c21434492515b8e85f2b42c148ce',
        'original_pinned_baseline_counts':oc,'original_pinned_baseline_unique_cases':len(o),
        'head_only_failure_error_ids_vs_original':list((hb-ob).elements()),
        'fixed_original_baseline_failure_error_ids':list((ob-hb).elements()),
        'added_test_ids_vs_original':sorted(h-o),'removed_test_ids_vs_original':sorted(o-h),
        'tests_receipt':row,'head_junit_sha256':hashlib.sha256((OUT/'tests.xml').read_bytes()).hexdigest(),
        'baseline_junit_sha256':hashlib.sha256((BASE/'head_r1_tests.xml').read_bytes()).hexdigest()}
(OUT/'comparison.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps({k:result[k] for k in ('status','head_counts','baseline_counts','head_unique_cases','baseline_unique_cases','head_only_failure_error_ids')},indent=2))
print('fixed',len(result['fixed_baseline_failure_error_ids']),'remaining',len(result['remaining_failure_error_ids']),'added',len(added))
