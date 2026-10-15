"""REX add-on loader slice (Codex ruling 6185035993 point 3; Cowork D1 6185519839): the base archive and its attested
manifest stay IMMUTABLE; a separate `zb-rex-gap-fill/1` add-on (binance_archive_fill.py --fetch --zips) may fill ONLY
the exact holes listed in the signed `zb-rex-gap-inventory/1` report it pins. Never an overwrite, never an
interpolation, never a row outside a hole; provenance is retained on the run identity and the ledger.

Validation chain (`load_pinned` + `validate` + `check_base`, ALL before any Access is opened; any failure refuses with
AddonError, every parse / float failure included):
  1. EXTERNAL PIN: the sha256 of the add-on file's raw bytes == the digest pinned by the run command, checked before
     the bytes are parsed; then the add-on's own digest (sha256 of its canonical JSON without `digest`);
  2. its pinned base-manifest digest == the attested manifest the run uses;
  3. the gap report verifies by its self-digest, passes binance_archive_fill.check_report (END <= the sealed cutoff,
     development splits only), pins the same base manifest, and is the exact report the add-on pins;
  4. every source file record: https on exactly data.binance.vision under /data/futures/um/, unique; a file rows are
     taken from has status `verified` and the expected member;
  5. every row: sha256 == SHA-256 of its verbatim line; kline schema (12 columns, aligned open == open_ms, close_time ==
     open + interval - 1); VALUES: open / high / low / close finite floats > 0, high >= max(open, close, low),
     low <= min(open, close), volume and quote volume finite >= 0 (4h klines and 1h marks alike); its source URL names
     the same series / symbol / interval and its file record lists the row's hole;
  6. every row: open + interval <= the sealed END; inside the run's access window (the base loader's bound); a run
     symbol; a REX series (4h klines / 1h marks); inside an EXACT hole of the report for that (symbol, series) on the
     hole's grid; unique;
  7. ARCHIVE RE-PROOF: every cited zip is read from the kept-zips directory (binance_archive_fill.zip_path); its bytes'
     sha256 == the file record's sha256 == the kept `.CHECKSUM` record; the rows of the file's holes are re-extracted
     from the zip (binance_archive_fill.extract) and must reproduce the record's row count / rows_digest, and every
     add-on row must equal the re-extracted archive line BYTE FOR BYTE. A missing zip refuses;
  8. `check_base`: no add-on timestamp is present in the base store (time-column index of rex_gap_inventory, the
     price-blind reader) - a collision refuses before the Access.
Merge (`merge`, after the base tape is read through the Access window): the pre-parsed rows enter only where the base
tape has no row (re-checked; a collision refuses). Holes the add-on does not cover stay holes (the evaluator then halts
INCOMPLETE exactly as before)."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binance_archive_fill as BF                                                           # noqa: E402
import pit as P                                                                             # noqa: E402
import range_vwap_rev as RV                                                                 # noqa: E402
import rex_gap_inventory as GI                                                              # noqa: E402
import splits as S                                                                          # noqa: E402

FORMAT = BF.ADDON_FORMAT
SERIES = {'klines': ('4h', RV.TF), 'markPriceKlines': ('1h', RV.HOUR)}      # the REX series an add-on may fill
RULE = ('add-on rows fill only exact holes of the pinned signed gap report, only where no base row exists; each row '
        're-proved byte for byte from its checksum-verified archive zip; no overwrite, no interpolation; uncovered '
        'holes stay holes')


class AddonError(ValueError):
    pass


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _coords(url: str):
    """(series, symbol, interval) named by an archive URL .../{monthly|daily}/{series}/{SYMBOL}/{interval}/file.zip."""
    parts = url.split('/')
    return parts[-4], parts[-3], parts[-2]


def load_pinned(raw, pin) -> dict:
    """The add-on document from its RAW file bytes, only after sha256(raw) equals the externally pinned digest."""
    if not isinstance(raw, (bytes, bytearray)):
        raise AddonError('the add-on must be given as the raw file bytes (pinned before parsing)')
    if not isinstance(pin, str) or not BF.HEX64.match(pin):
        raise AddonError('an add-on needs its pinned sha256 (64 lowercase hex) from the run command')
    if _sha(bytes(raw)) != pin:
        raise AddonError('add-on file sha256 is not the pinned --addon-sha256 (edited or another file?)')
    try:
        doc = json.loads(bytes(raw).decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as e:
        raise AddonError(f'add-on is not JSON ({type(e).__name__})')
    if not isinstance(doc, dict):
        raise AddonError('add-on is not a JSON object')
    return doc


def parse_row(line: str, t: int, step: int):
    """The pit.Bar of one verbatim archive kline line, VALUE-checked (finite, OHLC > 0, consistent high / low,
    volumes >= 0). Every failure is an AddonError."""
    try:
        raw = line.encode('ascii')
    except (UnicodeEncodeError, AttributeError):
        raise AddonError('non-ascii row')
    cols = raw.split(b',')
    if (len(cols) != 12 or not cols[0].isdigit() or int(cols[0]) != t or t % step or not cols[6].isdigit()
            or int(cols[6]) != t + step - 1):
        raise AddonError('row fails the kline schema')
    try:
        o, h, lo, c, v, q = (float(cols[i].decode('ascii')) for i in (1, 2, 3, 4, 5, 7))
    except ValueError:
        raise AddonError('a price / volume field is not a number')
    if not all(math.isfinite(x) for x in (o, h, lo, c, v, q)):
        raise AddonError('a price / volume field is not finite')
    if min(o, h, lo, c) <= 0:
        raise AddonError('OHLC must be > 0')
    if h < max(o, c, lo) or lo > min(o, c):
        raise AddonError('inconsistent OHLC (high / low do not bound open / close)')
    if v < 0 or q < 0:
        raise AddonError('negative volume / quote volume')
    return P.Bar(t, o, h, lo, c, v, q, t + step)


def _reprove(files: dict, used: dict, claims: dict, report: dict, zips: str) -> None:
    """Item 7: every cited zip re-read from `zips`, checksum-matched, its hole rows re-extracted and compared."""
    if not isinstance(zips, str) or not os.path.isdir(zips):
        raise AddonError('the kept archive zips directory (--addon-zips) is missing')
    hole_by_id = {BF.hole_id(h): h for h in report['holes']}
    for url in sorted(used):
        fr = files[url]
        try:
            p = BF.zip_path(zips, url)
            with open(p, 'rb') as f:
                raw = f.read()
            with open(p + '.CHECKSUM', 'rb') as f:
                published = BF.parse_checksum(f.read(), url.rsplit('/', 1)[1])
        except OSError:
            raise AddonError(f'{url}: archive zip / .CHECKSUM missing from the kept zips (cannot re-prove)')
        except BF.FillError as e:
            raise AddonError(f'{url}: {e}')
        if not (_sha(raw) == fr['sha256'] == published):
            raise AddonError(f'{url}: zip sha256 / file record / .CHECKSUM record disagree')
        series, sym, iv = _coords(url)
        req = {'url': url, 'interval': iv, 'member': fr['member']}
        try:
            wanted = set().union(*(BF.hole_opens(hole_by_id[k]) for k in fr['holes']))
            got = BF.extract(raw, req, wanted)
        except (KeyError, TypeError, BF.FillError, zipfile.BadZipFile, ValueError) as e:
            raise AddonError(f'{url}: cannot re-extract the hole rows ({type(e).__name__}: {e})')
        if len(got) != fr.get('rows') or _sha(b'\n'.join(ln for _, ln in got)) != fr.get('rows_digest'):
            raise AddonError(f'{url}: re-extracted rows differ from the file record (rows / rows_digest)')
        arch = {t: ln for t, ln in got}
        for t, line in claims[url]:
            if arch.get(t) != line.encode('ascii'):
                raise AddonError(f'{url}: add-on row {S.utc(t)} is not the archive line byte for byte')


def validate(addon, report, *, manifest_digest: str, symbols, lo: int, hi: int, cutoff: int, zips: str,
             addon_sha256: str) -> dict:
    """The fully validated add-on: {'rows': {(symbol, series): {open_ms: pit.Bar}}, 'provenance': {...}}. `addon` is
    the document returned by load_pinned (its pin is `addon_sha256`). Refuses (AddonError) on the first failure of
    items 1-7 of the module docstring."""
    try:
        return _validate(addon, report, manifest_digest, symbols, lo, hi, cutoff, zips, addon_sha256)
    except AddonError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as e:      # nothing else escapes
        raise AddonError(f'malformed add-on / report ({type(e).__name__}: {e})')


def _validate(addon, report, manifest_digest, symbols, lo, hi, cutoff, zips, addon_sha256) -> dict:
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
    claims: dict = {}
    for r in addon.get('rows') or ():
        sym, ser, iv, t, line, src = (r.get(k) for k in ('symbol', 'series', 'interval', 'open_ms', 'line',
                                                          'source_url'))
        tag = f'{sym}|{ser}|{t}'
        if not isinstance(line, str) or not line.isascii() or not isinstance(t, int) or isinstance(t, bool):
            raise AddonError(f'{tag}: malformed add-on row')
        if r.get('sha256') != _sha(line.encode('ascii')):
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
        try:
            bar = parse_row(line, t, step)
        except AddonError as e:
            raise AddonError(f'{tag}: {e}')
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
        bucket[t] = bar
        claims.setdefault(src, []).append((t, line))
    used = {u: len(v) for u, v in claims.items()}
    _reprove(files, used, claims, report, zips)
    prov = {'format': FORMAT, 'addon_sha256': addon_sha256, 'digest': addon['digest'], 'gap_report': report[GI.SIG],
            'base_manifest': manifest_digest, 'rule': RULE,
            'filled': {f'{s}|{ser}': len(v) for (s, ser), v in sorted(rows.items())},
            'source_files': [{'url': u, 'sha256': files[u]['sha256'], 'rows': used[u]} for u in sorted(used)]}
    return {'rows': rows, 'provenance': prov}


def check_base(validated: dict, base_index: dict) -> None:
    """Item 8, before the Access: `base_index` is rex_gap_inventory.index over the base store (time column only);
    any add-on timestamp the base holds is a collision and refuses (never overwritten)."""
    for (sym, ser), rows in sorted(validated['rows'].items()):
        have = set((base_index.get((ser, sym, SERIES[ser][0])) or {}).get('times', ()))
        clash = sorted(set(rows) & have)
        if clash:
            raise AddonError(f'{sym}|{ser}: add-on row(s) collide with base rows at {[S.utc(t) for t in clash[:3]]} '
                             f'(never overwritten)')


def merge(tape: dict, validated: dict) -> dict:
    """A NEW tape: the base series plus the validated (pre-parsed) add-on rows, each only where the base has no row
    (re-checked; a collision refuses). The base Series objects are never mutated; nothing is parsed here."""
    try:
        out = dict(tape)
        for (sym, ser), rows in sorted(validated['rows'].items()):
            x = out[sym]
            if ser == 'klines':
                clash = sorted(set(rows) & set(x.bars))
                bars = dict(x.bars)
                bars.update(rows)
                marks = x.marks
            else:
                clash = sorted(set(rows) & set(x.marks))
                marks = dict(x.marks)
                marks.update({t: b.close for t, b in rows.items()})
                bars = x.bars
            if clash:
                raise AddonError(f'{sym}|{ser}: add-on row(s) collide with base rows at '
                                 f'{[S.utc(t) for t in clash[:3]]} (never overwritten)')
            out[sym] = RV.Series(dict(sorted(bars.items())), x.rule, x.funding, dict(sorted(marks.items())))
        return out
    except AddonError:
        raise
    except Exception as e:                                                          # nothing else escapes
        raise AddonError(f'merge failed ({type(e).__name__}: {e})')
