"""Deterministic run outputs derived from the journal fold (plus the venue's fill / funding truth for the outcome).

    trades.csv       one row per closed lot (TradeOutcome), sorted by (entry time, symbol, lot id); Decimals as their
                     exact text; times as integer UTC ms and ISO UTC. The same journal and venue -> the same bytes.
    incidents.jsonl  one JSON object per line, sorted keys: first every incident the JOURNAL proves (mode changes,
                     refusals, stop failures, risk halts / kills, lost-intent resolutions, intents closed rejected /
                     not sent, unknown / not-found results) in sequence order, then the process's own incidents
                     (hard HOLD, emergency-set actions, invariant breaches: not journaled by design).
    health line      one line per cycle: Cairo time first (UTC in parentheses), mode, HOLD kind, positions, whether
                     every position is covered by owned reduce-only stops on the venue, counters.
Files are written to a temp name and renamed into place, so a reader never sees half a file.
"""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

from newcore.domain import (Action, DecisionRecorded, IntentState, IntentStateChanged, ModeChanged, ReasonCode,
                            ResultObserved, ResultPhase)
from newcore.ports.venue import ReadKind
from newcore.risk import CAIRO

from . import ids

TRADE_COLUMNS = ('symbol', 'side', 'lot_id', 'signal_close_ms', 'entry_ms', 'entry_utc', 'exit_ms', 'exit_utc',
                 'exit_signal_close_ms', 'qty', 'entry_price', 'exit_price', 'stop_price', 'risk_distance', 'risk_usd',
                 'gross', 'fees', 'funding', 'pnl', 'r', 'exit_code', 'exit_reason', 'entry_reason')


def utc_text(ms):
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')


def cairo_text(ms):
    t = datetime.fromtimestamp(ms // 1000, tz=timezone.utc)
    return f'{t.astimezone(CAIRO):%Y-%m-%d %H:%M} Cairo ({t:%H:%M} UTC)'


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='') as fh:
        fh.write(text)
    os.replace(tmp, path)


def trades_csv(runner) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator='\n')
    w.writerow(TRADE_COLUMNS)
    for t in sorted(runner.trades(), key=lambda t: (t.entry_ms, t.symbol, t.lot_id)):
        w.writerow([t.symbol, t.side, t.lot_id, t.signal_close_ms, t.entry_ms, utc_text(t.entry_ms), t.exit_ms,
                    utc_text(t.exit_ms), '' if t.exit_signal_close_ms is None else t.exit_signal_close_ms, t.qty,
                    t.entry_price, t.exit_price, t.stop_price, t.risk_distance, t.risk_usd, t.gross, t.fees,
                    t.funding, t.pnl, t.r, t.exit_code or '', t.exit_reason.value, t.reason_codes[0]])
    return out.getvalue()


def journal_incidents(events):
    """The incidents the journal itself proves, in sequence order."""
    out = []
    for ev in events:
        base = {'sequence': ev.sequence, 'at_ms': ev.at_ms, 'at_utc': utc_text(ev.at_ms), 'source': 'journal'}
        if isinstance(ev, ModeChanged):
            out.append(dict(base, kind='mode_changed', to_mode=ev.to_mode.value,
                            hold_kind=None if ev.to_hold is None else ev.to_hold.value,
                            reasons=[r.value for r in ev.reasons], decision_id=ev.decision_id))
        elif isinstance(ev, DecisionRecorded):
            d = ev.decision
            if d.action in (Action.SKIP, Action.WAIT, Action.HALT, Action.RECONCILE, Action.RESUME) or \
                    d.reason is ReasonCode.EXIT_STOP_FAILED:
                out.append(dict(base, kind=f'decision_{d.action.value}', reason=d.reason.value, symbol=d.symbol,
                                side=None if d.side is None else d.side.value, decision_id=d.decision_id,
                                detail=d.detail))
        elif isinstance(ev, IntentStateChanged) and ev.to_state in (IntentState.REJECTED, IntentState.NOT_SENT):
            out.append(dict(base, kind=f'intent_{ev.to_state.value}', intent_id=ev.intent_id, reason=ev.reason.value))
        elif isinstance(ev, ResultObserved) and ev.result.phase is ResultPhase.UNKNOWN:
            r = ev.result
            out.append(dict(base, kind='result_not_found' if r.lookup is not None else 'result_unknown',
                            intent_id=r.intent_id, client_order_id=r.client_order_id))
    return out


def incidents_jsonl(runner) -> str:
    rows = journal_incidents(runner.journal.read())
    rows += [{'source': 'process', 'kind': 'process', 'at_ms': at, 'at_utc': None if at is None else utc_text(at),
              'text': text} for at, text in runner.incidents]
    return ''.join(json.dumps(r, sort_keys=True, separators=(',', ':')) + '\n' for r in rows)


def write_reports(runner, out_dir):
    _atomic_write(os.path.join(out_dir, 'trades.csv'), trades_csv(runner))
    _atomic_write(os.path.join(out_dir, 'incidents.jsonl'), incidents_jsonl(runner))


def health_line(runner) -> str:
    """mode, HOLD kind, positions, protected?, counters (one line)."""
    f, rec = runner.fold, runner.last_rec
    if runner.hard_hold is not None:
        mode, hold = 'HOLD', 'durability_unavailable'
    else:
        mode, hold = f.mode.value.upper(), (f.hold.value if f.hold is not None else '-')
    positions, protected = [], 'unknown'
    if rec is not None and rec.positions is not None and rec.orders is not None:
        owned = lambda cid: cid in f.by_client_id or ids.is_emergency_client_id(cid)
        naked = []
        for p in rec.positions:
            if p.qty > 0:
                positions.append(f'{p.symbol}:{p.side}:{p.qty}')
                cover = sum((o.qty for o in rec.orders if o.reduce and o.order_type == 'STOP_MARKET'
                             and (o.ref.symbol, o.position_side) == (p.symbol, p.side) and owned(o.ref.client_id)),
                            Decimal(0))
                if cover < p.qty:
                    naked.append(p.symbol)
        protected = 'yes' if not naked else 'NO(' + ','.join(naked) + ')'
    eq = runner.reads.equity()
    equity = eq.value[0] if eq.kind is ReadKind.OK else 'unknown'
    c = runner.counters
    when = cairo_text(runner.now) if runner.now is not None else '-'
    return (f'HEALTH {when} mode={mode} hold={hold} positions={",".join(positions) or "flat"} '
            f'protected={protected} equity={equity} trades={sum(1 for x in f.lots() if not x.open)} '
            f'cycles={c.cycles} incidents={c.incidents} stops={runner.stop_routes_text()}'
            + (' degraded=' + ','.join(f'{s}:{sd}:{how}' for (s, sd), how in sorted(runner.degraded.items()))
               if getattr(runner, 'degraded', None) else ''))
