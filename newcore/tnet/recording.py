"""Cassettes of runner-target (testnet) runs: every HTTP interaction of a scenario - the factory boot, every Runner
cycle, the final truth and the teardown - goes into ONE sanitized, leak-audited cassette (newcore.venue.cassette:
recorder OUTSIDE the fault seam, so the cassette holds exactly what the transport saw, injected faults included),
plus a replay sidecar:

    tnet-<run nonce>-<scenario id>.json        zb-newcore-cassette/1
    tnet-<run nonce>-<scenario id>.meta.json   zb-newcore-tnet-replay/1: the exact spec, run nonce, account, symbols,
                                               settle, the candle-close tape, the preflight baseline, the verdict
    tnet-<run nonce>-preflight.json            the suite preflight (not replayed)

A fresh TestnetTarget (fresh factory boot) is built per scenario so every cassette replays on its own
(newcore.tnet.replay). Nothing is written when the leak audit finds anything: the scenario's cassette is None and the
CLI exits 5.
"""
import json
import os

from newcore.venue.cassette import CassetteLeak, CassetteRecorder
from newcore.venue.tnet import _audit

from .driver import (EXIT_DEADLINE, EXIT_PREFLIGHT, FAIL, INCONCLUSIVE, ScenarioResult, SuiteResult, adopted_orders,
                     attempt_nonce, run_scenario, suite_exit_code)

REPLAY_FORMAT = 'zb-newcore-tnet-replay/1'


def bundle_base(cassette_dir, run_nonce, scenario_id):
    return os.path.join(cassette_dir, f'tnet-{run_nonce}-{scenario_id.lower()}')


def _write(path, text):
    tmp = f'{path}.tmp{os.getpid()}'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def save_bundle(recorder, base, meta, redact):
    """Audit both texts first (CassetteLeak / ReportLeak: nothing is written), then write the cassette and its meta."""
    text = recorder.to_json()
    meta_text = json.dumps(meta, indent=1, sort_keys=True) + '\n'
    _audit((meta_text,), [v for v in redact if isinstance(v, str)])
    path = _write(base + '.json', text)
    _write(base + '.meta.json', meta_text)
    return path


def replay_meta(spec, result, *, run_nonce, account_id, symbols, settle_ms, baseline):
    return {'format': REPLAY_FORMAT, 'spec': spec, 'run_nonce': run_nonce, 'account_id': account_id,
            'symbols': list(symbols), 'settle_ms': settle_ms, 'cycle_times': list(result.cycle_times),
            'baseline': [[s, side, str(q)] for (s, side), q in sorted(baseline.items())],
            'verdict': result.verdict, 'error': result.error,
            'assertions': [[n, ok] for n, ok, _ in result.assertions]}


def run_recorded_suite(specs, make_target, *, run_nonce, cassette_dir, redact, monotonic, min_balance=None,
                       adopt_foreign=(), on_result=None, note='TNET-01 runner scenario'):
    """make_target(recorder_factory) -> TestnetTarget. Returns (SuiteResult, preflight cassette path | None,
    cassette errors [(scenario id, reason)]). Ctrl+C in the boot / preflight / between scenarios keeps the results
    so far (SuiteResult.interrupted): the CLI still writes the report."""
    st = {'pre': None, 'pre_path': None, 'results': [], 'errors': []}
    try:
        return _recorded_suite(st, specs, make_target, run_nonce=run_nonce, cassette_dir=cassette_dir,
                               redact=redact, monotonic=monotonic, min_balance=min_balance,
                               adopt_foreign=adopt_foreign, on_result=on_result, note=note)
    except KeyboardInterrupt:
        res = st['results']
        code = suite_exit_code(res) if any(r.residue for r in res) else EXIT_DEADLINE
        return SuiteResult(st['pre'], res, code, interrupted=True), st['pre_path'], st['errors']


def _recorded_suite(st, specs, make_target, *, run_nonce, cassette_dir, redact, monotonic, min_balance,
                    adopt_foreign, on_result, note):
    redact = tuple(v for v in redact if isinstance(v, str))
    errors = st['errors']

    def recorder(inner):
        return CassetteRecorder(inner, redact=redact, note=note)
    pre_t = make_target(recorder)
    symbols = sorted({s['symbol'] for s in specs if 'testnet' in s['targets']})
    kw = {} if min_balance is None else {'min_balance': min_balance}
    pre = st['pre'] = pre_t.preflight(symbols, adopt_foreign=adopt_foreign, **kw)
    pre_path = None
    try:
        text = pre_t.recorder.to_json()
        pre_path = st['pre_path'] = _write(bundle_base(cassette_dir, run_nonce, 'preflight') + '.json', text)
    except (CassetteLeak, OSError) as ex:
        errors.append(('preflight', type(ex).__name__))
    if not pre.ok:
        return SuiteResult(pre, [], EXIT_PREFLIGHT), pre_path, errors
    results = st['results']
    for spec in specs:
        if 'testnet' not in spec['targets']:
            r = run_scenario(spec, pre_t, run_nonce=run_nonce, monotonic=monotonic)      # SKIPPED, sends nothing
        else:
            r, booted = None, True
            for attempt in range(1, spec.get('attempts', 1) + 1):      # a bracket retries while INCONCLUSIVE
                nonce = attempt_nonce(run_nonce, attempt)
                try:
                    t = make_target(recorder)
                except Exception as ex:                              # noqa: BLE001 - a refused boot is a FAIL
                    r = ScenarioResult(spec['id'], spec['name'], 'testnet', FAIL,
                                       error=f'boot: {type(ex).__name__}: {ex}')
                    r.assertions.append(('target boot', False, r.error))
                    booted = False
                    break
                r = run_scenario(spec, t, run_nonce=nonce, monotonic=monotonic, baseline=pre.baseline,
                                 adopted=adopted_orders(pre))
                r.attempts = attempt
                meta = replay_meta(spec, r, run_nonce=nonce, account_id=t.config.account_id,
                                   symbols=t.config.symbols, settle_ms=t.settle_ms, baseline=pre.baseline)
                base = bundle_base(cassette_dir, run_nonce, spec['id'] + ('' if attempt == 1 else f'-a{attempt}'))
                try:
                    r.cassette = save_bundle(t.recorder, base, meta, redact)
                except Exception as ex:                              # noqa: BLE001 - leak / disk: exit 5, no file
                    errors.append((spec['id'], type(ex).__name__))
                if r.verdict != INCONCLUSIVE or r.residue or r.interrupted:
                    break
            if not booted:
                results.append(r)
                break
        results.append(r)
        if on_result is not None:
            on_result(r)
        if r.residue or r.interrupted:
            break
    return SuiteResult(pre, results, suite_exit_code(results)), pre_path, errors
