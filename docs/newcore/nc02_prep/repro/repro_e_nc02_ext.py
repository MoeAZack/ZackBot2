"""NC-02 prep (Cowork cross-check follow-up): legacy repros for the cases that had no fixture (NF-22 onward).

Runs the scratch copy of master cff3f88 (./src next to this file) against TEMP data folders only (common.setup redirects
LOCALAPPDATA before anything is imported). For each case it builds an initialized install, mutates the data folder and/or
the fake exchange, FREEZES the exact input bytes it is about to start on (into <stage>/<NF-id>/input), then starts the legacy
engine on them and prints what happened.

    python repro_e_nc02_ext.py <stage_dir>            > out_e.txt

The frozen bytes are what make_fixtures_ext.py turns into fixtures; the legacy behaviour quoted in each fixture.json comes
from the printed log of this same run (out_e.txt), so bytes and observation always belong together.
Never writes anywhere except the TEMP folders it creates and <stage_dir>."""
import errno, hashlib, json, os, shutil, sys, traceback
import common as C

TS, E = C.setup()
STAGE = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else None
STATE = 'state.json'


# ------------------------------------------------------------------ mutations (data folder `t`, fake exchange `x`)
def p(t, n): return os.path.join(t, n)


def raw(t, n):
    with open(p(t, n), 'rb') as f: return f.read()


def put(t, n, b):
    with open(p(t, n), 'wb') as f: f.write(b)


def rm(t, *ns):
    for n in ns:
        if os.path.exists(p(t, n)): os.remove(p(t, n))


def stamp_raw(t, n, literal):
    """Set schema_version to a JSON literal written verbatim (so 1.0 stays a float, "2" a string, null stays null)."""
    doc = json.loads(raw(t, n))
    doc['schema_version'] = '__SV__'
    put(t, n, json.dumps(doc, indent=2, sort_keys=True).replace('"__SV__"', literal).encode())


def stamp(t, n, v): C.rewrite(p(t, n), lambda d: d.update(schema_version=v))
def trunc(t, n, k=40): put(t, n, raw(t, n)[:k])
def nul_all(t, n): put(t, n, b'\x00' * len(raw(t, n)))            # NTFS: size committed, data never flushed
def nul_tail(t, n, k=512): put(t, n, raw(t, n) + b'\x00' * k)       # valid document + zero-filled extension
def lot_key(t, n=STATE): return next(iter(json.loads(raw(t, n))['lots']))


def lot_literal(t, n, field, literal):
    """Write one lot field as a verbatim JSON literal (NaN, 10**400 ...) - json.dump cannot produce these."""
    doc = json.loads(raw(t, n)); k = next(iter(doc['lots']))
    doc['lots'][k][field] = '__LIT__'
    put(t, n, json.dumps(doc, indent=2, sort_keys=True).replace('"__LIT__"', literal).encode())


def set_pending(t, n, pend):
    def m(d): d['lots'][next(iter(d['lots']))]['pending'] = pend
    C.rewrite(p(t, n), m)


def empty_lots(t, n): C.rewrite(p(t, n), lambda d: d.update(lots={}))
def flat(x): x.pos.clear(); x.stops.clear()


def other_account(t):
    for n in ('install.json', 'install.json.bak'):
        C.rewrite(p(t, n), lambda d: d['account'].update(key='0123456789abcdef'))


def old_orders(t):
    """Lots whose last order is long past (a real restart): the legacy 30 s 'recent order' grace does not apply."""
    for n in (STATE, STATE + '.bak'):
        def m(d):
            for l in d['lots'].values(): l['last_order_t'] = 1.0
        C.rewrite(p(t, n), m)


def older_generation(t):
    """File-level rollback (backup tool / cloud sync / copy from another PC): state.json AND .bak replaced by an OLDER valid
    generation from before the last add - the lot held 0.5, the exchange (and its stop) now hold 1.0."""
    for n in (STATE, STATE + '.bak'):
        def m(d):
            for l in d['lots'].values(): l['qty'] = 0.5; l['last_order_t'] = 1.0
        C.rewrite(p(t, n), m)


# ------------------------------------------------------------------ fault injection (legacy writes go through these)
class Inject:
    """Every durable write of the legacy engine (engine._write_durable / _write_bytes_durable) and every rename/replace
    raises OSError(code). Restored on exit. The engine's own module-level `os` is the process os module."""
    def __init__(self, code): self.code = code
    def __enter__(self):
        c = self.code
        def boom(*a, **k): raise OSError(c, os.strerror(c) + ' (injected)')
        self.saved = [(E, '_write_durable', E._write_durable), (E.os, 'rename', os.rename), (E.os, 'replace', os.replace)]
        if hasattr(E, '_write_bytes_durable'): self.saved.append((E, '_write_bytes_durable', E._write_bytes_durable))
        for mod, name, _ in self.saved: setattr(mod, name, boom)
        return self
    def __exit__(self, *exc):
        for mod, name, fn in self.saved: setattr(mod, name, fn)


def unreadable_dir_state(t):
    os.remove(p(t, STATE)); os.makedirs(p(t, STATE))                 # state.json is a DIRECTORY


# ------------------------------------------------------------------ cases (id, cowork ref, title, data mutation, exchange mutation, opts)
CASES = [
 ('NF-22', 'A06', 'future state.json (v99), NO state.json.bak', lambda t: (stamp(t, STATE, 99), rm(t, STATE + '.bak')), None, {}),
 ('NF-23', 'A08', 'state.json schema_version "2" (string), state.json.bak schema_version true (bool)',
  lambda t: (stamp_raw(t, STATE, '"2"'), stamp_raw(t, STATE + '.bak', 'true')), None, {}),
 ('NF-24', 'A08', 'state.json schema_version 1.0 (float), state.json.bak current', lambda t: stamp_raw(t, STATE, '1.0'), None, {}),
 ('NF-25', 'A08', 'state.json schema_version null, state.json.bak schema_version -1',
  lambda t: (stamp_raw(t, STATE, 'null'), stamp_raw(t, STATE + '.bak', '-1')), None, {}),
 ('NF-26', 'B05', 'state.json truncated, NO state.json.bak', lambda t: (trunc(t, STATE), rm(t, STATE + '.bak')), None, {}),
 ('NF-27', 'B06', 'state.json = valid document + 512 NUL bytes, state.json.bak = all NUL (same length)',
  lambda t: (nul_tail(t, STATE), nul_all(t, STATE + '.bak')), None, {}),
 ('NF-28', 'B07', 'state.json all NUL (NTFS zero-fill), NO state.json.bak', lambda t: (nul_all(t, STATE), rm(t, STATE + '.bak')), None, {}),
 ('NF-29', 'B08', 'state.json truncated (damaged), state.json.bak FUTURE (v99)',
  lambda t: (trunc(t, STATE), stamp(t, STATE + '.bak', 99)), None, {}),
 ('NF-30', 'B13', 'state.json has a duplicate "lots" key, the LAST one empty (json.loads keeps the last)',
  lambda t: put(t, STATE, raw(t, STATE).rstrip()[:-1].rstrip() + b',\n  "lots": {}\n}\n'), None, {}),
 ('NF-31', 'B14', 'state.json lot field R = NaN (a field the legacy schema does not validate)',
  lambda t: lot_literal(t, STATE, 'R', 'NaN'), None, {}),
 ('NF-32', 'B15', 'state.json lot qty = 10**400 (integer literal beyond float range)',
  lambda t: lot_literal(t, STATE, 'qty', '1' + '0' * 400), None, {}),
 ('NF-33', 'C01', 'state.json is a DIRECTORY (state.json.bak good)', unreadable_dir_state, None, {'layout': {STATE: 'directory'}}),
 ('NF-34', 'C05', 'state.json truncated, good .bak, and every write/rename in the data folder fails ENOSPC',
  lambda t: trunc(t, STATE), None, {'inject': errno.ENOSPC}),
 ('NF-35', 'C06/C07', 'intact current store, data folder read-only (EROFS on every write/rename)', None, None,
  {'inject': errno.EROFS}),
 ('NF-36', 'D01', 'crash after the temp file fsync, before the replace: newer generation (lot closed) only in '
  'state.json.4242-1111.tmp; state.json/.bak still hold the lot; exchange flat',
  lambda t: (shutil.copyfile(p(t, STATE), p(t, 'state.json.4242-1111.tmp')), empty_lots(t, 'state.json.4242-1111.tmp')),
  flat, {}),
 ('NF-37', 'D02', 'crash mid-write of the temp file: torn state.json.4242-1111.tmp next to an intact current store '
  '(POSITIVE control)', lambda t: put(t, 'state.json.4242-1111.tmp', raw(t, STATE)[:100]), None, {}),
 ('NF-38', 'D03', 'crash after the move-aside, before the replacement is saved: state.json missing, '
  'state.json.corrupt-20261008T000000Z present, good .bak, ENTRIES_PAUSED persisted',
  lambda t: (os.replace(p(t, STATE), p(t, 'state.json.corrupt-20261008T000000Z')), trunc(t, 'state.json.corrupt-20261008T000000Z'),
             C.rewrite(p(t, 'settings.json'), lambda d: d.update(ENTRIES_PAUSED=True))), None, {}),
 ('NF-39', 'D04', 'first run interrupted before install.json: empty v1 state + settings, no marker; exchange holds an '
  'UNKNOWN ETHUSDT SHORT 0.5 with no stop (owner opened it by hand)',
  lambda t: (rm(t, 'install.json', 'install.json.bak', STATE + '.bak', 'settings.json.bak'), empty_lots(t, STATE)),
  lambda x: (flat(x), x.pos.__setitem__(('ETHUSDT', 'SHORT'), 0.5)), {'confirm': True}),
 ('NF-40', 'E01/E05/E10', 'lot.pending written by the legacy SAVE with only a qty (no kind/t) - save accepts what load rejects',
  None, None, {'pending_via_save': dict(qty=0.5)}),
 ('NF-41', 'E02', 'lot.pending close with qty 0 (passes the legacy schema)', None, None,
  {'pending_via_save': dict(kind='close', qty=0.0, px=100.0, why='tp', post={}, t=1.0, cid='zb-c-0000001')}),
 ('NF-42', 'E03', 'lot.pending add with qty 1e300 (finite, passes the legacy schema)', None, None,
  {'pending_via_save': dict(kind='add', qty=1e300, px=100.0, why='dca', post={}, t=1.0, cid='zb-a-0000001')}),
 ('NF-43', 'F05', 'valid state.json with EMPTY lots while state.json.bak holds the lot; exchange FLAT (trivially-empty match)',
  lambda t: empty_lots(t, STATE), flat, {}),
 ('NF-44', 'shape', 'intact reconciled store; exchange ALSO holds an unknown ETHUSDT SHORT 0.5 and a foreign stop o:99',
  None, lambda x: (x.pos.__setitem__(('ETHUSDT', 'SHORT'), 0.5), x.stops.__setitem__('o:99', ('ETHUSDT', 'SHORT', 0.5, 60.0))),
  {'verify_stops': True}),
 ('NF-45', 'match', 'intact store holds BTCUSDT LONG 1.0; exchange holds 0.6 (stop still 1.0) - qty mismatch',
  old_orders, lambda x: x.pos.__setitem__(('BTCUSDT', 'LONG'), 0.6), {'passes': 3}),
 ('NF-46', 'identity', 'intact store whose install.json (+.bak) names ANOTHER account; exchange positions equal the state',
  other_account, None, {'confirm': True}),
 ('NF-47', 'not-found', 'lot.pending close 0.5 with a client id; exchange already holds 0.5 (it filled) but the order '
  'lookup answers bare not-found', old_orders, lambda x: setattr(x, 'get_order', lambda s, cid: None),
  {'pending_via_save': dict(kind='close', qty=0.5, px=101.0, why='tp1', post={}, t=1.0, cid='zb-c-0000047'),
   'exchange_after_save': lambda x: x.pos.__setitem__(('BTCUSDT', 'LONG'), 0.5), 'passes': 3}),
 ('NF-48', 'rollback-attack', 'generation rollback: state.json AND .bak replaced by an OLDER valid generation (lot 0.5) '
  'while the exchange and its stop hold 1.0', older_generation, None, {'passes': 3}),
 ('NF-49', 'legacy-import', 'a complete, intact, reconciled legacy (cff3f88) data folder offered to NEWCORE (legacy import '
  'attempt); exchange =state', None, None, {}),
]


EXCHANGE_WRITES = ('open', 'close', 'stop', 'cancel', 'leverage', 'margin')


def where(ex):
    """file:line of the innermost frame inside the legacy source for an exception."""
    tb = traceback.extract_tb(ex.__traceback__)
    src = [f for f in tb if os.sep + 'src' + os.sep in f.filename] or tb
    f = src[-1]
    return f'{os.path.basename(f.filename)}:{f.lineno} ({f.name})'


def snap_all(d):
    out = {}
    for n in sorted(os.listdir(d)):
        q = os.path.join(d, n)
        if os.path.isdir(q): out[n + '/'] = 'dir'
        elif n.startswith(('state.json', 'settings.json', 'install.json')):
            with open(q, 'rb') as f: out[n] = hashlib.sha256(f.read()).hexdigest()[:12]
    return out


def freeze(fid, tmp, fake):
    if not STAGE: return
    d = os.path.join(STAGE, fid); shutil.rmtree(d, ignore_errors=True); os.makedirs(os.path.join(d, 'input'))
    for n in sorted(os.listdir(tmp)):
        q = os.path.join(tmp, n)
        if os.path.isfile(q) and n.startswith(('state.json', 'settings.json', 'install.json')):
            shutil.copyfile(q, os.path.join(d, 'input', n))
    ex = dict(positions=[dict(symbol=s, side=sd, qty=q) for (s, sd), q in sorted(fake.pos.items()) if q > 1e-12],
              protective_orders=[dict(tag=k, symbol=v[0], side=v[1], qty=v[2], stop=v[3]) for k, v in sorted(fake.stops.items())])
    if getattr(fake, 'get_order', None): ex['order_lookup'] = 'every client id answers bare not-found (no error code, no record)'
    with open(os.path.join(d, 'exchange.json'), 'w') as f: json.dump(ex, f, indent=2)


def run(fid, ref, title, mut, xmut, o):
    print(f'\n=== {fid} [{ref}] {title}')
    e0, tmp, fake = C.build(TS, E)
    if o.get('pending_via_save'):                              # the legacy SAVE path writes the record (E10)
        k = next(iter(e0.state['lots']))
        e0.state['lots'][k]['pending'] = dict(o['pending_via_save'])
        print(f' legacy save_state() with that pending record -> {e0.save_state()!r}; again -> {e0.save_state()!r}')
        if o.get('exchange_after_save'): o['exchange_after_save'](fake)
    if mut: mut(tmp)
    if xmut: xmut(fake)
    freeze(fid, tmp, fake)
    before = snap_all(tmp)
    try:
        if o.get('inject'):
            with Inject(o['inject']):
                e = C.restart(E, tmp, fake)
                print(f' [inject {errno.errorcode[o["inject"]]}] start-up returned an Engine (no abort)')
                after_inject(TS, E, e, tmp, before)
                return
        e = C.restart(E, tmp, fake)
        print(' start-up returned an Engine (no abort)')
    except Exception as ex:
        print(f' start-up RAISED {type(ex).__name__}: {str(ex)[:160]}  at {where(ex)}')
        print(' file hashes before -> after the failed start-up:'); print(C.diff(before, snap_all(tmp)))
        return
    print(' file hashes before -> after start-up:'); print(C.diff(before, snap_all(tmp)))
    C.posture(E, e, 'start')
    if o.get('verify_stops'):
        try:
            e.verify_stops(reason='startup')
        except Exception as ex:
            print(f'  verify_stops raised {type(ex).__name__}: {ex} at {where(ex)}')
        print(f'  [verify_stops] stats={ {k: v for k, v in e.stopv_stats.items() if v} }  exchange stops now: {sorted(fake.stops)}')
    booked = []
    real_log = e.log_trade
    def log_trade(**kw):
        booked.append({k: kw.get(k) for k in ('event', 'symbol', 'side', 'qty', 'price', 'pnl')}); return real_log(**kw)
    e.log_trade = log_trade
    n_calls = len(fake.calls)
    try:
        C.reconcile_twice(e)
        for i in range(o.get('passes', 2) - 2):
            e.reconcile(e.equity()); print(f'  untracked after pass {3 + i}: {dict(e.untracked)}')
    except Exception as ex:
        print(f'  reconcile raised {type(ex).__name__}: {str(ex)[:120]} at {where(ex)}')
    lots = e.state['lots']
    print(f'  lots after reconcile: { {k[-10:]: (l["symbol"], l["side"], l["qty"], l.get("pending")) for k, l in lots.items()} }')
    print(f'  bookings during reconcile: {booked}')
    print(f'  exchange WRITE calls during reconcile: {[c for c in fake.calls[n_calls:] if c in EXCHANGE_WRITES]}  '
          f'stops now: {sorted(fake.stops)}')
    if o.get('confirm'):
        print(f'  install_block before confirm: {e.install_block()!r}; confirm_install() -> {e.confirm_install()!r}')
    C.try_new_risk(TS, E, e, 'resume')
    b2 = snap_all(tmp); r1 = e.save_state(); r2 = e.save_state()
    print(f' after two state saves (returned {r1!r}, {r2!r}; state_untrusted={sorted(e.state_untrusted)}):')
    print(C.diff(b2, snap_all(tmp)))


def after_inject(TS, E, e, tmp, before):
    print(' file hashes before -> after start-up:'); print(C.diff(before, snap_all(tmp)))
    C.posture(E, e, 'start')
    print(f'  save_hold={e._save_hold}  state_untrusted={e.state_untrusted}  save_state() -> {e.save_state()!r}  '
          f'persist_block={e.persist_block()!r}')
    C.reconcile_twice(e)
    C.try_new_risk(TS, E, e, 'resume')


only = set(sys.argv[2:])
for c in CASES:
    if not only or c[0] in only: run(*c)
E.close_fill_writers(2.0)
