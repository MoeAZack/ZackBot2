"""`zb-cost-model/2`: fees, signed funding, the frozen `slip-v1` slippage formula and the stress rows (RES-01 R3;
plan section 2). Pure functions, stdlib + the shared `feasibility.py` rounding; no clock, no I/O, no returns computed.

Cost rows are per cost class, and every symbol must be mapped to a class explicitly (`symbol_class`, normally derived
from the PIT universe's instrument classification with `symbol_class_from_universe`). There is no default class: an
unmapped symbol raises (Codex R3 ruling 1: never fall through to crypto defaults).
  crypto     VIP0 taker 0.05% / maker 0.02% per side (plan section 2), funding at each symbol's actual fundingTimes,
             24/7. Status PLAN.
  gold       XAUUSDT (owner decision: gold is IN the research). Binance VIP0 fee tier, its own observed 4h funding
             cadence (checked against the rows: `check_funding_cadence`), its own slip-v1 coefficient, and reference-
             session / gap labels (the perp trades 24/7, the reference gold market closes at weekends). PROVISIONAL
             until its spread / slippage / liquidity calibration exists.
  commodity  CL, BZ, NATGAS, XAG, XPT, XPD, COPPER and the tokenized gold perps: own row, UNCALIBRATED.
  equity     single stocks, ETFs and pre-IPO perps: own row, US-equity reference session, UNCALIBRATED.
  fx         USDBRL: own row, UNCALIBRATED.
An UNCALIBRATED row is a placeholder that carries labels; it must be calibrated before any result is promoted.

Fees: market entries/exits pay taker. A resting limit target fills as maker only with price-through or ordered-trade
evidence; a mere touch is no fill (the `slip-cal-v1` primary rule). `limit_no_fill` / `limit_as_taker` are named stress
rows, never an analyst choice. No maker entries in v1.

Funding: signed actual rate x mark notional at every actual funding time T the position spans, entry <= T < exit
(entry == T counts, exit == T does not). Longs pay a positive rate, shorts receive it. The mark is the funding-mark
proxy v1 (`pit.FUNDING_MARK_PROXY`, the close of the 1h mark bar ending at or before T; Codex ruling 3: a causal proxy,
labelled, stressed by `funding_mark_adverse`, to be compared with exchange-reported funding in forward/testnet records).
The legacy flat 0.005%/bar is only the `flat_funding_legacy` reproduction row.

slip-v1 (frozen): per side, bps of fill price, `slip_bps = max(2, c x 10^4 x VOL / close)` on the signal timeframe's
closed bars, `close` = the last closed bar's close. VOL is `TR-SMA14` (Codex ruling 2: the causal simple mean of the
last 14 true ranges, TR_i = max(h_i - l_i, |h_i - c_(i-1)|, |l_i - c_(i-1)|), exactly 15 closed bars read; it is NOT
Wilder's ATR). Standard Wilder ATR14 (`WILDER-ATR14`, seeded by the first 14-TR mean, then (prev x 13 + TR) / 14 over
every bar passed) is the sensitivity candidate, reported as the `slip_wilder_atr14` stress row. `c` comes from the frozen,
hashed calibration config `slip-cal-v1` (one value per cost class); `fitted: false` marks a coefficient that was set,
not fitted, and labels every result `SLIP-C-UNFITTED`. Slippage moves the fill adversely; the price is then rounded
adversely to the tick. A stop the bar opens through fills at the bar open (gap fill). Quantities are floored to the
step, never upsized (`feasibility.size_check`).
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

FORMAT = 'zb-cost-model/2'
SLIP_ID = 'slip-v1'
SLIP_FLOOR_BPS = 2.0
VOL_N = 14
SLIP_BARS = VOL_N + 1
TR_SMA14 = 'TR-SMA14'
WILDER_ATR14 = 'WILDER-ATR14'
TAKER = 0.0005
MAKER = 0.0002
LEGACY_FLAT_FUNDING = 0.00005                     # per bar held, the legacy reproduction row only
CAL_ID = 'slip-cal-v1'
LIMIT_TOUCH_RULE = 'touch = no fill; price-through or ordered-trade evidence = maker fill'
EVIDENCE = ('touch', 'price_through', 'ordered_trade')
STATUSES = ('PLAN', 'PROVISIONAL', 'UNCALIBRATED')
CAL_KEYS = {'id', 'target', 'source_series', 'estimator', 'loss', 'pooling', 'fallback', 'limit_touch_rule',
            'calibration_window', 'fitted', 'c'}
FUNDING_MARK_LABEL = 'FUNDING-MARK-PROXY-V1'


class CostError(ValueError):
    pass


@dataclass(frozen=True)
class CostRow:
    cost_class: str
    taker: float
    maker: float
    fee_tier: str
    funding: str                                   # 'actual' (signed rate at each actual funding time)
    funding_cadence_hours: int | None              # observed cadence the rows must show; None = per-symbol actual
    hours: str                                     # trading hours of the perp
    reference_session: str                         # the underlying's reference market session (gap label source)
    status: str                                    # PLAN | PROVISIONAL | UNCALIBRATED
    note: str = ''


CRYPTO = CostRow('crypto', TAKER, MAKER, 'VIP0', 'actual', None, '24/7', 'none (24/7 underlying)', 'PLAN',
                 'plan section 2')
GOLD = CostRow('gold', TAKER, MAKER, 'VIP0', 'actual', 4, '24/7',
               'spot gold reference market: closed at weekends (WEEKEND-REFERENCE-GAP)', 'PROVISIONAL',
               'Codex ruling 1 (6077894871): Binance fee tier, own observed 4h funding, own slip coefficient, '
               'reference-session/gap labels; spread/slippage/liquidity calibration pending')
COMMODITY = CostRow('commodity', TAKER, MAKER, 'VIP0', 'actual', None, '24/7',
                    'commodity futures reference sessions (per underlying)', 'UNCALIBRATED',
                    'classified placeholder; calibrate before promotion')
EQUITY = CostRow('equity', TAKER, MAKER, 'VIP0', 'actual', None, '24/7',
                 'US/KR/HK equity exchange sessions (per underlying); weekends and holidays closed', 'UNCALIBRATED',
                 'classified placeholder; calibrate before promotion')
FX = CostRow('fx', TAKER, MAKER, 'VIP0', 'actual', None, '24/7', 'FX reference market: closed at weekends',
             'UNCALIBRATED', 'classified placeholder; calibrate before promotion')
DEFAULT_ROWS = {r.cost_class: r for r in (CRYPTO, GOLD, COMMODITY, EQUITY, FX)}


def symbol_class_from_universe(u: dict) -> dict:
    """symbol -> cost class from a `zb-pit-universe/2` classification; unclassified symbols get no class."""
    out = {}
    for s in u['symbols']:
        cls, sub = s['class'], s['subclass']
        if cls == 'crypto':
            out[s['symbol']] = 'crypto'
        elif cls == 'gold-commodity':
            out[s['symbol']] = 'gold' if sub == 'gold-spot' else 'commodity'
        elif cls in ('equity', 'fx'):
            out[s['symbol']] = cls
    return dict(sorted(out.items()))


@dataclass(frozen=True)
class Scenario:
    name: str
    fee_mult: float = 1.0
    slip_mult: float = 1.0
    gap_slip_mult: float = 1.0                     # extra multiplier on gap/stop fills only
    funding_mult: float = 1.0                      # sign kept
    funding_mode: str = 'actual'                   # 'actual' | 'flat_legacy'
    limit_rule: str = 'primary'                    # 'primary' | 'no_fill' | 'taker'
    vol: str = TR_SMA14                            # slip-v1 volatility estimator
    mark_adverse_bps: float = 0.0                  # funding-mark proxy moved against the position


STRESS = {s.name: s for s in (
    Scenario('base'),
    Scenario('fees_slip_x2', fee_mult=2.0, slip_mult=2.0),
    Scenario('funding_x2', funding_mult=2.0),
    Scenario('gap_slip_x5', gap_slip_mult=5.0),
    Scenario('limit_no_fill', limit_rule='no_fill'),
    Scenario('limit_as_taker', limit_rule='taker'),
    Scenario('flat_funding_legacy', funding_mode='flat_legacy'),
    Scenario('slip_wilder_atr14', vol=WILDER_ATR14),
    Scenario('funding_mark_adverse', mark_adverse_bps=50.0),
)}


def validate_calibration(cal: dict, classes) -> None:
    """`slip-cal-v1` is fully frozen: every field named, the one primary limit-touch rule, c > 0 for every class."""
    if not isinstance(cal, dict) or set(cal) != CAL_KEYS:
        raise CostError(f'{CAL_ID} must have exactly the keys {sorted(CAL_KEYS)}')
    if cal['id'] != CAL_ID or cal['limit_touch_rule'] != LIMIT_TOUCH_RULE:
        raise CostError(f'id must be {CAL_ID!r} and limit_touch_rule must be the one primary rule')
    for k in CAL_KEYS - {'id', 'c', 'calibration_window', 'fitted'}:
        if not isinstance(cal[k], str) or not cal[k]:
            raise CostError(f'{CAL_ID}.{k} must be a non-empty string')
    if type(cal['fitted']) is not bool:
        raise CostError(f'{CAL_ID}.fitted must be a bool (false = c was set, not fitted)')
    w = cal['calibration_window']
    if not isinstance(w, dict) or set(w) != {'start', 'end'}:
        raise CostError(f'{CAL_ID}.calibration_window must be {{start, end}} UTC')
    c = cal['c']
    if not isinstance(c, dict) or set(c) != set(classes):
        raise CostError(f'{CAL_ID}.c needs exactly one coefficient per cost class {sorted(classes)}')
    if any(type(v) is not float or not math.isfinite(v) or v <= 0 for v in c.values()):
        raise CostError(f'{CAL_ID}.c values must be finite floats > 0')


class CostModel:
    def __init__(self, calibration: dict, symbol_class: dict, rows=None, vol_estimator: str = TR_SMA14):
        rows = dict(DEFAULT_ROWS if rows is None else rows)
        sc = dict(symbol_class)
        if any(c not in rows for c in sc.values()):
            raise CostError('every class named by symbol_class needs a cost row')
        for k, r in rows.items():
            if not isinstance(r, CostRow) or r.cost_class != k or r.status not in STATUSES or r.funding != 'actual':
                raise CostError(f'cost row {k!r} is malformed')
            if not (0 <= r.maker <= r.taker < 0.01):
                raise CostError(f'cost row {k!r}: need 0 <= maker <= taker < 1%')
            if r.funding_cadence_hours is not None and (type(r.funding_cadence_hours) is not int
                                                        or r.funding_cadence_hours <= 0):
                raise CostError(f'cost row {k!r}: funding_cadence_hours must be a positive int or None')
        if vol_estimator not in VOL:
            raise CostError(f'vol_estimator must be one of {sorted(VOL)}')
        validate_calibration(calibration, rows)
        self.rows, self.symbol_class, self.calibration = rows, sc, dict(calibration)
        self.vol_estimator = vol_estimator
        self.cal_digest = hashlib.sha256(M.canonical(self.calibration)).hexdigest()
        self.doc = {'format': FORMAT, 'slip': SLIP_ID, 'slip_cal_digest': self.cal_digest, 'vol_estimator': vol_estimator,
                    'funding_mark': FUNDING_MARK_LABEL,
                    'rows': {k: asdict(r) for k, r in sorted(rows.items())}, 'symbol_class': dict(sorted(sc.items())),
                    'stress': {k: asdict(s) for k, s in STRESS.items()}}
        self.digest = hashlib.sha256(M.canonical(self.doc)).hexdigest()

    def cost_class(self, symbol: str) -> str:
        try:
            return self.symbol_class[symbol]
        except KeyError:
            raise CostError(f'{symbol} has no cost class (no silent crypto fallback; classify it first)')

    def row(self, symbol: str) -> CostRow:
        return self.rows[self.cost_class(symbol)]

    def slip_c(self, symbol: str) -> float:
        return self.calibration['c'][self.cost_class(symbol)]

    def labels(self, symbol: str) -> list[str]:
        r = self.row(symbol)
        out = [FUNDING_MARK_LABEL, f'SLIP-VOL-{self.vol_estimator}']
        if r.status != 'PLAN':
            out.append(f'COST-{r.cost_class.upper()}-{r.status}')
        if not self.calibration['fitted']:
            out.append('SLIP-C-UNFITTED')
        if r.reference_session.endswith('(WEEKEND-REFERENCE-GAP)'):
            out.append('WEEKEND-REFERENCE-GAP')
        return out

    def check_funding_cadence(self, symbol: str, funding_rows) -> None:
        """A row with a declared cadence (gold: 4h) must match every funding row's interval; no assumed schedule."""
        want = self.row(symbol).funding_cadence_hours
        if want is None:
            return
        bad = sorted({r.interval_hours for r in funding_rows if r.interval_hours != want})
        if bad:
            raise CostError(f'{symbol}: funding rows show interval {bad}h, the {self.cost_class(symbol)} row declares '
                            f'{want}h')


# ------------------------------------------------------------------ slip-v1
def _true_ranges(bars):
    return [max(x.high - x.low, abs(x.high - p.close), abs(x.low - p.close)) for p, x in zip(bars, bars[1:])]


def tr_sma14(bars) -> float:
    """TR-SMA14: the simple mean of the last 14 true ranges over the last 15 closed bars (oldest first). Not Wilder."""
    if len(bars) < SLIP_BARS:
        raise CostError(f'{TR_SMA14} needs {SLIP_BARS} closed bars, got {len(bars)}')
    return math.fsum(_true_ranges(bars[-SLIP_BARS:])) / VOL_N


def wilder_atr14(bars) -> float:
    """Standard Wilder ATR14 over every bar passed: seed = mean of the first 14 TRs, then (prev x 13 + TR) / 14."""
    if len(bars) < SLIP_BARS:
        raise CostError(f'{WILDER_ATR14} needs at least {SLIP_BARS} closed bars, got {len(bars)}')
    tr = _true_ranges(bars)
    atr = math.fsum(tr[:VOL_N]) / VOL_N
    for x in tr[VOL_N:]:
        atr = (atr * (VOL_N - 1) + x) / VOL_N
    return atr


VOL = {TR_SMA14: tr_sma14, WILDER_ATR14: wilder_atr14}


def slip_bps(bars, c: float, vol: str = TR_SMA14) -> float:
    close = bars[-1].close
    if not close > 0:
        raise CostError('close must be > 0')
    if vol not in VOL:
        raise CostError(f'vol must be one of {sorted(VOL)}')
    return max(SLIP_FLOOR_BPS, c * 1e4 * VOL[vol](bars) / close)


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
    """Funding paid (+) or received (-). `events` = [(time_ms, rate, mark_px)] the position spans (see module doc).
    `funding_mark_adverse` moves the proxy mark against the position (more paid, less received)."""
    if side not in ('long', 'short'):
        raise CostError("side must be 'long' or 'short'")
    if scen.funding_mode == 'flat_legacy':
        return LEGACY_FLAT_FUNDING * abs(entry_notional) * bars_held
    sign = 1.0 if side == 'long' else -1.0
    k = scen.mark_adverse_bps / 1e4
    return math.fsum(sign * rate * abs(qty) * mark * (1 + k if sign * rate > 0 else 1 - k)
                     for _, rate, mark in events) * scen.funding_mult


def size(qty_raw: float, px: float, rule) -> dict:
    """Floor to the step and check min qty / notional (never upsized); a refusal is counted by its `code`."""
    return F.size_check(qty_raw, qty_raw, px, rule)
