"""CAND-EVICT-ACK-01 (contract contracts/cand_evict_ack_01, SIGNED by Codex 6517958103; pin amendment 6518282215).

Integration's acknowledgement of a durable pending-cost index entry (state['candidate_cost_pending']) whose history row
was evicted from history[-3000:] before its costs settled. Such an entry keeps its candidate family fail-closed for good
(factory_f01f02_family OQ6); this is the only path that clears it, and it is never triggered by time.

- Integration only, by a numeric GitHub comment id that is RECORDED, never fetched (no network call here).
- One row id per call, PAPER only, under the engine lock (an RLock; Engine.candidate_guard holds it too).
- Durable audited record state['candidate_cost_pending_ack'][row id] with content-strict fields + the sha256 of the
  strict canonical original; saved at once. A failed save rolls back the index and record subtrees exactly (the
  AUD-05 _persist_fault latch, state_untrusted / ENTRIES_PAUSED, is expected and stays).
- Idempotent. Never re-enables a slot, never touches P&L, history, cohort or evidence files, never stores ids.
"""
import hashlib
import json
import logging
import re
from datetime import datetime, timezone

import pandas as pd

log = logging.getLogger('zackbot')

ROUTE = '/api/candidate/evict_ack'
ACK_KEY = 'candidate_cost_pending_ack'
FIELDS = frozenset({'row_id', 'reason', 'comment_id'})
COMMENT_ID = re.compile(r'[0-9]{6,12}')
REASON_MAX = 200


def strict_sha256(entry):
    """sha256 of the strict canonical JSON of the original entry. Raises TypeError / ValueError when the entry is not
    strictly JSON-serializable (no default= fallback, no NaN / inf) - the caller refuses (A3.6a)."""
    return hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                                     allow_nan=False).encode('ascii')).hexdigest()


def canonical_closed(v):
    """A4.1: a str that pd.Timestamp parses to a real time -> canonical UTC isoformat (naive = UTC); anything else None.
    A non-str is never parsed (Cowork advisory at signing)."""
    if not isinstance(v, str): return None
    try: ts = pd.Timestamp(v)
    except (ValueError, TypeError, OverflowError): return None
    if ts is pd.NaT or ts != ts: return None
    ts = ts.tz_localize('UTC') if ts.tzinfo is None else ts.tz_convert('UTC')
    return ts.isoformat()


def sanitize(entry, slots, sym_re):
    """A4.1 content-strict fields of an index entry: sleeve only an exact slot id, symbol only a re.fullmatch of the
    engine's SYM_RE, closed only a canonical UTC time; otherwise None. Nothing else of the entry is kept."""
    e = entry if isinstance(entry, dict) else {}
    sl, sy = e.get('sleeve'), e.get('symbol')
    return dict(sleeve=sl if isinstance(sl, str) and sl in slots else None,
                symbol=sy if isinstance(sy, str) and sym_re.fullmatch(sy) else None,
                closed=canonical_closed(e.get('closed')))


def parse(b):
    """-> (row_id, reason, comment_id, None) or (None, None, None, refusal)."""
    if not isinstance(b, dict): return None, None, None, 'bad request'
    extra = sorted(str(k) for k in b if k not in FIELDS)
    if extra: return None, None, None, f"unknown field(s): {', '.join(extra)}"
    rid, reason, cid = b.get('row_id'), b.get('reason'), b.get('comment_id')
    if not isinstance(rid, str) or not rid.strip(): return None, None, None, 'row_id is required (one row id per call)'
    if not isinstance(reason, str) or not reason.strip(): return None, None, None, 'a reason is required'
    if len(reason.strip()) > REASON_MAX: return None, None, None, f'reason: at most {REASON_MAX} characters'
    if not isinstance(cid, str) or not COMMENT_ID.fullmatch(cid):
        return None, None, None, 'comment_id must be the numeric id of the Integration ruling comment (6-12 digits)'
    return rid, reason.strip(), cid, None


def ack(e, b, *, slots, sym_re, index_key):
    """The acknowledgement (A3-A6). -> (ok, message, record or None). Never raises for a bad request."""
    if getattr(e, 'live', True): return False, 'refused: candidate pending-cost acknowledgements are PAPER-only', None
    rid, reason, cid, why = parse(b)
    if why: return False, why, None
    with e.lock:
        st = e.state
        idx, acks = st.get(index_key), st.get(ACK_KEY)
        if not isinstance(idx, dict):
            return False, 'refused: the pending-cost index is missing or unreadable - it stays fail-closed', None
        if acks is not None and not isinstance(acks, dict):
            return False, 'refused: the acknowledgement record is unreadable - nothing changed (fail closed)', None
        if acks and rid in acks:                                               # A5: idempotent, no write, no save
            prev = acks[rid]
            first = prev.get('comment_id') if isinstance(prev, dict) else None
            msg = f'{rid} already acknowledged (comment {first})'
            if cid != first: msg += f'; comment {cid} not recorded - the first record stands'
            return True, msg, prev
        if rid not in idx: return False, f'refused: {rid} is not in the pending-cost index (unknown)', None
        if any(isinstance(h, dict) and h.get('id') == rid for h in e.history):
            return False, f'refused: {rid} is still in history (not evicted) - it settles by measurement or ack_unmeasured', None
        ent = idx[rid]
        try: sha = strict_sha256(ent)
        except (TypeError, ValueError) as ex:
            return False, (f'refused: the index entry of {rid} is not strictly JSON-serializable ({type(ex).__name__}) - '
                           'kept (fail closed); it needs a separate state repair'), None
        rec = dict(row_id=rid, entry=sanitize(ent, slots, sym_re), entry_sha256=sha, reason=reason, comment_id=cid,
                   t=datetime.now(timezone.utc).isoformat(timespec='seconds'), by='integration')
        snap = list(idx.items())                                               # exact order, for the rollback
        created = acks is None
        if created: acks = st[ACK_KEY] = {}
        acks[rid] = rec                                                        # A4.1 record, then A4.2 pop
        idx.pop(rid)
        try: saved = bool(e.save_state())
        except Exception: saved = False
        if not saved:                                                          # A4.3: only these two subtrees restored
            idx.clear(); idx.update(snap)
            acks.pop(rid, None)
            if created and not acks: st.pop(ACK_KEY, None)
            return False, (f'{rid}: state not saved - acknowledgement rolled back, the entry stays (fail closed); '
                           'entries may be paused until a successful save and an operator resume (AUD-05)'), None
        r = rec['entry']
        log.warning(f"candidate pending-cost entry {rid} ({r['symbol']} [{r['sleeve']}]) acknowledged by Integration "
                    f"(comment {cid}): {reason} - the slot is NOT re-enabled")
        return True, f'{rid} acknowledged (comment {cid}); the slot stays as it is - re-enable is a separate Integration action', rec


def api(e, b):
    """PAPER HMAC API (app.handle, behind do_POST's panel-token check). Raises ValueError on a refusal."""
    ok, msg, rec = e.ack_evicted_pending(b)
    if not ok: raise ValueError(msg)
    return dict(row_id=rec.get('row_id') if isinstance(rec, dict) else None, msg=msg, record=rec)
