"""REC-01 prep: shared harness. Runs the scratch copy of master (rec01prep/src) against TEMP data folders only.
Never touches %LOCALAPPDATA%\\ZackBot: LOCALAPPDATA is redirected to a fresh temp dir before anything is imported."""
import hashlib, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16)


def setup(src='src'):
    root = os.path.join(HERE, src)
    os.environ['LOCALAPPDATA'] = tempfile.mkdtemp(prefix='rec01_lad_')
    sys.path[:0] = [root, os.path.join(root, 'tests')]
    os.chdir(tempfile.mkdtemp(prefix='rec01_cwd_'))
    import test_safety as TS
    import engine as E
    E.Engine.notify = lambda self, t: None
    if hasattr(E, '_sleep'): E._sleep = lambda s: None
    return TS, E


def build(TS, E):
    """Initialized install: one BTCUSDT LONG lot with its stop on the fake exchange; state/settings/install + .bak on disk."""
    e, tmp = TS.mk_engine()
    TS.opened(e)
    e.save_settings(); e.save_settings()
    e.save_state(); e.save_state()
    return e, tmp, e.trade


def restart(E, tmp, fake):
    E.Futures = lambda *a, **k: fake
    e = E.Engine(dict(CFG), tmp)
    e.data = e.trade; e.connect(); e.marks = e.trade.marks()
    return e


def snap(d):
    out = {}
    for n in sorted(os.listdir(d)):
        p = os.path.join(d, n)
        if os.path.isfile(p) and (n.startswith(('state.json', 'settings.json', 'install.json'))):
            with open(p, 'rb') as f: out[n] = hashlib.sha256(f.read()).hexdigest()[:12]
    return out


def diff(a, b):
    lines = []
    for n in sorted(set(a) | set(b)):
        if n not in b: lines.append(f'  GONE     {n} ({a[n]})')
        elif n not in a: lines.append(f'  NEW      {n} ({b[n]})')
        elif a[n] != b[n]: lines.append(f'  CHANGED  {n} {a[n]} -> {b[n]}')
        else: lines.append(f'  same     {n} ({a[n]})')
    return '\n'.join(lines)


def ver(path):
    try:
        with open(path, 'rb') as f: return json.loads(f.read().decode('utf-8-sig')).get('schema_version', 0)
    except Exception as ex:
        return f'unparseable ({type(ex).__name__})'


def rewrite(path, fn):
    with open(path) as f: doc = json.load(f)
    fn(doc)
    with open(path, 'w') as f: json.dump(doc, f)


def posture(E, e, tag=''):
    """What the restarted engine believes vs the fake exchange, and whether new risk can get through."""
    fake = e.trade
    print(f'  [{tag}] lots in memory: {len(e.state["lots"])}   exchange positions: '
          f'{ {f"{s}|{d}": q for (s, d), q in fake.pos.items() if q > 1e-12} }   exchange stops: {len(fake.stops)}')
    print(f'  [{tag}] integrity: {e.integrity}')
    print(f'  [{tag}] ENTRIES_PAUSED={e.S.get("ENTRIES_PAUSED")}  persist_block={e.persist_block()!r}  '
          f'install_block={e.install_block()!r}')


def reconcile_twice(e):
    e.reconcile(e.equity()); first = dict(e.untracked)
    e.reconcile(e.equity())
    print(f'  untracked after pass 1: {first}   after pass 2: {dict(e.untracked)}')


def try_new_risk(TS, E, e, tag=''):
    """Owner resumes the way app.py / telegram /resume allow it (only install_block is consulted), then probes."""
    can_resume = not e.install_block() and not e.state.get('halted')
    print(f'  [{tag}] resume allowed by app/telegram gate (install_block only): {can_resume}')
    man_paused = e.entry_block(TS.SL, 'ETHUSDT', 'LONG', manual=True)
    print(f'  [{tag}] MANUAL entry gate while still paused (ETHUSDT LONG): {man_paused!r}  (None = allowed)')
    if can_resume:
        e.S['ENTRIES_PAUSED'] = False
        before = dict(e.trade.pos)
        ok = e.open_lot(TS.SL, 'ETHUSDT', 'LONG', TS.SG, None, e.equity())
        print(f'  [{tag}] after resume: automatic ETHUSDT LONG entry sent={bool(ok)} last_skip={e.last_skip!r} '
              f'exchange pos change={ {k: v for k, v in e.trade.pos.items() if before.get(k) != v} }')
