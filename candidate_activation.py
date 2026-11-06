"""Generic, default-OFF PAPER candidate activation.

This module owns one small interface for every future candidate card: ``stage`` freezes the
already-installed disabled sleeve and its source/config identity, ``preflight`` proves the
current PAPER/testnet account is at a clean cohort boundary, ``enable`` repeats that proof and
turns on only the frozen card, and ``rollback`` turns it off.  It does not install strategies,
promote economic evidence, contact GitHub, or know candidate-specific trading logic.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone

import actual_costs as AC

ROUTE = '/api/candidate_activation/'
BOUNDARY_ROUTE = '/api/paper_cohort/boundary'
CONFIG_KEY = 'CANDIDATE_ACTIVATION_CONFIGS'
PREFLIGHT_KEY = 'CANDIDATE_ACTIVATION_PREFLIGHTS'
AUDIT_KEY = 'CANDIDATE_ACTIVATION_AUDIT'
OWNED_KEYS = (CONFIG_KEY, PREFLIGHT_KEY, AUDIT_KEY)
HEX40 = re.compile(r'^[0-9a-f]{40}$')
HEX64 = re.compile(r'^[0-9a-f]{64}$')
SLOT_RE = re.compile(r'^C-[A-Z0-9_-]{1,10}$')
FILE_RE = re.compile(r'^[A-Za-z0-9_.\-/]{1,180}$')
ZERO = '0' * 64
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
BOUND_FIELDS = ('max_calendar_days', 'max_closed_trades', 'max_drawdown_r',
                'max_open_positions', 'max_risk_per_trade')
CONFIG_FIELDS = {'version', 'slot_id', 'card', 'card_sha256', 'bounds', 'source_commit',
                 'source_files', 'source_manifest', 'source_sha256', 'evidence_path',
                 'start_utc', 'config_sha256'}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('utf-8')


def _sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else _canonical(value)).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def _timestamp(value):
    try:
        out = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return out.astimezone(timezone.utc) if out.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _slot(settings, slot_id):
    return next((x for x in (settings or {}).get('SLEEVES') or ()
                 if isinstance(x, dict) and x.get('id') == slot_id), None)


def _card(slot):
    return {k: copy.deepcopy(v) for k, v in (slot or {}).items() if k != 'enabled'}


def _env(engine, testnet):
    if getattr(engine, 'live', True) is not False:
        raise ValueError('candidate activation is PAPER/testnet only - refused in live mode')
    if getattr(getattr(engine, 'trade', None), 'base', None) != testnet:
        raise ValueError('candidate activation requires the exact Binance USD-M TESTNET endpoint')


def _strict_path(path, *, outside=()):
    if not isinstance(path, str) or not path or not os.path.isabs(path):
        raise ValueError('evidence_path must be an absolute path')
    real = os.path.normcase(os.path.realpath(path))
    for parent in outside:
        p = os.path.normcase(os.path.realpath(parent))
        try:
            if os.path.commonpath((real, p)) == p:
                raise ValueError('evidence_path must be outside the repository and runtime folder')
        except ValueError as ex:
            if 'outside' in str(ex):
                raise
    return os.path.abspath(path)


def _source_manifest(files, root=REPO_ROOT):
    if not isinstance(files, list) or not files or len(files) > 32:
        raise ValueError('source_files must be a non-empty list of at most 32 files')
    out = {}
    base = os.path.realpath(root)
    for name in files:
        if (not isinstance(name, str) or not FILE_RE.fullmatch(name) or '\\' in name
                or name.startswith('/') or '..' in name.split('/')):
            raise ValueError('source_files must contain safe repository-relative paths')
        path = os.path.realpath(os.path.join(base, *name.split('/')))
        if os.path.commonpath((path, base)) != base or not os.path.isfile(path):
            raise ValueError(f'candidate source file is missing: {name}')
        with open(path, 'rb') as fh:
            out[name] = hashlib.sha256(fh.read().replace(b'\r\n', b'\n')).hexdigest()
    return out


def _running_commit(engine):
    """Installer-authenticated source commit; development checkouts are never activation authority."""
    try:
        from build_info import BUILD_ID
        with open(os.path.join(os.path.abspath(engine.dir), 'build.json'), encoding='utf-8-sig') as fh:
            row = json.load(fh)
        commit = row.get('source_commit') if isinstance(row, dict) and row.get('build_id') == BUILD_ID else None
        return commit if isinstance(commit, str) and HEX40.fullmatch(commit) and BUILD_ID != 'dev' else None
    except (ImportError, OSError, ValueError, TypeError):
        return None


def _bounds(value, card):
    if not isinstance(value, dict) or set(value) != set(BOUND_FIELDS):
        raise ValueError('bounds must contain exactly ' + ', '.join(BOUND_FIELDS))
    ints = {'max_calendar_days': (1, 365), 'max_closed_trades': (1, 10000),
            'max_open_positions': (1, 20)}
    out = {}
    for key, (lo, hi) in ints.items():
        val = value.get(key)
        if isinstance(val, bool) or not isinstance(val, int) or not lo <= val <= hi:
            raise ValueError(f'{key} must be an integer from {lo} to {hi}')
        out[key] = val
    for key, lo, hi in (('max_drawdown_r', 0.01, 100.0),
                        ('max_risk_per_trade', 0.0001, 0.05)):
        val = value.get(key)
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or not lo <= val <= hi:
            raise ValueError(f'{key} must be a finite number from {lo} to {hi}')
        out[key] = float(val)
    if int(card.get('max_pos') or 0) > out['max_open_positions']:
        raise ValueError('candidate card max_pos exceeds its frozen max_open_positions bound')
    if float(card.get('risk') or 0) > out['max_risk_per_trade']:
        raise ValueError('candidate card risk exceeds its frozen max_risk_per_trade bound')
    return out


def _configs(settings):
    rows = (settings or {}).get(CONFIG_KEY, {})
    if not isinstance(rows, dict):
        raise ValueError('candidate activation configs are malformed - refused (fail closed)')
    return rows


def _config(settings, slot_id):
    cfg = _configs(settings).get(slot_id)
    valid = (isinstance(cfg, dict) and set(cfg) == CONFIG_FIELDS and cfg.get('version') == 1
             and cfg.get('slot_id') == slot_id and isinstance(slot_id, str) and SLOT_RE.fullmatch(slot_id)
             and isinstance(cfg.get('card'), dict) and cfg.get('card_sha256') == _sha(cfg.get('card'))
             and isinstance(cfg.get('source_manifest'), dict)
             and cfg.get('source_sha256') == _sha(cfg.get('source_manifest'))
             and isinstance(cfg.get('source_commit'), str) and HEX40.fullmatch(cfg['source_commit'])
             and _timestamp(cfg.get('start_utc')) is not None
             and cfg.get('config_sha256') == _sha({k: v for k, v in cfg.items() if k != 'config_sha256'}))
    if not valid:
        raise ValueError(f'{slot_id} has no valid immutable activation config')
    _bounds(cfg.get('bounds'), cfg.get('card'))
    return cfg


def _rows(engine, slot_id):
    seen, rows = set(), []
    for src in (getattr(engine, 'history', None) or (), getattr(engine, 'cohort_rows', None) or ()):
        for row in src:
            if not isinstance(row, dict) or row.get('sleeve') != slot_id:
                continue
            key = row.get('id') if isinstance(row.get('id'), str) and row.get('id') else _sha(row)
            if key not in seen:
                seen.add(key); rows.append(row)
    return rows


def bound_status(engine, cfg, now=None):
    """Pure measured-cost status for the frozen candidate-specific limits."""
    now = now or _now()
    start = _timestamp(cfg['start_utc'])
    rows, cumulative, peak, drawdown, pending = _rows(engine, cfg['slot_id']), 0.0, 0.0, 0.0, []
    for row in rows:
        view, risk = AC.row_view(row), row.get('risk_usd')
        if view['status'] not in ('measured', 'acknowledged') or view['pnl'] is None \
                or isinstance(risk, bool) or not isinstance(risk, (int, float)) or risk <= 0:
            pending.append(row.get('symbol')); continue
        cumulative += float(view['pnl']) / float(risk)
        peak = max(peak, cumulative); drawdown = max(drawdown, peak - cumulative)
    idx = (getattr(engine, 'state', None) or {}).get('candidate_cost_pending')
    if idx is not None and not isinstance(idx, dict):
        pending.append('unreadable-index')
    elif isinstance(idx, dict):
        pending += [v.get('symbol') for v in idx.values()
                    if isinstance(v, dict) and v.get('sleeve') == cfg['slot_id']]
    days = max(0.0, (now - start).total_seconds() / 86400)
    bounds = cfg['bounds']; block = None
    if now < start:
        block = 'candidate is before its frozen start_utc'
    elif pending:
        block = f'{len(pending)} candidate close(s) have pending/unreadable costs'
    elif days >= bounds['max_calendar_days']:
        block = f"candidate duration {days:.2f} days reached {bounds['max_calendar_days']}"
    elif len(rows) >= bounds['max_closed_trades']:
        block = f"candidate closes {len(rows)} reached {bounds['max_closed_trades']}"
    elif drawdown >= bounds['max_drawdown_r']:
        block = f"candidate measured drawdown {drawdown:.2f}R reached {bounds['max_drawdown_r']:.2f}R"
    return dict(block=block, closed=len(rows), pending=sorted(str(x) for x in pending),
                net_r=round(cumulative, 6), drawdown_r=round(drawdown, 6), days=round(days, 6))


def _busy(engine):
    state = getattr(engine, 'state', None) or {}
    keys = tuple(getattr(engine, 'BOUNDARY_BUSY', ('lots', 'pending_entries', 'resting_entries',
                                                   'unconfirmed_entries', 'grids')))
    busy = [key for key in keys if state.get(key)]
    if getattr(engine, '_deferred', None):
        busy.append('deferred_entries')
    return busy


def _exchange_busy(engine):
    """Authenticated exchange truth: unknown is a refusal, never an empty account."""
    try:
        positions = engine.trade.positions(critical=True, retry=False)
        orders = engine.trade.open_orders_all()
    except Exception as ex:
        return [f'exchange flatness unavailable ({type(ex).__name__})']
    out = []
    if not isinstance(positions, dict): out.append('exchange positions answer is malformed')
    elif positions: out.append(f'exchange has {len(positions)} open position(s)')
    if not isinstance(orders, list): out.append('exchange orders answer is malformed')
    elif orders: out.append(f'exchange has {len(orders)} working order(s)')
    return out


def _account_pending_costs(engine):
    pending = []
    for src in (getattr(engine, 'history', None) or (), getattr(engine, 'cohort_rows', None) or ()):
        for row in src:
            try:
                if not isinstance(row, dict) or AC.row_view(row)['status'] == 'pending':
                    pending.append((row or {}).get('symbol') if isinstance(row, dict) else 'unreadable-row')
            except Exception:
                pending.append('unreadable-row')
    idx = (getattr(engine, 'state', None) or {}).get('candidate_cost_pending')
    if idx is not None and not isinstance(idx, dict): pending.append('unreadable-index')
    elif isinstance(idx, dict): pending += [str((v or {}).get('symbol') or 'unknown') for v in idx.values()]
    return pending


def _checks(engine, cfg, testnet, root=REPO_ROOT):
    failures = []
    try: _env(engine, testnet)
    except ValueError as ex: failures.append(str(ex))
    slot = _slot(engine.S, cfg['slot_id'])
    if slot is None: failures.append('candidate slot is missing')
    elif slot.get('enabled') is not False: failures.append('candidate slot is not default OFF')
    elif _sha(_card(slot)) != cfg['card_sha256']: failures.append('candidate card changed after staging')
    try:
        if _source_manifest(cfg['source_files'], root) != cfg['source_manifest']:
            failures.append('candidate source hash changed after staging')
    except ValueError as ex: failures.append(str(ex))
    try: _strict_path(cfg['evidence_path'], outside=(root, getattr(engine, 'dir', root)))
    except ValueError as ex: failures.append(str(ex))
    if _running_commit(engine) != cfg['source_commit']:
        failures.append('installed build does not match the frozen source_commit')
    busy = _busy(engine)
    if busy: failures.append('account is not flat/no-working: ' + ', '.join(busy))
    exchange_busy = _exchange_busy(engine)
    if exchange_busy: failures.append('account is not flat/no-working: ' + ', '.join(exchange_busy))
    status = bound_status(engine, cfg)
    pending = _account_pending_costs(engine)
    if pending: failures.append(f'{len(pending)} account close(s) have pending/unreadable costs')
    if (getattr(engine, 'state', None) or {}).get('cohort') is None:
        failures.append('no open PAPER cohort is bound to this activation')
    return failures, status


def stage(engine, body, *, testnet, root=REPO_ROOT):
    if not isinstance(body, dict): raise ValueError('bad request')
    allowed = {'slot_id', 'bounds', 'source_commit', 'source_files', 'evidence_path', 'start_utc'}
    if set(body) != allowed: raise ValueError('stage requires exactly ' + ', '.join(sorted(allowed)))
    slot_id = body['slot_id']
    if not isinstance(slot_id, str) or not SLOT_RE.fullmatch(slot_id):
        raise ValueError('slot_id must be C- followed by letters, digits, dash or underscore')
    if not isinstance(body['source_commit'], str) or not HEX40.fullmatch(body['source_commit']):
        raise ValueError('source_commit must be the accepted full 40-hex commit')
    start = _timestamp(body['start_utc'])
    if start is None or start <= _now(): raise ValueError('start_utc must be a future tz-aware timestamp')
    with engine.lock:
        _env(engine, testnet)
        slot = _slot(engine.S, slot_id)
        if slot is None: raise ValueError('candidate slot is not installed')
        if slot.get('enabled') is not False: raise ValueError('candidate must be default OFF before staging')
        if slot_id in _configs(engine.S): raise ValueError('candidate activation config is immutable once staged')
        if _running_commit(engine) != body['source_commit']:
            raise ValueError('source_commit does not match the authenticated installed build')
        card = _card(slot); bounds = _bounds(body['bounds'], card)
        manifest = _source_manifest(body['source_files'], root)
        evidence = _strict_path(body['evidence_path'], outside=(root, getattr(engine, 'dir', root)))
        payload = dict(version=1, slot_id=slot_id, card=card, card_sha256=_sha(card), bounds=bounds,
                       source_commit=body['source_commit'], source_files=list(body['source_files']),
                       source_manifest=manifest, source_sha256=_sha(manifest), evidence_path=evidence,
                       start_utc=start.isoformat(timespec='seconds'))
        payload['config_sha256'] = _sha(payload)
        ns = copy.deepcopy(engine.S); ns.setdefault(CONFIG_KEY, {})[slot_id] = payload
        engine.commit_settings(ns)
    return dict(staged=True, slot_id=slot_id, config_sha256=payload['config_sha256'],
                card_sha256=payload['card_sha256'], source_sha256=payload['source_sha256'])


def preflight(engine, body, *, testnet, root=REPO_ROOT, write=True):
    if not isinstance(body, dict) or set(body) != {'slot_id'}:
        raise ValueError('preflight requires exactly slot_id')
    with engine.lock:
        cfg = _config(engine.S, body['slot_id'])
        failures, status = _checks(engine, cfg, testnet, root)
        cohort = (engine.state or {}).get('cohort') or {}
        record = dict(version=1, slot_id=cfg['slot_id'], config_sha256=cfg['config_sha256'],
                      card_sha256=cfg['card_sha256'], source_sha256=cfg['source_sha256'],
                      cohort_id=cohort.get('id'), passed=not failures, failures=failures,
                      bounds_status=status, at_utc=_now().isoformat(timespec='seconds'))
        record['record_sha256'] = _sha(record)
        if write:
            ns = copy.deepcopy(engine.S); ns.setdefault(PREFLIGHT_KEY, {})[cfg['slot_id']] = record
            engine.commit_settings(ns)
    return copy.deepcopy(record)


def _audit(settings, slot_id, action, cfg, preflight_hash=None):
    prior = settings.get(AUDIT_KEY, [])
    if not isinstance(prior, list): raise ValueError('candidate activation audit is malformed')
    rec = dict(version=1, sequence=len(prior) + 1,
               prev_event_sha256=prior[-1]['event_sha256'] if prior else ZERO,
               slot_id=slot_id, action=action, config_sha256=cfg['config_sha256'],
               preflight_record_sha256=preflight_hash, at_utc=_now().isoformat(timespec='seconds'))
    rec['event_sha256'] = _sha(rec)
    return prior, rec


def enable(engine, body, *, testnet, root=REPO_ROOT):
    if not isinstance(body, dict) or set(body) != {'slot_id'}:
        raise ValueError('enable requires exactly slot_id')
    with engine.lock:
        cfg = _config(engine.S, body['slot_id']); _env(engine, testnet)
        slot = _slot(engine.S, cfg['slot_id'])
        if slot is None or slot.get('enabled') is not False: raise ValueError('candidate is missing or already enabled')
        stored = (engine.S.get(PREFLIGHT_KEY) or {}).get(cfg['slot_id'])
        if not isinstance(stored, dict) or stored.get('passed') is not True:
            raise ValueError('no stored PASS preflight for this candidate')
        fresh = preflight(engine, body, testnet=testnet, root=root, write=False)
        if not fresh['passed']: raise ValueError('fresh preflight failed: ' + '; '.join(fresh['failures']))
        if _timestamp(cfg['start_utc']) <= _now():
            raise ValueError('the frozen start_utc has passed - restage a new candidate config')
        if (stored.get('config_sha256'), stored.get('card_sha256'), stored.get('source_sha256'), stored.get('cohort_id')) != \
                (fresh['config_sha256'], fresh['card_sha256'], fresh['source_sha256'], fresh['cohort_id']):
            raise ValueError('stored PASS does not bind the current config/source/card/cohort')
        ns = copy.deepcopy(engine.S); _slot(ns, cfg['slot_id'])['enabled'] = True
        prior, event = _audit(engine.S, cfg['slot_id'], 'enable', cfg, stored['record_sha256'])
        ns[AUDIT_KEY] = copy.deepcopy(prior) + [event]; engine.commit_settings(ns)
    return dict(enabled=True, slot_id=cfg['slot_id'], event_sha256=event['event_sha256'])


def rollback(engine, body, *, testnet):
    if not isinstance(body, dict) or set(body) != {'slot_id', 'reason'} or not isinstance(body.get('reason'), str) \
            or not body['reason'].strip() or len(body['reason']) > 200:
        raise ValueError('rollback requires slot_id and a non-empty reason (max 200 chars)')
    with engine.lock:
        _env(engine, testnet); cfg = _config(engine.S, body['slot_id'])
        slot = _slot(engine.S, cfg['slot_id'])
        if slot is None: raise ValueError('candidate slot is missing')
        ns = copy.deepcopy(engine.S); _slot(ns, cfg['slot_id'])['enabled'] = False
        prior, event = _audit(engine.S, cfg['slot_id'], 'rollback', cfg)
        event['reason'] = body['reason'].strip(); event['event_sha256'] = _sha({k: v for k, v in event.items() if k != 'event_sha256'})
        ns[AUDIT_KEY] = copy.deepcopy(prior) + [event]; engine.commit_settings(ns)
    return dict(enabled=False, slot_id=cfg['slot_id'], event_sha256=event['event_sha256'])


def runtime_block(engine, slot, *, testnet):
    """Fail-closed new-entry gate for a staged generic candidate. Open risk stays managed."""
    try:
        slot_id = (slot or {}).get('id')
        if slot_id not in _configs(getattr(engine, 'S', {})): return None
        cfg = _config(engine.S, slot_id); _env(engine, testnet)
        if _sha(_card(slot)) != cfg['card_sha256']: return 'candidate activation card drifted - no new risk'
        status = bound_status(engine, cfg)
        return (status['block'] + ' - no new risk; open positions stay protected') if status['block'] else None
    except Exception as ex:
        return f'candidate activation check failed ({type(ex).__name__}) - no new risk'


def sleeves_guard(settings, sleeves):
    """Freeze staged cards; generic sleeve editing may only roll an enabled card back to OFF."""
    configs = _configs(settings)
    if not configs: return sleeves
    current = {x.get('id'): x for x in settings.get('SLEEVES') or [] if isinstance(x, dict)}
    posted = {x.get('id'): x for x in sleeves or [] if isinstance(x, dict)}
    out = list(sleeves)
    for slot_id, cfg in configs.items():
        old, new = current.get(slot_id), posted.get(slot_id)
        if old is None or new is None: raise ValueError(f'{slot_id} staged candidate card cannot be removed')
        if _sha(_card(new)) != cfg['card_sha256']:
            raise ValueError(f'{slot_id} staged candidate card is immutable')
        if new.get('enabled') and not old.get('enabled'):
            raise ValueError(f'{slot_id} can be enabled only through {ROUTE}enable')
        keep = dict(copy.deepcopy(old), enabled=bool(new.get('enabled')))
        out = [keep if isinstance(x, dict) and x.get('id') == slot_id else x for x in out]
    return out


def boundary(engine, body, *, testnet):
    """Authenticated by app.do_POST; seal the explicit drift decision into the cohort pack."""
    if not isinstance(body, dict) or set(body) != {'evidence_path', 'note', 'accept_drift'}:
        raise ValueError('boundary requires exactly evidence_path, note and accept_drift')
    if not isinstance(body['accept_drift'], bool): raise ValueError('accept_drift must be an explicit JSON boolean')
    if not isinstance(body['note'], str) or len(body['note']) > 500: raise ValueError('note must be a string (max 500 chars)')
    _env(engine, testnet)
    path = _strict_path(body['evidence_path'], outside=(REPO_ROOT, getattr(engine, 'dir', REPO_ROOT)))
    result = engine.cohort_boundary(path, note=body['note'], accept_drift=body['accept_drift'])
    return dict(result, evidence_path=path, accept_drift=body['accept_drift'], note=body['note'])


def handle(engine, path, body, *, testnet, root=REPO_ROOT):
    if path == BOUNDARY_ROUTE: return boundary(engine, body, testnet=testnet)
    if not path.startswith(ROUTE): raise ValueError('unknown candidate activation action')
    action = path[len(ROUTE):]
    funcs = {'stage': stage, 'preflight': preflight, 'enable': enable, 'rollback': rollback}
    if action not in funcs: raise ValueError('unknown candidate activation action')
    kw = {'testnet': testnet}
    if action in ('stage', 'preflight', 'enable'): kw['root'] = root
    return funcs[action](engine, body, **kw)
