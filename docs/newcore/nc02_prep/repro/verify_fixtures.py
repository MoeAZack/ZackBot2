"""Check the frozen fixture pack: every fixture's input bytes match input_files, its contract hash matches its contract
version (v1 = NF-01..21, v2 = NF-22+), and MANIFEST lists exactly the fixtures on disk. Read-only; stdlib only.

    python verify_fixtures.py [fixtures_dir]"""
import hashlib, json, os, sys

D = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'fixtures'))
man = json.load(open(os.path.join(D, 'MANIFEST.json')))
fields = man.get('contract_fields', {'v1': ['id', 'reader_build', 'input_files', 'exchange', 'nc02_required']})
bad = []
on_disk = sorted(n for n in os.listdir(D) if n.startswith('NF-'))
if on_disk != sorted(man['fixtures']): bad.append(f'MANIFEST/disk mismatch: {sorted(set(on_disk) ^ set(man["fixtures"]))}')
for fid in on_disk:
    fx = json.load(open(os.path.join(D, fid, 'fixture.json')))
    ver = man.get('contract_version', {}).get(fid, 'v1')
    c = {k: fx[k] for k in fields[ver]}
    h = hashlib.sha256(json.dumps(c, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if h != fx['contract_sha'] or h != man['fixtures'].get(fid): bad.append(f'{fid}: contract hash mismatch ({ver})')
    inp = os.path.join(D, fid, 'input')
    present = sorted(n for n in os.listdir(inp) if n != '.gitkeep') if os.path.isdir(inp) else []
    if present != sorted(fx['input_files']): bad.append(f'{fid}: input files {present} != contract {sorted(fx["input_files"])}')
    for n, want in fx['input_files'].items():
        p = os.path.join(inp, n)
        if os.path.isfile(p) and hashlib.sha256(open(p, 'rb').read()).hexdigest() != want: bad.append(f'{fid}: {n} bytes changed')
print('\n'.join(bad) or f'OK: {len(on_disk)} fixtures, contracts and input bytes match MANIFEST')
sys.exit(1 if bad else 0)
