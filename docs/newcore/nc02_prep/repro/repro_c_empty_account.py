"""REC-01 repro (c): start-up paths that end with the bot managing NOTHING while the (fake) exchange holds positions/stops.
(Also reproduced inside a2, a7, b3, b4, b7.)"""
import os, shutil
import common as C

TS, E = C.setup()


def rm(*names):
    def f(tmp):
        for n in names:
            p = os.path.join(tmp, n)
            if os.path.exists(p): os.remove(p)
    return f


def case(title, mutate, fresh_dir=False):
    print(f'\n=== {title}')
    e0, tmp, fake = C.build(TS, E)
    if fresh_dir:
        tmp = os.path.join(os.path.dirname(tmp), os.path.basename(tmp) + '_new'); os.makedirs(tmp)
    else:
        mutate(tmp)
    before = C.snap(tmp)
    e = C.restart(E, tmp, fake)
    print(' file hashes before -> after start-up:'); print(C.diff(before, C.snap(tmp)) or '  (no files)')
    C.posture(E, e, 'start')
    C.reconcile_twice(e)
    C.try_new_risk(TS, E, e, 'resume')


case('c1 state.json + state.json.bak deleted (install.json kept)', rm('state.json', 'state.json.bak'))
case('c2 state + install markers deleted, versioned settings kept', rm('state.json', 'state.json.bak', 'install.json',
                                                                     'install.json.bak'))
case('c3 whole data folder replaced by an EMPTY folder (reinstall / LOCALAPPDATA moved), same API key', None, fresh_dir=True)
case('c4 everything deleted except an unversioned (v0) settings.json',
     lambda t: (rm('state.json', 'state.json.bak', 'install.json', 'install.json.bak', 'settings.json.bak')(t),
                C.rewrite(os.path.join(t, 'settings.json'), lambda d: d.pop('schema_version', None))))
E.close_fill_writers(2.0)
