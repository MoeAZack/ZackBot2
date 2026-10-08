"""REC-01 repro (d): rollback / roll-forward between builds on the same data folder (TEMP folders only).
OLD = b8c11c8 (last master before AUD-05, i.e. what an installer rollback across AUD-05 would restore); NEW = cff3f88.
A 'future' NEW+1 build is simulated by stamping schema_version=2 (what a v2 writer would leave)."""
import os, subprocess, sys, tempfile
import common as C

PY = sys.executable
W = os.path.join(C.HERE, 'rollback_worker.py')


def run(src, data, fakef, action):
    r = subprocess.run([PY, W, src, data, fakef, action], capture_output=True, text=True, cwd=C.HERE)
    out = r.stdout.rstrip()
    print(out if out else f'  worker rc={r.returncode}\n' + r.stderr[-1500:])


def scen(title, steps, mutate_between=None):
    print(f'\n=== {title}')
    root = tempfile.mkdtemp(prefix='rec01_d_'); data = os.path.join(root, 'data'); os.makedirs(data)
    fakef = os.path.join(root, 'fake.json')
    for i, (src, action) in enumerate(steps):
        if mutate_between and i in mutate_between:
            mutate_between[i](data); print(f'  (files mutated: {mutate_between[i].__doc__})')
        before = C.snap(data)
        run(src, data, fakef, action)
        if action == 'load': print(C.diff(before, C.snap(data)))


def stamp_v2(data):
    """state.json + state.json.bak + settings.json stamped schema_version=2 (a NEW+1 writer)"""
    for n in ('state.json', 'state.json.bak', 'settings.json'):
        p = os.path.join(data, n)
        if os.path.exists(p): C.rewrite(p, lambda d: d.update(schema_version=2))


def trunc_state(data):
    """state.json truncated to 40 bytes"""
    p = os.path.join(data, 'state.json')
    with open(p, 'rb') as f: raw = f.read()
    with open(p, 'wb') as f: f.write(raw[:40])


scen('d1 NEW writes -> OLD build reads (installer rollback across AUD-05)', [('src', 'init'), ('old_src', 'load')])
scen('d2 OLD writes (unversioned, no marker) -> NEW reads (upgrade)', [('old_src', 'init'), ('src', 'load')])
scen('d3 NEW -> OLD -> NEW round trip', [('src', 'init'), ('old_src', 'load'), ('src', 'load')])
scen('d4 NEW+1 (v2) files -> OLD build reads', [('src', 'init'), ('old_src', 'load')], {1: stamp_v2})
scen('d5 NEW+1 (v2) files -> NEW build reads (rollback of a schema bump)', [('src', 'init'), ('src', 'load')], {1: stamp_v2})
scen('d6 damaged current state -> OLD build reads', [('src', 'init'), ('old_src', 'load')], {1: trunc_state})
