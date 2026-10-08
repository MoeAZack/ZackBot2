"""REC-01 repro (d) worker: one start-up of ONE build (src = new master cff3f88, old_src = pre-AUD-05 b8c11c8) on a TEMP
data folder. The fake exchange's positions/stops persist between processes in <fake.json> (outside the data folder).
usage: rollback_worker.py <src|old_src> <data_dir> <fake.json> <init|load>"""
import json, os, sys
import common as C

src, data, fakef, action = sys.argv[1:5]
TS, E = C.setup(src)
tag = 'NEW(cff3f88)' if src == 'src' else 'OLD(b8c11c8)'


def dump(fake):
    with open(fakef, 'w') as f:
        json.dump(dict(pos=[[s, d, q] for (s, d), q in fake.pos.items()], stops=fake.stops, n=fake.n), f)


def load_fake():
    fake = TS.FakeX()
    with open(fakef) as f: d = json.load(f)
    fake.pos = {(s, d_): q for s, d_, q in d['pos']}; fake.stops = {k: tuple(v) for k, v in d['stops'].items()}; fake.n = d['n']
    return fake


if action == 'init':
    e, _ = TS.mk_engine(data); TS.opened(e); e.save_state(); e.save_settings()
    if hasattr(e, '_save_safe'): e.save_state(); e.save_settings()
    dump(e.trade)
    print(f'  {tag} init: lots={len(e.state["lots"])} files={sorted(os.listdir(data))}')
else:
    fake = load_fake()
    E.Futures = lambda *a, **k: fake
    e = E.Engine(dict(C.CFG), data)
    e.data = e.trade; e.connect(); e.marks = e.trade.marks()
    print(f'  {tag} start: lots in memory={len(e.state["lots"])} state schema_version in memory='
          f'{e.state.get("schema_version", "-")} ENTRIES_PAUSED={e.S.get("ENTRIES_PAUSED")} '
          f'integrity={getattr(e, "integrity", "n/a (no such concept)")} '
          f'install_block={e.install_block() if hasattr(e, "install_block") else "n/a"!r}')
    e.reconcile(e.equity()); e.reconcile(e.equity())
    print(f'  {tag} untracked after 2 passes: {dict(e.untracked)}')
    e.save_state()
    print(f'  {tag} after one save: files={sorted(os.listdir(data))}  state.json schema_version={C.ver(os.path.join(data, "state.json"))}')
    dump(fake)
try: E.close_fill_writers(2.0)
except Exception: pass
