"""Freeze NF-22 onward (Cowork cross-check follow-up) from the bytes staged by repro_e_nc02_ext.py.

    python repro_e_nc02_ext.py <stage>  > out_e.txt      # legacy run on the exact bytes it stages
    python make_fixtures_ext.py <stage> <fixtures_dir>

FREEZE RULE: NF-01..NF-21 are frozen. This script never opens, rewrites or re-hashes them; it refuses to run if any target
directory already exists, and it only ADDS keys to MANIFEST.json (existing entries are compared byte-for-byte before
writing). Contract v2 (NF-22+) hashes more fields than v1, because these fixtures also pin the outcome, the mtimes, the
on-disk layout and the injected faults.

The legacy_observed text below is the reading of out_e.txt from the run that staged these bytes; file:line cite master
cff3f88 engine.py unless another file is named."""
import hashlib, json, os, shutil, sys

STAGE, OUT = (os.path.abspath(a) for a in sys.argv[1:3])
FROZEN = {f'NF-{i:02d}' for i in range(1, 22)}
BASE_MS = 1_791_400_000_000          # 2026-10-08 UTC; the runner sets every input mtime from input_mtimes_ms (git keeps none)
D1, D2, D3, D4 = ('D1 future schema: abort, state+backup byte-identical', 'D2 current damage: fail closed, keep evidence',
                  'D3 never an empty managed account', 'D4 explicit exchange/state reconciliation before management resumes')
V2_FIELDS = ('id', 'reader_build', 'kind', 'input_files', 'input_mtimes_ms', 'layout', 'inject', 'exchange', 'nc02_outcome',
             'nc02_required')

# New requirement ids (added to MANIFEST.requirements; the ten v1 ids are unchanged and reused).
R_NEW = {
    'R-STRICT-PARSE': 'the store parser treats as DAMAGE (rule 4, HOLD with evidence) - never as a valid document and never as an '
                      'uncaught exception: duplicate object keys, NaN/Infinity tokens, numbers outside the declared range '
                      '(quantities above the venue maximum, integers outside int64), any bytes after the document (NUL padding '
                      'included), and a document that is all NUL',
    'R-VERSION-TYPE': 'a format/version header that is not a JSON integer >= 0 (string, float, bool, null, negative) is an UNKNOWN '
                      'format and is handled like a future one (rule 1, ABORT-RO): the reader cannot prove it is older',
    'R-WRITE-VALIDATES': 'every record is validated with the reader schema BEFORE commit; a record the reader would reject is never '
                         'committed (the intent is refused and nothing is sent)',
    'R-INTENT-SHAPE': 'an order intent carries kind, client id, created_ms (integer UTC ms) and a quantity that is > 0, a whole '
                      'number of venue steps and <= the venue maximum; any other shape is refused at write time and is damage '
                      'at read time',
    'R-NOTFOUND-NOT-PROOF': 'a bare not-found order lookup never resolves an intent; only a final order record (or a venue error '
                            'that proves rejection) does. Until then the outcome is unknown (None) and the symbol/side stays HOLD '
                            'for new risk',
    'R-RESULT-BEFORE-APPLY': 'ownership changes only from a durable result record; reconcile never books an exit (stop, resync, '
                             'close) at a guessed price from a position delta; an unexplained delta is a HOLD item',
    'R-UNCOMMITTED': 'files that are not part of a committed generation (temp, partial, orphan) are never read as state and never '
                     'silently deleted; they are reported and kept as evidence. A torn uncommitted temp alone does not block MANAGE',
    'R-IO-FAULT': 'an I/O fault on the store (ENOSPC, EROFS, a directory or other non-file at a store path, permission) means no '
                  'intent can be made durable, so nothing is sent; it is reported out-of-band; no partial generation and no '
                  'partial evidence is left behind',
    'R-KNOWN-EMPTY': 'an empty portfolio is KNOWN empty only after a proven first run with a flat exchange, or through a journaled '
                     'chain of results from the last non-empty generation; otherwise ownership is unknown (None, never []) and '
                     'can never auto-match a flat exchange',
    'R-IDENTITY': 'account binding is verified before any match; position equality never proves identity; an identity mismatch '
                  'stays HOLD until the owner confirms the binding AND a fresh reconciliation record exists',
    'R-MATCH': '"match" = same account binding + snapshot younger than the max age + per symbol/side aggregate quantity equal within '
               'the venue step tolerance + every owned order/stop id present with the expected lifecycle state + no unowned '
               'position or order; re-checked atomically at promotion',
    'R-NO-AUTO-CANCEL-FOREIGN': 'an unknown/foreign order or position is never cancelled, closed or adopted automatically; it is a '
                                'per-item owner decision while the account is HOLD',
    'R-ROLLBACK-DETECT': 'a readable generation lower than the high-water (or than what the exchange history proves) is HOLD, '
                         'never MANAGE; with no surviving high-water, the exchange difference alone must still yield HOLD',
    'R-HOLD': 'HOLD (Codex rulings 3 and 6): no new risk from any source (auto, manual, Telegram, UI, grid, maker); '
              'non-protective open intents are cancelled or drained through the priority queue; a late or partial fill is '
              'adopted and protected and stays blocked from new risk until reconciled; protective orders are kept; durable '
              'local append-only intent/result/incident events are allowed; no promotion and no reconciliation claim',
    'R-NO-LEGACY-IMPORT': 'NEWCORE never reads legacy state/settings/install files as state (Codex ruling 10): their presence is '
                          'reported, their bytes stay identical, and NEWCORE starts with INIT on testnet (flat exchange -> MANAGE '
                          'empty; non-flat -> HOLD-INIT)',
}
with open(os.path.join(OUT, 'MANIFEST.json'), 'rb') as fh: MAN_RAW = fh.read()
MAN = json.loads(MAN_RAW)
R_ALL = dict(MAN['requirements'], **R_NEW)

GATE = ('resume allowed by the app/telegram gate that checks install_block only (app.py:1225-1227, telegram_ctl.py:524); MANUAL '
        'entries pass while paused (engine.py:3415); untracked only after 2 reconcile passes (engine.py:3268-3272)')
SENT = 'a new automatic ETHUSDT entry was SENT after resume. ' + GATE

# id: (kind, outcome, violates, required, scenario, observed)
F = {
 'NF-22': ('negative', 'ABORT-RO', [D1, D3], ['R-ABORT-RO', 'R-NO-MANAGE', 'R-GEN-MONOTONIC'],
           'snapshot G is the only generation and its format_version > R',
           'SchemaError from _version (engine.py:547-551) handled like damage (engine.py:1262); state.json moved aside, no backup -> '
           'failed_closed with ZERO lots (engine.py:1298) while the exchange holds BTCUSDT LONG 1.0 + stop; settings rewritten '
           'to persist the pause; ' + SENT),
 'NF-23': ('negative', 'ABORT-RO', [D1, D3], ['R-VERSION-TYPE', 'R-ABORT-RO', 'R-NO-MANAGE'],
           'every generation carries a non-integer version header (string, bool)',
           '"not a version number" (engine.py:549) is handled as damage: both files moved aside (engine.py:1294), failed_closed '
           'with ZERO lots; ' + SENT),
 'NF-24': ('negative', 'ABORT-RO', [D1, D4], ['R-VERSION-TYPE', 'R-ABORT-RO', 'R-BACKUP-CANDIDATE'],
           'primary header version is the float 1.0, backup is current',
           'float 1.0 rejected as damage (engine.py:549, 1262); primary moved aside and the OLDER .bak promoted to managed lots '
           '(engine.py:1283-1292, restored_from_backup); ' + SENT),
 'NF-25': ('negative', 'ABORT-RO', [D1, D3], ['R-VERSION-TYPE', 'R-ABORT-RO', 'R-NO-MANAGE'],
           'primary version null, backup version -1',
           'both rejected as damage (engine.py:549), both moved aside, failed_closed with ZERO lots; ' + SENT),
 'NF-26': ('negative', 'HOLD', [D2, D3, D4], ['R-EVIDENCE', 'R-NO-EMPTY', 'R-DURABLE-FAIL', 'R-RECON-GATE'],
           'snapshot G truncated, no other generation',
           'damaged file moved aside (engine.py:1263, quarantine engine.py:908), no backup -> failed_closed with ZERO lots; '
           'after two saves a clean empty state.json + .bak exist; ' + SENT),
 'NF-27': ('negative', 'HOLD', [D2, D3, D4], ['R-STRICT-PARSE', 'R-EVIDENCE', 'R-NO-EMPTY', 'R-RECON-GATE'],
           'snapshot G = complete valid document + 512 NUL bytes; G-1 all NUL (NTFS zero-fill after a crash)',
           'json.loads "Extra data" (engine.py:444-445) -> damage; both moved aside; failed_closed with ZERO lots; ' + SENT),
 'NF-28': ('negative', 'HOLD', [D2, D3, D4], ['R-STRICT-PARSE', 'R-EVIDENCE', 'R-NO-EMPTY', 'R-RECON-GATE'],
           'snapshot G all NUL, no other generation',
           'damage (engine.py:444-445), moved aside, failed_closed with ZERO lots; ' + SENT),
 'NF-29': ('negative', 'ABORT-RO', [D1, D2, D3], ['R-ABORT-RO', 'R-NO-MANAGE', 'R-GEN-MONOTONIC'],
           'snapshot G damaged (truncated) and G-1 has format_version > R: rule 1 wins over damage',
           'primary moved aside as damage, then the FUTURE .bak is rejected (engine.py:549) and moved aside too (engine.py:1294) '
           '- the newer-writer evidence is renamed away; failed_closed with ZERO lots; ' + SENT),
 'NF-30': ('negative', 'HOLD', [D2, D3, D4], ['R-STRICT-PARSE', 'R-BACKUP-CANDIDATE', 'R-RECON-GATE'],
           'snapshot G carries two "lots" members, the last one empty',
           'json.loads keeps the LAST duplicate (engine.py:444): an EMPTY portfolio loads with integrity={} and entries OPEN, no '
           'incident at all; the exchange position becomes untracked after pass 2; after two saves the .bak that still held '
           'the lot is overwritten (save_json engine.py:483-487); ' + SENT),
 'NF-31': ('negative', 'HOLD', [D2, D4], ['R-STRICT-PARSE', 'R-WRITE-VALIDATES', 'R-BACKUP-CANDIDATE'],
           'snapshot G has a NaN number in a lot field',
           'json.loads accepts NaN (engine.py:444) and lot.R is not validated (_validate_lot engine.py:568-590): loads clean, '
           'integrity={}, entries OPEN, the lot is managed with R=NaN; every later save fails because the strict writer refuses '
           'NaN (engine.py:418, _save_safe engine.py:1332-1336) -> state_untrusted, the entry is not sent (write-ahead refused), '
           'state.json is never saved again'),
 'NF-32': ('negative', 'HOLD', [D2, D4], ['R-STRICT-PARSE', 'R-BACKUP-CANDIDATE', 'R-RECON-GATE'],
           'snapshot G has a quantity integer of 401 digits',
           'start-up RAISES an uncaught OverflowError at engine.py:518 (math.isfinite on a huge int inside _num); no incident, '
           'no evidence, no file written - an accidental, uncontrolled abort that repeats on every start'),
 'NF-33': ('negative', 'HOLD', [D3, D4], ['R-IO-FAULT', 'R-NO-EMPTY', 'R-RECON-GATE'],
           'the snapshot path is a directory (layout), G-1 intact',
           'PermissionError(13) read -> "cannot be read" branch (engine.py:1264-1270): file held, ZERO lots, .bak NOT offered; '
           'position untracked after pass 2; resume allowed (install_block only); the entry was refused only because the '
           'write-ahead save failed (OSError)'),
 'NF-34': ('negative', 'HOLD', [D4], ['R-IO-FAULT', 'R-EVIDENCE', 'R-BACKUP-CANDIDATE', 'R-RECON-GATE'],
           'snapshot G truncated, G-1 intact, every store write/rename fails ENOSPC',
           'pause could not be persisted, damaged file left in place (save_hold unmovable, engine.py:1272-1279); the OLDER .bak '
           'is promoted to managed lots in memory (engine.py:1283-1292); new risk blocked only by persist_block '
           '(state_untrusted, engine.py:1366-1372); no durable incident record'),
 'NF-35': ('positive-control', 'HOLD', [], ['R-IO-FAULT'],
           'intact reconciled store, the store directory is read-only',
           'start-up needs no write; the first save fails -> state_untrusted -> persist_block refuses automatic AND manual '
           'entries (engine.py:1366-1372) - CORRECT (no intent can be made durable, nothing sent)'),
 'NF-36': ('negative', 'HOLD', [D4], ['R-UNCOMMITTED', 'R-RESULT-BEFORE-APPLY', 'R-RECON-GATE'],
           'committed G holds the lot; an uncommitted complete temp holds G+1 (lot closed); exchange flat',
           'the temp file is ignored and never reported; reconcile sees the stop id missing and books a STOP exit at the stop '
           'price 95.0, pnl -5.0 (engine.py:3223-3233) for a lot that was really closed by another path - guessed result '
           'applied from a position delta; ' + SENT),
 'NF-37': ('positive-control', 'MANAGE', [], ['R-UNCOMMITTED'],
           'intact reconciled store + a torn uncommitted temp',
           'the torn temp is ignored; the intact store is managed normally - CORRECT for the outcome (the temp is not reported '
           'either, which NC-02 must add)'),
 'NF-38': ('negative', 'HOLD', [D2, D4], ['R-EVIDENCE', 'R-BACKUP-CANDIDATE', 'R-RECON-GATE'],
           'snapshot G missing, evidence copy of G present, G-1 intact (crash after move-aside)',
           '"missing while its backup exists" (engine.py:1259) -> the OLDER .bak is promoted to managed lots; the existing '
           '.corrupt-* evidence is not linked (evidence=None); ' + SENT),
 'NF-39': ('negative', 'HOLD-INIT', [D3, D4], ['R-FIRST-RUN-FLAT', 'R-KNOWN-EMPTY', 'R-IDENTITY'],
           'first run never completed (no marker); exchange holds an unknown ETHUSDT SHORT 0.5 without a stop',
           'versioned settings without a marker -> install_block (engine.py:1136-1144) - correct so far; but confirm_install '
           '(engine.py:1211-1224) alone re-opens entries with an EMPTY portfolio while ETHUSDT SHORT 0.5 stays untracked; ' + SENT),
 'NF-40': ('negative', 'HOLD', [D2, D3, D4], ['R-WRITE-VALIDATES', 'R-INTENT-SHAPE', 'R-NO-EMPTY', 'R-RECON-GATE'],
           'the committed snapshot holds an intent with only a quantity',
           'save_state() accepted the qty-only pending record twice (no validation on write, engine.py:1806-1807 -> 1332), so '
           'primary AND .bak hold it; the reader rejects both (engine.py:582-585) -> both moved aside, failed_closed with ZERO '
           'lots; ' + SENT),
 'NF-41': ('negative', 'HOLD', [D4], ['R-INTENT-SHAPE', 'R-WRITE-VALIDATES', 'R-RESULT-BEFORE-APPLY'],
           'the committed snapshot holds a close intent with quantity 0',
           'qty 0 passes the schema (lo=0, engine.py:584); _resolve_pending treats it as FILLED from the position '
           '(engine.py:2259-2266): books a 0-qty "tp" exit and replaces the protective stop (stop + cancel sent, '
           'engine.py:2277)'),
 'NF-42': ('negative', 'HOLD', [D4], ['R-INTENT-SHAPE', 'R-WRITE-VALIDATES', 'R-STRICT-PARSE'],
           'the committed snapshot holds an add intent with quantity 1e300',
           'qty 1e300 passes the schema (finite, engine.py:584); saved and reloaded unchanged; the pending is dropped as '
           '"did not fill" by the position check (engine.py:2279-2280) - an impossible intent was persisted and acted on '
           'instead of being refused'),
 'NF-43': ('negative', 'HOLD', [D3, D4], ['R-KNOWN-EMPTY', 'R-MATCH', 'R-RECON-GATE'],
           'committed G is empty, G-1 owns a lot, no journaled result explains the transition; exchange flat',
           'the empty primary loads clean (integrity={}), entries OPEN, the .bak is never consulted, and after two saves the '
           '.bak that held the lot is overwritten (engine.py:483-487) - prior ownership erased without any record; ' + SENT),
 'NF-44': ('negative', 'HOLD', [D4], ['R-MATCH', 'R-NO-AUTO-CANCEL-FOREIGN', 'R-RECON-GATE'],
           'intact reconciled store; exchange additionally holds an unknown ETHUSDT SHORT 0.5 and a foreign stop o:99',
           'store loads clean, entries OPEN; ETHUSDT SHORT untracked only after pass 2 (engine.py:3268-3272); the foreign stop '
           'is left alone (verify_stops: reads only) - good; ' + SENT),
 'NF-45': ('negative', 'HOLD', [D4], ['R-MATCH', 'R-RESULT-BEFORE-APPLY', 'R-RECON-GATE'],
           'intact store 1.0 vs exchange 0.6 with the stop still sized 1.0',
           'after the 2-pass grace (engine.py:3241-3242) the lot is resized to 0.6 and a "resync" exit of 0.4 is BOOKED at the '
           'market price (engine.py:3258), and the stop is replaced (stop + cancel sent, engine.py:3260); entries stay OPEN'),
 'NF-46': ('negative', 'HOLD', [D4], ['R-IDENTITY', 'R-MATCH', 'R-RECON-GATE'],
           'marker binds ANOTHER account; positions equal the state (same-position / different-account attack)',
           'install_mismatch -> paused (engine.py:1180-1190) - correct; one confirm_install (engine.py:1211-1224) re-binds and '
           're-opens entries; no reconciliation record or snapshot-age check is required, position equality is taken as '
           'enough; ' + SENT),
 'NF-47': ('negative', 'HOLD', [D4], ['R-NOTFOUND-NOT-PROOF', 'R-RESULT-BEFORE-APPLY', 'R-RECON-GATE'],
           'committed close intent 0.5 with a client id; the exchange shows 0.5 left but the order lookup answers bare not-found',
           'bare not-found + age > 20 s deletes the pending as "never reached Binance" (engine.py:2248-2250); the next passes '
           'book a "resync" exit of 0.5 at the market price 100.0 instead of the real 101.0 fill (engine.py:3258) and replace '
           'the stop (engine.py:3260); ' + SENT),
 'NF-48': ('negative', 'HOLD', [D4], ['R-ROLLBACK-DETECT', 'R-GEN-MONOTONIC', 'R-RECON-GATE'],
           'whole store replaced by an older valid generation (lot 0.5) while the exchange and its stop hold 1.0',
           'no rollback detection: loads clean, entries OPEN; the 0.5 difference is only "untracked" after pass 2 '
           '(engine.py:3268-3272) while the stop still protects 1.0; ' + SENT),
 'NF-49': ('negative', 'HOLD-INIT', [D4], ['R-NO-LEGACY-IMPORT', 'R-FIRST-RUN-FLAT'],
           'a complete legacy data folder is present where NEWCORE starts (legacy import attempt); exchange non-flat',
           'legacy reads its own folder and manages it (MANAGE, entries open) - the behaviour NEWCORE must NOT inherit: under '
           'Codex ruling 10 these bytes are never imported'),
}

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out_e.txt'), encoding='utf-8') as fh:
    titles = {ln.split()[1]: ln.split('] ', 1)[1].strip() for ln in fh if ln.startswith('=== NF-')}
LAYOUT = {'NF-33': {'state.json': 'directory'}}
INJECT = {'NF-34': 'every write, create, rename and replace in the data folder raises OSError(ENOSPC)',
          'NF-35': 'every write, create, rename and replace in the data folder raises OSError(EROFS)'}
COWORK = {'NF-22': 'A06', 'NF-23': 'A08', 'NF-24': 'A08', 'NF-25': 'A08', 'NF-26': 'B05', 'NF-27': 'B06', 'NF-28': 'B07',
          'NF-29': 'B08', 'NF-30': 'B13', 'NF-31': 'B14', 'NF-32': 'B15', 'NF-33': 'C01', 'NF-34': 'C05', 'NF-35': 'C06/C07',
          'NF-36': 'D01', 'NF-37': 'D02', 'NF-38': 'D03', 'NF-39': 'D04', 'NF-40': 'E01/E05/E10', 'NF-41': 'E02', 'NF-42': 'E03',
          'NF-43': 'F05', 'NF-44': 'review-8 exchange shape', 'NF-45': 'review-2 match', 'NF-46': 'review-1 identity',
          'NF-47': 'Codex ruling: bare not-found', 'NF-48': 'Codex: generation rollback', 'NF-49': 'review-8 / ruling 10 legacy import'}


def sha(p):
    with open(p, 'rb') as fh: return hashlib.sha256(fh.read()).hexdigest()


def contract_sha(fx):
    c = {k: fx[k] for k in V2_FIELDS}
    return hashlib.sha256(json.dumps(c, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


ids = sorted(F)
assert not FROZEN & set(ids), 'refusing to touch a frozen fixture'
clash = [i for i in ids if os.path.exists(os.path.join(OUT, i)) or i in MAN['fixtures']]
if clash: sys.exit(f'refusing to overwrite existing fixtures: {clash}')
add = {}
for fid in ids:
    kind, outcome, violates, req, scenario, observed = F[fid]
    if outcome.startswith('HOLD') and 'R-HOLD' not in req: req = req + ['R-HOLD']
    sd = os.path.join(STAGE, fid)
    d = os.path.join(OUT, fid); os.makedirs(os.path.join(d, 'input'))
    files, mt = {}, {}
    for n in sorted(os.listdir(os.path.join(sd, 'input'))):
        shutil.copyfile(os.path.join(sd, 'input', n), os.path.join(d, 'input', n))
        files[n] = sha(os.path.join(d, 'input', n))
        mt[n] = BASE_MS + (1000 if n.endswith('.tmp') else 0)        # an orphan temp is newer than the committed files
    if not files: open(os.path.join(d, 'input', '.gitkeep'), 'w').close()
    with open(os.path.join(sd, 'exchange.json')) as fh: ex = json.load(fh)
    title = titles[fid]
    fx = dict(id=fid, title=title, contract='v2', kind=kind, cowork_ref=COWORK[fid], reader_build='N',
              source_repro='repro_e_nc02_ext.py (log: repro/out_e.txt, section "=== ' + fid + '")',
              input_files=files, input_mtimes_ms=mt, layout=LAYOUT.get(fid, {}), inject=INJECT.get(fid),
              exchange=ex, legacy_observed=dict(build='cff3f88', behaviour=observed), violates=violates,
              nc02_scenario=scenario, nc02_outcome=outcome, nc02_required={r: R_ALL[r] for r in req})
    fx['contract_sha'] = contract_sha(fx)
    with open(os.path.join(d, 'fixture.json'), 'w') as fh: json.dump(fx, fh, indent=2)
    add[fid] = fx['contract_sha']

with open(os.path.join(OUT, 'MANIFEST.json'), 'rb') as fh: assert fh.read() == MAN_RAW, 'MANIFEST changed underneath'
old = dict(MAN['fixtures'])
MAN['requirements'] = R_ALL
MAN['fixtures'].update(add)
MAN['contract_fields'] = {'v1': ['id', 'reader_build', 'input_files', 'exchange', 'nc02_required'], 'v2': list(V2_FIELDS)}
MAN['contract_version'] = dict({k: 'v1' for k in old}, **{k: 'v2' for k in add})
MAN['frozen'] = sorted(old)
MAN['positive_controls'] = ['NF-15', 'NF-21'] + [k for k in ids if F[k][0] == 'positive-control']
assert all(MAN['fixtures'][k] == v for k, v in old.items())
with open(os.path.join(OUT, 'MANIFEST.json'), 'w') as fh: json.dump(MAN, fh, indent=2)
print(f'{len(add)} fixtures added: {ids[0]}..{ids[-1]}')
