"""RUN-PACK-01: immutable PAPER cohort identity and a checksum-verifiable, sanitized evidence pack.

A cohort is one uninterrupted experiment run (same code, book map, fee model and capital). Its record is created once and
never rewritten while it is open; every new book lot / closed trade carries its id. At a cohort boundary the evidence is
written to a pack outside the runtime: every file with its bytes / rows / sha256 in MANIFEST.json, written to a temp dir
and renamed into place (never overwrites), scanned so no credential value or secret-named key can be inside.
"""
import hashlib, json, os, re, shutil

COHORT_FIELDS = ('started_utc', 'seq', 'mode', 'venue', 'code_sha', 'settings_sha', 'book_version', 'fee_model_sha', 'book_cap')
SECRET_KEY_RX = re.compile(r'(?i)^(.*api[_-]?(key|secret)|.*secret|private[_-]?key|.*passw(or)?d|.*token|.*_pin|pin)$')
BOT_TOKEN_RX = re.compile(rb'\d{5,}:[A-Za-z0-9_\-]{20,}')
MANIFEST = 'MANIFEST.json'


class PackRefused(Exception):
    pass


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_doc(doc):
    return sha256_bytes(json.dumps(doc, sort_keys=True, separators=(',', ':'), default=str).encode())


def code_sha(paths):
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(os.path.basename(p).encode() + b'\0')
        with open(p, 'rb') as f: h.update(f.read())
    return h.hexdigest()[:16]


def cohort_id(rec):
    body = '|'.join(str(rec.get(k)) for k in COHORT_FIELDS)
    t = str(rec['started_utc'])[:19].replace('-', '').replace(':', '')
    return f'CH-{t}Z-{sha256_bytes(body.encode())[:8]}'


def new_cohort(started_utc, code_sha, settings_sha, book_version, fee_model_sha, book_cap, seq=0):
    rec = dict(started_utc=started_utc, seq=int(seq), mode='PAPER', venue='binance_futures_testnet', code_sha=code_sha,
               settings_sha=settings_sha, book_version=book_version, fee_model_sha=fee_model_sha, book_cap=float(book_cap),
               ended_utc=None)
    rec['id'] = cohort_id(rec)
    return rec


def validate_cohort(rec):
    """Problems with a stored cohort record ([] = intact). The id is a hash of its fields, so an edit is visible."""
    if not isinstance(rec, dict): return ['not a record']
    miss = [k for k in COHORT_FIELDS + ('id',) if k not in rec]
    if miss: return [f'missing {", ".join(miss)}']
    out = []
    if rec['mode'] != 'PAPER': out.append('mode is not PAPER')
    if rec['id'] != cohort_id(rec): out.append('id does not match its fields (record was edited)')
    return out


def drift(rec, **now):
    """Which identity components differ from what the cohort was started with (observe-only warning)."""
    return sorted(k for k, v in now.items() if rec.get(k) != v)


def sanitize_settings(doc, allow):
    """Allow-listed, non-secret settings only."""
    return {k: doc[k] for k in sorted(allow) if k in doc and not SECRET_KEY_RX.match(k)}


def _secret_keys(obj, path=''):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if SECRET_KEY_RX.match(str(k)): yield f'{path}/{k}'
            yield from _secret_keys(v, f'{path}/{k}')
    elif isinstance(obj, list):
        for i, v in enumerate(obj): yield from _secret_keys(v, f'{path}[{i}]')


def scan(name, data, forbid=()):
    """Raise PackRefused when `data` (bytes) holds a credential value, a bot-token-shaped value or a secret-named JSON key.
    The message names the file and the kind of hit, never the value."""
    for v in forbid:
        if v and len(v) >= 8 and v.encode() in data: raise PackRefused(f'{name}: contains a credential value')
    if BOT_TOKEN_RX.search(data): raise PackRefused(f'{name}: contains a token-shaped value')
    docs = []
    try: docs = [json.loads(data)]
    except ValueError:
        for ln in data.splitlines():
            try: docs.append(json.loads(ln))
            except ValueError: pass
    for d in docs:
        for hit in _secret_keys(d): raise PackRefused(f'{name}: secret-named key {hit}')


def _rows(name, data):
    if name.endswith('.jsonl'): return sum(1 for ln in data.splitlines() if ln.strip())
    try: d = json.loads(data)
    except ValueError: return None
    return len(d) if isinstance(d, (list, dict)) else None


def write_pack(dest_root, cohort, docs=None, sources=None, forbid=()):
    """docs: {name: json-able}; sources: {name: existing file path (missing files are recorded as absent)}.
    Returns (pack_dir, pack_sha). Never overwrites; on any failure nothing is left at the final path."""
    final = os.path.join(dest_root, cohort['id'])
    if os.path.lexists(final): raise PackRefused(f'{final} already exists - a pack is never overwritten')
    blobs, absent = {}, []
    for name, doc in sorted((docs or {}).items()):
        blobs[name] = json.dumps(doc, indent=1, sort_keys=True, default=str).encode()
    for name, p in sorted((sources or {}).items()):
        if not p or not os.path.isfile(p): absent.append(name); continue
        with open(p, 'rb') as f: blobs[name] = f.read()
    for name, b in blobs.items():
        if os.path.basename(name) != name or name == MANIFEST: raise PackRefused(f'bad file name {name!r}')
        scan(name, b, forbid)
    files = [dict(file=n, bytes=len(b), rows=_rows(n, b), sha256=sha256_bytes(b)) for n, b in sorted(blobs.items())]
    man = json.dumps(dict(cohort=cohort, files=files, absent=sorted(absent), pack_version=1), indent=1, sort_keys=True).encode()
    os.makedirs(dest_root, exist_ok=True)
    tmp = os.path.join(dest_root, f".tmp-{cohort['id']}-{os.getpid()}")
    os.mkdir(tmp)
    try:
        for name, b in list(blobs.items()) + [(MANIFEST, man)]:
            with open(os.path.join(tmp, name), 'xb') as f:
                f.write(b); f.flush(); os.fsync(f.fileno())
        if os.path.lexists(final): raise PackRefused(f'{final} appeared while writing - not overwritten')
        os.rename(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True); raise
    return final, sha256_bytes(man)


def verify_pack(path):
    """Re-hash every file against MANIFEST.json. {'ok', 'pack_sha', 'problems'}."""
    probs = []
    try:
        with open(os.path.join(path, MANIFEST), 'rb') as f: raw = f.read()
        man = json.loads(raw)
    except (OSError, ValueError) as ex:
        return dict(ok=False, pack_sha=None, problems=[f'manifest unreadable ({type(ex).__name__})'])
    listed = {x['file']: x for x in man.get('files', [])}
    for name, x in sorted(listed.items()):
        try:
            with open(os.path.join(path, name), 'rb') as f: b = f.read()
        except OSError: probs.append(f'{name}: missing'); continue
        if len(b) != x['bytes'] or sha256_bytes(b) != x['sha256']: probs.append(f'{name}: checksum mismatch')
    extra = sorted(set(os.listdir(path)) - set(listed) - {MANIFEST})
    if extra: probs.append(f'unlisted files: {", ".join(extra)}')
    probs += [f'cohort: {p}' for p in validate_cohort(man.get('cohort'))]
    return dict(ok=not probs, pack_sha=sha256_bytes(raw), problems=probs, cohort_id=(man.get('cohort') or {}).get('id'))


def replay_check(path):
    """Restorability: per-book closed net recomputed from the packed history equals the packed final ledger."""
    import book_ledger as BL
    with open(os.path.join(path, MANIFEST), 'rb') as f: coh = json.loads(f.read())['cohort']
    with open(os.path.join(path, 'history.json'), 'rb') as f: hist = json.loads(f.read())
    with open(os.path.join(path, 'book_ledger_final.json'), 'rb') as f: led = json.loads(f.read())
    doc = BL.compute(coh, hist, (), {}, coh['book_cap'])
    return {k: [b['realized_net'], led['books'][k]['realized_net']] for k, b in doc['books'].items()
            if abs(b['realized_net'] - led['books'][k]['realized_net']) > 1e-6 or b['closed'] != led['books'][k]['closed']}
