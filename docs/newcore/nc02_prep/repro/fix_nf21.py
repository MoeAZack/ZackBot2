"""Replace NF-21's placeholder inputs with real pre-AUD-05 (b8c11c8) output and re-hash its contract."""
import hashlib, json, os, shutil, subprocess, sys, tempfile
import make_fixtures_hash as H
HERE = os.path.dirname(os.path.abspath(__file__))
root = tempfile.mkdtemp(prefix='rec01_nf21_'); data = os.path.join(root, 'data'); os.makedirs(data)
fakef = os.path.join(root, 'fake.json')
subprocess.run([sys.executable, os.path.join(HERE, 'rollback_worker.py'), 'old_src', data, fakef, 'init'], check=True, cwd=HERE)
d = os.path.join(HERE, 'fixtures', 'NF-21'); shutil.rmtree(os.path.join(d, 'input')); os.makedirs(os.path.join(d, 'input'))
files = {}
for n in sorted(os.listdir(data)):
    if n.startswith(('state.json', 'settings.json', 'install.json')):
        shutil.copyfile(os.path.join(data, n), os.path.join(d, 'input', n))
        files[n] = hashlib.sha256(open(os.path.join(data, n), 'rb').read()).hexdigest()
fx = json.load(open(os.path.join(d, 'fixture.json')))
fk = json.load(open(fakef))
fx['input_files'] = files
fx['exchange'] = dict(positions=[dict(symbol=s, side=sd, qty=q) for s, sd, q in fk['pos'] if q > 1e-12],
                      protective_orders=[dict(tag=k, symbol=v[0], side=v[1], qty=v[2], stop=v[3]) for k, v in fk['stops'].items()])
fx['legacy_observed']['writer_build'] = 'b8c11c8'; fx.pop('note', None)
fx['contract_sha'] = H.contract_sha(fx)
json.dump(fx, open(os.path.join(d, 'fixture.json'), 'w'), indent=2)
m = json.load(open(os.path.join(HERE, 'fixtures', 'MANIFEST.json'))); m['fixtures']['NF-21'] = fx['contract_sha']
json.dump(m, open(os.path.join(HERE, 'fixtures', 'MANIFEST.json'), 'w'), indent=2)
print('NF-21 inputs:', files)
