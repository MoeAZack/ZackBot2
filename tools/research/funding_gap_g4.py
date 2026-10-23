"""FUND-GAP-01 G4: funding-cadence transition proof for the G3 `uncovered_terminal` at SOLUSDT 2022-11-18 (Codex
6362587273). Evidence only: no change to `funding_events`, no repair, no performance run, no holdout.

G3 showed all four wf[0] cells fail the terminal check of `range_vwap_rev.funding_events` at 2022-11-18T10:00Z: the
last covered row (08:00Z) carries a 2h interval, the next stored row is 16:00Z with an 8h interval. This tool:

  1. `provenance`   traces `funding_interval_hours` for the SOL rows at 06:00 / 08:00 / 16:00Z to the archive file
                    (path, manifest sha256, source URL), the CSV line and the raw time / interval fields; the parse
                    and add-on steps that deliver it to the evaluator are named (the add-on never touches funding);
  2. `transitions`  enumerates every cadence change in the non-holdout prereg window for CORE-10 by timestamp and
                    TYPED interval only, and classifies each gap as the old interval, the new one, both or neither,
                    with the number of slots the shorter cadence would place inside it (`hidden_slots`);
  3. `simulate`     tests two terminal-coverage rules, each composed over the real `funding_events`, against a
                    synthetic family of true schedules (cadence switches, both switch-row label conventions) with
                    missing-row mutations: `last_row` (the current rule) and `lookahead1` (a bounded one-row
                    lookahead that judges the terminal by the first row after the window when one lies within one
                    maximum interval, valid only under the either-row interval rule). Counts, the first mismatch
                    and the counterexample sets (identical observed tapes with different truth) - nothing else.

Never a rate, mark, quantity, trade or result: the CSV rate column is not parsed. Funding files that reach the sealed
holdout are skipped unread (listed by path); the enumeration end is therefore the last pre-holdout file. In the frozen
report every CORE-10 symbol ends on 2026-08-31 (the 2026-09 files reach 30 Sep), so "3 transitions total" means 3 from
2021-01-04 through 31 Aug 2026 - September 2026 is not covered.

Ruling (Codex 6363158911): option (a) - `last_row` stays, the four wf[0] cells stay INCOMPLETE ("SOLUSDT 2h->8h cadence
switch on 18 Nov 2022 is not provable from the stored tape"); `lookahead1` is not a completeness proof.
Known limit, tracked as a later G5 evidence line (no repair / rerun now): the 2022-11-10 SOLUSDT 04:00 4h->2h pair
(gap 4h, class `old`) passes today's either-row rule, but under the backward label convention it hides a missing 02:00
event - the same false-accept class `last_row` shows in the simulation.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import itertools
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C                                                                           # noqa: E402
import funding_gap_g3 as G3                                                                 # noqa: E402
import funding_gap_scan as F                                                                # noqa: E402
import manifest as M                                                                        # noqa: E402
import range_vwap_rev as RV                                                                 # noqa: E402

FORMAT = 'zb-fundgap-g4/1'
HOUR = RV.HOUR
MAX_H = C.MAX_FUNDING_INTERVAL_H
TARGET_SYMBOL = 'SOLUSDT'
TARGET_TIMES = ('2022-11-18T06:00:00Z', '2022-11-18T08:00:00Z', '2022-11-18T16:00:00Z')
SCAN_LO = G3.DEV_WINDOW.split('/')[0]
RULES = ('last_row', 'lookahead1')
CLASSES = ('old', 'new', 'both', 'neither')
CONVENTIONS = ('old_label', 'new_label')     # which interval the row AT a cadence switch carries
DELIVERY = ('manifest.parse_archive_csv: row split; calc_time and funding_interval_hours are columns 0 and 1',
            'pit.Dataset.slice: Funding(time_ms=int(col0), interval_hours=int(col1), available_ms=int(col0))',
            'pit_run.rex_series -> View.funding (Dataset.slice, available_ms <= t)',
            'rex_addon.merge: funding passed through unchanged (the add-on carries klines / markPriceKlines only)',
            'range_vwap_rev.funding_events -> t_nom(row) (hour rounding, interval-grid check)')


class G4Error(Exception):
    pass


# ------------------------------------------------------------------ 1. provenance (time + interval columns only)
def _raw_rows(store: str, f: dict) -> list[tuple[int, bytes, bytes]]:
    """(csv line number, raw calc_time, raw funding_interval_hours) per row of one manifest-verified archive file."""
    p = f['path']
    if f['last_open_ms'] >= F.parse_utc(F.SEALED_END):
        raise G4Error(f'{p}: reaches the sealed holdout; refusing')
    _, published, data = M.read_archive(M.contained(store, p), p)
    if published != f['sha256']:
        raise G4Error(f'{p}: bytes no longer match the manifest sha256')
    rows = M.parse_archive_csv(p, data, M.archive_meta(p))
    if len(rows) != f['rows']:
        raise G4Error(f'{p}: {len(rows)} rows != manifest {f["rows"]}')
    first = 2 if data.split(b'\n', 1)[0].rstrip(b'\r') == M.FUNDING_HEADER else 1
    return [(first + i, r[0], r[1]) for i, r in enumerate(rows)]


def provenance(store: str, man: dict, symbol: str = TARGET_SYMBOL, times=TARGET_TIMES) -> list[dict]:
    out = []
    for ts in times:
        t = F.parse_utc(ts)
        fs = [f for f in man['files'] if f.get('series') == 'fundingRate' and f['symbol'] == symbol
              and f['first_open_ms'] is not None and f['first_open_ms'] - RV.FUNDING_TOL <= t
              <= f['last_open_ms'] + RV.FUNDING_TOL]
        hit = [(f, ln, a, b) for f in fs for ln, a, b in _raw_rows(store, f) if abs(int(a) - t) <= RV.FUNDING_TOL]
        if len(hit) != 1:
            raise G4Error(f'{symbol} {ts}: {len(hit)} stored rows within tolerance (expected exactly one)')
        f, ln, a, b = hit[0]
        out.append({'symbol': symbol, 'nominal_utc': ts, 'path': f['path'], 'sha256': f['sha256'],
                    'source_url': (f.get('source') or {}).get('url'), 'csv_line': ln,
                    'raw_calc_time': a.decode('ascii'), 'raw_funding_interval_hours': b.decode('ascii'),
                    'time_ms': int(a), 'interval_hours': int(b), 'interval_type': type(int(b)).__name__})
    return out


# ------------------------------------------------------------------ 2. cadence transitions (timestamp + interval)
def classify_gap(a_h: int, b_h: int, gap_ms: int) -> str:
    old, new = gap_ms == a_h * HOUR, gap_ms == b_h * HOUR
    return 'both' if old and new else 'old' if old else 'new' if new else 'neither'


def transitions_of(rows) -> list[dict]:
    """Every consecutive pair (by time) whose typed intervals differ, by nominal instant."""
    keyed = []
    for r in sorted(rows, key=lambda r: r.time_ms):
        try:
            keyed.append((RV.t_nom(r), r))
        except RV.Incomplete:
            continue                                     # off-grid rows are G2 defects, not cadence changes
    out = []
    for (ta, ra), (tb, rb) in zip(keyed, keyed[1:]):
        if ra.interval_hours != rb.interval_hours:
            gap = tb - ta
            out.append({'from_utc': F.utc(ta), 'to_utc': F.utc(tb), 'old_h': ra.interval_hours,
                        'new_h': rb.interval_hours, 'gap_h': gap // HOUR, 'class': classify_gap(
                            ra.interval_hours, rb.interval_hours, gap),
                        'hidden_slots': gap // (min(ra.interval_hours, rb.interval_hours) * HOUR) - 1})
    return out


def transitions(store: str, man: dict, symbols, lo: int) -> dict:
    sealed = F.parse_utc(F.SEALED_END)
    per, skipped, read = {}, [], []
    for s in symbols:
        fs = sorted((f for f in man['files'] if f.get('series') == 'fundingRate' and f['symbol'] == s
                     and f['first_open_ms'] is not None and f['last_open_ms'] >= lo - MAX_H * HOUR),
                    key=lambda f: f['first_open_ms'])
        skipped += [f['path'] for f in fs if f['last_open_ms'] >= sealed]
        use = [f for f in fs if f['last_open_ms'] < sealed]
        rows = [F.Row(int(a), int(b)) for f in use for _, a, b in _raw_rows(store, f) if int(a) >= lo - MAX_H * HOUR]
        read += [{'path': f['path'], 'sha256': f['sha256']} for f in use]
        tr = transitions_of(rows)
        per[s] = {'rows': len(rows), 'last_row_utc': F.utc(max(r.time_ms for r in rows)) if rows else None,
                  'transitions': tr}
    allt = [t for v in per.values() for t in v['transitions']]
    return {'window_lo': F.utc(lo), 'symbols': per, 'skipped_sealed_files': skipped, 'files_read': read,
            'counts': {c: sum(t['class'] == c for t in allt) for c in CLASSES},
            'ambiguous': sum(t['hidden_slots'] > 0 for t in allt), 'total': len(allt)}


# ------------------------------------------------------------------ 3. the two terminal rules (over the real check)
_Ev = collections.namedtuple('_Ev', 'time_ms interval_hours rate')     # synthetic: rate is always None


def _series(events) -> RV.Series:
    return RV.Series({}, {}, tuple(_Ev(t, h, None) for t, h in events), {})


def covered(rule: str, x, entry_open: int, lo: int, hi: int, incl: bool) -> bool:
    """Whether `rule` accepts the funding call: `last_row` IS `funding_events`; `lookahead1` only relaxes it - a call
    refused for `uncovered_terminal` alone is accepted when the first row after hi (within one maximum interval) forms
    a pair with the last covered row that is valid under the either-row interval rule. Every other predicate and every
    passing call are unchanged."""
    try:
        RV.funding_events(x, entry_open, lo, hi, incl=incl)
        passed = True
    except RV.Incomplete:
        passed = False
    if rule == 'last_row':
        return passed
    if rule != 'lookahead1':
        raise G4Error(f'unknown rule {rule}')
    if passed or G3.classify(x, entry_open, lo, hi, incl)[0] != 'uncovered_terminal':
        return passed
    rows = sorted(((RV.t_nom(r), r) for r in x.funding), key=lambda p: p[0])
    last = [p for p in rows if p[0] <= hi][-1]
    nxt = next((p for p in rows if hi < p[0] <= hi + MAX_H * HOUR), None)
    return nxt is not None and (nxt[0] - last[0]) in {last[1].interval_hours * HOUR, nxt[1].interval_hours * HOUR}


def schedule(day: int, old: int, new: int, switch: int, conv: str, span=(-48, 72)) -> list[tuple[int, int]]:
    """A true event schedule: `old`-hour events up to `switch`, `new`-hour events after it; the row AT the switch
    carries `old` (old_label) or `new` (new_label)."""
    out = []
    t = day + span[0] * HOUR
    t -= t % (old * HOUR)
    while t <= switch:
        out.append((t, new if (t == switch and conv == 'new_label') else old))
        t += old * HOUR
    t = switch + new * HOUR
    while t <= day + span[1] * HOUR:
        out.append((t, new))
        t += new * HOUR
    return out


def truth(events, entry_open: int, hi: int, incl: bool, kept) -> bool:
    """Coverage in fact: the anchor (last true event <= entry_open) and every true event after it up to hi are kept."""
    anchor = max(t for t, _ in events if t <= entry_open)
    need = {t for t, _ in events if anchor <= t and (t <= hi if incl else t < hi)}
    return need <= {t for t, _ in kept}


def cases(day: int = F.parse_utc('2022-01-03T00:00:00Z')):
    """The synthetic family: every ordered pair of grids, switch instants on the 8h grid around the window, both label
    conventions, both window flags, and every run of 0-3 consecutive true events deleted near the window."""
    for old, new in itertools.product(RV.FUNDING_GRIDS, repeat=2):
        for conv, entry_h, sw_h, incl in itertools.product(CONVENTIONS, (4, 8), (-8, 0, 8, 16, 24), (True, False)):
            entry = day + entry_h * HOUR
            lo, hi = entry + 4 * HOUR, entry + 8 * HOUR
            ev = schedule(day, old, new, day + sw_h * HOUR, conv)
            near = [i for i, (t, _) in enumerate(ev) if entry - MAX_H * HOUR <= t <= hi + MAX_H * HOUR]
            dels = [()] + [tuple(range(i, i + k)) for k in (1, 2, 3) for i in near if i + k <= len(ev)]
            for d in dels:
                kept = [e for i, e in enumerate(ev) if i not in d]
                ident = {'old_h': old, 'new_h': new, 'switch_h': sw_h, 'convention': conv, 'entry_h': entry_h,
                         'inclusive': incl, 'deleted_utc': [F.utc(ev[i][0]) for i in d]}
                yield ident, ev, kept, entry, lo, hi, incl


def simulate() -> dict:
    res = {r: {'cases': 0, 'false_accept': 0, 'false_reject': 0, 'first_mismatch': []} for r in RULES}
    tapes: dict = {}
    for ident, ev, kept, entry, lo, hi, incl in cases():
        x = _series(kept)
        fact = truth(ev, entry, hi, incl, kept)
        window = tuple((t, h) for t, h in kept if entry - MAX_H * HOUR - RV.FUNDING_TOL <= t <= hi + MAX_H * HOUR)
        tapes.setdefault((entry, hi, incl, window), []).append((fact, ident))
        for r in RULES:
            got, s = covered(r, x, entry, lo, hi, incl), res[r]
            s['cases'] += 1
            if got != fact:
                s['false_accept' if got else 'false_reject'] += 1
                if not s['first_mismatch']:
                    s['first_mismatch'] = [{**ident, 'truth': fact, 'rule': got}]
    same, cross = [], []
    for grp in tapes.values():                        # an observed tape with two truths is undecidable from the data
        one = [[g for g in grp if g[1]['convention'] == c] for c in CONVENTIONS]
        sub = next((g for g in one if len({f for f, _ in g}) == 2), None)
        if sub is None and len({f for f, _ in grp}) < 2:
            continue
        src = sub or grp
        pair = {'covered': next(i for f, i in src if f), 'not_covered': next(i for f, i in src if not f)}
        (same if sub else cross).append(pair)
    day = F.parse_utc('2022-01-03T00:00:00Z')
    entry, hi = day + 4 * HOUR, day + 12 * HOUR
    shape = tuple((t, h) for t, h in schedule(day, 2, 8, day + 8 * HOUR, 'old_label')
                  if entry - MAX_H * HOUR - RV.FUNDING_TOL <= t <= hi + MAX_H * HOUR)
    sol = [{**i, 'truth': f} for f, i in tapes.get((entry, hi, True, shape), [])]
    return {'rules': res, 'sol_shape': {c: sorted({x['truth'] for x in sol if x['convention'] == c})
                                        for c in CONVENTIONS}, 'sol_shape_cases': sol[:8], 'counterexamples': {
        'same_convention': {'count': len(same), 'examples': same[:5]},
        'cross_convention_only': {'count': len(cross), 'examples': cross[:5]}}}


def sol_replica(nearby: list[dict], entry_open: int, lo: int, hi: int, incl: bool) -> dict:
    """Both rules on the exact G3 SOL tape (nominal instants and typed intervals from the frozen G3 report)."""
    x = _series([(r['t_nom_ms'], r['interval_hours']) for r in nearby])
    return {r: covered(r, x, entry_open, lo, hi, incl) for r in RULES}


# ------------------------------------------------------------------ report
_S, _I, _B, _N = str, int, bool, type(None)
_IDENT = {'old_h': (_I,), 'new_h': (_I,), 'switch_h': (_I,), 'convention': (_S,), 'entry_h': (_I,),
          'inclusive': (_B,), 'deleted_utc': [(_S,)], 'truth': (_B,), 'rule': (_B,)}
_RULE = {'cases': (_I,), 'false_accept': (_I,), 'false_reject': (_I,), 'first_mismatch': [_IDENT]}
_CE = {'count': (_I,), 'examples': [{'covered': _IDENT, 'not_covered': _IDENT}]}
_TR = {'from_utc': (_S,), 'to_utc': (_S,), 'old_h': (_I,), 'new_h': (_I,), 'gap_h': (_I,), 'class': (_S,),
       'hidden_slots': (_I,)}
SCHEMA = {'format': (_S,), 'purpose': (_S,), 'g3_report_sha256': (_S,), 'manifest_digest': (_S, _N),
          'provenance_rows': [{'symbol': (_S,), 'nominal_utc': (_S,), 'path': (_S,), 'sha256': (_S,),
                               'source_url': (_S, _N), 'csv_line': (_I,), 'raw_calc_time': (_S,),
                               'raw_funding_interval_hours': (_S,), 'time_ms': (_I,), 'interval_hours': (_I,),
                               'interval_type': (_S,)}],
          'delivery_chain': [(_S,)],
          'transitions': {'window_lo': (_S,), 'symbols': {'*': {'rows': (_I,), 'last_row_utc': (_S, _N),
                                                                'transitions': [_TR]}},
                          'skipped_sealed_files': [(_S,)], 'files_read': [{'path': (_S,), 'sha256': (_S,)}],
                          'counts': {c: (_I,) for c in CLASSES}, 'ambiguous': (_I,), 'total': (_I,)},
          'simulation': {'rules': {r: _RULE for r in RULES}, 'sol_shape': {c: [(_B,)] for c in CONVENTIONS},
                         'sol_shape_cases': [_IDENT], 'counterexamples': {
              'same_convention': _CE, 'cross_convention_only': _CE}},
          'sol_replica': {r: (_B,) for r in RULES},
          'provenance': {'commit': (_S,), 'clean': (_B,)}}


def assert_blind(doc) -> None:
    """The G4 omission contract: SCHEMA exactly; the per-symbol map admits only symbol-like keys ('*')."""
    syms = ((doc.get('transitions') or {}).get('symbols') or {}) if type(doc) is dict else {}
    if any(type(k) is not str or not k.isalnum() or not k.isupper() for k in syms):
        raise G4Error('omission contract: transitions/symbols keys must be symbols')
    schema = dict(SCHEMA, transitions=dict(SCHEMA['transitions'], symbols={
        k: SCHEMA['transitions']['symbols']['*'] for k in syms}))
    G3.check_schema(doc, schema, G4Error)


def run(*, store: str, g3_report: str, repo: str = F.REPO) -> dict:
    import pit_run as PR                                                                    # noqa: E402
    src = PR.rex_sources(repo)
    man = src['manifest']
    with open(g3_report, 'rb') as fh:
        raw = fh.read()
    g3 = json.loads(raw)
    G3.assert_blind(g3)
    cell = g3['cells'][0]
    if cell['censor']['symbol'] != TARGET_SYMBOL or cell['predicate'] != 'uncovered_terminal':
        raise G4Error('the G3 report does not name the SOLUSDT uncovered_terminal this proof is about')
    doc = {'format': FORMAT, 'purpose': 'FUND-GAP-01 G4 funding-cadence transition proof (evidence only)',
           'g3_report_sha256': hashlib.sha256(raw).hexdigest(), 'manifest_digest': man.get('digest'),
           'provenance_rows': provenance(store, man), 'delivery_chain': list(DELIVERY),
           'transitions': transitions(store, man, list(src['static']['core_10']), F.parse_utc(SCAN_LO)),
           'simulation': simulate(),
           'sol_replica': sol_replica(cell['nearby_rows'], cell['entry_open_ms'], cell['bar_lo_ms'],
                                      cell['bar_hi_ms'], cell['inclusive'])}
    assert_blind(doc)
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    for k in ('--store', '--g3-report', '--out'):
        ap.add_argument(k, required=True)
    a = ap.parse_args(argv)
    if os.path.exists(a.out):
        print(f'error: {a.out} exists (a frozen report is never overwritten)', file=sys.stderr)
        return 2
    try:
        doc = run(store=a.store, g3_report=a.g3_report)
        doc['provenance'] = {'commit': G3._git('rev-parse', 'HEAD'), 'clean': G3._git('status', '--porcelain') == ''}
        assert_blind(doc)
    except Exception as e:                                       # every refusal: nothing written
        print(f'error: {type(e).__name__}: {e}', file=sys.stderr)
        return 2
    text = json.dumps(doc, sort_keys=True, indent=1) + '\n'
    with open(a.out, 'x', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    t = doc['transitions']
    ends = sorted({v['last_row_utc'] for v in t['symbols'].values() if v['last_row_utc']})
    print(f'{a.out} sha256 {hashlib.sha256(text.encode()).hexdigest()} transitions {t["total"]} {t["counts"]} '
          f'(from {t["window_lo"]} through {ends[-1] if ends else None}; sealed files skipped unread: '
          f'{len(t["skipped_sealed_files"])}) '
          f'sol_replica {doc["sol_replica"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
