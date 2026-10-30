"""FACTORY-F01-IMPL-01 (Codex ruling 6513808658): the bounded C-F01 PAPER canary runtime, default OFF.

Specified by the FACTORY-F01-PARITY-01 contract (PR #248, OQ1-OQ8 ruled 6512712537). Nothing here enables a slot, changes a
setting, sizes, orders or closes anything. Three parts:

  * bound_status: the canary bounds (OQ1) - the first of 30 calendar days from the frozen start, 8 closed C-F01 trades
    counted from the start, or a 4.0 R measured net drawdown from the canary peak. Only MEASURED costs count; a C-F01 close
    whose costs are pending (row view, or the durable candidate_cost_pending index) fails closed. A reached bound stops NEW
    C-F01 entries only (OQ8): open lots keep their exchange stop and normal management, nothing is force-closed.
  * preflight: the contract's C1-C10 start checks over a read-only view of the engine -> a structured pass / fail record.
  * ExtractWriter: immutable, sha256-addressed runtime extracts (decision / trade / preflight records) under a cohort
    directory the settings name (never inside the repo). Whitelisted fields only - no order, trade, stop or client ids.

The flag is settings[CANARY_KEY] = {"on": true, "start_utc": <tz-aware ISO>, "authorization_comment_id": <id>,
"cohort_dir": <path or null>}. Absent, or "on" not exactly True = OFF: the engine behaves exactly as before. ON is honoured
only in PAPER on the Binance Futures testnet; anywhere else every new C-F01 entry is refused (fail closed, logged).
"""
import ast
import csv
import hashlib
import io
import json
import math
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

import actual_costs as AC

CANARY_KEY = 'F01_CANARY'
SLOT, OTHER = 'C-F01', 'C-F02'
FAMILY_SLOTS = (SLOT, OTHER)
DURATION_DAYS, MAX_CLOSED_TRADES, DRAWDOWN_STOP_R = 30, 8, 4.0       # OQ1 (6512712537)
FAMILY_CAP, MAX_LEVERAGE, ENTRY_ORDER = 4, 10, 'market'            # OQ5 / C8
STOP_FAIL = ('restoring', 'owner_check')                            # engine.CANDIDATE_STOP_FAIL
MEASURED = ('measured', 'acknowledged')
# contract canary.evidence_outputs.forbidden_output_fields + every other id-like key the engine carries
FORBIDDEN_KEYS = frozenset(k.lower() for k in (
    'orderId', 'clientOrderId', 'tradeId', 'exchange_order_id', 'exchange_trade_id', 'stop_id', 'cid', 'API_KEY', 'API_SECRET',
    'id', 'order_id', 'trade_id', 'client_id', 'client_order_id', 'tp_id', 'cost_ref', 'cost_refs', 'fill_refs', 'algoId',
    'newClientOrderId', 'origClientOrderId', 'secret', 'signature'))
EXTRACT_KINDS = ('decision', 'trade', 'preflight')
DECISION_COLUMNS = ('symbol', 'decision_bar', 'scored', 'rt_le', 'rp_le', 'rt_lx', 'rp_lx', 'rt_atr', 'rp_atr', 'rt_outcome',
                    'rt_reason', 'rt_reason_logged_at', 'rp_outcome', 'causal_window_id', 'field_ids', 'result')
TRADE_COLUMNS = ('row_key_sha256', 'symbol', 'entry_bar', 'exit_bar', 'rt_why', 'rp_why', 'rt_entry', 'rp_entry', 'rt_exit',
                 'rp_exit', 'rt_stop', 'rule_stop', 'stop_dist', 'risk_usd', 'sleeve_eq', 'risk_fraction', 'qty_raw',
                 'qty_rule_raw', 'qty_sent', 'qty_rule_quantized', 'step', 'stop_raw', 'stop_rule_raw', 'stop_rule_quantized',
                 'tick', 'fees_measured', 'fees_model', 'funding_measured', 'funding_model', 'slip_entry_bps', 'slip_exit_bps',
                 'rt_r', 'rp_r', 'cost_status')
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
TF_S = 4 * 3600                                                     # C-F01 decides on closed 4h bars
HEX40 = re.compile(r'^[0-9a-f]{40}$')
# the frozen C-F01 card (parity contract binding; Codex 6514109966 #6: share is checked too)
FROZEN_CARD = dict(key='ema_mom', sides='long', risk=0.01, max_pos=4, tf='4h', share=1 / 6, mgmt={},
                   symbols=['BTCUSDT', 'ETHUSDT', 'ZECUSDT', 'SOLUSDT', 'XRPUSDT', 'NEARUSDT', 'DOGEUSDT', 'UNIUSDT', 'SUIUSDT',
                            'QNTUSDT', 'BNBUSDT', 'LSKUSDT', 'ENAUSDT', '1000PEPEUSDT', 'WLDUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT'])


# ------------------------------------------------------------------ flag + environment
def _ts(v):
    """A tz-aware UTC datetime from an ISO string / datetime, else None (a naive or unreadable time is not a frozen start)."""
    try:
        t = v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return t.astimezone(timezone.utc) if t.tzinfo is not None else None


def config(S):
    """The canary config when the flag is ON, else None (OFF = absent, not a dict, or "on" not exactly True). An enabled
    C-F01 with no ON flag is refused new entries by the engine gate (Codex 6514109966 #2)."""
    c = (S or {}).get(CANARY_KEY)
    if not isinstance(c, dict) or c.get('on') is not True: return None
    return dict(on=True, start_utc=c.get('start_utc'), authorization_comment_id=c.get('authorization_comment_id'),
                cohort_dir=c.get('cohort_dir') or None, impl_head=c.get('impl_head'), impl_tree=c.get('impl_tree'))


def auth_id_ok(v):
    """A positive numeric GitHub comment id (int, or a string of digits without a leading zero); never a bool."""
    if isinstance(v, bool): return False
    if isinstance(v, int): return v > 0
    return isinstance(v, str) and v.isdigit() and v.isascii() and not v.startswith('0')


def cohort_dir_ok(v, repo_root=None):
    return isinstance(v, str) and bool(v) and os.path.isabs(v) and not _inside(v, repo_root or REPO_ROOT)


def cfg_problems(cfg, repo_root=None):
    """Every malformed or missing field of an ON config (empty = well-formed). Each one fails closed."""
    c, out = cfg or {}, []
    if _ts(c.get('start_utc')) is None: out.append('no frozen tz-aware start timestamp')
    if not auth_id_ok(c.get('authorization_comment_id')): out.append('authorization id is not a positive numeric GitHub comment id')
    if not cohort_dir_ok(c.get('cohort_dir'), repo_root): out.append('cohort_dir is not an absolute path outside the repository')
    if not (isinstance(c.get('impl_head'), str) and HEX40.match(c['impl_head'])): out.append('impl_head is not a full 40-hex commit SHA')
    if not (isinstance(c.get('impl_tree'), str) and HEX40.match(c['impl_tree'])): out.append('impl_tree is not a full 40-hex tree SHA')
    return out


def running_build(root=None):
    """(head, tree, clean) of the running source checkout via read-only git; (None, None, False) when it cannot be read."""
    root = root or REPO_ROOT
    try:
        run = lambda *a: subprocess.run(['git', '-C', root, *a], capture_output=True, text=True, timeout=20, check=True).stdout.strip()
        head, tree = run('rev-parse', 'HEAD'), run('rev-parse', 'HEAD^{tree}')
        clean = run('status', '--porcelain', '--untracked-files=no') == ''
        return head, tree, clean
    except Exception:
        return None, None, False


def card_mismatch(sl):
    """Fields where a slot differs from FROZEN_CARD (share included). mgmt None == {}; symbols compare as the ordered list."""
    sl = sl or {}
    def norm(f, v):
        if f == 'symbols' and isinstance(v, (list, tuple)): return list(v)
        return (v or {}) if f == 'mgmt' else v
    return [f for f in FROZEN_CARD if norm(f, sl.get(f)) != FROZEN_CARD[f]]


def env_refusal(live, base, testnet):
    """None only for PAPER on the Binance Futures testnet base URL; anything else is refused (S7, fail closed)."""
    if live is not False: return 'F01 canary is PAPER/testnet only - refused in live mode'
    if not base or base != testnet: return 'F01 canary: environment not confirmed as Binance Futures testnet - refused'
    return None


# ------------------------------------------------------------------ bounds (OQ1) + rollback semantics (OQ8)
def _rid_ok(r):
    return isinstance(r.get('id'), str) and r['id'].strip() != ''


def canary_rows(rows, start, extra=()):
    """(C-F01 closed rows opened AND closed at / after the frozen start, by close; bad) over the trading history then the
    cohort evidence store (extra). A row joins by its own non-empty string id: history wins a duplicate id, the cohort copy
    of a row never replaces or hides another row (Codex 6514109966 #7). A C-F01 row whose id or times are unusable cannot
    be classified - it is returned in bad (the caller fails closed); a pre-canary lot is never counted (OQ3)."""
    out, bad, seen = [], [], set()
    for src in (rows or (), extra or ()):
        for r in src:
            if not isinstance(r, dict) or r.get('sleeve') != SLOT: continue
            if not _rid_ok(r): bad.append(r.get('symbol')); continue
            if r['id'] in seen: continue
            seen.add(r['id'])
            o, c = _ts(r.get('opened')), _ts(r.get('closed'))
            if o is None or c is None: bad.append(r.get('symbol')); continue
            if o < start or c < start: continue
            out.append((c, r))
    return [r for _c, r in sorted(out, key=lambda x: x[0])], bad


def bound_status(cfg, rows, pending_index=None, now=None, extra=(), repo_root=None):
    """-> dict(block, stop, closed, net_r, peak_r, drawdown_r, pending, days). block = the refusal text for a NEW C-F01 entry
    (None = entries allowed); stop = {'id', 'why'} once a terminal bound (S5 / S6) is reached. Pure."""
    now = _ts(now) or datetime.now(timezone.utc)
    start = _ts((cfg or {}).get('start_utc'))
    out = dict(block=None, stop=None, closed=0, net_r=0.0, peak_r=0.0, drawdown_r=0.0, pending=[], days=None)
    bad_cfg = cfg_problems(cfg, repo_root)
    if bad_cfg:
        out['block'] = f"F01 canary: malformed config ({'; '.join(bad_cfg)}) - no new C-F01 entry (fail closed)"; return out
    if now < start:
        out['block'] = 'F01 canary: before the frozen start - no new C-F01 entry'; return out
    out['days'] = (now - start).total_seconds() / 86400
    got, bad = canary_rows(rows, start, extra)
    out['closed'] = len(got)
    if bad:
        out['block'] = (f'F01 canary: {len(bad)} C-F01 row(s) without a usable id or close time - trades not countable, '
                        'no new C-F01 entry (fail closed)'); return out
    ids = {r.get('id') for r in got}
    cum = peak = dd = 0.0
    for r in got:
        v = AC.row_view(r)
        risk = r.get('risk_usd')
        if v['status'] not in MEASURED or v['pnl'] is None or not isinstance(risk, (int, float)) or not risk > 0:
            out['pending'].append(r.get('symbol')); continue
        cum += float(v['pnl']) / float(risk)
        peak = max(peak, cum); dd = max(dd, peak - cum)
    for rid, ent in (pending_index or {}).items() if isinstance(pending_index, dict) else ():   # the durable index (FACTORY-CAND-02)
        if rid in ids or not isinstance(ent, dict) or ent.get('sleeve') != SLOT: continue
        c = _ts(ent.get('closed'))
        if c is None or c >= start: out['pending'].append(ent.get('symbol'))     # unreadable close -> fail closed
    out.update(net_r=round(cum, 6), peak_r=round(peak, 6), drawdown_r=round(dd, 6))
    if now >= start + timedelta(days=DURATION_DAYS):
        out['stop'] = dict(id='S6', why=f'F01 canary bound reached: {DURATION_DAYS} calendar days since the frozen start')
    elif len(got) >= MAX_CLOSED_TRADES:
        out['stop'] = dict(id='S6', why=f'F01 canary bound reached: {MAX_CLOSED_TRADES} closed C-F01 trades since the start')
    elif dd >= DRAWDOWN_STOP_R:
        out['stop'] = dict(id='S5', why=f'F01 canary bound reached: measured net drawdown {dd:.2f} R >= {DRAWDOWN_STOP_R} R from the canary peak')
    if out['stop']:
        out['block'] = out['stop']['why'] + ' - no new C-F01 entry; open lots stay protected and managed'
    elif out['pending']:
        out['block'] = (f"F01 canary: {len(out['pending'])} closed C-F01 trade(s) with costs pending - drawdown not measurable, "
                        'no new C-F01 entry until measured (fail closed)')
    return out


def is_scored(decision_bar, cfg, logged_at):
    """OQ2 (Codex 6514109966 #3): scored only for a fully closed 4h decision bar that opened at / after the frozen start -
    the bar spanning the start is excluded - and that the engine confirmed closed (bar open + 4h <= the decision time)."""
    b, s, t = _ts(_iso(decision_bar)), _ts((cfg or {}).get('start_utc')), _ts(logged_at)
    return bool(b and s and t and b >= s and b + timedelta(seconds=TF_S) <= t)


def _iso(v):
    s = str(v or '')
    if s and 'T' not in s and ' ' in s: s = s.replace(' ', 'T', 1)
    if s and not (s.endswith('Z') or '+' in s[10:] or s[10:].count('-')): s += '+00:00'      # engine candles are UTC
    return s


# ------------------------------------------------------------------ preflight (C1-C10), read-only
def _owner(x, key=None):
    sid = (x or {}).get('sleeve') or (((x or {}).get('plan') or {}).get('sl') or {}).get('id')
    if not sid and key and str(key).startswith('UE|') and str(key).count('|') >= 2: sid = str(key).split('|')[1]
    return sid


def preflight(S, state, *, live, base, testnet, deferred=(), drift=None, governor_mult=None, kelly_mult=None,
              ctrl_long_factor=None, running=(None, None, False), pins_expected=None, pins_actual=None, now=None,
              repo_root=None):
    """The contract's candidate-flat start checks over plain values (no engine method is called here, nothing is written).
    -> dict(pass, checks={C*: {ok, ...}}, recorded={...}). Recorded values (AI_FILTER, FEE_EDGE_GATE, other open slots)
    are captured, never changed."""
    S, state = S or {}, state or {}
    sleeves = {x.get('id'): x for x in S.get('SLEEVES') or () if isinstance(x, dict)}
    f01, f02 = sleeves.get(SLOT), sleeves.get(OTHER)
    lots = list((state.get('lots') or {}).values())
    working = ([(_owner(p), p.get('symbol'), 'pending') for p in (state.get('pending_entries') or {}).values()]
               + [(_owner(r), r.get('symbol'), 'resting') for r in (state.get('resting_entries') or {}).values()]
               + [(_owner(u, k), u.get('symbol'), 'unconfirmed') for k, u in (state.get('unconfirmed_entries') or {}).items()]
               + [(((d or {}).get('sl') or {}).get('id'), (d or {}).get('sym'), 'deferred') for d in deferred or ()])
    fam_lots = [dict(slot=l.get('sleeve'), symbol=l.get('symbol')) for l in lots if l.get('sleeve') in FAMILY_SLOTS]
    fam_work = [dict(slot=o, symbol=s, kind=k) for o, s, k in working if o in FAMILY_SLOTS]
    others = sorted((str(l.get('symbol')), str(l.get('sleeve')), str(l.get('side'))) for l in lots if l.get('sleeve') not in FAMILY_SLOTS)
    disabled = (state.get('candidate_disabled') or {}).get(SLOT)
    stop_fail = [l.get('symbol') for l in lots if l.get('sleeve') == SLOT and l.get('stop_miss') and l.get('stop_miss_why') in STOP_FAIL]
    pend = state.get('candidate_cost_pending')
    cfg = (S.get(CANARY_KEY) if isinstance(S.get(CANARY_KEY), dict) else {}) or {}
    env = env_refusal(live, base, testnet)
    pins_bad = sorted(k for k, v in (pins_expected or {}).items() if not k.startswith('_') and (pins_actual or {}).get(k) != v)
    head, tree, clean = running
    head_ok = isinstance(cfg.get('impl_head'), str) and bool(HEX40.match(cfg['impl_head'])) and head == cfg['impl_head']
    tree_ok = isinstance(cfg.get('impl_tree'), str) and bool(HEX40.match(cfg['impl_tree'])) and tree == cfg['impl_tree']
    card = card_mismatch(f01) if f01 else list(FROZEN_CARD)
    bad_cfg = cfg_problems(cfg, repo_root)
    c = {
        'ENV': dict(ok=env is None, why=env, live=live is not False, testnet=base == testnet and bool(base)),
        'C1': dict(ok=not fam_lots and not fam_work, family_lots=fam_lots, family_working=fam_work),
        'C1b': dict(ok=True, other_open=[dict(symbol=a, slot=b, side=s) for a, b, s in others]),
        'C2': dict(ok=f02 is None or f02.get('enabled') is False, c_f02_enabled=None if f02 is None else f02.get('enabled')),
        'C3': dict(ok=f01 is not None and not disabled and not stop_fail, candidate_disabled=disabled, stop_fail=stop_fail),
        'C4': dict(ok=pend is None or (isinstance(pend, dict) and not pend), size=len(pend) if isinstance(pend, dict) else None),
        'C5': dict(ok=f01 is not None and drift == [] and not card, drift=drift, card_mismatch=card),
        'C6': dict(ok=governor_mult == 1.0 and kelly_mult == 1.0, governor_mult=governor_mult, kelly_mult=kelly_mult),
        'C7': dict(ok=ctrl_long_factor == 1.0, ctrl_long_factor=ctrl_long_factor),
        'C8': dict(ok=S.get('ENTRY_ORDER') == ENTRY_ORDER and S.get('MAX_LEVERAGE') == MAX_LEVERAGE,
                   ENTRY_ORDER=S.get('ENTRY_ORDER'), MAX_LEVERAGE=S.get('MAX_LEVERAGE'),
                   AI_FILTER=S.get('AI_FILTER'), FEE_EDGE_GATE=S.get('FEE_EDGE_GATE')),
        'C9': dict(ok=head_ok and tree_ok and clean is True and bool(pins_expected) and not pins_bad,
                   impl_head=cfg.get('impl_head'), impl_tree=cfg.get('impl_tree'), running_head=head, running_tree=tree,
                   running_clean=clean, pins_mismatch=pins_bad,
                   pins_checked=len([k for k in pins_expected or {} if not k.startswith('_')])),
        'C10': dict(ok=cfg.get('on') is True and not bad_cfg, problems=bad_cfg,
                    authorization_comment_id=cfg.get('authorization_comment_id'), start_utc=cfg.get('start_utc'),
                    cohort_dir=cfg.get('cohort_dir')),
    }
    rec = dict(slot_f01=dict(slot=f01.get('id'), strategy_key=f01.get('key'),
                             **{k: f01.get(k) for k in ('sides', 'risk', 'max_pos', 'symbols', 'mgmt', 'tf', 'share', 'enabled')})
               if f01 else None, c_f02_enabled=None if f02 is None else f02.get('enabled'),
               pending_index_size=len(pend) if isinstance(pend, dict) else 0,
               at_utc=(_ts(now) or datetime.now(timezone.utc)).isoformat(timespec='seconds'))
    return {'pass': all(v['ok'] for v in c.values()), 'checks': c, 'recorded': rec}


def function_pins(names, root=REPO_ROOT):
    """{'file.py:[Class.]func': sha256 of ast.get_source_segment (LF)} - the contract's C9 pin rule; read-only."""
    out, src, trees = {}, {}, {}
    for name in names:
        if name.startswith('_'): continue
        path, _, qual = name.partition(':')
        if path not in src:
            with open(os.path.join(root, path), encoding='utf-8', newline='') as f: src[path] = f.read().replace('\r\n', '\n')
            trees[path] = ast.parse(src[path])
        node, parts = trees[path], qual.split('.')
        for p in parts:
            node = next((n for n in getattr(node, 'body', ()) if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
                         and n.name == p), None)
            if node is None: break
        out[name] = (hashlib.sha256(ast.get_source_segment(src[path], node).encode('utf-8')).hexdigest() if node is not None else None)
    return out


# ------------------------------------------------------------------ extracts (immutable, hashed, no ids)
def _enc(v):
    """Contract value_encoding: non-finite floats as {"float": "nan" | "inf" | "-inf"}; bools stay bools."""
    if isinstance(v, float) and not math.isfinite(v): return {'float': 'nan' if v != v else ('inf' if v > 0 else '-inf')}
    if isinstance(v, dict): return {str(k): _enc(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)): return [_enc(x) for x in v]
    if v is None or isinstance(v, (bool, int, float, str)): return v
    return str(v)


def id_fields(rec, path=''):
    """Every key path in rec whose name is an order / trade / stop / client id or a credential (must be empty)."""
    bad = []
    if isinstance(rec, dict):
        for k, v in rec.items():
            p = f'{path}.{k}' if path else str(k)
            if str(k).lower() in FORBIDDEN_KEYS: bad.append(p)
            bad += id_fields(v, p)
    elif isinstance(rec, (list, tuple)):
        for i, v in enumerate(rec): bad += id_fields(v, f'{path}[{i}]')
    return bad


def canonical(rec):
    return json.dumps(_enc(rec), sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def row_key_sha256(row_id):
    """The trade's key for evidence: sha256 of the bot's own row key (never an exchange id)."""
    return sha256(str(row_id).encode('utf-8'))


def _num(v):
    try: return float(v) if v is not None and not isinstance(v, bool) else None
    except (TypeError, ValueError): return None


def decision_record(sym, sg, outcome, reason, logged_at, raw=None, family_open=None, held=None, start_cfg=None):
    """One C-F01 decision (P01-P07 runtime side). Whitelisted fields only; the refusal text is kept exactly."""
    sg, raw = sg or {}, raw or {}
    return dict(kind='decision', slot=SLOT, symbol=sym, decision_bar=str(sg.get('time')),
                scored=is_scored(sg.get('time'), start_cfg, logged_at),
                le=bool(sg.get('le')), lx=bool(sg.get('lx')), atr=_num(sg.get('atr')), close=_num(sg.get('close')),
                raw_le=raw.get('le') if raw.get('time') == sg.get('time') else None,
                outcome=outcome, reason=None if reason is None else str(reason), reason_logged_at=logged_at,
                cap=dict(family_open=family_open, family_cap=FAMILY_CAP, slot_held=held))


def entry_record(lot, rules=None, sg=None):
    """A C-F01 entry as sized and protected (P14-P19 runtime side). qty_raw / stop_raw are the exact pre-quantization
    values the bot computed (lot['sizing_raw'], captured observe-only in open_lot / _create_lot - Codex 6514109966 #1);
    qty_sent / stop_sent are the exchange-quantized values it sent. A lot without the capture records None (never a
    re-derived value)."""
    lot, r, sg = lot or {}, rules or {}, sg or {}
    raw = lot.get('sizing_raw') if isinstance(lot.get('sizing_raw'), dict) else {}
    risk, eq = _num(lot.get('risk_usd')), _num(lot.get('eq_at_entry'))
    return dict(kind='entry', slot=SLOT, symbol=lot.get('symbol'), side=lot.get('side'), decision_bar=str(sg.get('time')),
                opened=lot.get('opened'), entry_fill=_num(lot.get('e0')), atr_at_entry=_num(lot.get('atr0')),
                stop_atr=_num((lot.get('mgmt') or {}).get('stop_atr')), stop_dist=_num(raw.get('stop_dist')),
                stop_raw=_num(raw.get('stop_raw')), stop_sent=_num(lot.get('stop')), qty_raw=_num(raw.get('qty_raw')),
                qty_sent=_num(lot.get('q0')), tick=_num(r.get('tick')), step=_num(r.get('step')), risk_usd=risk, sleeve_eq=eq,
                risk_fraction=(risk / eq) if risk and eq else None, mgmt=lot.get('mgmt') or {})


def trade_record(row):
    """A closed C-F01 row (P15-P20, P22-P26 runtime side): exit, why, management, measured costs and pending status."""
    row = row or {}
    v = AC.row_view(row)
    m = AC.measured(row) if v['status'] in MEASURED else {}
    fills = [[f[0], f[1], _num(f[2]), _num(f[3])] for f in row.get('fills') or () if isinstance(f, (list, tuple)) and len(f) >= 4]
    return dict(kind='trade', slot=SLOT, row_key_sha256=row_key_sha256(row.get('id')), symbol=row.get('symbol'),
                side=row.get('side'), opened=row.get('opened'), closed=row.get('closed'), exit_why=row.get('exit_reason'),
                entry=_num(row.get('entry')), avg_entry=_num(row.get('avg_entry')), exit=_num(row.get('exit')),
                qty_max=_num(row.get('qty_max')), risk_usd=_num(row.get('risk_usd')), adds=row.get('adds'), tp1=row.get('tp1'),
                fills=fills, pnl_gross_booked=_num(row.get('pnl_gross')), fees_model=_num(row.get('fees')),
                fees_measured=_num(m.get('fees')), funding_measured=_num(m.get('funding')), net_measured=_num(m.get('net')),
                r_measured=(_num(v['pnl']) / _num(row.get('risk_usd'))) if v['status'] in MEASURED and v['pnl'] is not None
                and _num(row.get('risk_usd')) else None, cost_status=v['status'], cost_pending_why=list(v.get('why') or ()))


def _inside(path, root):
    try: return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
    except ValueError: return False                     # different drives -> not inside


class ExtractWriter:
    """Write-once, content-addressed extracts: <cohort_dir>/extracts/<kind>/<sha256>.json. A record with an id-like field,
    a cohort dir inside the repo, or an existing file with different bytes raises; the same bytes again are a no-op."""

    def __init__(self, cohort_dir, repo_root=REPO_ROOT):
        if not cohort_dir: raise ValueError('no cohort directory configured (extracts disabled)')
        if _inside(cohort_dir, repo_root): raise ValueError('the cohort directory must be outside the repository')
        self.dir = os.path.abspath(cohort_dir)

    def write(self, kind, rec):
        if kind not in EXTRACT_KINDS: raise ValueError(f'unknown extract kind {kind!r}')
        bad = id_fields(rec)
        if bad: raise ValueError(f'id fields are never written to evidence: {bad}')
        b = canonical(rec); h = sha256(b)
        d = os.path.join(self.dir, 'extracts', kind); os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f'{h}.json')
        try:
            with open(p, 'xb') as f: f.write(b)
        except FileExistsError:
            with open(p, 'rb') as f:
                if f.read() != b: raise ValueError(f'immutable extract {h} exists with different bytes')
        return h

    def records(self, kind):
        d = os.path.join(self.dir, 'extracts', kind)
        out = []
        for n in sorted(os.listdir(d)) if os.path.isdir(d) else ():
            with open(os.path.join(d, n), 'rb') as f: b = f.read()
            if sha256(b) + '.json' != n: raise ValueError(f'extract {n} does not match its sha256')
            out.append(json.loads(b))
        return out

    def finalize(self, out_dir=None):
        """Build preflight.json, decisions.csv, trades.csv and MANIFEST.sha256 (runtime columns only; rp_* / causal window /
        result stay empty for Cowork). Every file is written once (exclusive create). Returns {file: sha256}."""
        out = os.path.abspath(out_dir or self.dir)
        if _inside(out, REPO_ROOT): raise ValueError('evidence output must be outside the repository')
        os.makedirs(out, exist_ok=True)
        files = {}
        pf = sorted(self.records('preflight'), key=lambda r: str(r.get('recorded', {}).get('at_utc')))
        if pf: files['preflight.json'] = json.dumps(_enc(pf[-1]), sort_keys=True, indent=1).encode('ascii')
        dec = sorted(self.records('decision'), key=lambda r: (r['decision_bar'], r['symbol'], str(r.get('reason_logged_at'))))
        files['decisions.csv'] = _csv(DECISION_COLUMNS, [dict(
            symbol=r['symbol'], decision_bar=r['decision_bar'], scored=r['scored'], rt_le=r['le'], rt_lx=r['lx'], rt_atr=r['atr'],
            rt_outcome=r['outcome'], rt_reason=r['reason'], rt_reason_logged_at=r['reason_logged_at']) for r in dec])
        ent = {(r['symbol'], r['opened']): r for r in self.records('trade') if r.get('kind') == 'entry'}
        rows = {}
        for r in self.records('trade'):
            if r.get('kind') != 'trade': continue
            cur = rows.get(r['row_key_sha256'])                       # the measured revision wins over a pending one
            if cur is None or (cur['cost_status'] not in MEASURED and r['cost_status'] in MEASURED): rows[r['row_key_sha256']] = r
        tr = []
        for r in sorted(rows.values(), key=lambda r: (str(r['closed']), r['symbol'])):
            e = ent.get((r['symbol'], r['opened'])) or {}
            tr.append(dict(row_key_sha256=r['row_key_sha256'], symbol=r['symbol'], entry_bar=e.get('decision_bar'),
                           exit_bar=r['closed'], rt_why=r['exit_why'], rt_entry=r['entry'], rt_exit=r['exit'], rt_stop=e.get('stop_sent'),
                           stop_dist=e.get('stop_dist'), risk_usd=r['risk_usd'], sleeve_eq=e.get('sleeve_eq'),
                           risk_fraction=e.get('risk_fraction'), qty_raw=e.get('qty_raw'), qty_sent=e.get('qty_sent'),
                           step=e.get('step'), stop_raw=e.get('stop_raw'), tick=e.get('tick'),
                           fees_measured=r['fees_measured'], fees_model=r['fees_model'], funding_measured=r['funding_measured'],
                           rt_r=r['r_measured'], cost_status=r['cost_status']))
        files['trades.csv'] = _csv(TRADE_COLUMNS, tr)
        hashes = {}
        for n, b in files.items():
            with open(os.path.join(out, n), 'xb') as f: f.write(b)
            hashes[n] = sha256(b)
        man = ''.join(f'{h}  {n}\n' for n, h in sorted(hashes.items())).encode('ascii')
        with open(os.path.join(out, 'MANIFEST.sha256'), 'xb') as f: f.write(man)
        return hashes


def _csv(cols, rows):
    s = io.StringIO()
    w = csv.DictWriter(s, fieldnames=list(cols), lineterminator='\n')
    w.writeheader()
    for r in rows: w.writerow({k: ('' if r.get(k) is None else r.get(k)) for k in cols})
    return s.getvalue().encode('utf-8')
