"""The runner's testnet factory hook: `venue.kind = "testnet"`, `venue.factory = "newcore.venue.factory:build_testnet"`.

build_testnet(config) -> {'venue': TestnetVenue, 'bars': TestnetBarSource, 'account_reader': TestnetAccountReader,
                          'rules': RulesSnapshot, 'instrument_rules': {symbol: InstrumentRules}, 'clock': OffsetClock,
                          'binding_digest': str}
(the shape newcore/runner/testnet_hook.build expects: venue, bars and account_reader; the rest is extra.)

What it does, in order, and every refusal is a typed exception BEFORE the runner can trade:
 1. config: mode TESTNET and venue.kind testnet only; a base_url other than the pinned testnet host is refused
    (the transport is hard-pinned anyway - there is no other host to choose).
 2. credentials: from the DPAPI credential store of config.account_id (keys NEVER come from the config; the config only
    carries the non-secret 16-hex binding digest). Missing / corrupt / undecryptable -> CredentialsUnavailable.
 3. binding: the stored key's binding digest must equal config.key_digest -> else BindingMismatch (a different key for
    this AccountId is a rotation: the binding is unconfirmed, the runner must not attach to it silently).
 4. clock: the OffsetClock is calibrated from server time -> else FactoryRefused.
 5. hedge mode: the account must be in hedge mode -> HedgeModeRequired / VenueBootUnknown.
 6. rules: the exchange-rules snapshot of config.symbols (every symbol TRADING) -> RulesUnavailable / SymbolRefused.
Nothing here retries; one build = a bounded handful of reads (3 time samples, position mode, exchangeInfo).
"""
from .clock import OffsetClock, system_clock_ms
from .credentials import CredentialsUnavailable, CredentialStore, SecretScrubber, binding_digest
from .guard import TESTNET_BASE_URL, VenueGuardError
from .rules_fetch import fetch_rules
from .testnet_bars import TestnetBarSource
from .testnet_venue import TestnetAccountReader, TestnetVenue
from .transport import BinanceTestnetTransport, PositionMode


class FactoryRefused(Exception):
    """The testnet adapters were not built (wrong mode / kind / host, or the clock could not be calibrated)."""


class BindingMismatch(FactoryRefused):
    """The stored key's binding digest is not the config's account.key_digest: never attach silently."""


def build_testnet(config, *, http=None, local_clock=None, store=None, credential_root=None, scrubber=None,
                  timeout_s=10.0):
    mode, kind = getattr(config, 'mode', None), getattr(config, 'venue_kind', None)
    if mode != 'TESTNET' or kind != 'testnet':
        raise FactoryRefused(f'build_testnet needs mode TESTNET and venue.kind testnet (got {mode!r} / {kind!r})')
    base = getattr(config, 'base_url', None)
    if base is not None and base != TESTNET_BASE_URL:
        raise VenueGuardError('the testnet factory refuses any host other than the pinned testnet host')
    symbols = tuple(getattr(config, 'symbols', ()) or ())
    if not symbols:
        raise FactoryRefused('config.symbols is empty')

    store = store or CredentialStore(config.account_id, root=credential_root)
    scrubber = scrubber or SecretScrubber()
    creds = store.load(scrubber=scrubber)                          # CredentialsUnavailable propagates (HOLD)
    try:
        digest = binding_digest(creds.api_key())
    except ValueError:
        raise CredentialsUnavailable('the stored key is not usable', 'corrupt') from None
    if digest != getattr(config, 'key_digest', None):
        raise BindingMismatch(f'the stored key for {config.account_id} has binding digest {digest}, the run config '
                              f'says {getattr(config, "key_digest", None)}: confirm the binding (rotation) first')

    if http is None:
        from .http_sender import TestnetHttpSender                 # the only network-capable module
        http = TestnetHttpSender()
    oc = OffsetClock(local_clock or system_clock_ms)
    transport = BinanceTestnetTransport(environment='testnet', http=http, clock=oc, position_mode=PositionMode.HEDGE,
                                        credentials=creds, scrubber=scrubber, timeout_s=timeout_s)
    m = oc.resync(transport)
    if not m.ok:
        raise FactoryRefused(f'server clock calibration failed ({m.reason})')
    venue = TestnetVenue(transport, oc)
    venue.check_hedge_mode()                                       # HedgeModeRequired / VenueBootUnknown propagate
    rules = fetch_rules(http, symbols=list(symbols), clock=oc)     # RulesUnavailable / SymbolRefused propagate
    return {'venue': venue, 'bars': TestnetBarSource(transport, oc), 'account_reader': TestnetAccountReader(transport, oc),
            'rules': rules, 'instrument_rules': rules.instrument_rules(), 'clock': oc, 'binding_digest': digest}
