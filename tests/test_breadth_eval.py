"""breadth_eval (zb-breadth-eval/1): refusals before the window closes / without the pinned Y / on a dirty or wrong
HEAD / on a second evaluation, packet binding (Cowork 6201874565), and the end-to-end computation on SYNTHETIC packets
with an injected clock. No market data is read or fetched: the wired collector and runner calls are replaced by
tripwires that fail the test if ever reached."""
import copy
import json
import os
import shutil

import pytest

import test_breadth_prereg as TP
from test_breadth_prereg import BP, _git, _head, _commit_ledger, repo  # noqa: F401  (fixture)

import breadth_eval as E                                                                    # noqa: E402
import run_vwaprev_candidate as VC                                                          # noqa: E402

AFTER = E.HOLDOUT_END_MS + 3_600_000
SYMS = [x['symbol'] for x in VC.frozen_universe()['symbols']]
INC = ['AKEUSDT', 'BRUSDT']
RUN = [s for s in SYMS if s not in INC]
DEV_RUNNER_COMMIT = 'c8c7b927ee575ba71a9724ce84d23f72774b07b8'     # the commit the #114 development packets ran at


@pytest.fixture(autouse=True)
def tripwires(monkeypatch):
    import collect_klines_research as K
    import run_breadth_candidate as B

    def boom(*a, **k):
        raise AssertionError('a data path was reached')
    monkeypatch.setattr(K, 'collect', boom)
    monkeypatch.setattr(B, 'run', boom)
    monkeypatch.setattr(K, 'fetch', boom)


def manifest(window=None):
    w = window or {'start': BP.HOLDOUT['start'], 'end': BP.HOLDOUT['end'], 'end_exclusive': True}
    return {'format': E.MANIFEST_FORMAT, 'interval': '4h', 'window': w,
            'files': {s: {'sha256': f'{i + 1:064x}', 'status': 'complete'}
                      for i, s in enumerate(sorted(set(SYMS) | {VC.BTC}))}}


def seal(p):
    p['stats']['aggregate'] = dict(E.aggregate_from_trades(p['trades']), win_rate=0.5, max_dd_equity=-0.1)
    p['packet_sha256'] = E.packet_sha256(p)
    return p


def synth_packet(h, pre, i, y, man, days=None):
    days = days or [f'2027-0{1 + d // 28}-{1 + d % 28:02d}' for d in range(40)]
    strong = i % 3 == 0                                   # every third hypothesis has a strong positive edge
    side = 1 if h['side'] == 'long' else -1
    trades = []
    for d, day in enumerate(days):
        pnl = (2.0 if d % 4 else -1.0) if strong else (1.0 if (d * 7 + i) % 2 else -1.0)
        trades.append({'R': pnl / 10, 'pnl': pnl, 'gross': pnl + 0.1, 'fees': 0.08, 'funding': 0.02, 'side': side,
                       'symbol': RUN[d % len(RUN)], 'why': 'signal', 't_in': f'{day}T04:00:00Z',
                       't_out': f'{day}T08:00:00Z'})
    return seal({'format': pre['config']['runner_format'], 'status': 'ok', 'commit': y, 'tree': 'clean',
                 'strategy': {'key': h['key'], 'mgmt_mode': 'defaults', 'mgmt_override': {}, 'params': h['params'],
                              'mgmt': h['mgmt']},
                 'side': h['side'], 'window': {'start': BP.HOLDOUT['start'], 'end': BP.HOLDOUT['end'], 'tf': '4h'},
                 'runner_sha256': {'run_breadth_candidate.py':
                                   pre['code']['sha256']['tools/research/run_breadth_candidate.py']},
                 'costs': {'fee_taker': 0.0005, 'slip': 0.0002, 'fund_per_bar': 5.000000000000001e-05},
                 'universe': {'digest': pre['universe']['digest'], 'requested': SYMS, 'run': RUN,
                              'incomplete': [{'symbol': s} for s in INC]},
                 'cache': {'completeness': {s: {'complete': True} for s in RUN},
                           'files': {s: {'sha256': man['files'][s]['sha256']} for s in RUN + [VC.BTC]}},
                 'stats': {}, 'bust': {'busted': False}, 'trades': trades})


def all_packets(pre, y, man):
    return {h['id']: synth_packet(h, pre, i, y, man) for i, h in enumerate(pre['hypotheses'])}


def write_inputs(d, pre, y, man=None):
    man = man or manifest()
    os.makedirs(d, exist_ok=True)
    for i, h in enumerate(pre['hypotheses']):
        with open(os.path.join(d, f"{h['key']}_{h['side']}_defaults.json"), 'w', encoding='utf-8') as f:
            json.dump(synth_packet(h, pre, i, y, man), f)
    mp = os.path.join(d, 'manifest.json')
    with open(mp, 'w', encoding='utf-8') as f:
        json.dump(man, f)
    return d, mp


@pytest.fixture
def registered(repo, monkeypatch):
    acc = _head(repo)
    BP.register(repo, accepted_commit=acc)
    _commit_ledger(repo)
    return repo, acc, _head(repo)                       # evaluation runs from the pinned Y, never from X


def _commit_spend(r):
    _commit_ledger(r, date='2027-10-18T02:00:00Z')      # R: Y's only child, ledger-only
    return _head(r)


# ------------------------------------------------------------------ refusals
@pytest.mark.parametrize('now', [E.HOLDOUT_START_MS, E.HOLDOUT_END_MS - 1, BP.HOLDOUT_START_MS - 1])
def test_refuses_while_the_window_is_open(registered, monkeypatch, tmp_path, now):
    r, acc, y = registered
    monkeypatch.setattr(E, 'CLOCK', lambda: now)
    o = str(tmp_path / 'o')
    for call in (lambda: E.collect_holdout(r, registration=y, cache=str(tmp_path / 'c'), manifest=str(tmp_path / 'm')),
                 lambda: E.run_holdout_packets(r, registration=y, cache=str(tmp_path / 'c'), out_dir=o),
                 lambda: E.spend(r, registration=y, packets_dir=o, manifest='m', out_dir=o),
                 lambda: E.evaluate(r, registration=y, packets_dir=str(tmp_path / 'p'), manifest='m', out_dir=o)):
        with pytest.raises(E.EvalError, match='holdout window is open'):
            call()
    assert not os.path.exists(o) and not os.path.exists(tmp_path / 'c')
    assert E.main(['collect', '--registration', y, '--cache', str(tmp_path / 'c'), '--manifest', 'x']) == 2


def test_refuses_dirty_or_wrong_head(registered, monkeypatch, tmp_path):
    r, acc, y = registered
    monkeypatch.setattr(E, 'CLOCK', lambda: AFTER)
    _git(r, 'checkout', '-q', acc)                                  # HEAD == X
    with pytest.raises(E.EvalError, match='not the registration commit Y'):
        E.collect_holdout(r, registration=y, cache=str(tmp_path / 'c'), manifest=str(tmp_path / 'm'))
    with pytest.raises(E.EvalError, match='no registration'):     # X pinned as if it were Y
        E.collect_holdout(r, registration=acc, cache=str(tmp_path / 'c'), manifest=str(tmp_path / 'm'))
    _git(r, 'checkout', '-q', y)
    _git(r, 'commit', '-q', '--allow-empty', '-m', 'after Y')     # HEAD != Y
    for stage in ('collect', 'packets', 'spend', 'evaluate'):
        with pytest.raises(E.EvalError, match='not the registration commit Y'):
            E.preflight(r, y, stage=stage, out_dir=str(tmp_path / 'o'))
    _git(r, 'checkout', '-q', y)
    with open(os.path.join(r, 'strategies.py'), 'ab') as f:
        f.write(b'# dirty\n')
    with pytest.raises(E.EvalError, match='dirty worktree'):
        E.run_holdout_packets(r, registration=y, cache=str(tmp_path / 'c'), out_dir=str(tmp_path / 'o'))


def test_refuses_without_registration(repo, monkeypatch, tmp_path):
    monkeypatch.setattr(E, 'CLOCK', lambda: AFTER)
    with pytest.raises(E.EvalError, match='no registration'):
        E.preflight(repo, 'HEAD', stage='collect')
    with pytest.raises(E.EvalError, match='does not resolve'):
        E.preflight(repo, 'v-no-such-tag', stage='collect')


def test_holdout_window_guard_is_exact_and_restored():
    old = (VC.check_window, VC.SEALED_CUTOFF)
    with E.holdout_window():
        assert VC.check_window(E.HOLDOUT_SPEC) == (E.HOLDOUT_START_MS, E.HOLDOUT_END_MS,
                                                   E.HOLDOUT_START_MS - 220 * E.TF_MS)
        with pytest.raises(E.EvalError):
            VC.check_window('2026-10-19T00:00:00Z/2027-01-01T00:00:00Z')
    assert (VC.check_window, VC.SEALED_CUTOFF) == old
    with pytest.raises(VC.RunnerError):
        VC.check_window(E.HOLDOUT_SPEC)


# ------------------------------------------------------------------ one evaluation, end to end on synthetic packets
def test_one_evaluation_end_to_end(registered, monkeypatch, tmp_path):
    r, acc, y = registered
    monkeypatch.setattr(E, 'CLOCK', lambda: AFTER)
    pre, _ = BP.load_prereg(r)
    pk, mp = write_inputs(str(tmp_path / 'packets'), pre, y)
    out = str(tmp_path / 'eval')
    with pytest.raises(E.EvalError, match='record the evaluation spend first'):      # no verdict before the spend
        E.evaluate(r, registration=y, packets_dir=pk, manifest=mp, out_dir=out)
    rec = E.spend(r, registration=y, packets_dir=pk, manifest=mp, out_dir=out)
    assert rec['kind'] == 'window_spent' and rec['split'] == 'holdout' and rec['detail']['registration_commit'] == y
    assert not os.path.exists(out)                                                     # nothing computed yet
    rc = _commit_spend(r)
    assert BP.verify_registration(r) == []
    # a second spend: at R, or from a fresh checkout of Y in a clone that holds R
    with pytest.raises(E.EvalError, match='not the registration commit Y'):
        E.spend(r, registration=y, packets_dir=pk, manifest=mp, out_dir=str(tmp_path / 'other'))
    _git(r, 'checkout', '-q', y)
    for stage in ('collect', 'packets', 'spend'):
        with pytest.raises(E.EvalError, match='already holds its evaluation spend'):
            E.preflight(r, y, stage=stage)
    _git(r, 'checkout', '-q', rc)
    # other inputs than the spend bound
    alt = str(tmp_path / 'alt')
    shutil.copytree(pk, alt)
    h0 = pre['hypotheses'][1]
    fn = os.path.join(alt, f"{h0['key']}_{h0['side']}_defaults.json")
    with open(fn, encoding='utf-8') as f:
        p = json.load(f)
    p['trades'][0]['pnl'] += 5.0
    with open(fn, 'w', encoding='utf-8') as f:
        json.dump(seal(p), f)
    with pytest.raises(E.EvalError, match='not the inputs the evaluation spend recorded'):
        E.evaluate(r, registration=y, packets_dir=alt, manifest=mp, out_dir=out)
    with pytest.raises(E.EvalError, match='output location'):                        # a new --out is refused
        E.evaluate(r, registration=y, packets_dir=pk, manifest=mp, out_dir=str(tmp_path / 'eval2'))
    doc = E.evaluate(r, registration=y, packets_dir=pk, manifest=mp, out_dir=out)
    rows = doc['hypotheses']
    assert len(rows) == 12 and doc['n_flips'] == 100_000 and doc['binding']['accepted_commit'] == acc
    assert doc['binding']['y'] == y and doc['spend']['detail']['eval_digest'] == doc['eval_digest']
    strong = [h['id'] for i, h in enumerate(pre['hypotheses']) if i % 3 == 0]
    for i in strong:
        assert rows[i]['holm_reject'] and rows[i]['verdict'] == 'PASS', rows[i]
    for i in set(rows) - set(strong):
        assert rows[i]['verdict'].startswith('FAIL') and 'holm' in rows[i]['verdict']
    assert all(r_['data_complete'] and r_['incomplete'] == INC for r_ in rows.values())
    with open(os.path.join(out, E.OUT_NAME), encoding='utf-8') as f:
        assert json.load(f)['hypotheses'] == json.loads(json.dumps(rows))
    with pytest.raises(E.EvalError, match='already exists'):                          # one evaluation
        E.evaluate(r, registration=y, packets_dir=pk, manifest=mp, out_dir=out)
    with pytest.raises(E.EvalError, match='output location'):
        E.evaluate(r, registration=y, packets_dir=pk, manifest=mp, out_dir=str(tmp_path / 'eval3'))
    again = E.evaluate_packets(all_packets(pre, y, manifest()), pre, SYMS, y=y, manifest=manifest())
    assert again['hypotheses'] == json.loads(json.dumps(rows))                         # deterministic


# ------------------------------------------------------------------ packet binding (Cowork 6201874565 #1)
Y = 'a' * 40


@pytest.mark.parametrize('mut, msg', [
    (lambda p: p['window'].update(start='2026-01-05T00:00:00Z'), 'not the holdout'),
    (lambda p: p['strategy'].update(mgmt_override={'stop_atr': 3.0}), 'mgmt'),
    (lambda p: p['strategy'].update(params={'k': 6}), 'params'),
    (lambda p: p['costs'].update(fee_taker=0.0004), 'costs'),
    (lambda p: p['runner_sha256'].update({'run_breadth_candidate.py': '0' * 64}), 'runner sha256'),
    (lambda p: p['universe'].update(digest='0' * 64), 'universe digest'),
    (lambda p: p.update(commit='b' * 40), 'registration commit Y'),
    (lambda p: p.update(tree='dirty'), 'registration commit Y'),
    (lambda p: p['trades'][3].update(t_out='2027-10-18T00:00:00Z'), 'outside the holdout'),
    (lambda p: p['trades'][0].update(t_in='2026-10-18T20:00:00Z'), 'outside the holdout'),
    (lambda p: p['cache']['files'][RUN[2]].update(sha256='f' * 64), 'cache file digests'),
])
def test_packet_binding_refused(mut, msg):
    pre, _ = BP.load_prereg(TP.ROOT)
    packets = all_packets(pre, Y, manifest())
    p = packets[pre['hypotheses'][8]['id']]
    mut(p)
    p['packet_sha256'] = E.packet_sha256(p)            # re-sealed: each binding check stands on its own
    with pytest.raises(E.EvalError, match=msg):
        E.evaluate_packets(packets, pre, SYMS, y=Y, manifest=manifest(), n_flips=10)


@pytest.mark.parametrize('mut, msg', [
    (lambda p: p['stats']['aggregate'].update(pnl=p['stats']['aggregate']['pnl'] + 1.0), 'does not recompute'),
    (lambda p: p['stats']['aggregate'].update(trades=41), 'does not recompute'),
    (lambda p: p['stats']['aggregate'].update(max_dd_r=0.0), 'does not recompute'),
    (lambda p: p['trades'][0].update(pnl=99.0), 'does not recompute'),
])
def test_aggregate_must_recompute_from_the_trades(mut, msg):
    pre, _ = BP.load_prereg(TP.ROOT)
    packets = all_packets(pre, Y, manifest())
    p = packets[pre['hypotheses'][4]['id']]
    mut(p)
    p['packet_sha256'] = E.packet_sha256(p)
    with pytest.raises(E.EvalError, match=msg):
        E.evaluate_packets(packets, pre, SYMS, y=Y, manifest=manifest(), n_flips=10)


def test_edited_packet_without_reseal_refused():
    pre, _ = BP.load_prereg(TP.ROOT)
    packets = all_packets(pre, Y, manifest())
    packets[pre['hypotheses'][0]['id']]['status'] = 'ok '
    with pytest.raises(E.EvalError, match='packet_sha256 does not recompute'):
        E.evaluate_packets(packets, pre, SYMS, y=Y, manifest=manifest(), n_flips=10)


@pytest.mark.parametrize('attacker_sets_commit_and_cache', [False, True])
def test_relabelled_dev_packet_refused(attacker_sets_commit_and_cache):
    """A development-window packet (dev runner commit, 2025 trades, dev cache) relabelled as a holdout packet and
    re-sealed is refused: wrong commit, trades outside the holdout, cache digests not the holdout manifest's."""
    pre, _ = BP.load_prereg(TP.ROOT)
    dev_man = manifest({'start': '2024-10-14T00:00:00Z', 'end': '2026-09-28T00:00:00Z'})
    dev_man['files'] = {s: {'sha256': f'{i + 7_000:064x}'} for i, s in enumerate(dev_man['files'])}
    packets = all_packets(pre, Y, manifest())
    i, h = 5, pre['hypotheses'][5]
    p = synth_packet(h, pre, i, DEV_RUNNER_COMMIT, dev_man,
                     days=[f'2025-0{1 + d // 28}-{1 + d % 28:02d}' for d in range(40)])
    p['window'] = {'start': BP.HOLDOUT['start'], 'end': BP.HOLDOUT['end'], 'tf': '4h'}     # the relabel
    if attacker_sets_commit_and_cache:
        p['commit'] = Y
        p['cache']['files'] = copy.deepcopy(packets[h['id']]['cache']['files'])
    p['packet_sha256'] = E.packet_sha256(p)
    packets[h['id']] = p
    with pytest.raises(E.EvalError, match='outside the holdout') as ei:
        E.evaluate_packets(packets, pre, SYMS, y=Y, manifest=manifest(), n_flips=10)
    if not attacker_sets_commit_and_cache:
        assert 'registration commit Y' in str(ei.value) and 'cache file digests' in str(ei.value)
    with pytest.raises(E.EvalError, match='manifest window is not the holdout'):          # dev manifest refused
        E.evaluate_packets(packets, pre, SYMS, y=Y, manifest=dev_man, n_flips=10)


def test_data_completeness_rows():
    pre, _ = BP.load_prereg(TP.ROOT)
    h = pre['hypotheses'][0]
    p = synth_packet(h, pre, 0, Y, manifest())
    assert E.data_complete(p, SYMS)
    p['cache']['completeness'][p['universe']['run'][3]]['complete'] = False
    assert not E.data_complete(p, SYMS)
    p = synth_packet(h, pre, 0, Y, manifest())
    p['universe']['incomplete'] = p['universe']['incomplete'][:1]                     # BRUSDT unaccounted
    assert not E.data_complete(p, SYMS)

