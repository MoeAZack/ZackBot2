"""RES-01 R3: PIT view + holdout guard (tools/research/pit.py), costs + slip-v1 (costs.py), splits (splits.py), the 1m
intrabar resolver (intrabar.py) and the `zb-research-run/1` envelope (report.py). Small synthetic stores and bars only
(no network, no real data, no strategy, no returns)."""
import ast
import shutil
import subprocess
import glob
import hashlib
import io
import json
import os
import random
import re
import sys
import zipfile
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import costs as C                                                                           # noqa: E402
import intrabar as I                                                                        # noqa: E402
import ledger as L                                                                          # noqa: E402
import manifest as M                                                                        # noqa: E402
import pit as P                                                                             # noqa: E402
import report as R                                                                          # noqa: E402
import splits as S                                                                          # noqa: E402
import universe as U                                                                        # noqa: E402

MIN, HOUR, DAY = 60_000, 3_600_000, 86_400_000
H4 = 4 * HOUR


def ms(s):
    return S.parse_utc(s if 'T' in s else s + 'T00:00:00Z')


def px(t):
    return 100 + ((t // MIN) % 97) * 0.05


def kl(t, step):
    o, c = px(t), px(t + step - MIN)
    return f'{t},{o},{max(o, c) + 1},{min(o, c) - 1},{c},10,{t + step - 1},{1000.0 + (t // step) % 7},5,4,1,0'


def put(store, rel, lines):
    p = os.path.join(store, *rel.split('/'))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(rel.rsplit('/', 1)[1][:-4] + '.csv', '\n'.join(lines) + '\n')
    raw = buf.getvalue()
    with open(p, 'wb') as f:
        f.write(raw)
    with open(p + '.ok', 'w') as f:
        f.write(hashlib.sha256(raw).hexdigest())


MONTHS = [(2024, 1), (2024, 2), (2024, 3)]


def month_range(y, m):
    a = int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
    return a, int(datetime(y + m // 12, m % 12 + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def build_store(store):
    for sym in ('AAAUSDT', 'XAUUSDT'):
        for y, m in MONTHS:
            a, b = month_range(y, m)
            tag = f'{y}-{m:02d}'
            for iv, step in (('1d', DAY), ('4h', H4)):
                put(store, f'um/monthly/klines/{sym}/{iv}/{sym}-{iv}-{tag}.zip', [kl(t, step) for t in range(a, b, step)])
            if not (sym == 'XAUUSDT' and m == 3):            # XAU: no mark bars in March (join gap test)
                put(store, f'um/monthly/markPriceKlines/{sym}/1h/{sym}-1h-{tag}.zip',
                    [kl(t, HOUR) for t in range(a, b, HOUR)])
            rate, ih = (0.0001, 8) if sym == 'AAAUSDT' else (-0.0002, 4)      # gold: observed 4h cadence
            put(store, f'um/monthly/fundingRate/{sym}/{sym}-fundingRate-{tag}.zip',
                ['calc_time,funding_interval_hours,last_funding_rate'] +
                [f'{t + 3},{ih},{rate}' for t in range(a, b, ih * HOUR)])
    a, b = month_range(2024, 2)
    put(store, f'um/monthly/klines/AAAUSDT/1m/AAAUSDT-1m-2024-02.zip', [kl(t, MIN) for t in range(a, b, MIN)])


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    store = str(tmp_path_factory.mktemp('store'))
    build_store(store)
    m = M.build(['um/monthly'], manifest_id='fx-r3', source_class='archive-verified', base=store,
                data_root='binance_um', survivor_only=False, loader=M.ARCHIVE_LOADER)
    cls = U.make_classes([{'symbol': 'XAUUSDT', 'class': 'gold-commodity', 'subclass': 'gold-spot',
                           'effective_from_ms': ms('2024-01-01'), 'basis': 'fixture listing notice'}],
                         classes_id='fx-classes', tradfi_cutoff_ms=ms('2025-12-01'), reviewed_cairo='2026-10-09',
                         method='fixture', pre_cutoff_rule='fixture: pre-cutoff symbols are crypto')
    u = U.build(U.load_daily(m, store), manifest_digest=m['digest'], classes=cls)
    return store, m, u


BOOKS = ('crypto', 'gold-commodity')


def ds_of(world):
    store, m, u = world
    return P.Dataset(m, store, u, books=BOOKS)


def git(repo, *a):
    return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', *a],
                          capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A throwaway Git checkout holding the research modules + one candidate file (the evaluation code)."""
    r = tmp_path / 'repo'
    for rel in R.CORE_EVAL_FILES:
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(os.path.join(ROOT, rel), r / rel)
    (r / 'strategy').mkdir()
    (r / 'strategy' / 'cand.py').write_text('RULE = 1\n')
    git(tmp_path, 'init', '-q', str(r))
    git(r, 'add', '-A')
    git(r, 'commit', '-q', '-m', 'fixture')
    return str(r)


PLAN_SPLITS = [{'name': 'train', 'start': '2024-02-05T00:00:00Z', 'end': '2024-02-19T00:00:00Z'},
               {'name': 'walk_forward', 'fold': 0, 'start': '2024-02-19T00:00:00Z', 'end': '2024-03-04T00:00:00Z'},
               {'name': 'holdout', 'start': '2024-03-04T00:00:00Z', 'end': '2024-03-25T00:00:00Z'}]


def plan():
    return S.SplitPlan(PLAN_SPLITS, interval='4h', lookback_bars=15, horizon_bars=6)


def dirs(tmp_path):
    (tmp_path / 'ledger').mkdir()
    (tmp_path / 'runs').mkdir()
    return str(tmp_path / 'ledger' / 'fam_x.jsonl'), str(tmp_path / 'runs')


@pytest.fixture
def canon(repo, monkeypatch):
    """The canonical registered ledger + runs store inside the checkout (a copy of the committed ledger, so the
    registry genesis pin holds), and that checkout made the canonical research checkout."""
    led = os.path.join(repo, 'research_evidence', 'ledger')
    os.makedirs(led)
    os.makedirs(os.path.join(repo, 'research_evidence', 'runs'))
    for f in glob.glob(os.path.join(ROOT, 'research_evidence', 'ledger', '*.jsonl')):
        shutil.copyfile(f, os.path.join(led, os.path.basename(f)))
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'registered ledger')
    monkeypatch.setattr(P, 'CANONICAL_REPO', repo)
    return os.path.join(led, 'fam_x.jsonl'), os.path.join(repo, 'research_evidence', 'runs')


def access(ds, path, pl='default', repo=M.REPO, candidate_id='c.v1'):
    return P.Access(ds, plan() if pl == 'default' else pl, path, candidate_id=candidate_id, author='t',
                    cairo_date='2026-10-09', repo=repo)


# ------------------------------------------------------------------ PIT view
def test_view_serves_only_closed_bars_available_at_t(world, tmp_path):
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    w = access(ds, path).open('train', lineage='root')
    t = ms('2024-02-12') + 2 * H4                                  # a 4h close
    bars = w.view(t).bars('AAAUSDT', '4h', 20)
    assert len(bars) == 20 and bars[-1].available_ms == t and all(b.available_ms <= t for b in bars)
    assert w.view(t - 1).bars('AAAUSDT', '4h', 1)[-1].available_ms == t - H4      # the bar closing at t is not yet out
    assert bars[0].open_ms >= w.lo                                                  # never reads before the split
    with pytest.raises(P.PITError, match='outside the opened window'):
        w.view(w.hi + 1)
    with pytest.raises(AttributeError):
        w.view(t).t = t + H4


HONEST = """
import costs as C


def run(v):
    b = v.bars('AAAUSDT', '4h', 15)
    return [b[-1].close > sum(x.close for x in b) / 15, round(C.slip_bps(b, 0.05), 9)]
"""


REPORTING = """

import report as _R


def summarize(outputs):
    sec = {s: {k: None for k in keys} for s, keys in _R.SECTIONS.items()}
    sec['counts'] = dict(sec['counts'], trades=len(outputs))
    return {'long': {row: sec for row in C.STRESS}}


def summarize2(outputs):
    return summarize(outputs[:1])


def run2(v):
    return [True, 0.0]
"""


def evaluator(dirpath, src, name='ev.py'):
    os.makedirs(str(dirpath), exist_ok=True)
    f = os.path.join(str(dirpath), name)
    with open(f, 'w', newline='\n') as fh:
        fh.write(src)
    return f


def train_times(n=40, seed=7):
    rnd = random.Random(seed)
    lo, hi = plan().decision_range('train')
    return sorted({lo + rnd.randrange((hi - lo) // H4 + 1) * H4 for _ in range(n)})


def test_evaluator_runs_sandboxed_and_a_nondeterministic_control_is_caught(world, tmp_path):
    """Codex R3 P1: evaluation runs through `evaluate`, which runs the evaluator in a separate sandboxed process with
    a View proxy and re-runs every decision with all not-yet-available rows perturbed."""
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    w = access(ds, path).open('train', lineage='root')
    times = train_times()
    out = P.evaluate(w, evaluator(tmp_path / 'ev', HONEST), 'run', times)
    direct = []
    for t in times:                                       # the same rule in-process gives the same outputs
        b = w.view(t).bars('AAAUSDT', '4h', 15)
        direct.append([b[-1].close > sum(x.close for x in b) / 15, round(C.slip_bps(b, 0.05), 9)])
    assert list(out) == direct and out.attestation['perturbed'] is True
    assert out.attestation['isolation'] == P.ISOLATION_ID and out.attestation['schedule'] == R.schedule_of(times)
    rec = L._check_state(path)['fam_x'][-1]
    assert rec['detail']['evaluation_attestation'] == out.attestation_digest
    counter = evaluator(tmp_path / 'ev', 'N = [0]\n\n\ndef run(v):\n    N[0] += 1\n    return N[0]\n', 'cnt.py')
    with pytest.raises(P.PITError, match='bypassed'):
        P.evaluate(w, counter, 'run', times[:1])
    with pytest.raises(P.PITError, match='Window opened through Access'):
        P.evaluate(w.view(times[0]), counter, 'run', times)


@pytest.mark.parametrize('src,msg', [
    # Codex 6079042573 repro: the raw Dataset behind the capability (manifest metadata of a future file)
    ("import pit\n\n\ndef run(v):\n    return pit._src(v._cap)._ds.files('klines', 'AAAUSDT', '4h')[-1]"
     "['last_open_ms']\n", "no attribute '_cap'"),
    # a fresh Dataset built from the manifest/store on disk
    ("import gzip\n\n\ndef run(v):\n    return len(open({manifest!r}, 'rb').read())\n", 'sandbox refused'),
    # direct read of an archive file of the store
    ("def run(v):\n    return len(open({zip!r}, 'rb').read())\n", 'sandbox refused'),
    ("import os\n\n\ndef run(v):\n    return os.listdir({store!r})\n", 'sandbox refused'),
    ("def run(v):\n    return open({evidence!r}).read()\n", 'sandbox refused'),
    ("import subprocess\n\n\ndef run(v):\n    return subprocess.run(['git', 'log']).returncode\n",
     'sandbox refused'),
    ("def run(v):\n    open('out.txt', 'w').write('x')\n    return 1\n", 'sandbox refused write'),
    ("import ctypes\n\n\ndef run(v):\n    return 1\n", 'sandbox refused import'),
], ids=['raw-dataset-via-pit', 'manifest-file', 'store-zip', 'store-listing', 'research-evidence', 'subprocess',
        'write', 'ctypes'])
def test_evaluator_cannot_reach_raw_store_manifest_or_files(world, tmp_path, src, msg):
    """Codex 6079042573 P1 negative controls: manifest metadata, direct file reads and the raw Dataset are not
    reachable from evaluator code; every attempt fails closed."""
    ds = ds_of(world)
    store = world[0]
    path, _ = dirs(tmp_path)
    mf = tmp_path / 'manifest.json'
    mf.write_text(json.dumps(world[1]))
    zp = glob.glob(os.path.join(store, 'um', 'monthly', 'klines', 'AAAUSDT', '4h', '*.zip'))[-1]
    evid = os.path.join(ROOT, 'research_evidence', 'ledger', 'REGISTRY.jsonl')
    w = access(ds, path).open('train', lineage='root')
    f = evaluator(tmp_path / 'ev', src.format(manifest=str(mf), zip=zp, store=store, evidence=evid), 'bad.py')
    with pytest.raises(P.PITError, match=msg):
        P.evaluate(w, f, 'run', train_times(3))


ATTACKS = {
    # Codex 6088593971 P1: a tracked evaluator dynamically loading an ignored helper
    'ignored-helper-spec': ("import importlib.util\nimport os\nROOT = os.path.dirname(os.path.dirname("
                            "os.path.abspath(__file__)))\n\n\ndef run(v):\n    spec = importlib.util."
                            "spec_from_file_location('ign', os.path.join(ROOT, 'dev_out', 'ignored.py'))\n"
                            "    m = importlib.util.module_from_spec(spec)\n    spec.loader.exec_module(m)\n"
                            "    return m.V\n", 'sandbox refused'),
    # Codex 6088593971 P1: an unpinned installed (site-packages) distribution
    'site-package-import': ("def run(v):\n    import pytest\n    return pytest.__version__\n",
                            "No module named 'pytest'"),
    'site-package-file': ("def run(v):\n    return len(open({site!r}, 'rb').read())\n", 'sandbox refused'),
    # self-attack: dynamic imports of a repo module outside the static closure
    'dunder-import': ("def run(v):\n    return __import__('other').X\n", "No module named 'other'"),
    'importlib-import-module': ("import importlib\n\n\ndef run(v):\n    return importlib.import_module('other').X\n",
                                "No module named 'other'"),
    'runpy': ("import os\nimport runpy\n\n\ndef run(v):\n    return runpy.run_path(os.path.join(os.path.dirname("
              "__file__), 'other.py'))['X']\n", 'sandbox refused'),
    'exec-of-repo-source': ("import os\n\n\ndef run(v):\n    g = {{}}\n    exec(open(os.path.join(os.path.dirname("
                            "__file__), 'other.py')).read(), g)\n    return g['X']\n", 'sandbox refused'),
    'data-file-as-code': ("import os\n\n\ndef run(v):\n    g = {{}}\n    exec(open(os.path.join(os.path.dirname("
                          "__file__), 'payload.txt')).read(), g)\n    return g['X']\n", 'sandbox refused'),
    'repo-pyc': ("import marshal\nimport os\n\n\ndef run(v):\n    p = os.path.join(os.path.dirname(__file__), "
                 "'__pycache__', 'other.cpython-x.pyc')\n    return len(open(p, 'rb').read())\n",
                 'sandbox refused'),
}


@pytest.mark.parametrize('name', sorted(ATTACKS))
def test_sandbox_refuses_code_outside_the_hashed_closure(world, tmp_path, repo, name):
    """Codex 6088593971 P1 + Build self-attack: code the closure does not hash can never execute in the sandbox."""
    import pytest as _pt
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    strat = os.path.join(repo, 'strategy')
    with open(os.path.join(repo, '.gitignore'), 'w') as f:
        f.write('dev_out/\n')
    os.makedirs(os.path.join(repo, 'dev_out'))
    for rel, body in (('dev_out/ignored.py', 'V = 1\n'), ('strategy/other.py', 'X = 7\n'),
                      ('strategy/payload.txt', 'X = 7\n'), ('strategy/__pycache__/other.cpython-x.pyc', 'x')):
        os.makedirs(os.path.dirname(os.path.join(repo, *rel.split('/'))), exist_ok=True)
        with open(os.path.join(repo, *rel.split('/')), 'w') as f:
            f.write(body)
    src, msg = ATTACKS[name]
    f = evaluator(strat, src.format(site=_pt.__file__), 'attack.py')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'attack fixture')
    assert 'dev_out/ignored.py' not in git(repo, 'ls-files')                    # ignored: invisible to Git status
    w = access(ds, path, repo=repo).open('train', lineage='root')
    with pytest.raises(P.PITError, match=msg):
        P.evaluate(w, f, 'run', train_times(1))


OBSERVE = """
import locale
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def run(v):
    return [os.path.exists(os.path.join(ROOT, 'dev_out', 'marker')), os.path.exists(os.path.join(ROOT, '.git')),
            os.path.exists(os.path.join(ROOT, '.gitignore')), sorted(os.listdir(HERE)), sorted(os.listdir(ROOT)),
            os.listdir('.'), int(os.stat(__file__).st_mtime), sorted(os.environ), sys.flags.hash_randomization,
            time.timezone, locale.getencoding(), hash('zb') == {seed0!r}]


def probe(v):
    return os.stat({host!r}).st_size


def probe_exists(v):
    return os.path.exists({host!r})
"""


def test_evaluator_cannot_observe_ignored_repo_state_or_unbound_environment(world, tmp_path, repo):
    """Codex 6089091570 P1: an ignored filename / existence probe cannot change the output; the evaluator sees only
    the materialized hashed tree, a fixed environment, and the observed environment is bound in the attestation."""
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    with open(os.path.join(repo, '.gitignore'), 'w') as f:
        f.write('dev_out/\n')
    os.makedirs(os.path.join(repo, 'dev_out'))
    marker = os.path.join(repo, 'dev_out', 'marker')
    with open(marker, 'w') as f:
        f.write('1')
    seed0 = subprocess.run([sys.executable, '-c', "print(hash('zb'))"], capture_output=True, text=True,
                           env={**{k: os.environ[k] for k in ('SYSTEMROOT', 'WINDIR') if k in os.environ},
                                'PYTHONHASHSEED': '0'}, stdin=subprocess.DEVNULL).stdout.strip()
    f = evaluator(os.path.join(repo, 'strategy'), OBSERVE.format(host=marker, seed0=int(seed0)), 'obs.py')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'observer')
    assert R.code_identity(repo)['dirty'] is False and os.path.exists(marker)  # Git is clean, the marker exists
    w = access(ds, path, repo=repo).open('train', lineage='root')
    out = P.evaluate(w, f, 'run', train_times(1))
    (got,) = list(out)
    assert got[:3] == [False, False, False]                   # ignored marker, .git, .gitignore: not in the tree
    assert got[3] == ['obs.py'] and got[4] == ['strategy'] and got[5] == []    # only the hashed closure exists
    assert got[6] == P.TREE_MTIME                             # mtimes fixed, not the checkout's
    assert got[7] == sorted(set(P.SANDBOX_ENV) | {k for k in P.SANDBOX_ENV_KEYS if k in os.environ})
    env = out.attestation['sandbox_env']
    assert got[8:10] == [0, 0] and got[11] is True             # fixed hash seed and TZ
    assert got[10] == env['locale_encoding'] and env['utf8_mode'] == 1   # host code page: bound, not fixed
    assert env['hash_randomization'] == 0 and env['timezone'] == 0 and env['cwd_entries'] == []
    assert env['tree_mtime'] == P.TREE_MTIME and env['env'] == P.SANDBOX_ENV
    os.remove(marker)                                          # host state changes: the output does not
    assert list(P.evaluate(w, f, 'run', train_times(1))) == [got]
    with pytest.raises(P.PITError, match='sandbox refused path probe'):     # absolute host probes fail closed
        P.evaluate(w, f, 'probe', train_times(1))
    with open(marker, 'w') as fh:                              # boolean probes: constant False, path-independent
        fh.write('x')
    assert list(P.evaluate(w, f, 'probe_exists', train_times(1))) == [False]


def test_sandbox_has_no_site_pth_or_entry_points_and_hashes_its_closure(world, tmp_path, repo):
    """-S: no site-packages, `.pth`, `sitecustomize` or distribution entry points; the attestation lists every
    repo file the evaluator could load, and a statically imported ignored helper cannot be frozen into a run."""
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    strat = os.path.join(repo, 'strategy')
    src = ("import importlib.metadata\nimport sys\nimport costs\n\n\ndef run(v):\n    return [sys.flags.no_site, "
           "'sitecustomize' in sys.modules, len(list(importlib.metadata.distributions())), "
           "len(importlib.metadata.entry_points()), any('site-packages' in p for p in sys.path)]\n")
    f = evaluator(strat, src, 'probe.py')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'probe')
    w = access(ds, path, repo=repo).open('train', lineage='root')
    out = P.evaluate(w, f, 'run', train_times(1))
    assert list(out) == [[1, False, 0, 0, False]]
    code = {c['path'] for c in out.attestation['code']}
    assert {'strategy/probe.py', 'tools/research/costs.py', 'tools/research/manifest.py', 'feasibility.py'} <= code
    assert all(not os.path.isabs(c) for c in code)                             # all from the run checkout
    with open(os.path.join(repo, '.gitignore'), 'w') as fh:
        fh.write('dev_out/\n')
    os.makedirs(os.path.join(repo, 'dev_out'))
    with open(os.path.join(repo, 'dev_out', 'ignored.py'), 'w') as fh:
        fh.write('V = 1\n')
    evaluator(strat, 'from dev_out.ignored import V\n\n\ndef run(v):\n    return V\n', 'stat.py')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'static import of an ignored helper')
    with pytest.raises(R.ReportError, match='tracked'):
        R.freeze_run(repo=repo, entrypoint=ep('strategy/stat.py'), schedule=SCHED, eval_files=['strategy/stat.py'], config={}, seeds=[], **ident(ds, plan()))
    att = P.evaluate(w, os.path.join(strat, 'stat.py'), 'run', train_times(1)).attestation
    assert 'dev_out/ignored.py' in {c['path'] for c in att['code']}            # hashed, so a report would refuse it


# Codex 6089789885 P1: one frozen identity must yield one result across fresh sandbox processes. Each evaluator reads
# a process-dependent value at import time; two decisions inside one process agree, two fresh processes do not.
FRESH_PROCESS = {
    'wall-clock': ('import time\nX = time.time()\n', 'differ between two fresh sandbox processes'),
    'pid': ('import os\nX = os.getpid()\n', 'differ between two fresh sandbox processes'),
    'auto-seeded-random': ('import random\nX = random.random()\n', 'differ between two fresh sandbox processes'),
    '__file__': ('X = __file__\n', 'host path of the sandbox'),
    'cwd': ('import os\nX = os.getcwd()\n', 'host path of the sandbox'),
}


@pytest.mark.parametrize('name', sorted(FRESH_PROCESS))
def test_process_dependent_evaluator_is_refused_across_fresh_processes(world, tmp_path, name):
    src, msg = FRESH_PROCESS[name]
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    w = access(ds, path).open('train', lineage='root')
    f = evaluator(tmp_path / 'ev', src + '\n\ndef run(v):\n    return X\n', 'proc.py')
    n = len(L._check_state(path)['fam_x'])
    with pytest.raises(P.PITError, match=msg):
        P.evaluate(w, f, 'run', train_times(2))
    g = evaluator(tmp_path / 'ev', src + '\n\ndef run(v):\n    return 1\n\n\ndef summarize(o):\n    return [X]\n',
                  'proc2.py')
    with pytest.raises(P.PITError, match=msg):                 # the same dependency in the summary only
        P.evaluate(w, g, 'run', train_times(2), summary='summarize')
    assert len(L._check_state(path)['fam_x']) == n            # a refused run records no evaluation


def test_fresh_process_replay_binds_interpreter_without_host_paths(world, tmp_path):
    """Items 2 + 3: a path-independent evaluator passes both fresh processes; the attestation binds the interpreter
    version + executable hash but carries no interpreter / temp-tree host path; a host path in results is refused."""
    import platform
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    w = access(ds, path).open('train', lineage='root')
    src = ("import os\nimport sys\n\n\ndef run(v):\n    return [os.path.basename(__file__), os.listdir('.'), "
           "len(v.bars('AAAUSDT', '4h', 3))]\n\n\ndef exe(v):\n    return sys.executable\n\n\n"
           "def prefix(v):\n    return {'k': [sys.base_prefix]}\n\n\n"
           "LOC = ('executable', 'prefix', 'exec_prefix', 'base_prefix', 'base_exec_prefix')\n\n\n"
           "def codes(v):\n    return [[ord(c) for c in getattr(sys, n)] for n in LOC]\n\n\n"
           "def phash(v):\n    import hashlib\n"
           "    return [hashlib.sha256(getattr(sys, n).encode()).hexdigest() for n in LOC]\n")
    f = evaluator(tmp_path / 'ev', src, 'fine.py')
    out = P.evaluate(w, f, 'run', train_times(2))
    assert list(out) == [['fine.py', [], 3]] * 2
    interp = out.attestation['execution_environment']
    with open(sys.executable, 'rb') as fh:
        assert interp['executable_sha256'] == hashlib.sha256(fh.read()).hexdigest()
    assert interp['python'] == platform.python_version() and interp['hexversion'] == sys.hexversion
    assert interp == R.SB.execution_environment()             # the child computes the parent's canonical identity
    assert interp['os'] == sys.platform and interp['cpu_count'] == os.cpu_count()
    blob = json.dumps(out.attestation).lower()
    assert not re.search(r'zb-eval-[a-z0-9_]{8}(\\|/)', blob)      # no materialized-tree / cwd temp path
    for host in (sys.executable, sys.base_prefix, os.path.dirname(sys.executable)):
        assert json.dumps(host).lower()[1:-1] not in blob and host.lower().replace('\\', '/') not in blob
    # Codex 6093943713 P1: the evaluator sees only path-independent sentinels for the interpreter location, so no
    # encoding of it (plain string, integer codepoints, path hash) carries the install path into accepted results.
    loc = ('executable', 'prefix', 'exec_prefix', 'base_prefix', 'base_exec_prefix')
    sent = [R.SB.SYS_SENTINELS[n] for n in loc]
    assert all(R.SB.SYS_SENTINELS[n] != getattr(sys, n) for n in loc)
    assert list(P.evaluate(w, f, 'exe', train_times(1))) == [sent[0]]
    assert list(P.evaluate(w, f, 'prefix', train_times(1))) == [{'k': [sent[3]]}]
    cp = P.evaluate(w, f, 'codes', train_times(1))
    assert list(cp) == [[[ord(c) for c in x] for x in sent]]
    assert all(''.join(map(chr, c)) == x for c, x in zip(list(cp)[0], sent))      # reconstructs only the sentinel
    hs = P.evaluate(w, f, 'phash', train_times(1))
    assert list(hs) == [[hashlib.sha256(x.encode()).hexdigest() for x in sent]]
    real = [hashlib.sha256(getattr(sys, n).encode()).hexdigest() for n in loc]
    assert not set(real) & set(list(hs)[0])                                          # never the host path's hash


LAUNCH_PROBE = '''import hashlib
import sys


def launch(v):
    try:
        f = sys._getframe()
        while f.f_back is not None:
            f = f.f_back
        outer = f.f_code.co_filename
    except PermissionError:
        outer = 'frames refused'
    main = sys.modules['__main__']
    stray = sorted(k for k in sys.path_importer_cache if not any(k.startswith(p) for p in sys.path if p))
    vals = [sys.argv, sys.orig_argv, getattr(main, '__file__', None), outer]
    return [vals, [[ord(c) for c in str(x)] for x in vals], hashlib.sha256(repr(vals).encode()).hexdigest(),
            hashlib.sha256(repr(stray).encode()).hexdigest()]
'''


def test_sandbox_launch_is_checkout_path_independent(world, tmp_path, monkeypatch):
    """Cowork 6094010557: the checkout path that launched the sandbox (argv, orig_argv, __main__.__file__, the
    outermost frame's filename, the path-importer cache) is never evaluator-visible; two byte-identical sandbox.py
    copies at different checkout locations give identical outputs, plain, as codepoints and as hashes."""
    import shutil
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    w = access(ds, path).open('train', lineage='root')
    f = evaluator(tmp_path / 'ev', LAUNCH_PROBE, 'probe.py')
    outs = []
    for loc in ('checkout-a', os.path.join('other', 'checkout-b-longer')):
        d = tmp_path / loc / 'tools' / 'research'
        os.makedirs(str(d))
        monkeypatch.setattr(P, 'SANDBOX', shutil.copy(P.SANDBOX, str(d / 'sandbox.py')))
        outs.append(list(P.evaluate(w, f, 'launch', train_times(1))))
    monkeypatch.undo()
    assert outs[0] == outs[1]
    main = R.SB.SANDBOX_MAIN
    vals = [[main], [R.SB.SYS_SENTINELS['executable'], '-s', '-S', '-B', '-P', main], main, 'frames refused']
    assert outs[0] == [[vals, [[ord(c) for c in str(x)] for x in vals], hashlib.sha256(repr(vals).encode()).hexdigest(),
                        hashlib.sha256(repr([]).encode()).hexdigest()]]
    assert 'checkout' not in json.dumps(outs)


SELF_DISABLE = '''import sys

POLICY = ('BLOCKED_EVENTS', 'BLOCKED_IMPORTS', 'WRITE_FLAGS', 'EVIDENCE_DIR', 'THIRD_PARTY_DIRS', 'INTROSPECTION_EVENTS',
          'FRAME_ATTRS', 'PROBE_FUNCS', 'make_hook', 'guard_path_probes', '_Chan', '_builtin_norm', 'main')


def attempt(fn):
    try:
        fn()
        return 'allowed'
    except PermissionError:
        return 'refused'
    except Exception as e:
        return 'error ' + type(e).__name__


def cdll():
    import ctypes
    ctypes.CDLL(None)


def write():
    with open('pwn.txt', 'w') as f:
        f.write('x')


def sock():
    import socket
    socket.socket()


def proc():
    import subprocess
    subprocess.run(['echo', 'x'])


def system():
    import os
    os.system('echo x')


def gc_walk():
    import gc
    gc.get_objects()


def frame():
    sys._getframe(1)


def tb_frame():
    try:
        raise ValueError
    except ValueError as e:
        return e.__traceback__.tb_frame


SEEN = []


def run(v):
    gs = list({id(g): g for g in (sys.modules['__main__'].__dict__, type(v).__init__.__globals__,
                                  type(v)._call.__globals__)}.values())
    if not SEEN:                                   # what the sandbox left reachable, before any tampering
        SEEN.append(sorted({n for g in gs for n in POLICY if n in g}))
    for g in gs:                                   # clear / rebind every policy name, every decision
        for n in POLICY:
            x = g.get(n)
            if hasattr(x, 'clear'):
                x.clear()
            g[n] = () if n != 'WRITE_FLAGS' else 0
    return [SEEN[0], {k: attempt(f) for k, f in sorted({
        'ctypes.CDLL(None)': cdll, 'write open': write, 'socket': sock, 'subprocess': proc, 'os.system': system,
        'gc.get_objects': gc_walk, 'sys._getframe': frame, 'tb_frame': tb_frame}.items())}]
'''


def test_evaluator_cannot_disable_the_sandbox_policy(world, tmp_path):
    """Codex 6094254617: the evaluator reaches no policy / protocol / factory name through __main__, sys.modules or
    function globals; clearing or rebinding every policy name there changes nothing, because the installed hook
    closes over immutable snapshots. ctypes.CDLL(None), a write open, a socket, a process and the gc / frame routes to
    the hook stay refused at every decision, in both fresh processes (the replay must equal the first run)."""
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    w = access(ds, path).open('train', lineage='root')
    f = evaluator(tmp_path / 'ev', SELF_DISABLE, 'attack.py')
    out = list(P.evaluate(w, f, 'run', train_times(3)))
    want = {k: 'refused' for k in ('ctypes.CDLL(None)', 'write open', 'socket', 'subprocess', 'os.system',
                                   'gc.get_objects', 'sys._getframe', 'tb_frame')}
    assert out == [[[], want]] * 3
    assert not os.path.exists(tmp_path / 'ev' / 'pwn.txt')


PROBE_ATTACK = '''import importlib
import importlib.machinery
import importlib.util
import os
import sys

OUT = os.sep                                     # a path outside every allowed root on any host
NAMES = ('stat', 'lstat', 'access', 'readlink', '_path_exists', '_path_isdir', '_path_isfile', '_getfinalpathname')


def attempt(fn):
    try:
        fn()
        return 'allowed'
    except PermissionError:
        return 'refused'
    except Exception as e:
        return 'error ' + type(e).__name__


def raw_like(x):
    return type(x).__name__ == 'builtin_function_or_method' and getattr(x, '__name__', '') in NAMES + ('create_builtin',)


def harvested():
    """Every builtin probe reachable from the guarded functions' closures, defaults and __wrapped__."""
    import _imp
    found = []
    for m in (os, os.path, sys.modules[os.name], _imp):
        for v in list(vars(m).values()):
            for c in (getattr(v, '__closure__', None) or ()):
                try:
                    found.append(c.cell_contents)
                except ValueError:
                    pass
            found += list(getattr(v, '__defaults__', None) or ()) + list((getattr(v, '__kwdefaults__', None) or {}).values())
            found.append(getattr(v, '__wrapped__', None))
    return [f for f in found if raw_like(f)]


def call_all(fs):
    for f in fs:
        if f.__name__ == 'create_builtin':
            f(importlib.machinery.BuiltinImporter.find_spec(os.name)).stat(OUT)
        else:
            f(OUT) if f.__name__ != 'access' else f(OUT, os.F_OK)


def fresh_module():
    import _imp
    return _imp.create_builtin(importlib.machinery.BuiltinImporter.find_spec(os.name))


def reimport():
    saved = sys.modules.pop(os.name)
    try:
        return importlib.import_module(os.name)
    finally:
        sys.modules[os.name] = saved


def harvest_only(v):
    return [len(harvested()), attempt(lambda: call_all(harvested()) or (_ for _ in ()).throw(PermissionError()))]


def run(v):
    rebound = {}
    for name in NAMES:                           # rebind every guarded name to whatever its wrapper holds
        f = getattr(sys.modules[os.name], name, None)
        if f is None:
            continue
        cells = [c.cell_contents for c in (f.__closure__ or ())] if hasattr(f, '__closure__') else []
        for c in cells:
            if raw_like(c):
                setattr(os, name, c)
                rebound[name] = True
    res = {
        'harvested raw': len(harvested()),
        'call harvested': attempt(lambda: call_all(harvested()) or (_ for _ in ()).throw(PermissionError())),
        'os.stat': attempt(lambda: os.stat(OUT)),
        'os.lstat': attempt(lambda: os.lstat(OUT)),
        'os.access': attempt(lambda: os.access(OUT, os.F_OK)),
        'nt/posix stat': attempt(lambda: sys.modules[os.name].stat(OUT)),
        'reload os': attempt(lambda: importlib.reload(os).stat(OUT)),
        'reload nt/posix': attempt(lambda: importlib.reload(sys.modules[os.name]).stat(OUT)),
        'create_builtin': attempt(lambda: fresh_module().stat(OUT)),
        'module_from_spec': attempt(lambda: importlib.util.module_from_spec(
            importlib.machinery.BuiltinImporter.find_spec(os.name)).stat(OUT)),
        're-import': attempt(lambda: reimport().stat(OUT)),
        'scandir entry': attempt(lambda: [e.stat() for e in os.scandir(OUT)]),
        'audit forged': attempt(lambda: sys.audit('zb.probe', 'stat', (OUT,), {}, [])),
        'audit dir_fd': attempt(lambda: sys.audit('zb.probe', 'stat', ('x',), {'dir_fd': 3}, [])),
        'exists is constant': os.path.exists(OUT) is False and os.path.isdir(OUT) is False,
        'own file': os.path.isfile(__file__),
        'rebound': sorted(rebound),
    }
    return res
'''


def test_evaluator_cannot_restore_the_path_probe_originals(world, tmp_path):
    """Owner order on cbcdbcb (self-reported residual): no raw unaudited path probe is Python-reachable in the
    sandbox. Harvesting wrapper closures / defaults / __wrapped__, rebinding, reloading os or nt/posix, minting a
    fresh nt/posix (`_imp.create_builtin`, `module_from_spec`, re-import), DirEntry.stat and forged `zb.probe`
    events all stay refused for a path outside the roots, identically in both fresh processes."""
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    w = access(ds, path).open('train', lineage='root')
    f = evaluator(tmp_path / 'ev', PROBE_ATTACK, 'probe_attack.py')
    out = list(P.evaluate(w, f, 'run', train_times(2)))
    refused = ('call harvested', 'os.stat', 'os.lstat', 'os.access', 'nt/posix stat', 'reload os', 'reload nt/posix',
               'create_builtin', 'module_from_spec', 're-import', 'scandir entry', 'audit forged', 'audit dir_fd')
    want = dict({k: 'refused' for k in refused}, **{'harvested raw': 0, 'exists is constant': True, 'own file': True,
                                                   'rebound': []})
    assert out == [want] * 2
    assert list(P.evaluate(w, f, 'harvest_only', train_times(1))) == [[0, 'refused']]


PROCESS_ATTACK = '''import os
import sys
import types


def attempt(fn):
    try:
        fn()
        return 'allowed'
    except PermissionError:
        return 'refused'
    except Exception as e:
        return 'error ' + type(e).__name__


def import_low():
    import _posixsubprocess
    _posixsubprocess.fork_exec()


def reload_low():
    import importlib
    import subprocess
    sys.modules['_posixsubprocess'] = subprocess._posixsubprocess      # put the alias back, then re-execute it
    try:
        importlib.reload(subprocess._posixsubprocess)
    finally:
        del sys.modules['_posixsubprocess']


def reload_interp():
    import concurrent.futures as cf
    import importlib
    sys.modules['_interpreters'] = cf._interpreters
    try:
        importlib.reload(cf._interpreters)
    finally:
        del sys.modules['_interpreters']


def subinterp():
    import _interpreters
    _interpreters.exec(_interpreters.create(), 'x = 1')


def subinterp_alias():
    import concurrent.futures as cf
    i = cf._interpreters
    i.exec(i.create(), 'x = open(__file__).read()')


STUBBED = ('_posixsubprocess', '_interpreters', '_xxsubinterpreters', '_interpqueues', '_interpchannels',
           '_xxinterpchannels')
IMPORT_REFUSED = STUBBED + ('interpreters', 'concurrent.interpreters', '_testcapi', '_testinternalcapi',
                            '_testlimitedcapi')


def builtin_route():
    import _imp
    _imp.create_builtin(types.SimpleNamespace(name='_posixsubprocess'))


def dynamic_route(name, origin):
    import _imp
    _imp.create_dynamic(types.SimpleNamespace(name=name, origin=origin))


def aliases():
    """Any raw function of a stubbed module still reachable from a loaded module (aliases such as subprocess._fork_exec,
    or attributes of an aliased stubbed module)."""
    found = []
    for m in list(sys.modules.values()):
        try:
            items = list(vars(m).items())
        except TypeError:
            continue
        for k, v in items:
            if type(v).__name__ == 'module' and v.__name__ in STUBBED:
                items += [(k + '.' + a, b) for a, b in vars(v).items()]
            if type(v).__name__ == 'builtin_function_or_method' and                     getattr(getattr(v, '__self__', None), '__name__', '') in STUBBED:
                found.append(k)
    return found


def low_level_launch():
    """A direct no-op child launch through the platform's lowest Python-level primitive."""
    if os.name == 'nt':
        import _winapi
        _winapi.CreateProcess(None, 'cmd /c rem', None, None, False, 0, None, None, None)
    else:
        import _posixsubprocess
        _posixsubprocess.fork_exec()


def run(v):
    import subprocess                              # Cowork 6094554597: must import on every platform
    import concurrent.futures as cf
    alias = getattr(subprocess, '_fork_exec', None)
    # Codex 6094776448: an alias that this interpreter does not have (CPython 3.13 has neither
    # `subprocess._posixsubprocess` nor `concurrent.futures._interpreters`) is 'unavailable', decided by a feature
    # check before the attempt - never by accepting an arbitrary exception
    has_psp, has_cfi = hasattr(subprocess, '_posixsubprocess'), hasattr(cf, '_interpreters')
    return {
        'import subprocess': 'ok',
        'imports': {n: attempt(lambda: __import__(n)) for n in IMPORT_REFUSED},
        'in sys.modules': [n for n in IMPORT_REFUSED if n in sys.modules],  # before the reload rows re-insert
        '_posixsubprocess.fork_exec': attempt(import_low),
        'reload _posixsubprocess': attempt(reload_low) if has_psp else 'unavailable',
        'subinterpreter': attempt(subinterp),
        'subinterpreter via alias': attempt(subinterp_alias) if has_cfi else 'unavailable',
        'reload _interpreters': attempt(reload_interp) if has_cfi else 'unavailable',
        'create_builtin _interpreters': attempt(lambda: __import__('_imp').create_builtin(
            types.SimpleNamespace(name='_interpreters'))),
        'aliases': aliases(),
        'subprocess._fork_exec': 'absent' if alias is None else attempt(lambda: alias()),
        'create_builtin': attempt(builtin_route),
        'create_dynamic': attempt(lambda: dynamic_route('_posixsubprocess', 'x/_posixsubprocess.so')),
        'create_dynamic dotted': attempt(lambda: dynamic_route('pkg._posixsubprocess', 'x/m.so')),
        'create_dynamic renamed': attempt(lambda: dynamic_route('harmless', 'x/_posixsubprocess.cpython-314.so')),
        'low-level launch': attempt(low_level_launch),
        'subprocess.run': attempt(lambda: subprocess.run(['echo', 'x'])),
        'os.system': attempt(lambda: os.system('echo x')),
        'socket': attempt(lambda: __import__('socket').socket()),
        'write open': attempt(lambda: open('pwn.txt', 'w')),
        'json works': __import__('json').dumps({'a': 1}) == '{"a": 1}',
    }
'''


def test_evaluator_cannot_reach_low_level_process_creation(world, tmp_path):
    """Codex 6094447977 (Cowork, Linux): `_posixsubprocess.fork_exec` raises no audit event. The module stays importable
    (`subprocess` needs it; Cowork 6094554597 finding 1) but the function is a refusing stub in the module and every alias
    (`subprocess._fork_exec`), and reload / `_imp.create_builtin` / `_imp.create_dynamic` (by name, dotted name or file
    stem) cannot mint a fresh one. Subinterpreters, which have no audit hook (finding 2), are refused. The direct
    low-level no-op launch is refused on this platform (Windows: `_winapi.CreateProcess`, an audited event); subprocess / os / socket / write refusals hold and an
    ordinary stdlib evaluator still works, identically in both fresh processes."""
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    w = access(ds, path).open('train', lineage='root')
    f = evaluator(tmp_path / 'ev', PROCESS_ATTACK, 'process_attack.py')
    out = list(P.evaluate(w, f, 'run', train_times(2)))
    refused = ('subinterpreter', '_posixsubprocess.fork_exec', 'create_builtin _interpreters',
               'create_builtin', 'create_dynamic', 'create_dynamic dotted', 'create_dynamic renamed', 'low-level launch',
               'subprocess.run', 'os.system', 'socket', 'write open')
    names = ('_posixsubprocess', '_interpreters', '_xxsubinterpreters', '_interpqueues', '_interpchannels',
             '_xxinterpchannels', 'interpreters', 'concurrent.interpreters', '_testcapi', '_testinternalcapi',
             '_testlimitedcapi')
    want = dict({k: 'refused' for k in refused}, **{'import subprocess': 'ok', 'aliases': [], 'json works': True,
                                                   'imports': {n: 'refused' for n in names}, 'in sys.modules': []})
    want['subprocess._fork_exec'] = 'absent' if os.name == 'nt' else 'refused'
    import concurrent.futures as cf                # the sandbox child runs this same interpreter
    import subprocess
    cfi = 'refused' if hasattr(cf, '_interpreters') else 'unavailable'
    want.update({'reload _posixsubprocess': 'refused' if hasattr(subprocess, '_posixsubprocess') else 'unavailable',
                 'subinterpreter via alias': cfi, 'reload _interpreters': cfi})
    assert out == [want] * 2
    assert not os.path.exists(tmp_path / 'ev' / 'pwn.txt')


def test_position_capability_replaces_caller_entered_ms(world, tmp_path):
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    acc = access(ds, path, pl=None)
    w = acc.open_development('2024-02-05T00:00:00Z', '2024-03-04T00:00:00Z', lineage='root')
    t0 = ms('2024-02-12')
    pos = w.view(t0).enter('AAAUSDT', 'dec-1')
    v = w.view(t0 + 10 * H4)
    assert v.bars('AAAUSDT', '4h', 3, position=pos)[-1].available_ms == v.t
    assert w.decisions() == (('dec-1', 'AAAUSDT', t0),)
    with pytest.raises(P.PITError, match='issued only by View.enter'):
        P.Position('BTCUSDT', w.lo, 'forged', v._cap)                   # cannot invent an earlier membership date
    with pytest.raises(AttributeError):
        pos.entry_ms = w.lo
    with pytest.raises(P.PITError, match='already issued'):
        w.view(t0).enter('AAAUSDT', 'dec-1')
    with pytest.raises(P.PITError, match='not a universe member'):
        w.view(t0).enter('BTCUSDT', 'dec-2')
    with pytest.raises(P.PITError, match='position is for'):
        v.bars('XAUUSDT', '4h', 1, position=pos)
    with pytest.raises(P.PITError, match='not after the decision time'):
        w.view(t0 - H4).bars('AAAUSDT', '4h', 1, position=pos)
    other = acc.open_development('2024-02-05T00:00:00Z', '2024-03-04T00:00:00Z')
    with pytest.raises(P.PITError, match='issued by this window'):
        other.view(t0 + H4).bars('AAAUSDT', '4h', 1, position=pos)
    with pytest.raises(TypeError):
        v.bars('AAAUSDT', '4h', 1, entered_ms=w.lo)                    # the self-attested integer is gone


def test_truncation_rerun_reproduces_every_decision(world, tmp_path):
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    acc = access(ds, path, pl=None)
    full = acc.open_development('2024-02-05T00:00:00Z', '2024-03-04T00:00:00Z', lineage='root')
    rnd = random.Random(11)
    for _ in range(12):
        t = full.lo + (15 + rnd.randrange(100)) * H4
        trunc = acc.open_development('2024-02-05T00:00:00Z', S.utc(t))
        assert full.view(t).bars('AAAUSDT', '4h', 15) == trunc.view(t).bars('AAAUSDT', '4h', 15)
    recs = L._check_state(path)['fam_x']
    assert len(recs) == 13 and {r['split'] for r in recs} == {'development'}


def test_membership_is_point_in_time_and_gold_is_served(world, tmp_path):
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    acc = access(ds, path, pl=None)
    early = acc.open_development('2024-01-02T00:00:00Z', '2024-01-20T00:00:00Z', lineage='root')
    with pytest.raises(P.PITError, match='not a universe member'):
        early.view(early.hi).bars('AAAUSDT', '4h', 2)              # no ranking before the first Monday
    w = acc.open_development('2024-02-05T00:00:00Z', '2024-03-01T00:00:00Z')
    v = w.view(w.hi)
    assert {'AAAUSDT', 'XAUUSDT'} <= v.members()
    assert len(v.bars('XAUUSDT', '4h', 5)) == 5
    with pytest.raises(P.PITError, match='not a universe member'):
        v.bars('BTCUSDT', '4h', 1)


def test_funding_events_timestamp_rule_and_mark_join(world, tmp_path):
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    w = access(ds, path, pl=None).open_development('2024-02-05T00:00:00Z', '2024-03-20T00:00:00Z', lineage='root')
    t0 = ms('2024-02-12') + 3                                            # a funding calc_time
    pos = w.view(t0).enter('AAAUSDT', 'f-aaa')
    v = w.view(ms('2024-02-14'))
    ev = v.funding_events(pos, t0 + 16 * HOUR)                          # entry == T counts, exit == T does not
    assert [e[0] for e in ev] == [t0, t0 + 8 * HOUR]
    assert ev[0][2] == px(t0 - 3 - MIN)                                 # close of the 1h mark bar closing at T
    long_ = C.funding_cost('long', 2.0, ev, C.STRESS['base'])
    short = C.funding_cost('short', 2.0, ev, C.STRESS['base'])
    assert long_ > 0 and short == -long_                                # longs pay a positive rate, shorts receive
    assert C.funding_cost('long', 2.0, ev, C.STRESS['funding_x2']) == pytest.approx(2 * long_)
    assert C.funding_cost('long', 2.0, ev, C.STRESS['funding_mark_adverse']) == pytest.approx(long_ * 1.005)
    assert C.funding_cost('short', 2.0, ev, C.STRESS['funding_mark_adverse']) == pytest.approx(short * 0.995)
    gpos = w.view(t0).enter('XAUUSDT', 'f-xau')
    gold = v.funding_events(gpos, t0 + 9 * HOUR)
    assert [e[0] for e in gold] == [t0, t0 + 4 * HOUR, t0 + 8 * HOUR]  # gold's actual 4h funding times
    assert C.funding_cost('long', 1.0, gold, C.STRESS['base']) < 0     # negative rate: longs receive
    m = C.CostModel(CAL, C.symbol_class_from_universe(world[2]))
    m.check_funding_cadence('XAUUSDT', v.funding('XAUUSDT', t0))
    with pytest.raises(C.CostError, match='declares 4h'):
        m.check_funding_cadence('XAUUSDT', v.funding('AAAUSDT', t0))    # 8h rows cannot pass as gold's
    mpos = w.view(ms('2024-03-11')).enter('XAUUSDT', 'f-gap')
    with pytest.raises(P.PITError, match='gap > 1 bar'):
        w.view(ms('2024-03-19')).funding_events(mpos, ms('2024-03-12'))


def test_dataset_fails_closed_on_changed_bytes(world, tmp_path):
    store, m, u = world
    import shutil
    s2 = str(tmp_path / 's2')
    shutil.copytree(store, s2)
    rel = 'um/monthly/klines/AAAUSDT/4h/AAAUSDT-4h-2024-02.zip'
    a, b = month_range(2024, 2)
    put(s2, rel, [kl(t, H4) for t in range(a, b - H4, H4)])               # republished with its own matching .ok
    ds = P.Dataset(m, s2, u, books=BOOKS)
    path, _ = dirs(tmp_path)
    w = access(ds, path).open('train', lineage='root')
    with pytest.raises(P.PITError, match='no longer match'):
        w.view(w.hi).bars('AAAUSDT', '4h', 3)


def test_window_cannot_be_built_without_access(world):
    with pytest.raises(P.PITError, match='only through Access'):
        P.Window(ds_of(world), 0, 1, 'x')


def test_universe_must_match_manifest(world):
    store, m, u = world
    bad = dict(u, manifest_digest='0' * 64)
    bad['digest'] = U.digest_of(bad)
    with pytest.raises(P.PITError, match='different manifest'):
        P.Dataset(m, store, bad, books=BOOKS)
    with pytest.raises(P.PITError, match='PIT universe'):
        P.Dataset(m, store, None)
    for books in (None, (), ('mixed',)):
        with pytest.raises(P.PITError, match='no mixed default'):
            P.Dataset(m, store, u, books=books)
    crypto_only = P.Dataset(m, store, u, books=('crypto',))
    assert 'XAUUSDT' not in crypto_only.members(ms('2024-02-12'))      # gold lives in its own book
    assert 'XAUUSDT' in ds_of(world).members(ms('2024-02-12'))


# ------------------------------------------------------------------ splits + holdout guard
def test_split_plan_purge_embargo_and_episode_membership():
    p = plan()
    assert p.purge_ms == 15 * H4 and p.embargo_ms == H4 and p.horizon_ms == 6 * H4
    first, last = p.decision_range('train')
    assert first == ms('2024-02-05') + 16 * H4 and last == ms('2024-02-19') - 6 * H4
    assert p.split_of_episode(first, first + 6 * H4) == 'train'
    for e, x in ((first - H4, first), (last, last + 7 * H4), (last + H4, last + 2 * H4)):
        with pytest.raises(S.SplitError):
            p.split_of_episode(e, x)
    assert p.doc()['digest'] == p.digest == plan().digest
    with pytest.raises(AttributeError):
        p.horizon_ms = 1


@pytest.mark.parametrize('splits,kw,msg', [
    (PLAN_SPLITS, dict(horizon_bars=None), 'finite ex-ante'),
    (PLAN_SPLITS, dict(lookback_bars=10), 'lookback_bars'),
    (PLAN_SPLITS[::-1], {}, 'time order'),
    ([PLAN_SPLITS[0], dict(PLAN_SPLITS[1], start='2024-02-18T00:00:00Z'), PLAN_SPLITS[2]], {}, 'non-overlapping'),
    ([PLAN_SPLITS[2], dict(PLAN_SPLITS[0], start='2024-03-25T00:00:00Z', end='2024-04-08T00:00:00Z')], {}, 'last'),
    (PLAN_SPLITS[:2], {}, 'exactly one holdout'),
    ([dict(PLAN_SPLITS[0], start='2024-02-05T01:00:00Z')] + PLAN_SPLITS[1:], {}, 'align'),
    ([dict(PLAN_SPLITS[0], end='2024-02-08T00:00:00Z'), dict(PLAN_SPLITS[1], start='2024-02-08T00:00:00Z'),
      PLAN_SPLITS[2]], {}, 'no decision fits'),
    ([PLAN_SPLITS[0], dict(PLAN_SPLITS[1], fold=1), PLAN_SPLITS[2]], {}, 'numbered'),
])
def test_split_plan_refusals(splits, kw, msg):
    args = dict(interval='4h', lookback_bars=15, horizon_bars=6)
    args.update(kw)
    with pytest.raises(S.SplitError, match=msg):
        S.SplitPlan(splits, **args)


SCHED = [0]


def ep(path, function='run', summary='summarize'):
    return {'path': path, 'function': function, 'summary': summary}


def ident(ds, pl, *, family='fam_x', window=None, candidate_id='c.v1'):
    return {'family': family, 'candidate_id': candidate_id, 'split': 'holdout', 'window': window or pl.window('holdout'),
            'books': list(ds.books), 'manifest_digest': ds.digest, 'universe_digest': ds.universe_digest,
            'split_plan_digest': pl.digest, 'cost_model_digest': 'c' * 64, 'slip_cal_digest': 'd' * 64,
            'author': 't', 'cairo_date': '2026-10-09'}


def make_env(ds, pl, repo, *, config=None, **kw):
    return R.freeze_run(repo=repo, entrypoint=ep('strategy/cand.py'), schedule=SCHED, eval_files=['strategy/cand.py'], config=config or {'k': 1}, seeds=[1, 2],
                        **ident(ds, pl, **kw))


def reveal(path, env, kind='holdout_reveal'):
    r = env['run']
    L.append(path, kind=kind, candidate_id=r['candidate_id'], split='holdout', window=r['window'], author='t',
             cairo_date='2026-10-09', manifest_digest=r['manifest_digest'], run_digest=env['run_digest'],
             detail={'eval_digest': r['eval_digest']})


def test_holdout_is_sealed_without_the_atomic_reveal(world, tmp_path, repo, canon):
    ds = ds_of(world)
    path, runs = canon
    pl = plan()
    acc = access(ds, path, pl, repo=repo)
    acc.open('train', lineage='root')                                   # the family exists, no reveal yet
    env = make_env(ds, pl, repo)
    with pytest.raises(P.PITError, match='sealed'):
        acc.open('holdout')                                             # no envelope at all
    with pytest.raises(P.PITError, match='sealed'):
        acc.open('holdout', envelope=env)                               # envelope not frozen in the runs store
    R.write_envelope(runs, env)
    with pytest.raises(P.PITError, match='no atomic holdout_reveal'):
        acc.open('holdout', envelope=env)
    with pytest.raises(P.PITError, match='may not touch the sealed holdout'):
        acc.open_development('2024-03-01T00:00:00Z', '2024-03-05T00:00:00Z')
    reveal(path, env)
    w = acc.open('holdout', envelope=env)
    assert w.view(w.hi).bars('AAAUSDT', '4h', 3)
    recs = L._check_state(path)['fam_x']
    assert [r['kind'] for r in recs][-2:] == ['holdout_reveal', 'data_access'] and recs[-1]['split'] == 'holdout'
    assert recs[-1]['candidate_id'] == 'c.v1' and recs[-1]['detail']['books'] == list(BOOKS)
    acc.open('train')                                                   # any other record closes the reveal group
    with pytest.raises(P.PITError, match='group is closed'):
        acc.open('holdout', envelope=env)
    reveal(path, env, 'holdout_rerun')                                  # a deterministic rerun reopens it
    acc.open('holdout', envelope=env)


@pytest.mark.parametrize('kw,msg', [
    (dict(family='fam_y'), 'identity'),
    (dict(window={'start': '2024-03-04T00:00:00Z', 'end': '2024-03-18T00:00:00Z'}), 'identity'),
    (dict(candidate_id='c.v2'), 'identity'),
])
def test_holdout_guard_refuses_foreign_envelopes(world, tmp_path, repo, canon, kw, msg):
    ds = ds_of(world)
    path, runs = canon
    pl = plan()
    acc = access(ds, path, pl, repo=repo)
    acc.open('train', lineage='root')
    env = make_env(ds, pl, repo, **kw)
    R.write_envelope(runs, env)
    with pytest.raises(P.PITError, match=msg):
        acc.open('holdout', envelope=env)


def test_execution_environment_is_frozen_and_must_match_exactly(world, tmp_path, repo, monkeypatch):
    """Codex 6093381880 P1: run.code carries the canonical execution environment, captured at freeze time BEFORE
    evaluation; a run frozen under another environment fails closed in both fresh processes and at holdout access."""
    ds = ds_of(world)
    pl = plan()
    evaluator(os.path.join(repo, 'strategy'), HONEST + REPORTING)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'evaluator')
    times = train_times(2)

    def freeze():
        return R.freeze_run(repo=repo, entrypoint=ep('strategy/ev.py'), schedule=times,
                            eval_files=['strategy/cand.py', 'strategy/ev.py'], config={'k': 1}, seeds=[1, 2],
                            **dict(ident(ds, pl), split='train', window=pl.window('train')))
    env = freeze()
    xe = env['run']['code']['execution_environment']
    assert xe == R.SB.execution_environment() and set(xe) == set(R.SB.EXEC_ENV_KEYS)
    assert xe['python'] == env['run']['code']['python'] and R.verify_code(env, repo) == []
    # malformed / inconsistent frozen identities never validate
    for bad in ({k: v for k, v in xe.items() if k != 'cpu_count'}, dict(xe, cpu_count='8'), dict(xe, python='0.0.0'),
                dict(xe, executable_sha256='x'), None):
        with pytest.raises(R.ReportError, match='execution_environment'):
            R.envelope(dict(env['run'], code=dict(env['run']['code'], execution_environment=bad)))
    # relocated interpreter (identical executable bytes, another install path) is another execution environment
    monkeypatch.setattr(sys, 'base_prefix', sys.base_prefix + '-relocated')
    moved = R.SB.execution_environment()
    monkeypatch.undo()
    assert moved['executable_sha256'] == xe['executable_sha256'] and moved['install_digest'] != xe['install_digest']
    assert {k for k in xe if moved[k] != xe[k]} == {'install_digest'}
    # cross-environment: frozen on a simulated other host (one more CPU), then verified / evaluated here
    other = dict(xe, cpu_count=xe['cpu_count'] + 1)
    monkeypatch.setattr(R.SB, 'execution_environment', lambda: dict(other))
    foreign = freeze()
    monkeypatch.undo()
    assert foreign['run']['code']['execution_environment'] == other
    assert R.verify_code(foreign, repo) == [
        "execution environment of this process differs from the frozen run.code identity: ['cpu_count']"]
    path, _ = dirs(tmp_path)
    w = access(ds, path, pl, repo=repo).open('train', envelope=foreign, lineage='root')
    assert w.execution_environment == other
    with pytest.raises(P.PITError, match=r"fresh sandbox process 1 differs from the frozen run.code identity: "
                                         r"\['cpu_count'\]"):
        P.evaluate(w, os.path.join(repo, 'strategy', 'ev.py'), 'run', times, summary='summarize')
    # the same run frozen here evaluates and seals
    w = access(ds, path, pl, repo=repo).open('train', envelope=env)
    out = P.evaluate(w, os.path.join(repo, 'strategy', 'ev.py'), 'run', times, summary='summarize')
    assert out.attestation['execution_environment'] == xe
    assert R.make_report(env, out.results, out.attestation, path)['report_digest']


def test_code_identity_is_captured_not_self_attested(world, tmp_path, repo):
    """Codex R3 P1: a fabricated clean identity, a dirty or moved checkout, or a changed evaluator never opens."""
    ds = ds_of(world)
    pl = plan()
    env = make_env(ds, pl, repo)
    run = env['run']
    assert run['code']['git_head'] == git(repo, 'rev-parse', 'HEAD') and run['code']['dirty'] is False
    assert {f['path'] for f in run['eval']['files']} == set(R.CORE_EVAL_FILES) | {'strategy/cand.py'}
    assert R.verify_code(env, repo) == []
    # fabricated: any 40-hex head, an arbitrary eval_digest, empty libs
    fake = dict(run, eval_digest='e' * 64)
    with pytest.raises(R.ReportError, match='eval_digest does not match'):
        R.envelope(fake)
    fake = dict(run, code=dict(run['code'], git_head='a' * 40))
    assert any('HEAD moved' in x for x in R.verify_code(R.envelope(fake), repo))
    fake = dict(run, eval=dict(run['eval'], files=[dict(f, sha256='0' * 64) if f['path'] == 'strategy/cand.py' else f
                                                   for f in run['eval']['files']]))
    fake['eval_digest'] = R.eval_digest(fake['eval'], fake['seeds'])
    assert any('evaluation files changed' in x for x in R.verify_code(R.envelope(fake), repo))
    assert R.verify_code(R.envelope(dict(run, code=dict(run['code'], libs={'numpy': '9'}))), repo) == [
        'dependency set differs from the canonical frozen set']
    no_core = [f for f in run['eval']['files'] if f['path'] != 'tools/research/pit.py']
    with pytest.raises(R.ReportError, match='CORE_EVAL_FILES'):
        R.envelope(dict(run, eval=dict(run['eval'], files=no_core)))
    with pytest.raises(R.ReportError, match='tracked'):
        R.freeze_run(repo=repo, entrypoint=ep('strategy/untracked.py'), schedule=SCHED, eval_files=['strategy/untracked.py'], config={}, seeds=[], **ident(ds, pl))
    # the executing checkout changes after freezing
    cand = os.path.join(repo, 'strategy', 'cand.py')
    with open(cand, 'a') as f:
        f.write('RULE = 2\n')
    bad = R.verify_code(env, repo)
    assert 'the executing checkout is dirty' in bad and any('evaluation files changed' in x for x in bad)
    assert any('eval_digest recomputed' in x for x in bad)
    with pytest.raises(R.ReportError, match='dirty'):
        make_env(ds, pl, repo)                                          # freezing a dirty tree is refused
    git(repo, 'commit', '-q', '-am', 'tweak')
    bad = R.verify_code(env, repo)
    assert any('HEAD moved' in x for x in bad) and not any('dirty' in x for x in bad)
    os.makedirs(os.path.join(repo, 'research_evidence'), exist_ok=True)
    with open(os.path.join(repo, 'research_evidence', 'x.json'), 'w') as f:
        f.write('{}')
    assert R.code_identity(repo)['dirty'] is False                      # evidence writes are not code
    with open(os.path.join(repo, 'research_evidence', 'x.py'), 'w') as f:
        f.write('X = 1')
    assert R.code_identity(repo)['dirty'] is True                       # executable evidence is code


def test_moved_or_dirty_checkout_cannot_open_the_holdout(world, tmp_path, repo, canon):
    ds = ds_of(world)
    path, runs = canon
    pl = plan()
    acc = access(ds, path, pl, repo=repo)
    acc.open('train', lineage='root')
    env = make_env(ds, pl, repo)
    R.write_envelope(runs, env)
    reveal(path, env)
    with open(os.path.join(repo, 'strategy', 'cand.py'), 'a') as f:
        f.write('RULE = 3\n')
    with pytest.raises(P.PITError, match='frozen code identity'):
        acc.open('holdout', envelope=env)
    git(repo, 'commit', '-q', '-am', 'moved')
    with pytest.raises(P.PITError, match='HEAD moved'):
        acc.open('holdout', envelope=env)


def test_ledger_recomputes_run_digest_from_the_envelope(world, tmp_path, repo):
    ds = ds_of(world)
    path, runs = dirs(tmp_path)
    pl = plan()
    access(ds, path, pl).open('train', lineage='root')
    env = make_env(ds, pl, repo)
    with pytest.raises(L.LedgerError, match='frozen run envelope'):
        reveal(path, env)                                               # envelope not stored: refused
    R.write_envelope(runs, env)
    other = make_env(ds, pl, repo, config={'k': 2})
    R.write_envelope(runs, other)
    forged = dict(env, run=other['run'])                                # stored digest of A, identity of B
    with pytest.raises(L.LedgerError, match='frozen run envelope'):
        reveal(path, forged)
    reveal(path, env)
    assert L.recompute_run_digest(L._check_state(path)['fam_x'][-1], runs) == env['run_digest']
    assert L.recompute_run_digest({'run_digest': env['run_digest']}) == 'no-immutable-runs-store'


def test_scratch_ledger_never_skips_the_envelope_proof_for_holdout_records(world, tmp_path, repo):
    """Codex R3 P1 / ruling 5: no runs store beside the ledger = development only."""
    ds = ds_of(world)
    (tmp_path / 'scratch').mkdir()
    path = str(tmp_path / 'scratch' / 'fam_x.jsonl')
    pl = plan()
    access(ds, path, pl).open('train', lineage='root')                 # development/train records still work
    env = make_env(ds, pl, repo)
    with pytest.raises(L.LedgerError, match='immutable runs store'):
        reveal(path, env)
    r = env['run']
    with pytest.raises(L.LedgerError, match='immutable runs store'):
        L.append(path, kind='data_access', candidate_id='c.v1', split='holdout', window=r['window'], author='t',
                 cairo_date='2026-10-09', manifest_digest=r['manifest_digest'], run_digest=env['run_digest'],
                 detail={'eval_digest': r['eval_digest']})


def test_reveal_components_bind_the_complete_identity_cross_candidate_rejected(world, tmp_path, repo, canon):
    """Codex R3 P2: a component naming another candidate (with its own valid envelope) cannot join the reveal group."""
    ds = ds_of(world)
    path, runs = canon
    pl = plan()
    access(ds, path, pl).open('train', lineage='root')
    env = make_env(ds, pl, repo)
    other = make_env(ds, pl, repo, candidate_id='c.v2')
    R.write_envelope(runs, env)
    R.write_envelope(runs, other)
    reveal(path, env)
    o = other['run']
    with pytest.raises(L.LedgerError, match='complete identity'):
        L.append(path, kind='data_access', candidate_id='c.v2', split='holdout', window=o['window'], author='t',
                 cairo_date='2026-10-09', manifest_digest=o['manifest_digest'], run_digest=other['run_digest'],
                 detail={'eval_digest': o['eval_digest']})
    with pytest.raises(P.PITError, match='another run'):
        access(ds, path, pl, repo=repo, candidate_id='c.v2').open('holdout', envelope=other)
    access(ds, path, pl, repo=repo).open('holdout', envelope=env)       # the right candidate still opens


def test_scratch_ledger_and_runs_tree_never_open_the_holdout(world, tmp_path, repo, monkeypatch):
    """Codex 6079042573 P1 repro: a scratch ledger + runs tree with a valid envelope, train + reveal records."""
    ds = ds_of(world)
    pl = plan()
    sc = tmp_path / 'scratch'
    (sc / 'ledger').mkdir(parents=True)
    (sc / 'runs').mkdir()
    path, runs = str(sc / 'ledger' / 'fam_x.jsonl'), str(sc / 'runs')
    acc = access(ds, path, pl, repo=repo)
    acc.open('train', lineage='root')
    env = make_env(ds, pl, repo)
    R.write_envelope(runs, env)
    reveal(path, env)                                                   # the scratch tree accepts the reveal...
    with pytest.raises(P.PITError, match='canonical research checkout'):
        acc.open('holdout', envelope=env)                               # ...but it cannot open the holdout
    monkeypatch.setattr(P, 'CANONICAL_REPO', repo)
    with pytest.raises(P.PITError, match='canonical registered ledger'):
        acc.open('holdout', envelope=env)
    # the canonical location itself, but a freshly minted registry (not the pinned genesis): append-only check fails
    led = os.path.join(repo, 'research_evidence', 'ledger')
    os.makedirs(led)
    os.makedirs(os.path.join(repo, 'research_evidence', 'runs'))
    path2 = os.path.join(led, 'fam_x.jsonl')
    acc2 = access(ds, path2, pl, repo=repo)
    acc2.open('train', lineage='root')
    R.write_envelope(os.path.join(repo, 'research_evidence', 'runs'), env)
    reveal(path2, env)
    with pytest.raises(P.PITError, match='append-only verification'):
        acc2.open('holdout', envelope=env)


def test_executable_evidence_and_imported_helpers_are_code(world, tmp_path, repo):
    """Codex 6079042573 P1 repro: a tracked candidate importing a tracked research_evidence/helper.py."""
    ds = ds_of(world)
    pl = plan()
    ev = os.path.join(repo, 'research_evidence')
    os.makedirs(ev)
    with open(os.path.join(ev, 'helper.py'), 'w', newline='\n') as f:
        f.write('K = 1\n')
    with open(os.path.join(repo, 'strategy', 'uses_ev.py'), 'w', newline='\n') as f:
        f.write('from research_evidence.helper import K\n')
    with open(os.path.join(repo, 'strategy', 'util.py'), 'w', newline='\n') as f:
        f.write('W = 1\n')
    with open(os.path.join(repo, 'strategy', 'cand2.py'), 'w', newline='\n') as f:
        f.write('import util\n')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'helpers')
    with pytest.raises(R.ReportError, match='may not live in or import from research_evidence'):
        R.freeze_run(repo=repo, entrypoint=ep('strategy/uses_ev.py'), schedule=SCHED, eval_files=['strategy/uses_ev.py'], config={}, seeds=[], **ident(ds, pl))
    with pytest.raises(R.ReportError, match='may not live in or import from research_evidence'):
        R.freeze_run(repo=repo, entrypoint=ep('research_evidence/helper.py'), schedule=SCHED, eval_files=['research_evidence/helper.py'], config={}, seeds=[], **ident(ds, pl))
    env = R.freeze_run(repo=repo, entrypoint=ep('strategy/cand2.py'), schedule=SCHED, eval_files=['strategy/cand2.py'], config={}, seeds=[], **ident(ds, pl))
    assert 'strategy/util.py' in {f['path'] for f in env['run']['eval']['files']}     # closure hashed automatically
    assert R.verify_code(env, repo) == []
    with open(os.path.join(ev, 'helper.py'), 'w', newline='\n') as f:
        f.write('K = 999\n')                                         # executable evidence changed
    assert R.code_identity(repo)['dirty'] is True and 'the executing checkout is dirty' in R.verify_code(env, repo)
    assert R.dirty_paths(repo) == ['research_evidence/helper.py']
    git(repo, 'checkout', '--', 'research_evidence/helper.py')
    with open(os.path.join(ev, 'run.json'), 'w') as f:
        f.write('{}')
    assert R.code_identity(repo)['dirty'] is False                      # a non-executable artifact is evidence
    with open(os.path.join(ev, 'sneaky.pth'), 'w') as f:
        f.write('import os')
    assert R.dirty_paths(repo) == ['research_evidence/sneaky.pth']      # anything not a known artifact is code
    os.remove(os.path.join(ev, 'sneaky.pth'))
    with open(os.path.join(repo, 'strategy', 'util.py'), 'a') as f:
        f.write('W = 2\n')
    git(repo, 'commit', '-q', '-am', 'util')
    assert any('evaluation files changed' in x for x in R.verify_code(env, repo))


# ------------------------------------------------------------------ costs + slip-v1
CAL = {'id': 'slip-cal-v1', 'target': 'per-side fill-vs-reference bps', 'source_series': 'synthetic fixture',
       'estimator': 'median ratio', 'loss': 'absolute', 'pooling': 'per cost class', 'fallback': 'floor 2 bps',
       'limit_touch_rule': C.LIMIT_TOUCH_RULE,
       'calibration_window': {'start': '2022-01-01T00:00:00Z', 'end': '2022-07-01T00:00:00Z'}, 'fitted': False,
       'c': {'crypto': 0.05, 'gold-spot': 0.06, 'gold-tokenized': 0.065, 'commodity': 0.07, 'equity': 0.08,
             'fx': 0.09}}
SC = {'BTCUSDT': 'crypto', 'XAUUSDT': 'gold-spot', 'PAXGUSDT': 'gold-tokenized', 'CLUSDT': 'commodity',
      'TSLAUSDT': 'equity', 'USDBRLUSDT': 'fx'}


def bars_of(rows):
    return [P.Bar(i * H4, o, h, lo, c, 1.0, 1.0, (i + 1) * H4) for i, (o, h, lo, c) in enumerate(rows)]


def test_slip_v1_formula_is_exact():
    rows = [(100, 101, 99, 100)] * 15
    b = bars_of(rows)
    assert C.tr_sma14(b) == 2.0 and C.wilder_atr14(b) == 2.0                 # 15 bars: Wilder's seed = the SMA
    assert C.slip_bps(b, 0.05) == pytest.approx(max(2, 0.05 * 1e4 * 2 / 100))          # 10 bps
    assert C.slip_bps(b, 0.001) == 2.0                                                   # floor
    gap = bars_of([(100, 100, 100, 100)] + [(100, 100, 100, 100)] * 13 + [(110, 111, 109, 110)])
    assert C.tr_sma14(gap) == pytest.approx(11 / 14)                                     # |h - prev close| counts
    with pytest.raises(C.CostError, match='15 closed bars'):
        C.tr_sma14(b[:14])
    assert not hasattr(C, 'atr14')                                                       # renamed: not Wilder ATR


def test_wilder_atr14_is_the_sensitivity_candidate():
    b = bars_of([(100, 101, 99, 100)] * 15 + [(100, 108, 100, 100)])                     # one TR of 8 after 14 of 2
    assert C.tr_sma14(b) == pytest.approx((13 * 2 + 8) / 14)                             # the simple mean of 14 TRs
    assert C.wilder_atr14(b) == pytest.approx((2 * 13 + 8) / 14)                         # Wilder smoothing step
    long_ = bars_of([(100, 101, 99, 100)] * 15 + [(100, 108, 100, 100)] * 3)
    assert C.wilder_atr14(long_) != pytest.approx(C.tr_sma14(long_))
    assert C.slip_bps(long_, 0.05, C.WILDER_ATR14) == pytest.approx(
        max(2, 0.05 * 1e4 * C.wilder_atr14(long_) / 100))
    assert C.STRESS['slip_wilder_atr14'].vol == C.WILDER_ATR14 and C.STRESS['base'].vol == C.TR_SMA14
    with pytest.raises(C.CostError, match='vol must be'):
        C.slip_bps(long_, 0.05, 'ATR14')


def test_fill_prices_are_adverse_and_gap_stops_fill_at_open():
    base, x5 = C.STRESS['base'], C.STRESS['gap_slip_x5']
    assert C.fill_price(100.0, 'buy', 10, base, tick=0.01) == 100.1
    assert C.fill_price(100.0, 'sell', 10, base, tick=0.01) == 99.9
    assert C.fill_price(100.004, 'buy', 0, base, tick=0.01) == 100.01                   # rounded against us
    assert C.fill_price(100.006, 'sell', 0, base, tick=0.01) == 100.0
    assert C.fill_price(100.0, 'sell', 10, x5, gap=True) == pytest.approx(99.5)
    assert C.fill_price(100.0, 'sell', 10, C.STRESS['fees_slip_x2']) == pytest.approx(99.8)
    assert C.stop_reference('long', 95, 94) == (94, True) and C.stop_reference('long', 95, 96) == (95, False)
    assert C.stop_reference('short', 105, 106) == (106, True)


def test_limit_fill_rule_and_stress_rows():
    assert set(C.STRESS) == {'base', 'fees_slip_x2', 'funding_x2', 'gap_slip_x5', 'limit_no_fill', 'limit_as_taker',
                             'flat_funding_legacy', 'slip_wilder_atr14', 'funding_mark_adverse'}
    assert C.limit_fill('touch', C.STRESS['base']) is None
    assert C.limit_fill('price_through', C.STRESS['base']) == 'maker'
    assert C.limit_fill('price_through', C.STRESS['limit_no_fill']) is None
    assert C.limit_fill('touch', C.STRESS['limit_as_taker']) == 'taker'
    m = C.CostModel(CAL, SC)
    assert C.fee(1000, 'taker', m.row('BTCUSDT'), C.STRESS['base']) == pytest.approx(0.5)
    assert C.fee(1000, 'maker', m.row('BTCUSDT'), C.STRESS['fees_slip_x2']) == pytest.approx(0.4)
    assert C.funding_cost('short', 1, [], C.STRESS['flat_funding_legacy'], bars_held=10,
                          entry_notional=1000) == pytest.approx(0.5)


def test_cost_rows_per_class_gold_own_row_no_crypto_fallback(world):
    m = C.CostModel(CAL, SC)
    gold, crypto = m.row('XAUUSDT'), m.row('BTCUSDT')
    assert gold.cost_class == 'gold-spot' and gold.status == 'PROVISIONAL' and gold.funding_cadence_hours == 4
    assert (gold.taker, gold.maker, gold.fee_tier) == (crypto.taker, crypto.maker, crypto.fee_tier)   # Binance tier
    assert m.slip_c('XAUUSDT') == 0.06 != m.slip_c('BTCUSDT')                                        # own slip c
    assert m.labels('XAUUSDT') == [C.FUNDING_MARK_LABEL, 'SLIP-VOL-TR-SMA14', 'COST-GOLD-SPOT-PROVISIONAL',
                                   'SLIP-C-UNFITTED', 'WEEKEND-REFERENCE-GAP']
    assert m.labels('BTCUSDT') == [C.FUNDING_MARK_LABEL, 'SLIP-VOL-TR-SMA14', 'SLIP-C-UNFITTED']
    for sym, cls in (('CLUSDT', 'commodity'), ('TSLAUSDT', 'equity'), ('USDBRLUSDT', 'fx')):
        assert m.row(sym).cost_class == cls and m.row(sym).status == 'UNCALIBRATED'
        assert f'COST-{cls.upper()}-UNCALIBRATED' in m.labels(sym)
    with pytest.raises(C.CostError, match='no silent crypto fallback'):
        m.row('NEWUSDT')
    with pytest.raises(TypeError):
        C.CostModel(CAL)                                                   # no default symbol mapping
    with pytest.raises(C.CostError, match='one coefficient per cost class'):
        C.CostModel(dict(CAL, c={'crypto': 0.05}), SC)
    with pytest.raises(C.CostError, match='primary rule'):
        C.CostModel(dict(CAL, limit_touch_rule='touch = maker'), SC)
    with pytest.raises(C.CostError, match='fitted'):
        C.CostModel(dict(CAL, fitted='no'), SC)
    assert C.CostModel(CAL, SC).digest == m.digest != C.CostModel(CAL, SC, vol_estimator=C.WILDER_ATR14).digest
    u = world[2]
    assert C.symbol_class_from_universe(u) == {'AAAUSDT': 'crypto', 'XAUUSDT': 'gold-spot'}
    assert C.symbol_class_from_universe({'symbols': [
        {'symbol': 'CLUSDT', 'class': 'gold-commodity', 'subclass': 'energy-oil'},
        {'symbol': 'PAXGUSDT', 'class': 'gold-commodity', 'subclass': 'gold-tokenized'},
        {'symbol': 'ODDUSDT', 'class': 'unclassified', 'subclass': 'x'}]}) == {'CLUSDT': 'commodity',
                                                                               'PAXGUSDT': 'gold-tokenized'}


def test_tokenized_gold_has_its_own_cost_row_in_the_gold_family():
    """Codex 6079042573 P2: PAXG / XAUT are gold (shared regime family) with their own execution/cost row."""
    sc = C.symbol_class_from_universe({'symbols': [
        {'symbol': 'XAUUSDT', 'class': 'gold-commodity', 'subclass': 'gold-spot'},
        {'symbol': 'PAXGUSDT', 'class': 'gold-commodity', 'subclass': 'gold-tokenized'},
        {'symbol': 'XAUTUSDT', 'class': 'gold-commodity', 'subclass': 'gold-tokenized'},
        {'symbol': 'CLUSDT', 'class': 'gold-commodity', 'subclass': 'energy-oil'}]})
    assert sc == {'CLUSDT': 'commodity', 'PAXGUSDT': 'gold-tokenized', 'XAUTUSDT': 'gold-tokenized',
                  'XAUUSDT': 'gold-spot'}
    m = C.CostModel(CAL, sc)
    tok, spot, com = m.row('PAXGUSDT'), m.row('XAUUSDT'), m.row('CLUSDT')
    assert tok.cost_class == 'gold-tokenized' and tok != com and tok != spot and m.row('XAUTUSDT') == tok
    assert m.regime_family('PAXGUSDT') == m.regime_family('XAUTUSDT') == m.regime_family('XAUUSDT') == 'gold'
    assert m.regime_family('CLUSDT') == 'commodity'
    assert tok.funding_cadence_hours is None and spot.funding_cadence_hours == 4      # cadence may differ
    assert m.slip_c('PAXGUSDT') == 0.065 != m.slip_c('XAUUSDT')
    assert 'COST-GOLD-TOKENIZED-UNCALIBRATED' in m.labels('PAXGUSDT') and 'WEEKEND-REFERENCE-GAP' in m.labels('PAXGUSDT')


def test_funding_cadence_detects_missing_and_unexpected_events():
    """Codex 6079042573 P2 repro: rows at T and T+8h both labelled 4h omit the T+4h event."""
    m = C.CostModel(CAL, SC)
    T = ms('2024-02-12') + 3
    F = P.Funding
    with pytest.raises(C.CostError, match='missing or unexpected funding event'):
        m.check_funding_cadence('XAUUSDT', [F(T, -0.0002, 4, T), F(T + 8 * HOUR, -0.0002, 4, T + 8 * HOUR)])
    ok = [F(T + i * 4 * HOUR, -0.0002, 4, T + i * 4 * HOUR) for i in range(3)]
    m.check_funding_cadence('XAUUSDT', ok, start_ms=T, end_ms=T + 9 * HOUR)
    m.check_funding_cadence('XAUUSDT', ok[::-1])                                     # order does not matter
    with pytest.raises(C.CostError, match='no funding phase anchor'):
        m.check_funding_cadence('XAUUSDT', ok, start_ms=T - 5 * HOUR, end_ms=T + 9 * HOUR)
    with pytest.raises(C.CostError, match='missing after the last row'):
        m.check_funding_cadence('XAUUSDT', ok, start_ms=T, end_ms=T + 13 * HOUR)
    with pytest.raises(C.CostError, match='no funding phase anchor'):
        m.check_funding_cadence('XAUUSDT', [], start_ms=T, end_ms=T + 3 * HOUR)       # no rows prove nothing
    m.check_funding_cadence('XAUUSDT', ok, start_ms=T + HOUR, end_ms=T + 9 * HOUR)   # T is the phase anchor
    with pytest.raises(C.CostError, match='outside the covered interval'):
        m.check_funding_cadence('XAUUSDT', ok, start_ms=T, end_ms=T + 7 * HOUR)
    # per-symbol cadence (crypto): an 8h -> 4h schedule switch is accepted, an off-schedule event is not
    m.check_funding_cadence('BTCUSDT', [F(T, 1e-4, 8, T), F(T + 8 * HOUR, 1e-4, 4, T + 8 * HOUR),
                                        F(T + 12 * HOUR, 1e-4, 4, T + 12 * HOUR)])
    with pytest.raises(C.CostError, match='missing or unexpected'):
        m.check_funding_cadence('BTCUSDT', [F(T, 1e-4, 8, T), F(T + 3 * HOUR, 1e-4, 8, T + 3 * HOUR)])
    with pytest.raises(C.CostError, match='missing or unexpected'):
        m.check_funding_cadence('BTCUSDT', [F(T, 1e-4, 8, T), F(T + 24 * HOUR, 1e-4, 8, T + 24 * HOUR)])
    m.check_funding_cadence('BTCUSDT', [F(T, 1e-4, 8, T), F(T + 8 * HOUR + 2_000, 1e-4, 8, T + 8 * HOUR)])


def test_funding_entry_boundary_event_must_be_present():
    """Codex 6088593971 P2 repro: one event at 4h for a covered [0h, 8h] at 4h cadence; the entry event is absent."""
    m = C.CostModel(CAL, SC)
    T = ms('2024-02-12')
    F = P.Funding
    at4 = [F(T + 4 * HOUR, -0.0002, 4, T + 4 * HOUR)]
    with pytest.raises(C.CostError, match='no funding phase anchor'):
        m.check_funding_cadence('XAUUSDT', at4, start_ms=T, end_ms=T + 8 * HOUR)
    with pytest.raises(C.CostError, match='missing or unexpected'):            # anchor at -4h, entry event missing
        m.check_funding_cadence('XAUUSDT', [F(T - 4 * HOUR, -0.0002, 4, T - 4 * HOUR)] + at4,
                                start_ms=T, end_ms=T + 8 * HOUR)
    m.check_funding_cadence('XAUUSDT', [F(T, -0.0002, 4, T)] + at4, start_ms=T, end_ms=T + 8 * HOUR)
    m.check_funding_cadence('XAUUSDT', [F(T - 2 * HOUR, -0.0002, 4, T - 2 * HOUR), F(T + 2 * HOUR, -0.0002, 4,
                                         T + 2 * HOUR), F(T + 6 * HOUR, -0.0002, 4, T + 6 * HOUR)],
                            start_ms=T, end_ms=T + 8 * HOUR)                   # off-phase schedule, anchored


def _drop_funding(ds, symbol, t):
    """Remove the cached funding row at time t (a synthetic missing event; the bytes were already verified)."""
    for f in ds.files('fundingRate', symbol, None):
        rows = ds.rows(f)
        hit = [i for i, r in enumerate(rows) if r.time_ms == t]
        if hit:
            del rows[hit[0]]
            ds._keys[f['path']] = ([r[0] for r in rows], [r.available_ms for r in rows])
            return
    raise AssertionError('no such funding row')


@pytest.mark.parametrize('drop', ['entry', 'inside'])
def test_pit_funding_events_fail_closed_on_a_missing_event(world, tmp_path, drop):
    """Codex 6088593971 P2: the generic PIT path enforces funding continuity itself, entry event included."""
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    w = access(ds, path, pl=None).open_development('2024-02-05T00:00:00Z', '2024-03-04T00:00:00Z', lineage='root')
    t0 = ms('2024-02-12') + 3
    v = w.view(ms('2024-02-14'))
    gpos = w.view(t0).enter('XAUUSDT', 'g')
    assert len(v.funding_events(gpos, t0 + 9 * HOUR)) == 3                   # complete before the drop
    _drop_funding(ds, 'XAUUSDT', t0 if drop == 'entry' else t0 + 4 * HOUR)
    with pytest.raises(P.PITError, match='funding events incomplete'):
        v.funding_events(gpos, t0 + 9 * HOUR)


def test_quantity_is_floored_never_upsized():
    rule = {'step': 0.001, 'min_qty': 0.001, 'min_notional': 5.0, 'tick': 0.1}
    assert C.size(0.0129, 1000.0, rule)['qty'] == 0.012
    r = C.size(0.0049, 1000.0, rule)
    assert r['ok'] is False and r['code'] == 'below_min_notional'


# ------------------------------------------------------------------ intrabar resolver
def mins(start, rows):
    return [P.Bar(start + i * MIN, o, h, lo, c, 1, 1, start + (i + 1) * MIN) for i, (o, h, lo, c) in enumerate(rows)]


SIG = P.Bar(0, 100, 110, 90, 100, 1, 1, 4 * MIN)                   # a 4-minute "signal bar" for brevity


def test_resolver_single_level_and_1m_order():
    r = I.resolve('long', P.Bar(0, 100, 104, 96, 100, 1, 1, 4 * MIN), 4 * MIN, 95, 105, None)
    assert r.outcome == 'none' and r.label is None
    r = I.resolve('long', P.Bar(0, 100, 106, 96, 100, 1, 1, 4 * MIN), 4 * MIN, 95, 105, None)
    assert (r.outcome, r.label, r.ambiguous) == ('target', I.ONE_LEVEL, False)
    m = mins(0, [(100, 101, 99, 100), (100, 106, 99, 105), (105, 105, 94, 95), (95, 100, 90, 100)])
    r = I.resolve('long', SIG, 4 * MIN, 95, 105, m)
    assert (r.outcome, r.label, r.ambiguous, r.unresolved) == ('target', I.RES_1M, True, False)
    assert (r.stop_first, r.target_first) == ('stop', 'target')
    r = I.resolve('short', SIG, 4 * MIN, 105, 95, m)                   # short: first touch is 106 >= stop
    assert (r.outcome, r.label) == ('stop', I.RES_1M)


def test_resolver_same_minute_missing_1m_and_gap_fall_back_to_stop():
    m = mins(0, [(100, 106, 94, 100)] + [(100, 100, 100, 100)] * 3)
    r = I.resolve('long', SIG, 4 * MIN, 95, 105, m)
    assert (r.outcome, r.label, r.unresolved) == ('stop', I.SAME_MINUTE, True)
    for partial in (None, [], m[:3]):
        r = I.resolve('long', SIG, 4 * MIN, 95, 105, partial)
        assert (r.outcome, r.label, r.unresolved) == ('stop', I.NO_1M, True)
    gap = I.resolve('long', P.Bar(0, 94, 110, 90, 100, 1, 1, 4 * MIN), 4 * MIN, 95, 105, None)
    assert (gap.outcome, gap.gap, gap.unresolved) == ('stop', True, False)
    m2 = mins(0, [(100, 101, 99, 100), (94, 106, 90, 100), (100, 100, 100, 100), (100, 100, 100, 100)])
    r = I.resolve('long', SIG, 4 * MIN, 95, 105, m2)
    assert (r.outcome, r.gap, r.label) == ('stop', True, I.RES_1M)


def test_resolver_summary_parks_above_five_percent():
    amb = I.resolve('long', SIG, 4 * MIN, 95, 105, None)
    clean = I.resolve('long', P.Bar(0, 100, 106, 96, 100, 1, 1, 4 * MIN), 4 * MIN, 95, 105, None)
    s = I.summarize([amb] * 5 + [clean] * 95, 100)
    assert s['unresolved_rate'] == 0.05 and not s['park'] and s['by_label'][I.NO_1M] == 5
    assert I.summarize([amb] * 6 + [clean] * 94, 100)['park']


def test_resolver_on_the_pit_view_minutes(world, tmp_path):
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    w = access(ds, path, pl=None).open_development('2024-02-05T00:00:00Z', '2024-03-04T00:00:00Z', lineage='root')
    t = ms('2024-02-20') + H4
    v = w.view(t)
    sig = v.bars('AAAUSDT', '4h', 1)[-1]
    assert len(v.minute_bars('AAAUSDT', sig.open_ms, sig.available_ms)) == 240
    assert v.minute_bars('XAUUSDT', sig.open_ms, sig.available_ms) == ()          # no 1m for gold here -> NO_1M
    with pytest.raises(P.PITError, match='not closed'):
        v.minute_bars('AAAUSDT', sig.available_ms, sig.available_ms + H4)


# ------------------------------------------------------------------ report envelope
def test_envelope_write_once_and_report_needs_the_runner_attestation(world, tmp_path, repo, monkeypatch):
    """Codex 6079042573 P1: only `pit.evaluate` (sandboxed, perturbed, ledger-recorded) can produce a report."""
    ds = ds_of(world)
    pl = plan()
    evaluator(os.path.join(repo, 'strategy'), HONEST + REPORTING)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'evaluator')
    times = train_times(4)
    env = R.freeze_run(repo=repo, entrypoint=ep('strategy/ev.py'), schedule=times,
                       eval_files=['strategy/cand.py', 'strategy/ev.py'], config={'k': 1}, seeds=[1, 2],
                       **dict(ident(ds, pl), split='train', window=pl.window('train')))
    assert env['run_digest'] == R.run_digest(env['run']) and env['format'] == 'zb-research-run/2'
    assert env['run']['eval']['entrypoint'] == ep('strategy/ev.py') and env['run']['eval']['schedule']['n'] == 4
    R.write_envelope(str(tmp_path), env)
    R.write_envelope(str(tmp_path), env)                                         # identical: no-op
    assert R.load_envelope(str(tmp_path), env['run_digest']) == env
    with pytest.raises(R.ReportError):
        R.write_envelope(str(tmp_path), dict(env, run_digest='0' * 64))
    path, _ = dirs(tmp_path)
    w = access(ds, path, pl, repo=repo).open('train', envelope=env, lineage='root')
    ev = os.path.join(repo, 'strategy', 'ev.py')
    out = P.evaluate(w, ev, 'run', times, summary='summarize')
    full = out.results
    assert full['long']['base']['counts']['trades'] == 4
    rep = R.make_report(env, full, out.attestation, path)
    assert rep['report_digest'] and rep['run_digest'] == env['run_digest'] and rep['results'] == full
    assert rep['attestation_digest'] == out.attestation_digest and rep['attestation']['evaluator']['path'] ==         'strategy/ev.py'
    sec = {s: {k: None for k in keys} for s, keys in R.SECTIONS.items()}
    with pytest.raises(R.ReportError, match='every stress row'):
        R.make_report(env, {'long': {'base': sec}}, out.attestation, path)
    with pytest.raises(R.ReportError, match='keys must be exactly'):
        R.make_report(env, {'short': {row: dict(sec, ci={}) for row in C.STRESS}}, out.attestation, path)
    # Codex 6089002043 P1: an unrelated payload, a second function of the same file, an altered / subset schedule
    with pytest.raises(R.ReportError, match='not the payload the frozen summary produced'):
        R.make_report(env, {'long': {row: sec for row in C.STRESS}}, out.attestation, path)
    other_fn = P.evaluate(w, ev, 'run2', times, summary='summarize')
    with pytest.raises(R.ReportError, match='entrypoint'):
        R.make_report(env, other_fn.results, other_fn.attestation, path)
    other_sum = P.evaluate(w, ev, 'run', times, summary='summarize2')
    with pytest.raises(R.ReportError, match='entrypoint'):
        R.make_report(env, other_sum.results, other_sum.attestation, path)
    subset = P.evaluate(w, ev, 'run', times[:2], summary='summarize')
    with pytest.raises(R.ReportError, match='schedule'):
        R.make_report(env, subset.results, subset.attestation, path)
    shifted = P.evaluate(w, ev, 'run', [t + H4 for t in times], summary='summarize')
    with pytest.raises(R.ReportError, match='schedule'):
        R.make_report(env, shifted.results, shifted.attestation, path)
    with pytest.raises(P.PITError, match='strictly increasing'):
        P.evaluate(w, ev, 'run', times[::-1], summary='summarize')
    # bypass-runner negative controls
    with pytest.raises(TypeError):
        R.make_report(env, full)                                                 # no attestation at all
    with pytest.raises(R.ReportError, match='not a runner attestation'):
        R.make_report(env, full, {'outputs': [w.view(t).bars('AAAUSDT', '4h', 1) for t in times]}, path)
    nop = P.evaluate(w, ev, 'run', times, summary='summarize', perturb=False)
    with pytest.raises(R.ReportError, match='future-perturbation'):
        R.make_report(env, full, nop.attestation, path)
    with pytest.raises(R.ReportError, match='no matching evaluation record'):
        R.make_report(env, full, dict(out.attestation, outputs_digest='0' * 64), path)
    # Codex 6093381880 P1: the attestation's execution environment must EQUAL the frozen run.code one (tamper)
    xe = out.attestation['execution_environment']
    assert xe == env['run']['code']['execution_environment']
    for k, v in (('python', '0.0.0'), ('hexversion', 1), ('cache_tag', 'cpython-00'), ('executable_sha256', 'f' * 64),
                 ('os', 'plan9'), ('platform', 'other-arch'), ('pointer_bits', 16), ('cpu_count', xe['cpu_count'] + 1),
                 ('implementation', 'pypy'), ('version', 'x'), ('install_digest', '0' * 64)):
        bad = dict(out.attestation, execution_environment=dict(xe, **{k: v}))
        with pytest.raises(R.ReportError, match=rf"execution environment of the evaluator processes differs.*'{k}'"):
            R.make_report(env, full, bad, path)
    with pytest.raises(R.ReportError, match='execution environment of the evaluator processes is missing'):
        R.make_report(env, full, {k: v for k, v in out.attestation.items() if k != 'execution_environment'}, path)
    with pytest.raises(R.ReportError, match='missing or malformed'):
        R.make_report(env, full, dict(out.attestation, execution_environment=dict(xe, extra=1)), path)
    # a tampered frozen identity: edited in place breaks run_digest; re-sealed it is another run
    t_code = dict(env['run']['code'], execution_environment=dict(xe, cpu_count=xe['cpu_count'] + 1))
    t_run = dict(env['run'], code=t_code)
    with pytest.raises(R.ReportError, match='run_digest does not match'):
        R.make_report(dict(env, run=t_run), full, out.attestation, path)
    with pytest.raises(R.ReportError, match='another run'):
        R.make_report(R.envelope(t_run), full, out.attestation, path)
    # the process sealing the report runs in another environment: fails closed even with a genuine attestation
    monkeypatch.setattr(R.SB, 'execution_environment', lambda: dict(xe, platform='other-arch'))
    with pytest.raises(R.ReportError, match=r"process sealing the report differs.*'platform'"):
        R.make_report(env, full, out.attestation, path)
    monkeypatch.undo()
    assert R.make_report(env, full, out.attestation, path)['report_digest'] == rep['report_digest']
    no_sum = P.evaluate(w, ev, 'run', times)
    with pytest.raises(R.ReportError, match='entrypoint'):
        R.make_report(env, full, no_sum.attestation, path)
    stray = P.evaluate(w, evaluator(tmp_path / 'stray', HONEST + REPORTING), 'run', times, summary='summarize')
    with pytest.raises(R.ReportError, match='hashed evaluation files'):
        R.make_report(env, full, stray.attestation, path)
    dev = access(ds, path, pl, repo=repo).open('walk_forward[0]')
    with pytest.raises(R.ReportError, match='another run'):
        R.make_report(env, full, P.evaluate(dev, ev, 'run', [dev.hi], summary='summarize').attestation, path)
    dirty = dict(env['run'], code=dict(env['run']['code'], dirty=True))
    assert R.sealable(R.envelope(dirty)) == ['dirty working tree']


def test_runtime_never_imports_research_code():
    names = {'manifest', 'ledger', 'universe', 'pit', 'costs', 'splits', 'intrabar', 'report', 'sandbox'}
    files = glob.glob(os.path.join(ROOT, '*.py')) + glob.glob(os.path.join(ROOT, 'newcore', '**', '*.py'), recursive=True)
    bad = []
    for f in files:
        with open(f, encoding='utf-8') as fh:
            tree = ast.parse(fh.read())
        for n in ast.walk(tree):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                [n.module or ''] if isinstance(n, ast.ImportFrom) and not n.level else []
            bad += [(f, m) for m in mods if m.split('.')[0] in names or m.startswith('tools.research')]
    assert bad == []


# ------------------------------------------------------------------ R3 data integrity: overlapping coverage refuses
# (Codex #53 6094780810 Q1): a Dataset must never serve two manifest files of one (series, symbol, interval) whose
# coverage overlaps; there is no source preference - choosing a source is the manifest/universe artifact's job.
def _csv_world(tmp_path, spans):
    """A survivor-only local-CSV store: one BTCUSDT_1h.csv per (dir, first_hour, last_hour) in `spans`."""
    store = tmp_path / 'csv'
    for d, a, b in spans:
        p = store / d / 'BTCUSDT_1h.csv'
        p.parent.mkdir(parents=True, exist_ok=True)
        t0 = ms('2024-01-01')
        rows = [datetime.fromtimestamp((t0 + h * HOUR) / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                + ',1,2,0.5,1.5,10' for h in range(a, b + 1)]
        p.write_bytes(('t,o,h,l,c,v\n' + '\n'.join(rows) + '\n').encode())
    m = M.build(sorted({d for d, _, _ in spans}), manifest_id='fx-overlap', source_class='legacy-unverified',
                base=str(store), data_root='repo', survivor_only=True)
    return str(store), m


@pytest.mark.parametrize('spans,lo,hi', [
    ((('a', 0, 47), ('b', 24, 71)), '2024-01-02T00:00:00Z', '2024-01-02T23:00:00Z'),     # partial overlap
    ((('a', 0, 47), ('b', 0, 47)), '2024-01-01T00:00:00Z', '2024-01-02T23:00:00Z'),      # duplicate (identical range)
    ((('a', 0, 71), ('b', 24, 47)), '2024-01-02T00:00:00Z', '2024-01-02T23:00:00Z'),     # nested
    ((('a', 0, 47), ('b', 47, 71)), '2024-01-02T23:00:00Z', '2024-01-02T23:00:00Z'),     # one shared bar
    ((('a', 0, 23), ('b', 24, 47), ('c', 30, 30)), '2024-01-02T06:00:00Z', '2024-01-02T06:00:00Z'),  # 3rd file
])
def test_dataset_refuses_overlapping_coverage(tmp_path, spans, lo, hi):
    store, m = _csv_world(tmp_path, spans)
    with pytest.raises(P.OverlapError) as e:
        P.Dataset(m, store, None)
    msg = str(e.value)
    assert isinstance(e.value, P.PITError) and msg.startswith('klines BTCUSDT 1h: ')
    paths = re.findall(r'manifest files (\S+) and (\S+) overlap', msg)
    assert len(paths) == 1 and all(p.endswith('/BTCUSDT_1h.csv') for p in paths[0]) and paths[0][0] != paths[0][1]
    assert f'{lo} .. {hi}' in msg


def test_dataset_loads_exactly_adjacent_coverage(tmp_path):
    store, m = _csv_world(tmp_path, (('a', 0, 23), ('b', 24, 47), ('c', 48, 71)))
    ds = P.Dataset(m, store, None)
    fs = ds.files('klines', 'BTCUSDT', '1h')
    assert [f['path'] for f in fs] == ['a/BTCUSDT_1h.csv', 'b/BTCUSDT_1h.csv', 'c/BTCUSDT_1h.csv']
    assert [len(ds.rows(f)) for f in fs] == [24, 24, 24]


def test_dataset_rows_refuse_misdeclared_coverage(tmp_path):
    """The overlap check trusts the declared span, so loading re-checks it against the hashed bytes."""
    store, m = _csv_world(tmp_path, (('a', 0, 23),))
    m = json.loads(json.dumps(m))
    m['files'][0]['last_open_ms'] -= HOUR                          # a hand-edited manifest hiding its last bar
    m['digest'] = M.digest_of(m)
    with pytest.raises(P.PITError, match='!= manifest coverage'):
        P.Dataset(m, store, None).rows(m['files'][0])


def test_committed_archive_manifest_has_no_overlap():
    """Manifest metadata only (no market data): the archive manifest the R2 universe is built on refuses nothing."""
    m = M.load(os.path.join(ROOT, 'research_evidence', 'manifests', 'binance-um-archive-v1.json.gz'))
    g = {}
    for f in m['files']:
        g.setdefault((f.get('series', 'klines'), f['symbol'], f['interval']), []).append(f)
    for k, fs in g.items():
        P._refuse_overlap(k, fs)
