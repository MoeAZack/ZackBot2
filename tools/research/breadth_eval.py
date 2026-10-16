"""Holdout evaluator of `breadth_static40.v1` (zb-breadth-prereg/1). It lives in the accepted code commit X, so it is
frozen with everything else (Cowork 6201545438 finding 1); it runs from the registration commit Y, whose tree minus
the two ledger files equals X's (Codex 6201777892).

  packets (frozen runner, holdout window) + the holdout manifest
    -> spend: one family `window_spent` record binding the exact packets + manifest (eval_digest), committed as R
    -> evaluate at R: after-cost daily P&L -> one-sided day-level sign-flip (frozen seed / flips) -> Holm across the
       12 -> the frozen gate rows (practical gates AND Holm) -> PASS / FAIL per hypothesis.

Every entry point first calls `preflight`, which refuses, before any data path is touched, when:
  1. CLOCK() < the holdout end 2027-10-18T00:00:00Z (the window is open: no interim look);
  2. the pinned registration commit Y (`--registration`, the immutable tag / SHA) does not verify:
     breadth_prereg.evaluation_guard (clean tree; Y introduced the 12 records; Y's only parent is X; X..Y ledger-only;
     HEAD == Y, or for `evaluate` HEAD == R = Y's single ledger-only child holding exactly one evaluation spend);
  3. collect / packets / spend: an evaluation spend already exists (one evaluation);
     evaluate: the packets + manifest are not exactly those the spend recorded, the output location is not the one
     it recorded, or the output already exists.
One evaluation: the spend is recorded (and must be committed as R) BEFORE any verdict is computed, a second spend is
refused, and `evaluate` only accepts the inputs the spend bound; re-running it on those inputs reproduces the same
deterministic document.

`evaluate_packets` is the pure computation (no clock, git or file access). Packets are bound to the run: commit == Y,
clean tree, packet_sha256 recomputed, every trade inside [holdout start, end), the aggregate recomputed from the
trades, cache file digests equal to the holdout manifest's, and the frozen identity (key / side / mgmt / params /
costs / runner sha / universe).

The holdout collection and packet runs reuse the frozen collector / runner unchanged; `holdout_window()` only swaps
their development-window guard for an exact holdout-window guard for the duration of the call (restored afterwards).

Usage (only after 2027-10-18T00:00:00Z, from a clean checkout of Y, then of R):
  python tools/research/breadth_eval.py collect  --registration TAG --cache DIR --manifest FILE
  python tools/research/breadth_eval.py packets  --registration TAG --cache DIR --out DIR
  python tools/research/breadth_eval.py spend    --registration TAG --packets DIR --manifest FILE --out DIR  (commit R)
  python tools/research/breadth_eval.py evaluate --registration TAG --packets DIR --manifest FILE --out DIR
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import breadth_prereg as BP                                                                 # noqa: E402
import ledger as L                                                                          # noqa: E402

FORMAT = 'zb-breadth-eval/1'
TF_MS = 4 * 3_600_000
HOLDOUT_SPEC = f"{BP.HOLDOUT['start']}/{BP.HOLDOUT['end']}"
HOLDOUT_START_MS, HOLDOUT_END_MS = BP._ms(BP.HOLDOUT['start']), BP._ms(BP.HOLDOUT['end'])
OUT_NAME = 'evaluation.json'
MANIFEST_FORMAT = 'zb-klines-research/1'
EXACT = ('trades', 'longs', 'shorts', 'wins', 'exits')
CLOSE = ('pnl', 'gross_win', 'gross_loss', 'r_sum', 'gross', 'fees', 'funding', 'max_dd_r')
CLOCK = BP._wall_clock_ms        # production time source; tests replace this module attribute


class EvalError(ValueError):
    pass


def _spent(repo: str, y: str) -> bool:
    """A spend at HEAD, or a child of Y anywhere in this clone whose ledger already holds a spend (an R)."""
    def has(rev):
        raw = BP._blob(repo, rev, BP.LEDGER) or b''
        return bool(raw) and any(r['kind'] in ('window_spent', 'holdout_reveal') for r in BP._family_lines(raw))
    kids = [ln.split()[0] for ln in BP._git(repo, 'rev-list', '--all', '--parents').splitlines() if y in ln.split()[1:]]
    return has('HEAD') or any(has(k) for k in kids)


def preflight(repo: str, registration: str, *, stage: str, out_dir: str | None = None) -> dict:
    now = int(CLOCK())
    if now < HOLDOUT_END_MS:
        raise EvalError(f'evaluation refused: the holdout window is open until {BP.HOLDOUT["end"]} (now {BP.ts(now)}); '
                        'no interim look')
    try:
        binding = BP.evaluation_guard(repo, registration, allow_spend_child=stage == 'evaluate')
    except (BP.BreadthPreregError, L.LedgerError) as e:
        raise EvalError(str(e)) from None
    if stage == 'evaluate':
        if binding['spend'] is None:
            raise EvalError('evaluation refused: record the evaluation spend first (spend, commit R), then evaluate at R')
        if not out_dir or os.path.abspath(out_dir) != binding['spend']['detail'].get('out_dir'):
            raise EvalError('evaluation refused: the output location is not the one the evaluation spend recorded '
                            '(one evaluation)')
        if os.path.exists(os.path.join(out_dir, OUT_NAME)):
            raise EvalError('evaluation refused: an evaluation output already exists')
    elif _spent(repo, binding['y']):
        raise EvalError('evaluation refused: the family already holds its evaluation spend (one evaluation)')
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


def collect_holdout(repo: str, *, registration: str, cache: str, manifest: str) -> dict:
    """Wired, unreachable before the window closes: the frozen bounded collector over warm-up + holdout."""
    preflight(repo, registration, stage='collect')
    import collect_klines_research as K
    import run_vwaprev_candidate as VC
    syms = sorted({x['symbol'] for x in VC.frozen_universe()['symbols']} | {VC.BTC})
    with holdout_window():
        return K.collect(symbols=syms, interval='4h', window=HOLDOUT_SPEC, out=cache, manifest=manifest,
                         warmup_bars=220)


def run_holdout_packets(repo: str, *, registration: str, cache: str, out_dir: str) -> list[str]:
    """Wired, unreachable before the window closes: one frozen-runner packet per hypothesis (defaults, Static-40)."""
    preflight(repo, registration, stage='packets')
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
def packet_sha256(p: dict) -> str:
    d = {k: v for k, v in p.items() if k != 'packet_sha256'}
    return hashlib.sha256(json.dumps(d, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def aggregate_from_trades(trades: list) -> dict:
    """The packet aggregate fields recomputable from the stored trades (runner stats / VC.trade_stats semantics)."""
    n = len(trades)
    pnl = [float(t['pnl']) for t in trades]
    gw, gl = sum(x for x in pnl if x > 0), -sum(x for x in pnl if x < 0)
    exits = {}
    for t in trades:
        exits[str(t['why'])] = exits.get(str(t['why']), 0) + 1
    cum, peak, dd = 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda t: (t['t_out'], t['t_in'])):
        cum += float(t['R'])
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return {'trades': n, 'longs': sum(t['side'] == 1 for t in trades), 'shorts': sum(t['side'] == -1 for t in trades),
            'wins': sum(x > 0 for x in pnl), 'exits': dict(sorted(exits.items())), 'pnl': sum(pnl),
            'gross_win': gw, 'gross_loss': gl, 'pf': gw / gl if gl > 0 else None,
            'r_sum': sum(float(t['R']) for t in trades), 'gross': sum(float(t['gross']) for t in trades),
            'fees': sum(float(t['fees']) for t in trades), 'funding': sum(float(t['funding']) for t in trades),
            'max_dd_r': dd if n else None}


def manifest_failures(man: dict) -> list[str]:
    bad = []
    w = man.get('window') or {}
    if man.get('format') != MANIFEST_FORMAT or man.get('interval') != '4h':
        bad.append(f'manifest must be a 4h {MANIFEST_FORMAT} manifest')
    if (w.get('start'), w.get('end')) != (BP.HOLDOUT['start'], BP.HOLDOUT['end']):
        bad.append('manifest window is not the holdout')
    if not isinstance(man.get('files'), dict) or not all(
            isinstance(f, dict) and BP.HEX64.match(str(f.get('sha256', ''))) for f in man['files'].values()):
        bad.append('manifest files must carry sha256 digests')
    return bad


def packet_failures(p: dict, h: dict, pre: dict, y: str, man: dict) -> list[str]:
    bad = []
    if p.get('format') != pre['config']['runner_format']:
        bad.append('packet format')
    if p.get('packet_sha256') != packet_sha256(p):
        bad.append('packet_sha256 does not recompute (edited packet)')
    if (p.get('commit'), p.get('tree')) != (y, 'clean'):
        bad.append('packet was not produced at the registration commit Y on a clean tree')
    st = p.get('strategy', {})
    if (st.get('key'), p.get('side'), st.get('mgmt_mode'), st.get('mgmt_override')) != (h['key'], h['side'],
                                                                                         'defaults', {}):
        bad.append('packet key / side / mgmt differ from the hypothesis')
    if st.get('params') != h['params'] or st.get('mgmt') != h['mgmt']:
        bad.append('packet params / mgmt differ from the frozen definition')
    w = p.get('window', {})
    if (w.get('start'), w.get('end'), w.get('tf')) != (BP.HOLDOUT['start'], BP.HOLDOUT['end'], '4h'):
        bad.append('packet window is not the holdout')
    lo, hi = BP.HOLDOUT['start'], BP.HOLDOUT['end']
    if not all(lo <= t['t_in'] < hi and lo <= t['t_out'] < hi for t in p.get('trades', [])):
        bad.append('a trade lies outside the holdout window')
    if (p.get('runner_sha256') or {}).get('run_breadth_candidate.py') != \
            pre['code']['sha256']['tools/research/run_breadth_candidate.py']:
        bad.append('packet runner sha256 differs from the frozen runner')
    c, f = p.get('costs', {}), pre['config']['costs']
    if (c.get('fee_taker'), c.get('slip')) != (f['fee_taker'], f['slip']) or \
            abs(float(c.get('fund_per_bar', -1)) - f['fund_per_bar_4h']) > 1e-12:
        bad.append('packet costs differ from the frozen config')
    if (p.get('universe') or {}).get('digest') != pre['universe']['digest']:
        bad.append('packet universe digest differs')
    try:
        a, re_ = p['stats']['aggregate'], aggregate_from_trades(p['trades'])
        tol = 1e-6 * (len(p['trades']) + 1)
        if any(a.get(k) != re_[k] for k in EXACT) or any(
                (a.get(k) is None) != (re_[k] is None) or (re_[k] is not None and abs(float(a[k]) - re_[k]) > tol)
                for k in CLOSE) or ((a.get('pf') is None) != (re_['pf'] is None)) or (
                re_['pf'] is not None and abs(float(a['pf']) - re_['pf']) > 1e-6 * max(1.0, re_['pf'])):
            bad.append('stats.aggregate does not recompute from the trades')
    except (KeyError, TypeError, ValueError):
        bad.append('stats.aggregate / trades malformed')
    files = (p.get('cache') or {}).get('files') or {}
    mf = man.get('files') or {}
    if not files or any(mf.get(s, {}).get('sha256') != v.get('sha256') for s, v in files.items()):
        bad.append('cache file digests differ from the holdout manifest')
    return bad


def data_complete(p: dict, pre_universe_symbols: list[str]) -> bool:
    u, comp = p.get('universe', {}), (p.get('cache') or {}).get('completeness', {})
    inc = {x['symbol'] for x in u.get('incomplete', [])}
    run = list(u.get('run', []))
    return (list(u.get('requested', [])) == pre_universe_symbols and set(run) | inc == set(pre_universe_symbols)
            and not set(run) & inc and all(comp.get(s, {}).get('complete') is True for s in run))


def inputs_digest(packets: dict, man: dict, pre: dict) -> str:
    """eval_digest: the exact evaluation inputs (packet hashes, manifest, evaluator bytes identity via the prereg)."""
    return hashlib.sha256(BP.canon({'packets': {i: packets[i].get('packet_sha256') for i in sorted(packets)},
                                    'manifest_sha256': hashlib.sha256(BP.canon(man)).hexdigest(),
                                    'code_digest': pre['code']['digest'], 'n_flips': pre['statistics']['n_flips'],
                                    'format': FORMAT})).hexdigest()


def check_inputs(packets: dict, pre: dict, y: str, man: dict) -> None:
    ids = [h['id'] for h in pre['hypotheses']]
    if sorted(packets) != sorted(ids):
        raise EvalError(f'need exactly one packet per hypothesis ({len(ids)})')
    bad = manifest_failures(man)
    if bad:
        raise EvalError('; '.join(bad))
    for h in pre['hypotheses']:
        bad = packet_failures(packets[h['id']], h, pre, y, man)
        if bad:
            raise EvalError(f"{h['id']}: {'; '.join(bad)}")


def evaluate_packets(packets: dict, pre: dict, universe_symbols: list[str], *, y: str, manifest: dict,
                     n_flips: int | None = None, btc_context=None) -> dict:
    """{hypothesis id: packet} -> the evaluation document. Pure; n_flips defaults to the frozen count."""
    check_inputs(packets, pre, y, manifest)
    ids = [h['id'] for h in pre['hypotheses']]
    n = pre['statistics']['n_flips'] if n_flips is None else n_flips
    rows, pv = {}, {}
    for h in pre['hypotheses']:
        p = packets[h['id']]
        a, b = p['stats']['aggregate'], p.get('bust') or {}
        daily = BP.daily_pnl_micro(p['trades'])
        pv[h['id']] = BP.sign_flip_p(daily, h['id'], n)
        rows[h['id']] = {'trades': a['trades'], 'pf': a['pf'], 'r_sum': a['r_sum'], 'pnl': a['pnl'],
                         'max_dd_r': a['max_dd_r'], 'max_dd_equity': a.get('max_dd_equity'),
                         'win_rate': a.get('win_rate'), 'busted': bool(b.get('busted')), 'status': p.get('status'),
                         'data_complete': data_complete(p, universe_symbols),
                         'incomplete': [x['symbol'] for x in p['universe'].get('incomplete', [])],
                         'n_days': len(daily), 'sign_flip_p': pv[h['id']], 'packet_sha256': p.get('packet_sha256')}
    adj = BP.holm(pv, ids, pre['statistics']['multiplicity']['alpha'])
    for i in ids:
        rows[i]['holm_adjusted_p'], rows[i]['holm_reject'] = adj[i]
        g = BP.gate_rows(rows[i])                            # practical gates AND Holm (Codex 6201777892)
        rows[i]['gate_rows'] = g
        rows[i]['verdict'] = 'PASS' if all(g.values()) else 'FAIL: ' + ', '.join(k for k, v in g.items() if not v)
    return {'format': FORMAT, 'candidate_id': BP.CANDIDATE, 'holdout': dict(BP.HOLDOUT), 'n_flips': n,
            'eval_digest': inputs_digest(packets, manifest, pre), 'hypotheses': rows,
            'context': {'btc_side_matched_passive': btc_context, 'gate': False}, 'claim': 'gate result only'}


def _load_inputs(packets_dir: str, manifest_path: str) -> tuple[dict, dict]:
    packets = {}
    for k, s in BP.HYPOTHESES:
        with open(os.path.join(packets_dir, f'{k}_{s}_defaults.json'), encoding='utf-8') as f:
            packets[BP.hypothesis_id(k, s)] = json.load(f)
    with open(manifest_path, encoding='utf-8') as f:
        return packets, json.load(f)


def spend(repo: str, *, registration: str, packets_dir: str, manifest: str, out_dir: str,
          author: str = BP.AUTHOR) -> dict:
    """Record the one evaluation spend (family `window_spent`, split holdout, the holdout window) binding the exact
    validated inputs, before any verdict is computed. Commit the two ledger files as R (Y's only child)."""
    binding = preflight(repo, registration, stage='spend')
    pre, _ = BP.load_prereg(repo)
    packets, man = _load_inputs(packets_dir, manifest)
    check_inputs(packets, pre, binding['y'], man)
    now = int(CLOCK())
    cairo = datetime.fromtimestamp(now / 1000, timezone.utc).astimezone(BP._cairo()).strftime('%Y-%m-%d')
    return L.append(os.path.join(repo, BP.LEDGER), kind='window_spent', candidate_id=BP.CANDIDATE, split='holdout',
                    window={'start': BP.HOLDOUT['start'], 'end': BP.HOLDOUT['end']},
                    manifest_digest=hashlib.sha256(BP.canon(man)).hexdigest(), run_digest=None, author=author,
                    cairo_date=cairo,
                    detail={'role': 'evaluation_spend', 'format': FORMAT, 'registration_commit': binding['y'],
                            'eval_digest': inputs_digest(packets, man, pre), 'spent_at_utc': BP.ts(now),
                            'out_dir': os.path.abspath(out_dir),
                            'packets': {i: packets[i]['packet_sha256'] for i in sorted(packets)}})


def evaluate(repo: str, *, registration: str, packets_dir: str, manifest: str, out_dir: str,
             btc_context=None) -> dict:
    binding = preflight(repo, registration, stage='evaluate', out_dir=out_dir)
    pre, sha = BP.load_prereg(repo)
    import run_vwaprev_candidate as VC
    syms = [x['symbol'] for x in VC.frozen_universe()['symbols']]
    packets, man = _load_inputs(packets_dir, manifest)
    if inputs_digest(packets, man, pre) != binding['spend']['detail'].get('eval_digest'):
        raise EvalError('evaluation refused: the packets / manifest are not the inputs the evaluation spend recorded')
    doc = evaluate_packets(packets, pre, syms, y=binding['y'], manifest=man, btc_context=btc_context)
    doc['binding'] = {k: v for k, v in binding.items() if k != 'spend'}
    doc['spend'] = binding['spend']
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, OUT_NAME), 'x', encoding='utf-8', newline='\n') as f:     # 'x': never overwrite
        f.write(json.dumps(doc, sort_keys=True, indent=1) + '\n')
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('cmd', choices=['collect', 'packets', 'spend', 'evaluate'])
    ap.add_argument('--registration', required=True, help='the immutable registration tag / SHA of Y')
    ap.add_argument('--cache')
    ap.add_argument('--manifest')
    ap.add_argument('--packets')
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'collect':
            collect_holdout(ROOT, registration=a.registration, cache=a.cache, manifest=a.manifest)
        elif a.cmd == 'packets':
            run_holdout_packets(ROOT, registration=a.registration, cache=a.cache, out_dir=a.out)
        elif a.cmd == 'spend':
            spend(ROOT, registration=a.registration, packets_dir=a.packets, manifest=a.manifest, out_dir=a.out)
        else:
            evaluate(ROOT, registration=a.registration, packets_dir=a.packets, manifest=a.manifest, out_dir=a.out)
    except (EvalError, BP.BreadthPreregError, L.LedgerError) as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
