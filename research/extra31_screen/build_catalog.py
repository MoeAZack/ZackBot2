"""Write catalog.json: every strategy with runnable code, from the four frozen source commits in contract.json.
Lists only (name, timeframe, side, source); it runs no strategy on any data.  python research/extra31_screen/build_catalog.py"""
import sys, os, json, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_screen as RS

CODE = r'''
import sys, os, json, re
src, mods = sys.argv[1], [m for m in sys.argv[2].split(',') if m]
sys.path[:0] = [src, os.path.join(src, 'research')]
import strategies as S
base = set(S.STRATEGIES)
for m in mods: __import__(m).register()
keys = [k for k in S.STRATEGIES if k not in base] if mods else list(S.STRATEGIES)
out = []
for k in keys:
    st = S.STRATEGIES[k]
    c = re.search(r'Candidate (\d+)', st.get('desc') or '')
    out.append(dict(key=k, name=st.get('name'), tf=st.get('tf') or '4h', sides=st.get('sides'), candidate=('C%s' % c.group(1)) if c else None,
                    mgmt=st.get('mgmt'), params=st.get('params') or {}, desc=(st.get('desc') or '')[:240]))
print(json.dumps(out, default=str))
'''


def main():
    rows, seen = [], set()
    with tempfile.TemporaryDirectory() as tmp:
        for name, spec in RS.CONTRACT['sources'].items():
            src = RS.export(name, tmp)
            got = json.loads(subprocess.run([sys.executable, '-c', CODE, src, ','.join(spec['modules'])], check=True,
                                            capture_output=True, text=True).stdout)
            for r in got:
                if r['key'] in seen: continue            # master wins for the keys c254 shares with it
                seen.add(r['key'])
                r.update(source=name, commit=spec['commit'],
                         plan=('SKIPPED_ALREADY_TESTED' if r['key'] in RS.SKIP else 'NO_DATA_15M' if r['tf'] == '15m' else 'RUN'))
                rows.append(r)
    json.dump(dict(contract='EXTRA31-SCREEN', count=len(rows), strategies=rows),
              open(os.path.join(HERE, 'catalog.json'), 'w', encoding='utf-8'), indent=1)
    by = {}
    for r in rows: by[(r['source'], r['plan'])] = by.get((r['source'], r['plan']), 0) + 1
    print(len(rows), by)


if __name__ == '__main__':
    main()
