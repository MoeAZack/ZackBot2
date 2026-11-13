"""Compare exact-head Windows full gates without changing either checkout."""
import collections
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(r'F:\ZackBot2_data\CLOCK-1021-REVIEW-20261113')

def read_run(name):
    folder = ROOT / (name + '_full')
    summary_path = folder / 'latest_full.json'
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    xml_path = folder / 'junit_full.xml'
    cases = []
    if xml_path.exists():
        for case in ET.parse(xml_path).getroot().iter('testcase'):
            for kind in ('failure', 'error'):
                if case.find(kind) is not None:
                    cases.append((case.get('classname'), case.get('name'), kind))
    return {
        'commit': summary.get('git', summary.get('provenance')),
        'started': summary.get('started'), 'finished': summary.get('finished'),
        'passed': summary.get('passed'), 'tests': summary.get('tests'),
        'steps': [{k: s[k] for k in ('name','status','seconds','why','counts') if k in s} for s in summary['steps']],
        'failure_ids': sorted(cases),
        'summary_sha256': hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        'junit_sha256': hashlib.sha256(xml_path.read_bytes()).hexdigest() if xml_path.exists() else None,
        'test_comparison_complete': xml_path.exists() and bool(summary.get('tests', {}).get('tests (all)', {}).get('tests')),
        'host': summary.get('host'), 'dependencies': summary.get('dependencies'),
    }

head, base = read_run('head_r1'), read_run('base')
h = collections.Counter(tuple(x) for x in head['failure_ids'])
b = collections.Counter(tuple(x) for x in base['failure_ids'])
complete = head['test_comparison_complete'] and base['test_comparison_complete']
result = {'head': head, 'base': base, 'head_only': list((h-b).elements()) if complete else None,
          'base_only': list((b-h).elements()) if complete else None,
          'failure_id_multisets_identical': (h == b) if complete else None,
          'comparison_status': 'COMPLETE' if complete else 'INCONCLUSIVE',
          'note': 'An identical failure set does not make a failing full gate PASS. Missing JUnit makes test comparison inconclusive.'}
out = ROOT / 'windows_comparison_r1.json'
out.write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({k: result[k] for k in ('head_only','base_only','failure_id_multisets_identical')},indent=2))
for name, run in [('head',head), ('base',base)]:
    print(name, json.dumps({'tests':run['tests'],'passed':run['passed'],'steps':run['steps']}))
