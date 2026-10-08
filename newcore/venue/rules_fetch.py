"""Exchange-rules fetcher: testnet exchangeInfo -> the zackbot.exchange_rules/1 snapshot the S1 adapter
(newcore/adapters/exchange_rules.py load_rules) reads, plus NC-01 InstrumentRules directly.

Same filter semantics as the legacy snapshot (feasibility.py): MARKET_LOT_SIZE stepSize / minQty (and maxQty),
PRICE_FILTER tickSize, MIN_NOTIONAL notional; PERPETUAL contracts only. Every requested symbol must be present, its rules
must parse, and its status must be TRADING: otherwise a typed refusal (SymbolUnknown / SymbolNotTrading /
SymbolRefused) is raised and no snapshot is produced (never a partial rule set).

The snapshot carries raw_sha256 (SHA-256 of the exact exchangeInfo bytes received) and rules_sha256 (SHA-256 of the
canonical rules of the requested symbols: sorted keys, exact decimal strings) so a run can journal which rules it used.
Numbers are written as exact JSON number text (never through float); the S1 loader reads them back with
parse_float=Decimal / parse_int=Decimal.
"""
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from newcore.domain import Capability, InstrumentId, InstrumentRules, Venue

from .guard import TESTNET_BASE_URL
from .outcomes import ReadKind
from .transport import BinanceTestnetTransport, PositionMode

SCHEMA = 'zackbot.exchange_rules/1'
SOURCE_URL = TESTNET_BASE_URL + '/fapi/v1/exchangeInfo'
CAPABILITIES = (Capability.HEDGE_MODE, Capability.STOP_MARKET, Capability.REDUCE_ONLY)
_DEC = re.compile(r'"@@dec:(-?[0-9]+(?:\.[0-9]+)?)@@"')


class RulesUnavailable(Exception):
    """exchangeInfo could not be read, or did not parse. reason is a stable token."""

    def __init__(self, message, reason):
        super().__init__(message)
        self.reason = reason


class SymbolRefused(Exception):
    def __init__(self, symbol, reason):
        super().__init__(f'{symbol}: {reason}')
        self.symbol, self.reason = symbol, reason


class SymbolUnknown(SymbolRefused):
    pass


class SymbolNotTrading(SymbolRefused):
    pass


def _plain(d):
    """Exact plain decimal text: no exponent, no trailing fractional zeros, NO context rounding (Decimal.normalize()
    would round to the 28-digit context precision)."""
    s = format(d, 'f')
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return '0' if s in ('-0', '', '0') else s


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def dumps_exact(obj):
    """JSON with every Decimal written as exact number text (no float on the way)."""
    def prep(o):
        if isinstance(o, Decimal):
            return f'@@dec:{_plain(o)}@@'
        if isinstance(o, dict):
            return {k: prep(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [prep(v) for v in o]
        return o
    return _DEC.sub(r'\1', json.dumps(prep(obj), indent=1, sort_keys=True))


@dataclass(frozen=True)
class RulesSnapshot:
    doc: dict                        # the zackbot.exchange_rules/1 document (Decimal numbers)
    raw_sha256: str
    rules_sha256: str

    def to_json(self):
        return dumps_exact(self.doc) + '\n'

    def instrument_rules(self):
        """{symbol: NC-01 InstrumentRules} (BINANCE_USDM, hedge / stop-market / reduce-only capabilities)."""
        return {s: InstrumentRules(instrument=InstrumentId(venue=Venue.BINANCE_USDM, symbol=s), tick_size=r['tick'],
                                   step_size=r['step'], min_qty=r['min_qty'], max_qty=r['max_qty'],
                                   min_notional=r['min_notional'], capabilities=CAPABILITIES)
                for s, r in self.doc['symbols'].items()}

    def save(self, path):
        """Atomic write (temp file + fsync + replace) of the snapshot JSON to an explicit path."""
        tmp = f'{path}.tmp{os.getpid()}'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(self.to_json())
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return path


class _Capture:
    """Passes requests through and keeps the exact bytes of the last answer (for raw_sha256)."""

    def __init__(self, http):
        self._http, self.body = http, None

    def __call__(self, request):
        resp = self._http(request)
        self.body = bytes(resp.body)
        return resp


def rules_sha256(symbols_doc):
    canonical = {s: {k: _plain(v) if isinstance(v, Decimal) else v for k, v in sorted(r.items())}
                 for s, r in sorted(symbols_doc.items())}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode('ascii')).hexdigest()


def fetch_rules(http, *, symbols, clock, environment='testnet'):
    """Fetch exchangeInfo once (unsigned) through the testnet-pinned transport and build the snapshot."""
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError('symbols must be a non-empty list without duplicates')
    capture = _Capture(http)
    transport = BinanceTestnetTransport(environment=environment, http=capture, clock=clock,
                                        position_mode=PositionMode.HEDGE)
    out = transport.exchange_info()
    if out.kind is not ReadKind.OK:
        reason = out.unknown_reason if out.kind is ReadKind.UNKNOWN else f'rejected_{out.error.code}'
        raise RulesUnavailable(f'exchangeInfo not readable ({out.kind.value})', reason or 'unknown')
    try:
        return _snapshot(out.value, symbols, capture, clock, environment)
    except (ArithmeticError, OverflowError, OSError, ValueError, TypeError) as ex:   # typed, never a crash
        if isinstance(ex, SymbolRefused):
            raise
        raise RulesUnavailable(f'exchangeInfo did not build a snapshot ({type(ex).__name__})', 'malformed') from None


def _snapshot(info, symbols, capture, clock, environment):
    rows = {}
    for s in symbols:
        if s in info.unparseable:
            raise SymbolRefused(s, 'rules did not parse')
        r = info.symbols.get(s)
        if r is None:
            raise SymbolUnknown(s, 'not listed by exchangeInfo')
        if r.status != 'TRADING':
            raise SymbolNotTrading(s, f'status {r.status}, not TRADING')
        if r.contract_type != 'PERPETUAL':
            raise SymbolRefused(s, f'contract type {r.contract_type}, not PERPETUAL')
        rows[s] = dict(step=r.market_step_size, min_qty=r.market_min_qty, max_qty=r.market_max_qty, tick=r.tick_size,
                       min_notional=r.min_notional, status=r.status)
    snapshot_rules = rules_sha256(rows)
    raw = hashlib.sha256(capture.body or b'').hexdigest()
    doc = {'schema': SCHEMA, 'environment': environment, 'provenance': 'direct_fetch', 'source': SOURCE_URL,
           'source_url': SOURCE_URL, 'fetched_at': _iso(clock()), 'server_time': _iso(info.server_time_ms),
           'raw_sha256': raw, 'rules_sha256': snapshot_rules, 'note': 'fetched by newcore.venue.rules_fetch',
           'symbols': rows}
    snap = RulesSnapshot(doc, raw, snapshot_rules)
    snap.instrument_rules()                    # NC-01 validation (positive, min <= max, on the step grid) now
    return snap
