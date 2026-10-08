"""S5 READ-ONLY smoke: the sequence of reads the owner-run tools/newcore_smoke.py performs, and its summary.

run_smoke() only ever calls the read methods in READ_ONLY_CALLS. It never places, cancels or queries an order and
never changes leverage, margin or position mode (the transport has no such methods; tests also prove the order
methods stay untouched). Any read that is not OK stops the run with SmokeReadFailed (no retry, no loop).
"""
from dataclasses import dataclass, field

from .income import DAY_MS, income_history, summarize_income
from .outcomes import ReadKind

CORE8 = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT')
READ_ONLY_CALLS = frozenset({'server_time', 'exchange_info', 'account', 'positions', 'dual_side_position',
                             'open_orders', 'open_algo_orders', 'income'})
NOT_FLAT = 'account not flat — NEWCORE first INIT will HOLD'


class SmokeReadFailed(Exception):
    """A read did not return trustworthy data. step names the read; outcome is the ReadOutcome (or None)."""

    def __init__(self, step, outcome=None, detail=None):
        super().__init__(step)
        self.step, self.outcome, self.detail = step, outcome, detail

    def describe(self):
        o = self.outcome
        if o is None:
            return f'{self.step}: {self.detail or "failed"}'
        if o.kind is ReadKind.REJECTED and o.error is not None:
            e = o.error
            hint = ' (local clock out of sync with Binance; run again)' if e.code == -1021 else ''
            return f'{self.step}: REJECTED by Binance, code {e.code} {e.name} [{e.category.value}]{hint}'
        return f'{self.step}: UNKNOWN ({o.unknown_reason or "no trustworthy answer"})'


@dataclass
class SmokeReport:
    offset_ms: int = 0
    applied_offset_ms: int = 0
    rtt_ms: int = 0
    rules: dict = field(default_factory=dict)
    missing_symbols: tuple = ()
    account: object = None
    positions: tuple = ()                 # non-zero rows only
    hedge: object = None
    open_orders: tuple = ()
    open_algo_orders: tuple = ()
    income: object = None
    income_rows: int = 0
    income_start_ms: int = 0
    income_end_ms: int = 0
    max_weight_1m: object = None
    warnings: list = field(default_factory=list)

    @property
    def flat(self):
        return not self.positions and not self.open_orders and not self.open_algo_orders


def run_smoke(transport, offset_clock, *, symbols=CORE8, income_days=7, calibration_samples=3):
    rep = SmokeReport()
    weights = []

    def need(step, outcome):
        w = outcome.rate.weight('1m') if outcome.rate is not None else None
        if w is not None:
            weights.append(w)
        if outcome.kind is not ReadKind.OK:
            raise SmokeReadFailed(step, outcome)
        return outcome.value

    m = offset_clock.resync(transport, samples=calibration_samples)
    if not m.ok:
        raise SmokeReadFailed('server_time (clock calibration)', None, m.reason)
    rep.offset_ms, rep.applied_offset_ms, rep.rtt_ms = m.offset_ms, m.applied_offset_ms, m.rtt_ms

    info = need('exchange_info', transport.exchange_info())
    rep.rules = {s: info.symbols[s] for s in symbols if s in info.symbols}
    rep.missing_symbols = tuple(s for s in symbols if s not in info.symbols)
    if rep.missing_symbols:
        rep.warnings.append('no usable rules on testnet for: ' + ', '.join(rep.missing_symbols))

    rep.account = need('account', transport.account())
    if not rep.account.can_trade:
        rep.warnings.append('account reports canTrade=false')

    rep.positions = tuple(p for p in need('positions', transport.positions()) if p.position_amt != 0)
    rep.hedge = need('dual_side_position', transport.dual_side_position())
    if rep.hedge is not True:
        rep.warnings.append('position mode is ONE-WAY: NEWCORE expects hedge mode (dualSidePosition=true)')
    rep.open_orders = need('open_orders', transport.open_orders())
    rep.open_algo_orders = need('open_algo_orders', transport.open_algo_orders())
    if not rep.flat:
        rep.warnings.append(NOT_FLAT)

    end = offset_clock()
    start = end - income_days * DAY_MS + 1
    rows = need('income (7-day window)', income_history(transport, start_ms=start, end_ms=end))
    rep.income, rep.income_rows, rep.income_start_ms, rep.income_end_ms = summarize_income(rows), len(rows), start, end
    rep.max_weight_1m = max(weights) if weights else None
    return rep


def fmt_cairo(ms):
    """Cairo local time first (owner preference), UTC in parentheses; UTC only when tz data is missing."""
    import datetime as dt
    utc = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return f'{utc.astimezone(ZoneInfo("Africa/Cairo")):%Y-%m-%d %H:%M} Cairo ({utc:%H:%M} UTC)'
    except Exception:
        return f'{utc:%Y-%m-%d %H:%M} UTC'


def _n(d):
    """Decimal for display: no exponent, no trailing zeros ('0.0100' -> '0.01', '100.00000000' -> '100')."""
    s = format(d.normalize(), 'f')
    return '0' if s in ('-0', '') else s


def format_report(rep, *, environment, masked_key, key_digest):
    L = [f'environment   : {environment}   key {masked_key}   digest {key_digest[:17]}…',
         f'clock         : server offset {rep.offset_ms:+d} ms, RTT {rep.rtt_ms} ms '
         f'(signing with {rep.applied_offset_ms:+d} ms)',
         f'position mode : {"HEDGE" if rep.hedge else "ONE-WAY"}' + ('' if rep.hedge else '   WARNING')]
    bal = [a for a in rep.account.assets if a.wallet_balance != 0 or a.available_balance != 0]
    L.append('balances      : ' + (', '.join(f'{a.asset} wallet {_n(a.wallet_balance)} / available '
                                             f'{_n(a.available_balance)}' for a in bal) or 'none'))
    L.append('positions     : ' + (', '.join(f'{p.symbol} {p.position_side} {_n(p.position_amt)} @ '
                                             f'{_n(p.entry_price)}' for p in rep.positions) or 'flat'))
    L.append(f'open orders   : {len(rep.open_orders)} classic, {len(rep.open_algo_orders)} algo/conditional')
    s = rep.income
    others = ', '.join(f'{t} {_n(s.total(t))}' for t in s.other_types())
    L.append(f'income 7d     : funding {_n(s.funding())} USDT, commission {_n(s.commission())} USDT, realized pnl '
             f'{_n(s.realized_pnl())} USDT ({rep.income_rows} rows' + (f'; {others}' if others else '') + ')')
    L.append(f'               window {fmt_cairo(rep.income_start_ms)} .. {fmt_cairo(rep.income_end_ms)}')
    for sym, r in rep.rules.items():
        L.append(f'rules {sym:<9}: tick {_n(r.tick_size)}  step {_n(r.step_size)}  min qty {_n(r.min_qty)}  '
                 f'min notional {_n(r.min_notional)}  [{r.status}]')
    L.append(f'rate limit    : max used weight (1m) {rep.max_weight_1m}')
    for w in rep.warnings:
        L.append(f'WARNING       : {w}')
    return '\n'.join(L)
