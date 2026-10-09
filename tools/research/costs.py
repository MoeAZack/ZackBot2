"""`zb-cost-model/1`: fees, signed funding, the frozen `slip-v1` slippage formula and the stress rows (RES-01 R3;
plan section 2). Pure functions, stdlib + the shared `feasibility.py` rounding; no clock, no I/O, no returns computed.

Cost rows are per cost class; a symbol maps to a class (`symbol_class`, default class `crypto`), so a TradFi perp can
carry its own fee / funding / hours row without touching the others.
  crypto       VIP0 taker 0.05% / maker 0.02% per side (plan section 2), funding at the real `fundingTime`s, 24/7.
  tradfi_gold  XAUUSDT. Owner decision: gold is IN the research. Values equal the crypto row and the row is labelled
               PROVISIONAL until Codex rules (PR #51) whether TradFi perps need their own cost / funding / hours row.

Fees: market entries/exits pay taker. A resting limit target fills as maker only with price-through or ordered-trade
evidence; a mere touch is no fill (the `slip-cal-v1` primary rule). `limit_no_fill` / `limit_as_taker` are named stress
rows, never an analyst choice. No maker entries in v1.

Funding: signed actual rate x mark notional at every `fundingTime` T the position spans, entry <= T < exit (entry == T
counts, exit == T does not). Longs pay a positive rate, shorts receive it. The mark is the close of the 1h mark bar
available at T (pit.View.funding_events joins it, no forward fill across a gap). The legacy flat 0.005%/bar is only the
`flat_funding_legacy` reproduction row.

slip-v1 (frozen): per side, bps of fill price, `slip_bps = max(2, c x 10^4 x ATR14 / close)` on the signal timeframe's
closed bars. ATR14 = the simple mean of the last 14 true ranges, TR_i = max(h_i - l_i, |h_i - c_(i-1)|, |l_i - c_(i-1)|),
so exactly 15 closed bars are read; `close` = the last closed bar's close. `c` comes from the frozen, hashed calibration
config `slip-cal-v1` (one value per cost class; fitted once on its own interval, never per candidate). Slippage moves
the fill adversely; the price is then rounded adversely to the tick. A stop the bar opens through fills at the bar open
(gap fill). Quantities are floored to the step, never upsized (`feasibility.size_check`).
"""
from __future__ import annotations

import hashlib
import math
import os
import sys
from dataclasses import asdict, dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import manifest as M                                                                        # noqa: E402

sys.path.insert(0, M.REPO)
import feasibility as F                                                                     # noqa: E402

FORMAT = 'zb-cost-model/1'
SLIP_ID = 'slip-v1'
SLIP_FLOOR_BPS = 2.0
ATR_N = 14
SLIP_BARS = ATR_N + 1
TAKER = 0.0005
MAKER = 0.0002
LEGACY_FLAT_FUNDING = 0.00005                     # per bar held, the legacy reproduction row only
CAL_ID = 'slip-cal-v1'
LIMIT_TOUCH_RULE = 'touch = no fill; price-through or ordered-trade evidence = maker fill'
EVIDENCE = ('touch', 'price_through', 'ordered_trade')
STATUSES = ('PLAN', 'PROVISIONAL')
CAL_KEYS = {'id', 'target', 'source_series', 'estimator', 'loss', 'pooling', 'fallback', 'limit_touch_rule',
            'calibration_window', 'c'}


class CostError(ValueError):
    pass


@dataclass(frozen=True)
class CostRow:
    cost_class: str
    taker: float
    maker: float
    fee_tier: str
    funding: str                                   # 'actual' (signed rate at fundingTime)
    hours: str                                     # trading hours; '24/7' for crypto
    status: str                                    # PLAN | PROVISIONAL
    note: str = ''


CRYPTO = CostRow('crypto', TAKER, MAKER, 'VIP0', 'actual', '24/7', 'PLAN', 'plan section 2')
GOLD = CostRow('tradfi_gold', TAKER, MAKER, 'VIP0', 'actual', '24/7', 'PROVISIONAL',
               'owner: XAUUSDT included in the research; defaults = crypto row pending the Codex ruling on TradFi perps '
               '(PR #51: own cost / funding / hours row?)')
DEFAULT_ROWS = {r.cost_class: r for r in (CRYPTO, GOLD)}
DEFAULT_SYMBOL_CLASS = {'XAUUSDT': 'tradfi_gold'}


@dataclass(frozen=True)
class Scenario:
    name: str
    fee_mult: float = 1.0
    slip_mult: float = 1.0
    gap_slip_mult: float = 1.0                     # extra multiplier on gap/stop fills only
    funding_mult: float = 1.0                      # sign kept
    funding_mode: str = 'actual'                   # 'actual' | 'flat_legacy'
    limit_rule: str = 'primary'                    # 'primary' | 'no_fill' | 'taker'


STRESS = {s.name: s for s in (
    Scenario('base'),
    Scenario('fees_slip_x2', fee_mult=2.0, slip_mult=2.0),
    Scenario('funding_x2', funding_mult=2.0),
    Scenario('gap_slip_x5', gap_slip_mult=5.0),
    Scenario('limit_no_fill', limit_rule='no_fill'),
    Scenario('limit_as_taker', limit_rule='taker'),
    Scenario('flat_funding_legacy', funding_mode='flat_legacy'),
)}


def validate_calibration(cal: dict, classes) -> None:
    """`slip-cal-v1` is fully frozen: every field named, the one primary limit-touch rule, c > 0 for every class."""
    if not isinstance(cal, dict) or set(cal) != CAL_KEYS:
        raise CostError(f'{CAL_ID} must have exactly the keys {sorted(CAL_KEYS)}')
    if cal['id'] != CAL_ID or cal['limit_touch_rule'] != LIMIT_TOUCH_RULE:
        raise CostError(f'id must be {CAL_ID!r} and limit_touch_rule must be the one primary rule')
    for k in CAL_KEYS - {'id', 'c', 'calibration_window'}:
        if not isinstance(cal[k], str) or not cal[k]:
            raise CostError(f'{CAL_ID}.{k} must be a non-empty string')
    w = cal['calibration_window']
    if not isinstance(w, dict) or set(w) != {'start', 'end'}:
        raise CostError(f'{CAL_ID}.calibration_window must be {{start, end}} UTC')
    c = cal['c']
    if not isinstance(c, dict) or set(c) != set(classes):
        raise CostError(f'{CAL_ID}.c needs exactly one coefficient per cost class {sorted(classes)}')
    if any(type(v) is not float or not math.isfinite(v) or v <= 0 for v in c.values()):
        raise CostError(f'{CAL_ID}.c values must be finite floats > 0')


class CostModel:
    def __init__(self, calibration: dict, rows=None, symbol_class=None, default_class: str = 'crypto'):
        rows = dict(DEFAULT_ROWS if rows is None else rows)
        sc = dict(DEFAULT_SYMBOL_CLASS if symbol_class is None else symbol_class)
        if default_class not in rows or any(c not in rows for c in sc.values()):
            raise CostError('every class named by symbol_class / default_class needs a cost row')
        for k, r in rows.items():
            if not isinstance(r, CostRow) or r.cost_class != k or r.status not in STATUSES or r.funding != 'actual':
                raise CostError(f'cost row {k!r} is malformed')
            if not (0 <= r.maker <= r.taker < 0.01):
                raise CostError(f'cost row {k!r}: need 0 <= maker <= taker < 1%')
        validate_calibration(calibration, rows)
        self.rows, self.symbol_class, self.default_class, self.calibration = rows, sc, default_class, dict(calibration)
        self.cal_digest = hashlib.sha256(M.canonical(self.calibration)).hexdigest()
        self.doc = {'format': FORMAT, 'slip': SLIP_ID, 'slip_cal_digest': self.cal_digest, 'default_class': default_class,
                    'rows': {k: asdict(r) for k, r in sorted(rows.items())}, 'symbol_class': dict(sorted(sc.items())),
                    'stress': {k: asdict(s) for k, s in STRESS.items()}}
        self.digest = hashlib.sha256(M.canonical(self.doc)).hexdigest()

    def cost_class(self, symbol: str) -> str:
        return self.symbol_class.get(symbol, self.default_class)

    def row(self, symbol: str) -> CostRow:
        return self.rows[self.cost_class(symbol)]

    def slip_c(self, symbol: str) -> float:
        return self.calibration['c'][self.cost_class(symbol)]

    def labels(self, symbol: str) -> list[str]:
        r = self.row(symbol)
        return [f'COST-{r.cost_class.upper()}-{r.status}'] if r.status != 'PLAN' else []


# ------------------------------------------------------------------ slip-v1
def atr14(bars) -> float:
    """Simple mean of the last 14 true ranges over the last 15 closed bars (`Bar` tuples, oldest first)."""
    if len(bars) < SLIP_BARS:
        raise CostError(f'{SLIP_ID} needs {SLIP_BARS} closed bars, got {len(bars)}')
    b = bars[-SLIP_BARS:]
    tr = [max(x.high - x.low, abs(x.high - p.close), abs(x.low - p.close)) for p, x in zip(b, b[1:])]
    return math.fsum(tr) / ATR_N


def slip_bps(bars, c: float) -> float:
    close = bars[-1].close
    if not close > 0:
        raise CostError('close must be > 0')
    return max(SLIP_FLOOR_BPS, c * 1e4 * atr14(bars) / close)


def round_adverse(px: float, tick, action: str) -> float:
    """A buy fills no better than the next tick up, a sell no better than the tick below."""
    if not tick:
        return px
    n = px / tick
    k = math.ceil(n - 1e-9) if action == 'buy' else math.floor(n + 1e-9)
    dec = len(f'{tick:.12f}'.rstrip('0').split('.')[1])
    return round(k * tick, dec)


def fill_price(ref: float, action: str, bps: float, scen: Scenario, *, gap=False, tick=None) -> float:
    """Reference price moved adversely by `bps` x the scenario multipliers, then rounded adversely to the tick."""
    if action not in ('buy', 'sell'):
        raise CostError("action must be 'buy' or 'sell'")
    k = bps * scen.slip_mult * (scen.gap_slip_mult if gap else 1.0) / 1e4
    return round_adverse(ref * (1 + k) if action == 'buy' else ref * (1 - k), tick, action)


def stop_reference(side: str, stop: float, bar_open: float) -> tuple[float, bool]:
    """(reference price, gap) for a stop hit in a bar: a bar that opens through the stop fills at its open."""
    if side == 'long':
        return (bar_open, True) if bar_open <= stop else (stop, False)
    if side == 'short':
        return (bar_open, True) if bar_open >= stop else (stop, False)
    raise CostError("side must be 'long' or 'short'")


def limit_fill(evidence: str, scen: Scenario):
    """Liquidity of a resting limit target: None = no fill, 'maker' or 'taker'."""
    if evidence not in EVIDENCE:
        raise CostError(f'evidence must be one of {EVIDENCE}')
    if scen.limit_rule == 'no_fill':
        return None
    if scen.limit_rule == 'taker':
        return 'taker'
    return None if evidence == 'touch' else 'maker'


def fee(notional: float, liquidity: str, row: CostRow, scen: Scenario) -> float:
    if liquidity not in ('taker', 'maker'):
        raise CostError("liquidity must be 'taker' or 'maker'")
    return abs(notional) * (row.taker if liquidity == 'taker' else row.maker) * scen.fee_mult


def funding_cost(side: str, qty: float, events, scen: Scenario, *, bars_held: int = 0, entry_notional: float = 0.0):
    """Funding paid (+) or received (-). `events` = [(time_ms, rate, mark_px)] the position spans (see module doc)."""
    if side not in ('long', 'short'):
        raise CostError("side must be 'long' or 'short'")
    if scen.funding_mode == 'flat_legacy':
        return LEGACY_FLAT_FUNDING * abs(entry_notional) * bars_held
    sign = 1.0 if side == 'long' else -1.0
    return math.fsum(sign * rate * abs(qty) * mark for _, rate, mark in events) * scen.funding_mult


def size(qty_raw: float, px: float, rule) -> dict:
    """Floor to the step and check min qty / notional (never upsized); a refusal is counted by its `code`."""
    return F.size_check(qty_raw, qty_raw, px, rule)
