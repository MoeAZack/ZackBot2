"""NEWCORE venue wire layer (NC-03): a thin, testnet-only Binance USD-M Futures REST transport.

This package is the WIRE layer only. It builds and signs requests, sends them through an injected HTTP function and
turns each answer into a typed outcome (KNOWN / FINAL / ACKNOWLEDGED / REJECTED / UNKNOWN / NOT_FOUND for order
operations; OK / REJECTED / UNKNOWN for reads). It never retries, never sleeps, never reads the wall clock, never reads
the environment and never imports legacy modules. The VenuePort adapter that maps these outcomes onto NC-01 domain
records is a separate, later layer.

Modules (import from them directly; this file deliberately imports nothing):
- guard        TESTNET_BASE_URL pin + environment binding (VenueGuardError)
- credentials  CredentialSource interface, StaticCredentials (redacted), key_digest()
- signing      HMAC-SHA256 query signing with an injected integer-ms clock
- wire         HttpRequest / HttpResponse, WireTimeout / WireConnectionError, rate-limit header parsing
- errors       Binance error-code table, VenueError, NotFoundEvidence (typed, never "never filled")
- records      strict Decimal parsers for exchangeInfo, klines, account, positions, orders, algo orders, fills
- outcomes     OrderOutcome / ReadOutcome
- transport    BinanceTestnetTransport (the endpoints)

Safety: the base URL is hard-pinned to the Binance Futures TESTNET host (see guard.py). Credentials are reached only
through an injected CredentialSource; the transport never holds the secret.
"""
