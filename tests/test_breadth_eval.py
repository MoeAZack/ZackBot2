"""breadth_eval (zb-breadth-eval/1): refusals before the window closes / on a dirty or wrong HEAD / on a second run,
and the end-to-end computation on SYNTHETIC packets with an injected clock. No market data is read or fetched: the
wired collector and runner calls are replaced by tripwires that fail the test if ever reached."""
import json
import os

import pytest

import test_breadth_prereg as TP
from test_breadth_prereg import BP, _git, _head, _commit_ledger, repo  # noqa: F401  (fixture)

import breadth_eval as E                                                                    # noqa: E402
import run_vwaprev_candidate as VC                                                          # noqa: E402

AFTER = E.HOLDOUT_END_MS + 3_600_000
SYMS = [x['symbol'] for x in VC.frozen_universe()['symbols']]
INC = ['AKEUSDT', 'BRUSDT']


@pytest.fixture(autouse=True)
def tripwires(monkeypatch):
    import collect_klines_research as K
    import run_breadth_candidate as B

    def boom(*a, **k):
        raise AssertionError('a data path was reached')
    monkeypatch.setattr(K, 'collect', boom)
    monkeypatch.setattr(B, 'run', boom)
    monkeypatch.setattr(K, 'fetch', boom)


def synth_packet(h, pre, i):
    days = [f'2027-0{1 + d // 28}-{1 + d % 28:02d}T08:00:00Z' for d in range(40)]
    edge = 3.0 if i % 3 == 0 else 0.0                     # every third hypothesis has a strong positive edge
    trades = [{'t_out': t, 'pnl': edge + (1.0 if (d * 7 + i) % 2 else -1.0)} for d, t in enumerate(days)]
    pnl = sum(t['pnl'] for t in trades)
    run = [s for s in SYMS if s not in INC]
    return {'format': pre['config']['runner_format'], 'status': 'ok', 'packet_sha256': f'{i:064x}',
            'strategy': {'key': h['key'], 'mgmt_mode': 'defaults', 'mgmt_override': {}, 'params': h['params'],
                         'mgmt': h['mgmt']},
            'side': h['side'], 'window': {'start': BP.HOLDOUT['start'], 'end': BP.HOLDOUT['end'], 'tf': '4h'},
            'runner_sha256': {'run_breadth_candidate.py': pre['code']['sha256']['tools/research/run_breadth_candidate.py']},
            'costs': {'fee_taker': 0.0005, 'slip': 0.0002, 'fund_per_bar': 5.000000000000001e-05},
            'universe': {'digest': pre['universe']['digest'], 'requested': SYMS, 'run': run,
                         'incomplete': [{'symbol': s} for s in INC]},
            'cache': {'completeness': {s: {'complete': True} for s in run}},
            'stats': {'aggregate': {'trades': 40, 'pf': 2.0 if edge else 1.0, 'r_sum': pnl, 'pnl': pnl,
                                    'max_dd_r': 10.0, 'max_dd_equity': -0.1, 'win_rate': 0.5}},
            'bust': {'busted': False}, 'trades': trades}


def write_packets(d, pre):
    os.makedirs(d, exist_ok=True)
    for i, h in enumerate(pre['hypotheses']):
        with open(os.path.join(d, f"{h['key']}_{h['side']}_defaults.json"), 'w', encoding='utf-8') as f:
            json.dump(synth_packet(h, pre, i), f)


@pytest.fixture
def registered(repo, monkeypatch):
    acc = _head(repo)
    BP.register(repo, accepted_commit=acc)
    _commit_ledger(repo)
    reg = _head(repo)
    _git(repo, 'checkout', '-q', acc)
    return repo, acc, reg


# ------------------------------------------------------------------ refusals
@pytest.mark.parametrize('now', [E.HOLDOUT_START_MS, E.HOLDOUT_END_MS - 1, BP.HOLDOUT_START_MS - 1])
def test_refuses_while_the_window_is_open(registered, monkeypatch, tmp_path, now):
    r, acc, reg = registered
    monkeypatch.setattr(E, 'CLOCK', lambda: now)
    for call in (lambda: E.collect_holdout(r, ledger_ref=reg, cache=str(tmp_path / 'c'), manifest=str(tmp_path / 'm')),
                 lambda: E.run_holdout_packets(r, ledger_ref=reg, cache=str(tmp_path / 'c'), out_dir=str(tmp_path / 'o')),
                 lambda: E.evaluate(r, ledger_ref=reg, packets_dir=str(tmp_path / 'p'), out_dir=str(tmp_path / 'o'))):
        with pytest.raises(E.EvalError, match='holdout window is open'):
            call()
    assert not os.path.exists(tmp_path / 'o')
    assert E.main(['collect', '--ledger-ref', reg, '--cache', str(tmp_path / 'c'), '--manifest', 'x']) == 2


def test_refuses_dirty_or_wrong_head(registered, monkeypatch, tmp_path):
    r, acc, reg = registered
    monkeypatch.setattr(E, 'CLOCK', lambda: AFTER)
    _git(r, 'checkout', '-q', reg)                                  # HEAD = registration commit, not X
    with pytest.raises(E.EvalError, match='not the registered'):
        E.collect_holdout(r, ledger_ref=reg, cache=str(tmp_path / 'c'), manifest=str(tmp_path / 'm'))
    _git(r, 'checkout', '-q', acc)
    with open(os.path.join(r, 'strategies.py'), 'ab') as f:
        f.write(b'# dirty\n')
    with pytest.raises(E.EvalError, match='dirty worktree'):
        E.run_holdout_packets(r, ledger_ref=reg, cache=str(tmp_path / 'c'), out_dir=str(tmp_path / 'o'))


def test_refuses_without_registration(repo, monkeypatch, tmp_path):
    monkeypatch.setattr(E, 'CLOCK', lambda: AFTER)
    with pytest.raises(E.EvalError, match='no registration'):
        E.evaluate(repo, ledger_ref='HEAD', packets_dir=str(tmp_path), out_dir=str(tmp_path / 'o'))


def test_refuses_after_a_reveal(registered, monkeypatch):
    r, acc, reg = registered
    monkeypatch.setattr(E, 'CLOCK', lambda: AFTER)
    binding = BP.registered_binding(r, reg)
    real = BP._blob
    monkeypatch.setattr(BP, 'registered_binding', lambda repo, ref: binding)
    monkeypatch.setattr(BP, '_blob', lambda repo, rev, rel: (real(repo, reg, rel) or b'') + b'{"kind":"holdout_reveal"}\n'
                        if (rev, rel) == ('REVEALED', BP.LEDGER) else real(repo, rev, rel))
    E.preflight(r, reg)                                                     # no reveal: passes
    with pytest.raises(E.EvalError, match='holdout_reveal'):
        E.preflight(r, 'REVEALED')


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


# ------------------------------------------------------------------ end to end on synthetic packets
def test_end_to_end_on_synthetic_packets(registered, monkeypatch, tmp_path):
    r, acc, reg = registered
    monkeypatch.setattr(E, 'CLOCK', lambda: AFTER)
    pre, _ = BP.load_prereg(r)
    pk, out = str(tmp_path / 'packets'), str(tmp_path / 'eval')
    write_packets(pk, pre)
    doc = E.evaluate(r, ledger_ref=reg, packets_dir=pk, out_dir=out)
    rows = doc['hypotheses']
    assert len(rows) == 12 and doc['n_flips'] == 100_000 and doc['binding']['accepted_commit'] == acc
    strong = [h['id'] for i, h in enumerate(pre['hypotheses']) if i % 3 == 0]
    for i in strong:
        assert rows[i]['holm_reject'] and rows[i]['verdict'] == 'PASS', rows[i]
    for i in set(rows) - set(strong):
        assert rows[i]['verdict'].startswith('FAIL') and 'holm' in rows[i]['verdict']
    assert all(r_['data_complete'] and r_['incomplete'] == INC for r_ in rows.values())
    with open(os.path.join(out, E.OUT_NAME), encoding='utf-8') as f:
        assert json.load(f)['hypotheses'] == json.loads(json.dumps(rows))
    with pytest.raises(E.EvalError, match='already exists'):              # one evaluation
        E.evaluate(r, ledger_ref=reg, packets_dir=pk, out_dir=out)
    again = E.evaluate_packets({h['id']: synth_packet(h, pre, i) for i, h in enumerate(pre['hypotheses'])}, pre, SYMS)
    assert again['hypotheses'] == json.loads(json.dumps(rows))              # deterministic


@pytest.mark.parametrize('mut, msg', [
    (lambda p: p['window'].update(start='2026-01-05T00:00:00Z'), 'not the holdout'),
    (lambda p: p['strategy'].update(mgmt_override={'stop_atr': 3.0}), 'mgmt'),
    (lambda p: p['strategy'].update(params={'k': 6}), 'params'),
    (lambda p: p['costs'].update(fee_taker=0.0004), 'costs'),
    (lambda p: p['runner_sha256'].update({'run_breadth_candidate.py': '0' * 64}), 'runner sha256'),
    (lambda p: p['universe'].update(digest='0' * 64), 'universe digest'),
])
def test_packet_identity_refused(mut, msg):
    pre, _ = BP.load_prereg(TP.ROOT)
    packets = {h['id']: synth_packet(h, pre, i) for i, h in enumerate(pre['hypotheses'])}
    mut(packets[pre['hypotheses'][8]['id']])
    with pytest.raises(E.EvalError, match=msg):
        E.evaluate_packets(packets, pre, SYMS, n_flips=10)


def test_data_completeness_rows():
    pre, _ = BP.load_prereg(TP.ROOT)
    h = pre['hypotheses'][0]
    p = synth_packet(h, pre, 0)
    assert E.data_complete(p, SYMS)
    p['cache']['completeness'][p['universe']['run'][3]]['complete'] = False
    assert not E.data_complete(p, SYMS)
    p = synth_packet(h, pre, 0)
    p['universe']['incomplete'] = p['universe']['incomplete'][:1]                     # BRUSDT unaccounted
    assert not E.data_complete(p, SYMS)
