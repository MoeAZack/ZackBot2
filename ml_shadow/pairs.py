"""ML-PAIR-01 (contract contracts/ml_train_01, sections 1.1-1.8): the durable labelable-decision store and the append-only
paired training ledger. Observation only: nothing here places, sizes, gates or cancels an order, and nothing in the
trading code reads what it writes.

Files, all under <data>/ml_shadow/pairs/ and NEVER rotated:
- labelable_decisions.jsonl  every taken / pending decision (ledger.labelable) as written at decision time, plus the
                             lot's effective management snapshot taken when the lot opens (section 1.7). Skipped
                             decisions stay only in the rotating decision ledger. The pairing step reads the decision
                             from here, so decisions.jsonl rotation can no longer orphan a close.
- pairs.jsonl                one `ml_shadow.pair.v1` row per (decision_id, cost_rev_id), appended on the writer thread
                             ONLY after a measured, cost-complete close (actual_costs.row_view status 'measured' and
                             actual_costs.measured complete). Acknowledged and booked closes are counted in
                             non_training_closes and never paired.
- pair_epoch.json            written once by exclusive create when an installed build (BUILD_ID != 'dev') handles its
                             first labelable decision; never rewritten (section 1.8).

Ledger rules (the ai_scan_ledger.ScanLedger idiom): one sealed line per record (write + flush + fsync under a lock); a
torn last line is closed off before the next append; record_hash covers the content only (never paired_at), so a replay
after a restart is a no-op; a different record_hash under an existing pair_key is refused, counted as duplicate_pairs
and nothing is written; reads verify record_hash and skip / count a corrupt or edited line, never raise.

Every PairStore method is fail-safe for trading (it never raises; errors are counted) and fail-closed for the data
(anything not provably measured, cost-complete, joined and inside the epoch is not written).
"""
import json
import logging
import math
import os
import sys
import re
import threading
from datetime import datetime, timezone

import actual_costs as AC

from .common import SCHEMA_VERSION, canonical_json, sha256_hex, to_utc
from .features import FEATURE_NAMES
from .ledger import labelable
from .sanitize import sanitize_row

log = logging.getLogger('zackbot.ml_shadow')

PAIR_SCHEMA = 'ml_shadow.pair.v1'
MGMT_SCHEMA = 'ml_shadow.mgmt_snapshot.v1'
EPOCH_SCHEMA = 'ml_shadow.pair_epoch.v1'
PAIRS_DIR, PAIRS_FILE, DURABLE_FILE, EPOCH_FILE = 'pairs', 'pairs.jsonl', 'labelable_decisions.jsonl', 'pair_epoch.json'
SOURCE_LIVE = 'live'
NET_BASIS = 'measured'
# cost_rev_id: the AI-SCAN-01 (#201) actual-cost revision identifier, byte-for-byte the definition in
# ai_scan.cost_revision_id (Codex 6453104005; tests pin the two equal). It is re-stated here, not imported, so that the
# section 1.6 module-bundle hash (engine.py, actual_costs.py, ml_shadow/*.py) covers the code that produces it.
COST_REV_SCHEMA = 'ai-scan-cost-rev-v2'
COST_REV_FIELDS = ('fees_actual', 'funding_actual', 'pnl_gross', 'pnl_fill_adj', 'risk_usd')
COST_MODEL_ID = AC.MEASURED_COST_MODEL_ID             # defined once in actual_costs (shared with ai_scan)
FEATURE_SCHEMA = SCHEMA_VERSION                       # decision features as persisted by the ml_shadow.v2 decision row
FEATURE_NAMES_SHA256 = sha256_hex(canonical_json(list(FEATURE_NAMES)))
TF_HOURS = {'5m': 5 / 60, '15m': 0.25, '1h': 1.0, '4h': 4.0}
# Section 1.6: the modules that produce a pair, exactly as installer.ps1 packages them (tests pin the lists equal).
BUNDLE_MODULES = ('engine.py', 'actual_costs.py', 'ml_shadow/__init__.py', 'ml_shadow/common.py', 'ml_shadow/context.py',
                  'ml_shadow/features.py', 'ml_shadow/labels.py', 'ml_shadow/ledger.py', 'ml_shadow/pairs.py',
                  'ml_shadow/sanitize.py', 'ml_shadow/scoring.py', 'ml_shadow/trainer.py', 'ml_shadow/writer.py')
BUILD_RE, COMMIT_RE, HASH_RE = (re.compile(r'^[A-Za-z0-9._-]{1,40}$'), re.compile(r'^[0-9a-f]{40}$'),
                                re.compile(r'^[0-9a-f]{64}$'))
PAIR_COUNTERS = ('pairs', 'pair_replays', 'duplicate_pairs', 'no_durable_decision', 'refused_skipped', 'cost_incomplete',
                 'pre_epoch', 'no_epoch', 'epoch_invalid', 'epoch_refused_identity', 'identity_incomplete',
                 'durable_decisions', 'mgmt_snapshots', 'skipped_lines', 'errors')


class PairError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _finite(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)): return None
    v = float(v)
    return v if math.isfinite(v) else None


def chash(obj):
    """sha256 of canonical JSON (management snapshots, overlay entries, epoch bodies)."""
    return sha256_hex(canonical_json(obj))


def paths(data_dir):
    d = os.path.join(str(data_dir), 'ml_shadow', PAIRS_DIR)
    return dict(dir=d, pairs=os.path.join(d, PAIRS_FILE), durable=os.path.join(d, DURABLE_FILE),
                epoch=os.path.join(d, EPOCH_FILE))


# ------------------------------------------------------------------ cost identity
def cost_inputs(row, view, meas):
    """The engine side (engine thread, at the measured close): a plain, detached copy of what pairing needs from one closed
    history row. view = actual_costs.row_view(row), meas = actual_costs.measured(row). No exchange ids are copied."""
    adj = row.get('pnl_fill_adj', 0.0) if 'pnl_fill_adj' in row else 0.0
    return dict(status=view.get('status'), complete=bool(meas.get('complete')), why=list(meas.get('why') or ()),
                pnl_gross=_finite(row.get('pnl_gross')), pnl_fill_adj=_finite(adj), fees_actual=_finite(meas.get('fees')),
                funding_actual=_finite(meas.get('funding')), risk_usd=_finite(row.get('risk_usd')),
                gross=_finite(meas.get('gross')))


def cost_revision_id(c, net_basis=NET_BASIS, cost_model_id=COST_MODEL_ID):
    """Same identity as ai_scan.cost_revision_id: sha256 over [COST_REV_SCHEMA, [[field, value]...], basis, model]."""
    vals = []
    for f in COST_REV_FIELDS:
        v = _finite(c.get(f))
        if v is None: raise PairError(f'cost field {f} missing')
        vals.append([f, v])
    if not c['risk_usd'] > 0: raise PairError('risk_usd must be > 0')
    if net_basis != NET_BASIS or not (isinstance(cost_model_id, str) and cost_model_id): raise PairError('cost identity')
    return sha256_hex(canonical_json([COST_REV_SCHEMA, vals, net_basis, cost_model_id]))


# ------------------------------------------------------------------ code identity (section 1.6)
def bundle_base():
    return getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundle_hash(base=None):
    """sha256 over the sorted (path, sha256) list of BUNDLE_MODULES as packaged. None when ANY of them cannot be read
    from the bundle: the code identity is then incomplete and nothing is paired."""
    base = base or bundle_base()
    out = []
    for m in sorted(BUNDLE_MODULES):
        try:
            with open(os.path.join(base, *m.split('/')), 'rb') as fh: out.append([m, sha256_hex(fh.read())])
        except OSError:
            return None
    return chash(out)


def code_identity(data_dir, base=None):
    """-> {build_id, source_commit, bundle_hash}. build_id = build_info.BUILD_ID ('dev' when not packaged);
    source_commit = build.json source_commit (the installer record in the data folder), taken ONLY when that record's
    build_id is this running build's; bundle_hash as above. Never raises."""
    try:
        from build_info import BUILD_ID as bid               # written into the staged source by installer.ps1
    except Exception:                                       # noqa: BLE001
        bid = 'dev'
    sc = None
    try:
        with open(os.path.join(str(data_dir), 'build.json'), encoding='utf-8-sig') as fh: rec = json.load(fh)
        if isinstance(rec, dict) and rec.get('build_id') == bid and bid != 'dev': sc = rec.get('source_commit')
    except Exception:                                       # noqa: BLE001 - no record: no source commit
        sc = None
    if not (isinstance(sc, str) and COMMIT_RE.fullmatch(sc)): sc = None
    try: bh = bundle_hash(base)
    except Exception: bh = None                             # noqa: BLE001
    return dict(build_id=str(bid), source_commit=sc, bundle_hash=bh)


def identity_gap(code):
    """The complete-code-identity gate (Codex 6459654266 #2): None when the identity is complete - a non-dev build id, a
    40-hex source commit from the matching build.json and a 64-hex bundle hash - else the first missing part."""
    c = code if isinstance(code, dict) else {}
    b = c.get('build_id')
    if not (isinstance(b, str) and BUILD_RE.fullmatch(b)) or b == 'dev': return 'dev_build'
    if not (isinstance(c.get('source_commit'), str) and COMMIT_RE.fullmatch(c['source_commit'])): return 'source_commit'
    if not (isinstance(c.get('bundle_hash'), str) and HASH_RE.fullmatch(c['bundle_hash'])): return 'bundle_hash'
    return None


def packaged(code):
    return identity_gap(code) is None


# ------------------------------------------------------------------ management snapshot (section 1.7)
def mgmt_snapshot(decision_id, effective, canonical, overlay_id=None, overlay=None, tf=None):
    """The lot's effective management as the engine will apply it, taken when the lot opens. Immutable once written."""
    g = effective if isinstance(effective, dict) else {}
    mb = g.get('max_bars')
    mb = _finite(mb) if mb else None
    hrs = TF_HOURS.get(str(tf))
    return {'kind': 'mgmt', 'schema': MGMT_SCHEMA, 'decision_id': decision_id, 'effective_max_bars': mb,
            'runner': 'runner' in g, 'overlay_id': overlay_id if overlay else None,
            'overlay_hash': chash(overlay) if overlay else None, 'mgmt_hash': chash(g),
            'canonical_mgmt_hash': chash(canonical if isinstance(canonical, dict) else {}), 'tf': tf,
            'horizon_h': (mb * hrs) if (mb and hrs) else None}


# ------------------------------------------------------------------ sealed append-only JSONL
def seal(row):
    body = {k: v for k, v in row.items() if k not in ('record_hash', 'paired_at')}
    return chash(body)


class SealedJsonl:
    """Append-only, never-rotated JSONL of sealed records (record_hash over everything but record_hash / paired_at).
    key(rec) -> the immutable key. A key already on disk: same hash -> no-op ('replay'), other hash -> 'conflict'
    (nothing written). Corrupt / torn / edited lines are skipped and counted, never raised."""

    def __init__(self, path, key):
        self.path, self.key = str(path), key
        self._lock = threading.Lock()
        self.skipped = 0
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._rows = {}
        for r in self.records(): self._rows.setdefault(self.key(r), r)

    def records(self):
        out, bad = [], 0
        if os.path.exists(self.path):
            with open(self.path, 'rb') as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line: continue
                    try:
                        rec = json.loads(line.decode('utf-8'))
                        if not isinstance(rec, dict) or rec.get('record_hash') != seal(rec): raise ValueError('hash')
                        self.key(rec)
                    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                        bad += 1
                        continue
                    out.append(rec)
        if bad: log.warning('ml_shadow: %d invalid line(s) skipped in %s', bad, os.path.basename(self.path))
        self.skipped = bad
        return out

    def get(self, key):
        return self._rows.get(key)

    def append(self, rec):
        """-> 'written' | 'replay' | 'conflict'. Raises only on an I/O failure (the caller counts it)."""
        rec = dict(rec, record_hash=seal(rec))
        k = self.key(rec)
        line = (canonical_json(rec) + '\n').encode('utf-8')
        with self._lock:
            have = self._rows.get(k)
            if have is not None: return 'replay' if have['record_hash'] == rec['record_hash'] else 'conflict'
            with open(self.path, 'ab') as fh:
                if fh.tell():                            # close off a torn last line (crash mid-append) first
                    with open(self.path, 'rb') as fr:
                        fr.seek(-1, os.SEEK_END)
                        if fr.read(1) != b'\n': line = b'\n' + line
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            self._rows[k] = rec
        return 'written'


def _durable_key(r):
    if r.get('kind') not in ('decision', 'mgmt') or not isinstance(r.get('decision_id'), str): raise KeyError('kind')
    return (r['kind'], r['decision_id'])


def _pair_key(r):
    pk = r.get('pair_key')
    if r.get('kind') != 'pair' or not (isinstance(pk, list) and len(pk) == 2 and all(isinstance(x, str) for x in pk)):
        raise KeyError('pair_key')
    return tuple(pk)


# ------------------------------------------------------------------ epoch (section 1.8)
def read_epoch(path):
    """-> the verified epoch body, None when absent. Raises PairError when present but unverifiable."""
    if not os.path.exists(path): return None
    try:
        with open(path, encoding='utf-8') as fh: e = json.load(fh)
    except Exception as ex:                                 # noqa: BLE001
        raise PairError(f'epoch unreadable ({type(ex).__name__})')
    if not isinstance(e, dict) or e.get('epoch_hash') != chash({k: v for k, v in e.items() if k != 'epoch_hash'}):
        raise PairError('epoch_hash does not verify')
    if to_utc(e['first_decision_candle']) > to_utc(e['created_at']): raise PairError('epoch backdated')
    if identity_gap(e) is not None: raise PairError('epoch code identity incomplete')
    return e


def create_epoch(path, code, first_candle, now=None):
    """Exclusive create, once. -> the epoch (the existing one if another writer won). Raises PairError when refused."""
    created = now or _now()
    gap = identity_gap(code)
    if gap is not None: raise PairError(f'code identity incomplete ({gap})')
    if to_utc(first_candle) > to_utc(created): raise PairError('first decision candle after created_at')
    body = dict(schema=EPOCH_SCHEMA, build_id=code.get('build_id'), source_commit=code.get('source_commit'),
                bundle_hash=code.get('bundle_hash'), first_decision_candle=first_candle, created_at=created)
    body['epoch_hash'] = chash(body)
    data = (canonical_json(body) + '\n').encode('utf-8')
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0))
    except FileExistsError:
        return read_epoch(path)
    try:
        os.write(fd, data); os.fsync(fd)
    finally:
        os.close(fd)
    return body


# ------------------------------------------------------------------ the store
class PairStore:
    """Opened on the ML writer thread. counters: the engine health block; pairing counters live in counters['pairs']."""

    def __init__(self, data_dir, counters=None, code=None, now=None):
        self.p = paths(data_dir)
        self.ctr = (counters if counters is not None else {}).setdefault('pairs', {})
        for k in PAIR_COUNTERS: self.ctr.setdefault(k, 0)
        self.ctr.setdefault('non_training_closes', {'acknowledged': 0, 'booked': 0})
        self.ctr.setdefault('identity_reasons', {})
        self.code = code if code is not None else code_identity(data_dir)
        self.now = now or _now
        self.durable = SealedJsonl(self.p['durable'], _durable_key)
        self.pairs = SealedJsonl(self.p['pairs'], _pair_key)
        self.ctr['skipped_lines'] = self.durable.skipped + self.pairs.skipped

    def _gap(self, counter):
        """Count an identity refusal under `counter` with its reason. -> the reason, None when the identity is complete."""
        gap = identity_gap(self.code)
        if gap is not None:
            self.ctr[counter] += 1
            self.ctr['identity_reasons'][gap] = self.ctr['identity_reasons'].get(gap, 0) + 1
            self.ctr['identity_last'] = gap
        return gap

    def _err(self, where, ex):
        self.ctr['errors'] += 1
        log.warning('ml_shadow pairs: %s failed (%s)', where, type(ex).__name__)   # type only: no data in logs

    # ---- decision time
    def record_decision(self, row):
        """A taken / pending decision -> the durable store (sanitized exactly like the rotating ledger row); the first
        labelable decision of an installed build creates the epoch. Skipped decisions are never stored. Never raises."""
        try:
            if not labelable(row): return None
            rec = sanitize_row('decision', row)
            if self.durable.append(rec) == 'written': self.ctr['durable_decisions'] += 1
            if not os.path.exists(self.p['epoch']) and self._gap('epoch_refused_identity') is None:
                create_epoch(self.p['epoch'], self.code, rec['candle_time'], self.now())   # complete identity only
            return rec
        except Exception as ex:                             # noqa: BLE001
            self._err('durable decision', ex); return None

    def record_mgmt(self, snap):
        """The lot's effective management at open (section 1.7). First write wins; never edited. Never raises."""
        try:
            rec = sanitize_row('mgmt', snap)
            if self.durable.append(rec) == 'written': self.ctr['mgmt_snapshots'] += 1
            return rec
        except Exception as ex:                             # noqa: BLE001
            self._err('mgmt snapshot', ex); return None

    def decision(self, did):
        r = self.durable.get(('decision', did))
        return r and {k: v for k, v in r.items() if k != 'record_hash'}

    # ---- close time (writer thread, label branch)
    def pair(self, label, close):
        """label: the sanitized label row; close: {'cost': cost_inputs(...), 'mgmt_close_hash': ...} built at the
        measured close. -> 'written' | 'replay' | None. Never raises."""
        try:
            return self._pair(label, close)
        except Exception as ex:                             # noqa: BLE001
            self._err('pair', ex); return None

    def _pair(self, label, close):
        c = (close or {}).get('cost') or {}
        st = c.get('status')
        if st in ('acknowledged', 'booked'):               # operator-typed / legacy nets: never training pairs
            self.ctr['non_training_closes'][st] += 1; return None
        if st != 'measured' or c.get('complete') is not True:
            self.ctr['cost_incomplete'] += 1; return None
        if self._gap('identity_incomplete') is not None: return None   # never a pair without a complete code_id
        did = label.get('decision_id')
        dec = self.decision(did)
        if dec is None:
            self.ctr['no_durable_decision'] += 1; return None
        if not labelable(dec):
            self.ctr['refused_skipped'] += 1; return None
        try:
            ep = read_epoch(self.p['epoch'])
        except PairError:
            self.ctr['epoch_invalid'] += 1; return None
        if ep is None:
            self.ctr['no_epoch'] += 1; return None
        if to_utc(dec['candle_time']) < to_utc(ep['first_decision_candle']):
            self.ctr['pre_epoch'] += 1; return None
        try: crid = cost_revision_id(c)
        except PairError:
            self.ctr['cost_incomplete'] += 1; return None
        snap = self.durable.get(('mgmt', did)) or {}
        close_h = (close or {}).get('mgmt_close_hash')
        open_h = snap.get('mgmt_hash')
        mgmt = {k: snap.get(k) for k in ('effective_max_bars', 'runner', 'overlay_id', 'overlay_hash', 'mgmt_hash',
                                          'canonical_mgmt_hash', 'tf', 'horizon_h')}
        mgmt.update(mgmt_close_hash=close_h, mgmt_changed=(close_h != open_h) if (close_h and open_h) else None)
        row = {
            'kind': 'pair', 'schema': PAIR_SCHEMA, 'pair_key': [did, crid], 'source': SOURCE_LIVE,
            'decision_id': did, 'corr_id': label.get('corr_id'), 'decision': dec,
            'label': dict({k: label.get(k) for k in ('closed_at', 'net_r', 'net_pnl', 'exit_reason', 'mfe_r', 'mae_r',
                                                     'target_before_stop')}, net_basis=NET_BASIS),
            'costs': dict(gross=c.get('gross'), fees_actual=c.get('fees_actual'), funding_actual=c.get('funding_actual'),
                          fill_adj=c.get('pnl_fill_adj'), risk_usd=c.get('risk_usd'), complete=True, why=c.get('why') or []),
            'ids': dict(code_id=dict(self.code), decision_schema=dec.get('schema'), label_schema=label.get('schema'),
                        feature_schema=FEATURE_SCHEMA, feature_names_sha256=FEATURE_NAMES_SHA256,
                        cost_model_id=COST_MODEL_ID, cost_rev_id=crid),
            'mgmt': mgmt, 'epoch_hash': ep['epoch_hash'], 'provenance': None,
        }
        rec = sanitize_row('pair', row)
        rec['paired_at'] = self.now()
        res = self.pairs.append(rec)
        if res == 'conflict':
            self.ctr['duplicate_pairs'] += 1; return None
        self.ctr['pairs' if res == 'written' else 'pair_replays'] += 1
        return res


def read_pairs(data_dir):
    """Read-only: (verified pair rows in file order, skipped line count). Never creates a file."""
    p = paths(data_dir)['pairs']
    if not os.path.exists(p): return [], 0
    led = SealedJsonl.__new__(SealedJsonl)
    led.path, led.key = p, _pair_key
    return led.records(), led.skipped
