"""REC-01 repro (a): a FUTURE schema_version at start-up. Direction: state AND backup must stay byte-identical, start aborts."""
import os
import common as C

TS, E = C.setup()
print(f'build schema: STATE={E.STATE_SCHEMA} SETTINGS={E.SETTINGS_SCHEMA} INSTALL={E.INSTALL_SCHEMA}')


def case(title, mutate, after=None):
    print(f'\n=== {title}')
    e0, tmp, fake = C.build(TS, E)
    mutate(tmp)
    before = C.snap(tmp)
    e = C.restart(E, tmp, fake)
    print(' start-up returned an Engine (no abort)')
    print(' file hashes before -> after start-up:'); print(C.diff(before, C.snap(tmp)))
    for n in ('state.json', 'state.json.bak', 'settings.json', 'settings.json.bak', 'install.json'):
        p = os.path.join(tmp, n)
        if os.path.exists(p): print(f'  {n}: schema_version on disk now = {C.ver(p)}')
    C.posture(E, e, 'start')
    if after: after(e, tmp, before)
    return e


def fut(name, v):
    return lambda tmp: C.rewrite(os.path.join(tmp, name), lambda d: d.update(schema_version=v))


def two_more_saves(e, tmp, before):
    mid = C.snap(tmp)
    e.save_state(); e.save_state(); e.save_settings(); e.save_settings()
    print(' after 2 more state + settings saves (normal cycles):'); print(C.diff(before, C.snap(tmp)))


def recon(e, tmp, before):
    C.reconcile_twice(e); C.try_new_risk(TS, E, e, 'resume')


case('a1 state.json schema_version=99, state.json.bak current', fut('state.json', 99), two_more_saves)
case('a2 state.json AND state.json.bak schema_version=2 (Codex reproduction)',
     lambda t: (fut('state.json', 2)(t), fut('state.json.bak', 2)(t)), recon)
case('a3 settings.json schema_version=2, settings.json.bak current', fut('settings.json', 2), two_more_saves)
case('a4 settings.json AND settings.json.bak schema_version=2',
     lambda t: (fut('settings.json', 2)(t), fut('settings.json.bak', 2)(t)), two_more_saves)
case('a5 install.json schema_version=2 (install.json.bak current)', fut('install.json', 2))
case('a6 only state.json.bak future (99), main current', fut('state.json.bak', 99), two_more_saves)
E.close_fill_writers(2.0)


def restart_again(save_first):
    def f(e, tmp, before):
        if save_first: e.save_state(); print(' one normal state save (a cycle) ...')
        print(' ... then the process restarts:')
        b2 = C.snap(tmp)
        e2 = C.restart(E, tmp, e.trade)
        print(C.diff(b2, C.snap(tmp)))
        C.posture(E, e2, 'restart#2')
    return f


case('a7 = a2, then one cycle saves state, then restart (is the failed-closed condition durable?)',
     lambda t: (fut('state.json', 2)(t), fut('state.json.bak', 2)(t)), restart_again(True))
case('a8 = a5 (future install.json), then plain restart (does the account-confirmation requirement survive?)',
     fut('install.json', 2), restart_again(False))
E.close_fill_writers(2.0)
