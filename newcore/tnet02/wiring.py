"""TNET-02 composition: #291 venue -> FaultingSender -> NC-04 budget/context -> RealTestnetAdapter.

Pure wiring over injected collaborators.  The real HTTP sender, credential source and clocks are supplied by the
CLI; tests supply fakes.  ``key_digest`` reproduces the NC-01 binding digest formula (PBKDF2-HMAC-SHA256, legacy
``account_fingerprint`` salt) without importing the legacy engine.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

from newcore.account_context import AccountBudget, AccountContext, RawLimitPayload
from newcore.venue.binance_usdm_testnet import (TESTNET_BASE_URL, TESTNET_ORIGINS, BinanceUsdMTestnetVenue,
                                                CredentialSource,
                                                HttpRequest, HttpResponse)

from .adapter import HarnessConfig, RealTestnetAdapter
from .faults import FaultingSender
from .ports import AccountPort, Clock, FiltersPort, JournalOpener, Sleeper


_KDF_SALT = b"zackbot-install-marker-v1"
_KDF_ROUNDS = 200_000


def key_digest(api_key: str) -> str:
    """16-hex NC-01 ``AccountBinding.key_digest`` of an API key; the key itself is never returned or stored."""
    if type(api_key) is not str or not api_key:
        raise ValueError("key_digest.api_key")
    return hashlib.pbkdf2_hmac("sha256", api_key.encode(), _KDF_SALT, _KDF_ROUNDS).hex()[:16]


@dataclass(frozen=True, slots=True)
class Wired:
    adapter: RealTestnetAdapter
    venue: BinanceUsdMTestnetVenue
    budget: AccountBudget
    context: AccountContext
    faults: FaultingSender


def build_adapter(*, sender: Callable[[HttpRequest], HttpResponse], credentials: CredentialSource,
                  account_id: str, egress_id: str, exchange_info: RawLimitPayload,
                  account_order_limits: RawLimitPayload, account: AccountPort, filters: FiltersPort,
                  journals: JournalOpener, clock: Clock, monotonic: Clock, sleeper: Sleeper,
                  config: HarnessConfig = HarnessConfig(), base_url: str = TESTNET_BASE_URL) -> Wired:
    # One explicit origin per run: the venue, and (in the CLI) the sender and reader, all receive the same string.
    if type(base_url) is not str or base_url not in TESTNET_ORIGINS:
        raise ValueError("wiring.origin")
    sender_origin = getattr(sender, "origin", base_url)
    if sender_origin != base_url:
        raise ValueError("wiring.origin_mismatch")
    faults = FaultingSender(sender, clock)
    budget = AccountBudget(account_id=account_id, egress_id=egress_id, exchange_info=exchange_info,
                           account_order_limits=account_order_limits, monotonic_clock=monotonic, wall_clock=clock)

    def observe(evidence) -> None:
        budget.observe(evidence)

    bind = getattr(account, "bind_budget", None)
    if bind is not None:
        # Live account reads spend from, and report rate evidence to, the same budget as the venue.
        bind(budget)
    venue = BinanceUsdMTestnetVenue(environment="testnet", base_url=base_url, sender=faults,
                                    credentials=credentials, clock=clock, rate_observer=observe)
    context = AccountContext(account_id=account_id, egress_id=egress_id, venue=venue, budget=budget,
                             monotonic_clock=monotonic)
    try:
        digest = key_digest(credentials.api_key())
    except Exception:
        raise ValueError("wiring.credentials") from None
    adapter = RealTestnetAdapter(venue=venue, context=context, budget=budget, faults=faults, account=account,
                                 filters=filters, journals=journals, clock=clock, monotonic=monotonic,
                                 sleeper=sleeper, binding_digest=digest, config=config)
    return Wired(adapter, venue, budget, context, faults)


__all__ = ["Wired", "build_adapter", "key_digest"]
