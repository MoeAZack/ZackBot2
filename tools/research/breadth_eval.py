"""Holdout evaluator of `breadth_static40.v1` (zb-breadth-prereg/1). It lives in the accepted tree X on purpose: the
evaluation guard only lets it run with HEAD == the registered accepted commit / tree, so the evaluator is frozen with
everything else (Cowork 6201545438, finding 1).

  packets -> after-cost daily P&L -> one-sided day-level sign-flip (frozen seed / flip count) -> Holm across the 12 ->
  the frozen gate rows -> PASS / FAIL per hypothesis.

Every entry point (collect_holdout, run_holdout_packets, evaluate) first calls `preflight`, which refuses, in order and
before any data path is touched:
  1. CLOCK() < the holdout end 2027-10-18T00:00:00Z (the window has not closed: no interim look);
  2. no registration in the family ledger at `ledger_ref`;
  3. a dirty worktree, or HEAD commit / tree != the registered accepted commit / tree (breadth_prereg.evaluation_guard);
  4. a holdout_reveal already in the family ledger, or an evaluation output already written (one evaluation).
`evaluate_packets` is the pure computation (no clock, no git, no file access); tests drive it on synthetic packets.

The holdout collection and packet runs reuse the frozen collector / runner unchanged; `holdout_window()` only swaps
their development-window guard for an exact holdout-window guard for the duration of the call (restored afterwards).

Usage (only after 2027-10-18T00:00:00Z, at the accepted commit):
  python tools/research/breadth_eval.py collect  --ledger-ref origin/master --cache DIR --manifest FILE
  python tools/research/breadth_eval.py packets  --ledger-ref origin/master --cache DIR --out DIR
  python tools/research/breadth_eval.py evaluate --ledger-ref origin/master --packets DIR --out DIR
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import breadth_prereg as BP                                                                 # noqa: E402

FORMAT = 'zb-breadth-eval/1'
TF_MS = 4 * 3_600_000
HOLDOUT_SPEC = f"{BP.HOLDOUT['start']}/{BP.HOLDOUT['end']}"
HOLDOUT_START_MS, HOLDOUT_END_MS = BP._ms(BP.HOLDOUT['start']), BP._ms(BP.HOLDOUT['end'])
OUT_NAME = 'evaluation.json'
CLOCK = BP._wall_clock_ms        # production time source; tests replace this module attribute


class EvalError(ValueError):
    pass


def preflight(repo: str, ledger_ref: str, out_dir: str | None = None) -> dict:
    now = int(CLOCK())
    if now < HOLDOUT_END_MS:
        raise EvalError(f'evaluation refused: the holdout window is open until {BP.HOLDOUT["end"]} (now {BP.ts(now)}); '
                        'no interim look')
    try:
        binding = BP.registered_binding(repo, ledger_ref)
        BP.evaluation_guard(repo, binding)
    except BP.BreadthPreregError as e:
        raise EvalError(str(e)) from None
    raw = BP._blob(repo, ledger_ref, BP.LEDGER) or b''
    if b'"kind":"holdout_reveal"' in raw:
        raise EvalError('evaluation refused: the family already holds a holdout_reveal (one evaluation)')
    if out_dir and os.path.exists(os.path.join(out_dir, OUT_NAME)):
        raise EvalError('evaluation refused: an evaluation output already exists (one evaluation per hypothesis)')
    return binding


def holdout_check_window(spec, plan=None):
    """Exact holdout-window stand-in for the frozen development guard: (lo, hi, warm_lo) for the holdout only."""
    if spec != HOLDOUT_SPEC:
        raise EvalError(f'holdout mode accepts exactly the window {HOLDOUT_SPEC}')
    return HOLDOUT_START_MS, HOLDOUT_END_MS, HOLDOUT_START_MS - 220 * TF_MS


@contextlib.contextmanager
def holdout_window():
    import run_vwaprev_candidate as VC
    old = (VC.check_window, VC.SEALED_CUTOFF)
    VC.check_window, VC.SEALED_CUTOFF = holdout_check_window, BP.HOLDOUT['end']
    try:
        yield
    finally:
        VC.check_window, VC.SEALED_CUTOFF = old


def collect_holdout(repo: str, *, ledger_ref: str, cache: str, manifest: str) -> dict:
    """Wired, unreachable before the window closes: the frozen bounded collector over warm-up + holdout."""
    preflight(repo, ledger_ref)
    import collect_klines_research as K
    import run_vwaprev_candidate as VC
    u = VC.frozen_universe()
    syms = sorted({x['symbol'] for x in u['symbols']} | {VC.BTC})
    with holdout_window():
        return K.collect(symbols=syms, interval='4h', window=HOLDOUT_SPEC, out=cache, manifest=manifest,
                         warmup_bars=220)


def run_holdout_packets(repo: str, *, ledger_ref: str, cache: str, out_dir: str) -> list[str]:
    """Wired, unreachable before the window closes: one frozen-runner packet per hypothesis (defaults, Static-40)."""
    preflight(repo, ledger_ref, out_dir)
    import run_breadth_candidate as B
    import run_vwaprev_candidate as VC
    syms = [x['symbol'] for x in VC.frozen_universe()['symbols']]
    os.makedirs(out_dir, exist_ok=True)
    done = []
    with holdout_window():
        for k, s in BP.HYPOTHESES:
            out = os.path.join(out_dir, f'{k}_{s}_defaults.json')
            if os.path.exists(out):
                raise EvalError(f'{out} exists: one run per hypothesis')
            B.run(cache=cache, window=HOLDOUT_SPEC, out=out, key=k, side=s, mgmt_mode='defaults', symbols=syms,
                  repo=repo)
            done.append(out)
    return done


# ------------------------------------------------------------------ the pure computation
def packet_failures(p: dict, h: dict, pre: dict) -> list[str]:
    bad = []
    if p.get('format') != pre['config']['runner_format']:
        bad.append('packet format')
    st = p.get('strategy', {})
    if (st.get('key'), p.get('side'), st.get('mgmt_mode'), st.get('mgmt_override')) != (h['key'], h['side'],
                                                                                         'defaults', {}):
        bad.append('packet key / side / mgmt differ from the hypothesis')
    if st.get('params') != h['params'] or st.get('mgmt') != h['mgmt']:
        bad.append('packet params / mgmt differ from the frozen definition')
    w = p.get('window', {})
    if (w.get('start'), w.get('end'), w.get('tf')) != (BP.HOLDOUT['start'], BP.HOLDOUT['end'], '4h'):
        bad.append('packet window is not the holdout')
    if (p.get('runner_sha256') or {}).get('run_breadth_candidate.py') != \
            pre['code']['sha256']['tools/research/run_breadth_candidate.py']:
        bad.append('packet runner sha256 differs from the frozen runner')
    c, f = p.get('costs', {}), pre['config']['costs']
    if (c.get('fee_taker'), c.get('slip')) != (f['fee_taker'], f['slip']) or \
            abs(float(c.get('fund_per_bar', -1)) - f['fund_per_bar_4h']) > 1e-12:
        bad.append('packet costs differ from the frozen config')
    if (p.get('universe') or {}).get('digest') != pre['universe']['digest']:
        bad.append('packet universe digest differs')
    return bad


def data_complete(p: dict, pre_universe_symbols: list[str]) -> bool:
    u, comp = p.get('universe', {}), (p.get('cache') or {}).get('completeness', {})
    inc = {x['symbol'] for x in u.get('incomplete', [])}
    run = list(u.get('run', []))
    return (list(u.get('requested', [])) == pre_universe_symbols and set(run) | inc == set(pre_universe_symbols)
            and not set(run) & inc and all(comp.get(s, {}).get('complete') is True for s in run))


def evaluate_packets(packets: dict, pre: dict, universe_symbols: list[str], n_flips: int | None = None,
                     btc_context=None) -> dict:
    """{hypothesis id: packet} -> the evaluation document. Pure; n_flips defaults to the frozen count."""
    ids = [h['id'] for h in pre['hypotheses']]
    if sorted(packets) != sorted(ids):
        raise EvalError(f'need exactly one packet per hypothesis ({len(ids)})')
    n = pre['statistics']['n_flips'] if n_flips is None else n_flips
    rows, pv = {}, {}
    for h in pre['hypotheses']:
        p = packets[h['id']]
        bad = packet_failures(p, h, pre)
        if bad:
            raise EvalError(f"{h['id']}: {'; '.join(bad)}")
        a, b = p['stats']['aggregate'], p.get('bust') or {}
        daily = BP.daily_pnl_micro(p['trades'])
        pv[h['id']] = BP.sign_flip_p(daily, h['id'], n)
        rows[h['id']] = {'trades': a['trades'], 'pf': a['pf'], 'r_sum': a['r_sum'], 'pnl': a['pnl'],
                         'max_dd_r': a['max_dd_r'], 'max_dd_equity': a['max_dd_equity'], 'win_rate': a.get('win_rate'),
                         'busted': bool(b.get('busted')), 'status': p.get('status'),
                         'data_complete': data_complete(p, universe_symbols),
                         'incomplete': [x['symbol'] for x in p['universe'].get('incomplete', [])],
                         'n_days': len(daily), 'sign_flip_p': pv[h['id']], 'packet_sha256': p.get('packet_sha256')}
    adj = BP.holm(pv, ids, pre['statistics']['multiplicity']['alpha'])
    for i in ids:
        rows[i]['holm_adjusted_p'], rows[i]['holm_reject'] = adj[i]
        g = BP.gate_rows(rows[i])
        rows[i]['gate_rows'] = g
        rows[i]['verdict'] = 'PASS' if all(g.values()) else 'FAIL: ' + ', '.join(k for k, v in g.items() if not v)
    return {'format': FORMAT, 'candidate_id': BP.CANDIDATE, 'holdout': dict(BP.HOLDOUT), 'n_flips': n,
            'hypotheses': rows, 'context': {'btc_side_matched_passive': btc_context, 'gate': False},
            'claim': 'gate result only'}


def evaluate(repo: str, *, ledger_ref: str, packets_dir: str, out_dir: str, btc_context=None) -> dict:
    binding = preflight(repo, ledger_ref, out_dir)
    pre, sha = BP.load_prereg(repo)
    if sha != binding['prereg_sha256']:
        raise EvalError('prereg differs from the registration')
    import run_vwaprev_candidate as VC
    syms = [x['symbol'] for x in VC.frozen_universe()['symbols']]
    packets = {}
    for k, s in BP.HYPOTHESES:
        with open(os.path.join(packets_dir, f'{k}_{s}_defaults.json'), encoding='utf-8') as f:
            packets[BP.hypothesis_id(k, s)] = json.load(f)
    doc = evaluate_packets(packets, pre, syms, btc_context=btc_context)
    doc['binding'] = binding
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, OUT_NAME), 'x', encoding='utf-8', newline='\n') as f:     # 'x': never overwrite
        f.write(json.dumps(doc, sort_keys=True, indent=1) + '\n')
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('cmd', choices=['collect', 'packets', 'evaluate'])
    ap.add_argument('--ledger-ref', default='origin/master')
    ap.add_argument('--cache')
    ap.add_argument('--manifest')
    ap.add_argument('--packets')
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'collect':
            collect_holdout(ROOT, ledger_ref=a.ledger_ref, cache=a.cache, manifest=a.manifest)
        elif a.cmd == 'packets':
            run_holdout_packets(ROOT, ledger_ref=a.ledger_ref, cache=a.cache, out_dir=a.out)
        else:
            evaluate(ROOT, ledger_ref=a.ledger_ref, packets_dir=a.packets, out_dir=a.out)
    except (EvalError, BP.BreadthPreregError) as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
