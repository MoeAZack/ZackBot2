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


def test_evaluator_gets_only_the_view_and_a_peeking_control_is_caught(world, tmp_path):
    """Codex R3 P1: evaluation runs through `evaluate`, which hands out the frozen View only and re-runs every decision
    with all not-yet-available rows perturbed; an evaluator that bypasses the view fails closed."""
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    w = access(ds, path).open('train', lineage='root')
    rnd = random.Random(7)
    lo, hi = plan().decision_range('train')
    times = sorted({lo + rnd.randrange((hi - lo) // H4 + 1) * H4 for _ in range(40)})
    seen = []

    def honest(v):
        seen.append(v)
        b = v.bars('AAAUSDT', '4h', 15)
        return (b[-1].close > sum(x.close for x in b) / 15, round(C.slip_bps(b, 0.05), 9))

    out = P.evaluate(w, honest, times)
    assert len(out) == len(times) and all(type(v) is P.View for v in seen)
    v = seen[0]
    assert type(v).__slots__ == ('_cap', 't') and not any(hasattr(v, a) for a in ('ds', '_w', '_ds', 'window', 'lo'))
    assert not isinstance(v._cap, (P.Window, P.Dataset)) and not hasattr(v._cap, '__dict__')

    def peeking(v):                           # negative control: reaches the dataset behind the capability
        src = P._SOURCES[v._cap]._ds
        rows = [r for f in src.files('klines', 'AAAUSDT', '4h') for r in src.rows(f)]
        nxt = [r for r in rows if r.open_ms == v.t]
        return nxt[0].close if nxt else None

    for t in times[:5]:
        with pytest.raises(P.PITError, match='bypassed'):
            P.evaluate(w, peeking, [t])
    with pytest.raises(P.PITError, match='Window opened through Access'):
        P.evaluate(v, honest, times)


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


def ident(ds, pl, *, family='fam_x', window=None, candidate_id='c.v1'):
    return {'family': family, 'candidate_id': candidate_id, 'split': 'holdout', 'window': window or pl.window('holdout'),
            'books': list(ds.books), 'manifest_digest': ds.digest, 'universe_digest': ds.universe_digest,
            'split_plan_digest': pl.digest, 'cost_model_digest': 'c' * 64, 'slip_cal_digest': 'd' * 64,
            'author': 't', 'cairo_date': '2026-10-09'}


def make_env(ds, pl, repo, *, config=None, **kw):
    return R.freeze_run(repo=repo, eval_files=['strategy/cand.py'], config=config or {'k': 1}, seeds=[1, 2],
                        **ident(ds, pl, **kw))


def reveal(path, env, kind='holdout_reveal'):
    r = env['run']
    L.append(path, kind=kind, candidate_id=r['candidate_id'], split='holdout', window=r['window'], author='t',
             cairo_date='2026-10-09', manifest_digest=r['manifest_digest'], run_digest=env['run_digest'],
             detail={'eval_digest': r['eval_digest']})


def test_holdout_is_sealed_without_the_atomic_reveal(world, tmp_path, repo):
    ds = ds_of(world)
    path, runs = dirs(tmp_path)
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
def test_holdout_guard_refuses_foreign_envelopes(world, tmp_path, repo, kw, msg):
    ds = ds_of(world)
    path, runs = dirs(tmp_path)
    pl = plan()
    acc = access(ds, path, pl, repo=repo)
    acc.open('train', lineage='root')
    env = make_env(ds, pl, repo, **kw)
    R.write_envelope(runs, env)
    with pytest.raises(P.PITError, match=msg):
        acc.open('holdout', envelope=env)


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
    fake['eval_digest'] = R.eval_digest(fake['eval']['files'], fake['eval']['config'], fake['seeds'])
    assert any('evaluation files changed' in x for x in R.verify_code(R.envelope(fake), repo))
    assert R.verify_code(R.envelope(dict(run, code=dict(run['code'], libs={'numpy': '9'}))), repo) == [
        'dependency set differs from the canonical frozen set']
    no_core = [f for f in run['eval']['files'] if f['path'] != 'tools/research/pit.py']
    with pytest.raises(R.ReportError, match='CORE_EVAL_FILES'):
        R.envelope(dict(run, eval=dict(run['eval'], files=no_core)))
    with pytest.raises(R.ReportError, match='tracked'):
        R.freeze_run(repo=repo, eval_files=['strategy/untracked.py'], config={}, seeds=[], **ident(ds, pl))
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


def test_moved_or_dirty_checkout_cannot_open_the_holdout(world, tmp_path, repo):
    ds = ds_of(world)
    path, runs = dirs(tmp_path)
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


def test_reveal_components_bind_the_complete_identity_cross_candidate_rejected(world, tmp_path, repo):
    """Codex R3 P2: a component naming another candidate (with its own valid envelope) cannot join the reveal group."""
    ds = ds_of(world)
    path, runs = dirs(tmp_path)
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


# ------------------------------------------------------------------ costs + slip-v1
CAL = {'id': 'slip-cal-v1', 'target': 'per-side fill-vs-reference bps', 'source_series': 'synthetic fixture',
       'estimator': 'median ratio', 'loss': 'absolute', 'pooling': 'per cost class', 'fallback': 'floor 2 bps',
       'limit_touch_rule': C.LIMIT_TOUCH_RULE,
       'calibration_window': {'start': '2022-01-01T00:00:00Z', 'end': '2022-07-01T00:00:00Z'}, 'fitted': False,
       'c': {'crypto': 0.05, 'gold': 0.06, 'commodity': 0.07, 'equity': 0.08, 'fx': 0.09}}
SC = {'BTCUSDT': 'crypto', 'XAUUSDT': 'gold', 'CLUSDT': 'commodity', 'TSLAUSDT': 'equity', 'USDBRLUSDT': 'fx'}


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
    assert gold.cost_class == 'gold' and gold.status == 'PROVISIONAL' and gold.funding_cadence_hours == 4
    assert (gold.taker, gold.maker, gold.fee_tier) == (crypto.taker, crypto.maker, crypto.fee_tier)   # Binance tier
    assert m.slip_c('XAUUSDT') == 0.06 != m.slip_c('BTCUSDT')                                        # own slip c
    assert m.labels('XAUUSDT') == [C.FUNDING_MARK_LABEL, 'SLIP-VOL-TR-SMA14', 'COST-GOLD-PROVISIONAL',
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
    assert C.symbol_class_from_universe(u) == {'AAAUSDT': 'crypto', 'XAUUSDT': 'gold'}
    assert C.symbol_class_from_universe({'symbols': [
        {'symbol': 'CLUSDT', 'class': 'gold-commodity', 'subclass': 'energy-oil'},
        {'symbol': 'PAXGUSDT', 'class': 'gold-commodity', 'subclass': 'gold-tokenized'},
        {'symbol': 'ODDUSDT', 'class': 'unclassified', 'subclass': 'x'}]}) == {'CLUSDT': 'commodity',
                                                                               'PAXGUSDT': 'commodity'}


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
def test_envelope_write_once_and_report_shape(world, tmp_path, repo):
    ds = ds_of(world)
    env = make_env(ds, plan(), repo)
    assert env['run_digest'] == R.run_digest(env['run']) and env['format'] == 'zb-research-run/2'
    R.write_envelope(str(tmp_path), env)
    R.write_envelope(str(tmp_path), env)                                         # identical: no-op
    assert R.load_envelope(str(tmp_path), env['run_digest']) == env
    with pytest.raises(R.ReportError):
        R.write_envelope(str(tmp_path), dict(env, run_digest='0' * 64))
    sec = {s: {k: None for k in keys} for s, keys in R.SECTIONS.items()}
    rep = R.make_report(env, {'long': {row: sec for row in C.STRESS}})
    assert rep['report_digest'] and rep['run_digest'] == env['run_digest']
    with pytest.raises(R.ReportError, match='every stress row'):
        R.make_report(env, {'long': {'base': sec}})
    with pytest.raises(R.ReportError, match='keys must be exactly'):
        R.make_report(env, {'short': {row: dict(sec, ci={}) for row in C.STRESS}})
    dirty = dict(env['run'], code=dict(env['run']['code'], dirty=True))
    assert R.sealable(R.envelope(dirty)) == ['dirty working tree']


def test_runtime_never_imports_research_code():
    names = {'manifest', 'ledger', 'universe', 'pit', 'costs', 'splits', 'intrabar', 'report'}
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
