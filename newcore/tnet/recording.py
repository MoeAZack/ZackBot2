"""Cassettes of runner-target (testnet) runs: every HTTP interaction of a scenario - the factory boot, every Runner
cycle, the final truth and the teardown - goes into ONE sanitized, leak-audited cassette (newcore.venue.cassette:
recorder OUTSIDE the fault seam, so the cassette holds exactly what the transport saw, injected faults included),
plus a replay sidecar:

    tnet-<run nonce>-<scenario id>.json        zb-newcore-cassette/1
    tnet-<run nonce>-<scenario id>.meta.json   zb-newcore-tnet-replay/1: the exact spec, run nonce, account, symbols,
                                               settle, the candle-close tape, the preflight baseline, the verdict
    tnet-<run nonce>-preflight.json            the suite preflight (not replayed)

A long scenario whose cassette passes one audit budget (T04: 31 cycles) is SEGMENTED at cycle checkpoints (Codex T04
triage 6087020052; CassetteRecorder.to_segments): every segment is a complete, separately leak-audited cassette

    tnet-<run nonce>-<scenario id>.segNN.json  zb-newcore-cassette/1 with 'segment': {index, count, first}
    tnet-<run nonce>-<scenario id>.json        zb-newcore-cassette-set/1: the ordered segment files, their SHA-256,
                                               interaction counts and first indexes (written LAST: the commit point)

All segments (and the meta) are audited before any file is written; a write that fails removes what it wrote, so no
manifest ever names a missing segment. A cassette that fits one budget is written exactly as before.

A fresh TestnetTarget (fresh factory boot) is built per scenario so every cassette replays on its own
(newcore.tnet.replay). Nothing is written when the leak audit finds anything: the scenario's cassette is None and the
CLI exits 5.
"""
import hashlib
import json
import os

from newcore.venue.cassette import CassetteLeak, CassetteRecorder
from newcore.venue.safe_text import exc_text
from newcore.venue.tnet import EvidenceInterrupt, _audit, commit_evidence

from .driver import (EXIT_DEADLINE, EXIT_PREFLIGHT, FAIL, INCONCLUSIVE, ScenarioResult, SuiteResult, adopted_orders,
                     attempt_nonce, run_scenario, suite_exit_code)

REPLAY_FORMAT = 'zb-newcore-tnet-replay/1'
SEGMENT_SET_FORMAT = 'zb-newcore-cassette-set/1'


def bundle_base(cassette_dir, run_nonce, scenario_id):
    return os.path.join(cassette_dir, f'tnet-{run_nonce}-{scenario_id.lower()}')


def _write(path, text):
    tmp = f'{path}.tmp{os.getpid()}'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:                                          # never a leftover *.tmpPID file
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


def segment_name(base, index):
    return f'{os.path.basename(base)}.seg{index:02d}.json'


def segment_manifest(base, parts):
    """The zb-newcore-cassette-set/1 manifest of [(text, first, count), ...] (two or more segments)."""
    return {'format': SEGMENT_SET_FORMAT, 'interactions': sum(n for _, _, n in parts),
            'segments': [{'file': segment_name(base, i), 'sha256': hashlib.sha256(t.encode('utf-8')).hexdigest(),
                          'first': first, 'interactions': n} for i, (t, first, n) in enumerate(parts, 1)]}


def save_bundle(recorder, base, meta, redact, flag=None):
    """Audit every text first (CassetteLeak / ReportLeak: nothing is written), then write the cassette and its meta.
    A segmented cassette: the segments, the meta, then the manifest last; any failed write removes what this commit
    wrote and re-raises (fail closed: never a manifest naming a missing or partial segment)."""
    parts = recorder.to_segments()
    meta_text = json.dumps(meta, indent=1, sort_keys=True) + '\n'
    values = [v for v in redact if isinstance(v, str)]
    if len(parts) == 1:
        text = parts[0][0]
        _audit((meta_text,), values)

        def commit():                                 # SIGINT deferred, one retry (tnet.commit_evidence)
            p = _write(base + '.json', text)
            _write(base + '.meta.json', meta_text)
            return p
        return commit_evidence(commit, flag)
    manifest_text = json.dumps(segment_manifest(base, parts), indent=1, sort_keys=True) + '\n'
    _audit((meta_text, manifest_text), values)
    folder = os.path.dirname(base)
    files = [(os.path.join(folder, segment_name(base, i)), t) for i, (t, _, _) in enumerate(parts, 1)]
    files += [(base + '.meta.json', meta_text), (base + '.json', manifest_text)]

    def commit_set():
        written = []
        try:
            for path, text in files:
                written.append(_write(path, text))
        except BaseException:
            for path in written:
                try:
                    os.remove(path)
                except OSError:
                    pass
            raise
        return written[-1]
    return commit_evidence(commit_set, flag)


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
    st = {'pre': None, 'pre_path': None, 'results': [], 'errors': [], 'evidence': EvidenceInterrupt()}
    try:
        return _recorded_suite(st, specs, make_target, run_nonce=run_nonce, cassette_dir=cassette_dir,
                               redact=redact, monotonic=monotonic, min_balance=min_balance,
                               adopt_foreign=adopt_foreign, on_result=on_result, note=note)
    except (KeyboardInterrupt, SystemExit):
        res = st['results']                          # 8 / 7 from the completed results win over 6
        code = suite_exit_code(res, interrupted=True)
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
        pre_path = st['pre_path'] = commit_evidence(
            lambda: _write(bundle_base(cassette_dir, run_nonce, 'preflight') + '.json', text), st['evidence'])
    except (CassetteLeak, OSError) as ex:
        errors.append(('preflight', type(ex).__name__))
    if not pre.ok:                                    # Codex P2: a refusal (4) wins over an absorbed Ctrl+C (6)
        return SuiteResult(pre, [], EXIT_PREFLIGHT, interrupted=st['evidence'].hit), pre_path, errors
    if st['evidence'].hit:                            # Codex P1: a Ctrl+C absorbed while the preflight cassette was
        return SuiteResult(pre, [], EXIT_DEADLINE, interrupted=True), pre_path, errors   # written: no scenario
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
                                       error=f'boot: {exc_text(ex)}')
                    r.assertions.append(('target boot', False, r.error))
                    booted = False
                    break
                r = run_scenario(spec, t, run_nonce=nonce, monotonic=monotonic, baseline=pre.baseline,
                                 adopted=adopted_orders(pre), interrupt_pending=lambda: st['evidence'].hit)
                r.attempts = attempt
                meta = replay_meta(spec, r, run_nonce=nonce, account_id=t.config.account_id,
                                   symbols=t.config.symbols, settle_ms=t.settle_ms, baseline=pre.baseline)
                base = bundle_base(cassette_dir, run_nonce, spec['id'] + ('' if attempt == 1 else f'-a{attempt}'))
                try:
                    r.cassette = save_bundle(t.recorder, base, meta, redact, st['evidence'])
                except Exception as ex:                              # noqa: BLE001 - leak / disk: exit 5, no file
                    errors.append((spec['id'], type(ex).__name__))
                if st['evidence'].hit:                    # Codex P1: Ctrl+C during this scenario's evidence
                    r.evidence_interrupted = True         # commit: keep its (complete) result, stop before any
                if (r.verdict != INCONCLUSIVE or r.residue or r.interrupted   # later send (P2: not a stop)
                        or r.evidence_interrupted):
                    break
            if not booted:
                results.append(r)
                break
        results.append(r)
        if on_result is not None:
            on_result(r)
        if r.residue or r.interrupted or r.evidence_interrupted:
            break
    hit = st['evidence'].hit
    return SuiteResult(pre, results, suite_exit_code(results, interrupted=hit), interrupted=hit), pre_path, errors
