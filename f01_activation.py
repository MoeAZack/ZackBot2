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
edit: it is never replaced here and every step stays refused (fail closed) until Integration recovers it out of band
(Codex 6518282236 Q2).

Nothing here starts a second Engine, patches a live file by hand, or touches strategy logic or a pinned function.
"""
import copy
import json
import os

import engine as ENG
import f01_canary as FC

PREFLIGHT_KEY = 'F01_CANARY_PREFLIGHT'
OWNED_KEYS = (FC.CANARY_KEY, PREFLIGHT_KEY)
CFG_FIELDS = ('start_utc', 'authorization_comment_id', 'cohort_dir', 'source_commit', 'source_manifest')
PINS_FILE = os.path.join('contracts', 'factory_f01_impl_01', 'cohort_manifest.v1.json')
ROUTE = '/api/f01_canary/'
ENABLE_HINT = 'C-F01 is switched on only by /api/f01_canary/enable after a PASS preflight'


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
    with e.lock:
        _env(e)
        if FC.CANARY_KEY in e.S or PREFLIGHT_KEY in e.S:
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
        ns = copy.deepcopy(e.S)
        _slot(ns)['enabled'] = True
        e.commit_settings(ns)
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


ACTIONS = dict(stage=stage, preflight=preflight, enable=enable)


def handle(e, path, b):
    act = ACTIONS.get(path[len(ROUTE):]) if path.startswith(ROUTE) else None
    if act is None: raise ValueError('unknown F01 canary action')
    return act(e, b)
