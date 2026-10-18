"""#148 Phase C - first evidence read of the PAPER strategy set (read-only, deterministic; Codex ruling 6251169379).

Two commands:
  freeze  (Integration only) copies the whitelisted PAPER artifacts from a data dir into a NEW snapshot dir and writes a
          sha256 manifest. Nothing else is copied (no config, keys or settings); the source is only read.
  report  reads ONLY a manifest-pinned frozen snapshot (+ optionally the frozen Wave-2 packet) and writes
          zb-phase-c-report/1 (report.json + report.md), write-once. A pin mismatch, a missing pinned file or an unreadable
          input -> an INCOMPLETE-only record and a non-zero exit. It never reads a live PAPER dir, a research dataset or
          the sealed holdout, and it changes no runtime, config or routing.

Sections: 1 executed PAPER results (family / strategy / strategy x symbol), 2 shadow + rejected-signal comparison +
counterfactual exits, 3 USDT vs USDC (Wave-2 packet verbatim + descriptive live split), 4 shortlist, 5 operational defects,
plus an optional descriptive ML ledger section (never a class input). A stream is rankable only when it is COMPLETE and has
at least MIN_N closed episodes in that stream; below that a strategy is UNDECIDED, never failed. No class authorizes
routing or promotion. Exchange order / trade ids never reach an output: trade references are hashed.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
if HERE not in sys.path: sys.path.insert(0, HERE)

import paper_books as PB                      # noqa: E402
import strategy_shadow as SSH                 # noqa: E402
import trade_audit as TA                      # noqa: E402
import true_books as TB                       # noqa: E402
import run_pack as RP                         # noqa: E402

FORMAT = 'zb-phase-c-report/1'
SNAPSHOT_FORMAT = 'zb-phase-c-snapshot/1'
CONTRACT = 'issue-148-phase-c.v0 (Codex 6251169379)'
WAVE2_FORMAT = 'zb-quote-pair-wave2-report/1'
MIN_N = 20
KEEP_PF = Decimal('1.10')
SLOW_TFS = ('4h', '1d')
STATE_KEYS = ('cohort', 'funding_unowned', 'lots')          # the only state.json keys read
# the engine's shadow model (engine._shadow_book: MODEL_V1 + FEE_EST + TA.EXEC_SLIP_BPS); a ledger written under another
# model never replays here (ShadowBook marks it model_mismatch -> INCOMPLETE)
SHADOW_MODEL = dict(SSH.MODEL_V1, fee_rate=0.0005, adverse_slippage_bps=TA.EXEC_SLIP_BPS)
WHITELIST = ('history.json', 'cohort_rows.jsonl', 'cohort.json', 'state.json', 'trade_audit.jsonl.1', 'trade_audit.jsonl',
             'ml_shadow/decisions.jsonl', 'ml_shadow/labels.jsonl')
SHADOW_DIR = 'strategy_shadow'
FORBIDDEN_KEYS = frozenset({'orderId', 'order_id', 'tradeId', 'trade_id', 'clientOrderId', 'client_order_id', 'orderid',
                            'cost_refs', 'fills', 'fill_log', 'qty_hist', 'decision_id', 'corr_id', 'candidate_id'})
CLASSES = ('keep_executable', 'shadow_only', 'repair', 'retire', 'UNDECIDED')


class PhaseCError(Exception):
    pass


# ------------------------------------------------------------------ small helpers
def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''): h.update(chunk)
    return h.hexdigest()


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False, default=str)


def dumps(obj):
    return json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=True, allow_nan=False, default=str) + '\n'


def ref(x):
    """A trade / episode reference that is never an exchange id: a 12-hex hash of it."""
    return None if x is None else sha_bytes(str(x).encode('utf-8'))[:12]


def write_once(path, text):
    if os.path.exists(path): raise PhaseCError(f'{path} exists; output is never overwritten')
    tmp = path + '.tmp'
    with open(tmp, 'x', encoding='utf-8', newline='\n') as f:
        f.write(text); f.flush(); os.fsync(f.fileno())
    os.rename(tmp, path)


def D(x):
    return TB.D(x)


def q(d):
    return TB.q(d)


def fnum(x, nd=6):
    return None if x is None else round(float(x), nd)


def family_of(key):
    """The engine's family of a strategy key (paper_books.book_for: an unlisted key is B5_DISCOVERY)."""
    return PB.book_for(key)


def mapped(key):
    return TB.book_of(key) is not None


def _utc_ms(iso, what):
    """Epoch ms of an ISO instant that carries an explicit UTC offset (a naive time would be read as local time)."""
    from datetime import datetime
    try: d = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError): raise PhaseCError(f'{what} is not an ISO time')
    if d.tzinfo is None: raise PhaseCError(f'{what} has no UTC offset')
    return d.timestamp() * 1000


def scrub_check(obj, path='$'):
    """Raise when an output carries a forbidden id-bearing key (defence in depth; outputs are built from whitelists)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_KEYS: raise PhaseCError(f'output carries forbidden key {path}.{k}')
            scrub_check(v, f'{path}.{k}')
    elif isinstance(obj, list):
        for i, v in enumerate(obj): scrub_check(v, f'{path}[{i}]')


# ------------------------------------------------------------------ freeze (Integration)
def allowed(rel):
    """Only the whitelisted artifacts and top-level strategy_shadow/*.jsonl ledgers may be pinned (never config/keys)."""
    if rel in WHITELIST: return True
    parts = rel.split('/')
    return len(parts) == 2 and parts[0] == SHADOW_DIR and parts[1].endswith('.jsonl') and parts[1] not in ('.jsonl',)


def _rel_files(src):
    out = [r for r in WHITELIST if os.path.isfile(os.path.join(src, r))]
    sd = os.path.join(src, SHADOW_DIR)
    if os.path.isdir(sd):
        out += sorted(f'{SHADOW_DIR}/{n}' for n in os.listdir(sd) if n.endswith('.jsonl') and os.path.isfile(os.path.join(sd, n)))
    return out


def freeze(src, dst, snapshot_utc, label='paper'):
    """Copy the whitelisted artifacts into a NEW dir; each file is copied until its source hash is stable across the copy
    (the engine may be appending). Returns the manifest (also written write-once as MANIFEST.phase-c-snapshot.json)."""
    _utc_ms(snapshot_utc, 'snapshot_utc')
    if os.path.exists(dst): raise PhaseCError(f'{dst} exists; a snapshot is never overwritten')
    os.makedirs(dst)
    files = {}
    for rel in _rel_files(src):
        s, d = os.path.join(src, rel), os.path.join(dst, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        for _ in range(5):
            before = sha_file(s)
            if rel == 'state.json':                 # only the keys the report reads; the rest never leaves the data dir
                st = _read_json(s, 'state.json')
                if os.path.exists(d): os.remove(d)
                with open(d, 'x', encoding='utf-8', newline='\n') as f:
                    f.write(dumps({k: st.get(k) for k in STATE_KEYS} if isinstance(st, dict) else {}))
            else:
                shutil.copyfile(s, d)
            got = sha_file(d)
            if before == sha_file(s): break
        else:
            raise PhaseCError(f'{rel} kept changing during the copy; snapshot refused')
        files[rel] = dict(sha256=got, bytes=os.path.getsize(d))
    man = dict(format=SNAPSHOT_FORMAT, snapshot_utc=snapshot_utc, source_label=label, files=files)
    write_once(os.path.join(dst, 'MANIFEST.phase-c-snapshot.json'), dumps(man))
    return man


# ------------------------------------------------------------------ preflight (report)
def _read_json(path, what):
    try:
        with open(path, encoding='utf-8') as f: return json.load(f)
    except (OSError, ValueError) as e:
        raise PhaseCError(f'{what} unreadable: {type(e).__name__}')


def _jsonl(path):
    """(rows, torn line count). A torn / non-object line is counted, never guessed."""
    rows, torn = [], 0
    with open(path, encoding='utf-8') as f:
        for ln in f:
            if not ln.strip(): continue
            try:
                r = json.loads(ln)
            except ValueError:
                torn += 1; continue
            if isinstance(r, dict): rows.append(r)
            else: torn += 1
    return rows, torn


def preflight(snapshot, manifest_path, wave2=None, wave2_sha256=None):
    """Every pinned file must exist and hash to its pin, and the snapshot dir holds nothing else (a live data dir - config,
    settings, unpinned logs - is refused, never read). -> (manifest, pins)."""
    man = _read_json(manifest_path, 'snapshot manifest')
    if not isinstance(man, dict) or man.get('format') != SNAPSHOT_FORMAT or not isinstance(man.get('files'), dict):
        raise PhaseCError('snapshot manifest has the wrong format')
    if not man.get('snapshot_utc'): raise PhaseCError('snapshot manifest has no snapshot_utc')
    _utc_ms(man['snapshot_utc'], 'snapshot_utc')
    root = os.path.abspath(snapshot)
    for rel, rec in sorted(man['files'].items()):
        if not isinstance(rec, dict): raise PhaseCError(f'manifest entry {rel} is not a pin record')
        if not allowed(rel): raise PhaseCError(f'manifest pins {rel}, which is not a Phase-C artifact')
        p = os.path.abspath(os.path.join(root, rel))
        if os.path.commonpath([root, p]) != root or '..' in rel.replace('\\', '/').split('/'):
            raise PhaseCError(f'manifest path {rel} escapes the snapshot')
        if not os.path.isfile(p): raise PhaseCError(f'pinned file {rel} is missing')
        if os.path.islink(p) or os.path.normcase(os.path.realpath(p)) != os.path.normcase(os.path.realpath(root) + p[len(root):]):
            raise PhaseCError(f'pinned file {rel} is a link; refused')
        if sha_file(p) != (rec or {}).get('sha256'): raise PhaseCError(f'pinned file {rel} hash mismatch')
    mp = os.path.abspath(manifest_path)
    pinned = {os.path.normcase(os.path.abspath(os.path.join(root, r))) for r in man['files']} | {os.path.normcase(mp)}
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            if os.path.normcase(os.path.abspath(os.path.join(dp, fn))) not in pinned:
                raise PhaseCError(f'unpinned file {os.path.relpath(os.path.join(dp, fn), root)} in the snapshot (not a frozen snapshot?)')
    pins = dict(manifest_sha256=sha_file(manifest_path), snapshot_utc=man['snapshot_utc'],
                files={k: v['sha256'] for k, v in sorted(man['files'].items())})
    if wave2 is not None:
        sp = os.path.join(wave2, 'summary.json')
        if not os.path.isfile(sp): raise PhaseCError('Wave-2 summary.json is missing')
        pins['wave2_summary_sha256'] = sha_file(sp)
        if wave2_sha256 and pins['wave2_summary_sha256'] != wave2_sha256:
            raise PhaseCError('Wave-2 summary.json does not match the expected pin')
        if not wave2_sha256: raise PhaseCError('a Wave-2 packet needs its expected summary.json sha256 (--wave2-sha256)')
    elif wave2_sha256:
        raise PhaseCError('a Wave-2 pin was given without a packet')
    return man, pins


def _files(snapshot, man):
    return {rel: os.path.join(snapshot, rel) for rel in man['files']}


# ------------------------------------------------------------------ loading
def load_snapshot(snapshot, man):
    fp = _files(snapshot, man)
    out = dict(history=[], cohort_rows=[], state={}, cohort=None, audit=[], ml_dec=[], ml_lab=[], torn={}, audit_duplicates=0, present=sorted(fp))
    if 'history.json' in fp:
        h = _read_json(fp['history.json'], 'history.json')
        if not isinstance(h, list): raise PhaseCError('history.json is not a list')
        out['history'] = [r for r in h if isinstance(r, dict)]
    for rel, key in (('cohort_rows.jsonl', 'cohort_rows'), ('ml_shadow/decisions.jsonl', 'ml_dec'),
                     ('ml_shadow/labels.jsonl', 'ml_lab')):
        if rel in fp:
            out[key], n = _jsonl(fp[rel])
            if n: out['torn'][rel] = n
    seen = set()
    for rel in ('trade_audit.jsonl.1', 'trade_audit.jsonl'):                 # oldest first; an identical line counts once
        if rel in fp:
            rows, n = _jsonl(fp[rel])
            if n: out['torn'][rel] = n
            for r in rows:
                c = canon(r)
                if c in seen: out['audit_duplicates'] += 1; continue
                seen.add(c); out['audit'].append(r)
    if 'state.json' in fp:
        st = _read_json(fp['state.json'], 'state.json')
        out['state'] = {k: st.get(k) for k in STATE_KEYS} if isinstance(st, dict) else {}
    if 'cohort.json' in fp:
        out['cohort'] = _read_json(fp['cohort.json'], 'cohort.json')
    return out


def cohort_records(snap):
    """{cohort id: record} from the sidecar and state (the sidecar wins when both carry the same id)."""
    out = {}
    for rec in ((snap['state'] or {}).get('cohort'), snap['cohort']):
        if isinstance(rec, dict) and rec.get('id'): out[rec['id']] = rec
    return out


def shadow_path(snapshot, man, cid):
    import re
    rel = f'{SHADOW_DIR}/' + re.sub(r'[^A-Za-z0-9_.-]', '_', str(cid)) + '.jsonl'
    return (os.path.join(snapshot, rel), rel) if rel in man['files'] else (None, rel)


# ------------------------------------------------------------------ section 1: executed
def _close_reasons(r):
    why = set(TB._close_gaps(r))
    if not TB._row_complete(r): why.add('cost_incomplete')
    if r.get('_conflict'): why.add('row_conflict')
    if D(r.get('r')) is None: why.add('risk_unknown')
    if r.get('commission_missing'): why.add('commission_missing')
    return sorted(why)


def _net(r):
    g = D(r.get('pnl_gross')) or Decimal(0)
    fa, fu = D(r.get('fees_actual')), D(r.get('funding_actual')) or Decimal(0)
    fe = fa if fa is not None else (D(r.get('fees')) or Decimal(0))
    return g - fe + fu, fe, fu


def _max_dd(nets, start):
    eq = peak = start
    dd = Decimal(0)
    for n in nets:
        eq += n; peak = max(peak, eq)
        if peak > 0: dd = min(dd, eq / peak - 1)
    return dd


NO_LOSSES = 'no_losses'


def _pf_show(pf, closed, expectancy):
    """PF for display: 'no_losses' (effectively infinite) when there are closes, a positive expectancy and no losing
    close; None when undefined for any other reason (no closes, or nothing won and nothing lost)."""
    if pf is not None: return fnum(pf)
    return NO_LOSSES if closed and expectancy is not None and expectancy > 0 else None


def exec_stats(rows, start=TB.START):
    rows = sorted(rows, key=lambda r: (str(r.get('closed') or ''), ref(r.get('id')) or ''))
    nets, rs, notional, fees, fund, why, exits = [], [], Decimal(0), Decimal(0), Decimal(0), {}, {}
    for r in rows:
        n, fe, fu = _net(r)
        nets.append(n); fees += fe; fund += fu
        if D(r.get('r')) is not None: rs.append(D(r.get('r')))
        notional += abs(D(r.get('notional')) or Decimal(0))
        for w in _close_reasons(r): why[w] = why.get(w, 0) + 1
        exits[r.get('exit_reason') or '?'] = exits.get(r.get('exit_reason') or '?', 0) + 1
    s = TB._stats(nets, rs, notional, start)
    loss = -sum((x for x in nets if x <= 0), Decimal(0))
    s['pf_exact'] = str(sum((x for x in nets if x > 0), Decimal(0)) / loss) if loss > 0 else None
    s['pf'] = _pf_show(s['pf'], s['closed'], s['expectancy'])
    s.update(fees=q(fees), funding=q(fund), roi=q(sum(nets, Decimal(0)) / start) if start else None,
             max_dd=q(_max_dd(nets, start)), exits=dict(sorted(exits.items())), incomplete=dict(sorted(why.items())),
             complete=not why, first_close=str(rows[0].get('closed')) if rows else None,
             last_close=str(rows[-1].get('closed')) if rows else None)
    s['rankable'] = s['complete'] and s['closed'] >= MIN_N
    s['note'] = None if s['closed'] >= MIN_N else f'insufficient n ({s["closed"]}/{MIN_N})'
    return s


def ev_ms(x):
    """Epoch ms of an event time: an ISO string WITH a UTC offset, or epoch s / ms. A naive ISO time (which a parser would
    read as local time) or anything unreadable is untimed (None) - never guessed."""
    from datetime import datetime
    if isinstance(x, bool) or x is None: return None
    if isinstance(x, (int, float)): return float(x) if x > 1e11 else float(x) * 1000.0
    try: d = datetime.fromisoformat(str(x).replace('Z', '+00:00'))
    except (TypeError, ValueError): return None
    return None if d.tzinfo is None else d.timestamp() * 1000.0


def _iso_ms(t):
    from datetime import datetime, timezone
    return None if t is None else datetime.fromtimestamp(t / 1000.0, timezone.utc).isoformat()


def window_stats():
    return dict(accepted=0, first_utc=None, last_utc=None, excluded_before_cohort=0, excluded_at_or_after_snapshot=0,
                excluded_untimed=0)


def _place(st, t, lo, hi):
    """Count one record into st; True when it is inside [lo, hi)."""
    if t is None: st['excluded_untimed'] += 1; return False
    if hi is not None and t >= hi: st['excluded_at_or_after_snapshot'] += 1; return False
    if lo is not None and t < lo: st['excluded_before_cohort'] += 1; return False
    st['accepted'] += 1
    st['_lo'] = t if st.get('_lo') is None else min(st['_lo'], t)
    st['_hi'] = t if st.get('_hi') is None else max(st['_hi'], t)
    return True


def _close_stats(st):
    st['first_utc'], st['last_utc'] = _iso_ms(st.pop('_lo', None)), _iso_ms(st.pop('_hi', None))
    return st


def split_window(items, t_of, lo, hi):
    """(items with lo <= event time < hi, accepted min/max + counts excluded before / at-or-after / untimed)."""
    st, keep = window_stats(), []
    for x in items:
        if _place(st, t_of(x), lo, hi): keep.append(x)
    return keep, _close_stats(st)


def cohort_closes(snap, cid, snapshot_utc, start_utc=None):
    """(closes of the cohort with cohort start <= close < snapshot_utc, window stats)."""
    rows = TB.merge_rows([r for r in snap['cohort_rows'] if r.get('cohort_id') == cid] +
                         [r for r in snap['history'] if r.get('cohort_id') == cid])
    return split_window(rows, lambda r: ev_ms(r.get('closed')), ev_ms(start_utc), ev_ms(snapshot_utc))


def _key(r):
    return r.get('strategy_key') or '?'


def book_mismatch(r):
    """A close whose stamped book is not its strategy's family (one strategy is ONE stream, never split by a stray book)."""
    return r.get('book') != family_of(_key(r))


def executed_section(rows):
    fams, strats, cells = [], [], []
    for bk, name, keys in PB.BOOKS:
        mine = [r for r in rows if family_of(_key(r)) == bk]
        fams.append(dict(exec_stats(mine), family=bk, name=name))
        for key in sorted({_key(r) for r in mine} | set(keys)):
            rs = [r for r in mine if _key(r) == key]
            strats.append(dict(exec_stats(rs), family=bk, key=key, mapped=mapped(key),
                               book_mismatch=sum(1 for r in rs if book_mismatch(r))))
            for sym in sorted({str(r.get('symbol')) for r in rs}):
                cells.append(dict(exec_stats([r for r in rs if str(r.get('symbol')) == sym]), family=bk, key=key, symbol=sym))
    by_quote = {}
    for r in rows:
        qa = _quote(r.get('symbol'))
        by_quote.setdefault(f'{r.get("strategy_key") or "?"}|{qa}', []).append(r)
    quote_split = [dict(exec_stats(v), key=k.split('|')[0], quote=k.split('|')[1]) for k, v in sorted(by_quote.items())]
    return dict(families=fams, strategies=strats, strategy_symbol=cells, quote_split=quote_split,
                basis='net = pnl_gross - fees_actual (estimate when missing -> INCOMPLETE) + funding_actual; ROI and max DD '
                      'on a normalized $2,000 per stream, closed-trade equity curve in close order')


def _quote(sym):
    for qa in ('USDT', 'USDC'):
        if isinstance(sym, str) and sym.endswith(qa) and len(sym) > len(qa): return qa
    return 'OTHER'


# ------------------------------------------------------------------ section 2: shadow, rejected signals, counterfactuals
TIMED_KINDS = ('candidate', 'signal', 'bar', 'funding', 'cycle')   # windowed; cadence / incomplete / unknown kinds are kept


def record_time(rec):
    """Event time (epoch s) of a shadow ledger record, None when it has none: candidate = decision close; signal = the
    later of decision close and the observed entry mark; bar = its (close-stamped) bar time; funding = settlement
    instant; cycle = decision time."""
    k = rec.get('kind')
    try:
        if k == 'candidate': ts = [(rec.get('signal') or {}).get('time')]
        elif k == 'signal': ts = [(rec.get('signal') or {}).get('time'), (rec.get('mark') or {}).get('time')]
        elif k == 'bar': ts = [(b or {}).get('time') for b in (rec.get('bars') or {}).values()]
        elif k == 'funding': ts = [rec.get('at')]
        elif k == 'cycle': ts = [rec.get('decision_time')]
        else: return None
        v = [SSH._ts(t) for t in ts if t is not None]
    except (TypeError, ValueError, AttributeError):
        return None
    return max(v) if v else None


def window_ledger(path, out, lo_s, hi_s):
    """Copy the ledger lines whose event time is in [lo, hi) to out (line bytes unchanged; torn lines are kept so the
    reducer still reports them). -> window stats (+ kept_timeless: cadence / incomplete / unknown kinds, kept)."""
    st = dict(window_stats(), kept_timeless=0)
    lo = None if lo_s is None else lo_s * 1000.0
    hi = None if hi_s is None or hi_s == float('inf') else hi_s * 1000.0
    with open(path, 'rb') as f, open(out, 'xb') as g:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                g.write(line); continue
            if not isinstance(rec, dict) or rec.get('kind') not in TIMED_KINDS:   # timeless / unknown: the reducer decides
                g.write(line); st['kept_timeless'] += 1; continue
            t = record_time(rec)
            if _place(st, None if t is None else t * 1000.0, lo, hi): g.write(line)
    return _close_stats(st)


def shadow_section(path, cid, cutoff_utc=None, start_utc=None):
    """Replay the cohort's write-ahead ledger through the shared reducer (no re-derivation). -> (doc, episodes).
    Causality (Codex 6251456639 #1): only records with cohort start <= event time < snapshot_utc enter the reducer - the
    same half-open window as the executed closes; later, earlier and untimed records are excluded and counted. The
    filtered copy lives in a private temp dir (never the snapshot) and is removed after the replay."""
    if path is None:
        return dict(available=False, reason='no shadow ledger pinned for this cohort', strategies=[]), []
    import tempfile
    tmp = tempfile.mkdtemp(prefix='phase_c_shadow_')
    try:
        lp = os.path.join(tmp, 'ledger.jsonl')
        hi = _utc_ms(cutoff_utc, 'snapshot_utc') / 1000.0 if cutoff_utc else float('inf')
        lo = ev_ms(start_utc) / 1000.0 if ev_ms(start_utc) is not None else None
        excl = window_ledger(path, lp, lo, hi)
        sb = SSH.ShadowBook(lp, cid, SHADOW_MODEL, min_n=MIN_N)
        doc = sb.compute()
        eps = list(sb.episodes.values())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rows = []
    for r in doc['strategies']:
        rows.append(dict(key=r['key'], family=family_of(r['key']), config_digest=r['config_digest'],
                         closed=r['closed'], open=r['open'], held=r['held'], episodes=r['episodes'],
                         net=fnum(r['realized']), fees=fnum(r['fees']), funding=fnum(r['funding']), roi=fnum(r['roi']),
                         max_dd=fnum(r['max_dd']), pf=_pf_show(r['pf'], r['closed'], r['expectancy']),
                         pf_exact=None if r['pf'] is None else repr(float(r['pf'])), expectancy=fnum(r['expectancy']),
                         avg_r=fnum(r['avg_r']), net_r=fnum(r['net_r']), win_rate=fnum(r['win_rate'], 4),
                         turnover=fnum(r['turnover']), excursions_excluded=r['excursions_excluded'],
                         mfe_r=r['mfe_r'], mae_r=r['mae_r'], giveback_r=r['giveback_r'], exits=r['exits'],
                         live_decisions=r['live_decisions'],
                         incomplete=sorted({str(w).split(':')[0] for w in r['incomplete']}),
                         complete=r['complete'], rankable=r['complete'] and r['closed'] >= MIN_N,
                         note=None if r['closed'] >= MIN_N else f'insufficient n ({r["closed"]}/{MIN_N})'))
    rows.sort(key=lambda x: (x['family'], x['key'], x['config_digest']))
    return dict(available=True, model_hash=doc['model']['hash'], coverage=dict(complete=doc['coverage']['complete'],
                gaps=sorted({str(g).split(':')[0] for g in doc['coverage']['gaps']})), window_records=excl,
                strategies=rows), eps


def _decision(e):
    ld = e.get('live_decision') or {}
    st = ld.get('status') or 'unknown'
    if st == 'taken': return 'taken'
    if st == 'skipped': return 'skipped:' + (TA.reason_info(ld.get('reason')).get('code') or 'other')
    return st


def rejected_section(episodes):
    """Per strategy x symbol: the shadow outcome of signals the live engine skipped vs those it took (closed episodes)."""
    grp = {}
    for e in episodes:
        k = (e.get('key') or '?', str(e.get('symbol')), _decision(e))
        g = grp.setdefault(k, dict(closed=0, other=0, nets=[], rs=[]))
        if e.get('status') != 'closed': g['other'] += 1; continue
        n = float(e.get('gross') or 0) - float(e.get('fees') or 0) + float(e.get('funding') or 0)
        g['closed'] += 1; g['nets'].append(n)
        if e.get('risk'): g['rs'].append(n / float(e['risk']))
    out = []
    for (key, sym, dec), g in sorted(grp.items()):
        out.append(dict(key=key, family=family_of(key), symbol=sym, decision=dec, closed=g['closed'], not_closed=g['other'],
                        net=fnum(sum(g['nets'])), avg_r=fnum(sum(g['rs']) / len(g['rs'])) if g['rs'] else None))
    taken = {(x['key'], x['symbol']): x for x in out if x['decision'] == 'taken'}
    for x in out:
        t = taken.get((x['key'], x['symbol']))
        x['avg_r_vs_taken'] = None if x['decision'] == 'taken' or not t or t['avg_r'] is None or x['avg_r'] is None \
            else fnum(x['avg_r'] - t['avg_r'])
    return dict(rows=out, basis='shadow-model-v1 episodes; decision = the live engine decision on the same candidate; '
                                'descriptive only (sample sizes shown), never a class input')


def counterfactual_section(rows, audit):
    """Would another predeclared exit have done better on the same trades: causal policies from the trade audit."""
    by_id = {r.get('id'): r for r in rows if r.get('id') is not None}
    grp, hind, seen = {}, {}, set()
    for a in audit:
        if a.get('kind') != 'trade_audit' or a.get('id') not in by_id: continue
        if a.get('id') in seen: continue
        seen.add(a.get('id'))
        r = by_id[a['id']]
        risk = D(r.get('risk_usd'))
        for c in a.get('counterfactuals') or ():
            if not isinstance(c, dict): continue
            k = (r.get('strategy_key') or '?', str(r.get('symbol')), str(c.get('rule')))
            tgt = hind if c.get('kind') == 'hindsight_ceiling' else grp
            g = tgt.setdefault(k, dict(n=0, unevaluated=0, d=[], dr=[]))
            if c.get('kind') not in ('causal_policy', 'hindsight_ceiling') or D(c.get('vs_actual')) is None:
                g['unevaluated'] += 1; continue
            g['n'] += 1; g['d'].append(D(c['vs_actual']))
            if risk and risk > 0: g['dr'].append(D(c['vs_actual']) / risk)
    def rows_of(src):
        return [dict(key=k[0], family=family_of(k[0]), symbol=k[1], rule=k[2], evaluated=g['n'], unevaluated=g['unevaluated'],
                     sum_vs_actual=q(sum(g['d'], Decimal(0))), mean_vs_actual_r=q(sum(g['dr'], Decimal(0)) / len(g['dr'])) if g['dr'] else None)
                for k, g in sorted(src.items())]
    return dict(audited_closes=len(seen), closes=len(by_id), policies=rows_of(grp), hindsight_ceiling=rows_of(hind),
                basis='causal_policy entries only answer "would another exit have been better"; hindsight_ceiling is NOT '
                      'achievable live and is shown apart; positive vs_actual = the policy beat the actual exit')


# ------------------------------------------------------------------ section 3: USDT vs USDC (Wave-2 verbatim)
def wave2_section(wave2):
    if wave2 is None: return dict(available=False, reason='no Wave-2 packet supplied')
    import run_quote_pair_wave2 as W2
    s = _read_json(os.path.join(wave2, 'summary.json'), 'Wave-2 summary')
    if s.get('format') != WAVE2_FORMAT: raise PhaseCError('Wave-2 summary has the wrong format')
    cells = []
    for name, c in sorted((s.get('cells') or {}).items()):
        env = W2.load_cell(os.path.join(wave2, 'cells', f'{name}.json'), name, s.get('preflight_sha256'))
        if env['cell_sha256'] != c.get('cell_sha256'): raise PhaseCError(f'Wave-2 cell {name} does not match its summary pin')
        for sid, row in sorted((c.get('scenarios') or {}).items()):
            qs = {}
            for qa, x in sorted((row.get('quotes') or {}).items()):
                qs[qa] = {k: x.get(k) for k in ('closed_trades', 'open_trades', 'gross_pnl', 'fees', 'adverse_slippage',
                                                'funding', 'net_pnl', 'turnover', 'expectancy_r', 'slip_r_sum', 'fee_r_sum',
                                                'funding_r_sum', 'net_r_sum', 'pf', 'max_dd_r')}
                if isinstance(x.get('fee_window'), dict): qs[qa]['coverage'] = x['fee_window']
            p = row.get('paired')
            cells.append(dict(cell=name, role=c.get('role'), strategy=c.get('strategy'), side=c.get('side'), tf=c.get('tf'),
                              scenario=sid, status=row.get('status'), quotes=qs,
                              paired=None if p is None else dict(metrics=p.get('metrics'), sufficiency=p.get('sufficiency'),
                                                                 n_paired=p.get('n_paired'), unmatched=p.get('unmatched'),
                                                                 verdict_use=p.get('verdict_use'))))
    return dict(available=True, run_id=s.get('run_id'), preflight_sha256=s.get('preflight_sha256'),
                packet_status=s.get('packet_status'), verdict=s.get('verdict'), usdc_better=s.get('usdc_better'),
                global_verdict_available=s.get('global_verdict_available'), non_promotable=s.get('non_promotable'),
                controls_not_run=s.get('controls_not_run'), cells=cells,
                basis='copied verbatim from the frozen packet; slippage, fees, funding and coverage are separate columns; '
                      'this report adds no USDC verdict')


# ------------------------------------------------------------------ section 5: defects
def defects_section(rows, shadow_rows, shadow_cov, audit, state, cohort_status, torn, window, dup=0, late=0):
    q_ = {}

    def add(source, code, key=None, symbol=None, t=None, class_input=False, n=1):
        k = (source, code, key, symbol)
        d = q_.setdefault(k, dict(source=source, code=code, key=key, family=family_of(key) if key else None, symbol=symbol,
                                  count=0, first_t=None, last_t=None, class_input=class_input))
        d['count'] += n
        if t:
            d['first_t'] = min(d['first_t'] or str(t), str(t)); d['last_t'] = max(d['last_t'] or str(t), str(t))

    for r in rows:
        for w in _close_reasons(r):
            add('executed_close', w, _key(r), str(r.get('symbol')), r.get('closed'), class_input=True)
        if book_mismatch(r): add('executed_close', 'book_mismatch', _key(r), str(r.get('symbol')), r.get('closed'), class_input=True)
    for s in shadow_rows:
        for w in s['incomplete']: add('shadow', w, s['key'], class_input=True)
    for g in shadow_cov.get('gaps') or (): add('shadow_ledger', g)
    ids = {r.get('id'): r for r in rows}
    audit_w, _st = split_window(audit, lambda a: ev_ms(a.get('t')), ev_ms(window[0]), ev_ms(window[1]))
    for a in audit_w:
        t = a.get('t')
        if a.get('kind') == 'coverage':
            r = ids.get(a.get('id')) or {}
            add('trade_audit_coverage', 'coverage_partial', r.get('strategy_key'), a.get('symbol'), t)
        if a.get('kind') == 'trade_audit' and a.get('id') in ids:
            for w in a.get('coverage_why') or ():
                add('trade_audit_tracking', str(w), ids[a['id']].get('strategy_key'), a.get('symbol'), t)
    for f in (state or {}).get('funding_unowned') or ():
        if isinstance(f, dict): add('funding_unowned', 'funding_unattributed', None, f.get('symbol'), f.get('time_ms'))
        else: add('funding_unowned', 'funding_unattributed')
    for code in cohort_status: add('cohort', code)
    for rel, n in sorted(torn.items()): add('snapshot', f'torn_lines:{rel}', n=n)
    if dup: add('snapshot', 'duplicate_audit_lines', n=dup)
    if late: add('snapshot', 'closes_outside_window_or_untimed', n=late)
    items = sorted(q_.values(), key=lambda d: (-d['count'], d['source'], d['code'], d['key'] or '', d['symbol'] or ''))
    return dict(queue=items, class_inputs='executed_close and shadow items attributed to a strategy make it "repair"; '
                                          'tracking / coverage / unowned items are operational only')


# ------------------------------------------------------------------ section 4: shortlist
def classify(key, ex, shs, defects):
    """One strategy's class with every input shown. ex: executed strategy row or None; shs: its shadow rows (one per
    config lineage); defects: its class-input defect codes."""
    sh = shs[0] if len(shs) == 1 else None
    ins = dict(executed=None if ex is None else {k: ex.get(k) for k in ('closed', 'complete', 'rankable', 'expectancy', 'pf', 'pf_exact', 'avg_r', 'net')},
               shadow=None if sh is None else {k: sh.get(k) for k in ('closed', 'complete', 'rankable', 'expectancy', 'pf', 'pf_exact', 'avg_r', 'net')},
               shadow_lineages=len(shs), defects=sorted(defects))
    exr = bool(ex and ex['rankable']); shr = bool(sh and sh['rankable'])
    pos = lambda r: r.get('expectancy') is not None and r['expectancy'] > 0
    neg = lambda r: r.get('expectancy') is not None and r['expectancy'] < 0
    # PF is undefined (None) only when there is no losing close: with closes and expectancy > 0 that is an unbounded PF
    def pf_ok(r):                               # the exact PF, never the 4-decimal display value (Cowork G1)
        if r.get('pf') == NO_LOSSES: return bool(r.get('closed')) and pos(r)
        x = D(r.get('pf_exact')) if r.get('pf_exact') is not None else D(r.get('pf'))
        return x is not None and x >= KEEP_PF
    if exr and shr and pos(ex) and pos(sh) and pf_ok(ex) and pf_ok(sh) and not defects:
        unb = ' (PF no_losses: effectively infinite)' if NO_LOSSES in (ex.get('pf'), sh.get('pf')) else ''
        return 'keep_executable', 'both streams rankable, expectancy > 0 and PF >= 1.10 in both, no attributed defect' + unb, ins
    if defects:
        return 'repair', 'attributed defect(s): ' + ', '.join(sorted(defects)), ins
    if exr and shr and pos(ex) != pos(sh) and not (ex['expectancy'] == 0 or sh['expectancy'] == 0):
        return 'repair', 'executed and shadow disagree in sign', ins
    if exr and shr and neg(ex) and neg(sh):
        return 'retire', 'both streams rankable with expectancy < 0', ins
    if shr and pos(sh) and not exr:
        return 'shadow_only', 'shadow rankable and positive; executed not rankable (n < 20 or INCOMPLETE)', ins
    if len(shs) > 1: why = f'{len(shs)} shadow config lineages; no single shadow stream'
    elif not exr and not shr: why = 'neither stream rankable (complete and >= 20 closes)'
    else: why = 'rules not met'
    return 'UNDECIDED', why, ins


def shortlist(exec_doc, shadow_doc, defects_doc, tfs):
    exs = {s['key']: s for s in exec_doc['strategies'] if s['key'] != '?'}
    shs = {}
    for s in shadow_doc.get('strategies') or (): shs.setdefault(s['key'], []).append(s)
    dfx = {}
    for d in defects_doc['queue']:
        if d['class_input'] and d['key']: dfx.setdefault(d['key'], set()).add(f'{d["source"]}:{d["code"]}')
    out = []
    for key in sorted(set(exs) | set(shs)):
        ex = exs.get(key)
        if ex is not None and ex['closed'] == 0: ex = None
        cls, why, ins = classify(key, ex, shs.get(key, []), dfx.get(key, set()))
        tf = tfs.get(key)
        note = 'slow timeframe below n: UNDECIDED, not failed' if cls == 'UNDECIDED' and tf in SLOW_TFS else None
        out.append(dict(key=key, family=family_of(key), tf=tf, cls=cls, why=why, note=note, inputs=ins))
    return dict(rows=out, rule=dict(min_n=MIN_N, keep_pf=str(KEEP_PF), order=list(CLASSES)),
                authority='NO class authorizes routing, sizing or promotion')


def _tfs():
    import strategies as S
    out = {}
    for k, v in (getattr(S, 'STRATEGIES', None) or {}).items():
        tf = v.get('tf') if isinstance(v, dict) else getattr(v, 'tf', None)
        if tf: out[k] = tf
    return out


# ------------------------------------------------------------------ ML (descriptive, optional)
def ml_section(snap, window):
    if not snap['ml_dec'] and not snap['ml_lab']:
        return dict(available=False, reason='ML ledger not present in the snapshot (ML_SHADOW off or not pinned)')
    dec, _st = split_window(snap['ml_dec'], lambda d: ev_ms(d.get('ts_utc')), ev_ms(window[0]), ev_ms(window[1]))
    lab = {l.get('decision_id'): l for l in snap['ml_lab']}
    grp = {}
    for d in dec:
        g = grp.setdefault(str(d.get('strategy')), dict(decisions=0, taken=0, labelled=0, taken_unlabelled=0, rs=[]))
        g['decisions'] += 1
        tk = bool(d.get('taken'))
        g['taken'] += tk
        l = lab.get(d.get('decision_id'))
        if l is not None:
            g['labelled'] += 1
            if D(l.get('net_r')) is not None: g['rs'].append(D(l['net_r']))
        elif tk: g['taken_unlabelled'] += 1
    rows = [dict(strategy=k, family=family_of(k), decisions=g['decisions'], taken=g['taken'], labelled=g['labelled'],
                 taken_unlabelled=g['taken_unlabelled'], mean_label_net_r=q(sum(g['rs'], Decimal(0)) / len(g['rs'])) if g['rs'] else None)
            for k, g in sorted(grp.items())]
    return dict(available=True, decisions_in_window=len(dec), rows=rows,
                complete=all(r['taken_unlabelled'] == 0 for r in rows),
                authority='descriptive only; never a shortlist, class or promotion input')


# ------------------------------------------------------------------ build
REQUIRED = ('state.json', 'cohort.json', 'trade_audit.jsonl')


HOLDOUT_FLOOR_UTC = '2026-09-28T00:00:00+00:00'


def cohort_status(snap, rank, recs, man=None, snapshot=None):
    why = []
    if not rank: return ['no_clean_rank_cohort']
    files = set((man or {}).get('files') or ())
    why += [f'missing_input:{rel}' for rel in REQUIRED if rel not in files]
    if not files & {'history.json', 'cohort_rows.jsonl'}: why.append('missing_input:history.json|cohort_rows.jsonl')
    for cid in rank:
        if shadow_path(snapshot, man, cid)[0] is None: why.append(f'missing_input:{shadow_path(snapshot, man, cid)[1]}')
    for cid in rank:
        rec = recs.get(cid)
        if rec is None: why.append('rank_cohort_record_missing'); continue
        if RP.validate_cohort(rec): why.append('rank_cohort_invalid')
    lots = (snap['state'] or {}).get('lots') or {}
    if isinstance(lots, dict) and any(isinstance(l, dict) and l.get('cohort_id') not in rank for l in lots.values()):
        why.append('inherited_positions_open')
    return sorted(set(why))


def build(snapshot, manifest_path, rank=(), diagnostic=(), wave2=None, wave2_sha256=None):
    rank, diagnostic = sorted(set(rank)), sorted(set(diagnostic))
    if set(rank) & set(diagnostic): raise PhaseCError('a cohort cannot be both rank and diagnostic')
    man, pins = preflight(snapshot, manifest_path, wave2, wave2_sha256)
    snap = load_snapshot(snapshot, man)
    recs = cohort_records(snap)
    cst = cohort_status(snap, rank, recs, man, snapshot)
    tfs = _tfs()
    body = dict(format=FORMAT, contract=CONTRACT, authority='READ_ONLY; NO_EXECUTION_ROUTING_OR_PROMOTION_AUTHORITY',
                pins=pins, snapshot_utc=man['snapshot_utc'], holdout_read=False, research_data_read=False,
                rank_cohorts=rank, diagnostic_cohorts=diagnostic, cohort_status=cst, min_n=MIN_N, non_promotable=True,
                source_cutoffs=dict(holdout_floor_utc=HOLDOUT_FLOOR_UTC, window='cohort start <= event time < snapshot_utc',
                                    basis='forward PAPER runtime artifacts (Codex Q1 in scope); accepted min/max event time '
                                          'and exclusion counts per logical source are in each cohort block; file mtimes '
                                          'are never evidence time'))

    def per_cohort(cid):
        rec = recs.get(cid) or {}
        window = (rec.get('started_utc'), man['snapshot_utc'])
        rows, st_ex = cohort_closes(snap, cid, man['snapshot_utc'], rec.get('started_utc'))
        late = st_ex['excluded_before_cohort'] + st_ex['excluded_at_or_after_snapshot'] + st_ex['excluded_untimed']
        ex = dict(executed_section(rows), excluded_outside_window_or_untimed=late)
        sp, rel = shadow_path(snapshot, man, cid)
        sh, eps = shadow_section(sp, cid, man['snapshot_utc'], rec.get('started_utc'))
        df = defects_section(rows, sh.get('strategies') or [], sh.get('coverage') or {}, snap['audit'], snap['state'],
                             cst if cid in rank else [], snap['torn'], window, snap['audit_duplicates'], late)
        lo, hi = ev_ms(window[0]), ev_ms(window[1])
        cut = dict(executed_closes=st_ex,
                   trade_audit=split_window(snap['audit'], lambda a: ev_ms(a.get('t')), lo, hi)[1],
                   ml_decisions=split_window(snap['ml_dec'], lambda d: ev_ms(d.get('ts_utc')), lo, hi)[1]
                   if snap['ml_dec'] else None,
                   shadow_ledger=sh.get('window_records'))
        return dict(cohort_id=cid, window=dict(from_utc=window[0], to_utc_exclusive=window[1]), source_cutoffs=cut,
                    shadow_ledger=rel,
                    executed=ex, shadow=sh, rejected_signals=rejected_section(eps),
                    counterfactual_exits=counterfactual_section(rows, snap['audit']), defects=df, ml=ml_section(snap, window))

    body['rank'] = [per_cohort(c) for c in rank]
    body['diagnostic'] = [dict(per_cohort(c), use='DIAGNOSTIC: never a shortlist input') for c in diagnostic]
    body['usdt_vs_usdc'] = wave2_section(wave2)
    if len(rank) == 1 and not cst:
        r = body['rank'][0]
        body['shortlist'] = shortlist(r['executed'], r['shadow'], r['defects'], tfs)
    else:
        keys = sorted({s['key'] for c in body['rank'] + body['diagnostic'] for s in c['executed']['strategies']
                       if s['key'] != '?' and s['closed']} | {s['key'] for c in body['rank'] for s in c['shadow'].get('strategies') or ()})
        why = ', '.join(cst) if cst else 'more than one rank cohort; streams are not pooled'
        body['shortlist'] = dict(rows=[dict(key=k, family=family_of(k), tf=tfs.get(k), cls='UNDECIDED', why=why, note=None, inputs=None)
                                       for k in keys], rule=dict(min_n=MIN_N, keep_pf=str(KEEP_PF), order=list(CLASSES)),
                                 authority='NO class authorizes routing, sizing or promotion')
    shadow_ok = all(c['shadow'].get('available') and c['shadow']['coverage']['complete'] for c in body['rank'])
    body['report_status'] = 'COMPLETE' if (not cst and shadow_ok and body['usdt_vs_usdc'].get('available')) else 'INCOMPLETE'
    scrub_check(body)
    return body


MD_ESC = {'|': '\\|', '`': '\\`', '<': '&lt;', '>': '&gt;', '&': '&amp;', '*': '\\*', '_': '\\_', '[': '\\[',
          ']': '\\]', '\\': '\\\\'}


def _md(x):
    """A markdown table cell: markup characters escaped, every control character (C0, DEL, C1) replaced by '?'."""
    if x is None: return ''
    return ''.join('?' if (ord(c) < 32 or 127 <= ord(c) < 160) else MD_ESC.get(c, c) for c in str(x))


def markdown(b):
    L = [f'# Phase C evidence read - snapshot {b["snapshot_utc"]}', '',
         f'Status: **{b["report_status"]}** · rank cohorts: {", ".join(b["rank_cohorts"]) or "none"} · '
         f'diagnostic: {", ".join(b["diagnostic_cohorts"]) or "none"} · cohort status: {", ".join(b["cohort_status"]) or "ok"}',
         '', 'Read-only. No class authorizes routing, sizing or promotion.', '', '## Shortlist', '',
         '| strategy | family | tf | class | why |', '|---|---|---|---|---|']
    for r in b['shortlist']['rows']:
        L.append(f'| {_md(r["key"])} | {_md(r["family"])} | {_md(r["tf"])} | {_md(r["cls"])} | {_md(r["why"])}'
                 f'{" - " + _md(r["note"]) if r["note"] else ""} |')
    for c in b['rank'] + b['diagnostic']:
        L += ['', f'## Cohort {c["cohort_id"]}{" (DIAGNOSTIC)" if c.get("use") else ""}', '',
              '| family | closes | net | PF | exp | avg R | max DD | complete |', '|---|---|---|---|---|---|---|---|']
        for f in c['executed']['families']:
            if f['closed']: L.append(f'| {f["family"]} | {f["closed"]} | {f["net"]} | {f["pf"]} | {f["expectancy"]} | {f["avg_r"]} | {f["max_dd"]} | {f["complete"]} |')
        L += ['', f'Defects: {len(c["defects"]["queue"])} queue items. Shadow ledger: '
                  f'{"available" if c["shadow"].get("available") else "missing"}.']
    w = b['usdt_vs_usdc']
    L += ['', '## USDT vs USDC', '', f'Wave-2 packet {w.get("run_id")}: {w.get("packet_status")}, {w.get("usdc_better")}'
          if w.get('available') else 'Wave-2 packet not supplied.']
    return '\n'.join(L) + '\n'


def run(snapshot, manifest, out_dir, rank=(), diagnostic=(), wave2=None, wave2_sha256=None):
    so, oo = os.path.normcase(os.path.abspath(snapshot)), os.path.normcase(os.path.abspath(out_dir))
    if oo == so or oo.startswith(so + os.sep): raise PhaseCError('the output dir is inside the snapshot; refused')
    os.makedirs(out_dir, exist_ok=True)
    for n in ('report.json', 'report.md', 'INCOMPLETE.json'):
        if os.path.exists(os.path.join(out_dir, n)): raise PhaseCError(f'{n} exists in {out_dir}; never overwritten')
    try:
        body = build(snapshot, manifest, rank, diagnostic, wave2, wave2_sha256)
    except Exception as e:                       # any failure (incl. a reducer / Wave-2 error) leaves an INCOMPLETE record
        msg = str(e) if isinstance(e, PhaseCError) else f'{type(e).__name__}: {str(e)[:200]}'
        write_once(os.path.join(out_dir, 'INCOMPLETE.json'),
                   dumps(dict(format=FORMAT, report_status='INCOMPLETE', refused=msg, non_promotable=True)))
        if isinstance(e, PhaseCError): raise
        raise PhaseCError(msg) from e
    write_once(os.path.join(out_dir, 'report.json'), dumps(body))
    write_once(os.path.join(out_dir, 'report.md'), markdown(body))
    return body


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('freeze', help='Integration: copy whitelisted PAPER artifacts into a new pinned snapshot dir')
    f.add_argument('--src', required=True); f.add_argument('--dst', required=True); f.add_argument('--snapshot-utc', required=True)
    r = sub.add_parser('report', help='build the Phase-C report from a frozen snapshot')
    r.add_argument('--snapshot', required=True); r.add_argument('--manifest', required=True); r.add_argument('--out-dir', required=True)
    r.add_argument('--rank-cohort', action='append', default=[]); r.add_argument('--diagnostic-cohort', action='append', default=[])
    r.add_argument('--wave2'); r.add_argument('--wave2-sha256')
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'freeze':
            m = freeze(a.src, a.dst, a.snapshot_utc)
            print(f'snapshot: {len(m["files"])} files pinned -> {a.dst}')
        else:
            b = run(a.snapshot, a.manifest, a.out_dir, a.rank_cohort, a.diagnostic_cohort, a.wave2, a.wave2_sha256)
            print(f'report_status={b["report_status"]} shortlist={len(b["shortlist"]["rows"])} -> {a.out_dir}')
    except PhaseCError as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
