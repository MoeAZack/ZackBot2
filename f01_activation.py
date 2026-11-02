"""FACTORY-F01-ACT-01 (Codex ruling 6517762340): the authenticated PAPER/testnet-only activation path for the C-F01 canary.

The two-stage start of contracts/factory_f01_impl_01/COHORT_PACK.md, as three engine-locked steps that each go through
Engine.commit_settings (durable save first, memory only after it - a failed save leaves both untouched):

  stage     (a) write the immutable authorized F01_CANARY config (future start_utc, authorization id, absolute cohort_dir
                outside the repo, source_commit, source_manifest) while C-F01 stays DISABLED. Refused once any F01_CANARY
                value exists: the config is immutable and the start never moves.
  preflight (b) Engine.f01_canary_preflight(pins_expected, write=True) with the contract pins bundled in this build; the
                outcome (pass, record / extract sha256, config + C-F01 slot sha256) is stored as F01_CANARY_PREFLIGHT.
  enable    (c) C-F01 enabled = true only when the stored preflight PASSED for this exact config, C-F01 card and pins, its
                immutable extract verifies, a FRESH preflight (write=False, same lock, immediately before the commit)
                passes too, and start_utc is still strictly in the future. Otherwise nothing changes. The stored extract
                stays the activation evidence; the fresh re-check writes no second extract (Codex 6518282236 #1).

Generic /api/settings rejects both keys. Once F01_CANARY is staged the whole C-F01 card is frozen: /api/sleeves may only
perform the rollback enabled -> disabled with every other C-F01 field unchanged (sleeves_guard, Codex 6518282236 #3);
before staging it may not switch C-F01 on. So this module is the only writer that can switch C-F01 on.

A stored F01_CANARY that is malformed, OFF or otherwise not what stage writes can only come from corruption or a manual
edit: it is never replaced here and every step stays refused (fail closed). There are two bounded replacements below:
recover_stage covers the confirmed packaged-pins failure on a never-enabled stage (#254), while rearm creates a new,
disabled stage after an expired PASS stage was rolled back. Rearm needs a new authorization, start and cohort directory,
preserves the old evidence, and still requires the normal fresh preflight + enable sequence.

Nothing here starts a second Engine, patches a live file by hand, or touches strategy logic or a pinned function.
"""
import copy
import json
import os
from datetime import timezone

import engine as ENG
import f01_canary as FC

PREFLIGHT_KEY = 'F01_CANARY_PREFLIGHT'
RECOVERY_KEY = 'F01_CANARY_RECOVERY'
REARM_KEY = 'F01_CANARY_REARMS'
REARM_CHAIN_KEY = 'F01_CANARY_REARM_CHAIN'
ACTIVATION_KEY = 'F01_CANARY_ACTIVATIONS'
ACTIVATION_CHAIN_KEY = 'F01_CANARY_ACTIVATION_CHAIN'
OWNED_KEYS = (FC.CANARY_KEY, PREFLIGHT_KEY, RECOVERY_KEY, REARM_KEY, REARM_CHAIN_KEY,
              ACTIVATION_KEY, ACTIVATION_CHAIN_KEY)
CFG_FIELDS = ('start_utc', 'authorization_comment_id', 'cohort_dir', 'source_commit', 'source_manifest')
PINS_FILE = os.path.join('contracts', 'factory_f01_impl_01', 'cohort_manifest.v1.json')
ROUTE = '/api/f01_canary/'
ENABLE_HINT = 'C-F01 is switched on only by /api/f01_canary/enable after a PASS preflight'
HEX0 = '0' * 64
ACTIVATION_FIELDS = {'version', 'sequence', 'prev_event_sha256', 'config_sha256', 'preflight_record_sha256',
                     'preflight_extract_sha256', 'slot_sha256', 'enabled_at_utc', 'event_sha256'}
REARM_FIELDS = {'version', 'sequence', 'prev_audit_sha256', 'old_config_sha256', 'new_config_sha256',
                'old_authorization_comment_id', 'new_authorization_comment_id', 'old_cohort_dir', 'new_cohort_dir',
                'old_start_utc', 'new_start_utc', 'activation_proof_sha256', 'activation_genesis_config_sha256',
                'activation_count', 'activation_head_sha256', 'activation_migration_proof_sha256',
                'at_utc', 'reason', 'audit_sha256'}
CHAIN_FIELDS = {'version', 'genesis_config_sha256', 'current_config_sha256', 'count', 'head_sha256',
                'activation_genesis_config_sha256', 'activation_count', 'activation_head_sha256',
                'activation_migration_proof_sha256'}
ACTIVATION_CHAIN_FIELDS = {'version', 'genesis_config_sha256', 'staged_config_sha256', 'count', 'head_sha256',
                           'migration_proof_sha256'}
EXTERNAL_ANCHOR_FIELDS = {'version', 'sequence', 'prev_anchor_sha256', 'event', 'staged_config_sha256',
                          'activation_genesis_config_sha256', 'activation_count', 'activation_head_sha256',
                          'activation_migration_proof_sha256', 'rearm_head_sha256', 'pre_state_sha256',
                          'post_state_sha256', 'at_utc'}


def _env(e):
    why = FC.env_refusal(getattr(e, 'live', True), getattr(getattr(e, 'trade', None), 'base', None), ENG.TESTNET)
    if why: raise ValueError(why)


def _slot(S, sid=FC.SLOT):
    return next((x for x in (S or {}).get('SLEEVES') or () if isinstance(x, dict) and x.get('id') == sid), None)


def _sha(obj):
    return FC.sha256(FC.canonical(obj))


def _slot_sha(sl):
    """The C-F01 card as preflight saw it (enabled excluded: preflight requires it off, enable turns it on)."""
    return _sha({k: v for k, v in (sl or {}).items() if k != 'enabled'})


def _signed(rec, hash_field):
    return _sha({k: v for k, v in rec.items() if k != hash_field})


def _activation_record(cfg_hash, rec, sl, prior):
    out = dict(version=1, sequence=len(prior) + 1,
               prev_event_sha256=prior[-1]['event_sha256'] if prior else HEX0,
               config_sha256=cfg_hash, preflight_record_sha256=rec['record_sha256'],
               preflight_extract_sha256=rec['extract_sha256'], slot_sha256=_slot_sha(sl),
               enabled_at_utc=ENG.now_utc().astimezone(timezone.utc).isoformat(timespec='seconds'))
    out['event_sha256'] = _signed(out, 'event_sha256')
    return out


def _validated_activations(S, staged_hash, allow_uninitialized=False):
    rows = S.get(ACTIVATION_KEY, [])
    if not isinstance(rows, list): raise ValueError('the F01 activation audit is malformed - refused (fail closed)')
    prev = HEX0
    for i, row in enumerate(rows, 1):
        if (not isinstance(row, dict) or set(row) != ACTIVATION_FIELDS or row.get('version') != 1
                or row.get('sequence') != i):
            raise ValueError('the F01 activation audit is malformed - refused (fail closed)')
        hashes = ('prev_event_sha256', 'config_sha256', 'preflight_record_sha256', 'preflight_extract_sha256',
                  'slot_sha256', 'event_sha256')
        if (any(not isinstance(row.get(k), str) or not FC.HEX64.fullmatch(row[k]) for k in hashes)
                or row['prev_event_sha256'] != prev or FC._ts(row.get('enabled_at_utc')) is None
                or _signed(row, 'event_sha256') != row['event_sha256']):
            raise ValueError('the F01 activation audit is malformed - refused (fail closed)')
        prev = row['event_sha256']
    summary = S.get(ACTIVATION_CHAIN_KEY)
    absent = ACTIVATION_KEY not in S and summary is None
    rearm_anchor = S.get(REARM_CHAIN_KEY)
    if absent and allow_uninitialized and rearm_anchor is None:
        return rows, None
    if summary is None:
        raise ValueError('the F01 activation audit summary is missing - refused (fail closed)')
    if (not isinstance(summary, dict) or set(summary) != ACTIVATION_CHAIN_FIELDS or summary.get('version') != 1
            or not isinstance(summary.get('count'), int) or isinstance(summary.get('count'), bool)
            or summary['count'] != len(rows)
            or any(not isinstance(summary.get(k), str) or not FC.HEX64.fullmatch(summary[k]) for k in
                   ('genesis_config_sha256', 'staged_config_sha256', 'head_sha256', 'migration_proof_sha256'))
            or summary['staged_config_sha256'] != staged_hash or summary['head_sha256'] != prev):
        raise ValueError('the F01 activation audit summary is invalid - refused (fail closed)')
    if rows and summary['genesis_config_sha256'] != rows[0]['config_sha256']:
        raise ValueError('the F01 activation audit genesis is invalid - refused (fail closed)')
    if not rows and summary['head_sha256'] != HEX0:
        raise ValueError('the F01 activation audit summary is invalid - refused (fail closed)')
    if rearm_anchor is not None:
        if (not isinstance(rearm_anchor, dict)
                or summary['genesis_config_sha256'] != rearm_anchor.get('activation_genesis_config_sha256')
                or summary['count'] != rearm_anchor.get('activation_count')
                or summary['head_sha256'] != rearm_anchor.get('activation_head_sha256')
                or summary['migration_proof_sha256'] != rearm_anchor.get('activation_migration_proof_sha256')):
            raise ValueError('the F01 activation audit disagrees with the rearm anchor - refused (fail closed)')
    elif summary['migration_proof_sha256'] != HEX0:
        raise ValueError('the F01 activation migration proof has no validated rearm anchor - refused (fail closed)')
    return rows, summary


def _canonical_cfg(cfg):
    out = copy.deepcopy(cfg)
    out['authorization_comment_id'] = str(out['authorization_comment_id'])
    return out


def _anchor_dir(e):
    """Fixed runtime-data location: settings cannot redirect or hide the external write-once anchor chain."""
    return os.path.join(os.path.dirname(os.path.abspath(e.F['settings'])), 'f01_activation_anchors')


def _anchor_state_sha(S):
    sl = _slot(S)
    view = {k: copy.deepcopy((S or {}).get(k)) for k in
            (FC.CANARY_KEY, PREFLIGHT_KEY, REARM_KEY, REARM_CHAIN_KEY, ACTIVATION_KEY, ACTIVATION_CHAIN_KEY)}
    view['C-F01'] = copy.deepcopy(sl)
    return _sha(view)


def _external_anchors(e):
    d = _anchor_dir(e)
    rows = {}
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else ():
        if name == 'pending' and os.path.isdir(os.path.join(d, name)): continue
        if not isinstance(name, str) or not name.endswith('.json') or not FC.HEX64.fullmatch(name[:-5]):
            raise ValueError('the external F01 activation anchor store is malformed - refused (fail closed)')
        with open(os.path.join(d, name), 'rb') as f: raw = f.read()
        if FC.sha256(raw) != name[:-5]:
            raise ValueError('the external F01 activation anchor hash is invalid - refused (fail closed)')
        try: row = json.loads(raw)
        except (ValueError, TypeError):
            raise ValueError('the external F01 activation anchor record is malformed - refused (fail closed)')
        if (not isinstance(row, dict) or set(row) != EXTERNAL_ANCHOR_FIELDS or row.get('version') != 1
                or not isinstance(row.get('sequence'), int) or isinstance(row.get('sequence'), bool)
                or row['sequence'] <= 0 or row.get('event') not in ('enable', 'rearm')
                or FC._ts(row.get('at_utc')) is None
                or not isinstance(row.get('activation_count'), int) or isinstance(row.get('activation_count'), bool)
                or row['activation_count'] < 0
                or any(not isinstance(row.get(k), str) or not FC.HEX64.fullmatch(row[k]) for k in
                       ('prev_anchor_sha256', 'staged_config_sha256', 'activation_genesis_config_sha256',
                        'activation_head_sha256', 'activation_migration_proof_sha256', 'rearm_head_sha256',
                        'pre_state_sha256', 'post_state_sha256'))):
            raise ValueError('the external F01 activation anchor record is malformed - refused (fail closed)')
        rows[name[:-5]] = row
    if not rows: return [], None
    roots = [h for h, r in rows.items() if r['prev_anchor_sha256'] == HEX0]
    children = {}
    for h, r in rows.items():
        if r['prev_anchor_sha256'] != HEX0: children.setdefault(r['prev_anchor_sha256'], []).append(h)
    if len(roots) != 1 or any(len(v) != 1 for v in children.values()):
        raise ValueError('the external F01 activation anchor chain is forked or rootless - refused (fail closed)')
    ordered, seen, cur = [], set(), roots[0]
    while cur:
        if cur in seen or cur not in rows: raise ValueError('the external F01 activation anchor chain is invalid - refused')
        seen.add(cur); ordered.append((cur, rows[cur]))
        nxt = children.get(cur, [])
        cur = nxt[0] if nxt else None
    if len(seen) != len(rows) or any(r['sequence'] != i for i, (_h, r) in enumerate(ordered, 1)):
        raise ValueError('the external F01 activation anchor chain is incomplete or reordered - refused (fail closed)')
    return ordered, ordered[-1]


def _anchor_state(staged_hash, activation_chain, rearm_chain):
    return dict(staged_config_sha256=staged_hash,
                activation_genesis_config_sha256=activation_chain['genesis_config_sha256'],
                activation_count=activation_chain['count'], activation_head_sha256=activation_chain['head_sha256'],
                activation_migration_proof_sha256=activation_chain['migration_proof_sha256'],
                rearm_head_sha256=rearm_chain['head_sha256'] if rearm_chain else HEX0)


def _validate_external_anchor(e, staged_hash, activation_chain, rearm_chain, allow_absent=False):
    ordered, leaf = _external_anchors(e)
    if leaf is None:
        if allow_absent: return ordered
        raise ValueError('the external F01 activation anchor is missing - refused (fail closed)')
    _h, rec = leaf
    expected = _anchor_state(staged_hash, activation_chain, rearm_chain)
    if any(rec.get(k) != v for k, v in expected.items()):
        raise ValueError('the external F01 activation anchor disagrees with settings - refused (fail closed)')
    return ordered


def _prepare_external_anchor(e, event, staged_hash, activation_chain, rearm_chain, prior, pre_settings, post_settings):
    state = _anchor_state(staged_hash, activation_chain, rearm_chain)
    rec = dict(version=1, sequence=len(prior) + 1, prev_anchor_sha256=prior[-1][0] if prior else HEX0,
               event=event, pre_state_sha256=_anchor_state_sha(pre_settings),
               post_state_sha256=_anchor_state_sha(post_settings),
               at_utc=ENG.now_utc().astimezone(timezone.utc).isoformat(timespec='seconds'), **state)
    raw = FC.canonical(rec); h = FC.sha256(raw); d = os.path.join(_anchor_dir(e), 'pending'); os.makedirs(d, exist_ok=True)
    path = os.path.join(d, h + '.json')
    try:
        with open(path, 'xb') as f: f.write(raw)
    except FileExistsError:
        with open(path, 'rb') as f:
            if f.read() != raw: raise ValueError('the external F01 activation anchor is not immutable')
    return dict(hash=h, path=path, record=rec)


def _promote_external_anchor(e, tx):
    dst = os.path.join(_anchor_dir(e), tx['hash'] + '.json')
    os.replace(tx['path'], dst)
    try: os.rmdir(os.path.dirname(tx['path']))
    except OSError: pass


def _commit_with_anchor(e, ns, tx):
    try:
        e.commit_settings(ns)
    except Exception:
        try: os.remove(tx['path'])
        except OSError: pass
        try: os.rmdir(os.path.dirname(tx['path']))
        except OSError: pass
        raise
    _promote_external_anchor(e, tx)


def _reconcile_external_pending(e):
    d = os.path.join(_anchor_dir(e), 'pending')
    names = sorted(os.listdir(d)) if os.path.isdir(d) else []
    if not names: return None
    if len(names) != 1:
        raise ValueError('multiple pending F01 activation anchor transactions - refused (fail closed)')
    name = names[0]; path = os.path.join(d, name)
    if not name.endswith('.json') or not FC.HEX64.fullmatch(name[:-5]) or not os.path.isfile(path):
        raise ValueError('the pending F01 activation anchor is malformed - refused (fail closed)')
    with open(path, 'rb') as f: raw = f.read()
    if FC.sha256(raw) != name[:-5]: raise ValueError('the pending F01 activation anchor hash is invalid - refused')
    try: rec = json.loads(raw)
    except (ValueError, TypeError): raise ValueError('the pending F01 activation anchor is malformed - refused')
    if not isinstance(rec, dict) or set(rec) != EXTERNAL_ANCHOR_FIELDS:
        raise ValueError('the pending F01 activation anchor is malformed - refused')
    cur = _anchor_state_sha(e.S)
    if cur == rec.get('pre_state_sha256'):
        os.remove(path)
        try: os.rmdir(d)
        except OSError: pass
        return dict(event=rec.get('event'), committed=False)
    if cur == rec.get('post_state_sha256'):
        _promote_external_anchor(e, dict(hash=name[:-5], path=path, record=rec))
        return dict(event=rec.get('event'), committed=True)
    raise ValueError('pending F01 activation anchor matches neither durable settings state - refused (fail closed)')


def _activation_proof(e, cfg, cfg_hash, pf, sl):
    """Return a durable proof hash for an actual enable, or None. Current C-F01 must already be OFF.

    New builds persist an exact signed activation event in settings. The pre-existing first canary predates that event;
    its atomic settings backup is accepted only when it contains this exact config/PASS/card with enabled=true while the
    current exact card is disabled. No log line or mere expired PASS is sufficient.
    """
    rows, _summary = _validated_activations(e.S, cfg_hash, allow_uninitialized=True)
    for row in rows:
        if (row['config_sha256'] == cfg_hash and row['preflight_record_sha256'] == pf.get('record_sha256')
                and row['preflight_extract_sha256'] == pf.get('extract_sha256') and row['slot_sha256'] == _slot_sha(sl)):
            return row['event_sha256']
    try:
        with open(e.F['settings'] + '.bak', 'rb') as f: raw = f.read()
        old = json.loads(raw)
        old_sl = _slot(old)
        old_pf = old.get(PREFLIGHT_KEY)
        if (_sha(old.get(FC.CANARY_KEY)) == cfg_hash and old_sl and old_sl.get('enabled') is True
                and _slot_sha(old_sl) == _slot_sha(sl) and isinstance(old_pf, dict) and old_pf == pf
                and old_pf.get('passed') is True):
            return FC.sha256(raw)
    except (OSError, ValueError, TypeError):
        pass
    return None


def _validated_rearms(S, current_hash):
    """Validate the exact versioned audit schema, hash chain, summary head and current-config linkage."""
    rows, chain = S.get(REARM_KEY, []), S.get(REARM_CHAIN_KEY)
    if not isinstance(rows, list): raise ValueError('the F01 rearm audit is malformed - refused (fail closed)')
    prev, genesis, prior_new = HEX0, current_hash, None
    seen_auth, seen_dirs, prior_auth, prior_dir = set(), set(), None, None
    activation_genesis = activation_migration = None
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) != REARM_FIELDS or row.get('version') != 1 or row.get('sequence') != i:
            raise ValueError('the F01 rearm audit schema is invalid - refused (fail closed)')
        hashes = ('prev_audit_sha256', 'old_config_sha256', 'new_config_sha256', 'activation_proof_sha256',
                  'activation_genesis_config_sha256', 'activation_head_sha256',
                  'activation_migration_proof_sha256', 'audit_sha256')
        if any(not isinstance(row.get(k), str) or not FC.HEX64.fullmatch(row[k]) for k in hashes):
            raise ValueError('the F01 rearm audit schema is invalid - refused (fail closed)')
        if (row['prev_audit_sha256'] != prev or (prior_new is not None and row['old_config_sha256'] != prior_new)
                or _signed(row, 'audit_sha256') != row['audit_sha256']):
            raise ValueError('the F01 rearm audit hash chain is invalid - refused (fail closed)')
        auths = (row.get('old_authorization_comment_id'), row.get('new_authorization_comment_id'))
        dirs = (row.get('old_cohort_dir'), row.get('new_cohort_dir'))
        norm_dirs = tuple(os.path.normcase(os.path.realpath(x)) if isinstance(x, str) else None for x in dirs)
        times = (row.get('old_start_utc'), row.get('new_start_utc'), row.get('at_utc'))
        if (any(not isinstance(x, str) or not x.isdigit() or int(x) <= 0 for x in auths)
                or auths[0] == auths[1] or any(not isinstance(x, str) or not os.path.isabs(x) for x in dirs)
                or norm_dirs[0] == norm_dirs[1]
                or any(FC._ts(x) is None for x in times) or row.get('reason') != 'owner_authorized_rearm_after_disable'
                or row['old_config_sha256'] == row['new_config_sha256']
                or not isinstance(row.get('activation_count'), int) or isinstance(row.get('activation_count'), bool)
                or row['activation_count'] < 0
                or (row['activation_count'] == 0 and row['activation_head_sha256'] != HEX0)):
            raise ValueError('the F01 rearm audit schema is invalid - refused (fail closed)')
        if i == 1:
            activation_genesis = row['activation_genesis_config_sha256']
            activation_migration = row['activation_migration_proof_sha256']
            if row['activation_count'] == 0 and activation_migration != row['activation_proof_sha256']:
                raise ValueError('the F01 rearm audit legacy migration proof is invalid - refused (fail closed)')
        elif (row['activation_genesis_config_sha256'] != activation_genesis
              or row['activation_migration_proof_sha256'] != activation_migration):
            raise ValueError('the F01 rearm audit activation anchor changed - refused (fail closed)')
        if i == 1:
            seen_auth.add(auths[0]); seen_dirs.add(norm_dirs[0])
        elif auths[0] != prior_auth or norm_dirs[0] != prior_dir:
            raise ValueError('the F01 rearm audit history linkage is invalid - refused (fail closed)')
        if auths[1] in seen_auth:
            raise ValueError('the F01 rearm audit reuses a historical authorization id - refused (fail closed)')
        if norm_dirs[1] in seen_dirs:
            raise ValueError('the F01 rearm audit reuses a historical cohort directory - refused (fail closed)')
        seen_auth.add(auths[1]); seen_dirs.add(norm_dirs[1])
        prior_auth, prior_dir = auths[1], norm_dirs[1]
        if i == 1: genesis = row['old_config_sha256']
        prev, prior_new = row['audit_sha256'], row['new_config_sha256']
    if rows and prior_new != current_hash:
        raise ValueError('the F01 rearm audit does not link to the current staged config - refused (fail closed)')
    current_cfg = S.get(FC.CANARY_KEY) if isinstance(S.get(FC.CANARY_KEY), dict) else {}
    if rows and (str(current_cfg.get('authorization_comment_id')) != prior_auth
                 or os.path.normcase(os.path.realpath(current_cfg.get('cohort_dir', ''))) != prior_dir):
        raise ValueError('the F01 rearm audit history does not match the current authorization/cohort - refused (fail closed)')
    expected = dict(version=1, genesis_config_sha256=genesis, current_config_sha256=current_hash,
                    count=len(rows), head_sha256=prev)
    if rows:
        if (not isinstance(chain, dict) or set(chain) != CHAIN_FIELDS
                or any(chain.get(k) != v for k, v in expected.items())
                or not isinstance(chain.get('activation_count'), int) or isinstance(chain.get('activation_count'), bool)
                or chain['activation_count'] < 0
                or any(not isinstance(chain.get(k), str) or not FC.HEX64.fullmatch(chain[k]) for k in
                       ('activation_genesis_config_sha256', 'activation_head_sha256',
                        'activation_migration_proof_sha256'))):
            raise ValueError('the F01 rearm audit summary is missing or invalid - refused (fail closed)')
        last = rows[-1]
        if (chain['activation_genesis_config_sha256'] != activation_genesis
                or chain['activation_migration_proof_sha256'] != activation_migration
                or chain['activation_count'] != last['activation_count']
                or chain['activation_head_sha256'] != last['activation_head_sha256']):
            raise ValueError('the F01 rearm activation anchor is invalid - refused (fail closed)')
    elif chain is not None:
        raise ValueError('the F01 rearm audit summary exists without its history - refused (fail closed)')
    return rows, chain if rows else expected


def _staged(e):
    cfg = (e.S or {}).get(FC.CANARY_KEY)
    if cfg is None: raise ValueError('F01 canary is not staged - run /api/f01_canary/stage first')
    if not isinstance(cfg, dict) or cfg.get('on') is not True or FC.cfg_problems(cfg):
        raise ValueError('the staged F01 canary config is malformed - refused (fail closed)')
    return cfg


def bundled_pins(root=None):
    """The contract's pins_expected as bundled with this build (cohort_manifest.v1.json); never taken from a request."""
    p = os.path.join(root or FC.REPO_ROOT, PINS_FILE)
    try:
        with open(p, encoding='utf-8') as f: pins = json.load(f)['pins_expected']
    except (OSError, ValueError, KeyError, TypeError) as ex:
        raise ValueError(f'the contract pins are not bundled with this build ({type(ex).__name__}) - preflight refused')
    real = {k: v for k, v in pins.items() if not str(k).startswith('_')} if isinstance(pins, dict) else {}
    if not real or not all(isinstance(v, str) and FC.HEX64.fullmatch(v) for v in real.values()):
        raise ValueError('the bundled contract pins are malformed - preflight refused')
    return pins


def stage(e, b):
    """(a) Validate and commit the immutable authorized config. C-F01 must exist and be disabled; the start must be a
    tz-aware time strictly in the future. Returns the stored config and its sha256."""
    if not isinstance(b, dict): raise ValueError('bad request')
    extra = sorted(set(b) - set(CFG_FIELDS) - {'on'})
    if extra: raise ValueError('unknown F01 canary field(s): ' + ', '.join(str(k)[:30] for k in extra)[:120])
    if 'on' in b and b['on'] is not True: raise ValueError('"on" must be the JSON boolean true')
    missing = [k for k in CFG_FIELDS if k not in b]
    if missing: raise ValueError('missing F01 canary field(s): ' + ', '.join(missing))
    if not isinstance(b['start_utc'], str): raise ValueError('start_utc must be a tz-aware ISO UTC string')
    cfg = dict(on=True, **{k: copy.deepcopy(b[k]) for k in CFG_FIELDS})
    bad = FC.cfg_problems(cfg)
    if bad: raise ValueError('F01 canary config refused: ' + '; '.join(bad))
    cfg = _canonical_cfg(cfg)
    with e.lock:
        _env(e)
        if any(k in e.S for k in OWNED_KEYS):
            raise ValueError('F01 canary already staged - the config is immutable and its start never moves')
        sl = _slot(e.S)
        if sl is None: raise ValueError('C-F01 slot not present - nothing to stage')
        if sl.get('enabled') is not False: raise ValueError('C-F01 must be disabled while the canary is staged (two-stage start)')
        start = FC._ts(cfg['start_utc'])
        if not start > ENG.now_utc(): raise ValueError('start_utc must be strictly in the future')
        ns = copy.deepcopy(e.S)
        ns[FC.CANARY_KEY] = cfg
        e.commit_settings(ns)                       # durable first; on failure nothing is applied
    ENG.log.info(f"FACTORY-F01 canary staged: start {cfg['start_utc']}, authorization {cfg['authorization_comment_id']}")
    return dict(staged=True, config=copy.deepcopy(cfg), config_sha256=_sha(cfg))


def _owned(x, key=None):
    """Whether a durable/current candidate record belongs to C-F01."""
    return ENG.candidate_owner(x, key) == FC.SLOT


def _at_or_after_start(row, start):
    """A candidate row is attributable to this staged config only from its immutable start onward."""
    for field in ('opened', 'decision_bar', 'closed'):
        t = FC._ts((row or {}).get(field))
        if t is not None: return t >= start
    return True                         # an owned record with unreadable time fails closed


def _ever_enabled(e, cfg):
    """Return durable/current proof that C-F01 was enabled under cfg, else None.

    A successful enable normally leaves its PASS preflight (checked separately). These candidate/cohort records are the
    independent durable proof required by the recovery contract, including activity which survived history trimming.
    """
    st, start = e.state or {}, FC._ts(cfg.get('start_utc'))
    if (st.get('candidate_disabled') or {}).get(FC.SLOT) is not None:
        return 'durable candidate_disabled record'
    for family in ('lots', 'pending_entries', 'resting_entries', 'unconfirmed_entries'):
        vals = (st.get(family) or {}).items() if isinstance(st.get(family), dict) else enumerate(st.get(family) or ())
        if any(_owned(v, k) for k, v in vals): return f'durable {family} record'
    idx = st.get(ENG.CANDIDATE_PENDING_KEY)
    if not isinstance(idx, dict) and idx is not None: return 'unreadable durable candidate pending index'
    if any(_owned(v, k) for k, v in (idx or {}).items()): return 'durable candidate pending record'
    for name, rows in (('history', getattr(e, 'history', None)), ('cohort', getattr(e, 'cohort_rows', None))):
        if not isinstance(rows, list):
            if rows is not None: return f'unreadable durable {name} records'
            continue
        if any(_owned(r) and _at_or_after_start(r, start) for r in rows): return f'durable {name} C-F01 record'
    trade_dir = os.path.join(cfg['cohort_dir'], 'extracts', 'trade')
    if os.path.lexists(trade_dir) and not os.path.isdir(trade_dir):
        return 'unreadable durable cohort trade records (not a directory)'
    try:
        rows = FC.ExtractWriter(cfg['cohort_dir']).records('trade')
    except (OSError, ValueError, TypeError) as ex:
        return f'unreadable durable cohort trade records ({type(ex).__name__})'
    if any((r or {}).get('slot') == FC.SLOT and (r or {}).get('kind') in ('entry', 'trade')
           and _at_or_after_start(r, start) for r in rows):
        return 'durable cohort trade extract'
    return None


def recover_stage(e, b):
    """Replace only a failed, never-enabled immutable stage after the packaged-pins defect (#254)."""
    if not isinstance(b, dict): raise ValueError('bad request')
    fields = {'config_sha256', *CFG_FIELDS}
    extra = sorted(set(b) - fields)
    if extra: raise ValueError('unknown F01 recovery field(s): ' + ', '.join(str(k)[:30] for k in extra)[:120])
    missing = [k for k in ('config_sha256', *CFG_FIELDS) if k not in b]
    if missing: raise ValueError('missing F01 recovery field(s): ' + ', '.join(missing))
    old_hash = b['config_sha256']
    if not isinstance(old_hash, str) or not FC.HEX64.fullmatch(old_hash):
        raise ValueError('config_sha256 must be the exact 64-hex old config hash')
    if not isinstance(b['start_utc'], str): raise ValueError('start_utc must be a tz-aware ISO UTC string')
    new_cfg = dict(on=True, **{k: copy.deepcopy(b[k]) for k in CFG_FIELDS})
    bad = FC.cfg_problems(new_cfg)
    if bad: raise ValueError('F01 canary recovery config refused: ' + '; '.join(bad))
    new_cfg = _canonical_cfg(new_cfg)
    with e.lock:
        _env(e)
        old_cfg = _staged(e)
        if old_hash != _sha(old_cfg): raise ValueError('config_sha256 does not match the exact staged config')
        sl = _slot(e.S)
        if sl is None: raise ValueError('C-F01 slot not present - nothing to recover')
        if sl.get('enabled') is not False: raise ValueError('C-F01 is enabled - recovery refused')
        pf = e.S.get(PREFLIGHT_KEY)
        if isinstance(pf, dict) and pf.get('passed') is True:
            raise ValueError('a PASS preflight is stored - recovery refused')
        prior = e.S.get(RECOVERY_KEY, [])
        if not isinstance(prior, list):
            raise ValueError('the F01 recovery audit is malformed - refused (fail closed)')
        if prior:
            raise ValueError('the one-time F01 stage recovery was already used - recovery refused')
        proof = _ever_enabled(e, old_cfg)
        if proof: raise ValueError(f'C-F01 was ever enabled under the staged config ({proof}) - recovery refused')
        if new_cfg['authorization_comment_id'] == old_cfg.get('authorization_comment_id'):
            raise ValueError('a new Codex authorization comment id is required')
        if os.path.realpath(new_cfg['cohort_dir']) == os.path.realpath(old_cfg.get('cohort_dir', '')):
            raise ValueError('a new cohort directory is required')
        if not FC._ts(new_cfg['start_utc']) > ENG.now_utc():
            raise ValueError('start_utc must be strictly in the future')
        new_hash = _sha(new_cfg)
        if new_hash == old_hash: raise ValueError('the replacement config must be new, not a start-only retry')
        audit = dict(old_config_sha256=old_hash, new_config_sha256=new_hash,
                     old_authorization_comment_id=str(old_cfg['authorization_comment_id']),
                     new_authorization_comment_id=new_cfg['authorization_comment_id'],
                     at_utc=ENG.now_utc().astimezone(timezone.utc).isoformat(timespec='seconds'),
                     reason='packaged_pins_missing')
        ns = copy.deepcopy(e.S)
        ns[FC.CANARY_KEY] = new_cfg
        ns.pop(PREFLIGHT_KEY, None)                 # only a non-PASS value can reach here
        ns[RECOVERY_KEY] = copy.deepcopy(prior) + [audit]
        e.commit_settings(ns)                       # one durable atomic replacement; memory changes only after success
    ENG.log.info(f"FACTORY-F01 stage recovered: {old_hash} -> {new_hash}, authorization {new_cfg['authorization_comment_id']}")
    return dict(recovered=True, config=copy.deepcopy(new_cfg), config_sha256=new_hash, audit=copy.deepcopy(audit))


def rearm(e, b):
    """Replace an expired, previously preflighted stage after C-F01 was rolled back or otherwise switched OFF.

    This is deliberately a *stage-only* operation.  It never enables the slot.  A new authorization, cohort directory,
    future start, installed-build identity and the normal preflight + enable steps are still required.  The old cohort is
    left untouched and an append-only settings audit links the old and new config hashes.
    """
    if not isinstance(b, dict): raise ValueError('bad request')
    fields = {'config_sha256', *CFG_FIELDS}
    extra = sorted(set(b) - fields)
    if extra: raise ValueError('unknown F01 rearm field(s): ' + ', '.join(str(k)[:30] for k in extra)[:120])
    missing = [k for k in ('config_sha256', *CFG_FIELDS) if k not in b]
    if missing: raise ValueError('missing F01 rearm field(s): ' + ', '.join(missing))
    old_hash = b['config_sha256']
    if not isinstance(old_hash, str) or not FC.HEX64.fullmatch(old_hash):
        raise ValueError('config_sha256 must be the exact 64-hex old config hash')
    if not isinstance(b['start_utc'], str): raise ValueError('start_utc must be a tz-aware ISO UTC string')
    new_cfg = dict(on=True, **{k: copy.deepcopy(b[k]) for k in CFG_FIELDS})
    bad = FC.cfg_problems(new_cfg)
    if bad: raise ValueError('F01 canary rearm config refused: ' + '; '.join(bad))
    new_cfg = _canonical_cfg(new_cfg)
    with e.lock:
        _env(e)
        recovered_tx = _reconcile_external_pending(e)
        if recovered_tx and recovered_tx.get('committed') and recovered_tx.get('event') == 'rearm':
            cfg = _staged(e)
            return dict(rearmed=True, recovered=True, config=copy.deepcopy(cfg), config_sha256=_sha(cfg))
        old_cfg = _staged(e)
        if old_hash != _sha(old_cfg): raise ValueError('config_sha256 does not match the exact staged config')
        sl = _slot(e.S)
        if sl is None: raise ValueError('C-F01 slot not present - nothing to rearm')
        if sl.get('enabled') is not False: raise ValueError('C-F01 is enabled - rearm refused')
        old_pf = e.S.get(PREFLIGHT_KEY)
        if not isinstance(old_pf, dict) or old_pf.get('passed') is not True:
            raise ValueError('the old stage has no PASS preflight - rearm refused')
        if old_pf.get('config_sha256') != old_hash or old_pf.get('slot_sha256') != _slot_sha(sl):
            raise ValueError('the old PASS preflight does not match the exact config and C-F01 card - rearm refused')
        if not _extract_ok(old_cfg, old_pf.get('extract_sha256')):
            raise ValueError('the old PASS preflight extract is missing or altered - rearm refused')
        enabled_proof = _activation_proof(e, old_cfg, old_hash, old_pf, sl)
        if enabled_proof is None:
            raise ValueError('no durable proof that the old staged canary was actually enabled then disabled - rearm refused')
        if FC._ts(old_cfg['start_utc']) > ENG.now_utc():
            raise ValueError('the old frozen start is still in the future - use the normal enable path')
        prior, chain = _validated_rearms(e.S, old_hash)
        used_auth = {str(old_cfg.get('authorization_comment_id'))}
        used_dirs = {os.path.normcase(os.path.realpath(old_cfg.get('cohort_dir', '')))}
        for row in prior:
            used_auth.update((row['old_authorization_comment_id'], row['new_authorization_comment_id']))
            used_dirs.update(os.path.normcase(os.path.realpath(row[k])) for k in ('old_cohort_dir', 'new_cohort_dir'))
        if new_cfg['authorization_comment_id'] in used_auth:
            raise ValueError('a never-before-used Codex authorization comment id is required across the full rearm history')
        if os.path.normcase(os.path.realpath(new_cfg['cohort_dir'])) in used_dirs:
            raise ValueError('a never-before-used cohort directory is required across the full rearm history')
        if not FC._ts(new_cfg['start_utc']) > ENG.now_utc():
            raise ValueError('start_utc must be strictly in the future')

        # The replacement is allowed only at a clean family boundary.  Other PAPER lots may remain open and are captured
        # by the subsequent full preflight, but neither candidate may have risk, working orders or unresolved accounting.
        check = FC.preflight(e.S, e.state, live=getattr(e, 'live', True),
                             base=getattr(getattr(e, 'trade', None), 'base', None), testnet=ENG.TESTNET,
                             deferred=getattr(e, '_deferred', None) or (), drift=[], now=ENG.now_utc())['checks']
        failed = [k for k in ('C1', 'C2', 'C3', 'C4') if not check[k]['ok']]
        if failed: raise ValueError('C-F01 rearm requires a clean candidate boundary (' + ', '.join(failed) + ')')

        new_hash = _sha(new_cfg)
        activation_rows, activation_chain = _validated_activations(e.S, old_hash, allow_uninitialized=True)
        if activation_chain is None:
            external_prior, external_leaf = _external_anchors(e)
            if external_leaf is not None:
                raise ValueError('external activation history exists without its settings chain - rearm refused')
            activation_chain = dict(version=1, genesis_config_sha256=old_hash, staged_config_sha256=new_hash,
                                    count=0, head_sha256=HEX0, migration_proof_sha256=enabled_proof)
        else:
            external_prior = _validate_external_anchor(e, old_hash, activation_chain, chain if prior else None)
            activation_chain = dict(copy.deepcopy(activation_chain), staged_config_sha256=new_hash)
            if not activation_rows:
                activation_chain['migration_proof_sha256'] = enabled_proof
        audit = dict(version=1, sequence=len(prior) + 1, prev_audit_sha256=chain['head_sha256'],
                     old_config_sha256=old_hash, new_config_sha256=new_hash,
                     old_authorization_comment_id=str(old_cfg['authorization_comment_id']),
                     new_authorization_comment_id=new_cfg['authorization_comment_id'],
                     old_cohort_dir=old_cfg['cohort_dir'], new_cohort_dir=new_cfg['cohort_dir'],
                     old_start_utc=old_cfg['start_utc'], new_start_utc=new_cfg['start_utc'],
                     activation_proof_sha256=enabled_proof,
                     activation_genesis_config_sha256=activation_chain['genesis_config_sha256'],
                     activation_count=activation_chain['count'], activation_head_sha256=activation_chain['head_sha256'],
                     activation_migration_proof_sha256=activation_chain['migration_proof_sha256'],
                     at_utc=ENG.now_utc().astimezone(timezone.utc).isoformat(timespec='seconds'),
                     reason='owner_authorized_rearm_after_disable')
        audit['audit_sha256'] = _signed(audit, 'audit_sha256')
        new_chain = dict(version=1, genesis_config_sha256=chain['genesis_config_sha256'],
                         current_config_sha256=new_hash, count=len(prior) + 1, head_sha256=audit['audit_sha256'],
                         activation_genesis_config_sha256=activation_chain['genesis_config_sha256'],
                         activation_count=activation_chain['count'], activation_head_sha256=activation_chain['head_sha256'],
                         activation_migration_proof_sha256=activation_chain['migration_proof_sha256'])
        ns = copy.deepcopy(e.S)
        ns[FC.CANARY_KEY] = new_cfg
        ns.pop(PREFLIGHT_KEY, None)
        ns[REARM_KEY] = copy.deepcopy(prior) + [audit]
        ns[REARM_CHAIN_KEY] = new_chain
        ns[ACTIVATION_CHAIN_KEY] = activation_chain
        tx = _prepare_external_anchor(e, 'rearm', new_hash, activation_chain, new_chain, external_prior, e.S, ns)
        _commit_with_anchor(e, ns, tx)
    ENG.log.info(f"FACTORY-F01 rearmed as a disabled stage: {old_hash} -> {new_hash}, "
                 f"authorization {new_cfg['authorization_comment_id']}")
    return dict(rearmed=True, config=copy.deepcopy(new_cfg), config_sha256=new_hash, audit=copy.deepcopy(audit),
                audit_chain=copy.deepcopy(new_chain))


def preflight(e, b=None):
    """(b) Run the contract preflight with write=True and store its outcome. A FAIL is stored too (it replaces any earlier
    record, so enable needs the latest preflight to have passed). Returns pass, both sha256 and the full record."""
    pins = bundled_pins()
    with e.lock:
        _env(e)
        cfg = _staged(e)
        sl = _slot(e.S)
        if sl is None: raise ValueError('C-F01 slot not present')
        if sl.get('enabled') is not False: raise ValueError('C-F01 is already enabled - preflight runs only while it is disabled')
        pf = e.f01_canary_preflight(pins_expected=pins, write=True)
        ok = pf.get('pass') is True and isinstance(pf.get('extract_sha256'), str) and bool(FC.HEX64.fullmatch(pf['extract_sha256']))
        rec = dict(passed=ok, record_sha256=pf.get('record_sha256'), extract_sha256=pf.get('extract_sha256'),
                   config_sha256=_sha(cfg), slot_sha256=_slot_sha(sl), pins_sha256=_sha(pins),
                   at_utc=ENG.now_utc().isoformat(timespec='seconds'))
        ns = copy.deepcopy(e.S)
        ns[PREFLIGHT_KEY] = rec
        e.commit_settings(ns)
    ENG.log.info(f"FACTORY-F01 canary preflight {'PASS' if ok else 'FAIL'}: record {rec['record_sha256']}")
    out = {'pass': ok, 'record_sha256': rec['record_sha256'], 'extract_sha256': rec['extract_sha256'], 'record': pf}
    if pf.get('pass') is True and not ok: out['why'] = 'preflight passed but its immutable extract was not written - not usable'
    return out


def _extract_ok(cfg, h):
    """The stored preflight extract exists, matches its sha256 name and records a PASS."""
    try:
        with open(os.path.join(cfg['cohort_dir'], 'extracts', 'preflight', f'{h}.json'), 'rb') as f: raw = f.read()
        return FC.sha256(raw) == h and json.loads(raw).get('pass') is True
    except (OSError, ValueError, AttributeError):
        return False


def enable(e, b=None):
    """(c) Switch C-F01 on - only after a stored PASS for this exact config and card, with a verified extract, before the
    frozen start. Any refusal leaves C-F01 disabled and settings unchanged."""
    with e.lock:
        _env(e)
        recovered_tx = _reconcile_external_pending(e)
        if recovered_tx and recovered_tx.get('committed') and recovered_tx.get('event') == 'enable':
            cfg = _staged(e)
            return dict(enabled=True, recovered=True, start_utc=cfg['start_utc'])
        cfg = _staged(e)
        rec = e.S.get(PREFLIGHT_KEY)
        if not isinstance(rec, dict) or rec.get('passed') is not True:
            raise ValueError('no PASS preflight stored - C-F01 stays disabled')
        if rec.get('config_sha256') != _sha(cfg): raise ValueError('the stored preflight is for another config - C-F01 stays disabled')
        sl = _slot(e.S)
        if sl is None: raise ValueError('C-F01 slot not present')
        if sl.get('enabled') is not False: raise ValueError('C-F01 is already enabled')
        if rec.get('slot_sha256') != _slot_sha(sl):
            raise ValueError('the C-F01 card changed since the PASS preflight - run preflight again; C-F01 stays disabled')
        if not _extract_ok(cfg, rec.get('extract_sha256')):
            raise ValueError('the PASS preflight extract is missing or altered - C-F01 stays disabled')
        if not FC._ts(cfg['start_utc']) > ENG.now_utc():
            raise ValueError('the frozen start_utc has passed - enabling now is refused (the start never moves)')
        pins = bundled_pins()
        if rec.get('pins_sha256') != _sha(pins):
            raise ValueError('the bundled pins changed since the PASS preflight - C-F01 stays disabled')
        fresh = e.f01_canary_preflight(pins_expected=pins, write=False)      # dynamic facts may have moved since the PASS
        if fresh.get('pass') is not True:
            bad = ', '.join(k for k, v in (fresh.get('checks') or {}).items() if not v.get('ok'))
            raise ValueError(f'the fresh preflight fails ({bad or "unknown"}) - C-F01 stays disabled')
        rearm_chain = None
        if REARM_KEY in e.S or REARM_CHAIN_KEY in e.S:
            _rows, rearm_chain = _validated_rearms(e.S, _sha(cfg))
        ns = copy.deepcopy(e.S)
        _slot(ns)['enabled'] = True
        activations, activation_chain = _validated_activations(e.S, _sha(cfg), allow_uninitialized=True)
        if activation_chain is None:
            external_prior, external_leaf = _external_anchors(e)
            if external_leaf is not None:
                raise ValueError('external activation history exists without its settings chain - C-F01 stays disabled')
        else:
            external_prior = _validate_external_anchor(e, _sha(cfg), activation_chain, rearm_chain)
        event = _activation_record(_sha(cfg), rec, sl, activations)
        ns[ACTIVATION_KEY] = copy.deepcopy(activations) + [event]
        ns[ACTIVATION_CHAIN_KEY] = dict(
            version=1,
            genesis_config_sha256=(activation_chain['genesis_config_sha256'] if activation_chain else _sha(cfg)),
            staged_config_sha256=_sha(cfg), count=len(activations) + 1, head_sha256=event['event_sha256'],
            migration_proof_sha256=(activation_chain['migration_proof_sha256'] if activation_chain else HEX0))
        if rearm_chain is not None:
            rearm_rows = copy.deepcopy(ns[REARM_KEY])
            rearm_rows[-1]['activation_count'] = ns[ACTIVATION_CHAIN_KEY]['count']
            rearm_rows[-1]['activation_head_sha256'] = ns[ACTIVATION_CHAIN_KEY]['head_sha256']
            rearm_rows[-1]['audit_sha256'] = _signed(rearm_rows[-1], 'audit_sha256')
            ns[REARM_KEY] = rearm_rows
            ns[REARM_CHAIN_KEY] = dict(copy.deepcopy(rearm_chain), head_sha256=rearm_rows[-1]['audit_sha256'],
                activation_genesis_config_sha256=ns[ACTIVATION_CHAIN_KEY]['genesis_config_sha256'],
                activation_count=ns[ACTIVATION_CHAIN_KEY]['count'],
                activation_head_sha256=ns[ACTIVATION_CHAIN_KEY]['head_sha256'],
                activation_migration_proof_sha256=ns[ACTIVATION_CHAIN_KEY]['migration_proof_sha256'])
        tx = _prepare_external_anchor(e, 'enable', _sha(cfg), ns[ACTIVATION_CHAIN_KEY], ns.get(REARM_CHAIN_KEY),
                                      external_prior, e.S, ns)
        _commit_with_anchor(e, ns, tx)
    ENG.log.info(f"FACTORY-F01 canary: C-F01 enabled after PASS preflight {rec['record_sha256']} (start {cfg['start_utc']})")
    return dict(enabled=True, start_utc=cfg['start_utc'], record_sha256=rec['record_sha256'])


def sleeves_guard(S, new_sleeves, normalize):
    """For /api/sleeves (called under e.lock with the validated list). Not staged: C-F01 may not be switched on. Staged:
    the whole C-F01 card is frozen - the posted C-F01 must equal normalize(stored card) on every field but `enabled`, and
    `enabled` may only stay as it is or go True -> False (the rollback). Returns the list to store, with C-F01 replaced
    by an exact copy of the stored card (enabled as posted) so a permitted rollback never rewrites another field."""
    cur, new = _slot(S), _slot({'SLEEVES': new_sleeves})
    if FC.CANARY_KEY not in (S or {}):
        if new is not None and new.get('enabled') and (cur or {}).get('enabled') is not True: raise ValueError(ENABLE_HINT)
        return new_sleeves
    if cur is None:                                     # staged without its card = corruption / manual edit: fail closed
        raise ValueError('the F01 canary is staged but the stored C-F01 card is missing - slot edits are refused '
                         'until Integration recovers it (fail closed)')
    if new is None: raise ValueError('the C-F01 card is frozen while the F01 canary is staged - it cannot be removed')
    was, now = cur.get('enabled') is True, new.get('enabled') is True
    if now and not was: raise ValueError(ENABLE_HINT)
    try: ref = normalize(cur)
    except Exception: ref = None
    if ref is None or {k: v for k, v in ref.items() if k != 'enabled'} != {k: v for k, v in new.items() if k != 'enabled'}:
        raise ValueError('the C-F01 card is frozen while the F01 canary is staged - only the rollback enabled -> disabled is allowed')
    keep = dict(copy.deepcopy(cur), enabled=now)
    return [keep if isinstance(x, dict) and x.get('id') == FC.SLOT else x for x in new_sleeves]


ACTIONS = dict(stage=stage, recover_stage=recover_stage, rearm=rearm, preflight=preflight, enable=enable)


def handle(e, path, b):
    act = ACTIONS.get(path[len(ROUTE):]) if path.startswith(ROUTE) else None
    if act is None: raise ValueError('unknown F01 canary action')
    return act(e, b)
