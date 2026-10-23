"""FUND-GAP-01 G3: results-blind first-failure proof for the four wf[0] `incomplete.funding` cells (Codex 6358557359).

The REX packet `rex-core10-gapfill-splitcut-v1` is INCOMPLETE for walk_forward[0], long, configs
`range_vwap_rev.v1.rg+0.5` and `B2`, rows `base` and `fees_slip_x2`, all `incomplete.funding`. The G2 scan found no
stored-series defect in that window and an evaluator-identical funding view, so the cause lies in how the evaluator
CALLS `range_vwap_rev.funding_events`. This tool runs the real evaluator path for wf[0] only - `pit_run.rex_split_pass`
-> `range_vwap_rev.run_book` -> `bar_funding` -> `funding_events` - and records, per target cell, the first funding
call that raises, classified by the exact predicate of `funding_events` that failed:

  * `off_grid`              `t_nom` refused a row in the call's span (time tolerance, interval type or grid);
  * `missing_anchor`        no event with T_nom <= entry_open (the phase anchor);
  * `nonconsecutive_pair`   two consecutive chain events not exactly one interval apart;
  * `uncovered_terminal`    the event after the last covered row falls inside the call's window.

The classifier repeats those steps on the same inputs and must name the same failing time as the real raise (else it
refuses). Output is identity, times, bounds, the inclusive flag, the nearby rows' timestamps / nominal instants /
typed intervals and the predicate - never a rate, mark, quantity, trade, P&L or ranking. Other cells get an empty
stub (run_book is stateless per call), so nothing beyond the four cells is evaluated.

No Access opens and nothing is recorded in a ledger: the tape is read through `view_rows`, a mirror of
`pit.View._rows` over the same `Dataset.files` / `Dataset.slice` (proved equal to the real Access path by the tests),
for the wf[0] access window only, and the pinned gap-fill add-on is validated and merged exactly as `rex_load` does.
Any selected file reaching the sealed holdout refuses before it is read. No fetch, no repair, no other split.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import funding_gap_scan as F                                                                # noqa: E402
import range_vwap_rev as RV                                                                 # noqa: E402
import costs as C                                                                           # noqa: E402

FORMAT = 'zb-fundgap-g3/1'
SPLIT = 'walk_forward[0]'
SIDE = 'long'
CELLS = tuple((SPLIT, SIDE, c, r) for c in ('range_vwap_rev.v1.rg+0.5', 'B2') for r in ('base', 'fees_slip_x2'))
VARIANT = 'range_vwap_rev.v1-splitcut'
DEV_WINDOW = '2021-01-04T00:00:00Z/2026-09-28T00:00:00Z'
PREDICATES = ('off_grid', 'missing_anchor', 'nonconsecutive_pair', 'uncovered_terminal')
NEAR_H = 24                                        # nearby rows: +- this many hours around the failing instant
# The omission contract as an explicit output-schema ALLOWLIST (Codex 6362587273): every key a report may carry, with
# its exact leaf types. Anything else - a new key, a float, a nested object where a leaf belongs - refuses.
_S, _I, _B, _N = str, int, bool, type(None)
_CENSOR = {'reason': (_S, _N), 'class': (_S, _N), 'symbol': (_S, _N), 'phase': (_S, _N),
           **{f + sfx: (t, _N) for f in ('cycle', 'first_unaccountable', 'last_valid')
              for sfx, t in (('_ms', _I), ('_utc', _S))}}
_NEAR = {'time_ms': (_I,), 'utc': (_S,), 't_nom_ms': (_I, _N), 't_nom_utc': (_S, _N),
         'interval_hours': (_I, _S, _N), 'interval_type': (_S,)}
_CELL = {'split': (_S,), 'side': (_S,), 'config': (_S,), 'row': (_S,), 'status': (_S, _N), 'censor': _CENSOR,
         'predicate': (_S, _N), 'note': (_S,), 'failing_ms': (_I,), 'failing_utc': (_S,), 'inclusive': (_B,),
         'entry_open_ms': (_I,), 'entry_open_utc': (_S,), 'bar_lo_ms': (_I,), 'bar_lo_utc': (_S,),
         'bar_hi_ms': (_I,), 'bar_hi_utc': (_S,), 'causal': (_B,), 'nearby_rows': [_NEAR]}
SCHEMA = {'format': (_S,), 'purpose': (_S,), 'evaluator_path': (_S,),
          'split': {'key': (_S,), 'lo': (_S,), 'hi': (_S,), 'first_decision': (_S,)},
          'access_window': {'lo': (_S,), 'hi': (_S,), 'mirror': (_S,)},
          'variant': (_S,), 'manifest_digest': (_S, _N), 'universe': (_S, _N), 'notice_sha256': (_S,),
          'addon_sha256': (_S,), 'addon_provenance_digest': (_S, _N), 'symbols': [(_S,)], 'predicates': [(_S,)],
          'cells': [_CELL], 'files_read': [{'path': (_S,), 'sha256': (_S,)}],
          'provenance': {'commit': (_S,), 'clean': (_B,)}}
HOUR = RV.HOUR


class G3Error(Exception):
    """Refuse: holdout, digest, identity or classifier disagreement. Nothing is written."""


# ------------------------------------------------------------------ the classifier (same steps as funding_events)
def classify(x, entry_open: int, lo: int, hi: int, incl: bool) -> tuple[str, int]:
    """(predicate, failing ms) of the first check of `RV.funding_events(x, entry_open, lo, hi, incl=incl)` that fails,
    in its own order; None when every check passes."""
    span_lo = entry_open - (C.MAX_FUNDING_INTERVAL_H * HOUR) - RV.FUNDING_TOL
    rows = []
    for r in x.funding:
        if span_lo <= r.time_ms <= hi + RV.FUNDING_TOL:
            try:
                rows.append((RV.t_nom(r), r))
            except RV.Incomplete as e:
                return 'off_grid', e.first_ms
    anchors = [(t, r) for t, r in rows if t <= entry_open]
    if not anchors:
        return 'missing_anchor', entry_open
    chain = [anchors[-1]] + [(t, r) for t, r in rows if anchors[-1][0] < t <= hi]
    for (ta, ra), (tb, rb) in zip(chain, chain[1:]):
        if (tb - ta) not in {ra.interval_hours * HOUR, rb.interval_hours * HOUR}:
            return 'nonconsecutive_pair', ta
    nxt = chain[-1][0] + chain[-1][1].interval_hours * HOUR
    if (nxt <= hi) if incl else (nxt < hi):
        return 'uncovered_terminal', nxt
    return None


def nearby(x, t: int) -> list[dict]:
    """The funding rows within NEAR_H hours of `t`: timestamp, nominal instant (None when t_nom refuses it) and the
    TYPED interval - no rate."""
    out = []
    for r in sorted(x.funding, key=lambda r: r.time_ms):
        if abs(r.time_ms - t) <= NEAR_H * HOUR:
            try:
                tn = RV.t_nom(r)
            except RV.Incomplete:
                tn = None
            out.append({'time_ms': r.time_ms, 'utc': F.utc(r.time_ms), 't_nom_ms': tn,
                        't_nom_utc': None if tn is None else F.utc(tn), 'interval_hours': r.interval_hours,
                        'interval_type': type(r.interval_hours).__name__})
    return out


# ------------------------------------------------------------------ the capture hooks (real path, results discarded)
@contextlib.contextmanager
def capture(configs, cells=CELLS, split=SPLIT):
    """Patch RV.run_book / RV.funding_events for one split pass: target cells run the real run_book with the first
    raising funding call recorded; every other cell returns an empty stub. Yields {cell: record}."""
    real_book, real_fe = RV.run_book, RV.funding_events
    got, cur = {}, [None]
    names = [(c['cfg'], c['mode'], c['name']) for c in configs]

    def fe(x, entry_open, lo, hi, *, incl):
        try:
            return real_fe(x, entry_open, lo, hi, incl=incl)
        except RV.Incomplete as e:
            k = cur[0]
            if k is not None and 'call' not in got[k]:
                got[k]['call'] = dict(x=x, entry_open=entry_open, lo=lo, hi=hi, incl=incl, first_ms=e.first_ms)
            raise

    def book(tape, cfg, side, *a, row='base', mode=None, **kw):
        name = next((n for c, m, n in names if c == cfg and m == mode), None)
        k = (split, side, name, row)
        if k not in cells:
            return {'trades': [], 'status': RV.COMPLETE}               # not a target: never evaluated
        got[k], cur[0] = {}, k
        try:
            res = real_book(tape, cfg, side, *a, row=row, mode=mode, **kw)
        finally:
            cur[0] = None
        cz = res.get('censor') or {}
        got[k]['censor'] = {f: cz.get(f) for f in ('reason', 'class', 'symbol', 'phase', 'cycle_ms',
                                                   'first_unaccountable_ms', 'last_valid_ms')}
        got[k]['status'] = res['status']
        return res
    RV.run_book, RV.funding_events = book, fe
    try:
        yield got
    finally:
        RV.run_book, RV.funding_events = real_book, real_fe


def cell_record(k, rec: dict) -> dict:
    """The emitted record of one cell: identity, censor times/phase, call bounds, predicate, nearby rows."""
    split, side, config, row = k
    out = {'split': split, 'side': side, 'config': config, 'row': row, 'status': rec.get('status')}
    cz = rec.get('censor') or {}
    out['censor'] = {f: cz.get(f) for f in ('reason', 'class', 'symbol', 'phase')}
    for f in ('cycle_ms', 'first_unaccountable_ms', 'last_valid_ms'):
        out['censor'][f] = cz.get(f)
        out['censor'][f.replace('_ms', '_utc')] = None if cz.get(f) is None else F.utc(cz[f])
    call = rec.get('call')
    if call is None:
        out['predicate'] = None
        out['note'] = 'no funding_events call raised in this cell'
        return out
    got = classify(call['x'], call['entry_open'], call['lo'], call['hi'], call['incl'])
    if got is None or got[1] != call['first_ms']:
        raise G3Error(f'{k}: the classifier ({got}) disagrees with the real funding_events raise at '
                      f'{F.utc(call["first_ms"])}')
    pred, t = got
    out.update(predicate=pred, failing_ms=t, failing_utc=F.utc(t), inclusive=call['incl'],
               entry_open_ms=call['entry_open'], entry_open_utc=F.utc(call['entry_open']),
               bar_lo_ms=call['lo'], bar_lo_utc=F.utc(call['lo']), bar_hi_ms=call['hi'], bar_hi_utc=F.utc(call['hi']),
               causal=(cz.get('reason') == 'incomplete.funding' and cz.get('first_unaccountable_ms') == t),
               nearby_rows=nearby(call['x'], t))
    return out


def check_schema(doc, schema, err=None) -> None:
    """Refuse `doc` unless every key is in the allowlist `schema` and every leaf has one of its exact types (a dict
    schema is an object, [s] a list of s, a tuple the allowed leaf types; bool never passes as int)."""
    err = err or G3Error
    def walk(o, sc, path):
        if isinstance(sc, dict):
            if type(o) is not dict:
                raise err(f'omission contract: {path or "/"} must be an object')
            for key, v in o.items():
                if key not in sc:
                    raise err(f'omission contract: field {path}/{key} is not in the report allowlist')
                walk(v, sc[key], f'{path}/{key}')
        elif isinstance(sc, list):
            if type(o) is not list:
                raise err(f'omission contract: {path} must be a list')
            for v in o:
                walk(v, sc[0], f'{path}[]')
        elif type(o) not in sc:
            raise err(f'omission contract: {path} has type {type(o).__name__}, allowed {[t.__name__ for t in sc]}')
    walk(doc, schema, '')


def assert_blind(doc) -> None:
    """The G3 omission contract: the report matches SCHEMA exactly (no rate, result or any unlisted field)."""
    check_schema(doc, SCHEMA)


# ------------------------------------------------------------------ the tape (mirror of the Access read)
def view_rows(ds, series, symbol, interval, w_lo: int, t: int, n=None, since=None) -> tuple:
    """`pit.View._rows` over a window [w_lo, ...] at decision time t, without a Window: same file order, same
    `Dataset.slice`, same early stop. A selected file reaching the sealed holdout refuses before it is read."""
    lo, out = max(w_lo, since or w_lo), []
    for f in reversed(ds.files(series, symbol, interval)):
        if f['first_open_ms'] is None or f['first_open_ms'] > t or f['last_open_ms'] < lo:
            continue
        if f['last_open_ms'] >= F.parse_utc(F.SEALED_END):
            raise G3Error(f'{symbol}|{series}: a selected file reaches the sealed holdout; refusing')
        out = ds.slice(f, lo, t) + out
        if n is not None and len(out) >= n:
            break
    return tuple(out[-n:] if n is not None else out)


def series_of(ds, s: str, rule: dict, acc_lo: int, acc_hi: int):
    """`pit_run.rex_series` for an access window [acc_lo, acc_hi] (acc_hi includes the funding tail)."""
    import pit_run as PR                                                                    # noqa: E402
    end = acc_hi - PR.REX_FUNDING_TAIL_MS
    bars = {b.open_ms: b for b in view_rows(ds, 'klines', s, '4h', acc_lo, end, n=(end - acc_lo) // RV.TF + 1)}
    marks = {b.open_ms: b.close for b in view_rows(ds, 'markPriceKlines', s, '1h', acc_lo, end,
                                                   n=(end - acc_lo) // HOUR + 1)}
    fund = view_rows(ds, 'fundingRate', s, None, acc_lo, acc_hi, since=acc_lo)
    return RV.Series(bars, {k: rule[k] for k in PR.RULES_FIELDS}, tuple(fund), marks)


def files_read(ds, symbols, acc_lo: int, acc_hi: int) -> list[dict]:
    out = []
    for s in symbols:
        for series, iv in (('klines', '4h'), ('markPriceKlines', '1h'), ('fundingRate', None)):
            for f in ds.files(series, s, iv):
                if f['first_open_ms'] is not None and f['first_open_ms'] <= acc_hi and f['last_open_ms'] >= acc_lo:
                    out.append({'path': f['path'], 'sha256': f['sha256']})
    return out


def run(*, store: str, notice: str, notice_sha256: str, addon: str, addon_sha256: str, addon_report: str,
        addon_zips: str, repo: str = F.REPO) -> dict:
    """The wf[0] pass through the real evaluator with the capture hooks; returns the results-blind report."""
    import pit_run as PR                                                                    # noqa: E402
    import rex_addon as RA                                                                  # noqa: E402
    src = PR.rex_sources(repo)
    plan = PR.rex_plan(repo)
    dev_lo, dev_hi = PR.rex_window(DEV_WINDOW, plan)
    seg = next((g for g in PR.rex_segments(plan, dev_lo, dev_hi) if g['key'] == SPLIT), None)
    if seg is None:
        raise G3Error(f'{SPLIT} is not a development split of the committed plan')
    if seg['hi'] > F.parse_utc(F.SEALED_END):
        raise G3Error('the split reaches the sealed holdout; refusing')
    symbols = list(src['static']['core_10'])
    art = PR._rex_read_artifact(notice, notice_sha256)
    _cid, vrec = PR.rex_variant(VARIANT)
    with open(addon, 'rb') as fh:
        raw = fh.read()
    with open(addon_report, encoding='utf-8') as fh:
        report = json.load(fh)                      # run_r4: the signed gap report is passed parsed
    added = RA.validate(RA.load_pinned(raw, addon_sha256), report, manifest_digest=src['manifest']['digest'],
                        symbols=symbols, lo=dev_lo, hi=dev_hi, cutoff=PR.rex_sealed_cutoff(plan), zips=addon_zips,
                        addon_sha256=addon_sha256)
    ds = PR.StaticDataset(src['manifest'], store, src['static'], symbols)
    acc_lo, acc_hi = seg['lo'], seg['hi'] + PR.REX_FUNDING_TAIL_MS
    tape = RA.merge({s: series_of(ds, s, src['rules'][s], acc_lo, acc_hi) for s in symbols}, added)
    env = {'tape': tape, 'notice_artifact': art, 'notice_sha256': notice_sha256, 'variant_record': vrec}
    with capture(PR.rex_configs()) as got:
        PR.rex_split_pass(env, seg, symbols)
    missing = [k for k in CELLS if k not in got]
    if missing:
        raise G3Error(f'target cell(s) never ran: {missing}')
    cells = [cell_record(k, got[k]) for k in CELLS]
    doc = {'format': FORMAT, 'purpose': 'FUND-GAP-01 G3 results-blind first-failure proof (wf[0] only)',
           'evaluator_path': 'pit_run.rex_split_pass -> range_vwap_rev.run_book -> bar_funding -> funding_events',
           'split': {'key': seg['key'], 'lo': F.utc(seg['lo']), 'hi': F.utc(seg['hi']),
                     'first_decision': F.utc(seg['first'])},
           'access_window': {'lo': F.utc(acc_lo), 'hi': F.utc(acc_hi), 'mirror': 'pit.View._rows (no Access opened)'},
           'variant': VARIANT, 'manifest_digest': src['manifest']['digest'], 'universe': src['static'].get('digest'),
           'notice_sha256': notice_sha256, 'addon_sha256': addon_sha256,
           'addon_provenance_digest': (added.get('provenance') or {}).get('digest'),
           'symbols': symbols, 'predicates': list(PREDICATES), 'cells': cells,
           'files_read': files_read(ds, symbols, acc_lo, acc_hi)}
    assert_blind(doc)
    return doc


def _git(*args) -> str:
    return subprocess.run(['git', '-C', F.REPO, *args], capture_output=True, text=True, check=True).stdout.strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    for k in ('--store', '--notice', '--notice-sha256', '--addon', '--addon-sha256', '--addon-report', '--addon-zips',
              '--out'):
        ap.add_argument(k, required=True)
    a = ap.parse_args(argv)
    if os.path.exists(a.out):
        print(f'error: {a.out} exists (a frozen report is never overwritten)', file=sys.stderr)
        return 2
    try:
        doc = run(store=a.store, notice=a.notice, notice_sha256=a.notice_sha256, addon=a.addon,
                  addon_sha256=a.addon_sha256, addon_report=a.addon_report, addon_zips=a.addon_zips)
        doc['provenance'] = {'commit': _git('rev-parse', 'HEAD'), 'clean': _git('status', '--porcelain') == ''}
    except Exception as e:                                       # every refusal: nothing written
        print(f'error: {type(e).__name__}: {e}', file=sys.stderr)
        return 2
    text = json.dumps(doc, sort_keys=True, indent=1) + '\n'
    with open(a.out, 'x', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    print(f'{a.out} sha256 {hashlib.sha256(text.encode()).hexdigest()} '
          f'predicates {[c["predicate"] for c in doc["cells"]]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
