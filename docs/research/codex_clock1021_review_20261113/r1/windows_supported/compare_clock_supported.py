"""Compare completed native Windows pytest reports by test identity, never raw XML order."""
from collections import Counter
import hashlib,json,re
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path(r'F:\ZackBot2_data\CLOCK-1021-REVIEW-20261113\supported_window_20261114')
receipts=json.loads((ROOT/'receipt.json').read_text(encoding='utf-8'))

def read(name):
    row=next(r for r in receipts if r['name']==name and r['kind']=='tests')
    assert 'finished_cairo' in row and not row['timed_out']
    assert row['exit_code'] in (0,1)
    xml=ROOT/(name+'_tests.xml')
    tree=ET.parse(xml).getroot()
    counts=Counter(); ids=Counter(); failures=Counter(); details=[]
    for case in tree.iter('testcase'):
        identity=(case.get('classname',''),case.get('name',''))
        ids[identity]+=1
        bad=False
        for kind in ('failure','error'):
            for item in case.findall(kind):
                counts[kind]+=1;failures[(*identity,kind)]+=1;bad=True
                details.append({'classname':identity[0],'name':identity[1],'kind':kind,'type':item.get('type')})
        skipped=case.find('skipped')
        if not bad:
            if skipped is not None:
                counts['xfailed' if skipped.get('type')=='pytest.xfail' else 'skipped']+=1
            else:counts['passed']+=1
    log=(ROOT/(name+'_tests.log')).read_text(encoding='utf-8',errors='replace')
    match=re.search(r'\d+ workers? \[(\d+) items?\]',log)
    expected=int(match.group(1)) if match else None
    if expected is None:
        # -q suppresses xdist's item-count header; use a collection-only run at the same exact head.
        collected=(ROOT/(name+'_collected.txt')).read_text(encoding='utf-8',errors='replace')
        match=re.search(r'^(\d+) tests? collected\b',collected,re.M)
        expected=int(match.group(1)) if match else None
    complete=(expected==len(ids) and not re.search(r'worker .{0,40}crashed|INTERNALERROR',log))
    return {'receipt':row,'counts':dict(counts),'unique_test_ids':len(ids),
            'collected':expected,'complete':complete,'failures':sorted(details,key=lambda x:(x['classname'],x['name'],x['kind'])),
            'junit_sha256':hashlib.sha256(xml.read_bytes()).hexdigest()},ids,failures

head,hids,hfail=read('head_r1');base,bids,bfail=read('base')
assert head['complete'] and base['complete'],'Native test reports do not prove complete collection coverage'
result={'status':'COMPLETE','head':head,'base':base,
        'head_only_failure_or_error_ids':list((hfail-bfail).elements()),
        'base_only_failure_or_error_ids':list((bfail-hfail).elements()),
        'identical_failure_error_multisets':hfail==bfail,
        'shared_failure_error_occurrences':sum((hfail&bfail).values()),
        'added_test_ids':sorted(set(hids)-set(bids)),'removed_test_ids':sorted(set(bids)-set(hids)),
        'method':'Exact (classname, name, failure/error) multiset comparison. Native complete JUnit plus collected-item coverage; XML order and line endings ignored.'}
(ROOT/'test_comparison.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps({k:result[k] for k in ('status','identical_failure_error_multisets','shared_failure_error_occurrences','head_only_failure_or_error_ids','base_only_failure_or_error_ids')},indent=2))
print('HEAD',head['counts'],'BASE',base['counts'],'ADDED',len(result['added_test_ids']),'REMOVED',len(result['removed_test_ids']))
