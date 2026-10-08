"""REC-01 repro (b): damage to a CURRENT-schema file. Direction: fail closed, keep the evidence, require reconciliation."""
import os
import common as C

TS, E = C.setup()


def trunc(name, n=40):
    def f(tmp):
        p = os.path.join(tmp, name)
        with open(p, 'rb') as fh: raw = fh.read()
        with open(p, 'wb') as fh: fh.write(raw[:n])
    return f


def bad_lot(name):
    def m(d):
        k = next(iter(d['lots'])); d['lots'][k]['qty'] = 'abc'
    return lambda tmp: C.rewrite(os.path.join(tmp, name), m)


def empty_lots(name):
    return lambda tmp: C.rewrite(os.path.join(tmp, name), lambda d: d.update(lots={}))


def case(title, mutate, probe=True, restart2=False):
    print(f'\n=== {title}')
    e0, tmp, fake = C.build(TS, E)
    mutate(tmp)
    before = C.snap(tmp)
    e = C.restart(E, tmp, fake)
    print(' start-up returned an Engine (no abort)')
    print(' file hashes before -> after start-up:'); print(C.diff(before, C.snap(tmp)))
    C.posture(E, e, 'start')
    if probe:
        C.reconcile_twice(e)
        C.try_new_risk(TS, E, e, 'resume')
    if restart2:
        e.save_state(); b2 = C.snap(tmp)
        e2 = C.restart(E, tmp, fake)
        print(' after one state save + restart:'); print(C.diff(b2, C.snap(tmp)))
        C.posture(E, e2, 'restart#2')


case('b1 state.json truncated (invalid JSON), state.json.bak good', trunc('state.json'))
case('b2 state.json schema-invalid lot (qty="abc"), state.json.bak good', bad_lot('state.json'))
case('b3 state.json AND .bak schema-invalid lot', lambda t: (bad_lot('state.json')(t), bad_lot('state.json.bak')(t)),
     restart2=True)
case('b4 state.json truncated, state.json.bak valid but OLDER and empty (lots={}: saved before the trade)',
     lambda t: (trunc('state.json')(t), empty_lots('state.json.bak')(t)))
case('b5 settings.json truncated, .bak good', trunc('settings.json'), probe=False)
case('b6 settings.json AND .bak truncated', lambda t: (trunc('settings.json')(t), trunc('settings.json.bak')(t)), probe=False)


def unreadable(tmp):
    real = E.read_json_retry
    def rj(path, kind=None):
        if os.path.basename(path) == 'state.json': raise PermissionError(13, 'sharing violation (injected)')
        return real(path, kind)
    E.read_json_retry = rj
    return real


print('\n=== b7 state.json persistently unreadable (PermissionError) at start-up')
e0, tmp, fake = C.build(TS, E)
before = C.snap(tmp)
real = unreadable(tmp)
try:
    e = C.restart(E, tmp, fake)
finally:
    E.read_json_retry = real
print(C.diff(before, C.snap(tmp)))
C.posture(E, e, 'start')
print(f'  save_hold={e._save_hold}  save_state() -> {e.save_state()}')
C.reconcile_twice(e)
E.close_fill_writers(2.0)
