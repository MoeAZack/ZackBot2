"""REX add-on loader slice (Codex ruling 6185035993 point 3): the base archive and its attested manifest stay IMMUTABLE;
a separate, checksum-pinned `zb-rex-gap-fill/1` add-on (binance_archive_fill.py --fetch) may fill ONLY the exact holes
listed in the signed `zb-rex-gap-inventory/1` report (rex_gap_inventory.py) it pins. Never an overwrite, never an
interpolation, never a row outside a hole; provenance is retained on the run identity and the ledger.

Validation chain (`validate`, BEFORE any Access is opened; any failure refuses the run):
  1. the add-on's own digest (sha256 of its canonical JSON without `digest`);
  2. its pinned base-manifest digest == the attested manifest the run uses;
  3. the gap report verifies by its self-digest, passes binance_archive_fill.check_report (END <= the sealed cutoff,
     development splits only), pins the same base manifest, and is the exact report the add-on pins;
  4. every source file record: https on exactly data.binance.vision under /data/futures/um/, unique, and for a file
     rows are taken from: status `verified` with its 64-hex published `.CHECKSUM` sha256 and the expected member;
  5. every row: its sha256 == SHA-256 of its verbatim line; the line passes the kline schema (12 columns, aligned
     open == open_ms, close_time == open + interval - 1); its source URL names the same series / symbol / interval and
     its file record lists the row's hole;
  6. every row: open < the sealed END; inside the run's access window (the same bound the base loader applies); a
     run symbol; a REX series (4h klines / 1h marks); inside an EXACT hole of the report for that (symbol, series) on
     the hole's grid; unique.
Merge rule (`merge`, after the base tape is read through the Access window): an add-on row enters only where the base
tape has NO row at that timestamp; any collision refuses. Holes the add-on does not cover stay holes (the evaluator
then halts INCOMPLETE exactly as before)."""
from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binance_archive_fill as BF                                                           # noqa: E402
import pit as P                                                                             # noqa: E402
import range_vwap_rev as RV                                                                 # noqa: E402
import rex_gap_inventory as GI                                                              # noqa: E402
import splits as S                                                                          # noqa: E402

FORMAT = BF.ADDON_FORMAT
SERIES = {'klines': ('4h', RV.TF), 'markPriceKlines': ('1h', RV.HOUR)}      # the REX series an add-on may fill
RULE = ('add-on rows fill only exact holes of the pinned signed gap report, only where no base row exists; '
        'no overwrite, no interpolation; uncovered holes stay holes')


class AddonError(ValueError):
    pass


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _coords(url: str):
    """(series, symbol, interval) named by an archive URL .../{monthly|daily}/{series}/{SYMBOL}/{interval}/file.zip."""
    parts = url.split('/')
    return parts[-4], parts[-3], parts[-2]


def validate(addon, report, *, manifest_digest: str, symbols, lo: int, hi: int, cutoff: int) -> dict:
    """The fully validated add-on: {'rows': {(symbol, series): {open_ms: line}}, 'provenance': {...}}. Refuses
    (AddonError) on the first failure of the chain in the module docstring."""
    if not isinstance(addon, dict) or addon.get('format') != FORMAT:
        raise AddonError(f'the add-on is not a {FORMAT} document')
    if addon.get('digest') != BF.digest(addon):
        raise AddonError('add-on digest does not match its canonical content (edited?)')
    if addon.get('manifest_digest') != manifest_digest:
        raise AddonError('the add-on pins another base manifest than the attested one in use')
    try:
        GI.verify(report)
        BF.check_report(report)
    except (GI.InventoryError, BF.FillError) as e:
        raise AddonError(f'gap report refused: {e}')
    if report.get('manifest_digest') != manifest_digest:
        raise AddonError('the gap report pins another base manifest than the attested one in use')
    if addon.get('inventory_digest') != report[GI.SIG]:
        raise AddonError('the add-on pins another gap report than the one supplied')
    files = {}
    for f in addon.get('files') or ():
        u = f.get('url')
        if not BF.archive_url_ok(u):
            raise AddonError(f'source file {u!r} is not on https://{BF.HOST}{BF.PATH_PREFIX}')
        if u in files:
            raise AddonError(f'source file {u} listed twice')
        files[u] = f
    holes: dict = {}
    for h in report['holes']:
        holes.setdefault((h['symbol'], h['series']), []).append(h)
    hole_ids = {BF.hole_id(h): h for h in report['holes']}
    want = set(symbols)
    rows: dict = {}
    used: dict = {}
    for r in addon.get('rows') or ():
        sym, ser, iv, t, line, src = (r.get(k) for k in ('symbol', 'series', 'interval', 'open_ms', 'line',
                                                          'source_url'))
        tag = f'{sym}|{ser}|{t}'
        if not isinstance(line, str) or not isinstance(t, int) or isinstance(t, bool):
            raise AddonError(f'{tag}: malformed add-on row')
        raw = line.encode('ascii', 'strict') if line.isascii() else None
        if raw is None or r.get('sha256') != _sha(raw):
            raise AddonError(f'{tag}: row sha256 does not match its verbatim line')
        if not BF.archive_url_ok(src):
            raise AddonError(f'{tag}: source {src!r} is not on https://{BF.HOST}{BF.PATH_PREFIX}')
        fr = files.get(src)
        if (fr is None or fr.get('status') != 'verified' or not BF.HEX64.match(str(fr.get('sha256')))
                or fr.get('member') != src.rsplit('/', 1)[1][:-4] + '.csv'):
            raise AddonError(f'{tag}: source {src} has no verified archive checksum record')
        if _coords(src) != (ser, sym, iv) or r.get('hole') not in (fr.get('holes') or ()):
            raise AddonError(f'{tag}: row does not belong to its source file {src}')
        if ser not in SERIES or SERIES[ser][0] != iv:
            raise AddonError(f'{tag}: not a REX series (4h klines / 1h marks)')
        step = SERIES[ser][1]
        cols = raw.split(b',')
        if (len(cols) != 12 or not cols[0].isdigit() or int(cols[0]) != t or t % step or not cols[6].isdigit()
                or int(cols[6]) != t + step - 1):
            raise AddonError(f'{tag}: row fails the kline schema')
        if t + step > cutoff:
            raise AddonError(f'{tag}: row at / after the sealed cutoff {S.utc(cutoff)} (holdout refused)')
        if t < lo or t + step > hi:
            raise AddonError(f'{tag}: row outside the run access window [{S.utc(lo)}, {S.utc(hi)}]')
        if sym not in want:
            raise AddonError(f'{tag}: not a symbol of this run')
        inside = [h for h in holes.get((sym, ser), ()) if h['start_ms'] <= t < h['end_ms']
                  and (t - h['start_ms']) % step == 0]
        if not inside or hole_ids.get(r.get('hole')) not in inside:
            raise AddonError(f'{tag}: row outside every exact hole of the signed gap report')
        bucket = rows.setdefault((sym, ser), {})
        if t in bucket:
            raise AddonError(f'{tag}: duplicate add-on row')
        bucket[t] = line
        used[src] = used.get(src, 0) + 1
    for src, n in used.items():
        if not isinstance(files[src].get('rows'), int) or n > files[src]['rows']:
            raise AddonError(f'{src}: more add-on rows than its verified file record holds')
    prov = {'format': FORMAT, 'digest': addon['digest'], 'gap_report': report[GI.SIG],
            'base_manifest': manifest_digest, 'rule': RULE,
            'filled': {f'{s}|{ser}': len(v) for (s, ser), v in sorted(rows.items())},
            'source_files': [{'url': u, 'sha256': files[u]['sha256'], 'rows': used[u]} for u in sorted(used)]}
    return {'rows': rows, 'provenance': prov}


def _bar(line: str, step: int):
    c = line.split(',')
    t = int(c[0])
    return P.Bar(t, float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5]), float(c[7]), t + step)


def merge(tape: dict, validated: dict) -> dict:
    """A NEW tape: the base series plus the validated add-on rows, each only where the base has no row (a collision
    refuses). The base Series objects are never mutated."""
    out = dict(tape)
    for (sym, ser), rows in sorted(validated['rows'].items()):
        x = out[sym]
        if ser == 'klines':
            clash = sorted(set(rows) & set(x.bars))
            bars = dict(x.bars)
            bars.update({t: _bar(ln, RV.TF) for t, ln in rows.items()})
            marks = x.marks
        else:
            clash = sorted(set(rows) & set(x.marks))
            marks = dict(x.marks)
            marks.update({t: _bar(ln, RV.HOUR).close for t, ln in rows.items()})
            bars = x.bars
        if clash:
            raise AddonError(f'{sym}|{ser}: add-on row(s) collide with base rows at {[S.utc(t) for t in clash[:3]]} '
                             f'(never overwritten)')
        out[sym] = RV.Series(dict(sorted(bars.items())), x.rule, x.funding, dict(sorted(marks.items())))
    return out
