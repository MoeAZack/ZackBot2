"""Testnet guard: the only endpoint this package can ever talk to is the Binance USD-M Futures TESTNET host.

Design (NC-01 invariant 13, slice plan 1.3):
- The allow-list is a single exact string. No normalisation is applied: a trailing slash, another scheme, upper case,
  a port, user-info, a path or a look-alike host are all refused rather than "cleaned up".
- The environment binding is an explicit second key that must be exactly the string "testnet". A caller cannot reach
  the venue by passing the right URL with the wrong environment, or the right environment with another URL.
- The check runs at construction AND again on every request URL the transport builds (defence in depth: a mutated
  attribute or a bad path join cannot leave the testnet host).
"""

TESTNET_BASE_URL = 'https://testnet.binancefuture.com'
TESTNET_ENVIRONMENT = 'testnet'

# environment -> the one base URL allowed for it. There is deliberately no mainnet entry.
_ALLOWED = {TESTNET_ENVIRONMENT: TESTNET_BASE_URL}


class VenueGuardError(Exception):
    """Refused: the binding is not the Binance Futures testnet. Raised before any request is built or sent."""


def check_binding(environment, base_url):
    """Return (environment, base_url) when they are exactly the testnet pair; raise VenueGuardError otherwise.
    The message names the refusal, never echoes credentials (none are involved here)."""
    if type(environment) is not str:
        raise VenueGuardError(f'environment must be the string {TESTNET_ENVIRONMENT!r}, got {type(environment).__name__}')
    if environment not in _ALLOWED:
        raise VenueGuardError(f'environment {environment!r} is not allowed: this transport is testnet-only')
    if type(base_url) is not str:
        raise VenueGuardError(f'base_url must be a str, got {type(base_url).__name__}')
    if base_url != _ALLOWED[environment]:
        raise VenueGuardError(f'base_url {base_url!r} is not the pinned {environment} host {_ALLOWED[environment]!r}')
    return environment, base_url


def check_request_url(url):
    """Every request URL must be <pinned host>/fapi/<path> with no query string, fragment or user-info tricks."""
    prefix = TESTNET_BASE_URL + '/fapi/'
    if type(url) is not str or not url.startswith(prefix):
        raise VenueGuardError('request URL is outside the pinned testnet host')
    rest = url[len(prefix):]
    if not rest or rest.startswith('/') or any(ch in rest for ch in '?#@\\ ') or '..' in rest or '//' in rest:
        raise VenueGuardError('request URL path is not a plain /fapi/ path')
    return url
